# Changelog

All notable changes to NodePulse are documented here.

## [1.23.0] - 2026-08-30
### Fixed
- **Home Assistant Location State** — Fixed an integration compatibility issue with Home Assistant Core 2024+ where `device_tracker` and `geo_location` entities remained in an "Unknown" state despite having valid GPS coordinates. Removed Python `@property` overrides for `latitude`/`longitude` which conflict with the `propcache.api.cached_property` metaclass, replacing them with the standard `_attr_latitude` and `_attr_longitude` update pattern.
- **Location Entity Registration** — Fixed a `TypeError` crash (`object is not iterable`) in `NodeDiscovery.run` that prevented location entities from registering with the Home Assistant state machine. Ensured the entity factories correctly return an iterable list.

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

### Changed
- **Message buffer size** — Increased from 200 to 1000 messages to provide deeper immediate history access before archive lookup is needed.

## [1.21.7] - 2026-08-27
### Fixed
- **Message search pagination** — Fixed "Load previous days" button not appearing when searching messages. The search logic now properly checks for older messages before applying search filters, allowing users to expand the time window to find older matching messages.

## [1.21.6] - 2026-08-27
### Fixed
- **Traceroute path construction** — Fixed traceroute path display on both node cards and map. The logic now properly handles different firmware versions that may or may not include self/target nodes in the route array, preventing duplicate nodes in the path and ensuring correct multi-hop path visualization. Added comprehensive unit tests for path construction logic.

## [1.21.5] - 2026-08-24
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
- **Unit tests expanded** — Added comprehensive unit tests for node lookup logic, traceroute data preservation, device favorite communication, and traceroute path construction to prevent regression of the fixes.

## [1.21.4] - 2026-08-23
### Added
- **Editable Security & Admin Keys in Remote Admin** — The Remote Admin view's **Security & Admin Keys** card now lets you manage a remote node's `admin_key` list: paste a base64 admin/public key and click **Add**, one-click add *this gateway's* public key (from the "keys for targets" strip), and remove any existing key with its ✕. Saving rewrites the target's full admin-key list over the radio (requires a reboot to take effect) — the workflow for granting the gateway or any other node admin rights on the target. `public_key` / `private_key` remain read-only identity chips.

## [1.21.3] - 2026-08-23
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
- **Meshtastic 2.8 features surfaced in the Web UI** — the diagnostics, GPX, hops, signed-node, status-text and beacon capabilities (previously Telegram-only) are now available in the dashboard:
  - **Node Diagnostics modal** — "Diagnostics" button on every node card and map popup opens a panel with hops-away, rolling SNR + signal quality, battery/voltage/uptime, channel/air utilisation, the 2.8 noise floor, and environment telemetry. For the local gateway it also shows the **Mesh Beacon (2.8)** module status.
  - **GPX download** — "GPX" button on each node card / map popup downloads that node's position history as a GPX 1.1 track.
  - **Hops Distribution chart** — new dashboard bar chart of nodes per hop count from the gateway (`/api/hops`).
  - **Signed-node 🔒 badge + broadcasted status text** — shown on node cards, the node list, and map popups when a node advertises a public key / status message.
  - New backend routes: `GET /api/node/{id}/signal`, `GET /api/node/{id}/gpx`, `GET /api/hops`, `GET /api/beacon`.

### Changed
- **Telegram sender attribution** — forwarded mesh messages now show the sender's short name (e.g. `*Bob*`), appending the long name in parentheses when it adds clarity (e.g. `*Bob (Bob The Tester)*`), so incoming messages are immediately identifiable. The sender name is resolved from the live node snapshot and falls back to the persistent node store (e.g. for nodes heard via MQTT/traceroute). The node ID is still included for unambiguous replies.

## [1.21.2] - 2026-08-23
### Added
- **Meshtastic 2.8 features in the Telegram bot** — `/diag`, `/gpx`, `/hops`, `/waypoint`, `/beacon` commands, signed-node 🔒 + status badges in `/nodes`/`/where`, and a `/setpos` public-channel warning. Connection helpers `get_node_signal()`, `send_waypoint()`, `get_beacon_config()`, 2.8 noise-floor capture, and a channel `public` flag.

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

## [1.21.0] - 2026-08-20
### Added
- **Security keys surfaced in the UI** — The **Security & Admin Keys** section now renders in the Configure tab (and in remote nodes), showing `public_key` / `private_key` / `admin_key` as read-only, copyable base64 chips. Keys are base64-encoded in the API (`serialize_config_sections`) and base64-decoded on write (`validate_and_apply_patch`); the private key is masked until revealed. This is the exact workflow for wiring up remote admin: copy this gateway's public key into each target's **Security → Admin Keys**.
- **Gateway keys reference in Remote Admin** — The Remote Admin view now shows a "This gateway — keys for targets" strip with copy buttons for the gateway's public key and admin keys (from `GET /api/admin/available`), so you can grab the gateway's key without leaving the view. `remote_admin_capability()` returns `public_key` / `admin_keys` (base64).
- **`security` is now a reboot section** — Writing the Security section reports a reboot requirement, since admin-key/keypair changes only take effect on reboot.
- **Remote node administration** — New "Remote Admin" view (sidebar + header tab) that administers OTHER mesh nodes over the Meshtastic AdminModule: read/edit their full Config + ModuleConfig (schema-driven forms reusing the device-config cards), set the owner, reboot/shutdown with a delay, factory reset (config-only or full device), reset the remote NodeDB, set/clear a fixed position, sync the remote clock, and evict nodes from the remote NodeDB.
- **Admin capability detection** — The backend reports whether the gateway can administer remote nodes (`GET /api/admin/available`): an `admin` channel, Security admin keys, or admin channel enabled. The UI shows a warning banner when none is configured. Admin round-trips are bounded by a timeout (15 s per action, 25 s for a full config read) so a dead or non-ADMIN node can never hang the app. A channel merely NAMED `admin` is not capability on its own — firmware only honours it when `admin_channel_enabled` (the legacy toggle, now hidden from the mobile apps) is set — so `remote_admin_capability()` also checks for a PKC keypair (`public_key` + `private_key`).
- **Security admin-key support** — Remote admin no longer requires a channel literally named `admin`. Modern firmware administers via **Security → Admin Keys** (`Config.SecurityConfig.admin_key`): the gateway signs with its private key and the target accepts it over the primary channel. `remote_admin_capability()` treats non-empty admin keys, a PKC keypair, or `admin_channel_enabled` as valid capability; the fast-fail only fires when none are configured.
- **Correct admin send channel** — Admin traffic is sent on the channel the firmware actually honours: the PRIMARY channel (index 0) with PKC authentication against the target's `admin_key` list (firmware 2.5+ default), or the reserved `admin` channel only when `admin_channel_enabled` (legacy admin) is set. The meshtastic library otherwise sends to any channel named 'admin' — which firmware ignores unless legacy admin is enabled — making every round-trip time out. `_bind_admin_channel` wraps the remote node's `_sendAdmin` so the channel lookup is overridden just for that node's sends and restored afterwards.
- **Backend remote-admin API** — `GET /api/admin/{node_id}/config`, `PUT /api/admin/{node_id}/config/{section}`, and `POST /api/admin/{node_id}/action/{action}` in `routes.py`, with logic in the new `app/remote_admin.py`. Crucially, remote nodes are built WITHOUT `interface.getNode()`, which would call `our_exit()`/`sys.exit()` on a channel timeout and kill the addon.

### Changed
- **`device_config.py`** — Extracted `serialize_config_sections(local_config, module_config)` (the Config/ModuleConfig → dict + `_schema` serialization) so both the local and remote config readers share one code path.
- **Frontend reuse** — `device_config.js` now exports `renderDeviceConfigSections(container, data, options)` (pluggable save/toast hooks, optional local reboot banner) and its `SECTION_META`/`SECTION_ORDER`; `remote_admin.js` reuses the same schema-driven cards for remote config editing. `showToast` and `getSelfNodeId` are exposed on `window` for sub-view modules.

### Fixed
- **Empty string fields no longer serialize as `[]`** — The base64 encoder previously treated any empty iterable as an empty repeated-bytes list, so singular string fields (e.g. `wifi_ssid`, `wifi_psk`, `ntp_server`) read back as `[]` instead of `""`. Encoding is now driven by the field's declared protobuf type/label rather than guessed from the value.
- **Device-config read errors include the cause** — `GET /api/device-config` now appends the actual exception to the error response so transient radio/handshake failures are diagnosable instead of a bare "Failed to read device configuration".

## [1.20.0] - 2026-08-19
### Added
- **Terrain link analysis (LOS / Fresnel link budget)** — New "⛰ Terrain" button in the Map view opens a panel that lets you pick two nodes and computes a point-to-point radio link analysis over real terrain. The backend fetches elevation profiles from the DEM (default: OpenTopoData SRTM30m, configurable via `terrain_dem_url`), then reports free-space path loss, Fresnel zone clearance (with 4/3-earth earth-bulge correction), effective received signal, and verdicts (LOS clear, Fresnel margin, blocked). A profile chart draws the terrain cross-section with the LOS beam and first Fresnel zone. API: `GET /api/terrain/elevation` and `POST /api/terrain/link`.
- **3D terrain view** — New "🏔 3D" button in the Map view switches the map to a 3D terrain view (MapLibre GL, loaded on demand from CDN) using AWS Terrain Tiles (terrarium encoding) with hillshading and extruded node markers. The 3D view tears down cleanly when you toggle back or leave the Map view.
- **Frontend terrain support** — `web_ui/js/terrain.js` module wired into `app.js` (panel init, node-select population, 3D toggle handling, teardown on view switch); terrain panel styling added to `main.css`.

### Fixed
- **`_interpolate_nones`** — Helper added in `routes.py` to fill gaps in elevation profiles returned by the DEM before geometry computations run.

## [1.19.2] - 2026-08-19
### Fixed
- **Traceroute spam no longer wedges the app** — Running many traceroutes in quick succession could pile up an unbounded number of background tasks and threads (each serialized traceroute can block for up to 300 s), eventually freezing the addon. The pending queue is now capped (8); further requests are rejected with `dispatched: false` instead of being accepted and frozen.
- **Traceroute replies are attributed to the right node** — RouteDiscovery replies are now matched to the request that produced them instead of blindly popping the oldest pending request. This prevents replies from timed-out requests or auto-traceroutes from being stored under the wrong node and from leaking stale queue entries.
- **Auto-traceroute joins the queue** — Auto-traceroutes triggered by newly-discovered nodes are now registered in the bounded pending queue (deduped) and respect the cap, so they can no longer spawn an unbounded number of threads or steal a manual request's reply.
- **Lint policy + cleanup** — Added `ruff.toml` (strict-but-achievable rule set: `E4,E7,E9,F,UP,B,BLE,I,RUF,SIM`) and made `ruff check app tests/unit tests/e2e tests/conftest.py` fully clean: modernized type annotations (`Dict`→`dict`, `Optional`→`X | None`, `Callable` from `collections.abc`), combined nested `with`/`if` blocks, `raise ... from` chaining, timezone-aware `datetime.timezone.utc`, and strong references to fire-and-forget `create_task` Tasks (traceroute dispatch + MQTT forwarding) so they can't be garbage-collected mid-flight. Intentional boundary `except Exception` catches are marked `# noqa: BLE001`. Lint runs in CI.

## [1.16.0] - 2026-08-14
### Added
- **Comprehensive Test Coverage** — Added extensive unit and E2E test suite with 80 tests covering core functionality. Includes unit tests for configuration management, security scanning, MQTT bridge filtering, and API route handlers. E2E tests expanded to cover additional API endpoints including position history, waypoints, tags, traceroute, and node management. Coverage reporting integrated with pytest-cov, achieving 26% overall coverage with 95%+ coverage on critical modules like config.py and security_scanner.py.
- **Test Infrastructure** — Added pytest-cov for coverage reporting, improved test fixtures for comprehensive mocking, and structured test organization with unit and E2E test suites.

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
- **Full config in Settings tab** — The Web UI Settings view now mirrors every addon option: MQTT broker port, username/password presence (masked), portnum allowlist and node blocklist; the HA token-validation toggle; the Telegram authorized-chat-IDs list; and the Auto Responder panel (status + welcome message).
- **`/api/status` exposes complete config** — Added `disable_token_validation`, `mqtt_port`, `mqtt_username_set`, `mqtt_password_set`, `mqtt_portnum_allowlist`, `mqtt_node_blocklist`, `telegram_authorized_chat_ids`, `auto_responder_enabled`, and `auto_responder_message` to the status payload.
- **Settings rendering polish** — Secrets always render masked (`●●●●●● (set)`), MQTT/Telegram rows show values only when enabled (else `—`), proxy rows appear only in proxy mode, and the legacy `telegram_chat_id` is shown beside the authorized-chat-IDs list.

## [1.9.3] - 2026-08-07
### Added
- **Telegram channel selection** — `/send` accepts an optional channel index (`/send 1 <msg>` or `/send #1 <msg>`); bare numeric messages still default to Ch 0.
- **`/channels` command** — Lists the radio's configured channels with indices, marking which are relayed to Telegram.

## [1.9.2] - 2026-08-07
### Fixed
- **Telegram reply routing** — Replies to forwarded mesh messages are now routed by the forwarded message's Telegram `message_id` (tracked at relay time), so they reliably return to the originating channel/DM node instead of the default channel. Text parsing kept as fallback for older messages.

## [1.9.1] - 2026-08-07
### Fixed
- **Telegram relay channels config** — `telegram_forward_channels` is now a string field in the addon config UI (e.g. `0, 1, 2`) to work around the HA frontend list serialisation bug that produced "Invalid list for option 'telegram_forward_channels'" on save. Legacy list values are still accepted by `app/config.py`.

## [1.8.0] - 2026-08-06
### Added
- **Telegram Bot Integration** — Bidirectional Telegram bridge (`app/telegram_bot.py`) using the existing `aiohttp` stack. Inbound mesh text messages are relayed to an authorized Telegram chat; commands from Telegram (`/status`, `/nodes`, `/send`, `/dm`, `/help`) are executed against the live radio.
- **`_telegram_forward_callback` hook** — Registered on `MeshtasticConnection` in `main.py` after both objects are created, keeping `connection.py` decoupled from the Telegram module.
- **Telegram Settings UI** — New panel in the Web UI Settings tab showing enabled state, token presence, chat ID, relay channels, DM relay, and command permission.
- **Config schema** — Six new options: `telegram_enabled`, `telegram_bot_token`, `telegram_chat_id`, `telegram_forward_channels`, `telegram_forward_dms`, `telegram_allow_commands`.

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
- **Waypoints (Mesh + Local)** — Inbound `WAYPOINT_APP` protobuf capture (`_capture_waypoint`), normalised to canonical `from_id-wp_int_id` keys with upsert logic so re-broadcasts update in-place. Persisted to `waypoints.json` on daemon threads. Locally-created waypoints get a UUID-based ID. Expired waypoints filtered at read time.
- **Waypoint REST API** — `GET /api/waypoints`, `POST /api/waypoints`, `PATCH /api/waypoints/{waypoint_id}` (field updates), `DELETE /api/waypoints/{waypoint_id}`.
- **Waypoint Web UI** — Floating "📍 Waypoint" panel with name/description/icon/coords inputs. Map click auto-fills lat/lng. lat/lng optional — falls back to map centre. Markers are **draggable**; `dragend` fires `PATCH` to persist position.
- **Ruler measurement tool** — `MapManager.enableRuler()` / `disableRuler()` toggles click-to-measure mode. Each click adds a point (amber circle). Lines drawn as dashed amber polylines with midpoint distance labels.
- **Ruler elevation profile** — `_sampleElevationPath()` builds known-altitude points from position history, samples the path at fine granularity (up to 500 steps, one per 5m) using path interpolation, and estimates altitude via IDW from the 4 nearest known fixes. Ruler panel shows total distance, elevation gain, elevation loss, and a canvas-drawn profile chart.
- **Ruler toolbar collapse** — Activating ruler adds `ruler-active` class to `#view-map`; CSS hides all filter bar children except the ruler button, shrinking it to a compact floating pill.
- **Ruler position history integration** — Fetch position history every poll while ruler is active to keep elevation data current.
- **`setPosHistory()` on MapManager** — Updates the elevation reference data from the latest API fetch.
- **`updateWaypoint()` API function** — New `PATCH` client function in `api.js` for updating waypoint fields.

### Changed
- **Version bump** — 1.3.0 → 1.4.0.
- **Waypoint create relaxed** — `handle_add_waypoint` no longer requires `lat`/`lng`; accepts `null` and passes through.
- **`_add_waypoint_sync`** — Stores `lat`/`lng` as `None` when omitted, allowing front-end to set them on drag.

### Fixed
- **Missing `handle_set_tags` import** — Added to `main.py` import list to fix `NameError` on startup.
- **Missing `handle_delete_node` import** — Added `handle_delete_node` to route imports.

### Added
- **Delete single node** — `DELETE /api/node/{node_id}` endpoint, `_delete_node_sync()` in connection.py, and a red "Delete" button on each node card with confirmation prompt.

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

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-28
### Added
- **Standalone Docker support** — New `Dockerfile.standalone` based on `python:3.12-alpine3.19` for running the Web UI without Home Assistant. Full guide in `STANDALONE_DOCKER.md`.
- **Signal strength filter** on the Nodes tab now uses `snr_avg` (rolling average of last 10 packet SNRs) instead of the instantaneous `snr` value for more stable filtering.

### Fixed
- **`notifyNode` dead code** — Removed the async `try/catch/await` wrapper and rollback logic that could never trigger since `notifyNode` is entirely client-side (localStorage + toast). Now directly updates state and calls `_updateNotifyUI`.
- **`_originalColor` shallow clone** — `{...node.color}` only copied the top-level keys, so vis.js mutations to nested `highlight`/`hover` objects corrupted the stored original. Changed to a deep clone with spread for both sub-objects.
- **`_applySearchHighlight` color comparison** — The `JSON.stringify` comparison didn't include `opacity`, so an opacity-only change (e.g. search dimming) wouldn't trigger a vis.js redraw. Now explicitly checks `opacity` alongside `background` and `border`.
- **`_buildNodeTooltip` indentation** — The method and its `escapeTextContent` helper were at column 0 inside the class, which would cause syntax errors in strict mode or under minifiers. Properly indented.
- **`app.js init()` indentation** — The `localStorage` loading blocks for `state.notifyNodes` and `state.dismissedConvs` had mismatched indentation relative to surrounding code.
- **`storeMessage` echo dedup false positive** — Sending identical message text twice within 3 seconds would incorrectly suppress the second one. Now also matches on `destination` and `channel`, making the dedup specific enough to avoid false positives while still suppressing firmware echoes.

### Changed
- **Version bump** — 1.1.0 → 1.2.0.

## [1.1.0] - 2026-07-24
### Added
- **Packet Inspector column sort/filter** — Clickable column headers for sorting (asc/desc) and filtering by unique values via dropdown. Active filters and sort direction indicated on headers.
- **Short names in packet table** — From and To columns show node short name next to hex ID.
- **Complete README rewrite** — Restored architecture diagrams, full installation guide, addon configuration docs, troubleshooting, and development sections.

### Fixed
- **Packet table not populating** — Fixed ID mismatch between HTML (`packet-table-body`) and JS (`packet-tbody`).
- **Sniffer stats toggle broken on wide screens** — Moved `.hidden` utility class out of `@media` query into global scope.
- **Portnum filter input not working** — Fixed ID mismatch (`pkt-filter-port` vs `pkt-filter-portnum`).
- **Sniffer distribution bars not rendering** — Fixed ID mismatch (`sniffer-dist-bars` vs `sniffer-bars`).

### Changed
- **Version bump** — 1.0.0 → 1.1.0.

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

## [0.2.37] - 2026-07-23
### Added
- **Dynamic topology toolbar** — The Network Topology view now includes interactive toggles for node names, traceroute edges, neighbor edges, and physics simulation, plus a node search box with real-time highlight filtering and a "Reset" layout button.

## [0.2.36] - 2026-07-23
### Added
- **Heatmap loading indicator** — When the coverage heatmap is enabled, a toast now appears showing "Loading heatmap data…" while the initial position history fetch completes, so users know the data is coming.
- **`willReadFrequently` canvas hint** — Patched leaflet-heat's `simpleheat` to request `willReadFrequently: true` on the canvas context, silencing the Chrome DevTools warning and improving redraw performance for the heatmap layer.
- **Zero-size canvas crash guard** — Added defensive monkeypatch and size checks so `getImageData` on a zero-dimension canvas no longer throws `IndexSizeError` when the map container hasn't laid out yet.

### Changed
- **Heatmap refresh cadence** — When the heatmap is visible, position history is now fetched **every poll** (15s) instead of every 8 polls (120s), so the signal-strength overlay stays fresh. When hidden, it reverts to the 8-poll cadence to save bandwidth.
- **Heatmap toggle UX** — Enabling the heatmap (🌡 button or **M** key) immediately triggers a fresh position-history fetch so it appears within seconds instead of waiting up to 120s for the next scheduled refresh.
- **Redundant redraw elimination** — `updateTrails` now compares the new heatmap points against the last-set points via serialization; if unchanged, the expensive `setLatLngs()` (which triggers a full canvas `getImageData` redraw) is skipped entirely.

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
- **Map node filter** — Filter map markers by name/ID text, max hops away, last-heard time window, or cached-only (stale). Live "N shown" counter.

## [0.2.29] - 2026-07-19
### Added
- **Persistent node store** — Every node seen is saved to `nodes.json`. Nodes the radio drops (its node DB is bounded, ~250 entries) are re-injected as `stale` so they remain visible with their last-known position. Survives restarts; debounced/off-thread writes.

## [0.2.28] - 2026-07-19
### Added
- **Last-known-position retention** — Nodes that lose GPS or stop reporting (`position=None`) keep their previous good latitude/longitude/altitude so the map marker doesn't disappear. A `last_position_fix` timestamp is recorded whenever a valid fix is seen (periodic packet or "Req. Position" reply).

## [0.2.27] - 2026-07-19
### Fixed
- Channel list now sourced from `interface.localNode.channels` instead of `interface.localConfig.channel_settings` (which is often empty). The Web UI's channel tabs now show every active channel (Primary + secondaries) on load instead of only the Primary tab until a message arrives on another channel.

## [0.2.26] - 2026-07-19
### Changed
- Bumped in lockstep with the integration (0.2.26): the "Track in HA" relay now returns immediately and lets HA refresh in the background, and the addon's traceroute dispatch uses `asyncio.get_running_loop()` instead of the deprecated `get_event_loop()`.
- Shared Web UI helpers (`escapeHtml` / `haversineKm` / `formatDistance`) moved into `web_ui/js/util.js` to remove duplication between `app.js` and `map.js`.

## [0.2.25] - 2026-07-19
### Added
- Live channel refresh: re-reads the node's channel config immediately after each (re)connection and on a 5-minute background loop, keeping the Web UI channel list/tabs in sync with the radio.

## [0.2.24] - 2026-07-17
### Changed
- Bumped in lockstep with the integration (0.2.24); includes the short-name-in-chat fix and the immediate channel-tab seeding on the message dashboard.

## [0.2.23] - 2026-07-17
### Changed
- Mobile-friendly Web UI: slide-in navigation drawer, stacked responsive dashboard, and dynamic viewport height so the panel renders correctly inside the Home Assistant mobile app's ingress view.

## [0.2.22] - 2026-07-17
### Changed
- Bumped in lockstep with the integration (0.2.22) for the addon-reachability fix on the integration side.

## [0.2.21] - 2026-07-17
### Changed
- **Traceroute dispatch is now fire-and-forget** — `POST /api/traceRoute` returns immediately instead of blocking on the firmware RouteDiscovery ack, avoiding the addon ingress HTTP 503 timeout. Results appear on the next node poll.
- **RSSI labeled "Not provided"** — The firmware does not expose a persistent per-node RSSI, so the node card and map popup now say "Not provided" instead of a misleading `N/A`.
- **Landscape settings layout** — The settings screen now uses a responsive grid so groups sit side-by-side on wide screens.

## [0.2.20] - 2026-07-17
### Added
- **Outgoing messages recorded in the feed** — Sent text messages are now captured into the message buffer (with `outgoing: True`) at send time, so the Web UI message feed and the integration's "Last Message Sent" entity populate immediately and reliably, instead of relying on the firmware echo.

## [0.2.16] - 2026-07-17
### Fixed
- **Message sensors showed nothing for tracked nodes** — The integration's "Last Message Received/Sent" sensors failed to match messages because of node-ID formatting differences (leading `!` / letter case) between the tracked node ID and the message `from_id`/`to_id`. Matching is now normalised so it always aligns.
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
- Companion integration now exposes Voltage, Channel Utilization, Air Utilization TX, Uptime, Role, and Gas Resistance sensors per tracked node, plus a per-node "Online" binary sensor. The addon now forwards `role`, `uptime`, and `gas_resistance` in the node payload.

### Fixed
- **Always-live connection** — replaced the passive socket health check with an active node-DB probe run every 60s. A dropped-but-apparently-open TCP session is now detected and reconnected automatically instead of looking healthy while no data flows.
- **Reconnect loop on slow nodes** — a freshly established session is now given a 30s grace period before an empty node DB is treated as a dead connection, preventing reconnect loops on nodes that sync their node DB asynchronously after connect.
- **Role normalization** — the device `role` (e.g. CLIENT, ROUTER) is now normalized to a clean name instead of a raw enum string/int.

## [0.2.12] - 2026-07-16
### Added
- GPS coordinate sensors (Latitude, Longitude, Altitude) and separate sent/received message sensors are now exposed by the companion integration (see integration changelog).

## [0.2.11] - 2026-07-16
### Added
- Message sensor entities in the companion integration for showing the last received text message per tracked node.

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
