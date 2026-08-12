#!/usr/bin/env python3
"""Tests for clone_anchor_id (kicadstamp/placement/services/clone_position_calculator.py)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import ClonePlacement
from kicadstamp.placement.services.clone_position_calculator import clone_anchor_id


def _clone(**kwargs):
    defaults = dict(name="c", cell="t", xy=(0.0, 0.0))
    defaults.update(kwargs)
    return ClonePlacement(**defaults)


class TestCloneAnchorId:
    def test_anchor_role_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    xy=(7.0, -6.0)))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    xy=(7.0, 6.0)))
        assert a != b

    def test_anchor_ref_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_ref="IC1", anchor_pad="17",
                                    xy=(1.0, 1.0)))
        b = clone_anchor_id(_clone(anchor_ref="IC1", anchor_pad="17",
                                    xy=(2.0, 1.0)))
        assert a != b

    def test_same_anchor_same_offset_is_same_id(self):
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    xy=(7.0, -6.0)))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    xy=(7.0, -6.0)))
        assert a == b

    def test_anchor_role_includes_cluster(self):
        """Found 2026-07-28: p5v_led_spoke/n5v_led_spoke share identical
        anchor_role/anchor_sheet/anchor_pad/origin and differ ONLY by
        anchor_cluster (Pos vs Neg) — must not collapse to the same id."""
        a = clone_anchor_id(_clone(anchor_role="C_OUT_BYPASS", anchor_pad="1",
                                    anchor_cluster="In_Pi_Filter_Pos",
                                    xy=(3.0, 0.0)))
        b = clone_anchor_id(_clone(anchor_role="C_OUT_BYPASS", anchor_pad="1",
                                    anchor_cluster="In_Pi_Filter_Neg",
                                    xy=(3.0, 0.0)))
        assert a != b

    def test_name_mode_unaffected_by_offset(self):
        """No anchor_ref/anchor_role at all -> identity is name-based, as before."""
        a = clone_anchor_id(_clone(name="x", xy=(1.0, 2.0)))
        b = clone_anchor_id(_clone(name="x", xy=(99.0, -99.0)))
        assert a == b == "name:x"

    def test_anchor_point_is_not_the_name_fallback(self):
        """Found 2026-08-06: anchor_point had NO branch at all here, so it fell
        through all the way to name:{clone.name} — same identity as absolute
        coordinates, and with none of the rename-safety anchor_ref/anchor_role
        get. A Point-anchored clone must key on the point + offset, not name."""
        result = clone_anchor_id(_clone(name="x", anchor_point="Origin", xy=(4.0, -110.0)))
        assert result != "name:x"
        assert "Origin" in result

    def test_anchor_point_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_point="Origin", xy=(4.0, -110.0)))
        b = clone_anchor_id(_clone(anchor_point="Origin", xy=(4.0, -100.0)))
        assert a != b

    def test_anchor_point_survives_rename(self):
        """The whole point of keying on physical binding instead of clone.name —
        renaming a Point-anchored clone must NOT change its registry identity,
        exactly like it already doesn't for anchor_ref/anchor_role."""
        a = clone_anchor_id(_clone(name="Conn_PM5V", anchor_point="Origin", xy=(4.0, -110.0)))
        b = clone_anchor_id(_clone(name="Conn_PM5V_renamed", anchor_point="Origin", xy=(4.0, -110.0)))
        assert a == b

    def test_polar_offset_distinguishes_anchored_clones(self):
        """A polar clone's offset must be reflected in its registry identity —
        two clones on the same anchor with different radii must NOT collapse
        to the same id (their xy is the loader default (0,0))."""
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                   radius_mm=5.0, angle_deg=0.0))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                   radius_mm=7.0, angle_deg=0.0))
        assert a != b

    def test_polar_offset_equivalent_to_cartesian_xy(self):
        """radius=5/angle=0 is the same offset as xy=(5,0) — must produce the
        same identity, so a config switched between the two representations
        does not lose its registry vias/tracks."""
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                   radius_mm=5.0, angle_deg=0.0))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                   xy=(5.0, 0.0)))
        assert a == b
