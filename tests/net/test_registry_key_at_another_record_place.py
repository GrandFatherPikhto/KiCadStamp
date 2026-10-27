# tests/net/test_registry_key_at_another_record_place.py
"""A registry number that holds a NEIGHBOUR's copper is a shifted number at ANY net.

Ч1 of plan_2026_10_27_registry_key_at_another_record_place (Denis, 27.10: «the
proof is the PLACE, not the net»), the case the «another net» rule of
plan_2026_10_26_select_cell_shifted_registry_pair leaves behind. The live defect on
v103: `ldo_adj_n2v5` / `LDO_ADJ_N2V5` / `Power` — key `track|4` holds record `|2`'s
GND track, `track|5` holds `|1`'s, `via|1` holds `via|5`'s, so «Select cell» loses
exactly the copper nobody claims.

The fixture is that ring in miniature: a cell of cell-level track records at
(0,0)->(1,0), (0,2)->(1,2), (0,4)->(1,4), all on ONE net, a board carrying all of
them at their places, and a registry whose keys point ONE RECORD TO THE RIGHT. The
defect: every key holds a live uuid, so today's pass skips every entry «reconcile
will move it», and record |0's own copper is held by nobody (the trail).

The cells below pin the rule (§8.8 of the plan), the companion that drops a released
key whose record found no copper, and the Redraw invariant «a tail, not a loss».
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from kicadstamp.absent_copper_prune import (at_another_record_place, record_of,
                                            registry_record_copper_map)
from kicadstamp.adopt_at_current_place import adopt_cell_copper_at_current_place
from kicadstamp.config import ClonePlacement, format_version
from kicadstamp.constants import CELL_LEVEL_ROLE_PLACEHOLDER, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.placement.services.clone_position_calculator import (
    clone_registry_identity,
)
from kicadstamp.registry import (PlacementRegistry, TrackRegistry,
                                 make_registry_key)
from kicadstamp.registry_match import (PAIR_AWAY, PAIR_SHIFTED,
                                       PlannedCopperMatch, at_other_command_place,
                                       pair_is_shifted, pair_verdict,
                                       record_accepts_net, shifted_entries)
from tests.fakes.format3 import det_uuid, registry_schema

CELL = "dac_buf"
CLUSTER = "DAC_BUF"
#: Where the live FPGA slot sits: every record of the cell then lands at
#: `(LIVE_X, LIVE_Y) + its own stored offset` in the cell frame.
LIVE = (10.0, 10.0)
NET = "GND"


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


# ── the rig (the same shapes the at-current-place cells drive) ───────────────

class _Adapter:
    def __init__(self, footprints=(), vias=(), tracks=()):
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        return fp.role if name == ROLE_FIELD_NAME else fp.cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((fp for fp in self._footprints if fp.ref == ref), None)

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


def _live_track(uuid, x1, y1, x2, y2, net=NET, width=0.25):
    return Track(uuid=uuid, net_name=net, start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2), width_mm=width,
                 layer=BoardLayer.BL_F_Cu)


def _live_via(uuid, x_mm, y_mm, net=NET):
    return Via(uuid=uuid, net_name=net, position=Vector2.from_xy_mm(x_mm, y_mm),
               drill_mm=0.3, diameter_mm=0.6)


def _record(start, end, net=NET, layer=None, width=0.25):
    return SimpleNamespace(start_along_mm=start[0], start_across_mm=start[1],
                           end_along_mm=end[0], end_across_mm=end[1],
                           width_mm=width, layer=layer, net=net)


def _cell(tracks=None, vias=None, second_slot=None):
    """A cell of cell-level tracks/vias whose only role FPGA stands at (0,0)."""
    comps = [SimpleNamespace(role="FPGA", offset_along_mm=0.0,
                             offset_across_mm=0.0, angle_deg=0.0,
                             net_template=None, vias=[])]
    if second_slot is not None:
        comps.append(SimpleNamespace(role="DA", offset_along_mm=second_slot[0],
                                     offset_across_mm=second_slot[1],
                                     angle_deg=0.0, net_template=None, vias=[]))
    return SimpleNamespace(uuid=det_uuid(f"cells:{CELL}"), anchor_role="FPGA",
                           anchor_xy=None, vias=vias or [], tracks=tracks or [],
                           components=comps)


def _ring(track):                              # records |0 |1 |2 at (0,0),(0,2),(0,4)
    return _cell(tracks=[_record((0.0, y), (1.0, y)) for y in track])


def _clone(xy=(0.0, 0.0), name="dac0"):
    return ClonePlacement(cluster=CLUSTER, cell=CELL, xy=xy, name=name,
                          uuid=det_uuid(f"clone_placements:{name}"), nets={})


def _cfg(cell):
    return SimpleNamespace(cells={CELL: cell}, entities=[], clone_placements=[])


def _items(*clones):
    return [SimpleNamespace(kind="clone", obj=clone) for clone in clones]


def _adapter(*, live=LIVE, vias=(), tracks=(), second_live=None):
    fps = [_Fp("IC9", "FPGA", CLUSTER, *live)]
    if second_live is not None:
        fps.append(_Fp("R1", "DA", CLUSTER, *second_live))
    return _Adapter(footprints=fps, vias=list(vias), tracks=list(tracks))


def _track_entry(uuid, x1, y1, x2, y2, net=NET):
    return {"uuid": uuid, "start_x_mm": x1, "start_y_mm": y1, "end_x_mm": x2,
            "end_y_mm": y2, "width_mm": 0.25, "net": net, "layer": "F.Cu"}


def _via_entry(uuid, x_mm, y_mm, net=NET):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": net,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _registries(adapter, tmp_path, via_entries=None, track_entries=None):
    via_path = tmp_path / "registry" / "config.registry.json"
    trk_path = tmp_path / "tracks" / "config.tracks.registry.json"
    for path, entries in ((via_path, via_entries or {}),
                          (trk_path, track_entries or {})):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": registry_schema(),
                                    **entries}), encoding="utf-8")
    return (PlacementRegistry(adapter, str(via_path)),
            TrackRegistry(adapter, str(trk_path)))


def _key(kind, index, clone=None, cell=None, role=None):
    """The key the planner/adoption build — the SAME builder."""
    anchor_id, _cell_name, key_part = clone_registry_identity(
        clone or _clone(), (cell and {CELL: cell}) or {CELL: _cell()})
    return make_registry_key(anchor_id, key_part, role, index)


def _select_key(cell, index):
    """A key the REGISTRY-ONLY door calls its own: the physical ``anchor:`` prefix a
    cell bears when its instance is anchored on a component (`is_own_key` reads the
    ref out of it), with this cell's own identity as the template part."""
    from kicadstamp.registry import record_key_part

    return make_registry_key("anchor:IC9:1:0.0000:0.0000",
                             record_key_part(CELL, cell.uuid),
                             CELL_LEVEL_ROLE_PLACEHOLDER, index)


def _cmd(index, y, *, cell=None, clone=None, net=NET, x=LIVE[0], dy=LIVE[1]):
    start = (x, dy + y)
    return TrackCommand(start=Vector2.from_xy_mm(*start),
                        end=Vector2.from_xy_mm(x + 1.0, dy + y),
                        width_mm=0.25, net_name=net,
                        layer=BoardLayer.BL_F_Cu, owner_ref="IC9",
                        registry_key=_key("track", index, clone, cell))


def _run(adapter, cell, clone, tmp_path, *, write=True, via_entries=None,
         track_entries=None):
    via_reg, trk_reg = _registries(adapter, tmp_path, via_entries, track_entries)
    report = adopt_cell_copper_at_current_place(
        adapter, _cfg(cell), _items(clone), via_reg, trk_reg, write=write)
    return report, via_reg, trk_reg


def _owners(reg) -> dict:
    """uuid -> how many entries of `reg` hold it. A uuid must never be twice."""
    counts: dict = {}
    for entry in reg.entries.values():
        counts[entry.uuid] = counts.get(entry.uuid, 0) + 1
    return counts


# ── cell 1: the ring — keys one record to the right, all on ONE net ──────────

def test_the_ring_is_released_and_every_record_finds_its_own_copper(gate, tmp_path):
    """Keys |0→t1, |1→t2, |2→(dead). Every item is live, every net is the same, so
    the old rule skipped every entry — and record |0's own track (t0) belonged to
    NOBODY. The place decides now: |0's item stands at |1's place, |1's at |2's, and
    both keys are released; all three records are bound to their own copper."""
    cell = _ring((0.0, 2.0, 4.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.0, 11.0, 10.0),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0),
                               _live_track("t2", 10.0, 14.0, 11.0, 14.0)])
    k0, k1, k2 = (_key("track", i, clone, cell) for i in (0, 1, 2))

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("t2", 10.0, 14.0, 11.0, 14.0),
                       k2: _track_entry("dead", 10.0, 14.0, 11.0, 14.0)})

    assert report.shifted == 2, "only the two keys holding a neighbour's copper"
    assert report.adopted == 3, "all three records bound, t0 included (the trail)"
    assert report.dropped == 0, "every record found copper, so no key is dropped"
    assert {trk_reg.entries[k].uuid for k in (k0, k1, k2)} == {"t0", "t1", "t2"}
    assert _owners(trk_reg) == {"t0": 1, "t1": 1, "t2": 1}
    assert trk_reg.entries[k0].uuid == "t0" and trk_reg.entries[k1].uuid == "t1"


# ── cell 2: the same rotation at the live size (18 records, one step) ────────

def test_a_full_rotation_of_eighteen_records_is_repaired(gate, tmp_path):
    """The shape measured on v103's three `dac_buf` channels: `|k` holds the copper
    of record `|k-1`, 18 to a chain. Every released key is rebound to its own
    copper and the rotation leaves no item owned twice."""
    count = 18
    ys = [2.0 * i for i in range(count)]
    cell = _ring(ys)
    clone = _clone()
    tracks = [_live_track(f"t{i}", 10.0, 10.0 + y, 11.0, 10.0 + y)
              for i, y in enumerate(ys)]
    keys = [_key("track", i, clone, cell) for i in range(count)]
    entries = {keys[i]: _track_entry(f"t{(i - 1) % count}", 10.0,
                                     10.0 + ys[(i - 1) % count], 11.0,
                                     10.0 + ys[(i - 1) % count])
               for i in range(count)}

    report, _, trk_reg = _run(_adapter(tracks=tracks), cell, clone, tmp_path,
                              track_entries=entries)

    assert report.shifted == count
    assert report.adopted == count
    assert all(trk_reg.entries[keys[i]].uuid == f"t{i}" for i in range(count))
    assert set(_owners(trk_reg).values()) == {1}


def test_the_registry_only_door_drops_a_neighbour_place_pair(gate, tmp_path):
    """The SAME rotation through the REGISTRY-ONLY door (`place_check=False`): both
    keys are dropped from the map and counted as shifted — this door used to carry
    its own copy of the net comparison, which is exactly how it could keep a pair the
    other doors free."""
    cell = _ring((0.0, 2.0, 4.0))
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.0, 11.0, 10.0),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0),
                               _live_track("t2", 10.0, 14.0, 11.0, 14.0)])
    k0, k1, k2 = (_select_key(cell, i) for i in (0, 1, 2))
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    _registries(adapter, tmp_path,
                track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                               k1: _track_entry("t2", 10.0, 14.0, 11.0, 14.0),
                               k2: _track_entry("t0", 10.0, 10.0, 11.0, 10.0)})

    record_map = registry_record_copper_map(
        adapter, str(config_path), _cfg(cell), CELL, CLUSTER, None,
        own_refs=["IC9"], sheet_names={}, place_check=False)

    assert record_map.shifted == 3, "every key holds a neighbour's copper"
    assert record_map.by_record == {}


# ── cell 3: away, same net, NOBODY's place — the operator moved it ───────────

def test_a_pair_away_on_its_own_net_on_nobodys_place_keeps_its_key(gate, tmp_path):
    """The copper sits 2 mm off the ring, on the record's own net and at NO
    record's place: that is the operator having moved it, not a shifted number.
    The key keeps ownership — exactly as before this rule."""
    cell = _ring((0.0, 2.0, 4.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.5, 11.0, 10.5),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    k0 = _key("track", 0, clone, cell)

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t0", 10.0, 10.5, 11.0, 10.5)})

    assert report.shifted == 0
    assert trk_reg.entries[k0].uuid == "t0"      # ownership unchanged
    assert report.dropped == 0


def test_away_on_another_net_at_nobodys_place_is_still_shifted(gate, tmp_path):
    """The NET-only half of the rule, standing alone: the item is away from its own
    record, on ANOTHER net, and at NO record's place — the key is released with no
    witness at all. Pinned because the place half would otherwise hide this one (the
    Ч0 probe measured 6 such pairs on `ldo_adj_n2v5` alone)."""
    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t_x", 10.0, 15.5, 11.0, 15.5,
                                           net="OTHER")])
    k0 = _key("track", 0, clone, cell)

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t_x", 10.0, 15.5, 11.0, 15.5,
                                        net="OTHER")})

    assert report.shifted == 1
    assert report.adopted == 0, "record |0 has no copper on the board"
    assert report.dropped == 1, "the released key is dropped, not left behind"
    assert k0 not in trk_reg.entries


# ── cell 4: at a neighbour's place, but the neighbour wants ANOTHER net ──────

def test_a_neighbour_with_another_net_does_not_release_the_key(gate, tmp_path):
    """The item stands at record |1's place, but |1 is declared on another net — the
    item is not that record's copper, so this path keeps its hands off (the
    pre-existing «another net» rule judges the pair, and here the own net agrees,
    so the key keeps ownership)."""
    cell = _cell(tracks=[_record((0.0, 0.0), (1.0, 0.0)),
                         _record((0.0, 2.0), (1.0, 2.0), net="OTHER")])
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t_x", 10.0, 12.0, 11.0, 12.0, net=NET)])
    k0 = _key("track", 0, clone, cell)

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t_x", 10.0, 12.0, 11.0, 12.0)})

    assert report.shifted == 0
    assert trk_reg.entries[k0].uuid == "t_x"


# ── cell 5: a neighbour whose net cannot be named is not a witness ───────────

def test_record_accepts_net_demands_a_named_net():
    """Three outcomes, one permission: only a NAMED and RESOLVED net of the
    neighbour that equals the item's releases anything. Silence is not permission —
    it is the absence of proof, the opposite of `same_net`'s leniency."""
    item = SimpleNamespace(net_name="GND")

    assert record_accepts_net(item, declared=True, expected="GND")
    assert not record_accepts_net(item, declared=True, expected="OTHER")
    assert not record_accepts_net(item, declared=False, expected=None)
    assert not record_accepts_net(item, declared=True, expected=None)


def test_a_neighbour_without_a_declared_net_is_not_a_witness(gate, tmp_path):
    """Record |1 names no net at all: we cannot say the item is |1's copper, so key
    |0 is NOT released by the place — and the pair is not a shifted number either."""
    cell = _cell(tracks=[_record((0.0, 0.0), (1.0, 0.0)), _record((0.0, 2.0),
                                                                (1.0, 2.0),
                                                                net=None)])
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t_x", 10.0, 12.0, 11.0, 12.0)])
    k0 = _key("track", 0, clone, cell)

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t_x", 10.0, 12.0, 11.0, 12.0)})

    assert report.shifted == 0
    assert trk_reg.entries[k0].uuid == "t_x"


# ── cell 6: another KIND is never a witness (and never an IndexError) ────────

def test_a_record_of_another_kind_is_never_a_witness(gate, tmp_path):
    """A live VIA has one point, a track two: asking a via's item about a track's
    place compared a 1-tuple with a 2-tuple and crashed the Ч0 probe with
    IndexError. The kind is pinned — the product never pairs across kinds."""
    from kicadstamp.cell_geometry_refresh import cell_frame_from_live
    from kicadstamp.cell_origin import cell_slot_dicts

    cell = _cell(tracks=[_record((0.0, 0.0), (1.0, 0.0))],
                 vias=[SimpleNamespace(offset_along_mm=0.0,
                                       offset_across_mm=0.0)])
    adapter = _adapter(vias=[_live_via("v0", *LIVE)])
    frame = cell_frame_from_live(cell_slot_dicts(cell),
                                 list(adapter.get_footprints()), adapter,
                                 origin_role="FPGA")
    track_at_origin = _live_track("t0", 10.0, 10.0, 11.0, 10.0)

    assert at_another_record_place(cell, "via", CELL_LEVEL_ROLE_PLACEHOLDER, 0,
                                  track_at_origin, frame) is None
    # …while the SAME kind finds the neighbour it really stands at: the track record
    # |0 is where this live track lies, for every record index but its own.
    assert at_another_record_place(cell, "track", CELL_LEVEL_ROLE_PLACEHOLDER, 1,
                                  track_at_origin, frame) == (
        "track", CELL_LEVEL_ROLE_PLACEHOLDER, 0)


# ── cell 7: the companion — a released key with no copper of its own goes ────

def test_a_released_key_whose_record_found_nothing_is_dropped(gate, tmp_path):
    """Key |0 holds t1 (record |1's copper) and record |0's own copper is GONE. The
    key is released but rebinds nothing, so it keeps a foreign uuid — and a Redraw
    would delete that uuid under the stale key while its real owner counted it «in
    place». The key is DROPPED through the registry's own writer."""
    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    k0, k1 = (_key("track", i, clone, cell) for i in (0, 1))

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    assert report.shifted == 1
    assert report.dropped == 1
    assert report.dropped_keys == (("track", k0),)
    assert k0 not in trk_reg.entries, "the stale key must not stay"
    assert trk_reg.entries[k1].uuid == "t1", "the real owner took its copper"


def test_a_dry_run_counts_the_drop_and_writes_nothing(gate, tmp_path):
    """Same board, `write=False`: everything is decided and counted, the files are
    untouched — the key is still there and `report.dropped` already says 1."""
    from kicadstamp.adopt_at_current_place import dry_run_adoption

    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    k0, k1 = (_key("track", i, clone, cell) for i in (0, 1))
    via_reg, trk_reg = _registries(
        adapter, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    report, lines = dry_run_adoption(adapter, _cfg(cell), _items(clone),
                                     via_reg, trk_reg)

    assert report.dropped == 1
    assert k0 in trk_reg.entries, "a dry run writes nothing"
    assert any("would drop 1 released key(s)" in line for line in lines)


def test_a_released_key_with_an_ambiguous_claim_is_still_dropped(gate, tmp_path):
    """Д-1 of the Ч1 acceptance: «found its own copper» must mean BOUND, not CLAIMED.

    Records |0 (y=0) and |2 (y=0 again — a duplicate place), record |1 at y=2; the
    board carries t0 at |0/|2's place and t1 at |1's. Key |0 holds t1 (record |1's
    copper) and key |1 holds a dead uuid, so |0 is released and then CLAIMS t0 — but
    |2 claims the same t0, so `uuid_to_keys` sees two claimants and NOBODY is bound.
    A companion that asked the CLAIMS would leave key |0 in place holding t1 while
    record |1 binds t1 — two keys on one item, and `reconcile` would delete the very
    copper `|1` counts as «in place». Asked by the BINDINGS, |0 is dropped.
    """
    cell = _cell(tracks=[_record((0.0, 0.0), (1.0, 0.0)),
                         _record((0.0, 2.0), (1.0, 2.0)),
                         _record((0.0, 0.0), (1.0, 0.0))])
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.0, 11.0, 10.0),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    k0, k1, k2 = (_key("track", i, clone, cell) for i in (0, 1, 2))

    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    assert report.ambiguous == 1, "records |0 and |2 both claim t0 — nobody takes it"
    assert report.shifted == 1
    assert report.dropped == 1, "an ambiguous claim is not copper of its own"
    assert k0 not in trk_reg.entries
    assert trk_reg.entries[k1].uuid == "t1"
    assert _owners(trk_reg) == {"t1": 1}, "t1 must never be held by two keys"
    assert k2 not in trk_reg.entries


# ── cell 8: Redraw on the ring — a TAIL, not a loss ─────────────────────────

def test_redraw_leaves_no_nobodys_copper_behind(gate, tmp_path):
    """The invariant Denis wrote for cell 8: after the pass and `reconcile` on an
    instance that MOVES (Δ ≠ 0), (а) no live item of the instance is left without
    an owner — the trail of the live defect (`3ef337ff`) is gone; (б) no uuid is
    held by two keys; (в) `to_create` holds exactly one command per record. The
    deletions themselves are the legitimate move of the instance, not a loss."""
    cell = _ring((0.0, 2.0, 4.0))
    # The instance stands 3.46 mm to the right of where the clone puts it.
    clone = _clone(xy=(LIVE[0] - 3.46, LIVE[1]))
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.0, 11.0, 10.0),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0),
                               _live_track("t2", 10.0, 14.0, 11.0, 14.0)])
    k0, k1, k2 = (_key("track", i, clone, cell) for i in (0, 1, 2))
    report, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("t2", 10.0, 14.0, 11.0, 14.0),
                       k2: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    # The plan is where the CLONE puts the instance; the copper is 3.46 mm away.
    commands = [_cmd(i, 2.0 * i, cell=cell, clone=clone, x=clone.xy[0])
                for i in (0, 1, 2)]
    # Ownership is read BEFORE reconcile: reconcile deletes the entries it replaces.
    owned = _owners(trk_reg)
    to_create, to_delete = trk_reg.reconcile(commands, known_anchor_ids=None)

    assert report.adopted == 3, "every record of the instance is bound"
    assert set(owned) == {"t0", "t1", "t2"}, "no live item of the instance is a tail"
    assert set(owned.values()) == {1}, "no uuid is held by two keys"
    assert sorted(to_delete) == ["t0", "t1", "t2"], \
        "the whole instance moves, so all of its copper is legitimately replaced"
    assert len(to_create) == 3, "one command per record, the owner's included"


# ── cell 9: no place vocabulary — the rule stays silent ─────────────────────

def test_without_a_frame_nothing_is_released(gate, tmp_path):
    """`frame_check=False` (the internode door) has no cell frame, so «another
    record's place» cannot be asked at all: the away pair is kept and the counter
    stays at zero — «we could not check», never a guess."""
    cell = _ring((0.0, 2.0))
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    k0 = _select_key(cell, 0)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    _registries(adapter, tmp_path,
                track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0)})

    record_map = registry_record_copper_map(
        adapter, str(config_path), _cfg(cell), CELL, CLUSTER, None,
        own_refs=["IC9"], sheet_names={}, place_check=False,
        frame_check=False)

    assert record_map.shifted == 0
    assert record_map.by_record[("track", CELL_LEVEL_ROLE_PLACEHOLDER, 0)] == "t1"


# ── cell 10: `adopt_matching_unowned` is inert by construction ──────────────

def test_adopt_matching_unowned_has_no_candidates_on_a_known_rotation(tmp_path, gate):
    """This door is handed only the commands whose key the registry does NOT know.
    On a rotation every key IS known, so the call has no candidates at all and the
    rotation is repaired by the at-current-place pass — not here. Pinned so nobody
    expects the effect from this door (its blindness is documented, not «fixed»)."""
    from kicadstamp.registry import adopt_matching_unowned

    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    commands = [_cmd(0, 0.0, cell=cell, clone=clone),
                _cmd(1, 2.0, cell=cell, clone=clone)]
    k0, k1 = (_key("track", i, clone, cell) for i in (0, 1))
    _, trk_reg = _registries(
        adapter, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    assert adopt_matching_unowned(trk_reg, commands) == 0
    assert {key: entry.uuid for key, entry in trk_reg.entries.items()} == {
        k0: "t1", k1: "dead"}


# ── cell 11: the instance at its planned place — nothing moves, nothing appears ─

def test_at_the_planned_place_the_rotation_moves_nothing(gate, tmp_path):
    """Δ = 0: after the pass with `write=True` the keys hold their own copper, so
    `reconcile` deletes nothing and creates nothing — the whole rotation is repaired
    by ownership alone."""
    cell = _ring((0.0, 2.0, 4.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t0", 10.0, 10.0, 11.0, 10.0),
                               _live_track("t1", 10.0, 12.0, 11.0, 12.0),
                               _live_track("t2", 10.0, 14.0, 11.0, 14.0)])
    k0, k1, k2 = (_key("track", i, clone, cell) for i in (0, 1, 2))
    _, _, trk_reg = _run(
        adapter, cell, clone, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0),
                       k1: _track_entry("t2", 10.0, 14.0, 11.0, 14.0),
                       k2: _track_entry("dead", 9.0, 9.0, 9.0, 9.0)})

    to_create, to_delete = trk_reg.reconcile(
        [_cmd(i, 2.0 * i, cell=cell, clone=clone) for i in (0, 1, 2)],
        known_anchor_ids=None)

    assert to_delete == []
    assert to_create == []


# ── the rule itself ─────────────────────────────────────────────────────────

def test_pair_is_shifted_puts_the_place_first():
    """The rule in four lines, asked directly: at the place — never shifted, whatever
    the neighbour says; away at a neighbour's place — shifted even on the same net;
    away on another net — shifted; away on nobody's place on its own net — away."""
    assert not pair_is_shifted(at_place=True, other_record_place=True,
                               net_agrees=False)
    assert pair_is_shifted(at_place=False, other_record_place=True, net_agrees=True)
    assert pair_is_shifted(at_place=False, other_record_place=False, net_agrees=False)
    assert not pair_is_shifted(at_place=False, other_record_place=False,
                               net_agrees=True)


def test_the_command_door_sees_a_sibling_place_at_zero_distance(gate, tmp_path):
    """The COMMAND vocabulary: key |0's item stands exactly where command |1 plans
    its copper, on the same net — `at_other_command_place` names it, the verdict is
    SHIFTED and the key is freed. This is the `dac_buf` shape of v103 (54 pairs at
    0.0000 mm), and it works only while the instance is at its planned place."""
    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    commands = [_cmd(0, 0.0, cell=cell, clone=clone),
                _cmd(1, 2.0, cell=cell, clone=clone)]
    k0, k1 = (_key("track", i, clone, cell) for i in (0, 1))
    _, trk_reg = _registries(
        adapter, tmp_path,
        track_entries={k0: _track_entry("t1", 10.0, 12.0, 11.0, 12.0)})

    witnesses = at_other_command_place(adapter.get_tracks()[0], commands[0],
                                       commands)
    assert [w.registry_key for w in witnesses] == [k1]
    assert pair_verdict(PlannedCopperMatch(command=commands[0],
                                           live=adapter.get_tracks()[0],
                                           tier="registry"), siblings=commands) \
        == PAIR_SHIFTED
    assert shifted_entries(trk_reg, commands,
                           live_items=adapter.get_tracks()) == {k0: "t1"}


def test_a_silent_sibling_command_is_not_a_witness(gate, tmp_path):
    """A sibling command that names NO net cannot accept the item's net, so it does
    not release anything: away stays «away» (Д-1, carried into this rule)."""
    cell = _ring((0.0, 2.0))
    clone = _clone()
    adapter = _adapter(tracks=[_live_track("t1", 10.0, 12.0, 11.0, 12.0)])
    commands = [_cmd(0, 0.0, cell=cell, clone=clone),
                _cmd(1, 2.0, cell=cell, clone=clone, net=None)]

    assert at_other_command_place(adapter.get_tracks()[0], commands[0],
                                  commands) == []
    assert pair_verdict(PlannedCopperMatch(command=commands[0],
                                           live=adapter.get_tracks()[0],
                                           tier="registry"), siblings=commands) \
        == PAIR_AWAY


def test_a_sibling_of_another_kind_is_never_a_witness():
    """A via command and a track command share the key STRING (`…|role|index`) but
    never a place: the kind must match, or a via's single point would be compared
    with a track's two ends."""
    via = SimpleNamespace(net_name=NET, drill_mm=0.3, diameter_mm=0.6,
                          position=Vector2.from_xy_mm(10.0, 10.0),
                          registry_key="a|b|__spoke__|0")
    track_cmd = SimpleNamespace(net_name=NET, width_mm=0.25,
                                layer=BoardLayer.BL_F_Cu,
                                start=Vector2.from_xy_mm(10.0, 10.0),
                                end=Vector2.from_xy_mm(11.0, 10.0),
                                registry_key="a|b|__spoke__|1")
    live_via = SimpleNamespace(net_name=NET,
                               position=Vector2.from_xy_mm(10.0, 10.0))

    assert at_other_command_place(live_via, via, [via, track_cmd]) == []


def test_record_of_finds_the_neighbour_a_door_asks_about(gate):
    """`at_another_record_place` returns a SLOT, and the doors read that record's net
    through the shared `record_of`: the two must name the same record."""
    cell = _cell(tracks=[_record((0.0, 0.0), (1.0, 0.0)),
                         _record((0.0, 2.0), (1.0, 2.0), net="OTHER")])

    assert record_of(cell, "track", CELL_LEVEL_ROLE_PLACEHOLDER, 1).net == "OTHER"
