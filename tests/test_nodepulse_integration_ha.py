"""
Home Assistant integration tests for NodePulse (T1).

Covers ``async_setup_entry`` / ``async_unload_entry``, the options flow
(reload on update), untrack -> re-track via the coordinator's tracked-node
set, and the relay-view token validation (``_validate_token``).

These tests need the Home Assistant test runtime and are therefore skipped
cleanly (with a visible reason) when ``homeassistant`` or
``pytest_homeassistant_custom_component`` is not installed. Run them in an HA
development environment, e.g.::

    pip install homeassistant pytest-homeassistant-custom-component
    python -m pytest tests/test_nodepulse_integration_ha.py -q
"""
import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytest_plugins = "pytest_homeassistant_custom_component"

from unittest.mock import AsyncMock, MagicMock, Mock, patch  # noqa: E402

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.nodepulse import (  # noqa: E402
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
    _async_update_listener,
)
from custom_components.nodepulse.api import _validate_token  # noqa: E402
from custom_components.nodepulse.const import (  # noqa: E402
    CONF_HOST,
    CONF_IGNORED_NODES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_NODES,
    DOMAIN,
)
from custom_components.nodepulse.coordinator import NodePulseCoordinator  # noqa: E402


@pytest.fixture
def config_entry(hass):
    """A standard NodePulse config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="NodePulse",
        entry_id="nodepulse_test_entry",
        data={CONF_HOST: "http://localhost:3000"},
        options={CONF_SCAN_INTERVAL: 60},
    )


def _patch_coordinator():
    """Patch the coordinator class so setup does no real addon I/O."""
    patcher = patch("custom_components.nodepulse.NodePulseCoordinator")
    mock_cls = patcher.start()
    coordinator = mock_cls.return_value
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = Mock(return_value=lambda: None)
    coordinator.async_update_listener = Mock(return_value=lambda: None)
    return patcher, mock_cls, coordinator


# ---------------------------------------------------------------------------
# async_setup_entry / async_unload_entry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_setup_entry_creates_coordinator(hass, config_entry):
    config_entry.add_to_hass(hass)
    patcher, mock_cls, coordinator = _patch_coordinator()
    with patcher, patch.object(
        hass.config_entries, "async_forward_entry_setups"
    ) as mock_forward:
        assert await async_setup_entry(hass, config_entry) is True

    assert hass.data[DOMAIN][config_entry.entry_id] is coordinator
    mock_forward.assert_awaited_once()
    mock_forward.assert_awaited_once_with(config_entry, PLATFORMS)
    coordinator.async_add_listener.assert_called_once()
    # The options-change update listener must be registered (B5 regression).
    assert config_entry.update_listeners


@pytest.mark.asyncio
async def test_setup_entry_cleans_invalid_options(hass, config_entry):
    """Addon-specific option keys are stripped on setup (Q-session fix)."""
    config_entry.options = {
        CONF_SCAN_INTERVAL: 60,
        "addon_only_key": "stale",
        "another_invalid": 1,
    }
    config_entry.add_to_hass(hass)
    patcher, _, _ = _patch_coordinator()
    with patcher, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        assert await async_setup_entry(hass, config_entry) is True

    assert set(config_entry.options) == {
        CONF_SCAN_INTERVAL,
        CONF_IGNORED_NODES,
        CONF_TRACKED_NODES,
    }


@pytest.mark.asyncio
async def test_setup_entry_survives_first_refresh_failure(hass, config_entry):
    """A transient addon failure must not take the whole entry down."""
    config_entry.add_to_hass(hass)
    patcher, mock_cls, coordinator = _patch_coordinator()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=RuntimeError("addon unreachable")
    )
    with patcher, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        assert await async_setup_entry(hass, config_entry) is True

    assert hass.data[DOMAIN][config_entry.entry_id] is coordinator


@pytest.mark.asyncio
async def test_unload_entry_cleans_data(hass, config_entry):
    config_entry.add_to_hass(hass)
    patcher, _, _ = _patch_coordinator()
    with patcher, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        assert await async_setup_entry(hass, config_entry) is True

    with patch.object(hass.config_entries, "async_unload_platforms") as mock_unload:
        mock_unload.return_value = True
        assert await async_unload_entry(hass, config_entry) is True

    assert config_entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_unload_last_entry_removes_services(hass, config_entry):
    """When no config entries remain, integration-level services are removed."""
    hass.data[DOMAIN] = {}
    hass.services.async_register(DOMAIN, "send_message", Mock())
    hass.services.async_register(DOMAIN, "request_position", Mock())
    hass.services.async_register(DOMAIN, "trace_route", Mock())

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, config_entry) is True

    assert not hass.services.has_service(DOMAIN, "send_message")
    assert not hass.services.has_service(DOMAIN, "request_position")
    assert not hass.services.has_service(DOMAIN, "trace_route")


# ---------------------------------------------------------------------------
# Options flow: add -> remove -> re-add (reload on update)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_async_update_listener_reloads_entry(hass, config_entry):
    config_entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_reload") as mock_reload:
        await _async_update_listener(hass, config_entry)
    mock_reload.assert_awaited_once_with(config_entry.entry_id)


@pytest.mark.asyncio
async def test_options_flow_change_reloads_entry(hass, config_entry):
    """Changing options via the flow triggers the update listener -> reload."""
    config_entry.add_to_hass(hass)
    patcher, _, _ = _patch_coordinator()
    with patcher, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ), patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as mock_reload:
        assert await async_setup_entry(hass, config_entry) is True

        await hass.config_entries.async_set_options(
            config_entry.entry_id, {CONF_SCAN_INTERVAL: 120}
        )
        await hass.async_block_till_done()

    assert mock_reload.await_count >= 1
    assert config_entry.options[CONF_SCAN_INTERVAL] == 120


# ---------------------------------------------------------------------------
# Untrack -> re-track cycle (coordinator tracked-node set + persistence)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_untrack_retrack_cycle(hass, config_entry):
    config_entry.add_to_hass(hass)
    coordinator = NodePulseCoordinator(hass, config_entry)
    assert coordinator.tracked_nodes == set()

    # Track a node: membership changes -> True; a repeat call is a no-op.
    assert coordinator.set_tracked_node("!ab12cd34", True) is True
    assert coordinator.set_tracked_node("!ab12cd34", True) is False
    assert coordinator.tracked_nodes == {"!ab12cd34"}

    # Persisting writes the set back into the config entry options.
    await coordinator.persist_tracked_nodes(hass)
    entry = hass.config_entries.async_get_entry(config_entry.entry_id)
    assert entry.options[CONF_TRACKED_NODES] == ["!ab12cd34"]

    # Untrack: membership changes again -> True.
    assert coordinator.set_tracked_node("!ab12cd34", False) is True
    assert coordinator.tracked_nodes == set()
    await coordinator.persist_tracked_nodes(hass)
    assert hass.config_entries.async_get_entry(
        config_entry.entry_id
    ).options[CONF_TRACKED_NODES] == []

    # Re-track after untracking.
    assert coordinator.set_tracked_node("!ab12cd34", True) is True
    assert coordinator.tracked_nodes == {"!ab12cd34"}

    # Malformed ids are rejected without touching the set (S8).
    assert coordinator.set_tracked_node("not-a-node", True) is False
    assert coordinator.tracked_nodes == {"!ab12cd34"}


@pytest.mark.asyncio
async def test_persist_tracked_nodes_skips_missing_entry(hass, config_entry):
    """persist_tracked_nodes must not crash when the entry has been removed."""
    coordinator = NodePulseCoordinator(hass, config_entry)
    coordinator.set_tracked_node("!ab12cd34", True)
    # Entry never added to hass -> async_get_entry returns None.
    await coordinator.persist_tracked_nodes(hass)
    assert coordinator.tracked_nodes == {"!ab12cd34"}


# ---------------------------------------------------------------------------
# _validate_token (relay-view auth, Q7/Q13)
# ---------------------------------------------------------------------------
def _request(**headers):
    from aiohttp.test_utils import make_mocked_request

    return make_mocked_request(
        "GET", "/api/nodepulse/tracked-nodes", headers=headers or None
    )


def _no_auth_hass():
    """A fake hass with HA auth helpers that reject everything."""
    hass = MagicMock()
    hass.http.auth.async_validate_auth_header = AsyncMock(return_value=None)
    hass.auth.async_validate_access_token = AsyncMock(return_value=None)
    return hass


@pytest.mark.asyncio
async def test_validate_token_accepts_supervisor_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sekret-token")
    hass = MagicMock()
    assert (
        await _validate_token(hass, _request(Authorization="Bearer sekret-token"))
        is None
    )


@pytest.mark.asyncio
async def test_validate_token_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sekret-token")
    reason = await _validate_token(
        _no_auth_hass(), _request(Authorization="Bearer wrong-token")
    )
    assert reason is not None
    assert "bearer_len=12" in reason
    assert "bearer_head=wron" in reason


@pytest.mark.asyncio
async def test_validate_token_rejects_missing_auth(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    reason = await _validate_token(_no_auth_hass(), _request())
    assert reason is not None
    assert "supervisor_token=unset" in reason
    assert "bearer=no" in reason


@pytest.mark.asyncio
async def test_validate_token_accepts_ha_header_auth(monkeypatch):
    """A valid HA session-cookie / header auth is accepted as a fallback."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = _no_auth_hass()
    hass.http.auth.async_validate_auth_header = AsyncMock(return_value=object())
    assert (
        await _validate_token(
            hass, _request(Authorization="Bearer whatever-token")
        )
        is None
    )


@pytest.mark.asyncio
async def test_validate_token_accepts_access_token_fallback(monkeypatch):
    """A valid long-lived access token authenticates when the header path fails."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = _no_auth_hass()
    hass.auth.async_validate_access_token = AsyncMock(return_value=object())
    assert (
        await _validate_token(
            hass, _request(Authorization="Bearer llat-abc123")
        )
        is None
    )