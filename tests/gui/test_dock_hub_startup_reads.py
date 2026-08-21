#!/usr/bin/env python3
"""Regression test for the fix in
techdocs/handoff/plan_2026_08_15_config_read_cache_startup.md: a single
MainWindow/DockHub construction used to parse the SAME include: graph of
YAML files ~13 times and the same *.kicad_sch files 4+ times each, because
none of the four independent raw open()+parse call sites
(kicadstamp/config/includes.py's _resolve/_walk, config/loader.py's
load_config, config_writer.py's _read_data, sheet_names.py's
_parse_sheet_uuids) shared any cache. cProfile on a real project measured
yaml.safe_load() at 113 calls / 11.4s of a 15s MainWindow().

These tests count ACTUAL parses (loader invocations) per resolved path
across a real DockHub construction on a real, load_config()-valid multi-file
test project (no mocks of the config, no patching load_config) — the cache
must make each unique file parsed at most once, and the differential test
shows the same construction parses each file many times when the cache is
bypassed (the pre-fix behavior, reproduced in CI).
"""
import collections
import importlib
import logging
from pathlib import Path

from gui import settings
from gui.dock_hub import DockHub

# Every module with its own `cached_file_read` import binding (each consumer
# imports it directly at module level, so each needs its own counter wrapper —
# patching file_cache.cached_file_read itself would not affect these bindings).
# gui.yaml_io joined this set on 2026-08-21 (plan_2026_08_21_startup_graph_level_
# cache.md's "actual bottleneck" finding): it was the one raw reader the
# 2026-08-15 cache missed, so RootMetadataDock re-parsed the root YAML outside
# the cache. Now that it goes through cached_file_read too, it must be counted
# like every other reader or the root file's single parse is invisible here.
_CACHE_CONSUMER_MODULES = (
    "gui.yaml_io",
    "kicadstamp.config.includes",
    "kicadstamp.config.loader",
    "kicadstamp.config_writer",
    "kicadstamp.sheet_names",
)

# `components:` must be nested UNDER the cell key (4-space indent), same shape
# as tests/test_config_includes.py's MINIMAL_TEMPLATE — otherwise the cell is
# null and load_config fails validation before ever building the sheet map.
_MINIMAL_CELL = """    components:
      - role: {role}
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
"""


def _write_test_project(tmp_path):
    """A real, load_config()-valid multi-file project: root.yaml includes
    sub1.yaml/sub2.yaml and points schematic_dir at a directory of .kicad_sch
    files — the exact shape that makes every startup read path
    (walk_include_tree, collect_graph_files, read_data, collect_all_*,
    load_config) reach the SAME files over and over. Returns the root path."""
    (tmp_path / "sub1.yaml").write_text(
        "cells:\n  sub1_cell:\n" + _MINIMAL_CELL.format(role="R1"), encoding="utf-8")
    (tmp_path / "sub2.yaml").write_text(
        "cells:\n  sub2_cell:\n" + _MINIMAL_CELL.format(role="R2"), encoding="utf-8")

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    (sheets / "main.kicad_sch").write_text(
        '(kicad_sch (version 20230121)\n'
        '  (sheet (at 0 0) (size 100 100) (uuid "11111111-1111-1111-1111-111111111111")\n'
        '    (property "Sheetname" "Main")\n'
        '  )\n'
        ')\n', encoding="utf-8")
    (sheets / "sub.kicad_sch").write_text(
        '(kicad_sch (version 20230121)\n'
        '  (sheet (at 0 0) (size 100 100) (uuid "22222222-2222-2222-2222-222222222222")\n'
        '    (property "Sheetname" "Sub")\n'
        '  )\n'
        ')\n', encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text(
        "layer: F.Cu\n"
        "rules: []\n"
        "cells: {}\n"
        "points: {}\n"
        "clone_placements: []\n"
        "thermal_via_arrays: []\n"
        "include:\n"
        "  - sub1.yaml\n"
        "  - sub2.yaml\n"
        "schematic_dir: sheets\n", encoding="utf-8")
    return root


def _seed_last_root_file(root) -> None:
    """Point the (per-test-isolated, see tests/gui/conftest.py's
    isolated_settings) gui_state.json at `root` so RootMetadataDock's
    _restore_last_root() picks the project up during DockHub construction —
    without a restored root there is no graph to walk and the test would
    measure nothing."""
    data = settings.load()
    data["last_root_file"] = str(root)
    settings.save(data)


def _install_read_counters(monkeypatch, use_real_cache: bool = True):
    """Wrap cached_file_read in every consumer module with counters that
    record, per resolved path:
      - requests: how many times the cache was ASKED to read that file;
      - parses:    how many times the underlying loader actually RAN (i.e.
                   real disk reads/parses — a cache hit is a request that
                   never reaches the loader).
    use_real_cache=True keeps the counters in FRONT of the real cache (the
    normal production path); False bypasses it entirely (every request is a
    parse) — used by the differential test to show the pre-fix redundancy.
    Returns (requests, parses) counters keyed by str(resolved_path)."""
    requests = collections.Counter()
    parses = collections.Counter()

    for mod_name in _CACHE_CONSUMER_MODULES:
        mod = importlib.import_module(mod_name)
        real = mod.cached_file_read

        def _make_wrapper(real_fn, use_real_cache):
            def wrapper(path, loader):
                resolved = str(Path(path).resolve())
                requests[resolved] += 1

                def counting_loader(p):
                    parses[resolved] += 1
                    return loader(p)

                if use_real_cache:
                    return real_fn(path, counting_loader)
                return counting_loader(path)

            return wrapper

        monkeypatch.setattr(mod, "cached_file_read", _make_wrapper(real, use_real_cache))

    return requests, parses


def _teardown_hub(hub):
    """Same leak-close as tests/gui/test_phase3_wiring.py's _teardown_hub: a
    standalone DockHub embeds a real fieldstool MainWindow and a LogDock
    (root-logger handler) that would otherwise leak across tests; the
    log_file: FileHandler additionally holds an open file handle into tmp_path
    that blocks deletion on Windows."""
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def test_dock_hub_startup_reads_each_unique_file_at_most_once(tmp_path, qapp, main_window, monkeypatch):
    """The core regression: one DockHub construction (the heart of
    MainWindow.__init__) must not parse each project file 6-13 times any
    more. Every unique YAML/.kicad_sch file is now parsed exactly once, no
    matter how many of the many startup paths request it — and the cache is
    genuinely being hit (root.yaml alone is requested many more times than it
    is parsed)."""
    root = _write_test_project(tmp_path)
    _seed_last_root_file(root)

    requests, parses = _install_read_counters(monkeypatch)
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        expected = {
            str(p.resolve())
            for p in (root, tmp_path / "sub1.yaml", tmp_path / "sub2.yaml",
                      tmp_path / "sheets" / "main.kicad_sch",
                      tmp_path / "sheets" / "sub.kicad_sch")
        }
        # every project file was actually parsed at least once:
        assert expected <= set(parses), \
            f"project files not parsed at all: {expected - set(parses)}"
        # ...and each one exactly once (the whole point of the cache):
        for p in expected:
            assert parses[p] == 1, f"{Path(p).name} parsed {parses[p]}x, want 1x"
        # no OTHER file slipped in with a multi-parse either (<=2 for any
        # legitimate surprise, per the plan's "at most one or two times"):
        assert all(n <= 2 for n in parses.values()), f"some file parsed >2x: {parses}"
        # the cache is actually absorbing repeated requests: root.yaml alone is
        # requested by every graph walk and every dock's load_config, so the
        # request count must far exceed its single parse.
        assert requests[str(root.resolve())] > 2
        assert requests[str(root.resolve())] > parses[str(root.resolve())]
    finally:
        _teardown_hub(hub)


def test_dock_hub_startup_runs_graph_bodies_at_most_twice(tmp_path, qapp, main_window, monkeypatch):
    """The graph-level result cache (2026-08-21, plan_2026_08_21_startup_graph_
    level_cache.md): during ONE DockHub construction the EXPENSIVE bodies of
    load_config()/walk_include_tree() — counted on their internal "uncached"
    versions — must each run at most 1-2 times, not the pre-fix 6 / 11 times
    measured on the real project (see diagnostics/count_startup_graph_calls.
    py). The cached public wrappers are still called once per dock; only the
    real traversal/merge/validation collapses to a single run per changed
    graph."""
    import kicadstamp.config.includes as includes_mod
    import kicadstamp.config.loader as loader_mod

    root = _write_test_project(tmp_path)
    _seed_last_root_file(root)

    load_calls = []
    walk_calls = []
    real_load = loader_mod._load_config_uncached
    real_walk = includes_mod._walk_include_tree_uncached

    def counting_load(p):
        load_calls.append(p)
        return real_load(p)

    def counting_walk(p):
        walk_calls.append(p)
        return real_walk(p)

    monkeypatch.setattr(loader_mod, "_load_config_uncached", counting_load)
    monkeypatch.setattr(includes_mod, "_walk_include_tree_uncached", counting_walk)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert len(load_calls) <= 2, f"load_config body ran {len(load_calls)}x"
        assert len(walk_calls) <= 2, f"walk_include_tree body ran {len(walk_calls)}x"
    finally:
        _teardown_hub(hub)


def test_without_cache_same_startup_parses_each_file_many_times(tmp_path, qapp, main_window, monkeypatch):
    """Differential proof that the at-most-once bound above comes FROM THE
    CACHE, not from some happy accident of the test project's shape: with
    cached_file_read bypassed (every request goes straight to the loader), the
    SAME DockHub construction parses each YAML file many times — the 6-13x
    redundancy the plan measured, reproduced deterministically in CI."""
    root = _write_test_project(tmp_path)
    _seed_last_root_file(root)

    requests, parses = _install_read_counters(monkeypatch, use_real_cache=False)
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        for p in (root, tmp_path / "sub1.yaml", tmp_path / "sub2.yaml"):
            assert parses[str(p.resolve())] > 2, \
                f"{p.name} parsed only {parses[str(p.resolve())]}x without the cache"
        # bypassed cache means every request really re-parses:
        assert parses == requests
    finally:
        _teardown_hub(hub)
