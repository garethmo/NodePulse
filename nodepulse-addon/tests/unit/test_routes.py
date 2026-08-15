"""
Unit tests for app/routes.py
"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from aiohttp import web
import asyncio

# Import the module under test
from app import routes

# ----------------------------------------------------------------------
# Helper to create a mock request
# ----------------------------------------------------------------------
def make_request(app_dict=None, method="GET", path="/", headers=None, body=None, query=None, match_info=None):
    """Create a mock aiohttp request."""
    request = MagicMock()
    request.method = method
    request.path = path
    request.headers = headers or {}
    request.query = query or {}
    request.match_info = match_info or {}
    if app_dict:
        # Ensure ignored_nodes is present if connection is provided
        if "connection" in app_dict and "ignored_nodes" not in app_dict:
            app_dict["ignored_nodes"] = set()
        request.app = app_dict
    else:
        request.app = {}
    if body is not None:
        request._body = json.dumps(body).encode()
        request.content = MagicMock()
        request.content.read = AsyncMock(return_value=json.dumps(body).encode())
    else:
        request._body = b""
        request.content = MagicMock()
        request.content.read = AsyncMock(return_value=b"")
    request.json = AsyncMock(return_value=body or {})
    return request


# ----------------------------------------------------------------------
# _validate_destination tests
# ----------------------------------------------------------------------
class TestValidateDestination:
    def test_validate_destination_normal(self):
        body = {"destination": "!12345678"}
        result = routes._validate_destination(body)
        assert result == "!12345678"

    def test_validate_destination_missing(self):
        body = {}
        result = routes._validate_destination(body)
        assert result is None

    def test_validate_destination_empty(self):
        body = {"destination": "   "}
        result = routes._validate_destination(body)
        assert result is None

    def test_validate_destination_invalid_format(self):
        body = {"destination": "invalid"}
        result = routes._validate_destination(body)
        assert result is None


# ----------------------------------------------------------------------
# _apply_access_key tests
# ----------------------------------------------------------------------
class TestApplyAccessKey:
    def test_apply_access_key_with_header(self):
        mock_conn = MagicMock()
        request = make_request(app_dict={"connection": mock_conn}, headers={"X-NodePulse-Access-Key": "test-key"})
        routes._apply_access_key(request)
        mock_conn.set_access_key.assert_called_once_with("test-key")

    def test_apply_access_key_without_header(self):
        mock_conn = MagicMock()
        request = make_request(app_dict={"connection": mock_conn}, headers={})
        routes._apply_access_key(request)
        mock_conn.set_access_key.assert_not_called()


# ----------------------------------------------------------------------
# _json_response and _error_response tests
# ----------------------------------------------------------------------
class TestJsonErrorResponses:
    def test_json_response(self):
        resp = routes._json_response({"status": "ok"}, status=201)
        assert resp.status == 201
        body = json.loads(resp.body)
        assert body == {"status": "ok"}

    def test_error_response(self):
        resp = routes._error_response("Bad request", status=400)
        assert resp.status == 400
        body = json.loads(resp.body)
        assert body == {"error": "Bad request"}


# ----------------------------------------------------------------------
# handle_status tests
# ----------------------------------------------------------------------
class TestHandleStatus:
    @pytest.mark.asyncio
    async def test_handle_status_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_status.return_value = {"connected": True, "my_info": {}}
        # Add scheduled messages attributes used in handle_status
        mock_conn._scheduled_messages = []
        mock_conn._scheduled_messages_lock = MagicMock()
        mock_conn._scheduled_messages_lock.__enter__ = MagicMock(return_value=None)
        mock_conn._scheduled_messages_lock.__exit__ = MagicMock(return_value=None)

        mock_config = MagicMock()
        config_attrs = {
            "connection_type": "tcp",
            "meshtastic_host": "localhost",
            "meshtastic_port": 4403,
            "proxy_host": "",
            "proxy_port": 8080,
            "scan_interval": 300,
            "log_level": "INFO",
            "ha_base_url": "",
            "disable_token_validation": False,
            "ignored_nodes": [],
            "access_key": "",
            "scheduled_messages_enabled": True,
            "mqtt_enabled": False,
            "mqtt_address": "",
            "mqtt_port": 1883,
            "mqtt_username": "",
            "mqtt_password": "",
            "mqtt_topic": "",
            "mqtt_forwarding_enabled": False,
            "mqtt_geo_filter_enabled": False,
            "mqtt_lat_min": -90.0,
            "mqtt_lat_max": 90.0,
            "mqtt_lng_min": -180.0,
            "mqtt_lng_max": 180.0,
        }
        for k, v in config_attrs.items():
            setattr(mock_config, k, v)

        request = make_request(app_dict={"connection": mock_conn, "config": mock_config})
        resp = await routes.handle_status(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert "connected" in body
        assert "config" in body
        mock_conn.get_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_status_connection_error(self):
        mock_conn = AsyncMock()
        mock_conn.get_status.side_effect = Exception("Connection failed")
        mock_conn._scheduled_messages = []
        mock_conn._scheduled_messages_lock = MagicMock()
        mock_conn._scheduled_messages_lock.__enter__ = MagicMock(return_value=None)
        mock_conn._scheduled_messages_lock.__exit__ = MagicMock(return_value=None)
        mock_config = MagicMock()
        request = make_request(app_dict={"connection": mock_conn, "config": mock_config})
        resp = await routes.handle_status(request)
        assert resp.status == 500


# ----------------------------------------------------------------------
# handle_nodes tests
# ----------------------------------------------------------------------
class TestHandleNodes:
    @pytest.mark.asyncio
    async def test_handle_nodes_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_nodes.return_value = [{"id": "!12345678", "name": "Node1"}]
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/nodes")
        resp = await routes.handle_nodes(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert isinstance(body, list)
        mock_conn.get_nodes.assert_called_once()


# ----------------------------------------------------------------------
# handle_clear_stale_nodes tests
# ----------------------------------------------------------------------
class TestHandleClearStaleNodes:
    @pytest.mark.asyncio
    async def test_handle_clear_stale_nodes_success(self):
        mock_conn = AsyncMock()
        mock_conn.clear_stale_nodes.return_value = 5
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/nodes/clear")
        resp = await routes.handle_clear_stale_nodes(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"removed": 5}
        mock_conn.clear_stale_nodes.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_clear_stale_nodes_error(self):
        mock_conn = AsyncMock()
        mock_conn.clear_stale_nodes.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/nodes/clear")
        resp = await routes.handle_clear_stale_nodes(request)
        assert resp.status == 500


# ----------------------------------------------------------------------
# handle_delete_node tests
# ----------------------------------------------------------------------
class TestHandleDeleteNode:
    @pytest.mark.asyncio
    async def test_handle_delete_node_success(self):
        mock_conn = AsyncMock()
        mock_conn.delete_node.return_value = True
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/nodes/!123", match_info={"node_id": "!123"})
        resp = await routes.handle_delete_node(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"deleted": "!123"}
        mock_conn.delete_node.assert_called_once_with("!123")

    @pytest.mark.asyncio
    async def test_handle_delete_node_not_found(self):
        mock_conn = AsyncMock()
        mock_conn.delete_node.return_value = False  # Return False to indicate not found
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/nodes/!missing", match_info={"node_id": "!missing"})
        resp = await routes.handle_delete_node(request)
        assert resp.status == 404


# ----------------------------------------------------------------------
# handle_search_messages tests
# ----------------------------------------------------------------------
class TestHandleSearchMessages:
    @pytest.mark.asyncio
    async def test_handle_search_messages_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_messages.return_value = [{"id": "!1", "text": "test message", "timestamp": 1234567890}]
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/messages/search")
        request.query = {"q": "test", "limit": "50"}
        resp = await routes.handle_search_messages(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert isinstance(body, list)
        mock_conn.get_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_search_messages_missing_query(self):
        mock_conn = AsyncMock()
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/messages/search")
        request.query = {}
        resp = await routes.handle_search_messages(request)
        assert resp.status == 400


# ----------------------------------------------------------------------
# handle_messages tests
# ----------------------------------------------------------------------
class TestHandleMessages:
    @pytest.mark.asyncio
    async def test_handle_messages_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_messages.return_value = [{"id": "!1", "text": "msg"}]
        request = make_request(app_dict={"connection": mock_conn})
        resp = await routes.handle_messages(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert isinstance(body, list)
        mock_conn.get_messages.assert_called_once()


# ----------------------------------------------------------------------
# handle_export_messages tests
# ----------------------------------------------------------------------
class TestHandleExportMessages:
    @pytest.mark.asyncio
    async def test_handle_export_messages_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_messages.return_value = [{"id": "!1", "text": "msg", "conversation": "ch:0"}]
        request = make_request(app_dict={"connection": mock_conn}, query={"format": "json", "conversation": "ch:0"})
        resp = await routes.handle_export_messages(request)
        assert resp.status == 200
        mock_conn.get_messages.assert_called_once()


# ----------------------------------------------------------------------
# handle_get_waypoints tests
# ----------------------------------------------------------------------
class TestHandleGetWaypoints:
    @pytest.mark.asyncio
    async def test_handle_get_waypoints_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_waypoints.return_value = [{"name": "wp1", "lat": 1, "lng": 2}]
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/waypoints")
        resp = await routes.handle_get_waypoints(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert isinstance(body, list)
        mock_conn.get_waypoints.assert_called_once()


# ----------------------------------------------------------------------
# handle_add_waypoint tests
# ----------------------------------------------------------------------
class TestHandleAddWaypoint:
    @pytest.mark.asyncio
    async def test_handle_add_waypoint_success(self):
        mock_conn = AsyncMock()
        mock_conn.add_waypoint.return_value = {"name": "wp1"}
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/waypoints", body={"name": "wp1", "lat": 1, "lng": 2})
        resp = await routes.handle_add_waypoint(request)
        assert resp.status == 200
        mock_conn.add_waypoint.assert_called_once()


# ----------------------------------------------------------------------
# handle_update_waypoint tests
# ----------------------------------------------------------------------
class TestHandleUpdateWaypoint:
    @pytest.mark.asyncio
    async def test_handle_update_waypoint_success(self):
        mock_conn = AsyncMock()
        mock_conn.update_waypoint.return_value = {"name": "wp1", "lat": 5}
        request = make_request(app_dict={"connection": mock_conn}, method="PUT", path="/api/waypoints/wp1", body={"lat": 5}, match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 200
        mock_conn.update_waypoint.assert_called_once_with("wp1", {"lat": 5})


# ----------------------------------------------------------------------
# handle_delete_waypoint tests
# ----------------------------------------------------------------------
class TestHandleDeleteWaypoint:
    @pytest.mark.asyncio
    async def test_handle_delete_waypoint_success(self):
        mock_conn = AsyncMock()
        mock_conn.delete_waypoint.return_value = True
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/waypoints/wp1", match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"deleted": "wp1"}
        mock_conn.delete_waypoint.assert_called_once_with("wp1")


# ----------------------------------------------------------------------
# handle_channels tests
# ----------------------------------------------------------------------
class TestHandleChannels:
    @pytest.mark.asyncio
    async def test_handle_channels_success(self):
        mock_conn = AsyncMock()
        mock_conn.refresh_channels.return_value = [{"channel": 0, "name": "Primary"}]
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/channels")
        resp = await routes.handle_channels(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert isinstance(body, list)
        mock_conn.refresh_channels.assert_called_once()


# ----------------------------------------------------------------------
# handle_send tests
# ----------------------------------------------------------------------
class TestHandleSend:
    @pytest.mark.asyncio
    async def test_handle_send_delegates(self):
        mock_conn = AsyncMock()
        mock_conn.send_message.return_value = True
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/send", body={"destination": "!12345678", "text": "hello", "channel": 0})
        resp = await routes.handle_send(request)
        assert resp.status == 200
        mock_conn.send_message.assert_called_once()


# ----------------------------------------------------------------------
# handle_traceroute tests
# ----------------------------------------------------------------------
class TestHandleTraceroute:
    @pytest.mark.asyncio
    async def test_handle_traceroute_delegates(self):
        mock_conn = AsyncMock()
        mock_conn.request_traceroute.return_value = None
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/traceroute", body={"destination": "!12345678"})
        resp = await routes.handle_traceroute(request)
        assert resp.status == 200
        mock_conn.request_traceroute.assert_called_once_with("!12345678")


# ----------------------------------------------------------------------
# handle_request_position tests
# ----------------------------------------------------------------------
class TestHandleRequestPosition:
    @pytest.mark.asyncio
    async def test_handle_request_position_delegates(self):
        mock_conn = AsyncMock()
        mock_conn.request_position.return_value = None
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/position/request", body={"destination": "!12345678"})
        resp = await routes.handle_request_position(request)
        assert resp.status == 200
        mock_conn.request_position.assert_called_once_with("!12345678")


# ----------------------------------------------------------------------
# Additional handlers can be added similarly
# ----------------------------------------------------------------------