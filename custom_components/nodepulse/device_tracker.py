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
from typing import Any, Dict, Optional

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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

    We only create a tracker for nodes that actually report GPS coordinates.
    Nodes without GPS still appear in the node list panel and sensors but
    do not clutter the HA map with unknown-location pins.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _has_gps_fix(node: Dict[str, Any]) -> bool:
        lat = node.get("latitude")
        lon = node.get("longitude")
        if lat is None or lon is None:
            return False
        return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)

    discovery = NodeDiscovery(coordinator, entry)
    discovery.attach(
        hass,
        async_add_entities,
        should_create=_has_gps_fix,
        make_entities=lambda node: NodeTracker(coordinator, entry, node["id"]),
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

    def _get_node(self) -> Optional[Dict[str, Any]]:
        return self.coordinator.get_node(self._node_id)

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
