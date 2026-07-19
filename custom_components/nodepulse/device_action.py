"""
NodePulse — Device Action Platform.

Provides automation actions for each tracked Meshtastic node device:

  * ``send_message``     — send a text message to / via the node.
  * ``request_position`` — ask the node to report its GPS fix.
  * ``trace_route``      — dispatch a traceroute towards the node.

These are the UI-friendly equivalents of the integration-level services,
scoped to the device they are invoked from.
"""
import logging
from typing import Any, Dict, List

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_automation import DEVICE_ACTION_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import (
    InvalidDeviceAutomationConfig,
)
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.template import TemplateVarsType

from .const import ATTR_CHANNEL, ATTR_TEXT, DOMAIN
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)


def _coordinator_for(hass: HomeAssistant):
    """Return the first loaded NodePulse coordinator, or None."""
    data = hass.data.get(DOMAIN)
    if not data:
        return None
    for coordinator in data.values():
        return coordinator
    return None

_ACTION_TYPES = {"send_message", "request_position", "trace_route"}

ACTION_SCHEMA = DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(_ACTION_TYPES),
        vol.Optional(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_CHANNEL): vol.All(int, vol.Range(min=0, max=7)),
    }
)


@callback
def _async_get_node_id(hass: HomeAssistant, device_id: str) -> str | None:
    reg = dr.async_get(hass)
    device = reg.async_get(device_id)
    if device is None:
        return None
    for ident in device.identifiers:
        if ident[0] == DOMAIN:
            return ident[1]
    return None


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> List[Dict[str, Any]]:
    """Return the list of actions supported for the given device."""
    node_id = _async_get_node_id(hass, device_id)
    if not node_id or not node_id.startswith("!"):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: t,
        }
        for t in _ACTION_TYPES
    ]


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Any = None,
) -> None:
    """Execute the requested device action."""
    device_id = config[CONF_DEVICE_ID]
    node_id = _async_get_node_id(hass, device_id)
    if node_id is None:
        raise InvalidDeviceAutomationConfig(f"Unknown NodePulse device {device_id}")

    coordinator: NodePulseCoordinator = _coordinator_for(hass)
    if coordinator is None:
        raise InvalidDeviceAutomationConfig("NodePulse integration not loaded")

    action_type = config[CONF_TYPE]
    try:
        if action_type == "send_message":
            text = config.get(ATTR_TEXT) or ""
            if not text:
                logger.warning("NodePulse send_message action called without text")
                return
            await coordinator.async_send_message(
                text, destination=node_id, channel=config.get(ATTR_CHANNEL, 0)
            )
        elif action_type == "request_position":
            await coordinator.async_request_position(node_id)
        elif action_type == "trace_route":
            await coordinator.async_trace_route(node_id)
    except Exception as exc:  # surface to the automation's error log
        logger.error("NodePulse device action %s failed: %s", action_type, exc)


@callback
def async_validate_action_config(config: ConfigType) -> ConfigType:
    """Validate an action configuration against the schema."""
    return ACTION_SCHEMA(config)
