# tests/gui/docks/test_trees_dock_group_move.py
"""Сторожа Ч1 п.2/п.3/п.6/п.7/п.8 of plan_2026_10_14_tree_group_move_and_mount_
rebase: the group "Move to…" of the trees dock — several selected nodes, ONE new
parent, nothing moving on the board, all or nothing.

This file owns its own fixture (a mount tree loaded from a written config), so the
cells select ROWS of a real dock the way a user does, and the structural half runs
through the real `_reparent_nodes`.

WHAT IS PINNED DOWN HERE:
  * a group of two record nodes under a new mount: every node's ABSOLUTE board
    place is the same before and after (the numbers change, the place does not);
  * the group comes back in DOCUMENT order under the new parent (Ч1 п.7);
  * one node that can neither be recalculated nor read cancels the WHOLE move:
    the tree is byte-for-byte what it was and the Log names the node (Ч1 п.6);
  * picking the parent the group already hangs under does nothing at all;
  * ONE selected node still goes through the single-node flow, and an ancestor
    with its own descendant is that ONE node (Ч1 п.3 — the descendant rides);
  * a REFUSED structural half rolls the group's numbers back — every node stays
    byte for byte what it was (M5 of the Ч1 acceptance);
  * the GROUP'S old anchor gone from the board falls back to the live read (M7):
    the group still moves, its board places are preserved, and the Log names the
    source (offset read from the board) with the reason.

NOT covered here, named rather than implied: the busy-socket deferral and the
rebuild count of the group path (the single path's own cells in
tests/gui/test_board_door_offenders.py cover the shared refusal), and the copper
note (a copper node in the selection is dropped and named) — the pure half of both
is guarded in tests/gui/docks/test_trees_move_flow.py.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.trees import tree_to_sexp
from kicadstamp.utils.units import MM

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_dock import TreesDock
from gui.docks.trees_move_flow import _OFFSET_READ_FROM_BOARD

from tests.fakes.write_later import write_later


#: The base of every mount node in this fixture, in board mm — the numbers the
#: absolute-place assertions are built from.
MOUNT_X_MM = {"mnt_a": 10.0, "mnt_b": 30.0}


def _row_node(dock, ref):
    """The TreeNode a ROW carries — what the flow reads from the selection."""
    return dock._node_items[ref].data(0, Qt.ItemDataRole.UserRole)


def _dock_with(main_window, tmp_path):
    """A TreesDock whose tree is TWO mount nodes with two record nodes under the
    first one — the shape a group move is asked for."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({
        "trees": [{
            "name": "group_tree", "anchor": {"origin": True},
            "nodes": [
                {"ref": "mnt_a", "kind": "mount", "anchor": {"role": "IC1"},
                 "children": [
                     {"ref": "R_ONE", "kind": "external", "xy": [12.0, 0.0]},
                     {"ref": "R_TWO", "kind": "external", "xy": [14.0, 0.0]},
                 ]},
                {"ref": "mnt_b", "kind": "mount", "anchor": {"role": "IC2"}},
            ]}]}, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    # The tabs are rebuilt on every structural change; this fixture's page does not
    # need it, and the rebuild would replace the widgets the cells hold.
    dock._rebuild_tabs = lambda: None
    return dock


def _pose_stub(monkeypatch):
    """Every base resolve answers from MOUNT_X_MM (the tree's own anchor is 0):
    the arithmetic then has two real bases to work between, and no board is
    needed."""
    def _fake_base(cfg, adapter, sheet_names, tree, parent_node,
                   base_anchor=None, *args, **kwargs):
        ref = getattr(parent_node, "ref", None)
        return (Vector2.from_xy(int(round(MOUNT_X_MM.get(ref, 0.0) * MM)), 0),
                0.0, False)

    monkeypatch.setattr(trees_dock_mod, "_resolve_node_base_pose", _fake_base)


def _select_rows(dock, *refs):
    tree_widget = dock._current_tree_widget()
    tree_widget.clearSelection()
    for ref in refs:
        dock._node_items[ref].setSelected(True)
    return tree_widget


def _pick(monkeypatch, label):
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *a, **k: (label, True))


def _absolute_x_mm(node, parent_ref):
    """The node's BOARD x: its base's x plus its local offset — the number the
    plan's "ничего не двигается на плате" rule is about."""
    return MOUNT_X_MM[parent_ref] + node.xy[0]


# ── Ч1 п.2/п.7/п.8: the group itself ───────────────────────────────────────

def test_a_group_moves_under_a_new_mount_and_stays_on_the_board(
        main_window, tmp_path, monkeypatch):
    """The heart of Ч1: TWO selected nodes, ONE new parent, and neither of them
    moves by a thousandth of a millimetre — the stored numbers are re-expressed
    through the new base (10 + 12 = 22 = 30 + (-8)).

    Mutation check: write the numbers and forget the recalculation (re-hang with
    the stored xy) and this fails by 20 mm on both nodes."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _pick(monkeypatch, "mnt_b")
    _select_rows(dock, "R_ONE", "R_TWO")
    before = [_absolute_x_mm(node, "mnt_a") for node in tree.nodes[0].children]

    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert [n.ref for n in tree.nodes[1].children] == ["R_ONE", "R_TWO"]
    assert tree.nodes[0].children == [], "the old parent lost both"
    after = [_absolute_x_mm(node, "mnt_b") for node in tree.nodes[1].children]
    assert after == before, "every node stayed exactly where it was on the board"
    assert [n.xy for n in tree.nodes[1].children] == [(-8.0, 0.0), (-6.0, 0.0)]
    assert dock._dirty is True


def test_the_group_keeps_document_order_even_when_clicked_backwards(
        main_window, tmp_path, monkeypatch):
    """Ч1 п.7 — the moved nodes keep their MUTUAL document order under the new
    parent, whatever order the rows were clicked in (the redraw reads that order
    as its tie-breaker)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _pick(monkeypatch, "mnt_b")
    _select_rows(dock, "R_TWO", "R_ONE")     # backwards on purpose

    dock._move_nodes_flow(tree, _row_node(dock, "R_TWO"))

    assert [n.ref for n in tree.nodes[1].children] == ["R_ONE", "R_TWO"]


def test_one_node_that_cannot_be_held_cancels_the_whole_move(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч1 п.6 — all or nothing. Both nodes' bases are unresolvable AND unreadable,
    so nothing may move: the WHOLE TREE stays byte for byte as it was, no rebuild
    is triggered, and ONE Log line names the nodes and the reason. A partial move
    would be the worst outcome of the three (the user cannot see which half)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    before = tree_to_sexp(tree)
    rebuilt: list = []
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: rebuilt.append(True))

    def _unresolvable(*args, **kwargs):
        raise trees_dock_mod.ValidationError("no base, no live record")
    monkeypatch.setattr(trees_dock_mod, "_resolve_node_base_pose", _unresolvable)
    _pick(monkeypatch, "mnt_b")
    _select_rows(dock, "R_ONE", "R_TWO")

    caplog.clear()
    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert tree_to_sexp(tree) == before, "a refused group changed NOTHING"
    assert rebuilt == [], "…and no rebuild was triggered"
    told = [r.message for r in caplog.records]
    assert any("R_ONE" in m and "R_TWO" in m and "move cancelled" in m
               for m in told), f"one line must name both nodes — got {told!r}"


def test_picking_the_parent_the_group_already_hangs_under_does_nothing(
        main_window, tmp_path, monkeypatch):
    """A re-hang onto the CURRENT parent is not a re-hang: no numbers are touched,
    no rebuild, no dirty flag — the same no-op the single-node flow has."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    before = tree_to_sexp(tree)
    rebuilt: list = []
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: rebuilt.append(True))
    _pose_stub(monkeypatch)
    _pick(monkeypatch, "mnt_a")
    _select_rows(dock, "R_ONE", "R_TWO")

    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert tree_to_sexp(tree) == before
    assert rebuilt == [] and dock._dirty is False


# ── Ч1 п.3 + the single-node path: the routing ─────────────────────────────

def test_one_selected_node_goes_through_the_single_flow(
        main_window, tmp_path, monkeypatch):
    """ONE selected row is the single-node case, and the single flow owns its
    question ("re-hang WITHOUT recalculating?") — so the group path must hand the
    move over instead of re-implementing it (one picker, one question)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    single: list = []
    monkeypatch.setattr(dock, "_move_node_flow",
                        lambda t, node: single.append(node.ref))
    _select_rows(dock, "R_ONE")

    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert single == ["R_ONE"]


def test_an_ancestor_and_its_descendant_are_that_one_node(
        main_window, tmp_path, monkeypatch):
    """Ч1 п.3 — a selected descendant of a selected node is NOT moved separately:
    the ancestor is the group, and the descendant rides with it. With one root
    left, the flow is the single one again (the descendant is named nowhere)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    single: list = []
    monkeypatch.setattr(dock, "_move_node_flow",
                        lambda t, node: single.append(node.ref))
    _select_rows(dock, "mnt_a", "R_ONE")

    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert single == ["mnt_a"], "the ancestor is the group"


# ── ДОДЕЛКА п.2 (M5): a refused structural half rolls the numbers back ──────

def test_a_refused_structural_half_leaves_every_number_as_it_was(
        main_window, tmp_path, monkeypatch):
    """M5 of the Ч1 acceptance (доделка п.2): `apply_group_move` writes the
    planned offsets BEFORE the structural move so the rebuild renders the nodes
    in their new frame — and it must ROLL THEM BACK when the structural half
    refuses. Without the rollback the two nodes keep numbers measured for the
    NEW parent while still hanging under the old one, so they really jump on the
    board even though nothing moved in the tree.

    `dock._reparent_nodes` is the ONE structural half (Ч1 п.8); it refusing is
    the programming-error cover the candidate list makes unreachable, and a
    refused group must leave every node byte for byte as it was.

    Mutation check: drop the rollback loop in trees_move_flow.apply_group_move
    and the xy values below come back re-expressed (-8 / -6) under mnt_a."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _pick(monkeypatch, "mnt_b")
    monkeypatch.setattr(dock, "_reparent_nodes", lambda *a, **k: False)
    _select_rows(dock, "R_ONE", "R_TWO")
    before = tree_to_sexp(tree)
    numbers = [(n.xy, n.polar, n.rotation) for n in tree.nodes[0].children]

    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert [(n.xy, n.polar, n.rotation) for n in tree.nodes[0].children] == numbers, \
        "a refused group must roll EVERY node's numbers back"
    assert [n.ref for n in tree.nodes[0].children] == ["R_ONE", "R_TWO"]
    assert tree_to_sexp(tree) == before, "…and the tree is byte for byte as it was"


# ── ДОДЕЛКА п.3 (M7): the group's OLD anchor is gone from the board ─────────

def test_the_group_falls_back_to_the_live_read_when_the_old_anchor_is_gone(
        main_window, tmp_path, monkeypatch, caplog):
    """M7 of the Ч1 acceptance (доделка п.3) — the case the whole feature is for:
    the OLD mount's anchor is NOT on the board any more (the fake raises for it,
    answering for the NEW one). The recalculation is therefore impossible, so the
    source order falls back to the LIVE read — each node's CURRENT place read
    relative to the new parent — and the group still moves.

    The point of the cell: nothing is lost by that fallback. Every node's BOARD
    place is exactly where it was (the numbers change, the place does not), and
    the Log NAMES the source (`_OFFSET_READ_FROM_BOARD`) with the reason, because
    the board and the config may disagree and the source must be visible.

    Mutation check: drop the live-read leg from trees_move_flow.plan_rehang and
    the whole plan is refused — the group does not move at all."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    abs_x = {"R_ONE": MOUNT_X_MM["mnt_a"] + 12.0,
             "R_TWO": MOUNT_X_MM["mnt_a"] + 14.0}

    def _old_anchor_gone(cfg, adapter, sheet_names, tree_, parent_node,
                         base_anchor=None, *args, **kwargs):
        ref = getattr(parent_node, "ref", None)
        if ref == "mnt_a":
            raise trees_dock_mod.ValidationError(
                "the old mount's anchor is not on the board")
        return (Vector2.from_xy(int(round(MOUNT_X_MM.get(ref, 0.0) * MM)), 0),
                0.0, False)
    monkeypatch.setattr(trees_dock_mod, "_resolve_node_base_pose", _old_anchor_gone)

    def _read_live(cfg, adapter, sheet_names, tree_, new_parent, ref, kind,
                   component_address=None, *args, **kwargs):
        # The real read answers with the node's place against the NEW parent.
        return ((abs_x[ref] - MOUNT_X_MM["mnt_b"], 0.0), 0.0)
    monkeypatch.setattr(trees_dock_mod, "_resolve_live_offset", _read_live)

    _pick(monkeypatch, "mnt_b")
    _select_rows(dock, "R_ONE", "R_TWO")
    before = [_absolute_x_mm(node, "mnt_a") for node in tree.nodes[0].children]

    caplog.clear()
    dock._move_nodes_flow(tree, _row_node(dock, "R_ONE"))

    assert [n.ref for n in tree.nodes[1].children] == ["R_ONE", "R_TWO"], \
        "the group still moves even though the recalculation was impossible"
    assert [n.xy for n in tree.nodes[1].children] == [(-8.0, 0.0), (-6.0, 0.0)]
    after = [_absolute_x_mm(node, "mnt_b") for node in tree.nodes[1].children]
    assert after == before, "every node stayed exactly where it was on the board"

    told = [r.message for r in caplog.records]
    for ref in ("R_ONE", "R_TWO"):
        assert any(_OFFSET_READ_FROM_BOARD.format(
            node=ref, reason="the old mount's anchor is not on the board") == m
            for m in told), f"the Log must name the source for {ref} — got {told!r}"


# ── Ч2-9: the SINGLE path rolls its numbers back too ───────────────────────

def test_a_refused_structural_half_leaves_the_single_node_as_it_was(
        main_window, tmp_path, monkeypatch):
    """Ч2-9 of plan_2026_10_14_tree_group_move_and_mount_rebase — the cell the Ч1
    acceptance asked for: the mutation "no rollback on the SINGLE path" survived
    the whole of Ч1, because the hole was there BEFORE the task (the group half
    was guarded, the single half was not).

    `run_single_move` writes the re-expressed offset BEFORE `_reparent_node`, so
    the (immediate) rebuild renders the node in its new frame — and it must roll
    that back when the structural half refuses, or the node keeps numbers measured
    for a parent it does not hang under, i.e. it really jumps on the board while
    the tree did not change.

    `_reparent_node` refusing is the programming-error cover the candidate list
    makes unreachable. Mutation check: drop the `previous` restore in
    trees_move_flow.run_single_move and the xy below comes back re-expressed for
    mnt_b (−8) instead of the stored 12."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    before = tree_to_sexp(tree)
    monkeypatch.setattr(dock, "_rehang_offset_or_ask",
                        lambda *a, **k: (True, ((-8.0, 0.0), None, 0.0)))
    monkeypatch.setattr(dock, "_reparent_node", lambda *a, **k: False)
    _pick(monkeypatch, "mnt_b")
    _select_rows(dock, "R_ONE")
    node = _row_node(dock, "R_ONE")

    dock._move_nodes_flow(tree, node)

    assert (node.xy, node.polar, node.rotation) == ((12.0, 0.0), None, 0.0), \
        "a refused single move must leave the node byte for byte as it was"
    assert [n.ref for n in tree.nodes[0].children] == ["R_ONE", "R_TWO"]
    assert tree_to_sexp(tree) == before, "…and the tree is byte for byte as it was"
