"""
Unit tests for app/mqtt_bridge.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add app to path so we can import it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app")))

import app.mqtt_bridge as mqtt_bridge


def test_mqtt_bridge_initialization():
    """Test MQTT bridge initialization with config."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = True
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    assert bridge.enabled is True
    assert bridge.address == "mqtt.example.com"
    assert bridge.port == 1883
    assert bridge.username == "user"
    assert bridge.password == "pass"
    assert bridge.topic == "msh/+"
    assert bridge.forwarding_enabled is True


def test_mqtt_bridge_disabled():
    """Test MQTT bridge when disabled in config."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = False
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = ""
    mock_config.mqtt_password = ""
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    assert bridge.enabled is False


def test_mqtt_bridge_empty_credentials():
    """Test that empty strings are converted to None for credentials."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = ""
    mock_config.mqtt_password = ""
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    assert bridge.username is None
    assert bridge.password is None


def test_mqtt_bridge_geo_filter_settings():
    """Test MQTT bridge with geo filter enabled."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = True
    mock_config.mqtt_lat_min = 40.0
    mock_config.mqtt_lat_max = 41.0
    mock_config.mqtt_lng_min = -74.0
    mock_config.mqtt_lng_max = -73.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    assert bridge.geo_filter_enabled is True
    assert bridge.lat_min == 40.0
    assert bridge.lat_max == 41.0
    assert bridge.lng_min == -74.0
    assert bridge.lng_max == -73.0


def test_mqtt_bridge_filter_settings():
    """Test MQTT bridge with portnum allowlist and node blocklist."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = ["TEXT_MESSAGE_APP", "POSITION_APP"]
    mock_config.mqtt_node_blocklist = ["!12345678", "!87654321"]
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    assert "TEXT_MESSAGE_APP" in bridge.portnum_allowlist
    assert "!12345678" in bridge.node_blocklist


def test_check_position_bounds_in_bounds():
    """Test position bounds check for coordinates within bounds."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = True
    mock_config.mqtt_lat_min = 40.0
    mock_config.mqtt_lat_max = 41.0
    mock_config.mqtt_lng_min = -74.0
    mock_config.mqtt_lng_max = -73.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Position within bounds (New York City area)
    position = {
        "latitudeI": 407100000,  # 40.71 * 1e7
        "longitudeI": -740000000  # -74.0 * 1e7
    }
    
    result = bridge._check_position_bounds(position)
    assert result is True


def test_check_position_bounds_out_of_bounds():
    """Test position bounds check for coordinates outside bounds."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = True
    mock_config.mqtt_lat_min = 40.0
    mock_config.mqtt_lat_max = 41.0
    mock_config.mqtt_lng_min = -74.0
    mock_config.mqtt_lng_max = -73.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Position outside bounds (Los Angeles area)
    position = {
        "latitudeI": 341000000,  # 34.1 * 1e7
        "longitudeI": -1182000000  # -118.2 * 1e7
    }
    
    result = bridge._check_position_bounds(position)
    assert result is False


def test_check_position_bounds_missing_coordinates():
    """Test position bounds check with missing coordinates."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = True
    mock_config.mqtt_lat_min = 40.0
    mock_config.mqtt_lat_max = 41.0
    mock_config.mqtt_lng_min = -74.0
    mock_config.mqtt_lng_max = -73.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Position with missing coordinates
    position = {}
    
    result = bridge._check_position_bounds(position)
    assert result is True  # Should allow through when coordinates are missing


def test_apply_filters_node_blocklist():
    """Test filter pipeline with node blocklist."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = ["!12345678"]
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Packet from blocked node
    packet_dict = {
        "from": 0x12345678,  # Hex node number
        "decoded": {
            "portnum": "TEXT_MESSAGE_APP"
        }
    }
    
    result = bridge._apply_filters(packet_dict)
    assert result is False


def test_apply_filters_portnum_allowlist():
    """Test filter pipeline with portnum allowlist."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = ["TEXT_MESSAGE_APP"]
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Packet with non-allowed portnum
    packet_dict = {
        "from": 0x87654321,
        "decoded": {
            "portnum": "POSITION_APP"
        }
    }
    
    result = bridge._apply_filters(packet_dict)
    assert result is False


def test_apply_filters_allows_allowed_portnum():
    """Test filter pipeline allows packet with allowed portnum."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = ["TEXT_MESSAGE_APP"]
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Packet with allowed portnum
    packet_dict = {
        "from": 0x87654321,
        "decoded": {
            "portnum": "TEXT_MESSAGE_APP"
        }
    }
    
    result = bridge._apply_filters(packet_dict)
    assert result is True


def test_apply_filters_allows_unencrypted():
    """Test filter pipeline allows unencrypted packets (no decoded field)."""
    mock_config = MagicMock()
    mock_config.mqtt_enabled = True
    mock_config.mqtt_address = "mqtt.example.com"
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = "user"
    mock_config.mqtt_password = "pass"
    mock_config.mqtt_topic = "msh/+"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = 0.0
    mock_config.mqtt_lat_max = 0.0
    mock_config.mqtt_lng_min = 0.0
    mock_config.mqtt_lng_max = 0.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    
    packet_callback = MagicMock()
    forward_callback = MagicMock()
    
    bridge = mqtt_bridge.MqttBridge(mock_config, packet_callback, forward_callback)
    
    # Unencrypted packet (no decoded field)
    packet_dict = {
        "from": 0x87654321,
    }
    
    result = bridge._apply_filters(packet_dict)
    assert result is True
