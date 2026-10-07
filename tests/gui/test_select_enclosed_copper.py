# tests/gui/test_select_enclosed_copper.py
"""«Select enclosed copper» — MENU, WORKER and the board DOOR (plan
``plan_2026_10_05_select_enclosed_copper.md``; Denis 2026-10-05).

Three layers, one property each:

* the MENU: the item exists on a Cells leaf AND on an Entities leaf (found by its
  ``objectName``, never by the translated label) and it emits the right
  (name, file_path, cluster, sheet);
* the WORKER: components AND copper leave in ONE ``select_items`` call, through
  its own adapter, which is closed on the way out;
* the DOOR: on a REAL ``BoardConnection`` with the UI-thread predicate ARMED in
  ``raise`` mode, running the item reads ``connection.board`` on NO UI-thread
  path (the whole flow runs inline here, so even the worker code is on the UI
  thread — a stricter place than production, where it runs on its own thread).

The board stand-in is ``tests/explode/board.py`` (same DTOs as production), and a
recording adapter catches the selection — the real geometry is covered by
``tests/explode/test_enclosed_copper.py``.
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

import gui.select_enclosed_copper as sec_mod
import gui.worker as worker_mod
import kicadstamp.adapter_factory as adapter_factory_mod
import kicadstamp.config as config_mod

from kicadstamp.enclosed_copper import MIN_INSTANCE_PADS
from kicadstamp.i18n import _

from tests.explode.board import F_CU, fp, pad, track
from tests.gui.create_entity_helpers import (
    category,
    context_menu_actions,
    file_item,
    find_child,
    open_project,
    write_config,
)


def _uuid(item) -> object:
    return getattr(item, "uuid", None)


class _RecordingAdapter:
    """A stand-in adapter that records the ONE selection it was handed."""

    def __init__(self, fps, pads, tracks=(), vias=()):
        self._fps = list(fps)
        self._pads = dict(pads)
        self._tracks = list(tracks)
        self._vias = list(vias)
        self.calls: list = []
        self.closed = False

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, footprint, field_name):
        return getattr(footprint, "_fields", {}).get(field_name)

    def get_footprint_pads(self, footprint):
        return list(self._pads.get(footprint.uuid, ()))

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def refresh_board(self):
        pass

    def select_items(self, items):
        self.calls.append(list(items))

    def close(self):
        self.closed = True


def _instance_board():
    """R1 and R2 of cluster FPGA joined by one track (their own copper)."""
    fps = [fp("u1", "R1", "FPGA", 0.0, 0.0), fp("u2", "R2", "FPGA", 5.0, 0.0)]
    pads = {"u1": [pad("1", 0.0, 0.0)], "u2": [pad("1", 5.0, 0.0)]}
    tracks = [track("t12", F_CU, 0.0, 0.0, 5.0, 0.0)]
    return _RecordingAdapter(fps, pads, tracks)


# ── M1 — the Cells leaf carries the item and emits its (name, file, None, None) ─

def test_cells_leaf_has_the_item_and_emits_the_rule_instance(
        real_main_window, tmp_path, monkeypatch):
    """M1: on a Cells leaf there is exactly ONE ``select_enclosed_copper_action``
    (objectName, not a translated label) and triggering it emits
    ``cell_select_enclosed_requested(name, file_path, None, None)`` — the
    instance is resolved downstream by the shared "Select cell" rule."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    # The cell carries an entity (3б): an ENTITYLESS cell is a read-only
    # drawing whose menu keeps only "Create entity" + "Delete…", so the paid
    # select item lives on a cell with an entity.
    write_config(root, {"cells": {"dac_buf": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "dac0", "cell": "dac_buf"}]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(find_child(tree.topLevelItem(0), "Cells"), "dac_buf")

    actions = context_menu_actions(hub.config_tree_dock, leaf, monkeypatch)
    matching = [act for _label, act in actions
                if act.objectName() == "select_enclosed_copper_action"]
    assert len(matching) == 1, (
        "the Cells menu must carry exactly one select_enclosed_copper_action; "
        "seen: " + repr([label for label, _ in actions]))

    emitted = []
    hub.config_tree_dock.cell_select_enclosed_requested.connect(
        lambda n, f, c, s: emitted.append((n, c, s)))
    matching[0].trigger()
    assert emitted and emitted[0] == ("dac_buf", None, None), emitted


# ── M2 — the Entities leaf sends its OWN explicit (cluster, sheet) ────────────

def test_entities_leaf_has_the_item_and_sends_its_explicit_instance(
        real_main_window, tmp_path, monkeypatch):
    """M2: on an Entities leaf the item sends the entity's (cluster, sheet) and
    file_path=None (the entity's file must never become the cell's file)."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {
        "cells": {"dac_buf": {"components": [{"role": "R"}]}},
        "entities": [{"name": "dac0", "cell": "dac_buf",
                      "cluster": "DAC_BUF", "sheet": "Channel_0"}]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    # plan_2026_10_05_entities_under_cells (Р63): an entity is now a CHILD of
    # its cell, not a leaf of an "Entities" section — the menu and payload are
    # unchanged, only the node moved.
    cell_leaf = find_child(category(file_item(tree, root), "cells"), "dac_buf")
    leaf = find_child(cell_leaf, "dac0")

    actions = context_menu_actions(hub.config_tree_dock, leaf, monkeypatch)
    matching = [act for _label, act in actions
                if act.objectName() == "select_enclosed_copper_action"]
    assert len(matching) == 1, (
        "the Entities menu must carry exactly one select_enclosed_copper_action; "
        "seen: " + repr([label for label, _ in actions]))

    emitted = []
    hub.config_tree_dock.cell_select_enclosed_requested.connect(
        lambda n, f, c, s: emitted.append((n, f, c, s)))
    matching[0].trigger()
    assert emitted == [("dac_buf", None, "DAC_BUF", "Channel_0")], emitted


# ── W1 — one select_items call for components AND copper ─────────────────────

def _worker_payload(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text("", encoding="utf-8")
    return {"root_path": str(root), "config_path": str(root),
            "timeout_ms": 5000, "cell_name": "fpga",
            "cluster": "FPGA", "sheet": None}


def test_worker_selects_components_and_copper_in_one_call(tmp_path, monkeypatch):
    """W1: the worker hands ``select_items`` the instance's components AND the
    taken copper in ONE call, and closes its own adapter. Mutation: "only copper
    goes into the selection" / "a second select_items call" die here."""
    adapter = _instance_board()
    monkeypatch.setattr(adapter_factory_mod, "create_board_adapter",
                        lambda **kwargs: adapter)
    monkeypatch.setattr(config_mod, "load_config",
                        lambda path: (SimpleNamespace(),
                                      SimpleNamespace(sheet_names={})))

    result = sec_mod.run_select_enclosed_copper_worker(_worker_payload(tmp_path))

    assert len(adapter.calls) == 1, "select_items must be called exactly once"
    assert {_uuid(i) for i in adapter.calls[0]} == {"u1", "u2", "t12"}
    assert result["components"] == 2 and result["pieces"] == 1
    assert result["dangling"] == 0 and result["pruned"] == 0
    assert adapter.closed is True


def test_worker_reports_an_empty_instance_without_selecting(tmp_path, monkeypatch):
    """W2: no live component for the instance is an honest ``empty`` answer —
    nothing is selected (red Log line handled by the reporter)."""
    adapter = _RecordingAdapter([], {})
    monkeypatch.setattr(adapter_factory_mod, "create_board_adapter",
                        lambda **kwargs: adapter)
    monkeypatch.setattr(config_mod, "load_config",
                        lambda path: (SimpleNamespace(),
                                      SimpleNamespace(sheet_names={})))

    result = sec_mod.run_select_enclosed_copper_worker(_worker_payload(tmp_path))

    assert result.get("empty") is True
    assert adapter.calls == []
    assert adapter.closed is True


# ── W3 — the reporter writes a LOG LINE, never a dialog ──────────────────────

def test_reporter_writes_the_selection_line(caplog):
    """W3: success is one Log line naming the counters."""
    with caplog.at_level(logging.INFO, logger=sec_mod.__name__):
        sec_mod.report_select_enclosed_copper(
            {"cell": "fpga", "cluster": "FPGA", "sheet": None,
             "components": 3, "pieces": 2, "foreign": 4,
             "dangling": 5, "pruned": 6})
    assert any("fpga" in r.message and "3" in r.message and "2" in r.message
               and "5" in r.message and "6" in r.message
               for r in caplog.records)


def test_reporter_writes_a_red_line_for_an_empty_instance(caplog):
    """W3-b: an empty instance is an ERROR record, not a dialog."""
    with caplog.at_level(logging.INFO, logger=sec_mod.__name__):
        sec_mod.report_select_enclosed_copper(
            {"empty": True, "cell": "fpga", "cluster": "FPGA", "sheet": None})
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and "fpga" in errors[0].message


# ── O1–O3 — the door orchestrator ────────────────────────────────────────────

class _Hub:
    def __init__(self, root, connected):
        self.root_metadata_dock = SimpleNamespace(root_path=root)
        self.main_window = SimpleNamespace(
            connection=SimpleNamespace(is_connected=connected))


def test_no_root_is_a_red_line_and_no_launch(caplog, monkeypatch):
    """O1: without a project root nothing is launched — a red Log line only."""
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a))
    with caplog.at_level(logging.INFO, logger=sec_mod.__name__):
        sec_mod.select_enclosed_copper(_Hub(None, True), "fpga",
                                       cluster="FPGA", sheet=None)
    assert launched == []
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_not_connected_is_a_red_line_and_no_launch(tmp_path, caplog, monkeypatch):
    """O2: not connected — nothing is launched, a red Log line only."""
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a))
    with caplog.at_level(logging.INFO, logger=sec_mod.__name__):
        sec_mod.select_enclosed_copper(_Hub(tmp_path, False), "fpga",
                                       cluster="FPGA", sheet=None)
    assert launched == []
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_launch_opens_the_exploded_gate_and_carries_the_instance(
        tmp_path, monkeypatch):
    """O3: the op is launched with ``allowed_while_exploded=True`` (the selection
    writes nothing) and the payload carries the explicit instance and the
    connection's timeout."""
    launched = []

    def _fake_start(connection, widgets, fn, on_success, on_error, *args, **kw):
        launched.append((fn, args, kw))

    monkeypatch.setattr(worker_mod, "start_long_op", _fake_start)
    sec_mod.select_enclosed_copper(_Hub(tmp_path, True), "fpga",
                                   cluster="FPGA", sheet="Channel_0")

    assert len(launched) == 1
    fn, args, kw = launched[0]
    assert fn is sec_mod.run_select_enclosed_copper_worker
    assert args[0]["cluster"] == "FPGA" and args[0]["sheet"] == "Channel_0"
    assert kw.get("allowed_while_exploded") is True
    assert kw.get("busy_text")


def test_missing_cluster_uses_the_shared_resolver_submenu(tmp_path, monkeypatch):
    """O4: with no explicit address the SAME resolver "Select cell" uses runs,
    and a "choose" answer opens the SHARED per-instance submenu (no third rule).
    Mutation: "the instance rule copied instead of taken from
    resolve_action_instance" dies here."""
    from gui.select_cell import InstanceChoice
    picked = []
    monkeypatch.setattr(
        "gui.select_cell.resolve_action_instance",
        lambda *a, **k: InstanceChoice("choose",
                                       candidates=(("A", None), ("B", None))))
    monkeypatch.setattr("gui.select_cell.pick_instance",
                        lambda parent, candidates, on_pick: picked.append(
                            tuple(candidates)))
    monkeypatch.setattr(worker_mod, "start_long_op", lambda *a, **k: None)

    sec_mod.select_enclosed_copper(_Hub(tmp_path, True), "fpga")
    assert picked == [(("A", None), ("B", None))]


# ── D1 — the door on a REAL BoardConnection, predicate in raise mode ─────────

def test_no_ui_thread_board_read_when_the_item_runs(real_main_window, tmp_path,
                                                    monkeypatch):
    """D1: on a REAL ``BoardConnection`` with the UI-thread predicate ARMED in
    ``raise`` mode, running the whole item (resolver + worker, both INLINE on the
    UI thread here) reads ``connection.board`` on NO path. Worker stubbed at the
    module and ``start_long_op`` made synchronous, so only the product code of
    this feature is on the hook."""
    from gui import connection as conn_mod
    from gui.connection import BoardConnection

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())   # setter; getter is guarded
    w.connection = conn
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"dac_buf": {"components": [{"role": "R"}]}}})
    hub = w._dock_hub
    hub.root_metadata_dock._path = root

    adapter = _instance_board()
    monkeypatch.setattr(adapter_factory_mod, "create_board_adapter",
                        lambda **kwargs: adapter)

    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        try:
            on_success(fn(*args))
        except Exception as e:  # noqa: BLE001 — mirror the worker's routing
            on_error(str(e))
        return None
    monkeypatch.setattr(worker_mod, "start_long_op", _sync)

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    hub._select_enclosed_copper_from_tree("dac_buf", None, "FPGA", None)

    assert len(adapter.calls) == 1
    assert {_uuid(i) for i in adapter.calls[0]} == {"u1", "u2", "t12"}
    assert adapter.closed is True


def test_the_action_is_neutral_about_the_exploded_gate():
    """The op is a selection, so the gate is opened for it (see O3) — this pins
    that the module imports nothing else that would close it: the ONE call to
    start_long_op is the only launch site."""
    import inspect
    src = inspect.getsource(sec_mod)
    assert src.count("start_long_op(") == 1
    assert MIN_INSTANCE_PADS == 2
