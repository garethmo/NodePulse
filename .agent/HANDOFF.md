# NodePulse — Agent Handoff

> **Read this first when starting a new session.**  
> Updated: 2026-09-08 · Branch: `main`

---

## Project Summary

NodePulse is a **Home Assistant addon + custom integration** for Meshtastic mesh radio networks.

| Layer | Location | Language |
|---|---|---|
| Addon backend (FastAPI-style aiohttp) | `nodepulse-addon/app/` | Python 3.10+ |
| Web UI (SPA, no framework) | `nodepulse-addon/web_ui/` | Vanilla JS (ES modules) + CSS variables |
| HA integration | `custom_components/nodepulse/` | Python |
| Unit + E2E tests | `nodepulse-addon/tests/` | pytest (run from `nodepulse-addon/`) |

**Run tests** from `nodepulse-addon/`:
```bash
python3 -m pytest tests/ -v
```

**Key files to know:**
- [`nodepulse-addon/app/connection.py`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/app/connection.py) — all Meshtastic radio logic, node store, delete/clear methods
- [`nodepulse-addon/app/routes.py`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/app/routes.py) — all API route handlers
- [`nodepulse-addon/app/main.py`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/app/main.py) — route registration (`app.router.add_*`)
- [`nodepulse-addon/web_ui/js/api.js`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/web_ui/js/api.js) — all HTTP calls to the backend (one function per endpoint)
- [`nodepulse-addon/web_ui/js/app.js`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/web_ui/js/app.js) — single-page app: state, rendering, event wiring
- [`nodepulse-addon/web_ui/index.html`](file:///home/garethmo/Documents/GitHub/NodePulse/nodepulse-addon/web_ui/index.html) — single HTML shell
- [`ROADMAP.md`](file:///home/garethmo/Documents/GitHub/NodePulse/ROADMAP.md) — planned features with checkmarks/status

---

## Current Git State (as of handoff)

```
HEAD (main):  3565b0a  fix: node deduplication and co-located map marker separation
origin/main:  8c486c2  feature: Ensure map node labels are clickable to open popup dialogue
```

> Local `main` is **1 commit ahead** of `origin/main` (`3565b0a`). There are also **unstaged changes** from this session ready to commit (see Immediately Pending Actions).

### Recent Features Implemented

1. **Non-overlapping Co-located Node Labels** — labels stack vertically for co-located nodes
2. **Bulk Remove Stale Nodes & Device Eviction** (v1.26.0)
3. **Check and Remove Node from Radio Device when Deleting**
4. **Node deduplication & co-located map marker separation** (commit 3565b0a)
5. **Duplicate same-node co-located map marker resolution** (unstaged — this session)

---

## Session Work Done (2026-09-08)

### Bug fixed: Multiple of the same nodes appearing on the map in the same location

**User report**: *"i see multiple of the same nodes on the map in the same location this shouldnt happen it should only show one node and its location fix this"*

**Root cause analysis**:
1. When nodes had previously been saved to `/data/nodes.json` under an earlier ID, reflash, or unnormalized format, the stale re-injection loop in `_get_nodes_sync()` re-injected the stale record as a distinct node even when the active radio node (or local gateway node) was live with the exact same name and coordinates.
2. The Leaflet map renderer (`map.js` `updateNodes`) clustered all nodes within 25m into vertical offset stacks. When duplicate records existed for the same physical node (or stale ghost + active node, or multiple entries for the gateway node), it fanned out multiple separate marker icons and labels for what is physically a single node at that location.
3. Node IDs in `_load_nodes`, `_capture_position`, and frontend deduplication did not guard against co-located identical-name duplicate nodes.

**Fixes applied**:
1. **`connection.py`**:
   - Added `_is_co_located(lat1, lon1, lat2, lon2, threshold_km=0.025)` helper function.
   - Updated `_load_nodes()` to collapse co-located entries with identical names upon startup loading.
   - Updated `_capture_position()` to match destinations using `normalize_node_id`.
   - In `_get_nodes_sync()`, the stale re-injection loop checks for live active nodes or the local gateway node sharing the same name and location, skipping and pruning stale duplicate ghosts.
   - In `_get_nodes_sync()`, final pass deduplicates co-located nodes sharing identical names, keeping the active/newest node.
2. **`app.js`**:
   - Updated `pollData()` to filter out stale duplicate ghosts that share the same name and location with an active node.
3. **`map.js`**:
   - In `updateNodes()`, deduplicates `rawGpsNodes` into `gpsNodes` by collapsing any co-located nodes sharing the same location (within 25m) and identical names (or sharing the location with the gateway `_selfId`). Only one marker and location is retained.
   - Marker cleanup removes any old markers from Leaflet when their node was deduplicated or removed.
4. **`test_connection.py`**:
   - Added unit tests: `test_get_nodes_sync_skips_stale_duplicate_of_live_node` and `test_get_nodes_sync_deduplicates_colocated_same_name`.

### Test results (all passing)
```
539 passed, 14 skipped, 0 failed in 18.77s
```

---

## Immediately Pending Actions

1. **Commit the unstaged changes** from this session:
   ```bash
   git add nodepulse-addon/app/connection.py nodepulse-addon/web_ui/js/map.js nodepulse-addon/web_ui/js/app.js nodepulse-addon/tests/unit/test_connection.py .agent/HANDOFF.md
   git commit -m "fix: collapse duplicate same-node markers on map to a single node and location"
   ```
2. **Push all commits** to origin (2 local commits ahead after step 1):
   ```bash
   git push origin main
   ```

---

## Next Features to Build (from ROADMAP.md)

These are the open items with the most value, in rough priority order:

### High confidence (scoped)

| Feature | Where to build | Notes |
|---|---|---|
| **Node health scoring** | `connection.py` (new method) + `routes.py` (new GET endpoint) + `app.js` (render badge on node card) | Aggregate SNR + battery + uptime into a single `excellent/good/fair/poor` score. Parallel to `get_node_signal`. |
| **E2E test suite** | `nodepulse-addon/tests/e2e/` | pytest + Playwright. Some e2e API tests already exist in `tests/e2e/test_api.py` — extend them. |
| **i18n / multi-language** | `web_ui/` | Localise all UI strings. No framework yet — a simple `t()` lookup dict approach would fit the codebase style. |

### Good ideas (need design)

| Feature | Notes |
|---|---|
| **Bulk remove mode (manual select)** | Toggle button enters "selection mode" — checkboxes on each node card, floating "Delete N selected" action bar, then calls `DELETE /api/node/{id}` per selected node or a new `POST /api/nodes/delete-batch` endpoint. |
| **Remote channel administration** | View + manage channel config (names, keys, PSK) from the Web UI. Builds on the existing remote admin infrastructure in `remote_admin.py`. |
| **Push notifications (Apprise)** | Email / Slack / Discord / Telegram alerts on mesh events. Python `apprise` library. |
| **Reboot-pending auto-clear** | Detect node reboot from reconnect / uptime reset and auto-dismiss the "reboot required" banner. |

---

## Coding Rules (from `.agent/` and user rules)

- **Readability > cleverness** — SOLID, DRY, KISS
- **Explicit over implicit** — no magic one-liners, descriptive names
- **Low-cardinality logging** — `logger.info({"id": nid}, "Msg")` style, stable message strings
- **CSS**: vanilla CSS / CSS variables, no inline styles, no Tailwind
- **No** inline styles except for truly dynamic values
- **Error handling**: always handle and log; routes return `_error_response(...)` on exception
- **Tests**: run from `nodepulse-addon/` with `python3 -m pytest tests/ -v`
- Verify locally before asking for review
