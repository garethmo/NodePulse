/**
 * NodePulse Web UI — Device Configuration View
 *
 * Renders the Configuration tab as a set of schema-driven sectioned cards.
 * The backend returns both the field values and the field types/enum options,
 * so we generate forms dynamically — no hand-written field list here.
 *
 * Registration: we attach renderDeviceConfig() to window so app.js can call
 * it from switchView() without a circular import.
 */

import { fetchDeviceConfig, saveDeviceConfig, reloadDeviceConfig } from './api.js';
import { escapeHtml } from './util.js';

// ---------------------------------------------------------------------------
// Section display metadata — human-readable title + description per section.
// Sections not in this map are rendered with a title-cased version of the key.
// ---------------------------------------------------------------------------
const SECTION_META = {
  owner:          { title: 'Node Identity',        icon: '🪪', danger: false },
  device:         { title: 'Device',               icon: '📟', danger: false },
  lora:           { title: 'LoRa Radio',           icon: '📡', danger: true  },
  position:       { title: 'Position',             icon: '📍', danger: false },
  power:          { title: 'Power',                icon: '🔋', danger: false },
  display:        { title: 'Display',              icon: '🖥️', danger: false },
  network:        { title: 'Network / WiFi',       icon: '🌐', danger: true  },
  bluetooth:      { title: 'Bluetooth',            icon: '🔵', danger: false },
  telemetry:      { title: 'Telemetry',            icon: '📊', danger: false },
  neighbor_info:  { title: 'Neighbor Info',        icon: '🗺️', danger: false },
  mqtt:           { title: 'MQTT Module',          icon: '📨', danger: false },
  canned_message: { title: 'Canned Messages',      icon: '💬', danger: false },
  store_forward:  { title: 'Store & Forward',      icon: '📦', danger: false },
};

// Ordered list of sections to render (controls card order).
const SECTION_ORDER = [
  'owner', 'device', 'lora', 'position', 'power', 'display',
  'network', 'bluetooth', 'telemetry', 'neighbor_info',
  'mqtt', 'canned_message', 'store_forward',
];

// Fields that are passwords / sensitive — render as password inputs.
const SENSITIVE_FIELDS = new Set([
  'wifi_psk', 'password',
]);

// LoRa manual fields — only editable when use_preset is false.
const LORA_PRESET_LOCKED = new Set([
  'bandwidth', 'spread_factor', 'coding_rate', 'frequency_offset',
]);

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------
let _configData    = null;  // Last fetched config snapshot
let _rebootPending = false; // True when a write that requires reboot was made

// ---------------------------------------------------------------------------
// Public entry point (registered on window)
// ---------------------------------------------------------------------------

/** Fetch device config and render all section cards. Called by app.js switchView. */
async function renderDeviceConfig() {
  const content = document.getElementById('cfg-content');
  if (!content) return;

  _showLoading(content);

  try {
    _configData = await fetchDeviceConfig();
    _renderSections(content, _configData);
    _restoreRebootBanner();
  } catch (err) {
    _showError(content, err.message);
  }
}

// Export to window so app.js can call it without a circular import.
window.renderDeviceConfig = renderDeviceConfig;

// ---------------------------------------------------------------------------
// Refresh button
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const refreshBtn = document.getElementById('cfg-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = '⟳ Refreshing…';
      try {
        await reloadDeviceConfig();
        await renderDeviceConfig();
      } catch (err) {
        _toast(`Refresh failed: ${err.message}`, 'error');
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '⟳ Refresh';
      }
    });
  }

  const dismissBtn = document.getElementById('cfg-reboot-dismiss');
  if (dismissBtn) {
    dismissBtn.addEventListener('click', () => {
      _rebootPending = false;
      const banner = document.getElementById('cfg-reboot-banner');
      if (banner) banner.classList.add('hidden');
    });
  }
});

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function _showLoading(container) {
  container.innerHTML = `
    <div class="cfg-loading">
      <div class="spinner"></div>
      Loading device configuration…
    </div>`;
}

function _showError(container, message) {
  container.innerHTML = `
    <div class="cfg-error-card">
      <span class="cfg-error-icon">⚠️</span>
      <div>
        <div class="cfg-error-title">Could not load configuration</div>
        <div class="cfg-error-msg">${escapeHtml(message)}</div>
      </div>
    </div>`;
}

function _restoreRebootBanner() {
  const banner = document.getElementById('cfg-reboot-banner');
  if (!banner) return;
  if (_rebootPending) {
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}

function _renderSections(container, data) {
  // Build registry from the returned data (the backend includes field types).
  // We determine types from the values themselves when registry isn't returned.
  // (Phase 0: the backend currently returns flat values; Phase 1 enhancement:
  // return field meta alongside values in a future pass.)
  container.innerHTML = '';

  const grid = document.createElement('div');
  grid.className = 'cfg-grid';

  for (const sectionKey of SECTION_ORDER) {
    if (!(sectionKey in data)) continue;
    const sectionData = data[sectionKey];
    if (!sectionData || typeof sectionData !== 'object') continue;

    const meta = SECTION_META[sectionKey] || {
      title: _titleCase(sectionKey),
      icon: '⚙️',
      danger: false,
    };

    const card = _buildSectionCard(sectionKey, sectionData, meta);
    grid.appendChild(card);
  }

  container.appendChild(grid);
}

function _buildSectionCard(sectionKey, sectionData, meta) {
  const card = document.createElement('div');
  card.className = `cfg-card${meta.danger ? ' cfg-card-danger' : ''}`;
  card.id = `cfg-card-${sectionKey}`;

  const header = document.createElement('div');
  header.className = 'cfg-card-header';
  header.innerHTML = `
    <span class="cfg-card-icon">${meta.icon}</span>
    <span class="cfg-card-title">${escapeHtml(meta.title)}</span>
    ${meta.danger ? '<span class="cfg-danger-badge">Advanced</span>' : ''}
  `;
  card.appendChild(header);

  const form = document.createElement('form');
  form.className = 'cfg-form';
  form.noValidate = true;

  // Track original values for dirty detection
  const originalValues = { ...sectionData };

  // Build fields
  for (const [fieldKey, fieldValue] of Object.entries(sectionData)) {
    const row = _buildFieldRow(sectionKey, fieldKey, fieldValue);
    if (row) form.appendChild(row);
  }

  // Save + Revert buttons
  const actions = document.createElement('div');
  actions.className = 'cfg-form-actions';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'action-btn cfg-save-btn';
  saveBtn.textContent = 'Save';
  saveBtn.disabled = true;
  saveBtn.id = `cfg-save-${sectionKey}`;

  const revertBtn = document.createElement('button');
  revertBtn.type = 'button';
  revertBtn.className = 'action-btn cfg-revert-btn';
  revertBtn.textContent = 'Revert';
  revertBtn.disabled = true;
  revertBtn.id = `cfg-revert-${sectionKey}`;

  actions.appendChild(revertBtn);
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  card.appendChild(form);

  // Dirty tracking — enable Save/Revert when any input changes
  form.addEventListener('input', () => {
    const isDirty = _isFormDirty(form, originalValues, sectionKey);
    saveBtn.disabled  = !isDirty;
    revertBtn.disabled = !isDirty;
    // Grey out LoRa manual fields when use_preset is checked
    if (sectionKey === 'lora') _syncLoraPresetFields(form);
  });
  form.addEventListener('change', () => {
    const isDirty = _isFormDirty(form, originalValues, sectionKey);
    saveBtn.disabled  = !isDirty;
    revertBtn.disabled = !isDirty;
    if (sectionKey === 'lora') _syncLoraPresetFields(form);
  });

  // Initial LoRa preset sync
  if (sectionKey === 'lora') {
    // Use setTimeout so the DOM is ready
    setTimeout(() => _syncLoraPresetFields(form), 0);
  }

  // Save handler
  saveBtn.addEventListener('click', async () => {
    const patch = _collectPatch(form, originalValues, sectionKey);
    if (!patch) return;

    const needsConfirm = _patchNeedsConfirm(sectionKey, patch);
    if (needsConfirm && !_showDangerConfirm(sectionKey, patch)) {
      return; // User cancelled
    }

    saveBtn.disabled  = true;
    saveBtn.textContent = 'Saving…';

    try {
      const result = await saveDeviceConfig(
        sectionKey,
        patch,
        needsConfirm, // pass confirm=true for dangerous ops
      );

      // Update original values so dirty tracking resets
      Object.assign(originalValues, _currentFormValues(form, sectionKey));
      saveBtn.disabled   = true;
      revertBtn.disabled = true;

      if (result.reboot_required) {
        _rebootPending = true;
        _restoreRebootBanner();
        _toast(`${meta.title} saved — reboot the node to apply.`, 'success', 6000);
      } else {
        _toast(`${meta.title} saved.`, 'success');
      }
    } catch (err) {
      _toast(`Save failed: ${err.message}`, 'error');
    } finally {
      saveBtn.textContent = 'Save';
      // Re-evaluate dirty state (save may have partially updated values)
      const isDirty = _isFormDirty(form, originalValues, sectionKey);
      saveBtn.disabled   = !isDirty;
      revertBtn.disabled = !isDirty;
    }
  });

  // Revert handler — reset inputs to original values
  revertBtn.addEventListener('click', () => {
    _revertForm(form, originalValues, sectionKey);
    saveBtn.disabled   = true;
    revertBtn.disabled = true;
    if (sectionKey === 'lora') _syncLoraPresetFields(form);
  });

  return card;
}

/**
 * Build a single field row (label + input/select/checkbox).
 * Returns null for fields we can't render (nested messages, etc.).
 */
function _buildFieldRow(sectionKey, fieldKey, fieldValue) {
  // Skip complex nested objects (repeated fields like available_pins)
  if (fieldValue !== null && typeof fieldValue === 'object' && !Array.isArray(fieldValue)) {
    return null;
  }

  const row = document.createElement('div');
  row.className = 'cfg-field-row';

  const label = document.createElement('label');
  label.className = 'cfg-field-label';
  label.htmlFor = `cfg-${sectionKey}-${fieldKey}`;
  label.textContent = _labelify(fieldKey);

  const inputId = `cfg-${sectionKey}-${fieldKey}`;
  let input;

  if (typeof fieldValue === 'boolean') {
    // Checkbox
    input = document.createElement('input');
    input.type    = 'checkbox';
    input.id      = inputId;
    input.checked = !!fieldValue;
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'bool';
    input.className = 'cfg-checkbox';
  } else if (SENSITIVE_FIELDS.has(fieldKey)) {
    // Password field
    input = document.createElement('input');
    input.type        = 'password';
    input.id          = inputId;
    input.value       = fieldValue ?? '';
    input.className   = 'cfg-input';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'string';
    input.autocomplete = 'new-password';
  } else if (typeof fieldValue === 'number') {
    // Number input
    input = document.createElement('input');
    input.type    = 'number';
    input.id      = inputId;
    input.value   = fieldValue;
    input.step    = Number.isInteger(fieldValue) ? '1' : 'any';
    input.className = 'cfg-input cfg-input-number';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = Number.isInteger(fieldValue) ? 'int' : 'float';
  } else if (typeof fieldValue === 'string') {
    // Text input (may be an enum value — we don't have enum options here
    // without registry meta, so fall back to text for now)
    input = document.createElement('input');
    input.type  = 'text';
    input.id    = inputId;
    input.value = fieldValue;
    input.className = 'cfg-input';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'string';
    if (sectionKey === 'owner') {
      if (fieldKey === 'long_name')  { input.maxLength = 39; }
      if (fieldKey === 'short_name') { input.maxLength = 4;  }
    }
  } else {
    // Null / unsupported — render a disabled placeholder
    input = document.createElement('input');
    input.type     = 'text';
    input.id       = inputId;
    input.value    = fieldValue ?? '';
    input.disabled = true;
    input.className = 'cfg-input cfg-input-disabled';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'unknown';
  }

  row.appendChild(label);
  row.appendChild(input);

  // Tag LoRa manual-mode-locked fields for greying out
  if (LORA_PRESET_LOCKED.has(fieldKey)) {
    row.dataset.loraManual = 'true';
  }

  return row;
}

// ---------------------------------------------------------------------------
// Dirty tracking helpers
// ---------------------------------------------------------------------------

function _currentFormValues(form, sectionKey) {
  const values = {};
  form.querySelectorAll('[data-field-key]').forEach(el => {
    const key  = el.dataset.fieldKey;
    const type = el.dataset.fieldType;
    if (type === 'bool') {
      values[key] = el.checked;
    } else if (type === 'int') {
      values[key] = el.value !== '' ? parseInt(el.value, 10) : null;
    } else if (type === 'float') {
      values[key] = el.value !== '' ? parseFloat(el.value) : null;
    } else {
      values[key] = el.value;
    }
  });
  return values;
}

function _isFormDirty(form, original, sectionKey) {
  const current = _currentFormValues(form, sectionKey);
  for (const [key, val] of Object.entries(current)) {
    if (String(val) !== String(original[key] ?? '')) return true;
  }
  return false;
}

function _collectPatch(form, original, sectionKey) {
  const current = _currentFormValues(form, sectionKey);
  const patch = {};
  for (const [key, val] of Object.entries(current)) {
    if (String(val) !== String(original[key] ?? '')) {
      patch[key] = val;
    }
  }
  return Object.keys(patch).length ? patch : null;
}

function _revertForm(form, original, sectionKey) {
  form.querySelectorAll('[data-field-key]').forEach(el => {
    const key  = el.dataset.fieldKey;
    const type = el.dataset.fieldType;
    const orig = original[key];
    if (type === 'bool') {
      el.checked = !!orig;
    } else {
      el.value = orig ?? '';
    }
  });
}

// ---------------------------------------------------------------------------
// Danger confirmation
// ---------------------------------------------------------------------------

function _patchNeedsConfirm(section, patch) {
  if (section === 'device' && patch.role === 'ROUTER') return true;
  if (section === 'lora'   && 'tx_enabled' in patch && !patch.tx_enabled) return true;
  return false;
}

function _showDangerConfirm(section, patch) {
  let message = '';
  if (section === 'device' && patch.role === 'ROUTER') {
    message = '⚠️ Setting the device role to ROUTER significantly increases airtime ' +
              'and battery consumption. This is intended for mains-powered installs only.\n\n' +
              'Are you sure you want to change the role to ROUTER?';
  } else if (section === 'lora' && !patch.tx_enabled) {
    message = '⚠️ Disabling LoRa TX will make this node receive-only — it will be ' +
              'invisible to the rest of the mesh.\n\nAre you sure you want to disable TX?';
  }
  return message ? confirm(message) : true;
}

// ---------------------------------------------------------------------------
// LoRa preset gating
// ---------------------------------------------------------------------------

function _syncLoraPresetFields(form) {
  const presetCheckbox = form.querySelector('[data-field-key="use_preset"]');
  if (!presetCheckbox) return;
  const usePreset = presetCheckbox.checked;

  form.querySelectorAll('[data-lora-manual="true"]').forEach(row => {
    const input = row.querySelector('[data-field-key]');
    if (input) {
      input.disabled = usePreset;
      row.classList.toggle('cfg-field-disabled', usePreset);
    }
  });
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function _titleCase(str) {
  return str.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function _labelify(fieldKey) {
  // Convert snake_case to "Title Case" with a few manual overrides
  const overrides = {
    wifi_ssid:    'WiFi SSID',
    wifi_psk:     'WiFi Password',
    ntp_server:   'NTP Server',
    tx_enabled:   'TX Enabled',
    use_preset:   'Use Modem Preset',
    modem_preset: 'Modem Preset',
    sx126x_rx_boosted_gain: 'SX126x RX Boosted Gain',
    long_name:    'Long Name',
    short_name:   'Short Name',
    is_power_saving: 'Power Saving Mode',
  };
  return overrides[fieldKey] || _titleCase(fieldKey);
}

function _toast(message, type = 'info', durationMs = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  if (durationMs > 0) setTimeout(() => toast.remove(), durationMs);
}
