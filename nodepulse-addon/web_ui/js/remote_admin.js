/**
 * NodePulse Web UI — Remote Node Administration View
 *
 * Administers OTHER mesh nodes over the Meshtastic AdminModule:
 *   - pick any visible node and read its full config
 *   - edit config sections (reusing device_config.js's schema-driven cards)
 *   - run actions: reboot, shutdown, factory reset (config / full device),
 *     NodeDB reset, fixed position (set/clear), clock sync, evict node
 *
 * Requires the connected gateway to have admin capability — Security → Admin
 * Keys on the radio, an ADMIN channel shared with the target, or admin channel
 * enabled. The backend enforces a bounded timeout on every round-trip so a
 * dead node can never hang the UI.
 *
 * Registration: renderRemoteAdmin() is attached to window so app.js can call
 * it from switchView() without a circular import.
 */

import { fetchNodes, fetchAdminAvailable, fetchRemoteConfig, fetchRemoteConfigSection, saveRemoteConfig, remoteAdminAction } from './api.js';
import { renderDeviceConfigSections, SECTION_META, SECTION_ORDER } from './device_config.js';
import { escapeHtml } from './util.js';

const ACTIONS = {
  reboot:              { label: 'Reboot node',       danger: false, confirm: false },
  shutdown:            { label: 'Shut down node',    danger: false, confirm: true },
  factory_reset:       { label: 'Factory reset config', danger: true,  confirm: true },
  factory_reset_device:{ label: 'Factory reset full device', danger: true, confirm: true },
  nodedb_reset:        { label: 'Reset NodeDB',      danger: true,  confirm: true },
  set_fixed_position:  { label: 'Set fixed position',danger: false, confirm: false },
  clear_fixed_position:{ label: 'Clear fixed position', danger: false, confirm: false },
  set_time:            { label: 'Sync clock',        danger: false, confirm: false },
  remove_node:         { label: 'Remove node from NodeDB', danger: true, confirm: true },
};

let _currentNodeId = null;       // Selected remote node ("!hex")
let _capability = null;          // Last /admin/available response
// Cache fetched config per node so switching back doesn't re-fetch over radio.
const _configCache = new Map();

// ---------------------------------------------------------------------------
// Public entry point (called by app.js switchView)
// ---------------------------------------------------------------------------

async function renderRemoteAdmin() {
  const content = document.getElementById('admin-content');
  if (!content) return;

  _showLoading(content);

  try {
    const [available, nodes] = await Promise.all([fetchAdminAvailable(), fetchNodes()]);
    _capability = available;

    const selfId = window.getSelfNodeId ? window.getSelfNodeId() : null;
    const remoteNodes = (nodes || []).filter(n => n && n.id && n.id !== selfId);

    // Sort priority:
    // 1. Nodes with a remote configuration (cached on backend or locally)
    // 2. Live nodes (stale = false)
    // 3. Most recently heard (last_heard desc)
    remoteNodes.sort((a, b) => {
      const aConf = _configCache.has(a.id) || a.has_remote_config;
      const bConf = _configCache.has(b.id) || b.has_remote_config;
      if (aConf !== bConf) return aConf ? -1 : 1;

      const aStale = a.stale === true;
      const bStale = b.stale === true;
      if (aStale !== bStale) return aStale ? 1 : -1;

      const aTime = a.last_heard || 0;
      const bTime = b.last_heard || 0;
      return bTime - aTime;
    });

    // Preserve the previously selected node across view switches if still present.
    if (_currentNodeId && !remoteNodes.some(n => n.id === _currentNodeId)) {
      _currentNodeId = null;
    }

    // Clear the loading spinner before appending chrome elements.
    // _showLoading() set content.innerHTML; leaving it would cause the spinner
    // to persist underneath every subsequent appendChild call.
    content.innerHTML = '';

    _renderChrome(content, available, remoteNodes);
    if (remoteNodes.length === 0) {
      _renderNoNodes(content);
      return;
    }
    if (!_currentNodeId) _currentNodeId = remoteNodes[0].id;
    _renderGatewayKeys(content, available);
    _renderNodeSelector(content, remoteNodes);

    if (!available.available) {
      // No admin capability: attempting to read a remote config would block
      // until the backend timeout. Show an explanatory notice instead.
      _renderNoAdminCapability(content);
      return;
    }

    // If this node's config is already cached, render it immediately.
    // Otherwise show a prompt — don't auto-fetch over radio on tab open,
    // since a single unreachable node would block for the full 25 s timeout.
    if (_configCache.has(_currentNodeId)) {
      _renderCachedConfig(content, _currentNodeId);
    } else {
      _renderLoadPrompt(content);
    }
  } catch (err) {
    _showError(content, err.message);
  }
}

function _renderNoAdminCapability(content) {
  const card = document.createElement('div');
  card.className = 'cfg-error-card';
  card.innerHTML = `
    <span class="cfg-error-icon">🔐</span>
    <div>
      <div class="cfg-error-title">No admin capability configured</div>
      <div class="cfg-error-msg">This gateway cannot administer other nodes. Configure it either via <b>Security → Admin Keys</b> on the radio (add this node's key / enable admin), or add a channel named <code>admin</code> (with a shared PSK) to both this gateway and the target node, then refresh.</div>
    </div>`;
  content.appendChild(card);
}

function _renderChrome(content, available, remoteNodes) {
  const banner = document.getElementById('admin-capability');
  if (!banner) return;
  if (!available.available) {
    banner.textContent = `⚠️ No admin capability on this gateway — remote administration requires Security → Admin Keys, or a channel named "admin" (with a shared PSK on both nodes).`;
    banner.classList.remove('hidden');
    banner.classList.add('admin-capability-warn');
  } else {
    let via;
    if (available.admin_channel_enabled) {
      via = available.admin_channel_index != null
        ? `the legacy admin channel (index ${available.admin_channel_index})`
        : 'the legacy admin channel (primary)';
    } else if (available.admin_key_count > 0) {
      via = `Security admin keys (${available.admin_key_count} configured) over the primary channel`;
    } else if (available.has_keypair) {
      via = 'PKC (Security keypair) over the primary channel';
    } else {
      via = 'the primary channel';
    }
    banner.textContent = `Remote admin available via ${via} — ${remoteNodes.length} node(s) selectable.`;
    banner.classList.remove('hidden');
    banner.classList.remove('admin-capability-warn');
    banner.classList.add('admin-capability-ok');
  }
}

function _renderNoNodes(content) {
  content.innerHTML = `
    <div class="cfg-error-card">
      <span class="cfg-error-icon">📡</span>
      <div>
        <div class="cfg-error-title">No remote nodes available</div>
        <div class="cfg-error-msg">No mesh nodes besides this gateway are currently visible. Wait for a node to be heard, then refresh.</div>
      </div>
    </div>`;
}

function _renderGatewayKeys(content, available) {
  const keys = [];
  if (available.public_key) keys.push({ label: 'This gateway public key', value: available.public_key });
  (available.admin_keys || []).forEach((k, i) => {
    keys.push({ label: `This gateway admin key ${i + 1}`, value: k });
  });
  if (keys.length === 0) return;

  const card = document.createElement('div');
  card.className = 'admin-gwkeys';
  const title = document.createElement('div');
  title.className = 'admin-gwkeys-title';
  title.textContent = '🔑 This gateway — keys for targets';
  card.appendChild(title);
  const hint = document.createElement('div');
  hint.className = 'admin-gwkeys-hint';
  hint.textContent = 'Add one of these to a target node\'s Security → Admin Keys so this gateway may administer it.';
  card.appendChild(hint);
  keys.forEach((k) => {
    const row = document.createElement('div');
    row.className = 'admin-gwkeys-row';
    const label = document.createElement('span');
    label.className = 'admin-gwkeys-label';
    label.textContent = k.label;
    const code = document.createElement('code');
    code.className = 'admin-gwkeys-code';
    code.textContent = k.value;
    code.title = k.value;
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'cfg-key-copy';
    copy.textContent = 'Copy';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(k.value);
        _copied(copy);
      } catch {
        const ta = document.createElement('textarea');
        ta.value = k.value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch { /* ignore */ }
        ta.remove();
        _copied(copy);
      }
    });
    row.appendChild(label);
    row.appendChild(code);
    row.appendChild(copy);
    card.appendChild(row);
  });
  content.appendChild(card);
}

function _copied(btn) {
  const original = btn.textContent;
  btn.textContent = 'Copied ✓';
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = original;
    btn.disabled = false;
  }, 1500);
}

function _renderNodeSelector(content, nodes) {
  const options = nodes.map(n =>
    `<option value="${escapeHtml(n.id)}">${escapeHtml(n.long_name || n.short_name || n.id)} (${escapeHtml(n.id)})</option>`
  ).join('');

  const picker = document.createElement('div');
  picker.className = 'admin-picker';
  picker.innerHTML = `
    <label class="admin-picker-label" for="admin-node-select">Target node</label>
    <select id="admin-node-select" class="admin-node-select">${options}</select>
    <span class="admin-picker-hint">Select a node, then click "Load Configuration" to read its config over the radio.</span>
  `;
  content.appendChild(picker);

  const select = picker.querySelector('#admin-node-select');
  select.value = _currentNodeId;
  select.addEventListener('change', () => {
    _currentNodeId = select.value;
    // When the user picks a different node, show cached config if available,
    // otherwise show the load prompt (don't auto-fetch — it's a slow radio op).
    if (_configCache.has(_currentNodeId)) {
      _renderCachedConfig(content, _currentNodeId);
    } else {
      _renderLoadPrompt(content);
    }
  });
}

/**
 * Show a prompt card with a "Load Configuration" button.
 * Config is only fetched over the radio when the user explicitly requests it,
 * preventing the 25 s timeout from firing on unreachable nodes automatically.
 */
function _renderLoadPrompt(content) {
  // Remove any previously rendered config body.
  const existing = content.querySelector('.admin-body');
  if (existing) existing.remove();
  const existing2 = content.querySelector('.admin-load-prompt');
  if (existing2) existing2.remove();

  const card = document.createElement('div');
  card.className = 'admin-load-prompt cfg-error-card';
  card.innerHTML = `
    <span class="cfg-error-icon">📡</span>
    <div>
      <div class="cfg-error-title">Configuration not yet loaded</div>
      <div class="cfg-error-msg">Click the button below to read this node's configuration over the radio. This may take up to 25 seconds if the node is slow to respond.</div>
      <button id="admin-load-btn" class="action-btn" style="margin-top:12px">Load Configuration</button>
    </div>`;
  content.appendChild(card);

  card.querySelector('#admin-load-btn').addEventListener('click', async () => {
    card.remove();
    _showLoadingInline(content);
    await _loadRemoteNode(content, _currentNodeId);
  });
}

/** Render previously cached config (no radio round-trip). */
function _renderCachedConfig(content, nodeId) {
  const data = _configCache.get(nodeId);
  if (!data) { _renderLoadPrompt(content); return; }
  _renderConfigBody(content, nodeId, data);
}

function _showLoadingInline(container) {
  const existing = container.querySelector('.admin-loading-inline');
  if (existing) return;
  const el = document.createElement('div');
  el.className = 'cfg-loading admin-loading-inline';
  el.innerHTML = `<div class="spinner"></div>Loading remote configuration… (may take up to 150 s if reading from radio)`;
  container.appendChild(el);
}

async function _loadRemoteNode(content, nodeId, force = false) {
  try {
    const data = await fetchRemoteConfig(nodeId, force);
    // Remove inline spinner once fetch is complete.
    const spinner = content.querySelector('.admin-loading-inline');
    if (spinner) spinner.remove();

    // Cache so switching back to this node doesn't re-fetch.
    _configCache.set(nodeId, data);
    _renderConfigBody(content, nodeId, data);
  } catch (err) {
    _showError(content, err.message);
  }
}

function _renderConfigBody(content, nodeId, data) {
  const body = document.createElement('div');
  body.className = 'admin-body';

  body.appendChild(_buildIdentityCard(data.owner || {}));
  body.appendChild(_buildActionsPanel(data.owner || {}));

  const cfgContainer = document.createElement('div');
  cfgContainer.className = 'admin-config';
  
  const headingWrap = document.createElement('div');
  headingWrap.style.display = 'flex';
  headingWrap.style.justifyContent = 'space-between';
  headingWrap.style.alignItems = 'center';

  const heading = document.createElement('h2');
  heading.className = 'admin-section-title';
  heading.textContent = 'Configuration';
  heading.style.margin = '0';
  headingWrap.appendChild(heading);

  const reloadBtn = document.createElement('button');
  reloadBtn.className = 'action-btn';
  reloadBtn.textContent = '⟳ Reload from Radio';
  reloadBtn.style.padding = '4px 12px';
  reloadBtn.style.fontSize = '0.85em';
  reloadBtn.addEventListener('click', async () => {
    reloadBtn.disabled = true;
    reloadBtn.textContent = 'Working…';
    body.remove();
    _showLoadingInline(content);
    await _loadRemoteNode(content, nodeId, true); // force=true
  });
  headingWrap.appendChild(reloadBtn);

  cfgContainer.appendChild(headingWrap);

  renderDeviceConfigSections(cfgContainer, data, {
    saveFn: (section, patch, confirm) => saveRemoteConfig(nodeId, section, patch, confirm),
    toastFn: (msg, type, dur) => window.showToast ? window.showToast(msg, type, dur) : console.log(msg),
    localBanner: false,
    reloadSectionFn: async (section) => {
      // 1. Fetch the section live from the radio (backend updates its cache).
      await fetchRemoteConfigSection(nodeId, section);
      // 2. Pull the now-fresh full config from the backend cache (force=false
      //    returns the updated cache without another radio round-trip).
      const updatedData = await fetchRemoteConfig(nodeId, false);
      // 3. Update the in-memory JS cache and re-render the config body.
      _configCache.set(nodeId, updatedData);
      _renderConfigBody(content, nodeId, updatedData);
    }
  });
  body.appendChild(cfgContainer);

  // Replace content below the capability banner + gateway keys + picker.
  const existing = content.querySelector('.admin-body');
  if (existing) existing.replaceWith(body);
  else content.appendChild(body);
}

// ---------------------------------------------------------------------------
// Identity + actions
// ---------------------------------------------------------------------------

function _buildIdentityCard(owner) {
  const card = document.createElement('div');
  card.className = 'admin-identity';
  const name = owner.long_name || 'Unknown node';
  card.innerHTML = `
    <div class="admin-identity-icon">🛰️</div>
    <div class="admin-identity-info">
      <div class="admin-identity-name">${escapeHtml(name)} <span class="admin-identity-short">${escapeHtml(owner.short_name || '')}</span></div>
      <div class="admin-identity-meta">
        ${escapeHtml(owner.hw_model || '—')} · firmware ${escapeHtml(owner.firmware_version || 'unknown')}
        · role ${escapeHtml(owner.role || 'unknown')}
      </div>
    </div>
    <div class="admin-identity-id">${escapeHtml(_currentNodeId || '')}</div>
  `;
  return card;
}

function _buildActionsPanel(owner) {
  const panel = document.createElement('div');
  panel.className = 'admin-actions';
  const heading = document.createElement('h2');
  heading.className = 'admin-section-title';
  heading.textContent = 'Actions';
  panel.appendChild(heading);

  const grid = document.createElement('div');
  grid.className = 'admin-actions-grid';

  for (const [key, meta] of Object.entries(ACTIONS)) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = meta.danger ? 'action-btn action-btn-danger admin-action-btn' : 'action-btn admin-action-btn';
    btn.textContent = meta.label;
    btn.dataset.action = key;
    btn.addEventListener('click', () => _runAction(key, btn, owner));
    grid.appendChild(btn);
  }

  // Fixed position + seconds controls are handled via prompt() in _runAction.
  panel.appendChild(grid);
  return panel;
}

async function _runAction(action, btn, owner) {
  const meta = ACTIONS[action];
  let params = {};
  let label = meta.label;

  if (action === 'reboot' || action === 'shutdown') {
    const input = prompt(`${label}: seconds before it happens?`, '10');
    if (input === null) return;
    const seconds = parseInt(input, 10);
    if (!Number.isFinite(seconds) || seconds < 0) {
      window.showToast && window.showToast('Seconds must be a non-negative number.', 'error');
      return;
    }
    params = { seconds };
  } else if (action === 'set_fixed_position') {
    const lat = prompt('Fixed position latitude (e.g. 37.7749):', '');
    if (lat === null) return;
    const lng = prompt('Fixed position longitude (e.g. -122.4194):', '');
    if (lng === null) return;
    const altRaw = prompt('Altitude (m, optional):', '0');
    if (altRaw === null) return;
    const latN = parseFloat(lat);
    const lngN = parseFloat(lng);
    const altN = parseInt(altRaw, 10) || 0;
    if (!Number.isFinite(latN) || !Number.isFinite(lngN)) {
      window.showToast && window.showToast('Latitude and longitude must be numbers.', 'error');
      return;
    }
    params = { lat: latN, lng: lngN, alt: altN };
  } else if (action === 'remove_node') {
    const target = prompt("Node ID to evict from this node's NodeDB (e.g. !a1b2c3d4):", '');
    if (target === null) return;
    if (!/^![0-9a-fA-F]{1,8}$/.test(target.trim())) {
      window.showToast && window.showToast('Enter a valid !hex node ID.', 'error');
      return;
    }
    params = { target_node_id: target.trim() };
  }

  if (meta.confirm) {
    const subject = owner.long_name || _currentNodeId;
    if (!confirm(`Confirm "${label}" on ${subject}?`)) return;
  }

  btn.disabled = true;
  btn.textContent = 'Working…';
  try {
    const result = await remoteAdminAction(_currentNodeId, action, params);
    const msg = action === 'set_time'
      ? `Clock sync sent to ${_currentNodeId}.`
      : `${label} sent to ${_currentNodeId}.`;
    window.showToast ? window.showToast(msg, 'success', 5000) : console.log(msg);
  } catch (err) {
    window.showToast ? window.showToast(`${label} failed: ${err.message}`, 'error') : console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = meta.label;
  }
}

// ---------------------------------------------------------------------------
// Refresh button
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  const refreshBtn = document.getElementById('admin-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = '⟳ Refreshing…';
      try {
        // Evict the current node from the cache so the refresh actually
        // re-reads from the radio rather than showing the cached snapshot.
        if (_currentNodeId) _configCache.delete(_currentNodeId);
        await renderRemoteAdmin();
      } catch (err) {
        window.showToast ? window.showToast(`Refresh failed: ${err.message}`, 'error') : console.error(err);
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '⟳ Refresh';
      }
    });
  }
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _showLoading(container) {
  container.innerHTML = `
    <div class="cfg-loading">
      <div class="spinner"></div>
      Loading remote administration…
    </div>`;
}

function _showError(container, message) {
  container.innerHTML = `
    <div class="cfg-error-card">
      <span class="cfg-error-icon">⚠️</span>
      <div>
        <div class="cfg-error-title">Could not load remote administration</div>
        <div class="cfg-error-msg">${escapeHtml(message)}</div>
      </div>
    </div>`;
}

// Register on window for app.js
window.renderRemoteAdmin = renderRemoteAdmin;