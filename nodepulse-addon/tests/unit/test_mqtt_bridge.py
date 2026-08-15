"""
Unit tests for app/mqtt_bridge.py
"""
import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import asyncio

from app.mqtt_bridge import MqttBridge


def make_mock_config(**kwargs):
    """Create a mock config with all required MqttBridge attributes."""
    defaults = {
        "mqtt_enabled": True,
        "mqtt_address": "mqtt.example.com",
        "mqtt_port": 1883,
        "mqtt_username": "user",
        "mqtt_password": "pass",
        "mqtt_topic": "msh/US/2/json",
        "mqtt_forwarding_enabled": True,
        "mqtt_geo_filter_enabled": False,
        "mqtt_lat_min": -90.0,
        "mqtt_lat_max": 90.0,
        "mqtt_lng_min": -180.0,
        "mqtt_lng_max": 180.0,
        "mqtt_portnum_allowlist": [],
        "mqtt_node_blocklist": [],
    }
    defaults.update(kwargs)
    mock_config = Mock()
    for k, v in defaults.items():
        setattr(mock_config, k, v)
    return mock_config


class TestMqttBridgeInit:
    def test_init_enabled(self):
        mock_config = make_mock_config()
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        assert bridge.enabled is True
        assert bridge.address == "mqtt.example.com"
        assert bridge.port == 1883
        assert bridge.username == "user"
        assert bridge.password == "pass"
        assert bridge.topic == "msh/US/2/json"
        assert bridge.forwarding_enabled is True

    def test_init_disabled(self):
        mock_config = make_mock_config(mqtt_enabled=False)
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        assert bridge.enabled is False

    def test_init_empty_auth(self):
        mock_config = make_mock_config(mqtt_username="", mqtt_password="")
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        assert bridge.username is None
        assert bridge.password is None

    def test_init_filter_settings(self):
        mock_config = make_mock_config(
            mqtt_geo_filter_enabled=True,
            mqtt_lat_min=40.0,
            mqtt_lat_max=50.0,
            mqtt_lng_min=-120.0,
            mqtt_lng_max=-110.0,
            mqtt_portnum_allowlist=["TEXT_MESSAGE_APP", "POSITION_APP"],
            mqtt_node_blocklist=["!abcdef12"],
        )
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        assert bridge.geo_filter_enabled is True
        assert bridge.lat_min == 40.0
        assert bridge.lat_max == 50.0
        assert bridge.lng_min == -120.0
        assert bridge.lng_max == -110.0
        assert "TEXT_MESSAGE_APP" in bridge.portnum_allowlist
        assert "POSITION_APP" in bridge.portnum_allowlist
        assert "!abcdef12" in bridge.node_blocklist


class TestMqttBridgeStartStop:
    @pytest.mark.asyncio
    async def test_start_disabled(self):
        mock_config = make_mock_config(mqtt_enabled=False)
        bridge = MqttBridge(mock_config, Mock(), Mock())
        await bridge.start()
        assert bridge._task is None

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mock_config = make_mock_config()
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        with patch("app.mqtt_bridge.asyncio.create_task") as mock_create_task:
            await bridge.start()
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        mock_config = make_mock_config()
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        async def dummy_task():
            await asyncio.sleep(100)
        
        bridge._task = asyncio.create_task(dummy_task())
        await bridge.stop()
        assert bridge._task.cancelled()


class TestMqttBridgeFilters:
    def test_check_position_bounds_in_bounds(self):
        mock_config = make_mock_config(
            mqtt_geo_filter_enabled=True,
            mqtt_lat_min=40.0,
            mqtt_lat_max=50.0,
            mqtt_lng_min=-120.0,
            mqtt_lng_max=-110.0,
        )
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {"latitudeI": 450000000, "longitudeI": -1150000000}  # microdegrees
            }
        }
        result = bridge._check_position_bounds(packet)
        assert result is True

    def test_check_position_bounds_out_of_bounds(self):
        mock_config = make_mock_config(
            mqtt_geo_filter_enabled=True,
            mqtt_lat_min=40.0,
            mqtt_lat_max=50.0,
            mqtt_lng_min=-120.0,
            mqtt_lng_max=-110.0,
        )
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {"latitudeI": 100000000, "longitudeI": -100000000}
            }
        }
        result = bridge._check_position_bounds(packet)
        assert result is True

    def test_check_position_bounds_missing_coordinates(self):
        mock_config = make_mock_config(mqtt_geo_filter_enabled=True)
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"decoded": {"portnum": "POSITION_APP", "position": {}}}
        result = bridge._check_position_bounds(packet)
        assert result is True  # Allow if no coordinates

    def test_check_position_bounds_not_position(self):
        mock_config = make_mock_config(mqtt_geo_filter_enabled=True)
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"decoded": {"portnum": "TEXT_MESSAGE_APP"}}
        result = bridge._check_position_bounds(packet)
        assert result is True  # Non-position packets don't check bounds

    def test_apply_filters_node_blocklist(self):
        mock_config = make_mock_config(mqtt_node_blocklist=["!00abcdef"])
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"from": 11259375, "decoded": {"portnum": "TEXT_MESSAGE_APP"}}  # 0x00abcdef
        result = bridge._apply_filters(packet)
        assert result is False

    def test_apply_filters_portnum_allowlist(self):
        mock_config = make_mock_config(mqtt_portnum_allowlist=["TEXT_MESSAGE_APP"])
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"from": "!12345678", "decoded": {"portnum": "POSITION_APP"}}
        result = bridge._apply_filters(packet)
        assert result is False

    def test_apply_filters_allows_allowed_portnum(self):
        mock_config = make_mock_config(mqtt_portnum_allowlist=["TEXT_MESSAGE_APP", "POSITION_APP"])
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"from": "!12345678", "decoded": {"portnum": "TEXT_MESSAGE_APP"}}
        result = bridge._apply_filters(packet)
        assert result is True

    def test_apply_filters_allows_unencrypted(self):
        mock_config = make_mock_config()
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        packet = {"from": "!12345678", "decoded": {"portnum": "UNKNOWN_APP"}}
        result = bridge._apply_filters(packet)
        assert result is True  # No allowlist = allow all


class TestMqttBridgeGeoCache:
    def test_geo_cache_remembers_bounds(self):
        mock_config = make_mock_config(
            mqtt_geo_filter_enabled=True,
            mqtt_lat_min=40.0,
            mqtt_lat_max=50.0,
            mqtt_lng_min=-120.0,
            mqtt_lng_max=-110.0,
        )
        bridge = MqttBridge(mock_config, Mock(), Mock())
        
        # First packet with position - in bounds
        packet1 = {
            "from": 11259375,  # !00abcdef
            "decoded": {"portnum": "POSITION_APP", "position": {"latitudeI": 450000000, "longitudeI": -1150000000}}
        }
        bridge._apply_filters(packet1)
        
        # Second packet without position - should use cached result
        packet2 = {"from": 11259375, "decoded": {"portnum": "TEXT_MESSAGE_APP"}}
        result = bridge._apply_filters(packet2)
        assert result is True
        
        # Third packet - out of bounds
        packet3 = {
            "from": 123456789,  # different node
            "decoded": {"portnum": "POSITION_APP", "position": {"latitudeI": 100000000, "longitudeI": -100000000}}
        }
        bridge._apply_filters(packet3)
        
        # Fourth packet without position - should use cached out-of-bounds
        packet4 = {"from": 123456789, "decoded": {"portnum": "TEXT_MESSAGE_APP"}}
        result = bridge._apply_filters(packet4)
        assert result is False