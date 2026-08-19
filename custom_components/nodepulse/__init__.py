"""
NodePulse — Home Assistant Custom Integration.

Entry points called by HA Core:
  - async_setup_entry: Called when a ConfigEntry is loaded. Creates the
    coordinator and forwards setup to each platform, registers the notify
    service, integration-level service actions, and the message event/logbook
    listener.
  - async_unload_entry: Called when the user removes the integration. Unloads
    all platforms and cancels the coordinator's polling task.
"""
import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ATTR_CHANNEL,
    ATTR_TARGET,
    ATTR_TEXT,
    CONF_IGNORED_NODES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_NODES,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import NodePulseCoordinator
from .api import NodePulseTrackView, NodePulseTrackedNodesView
from .helpers import coordinator_for

from .device_trigger import (
    EVENT_MESH_MESSAGE,
    EVENT_TRACEROUTE_COMPLETE,
    EVENT_CHANNEL,
    EVENT_DIRECTION,
    EVENT_FROM_ID,
    EVENT_IS_DM,
    EVENT_NODE_ID,
    EVENT_TEXT,
)

logger = logging.getLogger(__name__)

# Service action schemas ------------------------------------------------------
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_REQUEST_POSITION = "request_position"
SERVICE_TRACE_ROUTE = "trace_route"

_NODE_ID = cv.string

_SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_TARGET): _NODE_ID,
        vol.Optional(ATTR_CHANNEL, default=0): vol.All(
            int, vol.Range(min=0, max=7)
        ),
    }
)

_REQUEST_POSITION_SCHEMA = vol.Schema({vol.Required(ATTR_TARGET): _NODE_ID})

_TRACE_ROUTE_SCHEMA = vol.Schema({vol.Required(ATTR_TARGET): _NODE_ID})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the NodePulse integration (integration level, once).

    Service actions are registered here — not in ``async_setup_entry`` — so they
    remain available across config-entry (un)loads and work correctly when
    multiple NodePulse addons (config entries) are configured. Each handler
    resolves the target coordinator at call time.

    The HTTP relay views are also registered exactly once here (Q3). Their URL
    and name are fixed, so registering per config entry would collide when an
    entry is reloaded or a second entry is added. The views resolve the
    coordinator at request time instead of being bound to a specific entry.
    """
    async def _send_message(call):
        coordinator = coordinator_for(hass)
        if coordinator is None:
            raise HomeAssistantError("NodePulse is not configured")
        target = call.data.get(ATTR_TARGET)
        await coordinator.async_send_message(
            call.data[ATTR_TEXT],
            destination=target,
            channel=call.data.get(ATTR_CHANNEL, 0),
        )

    async def _request_position(call):
        coordinator = coordinator_for(hass)
        if coordinator is None:
            raise HomeAssistantError("NodePulse is not configured")
        await coordinator.async_request_position(call.data[ATTR_TARGET])

    async def _trace_route(call):
        coordinator = coordinator_for(hass)
        if coordinator is None:
            raise HomeAssistantError("NodePulse is not configured")
        await coordinator.async_trace_route(call.data[ATTR_TARGET])

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_MESSAGE, _send_message, schema=_SEND_MESSAGE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REQUEST_POSITION, _request_position,
        schema=_REQUEST_POSITION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TRACE_ROUTE, _trace_route, schema=_TRACE_ROUTE_SCHEMA
    )

    hass.http.register_view(NodePulseTrackView())
    hass.http.register_view(NodePulseTrackedNodesView())

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up NodePulse from a config entry.
    """
    # Clean up any invalid options that may have been added (e.g., addon-specific fields)
    # This can happen if addon config was incorrectly merged with integration config.
    allowed_keys = {CONF_SCAN_INTERVAL, CONF_IGNORED_NODES, CONF_TRACKED_NODES}
    if entry.options:
        current_options = dict(entry.options)
        cleaned_options = {k: v for k, v in current_options.items() if k in allowed_keys}
        if len(cleaned_options) != len(current_options):
            logger.info(
                "Cleaning invalid options from NodePulse config entry %s: removed %s",
                entry.entry_id,
                set(current_options.keys()) - allowed_keys
            )
            hass.config_entries.async_update_entry(entry, options=cleaned_options)

    coordinator = NodePulseCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        # Don't let a transient addon connectivity failure take down the whole
        # entry. The coordinator keeps retrying on its refresh schedule, and
        # the relay views (registered once in async_setup) return 503 until
        # data becomes available.
        logger.warning(
            "NodePulse initial coordinator refresh failed (entry=%s): %s",
            entry.entry_id, exc,
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Forward to all platform modules (binary_sensor, sensor, device_tracker,
    # geo_location, notify). The notify platform is now a real config-entry
    # platform so its entities are tracked and unloaded with the entry (Q2).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for new mesh messages: fire device-trigger events and write
    # logbook entries.
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _on_data_update(hass, coordinator, entry)
        )
    )

    # Wire up options-change listener so updating scan_interval or ignored_nodes
    # via the options flow triggers a reload without requiring a manual restart.
    # (B5: this was defined but never registered, so options changes had no effect.)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    logger.debug("NodePulse integration set up (entry_id=%s)", entry.entry_id)
    return True


def _self_node_id(status) -> str | None:
    """Return the canonical ``!hex`` self/gateway node id, or None.

    The addon reports ``my_node_num`` as an int, but a malformed payload (or a
    legacy addon that serialised it as a string) would otherwise crash the whole
    event-listener callback on ``int()`` (Q16). Coerce defensively and fall
    back to None so a bad value can never break message processing.
    """
    my_info = (status or {}).get("my_info") or {}
    my_num = my_info.get("my_node_num")
    if my_num is None:
        return None
    try:
        return "!" + format(int(my_num) & 0xFFFFFFFF, "08x")
    except (TypeError, ValueError):
        return None


def _on_data_update(hass: HomeAssistant, coordinator: NodePulseCoordinator, entry: ConfigEntry) -> None:
    """React to a coordinator refresh: surface new messages and events."""
    data = coordinator.data or {}

    # Fire traceroute-complete events for newly discovered routes.
    new_traceroutes: list = data.get("new_traceroutes") or []
    for nid in new_traceroutes:
        hass.bus.async_fire(
            EVENT_TRACEROUTE_COMPLETE,
            {EVENT_NODE_ID: nid},
        )

    new_messages = data.get("new_messages") or []
    if not new_messages:
        return

    self_id = _self_node_id(data.get("status"))

    for msg in new_messages:
        try:
            _handle_new_message(hass, entry, msg, self_id)
        except Exception:  # noqa: BLE001 - one bad message must not kill the listener
            logger.exception("NodePulse failed to process a new mesh message: %r", msg)


def _handle_new_message(hass, entry, msg, self_id) -> None:
    """Process a single new mesh message into events + logbook entries (Q5)."""
    from_id = msg.get("from_id")
    to_id = msg.get("to_id")
    is_outgoing = (from_id == self_id) if self_id else bool(msg.get("outgoing"))

    if is_outgoing:
        # A sent message is attributed to the gateway (local) device,
        # matching the official integration where gateway nodes expose the
        # richer "sent" triggers. Fall back to the recipient for DMs.
        node_id = self_id or to_id
        direction = "sent"
    else:
        # A received message is attributed to its sender's device.
        node_id = from_id
        direction = "received"

    hass.bus.async_fire(
        EVENT_MESH_MESSAGE,
        {
            EVENT_NODE_ID: node_id,
            EVENT_DIRECTION: direction,
            EVENT_CHANNEL: msg.get("channel", 0),
            EVENT_IS_DM: bool(msg.get("is_dm")),
            EVENT_TEXT: msg.get("text", ""),
            EVENT_FROM_ID: from_id,
        },
    )

    _logbook_message(hass, entry, msg, node_id, direction)


def _logbook_message(
    hass: HomeAssistant, entry: ConfigEntry, msg, node_id, direction
) -> None:
    """Write a mesh message into the HA logbook.

    Fires a ``logbook_entry`` event — the convention the core logbook
    integration listens for — so the message shows up in the logbook timeline.
    """
    name = msg.get("from_name") or node_id or "Mesh"
    if direction == "sent":
        message = f"Sent to {node_id or 'mesh'}: {msg.get('text', '')}"
    else:
        message = f"Received from {name}: {msg.get('text', '')}"
    hass.bus.async_fire(
        "logbook_entry",
        {
            "name": "NodePulse",
            "message": message,
            "entity_id": None,
            "domain": DOMAIN,
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources.

    ``notify`` is now a regular entry-tracked platform (part of ``PLATFORMS``),
    so ``async_unload_platforms`` unloads it like any other platform — there is
    no separate legacy load path to track anymore (Q2/Q9).
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # When the last config entry is gone, remove the integration-level service
    # actions registered in async_setup so they don't linger after removal.
    if not hass.data.get(DOMAIN):
        for svc in (SERVICE_SEND_MESSAGE, SERVICE_REQUEST_POSITION, SERVICE_TRACE_ROUTE):
            if hass.services.has_service(DOMAIN, svc):
                hass.services.async_remove(DOMAIN, svc)

    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when the user updates options."""
    await hass.config_entries.async_reload(entry.entry_id)
