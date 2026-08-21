/**
 * NodePulse Web UI — Terrain Link Analysis + 3D Terrain View
 *
 * Two related features live here:
 *
 *  1. Terrain Link panel (⛰ Terrain): pick two nodes, set frequency/power/gain,
 *     and get a point-to-point LOS / Fresnel-zone / link-budget analysis served
 *     by the backend's /api/terrain/link endpoint. The elevation profile is
 *     drawn on a canvas with the LOS beam line and Fresnel-zone band overlaid.
 *
 *  2. 3D terrain view (🏔 3D): a MapLibre GL map with real terrain elevation
 *     (AWS Terrain Tiles, terrarium-encoded — free, no API key). Toggling it
 *     lazily loads MapLibre from CDN so the core dashboard stays lean.
 */

import { analyzeTerrainLink, analyzeTerrainCoverage } from './api.js';
import { escapeHtml, formatDistance } from './util.js';

// AWS Terrain Tiles (terrarium encoding) — free public DEM tiles, no key.
const TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
// Base imagery for the 3D view (OSM raster — no key).
const OSM_RASTER_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAPLIBRE_CDN_JS = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js';
const MAPLIBRE_CDN_CSS = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css';

let _nodes = [];
let _selfId = null;
let _maplibreMap = null;
let _maplibreLoaded = false;
let _maplibreLoading = false;

/** Update the cached node list used to populate the panel selects. */
export function setTerrainNodes(nodes, selfId) {
  _nodes = nodes || [];
  _selfId = selfId || null;
  populateNodeSelects();
  // Keep extruded 3D markers in sync if the 3D view is live.
  if (_maplibreMap) _add3DNodes();
}

/** Populate the From/To node <select>s with nodes that have a position. */
function populateNodeSelects() {
  const from = document.getElementById('terrain-from');
  const to = document.getElementById('terrain-to');
  const center = document.getElementById('coverage-center');

  const positioned = _nodes.filter(n => n.latitude != null && n.longitude != null);
  const optionFor = (n) => `<option value="${escapeHtml(n.id)}">${escapeHtml(n.long_name || n.short_name || n.id)}</option>`;
  if (from) from.innerHTML = positioned.map(optionFor).join('');
  if (to) to.innerHTML = positioned.map(optionFor).join('');
  if (center) {
      const prev = center.value;
      center.innerHTML = '<option value="custom">📍 Custom Location (Click map)</option>' + positioned.map(optionFor).join('');
      if (prev) center.value = prev;
  }
}

/** Lazily load MapLibre GL (JS + CSS) from CDN. Resolves true when ready. */
function loadMapLibre() {
  if (typeof window.maplibregl !== 'undefined') {
    _maplibreLoaded = true;
    return Promise.resolve(true);
  }
  if (_maplibreLoading) {
    // Return a promise that resolves when the load completes.
    return new Promise((resolve) => {
      const check = () => {
        if (_maplibreLoaded || typeof window.maplibregl !== 'undefined') {
          _maplibreLoaded = true;
          resolve(true);
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  }
  _maplibreLoading = true;
  return new Promise((resolve) => {
    if (!document.querySelector('link[data-maplibre-css]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = MAPLIBRE_CDN_CSS;
      link.dataset.maplibreCss = '1';
      document.head.appendChild(link);
    }
    const script = document.createElement('script');
    script.src = MAPLIBRE_CDN_JS;
    script.onload = () => { _maplibreLoaded = true; _maplibreLoading = false; resolve(true); };
    script.onerror = () => { _maplibreLoading = false; resolve(false); };
    document.head.appendChild(script);
  });
}

/**
 * Toggle the 3D terrain view. When enabling, the Leaflet map is hidden and a
 * MapLibre GL map with terrain elevation is shown in its place.
 * @param {() => boolean} isActive Predicate that returns whether the toggle
 *   button is still active — used to bail out if the user toggles off while
 *   MapLibre is still loading.
 */
export async function toggle3DView(isActive) {
  const container = document.getElementById('map-3d-container');
  const leaflet = document.getElementById('full-map');
  if (!container || !leaflet) return;

  const wasVisible = !container.classList.contains('hidden');

  if (wasVisible) {
    // Exit 3D: destroy the MapLibre map, restore the Leaflet map.
    if (_maplibreMap) {
      try { _maplibreMap.remove(); } catch (_) {}
      _maplibreMap = null;
    }
    container.classList.add('hidden');
    leaflet.classList.remove('hidden');
    return;
  }

  const ready = await loadMapLibre();
  if (!ready) {
    // Undo the button state so the UI stays consistent with the failed load.
    const btn = document.getElementById('map-3d-btn');
    if (btn) btn.classList.remove('active');
    _toast('3D terrain failed to load — MapLibre CDN unreachable. Check network access.', 'error', 5000);
    return;
  }
  if (!isActive()) return;

  leaflet.classList.add('hidden');
  container.classList.remove('hidden');

  if (!_maplibreMap) {
    _maplibreMap = new window.maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {
          'osm': {
            type: 'raster',
            tiles: [OSM_RASTER_URL],
            tileSize: 256,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
          },
          'dem': {
            type: 'raster-dem',
            tiles: [TERRARIUM_URL],
            encoding: 'terrarium',
            tileSize: 256,
            maxzoom: 15,
          },
        },
        layers: [
          { id: 'base', type: 'raster', source: 'osm' },
          { id: 'hillshade', type: 'hillshade', source: 'dem', paint: { 'hillshade-exaggeration': 1.2 } },
        ],
      },
      center: [31.0218, -29.8587],
      zoom: 11,
      pitch: 50,
      bearing: -20,
    });
    _maplibreMap.on('load', () => {
      _maplibreMap.setTerrain({ source: 'dem', exaggeration: 1.5 });
      _add3DNodes();
    });
  }
}

/** Render mesh nodes as 3D-extruded markers on the MapLibre map. */
function _add3DNodes() {
  if (!_maplibreMap) return;
  const positioned = _nodes.filter(n => n.latitude != null && n.longitude != null);
  const features = positioned.map((n) => ({
    type: 'Feature',
    properties: {
      id: n.id,
      name: n.long_name || n.short_name || n.id,
      height: n.hops_away != null && n.hops_away > 0 ? 4 + n.hops_away * 2 : 6,
      color: n.id === _selfId ? '#4fc3f7' : (n.role === 'ROUTER' || n.role === 'REPEATER') ? '#ffb300' : '#00d4aa',
    },
    geometry: { type: 'Point', coordinates: [n.longitude, n.latitude] },
  }));

  if (_maplibreMap.getSource('nodes')) {
    if (_maplibreMap.getLayer('node-extruded')) _maplibreMap.removeLayer('node-extruded');
    _maplibreMap.removeSource('nodes');
  }
  _maplibreMap.addSource('nodes', { type: 'geojson', data: { type: 'FeatureCollection', features } });
  _maplibreMap.addLayer({
    id: 'node-extruded',
    type: 'fill-extrusion',
    source: 'nodes',
    paint: {
      'fill-extrusion-color': ['get', 'color'],
      'fill-extrusion-height': ['get', 'height'],
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.85,
    },
  });
}

/** Remove the 3D overlay (e.g. when leaving the Map view). */
export function destroy3DView() {
  if (_maplibreMap) {
    try { _maplibreMap.remove(); } catch (_) {}
    _maplibreMap = null;
  }
  const container = document.getElementById('map-3d-container');
  const leaflet = document.getElementById('full-map');
  if (container) container.classList.add('hidden');
  if (leaflet) leaflet.classList.remove('hidden');
}

// ---------------------------------------------------------------------------
// Terrain Link panel
// ---------------------------------------------------------------------------

/** Wire the Terrain Link panel controls and run analyses. */
export function initTerrainPanel() {
  const panel = document.getElementById('terrain-panel');
  const closeBtn = document.getElementById('terrain-panel-close');
  const analyzeBtn = document.getElementById('terrain-analyze-btn');
  const btn = document.getElementById('map-terrain-btn');

  if (!panel || !closeBtn || !analyzeBtn || !btn) return;

  const close = () => {
    panel.classList.add('hidden');
    btn.classList.remove('active');
  };
  closeBtn.addEventListener('click', close);
  btn.addEventListener('click', () => {
    const isActive = btn.classList.toggle('active');
    panel.classList.toggle('hidden', !isActive);
  });

  analyzeBtn.addEventListener('click', runTerrainAnalysis);
}

/** Build the request body from the panel inputs. Returns null if invalid. */
function buildTerrainRequest() {
  const from = document.getElementById('terrain-from').value;
  const to = document.getElementById('terrain-to').value;
  const fromNode = _nodes.find(n => n.id === from);
  const toNode = _nodes.find(n => n.id === to);
  if (!fromNode || !toNode || from === to) {
    setTerrainEmpty('Select two different positioned nodes.');
    return null;
  }
  if (fromNode.latitude == null || toNode.latitude == null) {
    setTerrainEmpty('Selected nodes are missing GPS positions.');
    return null;
  }
  return {
    from: { lat: fromNode.latitude, lng: fromNode.longitude },
    to: { lat: toNode.latitude, lng: toNode.longitude },
    frequency_mhz: parseFloat(document.getElementById('terrain-freq').value) || 915,
    tx_power_dbm: parseFloat(document.getElementById('terrain-tx-pwr').value) || 0,
    tx_gain_dbi: parseFloat(document.getElementById('terrain-tx-gain').value) || 0,
    rx_gain_dbi: parseFloat(document.getElementById('terrain-rx-gain').value) || 0,
    rx_sensitivity_dbm: parseFloat(document.getElementById('terrain-rx-sens').value) || -137,
    tx_antenna_height_m: parseFloat(document.getElementById('terrain-ant-h').value) || 2,
    rx_antenna_height_m: parseFloat(document.getElementById('terrain-ant-h').value) || 2,
    samples: 48,
  };
}

/** Run a link analysis and render the results. */
async function runTerrainAnalysis() {
  const verdicts = document.getElementById('terrain-verdicts');
  const emptyEl = document.getElementById('terrain-profile-empty');
  const budget = document.getElementById('terrain-budget');

  const body = buildTerrainRequest();
  if (!body) return;

  setTerrainEmpty('Analyzing…');
  if (verdicts) verdicts.innerHTML = '';
  if (budget) budget.innerHTML = '';

  try {
    const result = await analyzeTerrainLink(body);
    emptyEl.classList.add('hidden');
    renderVerdicts(result);
    drawTerrainProfile(result);
    renderBudget(result);
  } catch (err) {
    setTerrainEmpty(`Analysis failed: ${err.message}`);
    if (verdicts) verdicts.innerHTML = '';
    if (budget) budget.innerHTML = '';
  }
}

/** Show a placeholder message in the profile area. */
function setTerrainEmpty(message) {
  const emptyEl = document.getElementById('terrain-profile-empty');
  const canvas = document.getElementById('terrain-profile-canvas');
  if (emptyEl) {
    emptyEl.textContent = message;
    emptyEl.classList.remove('hidden');
  }
  if (canvas) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

/** Render LOS/Fresnel verdict badges above the profile. */
function renderVerdicts(result) {
  const el = document.getElementById('terrain-verdicts');
  if (!el) return;
  const losClass = result.los_clear ? 'verdict-ok' : 'verdict-bad';
  const fresnelClass = result.fresnel_clear ? 'verdict-ok' : 'verdict-warn';
  const margin = result.link_budget.fade_margin_db;
  const marginClass = margin >= 10 ? 'verdict-ok' : margin >= 0 ? 'verdict-warn' : 'verdict-bad';
  el.innerHTML = `
    <span class="terrain-verdict ${losClass}">${result.los_clear ? '✓ LOS clear' : '✗ LOS blocked'}</span>
    <span class="terrain-verdict ${fresnelClass}">${result.fresnel_clear ? '✓ Fresnel clear' : '⚠ Fresnel obstructed'}</span>
    <span class="terrain-verdict ${marginClass}">Margin ${margin.toFixed(1)} dB</span>
  `;
}

/** Render the link budget table. */
function renderBudget(result) {
  const el = document.getElementById('terrain-budget');
  if (!el) return;
  const b = result.link_budget;
  const rows = [
    ['Distance', formatDistance(result.distance_km)],
    ['Frequency', `${result.frequency_mhz} MHz`],
    ['EIRP', `${b.eirp_dbm.toFixed(1)} dBm`],
    ['Free-space loss', `${b.fspl_db.toFixed(1)} dB`],
    ['RX power', `${b.rx_power_dbm.toFixed(1)} dBm`],
    ['RX sensitivity', `${b.rx_sensitivity_dbm.toFixed(1)} dBm`],
    ['Fade margin', `${b.fade_margin_db.toFixed(1)} dB`],
    ['Min Fresnel clearance', `${(result.min_clearance_ratio * 100).toFixed(0)}%`],
  ];
  el.innerHTML = `
    <table class="terrain-budget-table">
      ${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}
    </table>`;
}

/** Draw the elevation profile with the LOS beam and Fresnel-zone band. */
function drawTerrainProfile(result) {
  const canvas = document.getElementById('terrain-profile-canvas');
  if (!canvas) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * (window.devicePixelRatio || 1);
  canvas.height = rect.height * (window.devicePixelRatio || 1);
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const pts = result.profile;
  if (!pts || pts.length < 2) return;

  const padL = 40 * dpr, padR = 12 * dpr, padT = 10 * dpr, padB = 20 * dpr;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const alts = pts.map(p => p.elevation_m);
  const beams = pts.map(p => p.beam_height_m);
  const all = alts.concat(beams);
  let altMin = Math.min(...all) - 5;
  let altMax = Math.max(...all) + 5;
  if (altMax - altMin < 10) altMax = altMin + 10;
  const maxDist = pts[pts.length - 1].distance_m / 1000;

  const xScale = maxDist > 0 ? plotW / maxDist : plotW;
  const yScale = plotH / (altMax - altMin);
  const toX = (km) => padL + km * xScale;
  const toY = (alt) => padT + plotH - (alt - altMin) * yScale;

  // Grid + axis labels.
  ctx.strokeStyle = '#2a2a2a';
  ctx.lineWidth = 0.5 * dpr;
  ctx.setLineDash([2, 3]);
  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const y = padT + (plotH / gridSteps) * i;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.fillStyle = '#666';
  ctx.font = `${9 * dpr}px Inter, sans-serif`;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= gridSteps; i++) {
    const alt = altMin + ((altMax - altMin) / gridSteps) * i;
    const y = padT + plotH - (plotH / gridSteps) * i;
    ctx.fillText(`${Math.round(alt)}m`, padL - 4 * dpr, y);
  }
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(maxDist < 1 ? `${Math.round(maxDist * 1000)}m` : `${maxDist.toFixed(2)}km`, padL + plotW / 2, H - padB + 2 * dpr);

  // Fresnel-zone band: beam ± Fresnel radius, shaded blue.
  ctx.beginPath();
  ctx.moveTo(toX(pts[0].distance_m / 1000), toY(pts[0].beam_height_m - pts[0].fresnel_radius_m));
  for (const p of pts) ctx.lineTo(toX(p.distance_m / 1000), toY(p.beam_height_m - p.fresnel_radius_m));
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    ctx.lineTo(toX(p.distance_m / 1000), toY(p.beam_height_m + p.fresnel_radius_m));
  }
  ctx.closePath();
  ctx.fillStyle = 'rgba(79, 195, 247, 0.15)';
  ctx.fill();
  ctx.strokeStyle = 'rgba(79, 195, 247, 0.5)';
  ctx.lineWidth = 1 * dpr;
  ctx.setLineDash([3, 3]);
  ctx.stroke();
  ctx.setLineDash([]);

  // Terrain fill.
  ctx.beginPath();
  ctx.moveTo(toX(pts[0].distance_m / 1000), padT + plotH);
  for (const p of pts) ctx.lineTo(toX(p.distance_m / 1000), toY(p.elevation_m));
  ctx.lineTo(toX(pts[pts.length - 1].distance_m / 1000), padT + plotH);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
  grad.addColorStop(0, 'rgba(255, 213, 79, 0.3)');
  grad.addColorStop(1, 'rgba(255, 213, 79, 0.02)');
  ctx.fillStyle = grad;
  ctx.fill();

  // Terrain line.
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {
    const x = toX(pts[i].distance_m / 1000);
    const y = toY(pts[i].elevation_m);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.strokeStyle = '#ffd54f';
  ctx.lineWidth = 2 * dpr;
  ctx.stroke();

  // LOS beam line.
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {
    const x = toX(pts[i].distance_m / 1000);
    const y = toY(pts[i].beam_height_m);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.strokeStyle = '#00d4aa';
  ctx.lineWidth = 2 * dpr;
  ctx.setLineDash([6, 3]);
  ctx.stroke();
  ctx.setLineDash([]);
}

/** Show a transient toast message (mirrors app.js showToast). */
function _toast(message, type = 'info', durationMs = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  if (durationMs > 0) setTimeout(() => toast.remove(), durationMs);
}

// ---------------------------------------------------------------------------
// Coverage Planner
// ---------------------------------------------------------------------------
let _coverageLayers = [];
let _customCoverageLat = null;
let _customCoverageLng = null;
let _coverageMapClickListener = null;

export function initCoveragePanel(leafletMap) {
  const panel = document.getElementById('coverage-panel');
  const closeBtn = document.getElementById('coverage-panel-close');
  const analyzeBtn = document.getElementById('coverage-analyze-btn');
  const btn = document.getElementById('map-coverage-btn');
  const centerSelect = document.getElementById('coverage-center');
  const customLocDiv = document.getElementById('coverage-custom-loc');
  const customCoordsSpan = document.getElementById('coverage-custom-coords');

  if (!panel || !closeBtn || !analyzeBtn || !btn) return;

  const close = () => {
    panel.classList.add('hidden');
    btn.classList.remove('active');
    if (_coverageMapClickListener && leafletMap && leafletMap._map) {
        leafletMap._map.off('click', _coverageMapClickListener);
        _coverageMapClickListener = null;
    }
  };
  closeBtn.addEventListener('click', close);
  btn.addEventListener('click', () => {
    const isActive = btn.classList.toggle('active');
    panel.classList.toggle('hidden', !isActive);
    if (!isActive && _coverageMapClickListener && leafletMap && leafletMap._map) {
        leafletMap._map.off('click', _coverageMapClickListener);
        _coverageMapClickListener = null;
    }
  });

  centerSelect.addEventListener('change', () => {
      if (centerSelect.value === 'custom') {
          customLocDiv.classList.remove('hidden');
          if (!_coverageMapClickListener && leafletMap && leafletMap._map) {
              _coverageMapClickListener = (e) => {
                  _customCoverageLat = e.latlng.lat;
                  _customCoverageLng = e.latlng.lng;
                  customCoordsSpan.textContent = `${_customCoverageLat.toFixed(5)}, ${_customCoverageLng.toFixed(5)}`;
              };
              leafletMap._map.on('click', _coverageMapClickListener);
          }
      } else {
          customLocDiv.classList.add('hidden');
          if (_coverageMapClickListener && leafletMap && leafletMap._map) {
              leafletMap._map.off('click', _coverageMapClickListener);
              _coverageMapClickListener = null;
          }
      }
  });

  analyzeBtn.addEventListener('click', () => runCoverageAnalysis(leafletMap));
}

async function runCoverageAnalysis(leafletMap) {
  const verdicts = document.getElementById('coverage-verdicts');
  const centerId = document.getElementById('coverage-center').value;
  
  let lat, lng;
  
  if (centerId === 'custom') {
      if (_customCoverageLat == null || _customCoverageLng == null) {
          if (verdicts) verdicts.textContent = 'Please click on the map to set a custom location first.';
          return;
      }
      lat = _customCoverageLat;
      lng = _customCoverageLng;
  } else {
      const centerNode = _nodes.find(n => n.id === centerId);
      if (!centerNode || centerNode.latitude == null) {
        if (verdicts) verdicts.textContent = 'Selected node is missing a GPS position.';
        return;
      }
      lat = centerNode.latitude;
      lng = centerNode.longitude;
  }
  
  const body = {
    lat,
    lng,
    radius_m: parseFloat(document.getElementById('coverage-radius').value) || 10000,
    freq_mhz: parseFloat(document.getElementById('coverage-freq').value) || 915,
    tx_power_dbm: parseFloat(document.getElementById('coverage-tx-pwr').value) || 10,
    tx_gain_dbi: parseFloat(document.getElementById('coverage-tx-gain').value) || 2.1,
    rx_gain_dbi: parseFloat(document.getElementById('coverage-rx-gain').value) || 2.1,
    rx_sensitivity_dbm: parseFloat(document.getElementById('coverage-rx-sens').value) || -137,
    tx_antenna_height_m: parseFloat(document.getElementById('coverage-ant-h').value) || 2,
    rx_antenna_height_m: parseFloat(document.getElementById('coverage-ant-h').value) || 2,
    env_loss_db: parseFloat(document.getElementById('coverage-env').value) || 0.0,
    radial_count: 72,
    samples_per_radial: 30,
  };
  
  if (verdicts) verdicts.innerHTML = '<div class="spinner" style="display:inline-block; vertical-align:middle; width:16px; height:16px; border-width:2px; margin-right:8px;"></div> Analyzing radial coverage...';
  
  try {
    const result = await analyzeTerrainCoverage(body);
    
    if (leafletMap && leafletMap._map) {
        _coverageLayers.forEach(l => leafletMap._map.removeLayer(l));
        _coverageLayers = [];
    }
    
    if (leafletMap && leafletMap._map && window.L) {
        const drawPoly = (polyData, color, fillOpacity) => {
            if (!polyData || polyData.length === 0) return null;
            const latlngs = polyData.map(p => [p.lat, p.lng]);
            const layer = window.L.polygon(latlngs, {
                color: color,
                fillColor: color,
                fillOpacity: fillOpacity,
                weight: 1,
                interactive: false
            }).addTo(leafletMap._map);
            _coverageLayers.push(layer);
            return layer;
        };

        const weak = drawPoly(result.polygons.weak, '#f44336', 0.2);     // Red (weak)
        const medium = drawPoly(result.polygons.medium, '#ffb300', 0.3); // Yellow (medium)
        const strong = drawPoly(result.polygons.strong, '#00d4aa', 0.4); // Green (strong)
        
        if (weak) {
            leafletMap._map.fitBounds(weak.getBounds());
        }
    }
    
    if (verdicts) verdicts.innerHTML = `
      <div style="display: flex; gap: 12px; font-size: 0.9em; margin-top: 8px;">
         <span style="color: #00d4aa;">■ Strong (>-100dBm)</span>
         <span style="color: #ffb300;">■ Med (>-120dBm)</span>
         <span style="color: #f44336;">■ Weak</span>
      </div>`;
  } catch (err) {
    if (verdicts) verdicts.textContent = `Analysis failed: ${err.message}`;
  }
}