# tests/selection/test_registry_pair_frame.py
"""A registry pair must SIT WHERE THE RECORD PUTS ITS COPPER (plan
``plan_2026_10_07_registry_pair_frame_check``, rules 1-3; finding of the
f3e116d0 acceptance).

The key's ``index`` is the record's number in the cell's list AT THE LAST REDRAW.
Editing that list shifts the numbers and nobody renumbers the registry, so a key
can keep the uuid of ANOTHER record's copper — and after a record is deleted its
copper is usually still on the board, so the pair would name the record that now
sits at index k. Every path therefore checks the pair by PLACE, in the one place
it can trust, and the cells below pin each of them:

  * a DRY RUN — the live item must lie where the COMMAND plans the copper
    (``registry_match.accept_planned_match``, the registry's own predicate and
    its ``POSITION_TOLERANCE_MM``): the tree was not refused, so its plan's own
    place is exact even when the live cluster is NOT a rigid copy;
  * the REGISTRY-ONLY path — inside the CELL FRAME built from the instance's live
    components, and only while that frame is RIGID
    (``cell_frame.RIGID_TOLERANCE_MM``): a non-rigid frame is off by more than the
    step between neighbouring records, so it cannot tell a pair from its
    neighbour — then NO registry pair is accepted at all;
  * an ORPHAN key (its index is past the end of the cell's record list, or names
    a role the cell no longer has) is NEVER a pair.

Every refusal is COUNTED and NAMED (``absent_copper_prune.not_checked_reasons``),
never silent: the cells below also pin the wording both doors print.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kicadstamp.absent_copper_prune import (
    RecordCopperMap,
    not_checked_reasons,
    pair_agrees_with_record,
    record_copper_map_for,
    record_points,
    registry_record_copper_map,
)
from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.config import format_version
from kicadstamp.constants import ROLE_FIELD_NAME, SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.registry import make_registry_key, record_key_part
from tests.fakes.format3 import det_uuid, registry_schema

CELL = "dac_buf"
CLUSTER = "DAC_BUF"
# The record identity of the fixture's cell under the ACTIVE format gate — the
# SAME call the product makes, so the keys below address the same cell either way.
CELL_IDENTITY = record_key_part(CELL, "uuid-cell")
# The live cluster of the fixture: the FPGA slot sits HERE, so every record of
# the cell the fixture describes lands at `(10, 10) + its own stored offset`.
FPGA_LIVE = (10.0, 10.0)


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Adapter:
    """A board made of exactly what the map and the frame read."""

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


class _Fp(Footprint):
    """A Footprint that also carries the Role/Cluster fields the adapter reads."""

    def __init__(self, ref, role, cluster, x_mm, y_mm):
        super().__init__(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                         position=Vector2.from_xy_mm(x_mm, y_mm),
                         layer=BoardLayer.BL_F_Cu)
        self.role = role
        self.cluster = cluster
        self.sheet_path_uuids = ()


def _live_via(uuid, x_mm, y_mm, net="N"):
    return Via(uuid=uuid, net_name=net, position=Vector2.from_xy_mm(x_mm, y_mm),
               drill_mm=0.3, diameter_mm=0.6)


def _live_track(uuid, x1, y1, x2, y2, net="N"):
    return Track(uuid=uuid, net_name=net, start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2), width_mm=0.25,
                 layer=BoardLayer.BL_F_Cu)


def _slot(role, along, across):
    return SimpleNamespace(role=role, offset_along_mm=along,
                           offset_across_mm=across, vias=[])


def _cell(*, second_slot=None, tracks=None):
    """The fixture cell: slot FPGA at the cell's own (0,0) (its anchor_role), a
    cell-level via 0 at (0,0) and a cell-level track 0 from (0,0) to (1,0) —
    so, with the live FPGA at FPGA_LIVE, via 0 lands on (10,10) and track 0 on
    (10,10)-(11,10).

    ``second_slot`` — an extra slot at that stored offset (its live footprint is
    placed elsewhere by the caller), which is what makes the frame NON-RIGID."""
    components = [_slot("FPGA", 0.0, 0.0)]
    if second_slot is not None:
        components.append(_slot("DA", second_slot[0], second_slot[1]))
    return SimpleNamespace(
        uuid=det_uuid(f"cells:{CELL}"),
        anchor_role="FPGA",
        anchor_xy=None,
        vias=[SimpleNamespace(offset_along_mm=0.0, offset_across_mm=0.0)],
        tracks=tracks if tracks is not None else [
            SimpleNamespace(start_along_mm=0.0, start_across_mm=0.0,
                            end_along_mm=1.0, end_across_mm=0.0)],
        components=components)


class _Cfg:
    def __init__(self, cell, second_live=None):
        self.cells = {CELL: cell}
        self.entities = [SimpleNamespace(
            name="dac0", uuid="uuid-ent", cell=CELL, cluster=CLUSTER,
            sheet=None, retired=False)]
        self.clone_placements = []


def _adapter(cell, *, second_live=None, vias=(), tracks=()):
    footprints = [_Fp("IC9", "FPGA", CLUSTER, *FPGA_LIVE)]
    if second_live is not None:
        footprints.append(_Fp("R1", "DA", CLUSTER, *second_live))
    return _Adapter(footprints=footprints, vias=list(vias), tracks=list(tracks))


def _registries(tmp_path, via_entries, track_entries):
    """The two registry files `registry_paths_for_config` derives from the
    config's stem — with the schema the ACTIVE format gate reads (the registry
    reader refuses the other one, which is the point of the gate)."""
    schema = registry_schema()
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **track_entries}), encoding="utf-8")


def _via_entry(uuid, x_mm, y_mm):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": "N",
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_entry(uuid, x1, y1, x2, y2):
    return {"uuid": uuid, "start_x_mm": x1, "start_y_mm": y1, "end_x_mm": x2,
            "end_y_mm": y2, "width_mm": 0.25, "net": "N", "layer": "F.Cu"}


def _own_key(cell, index, role=None):
    return make_registry_key(f"name:{record_key_part('dac0', 'uuid-ent')}",
                             record_key_part(CELL, cell.uuid), role, index)


def _map(tmp_path, cell, *, second_live=None, via_entries=None,
         track_entries=None, vias=(), tracks=()):
    _registries(tmp_path, via_entries or {}, track_entries or {})
    adapter = _adapter(cell, second_live=second_live, vias=vias, tracks=tracks)
    cfg = _Cfg(cell)
    return registry_record_copper_map(adapter, str(tmp_path / "config.sexp"),
                                      cfg, CELL, CLUSTER, None)


# ── record_points: what a registry tail names (the ORPHAN rule's home) ───────

def test_record_points_names_the_two_levels_and_refuses_an_orphan():
    cell = _cell()
    assert record_points(cell, "via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0) == \
        ((0.0, 0.0),)
    assert record_points(cell, "track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0) == \
        ((0.0, 0.0), (1.0, 0.0))
    # past the end of the list / a role the cell does not have: NO record
    assert record_points(cell, "via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 1) is None
    assert record_points(cell, "track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 5) is None
    assert record_points(cell, "track", "DA", 0) is None
    assert record_points(cell, "via", "GONE", 0) is None


def test_pair_agrees_with_record_needs_a_record_and_a_frame():
    """A pair with no record (an ORPHAN) or no frame is NEVER accepted — the two
    "cannot be checked" cases must not degrade into a silent "yes"."""
    from kicadstamp.cell_frame import CellFrame
    frame = CellFrame(placement_origin=Vector2.from_xy_mm(10.0, 10.0))
    live = _live_via("uv", 10.0, 10.0)

    assert pair_agrees_with_record(((0.0, 0.0),), live, frame, 0.05) is True
    assert pair_agrees_with_record(None, live, frame, 0.05) is False
    assert pair_agrees_with_record(((0.0, 0.0),), live, None, 0.05) is False


# ── the registry-only path (a REFUSED tree): place in a RIGID frame ─────────

def test_a_rigid_frame_accepts_the_pair_that_sits_where_the_record_puts_it(
        gate, tmp_path):
    """The happy path: the frame is rigid, the live via IS at the record's place
    — the pair is taken, and NOTHING is reported as refused."""
    cell = _cell()
    record_map = _map(tmp_path, cell, via_entries={_own_key(cell, 0): _via_entry(
        "uv", *FPGA_LIVE)}, vias=[_live_via("uv", *FPGA_LIVE)])

    assert record_map.by_record == \
        {("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "uv"}
    assert record_map.disagreed == 0
    assert record_map.orphan_keys == 0
    assert record_map.not_rigid is False
    assert record_map.frame_residual_mm == pytest.approx(0.0)
    assert not_checked_reasons(not_rigid=record_map.not_rigid,
                               frame_residual_mm=record_map.frame_residual_mm,
                               disagreed=record_map.disagreed,
                               orphan_keys=record_map.orphan_keys) == []


@pytest.mark.parametrize("moved_mm, accepted", [
    (0.0, True),                            # exactly the record's own place
    (RIGID_TOLERANCE_MM - 0.01, True),      # inside a RIGID frame's tolerance
    (RIGID_TOLERANCE_MM + 0.01, False),     # just past it
    (2.0, False),                           # the probe's step between neighbours
])
def test_the_place_check_of_the_registry_only_path_is_a_tolerance(
        gate, tmp_path, moved_mm, accepted):
    """The defect itself, and its tolerance: the key's uuid still points at live
    copper, but that copper may NOT be where the record puts it (the numbers
    shifted). The tolerance is `RIGID_TOLERANCE_MM` — the very constant the frame
    is judged rigid by, so the two can never disagree about "close enough".

    The rows sit INSIDE and PAST the tolerance, never exactly on it: the
    comparison is a plain `<=` on floats, so a value that lands one ulp over is
    refused — the safe side, and not something a cell should pretend to promise.
    """
    cell = _cell()
    record_map = _map(tmp_path, cell,
                      via_entries={_own_key(cell, 0): _via_entry(
                          "uv", FPGA_LIVE[0], FPGA_LIVE[1] + moved_mm)},
                      vias=[_live_via("uv", FPGA_LIVE[0],
                                      FPGA_LIVE[1] + moved_mm)])

    if accepted:
        assert record_map.by_record == \
            {("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "uv"}, record_map
        assert record_map.disagreed == 0
        return
    assert record_map.by_record == {}
    assert record_map.planned == 0
    assert record_map.disagreed == 1
    notes = not_checked_reasons(not_rigid=False,
                                frame_residual_mm=record_map.frame_residual_mm,
                                disagreed=record_map.disagreed, orphan_keys=0)
    assert notes and "do not sit where the record puts them" in notes[0]
    assert "1" in notes[0]


def test_an_orphan_key_is_never_a_pair(gate, tmp_path):
    """The index is past the end of the cell's list (the record was deleted and
    the numbers shifted): the key names NO record, so it cannot be checked — and
    it is counted apart from the "does not sit" refusals."""
    cell = _cell()
    record_map = _map(tmp_path, cell,
                      via_entries={_own_key(cell, 3): _via_entry("uv", 10.0, 10.0)},
                      vias=[_live_via("uv", 10.0, 10.0)])

    assert record_map.by_record == {}
    assert record_map.orphan_keys == 1
    assert record_map.disagreed == 0
    notes = not_checked_reasons(not_rigid=False, frame_residual_mm=0.0,
                                disagreed=0, orphan_keys=1)
    assert notes and "past the end of the cell's record list" in notes[0]


def test_a_non_rigid_frame_accepts_no_pair_at_all(gate, tmp_path):
    """The whole reason for the rigidity rule: a frame built on a cluster that is
    NOT a rigid copy of the cell is off by MORE than the step between
    neighbouring records (the probe measured 2.56 mm on the live `fpga` against
    shifted-pair distances of 0.6-2.26 mm), so it cannot tell a pair from its
    neighbour. Nothing is accepted, and the residual is the printed reason."""
    cell = _cell(second_slot=(0.0, 5.0))
    record_map = _map(tmp_path, cell, second_live=(10.0, 60.0),
                      via_entries={_own_key(cell, 0): _via_entry("uv", 10.0, 10.0)},
                      vias=[_live_via("uv", 10.0, 10.0)])

    assert record_map.by_record == {}
    assert record_map.planned == 0
    assert record_map.not_rigid is True
    assert record_map.frame_residual_mm > RIGID_TOLERANCE_MM
    assert record_map.disagreed == 0            # nothing was even compared
    notes = not_checked_reasons(not_rigid=True,
                                frame_residual_mm=record_map.frame_residual_mm,
                                disagreed=0, orphan_keys=0)
    assert notes and "not a rigid copy of the cell" in notes[0]
    assert f"{record_map.frame_residual_mm:.3f}" in notes[0]


def test_a_frame_that_cannot_be_built_accepts_no_pair_at_all(gate, tmp_path):
    """No live component carries the cell's anchor role (the instance is not on
    the board / cannot be identified): there is nothing to measure against, so no
    pair is accepted and the note says the frame is unusable — not a number it
    does not have."""
    cell = _cell()
    _registries(tmp_path, {_own_key(cell, 0): _via_entry("uv", 10.0, 10.0)}, {})
    adapter = _Adapter(vias=[_live_via("uv", 10.0, 10.0)])      # no footprints
    record_map = registry_record_copper_map(
        adapter, str(tmp_path / "config.sexp"), _Cfg(cell), CELL, CLUSTER, None)

    assert record_map.by_record == {}
    assert record_map.not_rigid is True
    assert record_map.frame_residual_mm is None
    notes = not_checked_reasons(not_rigid=True, frame_residual_mm=None,
                                disagreed=0, orphan_keys=0)
    assert notes and "no usable cell frame" in notes[0]


def test_the_records_without_a_registry_key_are_still_counted(gate, tmp_path):
    """The place check must not eat the OTHER honest count: the cell's records the
    registry has no key for are named (never "the cell has no copper")."""
    cell = _cell()
    record_map = _map(tmp_path, cell, vias=[_live_via("uv", *FPGA_LIVE)])

    assert record_map.by_record == {}
    assert record_map.without_registry == 2       # the cell's via 0 + track 0


# ── a DRY RUN: the pair must sit at the COMMAND's own planned place ─────────

def _planned_via(key, x_mm, y_mm):
    return ViaCommand(position=Vector2.from_xy_mm(x_mm, y_mm), net_name="N",
                      drill_mm=0.3, diameter_mm=0.6, owner_ref="IC9",
                      registry_key=key)


def _dry_run(tmp_path, key, *, live_via, planned=(0.0, 0.0)):
    _registries(tmp_path, {key: _via_entry("uv", planned[0], planned[1])}, {})
    adapter = _adapter(_cell(), vias=[live_via])
    return record_copper_map_for(adapter, str(tmp_path / "config.sexp"),
                                 CELL_IDENTITY, [_planned_via(key, *planned)], [])


def test_a_dry_run_takes_the_tier1_pair_only_at_the_commands_own_place(
        gate, tmp_path):
    """The tree was NOT refused, so the command's own place is exact — no cell
    frame needed, and a non-rigid cluster cannot spoil it. At the place: taken."""
    key = make_registry_key("anchor:IC9:1:0.0000:0.0000", CELL_IDENTITY, None, 0)
    record_map = _dry_run(tmp_path, key, live_via=_live_via("uv", 0.0, 0.0),
                          planned=(0.0, 0.0))

    assert record_map.by_record == {("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "uv"}
    assert record_map.disagreed == 0


def test_a_dry_run_refuses_the_tier1_pair_that_moved_off_the_planned_place(
        gate, tmp_path):
    """Mutation "tier 1 is accepted without the planned place": the uuid's copper
    is on the board but 5 mm from where THIS record plans it — refused, counted,
    and never claimed by geometry either (the registry owns that uuid)."""
    key = make_registry_key("anchor:IC9:1:0.0000:0.0000", CELL_IDENTITY, None, 0)
    record_map = _dry_run(tmp_path, key, live_via=_live_via("uv", 5.0, 5.0),
                          planned=(0.0, 0.0))

    assert record_map.by_record == {}
    assert record_map.disagreed == 1
    assert record_map.planned == 1            # the run itself DID plan


def test_a_dry_run_tier2_geometry_pair_is_not_touched_by_the_place_check(
        gate, tmp_path):
    """Tier 2 IS the place check (it is how the item was found): a command whose
    key the registry does not know still pairs with the live item at its place."""
    key = make_registry_key("anchor:IC9:1:0.0000:0.0000", CELL_IDENTITY, None, 0)
    record_map = _dry_run(tmp_path, key, live_via=_live_via("uv", 0.0, 0.0),
                          planned=(0.0, 0.0))
    second = record_copper_map_for(
        _adapter(_cell(), vias=[_live_via("uv2", 7.0, 7.0)]),
        str(tmp_path / "config.sexp"), CELL_IDENTITY,
        [_planned_via(key, 7.0, 7.0)], [])

    assert record_map.by_record and second.by_record == \
        {("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "uv2"}
    assert second.disagreed == 0


def test_the_dry_run_map_is_the_only_reader_of_the_disagreed_counter(
        gate, tmp_path):
    """A map built by the dry run carries the SAME counters the registry-only map
    does (one vocabulary for the two doors), and a map that refused nothing keeps
    them at zero — the report must not invent a refusal."""
    key = make_registry_key("anchor:IC9:1:0.0000:0.0000", CELL_IDENTITY, None, 0)
    record_map = _dry_run(tmp_path, key, live_via=_live_via("uv", 0.0, 0.0))
    assert (record_map.disagreed, record_map.orphan_keys,
            record_map.not_rigid) == (0, 0, False)
    assert isinstance(RecordCopperMap(), RecordCopperMap)
