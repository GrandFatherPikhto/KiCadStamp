# tests/gui/docks/test_mount_and_record_same_name.py
"""plan_2026_11_14_mount_and_record_may_share_a_name — Ч0-1 and cells а–з.

Ч0-1 was a PROBE (kept as cell г): `TreesDock._node_items` used to be keyed by
the node's REF and spanned the WHOLE dock, not one tree, so two trees carrying
one name wrote the SAME key and the row rendered LAST survived. The three `г`
cells below state what must hold — and they measured 2 keys instead of 3, an
empty `selected_refs` for a ticked mount, and an «Edit node…» that selected the
tree's ANCHOR row.

A mount and a positioned node are different things (kind + uuid), so they may
carry one name in ONE tree since 16.11.2026: cells а, б, в, д, е and з pin that,
while the loader keeps refusing two MOUNTS of one name (д).
"""
import pytest
from PyQt6.QtCore import Qt

from gui.docks.trees_accept import subtree_refs
from gui.docks.trees_dock import TreesDock, collect_tree_refs
from gui.docks.trees_node_row import checked_record_refs
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import (Tree, TreeAnchor, TreeNode, tree_from_dict,
                              tree_to_dict)
from tests.gui.conftest import row_of_ref

CFG = {
    "cells": {"cell_base": {}},
    "entities": [{"name": "X", "cluster": "c", "cell": "cell_base"}],
    "trees": [
        {"name": "t1", "anchor": {"origin": True}, "nodes": []},
        {"name": "t2", "anchor": {"origin": True}, "nodes": []},
    ],
}


def _dock(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(CFG, format_number=2), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None
    return dock, root


def _node(ref, kind, children=(), anchor=None):
    return TreeNode(ref=ref, kind=kind, xy=(1.0, 1.0), polar=None,
                    rotation=0.0, name=None, group=None, children=list(children),
                    anchor=anchor)


def _two_trees_one_name(dock):
    """t1: mount `X` with the positioned child `CH` under it.
    t2: positioned node `X`. The mount and the t2 node carry the SAME name."""
    mount = _node("X", "mount", children=[_node("CH", "external")],
                  anchor=TreeAnchor(role="R1"))
    entity = _node("X", "external")
    dock._trees[0].nodes.append(mount)
    dock._trees[1].nodes.append(entity)
    dock._rebuild_tabs()
    return mount, entity


def _row(tree_widget, node):
    """The QTreeWidgetItem whose UserRole is `node` (the render puts the TreeNode
    itself there — `_render_node`)."""
    stack = [tree_widget.invisibleRootItem()]
    while stack:
        item = stack.pop()
        if item.data(0, Qt.ItemDataRole.UserRole) is node:
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    raise AssertionError(f"no row for node {node.ref!r}")


# ── Ч0-1 / cell г1: ONE key per NODE, not one per name ────────────────────

def test_g1_two_trees_one_name_give_one_key_per_node(
        main_window, tmp_path):
    """Three node rows live in the dock (mount `X`, its child `CH`, the t2 node
    `X`), so the map must hold THREE entries. Keyed by REF it holds two: the
    mount's row and the t2 row overwrite each other under "X", and the mount's
    row is unreachable from the map for the rest of the session."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _two_trees_one_name(dock)

    assert len(dock._node_items) == 3
    # ...and each entry is the row of ITS node, in ITS tree's widget
    assert _row(dock._tree_widget_for(dock._trees[0]), mount) is not None
    assert _row(dock._tree_widget_for(dock._trees[1]), entity) is not None


# ── Ч0-1 / cell г2: ticking the mount reaches the redraw ──────────────────

def test_g2_a_checked_mount_gives_the_records_of_its_subtree(
        main_window, tmp_path, monkeypatch):
    """The user ticks the MOUNT row of t1 and presses "Redraw selected". What
    must reach the planner is the mount's SUBTREE records (`CH`) — the mount's
    own name is not a record and must not appear. With the ref-keyed map the
    tick is lost entirely: `_on_redraw_selected` reads the check state of the
    row the key points at, which is t2's unchecked node."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _two_trees_one_name(dock)
    _row(dock._tree_widget_for(dock._trees[0]), mount).setCheckState(
        0, Qt.CheckState.Checked)

    captured = {}
    monkeypatch.setattr(
        dock, "_run_curated_redraw",
        lambda refs, trigger=None, tree=None: captured.update(refs=refs))

    dock._on_redraw_selected()

    assert captured["refs"] == {"CH"}


def test_g2b_a_checked_positioned_node_of_the_other_tree_still_reaches_it(
        main_window, tmp_path, monkeypatch):
    """The other half of the same question: ticking the t2 node `X` must give
    exactly `X` — this one happens to work today (its row is the surviving key),
    and it must keep working after the fix."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _two_trees_one_name(dock)
    _row(dock._tree_widget_for(dock._trees[1]), entity).setCheckState(
        0, Qt.CheckState.Checked)

    captured = {}
    monkeypatch.setattr(
        dock, "_run_curated_redraw",
        lambda refs, trigger=None, tree=None: captured.update(refs=refs))

    dock._on_redraw_selected()

    assert captured["refs"] == {"X"}


# ── Ч0-1 / cell г3: "Edit node…" selects the row the user asked about ─────

def test_g3_editing_the_mount_selects_its_own_row(
        main_window, tmp_path):
    """`_edit_in_panel` collapses the selection onto the node's row through
    `select_only(widget_of_THIS_tree, _node_items.get(node.ref))`. With the
    ref-keyed map that item belongs to the OTHER tree's widget, so the call
    selects nothing at all — the t1 widget ends up with no selection."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _two_trees_one_name(dock)
    widget = dock._tree_widget_for(dock._trees[0])
    mount_row = _row(widget, mount)

    dock._edit_in_panel(dock._trees[0], mount)

    selected = widget.selectedItems()
    assert selected == [mount_row], (
        f"the t1 widget selected {[item.text(0) for item in selected]!r} "
        f"instead of the mount's own row {mount_row.text(0)!r}")


# ── cells а–з of the plan: the pair inside ONE tree ───────────────────────

def _one_tree_pair(dock):
    """t1: mount `X` whose child IS the positioned node `X` — the pair inside
    ONE tree, the shape the lifted ban used to refuse."""
    mount = _node("X", "mount", children=[_node("X", "external")],
                  anchor=TreeAnchor(role="R1"))
    dock._trees[0].nodes.append(mount)
    dock._rebuild_tabs()
    return mount, mount.children[0]


def test_a_the_pair_in_one_tree_loads_both_parsers(main_window, tmp_path):
    """Cell а: a mount `X` and the positioned `X` under it in ONE tree. The dict
    parser takes it, and a whole ROOT CONFIG carrying the pair is read by
    `load_config` — the file the GUI writes is a file the loader reads."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _one_tree_pair(dock)
    assert (mount.kind, entity.kind) == ("mount", "external")

    tree_from_dict(tree_to_dict(dock._trees[0]))      # the dict parser

    cfg = dict(CFG)
    cfg["trees"] = [
        {"name": "t1", "anchor": {"origin": True}, "nodes": [
            {"ref": "X", "kind": "mount", "anchor": {"role": "R1"},
             "children": [{"ref": "X", "kind": "external", "xy": [1.0, 1.0]}]}]},
    ]
    path = tmp_path / "pair.sexp"
    path.write_text(dict_to_sexp(cfg, format_number=2), encoding="utf-8")
    loaded = load_config(str(path))[0]                # both halves, on disk
    assert [node.ref for node in loaded.trees[0].nodes] == ["X"]
    assert [child.ref for child in loaded.trees[0].nodes[0].children] == ["X"]


def test_b_a_checked_mount_gives_the_record_under_it(main_window, tmp_path,
                                                     monkeypatch):
    """Cell б: the tick on the MOUNT hands in the record `X` of its subtree —
    exactly one name, and it is the CHILD's.

    The set alone cannot tell the two apart here (the mount and its child carry
    the SAME name), so the lock on "the mount's name never enters the set" lives
    in г2 (a mount `X` with a child `CH` → `{"CH"}`) and in з (a mount whose
    subtree names no record → `set()`). This cell is the shape from the plan."""
    dock, _root = _dock(main_window, tmp_path)
    mount, _entity = _one_tree_pair(dock)
    _row(dock._tree_widget_for(dock._trees[0]), mount).setCheckState(
        0, Qt.CheckState.Checked)

    assert subtree_refs(mount) == {"X"}
    captured = {}
    monkeypatch.setattr(dock, "_run_curated_redraw",
                        lambda refs, trigger=None, tree=None:
                        captured.update(refs=refs))
    dock._on_redraw_selected()
    assert captured["refs"] == {"X"}


def test_v_the_two_rows_stay_apart_when_only_the_record_is_ticked(
        main_window, tmp_path):
    """Cell в: the mount did not «eat» the record's row — two rows, two nodes,
    two check states."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _one_tree_pair(dock)
    widget = dock._tree_widget_for(dock._trees[0])
    mount_row, entity_row = _row(widget, mount), _row(widget, entity)
    assert mount_row is not entity_row

    entity_row.setCheckState(0, Qt.CheckState.Checked)
    assert mount_row.checkState(0) == Qt.CheckState.Unchecked
    assert checked_record_refs(dock) == {"X"}


def test_d_two_mounts_of_one_name_are_still_refused():
    """Cell д: the uniqueness of mounts among THEMSELVES is untouched."""
    tree = Tree(name="t1", anchor=TreeAnchor(is_origin=True), nodes=[
        _node("X", "mount", anchor=TreeAnchor(role="R1")),
        _node("X", "mount", anchor=TreeAnchor(role="R2")),
    ])
    with pytest.raises(ValidationError, match="not unique"):
        tree_from_dict(tree_to_dict(tree))


def test_e_a_mount_rename_keeps_both_rows(main_window, tmp_path):
    """Cell е: the mount `X` renamed to `Y` in place — the record's row keeps
    `X`, the mount's row follows its node, and both ticks survive."""
    dock, _root = _dock(main_window, tmp_path)
    mount, entity = _one_tree_pair(dock)
    widget = dock._tree_widget_for(dock._trees[0])
    mount_row, entity_row = _row(widget, mount), _row(widget, entity)
    mount_row.setCheckState(0, Qt.CheckState.Checked)
    entity_row.setCheckState(0, Qt.CheckState.Checked)

    mount.ref = "Y"
    dock._refresh_tree_marks(dock._trees[0])

    assert row_of_ref(dock, "Y") is mount_row
    assert row_of_ref(dock, "X") is entity_row
    assert "Y (Mount)" in mount_row.text(0)
    tree_from_dict(tree_to_dict(dock._trees[0]))


def test_z_redraw_sets_carry_no_local_names(main_window, tmp_path):
    """Cell з: both redraw sets are RECORDS only. «Redraw whole tree» lists the
    shared name ONCE (the child's record), and a mount whose subtree names no
    record hands in an empty set — «nothing to redraw» now means records."""
    dock, _root = _dock(main_window, tmp_path)
    mount, _entity = _one_tree_pair(dock)

    assert collect_tree_refs(dock._trees[0]) == ["X"]
    assert subtree_refs(mount) == {"X"}
    bare = _node("M2", "mount", anchor=TreeAnchor(role="R3"))
    assert subtree_refs(bare) == set()
