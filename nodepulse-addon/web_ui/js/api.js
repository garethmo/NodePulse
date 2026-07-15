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

/**
 * Send a message over the mesh.
 * @param {string} text - Plaintext message content.
 * @param {string|null} destination - Node ID hex string for DM, or null for broadcast.
 * @param {number} channel - Channel index (default 0).
 */
export async function sendMessage(text, destination = null, channel = 0) {
  return _apiFetch('/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, destination, channel }),
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
