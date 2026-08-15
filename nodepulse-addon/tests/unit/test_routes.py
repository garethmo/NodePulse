"""
Unit tests for app/routes.py
"""
import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from aiohttp import web
import asyncio

# Import the module under test
from app import routes

# ----------------------------------------------------------------------
# Helper fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def sample_body():
    return {"test": "data"}

@pytest.fixture
def sample_headers():
    return {}

# ----------------------------------------------------------------------
# _validate_destination tests
# ----------------------------------------------------------------------
class TestValidateDestination:
    @patch("app.routes.DeviceQuery")
    @patch("app.routes.qrcode")
    @patch("app.routes.DeviceDataPushRealm.stream_msg")
    def test_validate_destination_normal(self, mock_stream_msg, mock_qrcode, mock_device_query):
        """Valid destination should call the device query and stream method."""
        valid_dest = {"address": "!12345678", "channel": 0}
        with patch.dict("app.routes.topology", {"address": "!12345678", "channel": 0}):
            result = routes._validate_destination(sample_body, valid_dest, {}, {})
            # No exception means test passes
            assert result is None

    @patch("app.routes.DeviceQuery")
    @patch("app.routes.qrcode")
    @patch("app.routes.DeviceDataPushRealm.stream_msg")
    def test_validate_destination_missing_address(self, mock_stream_msg, mock_qrcode, mock_device_query):
        """Missing address should raise ValueError."""
        dest = {"channel": 0}
        with patch.dict("app.routes.topology", {"address": "!12345678", "channel": 0}):
            with pytest.raises(ValueError, match="Valid destination required"):
                routes._validate_destination(sample_body, dest, {}, {})

    @patch("app.routes.DeviceQuery")
    @patch("app.routes.qrcode")
    @patch("app.routes.DeviceDataPushRealm.stream_msg")
    def test_validate_destination_missing_channel(self, mock_stream_msg, mock_qrcode, mock_device_query):
        """Missing channel should raise ValueError."""
        dest = {"address": "!12345678"}
        with patch.dict("app.routes.topology", {"address": "!12345678", "channel": 0}):
            with pytest.raises(ValueError, match="Valid destination required"):
                routes._validate_destination(sample_body, dest, {}, {})

    @patch("app.routes.DeviceQuery")
    @patch("app.routes.qrcode")
    @patch("app.routes.DeviceDataPushRealm.stream_msg")
    def test_validate_destination_invalid_address(self, mock_stream_msg, mock_qrcode, mock_device_query):
        """Invalid address should raise ValueError."""
        dest = {"address": "invalidaddr", "channel": 0}
        with patch.dict("app.routes.topology", {"address": "!12345678", "channel": 0}):
            with pytest.raises(ValueError, match="Valid destination required"):
                routes._validate_destination(sample_body, dest, {}, {})

# ----------------------------------------------------------------------
# _apply_access_key tests
# ----------------------------------------------------------------------
class TestApplyAccessKey:
    @patch("app.routes._send_custom_event")
    def test_apply_access_key_good(self, mock_send_event):
        request = MagicMock()
        request.path = "/some/path"
        request.method = "POST"
        request.transport = MagicMock()
        with patch("app.routes.access_key", {"valid_key": "abc"}):
            routes._apply_access_key(request, "abc")
            mock_send_event.assert_called_once()

    @patch("app.routes._send_custom_event")
    def test_apply_access_key_bad(self, mock_send_event):
        request = MagicMock()
        request.path = "/some/path"
        request.method = "POST"
        request.transport = MagicMock()
        with patch("app.routes.access_key", {"valid_key": "abc"}):
            with pytest.raises(ValueError, match="Access key required"):
                routes._apply_access_key(request, "wrong_key")
            mock_send_event.assert_not_called()

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
        assert body == {"message": "Bad request"}

# ----------------------------------------------------------------------
# handle_status tests
# ----------------------------------------------------------------------
class TestHandleStatus:
    @patch("app.routes.render_template")
    @patch("app.routes.get_node_by_id")
    def test_handle_status_success(self, mock_get_node, mock_render):
        mock_node = MagicMock()
        mock_node.name = "TestNode"
        mock_node.id = "!12345678"
        mock_get_node.return_value = mock_node
        mock_render.return_value = MagicMock(
            body=any,  # we don't inspect rendered body here
            status=200
        )
        result = routes.handle_status(MagicMock())
        assert result.status == 200
        mock_get_node.assert_called_once_with("!12345678")
        mock_render.assert_called_once()

    @patch("app.routes.render_template")
    @patch("app.routes.get_node_by_id")
    def test_handle_status_node_not_found(self, mock_get_node, mock_render):
        mock_get_node.side_effect = KeyError("node not found")
        result = routes.handle_status(MagicMock())
        assert result.status == 404
        mock_get_node.assert_called_once_with("!someid")

# ----------------------------------------------------------------------
# handle_clear_stale_nodes tests
# ----------------------------------------------------------------------
class TestHandleClearStaleNodes:
    @patch("app.routes.clear_stale_nodes")
    @patch("app.routes.render_template")
    def test_handle_clear_stale_nodes_success(self, mock_render, mock_clear):
        mock_clear.return_value = 5
        result = routes.handle_clear_stale_nodes(MagicMock(), "!123")
        assert result.status == 200
        mock_clear.assert_called_once_with("!123")

    @patch("app.routes.render_template")
    @patch("app.routes.clear_stale_nodes")
    def test_handle_clear_stale_nodes_not_found(self, mock_render, mock_clear):
        mock_clear.side_effect = KeyError("node not found")
        with pytest.raises(KeyError):
            routes.handle_clear_stale_nodes(MagicMock(), "!missing")
        mock_clear.assert_called_once_with("!missing")

# ----------------------------------------------------------------------
# handle_delete_node tests
# ----------------------------------------------------------------------
class TestHandleDeleteNode:
    @patch("app.routes.delete_node")
    @patch("app.routes.render_template")
    def test_handle_delete_node_success(self, mock_render, mock_delete):
        result = routes.handle_delete_node(MagicMock(), "!123")
        assert result.status == 200
        mock_delete.assert_called_once_with("!123")

    @patch("app.routes.delete_node")
    @patch("app.routes.render_template")
    def test_handle_delete_node_not_found(self, mock_render, mock_delete):
        mock_delete.side_effect = KeyError("node not found")
        with pytest.raises(KeyError):
            routes.handle_delete_node(MagicMock(), "!missing")
        mock_delete.assert_called_once_with("!missing")

# ----------------------------------------------------------------------
# handle_search_messages tests
# ----------------------------------------------------------------------
class TestHandleSearchMessages:
    @patch("app.routes.search_messages")
    @patch("app.routes.render_template")
    def test_handle_search_messages_success(self, mock_render, mock_search):
        mock_search.return_value = [{"id": "!1", "text": "msg"}]
        result = routes.handle_search_messages(MagicMock(), "query")
        assert result.status == 200
        mock_search.assert_called_once_with("query")

    @patch("app.routes.search_messages")
    @patch("app.routes.render_template")
    def test_handle_search_messages_not_found(self, mock_render, mock_search):
        mock_search.side_effect = KeyError("not found")
        with pytest.raises(KeyError):
            routes.handle_search_messages(MagicMock(), "q")
        mock_search.assert_called_once_with("q")

# ----------------------------------------------------------------------
# handle_messages tests
# ----------------------------------------------------------------------
class TestHandleMessages:
    @patch("app.routes.handle_messages")
    def test_handle_messages_delegates(self, mock_handle_messages):
        routes.handle_messages(MagicMock(), "path", "GET")
        mock_handle_messages.assert_called_once()

# ----------------------------------------------------------------------
# handle_export_messages tests
# ----------------------------------------------------------------------
class TestHandleExportMessages:
    @patch("app.routes.handle_export_messages")
    def test_handle_export_messages_delegates(self, mock_handle):
        routes.handle_export_messages(MagicMock(), "path")
        mock_handle.assert_called_once()

# ----------------------------------------------------------------------
# handle_get_waypoints tests
# ----------------------------------------------------------------------
class TestHandleGetWaypoints:
    @patch("app.routes.get_waypoints")
    @patch("app.routes.render_template")
    def test_handle_get_waypoints_success(self, mock_render, mock_get):
        mock_get.return_value = [{"name": "wp1", "lat": 1}]
        result = routes.handle_get_waypoints(MagicMock(), "")
        assert result.status == 200
        mock_get.assert_called_once()

# ----------------------------------------------------------------------
# handle_add_waypoint tests
# ----------------------------------------------------------------------
class TestHandleAddWaypoint:
    @patch("app.routes.add_waypoint")
    @patch("app.routes.render_template")
    def test_handle_add_waypoint_success(self, mock_render, mock_add):
        routes.handle_add_waypoint(MagicMock(), "name", {"lat": 1})
        mock_add.assert_called_once()

# ----------------------------------------------------------------------
# handle_update_waypoint tests
# ----------------------------------------------------------------------
class TestHandleUpdateWaypoint:
    @patch("app.routes.update_waypoint")
    @patch("app.routes.render_template")
    def test_handle_update_waypoint_success(self, mock_render, mock_up):
        routes.handle_update_waypoint(MagicMock(), "id", {"lat": 2})
        mock_up.assert_called_once_with("id", {"lat": 2})

# ----------------------------------------------------------------------
# handle_delete_waypoint tests
# ----------------------------------------------------------------------
class TestHandleDeleteWaypoint:
    @patch("app.routes.delete_waypoint")
    @patch("app.routes.render_template")
    def test_handle_delete_waypoint_success(self, mock_render, mock_del):
        routes.handle_delete_waypoint(MagicMock(), "id")
        mock_del.assert_called_once_with("id")

# ----------------------------------------------------------------------
# handle_channels tests
# ----------------------------------------------------------------------
class TestHandleChannels:
    @patch("app.routes.get_channels")
    @patch("app.routes.render_template")
    def test_handle_channels_success(self, mock_render, mock_get):
        mock_get.return_value = [{"channel": 0}]
        result = routes.handle_channels(MagicMock())
        assert result.status == 200
        mock_get.assert_called_once()

# ----------------------------------------------------------------------
# handle_send tests
# ----------------------------------------------------------------------
class TestHandleSend:
    @patch("app.routes._send_custom_event")
    def test_handle_send_delegates(self, mock_send):
        routes.handle_send(MagicMock(), "", "text", 0, "channel")
        mock_send.assert_called_once()

# ----------------------------------------------------------------------
# handle_traceroute tests
# ----------------------------------------------------------------------
class TestHandleTraceroute:
    @patch("app.routes.handle_traceroute")
    def test_handle_traceroute_delegates(self, mock_handle):
        routes.handle_traceroute(MagicMock(), "path")
        mock_handle.assert_called_once()

# ----------------------------------------------------------------------
# handle_request_position tests
# ----------------------------------------------------------------------
class TestHandleRequestPosition:
    @patch("app.routes.handle_request_position")
    def test_handle_request_position_delegates(self, mock_handle):
        routes.handle_request_position(MagicMock(), "")
        mock_handle.assert_called_once()