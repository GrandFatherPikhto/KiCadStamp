# tests/gui/test_entity_page.py
"""Tests for the Config right-QView Entity page (EntityInfoDock, 2026-09-05,
design config_qview_chain_entity_pages §5) — a read-mostly Entity RECORD
editor: "Справка" (Name read-only, Comment editable, Cell/Sheet/Cluster
read-only) + the clickable placements list (trees whose node.ref names this
Entity; today ≤1, designed for N)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import types

import pytest

import gui.docks.entity_page as entity_page_mod
from gui.docks.entity_page import EntityInfoDock
from kicadstamp.config.sexp_format import dict_to_sexp


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _make_dock(main_window, tmp_path, entities):
    root = tmp_path / "root.sexp"
    _write(root, {"entities": entities})
    dock = EntityInfoDock(main_window)
    dock.set_root_path(root)
    return dock, root


def _node(ref, kind="placement", name=None, children=None):
    return types.SimpleNamespace(
        ref=ref, kind=kind, name=name, children=children or [])


def _tree(name, nodes):
    return types.SimpleNamespace(name=name, nodes=nodes)


def test_load_entity_renders_record(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, [
        {"name": "E1", "cell": "c1", "cluster": "CL", "sheet": "S",
         "comment": "note"}])
    dock.load_entity("E1")

    assert dock.name_label.text() == "E1"
    assert dock.comment_edit.text() == "note"
    assert dock.cell_label.text() == "c1"
    assert dock.sheet_label.text() == "S"
    assert dock.cluster_label.text() == "CL"
    # A cell-based Entity has no Scheme List identity — the row stays hidden.
    assert dock.scheme_list_label.isVisibleTo(dock) is False
    # No trees -> unplaced.
    assert dock.placements_list.count() == 0
    assert "Not placed in any tree" in dock.placements_hint.text()


def test_missing_entity_clears_and_reports(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, [{"name": "E1", "cell": "c1"}])
    dock.load_entity("NOPE")
    assert dock.name_label.text() == "—"
    assert "not found" in dock._status_label.text()


def test_comment_commit_writes_and_emits_saved(main_window, tmp_path):
    from kicadstamp.config.sexp_format import sexp_to_dict
    dock, root = _make_dock(main_window, tmp_path, [
        {"name": "E1", "cell": "c1", "cluster": "CL"}])
    dock.load_entity("E1")
    dock.comment_edit.setText("  fresh note  ")
    fired = []
    dock.saved.connect(lambda: fired.append(True))
    dock._on_comment_commit()

    assert len(fired) == 1
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert data["entities"][0]["comment"] == "fresh note"


def test_placements_list_lists_trees_and_jumps(main_window, tmp_path, monkeypatch):
    dock, _ = _make_dock(main_window, tmp_path, [{"name": "E1", "cell": "c1"}])
    # A tree with one placement node referencing E1 (nested under a parent).
    parent = _node("P1", kind="module", name="module")
    parent.children = [_node("E1", kind="placement", name="E1")]
    monkeypatch.setattr(
        entity_page_mod, "load_config",
        lambda path: (types.SimpleNamespace(
            trees=[_tree("fpga", [parent]), _tree("other", [])]), None))

    dock.load_entity("E1")
    assert dock.placements_list.count() == 1
    assert "fpga" in dock.placements_list.item(0).text()

    jumped = []
    dock.open_tree.connect(jumped.append)
    dock._on_placement_clicked(dock.placements_list.item(0))
    assert jumped == ["fpga"]


def test_comment_field_never_writes_without_loaded_entity(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path, [{"name": "E1", "cell": "c1"}])
    dock.comment_edit.setText("x")
    dock._on_comment_commit()  # nothing loaded -> no write, no crash
    assert dock._entity_data == {}


def test_load_scheme_list_entity_shows_scheme_list_row(main_window, tmp_path):
    """P6 Stage 4 .cell audit — a scheme_list-based Entity (cell=None) must not
    render as a misleading blank "Cell: —": the dedicated Scheme List row shows
    the recorded snapshot it clones, and the comment write path preserves the
    record (never rewrites it into a cell-less Entity)."""
    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [{
            "name": "psu", "anchor_ref": "R1", "source_sheet": "Channel_0",
            "anchor_rotation_deg": 0.0,
            "components": [
                {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                 "rotation_deg": 0.0},
            ],
        }],
        "entities": [{"name": "S1", "scheme_list": "psu", "sheet": "Channel_1"}],
    })
    dock = EntityInfoDock(main_window)
    dock.set_root_path(root)
    dock.load_entity("S1")

    assert dock.name_label.text() == "S1"
    assert dock.cell_label.text() == "—"
    assert dock.scheme_list_label.text() == "psu"
    assert dock.scheme_list_label.isVisibleTo(dock) is True
    assert dock.sheet_label.text() == "Channel_1"

    # Comment editing goes through the same upsert_entity path — the loaded
    # record keeps its scheme_list (the write is never a cell-less rewrite).
    from kicadstamp.config.sexp_format import sexp_to_dict
    dock.comment_edit.setText("note")
    fired = []
    dock.saved.connect(lambda: fired.append(True))
    dock._on_comment_commit()
    assert len(fired) == 1
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    entry = next(e for e in data["entities"] if e.get("name") == "S1")
    assert entry["scheme_list"] == "psu"
    assert entry["comment"] == "note"
    assert "cell" not in entry
