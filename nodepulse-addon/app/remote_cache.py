import contextlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("NODEPULSE_DATA_DIR", "/data")
_CACHE_FILE = os.path.join(_DATA_DIR, "remote_configs.json")

def _get_cache_path() -> str:
    """Return the correct path based on NODEPULSE_DATA_DIR environment variable."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    return _CACHE_FILE

def load_remote_cache() -> dict[str, dict[str, Any]]:
    """Load the remote config cache from disk."""
    path = _get_cache_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load remote config cache: %s", exc)
    return {}

def save_remote_cache(cache: dict[str, dict[str, Any]]) -> None:
    """Atomically save the remote config cache to disk.

    Writes to a `.tmp` file first, then renames to the real path so that a
    serialization failure never corrupts an existing valid cache file.
    """
    path = _get_cache_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save remote config cache: %s", exc)
        # Clean up the partial temp file so it can't interfere later.
        with contextlib.suppress(OSError):
            os.remove(tmp_path)

def get_cached_remote_config(node_id: str) -> dict[str, Any] | None:
    """Get a cached remote config by node ID."""
    cache = load_remote_cache()
    return cache.get(node_id)

def update_cached_remote_config(node_id: str, config: dict[str, Any]) -> None:
    """Update and persist a remote config in the cache."""
    cache = load_remote_cache()
    cache[node_id] = config
    save_remote_cache(cache)

def patch_cached_remote_config(node_id: str, section: str, patch: dict[str, Any]) -> None:
    """Patch a specific section of a cached remote config."""
    cache = load_remote_cache()
    if node_id in cache:
        if section not in cache[node_id]:
            cache[node_id][section] = {}
        # Owner is a top level key, others are usually nested inside the config,
        # but the JSON serialization structures it so that sections are top-level keys.
        cache[node_id][section].update(patch)
        save_remote_cache(cache)
