"""
NodePulse — Pure validation helpers (no Home Assistant imports).

These helpers only touch strings / ints and are deliberately kept free of any
``homeassistant`` imports so they can be unit-tested without a full HA runtime
(Q1/T1). Anything that needs HA internals stays in ``config_flow.py``.
"""
from typing import List

from .const import is_valid_node_id


def normalise_node_ids(raw: str) -> List[str]:
    """Parse a comma-separated node-id string into a clean, canonical list.

    Accepts ``!abc1234``, ``abc1234``, or mixed case; each entry is stripped,
    lowercased, and given a leading ``!`` so the coordinator can match against
    the addon's ``!xxxxxxxx`` node ids by direct membership. Blank/whitespace
    entries and ids that are not valid canonical ``!hex`` node ids are dropped
    (S8).
    """
    out: List[str] = []
    for part in (raw or "").split(","):
        s = part.strip().lower()
        if not s:
            continue
        s = s[1:] if s.startswith("!") else s
        if s:
            candidate = "!" + s
            if is_valid_node_id(candidate):
                out.append(candidate)
    return out


def validated_access_key(value: str) -> str:
    """Trim and sanity-check the optional access key (S13).

    Returns the stripped key. Raises ``ValueError`` with a user-facing message
    when the value is present but clearly malformed (too short, or contains
    control characters from a bad paste).
    """
    key = (value or "").strip()
    if not key:
        return key
    if len(key) < 4:
        raise ValueError("Access key must be at least 4 characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("Access key contains invalid characters")
    return key