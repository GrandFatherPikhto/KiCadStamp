# tests/gui/docks/test_tree_node_redraw_from_live_parent.py
"""A child node's Redraw is placed from its parent ON THE BOARD, not from the tree.

Plan plan_2026_11_09_node_redraw_from_live_parent (09.11, HP-Power-v099, tree `switch_a`,
node `conn_out_sw_a`): "Read current position" gave 69.994, 8.014 and was right, while Redraw
of the same numbers put the pads below the board — the Read resolved the parent from the
BOARD, the Redraw let `ApplyPipeline(only=[ref])` lay the node out from the tree's anchor
(and therefore from the Grid Origin and from module offsets the tab does not know).

THE CONTRACT, cell by cell:

* а / г — Read then Redraw of the same node does NOT move it (and the base rotation and the
  node's own `rotation` both take part);
* б — the tree's own base (its anchor) has no influence on a CHILD;
* в — an embedded tree's child is placed from its LIVE parent, module offset and all;
* д / е — a parent that cannot be read (missing, mirrored) is a REFUSAL: one Log line, the
  pipeline is never even constructed, and there is NO fallback to the tree layout;
* ж / з — the TOP node of a module-embedded tree is refused (that one is placed through its
  module), while the top node of a plain tree still comes from its anchor;
* и — a `mount` parent contributes its POINT;
* к — the `module` and `SUBTREE_KINDS` branches are untouched.

The board is faked at ONE seam — `read_record_live_pose`, the very function the Read uses —
so both sides of every comparison see the same "live" answer, and the composition
(`local_offset_to_board_mm`), the mirror refusal and the payload plumbing stay real.
"""
from __future__ import annotations

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.tree_position import MountFrames, PositionOverride
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

import gui.docks.cascade as cascade_mod
import gui.docks.trees_accept as td_ac
import gui.docks.trees_base_pose as base_pose_mod
import gui.docks.trees_dock as td_mod
from gui.docks.cascade import run_single_node_redraw_worker
from gui.docks.live_position import LiveRecordPose
from gui.docks.trees_dock import TreesDock

from tests.fakes.explore_board import FakeExploreBoard
from tests.fakes.write_later import write_later

CHILD = "conn_out_sw_a"
PARENT = "driver_sw_a"

# The live board of the plan's own table: IC1 at 104.46, 104.39 and J6 at 174.454, 112.404 —
# i.e. the child stands at parent + (69.994, 8.014), which is exactly what the node stores.
LIVE = {
    PARENT: (104.46, 104.39, 0.0),
    CHILD: (174.454, 112.404, 0.0),
}

# `switch_a`'s anchor carries the profile's Grid Origin as its own shift, so the TREE puts
# its root at 72.454, 139.904 — the number Denis saw. A child's Redraw must not care.
TREES = {
    "trees": [
        {"name": "main", "anchor": {"origin": True},
         "nodes": [{"ref": "switch_a", "kind": "module", "xy": [32.0, -35.0]}]},
        {"name": "switch_a",
         "anchor": {"origin": True, "shift": [72.454, 139.904]},
         "nodes": [{"ref": PARENT, "kind": "placement", "xy": [0.0, 0.0],
                    "children": [{"ref": CHILD, "kind": "placement",
                                  "xy": [70.0, 8.0]}]}]},
        {"name": "solo", "anchor": {"origin": True},
         "nodes": [{"ref": "R_SOLO", "kind": "placement", "xy": [100.0, 50.0]}]},
    ],
    "cells": {"cell_sw": {}},
    "entities": [
        {"name": PARENT, "cluster": PARENT, "cell": "cell_sw"},
        {"name": CHILD, "cluster": CHILD, "cell": "cell_sw"},
        {"name": "R_SOLO", "cluster": "R_SOLO", "cell": "cell_sw"},
    ],
}


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """A QApplication must exist even when a cell runs FIRST in a worker (SIGABRT
    otherwise) — the same guard the sibling trees cells carry."""
    return qapp


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в): the format-3 writer resolves references against the ACTIVE GRAPH
    ROOT, and these cells write self-contained configs — so the root is a path that does not
    exist (the stamp indexes this write's own records)."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


@pytest.fixture
def pipes(monkeypatch):
    """Every `ApplyPipeline` the worker under test CONSTRUCTED, as its kwargs — the list a
    refusal must leave empty ("ни одной записи на плату")."""
    built = []

    class _Pipe:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def run(self):
            return None

    def _factory(**kwargs):
        built.append(kwargs)
        return _Pipe(**kwargs)

    monkeypatch.setattr(cascade_mod, "ApplyPipeline", _factory)
    return built


def _nm(x_mm: float, y_mm: float) -> Vector2:
    return Vector2.from_xy(int(round(x_mm * MM)), int(round(y_mm * MM)))


def _patch_live(monkeypatch, live=None, *, mirror=(), missing=()):
    """The BOARD half: `read_record_live_pose` answers from `live`.

    ONE seam, deliberately: the Read (`_resolve_live_offset`) and the Redraw
    (`resolve_node_base_pose`) both go through it, so a cell that compares them is comparing
    the two COMPOSITIONS, not two different fakes. `mirror` / `missing` name the refs whose
    pose is mirrored / unreadable.
    """
    live = dict(LIVE if live is None else live)

    def _fake(adapter, cfg, ref, record, sheet_names, *, snapshot=None, strict=False):
        if ref in missing:
            raise ValidationError(f"{ref!r} is not on the board")
        x_mm, y_mm, rot = live[ref]
        return LiveRecordPose(position=_nm(x_mm, y_mm), rotation_deg=rot,
                              mirror=ref in mirror)

    monkeypatch.setattr(td_mod, "read_record_live_pose", _fake)
    monkeypatch.setattr(base_pose_mod, "read_record_live_pose", _fake)
    return live


def _dock(main_window, tmp_path, data=None):
    root = tmp_path / "redraw.sexp"
    write_later(root, dict_to_sexp(data if data is not None else TREES, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway config failed to load — see the Log line"
    # A live adapter must be reachable (`_live_adapter`); the fake's `.adapter` is a non-None
    # sentinel on purpose (tests/fakes/explore_board.py).
    main_window.connection.board = FakeExploreBoard()
    return dock, root


def _tree(dock, name):
    return next(t for t in dock._trees if t.name == name)


def _child(dock):
    """`switch_a`'s root node and its one child, the plan's own pair."""
    root = _tree(dock, "switch_a").nodes[0]
    return root, root.children[0]


def _capture_starts(monkeypatch):
    """Record every worker a redraw door starts, from BOTH modules that own a `start_long_op`
    name: `trees_dock` (`_redraw_edited_node`, the tree-level redraws) and `trees_accept`
    (`redraw_subtree`, which imported the factory into its own namespace)."""
    starts = []

    def fake_start(connection, widgets, worker, finish, failed, payload, **kwargs):
        starts.append({"worker": worker, "payload": payload})
        return object()

    monkeypatch.setattr(td_mod, "start_long_op", fake_start)
    monkeypatch.setattr(td_ac, "start_long_op", fake_start)
    return starts


def _redraw(dock, node, monkeypatch):
    """Through the DOCK's own door — `_redraw_edited_node` is the product's only way into the
    worker, so every cell below starts there and takes the payload it hands over."""
    starts = _capture_starts(monkeypatch)
    dock._redraw_edited_node(node)
    return {"starts": starts, **(starts[0] if starts else {})}


def _redraw_whole_tree(dock, tree, monkeypatch):
    """«Redraw whole tree» through its own door: no `tree` = the Tools menu (the active tab),
    a `tree` = the root context menu. Returns the starts, empty when it refused."""
    from PyQt6.QtCore import Qt as _Qt

    for item in dock._node_items.values():      # «whole tree» ignores the ticks, but be clean
        item.setCheckState(0, _Qt.CheckState.Unchecked)
    starts = _capture_starts(monkeypatch)
    dock._on_redraw_whole_tree(None, tree)
    return starts


def _redraw_selected(dock, refs, monkeypatch, tree=None):
    """«Redraw selected» with exactly `refs` TICKED on the rendered rows — the real door.
    `tree` = the tree the ROOT context menu passed (None = the Tools menu, active tab)."""
    from PyQt6.QtCore import Qt as _Qt

    for ref, item in dock._node_items.items():
        item.setCheckState(0, _Qt.CheckState.Checked if ref in refs
                           else _Qt.CheckState.Unchecked)
    starts = _capture_starts(monkeypatch)
    dock._on_redraw_selected(None, tree)
    return starts


def _override(pipes, ref=CHILD, index=-1) -> PositionOverride:
    """The override the LAST pipeline run carried for `ref` (a cell that runs the worker
    twice compares the two runs itself)."""
    assert pipes, "no pipeline was constructed at all"
    overrides = pipes[index]["position_overrides"]
    assert overrides and ref in overrides
    return overrides[ref]


# ── а: Read then Redraw does not move the node ─────────────────────────────

def test_a_read_then_redraw_does_not_move_the_child(main_window, tmp_path, monkeypatch,
                                                    pipes):
    """Cell а: the parent stands at its LIVE place, which is far from where the tree would
    put it (the tree root sits at the Grid Origin, the board's IC1 does not). Reading the
    node and then redrawing the very numbers the Read produced must land on the SAME board
    point — the old code laid it out from the tree and "moved" it to 142.454, 147.904."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    node = _child(dock)[1]
    adapter = dock._live_adapter()

    offset_mm, rotation = td_mod._resolve_live_offset(
        dock._cfg, adapter, {}, _tree(dock, "switch_a"), _tree(dock, "switch_a").nodes[0],
        node.ref, node.kind)
    # The Read's own answer: 69.994, 8.014 — the plan's numbers (the node happened to store
    # 70, 8, i.e. the hand-picked pair; the contract is about the numbers the READ produced).
    assert offset_mm == pytest.approx((69.994, 8.014), abs=1e-6)
    assert rotation == pytest.approx(0.0)
    node.xy = offset_mm
    node.rotation = rotation

    captured = _redraw(dock, node, monkeypatch)
    assert captured["worker"] is run_single_node_redraw_worker
    results, warnings = run_single_node_redraw_worker(captured["payload"])

    assert results == [(CHILD, True, None)] and warnings == []
    target = _override(pipes)
    assert target.position == _nm(*LIVE[CHILD][:2]), (
        "Read then Redraw moved the node: expected the live child pose")
    assert target.rotation_deg == pytest.approx(0.0)


# ── б: the tree's own base never reaches a child ───────────────────────────

def test_b_the_tree_base_has_no_influence_on_a_child(main_window, tmp_path, monkeypatch,
                                                     pipes):
    """Cell б: the SAME node under two different tree bases (the anchor's shift stands in for
    two Grid Origins — the live ones need a board, see the probe in the report). The target
    must not budge, while the tree layout of that node moves by the whole shift."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    tree = _tree(dock, "switch_a")
    node = _child(dock)[1]

    captured = _redraw(dock, node, monkeypatch)
    run_single_node_redraw_worker(captured["payload"])
    first = _override(pipes).position

    tree.anchor = TreeAnchor(is_origin=True, shift_xy=(10.0, 20.0))
    captured = _redraw(dock, node, monkeypatch)
    run_single_node_redraw_worker(captured["payload"])
    second = _override(pipes).position

    # The node stores (70, 8): its target is the LIVE parent plus exactly that, both times.
    assert first == second == _nm(LIVE[PARENT][0] + 70.0, LIVE[PARENT][1] + 8.0)
    assert first != _nm(LIVE[PARENT][0] + 10.0 + 70.0, LIVE[PARENT][1] + 20.0 + 8.0)


# ── в: an embedded tree is placed from its live parent ─────────────────────

def test_v_an_embedded_trees_child_is_placed_from_its_live_parent(
        main_window, tmp_path, monkeypatch, pipes):
    """Cell в: `switch_a` is embedded by module `switch_a` of tree `main` at (32, −35), and
    its own anchor sits at the Grid Origin. Neither number may reach the child: the target is
    live parent + xy (70, 8)."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    tree = _tree(dock, "switch_a")
    assert dock._embedded_in(tree) == ["main"]
    node = tree.nodes[0].children[0]

    captured = _redraw(dock, node, monkeypatch)
    assert captured["payload"]["tree"] is tree
    assert captured["payload"]["parent_node"] is tree.nodes[0]
    run_single_node_redraw_worker(captured["payload"])

    # Live parent + the node's OWN xy (70, 8) — not the tree's 142.454, 147.904, and not the
    # module's (32, −35) either.
    assert _override(pipes).position == _nm(LIVE[PARENT][0] + 70.0,
                                           LIVE[PARENT][1] + 8.0)
    assert _override(pipes).position != _nm(72.454 + 70.0, 139.904 + 8.0)


# ── г: a rotated parent, and the node's own rotation ───────────────────────

def test_g_a_rotated_parent_is_composed_and_the_rotation_is_read(
        main_window, tmp_path, monkeypatch, pipes):
    """Cell г: the parent is turned 90°, so the child's stored (70, 8) is a vector in the
    PARENT's frame. The Read must give (70, 8) back from the rotated board pose, and the
    Redraw must compose it at the parent's angle again; a node `rotation` of 30 makes the
    placed angle 90 + 30."""
    dock, _root = _dock(main_window, tmp_path)
    # Y-down +90 maps (x, y) -> (y, -x): the parent at (100, 100) puts the child at (108, 30).
    live = {PARENT: (100.0, 100.0, 90.0), CHILD: (108.0, 30.0, 90.0)}
    _patch_live(monkeypatch, live)
    tree = _tree(dock, "switch_a")
    node = tree.nodes[0].children[0]
    adapter = dock._live_adapter()

    offset_mm, rotation = td_mod._resolve_live_offset(
        dock._cfg, adapter, {}, tree, tree.nodes[0], node.ref, node.kind)
    assert offset_mm == pytest.approx((70.0, 8.0), abs=1e-6)
    assert rotation == pytest.approx(0.0)
    node.xy = offset_mm
    node.rotation = rotation

    captured = _redraw(dock, node, monkeypatch)
    run_single_node_redraw_worker(captured["payload"])
    target = _override(pipes)
    assert target.position == _nm(108.0, 30.0)
    assert target.rotation_deg == pytest.approx(90.0)

    node.rotation = 30.0
    captured = _redraw(dock, node, monkeypatch)
    run_single_node_redraw_worker(captured["payload"])
    assert pipes[-1]["position_overrides"][CHILD].rotation_deg == pytest.approx(120.0)


# ── д / е: refusals that never touch the board ─────────────────────────────

def test_d_a_parent_missing_from_the_board_is_refused(main_window, tmp_path, monkeypatch,
                                                      pipes, caplog):
    """Cell д: the parent is not resolvable. ONE Log line naming the node AND the parent, the
    pipeline is never constructed (so nothing is written), and there is no fallback to the
    tree layout."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch, missing={PARENT})
    node = _child(dock)[1]

    captured = _redraw(dock, node, monkeypatch)
    with caplog.at_level("WARNING"):
        results, warnings = run_single_node_redraw_worker(captured["payload"])

    assert pipes == [], "a refused redraw must not even build the pipeline"
    assert results[0][0] == CHILD and results[0][1] is False
    message = results[0][2]
    assert CHILD in message and PARENT in message
    assert "NOT used instead" in message
    assert any(CHILD in record.getMessage() and PARENT in record.getMessage()
               for record in caplog.records)


def test_e_a_mirrored_parent_is_refused(main_window, tmp_path, monkeypatch, pipes):
    """Cell е: the parent's live instance is MIRRORED. A tree node stores no mirror, so this
    is the same refusal the Read gives — and again nothing is placed."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch, mirror={PARENT})
    node = _child(dock)[1]

    captured = _redraw(dock, node, monkeypatch)
    results, _warnings = run_single_node_redraw_worker(captured["payload"])

    assert pipes == []
    assert results[0][1] is False
    assert "MIRRORED" in results[0][2]
    assert PARENT in results[0][2]


# ── ж: the top of an EMBEDDED tree is placed through its module ────────────

def test_zh_the_top_of_an_embedded_tree_is_refused_with_the_marker(
        main_window, tmp_path, monkeypatch, caplog):
    """Cell ж: `switch_a`'s root belongs to module `switch_a` in tree `main`; redrawing it
    from its own tab could only anchor it at the tree's base (the Grid Origin) — the sentence
    names BOTH the module and the owning tree, and no worker starts."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    root = _child(dock)[0]

    with caplog.at_level("ERROR"):
        captured = _redraw(dock, root, monkeypatch)

    assert captured["starts"] == [], "no worker may start for a refused node"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "switch_a" in text and "main" in text
    assert "module" in text and "not from this tab" in text
    # §4.3: the sentence names the NODE too — «Redraw of node 'driver_sw_a'» — so a reader
    # with several refusal types in the Log knows which one is theirs.
    assert "node \'driver_sw_a\'" in text


# ── з: the top of a PLAIN tree still comes from its anchor ─────────────────

def test_z_the_top_of_a_plain_tree_still_comes_from_its_anchor(main_window, tmp_path,
                                                               monkeypatch, pipes):
    """Cell з: `solo` is embedded by nobody, so its top node keeps today's behaviour — the
    tree's own anchor. With an origin anchor the base is (0, 0), so xy (100, 50) lands at
    (100, 50), and the payload says `parent_node is None`."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    node = _tree(dock, "solo").nodes[0]

    captured = _redraw(dock, node, monkeypatch)
    assert captured["payload"]["parent_node"] is None
    run_single_node_redraw_worker(captured["payload"])

    assert _override(pipes, "R_SOLO").position == _nm(100.0, 50.0)


# ── и: a mount parent contributes its POINT ────────────────────────────────

def test_i_a_mount_parent_contributes_its_point(monkeypatch, pipes):
    """Cell и: the frame under a `mount` node is the mount's POINT (anchor composed with the
    node's own xy/rotation), never its anchor — the same frame the Read uses."""
    frames = MountFrames(anchor_pos=_nm(10.0, 20.0), anchor_rot=0.0,
                         point_pos=_nm(50.0, 60.0), point_rot=0.0)
    monkeypatch.setattr(base_pose_mod, "_mount_anchor_pose",
                        lambda *a, **k: frames)
    tree = Tree(name="mounted", anchor=TreeAnchor(is_origin=True), nodes=[])
    mount = TreeNode(ref="M1", kind="mount", xy=(40.0, 40.0), polar=None,
                     rotation=0.0, name=None, group=None, children=[])
    node = TreeNode(ref=CHILD, kind="placement", xy=(1.0, 2.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])

    results, _warnings = run_single_node_redraw_worker({
        # `cfg` is only carried through: the mount branch is the patched seam above.
        "ref": CHILD, "cfg": object(), "tree": tree, "node": node, "parent_node": mount,
        "adapter": object(), "sheet_names": {},
    })

    assert results == [(CHILD, True, None)]
    assert _override(pipes).position == _nm(51.0, 62.0)


# ── к: the module and SUBTREE_KINDS branches are untouched ─────────────────

def test_k_module_and_subtree_nodes_keep_their_own_branches(main_window, tmp_path,
                                                           monkeypatch, pipes):
    """Cell к: a `module` node still goes to the forest-content worker (never to the
    single-node one), and a SUBTREE_KINDS node (`external`) is still handled by
    `trees_accept.redraw_subtree` — neither ever reaches the live-base resolver."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)

    module_node = _tree(dock, "main").nodes[0]
    captured = _redraw(dock, module_node, monkeypatch)
    assert captured["worker"] is cascade_mod.run_curated_forest_redraw_worker
    assert captured["payload"]["selected_refs"] == {"switch_a"}

    external = TreeNode(ref="EXT", kind="external", xy=None, polar=None, rotation=0.0,
                        name=None, group=None, children=[
                            TreeNode(ref="C1", kind="placement", xy=(1.0, 1.0),
                                     polar=None, rotation=0.0, name=None, group=None,
                                     children=[])])
    _tree(dock, "solo").nodes.append(external)
    captured = _redraw(dock, external, monkeypatch)
    assert captured["worker"] is not run_single_node_redraw_worker
    assert "node" not in captured["payload"]
    assert pipes == []


# ── Claude's §2: the three holes the first acceptance found ────────────────

def test_a_polar_node_is_placed_from_its_polar_offset(main_window, tmp_path, monkeypatch,
                                                      pipes):
    """Claude's §2, hole 1: `node_offset_of` has an `xy` branch and a `polar` one, and only the
    first was ever exercised — a mutant that reads `xy` alone (a polar node has none) stayed
    green. Here the child STORES `polar`: the same live geometry as cell а, expressed as a
    radius and an angle, and the Redraw must land on the same board point."""
    import math

    data = {**TREES, "trees": [dict(t) for t in TREES["trees"]]}
    for tree in data["trees"]:
        if tree["name"] != "switch_a":
            continue
        root = dict(tree["nodes"][0])
        root["children"] = [{"ref": CHILD, "kind": "placement",
                             "polar": [math.hypot(70.0, 8.0),
                                       math.degrees(math.atan2(8.0, 70.0))]}]
        tree["nodes"] = [root]
    dock, _root = _dock(main_window, tmp_path, data)
    _patch_live(monkeypatch, {PARENT: (100.0, 100.0, 0.0), CHILD: (170.0, 108.0, 0.0)})
    node = _tree(dock, "switch_a").nodes[0].children[0]
    assert node.xy is None and node.polar is not None

    captured = _redraw(dock, node, monkeypatch)
    run_single_node_redraw_worker(captured["payload"])

    assert _override(pipes).position == _nm(170.0, 108.0), (
        "a polar node must be placed from its polar offset, not from a missing xy")


def test_the_dock_hands_the_worker_its_sheet_names(main_window, tmp_path, monkeypatch,
                                                   pipes):
    """Claude's §2, hole 2: the payload carries a COPY of `ctx.sheet_names`, and no cell read it
    — a mutant that sends `{}` stayed green. A role spread over two sheets (HP-Power) cannot
    resolve without them, so the copy is pinned at the door."""
    from types import SimpleNamespace

    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    names = {"uuid-1": "Channel_1", "uuid-2": "Channel_2"}
    dock._ctx = SimpleNamespace(sheet_names=names)
    node = _child(dock)[1]

    captured = _redraw(dock, node, monkeypatch)

    assert captured["payload"]["sheet_names"] == names
    assert captured["payload"]["sheet_names"] is not names, (
        "a copy must cross the thread boundary, not the dock's live mapping")


def test_a_payload_naming_a_node_without_an_adapter_is_refused(main_window, tmp_path,
                                                               monkeypatch, pipes):
    """Claude's §2, hole 3: the refusal for «a node but no tree/config/adapter» had no cell —
    removing it stayed green. The board read cannot happen without an adapter, so the honest
    answer is a Log line and NO pipeline, never a layout guessed from the tree."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    tree = _tree(dock, "switch_a")
    node = tree.nodes[0].children[0]

    results, _warnings = run_single_node_redraw_worker({
        "ref": CHILD, "cfg": dock._cfg, "tree": tree, "node": node,
        "parent_node": tree.nodes[0], "sheet_names": {},
        # …and NO "adapter".
    })

    assert pipes == []
    assert results[0][1] is False
    assert "adapter" in results[0][2]


# ── §3: a module-placed tree is not redrawn from its own tab ───────────────

def _two_embedded_trees():
    """TREES plus a SECOND tree a module places (`switch_b`), so a cell can prove the refusal
    is about the MODULE MARKER and not about one particular tree."""
    data = {**TREES, "trees": [dict(t) for t in TREES["trees"]]}
    data["trees"].append(
        {"name": "switch_b", "anchor": {"origin": True},
         "nodes": [{"ref": "driver_sw_b", "kind": "placement", "xy": [0.0, 0.0]}]})
    data["entities"] = TREES["entities"] + [
        {"name": "driver_sw_b", "cluster": "driver_sw_b", "cell": "cell_sw"}]
    for tree in data["trees"]:
        if tree["name"] == "main":
            tree["nodes"] = list(tree["nodes"]) + [
                {"ref": "switch_b", "kind": "module", "xy": [-40.0, 20.0]}]
    return data


def test_a_whole_tree_redraw_of_an_embedded_tree_is_refused_from_both_doors(
        main_window, tmp_path, monkeypatch, caplog):
    """Cell а: «Redraw whole tree» on the tab of a MODULE-placed tree starts NOTHING and says
    which module to use — from Tools (no tree passed) and from the root menu (the tree passed).
    Cell б rides in the same cell: the refusal follows the MARKER, so a second embedded tree is
    refused the same way."""
    dock, _root = _dock(main_window, tmp_path, _two_embedded_trees())
    _patch_live(monkeypatch)
    embedded = _tree(dock, "switch_a")
    second = _tree(dock, "switch_b")
    dock._switch_to_tree("switch_a")      # the Tools menu acts on the ACTIVE tab

    with caplog.at_level("ERROR"):
        from_tools = _redraw_whole_tree(dock, None, monkeypatch)
        from_menu = _redraw_whole_tree(dock, embedded, monkeypatch)
        other = _redraw_whole_tree(dock, second, monkeypatch)

    assert from_tools == from_menu == other == [], "no worker may start for a refused tree"
    text = "\n".join(record.getMessage() for record in caplog.records)
    for name in ("switch_a", "switch_b", "main"):
        assert name in text
    assert "module" in text and "not from this tab" in text


def test_a_whole_tree_redraw_of_a_plain_tree_still_starts(main_window, tmp_path,
                                                          monkeypatch):
    """Cell в: `main` places other trees and is placed by nobody — «Redraw whole tree» there
    runs the forest worker exactly as before, and says so (True)."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)

    starts = _redraw_whole_tree(dock, _tree(dock, "main"), monkeypatch)

    assert len(starts) == 1
    assert starts[0]["worker"] is cascade_mod.run_curated_forest_redraw_worker


def test_redraw_selected_on_an_embedded_tree_is_refused(main_window, tmp_path, monkeypatch,
                                                        caplog):
    """Cells г and д — and the measurement that made д a refusal too.

    г: the TOP node of a module-placed tree is re-projected onto the tree's anchor (the Grid
    Origin): on the live board `driver_sw_a` → (72.4540, 139.9041) while it stands at
    (104.4600, 104.3900), i.e. (−32.006, +35.514) mm.

    д: a `placement` PARENT's base comes from the TREE, so even a CHILD-ONLY selection drifts by
    the same (−32.000, +35.000) mm (`conn_out_sw_a` alone). The plan expected children to be
    safe; the numbers say otherwise, and the refusal covers the whole run."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    dock._switch_to_tree("switch_a")

    with caplog.at_level("ERROR"):
        top = _redraw_selected(dock, {"driver_sw_a"}, monkeypatch)
        child = _redraw_selected(dock, {"conn_out_sw_a"}, monkeypatch)

    assert top == child == [], "neither selection may start a worker"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert text.count("switch_a") >= 2 and "main" in text


# ── §4.1: the FOURTH door — a subtree inside an embedded tree ───────────────

def _mount_subtree_cfg():
    """TREES where `switch_a` carries a `mount` node WITH a child — the shape whose node
    Redraw is a curated SUBTREE run (`trees_accept.redraw_subtree`), not a single-node one."""
    data = {**TREES, "trees": [dict(t) for t in TREES["trees"]]}
    for tree in data["trees"]:
        if tree["name"] != "switch_a":
            continue
        tree["nodes"] = [{
            "ref": PARENT, "kind": "placement", "xy": [0.0, 0.0],
            "children": [{"ref": "mnt_a", "kind": "mount",
                          "anchor": {"role": "SW_ROLE"},
                          "children": [{"ref": CHILD, "kind": "placement",
                                        "xy": [1.0, 2.0]}]}]}]
    return data


def test_a_mount_subtree_on_an_embedded_tree_is_refused(main_window, tmp_path,
                                                        monkeypatch, caplog):
    """§4.1 — the fourth door Denis found live (8 such nodes in `switch_a`).

    A `mount` node's Redraw is the curated redraw of its OWN subtree, and that path reached
    the worker from an embedded tree's tab as well: every child was laid out from the tree's
    anchor, i.e. the Grid Origin, and drifted by the module's (−32.000, +35.000) mm. Now the
    dock REFUSES it (handled = False), starts nothing and names the node.

    The control rides in the same cell: the very same shape on the PLAIN tree `solo` still
    starts the curated worker — the refusal is about the module marker, not about `mount`.
    """
    _patch_live(monkeypatch, {PARENT: (104.46, 104.39, 0.0), CHILD: (105.46, 106.39, 0.0)})
    dock, _root = _dock(main_window, tmp_path, _mount_subtree_cfg())
    mount = _tree(dock, "switch_a").nodes[0].children[0]

    with caplog.at_level("ERROR"):
        starts = _capture_starts(monkeypatch)
        handled = td_ac.redraw_node(dock, mount)

    assert handled is False, "the refusal is HANDLED here — True would mean a worker started"
    assert starts == [], "the fourth door may not start the curated worker on an embedded tree"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "mnt_a" in text and "switch_a" in text and "main" in text

    plain = TreeNode(ref="mnt_s", kind="mount", xy=None, polar=None, rotation=0.0,
                     name=None, group=None, anchor=TreeAnchor(role="SW_ROLE"),
                     children=[TreeNode(ref="C_S", kind="placement", xy=(1.0, 2.0),
                                        polar=None, rotation=0.0, name=None, group=None,
                                        children=[])])
    _tree(dock, "solo").nodes.append(plain)
    starts = _capture_starts(monkeypatch)
    assert td_ac.redraw_node(dock, plain) is True
    assert len(starts) == 1, "a `mount` subtree on a plain tree must still be redrawn"


# ── §4.3: the reverse scan itself ───────────────────────────────────────────

def _nested_module_cfg():
    """TREES with `main`'s module marker NESTED under one of its own nodes."""
    data = {**TREES, "trees": [dict(t) for t in TREES["trees"]]}
    data["entities"] = TREES["entities"] + [
        {"name": "grp_sw", "cluster": "grp_sw", "cell": "cell_sw"}]
    for tree in data["trees"]:
        if tree["name"] == "main":
            tree["nodes"] = [{"ref": "grp_sw", "kind": "placement", "xy": [0.0, 0.0],
                              "children": [{"ref": "switch_a", "kind": "module",
                                            "xy": [32.0, -35.0]}]}]
    return data


def test_a_module_nested_under_another_node_still_places_its_tree(
        main_window, tmp_path, monkeypatch, caplog):
    """§4.3: `module_nodes` walks the WHOLE owner tree, not only its top level. The marker
    here hangs under `main`'s own `grp_sw` node — a scan of `tree.nodes` alone would let
    `switch_a` look unembedded and start a redraw from the Grid Origin."""
    dock, _root = _dock(main_window, tmp_path, _nested_module_cfg())
    _patch_live(monkeypatch)
    embedded = _tree(dock, "switch_a")

    assert dock._embedded_in(embedded) == ["main"]

    with caplog.at_level("ERROR"):
        starts = _redraw_whole_tree(dock, embedded, monkeypatch)

    assert starts == [], "a marker under another node still places the tree"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "main" in text and "module" in text


def test_the_owner_tree_need_not_be_first_in_the_list(main_window, tmp_path,
                                                      monkeypatch, caplog):
    """§4.3: the reverse scan looks at EVERY other tree. With `switch_a` FIRST and its owner
    `main` LAST, a scan that stopped at the first tree would find no marker and redraw the
    embedded tree from its own anchor."""
    data = {**TREES, "trees": [dict(t) for t in TREES["trees"]]}
    order = {"switch_a": 0, "solo": 1, "main": 2}
    data["trees"] = sorted(data["trees"], key=lambda t: order[t["name"]])
    dock, _root = _dock(main_window, tmp_path, data)
    _patch_live(monkeypatch)

    assert [t.name for t in dock._trees] == ["switch_a", "solo", "main"]

    with caplog.at_level("ERROR"):
        starts = _redraw_whole_tree(dock, _tree(dock, "switch_a"), monkeypatch)

    assert starts == []
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "main" in text and "module" in text


# ── §4.3: the two FORMS must say what the dock did ─────────────────────────

def test_the_anchor_form_says_refused_and_started(main_window, tmp_path, monkeypatch,
                                                  caplog):
    """§4.3 — the THIRD door of §3, and the one cell that was missing: the anchor form's
    Apply. It goes through `trees_accept.redraw_form`, which asks `_on_redraw_whole_tree`
    about the ACTIVE tab — on `switch_a` the answer is a REFUSAL and the form must say so,
    on `main` a worker starts and the form says THAT."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)

    dock._switch_to_tree("switch_a")
    refused = dock._build_anchor_form(_tree(dock, "switch_a"))
    with caplog.at_level("ERROR"):
        starts = _capture_starts(monkeypatch)
        td_ac.redraw_form(dock, refused)
    assert starts == [], "the embedded tree's anchor form may not start a worker"
    assert "refused" in refused.apply_status_label.text()

    dock._switch_to_tree("main")
    started = dock._build_anchor_form(_tree(dock, "main"))
    starts = _capture_starts(monkeypatch)
    td_ac.redraw_form(dock, started)
    assert len(starts) == 1, "`main` is placed by nobody — its anchor form redraws it"
    assert "started" in started.apply_status_label.text()


def test_the_node_form_says_refused_and_started(main_window, tmp_path, monkeypatch,
                                                caplog):
    """§4.2 — the NODE form used to write «Redraw started» no matter what the dock answered.
    A `mount` with a child on an embedded tree is refused (the reason is in the Log, so the
    form must not claim a start); the same shape on `solo` really does start."""
    _patch_live(monkeypatch, {PARENT: (104.46, 104.39, 0.0), CHILD: (105.46, 106.39, 0.0)})
    dock, _root = _dock(main_window, tmp_path, _mount_subtree_cfg())
    tree = _tree(dock, "switch_a")
    form = dock._build_node_form(tree, tree.nodes[0].children[0])

    with caplog.at_level("ERROR"):
        starts = _capture_starts(monkeypatch)
        td_ac.redraw_form(dock, form)
    assert starts == []
    assert "refused" in form.apply_status_label.text()

    plain = TreeNode(ref="mnt_s2", kind="mount", xy=None, polar=None, rotation=0.0,
                     name=None, group=None, anchor=TreeAnchor(role="SW_ROLE"),
                     children=[TreeNode(ref="C_S2", kind="placement", xy=(1.0, 2.0),
                                        polar=None, rotation=0.0, name=None, group=None,
                                        children=[])])
    solo = _tree(dock, "solo")
    solo.nodes.append(plain)
    started = dock._build_node_form(solo, plain)
    starts = _capture_starts(monkeypatch)
    td_ac.redraw_form(dock, started)
    assert len(starts) == 1
    assert "started" in started.apply_status_label.text()


# ── §4.3: «Redraw selected» follows the tree it is GIVEN ───────────────────

def test_redraw_selected_uses_the_tree_it_is_given_not_the_active_tab(
        main_window, tmp_path, monkeypatch, caplog):
    """§4.3, both directions. The tree ROOT's context menu passes its OWN tree while another
    tab is in front: the REFUSAL must follow the given tree (`switch_a`'s menu, `main`
    active) and the RUN must follow it too (`main`'s menu, `switch_a` active). A dock that
    read `_current_tree()` instead would get both cases exactly backwards."""
    dock, _root = _dock(main_window, tmp_path)
    _patch_live(monkeypatch)
    embedded = _tree(dock, "switch_a")
    main = _tree(dock, "main")

    dock._switch_to_tree("main")
    with caplog.at_level("ERROR"):
        refused = _redraw_selected(dock, {"switch_a"}, monkeypatch, tree=embedded)
    assert refused == [], "the GIVEN tree is embedded — the run must be refused"

    dock._switch_to_tree("switch_a")
    started = _redraw_selected(dock, {PARENT}, monkeypatch, tree=main)
    assert len(started) == 1, "the GIVEN tree is `main` — the run must start"
    assert started[0]["payload"]["tree_name"] == "main"
