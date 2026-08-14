"""
Unit tests for app/config.py
"""
import os
import json
import pytest
import tempfile
from app.config import Config, load_config, parse_int_list, resolve_target, CONNECTION_TYPE_DIRECT, CONNECTION_TYPE_PROXY


def test_parse_int_list_string():
    """Test parsing comma-separated string of channel indices."""
    result = parse_int_list("0, 1, 2", [0])
    assert result == [0, 1, 2]


def test_parse_int_list_whitespace():
    """Test parsing whitespace-separated string of channel indices."""
    result = parse_int_list("0 1 2", [0])
    assert result == [0, 1, 2]


def test_parse_int_list_list():
    """Test parsing list of channel indices."""
    result = parse_int_list([0, 1, 2], [0])
    assert result == [0, 1, 2]


def test_parse_int_list_with_none():
    """Test parsing list with None values."""
    result = parse_int_list([0, None, 2], [0])
    assert result == [0, 2]


def test_parse_int_list_empty_string():
    """Test parsing empty string returns default."""
    result = parse_int_list("", [0])
    assert result == [0]


def test_parse_int_list_none():
    """Test parsing None returns default."""
    result = parse_int_list(None, [0])
    assert result == [0]


def test_parse_int_list_invalid_string():
    """Test parsing invalid string raises RuntimeError."""
    with pytest.raises(RuntimeError):
        parse_int_list("invalid", [0])


def test_parse_int_list_invalid_list_item():
    """Test parsing list with invalid item raises RuntimeError."""
    with pytest.raises(RuntimeError):
        parse_int_list([0, "invalid"], [0])


def test_resolve_target_direct_mode():
    """Test resolving target in direct connection mode."""
    config = Config(
        log_level="INFO",
        connection_type=CONNECTION_TYPE_DIRECT,
        meshtastic_host="192.168.1.100",
        meshtastic_port=4403,
        proxy_host=None,
        proxy_port=4403,
        access_key=None,
        scan_interval=30
    )
    host, port, mode = resolve_target(config)
    assert host == "192.168.1.100"
    assert port == 4403
    assert mode == CONNECTION_TYPE_DIRECT


def test_resolve_target_proxy_mode():
    """Test resolving target in proxy connection mode."""
    config = Config(
        log_level="INFO",
        connection_type=CONNECTION_TYPE_PROXY,
        meshtastic_host="192.168.1.100",
        meshtastic_port=4403,
        proxy_host="192.168.1.50",
        proxy_port=4403,
        access_key=None,
        scan_interval=30
    )
    host, port, mode = resolve_target(config)
    assert host == "192.168.1.50"
    assert port == 4403
    assert mode == CONNECTION_TYPE_PROXY


def test_resolve_target_proxy_mode_missing_host():
    """Test proxy mode without proxy_host raises RuntimeError."""
    config = Config(
        log_level="INFO",
        connection_type=CONNECTION_TYPE_PROXY,
        meshtastic_host="192.168.1.100",
        meshtastic_port=4403,
        proxy_host=None,
        proxy_port=4403,
        access_key=None,
        scan_interval=30
    )
    with pytest.raises(RuntimeError):
        resolve_target(config)


def test_load_config_minimal():
    """Test loading minimal valid configuration."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "log_level": "info",
            "connection_type": "direct",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403
        }, f)
        temp_path = f.name

    try:
        # Temporarily set options path
        import app.config as config_module
        original_options = config_module._OPTIONS_FILE
        config_module._OPTIONS_FILE = temp_path

        config = load_config()
        
        assert config.log_level == "INFO"
        assert config.connection_type == CONNECTION_TYPE_DIRECT
        assert config.meshtastic_host == "localhost"
        assert config.meshtastic_port == 4403
        assert config.scan_interval == 30  # default
        assert config.mqtt_enabled is False  # default
    finally:
        os.unlink(temp_path)
        config_module._OPTIONS_FILE = original_options


def test_load_config_with_mqtt_settings():
    """Test loading configuration with MQTT settings."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "log_level": "debug",
            "connection_type": "direct",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403,
            "mqtt_enabled": True,
            "mqtt_address": "mqtt.example.com",
            "mqtt_port": 1883,
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "mqtt_topic": "msh/custom",
            "mqtt_geo_filter_enabled": True,
            "mqtt_lat_min": 40.0,
            "mqtt_lat_max": 41.0,
            "mqtt_lng_min": -74.0,
            "mqtt_lng_max": -73.0
        }, f)
        temp_path = f.name

    try:
        import app.config as config_module
        original_options = config_module._OPTIONS_FILE
        config_module._OPTIONS_FILE = temp_path

        config = load_config()
        
        assert config.mqtt_enabled is True
        assert config.mqtt_address == "mqtt.example.com"
        assert config.mqtt_port == 1883
        assert config.mqtt_username == "user"
        assert config.mqtt_password == "pass"
        assert config.mqtt_topic == "msh/custom"
        assert config.mqtt_geo_filter_enabled is True
        assert config.mqtt_lat_min == 40.0
        assert config.mqtt_lat_max == 41.0
        assert config.mqtt_lng_min == -74.0
        assert config.mqtt_lng_max == -73.0
    finally:
        os.unlink(temp_path)
        config_module._OPTIONS_FILE = original_options


def test_load_config_invalid_geo_filter():
    """Test that invalid geo-filter bounds are corrected."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "log_level": "info",
            "connection_type": "direct",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403,
            "mqtt_geo_filter_enabled": True,
            "mqtt_lat_min": 41.0,  # Invalid: min >= max
            "mqtt_lat_max": 40.0,
            "mqtt_lng_min": -73.0,
            "mqtt_lng_max": -74.0
        }, f)
        temp_path = f.name

    try:
        import app.config as config_module
        original_options = config_module._OPTIONS_FILE
        config_module._OPTIONS_FILE = temp_path

        config = load_config()
        
        # Should be disabled due to invalid bounds
        assert config.mqtt_geo_filter_enabled is False
    finally:
        os.unlink(temp_path)
        config_module._OPTIONS_FILE = original_options


def test_load_config_with_telegram_settings():
    """Test loading configuration with Telegram settings."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "log_level": "info",
            "connection_type": "direct",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403,
            "telegram_enabled": True,
            "telegram_bot_token": "test_token",
            "telegram_chat_id": "123456",
            "telegram_authorized_chat_ids": ["123456", "789012"],
            "telegram_forward_channels": "0, 1",
            "telegram_forward_dms": True,
            "telegram_allow_commands": True
        }, f)
        temp_path = f.name

    try:
        import app.config as config_module
        original_options = config_module._OPTIONS_FILE
        config_module._OPTIONS_FILE = temp_path

        config = load_config()
        
        assert config.telegram_enabled is True
        assert config.telegram_bot_token == "test_token"
        assert config.telegram_chat_id == "123456"
        assert "123456" in config.telegram_authorized_chat_ids
        assert "789012" in config.telegram_authorized_chat_ids
        assert config.telegram_forward_channels == [0, 1]
        assert config.telegram_forward_dms is True
        assert config.telegram_allow_commands is True
    finally:
        os.unlink(temp_path)
        config_module._OPTIONS_FILE = original_options


def test_load_config_invalid_connection_type():
    """Test that invalid connection type raises RuntimeError."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "log_level": "info",
            "connection_type": "invalid",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403
        }, f)
        temp_path = f.name

    try:
        import app.config as config_module
        original_options = config_module._OPTIONS_FILE
        config_module._OPTIONS_FILE = temp_path

        with pytest.raises(RuntimeError):
            load_config()
    finally:
        os.unlink(temp_path)
        config_module._OPTIONS_FILE = original_options
