import base64

import pytest
from meshtastic.protobuf import config_pb2, module_config_pb2

from app.device_config import (
    build_config_registry,
    serialize_config_sections,
    validate_and_apply_patch,
)


class FakeNode:
    """Minimal stand-in for a meshtastic local node with real protobufs."""

    def __init__(self):
        self.localConfig = config_pb2.Config()
        self.moduleConfig = module_config_pb2.ModuleConfig()
        self.written_sections = []
        self.owner = None

    def writeConfig(self, section_name):
        self.written_sections.append(section_name)

    def setOwner(self, long_name="", short_name=""):
        self.owner = {"long_name": long_name, "short_name": short_name}


def _fake_interface():
    return object()


# ----------------------------------------------------------------------
# Section / registry validation
# ----------------------------------------------------------------------
class TestRegistryValidation:
    def test_build_config_registry_returns_dict(self):
        registry = build_config_registry()
        assert isinstance(registry, dict)
        assert "device" in registry
        assert "node_info_broadcast_secs" in registry["device"]["fields"]

    def test_unknown_section_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Unknown section"):
            validate_and_apply_patch("nope", {}, node, _fake_interface())


# ----------------------------------------------------------------------
# Owner section
# ----------------------------------------------------------------------
class TestOwnerPatch:
    def test_owner_requires_both_names(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Long name and short name cannot be empty"):
            validate_and_apply_patch("owner", {"long_name": "  ", "short_name": "ab"}, node, _fake_interface())

    def test_owner_long_name_too_long(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Long name cannot exceed 39 characters"):
            validate_and_apply_patch("owner", {"long_name": "x" * 40, "short_name": "ab"}, node, _fake_interface())

    def test_owner_short_name_too_long(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Short name cannot exceed 4 characters"):
            validate_and_apply_patch("owner", {"long_name": "Valid", "short_name": "abcde"}, node, _fake_interface())

    def test_owner_valid(self):
        node = FakeNode()
        ok, reboot = validate_and_apply_patch("owner", {"long_name": "Gateway", "short_name": "GTW"}, node, _fake_interface())
        assert ok is True
        assert reboot is False
        assert node.owner == {"long_name": "Gateway", "short_name": "GTW"}


# ----------------------------------------------------------------------
# Danger-zone confirm gates
# ----------------------------------------------------------------------
class TestConfirmGates:
    def test_role_router_requires_confirm(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Setting role to ROUTER requires 'confirm': true"):
            validate_and_apply_patch("device", {"role": "ROUTER"}, node, _fake_interface())

    def test_role_router_with_confirm_applies(self):
        node = FakeNode()
        ok, reboot = validate_and_apply_patch(
            "device", {"role": "ROUTER", "confirm": True}, node, _fake_interface()
        )
        assert ok is True
        assert reboot is True  # device section always requires a reboot
        assert node.localConfig.device.role == config_pb2.Config.DeviceConfig.Role.ROUTER
        assert "device" in node.written_sections

    def test_lora_tx_disabled_requires_confirm(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Disabling LoRa TX requires 'confirm': true"):
            validate_and_apply_patch("lora", {"tx_enabled": False}, node, _fake_interface())

    def test_lora_tx_disabled_with_confirm_applies(self):
        node = FakeNode()
        ok, _reboot = validate_and_apply_patch(
            "lora", {"tx_enabled": False, "confirm": True}, node, _fake_interface()
        )
        assert ok is True
        assert node.localConfig.lora.tx_enabled is False

    def test_confirm_stripped_from_patch(self):
        # 'confirm' must not leak into the protobuf write.
        node = FakeNode()
        validate_and_apply_patch(
            "device", {"role": "CLIENT", "confirm": True}, node, _fake_interface()
        )
        assert node.localConfig.device.role == config_pb2.Config.DeviceConfig.Role.CLIENT


# ----------------------------------------------------------------------
# Manual LoRa parameter gating
# ----------------------------------------------------------------------
class TestManualLoRaGating:
    def test_manual_params_blocked_while_use_preset(self):
        node = FakeNode()
        node.localConfig.lora.use_preset = True
        with pytest.raises(ValueError, match="require 'use_preset': false"):
            validate_and_apply_patch("lora", {"bandwidth": 125}, node, _fake_interface())

    def test_manual_params_allowed_when_use_preset_false(self):
        node = FakeNode()
        node.localConfig.lora.use_preset = False
        ok, _ = validate_and_apply_patch(
            "lora", {"use_preset": False, "bandwidth": 125}, node, _fake_interface()
        )
        assert ok is True
        assert node.localConfig.lora.bandwidth == 125

    def test_use_preset_false_in_patch_permits_manual_params(self):
        node = FakeNode()
        ok, _ = validate_and_apply_patch(
            "lora", {"use_preset": False, "spread_factor": 7}, node, _fake_interface()
        )
        assert ok is True
        assert node.localConfig.lora.spread_factor == 7


# ----------------------------------------------------------------------
# Field-level constraints (enum, numeric range, string length, unknown fields)
# ----------------------------------------------------------------------
class TestFieldConstraints:
    def test_invalid_enum_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="Invalid enum value"):
            validate_and_apply_patch("device", {"role": "NOT_A_ROLE"}, node, _fake_interface())

    def test_numeric_out_of_range_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="tx_power"):
            validate_and_apply_patch("lora", {"tx_power": 99}, node, _fake_interface())

    def test_numeric_below_min_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="tx_power"):
            validate_and_apply_patch("lora", {"tx_power": -5}, node, _fake_interface())

    def test_non_numeric_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="must be a number"):
            validate_and_apply_patch("lora", {"tx_power": "lots"}, node, _fake_interface())

    def test_unknown_field_ignored(self):
        node = FakeNode()
        ok, _reboot = validate_and_apply_patch(
            "device", {"role": "CLIENT", "not_a_real_field": 1}, node, _fake_interface()
        )
        assert ok is True
        assert node.localConfig.device.role == config_pb2.Config.DeviceConfig.Role.CLIENT

    def test_valid_numeric_applies(self):
        node = FakeNode()
        ok, reboot = validate_and_apply_patch("lora", {"tx_power": 20}, node, _fake_interface())
        assert ok is True
        assert node.localConfig.lora.tx_power == 20
        assert reboot is True  # lora is a reboot section

    def test_string_length_enforced(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="exceeds max length"):
            validate_and_apply_patch("network", {"wifi_ssid": "x" * 33}, node, _fake_interface())

    def test_non_reboot_section_reports_reboot_false(self):
        node = FakeNode()
        ok, reboot = validate_and_apply_patch("telemetry", {"display_units": "METRIC"}, node, _fake_interface())
        assert ok is True
        assert reboot is False


# ----------------------------------------------------------------------
# mesh_beacon bitfield handling (firmware 2.8+ only)
# ----------------------------------------------------------------------
class TestMeshBeacon:
    def test_rejected_on_firmware_without_support(self):
        # The installed meshtastic protobuf (2.7.x) has no mesh_beacon message,
        # so the registry-driven patcher must reject the section defensively
        # rather than crash on an unknown protobuf attribute.
        node = FakeNode()
        with pytest.raises(ValueError, match="not available in firmware"):
            validate_and_apply_patch("mesh_beacon", {"flags": 3}, node, _fake_interface())


# ----------------------------------------------------------------------
# Security bytes serialization (base64 keys)
# ----------------------------------------------------------------------
class TestSecurityBytes:
    def test_security_section_in_registry(self):
        registry = build_config_registry()
        assert "security" in registry
        fields = registry["security"]["fields"]
        assert fields["public_key"]["type"] == "bytes"
        assert fields["private_key"]["type"] == "bytes"
        assert fields["admin_key"]["type"] == "bytes"
        assert fields["admin_key"]["repeated"] is True

    def test_serialize_bytes_base64_encodes(self):
        node = FakeNode()
        node.localConfig.security.public_key = b"\xaa" * 32
        node.localConfig.security.private_key = b"\xbb" * 32
        node.localConfig.security.admin_key.append(b"\x01\x02\x03")
        sections = serialize_config_sections(node.localConfig, node.moduleConfig)
        security = sections["security"]
        assert security["public_key"] == base64.b64encode(b"\xaa" * 32).decode("ascii")
        assert security["private_key"] == base64.b64encode(b"\xbb" * 32).decode("ascii")
        assert security["admin_key"] == ["AQID"]

    def test_serialize_empty_bytes(self):
        node = FakeNode()
        sections = serialize_config_sections(node.localConfig, node.moduleConfig)
        assert sections["security"]["public_key"] == ""
        assert sections["security"]["admin_key"] == []

    def test_apply_bytes_decodes_base64(self):
        node = FakeNode()
        b64 = base64.b64encode(b"\xcc" * 16).decode("ascii")
        ok, reboot = validate_and_apply_patch(
            "security", {"public_key": b64}, node, _fake_interface()
        )
        assert ok is True
        assert reboot is True  # security changes require a reboot
        assert node.localConfig.security.public_key == b"\xcc" * 16

    def test_apply_bytes_repeated(self):
        node = FakeNode()
        ok, _ = validate_and_apply_patch(
            "security",
            {"admin_key": ["AQID", "BAUG"]},
            node,
            _fake_interface(),
        )
        assert ok is True
        assert list(node.localConfig.security.admin_key) == [b"\x01\x02\x03", b"\x04\x05\x06"]

    def test_apply_invalid_base64_rejected(self):
        node = FakeNode()
        with pytest.raises(ValueError, match="must be valid base64"):
            validate_and_apply_patch("security", {"public_key": "!!!not-b64!!!"}, node, _fake_interface())

    def test_empty_strings_stay_strings(self):
        # Empty string fields must never be mistaken for an empty repeated-bytes
        # list (regression: they used to serialize as [] instead of "").
        node = FakeNode()
        sections = serialize_config_sections(node.localConfig, node.moduleConfig)
        assert sections["network"]["wifi_ssid"] == ""
        assert sections["network"]["wifi_psk"] == ""
        assert sections["network"]["ntp_server"] == ""

    def test_non_empty_bytes_are_base64(self):
        node = FakeNode()
        sec = node.localConfig.security
        sec.public_key = b"\xaa" * 32
        sections = serialize_config_sections(node.localConfig, node.moduleConfig)
        assert sections["security"]["public_key"] == base64.b64encode(b"\xaa" * 32).decode("ascii")
        assert sections["security"]["private_key"] == ""