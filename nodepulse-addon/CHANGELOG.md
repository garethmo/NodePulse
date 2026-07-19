# Changelog

All notable changes to NodePulse are documented here.

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
