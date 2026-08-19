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
    CONF_TRACKED_NODES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .host_candidates import host_candidates_for_addon
from .validation import normalise_node_ids, validated_access_key

logger = logging.getLogger(__name__)

# Validation schema for the initial setup step.
# The suggested value uses the correct modern HAOS Supervisor DNS name.
_STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST, description={"suggested_value": "http://local-nodepulse"}): str,
    vol.Optional(CONF_ACCESS_KEY, default=""): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=10, max=300)),
})

# Schema for the options flow (allows updating scan_interval post-setup).
_OPTIONS_SCHEMA = vol.Schema({
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=10, max=300)),
    vol.Optional(CONF_IGNORED_NODES, default=""): str,
})

# Extra validation to ensure only supported keys are present
def _validate_options_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Filter to only allow known integration options."""
    allowed_keys = {CONF_SCAN_INTERVAL, CONF_IGNORED_NODES, CONF_TRACKED_NODES}
    return {k: v for k, v in data.items() if k in allowed_keys}


async def _validate_connection(
    session: aiohttp.ClientSession, host: str
) -> tuple[str | None, Dict[str, Any] | None]:
    """
    Attempt to call the /api/status endpoint to verify the addon is reachable.

    Returns ``(working_host, status_data)`` — the first candidate URL that
    responded with HTTP 200 JSON (so the caller can persist the *working* host,
    not the raw user input) and the parsed status body (which carries the
    gateway's stable ``node_id`` for the config-entry unique_id), or
    ``(None, None)`` if no candidate responded.

    We do NOT require ``connected: true`` here because the Meshtastic node may
    be temporarily offline or still initialising when the user first sets up
    the integration.  Requiring a live node connection would make the setup fail
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
                status_data = await resp.json()
                return candidate, status_data
        except Exception as exc:
            logger.debug("Addon connection validation failed (url=%s): %s", url, exc)
    return None, None


def _host_candidates(host: str) -> list:
    """Delegate to the shared host-candidate builder in host_candidates.py."""
    return host_candidates_for_addon(host)


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
            # Validate the optional access key (S13) before attempting the
            # connection check, so a typo / bad paste is reported immediately.
            try:
                access_key = validated_access_key(user_input.get(CONF_ACCESS_KEY, ""))
            except ValueError as exc:
                errors["base"] = "access_key"
                logger.warning("Config flow rejected access key: %s", exc)
                return self.async_show_form(
                    step_id="user",
                    data_schema=_STEP_USER_SCHEMA,
                    errors=errors,
                )

            session = async_get_clientsession(self.hass)
            host = user_input[CONF_HOST].rstrip("/")

            working_host, status_data = await _validate_connection(session, host)
            if working_host:
                # Persist the *working* candidate (which may be a supervisor DNS
                # alias that resolved when the user's raw input did not), so the
                # runtime coordinator connects reliably instead of retrying a
                # host that only validated via a fallback.
                #
                # Use the gateway's stable ``node_id`` from /api/status as the
                # config-entry unique_id when available (Q10), so the same addon
                # reached via different aliases can't create duplicate entries.
                # Fall back to the working host only when the gateway hasn't
                # connected yet (no ``my_info``) — the node_id is not known then.
                my_info = (status_data or {}).get("my_info") or {}
                node_id = my_info.get("node_id")
                unique_id = node_id or working_host
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"NodePulse ({working_host})",
                    data={
                        CONF_HOST: working_host,
                        CONF_ACCESS_KEY: access_key,
                        CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    },
                    options={
                        CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                        CONF_IGNORED_NODES: [],
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
            # Validate and filter to only allow known integration options.
            # This prevents addon-specific fields (e.g., telegram_forward_channels)
            # from being saved to the integration config entry.
            user_input = _validate_options_data(user_input)
            
            # Normalise the comma-separated ignored_nodes string into a clean
            # list (stripped, canonical "!hex" form) so the coordinator can
            # filter by direct membership rather than re-parsing a string.
            raw_ignored = (user_input.get(CONF_IGNORED_NODES) or "").strip()
            ignored = normalise_node_ids(raw_ignored)
            # Preserve any keys not edited here (e.g. tracked_nodes, which the
            # Web UI persists into options). Rebuilding the dict from scratch
            # used to silently wipe those — S5.
            options_data = dict(self._config_entry.options)
            options_data[CONF_SCAN_INTERVAL] = user_input.get(CONF_SCAN_INTERVAL)
            options_data[CONF_IGNORED_NODES] = ignored
            options_data = _validate_options_data(options_data)
            return self.async_create_entry(
                title="",
                data=options_data,
            )

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
