# tests/gui/test_cell_editor.py
"""
CellDock tests are deliberately headless AND board-mutation-free, same
reasoning as tests/gui/test_points_dock.py/test_rules_dock.py — no live
KiCad connection is involved (CellDock has no Resolve/Redraw of its own,
see its module docstring), so these only check what the dock builds/
validates/writes.
"""
from types import SimpleNamespace

import pytest
import yaml

from gui.docks.cell_editor import CellDock
from kicadstamp.config import load_template_via
from kicadstamp.exceptions import ValidationError


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _read_yaml(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, data if data is not None else {"cells": {}})
    dock = CellDock(main_window)
    dock.set_target_file(target_file)
    return dock, target_file


# ── Components tab ───────────────────────────────────────────────────────

def test_add_component_appends_and_selects_the_new_row(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock.comp_offset_along_edit.setText("1.5")
    dock.comp_offset_across_edit.setText("-2.0")
    dock.comp_angle_edit.setText("90")

    dock._on_add_component()

    assert dock._components == [
        {"role": "HEAVY", "offset_along_mm": 1.5, "offset_across_mm": -2.0, "angle_deg": 90.0}
    ]
    assert dock.components_table.rowCount() == 1
    assert dock._selected_component == 0
    assert dock.components_table.item(0, 0).text() == "HEAVY"


def test_component_role_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    assert dock._build_component_dict() is None
    assert any("Role is required" in r.message for r in caplog.records)


def test_duplicate_component_role_is_rejected_on_add(main_window, tmp_path):
    """The per-slot validator (load_template_component_slot) doesn't check
    uniqueness by itself (that's a whole-cell check, see _load_cell) — this
    only proves a missing role IS caught immediately, the duplicate-role
    case is caught later at Save via load_cell (see
    test_save_rejects_duplicate_component_roles below)."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()
    assert len(dock._components) == 2  # allowed at add-time, caught at Save


def test_update_component(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()

    dock.comp_offset_along_edit.setText("3.0")
    dock._on_update_component()

    assert dock._components == [{"role": "HEAVY", "offset_along_mm": 3.0}]
    assert dock.components_table.item(0, 1).text() == "3.0"


def test_remove_component(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()

    dock._on_remove_component()

    assert dock._components == []
    assert dock.components_table.rowCount() == 0
    assert dock._selected_component is None


def test_remove_component_without_selection_shows_error(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._on_remove_component()
    assert any("Pick a component row first" in r.message for r in caplog.records)


# ── Vias tab ──────────────────────────────────────────────────────────────

def test_add_via_appends_and_selects_the_new_row(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.via_offset_along_edit.setText("0.5")
    dock.via_net_edit.setText("GND")
    dock.via_drill_edit.setText("0.4")
    dock.via_diameter_edit.setText("0.8")

    dock._on_add_via()

    assert dock._vias == [
        {"offset_along_mm": 0.5, "net": "GND", "drill_mm": 0.4, "diameter_mm": 0.8}
    ]
    assert dock.vias_table.rowCount() == 1


def test_via_defaults_when_blank(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._on_add_via()
    assert dock._vias == [{}]  # everything defaults, nothing written


def test_remove_via(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._on_add_via()
    dock._on_remove_via()
    assert dock._vias == []


def test_via_net_source_toggles_row_visibility(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._tabs.setCurrentWidget(dock.vias_table.parentWidget())  # isVisibleTo needs the active tab

    dock.via_net_source_combo.setCurrentIndex(0)
    assert dock._via_net_literal_row.isVisibleTo(dock) and not dock._via_net_role_row.isVisibleTo(dock)

    dock.via_net_source_combo.setCurrentIndex(1)
    assert dock._via_net_role_row.isVisibleTo(dock) and not dock._via_net_literal_row.isVisibleTo(dock)


def test_add_via_with_net_from_role(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("C_IN_BULK")
    dock._on_add_component()
    dock.via_net_source_combo.setCurrentIndex(1)
    dock.via_net_from_role_combo.setCurrentText("C_IN_BULK")

    dock._on_add_via()

    assert dock._vias == [{"net_from_role": "C_IN_BULK"}]
    assert dock.vias_table.item(0, 2).text() == "role:C_IN_BULK"


def test_add_via_with_net_from_role_and_pad(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("LDO")
    dock._on_add_component()
    dock.via_net_source_combo.setCurrentIndex(1)
    dock.via_net_from_role_combo.setCurrentText("LDO")
    dock.via_net_from_role_pad_edit.setText("2")

    dock._on_add_via()

    assert dock._vias == [{"net_from_role": "LDO", "net_from_role_pad": "2"}]
    assert dock.vias_table.item(0, 2).text() == "role:LDO/pad:2"


def test_via_net_from_role_requires_a_role(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.via_net_source_combo.setCurrentIndex(1)
    assert dock._build_via_dict() is None
    assert any("pick a Role first" in r.message for r in caplog.records)


def test_via_net_and_net_from_role_together_is_rejected(main_window, tmp_path):
    """Mutual exclusion between net: and net_from_role: is enforced by the
    shared config loader (load_template_via), not duplicated in the GUI —
    exercised here via a hand-built dict since the form itself only ever
    writes one or the other (see _build_via_dict)."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.via_net_source_combo.setCurrentIndex(0)
    dock.via_net_edit.setText("GND")
    entry = dock._build_via_dict()
    assert entry == {"net": "GND"}  # sanity: literal mode alone is fine

    with pytest.raises(ValidationError, match="net and via.net_from_role"):
        load_template_via({"net": "GND", "net_from_role": "C_IN_BULK"})


# ── Tracks tab ────────────────────────────────────────────────────────────

def test_add_track_appends_and_selects_the_new_row(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.track_start_along_edit.setText("0")
    dock.track_start_across_edit.setText("0")
    dock.track_end_along_edit.setText("5")
    dock.track_end_across_edit.setText("0")
    dock.track_width_edit.setText("0.3")
    dock.track_net_edit.setText("GND")

    dock._on_add_track()

    assert dock._tracks == [
        {"end_along_mm": 5.0, "width_mm": 0.3, "net": "GND"}
    ]
    assert dock.tracks_table.rowCount() == 1


def test_remove_track(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._on_add_track()
    dock._on_remove_track()
    assert dock._tracks == []


def test_track_net_source_toggles_row_visibility(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._tabs.setCurrentWidget(dock.tracks_table.parentWidget())  # isVisibleTo needs the active tab

    dock.track_net_source_combo.setCurrentIndex(0)
    assert (dock._track_net_literal_row.isVisibleTo(dock)
            and not dock._track_net_role_row.isVisibleTo(dock))

    dock.track_net_source_combo.setCurrentIndex(1)
    assert (dock._track_net_role_row.isVisibleTo(dock)
            and not dock._track_net_literal_row.isVisibleTo(dock))


def test_add_track_with_net_from_role(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("C_OUT_BULK")
    dock._on_add_component()
    dock.track_net_source_combo.setCurrentIndex(1)
    dock.track_net_from_role_combo.setCurrentText("C_OUT_BULK")

    dock._on_add_track()

    assert dock._tracks == [{"net_from_role": "C_OUT_BULK"}]
    assert dock.tracks_table.item(0, 5).text() == "role:C_OUT_BULK"


def test_track_net_from_role_requires_a_role(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.track_net_source_combo.setCurrentIndex(1)
    assert dock._build_track_dict() is None
    assert any("pick a Role first" in r.message for r in caplog.records)


# ── Nested cells tab ──────────────────────────────────────────────────────

def test_add_nested_cell_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_name_edit.setText("inner")
    dock.nested_mode_combo.setCurrentIndex(0)
    dock.nested_cell_combo.setCurrentText("leaf")
    dock.nested_x_edit.setText("5.0")
    dock.nested_y_edit.setText("2.0")
    dock.nested_rotation_edit.setText("90")

    dock._on_add_nested()

    assert dock._nested == [
        {"name": "inner", "cell": "leaf", "xy": [5.0, 2.0], "rotation_deg": 90.0}
    ]
    assert dock.nested_table.rowCount() == 1
    assert dock.nested_table.item(0, 1).text() == "cell:leaf"


def test_add_nested_role_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_name_edit.setText("inner")
    dock.nested_mode_combo.setCurrentIndex(1)
    dock.nested_role_combo.setCurrentText("SOME_ROLE")

    dock._on_add_nested()

    assert dock._nested == [{"name": "inner", "role": "SOME_ROLE"}]
    assert dock.nested_table.item(0, 1).text() == "role:SOME_ROLE"


def test_nested_name_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_cell_combo.setCurrentText("leaf")
    assert dock._build_nested_dict() is None
    assert any("name is required" in r.message for r in caplog.records)


def test_nested_cell_mode_requires_a_cell(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_name_edit.setText("inner")
    dock.nested_mode_combo.setCurrentIndex(0)
    assert dock._build_nested_dict() is None
    assert any("Pick a Cell first" in r.message for r in caplog.records)


def test_nested_role_mode_requires_a_role(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_name_edit.setText("inner")
    dock.nested_mode_combo.setCurrentIndex(1)
    assert dock._build_nested_dict() is None
    assert any("Pick a Role first" in r.message for r in caplog.records)


def test_remove_nested(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.nested_name_edit.setText("inner")
    dock.nested_cell_combo.setCurrentText("leaf")
    dock._on_add_nested()
    dock._on_remove_nested()
    assert dock._nested == []


# ── Anchor UI ─────────────────────────────────────────────────────────────

def test_anchor_mode_toggles_row_visibility(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    dock.anchor_mode_combo.setCurrentIndex(0)
    assert not dock._anchor_xy_row.isVisibleTo(dock) and not dock._anchor_role_row.isVisibleTo(dock)

    dock.anchor_mode_combo.setCurrentIndex(1)
    assert dock._anchor_xy_row.isVisibleTo(dock) and not dock._anchor_role_row.isVisibleTo(dock)

    dock.anchor_mode_combo.setCurrentIndex(2)
    assert dock._anchor_role_row.isVisibleTo(dock) and not dock._anchor_xy_row.isVisibleTo(dock)


def test_anchor_role_choices_follow_the_components_list(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()
    items = [dock.anchor_role_combo.itemText(i) for i in range(dock.anchor_role_combo.count())]
    assert items == ["HEAVY"]


def test_via_and_track_net_from_role_choices_follow_the_components_list(main_window, tmp_path):
    """Same closed-set source as anchor_role_combo (this cell's own current
    Components list) — see _refresh_role_choices."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()
    via_items = [dock.via_net_from_role_combo.itemText(i)
                 for i in range(dock.via_net_from_role_combo.count())]
    track_items = [dock.track_net_from_role_combo.itemText(i)
                   for i in range(dock.track_net_from_role_combo.count())]
    assert via_items == ["HEAVY"]
    assert track_items == ["HEAVY"]


# ── Building/Save ─────────────────────────────────────────────────────────

def test_build_cell_dict_with_anchor_xy(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()
    dock.anchor_mode_combo.setCurrentIndex(1)
    dock.anchor_x_edit.setText("1.0")
    dock.anchor_y_edit.setText("2.0")

    name, entry = dock._build_cell_dict()
    assert name == "t"
    assert entry["anchor_xy"] == [1.0, 2.0]


def test_build_cell_dict_with_anchor_role(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()
    dock.anchor_mode_combo.setCurrentIndex(2)
    dock.anchor_role_combo.setCurrentText("A")
    dock.anchor_pad_edit.setText("1")

    name, entry = dock._build_cell_dict()
    assert entry["anchor_role"] == "A"
    assert entry["anchor_pad"] == "1"


def test_anchor_role_combo_is_a_closed_picker_not_a_free_text_field(main_window, tmp_path):
    """Regression (found live 2026-08-06, Denis: clicked Role a couple
    times on a freshly-added, still-componentless cell — GUI froze).
    anchor_role_combo deliberately isn't configure_searchable() (unlike
    every other Role combo in the project) — its value MUST already be one
    of this cell's own components: roles, free text is never valid, so it's
    a plain non-editable dropdown. Trying to set text that isn't an
    existing item is therefore a silent no-op (Qt's own behaviour for a
    non-editable combo), not something the GUI can even produce — the
    equivalent backend rejection (anchor_role naming a role that ISN'T a
    component) is covered directly in tests/test_unique_roles.py's
    TestCellAnchor::test_anchor_role_not_a_component_is_fatal."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock.anchor_role_combo.setCurrentText("NOT_A_COMPONENT")

    assert dock.anchor_role_combo.currentText() != "NOT_A_COMPONENT"


def test_name_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    assert dock._build_cell_dict() is None
    assert any("Name is required" in r.message for r in caplog.records)


def test_save_rejects_duplicate_component_roles(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()
    dock.comp_role_edit.setCurrentText("HEAVY")
    dock._on_add_component()

    assert dock._build_cell_dict() is None
    assert any("appears twice" in r.message for r in caplog.records)


def test_save_writes_dict_section_and_preserves_other_keys(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"points": {"origin": {"xy": [0, 0]}}})
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock._on_save()

    data = _read_yaml(target)
    assert data["cells"] == {"t": {"layer": "F.Cu", "components": [{"role": "A"}],
                                   "vias": [], "tracks": [], "clone_placements": []}}
    assert data["points"] == {"origin": {"xy": [0, 0]}}
    assert any("Wrote" in r.message for r in caplog.records)


def test_save_overwrites_an_existing_cell_by_name(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock._on_save()

    assert _read_yaml(target)["cells"]["t"]["components"] == [{"role": "A"}]
    assert any("Overwrote" in r.message for r in caplog.records)


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = CellDock(main_window)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock._on_save()
    assert any("Pick a file" in r.message for r in caplog.records)


def test_save_emits_saved_signal(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    fired = []
    dock.saved.connect(lambda: fired.append(True))
    dock._on_save()
    assert fired == [True]


# ── new_cell / load_entry ────────────────────────────────────────────────

def test_new_cell_resets_the_form(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("stale")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock.new_cell(target)

    assert dock.name_edit.text() == ""
    assert dock._components == []
    assert dock.components_table.rowCount() == 0


def test_load_entry_round_trips_everything(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {
        "composite": {
            "layer": "B.Cu",
            "anchor_role": "A",
            "anchor_pad": "1",
            "components": [{"role": "A", "offset_along_mm": 1.0}],
            "vias": [{"offset_along_mm": 0.5, "net": "GND"}],
            "tracks": [{"end_along_mm": 5.0, "width_mm": 0.3}],
            "clone_placements": [{"name": "inner", "cell": "leaf", "xy": [1.0, 1.0]}],
        }
    }})

    dock.load_entry("composite")

    assert dock.name_edit.text() == "composite"
    assert dock.layer_combo.currentData() == "B.Cu"
    assert dock.anchor_mode_combo.currentIndex() == 2
    assert dock.anchor_role_combo.currentText() == "A"
    assert dock.anchor_pad_edit.text() == "1"
    assert dock._components == [{"role": "A", "offset_along_mm": 1.0}]
    assert dock._vias == [{"offset_along_mm": 0.5, "net": "GND"}]
    assert dock._tracks == [{"end_along_mm": 5.0, "width_mm": 0.3}]
    assert dock._nested == [{"name": "inner", "cell": "leaf", "xy": [1.0, 1.0]}]
    assert dock.components_table.rowCount() == 1
    assert dock.vias_table.rowCount() == 1
    assert dock.tracks_table.rowCount() == 1
    assert dock.nested_table.rowCount() == 1

    # And it round-trips back out unchanged on Save.
    dock._on_save()
    assert _read_yaml(target)["cells"]["composite"]["anchor_role"] == "A"


def test_load_entry_round_trips_net_from_role(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {
        "composite": {
            "components": [{"role": "LDO", "offset_along_mm": 1.0}],
            "vias": [{"net_from_role": "LDO", "net_from_role_pad": "2"}],
            "tracks": [{"end_along_mm": 5.0, "net_from_role": "LDO"}],
        }
    }})

    dock.load_entry("composite")

    assert dock._vias == [{"net_from_role": "LDO", "net_from_role_pad": "2"}]
    assert dock._tracks == [{"end_along_mm": 5.0, "net_from_role": "LDO"}]

    # Selecting each row must reflect the "From role" mode, not "Literal".
    dock.vias_table.selectRow(0)
    assert dock.via_net_source_combo.currentIndex() == 1
    assert dock.via_net_from_role_combo.currentText() == "LDO"
    assert dock.via_net_from_role_pad_edit.text() == "2"

    dock.tracks_table.selectRow(0)
    assert dock.track_net_source_combo.currentIndex() == 1
    assert dock.track_net_from_role_combo.currentText() == "LDO"
    assert dock.track_net_from_role_pad_edit.text() == ""

    # And it round-trips back out unchanged on Save.
    dock.name_edit.setText("composite")
    dock._on_save()
    saved = _read_yaml(target)["cells"]["composite"]
    assert saved["vias"] == [{"net_from_role": "LDO", "net_from_role_pad": "2"}]
    assert saved["tracks"] == [{"end_along_mm": 5.0, "net_from_role": "LDO"}]


def test_load_entry_with_anchor_xy(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {
        "t": {"components": [], "anchor_xy": [1.5, -2.0]},
    }})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentIndex() == 1
    assert dock.anchor_x_edit.text() == "1.5"
    assert dock.anchor_y_edit.text() == "-2.0"


def test_load_entry_with_no_anchor(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentIndex() == 0


# ── set_root_path / refresh_known_roles ──────────────────────────────────

def test_set_root_path_populates_nested_cell_combo(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    _write_yaml(root, {"cells": {"leaf": {"components": []}, "other": {"components": []}}})
    dock = CellDock(main_window)

    dock.set_root_path(root)

    items = {dock.nested_cell_combo.itemText(i) for i in range(dock.nested_cell_combo.count())}
    assert items == {"leaf", "other"}


def test_refresh_known_roles_populates_role_combos(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    snapshot = [SimpleNamespace(role="HEAVY"), SimpleNamespace(role="LIGHT"), SimpleNamespace(role="")]

    dock.refresh_known_roles(snapshot)

    comp_items = {dock.comp_role_edit.itemText(i) for i in range(dock.comp_role_edit.count())}
    nested_items = {dock.nested_role_combo.itemText(i) for i in range(dock.nested_role_combo.count())}
    assert comp_items == {"HEAVY", "LIGHT"}
    assert nested_items == {"HEAVY", "LIGHT"}
