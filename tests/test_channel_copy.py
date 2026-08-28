# tests/test_channel_copy.py
"""
Tests for kicadstamp/channel_copy.py — variant B of the A/B/C triple
(plan_2026_08_15_channel_copy.md): live copy of a whole channel's placement
(components + vias + tracks) via a twin map built from sheet_path UUID chains.

Covers (unified plan, Stage 4):
  - Task 4.1 unit: transform math vs rotate_local_offset (and, for --mirror,
    vs _mirror_x / (180°-phi) mod 360°), twin-map building from paths, net
    mapping (twin_net), idempotency (skip already-in-place);
  - Task 4.2 mock-adapter: full plan -> execute roundtrip through the real
    BatchExecutor (no registry), foreign report, --include-global, --mirror
    (layers inverted on every element);
  - Task 4.3: double run on one dst produces NO duplicates.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance, Via, Track
from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.geometry import Vector2

from kicadstamp.config import Config
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.clone_geometry import _mirror_x
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.utils.units import MM

from kicadstamp.channel_copy import (
    ChannelTransform,
    build_channel_groups,
    build_live_twin_map,
    channel_copy,
    execute_channel_copy,
    format_channel_copy_report,
    plan_channel_copy,
    resolve_channel_uuids,
    resolve_transform,
    transform_angle,
    transform_layer,
    transform_point,
    verify_channel_copy_nets,
)

# ── fixtures: a three-channel board (Channel_0/1), one "IC" and one "C" each ──

CH0 = "ch-uuid-0"
CH1 = "ch-uuid-1"
SUB = "sub-uuid"
IC_SYM = "ic-sym-uuid"
C_SYM = "c-sym-uuid"

# inner keys: "/sub-uuid/ic-sym-uuid" (IC) and "/sub-uuid/c-sym-uuid" (C)


def _pad(net_name):
    p = MagicMock()
    p.net = MagicMock()
    p.net_name = net_name
    return p


def _fp(ref, x_mm, y_mm, angle_deg, layer, path, pad_nets=(), role=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.angle_deg = angle_deg
    fp.layer = layer
    fp.sheet_path_uuids = tuple(path)
    fp._pad_nets = list(pad_nets)
    fp._role = role
    fp.uuid = f"uuid-{ref}"
    return fp


def _via(x_mm, y_mm, net_name, drill_mm=0.3, diameter_mm=0.6):
    v = MagicMock(spec=Via)
    v.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    v.net = MagicMock()
    v.net_name = net_name
    v.drill_mm = drill_mm
    v.diameter_mm = diameter_mm
    v.uuid = f"via-{net_name}-{x_mm}-{y_mm}"
    return v


def _track(x1, y1, x2, y2, net_name, width_mm=0.25, layer=BoardLayer.BL_F_Cu):
    t = MagicMock(spec=Track)
    t.start = Vector2.from_xy(int(x1 * MM), int(y1 * MM))
    t.end = Vector2.from_xy(int(x2 * MM), int(y2 * MM))
    t.net = MagicMock()
    t.net_name = net_name
    t.width_mm = width_mm
    t.layer = layer
    t.uuid = f"track-{net_name}-{x1}-{y1}"
    return t


def _channel_fps():
    return [
        _fp("IC0", 10, 10, 0, BoardLayer.BL_F_Cu, [CH0, SUB, IC_SYM],
            pad_nets=["/Channel_0/DAC/+3V3_AVDD", "/Channel_0/DAC/GND"], role="AD_DAC"),
        _fp("C0", 12, 14, 0, BoardLayer.BL_F_Cu, [CH0, SUB, C_SYM],
            pad_nets=["/Channel_0/DAC/+3V3_AVDD"], role="C_BYPASS"),
        _fp("IC1", 50, 50, 0, BoardLayer.BL_F_Cu, [CH1, SUB, IC_SYM],
            pad_nets=["/Channel_1/DAC/+3V3_AVDD", "/Channel_1/DAC/GND"], role="AD_DAC"),
        _fp("C1", 5, 5, 0, BoardLayer.BL_F_Cu, [CH1, SUB, C_SYM],
            pad_nets=["/Channel_1/DAC/+3V3_AVDD"], role="C_BYPASS"),
    ]


def _adapter(fps, vias=None, tracks=None):
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_vias.return_value = vias or []
    adapter.get_tracks.return_value = tracks or []
    by_ref = {fp.ref: fp for fp in fps}

    def pads(fp):
        return [_pad(n) for n in fp._pad_nets]

    def field_value(fp, name):
        return fp._role if name == "Role" else None

    adapter.get_footprint_pads.side_effect = pads
    adapter.get_field_value.side_effect = field_value
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_pad_by_number.side_effect = lambda fp, num: None
    return adapter


def _transform(angle_deg=0.0, mirror=False):
    """anchor_src = IC0 (10,10), anchor_dst = IC1 (50,50) — a plain +40/+40
    shift of the whole construction (matches the pivot-mode default)."""
    return ChannelTransform(anchor_src=Vector2.from_xy_mm(10, 10),
                            anchor_dst=Vector2.from_xy_mm(50, 50),
                            angle_deg=angle_deg, mirror=mirror)


def _plan_for(fps, vias=None, tracks=None, *, angle=0.0, mirror=False,
              include_global=False):
    """Plan Channel_0 -> Channel_1 with pivot IC0, no offset."""
    adapter = _adapter(fps, vias=vias, tracks=tracks)
    src_uuid, groups = build_live_twin_map(adapter, "IC0")
    uuids = resolve_channel_uuids(adapter, src_uuid, "Channel_0", ["Channel_1"],
                                  groups=groups)
    tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                           src_uuid=src_uuid, src_channel="Channel_0",
                           dst_uuid=uuids["Channel_1"], groups=groups,
                           angle_deg=angle, mirror=mirror)
    plan = plan_channel_copy(adapter, src_uuid=src_uuid, dst_uuid=uuids["Channel_1"],
                             src_channel="Channel_0", dst_channel="Channel_1",
                             transform=tr, groups=groups, include_global=include_global)
    return adapter, plan


# ── Task 4.1: transform math ─────────────────────────────────────────────────


class TestTransformMath:
    def test_plain_shift(self):
        tr = _transform()
        # C0 (12,14) -> (52,54); no rotation, no mirror
        out = transform_point(Vector2.from_xy_mm(12, 14), tr)
        assert out.x == 52 * MM and out.y == 54 * MM

    def test_rotation_matches_rotate_local_offset(self):
        tr = ChannelTransform(anchor_src=Vector2.from_xy_mm(10, 20),
                              anchor_dst=Vector2.from_xy_mm(30, 40),
                              angle_deg=90.0)
        p = Vector2.from_xy_mm(12, 21)
        expected_rot = rotate_local_offset(2.0, 1.0, 90.0)
        expected = Vector2.from_xy(tr.anchor_dst.x + expected_rot.x,
                                   tr.anchor_dst.y + expected_rot.y)
        out = transform_point(p, tr)
        assert out.x == expected.x and out.y == expected.y

    def test_angle_rotation(self):
        tr = _transform(angle_deg=90.0)
        assert transform_angle(30.0, tr) == pytest.approx(120.0)
        assert transform_angle(300.0, tr) == pytest.approx(30.0)  # wraps

    def test_mirror_point_matches_clone_convention(self):
        tr = ChannelTransform(anchor_src=Vector2.from_xy_mm(10, 20),
                              anchor_dst=Vector2.from_xy_mm(30, 40),
                              angle_deg=90.0, mirror=True)
        p = Vector2.from_xy_mm(12, 21)
        dx = (p.x - tr.anchor_src.x) / MM
        dy = (p.y - tr.anchor_src.y) / MM
        v = rotate_local_offset(dx, dy, 90.0)
        rotated_abs = Vector2.from_xy(tr.anchor_src.x + v.x, tr.anchor_src.y + v.y)
        mirrored = _mirror_x(tr.anchor_src, rotated_abs)
        expected = Vector2.from_xy(tr.anchor_dst.x + (mirrored.x - tr.anchor_src.x),
                                   tr.anchor_dst.y + (mirrored.y - tr.anchor_src.y))
        out = transform_point(p, tr)
        assert out.x == expected.x and out.y == expected.y

    def test_mirror_angle(self):
        tr = _transform(mirror=True)
        assert transform_angle(30.0, tr) == pytest.approx(150.0)  # 180-30
        tr2 = _transform(angle_deg=90.0, mirror=True)
        assert transform_angle(30.0, tr2) == pytest.approx(60.0)  # 180-(30+90)

    def test_mirror_layer_inverts(self):
        tr = _transform()
        assert transform_layer(BoardLayer.BL_F_Cu, tr) == BoardLayer.BL_F_Cu
        assert transform_layer(BoardLayer.BL_B_Cu, tr) == BoardLayer.BL_B_Cu
        trm = _transform(mirror=True)
        assert transform_layer(BoardLayer.BL_F_Cu, trm) == BoardLayer.BL_B_Cu
        assert transform_layer(BoardLayer.BL_B_Cu, trm) == BoardLayer.BL_F_Cu


# ── Task 4.1: twin map / channel resolution ──────────────────────────────────


class TestTwinMap:
    def test_build_live_twin_map_groups_twins(self):
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        assert src_uuid == CH0
        assert groups[f"/{SUB}/{IC_SYM}"] == {CH0: "IC0", CH1: "IC1"}
        assert groups[f"/{SUB}/{C_SYM}"] == {CH0: "C0", CH1: "C1"}

    def test_build_live_twin_map_pivot_missing_fatal(self):
        adapter = _adapter(_channel_fps())
        with pytest.raises(ValidationError):
            build_live_twin_map(adapter, "NOPE")

    def test_build_channel_groups_skips_flat_footprints(self):
        flat = _fp("J1", 0, 0, 0, BoardLayer.BL_F_Cu, [])
        adapter = _adapter(_channel_fps() + [flat])
        groups = build_channel_groups(adapter)
        # J1 (no hierarchy) must not appear in any twin group
        refs = {r for by_ch in groups.values() for r in by_ch.values()}
        assert "J1" not in refs

    def test_resolve_channel_uuids(self):
        adapter = _adapter(_channel_fps())
        groups = build_channel_groups(adapter)
        result = resolve_channel_uuids(adapter, CH0, "Channel_0", ["Channel_1"],
                                       groups=groups)
        assert result == {"Channel_0": CH0, "Channel_1": CH1}

    def test_resolve_channel_uuids_ignores_root_sheet(self):
        # A shared ROOT sheet footprint carrying /Channel_0/ local nets but NOT
        # a twin (its inner key is unique) must never be mistaken for Channel_0
        # (found live 2026-08-17 on 3CH-AWG-TIA). Without the twin-group filter
        # this would fatal "channel name is ambiguous".
        root = _fp("J0", 0, 0, 0, BoardLayer.BL_F_Cu, ["root-uuid", "root-sub", "j0-sym"],
                   pad_nets=["/Channel_0/DAC/+3V3_AVDD"])
        adapter = _adapter(_channel_fps() + [root])
        groups = build_channel_groups(adapter)
        result = resolve_channel_uuids(adapter, CH0, "Channel_0", ["Channel_1"],
                                       groups=groups)
        assert result == {"Channel_0": CH0, "Channel_1": CH1}

    def test_resolve_channel_uuids_dst_missing_fatal(self):
        adapter = _adapter(_channel_fps()[:2])  # only Channel_0
        groups = build_channel_groups(adapter)
        with pytest.raises(ValidationError):
            resolve_channel_uuids(adapter, CH0, "Channel_0", ["Channel_1"], groups=groups)

    def test_resolve_channel_uuids_src_name_mismatch_fatal(self):
        adapter = _adapter(_channel_fps())
        groups = build_channel_groups(adapter)
        with pytest.raises(ValidationError):
            resolve_channel_uuids(adapter, CH0, "Channel_1", ["Channel_1"], groups=groups)

    def test_resolve_channel_uuids_src_typo_fatal(self):
        # A typo in --src must be FATAL, not silently drop the vias/tracks —
        # they are filtered by the literal /src_channel/ prefix (review fix,
        # 2026-08-18: a typo moved the components by the pivot's uuid while
        # quietly emptying the via/track plan).
        adapter = _adapter(_channel_fps())
        groups = build_channel_groups(adapter)
        with pytest.raises(ValidationError, match="not found on the board"):
            resolve_channel_uuids(adapter, CH0, "Channel_X", ["Channel_1"], groups=groups)


# ── Task 2.2: transform resolution modes and errors ──────────────────────────


class TestResolveTransform:
    def test_points_mode(self):
        adapter = _adapter(_channel_fps())
        tr = resolve_transform(adapter, pivot_ref=None, pivot_role=None, pivot_pad=None,
                               src_uuid=CH0, src_channel="Channel_0", dst_uuid=CH1,
                               groups={}, src_point=(1.0, 2.0), dst_point=(3.0, 4.0),
                               angle_deg=45.0)
        assert tr.anchor_src.x == 1 * MM and tr.anchor_src.y == 2 * MM
        assert tr.anchor_dst.x == 3 * MM and tr.anchor_dst.y == 4 * MM
        assert tr.angle_deg == 45.0

    def test_pivot_mode_uses_twin_position(self):
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0", dst_uuid=CH1,
                               groups=groups)
        assert tr.anchor_src.x == 10 * MM and tr.anchor_src.y == 10 * MM
        assert tr.anchor_dst.x == 50 * MM and tr.anchor_dst.y == 50 * MM

    def test_pivot_mode_offset(self):
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0", dst_uuid=CH1,
                               groups=groups, offset=(2.0, -1.0))
        assert tr.anchor_dst.x == 52 * MM and tr.anchor_dst.y == 49 * MM

    def test_pivot_mode_target_dst_override(self):
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0", dst_uuid=CH1,
                               groups=groups, target_dst=(7.0, 8.0))
        assert tr.anchor_dst.x == 7 * MM and tr.anchor_dst.y == 8 * MM

    def test_pivot_mode_target_dst_with_offset(self):
        # --offset must apply to an explicit --target-dst too (review fix,
        # 2026-08-18: it used to be silently ignored in that branch).
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0", dst_uuid=CH1,
                               groups=groups, target_dst=(7.0, 8.0), offset=(2.0, -1.0))
        assert tr.anchor_dst.x == 9 * MM and tr.anchor_dst.y == 7 * MM

    def test_pivot_by_role(self):
        adapter = _adapter(_channel_fps())
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        tr = resolve_transform(adapter, pivot_ref=None, pivot_role="AD_DAC", pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0", dst_uuid=CH1,
                               groups=groups)
        assert tr.anchor_src.x == 10 * MM and tr.anchor_src.y == 10 * MM

    def test_no_mode_fatal(self):
        adapter = _adapter(_channel_fps())
        with pytest.raises(ValidationError):
            resolve_transform(adapter, pivot_ref=None, pivot_role=None, pivot_pad=None,
                              src_uuid=CH0, src_channel="Channel_0", dst_uuid=CH1,
                              groups={})

    def test_two_modes_fatal(self):
        adapter = _adapter(_channel_fps())
        with pytest.raises(ValidationError):
            resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                              src_uuid=CH0, src_channel="Channel_0", dst_uuid=CH1,
                              groups={}, src_point=(1.0, 2.0), dst_point=(3.0, 4.0))


# ── Task 2.4-2.6: planning (components + vias + tracks) ──────────────────────


class TestPlanning:
    def test_plan_moves_vias_tracks(self):
        fps = _channel_fps()
        vias = [_via(11, 11, "/Channel_0/DAC/+3V3_AVDD")]
        tracks = [_track(10, 10, 11, 11, "/Channel_0/DAC/GND")]
        _, plan = _plan_for(fps, vias=vias, tracks=tracks)

        # IC1 already stands at the anchor (50,50) -> skipped; C0(12,14)->(52,54)
        assert [m.ref for m in plan.moves] == ["C1"]
        m = plan.moves[0]
        assert m.position.x == 52 * MM and m.position.y == 54 * MM
        assert m.angle.degrees == 0.0

        assert len(plan.vias) == 1
        v = plan.vias[0]
        assert v.net_name == "/Channel_1/DAC/+3V3_AVDD"
        assert v.position.x == 51 * MM and v.position.y == 51 * MM
        assert v.registry_key is None  # channel-copy never joins the registry

        assert len(plan.tracks) == 1
        t = plan.tracks[0]
        assert t.net_name == "/Channel_1/DAC/GND"
        assert t.start.x == 50 * MM and t.start.y == 50 * MM
        assert t.end.x == 51 * MM and t.end.y == 51 * MM
        assert t.registry_key is None

    def test_twin_net_mapping(self):
        # twin_net only rewrites the /Channel_N/ prefix of the SOURCE channel;
        # global nets and nets of other channels pass through unchanged.
        from kicadstamp.channel_copy import _twin_net
        assert _twin_net("/Channel_0/DAC/+3V3_AVDD", "Channel_0", "Channel_1") == \
            "/Channel_1/DAC/+3V3_AVDD"
        assert _twin_net("GND", "Channel_0", "Channel_1") == "GND"
        assert _twin_net("/Channel_2/DAC/X", "Channel_0", "Channel_1") == "/Channel_2/DAC/X"

    def test_plan_skips_twin_already_at_target(self):
        # C1 already at (52,54) -> no move command at all
        fps = _channel_fps()
        c1 = fps[3]
        c1.position = Vector2.from_xy_mm(52, 54)
        _, plan = _plan_for(fps)
        assert [m.ref for m in plan.moves] == []

    def test_missing_twin_warns_and_skips(self, caplog):
        # Remove C1 from the board: C0 has no twin -> skipped with a warning
        fps = [fps0 for fps0 in _channel_fps() if fps0.ref != "C1"]
        with caplog.at_level("WARNING"):
            _, plan = _plan_for(fps)
        assert [m.ref for m in plan.moves] == []
        assert "No twin" in caplog.text


# ── Task 4.3: idempotency — double run must not duplicate ────────────────────


class TestIdempotency:
    def test_double_run_produces_no_duplicates(self):
        fps = _channel_fps()
        src_via = _via(11, 11, "/Channel_0/DAC/+3V3_AVDD")
        src_track = _track(10, 10, 11, 11, "/Channel_0/DAC/GND")

        # First run: dst has no copied copper yet -> plan is non-empty.
        _, plan1 = _plan_for(fps, vias=[src_via], tracks=[src_track])
        assert plan1.moves and plan1.vias and plan1.tracks

        # Second run: the copied C1/via/track now exist on the dst.
        c1 = fps[3]
        c1.position = Vector2.from_xy_mm(52, 54)
        dst_via = _via(51, 51, "/Channel_1/DAC/+3V3_AVDD")
        dst_track = _track(50, 50, 51, 51, "/Channel_1/DAC/GND")
        _, plan2 = _plan_for(fps, vias=[src_via, dst_via],
                             tracks=[src_track, dst_track])
        assert plan2.moves == []
        assert plan2.vias == []
        assert plan2.tracks == []


# ── Task 2.7: foreign copper (global nets inside the source bbox) ────────────


class TestForeign:
    def _adapter_with_foreign(self):
        fps = _channel_fps()
        src_via = _via(11, 11, "/Channel_0/DAC/+3V3_AVDD")
        src_track = _track(10, 10, 11, 11, "/Channel_0/DAC/GND")
        gnd_via = _via(10.5, 10.5, "GND")            # inside bbox, global net
        gnd_track = _track(10, 10, 10.5, 10.5, "GND")  # inside bbox, global net
        far_track = _track(200, 200, 201, 201, "GND")  # outside bbox
        adapter = _adapter(fps, vias=[src_via, gnd_via],
                           tracks=[src_track, gnd_track, far_track])
        return adapter

    def test_foreign_reported_but_not_copied_by_default(self, caplog):
        adapter = self._adapter_with_foreign()
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        uuids = resolve_channel_uuids(adapter, src_uuid, "Channel_0", ["Channel_1"],
                                      groups=groups)
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0",
                               dst_uuid=uuids["Channel_1"], groups=groups)
        with caplog.at_level("WARNING"):
            plan = plan_channel_copy(adapter, src_uuid=src_uuid, dst_uuid=uuids["Channel_1"],
                                     src_channel="Channel_0", dst_channel="Channel_1",
                                     transform=tr, groups=groups)
        assert plan.foreign.segments == 1
        assert plan.foreign.vias == 1
        assert plan.foreign.nets == {"GND"}
        # only the channel's own via/track are planned (foreign NOT copied)
        assert len(plan.vias) == 1
        assert len(plan.tracks) == 1

    def test_include_global_copies_foreign(self):
        adapter = self._adapter_with_foreign()
        src_uuid, groups = build_live_twin_map(adapter, "IC0")
        uuids = resolve_channel_uuids(adapter, src_uuid, "Channel_0", ["Channel_1"],
                                      groups=groups)
        tr = resolve_transform(adapter, pivot_ref="IC0", pivot_role=None, pivot_pad=None,
                               src_uuid=src_uuid, src_channel="Channel_0",
                               dst_uuid=uuids["Channel_1"], groups=groups)
        plan = plan_channel_copy(adapter, src_uuid=src_uuid, dst_uuid=uuids["Channel_1"],
                                 src_channel="Channel_0", dst_channel="Channel_1",
                                 transform=tr, groups=groups, include_global=True)
        assert len(plan.vias) == 2  # channel via + foreign GND via
        assert len(plan.tracks) == 2  # channel track + foreign GND track
        assert plan.foreign.include_global is True
        # foreign nets are NOT remapped (still "GND")
        assert {v.net_name for v in plan.vias} == {"/Channel_1/DAC/+3V3_AVDD", "GND"}


# ── Task 2.3 / 4.2: --mirror (layers inverted) ───────────────────────────────


class TestMirror:
    def test_plan_mirror_inverts_layers(self):
        fps = _channel_fps()
        via = _via(11, 11, "/Channel_0/DAC/+3V3_AVDD")
        track = _track(10, 10, 11, 11, "/Channel_0/DAC/GND", layer=BoardLayer.BL_F_Cu)
        _, plan = _plan_for(fps, vias=[via], tracks=[track], mirror=True)
        assert plan.moves, "expected at least C1 to move"
        for m in plan.moves:
            assert m.layer == BoardLayer.BL_B_Cu  # source was F.Cu
        assert plan.vias  # via is through-hole: layer is irrelevant but present
        assert plan.tracks[0].layer == BoardLayer.BL_B_Cu


# ── Task 2.8 / 4.2: execution through BatchExecutor (no registry) ────────────


class TestExecution:
    def test_execute_calls_executor_with_plan(self, monkeypatch):
        import kicadstamp.channel_copy as cc
        _, plan = _plan_for(_channel_fps(), vias=[_via(11, 11, "/Channel_0/DAC/+3V3_AVDD")],
                            tracks=[_track(10, 10, 11, 11, "/Channel_0/DAC/GND")])
        captured = {}

        class FakeExecutor:
            def __init__(self, adapter, config, batch_size=10, operation_log_dir=None):
                captured["config"] = config

            def execute(self, moves, vias, tracks, check_collisions=True,
                        collision_margin_mm=0.2):
                captured["moves"] = moves
                captured["vias"] = vias
                captured["tracks"] = tracks
                captured["check_collisions"] = check_collisions
                return [], [], []

        monkeypatch.setattr(cc, "BatchExecutor", FakeExecutor)
        adapter = _adapter(_channel_fps())
        failed = execute_channel_copy(adapter, plan, check_collisions=False)
        assert failed == ([], [], [])
        assert captured["moves"] == plan.moves
        assert captured["vias"] == plan.vias
        assert captured["tracks"] == plan.tracks
        assert captured["check_collisions"] is False
        assert isinstance(captured["config"], Config)

    def test_full_plan_to_execute_roundtrip(self, tmp_path):
        """Full plan -> execute through the REAL BatchExecutor with a mocked
        adapter (no KiCad): verifies the plan is really applied — moves
        update footprint positions, vias/tracks are created — and that NO
        registry is involved (execute() is called without one)."""
        fps = _channel_fps()
        src_via = _via(11, 11, "/Channel_0/DAC/+3V3_AVDD")
        src_track = _track(10, 10, 11, 11, "/Channel_0/DAC/GND")
        adapter = _adapter(fps, vias=[src_via], tracks=[src_track])

        adapter.get_net_by_name.side_effect = lambda name: SimpleNamespace(name=name)
        adapter.commit_with_retry.side_effect = lambda desc, work: work() or True
        adapter.update_items.side_effect = lambda items: None
        adapter.create_items.side_effect = lambda items: items  # return the same mock objects

        _, plan = _plan_for(fps, vias=[src_via], tracks=[src_track])
        failed = execute_channel_copy(
            adapter, plan, config=Config(operation_log_dir=str(tmp_path)),
            check_collisions=False)
        assert failed == ([], [], [])

        # C1 must have been moved to the target position
        c1 = next(fp for fp in fps if fp.ref == "C1")
        assert c1.position.x == 52 * MM and c1.position.y == 54 * MM
        # vias and tracks were created (2 create_items calls: vias, then tracks)
        assert adapter.create_items.call_count == 2


# ── High-level entry point + dry-run report ──────────────────────────────────


class TestHighLevel:
    def test_channel_copy_dry_run(self):
        adapter = _adapter(_channel_fps(), vias=[_via(11, 11, "/Channel_0/DAC/+3V3_AVDD")],
                           tracks=[_track(10, 10, 11, 11, "/Channel_0/DAC/GND")])
        plan = channel_copy(adapter, src="Channel_0", dst="Channel_1", pivot="IC0",
                            dry_run=True)
        assert plan.src_channel == "Channel_0"
        assert plan.dst_channel == "Channel_1"
        assert plan.moves and plan.vias and plan.tracks

    def test_channel_copy_executes_when_not_dry_run(self, tmp_path, monkeypatch):
        import kicadstamp.channel_copy as cc
        adapter = _adapter(_channel_fps())
        adapter.get_net_by_name.side_effect = lambda name: SimpleNamespace(name=name)
        adapter.commit_with_retry.side_effect = lambda desc, work: work() or True
        adapter.update_items.side_effect = lambda items: None
        adapter.create_items.side_effect = lambda items: items
        cc_exec = MagicMock(return_value=([], [], []))
        monkeypatch.setattr(cc, "execute_channel_copy", cc_exec)
        plan = channel_copy(adapter, src="Channel_0", dst="Channel_1", pivot="IC0")
        cc_exec.assert_called_once()
        assert plan.dst_channel == "Channel_1"

    def test_format_report(self):
        _, plan = _plan_for(_channel_fps(), vias=[_via(11, 11, "/Channel_0/DAC/+3V3_AVDD")])
        lines = format_channel_copy_report(plan)
        text = "\n".join(lines)
        assert "CHANNEL COPY (DRY RUN)" in text
        assert "Channel_0" in text and "Channel_1" in text
        assert "Moves" in text and "Vias" in text and "Tracks" in text

    def test_channel_copy_requires_reference_mode(self):
        adapter = _adapter(_channel_fps())
        with pytest.raises(ValidationError):
            channel_copy(adapter, src="Channel_0", dst="Channel_1", dry_run=True)


class TestVerifyChannelCopyNets:
    """Phase 3 step 3.2 — net_matching (Kuhn + SCC) verifies the Role<->Net
    correspondence between the two channels' LIVE footprints. Diagnostics are
    surfaced as warnings, NEVER a stop (safe-default thesis)."""

    def _adapter_and_fps(self, c1_nets=None):
        fps = _channel_fps()
        if c1_nets is not None:
            # retarget Channel_1's bypass cap onto a DIFFERENT net
            c1 = next(fp for fp in fps if fp.ref == "C1")
            c1._pad_nets = list(c1_nets)
        return _adapter(fps), fps

    def test_clean_channels_no_diagnostics(self):
        adapter, fps = self._adapter_and_fps()
        assert verify_channel_copy_nets(
            adapter, fps, CH0, CH1, "Channel_0", "Channel_1") == []

    def test_mismatched_channel_reports_diagnostic(self):
        adapter, fps = self._adapter_and_fps(
            c1_nets=["/Channel_1/DAC/DIFFERENT"])
        diagnostics = verify_channel_copy_nets(
            adapter, fps, CH0, CH1, "Channel_0", "Channel_1")
        assert any("net_matching" in d for d in diagnostics)

    def test_plan_still_executes_when_net_matching_reports(self, caplog):
        """The mismatch is a DIAGNOSTIC, not a stop — the copy plan is still
        built (the deterministic twin_net prefix remap governs the copper),
        and the net_matching report is logged as a warning."""
        adapter, fps = self._adapter_and_fps(
            c1_nets=["/Channel_1/DAC/DIFFERENT"])
        with caplog.at_level("WARNING"):
            _dummy, plan = _plan_for(fps)
        assert plan is not None
        assert any("net_matching" in r.message for r in caplog.records)
