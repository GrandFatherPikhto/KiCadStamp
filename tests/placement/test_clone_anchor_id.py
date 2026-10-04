#!/usr/bin/env python3
"""Tests for clone_anchor_id (kicadstamp/placement/services/clone_position_calculator.py)."""

import pytest
from kicadstamp.config import ClonePlacement, Entity
from kicadstamp.placement.services.clone_position_calculator import (
    clone_anchor_id,
    entity_anchor_id,
)
from tests.fakes.format3 import det_uuid, identity_value


def _clone(**kwargs):
    defaults = dict(cluster="c", cell="t", xy=(0.0, 0.0))
    defaults.update(kwargs)
    # The record's identity (name-or-cluster) and, for a Point-anchored clone,
    # the point's uuid — both needed under the format-3 gate (Р-У5.1).
    if "uuid" not in defaults:
        defaults["uuid"] = det_uuid(
            f"clone_placements:{defaults.get('name') or defaults['cluster']}")
    if defaults.get("anchor_point") and "anchor_point_uuid" not in defaults:
        defaults["anchor_point_uuid"] = det_uuid(f"points:{defaults['anchor_point']}")
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
        """No anchor_ref/anchor_role at all -> identity is the effective name
        (name-or-cluster), as before."""
        a = clone_anchor_id(_clone(cluster="x", xy=(1.0, 2.0)))
        b = clone_anchor_id(_clone(cluster="x", xy=(99.0, -99.0)))
        assert a == b == f"name:{identity_value('x', det_uuid('clone_placements:x'))}"

    def test_anchor_point_is_not_the_name_fallback(self):
        """Found 2026-08-06: anchor_point had NO branch at all here, so it fell
        through all the way to name:{clone.name} — same identity as absolute
        coordinates, and with none of the rename-safety anchor_ref/anchor_role
        get. A Point-anchored clone must key on the point + offset, not name."""
        result = clone_anchor_id(_clone(cluster="x", anchor_point="Origin", xy=(4.0, -110.0)))
        assert result != f"name:{identity_value('x', det_uuid('clone_placements:x'))}"
        assert identity_value("Origin", det_uuid("points:Origin")) in result

    def test_anchor_point_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_point="Origin", xy=(4.0, -110.0)))
        b = clone_anchor_id(_clone(anchor_point="Origin", xy=(4.0, -100.0)))
        assert a != b

    def test_anchor_point_survives_rename(self):
        """The whole point of keying on physical binding instead of clone.name —
        renaming a Point-anchored clone must NOT change its registry identity,
        exactly like it already doesn't for anchor_ref/anchor_role."""
        a = clone_anchor_id(_clone(cluster="Conn_PM5V", anchor_point="Origin", xy=(4.0, -110.0)))
        b = clone_anchor_id(_clone(cluster="Conn_PM5V_renamed", anchor_point="Origin", xy=(4.0, -110.0)))
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


class TestEntityAnchorId:
    """entity_anchor_id (Entity/Placement split, phase 3.1): an Entity's
    registry id is the "name:" branch — Entity carries no anchor fields by
    design, so there are no physical-binding branches, only the name."""

    def test_entity_id_is_name_branch(self):
        entity = Entity(name="CH0_DAC_BUF", cell="c",
                        uuid=det_uuid("entities:CH0_DAC_BUF"))
        assert entity_anchor_id(entity) == \
            f"name:{identity_value('CH0_DAC_BUF', entity.uuid)}"

    def test_entity_id_stable_across_instances(self):
        """The id depends only on the (required, unique) name — two identical
        entities produce the same id, matching the materialized clone's
        name:-branch (clone.name == entity.name, phase 4.1)."""
        entity_uuid = det_uuid("entities:E")
        assert entity_anchor_id(Entity(name="E", cell="c", uuid=entity_uuid)) == \
            entity_anchor_id(Entity(name="E", cell="c", uuid=entity_uuid))
