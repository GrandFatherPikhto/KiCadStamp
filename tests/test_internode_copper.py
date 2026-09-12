# tests/test_internode_copper.py
"""Tests for kicadstamp/internode_copper.py — the "copper between pads" unit
extraction and its classification (plan_2026_09_12_internode_copper_core.md,
stage Э1; design §4/§16).

All geometry is synthetic and pure — no board, no Qt, no adapter beyond a mock
that answers pad/box geometry.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.internode_copper import (
    CopperUnit,
    CopperVerdict,
    PadRef,
    classify_unit,
    find_copper_units,
    net_conflicts,
)


# ── fixtures ──────────────────────────────────────────────────────────────

def _fp(ref):
    return Footprint(ref=ref, uuid=f"fp-{ref}",
                     position=Vector2.from_xy_mm(0, 0), angle_deg=0.0,
                     layer=BoardLayer.BL_F_Cu)


def _pad(number, x_mm, y_mm, net="N"):
    return SimpleNamespace(number=number,
                           position=Vector2.from_xy_mm(x_mm, y_mm),
                           net_name=net)


def _track(start_mm, end_mm, net="N", uuid=None):
    return Track(uuid=uuid or f"t-{start_mm}-{end_mm}",
                 start=Vector2.from_xy_mm(*start_mm),
                 end=Vector2.from_xy_mm(*end_mm),
                 net_name=net, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _via(x_mm, y_mm, net="N", uuid=None):
    return Via(uuid=uuid or f"v-{x_mm}-{y_mm}",
               position=Vector2.from_xy_mm(x_mm, y_mm), net_name=net,
               drill_mm=0.3, diameter_mm=0.6)


def _adapter(pads_by_ref):
    """Mock adapter: get_footprint_pads answers per ref, get_bounding_boxes
    gives a 0.6 mm box centered on each item — the same shape the extract
    connectivity closure uses."""
    adapter = MagicMock()
    adapter.get_footprint_pads.side_effect = lambda fp: pads_by_ref.get(fp.ref, [])

    def _boxes(items):
        out = []
        for it in items:
            b = SimpleNamespace()
            b.pos = Vector2.from_xy(int(it.position.x - 0.3 * 1_000_000),
                                    int(it.position.y - 0.3 * 1_000_000))
            b.size = Vector2.from_xy(600_000, 600_000)
            b.inflate = lambda _d: None
            out.append(b)
        return out

    adapter.get_bounding_boxes.side_effect = _boxes
    return adapter


def _run(items, pads_by_ref, refs):
    adapter = _adapter(pads_by_ref)
    return find_copper_units(adapter, items, footprints=[_fp(r) for r in refs])


# ── unit extraction ───────────────────────────────────────────────────────

def test_two_bridges_between_different_pad_pairs_are_two_units():
    """Two independent links of the SAME net between different pad pairs must
    stay two units — the unit is the piece of copper, not the net."""
    pads = {"R1": [_pad("1", 10, 10)], "R2": [_pad("1", 20, 10)],
            "R3": [_pad("1", 10, 20)], "R4": [_pad("1", 20, 20)]}
    raw = [_track((10, 10), (20, 10), net="SHARED"),
           _track((10, 20), (20, 20), net="SHARED")]
    units, warnings = _run(raw, pads, ["R1", "R2", "R3", "R4"])
    assert warnings == []
    assert len(units) == 2
    assert [len(u.pads) for u in units] == [2, 2]
    assert [len(u.tracks) for u in units] == [1, 1]
    assert units[0].pads == (PadRef("R1", "1"), PadRef("R2", "1"))
    assert units[1].pads == (PadRef("R3", "1"), PadRef("R4", "1"))


def test_track_from_pad_into_nothing_is_a_stub():
    """A stub (copper from a pad into nowhere) is not a link: one pad, dead
    end -> STUB."""
    pads = {"R1": [_pad("1", 10, 10)]}
    units, _ = _run([_track((10, 10), (15, 10))], pads, ["R1"])
    assert len(units) == 1
    assert units[0].pads == (PadRef("R1", "1"),)
    assert classify_unit(units[0], {"R1": "A"}) == CopperVerdict.STUB


def test_t_branch_off_a_pad_is_one_unit_with_three_pads():
    """A T-branch whose joint is NOT on a pad is one physical piece of copper:
    one unit carrying three pads."""
    pads = {"R1": [_pad("1", 0, 0)], "R2": [_pad("1", 10, 0)],
            "R3": [_pad("1", 5, 10)]}
    raw = [_track((0, 0), (5, 0)), _track((5, 0), (10, 0)),
           _track((5, 0), (5, 10))]
    units, _ = _run(raw, pads, ["R1", "R2", "R3"])
    assert len(units) == 1
    assert len(units[0].tracks) == 3
    assert units[0].pads == (PadRef("R1", "1"), PadRef("R2", "1"),
                             PadRef("R3", "1"))


def test_tracks_meeting_on_one_pad_are_two_units():
    """The core trap: a pad is a TERMINATOR, not an edge. Two tracks meeting
    exactly on one pad do NOT merge — otherwise the whole net collapses back
    into one connected component and the strict rule dies."""
    pads = {"R1": [_pad("1", 10, 10)], "R2": [_pad("1", 20, 10)],
            "R3": [_pad("1", 30, 10)]}
    raw = [_track((10, 10), (20, 10)), _track((20, 10), (30, 10))]
    units, _ = _run(raw, pads, ["R1", "R2", "R3"])
    assert len(units) == 2
    owners = {"R1": "A", "R2": "B", "R3": "C"}
    assert [classify_unit(u, owners) for u in units] == [
        CopperVerdict.INTERNODE, CopperVerdict.INTERNODE]
    assert units[0].pads == (PadRef("R1", "1"), PadRef("R2", "1"))
    assert units[1].pads == (PadRef("R2", "1"), PadRef("R3", "1"))


def test_via_chain_without_pads_is_unmoored():
    """A stitching-via chain (no pad on either end) is one unit with no pads:
    it is filler, not a connection."""
    raw = [_via(30, 30), _via(31, 30), _track((30, 30), (31, 30))]
    units, _ = _run(raw, {}, [])
    assert len(units) == 1
    assert units[0].pads == ()
    assert classify_unit(units[0], {"R1": "A"}) == CopperVerdict.UNMOORED


def test_track_passing_over_a_pad_without_touching_it_is_not_moored():
    """Mooring is by a track ENDPOINT on a pad (the same rule the extract
    closure uses): copper merely passing over a pad is not moored to it."""
    pads = {"R1": [_pad("1", 10, 10)], "R2": [_pad("1", 40, 10)]}
    raw = [_track((0, 10), (40, 10))]
    units, _ = _run(raw, pads, ["R1", "R2"])
    # R1's pad sits ON the segment body but not on an endpoint -> not moored;
    # R2's pad IS the far endpoint -> the only pad of the unit.
    assert units[0].pads == (PadRef("R2", "1"),)


def test_no_copper_yields_no_units():
    units, warnings = _run([], {}, [])
    assert units == [] and warnings == []


def test_missing_pad_geometry_is_a_programming_error():
    """Without real pad boxes there is no boundary at all — refuse loudly
    instead of silently degrading to "everything is one unit"."""
    import pytest
    adapter = MagicMock(spec=[])
    with pytest.raises(ValueError):
        find_copper_units(adapter, [_track((0, 0), (1, 0))])


def test_zones_are_not_part_of_the_graph():
    """Decision Р4: zones never enter the connectivity graph. A zone object in
    the item list is ignored outright — it must not create or merge units."""
    pads = {"R1": [_pad("1", 10, 10)], "R2": [_pad("1", 20, 10)]}
    zone = SimpleNamespace(name="GND", position=Vector2.from_xy_mm(15, 10))
    raw = [_track((10, 10), (20, 10)), zone]
    units, _ = _run(raw, pads, ["R1", "R2"])
    assert len(units) == 1
    assert units[0].pads == (PadRef("R1", "1"), PadRef("R2", "1"))


# ── classification (the strict rule of Р1) ────────────────────────────────

def _unit(pads):
    return CopperUnit(tracks=[], vias=[], pads=tuple(pads))


def test_classify_two_nodes_is_internode():
    unit = _unit([PadRef("R1", "1"), PadRef("R2", "1")])
    assert classify_unit(unit, {"R1": "A", "R2": "B"}) == CopperVerdict.INTERNODE


def test_classify_all_pads_in_one_node_is_cluster():
    unit = _unit([PadRef("R1", "1"), PadRef("R1", "2")])
    assert classify_unit(unit, {"R1": "A"}) == CopperVerdict.CLUSTER


def test_classify_pad_outside_tree_is_foreign():
    unit = _unit([PadRef("R1", "1"), PadRef("R9", "1")])
    assert classify_unit(unit, {"R1": "A"}) == CopperVerdict.FOREIGN


def test_classify_no_pads_is_unmoored():
    assert classify_unit(_unit([]), {"R1": "A"}) == CopperVerdict.UNMOORED


def test_classify_single_node_pad_is_a_stub():
    unit = _unit([PadRef("R1", "1")])
    assert classify_unit(unit, {"R1": "A"}) == CopperVerdict.STUB


def test_foreign_pad_outranks_node_count():
    """One pad of the tree + one pad of a foreign component is NOT internode:
    the unit leaves the tree (design §4 / question 3 of §10)."""
    unit = _unit([PadRef("R1", "1"), PadRef("R2", "1"), PadRef("R9", "1")])
    assert classify_unit(unit, {"R1": "A", "R2": "B"}) == CopperVerdict.FOREIGN


# ── net-name consistency is a warning, never a silent merge/split ─────────

def test_net_conflict_on_one_unit_is_a_warning():
    pads = {"R1": [_pad("1", 0, 0)], "R2": [_pad("1", 20, 0)]}
    raw = [_track((0, 0), (10, 0), net="N1"),
           _track((10, 0), (20, 0), net="N2")]
    units, warnings = _run(raw, pads, ["R1", "R2"])
    assert len(units) == 1
    assert units[0].net_names == {"N1", "N2"}
    assert units[0].net_name is None
    assert len(warnings) == 1
    assert "N1" in warnings[0] and "N2" in warnings[0]


def test_single_net_unit_has_no_warning_and_reports_its_net():
    pads = {"R1": [_pad("1", 0, 0)], "R2": [_pad("1", 20, 0)]}
    units, warnings = _run([_track((0, 0), (20, 0), net="SHARED")], pads,
                           ["R1", "R2"])
    assert warnings == []
    assert units[0].net_name == "SHARED"
    assert net_conflicts(units[0]) == []


def test_pad_signature_is_the_unit_identity():
    """The matching key for a re-read is the SET of pads (design §11) — two
    different geometries between the same pads share it, a changed pad set does
    not."""
    a = _unit([PadRef("R1", "1"), PadRef("R2", "2")])
    b = _unit([PadRef("R2", "2"), PadRef("R1", "1")])
    c = _unit([PadRef("R1", "1"), PadRef("R3", "2")])
    assert a.pad_signature == b.pad_signature
    assert a.pad_signature != c.pad_signature


def test_pad_numbers_normalized_from_ints_and_floats():
    """A pad number read as float 2.0 names the same pad as '2'."""
    pads = {"R1": [_pad(2.0, 0, 0), _pad(2, 10, 0)]}
    raw = [_track((0, 0), (10, 0))]
    units, _ = _run(raw, pads, ["R1"])
    assert units[0].pads == (PadRef("R1", "2"),)
