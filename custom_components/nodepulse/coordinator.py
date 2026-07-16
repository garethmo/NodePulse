"""
NodePulse — DataUpdateCoordinator.

The coordinator is the single source of truth for all entity data in the
integration. It polls the NodePulse addon API on a configurable interval
and caches the result. All sensor/tracker entities subscribe to it — this
means ONE API call per cycle regardless of how many entities exist, avoiding
the N-calls-per-N-entities anti-pattern.

On a failed fetch, HA's coordinator raises UpdateFailed, which marks all
entities as "unavailable" automatically — no per-entity error handling needed.
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Set

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ACCESS_KEY,
    CONF_HOST,
    CONF_IGNORED_NODES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_NODES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

logger = logging.getLogger(__name__)


class NodePulseCoordinator(DataUpdateCoordinator):
    """
    Polls the NodePulse addon API and stores the data for all entities.

    `coordinator.data` is structured as:
        {
            "status": { ... },     # from GET /api/status
            "nodes":  [ ... ],     # from GET /api/nodes
        }

    The integration only creates per-node entities (sensors + device_tracker)
    for nodes in ``tracked_nodes``. This set is populated from the config entry
    options and mutated at runtime by the Web UI's "Track in HA" toggle (which
    relays through the addon to the NodePulseTrackView HTTP endpoint).
    """

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self._host = config_entry.data[CONF_HOST].rstrip("/")
        self._session = async_get_clientsession(hass)
        self._config_entry = config_entry

        # Optional access key, forwarded to the addon as a request header so the
        # addon can authenticate with a node that requires it. Empty when unset.
        self._access_key = (config_entry.data.get(CONF_ACCESS_KEY) or "").strip() or None

        # Candidate host URLs to try when polling the addon. Starts with the
        # user-supplied host, then falls back to the standard supervisor addon
        # container DNS names so the integration keeps working even if the
        # configured host is wrong (from HA core, "localhost" is HA itself, not
        # the addon container).
        self._host_candidates = _host_candidates(self._host)

        # The first candidate that actually responds is promoted to the front of
        # the list so future polls skip straight to it without re-scanning DNS.
        self._working_host: str | None = None

        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL,
            config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        # Per-node entities are only created for nodes the user has chosen to
        # track. Loaded from persisted config options so it survives restarts.
        self.tracked_nodes: Set[str] = set(
            config_entry.options.get(CONF_TRACKED_NODES, [])
        )

        # Bookkeeping for dynamic per-node entity discovery. Kept on the
        # coordinator (per config entry) rather than module-level so a reload
        # (e.g. triggered by the "Track in HA" toggle) resets cleanly and
        # entities are re-created instead of being skipped forever. One pair
        # per platform because each platform tracks its own created entities.
        self.registered_sensor_ids: Set[str] = set()
        self.registered_sensor_entities: List[Any] = []
        self.registered_tracker_ids: Set[str] = set()
        self.registered_tracker_entities: List[Any] = []

        super().__init__(
            hass,
            logger,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    def set_tracked_node(self, node_id: str, enabled: bool) -> bool:
        """
        Add or remove a node from the tracked set.

        Returns True if the membership changed (so the caller knows whether to
        persist + rediscover), False if it was already in the requested state.
        """
        node_id = (node_id or "").strip()
        if not node_id:
            return False
        if enabled:
            if node_id in self.tracked_nodes:
                return False
            self.tracked_nodes.add(node_id)
            return True
        if node_id not in self.tracked_nodes:
            return False
        self.tracked_nodes.discard(node_id)
        return True

    async def persist_tracked_nodes(self, hass: HomeAssistant) -> None:
        """Write the current tracked set back into the config entry options."""
        new_options = dict(self._config_entry.options)
        new_options[CONF_TRACKED_NODES] = list(self.tracked_nodes)
        hass.config_entries.async_update_entry(
            self._config_entry, options=new_options
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Fetch a fresh snapshot from the addon.

        /api/status, /api/nodes, and /api/messages are fetched in this single method.
        If any fails we raise UpdateFailed so HA marks all entities
        as unavailable — this is preferable to silently returning stale data.
        """
        try:
            # If we previously discovered a working host, try it first so the
            # full candidate scan is only done once (avoids repeated DNS timeouts).
            if self._working_host:
                candidates = [self._working_host] + [
                    c for c in self._host_candidates if c != self._working_host
                ]
            else:
                candidates = self._host_candidates

            status, nodes, messages, working = await _fetch_all(
                self._session, candidates, self._access_key
            )
            if working and working != self._working_host:
                logger.info("NodePulse addon reached at %s — pinning as preferred host", working)
                self._working_host = working
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Network error reaching addon at {self._host}: {exc}") from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error fetching NodePulse data: {exc}") from exc

        logger.debug(
            "NodePulse data refreshed (host=%s, node_count=%s, message_count=%s)",
            self._host, len(nodes), len(messages) if messages else 0,
        )
        logger.debug(
            "NodePulse messages sample: %s",
            messages[:2] if messages else None
        )
        return {"status": status, "nodes": nodes, "messages": messages}


async def _fetch_all(
    session: aiohttp.ClientSession, candidates: list, access_key: str | None = None
) -> tuple[Dict, List[Dict], List[Dict], str | None]:
    """
    Fetch /api/status, /api/nodes, and /api/messages, returning the working host alongside the data.

    Tries each candidate in order until one responds. Returns a 4-tuple of
    (status, nodes, messages, working_host) so the coordinator can pin the responsive host
    and avoid repeated DNS timeouts on every subsequent poll.
    """
    import asyncio

    # Probe candidates sequentially (not concurrently) so we stop as soon as one
    # responds — firing all candidates in parallel would hammer every slug at once.
    working_host: str | None = None
    for host in candidates:
        try:
            status, nodes, messages = await asyncio.gather(
                _get_json(session, f"{host}/api/status", access_key),
                _get_json(session, f"{host}/api/nodes", access_key),
                _get_json(session, f"{host}/api/messages", access_key),
            )
            working_host = host
            return status, nodes, messages, working_host
        except Exception as exc:
            logger.debug("Addon host unreachable (host=%s): %s", host, exc)
            continue

    raise UpdateFailed(
        f"No NodePulse addon host was reachable. Tried: {', '.join(candidates)}"
    )


async def _get_json(session: aiohttp.ClientSession, url: str, access_key: str | None = None) -> Any:
    """
    Perform a GET request and return parsed JSON, raising on HTTP errors.

    Uses a short connect timeout (3 s) so unreachable / wrong-DNS candidates
    are skipped quickly rather than blocking for the full 10-second total timeout.
    A longer read timeout is kept for the actual data transfer.
    """
    headers = {"X-NodePulse-Access-Key": access_key} if access_key else None
    timeout = aiohttp.ClientTimeout(connect=3, total=10)
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        return await resp.json()


def _host_candidates(host: str) -> list:
    """
    Build an ordered list of host URLs to try when reaching the addon.

    Starts with the user-supplied value, then falls back through the standard
    supervisor addon container DNS names. The addon slug is ``nodepulse`` and
    supervisor prefixes addon container names with ``a0d7b954-``.
    """
    candidates = []
    if host:
        candidates.append(host.rstrip("/"))
    slug = "nodepulse"
    slug2 = "nodepulse_addon"
    for base in (
        f"http://a0d7b954-{slug}",
        f"http://a0d7b954-{slug}:8099",
        f"http://local-{slug}",
        f"http://local-{slug}:8099",
        f"http://local_{slug}",
        f"http://local_{slug}:8099",
        f"http://local-{slug2}",
        f"http://local-{slug2}:8099",
        f"http://local_{slug2}",
        f"http://local_{slug2}:8099",
        f"http://local-{slug2.replace('_', '-')}",
        f"http://local-{slug2.replace('_', '-')}:8099",
        f"http://{slug}",
        f"http://{slug}:8099",
    ):
        if base not in candidates:
            candidates.append(base)
    return candidates
