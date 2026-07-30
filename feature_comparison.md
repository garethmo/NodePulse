# Feature Comparison: MeshSense vs Meshtastic for HA vs NodePulse

> **Scope:** NodePulse = addon (Web UI + API) + HA integration. "Meshtastic for HA" = the official `meshtastic/home-assistant` integration (broglep). "MeshSense" = Affirmatech desktop app.
> 
> ✅ = supported  |  ❌ = not supported  |  ⚠️ = partial/workaround

---

## 1. Connection & Transport

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **TCP / WiFi** | ✅ | ✅ | ✅ |
| **Serial / USB** | ❌ (requested) | ✅ | ❌ |
| **Bluetooth** | ✅ | ✅ (incl. BLE Proxy) | ❌ |
| **Bluetooth Proxy (ESPHome)** | ❌ | ✅ | ❌ |
| **Auto-discovery (Zeroconf/mDNS)** | ❌ | ✅ | ❌ |
| **USB-Serial Auto-discovery** | ❌ | ✅ | ❌ |
| **MQTT client/proxy support** | ⚠️ monitor only | ✅ (forwarding to broker) | ❌ |
| **Multi-gateway / multi-entry** | ❌ | ✅ | ⚠️ (UI only, first-coord wins in integration) |
| **Auto-reconnect with backoff** | ✅ | ✅ | ✅ |

---

## 2. Node Management

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **View all nodes** | ✅ | ✅ | ✅ |
| **Filter / search nodes** | ✅ | ❌ | ✅ |
| **Node tagging / custom labels** | ❌ | ❌ | ✅ |
| **Ignore/exclude nodes** | ❌ | ❌ | ✅ |
| **Stale node retention (beyond radio DB limit)** | ✅ | ❌ | ✅ |
| **Node hardware model display** | ✅ | ✅ | ✅ |
| **Node role display** | ✅ | ✅ | ✅ |
| **Node configuration / settings editor** | ❌ | ⚠️ (via bundled web client) | ❌ |
| **Remote node admin (over-air config)** | ❌ | ❌ | ❌ |
| **Node health scoring** | ❌ | ❌ | ❌ (roadmap) |
| **Per-node alert thresholds** | ❌ | ❌ | ❌ |

---

## 3. Messaging

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **Send/receive text messages** | ✅ | ✅ | ✅ |
| **Channel tabs with real names** | ✅ | ✅ | ✅ |
| **Direct Messages (DM)** | ✅ | ✅ | ✅ |
| **Per-conversation thread view** | ✅ | ✅ | ✅ |
| **Message history search** | ✅ | ❌ | ✅ |
| **Persistent message history (survives restart)** | ❌ | ✅ (via logbook) | ✅ (messages.json) |
| **Message export (JSON/CSV)** | ❌ | ❌ | ✅ (downloadable JSON & CSV via Web UI & API) |
| **Message acknowledgement (ACK) status** | ✅ | ✅ | ✅ (tracks sending/sent/delivered/failed ACK status) |
| **Waypoints (send/receive)** | ❌ | ⚠️ (partial via MQTT) | ✅ (receive via WAYPOINT_APP + locally create/delete, persisted to waypoints.json) |
| **Bell / notification on new message** | ✅ | ⚠️ (via HA notify automation) | ✅ (Web UI browser notifications) |

---

## 4. Mapping & Positioning

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **Interactive map (Leaflet/OSM)** | ✅ | ✅ (HA map card) | ✅ |
| **Node markers on map** | ✅ | ✅ | ✅ |
| **Position history trail / breadcrumbs** | ✅ | ❌ | ✅ |
| **Self → node distance lines** | ✅ | ❌ | ✅ |
| **Peer proximity / neighbor links** | ✅ | ❌ | ✅ |
| **Traceroute path overlays** | ✅ | ❌ | ✅ |
| **Map node filter (hops, time)** | ❌ | ❌ | ✅ |
| **KML / GPX export** | ❌ | ❌ | ✅ |
| **Map tile customization** | ✅ | ⚠️ (via HA map card settings) | ❌ (dark OSM only) |
| **Waypoints on map** | ⚠️ (display only, reported) | ❌ | ✅ (amber teardrop pin markers, emoji icon, popup with delete button) |
| **Geo-fencing / zone awareness** | ❌ | ✅ (HA zones via device_tracker) | ✅ (via device_tracker) |
| **Coverage heatmap** | ❌ | ❌ | ✅ |
| **Network topology force graph** | ✅ | ❌ | ✅ |

---

## 5. Telemetry & Sensors

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **SNR / RSSI** | ✅ | ✅ | ✅ |
| **Battery level** | ✅ | ✅ | ✅ |
| **Voltage** | ✅ | ✅ | ✅ |
| **Temperature / Humidity / Pressure** | ✅ | ✅ | ✅ |
| **Gas resistance** | ❌ | ❌ | ✅ |
| **Hops away** | ✅ | ✅ | ✅ |
| **Channel utilization / Air util TX** | ✅ | ✅ | ✅ |
| **Uptime** | ✅ | ✅ | ✅ |
| **Neighbor info (NEIGHBORINFO_APP)** | ✅ | ⚠️ (partial) | ✅ |
| **Altitude** | ✅ | ✅ | ✅ |
| **Distance from self** | ✅ | ❌ | ✅ |
| **Position fix count (trail depth)** | ❌ | ❌ | ✅ |
| **Rolling charts (SNR, RSSI, channel util)** | ✅ | ❌ | ✅ |
| **Signal quality trend / rating** | ❌ | ❌ | ✅ (rolling snr_avg rating: Excellent/Good/Fair/Poor) |
| **Power telemetry (watts, solar)** | ⚠️ (display if available) | ⚠️ | ❌ |

---

## 6. Traceroute & Diagnostics

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **Dispatch traceroute** | ✅ | ✅ | ✅ |
| **View hop-by-hop path + SNR** | ✅ | ❌ | ✅ |
| **Forward + return path display** | ✅ | ❌ | ✅ |
| **Traceroute persistence (survives restart)** | ❌ | ❌ | ✅ (traceroutes.json) |
| **Traceroute-complete device trigger (HA)** | N/A | ❌ | ✅ |
| **Packet / raw protobuf inspector** | ✅ | ❌ | ✅ (real-time ring buffer with expandable JSON detail) |
| **LoRa packet log / sniffer** | ✅ | ❌ | ✅ (live sniffer stats, portnum distribution, CSV/JSON export) |

---

## 7. Home Assistant Integration

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **HA custom integration** | ❌ | ✅ | ✅ |
| **Sensor entities (SNR, battery, etc.)** | N/A | ✅ | ✅ |
| **Binary sensor (connection / online)** | N/A | ✅ | ✅ |
| **Device tracker (GPS on map)** | N/A | ✅ | ✅ |
| **GeoLocation entity (geo_location platform)** | N/A | ❌ | ✅ |
| **Notify platform** | N/A | ✅ | ✅ |
| **Per-channel notify entities** | N/A | ✅ | ✅ |
| **Device triggers** | N/A | ✅ | ✅ |
| **Device actions (send, traceroute, position)** | N/A | ✅ | ✅ |
| **Request telemetry action** | N/A | ✅ | ❌ |
| **Logbook integration** | N/A | ✅ | ✅ |
| **Message ACK confirmation in automations** | N/A | ✅ | ❌ |
| **Push-based updates (local_push)** | N/A | ✅ (predominantly push) | ❌ (poll only, 30s default) |
| **MQTT proxy/forwarding** | N/A | ✅ | ❌ |
| **Multi-gateway support** | N/A | ✅ | ⚠️ (silently first-wins) |
| **HA Blueprint (pre-built automations)** | N/A | ❌ | ❌ (idea) |
| **HA Dashboard blueprint (Lovelace)** | N/A | ❌ | ❌ (idea) |
| **Energy dashboard integration** | N/A | ❌ | ❌ (idea) |
| **Options flow (post-setup config)** | N/A | ✅ | ✅ |
| **Zeroconf / mDNS auto-discovery** | N/A | ✅ | ❌ |

---

## 8. Platform & UX

| Feature | MeshSense | Meshtastic for HA | NodePulse |
|---|---|---|---|
| **Runs on Windows / macOS / Linux** | ✅ (Electron) | ❌ (HA only) | ✅ (via Standalone Docker) |
| **Headless / server mode** | ✅ | N/A | ✅ (Standalone Docker container) |
| **Mobile responsive** | ❌ (desktop app) | ✅ (HA mobile app) | ✅ (Ingress Web UI + PWA support) |
| **Dark / light theme** | ✅ | ✅ (HA theme) | ✅ |
| **Keyboard shortcuts** | ❌ | ❌ | ✅ |
| **i18n / localization** | ❌ | ⚠️ (partial) | ❌ (roadmap) |
| **E2E test suite** | ❌ | ✅ | ❌ (roadmap) |

---

## 🔴 Gap Analysis: What NodePulse Is Missing

These are features competitors have that NodePulse **does not currently implement**.

### High Priority (users will notice)

| Gap | Who has it | Impact |
|---|---|---|
| **Serial / USB connection** | Meshtastic for HA | Locks out nRF-based nodes and USB-only setups entirely |
| **Bluetooth / BLE Proxy** | MeshSense + Meshtastic for HA | Locks out nodes that don't run TCP (common for LORA32, RAK devices) |
| **Push-based HA updates** | Meshtastic for HA | NodePulse polls every 30s; state changes take up to 30s to appear in HA. The official integration pushes immediately on packet receipt |
| **MQTT forwarding** | Meshtastic for HA | No way to bridge to Grafana, InfluxDB, or cloud MQTT without a separate tool |
| **Message ACK confirmation** | MeshSense + Meshtastic for HA | No feedback in HA automations whether a sent message was actually delivered |
| **Node configuration / settings editor** | Meshtastic for HA (via bundled web client) | Users must open the Meshtastic app to change any radio settings |
| **Auto-discovery (Zeroconf / mDNS / USB)** | Meshtastic for HA | Setup requires knowing the TCP host; no plug-and-play |
| **Request telemetry device action** | Meshtastic for HA | NodePulse has `request_position` and `trace_route` but no explicit "request metrics" action |

### Medium Priority

| Gap | Who has it | Impact |
|---|---|---|
| **Waypoints** | MeshSense / Meshtastic HA | Both only partially support waypoints; NodePulse now receives, persists, and displays waypoints from the mesh, and supports locally-created pins |
| **Per-node alert thresholds** | Neither (gap for all) | No way to set "alert if battery < 20%" inside the tool — users must build HA automations manually |
| **HA Lovelace dashboard blueprint** | Neither | No one-click pre-built dashboard — high barrier to entry for new users |
| **HA automation blueprints** | Neither | "Node offline" / "low battery" automation blueprints would make the integration much more accessible |
| **Message export (JSON/CSV)** | None | Supported natively in NodePulse |
| **Power telemetry (solar/watts)** | Meshtastic for HA (partial) | Not surfaced in NodePulse even though Meshtastic firmware can report it |
| **Map tile customization** | MeshSense | NodePulse is locked to dark OSM tiles |

### Lower Priority

| Gap | Who has it | Impact |
|---|---|---|
| **i18n / localization** | Neither (partial in Meshtastic for HA) | Low priority unless targeting non-English markets |
| **E2E test suite** | Meshtastic for HA | Internal quality — not user-facing |
| **Multi-gateway proper support** | Meshtastic for HA | NodePulse has a first-coordinator-wins bug; multi-entry is possible but broken |

---

## NodePulse Unique Advantages

These are things NodePulse does that neither competitor offers:

| Feature | Notes |
|---|---|
| **HA addon + integration in one repo** | Zero separate installation — addon and integration are one unit |
| **Coverage heatmap** | Signal strength visualised as a geographic heatmap |
| **Force-directed network topology graph** | Visual mesh link graph with role-based node styling |
| **Node tagging system** | User-defined labels (`gateway`, `roof`, `mobile`) persisted in `tags.json` |
| **KML / GPX export** | Download GPS-fixed nodes for use in mapping software |
| **Stale node persistence with position** | Nodes evicted from the radio's 250-node DB still appear on map with last known position |
| **Traceroute persistence** | Hop-by-hop routes survive addon restarts |
| **Map node filter (hops + time window)** | Neither MeshSense nor official integration lets you filter map nodes by hop count or last-heard time |
| **Per-channel + per-node tracking toggle from Web UI** | "Track in HA" checkbox directly in the Web UI without touching HA |
| **geo_location platform** | HA native geo_location entities with position trail GeoJSON — the official integration doesn't use this platform |
| **Packet Inspector & Sniffer** | Built-in ring buffer packet capture, live sniffer statistics, and export capabilities |
| **Standalone Docker container** | Can be deployed completely outside of Home Assistant on Linux/macOS/Windows |
| **Message History JSON/CSV Export** | Export conversation history to JSON or formatted CSV directly from Web UI or REST API |
