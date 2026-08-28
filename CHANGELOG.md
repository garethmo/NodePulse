# Changelog

All notable changes to NodePulse are documented here.

## [1.22.0] - 2026-08-28
### Fixed
- **Lock Hierarchy & Concurrency Deadlocks** — Resolved thread-blocking lock acquisition in `_get_nodes_sync`, `_read_channels_from_interface`, and background thread dispatches (auto-responder, traceroute) by executing thread creation outside `_nodes_lock` and snapshotting interface/message state safely under dedicated locks (`_lock`, `_msg_lock`).
- **Terrain Service 503 Contract** — Updated `handle_terrain_coverage` in `routes.py` to return HTTP `503 Service Unavailable` when the terrain elevation service is disabled.
- **Scheduled Message Tuple Handling** — Corrected queue element processing in `_process_scheduled_messages` to ensure scheduled messages dispatch cleanly without type errors.
- **Topology Edge Synchronization** — Fixed traceroute and neighbor edge rendering in `topology.js` by explicitly setting `hidden: false` on edge initialization.
- **Test Suite Signature Sync** — Synchronized `mock_connection.get_messages` signature in `tests/conftest.py` with production parameters (`load_archived`), ensuring 100% test pass rate across unit and E2E suites (516 tests passing).

### Added
- **Concurrency & Remediation Test Suite** — Added `TestStabilityRemediations` test class in `tests/unit/test_connection.py` covering background traceroutes, channel interface snapshotting, scheduled message processing, and atomic message persistence.

## [1.21.8] - 2026-08-27
### Added
- **Enhanced message history** — Implemented hybrid message storage system: recent messages (last 7 days) kept in memory for fast access, older messages automatically archived to date-based JSON files in `messages_archive/`. In-memory buffer increased from 200 to 1000 messages for deeper history.
- **Message history endpoint** — Added `load_archived=true` query parameter to `GET /api/messages` to load archived messages on demand, enabling unlimited message history access.
- **UI history indication** — Added visual indicator showing when users have reached the oldest available messages in a conversation, displaying the date of the oldest message.

## [1.21.7] - 2026-08-27
### Fixed
- **Message search pagination** — Fixed "Load previous days" button not appearing when searching messages. The search logic now properly checks for older messages before applying search filters, allowing users to expand the time window to find older matching messages.

## [1.21.6] - 2026-08-27
### Fixed
- **Traceroute path construction** — Fixed traceroute path display on both node cards and map. The logic now properly handles different firmware versions that may or may not include self/target nodes in the route array, preventing duplicate nodes in the path and ensuring correct multi-hop path visualization. Added comprehensive unit tests for path construction logic.

## [1.21.5] - 2026-08-24
### Added
- **Editable Security & Admin Keys in Remote Admin** — The Remote Admin view's **Security & Admin Keys** card now lets you manage a remote node's `admin_key` list: paste a base64 admin/public key and click **Add**, one-click add *this gateway's* public key (from the "keys for targets" strip), and remove any existing key with its ✕. Saving rewrites the target's full admin-key list over the radio (requires a reboot to take effect) — the workflow for granting the gateway or any other node admin rights on the target. `public_key` / `private_key` remain read-only identity chips.

## [1.21.4] - 2026-08-24
### Added
- **Node role display in node cards** — The node's role (CLIENT, ROUTER, REPEATER, TRACKER, etc.) is now displayed in the metrics section of each node card in the Nodes view, making it easy to identify nodes serving special functions in the mesh network.
- **Last heard metric in node cards** — Added "Last Heard" metric to node cards showing the relative time since the node was last heard from (e.g., "2m ago", "1h ago"), helping identify inactive or stale nodes.
- **Position request visual feedback** — When requesting a node's position, the button now shows a loading state ("⏳ Requesting...") and automatically refreshes node data to show updated GPS coordinates when available. A timeout of 30 seconds is applied to the pending state.
- **Device favorite integration** — When marking a node as a favorite in the UI, the addon now sends admin messages to the device to actually mark the node as favorite in the device's NodeDB. This makes communication with favorited nodes not count against hop limits (same behavior as the Meshtastic Android app).

### Fixed
- **Telegram node name display** — Fixed node name resolution in Telegram messages. The lookup logic now tries multiple key formats (integer node number, string node ID, string without `!`) to handle different meshtastic library versions. Added comprehensive debug logging and improved fallback to persistent node store. Telegram messages now show human-readable node names instead of node IDs.
- **Traceroute data preservation** — Fixed traceroute data being lost during node data refreshes. The node update logic now preserves the `traceroute` field (including timeout markers) when merging fresh interface data, ensuring successful traceroute results override timeout entries correctly.
- **Node name lookup fallback** — Improved node name resolution in message processing to handle cases where the live interface lookup fails by better utilizing the persistent node store with proper field name handling (`short_name`/`long_name`).

### Changed
- **Unit tests expanded** — Added comprehensive unit tests for node lookup logic, traceroute data preservation, and device favorite communication to prevent regression of the fixes.

## [1.21.3] - 2026-08-23
### Added
- **Meshtastic 2.8 features surfaced in the Web UI** — the diagnostics, GPX, hops, signed-node, status-text and beacon capabilities (previously Telegram-only) are now in the dashboard:
  - **Node Diagnostics modal** — "Diagnostics" button on every node card and map popup (hops-away, rolling SNR + signal quality, battery/voltage/uptime, channel/air utilisation, 2.8 noise floor, environment telemetry, plus the gateway's Mesh Beacon status).
  - **GPX download** — "GPX" button per node downloads its position history as a GPX 1.1 track.
  - **Hops Distribution chart** — new dashboard bar chart of nodes per hop count (`/api/hops`).
  - **Signed-node 🔒 badge + broadcasted status text** — on node cards, the node list, and map popups.
  - New backend routes `GET /api/node/{id}/signal`, `GET /api/node/{id}/gpx`, `GET /api/hops`, `GET /api/beacon`.

### Changed
- **Telegram sender attribution** — forwarded mesh messages now show the sender's short name (e.g. `*Bob*`), appending the long name in parentheses when it adds clarity, so incoming messages are immediately identifiable. The sender name is resolved from the live node snapshot and falls back to the persistent node store; the node ID is still included for unambiguous replies.

## [1.21.2] - 2026-08-23
### Added
- **Meshtastic 2.8 features in the Telegram bot** — New commands built on the 2.8 firmware capabilities:
  - `/diag !node` — per-node diagnostics: hops-away, rolling SNR average + signal quality, battery, voltage, uptime, channel/air utilisation, and (where the python library exposes it) the 2.8 noise floor.
  - `/gpx !node` — exports a node's position history as a GPX 1.1 track file sent via Telegram (uses position history already recorded by the addon).
  - `/hops` — distribution of heard nodes by hop count ("nodes per hop").
  - `/waypoint <lat> <lon> [name] [expire_hours]` — drops a waypoint (stored locally, and broadcast over the mesh when the library supports `sendWaypoint`).
  - `/beacon` — shows the local node's Mesh Beacon module status (`available: false` + a clear reason when the installed library predates 2.8).
  - Signed-node 🔒 badge and broadcasted status text in `/nodes` and `/where` (read defensively; absent on older libraries).
  - `/setpos` now warns when the primary channel is public (2.8 firmware blocks precise position broadcasts on public channels).
- **Connection helpers** — `get_node_signal()`, `send_waypoint()`, `get_beacon_config()`; device-telemetry capture of the 2.8 noise floor; channel `public` flag for the public-channel guard.

## [1.21.1] - 2026-08-21
### Fixed
- **Syntax error in terrain analysis** — Removed extraneous `},` at `app/terrain.py:270` that caused `ImportError` on module load.
- **TX Queue debug spam** — Added `await asyncio.sleep(0.1)` after each message send in the scheduled messages loop (`app/main.py:136`) to prevent meshtastic radio TX buffer overflow, eliminating the continuous "Waiting for free space in TX Queue" debug logs.

### Added
- **Clutter height modeling** — `clutter_height_m` parameter in both `analyze_link()` and `analyze_coverage()` (`app/terrain.py`) adds a fixed elevation offset (meters) to model trees/buildings, realistically reducing coverage range in suburban/urban areas.
- **Atmospheric refraction control** — `k_factor` parameter (`app/routes.py`) allows adjusting the earth curvature k-factor beyond the default 4/3, with per-request validation.
- **GeoJSON/KML export** — New `export_coverage_geojson()` and `export_coverage_kml()` functions (`app/terrain.py`) convert coverage analysis results into exportable vector formats for GIS tools and Google Earth.
- **Debug logging across all message paths** — Added logging in `connection.send_message()` (`app/connection.py:488`), the scheduled messages loop (`app/main.py:120`), and the `/api/send` endpoint (`app/routes.py:770`) to track text excerpt, destination, channel, and send results for troubleshooting.

### Changed
- **Terrain link API** — `/api/terrain/link` now accepts `k_factor` and `clutter_height_m` query parameters, passed through to the analysis.
- **Coverage analysis** — `clutter_height_m` propagates through `analyze_coverage()` to affect beam height calculations and LOS verdicts across all radials.
- **Relay auth fallback** — The tracked-nodes relay (`_relay_to_integration`) now properly retries with `ha_access_token` fallback when `SUPERVISOR_TOKEN` is rejected, ensuring Track-in-HA works on both HAOS and custom Docker/venv installs.

## [1.16.8] - 2026-08-19
### Breaking
- **Entity IDs changed** — Per-node sensors are now device-prefixed (`sensor.<node_name>_snr`, e.g. `sensor.r1_mini_snr`) instead of the old colliding `sensor.snr` ids, and the notify entities moved from `notify.mesh_<entry>` to `notify.nodepulse` / `notify.nodepulse_<name>`. Automations referencing the old IDs must be updated. After installing, do a **full Home Assistant restart** (not Reload) and re-track your nodes if needed.

### Added
- **Integration unit tests (Q1)** — Dependency-free pure-logic tests in `tests/test_nodepulse_integration_pure.py` (24 cases) covering `const.py` node-id helpers and the new `validation.py` (`normalise_node_ids`, `validated_access_key`). Runs with plain pytest; no HA runtime required.
- **`validation.py`** — Config-flow input helpers extracted out of `config_flow.py` into a HA-free module so they can be unit-tested and reused.

### Changed
- **Notify platform rewritten (Q2/Q9/Q18)** — `notify.py` now uses the modern `NotifyEntity` API with a proper `async_setup_entry`; `notify` was added to `PLATFORMS` so entities unload cleanly with the entry. One gateway entity (`notify.nodepulse`) plus one per configured channel (`notify.nodepulse_<name>`), replacing the old `notify.mesh_<entry>` naming. Channels are clamped to the valid 0–7 range.
- **Shared entity discovery (Q11/Q12)** — New `helpers.NodeDiscovery` replaces the copy-pasted register/remove loop in the sensor, binary_sensor, device_tracker and geo_location platforms, and `helpers.coordinator_for(hass)` replaces four duplicated `_coordinator_for` implementations. The discovery loop keeps the tracked-nodes gate and re-creates entities on untrack → re-track.
- **Stable config-entry unique_id (Q10)** — The config flow now derives the entry unique_id from the gateway `node_id` in `/api/status` instead of the host URL, so the same addon reachable via multiple aliases no longer creates duplicate entries.

### Fixed
- **HTTP views registered once (Q3)** — Track/tracked-nodes views are now entry-agnostic and registered a single time in `async_setup`; reloads and a second config entry can no longer collide on URL/name.
- **Device-trigger variables (Q6)** — Device triggers build a proper trigger-variables dict and call `async_run_hass_job`; automations can now read `trigger.platform` / `trigger.type`.
- **Unified entity naming (Q7)** — All node sensors set `_attr_has_entity_name`; per-node sensor entity_ids change to device-prefixed names (old colliding ids were broken).
- **SNR sensor (Q8)** — Removed the mismatched `SIGNAL_STRENGTH` device class (unit stays `dB`).
- **Defensive payload coercion (Q16/Q17)** — `as_int`/`as_float` helpers and try/except guards applied to addon payload parsing in `__init__.py`, `binary_sensor.py` and `sensor.py`; `normalize_node_id` returns `None` for whitespace-only input; tags join coerces to strings.
- **Addon cleanup (Q4/Q14/Q15)** — `Tuple` import added in `connection.py`; dead `except ValueError` removed in `api.py`; unused imports/vars, inline imports and multi-import lines cleaned in the addon and `geo_location.py`.

---

### Fixed
- **Options flow no longer wipes tracked nodes** — `async_step_init` rebuilt the options dict from scratch (only `scan_interval` + `ignored_nodes`), silently discarding `tracked_nodes` persisted by the Web UI's track toggle. It now preserves existing options and only updates the edited keys.
- **Untrack → re-track now re-creates sensors** — Sensor discovery bookkeeping was keyed on `unique_id` for add/check but discarded by `node_id` on removal, so re-tracking a node never re-created its entities. Removal now discards every registered `unique_id` belonging to the node.
- **NaN traceroute interpolation (map crash)** — The map's gap-filling loop could produce `(NaN, NaN)` coordinates when interpolating between traceroute hop positions, throwing `Invalid LatLng object` from Leaflet's `L.polyline` and killing the whole poll cycle (the "Failed to load data" screen). The loop now resets the last-known index properly and skips non-finite coordinates defensively.
- **Topology link rendering** — Traceroute hops are stored as raw integer node numbers, not `!hex` IDs; the topology page now canonicalises them, builds the full forward (`Self → hops → dest`) and return paths (previously the destination and self were dropped), and injects neutral "relay" placeholder nodes for hops missing from the node DataSet so multi-hop routes render instead of silently disappearing.
- **Neighbor/traceroute persistence** — Confirmed `neighbors` and `traceroute` both survive the node merge and `nodes.json` persistence round-trip, so the Nodes screen shows them together after restarts.
- **Changelog gap** — Added the missing 1.18.0 entry (see below) to keep history in sync with `config.json`.

## [1.18.0] - 2026-08-17
### Added
- **Favorite Nodes (UI)** — Star (★) button on every node card in the Nodes view; favorites sort to the top of the list, then nodes with signal, then by recency. Toast on toggle. (Later hardened in 1.19.1 with server-side persistence.)
- **Auto Traceroute on New Node** — When `auto_traceroute_enabled` is on, a traceroute is dispatched automatically the moment a new node is discovered. Non-blocking background thread; respects the 300s timeout and serialization lock. Toggle in Settings → Auto Responder (default `false`).

## [1.17.0] - 2026-08-15
### Added
- **Meshtastic 2.8 Firmware Support** — Full compatibility with Meshtastic 2.8.x including new module configs, LoRa regions/presets, and device roles.
- **Map Base Layer Styles** — Four selectable map types in the top-left toolbar: Dark (CartoDB), Light (CartoDB), Satellite (ESRI World Imagery), Topographical (OpenTopoMap). Selection persists in localStorage.
- **MeshBeacon Config (2.8+)** — New "Mesh Beacon" section in Configure tab with Listen/Broadcast/Legacy-Split toggles (bitfield), beacon message, offer/TX channel-region-preset, and broadcast interval (min 1h). Greyed out on firmware < 2.8.0.
- **2.8 Module Configs** (all firmware-gated):
  - **Status Message** — Text status for UI display
  - **TAK / ATAK** — Team (16 colors) + Role (100+ ATAK roles) dropdowns
  - **Traffic Management** — Per-module enable/disable toggles (21 modules including MQTT, Serial, MeshBeacon, TAK, etc.)
  - **Ambient Lighting** — LED strip control (GPIO, count, type, brightness 0-255, pattern, color, speed)
- **New LoRa Regions** (11 new): EU_N_868, EU_866, EU_874, EU_917, ITU1_2M, ITU2_2M, ITU3_2M, ITU1_70CM, ITU2_70CM, ITU3_70CM, ITU2_125CM
- **New LoRa Presets** (7 new): LITE_FAST, LITE_SLOW, NARROW_FAST, NARROW_SLOW, TINY_FAST, TINY_SLOW, MEDIUM_TURBO
- **New Device Roles** (6 new): TAK, CLIENT_HIDDEN, LOST_AND_FOUND, TAK_TRACKER, ROUTER_LATE, CLIENT_BASE
- **New RebroadcastMode** (6 values): ALL, ALL_SKIP_DECODING, LOCAL_ONLY, KNOWN_ONLY, NONE, CORE_PORTNUMS_ONLY
- **New BuzzerMode** (5 values): ALL_ENABLED, DISABLED, NOTIFICATIONS_ONLY, SYSTEM_ONLY, DIRECT_MSG_ONLY
- **New Device Config Fields**: `rebroadcast_mode`, `buzzer_mode`, `tzdef`, `led_heartbeat_disabled`
- **Favorite Nodes** — Star (★) button on each node card in the Nodes view. Favorites appear at the top of the list, followed by nodes with signal, then by recency. Persisted in localStorage (`np_favorite_nodes`). Toast notification on toggle.
- **Auto Traceroute on New Node** — When enabled, automatically dispatches a traceroute immediately when a new node is discovered. Non-blocking background thread, respects 300s timeout and serialization lock. Configurable via `auto_traceroute_enabled` (default `false`) in Settings → Auto Responder.

### Fixed
- **OpenTopoMap tile DNS failures** — Corrected subdomain pattern from `abcd` to `abc` (OpenTopoMap only has a/b/c subdomains)
- **Map type initialization race condition** — Fixed Temporal Dead Zone error where `mapTypeKeyLabels` was used before declaration
- **MeshBeacon 2.8 enum population** — Manually added 2.8 RegionCode/ModemPreset values not present in meshtastic-python 2.7.x protobuf library
- **Traceroute timeout** — Increased from 90s to 300s
- **Traceroute logging** — Failures now logged at INFO level

## [1.16.0] - 2026-08-14
### Added
- **Comprehensive Test Coverage** — Added extensive unit and E2E test suite with 80 tests covering core functionality. Includes unit tests for configuration management, security scanning, MQTT bridge filtering, and API route handlers. E2E tests expanded to cover additional API endpoints including position history, waypoints, tags, traceroute, and node management. Coverage reporting integrated with pytest-cov, achieving 26% overall coverage with 95%+ coverage on critical modules like config.py and security_scanner.py.
- **Test Infrastructure** — Added pytest-cov for coverage reporting, improved test fixtures for comprehensive mocking, and structured test organization with unit and E2E test suites.

## [1.15.0] - 2026-08-14
### Added
- **E2E Testing Framework** — Added a comprehensive `pytest` + `Playwright` End-to-End testing suite for the Web UI and API. Tests execute entirely offline in a headless browser, utilizing robust mocking of the Meshtastic hardware and HA integration layers, and injecting stubs for CDN dependencies (Leaflet, Chart.js) to bypass network and Subresource Integrity (SRI) restrictions. Documented test execution commands in the README.

## [1.14.0] - 2026-08-13
### Added
- **Security Scanner** — Auto-detect weak or duplicate encryption keys across mesh channels. Displays visual warnings and classification (secure/weak/unencrypted) directly in the Packets view, plus a 🔓 badge on packet rows from flagged channels. Uses zero radio I/O for instantaneous scanning.

### Fixed
- **Device Configuration Bugs** — Fixed a routing bug in `main.py` where a parameterized route shadowed the reload endpoint, corrected inverted logger arguments in `connection.py`, replaced non-compliant `f-string` logging in `device_config.py`, and fixed state mutation/patch generation logic in the frontend `device_config.js`.

## [1.13.0] - 2026-08-12
### Added
- **Full-text message search** — Search across all message history using substring matching on message text. New `GET /api/messages/search?q=<query>` endpoint returns matching messages sorted by recency.
- **Scheduled messages** — Send mesh messages at a future time by providing `schedule_at` as a unix timestamp in the `/api/send` POST body. Messages are queued and automatically dispatched when their scheduled time arrives. Supports both broadcast and destination-cast messages.
- **Auto-traceroute on new node discovery** — Traceroutes are automatically dispatched when a new node appears in the mesh, without manual intervention. The coordinator fires `EVENT_TRACEROUTE_COMPLETE` when a node's traceroute timestamp updates.
- **Geofence triggers** — Fire Home Assistant device automations when a tracked node enters or exits a defined geographical zone. Uses GPS position history from the node to detect zone crossings.

### Fixed
- **Message persistence across addon restarts** — Scheduled messages and the message buffer are now persisted to `messages.json` with atomic writes, surviving addon restarts.

## [1.12.0] - 2026-08-10
### Added
- **Field validation on configuration** — The configuration API now validates numeric ranges, string lengths, and enum values against firmware-derived constraints (e.g. LoRa bandwidth 31–500 kHz, spread factor 7–12, coding rate 5–8) before writing to the radio. Out-of-range or malformed values are rejected with a clear 400 error.
- **Enum dropdowns in the Configure tab** — Fields backed by protobuf enums (modem presets, regions, roles, etc.) now render as proper dropdowns populated from the radio's firmware schema instead of free-text inputs, so invalid values can't be typed.
- **Manual LoRa parameter gating** — Backend now rejects direct edits to the manual radio params (`bandwidth` / `spread_factor` / `coding_rate` / `frequency_offset`) while `use_preset` is enabled, matching the UI's greyed-out fields and preventing preset override.
- **Extra danger confirmations** — Changing the LoRa region or updating WiFi/MQTT credentials now prompts for confirmation in the UI before being sent to the device.

### Fixed
- **Configuration form no longer relies on value types** — The Configure tab uses the backend's per-field schema (types, enum options, min/max, max length) so enum and numeric fields render correctly regardless of the serialised value.

## [1.11.0] - 2026-08-10
### Added
- **Node Identity in Settings** — New "Node Identity" section in the Web UI Settings tab displays the connected Meshtastic node's ID, long/short names, hardware model, firmware version, region, and role when connected.
- **Enhanced `/api/status`** — Now returns full node identity (`my_info.node_id`, `long_name`, `short_name`, `hw_model`, `firmware_version`, `region`, `role`) instead of just the node number.

### Fixed
- **Settings refresh showing "not connected"** — The UI now properly surfaces connection status and hides the Node Identity group when the Meshtastic node is not reachable, making it clear when the TCP link is down.

## [1.10.0] - 2026-08-08
### Added
- **Remote Device Configuration** — A brand new "Configure" tab in the Web UI that allows you to view and edit your mesh radio's configuration directly.
- **Dynamic Schema Generation** — Configuration schemas are live-introspected from the connected radio's firmware, ensuring compatibility across different Meshtastic versions without hardcoding fields.
- **Danger Zone Confirmations** — Advanced changes (like setting role to ROUTER or disabling LoRa TX) now prompt for confirmation to prevent accidental misconfigurations that could strand the node.
- **Reboot Banners** — The UI detects when you make a change that requires a reboot (e.g., LoRa or Network settings) and alerts you.
- **Safe Write Path** — Thread-safe radio configuration writes ensure that reading and writing configuration don't interfere with live message streams or background radio operations.

## [1.9.4] - 2026-08-07
### Added
- **Comprehensive Settings page** — The Web UI Settings tab now displays *every* addon configuration option instead of a subset. Newly surfaced settings include the MQTT broker port, MQTT username/password presence (masked), MQTT portnum allowlist and node blocklist, the Home Assistant token-validation toggle, the Telegram authorized-chat-IDs list, and the Auto Responder group (status + welcome message).
- **Settings API completeness** — `/api/status` now exposes the full runtime config: `disable_token_validation`, `mqtt_port`, `mqtt_username_set`, `mqtt_password_set`, `mqtt_portnum_allowlist`, `mqtt_node_blocklist`, `telegram_authorized_chat_ids`, `auto_responder_enabled`, and `auto_responder_message`.
- **Settings rendering polish** — Secrets are always masked (`●●●●●● (set)` / `Not set`), MQTT/Telegram rows show values only when the respective feature is enabled (otherwise `—`), proxy host/port rows render only in `proxy` connection mode, and the legacy single `telegram_chat_id` is shown alongside the newer authorized-chat-IDs list so no field is hidden.

## [1.9.3] - 2026-08-07
### Added
- **Telegram channel selection** — The `/send` command now accepts an optional channel: `/send 1 <message>` (or `/send #1 <message>`) broadcasts to that channel instead of always using the primary (Ch 0). A bare numeric message like `/send 5` is still treated as text and goes to Ch 0.
- **`/channels` command** — Lists the radio's configured channels with their indices (marked ✓ for channels relayed to Telegram), making it easy to pick a channel for `/send`.

## [1.9.2] - 2026-08-07
### Fixed
- **Telegram reply routing** — Replying to a forwarded mesh message in Telegram now reliably routes back to the channel or node the message originated from. Replies are matched by the forwarded message's Telegram `message_id` (tracked when relaying), so routing no longer depends on parsing the displayed text, which Telegram's Markdown rendering could alter and send to the wrong (default) channel. Text-based parsing is kept as a fallback for older messages.

## [1.9.1] - 2026-08-07
### Fixed
- **Telegram relay channels config** — `telegram_forward_channels` is now a plain string field in the addon config UI (e.g. `0, 1, 2`) instead of a list. This works around a Home Assistant frontend bug that serialises list-typed options as a scalar string, which Supervisor rejected with "Invalid list for option 'telegram_forward_channels'" and made it impossible to add channels. The legacy list form (e.g. `[0, 1, 2]`) is still accepted.

## [1.9.0] - 2026-08-07
### Added
- **Auto Responder** — Added a configurable auto-responder that automatically sends a direct message to any newly discovered node on the mesh. You can enable this and set your custom welcome message in the addon Configuration tab.

## [1.8.1] - 2026-08-07
### Fixed
- **Telegram Broadcast Syncing** — Plain text messages sent from authorized Telegram chats are now prefixed with the sender's name (e.g. `[Name] Message`) before broadcasting to the mesh. This ensures the messages are visually distinct in the NodePulse Web UI and on receiving mesh radios, rather than appearing as anonymous local broadcasts.
- **Telegram confirmation** — Added a silent-failure fix where the Telegram bot now replies with a ✅ confirmation when a plain message is successfully forwarded to the mesh.

## [1.8.0] - 2026-08-06
### Added
- **Telegram Bot Integration** — Bidirectional Telegram Bot bridge using the existing `aiohttp` library (no new dependencies). Inbound mesh text messages are automatically relayed to an authorized Telegram chat with sender name, channel/DM context, and live SNR data. Commands sent from the authorized chat are executed against the live mesh radio.
- **Bot commands** — `/status` (radio state + node count + battery), `/nodes` (top 20 by last-heard with SNR), `/send <text>` (broadcast to primary channel), `/dm !nodeid <text>` (direct message to a specific node), `/help` (command reference).
- **Security controls** — All incoming Telegram messages are filtered against the configured `telegram_chat_id`. Any message from an unauthorized chat is silently discarded, preventing unauthorized users from issuing mesh commands even if they know the bot's username. Outgoing (self-originated) mesh messages are never echoed back to Telegram to prevent relay loops.
- **Telegram Settings UI** — New "Telegram Bot" panel in the Web UI Settings tab displaying integration status, token presence, authorized chat ID, channel relay filter, DM relay state, and command permission state.
- **Config schema** — Six new addon options: `telegram_enabled`, `telegram_bot_token`, `telegram_chat_id`, `telegram_forward_channels`, `telegram_forward_dms`, `telegram_allow_commands`. All optional with safe defaults so existing installs are unaffected.
- **`app/telegram_bot.py`** — New `TelegramBot` class following the same lifecycle pattern as `MqttBridge` (start/stop managed in `main.py`).

## [1.7.0] - 2026-08-06
### Added
- **Advanced MQTT Bridge** — Integrated a robust, bidirectional MQTT bridge allowing ingestion of external mesh traffic directly from brokers (e.g., mqtt.meshtastic.org). Features a multi-stage filtering pipeline including Node-ID blocklist, PortNum allowlist, and a Geospatial bounding box (which caches node positions to block subsequent non-position packets from out-of-bounds nodes).
- **Outbound MQTT Forwarding** — Optional forwarding of filtered MQTT traffic to the local radio interface via `MqttClientProxyMessage`.
- **MQTT Settings UI** — Dedicated Web UI settings panel to view the active MQTT bridge configuration, including broker address, topic, geo-filter bounds, and forwarding status.

## [1.6.0] - 2026-08-06
- Version bump

## [1.5.0] - 2026-08-06
- Version bump
## [1.4.0] - 2026-07-30
### Added
- **Waypoints (Mesh + Local)** — Full waypoint support: capture inbound `WAYPOINT_APP` protobuf packets from the mesh, persist to `waypoints.json`, and display as amber teardrop markers with emoji icons on the map. Locally create waypoints via a floating "📍 Waypoint" panel with name, description, icon, and lat/lng (click-to-fill from map click). Delete waypoints from marker popups. Survives addon restarts.
- **Waypoint REST API** — `GET /api/waypoints` (all active/non-expired), `POST /api/waypoints` (create local), `PATCH /api/waypoints/{id}` (update position/fields), `DELETE /api/waypoints/{id}` (remove). Waypoints broadcast by the mesh are automatically upserted by canonical `from_id` + `wp_int_id`.
- **Draggable waypoint markers** — Waypoints on the map are draggable; dragging sends a `PATCH` to persist the new lat/lng immediately.
- **Optional GPS for waypoints** — lat/lng are no longer required when creating a waypoint. If omitted the waypoint is placed at the map centre and can be dragged into position.
- **Ruler measurement tool** — Click-to-measure point-to-point distances on the map with dashed amber polylines, midpoint distance labels, and amber circle markers at each point. Points appear immediately on each click.
- **Ruler elevation profile** — Samples elevation along the measured path from node position history altitudes (IDW interpolation). Displays total distance, elevation gain, elevation loss, and a canvas-drawn elevation profile chart (altitude vs distance with gradient fill, grid lines, and axis labels). Profiles sampled at up to 500 points (one per 5m) for smooth rendering.
- **Ruler minimises map toolbar** — Activating the ruler collapses the map filter bar to a compact floating pill showing only the ruler button, maximising map space for measurement.
- **feature_comparison.md** — Updated waypoints gap row from ❌ to ✅ for both "Waypoints (send/receive)" and "Waypoints on map". Gap analysis revised to note NodePulse now leads on this capability.

### Changed
- **Waypoint polling** — Waypoints fetched every poll when map view is active, every 8 polls otherwise, keeping markers fresh without excessive API load.

### Fixed
- **Missing `handle_set_tags` import** — Added missing `handle_set_tags` to the route import list in `main.py` that caused a `NameError` at startup.
- **Missing `handle_delete_node` import** — Added `handle_delete_node` to route imports in `main.py` and registered `DELETE /api/node/{node_id}`.

### Added
- **Delete single node** — New `DELETE /api/node/{node_id}` endpoint and `deleteNode()` API function. Each node card now has a "Delete" button (red danger styling) that removes the node from the persistent store with a confirmation prompt.

## [1.3.0] - 2026-07-30
### Added
- **Message History Export (JSON/CSV)** — Download full or per-conversation message history directly as JSON or formatted CSV from the Web UI thread header or via the `GET /api/messages/export` REST API.
- **Lazy-Loaded Message History UI** — Default view now loads only today's messages with a "Load previous days" pagination trigger to eliminate scroll stutter and heavy DOM loads for long-running threads.
- **Browser Notifications & Packet Inspector** — Updated feature parity comparison and unique advantages documentation reflecting full real-time packet inspection, LoRa sniffer stats, and Web UI notifications.

### Changed
- **Scroll Positioning Refactor** — Implemented intelligent pre-render scroll calculation (`list.dataset.lastConv`) in `renderMessagesThread` to automatically scroll to bottom on new messages or conversation switches while preserving scroll position when loading historical days.
- **Dead Code Cleanup** — Removed legacy `renderMessageList` duplicate function to streamline message thread rendering.

### Documentation
- Updated `feature_comparison.md` to reflect 1.3.0 capabilities (Packet Inspector, Sniffer, Browser Notifications, Docker Standalone, Signal Quality Rating, CSV/JSON Message Exports).
- Published comprehensive 4-phase development roadmap in `nodepulse_gap_resolution_plan.md`.

## [1.2.1] - 2026-07-29
### Added
- **PWA support** — Added missing `apple-mobile-web-app` meta tags and a proper viewport configuration to prevent iOS/Android zooming when tapping input fields.

### Changed
- **Cleaner mobile header** — Completely removed the redundant scrolling navigation tabs on mobile screens (≤768px), uncluttering the top bar to rely fully on the hamburger sidebar.

### Fixed
- **Thread safety** — Fixed a lock ordering bug in `_save_traceroutes` (which used the wrong lock, causing data races) and removed an unnecessary lock in `_schedule_save`.
- **Relay API crash** — Fixed a missing `NodePulseCoordinator` import in `api.py` that caused a silent `NameError` at parse time in newer Python versions, breaking the "Track in HA" views.
- **GeoLocation entity updates** — Removed dead code looking for a non-existent `position_fixes` key and correctly exposed `position_fix_count`.
- **Packet Inspector on mobile** — Attached the missing `.packet-table` CSS class to the HTML table so it scales down correctly on phones without blowing out the layout.
- **Dead code** — Removed unreachable `switchView` duplicate check in the node picker.

## [1.2.0] - 2026-07-28
### Added
- **Standalone Docker support** — Run NodePulse without Home Assistant using `Dockerfile.standalone`. Full guide in `STANDALONE_DOCKER.md`. All Web UI features work standalone; only HA integration relays (sensor entities, notify platform) require HA.
- **Signal strength filter** now uses `snr_avg` (rolling average of last 10 packets) instead of the instantaneous `snr` value for more stable filtering.

### Fixed
- **6 JavaScript bugs** — see `nodepulse-addon/CHANGELOG.md` for full breakdown:
  - `notifyNode` dead `try/catch/await` rollback simplified
  - `_originalColor` shallow clone → deep clone in topology graph
  - `_applySearchHighlight` color comparison missing `opacity`
  - `_buildNodeTooltip` broken indentation (syntax error in strict mode)
  - `app.js` init() localStorage loading indentation
  - `storeMessage` outgoing echo dedup now matches on `destination` + `channel` to prevent false dedup

### Changed
- **Version bump** — 1.1.0 → 1.2.0.

## [1.1.0] - 2026-07-24
### Added
- **Packet Inspector column sort/filter** — Clickable column headers on the packets table for sorting (asc/desc) and filtering by unique values via dropdown per column. Active filters and sort direction indicated on headers.
- **Short names in packet table** — From and To columns now show the node short name next to the hex ID.
- **Complete README rewrite** — Restored architecture diagrams (system overview, poll cycle, entity model), full installation guide, addon configuration docs, troubleshooting, and development sections.
- **Packet table now fully populates** — Fixed ID mismatch between HTML (`packet-table-body`) and JS (`packet-tbody`) that prevented the table from rendering.
- **Sniffer stats toggle works at all viewport sizes** — Moved `.hidden` utility class from `@media` query into global CSS scope.
- **Portnum filter input binding fixed** — Corrected ID mismatch (`pkt-filter-port` vs `pkt-filter-portnum`) so the filter works.
- **Sniffer distribution bars render correctly** — Fixed ID mismatch (`sniffer-dist-bars` vs `sniffer-bars`).

### Changed
- **Version bump** — 1.0.0 → 1.1.0 (minor release with new features and polish).

## [1.0.0] - 2026-07-23
### Added
- **Major Release** — NodePulse reaches 1.0! Stable release with full feature parity.
- **Packet Inspector** — Real-time packet capture ring buffer showing every inbound Meshtastic packet with portnum, source/destination (with short names), channel, SNR, hop count, ACK status, and expandable JSON detail. Filter by portnum or node ID, export to JSON/CSV, and view live sniffer stats (packets/min, unique nodes, portnum distribution).
- **Dynamic Topology Toolbar** — Interactive toggles for node names, traceroute edges, neighbor edges, physics simulation, plus node search with real-time highlight filtering and a "Reset" layout button.
- **Coverage Heatmap Loading Indicator** — Toast notification "Loading heatmap data…" while initial position history fetches.
- **Heatmap Canvas Optimization** — Patched leaflet-heat's simpleheat with `willReadFrequently: true` canvas hint, silencing Chrome DevTools warnings and improving redraw performance.
- **Zero-Size Canvas Crash Guard** — Defensive monkeypatch and size checks prevent `IndexSizeError` when map container hasn't laid out yet.
- **Heatmap Refresh Cadence** — Position history now fetched every poll (15s) when heatmap is visible; reverts to 8-poll cadence when hidden.
- **Heatmap Toggle UX** — Enabling heatmap (🌡 button or **M** key) immediately triggers a fresh fetch.
- **Redundant Redraw Elimination** — `updateTrails` compares serialized heatmap points; skips expensive `setLatLngs()` if unchanged.

### Changed
- **Quality Scale Promoted** — Integration manifest quality scale promoted to `silver`.

## [0.2.38] - 2026-07-23
### Fixed
- **Raw HTML in topology node hover tooltips** — Escaped HTML entities in node tooltip text when hovering over nodes in the topology graph. Ensures special characters (`<`, `>`, `&`, etc) are rendered as text rather than parsed as HTML in tooltips.

## [0.2.35] - 2026-07-22
### Added
- **Coverage Heatmap** — Added a visual heatmap layer to the map views showing signal strength (SNR) based on node position history and current live node positions. Includes a toggle button and a dynamic gradient legend.
- **Network Topology Graph** — Added a new "Topology" tab that visualizes the mesh network using a force-directed graph. Uses traceroute and neighbor data to draw connecting edges. Edges are color-coded by signal strength (SNR). Nodes are styled based on their Role (Router/Repeater/Tracker/Client). Includes a toolbar with a legend and a "Fit" button to center the graph.

## [0.2.34] - 2026-07-22
### Fixed
- **Integration 404 Error** — Fixed a `NameError` during the integration's async setup phase caused by dynamic sensor class instantiation. The integration now properly loads and registers its API endpoints, resolving the "Track-node relay rejected" 404 errors.

## [0.2.33] - 2026-07-22
### Changed
- **Cleaner per-node sensors** — NodePulse now discovers sensors from a single `SENSOR_CLASSES` list keyed by `unique_id`, and only registers a sensor when it actually has a value. Hardware metrics a node doesn't report (e.g. temperature, humidity, gas resistance) no longer clutter the UI with "Unknown" entities. Entity removal bookkeeping is fixed to track the sensor's `unique_id` so stale entities are cleaned up correctly.
- **Atomic addon persistence** — `nodes.json`, `messages.json`, `tags.json`, `position_history.json`, and `channels.json` are now written via a temp file + `fsync` + atomic `os.replace`, so a crash or power loss mid-write can no longer corrupt the store.
- **Correct destination handling** — `sendText`, `sendTraceRoute`, and `requestPosition` now convert `!hex` node IDs to their numeric form before calling the Meshtastic library; pending-destination bookkeeping for traceroutes and position replies now uses the shared connection lock. Fixes missed position replies / wrong traceroute attribution on some firmware/library versions.
- **Mobile UI polish** — Node cards stack in a single column on phones to prevent squishing/overlap; the status bar and node-count badge render after both status and nodes load so the count is accurate on first paint.
- **Map overlay defaults** — Self→node links and peer proximity links now start hidden by default, reducing clutter; traceroute paths, position-history trails, and node-name labels remain on.

## [0.2.32] - 2026-07-22
### Fixed
- **Track-in-HA 401 Unauthorized** — The addon's relay to the integration (track-node / tracked-nodes) now includes the `SUPERVISOR_TOKEN` as a `Bearer` token in the `Authorization` header. When `disable_token_validation` is set to `true` in the addon config, the addon sends an `X-NodePulse-Skip-Token` header instead, and the integration bypasses token validation entirely. Fixes the "Could not reach NodePulse integration" error on HAOS and custom Docker installs.

## [0.2.31] - 2026-07-21
### Added
- **Dark/light theme toggle** — Persistent theme switch via header button, stored in `localStorage`. CSS variable overrides for light backgrounds, borders, and text.
- **Collapsible map overlay controls** — Toggle bar collapse button with `localStorage` persistence; keyboard shortcut **C**.
- **Message history search** — Free-text filter over message text and sender name per conversation thread.
- **Node tagging / groups** — `tags.json` persistence on the server; `GET`/`PUT /api/tags` endpoints; comma-separated tag editor on each node card.
- **Map KML/GPX export** — Export visible GPS-fixed nodes as KML or GPX from the Map view filter bar.
- **Neighbor info panel** — `NEIGHBORINFO_APP` protobuf capture in the packet listener, stored per-node in the cache and rendered on node cards with per-peer SNR chips.
- **Position history trails** — GPS fix history (up to 200 entries/node) stored server-side in `position_history.json`; `GET /api/position-history` endpoint; deep-orange polylines on both maps with **H** key toggle.
- **Packet/airtime utilization trends** — Channel utilization and airtime utilization charts alongside existing SNR/RSSI/count charts, using a 120-point (~30 min) rolling window.

### Changed
- **CORS** — `PUT` method added to the allowed list for the tags endpoint.
- **Charts** — `ChartManager.addPoint()` now accepts `chanUtil` and `airUtil` parameters; utilization data is sampled from the self/gateway node.
- **Position capture** — `_capture_position` now records each fix into the position history ring buffer and triggers a background persistence write.

## [0.2.30] - 2026-07-19
### Added
- **Map node filter** — The Map view now has a filter bar to search/shown nodes by **short name / long name / ID** (text), **max hops away** (0–5+), and **last heard** within a time window (15 min / 1 h / 6 h / 24 h) or **cached-only** (stale nodes). A live "N shown" counter updates on filter changes and on every poll. Applies to both the dashboard mini-map and the full map.

## [0.2.29] - 2026-07-19
### Added
- **Persistent node store (survives the radio's 250-node DB limit)** — The addon now persists every node it has ever seen to `nodes.json` and re-injects any node the radio no longer reports (its bounded node DB evicts the oldest heard nodes once full). New nodes still appear immediately; evicted nodes are flagged `stale` (faded "cached" badge in the Web UI, `stale` attribute on the HA device tracker) and keep their last-known position on the map. History survives addon restarts.

## [0.2.28] - 2026-07-19
### Added
- **Last-known-position retention** — When a node loses GPS or stops reporting, the radio sends `position=None` and the map marker would previously vanish. NodePulse now keeps the most recent good latitude/longitude/altitude (from the prior fix or a "Req. Position" reply) so the node stays on the map until a newer fix arrives. A new `last_position_fix` attribute (epoch seconds) is exposed on each mesh node device tracker so you can see how stale a fix is and drive automations on it.

## [0.2.27] - 2026-07-19
### Fixed
- **All channels now show in the Web UI** — The addon read channels from `interface.localConfig.channel_settings`, which the radio frequently leaves empty, so the message dashboard only ever showed the "Primary" tab (until a message arrived on another channel, which created the tab via the message feed). Channels are now read from `interface.localNode.channels` (the `Channel` list the library populates for every slot during connect), filtering out disabled slots while always keeping the primary. Secondary channels (e.g. "LongFast") now appear immediately.

## [0.2.26] - 2026-07-19
### Fixed
- **Track-in-HA toggle no longer 503s** — The Web UI's "Track in HA" toggle previously awaited a full coordinator refresh inside the addon-relayed HTTP request, which could exceed the ingress proxy timeout and return HTTP 503 even when the change succeeded. The refresh is now fire-and-forget; the request returns immediately.
- **Message-replay flood guard** — After an addon restart (or a dedup-set reset) the integration could surface the entire message history as "new", flooding the logbook and device triggers. New messages are now keyed on a stable id/timestamp, and a poll that reports more than a handful of "new" messages is capped so only the most recent few are surfaced.
- **`ignored_nodes` option now works** — The integration options-flow "Ignored Node IDs" setting was never applied on the HA side. It now stores normalised `!hex` ids and the coordinator filters those nodes (and their messages) on every poll, mirroring the addon's own filter.
- **Async event-loop cleanup** — Replaced the deprecated `asyncio.get_event_loop()` (which raises on Python 3.12+) with `asyncio.get_running_loop()` in the addon's traceroute dispatch.
- **Service cleanup on removal** — Integration-level service actions are now removed when the last config entry is unloaded, instead of leaking after the integration is removed.
- **Shared JS helpers** — `escapeHtml` / `haversineKm` / `formatDistance` were de-duplicated into a single `web_ui/js/util.js` module used by both `app.js` and `map.js`.
- Bumped quality scale to `silver` in the manifest.

## [0.2.25] - 2026-07-19
### Added
- **Live channel refresh** — The addon now re-reads the node's channel configuration immediately after each (re)connection and on a 5-minute background loop, so the Web UI's channel list and conversation tabs stay in sync with the radio's actual channel settings without waiting for a config push.

## [0.2.24] - 2026-07-17
### Fixed
- **Channel tabs now appear immediately** — The message dashboard only showed channel conversation tabs after a message arrived on each channel. Tabs are now seeded from the node's configured channel list on load, and show the real channel name (e.g. "LongFast") instead of a generic "Channel N" label.
- **Short names in chat** — Received messages now show the sender's short name in the message window (addon records the short name; the UI also resolves it from the live node list, so even previously-saved messages display correctly).
- **Logbook crash** — Fixed a `NameError: name 'entry' is not defined` that broke the mesh-message listener and logbook entries.
- **Notify platform load crash** — Replaced the removed `hass.helpers.discovery.async_load_platform` call with the module-level `async_load_platform`, fixing `AttributeError` on newer Home Assistant versions.

## [0.2.23] - 2026-07-17
### Changed
- Mobile-friendly addon UI: sidebar becomes a slide-in drawer with a hamburger toggle inside the HA mobile app, dashboard stacks into a scrollable single column with usable map/message heights, dynamic viewport height (`100dvh`) so it fits the ingress iframe, and safe-area insets for notched phones.

## [0.2.22] - 2026-07-17
### Fixed
- **Integration could not reach the addon at runtime** — Setup validated connectivity via a supervisor DNS fallback but persisted the user's raw host input. At runtime the coordinator then retried that (often non-resolving) host and failed with "No NodePulse addon host was reachable". Setup now persists the *working* candidate URL, and the candidate list also includes the `addon_nodepulse` supervisor DNS forms. Re-run the integration setup (or re-add the entry) to pick up the corrected host.

## [0.2.21] - 2026-07-17
### Changed
- Version bump: traceroute fire-and-forget dispatch, RSSI "Not provided" labeling, logbook unavailable-spam fix, and landscape settings layout.

## [0.2.20] - 2026-07-17
### Changed
- Version bump reflecting the device-action / service-registration fixes from 0.2.19.

## [0.2.19] - 2026-07-17
### Fixed
- **Device actions were non-functional** — The device-action entrypoint was named `async_call_action`, but Home Assistant calls `async_call_action_from_config(hass, config, variables, context)`. Renamed to the correct hook so `send_message` / `request_position` / `trace_route` device actions actually execute.
- **Service actions broke with multiple config entries** — Services were registered per `async_setup_entry` and removed on unload of any single entry, which would break other entries and fail when no entry was loaded. Moved registration to `async_setup` / `async_unload` (integration level) and made handlers resolve the coordinator at call time.
- **Non-functional triggers on the integration device** — `message_*` device triggers/actions are now only offered for actual mesh-node devices, not the integration-level gateway device (whose identifier is a config-entry id).
- **HTTP errors during actions marked entities unavailable** — Service/notify/action failures now raise a plain error instead of `UpdateFailed`, so a bad node id or offline addon no longer flips all entities to unavailable.
- Removed unused imports.

## [0.2.17] - 2026-07-17
### Added
- **Notify platform** — `notify.mesh_<entry>` entity so mesh messages can be sent from any Home Assistant automation, script, or the UI (supports `target` node ID and `data.channel`).
- **Per-channel notify entities** — One `notify.mesh_<entry>_channel_<name>` entity per configured Meshtastic channel, mirroring the official integration's channel targets. A channel-pinned entity always broadcasts on that channel.
- **Integration service actions** — `nodepulse.send_message`, `nodepulse.request_position`, and `nodepulse.trace_route` for direct automation control without the Web UI.
- **Device triggers** — Per tracked-node automations fire on `message_received` / `message_sent`, and `channel_message.received` (scoped to a channel or direct-message context).
- **Device actions** — Per tracked-node device actions: `send_message`, `request_position`, and `trace_route`.
- **Logbook integration** — Sent and received mesh messages are recorded in the Home Assistant logbook timeline.

## [0.2.16] - 2026-07-17
### Fixed
- **Message sensors showed nothing for tracked nodes** — The "Last Message Received/Sent" sensors failed to match messages because of node-ID formatting differences (leading `!` / letter case) between the tracked node ID and the message `from_id`/`to_id`. Matching is now normalised so it always aligns.
- **Unreliable "outgoing" direction** — The sensors now derive the local self-node ID from the coordinator's status payload instead of the `outgoing` flag captured by the addon at packet time (which could be wrong when `myInfo` wasn't available yet). "Last Message Sent" now populates correctly when you DM a tracked node.

## [0.2.15] - 2026-07-17
### Fixed
- **Threading/lock hygiene** — Blocking network I/O (TCP connect, `sendTraceRoute`, `sendPosition`, `fetchNodeDB`, `interface.close`) is now performed outside the shared lock so polling threads (node list, health probe) are never stalled during long radio round-trips.
- **Overlapping traceroute requests** — Replaced the single shared pending-destination slot with a FIFO stack so multiple concurrent traceroute requests each attribute to the correct target instead of clobbering one another.
- **Spurious position replies** — Position requests now track the destinations they were sent to (`_pending_position_dests`), so periodic broadcast POSITION_APP packets are no longer mistaken for responses to a request we made.
- **Traceroute save storms** — Persistence is now debounced (`_TRACEROUTE_SAVE_DEBOUNCE`) so a burst of traceroute replies doesn't spawn a save thread per capture; the latest state is flushed once the burst settles.
- **Telemetry field mapping** — Corrected the device-metrics/telemetry field names to match current meshtastic payloads: `uptimeSeconds`, `relativeHumidity`, `barometricPressure` (was `uptime`, `relative_humidity`, `barometric_pressure`).

### Changed
- **Destination validation** — Traceroute and position request endpoints now validate the `destination` node ID with a strict `^![0-9a-fA-F]{1,8}$` regex and return a clear error if it isn't a canonical Meshtastic node ID.
- **Canonical node-ID formatting** — Extracted `_node_id_from_num()` so every raw packet number is formatted as a `!xxxxxxxx` ID in one place.

## [0.2.14] - 2026-07-17
### Fixed
- **Deadlock risk eliminated** — Fixed a lock ordering issue in `connection.py` that could cause the background pubsub thread to deadlock against the main poll loop.
- **Double-Subscribe Bug** — Fixed an issue where reconnecting to the node would double-register the pubsub listener, duplicating all received messages.
- **Starved Health Probes** — Narrowed the lock scope in `_is_interface_healthy` so it doesn't get blocked by the UI polling the node list.
- **Concurrent Connect Race** — Added a connection guard to prevent two threads from attempting to reconnect simultaneously.
- **Map Marker Icons** — The map UI now correctly updates the "self node" icon when the selected self node changes.
- **Checkboxes missing on load** — The "Track in HA" toggles now populate immediately upon initial UI load rather than waiting for the second poll cycle.

### Changed
- **Relay Performance** — The dashboard polling loop no longer waits for the potentially slow `fetchTrackedNodes` relay endpoint before rendering nodes and maps, eliminating long UI load times.
- **Poll Cadence Optimization** — Shifted the UI polling logic from `setInterval` to self-rescheduling `setTimeout` to prevent overlapping requests on slow networks.
- **DOM Rendering Performance** — Added data fingerprinting to the node list and grid so they no longer tear down and rebuild hundreds of DOM elements on every cycle if the node data hasn't changed.
- **Security hardening** — Added Subresource Integrity (SRI) hashes to all CDN-loaded JavaScript and CSS to prevent supply-chain vulnerabilities.

## [0.2.13] - 2026-07-16
### Added
- **New per-node sensors** matching the official Meshtastic integration: Voltage, Channel Utilization, Air Utilization TX, Uptime, Role, and Gas Resistance (when reported by the node's telemetry).
- **Per-node "Online" binary sensor** — True when a tracked node was last heard within the last 3 hours, enabling per-node online/offline automations.

### Fixed
- **Always-live mesh connection** — the addon now performs an *active* health probe (querying the node DB) every 60s instead of a passive socket check, so a silently-dropped session (node reboot, single-slot firmware reclaim, network blip) is detected and automatically reconnected instead of reporting "connected" while delivering no data.
- **Addon reachability** — broadened the list of supervisor DNS hostnames the integration tries (including `addon_nodepulse` and `a0d7b954-nodepulse_addon`), and clarified the "no host reachable" error so it's clear this is a HA↔add-on network/ingress issue, not the mesh node being offline.

## [0.2.12] - 2026-07-16
### Added
- **GPS coordinate sensors** for each tracked node: Latitude, Longitude, and Altitude, sourced from the node's last known position fix.
- **Separate sent/received message sensors** — the single "Last Message" sensor is replaced by "Last Message Received" and "Last Message Sent" so automations can trigger on each direction independently.

## [0.2.11] - 2026-07-16
### Added
- **Message sensor entities** for each tracked node, showing the last received text message for automation triggers.
- Device tracker entities now reliably create when tracking nodes with GPS coordinates.

### Fixed
- **Device tracker race condition** - Fixed a race condition where device trackers weren't created when toggling "Track in HA" because GPS data wasn't available yet. Now uses `async_config_entry_first_refresh()` to ensure data is loaded before discovery.
- **Message data fetching** - Updated coordinator to fetch messages from the addon API alongside status and nodes data.

## [0.2.10] - 2026-07-16
### Changed
- Added repository metadata files (`repository.json` and `hacs.json`) to allow direct installation from the GitHub repository via the Home Assistant Add-on Store and HACS.
- Polished the addon description and generated a custom icon/logo for a professional appearance in the Home Assistant UI.
- Rewrote the installation documentation to reflect the new GitHub installation paths.
- Synchronized `CHANGELOG.md` and `DOCS.md` into the `nodepulse-addon/` directory so they render correctly in the Home Assistant Add-on Store tabs.

## [0.2.9] - 2026-07-16
### Fixed
- Fixed an `ImportError` preventing integration load on HA 2024.5+ by replacing the deprecated `TEMP_CELSIUS` and `SIGNAL_STRENGTH_DECIBELS` constants.
- Resolved an `Integration error: 'bool' object can't be awaited` exception when clicking "Track in HA" on newer HA versions.
- Fixed a bug where partial traceroutes failed to render on the map if an intermediate hop lacked a GPS fix.
- Map links now clean up correctly and won't visually persist after they've been cleared.
- Fixed an issue where the HA integration would hang for 10 seconds per incorrect local DNS slug before finding the addon.

### Changed
- The Web UI Settings tab now dynamically displays the live config including connection status, logging, and integration keys instead of static text.
- Device names in the Home Assistant integration now use the node's long/short name instead of just the raw hex ID.
- Promoted the first successfully resolving HA addon DNS slug to avoid repeatedly testing unreachable ones in every poll.

## [0.2.8] - 2026-07-16

### Fixed
- **Integration Connection Failure:** Relaxed the setup validation in `config_flow.py`. The integration now successfully configures as long as the addon is reachable, no longer blocking setup if the Meshtastic radio is temporarily offline.
- **Silent Logger Crashes:** Fixed a `TypeError` bug across the custom integration (in `__init__.py`, `config_flow.py`, `sensor.py`, and `device_tracker.py`) caused by passing dictionaries to `logger.info`, which swallowed entity registration errors.
- **Data Race in Traceroutes:** Resolved a threading race condition where traceroute results were saved to disk without acquiring the shared nodes lock.
- **Misleading Log Noise:** First-boot connection attempts no longer emit a `WARNING` before actually trying to connect.
- **UI Message Deduplication:** Fixed a bug where identically-worded outbound messages sent minutes apart were aggressively suppressed; the deduplication window is now correctly limited to 3 seconds.
- **Map Popup `null` Values:** Guarded the HTML escaper in the map UI so missing node fields no longer display the literal string "null".
- **Misleading Setup Instructions:** Updated `strings.json` to correctly suggest the auto-discovered Supervisor addon host rather than `localhost`, preventing user confusion.

## [0.2.7] - 2026-07-16

### Fixed
- **Traceroutes were silently dropped.** `_capture_traceroute` referenced
  `target_id` before it was assigned, raising `UnboundLocalError` that the
  defensive `except` swallowed. Traceroute routes are now captured and shown
  on the map. (connection.py)
- **Captured GPS fixes lost on every poll.** `_get_nodes_sync` restored the
  previously captured latitude/longitude/altitude *after* overwriting them with
  the library's (often `None`) raw values. Position fixes from "Req. Position"
  now persist across polls. (connection.py)
- **Tracked HA entities vanished after a reload.** Per-node discovery used a
  module-level `registered_node_ids` set that survived `async_unload_entry`, so
  toggling "Track in HA" (which triggers a config reload) re-skipped already
  seen nodes and removed their entities. Bookkeeping now lives on the
  coordinator (per config entry) and resets on setup. (coordinator.py,
  sensor.py, device_tracker.py)
- **`persist_tracked_nodes` not awaited** — the `async_update_entry` coroutine
  was fire-and-forget; now awaited so the reload fires reliably. (coordinator.py)
- **Node-ID key mismatch.** Normalized `interface.nodes` keys (int or hex) to
  the canonical `!xxxxxxxx` form so traceroute/position merges and Web UI
  lookups stay consistent across meshtastic library versions. (connection.py)
- **Duplicate sent-message bubbles.** Meshtastic echoes our own DMs back,
  creating a second bubble; `storeMessage` now dedupes outgoing messages by
  text within a thread. (app.js)
- **Only the Primary channel was listed.** `_get_channels_sync` now reads the
  full `localConfig.channel_settings` list (filtered for configured channels)
  instead of the partial `interface.channels`. All your channels now appear in
  the channel selector. (connection.py)
- **Messaging card squashed.** The compose row crammed recipient + channel
  selector + textarea + send into one 360px row. Restructured into two rows
  (recipient + channel on top, textarea + send below) so the message list keeps
  its space. (index.html, main.css)

## [0.2.6] - 2026-07-15

### Added
- Per-node "Track in HA" toggle in the Web UI that creates/removes HA entities
  via the integration's local relay endpoints.
- Persistent message and traceroute storage across addon restarts.
- Conversation tabs (channels + DMs) with unread badges, per-thread history,
  and send status (sending/sent/failed + retry).
- Map overlay toggles: self links, peer proximity, traceroute routes, and
  node names.