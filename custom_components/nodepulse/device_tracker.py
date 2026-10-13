"""
NodePulse — Device Tracker Platform.

Registers a device_tracker entity for every tracked node. When a node has a
valid GPS fix, HA plots it on the native map card alongside other tracked
devices. Once a fix has been received, coordinates are retained ("sticky")
so that the state never regresses to ``unknown`` just because the latest
coordinator snapshot is missing lat/lon — the last known position is always
preferred over losing the location entirely.
"""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, normalize_node_id
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

    A device_tracker entity is created only for tracked nodes that have GPS coordinates.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _should_create_tracker(node: Dict[str, Any]) -> bool:
        """Only create device_tracker entity if node has GPS coordinates."""
        return node.get("latitude") is not None and node.get("longitude") is not None

    discovery = NodeDiscovery(coordinator, entry)
    discovery.attach(
        hass,
        async_add_entities,
        should_create=_should_create_tracker,
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
        # Normalize node_id for consistency with coordinator's normalized index
        self._node_id = normalize_node_id(node_id) or node_id
        self._attr_unique_id = f"{entry.entry_id}_{self._node_id}_tracker"

        # Resolve a human-readable name from the coordinator's latest data.
        # Falls back to the hex ID if node data isn't loaded yet.
        name = f"Mesh Node {self._node_id}"
        model = "Meshtastic Node"
        node = coordinator.get_node(self._node_id)
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
            "identifiers": {(DOMAIN, self._node_id)},
            "name": name,
            "manufacturer": "Meshtastic",
            "model": model,
            "via_device": (DOMAIN, entry.entry_id),
        }

        # Pre-initialise to None so _sync_coordinates can safely check whether
        # we already hold a valid fix before deciding to retain the old value.
        self._attr_latitude: Optional[float] = None
        self._attr_longitude: Optional[float] = None
        self._attr_altitude: Optional[float] = None

        # Seed coordinates from the coordinator snapshot so HA has valid state
        # before the first update callback fires.
        self._sync_coordinates(node)

    def _get_node(self) -> Optional[Dict[str, Any]]:
        return self.coordinator.get_node(self._node_id)

    def _sync_coordinates(self, node: Optional[Dict[str, Any]]) -> None:
        """Write fresh coordinates, clearing HA's cached_property values first.

        Home Assistant exposes ``latitude``/``longitude`` as cached properties.
        Some HA versions cache the first read before we receive a later GPS fix;
        explicitly clearing the cache guarantees the map sees each movement.

        Coordinates are **sticky**: once a valid GPS fix has been received we
        never overwrite it with ``None``. This prevents the entity state from
        regressing to ``unknown`` during reconnects or when a coordinator
        snapshot arrives without position data.
        """
        # latitude and longitude are @cached_property on TrackerEntity; clear the
        # instance-level cache so HA picks up fresh coordinates on every state write.
        self.__dict__.pop("latitude", None)
        self.__dict__.pop("longitude", None)

        if node:
            new_lat = node.get("latitude")
            new_lon = node.get("longitude")
            new_alt = node.get("altitude")

            if new_lat is not None and new_lon is not None:
                # Fresh valid fix — update all three coordinates.
                self._attr_latitude = new_lat
                self._attr_longitude = new_lon
                self._attr_altitude = new_alt
            # else: no new fix in this snapshot — retain the last known position
            # so the state stays at the previous coordinates rather than going unknown.
        # else: node vanished from coordinator — also retain last known position.

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        node = self._get_node()
        self._sync_coordinates(node)
        # Delegate the actual state write to the base class so HA's standard
        # entity update flow runs exactly once per coordinator cycle.
        # Calling async_write_ha_state() here AND in super() caused two writes
        # per cycle; the second write (from super) could race with HA's state
        # deduplication and silently discard the coordinate update.
        super()._handle_coordinator_update()

    # altitude is served via self._attr_altitude set by _sync_coordinates().
    # No property override needed — the _attr_* convention handles it.

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
