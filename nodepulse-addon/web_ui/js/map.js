/**
 * NodePulse Web UI — Map Manager
 *
 * Owns both the mini map on the Dashboard view and the full-screen map
 * on the Map view. Both share the same node data but are separate
 * Leaflet instances to avoid DOM conflicts.
 *
 * We use a dark CartoDB tile layer because it matches the overall UI theme
 * without any API key requirement.
 */

// Tile layer URL — dark CartoDB "Dark Matter" tiles, no API key needed.
const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_ATTRIBUTION = '&copy; <a href="https://carto.com">CARTO</a>';

// Custom icon for node markers — teal circle with a pulsing ring.
const NODE_ICON = L.divIcon({
  className: '',
  html: `
    <div style="
      width:14px; height:14px; border-radius:50%;
      background:#00d4aa; border:2px solid rgba(0,212,170,0.4);
      box-shadow:0 0 12px rgba(0,212,170,0.6);
    "></div>`,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
  popupAnchor: [0, -10],
});

const SELF_ICON = L.divIcon({
  className: '',
  html: `
    <div style="
      width:16px; height:16px; border-radius:50%;
      background:#4fc3f7; border:3px solid rgba(79,195,247,0.5);
      box-shadow:0 0 16px rgba(79,195,247,0.8);
    "></div>`,
  iconSize: [16, 16],
  iconAnchor: [8, 8],
  popupAnchor: [0, -10],
});

/**
 * Create and return a Leaflet map bound to a DOM element ID.
 * Defaults to a world view (zoom 3) until nodes with coordinates load.
 */
function createMap(elementId) {
  const map = L.map(elementId, {
    center: [20, 0],
    zoom: 3,
    zoomControl: true,
  });

  L.tileLayer(TILE_URL, {
    attribution: TILE_ATTRIBUTION,
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(map);

  return map;
}

/**
 * MapManager — manages node markers on one Leaflet map instance.
 *
 * Markers are stored by node ID so we can update position in-place rather
 * than destroying/recreating markers on every poll cycle (which would cause
 * visible flicker and lose popup state).
 */
export class MapManager {
  constructor(elementId) {
    this._elementId = elementId;
    this._map = null;
    // Map<nodeId, L.Marker>
    this._markers = new Map();
  }

  /**
   * Initialise the Leaflet map. Must be called after the container element
   * is visible in the DOM — Leaflet needs a non-zero bounding box.
   */
  init() {
    if (this._map) return; // already initialised
    this._map = createMap(this._elementId);
  }

  /**
   * Force Leaflet to recalculate its container size.
   * Must be called whenever the container becomes visible after being hidden
   * (e.g., switching tabs), otherwise tiles don't render correctly.
   */
  invalidateSize() {
    this._map?.invalidateSize();
  }

  /**
   * Update markers from the current node list.
   * Nodes without lat/lon coordinates are skipped — they still appear in
   * the node list panel but cannot be shown on the map.
   *
   * @param {Array} nodes - Array of node objects from the API.
   */
  updateNodes(nodes) {
    if (!this._map) return;

    const seenIds = new Set();

    for (const node of nodes) {
      const { id, latitude, longitude } = node;

      // Skip nodes that have no GPS fix yet.
      if (latitude == null || longitude == null) continue;
      if (latitude === 0 && longitude === 0) continue;

      seenIds.add(id);
      const latLng = [latitude, longitude];

      if (this._markers.has(id)) {
        // Update existing marker position without recreating it.
        this._markers.get(id).setLatLng(latLng);
      } else {
        // Create a new marker with a popup showing node details.
        const marker = L.marker(latLng, { icon: NODE_ICON })
          .bindPopup(this._buildPopupHtml(node))
          .addTo(this._map);
        this._markers.set(id, marker);
      }

      // Always refresh popup content in case metrics changed.
      this._markers.get(id).setPopupContent(this._buildPopupHtml(node));
    }

    // Remove markers for nodes no longer in the list.
    for (const [id, marker] of this._markers) {
      if (!seenIds.has(id)) {
        marker.remove();
        this._markers.delete(id);
      }
    }
  }

  /**
   * Pan and zoom the map to fit all visible markers.
   * Called after the initial data load so the user sees their whole network.
   */
  fitToMarkers() {
    if (!this._map || this._markers.size === 0) return;
    const group = L.featureGroup([...this._markers.values()]);
    this._map.fitBounds(group.getBounds().pad(0.3));
  }

  /** Build the HTML string for a marker popup. */
  _buildPopupHtml(node) {
    const snrText  = node.snr  != null ? `${node.snr.toFixed(1)} dB` : 'N/A';
    const rssiText = node.rssi != null ? `${node.rssi} dBm`          : 'N/A';
    const hops     = node.hops_away != null ? node.hops_away : '?';
    const battery  = node.battery_level != null ? `${node.battery_level}%` : 'N/A';

    return `
      <div style="font-family:Inter,sans-serif;min-width:160px">
        <div style="font-weight:700;font-size:14px;margin-bottom:6px">${escapeHtml(node.long_name || node.id)}</div>
        <div style="font-size:11px;color:#8892a4;margin-bottom:8px">${escapeHtml(node.id)}</div>
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          <tr><td style="color:#8892a4;padding:2px 0">SNR</td>  <td style="color:#00d4aa;text-align:right">${snrText}</td></tr>
          <tr><td style="color:#8892a4;padding:2px 0">RSSI</td> <td style="color:#4fc3f7;text-align:right">${rssiText}</td></tr>
          <tr><td style="color:#8892a4;padding:2px 0">Hops</td> <td style="text-align:right">${hops}</td></tr>
          <tr><td style="color:#8892a4;padding:2px 0">Battery</td><td style="text-align:right">${battery}</td></tr>
        </table>
      </div>`;
  }
}

/** Escape HTML special chars to prevent XSS in popup content. */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
