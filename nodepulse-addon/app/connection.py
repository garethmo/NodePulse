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
import re

# Persistent storage directory. Under HA Supervisor this is /data, which
# survives addon restarts (unlike the container's ephemeral filesystem). We
# persist the recent message buffer there so the Web UI's message history is
# not lost when the addon is restarted.
_DATA_DIR = os.environ.get("NODEPULSE_DATA_DIR", "/data")
_MESSAGES_FILE = os.path.join(_DATA_DIR, "messages.json")
_TRACEROUTES_FILE = os.path.join(_DATA_DIR, "traceroutes.json")
_CHANNELS_FILE = os.path.join(_DATA_DIR, "channels.json")
_NODES_FILE = os.path.join(_DATA_DIR, "nodes.json")
_TAGS_FILE = os.path.join(_DATA_DIR, "tags.json")
_POSITION_HISTORY_FILE = os.path.join(_DATA_DIR, "position_history.json")
logger = logging.getLogger(__name__)

# A canonical Meshtastic node ID is a "!" followed by the node number in hex.
# Node numbers are uint32, so 8 hex digits is the full width. This regex is
# used to validate destinations supplied by the Web UI before we hand them to
# the meshtastic library.
_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{1,8}$")

# Max position history entries to keep per node (oldest dropped when exceeded).
_POS_HISTORY_MAX = 200

# Minimum delay (seconds) between traceroute persistence flushes, so a burst of
# traceroute replies cannot spawn a save thread on every single capture.
_TRACEROUTE_SAVE_DEBOUNCE = 1.0

# Debounce for persisting the node list. Node updates arrive with every poll,
# so we coalesce them into at most one disk write per window.
_NODE_SAVE_DEBOUNCE = 5.0

# Max number of persisted nodes to keep. The radio's node DB is bounded (~250),
# but we accumulate evicted nodes over time. Cap to avoid unbounded growth.
_MAX_PERSISTED_NODES = 500

# --- ACK status constants ---------------------------------------------------
# Seconds after which an outgoing DM that received no ROUTING_APP reply is
# considered failed and its ack_status flipped to "failed".
_ACK_TIMEOUT_S = 30.0

# --- Packet inspector / sniffer constants -----------------------------------
# Maximum number of packets kept in the in-memory capture ring buffer.
_PACKET_LOG_MAX = 500

# --- Signal quality thresholds (SNR in dB) ----------------------------------
_SNR_EXCELLENT = 5.0
_SNR_GOOD = 0.0
_SNR_FAIR = -10.0
# Below _SNR_FAIR → "poor"


def _node_id_from_num(num: Any) -> Optional[str]:
    """Format a Meshtastic node number as a canonical "!hex" ID.

    Used everywhere we turn a raw packet ``from``/``to`` integer into the
    "!xxxxxxxx" form that the Web UI and our caches key on, so the formatting
    logic lives in exactly one place.
    """
    if num is None:
        return None
    try:
        return "!" + format(int(num) & 0xFFFFFFFF, "08x")
    except (TypeError, ValueError):
        return None


def _channel_role_name(value: int) -> str:
    """Map a Meshtastic Channel.Role integer to its enum name (e.g. 'PRIMARY').

    Imported lazily so the protobuf is only loaded when actually needed.
    Falls back to the raw integer string if the enum can't be resolved.
    """
    try:
        from meshtastic.protobuf.channel_pb2 import Channel
        return Channel.Role.Name(value)
    except Exception:
        return str(value)

# How long (seconds) to wait between reconnection attempts.
# Using a capped exponential backoff to avoid hammering an offline node.
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 60

# Max time (seconds) to wait for a single TCP connect attempt before giving
# up. The meshtastic TCPInterface constructor has no built-in connect timeout,
# so a node that accepts the SYN but never completes the handshake (or a black
# hole) would otherwise block forever and leave the addon stuck "Connecting".
_CONNECT_TIMEOUT = 15

# Interval (seconds) between active health probes while the connection looks
# healthy. A passive "is the socket object present?" check is not enough:
# Meshtastic firmware allows only one TCP client per node, and a session can
# be dropped silently (node reboot, firmware slot reclaim, network blip) while
# the OS socket object still exists. We proactively query the node to confirm
# it is actually responsive, and reconnect if it is not.
_HEALTH_CHECK_INTERVAL = 60

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
        # Lock ordering rule: always acquire _lock BEFORE _nodes_lock.
        # Never acquire _lock while holding _nodes_lock.
        self._lock = threading.Lock()

        # Guard against two concurrent _connect_sync calls racing on _interface.
        # Set to True while a connect is in-progress; callers that see it True
        # return immediately rather than starting a second connection attempt.
        self._connecting = False

        # Tracks whether the pubsub listener is currently subscribed. The
        # meshtastic library raises if you subscribe the same listener twice, and
        # silently accumulates duplicates in some versions — we use this flag to
        # ensure we subscribe at most once at any time.
        self._subscribed = False

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

        # Monotonic message counter for dedup-safe ID generation.
        # Incremented under _msg_lock alongside the deque append; guaranteed
        # unique even when two messages arrive on the same millisecond tick.
        self._msg_counter = 0

        # Persisted traceroute results, keyed by node ID. Restored on startup so
        # discovered routes survive restarts; refreshed whenever a new traceroute
        # for that node completes.
        self._traceroutes: Dict[str, Dict[str, Any]] = {}
        self._load_traceroutes()

        # Mutable cache of the channel configuration. Fetched during connection
        # handshake so all channels are immediately available in the UI, even
        # before any messages are sent on them.
        self._channels_lock = threading.Lock()
        self._channels: List[Dict[str, Any]] = []
        self._load_channels()

        # Persisted user-defined tags per node ID. Simple dict: node_id -> [tag strings].
        self._tags_lock = threading.Lock()
        self._tags: Dict[str, List[str]] = {}
        self._load_tags()

        # Position history per node: node_id -> [{lat, lng, alt, timestamp}, ...]
        # Capped at _POS_HISTORY_MAX entries per node.
        self._pos_hist_lock = threading.Lock()
        self._pos_history: Dict[str, List[Dict[str, Any]]] = {}
        self._load_position_history()

        # Persisted node list. The radio only keeps a bounded node DB (often
        # ~250 entries); once it is full, the oldest heard nodes drop out of
        # interface.nodes and would vanish from the UI. We persist every node
        # we've ever seen and re-inject any that the radio no longer reports, so
        # the full history survives both radio eviction and addon restarts.
        # Re-injected nodes are flagged "stale": True so the UI can show them
        # faded, and their last-known position is retained on the map.
        self._nodes_lock = threading.Lock()
        self._nodes: List[Dict[str, Any]] = []
        self._load_nodes()

        self._last_node_save = 0.0
        self._pending_node_save = False

        # Destination node IDs of in-flight traceroute requests. The reply
        # packet only carries the origin (the responding node), not the original
        # request target, so we remember the destinations to attribute the
        # route correctly. A FIFO stack (list) lets overlapping traceroute
        # requests each be attributed to the right target instead of clobbering
        # a single shared slot.
        self._pending_traceroute_dests: List[str] = []

        # Destination node IDs of in-flight position requests. Used to attribute
        # inbound POSITION_APP replies to the node we actually asked, so we don't
        # treat every broadcast position as a response to our request.
        self._pending_position_dests: set = set()

        # Timestamp (monotonic-ish, seconds) of the last traceroute persistence
        # flush, used to debounce _save_traceroutes. A pending debounced save is
        # tracked so a burst ends with a final flush of the latest state.
        self._last_traceroute_save = 0.0
        self._pending_traceroute_save = False

        # --- Feature: Message ACK status ------------------------------------
        # Maps outgoing packet_id (int) → internal message entry id (str).
        # When a ROUTING_APP ack arrives we look up the entry and flip its
        # ack_status from "sending" to "delivered" or "failed".
        # Bounded by expiry sweep — entries older than _ACK_TIMEOUT_S are
        # evicted and their messages marked "failed".
        self._pending_acks: Dict[int, str] = {}
        self._pending_ack_times: Dict[int, float] = {}

        # --- Feature: Signal quality trend ----------------------------------
        # Rolling window of the last 10 rxSnr readings per node. In-memory only
        # (no persistence needed — repopulates within a few poll cycles).
        self._snr_lock = threading.Lock()
        self._snr_history: Dict[str, collections.deque] = {}

        # --- Feature: Packet inspector / sniffer ----------------------------
        # Shared ring buffer of all inbound decoded packets (newest first).
        # Capped at _PACKET_LOG_MAX. In-memory only; not persisted.
        self._packet_log_lock = threading.Lock()
        self._packet_log: collections.deque = collections.deque(maxlen=_PACKET_LOG_MAX)

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

    async def clear_stale_nodes(self) -> int:
        """Drop every node flagged ``stale`` (not currently heard by the radio).

        The persistent node store keeps nodes that the radio's bounded DB has
        evicted so they remain visible. This lets the user purge that history
        on demand — e.g. after a mesh reshuffle — so only live-heard nodes
        remain. Returns the number of nodes removed.
        """
        return await asyncio.to_thread(self._clear_stale_nodes_sync)

    def _clear_stale_nodes_sync(self) -> int:
        with self._nodes_lock:
            before = len(self._nodes)
            self._nodes = [n for n in self._nodes if not n.get("stale")]
            removed = before - len(self._nodes)
        if removed:
            self._save_nodes()
            logger.debug("Cleared %s stale (cached) nodes from the store", removed)
        return removed

    async def get_channels(self) -> List[Dict[str, Any]]:
        """Return the channel configuration from the connected node."""
        return await asyncio.to_thread(self._get_channels_sync)

    async def refresh_channels(self) -> List[Dict[str, Any]]:
        """Force a fresh channel read from the node and update the cache.

        Used by the periodic background refresh and right after a (re)connection
        so the Web UI's channel list/channel tabs stay in sync with the radio
        without waiting for a config push.
        """
        return await asyncio.to_thread(self._refresh_channels_sync)

    async def run_channel_refresh_loop(self, interval: float = 300.0) -> None:
        """Background task: periodically refresh the channel list.

        Channels rarely change, so a 5-minute cadence is plenty. Best-effort:
        failures are logged but never terminate the loop.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                if self._connected:
                    await self.refresh_channels()
                    logger.debug("Periodic channel refresh completed")
            except Exception as exc:  # defensive: never crash the task
                logger.debug("Periodic channel refresh failed (ignored): %s", exc)

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
        """Request a traceroute towards a specific destination node.

        Returns immediately after queueing the request. The blocking firmware
        round-trip (``sendTraceRoute`` can wait 30 s+ for the RouteDiscovery
        ack) is performed in a background task so the HTTP request does not
        exceed the addon ingress proxy timeout (which returns HTTP 503). The
        UI polls ``/api/nodes`` to see the discovered route once it arrives.
        """
        logger.debug("Requesting traceroute to %s", destination)
        # Record the in-flight destination now so the reply can be attributed
        # even if the firmware ack is slow to arrive.
        with self._lock:
            self._pending_traceroute_dests.append(destination)
        try:
            # Use get_running_loop() (correct inside a coroutine). get_event_loop()
            # is deprecated in 3.10+ and raises RuntimeError on 3.12+ when no
            # loop is bound to the current context.
            loop = asyncio.get_running_loop()
            loop.create_task(self._traceroute_dispatch(destination))
        except RuntimeError:
            # No running loop (e.g. offline test harness): fall back to a
            # thread so the request still completes.
            await asyncio.to_thread(self._request_traceroute_sync, destination)
        return True

    async def _traceroute_dispatch(self, destination: str) -> None:
        """Background: perform the blocking send + cache refresh for a traceroute."""
        try:
            await asyncio.to_thread(self._request_traceroute_sync, destination)
            # Pull whatever route data we've captured into the cache.
            await asyncio.to_thread(self._refresh_node_from_interface, destination)
            logger.debug("Traceroute dispatch to %s completed", destination)
        except Exception as exc:  # defensive: never crash the background task
            logger.error("Traceroute background dispatch failed (%s): %s", destination, exc)

    async def request_position(self, destination: str) -> bool:
        """Request a fresh GPS position from a specific destination node."""
        ok = await asyncio.to_thread(self._request_position_sync, destination)
        # Merge the node's reply position into our cache.
        await asyncio.to_thread(self._refresh_node_from_interface, destination)
        return ok

    async def get_messages(self) -> List[Dict[str, Any]]:
        """Return the most recent received text messages (oldest first)."""
        return await asyncio.to_thread(self._get_messages_sync)

    async def get_tags(self) -> Dict[str, List[str]]:
        """Return the full tags dict: node_id -> list of tag strings."""
        return await asyncio.to_thread(self._get_tags_sync)

    async def set_tags(self, node_id: str, tags: List[str]) -> Dict[str, List[str]]:
        """Set the tags for a single node and persist. Returns the full tags dict."""
        return await asyncio.to_thread(self._set_tags_sync, node_id, tags)

    async def get_position_history(self, node_id: Optional[str] = None) -> Dict[str, List[Dict]]:
        """Return position history. If node_id is given, return only that node's trail."""
        return await asyncio.to_thread(self._get_position_history_sync, node_id)

    async def get_packet_log(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Return the most recent captured packets (newest first, up to limit)."""
        return await asyncio.to_thread(self._get_packet_log_sync, limit)

    async def get_sniffer_stats(self) -> Dict[str, Any]:
        """Return live sniffer statistics computed over the last 60 seconds."""
        return await asyncio.to_thread(self._get_sniffer_stats_sync)

    async def expire_pending_acks(self) -> None:
        """Sweep timed-out pending ACKs and mark their messages failed.

        Called periodically (every 10 s) by a background task so messages
        that never receive a ROUTING_APP reply within _ACK_TIMEOUT_S are
        correctly marked as failed instead of staying stuck at 'sending'.
        """
        await asyncio.to_thread(self._expire_pending_acks_sync)

    async def monitor_connection(self) -> None:
        """
        Background coroutine that keeps the connection to the Meshtastic node
        live. It runs as a persistent asyncio Task from main.py and is the ONLY
        place reconnection is initiated (avoids races from multiple callers).

        While the session looks healthy we probe the node every
        ``_HEALTH_CHECK_INTERVAL`` seconds. The probe is an active query (not a
        passive socket check) so a silently-dropped session is detected and
        recovered quickly instead of looking "connected" while delivering no
        data. When the probe fails we reconnect with capped exponential
        backoff.
        """
        delay = _RECONNECT_BASE_DELAY
        is_first_attempt = True
        while True:
            # Wait before each check. On the very first loop we connect
            # immediately (no backoff); afterwards we wait either the health
            # interval (when healthy) or the current backoff (when reconnecting).
            if not is_first_attempt:
                await asyncio.sleep(delay)

            if not self._is_interface_healthy():
                # On the very first iteration there is no prior connection, so
                # use INFO rather than WARNING to avoid alarming log noise at
                # every normal startup.
                if is_first_attempt:
                    logger.debug(
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
                    delay = _HEALTH_CHECK_INTERVAL  # healthy: poll at the probe cadence
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
            else:
                # Healthy: report connected and poll again at the probe cadence.
                if not self._connected:
                    # Just transitioned from disconnected -> connected: refresh
                    # channels immediately so the UI reflects the radio's current
                    # channel config without waiting for a config push.
                    try:
                        self._refresh_channels_sync()
                        logger.debug("Refreshed channels after (re)connection")
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Post-connect channel refresh failed (ignored): %s", exc)
                self._connected = True
                delay = _HEALTH_CHECK_INTERVAL

            is_first_attempt = False

    # ------------------------------------------------------------------
    # Private synchronous helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    def _connect_sync(self) -> None:
        """
        Synchronous connection attempt. Runs inside a thread pool worker.

        We close any stale interface before creating a new one to avoid
        resource leaks (file descriptors, threads) from orphaned TCP sockets.

        A _connecting guard prevents two concurrent connect attempts from
        racing on self._interface when asyncio.wait_for times out and the
        monitor loop fires again before the previous thread finishes.

        IMPORTANT — lock hygiene: _lock is held only while reading/writing
        shared state (_interface, _connecting, _subscribed, _connected). It
        is NEVER held during blocking network I/O (TCPInterface constructor,
        sendText, sendTraceRoute, requestConfig, fetchNodeDB). Holding _lock
        during those calls would stall every other thread (get_nodes, health
        probe, etc.) for the full network round-trip timeout.
        """
        # Guard check + setup under the lock — no blocking I/O here.
        with self._lock:
            if self._connecting:
                logger.debug("_connect_sync called while connect already in-progress — skipping")
                return
            self._connecting = True

        # Close any stale interface OUTSIDE the lock. _close_sync acquires
        # _lock itself for the brief state-mutation steps it needs.
        self._close_sync()

        logger.debug(
            "Connecting to Meshtastic node (host=%s, port=%s)", self._host, self._port
        )

        new_interface = None
        try:
            # TCPInterface() blocks until the TCP handshake completes (or
            # raises). We do this OUTSIDE _lock so other threads (health probe,
            # get_nodes) are never stalled waiting for a network timeout.
            kwargs = {"hostname": self._host, "portNumber": self._port, "debugOut": None}
            try:
                kwargs["access_key"] = self._access_key
                new_interface = meshtastic.tcp_interface.TCPInterface(**kwargs)
            except TypeError:
                # Older library without an access_key kwarg.
                kwargs.pop("access_key", None)
                new_interface = meshtastic.tcp_interface.TCPInterface(**kwargs)
                if self._access_key is not None:
                    try:
                        new_interface.access_key = self._access_key
                    except AttributeError:
                        logger.debug(
                            "meshtastic library does not support access_key — ignoring"
                        )

            # Ensure the key is set for library versions that only accept it
            # post-construction.
            if self._access_key is not None and getattr(new_interface, "access_key", None) is None:
                try:
                    new_interface.access_key = self._access_key
                except AttributeError:
                    pass

        except Exception as exc:
            with self._lock:
                self._connecting = False
                self._connected = False
                self._interface = None
            if self._mode == "proxy":
                logger.error(
                    "Connection to Meshtastic TCP proxy at %s:%s rejected. "
                    "Ensure the official Meshtastic HA integration is installed "
                    "and its 'TCP Proxy' option is enabled, and that proxy_host/"
                    "proxy_port are correct. Underlying error: %s",
                    self._host, self._port, exc,
                )
            elif _looks_like_slot_conflict(exc):
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

        # Connection succeeded — publish the new interface and subscribe.
        with self._lock:
            self._interface = new_interface
            self._connected = True
            self._connecting = False
            # Subscribe to inbound packets. We only subscribe once — subscribing
            # the same listener twice causes duplicate packet delivery.
            if not self._subscribed:
                try:
                    pub.subscribe(self._on_mesh_receive, "meshtastic.receive")
                    self._subscribed = True
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Could not subscribe to meshtastic receive events: %s", exc)

        logger.debug(
            "Connected to Meshtastic node (host=%s, port=%s)", self._host, self._port
        )

        # Proactively request the node DB so the dashboard populates quickly.
        # Called OUTSIDE _lock — these library methods do blocking radio I/O.
        self._trigger_node_db_sync()

    def _trigger_node_db_sync(self) -> None:
        """
        Best-effort: ask the radio to push its node DB + config immediately.

        Called right after a (re)connection. The meshtastic library only fills
        ``interface.nodes`` as NodeInfo packets arrive opportunistically, which
        can be very slow; explicitly requesting the data makes the Web UI /
        API show nodes within a couple of seconds of connecting.

        Tries the common library entry points in order; anything missing on the
        current library version is skipped. Failures are logged but never fatal
        — the lazy sync still works as a fallback.
        """
        iface = self._interface
        if iface is None:
            return

        # 1) Request the full config (also prompts a node-info push).
        for meth in ("requestConfig", "requestConfigCompressed"):
            fn = getattr(iface, meth, None)
            if callable(fn):
                try:
                    fn()
                    logger.debug("Requested node config via %s()", meth)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("requestConfig (%s) failed (ignored): %s", meth, exc)
                break

        # 2) Explicitly fetch the node DB if the library exposes it.
        fetch_db = getattr(iface, "fetchNodeDB", None)
        if callable(fetch_db):
            try:
                fetch_db()
                logger.debug("Fetched node DB via fetchNodeDB()")
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("fetchNodeDB() failed (ignored): %s", exc)

        # 3) Probe our own node info — this forces the radio to respond with a
        #    NodeInfo packet, kick-starting the node-DB sync.
        for meth in ("getMyNodeInfo", "getNode"):
            fn = getattr(iface, meth, None)
            if callable(fn):
                try:
                    if meth == "getNode":
                        fn("^local")
                    else:
                        fn()
                    logger.debug("Triggered node-info via %s()", meth)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("%s() failed (ignored): %s", meth, exc)
                break

    def _close_sync(self) -> None:
        """Close the current interface, suppressing errors (already closed, etc.).

        This method is safe to call with or without _lock held. It acquires
        _lock itself for the brief state-mutation steps, and then calls
        interface.close() OUTSIDE the lock so the blocking socket teardown
        does not prevent concurrent health probes or poll calls from running.
        """
        # Swap out shared state under the lock, then close without holding it.
        with self._lock:
            if self._subscribed:
                try:
                    pub.unsubscribe(self._on_mesh_receive, "meshtastic.receive")
                except Exception:
                    pass
                self._subscribed = False

            iface_to_close = self._interface
            if iface_to_close is not None:
                self._interface = None
                self._connected = False

        # Close the old interface outside the lock. interface.close() may block
        # briefly on socket teardown; releasing the lock here lets other threads
        # continue polling while the TCP connection drains.
        if iface_to_close is not None:
            try:
                iface_to_close.close()
            except Exception as exc:
                logger.debug("Exception during interface close (ignored): %s", exc)

    def _on_mesh_receive(self, packet: Dict[str, Any], interface=None) -> None:
        """
        Pubsub listener for inbound Meshtastic packets.

        Every packet is appended to the shared inspector/sniffer ring buffer
        and its SNR is recorded for signal-quality trending.
        Text messages are captured into the ring buffer for the Web UI feed.
        Traceroute replies (TRACEROUTE_APP) are captured into the per-node
        cache. ROUTING_APP ACKs update outgoing message delivery status.
        Runs on the meshtastic library's background thread, so shared state
        access is locked.
        """
        try:
            decoded = packet.get("decoded", {}) or {}
            portnum = decoded.get("portnum")

            # Record every packet in the shared inspector/sniffer buffer.
            self._capture_packet_log(packet)

            # Update per-node SNR history for signal-quality trending.
            from_num = packet.get("from")
            from_id_snr = _node_id_from_num(from_num)
            rx_snr = packet.get("rxSnr")
            if from_id_snr and rx_snr is not None:
                with self._snr_lock:
                    if from_id_snr not in self._snr_history:
                        self._snr_history[from_id_snr] = collections.deque(maxlen=10)
                    self._snr_history[from_id_snr].append(float(rx_snr))

            # --- Neighbour info -------------------------------------------
            if portnum == "NEIGHBORINFO_APP":
                self._capture_neighborinfo(packet)
                return

            # --- Traceroute replies ---------------------------------------
            if portnum == "TRACEROUTE_APP":
                self._capture_traceroute(packet)
                return

            # --- Position replies -----------------------------------------
            if portnum == "POSITION_APP":
                self._capture_position(packet)
                return

            # --- Routing ACKs (delivery confirmation) --------------------
            if portnum == "ROUTING_APP":
                self._capture_routing_ack(packet)
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
                    user = node.get("user") or {}
                    # Prefer the short name in the chat window (compact), falling
                    # back to the long name when no short name is set.
                    name = user.get("shortName") or user.get("longName")

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
                entry["id"] = f"{from_id or 'unknown'}-{channel}-{self._msg_counter}"
                self._msg_counter += 1
                self._messages.append(entry)
            # Persistence is offloaded to a short-lived daemon thread so we
            # never block the meshtastic receive thread (and never interleave
            # writes — _save_messages takes _persist_lock).
            self._schedule_save(self._messages, _MESSAGES_FILE)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error handling received packet (ignored): %s", exc)

    # ----------------------------------------------------------------
    # Packet inspector / sniffer helpers
    # ----------------------------------------------------------------

    def _capture_packet_log(self, packet: Dict[str, Any]) -> None:
        """Append a sanitised summary of every received packet to the ring buffer.

        Called at the top of _on_mesh_receive for every inbound packet so both
        the packet inspector (Feature 4) and the LoRa sniffer stats (Feature 5)
        share a single capture point. Runs on the meshtastic receive thread so
        the lock must be acquired briefly.
        """
        try:
            decoded = packet.get("decoded", {}) or {}
            entry = {
                "id":        packet.get("id"),
                "from_id":   _node_id_from_num(packet.get("from")),
                "to_id":     _node_id_from_num(packet.get("to")),
                "portnum":   decoded.get("portnum") or "UNKNOWN",
                "channel":   packet.get("channel", 0),
                "rx_snr":    packet.get("rxSnr"),
                "rx_rssi":   packet.get("rxRssi"),
                "hop_limit": packet.get("hopLimit"),
                "hop_start": packet.get("hopStart"),
                "want_ack":  bool(packet.get("wantAck")),
                "via_mqtt":  bool(packet.get("viaMqtt")),
                "decoded_ok": bool(decoded.get("portnum")),
                "timestamp": int(time.time()),
                # Full decoded payload serialised to a JSON-safe dict. Bytes
                # objects (raw payloads) are hex-encoded; enums are stringified.
                "decoded": self._safe_json_value(decoded),
            }
            with self._packet_log_lock:
                self._packet_log.appendleft(entry)  # newest first
        except Exception as exc:  # defensive — never crash the receive thread
            logger.debug("Error capturing packet to log (ignored): %s", exc)

    def _safe_json_value(self, obj: Any) -> Any:
        """Recursively convert an arbitrary value to a JSON-serialisable form.

        The meshtastic library returns decoded packet dicts that may contain
        bytes (raw payload), protobuf enum objects, or nested dicts. This
        function recursively sanitises them so json.dumps() won't raise.
        """
        if isinstance(obj, dict):
            return {k: self._safe_json_value(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._safe_json_value(i) for i in obj]
        if isinstance(obj, bytes):
            return obj.hex()
        if isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        # Protobuf enum objects and other types: stringify.
        return str(obj)

    # ----------------------------------------------------------------
    # Routing ACK helpers (message delivery status)
    # ----------------------------------------------------------------

    def _capture_routing_ack(self, packet: Dict[str, Any]) -> None:
        """Handle a ROUTING_APP packet — update outgoing message ACK status.

        Meshtastic firmware sends a ROUTING_APP packet after delivering a DM.
        The packet carries a ``requestId`` matching the packet ID returned by
        ``sendText``, and an ``errorReason`` of NONE (success) or a non-zero
        code (failure). We match it against _pending_acks and flip the
        corresponding message entry's ack_status.
        """
        try:
            decoded = packet.get("decoded", {}) or {}
            routing = decoded.get("routing") or {}
            # requestId may sit at the top level or inside the routing sub-dict,
            # depending on the meshtastic library version.
            request_id = routing.get("requestId") or packet.get("requestId")
            if not request_id:
                return
            try:
                request_id = int(request_id)
            except (TypeError, ValueError):
                return

            error_reason = routing.get("errorReason", "NONE")
            status = "delivered" if error_reason in ("NONE", 0, "", None) else "failed"

            with self._msg_lock:
                msg_id = self._pending_acks.pop(request_id, None)
                self._pending_ack_times.pop(request_id, None)
                if msg_id is None:
                    return
                for msg in self._messages:
                    if msg.get("id") == msg_id:
                        msg["ack_status"] = status
                        msg["ack_at"] = int(time.time())
                        break
            self._schedule_save(self._messages, _MESSAGES_FILE)
            logger.debug("ACK received for msg_id=%s: status=%s", msg_id, status)
        except Exception as exc:  # defensive
            logger.debug("Error handling routing ACK (ignored): %s", exc)

    def _expire_pending_acks_sync(self) -> None:
        """Mark timed-out outgoing messages as failed.

        Any pending ACK older than _ACK_TIMEOUT_S that has not yet received a
        ROUTING_APP reply is considered failed. Called from a periodic background
        task via expire_pending_acks().
        """
        now = time.time()
        timed_out = [
            pid for pid, sent_at in list(self._pending_ack_times.items())
            if now - sent_at > _ACK_TIMEOUT_S
        ]
        if not timed_out:
            return
        with self._msg_lock:
            for pid in timed_out:
                msg_id = self._pending_acks.pop(pid, None)
                self._pending_ack_times.pop(pid, None)
                if msg_id is None:
                    continue
                for msg in self._messages:
                    if msg.get("id") == msg_id and msg.get("ack_status") == "sending":
                        msg["ack_status"] = "failed"
                        msg["ack_at"] = int(time.time())
                        break
        self._schedule_save(self._messages, _MESSAGES_FILE)
        logger.debug("Expired %s timed-out pending ACKs", len(timed_out))

    # ----------------------------------------------------------------
    # Signal quality helpers
    # ----------------------------------------------------------------

    def _signal_quality(self, node_id: str) -> str:
        """Compute a signal quality label from the rolling SNR history.

        Uses a window of up to 10 recent SNR readings. Returns 'no_signal' if
        no readings exist for the node (e.g. stale/offline nodes).
        """
        with self._snr_lock:
            history = self._snr_history.get(node_id)
            if not history:
                return "no_signal"
            avg = sum(history) / len(history)
        if avg >= _SNR_EXCELLENT:
            return "excellent"
        if avg >= _SNR_GOOD:
            return "good"
        if avg >= _SNR_FAIR:
            return "fair"
        return "poor"

    def _snr_avg(self, node_id: str) -> Optional[float]:
        """Return the rolling SNR average (1 d.p.) or None if no history exists."""
        with self._snr_lock:
            history = self._snr_history.get(node_id)
            if not history:
                return None
            return round(sum(history) / len(history), 1)

    # ----------------------------------------------------------------
    # Packet log / sniffer accessors
    # ----------------------------------------------------------------

    def _get_packet_log_sync(self, limit: int) -> List[Dict[str, Any]]:
        """Return the most recent `limit` entries from the packet ring buffer."""
        import itertools
        with self._packet_log_lock:
            return list(itertools.islice(self._packet_log, limit))

    def _get_sniffer_stats_sync(self) -> Dict[str, Any]:
        """Compute sniffer statistics over the last 60 seconds from the packet log."""
        now = time.time()
        window = 60.0
        with self._packet_log_lock:
            recent = [p for p in self._packet_log if (now - p["timestamp"]) <= window]
            total = len(self._packet_log)
        portnum_counts: Dict[str, int] = {}
        unique_nodes: set = set()
        for p in recent:
            portnum = p.get("portnum", "UNKNOWN")
            portnum_counts[portnum] = portnum_counts.get(portnum, 0) + 1
            if p.get("from_id"):
                unique_nodes.add(p["from_id"])
        return {
            "packets_per_minute": len(recent),
            "unique_nodes": len(unique_nodes),
            "portnum_distribution": portnum_counts,
            "total_captured": total,
        }

    def _load_tags(self) -> None:
        """Restore persisted node tags (best-effort)."""
        try:
            if not os.path.exists(_TAGS_FILE):
                return
            with self._persist_lock:
                with open(_TAGS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, dict):
                with self._tags_lock:
                    self._tags = {str(k): v for k, v in data.items()}
                logger.debug("Restored %s tagged nodes from %s", len(self._tags), _TAGS_FILE)
        except Exception as exc:
            logger.debug("Could not load persisted tags (ignored): %s", exc)

    def _save_tags(self) -> None:
        """Persist the tags dict to disk (best-effort)."""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with self._tags_lock:
                snapshot = dict(self._tags)
            self._write_json(snapshot, _TAGS_FILE)
        except Exception as exc:
            logger.debug("Could not persist tags (ignored): %s", exc)

    def _get_tags_sync(self) -> Dict[str, List[str]]:
        with self._tags_lock:
            return dict(self._tags)

    def _set_tags_sync(self, node_id: str, tags: List[str]) -> Dict[str, List[str]]:
        if not isinstance(tags, list):
            raise ValueError("tags must be a list of strings")
        clean = [t.strip() for t in tags if t.strip()]
        with self._tags_lock:
            if clean:
                self._tags[node_id] = clean
            else:
                self._tags.pop(node_id, None)
            result = dict(self._tags)
        self._save_tags()
        return result

    def _load_position_history(self) -> None:
        """Restore persisted position history (best-effort)."""
        try:
            if not os.path.exists(_POSITION_HISTORY_FILE):
                return
            with self._persist_lock:
                with open(_POSITION_HISTORY_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, dict):
                with self._pos_hist_lock:
                    self._pos_history = {}
                    for k, v in data.items():
                        if isinstance(v, list):
                            self._pos_history[str(k)] = v[-_POS_HISTORY_MAX:]
                logger.debug(
                    "Restored position history for %s nodes from %s",
                    len(self._pos_history), _POSITION_HISTORY_FILE,
                )
        except Exception as exc:
            logger.debug("Could not load persisted position history (ignored): %s", exc)

    def _save_position_history(self) -> None:
        """Persist the position history dict to disk (best-effort)."""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with self._pos_hist_lock:
                snapshot = dict(self._pos_history)
            self._write_json(snapshot, _POSITION_HISTORY_FILE)
        except Exception as exc:
            logger.debug("Could not persist position history (ignored): %s", exc)

    def _record_position(self, node_id: str, lat: float, lng: float, alt: Optional[float] = None, snr: Optional[float] = None, rssi: Optional[float] = None) -> None:
        """Append a position fix to a node's trail, capping at _POS_HISTORY_MAX."""
        if node_id is None or lat is None or lng is None:
            return
        entry = {"lat": lat, "lng": lng, "timestamp": int(time.time())}
        if alt is not None:
            entry["alt"] = alt
        if snr is not None:
            entry["snr"] = snr
        if rssi is not None:
            entry["rssi"] = rssi
        with self._pos_hist_lock:
            trail = self._pos_history.get(node_id, [])
            trail.append(entry)
            # Keep only the most recent N entries.
            if len(trail) > _POS_HISTORY_MAX:
                trail = trail[-_POS_HISTORY_MAX:]
            self._pos_history[node_id] = trail

    def _get_position_history_sync(self, node_id: Optional[str] = None) -> Dict[str, List[Dict]]:
        with self._pos_hist_lock:
            if node_id:
                trail = self._pos_history.get(node_id, [])
                return {node_id: list(trail)}
            return {k: list(v) for k, v in self._pos_history.items()}

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
                logger.debug("Restored %s messages from %s", len(self._messages), _MESSAGES_FILE)
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
            self._write_json(snapshot, _MESSAGES_FILE)
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
            tmp_path = path + ".tmp"
            with self._persist_lock:
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    json.dump(snapshot, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, path)
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
                logger.debug(
                    "Restored %s traceroute results from %s",
                    len(self._traceroutes), _TRACEROUTES_FILE,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load persisted traceroutes (ignored): %s", exc)

    def _load_channels(self) -> None:
        """Restore persisted channel configuration (best-effort)."""
        try:
            if not os.path.exists(_CHANNELS_FILE):
                return
            with self._persist_lock:
                with open(_CHANNELS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, list):
                with self._channels_lock:
                    self._channels = data
                logger.debug("Restored %s channels from %s", len(self._channels), _CHANNELS_FILE)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load persisted channels (ignored): %s", exc)

    def _save_channels(self) -> None:
        """Persist the current channel configuration to disk (best-effort)."""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with self._channels_lock:
                snapshot = list(self._channels)
            self._write_json(snapshot, _CHANNELS_FILE)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not persist channels (ignored): %s", exc)

    def _load_nodes(self) -> None:
        """Restore the persisted node list so evicted nodes survive restarts.

        The radio's node DB is bounded (~250 entries) and silently drops the
        oldest heard nodes once full. We keep our own durable copy and
        re-inject those nodes into the cache on startup, flagged ``stale`` so
        the UI can distinguish radio-present nodes from restored ones.
        """
        try:
            if not os.path.exists(_NODES_FILE):
                return
            with self._persist_lock:
                with open(_NODES_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            if isinstance(data, list):
                with self._nodes_lock:
                    for n in data:
                        if isinstance(n, dict) and n.get("id"):
                            self._nodes.append(n)
                logger.debug(
                    "Restored %s persisted nodes from %s",
                    len(self._nodes), _NODES_FILE,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load persisted nodes (ignored): %s", exc)

    def _save_nodes(self, *, _is_scheduled: bool = False) -> None:
        """Persist the full node list to disk so evicted nodes survive.

        Offloaded to a daemon thread (like traceroutes) and debounced so a
        burst of node updates cannot spawn a save per change; the latest state
        within the debounce window is flushed shortly after the storm settles.

        The ``_is_scheduled`` parameter is an internal sentinel: when True the
        call comes from a previously-scheduled Timer — debouncing has already
        elapsed so we flush immediately without re-debouncing.
        """
        try:
            now = time.time()
            with self._nodes_lock:
                if (
                    not _is_scheduled
                    and _NODE_SAVE_DEBOUNCE > 0
                    and now - self._last_node_save < _NODE_SAVE_DEBOUNCE
                ):
                    if not self._pending_node_save:
                        self._pending_node_save = True
                        threading.Timer(
                            _NODE_SAVE_DEBOUNCE,
                            self._save_nodes,
                            kwargs={"_is_scheduled": True},
                        ).start()
                    return
                self._last_node_save = now
                self._pending_node_save = False
                # Cap persisted nodes: keep the most recently heard first.
                nodes_sorted = sorted(
                    self._nodes,
                    key=lambda n: n.get("last_heard") or 0,
                    reverse=True,
                )
                snapshot = nodes_sorted[:_MAX_PERSISTED_NODES]
            t = threading.Thread(
                target=self._write_json, args=(snapshot, _NODES_FILE), daemon=True
            )
            t.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not schedule node save (ignored): %s", exc)

    def _save_traceroutes(self, *, _is_scheduled: bool = False) -> None:
        """Persist the current traceroute results to disk (best-effort).

        Offloaded to a daemon thread so the meshtastic receive thread is never
        blocked, and serialised via _persist_lock. Saves are debounced so a
        burst of traceroute replies cannot spawn a save thread per capture;
        the most recent result within the debounce window is flushed shortly
        after the storm settles.

        The ``_is_scheduled`` parameter is an internal sentinel: when True the
        call comes from a previously-scheduled Timer — debouncing has already
        elapsed so we flush immediately without re-debouncing.
        """
        try:
            now = time.time()
            with self._nodes_lock:
                # If a save happened very recently, schedule a single trailing
                # flush after the debounce window so the latest state is still
                # persisted once the burst settles. A negative delta disables
                # debouncing entirely (save on every call).
                if (
                    not _is_scheduled
                    and _TRACEROUTE_SAVE_DEBOUNCE > 0
                    and now - self._last_traceroute_save < _TRACEROUTE_SAVE_DEBOUNCE
                ):
                    if not self._pending_traceroute_save:
                        self._pending_traceroute_save = True
                        threading.Timer(
                            _TRACEROUTE_SAVE_DEBOUNCE,
                            self._save_traceroutes,
                            kwargs={"_is_scheduled": True},
                        ).start()
                    return
                self._last_traceroute_save = now
                self._pending_traceroute_save = False
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
            from_id = _node_id_from_num(from_num)

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
            # Pop the most recent pending destination (LIFO) so overlapping
            # traceroute requests each attribute to their own target rather than
            # clobbering a single shared slot.
            target_id = from_id
            with self._lock:
                if self._pending_traceroute_dests:
                    target_id = self._pending_traceroute_dests.pop()

            logger.debug(
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
            from_id = _node_id_from_num(from_num)
            if not from_id:
                return

            # Integer microdegrees -> decimal degrees. 0 means "not set".
            lat = pos.latitude_i * 1e-7 if pos.latitude_i else None
            lng = pos.longitude_i * 1e-7 if pos.longitude_i else None
            alt = pos.altitude if pos.altitude else None
            snr = packet.get("rxSnr")
            rssi = packet.get("rxRssi")

            # Always record the fix in position history for trails and heatmaps.
            if lat is not None and lng is not None:
                self._record_position(from_id, lat, lng, alt, snr, rssi)
                # Persist asynchronously on a daemon thread.
                t = threading.Thread(target=self._save_position_history, daemon=True)
                t.start()

            # Only treat this as a reply to a position request we actually made.
            # POSITION_APP packets also arrive as periodic broadcasts from nodes
            # we didn't ask. If there's no matching pending request, ignore the packet.
            with self._lock:
                if from_id not in self._pending_position_dests:
                    return
                self._pending_position_dests.discard(from_id)

            logger.debug(
                "Captured requested position for %s: lat=%s lng=%s alt=%s",
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
                    if lat is not None or lng is not None:
                        node["last_position_fix"] = int(time.time())
                    node["last_heard"] = int(time.time())
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error capturing position (ignored): %s", exc)

    def _capture_neighborinfo(self, packet: Dict[str, Any]) -> None:
        """
        Capture a NEIGHBORINFO_APP packet and attach the neighbor list to
        the broadcasting node in our cache so the UI can display per-peer SNR.

        The meshtastic library does NOT store neighbor info anywhere we can
        read, so we must capture it from the raw protobuf payload ourselves.
        """
        try:
            from meshtastic.protobuf.mesh_pb2 import NeighborInfo
            decoded = packet.get("decoded", {}) or {}
            payload = decoded.get("payload")
            if not payload:
                return
            ni = NeighborInfo()
            ni.ParseFromString(payload)

            node_id_num = ni.node_id or packet.get("from")
            node_id = _node_id_from_num(node_id_num)
            if not node_id:
                return

            neighbors = []
            for nb in ni.neighbors:
                nid = _node_id_from_num(nb.node_id)
                if nid:
                    neighbors.append({
                        "id": nid,
                        "snr": round(nb.snr, 1) if hasattr(nb, 'snr') else None,
                    })

            logger.debug(
                "Neighbor info for %s: %d neighbors",
                node_id, len(neighbors),
            )

            with self._nodes_lock:
                node = next(
                    (n for n in self._nodes if n.get("id") == node_id), None
                )
                if node is not None:
                    node["neighbors"] = neighbors
                    node["neighbor_info_updated"] = int(time.time())
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error capturing neighbor info (ignored): %s", exc)

    def _get_messages_sync(self) -> List[Dict[str, Any]]:
        with self._msg_lock:
            return list(self._messages)

    def _is_interface_healthy(self) -> bool:
        """
        Health check that decides whether the session needs a reconnect.

        We treat the session as dead ONLY when the underlying socket is gone or
        reading the node DB raises — i.e. a genuinely broken connection. An
        *empty* node DB is NOT treated as dead: the meshtastic library
        populates ``interface.nodes`` asynchronously after connect, and on a
        busy/slow mesh this sync can take well over a minute.

        Lock strategy: we hold _lock only long enough to take a reference to
        the interface object. The actual DB probe happens *outside* the lock so
        a concurrent _get_nodes_sync call (which also holds _lock while reading
        nodes) cannot starve the health probe. If the interface reference
        becomes None between the check and the probe, the AttributeError is
        caught and treated as unhealthy.
        """
        with self._lock:
            iface = self._interface
        if iface is None:
            return False
        # Probe the node DB outside the lock. If the session is dead this will
        # raise (broken pipe, attribute error, etc.) and we return False.
        try:
            _ = iface.nodes
        except Exception as exc:
            logger.debug("Health probe failed (node DB unreadable): %s", exc)
            return False
        return True

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

    @staticmethod
    def _normalize_role(raw: Any) -> str:
        """
        Normalise a Meshtastic device role to a clean string.

        The library may expose the role as a protobuf enum object (whose str
        form is e.g. "Role.CLIENT"), an int (the enum value), or an already
        clean string. We strip the enum prefix and return the bare name (or an
        empty string when unknown) so the HA "Role" sensor shows "CLIENT" /
        "ROUTER" rather than "Role.CLIENT" / "2".
        """
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.split(".")[-1] if raw else ""
        # Enum-like object: prefer its .name; fall back to its string form.
        name = getattr(raw, "name", None)
        if name:
            return str(name).split(".")[-1]
        return str(raw).split(".")[-1]

    def _get_nodes_sync(self) -> List[Dict[str, Any]]:
        # Take a snapshot of the raw node dict under _lock, then release it
        # immediately. The heavy merge loop runs under _nodes_lock only,
        # keeping _lock free so the health probe and other readers aren't
        # blocked for the full duration of the merge.
        with self._lock:
            if not self._connected or self._interface is None:
                # Not connected (startup before first connect, or after a
                # disconnect). Return the last known node list so the UI/HA
                # keep showing nodes rather than going blank; these are the
                # persisted nodes restored at startup.
                with self._nodes_lock:
                    return list(self._nodes)
        # _lock is now released — proceed with the merge.
        nodes_raw = dict(self._interface.nodes or {})

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

                # Extract short name, falling back to truncated long name if not provided
                long_name = user.get("longName", "")
                short_name = user.get("shortName", "")
                if not short_name and long_name:
                    # Truncate long name to first 8 chars as a fallback short name
                    short_name = long_name[:8]

                entry = {
                    "id": node_id,
                    "long_name": long_name,
                    "short_name": short_name,
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
                    "uptime": device_metrics.get("uptimeSeconds"),
                    "temperature": environment.get("temperature"),
                    "relative_humidity": environment.get("relativeHumidity"),
                    "barometric_pressure": environment.get("barometricPressure"),
                    "gas_resistance": environment.get("gasResistance"),
                    "role": MeshtasticConnection._normalize_role(user.get("role")),
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
                    prev_fix = prev.get("last_position_fix")
                    cached[node_id].update(entry)
                    # Last-known-position retention: a node that loses GPS (or
                    # stops reporting) sends position=None. Instead of dropping
                    # the fix and making the marker vanish from the map, keep
                    # the most recent good coordinates so the node stays put
                    # until a newer fix (or a manual position request) arrives.
                    # Priority: freshly-captured POSITION_APP reply > raw library
                    # fix this cycle > previously retained last-known fix.
                    if entry["latitude"] is not None:
                        cached[node_id]["last_position_fix"] = int(time.time())
                    elif prev_lat is not None:
                        cached[node_id]["latitude"] = prev_lat
                        cached[node_id]["last_position_fix"] = prev_fix
                    if entry["longitude"] is not None:
                        cached[node_id]["last_position_fix"] = int(time.time())
                    elif prev_lng is not None:
                        cached[node_id]["longitude"] = prev_lng
                        cached[node_id]["last_position_fix"] = prev_fix
                    if entry["altitude"] is not None:
                        pass
                    elif prev_alt is not None:
                        cached[node_id]["altitude"] = prev_alt
                    result.append(cached[node_id])
                else:
                    entry["traceroute"] = None
                    if entry["latitude"] is not None or entry["longitude"] is not None:
                        entry["last_position_fix"] = int(time.time())
                    cached[node_id] = entry
                    result.append(entry)

            # Merge persisted traceroute results back onto their nodes so a
            # previously-discovered route is shown even before (or without)
            # a fresh traceroute request this session.
            for tid, rec in self._traceroutes.items():
                if tid in cached and rec:
                    cached[tid]["traceroute"] = rec

            # Re-inject nodes the radio no longer reports (its bounded node DB
            # evicts the oldest heard nodes once full). Any node we have
            # persisted but which is absent from this poll is restored from our
            # durable cache and flagged "stale" so the UI can show it faded and
            # we retain its last-known position. Freshly-present nodes are never
            # marked stale.
            # fresh_ids is the set of node IDs the radio actually reported this
            # poll (not the union with our cache), so an evicted-but-cached node
            # is correctly re-injected exactly once.
            fresh_ids = {_norm_id(k) for k in nodes_raw}
            for node in list(self._nodes):
                nid = node.get("id")
                if not nid or nid in fresh_ids:
                    continue
                restored = dict(node)
                restored["stale"] = True
                cached[nid] = restored
                result.append(restored)

            self._nodes = list(cached.values())

        # Inject extra fields from other caches into each node.
        with self._tags_lock:
            tags_snapshot = dict(self._tags)
        with self._pos_hist_lock:
            pos_hist_snapshot = {k: len(v) for k, v in self._pos_history.items()}
        for node in result:
            nid = node.get("id")
            if nid and nid in tags_snapshot:
                node["tags"] = tags_snapshot[nid]
            if nid and nid in pos_hist_snapshot:
                node["position_fix_count"] = pos_hist_snapshot[nid]
            # Signal quality trend: computed from the rolling SNR window.
            # Stale nodes have no recent SNR samples and will show "no_signal".
            if nid:
                node["signal_quality"] = self._signal_quality(nid)
                node["snr_avg"] = self._snr_avg(nid)

        # Persist the merged list so evicted nodes survive restarts and the
        # next reconnect. Best-effort and debounced/off-thread.
        self._save_nodes()

        return result

    def _read_channels_from_interface(self) -> List[Dict[str, Any]]:
        """Read the channel list straight from the connected node.

        Returns every channel slot the radio knows about. In Meshtastic the
        library populates ``interface.localNode.channels`` — a list of
        ``Channel`` protobufs (one per slot, indexed 0..N) — during the initial
        config handshake via ``localNode.setChannels(...)``. Each ``Channel``
        carries ``index``, ``role`` (PRIMARY / SECONDARY / DISABLED) and a nested
        ``settings`` with the human ``name``. We read from there.

        We deliberately DON'T rely on ``interface.localConfig.channel_settings``:
        the radio does not always push the channel settings into the local
        config, so that field is frequently empty and would make the UI show
        only the hardcoded "Primary" tab. We still accept it as a fallback for
        older library versions that populate it.

        Disabled slots are skipped (role == DISABLED) except slot 0, which is
        always present as the PRIMARY channel even when unnamed.
        """
        iface = self._interface
        if iface is None:
            return []

        # Preferred: the Channel list on the local node.
        local_node = getattr(iface, "localNode", None)
        channels = getattr(local_node, "channels", None) if local_node else None

        # Fallback for library versions that only populate localConfig.
        if not channels:
            local_config = getattr(iface, "localConfig", None)
            channels = getattr(local_config, "channel_settings", None) if local_config else None

        if not channels:
            return []

        result: List[Dict[str, Any]] = []
        for ch in channels:
            idx = getattr(ch, "index", None)
            if idx is None:
                # localConfig.channel_settings is keyed by position; derive index.
                idx = len(result)
            role_raw = getattr(ch, "role", None)
            # Role may be an enum, an enum name string, or an int.
            if hasattr(role_raw, "name"):
                role = role_raw.name
            elif isinstance(role_raw, int):
                role = _channel_role_name(role_raw)
            else:
                role = str(role_raw or "")
            role_upper = (role or "").upper()

            settings = getattr(ch, "settings", None)
            name = getattr(settings, "name", "") if settings else ""
            name = name or ""
            # The primary channel (index 0) is usually unnamed in the radio;
            # give it the conventional "Primary" label the UI expects.
            if idx == 0 and not name:
                name = "Primary"

            # Skip disabled slots, but always keep the primary (index 0).
            if role_upper == "DISABLED" and idx != 0:
                continue

            result.append({
                "index": idx,
                "name": name or f"Channel {idx}",
                "role": role_upper,
            })

        # Guarantee slot 0 exists (PRIMARY) even if the radio omitted it.
        if not any(c["index"] == 0 for c in result):
            result.insert(0, {"index": 0, "name": "Primary", "role": "PRIMARY"})

        logger.debug("Channel fetch: %d channels (source=%s)", len(result),
                     "localNode.channels" if local_node and getattr(local_node, "channels", None) else "localConfig.channel_settings")
        return result

    def _get_channels_sync(self) -> List[Dict[str, Any]]:
        # Return cached channels if available.
        if self._channels_lock.acquire(blocking=False):
            try:
                if self._channels:
                    return self._channels
            finally:
                self._channels_lock.release()

        # Fallback: fetch directly from interface if cache is empty.
        with self._lock:
            if not self._connected or self._interface is None:
                return []

            result = self._read_channels_from_interface()

            # Cache the result for future calls.
            with self._channels_lock:
                self._channels = result

            return result

    def _refresh_channels_sync(self) -> List[Dict[str, Any]]:
        """Read the channel list straight from the node and replace the cache.

        Unlike ``_get_channels_sync`` (which returns the cache when non-empty),
        this always re-reads from the interface so callers can force a refresh.
        """
        with self._lock:
            if not self._connected or self._interface is None:
                return list(self._channels)
            result = self._read_channels_from_interface()

        with self._channels_lock:
            self._channels = result
        # Persist so a restart restores the latest channel list.
        self._schedule_save(self._channels, _CHANNELS_FILE)
        return result

    def _send_message_sync(
        self, text: str, destination: Optional[str], channel: int
    ) -> bool:
        # Take a snapshot of the interface under the lock, then release it
        # BEFORE calling sendText. Holding _lock during sendText (which does
        # blocking radio I/O) would stall every other thread (get_nodes, health
        # probe, etc.) — mirroring the pattern used by _connect_sync,
        # _request_traceroute_sync, and _request_position_sync.
        with self._lock:
            if not self._connected or self._interface is None:
                logger.error("Cannot send message — not connected")
                return False
            iface = self._interface
            self_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
            self_id = ("!" + format(self_num, "08x")) if self_num is not None else None

        to_num = None
        if destination and destination != meshtastic.BROADCAST_ADDR:
            try:
                to_num = int(destination.replace("!", ""), 16)
            except (ValueError, AttributeError):
                to_num = None
        to_id = ("!" + format(to_num, "08x")) if to_num is not None else None

        is_dm = to_id is not None
        if is_dm:
            other = to_id if to_id != self_id else None
            conversation = f"dm:{other or 'unknown'}"
        else:
            conversation = f"ch:{channel}"

        try:
            # sendText handles both broadcast (destinationId=None or BROADCAST)
            # and unicast DMs. The meshtastic library manages PKI encryption
            # automatically for DMs when a shared key is configured on the channel.
            # sendText returns the outgoing packet object whose `.id` is the
            # packet ID the firmware uses in the ROUTING_APP ACK reply.
            sent_packet = iface.sendText(
                text,
                destinationId=to_num if to_num is not None else meshtastic.BROADCAST_ADDR,
                channelIndex=channel,
            )

            # Extract the packet ID so we can match the ROUTING_APP ACK.
            packet_id: Optional[int] = None
            if sent_packet is not None:
                try:
                    packet_id = int(
                        sent_packet.id
                        if hasattr(sent_packet, "id")
                        else sent_packet.get("id", None)
                    )
                except (TypeError, ValueError, AttributeError):
                    packet_id = None

            # Broadcast messages never receive a ROUTING_APP reply from
            # the firmware, so we mark them delivered immediately.
            # DMs get "sending" and flip on ROUTING_APP receipt (or timeout).
            initial_ack = "delivered" if not is_dm else "sending"

            entry = {
                "from_id": self_id,
                "to_id": to_id,
                "from_name": "You",
                "text": text,
                "channel": channel,
                "conversation": conversation,
                "is_dm": is_dm,
                "outgoing": True,
                "rx_snr": None,
                "rx_rssi": None,
                "timestamp": int(time.time()),
                "ack_status": initial_ack,
                "ack_at": int(time.time()) if initial_ack == "delivered" else None,
                "packet_id": packet_id,
            }
            with self._msg_lock:
                entry["id"] = f"{self_id or 'unknown'}-{channel}-{self._msg_counter}"
                self._msg_counter += 1
                self._messages.append(entry)
                # Register pending ACK only for DMs with a valid packet ID.
                if is_dm and packet_id is not None:
                    self._pending_acks[packet_id] = entry["id"]
                    self._pending_ack_times[packet_id] = time.time()
            self._schedule_save(self._messages, _MESSAGES_FILE)
            return True
        except Exception as exc:
            logger.error(
                "Failed to send message (destination=%s): %s", destination, exc
            )
            return False

    def _request_traceroute_sync(self, destination: str) -> bool:
        # Take a snapshot of the interface under the lock, then release it
        # BEFORE calling sendTraceRoute. sendTraceRoute blocks internally
        # waiting for the firmware RouteDiscovery ack (can take 30 s+);
        # holding _lock for that entire duration would freeze get_nodes,
        # get_status, and the health probe for every poll cycle.
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            iface = self._interface

        dest_num = None
        if destination:
            try:
                dest_num = int(destination.replace("!", ""), 16)
            except (ValueError, AttributeError):
                dest_num = destination

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
            # merge it into our node cache. The destination is already queued in
            # request_traceroute; the cache refresh is done by the background
            # dispatch task so this call stays non-blocking.
            iface.sendTraceRoute(dest_num if dest_num is not None else destination, 10)
            return True
        except Exception as exc:
            logger.error(
                "Traceroute request failed (destination=%s): %s", destination, exc
            )
            return False

    def _request_position_sync(self, destination: str) -> bool:
        # Release the lock before the blocking sendPosition call for the same
        # reason as _request_traceroute_sync — the firmware round-trip can take
        # several seconds and must not freeze other polling threads.
        with self._lock:
            if not self._connected or self._interface is None:
                return False
            iface = self._interface

        dest_num = None
        if destination:
            try:
                dest_num = int(destination.replace("!", ""), 16)
            except (ValueError, AttributeError):
                dest_num = destination

        try:
            # Request a position with wantResponse=True. The node replies with
            # a POSITION_APP packet which the library funnels through
            # onResponsePosition -> _onPositionReceive, updating its node DB.
            # We then copy that fresh fix into our own nodes dict below.
            iface.sendPosition(destinationId=dest_num if dest_num is not None else destination, wantResponse=True)
            with self._lock:
                self._pending_position_dests.add(destination)
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

        We selectively merge only our normalized flat fields — never do a
        blind ``dict.update`` with the raw protobuf-derived node because it
        carries untransformed nested keys (``user``, ``position``, etc.) that
        would corrupt the flat cache.
        """
        if self._interface is None or not node_id:
            return
        lib_node = self._interface.nodes.get(node_id)
        if lib_node is None:
            return

        user = lib_node.get("user", {})
        position = lib_node.get("position", {})
        device_metrics = lib_node.get("deviceMetrics", {})
        environment = lib_node.get("environmentMetrics", {}) or {}

        long_name = user.get("longName", "")
        short_name = user.get("shortName", "")
        if not short_name and long_name:
            short_name = long_name[:8]

        patch = {
            "long_name": long_name,
            "short_name": short_name,
            "hw_model": user.get("hwModel", ""),
            "last_heard": lib_node.get("lastHeard"),
            "snr": lib_node.get("snr"),
            "rssi": lib_node.get("rssi"),
            "hops_away": lib_node.get("hopsAway"),
            "is_licensed": user.get("isLicensed", False),
            "latitude": position.get("latitude"),
            "longitude": position.get("longitude"),
            "altitude": position.get("altitude"),
            "battery_level": device_metrics.get("batteryLevel"),
            "voltage": device_metrics.get("voltage"),
            "channel_utilization": device_metrics.get("channelUtilization"),
            "air_util_tx": device_metrics.get("airUtilTx"),
            "uptime": device_metrics.get("uptimeSeconds"),
            "temperature": environment.get("temperature"),
            "relative_humidity": environment.get("relativeHumidity"),
            "barometric_pressure": environment.get("barometricPressure"),
            "gas_resistance": environment.get("gasResistance"),
            "role": MeshtasticConnection._normalize_role(user.get("role")),
        }

        with self._nodes_lock:
            existing = next(
                (n for n in self._nodes if n.get("id") == node_id), None
            )
            if existing is not None:
                # Preserve our own captured traceroute/position data, which the
                # library's raw node dict does not carry (it only sets an
                # internal ack flag).
                captured_traceroute = existing.get("traceroute")
                captured_lat = existing.get("latitude")
                captured_lng = existing.get("longitude")
                captured_alt = existing.get("altitude")
                existing.update(patch)
                if captured_traceroute is not None:
                    existing["traceroute"] = captured_traceroute
                if captured_lat is not None:
                    existing["latitude"] = captured_lat
                if captured_lng is not None:
                    existing["longitude"] = captured_lng
                if captured_alt is not None:
                    existing["altitude"] = captured_alt
            else:
                entry = dict(patch)
                entry["id"] = node_id
                if patch.get("latitude") is not None or patch.get("longitude") is not None:
                    entry["last_position_fix"] = int(time.time())
                self._nodes.append(entry)
