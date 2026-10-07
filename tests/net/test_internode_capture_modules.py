# tests/net/test_internode_capture_modules.py
"""Т1/Т2 guards of plan_2026_10_05_tree_reread_modules: the inter-node copper
re-read sees the content of `kind "module"` nodes (Т1), and a NEW record is
anchored on the side it will REDRAW from (Т2).

The board is a small FPGA tree: a parent tree `fpga` with one FPGA node plus
`kind "module"` nodes embedding per-channel DAC trees (one node each, Cluster
`DAC_BUF`, sheet `Channel_N`). Each bridge is FPGA pad -> track -> via (mid) ->
track -> DAC pad, so BOTH ends own an item — the anchor rule is only meaningful
then (a single track touching both pads would record ONE reference and never
exercise the other side).

Test names describe the PROPERTY (rule §37); each docstring names the Т3 cell of
the plan. Cell 8 (no-module regression) lives in tests/net/test_internode_capture.py
and is verified by running that file unchanged.
"""
import dataclasses
from types import SimpleNamespace

from kicadstamp.config import Config, Entity
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import plan_internode_reread
from kicadstamp.internode_nodes import tree_node_keys
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


# ── board + config doubles ─────────────────────────────────────────────────

def _fp(ref, role, cluster, x_mm, y_mm, path):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6), y=int(y_mm * 1e6)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp.sheet_path_uuids = tuple(path) + (f"own-{ref}",)
    return fp


def _pad(number, net, x_mm, y_mm):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=SimpleNamespace(x=int(x_mm * 1e6),
                                                    y=int(y_mm * 1e6)))


def _track(x1, y1, x2, y2, net):
    return Track(uuid=f"t{x1}{y1}{x2}{y2}",
                 start=SimpleNamespace(x=int(x1 * 1e6), y=int(y1 * 1e6)),
                 end=SimpleNamespace(x=int(x2 * 1e6), y=int(y2 * 1e6)),
                 net_name=net, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _via(x, y, net):
    return Via(uuid=f"v{x}{y}",
               position=SimpleNamespace(x=int(x * 1e6), y=int(y * 1e6)),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


def _node(ref, kind):
    return TreeNode(ref=ref, kind=kind, xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])


class _Board:
    def __init__(self, footprints, pads_by_ref, selected=()):
        self._fps = list(footprints)
        self._pads = pads_by_ref
        self._selected = list(selected)

    def get_footprints(self):
        return list(self._fps)

    def get_selected_items(self):
        return list(self._selected)

    def get_field_value(self, fp, name):
        return {"Role": getattr(fp, "_role", None),
                "Cluster": getattr(fp, "_cluster", None)}.get(name)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, num):
        for pad in self._pads.get(fp.ref, []):
            if str(pad.number) == str(num):
                return pad
        return None

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            b = SimpleNamespace()
            b.pos = SimpleNamespace(x=int(it.position.x - 0.3e6),
                                    y=int(it.position.y - 0.3e6))
            b.size = SimpleNamespace(x=600_000, y=600_000)
            b.inflate = lambda _d: None
            out.append(b)
        return out


# ── the FPGA fixture ───────────────────────────────────────────────────────

_SHEET_NAMES = {"fpga": "FPGA",
                "ch0": "Channel_0", "dac0": "DAC",
                "ch1": "Channel_1", "dac1": "DAC",
                "ch2": "Channel_2", "dac2": "DAC"}

_Y = (10.0, 30.0, 50.0)          # one Y band per channel, far enough apart
_X_DAC = 110.0                   # the DAC chips share one X, differ in Y


def _fpga_channel_fps(channels):
    """FPGA (Role FPGA, once on the board) plus one AD_DAC/DAC_BUF chip per
    channel, each on its own `Channel_N` sheet (path `['Channel_N', 'DAC']`)."""
    fps = [_fp("FPGA1", "FPGA", "FPGA", 10.0, _Y[0], ("fpga",))]
    pads = {"FPGA1": [_pad(i + 1, f"DB{i}", 10.0, _Y[i]) for i in range(channels)]}
    for i in range(channels):
        dac = _fp(f"IC{i + 2}", "AD_DAC", "DAC_BUF", _X_DAC, _Y[i],
                  (f"ch{i}", f"dac{i}"))
        fps.append(dac)
        pads[dac.ref] = [_pad("1", f"DB{i}", _X_DAC, _Y[i]),
                         _pad("2", "XCH", _X_DAC + 5.0, _Y[i] + 5.0),
                         _pad("3", "DBAD", _X_DAC + 30.0, _Y[i] + 5.0)]
    return fps, pads


def _bridge_fpga_to_dac(i):
    """FPGA pad -> track -> via (mid-chain) -> track -> DAC pad: two items, one
    per end, so the anchor rule has something to choose between."""
    y = _Y[i]
    return [_track(10.0, y, 60.0, y, f"DB{i}"),
            _via(60.0, y, f"DB{i}"),
            _track(60.0, y, _X_DAC, y, f"DB{i}")]


def _piece_between_channels(i, j):
    """A piece between the two channels' own pads (net XCH): two DIFFERENT nodes
    of the SAME Cluster tag — INTERNODE, never CLUSTER."""
    return _track(_X_DAC + 5.0, _Y[i] + 5.0, _X_DAC + 5.0, _Y[j] + 5.0, "XCH")


def _cfg_fpga(channels=1):
    entities = [Entity(name="e_fpga", cell="c", cluster="FPGA", sheet="FPGA")]
    trees = [_Tree(f"ch{i}_dac_buf", [_node(f"e_dac_{i}", "placement")])
             for i in range(channels)]
    for i in range(channels):
        entities.append(Entity(name=f"e_dac_{i}", cell="c", cluster="DAC_BUF",
                               sheet=f"Channel_{i}"))
    nodes = [_node("e_fpga", "placement")]
    nodes += [_node(f"ch{i}_dac_buf", "module") for i in range(channels)]
    trees.append(_Tree("fpga", nodes))
    return Config(entities=entities, trees=trees)


def _Tree(name, nodes):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _tree_fpga(cfg):
    return next(t for t in cfg.trees if t.name == "fpga")


# ── Т3-1 / Т3-3: module content and the redrawable anchor ─────────────────

def test_a_module_channel_component_is_a_tree_node_for_the_reread():
    """Т3-1 — the DAC chip lives in a tree EMBEDDED through a `kind "module"`
    node. Before Т1 the FPGA<->DAC piece is FOREIGN and nothing is added; after
    Т1 it is INTERNODE and a record is written. Mutation 'module walk disabled'
    turns this red."""
    fps, pads = _fpga_channel_fps(1)
    board = _Board(fps, pads)
    cfg = _cfg_fpga(1)
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=_bridge_fpga_to_dac(0), area_footprints=fps,
        sheet_names=_SHEET_NAMES)
    assert len(plan.added) == 1
    record = plan.added[0].record
    assert record.pads == ["AD_DAC.1", "FPGA.1"]
    assert plan.discarded.get("foreign") is None


def test_the_new_record_is_anchored_on_the_side_it_redraws_from():
    """Т3-3 — role AD_DAC sits on three sheets, role FPGA once, and the FPGA pad
    sorts FIRST (`pads[0]`). The FPGA anchor would leave the DAC-side item's role
    (AD_DAC) ambiguous under (FPGA, FPGA) and apply would refuse the record, so
    the anchor must be the DAC side (Channel_0). `plan_net_traces` then resolves
    EVERY element of the stored record on the same double: the read->redraw loop
    closes. Mutation 'anchor = always pads[0]' turns this red."""
    fps, pads = _fpga_channel_fps(3)
    board = _Board(fps, pads)
    cfg = _cfg_fpga(3)
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=_bridge_fpga_to_dac(0), area_footprints=fps,
        sheet_names=_SHEET_NAMES)
    assert len(plan.added) == 1
    record = plan.added[0].record
    assert record.anchor_role == "AD_DAC" and record.anchor_pad == "1"
    assert record.anchor_sheet == "Channel_0"
    assert record.anchor_cluster == "DAC_BUF"

    # A stored record carries a uuid (format 3: identity = uuid); the writer
    # stamps it, so a re-read-through-plan_net_traces test must too.
    record = dataclasses.replace(record, uuid="uuid-db0")
    vias, tracks = plan_net_traces(board, [record], _SHEET_NAMES)
    assert len(tracks) == 2 and len(vias) == 1
    assert {t.net_name for t in tracks} == {"DB0"}
    assert {v.net_name for v in vias} == {"DB0"}


# ── Т3-2: two channels of one Cluster tag ─────────────────────────────────

def test_two_channels_of_one_cluster_get_two_records_and_the_cross_piece_is_internode():
    """Т3-2 — two modules on Channel_0/Channel_1 share the SAME Cluster tag
    (DAC_BUF). Each FPGA<->DAC bridge is its own record, anchored on ITS sheet,
    and a piece between the two channels is INTERNODE (two nodes), never CLUSTER
    (one label). Mutation 'node identity = label' turns the cross piece into
    CLUSTER and this red."""
    fps, pads = _fpga_channel_fps(3)
    board = _Board(fps, pads)
    cfg = _cfg_fpga(3)
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=(_bridge_fpga_to_dac(0) + _bridge_fpga_to_dac(1)
                    + [_piece_between_channels(0, 1)]),
        area_footprints=fps, sheet_names=_SHEET_NAMES)
    assert len(plan.added) == 3
    assert plan.discarded.get("cluster") is None
    signatures = [c.signature for c in plan.added]
    assert frozenset({"AD_DAC.1", "FPGA.1"}) in signatures   # a bridge
    assert frozenset({"AD_DAC.2"}) in signatures             # the cross piece
    sheets = sorted(c.record.anchor_sheet for c in plan.added)
    assert sheets == ["Channel_0", "Channel_0", "Channel_1"]


# ── Т3-4: the anchor does not depend on the board selection ───────────────

def test_the_anchor_does_not_depend_on_the_board_selection():
    """Т3-4 — the check disables the resolver's selection step, so a bridge whose
    DAC side is SELECTED anchors exactly as it does with nothing selected.
    Mutation 'anchor check WITH the selection step' anchors on FPGA here and
    turns this red."""
    fps, pads = _fpga_channel_fps(3)
    ic2 = next(f for f in fps if f.ref == "IC2")
    cfg = _cfg_fpga(3)
    items = _bridge_fpga_to_dac(0)
    plain = plan_internode_reread(_Board(fps, pads), cfg, _tree_fpga(cfg),
                                  area_items=items, area_footprints=fps,
                                  sheet_names=_SHEET_NAMES)
    selected = plan_internode_reread(_Board(fps, pads, selected=[ic2]), cfg,
                                     _tree_fpga(cfg), area_items=items,
                                     area_footprints=fps,
                                     sheet_names=_SHEET_NAMES)
    assert plain.added[0].record.anchor_sheet == "Channel_0"
    assert selected.added[0].record.anchor_sheet == "Channel_0"
    assert (plain.added[0].record.anchor_role,
            plain.added[0].record.anchor_pad) == \
           (selected.added[0].record.anchor_role,
            selected.added[0].record.anchor_pad)


# ── Т3-5: no pad qualifies -> skip, the neighbour is still written ────────

def test_a_piece_no_pad_can_anchor_is_skipped_and_its_neighbour_is_written():
    """Т3-5 — both ends carry a role that is DUPLICATED with the same Cluster on
    no sheet (the resolver cannot narrow it to one footprint), so NO pad can
    anchor the piece. It is SKIPPED with a warning naming the pads and the
    ambiguous roles — never a fatal — and the neighbouring valid piece is still
    written. Mutation 'unsuitable piece is fatal' turns this red."""
    fps = [_fp("AMB_A1", "AMB_ROLE", "AMB", 10.0, 10.0, ("fpga",)),
           _fp("AMB_A2", "AMB_ROLE", "AMB", 10.0, 20.0, ("fpga",)),
           _fp("AMB_B1", "AMB2_ROLE", "AMB2", 30.0, 10.0, ("fpga",)),
           _fp("AMB_B2", "AMB2_ROLE", "AMB2", 30.0, 20.0, ("fpga",)),
           _fp("OK1", "OK_ROLE", "OK", 10.0, 30.0, ("fpga",)),
           _fp("OK2", "OK2_ROLE", "OK2", 30.0, 30.0, ("fpga",))]
    pads = {"AMB_A1": [_pad("1", "AMB", 10.0, 10.0)],
            "AMB_A2": [_pad("1", "AMB", 10.0, 20.0)],
            "AMB_B1": [_pad("1", "AMB", 30.0, 10.0)],
            "AMB_B2": [_pad("1", "AMB", 30.0, 20.0)],
            "OK1": [_pad("1", "OKN", 10.0, 30.0)],
            "OK2": [_pad("1", "OKN", 30.0, 30.0)]}
    cfg = Config(
        entities=[Entity(name="e_amb", cell="c", cluster="AMB"),
                  Entity(name="e_amb2", cell="c", cluster="AMB2"),
                  Entity(name="e_ok", cell="c", cluster="OK"),
                  Entity(name="e_ok2", cell="c", cluster="OK2")],
        trees=[_Tree("t", [_node("e_amb", "placement"),
                           _node("e_amb2", "placement"),
                           _node("e_ok", "placement"),
                           _node("e_ok2", "placement")])])
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, cfg.trees[0],
        area_items=[_track(10.0, 10.0, 30.0, 10.0, "AMB"),     # unanchorable
                    _track(10.0, 30.0, 30.0, 30.0, "OKN")],    # fine
        area_footprints=fps)
    assert [set(c.record.pads) for c in plan.added] == [{"OK_ROLE.1", "OK2_ROLE.1"}]
    assert len(plan.warnings) == 1
    warning = plan.warnings[0]
    assert "AMB_A1.1" in warning and "AMB_B1.1" in warning
    assert "AMB_ROLE" in warning and "AMB2_ROLE" in warning


# ── Т3-6: cycles and missing module trees terminate ───────────────────────

def test_module_cycles_and_missing_trees_terminate():
    """Т3-6 — a cycle (A -> B -> A) and a module naming a tree that does not
    exist must neither hang nor crash: the walk breaks the cycle on already
    walked tree names and skips the missing tree. pytest-timeout (addopts) turns
    a missing guard into a loud failure instead of a frozen run."""
    tree_a = _Tree("A", [_node("e_a", "placement"), _node("B", "module")])
    tree_b = _Tree("B", [_node("e_b", "placement"), _node("A", "module")])
    tree_c = _Tree("C", [_node("no_such_tree", "module")])
    cfg = Config(entities=[Entity(name="e_a", cell="c", cluster="CA"),
                           Entity(name="e_b", cell="c", cluster="CB")],
                 trees=[tree_a, tree_b, tree_c])
    keys = tree_node_keys(tree_a, cfg)
    assert ("CA", None) in keys and ("CB", None) in keys
    assert tree_node_keys(tree_c, cfg) == {}


# ── Т3-7: a cluster outside the tree (the FPGA spokes) stays FOREIGN ──────

def test_a_component_of_a_cluster_outside_the_tree_stays_foreign():
    """Т3-7 — the FPGA spokes: a pad of FPGA plus a pad of a cluster that is
    neither a node nor inside a module is FOREIGN, exactly as before."""
    fps, pads = _fpga_channel_fps(1)
    fps.append(_fp("PWR1", "FPGA_PWR_BANK", "FPGA_PWR_BANK", 30.0, 90.0, ("fpga",)))
    pads["FPGA1"].append(_pad("9", "VCC", 10.0, 90.0))
    pads["PWR1"] = [_pad("1", "VCC", 30.0, 90.0)]
    board = _Board(fps, pads)
    cfg = _cfg_fpga(1)
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=[_track(10.0, 90.0, 30.0, 90.0, "VCC")],
        area_footprints=fps, sheet_names=_SHEET_NAMES)
    assert plan.added == []
    assert plan.discarded.get("foreign") == 1


# ── Т4-1: copper INSIDE one module is that module's, not the parent's ─────
#
# The parent `fpga` owns three placement nodes (FPGA, C, D) and TWO module nodes,
# each embedding a tree of TWO nodes on its own sheet (m0 -> DA/DB on Ch0,
# m1 -> DA/DB on Ch1). The SAME Cluster tags repeat, so only `(cluster, sheet)`
# tells the nodes apart — and only the OWNER tells "inside one module" from
# "between two of them".

def _two_module_cfg():
    entities = [
        Entity(name="e_fpga", cell="c", cluster="FPGA", sheet="FPGA"),
        Entity(name="e_c", cell="c", cluster="C", sheet="FPGA"),
        Entity(name="e_d", cell="c", cluster="D", sheet="FPGA"),
        Entity(name="e_a0", cell="c", cluster="DA", sheet="Ch0"),
        Entity(name="e_b0", cell="c", cluster="DB", sheet="Ch0"),
        Entity(name="e_a1", cell="c", cluster="DA", sheet="Ch1"),
        Entity(name="e_b1", cell="c", cluster="DB", sheet="Ch1"),
    ]
    m0 = _Tree("m0", [_node("e_a0", "placement"), _node("e_b0", "placement")])
    m1 = _Tree("m1", [_node("e_a1", "placement"), _node("e_b1", "placement")])
    fpga = _Tree("fpga", [_node("e_fpga", "placement"),
                          _node("e_c", "placement"), _node("e_d", "placement"),
                          _node("m0", "module"), _node("m1", "module")])
    return Config(entities=entities, trees=[m0, m1, fpga])


_SHEETS_2M = {"fpga": "FPGA", "ch0": "Ch0", "ch1": "Ch1"}


def _two_module_board():
    """Unique Role per component (the anchor rule always has an answer) and a
    disjoint net per concern, so a verdict never depends on the anchor."""
    fps = [_fp("A0", "R_A0", "DA", 10.0, 10.0, ("ch0",)),
           _fp("B0", "R_B0", "DB", 30.0, 10.0, ("ch0",)),
           _fp("A1", "R_A1", "DA", 10.0, 20.0, ("ch1",)),
           _fp("B1", "R_B1", "DB", 30.0, 20.0, ("ch1",)),
           _fp("FPGA1", "R_FPGA", "FPGA", 10.0, 50.0, ("fpga",)),
           _fp("C0", "R_C", "C", 30.0, 50.0, ("fpga",)),
           _fp("D0", "R_D", "D", 50.0, 50.0, ("fpga",))]
    pads = {"A0": [_pad("1", "N_M0", 10.0, 10.0), _pad("2", "N_XMOD", 12.0, 10.0),
                   _pad("3", "N_FMOD", 60.0, 10.0)],
            "B0": [_pad("1", "N_M0", 30.0, 10.0)],
            "A1": [_pad("1", "N_M1", 10.0, 20.0), _pad("2", "N_XMOD", 12.0, 20.0)],
            "B1": [_pad("1", "N_M1", 30.0, 20.0)],
            "FPGA1": [_pad("1", "N_FMOD", 50.0, 10.0)],
            "C0": [_pad("1", "N_CD", 30.0, 50.0)],
            "D0": [_pad("1", "N_CD", 50.0, 50.0)]}
    return _Board(fps, pads), fps


def test_copper_inside_one_module_is_the_modules_not_the_parents():
    """Т4-1 — a piece whose every node belongs to ONE module (m0's own two nodes,
    and m1's own two nodes) is that MODULE's inter-node copper: the parent does
    NOT take it and counts it as the new verdict `module` (reported as
    "module 2"). Mutation 'module copper taken by the parent' turns this red."""
    from kicadstamp.internode_capture import reread_report_lines

    board, fps = _two_module_board()
    cfg = _two_module_cfg()
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=[_track(10.0, 10.0, 30.0, 10.0, "N_M0"),    # inside m0
                    _track(10.0, 20.0, 30.0, 20.0, "N_M1")],   # inside m1
        area_footprints=fps, sheet_names=_SHEETS_2M)
    assert plan.added == []
    assert plan.discarded.get("module") == 2
    text = "\n".join(reread_report_lines("fpga", plan))
    assert "not taken:" in text and "module 2" in text


def test_copper_between_two_modules_of_the_same_tags_is_internode():
    """Т4-1 — a piece between TWO DIFFERENT modules (m0's DA and m1's DA, the SAME
    Cluster tag on different sheets) is INTERNODE: two nodes, two owners, so the
    parent takes it. Mutation 'two different modules -> module' turns this red."""
    board, fps = _two_module_board()
    cfg = _two_module_cfg()
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=[_track(12.0, 10.0, 12.0, 20.0, "N_XMOD")],
        area_footprints=fps, sheet_names=_SHEETS_2M)
    assert [set(c.record.pads) for c in plan.added] == [{"R_A0.2", "R_A1.2"}]
    assert plan.discarded.get("module") is None
    assert plan.discarded.get("cluster") is None


def test_copper_between_the_tree_and_a_module_is_internode():
    """Т4-1 — an owner MIX (the tree's OWN node and a module node) is INTERNODE,
    and so are two OWN nodes of the tree: only "every node of the SAME module"
    is the module's. Mutation 'any module owner -> module' turns this red."""
    board, fps = _two_module_board()
    cfg = _two_module_cfg()
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg),
        area_items=[_track(50.0, 10.0, 60.0, 10.0, "N_FMOD"),   # own + module
                    _track(30.0, 50.0, 50.0, 50.0, "N_CD")],    # two own nodes
        area_footprints=fps, sheet_names=_SHEETS_2M)
    assert plan.discarded.get("module") is None
    sigs = {c.signature for c in plan.added}
    assert frozenset({"R_A0.3", "R_FPGA.1"}) in sigs
    assert frozenset({"R_C.1", "R_D.1"}) in sigs


def test_node_owners_distinguish_the_tree_from_each_module():
    """Т4-1 — `tree_node_keys` records, per key, the OWNER: None for a node of the
    tree itself (top-level OR a module node's own child), and the TOPMOST module
    node for a node reached through one. Two modules are two DIFFERENT owners.
    It still equals the plain `{(cluster, sheet): label}` mapping it replaces."""
    cfg = _two_module_cfg()
    nodes = tree_node_keys(_tree_fpga(cfg), cfg)
    assert nodes.owners[("FPGA", "FPGA")] is None
    assert nodes.owners[("C", "FPGA")] is None
    assert nodes.owners[("DA", "Ch0")] is not None
    assert nodes.owners[("DA", "Ch1")] is not None
    assert nodes.owners[("DA", "Ch0")] is not nodes.owners[("DA", "Ch1")]
    assert nodes == dict(nodes.items())          # rule 33: the walk tests stand


# ── Т4-2: cells for the mutations that survived the 05.10 acceptance ──────

def _same_net_channels(channels=3):
    """FPGA (Role FPGA, once) plus one AD_DAC/DAC_BUF chip per channel, all on
    ONE net — so only the module label (`DAC_BUF/Channel_N`), never the net,
    tells the channels apart (Т4-3)."""
    fps = [_fp("FPGA1", "FPGA", "FPGA", 10.0, _Y[0], ("fpga",))]
    pads = {"FPGA1": [_pad(i + 1, "PA_EN", 10.0, _Y[i]) for i in range(channels)]}
    for i in range(channels):
        dac = _fp(f"IC{i + 2}", "AD_DAC", "DAC_BUF", _X_DAC, _Y[i],
                  (f"ch{i}", f"dac{i}"))
        fps.append(dac)
        pads[dac.ref] = [_pad("1", "PA_EN", _X_DAC, _Y[i])]
    return fps, pads


def _same_net_bridge(i):
    y = _Y[i]
    return [_track(10.0, y, 60.0, y, "PA_EN"), _via(60.0, y, "PA_EN"),
            _track(60.0, y, _X_DAC, y, "PA_EN")]


def test_three_channels_of_one_tag_on_one_net_get_three_distinct_names():
    """Т4-3 — three channels share the SAME Cluster tag AND the same net, so the
    name must come from the node LABEL `<cluster>/<sheet>`: three names that name
    their channel, never an order-dependent `_2`/`_3` suffix. Mutation 'module
    label without the sheet' turns this red."""
    fps, pads = _same_net_channels(3)
    cfg = _cfg_fpga(3)
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, _tree_fpga(cfg),
        area_items=(_same_net_bridge(0) + _same_net_bridge(1)
                    + _same_net_bridge(2)),
        area_footprints=fps, sheet_names=_SHEET_NAMES)
    identities = sorted(c.identity for c in plan.added)
    assert identities == ["pa_en__dac_buf_channel_0__fpga",
                          "pa_en__dac_buf_channel_1__fpga",
                          "pa_en__dac_buf_channel_2__fpga"]


def test_a_chain_between_two_channels_whose_roles_never_narrow_is_skipped():
    """Т4-2 A2 — a chain between the DAC of Channel_0 and the DAC of Channel_1.
    For EITHER candidate anchor the OTHER channel's AD_DAC role narrows to the
    candidate's OWN channel, not to the piece it touches — so neither candidate
    is acceptable and the piece is SKIPPED with a warning naming the role. With
    the mutation "narrowed to one is enough" the first candidate is accepted
    wrongly and the piece would be added."""
    fps, pads = _fpga_channel_fps(2)
    cfg = _cfg_fpga(2)
    yi, yj, mid = _Y[0] + 5.0, _Y[1] + 5.0, (_Y[0] + _Y[1]) / 2 + 5.0
    chain = [_track(_X_DAC + 5.0, yi, _X_DAC + 5.0, mid, "XCH"),
             _via(_X_DAC + 5.0, mid, "XCH"),
             _track(_X_DAC + 5.0, mid, _X_DAC + 5.0, yj, "XCH")]
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, _tree_fpga(cfg),
        area_items=chain, area_footprints=fps, sheet_names=_SHEET_NAMES)
    assert plan.added == []
    assert any("AD_DAC" in w for w in plan.warnings)


def test_a_module_nodes_own_child_is_a_node_of_this_tree():
    """Т4-2 A4 — a `kind "module"` node may carry its OWN placement children,
    which are ordinary nodes of THIS tree (owner None). A piece from such a child
    to the tree's own node is INTERNODE. Mutation 'a module node's own children
    are not walked' turns the child FOREIGN and this red."""
    module_node = _node("m0", "module")
    module_node.children = [_node("e_child", "placement")]
    inner = _Tree("m0", [_node("e_in", "placement")])
    fpga = _Tree("fpga", [_node("e_fpga", "placement"), module_node])
    cfg = Config(
        entities=[Entity(name="e_fpga", cell="c", cluster="FPGA", sheet="FPGA"),
                  Entity(name="e_child", cell="c", cluster="CHILD", sheet="FPGA"),
                  Entity(name="e_in", cell="c", cluster="INMOD", sheet="Ch0")],
        trees=[inner, fpga])
    fps = [_fp("FPGA1", "R_FPGA", "FPGA", 10.0, 10.0, ("fpga",)),
           _fp("CH1", "R_CHILD", "CHILD", 30.0, 10.0, ("fpga",))]
    pads = {"FPGA1": [_pad("1", "N1", 10.0, 10.0)],
            "CH1": [_pad("1", "N1", 30.0, 10.0)]}
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, fpga,
        area_items=[_track(10.0, 10.0, 30.0, 10.0, "N1")],
        area_footprints=fps, sheet_names={"fpga": "FPGA"})
    assert [set(c.record.pads) for c in plan.added] == [{"R_CHILD.1", "R_FPGA.1"}]


def test_a_mid_chain_via_falls_back_to_the_CHOSEN_anchor_not_pads_zero():
    """Т4-2 A6 — the FPGA pad sorts FIRST, but the anchor is the DAC side (the
    FPGA anchor would leave AD_DAC ambiguous). The mid-chain via touches no pad,
    so its reference falls back to the CHOSEN anchor's role — AD_DAC, not the
    first pad's FPGA. Mutation 'the record falls back to pads[0]' turns this red."""
    fps, pads = _fpga_channel_fps(3)
    cfg = _cfg_fpga(3)
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, _tree_fpga(cfg),
        area_items=_bridge_fpga_to_dac(0), area_footprints=fps,
        sheet_names=_SHEET_NAMES)
    record = plan.added[0].record
    assert record.anchor_role == "AD_DAC"          # NOT the first pad (FPGA)
    assert len(record.vias) == 1
    assert record.vias[0].net_from_role == "AD_DAC"
    assert record.vias[0].net_from_role_pad == "1"


# ── Т4-4: the narrowing cascade runs once per question ────────────────────

def test_the_role_narrowing_cascade_runs_once_per_question(monkeypatch):
    """Т4-4 — the anchor check asks the SAME `(role, sheet, cluster)` for every
    pad and every item; each call used to emit an INFO `role_narrowing` line (a
    live `fpga` re-read wrote 532 of them). The RESULT of narrowing is cached for
    the run, so two identical checks run the cascade ONCE. Mutation 'cache only
    the candidates' turns this red."""
    import kicadstamp.internode_nodes as nodes_mod
    from kicadstamp.internode_nodes import RoleResolver

    calls: list = []
    real = nodes_mod._narrow_by_sheet_cluster_selection

    def spy(candidates, adapter, selected_refs, anchor_sheet, anchor_cluster,
            sheet_names, label, role_str):
        calls.append((role_str, anchor_sheet, anchor_cluster))
        return real(candidates, adapter, selected_refs, anchor_sheet,
                    anchor_cluster, sheet_names, label, role_str)

    monkeypatch.setattr(nodes_mod, "_narrow_by_sheet_cluster_selection", spy)
    fps, pads = _fpga_channel_fps(3)
    resolver = RoleResolver(_Board(fps, pads), _SHEET_NAMES)
    assert resolver.resolves_to("AD_DAC", "Channel_0", "DAC_BUF", "IC2")
    assert resolver.resolves_to("AD_DAC", "Channel_0", "DAC_BUF", "IC2")
    assert calls == [("AD_DAC", "Channel_0", "DAC_BUF")]
