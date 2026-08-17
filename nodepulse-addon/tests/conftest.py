import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock

# Add app to path so we can import it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from app.main import build_app
from app.config import Config

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
    conn = MagicMock()
    # Async methods
    conn.monitor_connection = AsyncMock()
    conn.get_status = AsyncMock(return_value={"connected": True, "uptime": 1000})
    conn.run_channel_refresh_loop = AsyncMock()
    conn.expire_pending_acks = AsyncMock()
    conn.get_nodes = AsyncMock(return_value=[
        {"id": "!12345678", "long_name": "Test Node 1", "snr": 5.0, "snr_avg": 5.0, "battery_level": 80},
        {"id": "!87654321", "long_name": "Test Node 2", "snr": -2.0, "snr_avg": -2.0, "battery_level": 50}
    ])
    conn.get_channels = AsyncMock(return_value=[
        {"index": 0, "name": "LongFast", "role": "PRIMARY"}
    ])
    conn.refresh_channels = AsyncMock(return_value=[
        {"index": 0, "name": "LongFast", "role": "PRIMARY"}
    ])
    conn.get_messages = AsyncMock(return_value=[
        {"id": "msg1", "text": "Hello world", "from_id": "!12345678", "channel": 0, "timestamp": 1000000}
    ])
    conn.get_position_history = AsyncMock(return_value=[])
    conn.get_waypoints = AsyncMock(return_value=[])
    conn.get_tags = AsyncMock(return_value={})
    conn.send_message = AsyncMock(return_value=True)
    conn.disconnect = AsyncMock()
    conn.clear_stale_nodes = AsyncMock(return_value=0)
    conn.delete_node = AsyncMock(return_value=True)
    conn.add_waypoint = AsyncMock(return_value={"id": "wp1", "name": "Test"})
    conn.update_waypoint = AsyncMock(return_value={"id": "wp1", "name": "Updated"})
    conn.delete_waypoint = AsyncMock(return_value=True)
    conn.set_tags = AsyncMock(return_value={"!12345678": ["test"]})
    conn.request_traceroute = AsyncMock(return_value=True)
    conn.request_position = AsyncMock(return_value=True)
    
    # Sync methods / attributes
    conn._scheduled_messages_lock = MagicMock()
    conn._scheduled_messages_lock.__enter__ = MagicMock()
    conn._scheduled_messages_lock.__exit__ = MagicMock()
    conn._scheduled_messages = []
    
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

from unittest.mock import patch

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

import pytest_asyncio

@pytest_asyncio.fixture
async def aio_server(aiohttp_server, app):
    server = await aiohttp_server(app)
    return server

from playwright.async_api import async_playwright, Route

import pytest_asyncio

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

