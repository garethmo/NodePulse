import logging
from typing import Any

import meshtastic.protobuf.config_pb2 as config_pb2
import meshtastic.protobuf.module_config_pb2 as module_config_pb2
from google.protobuf.descriptor import FieldDescriptor

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

# Semantic min/max constraints not expressible in the protobuf descriptors.
# Keyed by (section, field) with optional min/max/max_length. Used for both
# backend validation and for the schema returned to the Web UI.
_FIELD_CONSTRAINTS = {
    # Device
    ("device", "node_info_broadcast_secs"): {"min": 1, "max": 2**32 - 1},
    # LoRa
    ("lora", "bandwidth"):        {"min": 31,   "max": 500},
    ("lora", "spread_factor"):    {"min": 7,    "max": 12},
    ("lora", "coding_rate"):      {"min": 5,    "max": 8},
    ("lora", "frequency_offset"): {"min": -0.5, "max": 0.5},  # MHz
    ("lora", "hop_limit"):        {"min": 0,    "max": 7},
    ("lora", "channel_num"):      {"min": 0,    "max": 255},
    ("lora", "tx_power"):         {"min": -1,   "max": 30},
    # Network
    ("network", "wifi_ssid"): {"max_length": 32},
    ("network", "wifi_psk"):  {"max_length": 64},
    ("network", "ntp_server"): {"max_length": 128},
    ("position", "position_broadcast_secs"): {"min": 0, "max": 2**32 - 1},
    ("position", "gps_update_interval"):     {"min": 0, "max": 2**32 - 1},
    ("power", "wait_bluetooth_secs"):        {"min": 0, "max": 24 * 3600},
    # MeshBeacon (firmware 2.8+)
    ("mesh_beacon", "broadcast_interval_secs"): {"min": 3600, "max": 2**32 - 1},
    ("mesh_beacon", "broadcast_message"): {"max_length": 100},
}

def build_config_registry() -> dict[str, Any]:
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

                    # Merge semantic constraints (min/max/max_length) so the
                    # schema mirrors backend validation.
                    constraint = _FIELD_CONSTRAINTS.get((section_name, field.name))
                    if constraint:
                        field_def.update(constraint)

                    registry[section_name]["fields"][field.name] = field_def

    # Add pseudo-section for owner
    registry["owner"] = {
        "category": "special",
        "fields": {
            "long_name": {"type": "string", "label": "long_name", "max_length": 39},
            "short_name": {"type": "string", "label": "short_name", "max_length": 4}
        }
    }
    
    # MeshBeaconConfig — not yet in released meshtastic protobuf (2.7.x), added manually
    # for firmware 2.8+ support. Gated by firmware version in the UI.
    registry["mesh_beacon"] = {
        "category": "module",
        "fields": {
            "flags": {
                "type": "enum",
                "label": "flags",
                "enum_type": "MeshBeaconConfigFlags",
                "options": ["FLAG_NONE", "FLAG_LISTEN_ENABLED", "FLAG_BROADCAST_ENABLED", "FLAG_LEGACY_SPLIT"]
            },
            "broadcast_send_as_node": {"type": "int", "label": "broadcast_send_as_node"},
            "broadcast_message": {"type": "string", "label": "broadcast_message", "max_length": 100},
            "broadcast_offer_channel": {"type": "string", "label": "broadcast_offer_channel"},
            "broadcast_offer_region": {
                "type": "enum",
                "label": "broadcast_offer_region",
                "enum_type": "RegionCode",
                "options": []  # Populated dynamically from config_pb2
            },
            "broadcast_offer_preset": {
                "type": "enum",
                "label": "broadcast_offer_preset",
                "enum_type": "ModemPreset",
                "options": []  # Populated dynamically from config_pb2
            },
            "broadcast_on_channel": {"type": "string", "label": "broadcast_on_channel"},
            "broadcast_on_region": {
                "type": "enum",
                "label": "broadcast_on_region",
                "enum_type": "RegionCode",
                "options": []  # Populated dynamically from config_pb2
            },
            "broadcast_on_preset": {
                "type": "enum",
                "label": "broadcast_on_preset",
                "enum_type": "ModemPreset",
                "options": []  # Populated dynamically from config_pb2
            },
            "broadcast_interval_secs": {"type": "int", "label": "broadcast_interval_secs", "min": 3600},
        }
    }
    
    # Populate RegionCode and ModemPreset enum options for mesh_beacon fields
    # Also includes 2.8 enum values not present in 2.7.x library
    try:
        region_enum = config_pb2.Config.LoRaConfig.RegionCode
        preset_enum = config_pb2.Config.LoRaConfig.ModemPreset
        region_options = []
        for i in range(38):  # 2.8 adds regions up to 37 (ITU2_125CM)
            try:
                name = region_enum.Name(i)
                if name != 'UNSET':
                    region_options.append(name)
            except ValueError:
                pass
        # Manually add 2.8 regions not in 2.7.x library
        region_28 = {
            27: 'ITU1_2M', 28: 'ITU2_2M', 29: 'EU_866',
            30: 'EU_874', 31: 'EU_917', 32: 'EU_N_868',
            33: 'ITU3_2M', 34: 'ITU1_70CM', 35: 'ITU2_70CM',
            36: 'ITU3_70CM', 37: 'ITU2_125CM'
        }
        for _num, name in region_28.items():
            if name not in region_options:
                region_options.append(name)

        preset_options = []
        for i in range(17):  # 2.8 adds presets up to 16 (MEDIUM_TURBO)
            try:
                name = preset_enum.Name(i)
                if name != 'UNSET':
                    preset_options.append(name)
            except ValueError:
                pass
        # Manually add 2.8 presets not in 2.7.x library
        preset_28 = {
            10: 'LITE_FAST', 11: 'LITE_SLOW', 12: 'NARROW_FAST',
            13: 'NARROW_SLOW', 14: 'TINY_FAST', 15: 'TINY_SLOW',
            16: 'MEDIUM_TURBO'
        }
        for _num, name in preset_28.items():
            if name not in preset_options:
                preset_options.append(name)

        for field in ["broadcast_offer_region", "broadcast_on_region"]:
            registry["mesh_beacon"]["fields"][field]["options"] = region_options
        for field in ["broadcast_offer_preset", "broadcast_on_preset"]:
            registry["mesh_beacon"]["fields"][field]["options"] = preset_options
    except Exception:  # noqa: BLE001
        pass

    # StatusMessageConfig — firmware 2.8+ (simple status text for UI)
    registry["status_message"] = {
        "category": "module",
        "fields": {
            "node_status": {"type": "string", "label": "node_status", "max_length": 100},
        }
    }

    # TAKConfig — firmware 2.8+ (ATAK integration)
    registry["tak"] = {
        "category": "module",
        "fields": {
            "team": {
                "type": "enum",
                "label": "team",
                "enum_type": "Team",
                "options": ["UNSPECIFIED", "RED", "BLUE", "GREEN", "YELLOW", "CYAN", "MAGENTA", "ORANGE", "VIOLET", "WHITE", "BLACK", "BROWN", "PINK", "GREY", "LIGHT_BLUE", "DARK_RED", "DARK_GREEN", "DARK_BLUE"]
            },
            "role": {
                "type": "enum",
                "label": "role",
                "enum_type": "MemberRole",
                "options": ["UNSPECIFIED", "TEAM_LEADER", "TEAM_MEMBER", "MEDIC", "OBSERVER", "JTAC", "FIRE_SUPPORT", "UAS_OPERATOR", "K9_HANDLER", "EOD", "INTELLIGENCE", "ENGINEER", "COMMUNICATIONS", "LOGISTICS", "SNIPE", "RECON", "CROWD_CONTROL", "DRIVER", "PILOT", "CREW_CHIEF", "LOADMASTER", "GUNNER", "RIFLEMAN", "AUTOMATIC_RIFLEMAN", "GRENADIER", "MACHINE_GUNNER", "ANTI_TANK", "ANTI_AIR", "MORTAR", "ARTILLERY", "TANKER", "MECHANIZED", "AIR_ASSAULT", "AIRBORNE", "SPECIAL_FORCES", "CIVIL_AFFAIRS", "PSYOP", "CHAPLAIN", "LEGAL", "PUBLIC_AFFAIRS", "MEDICAL", "VETERINARY", "DENTAL", "PHARMACY", "LAB", "RADIOLOGY", "PREVENTIVE_MED", "MENTAL_HEALTH", "NUTRITION", "ENVIRONMENTAL", "INDUSTRIAL_HYGIENE", "OCCUPATIONAL_HEALTH", "BIOENVIRONMENTAL", "RADIATION", "CBRN", "EXPLOSIVE_ORDNANCE", "WEAPONS", "MISSILE", "SPACE", "CYBER", "INTEL_ANALYST", "CRYPTOLOGIC", "SIGNALS_INTEL", "HUMAN_INTEL", "GEOSPATIAL", "TARGETING", "FIRE_CONTROL", "AIR_DEFENSE", "MISSILE_DEFENSE", "COUNTER_INTEL", "SECURITY_FORCES", "LAW_ENFORCEMENT", "CORRECTIONS", "INVESTIGATIONS", "FORENSICS", "EMERGENCY_MGMT", "FIREFIGHTING", "SEARCH_RESCUE", "HAZMAT", "DISASTER_RELIEF", "HUMANITARIAN", "CIVIL_ENGINEER", "UTILITIES", "CONSTRUCTION", "HEAVY_EQUIP", "SURVEY", "MAPPING", "NAVIGATION", "COMMUNICATIONS_SPECIALIST", "SATELLITE_COMMS", "NETWORK_ADMIN", "SYS_ADMIN", "PROGRAMMER", "DATA_ANALYST", "DB_ADMIN", "SECURITY_SPECIALIST", "INFO_ASSURANCE", "COMPLIANCE", "ACQUISITION", "CONTRACTING", "LOGISTICS_READINESS", "TRANSPORTATION", "SUPPLY", "FUEL", "MAINTENANCE", "AVIONICS", "AIRCRAFT_MAINT", "MUNITIONS", "ARMAMENT", "ELECTRONIC_WARFARE", "RADAR", "SONAR", "UAV_OPERATOR", "ROBOTICS", "AI_ML", "QUANTUM", "OTHER"]
            },
        }
    }

    # TrafficManagementConfig — firmware 2.8+
    registry["traffic_management"] = {
        "category": "module",
        "fields": {
            "enabled": {"type": "bool", "label": "enabled"},
            "mqtt_enabled": {"type": "bool", "label": "mqtt_enabled"},
            "mqtt_downlink_enabled": {"type": "bool", "label": "mqtt_downlink_enabled"},
            "uplink_enabled": {"type": "bool", "label": "uplink_enabled"},
            "downlink_enabled": {"type": "bool", "label": "downlink_enabled"},
            "ignore_mqtt": {"type": "bool", "label": "ignore_mqtt"},
            "ignore_serial": {"type": "bool", "label": "ignore_serial"},
            "ignore_external_notification": {"type": "bool", "label": "ignore_external_notification"},
            "ignore_canned_message": {"type": "bool", "label": "ignore_canned_message"},
            "ignore_audio": {"type": "bool", "label": "ignore_audio"},
            "ignore_remote_hardware": {"type": "bool", "label": "ignore_remote_hardware"},
            "ignore_ambient_lighting": {"type": "bool", "label": "ignore_ambient_lighting"},
            "ignore_detection_sensor": {"type": "bool", "label": "ignore_detection_sensor"},
            "ignore_paxcounter": {"type": "bool", "label": "ignore_paxcounter"},
            "ignore_store_forward": {"type": "bool", "label": "ignore_store_forward"},
            "ignore_range_test": {"type": "bool", "label": "ignore_range_test"},
            "ignore_neighbor_info": {"type": "bool", "label": "ignore_neighbor_info"},
            "ignore_telemetry": {"type": "bool", "label": "ignore_telemetry"},
            "ignore_tak": {"type": "bool", "label": "ignore_tak"},
            "ignore_status_message": {"type": "bool", "label": "ignore_status_message"},
            "ignore_mesh_beacon": {"type": "bool", "label": "ignore_mesh_beacon"},
        }
    }

    # AmbientLightingConfig — firmware 2.8+ (LED control)
    registry["ambient_lighting"] = {
        "category": "module",
        "fields": {
            "enabled": {"type": "bool", "label": "enabled"},
            "led_gpio": {"type": "int", "label": "led_gpio"},
            "led_count": {"type": "int", "label": "led_count"},
            "led_type": {"type": "int", "label": "led_type"},
            "brightness": {"type": "int", "label": "brightness", "min": 0, "max": 255},
            "pattern": {"type": "int", "label": "pattern"},
            "color": {"type": "int", "label": "color"},
            "speed": {"type": "int", "label": "speed"},
        }
    }

    return registry


CONFIG_REGISTRY = build_config_registry()


def _normalize_role(raw: Any) -> str:
    """
    Normalise a Meshtastic device role to a clean string.

    The library may expose the role as a string ("CLIENT"), a protobuf enum
    object (str form "Role.CLIENT"), or an int (the enum value). Strip the
    enum prefix and return the bare name (or "" when unknown).
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.split(".")[-1] if raw else ""
    name = getattr(raw, "name", None)
    if name:
        return str(name).split(".")[-1]
    return str(raw).split(".")[-1]


def _resolve_role(interface, user_info: dict[str, Any]) -> str:
    """
    Resolve the connected node's role.

    The most authoritative source is the device config's role field, read
    directly from the protobuf (``localNode.localConfig.device.role``) — it is
    always present and never subject to the MessageToDict default-omission
    problem. The serialised user dict can omit the role when it holds the
    default CLIENT (0), so fall back through: user dict role, then the device
    metadata role, then the default CLIENT.
    """
    # 1) Device config role (authoritative, always present)
    try:
        local_config = getattr(interface.localNode, "localConfig", None)
        if local_config is not None:
            cfg_role = getattr(local_config.device, "role", 0)
            if cfg_role != 0:
                return config_pb2.Config.DeviceConfig.Role.Name(cfg_role)
    except Exception:  # noqa: BLE001
        pass

    # 2) User dict role
    role = _normalize_role(user_info.get("role"))
    if role:
        return role

    # 3) Device metadata role
    metadata = getattr(interface, "metadata", None)
    if metadata is not None:
        meta_role = getattr(metadata, "role", 0)
        if meta_role != 0:
            try:
                return config_pb2.Config.DeviceConfig.Role.Name(meta_role)
            except Exception:  # noqa: BLE001
                pass
    return "CLIENT"


def _lookup_node(interface, node_num) -> dict[str, Any]:
    """
    Look up a node's cached info dict by its integer node number.

    The meshtastic library keeps node DB access in two parallel maps:
    ``interface.nodesByNum`` keyed by the integer node number, and
    ``interface.nodes`` keyed by the "!xxxxxxxx" hex node ID string. Node
    numbers that haven't received a User packet yet only appear in
    ``nodesByNum``, so we have to try that first (an integer key), falling
    back to the hex-ID key in ``nodes``.
    """
    if node_num is None:
        return {}
    try:
        nodes_by_num = getattr(interface, "nodesByNum", None)
        if nodes_by_num:
            hit = nodes_by_num.get(node_num)
            if hit:
                return hit
        node_id = "!" + format(int(node_num) & 0xFFFFFFFF, "08x")
        nodes = getattr(interface, "nodes", None) or {}
        return (
            nodes.get(node_id)
            or nodes.get(node_num)
            or nodes.get(str(node_num))
            or {}
        )
    except (TypeError, ValueError):
        return {}


def request_full_config(iface) -> None:
    """
    Ask the radio to re-send every Config + ModuleConfig section, refreshing
    the in-memory ``localNode.localConfig`` / ``localNode.moduleConfig``.

    On meshtastic >= 2.5.0 ``requestConfig`` lives on the Node (not the
    interface) and takes a config field descriptor, so we iterate over the
    protobuf field descriptors and request each one. Sections that fail are
    logged and skipped rather than aborting the whole refresh.
    """
    local_node = iface.localNode
    if local_node is None:
        raise ValueError("localNode is not available — radio may still be handshaking")
    request = getattr(local_node, "requestConfig", None)
    if not callable(request):
        raise ValueError("requestConfig is not available on this library version")

    for descriptor in (config_pb2.Config.DESCRIPTOR, module_config_pb2.ModuleConfig.DESCRIPTOR):
        for field in descriptor.fields:
            try:
                request(field)
            except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
                logger.debug("requestConfig(%s) failed (skipped): %s", field.name, exc)


def read_device_config(interface) -> dict[str, Any]:
    """
    Reads the configuration from the interface and serializes it to JSON,
    using the registry to shape the output.
    """
    if not interface or not interface.localNode:
        raise ValueError("Node is not connected or localNode is missing")

    local_config = interface.localNode.localConfig
    module_config = interface.localNode.moduleConfig

    config_dict = {}

    # Field schema the UI needs to render inputs (types, enum options, ranges).
    # Keyed by section name — includes sections that may be absent values-wise.
    config_dict["_schema"] = _serialize_schema()

    # Serialize sections
    for section_name, section_info in CONFIG_REGISTRY.items():
        if section_info["category"] == "special":
            continue
            
        source_proto = local_config if section_info["category"] == "config" else module_config
        
        if hasattr(source_proto, section_name):
            section_obj = getattr(source_proto, section_name)
            
            filtered_dict = {}
            for field_name, field_info in section_info["fields"].items():
                if hasattr(section_obj, field_name):
                    val = getattr(section_obj, field_name)
                    if field_info["type"] == "enum":
                        # Convert enum integer to string name
                        try:
                            enum_descriptor = section_obj.DESCRIPTOR.fields_by_name[field_name].enum_type
                            enum_name = enum_descriptor.values_by_number[val].name
                            filtered_dict[field_name] = enum_name
                        except KeyError:
                            filtered_dict[field_name] = str(val)
                    else:
                        filtered_dict[field_name] = val
            
            config_dict[section_name] = filtered_dict
        else:
            # Section not available in firmware (e.g., mesh_beacon on < 2.8)
            # Provide default values so UI can show it greyed out
            if section_name == "mesh_beacon":
                config_dict[section_name] = {
                    "flags": 0,
                    "broadcast_send_as_node": 0,
                    "broadcast_message": "",
                    "broadcast_offer_channel": "",
                    "broadcast_offer_region": None,
                    "broadcast_offer_preset": None,
                    "broadcast_on_channel": "",
                    "broadcast_on_region": None,
                    "broadcast_on_preset": None,
                    "broadcast_interval_secs": 3600,
                }

    # Add owner (Node Identity — includes read-only identity info alongside the
    # editable names)
    try:
        my_node_num = interface.myInfo.my_node_num
        node_info = _lookup_node(interface, my_node_num)
        user_info = node_info.get("user", {})

        # Firmware version lives in interface.metadata (DeviceMetadata), not the
        # User protobuf — read it defensively.
        firmware_version = ""
        metadata = getattr(interface, "metadata", None)
        if metadata is not None:
            firmware_version = getattr(metadata, "firmware_version", "") or ""

        # Region lives in the LoRa config, not the User protobuf.
        region = ""
        try:
            lora = interface.localNode.localConfig.lora
            if lora is not None and lora.region != 0:
                region = config_pb2.Config.LoRaConfig.RegionCode.Name(lora.region)
        except Exception:  # noqa: BLE001
            pass

        config_dict["owner"] = {
            "long_name": user_info.get("longName", ""),
            "short_name": user_info.get("shortName", ""),
            "hw_model": user_info.get("hwModel", ""),
            "firmware_version": firmware_version,
            "region": region,
            "role": _resolve_role(interface, user_info),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read owner info: %s", exc)
        config_dict["owner"] = {
            "long_name": "", "short_name": "", "hw_model": "",
            "firmware_version": "", "region": "", "role": "",
        }
    
    return config_dict


def _serialize_schema() -> dict[str, Any]:
    """
    Serialise the section/field registry into a JSON-safe schema the Web UI
    can use to render inputs (types, enum options, min/max, max_length).
    """
    schema = {}
    for section_name, section_info in CONFIG_REGISTRY.items():
        fields = {}
        for field_name, field_def in section_info["fields"].items():
            fields[field_name] = {
                "type": field_def.get("type"),
                "label": field_def.get("label", field_name),
                "options": field_def.get("options"),
                "min": field_def.get("min"),
                "max": field_def.get("max"),
                "max_length": field_def.get("max_length"),
            }
        schema[section_name] = {
            "category": section_info.get("category"),
            "fields": fields,
        }
    return schema


def read_device_config_schema() -> dict[str, Any]:
    """Public accessor for the field schema consumed by the Web UI."""
    return _serialize_schema()


def validate_and_apply_patch(section_name: str, patch: dict[str, Any], local_node, interface) -> tuple[bool, bool]:
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
            raise ValueError("Invalid owner names provided") from None
        return True, False

    source_proto = local_node.localConfig if section_info["category"] == "config" else local_node.moduleConfig
    if not hasattr(source_proto, section_name):
        raise ValueError(f"Section {section_name} not available in firmware")
        
    section_obj = getattr(source_proto, section_name)
    
    # Check confirm requirement for danger zones
    if section_name == "device" and patch.get("role") == "ROUTER" and not patch.pop("confirm", False):
        raise ValueError("Setting role to ROUTER requires 'confirm': true")

    if section_name == "lora" and "tx_enabled" in patch and not patch["tx_enabled"] and not patch.pop("confirm", False):
        raise ValueError("Disabling LoRa TX requires 'confirm': true")

    # Manual LoRa parameter gating: when use_preset is true (the default and
    # the value applied by any modem preset), the manual radio params are
    # derived from the preset and must not be patched directly.
    if section_name == "lora":
        use_preset = patch.get("use_preset", getattr(section_obj, "use_preset", True))
        manual_params = {"bandwidth", "spread_factor", "coding_rate", "frequency_offset"}
        touched_manual = manual_params & set(patch.keys())
        if use_preset and touched_manual:
            raise ValueError(
                "Manual LoRa parameters (bandwidth/spread_factor/coding_rate/"
                "frequency_offset) require 'use_preset': false"
            )

    # MeshBeaconConfig special handling (firmware 2.8+)
    if section_name == "mesh_beacon":
        # flags is a bitfield (uint32) - combine the checkbox values
        if "flags" in patch:
            try:
                flags_val = int(patch["flags"])
                if flags_val < 0 or flags_val > 7:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("'flags' must be an integer 0-7 (bitfield)") from None
            section_obj.flags = flags_val
            # Remove from patch so it's not processed again
            patch.pop("flags")

        # broadcast_offer_channel and broadcast_on_channel are ChannelSettings messages
        # For now, we skip them as they require complex nested message handling.
        # The UI renders them as text inputs for channel name reference only.
        for ch_field in ("broadcast_offer_channel", "broadcast_on_channel"):
            if ch_field in patch:
                patch.pop(ch_field)

    # Validate and apply fields
    for field_name, value in patch.items():
        if field_name not in section_info["fields"]:
            # Ignore extra fields like 'confirm' if they leak through, or just skip
            continue
            
        field_info = section_info["fields"][field_name]

        # Field-level constraints from the registry (PII/danger zones aside),
        # enforce min/max for numerics and max_length for strings.
        fmin = field_info.get("min")
        fmax = field_info.get("max")
        flen = field_info.get("max_length")
        
        if field_info["type"] == "enum":
            if value not in field_info["options"]:
                raise ValueError(f"Invalid enum value '{value}' for '{field_name}'. Options: {field_info['options']}")
            enum_descriptor = section_obj.DESCRIPTOR.fields_by_name[field_name].enum_type
            enum_val = enum_descriptor.values_by_name[value].number
            setattr(section_obj, field_name, enum_val)
            
        elif field_info["type"] in ["int", "float"]:
            try:
                cast = float if field_info["type"] == "float" else int
                value = cast(value)
            except (TypeError, ValueError):
                raise ValueError(f"'{field_name}' must be a number") from None
            if fmin is not None and value < fmin:
                raise ValueError(f"'{field_name}' must be >= {fmin}")
            if fmax is not None and value > fmax:
                raise ValueError(f"'{field_name}' must be <= {fmax}")
            setattr(section_obj, field_name, value)
            
        elif field_info["type"] == "bool":
            setattr(section_obj, field_name, bool(value))
            
        elif field_info["type"] == "string":
            value = str(value)
            if flen is not None and len(value) > flen:
                raise ValueError(f"'{field_name}' exceeds max length {flen}")
            setattr(section_obj, field_name, value)
            
    # Write to device
    local_node.writeConfig(section_name)
            
    # Conservative reboot defaults
    reboot_sections = ["device", "lora", "position", "network", "mesh_beacon"]
    reboot_required = section_name in reboot_sections
    
    return True, reboot_required
