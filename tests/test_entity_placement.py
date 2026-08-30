"""Tests for the Entity-placement materialization (Entity/Placement split,
Phase 4.1): kicadstamp/placement/entity_placement.py + the apply_pipeline
filter/ordering integration for entities.

An Entity carries no position — its position comes from a trees: node
(kind "placement"). materialize_entity_placements() turns each such node
into a transient absolute ClonePlacement so the existing clone planning
machinery applies it unchanged.
"""
import logging
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.apply_pipeline import apply_cluster_filter, apply_only_filter
from kicadstamp.config import Cell, ClonePlacement, Config, Entity, Rule
from kicadstamp.domain.geometry import Vector2
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


def test_point_anchor_tree_is_skipped_locally_not_fatal(caplog):
    """Bug #4 / Phase 4.2: a (point ...) tree anchor is NOT live-resolvable
    for entity materialization (point anchors are a future phase) — that is
    LOCAL to the tree: warn + skip it, NEVER fatal for the whole run. A single
    point-anchored tree with nothing else yields no materialized clones, not a
    ValidationError."""
    cfg = _cfg(
        [Entity(name="E1", cell="c")],
        [Tree(name="t", anchor=TreeAnchor(point="Origin"),
              nodes=[_node(ref="E1", xy=(1.0, 0.0))])])
    with caplog.at_level(logging.WARNING,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(None, cfg, {})
    assert clones == []
    assert "skipped" in caplog.text


def test_role_anchor_tree_materializes_on_resolved_footprint():
    """Phase 4.2: a (role ...) tree anchor is resolved LIVE via
    ComponentResolver (same logic as Rule.anchor_role) — the placement-node
    materializes at the matched footprint's current position + node offset,
    NOT skipped/fatal. anchor_sheet/anchor_cluster narrow the same cascade."""
    fpga = MagicMock(spec=FootprintInstance)
    fpga.ref = "IC1"
    fpga._role = "FPGA"
    fpga.position = Vector2.from_xy_mm(30.0, 40.0)
    fpga.angle_deg = 0.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []

    cfg = _cfg(
        [Entity(name="E1", cell="c")],
        [Tree(name="t", anchor=TreeAnchor(role="FPGA", anchor_sheet="FPGA",
                                          anchor_cluster="FPGA"),
              nodes=[_node(ref="E1", xy=(1.0, 2.0))])])
    clones = materialize_entity_placements(adapter, cfg, {})
    assert len(clones) == 1
    c = clones[0]
    assert c.name == "E1"
    # node offset (1,2) added to IC1's live position (30,40).
    assert c.xy[0] == pytest.approx(31.0)
    assert c.xy[1] == pytest.approx(42.0)
    assert c.rotation_deg == pytest.approx(0.0)


def test_role_anchor_tree_uses_anchor_rotation_as_parent_rotation():
    """The role anchor's live rotation feeds the node's parent frame: node
    offset (1,0) is rotated by the anchor's 90° (KiCad Y-down -> (0,-1)),
    and the clone inherits the anchor rotation (90 + node 0)."""
    fpga = MagicMock(spec=FootprintInstance)
    fpga.ref = "IC1"
    fpga._role = "FPGA"
    fpga.position = Vector2.from_xy_mm(30.0, 40.0)
    fpga.angle_deg = 90.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []

    cfg = _cfg(
        [Entity(name="E1", cell="c")],
        [Tree(name="t", anchor=TreeAnchor(role="FPGA"),
              nodes=[_node(ref="E1", xy=(1.0, 0.0))])])
    clones = materialize_entity_placements(adapter, cfg, {})
    assert len(clones) == 1
    c = clones[0]
    assert c.xy[0] == pytest.approx(30.0)  # (1,0) rotated by 90 -> (0,-1)
    assert c.xy[1] == pytest.approx(39.0)
    assert c.rotation_deg == pytest.approx(90.0)


def test_role_anchor_tree_with_anchor_pad_uses_pad_position():
    """anchor_pad moves the base onto the matched footprint's specific pad
    (resolve_anchor_pad_position), not the footprint origin."""
    fpga = MagicMock(spec=FootprintInstance)
    fpga.ref = "IC1"
    fpga._role = "FPGA"
    fpga.position = Vector2.from_xy_mm(30.0, 40.0)
    fpga.angle_deg = 0.0
    fpga._pads = [MagicMock(number="A1", net_name="X",
                            position=Vector2.from_xy_mm(31.0, 40.0))]
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)

    cfg = _cfg(
        [Entity(name="E1", cell="c")],
        [Tree(name="t", anchor=TreeAnchor(role="FPGA", anchor_pad="A1"),
              nodes=[_node(ref="E1", xy=(0.0, 0.0))])])
    clones = materialize_entity_placements(adapter, cfg, {})
    assert len(clones) == 1
    assert clones[0].xy[0] == pytest.approx(31.0)
    assert clones[0].xy[1] == pytest.approx(40.0)


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
    # raw CLI lists may carry comma-separated values ("--only a,b") — the
    # caller must split them (_split_comma_values) before narrowing, or the
    # result is silently empty (4.1-fix 2 regression).
    from kicadstamp.apply_pipeline import _split_comma_values
    assert [c.name for c in _filter_materialized_entities(
        materialized, _split_comma_values(["E1,E2"]), None)] == ["E1", "E2"]


def test_apply_cluster_filter_does_not_fatal_when_only_entities_match():
    """--cluster that selects exclusively an entity's cluster must not raise
    "matched nothing" (the entity stays in cfg and is narrowed later on the
    materialized clones)."""
    cfg = _cfg([Entity(name="E1", cell="c", cluster="CH0")], [])
    filtered = apply_cluster_filter(cfg, ["CH0"])
    assert {e.name for e in filtered.entities} == {"E1"}
    with pytest.raises(PlacerError):
        apply_cluster_filter(cfg, ["NO_SUCH_CLUSTER"])


def test_point_anchor_tree_skipped_but_neighbor_origin_tree_materialized(caplog):
    """Bug #4 gate scenario: ONE origin tree (materializable entity E1) next
    to a point-anchored tree (still unwired for materialization) — the origin
    tree's placement survives, the point tree is skipped with a warning, the
    call never fatal. (Role trees are wired since Phase 4.2; point is not.)"""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c", cluster="CH0"),
                  Entity(name="E2", cell="c", cluster="CH1")],
        trees=[
            _origin_tree([_node(ref="E1", xy=(1.0, 0.0))]),
            Tree(name="point_tree", anchor=TreeAnchor(point="Origin"),
                 nodes=[_node(ref="E2", xy=(5.0, 0.0))]),
        ],
    )
    with caplog.at_level(logging.WARNING,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["E1"]
    assert "skipped" in caplog.text
    assert "point_tree" in caplog.text


def _real_profile_adapter():
    """A live-like mock adapter for the real-profile gate: IC1 has Role=FPGA
    at (42, 17) mm — the FPGA_WITH_SUPP role anchor resolves to it — and the
    fpga tree's external (ref "FPGA_WITH_SUPP") anchor reads a live footprint
    at the origin. Roles not on the mock board (CONN_PM5V/AD_DAC/OP_AMP/MCU/
    ...) resolve to nothing, so those role trees are skipped locally (never
    fatal)."""
    ic1 = MagicMock(spec=FootprintInstance)
    ic1.ref = "IC1"
    ic1._role = "FPGA"
    ic1.position = Vector2.from_xy_mm(42.0, 17.0)
    ic1.angle_deg = 0.0
    ref_fp = MagicMock(spec=FootprintInstance)
    ref_fp.ref = "FPGA_WITH_SUPP"
    ref_fp.position = Vector2.from_xy_mm(0.0, 0.0)
    ref_fp.angle_deg = 0.0

    adapter = MagicMock()
    all_fps = [ic1, ref_fp]
    adapter.get_footprints.return_value = all_fps
    adapter.get_footprint.side_effect = lambda ref: next(
        (f for f in all_fps if f.ref == ref), None)
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []
    return adapter


def _real_profile_cfg():
    from pathlib import Path

    from kicadstamp.config import load_config

    profile = (Path(__file__).resolve().parents[1]
               / "profiles" / "3ch-awg-tia-v103" / "3ch-awg-tia.sexp")
    if not profile.exists():
        pytest.skip("real profile not present (profiles/ is gitignored)")
    cfg, _ctx = load_config(str(profile))
    assert len(cfg.trees) == 22
    return cfg


def test_real_profile_role_anchor_trees_do_not_fatal_whole_run():
    """Bug #4 on the REAL converted profile (22 trees, 21 role-anchored,
    1 ref/live): materialization must NOT fatal as a whole — role trees whose
    role is absent from the board are skipped with a warning, the fpga (ref)
    tree's entity placements still materialize, and (Phase 4.2) the
    role-anchored FPGA_WITH_SUPP tree materializes instead of being skipped.
    Guarded by profile presence (profiles/ is gitignored)."""
    cfg = _real_profile_cfg()
    # The 21 role trees resolve through ComponentResolver now — a bare
    # MagicMock would crash (get_footprints() is not iterable), so use the
    # live-like mock board with Role=FPGA -> IC1.
    clones = materialize_entity_placements(_real_profile_adapter(), cfg, {})
    names = [c.name for c in clones]
    # the fpga (ref/coordinate) tree's placement nodes materialized:
    assert "FPGA_FLASH" in names
    # Phase 4.2: the role-anchored FPGA_WITH_SUPP tree materializes too.
    assert "FPGA_WITH_SUPP" in names
    # the fpga tree's RULE nodes were NOT materialized (only kind "placement"):
    for rule_name in ("+3V3_VCCIO", "+1V2_VCCINT", "+1V2_VCCD_PLL", "+2V5_VCCA"):
        assert rule_name not in names


def test_real_profile_role_anchor_tree_materializes_at_ic1_live_position():
    """Phase 4.2 gate, explicit + live-like: materialize_entity_placements()
    on the REAL profile must return a materialized clone with
    name == "FPGA_WITH_SUPP" whose position == the LIVE Role=FPGA footprint
    (IC1) position + the node offset (xy 0,0) — found BY NAME and checked
    against the expected position, not merely "did not crash"."""
    cfg = _real_profile_cfg()
    clones = materialize_entity_placements(_real_profile_adapter(), cfg, {})
    by_name = {c.name: c for c in clones}

    # The role-anchored FPGA_WITH_SUPP tree materializes on the LIVE IC1:
    assert "FPGA_WITH_SUPP" in by_name
    c = by_name["FPGA_WITH_SUPP"]
    # node xy (0,0) -> the clone lands exactly on IC1's live position (mm).
    assert c.xy[0] == pytest.approx(42.0)
    assert c.xy[1] == pytest.approx(17.0)
    assert c.rotation_deg == pytest.approx(0.0)


def _mixed_tree_cfg():
    """The REAL fpga-tree shape (bug #3, 2026-08-30): ONE tree mixes a
    kind="placement" node (an Entity) with a kind="rule" node (a rules: entry)
    — a neighbor section of the SAME tree."""
    return Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c", cluster="CH0")],
        rules=[Rule(name="R1", net="+3V3_VCCIO", spokes=[])],
        trees=[Tree(name="t", anchor=TreeAnchor(is_origin=True),
                    nodes=[_node(ref="E1", xy=(1.0, 0.0)),
                           _node(ref="R1", kind="rule", xy=(2.0, 0.0))])],
    )


def test_only_on_placement_narrows_neighbor_rule_section():
    """Real-profile bug #3 (2026-08-30): --only on a placement node narrows a
    NEIGHBOR section (rules:) of the SAME tree. Materializing from the narrowed
    cfg would fatal on the rule node (link_trees "Node ... not found in config")
    — the OLD apply_pipeline behavior; the fix materializes from the FULL cfg,
    which resolves the rule node and still yields only the placement."""
    cfg = _mixed_tree_cfg()
    narrowed = apply_only_filter(cfg, ["E1"])
    assert narrowed.rules == []                        # the neighbor section narrowed
    assert {e.name for e in narrowed.entities} == {"E1"}
    with pytest.raises(ValidationError, match="not found in config"):
        materialize_entity_placements(None, narrowed, {})
    clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["E1"]


def test_resolve_order_materializes_from_full_cfg_not_only_narrowed():
    """Pipeline-level regression (bug #3): _resolve_order() must pass the FULL
    (pre-filter) config to materialize_entity_placements, not the
    --only-narrowed self.cfg — otherwise link_trees fatals on the first
    rule/coordinate/... node of the same tree whose section --only filtered
    away (TreesDock-Redraw ran --only per single node and died on all 13
    fpga-tree nodes)."""
    from unittest.mock import patch

    from kicadstamp.apply_pipeline import ApplyPipeline

    cfg = _mixed_tree_cfg()
    pipeline = ApplyPipeline("board.sexp", preloaded_cfg=cfg, only=["E1"])
    pipeline.adapter = None
    pipeline._load_config()
    pipeline._filter_config()
    # the bug's precondition: the neighbor (rule) section IS narrowed away
    assert pipeline.cfg.rules == []
    assert pipeline._full_cfg is cfg                   # the full cfg is preserved
    with patch("kicadstamp.apply_pipeline.resolve_execution_order", return_value=[]):
        pipeline._resolve_order()                      # must not fatal
    names = [c.name for c in pipeline.cfg.clone_placements]
    assert "E1" in names
