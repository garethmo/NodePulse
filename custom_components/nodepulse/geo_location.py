"""
NodePulse — Geo Location Platform.

Registers a geo_location entity for each tracked node with GPS coordinates.
HA renders these on the built-in map card natively.

Each entity exposes:
  - Current lat/lng (matching the node's latest position)
  - Node metadata (SNR, hops, short name, position-fix count) as extra
    attributes.

The HA Map card natively plots ``geo_location`` entities.
"""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.geo_location import GeolocationEvent
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
    """Dynamic geo_location discovery — shared NodeDiscovery helper (Q12)."""
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
        make_entities=lambda node: [NodeGeoLocation(coordinator, entry, node["id"])],
    )


class NodeGeoLocation(CoordinatorEntity, GeolocationEvent):
    """Geo location entity for one Meshtastic node.

    Appears on the HA native map card and provides the current position plus
    a few scalar node metrics as extra attributes. Position-history trails are
    not exposed by the coordinator (the addon serves them separately to the
    Web UI), so there is intentionally no ``trail_geojson`` attribute (Q13).
    """

    _attr_has_entity_name = True
    _attr_name = "Map Location"
    _attr_icon = "mdi:map-marker-radius"
    # Required by GeolocationEvent.source (@cached_property → self._attr_source).
    # Must be set at class level; omitting it causes an AttributeError on every
    # state write because the cached_property resolver raises before HA can catch it.
    _attr_source = "nodepulse"

    def __init__(
        self,
        coordinator: NodePulseCoordinator,
        entry: ConfigEntry,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_geo"

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
    def distance(self) -> Optional[float]:
        return 0.0

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra attributes for the geo location entity."""
        node = self._get_node()
        if not node:
            return {}

        # Note: position trail/history data is NOT included in the node object
        # returned by /api/nodes. It lives at /api/position-history and is fetched
        # separately by the Web UI. We surface only the scalar node metrics here.
        return {
            "snr": node.get("snr"),
            "hops_away": node.get("hops_away"),
            "short_name": node.get("short_name"),
            "last_position_fix": node.get("last_position_fix"),
            "stale": node.get("stale"),
            # How many GPS fixes have been recorded for this node in the addon store.
            "position_fix_count": node.get("position_fix_count"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._get_node() is not None
