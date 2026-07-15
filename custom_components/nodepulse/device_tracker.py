"""
NodePulse — Device Tracker Platform.

Registers a device_tracker entity for each node that has GPS coordinates.
HA renders these on the native map card, giving a live view of node locations
alongside any other tracked devices (phones, vehicles, etc.) in the system.

Nodes without a GPS fix are registered but reported as "not_home" / unknown
location — HA handles this gracefully by not pinning them to a map position.

Design decision: We extend CoordinatorEntity + TrackerEntity rather than
implementing a full ScannerEntity because we are not scanning a local network —
we are receiving position data from the mesh. TrackerEntity is the correct
choice for externally-reported GPS coordinates.
"""
import logging
from typing import Any, Dict, List, Optional, Set

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    """
    Dynamic tracker discovery — same pattern as sensor.py.

    We only create a tracker for nodes that actually report GPS coordinates.
    Nodes without GPS still appear in the node list panel and sensors but
    do not clutter the HA map with unknown-location pins.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]
    registered_node_ids: Set[str] = set()

    @callback
    def _discover_new_trackers() -> None:
        nodes: List[Dict] = coordinator.data.get("nodes", [])
        new_trackers = []

        for node in nodes:
            node_id = node.get("id")
            if not node_id or node_id in registered_node_ids:
                continue

            # Only register a tracker if the node has reported at least one
            # GPS fix — avoids cluttering the map with nodes that will never
            # have a location.
            lat = node.get("latitude")
            lon = node.get("longitude")
            if lat is None or lon is None:
                continue
            if lat == 0 and lon == 0:
                continue

            registered_node_ids.add(node_id)
            new_trackers.append(NodeTracker(coordinator, entry, node_id))
            logger.info({"node_id": node_id}, "Registering device tracker for node")

        if new_trackers:
            async_add_entities(new_trackers)

    _discover_new_trackers()
    entry.async_on_unload(coordinator.async_add_listener(_discover_new_trackers))


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
        self._attr_device_info = {
            "identifiers": {(DOMAIN, node_id)},
            "name": f"Mesh Node {node_id}",
            "manufacturer": "Meshtastic",
            "via_device": (DOMAIN, entry.entry_id),
        }

    def _get_node(self) -> Optional[Dict[str, Any]]:
        for node in self.coordinator.data.get("nodes", []):
            if node.get("id") == self._node_id:
                return node
        return None

    @property
    def latitude(self) -> Optional[float]:
        node = self._get_node()
        return node.get("latitude") if node else None

    @property
    def longitude(self) -> Optional[float]:
        node = self._get_node()
        return node.get("longitude") if node else None

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
            "altitude":   node.get("altitude"),
            "snr":        node.get("snr"),
            "rssi":       node.get("rssi"),
            "hops_away":  node.get("hops_away"),
            "hw_model":   node.get("hw_model"),
            "short_name": node.get("short_name"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._get_node() is not None
