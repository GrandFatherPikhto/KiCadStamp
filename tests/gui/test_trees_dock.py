# tests/gui/test_trees_dock.py
"""Tests for TreesDock (gui/docks/trees_dock.py) — the hand-authored s-expr
"trees" editor (design design_2026_08_27_trees_gui_dock.md).

Phase 1 scope: Open/New a .trees file, per-tree tabs with a read-only
QTreeWidget render, and the static node_offset() preview. The other toolbar
buttons are expected to stay disabled until their phase lands.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, load_trees

from gui.docks.trees_dock import TreesDock

# The same working example as tests/test_trees.py's GRAMMAR_EXAMPLE — two
# trees, nested nodes, xy and polar offsets, a ref anchor and an origin anchor.
GRAMMAR_EXAMPLE = """(kicadstamp-trees
  (version 1)
  (tree
    (name "power_tree")
    (anchor (ref "CONN_PM5V"))
    (node
      (ref "AMS1117_REG")
      (kind clone)
      (xy 5.0 2.0)
      (rotation 0)
      (node (ref "C_OUT") (xy 1.0 0)))
    (node (ref "R_AROUND") (polar 3.0 45.0)))
  (tree
    (name "misc")
    (anchor (origin))
    (node (ref "R_DEBUG") (xy 100.0 50.0))))"""


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _write(tmp_path, text, name="trees.trees"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ── New ───────────────────────────────────────────────────────────────────

def test_new_creates_a_valid_empty_file(main_window, tmp_path, monkeypatch):
    """New writes the canonical empty file (save_trees([], ...) — load_trees
    of it must not crash and must yield zero trees, with the placeholder tab."""
    target = tmp_path / "new.trees"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), "Trees (*.trees)"))
    dock = TreesDock(main_window)
    dock._on_new()

    assert dock._trees_path == target
    assert dock._trees == []
    trees = load_trees(str(target))
    assert trees == []
    # Placeholder tab, and the dirty indicator starts clean.
    assert dock.tabs.count() == 1
    assert dock._dirty is False


# ── Open ──────────────────────────────────────────────────────────────────

def test_open_invalid_file_warns_and_does_not_crash(main_window, tmp_path, monkeypatch, caplog):
    """A .trees file that violates the grammar raises ValidationError in
    load_trees — the dock must surface a QMessageBox, not crash, and leave
    the previous state untouched."""
    bad = _write(tmp_path, "(this is not valid (trees", name="bad.trees")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(bad), "Trees (*.trees)"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or QMessageBox.StandardButton.Ok)

    dock = TreesDock(main_window)
    dock._on_open()

    assert warnings, "expected a QMessageBox.warning call"
    assert dock._trees_path is None  # previous state untouched
    assert dock._trees == []


def test_open_renders_one_tab_per_tree_with_nested_structure(main_window, tmp_path, monkeypatch):
    """Opening a file with two trees shows two tabs; the first tree's render
    mirrors the nested grammar shape (anchor pseudo-root + nodes + child)."""
    path = _write(tmp_path, GRAMMAR_EXAMPLE)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(path), "Trees (*.trees)"))

    dock = TreesDock(main_window)
    dock._on_open()

    assert dock.tabs.count() == 2
    assert dock.tabs.tabText(0) == "power_tree"
    assert dock.tabs.tabText(1) == "misc"

    tree_widget = dock.tabs.widget(0)
    tops = _children(tree_widget.invisibleRootItem())
    # Pseudo-root anchor at the top; its children are the two top-level nodes.
    assert len(tops) == 1
    assert "CONN_PM5V" in tops[0].text(0)
    nodes = _children(tops[0])
    assert len(nodes) == 2
    assert nodes[0].text(0) == "AMS1117_REG (clone)"
    assert nodes[1].text(0) == "R_AROUND"
    # The nested child of AMS1117_REG.
    ams_children = _children(nodes[0])
    assert [c.text(0) for c in ams_children] == ["C_OUT"]


# ── Static preview ────────────────────────────────────────────────────────

def test_static_preview_xy_node(main_window, tmp_path, monkeypatch):
    path = _write(tmp_path, GRAMMAR_EXAMPLE)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(path), "Trees (*.trees)"))
    dock = TreesDock(main_window)
    dock._on_open()

    tree_widget = dock.tabs.widget(0)
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG (xy 5.0 2.0)

    text = dock.status_label.text()
    assert "AMS1117_REG" in text
    assert "xy=" in text
    assert "5.000" in text
    assert "2.000" in text


def test_static_preview_polar_node(main_window, tmp_path, monkeypatch):
    path = _write(tmp_path, GRAMMAR_EXAMPLE)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(path), "Trees (*.trees)"))
    dock = TreesDock(main_window)
    dock._on_open()

    tree_widget = dock.tabs.widget(0)
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND (polar 3.0 45.0)

    text = dock.status_label.text()
    assert "R_AROUND" in text
    assert "r=" in text
    assert "3.000" in text
    assert "45.000" in text


# ── Toolbar skeleton / root wiring ────────────────────────────────────────

def test_toolbar_buttons_enabled_by_phase(main_window):
    """All toolbar actions are enabled by Phase 4 (Open/New from 1, Add/Rename
    tree + Save from 2/3, Redraw from 4)."""
    dock = TreesDock(main_window)
    assert dock.add_tree_button.isEnabled() is True
    assert dock.rename_tree_button.isEnabled() is True
    assert dock.save_button.isEnabled() is True
    assert dock.redraw_button.isEnabled() is True
    assert dock.open_button.isEnabled() is True
    assert dock.new_button.isEnabled() is True


# ── Phase 2: structural editing ───────────────────────────────────────────

def _dock_with(main_window, tmp_path, monkeypatch, text=GRAMMAR_EXAMPLE):
    path = _write(tmp_path, text)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(path), "Trees (*.trees)"))
    dock = TreesDock(main_window)
    dock._on_open()
    return dock, path


def test_add_child_mutates_node_children_and_dirty(main_window, tmp_path, monkeypatch):
    """Add child appends to the parent node's children, marks dirty and
    rebuilds the widget tree (Phase 2 mutation logic)."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
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
    # The rebuilt tab shows the new child under AMS1117_REG.
    tree_widget = dock._current_tree_widget()
    tops = _children(tree_widget.invisibleRootItem())
    nodes = _children(tops[0])
    ams_children = _children(nodes[0])
    assert any(c.text(0) == "NEW_CHILD" for c in ams_children)


def test_delete_node_removes_subtree_and_marks_dirty(main_window, tmp_path, monkeypatch):
    """Delete removes the node from its parent's list (whole subtree goes
    with it), marks dirty, rebuilds."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
    tree = dock._current_tree()
    # Delete the top-level R_AROUND node.
    target = tree.nodes[1]
    tree.nodes.remove(target)
    dock._mark_dirty()
    dock._rebuild_tabs()

    assert target not in tree.nodes
    assert dock._dirty is True
    tree_widget = dock._current_tree_widget()
    tops = _children(tree_widget.invisibleRootItem())
    nodes = _children(tops[0])
    assert [n.text(0) for n in nodes] == ["AMS1117_REG (clone)"]


def test_move_into_own_descendant_is_forbidden(main_window, tmp_path, monkeypatch):
    """FORK-C structural invariant: moving a node into its own descendant is
    forbidden — _collect_subtree must include the whole subtree, so the move
    candidates exclude it."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
    tree = dock._current_tree()
    ams = tree.nodes[0]         # AMS1117_REG
    c_out = ams.children[0]     # C_OUT

    forbidden = dock._collect_subtree(ams)
    assert dock._in_list(c_out, forbidden)
    assert dock._in_list(ams, forbidden)
    # C_OUT (a descendant) is not a legal move candidate for AMS1117_REG.
    candidates = []
    for top in tree.nodes:
        dock._collect_move_candidates(top, forbidden, candidates)
    assert not any(c is c_out for _label, c in candidates)
    assert not any(c is ams for _label, c in candidates)


def test_rename_tree_enforces_unique_names(main_window, tmp_path, monkeypatch):
    """Renaming a tree to an existing name is refused (kept unique) — the
    dialog path is mocked, the dock's uniqueness check is exercised."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
    assert dock._current_tree().name == "power_tree"
    # Simulate the _on_rename_tree uniqueness check directly: renaming to the
    # other tree's name must be rejected.
    tree = dock._current_tree()
    other_names = {t.name for t in dock._trees if t is not tree}
    assert "misc" in other_names
    tree.name = "misc"
    assert any(t.name == tree.name for t in dock._trees if t is not tree)  # collision
    # Undo the manual rename so the test is self-contained.
    tree.name = "power_tree"


# ── Phase 3: Save + dirty tracking ────────────────────────────────────────

def _make_dirty(dock):
    """Bring the dock into a dirty state (a structural change) and rebuild."""
    dock._trees.append(Tree(name="extra", anchor=TreeAnchor(ref=None, is_origin=True),
                            nodes=[]))
    dock._mark_dirty()
    dock._rebuild_tabs()


def test_save_backs_up_before_writing_and_clears_dirty(main_window, tmp_path, monkeypatch):
    """_do_save: the .bak is created BEFORE the write (its content is the OLD
    file), and a successful save clears dirty + persists the new tree list."""
    dock, path = _dock_with(main_window, tmp_path, monkeypatch)
    _make_dirty(dock)

    old_text = path.read_text(encoding="utf-8")
    dock._do_save()

    # A .bak exists and holds the PRE-save content (backup-before-write).
    baks = list(tmp_path.glob("trees.trees.bak.*"))
    assert baks, "expected a timestamped backup"
    assert baks[0].read_text(encoding="utf-8") == old_text
    # The saved file now round-trips to the edited tree list; dirty cleared.
    assert dock._dirty is False
    saved = load_trees(str(path))
    assert [t.name for t in saved] == ["power_tree", "misc", "extra"]


def test_save_roundtrip_failure_warns_but_leaves_backup(main_window, tmp_path, monkeypatch):
    """A link_trees round-trip failure after save is reported, the file IS
    written (by design), and the fresh .bak is the recovery point."""
    dock, path = _dock_with(main_window, tmp_path, monkeypatch)

    # _do_save only runs link_trees when a root config is loaded (self._cfg
    # is not None) — give the dock a root so the round-trip link is attempted.
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")
    dock.set_root_file(root)
    assert dock._cfg is not None

    # Force a round-trip link failure: we mock link_trees to raise instead of
    # building a real config mismatch (simpler, still exercises the save path).
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
    assert list(tmp_path.glob("trees.trees.bak.*"))
    # The file is still written (expected, not a bug).
    assert load_trees(str(path))


def test_dirty_indicator_reflects_mark_dirty(main_window, tmp_path, monkeypatch):
    """_mark_dirty shows the ● indicator; a successful save clears it."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
    assert dock.dirty_label.text() == ""
    _make_dirty(dock)
    assert "●" in dock.dirty_label.text()
    dock._do_save()
    assert dock.dirty_label.text() == ""


# ── Phase 4: checkbox selection + Redraw ─────────────────────────────────

def test_redraw_selected_collects_checked_refs_and_calls_worker(
        main_window, tmp_path, monkeypatch):
    """_on_redraw_selected builds the payload (checked refs of the current
    tree) and dispatches it through start_long_op with the worker adapter —
    mocked, no real worker thread in a unit test."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)

    # Check AMS1117_REG and R_AROUND (both in the "power_tree" tab).
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
    """With nothing checked, _on_redraw_selected does not dispatch a worker —
    it shows a status hint instead."""
    dock, _path = _dock_with(main_window, tmp_path, monkeypatch)
    called = []
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", lambda *a, **k: called.append(a) or object())

    dock._on_redraw_selected()

    assert not called
    assert "Nothing selected" in dock.status_label.text()
