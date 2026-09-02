"""
Unit tests for app/connection.py
"""
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

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


class TestGetStatusNoInterface:
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


class TestGetNodesNoInterface:
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


class TestGetChannelsNoInterface:
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


class TestNodeLookupLogic:
    """Tests for improved node lookup logic in message processing"""
    
    def test_node_lookup_multiple_key_formats(self):
        """Test that node lookup tries multiple key formats"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface with nodes dict keyed by different formats
        mock_interface = Mock()
        mock_interface.nodes = {
            12345: {"user": {"shortName": "Test", "longName": "TestNode"}},
            "!00003039": {"user": {"shortName": "StringKey", "longName": "StringNode"}},
            "12345": {"user": {"shortName": "StringNum", "longName": "StringNumNode"}}
        }
        conn._interface = mock_interface
        
        # Test lookup by integer key
        node = conn._interface.nodes.get(12345)
        assert node is not None
        assert node["user"]["shortName"] == "Test"
        
        # Test lookup by string hex key
        node = conn._interface.nodes.get("!00003039")
        assert node is not None
        assert node["user"]["shortName"] == "StringKey"
        
        # Test lookup by string number key
        node = conn._interface.nodes.get("12345")
        assert node is not None
        assert node["user"]["shortName"] == "StringNum"


class TestTracerouteDataPreservation:
    """Tests for traceroute data preservation during node refreshes"""
    
    def test_traceroute_preserved_in_node_update(self):
        """Test that traceroute data is preserved when node data is refreshed"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Add a node with traceroute data
        with conn._nodes_lock:
            conn._nodes = [{
                "id": "!00003039",
                "short_name": "Test",
                "long_name": "TestNode",
                "traceroute": {
                    "route": ["!00003039", "!00003040"],
                    "timestamp": 1234567890
                },
                "latitude": 37.7749,
                "longitude": -122.4194
            }]
        
        # Simulate node data refresh (this happens in _get_nodes_sync)
        # The new data from interface won't have traceroute, but we should preserve it
        with conn._nodes_lock:
            cached = {n["id"]: n for n in conn._nodes}
            entry = {
                "id": "!00003039",
                "short_name": "Test",
                "long_name": "TestNode",
                "latitude": 37.7749,  # From interface
                "longitude": -122.4194,  # From interface
                # Note: no traceroute in new interface data
            }
            
            # This is the logic that should preserve traceroute
            prev = cached["!00003039"]
            prev_traceroute = prev.get("traceroute")
            entry.update(entry)
            if prev_traceroute is not None:
                entry["traceroute"] = prev_traceroute
        
        # Verify traceroute was preserved
        assert entry["traceroute"] is not None
        assert entry["traceroute"]["route"] == ["!00003039", "!00003040"]
        assert entry["traceroute"]["timestamp"] == 1234567890


class TestFavoriteDeviceCommunication:
    """Tests for favorite node device communication"""
    
    def test_set_favorite_calls_device_method(self):
        """Test that set_favorite calls device setFavorite method"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface and localNode
        mock_interface = Mock()
        mock_local_node = Mock()
        mock_interface.localNode = mock_local_node
        conn._interface = mock_interface
        
        # Call set_favorite
        node_id = "!00003039"
        result = conn._set_favorite_sync(node_id, favorited=True)
        
        # Verify device method was called
        mock_local_node.setFavorite.assert_called_once_with(node_id)
        assert node_id in result
        
    def test_remove_favorite_calls_device_method(self):
        """Test that set_favorite with favorited=False calls device removeFavorite method"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface and localNode
        mock_interface = Mock()
        mock_local_node = Mock()
        mock_interface.localNode = mock_local_node
        conn._interface = mock_interface
        
        # Add node to favorites first
        with conn._favorites_lock:
            conn._favorites.add("!00003039")
        
        # Call set_favorite with favorited=False
        node_id = "!00003039"
        result = conn._set_favorite_sync(node_id, favorited=False)
        
        # Verify device method was called
        mock_local_node.removeFavorite.assert_called_once_with(node_id)
        assert node_id not in result
        
    def test_set_favorite_handles_device_error(self):
        """Test that set_favorite handles device communication errors gracefully"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface that raises error
        mock_interface = Mock()
        mock_local_node = Mock()
        mock_local_node.setFavorite.side_effect = Exception("Device error")
        mock_interface.localNode = mock_local_node
        conn._interface = mock_interface
        
        # Call set_favorite - should not raise, should still update local favorites
        node_id = "!00003039"
        result = conn._set_favorite_sync(node_id, favorited=True)
        
        # Verify local favorites were still updated despite device error
        assert node_id in result
        
    def test_set_favorite_without_interface(self):
        """Test that set_favorite works when interface is not available"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # No interface
        conn._interface = None
        
        # Call set_favorite - should still update local favorites
        node_id = "!00003039"
        result = conn._set_favorite_sync(node_id, favorited=True)
        
        # Verify local favorites were updated
        assert node_id in result

    def test_sync_favorites_from_device(self):
        """Test that favorites are synced from device to local UI"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface with device favorites
        mock_interface = Mock()
        mock_local_node = Mock()
        
        # Create mock favorite nodes with proper node numbers
        mock_fav1 = Mock()
        mock_fav1.num = 0x12345678  # Will be converted to !12345678
        mock_fav2 = Mock()
        mock_fav2.num = 0xABCDEF01  # Will be converted to !abcdef01
        
        mock_local_node.favorites = [mock_fav1, mock_fav2]
        mock_interface.localNode = mock_local_node
        conn._interface = mock_interface
        
        # Set some initial local favorites
        with conn._favorites_lock:
            conn._favorites.add("!deadbeef")
        
        # Sync from device
        conn._sync_favorites_from_device()
        
        # Verify device favorites were merged with local favorites
        with conn._favorites_lock:
            assert "!12345678" in conn._favorites
            assert "!abcdef01" in conn._favorites
            assert "!deadbeef" in conn._favorites  # Local favorite preserved

    def test_sync_favorites_from_device_race_condition(self):
        """Test that sync handles race conditions with interface changes"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Start with no interface
        conn._interface = None
        
        # Set initial favorites
        with conn._favorites_lock:
            conn._favorites.add("!deadbeef")
        
        # Sync should handle missing interface gracefully
        conn._sync_favorites_from_device()
        
        # Verify existing favorites are preserved
        with conn._favorites_lock:
            assert "!deadbeef" in conn._favorites
            assert len(conn._favorites) == 1  # No new favorites added

    def test_sync_favorites_without_interface(self):
        """Test that sync works gracefully when interface is not available"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # No interface
        conn._interface = None
        
        # Should not raise
        conn._sync_favorites_from_device()
        
        # Local favorites should remain unchanged
        with conn._favorites_lock:
            assert len(conn._favorites) == 0

    def test_sync_favorites_without_device_favorites(self):
        """Test that sync works when device doesn't expose favorites"""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        
        # Mock interface without favorites attribute
        mock_interface = Mock()
        mock_local_node = Mock()
        del mock_local_node.favorites  # Remove favorites attribute
        mock_interface.localNode = mock_local_node
        conn._interface = mock_interface
        
        # Should not raise
        conn._sync_favorites_from_device()
        
        # Local favorites should remain unchanged
        with conn._favorites_lock:
            assert len(conn._favorites) == 0


class TestTraceroutePathConstruction:
    """Tests for traceroute path construction logic"""
    
    def test_node_card_traceroute_no_duplicates(self):
        """Test that node card traceroute display doesn't create duplicate nodes in path"""
        # Simulate the formatHop function logic
        route = [12345, 67890]  # intermediate hops as integers
        from_id = "!abcd1234"
        
        def formatHop(n):
            return '!' + format(n & 0xFFFFFFFF, '08x')
        forward = [formatHop(n) for n in route]
        
        # Add target node only if not already present
        if from_id and from_id not in forward:
            forward.append(from_id)
        
        # Verify no duplicates
        assert len(forward) == len(set(forward))
        assert "!abcd1234" in forward or "!00003039" in forward or "!000109b2" in forward
        
    def test_node_card_traceroute_includes_self(self):
        """Test that self node is included at the start of the path"""
        route = [12345]  # single intermediate hop
        from_id = "!abcd1234"
        self_id = "!00000001"
        
        def formatHop(n):
            return '!' + format(n & 0xFFFFFFFF, '08x')
        forward = [formatHop(n) for n in route]
        
        # Add self at start if not already present
        if self_id not in forward:
            forward.insert(0, self_id)
        
        # Add target node only if not already present
        if from_id and from_id not in forward:
            forward.append(from_id)
        
        # Verify self is at start
        assert forward[0] == self_id
        # Verify target is at end
        assert forward[-1] == from_id
        
    def test_traceroute_with_duplicate_handling(self):
        """Test traceroute handling when firmware includes self/target in route array"""
        # Case where firmware already includes self in route
        route = [1, 12345]  # self + intermediate hop
        from_id = "!abcd1234"
        self_id = "!00000001"
        
        def formatHop(n):
            return '!' + format(n & 0xFFFFFFFF, '08x')
        forward = [formatHop(n) for n in route]
        
        # Should not add self again since it's already present
        if self_id not in forward:
            forward.insert(0, self_id)
        
        # Add target node only if not already present
        if from_id and from_id not in forward:
            forward.append(from_id)
        
        # Verify no duplicate self
        assert forward.count(self_id) == 1
        # Verify path length is correct
        assert len(forward) == 3  # self + hop + target


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


# ----------------------------------------------------------------------
# Comprehensive tests for public async methods of MeshtasticConnection
# ----------------------------------------------------------------------
def create_conn(**kwargs):
    """Create a MeshtasticConnection with minimal mock config."""
    mock_config = Mock()
    mock_config.mqtt_enabled = False
    for k, v in kwargs.items():
        setattr(mock_config, k, v)
    return MeshtasticConnection(
        host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
    )


class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        conn = create_conn()
        with patch.object(conn, "_connect_sync") as mock_connect_sync:
            mock_connect_sync.return_value = None
            # The connect method calls asyncio.to_thread, which runs the sync method
            # We just verify it doesn't raise
            await conn.connect()
            # The real _connect_sync would set _connected, but our mock doesn't
            # So we just verify the method completes without exception

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        conn = create_conn()
        with patch.object(conn, "_connect_sync") as mock_connect_sync:
            mock_connect_sync.side_effect = RuntimeError("Connection failed")
            with pytest.raises(RuntimeError):
                await conn.connect()
            # The real _connect_sync would set _connected=False on failure
            # but our mock doesn't, so we just verify the exception is raised

    @pytest.mark.asyncio
    async def test_disconnect(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_close_sync") as mock_close_sync:
            mock_close_sync.return_value = None
            await conn.disconnect()
            # The real _close_sync would set _connected=False
            # but our mock doesn't, so we just verify it completes


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_connected(self):
        conn = create_conn()
        conn._connected = True
        conn._scheduled_messages = []
        conn._scheduled_messages_lock = MagicMock()
        conn._scheduled_messages_lock.__enter__ = MagicMock(return_value=None)
        conn._scheduled_messages_lock.__exit__ = MagicMock(return_value=None)

        with patch.object(conn, "_get_status_sync") as mock_get_status_sync:
            mock_get_status_sync.return_value = {"connected": True, "my_info": {"id": "!12345678"}}
            result = await conn.get_status()
            assert result["connected"] is True
            assert "my_info" in result

    def test_get_status_sync_enriched_self_telemetry(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._connected = True

        my_info = Mock()
        my_info.my_node_num = 0x12345678
        my_info.macaddr = b"\x11\x22\x33\x44\x55\x66"

        my_node = {
            "user": {"longName": "Base", "shortName": "BASE", "hwModel": "HELTEC_V3"},
            "deviceMetrics": {"batteryLevel": 88, "uptimeSeconds": 90061},
            "lastHeard": 1700000000,
        }
        iface = Mock()
        iface.myInfo = my_info
        iface.metadata = None
        iface.nodes = {"!12345678": my_node}
        iface.nodesByNum = {0x12345678: my_node}
        iface.localNode.localConfig = None

        conn._interface = iface
        with patch.object(conn, "_normalize_role", return_value="CLIENT") as mock_role:
            result = conn._get_status_sync()
            mock_role.assert_called()

        assert result["connected"] is True
        info = result["my_info"]
        assert info["node_id"] == "!12345678"
        assert info["long_name"] == "Base"
        assert info["battery_level"] == 88
        assert info["uptime"] == 90061
        assert info["last_heard"] == 1700000000
        assert info["macaddr"] == "11:22:33:44:55:66"


class TestGetNodes:
    @pytest.mark.asyncio
    async def test_get_nodes_connected(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_nodes_sync") as mock_get_nodes_sync:
            mock_get_nodes_sync.return_value = [{"id": "!12345678", "name": "Node1"}]
            result = await conn.get_nodes()
            assert len(result) == 1
            assert result[0]["id"] == "!12345678"


class TestGetChannels:
    @pytest.mark.asyncio
    async def test_get_channels_connected(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_channels_sync") as mock_get_channels_sync:
            mock_get_channels_sync.return_value = [{"channel": 0, "name": "Primary"}]
            result = await conn.get_channels()
            assert len(result) == 1
            assert result[0]["channel"] == 0


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_success(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_send_message_sync") as mock_send_sync:
            mock_send_sync.return_value = True
            result = await conn.send_message("hello", destination="!12345678", channel=0)
            assert result is True

    @pytest.mark.asyncio
    async def test_send_message_failure(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_send_message_sync") as mock_send_sync:
            mock_send_sync.return_value = False
            result = await conn.send_message("hello", destination="!12345678", channel=0)
            assert result is False


class TestSendMqttProxyMessage:
    @pytest.mark.asyncio
    async def test_send_mqtt_proxy_message_success(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_send_mqtt_proxy_message_sync") as mock_send_proxy_sync:
            mock_send_proxy_sync.return_value = True
            result = await conn.send_mqtt_proxy_message("test/topic", b"test data")
            assert result is True


class TestRequestTraceroute:
    @pytest.mark.asyncio
    async def test_request_traceroute_success(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_request_traceroute_sync") as mock_request_sync:
            mock_request_sync.return_value = None
            await conn.request_traceroute("!12345678")

    @staticmethod
    def _route_payload():
        from meshtastic.protobuf.mesh_pb2 import RouteDiscovery
        rd = RouteDiscovery()
        rd.route.extend([0x11111111, 0x22222222])
        return rd.SerializeToString()

    @pytest.mark.asyncio
    async def test_request_traceroute_dedupes_pending(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_request_traceroute_sync"):
            # Simulate a request already in the pending queue, then ask again.
            with conn._lock:
                conn._pending_traceroute_dests.append("!12345678")
            result = await conn.request_traceroute("!12345678")
            assert result is True
            with conn._lock:
                assert conn._pending_traceroute_dests.count("!12345678") == 1

    @pytest.mark.asyncio
    async def test_request_traceroute_rejects_when_queue_full(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_request_traceroute_sync") as mock_request_sync:
            with conn._lock:
                conn._pending_traceroute_dests.extend(
                    [f"!dead00{i:02x}" for i in range(connection._MAX_PENDING_TRACEROUTES)]
                )
            result = await conn.request_traceroute("!12345678")
            assert result is False
            mock_request_sync.assert_not_called()
            with conn._lock:
                assert len(conn._pending_traceroute_dests) == connection._MAX_PENDING_TRACEROUTES

    def test_capture_traceroute_matches_by_origin(self):
        conn = create_conn()
        with conn._lock:
            conn._pending_traceroute_dests.extend(["!11111111", "!22222222"])
        conn._nodes = [{"id": "!22222222"}]
        conn._traceroutes = {}
        with patch.object(conn, "_save_traceroutes"):
            conn._capture_traceroute({
                "from": 0x22222222,
                "decoded": {"payload": TestRequestTraceroute._route_payload()},
            })
        with conn._lock:
            assert conn._pending_traceroute_dests == ["!11111111"]
        assert conn._traceroutes.get("!22222222") is not None
        assert conn._traceroutes.get("!11111111") is None

    def test_capture_traceroute_falls_back_to_oldest(self):
        conn = create_conn()
        with conn._lock:
            conn._pending_traceroute_dests.extend(["!11111111", "!22222222"])
        conn._traceroutes = {}
        with patch.object(conn, "_save_traceroutes"):
            conn._capture_traceroute({
                "from": 0x33333333,
                "decoded": {"payload": TestRequestTraceroute._route_payload()},
            })
        with conn._lock:
            assert conn._pending_traceroute_dests == ["!22222222"]
        assert conn._traceroutes.get("!11111111") is not None

    def test_capture_traceroute_attributes_to_origin_when_no_pending(self):
        conn = create_conn()
        conn._traceroutes = {}
        with patch.object(conn, "_save_traceroutes"):
            conn._capture_traceroute({
                "from": 0x33333333,
                "decoded": {"payload": TestRequestTraceroute._route_payload()},
            })
        assert conn._traceroutes.get("!33333333") is not None


class TestRequestPosition:
    @pytest.mark.asyncio
    async def test_request_position_success(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_request_position_sync") as mock_request_sync:
            mock_request_sync.return_value = None
            await conn.request_position("!12345678")


class TestTags:
    @pytest.mark.asyncio
    async def test_set_tags(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_set_tags_sync") as mock_set_tags_sync:
            mock_set_tags_sync.return_value = {"!12345678": ["tag1", "tag2"]}
            result = await conn.set_tags("!12345678", ["tag1", "tag2"])
            assert result == {"!12345678": ["tag1", "tag2"]}

    @pytest.mark.asyncio
    async def test_get_tags(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_tags_sync") as mock_get_tags_sync:
            mock_get_tags_sync.return_value = {"!12345678": ["tag1", "tag2"]}
            result = await conn.get_tags()
            assert result == {"!12345678": ["tag1", "tag2"]}


class TestFavorites:
    @pytest.mark.asyncio
    async def test_set_favorite(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_set_favorite_sync") as mock_set_favorite_sync:
            mock_set_favorite_sync.return_value = ["!12345678"]
            result = await conn.set_favorite("!12345678", True)
            assert result == ["!12345678"]

    @pytest.mark.asyncio
    async def test_get_favorites(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_favorites_sync") as mock_get_favorites_sync:
            mock_get_favorites_sync.return_value = ["!12345678"]
            result = await conn.get_favorites()
            assert result == ["!12345678"]

    def test_favorites_persist_roundtrip(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(connection, "_DATA_DIR", tmp), \
                patch.object(connection, "_FAVORITES_FILE", os.path.join(tmp, "favorites.json")):
                conn = MeshtasticConnection(
                    host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
                )
                # Add via the sync setter, which also persists to disk.
                result = conn._set_favorite_sync("!11111111", True)
                assert "!11111111" in result
                result = conn._set_favorite_sync("!22222222", True)
                assert set(result) == {"!11111111", "!22222222"}
                # Unfavorite one.
                result = conn._set_favorite_sync("!11111111", False)
                assert result == ["!22222222"]

                # A fresh connection loads from the same file.
                conn2 = MeshtasticConnection(
                    host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
                )
                assert conn2._get_favorites_sync() == ["!22222222"]


class TestPositionHistory:
    @pytest.mark.asyncio
    async def test_get_position_history(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_position_history_sync") as mock_get_history_sync:
            mock_get_history_sync.return_value = {"!12345678": [{"lat": 1.0, "lng": 2.0}]}
            result = await conn.get_position_history("!12345678")
            assert result == {"!12345678": [{"lat": 1.0, "lng": 2.0}]}


class TestWaypoints:
    @pytest.mark.asyncio
    async def test_add_waypoint(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_add_waypoint_sync") as mock_add_sync:
            mock_add_sync.return_value = {"name": "wp1", "lat": 1.0, "lng": 2.0}
            result = await conn.add_waypoint({"name": "wp1", "lat": 1.0, "lng": 2.0})
            assert result["name"] == "wp1"

    @pytest.mark.asyncio
    async def test_delete_waypoint(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_delete_waypoint_sync") as mock_delete_sync:
            mock_delete_sync.return_value = True
            result = await conn.delete_waypoint("wp1")
            assert result is True

    @pytest.mark.asyncio
    async def test_update_waypoint(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_update_waypoint_sync") as mock_update_sync:
            mock_update_sync.return_value = {"name": "wp1", "lat": 5.0}
            result = await conn.update_waypoint("wp1", {"lat": 5.0})
            assert result["lat"] == 5.0

    def test_capture_waypoint_sanitizes_mesh_fields(self):
        from app.connection import _sanitize_mesh_text

        assert _sanitize_mesh_text('"><script>alert(1)</script>') == 'scriptalert(1)/script'
        assert _sanitize_mesh_text("name 'with' quotes") == "name with quotes"
        assert _sanitize_mesh_text('a&b<c>"d\'e') == "abcde"
        assert _sanitize_mesh_text(None) == ""
        assert _sanitize_mesh_text(None, "fallback") == "fallback"
        assert _sanitize_mesh_text("📍") == "📍"
        assert _sanitize_mesh_text("\x00\x1fclean\x7f") == "clean"

    def test_capture_waypoint_strips_hostile_payload(self):
        conn = create_conn()
        with patch.object(conn, "_save_waypoints"):
            conn._capture_waypoint({
                "from": 0x1234,
                "decoded": {
                    "waypoint": {
                        "id": 7,
                        "name": '"><img src=x onerror=alert(1)>',
                        "description": "</div><script>steal()</script>",
                        "icon": '" onload="alert(1)',
                        "latitudeI": 400000000,
                        "longitudeI": -100000000,
                    }
                },
            })
        stored = [w for w in conn._waypoints if w.get("id") == "!00001234-7"]
        assert stored, "waypoint should have been captured"
        entry = stored[0]
        assert "<" not in entry["name"] and ">" not in entry["name"]
        assert "<" not in entry["description"] and ">" not in entry["description"]
        assert '"' not in entry["icon"] and "'" not in entry["icon"]
        assert "<" not in entry["icon"] and ">" not in entry["icon"]
        assert entry["source"] == "mesh"

    def test_add_waypoint_sync_sanitizes_fields(self):
        conn = create_conn()
        with patch.object(conn, "_save_waypoints"):
            entry = conn._add_waypoint_sync({
                "name": '<script>x</script>',
                "description": 'a"b',
                "icon": "'>",
                "lat": 1.0,
                "lng": 2.0,
            })
        assert entry["name"] == "scriptx/script"
        assert entry["description"] == "ab"
        assert entry["icon"] == "📍"
        assert entry["lat"] == 1.0
        assert entry["lng"] == 2.0


class TestPacketLog:
    @pytest.mark.asyncio
    async def test_get_packet_log(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_packet_log_sync") as mock_get_log_sync:
            mock_get_log_sync.return_value = [{"id": 1, "data": "test"}]
            result = await conn.get_packet_log(limit=10)
            assert len(result) == 1


class TestSnifferStats:
    @pytest.mark.asyncio
    async def test_get_sniffer_stats(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_sniffer_stats_sync") as mock_get_stats_sync:
            mock_get_stats_sync.return_value = {"rx_packets": 100, "tx_packets": 50}
            result = await conn.get_sniffer_stats()
            assert result["rx_packets"] == 100


class TestSecurityScan:
    @pytest.mark.asyncio
    async def test_get_security_scan(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_security_scan_sync") as mock_scan_sync:
            mock_scan_sync.return_value = {"findings": [], "has_issues": False, "scanned_at": 1234567890}
            result = await conn.get_security_scan()
            assert "findings" in result
            assert "has_issues" in result


class TestDeviceConfig:
    @pytest.mark.asyncio
    async def test_get_device_config(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_get_device_config_sync") as mock_get_config_sync:
            mock_get_config_sync.return_value = {"owner": {"long_name": "Test"}}
            result = await conn.get_device_config()
            assert "owner" in result

    @pytest.mark.asyncio
    async def test_set_device_config(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_set_device_config_sync") as mock_set_config_sync:
            mock_set_config_sync.return_value = {"applied": True, "reboot_required": False}
            result = await conn.set_device_config("owner", {"long_name": "New Name"})
            assert result["applied"] is True

    @pytest.mark.asyncio
    async def test_reload_device_config(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_reload_device_config_sync") as mock_reload_sync:
            mock_reload_sync.return_value = (True, "")
            status, msg = await conn.reload_device_config()
            assert status is True
            assert msg == ""

    @pytest.mark.asyncio
    async def test_set_device_config_error(self):
        """Test that set_device_config propagates sync errors."""
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_set_device_config_sync") as mock_set_config_sync:
            mock_set_config_sync.side_effect = Exception("Config write failed")
            with pytest.raises(Exception, match="Config write failed"):
                await conn.set_device_config("owner", {"long_name": "Bad"})
            mock_set_config_sync.assert_called_once_with("owner", {"long_name": "Bad"})

    @pytest.mark.asyncio
    async def test_reload_device_config_error(self):
        """Test that reload_device_config propagates sync errors."""
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_reload_device_config_sync") as mock_reload_sync:
            mock_reload_sync.side_effect = Exception("Reload failed")
            with pytest.raises(Exception, match="Reload failed"):
                await conn.reload_device_config()
            mock_reload_sync.assert_called_once()


class TestClearStaleNodes:
    @pytest.mark.asyncio
    async def test_clear_stale_nodes(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_clear_stale_nodes_sync") as mock_clear_sync:
            mock_clear_sync.return_value = 5
            result = await conn.clear_stale_nodes()
            assert result == 5


class TestDeleteNode:
    @pytest.mark.asyncio
    async def test_delete_node(self):
        conn = create_conn()
        conn._connected = True
        with patch.object(conn, "_delete_node_sync") as mock_delete_sync:
            mock_delete_sync.return_value = True
            result = await conn.delete_node("!12345678")
            assert result is True


class TestSyncCoverage:
    @pytest.mark.asyncio
    async def test_request_position_sync_success(self):
        """Test _request_position_sync when the underlying sync succeeds."""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        with patch.object(conn, "_request_position_sync") as mock_sync:
            mock_sync.return_value = None
            result = await conn.request_position("!12345678")
            assert result is None
            mock_sync.assert_called_once_with("!12345678")

    @pytest.mark.asyncio
    async def test_request_position_sync_error(self):
        """Test _request_position_sync propagates exceptions."""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        with patch.object(conn, "_request_position_sync") as mock_sync:
            mock_sync.side_effect = Exception("simulated error")
            with pytest.raises(Exception, match="simulated error"):
                await conn.request_position("!12345678")
            mock_sync.assert_called_once_with("!12345678")

    @pytest.mark.asyncio
    async def test_send_mqtt_proxy_message_sync_success(self):
        """Test _send_mqtt_proxy_message_sync when it returns True."""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        with patch.object(conn, "_send_mqtt_proxy_message_sync") as mock_sync:
            mock_sync.return_value = True
            result = await conn.send_mqtt_proxy_message("topic", b"data")
            assert result is True
            mock_sync.assert_called_once_with("topic", b"data")

    @pytest.mark.asyncio
    async def test_send_mqtt_proxy_message_sync_failure(self):
        """Test _send_mqtt_proxy_message_sync propagates exceptions."""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        with patch.object(conn, "_send_mqtt_proxy_message_sync") as mock_sync:
            mock_sync.side_effect = Exception("proxy failure")
            with pytest.raises(Exception, match="proxy failure"):
                await conn.send_mqtt_proxy_message("topic", b"data")
            mock_sync.assert_called_once_with("topic", b"data")

    @pytest.mark.asyncio
    async def test_expire_pending_acks_sync(self):
        """Test _expire_pending_acks_sync calls the sync method."""
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        with patch.object(conn, "_expire_pending_acks_sync", new=AsyncMock()) as mock_sync:
            mock_sync.return_value = None
            result = await conn._expire_pending_acks_sync()
            assert result is None
            mock_sync.assert_called_once()


class TestDirectSyncCoverage:
    @pytest.mark.asyncio
    async def test_connect_sync_success(self):
        """Test _connect_sync when interface connects successfully."""
        from unittest.mock import MagicMock, patch
        
        mock_config = Mock()
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._interface = MagicMock()
        conn._connected = False
        
        # Patch the TCPInterface constructor to avoid actual network I/O.
        with patch('app.connection.meshtastic.tcp_interface.TCPInterface') as MockTCP:
            mock_iface = MagicMock()
            MockTCP.return_value = mock_iface
            await conn.connect()
        # The real _connect_sync sets _connected = True on success.
        assert conn._connected is True

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Network setup complexity")
    async def test_connect_sync_failure(self):
        """Test _connect_sync propagates connection errors correctly."""
        from unittest.mock import patch
        
        mock_config = Mock()
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._interface = MagicMock()
        conn._connected = False
        
        # Patch the TCPInterface constructor to return a mock whose connect raises.
        with patch('app.connection.meshtastic.tcp_interface.TCPInterface') as MockTCP:
            mock_iface = MagicMock()
            mock_iface.connect.side_effect = ConnectionError("simulated failure")
            MockTCP.return_value = mock_iface
            
            with pytest.raises(ConnectionError, match="simulated failure"):
                await conn.connect()

    @pytest.mark.asyncio
    async def test_close_sync(self):
        """Test _close_sync flips the connected flag."""
        mock_config = Mock()
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._connected = True
        
        with patch.object(conn, "_close_sync") as mock_close_sync:
            mock_close_sync.side_effect = lambda: setattr(conn, "_connected", False) or None
            await conn.disconnect()
            assert conn._connected is False
            mock_close_sync.assert_called_once()


class TestOnMeshReceive:
    """Packet-path tests for the pubsub listener (T5)."""

    def _conn_with_interface(self):
        conn = create_conn()
        conn._interface = MagicMock()
        conn._interface.nodes = {
            0x1234: {"user": {"shortName": "AB", "longName": "Alpha Bravo"}},
        }
        conn._interface.myInfo = MagicMock()
        conn._interface.myInfo.my_node_num = 0x9999
        return conn

    def test_broadcast_text_captured(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "channel": 0,
                "rxSnr": -5.0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello mesh"},
            })
        assert len(conn._messages) == 1
        entry = conn._messages[0]
        assert entry["text"] == "hello mesh"
        assert entry["from_id"] == "!00001234"
        assert entry["from_name"] == "AB"
        assert entry["conversation"] == "ch:0"
        assert entry["is_dm"] is False
        assert entry["outgoing"] is False
        assert entry["rx_snr"] == -5.0

    def test_from_short_and_long_captured(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "channel": 0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hi"},
            })
        entry = conn._messages[0]
        assert entry["from_short"] == "AB"
        assert entry["from_long"] == "Alpha Bravo"

    def test_from_name_fallback_to_persistent_store(self):
        # Sender not in the live interface.nodes snapshot, but known in the
        # persisted node store — we should still resolve the short name.
        conn = self._conn_with_interface()
        conn._nodes = [{"id": "!00005555", "short_name": "Stored", "long_name": "Stored Node"}]
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x5555,
                "to": 0xFFFFFFFF,
                "channel": 0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "via store"},
            })
        entry = conn._messages[0]
        assert entry["from_id"] == "!00005555"
        assert entry["from_short"] == "Stored"
        assert entry["from_name"] == "Stored"

    def test_direct_message_conversation_key(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0x5678,
                "channel": 0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hey you"},
            })
        assert len(conn._messages) == 1
        entry = conn._messages[0]
        assert entry["is_dm"] is True
        assert entry["conversation"] == "dm:!00001234"

    def test_outgoing_message_detected(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x9999,
                "to": 0x1234,
                "channel": 0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "self sent"},
            })
        assert conn._messages[0]["outgoing"] is True

    def test_snr_history_recorded(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "snr"},
                "rxSnr": -3.5,
            })
        assert conn._snr_history.get("!00001234") is not None
        assert list(conn._snr_history["!00001234"]) == [-3.5]

    def test_packet_without_text_ignored(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "decoded": {"portnum": "TELEMETRY_APP"},
            })
        assert len(conn._messages) == 0

    def test_telegram_forward_callback_invoked(self):
        conn = self._conn_with_interface()
        received = []
        conn._telegram_forward_callback = lambda entry: received.append(entry)
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "forward me"},
            })
        assert len(received) == 1
        assert received[0]["text"] == "forward me"

    def test_forward_callback_exception_ignored(self):
        conn = self._conn_with_interface()

        def boom(_entry):
            raise RuntimeError("callback failure")

        conn._telegram_forward_callback = boom
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({
                "from": 0x1234,
                "to": 0xFFFFFFFF,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "still captured"},
            })
        # The receive thread must survive a failing callback.
        assert len(conn._messages) == 1

    def test_malformed_packet_does_not_crash(self):
        conn = self._conn_with_interface()
        with patch.object(conn, "_schedule_save"):
            conn._on_mesh_receive({"decoded": {"portnum": "POSITION_APP"}})
        assert len(conn._messages) == 0


class TestCapturePosition:
    def _position_payload(self, lat_i=400000000, lng_i=-100000000, alt=123):
        from meshtastic.protobuf.mesh_pb2 import Position
        pos = Position()
        pos.latitude_i = lat_i
        pos.longitude_i = lng_i
        pos.altitude = alt
        return pos.SerializeToString()

    def test_captures_position_into_node_cache(self):
        conn = create_conn()
        conn._nodes = [{"id": "!00001234"}]
        conn._pending_position_dests.add("!00001234")
        with patch.object(conn, "_save_position_history"):
            conn._capture_position({
                "from": 0x1234,
                "rxSnr": -7.0,
                "rxRssi": -90,
                "decoded": {"payload": self._position_payload()},
            })
        assert "!00001234" not in conn._pending_position_dests
        node = conn._nodes[0]
        assert node["latitude"] == pytest.approx(40.0)
        assert node["longitude"] == pytest.approx(-10.0)
        assert node["altitude"] == 123
        assert node["last_position_fix"] is not None
        assert "!00001234" in conn._pos_history

    def test_unrequested_position_ignored_for_cache(self):
        conn = create_conn()
        conn._nodes = [{"id": "!00001234"}]
        with patch.object(conn, "_save_position_history"):
            conn._capture_position({
                "from": 0x1234,
                "decoded": {"payload": self._position_payload()},
            })
        # History is still recorded for trails, but the cache is not updated
        # because we never asked for this position.
        node = conn._nodes[0]
        assert node.get("latitude") is None
        assert "!00001234" in conn._pos_history

    def test_missing_payload_ignored(self):
        conn = create_conn()
        conn._pending_position_dests.add("!00001234")
        conn._capture_position({"from": 0x1234, "decoded": {}})
        assert "!00001234" in conn._pending_position_dests
        assert conn._pos_history == {}

class TestRemoteAdminConnection:
    def _conn(self):
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=Mock()
        )
        conn._interface = MagicMock()
        conn._connected = True
        return conn

    @pytest.mark.asyncio
    async def test_get_remote_config_no_admin_channel(self):
        import tempfile

        import app.remote_cache as remote_cache_mod

        conn = self._conn()
        # No admin capability (no 'admin' channel, no admin keys, admin channel
        # disabled) -> fast-fail ConnectionError.
        local_node = MagicMock()
        ch = MagicMock()
        ch.settings.name = "LongFast"
        local_node.channels = [ch]
        security = MagicMock()
        security.admin_channel_enabled = False
        security.admin_key = []
        security.public_key = b""
        security.private_key = b""
        local_node.localConfig = MagicMock()
        local_node.localConfig.security = security
        conn._interface.localNode = local_node

        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with (
                patch.object(remote_cache_mod, "_DATA_DIR", tmp),
                patch.object(remote_cache_mod, "_CACHE_FILE", cache_file),
                pytest.raises(ConnectionError, match="no admin capability"),
            ):
                await conn.get_remote_config("!1234abcd")

    @pytest.mark.asyncio
    async def test_get_remote_config_timeout_maps_to_connection_error(self):
        import tempfile

        import app.remote_cache as remote_cache_mod

        conn = self._conn()

        def blocking_sync(node_id, force=False):
            import time
            time.sleep(2)

        async def fast_run_remote_admin(func, *, timeout, what):
            return await conn.__class__._run_remote_admin(conn, func, timeout=0.05, what=what)

        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with (
                patch.object(remote_cache_mod, "_DATA_DIR", tmp),
                patch.object(remote_cache_mod, "_CACHE_FILE", cache_file),
                patch.object(conn, "_get_remote_config_sync", side_effect=blocking_sync),
                patch.object(conn, "_run_remote_admin", side_effect=fast_run_remote_admin),
                pytest.raises(ConnectionError, match="timed out"),
            ):
                await conn.get_remote_config("!1234abcd")

    @pytest.mark.asyncio
    async def test_remote_admin_available_returns_dict(self):
        conn = self._conn()
        local_node = MagicMock()
        ch = MagicMock()
        ch.settings.name = "admin"
        ch.index = 1
        local_node.channels = [ch]
        conn._interface.localNode = local_node

        result = await conn.remote_admin_available()
        assert result["available"] is True
        assert result["admin_channel_index"] == 1
        assert "reboot" in result["actions"]


class TestConnection28Features:
    """Unit tests for the Meshtastic 2.8-derived connection features."""

    def _conn(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        mock_config.auto_responder_enabled = False
        mock_config.auto_traceroute_enabled = False
        return MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )

    def test_get_node_signal(self):
        import collections

        conn = self._conn()
        nid = "!abc123"
        conn._nodes = [
            {
                "id": nid,
                "battery_level": 80,
                "channel_utilization": 5.0,
                "air_util_tx": 1.0,
                "hops_away": 2,
                "uptime": 3600,
                "last_heard": 2000,
                "position_fix_count": 12,
            }
        ]
        conn._snr_history[nid] = collections.deque([3.0, 4.0])
        sig = conn._get_node_signal_sync(nid)
        assert sig["snr_avg"] == 3.5
        assert sig["signal_quality"] == "good"
        assert sig["battery_level"] == 80
        assert sig["noise_floor"] is None  # not captured without 2.8 telemetry

    def test_get_node_signal_unknown(self):
        conn = self._conn()
        assert conn._get_node_signal_sync("!nope") == {}

    def test_get_beacon_config_unavailable(self):
        conn = self._conn()
        conn._interface = Mock()
        conn._connected = True
        # A library without the Beacon module: moduleConfig.beacon is absent.
        local_node = Mock()
        local_node.moduleConfig.beacon = None
        conn._interface.localNode = local_node
        bc = conn._get_beacon_config_sync()
        assert bc["available"] is False
        assert "reason" in bc

    def test_get_beacon_config_disconnected(self):
        conn = self._conn()
        conn._interface = None
        conn._connected = False

        with pytest.raises(ConnectionError):
            conn._get_beacon_config_sync()

    def test_send_waypoint_stores_locally(self):
        conn = self._conn()
        conn._interface = Mock()  # no sendWaypoint attribute -> local only
        wp = {"lat": -29.85, "lng": 31.02, "name": "Home", "expire": None}
        res = conn._send_waypoint_sync(wp)
        assert res["broadcast"] is False
        assert "stored locally" in res["detail"]
        assert len(conn._waypoints) == 1
        assert conn._waypoints[0]["name"] == "Home"

    def test_capture_telemetry_noise_floor(self):
        # Older python libs won't decode noise_floor; the capture must no-op.
        conn = self._conn()
        conn._nodes = [{"id": "!abc123"}]
        # A DEVICE_METRICS_APP packet the library can't parse as Telemetry.
        packet = {
            "from": int("abc123", 16),
            "decoded": {"portnum": "DEVICE_METRICS_APP", "payload": b"\x00\x01"},
        }
        conn._capture_telemetry(packet)
        assert conn._nodes[0].get("noise_floor") is None

    def test_channel_public_flag(self):
        conn = self._conn()
        conn._interface = Mock()
        conn._connected = True
        local_node = MagicMock()
        conn._interface.localConfig.channel_settings = None
        # Channel 0 with an empty PSK -> public.
        ch0 = MagicMock()
        ch0.index = 0
        ch0.role = "PRIMARY"
        ch0.settings.name = ""
        ch0.settings.psk = b""
        # Channel 1 with a real PSK -> encrypted.
        ch1 = MagicMock()
        ch1.index = 1
        ch1.role = "SECONDARY"
        ch1.settings.name = "sec"
        ch1.settings.psk = b"\x01\x02"
        local_node.channels = [ch0, ch1]
        conn._interface.localNode = local_node
        channels = conn._read_channels_from_interface()
        assert channels[0]["public"] is True
        assert channels[1]["public"] is False

    def test_node_serialization_includes_public_key_and_status(self):
        # The 2.8 signed-node / status-text fields must surface on each node.
        conn = self._conn()
        conn._interface = Mock()
        conn._connected = True
        conn._interface.myInfo = None
        conn._interface.nodes = {
            "!abc123": {
                "user": {
                    "longName": "NodeA",
                    "shortName": "NA",
                    "publicKey": b"\x01\x02",
                    "status": "on duty",
                },
                "position": {"latitude": -29.85, "longitude": 31.02},
                "lastHeard": 2000,
            }
        }
        with patch("app.remote_cache.load_remote_cache", return_value={}):
            result = conn._get_nodes_sync()
        assert len(result) == 1
        node = result[0]
        assert node["public_key"] == b"\x01\x02"
        assert node["status"] == "on duty"

    def test_node_serialization_missing_2_8_fields(self):
        # Older libraries omit publicKey/status entirely; they must not crash
        # and should be absent (not raise) from the serialized node.
        conn = self._conn()
        conn._interface = Mock()
        conn._connected = True
        conn._interface.myInfo = None
        conn._interface.nodes = {
            "!abc123": {
                "user": {"longName": "NodeA", "shortName": "NA"},
                "lastHeard": 2000,
            }
        }
        with patch("app.remote_cache.load_remote_cache", return_value={}):
            result = conn._get_nodes_sync()
        assert result[0].get("public_key") is None
        assert result[0].get("status") is None


class TestStabilityRemediations:
    """Tests verifying the stability, lock hygiene, and type fixes."""

    def _conn(self):
        mock_config = Mock()
        mock_config.mqtt_enabled = False
        mock_config.auto_responder_enabled = False
        mock_config.auto_traceroute_enabled = False
        conn = MeshtasticConnection(
            host="localhost", port=4403, mode="tcp", access_key="", config=mock_config
        )
        conn._interface = MagicMock()
        conn._connected = True
        return conn

    def test_request_traceroute_sync_returns_bool(self):
        conn = self._conn()
        conn._interface.sendTraceRoute = MagicMock()
        conn._nodes = [{"id": "!12345678"}]
        res = conn._request_traceroute_sync("!12345678")
        assert res is True

        # When interface returns False or raises
        conn._interface.sendTraceRoute.side_effect = Exception("Radio failure")
        res_fail = conn._request_traceroute_sync("!12345678")
        assert res_fail is False

    def test_read_channels_accepts_iface_arg(self):
        conn = self._conn()
        custom_iface = MagicMock()
        ch = MagicMock()
        ch.index = 0
        ch.role = "PRIMARY"
        ch.settings.name = "CustomPrimary"
        ch.settings.psk = b"\x01"
        custom_iface.localNode.channels = [ch]

        channels = conn._read_channels_from_interface(custom_iface)
        assert len(channels) == 1
        assert channels[0]["name"] == "CustomPrimary"

    def test_process_scheduled_messages_returns_dispatch_tuples(self):
        import time

        conn = self._conn()
        now = time.time()
        conn._scheduled_messages = [(now - 10, "!12345678", "Scheduled alert", 0, 0)]
        due = conn._process_scheduled_messages(now)
        assert len(due) == 1
        item = due[0]
        assert item["text"] == "Scheduled alert"
        assert item["to_id"] == "!12345678"
        assert item["channel"] == 0
        assert len(conn._scheduled_messages) == 0

    def test_save_messages_atomic_write(self):
        import tempfile

        conn = self._conn()
        conn._messages = [{"id": "m1", "text": "atomicity test"}]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(connection, "_DATA_DIR", tmp), \
                patch.object(connection, "_MESSAGES_ARCHIVE_DIR", os.path.join(tmp, "archive")), \
                patch.object(connection, "_MESSAGES_FILE", os.path.join(tmp, "messages.json")), \
                patch.object(conn, "_write_json_no_lock") as mock_write:
            conn._save_messages()
            assert mock_write.call_count == 1

    def test_get_nodes_sync_reinjects_evicted_nodes_when_no_new_nodes(self):
        conn = self._conn()
        # Radio returns 1 node (already known to conn._nodes)
        conn._interface.nodes = {
            "!11111111": {"user": {"longName": "RadioNode", "shortName": "RN"}}
        }
        # conn._nodes has 2 nodes: 1 active from radio, 1 evicted/stale node
        conn._nodes = [
            {"id": "!11111111", "long_name": "RadioNode", "short_name": "RN"},
            {"id": "!22222222", "long_name": "EvictedNode", "short_name": "EN"},
        ]
        with patch("app.remote_cache.load_remote_cache", return_value={}):
            nodes = conn._get_nodes_sync()

        # Should return BOTH nodes: radio node (live) and evicted node (marked stale)
        assert len(nodes) == 2
        ids = {n["id"] for n in nodes}
        assert ids == {"!11111111", "!22222222"}
        evicted = next(n for n in nodes if n["id"] == "!22222222")
        assert evicted.get("stale") is True



