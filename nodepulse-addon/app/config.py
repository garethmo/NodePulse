"""
NodePulse Addon — Application Configuration Loader.

This module reads the Home Assistant addon options from the standard
/data/options.json file that HA Supervisor injects into every addon container.
Centralizing config access here keeps all other modules free of file I/O
and makes testing easier (just mock this module).
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# HA Supervisor always writes addon options to this path inside the container.
_OPTIONS_FILE = "/data/options.json"

# Fallback path for local development outside a HA container.
_DEV_OPTIONS_FILE = os.path.join(os.path.dirname(__file__), "..", "dev_options.json")


# Connection modes:
#   "direct" — connect straight to the Meshtastic node over TCP.
#                The node firmware allows ONLY ONE TCP client, so this mode
#                cannot be used while another client (e.g. the official
#                Meshtastic HA integration) is also connected.
#   "proxy"  — connect to the official Meshtastic HA integration's TCP proxy
#                (enabled in that integration's options). The integration owns
#                the single node connection and relays framed packets, allowing
#                multiple clients (HA + NodePulse) to share the node.
CONNECTION_TYPE_DIRECT = "direct"
CONNECTION_TYPE_PROXY = "proxy"
_CONNECTION_TYPES = (CONNECTION_TYPE_DIRECT, CONNECTION_TYPE_PROXY)

# Default TCP port the official Meshtastic HA integration's proxy listens on.
DEFAULT_PROXY_PORT = 4403


@dataclass
class Config:
    """Immutable snapshot of the addon configuration options."""

    log_level: str
    connection_type: str
    meshtastic_host: str
    meshtastic_port: int
    proxy_host: Optional[str]
    proxy_port: int
    access_key: Optional[str]
    scan_interval: int
    ignored_nodes: List[str] = field(default_factory=list)
    # Base URL of the Home Assistant core instance that hosts the NodePulse
    # custom integration. The integration's relay endpoints (/api/nodepulse/*)
    # are served by HA core, NOT by this addon. From inside the addon's Docker
    # container, "localhost" is the addon itself — HA core is reachable on the
    # supervisor network at "homeassistant:8123" (the standard addon->HA host).
    ha_base_url: str = "http://homeassistant:8123"


def load_config() -> Config:
    """
    Load and validate the addon configuration from disk.

    Prefers /data/options.json (HA Supervisor) and falls back to
    dev_options.json for local development. Raises on missing required fields
    so problems surface immediately at startup rather than at first API call.
    """
    options_path = _OPTIONS_FILE if os.path.exists(_OPTIONS_FILE) else _DEV_OPTIONS_FILE

    logger.info("Loading addon configuration (path=%s)", options_path)

    try:
        with open(options_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError(
            f"No options file found at {_OPTIONS_FILE} or {_DEV_OPTIONS_FILE}. "
            "Create dev_options.json for local development."
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Options file is not valid JSON: {exc}") from exc

    connection_type = (raw.get("connection_type") or CONNECTION_TYPE_DIRECT).lower()
    if connection_type not in _CONNECTION_TYPES:
        raise RuntimeError(
            f"Invalid connection_type {connection_type!r}. "
            f"Must be one of {_CONNECTION_TYPES}."
        )

    return Config(
        log_level=(raw.get("log_level") or "info").upper(),
        connection_type=connection_type,
        meshtastic_host=raw["meshtastic_host"],
        meshtastic_port=int(raw.get("meshtastic_port", 4403)),
        proxy_host=raw.get("proxy_host") or None,
        proxy_port=int(raw.get("proxy_port", DEFAULT_PROXY_PORT)),
        access_key=raw.get("access_key") or None,
        scan_interval=int(raw.get("scan_interval", 30)),
        ignored_nodes=[n for n in raw.get("ignored_nodes", []) if n],
        ha_base_url=(raw.get("ha_base_url") or "http://homeassistant:8123").rstrip("/"),
    )


def resolve_target(config: "Config") -> tuple[str, int, str]:
    """
    Resolve the effective (host, port, mode) the addon should connect to.

    In "direct" mode this is the Meshtastic node itself. In "proxy" mode it is
    the official Meshtastic HA integration's TCP proxy (defaults to the same
    host as the node when proxy_host is omitted). The proxy speaks the identical
    Meshtastic frame protocol, so the connection code is identical for both.
    """
    if config.connection_type == CONNECTION_TYPE_PROXY:
        if not config.proxy_host:
            raise RuntimeError(
                "connection_type 'proxy' requires 'proxy_host' to be set to the "
                "IP/host of Home Assistant running the official Meshtastic "
                "integration (whose 'TCP Proxy' option must be enabled). It must "
                "NOT be the Meshtastic node itself — the proxy relays to the node. "
                "Set proxy_host and retry."
            )
        return config.proxy_host, config.proxy_port, CONNECTION_TYPE_PROXY
    return config.meshtastic_host, config.meshtastic_port, CONNECTION_TYPE_DIRECT
