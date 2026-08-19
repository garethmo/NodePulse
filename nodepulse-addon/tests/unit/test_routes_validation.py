"""
Unit tests for app/routes.py — input-validation edge cases and the
addon->HA relay waterfall (T3 from the code review).

Covers the critical validation paths that were previously untested:
  * handle_send text/channel/schedule validation
  * handle_add/update/delete_waypoint field validation
  * handle_set_tags / handle_track_node validation
  * device-config handlers (get/put/reload) error mapping
  * handle_packets limit parsing
  * handle_position_history
  * _relay_to_integration candidate ordering + working-URL cache
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import routes


# ----------------------------------------------------------------------
# Helper to create a mock aiohttp request
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
        if "connection" in app_dict and "ignored_nodes" not in app_dict:
            app_dict["ignored_nodes"] = set()
        request.app = app_dict
    else:
        request.app = {}
    request.rel_url = MagicMock()
    request.rel_url.query = query or {}
    if body is not None:
        request._body = json.dumps(body).encode()
        request.content = MagicMock()
        request.content.read = AsyncMock(return_value=json.dumps(body).encode())
    else:
        request._body = b""
        request.content = MagicMock()
        request.content.read = AsyncMock(return_value=b"")
    request.json = AsyncMock(return_value=body if body is not None else {})
    return request


def _conn(**overrides):
    conn = AsyncMock()
    defaults = {
        "send_message": True,
        "add_waypoint": {"id": "local-abc", "name": "Waypoint"},
        "update_waypoint": {"id": "wp1", "name": "Updated"},
        "delete_waypoint": True,
        "set_tags": {"!12345678": ["test"]},
        "get_position_history": {"!12345678": []},
        "get_packet_log": [],
        "get_device_config": {"device": {}},
        "set_device_config": {"applied": True, "section": "device", "reboot_required": False},
        "reload_device_config": (True, ""),
        "get_security_scan": {"findings": [], "has_issues": False, "scanned_at": 0},
    }
    for k, v in defaults.items():
        setattr(conn, k, AsyncMock(return_value=v))
    for k, v in overrides.items():
        setattr(conn, k, v)
    return conn


def _config(**overrides):
    cfg = MagicMock()
    for k, v in {
        "ha_base_url": "",
        "ha_access_token": "",
        "scheduled_messages_enabled": True,
    }.items():
        setattr(cfg, k, v)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ----------------------------------------------------------------------
# handle_send validation
# ----------------------------------------------------------------------
class TestHandleSendValidation:
    @pytest.mark.asyncio
    async def test_empty_text_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={"text": "  "})
        resp = await routes.handle_send(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "'text' field is required and must not be empty"

    @pytest.mark.asyncio
    async def test_missing_text_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={})
        resp = await routes.handle_send(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_broadcast_when_no_destination(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, body={"text": "hi"})
        resp = await routes.handle_send(request)
        assert resp.status == 200
        assert json.loads(resp.body) == {"sent": True}
        conn.send_message.assert_called_once_with("hi", destination=None, channel=0)

    @pytest.mark.asyncio
    async def test_channel_string_coerced(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, body={"text": "hi", "channel": "2"})
        resp = await routes.handle_send(request)
        assert resp.status == 200
        conn.send_message.assert_called_once_with("hi", destination=None, channel=2)

    @pytest.mark.asyncio
    async def test_channel_non_integer_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={"text": "hi", "channel": "abc"})
        resp = await routes.handle_send(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "'channel' must be an integer"

    @pytest.mark.asyncio
    async def test_channel_out_of_range_rejected(self):
        for bad in (-1, 8):
            request = make_request(app_dict={"connection": _conn()}, body={"text": "hi", "channel": bad})
            resp = await routes.handle_send(request)
            assert resp.status == 400
            assert json.loads(resp.body)["error"] == "'channel' must be between 0 and 7"

    @pytest.mark.asyncio
    async def test_schedule_at_invalid_timestamp(self):
        request = make_request(app_dict={"connection": _conn(), "config": _config()},
                               body={"text": "hi", "schedule_at": "not-a-number"})
        resp = await routes.handle_send(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "'schedule_at' must be a unix timestamp"

    @pytest.mark.asyncio
    async def test_schedule_at_disabled_in_config(self):
        request = make_request(app_dict={"connection": _conn(), "config": _config(scheduled_messages_enabled=False)},
                               body={"text": "hi", "schedule_at": 1720000000})
        resp = await routes.handle_send(request)
        assert resp.status == 403
        assert json.loads(resp.body)["error"] == "Scheduled messages are disabled in config"

    @pytest.mark.asyncio
    async def test_schedule_at_valid(self):
        conn = _conn()
        conn.schedule_message = MagicMock()
        request = make_request(app_dict={"connection": conn, "config": _config()},
                               body={"text": "hi", "destination": "!12345678", "schedule_at": 1720000000})
        resp = await routes.handle_send(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"scheduled": True, "schedule_at": 1720000000.0}
        conn.schedule_message.assert_called_once_with(1720000000.0, "!12345678", "hi", 0)

    @pytest.mark.asyncio
    async def test_send_rejected_by_interface(self):
        conn = _conn(send_message=AsyncMock(return_value=False))
        request = make_request(app_dict={"connection": conn}, body={"text": "hi"})
        resp = await routes.handle_send(request)
        assert resp.status == 502


# ----------------------------------------------------------------------
# handle_add_waypoint validation
# ----------------------------------------------------------------------
class TestHandleAddWaypointValidation:
    @pytest.mark.asyncio
    async def test_invalid_lat_rejected(self):
        request = make_request(app_dict={"connection": _conn()},
                               body={"name": "wp", "lat": "abc", "lng": 1.0})
        resp = await routes.handle_add_waypoint(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "lat and lng must be numbers"

    @pytest.mark.asyncio
    async def test_defaults_applied(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, body={"name": "wp"})
        resp = await routes.handle_add_waypoint(request)
        assert resp.status == 200
        waypoint = conn.add_waypoint.call_args.args[0]
        assert waypoint["name"] == "wp"
        assert waypoint["icon"] == "📍"
        assert waypoint["description"] == ""


# ----------------------------------------------------------------------
# handle_update_waypoint validation
# ----------------------------------------------------------------------
class TestHandleUpdateWaypointValidation:
    @pytest.mark.asyncio
    async def test_missing_waypoint_id_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={"lat": 1})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_empty_updates_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={},
                               match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "No valid fields to update"

    @pytest.mark.asyncio
    async def test_invalid_lat_rejected(self):
        request = make_request(app_dict={"connection": _conn()}, body={"lat": "abc"},
                               match_info={"waypoint_id": "wp1"})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "lat must be a number"

    @pytest.mark.asyncio
    async def test_not_found(self):
        conn = _conn(update_waypoint=AsyncMock(return_value=None))
        request = make_request(app_dict={"connection": conn}, body={"lat": 5.0},
                               match_info={"waypoint_id": "missing"})
        resp = await routes.handle_update_waypoint(request)
        assert resp.status == 404


# ----------------------------------------------------------------------
# handle_delete_waypoint validation
# ----------------------------------------------------------------------
class TestHandleDeleteWaypointValidation:
    @pytest.mark.asyncio
    async def test_missing_waypoint_id_rejected(self):
        request = make_request(app_dict={"connection": _conn()})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_not_found(self):
        conn = _conn(delete_waypoint=AsyncMock(return_value=False))
        request = make_request(app_dict={"connection": conn}, match_info={"waypoint_id": "missing"})
        resp = await routes.handle_delete_waypoint(request)
        assert resp.status == 404


# ----------------------------------------------------------------------
# handle_set_tags validation
# ----------------------------------------------------------------------
class TestHandleSetTagsValidation:
    @pytest.mark.asyncio
    async def test_invalid_node_id_rejected(self):
        request = make_request(app_dict={"connection": _conn()},
                               body={"node_id": "bogus", "tags": ["x"]})
        resp = await routes.handle_set_tags(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "'node_id' must be a valid node ID like '!abc12345'"

    @pytest.mark.asyncio
    async def test_tags_not_list_rejected(self):
        request = make_request(app_dict={"connection": _conn()},
                               body={"node_id": "!12345678", "tags": "not-a-list"})
        resp = await routes.handle_set_tags(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_valid(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn},
                               body={"node_id": "!12345678", "tags": ["gateway"]})
        resp = await routes.handle_set_tags(request)
        assert resp.status == 200
        conn.set_tags.assert_called_once_with("!12345678", ["gateway"])


# ----------------------------------------------------------------------
# handle_track_node validation
# ----------------------------------------------------------------------
class TestHandleTrackNode:
    @pytest.mark.asyncio
    async def test_missing_node_id_rejected(self):
        request = make_request(app_dict={}, body={"enabled": True})
        resp = await routes.handle_track_node(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_relay_success(self):
        request = make_request(app_dict={"config": _config()}, body={"node_id": "!12345678", "enabled": True})
        with patch.object(routes, "_relay_to_integration", new_callable=AsyncMock) as mock_relay:
            resp = await routes.handle_track_node(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body == {"node_id": "!12345678", "enabled": True}
        mock_relay.assert_awaited_once()
        _, kwargs = mock_relay.await_args
        assert kwargs["json_body"] == {"node_id": "!12345678", "enabled": True}

    @pytest.mark.asyncio
    async def test_relay_rejected_returns_502(self):
        request = make_request(app_dict={"config": _config()}, body={"node_id": "!12345678"})
        with patch.object(routes, "_relay_to_integration", new_callable=AsyncMock) as mock_relay:
            mock_relay.side_effect = RuntimeError("No relay credential configured")
            resp = await routes.handle_track_node(request)
        assert resp.status == 502


# ----------------------------------------------------------------------
# device-config handlers
# ----------------------------------------------------------------------
class TestHandleGetDeviceConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_get_device_config(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_not_connected_returns_503(self):
        conn = _conn(get_device_config=AsyncMock(side_effect=ConnectionError("not connected")))
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_get_device_config(request)
        assert resp.status == 503


class TestHandlePutDeviceConfigSection:
    @pytest.mark.asyncio
    async def test_missing_section(self):
        request = make_request(app_dict={"connection": _conn()}, body={"role": "ROUTER"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_empty_body(self):
        request = make_request(app_dict={"connection": _conn()}, body={},
                               match_info={"section": "device"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_non_dict_body(self):
        request = make_request(app_dict={"connection": _conn()}, body=[1, 2],
                               match_info={"section": "device"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_validation_error_returns_400(self):
        conn = _conn(set_device_config=AsyncMock(side_effect=ValueError("requires 'confirm': true")))
        request = make_request(app_dict={"connection": conn}, body={"role": "ROUTER"},
                               match_info={"section": "device"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 400
        assert json.loads(resp.body)["error"] == "requires 'confirm': true"

    @pytest.mark.asyncio
    async def test_not_connected_returns_503(self):
        conn = _conn(set_device_config=AsyncMock(side_effect=ConnectionError("not connected")))
        request = make_request(app_dict={"connection": conn}, body={"role": "ROUTER", "confirm": True},
                               match_info={"section": "device"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, body={"role": "CLIENT"},
                               match_info={"section": "device"})
        resp = await routes.handle_put_device_config_section(request)
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body["applied"] is True


class TestHandleReloadDeviceConfig:
    @pytest.mark.asyncio
    async def test_reloaded(self):
        request = make_request(app_dict={"connection": _conn()})
        resp = await routes.handle_reload_device_config(request)
        assert resp.status == 200
        assert json.loads(resp.body) == {"reloaded": True}

    @pytest.mark.asyncio
    async def test_not_connected(self):
        conn = _conn(reload_device_config=AsyncMock(return_value=(False, "not_connected")))
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_reload_device_config(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_request_failed(self):
        conn = _conn(reload_device_config=AsyncMock(return_value=(False, "request_failed: timeout")))
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_reload_device_config(request)
        assert resp.status == 503


# ----------------------------------------------------------------------
# handle_packets / handle_position_history
# ----------------------------------------------------------------------
class TestHandlePackets:
    @pytest.mark.asyncio
    async def test_default_limit(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_packets(request)
        assert resp.status == 200
        conn.get_packet_log.assert_called_once_with(200)

    @pytest.mark.asyncio
    async def test_invalid_limit_falls_back(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, query={"limit": "abc"})
        resp = await routes.handle_packets(request)
        assert resp.status == 200
        conn.get_packet_log.assert_called_once_with(200)

    @pytest.mark.asyncio
    async def test_limit_capped(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, query={"limit": "5000"})
        resp = await routes.handle_packets(request)
        assert resp.status == 200
        conn.get_packet_log.assert_called_once_with(500)


class TestHandlePositionHistory:
    @pytest.mark.asyncio
    async def test_with_node_id(self):
        conn = _conn()
        request = make_request(app_dict={"connection": conn}, match_info={"node_id": "!12345678"})
        resp = await routes.handle_position_history(request)
        assert resp.status == 200
        conn.get_position_history.assert_called_once_with("!12345678")

    @pytest.mark.asyncio
    async def test_error_returns_500(self):
        conn = _conn(get_position_history=AsyncMock(side_effect=Exception("boom")))
        request = make_request(app_dict={"connection": conn})
        resp = await routes.handle_position_history(request)
        assert resp.status == 500


# ----------------------------------------------------------------------
# _relay_to_integration candidate waterfall
# ----------------------------------------------------------------------
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


class _RecordingSession:
    """Session that records every URL tried and returns a canned response."""

    def __init__(self, status_for_url=None, default_status=200):
        self.urls = []
        self.requests = []
        self._status_for_url = status_for_url or (lambda url: default_status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        self.requests.append({"method": method, "url": url, "kwargs": kwargs})
        status = self._status_for_url(url)
        body = json.dumps({"node_ids": ["!12345678"]})
        return _FakeResp(status, body)


@pytest.fixture(autouse=True)
def _reset_working_cache():
    routes._working_ha_base = None
    yield
    routes._working_ha_base = None


def _patch_session(session):
    return patch.object(routes.aiohttp, "ClientSession", lambda *a, **k: session)


class TestRelayWaterfall:
    @pytest.mark.asyncio
    async def test_tries_supervisor_hostname_first(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-1")})
        session = _RecordingSession()
        with _patch_session(session), patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert session.urls
        assert session.urls[0].startswith("http://homeassistant:8123")

    @pytest.mark.asyncio
    async def test_advances_until_success(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-1")})
        # First two supervisor candidates are unreachable; the third answers.
        def status_for_url(url):
            if "homeassistant" in url or "supervisor" in url:
                raise OSError("connection refused")
            return 200
        session = _RecordingSession(status_for_url=status_for_url)
        with _patch_session(session), patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert session.urls[0].startswith("http://homeassistant:8123")
        assert session.urls[1].startswith("http://supervisor:8123")
        assert session.urls[2].startswith("http://hassio:8123")
        assert session.urls[2].endswith("/api/nodepulse/tracked-nodes")

    @pytest.mark.asyncio
    async def test_cached_url_used_on_next_call(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-1")})
        # First candidate works -> cached.
        session1 = _RecordingSession()
        with _patch_session(session1), patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert routes._working_ha_base == "http://homeassistant:8123"

        # Second call should go straight to the cached URL (single request).
        session2 = _RecordingSession()
        with _patch_session(session2), patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert len(session2.urls) == 1
        assert session2.urls[0].startswith("http://homeassistant:8123")

    @pytest.mark.asyncio
    async def test_cache_reset_when_cached_url_fails(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-1")})
        routes._working_ha_base = "http://homeassistant:8123"

        def status_for_url(url):
            if "homeassistant" in url:
                raise OSError("refused")
            return 200
        session = _RecordingSession(status_for_url=status_for_url)
        with _patch_session(session), patch.dict("os.environ", {}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        # Cache cleared after failure, so it fell through to the fallback candidates.
        assert routes._working_ha_base != "http://homeassistant:8123"

    @pytest.mark.asyncio
    async def test_all_candidates_fail_raises(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-1")})
        session = _RecordingSession(status_for_url=lambda url: (_ for _ in ()).throw(OSError("refused")))
        with _patch_session(session), patch.dict("os.environ", {}, clear=True), \
                pytest.raises(RuntimeError, match="Could not reach the NodePulse integration"):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")

    @pytest.mark.asyncio
    async def test_401_retries_with_second_token(self):
        request = make_request(app_dict={"config": _config(ha_access_token="ll-tok-2")})
        # Supervisor token rejected (401) everywhere; long-lived token accepted.

        class _HeaderAwareSession(_RecordingSession):
            def __init__(self):
                super().__init__()
                self.winning_auth = None

            def request(self, method, url, **kwargs):
                self.urls.append(url)
                self.requests.append({"method": method, "url": url, "kwargs": kwargs})
                auth = kwargs.get("headers", {}).get("Authorization", "")
                if auth == "Bearer ll-tok-2":
                    self.winning_auth = auth
                    return _FakeResp(200, json.dumps({"node_ids": ["!12345678"]}))
                return _FakeResp(401, json.dumps({"error": "unauthorized"}))

        session = _HeaderAwareSession()
        with _patch_session(session), patch.dict("os.environ", {"SUPERVISOR_TOKEN": "sup-tok-1"}, clear=True):
            await routes._relay_to_integration(request, "GET", "/api/nodepulse/tracked-nodes")
        assert session.winning_auth == "Bearer ll-tok-2"