# gui/docks/trees_copper_groups.py
"""The Trees dock's copper pseudo-rows: «Copper (n)» and «Extra copper (n)».

Split out of `trees_dock.py` (a giant that only shrinks) when the extra-copper door
gave the dock a SECOND copper group (plan_2026_11_10_extra_copper_by_selection,
п.2): every `kind "net_trace"` node of a tree is still drawn in one foldable place,
but the fold now tells the two OWNERS apart — the inter-node records a re-read
writes, and the extra ones «Read extra copper from selection» writes.

Both rows obey exactly the same rules as the original single one:
  * the row is VIEW-ONLY — its UserRole is the `_CopperGroupItem` marker (Т1.2),
    it holds no TreeNode, is never collected as a checked ref and is never a drop
    target (its children are `net_trace` rows, `_COPPER_ROW_KINDS`);
  * it carries the same checkable/tristate flags, and the propagation of its check
    state onto the children stays in `TreesDock._on_item_check_changed`;
  * it is drawn only when it has children (no copper, no pseudo-row);
  * the order is fixed: «Copper (n)» first, then «Extra copper (n)», each in
    DOCUMENT order of its nodes (Т1.1);
  * the expansion of BOTH is the one persisted `copper_expanded` key — a second
    key would be a settings migration for the sake of a fold.

The drawing of ONE node stays the dock's own (`TreesDock._render_node`), passed in
as `render_node`, so the row's text, marks and menu are the very same code for
both groups — never a second renderer.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItem

from kicadstamp.i18n import _

from .trees_view_helpers import _CopperGroupItem, copper_nodes


def copper_group_rows(extra_ids: Iterable[str], nodes: list) -> list:
    """`[(row_title, nodes), …]` — the two groups in their fixed order, empty ones
    dropped. `extra_ids` are the record identities the dock read as EXTRA; a node
    of any other identity stays in «Copper (n)», which is where every copper node
    was drawn before this door existed."""
    extra = set(extra_ids or ())
    plain = [n for n in nodes if getattr(n, "ref", None) not in extra]
    own = [n for n in nodes if getattr(n, "ref", None) in extra]
    return [(title, group) for title, group in
            ((_("Copper ({count})"), plain), (_("Extra copper ({count})"), own))
            if group]


def render_copper_groups(parent_item: QTreeWidgetItem, tree, *,
                         copper_expanded: bool,
                         extra_ids: Iterable[str] = (),
                         render_node: Optional[Callable] = None,
                         ) -> None:
    """Draw the tree's copper nodes under one or two pseudo-rows — nothing at all
    when the tree has no copper (Т1.1: no copper, no pseudo-node; a legacy
    "copper" container with no children therefore renders as nothing).

    The caller keeps ownership of everything a row CONTAINS: `render_node(item,
    node)` is the dock's own node renderer, so both groups are drawn by the same
    code that drew them before."""
    nodes = copper_nodes(tree)
    if not nodes or render_node is None:
        return
    for title, group in copper_group_rows(extra_ids, nodes):
        item = QTreeWidgetItem(parent_item)
        item.setText(0, title.format(count=len(group)))
        item.setData(0, Qt.ItemDataRole.UserRole, _CopperGroupItem())
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                      | Qt.ItemFlag.ItemIsUserTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        for node in group:
            render_node(item, node)
        # setExpanded is only meaningful on a populated parent, so it comes
        # after the children exist (the same order _render_node uses).
        item.setExpanded(bool(copper_expanded))
