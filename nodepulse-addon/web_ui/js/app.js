/**
 * NodePulse Web UI — Main Application Controller
 *
 * This is the top-level orchestrator. It:
 *   1. Initialises all sub-modules (MapManager, ChartManager).
 *   2. Runs the main poll loop (status + nodes).
 *   3. Handles view/tab switching.
 *   4. Renders the node list and node grid.
 *   5. Handles the messaging compose form.
 *
 * We deliberately keep this file focused on wiring — rendering helpers and
 * data-formatting functions are kept short and named clearly so the flow
 * is easy to trace top-to-bottom.
 */

import { fetchStatus, fetchNodes, fetchChannels, fetchMessages, sendMessage, requestTraceRoute, requestPosition } from './api.js';
import { MapManager } from './map.js';
import { ChartManager } from './charts.js';

// How often (ms) to poll the backend for fresh data.
// Matches the scan_interval default from config.json (30s) but we use a
// faster default here so the UI feels live from the first load.
const POLL_INTERVAL_MS = 15_000;

// ============================================================================
// App State — all mutable state lives here, not scattered in closures.
// ============================================================================
const state = {
  nodes:          [],     // last successful node list from the API
  status:         null,   // last successful status from the API
  selectedNodeId: null,   // ID of node highlighted in the list + chart source
  currentView:    'dashboard',
  seenMessageIds: new Set(),  // dedupe inbound messages across polls
  selfId:         null,   // node ID of the locally-connected node
};

// ============================================================================
// Sub-module instances
// ============================================================================
const dashMap  = new MapManager('map');
const fullMap  = new MapManager('full-map');
const charts   = new ChartManager();

// ============================================================================
// Utility: Toast notifications
// ============================================================================
function showToast(message, type = 'info', durationMs = 3000) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), durationMs);
}

// ============================================================================
// Utility: Time formatting
// ============================================================================
function formatRelativeTime(epochSeconds) {
  if (!epochSeconds) return 'never';
  const diffS = Math.floor(Date.now() / 1000 - epochSeconds);
  if (diffS < 60)    return `${diffS}s ago`;
  if (diffS < 3600)  return `${Math.floor(diffS / 60)}m ago`;
  if (diffS < 86400) return `${Math.floor(diffS / 3600)}h ago`;
  return `${Math.floor(diffS / 86400)}d ago`;
}

// ============================================================================
// Utility: SNR → signal quality class
// ============================================================================
function snrToClass(snr) {
  if (snr == null)  return 'signal-poor';
  if (snr >= 10)    return 'signal-excellent';
  if (snr >= 5)     return 'signal-good';
  if (snr >= 0)     return 'signal-fair';
  return 'signal-poor';
}

function snrToValueClass(snr) {
  if (snr == null) return 'neutral';
  if (snr >= 10)   return 'good';
  if (snr >= 0)    return 'fair';
  return 'poor';
}

function rssiToValueClass(rssi) {
  if (rssi == null) return 'neutral';
  if (rssi >= -70)  return 'good';
  if (rssi >= -90)  return 'fair';
  return 'poor';
}

// Great-circle distance (km) between two lat/lon points (haversine).
function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDistance(km) {
  if (km == null || Number.isNaN(km)) return 'N/A';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(2)} km`;
}

// ============================================================================
// Rendering: Status Bar
// ============================================================================
function renderStatusBar(status) {
  const dot   = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const count = document.getElementById('badge-value');

  if (status?.connected) {
    dot.className = 'status-dot connected';
    label.textContent = 'Connected';
  } else {
    dot.className = 'status-dot disconnected';
    label.textContent = 'Disconnected';
  }

  if (count) count.textContent = state.nodes.length;
}

// ============================================================================
// Rendering: Node List (Dashboard sidebar)
// ============================================================================
function renderNodeList(nodes) {
  const ul = document.getElementById('node-list');
  ul.innerHTML = '';

  if (nodes.length === 0) {
    ul.innerHTML = `<li class="list-placeholder">No nodes detected</li>`;
    return;
  }

  // Sort: most-recently-heard first, unknown last.
  const sorted = [...nodes].sort((a, b) => (b.last_heard ?? 0) - (a.last_heard ?? 0));

  for (const node of sorted) {
    const li = document.createElement('li');
    li.className = `node-item ${snrToClass(node.snr)}`;
    if (node.id === state.selectedNodeId) li.classList.add('selected');
    li.dataset.nodeId = node.id;

     const battery = node.battery_level != null ? `🔋 ${node.battery_level}%` : '';
    const snrText  = node.snr  != null ? `${node.snr.toFixed(1)} dB` : '—';
    const rssiText = node.rssi != null ? `${node.rssi} dBm` : '';
    const hasGps   = node.latitude != null && node.longitude != null;
    const noGpsMark = hasGps ? '' : `<span class="node-list-unknown" title="No GPS fix">?</span>`;


    li.innerHTML = `
      <div class="signal-bars">
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
      </div>
          <div class="node-info">
        <div class="node-name">${noGpsMark} ${escapeHtml(node.long_name || node.id)}</div>
        <div class="node-meta">${escapeHtml(node.short_name || '')} · ${escapeHtml(node.hw_model || '')}</div>
      </div>
      <div class="node-stats">
        <div class="node-snr">${snrText}</div>
        <div class="node-battery">${battery}</div>
        <div class="node-heard">${formatRelativeTime(node.last_heard)}</div>
      </div>`;

    li.addEventListener('click', () => selectNode(node.id));
    ul.appendChild(li);
  }

  // Also refresh the destination select in the messaging pane.
  _updateDestinationSelect(sorted);
}

// ============================================================================
// Rendering: Node Grid (Nodes view)
// ============================================================================
function renderNodesGrid(nodes) {
  const grid = document.getElementById('nodes-grid');
  grid.innerHTML = '';

  if (nodes.length === 0) {
    grid.innerHTML = `<div class="list-placeholder">No nodes detected yet.</div>`;
    return;
  }

  // Resolve the self node's coordinates once so we can compute per-node
  // distance (MeshSense-style "distance from your node").
  const selfNode = state.nodes.find(n => n.id === state.selfId);
  const selfLat  = selfNode?.latitude;
  const selfLon  = selfNode?.longitude;
  const selfHasGps = selfLat != null && selfLon != null;

  for (const node of nodes) {
    const card = document.createElement('div');
    card.className = 'node-card';

    const snrText   = node.snr         != null ? `${node.snr.toFixed(1)} dB` : 'N/A';
    const rssiText  = node.rssi        != null ? `${node.rssi} dBm`          : 'N/A';
    const hopsText  = node.hops_away   != null ? String(node.hops_away)      : 'N/A';
    const batText   = node.battery_level != null ? `${node.battery_level}%`  : 'N/A';
    const heardText = formatRelativeTime(node.last_heard);
    const hasGps    = node.latitude != null && node.longitude != null;
    const noGpsMark = hasGps ? '' : `<span class="node-card-unknown" title="No GPS fix">?</span>`;

    let distText = 'N/A';
    if (hasGps && selfHasGps && node.id !== state.selfId) {
      distText = formatDistance(haversineKm(selfLat, selfLon, node.latitude, node.longitude));
    }

    const tempText = node.temperature       != null ? `${node.temperature.toFixed(1)} °C` : 'N/A';
    const humText  = node.relative_humidity != null ? `${node.relative_humidity.toFixed(0)} %` : 'N/A';
    const presText = node.barometric_pressure != null ? `${node.barometric_pressure.toFixed(0)} hPa` : 'N/A';

    card.innerHTML = `
      <div class="node-card-header">
        <div>
          <div class="node-card-name">${noGpsMark} ${escapeHtml(node.long_name || node.id)}</div>
          <div class="node-card-id">${escapeHtml(node.id)}</div>
        </div>
        <span class="node-card-hw">${escapeHtml(node.hw_model || 'Unknown')}</span>
      </div>
      <div class="node-metrics">
        <div class="metric-item">
          <div class="metric-label">SNR</div>
          <div class="metric-value ${snrToValueClass(node.snr)}">${snrText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">RSSI</div>
          <div class="metric-value ${rssiToValueClass(node.rssi)}">${rssiText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Hops Away</div>
          <div class="metric-value neutral">${hopsText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Battery</div>
          <div class="metric-value neutral">${batText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Distance</div>
          <div class="metric-value neutral" style="font-size:12px">${distText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">GPS</div>
          <div class="metric-value ${hasGps ? 'good' : 'neutral'}" style="font-size:12px">${hasGps ? '✓ Fix' : 'No fix'}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Temp</div>
          <div class="metric-value neutral" style="font-size:12px">${tempText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Humidity</div>
          <div class="metric-value neutral" style="font-size:12px">${humText}</div>
        </div>
        <div class="metric-item">
          <div class="metric-label">Pressure</div>
          <div class="metric-value neutral" style="font-size:12px">${presText}</div>
        </div>
      </div>
      <div class="node-card-actions">
        <button class="action-btn" data-action="traceroute" data-node="${escapeHtml(node.id)}">Traceroute</button>
        <button class="action-btn" data-action="position"   data-node="${escapeHtml(node.id)}">Req. Position</button>
        <button class="action-btn" data-action="message"    data-node="${escapeHtml(node.id)}">Message</button>
      </div>`;

    grid.appendChild(card);
  }

  // NOTE: the grid click handler is attached ONCE in init() (event delegation),
  // not here — renderNodesGrid() runs every poll cycle and re-adding the
  // listener each time would leak handlers.
}

// ============================================================================
// Node Card Action Handler
// ============================================================================
async function handleNodeCardAction(event) {
  const btn = event.target.closest('[data-action]');
  if (!btn) return;

  const { action, node: nodeId } = btn.dataset;

  if (action === 'traceroute') {
    try {
      await requestTraceRoute(nodeId);
      showToast(`Traceroute dispatched to ${nodeId}`, 'success');
    } catch (err) {
      showToast(`Traceroute failed: ${err.message}`, 'error');
    }
  } else if (action === 'position') {
    try {
      await requestPosition(nodeId);
      showToast(`Position request sent to ${nodeId}`, 'success');
    } catch (err) {
      showToast(`Position request failed: ${err.message}`, 'error');
    }
  } else if (action === 'message') {
    // Switch to dashboard and pre-select this node as DM target.
    switchView('dashboard');
    document.getElementById('destination-select').value = nodeId;
    document.getElementById('message-input').focus();
  }
}

// ============================================================================
// Node Selection
// ============================================================================
function selectNode(nodeId) {
  state.selectedNodeId = nodeId;
  // Re-render the list to update the "selected" highlight.
  renderNodeList(state.nodes);
}

// ============================================================================
// Messaging
// ============================================================================
function _updateDestinationSelect(nodes) {
  const select = document.getElementById('destination-select');
  const current = select.value;
  select.innerHTML = `<option value="">Broadcast</option>`;
  for (const node of nodes) {
    const opt = document.createElement('option');
    opt.value = node.id;
    opt.textContent = node.short_name || node.long_name || node.id;
    select.appendChild(opt);
  }
  // Restore previously selected destination if it still exists.
  if (current && select.querySelector(`option[value="${current}"]`)) {
    select.value = current;
  }
}

function appendMessage(text, sender, type) {
  const list = document.getElementById('message-list');
  const bubble = document.createElement('div');
  bubble.className = `message-bubble ${type}`;
  const time = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  bubble.innerHTML = `
    <div class="message-sender">${escapeHtml(sender)}</div>
    <div>${escapeHtml(text)}</div>
    <div class="message-time">${time}</div>`;
  list.appendChild(bubble);
  list.scrollTop = list.scrollHeight;
}

async function handleSend() {
  const input       = document.getElementById('message-input');
  const destSelect  = document.getElementById('destination-select');
  const text        = input.value.trim();
  const destination = destSelect.value || null;

  if (!text) return;

  input.value = '';

  // Optimistically show the outgoing message immediately.
  appendMessage(text, 'Me', 'outgoing');

  try {
    await sendMessage(text, destination);
  } catch (err) {
    showToast(`Send failed: ${err.message}`, 'error');
  }
}

// ============================================================================
// View Switching
// ============================================================================
function switchView(viewName) {
  state.currentView = viewName;

  // Toggle view panels
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
  const target = document.getElementById(`view-${viewName}`);
  if (target) target.classList.add('active');

  // Toggle nav items
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`.nav-item[data-view="${viewName}"]`);
  if (navItem) navItem.classList.add('active');

  // Toggle tab buttons
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const tabBtn = document.querySelector(`.tab-btn[data-view="${viewName}"]`);
  if (tabBtn) tabBtn.classList.add('active');

  // Leaflet maps need invalidateSize() after becoming visible.
  if (viewName === 'dashboard') {
    dashMap.invalidateSize();
  } else if (viewName === 'map') {
    fullMap.init();
    fullMap.updateNodes(state.nodes);
    fullMap.invalidateSize();
  }
}

// ============================================================================
// Settings View — populate with read-only config info
// ============================================================================
async function renderSettings() {
  // Fetch status to display current connection details.
  try {
    const status = await fetchStatus();
    const host   = document.getElementById('settings-host');
    const conn   = document.getElementById('settings-conn');
    const count  = document.getElementById('settings-count');
    if (host)  host.textContent  = 'Configured in /data/options.json';
    if (conn)  conn.textContent  = status.connected ? 'Connected' : 'Disconnected';
    if (count) count.textContent = status.node_count ?? '—';
  } catch (_) { /* settings are informational — fail silently */ }
}

// ============================================================================
// Main Poll Loop
// ============================================================================
async function pollData() {
  // Fetch status and nodes in parallel — independent requests.
  const [statusResult, nodesResult, messagesResult] = await Promise.allSettled([
    fetchStatus(),
    fetchNodes(),
    fetchMessages(),
  ]);

  if (statusResult.status === 'fulfilled') {
    state.status = statusResult.value;
    renderStatusBar(state.status);
  } else {
    console.warn('Status fetch failed:', statusResult.reason);
  }

  if (nodesResult.status === 'fulfilled') {
    state.nodes = nodesResult.value;

    // Determine the self/local node ID from the status so the map can draw
    // distance-labelled links from it, and highlight it as the hub.
    const selfNum = state.status?.my_info?.my_node_num;
    const selfId = selfNum != null
      ? '!' + (selfNum >>> 0).toString(16).padStart(8, '0')
      : null;
    dashMap.setSelfNode(selfId);
    fullMap.setSelfNode(selfId);
    state.selfId = selfId;

    renderNodeList(state.nodes);
    renderNodesGrid(state.nodes);
    dashMap.updateNodes(state.nodes);

    // Push a chart point using the selected node's metrics, or the first node
    // in the list if no node is explicitly selected.
    const chartNode = state.nodes.find(n => n.id === state.selectedNodeId) ?? state.nodes[0];
    charts.addPoint(chartNode?.snr ?? null, chartNode?.rssi ?? null, state.nodes.length);
  } else {
    console.warn('Nodes fetch failed:', nodesResult.reason);
  }

  if (messagesResult.status === 'fulfilled') {
    renderIncomingMessages(messagesResult.value);
  } else {
    console.warn('Messages fetch failed:', messagesResult.reason);
  }
}

/**
 * Render any newly-arrived inbound text messages into the message feed.
 * We track seen message IDs so a message is only appended once even though
 * the API returns the whole recent buffer on every poll.
 */
function renderIncomingMessages(messages) {
  if (!Array.isArray(messages)) return;
  for (const msg of messages) {
    if (!msg.id || state.seenMessageIds.has(msg.id)) continue;
    state.seenMessageIds.add(msg.id);
    appendMessage(msg.text, msg.from_name || msg.from_id || 'Unknown', 'incoming');
  }
}

// ============================================================================
// Bootstrap
// ============================================================================
async function init() {
  // Initialise maps — they need the DOM to be ready.
  dashMap.init();
  charts.init();

  // Wire up navigation clicks — both sidebar nav items and top tab buttons.
  document.querySelectorAll('.nav-item[data-view], .tab-btn[data-view]').forEach(el => {
    el.addEventListener('click', () => switchView(el.dataset.view));
  });

  // Wire up the send button and Enter key shortcut in the message input.
  document.getElementById('send-btn').addEventListener('click', handleSend);
  document.getElementById('message-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  // Event delegation on the nodes grid — attached once here so it is NOT
  // re-added on every 15s poll inside renderNodesGrid().
  document.getElementById('nodes-grid').addEventListener('click', handleNodeCardAction);

  // Set the initial active view.
  switchView('dashboard');

  // Initial data load — show a spinner state while waiting.
  document.getElementById('node-list').innerHTML = `
    <li class="list-placeholder"><div class="spinner"></div>Loading nodes…</li>`;

  await pollData();                          // first immediate fetch
  dashMap.fitToMarkers();                    // zoom map to show the whole network

  // Schedule the repeating poll loop.
  setInterval(pollData, POLL_INTERVAL_MS);
}

// ============================================================================
// Utility: HTML escape (shared with map.js concept — repeated here for module isolation)
// ============================================================================
function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Start the app when the DOM is ready.
document.addEventListener('DOMContentLoaded', init);
