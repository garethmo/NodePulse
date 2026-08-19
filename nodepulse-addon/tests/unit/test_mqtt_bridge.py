from unittest.mock import Mock
from app.mqtt_bridge import MqttBridge

def test_mqtt_bridge_initializes_without_error():
    """Basic sanity check that MqttBridge can be instantiated."""
    # Minimal configuration – only the fields accessed in __init__ are needed.
    config = Mock()
    config.mqtt_enabled = False
    config.mqtt_address = ""
    config.mqtt_port = 0
    config.mqtt_username = None
    config.mqtt_password = None
    config.mqtt_topic = ""
    config.mqtt_forwarding_enabled = False
    # Other config attributes used later are set to harmless defaults.
    for attr in [
        "mqtt_portnum_allowlist", "mqtt_node_blocklist",
        "mqtt_geo_filter_enabled", "mqtt_lat_min", "mqtt_lat_max",
        "mqtt_lng_min", "mqtt_lng_max"
    ]:
        setattr(config, attr, set())
    # packet_callback is a no‑op function.
    bridge = MqttBridge(config, packet_callback=lambda _: None)
    assert bridge is not None