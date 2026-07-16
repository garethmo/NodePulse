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
import collections
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import meshtastic
import meshtastic.tcp_interface
from pubsub import pub

# Persistent storage directory. Under HA Supervisor this is /data, which
# survives addon restarts (unlike the container's ephemeral filesystem). We
# persist the recent message buffer there so the Web UI's message history is
# not lost when the addon is restarted.
_DATA_DIR = os.environ.get("NODEPULSE_DATA_DIR", "/data")
_MESSAGES_FILE = os.path.join(_DATA_DIR, "messages.json")
_TRACEROUTES_FILE = os.path.join(_DATA_DIR, "traceroutes.json")
logger = logging.getLogger(__name__)

# How long (seconds) to wait between reconnection attempts.
# Using a capped exponential backoff to avoid hammering an offline node.
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 60

# Max time (seconds) to wait for a single TCP connect attempt before giving
# up. The meshtastic TCPInterface constructor has no built-in connect timeout,
# so a node that accepts the SYN but never completes the handshake (or a black
# hole) would otherwise block forever and leave the addon stuck "Connecting".
_CONNECT_TIMEOUT = 15

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

    def __init__(self, host: str, port: int, mode: str = "direct", access_key: Optional[str] = None) -> None:
        self._host = host
        self._port = port
        self._mode = mode
        self._access_key = (access_key or "").strip() or None

        # The underlying meshtastic TCP interface — None when disconnected.
        self._interface: Optional[meshtastic.tcp_interface.TCPInterface] = None

        # Protects _interface from concurrent access across threads.
        self._lock = threading.Lock()

        # Used by the health monitor background task to signal reconnect attempts.
        self._connected = False

        # Bounded ring buffer of recently received text messages, so the Web UI
        # and API can present a live message feed (a MeshSense-style inbox).
        # Populated by the pubsub "meshtastic.receive" listener below.
        self._msg_lock = threading.Lock()
        self._messages: "collections.deque" = collections.deque(maxlen=200)
        # Serialises disk persistence (load/save) so a read and a write from
        # different threads can never interleave and corrupt the JSON file.
        self._persist_lock = threading.Lock()

        # Restore any previously-persisted messages so history survives restarts.
        self._load_messages()

        # Persisted traceroute results, keyed by node ID. Restored on startup so
        # discovered routes survive restarts; refreshed whenever a new traceroute
        # for that node completes.
        self._traceroutes: Dict[str, Dict[str, Any]] = {}
        self._load_traceroutes()

        # Mutable cache of the last node list we read from the interface. The
        # meshtastic library updates interface.nodes asynchronously (e.g. when a
        # traceroute/position response arrives); keeping our own snapshot lets us
        # merge those late updates so /api/nodes reflects them on the next poll.
        self._nodes_lock = threading.Lock()
        self._nodes: List[Dict[str, Any]] = []

        # Destination node ID of the most recent traceroute request. The reply
        # packet only carries the origin (the responding node), not the original
        # request target, so we remember it to attribute the route correctly.
        self._pending_traceroute_dest: Optional[str] = None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the initial connection, retrying until success at startup."""
        await asyncio.wait_for(
            asyncio.to_thread(self._connect_sync), timeout=_CONNECT_TIMEOUT
        )

    async def disconnect(self) -> None:
        """Cleanly close the interface on addon shutdown."""
        await asyncio.to_thread(self._close_sync)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_access_key(self, access_key: Optional[str]) -> None:
        """
        Update the access key used to authenticate admin operations with the
        node. Called when the integration relays a key via the API header so a
        user can configure it in one place. Applied immediately to the live
        interface (when supported) and stored for future reconnects.
        """
        key = (access_key or "").strip() or None
        self._access_key = key
        if key is not None and self._interface is not None:
            try:
                self._interface.access_key = key
            except AttributeError:
                logger.debug("meshtastic library does not support access_key — ignoring")

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
        """Request a traceroute towards a specific destination node."""
        logger.info("Requesting traceroute to %s", destination)
        ok = await asyncio.to_thread(self._request_traceroute_sync, destination)
        logger.info("Traceroute dispatch to %s returned: %s", destination, ok)
        # Pull the response (per-hop SNR / route) into our cache so the API
        # reflects it on the next poll.
        await asyncio.to_thread(self._refresh_node_from_interface, destination)
        return ok

    async def request_position(self, destination: str) -> bool:
        """Request a fresh GPS position from a specific destination node."""
        ok = await asyncio.to_thread(self._request_position_sync, destination)
        # Merge the node's reply position into our cache.
        await asyncio.to_thread(self._refresh_node_from_interface, destination)
        return ok

    async def get_messages(self) -> List[Dict[str, Any]]:
        """Return the most recent received text messages (oldest first)."""
        return await asyncio.to_thread(self._get_messages_sync)

    async def monitor_connection(self) -> None:
        """
        Background coroutine that periodically checks the connection health
        and triggers reconnection if the interface has gone stale.

        This is designed to be run as a persistent asyncio Task from main.py.
        It is the ONLY place where reconnection is initiated, which avoids
        race conditions from multiple callers trying to reconnect simultaneously.
        """
        delay = _RECONNECT_BASE_DELAY
        is_first_attempt = True
        while True:
            # Skip the initial backoff so we connect as soon as the addon
            # starts; only sleep between *subsequent* reconnect attempts.
            if not is_first_attempt:
                await asyncio.sleep(delay)

            if not self._is_interface_healthy():
                # On the very first iteration there is no prior connection, so
                # use INFO rather than WARNING to avoid alarming log noise at
                # every normal startup.
                if is_first_attempt:
                    logger.info(
                        "Initiating first connection to Meshtastic node (host=%s, port=%s)",
                        self._host, self._port,
                    )
                elif self._mode == "proxy":
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
                    await asyncio.wait_for(
                        asyncio.to_thread(self._connect_sync), timeout=_CONNECT_TIMEOUT
                    )
                    delay = _RECONNECT_BASE_DELAY  # reset on success
                except asyncio.TimeoutError:
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    logger.error(
                        "Connect to %s:%s timed out after %ss — will retry in %ss",
                        self._host, self._port, _CONNECT_TIMEOUT, delay,
                    )
                except Exception as exc:
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    logger.error(
                        "Reconnect failed — will retry in %ss (host=%s): %s",
                        delay, self._host, exc,
                    )

            is_first_attempt = False

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
                # The access_key is used to authenticate admin operations with
                # the node. Newer meshtastic library versions accept it directly
                # as a constructor kwarg; older versions do not, so we set it as
                # an attribute after the interface is created. A node that does
                # not require a key simply ignores it.
                kwargs = {"hostname": self._host, "portNumber": self._port, "debugOut": None}
                try:
                    kwargs["access_key"] = self._access_key
                    self._interface = meshtastic.tcp_interface.TCPInterface(**kwargs)
                except TypeError:
                    # Older library without an access_key kwarg — construct
                    # normally, then attach the key if the attribute is supported.
                    kwargs.pop("access_key", None)
                    self._interface = meshtastic.tcp_interface.TCPInterface(**kwargs)
                    if self._access_key is not None:
                        try:
                            self._interface.access_key = self._access_key
                        except AttributeError:
                            logger.debug(
                                "meshtastic library does not support access_key — ignoring"
                            )
                # Ensure the attribute is set on versions that accept it only
                # post-construction.
                if self._access_key is not None and getattr(self._interface, "access_key", None) is None:
                    try:
                        self._interface.access_key = self._access_key
                    except AttributeError:
                        pass

                # Subscribe to inbound packets so the Web UI / API can show a
                # live message feed. The meshtastic library delivers packets on
                # its own background thread via pubsub, so we store them
                # thread-safely.
                try:
                    pub.subscribe(self._on_mesh_receive, "meshtastic.receive")
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Could not subscribe to meshtastic receive events: %s", exc)
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
        try:
            pub.unsubscribe(self._on_mesh_receive, "meshtastic.receive")
        except Exception:
            pass
        if self._interface is not None:
            try:
                self._interface.close()
            except Exception as exc:
                logger.debug("Exception during interface close (ignored): %s", exc)
            finally:
                self._interface = None
                self._connected = False

    def _on_mesh_receive(self, packet: Dict[str, Any], interface=None) -> None:
        """
        Pubsub listener for inbound Meshtastic packets.

        Text messages are captured into the ring buffer for the Web UI feed.
        Traceroute replies (TRACEROUTE_APP) are captured into the per-node
        cache so the UI can show the discovered route + per-hop SNR — the
        meshtastic library only sets an internal acknowledgment flag and does
        NOT persist the route anywhere we can read. Runs on the meshtastic
        library's background thread, so shared state access is locked.
        """
        try:
            decoded = packet.get("decoded", {}) or {}
            portnum = decoded.get("portnum")

            # --- Traceroute replies -------------------------------------
            if portnum == "TRACEROUTE_APP":
                self._capture_traceroute(packet)
                return

            # --- Position replies ---------------------------------------
            # A position request (sendPosition wantResponse=True) makes the
            # destination node reply with its own POSITION_APP packet. The
            # meshtastic library only sets an internal ack flag and does NOT
            # write the fix into interface.nodes, so we must capture it here
            # and merge it into our own node cache ourselves.
            if portnum == "POSITION_APP":
                self._capture_position(packet)
                return

            # --- Text messages ------------------------------------------
            text = decoded.get("text")
            if not text:
                return

            from_num = packet.get("from")
            from_id = ("!" + format(from_num, "08x")) if from_num is not None else None

            to_num = packet.get("to")
            to_id = ("!" + format(to_num, "08x")) if to_num is not None else None

            # Identify the local/self node so we can mark direction and decide
            # whether an inbound packet is a broadcast (channel) or a direct
            # message to us. We derive it lazily from myInfo if available.
            self_num = None
            if self._interface is not None:
                my_info = getattr(self._interface, "myInfo", None)
                self_num = getattr(my_info, "my_node_num", None)
            self_id = ("!" + format(self_num, "08x")) if self_num is not None else None

            name = None
            if from_num is not None and self._interface is not None:
                node = self._interface.nodes.get(from_num)
                if node:
                    name = (node.get("user") or {}).get("longName")

            channel = packet.get("channel", 0)
            # A packet is a DM if it is addressed to a specific node (not the
            # broadcast address). Meshtastic uses a high-bit marker for broadcast.
            is_dm = to_id is not None and to_id != from_id and to_num not in (0xFFFFFFFF, None)

            # Conversation key groups messages into threads mirroring the
            # Meshtastic Android app: each channel is one thread ("Primary",
            # "LongFast", …) and each node we DM is its own thread.
            if is_dm:
                # The "other party" is whoever isn't us.
                other = from_id if from_id != self_id else to_id
                conversation = f"dm:{other}"
            else:
                conversation = f"ch:{channel}"

            entry = {
                "id": f"{from_id or 'unknown'}-{channel}-{int(time.time() * 1000)}-{len(self._messages)}",
                "from_id": from_id,
                "to_id": to_id,
                "from_name": name or from_id or "Unknown",
                "text": text,
                "channel": channel,
                "conversation": conversation,
                "is_dm": is_dm,
                "outgoing": from_id == self_id,
                "rx_snr": packet.get("rxSnr"),
                "rx_rssi": packet.get("rxRssi"),
                "timestamp": int(time.time()),
            }
            with self._msg_lock:
                self._messages.append(entry)
            # Persistence is offloaded to a short-lived daemon thread so we
            # never block the meshtastic receive thread (and never interleave
            # writes — _save_messages takes _persist_lock).
            self._schedule_save(self._messages, _MESSAGES_FILE)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error handling received packet (ignored): %s", exc)

    def _load_messages(self) -> None:
        """Restore the message buffer from disk so history survives restarts.

        Best-effort: any read/parse failure is ignored and we start empty.
        """
        try:
            if not os.path.exists(_MESSAGES_FILE):
                return
            with self._persist_lock:
                with open(_MESSAGES_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, list):
                # Keep only the most recent `maxlen` entries.
                self._messages = collections.deque(data[-self._messages.maxlen:], maxlen=self._messages.maxlen)
                logger.info("Restored %s messages from %s", len(self._messages), _MESSAGES_FILE)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load persisted messages (ignored): %s", exc)

    def _save_messages(self) -> None:
        """Persist the current message buffer to disk (best-effort)."""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            # Snapshot under lock so the writer sees a consistent list, then
            # release before the (slow) disk write. The _persist_lock guards
            # against concurrent reads/writes to the same file.
            with self._msg_lock:
                snapshot = list(self._messages)
            with self._persist_lock:
                with open(_MESSAGES_FILE, "w", encoding="utf-8") as fh:
                    json.dump(snapshot, fh)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not persist messages (ignored): %s", exc)

    def _schedule_save(self, source_deque, path: str) -> None:
        """
        Offload a deque snapshot to disk on a daemon thread.

        The meshtastic pubsub listener runs on the library's own background
        thread; doing a blocking json.dump there would stall inbound packet
        processing. We snapshot the deque and hand the write to a throwaway
        daemon thread instead.
        """
        try:
            with self._msg_lock:
                snapshot = list(source_deque)
            t = threading.Thread(
                target=self._write_json, args=(snapshot, path), daemon=True
            )
            t.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not schedule save (ignored): %s", exc)

    def _write_json(self, snapshot, path: str) -> None:
        """Write `snapshot` as JSON to `path` (runs on a worker thread)."""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with self._persist_lock:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(snapshot, fh)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Persist write failed (ignored): %s", exc)

    def _load_traceroutes(self) -> None:
        """Restore persisted traceroute results keyed by node ID (best-effort)."""
        try:
            if not os.path.exists(_TRACEROUTES_FILE):
                return
            with self._persist_lock:
                with open(_TRACEROUTES_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, dict):
                self._traceroutes = {str(k): v for k, v in data.items()}
                logger.info(
                    "Restored %s traceroute results from %s",
                    len(self._traceroutes), _TRACEROUTES_FILE,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load persisted traceroutes (ignored): %s", exc)

    def _save_traceroutes(self) -> None:
        """Persist the current traceroute results to disk (best-effort).

        Offloaded to a daemon thread so the meshtastic receive thread is never
        blocked, and serialised via _persist_lock.
        """
        try:
            # Snapshot under _nodes_lock so we never read a partially-updated
            # dict. _capture_traceroute writes self._traceroutes under the same
            # lock, so skipping it here would be a data-race.
            with self._nodes_lock:
                snapshot = dict(self._traceroutes)
            t = threading.Thread(
                target=self._write_json, args=(snapshot, _TRACEROUTES_FILE), daemon=True
            )
            t.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not schedule traceroute save (ignored): %s", exc)

    def _capture_traceroute(self, packet: Dict[str, Any]) -> None:
        """
        Parse a TRACEROUTE_APP reply and store the discovered route on the
        destination node's cache entry so the Web UI can render it.

        The reply is addressed to us (to == our node), and ``from`` is the
        origin of the response (the node we asked, or an intermediate that
        answered). We key the route under the requesting destination if known,
        otherwise under the packet's ``from`` node.
        """
        try:
            from meshtastic.protobuf.mesh_pb2 import RouteDiscovery
            decoded = packet.get("decoded", {}) or {}
            payload = decoded.get("payload")
            if not payload:
                return
            rd = RouteDiscovery()
            rd.ParseFromString(payload)
            as_dict = {}
            try:
                from google.protobuf.json_format import MessageToDict
                as_dict = MessageToDict(rd)
            except Exception:
                as_dict = {}

            route = as_dict.get("route", [])
            snr_towards = as_dict.get("snrTowards", [])
            route_back = as_dict.get("routeBack", [])
            snr_back = as_dict.get("snrBack", [])

            from_num = packet.get("from")
            from_id = ("!" + format(from_num, "08x")) if from_num is not None else None

            record = {
                "from_id": from_id,
                "route": route,
                "snr_towards": [s / 4 for s in snr_towards],
                "route_back": route_back,
                "snr_back": [s / 4 for s in snr_back],
                "timestamp": int(time.time()),
            }

            # Store under the node this route belongs to. Prefer the node the
            # user asked about if we have a pending request; otherwise the
            # reply's origin. A re-request simply overwrites the previous result.
            target_id = self._pending_traceroute_dest or from_id

            logger.info(
                "Captured traceroute for %s (target=%s): route=%s route_back=%s",
                from_id, target_id, route, route_back,
            )

            with self._nodes_lock:
                node = next(
                    (n for n in self._nodes if n.get("id") == target_id), None
                )
                if node is not None:
                    node["traceroute"] = record
                # Persist so the result survives addon restarts.
                self._traceroutes[target_id] = record
                self._pending_traceroute_dest = None
            self._save_traceroutes()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error capturing traceroute (ignored): %s", exc)

    def _capture_position(self, packet: Dict[str, Any]) -> None:
        """
        Capture a POSITION_APP reply and merge the GPS fix into our node cache.

        When we call ``sendPosition(destinationId=X, wantResponse=True)`` the
        firmware makes node X reply with its own position. The meshtastic
        library does NOT store that reply in ``interface.nodes`` (it only sets
        an internal acknowledgment flag), so without this capture the "Req.
        Position" button would appear to do nothing. We parse the protobuf
        directly (latitude_i / longitude_i are integer microdegrees) and update
        the destination node's cached coordinates so the map + UI reflect it.
        """
        try:
            from meshtastic.protobuf.mesh_pb2 import Position
            decoded = packet.get("decoded", {}) or {}
            payload = decoded.get("payload")
            if not payload:
                return
            pos = Position()
            pos.ParseFromString(payload)

            from_num = packet.get("from")
            from_id = ("!" + format(from_num, "08x")) if from_num is not None else None
            if not from_id:
                return

            # Integer microdegrees -> decimal degrees. 0 means "not set".
            lat = pos.latitude_i * 1e-7 if pos.latitude_i else None
            lng = pos.longitude_i * 1e-7 if pos.longitude_i else None
            alt = pos.altitude if pos.altitude else None

            logger.info(
                "Captured position for %s: lat=%s lng=%s alt=%s",
                from_id, lat, lng, alt,
            )

            with self._nodes_lock:
                node = next(
                    (n for n in self._nodes if n.get("id") == from_id), None
                )
                if node is not None:
                    if lat is not None:
                        node["latitude"] = lat
                    if lng is not None:
                        node["longitude"] = lng
                    if alt is not None:
                        node["altitude"] = alt
                    node["last_heard"] = int(time.time())
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error capturing position (ignored): %s", exc)

    def _get_messages_sync(self) -> List[Dict[str, Any]]:
        with self._msg_lock:
            return list(self._messages)

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

            # Normalize a node identity (int or "!hex" string) to the canonical
            # "!xxxxxxxx" form used everywhere in the Web UI and our caches. The
            # meshtastic library has historically keyed interface.nodes by either
            # an integer node number or a "!hex" string depending on version, so
            # we normalise up front to keep traceroute/position merges working.
            def _norm_id(raw: Any) -> Optional[str]:
                if raw is None:
                    return None
                if isinstance(raw, str):
                    s = raw.strip()
                    return s if s.startswith("!") else ("!" + s)
                try:
                    return "!" + format(int(raw), "08x")
                except Exception:
                    return None

            # Merge the interface's latest node data into our persistent cache.
            # This keeps late-arriving traceroute/position updates visible on the
            # next poll even though the library updates interface.nodes async.
            with self._nodes_lock:
                cached = {n.get("id"): n for n in self._nodes if n.get("id")}
                for node_id, node_data in nodes_raw.items():
                    node_id = _norm_id(node_id)
                    if not node_id:
                        continue
                    # Extract the nested sub-objects safely — the meshtastic
                    # library returns protobuf-derived dicts whose keys may be
                    # absent.
                    user = node_data.get("user", {})
                    position = node_data.get("position", {})
                    device_metrics = node_data.get("deviceMetrics", {})
                    environment = node_data.get("environmentMetrics", {}) or {}

                    entry = {
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
                        "temperature": environment.get("temperature"),
                        "relative_humidity": environment.get("relative_humidity"),
                        "barometric_pressure": environment.get("barometric_pressure"),
                    }
                    # traceroute is NOT present in the library's raw node dict —
                    # it is only populated by _capture_traceroute into our own
                    # cache. We must preserve any previously-captured value
                    # rather than overwrite it with the (always-None) raw entry.
                    if node_id in cached:
                        prev = cached[node_id]
                        # Preserve any captured POSITION_APP fix (from a
                        # "Req. Position" request) before the raw library update
                        # potentially overwrites it with None — the library
                        # never writes these replies into interface.nodes.
                        prev_lat = prev.get("latitude")
                        prev_lng = prev.get("longitude")
                        prev_alt = prev.get("altitude")
                        cached[node_id].update(entry)
                        if prev_lat is not None:
                            cached[node_id]["latitude"] = prev_lat
                        if prev_lng is not None:
                            cached[node_id]["longitude"] = prev_lng
                        if prev_alt is not None:
                            cached[node_id]["altitude"] = prev_alt
                        result.append(cached[node_id])
                    else:
                        entry["traceroute"] = None
                        cached[node_id] = entry
                        result.append(entry)

                # Merge persisted traceroute results back onto their nodes so a
                # previously-discovered route is shown even before (or without)
                # a fresh traceroute request this session.
                for tid, rec in self._traceroutes.items():
                    if tid in cached and rec:
                        cached[tid]["traceroute"] = rec

                self._nodes = list(cached.values())

            return result

    def _get_channels_sync(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._connected or self._interface is None:
                return []

            result = []

            # Prefer the canonical, full channel list from the node's local
            # config (channel_settings is a repeated field indexed 0..N and
            # always includes every provisioned channel, even ones the library
            # might not surface via interface.channels). Fall back to
            # interface.channels if localConfig isn't available.
            local_config = getattr(self._interface, "localConfig", None)
            channel_settings = getattr(local_config, "channel_settings", None)

            if channel_settings:
                for idx, settings in enumerate(channel_settings):
                    name = getattr(settings, "name", "") or ""
                    role = str(getattr(settings, "role", ""))
                    psk = getattr(settings, "psk", b"") or b""
                    # Channel 0 (Primary) is always present. Other slots are only
                    # meaningful if they carry a name or a real (non-zero) PSK —
                    # an empty PSK marks an unconfigured/disabled placeholder.
                    is_primary = idx == 0
                    configured = bool(name) or len(psk) > 1
                    if not is_primary and not configured:
                        continue
                    result.append({
                        "index": idx,
                        "name": name,
                        "role": role,
                    })
            else:
                channels = getattr(self._interface, "channels", {}) or {}
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
                    "Failed to send message (destination=%s): %s", destination, exc
                )
                return False

    def _request_traceroute_sync(self, destination: str) -> bool:
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            try:
                # hopLimit is a REQUIRED positional argument in meshtastic 2.7.x —
                # passing None raises TypeError. A hopLimit of 0 would prevent the
                # packet from relaying past directly-connected nodes, so most
                # traceroutes would silently fail. We use a sane high value (10)
                # which lets the firmware traverse the mesh; a 0 here is NOT the
                # "default", it actually disables relaying.
                # The call blocks until the RouteDiscovery reply arrives (the
                # library waits internally for the acknowledgment flag). The
                # actual per-hop route SNR is NOT stored by the library, so we
                # capture it ourselves in _on_mesh_receive (TRACEROUTE_APP) and
                # merge it into our node cache below.
                self._pending_traceroute_dest = destination
                self._interface.sendTraceRoute(destination, 10)
                # Pull whatever route data we've captured so far into the cache.
                self._refresh_node_from_interface(destination)
                return True
            except Exception as exc:
                logger.error(
                    "Traceroute request failed (destination=%s): %s", destination, exc
                )
                return False

    def _request_position_sync(self, destination: str) -> bool:
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            try:
                # Request a position with wantResponse=True. The node replies with
                # a POSITION_APP packet which the library funnels through
                # onResponsePosition -> _onPositionReceive, updating its node DB.
                # We then copy that fresh fix into our own nodes dict below.
                self._interface.sendPosition(destinationId=destination, wantResponse=True)
                return True
            except Exception as exc:
                logger.error(
                    "Position request failed (destination=%s): %s", destination, exc
                )
                return False

    def _refresh_node_from_interface(self, node_id: str) -> None:
        """
        Merge the latest position/metrics for a node from the meshtastic
        library's own node DB into our cached ``self._nodes`` dict.

        The library updates ``interface.nodes`` asynchronously when a
        traceroute/position response arrives. The next ``_get_nodes_sync`` call
        already re-merges everything, but calling this ensures the freshly
        returned data is reflected on the immediate next poll.
        """
        if self._interface is None or not node_id:
            return
        lib_node = self._interface.nodes.get(node_id)
        if lib_node is None:
            return
        with self._nodes_lock:
            existing = next(
                (n for n in self._nodes if n.get("id") == node_id), None
            )
            if existing is not None:
                # Preserve our own captured traceroute/position data, which the
                # library's raw node dict does not carry (it only sets an
                # internal ack flag). A blind update would clobber it.
                captured_traceroute = existing.get("traceroute")
                captured_lat = existing.get("latitude")
                captured_lng = existing.get("longitude")
                captured_alt = existing.get("altitude")
                existing.update(lib_node)
                if captured_traceroute is not None:
                    existing["traceroute"] = captured_traceroute
                if captured_lat is not None:
                    existing["latitude"] = captured_lat
                if captured_lng is not None:
                    existing["longitude"] = captured_lng
                if captured_alt is not None:
                    existing["altitude"] = captured_alt
            else:
                self._nodes.append(dict(lib_node))
