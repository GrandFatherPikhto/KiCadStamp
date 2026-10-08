# tests/gui/test_select_cell_copper.py
"""СЦ-2 guards (plan_2026_10_05_select_cell_split): «Select cell» = components +
RECORDED copper (registry first, then geometry over the redraw planner's
commands), and «Select cell components» = components ONLY.

The copper core is ``gui/select_cell.select_cell_copper_targets``; the workers
live in ``gui/select_cell_copper.py``. Here we drive the CORE against a fake
adapter + real registries (tmp files), the components worker against a fake
board adapter + a real config, and the structural menu guard.
"""
import json
import threading
from types import SimpleNamespace

import pytest

import gui.worker as worker_mod
import kicadstamp.adapter_factory as adapter_factory_mod

from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.constants import (CLUSTER_FIELD_NAME, ROLE_FIELD_NAME,
                                  SPOKE_LEVEL_ROLE_PLACEHOLDER)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import make_registry_key, record_key_part
from tests.fakes.format3 import det_uuid


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Rec:
    def __init__(self, name=None, uuid=None, cell=None, cluster=None, sheet=None,
                 retired=False, role=None, vias=(), tracks=(), components=(),
                 anchor_role=None, anchor_xy=None):
        self.name = name
        self.uuid = uuid
        self.cell = cell
        self.cluster = cluster
        self.sheet = sheet
        self.retired = retired
        # the cell's own record lists / slots (a Cell as the registry-only map
        # and the cell frame read them)
        self.role = role
        self.vias = list(vias)
        self.tracks = list(tracks)
        self.components = list(components)
        self.anchor_role = anchor_role
        self.anchor_xy = anchor_xy


class _Cfg:
    def __init__(self, entities=(), clone_placements=(), cells=None, chains=()):
        self.entities = list(entities)
        self.clone_placements = list(clone_placements)
        self.cells = cells or {}
        self.chains = list(chains)


class _Adapter:
    def __init__(self, fields, footprints=(), vias=(), tracks=()):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)
        self.selected = None

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def select_items(self, items):
        self.selected = list(items)

    def refresh_board(self):
        pass

    def close(self):
        pass


def _fp(ref, role, cluster, x_mm=0.0, y_mm=0.0):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = ()
    return fp


def _rec_via(along, across):
    return SimpleNamespace(offset_along_mm=along, offset_across_mm=across)


def _slot(role, along, across):
    return SimpleNamespace(role=role, offset_along_mm=along,
                           offset_across_mm=across, vias=[])


def _write_via_registry(tmp_path, via_entries):
    schema = 2 if current_format() >= 3 else 1
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema}), encoding="utf-8")


def _via_entry(uuid, x_mm, y_mm, net="N", drill=0.3, dia=0.6):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": net,
            "drill_mm": drill, "diameter_mm": dia}


def _via(uuid, x_mm, y_mm, net="N", drill=0.3, dia=0.6):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm), net_name=net,
               drill_mm=drill, diameter_mm=dia)


def _planned_via_at(key, x_mm, y_mm):
    return ViaCommand(position=Vector2.from_xy_mm(x_mm, y_mm), net_name="N",
                      drill_mm=0.3, diameter_mm=0.6, owner_ref="C1",
                      registry_key=key)


def _planned_via(key):
    return ViaCommand(position=Vector2.from_xy_mm(0.0, 0.0), net_name="N",
                      drill_mm=0.3, diameter_mm=0.6, owner_ref="C1",
                      registry_key=key)


def _setup(tmp_path, live_vias):
    """(cfg, config_path, adapter, our_registry_key) for cell dac_buf @ DAC_BUF.

    The cell carries ONE real record — the cell-level via 0 at the cell's own
    (0,0) — and ONE slot (role DA, also at (0,0)); the live footprint C1 of that
    role stands at the world origin, so the registry-only frame is RIGID with its
    origin on the cell's (0,0): the record's stored offset IS its world position.
    The key names that record through the spoke placeholder — an ORPHAN key (a
    role the cell does not have, an index past the list) is never a pair
    (plan_2026_10_07_registry_pair_frame_check, rule 3), so it could not carry
    the cells below."""
    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg(cells={"dac_buf": _Rec(
        uuid=cell_uuid, vias=[_rec_via(0.0, 0.0)],
        components=[_slot("DA", 0.0, 0.0)])})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0)
    adapter = _Adapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp_c1],
                       vias=live_vias)
    key = make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity,
                            SPOKE_LEVEL_ROLE_PLACEHOLDER, 0)
    return cfg, config_path, adapter, key


def _core(adapter, cfg, config_path, planned_vias, own_refs=("C1",)):
    from gui.select_cell import select_cell_copper_targets
    return select_cell_copper_targets(
        adapter, cfg, str(config_path), "dac_buf", "DAC_BUF", None, {},
        list(planned_vias), [], own_refs=list(own_refs))


# ── (в) live registry -> by registry, no doubling ───────────────────────────

def test_live_registry_is_found_by_registry(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_own", 0.0, 0.0)])
    _write_via_registry(tmp_path, {key: _via_entry("v_own", 0.0, 0.0)})
    plan = _core(adapter, cfg, config_path, [_planned_via(key)])
    assert [getattr(i, "uuid", None) for i in plan.copper] == ["v_own"]
    assert plan.copper_by_registry == 1 and plan.copper_by_geometry == 0
    assert plan.copper_missing == 0


# ── (б) stale registry + geometry -> by geometry ────────────────────────────

def test_stale_registry_falls_back_to_geometry(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_geo", 0.0, 0.0)])
    _write_via_registry(tmp_path, {})           # registry empty, board matches
    plan = _core(adapter, cfg, config_path, [_planned_via(key)])
    assert [getattr(i, "uuid", None) for i in plan.copper] == ["v_geo"]
    assert plan.copper_by_geometry == 1 and plan.copper_by_registry == 0
    assert "by geometry" in plan.line


# ── (Б1) пара реестра НЕ там, где план: выделяется и называется ─────────────

def test_a_registry_pair_away_from_the_plan_is_selected_and_named(gate, tmp_path):
    """Б1 of plan_2026_10_08_narrowing_net_traces_cost, the LIVE case: the instance
    was edited and never redrawn, so its recorded copper stands where the plan does
    NOT put it. «Select cell» takes the pair by the KEY (the instance's own record
    says the copper is its) and only NAMES the distance — it used to refuse it,
    which is exactly «copper — 0 by registry, 0 by geometry» with the copper on
    the board.

    Mutation: put the place check back in front of the selection and this cell
    reports 0 selected / 1 recorded-but-absent."""
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_own", 5.0, 5.0)])
    _write_via_registry(tmp_path, {key: _via_entry("v_own", 0.0, 0.0)})

    plan = _core(adapter, cfg, config_path, [_planned_via(key)])

    assert [getattr(i, "uuid", None) for i in plan.copper] == ["v_own"]
    assert plan.copper_by_registry == 1 and plan.copper_by_geometry == 0
    assert plan.copper_missing == 0
    assert "away from the planned place" in plan.line, plan.line


def test_the_strict_map_still_refuses_what_the_select_map_takes(gate, tmp_path):
    """The SUBTRACT door is untouched (rule 33): the SAME rig read with the map's
    default `place_check=True` refuses the away pair — a removal must never claim
    copper the frame cannot place. Only «Select cell» passes place_check=False."""
    from kicadstamp.absent_copper_prune import registry_record_copper_map

    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_own", 5.0, 5.0)])
    _write_via_registry(tmp_path, {key: _via_entry("v_own", 0.0, 0.0)})

    strict = registry_record_copper_map(adapter, str(config_path), cfg,
                                        "dac_buf", "DAC_BUF", None)
    lenient = registry_record_copper_map(adapter, str(config_path), cfg,
                                         "dac_buf", "DAC_BUF", None,
                                         place_check=False)

    assert strict.by_record == {} and strict.entries_total == 1
    assert strict.disagreed == 1
    assert lenient.by_record and lenient.disagreed == 1
    assert lenient.missing_from_board == 0


# ── (г) foreign-by-registry is never selected ──────────────────────────────

def test_foreign_registered_item_is_not_taken(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_foreign", 0.0, 0.0)])
    other = make_registry_key("anchor:X9:1:0.0000:0.0000", "other_cell", "r", 0)
    _write_via_registry(tmp_path, {other: _via_entry("v_foreign", 0.0, 0.0)})
    plan = _core(adapter, cfg, config_path, [_planned_via(key)])
    assert plan.copper == []
    assert plan.copper_missing == 1


# ── (д) recorded but absent -> K, no exception ─────────────────────────────

def test_recorded_but_absent_is_counted(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [])
    _write_via_registry(tmp_path, {})
    plan = _core(adapter, cfg, config_path, [_planned_via(key)])
    assert plan.copper == [] and plan.copper_missing == 1
    assert "recorded but not on the board — 1" in plan.line


# ── (Б2/Б3) медь ТЕКУЩЕГО места из прохода + строка Δ ───────────────────────

def test_the_current_place_copper_is_selected_and_the_delta_named(gate, tmp_path):
    """Б2+Б3 of plan_2026_10_08_narrowing_net_traces_cost: the pairs the
    at-current-place pass decided on are selected AS-IS (they are the answer to
    "what does this instance have on the board now") and the distance to the plan
    is named once. Nothing here recomputes the current place.

    Mutation: ignore `at_current_place` and the live case comes back — 0 copper
    while the board carries it."""
    from gui.select_cell import select_cell_copper_targets

    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_cur", 0.0, 0.0)])
    _write_via_registry(tmp_path, {})           # the registry does NOT know it yet
    live_via = adapter.get_vias()[0]

    plan = select_cell_copper_targets(
        adapter, cfg, str(config_path), "dac_buf", "DAC_BUF", None, {},
        [_planned_via_at(key, 5.0, 0.0)], [], own_refs=["C1"],
        at_current_place=(("via", key, live_via),))

    assert [getattr(i, "uuid", None) for i in plan.copper] == ["v_cur"]
    assert plan.copper_by_geometry == 1 and plan.copper_by_registry == 0
    assert plan.copper_missing == 0
    assert "5.000 mm away from where the tree places it" in plan.line, plan.line


def test_the_worker_selects_what_the_read_wrote_at_the_current_place(
        monkeypatch, tmp_path):
    """The acceptance scenario of part Б: the read wrote copper into the cell and
    the instance was NOT redrawn (the registry does not know the copper and the
    planned place finds nothing — exactly the live defect). «Select cell» right
    after the read must still select the components AND that copper, at the place
    the instance stands NOW, and say how far it is from the tree's place.

    Mutation: stop handing `pipeline.at_current_place` over and the selection is
    the components alone."""
    from gui import select_cell_copper as scc

    config_path = tmp_path / "root.sexp"
    _min_config(config_path)                  # cells.dac_buf + entity dac0
    live_via = _via("v-read", 10.0, 10.0)
    adapter = _Adapter({"C1": ("R", "DAC_BUF")},
                       footprints=[_fp("C1", "R", "DAC_BUF")], vias=[live_via])

    class _Pipe:
        def __init__(self, *a, **k):
            self.adapter = adapter
            self.at_current_place = SimpleNamespace(
                bound=(("via", "k1", live_via),), adopted=1)
            self.closed = False

        def run(self):
            pass

        def plan_copper(self):
            # The plan puts the record's copper 5 mm away from where it stands.
            return [_planned_via_at("k1", 15.0, 10.0)], []

        def close(self):
            self.closed = True

    monkeypatch.setattr("kicadstamp.apply_pipeline.ApplyPipeline", _Pipe)

    result = scc.run_select_cell_worker({
        "with_copper": True, "root_path": str(config_path), "timeout_ms": 1,
        "cell_name": "dac_buf", "cluster": "DAC_BUF", "sheet": None, "refs": {}})

    assert [getattr(i, "uuid", None) for i in adapter.selected] == \
        ["uuid-C1", "v-read"], "components AND the copper the read wrote"
    assert result["copper"] == 1 and result["by_geometry"] == 1
    assert "5.000 mm away from where the tree places it" in result["line"]


# ── registry files untouched ────────────────────────────────────────────────

def test_registry_files_are_byte_identical(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_own", 0.0, 0.0)])
    _write_via_registry(tmp_path, {key: _via_entry("v_own", 0.0, 0.0)})
    via_file = tmp_path / "registry" / "config.registry.json"
    track_file = tmp_path / "tracks" / "config.tracks.registry.json"
    before = (via_file.read_bytes(), track_file.read_bytes())
    _core(adapter, cfg, config_path, [_planned_via(key)])
    assert (via_file.read_bytes(), track_file.read_bytes()) == before


# ── record resolver & chain detection (pure) ───────────────────────────────

def test_instance_recording_name_prefers_entity_and_matches_address():
    from gui.select_cell import instance_recording_name
    cfg = _Cfg(entities=[_Rec(name="dac0", cell="dac_buf", cluster="DAC_BUF",
                              sheet="Channel_0")],
               clone_placements=[_Rec(name="cl", cell="dac_buf",
                                      cluster="DAC_BUF", sheet="Channel_1")])
    assert instance_recording_name(cfg, "dac_buf", "DAC_BUF", "Channel_0") == "dac0"
    assert instance_recording_name(cfg, "dac_buf", "DAC_BUF", "Channel_1") == "cl"
    assert instance_recording_name(cfg, "dac_buf", "DAC_BUF", "Channel_9") is None


def test_instance_placed_by_chain():
    from gui.select_cell import instance_placed_by_chain
    spoke = SimpleNamespace(cell="fpga_pwr_bank", cluster="FPGA_PWR_BANK",
                            retired=False, skip=False)
    cfg = _Cfg(chains=[SimpleNamespace(spokes=[spoke])])
    assert instance_placed_by_chain(cfg, "fpga_pwr_bank", "FPGA_PWR_BANK")
    assert not instance_placed_by_chain(cfg, "dac_buf", "DAC_BUF")


def test_cell_has_recorded_copper():
    from gui.select_cell import cell_has_recorded_copper
    assert cell_has_recorded_copper(None) is False
    assert cell_has_recorded_copper(
        SimpleNamespace(vias=[], tracks=[], components=[])) is False
    assert cell_has_recorded_copper(
        SimpleNamespace(vias=[1], tracks=[], components=[])) is True
    assert cell_has_recorded_copper(
        SimpleNamespace(vias=[], tracks=[],
                        components=[SimpleNamespace(vias=[1])])) is True


# ── (СЦ-4-2) planner gave no copper -> honest line, registry only ───────────

def test_no_planned_copper_uses_registry_only_and_says_so(gate, tmp_path):
    cfg, config_path, adapter, key = _setup(tmp_path, [_via("v_own", 0.0, 0.0)])
    _write_via_registry(tmp_path, {key: _via_entry("v_own", 0.0, 0.0)})
    from gui.select_cell import no_planned_copper_targets
    plan = no_planned_copper_targets(
        adapter, cfg, str(config_path), "dac_buf", "DAC_BUF", None, {},
        "dac0", own_refs=["C1"])
    assert [getattr(i, "uuid", None) for i in plan.copper] == ["v_own"]
    assert plan.copper_by_registry == 1
    assert "produced no copper for record dac0" in plan.line
    assert "registry only: 1" in plan.line


def test_worker_uses_the_honest_line_when_the_planner_gave_nothing(
        monkeypatch, tmp_path):
    from gui import select_cell_copper as scc
    config_path = tmp_path / "root.sexp"
    _min_config(config_path)          # cells.dac_buf + entity dac0
    adapter = _Adapter({"C1": ("R", "DAC_BUF")}, footprints=[_fp("C1", "R", "DAC_BUF")])

    class _Pipe:
        def __init__(self, *a, **k):
            self.adapter = adapter
            self.closed = False
        def run(self):
            pass
        def plan_copper(self):
            return [], []             # the planner produced NOTHING
        def close(self):
            self.closed = True

    monkeypatch.setattr("kicadstamp.apply_pipeline.ApplyPipeline", _Pipe)
    monkeypatch.setattr("gui.select_cell.cell_has_recorded_copper", lambda cell: True)
    calls = {}

    def _sentinel(adapter_, cfg_, config_path_, cell_name, cluster, sheet,
                  sheet_names, record_name, own_refs=None):
        calls["record"] = record_name
        return SimpleNamespace(footprints=[_fp("C1", "R", "DAC_BUF")], copper=[],
                               copper_by_registry=0, copper_by_geometry=0,
                               copper_missing=0, copper_planned=0, line="HONEST")
    monkeypatch.setattr("gui.select_cell.no_planned_copper_targets", _sentinel)

    result = scc.run_select_cell_worker({
        "with_copper": True, "root_path": str(config_path), "timeout_ms": 1,
        "cell_name": "dac_buf", "cluster": "DAC_BUF", "sheet": None, "refs": {}})

    assert calls["record"] == "dac0"
    assert result["line"] == "HONEST"
    assert [f.ref for f in adapter.selected] == ["C1"]


# ── (а) components worker selects ONLY components ──────────────────────────

def _min_config(path):
    from kicadstamp.config.sexp_format import dict_to_sexp
    path.write_text(dict_to_sexp({
        "cells": {"dac_buf": {"layer": "F.Cu",
                              "components": [{"role": "DA", "offset_along_mm": 0.0,
                                              "offset_across_mm": 0.0,
                                              "angle_deg": 0.0}],
                              "vias": [], "tracks": []}},
        "entities": [{"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
                      "sheet": "Channel_0"}]}, format_number=2), encoding="utf-8")


def test_components_worker_selects_components_only(monkeypatch, tmp_path):
    from gui import select_cell_copper as scc
    config_path = tmp_path / "root.sexp"
    _min_config(config_path)
    fp_c1 = _fp("C1", "DA", "DAC_BUF")
    fp_c2 = _fp("C2", "DB", "DAC_BUF")
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2],
                       vias=[_via("v_x", 0, 0)],
                       tracks=[Track(uuid="t_x", net_name="N",
                                     start=Vector2.from_xy_mm(0, 0),
                                     end=Vector2.from_xy_mm(1, 0),
                                     width_mm=0.25, layer=BoardLayer.BL_F_Cu)])
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kw: adapter)
    result = scc.run_select_cell_worker({"with_copper": False, "timeout_ms": 1,
                                         "cluster": "DAC_BUF", "sheet": None,
                                         "root_path": str(config_path), "refs": {}})
    assert result.get("error") is None
    assert result["selected"] == 2
    assert {f.ref for f in adapter.selected} == {"C1", "C2"}
    assert all(isinstance(i, Footprint) for i in adapter.selected)


# ── (е) menu: three items in order, by objectName ──────────────────────────

def test_menu_has_the_three_items_in_order():
    import inspect
    import gui.docks.config_tree as ct
    import gui.docks.entity_tree as et
    # часть 3, п.3: the three items live ONLY on the ENTITY leaf now (a cell leaf
    # carries no board item — its own guard lives in test_entities_under_cells),
    # so the source holds them ONCE.
    src = inspect.getsource(ct) + inspect.getsource(et)
    assert src.count("select_cell_components_action") == 1  # the entity menu
    assert src.count('"select_cell_action"') == 1
    assert src.count("select_enclosed_copper_action") == 1
    i_comp = src.index("select_cell_components_action")
    i_cell = src.index('"select_cell_action"')
    i_enc = src.index("select_enclosed_copper_action")
    assert i_comp < i_cell < i_enc
    assert hasattr(ct.ConfigTreeDock, "cell_select_components_requested")
    assert hasattr(ct.ConfigTreeDock, "cell_select_requested")


def test_hub_delegate_components_calls_the_shared_entry():
    """СЦ-1: DockHub's components delegate drives CellDock's components entry."""
    from gui.dock_hub import DockHub
    calls = []

    class _Cells:
        def select_cell_components_requested(self, name, fp, cluster, sheet):
            calls.append((name, fp, cluster, sheet))

    hub = SimpleNamespace(cells_dock=_Cells())
    DockHub._select_cell_components_from_tree(hub, "dac_buf", "f.sexp",
                                              "DAC_BUF", "Channel_1")
    assert calls == [("dac_buf", "f.sexp", "DAC_BUF", "Channel_1")]


# ── door: no UI-thread board read (real BoardConnection, raise mode) ────────

def _arm_door(real_main_window, tmp_path, monkeypatch, adapter):
    from gui import connection as conn_mod
    from gui.connection import BoardConnection
    from tests.gui.create_entity_helpers import write_config

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())   # setter; getter is guarded
    w.connection = conn
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"dac_buf": {
        "components": [{"role": "R"}], "vias": [], "tracks": []}}})
    hub = w._dock_hub
    hub.root_metadata_dock._path = root
    dock = hub.cells_dock
    dock.set_root_path(root)
    dock.load_entry("dac_buf", root)

    monkeypatch.setattr(adapter_factory_mod, "create_board_adapter",
                        lambda **kw: adapter)
    launched = {}

    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        launched["payload"] = args[0] if args else None
        try:
            on_success(fn(*args))
        except Exception as e:  # noqa: BLE001 — mirror the worker's routing
            on_error(str(e))
        return None
    monkeypatch.setattr(worker_mod, "start_long_op", _sync)
    # cell_editor bound the name at import (`from ..worker import start_long_op`)
    monkeypatch.setattr("gui.docks.cell_editor.start_long_op", _sync)

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)
    return hub, dock, launched


def test_components_button_door_reads_the_board_on_the_worker_only(
        real_main_window, tmp_path, monkeypatch):
    """The components entry (real worker, inline) reads ``connection.board`` on
    NO UI-thread path and carries NO board handle in the payload."""
    fp_c1 = _fp("C1", "R", "DAC_BUF")
    adapter = _Adapter({"C1": ("R", "DAC_BUF")}, footprints=[fp_c1])
    _hub, dock, launched = _arm_door(real_main_window, tmp_path, monkeypatch, adapter)

    dock.select_cell_components_requested("dac_buf", None, "DAC_BUF", None)

    assert launched["payload"].get("board") is None       # no UI-thread handle
    assert launched["payload"]["with_copper"] is False
    assert [f.ref for f in adapter.selected] == ["C1"]


def test_select_cell_door_payload_carries_no_board_handle(
        real_main_window, tmp_path, monkeypatch):
    """«Select cell» (copper) door: same real door, the worker stubbed (its heavy
    pipeline is its own concern) — the payload must carry NO board handle and
    ``with_copper=True``. Mutation: "the UI-thread handle returns to the payload"
    dies here."""
    adapter = _Adapter({})
    _hub, dock, launched = _arm_door(real_main_window, tmp_path, monkeypatch, adapter)
    monkeypatch.setattr("gui.select_cell_copper.run_select_cell_worker",
                        lambda payload: {"components": 0, "copper": 0, "line": "x"})

    dock.select_cell_requested("dac_buf", None, "DAC_BUF", None)

    assert "board" not in launched["payload"]
    assert launched["payload"]["with_copper"] is True
    assert launched["payload"]["config_path"] and launched["payload"]["timeout_ms"]


def test_each_entry_point_routes_its_own_mode(real_main_window, tmp_path, monkeypatch):
    """(а) guard 1: CLICKING the CellDock buttons routes the right mode —
    "Select cell components" (False) and "Select cell" (True). The door tests
    above call the entry methods directly; only a CLICK covers the buttons'
    own wiring (mutation C5)."""
    from gui import connection as conn_mod
    from gui.connection import BoardConnection
    from tests.gui.create_entity_helpers import write_config

    w = real_main_window
    conn = BoardConnection()
    conn.board = SimpleNamespace(adapter=object())
    w.connection = conn
    root = tmp_path / "root.sexp"
    write_config(root, {
        "cells": {"dac_buf": {"components": [{"role": "R"}],
                              "vias": [], "tracks": []}},
        "entities": [{"name": "dac0", "cell": "dac_buf",
                      "cluster": "DAC_BUF", "sheet": "Channel_0"}]})
    hub = w._dock_hub
    hub.root_metadata_dock._path = root
    dock = hub.cells_dock
    dock.set_root_path(root)
    dock.load_entry("dac_buf", root)

    monkeypatch.setattr("gui.select_cell_copper.run_select_cell_worker",
                        lambda payload: {"line": "x"})
    launched = {}

    def _sync(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        launched["payload"] = args[0]
        on_success(fn(*args))
        return None
    monkeypatch.setattr("gui.docks.cell_editor.start_long_op", _sync)

    main = threading.current_thread()
    monkeypatch.setattr(conn_mod, "ui_thread_predicate",
                        lambda: threading.current_thread() is main)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    # часть 3, п.4: the buttons are gone from CellDock; the two MODES live on the
    # entry points the tree doors drive (components-only vs components + copper).
    dock.select_cell_components_requested("dac_buf", root)
    assert launched["payload"]["with_copper"] is False
    launched.clear()
    dock.select_cell_requested("dac_buf", root)
    assert launched["payload"]["with_copper"] is True
