"""
NodePulse — Local API relay for the addon's Web UI.

The NodePulse addon runs as a HA addon (a separate Docker container) reachable
by the Web UI only through HA Ingress. The Web UI cannot register Home Assistant
entities directly — only a loaded integration can. So the Web UI's "Track in HA"
toggle calls the addon's ``/api/track-node`` endpoint, which relays the request
here over the supervisor network.

Addon authentication: the addon passes the SUPERVISOR_TOKEN as a Bearer token
in the Authorization header. This module validates that token against HA core's
own copy of SUPERVISOR_TOKEN. In environments where the token is not set
(dev / custom Docker), requests are rejected unless they carry valid Home
Assistant authentication — these endpoints are never open to anonymous callers.

This module registers two HTTP routes on HA core:

  * ``GET  /api/nodepulse/tracked-nodes``
        Return the set of node IDs currently tracked as HA entities.
  * ``POST /api/nodepulse/track``
        Body: ``{"node_id": "!abcd1234", "enabled": <bool>}``
        Add or remove a node from the tracked set and trigger a rediscovery of
        entities so the new device_tracker + sensors are created (or removed).

HA serves these routes on port 8123 by default.
"""
import inspect
import logging
import os
import secrets

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)

_TRACK_SCHEMA = vol.Schema({
    vol.Required("node_id"): cv.string,
    vol.Required("enabled"): vol.Boolean(),
})


async def _call_auth_check(callable_, *args):
    """Invoke an HA auth helper whether it is sync or async.

    HA's auth helpers are ``@callback`` (synchronous) in some versions and
    ``async def`` in others. ``await``-ing the synchronous form raises
    TypeError, which previously made the fallback auth path silently report
    failure even for a valid long-lived access token. This wrapper calls the
    helper and awaits only when it actually returns an awaitable.
    """
    try:
        result = callable_(*args)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:
        logger.debug(
            "Relay auth check %s raised: %s",
            getattr(callable_, "__name__", repr(callable_)), exc,
        )
        return None


async def _validate_token(hass: HomeAssistant, request: web.Request) -> str | None:
    """Validate the request's relay authentication.

    Returns ``None`` when the request is accepted, otherwise a short reason
    string describing why it was rejected. The reason is surfaced in the 401
    response body so the addon relay can log exactly what failed (this view
    runs on HA core, so the addon cannot otherwise see why a token was
    rejected).
    """
    expected = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer "):].strip() if auth_hdr.startswith("Bearer ") else ""

    if expected and bearer and secrets.compare_digest(bearer, expected):
        return None

    # Accept valid Home Assistant authentication (session cookie, long-lived
    # access token, etc.) as a second legitimate path. This keeps the relay
    # working even when SUPERVISOR_TOKEN is present on HA core but missing or
    # mismatched on the addon container. These endpoints are still never open
    # to anonymous callers.
    header_check = hasattr(getattr(hass, "http", None), "auth") and \
        hasattr(hass.http.auth, "async_validate_auth_header")
    access_check = hasattr(getattr(hass, "auth", None), "async_validate_access_token")

    header_user = await _call_auth_check(
        hass.http.auth.async_validate_auth_header, request
    ) if header_check else None
    if header_user is not None:
        return None

    # Some HA versions validate the raw Bearer token through the auth manager
    # directly; fall back to that if the header-based path rejected it.
    token_user = await _call_auth_check(
        hass.auth.async_validate_access_token, bearer
    ) if access_check and bearer else None
    if token_user is not None:
        return None

    reason = (
        f"supervisor_token={'set' if expected else 'unset'} "
        f"bearer={'yes' if bearer else 'no'} "
        f"bearer_len={len(bearer)} bearer_head={bearer[:4] or '-'} bearer_tail={bearer[-4:] or '-'} "
        f"header_check={'yes' if header_check else 'missing'} "
        f"access_check={'yes' if access_check else 'missing'} "
        f"header_auth={bool(header_user)} access_token_auth={bool(token_user)}"
    )
    logger.warning("NodePulse relay view rejected: %s", reason)
    return reason


def _coordinator_for(hass: HomeAssistant):
    """Return the first loaded NodePulse coordinator, or None."""
    data = hass.data.get(DOMAIN)
    if not data:
        return None
    for coordinator in data.values():
        return coordinator
    return None


class NodePulseTrackView(HomeAssistantView):
    """Local relay endpoint for the addon Web UI's per-node track toggle."""

    def __init__(self, entry_id: str) -> None:
        """Initialize the view."""
        super().__init__()
        self.entry_id = entry_id

    url = "/api/nodepulse/track"
    name = "api:nodepulse_track"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        reason = await _validate_token(hass, request)
        if reason:
            return web.json_response({"error": "Unauthorized", "reason": reason}, status=401)

        try:
            body = await request.json()
        except Exception:
            logger.warning("Track request received invalid JSON body")
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        try:
            body = _TRACK_SCHEMA(body)
        except vol.Invalid as exc:
            logger.warning("Track request failed schema validation: %s", exc)
            return web.json_response({"error": str(exc)}, status=400)

        node_id = body["node_id"].strip()
        enabled = body["enabled"]

        logger.debug("Track request: node_id=%s enabled=%s", node_id, enabled)

        coordinator = self._get_coordinator(hass)
        if coordinator is None:
            logger.error("Track request rejected: NodePulse integration not loaded for entry %s", self.entry_id)
            return web.json_response(
                {"error": "NodePulse integration not loaded"}, status=503
            )

        try:
            changed = coordinator.set_tracked_node(node_id, enabled)
            logger.debug(
                "set_tracked_node(%s, %s) -> changed=%s; tracked_nodes=%s",
                node_id, enabled, changed, sorted(coordinator.tracked_nodes),
            )
            if changed:
                await coordinator.persist_tracked_nodes(hass)
                logger.debug("Persisted tracked nodes to config entry options")
                hass.async_create_task(coordinator.async_refresh())
                logger.debug("Scheduled background coordinator refresh after tracking change")
        except ValueError as err:
            logger.warning(
                "Track request failed validation for %s: %s",
                node_id, err,
            )
            return web.json_response(
                {"error": "Invalid request"}, status=400
            )
        except HomeAssistantError as err:
            logger.error(
                "Track request failed with Home Assistant error for %s: %s",
                node_id, err,
            )
            return web.json_response(
                {"error": "Home Assistant error"}, status=500
            )
        except UpdateFailed as err:
            logger.error(
                "Track request failed to update coordinator for %s: %s",
                node_id, err,
            )
            return web.json_response(
                {"error": "Unable to update coordinator"}, status=503
            )
        except Exception:
            logger.exception(
                "Track request failed while updating coordinator for %s",
                node_id,
            )
            return web.json_response(
                {"error": "Integration error"}, status=500
            )

        logger.debug(
            "Track request succeeded: node_id=%s enabled=%s", node_id, enabled
        )
        return web.json_response({"node_id": node_id, "enabled": enabled})

    def _get_coordinator(self, hass: HomeAssistant) -> NodePulseCoordinator | None:
        """Get the coordinator for this view's config entry."""
        data = hass.data.get(DOMAIN)
        if not data:
            return None
        return data.get(self.entry_id)


class NodePulseTrackedNodesView(HomeAssistantView):
    def __init__(self, entry_id: str) -> None:
        """Initialize the view."""
        super().__init__()
        self.entry_id = entry_id

    url = "/api/nodepulse/tracked-nodes"
    name = "api:nodepulse_tracked_nodes"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        reason = await _validate_token(hass, request)
        if reason:
            return web.json_response({"error": "Unauthorized", "reason": reason}, status=401)
        coordinator = self._get_coordinator(hass)
        node_ids = list(coordinator.tracked_nodes) if coordinator else []
        logger.debug("Tracked-nodes request -> %s", node_ids)
        return web.json_response({"node_ids": node_ids})

    def _get_coordinator(self, hass: HomeAssistant) -> NodePulseCoordinator | None:
        """Get the coordinator for this view's config entry."""
        data = hass.data.get(DOMAIN)
        if not data:
            return None
        return data.get(self.entry_id)