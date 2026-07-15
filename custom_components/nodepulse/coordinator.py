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
from typing import Any, Dict, List

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

logger = logging.getLogger(__name__)


class NodePulseCoordinator(DataUpdateCoordinator):
    """
    Polls the NodePulse addon API and stores the data for all entities.

    `coordinator.data` is structured as:
        {
            "status": { ... },     # from GET /api/status
            "nodes":  [ ... ],     # from GET /api/nodes
        }
    """

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self._host = config_entry.data[CONF_HOST].rstrip("/")
        self._session = async_get_clientsession(hass)

        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL,
            config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        super().__init__(
            hass,
            logger,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Fetch a fresh snapshot from the addon.

        Both /api/status and /api/nodes are fetched in this single method.
        If either fails we raise UpdateFailed so HA marks all entities
        as unavailable — this is preferable to silently returning stale data.
        """
        try:
            status, nodes = await _fetch_all(self._session, self._host)
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Network error reaching addon at {self._host}: {exc}") from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error fetching NodePulse data: {exc}") from exc

        logger.debug(
            "NodePulse data refreshed (host=%s, node_count=%s)",
            self._host, len(nodes),
        )
        return {"status": status, "nodes": nodes}


async def _fetch_all(
    session: aiohttp.ClientSession, host: str
) -> tuple[Dict, List[Dict]]:
    """
    Fetch /api/status and /api/nodes concurrently.

    Separating the network calls from the coordinator class keeps _fetch_all
    unit-testable without needing to mock the entire coordinator.
    """
    import asyncio

    status_coro = _get_json(session, f"{host}/api/status")
    nodes_coro  = _get_json(session, f"{host}/api/nodes")

    # Run both requests in parallel — they are independent.
    status, nodes = await asyncio.gather(status_coro, nodes_coro)
    return status, nodes


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    """Perform a GET request and return parsed JSON, raising on HTTP errors."""
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        return await resp.json()
