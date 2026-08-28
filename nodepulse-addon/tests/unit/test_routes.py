"""
Unit tests for app/routes.py
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        assert "ha_access_token_set" in body["config"]
        mock_conn.get_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_status_reports_ha_access_token_set(self):
        mock_conn = AsyncMock()
        mock_conn.get_status.return_value = {"connected": True, "my_info": {}}
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
            "ha_access_token": "ll-tok-456",
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
        assert body["config"]["ha_access_token_set"] is True

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
        body = json.loads(resp.body)
        assert body == {"error": "Failed to retrieve status"}
        

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

    @pytest.mark.asyncio
    async def test_handle_nodes_error(self):
        """Test that handle_nodes returns 500 on connection error."""
        mock_conn = AsyncMock()
        mock_conn.get_nodes.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/nodes")
        resp = await routes.handle_nodes(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to retrieve nodes"}


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
        body = json.loads(resp.body)
        assert body == {"error": "Failed to clear stale nodes"}


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
        body = json.loads(resp.body)
        assert body == {"error": "Node not found"}
        mock_conn.delete_node.assert_called_once_with("!missing")

    @pytest.mark.asyncio
    async def test_delete_node_error(self):
        """Test that handle_delete_node returns 500 when delete_node raises."""
        mock_conn = AsyncMock()
        mock_conn.delete_node.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/nodes/!error", match_info={"node_id": "!error"})
        resp = await routes.handle_delete_node(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to delete node"}


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

    @pytest.mark.asyncio
    async def test_handle_add_waypoint_error(self):
        """Test that handle_add_waypoint returns 500 on internal error."""
        mock_conn = AsyncMock()
        mock_conn.add_waypoint.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/waypoints", body={"name": "wp1", "lat": 1, "lng": 2})
        resp = await routes.handle_add_waypoint(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to add waypoint"}


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

    @pytest.mark.asyncio
    async def test_handle_update_waypoint_error(self):
        """Test that handle_update_waypoint returns 500 on internal error."""
        mock_conn = AsyncMock()
        mock_conn.update_waypoint.side_effect = Exception("Update failed")
        request = make_request(app_dict={"connection": mock_conn}, method="PUT", path="/api/waypoints/wp1", body={"lat": 5}, match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to update waypoint"}


# ----------------------------------------------------------------------
# handle_delete_waypoint tests
# ----------------------------------------------------------------------
class TestHandleDeleteWaypoint:
    @pytest.mark.asyncio
    async def test_delete_waypoint_success(self):
        mock_conn = AsyncMock()
        mock_conn.delete_waypoint.return_value = True
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/waypoints/wp1", match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"deleted": "wp1"}
        mock_conn.delete_waypoint.assert_called_once_with("wp1")

    @pytest.mark.asyncio
    async def test_delete_waypoint_not_found(self):
        mock_conn = AsyncMock()
        mock_conn.delete_waypoint.return_value = False  # Return False to indicate not found
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/waypoints/wp1", match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 404
        body = json.loads(resp.body)
        assert body == {"error": "Waypoint not found"}
        mock_conn.delete_waypoint.assert_called_once_with("wp1")

    @pytest.mark.asyncio
    async def test_delete_waypoint_error(self):
        """Test that handle_delete_waypoint returns 500 when delete_node raises."""
        mock_conn = AsyncMock()
        mock_conn.delete_waypoint.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="DELETE", path="/api/waypoints/wp1", match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to delete waypoint"}


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

    @pytest.mark.asyncio
    async def test_handle_traceroute_error(self):
        """Test that handle_traceroute returns 500 on internal error."""
        mock_conn = AsyncMock()
        mock_conn.request_traceroute.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/traceroute", body={"destination": "!12345678"})
        resp = await routes.handle_traceroute(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to dispatch traceroute"}


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

    @pytest.mark.asyncio
    async def test_handle_request_position_error(self):
        """Test that handle_request_position returns 500 on internal error."""
        mock_conn = AsyncMock()
        mock_conn.request_position.side_effect = Exception("Network error")
        request = make_request(app_dict={"connection": mock_conn}, method="POST", path="/api/position/request", body={"destination": "!12345678"})
        resp = await routes.handle_request_position(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to dispatch position request"}


# ----------------------------------------------------------------------
# handle_favorites / handle_set_favorite tests
# ----------------------------------------------------------------------
class TestHandleFavorites:
    @pytest.mark.asyncio
    async def test_handle_favorites_success(self):
        mock_conn = AsyncMock()
        mock_conn.get_favorites.return_value = ["!12345678", "!87654321"]
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/favorites")
        resp = await routes.handle_favorites(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == ["!12345678", "!87654321"]
        mock_conn.get_favorites.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_favorites_error(self):
        mock_conn = AsyncMock()
        mock_conn.get_favorites.side_effect = Exception("DB error")
        request = make_request(app_dict={"connection": mock_conn}, method="GET", path="/api/favorites")
        resp = await routes.handle_favorites(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to retrieve favorites"}

    @pytest.mark.asyncio
    async def test_handle_set_favorite_success(self):
        mock_conn = AsyncMock()
        mock_conn.set_favorite.return_value = ["!12345678"]
        request = make_request(
            app_dict={"connection": mock_conn}, method="PUT", path="/api/favorites",
            body={"node_id": "!12345678", "favorited": True},
        )
        resp = await routes.handle_set_favorite(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == ["!12345678"]
        mock_conn.set_favorite.assert_called_once_with("!12345678", True)

    @pytest.mark.asyncio
    async def test_handle_set_favorite_invalid_node_id(self):
        mock_conn = AsyncMock()
        request = make_request(
            app_dict={"connection": mock_conn}, method="PUT", path="/api/favorites",
            body={"node_id": "bogus", "favorited": True},
        )
        resp = await routes.handle_set_favorite(request)
        assert resp.status == 400
        mock_conn.set_favorite.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_set_favorite_invalid_bool(self):
        mock_conn = AsyncMock()
        request = make_request(
            app_dict={"connection": mock_conn}, method="PUT", path="/api/favorites",
            body={"node_id": "!12345678", "favorited": "yes"},
        )
        resp = await routes.handle_set_favorite(request)
        assert resp.status == 400
        mock_conn.set_favorite.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_set_favorite_error(self):
        mock_conn = AsyncMock()
        mock_conn.set_favorite.side_effect = Exception("DB error")
        request = make_request(
            app_dict={"connection": mock_conn}, method="PUT", path="/api/favorites",
            body={"node_id": "!12345678", "favorited": True},
        )
        resp = await routes.handle_set_favorite(request)
        assert resp.status == 500
        body = json.loads(resp.body)
        assert body == {"error": "Failed to set favorite"}


# ----------------------------------------------------------------------
# _relay_to_integration token-selection tests
# ----------------------------------------------------------------------
class TestRelayTokenSelection:
    """Verify the relay sends the Supervisor token, the configured HA access
    token, or no Bearer header depending on what is available."""

    def _patch_session(self, resp_status=200, resp_json=None, status_for_auth=None):
        """Patch routes' aiohttp.ClientSession with a recorder that returns
        a canned response and captures the outbound headers."""
        recorded = {}

        class _FakeResp:
            def __init__(self, status, body):
                self.status = status
                self._body = body
                self.headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def text(self):
                return self._body

        class _FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def request(self, method, url, **kwargs):
                recorded["url"] = url
                recorded["headers"] = kwargs.get("headers", {})
                auth = recorded["headers"].get("Authorization", "")
                status = status_for_auth(auth) if status_for_auth else resp_status
                body = resp_json if resp_json is not None else json.dumps({"node_ids": ["!12345678"]})
                return _FakeResp(status, body)

        patcher = patch.object(routes.aiohttp, "ClientSession", _FakeSession)
        return patcher, recorded

    @pytest.mark.asyncio
    async def test_uses_supervisor_token_when_available(self):
        request = make_request(app_dict={"config": MagicMock(ha_base_url="", ha_access_token="")})
        patcher, recorded = self._patch_session()
        with patcher, patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok-123"}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert recorded["headers"].get("Authorization") == "Bearer sup-tok-123"

    @pytest.mark.asyncio
    async def test_falls_back_to_ha_access_token(self):
        request = make_request(app_dict={"config": MagicMock(ha_base_url="", ha_access_token="ll-tok-456")})
        patcher, recorded = self._patch_session()
        # No SUPERVISOR_TOKEN in the environment.
        with patcher, patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert recorded["headers"].get("Authorization") == "Bearer ll-tok-456"

    @pytest.mark.asyncio
    async def test_supervisor_token_takes_precedence_over_access_token(self):
        request = make_request(app_dict={"config": MagicMock(ha_base_url="", ha_access_token="ll-tok-456")})
        patcher, recorded = self._patch_session()
        with patcher, patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok-123"}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert recorded["headers"].get("Authorization") == "Bearer sup-tok-123"

    @pytest.mark.asyncio
    async def test_retries_with_access_token_when_supervisor_token_rejected(self):
        request = make_request(app_dict={"config": MagicMock(ha_base_url="", ha_access_token="ll-tok-456")})
        # All candidates reject the Supervisor token (401) but accept the long-lived token.
        def status_for_auth(auth):
            return 200 if auth == "Bearer ll-tok-456" else 401
        patcher, recorded = self._patch_session(status_for_auth=status_for_auth)
        with patcher, patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok-123"}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert recorded["headers"].get("Authorization") == "Bearer ll-tok-456"

    @pytest.mark.asyncio
    async def test_no_token_raises_configuration_error(self):
        request = make_request(app_dict={"config": MagicMock(ha_base_url="", ha_access_token="")})
        patcher, recorded = self._patch_session()
        with patcher, patch.dict("os.environ", {}, clear=True), \
                pytest.raises(RuntimeError, match="No relay credential configured"):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert "headers" not in recorded

# ----------------------------------------------------------------------
# Remote node administration routes
# ----------------------------------------------------------------------
class TestRemoteAdminRoutes:
    async def _conn(self):
        conn = AsyncMock()
        conn.remote_admin_available.return_value = {
            "available": True,
            "admin_channel_index": 1,
            "actions": {"reboot": {"label": "Reboot", "danger": False, "confirm": False}},
        }
        conn.get_remote_config.return_value = {"device": {}, "_schema": {}}
        conn.set_remote_config.return_value = {"applied": True, "section": "device", "reboot_required": False}
        conn.remote_admin_action.return_value = {"action": "reboot", "ok": True, "elapsed_s": 1.0}
        return conn

    @pytest.mark.asyncio
    async def test_admin_available(self):
        conn = await self._conn()
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_admin_available(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["available"] is True

    @pytest.mark.asyncio
    async def test_get_remote_config_ok(self):
        conn = await self._conn()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!1234abcd"})
        resp = await routes.handle_get_remote_config(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert "device" in data
        conn.get_remote_config.assert_awaited_once_with("!1234abcd", force=False)

    @pytest.mark.asyncio
    async def test_get_remote_config_invalid_node_id(self):
        conn = await self._conn()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "garbage"})
        resp = await routes.handle_get_remote_config(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_get_remote_config_timeout(self):
        conn = await self._conn()
        conn.get_remote_config.side_effect = ConnectionError("timed out")
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!1234abcd"})
        resp = await routes.handle_get_remote_config(request)
        assert resp.status == 504

    @pytest.mark.asyncio
    async def test_put_remote_config_ok(self):
        conn = await self._conn()
        request = make_request(
            app_dict={"connection": conn},
            match_info={"node_id": "!1234abcd", "section": "device"},
            body={"node_info_broadcast_secs": 123},
        )
        resp = await routes.handle_put_remote_config_section(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["applied"] is True
        conn.set_remote_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_remote_config_missing_section(self):
        conn = await self._conn()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!1234abcd", "section": ""})
        resp = await routes.handle_put_remote_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_remote_config_invalid_json(self):
        conn = await self._conn()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!1234abcd", "section": "device"})
        request.json = AsyncMock(side_effect=ValueError("bad json"))
        resp = await routes.handle_put_remote_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_remote_config_validation_error(self):
        conn = await self._conn()
        conn.set_remote_config.side_effect = ValueError("Unknown section: nope")
        request = make_request(
            app_dict={"connection": conn},
            match_info={"node_id": "!1234abcd", "section": "nope"},
            body={"x": 1},
        )
        resp = await routes.handle_put_remote_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_admin_action_ok(self):
        conn = await self._conn()
        request = make_request(
            app_dict={"connection": conn},
            match_info={"node_id": "!1234abcd", "action": "reboot"},
            body={"seconds": 5},
        )
        resp = await routes.handle_admin_action(request)
        assert resp.status == 200
        conn.remote_admin_action.assert_awaited_once_with("!1234abcd", "reboot", {"seconds": 5})

    @pytest.mark.asyncio
    async def test_admin_action_unknown_action(self):
        conn = await self._conn()
        conn.remote_admin_action.side_effect = ValueError("Unknown admin action")
        request = make_request(
            app_dict={"connection": conn},
            match_info={"node_id": "!1234abcd", "action": "frobnicate"},
        )
        resp = await routes.handle_admin_action(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_admin_action_timeout(self):
        conn = await self._conn()
        conn.remote_admin_action.side_effect = ConnectionError("timed out")
        request = make_request(
            app_dict={"connection": conn},
            match_info={"node_id": "!1234abcd", "action": "reboot"},
        )
        resp = await routes.handle_admin_action(request)
        assert resp.status == 504


# ----------------------------------------------------------------------
# 2.8 firmware feature routes
# ----------------------------------------------------------------------
class TestRoutes28Features:
    @pytest.mark.asyncio
    async def test_handle_node_signal_returns_diagnostics(self):
        conn = MagicMock()
        conn.get_node_signal = AsyncMock(
            return_value={"id": "!abc123", "snr_avg": 5.2, "signal_quality": "good"}
        )
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!abc123"})
        resp = await routes.handle_node_signal(request)
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["id"] == "!abc123"
        assert body["snr_avg"] == 5.2

    @pytest.mark.asyncio
    async def test_handle_node_signal_404_when_unknown(self):
        conn = MagicMock()
        conn.get_node_signal = AsyncMock(return_value={})
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!abcdef12"})
        resp = await routes.handle_node_signal(request)
        assert resp.status == 404
        assert "error" in json.loads(resp.text)

    @pytest.mark.asyncio
    async def test_handle_node_signal_rejects_bad_id(self):
        conn = MagicMock()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "not-a-node"})
        resp = await routes.handle_node_signal(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_handle_hops_distribution(self):
        conn = MagicMock()
        conn.get_nodes = AsyncMock(return_value=[
            {"id": "!a", "hops_away": 0},
            {"id": "!b", "hops_away": 1},
            {"id": "!c", "hops_away": 1},
            {"id": "!d", "hops_away": 2},
            {"id": "!e", "hops_away": None},  # ignored (no hops_away)
        ])
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_hops(request)
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["total"] == 4
        assert body["max_hops"] == 2
        by_hop = {d["hops"]: d["count"] for d in body["distribution"]}
        assert by_hop == {0: 1, 1: 2, 2: 1}

    @pytest.mark.asyncio
    async def test_handle_beacon_unavailable_on_old_lib(self):
        conn = MagicMock()
        conn.get_beacon_config = AsyncMock(
            return_value={"available": False, "reason": "meshtastic library predates 2.8"}
        )
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_beacon(request)
        assert resp.status == 200
        assert json.loads(resp.text)["available"] is False

    @pytest.mark.asyncio
    async def test_handle_node_gpx_builds_track(self):
        conn = MagicMock()
        conn.get_position_history = AsyncMock(return_value={
            "!abc123": [
                {"latitude": -29.85, "longitude": 31.02, "altitude": 120, "timestamp": 1700000000},
                {"latitude": -29.86, "longitude": 31.05, "timestamp": 1700000100},
            ]
        })
        conn.get_nodes = AsyncMock(return_value=[{"id": "!abc123", "long_name": "NodeA"}])
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!abc123"})
        resp = await routes.handle_node_gpx(request)
        assert resp.status == 200
        body = resp.text
        assert "<trk>" in body
        assert "NodeA" in body
        assert "trkpt" in body

    @pytest.mark.asyncio
    async def test_handle_node_gpx_rejects_bad_id(self):
        conn = MagicMock()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "bad"})
        resp = await routes.handle_node_gpx(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_handle_terrain_coverage_returns_503_when_disabled(self):
        request = make_request(app_dict={"terrain": None})
        resp = await routes.handle_terrain_coverage(request)
        assert resp.status == 503
        body = json.loads(resp.text)
        assert body["error"] == "Terrain analysis is not enabled"

