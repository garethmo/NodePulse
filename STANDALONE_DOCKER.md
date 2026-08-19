# NodePulse — Standalone Docker Install

Run the full NodePulse Web UI dashboard without Home Assistant. All features work except the HA integration relays (node tracking, sensor entities, etc.).

---

## Quick Start

### 1. Clone and build

```bash
git clone https://github.com/garethmo/NodePulse.git
cd NodePulse/nodepulse-addon

docker build -t nodepulse:latest -f Dockerfile.standalone .
```

### 2. Create a config file

Create `~/.nodepulse/config.json`:

```json
{
  "log_level": "info",
  "connection_type": "direct",
  "meshtastic_host": "192.168.1.100",
  "meshtastic_port": 4403,
  "scan_interval": 30,
  "ignored_nodes": [],
  "ha_base_url": ""
}
```

Edit `meshtastic_host` to your node's IP.

### 3. Run

```bash
docker run -d \
  --name nodepulse \
  --restart unless-stopped \
  -p 8099:8099 \
  -v ~/.nodepulse/config.json:/app/dev_options.json:ro \
  -v nodepulse_data:/data \
  nodepulse:latest
```

### 4. Open the dashboard

**http://localhost:8099**

---

## docker-compose

`docker-compose.yml`:

```yaml
version: "3.8"

services:
  nodepulse:
    build:
      context: ./nodepulse-addon
      dockerfile: Dockerfile.standalone
    container_name: nodepulse
    restart: unless-stopped
    ports:
      - "8099:8099"
    volumes:
      - ~/.nodepulse/config.json:/app/dev_options.json:ro
      - nodepulse_data:/data

volumes:
  nodepulse_data:
```

```bash
docker compose up -d
docker compose logs -f
```

---

## Step-by-step

### 1. Clone the repository

```bash
git clone https://github.com/garethmo/NodePulse.git
cd NodePulse
```

### 2. Build the image

Uses the lightweight `python:3.12-alpine` base (no Home Assistant dependencies):

```bash
docker build -t nodepulse:latest -f nodepulse-addon/Dockerfile.standalone nodepulse-addon/
```

### 3. Configure

```bash
mkdir -p ~/.nodepulse
```

Write your config to `~/.nodepulse/config.json`. At minimum set `meshtastic_host` to your node's IP.

### 4. Start

```bash
docker run -d \
  --name nodepulse \
  --restart unless-stopped \
  -p 8099:8099 \
  -v ~/.nodepulse/config.json:/app/dev_options.json:ro \
  -v nodepulse_data:/data \
  nodepulse:latest
```

Flags explained:

| Flag | Purpose |
|---|---|
| `-p 8099:8099` | Expose the Web UI on host port 8099 |
| `-v config.json:/app/dev_options.json:ro` | Mount config (addon falls back to `dev_options.json` when `/data/options.json` doesn't exist) |
| `-v nodepulse_data:/data` | Persist node DB, messages, tags, position history across restarts |
| `--restart unless-stopped` | Auto-restart on boot or crash |

### 5. Verify

```bash
docker logs nodepulse --tail 20
```

You should see log output ending with a successful connection. If the node connection fails, check the IP/port and ensure no other TCP client is connected (Meshtastic firmware allows only one).

### 6. Open the dashboard

Visit **http://localhost:8099** in your browser.

---

## Updating

```bash
cd NodePulse
git pull
docker build -t nodepulse:latest -f nodepulse-addon/Dockerfile.standalone nodepulse-addon/
docker stop nodepulse
docker rm nodepulse
# Re-run the docker run command from step 4
```

Or with compose:

```bash
git pull
docker compose build --pull
docker compose up -d
```

---

## Data persistence

The container stores persistent data (node DB, messages, tags, position history) at `/data`. The `nodepulse_data` volume keeps this across restarts and upgrades. Delete the volume to wipe everything and start fresh from the radio's current node DB.

---

## Troubleshooting

| Problem | Check |
|---|---|
| Container exits immediately | `docker logs nodepulse` — config JSON invalid or missing |
| "Cannot connect" in logs | Verify `meshtastic_host` is reachable and nothing else is connected on TCP/4403 |
| Web UI blank | Visit `http://localhost:8099/` directly. Open browser devtools for errors |
| "Connection refused" | The node's Meshtastic TCP interface must be enabled (serial → TCP, default port 4403) |
| Messages not showing | A channel `access_key` may be needed if the primary channel is encrypted |

---

## What doesn't work without HA

- **Track in HA** toggles (sensor entities)
- **HA notify platform** (`notify.nodepulse` / `notify.nodepulse_<name>`)
- **HA device tracker** entities on the HA map
- **Logbook entries** in HA

Everything else — mesh dashboard, node grid, map, topology graph, messaging, packet inspector, traceroute, position history, charts — works fully.
