"""
NodePulse — Shared Constants.

Centralises all string keys and default values used across the integration.
Importing from here instead of repeating literals in every file prevents
typo-driven bugs and makes refactoring trivial.
"""
import re

DOMAIN = "nodepulse"

# Config entry keys
CONF_HOST          = "host"
CONF_ACCESS_KEY    = "access_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_IGNORED_NODES = "ignored_nodes"
CONF_TRACKED_NODES = "tracked_nodes"

# Shared attribute / data keys
ATTR_TEXT    = "text"
ATTR_CHANNEL = "channel"
ATTR_TARGET  = "target"

# Defaults
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Platform names forwarded by __init__.py. ``notify`` is a config-entry
# tracked platform like the rest (Q2) so its entities are unloaded with the
# entry instead of lingering as legacy async_load_platform services.
PLATFORMS = ["binary_sensor", "sensor", "device_tracker", "geo_location", "notify"]

# Data-field keys (addon JSON response)
ATTR_NEIGHBORS          = "neighbors"
ATTR_LINKS              = "links"
ATTR_POSITION_FIXES     = "position_fixes"
ATTR_TRACEROUTE         = "traceroute"
ATTR_TAGS               = "tags"
ATTR_POSITION_FIX_COUNT = "position_fix_count"
ATTR_DISTANCE_KM        = "distance_km"
ATTR_NEIGHBOR_COUNT     = "neighbor_count"

# A Meshtastic node id is "!" followed by 1-8 hex digits (e.g. "!890bae69").
# Used to validate node ids before they are persisted into config-entry options
# and entity unique_ids (S8).
NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{1,8}$")


def is_valid_node_id(node_id: str) -> bool:
    """Return True when ``node_id`` is a canonical ``!hex`` Meshtastic node id."""
    return bool(NODE_ID_RE.fullmatch((node_id or "").strip()))


def normalize_node_id(raw) -> str | None:
    """Normalise a node id for comparison.

    The addon may emit ids as ``!XXXXXXXX`` (canonical), ``XXXXXXXX`` (no
    leading bang), or with mixed case. Returns the lowercased id without the
    leading bang so lookups across formats are consistent, or ``None`` for
    blank input.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    return s[1:] if s.startswith("!") else s
