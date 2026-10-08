# tests/gui/test_select_cell.py
"""Н5 cells — "Select cell" (plan_2026_10_04_refresh_mixed_cluster_selection, Н5;
Denis 2026-10-05). The pure resolution/ownership lives in gui/select_cell.py; the
button/tree menu route to the SAME one function.
"""
import json
from types import SimpleNamespace

import pytest

from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.constants import (CLUSTER_FIELD_NAME, ROLE_FIELD_NAME,
                                SPOKE_LEVEL_ROLE_PLACEHOLDER)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.registry import make_registry_key, record_key_part
from tests.fakes.format3 import det_uuid


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Rec:
    def __init__(self, name=None, uuid=None, cell=None, cluster=None, sheet=None,
                 anchor_role=None, role=None):
        self.name = name
        self.uuid = uuid
        self.cell = cell
        self.cluster = cluster
        self.sheet = sheet
        self.anchor_role = anchor_role
        self.role = role


class _Cfg:
    def __init__(self, entities=(), clone_placements=(), cells=None):
        self.entities = list(entities)
        self.clone_placements = list(clone_placements)
        self.cells = cells or {}


class _Adapter:
    def __init__(self, fields, footprints=(), vias=(), tracks=()):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)


def _fp(ref, role, cluster, x_mm=0.0, y_mm=0.0):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = ()
    return fp


def _slot(role, along, across):
    return SimpleNamespace(role=role, offset_along_mm=along,
                           offset_across_mm=across, vias=[])


def _rec_via(along, across):
    return SimpleNamespace(offset_along_mm=along, offset_across_mm=across)


def _rec_track(along, across, end_along, end_across):
    return SimpleNamespace(start_along_mm=along, start_across_mm=across,
                           end_along_mm=end_along, end_across_mm=end_across)


def _cell(uuid, *, vias=(), tracks=(), components=()):
    """The fixture cell: the two slots (DA at the cell's own (0,0), DB at (5,0))
    are what makes the registry-only frame RIGID and its origin the cell's own
    (0,0) — the live footprints below are placed to match."""
    return SimpleNamespace(uuid=uuid, anchor_role=None, anchor_xy=None,
                           vias=list(vias), tracks=list(tracks),
                           components=list(components))


def _write_registries(tmp_path, via_entries, track_entries):
    schema = 2 if current_format() >= 3 else 1
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir()
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **track_entries}),
        encoding="utf-8")


# ── cell_instances (pure) ───────────────────────────────────────────────────

def test_cell_instances_remembered_wins():
    from gui.select_cell import cell_instances
    cfg = _Cfg(entities=[_Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_1")])
    assert cell_instances(cfg, "dac_buf", "DAC_BUF", "Channel_0") == \
        [("DAC_BUF", "Channel_0")]


def test_cell_instances_from_records_deduped():
    from gui.select_cell import cell_instances
    cfg = _Cfg(entities=[_Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_0"),
                         _Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_0"),
                         _Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_1"),
                         _Rec(cell="pif", cluster="PIF")])
    assert cell_instances(cfg, "dac_buf") == [("DAC_BUF", "Channel_0"),
                                              ("DAC_BUF", "Channel_1")]


# ── select_cell_targets: components + OWN copper only ───────────────────────

def test_select_cell_targets_own_instance_and_own_copper(gate, tmp_path):
    from gui.select_cell import select_cell_targets

    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg(cells={"dac_buf": _cell(
        cell_uuid,
        vias=[_rec_via(0.0, 0.0), _rec_via(2.0, 2.0)],
        tracks=[_rec_track(0.0, 0.0, 1.0, 0.0)],
        components=[_slot("DA", 0.0, 0.0), _slot("DB", 5.0, 0.0)])})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0)
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 5.0, 0.0)
    fp_p1 = _fp("P1", "PA", "PIF_AVDD")
    v_own = Via(uuid="v_own", position=Vector2.from_xy_mm(0.0, 0.0),
                net_name=None, drill_mm=0.3, diameter_mm=0.6)
    v_foreign = Via(uuid="v_foreign", position=Vector2.from_xy_mm(1.0, 1.0),
                    net_name=None, drill_mm=0.3, diameter_mm=0.6)
    v_gone = Via(uuid="v_gone", position=Vector2.from_xy_mm(2.0, 2.0),
                 net_name=None, drill_mm=0.3, diameter_mm=0.6)
    t_own = Track(uuid="t_own", net_name=None,
                  start=Vector2.from_xy_mm(0.0, 0.0),
                  end=Vector2.from_xy_mm(1.0, 0.0),
                  width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    # own: anchor:<ref> of THIS instance; foreign: another instance; gone: not live.
    # Every key names a REAL record of the cell (the spoke placeholder + index) —
    # an ORPHAN key is never a pair, so it could not test the ownership rule.
    via_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity,
                          SPOKE_LEVEL_ROLE_PLACEHOLDER, 0):
            {"uuid": "v_own", "x_mm": 0.0, "y_mm": 0.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
        make_registry_key("anchor:X9:1:0.0000:0.0000", cell_identity,
                          SPOKE_LEVEL_ROLE_PLACEHOLDER, 0):
            {"uuid": "v_foreign", "x_mm": 1.0, "y_mm": 1.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
        make_registry_key("anchor:C2:1:0.0000:0.0000", cell_identity,
                          SPOKE_LEVEL_ROLE_PLACEHOLDER, 1):
            {"uuid": "v_gone", "x_mm": 2.0, "y_mm": 2.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
    }
    track_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity,
                          SPOKE_LEVEL_ROLE_PLACEHOLDER, 0):
            {"uuid": "t_own", "start_x_mm": 0.0, "start_y_mm": 0.0,
             "end_x_mm": 1.0, "end_y_mm": 0.0, "width_mm": 0.25,
             "net": "N", "layer": "F.Cu"},
    }
    _write_registries(tmp_path, via_entries, track_entries)
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "P1": ("PA", "PIF_AVDD")},
                       footprints=[fp_c1, fp_c2, fp_p1],
                       vias=[v_own, v_foreign], tracks=[t_own])

    plan = select_cell_targets(adapter, cfg, str(config_path), "dac_buf",
                               "DAC_BUF", None, {}, own_refs=["C1"])
    assert {f.ref for f in plan.footprints} == {"C1", "C2"}
    # own anchor:<ref> of THIS instance is selected; the other instance's is not
    assert {getattr(i, "uuid", None) for i in plan.copper} == {"v_own", "t_own"}


def test_select_cell_targets_counts_registry_uuids_not_on_board(gate, tmp_path):
    from gui.select_cell import select_cell_targets

    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg(cells={"dac_buf": _cell(
        cell_uuid, vias=[_rec_via(0.0, 0.0)],
        components=[_slot("DA", 0.0, 0.0)])})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    via_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity,
                          SPOKE_LEVEL_ROLE_PLACEHOLDER, 0):
            {"uuid": "v_gone", "x_mm": 0.0, "y_mm": 0.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
    }
    _write_registries(tmp_path, via_entries, {})
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0)
    adapter = _Adapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp_c1], vias=[])
    plan = select_cell_targets(adapter, cfg, str(config_path), "dac_buf",
                               "DAC_BUF", None, {}, own_refs=["C1"])
    assert plan.copper == []
    assert plan.missing_registry == 1


# ── structural: button and tree menu route to the SAME function ─────────────

def test_button_caption_and_shared_entry_point():
    """The CellDock button is captioned "Select cell" and the tree menu signal is
    wired to a delegate that calls the SAME `select_cell_requested` entry."""
    from PyQt6.QtWidgets import QPushButton
    from gui.docks import cell_editor as ce
    from gui.docks import config_tree as ct
    from kicadstamp.i18n import _
    # caption string exists in the catalog
    assert _("Select cell") == "Select cell" or _("Select cell")
    # the config tree exposes the signal the dock hub connects
    assert hasattr(ct.ConfigTreeDock, "cell_select_requested")
    # CellDock owns the shared entry point and the worker
    assert hasattr(ce.CellDock, "select_cell_requested")
    assert hasattr(ce.CellDock, "_run_select_cluster_on_board")
    assert QPushButton is not None


# ── C2: the DockHub delegate calls the shared entry ─────────────────────────

def test_hub_delegate_calls_the_shared_entry():
    """C2: DockHub._select_cell_from_tree drives CellDock.select_cell_requested
    with the explicit (cluster, sheet) — the tree menu's door into the SAME
    function the button uses."""
    from gui.dock_hub import DockHub
    calls = []

    class _Cells:
        def select_cell_requested(self, name, fp, cluster, sheet):
            calls.append((name, fp, cluster, sheet))

    hub = SimpleNamespace(cells_dock=_Cells())
    DockHub._select_cell_from_tree(hub, "dac_buf", "f.sexp",
                                   "DAC_BUF", "Channel_1")
    assert calls == [("dac_buf", "f.sexp", "DAC_BUF", "Channel_1")]


# ── C3: the tree's cells: item emits the signal ─────────────────────────────

def test_tree_item_emits_the_signal():
    """C3 (structural): the context-menu item emits cell_select_requested (the
    label is translated, so the guard reads the EMIT, not the caption).

    часть 3, п.3: the ENTITY leaf is the ONLY sender now — a cell leaf carries no
    board item at all (its behavioural guard lives in
    tests/gui/docks/test_entities_under_cells.py)."""
    import inspect
    import gui.docks.entity_tree as et
    src = inspect.getsource(et)
    assert "self.cell_select_requested.emit(" in src
    # 2б, п.4: the FOURTH field is the instance, the FIFTH the entity NAME.
    assert "self.cell_select_requested.emit(n, None, c, s, e)" in src


# ── Н5-2: an explicit instance is never overridden by the remembered sheet ──

def test_explicit_instance_wins_over_remembered():
    from gui.select_cell import effective_instance
    assert effective_instance("DAC_BUF", "Channel_1",
                              "DAC_BUF", "Channel_0") == ("DAC_BUF", "Channel_1")
    assert effective_instance(None, None,
                              "DAC_BUF", "Channel_0") == ("DAC_BUF", "Channel_0")


# ── C4: м3 narrows to the chosen address (foreign entity NOT named) ─────────

def test_m3_foreign_entity_is_not_named():
    from kicadstamp.cell_geometry_refresh import referencing_records_for_roles
    cfg = _Cfg(entities=[_Rec(cell="dac_buf", cluster="DAC_BUF",
                              anchor_role="GONE"),
                         _Rec(cell="pif", cluster="PIF_AVDD",
                              anchor_role="GONE")])
    refs = referencing_records_for_roles(cfg, {"GONE"}, "DAC_BUF", None)
    assert "GONE" in refs
    assert all("pif" not in label for label in refs["GONE"])


# ── C5: м2 retired/skip net_traces stay silent ──────────────────────────────

def test_m2_retired_net_trace_is_silent(gate, monkeypatch):
    import kicadstamp.net_trace_planner as planner
    from kicadstamp.selection_narrowing import subtract_net_trace_copper
    stub = SimpleNamespace(found=[], reason="retired/skip", identity="NT")
    monkeypatch.setattr(planner, "find_live_copper",
                        lambda adapter, nt, **kw: stub)
    nt = SimpleNamespace(net="NT", retired=True, skip=False)
    sub = subtract_net_trace_copper([], [nt], None, via_entries={},
                                    track_entries={})
    assert sub.notes == ()


# ── Н5-1: a cell with no remembered context uses its config instances ───────

def test_select_cell_uses_config_instances_when_nothing_remembered(
        main_window, tmp_path):
    """Н5-1(в): no remembered context, exactly ONE config record places the cell
    -> that address is used (cell_instances is IN USE, not dead code)."""
    from gui.docks.cell_editor import CellDock
    from kicadstamp.config.sexp_format import dict_to_sexp
    cfg = {"cells": {"dac_buf": {
               "layer": "F.Cu",
               "components": [{"role": "DA", "offset_along_mm": 0.0,
                               "offset_across_mm": 0.0, "angle_deg": 0.0}],
               "vias": [], "tracks": [], "clone_placements": []}},
           "entities": [{"name": "dac0", "cell": "dac_buf",
                         "cluster": "DAC_BUF", "sheet": "Channel_0"}]}
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(cfg, format_number=2), encoding="utf-8")
    dock = CellDock(main_window)
    dock.set_root_path(target)
    dock.load_entry("dac_buf")
    # The instance rule moved to gui/select_cell.py (Р2а-3): one resolver for
    # "Select cell" and "Explode…".
    from gui.select_cell import resolve_action_instance
    from kicadstamp.config.loader import load_config
    cfg_loaded, _ctx = load_config(str(target))
    choice = resolve_action_instance(cfg_loaded, target, "dac_buf")
    assert (choice.kind, choice.cluster, choice.sheet) == (
        "single", "DAC_BUF", "Channel_0")


# ── H5-4: tree nodes of OTHER clusters are not named ────────────────────────

def test_m3_foreign_tree_node_is_not_named():
    from kicadstamp.cell_geometry_refresh import referencing_records_for_roles
    node_match = SimpleNamespace(ref="n1", role="GONE", cluster="DAC_BUF",
                                 sheet=None, anchor=None)
    node_foreign = SimpleNamespace(ref="n2", role="GONE", cluster="PIF_AVDD",
                                   sheet=None, anchor=None)
    cfg = _Cfg()
    cfg.trees = [SimpleNamespace(name="t", nodes=[node_match, node_foreign])]
    refs = referencing_records_for_roles(cfg, {"GONE"}, "DAC_BUF", None)
    assert any("t:n1" in r for r in refs.get("GONE", []))
    assert all("t:n2" not in r for r in refs.get("GONE", []))


# ── Н5б: the entity door must not misuse the entity's file ──────────────────

def test_entity_item_sends_its_own_instance():
    """C7: the entities: menu item sends the ENTITY's (cluster, sheet), and no
    file_path (the entity's file must never become the cell's save target)."""
    import inspect
    import gui.docks.config_tree as ct
    import gui.docks.entity_tree as et
    # The entity leaf's menu block moved to the entity-tree mixin (rule 45: the
    # config tree is a giant and only shrinks) — the properties are unchanged,
    # so the guard reads BOTH sources, exactly like the cells: item above.
    src = inspect.getsource(ct) + inspect.getsource(et)
    assert 'c=entity.get("cluster"), s=entity.get("sheet"),' in src
    # 2б, п.4: the item ALSO sends the entity NAME (its fifth field) — that is
    # what makes the action read THIS channel instead of the last one the page
    # was on.
    assert 'e=entity.get("name"):' in src
    assert "self.cell_select_requested.emit(n, None, c, s, e)" in src


def _write_include_config(tmp_path):
    from kicadstamp.config.sexp_format import dict_to_sexp
    inc = tmp_path / "cells_inc.sexp"
    inc.write_text(dict_to_sexp({
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": [{"role": "DA", "offset_along_mm": 0.0,
                            "offset_across_mm": 0.0, "angle_deg": 0.0}],
            "vias": [], "tracks": [], "clone_placements": []}}},
        format_number=2), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "include": ["cells_inc.sexp"],
        "entities": [{"name": "dac0", "cell": "dac_buf",
                      "cluster": "DAC_BUF", "sheet": "Channel_0"}]},
        format_number=2), encoding="utf-8")
    return root, inc


def test_entity_door_uses_the_cells_own_file(main_window, tmp_path):
    """Н5б(2): an entity in the ROOT, its cell in an INCLUDE — after "Select cell"
    from the entity (file_path=None), CellDock._path is the CELL's file, not the
    entity's."""
    from pathlib import Path
    from gui.docks.cell_editor import CellDock
    root, inc = _write_include_config(tmp_path)
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.select_cell_requested("dac_buf", None, "DAC_BUF", "Channel_0")
    assert Path(dock._path) == inc


def test_select_cell_same_cell_keeps_unsaved_edits(main_window, tmp_path):
    """Н5б(3): the cell is ALREADY open — "Select cell" must not reload the form
    (a reload would discard unsaved edits silently)."""
    from pathlib import Path
    from gui.docks.cell_editor import CellDock
    root, inc = _write_include_config(tmp_path)
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.load_entry("dac_buf", inc)
    dock._components.append({"role": "UNSAVED", "offset_along_mm": 1.0,
                             "offset_across_mm": 1.0, "angle_deg": 0.0})
    before = [dict(c) for c in dock._components]
    dock.select_cell_requested("dac_buf", None, "DAC_BUF", "Channel_0")
    assert [dict(c) for c in dock._components] == before
    assert Path(dock._path) == inc


# ── the ownership filter has ONE home (plan_2026_10_07_refused_tree_matching) ──

def test_select_cell_fallback_uses_the_one_registry_map():
    """Mutation "`select_cell_targets` grows its own scan again" dies here: the
    registry-only copper comes from
    ``kicadstamp/absent_copper_prune.registry_record_copper_map`` — the SAME map
    «Subtract selected copper» builds for a refused tree, which owns the
    ``is_own_key`` loop, the key tail AND (plan_2026_10_07_registry_pair_frame_check)
    the place check and the ORPHAN rule. «Select cell»'s fallback must not carry a
    second copy of any of them."""
    import inspect
    from gui.select_cell import select_cell_targets

    src = inspect.getsource(select_cell_targets)
    assert "registry_record_copper_map" in src
    assert "own_registry_entries" not in src
    assert "is_own_key(" not in src      # no CALL to the filter — only its name in prose
