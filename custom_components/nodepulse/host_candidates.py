"""
NodePulse — Host candidate utilities.

Shared logic for building the ordered list of addon container DNS names to
probe when connecting between the HA integration (core) and the addon
(Docker / Supervisor container).

Candidate ordering matters because each failed probe costs a ~3-second connect
timeout. We put the most-likely-correct hosts first so the happy path is fast.

Modern HAOS Supervisor DNS naming (2024+):
  http://local-<slug>          ← correct, no port needed inside Supervisor net
  http://local-<slug>:<port>   ← same with explicit ingress port

Legacy / fallback patterns:
  http://a0d7b954-<slug>       ← old supervisor prefix (pre-2024)
  http://addon_<slug>          ← Docker Compose / manual install
  http://<slug>                ← bare slug (rare custom setups)
"""
import logging
from typing import List

logger = logging.getLogger(__name__)

# Canonical addon slug as defined in config.json.
_ADDON_SLUG = "nodepulse"

# Ingress port as defined in config.json.
_ADDON_PORT = 8099

# Old Supervisor hostname prefix (kept as fallback for pre-2024 HAOS).
_SUPERVISOR_PREFIX = "a0d7b954-"


def host_candidates_for_addon(user_host: str) -> List[str]:
    """
    Build an ordered list of host URLs to try when reaching the NodePulse addon.

    Order (most → least likely to work on a standard HAOS installation):
      1. User-supplied host — explicit config always wins.
      2. Modern Supervisor DNS: ``local-<slug>`` (no port, then with port).
      3. Legacy Supervisor DNS: ``a0d7b954-<slug>`` (old prefix, both forms).
      4. Docker Compose / manual: ``addon_<slug>``.
      5. Bare slug last resort.
    """
    slug = _ADDON_SLUG
    port = _ADDON_PORT

    # Build the ordered candidate list explicitly — readability over cleverness.
    ordered: List[str] = []

    # 1. User-supplied host (highest priority).
    if user_host:
        ordered.append(user_host.rstrip("/"))

    # 2. Modern HAOS Supervisor DNS (correct for HAOS 2024+).
    ordered.append(f"http://local-{slug}")
    ordered.append(f"http://local-{slug}:{port}")

    # 3. Legacy Supervisor prefix (fallback for older HAOS installs).
    ordered.append(f"http://{_SUPERVISOR_PREFIX}{slug}")
    ordered.append(f"http://{_SUPERVISOR_PREFIX}{slug}:{port}")

    # 4. Docker Compose / addon_ prefix (non-HAOS / manual installs).
    ordered.append(f"http://addon_{slug}")
    ordered.append(f"http://addon_{slug}:{port}")

    # 5. Bare slug (rare custom network setups).
    ordered.append(f"http://{slug}")
    ordered.append(f"http://{slug}:{port}")

    # Deduplicate while preserving insertion order so the user-supplied host
    # stays at position 0 even if it happens to match one of the patterns above.
    seen: set = set()
    deduped: List[str] = []
    for candidate in ordered:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)

    logger.debug("Addon host candidates (user_host=%s): %s", user_host, deduped)
    return deduped