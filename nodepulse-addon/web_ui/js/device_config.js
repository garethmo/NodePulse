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
  owner:             { title: 'Node Identity',        icon: '🪪', danger: false },
  device:            { title: 'Device',               icon: '📟', danger: false },
  lora:              { title: 'LoRa Radio',           icon: '📡', danger: true  },
  position:          { title: 'Position',             icon: '📍', danger: false },
  power:             { title: 'Power',                icon: '🔋', danger: false },
  display:           { title: 'Display',              icon: '🖥️', danger: false },
  network:           { title: 'Network / WiFi',       icon: '🌐', danger: true  },
  bluetooth:         { title: 'Bluetooth',            icon: '🔵', danger: false },
  security:          { title: 'Security & Admin Keys', icon: '🔐', danger: true },
  telemetry:         { title: 'Telemetry',            icon: '📊', danger: false },
  neighbor_info:     { title: 'Neighbor Info',        icon: '🗺️', danger: false },
  mesh_beacon:       { title: 'Mesh Beacon',          icon: '📢', danger: false },
  status_message:    { title: 'Status Message',       icon: '📝', danger: false },
  tak:               { title: 'TAK / ATAK',           icon: '🎯', danger: false },
  traffic_management:{ title: 'Traffic Management',   icon: '🚦', danger: false },
  ambient_lighting:  { title: 'Ambient Lighting',     icon: '💡', danger: false },
  mqtt:              { title: 'MQTT Module',          icon: '📨', danger: false },
  canned_message:    { title: 'Canned Messages',      icon: '💬', danger: false },
  store_forward:     { title: 'Store & Forward',      icon: '📦', danger: false },
};

// Ordered list of sections to render (controls card order).
const SECTION_ORDER = [
  'owner', 'device', 'lora', 'position', 'power', 'display',
  'network', 'bluetooth', 'security', 'telemetry', 'neighbor_info',
  'mesh_beacon',
  'status_message',
  'tak',
  'traffic_management',
  'ambient_lighting',
  'mqtt', 'canned_message', 'store_forward',
];

// Security key fields render as read-only, copyable chips (never edited
// in-place). The private key is additionally masked until revealed.
const SECURITY_KEY_FIELDS = new Set(['public_key', 'private_key', 'admin_key']);

export { SECTION_META, SECTION_ORDER };

// Fields that are passwords / sensitive — render as password inputs.
const SENSITIVE_FIELDS = new Set([
  'wifi_psk', 'password',
]);

// LoRa manual fields — only editable when use_preset is false.
const LORA_PRESET_LOCKED = new Set([
  'bandwidth', 'spread_factor', 'coding_rate', 'frequency_offset',
]);

// Node Identity fields that are read-only (hardware/firmware info set by the
// device — not editable from the configure screen).
const IDENTITY_READONLY = new Set([
  'hw_model', 'firmware_version', 'region', 'role',
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
    _renderSections(content, _configData, { allowSecurityKeyEdit: true });
    _restoreRebootBanner();
  } catch (err) {
    _showError(content, err.message);
  }
}

// Export to window so app.js can call it without a circular import.
window.renderDeviceConfig = renderDeviceConfig;

// Reusable schema-driven section renderer used by the Remote Admin view to
// edit a REMOTE node's config (same cards, different save/toast hooks).
export function renderDeviceConfigSections(container, data, options = {}) {
  _renderSections(container, data, options);
}

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

function _renderSections(container, data, options = {}) {
  // Work on a shallow clone so we don't mutate _configData's _schema —
  // renderDeviceConfig() may be called again without a fresh fetch.
  const dataClone = { ...data };
  const schema = (dataClone._schema) || {};
  delete dataClone._schema;

  // Extract firmware version from owner section for feature gating
  const firmwareVersion = dataClone.owner?.firmware_version || '';
  const isFw28 = _isFirmwareVersionAtLeast(firmwareVersion, '2.8.0');

  container.innerHTML = '';

  const grid = document.createElement('div');
  grid.className = 'cfg-grid';

  for (const sectionKey of SECTION_ORDER) {
    if (!(sectionKey in dataClone)) continue;
    const sectionData = dataClone[sectionKey];
    if (!sectionData || typeof sectionData !== 'object') continue;

    const meta = SECTION_META[sectionKey] || {
      title: _titleCase(sectionKey),
      icon: '⚙️',
      danger: false,
    };

    // Gate 2.8+ features
    const is28Section = ['mesh_beacon', 'status_message', 'tak', 'traffic_management', 'ambient_lighting'].includes(sectionKey);
    if (is28Section && !isFw28) {
      meta.disabled = true;
      meta.disabledReason = `Requires firmware 2.8.0+ (current: ${firmwareVersion || 'unknown'})`;
    }

    const card = _buildSectionCard(sectionKey, sectionData, meta, schema[sectionKey], options);
    grid.appendChild(card);
  }

  container.appendChild(grid);
}

function _buildSectionCard(sectionKey, sectionData, meta, sectionSchema = null, options = {}) {
  const saveFn = options.saveFn || saveDeviceConfig;
  const toastFn = options.toastFn || _toast;
  const localBanner = options.localBanner !== false; // Remote views disable local reboot banner
  const card = document.createElement('div');
  card.className = `cfg-card${meta.danger ? ' cfg-card-danger' : ''}${meta.disabled ? ' cfg-card-disabled' : ''}`;
  card.id = `cfg-card-${sectionKey}`;

  const header = document.createElement('div');
  header.className = 'cfg-card-header';
  header.innerHTML = `
    <span class="cfg-card-icon">${meta.icon}</span>
    <span class="cfg-card-title">${escapeHtml(meta.title)}</span>
    ${meta.danger ? '<span class="cfg-danger-badge">Advanced</span>' : ''}
    ${meta.disabled ? '<span class="cfg-disabled-badge" title="Unavailable">🔒 Unavailable</span>' : ''}
  `;
  card.appendChild(header);

  // Show disabled reason banner if section is gated
  if (meta.disabled) {
    const banner = document.createElement('div');
    banner.className = 'cfg-disabled-banner';
    banner.textContent = meta.disabledReason || 'This feature requires a newer firmware version.';
    card.appendChild(banner);
  }

  const form = document.createElement('form');
  form.className = 'cfg-form';
  form.noValidate = true;

  // Track original values for dirty detection
  const originalValues = { ...sectionData };

  // Build fields
  for (const [fieldKey, fieldValue] of Object.entries(sectionData)) {
    const fieldSchema = (sectionSchema && sectionSchema.fields)?.[fieldKey] || null;
    const row = _buildFieldRow(sectionKey, fieldKey, fieldValue, fieldSchema, options);
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

  // Disable buttons if section is gated
  if (meta.disabled) {
    saveBtn.disabled = true;
    saveBtn.title = 'Unavailable — requires firmware 2.8.0+';
    revertBtn.disabled = true;
  }

  // Reload Section button (primarily for remote config to fetch single section)
  if (options.reloadSectionFn && !meta.disabled) {
    const reloadBtn = document.createElement('button');
    reloadBtn.type = 'button';
    reloadBtn.className = 'action-btn cfg-revert-btn';
    reloadBtn.textContent = '⟳ Reload';
    reloadBtn.title = 'Reload just this section from the radio';
    reloadBtn.style.marginRight = 'auto'; // Push to left side
    
    reloadBtn.addEventListener('click', async () => {
      reloadBtn.disabled = true;
      const originalText = reloadBtn.textContent;
      reloadBtn.textContent = '⟳ ...';
      try {
        await options.reloadSectionFn(sectionKey);
      } catch (err) {
        toastFn(`Reload failed: ${err.message}`, 'error');
      } finally {
        reloadBtn.disabled = false;
        reloadBtn.textContent = originalText;
      }
    });
    actions.appendChild(reloadBtn);
  }

  actions.appendChild(revertBtn);
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  card.appendChild(form);

  // Disable all form inputs if section is gated
  if (meta.disabled) {
    form.querySelectorAll('input, select, button').forEach(el => {
      if (el !== saveBtn && el !== revertBtn) {
        el.disabled = true;
      }
    });
  }

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
      const result = await saveFn(
        sectionKey,
        patch,
        needsConfirm, // pass confirm=true for dangerous ops
      );

      // Update original values so dirty tracking resets
      Object.assign(originalValues, _currentFormValues(form, sectionKey));
      saveBtn.disabled   = true;
      revertBtn.disabled = true;

      if (result.reboot_required) {
        if (localBanner) {
          _rebootPending = true;
          _restoreRebootBanner();
        }
        toastFn(`${meta.title} saved — reboot the node to apply.`, 'success', 6000);
      } else {
        toastFn(`${meta.title} saved.`, 'success');
      }
    } catch (err) {
      toastFn(`Save failed: ${err.message}`, 'error');
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
 * Build field rows for MeshBeaconConfig section.
 * Handles the bitfield flags as checkboxes and ChannelSettings as combined inputs.
 */
function _buildMeshBeaconFieldRow(sectionKey, fieldKey, fieldValue, fieldSchema = null) {
  const row = document.createElement('div');
  row.className = 'cfg-field-row';

  const label = document.createElement('label');
  label.className = 'cfg-field-label';
  label.htmlFor = `cfg-${sectionKey}-${fieldKey}`;
  label.textContent = _labelify(fieldKey);

  const inputId = `cfg-${sectionKey}-${fieldKey}`;
  let input;

  // flags — bitfield rendered as three checkboxes
  if (fieldKey === 'flags') {
    const flagsValue = Number(fieldValue) || 0;
    const flagDefs = [
      { bit: 1, key: 'FLAG_LISTEN_ENABLED',   label: 'Listen',          title: 'Receive MESH_BEACON_APP packets from other nodes' },
      { bit: 2, key: 'FLAG_BROADCAST_ENABLED', label: 'Broadcast',      title: 'Periodically broadcast MESH_BEACON_APP packets' },
      { bit: 4, key: 'FLAG_LEGACY_SPLIT',     label: 'Legacy Split',    title: 'Split beacon into separate MESH_BEACON_APP (offer) and TEXT_MESSAGE_APP (text) packets' },
    ];

    const container = document.createElement('div');
    container.className = 'cfg-flags-container';
    container.style.display = 'flex';
    container.style.flexWrap = 'wrap';
    container.style.gap = '12px';
    container.style.alignItems = 'center';

    for (const def of flagDefs) {
      const isSet = (flagsValue & def.bit) !== 0;
      const cbId = `${inputId}-${def.key}`;

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = cbId;
      checkbox.checked = isSet;
      checkbox.dataset.fieldKey = fieldKey;
      checkbox.dataset.flagBit = def.bit;
      checkbox.dataset.flagKey = def.key;
      checkbox.className = 'cfg-checkbox cfg-flag-checkbox';
      checkbox.title = def.title;

      const checkboxLabel = document.createElement('label');
      checkboxLabel.htmlFor = cbId;
      checkboxLabel.className = 'cfg-flag-label';
      checkboxLabel.textContent = def.label;
      checkboxLabel.style.display = 'flex';
      checkboxLabel.style.alignItems = 'center';
      checkboxLabel.style.gap = '6px';
      checkboxLabel.style.cursor = 'pointer';

      checkboxLabel.prepend(checkbox);
      container.appendChild(checkboxLabel);
    }

    // Hidden input to track the combined flags value for form collection
    const hiddenInput = document.createElement('input');
    hiddenInput.type = 'hidden';
    hiddenInput.id = inputId;
    hiddenInput.value = flagsValue;
    hiddenInput.dataset.fieldKey = fieldKey;
    hiddenInput.dataset.fieldType = 'int';
    container.appendChild(hiddenInput);

    // Update hidden input when checkboxes change
    container.querySelectorAll('.cfg-flag-checkbox').forEach(cb => {
      cb.addEventListener('change', () => {
        let newFlags = 0;
        container.querySelectorAll('.cfg-flag-checkbox').forEach(c => {
          if (c.checked) newFlags |= Number(c.dataset.flagBit);
        });
        hiddenInput.value = newFlags;
        // Trigger input event for dirty tracking
        hiddenInput.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });

    row.appendChild(label);
    row.appendChild(container);
    return row;
  }

  // broadcast_offer_channel / broadcast_on_channel — ChannelSettings (name + PSK)
  // Render as a combined text input with a helper button
  if (fieldKey === 'broadcast_offer_channel' || fieldKey === 'broadcast_on_channel') {
    // fieldValue is a stringified ChannelSettings or empty
    const displayValue = fieldValue || '';

    const container = document.createElement('div');
    container.className = 'cfg-channel-settings-container';
    container.style.display = 'flex';
    container.style.gap = '8px';
    container.style.alignItems = 'center';

    input = document.createElement('input');
    input.type = 'text';
    input.id = inputId;
    input.value = displayValue;
    input.className = 'cfg-input';
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'string';
    input.placeholder = 'Channel name (PSK stored separately)';
    input.title = 'Channel settings are managed in the Channels section. Enter the channel name here.';
    input.style.flex = '1';

    const helperBtn = document.createElement('button');
    helperBtn.type = 'button';
    helperBtn.className = 'action-btn cfg-channel-helper';
    helperBtn.textContent = '⚙️';
    helperBtn.title = 'Configure channel in Channels section';
    helperBtn.style.flexShrink = '0';

    container.appendChild(input);
    container.appendChild(helperBtn);

    row.appendChild(label);
    row.appendChild(container);
    return row;
  }

  // broadcast_offer_region / broadcast_on_region / broadcast_offer_preset / broadcast_on_preset — enums
  const isEnum = fieldSchema?.type === 'enum' && Array.isArray(fieldSchema.options);
  if (isEnum && (
    fieldKey === 'broadcast_offer_region' ||
    fieldKey === 'broadcast_on_region' ||
    fieldKey === 'broadcast_offer_preset' ||
    fieldKey === 'broadcast_on_preset'
  )) {
    input = document.createElement('select');
    input.id = inputId;
    input.className = 'cfg-input cfg-input-select';
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'enum';

    // Add empty option for "not set"
    const emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = '(not set)';
    input.appendChild(emptyOpt);

    for (const opt of fieldSchema.options) {
      const option = document.createElement('option');
      option.value = opt;
      option.textContent = _labelify(opt);
      if (String(fieldValue) === opt) option.selected = true;
      input.appendChild(option);
    }

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  // broadcast_interval_secs — number with min constraint
  if (fieldKey === 'broadcast_interval_secs') {
    input = document.createElement('input');
    input.type = 'number';
    input.id = inputId;
    input.value = fieldValue ?? '';
    input.min = fieldSchema?.min ?? 3600;
    input.step = '1';
    input.className = 'cfg-input cfg-input-number';
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'int';
    input.title = 'Minimum 3600 seconds (1 hour)';

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  // broadcast_message — string with max_length
  if (fieldKey === 'broadcast_message') {
    input = document.createElement('input');
    input.type = 'text';
    input.id = inputId;
    input.value = fieldValue ?? '';
    input.maxLength = fieldSchema?.max_length ?? 100;
    input.className = 'cfg-input';
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'string';
    input.placeholder = 'Beacon message (max 100 chars)';
    input.title = `Max ${fieldSchema?.max_length ?? 100} characters`;

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  // broadcast_send_as_node — integer (node ID)
  if (fieldKey === 'broadcast_send_as_node') {
    input = document.createElement('input');
    input.type = 'number';
    input.id = inputId;
    input.value = fieldValue ?? '';
    input.min = '0';
    input.step = '1';
    input.className = 'cfg-input cfg-input-number';
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'int';
    input.placeholder = '0 = local node';

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  // Fallback to default rendering for any other fields
  return null;
}

/**
 * Build a single field row (label + input/select/checkbox).
 * `fieldSchema` is the per-field metadata block returned by the backend
 * (type, enum options, min/max, max_length) — used to render selects and
 * constrain inputs. Falls back to value-derived types when absent.
 * Returns null for fields we can't render (nested messages, etc.).
 */
function _buildFieldRow(sectionKey, fieldKey, fieldValue, fieldSchema = null, options = {}) {
  // Skip complex nested objects (repeated fields like available_pins)
  if (fieldValue !== null && typeof fieldValue === 'object' && !Array.isArray(fieldValue)) {
    return null;
  }

  // --- MeshBeacon special handling ---
  if (sectionKey === 'mesh_beacon') {
    return _buildMeshBeaconFieldRow(sectionKey, fieldKey, fieldValue, fieldSchema);
  }

  const row = document.createElement('div');
  row.className = 'cfg-field-row';

  const label = document.createElement('label');
  label.className = 'cfg-field-label';
  label.htmlFor = `cfg-${sectionKey}-${fieldKey}`;
  label.textContent = _labelify(fieldKey);

  const inputId = `cfg-${sectionKey}-${fieldKey}`;
  let input;

  const isEnum   = fieldSchema?.type === 'enum' && Array.isArray(fieldSchema.options);
  const isSensitive = SENSITIVE_FIELDS.has(fieldKey);
  const isSecurityKey = fieldSchema?.type === 'bytes' || SECURITY_KEY_FIELDS.has(fieldKey);

  if (isSecurityKey) {
    // In the Remote Admin view, admin_key is an editable list — you add/remove
    // admin keys (e.g. paste this gateway's public key so it may administer the
    // target). public_key / private_key remain read-only identity chips.
    if (fieldKey === 'admin_key' && options.allowSecurityKeyEdit) {
      return _buildAdminKeyEditor(fieldKey, fieldValue, options);
    }
    // Read-only, copyable key display (public_key / private_key / admin_key).
    const isPrivate = fieldKey === 'private_key';
    row.appendChild(label);
    const wrap = document.createElement('div');
    wrap.className = 'cfg-key-wrap';
    const values = Array.isArray(fieldValue) ? fieldValue : [fieldValue];
    if (values.length === 0) {
      const empty = document.createElement('span');
      empty.className = 'cfg-key-empty';
      empty.textContent = 'none configured';
      wrap.appendChild(empty);
    } else {
      values.forEach((b64, i) => {
        const chip = document.createElement('div');
        chip.className = 'cfg-key-chip';
        const code = document.createElement('code');
        code.className = 'cfg-key-code';
        const masked = isPrivate ? '••••••••••••••••••••••••••••••••••••••••••' : (b64 || '(empty)');
        code.textContent = masked;
        code.dataset.revealed = 'false';
        code.dataset.secret = b64 || '';
        chip.appendChild(code);
        if (isPrivate) {
          const toggle = document.createElement('button');
          toggle.type = 'button';
          toggle.className = 'cfg-key-toggle';
          toggle.textContent = 'Show';
          toggle.addEventListener('click', () => {
            if (code.dataset.revealed === 'true') {
              code.textContent = masked;
              code.dataset.revealed = 'false';
              toggle.textContent = 'Show';
            } else {
              code.textContent = code.dataset.secret;
              code.dataset.revealed = 'true';
              toggle.textContent = 'Hide';
            }
          });
          chip.appendChild(toggle);
        }
        const copy = _makeCopyButton(b64, i === 0 ? fieldKey : `${fieldKey}-${i + 1}`);
        chip.appendChild(copy);
        wrap.appendChild(chip);
      });
    }
    row.appendChild(wrap);
    // Hidden (disabled) input so dirty tracking skips it.
    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.id = inputId;
    hidden.dataset.fieldKey = fieldKey;
    hidden.dataset.fieldType = 'bytes';
    hidden.disabled = true;
    row.appendChild(hidden);
    return row;
  }

  if (typeof fieldValue === 'boolean') {
    // Checkbox
    input = document.createElement('input');
    input.type    = 'checkbox';
    input.id      = inputId;
    input.checked = !!fieldValue;
    input.dataset.fieldKey = fieldKey;
    input.dataset.fieldType = 'bool';
    input.className = 'cfg-checkbox';
  } else if (isSensitive) {
    // Password field
    input = document.createElement('input');
    input.type        = 'password';
    input.id          = inputId;
    input.value       = fieldValue ?? '';
    input.className   = 'cfg-input';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'string';
    input.autocomplete = 'new-password';
  } else if (isEnum) {
    // Enum field → dropdown from schema options
    input = document.createElement('select');
    input.id            = inputId;
    input.className     = 'cfg-input cfg-input-select';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'enum';
    for (const opt of fieldSchema.options) {
      const option = document.createElement('option');
      option.value = opt;
      option.textContent = _labelify(opt);
      if (String(fieldValue) === opt) option.selected = true;
      input.appendChild(option);
    }
    // Read-only Node Identity info fields (region/role) render as selects too
    if (sectionKey === 'owner' && IDENTITY_READONLY.has(fieldKey)) {
      input.disabled = true;
      input.className = 'cfg-input cfg-input-disabled';
      input.title = 'Set by the device — read-only';
    }
  } else if (typeof fieldValue === 'number') {
    // Number input — apply schema min/max when available
    input = document.createElement('input');
    input.type    = 'number';
    input.id      = inputId;
    input.value   = fieldValue;
    input.step    = Number.isInteger(fieldValue) ? '1' : 'any';
    if (fieldSchema) {
      if (fieldSchema.min != null) input.min = fieldSchema.min;
      if (fieldSchema.max != null) input.max = fieldSchema.max;
    }
    input.className = 'cfg-input cfg-input-number';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = Number.isInteger(fieldValue) ? 'int' : 'float';
  } else if (typeof fieldValue === 'string') {
    // Text input — apply schema max_length when available
    input = document.createElement('input');
    input.type  = 'text';
    input.id    = inputId;
    input.value = fieldValue;
    input.className = 'cfg-input';
    input.dataset.fieldKey  = fieldKey;
    input.dataset.fieldType = 'string';
    // Suppress browser autocomplete suggestions — these are device config
    // fields, not login forms, so browser fill is never appropriate.
    input.autocomplete = 'off';
    if (fieldSchema?.max_length) {
      input.maxLength = fieldSchema.max_length;
      input.title = `Max ${fieldSchema.max_length} characters`;
    }
    // Node Identity info fields are read-only
    if (sectionKey === 'owner' && IDENTITY_READONLY.has(fieldKey)) {
      input.disabled = true;
      input.className = 'cfg-input cfg-input-disabled';
      input.title = 'Set by the device — read-only';
    }
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
// Editable admin-key list (Remote Admin only)
// ---------------------------------------------------------------------------

function _buildAdminKeyEditor(fieldKey, fieldValue, options = {}) {
  // admin_key is a repeated bytes field: a list of base64 public keys that are
  // allowed to administer the node. The UI lets you add a pasted key (or this
  // gateway's own key) and remove existing ones. The working list is mirrored
  // into a hidden input with data-field-type="bytes-list" so it flows through
  // the normal dirty-tracking / patch machinery as a real JSON array.
  const row = document.createElement('div');
  row.className = 'cfg-field-row cfg-field-row-multiline';
  const label = document.createElement('label');
  label.className = 'cfg-field-label';
  label.htmlFor = `cfg-security-${fieldKey}`;
  label.textContent = _labelify(fieldKey);
  row.appendChild(label);

  // Right-hand body: chips (stacked) + the add control, so they don't collapse
  // into a single squashed flex row next to the label.
  const body = document.createElement('div');
  body.className = 'cfg-key-editor';

  const wrap = document.createElement('div');
  wrap.className = 'cfg-key-wrap';
  body.appendChild(wrap);

  const currentKeys = Array.isArray(fieldValue) ? fieldValue.slice() : [];

  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.id = `cfg-security-${fieldKey}`;
  hidden.dataset.fieldKey = fieldKey;
  hidden.dataset.fieldType = 'bytes-list';
  hidden.value = JSON.stringify(currentKeys);

  function renderKeys() {
    wrap.querySelectorAll('.cfg-key-chip').forEach(c => c.remove());
    if (currentKeys.length === 0) {
      const empty = document.createElement('span');
      empty.className = 'cfg-key-empty';
      empty.textContent = 'none configured';
      wrap.appendChild(empty);
    } else {
      currentKeys.forEach((b64, i) => {
        const chip = document.createElement('div');
        chip.className = 'cfg-key-chip';
        const code = document.createElement('code');
        code.className = 'cfg-key-code';
        code.textContent = b64 || '(empty)';
        chip.appendChild(code);
        const copy = _makeCopyButton(b64, i === 0 ? fieldKey : `${fieldKey}-${i + 1}`);
        chip.appendChild(copy);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'cfg-key-remove';
        remove.textContent = '✕';
        remove.title = 'Remove this admin key';
        remove.addEventListener('click', () => {
          currentKeys.splice(i, 1);
          hidden.value = JSON.stringify(currentKeys);
          renderKeys();
          hidden.dispatchEvent(new Event('input', { bubbles: true }));
        });
        chip.appendChild(remove);
        wrap.appendChild(chip);
      });
    }
    hidden.value = JSON.stringify(currentKeys);
  }

  renderKeys();
  body.appendChild(hidden);

  const addRow = document.createElement('div');
  addRow.className = 'cfg-key-add';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'cfg-input cfg-key-add-input';
  input.placeholder = 'Paste a base64 admin / public key…';
  input.autocomplete = 'off';
  input.spellcheck = false;
  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'action-btn cfg-key-add-btn';
  addBtn.textContent = 'Add';
  addBtn.addEventListener('click', () => {
    const raw = input.value.trim();
    if (!raw) return;
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(raw) || raw.length % 4 !== 0) {
      window.showToast ? window.showToast('Admin key must be valid base64.', 'error') : null;
      return;
    }
    if (currentKeys.includes(raw)) {
      window.showToast ? window.showToast('That key is already in the list.', 'error') : null;
      return;
    }
    currentKeys.push(raw);
    input.value = '';
    hidden.value = JSON.stringify(currentKeys);
    renderKeys();
    hidden.dispatchEvent(new Event('input', { bubbles: true }));
  });
  addRow.appendChild(input);
  addRow.appendChild(addBtn);

  const gatewayKey = options.gatewayPublicKey;
  if (gatewayKey) {
    const gwBtn = document.createElement('button');
    gwBtn.type = 'button';
    gwBtn.className = 'action-btn cfg-key-add-btn';
    gwBtn.textContent = '＋ This gateway’s key';
    gwBtn.title = 'Add this gateway’s public key so it may administer the target node';
    gwBtn.addEventListener('click', () => {
      if (currentKeys.includes(gatewayKey)) {
        window.showToast ? window.showToast('This gateway’s key is already in the list.', 'error') : null;
        return;
      }
      currentKeys.push(gatewayKey);
      hidden.value = JSON.stringify(currentKeys);
      renderKeys();
      hidden.dispatchEvent(new Event('input', { bubbles: true }));
    });
    addRow.appendChild(gwBtn);
  }
  body.appendChild(addRow);
  row.appendChild(body);

  // Allow Revert (in _revertForm) to reset the editor from the hidden input.
  hidden.addEventListener('cfg:revert', () => {
    let restored = [];
    try { restored = JSON.parse(hidden.value); } catch { /* keep as-is */ }
    currentKeys.length = 0;
    if (Array.isArray(restored)) currentKeys.push(...restored);
    renderKeys();
  });

  return row;
}

// ---------------------------------------------------------------------------
// Dirty tracking helpers
// ---------------------------------------------------------------------------

function _currentFormValues(form, sectionKey) {
  const values = {};
  form.querySelectorAll('[data-field-key]').forEach(el => {
    // Skip disabled/read-only inputs — they are never part of a patch.
    if (el.disabled) return;
    const key  = el.dataset.fieldKey;
    const type = el.dataset.fieldType;
    if (type === 'bool') {
      values[key] = el.checked;
    } else if (type === 'int') {
      values[key] = el.value !== '' ? parseInt(el.value, 10) : null;
    } else if (type === 'float') {
      values[key] = el.value !== '' ? parseFloat(el.value) : null;
    } else if (type === 'bytes-list') {
      // Repeated-bytes editor (admin_key) — value is a JSON array of base64
      // strings. Parse defensively so a stray value can never break a patch.
      try {
        const parsed = JSON.parse(el.value);
        values[key] = Array.isArray(parsed) ? parsed : [];
      } catch {
        values[key] = [];
      }
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
    } else if (type === 'bytes-list') {
      const restored = Array.isArray(orig) ? orig : [];
      el.value = JSON.stringify(restored);
      el.dispatchEvent(new Event('cfg:revert', { bubbles: true }));
    } else {
      el.value = orig ?? '';
    }
  });
}

// ---------------------------------------------------------------------------
// Copy-to-clipboard helper (for security keys)
// ---------------------------------------------------------------------------

function _makeCopyButton(value, key) {
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'cfg-key-copy';
  copy.textContent = 'Copy';
  copy.title = 'Copy to clipboard';
  copy.dataset.key = key;
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(value);
      _copied(copy);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* ignore */ }
      ta.remove();
      _copied(copy);
    }
  });
  return copy;
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

// ---------------------------------------------------------------------------
// Danger confirmation
// ---------------------------------------------------------------------------

function _patchNeedsConfirm(section, patch) {
  if (section === 'device' && patch.role === 'ROUTER') return true;
  if (section === 'lora'   && 'tx_enabled' in patch && !patch.tx_enabled) return true;
  // Region change cuts your node off a different radio frequency band
  if (section === 'lora' && 'region' in patch) return true;
  // Credentials are sensitive — confirm before sending to the device
  if (section === 'network' && 'wifi_psk' in patch) return true;
  if (section === 'mqtt' && ('username' in patch || 'password' in patch)) return true;
  return false;
}

function _showDangerConfirm(section, patch) {
  let message = '';
  if (section === 'device' && patch.role === 'ROUTER') {
    message = '⚠️ Setting the device role to ROUTER significantly increases airtime ' +
              'and battery consumption. This is intended for mains-powered installs only.\n\n' +
              'Are you sure you want to change the role to ROUTER?';
  } else if (section === 'lora' && 'tx_enabled' in patch && !patch.tx_enabled) {
    message = '⚠️ Disabling LoRa TX will make this node receive-only — it will be ' +
              'invisible to the rest of the mesh.\n\nAre you sure you want to disable TX?';
  } else if (section === 'lora' && 'region' in patch) {
    message = '⚠️ Changing the LoRa region will retune the radio to a different ' +
              'frequency band (which may be illegal in your country).\n\n' +
              'Are you sure you want to change the region?';
  } else if (section === 'network' && 'wifi_psk' in patch) {
    message = '🔑 The WiFi password will be stored on the device and used to ' +
              'join an access point.\n\nAre you sure you want to update the WiFi credentials?';
  } else if (section === 'mqtt' && ('username' in patch || 'password' in patch)) {
    message = '🔑 MQTT credentials will be sent to and stored on the device.\n\n' +
              'Are you sure you want to update MQTT credentials?';
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

/**
 * Compare firmware version strings (e.g., "2.8.0" >= "2.8.0" -> true)
 */
function _isFirmwareVersionAtLeast(version, minVersion) {
  if (!version) return false;
  const v = version.split('.').map(Number);
  const min = minVersion.split('.').map(Number);
  for (let i = 0; i < Math.max(v.length, min.length); i++) {
    const a = v[i] || 0;
    const b = min[i] || 0;
    if (a > b) return true;
    if (a < b) return false;
  }
  return true;
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
    hw_model:     'Hardware Model',
    firmware_version: 'Firmware Version',
    region:       'Region',
    role:         'Role',
    is_power_saving: 'Power Saving Mode',
    // MeshBeacon fields
    broadcast_send_as_node:     'Broadcast as Node',
    broadcast_message:          'Broadcast Message',
    broadcast_offer_channel:    'Offer Channel',
    broadcast_offer_region:     'Offer Region',
    broadcast_offer_preset:     'Offer Modem Preset',
    broadcast_on_channel:       'TX Channel',
    broadcast_on_region:        'TX Region',
    broadcast_on_preset:        'TX Modem Preset',
    broadcast_interval_secs:    'Broadcast Interval (s)',
    // StatusMessage fields
    node_status:                'Status Text',
    // TAK fields
    team:                       'Team',
    role:                       'Role',
    // TrafficManagement fields
    enabled:                    'Enabled',
    mqtt_enabled:               'MQTT Enabled',
    mqtt_downlink_enabled:      'MQTT Downlink',
    uplink_enabled:             'Uplink Enabled',
    downlink_enabled:           'Downlink Enabled',
    ignore_mqtt:                'Ignore MQTT',
    ignore_serial:              'Ignore Serial',
    ignore_external_notification: 'Ignore External Notification',
    ignore_canned_message:      'Ignore Canned Message',
    ignore_audio:               'Ignore Audio',
    ignore_remote_hardware:     'Ignore Remote Hardware',
    ignore_ambient_lighting:    'Ignore Ambient Lighting',
    ignore_detection_sensor:    'Ignore Detection Sensor',
    ignore_paxcounter:          'Ignore PaxCounter',
    ignore_store_forward:       'Ignore Store & Forward',
    ignore_range_test:          'Ignore Range Test',
    ignore_neighbor_info:       'Ignore Neighbor Info',
    ignore_telemetry:           'Ignore Telemetry',
    ignore_tak:                 'Ignore TAK',
    ignore_status_message:      'Ignore Status Message',
    ignore_mesh_beacon:         'Ignore Mesh Beacon',
    // AmbientLighting fields
    led_gpio:                   'LED GPIO',
    led_count:                  'LED Count',
    led_type:                   'LED Type',
    brightness:                 'Brightness',
    pattern:                    'Pattern',
    color:                      'Color',
    speed:                      'Speed',
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
