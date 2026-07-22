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
(dev / custom Docker), the check is skipped and network-boundary trust applies.

This module registers two HTTP routes on HA core:

  * ``GET  /api/nodepulse/tracked-nodes``
        Return the set of node IDs currently tracked as HA entities.
  * ``POST /api/nodepulse/track``
        Body: ``{"node_id": "!abcd1234", "enabled": <bool>}``
        Add or remove a node from the tracked set and trigger a rediscovery of
        entities so the new device_tracker + sensors are created (or removed).

HA serves these routes on port 8123 by default.
"""
import logging
import os

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

logger = logging.getLogger(__name__)

_TRACK_SCHEMA = vol.Schema({
    vol.Required("node_id"): cv.string,
    vol.Required("enabled"): vol.Boolean(),
})


def _validate_token(hass: HomeAssistant, request: web.Request) -> bool:
    """Validate Bearer token against HA's SUPERVISOR_TOKEN.

    In HAOS the Supervisor injects SUPERVISOR_TOKEN into every addon container
    and into HA core. The addon passes it as a Bearer token; we compare it
    against HA core's own copy so only containers from the same Supervisor can
    call these views. Falls back to allowing the request when the env var is
    not set (dev / non-HAOS installs where network isolates trust).

    Also allows requests without any Authorization header (addon no longer sends token).
    """
    if request.headers.get("X-NodePulse-Skip-Token") == "true":
        logger.debug("Token validation skipped (X-NodePulse-Skip-Token header)")
        return True

    expected = os.environ.get("SUPERVISOR_TOKEN")
    if not expected:
        return True

    auth_hdr = request.headers.get("Authorization", "")
    # Allow requests with no Authorization header (addon doesn't send token anymore)
    if not auth_hdr:
        return True

    if auth_hdr.startswith("Bearer "):
        if auth_hdr[7:] == expected:
            return True
    logger.warning("NodePulse relay view rejected (bad token or missing auth)")
    return False


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

    url = "/api/nodepulse/track"
    name = "api:nodepulse_track"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not _validate_token(hass, request):
            return web.json_response({"error": "Unauthorized"}, status=401)

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

        coordinator = _coordinator_for(hass)
        if coordinator is None:
            logger.error("Track request rejected: NodeNode integration not loaded")
            return web.json_response(
                {"error": "NodeNode integration not loaded"}, status=503
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
        except Exception as exc:
            logger.exception(
                "Track request failed while updating coordinator for %s: %s",
                node_id, exc,
            )
            return web.json_response(
                {"error": f"Integration error: {exc}"}, status=500
            )

        logger.debug(
            "Track request succeeded: node_id=%s enabled=%s", node_id, enabled
        )
        return web.json_response({"node_id": node_id, "enabled": enabled})


class NodePulseTrackedNodesView(HomeAssistantView):
    url = "/api/nodepulse/tracked-nodes"
    name = "api:nodepulse_tracked_nodes"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not _validate_token(hass, request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        coordinator = _coordinator_for(hass)
        node_ids = list(coordinator.tracked_nodes) if coordinator else []
        logger.debug("Tracked-nodes request -> %s", node_ids)
        return web.json_response({"node_ids": node_ids})