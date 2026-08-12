#!/usr/bin/env python3
"""Tests for coordinate_position_calculator.py — the "dumb placer"'s
geometry (2026-08-12): resolve an existing footprint by Cluster+Role,
compute its target position/rotation (Cartesian or polar), and — for
anchor: pad — where the footprint's own origin must land so a specific pad
of THAT SAME footprint ends up on the target point."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from kicadstamp.config import CoordinatePlacement, Point
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.spoke_layout import local_to_absolute
from kicadstamp.placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role, resolve_target_position,
    resolve_self_pad_anchor, build_coordinate_moves,
)
from kicadstamp.utils.units import MM


def _make_fp(ref, role=None, cluster=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp._role = role
    fp._cluster = cluster
    return fp


def _role_or_cluster(fp, field_name):
    if field_name == "Role":
        return fp._role
    if field_name == "Cluster":
        return fp._cluster
    return None


# ── resolve_footprint_by_cluster_role — exact Cluster+Role match ───────────

def test_unique_match_resolves():
    adapter = MagicMock()
    adapter.get_footprints.return_value = [
        _make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH"),
        _make_fp("R19", role="R_SERIES", cluster="OTHER_CLUSTER"),
    ]
    adapter.get_field_value.side_effect = _role_or_cluster

    fp = resolve_footprint_by_cluster_role(adapter, "FPGA_PERIPH", "R_SERIES", "label")

    assert fp.reference_field.text.value == "R18"


def test_no_match_raises():
    adapter = MagicMock()
    adapter.get_footprints.return_value = [_make_fp("R18", role="R_SERIES", cluster="OTHER")]
    adapter.get_field_value.side_effect = _role_or_cluster

    with pytest.raises(ValidationError, match="no component tagged"):
        resolve_footprint_by_cluster_role(adapter, "FPGA_PERIPH", "R_SERIES", "label")


def test_ambiguous_match_raises():
    adapter = MagicMock()
    adapter.get_footprints.return_value = [
        _make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH"),
        _make_fp("R20", role="R_SERIES", cluster="FPGA_PERIPH"),
    ]
    adapter.get_field_value.side_effect = _role_or_cluster

    with pytest.raises(ValidationError, match="expected exactly one"):
        resolve_footprint_by_cluster_role(adapter, "FPGA_PERIPH", "R_SERIES", "label")


def test_cluster_match_is_exact_not_prefix():
    """Deliberately NOT cluster_prefix_match — see the function's own
    docstring: identifying one already-uniquely-tagged instance is a
    different problem than narrowing several same-Role candidates."""
    adapter = MagicMock()
    adapter.get_footprints.return_value = [_make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH/SUB")]
    adapter.get_field_value.side_effect = _role_or_cluster

    with pytest.raises(ValidationError, match="no component tagged"):
        resolve_footprint_by_cluster_role(adapter, "FPGA_PERIPH", "R_SERIES", "label")


# ── resolve_target_position — Cartesian / polar ─────────────────────────────

def test_cartesian_mode_passes_through():
    cp = CoordinatePlacement(cluster="X", role="R1", x_mm=10.0, y_mm=20.0, rotation_deg=45.0)

    target, rotation_deg = resolve_target_position(cp)

    assert target.x == int(10.0 * MM)
    assert target.y == int(20.0 * MM)
    assert rotation_deg == 45.0


def test_polar_angle_becomes_rotation_by_default():
    cp = CoordinatePlacement(cluster="X", role="R1", center_x_mm=0.0, center_y_mm=0.0,
                              radius_mm=5.0, angle_deg=37.0)

    _target, rotation_deg = resolve_target_position(cp)

    assert rotation_deg == 37.0


def test_polar_explicit_rotation_overrides_angle():
    cp = CoordinatePlacement(cluster="X", role="R1", center_x_mm=0.0, center_y_mm=0.0,
                              radius_mm=5.0, angle_deg=37.0, rotation_deg=0.0)

    _target, rotation_deg = resolve_target_position(cp)

    assert rotation_deg == 0.0


def test_polar_point_matches_local_to_absolute_primitive():
    """Sign-convention-agnostic: the polar point must be exactly what
    local_to_absolute(center, radius, 0, angle) produces — the same
    primitive every cell's own along/across offsets use — rather than a
    second, independently-computed formula."""
    cp = CoordinatePlacement(cluster="X", role="R1", center_x_mm=10.0, center_y_mm=20.0,
                              radius_mm=5.0, angle_deg=63.0)

    target, _rotation_deg = resolve_target_position(cp)

    center = Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))
    expected = local_to_absolute(center, 5.0, 0.0, 63.0)
    assert target.x == expected.x
    assert target.y == expected.y


# ── resolve_self_pad_anchor — self-referential pad geometry ────────────────

def _adapter_with_pad(pad):
    adapter = MagicMock()
    adapter.get_pad_by_number.return_value = pad
    return adapter


def test_same_rotation_is_a_plain_offset_subtraction():
    """When new_rotation_deg equals the footprint's CURRENT rotation, the
    local-frame round-trip cancels out exactly — result is just
    target - (pad_position - fp_position), for ANY rotation value (doesn't
    depend on which way Vector2.rotate() turns positive angles)."""
    fp = MagicMock(spec=FootprintInstance)
    fp.position = Vector2.from_xy(int(5.0 * MM), int(-3.0 * MM))
    fp.orientation.degrees = 47.0
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(7.5 * MM), int(-1.0 * MM))
    adapter = _adapter_with_pad(pad)
    target = Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))

    result = resolve_self_pad_anchor(adapter, fp, "1", target, 47.0, "label")

    expected_dx = pad.position.x - fp.position.x
    expected_dy = pad.position.y - fp.position.y
    # Trig round-trip (rotate by -47 then +47) — not bit-exact, but must
    # cancel out to within a nanometre or two (same tolerance idiom as
    # registry.py's own _POSITION_TOLERANCE_MM for float geometry).
    assert abs(result.x - (target.x - expected_dx)) <= 2
    assert abs(result.y - (target.y - expected_dy)) <= 2


def test_rotation_preserves_pad_to_origin_distance():
    fp = MagicMock(spec=FootprintInstance)
    fp.position = Vector2.from_xy(0, 0)
    fp.orientation.degrees = 0.0
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(2.0 * MM), 0)
    adapter = _adapter_with_pad(pad)
    target = Vector2.from_xy(int(10.0 * MM), int(10.0 * MM))

    result = resolve_self_pad_anchor(adapter, fp, "1", target, 90.0, "label")

    dx_mm = (target.x - result.x) / MM
    dy_mm = (target.y - result.y) / MM
    distance = (dx_mm ** 2 + dy_mm ** 2) ** 0.5
    assert abs(distance - 2.0) < 1e-6


def test_missing_pad_raises():
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = "R18"
    fp.position = Vector2.from_xy(0, 0)
    fp.orientation.degrees = 0.0
    adapter = _adapter_with_pad(None)
    target = Vector2.from_xy(int(10.0 * MM), int(10.0 * MM))

    with pytest.raises(ValidationError, match="has no pad"):
        resolve_self_pad_anchor(adapter, fp, "99", target, 0.0, "label")


# ── build_coordinate_moves — end-to-end ─────────────────────────────────────

def test_build_coordinate_moves_center_anchor():
    fp = _make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH")
    fp.position = Vector2.from_xy(0, 0)
    fp.orientation.degrees = 0.0
    fp.layer = "F.Cu"
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = _role_or_cluster

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES", x_mm=10.0, y_mm=20.0,
                              rotation_deg=90.0)

    moves = build_coordinate_moves(adapter, [cp])

    assert len(moves) == 1
    move = moves[0]
    assert move.ref == "R18"
    assert move.position.x == int(10.0 * MM)
    assert move.position.y == int(20.0 * MM)
    assert move.angle.degrees == 90.0
    assert move.layer == "F.Cu"


def test_build_coordinate_moves_pad_anchor():
    fp = _make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH")
    fp.position = Vector2.from_xy(int(5.0 * MM), int(-3.0 * MM))
    fp.orientation.degrees = 47.0
    fp.layer = "F.Cu"
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(7.5 * MM), int(-1.0 * MM))
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = _role_or_cluster
    adapter.get_pad_by_number.return_value = pad

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES", x_mm=100.0, y_mm=200.0,
                              rotation_deg=47.0, anchor="pad", anchor_pad="2")

    moves = build_coordinate_moves(adapter, [cp])

    expected_dx = pad.position.x - fp.position.x
    expected_dy = pad.position.y - fp.position.y
    assert abs(moves[0].position.x - (int(100.0 * MM) - expected_dx)) <= 2
    assert abs(moves[0].position.y - (int(200.0 * MM) - expected_dy)) <= 2


def test_build_coordinate_moves_uses_effective_name_in_errors():
    adapter = MagicMock()
    adapter.get_footprints.return_value = []
    adapter.get_field_value.side_effect = _role_or_cluster

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES", x_mm=1.0, y_mm=2.0,
                              rotation_deg=0.0)

    with pytest.raises(ValidationError, match="FPGA_PERIPH/R_SERIES"):
        build_coordinate_moves(adapter, [cp])


# ── Anchor-relative mode (2026-08-12, Group 0 consolidation) ────────────────

def _moved_fp():
    fp = _make_fp("R18", role="R_SERIES", cluster="FPGA_PERIPH")
    fp.position = Vector2.from_xy(0, 0)
    fp.orientation.degrees = 0.0
    fp.layer = "F.Cu"
    return fp


def test_build_coordinate_moves_anchor_point_cartesian_offset():
    """anchor_point (resolved standalone via resolve_point_chain, since Phase
    0 runs before the planner's resolved_points) + Cartesian offset — the
    migrated root-placement shape."""
    fp = _moved_fp()
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = _role_or_cluster
    adapter.get_board_origin.return_value = Vector2.from_xy(int(30.0 * MM), int(40.0 * MM))
    points = {"Origin": Point(name="Origin", anchor_origin="grid")}

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES",
                             anchor_point="Origin", x_mm=10.0, y_mm=-70.0)

    moves = build_coordinate_moves(adapter, [cp], points=points, sheet_names={})

    assert len(moves) == 1
    move = moves[0]
    assert move.ref == "R18"
    assert move.position.x == int(30.0 * MM) + int(10.0 * MM)
    assert move.position.y == int(40.0 * MM) + int(-70.0 * MM)
    assert move.angle.degrees == 0.0


def test_build_coordinate_moves_anchor_ref_pad_offset():
    """anchor_ref + anchor_pad: the offset is measured from the ANCHOR
    component's pad (Rule/ClonePlacement semantics) — reused
    resolve_footprint_by_ref + resolve_anchor_pad_position."""
    moved = _moved_fp()
    anchor_fp = _make_fp("IC1", role="FPGA", cluster="FPGA")
    anchor_fp.position = Vector2.from_xy(int(5.0 * MM), int(5.0 * MM))
    anchor_fp.orientation.degrees = 0.0
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(7.0 * MM), int(9.0 * MM))
    adapter = MagicMock()
    adapter.get_footprints.return_value = [moved]
    adapter.get_field_value.side_effect = _role_or_cluster
    adapter.get_footprint.return_value = anchor_fp
    adapter.get_pad_by_number.return_value = pad

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES",
                             anchor_ref="IC1", anchor_pad="A17",
                             x_mm=2.0, y_mm=3.0)

    moves = build_coordinate_moves(adapter, [cp])

    assert len(moves) == 1
    move = moves[0]
    # target = anchor pad position + offset
    assert move.position.x == int(7.0 * MM) + int(2.0 * MM)
    assert move.position.y == int(9.0 * MM) + int(3.0 * MM)
    assert move.angle.degrees == 0.0


def test_build_coordinate_moves_anchor_polar_offset_rotation_defaults_to_angle():
    """Polar offset in anchor mode: target = anchor + (radius at angle), and
    angle_deg becomes rotation by default (spoke-style, same as the
    fixed-centre polar mode)."""
    fp = _moved_fp()
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = _role_or_cluster
    adapter.get_board_origin.return_value = Vector2.from_xy(0, 0)
    points = {"Origin": Point(name="Origin", anchor_origin="grid")}

    cp = CoordinatePlacement(cluster="FPGA_PERIPH", role="R_SERIES",
                             anchor_point="Origin", radius_mm=5.0, angle_deg=37.0)

    moves = build_coordinate_moves(adapter, [cp], points=points, sheet_names={})

    assert len(moves) == 1
    assert moves[0].angle.degrees == 37.0
    expected = local_to_absolute(Vector2.from_xy(0, 0), 5.0, 0.0, 37.0)
    assert moves[0].position.x == expected.x
    assert moves[0].position.y == expected.y
