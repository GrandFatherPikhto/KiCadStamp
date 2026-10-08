#!/usr/bin/env python3
# tests/gui/test_dock_hub_graph_changed_reads.py
"""Regression cells for the config-graph READS of ONE UI update after a write.

Part Д3′ of ``techdocs/handoff/deepseek/plan/plan_2026_10_08_narrowing_net_traces_cost.md``
(cell **С7**): step 1 of the plan measured these reads with a hand-made copy of
the post-write update (``refresh()`` + ``emit()`` written by the probe), which is
not a watcher. These cells drive the PRODUCT's own path — the tree re-reading
itself through ``ConfigTreeDock.refresh()`` **plus the real ``graph_changed``
signal**, wired by DockHub to ``_refresh_graph_dependent_choices``
(``gui/dock_hub.py``) — and pin:

* the WORK of one update: at most ONE real ``load_config`` rebuild and at most
  ONE real ``walk_include_tree`` traversal. ``stage_write`` drops the graph entry
  once, so exactly the first read of each kind rebuilds and the rest are hits —
  a future SECOND rebuild (or a skipped first one) turns red here;
* Д2′: at most one ``upgrade on disk skipped`` INFO line per kind;
* FRESHNESS: an edit staged in the working set is visible to the NEXT read —
  the ``invalidate_graph_path`` call in ``stage_write`` exists for it.

The graph bodies are counted on their ``_uncached`` versions: the cached PUBLIC
wrappers are called many more times (ventilation), and counting them would repeat
the step-1 mistake (a public NAME is not a traversal).

``tests/gui/test_dock_hub_startup_reads.py`` measures the STARTUP only and is
deliberately not touched.
"""
import copy
import logging
from pathlib import Path

from gui.dock_hub import DockHub

# The two whole-graph bodies whose ACTUAL execution is the work under test.
import kicadstamp.config.includes as includes_mod
import kicadstamp.config.loader as loader_mod
from kicadstamp.config_writer import read_data as _read_data


def _cell(role: str) -> dict:
    """A minimal but load_config()-valid ``cells:`` entry (components non-empty
    or the cell is null and the loader refuses before any graph read)."""
    return {"components": [{"role": role, "offset_along_mm": 0.0,
                            "offset_across_mm": 0.0, "angle_deg": 0.0}]}


def _write_project(tmp_path) -> Path:
    """A real, load_config()-valid two-file project: root.sexp includes sub.sexp.

    Written ALREADY CURRENT (format 3 mints its UUIDs) so opening it never LIFTS
    a file on disk — a lift is a write, and it would invalidate the very cache
    entry the update is about to read."""
    from kicadstamp.config.format_version import current_format
    from kicadstamp.config.sexp_format import dict_to_sexp
    from tests.fakes.format3 import mint_format3_files

    data = {
        "sub.sexp": {"cells": {"sub_cell": _cell("R2")}},
        "root.sexp": {"cells": {"root_cell": _cell("R1")}, "points": {},
                      "include": ["sub.sexp"]},
    }
    fmt = current_format()
    if fmt >= 3:
        data = mint_format3_files(data)
    for name, body in data.items():
        (tmp_path / name).write_text(
            dict_to_sexp(body, format_number=fmt), encoding="utf-8")
    return tmp_path / "root.sexp"


def _seed_last_root(root: Path) -> None:
    """Point the (per-test isolated) gui_state.json at `root` so DockHub's
    RootMetadataDock restore picks the project up during construction."""
    from gui import settings
    settings.state.set("last_root_file", str(root))


def _teardown(hub: DockHub) -> None:
    """Close the two root-logger handlers a standalone DockHub embeds — the same
    leak-close as tests/gui/test_dock_hub_startup_reads.py's _teardown_hub."""
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def _counting(real, into: list):
    """Record every ACTUAL run of a graph body: a cache MISS is exactly the body
    executing (a hit never reaches it)."""
    def _wrapped(path):
        into.append(path)
        return real(path)
    return _wrapped


def test_one_update_after_a_write_rebuilds_the_graph_at_most_once(
        tmp_path, qapp, main_window, monkeypatch, caplog):
    """С7 (Д3′): the reads of ONE post-write update, driven through the REAL
    ``graph_changed`` broadcast. After a staged write the graph cache is dropped
    once, so exactly the first read of each kind rebuilds — a second rebuild
    fails here."""
    from kicadstamp.config_working_set import WORKING_SET

    root = _write_project(tmp_path)
    _seed_last_root(root)
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        rebuilds_load: list = []
        rebuilds_walk: list = []
        monkeypatch.setattr(loader_mod, "_load_config_uncached",
                            _counting(loader_mod._load_config_uncached, rebuilds_load))
        monkeypatch.setattr(
            includes_mod, "_walk_include_tree_uncached",
            _counting(includes_mod._walk_include_tree_uncached, rebuilds_walk))

        # A dock edit: the change is staged into the working set, which drops the
        # graph-cache entry for the edited root.
        monkeypatch.setattr(WORKING_SET, "enabled", True)
        data = copy.deepcopy(_read_data(root))
        data["cells"]["root_cell"]["components"][0]["role"] = "R9"
        WORKING_SET.stage_write(root, data)

        rebuilds_load.clear()
        rebuilds_walk.clear()
        caplog.clear()

        # The PRODUCT's update path: the tree re-reads itself, then the REAL
        # signal wakes every other dock through DockHub's own connection.
        hub.config_tree_dock.refresh()
        hub.config_tree_dock.graph_changed.emit()

        assert len(rebuilds_load) >= 1, (
            "the real graph_changed -> _refresh_graph_dependent_choices path "
            "read the graph not at all — the watcher would prove nothing")
        assert len(rebuilds_load) <= 1, f"load_config rebuilt {len(rebuilds_load)}x"
        assert len(rebuilds_walk) <= 1, f"walk_include_tree rebuilt {len(rebuilds_walk)}x"

        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert sum("format upgrade on disk skipped" in m for m in infos) <= 1, infos
        assert sum("registry schema upgrade on disk skipped" in m for m in infos) <= 1, infos
    finally:
        _teardown(hub)


def test_an_edit_in_the_working_set_is_visible_to_the_next_read(
        tmp_path, qapp, main_window, monkeypatch):
    """С7 (Д3′) freshness: the NEXT read of the graph must see the staged edit.
    ``stage_write`` drops the edited path's graph-cache entry; remove that call
    and the next read hands back the PRE-edit Config — the mutant this cell
    exists for."""
    from kicadstamp.config.loader import load_config
    from kicadstamp.config_working_set import WORKING_SET

    root = _write_project(tmp_path)
    _seed_last_root(root)
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        cfg, _ = load_config(str(root))       # prime the graph cache from disk
        assert cfg.cells["root_cell"].components[0].role == "R1"

        monkeypatch.setattr(WORKING_SET, "enabled", True)
        data = copy.deepcopy(_read_data(root))
        data["cells"]["root_cell"]["components"][0]["role"] = "R9"
        WORKING_SET.stage_write(root, data)   # drops the graph entry

        cfg2, _ = load_config(str(root))      # the next read
        assert cfg2.cells["root_cell"].components[0].role == "R9", (
            "the staged edit is invisible to the next read — stage_write stopped "
            "dropping the graph cache")
    finally:
        _teardown(hub)


if __name__ == "__main__":  # pragma: no cover
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
