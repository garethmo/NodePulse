"""Unit tests for app/remote_cache.py.

Tests exercise the full public API:
  - load_remote_cache       (missing file, valid file, corrupt JSON)
  - save_remote_cache       (happy path, serialisation failure, tmp cleanup)
  - get_cached_remote_config  (hit, miss)
  - update_cached_remote_config  (new node, overwrite)
  - patch_cached_remote_config   (new section, existing section, missing node)
"""
import json
import os
import tempfile
from unittest.mock import patch

import app.remote_cache as rc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _with_tmp(fn):
    """Decorator that runs a test inside a fresh temp dir and patches rc paths."""
    def wrapper():
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                fn(tmp, cache_file)
    return wrapper


# ---------------------------------------------------------------------------
# load_remote_cache
# ---------------------------------------------------------------------------

class TestLoadRemoteCache:
    def test_returns_empty_dict_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                assert rc.load_remote_cache() == {}

    def test_returns_empty_dict_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                f.write("this is not json {{{")
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                result = rc.load_remote_cache()
            assert result == {}

    def test_loads_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            payload = {"!aabbccdd": {"lora": {"hop_limit": 3}}}
            with open(cache_file, "w") as f:
                json.dump(payload, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                result = rc.load_remote_cache()
            assert result == payload

    def test_creates_data_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = os.path.join(root, "nested", "data")
            cache_file = os.path.join(data_dir, "remote_configs.json")
            with patch.object(rc, "_DATA_DIR", data_dir), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.load_remote_cache()
            # Directory must now exist.
            assert os.path.isdir(data_dir)


# ---------------------------------------------------------------------------
# save_remote_cache
# ---------------------------------------------------------------------------

class TestSaveRemoteCache:
    def test_saves_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            payload = {"!11223344": {"device": {"role": "CLIENT"}}}
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.save_remote_cache(payload)
            with open(cache_file) as f:
                on_disk = json.load(f)
            assert on_disk == payload

    def test_atomic_write_leaves_no_tmp_on_success(self):
        """The .tmp file must not persist after a successful save."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            tmp_path = cache_file + ".tmp"
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.save_remote_cache({"!aabb": {}})
            assert not os.path.exists(tmp_path)

    def test_failed_json_dump_leaves_original_intact(self):
        """If serialisation fails, the original cache file must be unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            original = {"!original": {"lora": {}}}
            with open(cache_file, "w") as f:
                json.dump(original, f)

            bad_payload = {"!bad": object()}  # not JSON-serialisable
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.save_remote_cache(bad_payload)  # must not raise

            # Original file must be intact.
            with open(cache_file) as f:
                assert json.load(f) == original

    def test_failed_json_dump_cleans_up_tmp(self):
        """Partial .tmp must be removed on serialisation failure."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            tmp_path = cache_file + ".tmp"
            bad_payload = {"!bad": object()}
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.save_remote_cache(bad_payload)
            assert not os.path.exists(tmp_path)


# ---------------------------------------------------------------------------
# get_cached_remote_config
# ---------------------------------------------------------------------------

class TestGetCachedRemoteConfig:
    def test_returns_none_when_cache_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                assert rc.get_cached_remote_config("!aabbccdd") is None

    def test_returns_config_for_known_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            payload = {"!aabbccdd": {"lora": {"hop_limit": 5}}}
            with open(cache_file, "w") as f:
                json.dump(payload, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                result = rc.get_cached_remote_config("!aabbccdd")
            assert result == {"lora": {"hop_limit": 5}}

    def test_returns_none_for_unknown_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({"!known": {}}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                assert rc.get_cached_remote_config("!unknown") is None


# ---------------------------------------------------------------------------
# update_cached_remote_config
# ---------------------------------------------------------------------------

class TestUpdateCachedRemoteConfig:
    def test_adds_new_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.update_cached_remote_config("!aabb1122", {"device": {}})
                result = rc.load_remote_cache()
            assert "!aabb1122" in result

    def test_overwrites_existing_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({"!aabb1122": {"lora": {"hop_limit": 3}}}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.update_cached_remote_config("!aabb1122", {"lora": {"hop_limit": 7}})
                result = rc.load_remote_cache()
            assert result["!aabb1122"]["lora"]["hop_limit"] == 7

    def test_preserves_other_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({"!aaaaaaaa": {"device": {}}}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.update_cached_remote_config("!bbbbbbbb", {"lora": {}})
                result = rc.load_remote_cache()
            assert "!aaaaaaaa" in result
            assert "!bbbbbbbb" in result


# ---------------------------------------------------------------------------
# patch_cached_remote_config
# ---------------------------------------------------------------------------

class TestPatchCachedRemoteConfig:
    def test_patches_existing_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({"!aabb": {"lora": {"hop_limit": 3, "tx_power": 20}}}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.patch_cached_remote_config("!aabb", "lora", {"hop_limit": 7})
                result = rc.load_remote_cache()
            # hop_limit is updated, tx_power is preserved.
            assert result["!aabb"]["lora"]["hop_limit"] == 7
            assert result["!aabb"]["lora"]["tx_power"] == 20

    def test_creates_missing_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({"!aabb": {}}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.patch_cached_remote_config("!aabb", "device", {"role": "ROUTER"})
                result = rc.load_remote_cache()
            assert result["!aabb"]["device"]["role"] == "ROUTER"

    def test_noop_when_node_not_cached(self):
        """patch_cached_remote_config must not create a stub entry for unknown nodes."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "remote_configs.json")
            with open(cache_file, "w") as f:
                json.dump({}, f)
            with patch.object(rc, "_DATA_DIR", tmp), \
                 patch.object(rc, "_CACHE_FILE", cache_file):
                rc.patch_cached_remote_config("!unknown", "lora", {"hop_limit": 5})
                result = rc.load_remote_cache()
            # Unknown node must NOT be created as a side-effect.
            assert "!unknown" not in result
