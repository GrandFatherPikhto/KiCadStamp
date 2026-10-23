# tests/gui/docks/test_trees_accept_on_leave.py
"""The ONE Apply button and the acceptance of an edited form on every way out
(plan_2026_10_23_node_form_without_apply.md, Ч1).

What is pinned here, and why each cell is about a LOSS rather than about a
widget: before this change an edit typed into the master-detail form lived in
the widgets only, so a node switch, a Save, a rebuild, a Close or the window's
exit dropped it without a word. The cells below walk every door and assert the
edit SURVIVED (or, when it does not validate, that the door was refused and the
red Log line names the node).

The fixtures are the real dock on a real root config; `_dock_with` and its
helpers come from test_trees_dock (the same tree, the same config) so the two
files cannot drift about what "a node" is.
"""
import logging

import pytest

from gui.docks import trees_accept
from gui.docks.trees_dock import (AnchorFormWidget, NodeFormWidget, _NodeDialog)
from kicadstamp.trees import TreeAnchor, TreeNode

from tests.gui.docks.test_trees_dock import (_children, _dock_with,
                                             _embedded_form)


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в): accepting a form STAGES the trees section, and the
    format-3 writer resolves a reference's UUID against the ACTIVE GRAPH ROOT.
    The root is a path that does NOT exist, so the stamp indexes THIS write's
    own records (the format-3 product path, no on-disk graph walked)."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


def _silence_warnings(monkeypatch):
    """The FORM's own invalid-input message is a QMessageBox — its historic
    behaviour, unchanged by Ч1 (rule 43's exception is about the FORM's own
    refusal, and the acceptance's report is the red Log line the cells assert).
    A headless cell installs a stand-in so the modal never opens."""
    import gui.docks.trees_dock as trees_dock_mod

    monkeypatch.setattr(trees_dock_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))


def _tree_widget(dock):
    return dock._tree_widget_of_page(dock.tree_tabs.widget(0))


def _nodes(dock):
    tree_widget = _tree_widget(dock)
    return tree_widget, _children(_children(tree_widget.invisibleRootItem())[0])


# ── the way out by SELECTION ──────────────────────────────────────────────

def test_switching_to_another_node_accepts_the_edit(main_window, tmp_path):
    """Cell 1: the edit is written onto the node it was made on, the config is
    dirty, and nothing says the draft was discarded."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    form.name_edit.setText("edited_on_the_way_out")
    tree_widget.setCurrentItem(nodes[1])
    assert dock._trees[0].nodes[0].name == "edited_on_the_way_out"
    assert dock._dirty is True
    assert "discarded" not in dock.status_label.text()


def test_walking_through_nodes_without_editing_writes_nothing(
        main_window, tmp_path, monkeypatch):
    """Cell 2: opening and leaving forms is NOT an edit — a mere walk must never
    reach `_mark_dirty` at all (the flag is `_touched`, not "a form exists").

    The dock's `_mark_dirty` is the gate every acceptance goes through, so it is
    COUNTED here: "nothing was written" is exactly "it was never called"."""
    dock, _root = _dock_with(main_window, tmp_path)
    marked = []
    monkeypatch.setattr(dock, "_mark_dirty", lambda: marked.append(True))
    tree_widget, nodes = _nodes(dock)
    for item in (nodes[0], nodes[1], nodes[0]):
        tree_widget.setCurrentItem(item)
        assert _embedded_form(dock._active_form_page())._touched is False
    assert marked == []
    assert dock._dirty is False


def test_prefilling_a_form_never_marks_it_touched(main_window, tmp_path):
    """The acceptance's own condition: `_touched` is set by USER edits only, so
    a freshly opened form (both forms) is clean."""
    dock, _root = _dock_with(main_window, tmp_path)
    anchor_form = _embedded_form(dock._active_form_page())
    assert isinstance(anchor_form, AnchorFormWidget)
    assert anchor_form._touched is False
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    assert _embedded_form(dock._active_form_page())._touched is False


def test_a_refused_edit_holds_the_selection_and_names_the_node(
        main_window, tmp_path, caplog, monkeypatch):
    """Cell 3: a form that does not validate REFUSES the switch — the selection
    goes back to the node, the panel keeps the draft, and the red Log line names
    the node and the reason."""
    _silence_warnings(monkeypatch)
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    # A nonsense offset — the form's OWN refusal, unchanged (it writes the
    # reason into apply_status_label, which the red line quotes).
    form.offset_widget.x_edit.setText("not_a_number")
    with caplog.at_level(logging.ERROR):
        tree_widget.setCurrentItem(nodes[1])
    assert tree_widget.currentItem() is nodes[0]
    assert dock._current_tree().nodes[0].xy == (5.0, 2.0)
    assert dock._dirty is False
    assert _embedded_form(dock._active_form_page()) is form
    assert form._touched is True
    messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("AMS1117_REG" in m and "not applied" in m for m in messages), messages


def test_a_rename_is_accepted_before_the_new_row_is_selected(main_window, tmp_path):
    """Cell 4: the rename is accepted on the OLD node, the tree row carries the
    new name, and the selection ends on the node the user went to."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    form = _embedded_form(dock._active_form_page())
    form.name_edit.setText("renamed_here")
    tree_widget.setCurrentItem(nodes[1])
    assert dock._trees[0].nodes[0].name == "renamed_here"
    assert tree_widget.currentItem() is nodes[1]
    assert _embedded_form(dock._active_form_page())._existing is dock._trees[0].nodes[1]


def test_an_anchor_edit_is_accepted_on_the_way_out(main_window, tmp_path):
    """Cell 11: the TREE ANCHOR form (the second form of the panel) is accepted
    by the same door — the tree's anchor really changed."""
    dock, _root = _dock_with(main_window, tmp_path)
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    form.mode_combo.setCurrentIndex(0)  # Origin (board 0,0)
    assert form._touched is True
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    assert dock._trees[0].anchor.is_origin is True
    assert dock._dirty is True


# ── the ONE Apply button ──────────────────────────────────────────────────

#: The child ref is NEW on purpose: a record may have exactly one node in a
#: config, so re-using "C_OUT" (already placed under AMS1117_REG) would make the
#: form refuse the edit — a different cell's property, not this one.
MOUNT_NODE = TreeNode(
    ref="M1", kind="mount", xy=(2.0, 1.0), polar=None, rotation=0.0, name=None,
    group=None, anchor=TreeAnchor(role="U1", is_origin=False),
    children=[TreeNode(ref="M1_CHILD", kind="external", xy=(1.0, 0.0),
                       polar=None, rotation=0.0, name=None, group=None,
                       children=[])])


def test_apply_on_a_mount_node_redraws_its_own_subtree(main_window, tmp_path,
                                                       monkeypatch):
    """Cell 8: a `mount` node names no record, so its edit lives on its CHILDREN.
    One Apply: the edit is accepted AND the curated redraw of the subtree is
    started with `selected_refs` = the node + its descendants (the anchor/opora
    itself is in the set only as the subtree's ROOT — the curated plan does not
    move it; that guarantee is tree_position's own, tested there)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree.nodes.append(MOUNT_NODE)
    dock._rebuild_tabs()
    tree_widget = _tree_widget(dock)
    tree_widget.setCurrentItem(dock._node_items["M1"])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    calls = []
    monkeypatch.setattr(
        trees_accept, "start_long_op",
        lambda *a, **k: calls.append((a, k)) or None)
    form.name_edit.setText("mount_edited")
    assert trees_accept.apply_and_redraw(dock, form) is True
    assert MOUNT_NODE.name == "mount_edited"
    assert len(calls) == 1, calls
    payload = calls[0][0][5]
    assert payload["selected_refs"] == {"M1", "M1_CHILD"}
    assert payload["tree_name"] == "power_tree"


def test_apply_on_a_node_without_children_only_accepts(main_window, tmp_path,
                                                       monkeypatch):
    """Cell 9в (decision 3 of the Ч0 acceptance): `external`/`point` with no
    children has nothing to place — Apply accepts, starts NOTHING, and says so
    instead of claiming a redraw that never happened."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND — external, no children
    form = _embedded_form(dock._active_form_page())
    placed, started = [], []
    monkeypatch.setattr(dock, "_redraw_edited_node", lambda n: placed.append(n))
    monkeypatch.setattr(trees_accept, "start_long_op",
                        lambda *a, **k: started.append(a) or None)
    form.name_edit.setText("still_external")
    assert trees_accept.apply_and_redraw(dock, form) is True
    assert dock._trees[0].nodes[1].name == "still_external"
    assert placed == [] and started == []
    assert "nothing to redraw" in form.apply_status_label.text()


def test_apply_without_a_board_accepts_and_says_why_there_was_no_redraw(
        main_window, tmp_path):
    """Cell 9а: no dock/board → accepted, and the status names the missing board
    (never a silent "done")."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[1])
    form = _embedded_form(dock._active_form_page())
    trees_accept.redraw_form(None, form)
    assert "no live board Redraw available" in form.apply_status_label.text()


def test_a_refused_apply_does_nothing_else(main_window, tmp_path, monkeypatch):
    """The button's first half is the accept: when it refuses, no redraw is
    attempted on a half-edited node."""
    _silence_warnings(monkeypatch)
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    form = _embedded_form(dock._active_form_page())
    form.offset_widget.x_edit.setText("not_a_number")
    placed = []
    monkeypatch.setattr(dock, "_redraw_edited_node", lambda n: placed.append(n))
    assert trees_accept.apply_and_redraw(dock, form) is False
    assert placed == []


# ── the dialog (the third host of the button) ─────────────────────────────

def _dialog(dock, node):
    tree = dock._current_tree()
    return _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                       "Edit node", cfg=dock._cfg, adapter=None,
                       sheet_names={}, tree=tree,
                       parent_node=dock._find_parent(tree, node), existing=node)


def test_the_dialog_close_accepts_the_edit(main_window, tmp_path):
    """Cell 10: Close on a touched dialog ACCEPTS first — the edit is on the
    node, and the dialog is gone."""
    dock, _root = _dock_with(main_window, tmp_path)
    node = dock._current_tree().nodes[0]
    dlg = _dialog(dock, node)
    dlg.name_edit.setText("closed_with_edit")
    dlg._on_close()
    assert node.name == "closed_with_edit"
    assert dock._dirty is True


def test_a_refused_edit_keeps_the_dialog_open(main_window, tmp_path, monkeypatch,
                                              caplog):
    """Cell 10 (refusal half): a form that does not validate keeps the dialog
    OPEN and the node untouched — closing is never the way a draft dies."""
    _silence_warnings(monkeypatch)
    dock, _root = _dock_with(main_window, tmp_path)
    node = dock._current_tree().nodes[0]
    dlg = _dialog(dock, node)
    dlg.offset_widget.x_edit.setText("not_a_number")
    closed = []
    monkeypatch.setattr(dlg, "reject", lambda: closed.append(True))
    with caplog.at_level(logging.ERROR):
        dlg._on_close()
    assert closed == []
    assert dlg._form._touched is True
    assert node.xy == (5.0, 2.0)
    assert any("not applied" in r.message for r in caplog.records
               if r.levelno == logging.ERROR)


# ── every editable field flags the form (the enumeration of Ч1) ───────────

def _open_node_form(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget, nodes = _nodes(dock)
    tree_widget.setCurrentItem(nodes[0])
    return dock, _embedded_form(dock._active_form_page())


NODE_FIELD_EDITS = {
    "name": lambda form: form.name_edit.setText("x"),
    "group": lambda form: form.group_edit.setText("g"),
    "rotation": lambda form: form.rotation_edit.setText("12"),
    "kind": lambda form: form.kind_combo.setCurrentIndex(
        form.kind_combo.findData("external")),
    "ref": lambda form: form.ref_combo.setCurrentText("SOMETHING_ELSE"),
    "offset": lambda form: form.offset_widget.x_edit.setText("7.25"),
    "mount_anchor": lambda form: form.mount_anchor_widget.fieldChanged.emit(),
    "component_address": lambda form: form.component_address_widget.fieldChanged.emit(),
}


@pytest.mark.parametrize("group", sorted(NODE_FIELD_EDITS))
def test_every_node_form_field_marks_the_form_touched(group, main_window,
                                                      tmp_path):
    """A missed field is exactly the silent loss this change removes, so the
    NODE form's every user-facing group is exercised by name."""
    _dock, form = _open_node_form(main_window, tmp_path)
    assert isinstance(form, NodeFormWidget)
    assert form._touched is False
    NODE_FIELD_EDITS[group](form)
    assert form._touched is True, group


ANCHOR_FIELD_EDITS = {
    "mode": lambda form: form.mode_combo.setCurrentIndex(0),
    "kind": lambda form: form.kind_combo.setCurrentIndex(1),
    # A NEW ref, not the tree's own: the combo already SHOWS CONN_PM5V (its
    # first candidate), and setting the text it already holds fires nothing.
    "ref": lambda form: form.ref_combo.setEditText("FREE_TYPED_REF"),
    "role": lambda form: form.role_edit.setCurrentText("U1"),
    "sheet": lambda form: form.sheet_edit.setCurrentText("Power"),
    "cluster": lambda form: form.cluster_edit.setCurrentText("C1"),
    "pad": lambda form: form.pad_edit.setText("1"),
    "self_combo": lambda form: form.self_combo.setCurrentText("AMS1117_REG"),
    "self_pad": lambda form: form.self_pad_edit.setText("2"),
    "shift_x": lambda form: form.shift_x_edit.setText("1.5"),
    "pivot": lambda form: form.pivot_widget.x_edit.setText("2.5"),
    "rotation": lambda form: form.rotation_edit.setText("10"),
}


@pytest.mark.parametrize("group", sorted(ANCHOR_FIELD_EDITS))
def test_every_anchor_form_field_marks_the_form_touched(group, main_window,
                                                        tmp_path):
    """The ANCHOR/TREE-SETTINGS form's every group, the same way — including the
    `self` mode's own two rows, which were not wired before Ч1."""
    dock, _root = _dock_with(main_window, tmp_path)
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    assert form._touched is False
    ANCHOR_FIELD_EDITS[group](form)
    assert form._touched is True, group
