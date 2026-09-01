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


def _make_dock(main_window, tmp_path, entities=None, include_entities=None,
               cell_components=None):
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {"pi_filter": {
        "components": cell_components if cell_components is not None else [],
        "vias": [], "tracks": [], "layer": "F.Cu",
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


# ── Role/Net choices (2026-09-01 regression fix) ─────────────────────────

_ROLES = [{"role": "C_IN"}, {"role": "C_OUT"}, {"role": "C_OTHER"}]


def _combo_items(combo) -> list:
    return [combo.itemText(i) for i in range(combo.count())]


def test_pick_populates_role_choices_from_the_entitys_cell(main_window, tmp_path):
    """2026-09-01 regression fix: the ToolsDock (migrated 2026-08-30) only
    ever called load_dict() — set_key_choices()/set_value_choices() were never
    wired, so the Role/Net combos stayed empty free text. Picking an Entity
    must scope the Role choices to that Entity's OWN cell components
    (Entity -> cell -> cell.components[].role), not every role on the board."""
    dock, _ = _make_dock(
        main_window, tmp_path,
        entities=[{"name": "E1", "cell": "pi_filter", "nets": {"C_IN": "+3V3"}}],
        cell_components=_ROLES)
    dock.target_combo.setCurrentText("E1")
    assert _combo_items(dock.nets_table.key_edit) == ["C_IN", "C_OTHER", "C_OUT"]
    assert _combo_items(dock.refs_table.key_edit) == ["C_IN", "C_OTHER", "C_OUT"]


def test_role_choices_are_scoped_to_the_picked_cell_only(main_window, tmp_path):
    """Same scoping rule as PlacerDock._rebuild_cell_role_choices (placer.py:
    1625) — a role that is NOT in this entity's cell must not appear."""
    dock, _ = _make_dock(
        main_window, tmp_path,
        entities=[{"name": "E1", "cell": "pi_filter"}],
        cell_components=[{"role": "C_IN"}])
    dock.target_combo.setCurrentText("E1")
    assert _combo_items(dock.nets_table.key_edit) == ["C_IN"]
    assert "C_OUT" not in _combo_items(dock.nets_table.key_edit)


def test_refresh_known_nets_populates_net_value_choices(main_window, tmp_path):
    """2026-09-01 regression fix: the Net value combos are fed from the live
    board's net names via refresh_known_nets (wired in DockHub.push_snapshot),
    the same list PlacerDock's refresh_known_nets feeds its Nets/Net
    overrides tabs (placer.py:1546)."""
    class _Net:
        def __init__(self, name):
            self.name = name

    class _Adapter:
        def get_all_nets(self):
            return [_Net("+3V3"), _Net("GND"), _Net("+5V")]

    class _Board:
        adapter = _Adapter()

    dock, _ = _make_dock(main_window, tmp_path, entities=[
        {"name": "E1", "cell": "pi_filter"},
    ])
    dock.refresh_known_nets(_Board())
    assert _combo_items(dock.nets_table.value_edit) == ["+3V3", "+5V", "GND"]
    assert _combo_items(dock.net_overrides_table.key_edit) == ["+3V3", "+5V", "GND"]
    assert _combo_items(dock.net_overrides_table.value_edit) == ["+3V3", "+5V", "GND"]


def test_load_entity_preloads_without_the_combo_signal(main_window, tmp_path):
    """load_entity() is the programmatic entry point (Config tree DOUBLE click
    on an Entities leaf -> DockHub._start_edit_entity_template) — it must load
    the record and fill the role choices even when the target combo text never
    changes (mirror of PointsDock.load_entry)."""
    dock, _ = _make_dock(
        main_window, tmp_path,
        entities=[{"name": "E1", "cell": "pi_filter", "nets": {"C_IN": "+3V3"}}],
        cell_components=_ROLES)
    dock.load_entity("E1")
    assert dock.nets_table.to_dict() == {"C_IN": "+3V3"}
    assert _combo_items(dock.nets_table.key_edit) == ["C_IN", "C_OTHER", "C_OUT"]
