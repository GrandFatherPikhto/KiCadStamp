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
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import gui.docks.placer as placer_mod
import kicadstamp.undo as undo_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import (Cell, Config, RuntimeContext, load_clone_placement)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import CLUSTER_FIELD_NAME, DEFAULT_LOG_DIR
from kicadstamp.exceptions import ValidationError
from tests.gui.conftest import _pump


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _make_cell_and_dock(main_window, tmp_path):
    # The cells live in an INCLUDED file (cells.yaml), reachable from the
    # project root (root.yaml) via include: — after the file pickers were
    # removed (2026-08-21) PlacerDock reads cells from the WHOLE include
    # graph, so the cells file must be part of the root's graph for the
    # cell combo/params/roles to see pi_filter. Every test that checks
    # cfg.cells monkeypatches load_config with its own fake Config below.
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                             "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [{"offset_along_mm": 1, "offset_across_mm": 1, "net": "{PWR_OUT}",
                       "drill_mm": 0.3, "diameter_mm": 0.6}],
            "tracks": [],
            "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.sexp"
    _write(placer_file, {"clone_placements": [], "include": ["cells.sexp"]})

    dock = PlacerDock(main_window)
    dock.set_root_path(placer_file)
    dock.set_selected_cell("pi_filter")  # Cell picking now lives in ConfigTreeDock, see test_config_tree.py
    return dock, cells_file, placer_file


def test_set_cells_file_populates_the_cell_combo(main_window, tmp_path):
    dock, cells_file, _ = _make_cell_and_dock(main_window, tmp_path)
    items = [dock.cell_combo.itemText(i) for i in range(dock.cell_combo.count())]
    assert items == ["pi_filter"]


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


def test_new_placement_clears_the_cell_combo_selection(main_window, tmp_path):
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.new_placement(placer_file)
    assert dock._selected_cell is None
    assert dock.cell_combo.currentIndex() == -1


def test_comment_round_trips_through_placer_form(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("10.5")
    dock.y_edit.setText("-3.2")
    dock.placer_comment_edit.setText("a clone note")

    entry = dock._build_entry_dict()
    assert entry["comment"] == "a clone note"

    dock.load_placement(entry)
    assert dock.placer_comment_edit.text() == "a clone note"

    # new_placement (the Cell-mode clear path) wipes it.
    dock.new_placement(dock._placer_path)
    assert dock.placer_comment_edit.text() == ""


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
    dock._root_path = tmp_path / "root.sexp"
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
        "cluster": "Channel_2_PI_Filter", "cell": "pi_filter",
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


def test_polar_visibility_shows_only_active_coordinate_pair(main_window, tmp_path):
    """2026-09-05 (Denis): the Cartesian/Polar switch shows ONLY the active
    coordinate pair — X/Y (xy mode, Cartesian), Shift X/Y (anchor/point,
    Cartesian) or Radius/Angle (Polar). The inactive pair is hidden, not just
    disabled (previously the greyed-out pair stayed visible)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    ow = dock.origin_widget

    def vis(w):
        return w is not None and w.isVisibleTo(ow)

    # xy mode + Cartesian (default): X/Y shown, radius/angle + shift hidden.
    assert ow.origin_mode_combo.currentIndex() == 0  # xy
    assert ow._polar_combo.currentIndex() == 0  # Cartesian
    assert vis(ow._xy_row)
    assert not vis(ow._radius_angle_box)
    assert not vis(ow._shift_row)

    # xy mode + Polar: radius/angle shown, X/Y hidden.
    ow._polar_combo.setCurrentIndex(1)
    assert not vis(ow._xy_row)
    assert vis(ow._radius_angle_box)

    # anchor mode + Cartesian: Shift X/Y shown, radius/angle hidden.
    ow._polar_combo.setCurrentIndex(0)
    ow.origin_mode_combo.setCurrentIndex(1)  # anchor
    assert not vis(ow._xy_row)
    assert not vis(ow._radius_angle_box)
    assert vis(ow._shift_row)

    # anchor mode + Polar: radius/angle shown, Shift hidden.
    ow._polar_combo.setCurrentIndex(1)
    assert not vis(ow._shift_row)
    assert vis(ow._radius_angle_box)


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
        "cluster": "X", "cell": "pi_filter", "anchor_role": "FPGA",
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
        "cluster": "X", "cell": "pi_filter", "anchor_point": "origin_point",
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
    saved = _load(placer_file)
    assert len(saved["clone_placements"]) == 1

    overwritten2 = dock._upsert_clone_placement(placer_file, entry)
    assert overwritten2 is True
    saved2 = _load(placer_file)
    assert len(saved2["clone_placements"]) == 1  # no duplicate on the same name

    other = dict(entry, cluster="Channel_3_PI_Filter")
    dock._upsert_clone_placement(placer_file, other)
    saved3 = _load(placer_file)
    assert sorted(e["cluster"] for e in saved3["clone_placements"]) == [
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


def test_tag_cluster_only_tags_own_level_refs_not_nested(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-26 (handoff tag_cluster_overtag): _tag_cluster used to
    tag EVERY ref the placement dragged along — including nested CellPlacement
    components — with the top placement's Cluster, wiping their own Cluster
    fields (all 25 components of composite dac_buf ended up Cluster='DAC_BUF').
    The owner_ref filter must restrict tagging to the placement's OWN level."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    top = load_clone_placement({
        "cluster": "DAC_BUF", "cell": "dac_buf", "xy": [0, 0],
        "nets": {"R_OWN": "NET_OWN"},
    })
    cfg = Config(cells={}, clone_placements=[top])
    ctx = RuntimeContext()

    class _FakeItem:
        def __init__(self, obj):
            self.kind = "clone"
            self.obj = obj

    class _FakeMove:
        def __init__(self, ref, owner_ref):
            self.ref = ref
            self.owner_ref = owner_ref

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
        def __init__(self):
            self.adapter = _FakeAdapter()
            self.items = [_FakeItem(top)]

    class _FakePlanner:
        def __init__(self, adapter, cfg, sheet_names=None):
            pass

        def begin_planning(self):
            pass

        def plan_item(self, item):
            # my_item (DAC_BUF): one OWN component + two nested sub-cell ones
            return [
                _FakeMove("U_OWN", "DAC_BUF"),
                _FakeMove("C147", "ch1_pif_dvdd"),
                _FakeMove("C148", "ch1_pif_avdd"),
            ]

    pipeline = _FakePipeline()
    monkeypatch.setattr(placer_mod, "PlacementPlanner", _FakePlanner)

    tagged = dock._tag_cluster(pipeline, cfg, ctx, "DAC_BUF")

    assert tagged == 1
    assert pipeline.adapter.field_writes == [("U_OWN", CLUSTER_FIELD_NAME, "DAC_BUF")]


def test_tag_cluster_pure_composite_tags_nothing(main_window, tmp_path, monkeypatch):
    """A composite cell with NO own direct components (only nested
    clone_placements) has nothing on the board that belongs to the top
    placement's own level — _tag_cluster must tag nothing at all (the empty
    refs list is already handled by the existing `if updates:` branch)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    top = load_clone_placement({
        "cluster": "DAC_BUF", "cell": "dac_buf", "xy": [0, 0],
        "nets": {"R_OWN": "NET_OWN"},
    })
    cfg = Config(cells={}, clone_placements=[top])
    ctx = RuntimeContext()

    class _FakeItem:
        def __init__(self, obj):
            self.kind = "clone"
            self.obj = obj

    class _FakeMove:
        def __init__(self, ref, owner_ref):
            self.ref = ref
            self.owner_ref = owner_ref

    class _FakeAdapter:
        def __init__(self):
            self.field_writes = None

        def get_footprint(self, ref):
            return None  # not reachable — refs is empty

        def set_field_values_bulk(self, updates, description):
            self.field_writes = [(fp.ref, field, value) for fp, field, value in updates]

    class _FakePipeline:
        def __init__(self):
            self.adapter = _FakeAdapter()
            self.items = [_FakeItem(top)]

    class _FakePlanner:
        def __init__(self, adapter, cfg, sheet_names=None):
            pass

        def begin_planning(self):
            pass

        def plan_item(self, item):
            # ONLY nested sub-cell components — none at the top placement's own level
            return [
                _FakeMove("C147", "ch1_pif_dvdd"),
                _FakeMove("C148", "ch1_pif_avdd"),
            ]

    pipeline = _FakePipeline()
    monkeypatch.setattr(placer_mod, "PlacementPlanner", _FakePlanner)

    tagged = dock._tag_cluster(pipeline, cfg, ctx, "DAC_BUF")

    assert tagged == 0
    assert pipeline.adapter.field_writes is None  # set_field_values_bulk never called


# ── Cell source (2026-08-12, Group 0: role:/cluster: migrated to coordinate_placements) ──

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


def test_load_placement_round_trips_polar_xy(main_window, tmp_path):
    """A saved polar clone_placement (radius_mm/angle_deg) must reload into
    the XY row in Polar mode and round-trip back through _build_entry_dict
    without loss."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {"cluster": "polar", "cell": "pi_filter",
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
        "cluster": "X", "cell": "pi_filter", "xy": [2.0, 0.0],
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
    entry = {"cluster": "X", "cell": "pi_filter", "xy": [1.0, 2.0], "anchor_point": "origin_point"}

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
    assert captured["widgets"] == (
        dock.redraw_button, dock.redraw_dependents_button,
        dock.redraw_and_save_button, dock.select_button,
        dock.undo_button)
    # Bound methods: each access creates a fresh object, so compare with ==
    # (equality checks __self__ + __func__) rather than `is`.
    assert captured["fn"] == dock._run_redraw
    assert captured["on_success"] == dock._finish_redraw
    assert captured["on_error"] == dock._on_redraw_failed

    payload = captured["args"][0]
    assert payload["name"] == "Channel_2_PI_Filter"
    assert payload["placer_path"] == placer_file
    # The dock copies the config before mutating it (the graph cache is now
    # shared), so the payload carries the MUTATED copy, not the injected one.
    assert payload["cfg"] is not fake_cfg
    assert [c.cluster for c in payload["cfg"].clone_placements] == ["Channel_2_PI_Filter"]
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


def test_build_entry_dict_omits_empty_nets_tables(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_1")
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    entry = dock._build_entry_dict()
    assert "nets" not in entry
    assert "net_overrides" not in entry
    assert "refs" not in entry


def _make_two_role_cell_and_dock(main_window, tmp_path):
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {
        "pi_filter2": {
            "components": [
                {"role": "C_IN_BULK", "offset_along_mm": 0, "offset_across_mm": 0, "angle_deg": 0},
                {"role": "PI_FB", "offset_along_mm": 1, "offset_across_mm": 1, "angle_deg": 0},
            ],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root2.sexp"
    _write(placer_file, {"clone_placements": [], "include": ["cells.sexp"]})
    dock = PlacerDock(main_window)
    dock.set_root_path(placer_file)
    dock.set_selected_cell("pi_filter2")
    return dock, cells_file, placer_file


def test_load_placement_clears_stale_anchor_cluster(main_window, tmp_path):
    """Plan p.3: opening record B after record A must not leave A's Anchor
    cluster in the field — a stale value would feed the auto-fill trigger
    with the WRONG cluster before B's own nets load. The race is closed at
    the source: Origin is cleared at the start of load_placement()."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    dock.load_placement({"cluster": "A", "cell": "pi_filter", "xy": [1.0, 1.0],
                         "anchor_role": "SOME_ROLE", "anchor_cluster": "Channel_X"})
    assert dock.anchor_cluster_edit.currentText() == "Channel_X"

    # Record B: same cell, plain absolute xy (no anchor) — must NOT inherit X.
    dock.load_placement({"cluster": "B", "cell": "pi_filter", "xy": [2.0, 2.0]})
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
    had components.sexp open, schematic_dir: lives only on the project ROOT
    (3ch-awg-tia.sexp) which INCLUDES components.sexp — resolve_includes()
    merges downward only, so loading the leaf directly resolves an EMPTY
    sheet_names even though the root resolves real names, making Sheet
    narrowing a silent no-op at Redraw. _load_target_config() must fall back
    to the root's sheet_names when the leaf's own came up empty."""
    _write_schematic_dir(tmp_path, "sch",
                         {"11111111-1111-1111-1111-111111111111": "Channel_0"})
    root = tmp_path / "3ch-awg-tia.sexp"
    _write(root, {"schematic_dir": "sch", "include": ["components.sexp"]})
    leaf = tmp_path / "components.sexp"
    _write(leaf, {"clone_placements": []})

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
    root = tmp_path / "root.sexp"
    _write(root, {"schematic_dir": "sch_root"})
    leaf = tmp_path / "leaf.sexp"
    _write(leaf, {"schematic_dir": "sch_leaf"})

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
    leaf = tmp_path / "leaf.sexp"
    _write(leaf, {"clone_placements": []})

    dock = PlacerDock(main_window)
    dock._placer_path = leaf  # _root_path stays None

    loaded = dock._load_target_config()
    assert loaded is not None
    _, ctx = loaded
    assert ctx.sheet_names == {}


def test_load_target_config_leaf_load_error_shows_message_not_crash(main_window, tmp_path, monkeypatch, caplog):
    """2026-08-21 (handoff_2026_08_21_placer_gettext_shadowing_crash): when
    the Placer file itself fails to load — e.g. a legit ValidationError like
    "mirror without layer change" — _load_target_config() must show that
    error in the Log dock and return None, NOT crash the whole GUI process
    with UnboundLocalError. The old code used `_, root_ctx =
    load_config(...)` for the root fallback, which made `_` (this module's
    gettext import) a LOCAL for the whole method; the except block's
    _("Failed to load Placer file: {error}") then blew up with "cannot
    access local variable '_'" before the real error could be shown."""
    leaf = tmp_path / "leaf.sexp"
    _write(leaf, {"clone_placements": []})
    root = tmp_path / "root.sexp"
    _write(root, {"clone_placements": []})

    def _failing_load(path):
        raise ValidationError(
            "mirror without layer change in clone_placement 'PIF_3V3_VDDA'")

    dock = PlacerDock(main_window)
    dock._root_path = root
    dock._placer_path = leaf
    monkeypatch.setattr(placer_mod, "load_config", _failing_load)

    loaded = dock._load_target_config()
    assert loaded is None
    assert any("Failed to load Placer file" in r.message
               and "PIF_3V3_VDDA" in r.message for r in caplog.records)


def test_load_target_config_root_fallback_failure_keeps_leaf_sheet_names(main_window, tmp_path, monkeypatch, caplog):
    """2026-08-21 (DoD from handoff_2026_08_21_placer_gettext_shadowing_crash):
    the root-config fallback is best-effort — when the ROOT fails to load
    (ValidationError) but the leaf loaded fine with an empty sheet_names,
    the method must keep the leaf's (empty) sheet_names and return normally:
    no crash, no error message (the fallback must not fail Redraw). Same
    `_`-shadowing region as the leaf-load-error test above."""
    leaf = tmp_path / "leaf.sexp"
    _write(leaf, {"clone_placements": []})
    root = tmp_path / "root.sexp"
    _write(root, {"clone_placements": []})
    real_load_config = placer_mod.load_config

    def _root_only_failing_load(path):
        if path == str(root):
            raise ValidationError("root is broken")
        return real_load_config(path)

    dock = PlacerDock(main_window)
    dock._root_path = root
    dock._placer_path = leaf
    monkeypatch.setattr(placer_mod, "load_config", _root_only_failing_load)

    loaded = dock._load_target_config()
    assert loaded is not None
    _, ctx = loaded
    assert ctx.sheet_names == {}
    assert not any("Failed to load Placer file" in r.message for r in caplog.records)


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
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.set_cluster_name("ДругоеИмя")
    assert dock.cluster_edit.currentText() == "PIF_AVDD"


def test_new_placement_resets_dirty_flag_so_autofill_works_again(main_window, tmp_path):
    """2026-08-15 fix: the dirty flag is reset ONLY when the form goes back to
    blank (new_placement) — so a fresh placement gets the auto-fill again."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
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
    dock.load_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
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
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.placer_name_edit.setText("PIF_AVDD")
    entry = dock._build_entry_dict()
    assert entry["cluster"] == "PIF_AVDD"
    assert "name" not in entry


def test_build_entry_dict_includes_placer_name_when_different(main_window, tmp_path):
    """Placer name that differs from Cluster is written — that is the whole
    point of the split."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("PIF_AVDD")
    dock.placer_name_edit.setText("CH0_PIF_AVDD")
    entry = dock._build_entry_dict()
    assert entry["cluster"] == "PIF_AVDD"
    assert entry["name"] == "CH0_PIF_AVDD"


def test_new_placement_resets_placer_name_dirty_flag(main_window, tmp_path):
    """A fresh form wants auto-fill again: load (dirty) -> new_placement ->
    Cluster commit fills Placer name."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.load_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
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
    _write(placer_file, {"clone_placements": [
        {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "pi_filter",
         "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.placer_name_edit.setText("CH1_PIF_AVDD")
    dock._do_save()

    entries = _load(placer_file)["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["name"] == "CH1_PIF_AVDD"
    assert entries[0]["cluster"] == "PIF_AVDD"  # Cluster tag untouched


def test_save_after_renaming_cluster_without_placer_name_removes_old_entry(main_window, tmp_path):
    """Same fix applies to plain Cluster renames (no placer_name): identity is
    the raw name, so changing Cluster in the form and saving must replace, not
    duplicate."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write(placer_file, {"clone_placements": [
        {"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.cluster_edit.setCurrentText("CH0_PIF_AVDD")
    dock._do_save()

    entries = _load(placer_file)["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["cluster"] == "CH0_PIF_AVDD"


def test_save_without_identity_change_still_replaces_in_place(main_window, tmp_path):
    """Regression guard: loading without changing the identity (or changing
    something else entirely) must keep the old replace-in-place upsert — no
    spurious delete cycles."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write(placer_file, {"clone_placements": [
        {"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"cluster": "PIF_AVDD", "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock._do_save()
    dock._do_save()

    assert len(_load(placer_file)["clone_placements"]) == 1


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
    entries = _load(placer_file)["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["cluster"] == "PIF_AVDD"


def test_save_twice_in_a_row_after_rename_does_not_error(main_window, tmp_path):
    """Renaming then saving AGAIN without changes must not try to delete the
    already-removed old entry (identity now matches, delete path not entered)."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    _write(placer_file, {"clone_placements": [
        {"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "pi_filter",
         "xy": [1.0, 1.0]},
    ]})
    dock.load_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
                         "cell": "pi_filter", "xy": [1.0, 1.0]})
    dock.placer_name_edit.setText("CH1_PIF_AVDD")
    dock._do_save()  # renames: removes CH0, writes CH1
    dock._do_save()  # no change: identity matches, no delete, replaces in place

    entries = _load(placer_file)["clone_placements"]
    assert len(entries) == 1
    assert entries[0]["name"] == "CH1_PIF_AVDD"


# ── Redraw & Save (2026-08-25) ──────────────────────────────────────────

def test_on_redraw_and_save_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """The combined button reuses the plain Redraw collect/run path and only
    differs in the completion callback — start_long_op must get _run_redraw +
    _finish_redraw_and_save (NOT _finish_redraw), plus the full guard-widget
    set including the new button."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")
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

    dock._on_redraw_and_save()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (
        dock.redraw_button, dock.redraw_dependents_button,
        dock.redraw_and_save_button, dock.select_button,
        dock.undo_button)
    assert captured["fn"] == dock._run_redraw
    assert captured["on_success"] == dock._finish_redraw_and_save
    assert captured["on_error"] == dock._on_redraw_and_save_failed

    payload = captured["args"][0]
    assert payload["name"] == "Channel_2_PI_Filter"


def test_redraw_and_save_calls_save_after_successful_redraw(main_window, tmp_path, monkeypatch):
    """Order, not race: Save runs only after _run_redraw produced its
    (successful) result — via _do_redraw_and_save's synchronous composition."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    calls = []
    monkeypatch.setattr(dock, "_collect_redraw_inputs", lambda: {"dummy": 1})
    monkeypatch.setattr(dock, "_run_redraw",
                        lambda payload: calls.append("run") or {"name": "X", "tagged": 1})
    monkeypatch.setattr(dock, "_do_save", lambda: calls.append("save"))

    dock._do_redraw_and_save()

    assert calls == ["run", "save"]


def test_redraw_and_save_skips_save_when_redraw_fails(main_window, tmp_path, monkeypatch, caplog):
    """A failed Redraw must NOT save, and the log must say only Redraw was
    reached."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    saved = []
    monkeypatch.setattr(dock, "_collect_redraw_inputs", lambda: {"dummy": 1})
    monkeypatch.setattr(dock, "_run_redraw", lambda payload: {"error": "boom"})
    monkeypatch.setattr(dock, "_do_save", lambda: saved.append(1))

    dock._do_redraw_and_save()

    assert saved == []
    assert any("Save was not run" in r.message for r in caplog.records)


def test_redraw_and_save_worker_saves_only_after_redraw_finished(main_window, tmp_path, monkeypatch, qapp):
    """Real worker thread: the async button path saves exactly once, AFTER the
    worker result arrived back on the UI thread — no naive _on_redraw();
    _on_save() race."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    calls = []
    monkeypatch.setattr(dock, "_collect_redraw_inputs", lambda: {"dummy": 1})
    monkeypatch.setattr(dock, "_run_redraw",
                        lambda payload: calls.append("run") or {"name": "X", "tagged": 1})
    monkeypatch.setattr(dock, "_do_save", lambda: calls.append("save"))

    dock._on_redraw_and_save()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert calls == ["run", "save"]


# ── Undo (2026-08-25) ───────────────────────────────────────────────────

def _write_operation_file(log_dir: Path, name: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    path.write_text("{}", encoding="utf-8")
    return path


def test_undo_no_files_shows_message_without_confirmation(main_window, tmp_path, monkeypatch, caplog):
    """No operation files (missing dir or empty) -> an error message and NO
    confirmation dialog, no worker dispatch."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    log_dir = tmp_path / "logs"  # doesn't exist
    monkeypatch.setattr(dock, "_resolve_operation_log_dir", lambda: log_dir)
    asked = []
    monkeypatch.setattr(
        placer_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a)
                     or placer_mod.QMessageBox.StandardButton.Yes))
    dispatched = []
    monkeypatch.setattr(dock, "_start_undo_op", lambda f: dispatched.append(f))

    dock._on_undo()

    assert asked == []
    assert dispatched == []
    assert any("Nothing to undo" in r.message for r in caplog.records)


def test_undo_confirm_yes_dispatches_worker_with_newest_file(main_window, tmp_path, monkeypatch):
    """Yes -> the newest operation_*.json is handed to the worker (same pick
    as cmd_undo)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    log_dir = tmp_path / "logs"
    _write_operation_file(log_dir, "operation_20260825_100000.json")
    time.sleep(0.02)  # ensure a strictly later st_ctime for the newer file
    newer = _write_operation_file(log_dir, "operation_20260825_110000.json")
    monkeypatch.setattr(dock, "_resolve_operation_log_dir", lambda: log_dir)
    monkeypatch.setattr(
        placer_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: placer_mod.QMessageBox.StandardButton.Yes))
    dispatched = []
    monkeypatch.setattr(dock, "_start_undo_op", lambda f: dispatched.append(f))

    dock._on_undo()

    assert dispatched == [newer]


def test_undo_confirm_cancel_does_nothing(main_window, tmp_path, monkeypatch):
    """Cancel -> no worker dispatch at all."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    log_dir = tmp_path / "logs"
    _write_operation_file(log_dir, "operation_20260825_100000.json")
    monkeypatch.setattr(dock, "_resolve_operation_log_dir", lambda: log_dir)
    monkeypatch.setattr(
        placer_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: placer_mod.QMessageBox.StandardButton.Cancel))
    dispatched = []
    monkeypatch.setattr(dock, "_start_undo_op", lambda f: dispatched.append(f))

    dock._on_undo()

    assert dispatched == []


def test_undo_dispatches_to_worker_with_guard_widgets(main_window, tmp_path, monkeypatch):
    """Undo must run off the UI thread through start_long_op (own kipy socket
    inside undo_last_operation, gated by long_op_active) with the same guard
    buttons as Redraw/Save."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
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
    last_file = tmp_path / "operation.json"
    dock._start_undo_op(last_file)

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (
        dock.redraw_button, dock.redraw_dependents_button,
        dock.redraw_and_save_button, dock.select_button,
        dock.undo_button)
    assert captured["fn"] == dock._run_undo
    assert captured["on_success"] == dock._finish_undo
    assert captured["on_error"] == dock._on_undo_failed
    assert captured["args"][0] == {"json_path": str(last_file)}


def test_run_undo_calls_undo_last_operation_and_reports_name(main_window, tmp_path, monkeypatch, caplog):
    """Worker fn: undo_last_operation is called with the payload's path and the
    success message carries the file name."""
    undone = []
    monkeypatch.setattr(undo_mod, "undo_last_operation",
                        lambda json_path: undone.append(json_path) or True)
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    last_file = tmp_path / "operation_20260825_110000.json"

    result = dock._run_undo({"json_path": str(last_file)})

    assert undone == [last_file]
    assert result == {"name": "operation_20260825_110000.json"}
    dock._finish_undo(result)
    assert any("Undone operation operation_20260825_110000.json" in r.message
               for r in caplog.records)


def test_run_undo_error_path_reports_failure(main_window, tmp_path, monkeypatch, caplog):
    """If undo_last_operation raises, the result carries an explicit error and
    _finish_undo shows it (never a success impression)."""

    def _boom(json_path):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(undo_mod, "undo_last_operation", _boom)
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    last_file = tmp_path / "operation_20260825_110000.json"

    result = dock._run_undo({"json_path": str(last_file)})

    assert result["error"] == "kaboom"
    assert result["name"] == "operation_20260825_110000.json"
    dock._finish_undo(result)
    assert any("Undo failed for operation_20260825_110000.json" in r.message
               for r in caplog.records)


def test_resolve_operation_log_dir_uses_ctx_value(main_window, tmp_path, monkeypatch):
    """ctx.operation_log_dir (resolved by load_config) wins."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    ctx = RuntimeContext(operation_log_dir=str(tmp_path / "oplogs"))
    monkeypatch.setattr(dock, "_load_target_config", lambda silent=False: (Config(), ctx))

    assert dock._resolve_operation_log_dir() == tmp_path / "oplogs"


def test_resolve_operation_log_dir_falls_back_to_root(main_window, tmp_path, monkeypatch):
    """A leaf Placer file without operation_log_dir falls back to the root
    config's value (same downward-only include: merge as sheet_names)."""
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    root = tmp_path / "root.sexp"
    _write(root, {"operation_log_dir": "oplogs"})
    dock._root_path = root
    dock._placer_path = placer_file
    leaf_ctx = RuntimeContext()  # leaf resolves none
    root_ctx = RuntimeContext(operation_log_dir=str(tmp_path / "oplogs"))
    monkeypatch.setattr(placer_mod, "load_config",
                        lambda path: (Config(), root_ctx if str(path) == str(root) else leaf_ctx))

    assert dock._resolve_operation_log_dir() == tmp_path / "oplogs"


def test_resolve_operation_log_dir_defaults_to_default_log_dir(main_window, tmp_path, monkeypatch):
    """With no operation_log_dir anywhere, fall back to DEFAULT_LOG_DIR (same
    as cmd_undo)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    monkeypatch.setattr(dock, "_load_target_config",
                        lambda silent=False: (Config(), RuntimeContext()))

    assert dock._resolve_operation_log_dir() == Path(DEFAULT_LOG_DIR)


# ── 2026-08-31 (plan placer_source_tab_gaps P.1): Cluster auto-fill from
# the CURRENT board selection ────────────────────────────────────────────
# Денис selected a whole Cluster's components on the board and expected its
# name to fill itself into the Source tab's Cluster field (like ExtractDock
# does for Cell names). DockHub.set_board_selection now also feeds PlacerDock;
# _autofill_cluster_from_selection fills cluster_edit only when Cell mode,
# the selection is one non-empty Cluster and the field is blank + not
# user-owned (never overwrite), then triggers the Nets/Params pipeline.


def _p1_selected(cluster):
    return SimpleNamespace(cluster=cluster)


def test_set_board_selection_autofills_cluster_from_single_cluster(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    assert dock.cluster_edit.currentText() == ""

    dock.set_board_selection([], [_p1_selected("FPGA_FLASH"),
                                  _p1_selected("FPGA_FLASH")])

    assert dock.cluster_edit.currentText() == "FPGA_FLASH"
    assert dock._cluster_identity_dirty is True  # field is now user-owned


def test_set_board_selection_mixed_clusters_leaves_blank(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    dock.set_board_selection([], [_p1_selected("FPGA_FLASH"),
                                  _p1_selected("DAC_BUF")])

    assert dock.cluster_edit.currentText() == ""
    assert dock._cluster_identity_dirty is False


def test_set_board_selection_no_cluster_selection_leaves_blank(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)

    dock.set_board_selection([], [_p1_selected(None), _p1_selected(None)])

    assert dock.cluster_edit.currentText() == ""
    assert dock._cluster_identity_dirty is False


def test_set_board_selection_never_overwrites_typed_cluster(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("MY_TYPED_CLUSTER")
    dock._cluster_identity_dirty = True

    dock.set_board_selection([], [_p1_selected("FPGA_FLASH")])

    assert dock.cluster_edit.currentText() == "MY_TYPED_CLUSTER"


def test_set_board_selection_cell_mode_only(main_window, tmp_path):
    """Single-component and Entity modes must NOT touch the (hidden) Cell-mode
    cluster_edit."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cell_mode_combo.setCurrentIndex(1)  # Single component
    dock.set_board_selection([], [_p1_selected("FPGA_FLASH")])
    assert dock.cluster_edit.currentText() == ""

    dock.cell_mode_combo.setCurrentIndex(2)  # Entity
    dock.set_board_selection([], [_p1_selected("FPGA_FLASH")])
    assert dock.cluster_edit.currentText() == ""


def _make_cell_and_dock_anchor(main_window, tmp_path):
    """A composite cell (component with its own via, cell-level via, track,
    nested clone_placement) in an INCLUDED cells file — everything the rebase
    must shift, with clean offsets (FPGA at (2.5, 1.0))."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {
        "composite": {
            "components": [
                {"role": "FPGA", "offset_along_mm": 2.5, "offset_across_mm": 1.0,
                 "angle_deg": 0.0,
                 "vias": [{"offset_along_mm": 2.5, "offset_across_mm": 2.2,
                           "net": "GND"}]},
                {"role": "CAP", "offset_along_mm": 3.5, "offset_across_mm": -1.0,
                 "angle_deg": 90.0},
            ],
            "vias": [{"offset_along_mm": 5.0, "offset_across_mm": 4.0, "net": "VCC"}],
            "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                        "end_along_mm": 3.0, "end_across_mm": 2.0,
                        "width_mm": 0.25, "net": "+3V3"}],
            "clone_placements": [{"name": "leaf", "cell": "leaf_cell",
                                  "xy": [1.0, 1.0], "rotation_deg": 0.0}],
            "layer": "F.Cu",
        }
    }})
    placer_file = tmp_path / "root.sexp"
    _write(placer_file, {"clone_placements": [], "include": ["cells.sexp"]})
    dock = PlacerDock(main_window)
    dock.set_root_path(placer_file)
    dock.set_selected_cell("composite")
    return dock, cells_file, placer_file


def _close(a, b, tol=1e-6):
    return abs(a - b) < tol


def _off(record, key):
    """Value of a local-offset key, defaulting to 0.0 when absent — the sexp
    writer omits fields equal to their loader default, so a rebased-to-(0,0)
    offset is simply not stored in the file."""
    return float(record.get(key, 0.0))


def test_cell_anchor_role_combo_is_sourced_from_cell_components(main_window, tmp_path):
    """The Role picker lists THIS cell's own components (like CellDock's
    anchor_role_combo), never board-wide roles."""
    dock, _, _ = _make_cell_and_dock_anchor(main_window, tmp_path)
    items = [dock.cell_anchor_role_combo.itemText(i)
             for i in range(dock.cell_anchor_role_combo.count())]
    assert items == ["CAP", "FPGA"]


def test_set_cell_anchor_role_mode_rebases_offline(main_window, tmp_path):
    """Role mode needs NO live board (the board is not connected): rebase by
    the FPGA component's own offset — FPGA lands on (0,0), every local offset
    (incl. its own via, cell via, track endpoints, nested xy) shifts by the
    same delta, and anchor_role is written to the cell's OWN file."""
    dock, cells_file, _ = _make_cell_and_dock_anchor(main_window, tmp_path)
    assert main_window.connection.board is None  # offline regression guard
    dock.cell_anchor_role_combo.setCurrentText("FPGA")
    dock.cell_anchor_pad_edit.setText("")
    dock._on_set_cell_anchor()

    cell = _load(cells_file)["cells"]["composite"]
    by_role = {c["role"]: c for c in cell["components"]}
    assert _off(by_role["FPGA"], "offset_along_mm") == 0.0
    assert _off(by_role["FPGA"], "offset_across_mm") == 0.0
    assert _close(by_role["FPGA"]["vias"][0].get("offset_along_mm", 0.0), 0.0)
    assert _close(by_role["FPGA"]["vias"][0].get("offset_across_mm"), 1.2)
    assert _close(by_role["CAP"].get("offset_along_mm"), 1.0)
    assert _close(by_role["CAP"].get("offset_across_mm"), -2.0)
    assert _close(cell["vias"][0].get("offset_along_mm"), 2.5)
    assert _close(cell["vias"][0].get("offset_across_mm"), 3.0)
    assert _close(cell["tracks"][0].get("start_along_mm"), -2.5)
    assert _close(cell["tracks"][0].get("end_along_mm"), 0.5)
    assert _close(cell["clone_placements"][0]["xy"][0], -1.5)
    assert cell["clone_placements"][0]["xy"][1] == 0.0
    assert cell["anchor_role"] == "FPGA"
    assert "anchor_pad" not in cell
    assert "anchor_xy" not in cell


def test_set_cell_anchor_role_pad_requires_connection(main_window, tmp_path, monkeypatch):
    """Role+Pad mode needs the live pad geometry — no board must warn
    ("Not connected") and leave the cell untouched, never a silent partial
    write."""
    dock, cells_file, _ = _make_cell_and_dock_anchor(main_window, tmp_path)
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    before = _load(cells_file)
    dock.cell_anchor_role_combo.setCurrentText("FPGA")
    dock.cell_anchor_pad_edit.setText("A1")
    dock._on_set_cell_anchor()

    assert warnings  # the "no live board connection" warning fired
    assert _load(cells_file) == before  # nothing written


def test_set_cell_anchor_role_pad_stages_with_adapter(main_window, tmp_path, monkeypatch):
    """Role+Pad mode with a (fake) live adapter delegates the geometry to
    read_cell_anchor_offset_live and rebases + records anchor_pad into the
    cell's own file."""
    dock, cells_file, _ = _make_cell_and_dock_anchor(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=MagicMock())
    dock.cluster_edit.setCurrentText("FPGA_FLASH")
    # The pad's cell-local offset (2.5, 1.0) — the pure reader is covered by
    # tests/test_live_position.py; here we only exercise the dock's wiring.
    monkeypatch.setattr(placer_mod, "read_cell_anchor_offset_live",
                        lambda *a, **k: (2.5, 1.0))
    dock.cell_anchor_role_combo.setCurrentText("FPGA")
    dock.cell_anchor_pad_edit.setText("A1")
    dock._on_set_cell_anchor()

    cell = _load(cells_file)["cells"]["composite"]
    by_role = {c["role"]: c for c in cell["components"]}
    assert _off(by_role["FPGA"], "offset_along_mm") == 0.0
    assert _off(by_role["FPGA"], "offset_across_mm") == 0.0
    assert _close(by_role["CAP"].get("offset_across_mm"), -2.0)
    assert cell["anchor_role"] == "FPGA"
    assert cell["anchor_pad"] == "A1"
    assert "anchor_xy" not in cell


def test_set_cell_anchor_repeated_rebase_clears_previous_pad(main_window, tmp_path, monkeypatch):
    """A second rebase with a DIFFERENT anchor (role+pad first, then role-only)
    must not leave the previous anchor_pad behind (load_cell fatals on a pad
    without a role)."""
    dock, cells_file, _ = _make_cell_and_dock_anchor(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=MagicMock())
    dock.cluster_edit.setCurrentText("FPGA_FLASH")
    monkeypatch.setattr(placer_mod, "read_cell_anchor_offset_live",
                        lambda *a, **k: (2.5, 1.0))

    # 1st: role+pad rebase onto FPGA pad A1.
    dock.cell_anchor_role_combo.setCurrentText("FPGA")
    dock.cell_anchor_pad_edit.setText("A1")
    dock._on_set_cell_anchor()
    cell = _load(cells_file)["cells"]["composite"]
    assert cell["anchor_role"] == "FPGA"
    assert cell["anchor_pad"] == "A1"

    # 2nd: role-only rebase onto CAP (pad cleared) — old pad must vanish.
    dock.cell_anchor_role_combo.setCurrentText("CAP")
    dock.cell_anchor_pad_edit.setText("")
    dock._on_set_cell_anchor()
    cell2 = _load(cells_file)["cells"]["composite"]
    by_role = {c["role"]: c for c in cell2["components"]}
    assert cell2["anchor_role"] == "CAP"
    assert "anchor_pad" not in cell2
    assert "anchor_xy" not in cell2
    # CAP — the new anchor role — sits exactly on the new (0,0).
    assert _off(by_role["CAP"], "offset_along_mm") == 0.0
    assert _off(by_role["CAP"], "offset_across_mm") == 0.0


def test_unrelated_edit_preserves_stored_override_fields(main_window, tmp_path):
    """2026-09-05: the Nets/Net overrides/Refs tabs are gone, but a record
    that already stores nets:/params:/net_overrides:/refs: must survive an
    unrelated Placer edit unchanged — carried forward on save
    (_loaded_override_fields / _carry_override_fields)."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "cluster": "X", "cell": "pi_filter", "xy": [1.0, 2.0],
        "params": {"CH": "+3V3"},
        "nets": {"C_IN_BULK": "+3V3", "C_OUT_BYPASS": "+3V3_OSCILL"},
        "net_overrides": {"/FPGA/CLK": "/FPGA/CLK_0"},
        "refs": {"R_CLK": "C12"},
    }
    dock.load_placement(entry)
    # Unrelated edit — Cluster (identity) is the main thing the form still
    # edits for a clone; the override fields must come back untouched.
    dock.cluster_edit.setCurrentText("X2")
    rebuilt = dock._build_entry_dict()
    assert rebuilt["cluster"] == "X2"
    assert rebuilt["params"] == entry["params"]
    assert rebuilt["nets"] == entry["nets"]
    assert rebuilt["net_overrides"] == entry["net_overrides"]
    assert rebuilt["refs"] == entry["refs"]

    # Loading a record WITHOUT override fields clears the carry-forward —
    # a stale nets: from the previous record must not leak into the next save.
    dock.load_placement({"cluster": "Y", "cell": "pi_filter", "xy": [0.0, 0.0]})
    dock.cluster_edit.setCurrentText("Y2")
    rebuilt2 = dock._build_entry_dict()
    assert "params" not in rebuilt2
    assert "nets" not in rebuilt2
    assert "net_overrides" not in rebuilt2
    assert "refs" not in rebuilt2


def test_cell_anchor_is_its_own_tab_visible_in_cell_mode_only(main_window, tmp_path):
    """2026-09-05 (Denis): the "Cell anchor" (rebase) UI moved from the
    bottom of the Origin page into its OWN tab — available in Cell (clone)
    mode, hidden in Single-component and Entity modes."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    # Cell (clone) mode is the default: the Cell-anchor tab is available.
    assert dock._tabs.isTabVisible(dock._cell_anchor_tab_index)
    # Single component (coordinate): the tab is hidden.
    dock.cell_mode_combo.setCurrentIndex(1)
    assert not dock._tabs.isTabVisible(dock._cell_anchor_tab_index)
    # Entity: hidden too.
    dock.cell_mode_combo.setCurrentIndex(2)
    assert not dock._tabs.isTabVisible(dock._cell_anchor_tab_index)
    # Back to Cell: visible again, and the box sits on its own page.
    dock.cell_mode_combo.setCurrentIndex(0)
    assert dock._tabs.isTabVisible(dock._cell_anchor_tab_index)
    assert dock._tabs.widget(dock._cell_anchor_tab_index) is not None
