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
