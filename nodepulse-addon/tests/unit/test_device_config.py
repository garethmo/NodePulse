"""
Unit tests for app/device_config.py
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from google.protobuf.descriptor import FieldDescriptor

from app import device_config


class TestNormalizeRole:
    def test_normalize_role_none(self):
        assert device_config._normalize_role(None) == ""

    def test_normalize_role_empty_string(self):
        assert device_config._normalize_role("") == ""

    def test_normalize_role_plain_string(self):
        assert device_config._normalize_role("CLIENT") == "CLIENT"

    def test_normalize_role_enum_string(self):
        assert device_config._normalize_role("Role.CLIENT") == "CLIENT"

    def test_normalize_role_enum_object(self):
        mock_enum = Mock()
        mock_enum.name = "Role.ROUTER"
        assert device_config._normalize_role(mock_enum) == "ROUTER"

    def test_normalize_role_int(self):
        assert device_config._normalize_role(0) == "0"
        assert device_config._normalize_role(1) == "1"


class TestResolveRole:
    def test_resolve_role_from_device_config(self):
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.localConfig = Mock()
        mock_interface.localNode.localConfig.device = Mock()
        mock_interface.localNode.localConfig.device.role = 2  # ROUTER

        with patch("app.device_config.config_pb2.Config.DeviceConfig.Role.Name", return_value="ROUTER"):
            result = device_config._resolve_role(mock_interface, {})
            assert result == "ROUTER"

    def test_resolve_role_from_user_info(self):
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.localConfig = None

        result = device_config._resolve_role(mock_interface, {"role": "ROUTER"})
        assert result == "ROUTER"

    def test_resolve_role_from_metadata(self):
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.localConfig = None
        mock_interface.metadata = Mock()
        mock_interface.metadata.role = 3  # REPEATER

        with patch("app.device_config.config_pb2.Config.DeviceConfig.Role.Name", return_value="REPEATER"):
            result = device_config._resolve_role(mock_interface, {})
            assert result == "REPEATER"

    def test_resolve_role_default_client(self):
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.localConfig = None
        mock_interface.metadata = None

        result = device_config._resolve_role(mock_interface, {})
        assert result == "CLIENT"


class TestLookupNode:
    def test_lookup_node_none(self):
        mock_interface = Mock()
        result = device_config._lookup_node(mock_interface, None)
        assert result == {}

    def test_lookup_node_from_nodes_by_num(self):
        mock_interface = Mock()
        mock_interface.nodesByNum = {123: {"user": {"id": "!12345678"}}}
        mock_interface.nodes = {}

        result = device_config._lookup_node(mock_interface, 123)
        assert result == {"user": {"id": "!12345678"}}

    def test_lookup_node_from_nodes_hex(self):
        mock_interface = Mock()
        mock_interface.nodesByNum = {}
        mock_interface.nodes = {"!00000123": {"user": {"id": "!00000123"}}}

        result = device_config._lookup_node(mock_interface, 291)
        assert result == {"user": {"id": "!00000123"}}

    def test_lookup_node_from_nodes_int_key(self):
        mock_interface = Mock()
        mock_interface.nodesByNum = {}
        mock_interface.nodes = {123: {"user": {"id": "!12345678"}}}

        result = device_config._lookup_node(mock_interface, 123)
        assert result == {"user": {"id": "!12345678"}}

    def test_lookup_node_not_found(self):
        mock_interface = Mock()
        mock_interface.nodesByNum = {}
        mock_interface.nodes = {}

        result = device_config._lookup_node(mock_interface, 999)
        assert result == {}


class TestRequestFullConfig:
    def test_request_full_config_no_local_node(self):
        mock_iface = Mock()
        mock_iface.localNode = None

        with pytest.raises(ValueError, match="localNode is not available"):
            device_config.request_full_config(mock_iface)

    def test_request_full_config_no_request_method(self):
        mock_iface = Mock()
        mock_iface.localNode = Mock()
        mock_iface.localNode.requestConfig = None

        with pytest.raises(ValueError, match="requestConfig is not available"):
            device_config.request_full_config(mock_iface)

    def test_request_full_config_calls_request(self):
        mock_iface = Mock()
        mock_iface.localNode = Mock()
        mock_iface.localNode.requestConfig = Mock()

        with patch("app.device_config.config_pb2.Config.DESCRIPTOR") as mock_config_desc, \
             patch("app.device_config.module_config_pb2.ModuleConfig.DESCRIPTOR") as mock_module_desc:

            mock_config_desc.fields = [Mock(name="device")]
            mock_module_desc.fields = [Mock(name="mqtt")]

            device_config.request_full_config(mock_iface)

            assert mock_iface.localNode.requestConfig.call_count == 2


class TestReadDeviceConfig:
    def test_read_device_config_no_interface(self):
        with pytest.raises(ValueError, match="Node is not connected"):
            device_config.read_device_config(None)

    def test_read_device_config_no_local_node(self):
        mock_interface = Mock()
        mock_interface.localNode = None

        with pytest.raises(ValueError, match="localNode is missing"):
            device_config.read_device_config(mock_interface)


class TestValidateAndApplyPatch:
    def test_validate_unknown_section(self):
        mock_local_node = Mock()
        mock_interface = Mock()

        with pytest.raises(ValueError, match="Unknown section: invalid"):
            device_config.validate_and_apply_patch("invalid", {}, mock_local_node, mock_interface)

    def test_validate_owner_section_missing_names(self):
        mock_local_node = Mock()
        mock_interface = Mock()

        with pytest.raises(ValueError, match="Long name and short name cannot be empty"):
            device_config.validate_and_apply_patch("owner", {"long_name": "", "short_name": "AB"}, mock_local_node, mock_interface)

    def test_validate_owner_section_valid(self):
        mock_local_node = Mock()
        mock_local_node.setOwner = Mock()
        mock_interface = Mock()

        result = device_config.validate_and_apply_patch("owner", {"long_name": "Test", "short_name": "TST"}, mock_local_node, mock_interface)
        assert result == (True, False)
        mock_local_node.setOwner.assert_called_once_with(long_name="Test", short_name="TST")

    def test_validate_device_role_router_requires_confirm(self):
        mock_local_node = Mock()
        mock_local_node.localConfig = Mock()
        mock_local_node.localConfig.device = Mock()
        mock_local_node.writeConfig = Mock()
        mock_interface = Mock()

        with pytest.raises(ValueError, match="Setting role to ROUTER requires"):
            device_config.validate_and_apply_patch("device", {"role": "ROUTER"}, mock_local_node, mock_interface)

    def test_validate_lora_tx_disabled_requires_confirm(self):
        mock_local_node = Mock()
        mock_local_node.localConfig = Mock()
        mock_local_node.localConfig.lora = Mock()
        mock_local_node.writeConfig = Mock()
        mock_interface = Mock()

        with pytest.raises(ValueError, match="Disabling LoRa TX requires"):
            device_config.validate_and_apply_patch("lora", {"tx_enabled": False}, mock_local_node, mock_interface)


class TestSerializeSchema:
    def test_serialize_schema_structure(self):
        schema = device_config._serialize_schema()
        assert "device" in schema
        assert "lora" in schema
        assert "network" in schema
        assert "owner" in schema
        
        for section_name, section_info in schema.items():
            assert "category" in section_info
            assert "fields" in section_info
            for field_name, field_def in section_info["fields"].items():
                assert "type" in field_def
                assert "label" in field_def

    def test_serialize_schema_contains_constraints(self):
        schema = device_config._serialize_schema()
        lora_fields = schema["lora"]["fields"]
        assert "bandwidth" in lora_fields
        assert lora_fields["bandwidth"]["min"] == 31
        assert lora_fields["bandwidth"]["max"] == 500
        assert lora_fields["spread_factor"]["min"] == 7
        assert lora_fields["spread_factor"]["max"] == 12


class TestReadDeviceConfigSchema:
    def test_read_device_config_schema(self):
        schema = device_config.read_device_config_schema()
        assert "device" in schema
        assert "lora" in schema
        assert "network" in schema


class TestConfigRegistry:
    def test_config_registry_exists(self):
        assert device_config.CONFIG_REGISTRY is not None
        assert "device" in device_config.CONFIG_REGISTRY
        assert "lora" in device_config.CONFIG_REGISTRY
        assert "network" in device_config.CONFIG_REGISTRY
        assert "owner" in device_config.CONFIG_REGISTRY

    def test_owner_section_is_special(self):
        assert device_config.CONFIG_REGISTRY["owner"]["category"] == "special"
        assert "long_name" in device_config.CONFIG_REGISTRY["owner"]["fields"]
        assert "short_name" in device_config.CONFIG_REGISTRY["owner"]["fields"]


class TestProtoTypeMap:
    def test_proto_type_map_completeness(self):
        assert FieldDescriptor.TYPE_BOOL in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_INT32 in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_INT64 in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_UINT32 in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_UINT64 in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_FLOAT in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_DOUBLE in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_STRING in device_config._PROTO_TYPE_MAP
        assert FieldDescriptor.TYPE_ENUM in device_config._PROTO_TYPE_MAP