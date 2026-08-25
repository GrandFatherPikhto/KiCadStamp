#!/usr/bin/env python3
"""
Regression tests for pad_projection.py — the single point of calculation
for pad position after moving, rotating and/or mirroring a component.

In the current architecture, vias are computed geometrically at the planning
stage and do not use pad_projection directly, but the module remains important
for other operations (e.g., manual pad work or future extensions).
"""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer


from kicadstamp.geometry.pad_projection import predict_pad_position, local_pad_offset
from kicadstamp.utils.units import MM


def _make_fp(x_mm, y_mm, angle_deg, layer):
    fp = MagicMock()
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.angle_deg = angle_deg
    fp.layer = layer
    return fp


def _make_pad(x_mm, y_mm, net_name="GND"):
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _rotate_point(x_mm, y_mm, angle_deg):
    """Rotate point (x,y) by angle_deg clockwise (KiCad convention)."""
    theta = math.radians(angle_deg)
    rx = x_mm * math.cos(theta) + y_mm * math.sin(theta)
    ry = -x_mm * math.sin(theta) + y_mm * math.cos(theta)
    return rx, ry


class TestPredictPadPosition:
    def test_no_move_no_rotate_no_flip_returns_same_offset(self):
        """Without moving/rotating/flipping, the predicted pad position must
        match the original absolute pad position."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)
        dest = fp.position  # same position
        result = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        assert abs(result.x - pad.position.x) < 10
        assert abs(result.y - pad.position.y) < 10

    def test_rotation_changes_predicted_position(self):
        """Rotation by 90° must actually shift the predicted pad position,
        not leave it equal to the original absolute offset."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)
        dest = Vector2.from_xy(int(70.0 * MM), int(30.0 * MM))

        predicted_0deg = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        predicted_90deg = predict_pad_position(fp, pad, dest, angle_deg=90.0, needs_flip=False)

        dist_mm = math.hypot(predicted_0deg.x - predicted_90deg.x,
                             predicted_0deg.y - predicted_90deg.y) / MM
        assert dist_mm > 0.3, "rotation by 90° must noticeably change the predicted pad position"

    def test_flip_mirrors_local_x_before_rotation(self):
        """needs_flip=True must mirror the local X BEFORE applying the new
        angle — check via local_pad_offset (a constant geometry fact) and
        compare results with/without flip."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)  # local offset (+0.5675, 0)
        dest = fp.position

        offset = local_pad_offset(fp, pad)
        assert abs(offset.x / MM - 0.5675) < 1e-3
        assert abs(offset.y / MM) < 1e-3

        no_flip = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        with_flip = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=True)
        # At angle 0° and flip, the result must be X‑mirrored relative to dest,
        # not equal to no_flip.
        assert (no_flip.x - dest.x) == -(with_flip.x - dest.x)

    def test_local_pad_offset_independent_of_current_angle(self):
        """Check that local_pad_offset returns the same local offset regardless
        of the component's current rotation angle. We set a local offset (lx, ly) = (1,2),
        compute the absolute pad position at each angle, then verify that
        local_pad_offset gives back (1,2)."""
        lx, ly = 1.0, 2.0
        for angle_deg in (0.0, 45.0, 90.0, 180.0):
            fp = _make_fp(0.0, 0.0, angle_deg, BoardLayer.BL_F_Cu)
            rx, ry = _rotate_point(lx, ly, angle_deg)
            pad = _make_pad(rx, ry)
            offset = local_pad_offset(fp, pad)
            assert abs(offset.x / MM - lx) < 1e-3, f"angle {angle_deg}: X does not match"
            assert abs(offset.y / MM - ly) < 1e-3, f"angle {angle_deg}: Y does not match"

    def test_predict_with_pre_rotated_component(self):
        """Check that prediction works correctly when the component is already
        rotated and we apply a new rotation."""
        lx, ly = 0.5675, 0.0
        initial_angle = 45.0
        # Component at (50,50) with angle 45°
        fp = _make_fp(50.0, 50.0, initial_angle, BoardLayer.BL_F_Cu)
        # Absolute pad position at the initial angle
        rx, ry = _rotate_point(lx, ly, initial_angle)
        pad = _make_pad(50.0 + rx, 50.0 + ry)
        dest = Vector2.from_xy(int(70.0 * MM), int(30.0 * MM))

        # Predict pad position if the component is moved to dest with angle 0°
        predicted = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        expected_x = 70.0 + lx  # local offset does not change
        expected_y = 30.0 + ly
        assert abs(predicted.x / MM - expected_x) < 1e-3
        assert abs(predicted.y / MM - expected_y) < 1e-3