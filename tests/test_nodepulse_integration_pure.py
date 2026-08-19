"""
Pure-logic tests for the NodePulse Home Assistant integration (Q1/T1).

These tests only import the dependency-free modules (``const.py`` and
``validation.py``), which have no ``homeassistant`` imports, so the suite runs
with plain pytest and no HA runtime installed. The integration's ``__init__.py``
(which imports homeassistant) is deliberately NOT executed — we install a fake
package so relative imports resolve.
"""
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODEPULSE_DIR = ROOT / "custom_components" / "nodepulse"

cc = types.ModuleType("custom_components")
cc.__path__ = []
sys.modules["custom_components"] = cc

nps = types.ModuleType("custom_components.nodepulse")
nps.__path__ = [str(NODEPULSE_DIR)]
sys.modules["custom_components.nodepulse"] = nps

import custom_components.nodepulse.const as const  # noqa: E402
import custom_components.nodepulse.validation as validation  # noqa: E402


# ---------------------------------------------------------------------------
# const.is_valid_node_id (S8)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "node_id",
    ["!00000000", "!ab12cd34", "!890bae69", "!ABCDEF00", "!a", "!deadbeef"],
)
def test_is_valid_node_id_accepts_canonical_ids(node_id):
    assert const.is_valid_node_id(node_id)


@pytest.mark.parametrize(
    "node_id",
    [
        "ab12cd34",  # no leading bang
        "!zzz00000",  # non-hex
        "!123456789",  # too long (9 hex digits)
        "",
        None,
        "! ab12cd34",  # embedded space
    ],
)
def test_is_valid_node_id_rejects_malformed_ids(node_id):
    assert not const.is_valid_node_id(node_id)


def test_is_valid_node_id_trims_whitespace():
    assert const.is_valid_node_id("  !ab12cd34  ")


# ---------------------------------------------------------------------------
# const.normalize_node_id
# ---------------------------------------------------------------------------
def test_normalize_node_id_returns_lowercased_without_bang():
    assert const.normalize_node_id("!AB12CD34") == "ab12cd34"
    assert const.normalize_node_id("AB12CD34") == "ab12cd34"
    assert const.normalize_node_id("  !Ab12Cd34  ") == "ab12cd34"


def test_normalize_node_id_returns_none_for_blank():
    assert const.normalize_node_id("") is None
    assert const.normalize_node_id(None) is None
    assert const.normalize_node_id("   ") is None


# ---------------------------------------------------------------------------
# validation.normalise_node_ids
# ---------------------------------------------------------------------------
def test_normalise_node_ids_parses_comma_list():
    raw = "!ab12cd34, !ef56ab78"
    assert validation.normalise_node_ids(raw) == ["!ab12cd34", "!ef56ab78"]


def test_normalise_node_ids_adds_bang_and_lowercases():
    assert validation.normalise_node_ids("AB12CD34,EF56AB78") == [
        "!ab12cd34",
        "!ef56ab78",
    ]


def test_normalise_node_ids_drops_invalid_and_blank_entries():
    raw = "  !ab12cd34 ,  , !zzz00000, foo, !00000000"
    assert validation.normalise_node_ids(raw) == ["!ab12cd34", "!00000000"]


def test_normalise_node_ids_handles_empty_input():
    assert validation.normalise_node_ids("") == []
    assert validation.normalise_node_ids(None) == []


# ---------------------------------------------------------------------------
# validation.validated_access_key (S13)
# ---------------------------------------------------------------------------
def test_validated_access_key_accepts_empty():
    assert validation.validated_access_key("") == ""
    assert validation.validated_access_key(None) == ""


def test_validated_access_key_trims_whitespace():
    assert validation.validated_access_key("  abcd1234  ") == "abcd1234"


def test_validated_access_key_rejects_short_key():
    with pytest.raises(ValueError):
        validation.validated_access_key("abc")


def test_validated_access_key_rejects_control_characters():
    with pytest.raises(ValueError):
        validation.validated_access_key("ab\ncd")
    with pytest.raises(ValueError):
        validation.validated_access_key("ab\x00cd")


def test_validated_access_key_accepts_long_key():
    assert validation.validated_access_key("abcd1234efgh") == "abcd1234efgh"