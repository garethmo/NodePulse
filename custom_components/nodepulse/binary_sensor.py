"""
NodePulse — Binary Sensor Platform.

Registers one binary sensor per integration entry:
  - nodepulse.connection_status — True when the addon reports a live
    connection to the Meshtastic node, False when disconnected.

This entity is the primary health signal for automations (e.g., notify if
the mesh goes offline for more than N minutes).
"""
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the connection status binary sensor for this config entry."""
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NodePulseConnectionSensor(coordinator, entry)])


class NodePulseConnectionSensor(CoordinatorEntity, BinarySensorEntity):
    """
    Binary sensor representing the live connection to the Meshtastic node.

    Inherits CoordinatorEntity so HA automatically calls async_write_ha_state()
    whenever the coordinator updates — no manual subscription needed.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Connection"

    def __init__(self, coordinator: NodePulseCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        # Stable unique_id prevents entity duplication across restarts.
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "NodePulse",
            "manufacturer": "NodePulse",
            "model": "Meshtastic Monitor",
        }

    @property
    def is_on(self) -> bool:
        """Return True when the addon reports an active connection."""
        status = self.coordinator.data.get("status", {})
        return bool(status.get("connected", False))
