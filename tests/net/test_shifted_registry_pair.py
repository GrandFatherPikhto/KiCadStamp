# tests/net/test_shifted_registry_pair.py
"""A registry number that points at ANOTHER record's copper must not win.

Ч1 of plan_2026_10_26_select_cell_shifted_registry_pair, by the ОДОБРЕННОЕ rule
(«место решает первым», Denis 26.10):

  * an item AT the planned place is OWNERSHIP, whatever the width/layer says — the
    copper was edited on the board (34 of 45 pairs on v103);
  * an item AWAY from the place AND on another net is a SHIFTED NUMBER: the key is
    free and the item is nobody's claim (11 of 45, and the live defect among them);
  * away with the same net — today's behaviour, «selected anyway».

The fixture is the live defect in miniature: a cell with two tracks (record |0 on
net A, record |1 on net B), a board carrying BOTH at their planned places, and a
registry whose key |0 points at the net-B track (another net, another place). The
defect: key |0 took the net-B item, so record |0 never found its own copper and
record |1 could not take the item either.
"""
import json
from types import SimpleNamespace

import pytest

from kicadstamp.absent_copper_prune import _map_for, registry_record_copper_map
from kicadstamp.config import format_version
from kicadstamp.constants import (CELL_LEVEL_ROLE_PLACEHOLDER,
                                  CLUSTER_FIELD_NAME, ROLE_FIELD_NAME)
from kicadstamp.domain.board import Footprint, Track
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand
from kicadstamp.registry import (PlacementRegistry, TrackRegistry,
                                 make_registry_key, record_key_part)
from kicadstamp.registry_match import (PAIR_AGREES, PAIR_AWAY, PAIR_EDITED,
                                       PAIR_SHIFTED, pair_verdict,
                                       shifted_entries)
from tests.fakes.format3 import det_uuid, registry_schema


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


# ── doubles ───────────────────────────────────────────────────────────────

class _Rec:
    """One cell: its own copper records and ONE component slot at the origin."""

    def __init__(self, uuid, tracks, net_of_track):
        self.uuid = uuid
        self.vias = []
        self.tracks = [SimpleNamespace(start_along_mm=s, start_across_mm=sc,
                                       end_along_mm=e, end_across_mm=ec,
                                       width_mm=w, layer="B.Cu", net=net)
                       for (s, sc, e, ec, w), net in zip(tracks, net_of_track)]
        self.components = [SimpleNamespace(role="DA", offset_along_mm=0.0,
                                           offset_across_mm=0.0, vias=[])]
        self.anchor_role = None
        self.anchor_xy = None


class _Cfg:
    def __init__(self, cells):
        self.cells = cells
        self.entities = []
        self.clone_placements = []


class _Adapter:
    def __init__(self, fields, footprints=(), tracks=(), vias=()):
        self._fields = fields
        self._footprints = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)


def _fp(ref, role, cluster, x_mm=0.0, y_mm=0.0):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = ()
    return fp


def _live_track(uuid, start, end, net, width=0.2):
    return Track(uuid=uuid, start=Vector2.from_xy_mm(*start),
                 end=Vector2.from_xy_mm(*end), net_name=net, width_mm=width,
                 layer=BoardLayer.BL_B_Cu)


def _cmd(key, start, end, net, width=0.2):
    return TrackCommand(start=Vector2.from_xy_mm(*start),
                        end=Vector2.from_xy_mm(*end), width_mm=width,
                        net_name=net, layer=BoardLayer.BL_B_Cu, owner_ref="C1",
                        registry_key=key)


#: The two records of the cell: |0 on net A at (0,0)->(1,0), |1 on net B at
#: (0,5)->(1,5). Their offsets ARE world coordinates, because the instance's only
#: component stands at the origin (a rigid frame with its origin on the cell's).
_RECORDS = [((0.0, 0.0, 1.0, 0.0, 0.2), "A"),
            ((0.0, 5.0, 1.0, 5.0, 0.2), "B")]


def _setup(tmp_path, live_tracks, registry_rows):
    """(cfg, config_path, adapter, keys, planner commands) for the fixture."""
    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg({"dac_buf": _Rec(cell_uuid, [r[0] for r in _RECORDS],
                                [r[1] for r in _RECORDS])})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    adapter = _Adapter({"C1": ("DA", "DAC_BUF")},
                       footprints=[_fp("C1", "DA", "DAC_BUF")],
                       tracks=live_tracks)
    anchor = "anchor:C1:1:0.0000:0.0000"
    keys = [make_registry_key(anchor, cell_identity,
                              CELL_LEVEL_ROLE_PLACEHOLDER, index)
            for index in (0, 1)]
    _write_track_registry(tmp_path, {keys[i]: row for i, row in registry_rows})
    commands = [_cmd(keys[0], (0.0, 0.0), (1.0, 0.0), "A"),
                _cmd(keys[1], (0.0, 5.0), (1.0, 5.0), "B")]
    return cfg, config_path, adapter, keys, commands


def _write_track_registry(tmp_path, rows):
    schema = registry_schema()
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema}), encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **rows}), encoding="utf-8")


def _entry(uuid, start, end, net, width=0.2):
    return {"uuid": uuid, "start_x_mm": start[0], "start_y_mm": start[1],
            "end_x_mm": end[0], "end_y_mm": end[1], "net": net,
            "width_mm": width, "layer": "B.Cu"}


def _core(adapter, cfg, config_path, commands, own_refs=("C1",)):
    from gui.select_cell import select_cell_copper_targets
    return select_cell_copper_targets(
        adapter, cfg, str(config_path), "dac_buf", "DAC_BUF", None, {},
        [], list(commands), own_refs=list(own_refs))


# ── cell 1: the shifted number neither owns the item nor blocks the real one ─

def test_a_shifted_key_neither_owns_its_item_nor_blocks_the_real_one(gate, tmp_path):
    """THE live defect: key |0 (net A at (0,0)) holds the uuid of the net-B track
    that sits at record |1's place. Before Ч1 that key took the item, record |0
    never found its own copper (recorded but not on the board — 1) and record |1
    could not take the item by geometry. Now both records get their own copper and
    the shifted pair is NAMED."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_a", (0.0, 0.0), (1.0, 0.0), "A"),
                     _live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])

    plan = _core(adapter, cfg, config_path, commands)

    assert [getattr(i, "uuid", None) for i in plan.copper] == ["t_a", "t_b"]
    assert plan.copper_missing == 0
    assert "1 registry pair(s) point at another record's copper — ignored" in plan.line


def test_the_shifted_pair_itself_is_not_selected(gate, tmp_path):
    """And the pair is never SELECTED as the record's copper: the net-B track is
    selected for record |1 (its owner), not for record |0."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_a", (0.0, 0.0), (1.0, 0.0), "A"),
                     _live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])

    plan = _core(adapter, cfg, config_path, commands)

    assert len(plan.copper) == 2
    assert plan.copper_by_registry == 0        # no tier-1 claim survived
    assert plan.copper_by_geometry == 2


# ── cell 2: at the planned place, another width = OWNERSHIP ────────────────

def test_a_pair_at_the_place_with_another_width_is_ownership(gate, tmp_path):
    """The item sits where the command plans it and is 0.3 mm wide against the
    planned 0.2 — the copper was EDITED ON THE BOARD. Ownership holds, nothing is
    refused, and the line says so instead of calling it "away from the place"."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_a", (0.0, 0.0), (1.0, 0.0), "A", width=0.3)],
        registry_rows=[(0, _entry("t_a", (0.0, 0.0), (1.0, 0.0), "A", width=0.3))])

    plan = _core(adapter, cfg, config_path, commands[:1])

    assert "t_a" in [getattr(i, "uuid", None) for i in plan.copper]
    assert "edited on the board" in plan.line
    assert "point at another record" not in plan.line
    assert "away from the planned place" not in plan.line


# ── cell 3/4: away with the SAME net stays today's behaviour ───────────────

@pytest.mark.parametrize("width", [0.2, 0.3], ids=("same-width", "other-width"))
def test_a_pair_away_on_the_same_net_is_selected_anyway(gate, tmp_path, width):
    """Away from the place, same net — with or without a width difference: never a
    shifted number, always "selected anyway" (Б1 of
    plan_2026_10_08_narrowing_net_traces_cost)."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_a", (2.0, 2.0), (3.0, 2.0), "A", width=width)],
        registry_rows=[(0, _entry("t_a", (2.0, 2.0), (3.0, 2.0), "A", width=width))])

    plan = _core(adapter, cfg, config_path, commands[:1])

    assert "t_a" in [getattr(i, "uuid", None) for i in plan.copper]
    assert "away from the planned place" in plan.line
    assert "point at another record" not in plan.line


# ── cell 9: a key whose command is NOT in this call keeps its ownership ────

def test_a_key_outside_the_call_keeps_its_ownership(gate, tmp_path):
    """The rule never weakens an entry it cannot judge: key |0's command is not in
    this call, so its (wrong) claim on the net-B track stands and record |1 does
    NOT take it by geometry. Safe by default — the alternative would let any door
    take copper another record's key still points at."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])

    plan = _core(adapter, cfg, config_path, commands[1:])   # ONLY record |1

    assert plan.copper == []
    assert plan.copper_missing == 1


# ── cell 10: the registry-only door ────────────────────────────────────────

def test_the_registry_only_door_drops_only_the_shifted_pair(gate, tmp_path):
    """«Select cell» without a plan (`select_cell_targets` → the map with
    `place_check=False`): the shifted pair is dropped and NAMED, while a pair at
    the record's place with another width stays (it is the record's copper)."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B"),
                     _live_track("t_b2", (0.0, 5.0), (1.0, 5.0), "B", width=0.3)],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B")),
                       (1, _entry("t_b2", (0.0, 5.0), (1.0, 5.0), "B", width=0.3))])
    from gui.select_cell import select_cell_targets

    plan = select_cell_targets(adapter, cfg, str(config_path), "dac_buf",
                               "DAC_BUF", None, {}, own_refs=["C1"])

    uuids = [getattr(i, "uuid", None) for i in plan.copper]
    assert "t_b" not in uuids                 # the shifted pair is dropped
    assert uuids == ["t_b2"]                  # the edited-at-place pair is kept
    assert "1 registry pair(s) point at another record's copper" in plan.line


# ── cell 5/6: the two doors of the map keep their own behaviour ────────────

def test_the_strict_map_claims_neither_the_shifted_nor_the_away_pair(gate, tmp_path):
    """`place_check=True` («Subtract selected copper») claims nothing that is not
    where the record puts its copper — the shifted pair and the away one are both
    refused, and the counts say so."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B"),
                     _live_track("t_a", (2.0, 2.0), (3.0, 2.0), "A")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B")),
                       (1, _entry("t_a", (2.0, 2.0), (3.0, 2.0), "A"))])

    strict = registry_record_copper_map(adapter, str(config_path), cfg, "dac_buf",
                                        "DAC_BUF", None, own_refs=["C1"],
                                        sheet_names={}, place_check=True)

    assert strict.by_record == {}
    assert strict.shifted == 0                # nothing claimed, nothing "shifted"
    assert strict.disagreed == 2              # both pairs refused by the place


def test_the_dry_run_map_gives_both_records_their_own_copper(gate, tmp_path):
    """The dry-run map (`_map_for`) — the one Subtract and the planned «Select
    cell» share — finds record |0's own copper by geometry once the shifted key
    stopped blocking it, and record |1's too."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_a", (0.0, 0.0), (1.0, 0.0), "A"),
                     _live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])
    cell_uuid = cfg.cells["dac_buf"].uuid
    cell_identity = record_key_part("dac_buf", cell_uuid)

    record_map = _map_for(adapter, str(config_path), cell_identity, [], commands)

    assert record_map.by_record.get(("track", CELL_LEVEL_ROLE_PLACEHOLDER, 0)) == "t_a"
    assert record_map.by_record.get(("track", CELL_LEVEL_ROLE_PLACEHOLDER, 1)) == "t_b"
    assert record_map.disagreed == 0


# ── Д-1: a side WITHOUT a net accuses nobody ───────────────────────────────

def test_a_command_without_a_net_is_away_not_shifted(gate, tmp_path):
    """A planned command that names no net cannot call anything "another record's
    copper" — the pair is judged by its PLACE alone: away means «away», selected
    and named, never a shifted number. (Д-1 of the acceptance: strict equality here
    would have taken copper away from its own record.)"""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])

    plan = _core(adapter, cfg, config_path,
                 [_cmd(keys[0], (0.0, 0.0), (1.0, 0.0), None)])

    assert [getattr(i, "uuid", None) for i in plan.copper] == ["t_b"]
    assert "away from the planned place" in plan.line
    assert "point at another record" not in plan.line


def test_a_live_item_without_a_net_is_away_not_shifted(gate, tmp_path):
    """And the same for the LIVE side: an item the board reports with no net is
    not accused either. The place still decides — it is away, so «away»."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_x", (2.0, 2.0), (3.0, 2.0), None)],
        registry_rows=[(0, _entry("t_x", (2.0, 2.0), (3.0, 2.0), None))])

    plan = _core(adapter, cfg, config_path, commands[:1])

    assert [getattr(i, "uuid", None) for i in plan.copper] == ["t_x"]
    assert "away from the planned place" in plan.line
    assert "point at another record" not in plan.line


# ── the rule itself, in one place ──────────────────────────────────────────

def test_pair_verdict_names_the_three_cases():
    """The verdicts, pinned directly (the doors above exercise them): EDITED at
    the place, SHIFTED away on another net, AWAY away on the same net."""
    at_place = SimpleNamespace(position=Vector2.from_xy_mm(0.0, 0.0), net_name="A",
                               drill_mm=0.4, diameter_mm=0.6)
    away = SimpleNamespace(position=Vector2.from_xy_mm(5.0, 5.0), net_name="A",
                           drill_mm=0.3, diameter_mm=0.6)
    away_other_net = SimpleNamespace(position=Vector2.from_xy_mm(5.0, 5.0),
                                     net_name="B", drill_mm=0.3,
                                     diameter_mm=0.6)
    plain = SimpleNamespace(position=Vector2.from_xy_mm(0.0, 0.0), net_name="A",
                            drill_mm=0.3, diameter_mm=0.6)
    from kicadstamp.registry_match import PlannedCopperMatch
    from kicadstamp.placement.commands import ViaCommand
    cmd = ViaCommand(position=Vector2.from_xy_mm(0.0, 0.0), net_name="A",
                     drill_mm=0.3, diameter_mm=0.6, owner_ref="C1",
                     registry_key="k")

    def verdict(live):
        return pair_verdict(PlannedCopperMatch(command=cmd, live=live,
                                               tier="registry"))

    assert verdict(at_place) == PAIR_EDITED     # same place, another drill
    assert verdict(away) == PAIR_AWAY           # same net, other place
    assert verdict(away_other_net) == PAIR_SHIFTED
    assert verdict(plain) == PAIR_AGREES        # identical: neither edited nor shifted


def test_a_pair_where_one_side_names_no_net_is_never_shifted():
    """Д-1, directly: `same_net` answers True when EITHER side is silent — the
    place then produces «away», never «shifted»."""
    from kicadstamp.registry_match import same_net

    silent_planned = SimpleNamespace(net_name=None, drill_mm=0.3,
                                     diameter_mm=0.6)
    named_item = SimpleNamespace(net_name="B", drill_mm=0.3, diameter_mm=0.6)
    silent_item = SimpleNamespace(net_name=None, drill_mm=0.3, diameter_mm=0.6)
    named_planned = SimpleNamespace(net_name="A", drill_mm=0.3,
                                    diameter_mm=0.6)
    empty_planned = SimpleNamespace(net_name="", drill_mm=0.3, diameter_mm=0.6)

    assert same_net(named_item, silent_planned)      # the plan is silent
    assert same_net(silent_item, named_planned)      # the item is silent
    assert same_net(named_item, empty_planned)       # "" is silence too
    assert not same_net(named_item, named_planned)   # two names DO compare


def test_shifted_entries_reads_the_keys_of_this_call_only(tmp_path, gate):
    """The one function behind both the release and the reported count: it names
    exactly the keys of the given commands, nothing else."""
    cfg, config_path, adapter, keys, commands = _setup(
        tmp_path,
        live_tracks=[_live_track("t_b", (0.0, 5.0), (1.0, 5.0), "B")],
        registry_rows=[(0, _entry("t_b", (0.0, 5.0), (1.0, 5.0), "B"))])
    reg = TrackRegistry(adapter, str(tmp_path / "tracks" / "config.tracks.registry.json"))

    assert shifted_entries(reg, commands, live_items=adapter.get_tracks()) == {
        keys[0]: "t_b"}
    assert shifted_entries(reg, commands[1:],
                           live_items=adapter.get_tracks()) == {}
