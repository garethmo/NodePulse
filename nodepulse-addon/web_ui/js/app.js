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

import { fetchStatus, fetchNodes, fetchChannels, fetchMessages, sendMessage, requestTraceRoute, requestPosition, fetchTrackedNodes, trackNode } from './api.js';
import { MapManager } from './map.js';
import { ChartManager } from './charts.js';
import { escapeHtml, haversineKm, formatDistance } from './util.js';

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
  activeConversation: 'ch:0', // currently-open thread (ch:<n> or dm:<nodeId>)
  conversations:  {},       // key -> { key, name, kind, unread }
  messagesByConv: {},       // key -> [message objects], persisted across polls
  channels:       [],       // configured mesh channels from the node
  // Counter incremented on each fast poll cycle. Used to throttle the slow
  // tracked-nodes refresh so it does not run on every 15s tick.
  _pollCount:     0,
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

  // Sort by distance from the self node (nearest first); falls back to
  // most-recently-heard when GPS data is unavailable.
  const sorted = sortByDistance(nodes);

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
}

// ============================================================================
// Rendering: Node Grid (Nodes view)
// ============================================================================
function renderNodesGrid(nodes) {
  const grid = document.getElementById('nodes-grid');

  // Fast check: compute a fingerprint of the current state that affects the grid.
  // We include trackedNodes size and node IDs + last_heard + snr + distance (coords).
  const fingerprint = nodes.map(n => `${n.id}:${n.last_heard}:${n.snr}:${n.latitude}:${n.longitude}:${n.traceroute?.timestamp}`).join('|') + '|' + state.trackedNodes.size + '|' + state.nodeFilter;
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
  const filtered = q
    ? nodes.filter(n =>
        (n.long_name || '').toLowerCase().includes(q) ||
        (n.short_name || '').toLowerCase().includes(q) ||
        (n.hw_model || '').toLowerCase().includes(q) ||
        (n.id || '').toLowerCase().includes(q))
    : nodes;

  if (filtered.length === 0) {
    grid.innerHTML = `<div class="list-placeholder">No nodes match "${escapeHtml(state.nodeFilter)}".</div>`;
    return;
  }

  // Sort the filtered nodes by distance from the self node (nearest first).
  const sorted = sortByDistance(filtered);

  // Resolve the self node's coordinates once so we can compute per-node
  // distance (MeshSense-style "distance from your node").
  const selfNode = state.nodes.find(n => n.id === state.selfId);
  const selfLat  = selfNode?.latitude;
  const selfLon  = selfNode?.longitude;
  const selfHasGps = selfLat != null && selfLon != null;

  for (const node of sorted) {
    const card = document.createElement('div');
    card.className = 'node-card';

    const snrText   = node.snr         != null ? `${node.snr.toFixed(1)} dB` : 'N/A';
    const rssiText  = node.rssi        != null ? `${node.rssi} dBm`          : 'Not provided';
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
      ${tracerouteHtml}
      <div class="node-card-actions">
        <button class="action-btn" data-action="traceroute" data-node="${escapeHtml(node.id)}">Traceroute</button>
        <button class="action-btn" data-action="position"   data-node="${escapeHtml(node.id)}">Req. Position</button>
        <button class="action-btn" data-action="message"    data-node="${escapeHtml(node.id)}">Message</button>
        <label class="node-track-toggle" title="Create Home Assistant entities for this node">
          <input type="checkbox" data-action="track" data-node="${escapeHtml(node.id)}" ${state.trackedNodes.has(node.id) ? 'checked' : ''} />
          <span>Track in HA</span>
        </label>
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
  renderMessageList();
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
function storeMessage(msg) {
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
    Math.abs((m.timestamp || 0) - (msg.timestamp || now)) < THREE_SECONDS
  )) return;
  thread.push(msg);

  const conv = _ensureConversation(key);
  // Mark unread only if it arrived in a non-active conversation and isn't ours.
  if (key !== state.activeConversation && !msg.outgoing) {
    conv.unread = (conv.unread || 0) + 1;
  }
}

function renderMessageList() {
  const list = document.getElementById('message-list');
  if (!list) return;
  list.innerHTML = '';

  const thread = state.messagesByConv[state.activeConversation] || [];

  if (thread.length === 0) {
    list.innerHTML = `<div class="message-empty">No messages yet in this conversation.</div>`;
    return;
  }

  for (const msg of thread) {
    const bubble = document.createElement('div');
    const type = msg.outgoing ? 'outgoing' : 'incoming';
    bubble.className = `message-bubble ${type}`;
    const time = new Date((msg.timestamp || Date.now() / 1000) * 1000)
      .toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

    // Delivery status indicator for outgoing messages.
    let statusHtml = '';
    if (msg.outgoing) {
      if (msg.status === 'sending') {
        statusHtml = `<span class="msg-status sending" title="Sending…">🕓</span>`;
      } else if (msg.status === 'sent') {
        statusHtml = `<span class="msg-status sent" title="Delivered to node">✓</span>`;
      } else if (msg.status === 'failed') {
        statusHtml = `<span class="msg-status failed" title="Send failed — click to retry">⚠ Failed</span>`;
        bubble.classList.add('failed');
      }
    }

    // Channel indicator - show channel name for DMs to indicate which channel was used
    let channelHtml = '';
    const conv = conversationForKey(state.activeConversation);
    if (conv.kind === 'dm' && msg.channel != null) {
      const ch = parseInt(msg.channel, 10) || 0;
      const cfg = (state.channels || []).find(c => c && c.index === ch);
      const chName = cfg && cfg.name ? cfg.name : (ch === 0 ? 'Primary' : `Ch ${ch}`);
      channelHtml = `<span class="message-channel" title="Sent on channel ${ch}">${escapeHtml(chName)}</span>`;
    }

    const sender = msg.outgoing
      ? 'Me'
      : (shortNameFor(msg.from_id) || msg.from_name || nodeName(msg.from_id) || 'Unknown');
    bubble.innerHTML = `
      ${msg.outgoing ? '' : `<div class="message-sender">${escapeHtml(sender)}</div>`}
      <div class="message-text">${escapeHtml(msg.text)}</div>
      <div class="message-meta">
        <span class="message-time">${time}</span>${channelHtml}${statusHtml}
      </div>`;

    // Click a failed message to retry sending it.
    if (msg.outgoing && msg.status === 'failed') {
      bubble.style.cursor = 'pointer';
      bubble.addEventListener('click', () => retryMessage(msg));
    }
    list.appendChild(bubble);
  }
  list.scrollTop = list.scrollHeight;
}

// Retry a previously-failed outgoing message.
async function retryMessage(msg) {
  msg.status = 'sending';
  if (state.activeConversation === msg.conversation) renderMessageList();
  try {
    await sendMessage(msg.text, msg.destination ?? null, msg.channel ?? 0);
    msg.status = 'sent';
  } catch (err) {
    msg.status = 'failed';
    showToast(`Send failed: ${err.message}`, 'error');
  }
  if (state.activeConversation === msg.conversation) renderMessageList();
}

// Switch the active conversation to a node's DM thread (used when the user
// clicks "Message" on a node card or a node in the list).
function openDirectMessage(nodeId) {
  const key = `dm:${nodeId}`;
  selectConversation(key);
  if (state.currentView !== 'dashboard') switchView('dashboard');
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

  // Optimistically render the outgoing message in the active thread.
  const optimistic = {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    text,
    outgoing: true,
    conversation: state.activeConversation,
    timestamp: Date.now() / 1000,
    from_name: 'Me',
    status: 'sending', // sending -> sent | failed
    destination,
    channel,
  };
  storeMessage(optimistic);
  renderMessageList();
  input.value = '';
  _autoSizeInput(input);

  try {
    await sendMessage(text, destination, channel);
    optimistic.status = 'sent';
  } catch (err) {
    optimistic.status = 'failed';
    showToast(`Send failed: ${err.message}`, 'error');
  }
  // Re-render so the status indicator (tick / cross) updates.
  if (state.activeConversation === optimistic.conversation) {
    renderMessageList();
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
  } else if (viewName === 'settings') {
    renderSettings();
  }
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

    // Schedule & Logging
    _setEl('settings-scan-interval', cfg.scan_interval != null ? `${cfg.scan_interval} s` : '—');
    _setEl('settings-log-level',     cfg.log_level || '—');

  } catch (err) {
    // Surface the failure instead of hiding it behind "—" placeholders so the
    // cause (e.g. an unreachable addon API under ingress) is visible.
    const msg = (err && err.message) ? err.message : String(err);
    _setEl('settings-conn', `⚠ Error: ${msg}`);
    console.error('renderSettings failed:', err);
  }
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

  if (channelsResult.status === 'fulfilled') {
    const chans = Array.isArray(channelsResult.value) ? channelsResult.value : [];
    state.channels = chans;
    renderChannelSelect();
    // Channel tabs must re-render once the channel list is known so they
    // appear immediately (not only after a message arrives on each channel).
    renderConversationTabs();
  } else {
    console.warn('Channels fetch failed:', channelsResult.reason);
  }

  // Slow path: relay to HA for tracked-node state. Run only on the first poll
  // (so the checkboxes on the Nodes tab render correctly on initial load) and
  // then every TRACKED_NODES_POLL_EVERY_N cycles thereafter. This avoids
  // blocking the fast critical render on every tick.
  const isFirstPoll = state._pollCount === 1;
  const isDue = state._pollCount % TRACKED_NODES_POLL_EVERY_N === 0;
  if (isFirstPoll || isDue) {
    // Fire-and-forget: do NOT await — let it run concurrently so the rest of
    // the UI is already rendered before this potentially-slow call finishes.
    refreshTrackedNodes();
  }

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
  for (const msg of messages) {
    if (!msg.id || state.seenMessageIds.has(msg.id)) continue;
    state.seenMessageIds.add(msg.id);
    storeMessage(msg);
    changed = true;
  }
  if (changed) {
    renderConversationTabs();
    // Only repaint the list if we're looking at the affected (or a) thread.
    renderMessageList();
  }
  // Always refresh the tab bar so channel/DM tabs reflect the latest known
  // conversations and channel list (e.g. after channels load post-messages).
  renderConversationTabs();
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

  // Wire up the send button and Enter key shortcut in the message input.
  document.getElementById('send-btn').addEventListener('click', handleSend);
  const msgInput = document.getElementById('message-input');
  msgInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });
  msgInput.addEventListener('input', () => _autoSizeInput(msgInput));

  // Channel selector: switching it jumps to that channel's conversation thread.
  const channelSelect = document.getElementById('channel-select');
  if (channelSelect) {
    channelSelect.addEventListener('change', () => {
      const ch = parseInt(channelSelect.value, 10) || 0;
      selectConversation(`ch:${ch}`);
    });
  }

  // Event delegation on the nodes grid — attached once here so it is NOT
  // re-added on every 15s poll inside renderNodesGrid().
  document.getElementById('nodes-grid').addEventListener('click', handleNodeCardAction);

  // Nodes-tab filter: re-render the grid from the current cached node list
  // without waiting for the next poll.
  const nodeFilter = document.getElementById('node-filter');
  if (nodeFilter) {
    nodeFilter.addEventListener('input', (e) => {
      state.nodeFilter = e.target.value;
      renderNodesGrid(state.nodes);
    });
  }

  // Map overlay toggles: control buttons on each Leaflet map dispatch custom
  // events; the "L"/"T"/"N" keys are keyboard shortcuts for the same actions.
  const wireToggle = (eventName, key, toggleFn, label) => {
    const handler = () => {
      const dash = toggleFn(dashMap);
      toggleFn(fullMap);
      showToast(`${label} ${dash ? 'shown' : 'hidden'}`, 'info', 1500);
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

  // Set the initial active view.
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
// Utility: HTML escape is imported from ./util.js (shared with map.js).
// ============================================================================

// Start the app when the DOM is ready.
document.addEventListener('DOMContentLoaded', init);
