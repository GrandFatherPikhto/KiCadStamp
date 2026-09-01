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
from kicadstamp.config import Cell, ClonePlacement, Config, Entity, Point, Rule, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import entity_anchor_id
from kicadstamp.placement.services.component_pool import ROLE_FIELD_NAME
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
        chains=[Rule(name="R1", net="+3V3_VCCIO", spokes=[])],
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


# ---------------------------------------------------------------------------
# Phase 4.1 live: a tree anchored on an ENTITY (ref -> record.kind placement)
# resolves RECURSIVELY through the tree that places that Entity.
# ---------------------------------------------------------------------------


def test_ref_anchor_on_entity_recurses_through_placing_tree():
    """A tree anchored with (ref "E1") where E1 is an Entity placed by ANOTHER
    tree: the anchor base = the placing tree's anchor base + E1's node offset
    (the same composition _walk applies). No adapter needed — the chain is
    origin -> E1 node -> E2 node. (No (external) on the anchor: the ref must
    resolve to the Entity, not to a live footprint.)"""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c", cluster="CH0"),
                  Entity(name="E2", cell="c", cluster="CH1")],
        trees=[
            # the tree that PLACES E1 (origin anchor, E1 node at (5,0))
            Tree(name="root", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node(ref="E1", xy=(5.0, 0.0))]),
            # a tree anchored on the E1 ENTITY (ref, NOT external) — recursion
            Tree(name="sub", anchor=TreeAnchor(ref="E1"),
                 nodes=[_node(ref="E2", xy=(2.0, 0.0))]),
        ],
    )
    clones = materialize_entity_placements(None, cfg, {})
    by_name = {c.name: c for c in clones}
    assert set(by_name) == {"E1", "E2"}
    assert by_name["E1"].xy == pytest.approx((5.0, 0.0))
    # E2 = E1's absolute position (5,0) + E2's own offset (2,0)
    assert by_name["E2"].xy == pytest.approx((7.0, 0.0))


def test_ref_anchor_entity_recursion_accumulates_rotation():
    """The found node's rotation propagates into the referring tree's anchor
    frame, exactly as _walk accumulates it: E2's rotation = E1 node rotation
    (90), and E2's offset (2,0) is rotated into that 90° parent frame."""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c"), Entity(name="E2", cell="c")],
        trees=[
            Tree(name="root", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node(ref="E1", xy=(5.0, 0.0), rotation=90.0)]),
            Tree(name="sub", anchor=TreeAnchor(ref="E1"),
                 nodes=[_node(ref="E2", xy=(2.0, 0.0))]),
        ],
    )
    clones = materialize_entity_placements(None, cfg, {})
    by_name = {c.name: c for c in clones}
    assert by_name["E2"].rotation_deg == pytest.approx(90.0)
    # (2,0) rotated into the 90° parent frame -> (0,-2) (KiCad Y-down)
    assert by_name["E2"].xy[0] == pytest.approx(5.0)
    assert by_name["E2"].xy[1] == pytest.approx(-2.0)


def test_ref_anchor_on_unplaced_entity_is_fatal():
    """A tree anchored on an Entity that NO tree node places (the Entity is in
    config but has no placement) is a CONFIG error — fatal, never a skip."""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c")],
        trees=[Tree(name="t", anchor=TreeAnchor(ref="E1"), nodes=[])],
    )
    with pytest.raises(ValidationError, match="not placed in any tree"):
        materialize_entity_placements(None, cfg, {})


def test_ref_anchor_on_entity_in_two_trees_is_fatal():
    """Defensive: an Entity referenced by TWO placement nodes (impossible in a
    parser-valid config — trees rule 2 — but reachable when building Config
    directly) is fatal, never silently choosing one."""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="E1", cell="c")],
        trees=[
            Tree(name="a", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node(ref="E1", xy=(1.0, 0.0))]),
            Tree(name="b", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node(ref="E1", xy=(2.0, 0.0))]),
            Tree(name="t", anchor=TreeAnchor(ref="E1"), nodes=[]),
        ],
    )
    with pytest.raises(ValidationError, match="more than one"):
        materialize_entity_placements(None, cfg, {})


def test_entity_ref_anchor_cycle_is_fatal():
    """Two trees anchored on each other's placed Entity (A anchors on an Entity
    placed in B, B anchors on an Entity placed in A) is a CYCLE — fatal, never
    an infinite recursion."""
    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="EA", cell="c"), Entity(name="EB", cell="c")],
        trees=[
            Tree(name="ta", anchor=TreeAnchor(ref="EB"),
                 nodes=[_node(ref="EA", xy=(0.0, 0.0))]),
            Tree(name="tb", anchor=TreeAnchor(ref="EA"),
                 nodes=[_node(ref="EB", xy=(0.0, 0.0))]),
        ],
    )
    with pytest.raises(ValidationError, match="cycle"):
        materialize_entity_placements(None, cfg, {})


def _real_profile_cfg_fpga_anchor_no_external():
    """Real profile with the fpga tree's (external) anchor marker STRIPPED —
    the anchor (ref "FPGA_WITH_SUPP") then resolves to the Entity (kind
    "placement") instead of a live footprint, exercising the RECURSIVE
    Entity-ref path (the gate scenario from the plan)."""
    from kicadstamp.trees import Tree, TreeAnchor

    cfg = _real_profile_cfg()
    fpga = cfg.trees[0]
    assert fpga.name == "fpga"
    cfg.trees[0] = Tree(name=fpga.name,
                        anchor=TreeAnchor(ref="FPGA_WITH_SUPP", is_external=False),
                        nodes=fpga.nodes)
    return cfg


def test_real_profile_fpga_tree_anchored_on_entity_recurses_to_ic1():
    """Gate: the fpga tree with (anchor (ref "FPGA_WITH_SUPP")) WITHOUT
    (external) resolves recursively — the Entity FPGA_WITH_SUPP is placed by
    the role-anchored FPGA_WITH_SUPP tree, so the fpga anchor base = live IC1
    (42,17) + the FPGA_WITH_SUPP node offset (0,0); every fpga child is offset
    from that base."""
    cfg = _real_profile_cfg_fpga_anchor_no_external()
    clones = materialize_entity_placements(_real_profile_adapter(), cfg, {})
    by_name = {c.name: c for c in clones}

    # FPGA_WITH_SUPP itself materializes at the live IC1 (42,17).
    assert by_name["FPGA_WITH_SUPP"].xy[0] == pytest.approx(42.0)
    assert by_name["FPGA_WITH_SUPP"].xy[1] == pytest.approx(17.0)

    # fpga children, all offset from (42,17):
    assert by_name["CH0_DAC_BUF"].xy[0] == pytest.approx(42.0)
    assert by_name["CH0_DAC_BUF"].xy[1] == pytest.approx(42.0)      # 17+25
    assert by_name["CH0_DAC_BUF"].rotation_deg == pytest.approx(-90.0)
    assert by_name["CH1_DAC_BUF"].xy[0] == pytest.approx(67.0)      # 42+25
    assert by_name["CH1_DAC_BUF"].xy[1] == pytest.approx(17.0)
    assert by_name["FPGA_FLASH"].xy[0] == pytest.approx(22.0)       # 42-20
    assert by_name["FPGA_FLASH"].xy[1] == pytest.approx(7.0)        # 17-10


def test_real_profile_entity_ref_anchor_follows_moved_ic1():
    """Gate, live-move: with the fpga tree anchored on the FPGA_WITH_SUPP
    Entity (recursion), moving (mock) IC1 recalculates BOTH the FPGA_WITH_SUPP
    placement AND every fpga child from the NEW position — the entity-ref anchor
    is read live, not a captured coordinate."""
    cfg = _real_profile_cfg_fpga_anchor_no_external()
    adapter = _real_profile_adapter()
    ic1 = adapter.get_footprints.return_value[0]        # IC1 (Role=FPGA)
    ic1.position = Vector2.from_xy_mm(50.0, 30.0)
    clones = materialize_entity_placements(adapter, cfg, {})
    by_name = {c.name: c for c in clones}

    assert by_name["FPGA_WITH_SUPP"].xy[0] == pytest.approx(50.0)
    assert by_name["FPGA_WITH_SUPP"].xy[1] == pytest.approx(30.0)
    assert by_name["CH0_DAC_BUF"].xy[0] == pytest.approx(50.0)
    assert by_name["CH0_DAC_BUF"].xy[1] == pytest.approx(55.0)      # 30+25
    assert by_name["FPGA_FLASH"].xy[0] == pytest.approx(30.0)       # 50-20
    assert by_name["FPGA_FLASH"].xy[1] == pytest.approx(20.0)       # 30-10


# ---------------------------------------------------------------------------
# Bug #6 (2026-08-31): a (ref ...) tree anchor resolving to a NON-Entity record
# (a legacy ClonePlacement) that itself anchors via anchor_point. _anchor_base
# hands an EMPTY resolved_points dict into resolve_base_live_position, so the
# point must be resolved LAZILY on demand (the same resolve_point_chain pattern
# _resolve_clone_anchor_position uses) instead of a raw KeyError.
# ---------------------------------------------------------------------------


def _point_anchored_clone_cfg(point_name="Origin", have_point=True):
    """The handoff_2026_08_31 synthetic recipe: a Point, a legacy ClonePlacement
    anchored to it via anchor_point, an Entity + origin tree (the materializable
    "control"), and a SECOND tree whose (ref ...) anchor points at the clone's
    effective name ("FPGA"). have_point=False deliberately omits the Point to
    exercise the missing-point path."""
    clone_placements = [ClonePlacement(name="FPGA", cluster="FPGA", cell="c",
                                       xy=(0.0, 0.0), anchor_point=point_name)]
    return Config(
        cells={"c": _cell("c")},
        points={point_name: Point(name=point_name, xy=(10.0, 20.0))}
        if have_point else {},
        clone_placements=clone_placements,
        entities=[Entity(name="E1", cell="c"),
                  Entity(name="E2", cell="c")],
        trees=[
            _origin_tree([_node(ref="E1", xy=(1.0, 0.0))]),
            Tree(name="sub", anchor=TreeAnchor(ref="FPGA"),
                 nodes=[_node(ref="E2", xy=(0.0, 0.0))]),
        ],
    )


def test_ref_anchor_on_point_anchored_clone_materializes_on_point_position():
    """Bug #6 gate (fails with KeyError BEFORE the fix): the tree "sub" anchor
    (ref "FPGA") resolves to the ClonePlacement (kind "clone", NOT Entity), whose
    anchor_point "Origin" names a real Point (xy 10,20). _anchor_base passes an
    EMPTY resolved_points dict, so ClonePositionCalculator._resolve_anchor must
    lazily resolve the point on demand — E2 materializes at the Point's live
    position (10,20) + the clone's flat shift (0,0) + E2's own node offset (0,0).
    E1 (the origin-tree control) materializes unchanged at (1,0)."""
    cfg = _point_anchored_clone_cfg()
    clones = materialize_entity_placements(None, cfg, {})
    by_name = {c.name: c for c in clones}
    assert set(by_name) == {"E1", "E2"}
    assert by_name["E1"].xy == pytest.approx((1.0, 0.0))
    assert by_name["E2"].xy[0] == pytest.approx(10.0)
    assert by_name["E2"].xy[1] == pytest.approx(20.0)


def test_ref_anchor_on_clone_with_missing_point_is_skipped_locally_not_keyerror(caplog):
    """Bug #6: an anchor_point naming a point ABSENT from cfg.points must surface
    as a clear ValidationError (per-tree warning + skip in
    materialize_entity_placements, the bug-#4 tolerance), NEVER a raw KeyError
    leaking to the caller. The neighbor origin tree (E1) still materializes."""
    cfg = _point_anchored_clone_cfg(have_point=False)
    with caplog.at_level(logging.WARNING,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["E1"]
    assert "skipped" in caplog.text
    assert "sub" in caplog.text


# ---------------------------------------------------------------------------
# Auto-anchor from the root Entity's cell zero slot (2026-08-31, plan
# tree_self_anchor_from_entity): a tree with NO explicit (anchor ...) derives
# its base from the single top-level placement node's Entity — the ONE
# component of its cell at local offset (0,0) acts as the anchor role, narrowed
# by the Entity's OWN sheet/cluster, then resolved LIVE like a (role ...)
# anchor. Config ambiguity is a whole-run fatal (never a silent origin/skip).
# ---------------------------------------------------------------------------


def _role_adapter(role="FPGA", x=30.0, y=40.0, cluster=None):
    """Adapter with one footprint carrying the given Role/Cluster fields."""
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = "IC1"
    fp._role = role
    fp._cluster = cluster
    fp.position = Vector2.from_xy_mm(x, y)
    fp.angle_deg = 0.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = (
        lambda f, name: getattr(f, "_role", None) if name == ROLE_FIELD_NAME else
        (getattr(f, "_cluster", None) if name == CLUSTER_FIELD_NAME else None))
    adapter.get_selected_items.return_value = []
    return adapter


def _auto_tree(nodes):
    return Tree(name="t", anchor=TreeAnchor(is_auto=True), nodes=nodes)


def test_auto_anchor_materializes_on_zero_slot_role():
    """A tree with NO explicit (anchor ...) whose single top-level node is a
    placement on an Entity whose cell has ONE zero-offset slot (the "zero",
    self-referencing slot — e.g. role "FPGA") derives its anchor from that
    slot's role and materializes at the live footprint's position — the same
    live resolution as an explicit (role ...) anchor."""
    cell = Cell(name="fpga", components=[
        TemplateComponentSlot(role="FPGA"),                       # zero slot
        TemplateComponentSlot(role="R_TERM_N", offset_along_mm=1.0),
    ])
    cfg = Config(cells={"fpga": cell},
                 entities=[Entity(name="fpga", cell="fpga")],
                 trees=[_auto_tree([_node(ref="fpga", xy=(1.0, 2.0))])])
    clones = materialize_entity_placements(_role_adapter(), cfg, {})
    assert len(clones) == 1
    c = clones[0]
    assert c.name == "fpga"
    assert c.xy[0] == pytest.approx(31.0)   # IC1 (30,40) + node (1,2)
    assert c.xy[1] == pytest.approx(42.0)
    assert c.rotation_deg == pytest.approx(0.0)


def test_auto_anchor_no_zero_slot_is_fatal_not_silent():
    """Zero zero-offset components -> a CONFIG error, fatal for the whole run —
    never a silent origin fallback or a per-tree skip."""
    cfg = Config(cells={"c": Cell(name="c", components=[
        TemplateComponentSlot(role="R_TERM_N", offset_along_mm=1.0),
    ])},
        entities=[Entity(name="E1", cell="c")],
        trees=[_auto_tree([_node(ref="E1", xy=(0.0, 0.0))])])
    with pytest.raises(ValidationError, match="zero-offset"):
        materialize_entity_placements(_role_adapter(), cfg, {})


def test_auto_anchor_multiple_zero_slots_is_fatal_naming_count():
    """Two zero-offset components -> explicit fatal naming the count, not a
    guess."""
    cfg = Config(cells={"c": Cell(name="c", components=[
        TemplateComponentSlot(role="A"),
        TemplateComponentSlot(role="B"),
    ])},
        entities=[Entity(name="E1", cell="c")],
        trees=[_auto_tree([_node(ref="E1", xy=(0.0, 0.0))])])
    with pytest.raises(ValidationError, match="2 zero-offset"):
        materialize_entity_placements(_role_adapter(), cfg, {})


def test_auto_anchor_multiple_top_level_nodes_is_fatal():
    """Auto-anchor applies only when the tree has EXACTLY ONE top-level
    placement node; several roots without a common parent is ambiguous -> fatal
    (the plan's open question, resolved: trees CAN have several top-level
    nodes, so auto-derivation requires exactly one)."""
    cfg = Config(cells={"c": _cell("c")},
                 entities=[Entity(name="E1", cell="c"), Entity(name="E2", cell="c")],
                 trees=[Tree(name="t", anchor=TreeAnchor(is_auto=True),
                             nodes=[_node(ref="E1", xy=(0.0, 0.0)),
                                    _node(ref="E2", xy=(0.0, 0.0))])])
    with pytest.raises(ValidationError, match="EXACTLY ONE"):
        materialize_entity_placements(None, cfg, {})


def test_auto_anchor_explicit_anchor_wins():
    """An explicit (anchor (role ...)) is used as-is; the auto path does NOT run
    (regression gate: auto would resolve the zero slot's role "FPGA", the
    explicit anchor resolves "OTHER" instead)."""
    cell = Cell(name="c", components=[TemplateComponentSlot(role="FPGA")])
    cfg = Config(cells={"c": cell},
                 entities=[Entity(name="E1", cell="c")],
                 trees=[Tree(name="t", anchor=TreeAnchor(role="OTHER"),
                             nodes=[_node(ref="E1", xy=(1.0, 0.0))])])
    clones = materialize_entity_placements(_role_adapter(role="OTHER", x=5.0, y=5.0), cfg, {})
    assert len(clones) == 1
    assert clones[0].xy[0] == pytest.approx(6.0)   # OTHER (5,5) + node (1,0)
    assert clones[0].xy[1] == pytest.approx(5.0)


def test_auto_anchor_narrows_by_entity_cluster():
    """Entity.sheet/cluster feed the auto-anchor's narrowing exactly like an
    explicit (role ...) anchor: two live footprints with the same role but
    different Cluster — Entity.cluster picks the right instance."""
    ch0 = MagicMock(spec=FootprintInstance)
    ch0.ref = "IC_A"; ch0._role = "FPGA"; ch0._cluster = "CH0"
    ch0.position = Vector2.from_xy_mm(10.0, 10.0); ch0.angle_deg = 0.0
    ch1 = MagicMock(spec=FootprintInstance)
    ch1.ref = "IC_B"; ch1._role = "FPGA"; ch1._cluster = "CH1"
    ch1.position = Vector2.from_xy_mm(50.0, 50.0); ch1.angle_deg = 0.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [ch0, ch1]
    adapter.get_field_value.side_effect = (
        lambda f, name: getattr(f, "_role", None) if name == ROLE_FIELD_NAME else
        (getattr(f, "_cluster", None) if name == CLUSTER_FIELD_NAME else None))
    adapter.get_selected_items.return_value = []

    cell = Cell(name="c", components=[TemplateComponentSlot(role="FPGA")])
    cfg = Config(cells={"c": cell},
                 entities=[Entity(name="E1", cell="c", cluster="CH1")],
                 trees=[_auto_tree([_node(ref="E1", xy=(0.0, 0.0))])])
    clones = materialize_entity_placements(adapter, cfg, {})
    assert len(clones) == 1
    assert clones[0].xy[0] == pytest.approx(50.0)   # CH1 instance, not CH0
    assert clones[0].xy[1] == pytest.approx(50.0)


def test_auto_anchor_literal_self_ref_stays_cycle_fatal():
    """A LITERAL (anchor (ref "E1")) self-reference (not the absent-anchor auto
    case) KEEPS the existing semantics: the Entity-ref recursion loops into
    itself and the cycle-guard makes it a clear fatal. The auto-anchor only
    applies when (anchor ...) is ABSENT — it never rewrites the (ref ...)
    grammar (decision documented in the done handoff)."""
    cfg = Config(cells={"c": _cell("c")},
                 entities=[Entity(name="E1", cell="c")],
                 trees=[Tree(name="t", anchor=TreeAnchor(ref="E1"),
                             nodes=[_node(ref="E1", xy=(0.0, 0.0))])])
    with pytest.raises(ValidationError, match="cycle"):
        materialize_entity_placements(None, cfg, {})


def test_auto_anchor_roundtrips_through_dict_bridge():
    """The config dict inlay round-trips an auto-anchored tree: no "anchor" key
    in -> TreeAnchor(is_auto=True); tree_to_dict omits the key again."""
    from kicadstamp.trees import tree_from_dict, tree_to_dict
    data = {"name": "t",
            "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 2.0]}]}
    tree = tree_from_dict(data)
    assert tree.anchor.is_auto is True
    assert tree_to_dict(tree) == data


def test_auto_anchor_roundtrips_through_sexp_bridge():
    """The s-expr path (used by .sexp configs via sexp_format) round-trips an
    auto-anchored tree too: a (tree ...) node with no (anchor ...) child ->
    TreeAnchor(is_auto=True), and no (anchor ...) node is re-emitted."""
    from kicadstamp.cloner.sexp import sym
    from kicadstamp.trees import tree_from_sexp, tree_to_sexp
    sexp = [sym("tree"), [sym("name"), "t"],
            [sym("node"), [sym("ref"), "E1"], [sym("kind"), sym("placement")]]]
    tree = tree_from_sexp(sexp, seen_names=set(), seen_refs=set(), location="test")
    assert tree.anchor.is_auto is True
    assert tree_to_sexp(tree) == sexp


# ---------------------------------------------------------------------------
# position_overrides reach materialization (plan_2026_08_31_fpga_flash_rigid_
# redraw_not_following.md): a tree rigid-group redraw feeds a per-node
# PositionOverride into ApplyPipeline; materialization must let it REPLACE the
# structural pos/rot for that placement node (the same principle as
# ClonePositionCalculator.compute_raw_positions), closing the asymmetry where
# this step recomputed the position without seeing the override.
# ---------------------------------------------------------------------------


def test_materialize_position_override_replaces_placement_node():
    """An origin-anchored tree with a placement node at structural (5,0)/0° —
    a PositionOverride for "fpga" must replace it: the materialized clone lands
    at the override (10,20)/45°, NOT at the structural (5,0)."""
    from kicadstamp.tree_position import PositionOverride

    cfg = _cfg([Entity(name="fpga", cell="c")],
               [_origin_tree([_node(ref="fpga", xy=(5.0, 0.0))])])
    override = PositionOverride(position=Vector2.from_xy_mm(10.0, 20.0),
                                rotation_deg=45.0)
    clones = materialize_entity_placements(None, cfg, {},
                                           position_overrides={"fpga": override})
    assert len(clones) == 1
    c = clones[0]
    assert c.name == "fpga"
    assert c.xy == pytest.approx((10.0, 20.0))
    assert c.rotation_deg == pytest.approx(45.0)


def test_materialize_position_override_only_overridden_node():
    """Override applies ONLY to the node it names: fpga is overridden to
    (10,20)/45°, the child keeps the STRUCTURAL frame (fpga node at (5,0)/0°
    -> child at (7,0)/0°). A rigid redraw applies one node per run, so the
    child's structural frame is never observed there — this just pins the
    documented behavior."""
    from kicadstamp.tree_position import PositionOverride

    cfg = Config(
        cells={"c": _cell("c")},
        entities=[Entity(name="fpga", cell="c"), Entity(name="child", cell="c")],
        trees=[_origin_tree([
            _node(ref="fpga", xy=(5.0, 0.0),
                  children=[_node(ref="child", xy=(2.0, 0.0))])])])
    override = PositionOverride(position=Vector2.from_xy_mm(10.0, 20.0),
                                rotation_deg=45.0)
    clones = materialize_entity_placements(None, cfg, {},
                                           position_overrides={"fpga": override})
    by_name = {c.name: c for c in clones}
    assert by_name["fpga"].xy == pytest.approx((10.0, 20.0))
    assert by_name["fpga"].rotation_deg == pytest.approx(45.0)
    assert by_name["child"].xy == pytest.approx((7.0, 0.0))
    assert by_name["child"].rotation_deg == pytest.approx(0.0)


def test_materialize_without_override_unchanged():
    """Regression: no overrides -> the normal structural path is unchanged."""
    cfg = _cfg([Entity(name="fpga", cell="c")],
               [_origin_tree([_node(ref="fpga", xy=(5.0, 2.0), rotation=90.0)])])
    clones = materialize_entity_placements(None, cfg, {})
    assert len(clones) == 1
    assert clones[0].xy == pytest.approx((5.0, 2.0))
    assert clones[0].rotation_deg == pytest.approx(90.0)


def test_resolve_order_forwards_position_overrides_to_materialize():
    """The pipeline wires the asymmetry shut: _resolve_order passes its
    position_overrides through to materialize_entity_placements, so a tree
    rigid-group redraw's override reaches the materialized transient clone."""
    from unittest.mock import patch

    from kicadstamp.apply_pipeline import ApplyPipeline
    from kicadstamp.tree_position import PositionOverride

    cfg = _cfg([Entity(name="fpga", cell="c")],
               [_origin_tree([_node(ref="fpga", xy=(5.0, 0.0))])])
    override = PositionOverride(position=Vector2.from_xy_mm(10.0, 20.0),
                                rotation_deg=0.0)
    pipeline = ApplyPipeline("board.sexp", preloaded_cfg=cfg, only=["fpga"],
                             position_overrides={"fpga": override})
    pipeline.adapter = None
    pipeline._load_config()
    pipeline._filter_config()

    calls = []
    with patch("kicadstamp.apply_pipeline.materialize_entity_placements",
               side_effect=lambda *a, **k: calls.append(k) or []), \
         patch("kicadstamp.apply_pipeline.resolve_execution_order", return_value=[]):
        pipeline._resolve_order()
    assert calls
    assert calls[0]["position_overrides"] == {"fpga": override}


def _fpga_flash_mock_adapter():
    """Live-like mock board for the fpga_flash redraw repro: U6 (Role=FLASH,
    Cluster=FPGA_FLASH, pad +3V3_FLASH) and C117 (Role=C_IN_BULK,
    Cluster=FPGA_FLASH, pads +3V3/GND) — the minimal set that exercises the net
    auto-derivation for a role WITHOUT net_template (C_IN_BULK)."""
    import contextlib
    from unittest.mock import MagicMock

    def _pad(number, net):
        p = MagicMock()
        p.number = number
        p.net_name = net
        return p

    def _fp(ref, role, cluster, x, y, pads):
        fp = MagicMock()
        fp.ref = ref
        fp.position = Vector2.from_xy_mm(x, y)
        fp.angle_deg = 0.0
        fp._role = role
        fp._cluster = cluster
        fp._pads = list(pads)
        return fp

    u6 = _fp("U6", "FLASH", "FPGA_FLASH", 101.738, 15.0,
             [_pad("8", "+3V3_FLASH")])
    c117 = _fp("C117", "C_IN_BULK", "FPGA_FLASH", 99.769, 8.107,
               [_pad("1", "+3V3"), _pad("2", "GND")])
    adapter = MagicMock()
    adapter.get_footprints.return_value = [u6, c117]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role"
        else (getattr(fp, "_cluster", None) if name == "Cluster" else None))
    adapter.get_footprint_pads.side_effect = (
        lambda fp: list(getattr(fp, "_pads", [])))
    adapter.get_pad_by_number.side_effect = (
        lambda fp, num: next((p for p in getattr(fp, "_pads", [])
                              if p.number == str(num)), None))
    adapter.get_selected_items.return_value = []
    adapter.temporarily_ignore_selection.side_effect = (
        lambda clone: contextlib.nullcontext())
    return adapter


def test_placement_live_resolve_autoderives_net_for_role_without_template():
    """fpga_flash redraw repro (plan_2026_08_31_fpga_flash_rigid_redraw_
    not_following.md): an Entity whose materialized clone falls back its
    cluster to its OWN lower-case name ("fpga_flash") must still narrow the
    role candidates to the upper-case physical Cluster ("FPGA_FLASH") — a
    case-insensitive cluster_prefix_match lets the net auto-derivation
    (_auto_derive_live_net) find the unique C_IN_BULK instance and derive its
    net live, so the whole apply/redraw no longer fatals with "net-based
    mapping failed" on a role with no net_template (before the fix the live
    Redraw of fpga_flash silently moved nothing)."""
    import dataclasses

    from kicadstamp.apply_pipeline import _filter_materialized_entities
    from kicadstamp.placement.dependency_order import resolve_execution_order
    from kicadstamp.placement.planner import PlacementPlanner

    cell = Cell(name="fpga_flash", components=[
        TemplateComponentSlot(role="FLASH"),                      # zero-slot
        TemplateComponentSlot(role="C_IN_BULK",                   # NO net_template
                              offset_along_mm=-1.9695,
                              offset_across_mm=-6.8925),
    ])
    cfg = Config(
        cells={"fpga_flash": cell},
        entities=[Entity(name="fpga_flash", cell="fpga_flash")],
        trees=[_origin_tree([_node(ref="fpga_flash", xy=(0.0, 0.0))])],
    )
    adapter = _fpga_flash_mock_adapter()

    materialized = materialize_entity_placements(adapter, cfg, {})
    assert [c.name for c in materialized] == ["fpga_flash"]
    # The fallback cluster is the lower-case Entity name (Entity has no
    # explicit cluster) — the case that used to empty the candidate set.
    assert materialized[0].cluster == "fpga_flash"

    filtered = _filter_materialized_entities(materialized, ["fpga_flash"], None)
    cfg2 = dataclasses.replace(cfg, clone_placements=list(cfg.clone_placements) + filtered)
    items = resolve_execution_order(adapter, cfg2, sheet_names={})  # must NOT raise
    ff_item = next(it for it in items if getattr(it.obj, "name", None) == "fpga_flash")

    planner = PlacementPlanner(adapter, cfg2, sheet_names={})
    planner.begin_planning()
    moves = planner.plan_item(ff_item)  # must NOT raise (was net-based mapping fatal)
    assert any(m.ref == "U6" for m in moves)
    assert any(m.ref == "C117" for m in moves)
