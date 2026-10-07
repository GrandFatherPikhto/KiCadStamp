# tests/selection/test_adopt_at_current_place.py
"""«Adopt the copper at its CURRENT place, before the redraw» (plan
``plan_2026_10_07_adopt_at_current_place``, step 1).

The pass runs BEFORE any component moves and binds each cell record of the run's
own instances to the live copper that lies where the record puts it NOW. The one
fatal risk is a key the run's plan does NOT produce: ``BaseRegistry.reconcile``
treats such a key as stale and DELETES its copper. So the cells below pin BOTH:

  * every rule of the plan's cell table — new key / stale rebind / manual copper
    untouched / foreign record's copper not taken / live entry not rebound /
    ambiguous claim taken by no one / non-rigid frame skipped / rotated and
    mirrored clusters adopted correctly / dry run counts but writes nothing;
  * condition 1 — the adopted keys are a SUBSET of the SAME run's plan keys, on a
    cell via, a cell track and a component via, by driving the REAL planner
    (``ClonePositionCalculator.compute_raw_positions``) on the same instance.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from kicadstamp.adopt_at_current_place import adopt_cell_copper_at_current_place
from kicadstamp.config import (Cell, ClonePlacement, Config, TemplateComponentSlot,
                               TemplateTrack, TemplateVia)
from kicadstamp.config import format_version
from kicadstamp.constants import ROLE_FIELD_NAME, SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
    clone_registry_identity,
)
from kicadstamp.registry import PlacementRegistry, TrackRegistry, make_registry_key
from tests.fakes.format3 import det_uuid

CELL = "dac_buf"
CLUSTER = "DAC_BUF"
# The live FPGA slot of the fixture sits HERE, so every record of the cell lands
# at `(10, 10) + its own stored offset` in the identity frame.
FPGA_LIVE = (10.0, 10.0)


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


# ── the board/adapter rig (the pair-frame rig plus what the planner needs) ────

class _Adapter:
    def __init__(self, footprints=(), vias=(), tracks=()):
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        return fp.role if name == ROLE_FIELD_NAME else fp.cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_selected_items(self):
        return []

    def get_footprint_pads(self, fp):
        return list(getattr(fp, "pads", ()) or ())

    def get_pad_by_number(self, fp, number):
        return next((p for p in (getattr(fp, "pads", ()) or ())
                     if str(p.number) == str(number)), None)

    def temporarily_ignore_selection(self, flag):
        return nullcontext()


class _Fp(Footprint):
    def __init__(self, ref, role, cluster, x_mm, y_mm, nets=()):
        super().__init__(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                         position=Vector2.from_xy_mm(x_mm, y_mm),
                         layer=BoardLayer.BL_F_Cu)
        self.role = role
        self.cluster = cluster
        self.sheet_path_uuids = ()
        self.pads = [SimpleNamespace(number=str(i), net_name=n)
                     for i, n in enumerate(nets, start=1)]


def _live_via(uuid, x_mm, y_mm, net="N"):
    return Via(uuid=uuid, net_name=net, position=Vector2.from_xy_mm(x_mm, y_mm),
               drill_mm=0.3, diameter_mm=0.6)


def _live_track(uuid, x1, y1, x2, y2, net="N"):
    return Track(uuid=uuid, net_name=net, start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2), width_mm=0.25,
                 layer=BoardLayer.BL_F_Cu)


def _slot(role, along, across, vias=None):
    return SimpleNamespace(role=role, offset_along_mm=along,
                           offset_across_mm=across, vias=vias or [])


def _cell(*, second_slot=None, vias=None, tracks=None, components=None):
    """The fixture cell: slot FPGA at (0,0) (its anchor_role), a cell-level via 0
    at (0,0), a cell-level track 0 from (0,0) to (1,0). ``second_slot`` adds a
    role at a stored offset (its live footprint elsewhere makes the frame
    NON-RIGID); ``components`` overrides the slot list entirely."""
    comps = components if components is not None else [_slot("FPGA", 0.0, 0.0)]
    if second_slot is not None:
        comps = comps + [_slot("DA", second_slot[0], second_slot[1])]
    return SimpleNamespace(
        uuid=det_uuid(f"cells:{CELL}"),
        anchor_role="FPGA",
        anchor_xy=None,
        vias=vias if vias is not None else [
            SimpleNamespace(offset_along_mm=0.0, offset_across_mm=0.0)],
        tracks=tracks if tracks is not None else [
            SimpleNamespace(start_along_mm=0.0, start_across_mm=0.0,
                            end_along_mm=1.0, end_across_mm=0.0)],
        components=comps)


def _clone(*, name="dac0", xy=(0.0, 0.0), nets=None):
    return ClonePlacement(cluster=CLUSTER, cell=CELL, xy=xy, name=name,
                          uuid=det_uuid(f"clone_placements:{name}"),
                          nets=nets or {})


def _cfg(cell):
    return SimpleNamespace(cells={CELL: cell}, entities=[], clone_placements=[])


def _items(clone):
    return [SimpleNamespace(kind="clone", obj=clone)]


def _adapter(cell, *, second_live=None, vias=(), tracks=(), footprints=None):
    fps = list(footprints) if footprints is not None else [
        _Fp("IC9", "FPGA", CLUSTER, *FPGA_LIVE)]
    if second_live is not None:
        fps.append(_Fp("R1", "DA", CLUSTER, *second_live))
    return _Adapter(footprints=fps, vias=list(vias), tracks=list(tracks))


def _via_entry(uuid, x_mm, y_mm):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": "N",
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_entry(uuid, x1, y1, x2, y2):
    return {"uuid": uuid, "start_x_mm": x1, "start_y_mm": y1, "end_x_mm": x2,
            "end_y_mm": y2, "width_mm": 0.25, "net": "N", "layer": "F.Cu"}


def _schema() -> int:
    return 2 if format_version.current_format() >= 3 else 1


def _write_registry(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": _schema(), **entries}),
                    encoding="utf-8")


def _registries(adapter, tmp_path, via_entries=None, track_entries=None):
    via_path = tmp_path / "registry" / "config.registry.json"
    trk_path = tmp_path / "tracks" / "config.tracks.registry.json"
    _write_registry(via_path, via_entries or {})
    _write_registry(trk_path, track_entries or {})
    return PlacementRegistry(adapter, str(via_path)), TrackRegistry(adapter, str(trk_path))


def _key(clone, cfg, kind, role, index):
    """The key the adoption MUST build — the SAME builder the planner uses."""
    anchor_id, _cell, key_part = clone_registry_identity(clone, cfg.cells)
    return make_registry_key(anchor_id, key_part, role, index)


def _run(adapter, cell, clone, tmp_path, *, write=True, via_entries=None,
         track_entries=None):
    via_reg, trk_reg = _registries(adapter, tmp_path, via_entries, track_entries)
    report = adopt_cell_copper_at_current_place(
        adapter, _cfg(cell), _items(clone), via_reg, trk_reg, write=write)
    return report, via_reg, trk_reg


# ── the rules of the plan's cell table ────────────────────────────────────────

def test_empty_registry_adopts_the_copper_at_its_current_place(gate, tmp_path):
    """Empty registry, the node already displaced: the live via IS at the place
    the record puts it TODAY — it is bound to the record's key, so the later
    redraw deletes it (by uuid) instead of leaving a trail."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    key = _key(clone, _cfg(cell), "via", None, 0)
    assert report.adopted == 1
    assert report.vias == 1
    assert key in via_reg.entries
    assert via_reg.entries[key].uuid == "uv"


def test_a_foreign_uuid_is_rebound_to_this_board(gate, tmp_path):
    """A registry from another machine: the key exists but its uuid is not on
    THIS board. The record's live copper at its current place is rebound."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])
    key = _key(clone, _cfg(cell), "via", None, 0)

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path,
                              via_entries={key: _via_entry("gone", 99.0, 99.0)})

    assert report.adopted == 1
    assert via_reg.entries[key].uuid == "uv"


def test_manual_copper_away_from_the_record_is_left_alone(gate, tmp_path):
    """Copper that is NOT where the record puts its own is never claimed."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("manual", 50.0, 50.0)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    assert report.adopted == 0
    assert via_reg.entries == {}


def test_copper_owned_by_another_record_is_not_taken(gate, tmp_path):
    """A live item another registry entry owns is off limits — even if it happens
    to sit where THIS record puts its copper."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])
    other = make_registry_key("name:other", "other_cell", None, 0)

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path,
                              via_entries={other: _via_entry("uv", *FPGA_LIVE)})

    assert report.adopted == 0
    assert list(via_reg.entries) == [other]


def test_a_live_entry_is_never_rebound(gate, tmp_path):
    """The key's stored uuid is STILL on the board (just elsewhere): reconcile
    will move THAT copper by uuid, so the pass must not rebind the key to the
    fresh copper that sits at the record's place."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("old", 50.0, 50.0),
                                   _live_via("fresh", *FPGA_LIVE)])
    key = _key(clone, _cfg(cell), "via", None, 0)

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path,
                              via_entries={key: _via_entry("old", 50.0, 50.0)})

    assert report.adopted == 0
    assert via_reg.entries[key].uuid == "old"


def test_one_record_matching_two_live_items_is_not_taken(gate, tmp_path):
    """A record whose place matches TWO live items (duplicated copper) is an
    ambiguity — no item is taken."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("v1", *FPGA_LIVE),
                                   _live_via("v2", *FPGA_LIVE)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    assert report.adopted == 0
    assert report.ambiguous >= 1
    assert via_reg.entries == {}


def test_one_object_claimed_by_two_records_is_taken_by_neither(gate, tmp_path):
    """Two records of the SAME cell put their copper on one live item: an
    ambiguity — taken by no one."""
    cell = _cell(vias=[SimpleNamespace(offset_along_mm=0.0, offset_across_mm=0.0),
                       SimpleNamespace(offset_along_mm=0.0, offset_across_mm=0.0)])
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    assert report.adopted == 0
    assert report.ambiguous >= 1
    assert via_reg.entries == {}


def test_a_non_rigid_frame_skips_the_instance(gate, tmp_path):
    """A cluster that is not a rigid copy of the cell (the live `fpga` case) is
    skipped as a whole: the behaviour is unchanged for it, a trail may remain."""
    cell = _cell(second_slot=(5.0, 0.0))
    clone = _clone()
    adapter = _adapter(cell, second_live=(50.0, 50.0),
                       vias=[_live_via("uv", *FPGA_LIVE)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    assert report.skipped_not_rigid == 1
    assert report.adopted == 0
    assert via_reg.entries == {}


def test_a_rotated_cluster_adopts_the_rotated_place(gate, tmp_path):
    """A 90° cluster: the record's copper is claimed at the ROTATED world place."""
    cell = _cell(second_slot=(10.0, 0.0),
                 vias=[SimpleNamespace(offset_along_mm=10.0, offset_across_mm=0.0)])
    clone = _clone()
    # FPGA at (10,10); DA stored (10,0) at live (10,20) => the frame rotates
    # +X -> +Y, so the via stored at (10,0) lands at (10,20).
    adapter = _adapter(cell, second_live=(10.0, 20.0),
                       vias=[_live_via("uv", 10.0, 20.0)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    key = _key(clone, _cfg(cell), "via", None, 0)
    assert report.adopted == 1
    assert via_reg.entries[key].uuid == "uv"


def test_a_mirrored_cluster_adopts_the_mirrored_place(gate, tmp_path):
    """A mirrored cluster (B.Cu side): the record's copper is claimed at the
    MIRRORED world place."""
    cell = _cell(second_slot=(10.0, 0.0),
                 vias=[SimpleNamespace(offset_along_mm=10.0, offset_across_mm=0.0)])
    clone = _clone()
    # FPGA at (10,10); DA stored (10,0) at live (0,10) => the frame mirrors +X,
    # so the via stored at (10,0) lands at (0,10).
    adapter = _adapter(cell, second_live=(0.0, 10.0),
                       vias=[_live_via("uv", 0.0, 10.0)])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    key = _key(clone, _cfg(cell), "via", None, 0)
    assert report.adopted == 1
    assert via_reg.entries[key].uuid == "uv"


def test_dry_run_counts_but_writes_no_byte(gate, tmp_path):
    """write=False: the same pass, the same count — and both registry files stay
    byte-for-byte unchanged."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])
    via_path = tmp_path / "registry" / "config.registry.json"
    trk_path = tmp_path / "tracks" / "config.tracks.registry.json"
    _write_registry(via_path, {})
    _write_registry(trk_path, {})
    before = (via_path.read_bytes(), trk_path.read_bytes())

    report, via_reg, trk_reg = _run(adapter, cell, clone, tmp_path, write=False)

    assert report.adopted == 1
    assert via_reg.entries == {} and trk_reg.entries == {}
    assert (via_path.read_bytes(), trk_path.read_bytes()) == before


def test_copper_outside_the_frame_tolerance_is_not_taken(gate, tmp_path):
    """3 mm off the record's place is OUTSIDE RIGID_TOLERANCE_MM (0.05) — but a
    x100 tolerance mutation would reach it. The tolerance stays 0.05."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", FPGA_LIVE[0] + 3.0, FPGA_LIVE[1])])

    report, via_reg, _ = _run(adapter, cell, clone, tmp_path)

    assert report.adopted == 0
    assert via_reg.entries == {}


def test_only_clone_instances_are_examined(gate, tmp_path):
    """Condition 2: only clones from ``items`` are walked — a chain/point item of
    the same run is never touched."""
    cell = _cell()
    clone = _clone()
    adapter = _adapter(cell, vias=[_live_via("uv", *FPGA_LIVE)])
    via_reg, trk_reg = _registries(adapter, tmp_path)
    items = [SimpleNamespace(kind="chain", obj=object()),
             SimpleNamespace(kind="clone", obj=clone)]

    report = adopt_cell_copper_at_current_place(
        adapter, _cfg(cell), items, via_reg, trk_reg, write=True)

    assert report.adopted == 1


# ── condition 1: adopted keys ⊆ the SAME run's plan keys (real planner) ───────

def _planner_setup():
    """One absolute clone of a cell carrying a cell via, a cell track AND a
    component via, with the live board the planner places it onto."""
    cell = Cell(
        name=CELL, uuid=det_uuid(f"cells:{CELL}"), layer="F.Cu",
        components=[
            TemplateComponentSlot(role="MOUNT", offset_along_mm=0.0,
                                  offset_across_mm=0.0),
            TemplateComponentSlot(role="DA", offset_along_mm=5.0,
                                  offset_across_mm=0.0,
                                  vias=[TemplateVia(offset_along_mm=20.0,
                                                    offset_across_mm=0.0,
                                                    net="N2", drill_mm=0.3,
                                                    diameter_mm=0.6)]),
        ],
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="N1",
                          drill_mm=0.3, diameter_mm=0.6)],
        tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                              end_along_mm=1.0, end_across_mm=0.0, width_mm=0.25,
                              net="N1", layer="F.Cu")])
    clone = _clone(xy=(10.0, 10.0), nets={"MOUNT": "N1", "DA": "N2"})
    # MOUNT (zero offset) at (10,10) = origin; DA slot at (15,10) (frame only).
    # Copper offsets are in the CELL frame: cell via (0,0) -> (10,10), cell track
    # (0,0)->(1,0) -> (10,10)->(11,10), DA's via (20,0) -> (30,10) — kept apart
    # so no two records claim one object.
    adapter = _Adapter(
        footprints=[_Fp("IC1", "MOUNT", CLUSTER, 10.0, 10.0, nets=["N1"]),
                    _Fp("R1", "DA", CLUSTER, 15.0, 10.0, nets=["N2"])],
        vias=[_live_via("v_cell", 10.0, 10.0, net="N1"),
              _live_via("v_da", 30.0, 10.0, net="N2")],
        tracks=[_live_track("t_cell", 10.0, 10.0, 11.0, 10.0, net="N1")])
    cfg = Config(layer="F.Cu", cells={CELL: cell})
    return adapter, cell, clone, cfg


def test_adopted_keys_are_a_subset_of_the_plan_keys(gate, tmp_path):
    """Condition 1: the keys the adoption writes MUST be keys the same run's plan
    produces — otherwise reconcile prunes the entry and DELETES the copper. Covers
    a cell via, a cell track and a component via, checked against the REAL
    planner (``compute_raw_positions``)."""
    adapter, cell, clone, cfg = _planner_setup()
    _placed, plan_vias, plan_tracks = ClonePositionCalculator(
        adapter, cfg, sheet_names={}).compute_raw_positions([clone])
    plan_keys = {c.registry_key for c in list(plan_vias) + list(plan_tracks)}

    via_reg, trk_reg = _registries(adapter, tmp_path)
    report = adopt_cell_copper_at_current_place(
        adapter, SimpleNamespace(cells=cfg.cells), [SimpleNamespace(kind="clone",
                                                                    obj=clone)],
        via_reg, trk_reg, write=True)
    adopted = set(via_reg.entries) | set(trk_reg.entries)

    assert report.adopted == 3, "cell via + cell track + component via"
    assert adopted and adopted <= plan_keys
    # All three levels really are present (a single merged row would hide one).
    assert len(via_reg.entries) == 2      # cell via + component via
    assert len(trk_reg.entries) == 1      # cell track
    # The plan really carries both levels (spoke placeholder AND the role), so
    # the inclusion above is not vacuously true over a single merged key.
    assert any(SPOKE_LEVEL_ROLE_PLACEHOLDER in k for k in plan_keys)
    assert any("|DA|" in k for k in plan_keys)
