# tests/gui/test_rules_dock.py
"""
RuleDock tests are deliberately headless AND board-mutation-free — same
reasoning as tests/gui/test_thermal_via_dock.py. ApplyPipeline/load_config
are monkeypatched with fakes that only check what RuleDock PASSES them
(config_path, only=, and that other already-saved rules survive into the
config handed to the pipeline)."""
from types import SimpleNamespace

import gui.docks.rules as rules_mod
from gui.docks.rules import RuleDock
from kicadstamp.config import Config, RuntimeContext
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


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
    target_file = tmp_path / "rules.sexp"
    _write(target_file, data if data is not None else {"rules": []})
    dock = RuleDock(main_window)
    dock.set_root_path(target_file)
    return dock, target_file


def _bulk_graph(tmp_path):
    """A project root that includes two rule files sharing net +3V3 — the
    cross-file scenario Bulk-set Cell for net exists for (a net's rules
    routinely live in different included files)."""
    target = tmp_path / "rules.sexp"
    _write(target, {"rules": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "old_a"}]},
    ]})
    sibling = tmp_path / "sibling.sexp"
    _write(sibling, {"rules": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "26", "cell": "old_b"}]},
        {"net": "GND", "anchor_role": "FPGA", "spokes": []},
    ]})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["rules.sexp", "sibling.sexp"]})
    return target, sibling, root


# ── Building the rule dict ───────────────────────────────────────────────

def test_build_rule_dict_anchor_role_with_sheet_and_cluster(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.anchor_sheet_edit.setCurrentText("Channel_1")
    dock.anchor_cluster_edit.setCurrentText("PWR_BANK")

    entry = dock._build_rule_dict()
    assert entry == {
        "net": "+3V3", "spokes": [],
        "anchor_role": "FPGA", "anchor_sheet": "Channel_1", "anchor_cluster": "PWR_BANK",
    }


def test_refresh_sheet_names_populates_anchor_sheet_combo(main_window, tmp_path, monkeypatch):
    """2026-08-15 (plan step 3): the rule's own anchor Sheet field is
    autocompleted from the project's schematic files on root change (not
    the ~2s board poll)."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._root_path = tmp_path / "root.sexp"
    monkeypatch.setattr(rules_mod, "collect_all_sheet_names",
                        lambda root: ["Channel_0", "Channel_1"])
    dock._refresh_sheet_names()
    assert [dock.anchor_sheet_edit.itemText(i) for i in range(dock.anchor_sheet_edit.count())] \
        == ["Channel_0", "Channel_1"]


def test_build_rule_dict_point_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.point_edit.setCurrentText("fpga_center")

    entry = dock._build_rule_dict()
    assert entry == {"net": "+3V3", "spokes": [], "anchor_point": "fpga_center"}


def test_net_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.point_edit.setCurrentText("p1")

    assert dock._build_rule_dict() is None
    assert any("Net is required" in r.message for r in caplog.records)


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_ref_edit.setText("U3")
    dock.anchor_role_edit.setCurrentText("FPGA")

    assert dock._build_rule_dict() is None
    assert any("mutually exclusive" in r.message for r in caplog.records)


def test_origin_mode_toggles_row_visibility(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    def visible(row):
        # isVisibleTo(dock) would also depend on the Origin tab being the
        # CURRENTLY SELECTED tab (2026-08-05: _anchor_row/_point_row moved
        # inside the tabbed Origin page) — checking against the row's own
        # immediate parent isolates just AnchorOriginWidget's own
        # setVisible() toggle, independent of which tab happens to be up.
        return row.isVisibleTo(row.parentWidget())

    origin = dock.origin_widget  # 2026-08-11: rows now live on the shared
    # AnchorOriginWidget (gui/docks/_anchor_origin.py), not RuleDock itself.
    dock.origin_mode_combo.setCurrentIndex(0)
    assert visible(origin._anchor_row) and not visible(origin._point_row)

    dock.origin_mode_combo.setCurrentIndex(1)
    assert visible(origin._point_row) and not visible(origin._anchor_row)


# ── Spokes table ──────────────────────────────────────────────────────────

def test_add_spoke_appends_and_selects_the_new_row(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock.spoke_shift_x_edit.setText("1.2")
    dock.spoke_rotation_edit.setText("90")

    dock._on_add_spoke()

    assert dock._spokes == [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2, "rotation_deg": 90.0}]
    assert dock.spokes_table.rowCount() == 1
    assert dock._selected_index == 0
    assert dock.spokes_table.item(0, 0).text() == "17"
    assert dock.spokes_table.item(0, 1).text() == "cap_pair"


def test_spoke_pad_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_cell_combo.setCurrentText("cap_pair")

    assert dock._build_spoke_dict() is None
    assert any("Pad is required" in r.message for r in caplog.records)


def test_spoke_cell_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")

    assert dock._build_spoke_dict() is None
    assert any("Cell is required" in r.message for r in caplog.records)


def test_selecting_a_row_loads_it_into_the_editor(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])

    dock.spokes_table.selectRow(0)

    assert dock._selected_index == 0
    assert dock.spoke_pad_edit.text() == "17"
    assert dock.spoke_cell_combo.currentText() == "cap_pair"
    assert dock.spoke_shift_x_edit.text() == "1.2"


def test_update_selected_spoke_replaces_in_place(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock.spokes_table.selectRow(0)
    dock.spoke_pad_edit.setText("26")
    dock._on_update_spoke()

    assert dock._spokes == [{"pad": "26", "cell": "cap_pair"}]
    assert dock.spokes_table.rowCount() == 1
    assert dock.spokes_table.item(0, 0).text() == "26"


def test_update_without_a_selection_shows_error(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")

    dock._on_update_spoke()

    assert dock._spokes == []
    assert any("Pick a spoke row first" in r.message for r in caplog.records)


def test_remove_selected_spoke(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock.spokes_table.selectRow(0)
    dock._on_remove_spoke()

    assert dock._spokes == []
    assert dock.spokes_table.rowCount() == 0
    assert dock._selected_index is None


def test_move_spoke_down_and_up(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()
    dock.spoke_pad_edit.setText("26")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock.spokes_table.selectRow(0)
    dock._on_move_spoke(1)

    assert [s["pad"] for s in dock._spokes] == ["26", "17"]
    assert dock._selected_index == 1

    dock._on_move_spoke(-1)
    assert [s["pad"] for s in dock._spokes] == ["17", "26"]
    assert dock._selected_index == 0


def test_move_spoke_at_boundary_is_a_noop(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock.spokes_table.selectRow(0)
    dock._on_move_spoke(-1)  # already at index 0

    assert [s["pad"] for s in dock._spokes] == ["17"]
    assert dock._selected_index == 0


def test_add_polar_spoke_writes_radius_and_angle(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock.spoke_mode_combo.setCurrentIndex(1)  # Polar
    dock.spoke_radius_edit.setText("5.0")
    dock.spoke_angle_edit.setText("37")

    dock._on_add_spoke()

    assert dock._spokes == [{"pad": "17", "cell": "cap_pair",
                             "radius_mm": 5.0, "angle_deg": 37.0}]
    assert dock.spokes_table.item(0, 2).text() == "Polar"
    assert dock.spokes_table.item(0, 5).text() == "5.0"
    assert dock.spokes_table.item(0, 6).text() == "37.0"


def test_polar_mode_requires_both_radius_and_angle(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock.spoke_mode_combo.setCurrentIndex(1)
    dock.spoke_radius_edit.setText("5.0")

    assert dock._build_spoke_dict() is None
    assert any("both Radius and Angle" in r.message for r in caplog.records)


def test_selecting_a_polar_row_loads_mode_into_editor(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "radius_mm": 5.0, "angle_deg": 37.0}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])

    dock.spokes_table.selectRow(0)

    assert dock.spoke_mode_combo.currentIndex() == 1
    assert dock.spoke_radius_edit.text() == "5.0"
    assert dock.spoke_angle_edit.text() == "37.0"
    assert dock.spoke_shift_x_edit.isEnabled() is False


def test_polar_zero_values_render_and_round_trip(main_window, tmp_path):
    """2026-08-12, Group 2 fix: truthiness checks (spoke.get("radius_mm"))
    made a legitimate 0.0 render as an EMPTY field and reload as None, so a
    save was rejected ("Polar mode needs both Radius and Angle") even though
    the source data was valid."""
    dock, _ = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "radius_mm": 0.0, "angle_deg": 0.0}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])

    dock.spokes_table.selectRow(0)

    assert dock.spoke_radius_edit.text() == "0.0"
    assert dock.spoke_angle_edit.text() == "0.0"
    assert dock.spokes_table.item(0, 5).text() == "0.0"  # radius column, not blank
    assert dock.spokes_table.item(0, 6).text() == "0.0"  # angle column, not blank

    rebuilt = dock._build_spoke_dict()
    assert rebuilt["radius_mm"] == 0.0
    assert rebuilt["angle_deg"] == 0.0


def test_invalid_angle_error_is_not_clobbered_by_needs_both(main_window, tmp_path, caplog):
    """2026-08-12, Group 2 fix: when Radius is empty AND Angle is unparsable
    (e.g. "12,5"), the overloaded-None parse used to overwrite Angle's exact
    "not a number" error with the generic "Polar mode needs both Radius and
    Angle" — hiding the real problem. The (ok, value) parse reports the field
    error and stops before the generic check."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock.spoke_mode_combo.setCurrentIndex(1)  # Polar
    dock.spoke_angle_edit.setText("12,5")     # invalid number, Radius left blank

    entry = dock._build_spoke_dict()

    assert entry is None
    assert any("not a number" in r.message for r in caplog.records)
    assert not any("needs both" in r.message for r in caplog.records)


def test_mode_toggle_enables_only_the_active_fields(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    # default Cartesian: shift enabled, polar disabled
    assert dock.spoke_shift_x_edit.isEnabled() is True
    assert dock.spoke_shift_y_edit.isEnabled() is True
    assert dock.spoke_radius_edit.isEnabled() is False
    assert dock.spoke_angle_edit.isEnabled() is False

    dock.spoke_mode_combo.setCurrentIndex(1)
    assert dock.spoke_shift_x_edit.isEnabled() is False
    assert dock.spoke_shift_y_edit.isEnabled() is False
    assert dock.spoke_radius_edit.isEnabled() is True
    assert dock.spoke_angle_edit.isEnabled() is True


# ── Save ──────────────────────────────────────────────────────────────────

def test_save_writes_list_section_and_preserves_other_keys(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"c1": {"components": []}}})
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock._on_save()

    data = _load(target)
    assert data["rules"] == [{
        "net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "cap_pair"}],
    }]
    assert data["cells"] == {"c1": {"layer": "F.Cu", "vias": [], "components": [],
                                     "tracks": [], "clone_placements": []}}
    assert any("Wrote" in r.message for r in caplog.records)


def test_save_overwrites_by_name_or_net(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"rules": [
        {"net": "+3V3", "anchor_role": "FPGA_OLD", "spokes": []},
    ]})
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA_NEW")

    dock._on_save()

    data = _load(target)
    assert len(data["rules"]) == 1
    assert data["rules"][0]["anchor_role"] == "FPGA_NEW"
    assert any("Overwrote" in r.message for r in caplog.records)


# ── comment field (handoff_2026_08_27_entity_comment_field.md) ────────────

def test_build_rule_dict_includes_comment(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.comment_edit.setText("a rule note")

    entry = dock._build_rule_dict()
    assert entry == {"net": "+3V3", "spokes": [], "anchor_role": "FPGA",
                     "comment": "a rule note"}


def test_comment_saves_and_loads_back(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"rules": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": []},
    ]})
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.comment_edit.setText("a rule note")

    dock._on_save()

    data = _load(target)
    assert data["rules"][0]["comment"] == "a rule note"
    dock.load_entry(data["rules"][0])
    assert dock.comment_edit.text() == "a rule note"


def test_save_rejects_a_rule_without_any_anchor(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    # No anchor set on either mode's fields — _build_rule_dict() itself
    # already refuses (Anchor: set Ref or Role) before load_rule ever runs,
    # same as every other dock's own field-level guards.
    dock._on_save()

    assert _load(target) == {"rules": []}
    assert any("Anchor: set Ref or Role" in r.message for r in caplog.records)


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = RuleDock(main_window)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")

    dock._on_save()
    assert any("Set the project root first" in r.message for r in caplog.records)


# ── Autosave (Stage 2, 2026-08-20: "писать сразу, без Save") ──────────────

def test_spoke_field_editing_finished_autosaves(main_window, tmp_path, caplog):
    """A spoke field's editingFinished (blur/Enter) immediately persists —
    no explicit Save pressed (old 'Update selected' + 'Save' became one)."""
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)

    dock.spoke_shift_x_edit.setText("2.5")
    dock.spoke_shift_x_edit.editingFinished.emit()

    data = _load(target)
    assert data["rules"][0]["spokes"][0]["shift_x_mm"] == 2.5
    assert dock._spokes[0]["shift_x_mm"] == 2.5
    assert any("Spoke updated" in r.message for r in caplog.records)


def test_spoke_combo_activated_autosaves(main_window, tmp_path):
    """Picking a new cell from the (editable) Cell combo persists immediately."""
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair"}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)

    dock.spoke_cell_combo.setCurrentText("other_cell")
    dock.spoke_cell_combo.activated.emit(dock.spoke_cell_combo.currentIndex())

    assert _load(target)["rules"][0]["spokes"][0]["cell"] == "other_cell"


def test_add_spoke_persists_immediately(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")

    dock._on_add_spoke()  # no Save pressed

    assert _load(target)["rules"] == [{
        "net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "cap_pair"}]}]


def test_remove_spoke_persists_immediately(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair"}, {"pad": "26", "cell": "cap_pair"}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)

    dock._on_remove_spoke()

    assert [s["pad"] for s in _load(target)["rules"][0]["spokes"]] == ["26"]


def test_move_spoke_persists_immediately(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair"}, {"pad": "26", "cell": "cap_pair"}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)

    dock._on_move_spoke(1)

    assert [s["pad"] for s in _load(target)["rules"][0]["spokes"]] == ["26", "17"]


def test_autosave_creating_a_new_rule_notifies_the_tree(main_window, tmp_path):
    """A brand-new rule written by autosave must fire saved so the Config
    tree learns it exists (it shows rule NODES by name)."""
    dock, target = _make_dock(main_window, tmp_path)  # {"rules": []}
    fired = []
    dock.saved.connect(lambda: fired.append(True))
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")

    dock._on_add_spoke()

    assert fired
    assert _load(target)["rules"] == [{
        "net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "cap_pair"}]}]


def test_autosave_existing_rule_does_not_churn_the_tree(main_window, tmp_path):
    """A spoke value tweak on an ALREADY-saved rule must NOT fire saved —
    config_tree_dock.refresh() clears and rebuilds the whole tree (losing
    selection), and a spoke value isn't even shown there."""
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)
    fired = []
    dock.saved.connect(lambda: fired.append(True))

    dock.spoke_shift_x_edit.setText("2.5")
    dock.spoke_shift_x_edit.editingFinished.emit()

    assert not fired
    assert _load(target)["rules"][0]["spokes"][0]["shift_x_mm"] == 2.5


def test_autosave_failure_reports_error_never_silent(main_window, tmp_path, caplog):
    """A failed autosave must be visible in the Log dock — an edit that could
    not be written looks exactly like one that did (Stage 2 plan)."""
    dock, target = _make_dock(main_window, tmp_path, {"rules": [{
        "net": "+3V3", "anchor_role": "FPGA",
        "spokes": [{"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2}],
    }]})
    dock.load_entry(_load(dock._path)["rules"][0])
    dock.spokes_table.selectRow(0)

    dock.origin_widget.clear()  # break the rule -> _build_rule_dict fails
    dock.spoke_shift_x_edit.setText("2.5")
    dock.spoke_shift_x_edit.editingFinished.emit()

    assert any("Anchor: set Ref or Role" in r.message for r in caplog.records)
    # nothing was written
    assert _load(target)["rules"][0]["spokes"][0]["shift_x_mm"] == 1.2


# ── Bulk-set Cell for net (Stage 3, 2026-08-20) ───────────────────────────

def test_collect_rule_nets_and_rules_by_net_across_files(tmp_path):
    from gui.docks.rename import collect_all_rule_nets, collect_rules_by_net
    _target, _sibling, root = _bulk_graph(tmp_path)

    assert collect_all_rule_nets(root) == ["+3V3", "GND"]

    affected = collect_rules_by_net(root, "+3V3")
    assert len(affected) == 2
    assert {p.name for p, _ in affected} == {"rules.sexp", "sibling.sexp"}
    assert all(r["net"] == "+3V3" for _, r in affected)


def test_bulk_set_cell_writes_all_rules_on_net_across_files(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    target, sibling, root = _bulk_graph(tmp_path)
    dock.set_root_path(root)

    dock._apply_bulk_cell_set("+3V3", "new_cell")

    assert _load(target)["rules"][0]["spokes"][0]["cell"] == "new_cell"
    assert _load(sibling)["rules"][0]["spokes"][0]["cell"] == "new_cell"
    # a rule on a DIFFERENT net is untouched (its empty spokes: is omitted by
    # s-expr, so the assertion reads it back as the default empty list)
    assert _load(sibling)["rules"][1].get("spokes", []) == []
    assert any("Bulk-set Cell" in r.message for r in caplog.records)


def test_bulk_set_cell_partial_failure_reported(main_window, tmp_path, caplog, monkeypatch):
    """The plan's hard requirement: a partial write failure must be reported
    EXPLICITLY (which rules wrote, which did not) — never a silent half-applied
    change."""
    import gui.docks.rules as rules_mod
    dock, target = _make_dock(main_window, tmp_path)
    target, sibling, root = _bulk_graph(tmp_path)
    dock.set_root_path(root)

    real_upsert = rules_mod.upsert_list_entry

    def flaky(path, *args, **kwargs):
        if path.name == "sibling.sexp":
            raise OSError("locked by another process")
        return real_upsert(path, *args, **kwargs)

    monkeypatch.setattr(rules_mod, "upsert_list_entry", flaky)

    dock._apply_bulk_cell_set("+3V3", "new_cell")

    assert _load(target)["rules"][0]["spokes"][0]["cell"] == "new_cell"  # wrote
    assert _load(sibling)["rules"][0]["spokes"][0]["cell"] == "old_b"    # failed
    assert any("FAILED" in r.message for r in caplog.records)
    assert any("sibling.sexp" in r.message for r in caplog.records)


def test_bulk_dialog_preview_shows_rules_and_pads(main_window, tmp_path):
    from gui.docks.rules import BulkSetCellDialog
    _target, _sibling, root = _bulk_graph(tmp_path)

    dlg = BulkSetCellDialog(root, main_window)
    dlg.net_combo.setCurrentText("+3V3")
    dlg._refresh_preview()

    text = dlg.preview_label.text()
    assert "2 rule(s)" in text
    assert "rules.sexp" in text and "sibling.sexp" in text
    assert "17" in text and "26" in text


def test_bulk_set_cell_reloads_loaded_rule_if_affected(main_window, tmp_path):
    """The currently-loaded rule, if it is on the affected net, must be
    reloaded from disk — otherwise a later spoke autosave (Stage 2) would
    write the stale pre-bulk state back over the bulk change."""
    dock, target = _make_dock(main_window, tmp_path)
    target, sibling, root = _bulk_graph(tmp_path)
    dock.set_root_path(root)
    dock.load_entry(_load(target)["rules"][0])
    assert dock._spokes[0]["cell"] == "old_a"

    dock._apply_bulk_cell_set("+3V3", "new_cell")

    # the reloaded rule's in-memory spokes carry the bulk result...
    assert dock._spokes[0]["cell"] == "new_cell"
    # ...and selecting its row shows the new cell in the spoke editor
    dock.spokes_table.selectRow(0)
    assert dock.spoke_cell_combo.currentText() == "new_cell"


# ── new_rule / load_entry ─────────────────────────────────────────────────

def test_new_rule_resets_form_and_targets_file(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("stale")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    dock.new_rule(target)

    assert dock.net_edit.currentText() == ""
    assert dock._spokes == []
    assert dock.spokes_table.rowCount() == 0
    assert dock._path == target


def test_load_entry_anchor_role_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    dock.load_entry({
        "net": "+3V3", "name": "fpga_3v3", "anchor_role": "FPGA", "anchor_sheet": "Channel_1",
        "anchor_cluster": "PWR_BANK", "retired": True, "skip": True,
        "spokes": [{"pad": "17", "cell": "cap_pair"}],
    })

    assert dock.net_edit.currentText() == "+3V3"
    assert dock.name_edit.text() == "fpga_3v3"
    assert dock.origin_mode_combo.currentIndex() == 0
    assert dock.anchor_role_edit.currentText() == "FPGA"
    assert dock.anchor_sheet_edit.currentText() == "Channel_1"
    assert dock.anchor_cluster_edit.currentText() == "PWR_BANK"
    assert dock.retired_checkbox.isChecked() is True
    assert dock.skip_checkbox.isChecked() is True
    assert dock._spokes == [{"pad": "17", "cell": "cap_pair"}]
    assert dock.spokes_table.rowCount() == 1


def test_load_entry_point_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    dock.load_entry({"net": "+3V3", "anchor_point": "fpga_center", "spokes": []})

    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.point_edit.currentText() == "fpga_center"


# ── set_root_path (whole-graph Cell/Point combos) ────────────────────────

def test_set_root_path_populates_cell_and_point_combos(main_window, tmp_path):
    _write(tmp_path / "sub.sexp", {"cells": {"cap_pair": {}}, "points": {"fpga_center": {"xy": [0, 0]}}})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})
    dock = RuleDock(main_window)

    dock.set_root_path(root)

    assert [dock.spoke_cell_combo.itemText(i) for i in range(dock.spoke_cell_combo.count())] \
        == ["cap_pair"]
    assert [dock.point_edit.itemText(i) for i in range(dock.point_edit.count())] == ["fpga_center"]


def test_set_root_path_none_clears_combos(main_window):
    dock = RuleDock(main_window)
    dock.set_root_path(None)
    assert dock.spoke_cell_combo.count() == 0
    assert dock.point_edit.count() == 0


# ── refresh_known_roles / refresh_known_nets ─────────────────────────────

def test_refresh_known_roles_populates_anchor_and_spoke_cluster_combos(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    snapshot = [
        SimpleNamespace(role="FPGA", cluster="PWR_BANK"),
        SimpleNamespace(role="C_OUT", cluster="PWR_BANK"),
    ]

    dock.refresh_known_roles(snapshot)

    assert [dock.anchor_role_edit.itemText(i) for i in range(dock.anchor_role_edit.count())] \
        == ["C_OUT", "FPGA"]
    assert [dock.anchor_cluster_edit.itemText(i) for i in range(dock.anchor_cluster_edit.count())] \
        == ["PWR_BANK"]
    assert [dock.spoke_cluster_combo.itemText(i) for i in range(dock.spoke_cluster_combo.count())] \
        == ["PWR_BANK"]


def test_refresh_known_nets_populates_net_combo(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    board = SimpleNamespace(adapter=SimpleNamespace(
        get_all_nets=lambda: [SimpleNamespace(name="+3V3"), SimpleNamespace(name="GND")]))

    dock.refresh_known_nets(board)

    assert [dock.net_edit.itemText(i) for i in range(dock.net_edit.count())] == ["+3V3", "GND"]


# ── Redraw ────────────────────────────────────────────────────────────────

def test_redraw_rule_preserves_other_entries_for_registry_safety(main_window, tmp_path, monkeypatch, caplog):
    """Same correctness property as ThermalViaArrayDock's own equivalent
    test — Redraw must load the REAL config (with every other already-saved
    rule intact) and only narrow EXECUTION via only=, never build a config
    that looks like every other entry no longer exists (registry/
    known_anchor_ids protection, kicadstamp/apply_pipeline.py's
    apply_only_filter)."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    from kicadstamp.config import Rule
    other_rule = Rule(net="OTHER_NET", anchor_role="OTHER", spokes=[])
    fake_cfg = Config(rules=[other_rule])
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(rules_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    pipeline_calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"config_path": config_path, "cfg": preloaded_cfg, "only": only})

        def run(self):
            pass

    monkeypatch.setattr(rules_mod, "ApplyPipeline", _FakePipeline)

    dock._do_redraw_rule()

    assert pipeline_calls[-1]["only"] == ["+3V3"]
    assert pipeline_calls[-1]["config_path"] == str(target_file)
    used_cfg = pipeline_calls[-1]["cfg"]
    nets = [r.net for r in used_cfg.rules]
    assert "OTHER_NET" in nets  # not dropped -> registry-protected
    assert nets.count("+3V3") == 1  # replaced, not duplicated
    target_rule = next(r for r in used_cfg.rules if r.net == "+3V3")
    assert all(not s.skip for s in target_rule.spokes)  # whole-rule Redraw: nothing forced skip
    assert any("Placed" in r.message for r in caplog.records)


def test_redraw_rule_resolves_cells_via_project_root_not_rule_file(main_window, tmp_path, monkeypatch, caplog):
    """Regression (found live 2026-08-06): a spoke's cell definition
    routinely lives in a different included file than the rule referencing
    it (module docstring) — the rule file (self._path) itself carries no
    cells: key at all. Redraw used to load_config(self._path) directly and
    saw an empty cells:, failing every spoke's "cell not found" check even
    though the project as a whole is valid. Uses REAL load_config (not
    monkeypatched) to prove the include: graph is actually walked."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {"cap_pair": {}}})
    target_file = tmp_path / "rules.sexp"
    _write(target_file, {"rules": []})
    root_file = tmp_path / "root.sexp"
    _write(root_file, {"include": ["cells.sexp", "rules.sexp"]})

    dock = RuleDock(main_window)
    dock.set_root_path(target_file)
    dock.set_root_path(root_file)

    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()

    pipeline_calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"config_path": config_path, "cfg": preloaded_cfg})

        def run(self):
            pass

    monkeypatch.setattr(rules_mod, "ApplyPipeline", _FakePipeline)

    dock._do_redraw_rule()

    assert pipeline_calls[-1]["config_path"] == str(root_file)
    assert "cap_pair" in pipeline_calls[-1]["cfg"].cells
    assert any("Placed" in r.message for r in caplog.records)


def test_redraw_spoke_isolates_only_the_selected_spoke(main_window, tmp_path, monkeypatch):
    """The one property that makes "Redraw selected spoke" different from
    "Redraw rule" — every OTHER spoke gets a temporary skip=True injected
    into the copy handed to the pipeline (2026-08-05, Denis: "Redraw Rule,
    Redraw (выбранная спица) по-моему, так будет логично"); Save is
    unaffected — this never gets written back."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.spoke_pad_edit.setText("17")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()
    dock.spoke_pad_edit.setText("26")
    dock.spoke_cell_combo.setCurrentText("cap_pair")
    dock._on_add_spoke()
    dock.spokes_table.selectRow(0)  # pad 17

    fake_cfg = Config()
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(rules_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    pipeline_calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"cfg": preloaded_cfg, "only": only})

        def run(self):
            pass

    monkeypatch.setattr(rules_mod, "ApplyPipeline", _FakePipeline)

    dock._do_redraw_spoke()

    rule = pipeline_calls[-1]["cfg"].rules[0]
    by_pad = {s.pad: s.skip for s in rule.spokes}
    assert by_pad == {"17": False, "26": True}
    # the in-memory form/spoke list itself must be untouched by the preview
    assert dock._spokes == [{"pad": "17", "cell": "cap_pair"}, {"pad": "26", "cell": "cap_pair"}]


def test_redraw_spoke_without_a_selection_shows_error(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")

    dock._on_redraw_spoke()

    assert any("Pick a spoke row first" in r.message for r in caplog.records)


def test_on_redraw_rule_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.anchor_role_edit.setCurrentText("FPGA")

    fake_cfg = Config()
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(rules_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        return "fake-controller"

    monkeypatch.setattr(rules_mod, "start_long_op", _fake_start)

    dock._on_redraw_rule()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    # 2026-09-01 (plan project_save_model): the per-dock Save button is gone.
    assert captured["widgets"] == (dock.redraw_rule_button, dock.redraw_spoke_button)


# ── Target-file combo (2026-08-13, plan tree_to_combo_file_pickers) ──────

def _combo_index_for_filename(combo, filename):
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1
