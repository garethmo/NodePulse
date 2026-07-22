"""
NodePulse — Host candidate utilities.

Shared logic for building the list of addon container DNS names to try when
connecting between the integration (HA core) and the addon (Docker container),
or between the addon and HA core.
"""
import logging
from typing import List

logger = logging.getLogger(__name__)

# Canonical addon slug as it appears in the supervisor's internal DNS.
_ADDON_SLUG = "nodepulse"
# Legacy/alternate slugs the supervisor has used historically.
_ALT_SLUGS = ["nodepulse_addon", "nodepulse-addon"]

# Supervisor's internal hostname prefix for addon containers.
_SUPERVISOR_PREFIX = "a0d7b954-"

# Local/non-HAOS variants.
_LOCAL_PREFIXES = ["addon_", "local-", "local_"]


def _build_base_candidates(slug: str) -> List[str]:
    """Build DNS candidates for a single slug."""
    return [
        f"http://{_SUPERVISOR_PREFIX}{slug}",
        f"http://{_SUPERVISOR_PREFIX}{slug}:8099",
    ]


def _build_local_candidates(slug: str) -> List[str]:
    """Build local/non-HAOS candidates for a single slug."""
    out = []
    for prefix in _LOCAL_PREFIXES:
        out.append(f"http://{prefix}{slug}")
        out.append(f"http://{prefix}{slug}:8099")
    return out


def host_candidates_for_addon(user_host: str) -> List[str]:
    """
    Build an ordered list of host URLs to try when reaching the NodePulse addon.

    Order:
      1. User-supplied host (if any) — tried first so explicit config wins.
      2. Supervisor internal DNS (a0d7b954-<slug>).
      3. Local / non-HAOS Docker Compose patterns (addon_*, local-*, local_*).
      4. Bare slug (for rare custom network setups).
    """
    candidates: List[str] = []

    if user_host:
        candidates.append(user_host.rstrip("/"))

    # All known slugs.
    for slug in [_ADDON_SLUG] + _ALT_SLUGS:
        candidates.extend(_build_base_candidates(slug))

    for slug in [_ADDON_SLUG] + _ALT_SLUGS:
        candidates.extend(_build_local_candidates(slug))

    # Bare slugs as last resort.
    for slug in [_ADDON_SLUG] + _ALT_SLUGS:
        candidates.append(f"http://{slug}")
        candidates.append(f"http://{slug}:8099")

    # Deduplicate while preserving order.
    seen = set()
    deduped = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    logger.debug("Addon host candidates (user_host=%s): %s", user_host, deduped)
    return deduped


def host_candidates_for_ha_core(user_host: str) -> List[str]:
    """
    Build an ordered list of host URLs to try when reaching HA core from the addon.

    Order:
      1. Supervisor standard hostnames (homeassistant, supervisor, hassio).
      2. Cached working host (passed as user_host if set).
      3. Fallback hostnames for non-HAOS setups (localhost, docker gateway).
    """
    # This is used by routes.py's _HA_CANDIDATES + _HA_FALLBACK_CANDIDATES.
    # Kept here for documentation; actual implementation lives in routes.py
    # to avoid circular import.
    pass