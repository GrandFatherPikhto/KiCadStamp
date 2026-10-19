# tests/domain/test_micro_segment_consumers.py
"""The three consumers of the degenerate-segment rule (plan Ч1, 2026-10-19).

ONE rule (``kicadstamp/degenerate_segments.py``), three callers:

  1. "Дубли меди" — width joins the key, a degenerate segment never joins a
     group, is listed on its own line and is never deleted;
  2. ``track_matches`` — a degenerate live/planned segment never "matches", so
     the positional pre-check cannot silently skip copper that is missing;
  3. the read paths — a degenerate segment is not stored and its neighbours are
     pulled into one point (the 15.10 fact's bridges stay connected).

Each cell fails when its own call site loses the rule, which is what the three
acceptance mutations rely on.
"""
import math

from kicadstamp.board_dedupe import (find_short_tracks,
                                     find_track_duplicate_groups, format_report)
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.internode_copper import CopperUnit
from kicadstamp.internode_nodes import _items_from_unit
from kicadstamp.placement.commands import TrackCommand
from kicadstamp.registry import filter_existing_tracks, track_matches
from kicadstamp.utils.units import MM

NET = "+3V3_STM32"
LAYER = BoardLayer.BL_B_Cu


def _pt(x_mm: float, y_mm: float) -> Vector2:
    return Vector2(int(round(x_mm * MM)), int(round(y_mm * MM)))


def _track(uuid: str, sx: float, sy: float, ex: float, ey: float,
           width: float = 0.65, net: str = NET,
           layer: BoardLayer = LAYER) -> Track:
    return Track(uuid=uuid, start=_pt(sx, sy), end=_pt(ex, ey), net_name=net,
                 width_mm=width, layer=layer)


def _cmd(sx: float, sy: float, ex: float, ey: float,
         width: float = 0.65) -> TrackCommand:
    return TrackCommand(start=_pt(sx, sy), end=_pt(ex, ey), width_mm=width,
                        net_name=NET, layer=LAYER, owner_ref="probe")


def _fact_tracks() -> list[Track]:
    """The three segments of the 15.10 fact — exact registry geometry."""
    return [
        _track("A", 77.9784, 98.4459, 77.9742, 98.4416),   # 6.0 µm
        _track("B", 77.9742, 98.4416, 77.9827, 98.4416),   # 8.5 µm
        _track("C", 77.9784, 98.4459, 77.9843, 98.4400),   # 8.3 µm
    ]


def _components(segments) -> int:
    """Connected components of a set of (start, end) point pairs, ends within
    one positional tolerance (10 µm) being the same node."""
    tol = 10_000
    nodes: list[tuple[int, int]] = []
    parent: list[int] = []

    def node_of(point: tuple[int, int]) -> int:
        for index, existing in enumerate(nodes):
            if abs(existing[0] - point[0]) <= tol and abs(existing[1] - point[1]) <= tol:
                return index
        nodes.append(point)
        parent.append(len(nodes) - 1)
        return len(nodes) - 1

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for start, end in segments:
        a = node_of((start.x, start.y))
        b = node_of((end.x, end.y))
        if find(a) != find(b):
            parent[find(a)] = find(b)
    return len({find(i) for i in range(len(nodes))})


# ── 1. "Дубли меди" ─────────────────────────────────────────────────────────

def test_the_fact_is_no_duplicate_group_at_all():
    """"Найти" on the 15.10 fact: 0 groups. Before the rule this was ONE group
    of three with two members queued for deletion."""
    fact = _fact_tracks()
    assert find_track_duplicate_groups(fact) == []
    assert [t.uuid for t in find_short_tracks(fact)] == ["A", "B", "C"]


def test_the_report_lists_short_segments_and_deletes_nothing():
    fact = _fact_tracks()
    report = format_report([], find_track_duplicate_groups(fact),
                           find_short_tracks(fact))
    assert "No copper duplicates found." in report
    assert "Short track segments (not duplicates, nothing deleted): 3" in report
    assert "    delete " not in report       # no member is queued for deletion
    for track in fact:
        assert f"keep {track.uuid}" in report


def test_width_is_part_of_the_group_key():
    """Same ends, different widths — two different pieces of copper."""
    pair = [_track("w1", 20.0, 20.0, 22.0, 20.0, width=0.25),
            _track("w2", 20.0, 20.0, 22.0, 20.0, width=0.65)]
    assert find_track_duplicate_groups(pair) == []


def test_real_duplicates_are_still_found():
    pair = [_track("n1", 10.0, 10.0, 12.0, 10.0),
            _track("n2", 10.0, 10.0, 12.0, 10.0)]
    groups = find_track_duplicate_groups(pair)
    assert len(groups) == 1
    assert [t.uuid for t in groups[0]] == ["n1", "n2"]


def test_boundary_19_20_21_um():
    """Strictly BELOW 20 µm is unidentifiable: 19 pairs only as short lines,
    20 and 21 are ordinary duplicates."""
    def pair(length_um: float):
        return [_track("b1", 40.0, 40.0, 40.0 + length_um / 1000.0, 40.0),
                _track("b2", 40.0, 40.0, 40.0 + length_um / 1000.0, 40.0)]

    assert len(find_track_duplicate_groups(pair(19.0))) == 0
    assert len(find_short_tracks(pair(19.0))) == 2
    assert len(find_track_duplicate_groups(pair(20.0))) == 1
    assert len(find_track_duplicate_groups(pair(21.0))) == 1


# ── 2. track_matches ───────────────────────────────────────────────────────

def test_positional_precheck_never_pairs_a_degenerate_command():
    """All six ordered pairs of the fact: the POSITIONAL pre-check may not
    recognise any of them (acceptance Д-1 — the guard lives here, not in the
    shared predicate, because by uuid the segment IS known)."""
    fact = _fact_tracks()
    for planned in fact:
        cmd = _cmd(planned.start.x / MM, planned.start.y / MM,
                   planned.end.x / MM, planned.end.y / MM)
        for live in fact:
            assert filter_existing_tracks([cmd], [live]) == [cmd], (
                f"{live.uuid} must not be accepted as {planned.uuid}")


def test_positional_precheck_ignores_a_degenerate_neighbour():
    """A stub CAN answer for a NORMAL command positionally — a 15 µm stub
    against a 24 µm command puts both ends inside the tolerance, same net,
    width and layer — so the pre-check must never take a stub into its pool
    (otherwise real copper would be silently skipped)."""
    stub = _track("stub", 10.0, 20.0, 10.015, 20.0, width=0.25)
    normal = _cmd(10.0, 20.0, 10.024, 20.0, width=0.25)
    assert filter_existing_tracks([normal], [stub]) == [normal]


def test_positional_precheck_returns_a_degenerate_command_against_a_normal_live():
    """MIRROR of the cell above (acceptance Д-4): here the COMMAND is the stub
    (15 µm) and the live track is normal (24 µm) — same net, width and layer,
    both command ends inside the tolerance. Without the command-side check the
    pre-check would pair them and silently skip the command; the command must
    come back instead."""
    live = _track("live", 10.0, 20.0, 10.024, 20.0, width=0.25)
    stub_cmd = _cmd(10.0, 20.0, 10.015, 20.0, width=0.25)
    assert filter_existing_tracks([stub_cmd], [live]) == [stub_cmd]


def test_track_matches_still_compares_geometry_for_the_uuid_path():
    """The shared predicate keeps its pre-Д-1 behaviour: it is the uuid path's
    comparator, and there the geometry of a degenerate segment IS usable."""
    fact = _fact_tracks()
    live = fact[0]
    assert track_matches(live, _cmd(77.9784, 98.4459, 77.9742, 98.4416))


def test_ordinary_segments_still_match():
    live = _track("ok", 10.0, 20.0, 30.0, 40.0, net="+3V3", width=0.25,
                  layer=BoardLayer.BL_F_Cu)
    assert track_matches(live, TrackCommand(
        start=_pt(10.0, 20.0), end=_pt(30.0, 40.0), width_mm=0.25,
        net_name="+3V3", layer=BoardLayer.BL_F_Cu, owner_ref="U1"))


# ── 3. the read path: not written, neighbours pulled together ──────────────

def test_items_from_unit_drops_the_stub_and_keeps_the_chain_connected():
    anchor = _pt(0.0, 0.0)
    unit = CopperUnit(tracks=[
        _track("left", 0.0, 0.0, 1.0, 0.0),
        _track("stub", 1.0, 0.0, 1.008, 0.0),   # 8 µm
        _track("right", 1.008, 0.0, 2.0, 0.0),
    ])
    tracks, _vias = _items_from_unit(unit, {}, anchor, NET, named=False)

    assert len(tracks) == 2
    expected_mid = 1.004
    assert math.isclose(tracks[0].end_along_mm, expected_mid, abs_tol=1e-6)
    assert math.isclose(tracks[1].start_along_mm, expected_mid, abs_tol=1e-6)


def test_items_from_unit_pulls_the_stub_ends_onto_a_touched_via():
    anchor = _pt(0.0, 0.0)
    unit = CopperUnit(
        tracks=[_track("left", 0.0, 0.0, 1.0, 0.0),
                _track("stub", 1.0, 0.0, 1.008, 0.0),
                _track("right", 1.008, 0.0, 2.0, 0.0)],
        vias=[Via(uuid="v", position=_pt(1.0, 0.0), net_name=NET,
                  drill_mm=0.3, diameter_mm=0.6)])
    tracks, _vias = _items_from_unit(unit, {}, anchor, NET, named=False)

    assert len(tracks) == 2
    assert math.isclose(tracks[0].end_along_mm, 1.0, abs_tol=1e-6)
    assert math.isclose(tracks[1].start_along_mm, 1.0, abs_tol=1e-6)


def test_connectivity_of_the_fact_bridges_survives_the_collapse():
    """The bridge segments of Ч0 (spoke_vdd_03 7/21, nrst 0/11,
    heater_led_02 10/11/15): collapse keeps ONE component, before and after."""
    def chain(stub_um: float):
        stub_nm = int(round(stub_um * 1000.0))
        return [
            (Vector2(0, 0), Vector2(1_000_000, 0)),
            (Vector2(1_000_000, 0), Vector2(1_000_000 + stub_nm, 0)),
            (Vector2(1_000_000 + stub_nm, 0), Vector2(2_000_000, 0)),
        ]

    from kicadstamp.degenerate_segments import collapse_degenerate_segments

    for stub_um in (2.0, 8.3, 10.0):
        segments = chain(stub_um)
        before = _components(segments)
        result = collapse_degenerate_segments(segments)
        assert before == 1
        assert _components(result.segments) == before
        assert len(result.segments) == 2
