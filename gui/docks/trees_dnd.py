# gui/docks/trees_dnd.py
"""Ч3 of plan_2026_10_14_tree_group_move_and_mount_rebase: dragging the SELECTION
of a tree onto another node.

WHAT A DROP IS: the same "Move to…" the menu offers, with the new parent already
chosen by where the mouse was released (Ч3 п.1). The re-hang itself is
`trees_move_flow.rehang_under_known_parent`; which target is legal is
`trees_move_flow.drop_refusal` — the SAME predicate the menu's candidate list is
built from, so a target the cursor accepts can never be refused by the move and
vice versa. Nothing in this module re-expresses either rule.

WHY A SUB-CLASS, not an event filter: Qt only offers a drop to a view that
overrides `dragMoveEvent` / `dropEvent`, and the ONE thing this feature must not
do is let Qt perform ITS OWN move — `InternalMove` would edit the ITEMS (the
view) while the model is the `Tree`, and the two would drift apart silently
(Ч3 п.2). `dropEvent` therefore accepts the event and calls the model-side
operation itself, and NEVER `super()`. The existing guards can prove that: the
cell `test_the_drop_never_lets_qt_move_the_items` patches `QTreeWidget.dropEvent`
and fails if it is reached.

Ч3 п.4 (Д-6): only a drop ON a row (`OnItem`) and on the empty space below the
rows (`OnViewport` → "(top level)") are accepted. A drop BETWEEN rows would mean
"reorder", and ordering is what "Move up / Move down" is for — the position
predicate below is the ONE expression of that rule.

Ч3 п.3/Д-8: a drag does not even START for a selection that holds no movable real
node (a copper row, a pseudo-row), and a structurally impossible target is
refused silently — the cursor says "no", the Log stays empty.

WHAT IS NOT HERE, deliberately: the tree's own configuration (column/header,
selection mode, stylesheet, signals) stays in `trees_dock._rebuild_tabs`, which
builds the widget through `make_node_tree_widget`; and every board read stays in
the dock's own methods, reached by `trees_move_flow` through the `trees_dock`
module attribute (Д3 of the Ч0 review)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QTreeWidget

from kicadstamp.trees import Tree, TreeNode

from .trees_move_flow import drop_refusal, movable_roots, run_drop_move

#: Ч3 п.4: the drop positions that mean something in THIS tree. `OnItem` is the
#: row under the cursor, `OnViewport` the empty space (the top level). The two
#: BETWEEN-row positions (`AboveItem` / `BelowItem`) mean "insert next to", i.e.
#: a reorder, which this task deliberately does not implement.
_ACCEPTED_DROP_POSITIONS = (
    QAbstractItemView.DropIndicatorPosition.OnItem,
    QAbstractItemView.DropIndicatorPosition.OnViewport,
)


def drop_position_accepts(position) -> bool:
    """True when `position` (a `DropIndicatorPosition`) is one this tree acts on
    — see `_ACCEPTED_DROP_POSITIONS`. Pure, so the Ч3 п.4 rule is testable without
    synthesising a drag."""
    return position in _ACCEPTED_DROP_POSITIONS


def node_of_item(item) -> Optional[TreeNode]:
    """The real `TreeNode` of a row, or None — the ONE decoder of "is this row a
    node". The anchor pseudo-root carries no UserRole, the embedded-in /
    instance / instance-of pseudo-roots carry a tree NAME (a str) and the
    "Copper" fold carries its own marker object, so `isinstance(data, TreeNode)`
    is the whole rule (same decoder as `trees_selection.real_nodes_of_items`)."""
    if item is None:
        return None
    data = item.data(0, Qt.ItemDataRole.UserRole)
    return data if isinstance(data, TreeNode) else None


class NodeTreeWidget(QTreeWidget):
    """A tree tab's node widget with Ч3's drag&drop. The dock builds it through
    `make_node_tree_widget` and keeps its own configuration (selection mode,
    signals) — this class owns only what a drop needs."""

    def __init__(self, dock, tree: Tree) -> None:
        super().__init__()
        self._dock = dock
        self._tree = tree
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        # `InternalMove` is what makes Qt OFFER the drop at all; the MOVE is the
        # model's (see `dropEvent`), never Qt's list manipulation.
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    # ── what may be dragged ────────────────────────────────────────────────
    def _movable_roots(self) -> list[TreeNode]:
        """The roots `run_drop_move` would move for the CURRENT selection —
        asked through the SAME `movable_roots` helper the flow uses, so the
        cursor and the operation cannot disagree about what is being dragged."""
        from . import trees_dock

        selection = self._dock._selected_real_nodes(self._tree)
        roots, _excluded = movable_roots(self._tree, selection,
                                         trees_dock._COPPER_ROW_KINDS)
        return roots

    def startDrag(self, supported_actions) -> None:  # noqa: N802 — Qt API name
        """Д-8: a drag is started ONLY for a selection that really moves. A
        copper row and every pseudo-row are not draggable, and neither is a
        selection that is empty after copper rows are dropped — otherwise the
        user would drag something the drop would then silently leave behind."""
        if not self._movable_roots():
            return
        super().startDrag(Qt.DropAction.MoveAction)

    # ── the drop ───────────────────────────────────────────────────────────
    def _target(self, event):
        """`(ok, target)` for the drop under the cursor: `target` is the node a
        drop would become a child of, and `ok` says whether this position means
        anything at all.

        The two ways to have NO parent node are deliberately different, and the
        POSITION is what tells them apart: over the empty space
        (`OnViewport`) it is the viewport — "(top level)", a legal parent, so
        `ok` is True and `target` is None; ON a row (`OnItem`) whose payload is
        not a node (the anchor root, a navigation pseudo-row, the "Copper" fold)
        there is nothing to drop on, so `ok` is False. Reading `itemAt` alone
        cannot express that: an empty `itemAt` is the viewport, not a row."""
        position = self.dropIndicatorPosition()
        if not drop_position_accepts(position):
            return False, None
        if position == QAbstractItemView.DropIndicatorPosition.OnViewport:
            return True, None
        node = node_of_item(self.itemAt(event.position().toPoint()))
        return (node is not None), node

    def _allowed_at(self, event) -> bool:
        """ONE predicate for the cursor AND the drop: the position is one this
        tree acts on, it names a target, there is something to move, and
        `drop_refusal` — the very rule the menu's candidate list uses — accepts
        that target."""
        ok, target = self._target(event)
        if not ok:
            return False
        roots = self._movable_roots()
        if not roots:
            return False
        from . import trees_dock

        return drop_refusal(
            self._tree, roots, target,
            lambda node, candidate: trees_dock._pivot_ref_mount_parent(
                self._tree, node, candidate)) is None

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt API name
        """The BASE handler runs FIRST, and that order is the whole point.

        `dropIndicatorPosition()`, the painted indicator and the auto-scroll all
        live in `QAbstractItemView.dragMoveEvent`. Without it the position stays
        frozen at whatever `dragEnterEvent` left (measured 2026-10-14, the Ч3
        acceptance), and then a drop on the EMPTY SPACE still reads as "on a row"
        (so "(top level)" is unreachable), a drop BETWEEN rows is accepted (Д-6
        broken) and dragging down a long tree never scrolls it.

        Our verdict comes SECOND and overrides whatever the base accepted: the
        same `drop_refusal` the drop itself runs, so the cursor can never allow
        what the move would refuse. `dropEvent` deliberately does NOT call
        `super()` — Qt's own move would edit the items while the model is the
        `Tree` (see `dropEvent`)."""
        super().dragMoveEvent(event)
        if not self._allowed_at(event):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API name
        """The drop is HANDLED here and Qt's own move is NEVER run: `InternalMove`
        would edit the ITEMS (the view) while the model is the `Tree`, and the
        two would drift apart (Ч3 п.2). `super()` is deliberately not called —
        the cell `test_the_drop_never_lets_qt_move_the_items` fails if it is."""
        if not self._allowed_at(event):
            event.ignore()
            return
        _ok, target = self._target(event)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        run_drop_move(self._dock, self._tree, target)


def make_node_tree_widget(dock, tree: Tree) -> NodeTreeWidget:
    """The ONE place a tree's node widget is born (Ч3, Д-5): the dock's
    `_rebuild_tabs` replaces its bare `QTreeWidget()` with this call and keeps
    every other line of the widget's configuration (rule 45: the giant keeps the
    wiring, this module keeps the body)."""
    return NodeTreeWidget(dock, tree)
