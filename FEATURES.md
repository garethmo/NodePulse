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
| `/api/channels` | GET | Configured mesh channels |
| `/api/messages` | GET | Recent message buffer (capped at 200, oldest first) |
| `/api/send` | POST | Send a text message (broadcast or DM) |
| `/api/traceRoute` | POST | Dispatch a traceroute (fire-and-forget, results on next poll) |
| `/api/requestPosition` | POST | Request fresh GPS from a specific node |
| `/api/tags` | GET/PUT | Read and write user-defined node tags |
| `/api/position-history` | GET | Position fix history for map trails |
| `/api/nodes/clear-stale` | POST | Purge cached/stale nodes |
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

### Web UI — Dashboard View

The default view served under HA Ingress, with a responsive 3-column grid on desktop that stacks to a single scrollable column on mobile.

| Component | Description |
|-----------|-------------|
| **Map (mini)** | Leaflet dark-theme map centred on Durban, SA; shows all GPS-fixed nodes with teal markers, self node in blue; permanent name labels; distance-labelled self→node links; peer proximity links; traceroute paths; position history trails |
| **Node list** | Sidebar list sorted by distance from self; per-node SNR bar, battery %, last heard; click to select and drive charts |
| **Message feed** | Conversation tabs (per-channel + per-DM) with unread badges; message bubbles with sender name, time, channel indicator; send status (sending/sent/failed); click-to-retry failed messages |
| **Compose box** | Channel selector (broadcast) or implicit DM destination; auto-growing textarea; Enter to send |
| **Charts row** | 5 rolling charts — SNR (dB), RSSI (dBm), Node Count, Channel Utilization (%), Airtime Utilization (%). Signal charts: 30-point window (~7.5 min). Utilization charts: 120-point window (~30 min) |

### Web UI — Nodes View

A scrollable grid of node cards, one per mesh node.

Each card shows:
- **Header**: Long name, node ID, hardware model, stale/cached badge
- **Tags**: Comma-separated user-defined labels with inline editor
- **Metrics grid**: SNR, RSSI, hops away, battery, distance, GPS fix, temperature, humidity, pressure
- **Traceroute**: Forward and return path with hop-by-hop resolved names and timing
- **Neighbors**: Per-peer SNR chips when NEIGHBORINFO_APP data is available
- **Actions**: Traceroute, Request Position, Message, Track in HA

Free-text filter across name, short name, hardware model, and ID.

### Web UI — Map View

Full-screen map with an interactive filter bar:

| Filter | Options |
|--------|---------|
| **Text** | Substring match on name, short name, or ID |
| **Max hops** | Any / 0 (direct) / 1–4 / 5+ |
| **Heard within** | Any time / 15 min / 1 h / 6 h / 24 h / Cached only |

A live `N shown` counter updates on filter change and on every poll.

**Overlay toggle controls** (collapsible via **C** key):
- Self→node links (teal dashes, distance-labelled)
- Peer proximity links (amber dashes, within ~15 km or both 1-hop)
- Traceroute routes (blue, forward and return paths)
- Node name labels (permanent tooltips)
- Position history trails (deep orange polylines)

**Export**: KML and GPX download of visible GPS-fixed nodes.

### Web UI — Settings View

Read-only display of runtime configuration: connection type, host/port, node count, ignored nodes, HA base URL, access key status, scan interval, log level. "Clear stale nodes" action button.

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
