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

from kicadstamp.absent_copper_prune import (RecordCopperMap, record_copper_map_for)
from kicadstamp.domain.board import Track, Via
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
    record_map = RecordCopperMap(by_record={("track", "rp", 0): "u0"}, planned=2)

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
    record_map = RecordCopperMap(by_record={("track", "rp", 0): "u0"}, planned=1)

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
    record_map = RecordCopperMap(by_record={("via", "rp", 0): "uv",
                                            ("track", "rp", 0): "ut"},
                                 planned=2)

    outcome = plan_subtraction(record_map, [via0], [track0], {"uv"}, {"ut"})

    assert outcome.removed == (("via", via0), ("track", track0))


def test_an_index_outside_the_record_list_is_ignored():
    """Defensive: a map entry pointing past the cell's list can never crash the
    action (it removes nothing)."""
    rec0 = _track("GND", 2.0)
    record_map = RecordCopperMap(by_record={("track", "rp", 7): "u0"}, planned=8)

    outcome = plan_subtraction(record_map, [], [rec0], set(), {"u0"})

    assert outcome.removed == ()
    assert outcome.not_ours == 0


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
