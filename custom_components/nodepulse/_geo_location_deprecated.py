"""
NodePulse — Geo Location Platform.

Registers a geo_location entity for every tracked node. The HA map card
natively plots these entities. When a node has valid GPS coordinates the pin
appears immediately; nodes without a fix are registered but hold no position,
so they appear on the map as soon as their first GPS update arrives without
needing a re-discovery cycle.
"""

import logging
from typing import Any, Dict, Optional

from homeassistant.components.geo_location import GeolocationEvent
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
    """Dynamic geo_location discovery — shared NodeDiscovery helper (Q12).

    A geo_location entity is created only for tracked nodes that have GPS coordinates.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _should_create_geo(node: Dict[str, Any]) -> bool:
        """Only create geo_location entity if node has GPS coordinates."""
        return node.get("latitude") is not None and node.get("longitude") is not None

    discovery = NodeDiscovery(coordinator, entry)
    discovery.attach(
        hass,
        async_add_entities,
        should_create=_should_create_geo,
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
        # Normalize node_id for consistency with coordinator's normalized index
        self._node_id = normalize_node_id(node_id) or node_id
        self._attr_unique_id = f"{entry.entry_id}_{self._node_id}_geo"

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

        # Initialize coordinates from coordinator snapshot on creation so HA
        # has valid state before the first update callback fires.
        self._sync_coordinates(node)

    def _get_node(self) -> Optional[Dict[str, Any]]:
        return self.coordinator.get_node(self._node_id)

    def _sync_coordinates(self, node: Optional[Dict[str, Any]]) -> None:
        """Write fresh coordinates, clearing HA's cached_property values first.

        Home Assistant exposes ``latitude``/``longitude`` as cached properties.
        Some HA versions cache the first read before we receive a later GPS fix;
        explicitly clearing the cache guarantees the map sees each movement.
        """
        self.__dict__.pop("latitude", None)
        self.__dict__.pop("longitude", None)
        self.__dict__.pop("altitude", None)
        if node:
            self._attr_latitude = node.get("latitude")
            self._attr_longitude = node.get("longitude")
            self._attr_altitude = node.get("altitude")
        else:
            self._attr_latitude = None
            self._attr_longitude = None
            self._attr_altitude = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        node = self._get_node()
        self._sync_coordinates(node)
        # Delegate the state write to the base class so HA's update flow
        # runs exactly once per cycle (see device_tracker.py for rationale).
        super()._handle_coordinator_update()

    @property
    def altitude(self) -> Optional[float]:
        """Return the altitude in meters above sea level."""
        node = self._get_node()
        if not node:
            return None
        return node.get("altitude")

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
