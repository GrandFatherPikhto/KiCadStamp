# tests/gui/docks/test_trees_dock_drop.py
"""Сторожа Ч3 of plan_2026_10_14_tree_group_move_and_mount_rebase: dragging the
SELECTION of a tree onto another node.

A drop IS the same "Move to…" with the parent already chosen (Ч3 п.1), so most
of these cells call the flow body the way the widget does — `run_drop_move` —
and compare it with what Ч1 does. The widget's own two rules (Ч3 п.2: Qt never
moves the items; Д-6: only ON a row or on the empty space) are pinned through
`NodeTreeWidget` itself, with a real `QDropEvent`.

TABLE OF THE CELLS (rule 35 — property x branch, written before the code; each
row names the member that makes it fail):

  dragged            | target            | pinned down
  -------------------|-------------------|--------------------------------------
  group (2 nodes)    | another mount     | both re-hang, offsets re-expressed so
                     |                   | the BOARD places stay (Ч3 п.1/п.5)
  group under a mount| another mount     | the REDRAW PLAN of every child is
  with xy!=0, rot!=0 |                   | unchanged (уточнение 3 of Ч3)
  one node           | another mount     | moves through the same body; on an
                     |                   | impossible hold: a Log line, the tree
                     |                   | as it was, and NO question (Ч3 уточнение 1)
  group              | its own descendant| refused — the tree is byte-identical
  group              | one of itself     | refused — the tree is byte-identical
  group              | the empty space   | "(top level)", named in the Log
  group + a copper   | another mount     | the copper row is NAMED and left out,
  row                |                   | the rest of the group moves
  group              | a row that is not | `node_of_item` answers None; the cursor
                     | a node            | predicate refuses the position
  (widget) group     | a row             | `dropEvent` calls the flow and NEVER
                     |                   | `QTreeWidget.dropEvent` (Ч3 п.2), and the
                     |                   | rebuilt items agree with the model
  (widget) copper /  | —                 | `startDrag` does not start (Д-8)
  empty selection    |                   |

NOT covered here, named rather than implied: a real mouse drag (Qt's own event
synthesis in a headless run), the shared-socket deferral (the Ч1/Ч2 cells and
tests/gui/test_board_door_offenders.py own it) and the cursor's LOOK (the
predicate is pinned, the painted cursor is not).

WHAT THE CELLS WITH `dropIndicatorPosition()` SUBSTITUTED DO — AND DO NOT —
CHECK. Five cells below replace `dropIndicatorPosition()` on purpose, because
what they pin is the TARGET RULE (`drop_refusal`): which target is accepted and
that a refusal stays silent. They deliberately do NOT observe Qt's hit-testing —
they would keep passing if the position were computed wrongly. That half is
pinned separately and STRUCTURALLY, and the choice is measured, not assumed:
`diagnostics/probe_2026_10_14_drag_position_update.py` shows that a synthetic
drag event carries `source() is None`, and the base class then leaves
`dropIndicatorPosition()` at its default for EVERY position (the empty space and
a row both answer OnItem), so no honestly driven cell can see the computation in
a headless run. The cell
`test_drag_move_event_runs_the_base_handler_first` therefore pins the DELEGATION
and its ORDER — and fails the moment `super().dragMoveEvent(event)` is dropped.
"""
from PyQt6.QtCore import QMimeData, QPointF, Qt
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QMessageBox, QTreeWidget

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import TreeNode

from gui.docks.trees_dock import TreesDock
from gui.docks.trees_dnd import (drop_position_accepts, node_of_item)
from gui.docks.trees_move_flow import (RehangPlan, _GROUP_COPPER_SKIPPED,
                                       _GROUP_MOVE_REFUSED, _TREE_MOVE_DONE,
                                       run_drop_move)

from tests.fakes.write_later import write_later
from tests.gui.docks.test_trees_dock import sync_long_ops  # noqa: F401 — fixture
from tests.gui.docks.test_trees_dock_group_move import (_pose_stub, _row_node,
                                                        _select_rows)
from tests.gui.docks.test_trees_dock_mount_child_frame import (
    ANCHOR_DEG,
    ANCHOR_MM,
    MOVE_CFG,
    SECOND_DEG,
    SECOND_MM,
    _ChildFrameBoard,
    _assert_same_pose,
    _pin_dock,
    _plan_poses,
)

#: The tree the drop cells work on: ONE mount carrying three record nodes (one of
#: them with a child, so "drop on my own descendant" has a target) and a SECOND
#: mount to drop onto. The bases come from `_pose_stub` (mnt_a 10 mm, mnt_b 30 mm),
#: so a re-hang really has two frames to hop between, and 10 + 12 = 22 = 30 + (−8).
DROP_CFG = {
    "trees": [{
        "name": "drop_tree", "anchor": {"origin": True},
        "nodes": [
            {"ref": "mnt_a", "kind": "mount", "anchor": {"role": "IC1"},
             "children": [
                 {"ref": "R_ONE", "kind": "external", "xy": [12.0, 0.0],
                  "children": [
                      {"ref": "R_DEEP", "kind": "external", "xy": [1.0, 0.0]},
                  ]},
                 {"ref": "R_TWO", "kind": "external", "xy": [14.0, 0.0]},
             ]},
            {"ref": "mnt_b", "kind": "mount", "anchor": {"role": "IC2"}},
        ]}]}

_POSITION = QAbstractItemView.DropIndicatorPosition


def _drop_dock(main_window, tmp_path, cfg=None, *, rebuild=True):
    """A dock over `cfg`; `rebuild=False` keeps `_rebuild_tabs` out of the way
    for the cells that only read the model (the widget cells need the real one)."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp(cfg or DROP_CFG, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    if not rebuild:
        dock._rebuild_tabs = lambda: None
    return dock


def _select(dock, *refs):
    """Select rows the way the group flow reads them — without needing the
    click (the widget cells use `_select_rows`, which talks to the widget)."""
    monkey = [_row_node(dock, ref) for ref in refs]
    dock._selected_real_nodes = lambda tree: list(monkey)
    return monkey


def _model_refs(tree):
    out: list[str] = []

    def walk(nodes):
        for node in nodes:
            out.append(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return out


# ── the flow body: a drop is the Ч1 "Move to…" with the parent chosen ──────

def test_a_dropped_group_moves_and_stays_on_the_board(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч3 п.1/п.5: two nodes dropped on `mnt_b` land under it, and the numbers are
    re-expressed through the new base (10 + 12 = 22 = 30 + (−8)) — exactly what
    the menu entry writes for the same selection.

    Mutation check: drop the recalculation (write the group without
    `_rehang_plan`) and the absolute places below come out 20 mm off."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _select(dock, "R_ONE", "R_TWO")

    with caplog.at_level(logging.INFO):
        run_drop_move(dock, tree, _row_node(dock, "mnt_b"))

    assert [c.ref for c in tree.nodes[1].children] == ["R_ONE", "R_TWO"], \
        "the drop must re-hang the group under the target, in document order"
    assert _row_node(dock, "R_ONE").xy == (-8.0, 0.0)
    assert _row_node(dock, "R_TWO").xy == (-6.0, 0.0)
    assert _TREE_MOVE_DONE.format(nodes="R_ONE, R_TWO", parent="mnt_b") \
        in " ".join(rec.getMessage() for rec in caplog.records)


def test_a_drop_under_a_rotated_mount_keeps_the_planned_poses(
        main_window, tmp_path, monkeypatch):
    """уточнение 3 of Ч3 (Денис, 14.10): a drop under a mount that has its OWN
    `xy` and `rotation` must leave every child's REDRAW pose where it was.

    The fixture is the one the Ч2 boundary-3 доделка measured with: both mounts
    carry non-zero numbers, so the re-hang really has to re-express the children
    through two different POINT frames. Mutation check: make the mount-parent
    branch of `_resolve_node_base_pose` answer the ANCHOR again and the children
    land millimetres away, at another angle."""
    from tests.gui.docks.test_trees_dock import _dock_with, _live_fp

    dock, _root = _dock_with(main_window, tmp_path, MOVE_CFG)
    tree = dock._current_tree()
    mount_a, mount_b = tree.nodes
    children = list(mount_a.children)
    board = _ChildFrameBoard([
        _live_fp("IC1", "FPGA", None, *ANCHOR_MM, angle=ANCHOR_DEG),
        _live_fp("IC2", "FPGA2", None, *SECOND_MM, angle=SECOND_DEG)])
    before = _plan_poses(dock, tree, board)
    _pin_dock(dock, board, monkeypatch)
    monkeypatch.setattr(dock, "_selected_real_nodes", lambda t: list(children))

    run_drop_move(dock, tree, mount_b)

    assert mount_b.children == children, "the structural half really moved them"
    after = _plan_poses(dock, tree, board)
    for child in children:
        _assert_same_pose(after[child.ref], before[child.ref],
                          "a dropped child must stay exactly where the redraw "
                          "planned it — position AND angle")


def test_a_dropped_single_node_moves_and_never_asks(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч3 уточнение 1 (Денис, 14.10): a node dropped with the mouse is NOT asked
    anything. When the offset can be neither recalculated nor read, the Log says
    so and the tree stays as it was — the question stays reachable through
    "Move to…" only.

    `QMessageBox.question` is turned into a failure here: a modal dialog in the
    middle of a drag is exactly what this cell forbids."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    node = _row_node(dock, "R_ONE")
    before = (node.xy, node.polar, node.rotation)
    _select(dock, "R_ONE")
    monkeypatch.setattr(
        dock, "_rehang_plan",
        lambda *a, **k: RehangPlan(refused=[(node, "no base")]))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest_fail_no_question())

    with caplog.at_level(logging.ERROR):
        run_drop_move(dock, tree, _row_node(dock, "mnt_b"))

    assert (node.xy, node.polar, node.rotation) == before, \
        "a refused drop must leave the node byte for byte as it was"
    assert _GROUP_MOVE_REFUSED.format(nodes="R_ONE", reason="R_ONE: no base") \
        in " ".join(rec.getMessage() for rec in caplog.records)


def pytest_fail_no_question():
    raise AssertionError("a drop must never open a question dialog (Ч3 уточнение 1)")


def test_a_drop_onto_its_own_descendant_is_refused(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч3 п.3 + Д-7: dropping a node into its own subtree is refused — nothing is
    detached, the tree is byte-for-byte what it was, and the refusal is SILENT
    (the cursor says "no"; the Log stays empty).

    The silence is what makes this cell different from "the structural half
    refused": `_reparent_nodes` has its own warning for a descendant target, and
    reaching it would mean the drop's own rule did not fire. Mutation check: let
    `drop_refusal` skip the forbidden-subtree check and the warning below appears
    (the tree still survives — the structural guard catches it)."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    # The bases must RESOLVE for this cell to mean anything: with no board the
    # plan would refuse first, and the structural guard this cell watches would
    # never be reached (measured — the mutation below survived without it).
    _pose_stub(monkeypatch)
    before = _model_refs(tree)
    _select(dock, "R_ONE")

    with caplog.at_level(logging.WARNING):
        run_drop_move(dock, tree, _row_node(dock, "R_DEEP"))

    assert _model_refs(tree) == before, "a refused drop must not touch the tree"
    assert tree.nodes[0].children[0].ref == "R_ONE", "the node did not move"
    assert "Refusing to re-hang" not in \
        " ".join(rec.getMessage() for rec in caplog.records), \
        "the refusal must be the DROP's (silent), not the structural guard's"


def test_a_drop_onto_one_of_the_dropped_nodes_is_refused(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч3 п.3: the target being one of the dragged rows themselves is impossible
    — a group cannot be moved under its own member — and, like the descendant
    case above, the refusal is the DROP's own (silent)."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)      # see the cell above: the plan must resolve
    before = _model_refs(tree)
    _select(dock, "R_ONE", "R_TWO")

    with caplog.at_level(logging.WARNING):
        run_drop_move(dock, tree, _row_node(dock, "R_ONE"))

    assert _model_refs(tree) == before
    assert tree.nodes[0].children[0].ref == "R_ONE"
    assert "Refusing to re-hang" not in \
        " ".join(rec.getMessage() for rec in caplog.records), \
        "the drops own rule must fire before the structural guard"


def test_a_drop_on_the_empty_space_means_the_top_level(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч3 п.4: the empty space below the rows is "(top level)" — the same first
    row the picker offers, and the Log names it that way."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _select(dock, "R_ONE", "R_TWO")

    with caplog.at_level(logging.INFO):
        run_drop_move(dock, tree, None)

    assert [n.ref for n in tree.nodes] == ["mnt_a", "mnt_b", "R_ONE", "R_TWO"], \
        "the dropped nodes become top-level, in document order"
    assert _TREE_MOVE_DONE.format(nodes="R_ONE, R_TWO", parent="(top level)") \
        in " ".join(rec.getMessage() for rec in caplog.records)


def test_a_copper_row_is_named_and_left_behind(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч1 п.5 through the drop: a copper node cannot ride with the group (its
    copper is offset from its OWN anchor pad), so it is NAMED in the Log rather
    than silently dropped from the move."""
    import logging

    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    copper = TreeNode(ref="NT_X", kind="net_trace", xy=None, polar=None,
                      rotation=0.0, name=None, group=None)
    ordinary = _select(dock, "R_ONE", "R_TWO")
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [copper] + ordinary)

    with caplog.at_level(logging.WARNING):
        run_drop_move(dock, tree, _row_node(dock, "mnt_b"))

    assert [c.ref for c in tree.nodes[1].children] == ["R_ONE", "R_TWO"], \
        "the movable part of the selection still moves"
    assert _GROUP_COPPER_SKIPPED.format(nodes="NT_X") \
        in " ".join(rec.getMessage() for rec in caplog.records)


def test_a_refused_structural_half_rolls_the_dropped_numbers_back(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.1 keeps the group path's rollback: the numbers are written BEFORE the
    structural move (so the rebuild renders the nodes in their new frame), and a
    structural refusal must put every one of them back — otherwise a node keeps
    numbers measured for a parent it does not hang under, i.e. it jumps on the
    board while the tree did not change.

    Mutation check: drop the restore loop in `apply_group_move` and the xy below
    comes back re-expressed for mnt_b (−8) instead of the stored 12."""
    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    nodes = _select(dock, "R_ONE", "R_TWO")
    before = [(node.xy, node.polar, node.rotation) for node in nodes]
    monkeypatch.setattr(dock, "_reparent_nodes", lambda *a, **k: False)

    run_drop_move(dock, tree, _row_node(dock, "mnt_b"))

    assert [(node.xy, node.polar, node.rotation) for node in nodes] == before, \
        "a refused structural half must leave every dropped node as it was"


# ── the widget's own rules ─────────────────────────────────────────────────

def test_drop_positions_accept_a_row_and_the_empty_space_only():
    """Ч3 п.4 (Д-6): ON a row and on the empty space are the two positions this
    tree acts on; the BETWEEN-row positions mean "reorder", which the plan
    deliberately leaves to "Move up / Move down"."""
    assert drop_position_accepts(_POSITION.OnItem)
    assert drop_position_accepts(_POSITION.OnViewport)
    assert not drop_position_accepts(_POSITION.AboveItem)
    assert not drop_position_accepts(_POSITION.BelowItem)


def test_a_pseudo_row_is_not_a_node(main_window, tmp_path):
    """`node_of_item` answers None for the anchor pseudo-root (no UserRole) and
    for the navigation pseudo-rows (a tree NAME, a str) — the ONE decoder the
    drop's target check reads."""
    from PyQt6.QtWidgets import QTreeWidgetItem

    dock = _drop_dock(main_window, tmp_path)
    widget = dock._tree_widget_for(dock._current_tree())
    anchor_item = widget.invisibleRootItem().child(0)
    named = QTreeWidgetItem()
    named.setData(0, Qt.ItemDataRole.UserRole, "some_tree")

    assert node_of_item(anchor_item) is None
    assert node_of_item(named) is None
    assert node_of_item(dock._node_items["R_ONE"]) is not None


def _drop_event():
    return QDropEvent(QPointF(0.0, 0.0), Qt.DropAction.MoveAction, QMimeData(),
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def test_the_drop_never_lets_qt_move_the_items(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.2: `dropEvent` accepts the drop, runs the flow and NEVER calls
    `QTreeWidget.dropEvent` — Qt's `InternalMove` would edit the ITEMS (the view)
    while the model is the `Tree`, and the two would drift apart.

    Mutation check: add a `super().dropEvent(event)` and `qt_calls` below is no
    longer empty."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [_row_node(dock, "R_ONE")])
    widget = dock._tree_widget_for(tree)
    monkeypatch.setattr(widget, "itemAt",
                        lambda pos: dock._node_items["mnt_b"])
    monkeypatch.setattr(widget, "dropIndicatorPosition", lambda: _POSITION.OnItem)
    qt_calls: list = []
    monkeypatch.setattr(QTreeWidget, "dropEvent",
                        lambda self, event: qt_calls.append(event))

    widget.dropEvent(_drop_event())

    assert qt_calls == [], "Qt must never be handed the drop (Ч3 п.2)"
    assert tree.nodes[1].children[0].ref == "R_ONE", \
        "the MODEL is what moved — the flow ran"


def test_a_drop_between_rows_is_not_accepted(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.4: between two rows the event is refused, and the flow is never
    asked — a drop there would silently mean "reorder"."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [_row_node(dock, "R_ONE")])
    widget = dock._tree_widget_for(tree)
    monkeypatch.setattr(widget, "itemAt",
                        lambda pos: dock._node_items["mnt_b"])
    monkeypatch.setattr(widget, "dropIndicatorPosition",
                        lambda: _POSITION.BelowItem)
    called: list = []
    monkeypatch.setattr("gui.docks.trees_dnd.run_drop_move",
                        lambda *a, **k: called.append(a))

    event = _drop_event()
    widget.dropEvent(event)

    assert not event.isAccepted(), "a between-rows drop must not be accepted"
    assert called == [], "the flow must not be reached"


def test_drag_move_event_runs_the_base_handler_first(
        main_window, tmp_path, monkeypatch):
    """The Ч3 acceptance's fix, pinned where it can be: the drop POSITION
    (`dropIndicatorPosition()`), the painted indicator and the auto-scroll all
    live in `QAbstractItemView.dragMoveEvent`, so OUR handler must run it FIRST
    and only then override the verdict.

    Structural on purpose — the reason is in the file docstring: a synthetic drag
    event has `source() is None`, and the base class then never computes a
    position, so a driven cell cannot observe the computation headlessly. Order
    is checked by recording both calls into one list: `["base", "verdict"]` means
    the base ran before our own `_allowed_at`, which is what the fix asks for.

    Mutation checks: drop `super().dragMoveEvent(event)` -> `["verdict"]`; call it
    AFTER the verdict -> `["verdict", "base"]`. Both fail below."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [_row_node(dock, "R_ONE")])
    widget = dock._tree_widget_for(tree)
    monkeypatch.setattr(widget, "dropIndicatorPosition", lambda: _POSITION.OnItem)
    monkeypatch.setattr(widget, "itemAt",
                        lambda pos: dock._node_items["mnt_b"])
    order: list[str] = []
    monkeypatch.setattr(QTreeWidget, "dragMoveEvent",
                        lambda self, event: order.append("base"))
    monkeypatch.setattr(widget, "_allowed_at",
                        lambda event: (order.append("verdict"), True)[1])

    widget.dragMoveEvent(_drop_event())

    assert order == ["base", "verdict"], (
        "the BASE dragMoveEvent must run FIRST — it computes the drop position, "
        "paints the indicator and scrolls a long tree; our verdict overrides "
        "what it accepted (got {order!r})".format(order=order))


def test_a_drop_refuses_a_target_that_is_not_a_row(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.3/Д-6: the cursor is over a PSEUDO-row (the anchor root, a navigation
    row) — not a node, so the position is refused before any flow is asked.

    Not to be confused with an empty `itemAt`: that IS the viewport, and the
    viewport means "(top level)" (the cell below covers it)."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [_row_node(dock, "R_ONE")])
    widget = dock._tree_widget_for(tree)
    anchor_item = widget.invisibleRootItem().child(0)
    assert node_of_item(anchor_item) is None, "the anchor row is not a node"
    monkeypatch.setattr(widget, "itemAt", lambda pos: anchor_item)
    monkeypatch.setattr(widget, "dropIndicatorPosition", lambda: _POSITION.OnItem)
    called: list = []
    monkeypatch.setattr("gui.docks.trees_dnd.run_drop_move",
                        lambda *a, **k: called.append(a))

    widget.dropEvent(_drop_event())

    assert called == [], "nothing to drop ON — the flow is not reached"


def test_start_drag_does_not_start_for_an_empty_or_movable_only_selection(
        main_window, tmp_path, monkeypatch):
    """Д-8: a drag starts only for a selection that really moves. An empty
    selection (and a selection of copper rows alone) must not begin one —
    otherwise the user would drag something the drop would leave behind."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    widget = dock._tree_widget_for(tree)
    started: list = []
    monkeypatch.setattr(QTreeWidget, "startDrag",
                        lambda self, actions: started.append(actions))

    copper = TreeNode(ref="NT_X", kind="net_trace", xy=None, polar=None,
                      rotation=0.0, name=None, group=None)
    monkeypatch.setattr(dock, "_selected_real_nodes", lambda t: [])
    widget.startDrag(Qt.DropAction.MoveAction)
    monkeypatch.setattr(dock, "_selected_real_nodes", lambda t: [copper])
    widget.startDrag(Qt.DropAction.MoveAction)

    assert started == [], "no movable node — no drag"


def test_the_rebuilt_items_agree_with_the_model_after_a_drop(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.2 from the other side: the widget is rebuilt FROM the model, so the
    rows and the tree cannot disagree after a drop."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    _select_rows(dock, "R_ONE")
    widget = dock._tree_widget_for(tree)
    monkeypatch.setattr(widget, "itemAt",
                        lambda pos: dock._node_items["mnt_b"])
    monkeypatch.setattr(widget, "dropIndicatorPosition", lambda: _POSITION.OnItem)

    widget.dropEvent(_drop_event())

    fresh = dock._tree_widget_for(tree)
    rows: list[str] = []

    def walk(item):
        for index in range(item.childCount()):
            child = item.child(index)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, TreeNode):
                rows.append(data.ref)
            walk(child)

    walk(fresh.invisibleRootItem())
    assert sorted(rows) == sorted(_model_refs(tree)), \
        "every model node has its row and no row outlived a node"
    assert dock._node_items["R_ONE"].parent() is dock._node_items["mnt_b"], \
        "the rebuilt widget shows the node under its new parent"


def test_a_drop_on_the_viewport_goes_to_the_top_level_through_the_widget(
        main_window, tmp_path, monkeypatch):
    """Ч3 п.4 from the widget's side: an empty `itemAt` means the viewport, and
    the viewport means "(top level)" — NOT "no target, refuse". This is the one
    case where `node_of_item` answering None is a legal parent rather than a
    refusal, so it is pinned separately from the pseudo-row above."""
    dock = _drop_dock(main_window, tmp_path)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    monkeypatch.setattr(dock, "_selected_real_nodes",
                        lambda t: [_row_node(dock, "R_ONE")])
    widget = dock._tree_widget_for(tree)
    monkeypatch.setattr(widget, "itemAt", lambda pos: None)
    monkeypatch.setattr(widget, "dropIndicatorPosition",
                        lambda: _POSITION.OnViewport)

    widget.dropEvent(_drop_event())

    assert tree.nodes[-1].ref == "R_ONE", \
        "a drop on the empty space puts the node at the top level"


def test_a_mount_nodes_own_anchor_is_untouched_by_a_drop(
        main_window, tmp_path, monkeypatch):
    """A drop re-hangs NODES; a mount node's own `xy`/`rotation`/anchor are not a
    parent's business. Pinned because the Ч2 доделка moved the frames around —
    a drop must not have started rewriting the anchor frame."""
    dock = _drop_dock(main_window, tmp_path, rebuild=False)
    tree = dock._current_tree()
    _pose_stub(monkeypatch)
    mount_a = tree.nodes[0]
    before = (mount_a.xy, mount_a.rotation, mount_a.anchor.role)
    _select(dock, "R_ONE")

    run_drop_move(dock, tree, _row_node(dock, "mnt_b"))

    assert (mount_a.xy, mount_a.rotation, mount_a.anchor.role) == before
