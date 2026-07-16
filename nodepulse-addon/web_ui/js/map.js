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
 * Great-circle distance between two lat/lon points in kilometres (haversine).
 */
function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Human-friendly distance string from a kilometres value. */
function formatDistance(km) {
  if (km == null || Number.isNaN(km)) return '—';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(2)} km`;
}

// Default map view — Durban, South Africa (the user's local mesh region).
const DEFAULT_CENTER = [-29.8587, 31.0218];
const DEFAULT_ZOOM = 12;

// Distinct colours for each kind of overlay so they are easy to tell apart.
const COLOR_LINK_SELF   = '#00d4aa'; // self -> node connectors (teal)
const COLOR_LINK_PEER   = '#ffb74d'; // node <-> node proximity (amber)
const COLOR_TRACEROUTE  = '#4fc3f7'; // discovered traceroute paths (blue)

/**
 * Create and return a Leaflet map bound to a DOM element ID.
 * Starts centred on Durban, South Africa at a street/neighbourhood zoom.
 */
function createMap(elementId) {
  const map = L.map(elementId, {
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    zoomControl: true,
  });

  L.tileLayer(TILE_URL, {
    attribution: TILE_ATTRIBUTION,
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(map);

  // --- Map overlay toggle controls (top-right) ---------------------------
  // Each button toggles one overlay category and dispatches a custom event
  // that the owning MapManager listens for. The "L"/"T"/"N" keys mirror them.
  const makeToggle = (eventName, title, glyph, initialOn) => {
    const Ctrl = L.Control.extend({
      options: { position: 'topright' },
      onAdd() {
        const btn = L.DomUtil.create('button', 'leaflet-control-maptoggle');
        btn.type = 'button';
        btn.title = title;
        btn.textContent = glyph;
        if (initialOn) btn.classList.add('active');
        L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.on(btn, 'click', () => {
          const evt = new CustomEvent(eventName);
          map.getContainer().dispatchEvent(evt);
        });
        return btn;
      },
    });
    map.addControl(new Ctrl());
  };

  // Links (self<->node + peer proximity): teal/amber  — key "L"
  makeToggle('nodepulse:togglelinks',   'Toggle link lines (L)',        '⤳', true);
  // Traceroute paths: blue                            — key "T"
  makeToggle('nodepulse:toggletraces',  'Toggle traceroute paths (T)', '⤴', true);
  // Node name labels                                 — key "N"
  makeToggle('nodepulse:togglenames',   'Toggle node names (N)',       '🏷', true);

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
    // Node ID of the locally-connected node (used as the hub for link lines).
    this._selfId = null;
    // Map<nodeId, L.Polyline> — link lines from the self node to each node.
    this._links = new Map();
    // Map<nodeId, L.Polyline> — multi-hop traceroute route paths discovered
    // between nodes (shows which intermediate nodes can talk to each other).
    this._routeLinks = new Map();
    // Separate visibility flags for each overlay category so they can be
    // toggled independently from the map controls.
    this._linksVisible = true;    // self<->node + peer proximity lines
    this._tracesVisible = true;  // discovered traceroute paths
    this._namesVisible = true;    // permanent node-name labels
  }

  /**
   * Toggle the link lines (self<->node connectors + peer proximity links).
   * Returns the new visibility. Triggered by the map control and "L" key.
   */
  toggleLinks() {
    this._linksVisible = !this._linksVisible;
    for (const line of this._links.values()) {
      if (this._linksVisible) line.addTo(this._map);
      else this._map.removeLayer(line);
    }
    return this._linksVisible;
  }

  /**
   * Toggle the discovered traceroute path lines. Returns the new visibility.
   * Triggered by the map control and "T" key.
   */
  toggleTraces() {
    this._tracesVisible = !this._tracesVisible;
    for (const line of this._routeLinks.values()) {
      if (this._tracesVisible) line.addTo(this._map);
      else this._map.removeLayer(line);
    }
    return this._tracesVisible;
  }

  /**
   * Toggle the permanent node-name labels. Returns the new visibility.
   * Triggered by the map control and "N" key.
   */
  toggleNames() {
    this._namesVisible = !this._namesVisible;
    for (const marker of this._markers.values()) {
      const tip = marker.getTooltip();
      if (!tip) continue;
      if (this._namesVisible) marker.openTooltip();
      else marker.closeTooltip();
    }
    return this._namesVisible;
  }

  /**
   * Set which node is the local/self node. Link lines are drawn from this
   * node to every other GPS-fixed node, with a distance label.
   */
  setSelfNode(id) {
    this._selfId = id;
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

      const isSelf = id === this._selfId;
      const icon = isSelf ? SELF_ICON : NODE_ICON;

      if (this._markers.has(id)) {
        // Update existing marker position without recreating it.
        const marker = this._markers.get(id);
        marker.setLatLng(latLng);
      } else {
        // Create a new marker with a popup and a permanent name label.
        const marker = L.marker(latLng, { icon })
          .bindPopup(this._buildPopupHtml(node))
          .bindTooltip(escapeHtml(node.long_name || node.id), {
            permanent: true,
            direction: 'right',
            offset: [10, 0],
            className: 'node-label',
          })
          .addTo(this._map);
        this._markers.set(id, marker);
      }

      // Always refresh popup content and the name label in case metrics changed.
      const existing = this._markers.get(id);
      existing._nodeData = node; // stash raw data so link drawing can read routes
      existing.setPopupContent(this._buildPopupHtml(node));
      existing.setTooltipContent(escapeHtml(node.long_name || node.id));
      // Respect the current name-label visibility toggle.
      if (this._namesVisible) existing.openTooltip();
      else existing.closeTooltip();
      if (isSelf) existing.setIcon(SELF_ICON);
    }

    // Remove markers for nodes no longer in the list.
    for (const [id, marker] of this._markers) {
      if (!seenIds.has(id)) {
        marker.remove();
        this._markers.delete(id);
      }
    }

    // Redraw the link lines from the self node to every other GPS-fixed node.
    this._updateLinks();
  }

  /**
   * Draw link lines showing which nodes can talk to each other.
   *
   * Meshtastic firmware does not broadcast a full peer-to-peer link table, so
   * we reconstruct a *plausible* mesh connectivity graph from the data we do
   * have (each node's GPS fix, its hops_away from the local node, and any
   * explicit traceroute results):
   *
   *   1. Self → node: when the local node has a GPS fix, draw a connector from
   *      it to every other GPS-fixed node (the original behaviour).
   *   2. Node ↔ node proximity links: connect any two GPS-fixed nodes that are
   *      both directly reachable from the local node (hops_away == 1) or within
   *      a typical LoRa range of each other. This is the "which nodes can talk
   *      to each other" view and works even when the local node has no GPS.
   *   3. Traceroute routes: when a traceroute has been run, the discovered hop
   *      path lists intermediate node IDs — we draw the actual multi-hop path.
   *
   * All link lines are rebuilt each poll so they track live position changes.
   */
  _updateLinks() {
    if (!this._map) return;

    // Clear previous link lines of both kinds.
    for (const line of this._links.values()) line.remove();
    this._links.clear();
    for (const line of this._routeLinks.values()) line.remove();
    this._routeLinks.clear();

    // Collect GPS-fixed markers (exclude the self node from the pairing set).
    const gpsMarkers = [];
    for (const [id, marker] of this._markers) {
      const data = marker._nodeData || {};
      if (data.latitude == null || data.longitude == null) continue;
      if (data.latitude === 0 && data.longitude === 0) continue;
      gpsMarkers.push({ id, marker, data });
    }

    const selfMarker = this._markers.get(this._selfId);
    const selfHasGps = !!(selfMarker && selfMarker._nodeData &&
      selfMarker._nodeData.latitude != null &&
      selfMarker._nodeData.longitude != null &&
      !(selfMarker._nodeData.latitude === 0 && selfMarker._nodeData.longitude === 0));

    // --- 1. Self → node distance connectors (teal) -------------------
    if (selfHasGps) {
      const selfLatLng = selfMarker.getLatLng();
      for (const { id, marker } of gpsMarkers) {
        if (id === this._selfId) continue;
        const latLng = marker.getLatLng();
        const km = haversineKm(
          selfLatLng.lat, selfLatLng.lng, latLng.lat, latLng.lng
        );
        const line = L.polyline([selfLatLng, latLng], {
          color: COLOR_LINK_SELF,
          weight: 1.5,
          opacity: 0.6,
          dashArray: '4 4',
        }).bindTooltip(`↔ ${formatDistance(km)}`, {
          sticky: true,
          className: 'link-label',
        });
        if (this._linksVisible) line.addTo(this._map);
        this._links.set(id, line);
      }
    }

    // --- 2. Node ↔ node proximity / direct-reachability links (amber) -
    // Two nodes are treated as linked if BOTH are one hop from the local node
    // (they share the same direct-RF neighbourhood) or if the great-circle
    // distance between them is within a typical LoRa range (~15 km). This is a
    // heuristic for "can talk to each other", not a guaranteed link.
    const RANGE_KM = 15;
    for (let i = 0; i < gpsMarkers.length; i++) {
      for (let j = i + 1; j < gpsMarkers.length; j++) {
        const a = gpsMarkers[i];
        const b = gpsMarkers[j];
        if (a.id === this._selfId || b.id === this._selfId) continue;

        const bothDirect = a.data.hops_away === 1 && b.data.hops_away === 1;
        const km = haversineKm(
          a.data.latitude, a.data.longitude, b.data.latitude, b.data.longitude
        );
        if (!bothDirect && km > RANGE_KM) continue;

        const line = L.polyline([a.marker.getLatLng(), b.marker.getLatLng()], {
          color: COLOR_LINK_PEER,
          weight: 1,
          opacity: 0.35,
          dashArray: '2 3',
        }).bindTooltip(`↔ ${formatDistance(km)}`, {
          sticky: true,
          className: 'link-label',
        });
        if (this._linksVisible) line.addTo(this._map);
        this._links.set(`${a.id}|${b.id}`, line);
      }
    }

    // --- 3. Discovered traceroute routes between nodes (blue) ---------
    for (const { id, marker } of gpsMarkers) {
      const route = marker._nodeData && marker._nodeData.traceroute;
      if (!route) continue;

      // The route is keyed under the destination node. Build the ordered list
      // of node IDs: self -> route nodes -> destination (route), and the
      // mirrored path for routeBack. We draw both discovered directions.
      const selfId = this._selfId;
      const toNum = (n) => '!' + (n >>> 0).toString(16).padStart(8, '0');
      const segments = [];

      // Forward path: self -> ...route... -> from_id (the responding node).
      const forward = [selfId, ...(route.route || []).map(toNum)];
      if (route.from_id) forward.push(route.from_id);
      segments.push(forward);

      // Return path: from_id -> ...routeBack... -> self.
      if (route.route_back && route.route_back.length) {
        const back = [route.from_id, ...(route.route_back || []).map(toNum), selfId];
        segments.push(back);
      }

      for (const path of segments) {
        const pts = [];
        for (const nid of path) {
          const m = this._markers.get(nid);
          if (m) pts.push(m.getLatLng());
        }
        if (pts.length < 2) continue; // not all hops have known coordinates
        const line = L.polyline(pts, {
          color: COLOR_TRACEROUTE,
          weight: 2,
          opacity: 0.7,
        }).bindTooltip('Traceroute path', { sticky: true, className: 'link-label' });
        if (this._tracesVisible) line.addTo(this._map);
        this._routeLinks.set(`${id}-${segments.indexOf(path)}`, line);
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
    const alt      = node.altitude != null ? `${Math.round(node.altitude)} m` : 'N/A';
    const volt     = node.voltage  != null ? `${node.voltage.toFixed(2)} V` : 'N/A';
    const chanUtil = node.channel_utilization != null ? `${node.channel_utilization.toFixed(1)} %` : 'N/A';
    const airUtil  = node.air_util_tx != null ? `${node.air_util_tx.toFixed(1)} %` : 'N/A';
    const temp     = node.temperature != null ? `${node.temperature.toFixed(1)} °C` : 'N/A';
    const hum      = node.relative_humidity != null ? `${node.relative_humidity.toFixed(0)} %` : 'N/A';
    const pres     = node.barometric_pressure != null ? `${node.barometric_pressure.toFixed(0)} hPa` : 'N/A';

    // Distance from the self node, if both have GPS fixes.
    let dist = 'N/A';
    if (this._selfId && node.id !== this._selfId) {
      const self = this._markers.get(this._selfId);
      const ll = self && self.getLatLng();
      if (ll && node.latitude != null && node.longitude != null) {
        dist = formatDistance(haversineKm(ll.lat, ll.lng, node.latitude, node.longitude));
      }
    }

    const row = (label, value) =>
      `<tr><td style="color:#8892a4;padding:2px 0">${label}</td><td style="text-align:right">${value}</td></tr>`;

    return `
      <div style="font-family:Inter,sans-serif;min-width:170px">
        <div style="font-weight:700;font-size:14px;margin-bottom:6px">${escapeHtml(node.long_name || node.id)}</div>
        <div style="font-size:11px;color:#8892a4;margin-bottom:8px">${escapeHtml(node.id)}</div>
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          ${row('SNR', snrText)}
          ${row('RSSI', rssiText)}
          ${row('Hops', hops)}
          ${row('Battery', battery)}
          ${row('Distance', dist)}
          ${row('Altitude', alt)}
          ${row('Voltage', volt)}
          ${row('Chan Util', chanUtil)}
          ${row('Air Util', airUtil)}
          ${row('Temp', temp)}
          ${row('Humidity', hum)}
          ${row('Pressure', pres)}
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
