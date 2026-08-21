"""
NodePulse Addon — Application Configuration Loader.

This module reads the Home Assistant addon options from the standard
/data/options.json file that HA Supervisor injects into every addon container.
Centralizing config access here keeps all other modules free of file I/O
and makes testing easier (just mock this module).
"""
import dataclasses
import json
import logging
import os
import re
from dataclasses import dataclass, field

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


def parse_int_list(value, default) -> list[int]:
    """
    Parse a list-of-int config option into a clean list of ints.

    The HA addon config UI has a known frontend bug where list-typed options
    are serialised as a scalar string instead of a JSON list (see
    home-assistant/addons#4559), which makes Supervisor reject the save with
    "Invalid list for option". To work around it the schema for
    telegram_forward_channels uses a plain string, but we still accept the
    legacy list form so existing installs are unaffected. Accepts:
      - "0, 1, 2" / "0 1 2"  (comma or whitespace separated)
      - [0, 1, 2] / [0, "1", None]  (legacy list form, optional nulls skipped)
      - "" / None  (falls back to default)
    """
    if value is None:
        return list(default)
    if isinstance(value, str):
        parts = [p for p in re.split(r"[,\s]+", value.strip()) if p]
        if not parts:
            return list(default)
        try:
            return [int(p) for p in parts]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid telegram_forward_channels value {value!r}: "
                "expected channel indices like '0, 1, 2'."
            ) from exc
    if isinstance(value, (list, tuple)):
        channels = []
        for item in value:
            if item is None or (isinstance(item, str) and not item.strip()):
                continue
            try:
                channels.append(int(item))
            except (TypeError, ValueError) as err:
                raise RuntimeError(
                    f"Invalid telegram_forward_channels entry {item!r}: "
                    "expected channel indices like 0, 1, 2."
                ) from err
        return channels or list(default)
    raise RuntimeError(
        f"Invalid telegram_forward_channels value {value!r}: expected a list "
        "of channel indices or a comma/space separated string like '0, 1, 2'."
    )


@dataclass
class Config:
    """Immutable snapshot of the addon configuration options."""

    log_level: str
    connection_type: str
    meshtastic_host: str
    meshtastic_port: int
    proxy_host: str | None
    proxy_port: int
    access_key: str | None
    scan_interval: int
    ignored_nodes: list[str] = field(default_factory=list)
    # Base URL of the Home Assistant core instance that hosts the NodePulse
    # custom integration. The integration's relay endpoints (/api/nodepulse/*)
    # are served by HA core, NOT by this addon. From inside the addon's Docker
    # container, "localhost" is the addon itself — HA core is reachable on the
    # supervisor network at "homeassistant:8123" (the standard addon->HA host).
    ha_base_url: str = "http://homeassistant:8123"
    # Optional Home Assistant long-lived access token used to authenticate the
    # addon->HA relay (/api/nodepulse/*) on non-HAOS installs where the
    # SUPERVISOR_TOKEN environment variable is not injected into the addon
    # container. On HAOS the Supervisor provides SUPERVISOR_TOKEN automatically
    # and it takes precedence; this is only a fallback for custom Docker/venv
    # setups. Create one in HA at Profile -> Security -> Long-lived access tokens.
    ha_access_token: str = ""
    # DEPRECATED (2026-08-18): token validation is now always on. This option
    # is retained only for backward compatibility with existing configs and is
    # ignored by the relay logic. Default is False.
    disable_token_validation: bool = False
    
    # MQTT Bridge Settings
    mqtt_enabled: bool = False
    mqtt_address: str = "mqtt.meshtastic.org"
    mqtt_port: int = 1883
    mqtt_username: str = "meshdev"
    mqtt_password: str = "large4cats"
    mqtt_topic: str = "msh/+"
    mqtt_geo_filter_enabled: bool = False
    mqtt_lat_min: float = 0.0
    mqtt_lat_max: float = 0.0
    mqtt_lng_min: float = 0.0
    mqtt_lng_max: float = 0.0
    mqtt_portnum_allowlist: list[str] = field(default_factory=list)
    mqtt_node_blocklist: list[str] = field(default_factory=list)
    mqtt_forwarding_enabled: bool = False

    # Telegram Integration Settings
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # Deprecated: use telegram_authorized_chat_ids
    telegram_authorized_chat_ids: list[str] = field(default_factory=list)  # List of authorized chat IDs (private and groups)
    telegram_forward_channels: list[int] = field(default_factory=lambda: [0])
    telegram_forward_dms: bool = True
    telegram_allow_commands: bool = True

    # Auto Responder Settings
    auto_responder_enabled: bool = False
    auto_responder_message: str = "Welcome to the mesh! You have been discovered by NodePulse."
    auto_traceroute_enabled: bool = False

    # Scheduled Messages
    scheduled_messages_enabled: bool = True

    # Terrain Link Analysis
    terrain_dem_url: str = "https://api.opentopodata.org/v1/srtm30m"


def load_config() -> Config:
    """
    Load and validate the addon configuration from disk.

    Prefers /data/options.json (HA Supervisor) and falls back to
    dev_options.json for local development. Raises on missing required fields
    so problems surface immediately at startup rather than at first API call.
    """
    options_path = _OPTIONS_FILE if os.path.exists(_OPTIONS_FILE) else _DEV_OPTIONS_FILE

    logger.debug("Loading addon configuration (path=%s)", options_path)

    try:
        with open(options_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError(
            f"No options file found at {_OPTIONS_FILE} or {_DEV_OPTIONS_FILE}. "
            "Create dev_options.json for local development."
        ) from None
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Options file is not valid JSON: {exc}") from exc

    connection_type = (raw.get("connection_type") or CONNECTION_TYPE_DIRECT).lower()
    if connection_type not in _CONNECTION_TYPES:
        raise RuntimeError(
            f"Invalid connection_type {connection_type!r}. "
            f"Must be one of {_CONNECTION_TYPES}."
        )

    config = Config(
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
        ha_access_token=raw.get("ha_access_token") or "",
        disable_token_validation=bool(raw.get("disable_token_validation", False)),
        mqtt_enabled=bool(raw.get("mqtt_enabled", False)),
        mqtt_address=raw.get("mqtt_address", "mqtt.meshtastic.org"),
        mqtt_port=int(raw.get("mqtt_port", 1883)),
        mqtt_username=raw.get("mqtt_username", ""),
        mqtt_password=raw.get("mqtt_password", ""),
        mqtt_topic=raw.get("mqtt_topic", "msh/+"),
        mqtt_geo_filter_enabled=bool(raw.get("mqtt_geo_filter_enabled", False)),
        mqtt_lat_min=float(raw.get("mqtt_lat_min", 0.0)),
        mqtt_lat_max=float(raw.get("mqtt_lat_max", 0.0)),
        mqtt_lng_min=float(raw.get("mqtt_lng_min", 0.0)),
        mqtt_lng_max=float(raw.get("mqtt_lng_max", 0.0)),
        mqtt_portnum_allowlist=[s for s in raw.get("mqtt_portnum_allowlist", []) if s],
        mqtt_node_blocklist=[s for s in raw.get("mqtt_node_blocklist", []) if s],
        mqtt_forwarding_enabled=bool(raw.get("mqtt_forwarding_enabled", False)),
        telegram_enabled=bool(raw.get("telegram_enabled", False)),
        telegram_bot_token=raw.get("telegram_bot_token", ""),
        telegram_chat_id=str(raw.get("telegram_chat_id", "")),
        # Support both old single chat_id and new list of authorized chat IDs
        telegram_authorized_chat_ids=[str(x) for x in raw.get("telegram_authorized_chat_ids", []) if x],
        telegram_forward_channels=parse_int_list(raw.get("telegram_forward_channels", [0]), [0]),
        telegram_forward_dms=bool(raw.get("telegram_forward_dms", True)),
        telegram_allow_commands=bool(raw.get("telegram_allow_commands", True)),
        auto_responder_enabled=bool(raw.get("auto_responder_enabled", False)),
        auto_responder_message=str(raw.get("auto_responder_message", "Welcome to the mesh! You have been discovered by NodePulse.")),
        auto_traceroute_enabled=bool(raw.get("auto_traceroute_enabled", False)),
        scheduled_messages_enabled=bool(raw.get("scheduled_messages_enabled", True)),
        terrain_dem_url=str(raw.get("terrain_dem_url") or "https://api.opentopodata.org/v1/srtm30m"),
    )

    # Validate geo-filter bounds at load time so misconfigurations surface
    # immediately with a clear warning rather than silently dropping all packets.
    if config.mqtt_geo_filter_enabled:
        bounds_invalid = (
            config.mqtt_lat_min >= config.mqtt_lat_max
            or config.mqtt_lng_min >= config.mqtt_lng_max
        )
        if bounds_invalid:
            logger.warning(
                "mqtt_geo_filter_enabled is True but bounding box is invalid "
                "(lat %s..%s, lng %s..%s) — geo filter will be DISABLED. "
                "Set distinct lat_min < lat_max and lng_min < lng_max.",
                config.mqtt_lat_min, config.mqtt_lat_max,
                config.mqtt_lng_min, config.mqtt_lng_max,
            )
            config = dataclasses.replace(config, mqtt_geo_filter_enabled=False)

    return config


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
