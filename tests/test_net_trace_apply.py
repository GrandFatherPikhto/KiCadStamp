# tests/test_net_trace_apply.py
"""
Mock-adapter tests for the net_traces apply/redraw path (net_trace_planner.py
+ apply_pipeline wiring) — plan 2026_08_21_net_traces.md §3.2:
  - anchor moved between runs -> tracks/vias recompute to the NEW position;
  - repeated run without changes -> 0 new operations (idempotency through the
    registry, plus the one-time adoption of existing hand-routed copper);
  - retired/skip -> the record creates/touches no copper;
  - --only=<net> redraws exactly one record, not the whole config.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.domain.geometry import Vector2
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import Config, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.net_trace_planner import plan_net_traces, net_trace_anchor_id, adopt_net_trace_copper
from kicadstamp.registry import PlacementRegistry, TrackRegistry
from kicadstamp.apply_pipeline import (apply_only_filter, apply_cluster_filter,
                                       drop_inactive_items, _compute_all_anchor_ids)
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.utils.units import MM


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


def _make_live_track(sx, sy, ex, ey, net, width=0.25, layer=BoardLayer.BL_F_Cu, uuid="trk"):
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
    """NetTrace whose anchor pad sits at (anchor_x_mm, anchor_y_mm); local
    track (1,2)->(3,4), local via (5,6)."""
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
    """Adapter whose FPGA (role) has its pad 42 at the given anchor point and
    whose board carries the given live tracks/vias. angle_deg is the anchor
    footprint's OWN live rotation (for rotation-aware net traces)."""
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


# ── planning / following the anchor ───────────────────────────────────────────


def test_anchor_moved_between_runs_recomputes_positions():
    nt = _net_trace()
    adapter = _adapter(52, 52)
    vias, tracks = plan_net_traces(adapter, [nt])
    assert tracks[0].start.x / MM == 53.0 and tracks[0].start.y / MM == 54.0
    assert vias[0].position.x / MM == 57.0 and vias[0].position.y / MM == 58.0

    # Anchor pad moved to (72, 72) between runs.
    adapter2 = _adapter(72, 72)
    vias2, tracks2 = plan_net_traces(adapter2, [nt])
    assert tracks2[0].start.x / MM == 73.0 and tracks2[0].start.y / MM == 74.0
    assert tracks2[0].end.x / MM == 75.0 and tracks2[0].end.y / MM == 76.0
    assert vias2[0].position.x / MM == 77.0 and vias2[0].position.y / MM == 78.0


def test_registry_key_structure():
    nt = _net_trace()
    vias, tracks = plan_net_traces(_adapter(52, 52), [nt])
    assert net_trace_anchor_id(nt) == "net:DAC_DB0"
    assert tracks[0].registry_key == f"net:DAC_DB0|DAC_DB0|{SPOKE_LEVEL_ROLE_PLACEHOLDER}|0"
    assert vias[0].registry_key == f"net:DAC_DB0|DAC_DB0|{SPOKE_LEVEL_ROLE_PLACEHOLDER}|0"


def test_retired_and_skip_plan_nothing():
    retired = _net_trace()
    retired.retired = True
    skipped = _net_trace()
    skipped.skip = True
    active = _net_trace()
    adapter = _adapter(52, 52)
    vias, tracks = plan_net_traces(adapter, [retired, skipped, active])
    # Only the active record produces commands.
    assert len(tracks) == 1
    assert len(vias) == 1
    # net_trace_anchor_id still includes non-retired records only.
    assert _compute_all_anchor_ids(Config(net_traces=[retired, active])) == {"net:DAC_DB0"}


# ── filters ───────────────────────────────────────────────────────────────────


def test_only_filter_redraws_single_net():
    nt0 = _net_trace()
    nt1 = _net_trace()
    nt1.net = "DAC_DB1"
    cfg = Config(net_traces=[nt0, nt1])
    only = apply_only_filter(cfg, ["DAC_DB0"])
    assert [nt.net for nt in only.net_traces] == ["DAC_DB0"]


def test_only_filter_unknown_name_fatal():
    cfg = Config(net_traces=[_net_trace()])
    with pytest.raises(Exception, match="DAC_DB9"):
        apply_only_filter(cfg, ["DAC_DB9"])


def test_skip_filter_drops_but_keeps_registry_protection():
    skipped = _net_trace()
    skipped.skip = True
    active = _net_trace()
    active.net = "DAC_DB1"
    cfg = Config(net_traces=[skipped, active])
    out = drop_inactive_items(cfg)
    assert [nt.net for nt in out.net_traces] == ["DAC_DB1"]
    # Skipped record still protected in known_anchor_ids (not retired).
    assert _compute_all_anchor_ids(cfg) == {"net:DAC_DB0", "net:DAC_DB1"}


# ── registry adoption / idempotency ──────────────────────────────────────────


def _registries(adapter, tmp_path):
    vreg = PlacementRegistry(adapter, str(tmp_path / "v.registry.json"))
    treg = TrackRegistry(adapter, str(tmp_path / "t.registry.json"))
    return vreg, treg


def test_adoption_claims_existing_copper_then_reconcile_skips(tmp_path):
    nt = _net_trace()
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="existing-trk")],
        live_vias=[_make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="existing-via")],
    )
    vias, tracks = plan_net_traces(adapter, [nt])
    vreg, treg = _registries(adapter, tmp_path)

    adopt_net_trace_copper(adapter, vreg, treg, vias, tracks)
    assert list(vreg.entries.values())[0].uuid == "existing-via"
    assert list(treg.entries.values())[0].uuid == "existing-trk"

    # Reconcile with a full registry now sees everything "already correctly
    # placed" -> 0 new operations (the idempotency contract of §3.2).
    to_create_v, to_delete_v = vreg.reconcile(vias, known_anchor_ids={"net:DAC_DB0"})
    to_create_t, to_delete_t = treg.reconcile(tracks, known_anchor_ids={"net:DAC_DB0"})
    assert to_create_v == []
    assert to_create_t == []
    assert to_delete_v == []
    assert to_delete_t == []


def test_anchor_moved_reconcile_deletes_and_recreates(tmp_path):
    """The claimed UUID is carried across runs (persisted registry); after the
    anchor moves, reconcile deletes the old UUID and returns to_create. One
    adapter object whose board state (anchor position, live copper) mutates
    between runs — the same object the persisted registries are bound to."""
    nt = _net_trace()

    # Mutable board state, controlled from the test.
    fpga = _make_fp("U1", "FPGA", 50, 50)
    pad42 = _make_pad(52, 52)
    old_track = _make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="existing-trk")
    old_via = _make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="existing-via")
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: pad42 if num == "42" else None
    adapter.get_tracks.return_value = [old_track]
    adapter.get_vias.return_value = [old_via]

    # Run 1: anchor at (52,52), copper present and claimed.
    vias, tracks = plan_net_traces(adapter, [nt])
    vreg, treg = _registries(adapter, tmp_path)
    adopt_net_trace_copper(adapter, vreg, treg, vias, tracks)
    assert list(vreg.entries.values())[0].uuid == "existing-via"

    # Run 2: anchor moved to (72,72); the previously-claimed copper is STILL on
    # the board (orphaned at the old position) — exactly the real "moved the
    # anchor, tracks left hanging" scenario the feature exists for.
    fpga.position = Vector2.from_xy(int(70 * MM), int(70 * MM))
    pad42.position = Vector2.from_xy(int(72 * MM), int(72 * MM))
    vias2, tracks2 = plan_net_traces(adapter, [nt])

    to_create_v, to_delete_v = vreg.reconcile(vias2, known_anchor_ids={"net:DAC_DB0"})
    to_create_t, to_delete_t = treg.reconcile(tracks2, known_anchor_ids={"net:DAC_DB0"})
    assert len(to_create_v) == 1
    assert len(to_create_t) == 1
    assert to_create_v[0].position.x / MM == 77.0
    assert to_create_t[0].start.x / MM == 73.0
    # The previously-claimed old copper is deleted (no orphaned duplicate).
    assert to_delete_v == ["existing-via"]
    assert to_delete_t == ["existing-trk"]


def test_adoption_never_steals_foreign_owned_copper(tmp_path):
    """A live item already owned by ANOTHER registry key must not be claimed."""
    nt = _net_trace()
    adapter = _adapter(
        52, 52,
        live_tracks=[_make_live_track(53, 54, 55, 56, "DAC_DB0", 0.2, uuid="owned-by-rule")],
        live_vias=[_make_live_via(57, 58, "DAC_DB0", 0.3, 0.6, uuid="owned-by-rule-via")],
    )
    vias, tracks = plan_net_traces(adapter, [nt])
    vreg, treg = _registries(adapter, tmp_path)
    # Simulate another mechanism already owning the copper.
    treg.entries["anchor:U1|cell|None|0"] = treg._build_entry(tracks[0], "owned-by-rule")
    vreg.entries["anchor:U1|cell|None|0"] = vreg._build_entry(vias[0], "owned-by-rule-via")

    adopt_net_trace_copper(adapter, vreg, treg, vias, tracks)
    # Net-trace keys must NOT have been added (uuid already owned).
    assert tracks[0].registry_key not in treg.entries
    assert vias[0].registry_key not in vreg.entries


# ── apply_cluster_filter (--cluster) for net_traces ──────────────────────────
# (review fix 2026-08-21: the --cluster branch for net_traces was added but
# never tested — only --only was covered.)


def _nt(net, cluster=None, retired=False):
    nt = _net_trace()
    nt.net = net
    nt.anchor_cluster = cluster
    nt.retired = retired
    return nt


def test_cluster_filter_matches_by_anchor_cluster():
    cfg = Config(net_traces=[_nt("DAC_DB0", "Channel_0"),
                             _nt("DAC_DB1", "Channel_1")])
    out = apply_cluster_filter(cfg, ["Channel_0"])
    assert [nt.net for nt in out.net_traces] == ["DAC_DB0"]


def test_cluster_filter_prefix_match():
    """Segment-prefix match: 'Channel_0' also selects 'Channel_0/DAC'."""
    cfg = Config(net_traces=[_nt("DAC_DB0", "Channel_0/DAC"),
                             _nt("DAC_DB1", "Channel_1/DAC")])
    out = apply_cluster_filter(cfg, ["Channel_0"])
    assert [nt.net for nt in out.net_traces] == ["DAC_DB0"]


def test_cluster_filter_excludes_retired():
    cfg = Config(net_traces=[_nt("DAC_DB0", "Channel_0"),
                             _nt("DAC_DB1", "Channel_0", retired=True)])
    out = apply_cluster_filter(cfg, ["Channel_0"])
    assert [nt.net for nt in out.net_traces] == ["DAC_DB0"]


def test_cluster_filter_matched_nothing_fatal():
    cfg = Config(net_traces=[_nt("DAC_DB0", "Channel_0")])
    with pytest.raises(PlacerError, match="matched nothing"):
        apply_cluster_filter(cfg, ["Channel_9"])


def test_cluster_filter_composes_with_only_via_and():
    """--cluster AND --only (both are AND-narrowing, not OR)."""
    cfg = Config(net_traces=[_nt("DAC_DB0", "Channel_0"),
                             _nt("DAC_DB1", "Channel_0"),
                             _nt("DAC_DB2", "Channel_1")])
    clustered = apply_cluster_filter(cfg, ["Channel_0"])
    only = apply_only_filter(clustered, ["DAC_DB1"])
    assert [nt.net for nt in only.net_traces] == ["DAC_DB1"]


# ── rotation-aware apply (plan_2026_09_08_net_trace_rotation_aware.md) ───────


def test_rotation_apply_uses_delta_between_captured_and_live_angle():
    """Live-shaped scenario: captured at 180 deg, apply resolves the anchor at
    90 deg -> every track/via position matches the MANUAL
    relative_rotation_deg(90, 180)-based computation, NOT the old
    rotation_deg=0.0 one (proves the rotation is really applied)."""
    from kicadstamp.geometry.spoke_layout import local_to_absolute
    from kicadstamp.tree_position import relative_rotation_deg

    nt = _net_trace()
    nt.anchor_rotation_deg = 180.0
    adapter = _adapter(52, 52, angle_deg=90.0)  # live anchor now at 90 deg
    anchor = Vector2.from_xy(int(52 * MM), int(52 * MM))  # pad42 centre

    vias, tracks = plan_net_traces(adapter, [nt])

    rot = relative_rotation_deg(90.0, 180.0)
    assert tracks[0].start == local_to_absolute(anchor, 1, 2, rot)
    assert tracks[0].end == local_to_absolute(anchor, 3, 4, rot)
    assert vias[0].position == local_to_absolute(anchor, 5, 6, rot)
    # Proves rotation is applied, not merely stored: the result differs from
    # the pre-fix rotation_deg=0.0 geometry for at least one non-zero-relative
    # axis (90-180 = -90 deg -> (1,2) local lands off-axis, so start differs).
    assert tracks[0].start != local_to_absolute(anchor, 1, 2, 0.0)


def test_rotation_apply_legacy_without_field_uses_rotation_zero():
    """100% back-compat: a legacy record (anchor_rotation_deg=None) applies
    with rotation_deg=0.0 EXACTLY as before the fix, even when the live anchor
    sits at a nonzero angle — existing profiles never replay silently."""
    from kicadstamp.geometry.spoke_layout import local_to_absolute
    nt = _net_trace()  # anchor_rotation_deg stays None (default)
    assert nt.anchor_rotation_deg is None
    adapter = _adapter(52, 52, angle_deg=90.0)  # live anchor at 90 deg
    anchor = Vector2.from_xy(int(52 * MM), int(52 * MM))

    vias, tracks = plan_net_traces(adapter, [nt])

    # Identical to today's (pre-fix) geometry: pure translation.
    assert tracks[0].start == local_to_absolute(anchor, 1, 2, 0.0)
    assert tracks[0].end == local_to_absolute(anchor, 3, 4, 0.0)
    assert vias[0].position == local_to_absolute(anchor, 5, 6, 0.0)


def test_rotation_apply_matching_angles_produce_no_net_rotation():
    """Capture and apply under the SAME angle (both 90 deg) ->
    relative_rotation_deg gives 0 -> geometry identical to today's (no
    "extra" rotation where there shouldn't be any)."""
    from kicadstamp.geometry.spoke_layout import local_to_absolute

    nt = _net_trace()
    nt.anchor_rotation_deg = 90.0
    adapter = _adapter(52, 52, angle_deg=90.0)
    anchor = Vector2.from_xy(int(52 * MM), int(52 * MM))

    vias, tracks = plan_net_traces(adapter, [nt])

    assert tracks[0].start == local_to_absolute(anchor, 1, 2, 0.0)
    assert tracks[0].end == local_to_absolute(anchor, 3, 4, 0.0)
    assert vias[0].position == local_to_absolute(anchor, 5, 6, 0.0)


def test_rotation_apply_track_rotates_as_one_whole():
    """A track's two endpoints rotate TOGETHER under a nonzero angle delta:
    the end-minus-start vector equals the SAME rotated local (2,2) delta —
    the segment keeps its own shape/length/orientation (not "each point
    somewhere rotated independently")."""
    from kicadstamp.geometry.spoke_layout import local_to_absolute
    from kicadstamp.tree_position import relative_rotation_deg

    nt = _net_trace()
    nt.anchor_rotation_deg = 0.0
    adapter = _adapter(52, 52, angle_deg=90.0)  # delta = +90
    anchor = Vector2.from_xy(int(52 * MM), int(52 * MM))

    vias, tracks = plan_net_traces(adapter, [nt])

    rot = relative_rotation_deg(90.0, 0.0)
    start = tracks[0].start
    end = tracks[0].end
    # Both endpoints individually match the manual rotation-based placement.
    assert start == local_to_absolute(anchor, 1, 2, rot)
    assert end == local_to_absolute(anchor, 3, 4, rot)
    # The segment as a WHOLE: end - start == the local delta (2,2) rotated by
    # the same rot -> shape/length preserved (a rigid body, not independent
    # points).
    rotated_delta = (local_to_absolute(Vector2.from_xy(0, 0), 3, 4, rot)
                     - local_to_absolute(Vector2.from_xy(0, 0), 1, 2, rot))
    assert end - start == rotated_delta


def _net_trace_with_layer(layer: str) -> NetTrace:
    """The same record as _net_trace(), with the track's copper layer
    parameterised (net trace tracks carry an ABSOLUTE layer — a net trace has
    no cell to inherit one from)."""
    return NetTrace(
        net="DAC_DB0", anchor_role="FPGA", anchor_pad="42",
        tracks=[TemplateTrack(start_along_mm=1, start_across_mm=2,
                              end_along_mm=3, end_across_mm=4, width_mm=0.2,
                              net="DAC_DB0", layer=layer)],
        vias=[TemplateVia(offset_along_mm=5, offset_across_mm=6, net="DAC_DB0",
                          drill_mm=0.3, diameter_mm=0.6)],
    )


class TestNetTraceTrackCopperLayer:
    """plan_2026_09_12_strict_copper_layers.md Э4.1/Э4.2 (net_traces path).

    A net trace's track layer is COPPER, so an inner layer must reach the
    TrackCommand as-is. Note the shape of the defect here is the REFUSING one,
    not the silent collapse: `_layer_to_board` already fatals on anything that
    is not F.Cu/B.Cu (the loader does too), so In1.Cu was simply impossible on
    this path.
    """

    def test_inner_layer_track_reaches_the_track_command(self):
        _vias, tracks = plan_net_traces(_adapter(52, 52),
                                        [_net_trace_with_layer("In1.Cu")])
        assert len(tracks) == 1
        assert tracks[0].layer is BoardLayer.BL_In1_Cu

    def test_front_and_back_unchanged(self):
        _v, front = plan_net_traces(_adapter(52, 52), [_net_trace_with_layer("F.Cu")])
        _v, back = plan_net_traces(_adapter(52, 52), [_net_trace_with_layer("B.Cu")])
        assert front[0].layer is BoardLayer.BL_F_Cu
        assert back[0].layer is BoardLayer.BL_B_Cu

    def test_unknown_layer_name_is_a_fatal_not_f_cu(self):
        """Э4.2 — a hand-built NetTrace that bypassed the loader must still be
        refused, never silently rerouted onto F.Cu."""
        with pytest.raises(ValidationError):
            plan_net_traces(_adapter(52, 52), [_net_trace_with_layer("Top.Cu")])
