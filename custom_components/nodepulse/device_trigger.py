"""
NodePulse — Device Trigger Platform.

Provides automation triggers for each tracked Meshtastic node device:

  * ``message_received`` — fired when a node receives a text message (as a DM
    addressed to us, or on a channel it participates in).
  * ``message_sent``     — fired when a message is sent from this node.

Triggers are optionally scoped to a channel (``channel``) or direct-message
(``is_dm``) context, mirroring the official Meshtastic integration.

When the coordinator surfaces a new message, the entity listener fires the
underlying HA event that the device-automation framework listens for.
"""
import logging
from typing import Any, Dict, List

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import (
    InvalidDeviceAutomationConfig,
)
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

logger = logging.getLogger(__name__)


def _coordinator_for(hass: HomeAssistant):
    """Return the first loaded NodePulse coordinator, or None."""
    data = hass.data.get(DOMAIN)
    if not data:
        return None
    for coordinator in data.values():
        return coordinator
    return None

_TRIGGER_TYPES = {
    "message_received",
    "message_sent",
    "channel_message.received",
    "traceroute_complete",
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(_TRIGGER_TYPES),
        vol.Optional("channel"): vol.All(int, vol.Range(min=0, max=7)),
        vol.Optional("is_dm"): cv.boolean,
    }
)

# Event fired on the HA bus when a mesh message arrives/is sent. Device
# automation uses this to match triggers to devices.
EVENT_MESH_MESSAGE = f"{DOMAIN}_message"

# Event fired when a new traceroute completes for a node.
EVENT_TRACEROUTE_COMPLETE = f"{DOMAIN}_traceroute_complete"

# Keys attached to the event payload.
EVENT_NODE_ID = "node_id"
EVENT_DIRECTION = "direction"  # "received" | "sent"
EVENT_CHANNEL = "channel"
EVENT_IS_DM = "is_dm"
EVENT_TEXT = "text"
EVENT_FROM_ID = "from_id"


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> List[Dict[str, Any]]:
    """Return the list of triggers supported for the given device."""
    if not _is_mesh_node_device(hass, device_id):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: "message_received",
        },
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: "message_sent",
        },
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: "channel_message.received",
        },
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: "traceroute_complete",
        },
    ]


@callback
def _async_get_node_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Resolve the Meshtastic node id that backs the given HA device."""
    reg = dr.async_get(hass)
    device = reg.async_get(device_id)
    if device is None:
        return None
    for ident in device.identifiers:
        if ident[0] == DOMAIN:
            return ident[1]
    return None


def _is_mesh_node_device(hass: HomeAssistant, device_id: str) -> bool:
    """Return True if the device maps to an actual mesh node (not the
    integration-level gateway device, whose identifier is a config entry id)."""
    node_id = _async_get_node_id(hass, device_id)
    if not node_id or not node_id.startswith("!"):
        return False
    coordinator = _coordinator_for(hass)
    if coordinator is None:
        return True  # be permissive if coordinator isn't ready yet
    nodes = (coordinator.data or {}).get("nodes", [])
    return any(n.get("id") == node_id for n in nodes)


@callback
def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: Any,
    trigger_info: Any = None,
) -> Any:
    """Attach a device trigger and return its detach callback."""
    node_id = _async_get_node_id(hass, config[CONF_DEVICE_ID])
    if node_id is None:
        raise InvalidDeviceAutomationConfig(f"Unknown NodePulse device {config[CONF_DEVICE_ID]}")

    trigger_type = config[CONF_TYPE]
    want_channel = config.get("channel")
    want_dm = config.get("is_dm")

    if trigger_type == "traceroute_complete":
        @callback
        def _match(event):
            return event.data.get(EVENT_NODE_ID) == node_id

        @callback
        def _handle(event):
            if not _match(event):
                return
            hass.async_create_task(action(event))

        return hass.bus.async_listen(EVENT_TRACEROUTE_COMPLETE, _handle)

    if trigger_type == "channel_message.received":
        # Channel messages are received and never direct messages.
        direction = "received"
        want_dm = False
    else:
        direction = "received" if trigger_type == "message_received" else "sent"

    @callback
    def _match_msg(event):
        payload = event.data
        if payload.get(EVENT_NODE_ID) != node_id:
            return False
        if payload.get(EVENT_DIRECTION) != direction:
            return False
        if want_channel is not None and payload.get(EVENT_CHANNEL) != want_channel:
            return False
        if want_dm is not None and payload.get(EVENT_IS_DM) != want_dm:
            return False
        return True

    @callback
    def _handle_msg(event):
        if not _match_msg(event):
            return
        hass.async_create_task(action(event))

    return hass.bus.async_listen(EVENT_MESH_MESSAGE, _handle_msg)


@callback
def async_validate_trigger_config(config: ConfigType) -> ConfigType:
    """Validate a trigger configuration against the schema."""
    return TRIGGER_SCHEMA(config)
