# tests/gui/docks/test_trees_dock_select_node.py
"""Cells for the two halves of «К ноде в дереве» (Ч2б-2 of plan_2026_10_23_checks_tab_top_level).

A check report addresses a node by NAME (`to_node = {tree, node, cell}`), so the Checks page
needs one walk that lands on that node: `TreesDock.select_node` opens the named tree and makes
the named node the ONE selected row — expanding whatever hides it, because a selected row
nobody can see is not a jump — and `DockHub.select_tree_node` is the ONE door the page is
allowed to know about (it shows the Trees page first; the page must not learn that the Trees
tab and the tree widget are two different things).

Both answer False when there is nothing to land on, and neither pretends: the page says the
node is not there instead of leaving the operator on some other row believing they arrived.
"""
from types import SimpleNamespace

from kicadstamp.config.sexp_format import dict_to_sexp

from gui.dock_hub import DockHub
from gui.docks.trees_dock import TreesDock
from gui.docks.trees_selection import item_of_ref

from tests.fakes.write_later import write_later

#: `PARENT` carries `DEEP`, so landing on DEEP needs its parent expanded; `OTHER` is a
#: sibling row that must NOT come along — `select_only` means ONE row.
NODES = [
    {"ref": "PARENT", "kind": "external", "xy": [1.0, 0.0],
     "children": [{"ref": "DEEP", "kind": "external", "xy": [0.5, 0.0]}]},
    {"ref": "OTHER", "kind": "external", "xy": [2.0, 0.0]},
]


def _selected_refs(dock) -> list:
    """The `ref` of every selected NODE row, in selection order — read off the node objects
    rather than the row text (a row carries the kind tag too: "B_ONLY (External)")."""
    return [item.data(0, 0x0100).ref
            for item in dock._current_tree_widget().selectedItems()]


def _dock(main_window, tmp_path) -> TreesDock:
    """A TreesDock over a throwaway root config with TWO trees, so "open the named one" is a
    real switch and not a no-op: `alpha` carries the nested pair, `beta` a single node.

    The write goes through `write_later` (the dock's config readers are cached by
    `(path, mtime_ns)`, so a same-tick second write would be invisible)."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({
        "trees": [
            {"name": "alpha", "anchor": {"origin": True}, "nodes": NODES},
            {"name": "beta", "anchor": {"origin": True},
             "nodes": [{"ref": "B_ONLY", "kind": "external", "xy": [0.0, 0.0]}]},
        ]}, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    return dock


def test_select_node_selects_the_row_and_expands_its_parents(main_window, tmp_path):
    """T11 of Ч2б-2: the named TREE becomes the current tab, the named NODE becomes the only
    selected row (a group selection collapses — the door means ONE node), and every parent
    above it is expanded, or the operator would be "sent" somewhere they cannot see."""
    dock = _dock(main_window, tmp_path)

    assert dock.select_node("beta", "B_ONLY") is True
    assert dock._current_tab_tree_name() == "beta"
    assert _selected_refs(dock) == ["B_ONLY"]

    assert dock.select_node("alpha", "DEEP") is True
    assert dock._current_tab_tree_name() == "alpha"
    item = item_of_ref(dock._current_tree_widget(), "DEEP")
    assert item is not None and item.isSelected()
    assert item.parent().isExpanded(), "a selected row must be visible"
    assert _selected_refs(dock) == ["DEEP"], "ONE node — the door does not mean a group"


def test_select_node_answers_false_for_a_node_that_is_not_there(main_window, tmp_path):
    """T11b: a node name that is not in the tree, and a tree name that is not loaded, both
    answer False — the caller says so. Landing on the anchor or on another row and calling it
    a jump would send the operator to the wrong place with a straight face."""
    dock = _dock(main_window, tmp_path)

    assert dock.select_node("alpha", "GHOST") is False
    assert dock.select_node("no_such_tree", "PARENT") is False


def test_the_hub_shows_the_trees_page_and_selects_the_node(qapp):
    """T12 of Ч2б-2: the Checks page knows ONE name — `DockHub.select_tree_node`. It brings
    the Trees tab to the front and lands on the node; a miss is a False (and no page switch
    of its own is invented), and an empty address never reaches the dock at all."""
    hub = DockHub.__new__(DockHub)          # no __init__: this cell wires one method's body
    shown, asked = [], []
    hub.left_tabs = SimpleNamespace(
        setCurrentWidget=lambda widget: shown.append(widget))
    hub.trees_dock = SimpleNamespace(
        select_node=lambda tree, node: asked.append((tree, node)) or True)

    assert hub.select_tree_node("alpha", "DEEP") is True
    assert shown == [hub.trees_dock], "the Trees page is the one that comes to the front"
    assert asked == [("alpha", "DEEP")]

    hub.trees_dock = SimpleNamespace(
        select_node=lambda tree, node: asked.append((tree, node)) or False)
    asked.clear()
    shown.clear()
    assert hub.select_tree_node("alpha", "GHOST") is False
    assert asked == [("alpha", "GHOST")]

    asked.clear()
    assert hub.select_tree_node("", "DEEP") is False
    assert hub.select_tree_node("alpha", "") is False
    assert asked == [], "an empty address is not a request"
