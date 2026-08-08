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

import { fetchStatus, fetchNodes, fetchChannels, fetchMessages, sendMessage, requestTraceRoute, requestPosition, fetchTrackedNodes, trackNode, clearStaleNodes, fetchTags, setTags, fetchPositionHistory, fetchPackets, fetchSnifferStats, fetchWaypoints, addWaypoint, updateWaypoint, deleteWaypoint, deleteNode } from './api.js';
import { MapManager } from './map.js';
import { ChartManager } from './charts.js';
import { TopologyManager } from './topology.js';
import { escapeHtml, haversineKm, formatDistance, buildKml, buildGpx, downloadFile } from './util.js';

// How often (ms) to poll the backend for fresh node/status/message data.
// Matches the scan_interval default from config.json (30s) but we use a
// faster default here so the UI feels live from the first load.
const POLL_INTERVAL_MS = 15_000;

// How many fast poll cycles to skip between tracked-nodes refreshes.
// fetchTrackedNodes() relays to HA (potentially slow); we only need it to
// stay accurate, not be real-time — once every 5 minutes (20 × 15s) is
// plenty. A value of 0 means "refresh on every poll" (previous behaviour).
const TRACKED_NODES_POLL_EVERY_N = 20;

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
  trackedNodes:   new Set(), // node IDs currently tracked as HA entities
  nodeFilter:     '',       // free-text filter for the Nodes tab
  signalFilter:   '',       // signal-strength filter for the Nodes tab
  activeConversation: 'ch:0', // currently-open thread (ch:<n> or dm:<nodeId>)
  messageFilter:    '',       // free-text filter for message history
  conversations:  {},       // key -> { key, name, kind, unread }
  messagesByConv: {},       // key -> [message objects], persisted across polls
  channels:       [],       // configured mesh channels from the node
  nodeTags:        {},       // node_id -> [tag strings], loaded from /api/tags
  notifyNodes:     new Set(), // node IDs that should trigger HA notifications
  dismissedConvs:  new Set(), // conversation keys hidden from sidebar, persisted in localStorage
  _initialBatchComplete: false, // becomes true after first message poll

  // Counter incremented on each fast poll cycle. Used to throttle the slow
  // Packet inspector / sniffer state
  packetLog:          [],
  snifferStats:       null,
  packetFilters:      {},    // { col: value } — active column filters
  packetSort:         null,  // { col, dir } where dir is 'asc' or 'desc'

  // Notification state (Feature 2)
  _lastSeenMsgId:     localStorage.getItem('np_last_msg_id') || null,
  _notifPermission:   localStorage.getItem('np_notifications') || 'default',

  // tracked-nodes refresh so it does not run on every 15s tick.
  _pollCount:     0,

  // Position history data for trail polylines, heatmap, and ruler elevation sampling.
  posHistory:     {},
};

// ============================================================================
// Sub-module instances
// ============================================================================
const dashMap  = new MapManager('map');
const fullMap  = new MapManager('full-map');
const topology = new TopologyManager('topology-container');
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
  if (durationMs > 0) setTimeout(() => toast.remove(), durationMs);
  return toast;
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

// Great-circle distance helpers (haversineKm, formatDistance) are imported
// from ./util.js to avoid duplicating them in map.js and here.

// Distance (km) from the self/local node to a given node, or null if either
// side lacks a GPS fix. Used to sort the node list and grid by proximity.
function nodeDistanceKm(node) {
  const self = state.nodes.find(n => n.id === state.selfId);
  if (!self || self.latitude == null || self.longitude == null) return null;
  if (node.latitude == null || node.longitude == null) return null;
  if (node.id === state.selfId) return 0;
  return haversineKm(self.latitude, self.longitude, node.latitude, node.longitude);
}

// Sort nodes by distance from the self node (nearest first); nodes without a
// GPS fix or when the self node has no fix sort last.
function sortByDistance(nodes) {
  return [...nodes].sort((a, b) => {
    const da = nodeDistanceKm(a);
    const db = nodeDistanceKm(b);
    if (da == null && db == null) return (b.last_heard ?? 0) - (a.last_heard ?? 0);
    if (da == null) return 1;
    if (db == null) return -1;
    return da - db;
  });
}

function sortByLastHeard(nodes) {
  return [...nodes].sort((a, b) => (b.last_heard ?? 0) - (a.last_heard ?? 0));
}

function sortBySignal(nodes) {
  return [...nodes].sort((a, b) => {
    const sa = a.snr != null ? a.snr : -Infinity;
    const sb = b.snr != null ? b.snr : -Infinity;
    if (sb > sa) return 1;
    if (sb < sa) return -1;
    const ra = a.rssi != null ? a.rssi : -Infinity;
    const rb = b.rssi != null ? b.rssi : -Infinity;
    if (rb > ra) return 1;
    if (rb < ra) return -1;
    return 0;
  });
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

  // Fast check: compute a fingerprint of the list. We include the selectedNodeId
  // so changing selection immediately triggers a re-render to update the highlight.
  const fingerprint = nodes.map(n => `${n.id}:${n.last_heard}:${n.snr}`).join('|') + '|' + state.selectedNodeId;
  if (ul.dataset.fingerprint === fingerprint && ul.innerHTML !== '') return;
  ul.dataset.fingerprint = fingerprint;

  ul.innerHTML = '';

  if (nodes.length === 0) {
    ul.innerHTML = `<li class="list-placeholder">No nodes detected</li>`;
    return;
  }

  // Sort by signal strength (strongest first).
  const sorted = sortBySignal(nodes);

  for (const node of sorted) {
    const li = document.createElement('li');
    li.className = `node-item ${snrToClass(node.snr)}` + (node.stale ? ' node-item-stale' : '');
    if (node.id === state.selectedNodeId) li.classList.add('selected');
    li.dataset.nodeId = node.id;

     const battery = node.battery_level != null ? `🔋 ${node.battery_level}%` : '';
    const snrText  = node.snr  != null ? `${node.snr.toFixed(1)} dB` : '—';
    const rssiText = node.rssi != null ? `${node.rssi} dBm` : '';
    const hasGps   = node.latitude != null && node.longitude != null;
    const noGpsMark = hasGps ? '' : `<span class="node-list-unknown" title="No GPS fix">?</span>`;
    const staleMark = node.stale ? ` <span class="node-list-cached" title="Not currently heard by the radio — restored from stored history">cached</span>` : '';


    li.innerHTML = `
      <div class="signal-bars">
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
        <div class="signal-bar"></div>
      </div>
          <div class="node-info">
        <div class="node-name">${noGpsMark} ${escapeHtml(node.long_name || node.id)}${staleMark}</div>
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
}

// ============================================================================
// Rendering: Node Grid (Nodes view)
// ============================================================================
function renderNodesGrid(nodes) {
  const grid = document.getElementById('nodes-grid');

  // Fast check: compute a fingerprint of the current state that affects the grid.
  const fingerprint = [state.nodeFilter, state.signalFilter, state.trackedNodes.size, state.notifyNodes.size, JSON.stringify(state.nodeTags), ...nodes.map(n => `${n.id}:${n.last_heard}:${n.snr}:${n.snr_avg}:${n.latitude}:${n.longitude}`)].join('|');
  if (grid.dataset.fingerprint === fingerprint && grid.innerHTML !== '') return;
  grid.dataset.fingerprint = fingerprint;

  grid.innerHTML = '';

  if (nodes.length === 0) {
    grid.innerHTML = `<div class="list-placeholder">No nodes detected yet.</div>`;
    return;
  }

  // Apply the free-text filter from the Nodes tab search box. Match against
  // long name, short name, hardware model, or node ID (all case-insensitive).
  const q = state.nodeFilter.trim().toLowerCase();
  let filtered = q
    ? nodes.filter(n =>
        (n.long_name || '').toLowerCase().includes(q) ||
        (n.short_name || '').toLowerCase().includes(q) ||
        (n.hw_model || '').toLowerCase().includes(q) ||
        (n.id || '').toLowerCase().includes(q))
    : nodes;

  // Apply the signal-strength filter.
  const sf = state.signalFilter;
  if (sf) {
    const thresholds = {
      excellent: [10, Infinity],
      good:      [5, 10],
      fair:      [0, 5],
      poor:      [-Infinity, 0],
      none:      [null, null],
    };
    const [lo, hi] = thresholds[sf] || [];
    filtered = filtered.filter(n => {
      if (lo === null) return n.snr_avg == null;
      if (n.snr_avg == null) return false;
      return n.snr_avg >= lo && (hi === Infinity || n.snr_avg < hi);
    });
  }

  if (filtered.length === 0) {
    const reason = sf ? ` matching "${sf}" signal` : ` matching "${escapeHtml(state.nodeFilter)}"`;
    grid.innerHTML = `<div class="list-placeholder">No nodes${reason}.</div>`;
    return;
  }

  // Sort the filtered nodes by signal strength (strongest first).
  const sorted = sortBySignal(filtered);

  // Resolve the self node's coordinates once so we can compute per-node
  // distance (MeshSense-style "distance from your node").
  const selfNode = state.nodes.find(n => n.id === state.selfId);
  const selfLat  = selfNode?.latitude;
  const selfLon  = selfNode?.longitude;
  const selfHasGps = selfLat != null && selfLon != null;

  for (const node of sorted) {
    const card = document.createElement('div');
    card.className = 'node-card' + (node.stale ? ' node-card-stale' : '') + (node.role ? ' role-' + node.role.toLowerCase() : '');

    const snrText   = node.snr         != null ? `${node.snr.toFixed(1)} dB` : 'N/A';
    const rssiText  = node.rssi        != null ? `${node.rssi} dBm`          : 'Not provided';
    const hopsText  = node.hops_away   != null ? String(node.hops_away)      : 'N/A';
    const batText   = node.battery_level != null ? `${node.battery_level}%`  : 'N/A';
    const heardText = formatRelativeTime(node.last_heard);
    const hasGps    = node.latitude != null && node.longitude != null;
    const noGpsMark = hasGps ? '' : `<span class="node-card-unknown" title="No GPS fix">?</span>`;
    const staleMark = node.stale ? `<span class="node-card-cached" title="Not currently heard by the radio — restored from stored history (radio node DB is full)">cached</span>` : '';

    let distText = 'N/A';
    if (hasGps && selfHasGps && node.id !== state.selfId) {
      distText = formatDistance(haversineKm(selfLat, selfLon, node.latitude, node.longitude));
    }

    const tempText = node.temperature       != null ? `${node.temperature.toFixed(1)} °C` : 'N/A';
    const humText  = node.relative_humidity != null ? `${node.relative_humidity.toFixed(0)} %` : 'N/A';
    const presText = node.barometric_pressure != null ? `${node.barometric_pressure.toFixed(0)} hPa` : 'N/A';

    // Traceroute route (if one has been captured for this node).
    let tracerouteHtml = '';
    const tr = node.traceroute;
    if (tr) {
      const formatHop = (n) => {
        const id = '!' + (n >>> 0).toString(16).padStart(8, '0');
        const match = state.nodes.find(nn => nn.id === id);
        return escapeHtml(match ? (match.short_name || match.long_name || id) : id);
      };
      const forward = (tr.route || []).map(formatHop);
      if (tr.from_id) forward.push(escapeHtml(state.nodes.find(n => n.id === tr.from_id)?.short_name || tr.from_id));
      const pathStr = forward.length
        ? `<strong>${escapeHtml(state.selfId || 'Self')}</strong> → ${forward.join(' → ')}`
        : 'No route discovered';
      const ago = formatRelativeTime(tr.timestamp);
      tracerouteHtml = `
        <div class="node-card-traceroute">
          <div class="metric-label">Traceroute</div>
          <div class="traceroute-path">${pathStr}</div>
          <div class="traceroute-time">${ago}</div>
        </div>`;
    }

    const qualityLabel = { excellent: 'EXCELLENT', good: 'GOOD', fair: 'FAIR', poor: 'POOR', no_signal: 'NO SIGNAL' };
    const qualityColor = { excellent: '#00e5ff', good: '#69f0ae', fair: '#ffeb3b', poor: '#ff5252', no_signal: '#9e9e9e' };
    const quality = node.signal_quality || 'no_signal';
    const qColor  = qualityColor[quality] || '#9e9e9e';
    const qBadge  = `<span class="quality-badge" style="background:${qColor}18;color:${qColor};border:1px solid ${qColor}50">${qualityLabel[quality] || 'NO SIGNAL'}</span>`;
    const snrAvgText = node.snr_avg != null ? `${node.snr_avg} dB avg` : '';

    const nodeTags = state.nodeTags[node.id] || [];
    const tagsHtml = nodeTags.length
      ? `<div class="node-card-tags">${nodeTags.map(t => `<span class="node-tag">${escapeHtml(t)}</span>`).join('')}</div>`
      : '';

    card.innerHTML = `
      <div class="node-card-header">
        <div>
          <div class="node-card-name">${noGpsMark} ${escapeHtml(node.long_name || node.id)} ${staleMark}</div>
          <div class="node-card-id">${escapeHtml(node.id)}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          <span class="node-card-hw">${escapeHtml(node.hw_model || 'Unknown')}</span>
          ${qBadge}${snrAvgText ? `<span style="font-size:0.68rem;color:var(--text-muted)">${snrAvgText}</span>` : ''}
        </div>
      </div>
      ${tagsHtml}
      <div class="node-card-tag-edit">
        <input type="text" class="tag-input" data-node="${escapeHtml(node.id)}" placeholder="Add tag…" value="${escapeHtml(nodeTags.join(', '))}" title="Comma-separated tags" />
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
      ${tracerouteHtml}
      ${node.neighbors && node.neighbors.length > 0 ? `
      <div class="node-card-neighbors">
        <div class="metric-label">Neighbors (${node.neighbors.length})</div>
        <div class="neighbor-list">${node.neighbors.map(nb => {
          const n = state.nodes.find(x => x.id === nb.id);
          const name = n ? (n.short_name || n.long_name || nb.id) : nb.id;
          return `<span class="neighbor-item" title="${escapeHtml(nb.id)} · SNR ${nb.snr ?? 'N/A'} dB">${escapeHtml(name)} ${nb.snr != null ? `<span class="snr-chip ${snrToValueClass(nb.snr)}">${nb.snr.toFixed(1)}</span>` : ''}</span>`;
        }).join('')}</div>
      </div>` : ''}
      <div class="node-card-actions">
        <button class="action-btn" data-action="traceroute" data-node="${escapeHtml(node.id)}">Traceroute</button>
        <button class="action-btn" data-action="position"   data-node="${escapeHtml(node.id)}">Req. Position</button>
        <button class="action-btn" data-action="message"    data-node="${escapeHtml(node.id)}">Message</button>
        <label class="node-track-toggle" title="Create Home Assistant entities for this node">
          <input type="checkbox" data-action="track" data-node="${escapeHtml(node.id)}" ${state.trackedNodes.has(node.id) ? 'checked' : ''} />
          <span>Track in HA</span>
        </label>
        <label class="node-track-toggle" title="Receive browser notifications for messages from this node">
          <input type="checkbox" data-action="notify" data-node="${escapeHtml(node.id)}" ${state.notifyNodes.has(node.id) ? 'checked' : ''} />
          <span>Notify</span>
        </label>
        <button class="action-btn action-btn-danger" data-action="delete" data-node="${escapeHtml(node.id)}" title="Remove this node from the store">Delete</button>
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
    // Open (or focus) this node's Direct-Message thread on the dashboard.
    openDirectMessage(nodeId);
  } else if (action === 'track') {
    const checkbox = btn;
    const enabled = checkbox.checked;
    // Optimistically reflect the intended state; revert on failure.
    if (enabled) state.trackedNodes.add(nodeId);
    else state.trackedNodes.delete(nodeId);
    try {
      await trackNode(nodeId, enabled);
      showToast(
        `${enabled ? 'Tracking' : 'Stopped tracking'} ${nodeId} in Home Assistant`,
        'success',
      );
    } catch (err) {
      // Roll back on error so the checkbox matches reality.
      if (enabled) state.trackedNodes.delete(nodeId);
      else state.trackedNodes.add(nodeId);
      showToast(`Track request failed: ${err.message}`, 'error');
      btn.checked = !enabled;
    }
  } else if (action === 'notify') {
    const checkbox = btn;
    const enabled = checkbox.checked;
    if (enabled) state.notifyNodes.add(nodeId);
    else state.notifyNodes.delete(nodeId);
    localStorage.setItem('nodepulse_notify_nodes', JSON.stringify([...state.notifyNodes]));
    showToast(
      `${enabled ? 'Notifications enabled' : 'Notifications disabled'} for ${nodeId}`,
      'success',
    );
  } else if (action === 'delete') {
    if (!confirm(`Remove node ${nodeId} from the store?`)) return;
    try {
      await deleteNode(nodeId);
      state.nodes = state.nodes.filter(n => n.id !== nodeId);
      renderNodesGrid(state.nodes);
      renderNodeList(state.nodes);
      showToast(`Removed node ${nodeId}`, 'success');
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, 'error');
    }
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
// Messaging — conversation threads (mirrors the Meshtastic Android app:
// one thread per channel + one Direct-Message thread per node).
// ============================================================================

// Resolve a friendly display name for a node ID from the current node list.
function nodeName(nodeId) {
  if (!nodeId) return nodeId;
  const n = state.nodes.find(x => x.id === nodeId);
  return n ? (n.long_name || n.short_name || nodeId) : nodeId;
}

// Resolve a node's short name from the live node list — used for compact
// sender labels in the message window. Falls back to null so callers can
// chain to other name sources (e.g. the message's stored from_name).
function shortNameFor(nodeId) {
  if (!nodeId) return null;
  // Normalise the id (strip a leading "!" and lowercase) so it matches the
  // node list's id format even if the message packet formatted it differently.
  const norm = String(nodeId).trim().toLowerCase().replace(/^!/, '');
  const n = state.nodes.find(x => {
    const id = String(x.id || '').trim().toLowerCase().replace(/^!/, '');
    return id === norm;
  });
  return n && n.short_name ? n.short_name : null;
}

function formatMessageTime(timestamp) {
  const d = new Date((timestamp || Date.now() / 1000) * 1000);
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })} ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
}

// Build the canonical conversation key + display name for a destination the
// user is about to message. `destination` is a node ID (DM) or ""/null (the
// active channel's broadcast).
function conversationForKey(key) {
  if (key.startsWith('dm:')) {
    const nodeId = key.slice(3);
    return { key, kind: 'dm', name: nodeName(nodeId), nodeId };
  }
  const ch = parseInt(key.slice(3), 10) || 0;
  // Use the channel's real name when known (from the node config), falling
  // back to the generic Primary / Channel N labels.
  const cfg = (state.channels || []).find(c => c && c.index === ch);
  const name = cfg && cfg.name ? cfg.name : (ch === 0 ? 'Primary' : `Channel ${ch}`);
  return { key, kind: 'channel', name, channel: ch };
}

function _ensureConversation(key) {
  if (!state.conversations[key]) {
    state.conversations[key] = { ...conversationForKey(key), unread: 0 };
  }
  return state.conversations[key];
}

// Render the conversation tab bar (channels + DM threads) with unread badges.
function renderConversationTabs() {
  const bar = document.getElementById('conversation-tabs');
  if (!bar) return;

  // Always include the Primary channel; add any channel/DM seen in messages,
  // plus every configured channel from the node so the tabs appear immediately
  // (not only after a message arrives on that channel).
  const keys = new Set(['ch:0']);
  for (const k of Object.keys(state.conversations)) keys.add(k);
  for (const k of Object.keys(state.messagesByConv)) {
    if (state.messagesByConv[k].length) keys.add(k);
  }
  for (const ch of (state.channels || [])) {
    if (ch && ch.index != null) keys.add(`ch:${ch.index}`);
  }

  const ordered = [...keys].sort((a, b) => {
    // Channels first (by number), then DMs.
    const ca = a.startsWith('ch:') ? 0 : 1;
    const cb = b.startsWith('ch:') ? 0 : 1;
    if (ca !== cb) return ca - cb;
    return a.localeCompare(b);
  });

  bar.innerHTML = '';
  for (const key of ordered) {
    const conv = _ensureConversation(key);
    const tab = document.createElement('button');
    tab.className = `conversation-tab ${key === state.activeConversation ? 'active' : ''}`;
    tab.dataset.conv = key;
    tab.title = conv.name;
    const badge = conv.unread > 0
      ? `<span class="conv-badge">${conv.unread > 99 ? '99+' : conv.unread}</span>` : '';
    tab.innerHTML = `<span class="conv-name">${escapeHtml(conv.name)}</span>${badge}`;
    tab.addEventListener('click', () => selectConversation(key));
    bar.appendChild(tab);
  }
}

function selectConversation(key) {
  state.activeConversation = key;
  const conv = _ensureConversation(key);
  conv.unread = 0;

  // Reflect the recipient in the compose box + set the hidden destination.
  const label = document.getElementById('recipient-label');
  if (label) label.textContent = conv.name;

  // Sync the channel selector to the active conversation, and show/hide it
  // for DMs (which always send on channel 0).
  syncChannelSelect();

  renderConversationTabs();
  renderMessagesThread();
  updateMessagesBadge();
}

// Populate and sync the channel <select> in the compose box. Only meaningful
// for channel (broadcast) conversations — hidden for DMs.
function renderChannelSelect() {
  const sel = document.getElementById('channel-select');
  if (!sel) return;
  const prev = sel.value;

  const chans = (state.channels || []).filter(c => c && c.index != null);
  // Always ensure channel 0 (Primary) is present.
  const hasPrimary = chans.some(c => c.index === 0);
  const list = hasPrimary ? chans : [{ index: 0, name: 'Primary' }, ...chans];

  sel.innerHTML = '';
  for (const c of list) {
    const opt = document.createElement('option');
    opt.value = String(c.index);
    opt.textContent = c.name ? `${c.name} (ch ${c.index})` : `Channel ${c.index}`;
    sel.appendChild(opt);
  }

  // Restore previous selection if still present, else default to Primary.
  if (prev && list.some(c => String(c.index) === prev)) sel.value = prev;
  else sel.value = '0';

  syncChannelSelect();
}

// Show/hide + value-sync the channel selector based on the active conversation.
function syncChannelSelect() {
  const sel = document.getElementById('channel-select');
  if (!sel) return;
  const conv = conversationForKey(state.activeConversation);
  if (conv.kind === 'dm') {
    sel.style.display = 'none';
  } else {
    sel.style.display = '';
    sel.value = String(conv.channel ?? 0);
  }
}

// Append a message object to its conversation thread + (optionally) to the UI.
function storeMessage(msg, { skipUnread } = {}) {
  const key = msg.conversation || (msg.is_dm ? `dm:${msg.from_id}` : `ch:${msg.channel ?? 0}`);
  if (!state.messagesByConv[key]) state.messagesByConv[key] = [];
  const thread = state.messagesByConv[key];
  // Dedupe by id to avoid double-adding on poll repeats.
  if (thread.some(m => m.id === msg.id)) return;
  // Meshtastic broadcasts our own sent packets back to us, so a DM we just
  // sent also arrives as an "outgoing" server echo. Drop it if we already
  // have an optimistic bubble with the same text sent within the last 3 seconds
  // — this suppresses the firmware echo without silently dropping legitimately
  // repeated messages (e.g. a user sending "OK" twice).
  const THREE_SECONDS = 3;
  const now = Date.now() / 1000;
  if (msg.outgoing && thread.some(m =>
    m.outgoing &&
    m.text === msg.text &&
    m.destination === msg.destination &&
    m.channel === msg.channel &&
    Math.abs((m.timestamp || 0) - (msg.timestamp || now)) < THREE_SECONDS
  )) return;
  thread.push(msg);

  if (skipUnread) return;
  const conv = _ensureConversation(key);
  // Mark unread only if it arrived in a non-active conversation and isn't ours.
  if (key !== state.activeConversation && !msg.outgoing) {
    conv.unread = (conv.unread || 0) + 1;
    // New message on a dismissed conversation — bring it back
    state.dismissedConvs.delete(key);
  }
}


// Retry a previously-failed outgoing message.
async function retryMessage(msg) {
  msg.status = 'sending';
  if (state.activeConversation === msg.conversation) renderMessagesThread();
  try {
    await sendMessage(msg.text, msg.destination ?? null, msg.channel ?? 0);
    msg.status = 'sent';
  } catch (err) {
    msg.status = 'failed';
    showToast(`Send failed: ${err.message}`, 'error');
  }
  if (state.activeConversation === msg.conversation) renderMessagesThread();
}

// Switch the active conversation to a node's DM thread (used when the user
// clicks "Message" on a node card or a node in the list).
function openDirectMessage(nodeId) {
  const key = `dm:${nodeId}`;
  if (state.currentView !== 'messages') switchView('messages');
  selectMessagesConversation(key);
  const msgInput = document.getElementById('messages-message-input');
  if (msgInput) setTimeout(() => msgInput.focus(), 100);
}

// Grow the compose textarea with its content (capped) for comfortable typing.
function _autoSizeInput(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

async function handleSend() {
  const input  = document.getElementById('message-input');
  const text   = input.value.trim();
  if (!text) return;

  const conv = conversationForKey(state.activeConversation);
  const destination = conv.kind === 'dm' ? conv.nodeId : null;
  const channel = conv.kind === 'dm' ? 0 : conv.channel;
  const convKey = state.activeConversation;

  // Optimistically render the outgoing message in the active thread.
  const optimistic = {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    text,
    outgoing: true,
    conversation: convKey,
    timestamp: Date.now() / 1000,
    from_name: 'Me',
    status: 'sending', // sending -> sent | failed
    destination,
    channel,
  };
  storeMessage(optimistic);
  renderMessagesThread();
  input.value = '';
  _autoSizeInput(input);

  try {
    await sendMessage(text, destination, channel);
    optimistic.status = 'sent';
    // Register optimistic entry for pending-echo matching (see renderIncomingMessages).
    const echoKey = `${convKey}:${text}`;
    state._pendingEchoes = state._pendingEchoes || {};
    state._pendingEchoes[echoKey] = optimistic;
  } catch (err) {
    optimistic.status = 'failed';
    showToast(`Send failed: ${err.message}`, 'error');
  }
  // Re-render so the status indicator (tick / cross) updates.
  if (state.activeConversation === optimistic.conversation) {
    renderMessagesThread();
  }
}

// ============================================================================
// Unread badge on the Messages nav item and tab button
// ============================================================================

function updateMessagesBadge() {
  const total = Object.values(state.conversations).reduce((sum, c) => sum + (c.unread || 0), 0);
  const label = total > 0 ? (total > 99 ? '99+' : String(total)) : '';
  for (const id of ['nav-messages-badge', 'tab-messages-badge']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = label;
    el.classList.toggle('has-unread', total > 0);
  }
}

// ============================================================================
// Full-screen Messages View — sidebar + thread rendering
// ============================================================================

function renderMessagesSidebar() {
  const list = document.getElementById('messages-conv-list');
  if (!list) return;

  const keys = new Set(['ch:0']);
  for (const k of Object.keys(state.conversations)) keys.add(k);
  for (const k of Object.keys(state.messagesByConv)) {
    if (state.messagesByConv[k].length) keys.add(k);
  }
  for (const ch of (state.channels || [])) {
    if (ch && ch.index != null) keys.add(`ch:${ch.index}`);
  }

  // Filter out dismissed conversations (except the active one)
  const filtered = [...keys].filter(k => k === state.activeConversation || !state.dismissedConvs.has(k));

  const ordered = filtered.sort((a, b) => {
    const ca = a.startsWith('ch:') ? 0 : 1;
    const cb = b.startsWith('ch:') ? 0 : 1;
    if (ca !== cb) return ca - cb;
    return a.localeCompare(b);
  });

  list.innerHTML = '';
  for (const key of ordered) {
    const conv = _ensureConversation(key);
    const thread = (state.messagesByConv[key] || []);
    const lastMsg = thread.length > 0 ? thread[thread.length - 1] : null;
    const time = lastMsg
      ? new Date(lastMsg.timestamp * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
      : '';

    const item = document.createElement('div');
    item.className = `messages-conv-item ${key === state.activeConversation ? 'active' : ''}`;
    item.dataset.conv = key;
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', key === state.activeConversation);

    const avatar = document.createElement('div');
    avatar.className = `messages-conv-avatar ${conv.kind}`;
    avatar.textContent = conv.name.charAt(0).toUpperCase();

    const content = document.createElement('div');
    content.className = 'messages-conv-content';

    const nameRow = document.createElement('div');
    nameRow.className = 'messages-conv-name-row';
    nameRow.innerHTML = `<span class="messages-conv-name">${escapeHtml(conv.name)}</span>`;
    if (conv.unread > 0) {
      nameRow.innerHTML += `<span class="messages-conv-unread">${conv.unread > 99 ? '99+' : conv.unread}</span>`;
    }
    if (time) {
      nameRow.innerHTML += `<span class="messages-conv-time">${time}</span>`;
    }

    content.appendChild(nameRow);

    if (lastMsg) {
      const lastMsgEl = document.createElement('div');
      lastMsgEl.className = 'messages-conv-last-msg';
      lastMsgEl.textContent = lastMsg.text || '(media)';
      content.appendChild(lastMsgEl);
    }

    item.appendChild(avatar);
    item.appendChild(content);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'messages-conv-close';
    closeBtn.setAttribute('aria-label', `Close ${conv.name}`);
    closeBtn.innerHTML = '✕';
    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      state.dismissedConvs.add(key);
      try { localStorage.setItem('nodepulse_dismissed_convs', JSON.stringify([...state.dismissedConvs])); } catch (_) {}
      if (state.activeConversation === key) {
        selectConversation('ch:0');
      }
      renderMessagesSidebar();
      renderMessagesThread();
    });
    item.appendChild(closeBtn);

    item.addEventListener('click', () => selectMessagesConversation(key));
    list.appendChild(item);
  }
}

function selectMessagesConversation(key) {
  selectConversation(key);
  renderMessagesSidebar();
  renderMessagesThread();
  // On mobile, close the sidebar to show the thread
  document.body.classList.remove('messages-sidebar-open');
}

function renderMessagesThread() {
  const list = document.getElementById('messages-thread-list');
  if (!list) return;

  const nameEl = document.getElementById('messages-thread-name');
  const subtitleEl = document.getElementById('messages-thread-subtitle');
  const avatarEl = document.getElementById('messages-thread-avatar');

  if (!state.activeConversation || state.activeConversation === 'ch:0') {
    const conv = conversationForKey('ch:0');
    if (nameEl) nameEl.textContent = conv.name;
    if (subtitleEl) subtitleEl.textContent = 'Channel broadcast';
    if (avatarEl) { avatarEl.textContent = conv.name.charAt(0).toUpperCase(); avatarEl.style.display = ''; }
  } else {
    const conv = conversationForKey(state.activeConversation);
    if (nameEl) nameEl.textContent = conv.name;
    if (subtitleEl) subtitleEl.textContent = conv.kind === 'dm' ? 'Direct message' : 'Channel';
    if (avatarEl) { avatarEl.textContent = conv.name.charAt(0).toUpperCase(); avatarEl.style.display = ''; }
  }

  const conv = conversationForKey(state.activeConversation || 'ch:0');

  const chSelect = document.getElementById('messages-channel-select');
  if (chSelect) {
    if (conv.kind === 'dm') {
      chSelect.style.display = 'none';
    } else {
      chSelect.style.display = '';
      chSelect.value = String(conv.channel ?? 0);
    }
  }

  const thread = (state.messagesByConv[state.activeConversation] || []);

  if (!state.convHistoryDays) state.convHistoryDays = {};
  const daysToShow = state.convHistoryDays[state.activeConversation] || 0;
  
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const cutoffTime = startOfToday - (daysToShow * 86400);

  const q = state.messageFilter.trim().toLowerCase();
  
  let filtered = thread;
  let hasMore = false;
  
  if (q) {
    filtered = thread.filter(m =>
      (m.text || '').toLowerCase().includes(q) ||
      (m.from_name || '').toLowerCase().includes(q)
    );
  } else {
    const allLen = thread.length;
    filtered = thread.filter(m => (m.timestamp || (Date.now() / 1000)) >= cutoffTime);
    hasMore = filtered.length < allLen;
  }

  const convChanged = list.dataset.lastConv !== state.activeConversation;
  list.dataset.lastConv = state.activeConversation;
  const wasAtBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  const shouldScroll = wasAtBottom || convChanged;

  list.innerHTML = '';

  if (hasMore) {
    const loadMoreBtn = document.createElement('button');
    loadMoreBtn.className = 'messages-load-more';
    loadMoreBtn.textContent = 'Load previous days';
    loadMoreBtn.onclick = () => {
      state.convHistoryDays[state.activeConversation] = daysToShow + 1;
      renderMessagesThread();
    };
    list.appendChild(loadMoreBtn);
  }

  if (filtered.length === 0) {
    list.insertAdjacentHTML('beforeend', `
      <div class="messages-thread-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <div>${q ? `No messages match "${escapeHtml(q)}".` : (hasMore ? 'No messages for this period.' : 'No messages yet.')}</div>
      </div>`);
    return;
  }

  let lastSender = null;
  let lastOutgoing = null;

  for (let i = 0; i < filtered.length; i++) {
    const msg = filtered[i];
    const next = filtered[i + 1] || null;

    const isOutgoing = !!msg.outgoing;
    const senderKey = isOutgoing ? '__self__' : (msg.from_id || msg.from_name || '');
    const sameSender = senderKey === lastSender && isOutgoing === lastOutgoing;
    const nextSameSender = next && (!!next.outgoing === isOutgoing) &&
      (isOutgoing ? true : (next.from_id || next.from_name || '') === senderKey);

    lastSender = senderKey;
    lastOutgoing = isOutgoing;

    const type = isOutgoing ? 'outgoing' : 'incoming';
    const bubble = document.createElement('div');
    let cls = `message-bubble ${type}`;
    if (sameSender) cls += ' msg-grouped';
    if (!nextSameSender) cls += ' msg-group-end';
    bubble.className = cls;

    const time = formatMessageTime(msg.timestamp);

    let statusIcon = '';
    if (isOutgoing) {
      const ack = msg.ack_status || msg.status;
      if (ack === 'sending') {
        statusIcon = '<span class="msg-status sending" title="Sending">&#8987;</span>';
      } else if (ack === 'sent') {
        statusIcon = '<span class="msg-status sent" title="Sent">&#10003;</span>';
      } else if (ack === 'delivered') {
        statusIcon = '<span class="msg-status delivered" title="Delivered">&#10003;&#10003;</span>';
      } else if (ack === 'failed') {
        statusIcon = '<span class="msg-status failed" title="Failed">&#9888;</span>';
        bubble.classList.add('failed');
      }
    }

    let channelHtml = '';
    if (conv.kind === 'dm' && msg.channel != null) {
      const ch = parseInt(msg.channel, 10) || 0;
      const cfg = (state.channels || []).find(c => c && c.index === ch);
      const chName = cfg && cfg.name ? cfg.name : (ch === 0 ? 'Primary' : `Ch ${ch}`);
      channelHtml = `<span class="message-channel">${escapeHtml(chName)}</span>`;
    }

    const sender = isOutgoing
      ? null
      : (shortNameFor(msg.from_id) || msg.from_name || nodeName(msg.from_id) || 'Unknown');

    bubble.innerHTML = `
      ${sender && !sameSender ? `<div class="message-sender">${escapeHtml(sender)}</div>` : ''}
      <div class="message-text">${escapeHtml(msg.text)}</div>
      <div class="message-meta">
        <span class="message-time">${time}</span>${channelHtml}${statusIcon}
      </div>`;

    if (isOutgoing && msg.status === 'failed') {
      bubble.style.cursor = 'pointer';
      bubble.addEventListener('click', () => retryMessage(msg));
    }
    list.appendChild(bubble);
  }

  if (shouldScroll) {
    requestAnimationFrame(() => {
      list.scrollTop = list.scrollHeight;
    });
  }
}

async function handleMessagesSend() {
  const input  = document.getElementById('messages-message-input');
  const text   = input.value.trim();
  if (!text) return;

  const conv = conversationForKey(state.activeConversation || 'ch:0');
  const destination = conv.kind === 'dm' ? conv.nodeId : null;
  const channel = conv.kind === 'dm' ? 0 : conv.channel;
  const convKey = state.activeConversation || 'ch:0';

  const optimistic = {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    text,
    outgoing: true,
    conversation: convKey,
    timestamp: Date.now() / 1000,
    from_name: 'Me',
    status: 'sending',
    destination,
    channel,
  };
  storeMessage(optimistic);
  renderMessagesThread();
  renderMessagesSidebar();
  input.value = '';
  input.style.height = 'auto';

  try {
    await sendMessage(text, destination, channel);
    optimistic.status = 'sent';
    // Register this optimistic entry so renderIncomingMessages can match the
    // server-side confirmation (which arrives on the next poll, ~15 s later)
    // and upgrade the existing bubble instead of appending a second one.
    const echoKey = `${convKey}:${text}`;
    state._pendingEchoes = state._pendingEchoes || {};
    state._pendingEchoes[echoKey] = optimistic;
  } catch (err) {
    optimistic.status = 'failed';
    showToast(`Send failed: ${err.message}`, 'error');
  }
  if (state.activeConversation === optimistic.conversation) {
    renderMessagesThread();
    renderMessagesSidebar();
  }
}

function populateMessagesChannelSelect() {
  const sel = document.getElementById('messages-channel-select');
  if (!sel) return;
  const prev = sel.value;

  const chans = (state.channels || []).filter(c => c && c.index != null);
  const hasPrimary = chans.some(c => c.index === 0);
  const list = hasPrimary ? chans : [{ index: 0, name: 'Primary' }, ...chans];

  sel.innerHTML = '';
  for (const c of list) {
    const opt = document.createElement('option');
    opt.value = String(c.index);
    opt.textContent = c.name ? `${c.name} (ch ${c.index})` : `Channel ${c.index}`;
    sel.appendChild(opt);
  }

  if (prev && list.some(c => String(c.index) === prev)) sel.value = prev;
  else sel.value = '0';
}

// ============================================================================
// Node picker — search nodes and start a DM
// ============================================================================

function toggleNodePicker() {
  const picker = document.getElementById('messages-node-picker');
  if (!picker) return;
  const hidden = picker.classList.toggle('hidden');
  if (!hidden) {
    renderNodePicker('');
    const input = document.getElementById('messages-node-picker-search');
    if (input) { input.value = ''; input.focus(); }
  }
}

function closeNodePicker() {
  const picker = document.getElementById('messages-node-picker');
  if (picker) picker.classList.add('hidden');
}

function renderNodePicker(query) {
  const list = document.getElementById('messages-node-picker-list');
  if (!list) return;

  const nodes = (state.nodes || []).filter(n => n && n.id);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? nodes.filter(n =>
        (n.long_name || '').toLowerCase().includes(q) ||
        (n.short_name || '').toLowerCase().includes(q) ||
        n.id.toLowerCase().includes(q)
      )
    : nodes;

  list.innerHTML = '';

  if (filtered.length === 0) {
    list.innerHTML = `<div class="messages-node-picker-empty">${q ? 'No nodes match.' : 'No nodes available.'}</div>`;
    return;
  }

  for (const node of filtered) {
    const name = node.long_name || node.short_name || 'Unknown';
    const item = document.createElement('div');
    item.className = 'messages-node-picker-item';
    item.innerHTML = `
      <div class="node-picker-icon">${name.charAt(0).toUpperCase()}</div>
      <div class="node-picker-info">
        <div class="node-picker-name">${escapeHtml(name)}</div>
        <div class="node-picker-id">${escapeHtml(node.id)}</div>
      </div>`;
    item.addEventListener('click', () => {
      closeNodePicker();
      if (state.currentView !== 'messages') switchView('messages');
      const key = `dm:${node.id}`;
      selectMessagesConversation(key);
      // Focus the message input
      const msgInput = document.getElementById('messages-message-input');
      if (msgInput) setTimeout(() => msgInput.focus(), 100);
    });
    list.appendChild(item);
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

  // Defer the heavy per-view work (map initialisation, marker rendering,
  // topology graph build, settings fetch) out of the click handler. The view
  // panel is already shown synchronously above, so by the time rAF fires the
  // container has a real size and Leaflet/vis-network can lay out correctly
  // — this avoids the "Forced reflow" / long-click-handler violations.
  requestAnimationFrame(() => {
    // Leaflet maps need invalidateSize() after becoming visible.
    if (viewName === 'dashboard') {
      dashMap.invalidateSize();
    } else if (viewName === 'map') {
      fullMap.init();
      fullMap.updateNodes(state.nodes);
      fullMap.invalidateSize();
    } else if (viewName === 'settings') {
      renderSettings();
    } else if (viewName === 'topology') {
      topology.init();
      topology.updateData(state);
    } else if (viewName === 'packets') {
      pollPackets();
    } else if (viewName === 'messages') {
      renderMessagesSidebar();
      renderMessagesThread();
      updateMessagesBadge();
      // On mobile (≤768px), show the conversation sidebar by default so the
      // user sees the list before picking a conversation. The sidebar slides
      // over the thread and is dismissed when a conversation is tapped.
      if (window.innerWidth <= 768) {
        document.body.classList.add('messages-sidebar-open');
      }
    } else if (viewName === 'config') {
      // device_config.js registers itself on the window so we can call it
      // from here without a circular import.
      if (typeof window.renderDeviceConfig === 'function') {
        window.renderDeviceConfig();
      }
    }
  });
}

// ============================================================================
// Settings View — populate with read-only config info
// ============================================================================
function _setEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text ?? '—';
}

async function renderSettings() {
  try {
    const status = await fetchStatus();
    const cfg = status.config || {};

    // Connection
    _setEl('settings-conn',      status.connected ? '✓ Connected' : '✗ Disconnected');
    _setEl('settings-conn-type', cfg.connection_type
      ? (cfg.connection_type === 'direct' ? 'Direct (TCP)' : 'Proxy') : '—');
    _setEl('settings-host',      cfg.meshtastic_host || '—');
    _setEl('settings-port',      cfg.meshtastic_port ?? '—');

    // Show/hide proxy rows based on mode
    const proxyRow = document.getElementById('settings-proxy-row');
    const proxyPortRow = document.getElementById('settings-proxy-port-row');
    const isProxy = cfg.connection_type === 'proxy';
    if (proxyRow)     proxyRow.style.display     = isProxy ? '' : 'none';
    if (proxyPortRow) proxyPortRow.style.display  = isProxy ? '' : 'none';
    _setEl('settings-proxy-host', cfg.proxy_host || '—');
    _setEl('settings-proxy-port', cfg.proxy_port ?? '—');

    // Mesh
    _setEl('settings-count',   status.node_count ?? '—');
    const ignored = cfg.ignored_nodes;
    _setEl('settings-ignored', (ignored && ignored.length > 0) ? ignored.join(', ') : 'None');

    // Home Assistant
    _setEl('settings-ha-url',    cfg.ha_base_url || '—');
    _setEl('settings-access-key', cfg.access_key_set ? '••••••• (set)' : 'Not set');
    _setEl('settings-token-validation',
      cfg.disable_token_validation ? 'Disabled (accept any token)' : 'Enabled');

    // MQTT Bridge
    const mqttOn = cfg.mqtt_enabled;
    _setEl('settings-mqtt-forwarding', mqttOn
      ? (cfg.mqtt_forwarding_enabled ? '✓ Enabled' : 'Ingestion only')
      : 'Disabled (mqtt_enabled is false)');
    _setEl('settings-mqtt-address', mqttOn ? (cfg.mqtt_address || '—') : '—');
    _setEl('settings-mqtt-port',    mqttOn ? (cfg.mqtt_port ?? '—') : '—');
    _setEl('settings-mqtt-username', mqttOn
      ? (cfg.mqtt_username_set ? '••••••• (set)' : 'None') : '—');
    _setEl('settings-mqtt-password', mqttOn
      ? (cfg.mqtt_password_set ? '•••••••• (set)' : 'None') : '—');
    _setEl('settings-mqtt-topic',   mqttOn ? (cfg.mqtt_topic   || '—') : '—');
    const geoStr = cfg.mqtt_geo_filter_enabled
      ? `Box: ${(cfg.mqtt_lat_min ?? 0).toFixed(2)},${(cfg.mqtt_lng_min ?? 0).toFixed(2)} → ${(cfg.mqtt_lat_max ?? 0).toFixed(2)},${(cfg.mqtt_lng_max ?? 0).toFixed(2)}`
      : 'Disabled';
    _setEl('settings-mqtt-geo', geoStr);
    const portnums = cfg.mqtt_portnum_allowlist || [];
    _setEl('settings-mqtt-portnums',
      mqttOn ? ((portnums.length > 0) ? portnums.join(', ') : 'All (no filter)') : '—');
    const blocklist = cfg.mqtt_node_blocklist || [];
    _setEl('settings-mqtt-blocklist',
      mqttOn ? ((blocklist.length > 0) ? blocklist.join(', ') : 'None') : '—');

    // Telegram Bot
    const tgEnabled = cfg.telegram_enabled;
    _setEl('settings-telegram-status', tgEnabled ? '✓ Enabled' : 'Disabled');
    _setEl('settings-telegram-token',
      tgEnabled
        ? (cfg.telegram_bot_token_set ? '●●●●●●●● (set)' : '⚠ Not set')
        : '—');
    _setEl('settings-telegram-chat-id',
      tgEnabled ? (cfg.telegram_chat_id || '⚠ Not set') : '—');
    const chatIds = cfg.telegram_authorized_chat_ids || [];
    _setEl('settings-telegram-chat-ids',
      tgEnabled
        ? ((chatIds.length > 0) ? chatIds.join(', ') : (cfg.telegram_chat_id || '⚠ Not set'))
        : '—');
    _setEl('settings-telegram-channels',
      tgEnabled ? `Ch ${(cfg.telegram_forward_channels || [0]).join(', Ch ')}` : '—');
    _setEl('settings-telegram-dms',
      tgEnabled ? (cfg.telegram_forward_dms ? '✓ Yes' : 'No') : '—');
    _setEl('settings-telegram-commands',
      tgEnabled ? (cfg.telegram_allow_commands ? '✓ Yes' : 'No') : '—');

    // Auto Responder
    _setEl('settings-auto-responder-status',
      cfg.auto_responder_enabled ? '✓ Enabled' : 'Disabled');
    _setEl('settings-auto-responder-message',
      cfg.auto_responder_enabled ? (cfg.auto_responder_message || '—') : '—');

    // Schedule & Logging
    _setEl('settings-scan-interval', cfg.scan_interval != null ? `${cfg.scan_interval} s` : '—');
    _setEl('settings-log-level',     cfg.log_level || '—');

    // About
    _setEl('settings-version', status.addon_version || '—');

  } catch (err) {
    // Surface the failure instead of hiding it behind "—" placeholders so the
    // cause (e.g. an unreachable addon API under ingress) is visible.
    const msg = (err && err.message) ? err.message : String(err);
    _setEl('settings-conn', `⚠ Error: ${msg}`);
    console.error('renderSettings failed:', err);
  }
}

/**
 * Toggle browser notification preference for a mesh node.
 * This is purely client-side — stored in localStorage, no backend call needed.
 */
async function notifyNode(nodeId, enabled) {
  // No-op: the calling code already updates state.notifyNodes and localStorage.
  return { node_id: nodeId, enabled };
}

// ============================================================================
// Main Poll Loop
// ============================================================================

/**
 * Refresh tracked-node state from the HA relay.
 *
 * This is intentionally NOT part of pollData() because it relays to Home
 * Assistant (potentially slow — up to several seconds when HA is unreachable).
 * Running it in the critical poll path would block node/status/map rendering
 * on every 15s tick. We call it independently on a much slower cadence.
 */
async function refreshTrackedNodes() {
  try {
    const tracked = await fetchTrackedNodes();
    state.trackedNodes = new Set(
      Array.isArray(tracked) ? tracked : (tracked.node_ids || [])
    );
    // If nodes are already loaded, re-render the grid so the newly-fetched
    // checkboxes appear immediately instead of waiting for the next 15s poll.
    if (state.nodes.length > 0) {
      renderNodesGrid(state.nodes);
    }
  } catch (err) {
    // Non-fatal — tracked state is cosmetic (checkbox state on node cards).
    // The warning is intentionally quiet; the UI degrades gracefully.
    console.warn('Tracked-nodes fetch failed:', err);
  }
}

async function pollData() {
  state._pollCount += 1;

  const isFirstPoll = state._pollCount === 1;
  const isDue = state._pollCount % TRACKED_NODES_POLL_EVERY_N === 0;

  // Fast path: status, nodes, messages, and channels are all served directly
  // by the addon's own backend — no external relay, reliably sub-second.
  const [statusResult, nodesResult, messagesResult, channelsResult] = await Promise.allSettled([
    fetchStatus(),
    fetchNodes(),
    fetchMessages(),
    fetchChannels(),
  ]);

  if (statusResult.status === 'fulfilled') {
    state.status = statusResult.value;
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
    
    // Initialize topology graph if active, and update its data
    if (state.currentView === 'topology') {
      topology.init();
      topology.updateData(state);
    }

    // Pick the best node for SNR/RSSI charts. When the user has explicitly
    // selected a node, use that. Otherwise fall back to the first remote node
    // that actually reports signal data — the self (gateway) node never has
    // inbound SNR/RSSI because you don't receive packets from yourself.
    let chartNode = state.nodes.find(n => n.id === state.selectedNodeId);
    if (!chartNode) {
      chartNode = state.nodes.find(n => n.id !== state.selfId && (n.snr != null || n.rssi != null))
                  ?? state.nodes.find(n => n.id !== state.selfId)
                  ?? state.nodes[0];
    }

    // For utilization trends, prefer the self (gateway) node's device metrics,
    // but fall back to any node that reports them — the library may not always
    // populate deviceMetrics on the local node (e.g. no telemetry received yet).
    const selfNode = state.nodes.find(n => n.id === state.selfId);
    let chanUtil = selfNode?.channel_utilization ?? null;
    let airUtil  = selfNode?.air_util_tx ?? null;
    if (chanUtil == null && airUtil == null) {
      const utilNode = state.nodes.find(n => n.channel_utilization != null || n.air_util_tx != null);
      if (utilNode) {
        chanUtil = utilNode.channel_utilization ?? null;
        airUtil  = utilNode.air_util_tx ?? null;
      }
    }
    charts.addPoint(chartNode?.snr ?? null, chartNode?.rssi ?? null, state.nodes.length, chanUtil, airUtil);
  } else {
    console.warn('Nodes fetch failed:', nodesResult.reason);
  }

  // Update status bar and node count badge now that both status and nodes are loaded
  renderStatusBar(state.status);

  if (messagesResult.status === 'fulfilled') {
    renderIncomingMessages(messagesResult.value);
  } else {
    console.warn('Messages fetch failed:', messagesResult.reason);
  }

  if (channelsResult.status === 'fulfilled') {
    const chans = Array.isArray(channelsResult.value) ? channelsResult.value : [];
    state.channels = chans;
    renderChannelSelect();
    populateMessagesChannelSelect();
    // Channel tabs must re-render once the channel list is known so they
    // appear immediately (not only after a message arrives on each channel).
    renderConversationTabs();
  } else {
    console.warn('Channels fetch failed:', channelsResult.reason);
  }

  // Fetch tags on first poll only (they change rarely).
  if (isFirstPoll) {
    try {
      state.nodeTags = await fetchTags();
    } catch (err) {
      console.warn('Tags fetch failed:', err);
    }
  }

  // Fetch position history for map trails — first poll, then every 120s (8 cycles).
  // If the heatmap is currently visible, refresh every poll so it stays current.
  const heatmapVisible = fullMap._heatmapVisible || dashMap._heatmapVisible;
  if (isFirstPoll || state._pollCount % 8 === 0 || heatmapVisible || fullMap._rulerActive) {
    fetchPositionHistory().then(data => {
      state.posHistory = data;
      dashMap.updateTrails(data, state.nodes);
      fullMap.updateTrails(data, state.nodes);
      fullMap.setPosHistory(data);
      if (fullMap._rulerActive) fullMap._updateRulerPanel();
    }).catch(err => {
      console.warn('Position history fetch failed:', err);
    });
  }

  // Fetch waypoints every poll when the map view is active; every 8 polls otherwise.
  if (state.currentView === 'map' || isFirstPoll || state._pollCount % 8 === 0) {
    fetchWaypoints().then(wps => {
      fullMap.updateWaypoints(wps, handleDeleteWaypoint, handleUpdateWaypoint);
    }).catch(err => {
      console.warn('Waypoints fetch failed:', err);
    });
  }

  // Slow path: relay to HA for tracked-node state. Run only on the first poll
  // (so the checkboxes on the Nodes tab render correctly on initial load) and
  // then every TRACKED_NODES_POLL_EVERY_N cycles thereafter. This avoids
  // blocking the fast critical render on every tick.
  if (isFirstPoll || isDue) {
    // Fire-and-forget: do NOT await — let it run concurrently so the rest of
    // the UI is already rendered before this potentially-slow call finishes.
    refreshTrackedNodes();
  }

  if (state.currentView === 'packets') pollPackets();

  // Schedule the next poll safely (no overlapping executions if a fetch stalls).
  setTimeout(pollData, POLL_INTERVAL_MS);
}

/**
 * Render any newly-arrived inbound text messages into the message feed.
 * We track seen message IDs so a message is only appended once even though
 * the API returns the whole recent buffer on every poll.
 */
function renderIncomingMessages(messages) {
  if (!Array.isArray(messages)) return;
  let changed = false;

  const initialBatch = !state._initialBatchComplete;
  state._pendingEchoes = state._pendingEchoes || {};

  for (const msg of messages) {
    if (!msg.id) continue;

    // Patch ACK status on already-seen outgoing messages (server resolved it).
    if (state.seenMessageIds.has(msg.id)) {
      const key = msg.conversation || (msg.is_dm ? `dm:${msg.from_id}` : `ch:${msg.channel ?? 0}`);
      const thread = state.messagesByConv[key];
      if (thread) {
        const stored = thread.find(m => m.id === msg.id);
        if (stored && msg.ack_status && stored.ack_status !== msg.ack_status) {
          stored.ack_status = msg.ack_status;
          stored.ack_at    = msg.ack_at;
          changed = true;
        }
      }
      continue;
    }

    // When a server-confirmed outgoing message arrives, check if we already have
    // an optimistic local bubble for it. If so, upgrade the optimistic entry
    // in-place (swap its id and ack_status) rather than appending a second bubble.
    // This handles the case where the server echo arrives on the next poll
    // (~15 s later), well outside the 3-second firmware-echo dedup window.
    if (msg.outgoing) {
      const echoKey = `${msg.conversation || ''}:${msg.text || ''}`;
      const pending = state._pendingEchoes[echoKey];
      if (pending) {
        // Upgrade the existing optimistic entry to the real server-confirmed one.
        pending.id = msg.id;
        pending.ack_status = msg.ack_status || pending.ack_status;
        pending.ack_at = msg.ack_at ?? pending.ack_at;
        // Register the real id so future polls can patch ack status on it.
        state.seenMessageIds.add(msg.id);
        delete state._pendingEchoes[echoKey];
        changed = true;
        continue;
      }
    }

    state.seenMessageIds.add(msg.id);
    storeMessage(msg, { skipUnread: initialBatch });
    changed = true;

    if (!initialBatch && !msg.outgoing) _fireNewMessageNotification(msg);
  }

  state._initialBatchComplete = true;

  if (changed) {
    renderConversationTabs();
    renderMessagesSidebar();
    renderMessagesThread();
    updateMessagesBadge();
  }
}

// ============================================================================
// Browser Notifications (Feature 2)
// ============================================================================

function _fireNewMessageNotification(msg) {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  if (document.visibilityState === 'visible') return;
  const hasExplicitSettings = state.notifyNodes.size > 0;
  if (hasExplicitSettings && msg.from_id && !state.notifyNodes.has(msg.from_id)) return;
  const title = `NodePulse — ${msg.from_name || msg.from_id || 'Unknown'}`;
  const body  = (msg.text || '').length > 100 ? msg.text.slice(0, 97) + '…' : msg.text;
  try {
    const n = new Notification(title, { body, icon: '/assets/icon.png', tag: msg.id });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (_) { /* Not supported in this context */ }
}

async function requestNotificationPermission() {
  if (!('Notification' in window)) {
    showToast('Browser notifications not supported in this context.', 'error');
    return;
  }
  const result = await Notification.requestPermission();
  state._notifPermission = result;
  localStorage.setItem('np_notifications', result);
  _updateBellButton();
  if (result === 'granted') showToast('Notifications enabled ✓', 'success');
  else if (result === 'denied') showToast('Notifications blocked — check browser settings.', 'error');
}

function _updateBellButton() {
  const btn = document.getElementById('bell-btn');
  if (!btn) return;
  const perm = ('Notification' in window) ? Notification.permission : 'unsupported';
  btn.textContent = perm === 'denied' ? '🔕' : '🔔';
  btn.title = perm === 'granted'  ? 'Notifications ON — click to manage'
            : perm === 'denied'   ? 'Notifications BLOCKED by browser'
            : 'Enable notifications';
  btn.classList.toggle('active', perm === 'granted');
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
    el.addEventListener('click', () => {
      switchView(el.dataset.view);
      // Close the mobile drawer after navigating.
      document.body.classList.remove('nav-open');
    });
  });

  // Mobile sidebar drawer: hamburger opens it, backdrop closes it.
  const menuToggle = document.getElementById('menu-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (menuToggle) {
    menuToggle.addEventListener('click', () => {
      document.body.classList.toggle('nav-open');
    });
  }
  if (backdrop) {
    backdrop.addEventListener('click', () => document.body.classList.remove('nav-open'));
  }

  // Theme toggle: persist choice in localStorage.
  const themeToggle = document.getElementById('theme-toggle');
  if (themeToggle) {
    const saved = localStorage.getItem('nodepulse-theme');
    if (saved === 'light') {
      document.body.classList.add('light-theme');
      themeToggle.textContent = '☀️';
    }
    themeToggle.addEventListener('click', () => {
      const isLight = document.body.classList.toggle('light-theme');
      localStorage.setItem('nodepulse-theme', isLight ? 'light' : 'dark');
      themeToggle.textContent = isLight ? '☀️' : '🌙';
    });
  }

  // Load notify nodes from localStorage
    try {
      const savedNotify = localStorage.getItem('nodepulse_notify_nodes');
      if (savedNotify) {
        state.notifyNodes = new Set(JSON.parse(savedNotify));
      }
    } catch (_) { /* ignore */ }

  // Load dismissed conversations from localStorage
    try {
      const savedDismissed = localStorage.getItem('nodepulse_dismissed_convs');
      if (savedDismissed) {
        state.dismissedConvs = new Set(JSON.parse(savedDismissed));
      }
    } catch (_) { /* ignore */ }

  // Wire up the send button and Enter key shortcut in the message input.
  const sendBtn = document.getElementById('send-btn');
  if (sendBtn) sendBtn.addEventListener('click', handleSend);
  const msgInput = document.getElementById('message-input');
  if (msgInput) {
    msgInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
    msgInput.addEventListener('input', () => _autoSizeInput(msgInput));
  }

  // Channel selector: switching it jumps to that channel's conversation thread.
  const channelSelect = document.getElementById('channel-select');
  if (channelSelect) {
    channelSelect.addEventListener('change', () => {
      const ch = parseInt(channelSelect.value, 10) || 0;
      selectConversation(`ch:${ch}`);
    });
  }

  // Message search: re-render the list as the user types.
  const msgSearch = document.getElementById('message-search-input');
  if (msgSearch) {
    msgSearch.addEventListener('input', (e) => {
      state.messageFilter = e.target.value;
      renderMessagesThread();
    });
  }

  // Conversation tabs collapse toggle on dashboard
  const convTabsToggle = document.getElementById('conv-tabs-toggle');
  const convTabsSection = document.getElementById('conv-tabs-section');
  if (convTabsToggle && convTabsSection) {
    const saved = localStorage.getItem('nodepulse-conv-tabs-collapsed') === 'true';
    if (saved) {
      convTabsSection.classList.add('collapsed');
      convTabsToggle.classList.add('collapsed');
    }
    convTabsToggle.addEventListener('click', () => {
      const collapsed = convTabsSection.classList.toggle('collapsed');
      convTabsToggle.classList.toggle('collapsed');
      localStorage.setItem('nodepulse-conv-tabs-collapsed', collapsed);
    });
  }

  // ---- Full-screen Messages View event wiring ----------------------------

  const msgsSendBtn = document.getElementById('messages-send-btn');
  if (msgsSendBtn) {
    msgsSendBtn.addEventListener('click', handleMessagesSend);
  }
  const msgsInput = document.getElementById('messages-message-input');
  if (msgsInput) {
    msgsInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleMessagesSend();
      }
    });
    msgsInput.addEventListener('input', () => _autoSizeInput(msgsInput));
  }
  const msgsChanSelect = document.getElementById('messages-channel-select');
  if (msgsChanSelect) {
    msgsChanSelect.addEventListener('change', () => {
      const ch = parseInt(msgsChanSelect.value, 10) || 0;
      selectMessagesConversation(`ch:${ch}`);
    });
  }
  const msgsSearch = document.getElementById('messages-search-input');
  if (msgsSearch) {
    msgsSearch.addEventListener('input', (e) => {
      state.messageFilter = e.target.value;
      renderMessagesSidebar();
      renderMessagesThread();
    });
  }
  const msgsNewDm = document.getElementById('messages-new-dm');
  if (msgsNewDm) {
    msgsNewDm.addEventListener('click', toggleNodePicker);
  }
  const nodePickerClose = document.getElementById('messages-node-picker-close');
  if (nodePickerClose) {
    nodePickerClose.addEventListener('click', closeNodePicker);
  }
  const nodePickerSearch = document.getElementById('messages-node-picker-search');
  if (nodePickerSearch) {
    nodePickerSearch.addEventListener('input', (e) => {
      renderNodePicker(e.target.value);
    });
    nodePickerSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeNodePicker();
    });
  }
  // Close node picker on Escape anywhere
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNodePicker();
  });
  // Close node picker on click outside
  document.addEventListener('click', (e) => {
    const picker = document.getElementById('messages-node-picker');
    const btn = document.getElementById('messages-new-dm');
    if (picker && !picker.classList.contains('hidden') &&
        !picker.contains(e.target) && btn && !btn.contains(e.target)) {
      closeNodePicker();
    }
  });

  // Mobile messages sidebar: back button & backdrop
  const msgsBackBtn = document.getElementById('messages-thread-back');
  if (msgsBackBtn) {
    msgsBackBtn.addEventListener('click', () => {
      document.body.classList.add('messages-sidebar-open');
    });
  }
  const msgsBackdrop = document.querySelector('.messages-sidebar-backdrop');
  if (msgsBackdrop) {
    msgsBackdrop.addEventListener('click', () => {
      document.body.classList.remove('messages-sidebar-open');
    });
  }

  // Event delegation on the nodes grid — attached once here so it is NOT
  // re-added on every 15s poll inside renderNodesGrid().
  document.getElementById('nodes-grid').addEventListener('click', handleNodeCardAction);

  // Tag input change — debounced save via change event.
  document.getElementById('nodes-grid').addEventListener('change', async (e) => {
    const input = e.target.closest('.tag-input');
    if (!input) return;
    const nodeId = input.dataset.node;
    const raw = input.value;
    const tags = raw.split(',').map(s => s.trim()).filter(Boolean);
    try {
      state.nodeTags = await setTags(nodeId, tags);
      renderNodesGrid(state.nodes);
    } catch (err) {
      showToast(`Failed to save tags: ${err.message}`, 'error');
    }
  });

  // Nodes-tab filters: re-render the grid from the current cached node list
  // without waiting for the next poll.
  const nodeFilter = document.getElementById('node-filter');
  if (nodeFilter) {
    nodeFilter.addEventListener('input', (e) => {
      state.nodeFilter = e.target.value;
      renderNodesGrid(state.nodes);
    });
  }
  const signalFilter = document.getElementById('node-signal-filter');
  if (signalFilter) {
    signalFilter.addEventListener('change', (e) => {
      state.signalFilter = e.target.value;
      renderNodesGrid(state.nodes);
    });
  }

  // Map overlay toggles: control buttons on each Leaflet map dispatch custom
  // events; the "L"/"T"/"N" keys are keyboard shortcuts for the same actions.
  // `after(visible)` is an optional callback fired after a toggle, useful for
  // triggering a one-off data refresh (e.g. the heatmap).
  const wireToggle = (eventName, key, toggleFn, label, after) => {
    const handler = () => {
      const dash = toggleFn(dashMap);
      toggleFn(fullMap);
      showToast(`${label} ${dash ? 'shown' : 'hidden'}`, 'info', 1500);
      if (after) after(dash);
    };
    document.getElementById('map').addEventListener(eventName, handler);
    document.getElementById('full-map').addEventListener(eventName, handler);
    document.addEventListener('keydown', (e) => {
      // Ignore when typing in an input/textarea.
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key === key || e.key === key.toUpperCase()) handler();
    });
  };
  wireToggle('nodepulse:toggleselflinks', 's', (m) => m.toggleSelfLinks(), 'Self→node links');
  wireToggle('nodepulse:togglepeerlinks', 'p', (m) => m.togglePeerLinks(), 'Peer proximity links');
  wireToggle('nodepulse:toggletraces',    't', (m) => m.toggleTraces(), 'Traceroute paths');
  wireToggle('nodepulse:togglenames',     'n', (m) => m.toggleNames(), 'Node names');
  wireToggle('nodepulse:toggletrails',    'h', (m) => m.toggleTrails(), 'Position trails');
  wireToggle('nodepulse:toggleheatmap',   'm', (m) => m.toggleHeatmap(), 'Signal heatmap', (visible) => {
    if (!visible) return;
    // Show a loading toast so the user knows data is being fetched.
    const loadingToast = showToast('Loading heatmap data…', 'info', 30000);
    fetchPositionHistory()
      .then(data => {
        dashMap.updateTrails(data, dashMap._allNodes);
        fullMap.updateTrails(data, fullMap._allNodes);
        // Replace loading toast with success.
        if (loadingToast) loadingToast.remove();
        showToast('Heatmap updated', 'success', 1500);
      })
      .catch(() => {
        if (loadingToast) loadingToast.remove();
        showToast('Failed to load heatmap', 'error');
      });
  });

  // Keep the dashboard heatmap checkbox in sync with the map control-bar toggle.
  const syncHeatCheckbox = () => {
    const cb = document.getElementById('map-toggle-heatmap');
    if (cb) cb.checked = !!fullMap._heatmapVisible;
  };
  document.getElementById('map')?.addEventListener('nodepulse:toggleheatmap', syncHeatCheckbox);
  document.getElementById('full-map')?.addEventListener('nodepulse:toggleheatmap', syncHeatCheckbox);

  // Map node filter (text / max hops / last-heard window) — apply to both maps.
  wireMapFilters();

  // Topology: wire controls for dynamic updates
  const _updateTopologyFromUi = () => {
    const namesCk = document.getElementById('topology-toggle-names');
    const traceroutesCk = document.getElementById('topology-toggle-traceroutes');
    const neighborsCk = document.getElementById('topology-toggle-neighbors');
    const physicsCk = document.getElementById('topology-toggle-physics');

    if (namesCk) topology.setShowNames(namesCk.checked);
    if (traceroutesCk) topology.setShowTraceroutes(traceroutesCk.checked);
    if (neighborsCk) topology.setShowNeighbors(neighborsCk.checked);
    if (physicsCk) topology.setPhysicsEnabled(physicsCk.checked);
  };

  document.getElementById('topology-fit-btn')?.addEventListener('click', () => {
    topology.fit();
  });

  document.getElementById('topology-reset-btn')?.addEventListener('click', () => {
    topology.resetLayout();
  });

  const bindTopologyToggle = (id, setter) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', _updateTopologyFromUi);
    setter(el.checked); // initialize
  };

  bindTopologyToggle('topology-toggle-names', (v) => topology.setShowNames(!!v));
  bindTopologyToggle('topology-toggle-traceroutes', (v) => topology.setShowTraceroutes(!!v));
  bindTopologyToggle('topology-toggle-neighbors', (v) => topology.setShowNeighbors(!!v));
  bindTopologyToggle('topology-toggle-physics', (v) => topology.setPhysicsEnabled(!!v));

  const searchEl = document.getElementById('topology-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => topology.setSearchTerm(searchEl.value));
  }

  // Bell notification button (Feature 2).
  const bellBtn = document.getElementById('bell-btn');
  if (bellBtn) bellBtn.addEventListener('click', requestNotificationPermission);
  _updateBellButton();

  // Packet inspector controls.
  // Packet inspector — header click filters.
  document.getElementById('pkt-clear')?.addEventListener('click', () => { state.packetLog=[]; state.packetFilters={}; renderPacketTable(); });
  document.getElementById('pkt-export-json')?.addEventListener('click', exportPacketsJSON);
  document.getElementById('pkt-export-csv')?.addEventListener('click', exportPacketsCSV);
  document.getElementById('messages-export-json')?.addEventListener('click', exportMessagesJSON);
  document.getElementById('messages-export-csv')?.addEventListener('click', exportMessagesCSV);
  const snifferToggle = document.getElementById('sniffer-toggle');
  const snifferPanel  = document.getElementById('sniffer-panel');
  if (snifferToggle && snifferPanel) snifferToggle.addEventListener('click', () => snifferPanel.classList.toggle('hidden'));

  // Waypoint panel — open/close and form submission.
  const waypointPanel = document.getElementById('waypoint-panel');
  const waypointBtn   = document.getElementById('map-add-waypoint-btn');
  const waypointClose = document.getElementById('waypoint-panel-close');
  const waypointForm  = document.getElementById('waypoint-form');

  if (waypointBtn && waypointPanel) {
    waypointBtn.addEventListener('click', () => waypointPanel.classList.toggle('hidden'));
  }
  if (waypointClose && waypointPanel) {
    waypointClose.addEventListener('click', () => waypointPanel.classList.add('hidden'));
  }

  // Allow clicking the map to auto-fill Lat/Lng in the waypoint form.
  // We hook into the Leaflet map's click event via a custom listener so we
  // don't break any other map interactions.
  fullMap._map?.on('click', (e) => {
    const latEl = document.getElementById('wp-lat');
    const lngEl = document.getElementById('wp-lng');
    if (latEl && lngEl && waypointPanel && !waypointPanel.classList.contains('hidden')) {
      latEl.value = e.latlng.lat.toFixed(6);
      lngEl.value = e.latlng.lng.toFixed(6);
    }
  });

  if (waypointForm) {
    waypointForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = document.getElementById('wp-name')?.value?.trim();
      const desc = document.getElementById('wp-desc')?.value?.trim();
      const icon = document.getElementById('wp-icon')?.value?.trim() || '📍';
      let lat  = parseFloat(document.getElementById('wp-lat')?.value);
      let lng  = parseFloat(document.getElementById('wp-lng')?.value);
      if (!name) return;
      if (isNaN(lat) || isNaN(lng)) {
        const center = fullMap._map?.getCenter();
        if (center) {
          lat = center.lat;
          lng = center.lng;
        } else {
          lat = 0; lng = 0;
        }
      }
      try {
        await addWaypoint({ name, description: desc, icon, lat, lng });
        waypointForm.reset();
        document.getElementById('wp-icon').value = '📍';
        waypointPanel.classList.add('hidden');
        const wps = await fetchWaypoints();
        fullMap.updateWaypoints(wps, handleDeleteWaypoint, handleUpdateWaypoint);
      } catch (err) {
        console.error('Failed to add waypoint:', err);
      }
    });
  }

  // Expose a global so the popup delete button can call back.
  window._nodepulse_deleteWaypoint = handleDeleteWaypoint;

  // Ruler toggle and panel.
  const rulerBtn = document.getElementById('map-ruler-btn');
  const rulerPanel = document.getElementById('ruler-panel');
  const rulerClose = document.getElementById('ruler-panel-close');
  const rulerClear = document.getElementById('ruler-clear-btn');

  if (rulerBtn && rulerPanel) {
    rulerBtn.addEventListener('click', () => {
      const wasActive = rulerBtn.classList.contains('active');
      if (wasActive) {
        rulerBtn.classList.remove('active');
        rulerPanel.classList.add('hidden');
        fullMap.disableRuler();
      } else {
        rulerBtn.classList.add('active');
        rulerPanel.classList.remove('hidden');
        fullMap.enableRuler(state.posHistory || {});
      }
    });
  }
  if (rulerClose && rulerPanel) {
    rulerClose.addEventListener('click', () => {
      rulerBtn?.classList.remove('active');
      rulerPanel.classList.add('hidden');
      fullMap.disableRuler();
    });
  }
  if (rulerClear) {
    rulerClear.addEventListener('click', () => {
      fullMap.clearRuler();
    });
  }

  // Header sort on click, filter via icon button.
  const pktThead = document.querySelector('#view-packets thead');
  if (pktThead) {
    pktThead.addEventListener('click', e => {
      const th = e.target.closest('.pkt-th-sortable');
      if (th && !e.target.closest('.pkt-th-filter-btn')) {
        const col = th.dataset.col;
        if (!state.packetSort || state.packetSort.col !== col) {
          state.packetSort = { col, dir: 'asc' };
        } else if (state.packetSort.dir === 'asc') {
          state.packetSort.dir = 'desc';
        } else {
          state.packetSort = null;
        }
        renderPacketTable();
      }
    });
    pktThead.addEventListener('click', e => {
      const btn = e.target.closest('.pkt-th-filter-btn');
      if (btn) {
        const col = btn.dataset.col;
        const th = btn.closest('th');
        showPacketFilterDropdown(th, col);
      }
    });
  }

  // Close dropdown on outside click.
  document.addEventListener('click', e => {
    if (!e.target.closest('.pkt-filter-dropdown') && !e.target.closest('.pkt-th-filter-btn')) {
      const dd = document.getElementById('pkt-filter-dropdown');
      if (dd) dd.remove();
    }
  });

  switchView('dashboard');

  // Initial data load — show a spinner state while waiting.
  document.getElementById('node-list').innerHTML = `
    <li class="list-placeholder"><div class="spinner"></div>Loading nodes…</li>`;

  await pollData();                          // first immediate fetch
  selectConversation(state.activeConversation); // initialise message panel
  // NOTE: we intentionally do NOT auto-fit to markers on first load so the map
  // stays centred on its default view (Durban, South Africa). Users can still
  // pan/zoom, and the fitToMarkers() helper remains available if needed.
  // The recursive pollData setTimeout handles subsequent polling.
}

// ============================================================================
// Map Node Filter (text / max hops / last-heard window)
// ============================================================================

/**
 * Wire the Map view filter controls to both map instances. Any change applies
 * the filter immediately and updates the "N shown" counter. "Cached only"
 * (staleOnly) and the heard-within window are mutually exclusive sources of
 * the `staleOnly` flag, so selecting one resets the other.
 */
function wireMapFilters() {
  const textEl   = document.getElementById('map-filter-text');
  const hopsEl   = document.getElementById('map-filter-hops');
  const heardEl  = document.getElementById('map-filter-heard');
  const countEl  = document.getElementById('map-filter-count');
  const heatEl   = document.getElementById('map-toggle-heatmap');
  if (!textEl || !hopsEl || !heardEl || !countEl) return;

  const apply = () => {
    const heardVal = heardEl.value;
    const patch = {
      text: textEl.value,
      maxHops: hopsEl.value === '' ? null : parseInt(hopsEl.value, 10),
      // "stale" option => staleOnly; numeric => heardWithin seconds; "" => clear both.
      heardWithin: heardVal === '' || heardVal === 'stale' ? null : parseInt(heardVal, 10),
      staleOnly: heardVal === 'stale',
    };
    const shown = dashMap.setFilter(patch);
    fullMap.setFilter(patch);
    countEl.textContent = `${shown} shown`;
  };

  textEl.addEventListener('input', apply);
  hopsEl.addEventListener('change', apply);
  heardEl.addEventListener('change', apply);
  if (heatEl) {
    heatEl.addEventListener('change', () => {
      dashMap.toggleHeatmap(heatEl.checked);
      fullMap.toggleHeatmap(heatEl.checked);
      // Keep the map control-bar button's active state in sync.
      document.querySelectorAll('.leaflet-control-maptoggle').forEach(b => {
        if (b.title && b.title.toLowerCase().includes('heatmap')) {
          b.classList.toggle('active', heatEl.checked);
        }
      });
    });
  }
  // Keep the counter in sync on every poll (node set changes underneath filter).
  const origUpdateNodes = fullMap.updateNodes.bind(fullMap);
  fullMap.updateNodes = (nodes) => { origUpdateNodes(nodes); countEl.textContent = `${fullMap._filterNodes(fullMap._allNodes).length} shown`; };

  // Export buttons — use the nodes from state and re-apply filter logic.
  document.querySelectorAll('.map-export-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const fmt = btn.dataset.export; // 'kml' or 'gpx'
      // Get the currently visible nodes from the full map's filtered result.
      const visible = fullMap._filterNodes ? fullMap._filterNodes(fullMap._allNodes) : state.nodes;
      const withGps = visible.filter(n => n.latitude != null && n.longitude != null);
      if (withGps.length === 0) {
        showToast('No nodes with GPS fix to export', 'error');
        return;
      }
      let content, filename;
      if (fmt === 'kml') {
        content = buildKml(withGps, state.selfId);
        filename = `nodepulse_nodes_${Date.now()}.kml`;
      } else {
        content = buildGpx(withGps, state.selfId);
        filename = `nodepulse_nodes_${Date.now()}.gpx`;
      }
      downloadFile(content, filename);
      showToast(`Exported ${withGps.length} nodes as ${fmt.toUpperCase()}`, 'success');
    });
  });
}

// ============================================================================
// Utility: HTML escape is imported from ./util.js (shared with map.js).
// ============================================================================

// Start the app when the DOM is ready.
// ============================================================================
// Packet Inspector & LoRa Sniffer (Features 4 & 5)
// ============================================================================

const PORTNUM_COLORS = {
  TEXT_MESSAGE_APP: '#00e5ff', TELEMETRY_APP: '#69f0ae', POSITION_APP: '#ffeb3b',
  NODEINFO_APP: '#ce93d8',     NEIGHBORINFO_APP: '#80cbc4', TRACEROUTE_APP: '#ff8a65',
  ROUTING_APP: '#90caf9',      ADMIN_APP: '#ef9a9a',       UNKNOWN: '#9e9e9e',
};
function _portnumColor(p) { return PORTNUM_COLORS[p] || '#9e9e9e'; }
function _fmtPacketTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
}

function showPacketFilterDropdown(th, col) {
  const existing = document.getElementById('pkt-filter-dropdown');
  if (existing) existing.remove();

  const rect = th.getBoundingClientRect();
  const tableRect = th.closest('table').getBoundingClientRect();
  const dd = document.createElement('div');
  dd.id = 'pkt-filter-dropdown';
  dd.className = 'pkt-filter-dropdown';
  dd.style.top = (rect.bottom - tableRect.top) + 'px';
  dd.style.left = Math.max(0, rect.left - tableRect.left) + 'px';

  const activeVal = state.packetFilters[col];
  const values = new Set();
  for (const p of state.packetLog) {
    let v = p[col];
    if (v == null) v = '';
    values.add(String(v));
  }
  const sorted = [...values].sort((a, b) => a.localeCompare(b));

  // "All" option to clear filter
  const all = document.createElement('div');
  all.className = 'pkt-dd-item' + (!activeVal ? ' active' : '');
  all.textContent = '(All)';
  all.addEventListener('click', e => { e.stopPropagation(); delete state.packetFilters[col]; renderPacketTable(); dd.remove(); });
  dd.appendChild(all);

  for (const v of sorted) {
    const item = document.createElement('div');
    const display = col === 'from_id' || col === 'to_id' ? v : v || '(empty)';
    item.className = 'pkt-dd-item' + (activeVal === v ? ' active' : '');
    item.textContent = display;
    item.addEventListener('click', e => {
      e.stopPropagation();
      if (state.packetFilters[col] === v) {
        delete state.packetFilters[col];
      } else {
        state.packetFilters[col] = v;
      }
      renderPacketTable();
      dd.remove();
    });
    dd.appendChild(item);
  }

  th.closest('table').parentElement.appendChild(dd);
}

function renderPacketTable() {
  const tbody = document.getElementById('packet-table-body');
  if (!tbody) return;
  const pf = state.packetFilters;
  let packets = state.packetLog.filter(p => {
    for (const [col, val] of Object.entries(pf)) {
      const pv = String(p[col] ?? '');
      if (pv !== val) return false;
    }
    return true;
  });

  // Apply sort.
  if (state.packetSort) {
    const { col, dir } = state.packetSort;
    packets = [...packets].sort((a, b) => {
      let va = a[col], vb = b[col];
      if (va == null) va = '';
      if (vb == null) vb = '';
      if (typeof va === 'number' && typeof vb === 'number') {
        return dir === 'asc' ? va - vb : vb - va;
      }
      va = String(va);
      vb = String(vb);
      const cmp = va.localeCompare(vb);
      return dir === 'asc' ? cmp : -cmp;
    });
  }
  tbody.innerHTML = '';
  for (const pkt of packets) {
    const color = _portnumColor(pkt.portnum);
    const tr = document.createElement('tr');
    tr.className = 'packet-row';
    tr.innerHTML = `
      <td class="packet-time">${_fmtPacketTime(pkt.timestamp)}</td>
      <td><span class="portnum-badge" style="color:${color}">${escapeHtml(pkt.portnum||'UNKNOWN')}</span></td>
      <td class="mono pkt-id">${escapeHtml(pkt.from_id||'—')}${shortNameFor(pkt.from_id) ? ' <span class="pkt-name">'+escapeHtml(shortNameFor(pkt.from_id))+'</span>' : ''}</td>
      <td class="mono pkt-id">${escapeHtml(pkt.to_id||'—')}${shortNameFor(pkt.to_id) ? ' <span class="pkt-name">'+escapeHtml(shortNameFor(pkt.to_id))+'</span>' : ''}</td>
      <td>${pkt.channel??'—'}</td>
      <td>${pkt.rx_snr!=null?pkt.rx_snr.toFixed(1):'—'}</td>
      <td>${pkt.hop_limit!=null?`${pkt.hop_limit}/${pkt.hop_start??'?'}`:'—'}</td>
      <td>${pkt.want_ack?'✓':''}</td>`;
    const detail = document.createElement('tr');
    detail.className = 'packet-detail hidden';
    const pre = document.createElement('pre'); pre.className = 'json-dump';
    try { pre.textContent = JSON.stringify(pkt.decoded, null, 2); } catch { pre.textContent = '(unparseable)'; }
    const dtd = document.createElement('td'); dtd.colSpan = 8; dtd.appendChild(pre);
    detail.appendChild(dtd);
    tr.addEventListener('click', () => detail.classList.toggle('hidden'));
    tbody.appendChild(tr); tbody.appendChild(detail);
  }
  if (!packets.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="8" style="text-align:center;color:var(--text-muted);padding:24px">${state.packetLog.length===0?'No packets captured yet — waiting for mesh traffic.':'No packets match the current filter.'}</td>`;
    tbody.appendChild(tr);
  }

  // Update header indicators — sort arrow and active filter dot.
  document.querySelectorAll('.pkt-th-sortable').forEach(th => {
    const col = th.dataset.col;
    const arrow = th.querySelector('.pkt-th-arrow');
    if (arrow) {
      if (state.packetSort && state.packetSort.col === col) {
        arrow.textContent = state.packetSort.dir === 'asc' ? ' ▲' : ' ▼';
      } else {
        arrow.textContent = '';
      }
    }
    const btn = th.querySelector('.pkt-th-filter-btn');
    if (btn) btn.classList.toggle('active', !!state.packetFilters[col]);
  });
}

function renderSnifferStats(stats) {
  if (!stats) return;
  const sid = id => document.getElementById(id);
  if (sid('sniffer-ppm'))   sid('sniffer-ppm').textContent   = stats.packets_per_minute ?? 0;
  if (sid('sniffer-nodes')) sid('sniffer-nodes').textContent = stats.unique_nodes       ?? 0;
  if (sid('sniffer-total')) sid('sniffer-total').textContent = stats.total_captured     ?? 0;
  const dist = stats.portnum_distribution || {};
  const bars = sid('sniffer-dist-bars');
  if (!bars) return;
  const total = Object.values(dist).reduce((a,b)=>a+b,0)||1;
  const sorted = Object.entries(dist).sort((a,b)=>b[1]-a[1]).slice(0,8);
  bars.innerHTML = sorted.map(([portnum,count]) => {
    const pct = Math.round((count/total)*100);
    const color = _portnumColor(portnum);
    return `<div class="sniffer-bar-row">
      <span class="sniffer-portnum" style="color:${color}">${escapeHtml(portnum)}</span>
      <div class="sniffer-bar-track"><div class="sniffer-bar-fill" style="width:${pct}%;background:${color}40;border-right:2px solid ${color}"></div></div>
      <span class="sniffer-pct">${pct}%</span>
    </div>`;
  }).join('');
}

function exportPacketsJSON() {
  const blob = new Blob([JSON.stringify(state.packetLog,null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download='nodepulse_packets.json'; a.click(); URL.revokeObjectURL(url);
}

function exportPacketsCSV() {
  const cols = ['timestamp','from_id','to_id','portnum','channel','rx_snr','rx_rssi','hop_limit','want_ack','via_mqtt','decoded_ok'];
  const rows = state.packetLog.map(p=>cols.map(c=>JSON.stringify(p[c]??'')).join(','));
  const csv = [cols.join(','),...rows].join('\n');
  const blob = new Blob([csv],{type:'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download='nodepulse_packets.csv'; a.click(); URL.revokeObjectURL(url);
}

function exportMessagesJSON() {
  const conv = state.activeConversation || 'ch:0';
  const url = `./api/messages/export?format=json&conversation=${encodeURIComponent(conv)}`;
  const a = document.createElement('a'); a.href = url; a.click();
}

function exportMessagesCSV() {
  const conv = state.activeConversation || 'ch:0';
  const url = `./api/messages/export?format=csv&conversation=${encodeURIComponent(conv)}`;
  const a = document.createElement('a'); a.href = url; a.click();
}

async function pollPackets() {
  if (state.currentView !== 'packets') return;
  try {
    const [pkts, stats] = await Promise.all([fetchPackets(200), fetchSnifferStats()]);
    state.packetLog    = pkts   || [];
    state.snifferStats = stats  || null;
    renderPacketTable();
    renderSnifferStats(state.snifferStats);
  } catch (err) { console.warn('Packet poll failed:', err); }
}

/**
 * Delete a waypoint by ID, then refresh the map layer.
 */
async function handleDeleteWaypoint(waypointId) {
  try {
    await deleteWaypoint(waypointId);
    const wps = await fetchWaypoints();
    fullMap.updateWaypoints(wps, handleDeleteWaypoint, handleUpdateWaypoint);
  } catch (err) {
    console.error('Failed to delete waypoint:', err);
  }
}

/**
 * Update a waypoint's position after a drag, then refresh the map layer.
 */
async function handleUpdateWaypoint(waypointId, lat, lng) {
  try {
    await updateWaypoint(waypointId, { lat, lng });
    const wps = await fetchWaypoints();
    fullMap.updateWaypoints(wps, handleDeleteWaypoint, handleUpdateWaypoint);
  } catch (err) {
    console.error('Failed to update waypoint:', err);
  }
}

document.addEventListener('DOMContentLoaded', init);
