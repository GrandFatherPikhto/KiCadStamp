# tests/gui/docks/test_trees_dock_group_selection.py
"""Сторожа Ч1 п.1/п.9 of plan_2026_10_14_tree_group_move_and_mount_rebase: the
trees dock's node tree became a MULTI-select tree (Denis 14.10: "а можно сделать
перемещение в другую корневую ноду группой (по выделению? галочками)" — по
ВЫДЕЛЕНИЮ, не галочками).

Three things are pinned down here, and nothing else:

  * the node tree really allows ExtendedSelection (the Config tree's mode, copied
    with its own precedent gui/docks/config_tree.py:458);
  * a GROUP of selected real nodes shows the COUNT instead of an editor — the
    panel has no editor and no Apply, and the tree's data is untouched by the
    VIEW (Ч1 п.9: "Форма узла при N > 1 — надпись «Выделено узлов: N» вместо
    редактора; ничего не пишет"); the count is part of the panel identity, so
    growing the selection 2 -> 3 re-fills the page instead of keeping "2";
  * every entry point that means ONE node collapses a group selection onto that
    one row (double-click, "Edit node…", "Set anchor…") and a right-click OUTSIDE
    the selection acts on the clicked row alone, while a right-click INSIDE it
    keeps the group (that is what makes the group operations reachable from any
    row of the group).
"""
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QAbstractItemView, QLabel, QPushButton

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import tree_to_sexp

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_dock import AnchorFormWidget, NodeFormWidget, TreesDock

from tests.fakes.write_later import write_later


# Four sibling nodes, all kind "external": an external ref is never resolved
# against the config (link_trees), so the fixture needs no records at all and the
# cells can select rows without a live board.
def _nodes():
    return [{"ref": ref, "kind": "external", "xy": [float(i + 1), 0.0]}
            for i, ref in enumerate(("A_NODE", "B_NODE", "C_NODE", "D_NODE"))]


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _dock_with(main_window, tmp_path, nodes=None, extra=None):
    """A TreesDock over a throwaway root config whose only tree carries `nodes`.
    `extra` adds other sections (a `net_traces:` record for a copper node, say) —
    a format-3 load resolves every reference, so a kind="net_trace" node needs
    its record to exist.

    Ф3.7: the write goes through ``write_later``, not a bare ``write_text`` — the
    dock's config readers are cached by ``(path, mtime_ns)``, and a second write
    landing on the same tick would be invisible to them.
    """
    root = tmp_path / "root.sexp"
    sections = dict(extra or {})
    sections["trees"] = [{"name": "group_tree", "anchor": {"origin": True},
                          "nodes": _nodes() if nodes is None else nodes}]
    write_later(root, dict_to_sexp(sections, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    return dock


def _rows(dock):
    """The tree's NODE rows, in render order. The anchor pseudo-root is the first
    child of the invisible root, and the NODE rows are ITS children (the shape
    the existing master-detail cells walk: `_children(_children(root)[0])`) — so
    nothing is sliced off here; doing that would silently shift every index by one
    and the cells would assert against the wrong node."""
    root = dock._current_tree_widget().invisibleRootItem()
    return _children(_children(root)[0])


def _select(dock, *rows):
    """Ctrl-click equivalent: select exactly `rows` (the previous selection is
    cleared first, so the cell states its own starting point)."""
    tree_widget = dock._current_tree_widget()
    tree_widget.clearSelection()
    for row in rows:
        row.setSelected(True)


def _embedded_form(page):
    """The modal-agnostic form inside a _form_action_row wrapper page — the
    wrapper's top VBox puts the form at itemAt(0).

    NOT a "what is on this page" probe: the count page of a GROUP is not a
    wrapper at all (it is a bare QWidget whose own VBox starts with its label),
    so this helper returns that label for it. Cells that ask "is there an editor
    here?" ask `page.findChildren(NodeFormWidget)`."""
    lay = page.layout()
    if lay is None:
        return None
    item = lay.itemAt(0)
    return item.widget() if item is not None else None


def _page_text(page) -> str:
    """Every QLabel text of a panel page, joined — the count page's own words are
    read through this instead of a child index, so a layout change cannot make a
    cell pass for the wrong reason."""
    return " | ".join(label.text() for label in page.findChildren(QLabel))


def _right_click(dock, item, monkeypatch):
    """Run _on_context_menu for `item`: QMenu.exec is no-oped (no menu is shown)
    and itemAt is pinned to the item (offscreen geometry is degenerate, so the
    real hit test would be a coin toss — the sibling file's own idiom)."""
    monkeypatch.setattr(trees_dock_mod.QMenu, "exec", lambda self, *a, **k: None)
    tree_widget = dock._current_tree_widget()
    monkeypatch.setattr(tree_widget, "itemAt", lambda pos: item)
    dock._on_context_menu(QPoint(0, 0))


def _selected_ids(tree_widget):
    return {id(i) for i in tree_widget.selectedItems()}


# ── Ч1 п.1: the mode itself ────────────────────────────────────────────────

def test_the_node_tree_allows_extended_selection(main_window, tmp_path):
    """Ч1 п.1 — the node tree is an ExtendedSelection tree, like the Config tree
    that turned the mode on for its Export (gui/docks/config_tree.py:458). The
    MODE is the whole point of "группой (по выделению)": without it Ctrl/Shift
    cannot build a group and every group operation below is unreachable."""
    dock = _dock_with(main_window, tmp_path)
    assert (dock._current_tree_widget().selectionMode()
            == QAbstractItemView.SelectionMode.ExtendedSelection)


def test_two_selected_nodes_show_the_count_instead_of_an_editor(
        main_window, tmp_path):
    """Ч1 п.9 — a GROUP has no editor: the panel names the count, carries no
    Apply row and no form, and the VIEW changes nothing in the tree's DATA.

    Mutation check: drop the multi branch from _fill_form_panel and the panel
    shows the FIRST selected node's editor — an editor for a node the user did
    not single out."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    rows = _rows(dock)
    before = tree_to_sexp(tree)

    _select(dock, rows[0], rows[1])

    page = dock._active_form_page()
    assert page.property("_multi_selection_page") is True
    assert page.findChildren(NodeFormWidget) == [], "a group must not get an editor"
    assert page.findChildren(AnchorFormWidget) == [], "…and not the anchor form"
    assert page.findChildren(QPushButton) == [], "a group must offer no Apply"
    assert _page_text(page) == "2 nodes selected"
    assert dock._current_node_ref == ("multi-selection", 2)
    assert dock.status_label.text() == "2 nodes selected"
    assert tree_to_sexp(tree) == before, "the group VIEW must not touch the data"


def test_a_single_selected_node_keeps_its_editor(main_window, tmp_path):
    """The single-node case is untouched by Ч1 п.9: one selected row still shows
    that node's editor (the guard the group branch could have swallowed)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    rows = _rows(dock)

    _select(dock, rows[0])

    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is tree.nodes[0]
    assert dock._current_node_ref == "A_NODE"


def test_the_count_is_part_of_the_panel_identity(main_window, tmp_path):
    """Ч1 п.9 — the count is IN the identity: a selection that GROWS from two
    rows to three re-fills the page. A marker without the count would keep the
    page (the §7.1.5 "same selection, keep the form" guard compares identities)
    and the panel would go on claiming "2 nodes selected" with three rows
    selected.

    The third row is ADDED (Ctrl-click), never re-selected from scratch: a
    cleared selection passes through a different identity on its way back, which
    would re-fill the page for the wrong reason and hide this defect.

    Mutation check: make the identity a bare marker (drop the count from
    _refresh_form_panel's new_ref) and this fails on the "3"."""
    dock = _dock_with(main_window, tmp_path)
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    assert _page_text(dock._active_form_page()) == "2 nodes selected"

    rows[2].setSelected(True)          # Ctrl-click: the group GROWS

    assert _page_text(dock._active_form_page()) == "3 nodes selected"
    assert dock._current_node_ref == ("multi-selection", 3)
    assert dock.status_label.text() == "3 nodes selected"


def test_the_selection_readers_skip_pseudo_rows(main_window, tmp_path):
    """Ч1 п.1 — the group is made of REAL nodes only. The anchor pseudo-root
    carries no TreeNode in UserRole, so a selection of "anchor + one node" is a
    group of ONE (its editor), never a group of two counting the anchor."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._current_tree_widget()
    anchor_row = _children(tree_widget.invisibleRootItem())[0]
    rows = _rows(dock)

    _select(dock, anchor_row, rows[0])

    assert dock._selected_real_nodes(tree) == [tree.nodes[0]]
    assert dock._selected_real_node(tree) is tree.nodes[0]
    assert isinstance(_embedded_form(dock._active_form_page()), NodeFormWidget)

    _select(dock, anchor_row)

    assert dock._selected_real_nodes(tree) == []
    assert dock._selected_real_node(tree) is None
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)


# ── Ч1 п.1: one-node entry points collapse the group ───────────────────────

def test_double_click_collapses_the_group_onto_one_node(main_window, tmp_path):
    """A double-click means ONE node: the group collapses onto the clicked row,
    or the panel would keep showing "N nodes selected" for a row the user just
    asked about (and would open no editor at all)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    dock._on_node_activated(rows[1], 0)

    assert _selected_ids(tree_widget) == {id(rows[1])}
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is tree.nodes[1]


def test_collapsing_a_group_works_on_the_row_that_is_already_current(
        main_window, tmp_path):
    """The collapse must be EXPLICIT, and this is the case that proves it: the
    double-clicked row is the one already CURRENT (the user clicked it first, then
    Ctrl-clicked another row to build the group — Qt keeps the FIRST row current).
    `setCurrentItem` on the already-current row does nothing at all (its
    "clear and select" path is not even reached), so a collapse built on it alone
    would leave the group selected and the panel on "N nodes selected" for a row
    the user just asked about.

    Mutation check: reduce _select_only to a bare setCurrentItem and this fails;
    the other three collapse cells keep passing, which is WHY this one exists."""
    dock = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)

    tree_widget.clearSelection()
    rows[1].setSelected(True)
    tree_widget.setCurrentItem(rows[1])     # the first click: current + selected
    rows[0].setSelected(True)               # Ctrl-click: the group grows

    dock._on_node_activated(rows[1], 0)

    assert _selected_ids(tree_widget) == {id(rows[1])}
    assert isinstance(_embedded_form(dock._active_form_page()), NodeFormWidget)


def test_edit_node_collapses_the_group_onto_the_edited_row(main_window, tmp_path):
    """'Edit node…' means ONE node: the group collapses onto the row being
    edited, so the panel really shows the editor the action promises."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    dock._edit_in_panel(tree, tree.nodes[1])

    assert _selected_ids(tree_widget) == {id(rows[1])}
    assert isinstance(_embedded_form(dock._active_form_page()), NodeFormWidget)


def test_set_anchor_collapses_the_group_onto_the_root_row(main_window, tmp_path):
    """'Set anchor…' means ONE row: the group collapses onto the anchor row (the
    anchor row is not a TreeNode, so the panel shows the anchor form — what the
    collapse guarantees is WHAT IS SELECTED, not only what is shown)."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._current_tree_widget()
    anchor_row = _children(tree_widget.invisibleRootItem())[0]
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    dock._select_anchor_row(tree)

    assert _selected_ids(tree_widget) == {id(anchor_row)}
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)


# ── Ч1 п.1: the right-click rule ──────────────────────────────────────────

def test_right_click_outside_the_selection_acts_on_that_row_alone(
        main_window, tmp_path, monkeypatch):
    """The Config tree's rule, copied: a right-click OUTSIDE the current
    selection replaces it with just this row — which is what keeps a plain
    single right-click behaving exactly as before the mode was turned on."""
    dock = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    _right_click(dock, rows[2], monkeypatch)

    assert _selected_ids(tree_widget) == {id(rows[2])}


def test_right_click_inside_the_selection_keeps_the_group(
        main_window, tmp_path, monkeypatch):
    """A right-click INSIDE the selection KEEPS the group on purpose — that is
    what makes the group operations ("Move to…" over the whole selection, Ч1
    п.2) reachable from any row of the group.

    Mutation check: clear the selection unconditionally in _on_context_menu and
    this fails (the group is gone before the menu is built)."""
    dock = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    _right_click(dock, rows[0], monkeypatch)

    assert _selected_ids(tree_widget) == {id(rows[0]), id(rows[1])}
    assert dock._current_node_ref == ("multi-selection", 2)


def test_a_copper_row_selected_with_a_node_is_decided_by_the_real_nodes(
        main_window, tmp_path):
    """The "Copper" pseudo-node's EMPTY panel is about a selection OF that row,
    not about a selection that merely CONTAINS it (Ч1 п.1 turned the selection
    into a list, so "the first selected row decides" is no longer the rule). The
    pseudo-row is a fold, not data: the real node decides, and the status row says
    the same thing — the two readers can never describe ONE selection differently.

    Mutation check: go back to reading `items[0]` in _selected_copper_group and
    this fails — with the pseudo-row selected FIRST the panel would sit EMPTY
    while the status row named the node."""
    dock = _dock_with(
        main_window, tmp_path,
        nodes=[{"ref": "A_NODE", "kind": "external", "xy": [1.0, 0.0]},
               {"ref": "nt_group", "kind": "net_trace"}],
        extra={"net_traces": [{"name": "nt_group", "net": "GND",
                               "anchor_role": "FPGA", "tracks": [], "vias": []}]})
    tree = dock._current_tree()
    tree_widget = dock._current_tree_widget()
    rows = _rows(dock)
    copper_row = rows[-1]
    before = tree_to_sexp(tree)

    _select(dock, copper_row, rows[0])

    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget), \
        "a real node in the selection must win over the pseudo-row"
    assert form._existing is tree.nodes[0]
    assert "A_NODE" in dock.status_label.text()
    assert tree_to_sexp(tree) == before
    assert len(tree_widget.selectedItems()) == 2, "both rows stay selected"


def test_the_anchor_row_menu_keeps_a_group_it_does_not_belong_to(
        main_window, tmp_path, monkeypatch):
    """The rule is about the CLICKED row, not about the row kind: right-clicking
    the anchor pseudo-root while two nodes are selected replaces the selection
    with the anchor row (the anchor row is not part of any group of nodes).

    Mutation check: skip the rule for non-TreeNode rows and this fails — the
    anchor menu would then be offered while a stale node group stays selected."""
    dock = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    anchor_row = _children(tree_widget.invisibleRootItem())[0]
    rows = _rows(dock)

    _select(dock, rows[0], rows[1])
    _right_click(dock, anchor_row, monkeypatch)

    assert _selected_ids(tree_widget) == {id(anchor_row)}
