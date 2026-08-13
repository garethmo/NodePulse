# NodePulse vs MeshMonitor — Gap Analysis & Development Plan

A feature-by-feature comparison of NodePulse against
[MeshMonitor](https://meshmonitor.org/) (v4.x, [`yeraze/meshmonitor`](https://github.com/Yeraze/meshmonitor)),
identifying what NodePulse is missing and a prioritised development plan.

> Positioning note: the two tools have different philosophies. MeshMonitor is a
> **standalone multi-protocol mesh monitor** (Node.js/React, SQLite/Postgres/MySQL,
> its own auth, broker, and desktop apps). NodePulse is a **Home Assistant addon**
> whose core value is deep HA integration (entities, device trackers, notify
> platform, services, automations, logbook, ingress). The plan below borrows
> MeshMonitor's *standalone* strengths selectively and skips what HA already
> provides (see "Deliberately out of scope").

---

## Comparison

Legend: ✅ NodePulse has it · 🔶 partial ❌ NodePulse missing it.

### Protocols & Connectivity

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Meshtastic TCP | ✅ | ✅ | NodePulse: direct or HA proxy |
| Meshtastic Serial/USB (bridge) | ✅ | ❌ | NodePulse is TCP-only |
| Meshtastic BLE (bridge) | ✅ | ❌ | MeshMonitor ships a BLE bridge container |
| MeshCore (companions/repeaters) | ✅ | ❌ | Second protocol ecosystem |
| MQTT as a source | ✅ | 🔶 | NodePulse bridges mqtt.meshtastic.org; no local broker |
| Embedded MQTT broker | ✅ | ❌ | NodePulse has a bridge module, not a broker |
| Multi-source (simultaneous) | ✅ | ❌ | NodePulse: single configured connection |
| Virtual node endpoint (phone clients) | ✅ | ❌ | Let Meshtastic mobile apps connect via the gateway |
| Receive-only / passive mode | ✅ | ❌ | Shut off all TX |
| Connection passthrough/filtering | ✅ | 🔶 | Portnum allowlist, node blocklist, geo bbox exist |

### Messaging & Channels

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Channel messaging + DM | ✅ | ✅ | |
| Multi-channel views | ✅ | ✅ | |
| Message send status (ack/fail) | ✅ | ✅ | |
| **Full-text search across all conversations** | ✅ | ❌ | NodePulse: per-thread filter only |
| Message export (JSON/CSV) | ✅ | ✅ | |
| Message reactions / tapbacks | ✅ | ❌ | |
| Message replies / threading | ✅ | ❌ | |
| Drag-and-drop conversation reorder | ✅ | ❌ | Minor |
| **Scheduled / cron messages** | ✅ | ❌ | announced on a timer |
| **Store & Forward (S&F)** | ✅ | ❌ | query peers for missed history; flag S&F servers on map |
| Contact share QR / URL | ✅ | ❌ | generate `meshtastic.org/v/#` contact links |

### Maps & Geography

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Live node map, signal-coloured markers | ✅ | ✅ | NodePulse: role-coloured + permanent labels |
| Topology / traceroute overlays | ✅ | ✅ | |
| KML/GPX export | ✅ | ✅ | |
| GeoJSON / KML / KMZ zone imports | ✅ | ❌ | MeshMonitor: zones + overlays; NodePulse: ruler only |
| Custom tile servers (own XYZ/tiles) | ✅ | ❌ | NodePulse: single CartoDB dark layer |
| Map tileset selection (satellite/topo) | ✅ | ❌ | |
| Polar RF grid overlay | ✅ | ❌ | |
| Terrain link analysis (LOS / Fresnel) | ✅ | ❌ | Map Analysis tool with DEM elevation source |
| 3D terrain view | ✅ | ❌ | MapLibre pitched terrain |
| Position estimation (non-GPS nodes) | ✅ | ❌ | estimate fixes from neighbours/relay data |
| Estimated-accuracy regions | ✅ | ❌ | |
| Embed maps (public iframe) | ✅ | ❌ | |
| Waypoints | ✅ | ✅ | |
| Trail polylines | ✅ | ✅ | NodePulse: position-history trails |

### Network Insight / Analytics

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| SNR/RSSI/battery/telemetry charts | ✅ | ✅ | |
| Channel / airtime utilisation | ✅ | ✅ | |
| **Link Quality score (0–10)** | ✅ | ❌ | hop-stability + traceroute-failure derived |
| **Smart Hops history (min/avg/max)** | ✅ | ❌ | rolling 24 h hop analysis |
| Signal trend badge (24 h vs 7 d) | ✅ | ❌ | improving/stable/degrading |
| Noise-floor telemetry | ✅ | ❌ | |
| Telemetry widgets (drag-and-drop dashboard) | ✅ | ❌ | |
| **Analytics & reports workspace** | ✅ | ❌ | `/reports`, cross-source |
| **Solar monitoring + battery forecast** | ✅ | ❌ | forecast.solar + at-risk auto-detection |
| Node health score | 🔶 | ❌ | NodePulse roadmap item, not shipped |

### Automation & Alerts

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| **Auto-acknowledge** | ✅ | ❌ | pattern-matched responses |
| **Auto-responder** (text/HTTP/script) | ✅ | 🔶 | NodePulse: static "welcome" DM only |
| **Auto-traceroute** (schedule/on-discovery) | ✅ | ❌ | NodePulse: manual dispatch |
| **Scheduled messages (cron/interval)** | ✅ | ❌ | |
| **Auto-announce** | ✅ | ❌ | |
| **Auto-ping** (DM-triggered sessions) | ✅ | ❌ | |
| **Auto-welcome** | ✅ | 🔶 | |
| **Auto time-sync (admin)** | ✅ | ❌ | |
| **Remote admin scanner** | ✅ | ❌ | discover admin-capable nodes |
| **Geofence triggers** | ✅ | ❌ | enter/exit zone actions |
| **Automation Engine** (condition/action) | ✅ | ❌ | plus Python/Bash/Node script responses |
| Mailbox / dead-drop (asynchronous) | ✅ | ❌ | "mesh voicemail" |
| Airtime-usage cutoff (auto-mute bots) | ✅ | ❌ | pause automation when mesh is busy |
| **Push notifications (Web Push + Apprise)** | ✅ | ❌ | email/Slack/Discord/Telegram/desktop |
| Inactive-node / low-battery alerts | ✅ | 🔶 | NodePulse: HA-side automations can do this |

### Remote Administration & Security

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Device configuration UI | ✅ | ✅ | NodePulse: `Configure` tab (1.10+) |
| Channel administration (names/keys/PSK) | ✅ | ❌ | read-only channel list only |
| **Admin commands (remote admin protocol)** | ✅ | ❌ | reboot, set config, etc. over the mesh |
| **Firmware OTA push** | ✅ | ❌ | NodePulse roadmap idea |
| **Security scanner** | ✅ | ❌ | weak/duplicate encryption key detection |
| Impersonation detection | ✅ | ❌ | |
| PKI DM decryption | ✅ | ❌ | |
| Security filter / warnings in UI | ✅ | ❌ | |

### Administration, Auth & Ops

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Local accounts | ✅ | ❌ | rely on HA auth/ingress |
| OIDC/SSO, MFA | ✅ | ❌ | |
| Per-source permissions / RBAC | ✅ | ❌ | |
| Admin audit log | ✅ | ❌ | |
| Bearer-token REST API (v1) | ✅ | ❌ | NodePulse API is open behind ingress |
| System backup & restore (in-app) | ✅ | 🔶 | HA snapshots cover it |
| One-click self-upgrade | ✅ | 🔶 | HA addon store covers it |
| Multi-database (PostgreSQL/MySQL) | ✅ | ❌ | NodePulse: JSON persistence (fine at HA scale) |
| Desktop apps (Windows/macOS) | ✅ | ❌ | |
| PWA + system tray | ✅ | ❌ | |

### UX / Appearance

| Feature | MeshMonitor | NodePulse | Notes |
|---------|:-----------:|:---------:|-------|
| Dark / light theme | ✅ | ✅ | NodePulse: two themes |
| 15+ themes + visual editor, WCAG | ✅ | ❌ | |
| **i18n / translations (Weblate)** | ✅ | ❌ | NodePulse roadmap item |
| Unit preferences (temp/distance/time/date) | ✅ | ❌ | |
| Mobile responsive | ✅ | ✅ | |
| Smart node list filters (role/hidden/unknown) | ✅ | ❌ | NodePulse: text, hops, heard-window |

### Where NodePulse *exceeds* MeshMonitor

- Full **Home Assistant integration** — per-node sensors (19 types), device trackers
  (`device_tracker.nodepulse_*`), notify platform, services, device actions/triggers,
  logbook entries, HA entity tracking toggle.
- **Telegram bot** (bidirectional, commands, channel relay).
- Native **HA ingress** — no separate auth/ports to manage.
- **MQTT outbound forwarding + mesh injection** through the HA MQTT ecosystem.
- Traceroute/position-request dispatch wired straight into HA automations.

---

## Missing Features — Prioritised Development Plan

Priorities are weighted by value-per-effort for NodePulse's HA-centric positioning.
**P1** ships in the next release cycle; **P2** within a quarter; **P3** backlog.

### Phase 1 — Analytics & Insight (P1, high value, low risk)

Pure addons to existing data pipelines; no new transports or creds.

| # | Feature | Backend | Web UI | HA Integration |
|---|---------|---------|--------|----------------|
| 1.1 | **Link Quality score (0–10)** | Persist hop-count per message (`hopStart−hopLimit`); compute LQ event model (stabilised routing +1, degraded −2, failed traceroute −2, crypto error −5, clamp 0–10) | Per-node score + trend chart in Nodes view | New `_node_*_link_quality` sensor + signal-trend attribute |
| 1.2 | **Smart Hops (min/avg/max)** | Rolling 24 h hop histogram per node, 15-min buckets | Chart alongside LQ in Nodes view | Optional attribute on traps sensor |
| 1.3 | **Signal trend badge** | Store RSSI/SNR history; compare last 24 h vs 7-day baseline | ▲/→/▼ badge on node cards | Sensor attribute |
| 1.4 | **Noise floor capture** | Persist `local_stats` noise floor from telemetry | Show in popup/settings | New telemetry sensor |
| 1.5 | **Node health score** | Aggregate SNR, RSSI, battery, LQ, uptime → 0–100 | Card header badge + colour | `_node_*_health` sensor |

**Estimated effort:** ~2–3 sprints. No new dependencies.

### Phase 2 — Automation (P1, biggest feature gap)

NodePulse's only automation today is a **static new-node welcome DM**
(connection.py:2243–2255 — fires when an unknown node is first discovered and
calls `_send_message_sync` on a bare thread). This phase replaces that ad-hoc
hook with a small scheduling + trigger engine like MeshMonitor's, covering
timed sends, event triggers, and notifications — without growing into a
full-blown automation platform (HA itself is that platform).

#### 2.0 Architecture — the engine

New module `nodepulse-addon/app/automation.py`, lifecycle-managed by
`main.py` exactly like `MqttBridge`/`TelegramBot` (constructed in
`_on_startup`, `await .start()` on an asyncio task, `await .stop()` on
shutdown).

```
                    asyncio.Task (top-level loop)
                               │
                ┌──────────────┼──────────────┐
                │              │              │
           Scheduler      TriggerBus      MonitorLoop
        (cron+interval)   (mesh events)   (health/push)
                │              │              │
                └──────┐       └──────┐       └──────┐
                       ▼              ▼              ▼
                 ┌────────────────────────────────────────┐
                 │            AutomationRunner            │
                 │  evaluates  enabled → matches? → act   │
                 │  respects  airtime gate + cooldowns    │
                 └──────┬─────────────────────────┬───────┘
                        ▼                         ▼
              ┌──────────────────┐      ┌──────────────────┐
              │ connection.py    │      │ notifications.py │
              │ send_message /   │      │ push + Apprise   │
              │ request_tracer.  │      └──────────────────┘
              └──────────────────┘
```

Key design decisions, grounded in current code:

- **Single send path.** Every automation output goes through the *existing*
  `Connection.send_message()` (connection.py:429) and
  `request_traceroute()` (connection.py:449), never through bare
  `_send_message_sync` threads. This keeps the radio serialization lock
  (`_lock`), the pending-traceroute queue, ACK tracking, and message
  capture/dedup intact. The old welcome hook is deleted in favour of an
  `AutoWelcome` trigger.
- **Event sources feed the trigger bus.** The bus is a callback list that
  `connection.py` publishes into from its existing handlers — `onTextPacket`
  (message received), `onNodeInfo` (new node discovered), `onPositionPacket`
  (geofence evaluation), `onTracerouteComplete`. No new packet plumbing is
  needed; we only add one-line hook calls at known sites.
- **Definitions persisted as JSON.** New `automation.json` file holding all
  configured automations (see schema below), loaded on start, atomic-write on
  change, alongside `messages.json`/`channels.json`.
- **Airtime gate is global, evaluated before every action.** Automation emits
  nothing when the mesh is congested (uses channel-utilization/air-util from
  the existing telemetry) — MeshMonitor's "pause bots when the mesh is busy"
  behaviour, Table 2.7.
- **Cooldowns per automation + per node** prevent reply storms (MeshMonitor's
  per-node cooldown and 24 h welcome spam protection).

#### 2.1 Scheduled messages & auto-announce

**Backend (`Scheduler`):**
- Persist a list of jobs: `{ id, name, schedule, channel, target, template, enabled }`.
- Two schedule types: **interval** (`every: {seconds}`) and **cron**
  (`cron: "0 */6 * * *"`, 5-field; validate with a standard parser — no new
  deps, a ~30-line validator + `datetime` next-fire computation suffices).
- Scheduler loop wakes every second, computes next-fire per job, and fires
  via `AutomationRunner`. Support `announce_on_start` (send once on addon boot,
  with a 1-hour spam guard) and a manual **Send Now** (mapped to a REST
  endpoint for the UI button).
- Template expansion per send: `{VERSION}`, `{DURATION}`, `{NODECOUNT}`,
  `{DIRECTCOUNT}`, `{DATE}`, `{TIME}`, `{FEATURES}` (Table 2.9).

**Web UI (Automation tab, "Scheduled" section):** list of jobs with
channel selector, cron text field with live validation (green check / red
error), target selector (broadcast | channel | specific node), message
textarea with token picker, Enable toggle, Send-Now button, Delete.

**Effort:** ~1 sprint for the scheduler + interval jobs; +0.5 sprint for cron
and Send Now.

#### 2.2 Auto-traceroute

**Backend:**
- Job type `traceroute`: interval (configurable 1–60 min, default 3) + a node
  filter (all | specific node list | new-node-only | role-based).
- Implementation calls `Connection.request_traceroute()` in a throttled loop
  (one at a time — the existing serialization lock already serializes
  dispatches; spread round-robin so the mesh never gets a burst). Respect the
  airtime gate; cover mobile/battery nodes via a "skip if battery < X%" option.
- Captures new discovered routes automatically through the existing
  `_capture_traceroute` path — no new persistence work.

**Web UI:** interval + node-selection multiselect (reuse the node picker
pattern from `device_config.js`).

**Effort:** ~1 sprint.

#### 2.3 Auto-acknowledge

**Backend:**
- Trigger on **message received** (the existing onTextPacket handler site).
- Per-automation config: regex pattern (default `^(test|ping)`, case-insens.),
  channel scoping (whitelist of channel indices and/or DM-only), response
  template, response mode (`text` | `tapback` | both), DM-vs-broadcast choice
  (`always_dm` — don't clutter a channel), pre-send delay (0–120 s), resend
  attempts for unacked DMs (1–3).
- **Tapbacks** reuse `sendText` with an emoji payload (reaction) — no new
  protobuf work; a tapback is just a short message.
- Template tokens from the triggering packet: `{LONG_NAME}`, `{SHORT_NAME}`,
  `{NODE_ID}`, `{SNR}`, `{RSSI}`, `{HOPS}`, `{LAST_HOP}`, `{TIME}`, `{DATE}`,
  `{MESSAGE}` (Table 2.9). Direct vs multi-hop variants supported.

**Web UI:** pattern field with live "test message" box (highlight matches),
template editor with token insertion buttons and preview, channel checkboxes,
mode/delay toggles.

**Effort:** ~1 sprint.

#### 2.4 Auto-responder (generalised)

**Backend:**
- Replace the current static welcome hook with a trigger type `responder`:
  list of triggers `{ pattern, response_type, response, cooldown }`.
- Pattern matching with parameter capture: `weather {location}` extracts
  `location`; optional regex per param (`temp {value:\d+}`).
- Response types (in order of effort):
  1. **text** — template with `{param}` + token expansion (+ optional multiline
     split > ~180 bytes).
  2. **http** — `httpx` GET/POST webhook with URI-encoded params
     (`{location}`, `{NODE_ID}`, `{SNR}`…). aiohttp already present.
  3. **script** — run an executable in `/data/scripts/` (`.py`/`.sh`/`.js`)
     with env-vars/args, parse stdout for one or more reply lines. Sandboxed,
     enforced timeout (30 s), wrap in `asyncio.to_thread`.
  4. **mailbox (dead-drop)** — "mesh voicemail": DM commands `msg <name> <text>`
     / `inbox` / `inbox play` / `inbox delete <id>` / `inbox clear` store and
     release messages against a new `automation.json` store (7-day expiry,
     180-byte bodies, cap 20 pending per recipient). Pure backend + template
     logic, no protobuf work.

**Web UI:** trigger list with add/edit/delete, pattern field, response-type
select, response textarea/URL/script-path, cooldown, per-channel scoping,
"test trigger" preview.

**Effort:** text ≈ 0.5 sprint; http ≈ 1; script ≈ 1.5; mailbox ≈ 2.

#### 2.5 Auto-welcome & auto-ping

- **Auto-welcome**: trigger on **new node discovered** (the site of the old
  welcome hook at connection.py:2243). Channel scope (primary/all), max-hops
  filter (0–7), template tokens `{LONG_NAME}`, `{NODE_ID}`, `{DATE}`, `{TIME}`,
  `{VERSION}`, 24 h per-node cooldown. Back-compatible: if a user keeps the
  existing `auto_responder_enabled/message` options, the UI migrates them into
  an Auto-welcome automation on first save.
- **Auto-ping**: DM commands `ping N` / `ping stop` trigger N NODEINFO
  request/ack pings at a configured interval, collate ACK/NAK/timeout, DM a
  summary back. Uses the existing ack tracking in `connection.py`. One session
  per node; enforce max pings (default 20) to avoid airtime abuse.

**Effort:** welcome ≈ 0.5 sprint; auto-ping ≈ 1.

#### 2.6 Geofence triggers

**Backend:**
- Persist zones: `{ id, name, shape: circle|polygon, center/radius or points, trigger_events: [enter, exit] }`.
- Evaluated on **position updates** (the POSITION_APP handler in
  `connection.py`), not on poll. Point-in-polygon via a small util (ray-casting
  ~20 lines; no geo lib needed at HA scale).
- Actions: send mesh message / DM, fire an **HA event** (`nodepulse_geofence`
  with `{zone, node_id, event}` consumed via the custom component), or push a
  notification. `enter`/`exit` hysteresis (configurable gap distance) stops
  flutter at the boundary.

**Web UI:** zone list with circle (click map for centre + radius) or polygon
(click map for vertices — reuse the map click plumbing from the Ruler),
action picker.

**Effort:** ~2 sprints.

#### 2.7 Airtime gate (cross-cutting)

- Global read of `channel_utilization` / `air_util_tx` (> available on every
  node and the local node) → if `channel_util` > threshold (default 75%) all
  automation actions are paused until it drops below hysteresis (65%).
- Exposed as a status flag in `/api/status` and an "Automation suspended
  (mesh busy)" badge in the UI. Refuses queued sends with a clear log line.

**Effort:** ~0.5 sprint.

#### 2.8 Push notifications

**Backend:**
- New `notifications.py` alongside the engine. Two transport options:
  1. **Apprise** (add `apprise` to `requirements.txt`) → email/Slack/Discord/
     Telegram/telegram bot/desktop/etc. URLs configured per automation action.
  2. **HA notify** — reuse the existing HA integration's `notify.mesh_*`
     plumbing concepts but for local alerts; simplest: drop a JSON event on
     `homeassistant` via the existing `ha_base_url` webhook pattern (already in
     config) and let HA notify automations handle delivery.
- Standard triggers: node offline (last_heard > threshold), low battery
  (< threshold), new node detected, message keyword match, geofence enter/exit.
  All monitored on the existing poll cadence (no new polling infra) + the
  trigger bus for new-node/keyword.

**Web UI:** per-automation notification toggle + destination URL; settings
section for Apprise service URL and offline/low-battery thresholds.

**Effort:** HA-notch transport ≈ 1 sprint; Apprise ≈ 1.5.

#### 2.9 Template token engine (shared)

Single renderer consumed by all scheduled/responder/ack/welcome/ping actions:

| Token | Source | Example |
|-------|--------|---------|
| `{HOPS}` / `{NUMBER_HOPS}` | triggering packet hop count | `3` |
| `{RABBIT_HOPS}` | 🎯 direct / 🐇×hops | `🐇🐇🐇` |
| `{LONG_NAME}` | sender node | `Alice's Node` |
| `{SHORT_NAME}` | sender node | `ALI` |
| `{NODE_ID}` / `{NODEID}` | sender node | `!a1b2c3d4` |
| `{MESSAGE}` | original text | `ping` |
| `{SNR}` / `{RSSI}` | sender metrics | `7.5` / `-95` |
| `{LAST_HOP}` | relay short name → hex → unknown | `RLY1` |
| `{CHANNEL}` | packet channel index | `0` |
| `{DATE}` / `{TIME}` | render time | `8/12/2026` / `10:30 PM` |
| `{VERSION}` | addon version | `1.13.0` |
| `{DURATION}` | process uptime | `2 days, 4 hours` |
| `{NODECOUNT}` | active node count | `23` |
| `{DIRECTCOUNT}` | 0-hop node count | `6` |
| `{FEATURES}` | enabled automation emoji set | `🗺️ 🤖 📢` |
| `{param}` | responder capture group | `miami` |

#### 2.10 Config schema changes

Extend `nodepulse-addon/config.json` schema + `config.py` dataclass:

```jsonc
{
  // existing options unchanged …
  "automation_enabled": "bool?",
  "automation_channel_util_gate": "int(40,99)?",     // Table 2.7 threshold
  "automation_rule_default_cooldown": "int(0,3600)?", // seconds
  "notify_service_url": "str?",                       // Apprise URL (2.8)
  "notify_node_offline_hours": "int(1,168)?",
  "notify_low_battery_pct": "int(0,50)?"
}
```

Complex rule definitions (schedules, triggers, zones) live in the new
`automation.json` (managed via REST + Web UI), keeping HA addon options flat
and simple — mirroring how MQTT/Telegram keep simple toggles in options but
Operate details via endpoints.

#### 2.11 REST API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/automation` | List rules (schedules, triggers, zones) with enabled state |
| POST | `/api/automation` | Create/update a rule |
| DELETE | `/api/automation/{id}` | Remove a rule |
| POST | `/api/automation/{id}/run` | "Send Now" / test-fire a rule |
| POST | `/api/automation/{id}/toggle` | Enable/disable |
| GET | `/api/automation/status` | Gate state + last-run/last-error per rule |
| POST | `/api/notify/test` | Send a test push (Apprise or HA) |

`api.js` gains matching client functions; the Web UI Automation tab is wired
like the existing Settings/Configure tabs.

#### 2.12 Testing

- **Unit** (`test_automation.py`): cron next-fire computation; template token
  expansion incl. edge cases ({HOPS}/{SNR} missing, `{param}` from regex
  captures); point-in-polygon; cooldown logic; airtime gate on/off; Apprise
  URL parsing (no network).
- **Integration**: mock `Connection.send_message`/`request_traceroute`, feed
  synthetic packets through the trigger bus, assert single-send-path usage
  and no lock violations; verify welcome migration from old options; verify
  auto-ping ACK/NAK accounting with a fake interface.
- **Manual smoke**: scheduler fires on interval and cron; auto-ack replies on
  channel VS DM correctly; responder HTTP webhook intercept via local
  recorder; mailbox round-trip `msg` → `inbox play` → `inbox delete`.

#### 2.13 Sequencing & delivery

| Milestone | Ships | Contents |
|-----------|-------|----------|
| 1.14 | 2.0 engine + 2.1 scheduled/announce + 2.7 airtime gate | Scheduler, templates, gate, UI section |
| 1.15 | 2.2 auto-traceroute + 2.3 auto-ack | Trigger bus (message/node events) |
| 1.16 | 2.4 responder (text→http→script) + 2.5 welcome/ping | Migration of old welcome hook |
| 1.17 | 2.6 geofence + 2.8 notifications | Position events + push transports |
| 1.18 | 2.0 hardening | Mailbox (if not shipped earlier), docs, E2E |

Each milestone lands independently; nothing in this phase blocks another. All
outputs reuse `Connection.send_message()` / `request_traceroute()` so the
existing rate-limit/dedup/ACK/ingress-safety guarantees hold by construction.

**Estimated effort:** ~7–9 sprints total; high value-per-effort is front-loaded
(engine + scheduled + ack in the first two milestones).

### Phase 3 — Messages (P1/P2)

| # | Feature | Notes |
|---|---------|-------|
| 3.1 | **Full-text search across conversations** | index `messages.json`; extend `/api/messages` with `?q=&search=all`; results UI |
| 3.2 | **Replies & threading** | link messages by `reply_id`; thread view per conversation |
| 3.3 | **Tapbacks (reactions)** | store per-message reactions; UI emoji picker; reuse `sendText` with `wantAck` |
| 3.4 | **Store & Forward (S&F)** | detect S&F servers (role), flag on map; query `STORE_FORWARD_APP`; render retrieved history |
| 3.5 | **Contact share QR** | generate `meshtastic.org/v/#` URL + QR (small lib or inline SVG) in node details |

**Estimated effort:** 3.1 ≈ 1 sprint; 3.2+3.3 ≈ 2; 3.4 ≈ 2 (new protobuf plumbing); 3.5 ≈ 0.5.

### Phase 4 — Map & Geography (P2)

| # | Feature | Notes |
|---|---------|-------|
| 4.1 | **Tileset selection + custom tile servers** | extend Leaflet config; persist choice; satellite/topo options |
| 4.2 | **GeoJSON / KML / KMZ zone imports** | parse+render overlays on the map; store via API |
| 4.3 | **Position estimation** | infer fixes for GPS-less nodes from neighbour SNR/positions; mark estimated |
| 4.4 | **Accuracy regions** | render position-accuracy circles (GPS precision bits) |
| 4.5 | **Terrain link profile (LOS/Fresnel)** | optional: DEM tile fetch + elevation profile between two map points (partly present in Ruler) |
| 4.6 | **Polar RF grid overlay** | range-rings widget around self node |

**Estimated effort:** 4.1 ≈ 1; 4.2 ≈ 2; 4.3+4.4 ≈ 2; 4.5 ≈ 3 (backend DEM proxy); 4.6 ≈ 0.5. 4.5 is the only one needing outbound API creds.

### Phase 5 — Security Scanner (P2)

| # | Feature | Notes |
|---|---------|-------|
| 5.1 | **Duplicate / weak key detection** | read channel PSKs + per-node public keys; heuristic checks; warnings + filter in Nodes/Map |
| 5.2 | **Impersonation detection** | flag nodes whose public key / NODEINFO identity conflicts with a known node |
| 5.3 | **Security filter** | show-flagged / hide-flagged in the node list |

**Estimated effort:** ~2 sprints. Backend reads `channels.json` + node store; Web UI filter additions only.

### Phase 6 — Remote Admin & Firmware (P2/P3)

Requires the *remote admin* protocol that meshtastic-py exposes; most ambitious.

| # | Feature | Notes |
|---|---------|-------|
| 6.1 | **Remote admin scanner** | probe nodes for admin capability; store results |
| 6.2 | **Admin commands (reboot, set config)** | piggyback on `meshtastic` admin API; strict whitelist + confirmation UI |
| 6.3 | **Channel administration** | set channel name/keys/PSK via admin protocol; extend Configure tab |
| 6.4 | **Firmware OTA push** | send firmware-update URL to supported nodes |
| 6.5 | **Auto time-sync** | periodic Set Time admin command |

**Estimated effort:** 6.1+6.2 ≈ 2–3 sprints; 6.3 ≈ 2; 6.4 ≈ 1; 6.5 ≈ 0.5. **Risk:** the addon's single TCP slot means admin traffic competes with monitoring bandwidth.

### Phase 7 — i18n & UX polish (P3)

- Extract all Web UI strings to a locale map; add `en` default + UI language picker.
- Optional: configurable units (km/mi, °C/°F, 12/24 h, date format).
- Optional: signal-strength colouring of map markers (MeshMonitor parity).

---

## Deliberately out of scope (HA already provides)

- Local accounts, OIDC/SSO, MFA, RBAC, audit log → **HA auth, admin toggle, ingress**.
- System backup/restore, one-click upgrade → **HA snapshots + addon store**.
- Multi-database (Postgres/MySQL) → JSON persistence is adequate at HA scale.
- Desktop apps / PWA + system tray → **HA mobile app** covers this.
- Embedded MQTT broker → NodePulse bridges an external broker; a broker is a different product. (HA's own Mosquitto addon covers this.)
- Embed-map iframes → mesh maps are private by design.

---

## Suggested Release Sequencing

| Release | Content |
|---------|---------|
| 1.13 | Phase 1 (Link Quality, Smart Hops, health score, signal trend) |
| 1.14 | Automation mil. 1 (engine + scheduled/announce + airtime gate) |
| 1.15 | Automation mil. 2 (auto-traceroute, auto-ack) |
| 1.16 | Automation mil. 3 (responder generalisation, welcome/ping) |
| 1.17 | Automation mil. 4 (geofence, push notifications) |
| 1.18 | Automation mil. 5 (mailbox/hardening) — or Phase 3 (search, replies) |
| 1.19 | Phase 4 (tilesets, zone imports, position estimation) |
| 1.20 | Phase 5 (security scanner) |
| 1.21 | Phase 6 (remote admin) |
| 1.22 | Phase 7 + backlog |

Each phase lands independently, keeps the addon HA-integrated, and reuses the
existing `connection.py` send/rate-limit/persistence infrastructure.

---

*Sources: MeshMonitor docs (meshmonitor.org, v4.x) and README; NodePulse FEATURES.md, ROADMAP.md, CHANGELOG.md. Generated 2026-08-12.*