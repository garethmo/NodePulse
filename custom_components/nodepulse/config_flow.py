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
    vol.Required(CONF_HOST, description={"suggested_value": "http://localhost:8099"}): str,
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
    Attempt to call the /api/status endpoint to verify the addon is reachable
    AND actually connected to a node. Returns True only on HTTP 200 with a
    JSON body where ``connected`` is truthy — an error body (even with a 200)
    must not be treated as a valid connection.
    """
    url = f"{host.rstrip('/')}/api/status"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return False
            if resp.content_type != "application/json":
                return False
            data = await resp.json()
            return bool(data.get("connected"))
    except Exception as exc:
        logger.debug("Addon connection validation failed (url=%s): %s", url, exc)
    return False


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
            logger.warning({"host": host}, "Could not validate connection to NodePulse addon")

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
