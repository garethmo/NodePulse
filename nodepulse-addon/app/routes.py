"""
NodePulse Addon — REST API Route Handlers.

Each handler is a standalone coroutine that receives an aiohttp Request and
returns a Response. Handlers are kept thin: they validate input, delegate
to the MeshtasticConnection, and format the response. No business logic lives
here — that belongs in connection.py.

All responses use JSON. Error responses always include a human-readable
"error" key so clients can display a meaningful message.
"""
import json
import logging
import os
from typing import Any, Dict

import aiohttp
import re
from aiohttp import web

from .connection import MeshtasticConnection

logger = logging.getLogger(__name__)

# A canonical Meshtastic node ID is a "!" followed by up to 8 hex digits. The
# Web UI always sends IDs in this form, so we reject anything else before
# handing it to the meshtastic library.
_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{1,8}$")


def _validate_destination(body: Dict[str, Any]):
    """Extract and validate a 'destination' node ID from a request body.

    Returns the stripped destination string, or None if missing/invalid.
    """
    destination = (body.get("destination") or "").strip()
    if not destination or not _NODE_ID_RE.match(destination):
        return None
    return destination

# The NodePulse HA custom integration's relay endpoints (/api/nodepulse/*) are
# served by Home Assistant *core* — NOT by this addon. So the addon must reach
# HA on its own port (8123 by default), which is configurable via the addon's
# ha_base_url option. We read it from app["config"] at request time rather than
# hardcoding it here.

# Candidate base URLs to try when relaying to the integration. The addon runs
# in its own Docker container, so "localhost" there is the addon itself, not
# HA core. The supervisor network exposes HA core as "homeassistant" (standard
# HAOS) or "supervisor" (legacy). We try the standard supervisor hostnames
# FIRST, before the user-configured value, because a misconfigured ha_base_url
# (e.g. an ingress URL) would fail with 401/403 and waste time.
_HA_CANDIDATES = (
    "http://homeassistant:8123",      # Standard HAOS supervisor hostname
    "http://supervisor:8123",         # Legacy/alternative
    "http://hassio:8123",             # Legacy
)
# Fallback candidates tried when none of the supervisor hostnames resolve
# (custom Docker, non-HAOS installs, core-in-venv). Tried AFTER the supervisor
# candidates fail but BEFORE the user-configured ha_base_url (which may be
# misconfigured / ingress URL).
_HA_FALLBACK_CANDIDATES = (
    "http://localhost:8123",
    "http://127.0.0.1:8123",
    "http://172.17.0.1:8123",        # Docker gateway (bridge mode for HAOS)
    "http://host.docker.internal:8123",
)

# Cache the last HA base URL that produced a successful relay response.
# After the first successful probe we go straight to the known-good URL
# on subsequent calls, skipping the full waterfall of candidates (which
# could block up to len(candidates) * per-candidate-timeout seconds).
# Reset to None if the cached URL fails so the fallback chain re-runs.
_working_ha_base: str | None = None

# Per-candidate TCP connect timeout (seconds). Keep this short so a host
# that is unreachable fails quickly and we move to the next candidate.
_RELAY_TIMEOUT_S = 2


async def _relay_to_integration(request: web.Request, method: str, path: str, json_body=None) -> dict:
    """
    Relay an HTTP request to the NodePulse integration's local API, trying each
    candidate HA base URL until one responds.

    On the first successful relay we cache the working base URL. Subsequent
    calls go straight to the cached URL, skipping the full candidate waterfall.
    If the cached URL later fails we clear it and fall back to the full list.

    Returns the parsed JSON dict on success. Raises RuntimeError with a helpful
    message if no candidate could be reached / all rejected the request.
    """
    global _working_ha_base

    configured = request.app["config"].ha_base_url.rstrip("/")

    # Build candidate list: try the hardcoded supervisor network hostnames FIRST
    # (most reliable in HAOS), then fallback hostnames for non-HAOS setups,
    # then the cached working URL, then the user-configured value (which might
    # be an ingress URL or wrong). This avoids wasting time on a misconfigured
    # ha_base_url that returns 401/403.
    config = request.app["config"]
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    # When the user has set disable_token_validation, the integration's
    # _validate_token() checks for "expected" (SUPERVISOR_TOKEN on HA core).
    # If HA core has SUPERVISOR_TOKEN set but the addon sends no auth header,
    # _validate_token returns True. If disable_token_validation is False,
    # we send the token; otherwise we omit the header entirely so the
    # integration skips the token check.
    send_token = (
        not config.disable_token_validation
        and bool(supervisor_token)
    )

    seen: set[str] = set()
    candidates: list[str] = []
    for url in _HA_CANDIDATES:
        if url not in seen:
            seen.add(url)
            candidates.append(url)
    if _working_ha_base and _working_ha_base not in seen:
        seen.add(_working_ha_base)
        candidates.append(_working_ha_base)
    for url in _HA_FALLBACK_CANDIDATES:
        if url not in seen:
            seen.add(url)
            candidates.append(url)
    if configured and configured not in seen:
        seen.add(configured)
        candidates.append(configured)

    last_status = None
    last_body = None
    last_url = None

    async with aiohttp.ClientSession() as session:
        for base in candidates:
            url = f"{base}{path}"
            try:
                kwargs: dict = {
                    "timeout": aiohttp.ClientTimeout(total=_RELAY_TIMEOUT_S),
                    "headers": {},
                }
                if send_token:
                    kwargs["headers"]["Authorization"] = f"Bearer {supervisor_token}"
                if config.disable_token_validation:
                    kwargs["headers"]["X-NodePulse-Skip-Token"] = "true"
                if method.upper() == "POST":
                    kwargs["headers"]["Content-Type"] = "application/json"
                    kwargs["json"] = json_body
                logger.debug("Relaying %s %s body=%s", method, url, json_body)
                async with session.request(method, url, **kwargs) as resp:
                    last_status = resp.status
                    last_url = url
                    raw = await resp.text()
                    last_body = raw
                    logger.debug(
                        "Relay response from %s: status=%s headers=%s body=%s",
                        url, resp.status, dict(resp.headers), raw[:500],
                    )
                    if resp.status in (200, 201):
                        # Cache this base so we go straight here next time.
                        if _working_ha_base != base:
                            _working_ha_base = base
                            logger.debug("HA relay: caching working base URL as %s", base)
                        try:
                            return json.loads(raw)
                        except Exception as exc:
                            logger.error(
                                "Integration at %s returned OK but invalid JSON: %s",
                                base, exc,
                            )
                            raise RuntimeError(
                                f"Integration at {base} returned an unparseable response"
                            )
                    # 401/403 means HA auth rejected us. Try the next candidate
                    # with SUPERVISOR_TOKEN, except if all candidates failed auth.
                    if resp.status in (401, 403):
                        if base == _working_ha_base:
                            _working_ha_base = None
                        logger.debug(
                            "Relay candidate %s returned %s (unauthorized) — trying next",
                            base, resp.status,
                        )
                        continue
                    # A real response (even an error) means we found HA core;
                    # surface its error rather than trying other candidates.
                    try:
                        err = json.loads(raw) if raw else {}
                        detail = err.get("error", "")
                    except Exception:
                        # Response wasn't JSON (e.g. HA login page / HTML stack trace).
                        detail = raw[:200] if raw else ""
                    raise RuntimeError(
                        f"Integration at {base} rejected request (HTTP {resp.status}): {detail}".strip()
                    )
            except RuntimeError:
                raise  # propagate the integration's own error message
            except Exception as exc:
                # If the cached URL fails, clear it so we re-probe next time.
                if base == _working_ha_base:
                    logger.debug("Cached HA base %s is no longer reachable — resetting", base)
                    _working_ha_base = None
                logger.debug("Relay candidate %s failed: %s", base, exc)
                continue
    logger.error(
        "Could not reach NodePulse integration. last_url=%s last_status=%s last_body=%s",
        last_url, last_status, (last_body or "")[:500],
    )
    if last_status in (401, 403):
        raise RuntimeError(
            f"NodePulse integration rejected the request (HTTP {last_status}). "
            "This usually means the SUPERVISOR_TOKEN is missing or mismatched "
            "between the addon container and HA core. On HAOS ensure the addon "
            "is installed via the Supervisor add-on store. On custom Docker, "
            "pass SUPERVISOR_TOKEN to both the HA core and addon containers. "
            "Alternatively, set 'disable_token_validation' to true in the "
            "addon config."
        )
    raise RuntimeError(
        f"Could not reach the NodePulse integration. Tried: {', '.join(candidates)}. "
        "If you are on a non-HAOS install (custom Docker, venv, Supervised without "
        "the addon store), set 'ha_base_url' in the addon config to the URL where "
        "Home Assistant core is reachable from the addon container (e.g. "
        "http://172.17.0.1:8123). Otherwise, ensure the NodePulse custom integration "
        "is installed in HA and reachable from the addon."
    )


def _apply_access_key(request: web.Request) -> None:
    """
    If the integration relayed an access key via the X-NodePulse-Access-Key
    header, push it down to the live Meshtastic connection so admin operations
    (e.g. on nodes that require authentication) can succeed. Harmless when no
    key is supplied or the node does not require one.
    """
    key = request.headers.get("X-NodePulse-Access-Key")
    if key:
        conn: MeshtasticConnection = request.app["connection"]
        conn.set_access_key(key)


def _json_response(data: Any, status: int = 200) -> web.Response:
    """Helper that serialises to JSON with consistent content-type."""
    return web.Response(
        text=json.dumps(data, default=str),
        content_type="application/json",
        status=status,
    )


def _error_response(message: str, status: int = 500) -> web.Response:
    return _json_response({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Route: GET /api/status
# ---------------------------------------------------------------------------

async def handle_status(request: web.Request) -> web.Response:
    """
    Return the current connection state, node identity, and addon configuration.

    This is polled by the HA integration and the Web UI Settings page. We merge
    in the live config values so the Settings view can display them without
    needing a separate endpoint.
    """
    conn: MeshtasticConnection = request.app["connection"]
    config = request.app["config"]
    _apply_access_key(request)
    try:
        status = await conn.get_status()
        # Attach the addon's runtime config so the Settings page can render it.
        status["config"] = {
            "connection_type": config.connection_type,
            "meshtastic_host": config.meshtastic_host,
            "meshtastic_port": config.meshtastic_port,
            "proxy_host": config.proxy_host or "",
            "proxy_port": config.proxy_port,
            "scan_interval": config.scan_interval,
            "log_level": config.log_level,
            "ha_base_url": config.ha_base_url,
            "ignored_nodes": list(getattr(config, "ignored_nodes", [])),
            "access_key_set": bool(config.access_key),
        }
        return _json_response(status)
    except Exception as exc:
        logger.error("Error fetching status: %s", exc)
        return _error_response("Failed to retrieve status")


# ---------------------------------------------------------------------------
# Route: GET /api/nodes
# ---------------------------------------------------------------------------

async def handle_nodes(request: web.Request) -> web.Response:
    """
    Return the full node list, optionally filtered by the ignored_nodes config.

    Nodes in the ignored_nodes list are excluded from the response entirely
    rather than being marked inactive, keeping the API surface clean for the
    HA integration and the Web UI.
    """
    conn: MeshtasticConnection = request.app["connection"]
    ignored: set = request.app["ignored_nodes"]
    _apply_access_key(request)

    try:
        nodes = await conn.get_nodes()
        # Filter out nodes the user has asked to ignore by their hex ID.
        visible_nodes = [n for n in nodes if n.get("id") not in ignored]
        return _json_response(visible_nodes)
    except Exception as exc:
        logger.error("Error fetching nodes: %s", exc)
        return _error_response("Failed to retrieve nodes")


async def handle_clear_stale_nodes(request: web.Request) -> web.Response:
    """
    Remove every node flagged ``stale`` (not currently heard by the radio).

    The persistent store keeps radio-evicted nodes visible; this endpoint
    lets the user purge that history on demand so only live-heard nodes
    remain. Returns the count removed.
    """
    conn: MeshtasticConnection = request.app["connection"]
    _apply_access_key(request)
    try:
        removed = await conn.clear_stale_nodes()
        return _json_response({"removed": removed})
    except Exception as exc:
        logger.error("Error clearing stale nodes: %s", exc)
        return _error_response("Failed to clear stale nodes")


# ---------------------------------------------------------------------------
# Route: GET /api/messages
# ---------------------------------------------------------------------------

async def handle_messages(request: web.Request) -> web.Response:
    """
    Return the most recent received text messages (oldest first).

    This powers the Web UI message feed, mirroring MeshSense's "Message Window"
    — inbound packets captured via the meshtastic pubsub listener in
    connection.py, not just locally-sent ones.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        messages = await conn.get_messages()
        return _json_response(messages)
    except Exception as exc:
        logger.error("Error fetching messages: %s", exc)
        return _error_response("Failed to retrieve messages")


# ---------------------------------------------------------------------------
# Route: GET /api/channels
# ---------------------------------------------------------------------------

async def handle_channels(request: web.Request) -> web.Response:
    """Return the channel list configured on the connected Meshtastic node."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        channels = await conn.get_channels()
        logger.debug("Channels fetched: count=%s, data=%s", len(channels) if channels else 0, channels)
        return _json_response(channels)
    except Exception as exc:
        logger.error("Error fetching channels: %s", exc)
        return _error_response("Failed to retrieve channels")


# ---------------------------------------------------------------------------
# Route: POST /api/send
# ---------------------------------------------------------------------------

async def handle_send(request: web.Request) -> web.Response:
    """
    Send a text message over the mesh.

    Expected JSON body:
        {
            "text": "Hello mesh!",
            "destination": "!abcd1234",  // optional — omit for broadcast
            "channel": 0                 // optional — defaults to 0
        }

    The meshtastic library handles PKI encryption automatically for
    direct messages when a channel key is in place. We intentionally do
    NOT re-implement encryption here; the library owns that responsibility.
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    text = body.get("text", "").strip()
    if not text:
        return _error_response("'text' field is required and must not be empty", status=400)

    destination = body.get("destination")  # None → broadcast

    # Coerce channel to an int defensively — request bodies may contain a
    # string or an out-of-range / invalid value that would otherwise raise
    # and produce an unhandled 500.
    try:
        channel = int(body.get("channel", 0))
    except (TypeError, ValueError):
        return _error_response("'channel' must be an integer", status=400)
    if channel < 0 or channel > 7:
        return _error_response("'channel' must be between 0 and 7", status=400)

    try:
        success = await conn.send_message(text, destination=destination, channel=channel)
        if success:
            return _json_response({"sent": True})
        return _error_response("Message was not accepted by the Meshtastic interface", status=502)
    except Exception as exc:
        logger.error(
            "Unhandled error in send handler (destination=%s): %s", destination, exc
        )
        return _error_response("Failed to send message")


# ---------------------------------------------------------------------------
# Route: POST /api/traceRoute
# ---------------------------------------------------------------------------

async def handle_traceroute(request: web.Request) -> web.Response:
    """
    Initiate a traceroute towards a destination node.

    Expected JSON body:
        { "destination": "!abcd1234" }

    Traceroute results arrive asynchronously via the Meshtastic event system
    and are NOT returned in this HTTP response. The response only confirms
    that the traceroute packet was dispatched. The Web UI polls /api/nodes
    to see hop counts updated after a traceroute completes.
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    destination = _validate_destination(body)
    if destination is None:
        return _error_response("'destination' must be a node ID like '!abc12345'", status=400)

    try:
        success = await conn.request_traceroute(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:
        logger.error(
            "Traceroute dispatch failed (destination=%s): %s", destination, exc
        )
        return _error_response("Failed to dispatch traceroute")


# ---------------------------------------------------------------------------
# Route: POST /api/requestPosition
# ---------------------------------------------------------------------------

async def handle_request_position(request: web.Request) -> web.Response:
    """
    Ask a specific node to send its current GPS position.

    Expected JSON body:
        { "destination": "!abcd1234" }
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    destination = _validate_destination(body)
    if destination is None:
        return _error_response("'destination' must be a node ID like '!abc12345'", status=400)

    try:
        success = await conn.request_position(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:
        logger.error(
            "Position request dispatch failed (destination=%s): %s", destination, exc
        )
        return _error_response("Failed to dispatch position request")


# ---------------------------------------------------------------------------
# Route: GET /api/tracked-nodes
# ---------------------------------------------------------------------------

async def handle_tracked_nodes(request: web.Request) -> web.Response:
    """
    Return the node IDs the user currently tracks as HA entities.

    The authoritative tracked-set lives in the integration's config entry
    options (the integration is the only component that can register entities).
    We proxy the request to the integration's local relay endpoint so the Web
    UI has a single source of truth.
    """
    try:
        data = await _relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        node_ids = data.get("node_ids", [])
        return _json_response({"node_ids": node_ids})
    except RuntimeError as exc:
        return _error_response(str(exc), status=502)
    except Exception as exc:
        logger.error("Failed to fetch tracked nodes from integration: %s", exc)
        return _error_response("Failed to reach NodePulse integration")


# ---------------------------------------------------------------------------
# Route: POST /api/track-node
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Route: GET /api/position-history
# Route: GET /api/position-history/{node_id}
# ---------------------------------------------------------------------------

async def handle_position_history(request: web.Request) -> web.Response:
    """Return position history for all nodes, or for a single node if node_id is
    given in the path.

    Position history is a dict of node_id -> [{lat, lng, alt?, timestamp}, ...],
    capped at _POS_HISTORY_MAX entries per node. Used to draw GPS trails on the
    map overlay.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id")
    try:
        data = await conn.get_position_history(node_id)
        return _json_response(data)
    except Exception as exc:
        logger.error("Error fetching position history: %s", exc)
        return _error_response("Failed to retrieve position history")


# ---------------------------------------------------------------------------
# Route: GET /api/tags
# ---------------------------------------------------------------------------

async def handle_tags(request: web.Request) -> web.Response:
    """Return all user-defined node tags: {node_id: [tag, ...], ...}."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        tags = await conn.get_tags()
        return _json_response(tags)
    except Exception as exc:
        logger.error("Error fetching tags: %s", exc)
        return _error_response("Failed to retrieve tags")


# ---------------------------------------------------------------------------
# Route: PUT /api/tags
# ---------------------------------------------------------------------------

async def handle_set_tags(request: web.Request) -> web.Response:
    """
    Set the tags for a single node. Returns the full updated tags dict.

    Expected JSON body:
        { "node_id": "!abcd1234", "tags": ["gateway", "roof"] }
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    node_id = (body.get("node_id") or "").strip()
    if not node_id or not _NODE_ID_RE.match(node_id):
        return _error_response("'node_id' must be a valid node ID like '!abc12345'", status=400)

    tags = body.get("tags")
    if tags is None or not isinstance(tags, list):
        return _error_response("'tags' must be a list of strings", status=400)

    try:
        result = await conn.set_tags(node_id, tags)
        return _json_response(result)
    except ValueError as exc:
        return _error_response(str(exc), status=400)
    except Exception as exc:
        logger.error("Error setting tags (node=%s): %s", node_id, exc)
        return _error_response("Failed to set tags")


async def handle_track_node(request: web.Request) -> web.Response:
    """
    Enable or disable HA entity tracking for a node.

    Expected JSON body:
        { "node_id": "!abcd1234", "enabled": true }

    The Web UI cannot register HA entities directly, so we relay the request
    to the NodePulse integration's local API (served by HA core on its own
    port). The integration validates the node and creates/removes the
    device_tracker + sensor set for that node.
    """
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    node_id = (body.get("node_id") or "").strip()
    if not node_id:
        return _error_response("'node_id' field is required", status=400)

    enabled = bool(body.get("enabled", False))

    try:
        await _relay_to_integration(
            request, "POST", "/api/nodepulse/track",
            json_body={"node_id": node_id, "enabled": enabled},
        )
        return _json_response({"node_id": node_id, "enabled": enabled})
    except RuntimeError as exc:
        logger.error("Track-node relay rejected by integration (node=%s): %s", node_id, exc)
        return _error_response(str(exc), status=502)
    except Exception as exc:
        logger.error(
            "Failed to relay track-node request to integration (node=%s): %s",
            node_id, exc,
        )
        return _error_response("Failed to reach NodePulse integration")
