# tests/gui/test_trees_dock.py
"""Tests for TreesDock (gui/docks/trees_dock.py) — the hand-authored s-expr
"trees" editor, now editing the ROOT CONFIG's trees: section
(design_2026_08_27_trees_in_config_file.md FORK-5): the dock follows
root_changed (set_root_file), has no file identity of its own, and Save goes
through the single config_writer chokepoint.

Per-tree tabs with a read-only QTreeWidget render + the static node_offset()
preview; structural editing; Save + dirty tracking; checkbox subtree
selection + background curated Redraw.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from kicadstamp.config.loader import load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.docks.trees_dock import TreesDock, _NodeDialog

# The same working example as tests/test_trees.py's GRAMMAR_EXAMPLE, expressed
# as the root-config dict shape (tree_to_dict output) — two trees, nested
# nodes, xy and polar offsets, a ref anchor and an origin anchor.
GRAMMAR_TREES = {
    "trees": [
        {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"},
         "nodes": [
             {"ref": "AMS1117_REG", "kind": "clone", "xy": [5.0, 2.0],
              "children": [{"ref": "C_OUT", "xy": [1.0, 0]}]},
             {"ref": "R_AROUND", "polar": [3.0, 45.0]},
         ]},
        {"name": "misc", "anchor": {"origin": True},
         "nodes": [{"ref": "R_DEBUG", "xy": [100.0, 50.0]}]},
    ],
}

# A trees: section for the SAVE tests (which run _do_save's link_trees
# round-trip). GRAMMAR_TREES references records that the throwaway root config
# does not contain (no clone/point sections), so link_trees would legitimately
# raise "node not found" and _do_save would open a blocking QMessageBox — see
# design_2026_08_27_trees_in_config_file.md §5.2's Save round-trip. Nodes here
# are all kind "external" (never resolved against config, per link_trees.py) and
# anchors are (origin), so a Save round-trip succeeds with no extra sections.
SAVE_TREES = {
    "trees": [
        {"name": "power_tree", "anchor": {"origin": True},
         "nodes": [
             {"ref": "AMS1117_REG", "kind": "external", "xy": [5.0, 2.0],
              "children": [{"ref": "C_OUT", "kind": "external", "xy": [1.0, 0]}]},
             {"ref": "R_AROUND", "kind": "external", "polar": [3.0, 45.0]},
         ]},
        {"name": "misc", "anchor": {"origin": True},
         "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [100.0, 50.0]}]},
    ],
}


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _dock_with(main_window, tmp_path, trees=None):
    """A TreesDock pointed at a root config (YAML) carrying the given trees:
    section — the current way trees get into the dock (set_root_file, no
    Open/New of a .trees file anymore)."""
    trees = trees if trees is not None else GRAMMAR_TREES
    root = tmp_path / "root.yaml"
    root.write_text(yaml.safe_dump(trees), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


# ── Root wiring (replaces the old Open/New of a .trees file) ───────────────

def test_set_root_file_loads_trees_from_config(main_window, tmp_path):
    """Trees come from the ROOT CONFIG's trees: section — set_root_file reads
    them into _trees (empty when there is no root / no section)."""
    dock, root = _dock_with(main_window, tmp_path)
    assert [t.name for t in dock._trees] == ["power_tree", "misc"]
    assert dock._cfg is not None
    assert dock._root_path == root

    dock2 = TreesDock(main_window)
    dock2.set_root_file(None)  # no root -> no trees, placeholder tab
    assert dock2._trees == []
    assert dock2.tabs.count() == 1


def test_set_root_file_broken_config_does_not_crash(main_window, tmp_path):
    """A root config whose trees: section is malformed raises ValidationError
    in load_config — the dock must not crash: trees stay empty, cfg stays None
    (Save's link_trees round-trip is skipped until a good root loads)."""
    root = tmp_path / "root.yaml"
    root.write_text("trees:\n- name: t1\n  anchor: {ref: A}\n  nodes:\n  - ref: B\n    xy: 1\n",
                    encoding="utf-8")  # xy must be exactly 2 numbers
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._trees == []
    assert dock._cfg is None
    assert dock.tabs.count() == 1  # placeholder, not a crash


def test_set_root_file_renders_one_tab_per_tree_with_nested_structure(main_window, tmp_path):
    """Two trees -> two tabs; the first tree's render mirrors the nested
    grammar shape (anchor pseudo-root + nodes + child)."""
    dock, _root = _dock_with(main_window, tmp_path)

    assert dock.tabs.count() == 2
    assert dock.tabs.tabText(0) == "power_tree"
    assert dock.tabs.tabText(1) == "misc"

    tree_widget = dock.tabs.widget(0)
    tops = _children(tree_widget.invisibleRootItem())
    assert len(tops) == 1
    assert "CONN_PM5V" in tops[0].text(0)
    nodes = _children(tops[0])
    assert len(nodes) == 2
    assert nodes[0].text(0) == "AMS1117_REG (clone)"
    assert nodes[1].text(0) == "R_AROUND"
    ams_children = _children(nodes[0])
    assert [c.text(0) for c in ams_children] == ["C_OUT"]


# ── Static preview ────────────────────────────────────────────────────────

def test_static_preview_xy_node(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock.tabs.widget(0)
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG (xy 5.0 2.0)
    text = dock.status_label.text()
    assert "AMS1117_REG" in text and "xy=" in text
    assert "5.000" in text and "2.000" in text


def test_static_preview_polar_node(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock.tabs.widget(0)
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND (polar 3.0 45.0)
    text = dock.status_label.text()
    assert "R_AROUND" in text and "r=" in text
    assert "3.000" in text and "45.000" in text


# ── Toolbar ───────────────────────────────────────────────────────────────

def test_toolbar_buttons_enabled(main_window):
    """No Open/New buttons (trees live in the root config, RootMetadataDock
    owns the root); Add/Rename tree + Save + Redraw are enabled."""
    dock = TreesDock(main_window)
    assert dock.add_tree_button.isEnabled() is True
    assert dock.rename_tree_button.isEnabled() is True
    assert dock.save_button.isEnabled() is True
    assert dock.redraw_button.isEnabled() is True
    assert not hasattr(dock, "open_button")
    assert not hasattr(dock, "new_button")


# ── Phase 2: structural editing ───────────────────────────────────────────

def test_add_child_mutates_node_children_and_dirty(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    parent = tree.nodes[0]  # AMS1117_REG
    before = len(parent.children)

    new_node = TreeNode(ref="NEW_CHILD", kind=None, xy=(3.0, 4.0), polar=None,
                        rotation=0.0, name=None, group=None)
    parent.children.append(new_node)
    dock._mark_dirty()
    dock._rebuild_tabs()

    assert parent.children[before] is new_node
    assert dock._dirty is True
    tree_widget = dock._current_tree_widget()
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    assert any(c.text(0) == "NEW_CHILD" for c in _children(nodes[0]))


def test_delete_node_removes_subtree_and_marks_dirty(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    target = tree.nodes[1]  # R_AROUND
    tree.nodes.remove(target)
    dock._mark_dirty()
    dock._rebuild_tabs()

    assert target not in tree.nodes
    assert dock._dirty is True
    tree_widget = dock._current_tree_widget()
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    assert [n.text(0) for n in nodes] == ["AMS1117_REG (clone)"]


def test_move_into_own_descendant_is_forbidden(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    ams = tree.nodes[0]         # AMS1117_REG
    c_out = ams.children[0]     # C_OUT

    forbidden = dock._collect_subtree(ams)
    assert dock._in_list(c_out, forbidden)
    assert dock._in_list(ams, forbidden)
    candidates = []
    for top in tree.nodes:
        dock._collect_move_candidates(top, forbidden, candidates)
    assert not any(c is c_out for _label, c in candidates)
    assert not any(c is ams for _label, c in candidates)


def _context_menu_actions(dock, item, monkeypatch):
    """Runs _on_context_menu for `item` with QMenu.exec no-oped and captures
    the real QAction per label (lambda-capture regression safety)."""
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = td_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        action = original_add_action(self, text, *a, **k)
        captured.append((text, action))
        return action

    monkeypatch.setattr(td_mod.QMenu, "addAction", _record)
    tree_widget = dock._current_tree_widget()
    dock._on_context_menu(tree_widget.visualItemRect(item).center())
    return captured


def test_context_menu_on_anchor_offers_add_node(main_window, tmp_path, monkeypatch):
    """The anchor pseudo-root's context menu offers "Add node" (wired), and
    triggering it appends to tree.nodes (regression 2026-08-27)."""
    empty = {"trees": [{"name": "empty_tree", "anchor": {"origin": True}, "nodes": []}]}
    dock, _root = _dock_with(main_window, tmp_path, empty)
    tree = dock._current_tree()
    assert tree.nodes == []

    anchor_item = _children(dock._current_tree_widget().invisibleRootItem())[0]
    actions = dict(_context_menu_actions(dock, anchor_item, monkeypatch))
    assert "Add node" in actions
    assert "Set anchor…" in actions

    new_node = TreeNode(ref="FIRST_NODE", kind=None, xy=(0.0, 0.0), polar=None,
                        rotation=0.0, name=None, group=None)
    monkeypatch.setattr(dock, "_prompt_node", lambda *a, **k: new_node)
    actions["Add node"].trigger()

    assert tree.nodes == [new_node]
    assert dock._dirty is True


def test_rename_tree_enforces_unique_names(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    assert dock._current_tree().name == "power_tree"
    tree = dock._current_tree()
    other_names = {t.name for t in dock._trees if t is not tree}
    assert "misc" in other_names
    tree.name = "misc"
    assert any(t.name == tree.name for t in dock._trees if t is not tree)  # collision
    tree.name = "power_tree"  # undo, self-contained


# ── Phase 3: Save + dirty tracking (via config_writer into the root) ──────

def _make_dirty(dock):
    dock._trees.append(Tree(name="extra", anchor=TreeAnchor(ref=None, is_origin=True),
                            nodes=[]))
    dock._mark_dirty()
    dock._rebuild_tabs()


def test_save_backs_up_before_writing_and_clears_dirty(main_window, tmp_path, monkeypatch):
    """_do_save: the root config (.bak) is created BEFORE the write (its
    content is the OLD root), and a successful save clears dirty + persists
    the new tree list into the root's trees: section. Uses SAVE_TREES so the
    link_trees round-trip succeeds (external nodes — no records needed)."""
    dock, root = _dock_with(main_window, tmp_path, SAVE_TREES)
    _make_dirty(dock)

    old_text = root.read_text(encoding="utf-8")
    dock._do_save()

    baks = list(tmp_path.glob("root.yaml.bak.*"))
    assert baks, "expected a timestamped backup"
    assert baks[0].read_text(encoding="utf-8") == old_text
    assert dock._dirty is False
    cfg, _ = load_config(str(root))
    assert [t.name for t in cfg.trees] == ["power_tree", "misc", "extra"]


def test_save_roundtrip_failure_warns_but_leaves_backup(main_window, tmp_path, monkeypatch):
    """A link_trees round-trip failure after save is reported, the root IS
    written (by design), and the fresh .bak is the recovery point."""
    dock, root = _dock_with(main_window, tmp_path)
    assert dock._cfg is not None

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or QMessageBox.StandardButton.Ok)

    import gui.docks.trees_dock as td_mod
    def _boom(cfg, trees):
        raise ValidationError("broken link")
    monkeypatch.setattr(td_mod, "link_trees", _boom)

    _make_dirty(dock)
    dock._do_save()

    assert warnings, "expected a warning for the round-trip failure"
    assert list(tmp_path.glob("root.yaml.bak.*"))
    cfg, _ = load_config(str(root))  # still written
    assert [t.name for t in cfg.trees] == ["power_tree", "misc", "extra"]


def test_dirty_indicator_reflects_mark_dirty(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path, SAVE_TREES)
    assert dock.dirty_label.text() == ""
    _make_dirty(dock)
    assert "●" in dock.dirty_label.text()
    dock._do_save()
    assert dock.dirty_label.text() == ""


def test_save_without_root_is_a_noop(main_window):
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    dock._do_save()  # must not crash, must not write anywhere
    assert dock._dirty is False


# ── Phase 4: checkbox selection + Redraw ─────────────────────────────────

def test_redraw_selected_collects_checked_refs_and_calls_worker(
        main_window, tmp_path, monkeypatch):
    dock, _root = _dock_with(main_window, tmp_path)

    ams_item = dock._node_items["AMS1117_REG"]
    ams_item.setCheckState(0, Qt.CheckState.Checked)
    r_item = dock._node_items["R_AROUND"]
    r_item.setCheckState(0, Qt.CheckState.Checked)

    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, payload):
        captured["payload"] = payload
        return object()
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", fake_start)

    dock._on_redraw_selected()

    assert captured
    assert captured["payload"]["tree_name"] == "power_tree"
    assert captured["payload"]["selected_refs"] == {"AMS1117_REG", "R_AROUND"}
    assert captured["payload"]["trees"] is dock._trees


def test_redraw_selected_no_selection_shows_hint(main_window, tmp_path, monkeypatch):
    dock, _root = _dock_with(main_window, tmp_path)
    called = []
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", lambda *a, **k: called.append(a) or object())

    dock._on_redraw_selected()

    assert not called
    assert "Nothing selected" in dock.status_label.text()


# ── Read current position / Reread / Edit node (2026-08-27) ───────────────

class _FakeBoard:
    """A connection.board stand-in with a live .adapter — enough for
    _live_adapter() to return a non-None adapter in the reread/edit paths."""

    def __init__(self):
        self.adapter = object()


def _build_dialog(dock, tree, parent_node, existing=None, title="Add child"):
    """A _NodeDialog wired the same way _prompt_node wires it (cfg + a live
    adapter + the parent context), so the button's resolution can be tested
    directly without driving the modal exec()."""
    return _NodeDialog(
        dock, dock._all_ref_candidates(), dock._used_refs(), title,
        cfg=dock._cfg, adapter=object(), sheet_names={},
        tree=tree, parent_node=parent_node, existing=existing)


def test_context_menu_on_node_offers_reread_and_edit(main_window, tmp_path, monkeypatch):
    """The node context menu now carries the two new actions alongside the
    existing Add child/Add sibling/Delete/Rename/Move block. Offscreen item
    geometry can be degenerate, so force `_on_context_menu` to the node branch
    by stubbing itemAt to return the node item."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    ams_item = dock._node_items["AMS1117_REG"]
    monkeypatch.setattr(tree_widget, "itemAt", lambda pos: ams_item)
    actions = dict(_context_menu_actions(dock, ams_item, monkeypatch))
    assert "Reread current position" in actions
    assert "Edit node…" in actions
    assert "Add child" in actions
    assert "Add sibling" in actions
    assert "Delete node" in actions
    assert "Rename…" in actions
    assert "Move to…" in actions


def test_node_dialog_read_position_fills_xy_and_rotation(main_window, tmp_path, monkeypatch):
    """"Считать текущее положение" fills offset (Cartesian) + relative
    rotation from the live resolution relative to a known parent."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    parent = tree.nodes[0]  # AMS1117_REG

    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        lambda *a, **k: ((10.0, 5.0), 90.0))
    dlg = _build_dialog(dock, tree, parent)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))
    dlg.ref_combo.setCurrentText("C_OUT")
    assert dlg.read_position_button.isEnabled() is True

    dlg._on_read_position()

    assert dlg.offset_widget.x_edit.text() == "10.000"
    assert dlg.offset_widget.y_edit.text() == "5.000"
    assert dlg.rotation_edit.text() == "90.000"
    assert dlg.read_status_label.text() == ""


def test_node_dialog_read_position_point_kind_rotation_left_blank(
        main_window, tmp_path, monkeypatch):
    """A point-kind child has no rotation concept -> xy still fills, rotation
    stays blank, and a one-line status appears under the button (never a
    fabricated 0)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    parent = tree.nodes[0]

    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        lambda *a, **k: ((3.0, 7.0), None))
    dlg = _build_dialog(dock, tree, parent)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("point"))
    dlg.ref_combo.setCurrentText("PNT")

    dlg._on_read_position()

    assert dlg.offset_widget.x_edit.text() == "3.000"
    assert dlg.offset_widget.y_edit.text() == "7.000"
    assert dlg.rotation_edit.text() == ""
    assert "rotation not available" in dlg.read_status_label.text()


def test_node_dialog_read_position_warns_when_no_live_connection(
        main_window, tmp_path, monkeypatch):
    """adapter is None (not connected) -> a warning, and nothing is written
    to the offset fields (no silent partial state)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Add child", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree, parent_node=None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))
    dlg.ref_combo.setCurrentText("C_OUT")
    dlg._on_read_position()

    assert warnings
    assert dlg.offset_widget.x_edit.text() == ""
    assert dlg.rotation_edit.text() == ""


def test_reread_node_flow_overwrites_xy_rotation_and_marks_dirty(
        main_window, tmp_path, monkeypatch):
    """"Reread current position" overwrites an existing node's xy/rotation in
    place and marks the dock dirty (no confirmation)."""
    import gui.docks.trees_dock as td_mod
    main_window.connection.board = _FakeBoard()
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]  # AMS1117_REG, xy (5.0, 2.0), rotation 0.0
    node.rotation = 1.0

    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        lambda *a, **k: ((1.0, 2.0), 45.0))
    dock._reread_node_flow(tree, node)

    assert node.xy == (1.0, 2.0)
    assert node.polar is None
    assert node.rotation == 45.0
    assert dock._dirty is True


def test_reread_node_flow_resolution_failure_leaves_node_untouched(
        main_window, tmp_path, monkeypatch):
    """Error path (a ref that can't currently be resolved live) leaves the
    node's old values intact — no partial write — and does not mark dirty."""
    import gui.docks.trees_dock as td_mod
    main_window.connection.board = _FakeBoard()
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    node.rotation = 12.0
    before = (node.xy, node.polar, node.rotation)

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)

    def _boom(*a, **k):
        raise ValidationError("ref not on board")
    monkeypatch.setattr(td_mod, "_resolve_live_offset", _boom)

    dock._reread_node_flow(tree, node)

    assert (node.xy, node.polar, node.rotation) == before
    assert dock._dirty is False
    assert warnings


def test_edit_dialog_prefilled_and_own_ref_not_rejected(main_window, tmp_path, monkeypatch):
    """Editing a node WITHOUT changing its ref must not trip the "ref already
    used" check against itself (the §4 exclusion fix)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]  # AMS1117_REG

    monkeypatch.setattr(td_mod.QMessageBox, "warning", lambda *a, **k: None)
    dlg = _build_dialog(dock, tree, dock._find_parent(tree, node),
                        existing=node, title="Edit node")

    # Pre-filled from `existing`:
    assert dlg.kind_combo.currentData() == "clone"
    assert dlg.ref_combo.currentText() == "AMS1117_REG"
    assert dlg.offset_widget.x_edit.text() == "5.0"
    assert dlg.offset_widget.y_edit.text() == "2.0"
    assert dlg.rotation_edit.text() == "0.0"

    built = dlg.build_node()
    assert built is not None
    assert built.ref == "AMS1117_REG"


def test_edit_dialog_different_already_used_ref_still_rejected(
        main_window, tmp_path, monkeypatch):
    """Regression guard: the existing-ref exclusion must NOT let a DIFFERENT
    already-used ref through — this is exactly the boundary a naive
    `existing.ref` exclusion could get backwards."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]   # AMS1117_REG
    other = tree.nodes[1]  # R_AROUND

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _build_dialog(dock, tree, dock._find_parent(tree, node),
                        existing=node, title="Edit node")
    dlg.ref_combo.setCurrentText(other.ref)  # R_AROUND — used by another node

    assert dlg.build_node() is None
    assert warnings


def test_edit_node_flow_copies_fields_onto_existing_in_place(main_window, tmp_path, monkeypatch):
    """_edit_node_flow mutates the EXISTING node (doesn't swap identity) and
    marks the dock dirty."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    node.rotation = 1.0

    built = TreeNode(ref="AMS1117_REG", kind="clone", xy=(9.0, 8.0), polar=None,
                     rotation=77.0, name="new_label", group="g")
    monkeypatch.setattr(dock, "_prompt_node", lambda *a, **k: built)
    dock._edit_node_flow(tree, node)

    assert node.ref == "AMS1117_REG"
    assert node.xy == (9.0, 8.0)
    assert node.polar is None
    assert node.rotation == 77.0
    assert node.name == "new_label"
    assert node.group == "g"
    assert dock._dirty is True
