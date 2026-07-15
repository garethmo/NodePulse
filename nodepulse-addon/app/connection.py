"""
NodePulse Addon — Meshtastic Connection Manager.

This module owns the single, long-lived connection to the Meshtastic node.
It abstracts away:
  - The meshtastic Python library's TCP interface.
  - Auto-reconnection: if the link stalls or drops, we retry with backoff
    so the rest of the application never has to worry about transient failures.
  - Thread-safety: the meshtastic library is synchronous, but we wrap its
    calls so they can be safely invoked from the async aiohttp event loop via
    asyncio.to_thread().

Design decision: We maintain ONE MeshInterface instance for the lifetime of
the addon. Tearing down and recreating it on every poll would be expensive and
risks causing state loss in the upstream meshtastic library.
"""
import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import meshtastic
import meshtastic.tcp_interface

logger = logging.getLogger(__name__)

# How long (seconds) to wait between reconnection attempts.
# Using a capped exponential backoff to avoid hammering an offline node.
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 60

# Substrings that strongly suggest the node already has a TCP client and is
# rejecting the second connection, rather than a generic network failure.
_SLOT_CONFLICT_HINTS = (
    "refused",
    "reset",
    "denied",
    "in use",
    "already",
    "timed out",
    "timeout",
    "unreachable",
    "not connect",
    "too many",
)


def _looks_like_slot_conflict(exc: Exception) -> bool:
    """Heuristically detect a single-TCP-client slot conflict from an error."""
    message = str(exc).lower()
    if not message:
        # Some libraries raise with no message but a specific type name.
        message = type(exc).__name__.lower()
    return any(hint in message for hint in _SLOT_CONFLICT_HINTS)


class MeshtasticConnection:
    """
    Manages a persistent TCP connection to one Meshtastic node.

    All public methods are safe to call from an asyncio event loop.
    Internally they dispatch synchronous meshtastic library calls to a thread
    pool worker via asyncio.to_thread() to keep the event loop unblocked.
    """

    def __init__(self, host: str, port: int, mode: str = "direct") -> None:
        self._host = host
        self._port = port
        self._mode = mode

        # The underlying meshtastic TCP interface — None when disconnected.
        self._interface: Optional[meshtastic.tcp_interface.TCPInterface] = None

        # Protects _interface from concurrent access across threads.
        self._lock = threading.Lock()

        # Used by the health monitor background task to signal reconnect attempts.
        self._connected = False

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the initial connection, retrying until success at startup."""
        await asyncio.to_thread(self._connect_sync)

    async def disconnect(self) -> None:
        """Cleanly close the interface on addon shutdown."""
        await asyncio.to_thread(self._close_sync)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get_status(self) -> Dict[str, Any]:
        """
        Return a dict describing the current connection and node identity.

        This is a lightweight call — it reads from the already-cached node DB
        in the meshtastic interface object rather than making a fresh network
        request. Suitable for frequent polling.
        """
        return await asyncio.to_thread(self._get_status_sync)

    async def get_nodes(self) -> List[Dict[str, Any]]:
        """Return the full list of nodes the local node is aware of."""
        return await asyncio.to_thread(self._get_nodes_sync)

    async def get_channels(self) -> List[Dict[str, Any]]:
        """Return the channel configuration from the connected node."""
        return await asyncio.to_thread(self._get_channels_sync)

    async def send_message(
        self, text: str, destination: Optional[str] = None, channel: int = 0
    ) -> bool:
        """
        Send a text message over the mesh.

        Args:
            text: The plaintext message content.
            destination: Node ID hex string for a DM, or None for broadcast.
            channel: Channel index to send on (0 = primary channel).

        Returns:
            True if the send was accepted by the library, False otherwise.
        """
        return await asyncio.to_thread(self._send_message_sync, text, destination, channel)

    async def request_traceroute(self, destination: str) -> bool:
        """Request a traceroute packet towards a specific destination node ID."""
        return await asyncio.to_thread(self._request_traceroute_sync, destination)

    async def request_position(self, destination: str) -> bool:
        """Request a fresh GPS position from a specific destination node."""
        return await asyncio.to_thread(self._request_position_sync, destination)

    async def monitor_connection(self) -> None:
        """
        Background coroutine that periodically checks the connection health
        and triggers reconnection if the interface has gone stale.

        This is designed to be run as a persistent asyncio Task from main.py.
        It is the ONLY place where reconnection is initiated, which avoids
        race conditions from multiple callers trying to reconnect simultaneously.
        """
        delay = _RECONNECT_BASE_DELAY
        first_attempt = True
        while True:
            # Skip the initial backoff so we connect as soon as the addon
            # starts; only sleep between *subsequent* reconnect attempts.
            if not first_attempt:
                await asyncio.sleep(delay)
            first_attempt = False

            if not self._is_interface_healthy():
                if self._mode == "proxy":
                    logger.warning(
                        "Connection to Meshtastic TCP proxy at %s:%s lost — "
                        "attempting reconnect. Check that the official Meshtastic HA "
                        "integration is running with its 'TCP Proxy' option enabled and "
                        "that proxy_host/proxy_port are correct.",
                        self._host, self._port,
                    )
                else:
                    logger.warning(
                        "Connection health check failed — attempting reconnect "
                        "(host=%s, port=%s). If this persists, the node may already "
                        "serve another TCP client (Meshtastic firmware allows only one). "
                        "Disconnect the other client or reboot the node.",
                        self._host, self._port,
                    )
                self._connected = False

                # Exponential backoff with a ceiling to bound wait time.
                try:
                    await asyncio.to_thread(self._connect_sync)
                    delay = _RECONNECT_BASE_DELAY  # reset on success
                except Exception as exc:
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    logger.error(
                        "Reconnect failed — will retry in %ss (host=%s): %s",
                        delay, self._host, exc,
                    )

    # ------------------------------------------------------------------
    # Private synchronous helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    def _connect_sync(self) -> None:
        """
        Synchronous connection attempt. Runs inside a thread pool worker.

        We close any stale interface before creating a new one to avoid
        resource leaks (file descriptors, threads) from orphaned TCP sockets.
        """
        with self._lock:
            self._close_sync()  # clean up any existing connection first

            logger.info(
                "Connecting to Meshtastic node (host=%s, port=%s)", self._host, self._port
            )
            # The meshtastic library raises on connection failure, which propagates
            # up to the caller (connect() or monitor_connection()) for handling.
            try:
                self._interface = meshtastic.tcp_interface.TCPInterface(
                    hostname=self._host, portNumber=self._port, debugOut=None
                )
            except Exception as exc:
                self._interface = None
                self._connected = False
                if self._mode == "proxy":
                    # In proxy mode we talk to the official Meshtastic HA
                    # integration's TCP proxy, not the node directly. A rejected
                    # connection means that proxy isn't reachable — most often
                    # because the "TCP Proxy" option is disabled in the official
                    # integration, or proxy_host/proxy_port point at the wrong place.
                    logger.error(
                        "Connection to Meshtastic TCP proxy at %s:%s rejected. "
                        "Ensure the official Meshtastic HA integration is installed "
                        "and its 'TCP Proxy' option is enabled, and that proxy_host/"
                        "proxy_port are correct. Underlying error: %s",
                        self._host, self._port, exc,
                    )
                elif _looks_like_slot_conflict(exc):
                    # Meshtastic firmware only allows ONE TCP client per node.
                    # A refused/reset/denied connection almost always means
                    # another client (e.g. the official Meshtastic HA integration)
                    # already holds the single slot. The slot is only freed once
                    # the firmware detects the drop — power-cycle the node or
                    # disconnect the other client to recover.
                    logger.error(
                        "Connection to %s:%s rejected — the node already has a "
                        "TCP client connected. Meshtastic firmware permits ONLY ONE "
                        "TCP connection per node, so NodePulse (direct TCP) cannot "
                        "share it with the official Meshtastic HA integration. To run "
                        "both: set the official integration to connect via Serial or "
                        "Bluetooth (freeing the TCP slot for NodePulse), or disable it. "
                        "If you just disconnected the other client, reboot/power-cycle "
                        "the node so the firmware releases the slot. Underlying error: %s",
                        self._host, self._port, exc,
                    )
                else:
                    logger.error(
                        "Failed to connect to Meshtastic node at %s:%s: %s",
                        self._host, self._port, exc,
                    )
                raise
            self._connected = True
            logger.info(
                "Connected to Meshtastic node (host=%s, port=%s)", self._host, self._port
            )

    def _close_sync(self) -> None:
        """Close the current interface, suppressing errors (already closed, etc.)."""
        if self._interface is not None:
            try:
                self._interface.close()
            except Exception as exc:
                logger.debug("Exception during interface close (ignored): %s", exc)
            finally:
                self._interface = None
                self._connected = False

    def _is_interface_healthy(self) -> bool:
        """
        Heuristic health check on the interface.

        The meshtastic TCP interface stores the live socket on the PUBLIC
        `socket` attribute (NOT `_socket`). If it is None, the socket was
        closed unexpectedly and we need to reconnect. This is preferable to a
        full ping-style check because it avoids extra network traffic on
        healthy connections.
        """
        with self._lock:
            if self._interface is None:
                return False
            # Check the underlying socket — if it's gone the connection is dead.
            # NOTE: the attribute is `socket` (verified against meshtastic
            # 2.7.x). A stale `_socket` lookup always returned None, which
            # falsely reported a healthy connection as dead and caused an
            # endless reconnect loop.
            sock = getattr(self._interface, "socket", None)
            return sock is not None

    def _get_status_sync(self) -> Dict[str, Any]:
        with self._lock:
            if not self._connected or self._interface is None:
                return {"connected": False, "my_info": None}

            my_info = getattr(self._interface, "myInfo", None)
            return {
                "connected": True,
                "my_info": {
                    "my_node_num": getattr(my_info, "my_node_num", None),
                } if my_info else None,
                "node_count": len(self._interface.nodes or {}),
            }

    def _get_nodes_sync(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._connected or self._interface is None:
                return []

            nodes_raw = self._interface.nodes or {}
            result = []

            for node_id, node_data in nodes_raw.items():
                # Extract the nested sub-objects safely — the meshtastic library
                # returns protobuf-derived dicts whose keys may be absent.
                user = node_data.get("user", {})
                position = node_data.get("position", {})
                device_metrics = node_data.get("deviceMetrics", {})

                result.append({
                    "id": node_id,
                    "long_name": user.get("longName", ""),
                    "short_name": user.get("shortName", ""),
                    "hw_model": user.get("hwModel", ""),
                    "last_heard": node_data.get("lastHeard"),
                    "snr": node_data.get("snr"),
                    "rssi": node_data.get("rssi"),
                    "hops_away": node_data.get("hopsAway"),
                    "is_licensed": user.get("isLicensed", False),
                    "latitude": position.get("latitude"),
                    "longitude": position.get("longitude"),
                    "altitude": position.get("altitude"),
                    "battery_level": device_metrics.get("batteryLevel"),
                    "voltage": device_metrics.get("voltage"),
                    "channel_utilization": device_metrics.get("channelUtilization"),
                    "air_util_tx": device_metrics.get("airUtilTx"),
                })

            return result

    def _get_channels_sync(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._connected or self._interface is None:
                return []

            channels_raw = getattr(self._interface, "localConfig", None)
            if channels_raw is None:
                return []

            # The meshtastic library stores channel config under interface.channels
            channels = getattr(self._interface, "channels", {}) or {}
            result = []
            for idx, channel in channels.items():
                settings = getattr(channel, "settings", None)
                result.append({
                    "index": idx,
                    "name": getattr(settings, "name", "") if settings else "",
                    "role": str(getattr(channel, "role", "")),
                })
            return result

    def _send_message_sync(
        self, text: str, destination: Optional[str], channel: int
    ) -> bool:
        with self._lock:
            if not self._connected or self._interface is None:
                logger.error("Cannot send message — not connected")
                return False
            try:
                # sendText handles both broadcast (destinationId=None or BROADCAST)
                # and unicast DMs. The meshtastic library manages PKI encryption
                # automatically for DMs when a shared key is configured on the channel.
                self._interface.sendText(
                    text,
                    destinationId=destination or meshtastic.BROADCAST_ADDR,
                    channelIndex=channel,
                )
                return True
            except Exception as exc:
                logger.error(
                    {"destination": destination, "error": str(exc)}, "Failed to send message"
                )
                return False

    def _request_traceroute_sync(self, destination: str) -> bool:
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            try:
                self._interface.sendTraceRoute(destination)
                return True
            except Exception as exc:
                logger.error(
                    {"destination": destination, "error": str(exc)}, "Traceroute request failed"
                )
                return False

    def _request_position_sync(self, destination: str) -> bool:
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            try:
                self._interface.sendPosition(destinationId=destination)
                return True
            except Exception as exc:
                logger.error(
                    {"destination": destination, "error": str(exc)}, "Position request failed"
                )
                return False
