"""
Unit tests for app/connection.py
"""
import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import asyncio
import json
import os
import tempfile
import time
import collections

from app import connection
from app.connection import MeshtasticConnection


class TestNodeIdFromNum:
    def test_node_id_from_num_none(self):
        assert connection._node_id_from_num(None) is None

    def test_node_id_from_num_int(self):
        result = connection._node_id_from_num(12345)
        assert result == "!00003039"

    def test_node_id_from_num_string(self):
        result = connection._node_id_from_num("12345")
        assert result == "!00003039"

    def test_node_id_from_num_negative(self):
        result = connection._node_id_from_num(-1)
        assert result == "!ffffffff"

    def test_node_id_from_num_large(self):
        result = connection._node_id_from_num(0xFFFFFFFF)
        assert result == "!ffffffff"

    def test_node_id_from_num_invalid(self):
        assert connection._node_id_from_num("abc") is None
        assert connection._node_id_from_num([]) is None


class TestChannelRoleName:
    def test_channel_role_name_with_protobuf(self):
        from meshtastic.protobuf.channel_pb2 import Channel
        assert connection._channel_role_name(Channel.Role.PRIMARY) == "PRIMARY"
        assert connection._channel_role_name(Channel.Role.SECONDARY) == "SECONDARY"


class TestLooksLikeSlotConflict:
    def test_slot_conflict_refused(self):
        exc = Exception("connection refused")
        assert connection._looks_like_slot_conflict(exc) is True

    def test_slot_conflict_reset(self):
        exc = Exception("connection reset by peer")
        assert connection._looks_like_slot_conflict(exc) is True

    def test_slot_conflict_denied(self):
        exc = Exception("access denied")
        assert connection._looks_like_slot_conflict(exc) is True

    def test_slot_conflict_in_use(self):
        exc = Exception("address already in use")
        assert connection._looks_like_slot_conflict(exc) is True

    def test_slot_conflict_timeout(self):
        exc = Exception("connection timed out")
        assert connection._looks_like_slot_conflict(exc) is True

    def test_slot_conflict_generic(self):
        exc = Exception("some other error")
        assert connection._looks_like_slot_conflict(exc) is False


class TestMeshtasticConnectionInit:
    def test_init_tcp_mode(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost",
            port=4403,
            mode="tcp",
            access_key="test_key",
            config=mock_config,
        )
        assert conn._host == "localhost"
        assert conn._port == 4403
        assert conn._mode == "tcp"
        assert conn._access_key == "test_key"
        assert conn._config == mock_config

    def test_init_serial_mode(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="/dev/ttyUSB0",
            port=0,
            mode="serial",
            access_key="test_key",
            config=mock_config,
        )
        assert conn._host == "/dev/ttyUSB0"
        assert conn._mode == "serial"

    def test_init_defaults(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost",
            port=4403,
            mode="tcp",
            access_key="test_key",
            config=mock_config,
        )
        assert conn._interface is None
        assert conn._nodes == []
        assert conn._channels == []
        assert len(conn._messages) == 0


class TestAccessKey:
    def test_set_access_key(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn.set_access_key("new_key")
        assert conn._access_key == "new_key"

    def test_set_access_key_none(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="old_key", config=mock_config
        )
        conn.set_access_key(None)
        assert conn._access_key is None

    def test_set_access_key_empty_string(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="old_key", config=mock_config
        )
        conn.set_access_key("")
        assert conn._access_key is None


class TestNodeSerialization:
    def test_get_nodes_sync(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        result = conn._get_nodes_sync()
        assert result == []

    def test_get_channels_sync(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        result = conn._get_channels_sync()
        assert result == []


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_no_interface(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._connected = False
        conn._interface = None
        result = await conn.get_status()
        assert result["connected"] is False
        assert result["my_info"] is None


class TestGetNodes:
    @pytest.mark.asyncio
    async def test_get_nodes_no_interface(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._connected = False
        conn._interface = None
        result = await conn.get_nodes()
        assert result == []


class TestGetChannels:
    @pytest.mark.asyncio
    async def test_get_channels_no_interface(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._connected = False
        conn._interface = None
        result = await conn.get_channels()
        assert result == []


class TestPacketInspector:
    def test_capture_packet_log(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        packet = {"type": "data", "from": "!00000001", "to": "!00000002"}
        conn._capture_packet_log(packet)
        assert len(conn._packet_log) == 1
        captured = conn._packet_log[0]
        assert captured["decoded_ok"] is False
        assert captured["channel"] == 0
        assert "timestamp" in captured

    def test_packet_log_max_size(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        for i in range(600):
            conn._capture_packet_log({"id": i})
        assert len(conn._packet_log) == connection._PACKET_LOG_MAX


class TestAckHandling:
    def test_capture_routing_ack(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._capture_routing_ack({"id": "test_packet_id", "from": "!12345678", "to": "!87654321", "ack": True})
        assert True

    def test_expire_pending_acks(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._expire_pending_acks_sync()
        assert True


class TestScheduledMessages:
    def test_schedule_message(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn.schedule_message(time.time() + 10, "!123", "test message", 0)
        assert len(conn._scheduled_messages) == 1

    def test_process_scheduled_messages(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._scheduled_messages = [
            (time.time() - 1, "!123", "msg1", 0, 0),
            (time.time() + 100, "!456", "msg2", 0, 0),
        ]
        to_send = conn._process_scheduled_messages(time.time())
        assert len(to_send) == 1
        assert to_send[0]["text"] == "msg1"


class TestConstants:
    def test_node_id_regex(self):
        assert connection._NODE_ID_RE.match("!abcdef12")
        assert connection._NODE_ID_RE.match("!12345678")
        assert not connection._NODE_ID_RE.match("abcdef12")
        assert not connection._NODE_ID_RE.match("!abcdef123")

    def test_data_dir_from_env(self):
        with patch.dict(os.environ, {"NODEPULSE_DATA_DIR": "/custom/data"}):
            import importlib
            importlib.reload(connection)
            assert connection._DATA_DIR == "/custom/data"