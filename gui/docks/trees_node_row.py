# gui/docks/trees_node_row.py
"""The ONE post-Apply update of a tree node's ROW (and the node-copy routine it
shares with the dock).

`NodeFormWidget.apply()` mutates the edited TreeNode IN PLACE. The tree's
QTreeWidget ROW, however, is a separate object registered in
``TreesDock._node_items`` UNDER THE NODE'S REF — so a Ref edit used to leave the
row keyed by the OLD ref, its text stale until the whole GUI was restarted
(Denis, 08.10.2026; plan techdocs/handoff/deepseek/plan/2026_10_08_tree_node_ref_
apply.md). This module carries the fix: after a successful Apply the SAME
QTreeWidgetItem is re-registered under the new ref and repainted through the
render's OWN routine (``TreesDock._refresh_tree_marks``, which calls
``_node_item_text`` / ``_apply_node_marks``) — never a second computation, and
never a full ``_rebuild_tabs()`` (that would tear the open form down).

It lives OUTSIDE trees_dock.py on purpose: that file is a giant (7955 lines) and
may only SHRINK. `copy_node_onto` moved here together with the fix, so the dock
loses lines rather than gaining them.
"""
from __future__ import annotations

from kicadstamp.trees import TreeNode


def copy_node_onto(target: TreeNode, built: TreeNode) -> None:
    """Copy every editable field of a BUILT node onto an EXISTING node in place
    (mutate, don't swap identity — other structures may hold a reference, e.g.
    _node_items). The single copy routine shared by every node-edit apply path
    (the master-detail Node tab's apply()/redraw() and the dialog forms), so
    they can never drift.

    Moved verbatim from trees_dock._copy_node_onto (2026-10-08): the row update
    below belongs to this copy, so both live together and the giant shrinks."""
    target.ref = built.ref
    target.kind = built.kind
    target.xy = built.xy
    target.polar = built.polar
    target.rotation = built.rotation
    target.name = built.name
    target.group = built.group
    # NOTE (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.3): the
    # pivot_xy/pivot_polar copy that used to sit here is GONE — a node carries no
    # pivot any more (the inner point belongs to the TREE). The tree-level
    # editor arrives in stage Б2.1.
    target.anchor = built.anchor


def refresh_node_row(dock, tree, node: TreeNode, old_ref: str) -> None:
    """Re-key and repaint ONE row after an in-place node edit.

    `node` is the edited TreeNode (already carrying its NEW ref), `old_ref` its
    ref BEFORE the edit. The SAME QTreeWidgetItem moves from
    `dock._node_items[old_ref]` to `dock._node_items[node.ref]` — a dict move, so
    the widget item itself, its selection and its expansion state all survive and
    no item is created — and is then repainted by `dock._refresh_tree_marks(tree)`,
    the render's OWN routine. The dock's master-detail identity
    (`_current_node_ref`) follows the node, so re-selecting the row keeps the
    SAME (still open) form instead of tearing it down.

    A no-op when the dock carries no registry row for `old_ref` — a headless or
    standalone form with no built tree widget (the modal dialog's Edit mode in a
    test, a create-tree form), where there is no row to update."""
    items = getattr(dock, "_node_items", None)
    if items is None:
        return
    item = items.pop(old_ref, None)
    if item is None:
        return
    items[node.ref] = item
    if tree is not None:
        # The render's own text/marks routine — the row is repainted from the
        # node the form just committed, never by a second calculation here.
        refresh = getattr(dock, "_refresh_tree_marks", None)
        if refresh is not None:
            refresh(tree)
    if getattr(dock, "_current_node_ref", None) == old_ref:
        dock._current_node_ref = node.ref
