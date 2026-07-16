# Changelog

All notable changes to NodePulse are documented here.

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
