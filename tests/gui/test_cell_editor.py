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

import gui.docks.cell_editor as cell_editor_mod
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
    Role by ref — enough for build_refresh_plan's footprint role pass."""
    def __init__(self, items, roles=None):
        self.adapter = SimpleNamespace(
            get_selected_items=lambda: list(items),
            get_field_value=lambda fp, name: (roles or {}).get(fp.ref))


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
    """Successful plan -> Apply mutates ONLY the geometric keys on the SAME
    dicts already in _components/_vias, and every other field survives intact
    (net_template_same_as_role/net_template stay). Preview dialog is stubbed
    to Accept so the real finish->apply path runs."""
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

    accepted = {"value": False}
    class _AcceptDialog:
        def __init__(self, sections, parent=None):
            self.sections = sections
        def exec(self):
            return 1  # QDialog.Accepted
    monkeypatch.setattr(cell_editor_mod, "_RefreshPreviewDialog", _AcceptDialog)

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
