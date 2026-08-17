# NodePulse — Features

A detailed breakdown of every capability in the addon and Home Assistant integration.

---

## Addon (Web UI + Backend API)

The addon runs as a Home Assistant addon (Docker container) serving a REST API and a full-featured Web UI via HA Ingress.

### Connection Management

| Feature | Detail |
|---------|--------|
| **TCP client** | Persistent connection to a Meshtastic node at `host:port` (direct TCP mode) or via an HA integration TCP proxy |
| **Auto-reconnect** | Capped exponential backoff (5s–60s); detects silently-dropped sessions with active health probes every 60s |
| **Pubsub listener** | Captures all inbound packets via `meshtastic.receive` — text messages, traceroute replies, position replies, neighbor info, telemetry |
| **Single-TCP-slot handling** | Detect and log the Meshtastic firmware's single-client limit with a clear upgrade path (serial/BLE for the official integration, TCP slot for NodePulse) |

### REST API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | Connection state, node identity, runtime config |
| `/api/nodes` | GET | Full node list with SNR, position, traceroute, neighbors, telemetry |
| `/api/node/{node_id}` | DELETE | Remove a single node from the persistent store |
| `/api/nodes/clear-stale` | POST | Purge all cached/stale nodes |
| `/api/channels` | GET | Configured mesh channels |
| `/api/messages` | GET | Recent message buffer (capped at 200, oldest first) |
| `/api/messages/export` | GET | Download message history as JSON or CSV (optional `?format=json|csv` and `?conversation=` filters) |
| `/api/send` | POST | Send a text message (broadcast or DM) |
| `/api/traceRoute` | POST | Dispatch a traceroute (fire-and-forget, results on next poll) |
| `/api/requestPosition` | POST | Request fresh GPS from a specific node |
| `/api/tags` | GET/PUT | Read and write user-defined node tags |
| `/api/position-history` | GET | Position fix history for map trails (optional `/{node_id}` for single-node trail) |
| `/api/packets` | GET | Packet inspector ring buffer (latest captured packets, optional `?limit=`) |
| `/api/sniffer/stats` | GET | Live LoRa sniffer statistics (packets/min, unique nodes, portnum distribution) |
| `/api/security/scan` | GET | Auto-detect weak or duplicate encryption keys across mesh channels |
| `/api/waypoints` | GET | All active (non-expired) waypoints |
| `/api/waypoints` | POST | Create a local waypoint (`name`, `lat`/`lng` optional — defaults to map centre) |
| `/api/waypoints/{id}` | PATCH | Update waypoint fields (e.g. lat/lng after drag) |
| `/api/waypoints/{id}` | DELETE | Remove a waypoint |
| `/api/tracked-nodes` | GET | Proxy to integration — list HA-tracked nodes |
| `/api/track-node` | POST | Proxy to integration — toggle HA tracking |

### Persistence

| File | Contents | Purpose |
|------|----------|---------|
| `messages.json` | Last 200 text messages | Message history survives addon restart |
| `nodes.json` | Every node ever seen | Radio DB is bounded (~250 entries); evicted nodes re-injected as `stale` with last-known position |
| `traceroutes.json` | Discovered routes per node | Hop-by-hop SNR survives restart |
| `channels.json` | Channel config | Immediate tab rendering on startup |
| `tags.json` | User-defined tags per node | `!abc12345` → `["gateway", "roof"]` |
| `position_history.json` | GPS fix trail per node | Up to 200 fixes/node |
| `waypoints.json` | Waypoints from mesh + local | Alive/deleted, expires filtered at read time |

### Web UI — Dashboard View

The default view served under HA Ingress, with a responsive 3-column grid on desktop that stacks to a single scrollable column on mobile.

| Component | Description |
|-----------|-------------|
| **Map (mini)** | Leaflet dark-theme map centred on Durban, SA; shows all GPS-fixed nodes with teal markers, self node in blue; permanent name labels; distance-labelled self→node links; peer proximity links; traceroute paths; position history trails |
| **Node list** | Sidebar list sorted by distance from self; per-node SNR bar, battery %, last heard; click to select and drive charts |
| **Message feed** | Conversation tabs (per-channel + per-DM) with unread badges; message bubbles with sender name, time, channel indicator; send status (sending/sent/failed); click-to-retry failed messages; export any conversation as JSON or CSV |
| **Compose box** | Channel selector (broadcast) or implicit DM destination; auto-growing textarea; Enter to send |
| **Charts row** | 5 rolling charts — SNR (dB), RSSI (dBm), Node Count, Channel Utilization (%), Airtime Utilization (%). Signal charts: 30-point window (~7.5 min). Utilization charts: 120-point window (~30 min) |

### Web UI — Nodes View

A scrollable grid of node cards, one per mesh node.

Each card shows:
- **Header**: Long name, node ID, hardware model, stale/cached badge, **Favorite star (★)** — click to pin/unpin; favorites appear at top of list
- **Tags**: Comma-separated user-defined labels with inline editor
- **Metrics grid**: SNR, RSSI, hops away, battery, distance, GPS fix, temperature, humidity, pressure
- **Traceroute**: Forward and return path with hop-by-hop resolved names and timing
- **Neighbors**: Per-peer SNR chips when NEIGHBORINFO_APP data is available
- **Actions**: Traceroute, Request Position, Message, Track in HA, Notify, Delete (red button with confirmation prompt)

**Sorting**: Favorites first → nodes with signal (snr_avg) → by signal strength → by last heard (most recent). Persisted in `localStorage` (`np_favorite_nodes`). Toast notification on toggle.

Free-text filter across name, short name, hardware model, and ID.

### Web UI — Map View

Full-screen map with an interactive filter bar:

| Filter | Options |
|--------|---------|
| **Text** | Substring match on name, short name, or ID |
| **Max hops** | Any / 0 (direct) / 1–4 / 5+ |
| **Heard within** | Any time / 15 min / 1 h / 6 h / 24 h / Cached only |

A live `N shown` counter updates on filter change and on every poll.

**Base map style toggle** (top-left toolbar):
- **Dark** — CartoDB Dark Matter (default)
- **Light** — CartoDB Light Matter
- **Satellite** — ESRI World Imagery
- **Topographical** — OpenTopoMap

Selection persists in `localStorage` across sessions.

**Overlay toggle controls** (collapsible via **C** key):
- Self→node links (teal dashes, distance-labelled)
- Peer proximity links (amber dashes, within ~15 km or both 1-hop)
- Traceroute routes (blue, forward and return paths)
- Node name labels (permanent tooltips)
- Position history trails (deep orange polylines) — last 200 GPS fixes per node

**Export**: KML and GPX download of visible GPS-fixed nodes.

### Web UI — Packet Inspector

A dedicated "Packets" tab showing every inbound Meshtastic packet via a real-time ring buffer (configurable limit). Columns: Portnum, From (short name + hex ID), To, Channel, SNR, Hop Count, ACK status. Click any row to expand the full protobuf JSON detail.

- **Sort/filter**: Click column headers to sort asc/desc or filter by unique values via dropdown
- **Export**: Download visible packets as JSON or CSV
- **Sniffer stats**: Collapsible "📊 Stats" panel with packets/min, unique nodes, total captured, and portnum distribution bars
- **Security scanner**: Server-side classification of channel PSKs (secure/weak/unencrypted), duplicate key detection, and inline 🔓 badges on flagged packet rows
- **Responsive**: Table collapses gracefully on mobile with `.packet-table` CSS

### Web UI — Waypoints

Waypoints from the mesh (`WAYPOINT_APP` protobuf packets) are captured and persisted. A floating "📍 Waypoint" panel lets you create local waypoints with name, description, emoji icon, and optional lat/lng (click map to fill, or leave blank to place at map centre). Markers are amber teardrop pins with the chosen emoji, **draggable** to reposition (persisted via `PATCH`). Popups show name, description, coordinates, source, and a delete button. Expired waypoints are hidden automatically.

### Web UI — Ruler

Click the "📏 Ruler" button to enter measurement mode. The map filter bar collapses to a compact floating pill. Click the map to place measurement points (amber circles appear instantly). Dashed amber polylines connect points with midpoint distance labels. An elevation profile panel shows total distance, elevation gain, elevation loss, and a canvas-drawn altitude vs distance chart (sampled at up to 500 points from node position history using IDW interpolation). Click **Clear** to reset or close the panel to exit.

### Web UI — Settings View

Read-only display of runtime configuration: connection type, host/port, node count, ignored nodes, HA base URL, access key status, scan interval, log level, addon version. "Clear stale nodes" action button.

### Web UI — Configuration View (Device Configuration)

A **Configure** tab for viewing and editing the connected mesh radio's configuration (shipped 1.10.0+, refined in 1.11.0/1.12.0/1.17.0). Backed by `GET/PUT /api/device-config` + `POST /api/device-config/reload`.

- **Schema-driven forms** — The field schema (types, enum options, min/max, max length) is introspected live from the radio's installed protobuf descriptors, so forms render correctly across firmware versions. Sectioned cards: Node Identity (owner), Device, LoRa Radio, Position, Power, Display, Network/WiFi, Bluetooth, Telemetry, Neighbor Info, **Mesh Beacon (2.8+)**, **Status Message (2.8+)**, **TAK / ATAK (2.8+)**, **Traffic Management (2.8+)**, **Ambient Lighting (2.8+)**, MQTT, Canned Messages, Store & Forward.
- **Firmware gating** — 2.8+ sections (Mesh Beacon, Status Message, TAK, Traffic Management, Ambient Lighting) are greyed out with "Requires firmware 2.8.0+" banner on older firmware.
- **Backend validation** — Numeric ranges, string lengths, and enum values are validated against firmware-derived constraints; invalid values are rejected with HTTP 400.
- **Enum dropdowns** — Enum-backed fields render as `<select>`s populated from the radio's firmware schema.
- **Danger-zone confirmations** — Role→ROUTER, LoRa TX disabled, region change, and credential updates prompt for confirmation in the UI *and* are rejected server-side without `"confirm": true`.
- **LoRa preset gating** — Manual radio params (bandwidth/spread factor/coding rate/frequency offset) are greyed out and rejected while `use_preset` is enabled.
- **Reboot feedback** — Writes requiring a reboot show a dismissible "reboot the node to apply" banner; a Refresh button force re-reads config from the radio.
- **Thread-safe writes** — Writes run in a thread-pool worker under a config-write lock, never holding the connection lock during radio I/O.

#### Mesh Beacon (firmware 2.8+)
- **Flags** — Bitfield rendered as three checkboxes: Listen (receive beacons), Broadcast (transmit beacons), Legacy Split (split beacon text + offer into separate MESH_BEACON_APP + TEXT_MESSAGE_APP packets)
- **Broadcast message** — Text included in each beacon (max 100 bytes)
- **Offer channel/region/preset** — Advertised to listening clients for mesh discovery
- **TX channel/region/preset** — Radio settings used when transmitting beacons
- **Broadcast interval** — Seconds between beacons (min 3600s / 1 hour)

#### Status Message (firmware 2.8+)
- **Node status** — Free-text status string displayed in UI

#### TAK / ATAK (firmware 2.8+)
- **Team** — 16-color enum dropdown (Unspecified, Red, Blue, Green, Yellow, Cyan, Magenta, Orange, Violet, White, Black, Brown, Pink, Grey, Light Blue, Dark Red, Dark Green, Dark Blue)
- **Role** — 100+ ATAK MemberRole enum dropdown

#### Traffic Management (firmware 2.8+)
- **Enabled** — Master toggle for traffic management module
- **Per-module ignore toggles** — 21 boolean toggles to ignore specific module traffic (MQTT, Serial, External Notification, Canned Message, Audio, Remote Hardware, Ambient Lighting, Detection Sensor, PaxCounter, Store & Forward, Range Test, Neighbor Info, Telemetry, TAK, Status Message, Mesh Beacon, plus MQTT downlink/uplink variants)

#### Ambient Lighting (firmware 2.8+)
- **Enabled** — Master toggle
- **LED GPIO / Count / Type** — Hardware configuration
- **Brightness** — 0–255
- **Pattern / Color / Speed** — Animation parameters

### Web UI — Theming

Dark theme by default; light theme toggle in the header with `localStorage` persistence. All colours driven by CSS custom properties — the light theme replaces every background, border, text, and glow variable in one cascade.

### Web UI — Mobile

Hamburger menu opens/shuts a slide-in sidebar drawer. Dashboard stacks into a single column. Dynamic viewport height (`100dvh`) and `safe-area-inset-*` padding for notched phones in the HA mobile app.

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **S** | Toggle self→node links |
| **P** | Toggle peer proximity links |
| **T** | Toggle traceroute paths |
| **N** | Toggle node name labels |
| **H** | Toggle position history trails |
| **C** | Collapse/expand overlay controls |

---

## Integration (Home Assistant Custom Component)

The `custom_components/nodepulse/` package registers entities, services, device automations, and logbook integration.

### Sensors

#### Integration-Level Sensor

| Entity | ID | Description |
|--------|----|-------------|
| Node Count | `sensor.nodepulse_node_count` | Total nodes visible on the mesh |

#### Per-Node Sensors (one set per tracked node, grouped under one device)

| Entity | ID Suffix | Device Class | Unit | Description |
|--------|-----------|--------------|------|-------------|
| SNR | `_{node_id}_snr` | `signal_strength` | dB | Last received signal-to-noise ratio |
| RSSI | `_{node_id}_rssi` | `signal_strength` | dBm | Last received signal strength |
| Hops Away | `_{node_id}_hops` | — | — | How many hops from the local node |
| Last Heard | `_{node_id}_last_heard` | `timestamp` | — | When the node was last heard |
| Battery | `_{node_id}_battery` | `battery` | % | Reported battery level |
| Temperature | `_{node_id}_temperature` | `temperature` | °C | Ambient temperature (onboard sensor) |
| Humidity | `_{node_id}_humidity` | `humidity` | % | Relative humidity (onboard sensor) |
| Pressure | `_{node_id}_pressure` | `pressure` | hPa | Barometric pressure (onboard sensor) |
| Latitude | `_{node_id}_latitude` | — | ° | Last known latitude |
| Longitude | `_{node_id}_longitude` | — | ° | Last known longitude |
| Altitude | `_{node_id}_altitude` | `distance` | m | Last known altitude |
| Voltage | `_{node_id}_voltage` | `voltage` | V | Power voltage (device telemetry) |
| Channel Util | `_{node_id}_channel_util` | — | % | Channel utilization (0–100) |
| Air Util TX | `_{node_id}_air_util_tx` | — | % | Airtime transmit utilization (0–100) |
| Uptime | `_{node_id}_uptime` | `duration` | s | Node uptime |
| Role | `_{node_id}_role` | — | — | `CLIENT`, `ROUTER`, `ROUTER_CLIENT`, etc. |
| Gas Resistance | `_{node_id}_gas_resistance` | — | MΩ | Gas sensor resistance (e.g. MQ-135) |
| Distance | `_{node_id}_distance` | — | km | Haversine distance from self/gateway node |
| Neighbor Count | `_{node_id}_neighbor_count` | — | — | Number of peers this node sees from NEIGHBORINFO_APP |
| Scheduled Message | `_{node_id}_scheduled` | — | — | Message queued for future delivery |
| Message Received | `_{node_id}_message_received` | — | — | Text of last received message |
| Message Sent | `_{node_id}_message_sent` | — | — | Text of last sent message |

### Binary Sensors

| Entity | ID | Purpose |
|--------|----|---------|
| Connection | `binary_sensor.nodepulse_connection` | True when the addon's TCP link to the meshtastic node is up |
| Online | `binary_sensor.nodepulse_<node>_online` | True if the node was heard within the last 3 hours |

### Device Trackers

| Entity | Source Type | Extra Attributes |
|--------|-------------|-----------------|
| `device_tracker.nodepulse_<node>` | `GPS` | `altitude`, `snr`, `rssi`, `hops_away`, `hw_model`, `short_name`, `last_position_fix`, `stale` |

Created only for nodes with a valid GPS fix. Plots directly on the native Home Assistant map card. The `stale` attribute is `True` for nodes that the radio has evicted from its bounded DB but are kept visible from the persistent store.

### Notify Platform

| Entity | Scope |
|--------|-------|
| `notify.mesh_<entry>` | Gateway — supports `target` (DM) and `data.channel` |
| `notify.mesh_<entry>_channel_<name>` | Per configured channel — always broadcasts on that channel |

### Services

| Service | Schema | Description |
|---------|--------|-------------|
| `nodepulse.send_message` | `{ text, target?, channel? }` | Send a text message; omit `target` for broadcast |
| `nodepulse.request_position` | `{ target }` | Request fresh GPS from a node |
| `nodepulse.trace_route` | `{ target }` | Dispatch a traceroute to a node |

### Device Actions (per node device)

| Action | Description |
|--------|-------------|
| `send_message` | Send a text message to this node (DM); optional `text` and `channel` |
| `request_position` | Ask this node to report its GPS position |
| `trace_route` | Dispatch a traceroute to this node |

### Device Triggers (per node device)

| Trigger | Direction | Description |
|---------|-----------|-------------|
| `message_received` | `received` | Fires when a message arrives from this node |
| `message_sent` | `sent` | Fires when a message is sent from this node |
| `channel_message.received` | `received` | Fires on channel messages (excludes DMs) |

All triggers fire the `nodepulse_message` event with payload `{ node_id, direction, channel, is_dm, text, from_id }`. The event is also recorded in the Home Assistant logbook.

### Config Flow

| Step | Fields |
|------|--------|
| **User** | `host` (auto-suggested `http://a0d7b954-nodepulse:8099`), `access_key` (optional), `scan_interval` (default 30s, range 10–300) |
| **Options** | `scan_interval`, `ignored_nodes` (comma-separated node IDs, normalised to `!xxxxxxxx` form) |

Setup validates by hitting the addon's `/api/status` endpoint. The working host is cached to bypass DNS fallback on subsequent polls.

---

## Data Flow

```
Meshtastic Node (TCP :4403)
  │
  ▼
NodePulse Addon (Python / aiohttp :8099)
  ├── connection.py — TCP client, pubsub listener, message buffer, persistence
  ├── routes.py — REST API endpoints
  ├── main.py — aiohttp app, lifecycle, CORS
  └── web_ui/ — HTML + JS dashboard served at /
      │
      ▼ (REST /api/* via HA Ingress)
NodePulse Integration (Python / DataUpdateCoordinator)
  ├── binary_sensor.py — Connection + Online
  ├── sensor.py — 19 per-node metric sensors
  ├── device_tracker.py — GPS device trackers
  ├── notify.py — Gateway + per-channel notify entities
  ├── device_action.py — 3 actions per node
  ├── device_trigger.py — 3 triggers per node
  ├── api.py — Track/tracked-nodes HTTP views
  └── services.yaml → 3 integration services
```

---

*See [README.md](./README.md) for installation and [CHANGELOG.md](./CHANGELOG.md) for release history.*
