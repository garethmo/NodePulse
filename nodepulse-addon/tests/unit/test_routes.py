"""
Unit tests for app/routes.py
"""
import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from aiohttp import web

from app import routes


@pytest.fixture(autouse=True)
def reset_ha_base():
    """Reset the global HA base URL between tests."""
    import app.routes
    app.routes._working_ha_base = None
    yield
    app.routes._working_ha_base = None


def make_mock_request(**kwargs):
    """Create a mock request with connection and config."""
    mock_config = Mock()
    mock_config.ignored_nodes = []
    mock_config.log_level = "INFO"
    mock_config.ha_base_url = "http://localhost:8123"
    mock_config.access_key = "test_key"
    mock_config.connection_type = "tcp"
    mock_config.meshtastic_host = "localhost"
    mock_config.meshtastic_port = 4403
    mock_config.proxy_host = ""
    mock_config.proxy_port = 4403
    mock_config.scan_interval = 300
    mock_config.disable_token_validation = False
    mock_config.scheduled_messages_enabled = True
    mock_config.mqtt_enabled = False
    mock_config.mqtt_address = ""
    mock_config.mqtt_port = 1883
    mock_config.mqtt_username = ""
    mock_config.mqtt_password = ""
    mock_config.mqtt_topic = "nodepulse"
    mock_config.mqtt_forwarding_enabled = False
    mock_config.mqtt_geo_filter_enabled = False
    mock_config.mqtt_lat_min = -90.0
    mock_config.mqtt_lat_max = 90.0
    mock_config.mqtt_lng_min = -180.0
    mock_config.mqtt_lng_max = 180.0
    mock_config.mqtt_portnum_allowlist = []
    mock_config.mqtt_node_blocklist = []
    mock_config.telegram_enabled = False
    mock_config.telegram_chat_id = ""
    mock_config.telegram_authorized_chat_ids = []
    mock_config.telegram_forward_channels = []
    mock_config.telegram_forward_dms = False
    mock_config.telegram_allow_commands = False
    mock_config.telegram_bot_token = ""
    mock_config.auto_responder_enabled = False
    mock_config.auto_responder_message = ""
    
    mock_connection = Mock()
    mock_connection._scheduled_messages = []
    mock_connection._scheduled_messages_lock = Mock()
    mock_connection._scheduled_messages_lock.__enter__ = Mock(return_value=None)
    mock_connection._scheduled_messages_lock.__exit__ = Mock(return_value=None)
    
    defaults = {
        "app": {"connection": mock_connection, "config": mock_config, "ignored_nodes": set()},
    }
    defaults.update(kwargs)
    
    mock_request = Mock()
    for k, v in defaults.items():
        setattr(mock_request, k, v)
    
    return mock_request, mock_connection, mock_config


class TestValidateDestination:
    def test_validate_destination_valid(self):
        body = {"destination": "!abcdef12"}
        result = routes._validate_destination(body)
        assert result == "!abcdef12"

    def test_validate_destination_invalid_format(self):
        body = {"destination": "abcdef12"}
        result = routes._validate_destination(body)
        assert result is None

    def test_validate_destination_missing(self):
        body = {}
        result = routes._validate_destination(body)
        assert result is None

    def test_validate_destination_empty(self):
        body = {"destination": ""}
        result = routes._validate_destination(body)
        assert result is None

    def test_validate_destination_whitespace(self):
        body = {"destination": "  !abcdef12  "}
        result = routes._validate_destination(body)
        assert result == "!abcdef12"


class TestJsonResponse:
    def test_json_response(self):
        result = routes._json_response({"key": "value"})
        assert isinstance(result, web.Response)
        assert result.content_type == "application/json"
        assert result.status == 200

    def test_json_response_with_status(self):
        result = routes._json_response({"key": "value"}, status=201)
        assert result.status == 201

    def test_error_response(self):
        result = routes._error_response("Something went wrong")
        assert isinstance(result, web.Response)
        assert result.content_type == "application/json"
        assert result.status == 500

    def test_error_response_with_status(self):
        result = routes._error_response("Not found", status=404)
        assert result.status == 404


class TestApplyAccessKey:
    def test_apply_access_key_with_key(self):
        mock_request = Mock()
        mock_request.headers = {"X-NodePulse-Access-Key": "secret"}
        mock_request.app = {"connection": Mock()}
        mock_request.app["connection"].set_access_key = Mock()
        
        routes._apply_access_key(mock_request)
        mock_request.app["connection"].set_access_key.assert_called_once_with("secret")

    def test_apply_access_key_without_key(self):
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.app = {"connection": Mock()}
        mock_request.app["connection"].set_access_key = Mock()
        
        routes._apply_access_key(mock_request)
        mock_request.app["connection"].set_access_key.assert_not_called()


class TestHandleStatus:
    @pytest.mark.asyncio
    async def test_handle_status(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_status = AsyncMock(return_value={"connected": True})
        
        result = await routes.handle_status(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleNodes:
    @pytest.mark.asyncio
    async def test_handle_nodes(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_nodes = AsyncMock(return_value=[{"id": "!123"}])
        
        result = await routes.handle_nodes(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleChannels:
    @pytest.mark.asyncio
    async def test_handle_channels(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.refresh_channels = AsyncMock(return_value=[{"index": 0}])
        
        result = await routes.handle_channels(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleMessages:
    @pytest.mark.asyncio
    async def test_handle_messages(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_messages = AsyncMock(return_value=[{"id": "1"}])
        
        result = await routes.handle_messages(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandlePackets:
    @pytest.mark.asyncio
    async def test_handle_packets(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_packet_log = AsyncMock(return_value=[{"id": "1"}])
        
        result = await routes.handle_packets(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleSnifferStats:
    @pytest.mark.asyncio
    async def test_handle_sniffer_stats(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_sniffer_stats = AsyncMock(return_value={"packets": 100})
        
        result = await routes.handle_sniffer_stats(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleSend:
    @pytest.mark.asyncio
    async def test_handle_send_valid(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.send_message = AsyncMock(return_value=True)
        mock_request.json = AsyncMock(return_value={"text": "hello", "destination": "!abcdef12"})
        
        with patch("app.routes._validate_destination", return_value="!abcdef12"):
            result = await routes.handle_send(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_send_broadcast(self):
        """Test sending a broadcast message (no destination)."""
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.send_message = AsyncMock(return_value=True)
        mock_request.json = AsyncMock(return_value={"text": "hello"})
        
        result = await routes.handle_send(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200
        mock_connection.send_message.assert_called_once_with("hello", destination=None, channel=0)


class TestHandleTraceroute:
    @pytest.mark.asyncio
    async def test_handle_traceroute_valid(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.request_traceroute = AsyncMock(return_value=True)
        mock_request.json = AsyncMock(return_value={"destination": "!abcdef12"})
        
        with patch("app.routes._validate_destination", return_value="!abcdef12"):
            result = await routes.handle_traceroute(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleRequestPosition:
    @pytest.mark.asyncio
    async def test_handle_request_position_valid(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.request_position = AsyncMock(return_value=True)
        mock_request.json = AsyncMock(return_value={"destination": "!abcdef12"})
        
        with patch("app.routes._validate_destination", return_value="!abcdef12"):
            result = await routes.handle_request_position(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleTags:
    @pytest.mark.asyncio
    async def test_handle_tags(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_tags = AsyncMock(return_value={"!123": ["tag1"]})
        
        result = await routes.handle_tags(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleSetTags:
    @pytest.mark.asyncio
    async def test_handle_set_tags(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.set_tags = AsyncMock(return_value={"!123": ["tag1"]})
        mock_request.json = AsyncMock(return_value={"node_id": "!123", "tags": ["tag1"]})
        
        result = await routes.handle_set_tags(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandlePositionHistory:
    @pytest.mark.asyncio
    async def test_handle_position_history(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_position_history = AsyncMock(return_value={"!123": []})
        
        result = await routes.handle_position_history(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleTrackedNodes:
    @pytest.mark.asyncio
    async def test_handle_tracked_nodes(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_nodes = AsyncMock(return_value=[{"id": "!123"}])
        
        with patch("app.routes._relay_to_integration", AsyncMock(return_value={"node_ids": ["!123"]})):
            result = await routes.handle_tracked_nodes(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleTrackNode:
    @pytest.mark.asyncio
    async def test_handle_track_node(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_nodes = AsyncMock(return_value=[{"id": "!123"}])
        mock_request.json = AsyncMock(return_value={"node_id": "!123"})
        
        with patch("app.routes._relay_to_integration", AsyncMock()):
            result = await routes.handle_track_node(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleExportMessages:
    @pytest.mark.asyncio
    async def test_handle_export_messages(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_messages = AsyncMock(return_value=[{"id": "1"}])
        
        result = await routes.handle_export_messages(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleClearStaleNodes:
    @pytest.mark.asyncio
    async def test_handle_clear_stale_nodes(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.clear_stale_nodes = AsyncMock(return_value=5)
        
        result = await routes.handle_clear_stale_nodes(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleDeleteNode:
    @pytest.mark.asyncio
    async def test_handle_delete_node(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.delete_node = AsyncMock(return_value=True)
        mock_request.match_info = {"node_id": "!123"}
        
        result = await routes.handle_delete_node(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleWaypoints:
    @pytest.mark.asyncio
    async def test_handle_get_waypoints(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_waypoints = AsyncMock(return_value=[{"id": "1"}])
        
        result = await routes.handle_get_waypoints(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_add_waypoint(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.add_waypoint = AsyncMock(return_value={"id": "1"})
        mock_request.json = AsyncMock(return_value={"name": "WP1"})
        
        result = await routes.handle_add_waypoint(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_update_waypoint(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.update_waypoint = AsyncMock(return_value={"id": "1"})
        mock_request.json = AsyncMock(return_value={"name": "WP1 Updated"})
        mock_request.match_info = {"waypoint_id": "1"}
        
        result = await routes.handle_update_waypoint(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_delete_waypoint(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.delete_waypoint = AsyncMock(return_value=True)
        mock_request.match_info = {"waypoint_id": "1"}
        
        result = await routes.handle_delete_waypoint(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleDeviceConfig:
    @pytest.mark.asyncio
    async def test_handle_get_device_config(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_device_config = AsyncMock(return_value={"device": {}})
        
        result = await routes.handle_get_device_config(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_put_device_config_section(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.set_device_config = AsyncMock(return_value=(True, False))
        mock_request.json = AsyncMock(return_value={"field": "value"})
        mock_request.match_info = {"section": "device"}
        
        result = await routes.handle_put_device_config_section(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_handle_reload_device_config(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.reload_device_config = AsyncMock(return_value=(True, "OK"))
        
        result = await routes.handle_reload_device_config(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestHandleSecurityScan:
    @pytest.mark.asyncio
    async def test_handle_get_security_scan(self):
        mock_request, mock_connection, mock_config = make_mock_request()
        mock_connection.get_security_scan = AsyncMock(return_value={"scan": "data"})
        
        result = await routes.handle_get_security_scan(mock_request)
        assert isinstance(result, web.Response)
        assert result.status == 200


class TestNodeIdRegex:
    def test_node_id_regex_valid(self):
        assert routes._NODE_ID_RE.match("!abcdef12")
        assert routes._NODE_ID_RE.match("!12345678")
        assert routes._NODE_ID_RE.match("!a")
        assert routes._NODE_ID_RE.match("!ABCDEF12")

    def test_node_id_regex_invalid(self):
        assert not routes._NODE_ID_RE.match("abcdef12")
        assert not routes._NODE_ID_RE.match("!abcdef123")  # too long
        assert not routes._NODE_ID_RE.match("!ghijklmn")  # invalid hex
        assert not routes._NODE_ID_RE.match("")