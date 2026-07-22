"""
NodePulse — Sensor Platform.

Registers sensors for:
  - Overall node count (one sensor per integration entry).
  - Per-node metrics: SNR, RSSI, hops away, last heard, battery level.
    One set of sensors is created per discovered node.

Node discovery is dynamic: on each coordinator update we check for new nodes
and register entities for any that don't yet have one. Nodes that disappear
from the API are handled by the coordinator marking them unavailable.

We use a simple "seen node IDs" set to avoid re-registering entities for
nodes we've already set up in a previous poll cycle.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfTime,
)
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
    Initial entity setup and dynamic discovery on subsequent coordinator updates.

    We register a coordinator listener here rather than creating all entities
    upfront. This means new nodes that appear after the integration starts
    (e.g., a node comes back online) are automatically picked up.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Reset discovery bookkeeping on each setup (also runs after a reload), so
    # entities are re-created rather than skipped by a stale module-level set.
    coordinator.registered_sensor_ids = set()
    coordinator.registered_sensor_entities = []

    # Track which node IDs already have entities so we don't duplicate them.
    registered_node_ids = coordinator.registered_sensor_ids
    # Keep references so we can remove entities when a node is untracked.
    registered_entities = coordinator.registered_sensor_entities

    # Always create the aggregate node count sensor immediately.
    async_add_entities([NodeCountSensor(coordinator, entry)])

    SENSOR_CLASSES = [
        NodeSnrSensor, NodeRssiSensor, NodeHopsSensor, NodeLastHeardSensor,
        NodeBatterySensor, NodeTemperatureSensor, NodeHumiditySensor,
        NodePressureSensor, NodeVoltageSensor, NodeChannelUtilSensor,
        NodeAirUtilTxSensor, NodeUptimeSensor, NodeRoleSensor,
        NodeGasResistanceSensor, NodeLatitudeSensor, NodeLongitudeSensor,
        NodeAltitudeSensor, NodeMessageReceivedSensor, NodeMessageSentSensor,
        NodeDistanceSensor, NodeNeighborCountSensor, NodePositionFixCountSensor,
        NodeTagsSensor,
    ]

    @callback
    def _discover_new_nodes() -> None:
        """Called after every coordinator update to find and register new nodes.

        Only nodes the user has chosen to track (coordinator.tracked_nodes) get
        per-node sensor entities. This lets the Web UI's "Track in HA" toggle
        selectively create entities for individual nodes instead of importing
        the whole mesh at once.
        """
        nodes: List[Dict] = (coordinator.data or {}).get("nodes", [])
        visible_ids = {n.get("id") for n in nodes if n.get("id")}

        # Remove entities for nodes that are no longer tracked (or gone).
        for entity in list(registered_entities):
            nid = getattr(entity, "_node_id", None)
            if nid is not None and (
                nid not in coordinator.tracked_nodes or nid not in visible_ids
            ):
                registered_entities.remove(entity)
                registered_node_ids.discard(entity.unique_id)
                hass.async_create_task(entity.async_remove(force_remove=True))

        new_entities = []
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            # Per-node entities are created ONLY for tracked nodes.
            if node_id not in coordinator.tracked_nodes:
                continue

            for sensor_cls in SENSOR_CLASSES:
                # We instantiate the sensor briefly to check its unique_id and current value.
                # If it's already registered, we skip it.
                # If it's not registered, we only add it if it has a known value,
                # preventing the UI from being cluttered with "Unknown" metrics
                # for hardware features the node doesn't possess.
                sensor = sensor_cls(coordinator, entry, node_id)
                if sensor.unique_id in registered_node_ids:
                    continue

                if sensor.native_value is not None:
                    registered_node_ids.add(sensor.unique_id)
                    registered_entities.append(sensor)
                    new_entities.append(sensor)
                    logger.debug("Registering new node sensor %s", sensor.unique_id)

        if new_entities:
            async_add_entities(new_entities)

    # Run discovery for the already-loaded initial data.
    _discover_new_nodes()

    # Subscribe to future updates — HA will call this after every poll cycle.
    entry.async_on_unload(coordinator.async_add_listener(_discover_new_nodes))


# ---------------------------------------------------------------------------
# Aggregate sensor
# ---------------------------------------------------------------------------

class NodeCountSensor(CoordinatorEntity, SensorEntity):
    """Total number of nodes visible to the connected Meshtastic node."""

    _attr_name = "Node Count"
    _attr_icon = "mdi:radio-tower"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, coordinator: NodePulseCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_node_count"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("nodes", []))


# ---------------------------------------------------------------------------
# Per-node sensor base class
# ---------------------------------------------------------------------------

class _NodeSensorBase(CoordinatorEntity, SensorEntity):
    """
    Abstract base for sensors that track a metric on a specific node.

    Subclasses only need to define _metric_key and the standard HA sensor
    attributes (_attr_name, _attr_native_unit_of_measurement, etc.).
    """

    _metric_key: str  # key in the node dict from the API

    def __init__(
        self,
        coordinator: NodePulseCoordinator,
        entry: ConfigEntry,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        # Per-node device groups all metrics under one HA device per node.
        self._attr_device_info = _node_device_info(entry, node_id, coordinator)

    def _get_node(self) -> Optional[Dict[str, Any]]:
        """Find this node's dict in the coordinator data."""
        nodes = (self.coordinator.data or {}).get("nodes", [])
        for node in nodes:
            if node.get("id") == self._node_id:
                return node
        return None

    @property
    def native_value(self) -> Any:
        node = self._get_node()
        if node is None:
            return None
        return node.get(self._metric_key)

    @property
    def available(self) -> bool:
        """Entities stay available as long as the coordinator is healthy.

        We intentionally do NOT mark a sensor unavailable just because its node
        is momentarily absent from the node list — nodes routinely drop in and
        out of the mesh, and flipping entities to "unavailable" on every blip
        spams the logbook with useless "Unavailable" entries while the last
        known value (still shown by the entity) remains meaningful. The
        coordinator's ``last_update_success`` flag still drives availability so
        a genuine addon outage correctly marks everything unavailable.
        """
        return super().available


# ---------------------------------------------------------------------------
# Concrete per-node sensors
# ---------------------------------------------------------------------------

class NodeSnrSensor(_NodeSensorBase):
    _metric_key = "snr"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dB"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_snr"
        self._attr_name = "SNR"


class NodeRssiSensor(_NodeSensorBase):
    _metric_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dBm"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_rssi"
        self._attr_name = "RSSI"


class NodeHopsSensor(_NodeSensorBase):
    _metric_key = "hops_away"
    _attr_icon = "mdi:transit-connection-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_hops"
        self._attr_name = "Hops Away"


class NodeLastHeardSensor(_NodeSensorBase):
    """Reports the last-heard timestamp as a HA datetime sensor."""
    _metric_key = "last_heard"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_last_heard"
        self._attr_name = "Last Heard"

    @property
    def native_value(self):
        """Convert Unix epoch seconds to an aware datetime for the HA sensor."""
        node = self._get_node()
        epoch = node.get("last_heard") if node else None
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc)


class NodeBatterySensor(_NodeSensorBase):
    _metric_key = "battery_level"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_battery"
        self._attr_name = "Battery"


class NodeTemperatureSensor(_NodeSensorBase):
    """Environmental temperature (°C) reported by the node's telemetry, if any."""
    _metric_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_temperature"
        self._attr_name = "Temperature"


class NodeHumiditySensor(_NodeSensorBase):
    """Environmental relative humidity (%) from the node's telemetry, if any."""
    _metric_key = "relative_humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_humidity"
        self._attr_name = "Humidity"


class NodePressureSensor(_NodeSensorBase):
    """Barometric pressure (hPa) from the node's telemetry, if any."""
    _metric_key = "barometric_pressure"
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPressure.HPA

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_pressure"
        self._attr_name = "Pressure"


class NodeLatitudeSensor(_NodeSensorBase):
    """GPS latitude (°) reported by the node's last position fix, if any."""
    _metric_key = "latitude"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_latitude"
        self._attr_name = "Latitude"


class NodeLongitudeSensor(_NodeSensorBase):
    """GPS longitude (°) reported by the node's last position fix, if any."""
    _metric_key = "longitude"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_longitude"
        self._attr_name = "Longitude"


class NodeAltitudeSensor(_NodeSensorBase):
    """GPS altitude (m) reported by the node's last position fix, if any."""
    _metric_key = "altitude"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.METERS

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_altitude"
        self._attr_name = "Altitude"


class NodeVoltageSensor(_NodeSensorBase):
    """Battery supply voltage (V) reported by the node's device metrics, if any."""
    _metric_key = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_voltage"
        self._attr_name = "Voltage"


class NodeChannelUtilSensor(_NodeSensorBase):
    """Channel utilization (%) from the node's device metrics, if any."""
    _metric_key = "channel_utilization"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_channel_util"
        self._attr_name = "Channel Utilization"


class NodeAirUtilTxSensor(_NodeSensorBase):
    """Airtime utilization for TX (%) from the node's device metrics, if any."""
    _metric_key = "air_util_tx"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_air_util_tx"
        self._attr_name = "Air Utilization TX"


class NodeUptimeSensor(_NodeSensorBase):
    """Node uptime (seconds) reported by the device metrics, if any."""
    _metric_key = "uptime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_uptime"
        self._attr_name = "Uptime"


class NodeRoleSensor(_NodeSensorBase):
    """Meshtastic device role (e.g. CLIENT, ROUTER) for this node, if known."""
    _metric_key = "role"
    _attr_icon = "mdi:account-cog"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_role"
        self._attr_name = "Role"


class NodeGasResistanceSensor(_NodeSensorBase):
    """Gas resistance (MΩ) from the node's environment telemetry, if any."""
    _metric_key = "gas_resistance"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "MOhms"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_gas_resistance"
        self._attr_name = "Gas Resistance"


class NodeMessageSensor(_NodeSensorBase):
    """Base class for per-node message sensors that surface a message feed.

    Two concrete subclasses expose the most recent received and sent message
    for a node, so automations can trigger on both directions independently.
    """

    _attr_icon = "mdi:message-text"
    _attr_has_entity_name = True

    _outgoing: bool  # True = sent messages, False = received messages

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        direction = "sent" if self._outgoing else "received"
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_message_{direction}"

    @staticmethod
    def _norm_node_id(raw: Any) -> Optional[str]:
        """Normalise a node ID for comparison.

        The addon may emit IDs as ``!XXXXXXXX`` (canonical), ``XXXXXXXX``
        (no leading bang), or even with mixed case depending on the meshtastic
        library version and how the packet was decoded. We strip the optional
        leading ``!`` and lowercase so a tracked node id always matches the
        message's ``from_id``/``to_id`` regardless of formatting differences.
        """
        if not raw:
            return None
        s = str(raw).strip().lower()
        return s[1:] if s.startswith("!") else s

    def _self_node_id(self) -> Optional[str]:
        """Return the local/self node id (normalised), or None if unknown.

        Prefer the coordinator's status payload (populated from the node's
        ``myInfo``), which is reliable, over the ``outgoing`` flag recorded by
        the addon at capture time (which can be wrong when ``myInfo`` wasn't
        available the moment a packet arrived).
        """
        status = (self.coordinator.data or {}).get("status", {})
        my_info = status.get("my_info") or {}
        my_num = my_info.get("my_node_num")
        if my_num is not None:
            return self._norm_node_id("!" + format(int(my_num), "08x"))
        return None

    def _node_messages(self) -> List[Dict[str, Any]]:
        """Return received/sent messages involving this node, oldest first."""
        messages = (self.coordinator.data or {}).get("messages", [])
        node_id = self._norm_node_id(self._node_id)
        self_id = self._self_node_id()

        logger.debug(
            "NodeMessageSensor filtering: node_id=%s, self_id=%s, total_messages=%s",
            node_id, self_id, len(messages)
        )

        out = []
        for m in messages:
            from_id = self._norm_node_id(m.get("from_id"))
            to_id = self._norm_node_id(m.get("to_id"))
            if from_id != node_id and to_id != node_id:
                continue
            # Recompute direction from the tracked node id so it's robust even when the
            # addon's captured ``outgoing`` flag was unreliable.
            is_outgoing = (from_id == node_id) if node_id else bool(m.get("outgoing"))
            logger.debug(
                "Message check: from_id=%s, to_id=%s, is_outgoing=%s, self._outgoing=%s, match=%s",
                from_id, to_id, is_outgoing, self._outgoing, is_outgoing == self._outgoing
            )
            if is_outgoing == self._outgoing:
                out.append(m)
        return out

    @property
    def native_value(self) -> str:
        """Return the most recent message text for this direction."""
        node_messages = self._node_messages()
        logger.debug(
            "NodeMessageSensor (node_id=%s, outgoing=%s): filtered count=%s",
            self._node_id, self._outgoing, len(node_messages)
        )
        if not node_messages:
            return None
        # Messages are returned oldest first, so get the last one
        return node_messages[-1].get("text")


class NodeMessageReceivedSensor(NodeMessageSensor):
    """Last received text message for this node, for automation triggers."""

    _outgoing = False

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_name = "Last Message Received"


class NodeMessageSentSensor(NodeMessageSensor):
    """Last sent text message for this node, for automation triggers."""

    _outgoing = True

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_name = "Last Message Sent"


class NodeDistanceSensor(_NodeSensorBase):
    """Great-circle distance (km) from the self/gateway node."""

    _metric_key = "distance_km"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_distance"
        self._attr_name = "Distance"

    @property
    def native_value(self):
        node = self._get_node()
        if not node or node.get("latitude") is None or node.get("longitude") is None:
            return None
        # Find the self node from coordinator data.
        status = (self.coordinator.data or {}).get("status", {})
        my_info = status.get("my_info") or {}
        my_num = my_info.get("my_node_num")
        if my_num is None:
            return None
        self_id = "!" + format(int(my_num), "08x")
        nodes = (self.coordinator.data or {}).get("nodes", [])
        self_node = next((n for n in nodes if n.get("id") == self_id), None)
        if not self_node or self_node.get("latitude") is None or self_node.get("longitude") is None:
            return None
        from math import asin, cos, radians, sin, sqrt
        R = 6371.0
        lat1, lon1 = radians(self_node["latitude"]), radians(self_node["longitude"])
        lat2, lon2 = radians(node["latitude"]), radians(node["longitude"])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return round(R * 2 * asin(sqrt(a)), 2)


class NodeNeighborCountSensor(_NodeSensorBase):
    """Number of peers this node sees, from NEIGHBORINFO_APP data."""

    _metric_key = "neighbor_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_neighbor_count"
        self._attr_name = "Neighbor Count"

    @property
    def native_value(self):
        node = self._get_node()
        if not node:
            return None
        neighbors = node.get("neighbors")
        if neighbors is None:
            return None
        return len(neighbors)


class NodePositionFixCountSensor(_NodeSensorBase):
    """Number of recorded GPS trail points for this node."""

    _metric_key = "position_fix_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_position_fix_count"
        self._attr_name = "Position Fixes"


class NodeTagsSensor(_NodeSensorBase):
    """User-defined tags for this node (comma-separated), stored on the addon."""

    _metric_key = "tags"
    _attr_icon = "mdi:tag-text"

    def __init__(self, coordinator, entry, node_id):
        super().__init__(coordinator, entry, node_id)
        self._attr_unique_id = f"{entry.entry_id}_{node_id}_tags"
        self._attr_name = "Tags"

    @property
    def native_value(self):
        node = self._get_node()
        if not node:
            return None
        tags = node.get("tags")
        if not tags or not isinstance(tags, list):
            return None
        return ", ".join(tags)


# ---------------------------------------------------------------------------
# Device info helpers
# ---------------------------------------------------------------------------

def _device_info(entry: ConfigEntry) -> Dict:
    """Device info dict for the NodePulse integration-level device."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "NodePulse",
        "manufacturer": "NodePulse",
        "model": "Meshtastic Monitor",
    }


def _node_device_info(entry: ConfigEntry, node_id: str, coordinator) -> Dict:
    """Device info dict for a specific Meshtastic node device group."""
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

    return {
        "identifiers": {(DOMAIN, node_id)},
        "name": name,
        "manufacturer": "Meshtastic",
        "model": model,
        "via_device": (DOMAIN, entry.entry_id),
    }
