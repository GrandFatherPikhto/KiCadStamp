# tests/gui/test_placer_entity_mode.py
"""
PlacerDock Entity source mode (2026-08-30, Entity/Placement split, phase
5.2 redesign, Stage 1): the "Entity" Source pick edits an Entity record —
name + cell + electrical/identity fields, NO position (that lives only in
the trees: node; config/entries.py fatals on any positional key by design).

Headless like the rest of test_placer_dock.py: these tests exercise the
combo population from the include graph, the pick-into-form load, the
no-position _build_entity_dict and the _do_save_entity validate-then-upsert
path against real config files on disk — never touching a live board.
"""
from pathlib import Path

import pytest

from gui.docks.placer import PlacerDock
from kicadstamp.config import load_entity
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _make_entity_dock(main_window, tmp_path, entities=None, cells=None):
    """Root config with an included cells file + an entities: section — the
    same include-graph shape the real project uses (an Entity lives wherever
    its record does, possibly in an included file)."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": cells or {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                             "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [],
            "tracks": [],
            "layer": "F.Cu",
        }
    }})
    root_file = tmp_path / "root.sexp"
    _write(root_file, {
        "clone_placements": [],
        "include": ["cells.sexp"],
        "entities": entities or [
            {"name": "E1", "cell": "pi_filter", "cluster": "CL1",
             "nets": {"C_IN": "+3V3"}, "refs": {"C_IN": "C5"}},
            {"name": "E2", "cell": "pi_filter"},
        ],
    })
    dock = PlacerDock(main_window)
    dock.set_root_path(root_file)
    return dock, root_file


def _switch_to_entity(dock) -> None:
    dock.cell_mode_combo.setCurrentIndex(2)


# ── Source mode toggle ────────────────────────────────────────────────────

def test_entity_is_the_third_source_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    assert dock.cell_mode_combo.count() == 3
    assert not dock.is_entity
    assert not dock.is_coordinate
    _switch_to_entity(dock)
    assert dock.is_entity
    # Entity row replaces the Cell/name rows; the electrical tabs stay (they
    # carry Entity fields), the Coordinate tab does not.
    assert dock._entity_row.isHidden() is False
    assert dock._cell_row.isHidden() is True
    assert dock._name_row.isHidden() is True
    assert dock._coordinate_identity_row.isHidden() is True
    assert dock._tabs.isTabVisible(dock._nets_tab_index)
    assert dock._tabs.isTabVisible(dock._net_overrides_tab_index)
    assert dock._tabs.isTabVisible(dock._refs_tab_index)
    assert dock._tabs.isTabVisible(dock._origin_tab_index)
    assert not dock._tabs.isTabVisible(dock._coordinate_tab_index)


def test_cell_mode_hides_the_entity_row(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    assert dock._entity_row.isHidden() is False
    dock.cell_mode_combo.setCurrentIndex(0)  # back to Cell
    assert dock._entity_row.isHidden() is True
    assert dock._cell_row.isHidden() is False


# ── Entity combo population ───────────────────────────────────────────────

def test_entity_choices_populated_from_the_include_graph(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    items = [dock.entity_combo.itemText(i) for i in range(dock.entity_combo.count())]
    assert items == ["E1", "E2"]


def test_broken_root_leaves_entity_combo_empty(main_window, tmp_path):
    root_file = tmp_path / "root.sexp"
    root_file.write_text("(this is (not valid (sexp", encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(root_file)  # must not raise (2026-08-28 hardening)
    assert dock.entity_combo.count() == 0


# ── Picking an Entity loads its fields ────────────────────────────────────

def test_picking_entity_loads_its_fields_into_the_form(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    assert dock._selected_cell == "pi_filter"
    assert dock.placer_name_edit.text() == "E1"
    assert dock.cluster_edit.currentText() == "CL1"
    assert dock.nets_table.to_dict() == {"C_IN": "+3V3"}
    assert dock.refs_table.to_dict() == {"C_IN": "C5"}
    assert dock._loaded_entity_identity == "E1"


def test_entity_pick_unknown_name_is_ignored(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock._on_entity_picked("NOPE")  # must not raise, must not claim an identity
    assert dock._selected_cell is None
    assert dock._loaded_entity_identity is None


# ── Save payload: an Entity, never a position ─────────────────────────────

def test_entity_build_payload_has_no_position_fields(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    entry = dock._build_entry_dict()
    assert entry == {
        "name": "E1", "cell": "pi_filter", "cluster": "CL1",
        "nets": {"C_IN": "+3V3"}, "refs": {"C_IN": "C5"},
    }
    for forbidden in ("xy", "anchor_ref", "anchor_role", "anchor_point",
                      "rotation_deg", "radius_mm", "angle_deg"):
        assert forbidden not in entry
    entity = load_entity(entry)  # the real backend validator accepts it
    assert entity.name == "E1"
    assert entity.cell == "pi_filter"


def test_entity_save_requires_a_name(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    assert dock._build_entry_dict() is None


def test_entity_save_writes_entities_section_and_replaces_in_place(main_window, tmp_path):
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    dock.cluster_edit.setCurrentText("CL1_NEW")
    dock._do_save()
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    assert by_name["E1"]["cluster"] == "CL1_NEW"
    assert set(by_name) == {"E1", "E2"}  # E2 untouched, no duplicate E1


def test_entity_save_rejects_a_positional_key(main_window, tmp_path):
    """_build_entity_dict never emits a position, but the save path must
    still refuse one if it ever arrives (load_entity fatals) — the same
    no-position guarantee config/entries.py enforces. Nothing is written."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    before = _load(root_file)
    dock._do_save_entity({"name": "E1", "cell": "pi_filter", "xy": [1.0, 2.0]})
    assert _load(root_file) == before


def test_entity_save_rename_deletes_old_record_defensively(main_window, tmp_path):
    """Saving under a name different from the loaded identity must remove
    the old record (mirrors the clone path's rename, 2026-08-15)."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")  # loads E1, records the identity
    assert dock._loaded_entity_identity == "E1"
    dock._do_save_entity({"name": "E3", "cell": "pi_filter", "cluster": "CL1"})
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    assert set(by_name) == {"E2", "E3"}
    assert by_name["E3"]["cluster"] == "CL1"


# ── Window-title identity ─────────────────────────────────────────────────

def test_current_entity_name_reflects_entity_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E2")
    assert dock.current_entity_name == "E2"


def test_current_entity_name_falls_back_to_clone_name_outside_entity_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    assert dock.current_entity_name == ""  # blank clone Cluster in Cell mode
