#!/usr/bin/env python3
"""Tests for kicadstamp/utils/file_cache.py — the mtime-keyed single-file
read cache that stops a GUI startup from re-parsing the same YAML/.kicad_sch
from disk a dozen times (see
techdocs/handoff/plan_2026_08_15_config_read_cache_startup.md).

Deliberately tests the PUBLIC contract of cached_file_read()/invalidate_path()
(file-level semantics), not the internals (_cache/_keys_by_path are module
globals intentionally left untouched by tests — pytest's tmp_path gives each
test a unique directory, so distinct paths never collide in the shared cache).
"""
import os
from pathlib import Path

import pytest
import yaml

from kicadstamp.utils.file_cache import cached_file_read, invalidate_path


def _yaml_loader(path):
    """A loader that returns a fresh dict from the YAML at `path` — the same
    `yaml.safe_load(...) or {}` contract the real callers use (never None)."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _bump_mtime_forward(path: Path, seconds: float = 1.0) -> None:
    """os.utime() the file a full second into the future so a coarse-timer
    filesystem (NTFS) cannot give the rewrite the same mtime_ns as the
    original write — guarantees the "changed file is a miss" test is testing
    the mtime key, not the filesystem's clock granularity."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns + int(seconds * 1e9),
                       st.st_mtime_ns + int(seconds * 1e9)))


def _pin_mtime(path: Path, mtime_ns: int) -> None:
    """os.utime() the file to an EXACT mtime_ns — used to force the
    delete-then-upsert race invalidate_path() exists to close, independent of
    how fine-grained this machine's real filesystem clock happens to be (a
    fine clock would otherwise give the two writes naturally different
    mtimes, making the race test pass for the wrong reason — see
    handoff_2026_08_21_mtime_race_tests_dont_race.md)."""
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_repeat_read_on_unchanged_file_calls_loader_once(tmp_path):
    path = tmp_path / "conf.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    calls = []

    def loader(p):
        calls.append(p)
        return {"a": 1}

    first = cached_file_read(path, loader)
    second = cached_file_read(path, loader)

    assert first == second == {"a": 1}
    assert len(calls) == 1  # the second call was a cache hit, no re-parse


def test_changed_file_is_reread(tmp_path):
    path = tmp_path / "conf.yaml"
    path.write_text("value: 1\n", encoding="utf-8")
    calls = []

    def loader(p):
        calls.append(p)
        return _yaml_loader(p)

    assert cached_file_read(path, loader) == {"value": 1}
    assert len(calls) == 1

    # External hand-edit: new content + a bumped mtime (a changed mtime is a
    # cache miss on its own — that's how external edits are picked up with no
    # explicit invalidate() call anywhere).
    path.write_text("value: 2\n", encoding="utf-8")
    _bump_mtime_forward(path)
    assert cached_file_read(path, loader) == {"value": 2}
    assert len(calls) == 2


def test_returned_dict_is_safe_to_mutate(tmp_path):
    """The cache returns a deep copy on every call (hit or miss) — mutating
    what a caller got back must never corrupt the cache or another caller's
    view (the real callers build shallow top-level copies that still alias
    nested objects, so this is the guarantee that makes auditing every
    downstream mutator unnecessary)."""
    path = tmp_path / "conf.yaml"
    path.write_text("items:\n  a: 1\n", encoding="utf-8")

    first = cached_file_read(path, _yaml_loader)
    first["items"]["a"] = 999
    first["new_key"] = "mutated"

    second = cached_file_read(path, _yaml_loader)
    assert second["items"]["a"] == 1
    assert "new_key" not in second


def test_missing_file_calls_loader_directly_and_is_not_cached_as_absent(tmp_path):
    """A missing file is handled by loader(path) directly, uncached — so a
    file that doesn't exist yet is never cached "as absent forever" and
    becomes visible on the next call once it's created."""
    path = tmp_path / "absent.yaml"
    calls = []

    def loader(p):
        calls.append(p)
        if not Path(p).exists():
            raise FileNotFoundError(p)
        return {"seen": True}

    with pytest.raises(FileNotFoundError):
        cached_file_read(path, loader)
    assert len(calls) == 1

    path.write_text("seen: true\n", encoding="utf-8")
    assert cached_file_read(path, loader) == {"seen": True}
    assert len(calls) == 2  # re-parsed from scratch, never cached while absent


def test_invalidate_path_forces_reread_without_mtime_change(tmp_path):
    """invalidate_path() must work independent of mtime at all — read to fill
    the cache, invalidate with NO content/mtime change on disk, read again:
    the loader must run a second time."""
    path = tmp_path / "conf.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    calls = []

    def loader(p):
        calls.append(p)
        return {"a": len(calls)}

    assert cached_file_read(path, loader) == {"a": 1}
    assert len(calls) == 1

    invalidate_path(path)
    assert cached_file_read(path, loader) == {"a": 2}
    assert len(calls) == 2


def test_invalidate_path_on_never_cached_path_is_noop(tmp_path):
    invalidate_path(tmp_path / "never.yaml")  # must not raise


def test_two_loaders_for_same_path_share_one_cache_entry(tmp_path):
    """The whole point of a single shared cache: config_writer's _read_data
    and config/includes' _load_yaml_file read the SAME file with different
    loader functions, and must still parse it once, not once per reader."""
    path = tmp_path / "conf.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    calls = []

    def loader_a(p):
        calls.append("a")
        return {"a": 1}

    def loader_b(p):
        calls.append("b")
        return {"a": 1}

    assert cached_file_read(path, loader_a) == {"a": 1}
    assert cached_file_read(path, loader_b) == {"a": 1}  # cache HIT, loader_b never runs
    assert calls == ["a"]


def test_write_data_delete_then_upsert_never_stale(tmp_path):
    """Regression for the exact race invalidate_path() exists to prevent
    (PlacerDock._do_save does delete_entry() then upsert_*() on the SAME
    file, two physical writes microseconds apart, see
    techdocs/handoff/plan_2026_08_15_config_read_cache_startup.md): the read
    after the SECOND write must see the second write's content even if the
    filesystem gave both writes the same mtime_ns.

    The race is FORCED, not left to the filesystem's clock: both
    delete-then-upsert writes are pinned to the SAME mtime_ns via
    _pin_mtime(), so the mtime-keyed cache ALONE would return the stale
    pre-write content — the test passes only because _write_data() calls the
    real invalidate_path(). Verified by temporarily removing invalidate_path()
    from _write_data and watching this test fail (see
    handoff_2026_08_21_mtime_race_tests_dont_race.md)."""
    from kicadstamp import config_writer

    path = tmp_path / "target.yaml"
    config_writer._write_data(path, {"clone_placements": [{"name": "old"}]})
    pinned_ns = os.stat(path).st_mtime_ns
    assert config_writer._read_data(path)["clone_placements"][0]["name"] == "old"

    # delete-then-upsert, back to back, both writes pinned to the SAME
    # mtime_ns so the mtime check alone cannot tell them apart:
    config_writer._write_data(path, {"clone_placements": []})
    _pin_mtime(path, pinned_ns)
    config_writer._write_data(path, {"clone_placements": [{"name": "new"}]})
    _pin_mtime(path, pinned_ns)

    data = config_writer._read_data(path)
    assert [e["name"] for e in data["clone_placements"]] == ["new"]
