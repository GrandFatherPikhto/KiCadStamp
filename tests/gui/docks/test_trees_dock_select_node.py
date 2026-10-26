# tests/gui/docks/test_trees_dock_select_node.py
"""Cells for the walk behind «К ноде в дереве» (Ч2б-2 of plan_2026_10_23_checks_tab_top_level).

A check report addresses a node by NAME (`to_node = {tree, node, cell}`), so the Checks page
needs one walk that lands on that node. That walk is
`gui/docks/trees_selection.select_tree_node(dock, tree, node)` — the body lives in the
SELECTION layer and takes the dock, rather than being a method ON the dock: `gui/docks/
trees_dock.py` and `gui/dock_hub.py` are giants that only shrink (rule 45, Д-1 of the Ч2б-2
acceptance), so neither may grow a line for a door. The page switch is the hub's own
`show_left_page`, and `gui/docks/checks_report.jump_to_node` is the one place that composes
the two halves.

The walk never pretends: a node that is not in the tree, and a tree that is not loaded, both
answer False, and the door says so with the node and the tree in the line — leaving the
operator on some other row believing they arrived would be worse than no jump at all.
"""
from types import SimpleNamespace

from kicadstamp.config.sexp_format import dict_to_sexp

from gui.docks.checks_report import jump_to_node
from gui.docks.trees_dock import TreesDock
from gui.docks.trees_selection import item_of_ref, select_tree_node

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


def test_select_tree_node_selects_the_row_and_expands_its_parents(main_window, tmp_path):
    """T11 of Ч2б-2: the named TREE becomes the current tab, the named NODE becomes the only
    selected row (a group selection collapses — the door means ONE node), and every parent
    above it is expanded, or the operator would be "sent" somewhere they cannot see."""
    dock = _dock(main_window, tmp_path)

    assert select_tree_node(dock, "beta", "B_ONLY") is True
    assert dock._current_tab_tree_name() == "beta"
    assert _selected_refs(dock) == ["B_ONLY"]

    assert select_tree_node(dock, "alpha", "DEEP") is True
    assert dock._current_tab_tree_name() == "alpha"
    item = item_of_ref(dock._current_tree_widget(), "DEEP")
    assert item is not None and item.isSelected()
    assert item.parent().isExpanded(), "a selected row must be visible"
    assert _selected_refs(dock) == ["DEEP"], "ONE node — the door does not mean a group"


def test_select_tree_node_answers_false_for_a_node_that_is_not_there(main_window,
                                                                     tmp_path):
    """T11b: a node name that is not in the tree, a tree name that is not loaded and an empty
    address all answer False — the caller says so."""
    dock = _dock(main_window, tmp_path)

    assert select_tree_node(dock, "alpha", "GHOST") is False
    assert select_tree_node(dock, "no_such_tree", "PARENT") is False
    assert select_tree_node(dock, "", "PARENT") is False
    assert select_tree_node(dock, "alpha", "") is False


def test_the_door_brings_the_trees_page_and_lands_on_the_node(main_window, tmp_path):
    """T12 of Ч2б-2: «К ноде в дереве» is ONE walk — the Trees page to the front, then the
    node — and its two halves live where rule 45 puts them: `DockHub.show_left_page` for the
    page, `trees_selection.select_tree_node` for the row. A miss is one line in the status
    bar naming the node and the tree (never a modal — rule 43)."""
    dock = _dock(main_window, tmp_path)
    shown, status = [], []
    hub = SimpleNamespace(trees_dock=dock, show_left_page=shown.append)
    page = SimpleNamespace(
        _main_window=SimpleNamespace(dock_hub=hub),
        _show_status=lambda text, level: status.append((text, level)))

    jump_to_node(page, "alpha", "DEEP")
    assert shown == [dock], "the Trees page comes to the front"
    assert _selected_refs(dock) == ["DEEP"]
    assert status == [("Selected DEEP in alpha.", "success")]

    status.clear()
    jump_to_node(page, "alpha", "GHOST")
    assert status == [("The node GHOST is not in the tree alpha.", "error")]
    assert _selected_refs(dock) == ["DEEP"], "a miss leaves the selection where it was"
