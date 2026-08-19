"""
NodePulse — Notify Platform.

Exposes ``notify.nodepulse`` entities so users can send Meshtastic text
messages from any HA automation, script, or the UI using the standard
``notify.send_message`` service.

One notify entity is created per gateway **and** per configured **channel**
(``notify.nodepulse_<name>``). A channel-pinned entity always broadcasts on
that channel; the gateway-level entity broadcasts on channel 0 by default.
For DMs (``target``) and channel overrides use the integration-level
``nodepulse.send_message`` service, which accepts both.

This is a regular config-entry platform (part of ``PLATFORMS``) so its
entities are tracked and unloaded with the entry — the legacy
``async_load_platform`` discovery path has been removed (Q2).
"""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TARGET,
    NotifyEntity,
    NotifyEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_CHANNEL, DOMAIN
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)

# Meshtastic channel indexes are 0-7.
_CHANNEL_MIN = 0
_CHANNEL_MAX = 7


def _clamp_channel(channel: Any) -> int:
    """Clamp a (possibly malformed) channel value to the valid 0-7 range (Q18)."""
    try:
        value = int(channel)
    except (TypeError, ValueError):
        value = 0
    return min(max(value, _CHANNEL_MIN), _CHANNEL_MAX)


def _channel_slug(channel: Dict[str, Any]) -> str:
    """Return a stable slug for a channel: its name (lower, spaces→_) or index."""
    name = (channel.get("name") or "").strip().lower().replace(" ", "_")
    return name or f"ch{_clamp_channel(channel.get('index', 0))}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the gateway notify entity and one entity per configured channel.

    Channels are read from the coordinator's last snapshot. If the addon was
    unreachable during the first refresh the list may be empty, so we also
    subscribe to coordinator updates and add channel entities as they appear.
    """
    coordinator: NodePulseCoordinator = hass.data[DOMAIN][entry.entry_id]

    gateway = NodePulseNotificationEntity(coordinator, entry, None)
    async_add_entities([gateway])

    created_indexes = set()

    @callback
    def _discover_channels() -> None:
        channels: List[Dict[str, Any]] = (coordinator.data or {}).get("channels") or []
        new_entities = []
        for ch in channels:
            index = _clamp_channel(ch.get("index", 0))
            if index in created_indexes:
                continue
            created_indexes.add(index)
            new_entities.append(NodePulseNotificationEntity(coordinator, entry, ch))
        if new_entities:
            logger.debug(
                "Registering notify entities for channels: %s",
                [_channel_slug(ch) for ch in channels],
            )
            async_add_entities(new_entities)

    _discover_channels()
    entry.async_on_unload(coordinator.async_add_listener(_discover_channels))


class NodePulseNotificationEntity(NotifyEntity):
    """Send a Meshtastic message via the NodePulse addon.

    ``_channel`` is ``None`` for the gateway-level entity, or a dict for a
    channel-pinned entity.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:radio-tower"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self,
        coordinator: NodePulseCoordinator,
        entry: ConfigEntry,
        channel: Optional[Dict[str, Any]],
    ) -> None:
        """Initialize the notify entity."""
        super().__init__()
        self.coordinator = coordinator
        self._entry = entry
        self._channel = channel
        self._attr_unique_id = (
            f"{entry.entry_id}_notify"
            if channel is None
            else f"{entry.entry_id}_notify_{_clamp_channel(channel.get('index', 0))}"
        )
        self._attr_name = (
            "NodePulse" if channel is None else f"NodePulse {_channel_slug(channel)}"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "NodePulse",
            "manufacturer": "NodePulse",
            "model": "Meshtastic Monitor",
        }

    async def async_send_message(
        self, message: str, title: Optional[str] = None, **kwargs: Any
    ) -> None:
        """Send ``message`` to the mesh."""
        if not message:
            return

        data: Dict[str, Any] = kwargs.get(ATTR_DATA) or {}

        if self._channel is not None:
            # Channel-pinned entity: always broadcast on this channel.
            channel = _clamp_channel(self._channel.get("index", 0))
            destination = None
        else:
            channel = _clamp_channel(data.get(ATTR_CHANNEL, 0))

            # A target of "" / None / "broadcast" means broadcast on the channel.
            destination = None
            targets: Any = kwargs.get(ATTR_TARGET) or []
            if isinstance(targets, str):
                targets = [targets]
            if targets:
                raw = targets[0]
                if raw and str(raw).lower() not in ("broadcast", "all"):
                    destination = str(raw)

        try:
            await self.coordinator.async_send_message(
                message, destination=destination, channel=channel
            )
        except Exception:  # surfaced to the caller's log
            logger.exception("NodePulse notify send failed (channel=%s)", channel)