"""
NodePulse Addon — MQTT Bridge.

Manages a persistent async connection to an external MQTT broker (e.g.
mqtt.meshtastic.org). Inbound packets are decoded from ServiceEnvelope
protobufs, passed through a multi-stage filter pipeline, and injected into
the NodePulse packet handler. Optionally, filtered packets may also be
forwarded to the local radio via MqttClientProxyMessage.

Filter pipeline (in evaluation order — cheapest check first):
  1. Node-ID blocklist  — drop packets from explicitly-banned senders.
  2. PortNum allowlist  — drop packet types not in the allowlist (if set).
  3. Geospatial bounds  — for POSITION_APP, check lat/lng against bounding
     box and cache the result; subsequent packets of any type from the same
     node are dropped while that node remains out-of-bounds. This prevents
     distant nodes from bypassing the geo-fence by sending non-position
     packet types (TEXT_MESSAGE_APP, TELEMETRY_APP, etc.).
"""
import asyncio
import logging
from typing import Callable, Optional

import uuid
import aiomqtt
from meshtastic.protobuf.mqtt_pb2 import ServiceEnvelope
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

# Reconnect backoff constants (seconds)
_RECONNECT_BASE_S = 5
_RECONNECT_MAX_S = 120


class MqttBridge:
    def __init__(
        self,
        config,
        packet_callback: Callable,
        forward_callback: Optional[Callable] = None,
    ):
        self._config = config
        self.packet_callback = packet_callback
        self.forward_callback = forward_callback
        self.client: Optional[aiomqtt.Client] = None
        self._task: Optional[asyncio.Task] = None

        self.enabled = config.mqtt_enabled
        self.address = config.mqtt_address
        self.port = config.mqtt_port
        # Treat empty strings as "no auth" so aiomqtt sends no credentials
        self.username = config.mqtt_username or None
        self.password = config.mqtt_password or None
        self.topic = config.mqtt_topic
        self.forwarding_enabled = config.mqtt_forwarding_enabled

        # Filter settings
        self.geo_filter_enabled = config.mqtt_geo_filter_enabled
        self.lat_min = config.mqtt_lat_min
        self.lat_max = config.mqtt_lat_max
        self.lng_min = config.mqtt_lng_min
        self.lng_max = config.mqtt_lng_max
        self.portnum_allowlist: set[str] = set(config.mqtt_portnum_allowlist)
        self.node_blocklist: set[str] = set(config.mqtt_node_blocklist)

        # Per-node geo-bounds cache: node_hex -> in_bounds (bool).
        # Populated when a POSITION_APP packet is received; used to gate
        # all subsequent packet types so the geo-fence cannot be bypassed.
        self._node_in_bounds: dict[str, bool] = {}

    async def start(self) -> None:
        if not self.enabled:
            logger.info("MQTT bridge is disabled in config")
            return
        logger.info("Starting MQTT bridge (address=%s, port=%s)", self.address, self.port)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        """Persistent connect → subscribe → receive loop with exponential backoff."""
        reconnect_interval = _RECONNECT_BASE_S

        while True:
            try:
                logger.info(
                    "Connecting to MQTT broker (address=%s, port=%s)",
                    self.address, self.port,
                )
                async with aiomqtt.Client(
                    hostname=self.address,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    identifier=f"nodepulse-{uuid.uuid4().hex[:12]}",
                ) as client:
                    self.client = client
                    logger.info("Connected to MQTT broker (address=%s)", self.address)
                    await client.subscribe(self.topic)
                    logger.info("Subscribed to MQTT topic (topic=%s)", self.topic)
                    # Reset backoff on a successful connection
                    reconnect_interval = _RECONNECT_BASE_S

                    async for message in client.messages:
                        await self._handle_message(message)

            except aiomqtt.MqttError as exc:
                logger.warning(
                    "MQTT connection error — reconnecting (delay=%ss): %s",
                    reconnect_interval, exc,
                )
                await asyncio.sleep(reconnect_interval)
                reconnect_interval = min(reconnect_interval * 2, _RECONNECT_MAX_S)
            except asyncio.CancelledError:
                logger.info("MQTT bridge task cancelled")
                break
            except Exception as exc:
                logger.error(
                    "Unexpected error in MQTT loop — reconnecting (delay=%ss): %s",
                    reconnect_interval, exc,
                    exc_info=True,
                )
                await asyncio.sleep(reconnect_interval)
                reconnect_interval = min(reconnect_interval * 2, _RECONNECT_MAX_S)

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        topic = message.topic.value
        payload = message.payload

        logger.debug("Received MQTT message (topic=%s)", topic)

        try:
            envelope = ServiceEnvelope()
            envelope.ParseFromString(payload)

            packet_dict = MessageToDict(envelope.packet)

            if not self._apply_filters(packet_dict):
                return

            # Tag the packet so the UI can badge MQTT-sourced nodes with ☁️.
            packet_dict["_via_mqtt"] = True
            packet_dict["_mqtt_topic"] = topic

            # Dispatch to the meshtastic connection's receive handler.
            # _on_mesh_receive uses threading.Lock objects internally and was
            # designed to run on the meshtastic library's background thread.
            # Run it in a thread-pool worker so the async event loop is never
            # blocked by the lock-acquisition or the handler's processing work.
            await asyncio.to_thread(self.packet_callback, packet_dict)

            # Optionally bridge the filtered packet to the local radio.
            if self.forwarding_enabled and self.forward_callback:
                asyncio.create_task(self.forward_callback(topic, payload))

        except Exception as exc:
            logger.error("Failed to process MQTT message (topic=%s): %s", topic, exc)

    def _apply_filters(self, packet_dict: dict) -> bool:
        """
        Apply the three-stage filter pipeline.

        Returns True if the packet should be processed, False to drop it.
        Stages run cheapest-first to short-circuit early.
        """
        from_id = packet_dict.get("from")
        from_hex: Optional[str] = None

        # Stage 1: Node-ID blocklist
        if from_id:
            try:
                from_hex = f"!{int(from_id):08x}"
                if self.node_blocklist and from_hex in self.node_blocklist:
                    return False
            except (ValueError, TypeError):
                pass

        decoded = packet_dict.get("decoded")
        if not decoded:
            # Encrypted / undecoded packets have no portnum — let them through.
            return True

        portnum = decoded.get("portnum", "")

        # Stage 2: PortNum allowlist
        if portnum and self.portnum_allowlist and portnum not in self.portnum_allowlist:
            return False

        # Stage 3: Geospatial bounds
        # For POSITION_APP: evaluate the bounding box and update the cache.
        # For all other types: consult the cache for this sender's last
        # known position, dropping the packet if they were out-of-bounds.
        if self.geo_filter_enabled and from_hex:
            if portnum == "POSITION_APP":
                in_bounds = self._check_position_bounds(decoded.get("position", {}))
                self._node_in_bounds[from_hex] = in_bounds
                if not in_bounds:
                    return False
            elif from_hex in self._node_in_bounds:
                if not self._node_in_bounds[from_hex]:
                    return False

        return True

    def _check_position_bounds(self, position: dict) -> bool:
        """Return True if position coordinates fall within the configured bounding box."""
        lat_i = position.get("latitudeI")
        lng_i = position.get("longitudeI")
        if lat_i is None or lng_i is None:
            # No coordinate data — allow through (unknown location).
            return True
        lat = lat_i / 1e7
        lng = lng_i / 1e7
        return self.lat_min <= lat <= self.lat_max and self.lng_min <= lng <= self.lng_max
