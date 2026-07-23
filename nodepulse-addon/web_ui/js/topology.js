/**
 * NodePulse — Topology Graph Manager
 *
 * Renders a force-directed mesh graph using vis-network. Data sources:
 *   • Traceroute records   — explicit multi-hop paths with per-hop SNR labels.
 *   • Neighbor-info records — heard-from peers with direct SNR, fills in
 *     connectivity that has never been tracerouted.
 *
 * vis-network is loaded as a deferred global script (window.vis). We guard
 * every call site so that missing data is handled gracefully and the graph
 * is only constructed once the library is confirmed present.
 *
 * Design decisions:
 *   • Nodes and edges are held in vis.DataSets so diff updates are O(delta)
 *     rather than re-drawing the whole graph on every poll.
 *   • Edge deduplication uses a canonical "lower-id|upper-id" key so the
 *     same link discovered from both directions isn't doubled.
 *   • The graph is only active when the Topology tab is visible; vis-network
 *     does no DOM work when the container is display:none.
 */

// Role-based visual configuration — mirrors map marker colours.
const ROLE_STYLE = {
  ROUTER:   { color: '#ffb300', border: '#ffd54f', shape: 'square', size: 22 },
  REPEATER: { color: '#ffb300', border: '#ffd54f', shape: 'square', size: 22 },
  TRACKER:  { color: '#9e9e9e', border: '#bdbdbd', shape: 'dot',    size: 10 },
  CLIENT:   { color: '#00d4aa', border: '#4dd0c4', shape: 'dot',    size: 15 },
};

const DEFAULT_STYLE = ROLE_STYLE.CLIENT;

/** Canonical edge ID — always lower-id first so A→B and B→A collapse. */
function edgeId(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/** Convert a raw SNR value to a human-readable label string. */
function snrLabel(snrList) {
  if (!Array.isArray(snrList) || snrList.length === 0) return '';
  const avg = snrList.reduce((s, v) => s + v, 0) / snrList.length;
  return `${avg.toFixed(1)} dB`;
}

/** Return a colour string for a given SNR value (red→yellow→green). */
function snrColor(snr) {
  if (snr == null) return '#888888';
  if (snr > 5)   return '#4caf50'; // strong — green
  if (snr > 0)   return '#ffb300'; // moderate — amber
  if (snr > -10) return '#ff7043'; // weak — orange
  return '#e53935';                 // very weak — red
}

export class TopologyManager {
  constructor(elementId) {
    this._elementId = elementId;
    this._container = null;   // resolved on first init() call
    this._network   = null;
    this._nodesDS   = null;   // vis.DataSet — populated after vis loads
    this._edgesDS   = null;
    this._initialised = false;
    this._lastNodeCount = 0;
    
    // Toggle state
    this._showNames = true;
    this._showTraceroutes = true;
    this._showNeighbors = true;
    this._physicsEnabled = true;
    
    // Search highlight
    this._searchTerm = '';
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /**
   * Create the vis-network instance.
   * Safe to call multiple times — returns immediately after the first real init.
   * Must be called while the container element is visible (non-zero bounding box).
   */
  init() {
    if (this._initialised) return;

    // Guard: vis may not have loaded yet (e.g., offline / CDN blocked).
    if (typeof vis === 'undefined' || typeof vis.Network !== 'function') {
      this._showError('vis-network library failed to load. Check network access to unpkg.com.');
      return;
    }

    this._container = document.getElementById(this._elementId);
    if (!this._container) {
      console.warn(`TopologyManager: element #${this._elementId} not found`);
      return;
    }

    this._nodesDS = new vis.DataSet();
    this._edgesDS = new vis.DataSet();

    const options = {
      nodes: {
        font: {
          color: '#e8eaf6',
          size: 13,
          face: 'Inter, system-ui, sans-serif',
          strokeWidth: 3,
          strokeColor: 'rgba(0,0,0,0.6)',
        },
        borderWidth: 2,
        shadow: { enabled: true, color: 'rgba(0,0,0,0.4)', size: 8, x: 2, y: 2 },
        chosen: {
          node: (values) => { values.shadowSize = 16; values.borderWidth = 3; },
        },
      },
      edges: {
        width: 2,
        selectionWidth: 3,
        smooth: { enabled: true, type: 'dynamic' },
        font: { color: '#a0a6b8', size: 10, align: 'middle', strokeWidth: 0 },
        arrows: { to: { enabled: false } },
      },
      physics: {
        enabled: this._physicsEnabled,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -80,
          centralGravity: 0.005,
          springLength: 130,
          springConstant: 0.06,
          damping: 0.4,
        },
        maxVelocity: 60,
        timestep: 0.4,
        stabilization: { iterations: 200, fit: true },
      },
 interaction: {
 hover: true,
 tooltipDelay: 150,
 dragNodes: true,     // Allow dragging nodes with the mouse
 dragView: true,       // Allow panning the view with the mouse
 zoomView: true,       // Allow zooming with mouse wheel or pinch
 navigationButtons: false,
 keyboard: { enabled: true, bindToWindow: false },
 },
      layout: {
        improvedLayout: true,
      },
    };

    this._network = new vis.Network(
      this._container,
      { nodes: this._nodesDS, edges: this._edgesDS },
      options,
    );

    // Fit the graph after the initial stabilisation pass.
    this._network.on('stabilizationIterationsDone', () => {
      this._network.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
    });

    // Handle click to show node details
    this._network.on('click', (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        // Emit custom event for app to handle (e.g., switch to node view)
        const evt = new CustomEvent('topology:nodeclick', { detail: { nodeId } });
        document.dispatchEvent(evt);
      }
    });

    this._initialised = true;
    this._clearError();
  }

  /**
   * Fit all graph nodes into view with animation.
   * Safe to call even before init() completes.
   */
  fit() {
    this._network?.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
  }

  /**
   * Reset the physics layout — re-stabilizes the graph.
   */
  resetLayout() {
    if (!this._initialised) return;
    this._network.setOptions({ physics: { enabled: true, stabilization: { iterations: 200, fit: true } } });
    this._network.stabilize();
  }

  /**
   * Toggle node label visibility.
   */
  setShowNames(show) {
    this._showNames = show;
    this._applyNameVisibility();
  }

  /**
   * Toggle traceroute edges visibility.
   */
  setShowTraceroutes(show) {
    this._showTraceroutes = show;
    this._applyEdgeVisibility();
  }

  /**
   * Toggle neighbor edges visibility.
   */
  setShowNeighbors(show) {
    this._showNeighbors = show;
    this._applyEdgeVisibility();
  }

  /**
   * Toggle physics simulation.
   */
  setPhysicsEnabled(enabled) {
    this._physicsEnabled = enabled;
    if (this._initialised) {
      this._network.setOptions({ physics: { enabled } });
    }
  }

  /**
   * Search/highlight nodes by ID or short name.
   */
  setSearchTerm(term) {
    this._searchTerm = (term || '').trim().toLowerCase();
    this._applySearchHighlight();
  }

  /**
   * Push a fresh snapshot of the app state into the graph.
   * Diffs against current DataSet contents — unchanged nodes/edges are not
   * re-drawn, which avoids resetting the physics layout on every poll.
   *
   * @param {Object} state - The global app state object from app.js.
   */
  updateData(state) {
    if (!this._initialised) {
      this.init();
      if (!this._initialised) return; // still failed
    }

    const nodes = state.nodes || [];
    const newNodes = [];
    const newEdges = [];
    const edgeSeen = new Set();

    // ── Build node list ──────────────────────────────────────────────────────
    for (const node of nodes) {
      const role  = (node.role || 'CLIENT').toUpperCase();
      const style = ROLE_STYLE[role] || DEFAULT_STYLE;
      const label = node.short_name || node.id || '?';

      // Stale nodes are shown faded.
      const opacity = node.stale ? 0.4 : 1.0;

      // Tooltip: rich HTML shown on hover.
      const title = this._buildNodeTooltip(node);

      newNodes.push({
        id:    node.id,
        label: this._showNames ? label : '',
        title: title,
        color: {
          background: style.color,
          border:     style.border,
          highlight:  { background: '#ffffff', border: style.border },
          hover:      { background: '#ffffff', border: style.border },
        },
        shape:   style.shape,
        size:    style.size,
        opacity: opacity,
        // Store original label for search
        _originalLabel: label,
        // Store role for potential filtering
        _role: role,
      });
    }

    // ── Build edge list ──────────────────────────────────────────────────────
    // Priority: traceroute paths (have explicit hop order + SNR).
    if (this._showTraceroutes) {
      for (const node of nodes) {
        const tr = node.traceroute;
        if (!tr || !Array.isArray(tr.route) || tr.route.length < 2) continue;
        const route = tr.route;
        for (let i = 0; i < route.length - 1; i++) {
          const fromId = route[i];
          const toId   = route[i + 1];
          if (!fromId || !toId) continue;
          const eid = edgeId(fromId, toId);
          if (edgeSeen.has(eid)) continue;
          edgeSeen.add(eid);

          // Per-hop SNR comes from snr_towards (index matches the route array gap).
          const hopSnr = tr.snr_towards?.[i] ?? null;
          newEdges.push({
            id:    eid,
            from:  fromId,
            to:    toId,
            label: hopSnr != null ? `${hopSnr.toFixed(1)} dB` : '',
            title: `Traceroute hop — SNR: ${hopSnr != null ? hopSnr.toFixed(1) + ' dB' : 'n/a'}`,
            color: { color: snrColor(hopSnr), highlight: '#ffffff', hover: '#ffffff' },
            width: 2,
            dashes: false,
            _type: 'traceroute',
          });
        }
      }
    }

    // Fill in with neighbor-info edges not already covered by traceroutes.
    if (this._showNeighbors) {
      for (const node of nodes) {
        if (!Array.isArray(node.neighbors)) continue;
        for (const nb of node.neighbors) {
          if (!nb.id) continue;
          const eid = edgeId(node.id, nb.id);
          if (edgeSeen.has(eid)) continue;
          edgeSeen.add(eid);

          newEdges.push({
            id:    eid,
            from:  node.id,
            to:    nb.id,
            label: nb.snr != null ? `${nb.snr} dB` : '',
            title: `Neighbor link — SNR: ${nb.snr != null ? nb.snr + ' dB' : 'n/a'}`,
            color: { color: snrColor(nb.snr), highlight: '#ffffff', hover: '#ffffff' },
            width: 1.5,
            dashes: [4, 4], // dashed to visually distinguish from traced routes
            _type: 'neighbor',
          });
        }
      }
    }

    // ── Diff update ──────────────────────────────────────────────────────────
    const currentNodeIds = new Set(this._nodesDS.getIds());
    const currentEdgeIds = new Set(this._edgesDS.getIds());

    const nodesToAdd    = newNodes.filter(n => !currentNodeIds.has(n.id));
    const nodesToUpdate = newNodes.filter(n =>  currentNodeIds.has(n.id));
    const nodesToRemove = [...currentNodeIds].filter(id => !newNodes.some(n => n.id === id));

    const edgesToAdd    = newEdges.filter(e => !currentEdgeIds.has(e.id));
    const edgesToUpdate = newEdges.filter(e =>  currentEdgeIds.has(e.id));
    const edgesToRemove = [...currentEdgeIds].filter(id => !newEdges.some(e => e.id === id));

    if (nodesToRemove.length) this._nodesDS.remove(nodesToRemove);
    if (nodesToAdd.length)    this._nodesDS.add(nodesToAdd);
    if (nodesToUpdate.length) this._nodesDS.update(nodesToUpdate);

    if (edgesToRemove.length) this._edgesDS.remove(edgesToRemove);
    if (edgesToAdd.length)    this._edgesDS.add(edgesToAdd);
    if (edgesToUpdate.length) this._edgesDS.update(edgesToUpdate);

    // Apply search highlight if active
    this._applySearchHighlight();

    // Show empty state if no nodes.
    if (nodes.length === 0) {
      this._showError('No nodes visible. Connect to your Meshtastic node to populate the graph.');
    } else {
      this._clearError();
    }

    // Re-fit if the node count jumped noticeably (new nodes discovered).
    if (Math.abs(nodes.length - this._lastNodeCount) > 2) {
      this._network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
    }
    this._lastNodeCount = nodes.length;
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  _applyNameVisibility() {
    if (!this._initialised) return;
    const updates = [];
    this._nodesDS.forEach(node => {
      const label = this._showNames ? node._originalLabel : '';
      if (node.label !== label) {
        updates.push({ id: node.id, label });
      }
    });
    if (updates.length) this._nodesDS.update(updates);
  }

  _applyEdgeVisibility() {
    if (!this._initialised) return;
    const updates = [];
    this._edgesDS.forEach(edge => {
      const shouldShow = edge._type === 'traceroute' ? this._showTraceroutes : this._showNeighbors;
      if (edge.hidden !== undefined && edge.hidden === !shouldShow) return;
      updates.push({ id: edge.id, hidden: !shouldShow });
    });
    if (updates.length) this._edgesDS.update(updates);
  }

  _applySearchHighlight() {
    if (!this._initialised) return;
    const term = this._searchTerm;
    const updates = [];
    this._nodesDS.forEach(node => {
      const label = node._originalLabel || '';
      const matches = term === '' || label.toLowerCase().includes(term);
      const color = matches ? node.color : { background: '#444', border: '#666', opacity: 0.4 };
      const fontColor = matches ? '#e8eaf6' : '#666';
      if (node.color !== color || node.font?.color !== fontColor) {
        updates.push({
          id: node.id,
          color: color,
          font: { ...node.font, color: fontColor },
        });
      }
    });
    if (updates.length) this._nodesDS.update(updates);
  }

 /** Build a tooltip string for a node. */
_buildNodeTooltip(node) {
const escapeTextContent = (text) => {
if (!text) return '';
return String(text)
 .replace(/&/g, '&amp;')
 .replace(/</g, '&lt;')
 .replace(/>/g, '&gt;')
 .replace(/\"/g, '&quot;')
 .replace(/'/g, '&#39;');
};

const titleLines = [
`${escapeTextContent(node.long_name || node.id)}`,
node.id ? `ID: ${escapeTextContent(node.id)}` : null,
node.role ? `Role: ${escapeTextContent(node.role)}` : null,
node.hops_away != null ? `Hops: ${escapeTextContent(node.hops_away)}` : null,
node.snr != null ? `SNR: ${escapeTextContent(node.snr)} dB` : null,
node.rssi != null ? `RSSI: ${escapeTextContent(node.rssi)} dBm` : null,
node.battery_level != null ? `Battery: ${escapeTextContent(node.battery_level)}%` : null,
];
return titleLines.filter(Boolean).join('\n');
}

  /** Show a text message in the container (error / empty state). */
  _showError(msg) {
    const el = document.getElementById(`${this._elementId}-msg`);
    if (el) { el.textContent = msg; el.style.display = ''; return; }
    // Create on first call.
    const div = document.createElement('div');
    div.id = `${this._elementId}-msg`;
    div.className = 'topology-empty-state';
    div.textContent = msg;
    const container = document.getElementById(this._elementId)?.parentElement;
    if (container) container.appendChild(div);
  }

  _clearError() {
    const el = document.getElementById(`${this._elementId}-msg`);
    if (el) el.style.display = 'none';
  }
}
