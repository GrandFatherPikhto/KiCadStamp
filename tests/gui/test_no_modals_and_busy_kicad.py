# tests/gui/test_no_modals_and_busy_kicad.py
"""
"Not connected" is a Log line, never a modal — plan
techdocs/handoff/deepseek/plan/plan_2026_09_11_no_modals_and_busy_kicad.md X.1.

Denis's rule ("диалоговые окошки с ошибками — это просто ппц. Если нет
подключения, это надо в логе красным отметить"): an error dialog is the wrong
answer to "KiCad is not connected". The Log dock already renders ERROR records
in red (gui/docks/log_panel.py), so every connection / board-STATE failure goes
there — EXACTLY one line — while the operation is still refused. Only form
VALIDATION (a direct answer to what the user just typed or clicked in a field)
keeps its dialog (X.1.3).

This module covers the call sites whose own test module would otherwise have to
grow a second concern:
  * DockHub's two Scheme-List flows (Record... / Re-source...);
  * TreesDock's "Reread current position" context action;
  * the X.1.3 REGRESSION set — the six call sites that already went through
    _show_message (3x CellDock, 1x NetTraceDock here; the two SchemeListForm
    Widget ones are asserted in tests/gui/test_scheme_list.py) keep behaving
    exactly as before;
  * the node form's offline hint, which is a FORM hint and NOT an error
    (X.1.3 — "showing the STORED values" must stay, and must NOT log).

The remaining converted sites assert the same rule in their own modules
(test_placer_read_position, test_rules_read_position, test_fieldstool_window,
test_scheme_list, test_trees_dock).
"""
import logging

from PyQt6.QtWidgets import QMessageBox

import gui.dock_hub as dock_hub_mod
import gui.docks.cell_editor as cell_editor_mod
import gui.docks.trees_dock as trees_dock_mod
from gui.docks.cell_editor import CellDock
from gui.docks.net_trace import NetTraceDock
from gui.docks.trees_dock import TreesDock, _NodeDialog

from kicadstamp.config.sexp_format import dict_to_sexp

# A minimal root config with one tree whose single node is a "clone" (never
# resolved against the config here — the dock is only driven up to its
# connection guard).
TREES_CFG = {
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "R1", "kind": "clone", "xy": [5.0, 2.0]}]},
    ],
}


def _no_boxes(*a, **k):
    """Stand-in for QMessageBox.warning that FAILS the test if called — the
    strongest form of "this state error must not open a dialog"."""
    raise AssertionError("a connection-state error must not open a QMessageBox")


def _errors(caplog):
    return [r for r in caplog.records if r.levelno == logging.ERROR]


# ── DockHub — Record... / Re-source... (X.1.2, lines 477 / 1375) ───────────


def test_record_scheme_list_without_connection_logs_one_error(
        real_main_window, tmp_path, monkeypatch, caplog):
    """Tools -> Scheme Lists -> Record... without a live board: ONE ERROR line
    in the Log, no modal, and the Record dialog is never even constructed (the
    flow stops right there — X.1.4)."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    real_main_window.root_metadata_dock.set_root_file(root)

    monkeypatch.setattr(dock_hub_mod.QMessageBox, "warning", _no_boxes)
    constructed = []
    monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                        lambda *a, **k: constructed.append(True) or object())
    caplog.clear()

    real_main_window._dock_hub.record_scheme_list()

    errors = _errors(caplog)
    assert len(errors) == 1
    assert "Connect to KiCad first." in errors[0].message
    assert constructed == []


def test_resource_scheme_list_without_connection_logs_one_error(
        real_main_window, tmp_path, monkeypatch, caplog):
    """The Re-source... twin of the flow above (X.1.2) — same one ERROR line,
    no modal, no dialog, nothing captured."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    real_main_window.root_metadata_dock.set_root_file(root)

    monkeypatch.setattr(dock_hub_mod.QMessageBox, "warning", _no_boxes)
    constructed = []
    monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                        lambda *a, **k: constructed.append(True) or object())
    caplog.clear()

    real_main_window._dock_hub._run_resource_scheme_list({"name": "rec"}, root)

    errors = _errors(caplog)
    assert len(errors) == 1
    assert "Connect to KiCad first." in errors[0].message
    assert constructed == []


# ── TreesDock — "Reread current position" (X.1.2, line 2007) ───────────────


def test_reread_node_without_connection_logs_one_error(
        main_window, tmp_path, monkeypatch, caplog):
    """The node context action "Reread current position" without a live board:
    ONE ERROR line, no modal, and the node keeps its stored values (X.1.4 —
    the operation still stops)."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(TREES_CFG), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    tree = dock._trees[0]
    node = tree.nodes[0]
    before = (node.xy, node.polar, node.rotation)

    monkeypatch.setattr(trees_dock_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()

    dock._reread_node_flow(tree, node)

    errors = _errors(caplog)
    assert len(errors) == 1
    assert "No live board connection" in errors[0].message
    assert (node.xy, node.polar, node.rotation) == before
    assert dock._dirty is False


# ── X.1.3 regression: the six places that were ALREADY correct ─────────────


def test_cell_editor_live_flows_without_connection_log_one_error_each(
        main_window, tmp_path, monkeypatch, caplog):
    """The three CellDock flows that already reported "Connect to KiCad first."
    through _show_message (refresh geometry / import vias-tracks / select
    cluster on the board) keep doing exactly that — one ERROR line each, no
    modal. Guards the X.1.3 claim that these six places are NOT touched by the
    conversion."""
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    dock = CellDock(main_window)
    dock.set_root_path(target)

    monkeypatch.setattr(cell_editor_mod.QMessageBox, "warning", _no_boxes)

    for handler in (dock._on_refresh_geometry, dock._on_import_vias_tracks,
                    dock._on_select_cluster_on_board):
        caplog.clear()
        handler()
        errors = _errors(caplog)
        assert len(errors) == 1, handler.__name__
        assert "Connect to KiCad first." in errors[0].message


def test_net_trace_extract_without_connection_logs_one_error(
        main_window, tmp_path, monkeypatch, caplog):
    """NetTraceDock's Extract is the fourth of the X.1.3 set — same contract:
    one ERROR line, never a modal, nothing written. NetTraceDock never imported
    QMessageBox at all (it was converted long ago), so the "no modal" guard is
    patched at the class itself — ANY dialog opened anywhere in the process
    during this test fails it."""
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp({}), encoding="utf-8")
    dock = NetTraceDock(main_window)
    dock.set_root_path(target)

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_no_boxes))
    caplog.clear()

    dock._on_extract()

    errors = _errors(caplog)
    assert len(errors) == 1
    assert "Connect to KiCad first." in errors[0].message
    assert dock._active_op is None  # nothing was dispatched


# ── X.1.3: the offline hint of the node form is NOT an error ───────────────


def test_node_form_offline_hint_stays_and_is_not_logged(
        main_window, tmp_path, monkeypatch, caplog):
    """The node form's offline notice ("No live board connection — showing the
    STORED values ...", X.1.3) is a FORM hint rendered next to the disabled
    fields — it must stay exactly as it was and must NOT be routed to the Log.
    An offline open+save is a legal, explicitly supported workflow."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(TREES_CFG), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    tree = dock._trees[0]

    monkeypatch.setattr(trees_dock_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()

    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree, parent_node=None,
                      existing=tree.nodes[0])

    hint = dlg.offset_frame_label.text()
    assert "STORED values" in hint
    assert dlg.offset_frame_label.isVisible() or not dlg.isVisible()
    assert _errors(caplog) == []
    # The stored values are what the form shows (never numbers in a frame the
    # user cannot verify) and they are not editable until KiCad is connected.
    assert dlg.offset_widget.isEnabled() is False
    assert dlg.rotation_edit.isEnabled() is False
