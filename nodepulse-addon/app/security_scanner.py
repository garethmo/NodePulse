"""
NodePulse — Security Scanner

Inspects the PSK (pre-shared key) of every configured Meshtastic channel and
classifies each one as 'secure', 'weak', or 'unencrypted'.

Rules
-----
- PSK b'\\x01' is the Meshtastic firmware shorthand for the well-known 128-bit
  default key.  The firmware expands it at the radio level, so any channel
  using this byte appears encrypted to casual inspection but is trivially
  decrypted by anyone with the public default key.  We classify it as 'weak'.
- Empty or all-zero PSK → 'unencrypted' (no encryption applied).
- Short PSK (1 < length < 16, but not the \\x01 shorthand) → 'weak'
  (AES-128/256 requires 16 or 32 bytes).
- 16-byte or 32-byte unique, non-default, non-zero key → 'secure'.
- Duplicate keys across channels are flagged with a 'duplicate_of' pointer
  (sharing a key collapses the security boundary between channels).

This module is intentionally read-only and stateless: it derives all findings
directly from the live interface object without making any radio I/O calls.
"""
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The well-known Meshtastic default 128-bit channel key.  This is public
# information documented in the Meshtastic spec and used by all stock-firmware
# installs that have never changed their primary channel PSK.
# https://meshtastic.org/docs/overview/encryption/
_DEFAULT_PSK_BYTES: bytes = bytes([
    0xd4, 0xf1, 0xbb, 0x3a, 0x20, 0x29, 0x07, 0x59,
    0xf0, 0xbc, 0xff, 0xab, 0xcf, 0x4e, 0x69, 0x01,
])

# Single-byte PSK value the firmware uses as a shorthand for the 128-bit
# default key above.
_PSK_DEFAULT_SHORTHAND: bytes = b'\x01'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_psk(raw_psk: bytes) -> bytes:
    """
    Expand the firmware's single-byte PSK shorthand to the full 128-bit key.

    The Meshtastic firmware stores PSK = b'\\x01' to mean "use the built-in
    default key".  Any other value is used verbatim.
    """
    if raw_psk == _PSK_DEFAULT_SHORTHAND:
        return _DEFAULT_PSK_BYTES
    return raw_psk


def _key_fingerprint(resolved_psk: bytes) -> str:
    """
    Return a short SHA-256 fingerprint of a resolved PSK for dedup detection.

    Only used server-side to compare keys across channels; never sent to the
    frontend (we never expose key material of any kind).
    """
    return hashlib.sha256(resolved_psk).hexdigest()[:16]


def _classify_key(resolved_psk: bytes) -> tuple[str, str]:
    """
    Classify a resolved PSK and return (severity, reason).

    severity is one of: 'unencrypted', 'weak', 'secure'.
    reason is a short human-readable explanation.
    """
    # No PSK at all — plaintext channel.
    if not resolved_psk or resolved_psk == bytes(len(resolved_psk)):
        return "unencrypted", "No encryption key set — channel traffic is plaintext"

    # The well-known Meshtastic default key.
    if resolved_psk == _DEFAULT_PSK_BYTES:
        return (
            "weak",
            "Using the Meshtastic default key — any stock-firmware node can "
            "decode these packets",
        )

    # Undersized key (firmware shorthand already resolved above, so this is
    # a genuinely short custom key — indicates misconfiguration).
    if len(resolved_psk) < 16:
        return (
            "weak",
            f"Key is only {len(resolved_psk)} bytes — AES-128/256 requires "
            "at least 16 bytes",
        )

    # Non-standard key length (not 16 or 32).
    if len(resolved_psk) not in (16, 32):
        return (
            "weak",
            f"Unusual key length ({len(resolved_psk)} bytes) — expected 16 "
            "or 32 bytes for AES-128/256",
        )

    return "secure", "Custom unique key"


def _channel_is_disabled(channel) -> bool:
    """Return True when a Channel protobuf object is disabled / inactive."""
    try:
        role = getattr(channel, "role", None)
        if role is None:
            return False
        # Protobuf enum: DISABLED = 0
        role_val = role.value if hasattr(role, "value") else int(role)
        return role_val == 0
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_channel_keys(interface) -> list[dict[str, Any]]:
    """
    Inspect every active channel's PSK and return a list of security findings.

    Each finding dict contains:
        channel_index  (int)
        channel_name   (str)
        severity       ('secure' | 'weak' | 'unencrypted')
        reason         (str)  — human-readable explanation
        duplicate_of   (int | None)  — channel_index of first channel with
                                        the same key, if any

    The function is intentionally safe to call at any time: it only reads
    already-cached protobuf state from the meshtastic library object and never
    issues any radio I/O.  Any per-channel error is logged and that channel
    skipped rather than aborting the entire scan.
    """
    local_node = getattr(interface, "localNode", None)
    if local_node is None:
        raise ValueError("localNode is not available — radio may still be handshaking")

    raw_channels = getattr(local_node, "channels", None)
    if raw_channels is None:
        raise ValueError("Channel list not available — try refreshing after connecting")

    findings: list[dict[str, Any]] = []
    # Map fingerprint → channel_index for duplicate detection.
    seen_fingerprints: dict[str, int] = {}

    for channel in raw_channels.values() if hasattr(raw_channels, "values") else raw_channels:
        try:
            idx = int(getattr(channel, "index", len(findings)))

            # Skip disabled channels — they carry no traffic.
            if _channel_is_disabled(channel):
                continue

            settings = getattr(channel, "settings", None)
            raw_psk: bytes = getattr(settings, "psk", b"") if settings else b""
            channel_name: str = getattr(settings, "name", "") if settings else ""

            # Resolve and classify.
            resolved = _resolve_psk(raw_psk)
            severity, reason = _classify_key(resolved)

            # Duplicate detection (only meaningful for keys that exist at all).
            duplicate_of: int | None = None
            if resolved:
                fp = _key_fingerprint(resolved)
                if fp in seen_fingerprints:
                    duplicate_of = seen_fingerprints[fp]
                else:
                    seen_fingerprints[fp] = idx

            findings.append({
                "channel_index": idx,
                "channel_name":  channel_name or ("Primary" if idx == 0 else f"Channel {idx}"),
                "severity":      severity,
                "reason":        reason,
                "duplicate_of":  duplicate_of,
            })

        except Exception as exc:  # pragma: no cover — defensive  # noqa: BLE001
            logger.debug("Security scan skipped a channel due to error: %s", exc)

    findings.sort(key=lambda f: f["channel_index"])
    return findings
