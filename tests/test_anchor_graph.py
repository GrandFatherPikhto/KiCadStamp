#!/usr/bin/env python3
"""
Tests for anchor_graph.py — the STATIC anchor-dependency graph (§0 of
plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md): producer index,
edge resolution (single/zero/multiple parents, anchor_ref external leaf,
anchor_point leaf) and whole-graph structure — all without any live board.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config import (
    Config, Cell, TemplateComponentSlot, ManualSpoke, Rule,
    ClonePlacement, CoordinatePlacement, Point,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.anchor_graph import (
    Record, ExternalLeaf, AnchorGraph,
    build_records, build_producer_index, resolve_anchor_edge,
    build_anchor_graph, record_key, external_key,
    collect_dependents, topological_order, redraw_records_in_order,
)


def _cell(name, *roles):
    return Cell(name=name, components=[
        TemplateComponentSlot(role=r, offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0)
        for r in roles
    ])


def test_build_producer_index_clone_rule_coordinate():
    """§0.2: the index maps (cluster, role, sheet) -> producer records for
    clone_placements (cluster = name), rules (cluster = spoke.cluster) and
    coordinate_placements (its own cluster/role/sheet)."""
    buf_cell = _cell("buf_cell", "DAC_BUF", "AVDD")
    cfg = Config(
        cells={"buf_cell": buf_cell},
        clone_placements=[
            ClonePlacement(cluster="CH0", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_0"),
            ClonePlacement(cluster="CH1", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_1"),
        ],
        rules=[
            Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="buf_cell", cluster="R_CL")]),
        ],
        coordinate_placements=[
            CoordinatePlacement(cluster="CP_C", role="CP_R", sheet="CP_S"),
        ],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)

    assert index[("CH0", "DAC_BUF", "Channel_0")] == [records[0]]
    assert index[("CH0", "AVDD", "Channel_0")] == [records[0]]
    assert index[("CH1", "DAC_BUF", "Channel_1")] == [records[1]]
    assert index[("R_CL", "DAC_BUF", None)] == [records[2]]
    assert index[("R_CL", "AVDD", None)] == [records[2]]
    assert index[("CP_C", "CP_R", "CP_S")] == [records[3]]


def test_producer_index_rule_two_clusters_same_role():
    """A rule producing the same role under two clusters appears under two
    index keys — each spoke is a separate production entry."""
    cell = _cell("c", "R")
    cfg = Config(
        cells={"c": cell},
        rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="c", cluster="CL_A"),
            ManualSpoke(pad="2", cell="c", cluster="CL_B"),
        ])],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    assert index[("CL_A", "R", None)] == [records[0]]
    assert index[("CL_B", "R", None)] == [records[0]]


def test_resolve_anchor_edge_single_candidate():
    """anchor_role narrowing to exactly one producer -> single parent."""
    cell = _cell("buf_cell", "DAC_BUF")
    cfg = Config(
        cells={"buf_cell": cell},
        clone_placements=[
            ClonePlacement(cluster="CH0", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_0"),
        ],
        coordinate_placements=[
            CoordinatePlacement(cluster="CONS", role="CONS_R",
                                anchor_role="DAC_BUF", anchor_sheet="Channel_0"),
        ],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    parents = resolve_anchor_edge(records[-1], cfg, index, {})
    assert len(parents) == 1
    assert parents[0].kind == "clone" and parents[0].name == "CH0"


def test_resolve_anchor_edge_zero_candidates_fatal():
    """anchor_role with NO producer at all -> fatal (anchor points into nowhere)."""
    cfg = Config(coordinate_placements=[
        CoordinatePlacement(cluster="C", role="R", anchor_role="NOWHERE"),
    ])
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    with pytest.raises(ValidationError):
        resolve_anchor_edge(records[0], cfg, index, {})


def test_resolve_anchor_edge_multiple_parents():
    """anchor_role without enough narrowing -> 2+ parents (a DAG point, expected)."""
    cell = _cell("buf_cell", "DAC_BUF")
    cfg = Config(
        cells={"buf_cell": cell},
        clone_placements=[
            ClonePlacement(cluster="CH0", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_0"),
            ClonePlacement(cluster="CH1", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_1"),
        ],
        coordinate_placements=[
            CoordinatePlacement(cluster="C", role="R", anchor_role="DAC_BUF"),
        ],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    parents = resolve_anchor_edge(records[-1], cfg, index, {})
    assert {p.name for p in parents} == {"CH0", "CH1"}


def test_resolve_anchor_edge_sheet_narrows_to_one():
    """anchor_sheet prefix-narrows two same-role producers down to one."""
    cell = _cell("buf_cell", "DAC_BUF")
    cfg = Config(
        cells={"buf_cell": cell},
        clone_placements=[
            ClonePlacement(cluster="CH0", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_0"),
            ClonePlacement(cluster="CH1", cell="buf_cell", xy=(0.0, 0.0), sheet="Channel_1"),
        ],
        coordinate_placements=[
            CoordinatePlacement(cluster="C", role="R",
                                anchor_role="DAC_BUF", anchor_sheet="Channel_0"),
        ],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    parents = resolve_anchor_edge(records[-1], cfg, index, {})
    assert [p.name for p in parents] == ["CH0"]


def test_resolve_anchor_edge_cluster_narrows_to_one():
    """anchor_cluster prefix-narrows a rule's two same-role clusters down to one."""
    cell = _cell("c", "R")
    cfg = Config(
        cells={"c": cell},
        rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="c", cluster="CL_A"),
            ManualSpoke(pad="2", cell="c", cluster="CL_B"),
        ])],
        coordinate_placements=[
            CoordinatePlacement(cluster="C", role="R",
                                anchor_role="R", anchor_cluster="CL_A"),
        ],
    )
    records = build_records(cfg)
    index = build_producer_index(cfg, records)
    parents = resolve_anchor_edge(records[-1], cfg, index, {})
    # Both entries collapse to the SAME rule record after cluster narrowing.
    assert len(parents) == 1
    assert parents[0].kind == "rule"


def test_resolve_anchor_edge_anchor_ref_external():
    """anchor_ref -> ExternalLeaf, a legal non-fatal case."""
    cfg = Config(clone_placements=[
        ClonePlacement(cluster="X", cell="c", xy=(0.0, 0.0), anchor_ref="FPGA1"),
    ])
    records = build_records(cfg)
    edge = resolve_anchor_edge(records[0], cfg, {}, {})
    assert isinstance(edge, ExternalLeaf)
    assert edge.ref == "FPGA1"


def test_resolve_anchor_edge_anchor_point():
    """anchor_point -> the referenced points: record (a leaf)."""
    cfg = Config(
        points={"P1": Point(name="P1", xy=(1.0, 2.0))},
        clone_placements=[
            ClonePlacement(cluster="X", cell="c", xy=(0.0, 0.0), anchor_point="P1"),
        ],
    )
    records = build_records(cfg)
    points = {r.name: r for r in records if r.kind == "point"}
    edge = resolve_anchor_edge(records[0], cfg, {}, points)
    assert isinstance(edge, list) and len(edge) == 1
    assert edge[0].kind == "point" and edge[0].name == "P1"


def test_resolve_anchor_edge_no_anchor_is_none():
    """No anchor at all (absolute placement) -> None (root by construction)."""
    cfg = Config(clone_placements=[
        ClonePlacement(cluster="X", cell="c", xy=(0.0, 0.0)),
    ])
    records = build_records(cfg)
    assert resolve_anchor_edge(records[0], cfg, {}, {}) is None


def test_build_anchor_graph_external_root():
    """A record anchored on an external ref -> the external leaf is the root."""
    cell = _cell("buf_cell", "DAC_BUF")
    cfg = Config(
        cells={"buf_cell": cell},
        clone_placements=[
            ClonePlacement(cluster="CH0", cell="buf_cell", xy=(0.0, 0.0),
                           sheet="Channel_0", anchor_ref="FPGA"),
        ],
    )
    g = build_anchor_graph(cfg)
    assert g.external["ref:FPGA"].ref == "FPGA"
    assert g.parents["clone:CH0"] == ["ref:FPGA"]
    assert g.children["ref:FPGA"] == ["clone:CH0"]
    assert g.roots == ["ref:FPGA"]


def test_build_anchor_graph_role_chain():
    """consumer anchored on producer's produced role -> producer is the parent
    and the only root (both are records; the absolute producer has no anchor)."""
    prod = _cell("prod", "A")
    cons = _cell("cons", "B")
    cfg = Config(
        cells={"prod": prod, "cons": cons},
        clone_placements=[
            ClonePlacement(cluster="producer", cell="prod", xy=(0.0, 0.0)),
            ClonePlacement(cluster="consumer", cell="cons", xy=(0.0, 0.0), anchor_role="A"),
        ],
    )
    g = build_anchor_graph(cfg)
    assert g.parents["clone:consumer"] == ["clone:producer"]
    assert g.children["clone:producer"] == ["clone:consumer"]
    assert g.roots == ["clone:producer"]


def test_build_anchor_graph_retired_dropped():
    """retired records neither appear nor produce (drop_disabled_rules convention)."""
    cfg = Config(clone_placements=[
        ClonePlacement(cluster="ret", cell="c", xy=(0.0, 0.0), retired=True),
    ])
    assert build_records(cfg) == []
    g = build_anchor_graph(cfg)
    assert g.records == [] and g.roots == []


def test_record_and_external_keys_are_namespaced():
    """Node keys are kind-prefixed so a clone and a rule sharing an identity
    (and an external ref) can never collide."""
    rec = Record(kind="clone", obj=None, name="GND", sheet=None,
                 anchor_ref=None, anchor_role=None, anchor_sheet=None,
                 anchor_cluster=None, anchor_point=None, params={})
    assert record_key(rec) == "clone:GND"
    assert external_key("GND") == "ref:GND"


# ── §2: cascade traversal + topological order ────────────────────────────────


def _chain_cfg():
    """FPGA-like external node <- CoordinatePlacement <- ClonePlacement <-
    ClonePlacement — the plan's 3-level chain, built purely from config."""
    cp_cell = _cell("cp_cell", "R_CP")
    c1_cell = _cell("c1_cell", "R_C1")
    c2_cell = _cell("c2_cell", "R_C2")
    return Config(
        cells={"cp_cell": cp_cell, "c1_cell": c1_cell, "c2_cell": c2_cell},
        coordinate_placements=[
            CoordinatePlacement(cluster="CP_C", role="R_CP", anchor_ref="FPGA"),
        ],
        clone_placements=[
            ClonePlacement(cluster="C1", cell="c1_cell", xy=(0.0, 0.0), anchor_role="R_CP"),
            ClonePlacement(cluster="C2", cell="c2_cell", xy=(0.0, 0.0), anchor_role="R_C1"),
        ],
    )


def test_collect_dependents_transitive():
    """§2.1: transitive dependents of the coordinate placement are both clones."""
    g = build_anchor_graph(_chain_cfg())
    deps = collect_dependents(g, "coordinate:CP_C/R_CP")
    assert set(deps) == {"clone:C1", "clone:C2"}


def test_cascade_topological_order_chain_of_three():
    """§2.2: parent ALWAYS before child — the 3-level chain redraws in
    coordinate -> C1 -> C2 order, with the external leaf skipped."""
    g = build_anchor_graph(_chain_cfg())
    order = redraw_records_in_order(g, "ref:FPGA")
    assert [r.name for r in order] == ["CP_C/R_CP", "C1", "C2"]


def test_cascade_multiple_parents_dag():
    """A node with two parents is ordered after BOTH of them when both are
    inside the induced subgraph; a cascade from one parent still places it
    after that parent."""
    pcell = _cell("p", "R")
    ccell = _cell("c", "C")
    cfg = Config(
        cells={"p": pcell, "c": ccell},
        clone_placements=[
            ClonePlacement(cluster="P1", cell="p", xy=(0.0, 0.0)),
            ClonePlacement(cluster="P2", cell="p", xy=(0.0, 0.0)),
            ClonePlacement(cluster="C", cell="c", xy=(0.0, 0.0), anchor_role="R"),
        ],
    )
    g = build_anchor_graph(cfg)
    assert set(g.parents["clone:C"]) == {"clone:P1", "clone:P2"}

    order = topological_order(g, "clone:P1")
    assert order[0] == "clone:P1"
    assert order[1] == "clone:C"


def test_redraw_records_skip_points_and_external():
    """Points and external leaves are not --only-appliable — they are skipped
    in the cascade order, their record descendants kept."""
    cfg = Config(
        points={"P1": Point(name="P1", xy=(1.0, 2.0))},
        clone_placements=[
            ClonePlacement(cluster="X", cell="c", xy=(0.0, 0.0), anchor_point="P1"),
        ],
    )
    g = build_anchor_graph(cfg)
    order = redraw_records_in_order(g, "point:P1")
    assert [r.name for r in order] == ["X"]


def test_point_chain_cascade_through_point():
    """A point is NOT an unconditional leaf: a point anchored on a produced
    role gets that producer as its parent, and a record anchored on the point
    is reached transitively by a cascade from the producer (review
    2026-08-21: the leaf shortcut silently dropped such records)."""
    prod_cell = _cell("prod", "R")
    y_cell = _cell("y_cell", "OTHER")
    cfg = Config(
        cells={"prod": prod_cell, "y_cell": y_cell},
        clone_placements=[
            ClonePlacement(cluster="X", cell="prod", xy=(0.0, 0.0)),
            ClonePlacement(cluster="Y", cell="y_cell", xy=(0.0, 0.0), anchor_point="P"),
        ],
        points={"P": Point(name="P", anchor_role="R")},
    )
    g = build_anchor_graph(cfg)
    assert g.parents["point:P"] == ["clone:X"]
    assert g.parents["clone:Y"] == ["point:P"]
    order = redraw_records_in_order(g, "clone:X")
    assert [r.name for r in order] == ["X", "Y"]


def test_anchor_cycle_is_fatal():
    """A pure anchor cycle (records looping onto each other, no root reaching
    them) is a config error — build_anchor_graph raises instead of silently
    dropping the records from the tree (review 2026-08-21)."""
    a_cell = _cell("a", "RA")
    b_cell = _cell("b", "RB")
    cfg = Config(
        cells={"a": a_cell, "b": b_cell},
        clone_placements=[
            ClonePlacement(cluster="A", cell="a", xy=(0.0, 0.0), anchor_role="RB"),
            ClonePlacement(cluster="B", cell="b", xy=(0.0, 0.0), anchor_role="RA"),
        ],
    )
    with pytest.raises(ValidationError, match="cycle"):
        build_anchor_graph(cfg)
