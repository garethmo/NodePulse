"""
NodePulse Addon — Remote Node Administration.

Meshtastic's AdminModule lets a node with an ADMIN channel configure OTHER
nodes on the mesh over the radio: read/write their Config + ModuleConfig,
set the owner, reboot/shutdown, factory-reset, reset the NodeDB, set a fixed
position, set the clock, and evict nodes from a remote node's node DB.

This module wraps the meshtastic library's ``Node`` object for a remote node.
Two important differences from the library's default usage:

  * We NEVER call ``interface.getNode()`` — on a channel timeout that method
    calls ``our_exit()`` which is a hard ``sys.exit()`` and would kill the
    addon. Instead we construct ``meshtastic.node.Node(iface, node_id)``
    directly and bound every admin round-trip with our own timeout.
  * All blocking radio I/O must run inside a thread (see
    ``MeshtasticConnection``) — nothing in this module touches the event loop.

Admin operations require the connected gateway to have admin capability: an
ADMIN channel, Security admin keys, or admin channel enabled.
(a channel whose settings name is "admin") configured, and the target node
to share that channel's PSK. The library resolves the admin channel index
via ``localNode._getAdminChannelIndex()``.
"""
import base64
import contextlib
import logging
import time

from . import device_config

logger = logging.getLogger(__name__)

# Bounded wait for a single remote admin round-trip (send + ACK/NAK). The
# mesh can be slow, so we are generous but bounded — the app must never hang.
REMOTE_ADMIN_TIMEOUT_S = 15.0

# Full config read: we now pipeline all section requests (send all without
# waiting for individual acks) then poll once for all responses. The firmware
# replies asynchronously so the effective wait is 1 × round-trip time, not
# 23 × round-trip.  60 s gives plenty of headroom on slow/distant nodes.
REMOTE_CONFIG_TIMEOUT_S = 60.0

# Which config sections count as "needs a reboot after applying".
_REBOOT_SECTIONS = frozenset({"device", "lora", "position", "network", "mesh_beacon"})


def admin_channel_index(interface) -> int | None:
    """
    Return the index of the connected node's ADMIN channel, or None if the
    gateway has no channel named 'admin'.

    NOTE: an admin channel is only ONE way to administer remote nodes. Modern
    firmware (2.x) administers via the Security config's admin keys instead —
    the gateway signs admin messages with its private key and the target
    accepts them when the gateway's key is in the target's ``admin_key`` list,
    all over the primary channel. Use ``remote_admin_capability`` to detect the
    full picture.
    """
    local_node = getattr(interface, "localNode", None)
    if local_node is None:
        return None
    channels = getattr(local_node, "channels", None) or []
    for ch in channels:
        settings = getattr(ch, "settings", None)
        name = getattr(settings, "name", "") if settings else ""
        if str(name).lower() == "admin":
            return getattr(ch, "index", 0)
    return None


def remote_admin_capability(interface) -> dict:
    """
    Describe how (or whether) this gateway can administer remote nodes.

    A channel merely NAMED "admin" does NOT grant capability — firmware only
    honours it when ``admin_channel_enabled`` (the legacy toggle, now hidden
    from the mobile apps and only reachable via the Web Client) is set.
    Remote admin is possible when ANY of these hold:

      * the Security config has ``admin_channel_enabled`` set (legacy admin
        channel is honoured), or
      * the gateway has a PKC keypair (``public_key`` + ``private_key``) and/or
        ``admin_key`` entries — firmware 2.5+ administers over the primary
        channel, PKC-authenticated against the TARGET's admin-key list.

    Returns a dict with ``available`` plus the individual signals so the UI can
    give targeted guidance. Key material is exposed base64-encoded so the Web
    UI can display/copy the gateway's public key for configuring targets.
    """
    local_node = getattr(interface, "localNode", None)
    if local_node is None:
        return {
            "available": False,
            "admin_channel_index": None,
            "has_admin_channel": False,
            "admin_key_count": 0,
            "admin_channel_enabled": False,
            "has_keypair": False,
            "public_key": None,
            "admin_keys": [],
        }

    has_admin_channel = admin_channel_index(interface) is not None

    local_config = getattr(local_node, "localConfig", None)
    security = getattr(local_config, "security", None) if local_config is not None else None
    admin_keys = list(getattr(security, "admin_key", []) or []) if security is not None else []
    admin_channel_enabled = getattr(security, "admin_channel_enabled", False) is True
    public_key = getattr(security, "public_key", None) if security is not None else None
    private_key = getattr(security, "private_key", None) if security is not None else None
    has_keypair = bool(public_key) and bool(private_key)

    def b64(b):
        return base64.b64encode(b).decode("ascii") if isinstance(b, bytes) and b else None

    return {
        "available": bool(admin_channel_enabled or admin_keys or has_keypair),
        "admin_channel_index": admin_channel_index(interface),
        "has_admin_channel": has_admin_channel,
        "admin_key_count": len(admin_keys),
        "admin_channel_enabled": admin_channel_enabled,
        "has_keypair": has_keypair,
        "public_key": b64(public_key),
        "admin_keys": [b64(k) for k in admin_keys],
    }


def remote_admin_available(interface) -> bool:
    """True when the gateway can administer remote nodes (admin channel OR admin keys)."""
    return remote_admin_capability(interface)["available"]


def _admin_send_channel_index(interface) -> int:
    """
    Which channel index admin messages should use, mirroring firmware:

    * ``admin_channel_enabled`` (the legacy "admin channel") → send on the
      reserved channel named ``admin`` (or the primary channel when none is
      named).
    * Otherwise (the 2.5+ default) → the PRIMARY channel (index 0), where
      admin messages are PKC-authenticated against the target's Security
      admin keys.

    The meshtastic library's own ``_sendAdmin`` always prefers a channel named
    'admin' when one exists — even when firmware isn't honouring it — which
    makes every round-trip time out because the target cannot decrypt it.
    """
    capability = remote_admin_capability(interface)
    if capability["admin_channel_enabled"]:
        return capability["admin_channel_index"] if capability["admin_channel_index"] is not None else 0
    return 0


def _bind_admin_channel(remote_node, interface, channel_index: int) -> None:
    """
    Force all of ``remote_node``'s admin traffic onto ``channel_index``.

    ``_sendAdmin`` picks the channel with ``iface.localNode._getAdminChannelIndex()``,
    which returns the reserved 'admin' channel whenever one is named on the
    gateway — even when the firmware is not honouring it. We wrap
    ``_sendAdmin`` so the lookup is overridden just for this node's sends and
    restored afterwards. No-op when ``_sendAdmin``/the lookup are unavailable.
    """
    original_send = getattr(remote_node, "_sendAdmin", None)
    local_node = getattr(interface, "localNode", None)
    original_lookup = getattr(local_node, "_getAdminChannelIndex", None) if local_node is not None else None
    if original_send is None or original_lookup is None:
        return

    def _send(*args, **kwargs):
        local_node._getAdminChannelIndex = lambda: channel_index
        try:
            logger.debug("Admin send to %s on channel index %s", remote_node.nodeNum, channel_index)
            return original_send(*args, **kwargs)
        finally:
            local_node._getAdminChannelIndex = original_lookup

    remote_node._sendAdmin = _send


def _capture_admin_errors(remote_node) -> None:
    """
    Wrap the node's response handler to record admin NAK reasons and count
    successful config responses, so callers can distinguish "the node refused
    us" (a routing NAK such as ADMIN_PUBLIC_KEY_UNAUTHORIZED) from "the node
    never replied" (a timeout / unreachable node).

    Records ``_lastAdminError`` (routing errorReason), ``_adminResponses``
    (count of real config responses) and ``_adminNakCount`` on the node.
    """
    original = getattr(remote_node, "onResponseRequestSettings", None)
    if not callable(original):
        return
    remote_node._lastAdminError = None
    remote_node._adminResponses = 0
    remote_node._adminNakCount = 0

    def wrapped(p: dict):
        decoded = p.get("decoded") or {}
        routing = decoded.get("routing")
        if routing:
            reason = routing.get("errorReason")
            if reason and reason != "NONE":
                remote_node._adminNakCount += 1
                remote_node._lastAdminError = reason
                logger.warning("Admin request to %s NAK'd: %s", remote_node.nodeNum, reason)
        elif "admin" in decoded:
            remote_node._adminResponses += 1
        return original(p)

    remote_node.onResponseRequestSettings = wrapped


def _make_remote_node(interface, node_id: str, timeout: float = REMOTE_ADMIN_TIMEOUT_S):
    """
    Construct a remote ``Node`` WITHOUT ``interface.getNode()``.

    ``getNode`` calls ``our_exit()`` (a hard ``sys.exit``) if it cannot fetch
    the remote node's channels, which would terminate the whole addon. We
    build the Node directly so failures surface as exceptions we can handle.

    Fast-fails with a clear ``ConnectionError`` when the gateway has no admin
    capability at all (no 'admin' channel, no Security admin keys, and no
    admin channel enabled) — otherwise every admin round-trip would block
    until its timeout.
    """
    import meshtastic.node as meshtastic_node

    capability = remote_admin_capability(interface)
    if not capability["available"]:
        raise ConnectionError(
            "This gateway has no admin capability — remote administration requires "
            "either Security → Admin Keys configured on the radio, or a channel "
            "named 'admin' (or admin channel enabled)"
        )

    node = meshtastic_node.Node(interface, node_id, timeout=int(timeout))
    # The library would send admin traffic to a channel named 'admin' even when
    # the firmware isn't honouring it; force the correct channel (primary when
    # using Security admin keys, the admin channel when legacy admin is enabled).
    _bind_admin_channel(node, interface, _admin_send_channel_index(interface))
    # Firmware 2.3+ requires a session key before admin ops are honoured. This
    # handshake blocks in waitForAckNak() up to the interface timeout (default
    # 20 s), so we temporarily shrink that timeout to keep the whole operation
    # within the outer bounded budget. Restored in finally.
    _session_key_handshake(node, interface)
    return node


def _session_key_handshake(remote_node, interface) -> None:
    """
    Best-effort admin session-key handshake.

    Firmware 2.5+ requires a session passkey before honouring admin packets.
    The exchange is a single LoRa round-trip; on a distant multi-hop node
    this can take 10-15 s, so we allow 25 s (generous but bounded).
    Failure is non-fatal — we log and continue; PKI-only nodes may still
    respond even without an explicit session key.
    """
    # 25 s gives distant/slow nodes a fair chance while still being bounded.
    _SESSION_KEY_TIMEOUT_S = 25
    timeout = getattr(interface, "_timeout", None)
    saved = None
    if timeout is not None:
        saved = getattr(timeout, "expireTimeout", None)
        try:
            timeout.expireTimeout = _SESSION_KEY_TIMEOUT_S
        except Exception:  # pragma: no cover - defensive  # noqa: BLE001
            saved = None
    try:
        remote_node.ensureSessionKey()
        logger.debug("Session key handshake completed for %s", remote_node.nodeNum)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "ensureSessionKey(%s) failed (continuing without session key): %s",
            remote_node.nodeNum, exc,
        )
    finally:
        if timeout is not None and saved is not None:
            with contextlib.suppress(Exception):
                timeout.expireTimeout = saved


def _run_admin(remote_node, method_name: str, *args, **kwargs):
    """
    Call one of the remote Node's admin methods.

    The meshtastic library blocks in ``sendData`` for ACK/NAK; the caller
    (MeshtasticConnection) wraps this in ``asyncio.wait_for`` for a bounded
    timeout so a dead/ignoring node can never hang the app.
    """
    method = getattr(remote_node, method_name, None)
    if method is None:
        raise ValueError(f"Admin operation '{method_name}' is not available on this library version")
    return method(*args, **kwargs)


# ---------------------------------------------------------------------------
# Read / write configuration
# ---------------------------------------------------------------------------

def request_remote_config(remote_node) -> int:
    """
    Ask the remote node to re-send every Config + ModuleConfig section.

    Smart Serial Strategy:
    ----------------------
    We cannot burst packets (clogs the mesh, causes drops) and we cannot rely
    on the library's `waitForAckNak` (too rigid, false timeouts on multi-hop).
    
    Instead, we fire a request and poll our response counter. The instant the
    response arrives, we proceed to the next request. If no response arrives
    within 8 seconds (a generously bounded LoRa round-trip), we assume the
    packet dropped and move to the next.
    
    This adapts dynamically to mesh speed (fast nodes finish in ~10s, slow
    or lossy nodes gracefully degrade but still finish within the 150s cap)
    while keeping exactly 1 request in flight to avoid congestion.
    """
    from meshtastic.protobuf import admin_pb2, config_pb2, module_config_pb2

    send_admin = getattr(remote_node, "_sendAdmin", None)
    if not callable(send_admin):
        # Fallback to slow serial
        _request_remote_config_serial(remote_node)
        return 0

    original_handler = getattr(remote_node, "onResponseRequestSettings", None)
    remote_node._admin_response_count = 0

    def _counting_handler(p):
        decoded = (p.get("decoded") or {}) if isinstance(p, dict) else {}
        admin_msg = decoded.get("admin", {})
        if "getConfigResponse" in admin_msg or "getModuleConfigResponse" in admin_msg:
            remote_node._admin_response_count += 1
            logger.debug(
                "Remote config response #%d received for %s",
                remote_node._admin_response_count, remote_node.nodeNum,
            )
        if callable(original_handler):
            try:
                original_handler(p)
            except Exception as exc:  # noqa: BLE001
                logger.debug("onResponseRequestSettings error (ignored): %s", exc)

    remote_node.onResponseRequestSettings = _counting_handler

    sections_sent = 0
    _PER_SECTION_TIMEOUT = 8.0

    for descriptor in (config_pb2.Config.DESCRIPTOR, module_config_pb2.ModuleConfig.DESCRIPTOR):
        for field in descriptor.fields:
            p = admin_pb2.AdminMessage()
            try:
                msg_index = field.index
                if field.containing_type.name == "LocalConfig":
                    p.get_config_request = msg_index
                else:
                    p.get_module_config_request = msg_index
                
                count_before = remote_node._admin_response_count
                
                # Send without blocking for ACK
                send_admin(p, wantResponse=True, onResponse=_counting_handler)
                sections_sent += 1
                
                # Smart wait: proceed instantly when response arrives, or timeout
                deadline = time.time() + _PER_SECTION_TIMEOUT
                while time.time() < deadline:
                    if remote_node._admin_response_count > count_before:
                        # Response received, small breather before next request
                        time.sleep(0.1)
                        break
                    time.sleep(0.2)
                else:
                    logger.debug(
                        "Smart-serial wait timed out (8s) for %s on %s",
                        field.name, remote_node.nodeNum,
                    )
                    
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Smart-serial requestConfig(%s) send failed (skipped): %s",
                    field.name, exc,
                )

    logger.debug("Completed %d smart-serial remote config requests for %s", sections_sent, remote_node.nodeNum)
    return sections_sent


def _request_remote_config_serial(remote_node) -> None:
    """Fallback: request each config section serially (old library without _sendAdmin)."""
    from meshtastic.protobuf import config_pb2, module_config_pb2
    request = getattr(remote_node, "requestConfig", None)
    if not callable(request):
        raise ValueError("requestConfig is not available on this library version")

    for descriptor in (config_pb2.Config.DESCRIPTOR, module_config_pb2.ModuleConfig.DESCRIPTOR):
        for field in descriptor.fields:
            try:
                request(field)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Serial requestConfig(%s) failed: %s", field.name, exc)


def request_remote_config_section(remote_node, section_name: str) -> bool:
    """
    Request a single config section from the remote node.
    Returns True if the section was successfully requested and received.
    """
    if section_name == "owner":
        # Owner info is read from the local gateway's memory/node DB, so there's
        # no over-the-air request needed.
        return True

    from meshtastic.protobuf import admin_pb2, config_pb2, module_config_pb2

    send_admin = getattr(remote_node, "_sendAdmin", None)
    
    # Map section string to descriptor
    field = config_pb2.Config.DESCRIPTOR.fields_by_name.get(section_name)
    is_local = True
    if not field:
        field = module_config_pb2.ModuleConfig.DESCRIPTOR.fields_by_name.get(section_name)
        is_local = False
    
    if not field:
        logger.error("Unknown section name %r", section_name)
        return False
        
    if not callable(send_admin):
        # Fallback to slow serial
        request = getattr(remote_node, "requestConfig", None)
        if callable(request):
            try:
                request(field)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Serial requestConfig(%s) failed: %s", section_name, exc)
        return False

    original_handler = getattr(remote_node, "onResponseRequestSettings", None)
    remote_node._admin_response_count = 0

    def _counting_handler(p):
        decoded = (p.get("decoded") or {}) if isinstance(p, dict) else {}
        admin_msg = decoded.get("admin", {})
        if "getConfigResponse" in admin_msg or "getModuleConfigResponse" in admin_msg:
            remote_node._admin_response_count += 1
            logger.debug(
                "Remote config single-section response received for %s on %s",
                section_name, remote_node.nodeNum,
            )
        if callable(original_handler):
            with contextlib.suppress(Exception):
                original_handler(p)

    remote_node.onResponseRequestSettings = _counting_handler

    p = admin_pb2.AdminMessage()
    # Config sections live under Config (not LocalConfig); module sections
    # under ModuleConfig. Use the containing descriptor name to route correctly.
    if field.containing_type.full_name.endswith("ModuleConfig"):
        p.get_module_config_request = field.index
    else:
        p.get_config_request = field.index
    
    try:
        count_before = remote_node._admin_response_count
        send_admin(p, wantResponse=True, onResponse=_counting_handler)
        
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if remote_node._admin_response_count > count_before:
                return True
            time.sleep(0.1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Single-section requestConfig(%s) send failed: %s", section_name, exc)

    logger.warning("Remote config single-section wait timed out for %s on %s", section_name, remote_node.nodeNum)
    return False

def wait_for_remote_config(
    remote_node,
    sections_sent: int = 0,
    timeout_s: float = REMOTE_CONFIG_TIMEOUT_S,
) -> bool:
    """
    With smart-serial fetching, the responses arrive DURING `request_remote_config`.
    This function simply verifies that we got at least one response (confirming
    the node is reachable and authorized). If some stragglers are still in the air,
    we wait up to a few extra seconds.
    """
    _MIN_RESPONSES = 1
    
    count = getattr(remote_node, "_admin_response_count", 0)
    if count >= _MIN_RESPONSES:
        return True

    # If 0 responses arrived during the smart-serial loop, give it a tiny grace period
    # just in case the last few packets are still traversing the mesh.
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if getattr(remote_node, "_admin_response_count", 0) >= _MIN_RESPONSES:
            return True
        time.sleep(0.5)

    logger.warning("Remote config wait timed out with 0 valid responses for %s", remote_node.nodeNum)
    return False


def read_remote_config(remote_node, interface) -> dict:
    """
    Read a remote node's full configuration as a JSON-safe dict.

    Mirrors ``device_config.read_device_config`` but serializes the remote
    node's admin-fetched config. The ``owner`` section is best-effort.
    """
    local_config = getattr(remote_node, "localConfig", None)
    module_config = getattr(remote_node, "moduleConfig", None)
    if local_config is None or module_config is None:
        raise ConnectionError("Remote node config is not loaded")

    config_dict = device_config.serialize_config_sections(local_config, module_config)
    config_dict["_schema"] = device_config.read_device_config_schema()

    # Best-effort owner info from the interface's node DB (user packet).
    try:
        user_info = _remote_user_info(interface, remote_node)
        config_dict["owner"] = {
            "long_name": user_info.get("longName", ""),
            "short_name": user_info.get("shortName", ""),
            "hw_model": user_info.get("hwModel", ""),
            "firmware_version": _remote_firmware(interface, remote_node),
            "region": "",
            "role": user_info.get("role", ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read remote owner info: %s", exc)
        config_dict["owner"] = {
            "long_name": "", "short_name": "", "hw_model": "",
            "firmware_version": "", "region": "", "role": "",
        }
    return config_dict



def _remote_node_num(remote_node) -> int | None:
    """Resolve the remote node's integer node number from its Node object."""
    num = getattr(remote_node, "nodeNum", None)
    if num is None:
        return None
    if isinstance(num, int):
        return num
    # Hex string "!ab12cd34"
    try:
        return int(str(num).lstrip("!"), 16)
    except ValueError:
        return None


def _remote_user_info(interface, remote_node) -> dict:
    """Look up the remote node's cached user info dict."""
    node_num = _remote_node_num(remote_node)
    lookup = getattr(device_config, "_lookup_node", None)
    if callable(lookup) and node_num is not None:
        info = lookup(interface, node_num)
        return info.get("user", {}) if info else {}
    return {}


def _remote_firmware(interface, remote_node) -> str:
    """Best-effort firmware version for a remote node."""
    metadata = getattr(interface, "metadata", None)
    if metadata is not None:
        fw = getattr(metadata, "firmware_version", "") or ""
        if fw:
            return fw
    get_meta = getattr(remote_node, "getMetadata", None)
    if callable(get_meta):
        try:
            get_meta()
            meta = getattr(interface, "metadata", None)
            if meta is not None:
                return getattr(meta, "firmware_version", "") or ""
        except Exception:  # pragma: no cover - defensive  # noqa: BLE001
            pass
    return ""


def apply_remote_config(remote_node, interface, section: str, patch: dict) -> dict:
    """
    Validate and apply a config patch to a remote node.

    Reuses ``device_config.validate_and_apply_patch`` — it works against any
    node-like object exposing ``localConfig`` / ``moduleConfig`` /
    ``writeConfig`` / ``setOwner``, and remote ``Node`` objects satisfy that.

    Returns ``{ applied: bool, section: str, reboot_required: bool }``.
    """
    success, reboot_required = device_config.validate_and_apply_patch(
        section, patch, remote_node, interface
    )
    return {"applied": success, "section": section, "reboot_required": reboot_required}


# ---------------------------------------------------------------------------
# Actions (reboot / shutdown / factory reset / nodedb / position / time / evict)
# ---------------------------------------------------------------------------

def remote_set_owner(remote_node, long_name: str, short_name: str) -> None:
    """Set the remote node's long/short name."""
    _run_admin(remote_node, "setOwner", long_name=long_name, short_name=short_name)


def remote_reboot(remote_node, seconds: int = 10) -> None:
    """Tell the remote node to reboot in ``seconds``."""
    _run_admin(remote_node, "reboot", seconds)


def remote_shutdown(remote_node, seconds: int = 10) -> None:
    """Tell the remote node to shut down in ``seconds``."""
    _run_admin(remote_node, "shutdown", seconds)


def remote_factory_reset(remote_node, full: bool = False) -> None:
    """Factory-reset the remote node (config-only unless ``full``)."""
    _run_admin(remote_node, "factoryReset", full)


def remote_nodedb_reset(remote_node) -> None:
    """Tell the remote node to clear its NodeDB."""
    _run_admin(remote_node, "resetNodeDb")


def remote_set_fixed_position(remote_node, lat: float, lng: float, alt: int = 0) -> None:
    """Set (and enable) the remote node's fixed position."""
    _run_admin(remote_node, "setFixedPosition", lat, lng, alt)


def remote_remove_fixed_position(remote_node) -> None:
    """Disable and clear the remote node's fixed position."""
    _run_admin(remote_node, "removeFixedPosition")


def remote_set_time(remote_node, epoch_secs: int = 0) -> None:
    """Set the remote node's clock (0 = the gateway's current time)."""
    _run_admin(remote_node, "setTime", epoch_secs)


def remote_remove_node(remote_node, target_node_id: str) -> None:
    """Evict a node from the remote node's NodeDB."""
    _run_admin(remote_node, "removeNode", target_node_id)


def remote_actions() -> dict[str, dict]:
    """
    Registry of supported admin actions, used by the API/UI to render buttons.
    """
    return {
        "reboot": {"label": "Reboot", "danger": False, "confirm": False},
        "shutdown": {"label": "Shut down", "danger": False, "confirm": True},
        "factory_reset": {"label": "Factory reset (config)", "danger": True, "confirm": True},
        "factory_reset_device": {"label": "Factory reset (full device)", "danger": True, "confirm": True},
        "nodedb_reset": {"label": "Reset NodeDB", "danger": True, "confirm": True},
        "set_fixed_position": {"label": "Set fixed position", "danger": False, "confirm": False},
        "clear_fixed_position": {"label": "Clear fixed position", "danger": False, "confirm": False},
        "set_time": {"label": "Sync clock", "danger": False, "confirm": False},
        "remove_node": {"label": "Remove node from NodeDB", "danger": True, "confirm": True},
    }


def execute_admin_action(
    remote_node, action: str, params: dict | None = None
) -> dict:
    """
    Run a named admin action against a remote node.

    ``params`` carries action-specific inputs (reboot/shutdown seconds, fixed
    position lat/lng/alt, remove_node target, etc.).

    NOTE: timeout bounding happens in the async layer (``asyncio.wait_for``
    around ``asyncio.to_thread``); this function runs inside that thread.
    """
    params = params or {}
    action_map = {
        "reboot": lambda: remote_reboot(remote_node, int(params.get("seconds", 10))),
        "shutdown": lambda: remote_shutdown(remote_node, int(params.get("seconds", 10))),
        "factory_reset": lambda: remote_factory_reset(remote_node, full=False),
        "factory_reset_device": lambda: remote_factory_reset(remote_node, full=True),
        "nodedb_reset": lambda: remote_nodedb_reset(remote_node),
        "set_fixed_position": lambda: remote_set_fixed_position(
            remote_node,
            float(params["lat"]),
            float(params["lng"]),
            int(params.get("alt", 0)),
        ),
        "clear_fixed_position": lambda: remote_remove_fixed_position(remote_node),
        "set_time": lambda: remote_set_time(remote_node, int(params.get("epoch_secs", 0))),
        "remove_node": lambda: remote_remove_node(remote_node, str(params["target_node_id"])),
    }
    fn = action_map.get(action)
    if fn is None:
        raise ValueError(f"Unknown admin action: {action}")

    started = time.time()
    fn()
    return {"action": action, "ok": True, "elapsed_s": round(time.time() - started, 2)}