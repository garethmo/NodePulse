"""Unit tests for the remote node administration module."""

import base64

import pytest
from meshtastic.protobuf import config_pb2, module_config_pb2

import app.remote_admin as ra
from app.remote_admin import (
    admin_channel_index,
    apply_remote_config,
    execute_admin_action,
    read_remote_config,
    remote_actions,
    remote_admin_available,
    remote_admin_capability,
    request_remote_config,
)


class FakeChannel:
    def __init__(self, name, index=0):
        self.settings = type("S", (), {"name": name})()
        self.index = index


class FakeLocalNode:
    def __init__(self, channels, security=None):
        self.channels = channels
        self.localConfig = type("LC", (), {"security": security})()

    def _getAdminChannelIndex(self):
        for ch in self.channels:
            if getattr(ch.settings, "name", "").lower() == "admin":
                return ch.index
        return 0


class FakeInterface:
    def __init__(self, channels, security=None):
        self.localNode = FakeLocalNode(channels, security)
        self.metadata = type("M", (), {"firmware_version": "2.3.0"})()


class FakeRemoteNode:
    """Records admin operations; exposes real protobuf config objects."""

    def __init__(self):
        self.localConfig = config_pb2.Config()
        self.moduleConfig = module_config_pb2.ModuleConfig()
        self.calls = []
        self.nodeNum = 0x1234ABCD

    def requestConfig(self, field):
        self.calls.append(("requestConfig", field.name))

    def writeConfig(self, section_name):
        self.calls.append(("writeConfig", section_name))

    def setOwner(self, long_name="", short_name=""):
        self.calls.append(("setOwner", long_name, short_name))

    def reboot(self, secs):
        self.calls.append(("reboot", secs))

    def shutdown(self, secs):
        self.calls.append(("shutdown", secs))

    def factoryReset(self, full):
        self.calls.append(("factoryReset", full))

    def resetNodeDb(self):
        self.calls.append(("resetNodeDb",))

    def setFixedPosition(self, lat, lng, alt):
        self.calls.append(("setFixedPosition", lat, lng, alt))

    def removeFixedPosition(self):
        self.calls.append(("removeFixedPosition",))

    def setTime(self, epoch_secs):
        self.calls.append(("setTime", epoch_secs))

    def removeNode(self, target_id):
        self.calls.append(("removeNode", target_id))


# ----------------------------------------------------------------------
# ADMIN channel detection
# ----------------------------------------------------------------------
class TestAdminChannelDetection:
    def test_finds_named_admin_channel(self):
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)])
        assert admin_channel_index(iface) == 1

    def test_named_admin_channel_alone_is_inert(self):
        # A channel merely named "admin" is NOT capability — firmware only
        # honours it when admin_channel_enabled (legacy) is set.
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)])
        assert remote_admin_available(iface) is False

    def test_no_admin_channel(self):
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("Other", 1)])
        assert remote_admin_available(iface) is False

    def test_available_requires_local_node(self):
        iface = object()
        assert remote_admin_available(iface) is False

    def test_admin_keys_enable_capability(self):
        security = config_pb2.Config.SecurityConfig()
        security.admin_key.append(b"\x01\x02\x03")
        iface = FakeInterface([FakeChannel("Default", 0)], security=security)
        cap = remote_admin_capability(iface)
        assert cap["available"] is True
        assert cap["admin_key_count"] == 1
        assert cap["has_admin_channel"] is False
        assert cap["public_key"] is None
        assert cap["admin_keys"] == ["AQID"]

    def test_keypair_enables_capability(self):
        security = config_pb2.Config.SecurityConfig()
        security.public_key = b"\xaa" * 32
        security.private_key = b"\xbb" * 32
        iface = FakeInterface([FakeChannel("Default", 0)], security=security)
        cap = remote_admin_capability(iface)
        assert cap["available"] is True
        assert cap["has_keypair"] is True
        assert cap["admin_key_count"] == 0
        assert cap["public_key"] == base64.b64encode(b"\xaa" * 32).decode("ascii")
        assert cap["admin_keys"] == []

    def test_capability_no_local_config(self):
        iface = FakeInterface([FakeChannel("Default", 0)])
        del iface.localNode.localConfig
        cap = remote_admin_capability(iface)
        assert cap["available"] is False
        assert cap["admin_key_count"] == 0
        assert cap["has_keypair"] is False
        assert cap["public_key"] is None
        assert cap["admin_keys"] == []

    def test_actions_registry(self):
        actions = remote_actions()
        assert "reboot" in actions
        assert actions["factory_reset"]["danger"] is True


# ----------------------------------------------------------------------
# Reading remote config
# ----------------------------------------------------------------------
class TestReadRemoteConfig:
    def test_read_remote_config_serializes(self, monkeypatch):
        node = FakeRemoteNode()
        iface = FakeInterface([FakeChannel("admin", 0)])

        monkeypatch.setattr(ra, "_remote_user_info", lambda *a: {})
        monkeypatch.setattr(ra, "_remote_firmware", lambda *a: "2.3.0")

        result = read_remote_config(node, iface)
        assert "device" in result
        assert "position" in result
        assert "lora" in result
        assert "_schema" in result
        assert result["owner"]["firmware_version"] == "2.3.0"

    def test_request_remote_config_iterates_sections(self):
        node = FakeRemoteNode()
        request_remote_config(node)
        assert node.calls
        assert ("requestConfig", "device") in node.calls
        assert ("requestConfig", "lora") in node.calls

    def test_request_remote_config_skips_missing_sections(self, monkeypatch):
        node = FakeRemoteNode()

        def boom(field):
            raise RuntimeError("nope")

        monkeypatch.setattr(node, "requestConfig", boom)
        request_remote_config(node)  # must not raise


# ----------------------------------------------------------------------
# Applying remote config
# ----------------------------------------------------------------------
class TestApplyRemoteConfig:
    def test_apply_remote_config_valid(self):
        node = FakeRemoteNode()
        iface = FakeInterface([FakeChannel("admin", 0)])
        result = apply_remote_config(node, iface, "device", {"node_info_broadcast_secs": 123})
        assert result["applied"] is True
        assert result["section"] == "device"
        assert ("writeConfig", "device") in node.calls
        assert node.localConfig.device.node_info_broadcast_secs == 123

    def test_apply_remote_config_unknown_section(self):
        node = FakeRemoteNode()
        with pytest.raises(ValueError, match="Unknown section"):
            apply_remote_config(node, FakeInterface([FakeChannel("admin", 0)]), "nope", {})


# ----------------------------------------------------------------------
# Admin actions
# ----------------------------------------------------------------------
class TestAdminActions:
    def test_reboot(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "reboot", {"seconds": 7})
        assert ("reboot", 7) in node.calls

    def test_shutdown(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "shutdown")
        assert ("shutdown", 10) in node.calls

    def test_factory_reset_config_only(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "factory_reset")
        assert ("factoryReset", False) in node.calls

    def test_factory_reset_device(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "factory_reset_device")
        assert ("factoryReset", True) in node.calls

    def test_nodedb_reset(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "nodedb_reset")
        assert ("resetNodeDb",) in node.calls

    def test_set_fixed_position(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "set_fixed_position", {"lat": 37.5, "lng": -122.1, "alt": 25})
        assert ("setFixedPosition", 37.5, -122.1, 25) in node.calls

    def test_clear_fixed_position(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "clear_fixed_position")
        assert ("removeFixedPosition",) in node.calls

    def test_set_time(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "set_time", {"epoch_secs": 1600000000})
        assert ("setTime", 1600000000) in node.calls

    def test_remove_node(self):
        node = FakeRemoteNode()
        execute_admin_action(node, "remove_node", {"target_node_id": "!deadbeef"})
        assert ("removeNode", "!deadbeef") in node.calls

    def test_unknown_action(self):
        with pytest.raises(ValueError, match="Unknown admin action"):
            execute_admin_action(FakeRemoteNode(), "frobnicate")

    def test_action_result_shape(self):
        result = execute_admin_action(FakeRemoteNode(), "nodedb_reset")
        assert result["ok"] is True
        assert result["action"] == "nodedb_reset"
        assert "elapsed_s" in result

# ----------------------------------------------------------------------
# _make_remote_node fast-fail + session-key handshake
# ----------------------------------------------------------------------
class TestMakeRemoteNode:
    def test_requires_admin_capability(self):
        iface = FakeInterface([FakeChannel("Default", 0)])
        with pytest.raises(ConnectionError, match="no admin capability"):
            ra._make_remote_node(iface, "!1234abcd")

    def test_admin_keys_permit_construction(self, monkeypatch):
        import meshtastic.node as meshtastic_node

        security = config_pb2.Config.SecurityConfig()
        security.admin_key.append(b"\x01\x02\x03")
        iface = FakeInterface([FakeChannel("Default", 0)], security=security)
        fake = object()

        def fake_node_ctor(*args, **kwargs):
            return fake

        monkeypatch.setattr(meshtastic_node, "Node", fake_node_ctor)
        monkeypatch.setattr(ra, "_session_key_handshake", lambda node, iface: None)

        result = ra._make_remote_node(iface, "!1234abcd")
        assert result is fake


class TestAdminSendChannel:
    def test_default_uses_primary_channel(self):
        # No admin_channel_enabled -> firmware 2.5+ admin over primary (index 0),
        # even when a channel is coincidentally named "admin".
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)])
        assert ra._admin_send_channel_index(iface) == 0

    def test_admin_keys_use_primary_channel(self):
        security = config_pb2.Config.SecurityConfig()
        security.admin_key.append(b"\x01\x02\x03")
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)], security=security)
        assert ra._admin_send_channel_index(iface) == 0

    def test_legacy_admin_channel_enabled_uses_named_channel(self):
        security = config_pb2.Config.SecurityConfig()
        security.admin_channel_enabled = True
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)], security=security)
        assert ra._admin_send_channel_index(iface) == 1

    def test_legacy_admin_channel_enabled_no_named_channel_uses_primary(self):
        security = config_pb2.Config.SecurityConfig()
        security.admin_channel_enabled = True
        iface = FakeInterface([FakeChannel("Default", 0)], security=security)
        assert ra._admin_send_channel_index(iface) == 0

    def test_bind_admin_channel_overrides_lookup_during_send(self):
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)])
        node = FakeRemoteNode()
        node._sendAdmin = lambda *a, **k: iface.localNode._getAdminChannelIndex()

        ra._bind_admin_channel(node, iface, 0)
        assert node._sendAdmin() == 0
        # The lookup is restored after the send.
        assert iface.localNode._getAdminChannelIndex() == 1

    def test_bind_admin_channel_noop_without_lookup(self):
        class NoLookupNode:
            pass

        iface = FakeInterface([FakeChannel("admin", 1)])
        iface.localNode = NoLookupNode()
        node = FakeRemoteNode()
        node._sendAdmin = lambda *a, **k: "sent"

        ra._bind_admin_channel(node, iface, 0)
        assert node._sendAdmin() == "sent"

    def test_bind_admin_channel_applied_in_make_remote_node(self, monkeypatch):
        import meshtastic.node as meshtastic_node

        security = config_pb2.Config.SecurityConfig()
        security.admin_key.append(b"\x01\x02\x03")
        iface = FakeInterface([FakeChannel("Default", 0), FakeChannel("admin", 1)], security=security)
        node = FakeRemoteNode()
        node._sendAdmin = lambda *a, **k: iface.localNode._getAdminChannelIndex()

        monkeypatch.setattr(meshtastic_node, "Node", lambda *a, **k: node)
        monkeypatch.setattr(ra, "_session_key_handshake", lambda n, i: None)

        result = ra._make_remote_node(iface, "!1234abcd")
        assert result is node
        assert node._sendAdmin() == 0

    def test_with_admin_capability_constructs_node(self, monkeypatch):
        import meshtastic.node as meshtastic_node

        security = config_pb2.Config.SecurityConfig()
        security.public_key = b"\xaa" * 32
        security.private_key = b"\xbb" * 32
        iface = FakeInterface([FakeChannel("admin", 1)], security=security)
        fake = object()

        def fake_node_ctor(*args, **kwargs):
            return fake

        monkeypatch.setattr(meshtastic_node, "Node", fake_node_ctor)
        monkeypatch.setattr(ra, "_session_key_handshake", lambda node, iface: None)

        result = ra._make_remote_node(iface, "!1234abcd")
        assert result is fake

    def test_session_key_handshake_restores_timeout(self):
        iface = FakeInterface([FakeChannel("admin", 0)])
        iface._timeout = type("T", (), {"expireTimeout": 20})()
        node = FakeRemoteNode()

        ra._session_key_handshake(node, iface)
        assert iface._timeout.expireTimeout == 20

    def test_session_key_handshake_survives_failure(self, monkeypatch):
        iface = FakeInterface([FakeChannel("admin", 0)])
        iface._timeout = type("T", (), {"expireTimeout": 20})()
        node = FakeRemoteNode()

        def boom():
            raise RuntimeError("session key timeout")

        node.ensureSessionKey = boom
        ra._session_key_handshake(node, iface)  # must not raise
        assert iface._timeout.expireTimeout == 20
