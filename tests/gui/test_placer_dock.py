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


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U1")
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")

    assert dock._build_entry_dict() is None
    assert "mutually exclusive" in dock.message_label.text()


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


def test_point_mode_requires_a_name(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("X")
    dock.origin_mode_combo.setCurrentIndex(2)

    assert dock._build_entry_dict() is None
    assert "name is required" in dock.message_label.text()

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


def test_redraw_requires_cell_reachable_via_placer_config(main_window, tmp_path, monkeypatch):
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
    assert "include" in dock.message_label.text()


def test_redraw_preserves_other_placements_for_registry_safety(main_window, tmp_path, monkeypatch):
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
    assert "Placed" in dock.message_label.text()
    assert "1 component(s) tagged Cluster" in dock.message_label.text()


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
    assert visible(dock._params_container)
    assert dock._tabs.isTabVisible(dock._nets_tab_index)
    assert dock._tabs.isTabVisible(dock._net_overrides_tab_index)
    assert dock._tabs.isTabVisible(dock._refs_tab_index)
    assert dock._tabs.isTabVisible(dock._origin_tab_index)
    assert not dock._tabs.isTabVisible(dock._coordinate_tab_index)


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
        return [SimpleNamespace(net=SimpleNamespace(name=n)) for n in fp._nets]


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


def test_autofill_nets_requires_a_cell(main_window):
    dock = PlacerDock(main_window)
    dock._do_autofill_nets()
    assert "Pick a Cell first" in dock.message_label.text()


def test_autofill_nets_requires_anchor_cluster(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock._do_autofill_nets()
    assert "Anchor cluster" in dock.message_label.text()


def test_autofill_nets_requires_board_connection(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = None

    dock._do_autofill_nets()

    assert "Not connected" in dock.message_label.text()


def test_do_autofill_nets_fills_unambiguous_role(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)  # single role "C_IN"
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN", "Out_Pi_Filter_N2V5", ["+1V2", "GND"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN": "+1V2"}
    assert "Auto-filled all 1 role(s)" in dock.message_label.text()


def test_do_autofill_nets_leaves_ambiguous_role_for_manual_entry(main_window, tmp_path):
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        # PI_FB bridges two nets -> not reducible to one identifying net.
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2"}
    assert "Auto-filled 1/2 role(s)" in dock.message_label.text()
    assert "PI_FB" in dock.message_label.text()


def test_do_autofill_nets_does_not_stomp_a_row_it_could_not_resolve(main_window, tmp_path):
    """A role Auto-fill can't determine keeps whatever was already typed for
    it — same "never overwrite an already-filled value" discipline as every
    other auto-fill in this GUI (ExtractDock's cluster autofill, etc.)."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.nets_table.load_dict({"PI_FB": "+1V2_VCCINT"})  # typed by hand earlier
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2", "PI_FB": "+1V2_VCCINT"}


def test_on_autofill_nets_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
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
    assert payload["roles"] == ["C_IN"]
    assert payload["cluster"] == "Out_Pi_Filter_N2V5"
    assert payload["adapter"] is main_window.connection.board.adapter


# ── Auto-fill safety + auto-trigger (2026-08-13, plan
# placer_autofill_default_and_docs) ─────────────────────────────────────

def test_do_autofill_nets_does_not_overwrite_a_prefilled_role(main_window, tmp_path):
    """Plan p.1: Auto-fill fills ONLY blank roles — a role the user already
    typed a value for is never overwritten, even when the worker suggests a
    different net for it (the auto-trigger re-fires on every Cell+Cluster
    commit, so an overwrite here would silently clobber manual edits on each
    re-fire; the manual button gets the same, strictly safer behaviour)."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    dock.nets_table.load_dict({"C_IN_BULK": "+1V2_VCCINT"})  # typed by hand
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
        _FakeAutofillFootprint("FB6", "PI_FB", "Out_Pi_Filter_N2V5", ["+1V2", "+1V2_VCCINT"]),
    ])

    dock._do_autofill_nets()

    # C_IN_BULK keeps the manual value; PI_FB stays blank (unresolvable) and
    # is reported as left for manual entry.
    assert dock.nets_table.to_dict() == {"C_IN_BULK": "+1V2_VCCINT"}
    assert "PI_FB" in dock.message_label.text()


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

    line_edit = dock.anchor_cluster_edit.lineEdit()
    # Typing (per-keystroke text change) must NOT fire.
    line_edit.setText("Out_Pi")
    line_edit.textChanged.emit("Out_Pi")
    assert calls == []

    # Commit the cluster via editingFinished (Enter/focus-out) -> fires once.
    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    line_edit.editingFinished.emit()
    assert len(calls) == 1

    # Picking the cluster from the dropdown (activated) fires too.
    dock.anchor_cluster_edit.activated.emit(0)
    assert len(calls) == 2

    # Picking a Cell with a ready cluster fires as well.
    dock.set_selected_cell("pi_filter2")
    assert len(calls) == 3


def test_auto_trigger_no_ops_without_cell_or_cluster(main_window, tmp_path, monkeypatch):
    """Plan p.2: the auto-trigger is a silent no-op until BOTH halves of the
    pair are ready — not an error, just nothing to fill yet."""
    dock, _, _ = _make_two_role_cell_and_dock(main_window, tmp_path)
    main_window.connection.board = _FakeAutofillBoard([
        _FakeAutofillFootprint("C22", "C_IN_BULK", "Out_Pi_Filter_N2V5", ["+1V2"]),
    ])
    calls = []
    monkeypatch.setattr(dock, "_start_autofill_nets_op", lambda payload: calls.append(payload))

    dock._maybe_autofill_nets()  # cell set (fixture), but no cluster yet
    assert calls == []

    dock.anchor_cluster_edit.setCurrentText("Out_Pi_Filter_N2V5")
    dock.new_placement(dock._placer_path)  # clears the cell
    dock._maybe_autofill_nets()  # cluster set, but no cell now
    assert calls == []
    assert dock.message_label.text() == ""  # silent, no error text


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
