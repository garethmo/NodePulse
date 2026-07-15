"""
NodePulse — Home Assistant Custom Integration.

Entry points called by HA Core:
  - async_setup_entry: Called when a ConfigEntry is loaded. Creates the
    coordinator and forwards setup to each platform.
  - async_unload_entry: Called when the user removes the integration. Unloads
    all platforms and cancels the coordinator's polling task.
"""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up NodePulse from a config entry.

    The coordinator is stored in hass.data so all platform modules can
    retrieve it without needing global variables or circular imports.
    """
    coordinator = NodePulseCoordinator(hass, entry)

    # Perform the first data fetch before setting up platforms.
    # This ensures entities have data available immediately after setup
    # rather than showing "unavailable" until the first poll cycle.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Forward to all platform modules (binary_sensor, sensor, device_tracker).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register a listener so option changes (e.g. scan_interval) trigger a reload.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    logger.info({"entry_id": entry.entry_id}, "NodePulse integration set up")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when the user updates options."""
    await hass.config_entries.async_reload(entry.entry_id)
