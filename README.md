# NodePulse

**Real-time Meshtastic mesh network monitoring for Home Assistant.**

NodePulse is a Home Assistant addon and custom integration that gives you deep visibility into your Meshtastic mesh network — node health, signal metrics, GPS positions on the HA map, and encrypted direct messaging — all from inside Home Assistant.

> ⚠️ **Clean Room Implementation**: NodePulse is built entirely from scratch. It does not use any code from MeshSense or any other prior project. MeshSense was used only as a conceptual feature reference.

---

## Features

| Feature | Description |
|---|---|
| 🟢 **Connection Status** | Binary sensor — know immediately if your mesh link drops |
| 📡 **Node Count** | Live count of all visible mesh nodes |
| 📶 **Per-Node Metrics** | SNR, RSSI, hops away, battery level, last heard — one HA device per node |
| 🗺️ **GPS Mapping** | Device trackers plotted on the native HA map card |
| 💬 **Messaging** | Send broadcast or DM messages via the Web UI |
| 🔍 **Traceroute** | Dispatch traceroutes to any node from the Web UI |
| 🖥️ **Web UI Dashboard** | Full-featured dashboard served via HA Ingress (no port forwarding) |

---

## Architecture

### System Overview

```mermaid
block-beta
  columns 3

  Mesh["🌐 Meshtastic\nMesh Network"] space:1 HA["🏠 Home Assistant OS"]

  space:3

  Node["📡 Meshtastic\nNode (TCP :4403)"] space:1 block:addon:1
    addonLabel["NodePulse Addon\n(Docker Container)"]
    backend["app/main.py\naiohttp :8099"]
    conn["connection.py\nTCP client + reconnect"]
    routes["routes.py\nREST API"]
    ui["web_ui/\nDashboard"]
  end

  space:3

  space:1 space:1 block:integration:1
    intLabel["Custom Integration\ncustom_components/nodepulse"]
    coord["coordinator.py\nDataUpdateCoordinator"]
    bs["binary_sensor.py"]
    sens["sensor.py"]
    dt["device_tracker.py"]
  end

  Node -->|"TCP stream"| conn
  conn --> routes
  routes --> ui
  routes -->|"REST /api/*"| coord
  coord --> bs
  coord --> sens
  coord --> dt

  style addon fill:#0f1629,stroke:#00d4aa,color:#e8eaf0
  style integration fill:#0f1629,stroke:#4fc3f7,color:#e8eaf0
```

### Poll Cycle — Data Flow

```mermaid
sequenceDiagram
  autonumber
  participant HA  as Home Assistant Core
  participant C   as DataUpdateCoordinator
  participant API as NodePulse Addon API
  participant M   as Meshtastic Node

  HA->>C: async_config_entry_first_refresh()
  activate C
  C->>API: GET /api/status
  C->>API: GET /api/nodes
  Note over C,API: Both requests run in parallel (asyncio.gather)
  API->>M: reads cached node DB
  M-->>API: node list + metrics
  API-->>C: JSON response
  C-->>HA: coordinator.data updated
  deactivate C

  loop Every scan_interval seconds
    HA->>C: scheduled refresh
    C->>API: GET /api/status + GET /api/nodes
    API-->>C: fresh snapshot
    C-->>HA: push state to all entities
    HA->>HA: async_write_ha_state() on each entity
  end
```

### HA Entity Model

```mermaid
erDiagram
  CONFIG_ENTRY ||--o{ NODE_DEVICE : "creates one per node"
  CONFIG_ENTRY ||--|| NODEPULSE_DEVICE : "owns"

  NODEPULSE_DEVICE {
    string identifier  "entry_id"
    string name        "NodePulse"
  }

  NODEPULSE_DEVICE ||--|| CONNECTION_BINARY_SENSOR : has
  NODEPULSE_DEVICE ||--|| NODE_COUNT_SENSOR : has

  CONNECTION_BINARY_SENSOR {
    string device_class  "connectivity"
    bool   is_on         "addon connected?"
  }

  NODE_COUNT_SENSOR {
    string state_class  "measurement"
    int    value        "visible node count"
  }

  NODE_DEVICE {
    string identifier  "node hex ID"
    string name        "Mesh Node !abcd1234"
  }

  NODE_DEVICE ||--|| SNR_SENSOR : has
  NODE_DEVICE ||--|| RSSI_SENSOR : has
  NODE_DEVICE ||--|| HOPS_SENSOR : has
  NODE_DEVICE ||--|| LAST_HEARD_SENSOR : has
  NODE_DEVICE ||--|| BATTERY_SENSOR : has
  NODE_DEVICE ||--o| GPS_TRACKER : "has (if GPS fix)"

  SNR_SENSOR        { string unit "dB" }
  RSSI_SENSOR       { string unit "dBm" }
  HOPS_SENSOR       { string unit "hops" }
  LAST_HEARD_SENSOR { string device_class "timestamp" }
  BATTERY_SENSOR    { string unit "%" }
  GPS_TRACKER       { string source_type "gps" }
```

---

## Installation

### 1. Install the Addon (Local Installation)

To run this as a local addon in your Home Assistant instance:

1. Connect to your Home Assistant's `addons` folder (via Samba, SSH, or the VSCode addon).
2. Copy the entire `nodepulse-addon/` directory from this project into your `addons/` folder.
3. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
4. Click the three vertical dots (⋮) in the top right and click **Check for updates**.
5. Scroll to the "Local add-ons" section at the top of the store, and click **NodePulse**.
6. Click **Install**.
7. Configure the addon options and start it.

### 2. Install the Custom Integration

1. Connect to your Home Assistant's `config` folder.
2. Copy the `custom_components/nodepulse/` folder into your HA `config/custom_components/` directory.
3. Restart Home Assistant.
4. Go to **Settings → Integrations → Add Integration** and search for **NodePulse**.
5. Enter the addon URL (default: `http://localhost:8099`) and follow the setup wizard.

---

## Addon Configuration

NodePulse reaches your Meshtastic node over TCP. Because Meshtastic
**firmware allows only ONE TCP client per node**, you must choose how
NodePulse shares (or owns) that single connection.

### Connection modes

| Mode | `connection_type` | Connects to | Use when |
|---|---|---|---|
| **Direct** (default) | `direct` | the Meshtastic node itself | NodePulse is the only TCP client on the node |
| **Proxy** | `proxy` | the official Meshtastic HA integration's TCP proxy | you also run the official Meshtastic integration and want both to share the node |

> ⚠️ **One-TCP-client limit:** the Meshtastic node firmware permits a
> single TCP connection. The official Meshtastic integration (TCP) and
> NodePulse (TCP) **cannot both connect directly to the same node**.
> Either run NodePulse in `direct` mode with the official integration
> disabled, or run NodePulse in `proxy` mode (below).

### Proxy mode (coexist with the official integration)

The official Meshtastic integration can expose a **TCP Proxy** that owns the
node's single connection and relays framed packets to multiple clients. To use it:

1. In the official Meshtastic integration, enable the **TCP Proxy** option
   (default port `4403`).
2. Set NodePulse's options:
   - `connection_type`: `proxy`
   - `proxy_host`: **`homeassistant`** — the Docker DNS name of the
     Home Assistant Core container. Do **not** use the node's LAN IP; the
     proxy runs *inside* Core, not on the node, so it is not reachable
     via the node's address.
   - `proxy_port`: `4403` (must match the integration's proxy port).

> 📝 Proxy mode depends on the official integration's proxy implementation.
> If the proxy is flaky, fall back to `direct` mode with the official
> integration disabled.

### Options reference

| Option | Type | Default | Description |
|---|---|---|---|
| `connection_type` | `direct` \| `proxy` | `direct` | How NodePulse reaches the node (see above) |
| `meshtastic_host` | string | — | **Direct mode:** IP/hostname of your Meshtastic node |
| `meshtastic_port` | int | `4403` | **Direct mode:** TCP port of the node's Meshtastic interface |
| `proxy_host` | string | _(empty)_ | **Proxy mode:** host running the official integration (`homeassistant`) |
| `proxy_port` | int | `4403` | **Proxy mode:** TCP proxy port (must match the integration) |
| `access_key` | string | _(empty)_ | Optional access key if your node requires authentication |
| `scan_interval` | int | `30` | How often (seconds) the integration polls the addon (10–300) |
| `ignored_nodes` | list | `[]` | List of node hex IDs to exclude from all API responses |

> 💡 After changing `connection_type` (or any add-on option), **uninstall and
> re-install** the add-on so Home Assistant re-reads `config.json` and
> surfaces the new fields — a plain restart does not refresh the schema.

---

## Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| Addon backend | Python 3.12 + `aiohttp` | Pure Python, async, no native compilation — fully HAOS compatible |
| Meshtastic client | `meshtastic` PyPI library | Official library, pure Python |
| Web UI charts | Chart.js (CDN) | No build toolchain inside Docker |
| Web UI mapping | Leaflet.js (CDN) | Dark-theme tile layer, no API key |
| HA Integration | Python 3.12 + HA Core APIs | Standard custom component stack |

---

## Development

### Running the addon locally (without HA)

```bash
cd nodepulse-addon/
# Edit dev_options.json with your node's IP address
pip install -r requirements.txt
python -m app.main
# Open http://localhost:8099/ui/index.html
```

---

## Contributing

- All code comments, docstrings, commit messages, and documentation **must be in English**.
- Follow the SOLID and DRY principles described in the project rules.
- Run existing code through a linter before submitting a PR.

---

## License

MIT © NodePulse Contributors
