/**
 * NodePulse Web UI — API Client
 *
 * Centralises all HTTP calls to the NodePulse addon backend.
 * Every function returns the parsed JSON response or throws a typed Error,
 * so callers get consistent error handling without duplicating fetch logic.
 *
 * We use a relative base URL so the same JS works both under HA Ingress
 * (where the path is injected by the proxy) and in local dev.
 */

// Resolve a base path WITHOUT a trailing slash. The API path is built as
// `${BASE_URL}/api${path}`, so a trailing slash here would produce a
// double slash (e.g. /app/local_nodepulse//api/status). Under HA Ingress
// the /app/<slug> prefix is stripped, leaving //api/status, which does NOT
// match the registered /api/status route and 404s. Keeping BASE slash-free
// yields a clean /app/local_nodepulse/api/status -> /api/status after strip.
const BASE_URL = (() => {
  const p = window.location.pathname.replace(/\/+$/, '');
  return p;
})();

/**
 * Internal helper: runs a fetch, checks response.ok, and parses JSON.
 * Throws an Error with the server's error message on failure.
 */
async function _apiFetch(path, options = {}) {
  const url = `${BASE_URL}/api${path}`;
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    throw new Error(`Network error reaching ${url}: ${err.message}`);
  }

  // Always parse JSON — even error responses have a JSON body with "error" key.
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`Server returned non-JSON response (HTTP ${response.status})`);
  }

  if (!response.ok) {
    throw new Error(body.error || `HTTP ${response.status}`);
  }

  return body;
}

/** Fetch the current connection status and node identity. */
export async function fetchStatus() {
  return _apiFetch('/status');
}

/** Fetch the full node list (ignored nodes already filtered server-side). */
export async function fetchNodes() {
  return _apiFetch('/nodes');
}

/** Fetch the channel list from the connected node. */
export async function fetchChannels() {
  return _apiFetch('/channels');
}

/** Fetch the most recent received text messages (oldest first). */
export async function fetchMessages() {
  return _apiFetch('/messages');
}

/**
 * Send a message over the mesh.
 * @param {string} text - Plaintext message content.
 * @param {string|null} destination - Node ID hex string for DM, or null for broadcast.
 * @param {number} channel - Channel index (default 0).
 */
export async function sendMessage(text, destination = null, channel = 0, scheduleAt = null) {
  const body = { text, destination, channel };
  if (scheduleAt != null) body.schedule_at = scheduleAt;
  return _apiFetch('/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Request a traceroute towards a specific node.
 * Results arrive asynchronously and are visible via subsequent /nodes polls.
 */
export async function requestTraceRoute(destination) {
  return _apiFetch('/traceRoute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ destination }),
  });
}

/** Ask a specific node to report its current GPS position. */
export async function requestPosition(destination) {
  return _apiFetch('/requestPosition', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ destination }),
  });
}

/** Fetch the set of node IDs currently tracked as HA entities. */
export async function fetchTrackedNodes() {
  return _apiFetch('/tracked-nodes');
}

/**
 * Enable or disable HA entity tracking for a specific node.
 * @param {string} nodeId - Node ID hex string (e.g. "!abcd1234").
 * @param {boolean} enabled - True to create entities, false to remove them.
 */
export async function trackNode(nodeId, enabled) {
  return _apiFetch('/track-node', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, enabled }),
  });
}

/**
 * Remove every node flagged "stale" (not currently heard by the radio) from
 * the persistent store. Returns { removed: <count> }.
 */
/**
 * Fetch position history for all nodes, or for a specific node.
 * Returns { node_id: [{ lat, lng, alt?, timestamp }, ...], ... }.
 */
export async function fetchPositionHistory(nodeId) {
  const path = nodeId ? `/position-history/${encodeURIComponent(nodeId)}` : '/position-history';
  return _apiFetch(path);
}

export async function clearStaleNodes() {
  return _apiFetch('/nodes/clear-stale', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
}

/** Fetch all user-defined node tags: { node_id: [tag, ...], ... }. */
export async function fetchTags() {
  return _apiFetch('/tags');
}

/**
 * Set tags for a single node. Returns the full updated tags dict.
 * @param {string} nodeId - Node ID hex string (e.g. "!abcd1234").
 * @param {string[]} tags - Array of tag strings.
 */
export async function setTags(nodeId, tags) {
  return _apiFetch('/tags', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, tags }),
  });
}

/** Fetch the persisted list of favorite node IDs. */
export async function fetchFavorites() {
  return _apiFetch('/favorites');
}

/**
 * Mark or unmark a node as favorite. Returns the full list of favorite node IDs.
 * @param {string} nodeId - Node ID hex string (e.g. "!abcd1234").
 * @param {boolean} favorited - True to favorite, false to unfavorite.
 */
export async function setFavorite(nodeId, favorited) {
  return _apiFetch('/favorites', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, favorited }),
  });
}

/**
 * Fetch the most recent captured packets from the packet inspector ring buffer.
 * @param {number} limit - Max entries to return (default 200, max 500).
 */
export async function fetchPackets(limit = 200) {
  return _apiFetch(`/packets?limit=${limit}`);
}

/**
 * Fetch live LoRa sniffer statistics computed over the last 60 seconds.
 */
export async function fetchSnifferStats() {
  return _apiFetch('/sniffer/stats');
}

/**
 * Fetch mesh discovery data from packet captures.
 * @param {number} windowSeconds - Time window in seconds (default 300)
 * @param {number} limit - Max nodes to return (default 100)
 */
export async function fetchMeshDiscovery(windowSeconds = 300, limit = 100) {
  return _apiFetch(`/mesh/discovery?window=${windowSeconds}&limit=${limit}`);
}

/** Delete a single node from the persistent store by hex ID. */
export async function deleteNode(nodeId) {
  return _apiFetch(`/node/${encodeURIComponent(nodeId)}`, {
    method: 'DELETE',
  });
}

/**
 * Fetch per-node signal/health diagnostics (mirrors the Telegram /diag command).
 * Returns the diagnostics dict from the backend, or throws on HTTP error.
 * @param {string} nodeId - Canonical "!hex" node ID.
 */
export async function fetchNodeSignal(nodeId) {
  return _apiFetch(`/node/${encodeURIComponent(nodeId)}/signal`);
}

/**
 * Fetch a single node's position history as a GPX 1.1 track document.
 * Returns the raw GPX string (not parsed JSON) so the caller can download it.
 * @param {string} nodeId - Canonical "!hex" node ID.
 */
export async function fetchNodeGpx(nodeId) {
  const url = `${BASE_URL}/api/node/${encodeURIComponent(nodeId)}/gpx`;
  const response = await fetch(url);
  if (!response.ok) {
    let msg = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      msg = body.error || msg;
    } catch { /* non-JSON error */ }
    throw new Error(msg);
  }
  return response.text();
}

/**
 * Fetch the node distribution grouped by hop count from the gateway.
 * Returns { distribution: [{hops, count}], total, max_hops }.
 */
export async function fetchHops() {
  return _apiFetch('/hops');
}

/**
 * Fetch the local gateway's Mesh Beacon (2.8) module configuration.
 * Returns { available: bool, ... }.
 */
export async function fetchBeacon() {
  return _apiFetch('/beacon');
}

/** Fetch all active (non-expired) waypoints from the server. */
export async function fetchWaypoints() {
  return _apiFetch('/waypoints');
}

/**
 * Create a locally-defined waypoint.
 * @param {{ name: string, lat: number, lng: number, description?: string, icon?: string, expire?: number }} wp
 */
export async function addWaypoint(wp) {
  return _apiFetch('/waypoints', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(wp),
  });
}

/**
 * Delete a waypoint by its string ID.
 * @param {string} waypointId
 */
export async function deleteWaypoint(waypointId) {
  return _apiFetch(`/waypoints/${encodeURIComponent(waypointId)}`, {
    method: 'DELETE',
  });
}

/**
 * Update a waypoint's position (or other fields).
 * @param {string} waypointId
 * @param {{ lat?: number, lng?: number, name?: string, description?: string, icon?: string }} updates
 */
export async function updateWaypoint(waypointId, updates) {
  return _apiFetch(`/waypoints/${encodeURIComponent(waypointId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
}

// ---------------------------------------------------------------------------
// Device Configuration API
// ---------------------------------------------------------------------------

/** Fetch the full device configuration snapshot from the connected node. */
export async function fetchDeviceConfig() {
  return _apiFetch('/device-config');
}

/**
 * Patch a single config section on the device.
 * @param {string} section - Section name (e.g. 'lora', 'device', 'owner').
 * @param {Record<string, unknown>} data - Partial field → value map.
 * @param {boolean} [confirm=false] - Required for danger-zone changes (ROUTER role, TX disabled).
 */
export async function saveDeviceConfig(section, data, confirm = false) {
  const body = confirm ? { ...data, confirm: true } : data;
  return _apiFetch(`/device-config/${encodeURIComponent(section)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Force a config re-read from the radio (Refresh button).
 */
export async function reloadDeviceConfig() {
  return _apiFetch('/device-config/reload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
}

/**
 * Run a security scan on all configured channels.
 * Returns { findings, has_issues, scanned_at }.
 * Throws on connection failure (HTTP 503) or unexpected error.
 */
export async function fetchSecurityScan() {
  return _apiFetch('/security/scan');
}

/**
 * Check whether the gateway can administer remote nodes (has an ADMIN channel).
 * Returns { available, admin_channel_index, actions }.
 */
export async function fetchAdminAvailable() {
  return _apiFetch('/admin/available');
}

/**
 * Read a remote node's full configuration over the admin channel.
 * @param {string} nodeId - The remote node's canonical "!hex" ID.
 * @param {boolean} force - Force fetching from the radio, bypassing local cache.
 */
export async function fetchRemoteConfig(nodeId, force = false) {
  const url = force ? `/admin/${encodeURIComponent(nodeId)}/config?force=true` : `/admin/${encodeURIComponent(nodeId)}/config`;
  return _apiFetch(url);
}

/**
 * Read a single config section from a remote node over the admin channel.
 * @param {string} nodeId - The remote node's canonical "!hex" ID.
 * @param {string} section - Section name (e.g. 'lora', 'device').
 */
export async function fetchRemoteConfigSection(nodeId, section) {
  return _apiFetch(`/admin/${encodeURIComponent(nodeId)}/config/${encodeURIComponent(section)}`);
}

/**
 * Patch a single config section on a remote node.
 * @param {string} nodeId - The remote node's canonical "!hex" ID.
 * @param {string} section - Section name (e.g. 'lora', 'device', 'owner').
 * @param {Record<string, unknown>} data - Partial field → value map.
 * @param {boolean} [confirm=false] - Required for danger-zone changes.
 */
export async function saveRemoteConfig(nodeId, section, data, confirm = false) {
  const body = confirm ? { ...data, confirm: true } : data;
  return _apiFetch(`/admin/${encodeURIComponent(nodeId)}/config/${encodeURIComponent(section)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Run a named admin action against a remote node.
 * @param {string} nodeId - The remote node's canonical "!hex" ID.
 * @param {string} action - reboot | shutdown | factory_reset | factory_reset_device |
 *   nodedb_reset | set_fixed_position | clear_fixed_position | set_time | remove_node.
 * @param {Record<string, unknown>} [params] - Action-specific parameters.
 */
export async function remoteAdminAction(nodeId, action, params = {}) {
  return _apiFetch(`/admin/${encodeURIComponent(nodeId)}/action/${encodeURIComponent(action)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

// ---------------------------------------------------------------------------
// Terrain Link Analysis API
// ---------------------------------------------------------------------------

/**
 * Fetch ground elevation for a single lat/lng point.
 * Returns { lat, lng, elevation_m } (elevation_m may be null if DEM unavailable).
 * @param {number} lat
 * @param {number} lng
 */
export async function fetchTerrainElevation(lat, lng) {
  return _apiFetch(`/terrain/elevation?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`);
}

/**
 * Analyse a point-to-point radio link over terrain (LOS / Fresnel / budget).
 * @param {object} body - See the POST /api/terrain/link schema.
 */
export async function analyzeTerrainLink(body) {
  return _apiFetch('/terrain/link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Analyse radial radio coverage (Site Planner).
 * @param {object} body - See the POST /api/terrain/coverage schema.
 */
export async function analyzeTerrainCoverage(body) {
  return _apiFetch('/terrain/coverage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
