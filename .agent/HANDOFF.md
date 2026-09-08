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
- [`ROADMAP.md`](file:///home/garethmo/Documents/GitHub/NodePulse/ROADMAP.md) — planned features with ✅/🔜/💡 status

---

## Current Git State (as of handoff)

```
HEAD (main, not yet pushed):  0560373  feature: Check and remove node from radio device when deleting
origin/main:                  cd9f32d  feature: Pixel-accurate parallel traceroute separation for shared routes
```

**The local `main` is 1 commit ahead of `origin/main` — not yet pushed.**

### Uncommitted working-tree changes (NOT yet committed)

These changes are complete and tested. They should be committed next:

| File | What changed |
|---|---|
| `nodepulse-addon/config.json` | Version bump to 1.26.0 |
| `custom_components/nodepulse/manifest.json` | Version bump to 1.26.0 |
| `CHANGELOG.md` | Release notes for 1.26.0 |
| `nodepulse-addon/CHANGELOG.md` | Release notes for 1.26.0 and 1.25.0 |
| `nodepulse-addon/app/connection.py` | Stale node clearing / delete fixes |
| `nodepulse-addon/app/routes.py` | Stale node clearing route support |
| `nodepulse-addon/tests/conftest.py` | Updated mock signature for clear_stale_nodes |
| `nodepulse-addon/tests/unit/test_connection.py` | New stale/delete tests |
| `nodepulse-addon/tests/unit/test_routes.py` | Fixed query-param test |
| `nodepulse-addon/web_ui/css/main.css` | `node-toolbar-select` CSS class |
| `nodepulse-addon/web_ui/index.html` | Added `bulk-remove-stale` dropdown + refactored signal filter to use CSS class |
| `nodepulse-addon/web_ui/js/api.js` | Fixed orphaned JSDoc; added correct doc to `clearStaleNodes` |
| `nodepulse-addon/web_ui/js/app.js` | Fixed bulk-remove handler (preview count, early return, pollData refresh) |
| `.agent/HANDOFF.md` | Agent handoff documentation |

---

## Session Work Done (2026-09-08)

### Feature audited: **Bulk Remove Stale Nodes** (dropdown in Nodes toolbar)

The feature removes nodes from the persistent store (and optionally from the radio's NodeDB) that haven't been heard within a chosen time window (15 / 30 / 60+ days).

**End-to-end flow:**
1. **UI**: `<select id="bulk-remove-stale">` dropdown in the Nodes toolbar (`index.html` line ~271)
2. **JS handler**: `bulkRemoveStale` listener in `app.js` (~line 2490)
3. **API client**: `clearStaleNodes(days)` in `api.js` → `POST /api/nodes/clear-stale?days=N`
4. **Route**: `handle_clear_stale_nodes` in `routes.py` (line 424)
5. **Backend**: `conn.clear_stale_nodes(days)` → `_clear_stale_nodes_sync` in `connection.py` (line 448)
   - Iterates `self._nodes`, collects IDs where `age >= threshold` OR `stale=True` with no `last_heard`
   - Skips the local gateway node
   - Calls `_delete_node_sync` for each (which also evicts from the radio if present)

### Bugs fixed in this session

#### 1. `api.js` — Orphaned JSDoc
Old `clearStaleNodes` doc comment was accidentally left floating above `fetchPositionHistory` (not above the function). Both functions now have their own correct JSDoc.

#### 2. `app.js` — Map markers not refreshed after bulk remove
Handler called `fetchNodes()` + `renderNodesGrid()` + `renderNodeList()` but skipped `dashMap.updateNodes()` / `fullMap.updateNodes()`. Deleted nodes stayed as ghost markers on the map until the next poll.  
**Fix:** replaced the manual calls with `await pollData()` — same as the Settings button does.

#### 3. `app.js` — Misleading confirm when 0 nodes match
If the user selected "15+ days" but all nodes were recently heard, the handler showed a vague `"Remove all stale nodes…"` confirm dialog.  
**Fix:** now shows an `info` toast — `"No nodes last heard 15+ days ago — nothing to remove."` — and resets the dropdown without opening the confirm.

#### 4. `test_routes.py` — Failing test (latent bug)
`test_handle_clear_stale_nodes_with_query_param` embedded `?days=30` in the path string, but `make_request()` never parses the path — it reads `request.query` from a separate dict. The handler never saw `days`, omitted it from the response, and the assertion failed.  
**Fix:** test now passes `query={"days": "30"}` to `make_request()`.

### Test results (all passing)
```
tests/e2e/test_api.py::test_clear_stale_nodes                              PASSED
tests/unit/test_connection.py::TestClearStaleNodes::test_clear_stale_nodes PASSED
tests/unit/test_connection.py::TestClearStaleNodes::test_clear_stale_nodes_sync_all_legacy PASSED
tests/unit/test_connection.py::TestClearStaleNodes::test_clear_stale_nodes_sync_with_days_filter PASSED
tests/unit/test_connection.py::TestClearStaleNodes::test_clear_stale_nodes_sync_protects_gateway_node PASSED
tests/unit/test_routes.py::TestHandleClearStaleNodes::test_handle_clear_stale_nodes_success PASSED
tests/unit/test_routes.py::TestHandleClearStaleNodes::test_handle_clear_stale_nodes_with_query_param PASSED
tests/unit/test_routes.py::TestHandleClearStaleNodes::test_handle_clear_stale_nodes_error PASSED
```

---

## Immediately Pending Actions

1. **Commit the working tree** — all changes above are tested and ready:
   ```bash
   git add -A
   git commit -m "feature: Bulk remove stale nodes — dropdown UI, route, backend, tests"
   ```
2. **Push both commits** to origin:
   ```bash
   git push origin main
   ```

---

## Next Features to Build (from ROADMAP.md)

These are the open items with the most value, in rough priority order:

### 🔜 High confidence (scoped)

| Feature | Where to build | Notes |
|---|---|---|
| **Node health scoring** | `connection.py` (new method) + `routes.py` (new GET endpoint) + `app.js` (render badge on node card) | Aggregate SNR + battery + uptime into a single `excellent/good/fair/poor` score. Parallel to `get_node_signal`. |
| **E2E test suite** | `nodepulse-addon/tests/e2e/` | pytest + Playwright. Some e2e API tests already exist in `tests/e2e/test_api.py` — extend them. |
| **i18n / multi-language** | `web_ui/` | Localise all UI strings. No framework yet — a simple `t()` lookup dict approach would fit the codebase style. |

### 💡 Good ideas (need design)

| Feature | Notes |
|---|---|
| **Bulk remove mode (manual select)** | A *different* feature from the stale-by-age dropdown. Toggle button enters "selection mode" — checkboxes on each node card, floating "Delete N selected" action bar, then calls `DELETE /api/node/{id}` per selected node or a new `POST /api/nodes/delete-batch` endpoint. |
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

---

## Architecture Notes

### Node deletion flow (important — recently changed)
`_delete_node_sync(node_id)` in `connection.py`:
1. Parses hex node ID → integer node_num
2. Refuses if the target is the local gateway node
3. Checks if node is on the physical radio (`iface.nodes`, `iface.nodesByNum`, `_lookup_node`)
4. If on device: calls `iface.removeNode(node_num)` and evicts from in-memory dicts
5. Removes from `self._nodes` (persistent store)
6. Cleans up: favorites, tags, traceroutes, position history

### `pollData()` is the canonical refresh
Always call `await pollData()` after mutations that affect the node list. It refreshes:
- Status bar
- Node list sidebar
- Nodes grid
- **Both map instances** (`dashMap` and `fullMap`)
- Topology graph (if visible)
- Charts

Do NOT call `fetchNodes()` + manual renders separately — you'll miss the maps.

### Request mock pattern in tests
`make_request()` in `tests/unit/test_routes.py` takes a `query` dict (not a path string).  
Always pass query params like: `make_request(query={"days": "30"})` — never embed `?key=val` in `path`.
