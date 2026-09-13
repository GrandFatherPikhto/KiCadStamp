# tests/gui/test_cell_editor.py
"""
CellDock tests are deliberately headless AND board-mutation-free, same
reasoning as tests/gui/test_points_dock.py/test_rules_dock.py — no live
KiCad connection is involved (CellDock has no Resolve/Redraw of its own,
see its module docstring), so these only check what the dock builds/
validates/writes.
"""
from types import SimpleNamespace

from kipy.board_types import BoardLayer as KipyBoardLayer
from PyQt6.QtWidgets import QCheckBox, QLabel

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.board_layers import ALL_COPPER_LAYERS, remember_read_layers
from gui.docks.cell_editor import CellDock
from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.config import load_template_via
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError


def _fill_cell_defaults(data: dict) -> dict:
    """s-expr omits default-valued Cell fields (layer='F.Cu', empty
    vias/components/tracks/clone_placements lists); re-apply them so the
    raw-dict assertions stay identical to the old yaml.safe_load reads."""
    for entry in data.get("cells", {}).values():
        entry.setdefault("layer", "F.Cu")
        entry.setdefault("vias", [])
        entry.setdefault("components", [])
        entry.setdefault("tracks", [])
        entry.setdefault("clone_placements", [])
    return data


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return _fill_cell_defaults(sexp_to_dict(path.read_text(encoding="utf-8"))) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "root.sexp"
    _write(target_file, data if data is not None else {"cells": {}})
    dock = CellDock(main_window)
    dock.set_root_path(target_file)
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


def test_add_component_with_net_template_pad(main_window, tmp_path):
    """2026-08-16 (net_template_pad): the component form accepts a pad number
    next to net_template and writes both into the built entry."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("LDO_ADJ")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock.comp_net_template_pad_edit.setText("3")

    dock._on_add_component()

    assert dock._components == [
        {"role": "LDO_ADJ", "net_template": "NET_{p}", "net_template_pad": "3"}
    ]
    assert dock.components_table.item(0, 6).text() == "3"  # Net template pad column


def test_component_net_template_pad_requires_net_template(main_window, tmp_path, caplog):
    """Mirror of the loader's fatal (2026-08-16, net_template_pad): the form
    must reject a pad without a net_template BEFORE assembling an entry the
    loader would reject on the next load — same error surface as the loader,
    just caught at edit time."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("LDO_ADJ")
    dock.comp_net_template_pad_edit.setText("3")

    assert dock._build_component_dict() is None
    assert any("Net template pad requires a net template" in r.message for r in caplog.records)
    assert dock._components == []


def test_add_component_with_same_as_role(main_window, tmp_path):
    """2026-08-16 (net_template_same_as_role): the component form accepts a
    same-net role reference (a closed combo of THIS cell's own roles) next to
    net_template and writes it into the built entry."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("R_FB_BOT")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock._on_add_component()
    dock.comp_role_edit.setCurrentText("R_FB_TOP")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock.comp_net_template_same_as_role_combo.setCurrentText("R_FB_BOT")
    dock._on_add_component()

    assert dock._components[1] == {"role": "R_FB_TOP", "net_template": "NET_{p}",
                                   "net_template_same_as_role": "R_FB_BOT"}
    assert dock.components_table.item(1, 7).text() == "R_FB_BOT"  # Same net as role column


def test_component_same_as_role_requires_net_template(main_window, tmp_path, caplog):
    """Mirror of the loader's fatal (2026-08-16, net_template_same_as_role):
    the form rejects a same-net role reference without a net_template BEFORE
    assembling an entry the loader would reject on the next load."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("R_FB_BOT")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock._on_add_component()
    dock.comp_role_edit.setCurrentText("R_FB_TOP")
    dock.comp_net_template_edit.setText("")  # no net_template for this row
    dock.comp_net_template_same_as_role_combo.setCurrentText("R_FB_BOT")

    assert dock._build_component_dict() is None
    assert any("Same net as role requires a net template" in r.message for r in caplog.records)


def test_component_pad_and_same_as_role_both_rejected(main_window, tmp_path, caplog):
    """Mirror of the loader's mutual-exclusion fatal (2026-08-16): a fixed pad
    number AND a same-net role reference together are rejected in the form."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.comp_role_edit.setCurrentText("R_FB_BOT")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock._on_add_component()
    dock.comp_role_edit.setCurrentText("R_FB_TOP")
    dock.comp_net_template_edit.setText("NET_{p}")
    dock.comp_net_template_pad_edit.setText("2")
    dock.comp_net_template_same_as_role_combo.setCurrentText("R_FB_BOT")

    assert dock._build_component_dict() is None
    assert any("not both" in r.message for r in caplog.records)


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

def test_anchor_mode_toggles_role_row_visibility(main_window, tmp_path):
    """The Anchor combo now offers only "(none)" and "Role" (Фаза B): the Role
    row shows in Role mode and nowhere else. The combo is keyed by
    currentData(), not by index, so the item ORDER carries no meaning."""
    dock, _ = _make_dock(main_window, tmp_path)

    dock.anchor_mode_combo.setCurrentIndex(dock.anchor_mode_combo.findData("none"))
    assert not dock._anchor_role_row.isVisibleTo(dock)

    dock.anchor_mode_combo.setCurrentIndex(dock.anchor_mode_combo.findData("role"))
    assert dock._anchor_role_row.isVisibleTo(dock)


def test_refresh_origin_role_returns_role_after_xy_item_removed(main_window, tmp_path):
    """THE Фаза B regression: _refresh_origin_role is keyed on currentData().
    Removing the old "XY" item shifted "Role" from index 2 to 1, and the old
    index-based check would then ALWAYS have returned None — build_refresh_plan
    would fall back to the legacy zero-slot origin and every Role-anchored
    cell's re-read would start failing, silently."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {"t": {
        "components": [{"role": "A", "offset_along_mm": -5.05,
                        "offset_across_mm": -0.295, "angle_deg": 0.0}],
        "anchor_role": "A", "anchor_pad": "1",
        "anchor_xy": [-8.05, -2.795]}}})
    dock.load_entry("t")

    assert dock.anchor_mode_combo.currentData() == "role"
    assert dock._refresh_origin_role() == "A"

    dock.anchor_mode_combo.setCurrentIndex(dock.anchor_mode_combo.findData("none"))
    assert dock._refresh_origin_role() is None



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

def test_build_cell_dict_with_anchor_role(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()
    dock.anchor_mode_combo.setCurrentIndex(dock.anchor_mode_combo.findData("role"))
    dock.anchor_role_combo.setCurrentText("A")
    dock.anchor_pad_edit.setText("1")

    name, entry = dock._build_cell_dict()
    assert entry["anchor_role"] == "A"
    assert entry["anchor_pad"] == "1"
    # The form has no X/Y editor any more (Фаза B): a freshly built cell writes
    # NO anchor_xy — cell_mount_offset's role-centre branch resolves the mount.
    assert "anchor_xy" not in entry


def test_build_cell_dict_carries_loaded_anchor_xy_through(main_window, tmp_path):
    """Фаза B, GUARD 1/2: an unrelated edit in CellDock must never DROP a
    stored anchor_xy. Losing it would turn a v2 Role+Pad+XY cell into the
    legacy rebase-by-pad shape, whose mount resolves to (0,0) and silently
    moves the cell's content."""
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"t": {
        "components": [{"role": "A", "offset_along_mm": -5.05,
                        "offset_across_mm": -0.295, "angle_deg": 0.0}],
        "anchor_role": "A", "anchor_pad": "1",
        "anchor_xy": [-8.05, -2.795]}}})
    dock.load_entry("t")
    dock.comment_edit.setText("edited elsewhere")

    name, entry = dock._build_cell_dict()
    assert entry["anchor_xy"] == [-8.05, -2.795]
    assert entry["anchor_role"] == "A"
    assert entry["anchor_pad"] == "1"
    assert entry["comment"] == "edited elsewhere"


def test_new_cell_clears_carried_anchor_xy(main_window, tmp_path):
    """new_cell resets the carry-forward: a brand-new cell must not inherit
    the previously loaded cell's anchor_xy."""
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"t": {
        "components": [{"role": "A", "offset_along_mm": 0.0,
                        "offset_across_mm": 0.0}],
        "anchor_xy": [-8.05, -2.795]}}})
    dock.load_entry("t")
    dock.new_cell(target)
    dock.name_edit.setText("fresh")
    name, entry = dock._build_cell_dict()
    assert name == "fresh"
    assert "anchor_xy" not in entry


# ── comment field (handoff_2026_08_27_entity_comment_field.md) ────────────

def test_build_cell_dict_includes_comment(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("t")
    dock.comment_edit.setText("a cell note")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    name, entry = dock._build_cell_dict()
    assert name == "t"
    assert entry["comment"] == "a cell note"


def test_comment_saves_and_loads_back(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.name_edit.setText("t")
    dock.comment_edit.setText("a cell note")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock._on_save()

    assert _load(target)["cells"]["t"]["comment"] == "a cell note"
    dock.load_entry("t")
    assert dock.comment_edit.text() == "a cell note"


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

    data = _load(target)
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

    assert _load(target)["cells"]["t"]["components"] == [{"role": "A"}]
    assert any("Overwrote" in r.message for r in caplog.records)


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = CellDock(main_window)
    dock.name_edit.setText("t")
    dock.comp_role_edit.setCurrentText("A")
    dock._on_add_component()

    dock._on_save()
    assert any("Set the project root first" in r.message for r in caplog.records)


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
    assert dock.anchor_mode_combo.currentData() == "role"
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
    assert _load(target)["cells"]["composite"]["anchor_role"] == "A"


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
    saved = _load(target)["cells"]["composite"]
    assert saved["vias"] == [{"net_from_role": "LDO", "net_from_role_pad": "2"}]
    assert saved["tracks"] == [{"end_along_mm": 5.0, "net_from_role": "LDO"}]


def test_load_entry_round_trips_net_template_pad(main_window, tmp_path):
    """2026-08-16 (net_template_pad): the component's pad field loads into the
    editor, shows in the table, and round-trips back out on Save alongside its
    net_template."""
    dock, target = _make_dock(main_window, tmp_path, {"cells": {
        "composite": {
            "components": [{"role": "LDO_ADJ", "net_template": "NET_{p}",
                            "net_template_pad": "3"}],
        }
    }})

    dock.load_entry("composite")

    assert dock._components == [{"role": "LDO_ADJ", "net_template": "NET_{p}",
                                 "net_template_pad": "3"}]
    dock.components_table.selectRow(0)
    assert dock.comp_net_template_edit.text() == "NET_{p}"
    assert dock.comp_net_template_pad_edit.text() == "3"
    assert dock.components_table.item(0, 6).text() == "3"  # Net template pad column

    # And it round-trips back out unchanged on Save.
    dock.name_edit.setText("composite")
    dock._on_save()
    saved = _load(target)["cells"]["composite"]
    assert saved["components"] == [{"role": "LDO_ADJ", "net_template": "NET_{p}",
                                    "net_template_pad": "3"}]


def test_load_entry_round_trips_same_as_role(main_window, tmp_path):
    """2026-08-16 (net_template_same_as_role): the same-net role reference
    loads into the closed combo, shows in the table, and round-trips back out
    on Save alongside its net_template."""
    dock, target = _make_dock(main_window, tmp_path, {"cells": {
        "composite": {
            "components": [
                {"role": "R_FB_BOT", "net_template": "NET_{p}"},
                {"role": "R_FB_TOP", "net_template": "NET_{p}",
                 "net_template_same_as_role": "R_FB_BOT"},
            ],
        }
    }})

    dock.load_entry("composite")

    assert dock._components[1]["net_template_same_as_role"] == "R_FB_BOT"
    dock.components_table.selectRow(1)
    assert dock.comp_net_template_same_as_role_combo.currentText() == "R_FB_BOT"
    assert dock.components_table.item(1, 7).text() == "R_FB_BOT"  # Same net as role column

    # And it round-trips back out unchanged on Save.
    dock.name_edit.setText("composite")
    dock._on_save()
    saved = _load(target)["cells"]["composite"]
    assert saved["components"][1]["net_template_same_as_role"] == "R_FB_BOT"


def test_load_entry_with_anchor_xy_only(main_window, tmp_path):
    """An anchor_xy-only cell (no anchor_role) opens in "(none)" — the Role
    form does not manage that anchor — but the value is kept on save (read
    back from disk by the loaded name; see G.1 tests below)."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {
        "t": {"components": [], "anchor_xy": [1.5, -2.0]},
    }})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentData() == "none"
    assert dock._loaded_name == "t"


def test_load_entry_role_pad_keeps_stored_anchor_xy(main_window, tmp_path):
    """A saved v2 Role+Pad+XY cell reloads in Role mode; the stored anchor_xy
    is still on disk for the save-time read (the form has no X/Y editors)."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {
        "t": {
            "components": [{"role": "C_OUT_BYPASS", "offset_along_mm": -5.05,
                            "offset_across_mm": -0.295, "angle_deg": 0.0}],
            "anchor_role": "C_OUT_BYPASS",
            "anchor_pad": "1",
            "anchor_xy": [-8.05, -2.795],
        },
    }})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentData() == "role"
    assert dock.anchor_role_combo.currentText() == "C_OUT_BYPASS"
    assert dock.anchor_pad_edit.text() == "1"
    assert dock._loaded_entry_on_disk()["anchor_xy"] == [-8.05, -2.795]


def test_load_entry_role_only_has_no_carried_anchor_xy(main_window, tmp_path):
    """A legacy role-only cell (no anchor_xy) reloads in Role mode; nothing to
    carry — cell_mount_offset's role-centre branch resolves it."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {
        "t": {
            "components": [{"role": "A", "offset_along_mm": 1.0,
                            "offset_across_mm": 0.0}],
            "anchor_role": "A",
        },
    }})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentData() == "role"
    assert dock.anchor_role_combo.currentText() == "A"
    assert "anchor_xy" not in dock._loaded_entry_on_disk()


# ── G.1: anchor_xy is read from DISK at save time (non-modal Cell dialog) ──

def _anchor_base_cell(**extra):
    cell = {"components": [{"role": "A", "offset_along_mm": -5.05,
                            "offset_across_mm": -0.295, "angle_deg": 0.0}]}
    cell.update(extra)
    return {"cells": {"t": cell}}


def test_anchor_xy_is_read_from_disk_at_save_case_a(main_window, tmp_path):
    """G.1 case A: the anchor page placed a marker (anchor_xy written on disk)
    while CellDock held the cell. An unrelated CellDock save must KEEP it."""
    dock, target = _make_dock(main_window, tmp_path, _anchor_base_cell())
    dock.load_entry("t")

    _write(target, _anchor_base_cell(anchor_xy=[1.5, 2.5]))

    dock.comment_edit.setText("unrelated edit")
    _name, entry = dock._build_cell_dict()
    assert entry["anchor_xy"] == [1.5, 2.5]


def test_anchor_xy_is_read_from_disk_at_save_case_b(main_window, tmp_path):
    """G.1 case B: the anchor page removed anchor_xy (Component anchor) while
    CellDock held the old value. The save must NOT resurrect it — a stale
    anchor_xy would WIN over the fresh Role/Pad anchor (GUARD 1)."""
    dock, target = _make_dock(main_window, tmp_path,
                              _anchor_base_cell(anchor_xy=[-8.05, -2.795]))
    dock.load_entry("t")

    _write(target, _anchor_base_cell(anchor_role="A", anchor_pad="1"))

    dock.comment_edit.setText("unrelated edit")
    _name, entry = dock._build_cell_dict()
    assert "anchor_xy" not in entry


def test_anchor_xy_survives_the_real_save_path(main_window, tmp_path):
    """G.1 case A through the real save: merge_write replaces the whole cell
    entry, so the marker must come from the save-time disk read."""
    dock, target = _make_dock(main_window, tmp_path, _anchor_base_cell())
    dock.load_entry("t")
    _write(target, _anchor_base_cell(anchor_xy=[1.5, 2.5]))

    dock.comment_edit.setText("unrelated edit")
    dock._on_save()

    on_disk = _load(target)["cells"]["t"]
    assert on_disk["anchor_xy"] == [1.5, 2.5]


def test_rename_carries_the_loaded_anchor_xy(main_window, tmp_path):
    """G.1 rename: changing the name in the form copies the cell — the
    anchor_xy must come from the LOADED cell, not be looked up under the new
    (absent) name."""
    dock, _ = _make_dock(main_window, tmp_path,
                         _anchor_base_cell(anchor_xy=[-8.05, -2.795]))
    dock.load_entry("t")
    dock.name_edit.setText("t_copy")

    name, entry = dock._build_cell_dict()
    assert name == "t_copy"
    assert entry["anchor_xy"] == [-8.05, -2.795]


def test_load_entry_with_no_anchor(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.load_entry("t")
    assert dock.anchor_mode_combo.currentData() == "none"


# ── set_root_path / refresh_known_roles ──────────────────────────────────

def test_set_root_path_populates_nested_cell_combo(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"leaf": {"components": []}, "other": {"components": []}}})
    dock = CellDock(main_window)

    dock.set_root_path(root)

    items = {dock.nested_cell_combo.itemText(i) for i in range(dock.nested_cell_combo.count())}
    assert items == {"leaf", "other"}


# ── Target-file combo (2026-08-13, plan tree_to_combo_file_pickers) ──────

def _combo_index_for_filename(combo, filename):
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1


def test_refresh_known_roles_populates_role_combos(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    snapshot = [SimpleNamespace(role="HEAVY"), SimpleNamespace(role="LIGHT"), SimpleNamespace(role="")]

    dock.refresh_known_roles(snapshot)

    comp_items = {dock.comp_role_edit.itemText(i) for i in range(dock.comp_role_edit.count())}
    nested_items = {dock.nested_role_combo.itemText(i) for i in range(dock.nested_role_combo.count())}
    assert comp_items == {"HEAVY", "LIGHT"}
    assert nested_items == {"HEAVY", "LIGHT"}


# ── Refresh geometry from selection (2026-09-03, plan cell_geometry_refresh)
# Headless: no worker threads — the dock's run/finish/apply methods are driven
# directly with synthetic board DTOs and a stubbed preview dialog.

def _refresh_dto_fp(ref, role, x_mm, y_mm, angle=0.0):
    return Footprint(ref=ref, uuid=f"uuid-{ref}",
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=angle, layer=BoardLayer.BL_F_Cu)


def _refresh_dto_via(net, x_mm, y_mm):
    return Via(uuid=f"v-{net}", position=Vector2.from_xy_mm(x_mm, y_mm),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


class _RefreshBoard:
    """connection.board stand-in: adapter returns the fixed selection and reads
    Role by ref — enough for build_refresh_plan's footprint role pass. Since
    the additive refresh mode (2026-09-05) classifies leftover copper through
    _selection_role_nets, get_footprint_pads is exposed (empty pad map ->
    literal-net fallback, same as _ImportBoard)."""
    def __init__(self, items, roles=None):
        self.adapter = SimpleNamespace(
            # K.1 (2026-09-10, plan stale_board_snapshot): refresh_board is part
            # of the adapter surface the workers now use — a live read refreshes
            # the board before touching the cached footprint list.
            refresh_board=lambda: None,
            get_selected_items=lambda: list(items),
            get_field_value=lambda fp, name: (roles or {}).get(fp.ref),
            get_footprint_pads=lambda fp: [])


def _loaded_cell_data():
    return {"cells": {"t": {
        "layer": "F.Cu",
        "components": [
            {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0, "net_template": "VCC"},
            {"role": "CAP", "offset_along_mm": 1.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0, "net_template": "VCC",
             "net_template_same_as_role": "ORIG"},
        ],
        "vias": [{"offset_along_mm": 0.5, "offset_across_mm": 1.5, "net": "GND"}],
        "tracks": [],
    }}}


def test_run_refresh_geometry_refreshes_the_board_first(main_window, tmp_path):
    """K.1 (2026-09-10, plan stale_board_snapshot): the refresh worker is a LIVE
    read — refresh_board() must run BEFORE get_selected_items()/get_footprints(),
    because build_refresh_plan resolves roles and fields through the adapter's
    cached footprint list and the GUI poll tick no longer refreshes it."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    calls = []

    class _Adapter:
        def refresh_board(self):
            calls.append("refresh")

        def get_selected_items(self):
            calls.append("select")
            return []

        def get_field_value(self, fp, name):
            return None

    class _Board:
        adapter = _Adapter()

    result = dock._run_refresh_geometry(
        {"board": _Board(), "components": [], "vias": [], "tracks": []})

    assert calls == ["refresh", "select"]
    assert "error" in result      # the empty payload is a role problem, not ours


def test_refresh_geometry_button_enabled_only_with_board_and_components(main_window, tmp_path):
    """§2.1 activity: adapter present (push_snapshot fires) AND the loaded cell
    has components. No board -> disabled; empty cell -> disabled."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.load_entry("t")
    # No board yet — refresh_known_roles isn't called, button stays disabled.
    assert not dock.refresh_geometry_button.isEnabled()

    # Board present but cell still empty.
    main_window.connection.board = _RefreshBoard([])
    dock.refresh_known_roles([])
    assert not dock.refresh_geometry_button.isEnabled()

    # Load a cell WITH components while the board is present.
    dock.load_entry("t", None)
    dock._components.append({"role": "ORIG", "offset_along_mm": 0.0,
                             "offset_across_mm": 0.0})
    dock._refresh_all_tables()
    assert dock.refresh_geometry_button.isEnabled()


def test_refresh_geometry_apply_updates_geometry_keeps_other_fields(main_window, tmp_path,
                                                                   monkeypatch):
    """Successful plan -> applied DIRECTLY, no preview dialog (2026-09-06):
    mutates ONLY the geometric keys on the SAME dicts already in
    _components/_vias, every other field survives intact
    (net_template_same_as_role/net_template stay), and the success report goes
    to _show_message."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    orig_cap = next(c for c in dock._components if c["role"] == "CAP")
    orig_via = dock._vias[0]

    # Live board: components + via moved to new positions.
    board = _RefreshBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.5, 9.0, angle=90.0),
         _refresh_dto_via("GND", 11.0, 13.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks)})
    assert "plan" in result
    dock._finish_refresh_geometry(result)

    assert next(c for c in dock._components if c["role"] == "CAP")["offset_along_mm"] == 1.5
    assert next(c for c in dock._components if c["role"] == "CAP")["offset_across_mm"] == -1.0
    assert next(c for c in dock._components if c["role"] == "CAP")["angle_deg"] == 90.0
    assert dock._vias[0]["offset_along_mm"] == 1.0
    assert dock._vias[0]["offset_across_mm"] == 3.0

    # Same dict objects were mutated (not replaced) and non-geo keys survived.
    assert dock._vias[0] is orig_via
    assert next(c for c in dock._components if c["role"] == "CAP") is orig_cap
    assert orig_cap["net_template"] == "VCC"
    assert orig_cap["net_template_same_as_role"] == "ORIG"
    assert dock._vias[0]["net"] == "GND"

    # The success report reached the status path — no dialog in between (the
    # autostage "Overwrote..." line precedes it in the log).
    assert "Updated cell 't' from selection" in messages[-1]
    assert "record(s) updated" in messages[-1]
    assert "Save to write the change." in messages[-1]


def test_refresh_nothing_changed_shows_message_applies_nothing(main_window,
                                                               tmp_path,
                                                               monkeypatch):
    """A plan with no edits and no new records (the selection already matches
    the cell) is reported as 'Nothing changed' and touches none of the dock's
    lists — the non-dialog branch of _finish_refresh_geometry."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    before_components = [dict(c) for c in dock._components]
    before_vias = [dict(v) for v in dock._vias]

    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock._finish_refresh_geometry({"plan": RefreshPlan([], [], [])})

    assert len(messages) == 1
    assert "Nothing changed" in messages[0]
    assert dock._components == before_components
    assert dock._vias == before_vias
    assert dock._tracks == []


def test_refresh_geometry_validation_error_shows_warning_tables_untouched(
        main_window, tmp_path, monkeypatch):
    """A structural mismatch (selection is the wrong cluster) -> the full error
    text goes to QMessageBox.warning and the dock's lists/tables do not change."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    before_components = [dict(c) for c in dock._components]

    # Live board is a DIFFERENT cluster: only the CAP role, no ORIG (origin).
    board = _RefreshBoard([_refresh_dto_fp("R-CAP", "CAP", 5.0, 0.0)],
                          roles={"R-CAP": "CAP"})
    warnings = []
    monkeypatch.setattr(cell_editor_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a))

    result = dock._run_refresh_geometry(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks)})
    assert "error" in result
    dock._finish_refresh_geometry(result)

    assert len(warnings) == 1
    assert "zero-offset origin" in warnings[0][2]
    assert dock._components == before_components
    assert dock._vias == [{"offset_along_mm": 0.5, "offset_across_mm": 1.5,
                           "net": "GND"}]
    assert dock.vias_table.rowCount() == 1


# ── H.2.3 / H.2.4: symmetric Refresh — removal + the per-record Log report ──
# plan_2026_09_10_cell_refresh_symmetric_and_no_dialog.md.

def test_apply_refresh_plan_removes_exactly_the_plan_records(main_window,
                                                             tmp_path,
                                                             monkeypatch):
    """_apply_refresh_plan drops the plan's removed_* records from the dock's
    lists BY IDENTITY (`id()`), not by value: two byte-identical records may
    legitimately coexist and only the one the plan unpaired may go. Returns the
    removed count and still autostages."""
    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    doomed = dock._vias[0]
    twin = dict(doomed)                  # equal by VALUE, a different object
    dock._vias.append(twin)

    staged = []
    monkeypatch.setattr(dock, "_autostage", lambda: staged.append(True))

    updated, added, removed = dock._apply_refresh_plan(
        RefreshPlan([], [], [], removed_via_records=[doomed]))

    assert (updated, added, removed) == (0, 0, 1)
    # Identity, not equality: the twin is byte-identical, so `in` would match it.
    assert all(v is not doomed for v in dock._vias)
    assert any(v is twin for v in dock._vias)
    assert dock.vias_table.rowCount() == 1
    assert staged == [True]


def test_finish_refresh_reports_each_added_and_removed_record(main_window,
                                                              tmp_path,
                                                              monkeypatch):
    """H.2.4: one '+ '/'- ' Log line per added/removed record BEFORE the
    summary, and the summary carries the removed counter — "в лог говорим:
    добавили то-то, удалили то-то" (Denis)."""
    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))
    monkeypatch.setattr(dock, "_autostage", lambda: None)

    added = {"net": "/N", "layer": "F.Cu", "width_mm": 0.254,
             "start_along_mm": 2.135, "start_across_mm": -5.04,
             "end_along_mm": 3.335, "end_across_mm": -5.04}
    removed = {"net_from_role": "C_OUT_BYPASS", "net_from_role_pad": "1",
               "layer": "B.Cu", "width_mm": 0.65,
               "start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 1.0, "end_across_mm": 0.0}
    # The removed record must actually BE in the dock's list — the counter
    # reports what was dropped, not what the plan wished for.
    dock._tracks.append(removed)
    plan = RefreshPlan([], [], [], new_track_records=[added],
                       removed_track_records=[removed])

    dock._finish_refresh_geometry({"plan": plan})

    lines = [m for m in messages if m.startswith("+ ") or m.startswith("- ")]
    assert len(lines) == 2
    assert lines[0].startswith("+ track /N F.Cu w=0.254")
    assert "(2.135,-5.04) -> (3.335,-5.04)" in lines[0]
    assert lines[1].startswith("- track net_from_role C_OUT_BYPASS/1 B.Cu")
    assert "1 record(s) removed" in messages[-1]


def test_finish_refresh_prints_the_frames_warnings(main_window, tmp_path,
                                                  monkeypatch):
    """J.1 (2026-09-10): the plan's honest frame warnings (non-rigid cluster /
    turned instance) are printed in the Log as their OWN lines — a warning-only
    plan must not fall into the "nothing changed" branch."""
    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))
    monkeypatch.setattr(dock, "_autostage", lambda: None)

    plan = RefreshPlan([], [], [], warnings=["the instance is rotated -90°"])
    dock._finish_refresh_geometry({"plan": plan})

    assert "the instance is rotated -90°" in messages


def test_record_report_line_formats_via_track_and_missing_net():
    """The one formatting helper both directions and both sections share."""
    from gui.docks.cell_editor import record_report_line

    via = {"net_from_role": "C_IN_BULK", "net_from_role_pad": "1",
           "offset_along_mm": 0.8625, "offset_across_mm": 1.374}
    line = record_report_line("+", via, "via")
    assert line.startswith("+ via net_from_role C_IN_BULK/1 ")
    assert "(0.8625,1.374)" in line

    bare = {"net": None, "offset_along_mm": 0.0, "offset_across_mm": 0.0}
    assert "(no net)" in record_report_line("-", bare, "via")

    literal = {"net": "/N", "start_along_mm": 0.0, "start_across_mm": 0.0,
               "end_along_mm": 1.0, "end_across_mm": 0.0, "width_mm": 0.65}
    assert "- track /N w=0.65" in record_report_line("-", literal, "track")


# ── Import vias/tracks from selection (2026-09-03, plan
#    fpga_oscill_missing_copper_and_cell_import §B.3) ──────────────────────

def _import_dto_track(net, x1_mm, y1_mm, x2_mm, y2_mm):
    return Track(uuid=f"t-{net}", net_name=net,
                 start=Vector2.from_xy_mm(x1_mm, y1_mm),
                 end=Vector2.from_xy_mm(x2_mm, y2_mm),
                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)


class _ImportBoard:
    """connection.board stand-in for the import path: like _RefreshBoard but
    the adapter also reports EMPTY pad lists — build_import_plan calls
    _selection_role_nets (needs get_footprint_pads), and no pad evidence makes
    the net classifier fall back to a literal net (same as the module test's
    empty-pads adapter)."""
    def __init__(self, items, roles=None):
        self.adapter = SimpleNamespace(
            refresh_board=lambda: None,   # K.1 live-read hook
            get_selected_items=lambda: list(items),
            get_field_value=lambda fp, name: (roles or {}).get(fp.ref),
            get_footprint_pads=lambda fp: [])


def test_import_button_enabled_only_with_board_and_components(main_window, tmp_path):
    """§B.3 activity: the import button shares Refresh's gate — adapter present
    AND the loaded cell has components."""
    dock, _ = _make_dock(main_window, tmp_path, {"cells": {"t": {"components": []}}})
    dock.load_entry("t")
    # No board yet — refresh_known_roles isn't called, button stays disabled.
    assert not dock.import_vias_tracks_button.isEnabled()

    # Board present but cell still empty.
    main_window.connection.board = _RefreshBoard([])
    dock.refresh_known_roles([])
    assert not dock.import_vias_tracks_button.isEnabled()

    # Load a cell WITH components while the board is present.
    dock.load_entry("t", None)
    dock._components.append({"role": "ORIG", "offset_along_mm": 0.0,
                             "offset_across_mm": 0.0})
    dock._refresh_all_tables()
    assert dock.import_vias_tracks_button.isEnabled()


def test_import_apply_appends_new_records_keeps_existing(main_window, tmp_path,
                                                         monkeypatch):
    """A clean import plan -> Apply APPENDS only the genuinely-new via/track
    records; the existing GND via (claimed by its live counterpart in tier 2)
    is neither duplicated nor mutated. Preview dialog is stubbed to Accept so
    the real finish->apply path runs."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    existing_via = dock._vias[0]
    via_snapshot = dict(existing_via)

    # Live: ORIG (origin) + CAP at new positions, the existing GND via's live
    # counterpart, and genuinely-new via + track copper the cell lacks.
    board = _ImportBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.5, 9.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _refresh_dto_via("NEW_NET", 12.0, 13.0),
         _import_dto_track("NEW_NET", 12.0, 12.0, 14.0, 12.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    accepted = {"value": False}

    class _AcceptDialog:
        def __init__(self, rows, parent=None):
            self.rows = rows

        def exec(self):
            accepted["value"] = True
            return 1  # QDialog.Accepted
    monkeypatch.setattr(cell_editor_mod, "_ImportPreviewDialog", _AcceptDialog)

    result = dock._run_import_vias_tracks(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks)})
    assert "plan" in result
    plan = result["plan"]
    # Only the genuinely-new copper is imported; the existing GND via is
    # claimed by tier 2 (its live counterpart), never duplicated.
    assert [r["net"] for r in plan.new_via_records] == ["NEW_NET"]
    assert [r["net"] for r in plan.new_track_records] == ["NEW_NET"]

    dock._finish_import_vias_tracks(result)
    assert accepted["value"] is True

    # Existing GND via: same dict object, untouched.
    assert dock._vias[0] is existing_via
    assert existing_via == via_snapshot
    # Brand-new records APPENDED (extend, not replace) with geometry relative
    # to the ORIG origin at (10.0, 10.0).
    assert len(dock._vias) == 2
    assert dock._vias[1]["net"] == "NEW_NET"
    assert dock._vias[1]["offset_along_mm"] == 2.0
    assert dock._vias[1]["offset_across_mm"] == 3.0
    assert len(dock._tracks) == 1
    assert dock._tracks[0]["net"] == "NEW_NET"
    assert dock._tracks[0]["start_along_mm"] == 2.0
    assert dock._tracks[0]["end_along_mm"] == 4.0
    assert dock.vias_table.rowCount() == 2
    assert dock.tracks_table.rowCount() == 1


def test_import_preview_rows_lists_only_new_records(main_window, tmp_path):
    """import_preview_rows is pure and shows one Kind/Position/Net row per NEW
    record — nothing about existing ones (the additive counterpart of the
    refresh preview's old/new/Δ rows)."""
    from kicadstamp.cell_geometry_refresh import ImportPlan
    plan = ImportPlan(
        new_via_records=[{"offset_along_mm": 2.0, "offset_across_mm": 3.0,
                          "net": "NEW_NET"}],
        new_track_records=[{"start_along_mm": 0.0, "start_across_mm": 0.0,
                            "end_along_mm": 4.0, "end_across_mm": 0.0,
                            "net_from_role": "CAP"}])
    rows = cell_editor_mod.import_preview_rows(plan)
    assert len(rows) == 2
    assert rows[0] == ["Via", "(2.0000, 3.0000)", "NEW_NET"]
    assert rows[1] == ["Track", "(0.0000, 0.0000) → (4.0000, 0.0000)", "role:CAP"]


def test_import_validation_error_shows_warning_tables_untouched(
        main_window, tmp_path, monkeypatch):
    """A structural mismatch is NOT softened by Import — a wrong cluster
    (missing the zero-offset origin role) goes to QMessageBox.warning and the
    dock's lists/tables do not change."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    before_vias = [dict(v) for v in dock._vias]

    board = _ImportBoard([_refresh_dto_fp("R-CAP", "CAP", 5.0, 0.0)],
                         roles={"R-CAP": "CAP"})
    warnings = []
    monkeypatch.setattr(cell_editor_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a))

    result = dock._run_import_vias_tracks(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks)})
    assert "error" in result
    dock._finish_import_vias_tracks(result)

    assert len(warnings) == 1
    assert "zero-offset origin" in warnings[0][2]
    assert dock._vias == before_vias
    assert dock._tracks == []
    assert dock.vias_table.rowCount() == 1


# ── Refresh additive (2026-09-05, plan update_from_selection_adds_copper): the
#    'Update from selection...' path now ADDS copper the cell does not describe ──

def test_refresh_additive_appends_new_copper_and_updates_existing(
        main_window, tmp_path, monkeypatch):
    """The pif_p5v scenario end-to-end through the dock: live copper the loaded
    cell's records don't describe is no longer an 'extra copper' fatal — the
    refresh applies the plan DIRECTLY (no preview dialog since 2026-09-06) and
    APPENDS the new copper (extend), while the existing GND via is matched and
    its geometry updated."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    existing_via = dock._vias[0]
    via_snapshot = dict(existing_via)
    orig_cap = next(c for c in dock._components if c["role"] == "CAP")

    # Live: ORIG (origin) + CAP moved, the existing GND via's live counterpart
    # MOVED (so its geometry updates), and genuinely-new via + track copper the
    # cell has no record for (the drawn-copper case).
    board = _RefreshBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.5, 9.0),
         _refresh_dto_via("GND", 11.0, 13.0),
         _refresh_dto_via("NEW_NET", 12.0, 13.0),
         _import_dto_track("NEW_NET", 12.0, 12.0, 14.0, 12.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks)})
    assert "plan" in result
    plan = result["plan"]
    assert [r["net"] for r in plan.new_via_records] == ["NEW_NET"]
    assert [r["net"] for r in plan.new_track_records] == ["NEW_NET"]
    # The existing GND via is matched for a geometry update, never re-imported.
    assert len(plan.via_updates) == 1 and len(plan.new_via_records) == 1

    dock._finish_refresh_geometry(result)

    # Existing GND via: same dict object, geometry updated to (1.0, 3.0).
    assert dock._vias[0] is existing_via
    assert dock._vias[0]["offset_along_mm"] == 1.0
    assert dock._vias[0]["offset_across_mm"] == 3.0
    # CAP component moved along with the refresh.
    assert next(c for c in dock._components if c["role"] == "CAP") is orig_cap
    assert next(c for c in dock._components if c["role"] == "CAP")["offset_along_mm"] == 1.5
    # Brand-new records APPENDED (extend), geometry relative to the ORIG origin.
    assert len(dock._vias) == 2
    assert dock._vias[1]["net"] == "NEW_NET"
    assert dock._vias[1]["offset_along_mm"] == 2.0
    assert dock._vias[1]["offset_across_mm"] == 3.0
    assert len(dock._tracks) == 1
    assert dock._tracks[0]["net"] == "NEW_NET"
    assert dock._tracks[0]["start_along_mm"] == 2.0
    assert dock._tracks[0]["end_along_mm"] == 4.0
    assert dock.vias_table.rowCount() == 2
    assert dock.tracks_table.rowCount() == 1

    # The success report reached the status path — applied, no dialog (the
    # autostage "Overwrote..." line precedes it in the log).
    assert "Updated cell 't' from selection" in messages[-1]
    assert "via/track record(s) added" in messages[-1]


# ── Copy placement from cell (2026-09-06, plan copy_placement_from_cell) ──

def _copy_donor_components():
    """pif_p5v-like donor: components + geometry (net_template = donor rail)."""
    return [
        {"role": "C_IN_BYPASS", "offset_along_mm": 4.5, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "+5V_DIRTY"},
        {"role": "C_OUT_BULK", "offset_along_mm": 7.0, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "+5V"},
    ]


def _copy_donor_vias():
    return [{"offset_along_mm": 4.5, "offset_across_mm": 0.2925, "drill_mm": 0.4,
             "diameter_mm": 0.8, "net_from_role": "C_IN_BYPASS",
             "net_from_role_pad": "2"}]


def _copy_donor_tracks():
    return [{"start_along_mm": 7.0, "start_across_mm": -1.4825,
             "end_along_mm": 9.8, "end_across_mm": -1.4825, "width_mm": 0.8,
             "net_from_role": "C_OUT_BULK", "net_from_role_pad": "2"}]


def _copy_target_components():
    """pif_n5v-like target: same roles/geometry frame as the donor but its own
    (-5V) net_templates; C_OUT_BULK deliberately mis-placed so the overlay is
    observable."""
    return [
        {"role": "C_IN_BYPASS", "offset_along_mm": 4.5, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "-5V_DIRTY"},
        {"role": "C_OUT_BULK", "offset_along_mm": 99.0, "offset_across_mm": -2.345,
         "angle_deg": -90.0, "net_template": "-5V"},
    ]


def _copy_cells_data():
    """A root file carrying a copperless target (tgt), a fully-routed donor and
    an invalid donor (copper references a role the target lacks)."""
    return {"cells": {
        "tgt": {"layer": "F.Cu", "components": _copy_target_components(),
                "vias": [], "tracks": []},
        "donor": {"layer": "F.Cu", "components": _copy_donor_components(),
                  "vias": _copy_donor_vias(), "tracks": _copy_donor_tracks()},
        "ghost": {"layer": "F.Cu", "components": _copy_donor_components(),
                  "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                            "drill_mm": 0.3, "diameter_mm": 0.6,
                            "net_from_role": "GHOST"}],
                  "tracks": []},
    }}


class _FakeCopyPicker:
    """Replaces cell_editor._CopyPlacementDialog in the flow tests: reports the
    chosen source and accepts (no event loop)."""
    source_choice = "donor"

    def __init__(self, candidates, parent=None):
        self.candidates = list(candidates)

    def exec(self):
        return 1  # QDialog.DialogCode.Accepted

    def source(self):
        return self.source_choice


def test_copy_placement_dialog_lists_candidates_and_gates_copy(main_window):
    """The MINIMAL donor picker (Denis 2026-09-06) holds only a combobox of the
    fitting donor cells + Copy/Cancel; Copy is disabled while no source text."""
    dialog = cell_editor_mod._CopyPlacementDialog(["donor", "ghost"])
    texts = [dialog.source_combo.itemText(i)
             for i in range(dialog.source_combo.count())]
    assert texts == ["donor", "ghost"]
    assert dialog.source() == "donor"
    assert dialog.copy_button.isEnabled()

    dialog.source_combo.setCurrentText("")
    dialog._on_source_changed()
    assert not dialog.copy_button.isEnabled()


def test_copy_placement_from_cell_applies_selected_donor(main_window, tmp_path,
                                                         monkeypatch):
    """Picking the donor in the minimal picker copies: geometry overlaid onto
    the SAME target dicts (net_template kept), donor copper appended."""
    dock, target_file = _make_dock(main_window, tmp_path, _copy_cells_data())
    dock.load_entry("tgt", target_file)
    _FakeCopyPicker.source_choice = "donor"
    monkeypatch.setattr(cell_editor_mod, "_CopyPlacementDialog", _FakeCopyPicker)

    dock.copy_placement_from_cell()

    bulk = next(c for c in dock._components if c["role"] == "C_OUT_BULK")
    assert bulk["offset_along_mm"] == 7.0        # donor geometry applied
    assert bulk["net_template"] == "-5V"         # target net_template kept
    assert len(dock._vias) == 1
    assert dock._vias[0]["net_from_role"] == "C_IN_BYPASS"
    assert len(dock._tracks) == 1


def test_copy_placement_from_cell_fatal_warns_without_applying(main_window,
                                                               tmp_path,
                                                               monkeypatch):
    """A donor whose copper references a role the target lacks is rejected with
    a warning BEFORE anything changes — no silent copy of garbage."""
    dock, target_file = _make_dock(main_window, tmp_path, _copy_cells_data())
    dock.load_entry("tgt", target_file)
    _FakeCopyPicker.source_choice = "ghost"
    monkeypatch.setattr(cell_editor_mod, "_CopyPlacementDialog", _FakeCopyPicker)

    class _FakeMsgBox:
        warnings = []

        @classmethod
        def warning(cls, parent, title, text):
            cls.warnings.append((title, text))

    monkeypatch.setattr(cell_editor_mod, "QMessageBox", _FakeMsgBox)

    dock.copy_placement_from_cell()

    assert len(_FakeMsgBox.warnings) == 1
    assert "GHOST" in _FakeMsgBox.warnings[0][1]
    assert dock._vias == [] and dock._tracks == []
    bulk = next(c for c in dock._components if c["role"] == "C_OUT_BULK")
    assert bulk["offset_along_mm"] == 99.0


def test_copy_placement_no_fitting_donor_shows_message(main_window, tmp_path,
                                                       caplog):
    """With no other role-compatible cell in the graph the copy is a no-op with
    a clear message (never opens an empty picker)."""
    dock, target_file = _make_dock(main_window, tmp_path, {
        "cells": {"tgt": {"layer": "F.Cu",
                          "components": _copy_target_components(),
                          "vias": [], "tracks": []}}})
    dock.load_entry("tgt", target_file)

    dock.copy_placement_from_cell()

    assert dock._vias == [] and dock._tracks == []
    assert any("fits" in r.message for r in caplog.records)


# ── N (2026-09-11): nested clone_placements in "Update from selection" ──────

def test_apply_refresh_plan_applies_nested_updates_and_drops_defaults(
        main_window, tmp_path, monkeypatch):
    """N: a nested clone_placement's update writes ONLY what the live board
    says and REMOVES the keys that became their default (the nested editor's own
    dict convention) — an update back to the origin clears xy/rotation/mirror."""
    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    record = {"name": "n1", "cell": "sub", "xy": [5.0, 6.0],
              "rotation_deg": 90.0, "mirror": True}
    dock._nested = [record]
    staged = []
    monkeypatch.setattr(dock, "_autostage", lambda: staged.append(True))

    updated, added, removed = dock._apply_refresh_plan(
        RefreshPlan([], [], [], nested_updates=[(record, {"xy": [1.0, 2.0]})]))

    assert (updated, added, removed) == (1, 0, 0)
    assert record["xy"] == [1.0, 2.0]
    assert "rotation_deg" not in record       # 90 -> 0 (the default) is REMOVED
    assert "mirror" not in record             # True -> False is REMOVED
    assert staged == [True]


def test_finish_refresh_prints_the_nested_report_lines(main_window, tmp_path,
                                                       monkeypatch):
    """N: the per-nested Log lines are printed next to the summary, exactly like
    the via/track added/removed lines (H.2.4 reporting style)."""
    from kicadstamp.cell_geometry_refresh import RefreshPlan
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(
        dock, "_show_message",
        lambda text, style=None: messages.append(text) or True)
    monkeypatch.setattr(dock, "_apply_refresh_plan", lambda plan: (1, 0, 0))

    dock._finish_refresh_geometry({"plan": RefreshPlan(
        [], [], [], nested_updates=[({"name": "n1"}, {"xy": [1.0, 2.0]})],
        nested_reports=["nested 'n1' (cell:sub): xy (0.0, 0.0) rot 0.0° "
                        "mirror=false -> xy (1.0, 2.0) rot 0.0° mirror=false"])})

    assert any("nested 'n1'" in m for m in messages)


# ── Э5: the per-read layer filter (plan_2026_09_12_cell_layer_dialog) ────────
#
# The layer set is decided on the UI thread and travels in the payload; the
# worker keeps only the tracks on it (gui/board_layers.filter_tracks_by_layers),
# so copper on an unchecked layer is INVISIBLE to the matcher instead of looking
# like copper the cell does not describe. For Refresh that also means its
# records are removed (remove_missing) — "not read" and "deleted" are one
# action; for Import it only means "not added".

def _dto_track_on(layer, net, x1_mm, y1_mm, x2_mm, y2_mm):
    """A live Track on a CHOSEN layer — _import_dto_track is F.Cu-only."""
    return Track(uuid=f"t-{net}-{layer.name}", net_name=net,
                 start=Vector2.from_xy_mm(x1_mm, y1_mm),
                 end=Vector2.from_xy_mm(x2_mm, y2_mm),
                 width_mm=0.25, layer=layer)


def _cell_with_two_layer_tracks():
    """_loaded_cell_data() plus TWO track records of the same net and shape, one
    per outer layer: the F.Cu one carries no `layer` key (it IS the cell's own
    layer), the B.Cu one names its layer explicitly — exactly what extract
    writes, so the two differ ONLY by layer and the filter can be told apart."""
    data = _loaded_cell_data()
    data["cells"]["t"]["tracks"] = [
        {"start_along_mm": 0.0, "start_across_mm": 4.0,
         "end_along_mm": 1.0, "end_across_mm": 4.0,
         "width_mm": 0.25, "net": "GND"},
        {"start_along_mm": 0.0, "start_across_mm": 6.0,
         "end_along_mm": 1.0, "end_across_mm": 6.0,
         "width_mm": 0.25, "net": "GND", "layer": "B.Cu"},
    ]
    return data


def _two_layer_board(board_cls, extra_items=()):
    """ORIG/CAP exactly at their recorded offsets (a clean frame), the GND via's
    live counterpart, and one live track per outer layer at each record's
    position — the F.Cu one moved 0.2mm across so its update is observable."""
    return board_cls(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.0, 10.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _dto_track_on(BoardLayer.BL_F_Cu, "GND", 10.0, 14.2, 11.0, 14.2),
         _dto_track_on(BoardLayer.BL_B_Cu, "GND", 10.0, 16.0, 11.0, 16.0),
         *extra_items],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})


def test_refresh_with_all_layers_keeps_the_old_behaviour(main_window, tmp_path):
    """ALL_COPPER_LAYERS — and a payload with no `layers` key at all, which is
    what every pre-existing caller passes — reads the whole selection: the live
    B.Cu copper the cell does not describe is still READ, so it becomes a new
    record exactly as before the filter existed."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _RefreshBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.0, 10.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _dto_track_on(BoardLayer.BL_B_Cu, "NEW_NET", 10.0, 14.0, 11.0, 14.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})
    base = {"board": board, "components": list(dock._components),
            "vias": list(dock._vias), "tracks": list(dock._tracks),
            "cell_layer": "F.Cu"}

    results = [dock._run_refresh_geometry(dict(base)),
               dock._run_refresh_geometry({**base, "layers": ALL_COPPER_LAYERS})]

    for result in results:
        assert "error" not in result
        plan = result["plan"]
        assert [(r["net"], r.get("layer")) for r in plan.new_track_records] == [
            ("NEW_NET", "B.Cu")]
        assert plan.removed_track_records == []


def test_refresh_unchecked_layer_is_not_read_at_all(main_window, tmp_path):
    """Э7.1: the same selection with B.Cu unchecked — the B.Cu copper stops
    existing for the read (no phantom 'the cell does not describe it' record),
    while the layer-less vias are untouched by the filter (P.2/Э7.7)."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _RefreshBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.0, 10.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _refresh_dto_via("NEW_VIA", 12.0, 13.0),
         _dto_track_on(BoardLayer.BL_B_Cu, "NEW_NET", 10.0, 14.0, 11.0, 14.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})

    result = dock._run_refresh_geometry(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks),
         "cell_layer": "F.Cu", "layers": {"F.Cu"}})

    assert "error" not in result
    plan = result["plan"]
    assert plan.new_track_records == []
    assert plan.removed_track_records == []
    assert [r["net"] for r in plan.new_via_records] == ["NEW_VIA"]


def test_refresh_removes_the_records_of_an_unchecked_layer(main_window, tmp_path,
                                                           monkeypatch):
    """Э7.2: a layer that is not read gives its records no live pair, so
    remove_missing drops them — and each one is named in the Log with a '- '
    line (the H.2.4 report), which is what keeps a week-old unchecked box from
    looking like silent damage."""
    dock, _ = _make_dock(main_window, tmp_path, _cell_with_two_layer_tracks())
    dock.load_entry("t")
    f_rec, b_rec = dock._tracks
    board = _two_layer_board(_RefreshBoard)

    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(
        {"board": board, "components": list(dock._components),
         "vias": list(dock._vias), "tracks": list(dock._tracks),
         "cell_layer": "F.Cu", "layers": {"F.Cu"}})
    assert "error" not in result
    plan = result["plan"]

    assert [rec for rec, _geo in plan.track_updates] == [f_rec]
    assert plan.removed_track_records == [b_rec]
    assert plan.new_track_records == []

    dock._finish_refresh_geometry(result)

    assert dock._tracks == [f_rec]
    assert any(m.startswith("- ") and "B.Cu" in m for m in messages)
    assert "1 record(s) removed" in messages[-1]


def test_import_with_an_unchecked_layer_never_adds_that_layer(main_window, tmp_path):
    """Э7.1 for the additive path: for Import a checked-off layer is simply 'not
    added' — both layers live in the selection give two new records, F.Cu alone
    gives one."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _ImportBoard(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.0, 10.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _dto_track_on(BoardLayer.BL_F_Cu, "NEW_NET", 10.0, 14.0, 11.0, 14.0),
         _dto_track_on(BoardLayer.BL_B_Cu, "NEW_NET", 10.0, 16.0, 11.0, 16.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})
    base = {"board": board, "components": list(dock._components),
            "vias": list(dock._vias), "tracks": list(dock._tracks),
            "cell_layer": "F.Cu"}

    all_layers = dock._run_import_vias_tracks(
        {**base, "layers": ALL_COPPER_LAYERS})
    f_cu_only = dock._run_import_vias_tracks({**base, "layers": {"F.Cu"}})

    assert "error" not in all_layers and "error" not in f_cu_only
    assert [(r["net"], r.get("layer"))
            for r in all_layers["plan"].new_track_records] == [
        ("NEW_NET", None), ("NEW_NET", "B.Cu")]
    assert [(r["net"], r.get("layer"))
            for r in f_cu_only["plan"].new_track_records] == [
        ("NEW_NET", None)]


# ── Э3: the remembered set and the dialog wiring ─────────────────────────────
# plan_2026_09_12_cell_layer_dialog. The dialog and its two-phase opening have
# their own tests (tests/gui/test_cell_layers.py) — what is asserted here is the
# DOCK's two seams: the fast path takes the remembered set, and the dialog path
# hands the opener the selection it was fed.

def _capture_payloads(monkeypatch):
    """Record the payload of every read the dock starts.

    Returns None like the refused-op path does: the dock keeps the controller in
    `_active_op` and bails out of a second start while it is set, and these tests
    deliberately start the same read twice."""
    payloads = []

    def _start(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        payloads.append(args[0] if args else None)
        return None

    monkeypatch.setattr(cell_editor_mod, "start_long_op", _start)
    return payloads


def test_the_fast_path_runs_with_the_remembered_layer_set(main_window, tmp_path,
                                                          monkeypatch):
    """P.3.1: one click, no dialog, and NO board read for the set itself — the
    payload carries what the dialog last remembered (None = nothing remembered
    yet = every layer, i.e. the old behaviour)."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _RefreshBoard([])
    payloads = _capture_payloads(monkeypatch)

    dock._on_refresh_geometry()
    assert payloads[-1]["layers"] is None

    remember_read_layers(["F.Cu"])
    dock._on_refresh_geometry()
    assert payloads[-1]["layers"] == ["F.Cu"]

    # The import path reads the same remembered set.
    dock._on_import_vias_tracks()
    assert payloads[-1]["layers"] == ["F.Cu"]


def test_the_refresh_dialog_path_is_fed_the_distributed_selection(
        main_window, tmp_path, monkeypatch):
    """P.3.4: the opener gets the items the ~400 ms tick distributed — the dock
    never calls get_selected_items() on the UI thread — and the dialog's answer
    becomes the payload of the read it continues into."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _RefreshBoard([])
    track = _dto_track_on(BoardLayer.BL_F_Cu, "GND", 1.0, 1.0, 2.0, 1.0)
    dock.set_board_selection([track], [])
    calls = {}

    def _open(parent, connection, adapter, items, on_ok, widgets=(), on_error=None):
        calls.update(parent=parent, items=list(items), on_ok=on_ok,
                     widgets=tuple(widgets))
        return "controller"

    monkeypatch.setattr(cell_editor_mod, "open_cell_layers_dialog", _open)
    payloads = _capture_payloads(monkeypatch)

    dock._on_refresh_geometry_with_layers()

    assert calls["parent"] is dock
    assert calls["items"] == [track]
    assert calls["widgets"] == (dock.refresh_geometry_button,
                                dock.import_vias_tracks_button)

    calls["on_ok"](["B.Cu"])
    assert payloads[-1]["layers"] == ["B.Cu"]


def test_the_import_dialog_path_continues_into_the_import_read(
        main_window, tmp_path, monkeypatch):
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _ImportBoard([])
    calls = {}

    def _open(parent, connection, adapter, items, on_ok, widgets=(), on_error=None):
        calls["on_ok"] = on_ok
        return "controller"

    monkeypatch.setattr(cell_editor_mod, "open_cell_layers_dialog", _open)
    payloads = _capture_payloads(monkeypatch)

    dock._on_import_vias_tracks_with_layers()
    calls["on_ok"](["F.Cu"])

    assert payloads[-1]["layers"] == ["F.Cu"]


def test_the_dialog_path_without_a_board_starts_nothing(main_window, tmp_path,
                                                        monkeypatch):
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))
    started = []
    monkeypatch.setattr(cell_editor_mod, "open_cell_layers_dialog",
                        lambda *args, **kwargs: started.append(args))

    dock._on_refresh_geometry_with_layers()

    assert started == []
    assert messages and messages[-1] == "Connect to KiCad first."


# ── Э4: the per-read layer report and the dialog entry point ────────────────

def _two_layer_selection_board(board_cls=None):
    """ORIG/CAP at their recorded offsets, the GND via's live counterpart and one
    live track PER OUTER LAYER — the smallest selection where the layer report has
    something to say about a layer it left out."""
    cls = board_cls or _RefreshBoard
    return cls(
        [_refresh_dto_fp("R-ORIG", "ORIG", 10.0, 10.0),
         _refresh_dto_fp("R-CAP", "CAP", 11.0, 10.0),
         _refresh_dto_via("GND", 10.5, 11.5),
         _dto_track_on(BoardLayer.BL_F_Cu, "GND", 10.0, 14.0, 11.0, 14.0),
         _dto_track_on(BoardLayer.BL_B_Cu, "GND", 10.0, 16.0, 11.0, 16.0)],
        roles={"R-ORIG": "ORIG", "R-CAP": "CAP"})


def _refresh_payload(dock, board, **extra):
    payload = {"board": board, "components": list(dock._components),
               "vias": list(dock._vias), "tracks": list(dock._tracks),
               "cell_layer": "F.Cu"}
    payload.update(extra)
    return payload


def test_the_fast_path_reports_the_layers_it_read(main_window, tmp_path, monkeypatch):
    """Э5/Э4: the fast path runs without a dialog, so these Log lines are the ONLY
    place that says which layers the read looked at — and which the remembered set
    left out (and why). Without them a week-old unchecked layer would silently
    stop being read."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(
        _refresh_payload(dock, _two_layer_selection_board(), layers={"F.Cu"}))
    dock._finish_refresh_geometry(result)

    assert "read layers: F.Cu" in messages
    assert "skipped B.Cu: unchecked by hand" in messages


def test_the_dialog_path_reports_the_empty_layers_too(main_window, tmp_path, monkeypatch):
    """The «пусто в выделении» names come from the dialog (a layer without copper
    leaves no trace in the selection), and they reach the same report."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(
        _refresh_payload(dock, _two_layer_selection_board(), layers={"F.Cu"},
                         empty_layers=["In1.Cu"]))
    dock._finish_refresh_geometry(result)

    assert "read layers: F.Cu" in messages
    assert "skipped In1.Cu: empty in the selection" in messages


def test_the_import_path_reports_its_layers_too(main_window, tmp_path, monkeypatch):
    """The report is not a refresh-only nicety: Import says the same thing, and it
    is printed BEFORE the "Nothing to import" branch."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    class _Reject:
        def __init__(self, rows, parent=None):
            pass

        def exec(self):
            return 0
    monkeypatch.setattr(cell_editor_mod, "_ImportPreviewDialog", _Reject)

    result = dock._run_import_vias_tracks(
        _refresh_payload(dock, _two_layer_selection_board(_ImportBoard),
                         layers={"F.Cu"}))
    dock._finish_import_vias_tracks(result)

    assert "read layers: F.Cu" in messages
    assert "skipped B.Cu: unchecked by hand" in messages


def test_the_requested_entry_points_can_choose_layers(main_window, tmp_path,
                                                      monkeypatch):
    """Э4: the "... (choose layers)..." legs of BOTH context-menu items reach the
    dialog path of the same read, while the plain items keep the fast one."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    called = []
    monkeypatch.setattr(dock, "_on_refresh_geometry_with_layers",
                        lambda: called.append("refresh-dialog"))
    monkeypatch.setattr(dock, "_on_refresh_geometry",
                        lambda: called.append("refresh-fast"))
    monkeypatch.setattr(dock, "_on_import_vias_tracks_with_layers",
                        lambda: called.append("import-dialog"))
    monkeypatch.setattr(dock, "_on_import_vias_tracks",
                        lambda: called.append("import-fast"))

    dock.refresh_from_selection_requested("t", dock._path, choose_layers=True)
    dock.refresh_from_selection_requested("t", dock._path)
    dock.import_from_selection_requested("t", dock._path, choose_layers=True)
    dock.import_from_selection_requested("t", dock._path)

    assert called == ["refresh-dialog", "refresh-fast",
                      "import-dialog", "import-fast"]


# ── Э2: the hidden-copper-layer warning ─────────────────────────────────────
# Read INSIDE the worker (it already holds the socket and is already reading the
# whole selection), so it is as fresh as the read itself and appears on the fast
# path too — where a hidden layer is most dangerous, because its copper cannot be
# selected and Refresh would delete its records silently.

_KIPY_F = KipyBoardLayer.BL_F_Cu
_KIPY_IN1 = KipyBoardLayer.BL_In1_Cu
_KIPY_IN2 = KipyBoardLayer.BL_In2_Cu
_KIPY_B = KipyBoardLayer.BL_B_Cu
_KIPY_SILK = KipyBoardLayer.BL_F_SilkS

_KIPY_NAMES = {_KIPY_F: "F.Cu", _KIPY_IN1: "In1.Cu", _KIPY_IN2: "In2.Cu",
               _KIPY_B: "B.Cu", _KIPY_SILK: "F.Silkscreen"}


class _LayersBoard:
    """The live-board handle the Э2 check reads. `visible` is MUTABLE on purpose:
    the freshness guard changes it between two reads."""

    def __init__(self, enabled=(_KIPY_F, _KIPY_IN1, _KIPY_IN2, _KIPY_B),
                 visible=None):
        self.enabled = list(enabled)
        self.visible = list(enabled if visible is None else visible)

    def get_enabled_layers(self):
        return list(self.enabled)

    def get_visible_layers(self):
        return list(self.visible)

    def get_layer_name(self, layer):
        return _KIPY_NAMES[layer]


def _hidden_warnings(caplog):
    return [r.message for r in caplog.records
            if "hidden copper layers" in r.message]


def test_the_fast_path_warns_about_hidden_copper_layers(main_window, tmp_path,
                                                        caplog):
    """Э2/Э7.6: the warning is emitted by the worker, so the FAST path (no
    dialog, no layer list on screen) is covered as well."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _two_layer_selection_board()
    board.adapter._board = _LayersBoard(visible=[_KIPY_F, _KIPY_IN2, _KIPY_B])

    dock._run_refresh_geometry(_refresh_payload(dock, board))

    assert _hidden_warnings(caplog) == [
        "hidden copper layers on the board: In1.Cu — the selection in KiCad "
        "cannot see their copper"]


def test_a_hidden_non_copper_layer_warns_about_nothing(main_window, tmp_path,
                                                       caplog):
    """Only copper is read, so only copper is warned about."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _two_layer_selection_board()
    board.adapter._board = _LayersBoard(
        enabled=[_KIPY_F, _KIPY_IN1, _KIPY_IN2, _KIPY_B, _KIPY_SILK],
        visible=[_KIPY_F, _KIPY_IN1, _KIPY_IN2, _KIPY_B])   # silk hidden only

    dock._run_refresh_geometry(_refresh_payload(dock, board))

    assert _hidden_warnings(caplog) == []


def test_the_hidden_layer_warning_is_as_fresh_as_the_read(main_window, tmp_path,
                                                          caplog):
    """Э7.6's freshness guard: the layer stops being hidden between two reads, and
    the warning appears on the FIRST only. This is what tells a worker-side read
    from a list cached in the dock (which would keep warning about a layer the
    user has already shown again)."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _two_layer_selection_board()
    live = _LayersBoard(visible=[_KIPY_F, _KIPY_IN2, _KIPY_B])   # In1.Cu hidden
    board.adapter._board = live

    dock._run_refresh_geometry(_refresh_payload(dock, board))
    assert len(_hidden_warnings(caplog)) == 1

    live.visible = [_KIPY_F, _KIPY_IN1, _KIPY_IN2, _KIPY_B]      # shown again
    caplog.clear()
    dock._run_refresh_geometry(_refresh_payload(dock, board))
    assert _hidden_warnings(caplog) == []


def test_the_import_worker_warns_about_hidden_copper_layers_too(main_window,
                                                                tmp_path,
                                                                caplog):
    """A hidden layer is a BOARD fact, not a refresh-only nicety."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    board = _two_layer_selection_board(_ImportBoard)
    board.adapter._board = _LayersBoard(visible=[_KIPY_F, _KIPY_IN2, _KIPY_B])

    dock._run_import_vias_tracks(_refresh_payload(dock, board))

    assert len(_hidden_warnings(caplog)) == 1
    assert "In1.Cu" in _hidden_warnings(caplog)[0]


# ── Э2а: an EMPTY layer set ─────────────────────────────────────────────────
# Empty is a legitimate answer ("nothing" is not "everything": ALL_COPPER_LAYERS
# is None), so the button is never greyed out — but on Refresh an empty set
# deletes every track record of the cell, and the preview dialog was removed on
# 2026-09-06. The confirmation below is therefore the ONLY place the consequence
# can be seen before it happens.

def _answer_question(monkeypatch, answer):
    """Stand in for QMessageBox.question: record the calls, answer `answer`."""
    calls = []

    def _question(parent, title, text, *args, **kwargs):
        calls.append((title, text))
        return answer

    monkeypatch.setattr(cell_editor_mod.QMessageBox, "question", _question)
    return calls


_CANCEL = cell_editor_mod.QMessageBox.StandardButton.Cancel
_OK = cell_editor_mod.QMessageBox.StandardButton.Ok


def test_an_empty_layer_set_asks_before_refresh_deletes_tracks(main_window,
                                                               tmp_path,
                                                               monkeypatch):
    """Э2а/Э7.9: the question names the consequence; Cancel starts nothing and
    changes nothing, OK runs the read."""
    dock, _ = _make_dock(main_window, tmp_path, _cell_with_two_layer_tracks())
    dock.load_entry("t")
    main_window.connection.board = _RefreshBoard([])
    payloads = _capture_payloads(monkeypatch)
    before = (list(dock._tracks), list(dock._vias))

    calls = _answer_question(monkeypatch, _CANCEL)
    dock._read_refresh_from_selection(set())

    assert len(calls) == 1
    assert calls[0][0] == "Layers to read"
    assert calls[0][1] == ("No layer is selected. Refresh deletes every track "
                           "record of this cell. Continue?")
    assert payloads == []                          # nothing was started
    assert (dock._tracks, dock._vias) == before    # and nothing changed

    _answer_question(monkeypatch, _OK)
    dock._read_refresh_from_selection(set())

    assert payloads and payloads[-1]["layers"] == set()


def test_the_empty_set_confirmation_covers_the_fast_path(main_window, tmp_path,
                                                         monkeypatch):
    """The REMEMBERED set can be empty too, and the fast path would then delete
    those records just as silently: the check lives in the read, so one place
    covers both entry points."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _RefreshBoard([])
    remember_read_layers([])                       # the user unchecked everything
    payloads = _capture_payloads(monkeypatch)
    calls = _answer_question(monkeypatch, _CANCEL)

    dock._on_refresh_geometry()                    # the one-click path

    assert len(calls) == 1
    assert payloads == []


def test_only_a_provided_and_empty_set_asks(main_window, tmp_path, monkeypatch):
    """The None-vs-empty distinction the design rests on: ALL_COPPER_LAYERS (None)
    and a set with layers in it both ask NOTHING."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _RefreshBoard([])
    payloads = _capture_payloads(monkeypatch)
    calls = _answer_question(monkeypatch, _OK)

    dock._read_refresh_from_selection(ALL_COPPER_LAYERS)
    dock._read_refresh_from_selection({"F.Cu"})

    assert calls == []
    assert [payload["layers"] for payload in payloads] == [None, {"F.Cu"}]


def test_an_empty_layer_set_only_logs_on_import(main_window, tmp_path, monkeypatch):
    """Э2а: Import deletes nothing, so no window — but the line must be exact: an
    empty set silences ONLY the tracks, and the read still makes sense."""
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())
    dock.load_entry("t")
    main_window.connection.board = _ImportBoard([])
    payloads = _capture_payloads(monkeypatch)
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))
    calls = _answer_question(monkeypatch, _OK)

    dock._read_import_from_selection(set())

    assert calls == []                             # no window for Import
    assert payloads and payloads[-1]["layers"] == set()   # the read still runs
    assert any("no track will be read" in m for m in messages)
    assert any("vias and components are read as usual" in m for m in messages)


# ── Э6: the cell card's layer indicator ─────────────────────────────────────

def _content_boxes(dock):
    """The indicator's checkboxes, in the order they were added."""
    return dock.content_layers_holder.findChildren(QCheckBox)


def test_the_cell_card_lights_the_layers_the_cell_occupies(main_window, tmp_path):
    """Э6: «галочки у ячейки — показ, а не хранилище». The layers come from the
    cell's OWN records, are checked, and cannot be clicked."""
    dock, _ = _make_dock(main_window, tmp_path, _cell_with_two_layer_tracks())
    dock.load_entry("t")

    boxes = _content_boxes(dock)

    assert [box.text() for box in boxes] == ["F.Cu", "B.Cu"]
    assert all(box.isChecked() for box in boxes)
    assert not any(box.isEnabled() for box in boxes)   # an indicator, not an editor


def test_a_copperless_cell_says_so(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, _loaded_cell_data())   # no tracks
    dock.load_entry("t")

    assert _content_boxes(dock) == []
    labels = [label.text() for label
              in dock.content_layers_holder.findChildren(QLabel)]
    assert labels == ["(no copper records)"]


def test_the_content_indicator_follows_the_cells_own_layer(main_window, tmp_path):
    """A record without a `layer` key sits on the cell's own layer — the same rule
    the read paths use — so switching the combo moves the indicator with it."""
    dock, _ = _make_dock(main_window, tmp_path, _cell_with_two_layer_tracks())
    dock.load_entry("t")
    assert [box.text() for box in _content_boxes(dock)] == ["F.Cu", "B.Cu"]

    dock.layer_combo.setCurrentIndex(1)               # B.Cu

    assert [box.text() for box in _content_boxes(dock)] == ["B.Cu"]


def test_the_indicator_is_rebuilt_with_the_records(main_window, tmp_path):
    """It follows the loaded cell, not a snapshot taken at construction."""
    dock, _ = _make_dock(main_window, tmp_path, _cell_with_two_layer_tracks())
    dock.load_entry("t")
    assert [box.text() for box in _content_boxes(dock)] == ["F.Cu", "B.Cu"]

    dock._tracks = [{"net": "GND", "layer": "In1.Cu"}]
    dock._refresh_all_tables()

    assert [box.text() for box in _content_boxes(dock)] == ["In1.Cu"]
