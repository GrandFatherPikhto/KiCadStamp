# gui/docks/trees_selection.py
"""The SELECTION layer of the trees dock: which rows of a tree's QTreeWidget
are selected, what that selection is worth as a node list, and the two pages the
right-hand panel shows for it (plan_2026_10_14_tree_group_move_and_mount_rebase,
Ч1 п.1/п.9).

Why this module exists at all: `gui/docks/trees_dock.py` is a giant, and rule 45
says a giant only ever SHRINKS. The dock keeps the wiring (one line per call, one
line per signal) and this module keeps the bodies. Splitting it out of
`trees_move_flow.py` is deliberate: that module is the PURE half — a walk over a
`Tree` that needs no widgets — while everything here is a widget read
(`selectedItems()`, `setSelected`, the panel QWidgets). Two different jobs, so
two files (rule 45.3).

WHAT STAYS IN THE DOCK, and why: the dock's own seams keep the SAME names the
existing guards drive by module attribute (`_selected_real_nodes`,
`_on_selection_changed`), so a guard that patches or calls them still reaches the
one body. A name a guard only CALLS keeps a one-line seam in the dock; a name no
guard touches moves here whole.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QLabel, QTreeWidget, QTreeWidgetItem,
                             QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeNode


def on_selection_changed(dock) -> None:
    """Selection changed on the dock's active tree: refresh the right-hand panel
    and the status row it drives.

    The dock's Qt slot (`itemSelectionChanged`) is a one-line seam onto this
    body, so the connection in `_rebuild_tabs` never changes. `dock` supplies
    the widget lookup, the rebuild guard, the panel refresh and the status row;
    everything that reads the SELECTION itself lives here."""
    # The selection a REFUSED switch puts back emits this signal synchronously
    # (trees_accept.restore_selection) — that one must not re-fill the panel,
    # or the draft it is trying to keep would be dropped by the very call that
    # saved it.
    if getattr(dock, "_restoring_selection", False):
        return
    tree_widget = dock._current_tree_widget()
    if tree_widget is None:
        return
    # Master-detail (Z.3): reflect the current selection in the single
    # right-hand panel — a real node shows its editor, the root row / an
    # empty selection shows the tree anchor form. Skipped while
    # _rebuild_tabs is repopulating — its own final
    # _rebuild_active_form_panel() already covers the restored page.
    if not dock._rebuilding_tabs:
        # Ч1 of plan_2026_10_23_node_form_without_apply.md: a switch AWAY from
        # a touched form ACCEPTS it first. When the edit does not validate the
        # switch is REFUSED: the selection has been put back and the red line
        # names the node — so the preview of the clicked row must not be
        # written over it.
        dock._switch_refused = False
        dock._refresh_form_panel()
        if getattr(dock, "_switch_refused", False):
            return
    items = tree_widget.selectedItems()
    if not items:
        dock._show_status("")
        return
    nodes = real_nodes_of_items(items)
    if len(nodes) > 1:
        # Ч1 п.9: the offset preview of ONE node would be a lie about a
        # group, so a group shows the COUNT — and it is the same count the
        # right-hand panel shows instead of an editor, so the two never
        # disagree about what is selected.
        dock._show_status(_("{n} nodes selected").format(n=len(nodes)))
        return
    if not nodes:
        # NO real node is selected: the anchor pseudo-root, a navigation
        # pseudo-row (a str) or the "Copper" pseudo-node (Т1.2 — a fold over
        # copper rows, not data of its own) — nothing to preview, exactly as
        # before the group existed. Read off the REAL nodes rather than off
        # `items[0]`, because with ExtendedSelection the first selected row
        # may be a pseudo-row while real nodes are selected too — the panel
        # (its own reader) and this row must never describe one selection
        # differently.
        dock._show_status("")
        return
    dock._show_status(node_preview_text(nodes[0]))


def node_preview_text(node: TreeNode) -> str:
    """Human-readable static offset of the node, straight from the
    dataclass numbers (mm / deg) — NOT recomputed from the Vector2."""
    if node.xy is not None:
        return _("{ref}: xy=({x:.3f}, {y:.3f}) mm").format(
            ref=node.ref, x=node.xy[0], y=node.xy[1])
    if node.polar is not None:
        return _("{ref}: r={r:.3f} mm, angle={a:.3f}°").format(
            ref=node.ref, r=node.polar[0], a=node.polar[1])
    return _("{ref}: no offset").format(ref=node.ref)


def empty_selection_page() -> QWidget:
    """A blank right-hand panel page — what the "Copper" pseudo-node shows
    (Т1.2). The property is how _current_panel_node_ref recognises this page
    when the panel is READ BACK (a tab switch) instead of re-filled, so the
    empty panel survives like the panel's other contents do."""
    page = QWidget()
    page.setProperty("_copper_group_page", True)
    return page


def multi_selection_page(count: int) -> QWidget:
    """The right-hand panel page for a MULTI-node selection (Ч1 п.9): the
    count and nothing else — no editor, no Apply row. Recognised back from
    its own property, COUNT included, exactly like empty_selection_page, so
    a tab round-trip restores the same page and a changed count re-fills it."""
    page = QWidget()
    page.setProperty("_multi_selection_page", True)
    page.setProperty("_multi_selection_count", int(count))
    layout = QVBoxLayout(page)
    label = QLabel(_("{n} nodes selected").format(n=int(count)))
    label.setWordWrap(True)
    layout.addWidget(label)
    layout.addStretch(1)
    return page


def real_nodes_of_items(items) -> list[TreeNode]:
    """The REAL TreeNodes of a selection, in selection order — the ONE
    decoder of "which selected rows are editable nodes". Pseudo-roots (the
    anchor, "⇐ embedded in X", "→ instance: X") carry a str or nothing, and
    the "Copper" pseudo-node (Т1.2) carries a marker object, so neither is
    ever a member: `isinstance(data, TreeNode)` is the whole rule, and it is
    asked HERE rather than at each reader (Ч1 п.1 turned the selection into a
    LIST, so every reader would otherwise re-walk it)."""
    out: list[TreeNode] = []
    for item in items or ():
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, TreeNode):
            out.append(data)
    return out


def selected_real_nodes(dock, tree: Tree) -> list[TreeNode]:
    """EVERY selected real TreeNode of `tree`'s widget, in selection order —
    the multi-select reader the panel (Ч1 п.9) and the group operations read;
    the single-node callers keep selected_real_node below."""
    tree_widget = dock._tree_widget_for(tree)
    if tree_widget is None:
        return []
    return real_nodes_of_items(tree_widget.selectedItems())


def select_tree_node(dock, tree_name: str, node_ref: str) -> bool:
    """Open the tree `tree_name` and make its node `node_ref` the ONE selected row,
    expanding whatever hides it. True when the node was there.

    This is the body of the Checks page's «К ноде в дереве» (Ч2б-2 of
    plan_2026_10_23_checks_tab_top_level), and it lives HERE rather than on the dock for
    rule 45: `gui/docks/trees_dock.py` is a giant that only shrinks, so the door is a
    function of the selection layer that takes the dock. A selected row nobody can see is
    not a jump, hence the expansion; the selection itself goes through `select_only`, the
    ONE place a single node is selected (Ч1 п.1), so a group selection collapses.

    False when the tree is not loaded or has no such node — the caller says so instead of
    landing the operator on some other row and calling it a jump."""
    if not tree_name or not node_ref:
        return False
    dock._switch_to_tree(tree_name)
    tree = next((t for t in dock._trees if t.name == tree_name), None)
    widget = dock._tree_widget_for(tree) if tree is not None else None
    item = item_of_ref(widget, node_ref)
    if item is None:
        return False
    parent = item.parent()
    while parent is not None:
        parent.setExpanded(True)
        parent = parent.parent()
    select_only(widget, item)
    return True


def item_of_ref(tree_widget: Optional[QTreeWidget],
                ref: str) -> Optional[QTreeWidgetItem]:
    """The row of the node whose `ref` is `ref`, anywhere in `tree_widget` — the ONE
    lookup of "the row for this node name", shared by every entry point that is handed a
    node NAME rather than a node (Ч2б-2: a check report's `to_node` is exactly that).

    Only a real `TreeNode` can match: the pseudo-rows (the anchor, "⇐ embedded in X",
    "→ instance: X") carry a str or nothing and the "Copper" group carries a marker, so
    none of them can answer for a node. None when the tree is not loaded or has no such
    node — the caller says so instead of guessing."""
    if tree_widget is None or not ref:
        return None
    it = QTreeWidgetItemIterator(tree_widget)
    while it.value() is not None:
        item = it.value()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, TreeNode) and data.ref == ref:
            return item
        it += 1
    return None


def select_only(tree_widget: Optional[QTreeWidget],
                item: Optional[QTreeWidgetItem]) -> None:
    """Make `item` the ONLY selected row of `tree_widget` (Ч1 п.1) — the ONE
    place an entry point that means ONE node collapses a multi-selection:
    double-click, "Edit node…", "Set anchor…" and the identified-copper
    highlight. Without it a group selection would survive the action and the
    master-detail panel would keep showing "N nodes selected" for something
    the user asked about one row.

    The clear+select is stated EXPLICITLY rather than left to
    `setCurrentItem`'s own selection flag, which is Qt-internal and depends
    on the selection mode and on the modifier keys held at that moment.
    Measured 2026-10-14 (mutation run of
    tests/gui/docks/test_trees_dock_group_selection.py): in THIS Qt build a
    bare `setCurrentItem` already collapses the group, so that mutation
    SURVIVES as an equivalent one — the explicit form is kept as the
    guarantee that does not depend on that coincidence, and the group cells
    (one of them on the row that is ALREADY current, where `setCurrentItem`
    has its own early return) pin the behaviour down either way."""
    if tree_widget is None or item is None:
        return
    tree_widget.clearSelection()
    item.setSelected(True)
    tree_widget.setCurrentItem(item)


def selected_real_node(dock, tree: Tree) -> Optional[TreeNode]:
    """The FIRST selected real TreeNode of `tree`'s widget, or None — the
    single-node reader (the existing guards read it by name, and the
    single-node flows that mean ONE node read the first element of the same
    list); a GROUP is read through selected_real_nodes. Pseudo-roots (anchor,
    "⇐ embedded in", "→ instance:") carry a str / nothing and are not editable
    nodes, and the "Copper" pseudo-node (Т1.2) carries a marker object, so it
    is not one either — all three are skipped rather than "the first row
    decides"."""
    nodes = selected_real_nodes(dock, tree)
    return nodes[0] if nodes else None


def selected_copper_group(dock, tree: Tree) -> bool:
    """True when the CURRENT selection of `tree`'s widget is the "Copper"
    pseudo-node (Т1.2) — the one selection whose right-hand panel must stay
    EMPTY. Asked separately from selected_real_node because "not a real
    node" is ALSO the anchor row, which shows the anchor form.

    The pseudo-node's rule is about a selection OF the pseudo-node: it is the
    ONLY selected row (Ч1 п.1 of plan_2026_10_14_tree_group_move_and_mount_
    rebase). A MIXED selection — the pseudo-node plus real nodes — is the
    group case (_MULTI_SELECTION_PANEL), so the panel and the status line
    cannot disagree about what the user has selected."""
    from . import trees_dock

    tree_widget = dock._tree_widget_for(tree)
    if tree_widget is None:
        return False
    items = tree_widget.selectedItems()
    return len(items) == 1 and trees_dock._is_copper_group_item(
        items[0].data(0, Qt.ItemDataRole.UserRole))
