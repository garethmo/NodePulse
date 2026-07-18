"""
NodePulse — Shared Constants.

Centralises all string keys and default values used across the integration.
Importing from here instead of repeating literals in every file prevents
typo-driven bugs and makes refactoring trivial.
"""

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

# Platform names forwarded by __init__.py
PLATFORMS = ["binary_sensor", "sensor", "device_tracker"]
