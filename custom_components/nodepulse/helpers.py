"""
NodePulse — Shared helpers.

Small, dependency-light utilities used across the platform and entry modules.
Keeping them here (rather than duplicated per module) avoids drift: Q11
centralised the coordinator lookup that used to be copy-pasted into
``__init__.py``, ``api.py``, ``device_trigger.py`` and ``device_action.py``.
"""
from typing import Optional

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import NodePulseCoordinator


def coordinator_for(hass: HomeAssistant) -> Optional[NodePulseCoordinator]:
    """Return the first loaded NodePulse coordinator, or None.

    Used by integration-level service handlers, HTTP relay views, and device
    automations where the operation is not tied to a specific config entry. For
    multi-addon setups the first *loaded* coordinator wins; entry-scoped callers
    look the coordinator up by ``entry.entry_id`` directly.
    """
    data = hass.data.get(DOMAIN)
    if not data:
        return None
    for coordinator in data.values():
        return coordinator
    return None


def as_int(value) -> Optional[int]:
    """Coerce ``value`` to int, returning None for anything malformed (Q16).

    Addon payloads are JSON from a remote container we do not control; a string
    or None reaching ``int()``/``float()`` used to raise inside entity
    properties and event listeners.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value) -> Optional[float]:
    """Coerce ``value`` to float, returning None for anything malformed (Q16)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NodeDiscovery:
    """Shared dynamic per-node entity discovery (Q12).

    The four platform modules (sensor, binary_sensor, device_tracker,
    geo_location) used to each copy this register/remove loop. This class owns
    the per-entry bookkeeping (keyed on node id, so untrack -> re-track always
    re-creates entities) and hands the node-specific decisions to callbacks:

      * ``should_create(node)`` — True when this platform wants an entity for
        the node (e.g. it has a GPS fix).
      * ``make_entities(node)`` — return the entity (or list of entities) to
        add for a newly-tracked node.

    ``run`` is idempotent and safe to call on every coordinator refresh.
    """

    def __init__(self, coordinator, entry) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self._registered_node_ids = set()
        self._registered_entities = []

    def run(self, hass, async_add_entities, should_create, make_entities) -> None:
        """Sync this platform's entities to the current tracked node list."""
        nodes = (self.coordinator.data or {}).get("nodes", [])
        visible_ids = {n.get("id") for n in nodes if n.get("id")}
        tracked = self.coordinator.tracked_nodes

        # Remove entities for nodes that are no longer tracked (or gone).
        for entity in list(self._registered_entities):
            nid = getattr(entity, "_node_id", None)
            if nid is not None and (nid not in tracked or nid not in visible_ids):
                self._registered_entities.remove(entity)
                self._registered_node_ids.discard(nid)
                hass.async_create_task(entity.async_remove(force_remove=True))

        new_entities = []
        for node in nodes:
            node_id = node.get("id")
            if not node_id or node_id in self._registered_node_ids:
                continue
            if node_id not in tracked:
                continue
            if not should_create(node):
                continue
            added = make_entities(node) or []
            if not added:
                continue
            self._registered_node_ids.add(node_id)
            self._registered_entities.extend(added)
            new_entities.extend(added)

        if new_entities:
            async_add_entities(new_entities)

    def attach(self, hass, async_add_entities, should_create, make_entities) -> None:
        """Run once for the already-loaded data, then subscribe to updates.

        Subscribes on the coordinator so newly tracked nodes are picked up on
        the next refresh. The listener is removed with the config entry via
        ``entry.async_on_unload`` (callers must pass the entry).
        """
        self.run(hass, async_add_entities, should_create, make_entities)
        self.entry.async_on_unload(
            self.coordinator.async_add_listener(
                lambda: self.run(hass, async_add_entities, should_create, make_entities)
            )
        )