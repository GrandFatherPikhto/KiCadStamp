"""Tests for the Entity-placement materialization (Entity/Placement split,
Phase 4.1): kicadstamp/placement/entity_placement.py + the apply_pipeline
filter/ordering integration for entities.

An Entity carries no position — its position comes from a trees: node
(kind "placement"). materialize_entity_placements() turns each such node
into a transient absolute ClonePlacement so the existing clone planning
machinery applies it unchanged.
"""
import pytest

from kicadstamp.apply_pipeline import apply_cluster_filter, apply_only_filter
from kicadstamp.config import Cell, ClonePlacement, Config, Entity
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import entity_anchor_id
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def _cell(name="c"):
    return Cell(name=name)


def _cfg(entities, trees):
    return Config(cells={e.cell: _cell(e.cell) for e in entities},
                  entities=list(entities), trees=list(trees))


def _origin_tree(nodes):
    return Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _node(ref, xy=None, kind="placement", rotation=0.0, children=None):
    """TreeNode has no field defaults — build with explicit keywords."""
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=children or [])


def test_no_entities_or_trees_is_empty():
    assert materialize_entity_placements(None, Config(), {}) == []
    cfg = _cfg([Entity(name="E1", cell="c")], [])
    assert materialize_entity_placements(None, cfg, {}) == []


def test_origin_anchor_top_level_node_position():
    """A top-level placement node under (origin) materializes as an absolute
    clone: position = node xy (5, 2) mm, rotation = node rotation (90)."""
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0", nets={"R": "+5V"})],
        [_origin_tree([_node(ref="E1", xy=(5.0, 2.0), rotation=90.0)])])
    clones = materialize_entity_placements(None, cfg, {})
    assert len(clones) == 1
    c = clones[0]
    assert isinstance(c, ClonePlacement)
    assert c.name == "E1"
    assert c.cell == "c"
    assert c.cluster == "CH0"
    assert c.nets == {"R": "+5V"}
    assert c.xy == (5.0, 2.0)
    assert c.rotation_deg == 90.0
    # registry identity aligns with entity_anchor_id (phase 3.1)
    assert entity_anchor_id(Entity(name="E1", cell="c")) == "name:E1"


def test_nested_node_rotation_accumulates():
    """A child node's absolute position = parent position + its offset rotated
    into the parent's frame; its rotation = parent rotation + own rotation."""
    cfg = _cfg(
        [Entity(name="E1", cell="c"), Entity(name="E2", cell="c")],
        [_origin_tree([
            _node(ref="E1", xy=(5.0, 0.0), rotation=90.0,
                  children=[_node(ref="E2", xy=(1.0, 0.0))])])])
    clones = materialize_entity_placements(None, cfg, {})
    by_name = {c.name: c for c in clones}
    assert set(by_name) == {"E1", "E2"}
    assert by_name["E1"].xy == (5.0, 0.0)
    assert by_name["E1"].rotation_deg == 90.0
    # E2 offset (1,0) is rotated into the parent's 90° frame, not added flat
    assert by_name["E2"].rotation_deg == 90.0
    assert by_name["E2"].xy != (6.0, 0.0)
    assert len(by_name["E2"].xy) == 2


def test_role_anchor_not_wired_raises():
    """role/point tree anchors are not live-resolvable for entity
    materialization yet (phase 4.2) — a clear error, never a silent guess."""
    cfg = _cfg(
        [Entity(name="E1", cell="c")],
        [Tree(name="t", anchor=TreeAnchor(role="FPGA"),
              nodes=[_node(ref="E1", xy=(1.0, 0.0))])])
    with pytest.raises(ValidationError, match="role"):
        materialize_entity_placements(None, cfg, {})


def test_apply_only_filter_keeps_entities_intact_and_recognizes_name():
    """--only must NOT cut cfg.entities: materialization runs link_trees over
    the FULL trees/entities and would fatal on a tree node whose entity was
    filtered away (regression found in review 2026-08-30). The filter only
    recognizes the entity name so --only E1 does not fatal; the entity
    narrowing happens on the materialized clones."""
    cfg = _cfg([Entity(name="E1", cell="c", cluster="CH0"),
                Entity(name="E2", cell="c", cluster="CH1")], [])
    filtered = apply_only_filter(cfg, ["E1"])
    assert {e.name for e in filtered.entities} == {"E1", "E2"}
    with pytest.raises(PlacerError):
        apply_only_filter(cfg, ["NO_SUCH_ENTITY"])


def test_apply_only_with_entities_in_trees_does_not_fatal():
    """Regression: --only E1 with E1 AND E2 both in trees must not fatal in
    materialization (entities stay intact, so link_trees resolves E2's node);
    the materialized clones are narrowed to E1 instead."""
    from kicadstamp.apply_pipeline import _filter_materialized_entities
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1")],
        [_origin_tree([_node(ref="E1", xy=(1.0, 0.0)),
                       _node(ref="E2", xy=(2.0, 0.0))])])
    filtered = apply_only_filter(cfg, ["E1"])
    assert {e.name for e in filtered.entities} == {"E1", "E2"}
    materialized = _filter_materialized_entities(
        materialize_entity_placements(None, filtered, {}), ["E1"], None)
    assert [c.name for c in materialized] == ["E1"]


def test_filter_materialized_entities_by_only_and_cluster():
    """The --only/--cluster narrowing of entity placements happens on the
    materialized clones: --only by clone name (== Entity.name), --cluster by
    the clone's cluster tag (== Entity.cluster)."""
    from kicadstamp.apply_pipeline import _filter_materialized_entities
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1")],
        [_origin_tree([_node(ref="E1", xy=(1.0, 0.0)),
                       _node(ref="E2", xy=(2.0, 0.0))])])
    materialized = materialize_entity_placements(None, cfg, {})
    assert {c.name for c in materialized} == {"E1", "E2"}
    assert [c.name for c in _filter_materialized_entities(materialized, ["E1"], None)] == ["E1"]
    assert [c.name for c in _filter_materialized_entities(materialized, None, ["CH1"])] == ["E2"]


def test_apply_cluster_filter_does_not_fatal_when_only_entities_match():
    """--cluster that selects exclusively an entity's cluster must not raise
    "matched nothing" (the entity stays in cfg and is narrowed later on the
    materialized clones)."""
    cfg = _cfg([Entity(name="E1", cell="c", cluster="CH0")], [])
    filtered = apply_cluster_filter(cfg, ["CH0"])
    assert {e.name for e in filtered.entities} == {"E1"}
    with pytest.raises(PlacerError):
        apply_cluster_filter(cfg, ["NO_SUCH_CLUSTER"])
