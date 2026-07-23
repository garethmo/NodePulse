"""
NodePulse — Geo Location Platform.

Registers a geo_location entity for each tracked node with GPS coordinates.
HA renders these on the built-in map card natively.

Each entity exposes:
  - Current lat/lng (matching the node's latest position)
  - An extra ``trail_geojson`` attribute containing a GeoJSON LineString
    of the node's position history trail (when available).

The HA Map card natively plots ``geo_location`` entities and can render
their trails via a ``geo_json_source`` configuration.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.geo_location import GeolocationEvent
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
    """Dynamic geo_location discovery — same pattern as sensor.py."""
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Reset discovery bookkeeping on each setup (also runs after a reload), so
    # entities are re-created rather than skipped by a stale set.
    coordinator.registered_geo_ids = set()
    coordinator.registered_geo_entities = []
    registered_node_ids = coordinator.registered_geo_ids
    registered_entities = coordinator.registered_geo_entities

    @callback
    def _discover() -> None:
        nodes: List[Dict] = (coordinator.data or {}).get("nodes", [])
        visible_ids = {n.get("id") for n in nodes if n.get("id")}

        # Remove entities for nodes no longer tracked or gone.
        for entity in list(registered_entities):
            nid = getattr(entity, "_node_id", None)
            if nid is not None and (
                nid not in coordinator.tracked_nodes or nid not in visible_ids
            ):
                registered_entities.remove(entity)
                registered_node_ids.discard(nid)
                hass.async_create_task(entity.async_remove(force_remove=True))

        new_entities = []
        for node in nodes:
            node_id = node.get("id")
            if not node_id or node_id in registered_node_ids:
                continue
            if node_id not in coordinator.tracked_nodes:
                continue
            lat = node.get("latitude")
            lon = node.get("longitude")
            if lat is None or lon is None:
                continue
            if abs(lat) < 1e-9 and abs(lon) < 1e-9:
                continue

            registered_node_ids.add(node_id)
            new_entities.append(NodeGeoLocation(coordinator, entry, node_id))
            logger.debug("Registering geo_location for node (node_id=%s)", node_id)

        if new_entities:
            async_add_entities(new_entities)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class NodeGeoLocation(CoordinatorEntity, GeolocationEvent):
    """Geo location entity for one Meshtastic node.

    Appears on the HA native map card. Provides the current position and
    a ``trail_geojson`` extra attribute for trail rendering.
    """

    _attr_has_entity_name = True
    _attr_name = "Map Location"
    _attr_icon = "mdi:map-marker-radius"
    _attr_source_type = "gps"
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
        return node.get("latitude") if node else None

    @property
    def longitude(self) -> Optional[float]:
        node = self._get_node()
        return node.get("longitude") if node else None

    @property
    def distance(self) -> Optional[float]:
        return 0.0

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return attributes including trail GeoJSON."""
        node = self._get_node()
        if not node:
            return {}

        attrs: Dict[str, Any] = {
            "snr": node.get("snr"),
            "hops_away": node.get("hops_away"),
            "short_name": node.get("short_name"),
            "last_position_fix": node.get("last_position_fix"),
            "stale": node.get("stale"),
        }

        # Build a GeoJSON LineString from the position history.
        # The coordinator stores position history under the node's ID.
        # We build a FeatureCollection so both the point and the trail
        # can be consumed by the HA Map card with geo_json_source.
        fixes = node.get("position_fixes")
        if fixes and isinstance(fixes, list) and len(fixes) >= 2:
            coords = [
                [f["lng"], f["lat"]]
                for f in fixes
                if f.get("lat") is not None and f.get("lng") is not None
            ]
            if len(coords) >= 2:
                attrs["trail_geojson"] = json.dumps({
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": coords},
                            "properties": {"name": "Position Trail"},
                        },
                    ],
                })

        return attrs

    @property
    def available(self) -> bool:
        return super().available and self._get_node() is not None
