# tests/selection/test_subtract_selection.py
"""С-2 «Subtract selected copper» — the CORE cells (Qt-free, no board).

Two halves, exactly the two the plan splits the action into:

  * the MAP — which live copper each cell record's own dry run matched
    (`kicadstamp/absent_copper_prune.py`): by the registry's stored uuid (tier 1),
    then by EXACT geometry (tier 2). A no-board fake adapter plus real registry
    files is all it needs.
  * the DECISION — which records the selection names
    (`kicadstamp/subtract_selection.py`): the exact pair is removed, selected
    copper that is not this cell's record is IGNORED (never silently dropped, and
    never removed), components are ignored, an empty dry run removes NOTHING.

Why the pair must be EXACT and not "nearest" (the plan's own mutation): the
refresh read pairs greedily by the nearest live item — right for updating
geometry, dangerous for deletion, because a foreign track 0.1 mm away from a
record would delete that record. The cells below pin the difference: a near-miss
is not claimed (map cell) and therefore cannot be subtracted (decision cell).

The GUI half (the worker, the button, the Log lines) is a separate commit; these
cells never touch Qt or the board.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from kicadstamp.absent_copper_prune import (RecordCopperMap, record_copper_map_for)
from kicadstamp.constants import ROLE_FIELD_NAME, SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.registry import make_registry_key, record_key_part
from kicadstamp.subtract_selection import matched_instance_labels, plan_subtraction

CELL = "dac_buf"
CELL_IDENTITY = record_key_part(CELL, "uuid-cell")


# ── the DECISION (no adapter at all) ────────────────────────────────────────

def _track(net, across):
    return {"net": net, "layer": "F.Cu", "width_mm": 0.25,
            "start_along_mm": 0.0, "start_across_mm": across,
            "end_along_mm": 1.0, "end_across_mm": across}


def test_the_exact_pair_is_the_one_removed():
    """The record whose LIVE COPPER is selected goes — by its own index, and by
    identity (the SAME dict the cell holds)."""
    rec0, rec1 = _track(None, 0.0), _track("GND", 2.0)
    # Cell-LEVEL copper: the key's role part is the spoke placeholder, the index
    # is a position in the cell's OWN tracks list.
    record_map = RecordCopperMap(
        by_record={("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u0"}, planned=2)

    outcome = plan_subtraction(record_map, [], [rec0, rec1], set(), {"u0"})

    assert outcome.removed == (("track", rec0),)
    assert outcome.removed[0][1] is rec0        # the very dict, not a copy
    assert outcome.not_ours == 0
    assert outcome.empty is False


def test_selected_copper_that_is_not_a_record_is_ignored_not_removed():
    """The "чужая дорожка в 0.1 мм" case at the decision level: the selection
    holds an item this cell's map never claimed — nothing is removed and the
    caller can SAY it (the counter), instead of silently dropping it."""
    rec0 = _track(None, 0.0)
    record_map = RecordCopperMap(
        by_record={("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u0"}, planned=1)

    outcome = plan_subtraction(record_map, [], [rec0], set(), {"foreign"})

    assert outcome.removed == ()
    assert outcome.not_ours == 1


def test_an_empty_dry_run_removes_nothing():
    """A refused tree / an unrealized record / a chain-only placement plans no
    command: the map is EMPTY, and an empty map must never delete — even when
    copper IS selected."""
    rec0 = _track("GND", 2.0)
    outcome = plan_subtraction(RecordCopperMap(), [], [rec0], set(), {"u0"})

    assert outcome.empty is True
    assert outcome.planned == 0
    assert outcome.removed == ()


def test_components_are_counted_and_never_subtracted():
    """Only copper is subtracted; the selected components are reported by count."""
    outcome = plan_subtraction(RecordCopperMap(planned=3), [], [], set(), set(),
                               selected_components=2)

    assert outcome.removed == ()
    assert outcome.components == 2


def test_via_and_track_records_use_their_own_lists():
    """A via index must never index into the tracks bucket (and vice versa)."""
    via0 = {"net": "N", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
            "drill_mm": 0.3, "diameter_mm": 0.6}
    track0 = _track("N", 5.0)
    record_map = RecordCopperMap(
        by_record={("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "uv",
                   ("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "ut"},
        planned=2)

    outcome = plan_subtraction(record_map, [via0], [track0], {"uv"}, {"ut"})

    assert outcome.removed == (("via", via0), ("track", track0))


def test_an_index_outside_the_record_list_is_not_a_record_of_the_cell():
    """A map entry pointing past the cell's list resolves to NO record: nothing is
    removed (no crash, no wrong neighbour) and the selected item is reported as
    "not a record of the cell" — the same wording an unknown role gets."""
    rec0 = _track("GND", 2.0)
    record_map = RecordCopperMap(
        by_record={("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 7): "u0"}, planned=8)

    outcome = plan_subtraction(record_map, [], [rec0], set(), {"u0"})

    assert outcome.removed == ()
    assert outcome.not_ours == 1


def test_matched_instance_labels_names_the_instances_whose_copper_is_selected():
    """Two instances whose dry runs both matched the selection are BOTH named —
    the caller refuses (deleting the wrong instance's record would take copper
    from a live one); one match is the instance, none is nothing."""
    one = RecordCopperMap(by_record={("track", "rp", 0): "u1"}, planned=1)
    two = RecordCopperMap(by_record={("track", "rp", 0): "u2"}, planned=1)

    assert matched_instance_labels(
        [("DAC_BUF on Channel_1", two)], {"u2"}) == ("DAC_BUF on Channel_1",)
    assert matched_instance_labels(
        [("DAC_BUF on Channel_0", one), ("DAC_BUF on Channel_1", two)],
        {"u2"}) == ("DAC_BUF on Channel_1",)
    assert matched_instance_labels(
        [("DAC_BUF on Channel_0", one), ("DAC_BUF on Channel_1", two)],
        {"u1", "u2"}) == ("DAC_BUF on Channel_0", "DAC_BUF on Channel_1")
    assert matched_instance_labels([("x", one)], {"nope"}) == ()
    assert matched_instance_labels([("x", one)], set()) == ()


# ── the MAP (a fake adapter + real registry files; no board) ────────────────

class _Adapter:
    """The only two reads `record_copper_map_for` makes of the board."""

    def __init__(self, vias=(), tracks=()):
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)


def _write_registries(tmp_path, via_entries, track_entries):
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": 2, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": 2, **track_entries}), encoding="utf-8")


def _config_path(tmp_path):
    path = tmp_path / "config.sexp"
    path.write_text("", encoding="utf-8")
    return str(path)


def _via_entry(uuid):
    return {"uuid": uuid, "x_mm": 10.0, "y_mm": 10.0, "net": "N",
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _live_via(uuid, x_mm, y_mm):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm), net_name="N",
               drill_mm=0.3, diameter_mm=0.6)


def _via_cmd(key, x_mm, y_mm):
    return ViaCommand(position=Vector2.from_xy_mm(x_mm, y_mm), drill_mm=0.3,
                      diameter_mm=0.6, net_name="N", owner_ref="ent",
                      registry_key=key)


def test_the_map_pairs_by_the_registry_uuid(tmp_path):
    """Tier 1: the uuid the registry stored for the record's key IS on the board —
    the map names that live copper, whatever its geometry does."""
    key = make_registry_key("name:ent", CELL_IDENTITY, "DA", 0)
    _write_registries(tmp_path, {key: _via_entry("u_live")}, {})
    adapter = _Adapter(vias=[_live_via("u_live", 10.0, 10.0)])

    record_map = record_copper_map_for(
        adapter, _config_path(tmp_path), CELL_IDENTITY, [_via_cmd(key, 10.0, 10.0)],
        [])

    assert record_map.planned == 1
    assert record_map.by_record == {("via", "DA", 0): "u_live"}


def test_the_map_pairs_by_exact_geometry_when_the_uuid_went_stale(tmp_path):
    """Tier 2: the registry knows nothing (empty file) but the command's geometry
    IS on the board — the map still names it."""
    key = make_registry_key("name:ent", CELL_IDENTITY, "DA", 0)
    _write_registries(tmp_path, {}, {})
    adapter = _Adapter(vias=[_live_via("u_new", 20.0, 20.0)])

    record_map = record_copper_map_for(
        adapter, _config_path(tmp_path), CELL_IDENTITY, [_via_cmd(key, 20.0, 20.0)],
        [])

    assert record_map.by_record == {("via", "DA", 0): "u_new"}


def test_the_map_never_claims_a_near_miss(tmp_path):
    """THE «0.1 мм» property, at the map level: a foreign via 0.1 mm away is not
    the record's copper — the exact-geometry tier refuses it, even though it is
    the FIRST candidate in board order. This is what makes deletion safe: the
    greedy nearest pairing of the refresh read would have taken it."""
    key = make_registry_key("name:ent", CELL_IDENTITY, "DA", 0)
    _write_registries(tmp_path, {}, {})
    adapter = _Adapter(vias=[_live_via("u_foreign", 20.1, 20.0),
                             _live_via("u_exact", 20.0, 20.0)])

    record_map = record_copper_map_for(
        adapter, _config_path(tmp_path), CELL_IDENTITY, [_via_cmd(key, 20.0, 20.0)],
        [])

    assert record_map.by_record == {("via", "DA", 0): "u_exact"}


def test_the_map_ignores_a_command_of_another_record(tmp_path):
    """A command whose template part is SOME OTHER record (a net_trace, a chain,
    a thermal array) is not this cell's record, however well its geometry
    matches. The control in the same cell: the SAME command under THIS cell's
    template IS claimed — so the empty answer above is the identity check working,
    not a blind zero."""
    other = make_registry_key("name:ent", record_key_part("pif_avdd", "u-x"),
                              "DA", 0)
    mine = make_registry_key("name:ent", CELL_IDENTITY, "DA", 0)
    _write_registries(tmp_path, {}, {})
    config_path = _config_path(tmp_path)
    adapter = _Adapter(vias=[_live_via("u_x", 30.0, 30.0)])

    foreign = record_copper_map_for(adapter, config_path, CELL_IDENTITY,
                                    [_via_cmd(other, 30.0, 30.0)], [])
    control = record_copper_map_for(adapter, config_path, CELL_IDENTITY,
                                    [_via_cmd(mine, 30.0, 30.0)], [])

    assert foreign.planned == 1 and foreign.by_record == {}
    assert control.by_record == {("via", "DA", 0): "u_x"}


def test_the_map_reports_an_empty_run_as_planned_zero(tmp_path):
    """The dry run produced no command at all — the caller must be able to tell
    "we could not check" from "the cell has no copper"."""
    _write_registries(tmp_path, {}, {})
    record_map = record_copper_map_for(_Adapter(), _config_path(tmp_path),
                                       CELL_IDENTITY, [], [])

    assert record_map.planned == 0
    assert record_map.empty is True


def test_a_track_record_pairs_by_exact_geometry(tmp_path):
    """The same two tiers on the tracks side (the predicate is shared, but the
    bucket is separate — the map must key it as a TRACK)."""
    key = make_registry_key("name:ent", CELL_IDENTITY, None, 0)
    _write_registries(tmp_path, {}, {})
    live = Track(uuid="u_track", net_name="N",
                 start=Vector2.from_xy_mm(0.0, 0.0),
                 end=Vector2.from_xy_mm(1.0, 0.0), width_mm=0.25,
                 layer=BoardLayer.BL_F_Cu)
    cmd = TrackCommand(start=Vector2.from_xy_mm(0.0, 0.0),
                       end=Vector2.from_xy_mm(1.0, 0.0), width_mm=0.25,
                       net_name="N", layer=BoardLayer.BL_F_Cu, owner_ref="ent",
                       registry_key=key)

    record_map = record_copper_map_for(_Adapter(tracks=[live]),
                                       _config_path(tmp_path), CELL_IDENTITY,
                                       [], [cmd])

    assert list(record_map.by_record.values()) == ["u_track"]
    assert [k[0] for k in record_map.by_record] == ["track"]
    assert [k[2] for k in record_map.by_record] == [0]


# ── the TWO LEVELS of a cell's copper (the key's role part) ─────────────────

def _via_record(across):
    return {"net": "N", "offset_along_mm": 0.0, "offset_across_mm": across,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def test_a_component_via_is_removed_from_its_component_not_from_the_cell():
    """The map key is THREE parts. The dry run plans the cell's own copper under
    the spoke placeholder (index into the cell's list) and each component's copper
    under THAT COMPONENT's role (index into the component's list). Selecting a
    component's via 0 must remove the component's via — the first version dropped
    the role part and took ``cell_vias[0]``, a silent wrong record."""
    cell_via = _via_record(0.0)
    comp_via = _via_record(5.0)
    components = [{"role": "R3", "vias": [comp_via]},
                  {"role": "C9", "vias": []}]
    record_map = RecordCopperMap(
        by_record={("via", "R3", 0): "u_comp",
                   ("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u_cell"},
        planned=2)

    outcome = plan_subtraction(record_map, [cell_via], [], {"u_comp"}, set(),
                               components=components)

    assert outcome.removed == (("via", comp_via),)
    assert outcome.removed[0][1] is comp_via
    assert cell_via not in outcome.removed_of("via")
    assert outcome.not_ours == 0


def test_the_cell_level_and_the_component_level_with_one_index_are_distinct():
    """The SAME index at the two levels names two different records: selecting one
    of them removes exactly it (the flat-list version removed the cell's one
    whichever was selected)."""
    cell_via = _via_record(0.0)
    comp_via = _via_record(5.0)
    components = [{"role": "R3", "vias": [comp_via]}]
    record_map = RecordCopperMap(
        by_record={("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u_cell",
                   ("via", "R3", 0): "u_comp"},
        planned=2)

    only_cell = plan_subtraction(record_map, [cell_via], [], {"u_cell"}, set(),
                                components=components)
    only_comp = plan_subtraction(record_map, [cell_via], [], {"u_comp"}, set(),
                                 components=components)

    assert only_cell.removed == (("via", cell_via),)
    assert only_comp.removed == (("via", comp_via),)


def test_a_role_the_cell_does_not_have_is_not_a_record_of_the_cell():
    """A role part naming no component of the cell (and not the placeholder)
    resolves to NO record: nothing is removed, and the selected item is reported
    as "not a record of the cell" instead of being dropped silently."""
    comp_via = _via_record(5.0)
    record_map = RecordCopperMap(by_record={("via", "GONE", 0): "u_x"}, planned=1)

    outcome = plan_subtraction(record_map, [], [], {"u_x"}, set(),
                               components=[{"role": "R3", "vias": [comp_via]}])

    assert outcome.removed == ()
    assert outcome.not_ours == 1


# ── the «registry only» map (a REFUSED tree: the dry run planned nothing) ────
#
# plan_2026_10_07_refused_tree_matching, rule 35. When the drift guard refused
# the tree that places the instance, the recording is NOT materialized, the dry
# run plans no command, and the exact pair cannot be built — so the map is built
# from the REGISTRY ALONE. The cells below pin, at the CORE level, the
# properties the plan's mutations attack: which keys are accepted (own only),
# that a key's copper must be ON the board, that `kind` comes from the file the
# key came from, and the "no registry entry" count.

_RMAP_CELL = "dac_buf"


class _RMapCfg:
    """The smallest cfg `record_key_part`/`cell_record_addresses` read: one cell
    with copper and the entities that place it."""

    def __init__(self, cell, entities):
        self.cells = {_RMAP_CELL: cell}
        self.entities = list(entities)
        self.clone_placements = []


def _rmap_entity(name, uuid, cluster="DAC_BUF", sheet=None):
    return SimpleNamespace(name=name, uuid=uuid, cell=_RMAP_CELL,
                           cluster=cluster, sheet=sheet, retired=False)


def _rmap_cell():
    """1 cell via, 2 cell tracks, 1 component (role DA) via — 4 records.

    The STORED offsets are the ones the fixture's live copper sits at (the cell's
    zero-offset slot DA is the live frame's origin, placed at the world origin by
    `_BoardAdapter`), so a registry-only pair of this fixture is a pair that DOES
    sit where its record puts it — the place check of
    plan_2026_10_07_registry_pair_frame_check is satisfied by construction, and
    the cells below stay about the properties they name."""
    return SimpleNamespace(
        uuid="uuid-cell",
        anchor_role=None,
        anchor_xy=None,
        vias=[SimpleNamespace(offset_along_mm=10.0, offset_across_mm=10.0)],
        tracks=[SimpleNamespace(start_along_mm=10.0, start_across_mm=10.0,
                                end_along_mm=11.0, end_across_mm=10.0),
                SimpleNamespace(start_along_mm=10.0, start_across_mm=12.0,
                                end_along_mm=11.0, end_across_mm=12.0)],
        components=[SimpleNamespace(
            role="DA", offset_along_mm=0.0, offset_across_mm=0.0,
            vias=[SimpleNamespace(offset_along_mm=10.0,
                                  offset_across_mm=10.0)])],
    )


class _RMapFp(Footprint):
    """A live footprint that also carries the Role/Cluster fields the adapter
    reads (`get_field_value`)."""

    def __init__(self, ref, role, cluster, x_mm, y_mm):
        super().__init__(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                         position=Vector2.from_xy_mm(x_mm, y_mm),
                         layer=BoardLayer.BL_F_Cu)
        self.role = role
        self.cluster = cluster
        self.sheet_path_uuids = ()


def _track_entry(uuid):
    return {"uuid": uuid, "start_x_mm": 10.0, "start_y_mm": 10.0,
            "end_x_mm": 11.0, "end_y_mm": 10.0, "width_mm": 0.25, "net": "N",
            "layer": "F.Cu"}


def _live_track(uuid):
    return Track(uuid=uuid, net_name="N", start=Vector2.from_xy_mm(10.0, 10.0),
                 end=Vector2.from_xy_mm(11.0, 10.0), width_mm=0.25,
                 layer=BoardLayer.BL_F_Cu)


class _BoardAdapter(_Adapter):
    """`_Adapter` plus the footprint reads `own_instance_context` and the cell
    frame make: ONE live footprint for the cell's own zero-offset slot (role DA,
    cluster DAC_BUF) at the world origin — so the fixture's frame is RIGID and its
    origin is the cell's own (0,0), which makes a record's stored offset its world
    position."""

    def get_field_value(self, fp, name):
        return fp.role if name == ROLE_FIELD_NAME else fp.cluster

    def get_footprints(self):
        return [_RMapFp("C1", "DA", "DAC_BUF", 0.0, 0.0)]


def _rmap_setup(tmp_path, *, foreign=False):
    """(cfg, config_path, adapter) for dac_buf @ DAC_BUF.

    Registry: an OWN track 0 (live), an OWN via DA 0 (live) and an OWN cell via
    0 whose uuid is NOT on the board; when `foreign`, ANOTHER cell's track 1
    (live) with the SAME tail as a record of ours."""
    cfg = _RMapCfg(_rmap_cell(), [_rmap_entity("dac0", "uuid-ent")])
    cell_identity = record_key_part(_RMAP_CELL, "uuid-cell")
    anchor = f"name:{record_key_part('dac0', 'uuid-ent')}"
    via_entries = {
        make_registry_key(anchor, cell_identity, "DA", 0): _via_entry("u_c0"),
        make_registry_key(anchor, cell_identity, None, 0): _via_entry("u_gone"),
    }
    track_entries = {
        make_registry_key(anchor, cell_identity, None, 0): _track_entry("u_t0"),
    }
    adapter = _BoardAdapter(vias=[_live_via("u_c0", 10.0, 10.0)],
                            tracks=[_live_track("u_t0")])
    if foreign:
        other = record_key_part("pif", "uuid-pif")
        track_entries[make_registry_key("name:" + other, other, None, 1)] = \
            _track_entry("u_foreign")
        adapter._tracks.append(_live_track("u_foreign"))
    _write_registries(tmp_path, via_entries, track_entries)
    return cfg, _config_path(tmp_path), adapter


def _rmap(adapter, config_path, cfg):
    from kicadstamp.absent_copper_prune import registry_record_copper_map
    return registry_record_copper_map(adapter, config_path, cfg, _RMAP_CELL,
                                      "DAC_BUF", None)


def test_registry_map_pairs_own_keys_with_the_live_copper_of_their_own_registry(
        tmp_path):
    """The happy path: own keys of BOTH files and BOTH levels map to the live
    copper the registry stores — a via key as a VIA, a track key as a TRACK
    (mutation: `kind` taken from the wrong registry)."""
    cfg, config_path, adapter = _rmap_setup(tmp_path)

    record_map = _rmap(adapter, config_path, cfg)

    assert record_map.source == "registry"
    assert record_map.planned == 2
    assert record_map.by_record == {
        ("via", "DA", 0): "u_c0",
        ("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u_t0"}


def test_registry_map_drops_a_key_whose_uuid_left_the_board(tmp_path):
    """Mutation "the registry map does not check the uuid is on the board": the
    cell's via 0 has an OWN key, but that uuid is GONE — it must NOT be claimed
    (the subtraction would then "remove" a record whose copper is gone)."""
    cfg, config_path, adapter = _rmap_setup(tmp_path)

    record_map = _rmap(adapter, config_path, cfg)

    assert ("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0) not in record_map.by_record
    # ...and it IS checked (the registry knows about it) — not "not checked".
    assert record_map.without_registry == 1        # only the foreign track 1


def test_registry_map_never_claims_a_foreign_key(tmp_path):
    """Mutation "the `is_own_key` filter is off": ANOTHER cell's key has its
    copper live on the board and the SAME tail `(track, PH, 1)` as a record of
    ours — it must never map."""
    cfg, config_path, adapter = _rmap_setup(tmp_path, foreign=True)

    record_map = _rmap(adapter, config_path, cfg)

    assert ("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, 1) not in record_map.by_record
    assert set(record_map.by_record.values()) == {"u_c0", "u_t0"}


def test_registry_map_counts_the_records_without_a_registry_key(tmp_path):
    """The honest count (mutation "the not-checked count is lost"): track 1 has
    no OWN key, so it cannot be checked — the number is named, never silent."""
    cfg, config_path, adapter = _rmap_setup(tmp_path, foreign=True)

    record_map = _rmap(adapter, config_path, cfg)

    assert record_map.without_registry == 1


def test_registry_map_of_an_empty_registry_is_empty_and_counts_every_record(
        tmp_path):
    """The "red planned nothing" side: with NO registry at all the map is empty
    (nothing to match), and EVERY record of the cell is "not checked"."""
    _write_registries(tmp_path, {}, {})
    cfg = _RMapCfg(_rmap_cell(), [_rmap_entity("dac0", "uuid-ent")])

    record_map = _rmap(_BoardAdapter(), _config_path(tmp_path), cfg)

    assert record_map.empty is True
    assert record_map.planned == 0
    assert record_map.source == "registry"
    assert record_map.without_registry == 4        # all four records
