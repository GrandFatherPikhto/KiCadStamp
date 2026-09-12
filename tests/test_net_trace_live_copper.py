# tests/test_net_trace_live_copper.py
"""
E1 of plan_2026_09_12_select_copper_by_record: the READ-ONLY matching half of
the net_traces apply path (net_trace_planner.match_net_trace_pieces /
find_live_copper), and the "one mechanism" contract that it and
adopt_net_trace_copper find the SAME live copper for the SAME record.

Covers (plan E4):
  * tier 1 (registry uuid) finds copper;
  * tier 1 runs BEFORE tier 2 (registry wins over an equally-matching geometry);
  * tier 2 (geometry) finds copper the registry does not know;
  * tier 2 never steals a uuid owned by another record;
  * a partial find returns what was found AND the missing count;
  * an unresolvable anchor is an honest "nothing to match geometry with",
    never an exception;
  * find_live_copper writes nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.config import Config, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.net_trace_planner import (
    TIER_GEOMETRY,
    TIER_REGISTRY,
    TRACK,
    VIA,
    adopt_net_trace_copper,
    find_live_copper,
    net_trace_anchor_id,
    plan_net_traces,
)
from kicadstamp.registry import (
    PlacementRegistry,
    RegistryEntry,
    TrackRegistry,
    load_registry,
    load_track_registry,
    make_registry_key,
)
from kicadstamp.utils.units import MM


# ── fixtures ────────────────────────────────────────────────────────────────

def _make_fp(ref, role, x_mm, y_mm, angle_deg=0.0):
    fp = MagicMock()
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.angle_deg = angle_deg
    fp._role = role
    return fp


def _make_pad(x_mm, y_mm):
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return pad


def _make_live_track(sx, sy, ex, ey, net, width=0.25,
                     layer=BoardLayer.BL_F_Cu, uuid="trk"):
    t = MagicMock()
    t.net_name = net
    t.start = Vector2.from_xy(int(sx * MM), int(sy * MM))
    t.end = Vector2.from_xy(int(ex * MM), int(ey * MM))
    t.width_mm = width
    t.layer = layer
    t.uuid = uuid
    return t


def _make_live_via(x, y, net, drill=0.3, dia=0.6, uuid="via"):
    v = MagicMock()
    v.net_name = net
    v.position = Vector2.from_xy(int(x * MM), int(y * MM))
    v.drill_mm = drill
    v.diameter_mm = dia
    v.uuid = uuid
    return v


def _net_trace(anchor_x_mm=52.0, anchor_y_mm=52.0):
    """One track (local 1,2 -> 3,4) and one via (local 5,6) anchored on pad 42
    of role FPGA at the given point. Absolute: track (53,54)->(55,56),
    via (57,58)."""
    return NetTrace(
        net="DAC_DB0", anchor_role="FPGA", anchor_pad="42",
        tracks=[TemplateTrack(start_along_mm=1, start_across_mm=2,
                              end_along_mm=3, end_across_mm=4, width_mm=0.2,
                              net="DAC_DB0", layer="F.Cu")],
        vias=[TemplateVia(offset_along_mm=5, offset_across_mm=6, net="DAC_DB0",
                          drill_mm=0.3, diameter_mm=0.6)],
    )


def _adapter(anchor_x_mm, anchor_y_mm, live_tracks=(), live_vias=(),
             angle_deg=0.0):
    fpga = _make_fp("U1", "FPGA", anchor_x_mm - 2, anchor_y_mm - 2,
                    angle_deg=angle_deg)
    pad42 = _make_pad(anchor_x_mm, anchor_y_mm)
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: pad42 if num == "42" else None
    adapter.get_tracks.return_value = list(live_tracks)
    adapter.get_vias.return_value = list(live_vias)
    return adapter


def _registries(adapter, tmp_path):
    return (PlacementRegistry(adapter, str(tmp_path / "v.registry.json")),
            TrackRegistry(adapter, str(tmp_path / "t.registry.json")))


def _uuid_set(items):
    return {i.uuid for i in items}


def _pieces_of(result, kind):
    return [p for p in result.pieces if p.expectation.kind == kind]


# ── tier 1: registry uuid ────────────────────────────────────────────────────

def test_tier1_finds_copper_by_registry_uuid(tmp_path):
    """The registry's stored uuid resolves to a live item even when that item's
    geometry does NOT match the plan (repositioned by hand) — the registry is
    the exact answer."""
    nt = _net_trace()
    live_track = _make_live_track(99, 99, 98, 98, "DAC_DB0", 0.2, uuid="stored-trk")
    adapter = _adapter(52, 52, live_tracks=[live_track])
    vreg, treg = _registries(adapter, tmp_path)
    _, tracks = plan_net_traces(adapter, [nt])
    treg.entries[tracks[0].registry_key] = treg._build_entry(tracks[0], "stored-trk")

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    found = result.found
    assert _uuid_set(found) == {"stored-trk"}
    assert result.found_by_registry == 1
    assert result.found_by_geometry == 0
    assert result.missing_count == 1  # the via is not on the board


def test_tier1_runs_before_tier2(tmp_path):
    """Strict order: with a registry hit for the planned geometry AND an unowned
    live item at exactly that geometry, the REGISTRY item wins."""
    nt = _net_trace()
    # Geometry that does NOT match the plan, but is the registry's answer.
    registry_track = _make_live_track(10, 10, 11, 11, "DAC_DB0", 0.2,
                                      uuid="registry-answer")
    # A decoy that DOES match the plan and is unowned.
    decoy = _make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="decoy")
    adapter = _adapter(52, 52, live_tracks=[registry_track, decoy])
    vreg, treg = _registries(adapter, tmp_path)
    _, tracks = plan_net_traces(adapter, [nt])
    treg.entries[tracks[0].registry_key] = treg._build_entry(tracks[0], "registry-answer")

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    track_pieces = _pieces_of(result, TRACK)
    assert len(track_pieces) == 1
    assert track_pieces[0].live.uuid == "registry-answer"
    assert track_pieces[0].tier == TIER_REGISTRY


# ── tier 2: geometry ─────────────────────────────────────────────────────────

def test_tier2_finds_copper_geometry_when_registry_empty(tmp_path):
    nt = _net_trace()
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="hand-trk")],
        live_vias=[_make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="hand-via")])
    vreg, treg = _registries(adapter, tmp_path)

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert _uuid_set(result.found) == {"hand-trk", "hand-via"}
    assert result.found_by_registry == 0
    assert result.found_by_geometry == 2
    assert result.missing_count == 0
    assert result.tiers == {TIER_GEOMETRY}


def test_tier2_never_steals_other_records_copper(tmp_path):
    """A live item matching the plan but owned by ANOTHER registry key is left
    alone — it is somebody else's copper."""
    nt = _net_trace()
    live_track = _make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="other-owned")
    adapter = _adapter(52, 52, live_tracks=[live_track])
    vreg, treg = _registries(adapter, tmp_path)
    _, tracks = plan_net_traces(adapter, [nt])
    treg.entries["net:SOMEBODY_ELSE|cell|None|0"] = treg._build_entry(
        tracks[0], "other-owned")

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert result.found == []
    assert result.missing_count == 2


def test_partial_find_returns_found_and_missing(tmp_path):
    """Two tracks expected, one exists — a normal partial answer, not an error."""
    nt = _net_trace()
    nt.vias = []
    nt.tracks = [
        TemplateTrack(start_along_mm=1, start_across_mm=2,
                      end_along_mm=3, end_across_mm=4, width_mm=0.2,
                      net="DAC_DB0", layer="F.Cu"),
        TemplateTrack(start_along_mm=10, start_across_mm=10,
                      end_along_mm=11, end_across_mm=11, width_mm=0.2,
                      net="DAC_DB0", layer="F.Cu"),
    ]
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="present")])
    vreg, treg = _registries(adapter, tmp_path)

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert result.expected_count == 2
    assert _uuid_set(result.found) == {"present"}
    assert result.missing_count == 1


def test_unresolvable_anchor_is_honest_not_exception(tmp_path):
    """No role match on the board -> tier 2 has nothing to match against; the
    call still returns, with `reason` set and the registry tier intact."""
    nt = _net_trace()
    adapter = _adapter(52, 52)
    # No footprint carries the FPGA role any more.
    adapter.get_footprints.return_value = []
    adapter.get_field_value.side_effect = lambda fp, name: None
    vreg, treg = _registries(adapter, tmp_path)
    # The registry still knows the via's uuid.
    live_via = _make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="known-via")
    adapter.get_vias.return_value = [live_via]
    vreg.entries[make_registry_key(net_trace_anchor_id(nt), "DAC_DB0", None, 0)] = \
        RegistryEntry(uuid="known-via", x_mm=57.0, y_mm=58.0, net="DAC_DB0",
                      drill_mm=0.3, diameter_mm=0.6)

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert result.reason is not None
    assert _uuid_set(result.found) == {"known-via"}
    assert result.missing_count == 1


def test_find_live_copper_writes_nothing(tmp_path):
    nt = _net_trace()
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="hand-trk")])
    via_path = str(tmp_path / "v.registry.json")
    trk_path = str(tmp_path / "t.registry.json")
    vreg, treg = _registries(adapter, tmp_path)

    find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert vreg.entries == {}
    assert treg.entries == {}
    assert not Path(via_path).exists()
    assert not Path(trk_path).exists()
    assert load_registry(via_path) == {}
    assert load_track_registry(trk_path) == {}


# ── the "one mechanism" contract ─────────────────────────────────────────────

def test_find_and_adopt_find_the_same_copper(tmp_path):
    """The key test of plan E4: find_live_copper and adopt_net_trace_copper must
    identify the SAME live copper for the SAME record — one matching mechanism,
    not two."""
    nt = _net_trace()
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="hand-trk")],
        live_vias=[_make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="hand-via")])
    vreg, treg = _registries(adapter, tmp_path)

    # Read-only first (must not affect the board/registries).
    found = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)
    assert _uuid_set(found.found) == {"hand-trk", "hand-via"}

    # Then the ownership claim on the SAME board.
    vias, tracks = plan_net_traces(adapter, [nt])
    adopt_net_trace_copper(adapter, vreg, treg, vias, tracks)

    adopted = {e.uuid for e in (*vreg.entries.values(), *treg.entries.values())}
    assert adopted == _uuid_set(found.found)

    # And now the read path finds exactly those by registry, not geometry.
    after = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)
    assert _uuid_set(after.found) == adopted
    assert after.found_by_geometry == 0
    assert after.found_by_registry == 2


def test_retired_record_reports_reason_and_registry_only(tmp_path):
    nt = _net_trace()
    nt.retired = True
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="hand-trk")])
    vreg, treg = _registries(adapter, tmp_path)

    result = find_live_copper(adapter, nt, via_registry=vreg, track_registry=treg)

    assert result.reason is not None
    assert result.found == []           # geometry tier deliberately not run
    assert result.missing_count == 2


def test_live_copper_imports_are_public():
    """The names E2/E3 build on must be importable from the module."""
    from kicadstamp.net_trace_planner import (  # noqa: F401
        Expectation, LiveCopper, MatchedCopper, match_net_trace_pieces,
    )
    assert SimpleNamespace is not None
