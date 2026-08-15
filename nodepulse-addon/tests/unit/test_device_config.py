import pytest
from app.device_config import build_config_registry

def test_build_config_registry_returns_dict():
    """Ensure the config registry is built and contains expected top‑level keys."""
    registry = build_config_registry()
    assert isinstance(registry, dict)
    # Basic sanity checks – the registry should have a 'device' section
    # and at least one field inside it.
    assert "device" in registry
    assert isinstance(registry["device"], dict)
    # Spot‑check that a known field is present
    assert "node_info_broadcast_secs" in registry["device"]["fields"]