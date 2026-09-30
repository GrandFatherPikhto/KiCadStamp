"""Guards for the STRUCTURAL pre-filter of Entity materialization
(plan_2026_09_14_materialize_only_wanted_trees, guards C1-C9).

`materialize_entity_placements(..., only=..., cluster=...)` may skip a whole tree
BEFORE its first live anchor read, when the tree's own node recursion can
provably not yield a clone surviving --only/--cluster. The invariant that holds
the whole change together (P.2.1): the pre-filter may only ever be WIDER than the
fact, never narrower — the final narrowing stays _filter_materialized_entities.

Each test below is a named guard from the plan's P.3/P.5 tables: it fails for a
reason, not "it did not crash".
"""
import logging
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.apply_pipeline import _filter_materialized_entities
from kicadstamp.config import Cell, Config, Entity, clone_placement_effective_name
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.placement.entity_placement import (
    _structural_candidates,
    materialize_entity_placements,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


# ── fixtures ────────────────────────────────────────────────────────────────

def _cell(name="c"):
    return Cell(name=name)


def _cfg(entities, trees):
    return Config(cells={e.cell: _cell(e.cell) for e in entities if e.cell},
                  entities=list(entities), trees=list(trees))


def _origin_tree(nodes, name="t"):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _node(ref, xy=None, kind="placement", rotation=0.0, children=None):
    """TreeNode has no field defaults — build with explicit keywords (same
    helper shape test_entity_placement.py uses)."""
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=children or [])


def _mount(ref, role, xy=None, rotation=0.0, children=None, **anchor_kw):
    """A kind "mount" TreeNode — places nothing itself, its children hang from
    the live component its (role ...) anchor names."""
    return TreeNode(ref=ref, kind="mount", xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=children or [],
                    anchor=TreeAnchor(role=role, is_origin=False, **anchor_kw))


def _module(ref, xy=None, children=None):
    """A kind "module" TreeNode — its ref is another TREE's name, never a config
    record (so record is None), and it places nothing itself."""
    return _node(ref=ref, kind="module", xy=xy, children=children)


def _role_adapter(role="BBB", x_mm=30.0, y_mm=40.0, angle=0.0):
    """Live-board mock with ONE footprint carrying `role` — enough for a role
    anchor / mount anchor to resolve LIVE."""
    fpga = MagicMock(spec=FootprintInstance)
    fpga.ref = "IC1"
    fpga._role = role
    fpga.position = Vector2.from_xy_mm(x_mm, y_mm)
    fpga.angle_deg = angle
    fpga._pads = []
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []
    return adapter


class _SpyAdapter:
    """Counts EVERY call reaching the wrapped adapter.

    C3 asks for "zero board reads on a skipped tree", which a log/warning
    assertion cannot prove — only a call counter can."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def counting(*args, **kwargs):
            self.calls += 1
            return attr(*args, **kwargs)

        return counting


def _multi_tree_cfg():
    """3 trees, 3 Entities, and one tree carrying a NESTED placement — so a
    non-recursive enumeration is caught as well as a mis-matched one."""
    return _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1"),
         Entity(name="E3", cell="c")],
        [Tree(name="tree_a", anchor=TreeAnchor(is_origin=True),
              nodes=[_node(ref="E1", xy=(1.0, 0.0),
                           children=[_node(ref="E3", xy=(0.0, 1.0))])]),
         Tree(name="tree_b", anchor=TreeAnchor(is_origin=True),
              nodes=[_node(ref="E2", xy=(2.0, 0.0))]),
         Tree(name="tree_c", anchor=TreeAnchor(is_origin=True), nodes=[])],
    )


def _knockout_cfg():
    """Two trees: an origin tree (E1, no board read) and a ROLE-anchored tree
    (E2, which necessarily reads the board) — the C3 shape."""
    return _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1")],
        [_origin_tree([_node(ref="E1", xy=(1.0, 0.0))], name="tree_a"),
         Tree(name="tree_b", anchor=TreeAnchor(role="BBB"),
              nodes=[_node(ref="E2", xy=(2.0, 0.0))])],
    )


# ── C1: result identity ─────────────────────────────────────────────────────

def _single_name_per_tree_cfg():
    """3 trees, 3 Entities, ONE Entity per tree — the shape of the real
    profiles/3ch-awg-tia-v103 forest (P.0: each of the 22 names is a candidate
    in exactly ONE tree), where the plan's literal C1 comparison holds."""
    return _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1"),
         Entity(name="E3", cell="c")],
        [_origin_tree([_node(ref="E1", xy=(1.0, 0.0))], name="tree_a"),
         _origin_tree([_node(ref="E2", xy=(2.0, 0.0))], name="tree_b"),
         _origin_tree([_node(ref="E3", xy=(3.0, 0.0))], name="tree_c")],
    )


def test_c1_narrowed_materialization_equals_filter_after_the_fact():
    """C1: on a forest with >=3 trees and >=3 Entities, for EVERY name
    materialize(cfg, only=[name]) must equal _filter(materialize(cfg), [name]) —
    the lists compared by ALL fields (dataclass equality), not by length. Here
    each tree carries ONE Entity, so the raw lists match literally. Kills a
    pre-filter that drops a needed tree."""
    cfg = _single_name_per_tree_cfg()
    full = materialize_entity_placements(None, cfg, {})
    assert {c.name for c in full} == {"E1", "E2", "E3"}

    for name in ("E1", "E2", "E3"):
        narrowed = materialize_entity_placements(None, cfg, {}, only=[name])
        reference = _filter_materialized_entities(full, [name], None)
        # ClonePlacement is a dataclass: == compares every field.
        assert narrowed == reference
        assert [c.name for c in narrowed] == [name]


def test_c1b_a_tree_carrying_several_entities_stays_a_wider_superset():
    """C1 (the reading that matches P.2.1): when ONE tree carries SEVERAL
    Entities, the raw narrowed call returns every clone of every KEPT tree — a
    SUPERSET of the name's own clones, by design. The materializer may be WIDER,
    never narrower, and the caller's _filter_materialized_entities is what
    narrows; so the invariant is asserted on the APPLIED result, which a dropped
    needed tree still breaks."""
    cfg = _multi_tree_cfg()
    full = materialize_entity_placements(None, cfg, {})

    for name in ("E1", "E2", "E3"):
        narrowed = materialize_entity_placements(None, cfg, {}, only=[name])
        applied_narrowed = _filter_materialized_entities(narrowed, [name], None)
        applied_reference = _filter_materialized_entities(full, [name], None)
        assert applied_narrowed == applied_reference
        assert [c.name for c in applied_narrowed] == [name]
        # wider, never narrower: the name's clone REALLY is in the raw list.
        assert name in {c.name for c in narrowed}

    for wanted in (["CH0"], ["CH1"], ["E3"]):
        narrowed = materialize_entity_placements(None, cfg, {}, cluster=wanted)
        assert (_filter_materialized_entities(narrowed, None, wanted)
                == _filter_materialized_entities(full, None, wanted))


# ── C2: the pre-filter is never narrower ────────────────────────────────────

def test_c2_structural_enumeration_is_a_superset_of_the_fact():
    """C2: the structurally enumerated names over the whole forest must be a
    SUPERSET of the effective names of everything that actually materializes.
    Kills an enumeration that drifts from _walk's recursion."""
    cfg = _multi_tree_cfg()
    linked = link_trees(cfg, cfg.trees)
    enumerated = set()
    for tree in linked:
        enumerated |= _structural_candidates(tree)

    actual = {clone_placement_effective_name(c)
              for c in materialize_entity_placements(None, cfg, {})}
    assert actual <= enumerated
    # the enumeration really carries both identities the clones expose
    assert {"E1", "E2", "E3"} <= enumerated
    assert {"CH0", "CH1"} <= enumerated


# ── C3: the economy is real ─────────────────────────────────────────────────

def test_c3_a_tree_that_cannot_yield_the_name_reads_the_board_zero_times():
    """C3: --only E1 must leave the role-anchored tree_b untouched. tree_a is
    origin-anchored (touches no board), tree_b is skipped BEFORE _anchor_base,
    so the narrowed run's total adapter calls must be EXACTLY ZERO — while the
    un-narrowed run does read the board (the counter is proven live first)."""
    cfg = _knockout_cfg()

    baseline = _SpyAdapter(_role_adapter())
    assert [c.name for c in materialize_entity_placements(baseline, cfg, {})] == ["E1", "E2"]
    assert baseline.calls > 0  # the role anchor really reads the board

    spy = _SpyAdapter(_role_adapter())
    clones = materialize_entity_placements(spy, cfg, {}, only=["E1"])
    assert [c.name for c in clones] == ["E1"]
    assert spy.calls == 0


# ── C4: embedded content (module nodes) ─────────────────────────────────────

def test_c4_a_placement_inside_a_module_node_keeps_its_tree():
    """C4: a "module" node has NO record, but a placement node inside it still
    materializes — the enumeration must recurse THROUGH the record-less module
    node (the 0878aa2 hole: embedded content reachable only through a node kind
    a naive walk stops at). Tree_b is the module TARGET, so it materializes its
    own content from its own top-level tree."""
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1")],
        [Tree(name="tree_a", anchor=TreeAnchor(is_origin=True),
              nodes=[_module("tree_b", children=[_node(ref="E1", xy=(1.0, 0.0))])]),
         Tree(name="tree_b", anchor=TreeAnchor(is_origin=True),
              nodes=[_node(ref="E2", xy=(2.0, 0.0))])],
    )
    tree_a = next(t for t in link_trees(cfg, cfg.trees) if t.name == "tree_a")
    assert _structural_candidates(tree_a) == {"E1", "CH0"}

    assert [c.name for c in materialize_entity_placements(None, cfg, {}, only=["E1"])] == ["E1"]
    assert [c.name for c in materialize_entity_placements(None, cfg, {}, only=["E2"])] == ["E2"]
    # the module TARGET's own content is NOT walked through tree_a (P.1.5) —
    # only its own tree materializes it, so E2 is absent from tree_a's run.
    assert materialize_entity_placements(None, cfg, {}, only=["E2"]) \
        == _filter_materialized_entities(
            materialize_entity_placements(None, cfg, {}), ["E2"], None)


# ── C5: nodes without a record (mount) ──────────────────────────────────────

def test_c5_a_placement_under_a_mount_node_keeps_its_tree():
    """C5: a "mount" node carries no record either — its children are the
    placements. The enumeration must not trip on the record-less node."""
    adapter = _role_adapter(role="FPGA")
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1")],
        [Tree(name="tree_a", anchor=TreeAnchor(is_origin=True),
              nodes=[_mount("m1", "FPGA",
                            children=[_node(ref="E1", xy=(1.0, 2.0))])]),
         _origin_tree([_node(ref="E2", xy=(2.0, 0.0))], name="tree_b")],
    )
    tree_a = next(t for t in link_trees(cfg, cfg.trees) if t.name == "tree_a")
    assert _structural_candidates(tree_a) == {"E1", "CH0"}

    assert [c.name for c in materialize_entity_placements(adapter, cfg, {}, only=["E1"])] == ["E1"]
    assert [c.name for c in materialize_entity_placements(adapter, cfg, {}, only=["E2"])] == ["E2"]


# ── C6: freshness between names ─────────────────────────────────────────────

def test_c6_the_next_call_reads_the_board_the_previous_one_moved():
    """C6: no inter-name cache. The board moves between two narrowed calls; the
    second call's clone must sit at the NEW position (an introduced
    materialization cache would return the stale one)."""
    fpga = MagicMock(spec=FootprintInstance)
    fpga.ref = "IC1"
    fpga._role = "BBB"
    fpga.position = Vector2.from_xy_mm(10.0, 0.0)
    fpga.angle_deg = 0.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fpga]
    adapter.get_field_value.side_effect = (
        lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None)
    adapter.get_selected_items.return_value = []
    cfg = _cfg([Entity(name="E2", cell="c", cluster="CH1")],
               [Tree(name="tree_b", anchor=TreeAnchor(role="BBB"),
                     nodes=[_node(ref="E2", xy=(1.0, 0.0))])])

    first = materialize_entity_placements(adapter, cfg, {}, only=["E2"])
    fpga.position = Vector2.from_xy_mm(20.0, 0.0)  # the previous name moved it
    second = materialize_entity_placements(adapter, cfg, {}, only=["E2"])

    assert first[0].xy[0] == pytest.approx(11.0)
    assert second[0].xy[0] == pytest.approx(21.0)


# ── C7: backward compatibility ──────────────────────────────────────────────

def test_c7_no_axes_and_empty_lists_never_narrow():
    """C7: a call without only/cluster (the ~40 existing positional callers,
    diagnostics/tree_mount_baseline.py included) returns exactly the same list
    as before the change — and an EMPTY list must not be read as "nothing is
    wanted" (M4)."""
    adapter = _role_adapter()
    cfg = _knockout_cfg()

    baseline = materialize_entity_placements(adapter, cfg, {})
    assert [c.name for c in baseline] == ["E1", "E2"]
    assert materialize_entity_placements(adapter, cfg, {}, only=[], cluster=[]) == baseline
    assert materialize_entity_placements(adapter, cfg, {}, only=None, cluster=None) == baseline


# ── C8: the --cluster axis ──────────────────────────────────────────────────

def test_c8_cluster_axis_uses_the_shared_matcher_and_the_name_fallback():
    """C8: cluster=[...] without --only narrows the same way (segment-prefix via
    the SHARED matcher, never a copy), and a cluster-less Entity's clone —
    whose cluster falls back to its own NAME in _to_clone — is matched by that
    name."""
    cfg = _cfg(
        [Entity(name="E1", cell="c", cluster="CH0/DAC"),
         Entity(name="E2", cell="c", cluster="CH1"),
         Entity(name="E3", cell="c")],  # no cluster -> clone.cluster == name
        [_origin_tree([_node(ref="E1", xy=(1.0, 0.0))], name="tree_a"),
         _origin_tree([_node(ref="E2", xy=(2.0, 0.0))], name="tree_b"),
         _origin_tree([_node(ref="E3", xy=(3.0, 0.0))], name="tree_c")],
    )
    assert [c.name for c in materialize_entity_placements(None, cfg, {}, cluster=["CH0"])] == ["E1"]
    assert [c.name for c in materialize_entity_placements(None, cfg, {}, cluster=["CH1"])] == ["E2"]
    assert [c.name for c in materialize_entity_placements(None, cfg, {}, cluster=["E3"])] == ["E3"]
    # nothing matches -> nothing materializes, and never an exception.
    assert materialize_entity_placements(None, cfg, {}, cluster=["NOPE"]) == []


# ── C9 / P.2.3: the fatals that MUST survive, and the one that does not ─────

def _broken_anchor_cfg():
    """tree_a (places E1, origin) plus tree_b (places E2) anchored on Entity E9
    that NO tree places — a config error that is fatal for the tree's own run."""
    return _cfg(
        [Entity(name="E1", cell="c", cluster="CH0"),
         Entity(name="E2", cell="c", cluster="CH1"),
         Entity(name="E9", cell="c", cluster="CH9")],
        [Tree(name="tree_a", anchor=TreeAnchor(is_origin=True),
              nodes=[_node(ref="E1", xy=(1.0, 0.0))]),
         Tree(name="tree_b", anchor=TreeAnchor(ref="E9"),
              nodes=[_node(ref="E2", xy=(2.0, 0.0))])],
    )


def test_c9_a_broken_anchor_of_the_NEEDED_tree_still_fatals():
    """C9 (P.2.3): narrowing must never swallow the fatal of the tree it DOES
    apply. --only E2 keeps tree_b (E2 lives there), whose Entity-ref anchor
    points at an unplaced Entity -> _EntityAnchorError, whole run."""
    cfg = _broken_anchor_cfg()
    with pytest.raises(ValidationError, match="not placed in any tree"):
        materialize_entity_placements(None, cfg, {}, only=["E2"])


def test_p23_a_broken_anchor_of_an_UNSELECTED_tree_is_deferred_to_its_own_run(caplog):
    """P.2.3, stated out loud: with --only E1 the broken tree_b is no longer
    reached, so THIS run neither warns nor fatals about it (before the change
    the fatal surfaced). The cascade gives tree_b its own run, where exactly the
    same fatal fires — the error is deferred, never lost."""
    cfg = _broken_anchor_cfg()
    with caplog.at_level(logging.WARNING,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(None, cfg, {}, only=["E1"])
    assert [c.name for c in clones] == ["E1"]
    assert "tree_b" not in caplog.text

    # ... and in its own run the same fatal is raised again, unchanged.
    with pytest.raises(ValidationError, match="not placed in any tree"):
        materialize_entity_placements(None, cfg, {}, only=["E2"])
