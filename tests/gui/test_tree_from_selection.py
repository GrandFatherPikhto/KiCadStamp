"""Pure-logic tests for "Tools -> Extract tree..." (2026-09-01, plan
extract_selection_as_tree.md) — gui/docks/tree_from_selection.py, no Qt.

Covers: fully-selected cluster detection (reused from reead.py), the
Tree-building from clusters (nodes, autopositioning, validation), anchor
construction, and inter-cluster net detection.
"""
from gui.docks.reead import ReReadCluster, fully_selected_clusters
from gui.docks.tree_from_selection import (
    InterClusterNet,
    build_role_anchor,
    build_tree_from_clusters,
    cluster_cell_name,
    cluster_errors,
    cluster_origin_role,
    cluster_raw_items,
    detect_inter_cluster_nets,
    resolve_cluster_entity,
    tree_anchor_from_cluster_entity,
)
from kicadstamp.config import Config, load_tree
from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.explore import Selected
from kicadstamp.link_trees import link_trees
from kicadstamp.trees import Tree, TreeAnchor, tree_to_dict


def _sel(ref, cluster, sheet, nets=None):
    """A Selected footprint with a single-segment sheet chain (the matching
    convention is 'entity.sheet in fp.sheet', same as Board.select(sheet=))."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[sheet],
                    nets=nets or {}, fp=object())


def _slot(role, along=0.0, across=0.0):
    return TemplateComponentSlot(role=role, offset_along_mm=along,
                                 offset_across_mm=across)


def _fp(ref):
    """A minimal Footprint DTO (cluster_raw_items only reads .ref)."""
    from kicadstamp.domain.board import Footprint
    from kicadstamp.domain.geometry import Vector2
    return Footprint(ref=ref, uuid=f"u-{ref}", position=Vector2.from_xy(0, 0),
                     angle_deg=0.0, layer=None)


def _cfg(entities=None, cells=None, trees=None, rules=None):
    return Config(
        entities=entities or [],
        cells={c.name: c for c in (cells or [])},
        trees=trees or [],
        chains=rules or [],
    )


# ── fully-selected cluster detection (reuse of reead.py) ──────────────────

def test_fully_selected_clusters_reused_for_tree():
    """The SAME detection Re-read uses: PIF_AVDD on Channel_1 maps to ITS
    entity by sheet even though Channel_0/2 share the cluster tag."""
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1"), _sel("C1", "PIF_AVDD", "Channel_1"),
        _sel("R2", "PIF_AVDD", "Channel_0"), _sel("C2", "PIF_AVDD", "Channel_0"),
    ]
    selected = [_sel("R1", "PIF_AVDD", "Channel_1"), _sel("C1", "PIF_AVDD", "Channel_1")]
    entities = [
        Entity(name="CH0_PIF_AVDD", cell="dac_pif_avdd", cluster="PIF_AVDD", sheet="Channel_0"),
        Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd", cluster="PIF_AVDD", sheet="Channel_1"),
    ]
    clusters = fully_selected_clusters(selected, snapshot, entities, ())
    assert len(clusters) == 1
    assert clusters[0].entity_name == "CH1_PIF_AVDD"


def test_multiple_instances_not_mixed():
    """Two full channels -> two SEPARATE rows, never merged (the PIF_AVDD
    hierarchy case)."""
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_0"), _sel("C1", "PIF_AVDD", "Channel_0"),
        _sel("R2", "PIF_AVDD", "Channel_1"), _sel("C2", "PIF_AVDD", "Channel_1"),
    ]
    entities = [
        Entity(name="CH0_PIF_AVDD", cell="dac_pif_avdd", cluster="PIF_AVDD", sheet="Channel_0"),
        Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd", cluster="PIF_AVDD", sheet="Channel_1"),
    ]
    clusters = fully_selected_clusters(snapshot, snapshot, entities, ())
    assert [(c.sheet, c.entity_name) for c in clusters] == [
        ("Channel_0", "CH0_PIF_AVDD"), ("Channel_1", "CH1_PIF_AVDD")]


# ── build_tree_from_clusters ──────────────────────────────────────────────

def _clusters():
    return [
        ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name="CH1_PIF_AVDD",
                      cell="dac_pif_avdd", profile_key=None, refs=["R1", "C1"]),
        ReReadCluster(cluster="PIF_CLKVDD", sheet="Channel_1", entity_name="CH1_PIF_CLKVDD",
                      cell="dac_pif_clkvdd", profile_key=None, refs=["R2", "C2"]),
    ]


def _cfg_with_entities():
    return _cfg(
        entities=[
            Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd", cluster="PIF_AVDD", sheet="Channel_1"),
            Entity(name="CH1_PIF_CLKVDD", cell="dac_pif_clkvdd", cluster="PIF_CLKVDD", sheet="Channel_1"),
        ],
        cells=[
            Cell(name="dac_pif_avdd", components=[_slot("DAC")]),
            Cell(name="dac_pif_clkvdd", components=[_slot("DAC"), _slot("CAP", along=2.0)]),
        ],
    )


def _anchor():
    return TreeAnchor(role="DAC", anchor_sheet="Channel_1", anchor_cluster="PIF_AVDD")


def test_build_tree_node_shape_and_anchor_preserved():
    """Each cluster -> a top-level kind="placement" TreeNode with ref = the
    Entity's name and the anchor preserved exactly as passed."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg)

    assert errors == []
    assert tree.name == "power_tree"
    assert tree.anchor == _anchor()
    assert [n.ref for n in tree.nodes] == ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD"]
    assert all(n.kind == "placement" for n in tree.nodes)
    assert all(n.children == [] for n in tree.nodes)


def test_build_tree_autopositioning_offsets():
    """xy = entity live position - anchor base (mocked positions): the node
    offsets freeze the current geometry relative to the anchor."""
    cfg = _cfg_with_entities()
    positions = {
        "CH1_PIF_AVDD": (10.0, 20.0),
        "CH1_PIF_CLKVDD": (16.0, 25.0),
    }
    anchor_base = (5.0, 10.0)
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_base)

    assert errors == []
    assert tree.nodes[0].xy == (5.0, 10.0)      # 10-5, 20-10
    assert tree.nodes[1].xy == (11.0, 15.0)     # 16-5, 25-10


def test_build_tree_without_positions_saves_no_xy():
    """No live positions -> nodes carry xy=None (live-position rule at apply),
    the tree still builds."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg)
    assert errors == []
    assert all(n.xy is None for n in tree.nodes)


def test_build_tree_auto_derives_entity_when_missing():
    """Phase A (2026-09-01): a cluster WITHOUT an Entity no longer blocks the
    build — the node references the auto-derived Entity name (cluster+sheet),
    persisted at save time."""
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["U7", "R36"])]
    cfg = _cfg()
    tree, errors = build_tree_from_clusters(clusters, "dac_tree", _anchor(),
                                            cfg.entities, cfg)
    assert errors == []
    assert tree.nodes[0].kind == "placement"
    assert tree.nodes[0].ref == "dac_buf_channel_0"


def test_build_tree_missing_cell_auto_satisfied():
    """Phase A: an existing Entity whose cell is missing from cfg.cells is
    auto-satisfiable (the cell is generated from the cluster at save time) —
    the build no longer errors."""
    clusters = [ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1",
                              entity_name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                              profile_key=None, refs=["R1"])]
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")])
    tree, errors = build_tree_from_clusters(clusters, "t", _anchor(), cfg.entities, cfg)
    assert errors == []
    assert tree.nodes[0].ref == "CH1_PIF_AVDD"


def test_build_tree_empty_name_is_an_error():
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(_clusters(), "", _anchor(), cfg.entities, cfg)
    assert tree is None
    assert any("empty" in e.lower() for e in errors)


def test_build_tree_duplicate_name_is_an_error():
    cfg = _cfg_with_entities()
    cfg.trees = [Tree(name="power_tree", anchor=_anchor(), nodes=[])]
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg)
    assert tree is None
    assert any("already exists" in e for e in errors)


def test_cluster_errors_never_blocks_auto_satisfiable_cluster():
    """Phase A: cluster_errors no longer blocks a cluster without an Entity
    (auto-satisfiable) — returns '' per row, so the dialog never disables OK
    for these."""
    clusters = [
        ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name="CH1_PIF_AVDD",
                      cell="dac_pif_avdd", profile_key=None, refs=["R1"]),
        ReReadCluster(cluster="PIF_CLKVDD", sheet="Channel_1", entity_name=None,
                      cell="x", profile_key=None, refs=["R2"]),
    ]
    cfg = _cfg(
        entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                         cluster="PIF_AVDD", sheet="Channel_1")],
        cells=[Cell(name="dac_pif_avdd", components=[_slot("DAC")])])
    errors = cluster_errors(clusters, cfg.entities, cfg)
    assert errors == ["", ""]


# ── Phase A: auto Entity/cell derivation (2026-09-01) ─────────────────────

def test_cluster_cell_name_slug():
    assert cluster_cell_name("DAC_BUF") == "dac_buf"
    assert cluster_cell_name("PIF_AVDD") == "pif_avdd"
    assert cluster_cell_name("") == ""


def test_resolve_cluster_entity_existing_matched_entity_wins():
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")])
    c = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1",
                      entity_name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                      profile_key=None, refs=["R1"])
    name, cell, is_new = resolve_cluster_entity(c, cfg)
    assert (name, cell, is_new) == ("CH1_PIF_AVDD", "dac_pif_avdd", False)


def test_resolve_cluster_entity_auto_derives_unique_per_instance():
    """No matching Entity -> an auto Entity name unique per cluster+sheet
    instance (Channel_0/1/2 share the cluster tag) pointing at the slug cell."""
    cfg = _cfg(entities=[Entity(name="pif_avdd", cell="pif_avdd")])
    c0 = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_0",
                       entity_name=None, cell="pif_avdd", profile_key=None, refs=["R1"])
    c1 = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1",
                       entity_name=None, cell="pif_avdd", profile_key=None, refs=["R2"])
    name0, cell0, new0 = resolve_cluster_entity(c0, cfg)
    name1, cell1, new1 = resolve_cluster_entity(c1, cfg)
    assert new0 and new1
    assert name0 != name1
    assert cell0 == cell1 == "pif_avdd"
    assert name0.startswith("pif_avdd_channel_0")
    assert name1.startswith("pif_avdd_channel_1")


def test_cluster_raw_items_narrows_to_cluster_refs():
    from kicadstamp.domain.board import Track, Via
    from kicadstamp.domain.geometry import Vector2
    c = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name=None,
                      cell="pif_avdd", profile_key=None, refs=["R1", "C1"])
    fp_other = _fp("X9")
    fp_own = _fp("R1")
    raw = [fp_other, fp_own, Track(uuid="t", start=Vector2.from_xy(0, 0),
                                   end=Vector2.from_xy(1, 1), net_name="N",
                                   width_mm=0.25, layer=None)]
    narrowed = cluster_raw_items(c, raw)
    assert fp_own in narrowed and fp_other not in narrowed
    assert len(narrowed) == 2


def _sel_role(ref, cluster, sheet, role):
    """A Selected with a non-None role (cluster_origin_role reads .role)."""
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[sheet],
                    nets={}, fp=object())


def test_cluster_origin_role_unique_role_picked():
    # FB appears exactly once among the cluster's own refs -> the anchor role.
    c = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name=None,
                      cell="pif_avdd", profile_key=None, refs=["R1", "C1", "R2"])
    selected = [
        _sel_role("R1", "PIF_AVDD", "Channel_1", "FB"),
        _sel_role("C1", "PIF_AVDD", "Channel_1", "CAP"),
        _sel_role("R2", "PIF_AVDD", "Channel_1", "CAP"),
        _sel_role("X9", "PIF_AVDD", "Channel_1", "CAP"),  # foreign ref, ignored
    ]
    assert cluster_origin_role(c, selected) == "FB"
    # no unique role -> None
    c2 = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1", entity_name=None,
                       cell="pif_avdd", profile_key=None, refs=["R1", "C1", "R2"])
    selected2 = [
        _sel_role("R1", "PIF_AVDD", "Channel_1", "CAP"),
        _sel_role("C1", "PIF_AVDD", "Channel_1", "CAP"),
        _sel_role("R2", "PIF_AVDD", "Channel_1", "CAP"),
    ]
    assert cluster_origin_role(c2, selected2) is None


def test_build_tree_auto_entity_round_trip_with_link_trees():
    """The phase-A save flow: build a node with the auto-derived Entity name,
    then add that Entity to cfg -> link_trees resolves the placement node."""
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["U7", "R36"])]
    cfg = _cfg()
    tree, errors = build_tree_from_clusters(clusters, "dac_tree", _anchor(),
                                            cfg.entities, cfg)
    assert errors == []
    entity_name, cell_name, is_new = resolve_cluster_entity(clusters[0], cfg)
    assert is_new
    auto = Entity(name=entity_name, cell=cell_name,
                  cluster="DAC_BUF", sheet="Channel_0")
    cfg2 = _cfg(entities=[auto],
                cells=[Cell(name=cell_name, components=[_slot("DAC")])])
    cfg2.trees = [tree]
    linked = link_trees(cfg2, [tree])
    assert linked[0].nodes[0].record.name == entity_name
    assert linked[0].nodes[0].record.kind == "placement"


# ── anchor construction ───────────────────────────────────────────────────

def test_build_role_anchor_fields():
    anchor = build_role_anchor("Channel_1", "PIF_AVDD", "DAC", "3")
    assert anchor.role == "DAC"
    assert anchor.anchor_sheet == "Channel_1"
    assert anchor.anchor_cluster == "PIF_AVDD"
    assert anchor.anchor_pad == "3"
    assert anchor.is_origin is False


def test_build_role_anchor_pad_optional_and_none_normalized():
    anchor = build_role_anchor(None, None, "DAC")
    assert anchor.role == "DAC"
    assert anchor.anchor_sheet is None
    assert anchor.anchor_cluster is None
    assert anchor.anchor_pad is None


def test_tree_anchor_from_cluster_entity_prefills_zero_slot():
    """The "existing cluster anchor": sheet/cluster from the Entity, role =
    the zero-offset component of its cell."""
    entity = Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                    cluster="PIF_AVDD", sheet="Channel_1")
    cfg = _cfg(cells=[Cell(name="dac_pif_avdd", components=[_slot("DAC")])])
    anchor = tree_anchor_from_cluster_entity(entity, cfg)
    assert anchor.role == "DAC"
    assert anchor.anchor_sheet == "Channel_1"
    assert anchor.anchor_cluster == "PIF_AVDD"


def test_tree_anchor_from_cluster_entity_falls_back_to_first_slot():
    """A cell without a zero-offset slot falls back to its first component's
    role (best effort)."""
    entity = Entity(name="E", cell="c", cluster="CL", sheet="Root")
    cfg = _cfg(cells=[Cell(name="c", components=[_slot("A", along=1.0),
                                                 _slot("B", along=2.0)])])
    anchor = tree_anchor_from_cluster_entity(entity, cfg)
    assert anchor.role == "A"


# ── inter-cluster net detection ───────────────────────────────────────────

def _tr(net, width=0.25):
    return Track(uuid=f"t-{net}", start=Vector2.from_xy(0, 0),
                 end=Vector2.from_xy(1, 1), net_name=net, width_mm=width,
                 layer=None)


def _via(net, diameter=0.6):
    return Via(uuid=f"v-{net}", position=Vector2.from_xy(0, 0), net_name=net,
               drill_mm=0.3, diameter_mm=diameter)


def test_detect_inter_cluster_nets_connects_two_clusters():
    """Nets on footprints of 2+ clusters become capture candidates with the
    selected track/via counts."""
    clusters = _clusters()
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "SHARED", "2": "AVDD"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "SHARED"}),
    ]
    raw = [_tr("SHARED"), _tr("SHARED"), _via("SHARED"), _tr("AVDD")]
    nets = detect_inter_cluster_nets(raw, clusters, snapshot)
    assert nets == [InterClusterNet(net="SHARED", track_count=2, via_count=1)]


def test_detect_inter_cluster_nets_excludes_single_cluster_and_rule_nets():
    """A net only on ONE cluster's footprints, and a rule net (GND), are not
    offered for capture."""
    clusters = _clusters()
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "AVDD", "2": "GND"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "GND"}),
    ]
    raw = [_tr("AVDD"), _tr("GND")]
    nets = detect_inter_cluster_nets(raw, clusters, snapshot, rule_nets=["GND"])
    assert nets == []


def test_detect_inter_cluster_nets_empty_without_cross_cluster_copper():
    """No selected copper touches 2+ clusters -> empty list (the tab offers
    no capture)."""
    clusters = _clusters()
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "AVDD"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "CLKVDD"}),
    ]
    raw = [_tr("AVDD"), _tr("CLKVDD")]
    assert detect_inter_cluster_nets(raw, clusters, snapshot) == []


def test_detect_inter_cluster_nets_requires_selected_copper():
    """An inter-cluster net with NO selected tracks/vias is not offered (only
    selected copper can be captured)."""
    clusters = _clusters()
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "SHARED"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "SHARED"}),
    ]
    assert detect_inter_cluster_nets([], clusters, snapshot) == []


def test_detect_inter_cluster_nets_gnd_excluded_even_without_rule_nets():
    """Regression (2026-09-01, live 3CH-AWG-TIA): a shared GND must NOT be
    offered even when NO Rule/Chain registers it — RULE_NETS={"GND"} is
    subtracted by default, the same always-excluded set the Cells/Extract dock
    uses (net_resolution.RULE_NETS)."""
    clusters = _clusters()
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "GND", "2": "AVDD"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "GND"}),
    ]
    raw = [_tr("GND"), _via("GND")]
    assert detect_inter_cluster_nets(raw, clusters, snapshot) == []


def test_detect_inter_cluster_nets_rail_on_3_clusters_excluded():
    """A net on pads of MORE than 2 selected Clusters is a ubiquitous rail
    (+3V3), not a point-to-point link — not offered (coverage=3 > 2). Live
    finding: +3V3 sat on 3 of 6 selected Clusters and leaked."""
    clusters = _clusters() + [
        ReReadCluster(cluster="PIF_DVDD", sheet="Channel_1", entity_name="CH1_PIF_DVDD",
                      cell="dac_pif_dvdd", profile_key=None, refs=["C3"]),
    ]
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "+3V3"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "+3V3"}),
        _sel("C3", "PIF_DVDD", "Channel_1", {"1": "+3V3"}),
    ]
    raw = [_tr("+3V3")]
    assert detect_inter_cluster_nets(raw, clusters, snapshot) == []


def test_detect_inter_cluster_nets_real_2cluster_link_kept_with_3rd_cluster():
    """The coverage rule must NOT drop a real point-to-point link: SHARED sits
    on exactly 2 of 3 selected Clusters -> still offered (coverage=2 <= 2)."""
    clusters = _clusters() + [
        ReReadCluster(cluster="PIF_DVDD", sheet="Channel_1", entity_name="CH1_PIF_DVDD",
                      cell="dac_pif_dvdd", profile_key=None, refs=["C3"]),
    ]
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "SHARED", "2": "AVDD"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "SHARED"}),
        _sel("C3", "PIF_DVDD", "Channel_1", {"1": "DVDD"}),
    ]
    raw = [_tr("SHARED")]
    assert detect_inter_cluster_nets(raw, clusters, snapshot) == [
        InterClusterNet(net="SHARED", track_count=1, via_count=0)]


def test_detect_inter_cluster_nets_max_cluster_coverage_configurable():
    """max_cluster_coverage raises the rail threshold: a net on exactly 3
    Clusters is excluded at the default (2) but offered at 3."""
    clusters = _clusters() + [
        ReReadCluster(cluster="PIF_DVDD", sheet="Channel_1", entity_name="CH1_PIF_DVDD",
                      cell="dac_pif_dvdd", profile_key=None, refs=["C3"]),
    ]
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1", {"1": "+3V3"}),
        _sel("R2", "PIF_CLKVDD", "Channel_1", {"1": "+3V3"}),
        _sel("C3", "PIF_DVDD", "Channel_1", {"1": "+3V3"}),
    ]
    raw = [_tr("+3V3")]
    assert detect_inter_cluster_nets(raw, clusters, snapshot) == []          # coverage 3 > 2
    assert detect_inter_cluster_nets(
        raw, clusters, snapshot, max_cluster_coverage=3) == [
            InterClusterNet(net="+3V3", track_count=1, via_count=0)]


# ── round-trip / link_trees ───────────────────────────────────────────────

def test_tree_round_trip_and_link_trees_with_role_anchor_and_n_nodes():
    """tree_to_dict -> load_tree == the built tree, and link_trees is happy
    with an explicit role anchor + N top-level placement nodes (the "exactly
    one top-level placement" rule is only for is_auto anchors)."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        entity_positions={"CH1_PIF_AVDD": (10.0, 20.0), "CH1_PIF_CLKVDD": (16.0, 25.0)},
        anchor_base=(5.0, 10.0))
    assert errors == []

    d = tree_to_dict(tree)
    reloaded = load_tree(d)
    assert reloaded == tree

    linked = link_trees(cfg, [tree])
    assert len(linked[0].nodes) == 2
    assert all(ln.record.kind == "placement" for ln in linked[0].nodes)
    # role anchor links to no config record (silently external) — no fatal.
    assert linked[0].anchor.anchor.role == "DAC"
