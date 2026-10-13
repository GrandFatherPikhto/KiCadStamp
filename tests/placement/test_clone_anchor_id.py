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
    def test_name_mode_unaffected_by_offset(self):
        """No anchor_ref/anchor_role at all -> identity is the effective name
        (name-or-cluster), as before."""
        a = clone_anchor_id(_clone(cluster="x", xy=(1.0, 2.0)))
        b = clone_anchor_id(_clone(cluster="x", xy=(99.0, -99.0)))
        assert a == b == f"name:{identity_value('x', det_uuid('clone_placements:x'))}"

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
