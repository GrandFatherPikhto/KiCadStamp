# tests/gui/docks/test_cell_anchor_view.py
"""Tests for gui/docks/cell_anchor_view.py — the CELL page's ONE remaining tab.

Since step 4 of plan_2026_10_09_entity_page this page keeps ONLY the "Source"
tab: the placement identity (task V), the working (Cluster, Sheet) context and
the identification of ONE instance. The Role/Marker anchor tabs, the live-cluster
frame and the overlay workers MOVED to gui/entity/anchor_tab.py and are guarded
by tests/gui/docks/test_anchor_tab.py.

What is pinned HERE: ONE tab, the working-context combos (the Sheet narrows the
Cluster list, G.2/G.3), the top-level clone_placements identity (Name/Comment
write the RECORD, never a tree-materialized placement), and that a cell selection
opens this page.

Headless and board-mutation-free (same reasoning as test_cell_editor.py).
"""
from types import SimpleNamespace

import pytest

from gui.docks.cell_anchor_view import CellAnchorView
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2
from kicadstamp.explore import Selected


# ── Widget: the working context + identity ────────────────────────────────

def _cell_data():
    return {"cells": {
        "cell1": {
            "layer": "F.Cu",
            "components": [
                {"role": "C1", "offset_along_mm": 1.0, "offset_across_mm": -2.0},
                {"role": "C2", "offset_along_mm": 3.0, "offset_across_mm": -4.0},
            ],
            "vias": [],
            "tracks": [],
            "clone_placements": [],
        }
    }}


def _make_view(main_window, tmp_path, data=None):
    target = tmp_path / "root.sexp"
    target.write_text(
        dict_to_sexp(data if data is not None else _cell_data(), format_number=2),
        encoding="utf-8")
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(target)
    view.load_entry("cell1", target)
    return view, target


def test_the_cell_page_now_carries_source_only(main_window, tmp_path):
    """Step 4 of plan_2026_10_09_entity_page: the Role/Marker anchor tabs MOVED
    to the ENTITY page, so this page has ONE tab left — Source. (The Refs tab
    left in step 3.) Their new home is guarded by test_anchor_tab.py and
    test_entity_page_refs_tab.py."""
    view, _ = _make_view(main_window, tmp_path)
    titles = [view._tabs.tabText(i) for i in range(view._tabs.count())]
    assert titles == ["Source"]
    assert "Refs" not in titles
    assert "Role anchor" not in titles
    assert "Marker anchor" not in titles


# ── G.2: the Sheet narrows the Cluster list ───────────────────────────────

def _sel_on_sheet(ref, cluster, sheet_uuid, role=None):
    """A Selected whose footprint resolves to ONE sheet segment via sheet_uuid.
    `.sheet` is left DEGENERATE (all None) on purpose — that is what a live Board
    produces, and what snapshot_with_resolved_sheets has to fix."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu",
                   sheet_path_uuids=(sheet_uuid, "comp"))
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[None],
                    nets={}, fp=fp)


def _feed_snapshot(view, sheet_names):
    view._sheet_names = dict(sheet_names)
    view.refresh_known_roles([
        _sel_on_sheet("R1", "PIF_3V3_VDD", "mcu"),
        _sel_on_sheet("R2", "FPGA", "fpga"),
    ])


def _clusters(view):
    return [view._cluster_combo.itemText(i)
            for i in range(view._cluster_combo.count())]


def test_sheet_narrows_the_cluster_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    assert _clusters(view) == ["FPGA", "PIF_3V3_VDD"]

    view._sheet_combo.setCurrentText("MCU")     # fires _on_sheet_changed
    assert _clusters(view) == ["PIF_3V3_VDD"]


def test_degenerate_live_sheets_are_re_resolved(main_window, tmp_path):
    """THE G.2 trap: a live snapshot's .sheet is a list of None. After the
    snapshot_with_resolved_sheets rebuild the stored snapshot must carry the
    config-based names."""
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})

    resolved = {tuple(s.sheet) for s in view._resolved_snapshot}
    assert resolved == {("MCU",), ("FPGA",)}     # NOT {(None,), (None,)}


def test_sheet_that_does_not_reduce_keeps_the_full_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    view._sheet_names = {"mcu": "MCU"}
    view.refresh_known_roles([
        _sel_on_sheet("R1", "PIF_3V3_VDD", "mcu"),
        _sel_on_sheet("R2", "PIF_AVDD", "mcu"),
    ])

    view._sheet_combo.setCurrentText("MCU")     # both clusters on MCU

    assert _clusters(view) == ["PIF_3V3_VDD", "PIF_AVDD"]


def test_empty_sheet_restores_the_full_list(main_window, tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    view._sheet_combo.setCurrentText("MCU")
    assert _clusters(view) == ["PIF_3V3_VDD"]

    view._sheet_combo.setCurrentText("")
    assert _clusters(view) == ["FPGA", "PIF_3V3_VDD"]


def test_selected_cluster_survives_narrowing_when_it_matches(main_window,
                                                             tmp_path):
    view, _ = _make_view(main_window, tmp_path)
    _feed_snapshot(view, {"mcu": "MCU", "fpga": "FPGA"})
    view._cluster_combo.setCurrentText("PIF_3V3_VDD")

    view._sheet_combo.setCurrentText("MCU")     # PIF_3V3_VDD IS on MCU

    assert view._cluster_combo.currentText() == "PIF_3V3_VDD"


# ── Task V: the placement identity (Name/Comment) ─────────────────────────

def _view_with_placements(main_window, tmp_path, placements):
    data = {"cells": {"cell1": {
        "layer": "F.Cu",
        "components": [{"role": "C1", "offset_along_mm": 0.0,
                        "offset_across_mm": 0.0}],
    }}, "clone_placements": placements}
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.set_root_path(target)
    view.load_entry("cell1", target)
    view._cluster_combo.setCurrentText("CL1")
    return view, target


def test_identity_block_is_the_shared_widget_in_both_pages(real_main_window):
    """One class, two places — the duplication is what desynchronised the two
    cell pages this task merges."""
    from gui.docks._cell_identity import CellIdentityWidget
    hub = real_main_window._dock_hub
    for owner in (hub.cell_anchor_view._identity, hub.placer_dock._name_row):
        assert isinstance(owner, CellIdentityWidget)
    assert (type(hub.cell_anchor_view._identity)
            is type(hub.placer_dock._name_row))


def test_identity_loads_from_the_top_level_record(main_window, tmp_path):
    view, _ = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1",
          "comment": "hi"}])

    assert view._name_edit.text() == "cell1"
    assert view._comment_edit.text() == "hi"
    assert view._name_edit.isReadOnly() is False
    assert view._comment_edit.isReadOnly() is False


def test_tree_placement_identity_is_read_only_and_never_saved(main_window,
                                                              tmp_path):
    """THE mine: with no top-level clone_placements record (the usual case — all
    real placements materialize from trees) Name/Comment must be read-only and
    committing must write NOTHING, never a duplicate entry."""
    view, target = _view_with_placements(main_window, tmp_path, [])

    assert view._name_edit.isReadOnly() is True
    assert view._comment_edit.isReadOnly() is True
    view._name_edit.setText("sneaky")
    view._comment_edit.setText("sneaky")
    view._on_identity_edited()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    assert not data.get("clone_placements")


def test_identity_edit_writes_back_into_the_top_level_record(main_window,
                                                             tmp_path):
    view, target = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1"}])

    view._name_edit.setText("renamed")
    view._comment_edit.setText("new note")
    view._on_identity_edited()

    data = sexp_to_dict(target.read_text(encoding="utf-8"))
    items = data.get("clone_placements")
    assert len(items) == 1                      # updated in place, no duplicate
    assert items[0]["name"] == "renamed"
    assert items[0]["comment"] == "new note"
    assert items[0]["cell"] == "cell1"


def test_identity_works_without_a_board(main_window, tmp_path):
    """Acceptance (Phase C, preserved by task V): the page works with
    connection.board = None."""
    assert main_window.connection.board is None
    view, _ = _view_with_placements(
        main_window, tmp_path,
        [{"name": "cell1", "cell": "cell1", "cluster": "CL1"}])
    assert view._name_edit.text() == "cell1"


def test_cell_selection_by_keyboard_opens_the_cell_page(real_main_window,
                                                        tmp_path):
    """Task V + G.5: moving the CURRENT item (what an arrow key does) opens the
    cell page for the picked cell — not the Placer."""
    from PyQt6.QtWidgets import QApplication
    hub = real_main_window._dock_hub
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp({"cells": {
        "A": {"components": [{"role": "C1", "offset_along_mm": 0.0,
                              "offset_across_mm": 0.0}]},
        "B": {"components": [{"role": "C1", "offset_along_mm": 0.0,
                              "offset_across_mm": 0.0}]},
    }}, format_number=2), encoding="utf-8")
    hub.config_tree_dock.set_root_file(target)
    hub.cell_anchor_view.set_root_path(target)

    top = hub.config_tree_dock.tree.topLevelItem(0)
    cells = next(top.child(i) for i in range(top.childCount())
                 if top.child(i).text(0) == "Cells")
    leaf_b = next(cells.child(i) for i in range(cells.childCount())
                  if cells.child(i).text(0) == "B")
    hub.config_tree_dock.tree.setCurrentItem(leaf_b)
    QApplication.processEvents()

    assert hub.config_tree_dock.current_right_page() is hub.cell_anchor_view
    assert hub.cell_anchor_view._cell_name == "B"


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 (class (в)): the format-3 writer resolves a reference's UUID against
    the ACTIVE GRAPH ROOT. These cells write a self-contained config."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)
