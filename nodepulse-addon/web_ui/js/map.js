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

import { escapeHtml, haversineKm, formatDistance } from './util.js';

// Defensive monkeypatch: leaflet-heat's _redraw() calls getImageData on its
// canvas. If the map container hasn't been laid out yet (zero width), the
// call throws IndexSizeError. Guard it so the app doesn't crash.
(function _patchHeatLayer() {
  if (typeof L !== 'undefined' && L.HeatLayer && L.HeatLayer.prototype) {
    const _orig = L.HeatLayer.prototype._redraw;
    L.HeatLayer.prototype._redraw = function() {
      try { _orig.call(this); } catch (_) {}
    };
  }
})();

// Also patch simpleheat to use willReadFrequently: true for faster getImageData.
(function _patchSimpleHeat() {
  if (typeof simpleheat !== 'undefined') {
    const _origInit = simpleheat.prototype.draw;
    simpleheat.prototype.draw = function() {
      // The canvas context is created in the simpleheat constructor.
      // We can't easily patch that, but we can try to get a new context
      // with willReadFrequently before drawing.
      if (this._ctx && this._canvas) {
        try {
          this._ctx = this._canvas.getContext('2d', { willReadFrequently: true });
        } catch (_) {}
      }
      return _origInit.call(this);
    };
  }
})();

// Tile layer URL — dark CartoDB "Dark Matter" tiles, no API key needed.
const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_ATTRIBUTION = '&copy; <a href="https://carto.com">CARTO</a>';

// Custom icon for default client node markers.
const CLIENT_ICON = L.divIcon({
  className: '',
  html: `<div class="map-marker-client"></div>`,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
  popupAnchor: [0, -10],
});

const ROUTER_ICON = L.divIcon({
  className: '',
  html: `<div class="map-marker-router"></div>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -12],
});

const TRACKER_ICON = L.divIcon({
  className: '',
  html: `<div class="map-marker-tracker"></div>`,
  iconSize: [10, 10],
  iconAnchor: [5, 5],
  popupAnchor: [0, -8],
});

function getNodeIcon(role) {
  if (role === 'ROUTER' || role === 'REPEATER') return ROUTER_ICON;
  if (role === 'TRACKER') return TRACKER_ICON;
  return CLIENT_ICON;
}

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

// Default map view — Durban, South Africa (the user's local mesh region).
const DEFAULT_CENTER = [-29.8587, 31.0218];
const DEFAULT_ZOOM = 12;

// Distinct colours for each kind of overlay so they are easy to tell apart.
const COLOR_LINK_SELF   = '#00d4aa'; // self -> node connectors (teal)
const COLOR_LINK_PEER   = '#ffb74d'; // node <-> node proximity (amber)
const COLOR_TRACEROUTE  = '#4fc3f7'; // discovered traceroute paths (blue)
const COLOR_TRAIL       = '#ff7043'; // position history trails (deep orange)

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
   // that the owning MapManager listens for. The "S"/"P"/"T"/"N" keys mirror them.
   // All toggles are stacked inside a single container for collective collapse.
   const toggleBar = L.control({ position: 'topright' });
   toggleBar.onAdd = () => {
     const container = L.DomUtil.create('div', 'leaflet-control-togglebar');
     container.style.display = 'flex';
     container.style.flexDirection = 'column';
     container.style.gap = '4px';
     container.style.transition = 'opacity 0.15s';

     const collapseBtn = L.DomUtil.create('button', 'leaflet-control-maptoggle leaflet-control-collapse');
     collapseBtn.type = 'button';
     collapseBtn.title = 'Collapse overlay controls (C)';
     collapseBtn.textContent = '−';
     collapseBtn.style.marginBottom = '2px';
     L.DomEvent.disableClickPropagation(collapseBtn);
     L.DomEvent.on(collapseBtn, 'click', () => {
       const collapsed = container.classList.toggle('collapsed');
       const toggles = container.querySelectorAll('.leaflet-control-maptoggle:not(.leaflet-control-collapse)');
       toggles.forEach(t => { t.style.display = collapsed ? 'none' : ''; });
       collapseBtn.textContent = collapsed ? '+' : '−';
       collapseBtn.title = collapsed ? 'Expand overlay controls (C)' : 'Collapse overlay controls (C)';
     });
     container.appendChild(collapseBtn);

     const makeToggle = (eventName, title, glyph, initialOn) => {
       const btn = L.DomUtil.create('button', 'leaflet-control-maptoggle');
       btn.type = 'button';
       btn.title = title;
       btn.textContent = glyph;
       if (initialOn) btn.classList.add('active');
       L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.on(btn, 'click', () => {
          // Reflect the toggle's on/off state visually (the `initialOn`
          // styling is only the initial state).
          btn.classList.toggle('active');
          const evt = new CustomEvent(eventName);
          map.getContainer().dispatchEvent(evt);
        });
       container.appendChild(btn);
     };

     // Self -> node connectors (teal)                  — key "S"
     makeToggle('nodepulse:toggleselflinks', 'Toggle self→node links (S)',      '⟐', false);
     // Node <-> node proximity links (amber)            — key "P"
     makeToggle('nodepulse:togglepeerlinks', 'Toggle peer proximity links (P)', '⤬', false);
     // Traceroute paths (blue)                          — key "T"
     makeToggle('nodepulse:toggletraces',    'Toggle traceroute paths (T)',     '⤴', true);
      // Node name labels                                 — key "N"
      makeToggle('nodepulse:togglenames',     'Toggle node names (N)',           '🏷', true);
       // Position history trails                          — key "H"
       makeToggle('nodepulse:toggletrails',    'Toggle position trails (H)',      '⏳', true);
       // Coverage heatmap (signal strength)               — key "M"
       makeToggle('nodepulse:toggleheatmap',  'Toggle signal heatmap (M)',      '🌡', false);

     // Restore saved collapsed state.
     const saved = localStorage.getItem('nodepulse-map-controls-collapsed');
     if (saved === 'true') {
       container.classList.add('collapsed');
       const toggles = container.querySelectorAll('.leaflet-control-maptoggle:not(.leaflet-control-collapse)');
       toggles.forEach(t => { t.style.display = 'none'; });
       collapseBtn.textContent = '+';
       collapseBtn.title = 'Expand overlay controls (C)';
     }

     return container;
   };
   toggleBar.addTo(map);

   // --- Role legend (bottom-left) -----------------------------------------
   const legend = L.control({ position: 'bottomleft' });
   legend.onAdd = () => {
     const div = L.DomUtil.create('div', 'map-legend');
     div.innerHTML = `
       <div class="map-legend-title">Node Roles</div>
       <div class="map-legend-item">
         <span class="map-legend-marker map-marker-router" style="display:inline-block;vertical-align:middle;"></span>
         Router / Repeater
       </div>
       <div class="map-legend-item">
         <span class="map-legend-marker map-marker-client" style="display:inline-block;vertical-align:middle;"></span>
         Client
       </div>
       <div class="map-legend-item">
         <span class="map-legend-marker map-marker-tracker" style="display:inline-block;vertical-align:middle;"></span>
         Tracker
       </div>
       <div class="map-legend-divider"></div>
       <div class="map-legend-heatmap" id="map-legend-heatmap" style="display:none">
         <div class="map-legend-title">Signal Strength</div>
         <div class="map-legend-gradient"></div>
         <div class="map-legend-gradient-labels"><span>Weak</span><span>Strong</span></div>
       </div>
     `;
     L.DomEvent.disableClickPropagation(div);
     return div;
   };
   legend.addTo(map);

   // Keyboard shortcut "C" to toggle.
   L.DomEvent.on(map.getContainer(), 'keydown', (e) => {
     if (e.key === 'c' || e.key === 'C') {
       const container = document.querySelector('.leaflet-control-togglebar');
       if (container) {
         container.querySelector('.leaflet-control-collapse')?.click();
       }
     }
   });

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
    // Separate overlay line categories, each independently toggleable.
    // Map<nodeId, L.Polyline> — connectors from the self node to each node (teal).
    this._selfLinks = new Map();
    // Map<"a|b", L.Polyline> — node<->node proximity links (amber).
    this._peerLinks = new Map();
    // Map<nodeId, L.Polyline> — multi-hop traceroute route paths discovered
    // between nodes (blue).
    this._routeLinks = new Map();
    // Map<nodeId, L.Polyline> — position history trail for each node (deep orange).
    this._trailLines = new Map();
    // Separate visibility flags for each overlay category so they can be
    // toggled independently from the map controls.
    this._selfLinksVisible = false; // self -> node connectors
    this._peerLinksVisible = false; // node <-> node proximity links
    this._tracesVisible = true;     // discovered traceroute paths
    this._trailsVisible = true;     // position history trails
    this._heatmapVisible = false;   // coverage heatmap
    this._lastHeatPoints = [];      // last computed heat points (for immediate redraw)
    this._lastHeatSig = '';         // serialised heatPoints — skip setLatLngs when unchanged
    this._namesVisible = true;      // permanent node-name labels
    this._centeredOnSelf = false;    // one-time auto-centre on the connected node
    // The last full node list we received (unfiltered). Markers are drawn from
    // the subset that passes the active filter (see _filterNodes).
    this._allNodes = [];
    // Active map filter. Keys: text (name substring), maxHops (int|null),
    // heardWithin (seconds|null — only show nodes heard within this window),
    // staleOnly (bool — only show cached/stale nodes).
    this._filter = { text: '', maxHops: null, heardWithin: null, staleOnly: false };
  }

  /**
   * Filter nodes down to those matching the active map filter.
   * Pure function over a node list; used by updateNodes() before drawing.
   */
  _filterNodes(nodes) {
    const f = this._filter;
    const text = (f.text || '').trim().toLowerCase();
    const now = Date.now();
    return nodes.filter((n) => {
      if (text) {
        const hay = `${n.short_name || ''} ${n.long_name || ''} ${n.id || ''}`.toLowerCase();
        if (!hay.includes(text)) return false;
      }
      if (f.maxHops != null) {
        if (n.hops_away == null || n.hops_away > f.maxHops) return false;
      }
      if (f.heardWithin != null) {
        if (!n.last_heard) return false;
        const ageMs = now - n.last_heard * 1000;
        if (ageMs > f.heardWithin * 1000) return false;
      }
      if (f.staleOnly && !n.stale) return false;
      return true;
    });
  }

  /**
   * Set the active map filter and re-render immediately from the cached node
   * list. Accepts a partial filter object; unspecified keys are left as-is.
   *
   * @param {Object} patch - e.g. { text: 'base', maxHops: 2, heardWithin: 3600 }
   * @returns {number} count of nodes passing the new filter.
   */
  setFilter(patch) {
    Object.assign(this._filter, patch);
    this.updateNodes(this._allNodes);
    return this._filterNodes(this._allNodes).length;
  }

  /** Current filter state (read-only snapshot). */
  getFilter() {
    return { ...this._filter };
  }

  /**
   * Toggle the self→node connector lines (teal). Returns the new visibility.
   * Triggered by the map control and "S" key.
   */
  toggleSelfLinks() {
    this._selfLinksVisible = !this._selfLinksVisible;
    for (const line of this._selfLinks.values()) {
      if (this._selfLinksVisible) line.addTo(this._map);
      else this._map.removeLayer(line);
    }
    return this._selfLinksVisible;
  }

  /**
   * Toggle the node↔node peer proximity links (amber). Returns the new visibility.
   * Triggered by the map control and "P" key.
   */
  togglePeerLinks() {
    this._peerLinksVisible = !this._peerLinksVisible;
    for (const line of this._peerLinks.values()) {
      if (this._peerLinksVisible) line.addTo(this._map);
      else this._map.removeLayer(line);
    }
    return this._peerLinksVisible;
  }

  /**
   * Toggle the discovered traceroute path lines (blue). Returns the new visibility.
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
   * Toggle the coverage heatmap. Returns the new visibility.
   * Shows/hides both the heat overlay and the legend section.
   * @param {boolean} [state] - Optional explicit state to set.
   */
  toggleHeatmap(state) {
    this._heatmapVisible = state !== undefined ? state : !this._heatmapVisible;

    // Lazily create the layer here as well (not only in updateTrails) so the
    // toggle works even if updateTrails hasn't run yet or leaflet.heat loaded
    // late (it is fetched with `defer`).
    if (this._heatmapVisible && !this._heatLayer && typeof L.heatLayer === 'function') {
      this._heatLayer = L.heatLayer([], {
        radius: 40,
        blur: 30,
        maxZoom: 0, // Disable zoom-based intensity scaling
        max: 1.0,
        minOpacity: 0.4,
      });
    }

    // Only add/remove the layer if the map container has real dimensions —
    // leaflet-heat's _reset / _redraw will throw IndexSizeError if the
    // canvas has zero width (e.g. the view was just switched but rAF hasn't
    // fired yet).
    if (this._heatLayer && this._mapHasSize()) {
      if (this._heatmapVisible) {
        if (!this._map.hasLayer(this._heatLayer)) {
          if (this._lastHeatPoints.length) this._heatLayer.setLatLngs(this._lastHeatPoints);
          this._heatLayer.addTo(this._map);
        }
      } else {
        this._map.removeLayer(this._heatLayer);
      }
    }
    // Show/hide the heatmap section in the legend.
    const legendHeat = this._map?.getContainer()?.querySelector('#map-legend-heatmap');
    if (legendHeat) legendHeat.style.display = this._heatmapVisible ? '' : 'none';
    return this._heatmapVisible;
  }
  
  /** True when the map container has non-zero width/height. */
  _mapHasSize() {
    if (!this._map) return false;
    const s = this._map.getSize();
    return s && s.x > 0 && s.y > 0;
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
   * Toggle position history trail polylines. Returns the new visibility.
   */
  toggleTrails() {
    this._trailsVisible = !this._trailsVisible;
    for (const line of this._trailLines.values()) {
      if (this._trailsVisible) line.addTo(this._map);
      else this._map.removeLayer(line);
    }
    return this._trailsVisible;
  }

  /**
   * Update position history trail polylines from the position-history API data.
   * Draws one polyline per node that has 2+ GPS fixes.
   *
   * @param {Object} posHistory - { node_id: [{ lat, lng, timestamp }, ...], ... }
   */
  /**
   * Update position history trail polylines and the coverage heatmap.
   *
   * @param {Object} posHistory  - { node_id: [{ lat, lng, snr?, rssi?, timestamp }, ...], ... }
   * @param {Array}  [liveNodes] - Current node list. Used to seed heatmap from
   *   live SNR values when history entries pre-date the snr/rssi additions.
   */
  updateTrails(posHistory, liveNodes = []) {
    if (!this._map) return;

    // Remove old trails.
    for (const line of this._trailLines.values()) {
      try { line.remove(); } catch (_) {}
    }
    this._trailLines.clear();

    // Lazily create the heat layer — leaflet.heat is loaded defer'd so it
    // may not be available when init() ran, but it is always ready by the
    // time the first poll resolves.
    if (!this._heatLayer && typeof L.heatLayer === 'function') {
      this._heatLayer = L.heatLayer([], { 
        radius: 40, 
        blur: 30, 
        maxZoom: 0, // Disable zoom-based intensity scaling
        max: 1.0,
        minOpacity: 0.4
      });
    }

    const heatPoints = [];

    if (posHistory) {
      for (const [nodeId, fixes] of Object.entries(posHistory)) {
        if (!Array.isArray(fixes) || fixes.length === 0) continue;

        // --- Trail polyline (2+ fixes required) ---
        const coords = fixes
          .filter(f => f.lat != null && f.lng != null)
          .map(f => [f.lat, f.lng]);
        if (coords.length >= 2) {
          const line = L.polyline(coords, {
            color: COLOR_TRAIL,
            weight: 2,
            opacity: 0.5,
          }).bindTooltip(`Trail — ${escapeHtml(nodeId)}`, {
            sticky: true,
            className: 'link-label',
          });
          if (this._trailsVisible) line.addTo(this._map);
          this._trailLines.set(nodeId, line);
        }

        // --- Heatmap points from position history ---
        for (const f of fixes) {
          if (f.lat == null || f.lng == null) continue;
          // Normalise SNR (-20 … +15 dB) → intensity 0.1 … 1.0
          const intensity = f.snr != null
            ? Math.max(0.1, Math.min(1.0, (f.snr + 20) / 35))
            : 0.4; // default when no snr stored yet
          heatPoints.push([f.lat, f.lng, intensity]);
        }
      }
    }

    // Supplement / seed from live node positions so the heatmap is useful
    // even before any node has broadcast a new POSITION_APP packet.
    for (const node of liveNodes) {
      if (node.latitude == null || node.longitude == null) continue;
      const intensity = node.snr != null
        ? Math.max(0.1, Math.min(1.0, (node.snr + 20) / 35))
        : 0.4;
      heatPoints.push([node.latitude, node.longitude, intensity]);
    }

    if (this._heatLayer && this._mapHasSize()) {
      // Only call setLatLngs (which triggers an expensive getImageData canvas
      // redraw) when the points have actually changed. A simple serialise-and-
      // compare is safe because the heatPoints array is always built fresh.
      const newSig = JSON.stringify(heatPoints);
      if (newSig !== this._lastHeatSig) {
        this._lastHeatSig = newSig;
        this._lastHeatPoints = heatPoints;
        this._heatLayer.setLatLngs(heatPoints);
      }
      // Ensure it is on the map if the toggle is active.
      if (this._heatmapVisible && !this._map.hasLayer(this._heatLayer)) {
        this._heatLayer.addTo(this._map);
      }
    } else {
      // Store the points for when the map container gets a valid size
      // (e.g. after the deferred rAF for the Map view fires).
      this._lastHeatSig = JSON.stringify(heatPoints);
      this._lastHeatPoints = heatPoints;
    }
  }

  /**
   * Set which node is the local/self node. Link lines are drawn from this
   * node to every other GPS-fixed node, with a distance label.
   */
  setSelfNode(id) {
    // Reset the one-time auto-centre flag when the self node changes so the
    // map re-centres on the (new) connected node.
    if (id !== this._selfId) this._centeredOnSelf = false;
    this._selfId = id;
  }

  /**
   * Centre the map on the connected (self) node once it has a GPS fix.
   * Called on the first data load so the user starts focused on their own
   * gateway rather than a hardcoded default location. Subsequent updates keep
   * the user's manual pan/zoom unless they haven't moved from the default.
   */
  centerOnSelf() {
    if (!this._map || !this._selfId) return;
    const marker = this._markers.get(this._selfId);
    if (!marker) return;
    const ll = marker.getLatLng();
    if (!ll || (ll.lat === 0 && ll.lng === 0)) return;
    // Only auto-centre once, so we don't yank the view away while the user is
    // exploring. Reset _centeredOnSelf=false to re-trigger after a reload.
    if (this._centeredOnSelf) return;
    this._centeredOnSelf = true;
    this._map.setView(ll, Math.max(this._map.getZoom() || 10, 12));
  }

  /**
   * Initialise the Leaflet map. Must be called after the container element
   * is visible in the DOM — Leaflet needs a non-zero bounding box.
   */
  init() {
    if (this._map) return; // already initialised
    this._map = createMap(this._elementId);
    // Note: heatLayer is created lazily in updateTrails() because leaflet.heat
    // is loaded with `defer` and may not yet be available at init() time.
  }

  /**
   * Force Leaflet to recalculate its container size.
   * Must be called whenever the container becomes visible after being hidden
   * (e.g., switching tabs), otherwise tiles don't render correctly.
   */
  invalidateSize() {
    this._map?.invalidateSize();
    // If a heat layer exists with pending points but was skipped because the
    // container had zero size (e.g. the deferred rAF just fired), apply it now.
    if (this._heatLayer && this._heatmapVisible && this._mapHasSize()) {
      if (!this._map.hasLayer(this._heatLayer)) {
        if (this._lastHeatPoints.length) this._heatLayer.setLatLngs(this._lastHeatPoints);
        this._heatLayer.addTo(this._map);
      }
    }
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

    // Cache the full (unfiltered) list so filter changes can re-render without
    // waiting for the next poll.
    this._allNodes = nodes || [];

    // Only draw markers for nodes that pass the active filter.
    const filtered = this._filterNodes(this._allNodes);

    const seenIds = new Set();

    for (const node of filtered) {
      const { id, latitude, longitude } = node;

      // Skip nodes that have no GPS fix yet.
      if (latitude == null || longitude == null) continue;
      if (latitude === 0 && longitude === 0) continue;

      seenIds.add(id);
      const latLng = [latitude, longitude];

      const isSelf = id === this._selfId;
      const icon = isSelf ? SELF_ICON : getNodeIcon(node.role);

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
      // Ensure the icon matches current self/node status (e.g. self ID changed).
      existing.setIcon(icon);
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

    // Focus the map on the connected node once it has a GPS fix.
    this.centerOnSelf();
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

    // Remove all existing Leaflet layers from the map before rebuilding.
    // Simply clearing the Map() store leaves stale polylines on the canvas.
    this._selfLinks.forEach(l => { try { l.remove(); } catch (_) {} });
    this._peerLinks.forEach(l => { try { l.remove(); } catch (_) {} });
    this._routeLinks.forEach(l => { try { l.remove(); } catch (_) {} });
    this._selfLinks.clear();
    this._peerLinks.clear();
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
        if (this._selfLinksVisible) line.addTo(this._map);
        this._selfLinks.set(id, line);
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
        if (this._peerLinksVisible) line.addTo(this._map);
        this._peerLinks.set(`${a.id}|${b.id}`, line);
      }
    }

    // --- 3. Discovered traceroute routes between nodes (blue) ---------
    // Helper: normalise a raw integer node number to the '!xxxxxxxx' format.
    const toNodeId = (n) => '!' + (n >>> 0).toString(16).padStart(8, '0');

    // Build a lookup of ALL nodes (incl. those without GPS) so we can
    // resolve names even when we can't draw a segment.
    const allNodes = new Map();
    for (const [nid, m] of this._markers) allNodes.set(nid, m);

    let lineIndex = 0;
    for (const { id, marker } of gpsMarkers) {
      const route = marker._nodeData && marker._nodeData.traceroute;
      if (!route) continue;

      const segments = [];

      // Forward path: self → intermediate hops → responding node.
      const forward = [this._selfId, ...(route.route || []).map(toNodeId)];
      if (route.from_id) forward.push(route.from_id);
      segments.push({ path: forward, label: `Traceroute → ${id}` });

      // Return path (if the device reported one).
      if (route.route_back && route.route_back.length) {
        const back = [route.from_id, ...(route.route_back || []).map(toNodeId), this._selfId];
        segments.push({ path: back, label: `Traceroute ← ${id}` });
      }

      for (const { path, label } of segments) {
        // Draw segment-by-segment so we skip individual hops that lack GPS
        // without dropping the entire route. Even a 2-node partial segment
        // gives useful topology information.
        for (let i = 0; i < path.length - 1; i++) {
          const mA = this._markers.get(path[i]);
          const mB = this._markers.get(path[i + 1]);
          if (!mA || !mB) continue; // neither hop has a map marker

          const lA = mA.getLatLng();
          const lB = mB.getLatLng();
          // Skip (0,0) placeholder positions — some nodes report 0,0 before
          // they have a GPS fix, which draws bogus lines to the ocean.
          if ((lA.lat === 0 && lA.lng === 0) || (lB.lat === 0 && lB.lng === 0)) continue;

          const line = L.polyline([lA, lB], {
            color: COLOR_TRACEROUTE,
            weight: 2.5,
            opacity: 0.8,
          }).bindTooltip(label, { sticky: true, className: 'link-label' });

          if (this._tracesVisible) line.addTo(this._map);
          this._routeLinks.set(`tr-${id}-${lineIndex++}`, line);
        }
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
    const rssiText = node.rssi != null ? `${node.rssi} dBm`          : 'Not provided';
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
