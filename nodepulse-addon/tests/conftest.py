import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add app to path so we can import it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

# Route is referenced by _abort_cdn below; the browser import stays local to the
# async_page fixture so a bare pytest run (no Playwright) still collects.
from playwright.async_api import Route

from app.config import Config
from app.main import build_app


@pytest.fixture
def mock_config():
    config = Config(
        log_level="DEBUG",
        connection_type="direct",
        meshtastic_host="localhost",
        meshtastic_port=4403,
        proxy_host=None,
        proxy_port=4403,
        access_key="",
        scan_interval=30,
        ignored_nodes=[],
        mqtt_enabled=False,
        telegram_enabled=False
    )
    return config

@pytest.fixture
def mock_connection():
    """Contract-faithful stand-in for MeshtasticConnection.

    Unlike a bare MagicMock, this stub carries realistic in-memory state and
    returns the same shapes the real connection produces, so handler tests
    catch contract drift (e.g. ``refresh_channels`` returning a list of dicts
    with specific keys, ``handle_status`` reading ``_scheduled_messages_lock``).
    Every public async method is an AsyncMock with a side effect that returns
    realistic data and records calls, so existing ``assert_called_once_with``
    assertions keep working and tests can still replace individual methods
    (e.g. ``conn.delete_node = AsyncMock(return_value=False)``).
    """
    conn = MagicMock()

    # Realistic in-memory state backing the async methods.
    state = {
        "nodes": [
            {"id": "!12345678", "long_name": "Test Node 1", "short_name": "TN1",
             "snr": 5.0, "snr_avg": 5.0, "battery_level": 80, "latitude": 40.0,
             "longitude": -74.0, "hops_away": 1},
            {"id": "!87654321", "long_name": "Test Node 2", "short_name": "TN2",
             "snr": -2.0, "snr_avg": -2.0, "battery_level": 50, "latitude": 41.0,
             "longitude": -73.0, "hops_away": 2},
        ],
        "channels": [{"index": 0, "name": "LongFast", "role": "PRIMARY"}],
        "messages": [
            {"id": "msg1", "text": "Hello world", "from_id": "!12345678",
             "channel": 0, "timestamp": 1000000, "conversation": "ch:0"},
        ],
        "waypoints": [],
        "tags": {},
        "favorites": [],
        "position_history": {},
        "packet_log": [],
        "device_config": {"device": {"role": "CLIENT"}, "lora": {}},
        "scheduled_messages": [],
    }

    async def _get_status():
        return {"connected": True, "uptime": 1000, "my_info": {"my_node_id": "!99999999"}}

    async def _get_nodes():
        return list(state["nodes"])

    async def _get_channels():
        return list(state["channels"])

    async def _refresh_channels():
        return list(state["channels"])

    async def _get_messages():
        return list(state["messages"])

    async def _get_position_history(node_id=None):
        if node_id is None:
            return dict(state["position_history"])
        return list(state["position_history"].get(node_id, []))

    async def _get_waypoints():
        return list(state["waypoints"])

    async def _get_tags():
        return dict(state["tags"])

    async def _set_tags(node_id, tags):
        state["tags"][node_id] = list(tags)
        return dict(state["tags"])

    async def _send_message(text, destination=None, channel=0):
        return True

    async def _add_waypoint(waypoint):
        entry = {"id": f"local-{len(state['waypoints'])}", **waypoint}
        state["waypoints"].append(entry)
        return entry

    async def _update_waypoint(waypoint_id, updates):
        for wp in state["waypoints"]:
            if wp.get("id") == waypoint_id:
                wp.update(updates)
                return dict(wp)
        return None

    async def _delete_waypoint(waypoint_id):
        for wp in state["waypoints"]:
            if wp.get("id") == waypoint_id:
                state["waypoints"].remove(wp)
                return True
        return False

    async def _delete_node(node_id):
        for node in list(state["nodes"]):
            if node.get("id") == node_id:
                state["nodes"].remove(node)
                return True
        return False

    async def _clear_stale_nodes():
        return 0

    async def _request_traceroute(destination):
        return True

    async def _request_position(destination):
        return True

    async def _get_packet_log(limit=200):
        return list(state["packet_log"])

    async def _get_sniffer_stats():
        return {"packets_per_minute": 0, "unique_nodes": 0, "total_captured": 0,
                "portnum_distribution": {}}

    async def _get_device_config():
        return dict(state["device_config"])

    async def _set_device_config(section, body):
        return {"applied": True, "section": section, "reboot_required": False}

    async def _reload_device_config():
        return (True, "")

    async def _get_security_scan():
        return {"findings": [], "has_issues": False, "scanned_at": 0}

    async def _get_favorites():
        return list(state["favorites"])

    async def _set_favorite(node_id, favorited):
        if favorited:
            if node_id not in state["favorites"]:
                state["favorites"].append(node_id)
        else:
            state["favorites"] = [f for f in state["favorites"] if f != node_id]
        return list(state["favorites"])

    # Async methods: AsyncMock(side_effect=<async fn>) both records calls (so
    # assert_called_once_with still works) and returns realistic shapes.
    conn.monitor_connection = AsyncMock()
    conn.run_channel_refresh_loop = AsyncMock()
    conn.expire_pending_acks = AsyncMock()
    conn.get_status = AsyncMock(side_effect=_get_status)
    conn.get_nodes = AsyncMock(side_effect=_get_nodes)
    conn.get_channels = AsyncMock(side_effect=_get_channels)
    conn.refresh_channels = AsyncMock(side_effect=_refresh_channels)
    conn.get_messages = AsyncMock(side_effect=_get_messages)
    conn.get_position_history = AsyncMock(side_effect=_get_position_history)
    conn.get_waypoints = AsyncMock(side_effect=_get_waypoints)
    conn.get_tags = AsyncMock(side_effect=_get_tags)
    conn.set_tags = AsyncMock(side_effect=_set_tags)
    conn.send_message = AsyncMock(side_effect=_send_message)
    conn.disconnect = AsyncMock()
    conn.clear_stale_nodes = AsyncMock(side_effect=_clear_stale_nodes)
    conn.delete_node = AsyncMock(side_effect=_delete_node)
    conn.add_waypoint = AsyncMock(side_effect=_add_waypoint)
    conn.update_waypoint = AsyncMock(side_effect=_update_waypoint)
    conn.delete_waypoint = AsyncMock(side_effect=_delete_waypoint)
    conn.request_traceroute = AsyncMock(side_effect=_request_traceroute)
    conn.request_position = AsyncMock(side_effect=_request_position)
    conn.get_packet_log = AsyncMock(side_effect=_get_packet_log)
    conn.get_sniffer_stats = AsyncMock(side_effect=_get_sniffer_stats)
    conn.get_device_config = AsyncMock(side_effect=_get_device_config)
    conn.set_device_config = AsyncMock(side_effect=_set_device_config)
    conn.reload_device_config = AsyncMock(side_effect=_reload_device_config)
    conn.get_security_scan = AsyncMock(side_effect=_get_security_scan)
    conn.get_favorites = AsyncMock(side_effect=_get_favorites)
    conn.set_favorite = AsyncMock(side_effect=_set_favorite)

    # Sync attributes/helpers that handlers reach into.
    conn._scheduled_messages_lock = MagicMock()
    conn._scheduled_messages_lock.__enter__ = MagicMock(return_value=None)
    conn._scheduled_messages_lock.__exit__ = MagicMock(return_value=None)
    conn._scheduled_messages = state["scheduled_messages"]
    conn._state = state

    return conn

@pytest.fixture
def mock_mqtt():
    mqtt = MagicMock()
    mqtt.start = AsyncMock()
    mqtt.stop = AsyncMock()
    return mqtt

@pytest.fixture
def mock_telegram():
    tg = MagicMock()
    tg.start = AsyncMock()
    tg.stop = AsyncMock()
    tg.forward_mesh_message = AsyncMock()
    return tg

@pytest.fixture
def app(mock_config, mock_connection, mock_mqtt, mock_telegram):
    app = build_app(mock_config)
    # Replace initialized connections with mocks
    app["connection"] = mock_connection
    app["mqtt_bridge"] = mock_mqtt
    app["telegram_bot"] = mock_telegram
    
    # Also override the startup tasks so it doesn't try to run real loops
    app.on_startup.clear()
    
    async def mock_startup(app):
        pass
    app.on_startup.append(mock_startup)
    
    with patch("app.routes._relay_to_integration", new_callable=AsyncMock) as mock_relay:
        mock_relay.return_value = {"nodes": []}
        yield app

@pytest_asyncio.fixture
async def aio_server(aiohttp_server, app):
    server = await aiohttp_server(app)
    return server

# ---------------------------------------------------------------------------
# Minimal stub JS for CDN libraries that the Web UI imports.
# map.js references `L.divIcon(...)` at MODULE SCOPE (before any function
# call), so Leaflet MUST be defined as a global before any of our ES modules
# execute.  We intercept the CDN fetch and return a lightweight fake.
# ---------------------------------------------------------------------------

_LEAFLET_STUB = r"""
(function() {
  function noop() { return {}; }
  function noopChain() {
    var o = {
      addTo: function() { return o; },
      bindPopup: function() { return o; },
      bindTooltip: function() { return o; },
      on: function() { return o; },
      openTooltip: function() { return o; },
      closeTooltip: function() { return o; },
      setLatLng: function() { return o; },
      setIcon: function() { return o; },
      setPopupContent: function() { return o; },
      setTooltipContent: function() { return o; },
      getLatLng: function() { return {lat: 0, lng: 0}; },
      getTooltip: function() { return null; },
      remove: function() { return o; },
      getContainer: function() { return document.createElement('div'); },
    };
    return o;
  }
  function fakeControl(opts) {
    return {
      onAdd: function() {},
      addTo: function() {},
    };
  }
  var L = {
    map: function() {
      var m = {
        setView: function() { return m; },
        addLayer: function() { return m; },
        removeLayer: function() { return m; },
        hasLayer: function() { return false; },
        getZoom: function() { return 10; },
        getSize: function() { return { x: 800, y: 600 }; },
        getContainer: function() { return document.createElement('div'); },
        invalidateSize: function() {},
        on: function() { return m; },
      };
      return m;
    },
    tileLayer: function() { return { addTo: function() {} }; },
    marker: function() { return noopChain(); },
    polyline: function() { return noopChain(); },
    divIcon: function() { return {}; },
    icon: function() { return {}; },
    heatLayer: function() { return noopChain(); },
    control: function() { return fakeControl(); },
    DomUtil: { create: function(tag, cls) { var el = document.createElement(tag); if(cls) el.className=cls; return el; } },
    DomEvent: {
      disableClickPropagation: function() {},
      on: function() {},
    },
    HeatLayer: { prototype: {} },
  };
  window.L = L;
  window.simpleheat = function() {};
  window.simpleheat.prototype = { draw: function() {} };
})();
"""

_CHARTJS_STUB = r"""
(function() {
  function Chart(el, cfg) {
    this.data = cfg && cfg.data ? cfg.data : { labels: [], datasets: [{ data: [] }] };
    this._cfg = cfg;
  }
  Chart.prototype.update = function() {};
  Chart.prototype.destroy = function() {};
  window.Chart = Chart;
})();
"""

_VIS_STUB = r"""
(function() {
  var vis = {
    Network: function(container, data, opts) {
      this.setData = function() {};
      this.fit = function() {};
      this.stabilize = function() {};
      this.destroy = function() {};
      this.on = function() {};
      this.getSelectedNodes = function() { return []; };
      this.setOptions = function() {};
    },
    DataSet: function(items) {
      var _items = items || [];
      this.add = function(i) { _items.push(i); };
      this.update = function() {};
      this.remove = function() {};
      this.clear = function() { _items = []; };
      this.get = function() { return _items; };
    },
  };
  window.vis = vis;
})();
"""


async def _abort_cdn(route: Route) -> None:
    """Abort CDN requests so headless tests don't need network access."""
    await route.abort()


@pytest_asyncio.fixture
async def async_page():
    """Provide a Playwright browser page for web-UI tests.

    Skips the test (rather than erroring) when the Playwright Chromium binary
    is not installed — e.g. a bare local ``pytest`` run without ``playwright
    install chromium``. CI installs the browser and runs these tests; a skip
    here is always explicit and visible in the report instead of a wall of
    ERRORs.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Playwright is not installed: {exc}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            page = await context.new_page()
            # Inject stubs before any scripts load to bypass SRI hash errors on intercept
            await page.add_init_script(_LEAFLET_STUB + "\n" + _CHARTJS_STUB + "\n" + _VIS_STUB)
            # Abort all CDN requests so they fail fast
            await page.route("**unpkg.com**", _abort_cdn)
            await page.route("**cdn.jsdelivr.net**", _abort_cdn)
            yield page
            await browser.close()
    except Exception as exc:  # pragma: no cover - env-dependent  # noqa: BLE001
        pytest.skip(f"Playwright browser unavailable: {exc}")

