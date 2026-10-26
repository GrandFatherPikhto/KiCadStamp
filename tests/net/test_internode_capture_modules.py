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
from kicadstamp.internode_capture import apply_reread_plan, plan_internode_reread
from kicadstamp.internode_nodes import RoleResolver, tree_node_keys
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
    plain = plan_internode_reread(_Board(fps, pads), cfg, _tree_fpga(cfg), config_path=None,
                                  area_items=items, area_footprints=fps,
                                  sheet_names=_SHEET_NAMES)
    selected = plan_internode_reread(_Board(fps, pads, selected=[ic2]), cfg,
                                     _tree_fpga(cfg), config_path=None, area_items=items,
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
        _Board(fps, pads), cfg, cfg.trees[0], config_path=None,
        area_items=[_track(10.0, 10.0, 30.0, 10.0, "AMB"),     # unanchorable
                    _track(10.0, 30.0, 30.0, 30.0, "OKN")],    # fine
        area_footprints=fps)
    assert [set(c.record.pads) for c in plan.added] == [{"OK_ROLE.1", "OK2_ROLE.1"}]
    # 23.10: a run now also says whether cell copper was given back. `config_path=None` is "no
    # profile", so the second line is the honest note about it — the skip message is still the
    # ONLY other line, which is what this cell is about.
    assert len(plan.warnings) == 2, plan.warnings
    # The skip message is not necessarily FIRST any more (the cell-copper note is appended by
    # the classifier's own step) — take it by what it says.
    warning = next(w for w in plan.warnings
                   if "no pad of the piece anchors the record" in w)
    assert any("cell copper is NOT subtracted" in w for w in plan.warnings)
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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
        board, cfg, _tree_fpga(cfg), config_path=None,
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


def _nested_module_cfg():
    """A module INSIDE a module: the parent tree owns ONE module node m0, whose
    children are its own placement node (e_a) AND another module node m1 (whose
    child is e_b). Both ends are reached THROUGH m0."""
    entities = [Entity(name="e_a", cell="c", cluster="CA", sheet="S0"),
                Entity(name="e_b", cell="c", cluster="CB", sheet="S1")]
    m1 = _Tree("m1", [_node("e_b", "placement")])
    m0 = _Tree("m0", [_node("e_a", "placement"), _node("m1", "module")])
    fpga = _Tree("fpga", [_node("m0", "module")])
    return Config(entities=entities, trees=[m1, m0, fpga])


_SHEETS_NESTED = {"s0": "S0", "s1": "S1"}


def _nested_module_board():
    fps = [_fp("A0", "R_A", "CA", 10.0, 10.0, ("s0",)),
           _fp("B0", "R_B", "CB", 30.0, 10.0, ("s1",))]
    pads = {"A0": [_pad("1", "N_AB", 10.0, 10.0)],
            "B0": [_pad("1", "N_AB", 30.0, 10.0)]}
    return _Board(fps, pads), fps


def test_copper_inside_a_module_nested_in_another_module_is_the_top_modules():
    """Доделка 4а / C1 — the TOPMOST module owns a node reached through it, however
    deep the nesting: a piece between m0's OWN child and a node INSIDE m0's nested
    module m1 is ONE module's copper (m0), so the parent tree must NOT take it.
    Mutation 'a NESTED module owns its nodes (not the topmost one)' gives the two
    ends different owners and turns this red."""
    board, fps = _nested_module_board()
    cfg = _nested_module_cfg()
    plan = plan_internode_reread(
        board, cfg, _tree_fpga(cfg), config_path=None,
        area_items=[_track(10.0, 10.0, 30.0, 10.0, "N_AB")],
        area_footprints=fps, sheet_names=_SHEETS_NESTED)
    assert plan.added == []
    assert plan.discarded.get("module") == 1


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
        _Board(fps, pads), cfg, _tree_fpga(cfg), config_path=None,
        area_items=(_same_net_bridge(0) + _same_net_bridge(1)
                    + _same_net_bridge(2)),
        area_footprints=fps, sheet_names=_SHEET_NAMES)
    identities = sorted(c.identity for c in plan.added)
    assert identities == ["pa_en__dac_buf_channel_0__fpga",
                          "pa_en__dac_buf_channel_1__fpga",
                          "pa_en__dac_buf_channel_2__fpga"]


def test_a_chain_between_two_channels_gets_a_record_whose_elements_all_stay_in_the_piece():
    """Т4-2 A2, rewritten by meaning (plan_2026_10_15, decision «вариант B»): the
    chain between the DAC of Channel_0 and the DAC of Channel_1 is no longer
    SKIPPED. Tier 1 still refuses both candidates (the far element's AD_DAC
    narrows to the candidate's OWN channel, not to the piece it touches), tier 2
    takes the first candidate whose OWN role resolves and writes every element
    that cannot name its own footprint with the ANCHOR's pair — the piece is ONE
    net, exactly as a mid-chain via has always been written.

    What A2 protected is kept as an INVARIANT: no element's recorded pair
    resolves, under the record's own (sheet, cluster), to a footprint OUTSIDE the
    piece (the wrong-net write the 05.10 check existed to prevent), and the
    read->redraw loop closes: plan_net_traces resolves every element on XCH."""
    fps, pads = _fpga_channel_fps(2)
    cfg = _cfg_fpga(2)
    yi, yj, mid = _Y[0] + 5.0, _Y[1] + 5.0, (_Y[0] + _Y[1]) / 2 + 5.0
    chain = [_track(_X_DAC + 5.0, yi, _X_DAC + 5.0, mid, "XCH"),
             _via(_X_DAC + 5.0, mid, "XCH"),
             _track(_X_DAC + 5.0, mid, _X_DAC + 5.0, yj, "XCH")]
    plan = plan_internode_reread(
        _Board(fps, pads), cfg, _tree_fpga(cfg), config_path=None,
        area_items=chain, area_footprints=fps, sheet_names=_SHEET_NAMES)
    assert not any("no pad of the piece anchors" in w for w in plan.warnings)
    assert len(plan.added) == 1
    record = plan.added[0].record
    assert record.anchor_sheet in {"Channel_0", "Channel_1"}

    resolver = RoleResolver(_Board(fps, pads), _SHEET_NAMES)
    piece_refs = {"IC2", "IC3"}
    pairs = _record_pairs(record)
    assert pairs                                  # every element names its chain
    for role, _pad in pairs:
        landed = {fp.ref for fp in resolver.narrowed(
            role, record.anchor_sheet, record.anchor_cluster)}
        assert landed and landed <= piece_refs

    record = dataclasses.replace(record, uuid="uuid-xch")
    vias, tracks = plan_net_traces(_Board(fps, pads), [record], _SHEET_NAMES)
    assert {t.net_name for t in tracks} == {"XCH"}
    assert {v.net_name for v in vias} == {"XCH"}


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
        _Board(fps, pads), cfg, fpga, config_path=None,
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
        _Board(fps, pads), cfg, _tree_fpga(cfg), config_path=None,
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


# ── Т5 of plan_2026_10_15_internode_power_copper_anchor: the anchor's SECOND ──
#    tier, and the piece it exists for
#
# Tier 1 (inside choose_anchor_pad) is byte-for-byte the 05.10 rule: a candidate
# is the anchor only when EVERY element of the piece resolves, under the anchor's
# (sheet, cluster), to the footprint it touches. Tier 2 runs ONLY when tier 1
# found no candidate, and asks less: the anchor's OWN role must resolve to itself
# (the same resolver.resolves_to — never weakened), while an element whose own
# pair does NOT resolve to its own footprint is written with the ANCHOR's pair.
# That is legal because the whole piece is ONE net: it is exactly what a
# mid-chain via (an element with no pad of its own) has always been written as.
#
# THE piece this exists for (Denis, 15.10, log 16:33:03, tree `mcu`): +5V /
# +3V3_STM32 copper between clusters that SHARE one role (C_IN_BULK at C1, C2, C3
# in three clusters) plus the tree's own pad. Every candidate anchor of such a
# piece fails tier 1 — an element at another cluster's C_IN_BULK narrows to the
# anchor's OWN C_IN_BULK, never to the one it touches — so the piece was skipped
# with "no pad of the piece anchors the record" and power copper between clusters
# could never be re-read at all.

def _record_pairs(record):
    """{(role, pad)} of every element of a record — the references AS STORED."""
    pairs = {(t.net_from_role, t.net_from_role_pad) for t in record.tracks}
    pairs |= {(v.net_from_role, v.net_from_role_pad) for v in record.vias}
    return pairs


def _tree(cfg, name):
    return next(t for t in cfg.trees if t.name == name)


_POWER_SHEET_NAMES = {"mcu": "MCU", "power": "POWER"}
_THIRD_SHEET_NAMES = {"mcu": "MCU", "power": "POWER"}


def _power_rail_fps():
    """The fact in miniature: BZ1 and R1 sit in the tree's OWN node (Cluster MCU,
    sheet MCU); C1/C2/C3 carry the SAME role C_IN_BULK in three DIFFERENT clusters
    of the embedded power body; F1 is a fourth cluster. Every pad of the piece is
    on one net (+5V) — one copper island, one net."""
    fps = [_fp("BZ1", "BUZZER_P5V", "MCU", 10.0, 10.0, ("mcu",)),
           _fp("R1", "R_MCU_PULL", "MCU", 110.0, 10.0, ("mcu",)),
           _fp("C1", "C_IN_BULK", "P5V_IN", 10.0, 110.0, ("power",)),
           _fp("C2", "C_IN_BULK", "LDO_P3V3_MAX6675_01", 110.0, 110.0, ("power",)),
           _fp("C3", "C_IN_BULK", "LDO_P3V3_MAX6675_02", 60.0, 10.0, ("power",)),
           _fp("F1", "FUSE", "FUSE", 60.0, 110.0, ("power",))]
    pads = {"BZ1": [_pad("1", "+5V", 10.0, 10.0)],
            "R1": [_pad("1", "+5V", 110.0, 10.0)],
            "C1": [_pad("1", "+5V", 10.0, 110.0)],
            "C2": [_pad("1", "+5V", 110.0, 110.0)],
            "C3": [_pad("1", "+5V", 60.0, 10.0)],
            "F1": [_pad("2", "+5V", 60.0, 110.0)]}
    return fps, pads


def _power_star():
    """A STAR: one central via with one track per pad, so the whole piece is ONE
    unit of SIX pads — the fact's shape ("a piece of copper between pads BZ1.1,
    C1.1, C2.1, C3.1, F1.2"). A serial pad->via->pad chain would be split INTO
    one unit per span (a pad box in the middle of a joint terminates it), which
    is a different, much smaller piece. Every branch ends in exactly one pad, so
    every track owns exactly one reference."""
    return [_track(10.0, 10.0, 60.0, 60.0, "+5V"),      # BZ1's branch
            _track(110.0, 10.0, 60.0, 60.0, "+5V"),     # R1
            _track(10.0, 110.0, 60.0, 60.0, "+5V"),     # C1
            _track(110.0, 110.0, 60.0, 60.0, "+5V"),    # C2
            _track(60.0, 10.0, 60.0, 60.0, "+5V"),      # C3
            _track(60.0, 110.0, 60.0, 60.0, "+5V"),     # F1
            _via(60.0, 60.0, "+5V")]


def _cfg_power_embedded():
    """The power body as a `kind "module"` node of tree `mcu` — the fact's shape,
    and the reason BZ1 counts as the tree's OWN node while C1/C2/C3/F1 are
    clusters of the embedded body (Т1 walks module content)."""
    entities = [Entity(name="e_mcu", cell="c", cluster="MCU", sheet="MCU"),
                Entity(name="e_p5v", cell="c", cluster="P5V_IN", sheet="POWER"),
                Entity(name="e_ldo1", cell="c", cluster="LDO_P3V3_MAX6675_01",
                       sheet="POWER"),
                Entity(name="e_ldo2", cell="c", cluster="LDO_P3V3_MAX6675_02",
                       sheet="POWER"),
                Entity(name="e_fuse", cell="c", cluster="FUSE", sheet="POWER")]
    power = _Tree("e_power", [_node("e_p5v", "placement"),
                              _node("e_ldo1", "placement"),
                              _node("e_ldo2", "placement"),
                              _node("e_fuse", "placement")])
    mcu = _Tree("mcu", [_node("e_mcu", "placement"),
                        _node("e_power", "module")])
    return Config(entities=entities, trees=[power, mcu])


def test_copper_between_clusters_that_share_a_role_gets_a_record_from_the_second_tier():
    """Т5-1 (the FACT) — +5V copper between the tree's own node and three clusters
    that share the role C_IN_BULK. Tier 1 refuses every candidate (an element at
    another cluster's C_IN_BULK narrows to the anchor's own), so before this plan
    the piece was skipped with "no pad of the piece anchors the record" and power
    copper between clusters never re-read. Tier 2 takes the first candidate whose
    OWN role resolves to itself; every element that cannot name its own footprint
    is written with the ANCHOR's pair and apply gives the whole piece one net.

    Mutation "tier 2 removed" turns this red (no record, the skip warning is
    back). Mutation "tier 2 writes the element's own pair whenever the narrowing
    found ONE" turns the pair assertion red (C_IN_BULK / FUSE appear)."""
    fps, pads = _power_rail_fps()
    cfg = _cfg_power_embedded()
    board = _Board(fps, pads)
    plan = plan_internode_reread(board, cfg, _tree(cfg, "mcu"), config_path=None,
                                 area_items=_power_star(), area_footprints=fps,
                                 sheet_names=_POWER_SHEET_NAMES)
    assert not any("no pad of the piece anchors" in w for w in plan.warnings)
    assert len(plan.added) == 1
    record = plan.added[0].record
    assert (record.anchor_sheet, record.anchor_cluster) == ("MCU", "MCU")
    assert (record.anchor_role, record.anchor_pad) in {("BUZZER_P5V", "1"),
                                                       ("R_MCU_PULL", "1")}
    # The THREE C_IN_BULK pads of the foreign clusters are never named: their own
    # pair does not narrow to the pad the element touches. The tree's OWN pads
    # (BZ1/R1, whose roles are unique on the board) and F1 (the only FUSE) keep
    # their own pair — what the second tier writes for an element is decided per
    # element, never for the piece.
    assert _record_pairs(record) == {("BUZZER_P5V", "1"), ("R_MCU_PULL", "1"),
                                     ("FUSE", "2")}

    record = dataclasses.replace(record, uuid="uuid-p5v")
    vias, tracks = plan_net_traces(board, [record], _POWER_SHEET_NAMES)
    assert len(tracks) == 6 and len(vias) == 1
    assert {t.net_name for t in tracks} == {"+5V"}
    assert {v.net_name for v in vias} == {"+5V"}


def test_rereading_the_same_power_copper_reports_unchanged_not_updated():
    """Т5-1b — the second read of the SAME board, with the first read's record in
    the config, reports "unchanged" and adds nothing: the record is matched by its
    pad signature (never by the element references) and `same_geometry` compares
    geometry only, so the second tier's pairs are not rewritten on disk.

    (The comparison moved to `internode_nodes` in Ч1 of
    plan_2026_10_26_reread_updated_noise; the claim of this cell is unchanged.)"""
    fps, pads = _power_rail_fps()
    cfg = _cfg_power_embedded()
    board = _Board(fps, pads)
    first = plan_internode_reread(board, cfg, _tree(cfg, "mcu"), config_path=None,
                                  area_items=_power_star(), area_footprints=fps,
                                  sheet_names=_POWER_SHEET_NAMES)
    assert len(first.added) == 1
    applied = apply_reread_plan(cfg, first)
    # The tree owns its record through a `kind "net_trace"` node whose ref IS the
    # record identity (tree_net_trace_identities) — exactly what the dialog writes
    # after a re-read adds one.
    tree = _Tree("mcu", [_node("e_mcu", "placement"),
                         _node("e_power", "module"),
                         _node(first.added[0].identity, "net_trace")])
    applied = dataclasses.replace(
        applied, trees=[tree if t.name == "mcu" else t for t in applied.trees])
    second = plan_internode_reread(board, applied, tree, config_path=None,
                                   area_items=_power_star(), area_footprints=fps,
                                   sheet_names=_POWER_SHEET_NAMES)
    assert second.added == [] and second.updated == []
    assert len(second.unchanged) == 1


def _third_component_board():
    """A piece between A1 (the tree's own node MCU) and C1 (cluster RAIL), with a
    THIRD component B1: the SAME role as C1, sitting in A1's cluster, on ANOTHER
    net. Under the anchor's own context (MCU, MCU) the role R_C of the element at
    C1 narrows to B1 — a component OUTSIDE the piece, on net OTHER — so writing
    that element its OWN pair would take the net from a pad the piece never
    touches: the wrong-net write the 05.10 check existed to prevent. A2 (cluster
    RAIL, role R_A) exists so the OTHER candidate fails tier 1 as well — without
    it the piece would anchor on C1 inside tier 1 and never reach tier 2."""
    fps = [_fp("A1", "R_A", "MCU", 10.0, 10.0, ("mcu",)),
           _fp("B1", "R_C", "MCU", 30.0, 30.0, ("mcu",)),
           _fp("C1", "R_C", "RAIL", 50.0, 10.0, ("power",)),
           _fp("A2", "R_A", "RAIL", 70.0, 30.0, ("power",))]
    pads = {"A1": [_pad("1", "SIG", 10.0, 10.0)],
            "B1": [_pad("1", "OTHER", 30.0, 30.0)],
            "C1": [_pad("1", "SIG", 50.0, 10.0)],
            "A2": [_pad("1", "OTHER", 70.0, 30.0)]}
    cfg = Config(
        entities=[Entity(name="e_a", cell="c", cluster="MCU", sheet="MCU"),
                  Entity(name="e_c", cell="c", cluster="RAIL", sheet="POWER")],
        trees=[_Tree("t", [_node("e_a", "placement"),
                           _node("e_c", "placement")])])
    chain = [_track(10.0, 10.0, 20.0, 10.0, "SIG"), _via(20.0, 10.0, "SIG"),
             _track(20.0, 10.0, 50.0, 10.0, "SIG")]
    return fps, pads, cfg, chain


def test_an_element_whose_pair_lands_on_a_component_outside_the_piece_gets_the_anchors_pair():
    """Т5-2 — A2's danger, kept and made visible: the component the narrowing
    finds may be a THIRD one, neither the anchor nor anything the piece touches.
    Every element of the record must therefore carry the ANCHOR's pair, so every
    element resolves INSIDE the piece (control first: the wrong pair really does
    land on B1, whose pad is on another net — rule 39). Mutation "the narrowing
    found one candidate, that is enough" (tier 2 writes the own pair) turns the
    invariant and the net assertion red — without a third component the mutation
    is invisible, because in A2 the foreign component IS the anchor."""
    fps, pads, cfg, chain = _third_component_board()
    board = _Board(fps, pads)
    resolver = RoleResolver(board, _THIRD_SHEET_NAMES)
    assert [fp.ref for fp in resolver.narrowed("R_C", "MCU", "MCU")] == ["B1"]
    assert [fp.ref for fp in resolver.narrowed("R_A", "POWER", "RAIL")] == ["A2"]

    plan = plan_internode_reread(board, cfg, cfg.trees[0], config_path=None, area_items=chain,
                                 area_footprints=fps,
                                 sheet_names=_THIRD_SHEET_NAMES)
    assert len(plan.added) == 1
    record = plan.added[0].record
    piece_refs = {"A1", "C1"}
    pairs = _record_pairs(record)
    assert pairs
    for role, _pad in pairs:
        landed = {fp.ref for fp in resolver.narrowed(
            role, record.anchor_sheet, record.anchor_cluster)}
        assert landed and landed <= piece_refs

    record = dataclasses.replace(record, uuid="uuid-third")
    vias, tracks = plan_net_traces(board, [record], _THIRD_SHEET_NAMES)
    assert {t.net_name for t in tracks} == {"SIG"}
    assert {v.net_name for v in vias} == {"SIG"}


# ── Д-1 of plan_2026_10_15_internode_power_copper_anchor: the UPDATE path ─────
#
# A tier-2 record's anchor is often NOT the piece's first pad (pads[0] failed the
# anchor's own check). When such a record is REFRESHED, an element that cannot name
# its own footprint must take the RECORD's anchor pair — because the piece's first
# pad can resolve, under the record's context, to a THIRD component on ANOTHER net.
# That is how A2's wrong-net write came back through the update path.

def _tree_owning(cfg, tree_name, identity, *nodes):
    """`cfg` (and the tree) with a `kind "net_trace"` node whose `ref` IS the record
    identity — exactly what the dialog writes when a re-read adds a record, and the
    only way a re-read can MATCH a stored record instead of adding a new one."""
    tree = _Tree(tree_name, list(nodes) + [_node(identity, "net_trace")])
    cfg = dataclasses.replace(
        cfg, trees=[tree if t.name == tree_name else t for t in cfg.trees])
    return cfg, tree


def _second_tier_reread_board(via_mm):
    """The Д-1 probe's board. A1 and A9 share the role R_DUP, so A1's candidacy is
    poisoned BY THE ROLE (A9 sits far away, on another net, and never touches the
    piece); C1 is the piece's other end (role R_C); B1 is the THIRD component —
    role R_DUP in C1's cluster, on net OTHER. The via sits at `via_mm` on X, so the
    two reads differ in GEOMETRY: the second one updates the stored record."""
    fps = [_fp("A1", "R_DUP", "MCU", 10.0, 10.0, ("mcu",)),
           _fp("A9", "R_DUP", "MCU", 10.0, 40.0, ("mcu",)),
           _fp("B1", "R_DUP", "RAIL", 10.0, 70.0, ("power",)),
           _fp("C1", "R_C", "RAIL", 50.0, 10.0, ("power",))]
    pads = {"A1": [_pad("1", "SIG", 10.0, 10.0)],
            "A9": [_pad("1", "OTHER2", 10.0, 40.0)],
            "B1": [_pad("1", "OTHER", 10.0, 70.0)],
            "C1": [_pad("1", "SIG", 50.0, 10.0)]}
    cfg = Config(
        entities=[Entity(name="e_mcu", cell="c", cluster="MCU", sheet="MCU"),
                  Entity(name="e_rail", cell="c", cluster="RAIL", sheet="POWER")],
        trees=[_Tree("e_power", [_node("e_rail", "placement")]),
               _Tree("mcu", [_node("e_mcu", "placement"),
                             _node("e_power", "module")])])
    chain = [_track(10.0, 10.0, via_mm, 10.0, "SIG"), _via(via_mm, 10.0, "SIG"),
             _track(via_mm, 10.0, 50.0, 10.0, "SIG")]
    return fps, pads, cfg, chain


def test_updating_a_second_tier_record_keeps_every_element_inside_the_piece():
    """Д-1 — the update path. The first read anchors on C1 (tier 2: A1's own role
    R_DUP is ambiguous on the board, so A1 is no candidate at all). The second read
    moves the via, so the record is UPDATED — and the element at A1 must take the
    record's anchor pair (R_C/1), never its own (R_DUP/1: under the record's
    (POWER, RAIL) that narrows to B1, a third component on net OTHER). Every pair
    resolves INSIDE {A1, C1} and apply gives ONE net.

    Mutation "the existing record's fall-back = unit.pads[0]" turns the pair
    assertion and the net assertion red."""
    fps, pads, cfg, chain = _second_tier_reread_board(20.0)
    board = _Board(fps, pads)
    first = plan_internode_reread(board, cfg, _tree(cfg, "mcu"), config_path=None, area_items=chain,
                                  area_footprints=fps,
                                  sheet_names=_POWER_SHEET_NAMES)
    assert len(first.added) == 1
    record = first.added[0].record
    assert (record.anchor_role, record.anchor_pad) == ("R_C", "1")
    assert _record_pairs(record) == {("R_C", "1")}

    applied, tree = _tree_owning(apply_reread_plan(cfg, first), "mcu",
                                 first.added[0].identity,
                                 _node("e_mcu", "placement"),
                                 _node("e_power", "module"))
    fps2, pads2, _cfg2, chain2 = _second_tier_reread_board(24.0)
    board2 = _Board(fps2, pads2)
    second = plan_internode_reread(board2, applied, tree, config_path=None, area_items=chain2,
                                   area_footprints=fps2,
                                   sheet_names=_POWER_SHEET_NAMES)
    assert second.added == [] and len(second.updated) == 1
    refreshed = second.updated[0][0].record
    assert _record_pairs(refreshed) == {("R_C", "1")}
    resolver = RoleResolver(board2, _POWER_SHEET_NAMES)
    for role, _pad in _record_pairs(refreshed):
        landed = {fp.ref for fp in resolver.narrowed(
            role, refreshed.anchor_sheet, refreshed.anchor_cluster)}
        assert landed and landed <= {"A1", "C1"}

    refreshed = dataclasses.replace(refreshed, uuid="uuid-d1")
    vias, tracks = plan_net_traces(board2, [refreshed], _POWER_SHEET_NAMES)
    assert {t.net_name for t in tracks} == {"SIG"}
    assert {v.net_name for v in vias} == {"SIG"}


def test_updating_an_fpga_bridge_record_keeps_the_via_on_the_records_anchor_pair():
    """Д-1, the older half — the same mechanism existed BEFORE tier 2: the FPGA<->
    DAC bridge of Т3-3, re-read after its via MOVED. That record is anchored on the
    DAC side while pads[0] is the FPGA end, so the mid-chain via (an element with no
    pad of its own) must take the RECORD's anchor pair `AD_DAC/1`; it used to take
    pads[0]'s role (FPGA), and only the FPGA role's uniqueness kept that harmless.
    Mutation "the existing record's fall-back = unit.pads[0]" turns this red."""
    fps, pads = _fpga_channel_fps(3)
    cfg = _cfg_fpga(3)
    board = _Board(fps, pads)
    first = plan_internode_reread(board, cfg, _tree_fpga(cfg), config_path=None,
                                  area_items=_bridge_fpga_to_dac(0),
                                  area_footprints=fps, sheet_names=_SHEET_NAMES)
    assert len(first.added) == 1
    applied, tree = _tree_owning(apply_reread_plan(cfg, first), "fpga",
                                 first.added[0].identity,
                                 _node("e_fpga", "placement"),
                                 _node("ch0_dac_buf", "module"),
                                 _node("ch1_dac_buf", "module"),
                                 _node("ch2_dac_buf", "module"))
    moved = [_track(10.0, _Y[0], 64.0, _Y[0], "DB0"),
             _via(64.0, _Y[0], "DB0"),
             _track(64.0, _Y[0], _X_DAC, _Y[0], "DB0")]
    second = plan_internode_reread(_Board(fps, pads), applied, tree, config_path=None,
                                   area_items=moved, area_footprints=fps,
                                   sheet_names=_SHEET_NAMES)
    assert second.added == [] and len(second.updated) == 1
    refreshed = second.updated[0][0].record
    assert len(refreshed.vias) == 1
    assert (refreshed.vias[0].net_from_role,
            refreshed.vias[0].net_from_role_pad) == ("AD_DAC", "1")
