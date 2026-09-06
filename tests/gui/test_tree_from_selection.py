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
    create_cell_and_entity_for_cluster,
    detect_inter_cluster_nets,
    extract_new_cell_for_instantiation,
    resolve_cluster_entity,
    tree_anchor_from_cluster_entity,
)
import pytest
from kicadstamp.config import Config, load_tree
from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.explore import Selected
from kicadstamp.utils.units import MM
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import (
    child_absolute_position,
    child_local_offset,
    relative_rotation_deg,
)
from kicadstamp.trees import Tree, TreeAnchor, tree_to_dict


def _sel(ref, cluster, sheet, nets=None):
    """A Selected footprint with a single-segment sheet chain (the matching
    convention is 'entity.sheet in fp.sheet', same as Board.select(sheet=))."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[sheet],
                    nets=nets or {}, fp=object())


def _slot(role, along=0.0, across=0.0, angle=0.0):
    return TemplateComponentSlot(role=role, offset_along_mm=along,
                                 offset_across_mm=across, angle_deg=angle)


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
    """A NON-colliding explicit role anchor for the generic build tests: role
    "REMOTE" never equals any checked cluster's own mount role, so EVERY cluster
    becomes a node (the N clusters -> N nodes shape these tests assert). The
    self-anchor duplicate skip (plan_2026_09_05_tree_root_rotation_drift §1) is
    exercised separately with _anchor_matching()."""
    return TreeAnchor(role="REMOTE", anchor_sheet="Channel_1", anchor_cluster="PIF_AVDD")


def _anchor_matching():
    """An explicit role anchor that DOES coincide with one checked cluster
    (PIF_AVDD mounts role DAC on sheet Channel_1 / cluster PIF_AVDD) — the
    self-anchor duplicate case: the cluster is both the tree anchor's source
    and a would-be placement node (like conn_pm5v_power under CONN_PM5V)."""
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


def test_build_tree_skips_cluster_matching_explicit_role_anchor():
    """§1 (2026-09-06, plan_2026_09_05_tree_root_rotation_drift): a cluster
    that IS the tree's EXPLICIT role anchor gets NO node — Extract-tree must
    not create the root/self duplicate (the "power" bug: conn_pm5v_power as
    both the anchor source and the first placement node). The anchor resolves
    independently of the node list, so skipping is safe; other clusters are
    still materialized."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor_matching(), cfg.entities, cfg)
    assert errors == []
    assert [n.ref for n in tree.nodes] == ["CH1_PIF_CLKVDD"]


def test_build_tree_keeps_cluster_for_auto_anchor():
    """§3: for an is_auto anchor the node MUST stay — an auto tree's single
    top-level placement node is its anchor SOURCE by construction
    (_auto_anchor_base needs exactly one); the skip never applies to auto."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", TreeAnchor(is_auto=True), cfg.entities, cfg)
    assert errors == []
    assert [n.ref for n in tree.nodes] == ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD"]


def test_build_tree_keeps_cluster_not_matching_anchor_control():
    """Control ("as fpga"): an anchor that does NOT coincide with any checked
    cluster leaves every node untouched — the skip fires only on a real
    role+sheet+cluster identity match."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg)
    assert errors == []
    assert [n.ref for n in tree.nodes] == ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD"]


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


def test_build_tree_rotation_aware_capture():
    """plan 2026-09-06 tree extract rotation (the double-rotation bug): when the
    anchor's live rotation at capture is non-zero AND the Entity's live rotation
    resolved, the node stores xy in the anchor's LOCAL frame (child_local_offset)
    and rotation = (live mount angle - baked mount angle) - anchor angle — so a
    redraw (node_position re-rotates a local xy by the anchor angle) reproduces
    the captured geometry instead of rotating it a second time. Round-trip
    properties: child_absolute_position(anchor, R, xy) == the live position, and
    R + rotation reproduces (live - baked) — materializing the cell then lands
    the mount component on baked + (live - baked) == live."""
    cfg = _cfg_with_entities()
    # Absolute-angle baking convention (template_extraction stores fp.angle_deg
    # as-is): give the CH1_PIF_AVDD cell's mount slot (role DAC, the zero-offset
    # component) a NON-zero baked angle.
    cfg.cells["dac_pif_avdd"].components[0].angle_deg = 30.0
    positions = {"CH1_PIF_AVDD": (10.0, 20.0, 75.0),
                 "CH1_PIF_CLKVDD": (16.0, 25.0, 15.0)}
    anchor_base = (5.0, 10.0)
    anchor_rot_deg = 270.0
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_base,
        anchor_rot_deg=anchor_rot_deg)
    assert errors == []
    node = tree.nodes[0]  # CH1_PIF_AVDD
    assert node.xy is not None
    # xy: offset in the anchor's LOCAL frame.
    expected_xy = child_local_offset(
        Vector2.from_xy_mm(*positions["CH1_PIF_AVDD"][:2]),
        Vector2.from_xy_mm(*anchor_base), anchor_rot_deg)
    assert node.xy[0] == pytest.approx(expected_xy.x / MM)
    assert node.xy[1] == pytest.approx(expected_xy.y / MM)
    # rotation: (live mount angle - baked mount angle) - anchor angle.
    assert node.rotation == pytest.approx(
        relative_rotation_deg(75.0 - 30.0, anchor_rot_deg))
    # Round-trip: re-projecting from the anchor reproduces the live position.
    replay = child_absolute_position(
        Vector2.from_xy_mm(*anchor_base), anchor_rot_deg,
        Vector2.from_xy_mm(*node.xy))
    assert replay.x / MM == pytest.approx(10.0)
    assert replay.y / MM == pytest.approx(20.0)
    # R + rotation reproduces (live - baked): the baked mount angle then lands
    # the mount component exactly on its live angle.
    assert (anchor_rot_deg + node.rotation) % 360.0 == pytest.approx(
        (75.0 - 30.0) % 360.0)


def test_build_tree_rotation_aware_capture_auto_cell():
    """A cluster with NO existing Entity yet (its auto cell is generated at save
    time from the CURRENT board) has no baked mount angle to read — the
    (live - baked) term cancels, so rotation = -anchor_rot_deg; xy is still
    captured in the anchor's LOCAL frame."""
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["U7", "R36"])]
    cfg = _cfg()
    anchor_base = (5.0, 10.0)
    positions = {"dac_buf_channel_0": (10.0, 20.0, 90.0)}
    tree, errors = build_tree_from_clusters(
        clusters, "dac_tree", _anchor(), cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_base,
        anchor_rot_deg=270.0)
    assert errors == []
    node = tree.nodes[0]
    assert node.ref == "dac_buf_channel_0"
    assert node.xy is not None
    assert node.rotation == pytest.approx(relative_rotation_deg(0.0, 270.0))
    expected_xy = child_local_offset(
        Vector2.from_xy_mm(10.0, 20.0), Vector2.from_xy_mm(*anchor_base), 270.0)
    assert node.xy[0] == pytest.approx(expected_xy.x / MM)
    assert node.xy[1] == pytest.approx(expected_xy.y / MM)


def test_build_tree_rotation_capture_requires_anchor_rotation():
    """Regression: a caller that still passes only the anchor base position (no
    anchor_rot_deg) keeps the historical raw-world-delta xy and rotation 0.0,
    even when entity_positions already carry an angle — nothing changes for
    existing callers/tests."""
    cfg = _cfg_with_entities()
    positions = {"CH1_PIF_AVDD": (10.0, 20.0, 75.0)}
    anchor_base = (5.0, 10.0)
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_base)
    assert errors == []
    node = tree.nodes[0]
    assert node.xy == (5.0, 10.0)  # 10-5, 20-10 — raw world delta
    assert node.rotation == 0.0


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


# ── one cluster -> flat Entity (2026-09-03, plan extract_cluster_entity) ──
# create_cell_and_entity_for_cluster is the shared "one cluster -> Cell (if
# new) + Entity" step behind BOTH "Extract tree..." and "Extract cluster...".

def test_create_cell_and_entity_for_cluster_new_cluster_generates_cell_and_entity(
        monkeypatch):
    """A cluster with no Entity -> the Entity dict (auto name, cluster+sheet)
    is returned AND its missing cell is generated from the cluster's selection
    and staged into cells_data."""
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    cells_data: dict = {}
    fake_cell = {"dac_buf": {"components": [{"role": "U7"}]}}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: fake_cell)
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, [], [])
    assert ent == {"name": "dac_buf_channel_0", "cell": "dac_buf",
                   "cluster": "DAC_BUF", "sheet": "Channel_0"}
    assert cells_data == {"dac_buf": {"components": [{"role": "U7"}]}}


def test_create_cell_and_entity_for_cluster_existing_entity_reused(
        monkeypatch):
    """An Entity for (cluster, sheet) already exists -> None (reuse, never a
    duplicate), cells_data untouched and no extraction happens."""
    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")],
               cells=[Cell(name="dac_pif_avdd", components=[_slot("DAC")])])
    c = ReReadCluster(cluster="PIF_AVDD", sheet="Channel_1",
                      entity_name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                      profile_key=None, refs=["R1"])
    cells_data: dict = {}
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda *a, **k: called.append(True) or {})
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, [], [])
    assert ent is None
    assert cells_data == {}
    assert called == []


def test_create_cell_and_entity_for_cluster_entity_name_override(monkeypatch):
    """The "Extract cluster..." dialog lets the user edit the auto Entity name:
    the override is used verbatim, the cell stays the cluster's slug."""
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    cells_data: dict = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: {name: {"components": []}})
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, [], [], entity_name="my_dac_buf")
    assert ent["name"] == "my_dac_buf"
    assert ent["cell"] == "dac_buf"
    assert ent["cluster"] == "DAC_BUF" and ent["sheet"] == "Channel_0"
    assert cells_data == {"dac_buf": {"components": []}}


def test_create_cell_and_entity_for_cluster_existing_cell_not_regenerated(
        monkeypatch):
    """The slug cell already exists in cfg.cells -> NOT regenerated, the Entity
    still returns pointing at it."""
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg(cells=[Cell(name="dac_buf", components=[_slot("DAC")])])
    cells_data: dict = {}
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda *a, **k: called.append(True) or {})
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, [], [])
    assert ent is not None
    assert ent["name"] == "dac_buf_channel_0"
    assert ent["cell"] == "dac_buf"
    assert cells_data == {}
    assert called == []


def test_create_cell_and_entity_for_cluster_failed_extraction_logs_warning(
        monkeypatch, caplog):
    """A failed cell generation must NOT crash the caller (one bad cluster must
    not drop the extract): it logs a warning and returns an Entity pointing at
    a cell name that simply doesn't exist yet (dock_hub.py:966-969 contract)."""
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    cells_data: dict = {}

    def _boom(*a, **k):
        raise RuntimeError("extract failed")

    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection", _boom)
    with caplog.at_level("WARNING"):
        ent = create_cell_and_entity_for_cluster(
            object(), c, cfg, cells_data, [], [])
    assert ent == {"name": "dac_buf_channel_0", "cell": "dac_buf",
                   "cluster": "DAC_BUF", "sheet": "Channel_0"}
    assert cells_data == {}
    assert any("not generated" in r.message for r in caplog.records)


# ── "Extract new cell from selection" (2026-09-04, plan
# instantiate_new_cell_from_selection) ────────────────────────────────────
# The dialog-tab-2 helper: STRICT (only a fully-selected cluster, `c` from
# fully_selected_clusters), built with the SAME extract_template_from_selection
# call as create_cell_and_entity_for_cluster, only the origin differs by the
# "geometry mode": zero-slot role (absolute=False) vs the selection-center
# Vector2 (absolute=True — the SAME point the node's "Take from selection"
# positioning uses, so the total position is exact, never doubled).

def _rc_cluster(cluster="PIF_AVDD", sheet="Channel_1", refs=("R1",)):
    return ReReadCluster(cluster=cluster, sheet=sheet, entity_name=None,
                          cell=cluster.lower(), profile_key=None, refs=list(refs))


def _sel_pos(ref, role, x_mm, y_mm):
    """A Selected-like footprint the helper reads (.ref/.role for the origin
    role, .fp.position for the selection center)."""
    from types import SimpleNamespace
    return SimpleNamespace(
        ref=ref, role=role,
        fp=SimpleNamespace(position=Vector2.from_xy_mm(x_mm, y_mm)))


def test_extract_new_cell_zero_slot_uses_cluster_origin_role(monkeypatch):
    """absolute=False -> the helper passes the cluster's unique selected role as
    origin_component_role (narrowed by the cluster+sheet) — the SAME zero-slot
    portable convention ordinary extraction uses — plus the cluster's raw items.
    No 'origin' kwarg (the extractor derives it from the role component)."""
    c = _rc_cluster(refs=["R1"])
    selected = [_sel_pos("R1", "R1", 5.0, 5.0)]
    raw = [_fp("R1")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    out = extract_new_cell_for_instantiation(
        object(), c, "pif_avdd", selected, raw, absolute=False)
    assert out == {"pif_avdd": {"components": []}}
    assert calls["items"] == raw
    assert calls["kw"] == {
        "origin_component_role": "R1",
        "origin_component_cluster": "PIF_AVDD",
        "origin_component_sheet": "Channel_1"}


def test_extract_new_cell_absolute_origin_is_selection_center(monkeypatch):
    """absolute=True -> origin = Vector2.from_xy_mm(selected_center_mm): the
    geometric center of the SELECTED footprints — the SAME point the node's
    'Take from selection' positioning uses. This is the regression that guards
    the design §1.1.2 double-offset: someone "fixing" the call to pass
    origin=Vector2(0,0) (or the bbox) instead of the center would shift the
    reproduced position by the center again."""
    c = _rc_cluster(sheet=None, refs=["R1", "U1"])
    selected = [_sel_pos("R1", "R1", 1.0, 2.0), _sel_pos("U1", "U1", 5.0, 4.0)]
    raw = [_fp("R1"), _fp("U1")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    out = extract_new_cell_for_instantiation(
        object(), c, "new_cell", selected, raw, absolute=True)
    assert out == {"new_cell": {"components": []}}
    origin = calls["kw"]["origin"]
    assert isinstance(origin, Vector2)
    assert (origin.x / MM, origin.y / MM) == (3.0, 3.0)  # center of (1,2),(5,4)
    assert "origin_component_role" not in calls["kw"]
    # The extractor computes cell offsets as (position - origin), so with this
    # origin the reproduced position = node_center + (pos - center) = pos.
    assert calls["items"] == raw


def test_extract_new_cell_absolute_without_positions_returns_none(
        monkeypatch, caplog):
    """absolute=True with no selectable footprint positions -> None + a warning
    (never a crash, never a cell silently built around an unavailable center)."""
    from types import SimpleNamespace
    c = _rc_cluster(sheet=None, refs=["R1"])
    selected = [SimpleNamespace(ref="R1", role="R1", fp=None)]
    raw = [_fp("R1")]
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda *a, **k: called.append(True) or {})
    with caplog.at_level("WARNING"):
        out = extract_new_cell_for_instantiation(
            object(), c, "new_cell", selected, raw, absolute=True)
    assert out is None
    assert called == []
    assert any("no footprint positions" in r.message for r in caplog.records)


def test_extract_new_cell_failed_extraction_logs_warning(monkeypatch, caplog):
    """A failed extraction must NOT crash the caller (the same 'one bad cluster
    must not crash the caller' contract as create_cell_and_entity_for_cluster):
    None + a logged warning."""
    c = _rc_cluster(refs=["R1"])
    selected = [_sel_pos("R1", "R1", 1.0, 1.0)]
    raw = [_fp("R1")]

    def _boom(*a, **k):
        raise RuntimeError("extract failed")

    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection", _boom)
    with caplog.at_level("WARNING"):
        out = extract_new_cell_for_instantiation(
            object(), c, "pif_avdd", selected, raw, absolute=False)
    assert out is None
    assert any("not generated" in r.message for r in caplog.records)


# ── manual origin override (2026-09-04, plan extract_origin_pad_restore) ──
# origin_role/origin_pad must WIN over cluster_origin_role's automatic choice —
# the regression test uses an override role that is EXPLICITLY DIFFERENT from
# the automatic one, so a silent fallback to automatic would not pass.

def test_extract_new_cell_zero_slot_manual_origin_override_wins(monkeypatch):
    """absolute=False + explicit origin_role/origin_pad -> the extractor gets
    EXACTLY the passed role/pad (NOT the unique-selection role cluster_origin_role
    would derive — the override deliberately differs from it)."""
    c = _rc_cluster(refs=["R1", "U1"])
    # Auto role (unique in selection) would be "R1"; the manual override is a
    # DIFFERENT role+pad — a regression that silently kept the auto role (or
    # attached the pad to it) would produce the wrong origin.
    selected = [_sel_pos("R1", "R1", 1.0, 1.0), _sel_pos("U1", "U1", 2.0, 2.0)]
    raw = [_fp("R1"), _fp("U1")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    out = extract_new_cell_for_instantiation(
        object(), c, "pif_avdd", selected, raw, absolute=False,
        origin_role="U1", origin_pad="A1")
    assert out == {"pif_avdd": {"components": []}}
    assert calls["kw"] == {
        "origin_component_role": "U1",
        "origin_component_pad": "A1",
        "origin_component_cluster": "PIF_AVDD",
        "origin_component_sheet": "Channel_1"}
    assert calls["items"] == raw


def test_extract_new_cell_zero_slot_default_origin_matches_previous(monkeypatch):
    """origin_role=None (default) -> behavior identical to BEFORE the plan: the
    automatic unique-role detection is used, no origin_component_pad is added."""
    c = _rc_cluster(refs=["R1"])
    selected = [_sel_pos("R1", "R1", 5.0, 5.0)]
    raw = [_fp("R1")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    out = extract_new_cell_for_instantiation(
        object(), c, "pif_avdd", selected, raw, absolute=False)
    assert out == {"pif_avdd": {"components": []}}
    assert calls["kw"] == {
        "origin_component_role": "R1",
        "origin_component_cluster": "PIF_AVDD",
        "origin_component_sheet": "Channel_1"}
    assert "origin_component_pad" not in calls["kw"]


def test_extract_new_cell_absolute_ignores_manual_origin_override(monkeypatch):
    """absolute=True -> the manual origin override is IGNORED (the Absolute mode
    has its own origin via selected_center_mm): passing origin_role/origin_pad
    alongside absolute=True must not smuggle a role-based origin in."""
    c = _rc_cluster(sheet=None, refs=["R1"])
    selected = [_sel_pos("R1", "R1", 1.0, 2.0)]
    raw = [_fp("R1")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    out = extract_new_cell_for_instantiation(
        object(), c, "new_cell", selected, raw, absolute=True,
        origin_role="R1", origin_pad="A1")
    assert out == {"new_cell": {"components": []}}
    origin = calls["kw"]["origin"]
    assert isinstance(origin, Vector2)
    assert (origin.x / MM, origin.y / MM) == (1.0, 2.0)
    assert "origin_component_role" not in calls["kw"]
    assert "origin_component_pad" not in calls["kw"]


def test_extract_new_cell_origin_pad_without_role_raises(monkeypatch):
    """origin_pad given but origin_role empty (programming error, not a user
    error) -> ValueError BEFORE any extraction (plan §1.2: pad is ONLY a
    refinement of an explicit role; the GUI never lets this state form)."""
    c = _rc_cluster(refs=["R1"])
    selected = [_sel_pos("R1", "R1", 1.0, 1.0)]
    raw = [_fp("R1")]
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda *a, **k: called.append(True) or {})
    with pytest.raises(ValueError):
        extract_new_cell_for_instantiation(
            object(), c, "pif_avdd", selected, raw, absolute=False,
            origin_pad="A1")
    assert called == []


def test_create_cell_and_entity_manual_origin_override_wins(monkeypatch):
    """create_cell_and_entity_for_cluster with explicit origin_role/origin_pad:
    the staged cell is generated with EXACTLY the passed role/pad, NOT the
    unique-selection role (the override deliberately differs from the auto one).
    """
    from types import SimpleNamespace
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    cells_data: dict = {}
    selected = [SimpleNamespace(ref="U7", role="U7"),
                SimpleNamespace(ref="U8", role="U8")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, selected, [_fp("U7"), _fp("U8")],
        origin_role="U8", origin_pad="B2")
    assert ent == {"name": "dac_buf_channel_0", "cell": "dac_buf",
                   "cluster": "DAC_BUF", "sheet": "Channel_0"}
    assert calls["kw"] == {
        "origin_component_role": "U8",
        "origin_component_pad": "B2",
        "origin_component_cluster": "DAC_BUF",
        "origin_component_sheet": "Channel_0"}


def test_create_cell_and_entity_default_origin_matches_previous(monkeypatch):
    """origin_role=None (default) -> the automatic unique-role detection is
    used, identical to BEFORE the plan (regression against the existing zero-slot
    mode)."""
    from types import SimpleNamespace
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    cells_data: dict = {}
    selected = [SimpleNamespace(ref="U7", role="U7"),
                SimpleNamespace(ref="U8", role="U8")]
    calls = {}
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda adapter, name, items=None, **kw: calls.update(items=items, kw=kw)
        or {name: {"components": []}})
    ent = create_cell_and_entity_for_cluster(
        object(), c, cfg, cells_data, selected, [_fp("U7"), _fp("U8")])
    assert ent == {"name": "dac_buf_channel_0", "cell": "dac_buf",
                   "cluster": "DAC_BUF", "sheet": "Channel_0"}
    assert calls["kw"] == {
        "origin_component_role": "U7",
        "origin_component_cluster": "DAC_BUF",
        "origin_component_sheet": "Channel_0"}
    assert "origin_component_pad" not in calls["kw"]


def test_create_cell_and_entity_origin_pad_without_role_raises(monkeypatch):
    """origin_pad given but origin_role empty -> ValueError BEFORE any extraction
    (plan §1.1: pad is ONLY a refinement of an explicit role)."""
    from types import SimpleNamespace
    c = ReReadCluster(cluster="DAC_BUF", sheet="Channel_0", entity_name=None,
                      cell="dac_buf", profile_key=None, refs=["U7"])
    cfg = _cfg()
    selected = [SimpleNamespace(ref="U7", role="U7")]
    called = []
    monkeypatch.setattr(
        "gui.docks.tree_from_selection.extract_template_from_selection",
        lambda *a, **k: called.append(True) or {})
    with pytest.raises(ValueError):
        create_cell_and_entity_for_cluster(
            object(), c, cfg, {}, selected, [_fp("U7")], origin_pad="A1")
    assert called == []


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


# ── Phase C: connectivity-based detection (2026-09-01) ────────────────────

def _pad_at(x_mm, y_mm, net="N"):
    from types import SimpleNamespace
    from kicadstamp.domain.geometry import Vector2
    p = SimpleNamespace()
    p.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    p.net_name = net
    return p


def _connectivity_adapter(pads_by_ref):
    """A mock adapter: get_footprint_pads returns the pads (with .position) for
    a ref, get_bounding_boxes returns a small centered box per pad — enough for
    _connected_cluster_labels' union-find closure."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from kicadstamp.domain.geometry import Vector2

    adapter = MagicMock()
    adapter.get_footprint_pads.side_effect = lambda fp: pads_by_ref.get(fp.ref, [])

    def _boxes(items):
        out = []
        for it in items:
            b = SimpleNamespace()
            b.pos = Vector2.from_xy(int(it.position.x - 0.3 * MM),
                                    int(it.position.y - 0.3 * MM))
            b.size = Vector2.from_xy(int(0.6 * MM), int(0.6 * MM))
            b.inflate = lambda _d: None
            out.append(b)
        return out

    adapter.get_bounding_boxes.side_effect = _boxes
    return adapter


def test_detect_connectivity_offers_net_reaching_two_clusters():
    """A net whose SELECTED track runs between cluster A's pad and cluster B's
    pad is offered when the adapter (connectivity) is passed."""
    clusters = [
        ReReadCluster(cluster="A", sheet="Ch", entity_name=None, cell="a",
                      profile_key=None, refs=["R1"]),
        ReReadCluster(cluster="B", sheet="Ch", entity_name=None, cell="b",
                      profile_key=None, refs=["R2"]),
    ]
    snapshot = [
        _sel("R1", "A", "Ch", {"1": "SHARED"}),
        _sel("R2", "B", "Ch", {"1": "SHARED"}),
    ]
    track = Track(uuid="t", start=Vector2.from_xy(int(10 * MM), int(10 * MM)),
                  end=Vector2.from_xy(int(20 * MM), int(20 * MM)),
                  net_name="SHARED", width_mm=0.25, layer=None)
    raw = [_fp("R1"), _fp("R2"), track]
    adapter = _connectivity_adapter({
        "R1": [_pad_at(10, 10)],
        "R2": [_pad_at(20, 20)],
    })
    assert detect_inter_cluster_nets(raw, clusters, snapshot, adapter=adapter) == [
        InterClusterNet(net="SHARED", track_count=1, via_count=0)]


def test_detect_connectivity_drops_stitching_via_not_touching_pads():
    """A net with only a floating stitching via (no cluster pad) is offered by
    the name-based detector but dropped by the connectivity filter."""
    clusters = [
        ReReadCluster(cluster="A", sheet="Ch", entity_name=None, cell="a",
                      profile_key=None, refs=["R1"]),
        ReReadCluster(cluster="B", sheet="Ch", entity_name=None, cell="b",
                      profile_key=None, refs=["R2"]),
    ]
    snapshot = [
        _sel("R1", "A", "Ch", {"1": "SHARED"}),
        _sel("R2", "B", "Ch", {"1": "SHARED"}),
    ]
    via = Via(uuid="v", position=Vector2.from_xy(int(100 * MM), int(100 * MM)),
              net_name="SHARED", drill_mm=0.3, diameter_mm=0.6)
    raw = [_fp("R1"), _fp("R2"), via]
    adapter = _connectivity_adapter({
        "R1": [_pad_at(10, 10)],
        "R2": [_pad_at(20, 20)],
    })
    assert [n.net for n in detect_inter_cluster_nets(raw, clusters, snapshot)] == ["SHARED"]
    assert detect_inter_cluster_nets(raw, clusters, snapshot, adapter=adapter) == []


def test_detect_connectivity_drops_net_touching_single_cluster():
    """A net whose selected copper reaches ONLY cluster A's pad is dropped."""
    clusters = [
        ReReadCluster(cluster="A", sheet="Ch", entity_name=None, cell="a",
                      profile_key=None, refs=["R1"]),
        ReReadCluster(cluster="B", sheet="Ch", entity_name=None, cell="b",
                      profile_key=None, refs=["R2"]),
    ]
    snapshot = [
        _sel("R1", "A", "Ch", {"1": "SHARED", "2": "AVDD"}),
        _sel("R2", "B", "Ch", {"1": "SHARED"}),
    ]
    track = Track(uuid="t", start=Vector2.from_xy(int(10 * MM), int(10 * MM)),
                  end=Vector2.from_xy(int(12 * MM), int(12 * MM)),
                  net_name="SHARED", width_mm=0.25, layer=None)  # only near A's pad
    raw = [_fp("R1"), _fp("R2"), track]
    adapter = _connectivity_adapter({
        "R1": [_pad_at(10, 10)],
        "R2": [_pad_at(20, 20)],
    })
    assert detect_inter_cluster_nets(raw, clusters, snapshot, adapter=adapter) == []


# ── Phase D: net_trace tree nodes (2026-09-01) ────────────────────────────

def test_build_tree_emits_net_trace_nodes():
    """Phase D: the checked inter-cluster nets become top-level
    kind="net_trace" nodes (ref = the net name, no xy)."""
    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        net_nodes=["SHARED", "AVDD"])
    assert errors == []
    assert [n.ref for n in tree.nodes] == \
        ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD", "SHARED", "AVDD"]
    assert [n.kind for n in tree.nodes] == \
        ["placement", "placement", "net_trace", "net_trace"]
    assert all(n.xy is None for n in tree.nodes[2:])


def test_build_tree_net_trace_round_trip_and_link_trees():
    """Phase D: a tree with a net_trace node round-trips and link_trees resolves
    it to the net_traces: record by key net_trace:<net>."""
    from kicadstamp.config import NetTrace

    cfg = _cfg_with_entities()
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        net_nodes=["SHARED"])
    assert errors == []

    reloaded = load_tree(tree_to_dict(tree))
    assert [n.ref for n in reloaded.nodes] == \
        ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD", "SHARED"]
    assert [n.kind for n in reloaded.nodes] == \
        ["placement", "placement", "net_trace"]

    cfg.net_traces = [NetTrace(net="SHARED", anchor_role="DAC")]
    linked = link_trees(cfg, [reloaded])
    nt_node = next(n for n in linked[0].nodes if n.node.kind == "net_trace")
    assert nt_node.record.name == "SHARED"
    assert nt_node.record.kind == "net_trace"


def test_link_trees_net_trace_requires_explicit_kind():
    """Phase D: a net_trace ref is NOT auto-searched — a kind-less node whose
    name matches a net stays unresolved (net_trace requires an explicit kind),
    so a net name can never collide with another section by accident."""
    from kicadstamp.config import NetTrace
    from kicadstamp.trees import TreeNode

    cfg = _cfg(entities=[Entity(name="CH1_PIF_AVDD", cell="dac_pif_avdd",
                                cluster="PIF_AVDD", sheet="Channel_1")],
               cells=[Cell(name="dac_pif_avdd", components=[_slot("DAC")])])
    cfg.net_traces = [NetTrace(net="SHARED", anchor_role="DAC")]
    tree = Tree(name="t", anchor=_anchor(),
                nodes=[TreeNode(ref="SHARED", kind=None, xy=None, polar=None,
                                rotation=0.0, name=None, group=None)])
    with pytest.raises(Exception, match="not found"):
        link_trees(cfg, [tree])


# ── Phase E: re-extract / delete-tree cascade (2026-09-01) ────────────────

def test_build_tree_allow_existing_rebuilds_on_duplicate_name():
    """Phase E: allow_existing=True lets the build proceed with a name that
    already exists (re-extract) instead of the "already exists" hard error."""
    cfg = _cfg_with_entities()
    cfg.trees = [Tree(name="power_tree", anchor=_anchor(), nodes=[])]
    # New tree would be blocked...
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg)
    assert tree is None
    assert any("already exists" in e for e in errors)
    # ...but with allow_existing (re-extract) it rebuilds.
    tree, errors = build_tree_from_clusters(
        _clusters(), "power_tree", _anchor(), cfg.entities, cfg,
        allow_existing=True, net_nodes=["SHARED"])
    assert errors == []
    assert [n.ref for n in tree.nodes] == \
        ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD", "SHARED"]


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
    assert linked[0].anchor.anchor.role == "REMOTE"
