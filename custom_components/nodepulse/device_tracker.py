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
from typing import Any, Dict, List, Optional

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
    # Reset discovery bookkeeping on each setup so a reload re-creates trackers
    # instead of skipping them due to a stale set.
    coordinator.registered_tracker_ids = set()
    coordinator.registered_tracker_entities = []
    registered_node_ids = coordinator.registered_tracker_ids
    registered_entities = coordinator.registered_tracker_entities

    @callback
    def _discover_new_trackers() -> None:
        nodes: List[Dict] = (coordinator.data or {}).get("nodes", [])
        visible_ids = {n.get("id") for n in nodes if n.get("id")}

        # Remove trackers for nodes that are no longer tracked (or gone).
        for entity in list(registered_entities):
            nid = getattr(entity, "_node_id", None)
            if nid is not None and (
                nid not in coordinator.tracked_nodes or nid not in visible_ids
            ):
                registered_entities.remove(entity)
                registered_node_ids.discard(nid)
                hass.async_create_task(entity.async_remove(force_remove=True))

        new_trackers = []

        for node in nodes:
            node_id = node.get("id")
            if not node_id or node_id in registered_node_ids:
                continue

            # Only one tracked node gets a tracker (the Web UI toggle drives
            # this), and only if it has reported at least one GPS fix.
            if node_id not in coordinator.tracked_nodes:
                continue

            lat = node.get("latitude")
            lon = node.get("longitude")
            if lat is None or lon is None:
                continue
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue

            registered_node_ids.add(node_id)
            new_trackers.append(NodeTracker(coordinator, entry, node_id))
            logger.debug("Registering device tracker for node (node_id=%s)", node_id)

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

        # Resolve a human-readable name from the coordinator's latest data.
        # Falls back to the hex ID if node data isn't loaded yet.
        name = f"Mesh Node {node_id}"
        model = "Meshtastic Node"
        nodes = (coordinator.data or {}).get("nodes", [])
        node = next((n for n in nodes if n.get("id") == node_id), None)
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
        nodes = (self.coordinator.data or {}).get("nodes", [])
        for node in nodes:
            if node.get("id") == self._node_id:
                return node
        return None

    @property
    def latitude(self) -> Optional[float]:
        node = self._get_node()
        lat = node.get("latitude") if node else None
        logger.debug(
            "NodeTracker (node_id=%s): latitude=%s, node_data=%s",
            self._node_id, lat, node
        )
        return lat

    @property
    def longitude(self) -> Optional[float]:
        node = self._get_node()
        lon = node.get("longitude") if node else None
        logger.debug(
            "NodeTracker (node_id=%s): longitude=%s",
            self._node_id, lon
        )
        return lon

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
