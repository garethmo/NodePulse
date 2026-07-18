"""
NodePulse — Notify Platform.

Exposes ``notify.mesh_<entry>`` entities so users can send Meshtastic text
messages from any HA automation, script, or the UI using the standard
``notify.send_message`` service.

Mirroring the official Meshtastic integration, one notify entity is created
per gateway **and** per configured **channel** (``notify.mesh_<entry>_channel_<name>``).
A channel-pinned entity always broadcasts on that channel; the gateway-level
entity sends on the primary channel by default but honours ``target`` (a node
id for a DM) and ``data.channel`` overrides.
"""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.notify import (
    ATTR_TARGET,
    ATTR_DATA,
    BaseNotificationService,
)
from homeassistant.core import HomeAssistant

from .const import ATTR_CHANNEL, DOMAIN
from .coordinator import NodePulseCoordinator

logger = logging.getLogger(__name__)


def _channel_slug(channel: Dict[str, Any]) -> str:
    """Return a stable slug for a channel: its name (lower, spaces→_) or index."""
    name = (channel.get("name") or "").strip().lower().replace(" ", "_")
    return name or f"ch{channel.get('index', 0)}"


async def async_get_service(
    hass: HomeAssistant,
    config: Optional[Dict[str, Any]],
    discovery_info: Optional[Dict[str, Any]] = None,
) -> Optional[BaseNotificationService]:
    """Return the NodePulse notification service for the given config entry."""
    if discovery_info is None:
        return None
    entry_id = discovery_info.get("entry_id")
    coordinator: NodePulseCoordinator = hass.data[DOMAIN].get(entry_id)
    if coordinator is None:
        return None
    # ``channel`` is None for the gateway-level entity, or a dict for a
    # channel-pinned entity.
    channel = discovery_info.get("channel")
    return NodePulseNotificationService(hass, coordinator, entry_id, channel)


class NodePulseNotificationService(BaseNotificationService):
    """Send a Meshtastic message via the NodePulse addon."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NodePulseCoordinator,
        entry_id: str,
        channel: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self.hass = hass
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.channel = channel  # None = gateway-level, dict = pinned channel

    @property
    def name(self) -> Optional[str]:
        """Entity name — distinguishes channel-pinned entities in the UI."""
        if self.channel is None:
            return "NodePulse"
        return f"NodePulse {_channel_slug(self.channel)}"

    @property
    def targets(self) -> Dict[str, str]:
        """List known node ids as selectable targets for the UI.

        On a channel-pinned entity targets are irrelevant (always broadcast on
        the channel) so we return an empty map; the gateway entity offers nodes.
        """
        if self.channel is not None:
            return {}
        out: Dict[str, str] = {}
        for node in (self.coordinator.data or {}).get("nodes", []):
            nid = node.get("id")
            if not nid:
                continue
            label = node.get("long_name") or node.get("short_name") or nid
            out[f"{label} ({nid})"] = nid
        return out

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        """Send ``message`` to the mesh."""
        if not message:
            return

        data: Dict[str, Any] = kwargs.get(ATTR_DATA) or {}

        if self.channel is not None:
            # Channel-pinned entity: always broadcast on this channel.
            channel = int(self.channel.get("index", 0))
            destination = None
        else:
            targets: List[str] = kwargs.get(ATTR_TARGET) or []
            if isinstance(targets, str):
                targets = [targets]

            channel = int(data.get(ATTR_CHANNEL, 0))

            # A target of "" / None / "broadcast" means broadcast on the channel.
            destination = None
            if targets:
                raw = targets[0]
                if raw and raw.lower() not in ("broadcast", "all"):
                    destination = raw

        try:
            await self.coordinator.async_send_message(
                message, destination=destination, channel=channel
            )
        except Exception as exc:  # surfaced to the caller's log
            logger.error("NodePulse notify send failed: %s", exc)
