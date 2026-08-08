import logging
from typing import Dict, Any, Tuple
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.json_format import MessageToDict
import meshtastic.protobuf.config_pb2 as config_pb2
import meshtastic.protobuf.module_config_pb2 as module_config_pb2

logger = logging.getLogger(__name__)

# Protobuf Type mappings to human-readable strings for the frontend
_PROTO_TYPE_MAP = {
    FieldDescriptor.TYPE_BOOL: "bool",
    FieldDescriptor.TYPE_INT32: "int",
    FieldDescriptor.TYPE_INT64: "int",
    FieldDescriptor.TYPE_UINT32: "int",
    FieldDescriptor.TYPE_UINT64: "int",
    FieldDescriptor.TYPE_FLOAT: "float",
    FieldDescriptor.TYPE_DOUBLE: "float",
    FieldDescriptor.TYPE_STRING: "string",
    FieldDescriptor.TYPE_ENUM: "enum",
}

def build_config_registry() -> Dict[str, Any]:
    """
    Builds a registry of all editable config fields by introspecting the
    protobuf definitions from the installed meshtastic library.
    """
    registry = {}
    
    # We will loop over both Config and ModuleConfig
    sources = [
        ("config", config_pb2.Config.DESCRIPTOR),
        ("module", module_config_pb2.ModuleConfig.DESCRIPTOR)
    ]
    
    for category, descriptor in sources:
        for section_field in descriptor.fields:
            section_name = section_field.name
            registry[section_name] = {"category": category, "fields": {}}
            
            # If a field is a message, introspect it
            if section_field.type == FieldDescriptor.TYPE_MESSAGE:
                for field in section_field.message_type.fields:
                    field_def = {
                        "type": _PROTO_TYPE_MAP.get(field.type, "unknown"),
                        "label": field.name
                    }
                    if field.type == FieldDescriptor.TYPE_ENUM:
                        field_def["enum_type"] = field.enum_type.name
                        field_def["options"] = [v.name for v in field.enum_type.values]
                        
                    registry[section_name]["fields"][field.name] = field_def

    # Add pseudo-section for owner
    registry["owner"] = {
        "category": "special",
        "fields": {
            "long_name": {"type": "string", "label": "long_name", "max_length": 39},
            "short_name": {"type": "string", "label": "short_name", "max_length": 4}
        }
    }
    
    return registry


CONFIG_REGISTRY = build_config_registry()


def read_device_config(interface) -> Dict[str, Any]:
    """
    Reads the configuration from the interface and serializes it to JSON,
    using the registry to shape the output.
    """
    if not interface or not interface.localNode:
        raise ValueError("Node is not connected or localNode is missing")

    local_config = interface.localNode.localConfig
    module_config = interface.localNode.moduleConfig

    config_dict = {}

    # Serialize sections
    for section_name, section_info in CONFIG_REGISTRY.items():
        if section_info["category"] == "special":
            continue
            
        source_proto = local_config if section_info["category"] == "config" else module_config
        
        if hasattr(source_proto, section_name):
            section_obj = getattr(source_proto, section_name)
            
            section_dict_full = MessageToDict(
                section_obj, 
                preserving_proto_field_name=True, 
                including_default_value_fields=True
            )
            
            filtered_dict = {}
            for field_name in section_info["fields"].keys():
                if field_name in section_dict_full:
                    filtered_dict[field_name] = section_dict_full[field_name]
            
            config_dict[section_name] = filtered_dict

    # Add owner
    try:
        my_node_num = interface.myInfo.my_node_num
        node_info = interface.nodes.get(my_node_num) or interface.nodes.get(str(my_node_num)) or {}
        user_info = node_info.get("user", {})
        config_dict["owner"] = {
            "long_name": user_info.get("longName", ""),
            "short_name": user_info.get("shortName", "")
        }
    except Exception as e:
        logger.warning(f"Failed to read owner info: {e}")
        config_dict["owner"] = {"long_name": "", "short_name": ""}
    
    return config_dict

def validate_and_apply_patch(section_name: str, patch: Dict[str, Any], local_node, interface) -> Tuple[bool, bool]:
    """
    Validates a patch against the registry and applies it to the protobuf object in memory,
    then writes to the radio.
    Returns (success, reboot_required)
    """
    if section_name not in CONFIG_REGISTRY:
        raise ValueError(f"Unknown section: {section_name}")
        
    section_info = CONFIG_REGISTRY[section_name]

    # Special handling for owner
    if section_info["category"] == "special" and section_name == "owner":
        long_name = patch.get("long_name", "").strip()
        short_name = patch.get("short_name", "").strip()
        
        if not long_name or not short_name:
            raise ValueError("Long name and short name cannot be empty")
        if len(long_name) > 39:
            raise ValueError("Long name cannot exceed 39 characters")
        if len(short_name) > 4:
            raise ValueError("Short name cannot exceed 4 characters")
            
        # Write owner
        try:
            local_node.setOwner(long_name=long_name, short_name=short_name)
        except SystemExit:
            # meshtastic library calls sys.exit() on some bad inputs, catch if it does
            raise ValueError("Invalid owner names provided")
        return True, False

    source_proto = local_node.localConfig if section_info["category"] == "config" else local_node.moduleConfig
    if not hasattr(source_proto, section_name):
        raise ValueError(f"Section {section_name} not available in firmware")
        
    section_obj = getattr(source_proto, section_name)
    
    # Check confirm requirement for danger zones
    if section_name == "device" and patch.get("role") == "ROUTER":
        if not patch.pop("confirm", False):
            raise ValueError("Setting role to ROUTER requires 'confirm': true")
            
    if section_name == "lora" and "tx_enabled" in patch and not patch["tx_enabled"]:
        if not patch.pop("confirm", False):
            raise ValueError("Disabling LoRa TX requires 'confirm': true")
            
    # Validate and apply fields
    for field_name, value in patch.items():
        if field_name not in section_info["fields"]:
            # Ignore extra fields like 'confirm' if they leak through, or just skip
            continue
            
        field_info = section_info["fields"][field_name]
        
        if field_info["type"] == "enum":
            if value not in field_info["options"]:
                raise ValueError(f"Invalid enum value '{value}' for '{field_name}'. Options: {field_info['options']}")
            enum_descriptor = section_obj.DESCRIPTOR.fields_by_name[field_name].enum_type
            enum_val = enum_descriptor.values_by_name[value].number
            setattr(section_obj, field_name, enum_val)
            
        elif field_info["type"] in ["int", "float"]:
            setattr(section_obj, field_name, type(value)(value))
            
        elif field_info["type"] == "bool":
            setattr(section_obj, field_name, bool(value))
            
        elif field_info["type"] == "string":
            setattr(section_obj, field_name, str(value))
            
    # Write to device
    local_node.writeConfig(section_name)
            
    # Conservative reboot defaults
    reboot_sections = ["device", "lora", "position", "network"]
    reboot_required = section_name in reboot_sections
    
    return True, reboot_required
