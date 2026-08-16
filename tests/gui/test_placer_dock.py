# tests/gui/test_placer_dock.py
"""
PlacerDock tests are deliberately headless AND board-mutation-free:
_on_redraw()'s real job is moving real footprints on a live board, which
these tests must never do on their own. ApplyPipeline/PlacementPlanner/
load_config are monkeypatched with fakes that only check what PlacerDock
PASSES them (config_path, only=, and — most importantly — that OTHER
already-saved clone_placements survive into the config handed to the
pipeline, see test_redraw_preserves_other_placements_for_registry_safety).
Actually invoking the real pipeline against a live board is left to
manual verification against KiCad, same as every other dock this session.
"""
from types import SimpleNamespace

import yaml

import gui.docks.placer as placer_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import Cell, Config, RuntimeContext, TemplateComponentSlot, load_clone_placement
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_cell_and_dock(main_window, tmp_path):
    # Content here is never actually parsed via the real cells: mechanism —
    # every test that checks cfg.cells monkeypatches load_config with its
    # own fake Config below; this file only exists to give set_cells_file()
    # a path. Still written in the real (wrapped) on-disk shape for realism.
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                             "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [{"offset_along_mm": 1, "offset_across_mm": 1, "net": "{PWR_OUT}",
                      "drill_mm": 0.3, "diameter_mm": 0.6}],
            "tracks": [],
            "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})

    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.set_selected_cell("pi_filter")  # Cell picking now lives in ConfigTreeDock, see test_config_tree.py
    return dock, cells_file, placer_file


def test_cell_click_discovers_placeholders(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    assert dock._selected_cell == "pi_filter"
    assert sorted(dock._param_edits.keys()) == ["PWR_IN", "PWR_OUT"]


# ── Cell picker combo (2026-08-06, Denis: "в пласере давай сделаем имя
# целла по выпадающему комбо-боксу... не удобно" — a second, in-place way
# to pick a Cell besides clicking it in the Config tree) ────────────────

def test_set_cells_file_populates_the_cell_combo(main_window, tmp_path):
    dock, cells_file, _ = _make_cell_and_dock(main_window, tmp_path)
    items = [dock.cell_combo.itemText(i) for i in range(dock.cell_combo.count())]
    assert items == ["pi_filter"]


def test_picking_from_the_cell_combo_selects_the_cell(main_window, tmp_path):
    """Picking directly in the combo must do exactly what set_selected_cell
    does when called from ConfigTreeDock — not just update currentText."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    assert dock._selected_cell is None
    assert dock._param_edits == {}

    dock.cell_combo.setCurrentText("pi_filter")

    assert dock._selected_cell == "pi_filter"
    assert sorted(dock._param_edits.keys()) == ["PWR_IN", "PWR_OUT"]


def test_cell_combo_is_a_closed_picker_not_a_free_text_field(main_window, tmp_path):
    """Same lesson as CellDock's anchor_role_combo (2026-08-06 live freeze):
    a combo whose values must match an existing cells: key stays a plain,
    non-editable QComboBox, not configure_searchable()."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    assert not dock.cell_combo.isEditable()


# ── Cells/Placer file combos (2026-08-13, plan tree_to_combo_file_pickers) ─

def _combo_index_for_filename(combo, filename):
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1


def test_set_root_path_populates_both_file_combos(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = PlacerDock(main_window)

    dock.set_root_path(root)

    cells_names = {dock.cells_file_combo.itemData(i).name for i in range(dock.cells_file_combo.count())}
    placer_names = {dock.placer_file_combo.itemData(i).name for i in range(dock.placer_file_combo.count())}
    assert cells_names == {"root.yaml", "sub.yaml"}
    assert placer_names == {"root.yaml", "sub.yaml"}


def test_picking_the_cells_file_combo_calls_set_cells_file(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(root)

    dock.cells_file_combo.setCurrentIndex(
        _combo_index_for_filename(dock.cells_file_combo, "sub.yaml"))

    assert dock._cells_path is not None
    assert dock._cells_path.name == "sub.yaml"


def test_picking_the_placer_file_combo_calls_set_placer_file(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(root)

    dock.placer_file_combo.setCurrentIndex(
        _combo_index_for_filename(dock.placer_file_combo, "sub.yaml"))

    assert dock._placer_path is not None
    assert dock._placer_path.name == "sub.yaml"


def test_set_cells_file_reflects_into_the_combo_even_before_root_is_known(main_window, tmp_path):
    """ConfigTreeDock's own file_selected click must keep working exactly
    as before — even before set_root_path() (or for a file outside the
    include graph) the path is still selected as an extra combo item."""
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("cells: {}\n", encoding="utf-8")
    dock = PlacerDock(main_window)

    dock.set_cells_file(cells_file)

    assert dock.cells_file_combo.currentData() == cells_file
    assert dock._cells_path == cells_file


def test_set_placer_file_reflects_into_the_combo_even_before_root_is_known(main_window, tmp_path):
    placer_file = tmp_path / "root.yaml"
    placer_file.write_text("clone_placements: []\n", encoding="utf-8")
    dock = PlacerDock(main_window)

    dock.set_placer_file(placer_file)

    assert dock.placer_file_combo.currentData() == placer_file
    assert dock._placer_path == placer_file


def test_file_combos_are_closed_pickers_not_free_text_fields(main_window):
    dock = PlacerDock(main_window)
    assert not dock.cells_file_combo.isEditable()
    assert not dock.placer_file_combo.isEditable()


def test_new_placement_clears_the_cell_combo_selection(main_window, tmp_path):
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(placer_file)
    assert dock._selected_cell is None
    assert dock.cell_combo.currentIndex() == -1


def test_build_entry_dict_absolute_xy_round_trips_through_loader(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("10.5")
    dock.y_edit.setText("-3.2")
    dock._param_edits["PWR_IN"].setCurrentText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setCurrentText("+3V3_CH2_DIRTY")

    entry = dock._build_entry_dict()
    assert entry == {
        "name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [10.5, -3.2],
        "params": {"PWR_IN": "+3V3_CH2", "PWR_OUT": "+3V3_CH2_DIRTY"},
    }
    cp = load_clone_placement(entry)  # must validate against the real backend loader
    assert cp.name == "Channel_2_PI_Filter"
    assert cp.xy == (10.5, -3.2)


def test_cell_mode_sheet_field_round_trips(main_window, tmp_path):
    """2026-08-15: the own sheet: field on the Cell mode's Source tab —
    build()/load() round-trip through PlacerDock keeps it, and new_placement()
    (the Cell-mode clear path) wipes it."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("0")
    dock.y_edit.setText("0")
    dock.sheet_edit.setCurrentText("Channel_2")

    entry = dock._build_entry_dict()
    assert entry["sheet"] == "Channel_2"

    dock.load_placement(entry)
    assert dock.sheet_edit.currentText() == "Channel_2"

    dock.new_placement(dock._placer_path)
    assert dock.sheet_edit.currentText() == ""


def test_cell_mode_sheet_field_not_written_when_empty(main_window, tmp_path):
    """Same 'only write if non-empty' pattern as name — an empty Sheet field
    must not inject a stray sheet: key into the saved Cell-mode entry."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("0")
    dock.y_edit.setText("0")

    entry = dock._build_entry_dict()
    assert "sheet" not in entry


def test_refresh_sheet_names_populates_every_sheet_combo(main_window, tmp_path, monkeypatch):
    """2026-08-15 (plan step 3): _refresh_sheet_names — fed by
    collect_all_sheet_names (mocked here) — reaches all four Sheet widgets:
    origin_widget (ClonePlacement's external anchor), coordinate_form's own
    sheet AND its anchor widget, and the Cell-mode sheet_edit. Source is the
    project's schematic files (RuntimeContext.sheet_names), not the ~2s
    board poll Cluster/Role ride."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock._root_path = tmp_path / "root.yaml"
    monkeypatch.setattr(placer_mod, "collect_all_sheet_names",
                        lambda root: ["Channel_0", "Channel_1"])
    dock._refresh_sheet_names()

    def items(combo):
        return [combo.itemText(i) for i in range(combo.count())]

    assert items(dock.origin_widget.anchor_sheet_edit) == ["Channel_0", "Channel_1"]
    assert items(dock.coordinate_form.sheet_edit) == ["Channel_0", "Channel_1"]
    assert items(dock.coordinate_form._anchor_widget.anchor_sheet_edit) == ["Channel_0", "Channel_1"]
    assert items(dock.sheet_edit) == ["Channel_0", "Channel_1"]


def test_clone_origin_anchor_sheet_round_trips(main_window, tmp_path):
    """2026-08-15 (plan step 4): ClonePlacement's external anchor Origin tab
    has had a Sheet field since this plan — _build_entry_dict writes
    anchor_sheet (the model/loader always supported it, only the form never
    reached it), load_placement() reads it back into the combo."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.origin_mode_combo.setCurrentIndex(1)  # anchor (ref/role)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.anchor_sheet_edit.setCurrentText("Channel_2")
    dock.anchor_pad_edit.setText("1")
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    entry = dock._build_entry_dict()
    assert entry["anchor_role"] == "FPGA"
    assert entry["anchor_sheet"] == "Channel_2"
    assert entry["anchor_pad"] == "1"

    dock.load_placement(entry)
    assert dock.anchor_sheet_edit.currentText() == "Channel_2"


def test_build_entry_dict_polar_xy_writes_radius_angle(main_window, tmp_path):
    """Polar mode (optional alternative to xy) must write radius_mm/angle_deg
    instead of xy, and still validate against the real backend loader."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    ow = dock.origin_widget
    ow._polar_combo.setCurrentIndex(1)  # Polar
    ow.radius_edit.setText("5.0")
    ow.angle_edit.setText("37")

    entry = dock._build_entry_dict()
    assert entry == {
        "name": "Channel_2_PI_Filter", "cell": "pi_filter",
        "radius_mm": 5.0, "angle_deg": 37.0,
    }
    cp = load_clone_placement(entry)
    assert cp.radius_mm == 5.0
    assert cp.angle_deg == 37.0
    assert cp.xy == (0.0, 0.0)  # default preserved (optional alternative)


def test_polar_mode_toggle_enables_only_active_xy_fields(main_window, tmp_path):
    """Same Cartesian/Polar field-toggle as the other docks — X/Y only in
    Cartesian, Radius/Angle only in Polar."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    ow = dock.origin_widget
    # default Cartesian
    assert ow.x_edit.isEnabled() is True
    assert ow.y_edit.isEnabled() is True
    assert ow.radius_edit.isEnabled() is False
    assert ow.angle_edit.isEnabled() is False

    ow._polar_combo.setCurrentIndex(1)
    assert ow.x_edit.isEnabled() is False
    assert ow.y_edit.isEnabled() is False
    assert ow.radius_edit.isEnabled() is True
    assert ow.angle_edit.isEnabled() is True


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path, caplog):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U1")
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")

    assert dock._build_entry_dict() is None
    assert any("mutually exclusive" in r.message for r in caplog.records)


def test_anchor_role_with_pad_and_shift(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")
    dock.anchor_pad_edit.setText("1")
    dock.shift_x_edit.setText("2")
    dock.shift_y_edit.setText("0")

    entry = dock._build_entry_dict()
    assert entry["anchor_role"] == "SOME_ROLE"
    assert entry["anchor_pad"] == "1"
    assert entry["xy"] == [2.0, 0.0]
    cp = load_clone_placement(entry)  # validates anchor_role/anchor_pad combination
    assert cp.anchor_role == "SOME_ROLE"
    assert cp.anchor_pad == "1"


def test_load_placement_anchor_with_polar_offset_round_trips(main_window, tmp_path):
    """2026-08-12, Group 2 fix: a ClonePlacement with BOTH an anchor and a
    polar offset (a valid, documented combo) used to load into the Cartesian-
    anchor branch — radius_mm/angle_deg silently disappeared from the form and
    were lost FOREVER on the next Save. Now the polar offset loads and
    round-trips through _build_entry_dict as radius_mm/angle_deg, not xy."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "X", "cell": "pi_filter", "anchor_role": "FPGA",
        "radius_mm": 5.0, "angle_deg": 37.0,
    }

    dock.load_placement(entry)

    assert dock.origin_widget.radius_edit.text() == "5.0"
    assert dock.origin_widget.angle_edit.text() == "37.0"
    rebuilt = dock._build_entry_dict()
    assert rebuilt["anchor_role"] == "FPGA"
    assert rebuilt["radius_mm"] == 5.0
    assert rebuilt["angle_deg"] == 37.0
    assert "xy" not in rebuilt


def test_load_placement_anchor_point_with_polar_offset_round_trips(main_window, tmp_path):
    """2026-08-12, Group 2 review: the anchor_point + polar-offset branch of
    load_placement forgot point= — AnchorOriginWidget.load defaults it to ""
    and unconditionally clears the combo, so a saved anchor_point+polar entry
    would lose its anchor on the next Save (same data-loss class as the
    anchor_role bug just fixed)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "X", "cell": "pi_filter", "anchor_point": "origin_point",
        "radius_mm": 5.0, "angle_deg": 37.0,
    }

    dock.load_placement(entry)

    assert dock.origin_widget.point_edit.currentText() == "origin_point"
    assert dock.origin_widget.radius_edit.text() == "5.0"
    rebuilt = dock._build_entry_dict()
    assert rebuilt["anchor_point"] == "origin_point"
    assert rebuilt["radius_mm"] == 5.0
    assert rebuilt["angle_deg"] == 37.0
    assert "xy" not in rebuilt


def test_point_mode_requires_a_name(main_window, tmp_path, caplog):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("X")
    dock.origin_mode_combo.setCurrentIndex(2)

    assert dock._build_entry_dict() is None
    assert any("name is required" in r.message for r in caplog.records)

    dock.point_edit.setCurrentText("origin_point")
    entry = dock._build_entry_dict()
    assert entry["anchor_point"] == "origin_point"


def test_save_upserts_by_name_without_duplicating(main_window, tmp_path):
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")
    entry = dock._build_entry_dict()

    overwritten1 = dock._upsert_clone_placement(placer_file, entry)
    assert overwritten1 is False
    saved = yaml.safe_load(placer_file.read_text())
    assert len(saved["clone_placements"]) == 1

    overwritten2 = dock._upsert_clone_placement(placer_file, entry)
    assert overwritten2 is True
    saved2 = yaml.safe_load(placer_file.read_text())
    assert len(saved2["clone_placements"]) == 1  # no duplicate on the same name

    other = dict(entry, name="Channel_3_PI_Filter")
    dock._upsert_clone_placement(placer_file, other)
    saved3 = yaml.safe_load(placer_file.read_text())
    assert sorted(e["name"] for e in saved3["clone_placements"]) == [
        "Channel_2_PI_Filter", "Channel_3_PI_Filter"]


def test_redraw_requires_cell_reachable_via_placer_config(main_window, tmp_path, monkeypatch, caplog):
    """The Cell must actually be loadable FROM the Placer file's own
    include: wiring (load_config's cfg.cells) — picking a cell name in
    the list alone isn't enough if include: was never pointed at it."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")

    monkeypatch.setattr(placer_mod, "load_config",
                         lambda path: (Config(), RuntimeContext()))  # cells: empty -> cell unreachable

    dock._on_redraw()
    assert any("include" in r.message for r in caplog.records)


def test_redraw_preserves_other_placements_for_registry_safety(main_window, tmp_path, monkeypatch, caplog):
    """The single most important correctness property here: Redraw must
    load the REAL config (with every other already-saved clone_placement
    intact) and only narrow EXECUTION via only=, never build a config that
    looks like every other placement no longer exists — see
    PlacementRegistry.reconcile()'s known_anchor_ids protection
    (kicadstamp/registry.py). A synthetic single-placement config here
    would make Redraw silently prune everyone else's vias/tracks."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("10")
    dock.y_edit.setText("5")
    dock._param_edits["PWR_IN"].setCurrentText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setCurrentText("+3V3_CH2_DIRTY")

    pre_existing = load_clone_placement({"name": "OTHER_PLACEMENT", "cell": "pi_filter", "xy": [0, 0]})
    fake_cfg = Config(
        cells={"pi_filter": Cell(name="pi_filter", vias=[], tracks=[], clone_placements=[], components=[
            TemplateComponentSlot(role="C_IN", offset_along_mm=0, offset_across_mm=0, angle_deg=0),
        ])},
        clone_placements=[pre_existing],
    )
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    pipeline_calls = []

    class _FakeItem:
        def __init__(self, obj):
            self.kind = "clone"
            self.obj = obj

    class _FakeMove:
        def __init__(self, ref):
            self.ref = ref

    class _FakeFootprint:
        def __init__(self, ref):
            self.ref = ref

    class _FakeAdapter:
        def __init__(self):
            self.field_writes = None

        def get_footprint(self, ref):
            return _FakeFootprint(ref)

        def set_field_values_bulk(self, updates, description):
            self.field_writes = [(fp.ref, field, value) for fp, field, value in updates]

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"config_path": config_path, "cfg": preloaded_cfg, "only": only})
            self.cfg = preloaded_cfg
            self.adapter = _FakeAdapter()
            my_placement = next(c for c in preloaded_cfg.clone_placements if c.name in only)
            self.items = [_FakeItem(pre_existing), _FakeItem(my_placement)]

        def run(self):
            pass

    class _FakePlanner:
        def __init__(self, adapter, cfg, sheet_names=None):
            pass

        def begin_planning(self):
            pass

        def plan_item(self, item):
            return [_FakeMove("U5")] if item.obj.name == "Channel_2_PI_Filter" else [_FakeMove("U1")]

    monkeypatch.setattr(placer_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(placer_mod, "PlacementPlanner", _FakePlanner)

    # Full success-path redraw: runs synchronously via the _do_redraw() core
    # (the async _on_redraw() path would not have finished by the asserts).
    dock._do_redraw()

    assert pipeline_calls[-1]["only"] == ["Channel_2_PI_Filter"]
    assert pipeline_calls[-1]["config_path"] == str(placer_file)
    used_cfg = pipeline_calls[-1]["cfg"]
    names = [c.name for c in used_cfg.clone_placements]
    assert "OTHER_PLACEMENT" in names  # not dropped -> registry-protected
    assert names.count("Channel_2_PI_Filter") == 1  # replaced, not duplicated
    assert any("Placed" in r.message for r in caplog.records)
    assert any("1 component(s) tagged Cluster" in r.message for r in caplog.records)


# ── Cell source (2026-08-12, Group 0: role:/cluster: migrated to coordinate_placements) ──

def test_cell_source_mode_stays_visible_and_is_the_default(main_window, tmp_path):
    """ClonePlacement is pure template cloning again (2026-08-12, Group 0
    consolidation) and the merged dock defaults to it (2026-08-12, Group 1:
    the Source combo now also offers "Single component" = CoordinatePlacement,
    but Cell is index 0 / the default): the cell row / name row /
    Params/Nets/Overrides/Refs/Origin tabs are visible, the coordinate
    form's tab is hidden."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    def visible(w):
        # isVisibleTo(dock) would also depend on w's own TAB being the
        # currently selected one — checking against the widget's own
        # immediate parent isolates just _on_cell_mode_changed()'s own
        # setVisible() toggle, independent of which tab is up (same fix
        # RuleDock's own tests already use for the same reason).
        return w.isVisibleTo(w.parentWidget())

    assert dock.cell_mode_combo.count() == 2  # Cell + Single component
    assert dock.cell_mode_combo.currentIndex() == 0
    assert visible(dock._cell_row)
    assert visible(dock._name_row)
    assert not visible(dock._coordinate_identity_row)  # Single-component identity (2026-08-13)
    assert visible(dock._params_container)
    assert dock._tabs.isTabVisible(dock._nets_tab_index)
    assert dock._tabs.isTabVisible(dock._net_overrides_tab_index)
    assert dock._tabs.isTabVisible(dock._refs_tab_index)
    assert dock._tabs.isTabVisible(dock._origin_tab_index)
    assert not dock._tabs.isTabVisible(dock._coordinate_tab_index)


def test_single_component_source_mode_shows_the_identity_row_and_coordinate_tab(main_window, tmp_path):
    """2026-08-13, plan coordinate_identity_on_source_tab (Denis: "Cluster,
    Role, Name надо на первый таб перенести"): in Single-component mode the
    Cluster/Role/Name identity fields live on the SOURCE tab
    (_coordinate_identity_row), the Cell-mode rows are hidden, and the
    Coordinate tab (positioning only) is shown. Switching back reverses it."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    def visible(w):
        return w.isVisibleTo(w.parentWidget())

    dock.cell_mode_combo.setCurrentIndex(1)  # Single component

    assert dock.cell_mode_combo.currentIndex() == 1
    assert not visible(dock._cell_row)
    assert not visible(dock._name_row)
    assert visible(dock._coordinate_identity_row)
    assert dock._tabs.isTabVisible(dock._coordinate_tab_index)
    assert not dock._tabs.isTabVisible(dock._nets_tab_index)
    assert not dock._tabs.isTabVisible(dock._origin_tab_index)

    dock.cell_mode_combo.setCurrentIndex(0)  # back to Cell
    assert visible(dock._cell_row)
    assert visible(dock._name_row)
    assert not visible(dock._coordinate_identity_row)
    assert not dock._tabs.isTabVisible(dock._coordinate_tab_index)


def test_single_component_identity_fields_round_trip_after_the_layout_move(main_window, tmp_path):
    """The Cluster/Role/Name widgets moved to the Source tab, but build()/load()
    still reach them through coordinate_form.<attr> — a full build->load
    round-trip through the PlacerDock keeps them (regression for the move)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_coordinate_placement(dock._placer_path)
    form = dock.coordinate_form
    form.cluster_combo.setCurrentText("FPGA_PERIPH")
    form.role_combo.setCurrentText("R18")
    form.name_edit.setText("my_cap")
    form.mode_combo.setCurrentIndex(0)  # Cartesian
    form.x_edit.setText("10.0")
    form.y_edit.setText("20.0")

    entry = dock._build_entry_dict()

    assert entry["cluster"] == "FPGA_PERIPH"
    assert entry["role"] == "R18"
    assert entry["name"] == "my_cap"

    dock.load_placement(entry)
    assert form.cluster_combo.currentText() == "FPGA_PERIPH"
    assert form.role_combo.currentText() == "R18"
    assert form.name_edit.text() == "my_cap"


def test_coordinate_sheet_field_round_trips(main_window, tmp_path):
    """2026-08-15: the own sheet: field on the Single-component form's
    identity row (Source tab, ordered before Cluster) — build()/load()/clear()
    round-trip, same as cluster/role/name."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_coordinate_placement(dock._placer_path)
    form = dock.coordinate_form
    form.cluster_combo.setCurrentText("AD_DAC")
    form.role_combo.setCurrentText("AD_DAC")
    form.sheet_edit.setCurrentText("Channel_0")

    entry = dock._build_entry_dict()
    assert entry["sheet"] == "Channel_0"

    dock.load_placement(entry)
    assert form.sheet_edit.currentText() == "Channel_0"

    form.clear()
    assert form.sheet_edit.currentText() == ""


def test_coordinate_sheet_field_not_written_when_empty(main_window, tmp_path):
    """Same 'only write if non-empty' pattern as name — an empty Sheet field
    must not inject a stray sheet: key into the saved Single-component entry."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_coordinate_placement(dock._placer_path)
    form = dock.coordinate_form
    form.cluster_combo.setCurrentText("AD_DAC")
    form.role_combo.setCurrentText("AD_DAC")

    entry = dock._build_entry_dict()
    assert "sheet" not in entry


def test_load_placement_round_trips_absolute_xy(main_window, tmp_path):
    """Reverse of _build_entry_dict — ConfigTreeDock's Clone placements
    category feeds a saved clone_placement dict straight back in via
    load_placement()."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [10.5, -3.2],
        "params": {"PWR_IN": "+3V3_CH2", "PWR_OUT": "+3V3_CH2_DIRTY"},
    }

    dock.load_placement(entry)

    assert dock.cluster_edit.currentText() == "Channel_2_PI_Filter"
    assert dock._selected_cell == "pi_filter"
    assert dock.origin_mode_combo.currentIndex() == 0
    assert dock.x_edit.text() == "10.5"
    assert dock.y_edit.text() == "-3.2"
    assert dock._param_edits["PWR_IN"].currentText() == "+3V3_CH2"
    assert dock._param_edits["PWR_OUT"].currentText() == "+3V3_CH2_DIRTY"


def test_load_placement_round_trips_polar_xy(main_window, tmp_path):
    """A saved polar clone_placement (radius_mm/angle_deg) must reload into
    the XY row in Polar mode and round-trip back through _build_entry_dict
    without loss."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {"name": "polar", "cell": "pi_filter",
             "radius_mm": 5.0, "angle_deg": 37.0}

    dock.load_placement(entry)

    ow = dock.origin_widget
    assert dock.origin_mode_combo.currentIndex() == 0
    assert ow._polar_combo.currentIndex() == 1
    assert ow.radius_edit.text() == "5.0"
    assert ow.angle_edit.text() == "37.0"
    # Round-trips back through _build_entry_dict without loss.
    assert dock._build_entry_dict() == entry


def test_load_placement_round_trips_anchor_mode(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "X", "cell": "pi_filter", "xy": [2.0, 0.0],
        "anchor_role": "SOME_ROLE", "anchor_pad": "1", "anchor_cluster": "Channel_1",
    }

    dock.load_placement(entry)

    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.anchor_role_edit.currentText() == "SOME_ROLE"
    assert dock.anchor_pad_edit.text() == "1"
    assert dock.anchor_cluster_edit.currentText() == "Channel_1"
    assert dock.shift_x_edit.text() == "2.0"
    assert dock.shift_y_edit.text() == "0.0"
    # Round-trips back through _build_entry_dict without loss.
    assert dock._build_entry_dict() == entry


def test_load_placement_round_trips_point_mode(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {"name": "X", "cell": "pi_filter", "xy": [1.0, 2.0], "anchor_point": "origin_point"}

    dock.load_placement(entry)

    assert dock.origin_mode_combo.currentIndex() == 2
    assert dock.point_edit.currentText() == "origin_point"
    assert dock.shift_x_edit.text() == "1.0"
    assert dock.shift_y_edit.text() == "2.0"


class _FakeNet:
    def __init__(self, name):
        self.name = name


class _FakeNetAdapter:
    def __init__(self, nets):
        self._nets = nets

    def get_all_nets(self):
        return self._nets


class _FakeNetBoard:
    def __init__(self, nets):
        self.adapter = _FakeNetAdapter(nets)


def test_refresh_known_nets_populates_param_combos(main_window, tmp_path):
    """Requested live 2026-08-02: "сети стоит сделать выпадашками
    (комбобоксами с поиском)" — Params comboboxes should list real net
    names from the board, not stay free-text fields."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    board = _FakeNetBoard([_FakeNet("+3V3"), _FakeNet("GND"), _FakeNet("")])

    dock.refresh_known_nets(board)

    assert dock._known_nets == ["+3V3", "GND"]  # sorted, blank net dropped
    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+3V3", "GND"]
    assert combo.isEditable()  # still accepts a literal not in the list


def test_rebuilt_param_rows_use_cached_known_nets(main_window, tmp_path):
    """A newly-discovered param row (picking a Cell after nets were already
    fetched) must not have to wait for the next ~2s poll tick to show the
    net list — refresh_known_nets() caches on self for this reason."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.refresh_known_nets(_FakeNetBoard([_FakeNet("+3V3"), _FakeNet("GND")]))

    dock.set_selected_cell("pi_filter")  # forces _rebuild_param_rows again

    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+3V3", "GND"]


def test_refresh_known_nets_preserves_per_role_narrowing(main_window, tmp_path):
    """2026-08-16 (net_template_pad): refresh_known_nets (the ~2s poll) resets
    the Nets value combobox to the full board net list, then immediately
    re-applies the per-role narrowing for the row being edited — so the poll
    can't silently undo the narrowing while the user is picking a net."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # cell role "C_IN"
    dock._candidate_nets_narrowing = {"C_IN": ["+1V2"]}
    dock.nets_table.key_edit.setCurrentText("C_IN")

    dock.refresh_known_nets(_FakeNetBoard([_FakeNet("+1V2"), _FakeNet("GND"), _FakeNet("+5V")]))

    narrowed = [dock.nets_table.value_edit.itemText(i) for i in range(dock.nets_table.value_edit.count())]
    assert narrowed == ["+1V2"]


def test_refresh_known_roles_populates_from_snapshot(main_window):
    """1.2 — refresh_known_roles() takes the already-built snapshot (the
    cached BoardConnection.snapshot) instead of calling board.select()
    itself, so a manual refresh builds the full snapshot exactly once."""
    class _Row:
        def __init__(self, ref, role, cluster):
            self.ref = ref
            self.role = role
            self.cluster = cluster

    dock = PlacerDock(main_window)
    snapshot = [_Row("R1", "ROLE_A", "C1"),
                _Row("R2", "ROLE_A", "C2"),
                _Row("R3", "", "C1")]  # blank role dropped, cluster kept

    dock.refresh_known_roles(snapshot)

    roles = [dock.anchor_role_edit.itemText(i) for i in range(dock.anchor_role_edit.count())]
    clusters = [dock.anchor_cluster_edit.itemText(i) for i in range(dock.anchor_cluster_edit.count())]
    cluster_field_items = [dock.cluster_edit.itemText(i) for i in range(dock.cluster_edit.count())]
    assert roles == ["ROLE_A"]
    assert clusters == ["C1", "C2"]
    assert cluster_field_items == ["C1", "C2"]


def test_refresh_known_roles_skips_repopulation_when_unchanged(main_window, monkeypatch):
    """G4.4 (2026-08-12): refresh_known_roles runs on the ~2s poll tick, so
    it must NOT repopulate the combos when the snapshot's Role/Cluster sets
    haven't changed — same set-compare guard as extract.py's
    _rebuild_net_aliases, carried over from the merged-in coordinate dock."""
    class _Row:
        def __init__(self, role, cluster):
            self.role = role
            self.cluster = cluster

    dock = PlacerDock(main_window)
    calls = []
    monkeypatch.setattr(placer_mod, "set_combo_items",
                        lambda combo, items: calls.append(list(items)))

    snapshot = [_Row("R_SERIES", "FPGA_PERIPH"), _Row("R_SERIES", "FPGA_PERIPH")]
    dock.refresh_known_roles(snapshot)
    first_count = len(calls)
    assert first_count > 0

    # Identical sets again — must be a no-op.
    dock.refresh_known_roles(snapshot)
    assert len(calls) == first_count

    # A brand-new role appears — repopulates again.
    dock.refresh_known_roles([_Row("R_SERIES", "FPGA_PERIPH"), _Row("R_TERM", "FPGA_PERIPH")])
    assert len(calls) > first_count


def test_cluster_edit_is_a_searchable_dropdown_that_still_accepts_free_text(main_window):
    """2026-08-04, Denis: "а мы можем сделать кластер в пласере выпадающим?
    Так было бы удобнее..." — Cluster is now a QComboBox populated with
    known cluster names (see refresh_known_roles above), but must stay
    editable so a brand new cluster/clone_placement name (one that doesn't
    exist on the board yet) can still be typed in, same as the anchor
    Role/Cluster combos already work."""
    class _Row:
        def __init__(self, ref, role, cluster):
            self.ref = ref
            self.role = role
            self.cluster = cluster

    dock = PlacerDock(main_window)
    dock.refresh_known_roles([_Row("R1", "ROLE_A", "Existing_Cluster")])

    dock.cluster_edit.setCurrentText("Existing_Cluster")
    assert dock.cluster_edit.currentText() == "Existing_Cluster"

    dock.cluster_edit.setCurrentText("Brand_New_Cluster")
    assert dock.cluster_edit.currentText() == "Brand_New_Cluster"


def test_on_redraw_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """Phase 5.2 — the Redraw button must NOT block the UI thread:
    _on_redraw() collects + validates inputs on the UI thread (including
    loading the Placer config), then hands the plain-data payload to
    start_long_op. PlacerDock has no injected connection, so the shared
    socket comes from the main window."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")

    # _collect_redraw_inputs loads the Placer file to verify the cell is
    # reachable via its include: — fake the load with a config that has it.
    fake_cfg = Config(
        cells={"pi_filter": Cell(name="pi_filter", vias=[], tracks=[],
                                 clone_placements=[], components=[])})
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["fn"] = fn
        captured["on_success"] = on_success
        captured["on_error"] = on_error
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(placer_mod, "start_long_op", _fake_start)

    dock._on_redraw()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.redraw_button, dock.save_button)
    # Bound methods: each access creates a fresh object, so compare with ==
    # (equality checks __self__ + __func__) rather than `is`.
    assert captured["fn"] == dock._run_redraw
    assert captured["on_success"] == dock._finish_redraw
    assert captured["on_error"] == dock._on_redraw_failed

    payload = captured["args"][0]
    assert payload["name"] == "Channel_2_PI_Filter"
    assert payload["placer_path"] == placer_file
    assert payload["cfg"] is fake_cfg
    assert payload["ctx"] is fake_ctx


# ── Nets tab: _KeyValueTableEditor + nets:/net_overrides:/refs: (2026-08-06) ──

def test_key_value_table_editor_add_update_remove():
    editor = placer_mod._KeyValueTableEditor("Key", "Value")

    editor.key_edit.setCurrentText("HEAVY")
    editor.value_edit.setCurrentText("+3V3")
    editor._on_add_or_update()
    assert editor.to_dict() == {"HEAVY": "+3V3"}
    assert editor.table.rowCount() == 1
    assert editor.table.item(0, 0).text() == "HEAVY"
    assert editor.table.item(0, 1).text() == "+3V3"

    # Add with an existing key updates in place, not a second row.
    editor.key_edit.setCurrentText("HEAVY")
    editor.value_edit.setCurrentText("GND")
    editor._on_add_or_update()
    assert editor.to_dict() == {"HEAVY": "GND"}
    assert editor.table.rowCount() == 1

    editor.table.selectRow(0)
    editor._on_remove()
    assert editor.to_dict() == {}
    assert editor.table.rowCount() == 0


def test_key_value_table_editor_ignores_blank_key_or_value():
    editor = placer_mod._KeyValueTableEditor("Key", "Value")
    editor.value_edit.setCurrentText("GND")
    editor._on_add_or_update()
    assert editor.to_dict() == {}


def test_key_value_table_editor_load_dict_round_trips():
    editor = placer_mod._KeyValueTableEditor("Key", "Value")
    editor.load_dict({"HEAVY": "+3V3", "LIGHT": "GND"})
    assert editor.to_dict() == {"HEAVY": "+3V3", "LIGHT": "GND"}
    assert editor.table.rowCount() == 2


def test_build_entry_dict_includes_nets_net_overrides_refs_in_cell_mode(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_1")
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock.nets_table.load_dict({"C_IN": "+5V"})
    dock.net_overrides_table.load_dict({"+5V": "+5V_DIRTY"})
    dock.refs_table.load_dict({"C_IN": "C12"})

    entry = dock._build_entry_dict()
    assert entry["nets"] == {"C_IN": "+5V"}
    assert entry["net_overrides"] == {"+5V": "+5V_DIRTY"}
    assert entry["refs"] == {"C_IN": "C12"}
    cp = load_clone_placement(entry)  # must validate against the real backend loader
    assert cp.nets == {"C_IN": "+5V"}
    assert cp.net_overrides == {"+5V": "+5V_DIRTY"}
    assert cp.refs == {"C_IN": "C12"}


def test_build_entry_dict_omits_empty_nets_tables(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_1")
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    entry = dock._build_entry_dict()
    assert "nets" not in entry
    assert "net_overrides" not in entry
    assert "refs" not in entry


def test_load_placement_round_trips_nets_net_overrides_refs(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "Channel_1", "cell": "pi_filter", "xy": [1.0, 2.0],
        "nets": {"C_IN": "+5V"}, "net_overrides": {"+5V": "+5V_DIRTY"}, "refs": {"C_IN": "C12"},
    }

    dock.load_placement(entry)

    assert dock.nets_table.to_dict() == {"C_IN": "+5V"}
    assert dock.net_overrides_table.to_dict() == {"+5V": "+5V_DIRTY"}
    assert dock.refs_table.to_dict() == {"C_IN": "C12"}


def test_new_placement_clears_nets_tables(main_window, tmp_path):
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.nets_table.load_dict({"C_IN": "+5V"})
    dock.net_overrides_table.load_dict({"+5V": "+5V_DIRTY"})
    dock.refs_table.load_dict({"C_IN": "C12"})

    dock.new_placement(placer_file)

    assert dock.nets_table.to_dict() == {}
    assert dock.net_overrides_table.to_dict() == {}
    assert dock.refs_table.to_dict() == {}


def test_selected_cell_scopes_nets_and_refs_key_choices_not_the_whole_board(main_window, tmp_path):
    """Regression (found live 2026-08-06, Denis: "зачем в выпадашках ВСЕ
    доступные на плате роли? Нас же интересуют только роли относящиеся к
    Pi_Filter_p5v?") — nets:/refs: are only ever consulted for a role
    that's actually one of the picked cell's own components: (see
    resolve_roles_by_nets) — the Role key choices must be scoped to that
    cell, not every role seen anywhere on the live board. _make_cell_and_
    dock's "pi_filter" cell has exactly one component, role "C_IN" (see
    its own docstring)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    nets_items = {dock.nets_table.key_edit.itemText(i) for i in range(dock.nets_table.key_edit.count())}
    refs_items = {dock.refs_table.key_edit.itemText(i) for i in range(dock.refs_table.key_edit.count())}
    assert nets_items == {"C_IN"}
    assert refs_items == {"C_IN"}


def test_refresh_known_roles_does_not_widen_nets_and_refs_key_choices(main_window, tmp_path):
    """refresh_known_roles() (the live-board poll) must NOT overwrite the
    cell-scoped Role choices set by set_selected_cell — otherwise the next
    poll tick would silently widen them back to every board-wide role."""
    from types import SimpleNamespace
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    dock.refresh_known_roles([SimpleNamespace(role="HEAVY", cluster=""),
                              SimpleNamespace(role="LIGHT", cluster="")])

    nets_items = {dock.nets_table.key_edit.itemText(i) for i in range(dock.nets_table.key_edit.count())}
    assert nets_items == {"C_IN"}


def test_refresh_known_nets_feeds_nets_and_net_overrides_value_choices(main_window, tmp_path):
    from types import SimpleNamespace
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    board = SimpleNamespace(adapter=SimpleNamespace(
        get_all_nets=lambda: [SimpleNamespace(name="+3V3"), SimpleNamespace(name="GND")]))

    dock.refresh_known_nets(board)

    nets_values = {dock.nets_table.value_edit.itemText(i) for i in range(dock.nets_table.value_edit.count())}
    override_keys = {dock.net_overrides_table.key_edit.itemText(i)
                     for i in range(dock.net_overrides_table.key_edit.count())}
    assert nets_values == {"+3V3", "GND"}
    assert override_keys == {"+3V3", "GND"}


# ── Nets "Auto-fill from board" (2026-08-12) ─────────────────────────────

class _FakeAutofillFootprint:
    def __init__(self, ref, role, cluster, nets):
        self.reference_field = SimpleNamespace(text=SimpleNamespace(value=ref))
        self._role = role
        self._cluster = cluster
        self._nets = nets


class _FakeAutofillAdapter:
    def __init__(self, footprints):
        self._footprints = footprints

    def get_footprints(self):
        return self._footprints

    def get_field_value(self, fp, field_name):
        if field_name == ROLE_FIELD_NAME:
            return fp._role
        if field_name == CLUSTER_FIELD_NAME:
            return fp._cluster
        return None

    def get_footprint_pads(self, fp):
        # Pads get sequential numbers 1..N (2026-08-16, net_template_pad) so
        # get_pad_by_number can resolve a role's net_template_pad.
        return [SimpleNamespace(net=SimpleNamespace(name=n), number=str(i + 1))
                for i, n in enumerate(fp._nets)]

    def get_pad_by_number(self, fp, pad_number):
        return next((p for p in self.get_footprint_pads(fp) if p.number == str(pad_number)), None)


class _FakeAutofillBoard:
    def __init__(self, footprints):
        self.adapter = _FakeAutofillAdapter(footprints)


def _make_two_role_cell_and_dock(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "pi_filter2": {
            "components": [
                {"role": "C_IN_BULK", "offset_along_mm": 0, "offset_across_mm": 0, "angle_deg": 0},
                {"role": "PI_FB", "offset_along_mm": 1, "offset_across_mm": 1, "angle_deg": 0},
            ],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root2.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.set_selected_cell("pi_filter2")
    return dock, cells_file, placer_file


def test_autofill_nets_requires_a_cell(main_window, caplog):
    dock = PlacerDock(main_window)
    dock._do_autofill_nets()
    assert any("Pick a Cell first" in r.message for r in caplog.records)


def test_autofill_nets_requires_placement_cluster(main_window, tmp_path, caplog):
    """Split 2026-08-14: the Auto-fill query key is now the Source tab's
    Cluster field (clone.name), not the Origin tab's Anchor cluster."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock._do_autofill_nets()
    assert any("Set Cluster on the Source tab first" in r.message for r in caplog.records)


def test_autofill_nets_requires_board_connection(main_window, tmp_path, caplog):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = None

    dock._do_autofill_nets()

    assert any("Not connected" in r.message for r in caplog.records)


def test_do_autofill_nets_fills_unambiguous_role(main_window, tmp_path, caplog):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # single role "C_IN"
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN", "Out_Pi_Filter_N2V5", ["+1V2", "GND"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN": "+1V2"}
    assert any("Auto-filled all 1 role(s)" in r.message for r in caplog.records)


def test_do_autofill_nets_leaves_ambiguous_role_for_manual_entry(main_window, tmp_path, caplog):
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        # PI_FB bridges two nets -> not reducible to one identifying net.
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2"}
    assert any("Auto-filled 1/2 role(s)" in r.message for r in caplog.records)
    assert any("PI_FB" in r.message for r in caplog.records)


def test_finish_autofill_nets_silence_comes_from_the_result_not_a_field(main_window, tmp_path, caplog):
    """Bug 2 (handoff_2026_08_13_focus_and_autorole_bugs): quiet travels
    through the payload/result, NOT a shared dock field — a second auto-fill
    started while the first is still running can no longer overwrite the
    first's silence flag. The manual button (quiet=False) shows the full
    success line; the auto-trigger (quiet=True) stays silent on full success,
    read from THIS run's own result."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # cell has role C_IN

    dock._finish_autofill_nets({"suggestions": {"C_IN": "+3V3"}, "roles": ["C_IN"], "quiet": False})
    assert any("Auto-filled all 1 role(s)" in r.message for r in caplog.records)

    caplog.clear()
    dock._finish_autofill_nets({"suggestions": {"C_IN": "+3V3"}, "roles": ["C_IN"], "quiet": True})
    assert not any("Auto-filled all 1 role(s)" in r.message for r in caplog.records)


def test_collect_autofill_nets_inputs_carries_quiet_in_the_payload(main_window, tmp_path):
    """The quiet flag starts its payload->result trip in _collect_autofill_nets_
    inputs — without it the worker can't echo it back (bug 2 regression guard)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeNetBoard([_FakeNet("+3V3")])

    payload = dock._collect_autofill_nets_inputs(quiet=True)

    assert payload is not None
    assert payload["quiet"] is True


def test_do_autofill_nets_does_not_stomp_a_row_it_could_not_resolve(main_window, tmp_path):
    """A role Auto-fill can't determine keeps whatever was already typed for
    it — same "never overwrite an already-filled value" discipline as every
    other auto-fill in this GUI (ExtractDock's cluster autofill, etc.)."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.nets_table.load_dict({"PI_FB": "+1V2_VCCINT"})  # typed by hand earlier
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2", "PI_FB": "+1V2_VCCINT"}


def test_do_autofill_nets_fills_multi_pad_role_with_net_template_pad(main_window, tmp_path, caplog):
    """THE fix (2026-08-16, net_template_pad): a multi-pad role whose cell
    carries net_template_pad now fills deterministically (that specific pad's
    net is read directly), instead of the old "exactly one non-rule net"
    skip — this is the 7/13 -> 13/13 difference reproduced on the GUI path."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "ldo_cell": {
            "components": [
                {"role": "LDO_ADJ", "net_template": "NET_{p}", "net_template_pad": "3"},
            ],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.set_selected_cell("ldo_cell")
    dock.cluster_edit.setCurrentText("LDO_ADJ_P2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("U2", "LDO_ADJ", "LDO_ADJ_P2V5", ["+2V5_ADJ", "+2V5_DIRTY", "+5V"]),
    ])

    dock._do_autofill_nets()

    # pad 3 of U2 -> "+5V", filled without any "exactly one net" requirement.
    assert dock.nets_table.to_dict() == {"LDO_ADJ": "+5V"}
    assert any("Auto-filled all 1 role(s)" in r.message for r in caplog.records)


def test_do_autofill_nets_resolves_same_as_role_via_sibling(main_window, tmp_path, caplog):
    """2026-08-16 (net_template_same_as_role): a multi-net role whose cell
    references ANOTHER role (net_template_same_as_role) is resolved live via
    that sibling on the same cluster — cross-instance-safe for electrically
    symmetric 2-pin parts, instead of a routing-artifact pad number."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "ldo_cell": {
            "components": [
                {"role": "R_FB_BOT", "net_template": "NET_{p}"},
                {"role": "R_FB_TOP", "net_template": "NET_{p}",
                 "net_template_same_as_role": "R_FB_BOT"},
            ],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.set_selected_cell("ldo_cell")
    dock.cluster_edit.setCurrentText("LDO_ADJ_P2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("R10", "R_FB_TOP", "LDO_ADJ_P2V5", ["+2V5_ADJ", "-2V5_DIRTY"]),
        _FakeAutofillFootprint("R11", "R_FB_BOT", "LDO_ADJ_P2V5", ["+2V5_ADJ", "GND"]),
    ])

    dock._do_autofill_nets()

    # R_FB_BOT is lemma-2-safe on "+2V5_ADJ" (its own fill); R_FB_TOP shares
    # that net via the same-as-role reference, NOT a pad number.
    assert dock.nets_table.to_dict() == {"R_FB_BOT": "+2V5_ADJ", "R_FB_TOP": "+2V5_ADJ"}
    assert any("Auto-filled all 2 role(s)" in r.message for r in caplog.records)


def test_run_autofill_nets_carries_candidate_nets_narrowing(main_window, tmp_path):
    """2026-08-16 (net_template_pad): the auto-fill worker also computes the
    per-role candidate-net narrowing (for the Net-combobox) in the SAME live
    run — no extra socket round-trip; _finish_autofill_nets caches it."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # cell role "C_IN"
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN", "Out_Pi_Filter_N2V5", ["+1V2", "GND"]),
    ])
    payload = dock._collect_autofill_nets_inputs()
    assert payload is not None

    result = dock._run_autofill_nets(payload)

    assert result["suggestions"] == {"C_IN": "+1V2"}
    assert result["narrowed"] == {"C_IN": ["+1V2"]}  # GND is a rule net, filtered
    dock._finish_autofill_nets(result)
    assert dock._candidate_nets_narrowing == {"C_IN": ["+1V2"]}


def test_finish_autofill_nets_narrows_params_combo(main_window, tmp_path):
    """2026-08-16 evening: the SAME auto-fill run also narrows the Params
    tab. Cell "pi_filter" has one role, C_IN, whose net_template is EXACTLY
    "{PWR_IN}" — that role's own resolved net (+1V2, GND filtered as a rule
    net) becomes PWR_IN's narrowed choice instead of the full board list."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # role "C_IN", param "PWR_IN"
    dock._known_nets = ["+1V2", "GND", "+5V"]
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN", "Out_Pi_Filter_N2V5", ["+1V2", "GND"]),
    ])
    payload = dock._collect_autofill_nets_inputs()
    assert payload is not None
    assert dock._param_placeholder_roles == {"PWR_IN": ["C_IN"]}

    result = dock._run_autofill_nets(payload)
    dock._finish_autofill_nets(result)

    assert dock._param_narrowing == {"PWR_IN": ["+1V2"]}
    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+1V2"]


def test_param_placeholder_not_exactly_one_role_stays_unnarrowed(main_window, tmp_path):
    """A placeholder used inside a COMPOUND net_template (e.g. a hierarchical
    net path with a literal prefix/suffix around it) can't be reverse-mapped
    to a single pad's net — no role's net_template equals the placeholder
    alone, so it's simply absent from _param_placeholder_roles and the combo
    keeps the full board list, same graceful degradation as everywhere else
    in this mechanism."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "sheet_cell": {
            "components": [
                {"role": "AD_DAC", "offset_along_mm": 0, "offset_across_mm": 0,
                 "angle_deg": 0, "net_template": "/{SHEET}/DAC/+3V3_AVDD"},
            ],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.set_selected_cell("sheet_cell")
    dock._known_nets = ["+1V2", "GND"]
    dock._rebuild_param_rows()  # re-populate now that _known_nets is set
    dock.cluster_edit.setCurrentText("Channel_0")
    main_window.connection.board = _FakeAutofillBoard([])

    payload = dock._collect_autofill_nets_inputs()
    assert payload is not None
    assert dock._param_placeholder_roles == {}

    combo = dock._param_edits["SHEET"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+1V2", "GND"]


def test_refresh_known_nets_preserves_param_narrowing(main_window, tmp_path):
    """2026-08-16 evening: same "poll can't silently undo it" fix as the Nets
    tab's own (test_refresh_known_nets_preserves_per_role_narrowing) — for
    the Params comboboxes."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # param "PWR_IN"
    dock._param_narrowing = {"PWR_IN": ["+1V2"]}

    dock.refresh_known_nets(_FakeNetBoard([_FakeNet("+1V2"), _FakeNet("GND"), _FakeNet("+5V")]))

    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+1V2"]


def test_nets_key_changed_narrows_value_choices(main_window, tmp_path):
    """2026-08-16 (net_template_pad): picking a role row in the Nets table
    narrows the Net combobox to that role's real candidate nets (cached from
    the last auto-fill worker result); a role with no narrowing explicitly
    falls back to the full board net list."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock._known_nets = ["+1V2", "GND", "+5V"]
    dock._candidate_nets_narrowing = {"C_IN": ["+1V2"]}
    dock.nets_table.set_value_choices(dock._known_nets)

    dock.nets_table.key_edit.setCurrentText("C_IN")
    dock._on_nets_key_changed("C_IN")
    narrowed = [dock.nets_table.value_edit.itemText(i) for i in range(dock.nets_table.value_edit.count())]
    assert narrowed == ["+1V2"]

    # No narrowing for this role -> full board list, not the stale narrowed set.
    dock.nets_table.key_edit.setCurrentText("OTHER_ROLE")
    dock._on_nets_key_changed("OTHER_ROLE")
    fallback = [dock.nets_table.value_edit.itemText(i) for i in range(dock.nets_table.value_edit.count())]
    assert fallback == ["+1V2", "GND", "+5V"]


def test_on_autofill_nets_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([])

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["fn"] = fn
        captured["on_success"] = on_success
        captured["on_error"] = on_error
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(placer_mod, "start_long_op", _fake_start)

    dock._on_autofill_nets_from_board()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.autofill_nets_button,)
    assert captured["fn"] == dock._run_autofill_nets
    assert captured["on_success"] == dock._finish_autofill_nets
    assert captured["on_error"] == dock._on_autofill_nets_failed

    payload = captured["args"][0]
    assert payload["role_hints"] == {"C_IN": (None, None)}
    assert payload["cluster"] == "Out_Pi_Filter_N2V5"
    assert payload["adapter"] is main_window.connection.board.adapter


# ── Auto-fill safety + auto-trigger (2026-08-13, plan
# placer_autofill_default_and_docs) ─────────────────────────────────────

def test_do_autofill_nets_does_not_overwrite_a_prefilled_role(main_window, tmp_path, caplog):
    """Plan p.1: Auto-fill fills ONLY blank roles — a role the user already
    typed a value for is never overwritten, even when the worker suggests a
    different net for it (the auto-trigger re-fires on every Cell+Cluster
    commit, so an overwrite here would silently clobber manual edits on each
    re-fire; the manual button gets the same, strictly safer behaviour)."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.nets_table.load_dict({"C_IN_BULK": "+1V2_VCCINT"})  # typed by hand
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    # C_IN_BULK keeps the manual value; PI_FB stays blank (unresolvable) and
    # is reported as left for manual entry.
    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2_VCCINT"}
    assert any("PI_FB" in r.message for r in caplog.records)


def test_auto_trigger_fires_on_commit_not_typing(main_window, tmp_path, monkeypatch):
    """Plan p.2: the auto-trigger fires on COMMIT signals — set_selected_cell
    with a ready cluster, combo activated, line-edit editingFinished — and
    NEVER on per-keystroke text change (which would flood the kipy socket
    with live board reads mid-typing)."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])
    calls = []
    monkeypatch.setattr(dock, "_start_autofill_nets_op", lambda payload: calls.append(payload))

    line_edit = dock.cluster_edit.lineEdit()
    # Typing (per-keystroke text change) must NOT fire.
    line_edit.setText("Out_Pi")
    line_edit.textChanged.emit("Out_Pi")
    assert calls == []

    # Commit the cluster via editingFinished (Enter/focus-out) -> fires once.
    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    line_edit.editingFinished.emit()
    assert len(calls) == 1

    # Picking the cluster from the dropdown (activated) fires too.
    dock.cluster_edit.activated.emit(0)
    assert len(calls) == 2

    # Picking a Cell with a ready cluster fires as well.
    dock.set_selected_cell("pi_filter2")
    assert len(calls) == 3


def test_auto_trigger_no_ops_without_cell_or_cluster(main_window, tmp_path, monkeypatch, caplog):
    """Plan p.2: the auto-trigger is a silent no-op until BOTH halves of the
    pair are ready — not an error, just nothing to fill yet."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
    ])
    calls = []
    monkeypatch.setattr(dock, "_start_autofill_nets_op", lambda payload: calls.append(payload))

    before = len(caplog.records)
    dock._maybe_autofill_nets()  # cell set (fixture), but no cluster yet
    assert calls == []

    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    dock.new_placement(dock._placer_path)  # clears the cell
    dock._maybe_autofill_nets()  # cluster set, but no cell now
    assert calls == []
    assert len(caplog.records) == before  # silent no-op: nothing logged


def test_auto_trigger_ignores_anchor_cluster_edit(main_window, tmp_path, monkeypatch):
    """Split 2026-08-14: the auto-trigger now fires on the Source tab's
    Cluster field only — committing the Origin tab's Anchor cluster
    (anchor_cluster_edit) must NOT trigger role auto-fill anymore, even when
    the rest of the pair (cell + Cluster) is ready."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    main_window.connection.board = _FakeAutofillBoard([])
    calls = []
    monkeypatch.setattr(dock, "_start_autofill_nets_op", lambda payload: calls.append(payload))

    dock.cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")  # pair is otherwise ready

    dock.anchor_cluster_edit.setCurrentText("Some_Anchor_Cluster")
    dock.anchor_cluster_edit.activated.emit(0)
    dock.anchor_cluster_edit.lineEdit().editingFinished.emit()
    assert calls == []

    # Sanity: the same commit on cluster_edit DOES fire.
    dock.cluster_edit.activated.emit(0)
    assert len(calls) == 1


def test_params_section_hidden_for_cell_without_placeholders(main_window, tmp_path):
    """Plan p.4: the Params section is hidden when the Cell has no
    {placeholder} anywhere."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)  # no placeholders
    assert dock._params_label.isHidden() is True
    assert dock._params_container.isHidden() is True


def test_params_section_visible_for_cell_with_placeholders(main_window, tmp_path):
    """Plan p.4: the Params section is shown (and populated) when the Cell
    has {placeholder}s."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # {PWR_IN}, {PWR_OUT}
    assert dock._params_label.isHidden() is False
    assert dock._params_container.isHidden() is False
    assert sorted(dock._param_edits.keys()) == ["PWR_IN", "PWR_OUT"]


def test_load_placement_clears_stale_anchor_cluster(main_window, tmp_path):
    """Plan p.3: opening record B after record A must not leave A's Anchor
    cluster in the field — a stale value would feed the auto-fill trigger
    with the WRONG cluster before B's own nets load. The race is closed at
    the source: Origin is cleared at the start of load_placement()."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    dock.load_placement({"name": "A", "cell": "pi_filter", "xy": [1.0, 1.0],
                         "anchor_role": "SOME_ROLE", "anchor_cluster": "Channel_X"})
    assert dock.anchor_cluster_edit.currentText() == "Channel_X"

    # Record B: same cell, plain absolute xy (no anchor) — must NOT inherit X.
    dock.load_placement({"name": "B", "cell": "pi_filter", "xy": [2.0, 2.0]})
    assert dock.anchor_cluster_edit.currentText() == ""


# ── _load_target_config sheet_names root fallback (2026-08-15,
# plan_2026_08_15_redraw_sheet_names_from_root.md) ───────────────────────

def _write_schematic_dir(tmp_path, dirname, sheet_names):
    """Write a minimal *.kicad_sch file so load_config()/build_sheet_name_map
    resolves real sheet names — same shape as test_rename.py's own
    test_collect_all_sheet_names_reads_schematic_dir (sexpdata-parseable,
    line formatting is irrelevant)."""
    sch = tmp_path / dirname
    sch.mkdir()
    sheets = "".join(
        f'  (sheet (uuid "{uuid}") (property "Sheetname" "{name}"))\n'
        for uuid, name in sheet_names.items()
    )
    (sch / "root.kicad_sch").write_text(f"(kicad_sch\n{sheets})\n", encoding="utf-8")


def test_load_target_config_falls_back_to_root_sheet_names_when_leaf_has_none(main_window, tmp_path):
    """The bug (found live 2026-08-15 on profiles/3ch-awg-tia/): the Placer
    had components.yaml open, schematic_dir: lives only on the project ROOT
    (3ch-awg-tia.yaml) which INCLUDES components.yaml — resolve_includes()
    merges downward only, so loading the leaf directly resolves an EMPTY
    sheet_names even though the root resolves real names, making Sheet
    narrowing a silent no-op at Redraw. _load_target_config() must fall back
    to the root's sheet_names when the leaf's own came up empty."""
    _write_schematic_dir(tmp_path, "sch",
                         {"11111111-1111-1111-1111-111111111111": "Channel_0"})
    root = tmp_path / "3ch-awg-tia.yaml"
    root.write_text("schematic_dir: sch\ninclude:\n  - components.yaml\n", encoding="utf-8")
    leaf = tmp_path / "components.yaml"
    leaf.write_text("clone_placements: []\n", encoding="utf-8")

    dock = PlacerDock(main_window)
    dock._root_path = root
    dock._placer_path = leaf

    loaded = dock._load_target_config()
    assert loaded is not None
    _, ctx = loaded
    assert ctx.sheet_names == {"11111111-1111-1111-1111-111111111111": "Channel_0"}


def test_load_target_config_keeps_leafs_own_sheet_names_when_present(main_window, tmp_path):
    """Regression guard: the root fallback must only fire when the leaf
    genuinely has no sheet_names of its own — a leaf that resolves its own
    (declares its own schematic_dir:) keeps them, never overwritten by the
    root's (a leaf that is itself the root is the same case)."""
    _write_schematic_dir(tmp_path, "sch_leaf",
                         {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": "Channel_0"})
    _write_schematic_dir(tmp_path, "sch_root",
                         {"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": "Root Sheet"})
    root = tmp_path / "root.yaml"
    root.write_text("schematic_dir: sch_root\n", encoding="utf-8")
    leaf = tmp_path / "leaf.yaml"
    leaf.write_text("schematic_dir: sch_leaf\n", encoding="utf-8")

    dock = PlacerDock(main_window)
    dock._root_path = root
    dock._placer_path = leaf

    loaded = dock._load_target_config()
    assert loaded is not None
    _, ctx = loaded
    assert ctx.sheet_names == {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": "Channel_0"}


def test_load_target_config_root_path_none_leaves_empty_sheet_names(main_window, tmp_path):
    """Old behavior preserved: with no root path known at all, a leaf with
    no schematic_dir keeps an empty sheet_names — no exception, no
    fallback."""
    leaf = tmp_path / "leaf.yaml"
    leaf.write_text("clone_placements: []\n", encoding="utf-8")

    dock = PlacerDock(main_window)
    dock._placer_path = leaf  # _root_path stays None

    loaded = dock._load_target_config()
    assert loaded is not None
    _, ctx = loaded
    assert ctx.sheet_names == {}


# ── Cluster/Name auto-fill vs. hard overwrite (2026-08-15, plan
# cluster_field_autofill_not_hard_overwrite) ──────────────────────────────
# The Cell-mode Cluster field doubles as ClonePlacement.name — the identity
# key upsert_list_entry() matches on. Clicking a Cluster group node in the
# live Components tree (RoleClusterTreeDock.cluster_picked ->
# set_cluster_name) used to overwrite it UNCONDITIONALLY, so a stray tree
# click while editing/renaming a loaded placement silently swapped its
# identity back and the next save appended a duplicate. set_cluster_name()
# now only fills a genuinely BLANK field; a _cluster_identity_dirty flag
# marks the field "owned" by the user (typed/picked, or loaded from a saved
# entry) and is reset only by new_placement().

def test_set_cluster_name_fills_blank_field(main_window, tmp_path):
    """Regression for the original 2026-08-01 convenience: clicking a Cluster
    group node auto-fills a BLANK Placer form."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    dock.set_cluster_name("PIF_AVDD")
    assert dock.cluster_edit.currentText() == "PIF_AVDD"


def test_set_cluster_name_does_not_overwrite_after_manual_edit(main_window, tmp_path):
    """2026-08-15 fix: once the user typed/picked a Cluster by hand, a stray
    tree click must not silently swap the placement's identity back."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    dock.cluster_edit.setCurrentText("CH0_PIF_AVDD")
    dock.cluster_edit.lineEdit().editingFinished.emit()  # user commit -> dirty
    dock.set_cluster_name("PIF_AVDD")
    assert dock.cluster_edit.currentText() == "CH0_PIF_AVDD"


def test_set_cluster_name_does_not_overwrite_loaded_entry(main_window, tmp_path):
    """2026-08-15 fix: an already-saved entry owns its identity — the tree
    click auto-fill must not pull the form off the loaded record."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.set_cluster_name("ДругоеИмя")
    assert dock.cluster_edit.currentText() == "PIF_AVDD"


def test_new_placement_resets_dirty_flag_so_autofill_works_again(main_window, tmp_path):
    """2026-08-15 fix: the dirty flag is reset ONLY when the form goes back to
    blank (new_placement) — so a fresh placement gets the auto-fill again."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    assert dock._cluster_identity_dirty is True
    dock.new_placement(dock._placer_path)
    assert dock._cluster_identity_dirty is False
    dock.set_cluster_name("X")
    assert dock.cluster_edit.currentText() == "X"


def test_auto_fill_itself_commits_the_dirty_flag(main_window, tmp_path):
    """2026-08-15 fix detail: a second tree click on a DIFFERENT Cluster must
    not immediately overwrite the first auto-filled value."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    dock.set_cluster_name("PIF_AVDD")
    assert dock._cluster_identity_dirty is True
    dock.set_cluster_name("ДругоеИмя")
    assert dock.cluster_edit.currentText() == "PIF_AVDD"


# ── Placer name (save/--only identity) vs. Cluster (2026-08-15, plan
# clone_placement_placer_name_split) ──────────────────────────────────────
# The Cell-mode Cluster field (ClonePlacement.name) is BOTH the tag written
# onto the board AND, historically, the identity upsert_list_entry matched
# on — so editing Cluster on an already-saved entry appended a duplicate.
# The new optional Placer name field (ClonePlacement.placer_name) carries the
# save/--only identity separately; it auto-fills from Cluster ONLY while
# creating a brand new placement (_placer_name_dirty, same pattern as the
# Cluster tree-click fix above) and is reset only by new_placement().

def test_placer_name_autofills_from_cluster_on_new_placement(main_window, tmp_path):
    """Creating a placement: committing a Cluster value fills Placer name
    from it (the original convenience, applied to the new identity field)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.cluster_edit.lineEdit().editingFinished.emit()  # Cluster commit
    assert dock.placer_name_edit.text() == "PIF_AVDD"


def test_placer_name_does_not_autofill_after_direct_edit(main_window, tmp_path):
    """Once the user types Placer name by hand, Cluster edits must not
    overwrite it."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    dock.placer_name_edit.setText("MY_OWN_NAME")
    dock.placer_name_edit.editingFinished.emit()  # user commit -> dirty
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.cluster_edit.lineEdit().editingFinished.emit()
    assert dock.placer_name_edit.text() == "MY_OWN_NAME"


def test_placer_name_does_not_autofill_while_editing_loaded_entry(main_window, tmp_path):
    """KEY Denis scenario: load a saved entry, then edit Cluster several
    times — Placer name must NOT follow, so the next save no longer spawns a
    duplicate."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    assert dock.placer_name_edit.text() == "CH0_PIF_AVDD"
    dock.cluster_edit.setCurrentText("PIF_AVDD2")
    dock.cluster_edit.lineEdit().editingFinished.emit()
    assert dock.placer_name_edit.text() == "CH0_PIF_AVDD"
    dock.cluster_edit.setCurrentText("PIF_AVDD3")
    dock.cluster_edit.lineEdit().editingFinished.emit()
    assert dock.placer_name_edit.text() == "CH0_PIF_AVDD"


def test_build_entry_dict_omits_placer_name_when_equal_to_cluster(main_window, tmp_path):
    """Same "don't write a redundant field" principle as sheet: Placer name
    that still equals Cluster is left out of the saved entry."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    # Load a full entry so the Origin widget has a position _build_entry_dict
    # can read back (same setup as the existing round-trip tests).
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.placer_name_edit.setText("PIF_AVDD")
    entry = dock._build_entry_dict()
    assert entry["name"] == "PIF_AVDD"
    assert "placer_name" not in entry


def test_build_entry_dict_includes_placer_name_when_different(main_window, tmp_path):
    """Placer name that differs from Cluster is written — that is the whole
    point of the split."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.placer_name_edit.setText("CH0_PIF_AVDD")
    entry = dock._build_entry_dict()
    assert entry["name"] == "PIF_AVDD"
    assert entry["placer_name"] == "CH0_PIF_AVDD"


def test_new_placement_resets_placer_name_dirty_flag(main_window, tmp_path):
    """A fresh form wants auto-fill again: load (dirty) -> new_placement ->
    Cluster commit fills Placer name."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    assert dock._placer_name_dirty is True
    dock.new_placement(dock._placer_path)
    assert dock._placer_name_dirty is False
    assert dock.placer_name_edit.text() == ""
    dock.cluster_edit.setCurrentText("X")
    dock.cluster_edit.lineEdit().editingFinished.emit()
    assert dock.placer_name_edit.text() == "X"


# ── Save from the form itself must rename, not duplicate (2026-08-15, plan
# placer_form_save_renames_not_duplicates) ───────────────────────────────
# _do_save() used to upsert by the CURRENT (edited) identity only — upsert has
# no memory of what the form loaded, so renaming Cluster/Placer name in the
# form and hitting Save always appended a duplicate. A _loaded_clone_identity
# attribute now remembers the loaded identity; when the identity being saved
# differs, the OLD entry is removed first (delete_entry, same mechanism as the
# Config tree's Delete).

def test_save_after_renaming_placer_name_removes_old_entry(main_window, tmp_path):
    """KEY Denis scenario: load an entry, rename Placer name in the form, Save
    — the OLD entry must be gone, exactly one with the new identity remains,
    and Cluster stays untouched."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write_yaml(placer_file, {"clone_placements": [
        {"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD", "cell": "pi_filter",
         "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.placer_name_edit.setText("CH1_PIF_AVDD")
    dock._do_save()

    entries = yaml.safe_load(placer_file.read_text(encoding="utf-8"))["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["placer_name"] == "CH1_PIF_AVDD"
    assert entries[0]["name"] == "PIF_AVDD"  # Cluster tag untouched


def test_save_after_renaming_cluster_without_placer_name_removes_old_entry(main_window, tmp_path):
    """Same fix applies to plain Cluster renames (no placer_name): identity is
    the raw name, so changing Cluster in the form and saving must replace, not
    duplicate."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write_yaml(placer_file, {"clone_placements": [
        {"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("CH0_PIF_AVDD")
    dock._do_save()

    entries = yaml.safe_load(placer_file.read_text(encoding="utf-8"))["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["name"] == "CH0_PIF_AVDD"


def test_save_without_identity_change_still_replaces_in_place(main_window, tmp_path):
    """Regression guard: loading without changing the identity (or changing
    something else entirely) must keep the old replace-in-place upsert — no
    spurious delete cycles."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write_yaml(placer_file, {"clone_placements": [
        {"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"name": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock._do_save()
    dock._do_save()

    assert len(yaml.safe_load(placer_file.read_text(encoding="utf-8"))["clone_placements"]) == 1


def test_save_new_placement_does_not_trigger_delete(main_window, tmp_path, monkeypatch):
    """new_placement() has no prior identity — Save must just append; the
    old-entry-removal path must not run at all."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(dock._placer_path)
    calls = []
    monkeypatch.setattr(placer_mod, "delete_entry",
                        lambda *a, **k: calls.append((a, k)) or {"backups": []})
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.placer_name_edit.setText("PIF_AVDD")
    dock.set_selected_cell("pi_filter")
    dock.origin_widget.load(mode="xy", x=1.0, y=1.0)
    dock._do_save()

    assert calls == []
    entries = yaml.safe_load(placer_file.read_text(encoding="utf-8"))["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["name"] == "PIF_AVDD"


def test_save_twice_in_a_row_after_rename_does_not_error(main_window, tmp_path):
    """Renaming then saving AGAIN without changes must not try to delete the
    already-removed old entry (identity now matches, delete path not entered)."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write_yaml(placer_file, {"clone_placements": [
        {"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD", "cell": "pi_filter",
         "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.placer_name_edit.setText("CH1_PIF_AVDD")
    dock._do_save()  # renames: removes CH0, writes CH1
    dock._do_save()  # no change: identity matches, no delete, replaces in place

    entries = yaml.safe_load(placer_file.read_text(encoding="utf-8"))["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["placer_name"] == "CH1_PIF_AVDD"
