"""
NodePulse Addon — REST API Route Handlers.

Each handler is a standalone coroutine that receives an aiohttp Request and
returns a Response. Handlers are kept thin: they validate input, delegate
to the MeshtasticConnection, and format the response. No business logic lives
here — that belongs in connection.py.

All responses use JSON. Error responses always include a human-readable
"error" key so clients can display a meaningful message.
"""
import asyncio
import datetime
import itertools
import json
import logging
import os
import re
from typing import Any

import aiohttp
from aiohttp import web

from .connection import MeshtasticConnection
from .terrain import TerrainService, analyze_link

logger = logging.getLogger(__name__)

# A canonical Meshtastic node ID is a "!" followed by up to 8 hex digits. The
# Web UI always sends IDs in this form, so we reject anything else before
# handing it to the meshtastic library.
_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{1,8}$")


def _validate_destination(body: dict[str, Any]):
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


def _token_fingerprint(token: str) -> str:
    """Short non-secret fingerprint of a credential for diagnostics.

    Only length and the first/last 4 characters are logged — never the full
    token. Lets us verify the exact string an HA core instance received
    matches what the addon config holds (catches whitespace, truncation,
    encoding issues and cross-instance token mixups).
    """
    if not token:
        return "none"
    return f"len={len(token)} head={token[:4]} tail={token[-4:]}"


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
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    # Relay credentials in preference order: the Supervisor-injected token
    # (HAOS) first, then the user-configured long-lived HA access token. If
    # every candidate rejects the first credential with 401/403, the whole
    # waterfall is retried with the next one, so a missing or mismatched
    # SUPERVISOR_TOKEN doesn't permanently break Track-in-HA on custom
    # Docker/venv installs. The legacy X-NodePulse-Skip-Token bypass header
    # has been removed — token validation is always on.
    ha_access_token = (request.app["config"].ha_access_token or "").strip()
    tokens: list[tuple[str, str]] = []
    seen_tokens: set[str] = set()
    for label, tok in (("SUPERVISOR_TOKEN", supervisor_token), ("ha_access_token", ha_access_token)):
        if tok and tok not in seen_tokens:
            seen_tokens.add(tok)
            tokens.append((label, tok))

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

    if not tokens:
        logger.error(
            "No relay credential configured: SUPERVISOR_TOKEN env unset and "
            "addon option 'ha_access_token' is empty"
        )
        raise RuntimeError(
            "No relay credential configured. The addon->HA relay needs either "
            "the SUPERVISOR_TOKEN (injected automatically on HAOS) or the addon's "
            "'ha_access_token' option set to a Home Assistant long-lived access "
            "token (Profile -> Security -> Long-lived access tokens). Note: "
            "'ha_access_token' is separate from 'access_key', which authenticates "
            "to your Meshtastic node, not to Home Assistant."
        )

    async with aiohttp.ClientSession() as session:
        for token_label, token in tokens:
            for base in candidates:
                url = f"{base}{path}"
                try:
                    kwargs: dict = {
                        "timeout": aiohttp.ClientTimeout(total=_RELAY_TIMEOUT_S),
                        "headers": {},
                    }
                    if token:
                        kwargs["headers"]["Authorization"] = f"Bearer {token}"
                    if method.upper() == "POST":
                        kwargs["headers"]["Content-Type"] = "application/json"
                        kwargs["json"] = json_body
                    logger.debug(
                        "Relaying %s %s body=%s token=%s",
                        method, url, json_body, _token_fingerprint(token),
                    )
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
                                ) from exc
                        # 401/403 means HA auth rejected us with this credential.
                        # Try the next candidate; after all candidates fail, the
                        # outer loop retries the waterfall with the next token.
                        if resp.status in (401, 403):
                            if base == _working_ha_base:
                                _working_ha_base = None
                            logger.debug(
                                "Relay candidate %s returned %s (unauthorized) "
                                "with %s %s — trying next",
                                base, resp.status,
                                f"{token_label} (Bearer)" if token else "no auth",
                                _token_fingerprint(token),
                            )
                            continue
                        # A real response (even an error) means we found HA core;
                        # surface its error rather than trying other candidates.
                        try:
                            err = json.loads(raw) if raw else {}
                            detail = err.get("error", "")
                        except Exception:  # noqa: BLE001
                            # Response wasn't JSON (e.g. HA login page / HTML stack trace).
                            detail = raw[:200] if raw else ""
                        raise RuntimeError(
                            f"Integration at {base} rejected request (HTTP {resp.status}): {detail}".strip()
                        )
                except RuntimeError:
                    raise  # propagate the integration's own error message
                except Exception as exc:  # noqa: BLE001
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
        detail = ""
        try:
            parsed = json.loads(last_body or "")
            detail = parsed.get("reason") or parsed.get("error") or ""
        except Exception:  # noqa: BLE001
            detail = (last_body or "")[:200]
        raise RuntimeError(
            f"NodePulse integration rejected the request (HTTP {last_status}): {detail} "
            if detail else
            f"NodePulse integration rejected the request (HTTP {last_status}). "
            "This usually means the SUPERVISOR_TOKEN is missing or mismatched "
            "between the addon container and HA core. On HAOS ensure the addon "
            "is installed via the Supervisor add-on store. On custom Docker/venv, "
            "either pass SUPERVISOR_TOKEN to both containers, or set the addon's "
            "'ha_access_token' option to a Home Assistant long-lived access token "
            "(Profile -> Security -> Long-lived access tokens). Note: 'ha_access_token' "
            "is a separate option from 'access_key' — 'access_key' authenticates to "
            "your Meshtastic node, not to Home Assistant, and has no effect on this relay."
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
            "ha_access_token_set": bool(config.ha_access_token),
            "disable_token_validation": config.disable_token_validation,
            "ignored_nodes": list(getattr(config, "ignored_nodes", [])),
            "access_key_set": bool(config.access_key),
            "scheduled_messages_enabled": getattr(config, "scheduled_messages_enabled", True),
            # MQTT Bridge settings
            "mqtt_enabled": config.mqtt_enabled,
            "mqtt_address": config.mqtt_address,
            "mqtt_port": config.mqtt_port,
            "mqtt_username_set": bool(config.mqtt_username),
            "mqtt_password_set": bool(config.mqtt_password),
            "mqtt_topic": config.mqtt_topic,
            "mqtt_forwarding_enabled": config.mqtt_forwarding_enabled,
            "mqtt_geo_filter_enabled": config.mqtt_geo_filter_enabled,
            "mqtt_lat_min": config.mqtt_lat_min,
            "mqtt_lat_max": config.mqtt_lat_max,
            "mqtt_lng_min": config.mqtt_lng_min,
            "mqtt_lng_max": config.mqtt_lng_max,
            "mqtt_portnum_allowlist": list(config.mqtt_portnum_allowlist),
            "mqtt_node_blocklist": list(config.mqtt_node_blocklist),
            # Telegram Bot settings
            "telegram_enabled": config.telegram_enabled,
            "telegram_chat_id": config.telegram_chat_id,
            "telegram_authorized_chat_ids": list(config.telegram_authorized_chat_ids),
            "telegram_forward_channels": config.telegram_forward_channels,
            "telegram_forward_dms": config.telegram_forward_dms,
            "telegram_allow_commands": config.telegram_allow_commands,
            "telegram_bot_token_set": bool(config.telegram_bot_token),
            # Auto Responder settings
            "auto_responder_enabled": config.auto_responder_enabled,
            "auto_responder_message": config.auto_responder_message,
            "auto_traceroute_enabled": config.auto_traceroute_enabled,
            # Terrain Link Analysis
            "terrain_dem_url": getattr(config, "terrain_dem_url", ""),
        }
        # Embed the addon version from config.json so the UI can display it
        # without hardcoding it in the HTML template.
        try:
            # HA Supervisor mounts config.json one level above the app/ package.
            _cfg_candidates = [
                os.path.join(os.path.dirname(__file__), "..", "config.json"),
                "/data/config.json",
            ]
            for _p in _cfg_candidates:
                if os.path.exists(_p):
                    with open(_p) as _f:
                        status["addon_version"] = json.load(_f).get("version", "")
                    break
        except Exception:  # noqa: BLE001
            status["addon_version"] = ""
        # Attach scheduled messages stats
        with conn._scheduled_messages_lock:
            status["scheduled_count"] = len(conn._scheduled_messages)
            if conn._scheduled_messages:
                status["next_scheduled_time"] = min(m[0] for m in conn._scheduled_messages)
            else:
                status["next_scheduled_time"] = None

        return _json_response(status)
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.error("Error clearing stale nodes: %s", exc)
        return _error_response("Failed to clear stale nodes")


# ---------------------------------------------------------------------------
# Route: GET /api/node/{node_id}/signal
# ---------------------------------------------------------------------------

async def handle_node_signal(request: web.Request) -> web.Response:
    """
    Return per-node signal/health diagnostics (2.8 feature set).

    Mirrors the Telegram ``/diag`` command. Computes hops-away, rolling SNR
    average + quality classification, battery/voltage/uptime, channel/air
    utilisation, environment telemetry, and (on 2.8 firmware) the noise floor.

    Returns 404 when the node is unknown, 503 when the radio is offline.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    _apply_access_key(request)
    try:
        result = await conn.get_node_signal(node_id)
        if not result:
            return _error_response("Node not found", status=404)
        return _json_response(result)
    except ConnectionError as exc:
        return _error_response(str(exc), status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error computing node signal for %s: %s", node_id, exc)
        return _error_response("Failed to compute node diagnostics")


# ---------------------------------------------------------------------------
# Route: GET /api/node/{node_id}/gpx
# ---------------------------------------------------------------------------

async def handle_node_gpx(request: web.Request) -> web.Response:
    """
    Export a single node's position history as a downloadable GPX 1.1 track.

    The track (``<trk>``) is built from the gateway's stored position history
    so it works even when the node is offline. When there are no fixes the
    response is a valid (empty) GPX document with just metadata.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    try:
        history = await conn.get_position_history(node_id)
        points = (history or {}).get(node_id, []) if isinstance(history, dict) else []
        nodes = await conn.get_nodes()
        node = next((n for n in nodes if n.get("id") == node_id), None)
        name = (node or {}).get("long_name") or (node or {}).get("short_name") or node_id
        gpx = _build_gpx_track(node_id, name, points)
        filename = f"nodepulse_{node_id.lstrip('!')}_track.gpx"
        return web.Response(
            body=gpx.encode("utf-8"),
            content_type="application/gpx+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error building GPX for %s: %s", node_id, exc)
        return _error_response("Failed to build GPX track")


def _build_gpx_track(node_id: str, name: str, points: list[dict]) -> str:
    """Build a GPX 1.1 document with a single <trk> from position history."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    escapes = {k: v for k, v in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&apos;")]}

    def esc(s):
        return "".join(escapes.get(c, c) for c in str(s))

    def _fmt(ts):
        if not ts:
            return now
        try:
            return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:  # noqa: BLE001
            return now

    trkpts = []
    for p in points:
        lat = p.get("latitude") if "latitude" in p else p.get("lat")
        lng = p.get("longitude") if "longitude" in p else p.get("lng")
        if lat is None or lng is None:
            continue
        alt = p.get("altitude") if "altitude" in p else p.get("alt")
        alt_str = f'<ele>{alt}</ele>' if alt is not None else ""
        trkpts.append(
            f'      <trkpt lat="{lat}" lon="{lng}">\n'
            f'        {alt_str}\n'
            f'        <time>{_fmt(p.get("timestamp"))}</time>\n'
            f'      </trkpt>'
        )
    track = "\n".join(trkpts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="NodePulse" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">\n'
        '  <metadata>\n'
        f'    <name>NodePulse Track {esc(name)}</name>\n'
        f'    <time>{now}</time>\n'
        '  </metadata>\n'
        '  <trk>\n'
        f'    <name>{esc(name)} ({esc(node_id)})</name>\n'
        '    <trkseg>\n'
        f'{track}\n'
        '    </trkseg>\n'
        '  </trk>\n'
        '</gpx>'
    )


# ---------------------------------------------------------------------------
# Route: GET /api/hops
# ---------------------------------------------------------------------------

async def handle_hops(request: web.Request) -> web.Response:
    """
    Return the distribution of nodes by hop count from the gateway.

    Response shape:
        {
          "distribution": [ {"hops": 0, "count": 1}, {"hops": 1, "count": 7}, ... ],
          "total": 23,
          "max_hops": 3
        }
    """
    conn: MeshtasticConnection = request.app["connection"]
    _apply_access_key(request)
    try:
        nodes = await conn.get_nodes()
        buckets: dict[int, int] = {}
        total = 0
        max_hops = 0
        for n in nodes:
            h = n.get("hops_away")
            if h is None:
                continue
            h = int(h)
            buckets[h] = buckets.get(h, 0) + 1
            total += 1
            if h > max_hops:
                max_hops = h
        distribution = [{"hops": h, "count": buckets.get(h, 0)} for h in range(0, max_hops + 1)]
        return _json_response({
            "distribution": distribution,
            "total": total,
            "max_hops": max_hops,
        })
    except Exception as exc:  # noqa: BLE001
        logger.error("Error computing hop distribution: %s", exc)
        return _error_response("Failed to compute hop distribution")


# ---------------------------------------------------------------------------
# Route: GET /api/beacon
# ---------------------------------------------------------------------------

async def handle_beacon(request: web.Request) -> web.Response:
    """
    Return the local gateway's Mesh Beacon (2.8) module configuration.

    The 2.8 beacon broadcasts the gateway's position on a fixed interval.
    Returns { "available": bool, ... } — ``available`` is False when the
    installed meshtastic library predates 2.8 and cannot read the config.
    """
    conn: MeshtasticConnection = request.app["connection"]
    _apply_access_key(request)
    try:
        return _json_response(await conn.get_beacon_config())
    except ConnectionError as exc:
        return _error_response(str(exc), status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading beacon config: %s", exc)
        return _error_response("Failed to read beacon configuration")


# ---------------------------------------------------------------------------
# Route: DELETE /api/node/{node_id}
# ---------------------------------------------------------------------------

async def handle_delete_node(request: web.Request) -> web.Response:
    """Remove a single node from the persistent store by its hex ID."""
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "")
    if not node_id:
        return _error_response("node_id is required", status=400)
    _apply_access_key(request)
    try:
        deleted = await conn.delete_node(node_id)
        if not deleted:
            return _error_response("Node not found", status=404)
        return _json_response({"deleted": node_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("Error deleting node %s: %s", node_id, exc)
        return _error_response("Failed to delete node")


# ---------------------------------------------------------------------------
# Route: GET /api/messages/search
# ---------------------------------------------------------------------------

async def handle_search_messages(request: web.Request) -> web.Response:
    """
    Search message text across all conversations.

    Query parameters:
      - q: search query string (substring match on message text)
      - limit: max results to return (default 50, max 200)

    Returns matching messages sorted by timestamp (newest first).
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        q = request.query.get("q", "").strip()
        if not q:
            return _error_response("'q' query parameter is required", status=400)

        limit = min(int(request.query.get("limit", 50)), 200)

        messages = await conn.get_messages()

        # Case-insensitive substring match on message text
        matched = [
            m for m in messages
            if q.lower() in (m.get("text") or "").lower()
        ]

        # Sort by timestamp descending (newest first)
        matched.sort(key=lambda m: m.get("timestamp", 0), reverse=True)

        matched = matched[:limit]

        return _json_response(matched)
    except ValueError:
        return _error_response("'limit' must be an integer", status=400)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error searching messages: %s", exc)
        return _error_response("Failed to search messages")


# ---------------------------------------------------------------------------
# Route: GET /api/messages
# ---------------------------------------------------------------------------

async def handle_messages(request: web.Request) -> web.Response:
    """
    Return the most recent received text messages (oldest first).

    This powers the Web UI message feed, mirroring MeshSense's "Message Window"
    — inbound packets captured via the meshtastic pubsub listener in
    connection.py, not just locally-sent ones.

    Query params:
      - load_archived: if "true", also loads archived messages from date-based files
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        load_archived = request.query.get("load_archived", "false").lower() == "true"
        messages = await conn.get_messages(load_archived=load_archived)
        return _json_response(messages)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching messages: %s", exc)
        return _error_response("Failed to retrieve messages")


# ---------------------------------------------------------------------------
# Route: GET /api/messages/export
# ---------------------------------------------------------------------------

async def handle_export_messages(request: web.Request) -> web.Response:
    """
    Export message history as downloadable JSON or CSV file.

    Query params:
      - format: 'json' or 'csv' (default: 'json')
      - conversation: optional conversation key (e.g. 'ch:0', 'dm:!12345678')
    """
    conn: MeshtasticConnection = request.app["connection"]
    fmt = request.query.get("format", "json").lower().strip()
    conv_filter = request.query.get("conversation", "").strip()

    try:
        messages = await conn.get_messages()
        if conv_filter:
            messages = [m for m in messages if m.get("conversation") == conv_filter]

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")

        if fmt == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "id", "timestamp", "datetime", "from_id", "from_name",
                "conversation", "outgoing", "channel", "ack_status", "text"
            ])

            for m in messages:
                ts = m.get("timestamp") or 0
                dt_str = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat() if ts else ""
                writer.writerow([
                    m.get("id", ""),
                    ts,
                    dt_str,
                    m.get("from_id", ""),
                    m.get("from_name", ""),
                    m.get("conversation", ""),
                    m.get("outgoing", False),
                    m.get("channel", ""),
                    m.get("ack_status") or m.get("status") or "",
                    m.get("text", ""),
                ])

            filename = f"nodepulse_messages_{now_str}.csv"
            return web.Response(
                body=output.getvalue().encode("utf-8"),
                content_type="text/csv",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                },
            )

        # Default: JSON
        filename = f"nodepulse_messages_{now_str}.json"
        body_str = json.dumps(messages, indent=2)
        return web.Response(
            body=body_str.encode("utf-8"),
            content_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error exporting messages: %s", exc)
        return _error_response("Failed to export messages")


# ---------------------------------------------------------------------------
# Routes: /api/waypoints
# ---------------------------------------------------------------------------

async def handle_get_waypoints(request: web.Request) -> web.Response:
    """Return all active (non-expired) waypoints."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        waypoints = await conn.get_waypoints()
        return _json_response(waypoints)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching waypoints: %s", exc)
        return _error_response("Failed to retrieve waypoints")


async def handle_add_waypoint(request: web.Request) -> web.Response:
    """Create a new locally-defined waypoint.

    Expected JSON body:
      { "name": str, "lat": float (opt, defaults to 0), "lng": float (opt, defaults to 0),
        "description": str (opt), "icon": str (opt), "expire": int (opt) }
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        body = await request.json()
        lat = body.get("lat")
        lng = body.get("lng")
        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            return _error_response("lat and lng must be numbers", status=400)

        waypoint = {
            "name": body.get("name") or "Waypoint",
            "description": body.get("description") or "",
            "lat": lat,
            "lng": lng,
            "icon": body.get("icon") or "📍",
            "expire": body.get("expire"),
        }
        entry = await conn.add_waypoint(waypoint)
        return _json_response(entry)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error adding waypoint: %s", exc)
        return _error_response("Failed to add waypoint")


async def handle_update_waypoint(request: web.Request) -> web.Response:
    """Update a waypoint's fields (e.g. lat/lng after map drag).

    Acceptable JSON body fields: lat, lng, name, description, icon.
    """
    conn: MeshtasticConnection = request.app["connection"]
    waypoint_id = request.match_info.get("waypoint_id", "")
    if not waypoint_id:
        return _error_response("waypoint_id is required", status=400)
    try:
        body = await request.json()
        updates = {}
        for key in ("lat", "lng", "name", "description", "icon"):
            if key in body:
                val = body[key]
                if key in ("lat", "lng") and val is not None:
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        return _error_response(f"{key} must be a number", status=400)
                updates[key] = val
        if not updates:
            return _error_response("No valid fields to update", status=400)
        updated = await conn.update_waypoint(waypoint_id, updates)
        if updated is None:
            return _error_response("Waypoint not found", status=404)
        return _json_response(updated)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error updating waypoint %s: %s", waypoint_id, exc)
        return _error_response("Failed to update waypoint")


async def handle_delete_waypoint(request: web.Request) -> web.Response:
    """Delete a waypoint by its string ID."""
    conn: MeshtasticConnection = request.app["connection"]
    waypoint_id = request.match_info.get("waypoint_id", "")
    if not waypoint_id:
        return _error_response("waypoint_id is required", status=400)
    try:
        deleted = await conn.delete_waypoint(waypoint_id)
        if not deleted:
            return _error_response("Waypoint not found", status=404)
        return _json_response({"deleted": waypoint_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("Error deleting waypoint %s: %s", waypoint_id, exc)
        return _error_response("Failed to delete waypoint")


# ---------------------------------------------------------------------------
# Route: GET /api/channels
# ---------------------------------------------------------------------------

async def handle_channels(request: web.Request) -> web.Response:
    """Return the channel list configured on the connected Meshtastic node."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        channels = await conn.refresh_channels()
        logger.debug("Channels fetched: count=%s, data=%s", len(channels) if channels else 0, channels)
        return _json_response(channels)
    except Exception as exc:  # noqa: BLE001
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
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
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

    # Check if scheduling is requested (provide 'schedule_at' as a unix timestamp)
    schedule_at = body.get("schedule_at")
    if schedule_at is not None:
        try:
            schedule_at = float(schedule_at)
        except (TypeError, ValueError):
            return _error_response("'schedule_at' must be a unix timestamp", status=400)
        if not getattr(request.app["config"], "scheduled_messages_enabled", True):
            return _error_response("Scheduled messages are disabled in config", status=403)
        # Add to the scheduled messages queue via connection manager
        conn.schedule_message(float(schedule_at), (body.get("destination") or "").strip(), text, channel)
        return _json_response({"scheduled": True, "schedule_at": schedule_at})
    else:
        try:
            logger.debug(
                "API send request: text='%s' destination=%s channel=%s",
                text[:30] if text else "",
                destination or "broadcast",
                channel,
            )
            success = await conn.send_message(text, destination=destination, channel=channel)
            if success:
                return _json_response({"sent": True})
            return _error_response("Message was not accepted by the Meshtastic interface", status=502)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unhandled error in send handler (destination=%s): %s", destination, exc
            )
            return _error_response("Failed to send message")


# ---------------------------------------------------------------------------
# Route: GET /api/messages/search
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
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)

    destination = _validate_destination(body)
    if destination is None:
        return _error_response("'destination' must be a node ID like '!abc12345'", status=400)

    try:
        success = await conn.request_traceroute(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:  # noqa: BLE001
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
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)

    destination = _validate_destination(body)
    if destination is None:
        return _error_response("'destination' must be a node ID like '!abc12345'", status=400)

    try:
        success = await conn.request_position(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.error("Error setting tags (node=%s): %s", node_id, exc)
        return _error_response("Failed to set tags")


# ---------------------------------------------------------------------------
# Route: GET /api/favorites
# ---------------------------------------------------------------------------

async def handle_favorites(request: web.Request) -> web.Response:
    """Return the persisted list of favorite node IDs."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        favorites = await conn.get_favorites()
        return _json_response(favorites)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching favorites: %s", exc)
        return _error_response("Failed to retrieve favorites")


# ---------------------------------------------------------------------------
# Route: PUT /api/favorites
# ---------------------------------------------------------------------------

async def handle_set_favorite(request: web.Request) -> web.Response:
    """
    Mark/unmark a node as favorite. Returns the full list of favorite node IDs.

    Expected JSON body:
        { "node_id": "!abcd1234", "favorited": true }
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)

    node_id = (body.get("node_id") or "").strip()
    if not node_id or not _NODE_ID_RE.match(node_id):
        return _error_response("'node_id' must be a valid node ID like '!abc12345'", status=400)

    favorited = body.get("favorited")
    if not isinstance(favorited, bool):
        return _error_response("'favorited' must be a boolean", status=400)

    try:
        result = await conn.set_favorite(node_id, favorited)
        return _json_response(result)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error setting favorite (node=%s): %s", node_id, exc)
        return _error_response("Failed to set favorite")


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
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to relay track-node request to integration (node=%s): %s",
            node_id, exc,
        )
        return _error_response("Failed to reach NodePulse integration")


# ---------------------------------------------------------------------------
# Route: GET /api/packets
# ---------------------------------------------------------------------------

async def handle_packets(request: web.Request) -> web.Response:
    """
    Return the most recent captured packets from the packet inspector ring buffer.

    Query parameters:
        limit  — max entries to return (default 200, max 500)

    The buffer is populated by every inbound Meshtastic packet received via the
    pubsub listener. Entries are ordered newest first so the UI can slice the
    most recent N without additional sorting.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        limit = min(int(request.rel_url.query.get("limit", 200)), 500)
    except (ValueError, TypeError):
        limit = 200
    try:
        packets = await conn.get_packet_log(limit)
        return _json_response(packets)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching packet log: %s", exc)
        return _error_response("Failed to retrieve packet log")


# ---------------------------------------------------------------------------
# Route: GET /api/sniffer/stats
# ---------------------------------------------------------------------------

async def handle_sniffer_stats(request: web.Request) -> web.Response:
    """
    Return live LoRa sniffer statistics computed over the last 60 seconds.

    Response:
        {
            "packets_per_minute": 62,
            "unique_nodes": 14,
            "total_captured": 320,
            "portnum_distribution": {
                "TEXT_MESSAGE_APP": 38,
                "TELEMETRY_APP": 22,
                ...
            }
        }
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        stats = await conn.get_sniffer_stats()
        return _json_response(stats)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching sniffer stats: %s", exc)
        return _error_response("Failed to retrieve sniffer stats")


# ---------------------------------------------------------------------------
# Route: GET /api/mesh/discovery
# ---------------------------------------------------------------------------

async def handle_mesh_discovery(request: web.Request) -> web.Response:
    """
    Return mesh discovery data computed from the packet log.

    Query params:
        window (int): Time window in seconds (default 300 = 5 min, max 3600)
        limit (int): Max nodes to return (default 100)

    Response:
        {
            "nodes": [
                {
                    "node_id": "!abcd1234",
                    "short_name": "NODE1",
                    "long_name": "Node One",
                    "last_seen": 1699900000,
                    "packet_count": 42,
                    "channels": [0, 1],
                    "portnums": ["TEXT_MESSAGE_APP", "TELEMETRY_APP"],
                    "best_snr": 12.5,
                    "worst_snr": -5.2,
                    "avg_snr": 4.3,
                    "best_rssi": -45,
                    "worst_rssi": -110,
                    "avg_hop_limit": 2.5,
                    "is_direct": true,
                    "via_mqtt": false
                },
                ...
            ],
            "window_seconds": 300,
            "total_packets_analyzed": 1500
        }
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        window = min(max(int(request.query.get("window", "300")), 60), 3600)
        limit = min(max(int(request.query.get("limit", "100")), 1), 500)
        data = await conn.get_mesh_discovery(window, limit)
        return _json_response(data)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching mesh discovery: %s", exc)
        return _error_response("Failed to retrieve mesh discovery")


# ---------------------------------------------------------------------------
# Routes: /api/device-config
# ---------------------------------------------------------------------------

async def handle_get_device_config(request: web.Request) -> web.Response:
    """
    Return the full device configuration as a JSON snapshot.

    The response is shaped by the section registry in device_config.py and
    includes all Config + ModuleConfig sections plus a pseudo 'owner' section.
    Returns 503 when the node is not connected.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        config_data = await conn.get_device_config()
        return _json_response(config_data)
    except ConnectionError as exc:
        return _error_response(str(exc), status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading device config: %s", exc)
        return _error_response(f"Failed to read device configuration: {exc}")


async def handle_put_device_config_section(request: web.Request) -> web.Response:
    """
    Patch one config section on the connected node.

    Path parameter:
        section — one of the registry section names (device, lora, position,
                  power, display, network, bluetooth, telemetry, neighbor_info,
                  mqtt, canned_message, store_forward) or 'owner'.

    Body: a partial dict of field → value pairs.  For dangerous changes
    (role → ROUTER, lora tx_enabled → false) the body must also include
    ``"confirm": true`` or the request is rejected with HTTP 400.

    Returns:
        { applied: true, section: str, reboot_required: bool }
    """
    conn: MeshtasticConnection = request.app["connection"]
    section = request.match_info.get("section", "").strip()
    if not section:
        return _error_response("section is required in the URL path", status=400)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)

    if not isinstance(body, dict) or not body:
        return _error_response("Request body must be a non-empty JSON object", status=400)

    try:
        result = await conn.set_device_config(section, body)
        return _json_response(result)
    except ValueError as exc:
        # Validation errors from the registry (unknown section/field, out of range, etc.)
        return _error_response(str(exc), status=400)
    except ConnectionError as exc:
        return _error_response(str(exc), status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error writing device config section %s: %s", section, exc)
        return _error_response("Failed to apply configuration")


async def handle_reload_device_config(request: web.Request) -> web.Response:
    """
    Force a requestConfig() call to refresh the in-memory config from the radio.
    Used by the Configuration view's 'Refresh' button.

    Returns { reloaded: true } on success, 503 if not connected.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        reloaded, reason = await conn.reload_device_config()
        if reloaded:
            return _json_response({"reloaded": True})
        if reason == "not_connected":
            return _error_response("Node is not connected", status=503)
        if reason.startswith("request_failed"):
            return _error_response(f"Config reload failed: {reason}", status=503)
        return _error_response(f"Config reload failed: {reason}", status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reloading device config: %s", exc)
        return _error_response("Failed to reload device configuration")


# ---------------------------------------------------------------------------
# Route: GET /api/security/scan
# ---------------------------------------------------------------------------

async def handle_get_security_scan(request: web.Request) -> web.Response:
    """
    Inspect every configured channel's PSK and return a list of security
    findings — severity classification, human-readable reason, and duplicate-
    key detection.

    Response shape:
        {
          "findings": [
            {
              "channel_index": 0,
              "channel_name":  "Primary",
              "severity":      "weak",
              "reason":        "Using the Meshtastic default key …",
              "duplicate_of":  null
            },
            …
          ],
          "has_issues": true,
          "scanned_at": 1723571234
        }

    Returns 503 when the node is not connected.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        result = await conn.get_security_scan()
        return _json_response(result)
    except ConnectionError as exc:
        return _error_response(str(exc), status=503)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error running security scan: %s", exc)
        return _error_response("Security scan failed")


# ---------------------------------------------------------------------------
# Route: GET /api/terrain/elevation
# ---------------------------------------------------------------------------

async def handle_terrain_elevation(request: web.Request) -> web.Response:
    """Return ground elevation (m) for a single lat/lng point.

    Query params: lat, lng. Returns { lat, lng, elevation_m } — elevation_m is
    null when the DEM source could not be reached.
    """
    terrain: TerrainService = request.app.get("terrain")
    if terrain is None:
        return _error_response("Terrain service is not available", status=503)
    try:
        lat = float(request.query.get("lat", ""))
        lng = float(request.query.get("lng", ""))
    except ValueError:
        return _error_response("'lat' and 'lng' query params must be numbers", status=400)

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return _error_response("'lat' and 'lng' must be valid coordinates", status=400)

    try:
        elevation = await terrain.get_elevation(lat, lng)
    except Exception as exc:  # noqa: BLE001
        logger.error("Terrain elevation lookup failed: %s", exc)
        return _error_response("Terrain elevation lookup failed", status=502)
    return _json_response({"lat": lat, "lng": lng, "elevation_m": elevation})


# ---------------------------------------------------------------------------
# Route: POST /api/terrain/coverage
# ---------------------------------------------------------------------------

async def handle_terrain_coverage(request: web.Request) -> web.Response:
    """Analyse radio coverage radially from a point."""
    from .terrain import TerrainService, analyze_coverage
    terrain: TerrainService = request.app.get("terrain")
    if terrain is None:
        return _error_response("Terrain analysis is not enabled", status=503)
    try:
        body = await request.json()
        lat = float(body["lat"])
        lng = float(body["lng"])
        radius_m = float(body.get("radius_m", 5000))
        freq_mhz = float(body.get("freq_mhz", 915.0))
        tx_power_dbm = float(body.get("tx_power_dbm", 10.0))
        tx_gain_dbi = float(body.get("tx_gain_dbi", 2.1))
        rx_gain_dbi = float(body.get("rx_gain_dbi", 2.1))
        rx_sensitivity_dbm = float(body.get("rx_sensitivity_dbm", -137.0))
        tx_antenna_height_m = float(body.get("tx_antenna_height_m", 2.0))
        rx_antenna_height_m = float(body.get("rx_antenna_height_m", 2.0))
        env_loss_db = float(body.get("env_loss_db", 0.0))
        radial_count = int(body.get("radial_count", 72))
        samples_per_radial = int(body.get("samples_per_radial", 30))
        
        res = await analyze_coverage(
            terrain, lat, lng, radius_m, freq_mhz, tx_power_dbm, tx_gain_dbi, rx_gain_dbi,
            rx_sensitivity_dbm, tx_antenna_height_m, rx_antenna_height_m, env_loss_db,
            radial_count=radial_count, samples_per_radial=samples_per_radial
        )
        return _json_response(res)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Terrain coverage error: %s", exc, exc_info=True)
        return _error_response(f"Coverage analysis failed: {exc}")


# ---------------------------------------------------------------------------
# Route: POST /api/terrain/link
# ---------------------------------------------------------------------------

async def handle_terrain_link(request: web.Request) -> web.Response:
    """Analyse a point-to-point radio link over terrain.

    Expected JSON body:
        {
          "from":     { "lat": -29.85, "lng": 31.02 },  // required
          "to":       { "lat": -29.86, "lng": 31.05 },  // required
          "frequency_mhz": 915,                          // required
          "tx_power_dbm": 10,                            // default 0
          "tx_gain_dbi": 2.1,                            // default 0
          "rx_gain_dbi": 2.1,                            // default 0
          "rx_sensitivity_dbm": -137,                    // default -137
          "cable_loss_db": 0.5,                          // default 0
          "tx_antenna_height_m": 2.0,                    // default 2
          "rx_antenna_height_m": 2.0,                    // default 2
          "samples": 48,                                 // default 48, max 128
          "k_factor": 1.333                              // default 4/3
        }

    Returns the full link analysis from app/terrain.py (profile, LOS/Fresnel
    verdicts, link budget). Individual points without elevation still appear
    in the profile with elevation_m: null so the UI can render a partial path.
    """
    terrain: TerrainService = request.app.get("terrain")
    if terrain is None:
        return _error_response("Terrain service is not available", status=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)
    if not isinstance(body, dict):
        return _error_response("Request body must be a JSON object", status=400)

    from_pt = body.get("from")
    to_pt = body.get("to")
    if not isinstance(from_pt, dict) or not isinstance(to_pt, dict):
        return _error_response("'from' and 'to' must be objects with 'lat'/'lng'", status=400)
    try:
        lat1 = float(from_pt.get("lat"))
        lng1 = float(from_pt.get("lng"))
        lat2 = float(to_pt.get("lat"))
        lng2 = float(to_pt.get("lng"))
    except (TypeError, ValueError):
        return _error_response("'from'/'to' lat/lng must be numbers", status=400)

    if not (-90.0 <= lat1 <= 90.0) or not (-90.0 <= lat2 <= 90.0):
        return _error_response("Latitude must be within [-90, 90]", status=400)
    if not (-180.0 <= lng1 <= 180.0) or not (-180.0 <= lng2 <= 180.0):
        return _error_response("Longitude must be within [-180, 180]", status=400)
    if (lat1, lng1) == (lat2, lng2):
        return _error_response("'from' and 'to' must be different points", status=400)

    try:
        freq_mhz = float(body.get("frequency_mhz"))
    except (TypeError, ValueError):
        return _error_response("'frequency_mhz' must be a number", status=400)
    if not (1 <= freq_mhz <= 60000):
        return _error_response("'frequency_mhz' must be within [1, 60000]", status=400)

    def _opt_float(key: str, default: float) -> float:
        try:
            value = body.get(key, default)
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            raise ValueError(key) from None

    try:
        tx_power_dbm = _opt_float("tx_power_dbm", 0.0)
        tx_gain_dbi = _opt_float("tx_gain_dbi", 0.0)
        rx_gain_dbi = _opt_float("rx_gain_dbi", 0.0)
        rx_sensitivity_dbm = _opt_float("rx_sensitivity_dbm", -137.0)
        cable_loss_db = _opt_float("cable_loss_db", 0.0)
        tx_antenna_height_m = _opt_float("tx_antenna_height_m", 2.0)
        rx_antenna_height_m = _opt_float("rx_antenna_height_m", 2.0)
        k_factor = _opt_float("k_factor", 4 / 3)
    except ValueError as exc:
        return _error_response(f"'{exc.args[0]}' must be a number", status=400)

    try:
        clutter_height_m = _opt_float("clutter_height_m", 0)
    except ValueError as exc:
        return _error_response(f"'{exc.args[0]}' must be a number", status=400)

    samples = int(body.get("samples", 48))
    samples = max(2, min(samples, 128))

    try:
        elevations = await terrain.sample_path(lat1, lng1, lat2, lng2, samples)
    except Exception as exc:  # noqa: BLE001
        logger.error("Terrain path sampling failed: %s", exc)
        return _error_response("Terrain elevation lookup failed", status=502)

    # Fill gaps in the profile: terrain APIs occasionally drop a sample.
    # Linear interpolation keeps the LOS/Fresnel geometry sane for the UI.
    filled = _interpolate_nones(elevations)
    if any(v is None for v in filled):
        return _error_response(
            "Terrain elevation unavailable along this path — check network/DEM source",
            status=502,
        )

    result = analyze_link(
        from_point={"lat": lat1, "lng": lng1},
        to_point={"lat": lat2, "lng": lng2},
        freq_mhz=freq_mhz,
        elevations=filled,
        tx_power_dbm=tx_power_dbm,
        tx_gain_dbi=tx_gain_dbi,
        rx_gain_dbi=rx_gain_dbi,
        rx_sensitivity_dbm=rx_sensitivity_dbm,
        cable_loss_db=cable_loss_db,
        tx_antenna_height_m=tx_antenna_height_m,
        rx_antenna_height_m=rx_antenna_height_m,
        k_factor=k_factor,
        clutter_height_m=clutter_height_m,
    )
    return _json_response(result)


# ---------------------------------------------------------------------------
# Routes: /api/admin — remote node administration
# ---------------------------------------------------------------------------

async def handle_admin_available(request: web.Request) -> web.Response:
    """
    Report whether the gateway can administer remote nodes (has an ADMIN
    channel) and which admin actions are supported.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        return _json_response(await conn.remote_admin_available())
    except Exception as exc:  # noqa: BLE001
        logger.error("Error checking remote admin availability: %s", exc)
        return _error_response("Failed to check remote admin availability")


async def handle_get_remote_config(request: web.Request) -> web.Response:
    """
    Read a remote node's full configuration over the admin channel.

    Path parameter:
        node_id — the remote node's canonical "!hex" ID.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    try:
        force = request.query.get("force", "false").lower() == "true"
        return _json_response(await conn.get_remote_config(node_id, force=force))
    except ConnectionError as exc:
        return _error_response(str(exc), status=504)
    except asyncio.TimeoutError:
        return _error_response("Remote config read timed out — the node may be offline, out of range, or not authorized (its Security → Admin Keys must include this gateway's public key)", status=504)
    except ValueError as exc:
        return _error_response(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading remote config for %s: %s", node_id, exc)
        return _error_response("Failed to read remote configuration")
        
async def handle_get_remote_config_section(request: web.Request) -> web.Response:
    """
    Read a single configuration section from a remote node.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    section = request.match_info.get("section", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    if not section:
        return _error_response("section is required in the URL path", status=400)
    
    try:
        return _json_response(await conn.get_remote_config_section(node_id, section))
    except ConnectionError as exc:
        return _error_response(str(exc), status=504)
    except asyncio.TimeoutError:
        return _error_response("Remote config section read timed out", status=504)
    except ValueError as exc:
        return _error_response(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading remote config section %s for %s: %s", section, node_id, exc)
        return _error_response("Failed to read remote configuration section")

async def handle_put_remote_config_section(request: web.Request) -> web.Response:
    """
    Patch one config section on a remote node over the admin channel.

    Path parameters:
        node_id — the remote node's canonical "!hex" ID.
        section — one of the registry section names or 'owner'.

    Body: a partial dict of field → value pairs. Dangerous changes (role →
    ROUTER, lora tx_enabled → false) require ``"confirm": true``.
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    section = request.match_info.get("section", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    if not section:
        return _error_response("section is required in the URL path", status=400)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _error_response("Request body must be valid JSON", status=400)

    if not isinstance(body, dict) or not body:
        return _error_response("Request body must be a non-empty JSON object", status=400)

    try:
        result = await conn.set_remote_config(node_id, section, body)
        return _json_response(result)
    except ValueError as exc:
        return _error_response(str(exc), status=400)
    except ConnectionError as exc:
        return _error_response(str(exc), status=504)
    except asyncio.TimeoutError:
        return _error_response("Remote config write timed out — the node may be offline, out of range, or not authorized (its Security → Admin Keys must include this gateway's public key)", status=504)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error writing remote config %s/%s: %s", node_id, section, exc)
        return _error_response("Failed to apply remote configuration")


async def handle_admin_action(request: web.Request) -> web.Response:
    """
    Run a named admin action against a remote node.

    Path parameters:
        node_id — the remote node's canonical "!hex" ID.
        action — reboot | shutdown | factory_reset | factory_reset_device |
                 nodedb_reset | set_fixed_position | clear_fixed_position |
                 set_time | remove_node

    Body: optional params (e.g. ``{"seconds": 5}`` for reboot/shutdown,
    ``{"lat":..., "lng":..., "alt":...}`` for set_fixed_position,
    ``{"target_node_id": "!hex"}`` for remove_node).
    """
    conn: MeshtasticConnection = request.app["connection"]
    node_id = request.match_info.get("node_id", "").strip()
    action = request.match_info.get("action", "").strip()
    if not _NODE_ID_RE.match(node_id):
        return _error_response("node_id must be a valid !hex Meshtastic node ID", status=400)
    if not action:
        return _error_response("action is required in the URL path", status=400)

    body: dict[str, Any] = {}
    if request.body_exists:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _error_response("Request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", status=400)

    try:
        result = await conn.remote_admin_action(node_id, action, body)
        return _json_response(result)
    except ValueError as exc:
        return _error_response(str(exc), status=400)
    except ConnectionError as exc:
        return _error_response(str(exc), status=504)
    except asyncio.TimeoutError:
        return _error_response(f"Admin action '{action}' timed out — the node may be offline, out of range, or not authorized (its Security → Admin Keys must include this gateway's public key)", status=504)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error running admin action %s on %s: %s", action, node_id, exc)
        return _error_response(f"Failed to run admin action '{action}'")


def _interpolate_nones(values: list[float | None]) -> list[float | None]:
    """Linearly fill None gaps in a list of numbers.

    Leading Nones are filled with the first known value (so the profile starts
    at the Tx endpoint); trailing Nones are left as None (Rx endpoint unknown).
    """
    result = list(values)
    known = [(i, v) for i, v in enumerate(result) if v is not None]
    if not known:
        return result

    first_i, first_v = known[0]
    for i in range(first_i):
        result[i] = first_v

    for (i0, v0), (i1, v1) in itertools.pairwise(known):
        span = i1 - i0
        if span <= 1:
            continue
        for i in range(i0 + 1, i1):
            result[i] = v0 + (v1 - v0) * (i - i0) / span
    return result

