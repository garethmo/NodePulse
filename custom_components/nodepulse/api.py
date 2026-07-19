"""
NodePulse — Local API relay for the addon's Web UI.

The NodePulse addon runs as a HA addon (a separate Docker container) reachable
by the Web UI only through HA Ingress. The Web UI cannot register Home Assistant
entities directly — only a loaded integration can. So the Web UI's "Track in HA"
toggle calls the addon's ``/api/track-node`` endpoint, which relays the request
here over localhost:8099.

This module registers two HTTP routes on HA core:

  * ``GET  /api/nodepulse/tracked-nodes``
        Return the set of node IDs currently tracked as HA entities.
  * ``POST /api/nodepulse/track``
        Body: ``{"node_id": "!abcd1234", "enabled": <bool>}``
        Add or remove a node from the tracked set and trigger a rediscovery of
        entities so the new device_tracker + sensors are created (or removed).

HA serves these routes on port 8123 by default, but both the addon and the
integration share the HA host, and the integration's ``run_callback_threadsafe``
relay is what actually performs the work. We expose the routes via a Home
Assistant ``View`` registered in ``__init__.py``.
"""
import logging
from typing import Any, Dict

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_TRACKED_NODES, DOMAIN

logger = logging.getLogger(__name__)

_TRACK_SCHEMA = vol.Schema({
    vol.Required("node_id"): cv.string,
    vol.Required("enabled"): vol.Boolean(),
})


def _coordinator_for(hass: HomeAssistant):
    """Return the first loaded NodePulse coordinator, or None."""
    data = hass.data.get(DOMAIN)
    if not data:
        return None
    # hass.data[DOMAIN] is keyed by entry_id -> coordinator.
    for coordinator in data.values():
        return coordinator
    return None


class NodePulseTrackView(HomeAssistantView):
    """Local relay endpoint for the addon Web UI's per-node track toggle."""

    url = "/api/nodepulse/track"
    name = "api:nodepulse_track"
    # SECURITY NOTE — requires_auth = False:
    # These relay endpoints are only ever called by the NodePulse addon
    # container over the supervisor network (homeassistant:8123 / localhost),
    # never by untrusted browsers — the addon Web UI reaches them indirectly
    # through the addon's own authenticated ingress proxy. We intentionally
    # disable HA auth here because the addon container has no HA user/session
    # to authenticate with, and the standard addon<->core relay pattern relies
    # on the network boundary (addon <-> HA core only) for trust. Do NOT expose
    # HA core's port 8123 to untrusted networks, or these endpoints become
    # reachable without authentication.

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

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

        logger.info(
            "Track request: node_id=%s enabled=%s", node_id, enabled
        )

        coordinator = _coordinator_for(hass)
        if coordinator is None:
            logger.error("Track request rejected: NodePulse integration not loaded")
            return web.json_response(
                {"error": "NodePulse integration not loaded"}, status=503
            )

        try:
            # Update the tracked set on the coordinator and persist it to the
            # config entry options so it survives restarts.
            changed = coordinator.set_tracked_node(node_id, enabled)
            logger.debug(
                "set_tracked_node(%s, %s) -> changed=%s; tracked_nodes=%s",
                node_id, enabled, changed, sorted(coordinator.tracked_nodes),
            )
            if changed:
                await coordinator.persist_tracked_nodes(hass)
                logger.debug("Persisted tracked nodes to config entry options")

                # Trigger a full refresh to ensure latest node data (including GPS)
                # is loaded before discovery runs. This fixes a race condition where
                # device trackers weren't created because GPS data wasn't available yet.
                # Use async_refresh() (not async_config_entry_first_refresh()) because
                # the entry is already LOADED here.
                #
                # IMPORTANT: fire-and-forget. This view is reached by the addon Web UI
                # through the HA ingress proxy, which has a request timeout. A full
                # refresh does 4 sequential GETs against the addon and can block for
                # many seconds (e.g. while probing a dead working-host candidate),
                # causing the proxy to return HTTP 503 even though the change already
                # succeeded. We return 200 immediately and let the refresh run in the
                # background — discovery picks up the new entities on the next poll.
                hass.async_create_task(coordinator.async_refresh())
                logger.debug("Scheduled background coordinator refresh after tracking change")
        except Exception as exc:  # defensive: never return a non-JSON error
            logger.exception(
                "Track request failed while updating coordinator for %s: %s",
                node_id, exc,
            )
            return web.json_response(
                {"error": f"Integration error: {exc}"}, status=500
            )

        logger.info(
            "Track request succeeded: node_id=%s enabled=%s", node_id, enabled
        )
        return web.json_response({"node_id": node_id, "enabled": enabled})


class NodePulseTrackedNodesView(HomeAssistantView):
    """Return the current set of tracked node IDs."""

    url = "/api/nodepulse/tracked-nodes"
    name = "api:nodepulse_tracked_nodes"
    # See NodePulseTrackView for the security rationale behind requires_auth=False.
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator_for(hass)
        node_ids = list(coordinator.tracked_nodes) if coordinator else []
        logger.debug("Tracked-nodes request -> %s", node_ids)
        return web.json_response({"node_ids": node_ids})
