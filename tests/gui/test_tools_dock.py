# tests/gui/test_tools_dock.py
"""ToolsDock (phase 5.2, stage 3): the Entity's electrical fields — Nets /
Net overrides / Refs — moved out of PlacerDock's tabs into their own
DetailDock page. The dock picks an Entity (graph-wide), edits the three
dicts, and Save MERGES them over the existing record (so cluster/comment/...
are preserved) and writes to the file the Entity actually lives in (an
Entity in an included file is updated in place, never duplicated)."""
from pathlib import Path

from gui.docks.tools import ToolsDock
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _make_dock(main_window, tmp_path, entities=None, include_entities=None):
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {"pi_filter": {
        "components": [], "vias": [], "tracks": [], "layer": "F.Cu",
    }}})
    root_data = {"clone_placements": [], "include": ["cells.sexp"]}
    if entities is not None:
        root_data["entities"] = entities
    if include_entities is not None:
        inc_file = tmp_path / "entities.sexp"
        _write(inc_file, {"entities": include_entities})
        root_data["include"].append("entities.sexp")
    root_file = tmp_path / "root.sexp"
    _write(root_file, root_data)
    dock = ToolsDock(main_window)
    dock.set_root_path(root_file)
    return dock, root_file


def test_entity_choices_populated_from_the_include_graph(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, entities=[
        {"name": "E1", "cell": "pi_filter"},
    ])
    items = [dock.target_combo.itemText(i) for i in range(dock.target_combo.count())]
    assert items == ["E1"]


def test_pick_loads_electrical_fields(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, entities=[
        {"name": "E1", "cell": "pi_filter", "cluster": "CL1",
         "nets": {"C_IN": "+3V3"}, "net_overrides": {"+3V3": "+3V3_DIRTY"},
         "refs": {"C_IN": "C5"}},
    ])
    dock.target_combo.setCurrentText("E1")
    assert dock.nets_table.to_dict() == {"C_IN": "+3V3"}
    assert dock.net_overrides_table.to_dict() == {"+3V3": "+3V3_DIRTY"}
    assert dock.refs_table.to_dict() == {"C_IN": "C5"}


def test_save_writes_electrical_fields_and_preserves_others(main_window, tmp_path):
    """Save MERGES the edited dicts over the existing raw record — cluster/
    comment and anything the dock doesn't edit are preserved (upsert replaces
    the whole record, so the merge is what keeps the other fields)."""
    dock, root_file = _make_dock(main_window, tmp_path, entities=[
        {"name": "E1", "cell": "pi_filter", "cluster": "CL1", "comment": "note",
         "nets": {"C_IN": "+3V3"}},
    ])
    dock.target_combo.setCurrentText("E1")
    dock.nets_table.load_dict({"C_IN": "+5V", "C_OUT": "GND"})
    dock._do_save()
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    assert by_name["E1"]["nets"] == {"C_IN": "+5V", "C_OUT": "GND"}
    assert by_name["E1"]["cluster"] == "CL1"   # preserved by the merge
    assert by_name["E1"]["comment"] == "note"  # preserved by the merge


def test_save_writes_to_the_entitys_own_file(main_window, tmp_path):
    """An Entity living in an INCLUDED file is updated in place there — never
    duplicated into the root (which would make the next load fatal)."""
    dock, root_file = _make_dock(
        main_window, tmp_path,
        include_entities=[{"name": "E1", "cell": "pi_filter", "nets": {"C_IN": "+3V3"}}])
    dock.target_combo.setCurrentText("E1")
    dock.nets_table.load_dict({"C_IN": "+5V"})
    dock._do_save()
    inc_data = _load(tmp_path / "entities.sexp")
    by_name = {e["name"]: e for e in inc_data["entities"]}
    assert by_name["E1"]["nets"] == {"C_IN": "+5V"}
    root_data = _load(root_file)
    assert "E1" not in [e["name"] for e in root_data.get("entities", [])]


def test_save_requires_a_pick(main_window, tmp_path):
    dock, root_file = _make_dock(main_window, tmp_path)
    before = _load(root_file)
    dock._do_save()  # no entity picked -> error, nothing written
    assert _load(root_file) == before


def test_save_emits_saved(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, entities=[
        {"name": "E1", "cell": "pi_filter"},
    ])
    dock.target_combo.setCurrentText("E1")
    emitted = []
    dock.saved.connect(lambda: emitted.append(1))
    dock._do_save()
    assert emitted == [1]
