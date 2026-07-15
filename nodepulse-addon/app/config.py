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


@dataclass
class Config:
    """Immutable snapshot of the addon configuration options."""

    meshtastic_host: str
    meshtastic_port: int
    access_key: Optional[str]
    scan_interval: int
    ignored_nodes: List[str] = field(default_factory=list)


def load_config() -> Config:
    """
    Load and validate the addon configuration from disk.

    Prefers /data/options.json (HA Supervisor) and falls back to
    dev_options.json for local development. Raises on missing required fields
    so problems surface immediately at startup rather than at first API call.
    """
    options_path = _OPTIONS_FILE if os.path.exists(_OPTIONS_FILE) else _DEV_OPTIONS_FILE

    logger.info({"path": options_path}, "Loading addon configuration")

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

    return Config(
        meshtastic_host=raw["meshtastic_host"],
        meshtastic_port=int(raw.get("meshtastic_port", 4403)),
        access_key=raw.get("access_key") or None,
        scan_interval=int(raw.get("scan_interval", 30)),
        ignored_nodes=[n for n in raw.get("ignored_nodes", []) if n],
    )
