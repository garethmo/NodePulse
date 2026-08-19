"""
NodePulse — Binary Sensor Platform.

Registers binary sensors:
  - nodepulse.connection_status — one per integration entry, True when the
    addon reports a live connection to the Meshtastic node.
  - Per tracked node "Online" status — True when the node has been heard
    recently (last_heard within a staleness threshold), giving per-node
    online/offline automations.

This entity is the primary health signal for automations (e.g., notify if
the mesh goes offline for more than N minutes).
"""
import logging
from time import time
from typing import Any, Dict, Optional

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
from .helpers import NodeDiscovery, as_float

logger = logging.getLogger(__name__)

# A node is considered "online" if it was last heard within this many seconds.
NODE_ONLINE_THRESHOLD = 3 * 60 * 60  # 3 hours


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the connection status and per-node online binary sensors."""
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Global connection status sensor (always present).
    async_add_entities([NodePulseConnectionSensor(coordinator, entry)])

    # Per-node "online" sensors via the shared discovery helper (Q12).
    discovery = NodeDiscovery(coordinator, entry)
    discovery.attach(
        hass,
        async_add_entities,
        should_create=lambda node: True,
        make_entities=lambda node: NodeOnlineSensor(coordinator, entry, node["id"]),
    )


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
        status = (self.coordinator.data or {}).get("status", {})
        return bool(status.get("connected", False))


class NodeOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Per-node online/offline status based on last-heard timestamp."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Online"

    def __init__(
        self,
        coordinator: NodePulseCoordinator,
        entry: ConfigEntry,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_online"
        # Group under the same per-node device as the sensors.
        node = coordinator.get_node(node_id)
        name = node_id
        if node:
            long_n = node.get("long_name")
            short = node.get("short_name")
            if long_n and short:
                name = f"{long_n} ({short})"
            elif long_n or short:
                name = long_n or short
        self._attr_device_info = {
            "identifiers": {(DOMAIN, node_id)},
            "name": name,
            "manufacturer": "Meshtastic",
            "model": node.get("hw_model") if node else "Meshtastic Node",
            "via_device": (DOMAIN, entry.entry_id),
        }

    def _get_node(self) -> Optional[Dict[str, Any]]:
        return self.coordinator.get_node(self._node_id)

    @property
    def is_on(self) -> bool:
        """Return True when the node was heard within the online threshold."""
        node = self._get_node()
        if not node:
            return False
        last_heard = as_float(node.get("last_heard"))
        if last_heard is None:
            return False

        return (time() - last_heard) <= NODE_ONLINE_THRESHOLD

    @property
    def available(self) -> bool:
        return super().available and self._get_node() is not None
