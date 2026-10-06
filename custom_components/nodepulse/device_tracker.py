"""
NodePulse — Device Tracker Platform.

Registers a device_tracker entity for every tracked node. When a node has a
valid GPS fix, HA plots it on the native map card alongside other tracked
devices. Nodes without a fix are registered but report an unknown location,
so they appear on the map the moment their first position update arrives
without needing a re-discovery cycle.
"""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NodePulseCoordinator
from .helpers import NodeDiscovery

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Dynamic tracker discovery — shared NodeDiscovery helper (Q12).

    A device_tracker entity is created for every tracked node regardless of
    whether it has a GPS fix at discovery time. Coordinates are populated (and
    updated) on every coordinator refresh. When no fix is available, latitude
    and longitude are None and HA reports the device as an unknown location —
    the entity is already registered, so it will appear on the map immediately
    once a GPS fix arrives without requiring a new discovery cycle.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    discovery = NodeDiscovery(coordinator, entry)
    discovery.attach(
        hass,
        async_add_entities,
        should_create=lambda node: True,
        make_entities=lambda node: [NodeTracker(coordinator, entry, node["id"])],
    )


class NodeTracker(CoordinatorEntity, TrackerEntity):
    """
    Device tracker entity for one Meshtastic node.

    Reports latitude, longitude, and altitude from the node's last known
    GPS fix. HA will plot this on the map card automatically.
    """

    _attr_source_type = SourceType.GPS
    _attr_has_entity_name = True
    _attr_name = "Location"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(
        self,
        coordinator: NodePulseCoordinator,
        entry: ConfigEntry,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_tracker"

        # Resolve a human-readable name from the coordinator's latest data.
        # Falls back to the hex ID if node data isn't loaded yet.
        name = f"Mesh Node {node_id}"
        model = "Meshtastic Node"
        node = coordinator.get_node(node_id)
        if node:
            short = node.get("short_name")
            long_n = node.get("long_name")
            if short and long_n:
                name = f"{long_n} ({short})"
            elif short or long_n:
                name = short or long_n
            hw = node.get("hw_model")
            if hw:
                model = hw

        self._attr_device_info = {
            "identifiers": {(DOMAIN, node_id)},
            "name": name,
            "manufacturer": "Meshtastic",
            "model": model,
            "via_device": (DOMAIN, entry.entry_id),
        }

        # Initialize coordinates from coordinator snapshot on creation so HA
        # has valid state before the first update callback fires.
        if node:
            self._attr_latitude = node.get("latitude")
            self._attr_longitude = node.get("longitude")
        else:
            self._attr_latitude = None
            self._attr_longitude = None

    def _get_node(self) -> Optional[Dict[str, Any]]:
        return self.coordinator.get_node(self._node_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        node = self._get_node()
        if node:
            self._attr_latitude = node.get("latitude")
            self._attr_longitude = node.get("longitude")
        else:
            self._attr_latitude = None
            self._attr_longitude = None
        super()._handle_coordinator_update()

    @property
    def location_accuracy(self) -> int:
        """
        GPS accuracy in metres. Meshtastic does not expose horizontal accuracy
        so we return a fixed reasonable value. HA requires this to be an int.
        """
        return 10

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Expose altitude and node metadata as extra attributes on the entity."""
        node = self._get_node()
        if not node:
            return {}
        return {
            "altitude":          node.get("altitude"),
            "snr":               node.get("snr"),
            "rssi":              node.get("rssi"),
            "hops_away":         node.get("hops_away"),
            "hw_model":          node.get("hw_model"),
            "short_name":        node.get("short_name"),
            "last_position_fix": node.get("last_position_fix"),
            "stale":             node.get("stale"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._get_node() is not None
