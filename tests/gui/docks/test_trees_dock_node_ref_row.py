# tests/gui/docks/test_trees_dock_node_ref_row.py
"""The node form's Apply must update the tree ROW IN PLACE when the Ref (or the
Kind) changes — the defect reported by Denis on 08.10.2026
(techdocs/handoff/deepseek/plan/plan_2026_10_08_tree_node_ref_apply.md):
NodeFormWidget.apply() mutated the node in place and marked the dock dirty, but
`_node_items` stayed keyed by the OLD ref, so the row's text went stale until the
whole GUI was restarted.

The guard is the MASTER-DETAIL Node tab — the ONE production node-edit path. The
modal `_NodeDialog`'s Edit mode wraps the SAME form and carries the same fix,
but no production caller ever opens it for an existing node (`_prompt_node` is
wired to the three Add flows only, `existing=None`); it is exercised by tests,
not by the running app.

Red before the fix (the row keeps the old text and the old `_node_items` key);
green after.
"""
import pytest
from PyQt6.QtWidgets import QTreeWidget

from gui.docks.trees_dock import NodeFormWidget, TreesDock

from tests.fakes.write_later import write_later


@pytest.fixture(autouse=True)
def _row_rig(tmp_path, monkeypatch):
    """The two rigs the node-form Apply write path needs, plus the format pin.

    У3.5 (class (в)): an Apply marks the dock dirty, which stages the trees:
    section through the config writer; under format 3 the writer stamp resolves
    references against the process-wide ACTIVE GRAPH ROOT, so it must be set
    (the same rig tests/gui/docks/test_trees_dock.py uses).

    CURRENT_FORMAT is pinned to 2 because the SUBJECT here is the ROW update,
    never the format: a Kind change to a record kind ("point") edits a node whose
    ref has no record in this throwaway config, and only the format-3 stamp would
    refuse that at write time. Under format 2 the write path is byte-for-byte the
    product's own (the same pin its format-independent cells use)."""
    from kicadstamp.config import format_version
    from kicadstamp.config_working_set import set_active_graph_root

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


# One tree, two nodes: "A" carries a child (so expansion is meaningful) and "B"
# is a second, independent row. All kinds are "external" — a live-board refdes,
# never resolved against the config, so a minimal root config loads and an
# Apply needs no records, no board and no clone/points sections.
TREES = {
    "trees": [
        {"name": "t", "anchor": {"origin": True},
         "nodes": [
             {"ref": "A", "kind": "external", "xy": [1.0, 0.0],
              "children": [{"ref": "A_CHILD", "kind": "external",
                            "xy": [0.5, 0.0]}]},
             {"ref": "B", "kind": "external", "xy": [2.0, 0.0]},
         ]},
    ],
}


def _dock(main_window, tmp_path) -> TreesDock:
    from kicadstamp.config.sexp_format import dict_to_sexp

    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp(TREES, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config did not load"
    return dock


def _open_form(dock: TreesDock, ref: str):
    """Select the row `ref` on the master-detail page and return (widget,
    item, form). The item is the one the dock's registry carries."""
    widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    assert isinstance(widget, QTreeWidget)
    widget.expandAll()
    item = dock._node_items[ref]
    widget.setCurrentItem(item)
    form = dock._embedded_form_of(dock._panel_page(dock._active_form_panel()))
    assert isinstance(form, NodeFormWidget)
    return widget, item, form


def test_node_form_ref_change_updates_row_in_place(main_window, tmp_path,
                                                   monkeypatch):
    """Ref edit -> Apply: the SAME row moves to the new `_node_items` key, its
    text is the new ref, the old key is gone, the selection and the expansion
    survive, and the whole dock is NOT rebuilt (`_rebuild_tabs` would tear the
    open form down)."""
    dock = _dock(main_window, tmp_path)
    widget, item, form = _open_form(dock, "A")
    node = form._existing
    assert node.ref == "A"
    assert item.isExpanded() is True

    # The fix must be IN PLACE: a full rebuild is forbidden here (the plan's
    # item 2 keeps it as the last resort only).
    monkeypatch.setattr(dock, "_rebuild_tabs",
                        lambda: pytest.fail("the row update rebuilt every tab"))

    form.ref_combo.setCurrentText("A2")
    assert form.apply() is True

    assert node.ref == "A2"
    assert "A" not in dock._node_items
    assert dock._node_items["A2"] is item          # the SAME widget item
    assert item.text(0) == "A2 (external)"         # render's own text idiom
    assert widget.currentItem() is item            # selection kept
    assert item.isExpanded() is True               # expansion kept
    # The dock's own master-detail identity follows the node.
    assert dock._current_node_ref == "A2"


def test_node_form_kind_change_updates_row_text(main_window, tmp_path):
    """Kind edit (same ref) -> Apply: the `_node_items` key stays, but the row's
    text follows the new kind tag through the render's OWN text routine."""
    dock = _dock(main_window, tmp_path)
    _widget, item, form = _open_form(dock, "B")
    node = form._existing
    assert item.text(0) == "B (external)"

    idx = form.kind_combo.findData("point")
    assert idx >= 0, "the Kind combo must offer the 'point' kind"
    form.kind_combo.setCurrentIndex(idx)
    assert form.apply() is True

    assert node.kind == "point"
    assert dock._node_items["B"] is item
    assert item.text(0) == "B (point)"


def test_node_form_ref_change_when_no_board_keeps_child_rows(main_window,
                                                             tmp_path):
    """The child's row is untouched (only the renamed row is re-keyed): the
    subtree stays reachable under the moved parent item."""
    dock = _dock(main_window, tmp_path)
    _widget, item, form = _open_form(dock, "A")
    child_item = dock._node_items["A_CHILD"]

    form.ref_combo.setCurrentText("A_RENAMED")
    assert form.apply() is True

    assert dock._node_items["A_CHILD"] is child_item
    assert child_item.parent() is item             # still the renamed parent's
