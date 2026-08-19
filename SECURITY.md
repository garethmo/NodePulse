# NodePulse — Security & Threat Model

This document describes the security posture of NodePulse (the Home Assistant
addon + custom integration), the trust boundaries between its components, and
the accepted risks. It corresponds to the security section (S1–S19) of
[CODE_REVIEW.md](./CODE_REVIEW.md).

> **TL;DR:** On Home Assistant OS the addon is not reachable from your LAN —
> it is only accessible through HA Ingress (which requires a HA login) and the
> Supervisor network. The only configuration that exposes the unauthenticated
> addon API to the network is the **standalone Docker** install, which
> publishes port `8099`. Keep that off untrusted networks, or put a
> reverse-proxy with authentication in front of it.

---

## 1. Trust boundaries

```
                ┌──────────────────────────────────────────────┐
   LAN / WAN    │  Home Assistant OS                            │
                │                                              │
  Browser ────► │  HA Ingress (auth required) ──► Addon :8099  │
                │                              ▲               │
                │  HA Core ◄── Supervisor network ──┘           │
                │      │                                       │
                │  Integration ◄── supervisor DNS ──► Addon    │
                └──────────────────────────────────────────────┘
```

| Component | Trust level | Notes |
|---|---|---|
| Meshtastic mesh traffic | **Untrusted** | Any node on the mesh can send packets (messages, waypoints, position reports). See §4. |
| HA Ingress traffic | **Authenticated** | Reaches the addon only after a HA session login. |
| Integration ↔ addon API | **Trusted network only** | Supervisor network (HAOS) or an isolated network (custom installs). |
| Standalone Docker :8099 | **Only if you publish it** | No authentication on the addon API. See §2. |

---

## 2. Deployment postures

### Home Assistant OS addon (recommended)

Since **v1.19.1** the addon does **not** publish any host port
(`"ports"` was removed from `config.json`). The addon binds `0.0.0.0:8099`
so HA Ingress can reach it inside the container, but that port is **not**
reachable from the host, the LAN, or the internet. This is intentional and
is exactly why the previously-published port was removed — the addon's own
REST API has no authentication, so publishing it would expose an
unauthenticated dashboard and write endpoints to the LAN (original S1).

Reachable surfaces:

- **HA Ingress** (`https://<ha>/api/hassio_ingress/<token>/`) — protected by
  Home Assistant authentication. This is how browsers reach the dashboard.
- **Supervisor network** — the HA integration and the addon talk over the
  internal supervisor Docker network using the addon's supervisor DNS name
  (e.g. `http://a0d7b954-nodepulse:8099`).

### Standalone Docker (`-p 8099:8099`)

See [STANDALONE_DOCKER.md](./STANDALONE_DOCKER.md). Publishing port 8099 makes
the **entire addon API — including write endpoints such as `/api/send`,
`/api/traceRoute`, `/api/device-config`, `/api/waypoints`, `/api/track-node` —
available without authentication** to anyone who can reach that port.

Recommendations when running standalone:

1. Bind to localhost only: `-p 127.0.0.1:8099:8099`.
2. Or firewall port 8099 to trusted hosts and put an authenticated reverse
   proxy in front of it.
3. Never expose port 8099 directly to the public internet.

---

## 3. Authentication model

### 3.1 Addon REST API and Web UI

The addon applies **no authentication** to its own routes (only a
no-cache middleware). This is acceptable *only* because the supported
deployments keep it off the network (HAOS) or constrain it (standalone).
There is currently **no rate limiting** on write endpoints (S18) — do not
expose the API where that matters.

### 3.2 Track-in-HA relay (addon → HA core)

The addon relays `/api/nodepulse/track` and `/api/nodepulse/tracked-nodes`
to HA core and **fails closed**:

- It validates a `Bearer` token against HA core's `SUPERVISOR_TOKEN`
  (injected automatically on HAOS) using a constant-time comparison.
- If the Supervisor token is missing or rejected, it falls back to the
  addon's `ha_access_token` option (a long-lived HA access token).
- If neither credential is present, the relay refuses to run.
- The legacy `X-NodePulse-Skip-Token` bypass header and the
  `disable_token_validation` option are **removed / deprecated no-ops**;
  token validation is always on (S4).

### 3.3 HA integration ↔ addon

The integration polls `GET /api/status`, `/api/nodes`, `/api/messages`, and
`/api/channels` over HTTP(S) on the supervisor network and can push an
`X-NodePulse-Access-Key` header to the addon.

---

## 4. Data sensitivity

| Data | Where it lives | Sensitivity |
|---|---|---|
| Mesh text messages | Addon `messages.json`, HA **logbook / history / event bus** | **Persisted in HA by default.** Message text is written to the HA logbook (S14). If your mesh carries sensitive content, this is a consideration — a future option will make this redactable/optional. |
| GPS positions | Addon `position_history.json`, HA state/attributes | Location data is stored and plotted. Protect the HA frontend accordingly. |
| `access_key` (mesh channel auth key) | Addon options, HA integration config | Sent in the `X-NodePulse-Access-Key` header in **plaintext over HTTP** (S15). On HAOS this stays on the trusted supervisor network. On custom installs, prefer HTTPS or an isolated network for the integration ↔ addon link, and do **not** expose the addon API to the internet. |
| `ha_access_token`, Telegram bot token, MQTT password | Addon options (`/data/options.json`) | Secrets masked in the Web UI Settings tab; stored in HA Supervisor's options store. Treat as sensitive. |

---

## 5. Accepted risks & known limitations

These are tracked as S14–S19 in CODE_REVIEW.md:

- **S14 — Message text persisted to HA logbook/history.** Mitigation:
  treat mesh traffic as non-sensitive, or plan to enable redaction.
- **S15 — Access key in plaintext over HTTP.** Mitigation: supervisor
  network / isolated network / HTTPS termination in front of the addon.
- **S16 — Bind-all-interfaces (`B104`).** Intentional: required for HA
  Ingress inside the container. Safe on HAOS because no port is published.
- **S17 — Config parse errors log the raw value.** Minor; error messages in
  `parse_int_list` embed the offending input.
- **S18 — No rate limiting on addon write endpoints.** Acceptable only while
  the API is not network-exposed.
- **S19 — MQTT defaults use Meshtastic's public broker**
  (`mqtt.meshtastic.org`, `meshdev` / `large4cats`). These are Meshtastic's
  documented public credentials and are **not secrets**; they are surfaced
  as non-secret via the `mqtt_*_set` booleans in `/api/status`.

**Mesh-controlled content is treated as untrusted** (S3): waypoint
name/description/icon are HTML-escaped in the Web UI and sanitized
server-side at the ingest boundary; no inline event handlers are used for
mesh-controlled values. Telegram inbound messages are filtered against the
configured authorized chat IDs before any mesh action.

---

## 6. Configuration hygiene (developers)

- `nodepulse-addon/dev_options.json` is committed with **empty placeholder
  secrets** (`"access_key": ""`, `"ha_access_token": ""`,
  `"telegram_bot_token": ""`). Keep it that way.
- **Never commit real access keys, HA access tokens, or bot tokens.**
  Real values belong in `/data/options.json` (HAOS) or in an uncommitted
  local copy of `dev_options.json`.
- If you add a new secret option, add it to `config.json`'s `"options"`
  block as an empty string, keep it masked in the Web UI Settings page, and
  leave the `dev_options.json` placeholder empty.

---

## 7. Reporting

Security issues are handled through the public GitHub issue tracker:
<https://github.com/garethmo/NodePulse/issues>. Please include the affected
version and deployment posture (HAOS addon vs standalone Docker).