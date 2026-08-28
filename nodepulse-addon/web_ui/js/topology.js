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
    this._hasStabilized = false;   // becomes true after first stabilization pass
    
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

    // Fit the graph once after the initial stabilisation pass.
    // The `_hasStabilized` flag prevents re-fitting on subsequent
    // stabilizations triggered by resetLayout() — those should preserve
    // the user's current viewport.
    this._network.on('stabilizationIterationsDone', () => {
      if (this._hasStabilized) return;
      this._hasStabilized = true;
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
    this._network.setOptions({ physics: { enabled: true, stabilization: { iterations: 200 } } });
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

      const roleColor = {
        background: style.color,
        border:     style.border,
        highlight:  { background: '#ffffff', border: style.border },
        hover:      { background: '#ffffff', border: style.border },
      };
      newNodes.push({
        id:    node.id,
        label: this._showNames ? label : '',
        title: title,
        color: roleColor,
        shape:   style.shape,
        size:    style.size,
        opacity: opacity,
        _originalLabel: label,
        _originalColor: { ...roleColor, highlight: { ...roleColor.highlight }, hover: { ...roleColor.hover } },
        _role: role,
      });
    }

    // ── Build edge list ──────────────────────────────────────────────────────
    // Priority: traceroute paths (have explicit hop order + SNR).
    if (this._showTraceroutes) {
      // RouteDiscovery stores hop numbers as raw integers; canonicalise them
      // to the same '!hex' form used as node IDs in this graph (mirrors map.js).
      const toNodeId = (n) => {
        if (typeof n === 'string') {
          const s = n.trim();
          return s.startsWith('!') ? s : '!' + s;
        }
        return '!' + (n >>> 0).toString(16).padStart(8, '0');
      };

      const addPathEdges = (path, snrList) => {
        for (let i = 0; i < path.length - 1; i++) {
          const fromId = path[i];
          const toId   = path[i + 1];
          if (!fromId || !toId) continue;
          const eid = edgeId(fromId, toId);
          if (edgeSeen.has(eid)) continue;
          edgeSeen.add(eid);

          // Per-hop SNR: snr_towards[i] aligns with the hop between path[i]
          // and path[i+1]. A raw value of 127 (which the backend divides by 4 to 31.75)
          // is a special firmware flag indicating the hop travelled over MQTT.
          const hopSnr = snrList?.[i] ?? null;
          const isMqtt = hopSnr === 31.75;
          const edgeLabel = isMqtt ? 'MQTT' : (hopSnr != null ? `${hopSnr.toFixed(1)} dB` : '');
          const edgeTitle = isMqtt ? 'Traceroute hop — via MQTT (Internet)' : `Traceroute hop — SNR: ${hopSnr != null ? hopSnr.toFixed(1) + ' dB' : 'n/a'}`;
          const edgeColor = isMqtt ? '#2196f3' : snrColor(hopSnr); // Blue for MQTT

          newEdges.push({
            id:    eid,
            from:  fromId,
            to:    toId,
            label: edgeLabel,
            title: edgeTitle,
            color: { color: edgeColor, highlight: '#ffffff', hover: '#ffffff' },
            width: 2,
            dashes: isMqtt ? [2, 2] : false, // Dotted line for MQTT
            hidden: false,
            _type: 'traceroute',
          });
        }
      };

      for (const node of nodes) {
        const tr = node.traceroute;
        if (!tr || tr.timeout) continue;
        const rawRoute = Array.isArray(tr.route) ? tr.route : [];
        const rawBack  = Array.isArray(tr.route_back) ? tr.route_back : [];

        // Full forward path: self → intermediate hops → responding node.
        const forward = [state.selfId, ...rawRoute.map(toNodeId)];
        if (tr.from_id) forward.push(tr.from_id);

        // Return path (if the device reported one).
        const back = (rawBack.length && tr.from_id)
          ? [tr.from_id, ...rawBack.map(toNodeId), state.selfId]
          : [];

        if (forward.length >= 2) addPathEdges(forward, tr.snr_towards);
        if (back.length >= 2)    addPathEdges(back, tr.snr_back);
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
            hidden: false,
            _type: 'neighbor',
          });
        }
      }
    }

    // ── Diff update ──────────────────────────────────────────────────────────
    // Ensure every edge endpoint exists as a node. vis-network silently drops
    // edges whose from/to are not present in the nodes DataSet, and traceroute
    // relay hops (raw node numbers) are frequently absent from the radio's
    // bounded node DB (and therefore from state.nodes). Without this, multi-hop
    // routes would render as dangling stubs or not at all. Relay placeholders
    // use a neutral style so the route stays readable.
    const endpointIds = new Set();
    for (const e of newEdges) { endpointIds.add(e.from); endpointIds.add(e.to); }
    const knownIds = new Set(newNodes.map(n => n.id));
    for (const eid of endpointIds) {
      if (!knownIds.has(eid)) {
        knownIds.add(eid);
        newNodes.push({
          id: eid,
          label: this._showNames ? eid : '',
          title: `Relay hop — ${eid}`,
          color: { background: '#5c6bc0', border: '#9fa8da', highlight: { background: '#ffffff', border: '#9fa8da' }, hover: { background: '#ffffff', border: '#9fa8da' } },
          shape: 'dot',
          size: 10,
          opacity: 0.8,
          _originalLabel: eid,
          _originalColor: { background: '#5c6bc0', border: '#9fa8da', highlight: { background: '#ffffff', border: '#9fa8da' }, hover: { background: '#ffffff', border: '#9fa8da' } },
          _role: 'RELAY',
        });
      }
    }

    const currentNodeIds = new Set(this._nodesDS.getIds());
    const currentEdgeIds = new Set(this._edgesDS.getIds());

    // Build a lookup of current vis node data by id for label comparison
    const currentNodesById = {};
    this._nodesDS.forEach(n => { currentNodesById[n.id] = n; });

    const nodesToAdd    = [];
    const nodesToUpdate = [];

    for (const n of newNodes) {
      if (!currentNodeIds.has(n.id)) {
        nodesToAdd.push(n);
      } else {
        // Force update if label or tooltip changed — vis merge can miss this
        const existing = currentNodesById[n.id];
        if (existing.label !== n.label || existing.title !== n.title || existing._originalLabel !== n._originalLabel) {
          nodesToUpdate.push(n);
        }
      }
    }
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

    // Force redraw so label/tooltip changes render immediately
    if (nodesToUpdate.length || edgesToUpdate.length) {
      this._network?.redraw();
    }

    // Apply search highlight if active
    this._applySearchHighlight();

    // Show empty state if no nodes.
    if (nodes.length === 0) {
      this._showError('No nodes visible. Connect to your Meshtastic node to populate the graph.');
    } else {
      this._clearError();
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
      const currentLabel = node._originalLabel || node.label || '';
      const label = this._showNames ? currentLabel : '';
      if (node.label !== label) {
        updates.push({ id: node.id, label });
      }
    });
    if (updates.length) this._nodesDS.update(updates);
    if (updates.length) this._network?.redraw();
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
      const targetColor = matches
        ? (node._originalColor || { background: '#00d4aa', border: '#4dd0c4' })
        : { background: '#444', border: '#666', opacity: 0.4 };
      const targetFontColor = matches ? '#e8eaf6' : '#666';
      if (
        node.color?.background !== targetColor.background ||
        node.color?.border !== targetColor.border ||
        node.color?.opacity !== targetColor.opacity ||
        node.font?.color !== targetFontColor
      ) {
        updates.push({
          id: node.id,
          color: targetColor,
          font: { ...node.font, color: targetFontColor },
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
