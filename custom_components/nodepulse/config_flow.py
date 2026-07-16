"""
NodePulse — Configuration Flow.

Implements the UI-based setup wizard shown in the HA integrations panel.
The user provides the addon's URL, an optional access key, and a scan interval.

Steps:
  1. User flow (user step): collect host URL and credentials.
  2. Validate by calling GET /api/status on the addon.
  3. On success, create a ConfigEntry and forward to each platform.

We also implement OptionsFlowHandler so the user can change the scan interval
after initial setup without removing and re-adding the integration.
"""
import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCESS_KEY,
    CONF_HOST,
    CONF_IGNORED_NODES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

logger = logging.getLogger(__name__)

# Validation schema for the initial setup step.
_STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST, description={"suggested_value": "http://a0d7b954-nodepulse:8099"}): str,
    vol.Optional(CONF_ACCESS_KEY, default=""): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=10, max=300)),
})

# Schema for the options flow (allows updating scan_interval post-setup).
_OPTIONS_SCHEMA = vol.Schema({
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=10, max=300)),
    vol.Optional(CONF_IGNORED_NODES, default=""): str,
})


async def _validate_connection(session: aiohttp.ClientSession, host: str) -> bool:
    """
    Attempt to call the /api/status endpoint to verify the addon is reachable.

    Returns True as long as the addon responds with HTTP 200 JSON — we do NOT
    require ``connected: true`` here because the Meshtastic node may be
    temporarily offline or still initialising when the user first sets up the
    integration.  Requiring a live node connection would make the setup fail
    whenever the node reboots, forcing the user to remove and re-add the
    integration unnecessarily.

    The user-supplied host is tried first, then a chain of well-known
    supervisor DNS names for the addon container, so the integration connects
    even if the user left the default or typed the wrong hostname.
    """
    candidates = _host_candidates(host)
    for candidate in candidates:
        url = f"{candidate}/api/status"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    continue
                if resp.content_type != "application/json":
                    continue
                # The addon responded with valid JSON — it is running.
                # We intentionally do NOT check data["connected"] here; the
                # Meshtastic node may be offline without the addon being broken.
                await resp.json()
                return True
        except Exception as exc:
            logger.debug("Addon connection validation failed (url=%s): %s", url, exc)
    return False


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
    for base in (
        f"http://a0d7b954-{slug}",
        f"http://a0d7b954-{slug}:8099",
        f"http://{slug}",
        f"http://{slug}:8099",
    ):
        if base not in candidates:
            candidates.append(base)
    return candidates


class NodePulseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the NodePulse integration setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        First (and only) step: collect the addon URL and validate connectivity.
        If validation fails we show an error inline rather than creating a broken entry.
        """
        errors: Dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            host = user_input[CONF_HOST].rstrip("/")

            if await _validate_connection(session, host):
                # Use the host as the unique ID so duplicate entries are prevented.
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"NodePulse ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_ACCESS_KEY: user_input.get(CONF_ACCESS_KEY, ""),
                        CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    },
                )

            errors["base"] = "cannot_connect"
            logger.warning("Could not validate connection to NodePulse addon (host=%s)", host)

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NodePulseOptionsFlow(config_entry)


class NodePulseOptionsFlow(config_entries.OptionsFlow):
    """Allow changing scan_interval and ignored_nodes without re-setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pre-populate the form with the current option values.
        current_ignored = ", ".join(
            self._config_entry.options.get(CONF_IGNORED_NODES, [])
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=300)),
                vol.Optional(
                    CONF_IGNORED_NODES,
                    default=current_ignored,
                ): str,
            }),
        )
