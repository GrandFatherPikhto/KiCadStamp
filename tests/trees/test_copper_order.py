# tests/test_copper_order.py
"""Tests for the "copper goes after the pads it connects" edges (Э2) and for
the planner's copper provider.

plan_2026_09_17_order_pass_and_component_node, Э2 + guard С4.

Two layers are tested here, deliberately:

  * kicadstamp.copper_order.copper_node_dependencies — the CONFIG half: a
    record's `pads: ["ROLE.pad", ...]` ends are turned into the tree nodes that
    place those roles. The lookup is the project's own predicate
    (trees.find_role_placement_matches), so these tests build a real Config
    (cells with slots, entities, plain Trees, net_traces) and assert which nodes
    each copper now waits for. No board, no adapter: the builder is pure;
  * tree_position._copper_provider — the GRAPH half: the edges it yields for a
    given {copper: {owners}} map.

What the edges do NOT change today is asserted too (and explained in the
report): the group-2 "copper last" slot already sinks every copper vertex behind
everything else, so a copper edge refines a graph that is already correct at the
ordering level. The guards below therefore pin the EDGES themselves, not an
order that the safety net would produce anyway.
"""
import pytest

from kicadstamp.anchor_graph import Record
from kicadstamp.config import Cell, Config, Entity, NetTrace, TemplateComponentSlot
from kicadstamp.copper_order import copper_node_dependencies
from kicadstamp.link_trees import LinkedAnchor, LinkedNode, LinkedTree
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.tree_position import (
    _copper_provider,
    curated_redraw_plan_forest,
)


# ── config doubles ───────────────────────────────────────────────────────────

def _slot(role):
    return TemplateComponentSlot(role=role)


def _cell(name, *roles):
    return Cell(name=name, components=[_slot(r) for r in roles])


def _entity(name, cell, cluster=None, sheet=None):
    return Entity(name=name, cell=cell, cluster=cluster, sheet=sheet)


def _node(ref, kind=None, xy=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=[])


def _tree(name, nodes):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _trace(name, pads, *, anchor_role="C_OUT_BULK", anchor_pad="1",
           anchor_cluster=None, anchor_sheet=None):
    return NetTrace(net="+3V3", anchor_role=anchor_role, name=name, pads=pads,
                    anchor_pad=anchor_pad, anchor_cluster=anchor_cluster,
                    anchor_sheet=anchor_sheet)


def _cfg(cells, entities, trees, traces):
    return Config(cells={c.name: c for c in cells}, entities=entities,
                  trees=trees, net_traces=traces)


# The live shape (profile 3ch-awg-tia-v103): one tree instance per channel; its
# copper bridges the DAC_BUF cluster (whose cell carries the AD_DAC role) and
# the PIF_CLKVDD cluster (whose cell carries C_OUT_BULK, the record's anchor).
def _live_shape(*, dac_cell_roles=("AD_DAC",), pif_cell_roles=("C_OUT_BULK",)):
    cells = [
        _cell("dac_cell", *dac_cell_roles),
        _cell("pif_cell", *pif_cell_roles),
    ]
    entities = [
        _entity("dac_buf_channel_0", "dac_cell", "DAC_BUF", "Channel_0"),
        _entity("pif_clkvdd_channel_0", "pif_cell", "PIF_CLKVDD", "Channel_0"),
    ]
    copper = "3v3_clkvdd__dac_buf__pif_clkvdd"
    trees = [_tree("ch0_dac_buf", [
        _node(copper, "net_trace"),
        _node("dac_buf_channel_0", "placement", xy=(0.0, 0.0)),
        _node("pif_clkvdd_channel_0", "placement", xy=(1.0, 0.0)),
    ])]
    traces = [_trace(copper, ["AD_DAC.11", "C_OUT_BULK.1"],
                     anchor_cluster="PIF_CLKVDD", anchor_sheet="Channel_0")]
    return _cfg(cells, entities, trees, traces), copper


# ── the config half: a record's pads -> owning tree nodes ────────────────────

def test_both_ends_of_a_copper_record_map_to_their_nodes():
    """С4: the record connects a component of the DAC cell (role AD_DAC) to its
    own anchor component (role C_OUT_BULK, narrowed by the record's
    anchor_cluster/anchor_sheet) — both ends become edges."""
    cfg, copper = _live_shape()
    assert copper_node_dependencies(cfg) == {
        copper: {"dac_buf_channel_0", "pif_clkvdd_channel_0"}}


def test_an_end_the_tree_does_not_place_contributes_no_edge():
    """Т2.1: the component of one end is NOT placed by this tree (its role is
    not in any cell of this tree) — legal, and the other end still gives its
    edge."""
    cfg, copper = _live_shape(dac_cell_roles=())  # AD_DAC is nowhere
    assert copper_node_dependencies(cfg) == {
        copper: {"pif_clkvdd_channel_0"}}


def test_an_ambiguous_role_inside_one_tree_invents_no_edge():
    """Т2.1/the no-guess rule: TWO nodes of the same tree place a cell carrying
    the queried role and nothing in the record tells them apart -> no edge at
    all (never "whichever came last")."""
    cfg, copper = _live_shape()
    # A second PIF node whose cell ALSO carries C_OUT_BULK, and an end that is
    # not the anchor end (pad 2, while the anchor pad is 1) -> role-only lookup
    # -> 2 nodes -> the copper keeps only its DAC edge.
    cfg.cells["pif_cell_2"] = _cell("pif_cell_2", "C_OUT_BULK")
    cfg.entities.append(_entity("pif_avdd_channel_0", "pif_cell_2",
                                "PIF_AVDD", "Channel_0"))
    cfg.trees[0].nodes.append(_node("pif_avdd_channel_0", "placement",
                                    xy=(2.0, 0.0)))
    cfg.net_traces = [NetTrace(net="+3V3", anchor_role="FPGA", name=copper,
                               pads=["C_OUT_BULK.2"], anchor_pad=None)]
    assert copper_node_dependencies(cfg) == {}


def test_the_anchor_end_is_pinned_by_the_records_own_cluster_and_sheet():
    """The anchor end (same role AND pad as anchor_role/anchor_pad) is narrowed
    with anchor_cluster/anchor_sheet — the same addressing the NetTrace anchor
    itself is resolved with. Two nodes carry the role; exactly one is the
    anchor's cluster, and that one becomes the edge."""
    cfg, copper = _live_shape()
    cfg.cells["pif_cell_2"] = _cell("pif_cell_2", "C_OUT_BULK")
    cfg.entities.append(_entity("pif_avdd_channel_0", "pif_cell_2",
                                "PIF_AVDD", "Channel_0"))
    cfg.trees[0].nodes.append(_node("pif_avdd_channel_0", "placement",
                                    xy=(2.0, 0.0)))
    assert copper_node_dependencies(cfg) == {
        copper: {"dac_buf_channel_0", "pif_clkvdd_channel_0"}}


def test_a_legacy_record_without_pads_is_ignored():
    """A record written before 2026-09-12 carries no `pads:` at all; it can only
    be matched by net, so this builder must not invent edges for it."""
    cfg, copper = _live_shape()
    cfg.net_traces = [_trace(copper, None, anchor_cluster="PIF_CLKVDD",
                             anchor_sheet="Channel_0")]
    assert copper_node_dependencies(cfg) == {}


def test_the_owning_tree_scopes_the_answer():
    """MEASURED LIVE, and the reason this builder is tree-scoped: a Role alone
    is not a board-wide identity — the same role lives in several Clusters and
    on several sheets ("C_OUT_BULK" is on 33 components of the real board). Two
    INSTANCE trees of the same shape each resolve the same role to their OWN
    node, because each instance tree carries its own sheet."""
    cells = [_cell("dac_cell", "AD_DAC"), _cell("pif_cell", "C_OUT_BULK")]
    entities = [
        _entity("dac_buf_channel_0", "dac_cell", "DAC_BUF", "Channel_0"),
        _entity("pif_clkvdd_channel_0", "pif_cell", "PIF_CLKVDD", "Channel_0"),
        _entity("dac_buf_channel_0__ch1_dac_buf", "dac_cell", "DAC_BUF", "Channel_1"),
        _entity("pif_clkvdd_channel_0__ch1_dac_buf", "pif_cell", "PIF_CLKVDD",
                "Channel_1"),
    ]
    c0 = "3v3_clkvdd__dac_buf__pif_clkvdd"
    c1 = "3v3_clkvdd__dac_buf__pif_clkvdd__ch1_dac_buf"
    trees = [
        _tree("ch0_dac_buf", [
            _node(c0, "net_trace"),
            _node("dac_buf_channel_0", "placement", xy=(0.0, 0.0)),
            _node("pif_clkvdd_channel_0", "placement", xy=(1.0, 0.0))]),
        _tree("ch1_dac_buf", [
            _node(c1, "net_trace"),
            _node("dac_buf_channel_0__ch1_dac_buf", "placement", xy=(0.0, 0.0)),
            _node("pif_clkvdd_channel_0__ch1_dac_buf", "placement", xy=(1.0, 0.0))]),
    ]
    traces = [
        _trace(c0, ["AD_DAC.11", "C_OUT_BULK.1"], anchor_cluster="PIF_CLKVDD",
               anchor_sheet="Channel_0"),
        _trace(c1, ["AD_DAC.11", "C_OUT_BULK.1"], anchor_cluster="PIF_CLKVDD",
               anchor_sheet="Channel_1"),
    ]
    deps = copper_node_dependencies(_cfg(cells, entities, trees, traces))
    assert deps == {
        c0: {"dac_buf_channel_0", "pif_clkvdd_channel_0"},
        c1: {"dac_buf_channel_0__ch1_dac_buf",
             "pif_clkvdd_channel_0__ch1_dac_buf"},
    }


def test_a_config_without_copper_traces_asks_nothing():
    """Cheap exit: no trees, no net_traces records, or a tree whose net_trace
    nodes own no record -> an empty map, never an exception."""
    cfg, _ = _live_shape()
    cfg.net_traces = []
    assert copper_node_dependencies(cfg) == {}
    assert copper_node_dependencies(Config()) == {}


def test_a_pad_end_without_a_role_prefix_is_skipped():
    """A malformed label ("no_dot") has no role to look up; it must be skipped,
    not treated as a role named ""."""
    cfg, copper = _live_shape()
    cfg.net_traces = [_trace(copper, ["no_dot"],
                             anchor_cluster="PIF_CLKVDD",
                             anchor_sheet="Channel_0")]
    assert copper_node_dependencies(cfg) == {}


# ── the graph half: the provider's edges ─────────────────────────────────────

def test_the_copper_provider_yields_one_edge_per_owner_inside_the_run():
    """С4 at the graph level: {copper: {A, B}} becomes A -> copper and
    B -> copper for the vertices of the run, and an owner OUTSIDE the run is
    not invented as a vertex."""
    provider = _copper_provider({"2v5_oa__x": {"DAC_BUF", "OUTSIDE"}})
    assert sorted(provider.edges(frozenset({"DAC_BUF", "2v5_oa__x"}))) == [
        ("DAC_BUF", "2v5_oa__x")]


def test_the_copper_provider_names_itself_for_a_cycle_report():
    """Т1.4: the provider's name is what a cycle report prints."""
    assert _copper_provider({"x": {"A"}}).name == "copper"


# ── end to end through the planner ───────────────────────────────────────────

def _record(kind, name, obj=None) -> Record:
    return Record(kind=kind, obj=obj if obj is not None else object(), name=name,
                  sheet=None, anchor_ref=None, anchor_role=None, anchor_sheet=None,
                  anchor_cluster=None, anchor_point=None, params={})


def _linked(ref, kind, record=None) -> LinkedNode:
    return LinkedNode(node=_node(ref, kind), record=record, is_external=False,
                      children=[])


def _linked_forest():
    """The live shape as the PLANNER sees it: copper first in document order
    (so a lexicographic/document order would apply it first), two components
    after it."""
    copper = "3v3_clkvdd__dac_buf__pif_clkvdd"
    anchor = LinkedAnchor(anchor=TreeAnchor(is_origin=True), record=None,
                          is_origin=True, is_external=False)
    nodes = [
        _linked(copper, "net_trace", _record("net_trace", copper)),
        _linked("dac_buf_channel_0", "placement",
                _record("placement", "dac_buf_channel_0")),
        _linked("pif_clkvdd_channel_0", "placement",
                _record("placement", "pif_clkvdd_channel_0")),
    ]
    return [LinkedTree(name="ch0_dac_buf", anchor=anchor, nodes=nodes)], copper


def test_the_planner_applies_copper_after_its_own_components():
    """С4 end to end: the copper ref is declared FIRST and sorts lexicographically
    FIRST, yet it is applied after both components its pads belong to."""
    linked, copper = _linked_forest()
    names, _warnings = curated_redraw_plan_forest(
        linked, {copper, "dac_buf_channel_0", "pif_clkvdd_channel_0"},
        copper_deps={copper: {"dac_buf_channel_0", "pif_clkvdd_channel_0"}})
    assert names == ["dac_buf_channel_0", "pif_clkvdd_channel_0", copper]


def test_todays_edges_do_not_move_a_name_the_safety_net_dominates():
    """MEASURED FACT, deliberately pinned (and reported honestly in the Э2
    report): the group-2 "copper last" slot sinks every copper vertex behind
    everything else, so a copper edge refines the GRAPH without changing the
    emitted order — the same run with and without `copper_deps` plans the same
    names in the same order.

    That is why keeping the safety net (Т2.2) costs nothing, and why the edges
    are worth having anyway: they state the dependency explicitly (the record's
    own `pads:` ends), they are what a cycle report names, and they are the
    thing that still holds if the coarse slot is ever retired."""
    linked, copper = _linked_forest()
    selected = {copper, "dac_buf_channel_0", "pif_clkvdd_channel_0"}
    with_edges, _w1 = curated_redraw_plan_forest(
        linked, selected, copper_deps={copper: {"dac_buf_channel_0"}})
    without, _w2 = curated_redraw_plan_forest(linked, selected)
    assert with_edges == without == ["dac_buf_channel_0",
                                     "pif_clkvdd_channel_0", copper]


def test_the_planner_is_unchanged_when_no_dependencies_are_given():
    """The copper_deps parameter is OPTIONAL: without it the pre-Э2 order is
    produced byte for byte (the safety net alone)."""
    linked, copper = _linked_forest()
    selected = {copper, "dac_buf_channel_0", "pif_clkvdd_channel_0"}
    without, _w1 = curated_redraw_plan_forest(linked, selected)
    empty, _w2 = curated_redraw_plan_forest(linked, selected, copper_deps={})
    assert without == empty


def test_a_dependency_on_a_record_outside_the_run_is_not_an_error():
    """Т2.1 through the planner: an owner that is not a vertex of this run is
    dropped by the order machine, and the copper is still planned."""
    linked, copper = _linked_forest()
    names, _warnings = curated_redraw_plan_forest(
        linked, {copper, "dac_buf_channel_0"},
        copper_deps={copper: {"SOMETHING_NOT_SELECTED"}})
    assert names == ["dac_buf_channel_0", copper]


def test_the_config_half_feeds_the_graph_half():
    """The two halves meet: the builder's keys are the copper node refs the
    planner plans, and its values are node refs of the same forest."""
    cfg, copper = _live_shape()
    deps = copper_node_dependencies(cfg)
    # The planner receives the builder's map verbatim and plans the copper last.
    linked, _ = _linked_forest()
    names, _w = curated_redraw_plan_forest(
        linked, {copper, "dac_buf_channel_0", "pif_clkvdd_channel_0"},
        copper_deps=deps)
    assert names[-1] == copper
    assert deps[copper] == {"dac_buf_channel_0", "pif_clkvdd_channel_0"}
