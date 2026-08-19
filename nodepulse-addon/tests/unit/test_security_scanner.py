"""
Unit tests for app/security_scanner.py
"""
from unittest.mock import MagicMock

import pytest

from app.security_scanner import (
    _DEFAULT_PSK_BYTES,
    _PSK_DEFAULT_SHORTHAND,
    _channel_is_disabled,
    _classify_key,
    _key_fingerprint,
    _resolve_psk,
    scan_channel_keys,
)


def test_resolve_psk_default_shorthand():
    """Test that single-byte shorthand is expanded to default key."""
    result = _resolve_psk(_PSK_DEFAULT_SHORTHAND)
    assert result == _DEFAULT_PSK_BYTES


def test_resolve_psk_custom_key():
    """Test that custom key is returned unchanged."""
    custom_key = b'\x12\x34\x56\x78'
    result = _resolve_psk(custom_key)
    assert result == custom_key


def test_key_fingerprint():
    """Test key fingerprint generation."""
    key = b'\x12\x34\x56\x78' * 4  # 16 bytes
    fingerprint = _key_fingerprint(key)
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 16  # First 16 chars of SHA-256 hex


def test_classify_key_unencrypted_empty():
    """Test classification of empty key as unencrypted."""
    severity, reason = _classify_key(b"")
    assert severity == "unencrypted"
    assert "No encryption key set" in reason


def test_classify_key_unencrypted_zero():
    """Test classification of all-zero key as unencrypted."""
    severity, reason = _classify_key(b'\x00' * 16)
    assert severity == "unencrypted"
    assert "No encryption key set" in reason


def test_classify_key_weak_default():
    """Test classification of default Meshtastic key as weak."""
    severity, reason = _classify_key(_DEFAULT_PSK_BYTES)
    assert severity == "weak"
    assert "Meshtastic default key" in reason


def test_classify_key_weak_short():
    """Test classification of short key as weak."""
    severity, reason = _classify_key(b'\x12\x34')
    assert severity == "weak"
    assert "only 2 bytes" in reason


def test_classify_key_weak_nonstandard_length():
    """Test classification of non-standard key length as weak."""
    severity, reason = _classify_key(b'\x12' * 20)  # 20 bytes, not 16 or 32
    assert severity == "weak"
    assert "Unusual key length" in reason


def test_classify_key_secure_16_bytes():
    """Test classification of 16-byte custom key as secure."""
    severity, reason = _classify_key(b'\x12' * 16)
    assert severity == "secure"
    assert reason == "Custom unique key"


def test_classify_key_secure_32_bytes():
    """Test classification of 32-byte custom key as secure."""
    severity, reason = _classify_key(b'\x34' * 32)
    assert severity == "secure"
    assert reason == "Custom unique key"


def test_channel_is_disabled_disabled_role():
    """Test detection of disabled channel by role."""
    channel = MagicMock()
    role = MagicMock()
    role.value = 0  # DISABLED
    channel.role = role
    assert _channel_is_disabled(channel) is True


def test_channel_is_disabled_enabled_role():
    """Test detection of enabled channel by role."""
    channel = MagicMock()
    role = MagicMock()
    role.value = 2  # PRIMARY
    channel.role = role
    assert _channel_is_disabled(channel) is False


def test_channel_is_disabled_no_role():
    """Test channel without role attribute is not disabled."""
    channel = MagicMock()
    del channel.role
    assert _channel_is_disabled(channel) is False


def test_scan_channel_keys_basic():
    """Test basic channel key scanning."""
    # Create mock interface
    interface = MagicMock()
    local_node = MagicMock()
    
    # Create mock channels
    channel0 = MagicMock()
    channel0.index = 0
    channel0.settings = MagicMock()
    channel0.settings.name = "Primary"
    channel0.settings.psk = b'\x12' * 16  # Secure 16-byte key
    channel0.role = MagicMock()
    channel0.role.value = 2  # PRIMARY
    
    channel1 = MagicMock()
    channel1.index = 1
    channel1.settings = MagicMock()
    channel1.settings.name = "Secondary"
    channel1.settings.psk = b""  # Unencrypted
    channel1.role = MagicMock()
    channel1.role.value = 2
    
    local_node.channels = {0: channel0, 1: channel1}
    interface.localNode = local_node
    
    findings = scan_channel_keys(interface)
    
    assert len(findings) == 2
    assert findings[0]["channel_index"] == 0
    assert findings[0]["severity"] == "secure"
    assert findings[1]["channel_index"] == 1
    assert findings[1]["severity"] == "unencrypted"


def test_scan_channel_keys_skips_disabled():
    """Test that disabled channels are skipped."""
    interface = MagicMock()
    local_node = MagicMock()
    
    channel0 = MagicMock()
    channel0.index = 0
    channel0.settings = MagicMock()
    channel0.settings.name = "Primary"
    channel0.settings.psk = b'\x12' * 16
    channel0.role = MagicMock()
    channel0.role.value = 0  # DISABLED
    
    local_node.channels = {0: channel0}
    interface.localNode = local_node
    
    findings = scan_channel_keys(interface)
    
    assert len(findings) == 0


def test_scan_channel_keys_duplicate_detection():
    """Test duplicate key detection across channels."""
    interface = MagicMock()
    local_node = MagicMock()
    
    same_key = b'\x12' * 16
    
    channel0 = MagicMock()
    channel0.index = 0
    channel0.settings = MagicMock()
    channel0.settings.name = "Primary"
    channel0.settings.psk = same_key
    channel0.role = MagicMock()
    channel0.role.value = 2
    
    channel1 = MagicMock()
    channel1.index = 1
    channel1.settings = MagicMock()
    channel1.settings.name = "Secondary"
    channel1.settings.psk = same_key
    channel1.role = MagicMock()
    channel1.role.value = 2
    
    local_node.channels = {0: channel0, 1: channel1}
    interface.localNode = local_node
    
    findings = scan_channel_keys(interface)
    
    assert len(findings) == 2
    assert findings[0]["duplicate_of"] is None
    assert findings[1]["duplicate_of"] == 0


def test_scan_channel_keys_default_shorthand():
    """Test that default PSK shorthand is detected as weak."""
    interface = MagicMock()
    local_node = MagicMock()
    
    channel0 = MagicMock()
    channel0.index = 0
    channel0.settings = MagicMock()
    channel0.settings.name = "DefaultKey"
    channel0.settings.psk = _PSK_DEFAULT_SHORTHAND
    channel0.role = MagicMock()
    channel0.role.value = 2
    
    local_node.channels = {0: channel0}
    interface.localNode = local_node
    
    findings = scan_channel_keys(interface)
    
    assert len(findings) == 1
    assert findings[0]["severity"] == "weak"
    assert "Meshtastic default key" in findings[0]["reason"]


def test_scan_channel_keys_no_local_node():
    """Test error handling when localNode is not available."""
    interface = MagicMock()
    del interface.localNode
    
    with pytest.raises(ValueError, match="localNode is not available"):
        scan_channel_keys(interface)


def test_scan_channel_keys_no_channels():
    """Test error handling when channels are not available."""
    interface = MagicMock()
    local_node = MagicMock()
    del local_node.channels
    interface.localNode = local_node
    
    with pytest.raises(ValueError, match="Channel list not available"):
        scan_channel_keys(interface)


def test_scan_channel_keys_list_vs_dict():
    """Test that both list and dict channel structures work."""
    interface = MagicMock()
    local_node = MagicMock()
    
    channel0 = MagicMock()
    channel0.index = 0
    channel0.settings = MagicMock()
    channel0.settings.name = "Primary"
    channel0.settings.psk = b'\x12' * 16
    channel0.role = MagicMock()
    channel0.role.value = 2
    
    # Test with list
    local_node.channels = [channel0]
    interface.localNode = local_node
    
    findings = scan_channel_keys(interface)
    assert len(findings) == 1
    
    # Test with dict
    local_node.channels = {0: channel0}
    findings = scan_channel_keys(interface)
    assert len(findings) == 1
