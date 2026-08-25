#!/usr/bin/env python3
"""Tests for the graph-level result cache (cached_graph_result) that wraps
load_config()/walk_include_tree() — the layer ABOVE the single-file mtime
cache, see
techdocs/handoff/deepseek/plan_2026_08_21_startup_graph_level_cache.md.

The cache is process-global and never cleared; pytest's tmp_path gives every
test a unique directory, so distinct paths never collide (same discipline as
tests/test_file_cache.py — module internals are deliberately left untouched).
"""
import os
from pathlib import Path

from kicadstamp.config import load_config
from kicadstamp.config.includes import walk_include_tree


def _bump_mtime_forward(path: Path, seconds: float = 1.0) -> None:
    """os.utime() the file a full second into the future so a coarse-timer
    filesystem cannot give the rewrite the same mtime_ns as the original
    write — guarantees the "changed file is a miss" tests exercise the mtime
    re-check, not the filesystem's clock granularity."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns + int(seconds * 1e9),
                       st.st_mtime_ns + int(seconds * 1e9)))


def _pin_mtime(path: Path, mtime_ns: int) -> None:
    """os.utime() the file to an EXACT mtime_ns — used to force the
    delete-then-upsert race invalidate_graph_path() exists to close,
    independent of how fine-grained this machine's real filesystem clock
    happens to be (a fine clock would otherwise give the two writes naturally
    different mtimes, making the race test pass for the wrong reason — see
    handoff_2026_08_21_mtime_race_tests_dont_race.md)."""
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _cell_yaml(name: str) -> str:
    """A load_config()-valid single-cell YAML body (same minimal shape as
    tests/gui/test_dock_hub_startup_reads.py's _MINIMAL_CELL)."""
    return (
        f"cells:\n"
        f"  {name}:\n"
        f"    components:\n"
        f"      - role: R1\n"
        f"        offset_along_mm: 0.0\n"
        f"        offset_across_mm: 0.0\n"
        f"        angle_deg: 0.0\n"
    )


_ROOT_SKELETON = (
    "layer: F.Cu\n"
    "rules: []\n"
    "cells: {}\n"
    "points: {}\n"
    "clone_placements: []\n"
    "thermal_via_arrays: []\n"
)


def _write_minimal(root: Path) -> Path:
    root.write_text(_ROOT_SKELETON, encoding="utf-8")
    return root


def _write_multi(root: Path, sub: Path, cell_name: str) -> None:
    """root includes sub; sub carries one cell. Same shape the GUI's per-dock
    graph walks exercise on every startup."""
    sub.write_text(_cell_yaml(cell_name), encoding="utf-8")
    root.write_text(_ROOT_SKELETON + "include:\n  - sub.yaml\n", encoding="utf-8")


def test_repeat_load_config_on_unchanged_path_runs_body_once(tmp_path, monkeypatch):
    """The core regression: a repeat load_config() on an UNCHANGED path must
    not re-run the traversal/merge/validation body — only the first call does
    (the second/third are graph-cache hits). Counted on the internal
    "uncached" version, same discipline as _parse_sheet_uuids_uncached in
    tests/test_sheet_names.py."""
    from kicadstamp.config import loader as loader_mod

    root = _write_minimal(tmp_path / "root.yaml")
    calls = []
    real = loader_mod._load_config_uncached

    def counting(p):
        calls.append(p)
        return real(p)

    monkeypatch.setattr(loader_mod, "_load_config_uncached", counting)
    cfg1, _ = load_config(str(root))
    cfg2, _ = load_config(str(root))
    cfg3, _ = load_config(str(root))

    assert cfg1.cells == cfg2.cells == cfg3.cells == {}
    assert len(calls) == 1, f"uncached body ran {len(calls)}x, want 1x"


def test_repeat_walk_include_tree_on_unchanged_path_runs_body_once(tmp_path, monkeypatch):
    """Same regression for walk_include_tree(): its traversal body runs once
    per changed graph, no matter how many docks ask for the same tree."""
    from kicadstamp.config import includes as includes_mod

    root = _write_minimal(tmp_path / "root.yaml")
    calls = []
    real = includes_mod._walk_include_tree_uncached

    def counting(p):
        calls.append(p)
        return real(p)

    monkeypatch.setattr(includes_mod, "_walk_include_tree_uncached", counting)
    walk_include_tree(str(root))
    walk_include_tree(str(root))
    walk_include_tree(str(root))

    assert len(calls) == 1, f"uncached body ran {len(calls)}x, want 1x"


def test_edit_an_included_file_invalidates_graph_cache(tmp_path):
    """Editing ANY file of the graph — not necessarily the root — must be
    visible on the next load_config() of the same root. Proves the mtime
    re-check covers the WHOLE file set, not just the root file."""
    root = tmp_path / "root.yaml"
    sub = tmp_path / "sub.yaml"
    _write_multi(root, sub, "c1")

    cfg1, _ = load_config(str(root))
    assert set(cfg1.cells) == {"c1"}

    # External hand-edit of the INCLUDED file only; root is untouched.
    sub.write_text(_cell_yaml("c2"), encoding="utf-8")
    _bump_mtime_forward(sub)

    cfg2, _ = load_config(str(root))
    assert set(cfg2.cells) == {"c2"}


def test_topology_change_via_new_include_is_seen(tmp_path):
    """A topology change (an include: starting to point at a new file) is
    detected through the mtime of the already-known file carrying the
    include: line — no separate topology logic needed."""
    root = _write_minimal(tmp_path / "root.yaml")
    cfg1, _ = load_config(str(root))
    assert cfg1.cells == {}

    b = tmp_path / "b.yaml"
    b.write_text(_cell_yaml("from_b"), encoding="utf-8")
    root.write_text(_ROOT_SKELETON + "include:\n  - b.yaml\n", encoding="utf-8")
    _bump_mtime_forward(root)

    cfg2, _ = load_config(str(root))
    assert set(cfg2.cells) == {"from_b"}


def test_write_data_delete_then_upsert_never_stale_graph(tmp_path):
    """Regression for the delete-then-upsert race ON THE GRAPH LAYER: fill the
    graph cache, then perform two physical Save-like writes of the same file
    back-to-back pinned to the SAME mtime_ns (the coarse-timer-filesystem
    scenario the mtime re-check alone cannot see), then load again — the
    second write must be visible. The race is FORCED via _pin_mtime(), so the
    graph cache ALONE would return the stale Config; the test passes only
    because _write_data() calls the real invalidate_graph_path() (verified by
    temporarily removing it and watching this test fail — see
    handoff_2026_08_21_mtime_race_tests_dont_race.md)."""
    from kicadstamp import config_writer

    root = tmp_path / "root.yaml"
    base = {"layer": "F.Cu", "rules": [], "points": {},
            "clone_placements": [], "thermal_via_arrays": []}
    cell_old = {"components": [{"role": "R1", "offset_along_mm": 0.0,
                                "offset_across_mm": 0.0, "angle_deg": 0.0}]}
    cell_new = {"components": [{"role": "R2", "offset_along_mm": 0.0,
                                "offset_across_mm": 0.0, "angle_deg": 0.0}]}
    config_writer._write_data(root, {**base, "cells": {"old": cell_old}})
    pinned_ns = os.stat(root).st_mtime_ns

    cfg1, _ = load_config(str(root))
    assert set(cfg1.cells) == {"old"}  # both cache layers now filled

    # delete-then-upsert, back to back, both writes pinned to the SAME
    # mtime_ns so the mtime re-check alone cannot tell them apart:
    config_writer._write_data(root, {**base, "cells": {}})
    _pin_mtime(root, pinned_ns)
    config_writer._write_data(root, {**base, "cells": {"new": cell_new}})
    _pin_mtime(root, pinned_ns)

    cfg2, _ = load_config(str(root))
    assert set(cfg2.cells) == {"new"}


def test_graph_cache_returns_shared_snapshot_copy_before_mutate(tmp_path):
    """2026-08-25 contract change: cached_graph_result returns the SHARED
    cached snapshot (no per-hit deepcopy — the old behavior cost ~0.5s of
    startup once the Config grew large). Two loads of an unchanged path are
    the SAME object, and a caller that copies before mutating (the GUI
    write-path docks do dataclasses.replace()) must not leak its mutation
    into the cached snapshot."""
    from dataclasses import replace

    root = _write_minimal(tmp_path / "root.yaml")
    cfg1, _ = load_config(str(root))
    cfg2, _ = load_config(str(root))
    assert cfg1 is cfg2  # shared snapshot, not a fresh deep copy

    cfg_copy = replace(cfg1)
    cfg_copy.thermal_via_arrays = ["MUTATED"]  # type: ignore[assignment]

    cfg3, _ = load_config(str(root))
    assert cfg3.thermal_via_arrays == []  # cached snapshot untouched
