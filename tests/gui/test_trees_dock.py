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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QTreeWidget

from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
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
    """A TreesDock pointed at a root config (s-expr) carrying the given trees:
    section — the current way trees get into the dock (set_root_file, no
    Open/New of a .trees file anymore)."""
    trees = trees if trees is not None else GRAMMAR_TREES
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(trees), encoding="utf-8")
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


# ── reload_trees (2026-09-01, plan extract_selection_as_tree.md) ────────────

def test_reload_trees_picks_up_external_write(main_window, tmp_path):
    """"Tools -> Extract tree..." saves through config_writer directly,
    bypassing TreesDock's own Save — reload_trees re-reads the root config so
    the new tree shows up without a root reassignment."""
    dock, root = _dock_with(main_window, tmp_path)
    assert [t.name for t in dock._trees] == ["power_tree", "misc"]

    # Simulate the external write: another writer appends a tree to the file.
    root.write_text(dict_to_sexp({
        "trees": [
            {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"}, "nodes": []},
            {"name": "misc", "anchor": {"origin": True}, "nodes": []},
            {"name": "from_selection", "anchor": {"role": "DAC"}, "nodes": []},
        ],
    }), encoding="utf-8")

    dock.reload_trees()

    assert [t.name for t in dock._trees] == ["power_tree", "misc", "from_selection"]
    assert dock.tabs.count() == 3


def test_reload_trees_preserves_dirty_edits(main_window, tmp_path):
    """reload_trees must NOT wipe unsaved edits (unlike set_root_file): a
    dirty buffer stays exactly as-is and externally-added trees are appended
    by name, so the "Extract tree..." tab appears without losing an in-progress
    hand edit."""
    dock, root = _dock_with(main_window, tmp_path)
    dock._mark_dirty()
    dock._trees[0].name = "renamed_locally"  # an unsaved edit
    root.write_text(dict_to_sexp({
        "trees": [
            {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"}, "nodes": []},
            {"name": "misc", "anchor": {"origin": True}, "nodes": []},
            {"name": "from_selection", "anchor": {"role": "DAC"}, "nodes": []},
        ],
    }), encoding="utf-8")

    dock.reload_trees()

    assert [t.name for t in dock._trees] == ["renamed_locally", "misc", "from_selection"]


def test_reload_trees_no_root_is_noop(main_window, tmp_path):
    """No root loaded -> reload_trees is a safe no-op (same guard as
    set_root_file/_do_save)."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    dock.reload_trees()
    assert dock._trees == []
    assert dock.tabs.count() == 1


def test_tree_widgets_have_an_explicit_minimum_width_floor(main_window, tmp_path):
    """2026-08-30, Denis: TreesDock couldn't be narrowed after being widened
    once — each QTreeWidget's natural minimumSizeHint() floored the dock's
    width, the same class of bug as LogDock's QPlainTextEdit height (commit
    9d8ddff), only horizontal. Every tab's tree widget must carry an explicit
    minimumWidth of 1 — NOT 0, which Qt treats as "unset" and silently falls
    back to minimumSizeHint() (see
    test_text_view_minimum_height_is_explicitly_overridden in test_log_panel)."""
    dock, _ = _dock_with(main_window, tmp_path)  # two real trees
    assert dock.tabs.count() == 2
    for i in range(dock.tabs.count()):
        widget = dock.tabs.widget(i)
        assert isinstance(widget, QTreeWidget)
        assert widget.minimumWidth() == 1


def test_placeholder_tree_widget_has_an_explicit_minimum_width_floor(main_window, tmp_path):
    """The "(no trees)" placeholder gets the same explicit width override, for
    consistency with a real tree's tab (set_root_file(None) -> placeholder)."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock.tabs.count() == 1
    widget = dock.tabs.widget(0)
    assert isinstance(widget, QTreeWidget)
    assert widget.minimumWidth() == 1


def test_set_root_file_broken_config_does_not_crash(main_window, tmp_path):
    """A root config whose trees: section is malformed raises ValidationError
    in load_config — the dock must not crash: trees stay empty, cfg stays None
    (Save's link_trees round-trip is skipped until a good root loads)."""
    root = tmp_path / "root.sexp"
    # A tree record missing its required name: — load_config raises
    # ValidationError on the malformed trees: section.
    root.write_text(
        "(kicadstamp-config\n"
        "  (trees\n"
        "    (tree\n"
        "      (anchor (ref \"A\"))\n"
        "      (nodes (node (ref \"B\") (xy 1 2)))\n"
        "    )\n"
        "  )\n"
        ")\n", encoding="utf-8")
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
    owns the root); Add/Rename/Delete tree + Save + Redraw selected/whole tree
    + the read-only Anchor position indicator are enabled."""
    dock = TreesDock(main_window)
    assert dock.add_tree_button.isEnabled() is True
    assert dock.rename_tree_button.isEnabled() is True
    assert dock.delete_tree_button.isEnabled() is True
    assert dock.redraw_button.isEnabled() is True
    assert dock.redraw_whole_button.isEnabled() is True
    assert dock.anchor_pos_button.isEnabled() is True
    assert not hasattr(dock, "open_button")
    assert not hasattr(dock, "new_button")


def _toolbar_layout(dock):
    """The toolbar QHBoxLayout — the first item of the container's QVBoxLayout."""
    return dock.widget().layout().itemAt(0).layout()


def test_toolbar_moves_secondary_actions_into_an_overflow_menu(main_window):
    """2026-08-30, Denis: TreesDock couldn't be narrowed after being widened —
    seven QPushButtons in one row never shrink below their sizeHint (the real
    width floor; the QTreeWidget setMinimumWidth(1) fix wasn't the place). The
    tree-management actions move into a "⋯" menu, leaving only the primary
    buttons + the menu trigger in the visible row."""
    dock = TreesDock(main_window)
    toolbar = _toolbar_layout(dock)
    visible = [toolbar.itemAt(i).widget() for i in range(toolbar.count())
               if toolbar.itemAt(i).widget() is not None]
    assert dock.redraw_button in visible
    assert dock.redraw_whole_button in visible
    # the secondary actions are reachable only through the "⋯" menu
    for button in (dock.add_tree_button, dock.rename_tree_button,
                   dock.delete_tree_button, dock.anchor_pos_button):
        assert button not in visible
    assert dock.more_button in visible
    menu = dock.more_button.menu()
    assert menu is not None
    texts = [a.text() for a in menu.actions()]
    assert "Add tree…" in texts
    assert "Rename tree…" in texts
    assert "Delete tree…" in texts
    assert "Anchor position" in texts


def test_more_menu_action_re_triggers_the_wrapped_button(main_window, monkeypatch):
    """The "⋯" menu's actions re-fire their own (hidden) QPushButton, so the
    existing handler/API wiring is untouched. `click` is patched at call time
    (the menu lambda resolves the attribute then), proving the routing."""
    dock = TreesDock(main_window)
    clicked = []
    monkeypatch.setattr(dock.add_tree_button, "click", lambda: clicked.append("add"))
    menu = dock.more_button.menu()
    action = next(a for a in menu.actions() if a.text() == "Add tree…")
    action.trigger()
    assert clicked == ["add"]


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


def test_unique_ref_auto_numbers_a_collision():
    """Phase 5.5 auto-numbering: ref_1, ref_2, ... for the first free variant."""
    assert TreesDock._unique_ref("R1", set()) == "R1"
    assert TreesDock._unique_ref("R1", {"R1"}) == "R1_1"
    assert TreesDock._unique_ref("R1", {"R1", "R1_1"}) == "R1_2"
    assert TreesDock._unique_ref("R1", {"OTHER"}) == "R1"


def test_add_node_auto_numbers_a_free_typed_colliding_ref(main_window, tmp_path, monkeypatch):
    """Phase 5.5: a NEW node whose free-typed ref (an external/refdes name,
    not a placeable record) collides with an existing node is auto-numbered
    (ref_1), so the next Save doesn't fatal with link_trees' "already has a
    node elsewhere". Placeable records keep the strict "(used)" check."""
    from PyQt6.QtWidgets import QDialog

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()  # power_tree: AMS1117_REG, R_AROUND
    built = TreeNode(ref="R_AROUND", kind="external", xy=(1.0, 1.0), polar=None,
                     rotation=0.0, name=None, group=None)
    monkeypatch.setattr(_NodeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_NodeDialog, "build_node", lambda self: built)

    dock._add_node_flow(tree)

    refs = [n.ref for n in tree.nodes]
    assert "R_AROUND" in refs      # the original node stays
    assert "R_AROUND_1" in refs    # the new collision is auto-numbered
    assert refs.count("R_AROUND_1") == 1


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


def test_anchor_dialog_origin_mode_returns_origin(main_window):
    """The historic "Origin (board 0,0)" mode still yields is_origin=True with
    every other field at its default — regression gate for the 6-mode rework."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "CL_A")])
    dlg.mode_combo.setCurrentIndex(0)  # "Origin (board 0,0)"
    dlg._accept()
    assert dlg._result == TreeAnchor(ref=None, is_origin=True, is_external=False)


def test_anchor_dialog_external_mode_carries_is_external(main_window):
    """The "External refdes" mode of _AnchorDialog must STORE is_external=True
    — otherwise the resolver cannot tell "external" from "config record" and
    the name collision returns (note_2026_08_28_tree_anchor_name_collision)."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "CL_A"), ("placement", "CL_B")])
    dlg.mode_combo.setCurrentIndex(2)  # "External refdes" (0=Origin, 1=Config record)
    dlg.ref_combo.setCurrentText("fpga")  # editable combo — free text is allowed
    dlg._accept()
    assert dlg._result == TreeAnchor(ref="fpga", is_origin=False, is_external=True)


def test_anchor_dialog_record_mode_stays_non_external(main_window):
    """Contrast: the "Config record" mode must NOT set is_external — a normal
    record anchor resolves against the config (guards the same regression)."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "CL_A"), ("placement", "CL_B")])
    dlg.mode_combo.setCurrentIndex(1)  # "Config record"
    dlg.ref_combo.setCurrentText("CL_A")
    dlg._accept()
    assert dlg._result == TreeAnchor(ref="CL_A", is_origin=False, is_external=False)


def test_anchor_dialog_auto_mode_returns_is_auto(main_window):
    """The new "Auto (derive from Entity's own cell)" mode must yield
    TreeAnchor(is_auto=True) with every other field at its default — the only
    GUI path to an auto anchor (2026-08-31 plan)."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "FPGA")])
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("auto"))
    dlg._accept()
    assert dlg._result == TreeAnchor(is_auto=True)


def test_anchor_dialog_role_mode_returns_role_anchor(main_window):
    """The new "Role" mode builds a role anchor: role required, sheet/cluster/
    pad optional, nothing else set (ref/is_origin/is_external/is_auto all off)."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [],
                        role_candidates=["FPGA", "R_FB"],
                        sheet_names={"S1": "s1.sex", "S2": "s2.sex"})
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("role"))
    dlg.role_edit.setCurrentText("FPGA")
    dlg.sheet_edit.setCurrentText("S1")
    dlg.cluster_edit.setCurrentText("CL_A")
    dlg.pad_edit.setText("A1")
    dlg._accept()
    assert dlg._result == TreeAnchor(role="FPGA", is_origin=False,
                                     anchor_sheet="S1", anchor_cluster="CL_A",
                                     anchor_pad="A1")


def test_anchor_dialog_role_mode_requires_role(main_window, monkeypatch):
    """An empty Role in "Role" mode must warn and NOT accept — never a silent
    role=None anchor (mirrors the node dialog's "Ref is required." gate)."""
    import gui.docks.trees_dock as td_mod
    from gui.docks.trees_dock import _AnchorDialog
    shown = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning", lambda *a, **k: shown.append(a))
    dlg = _AnchorDialog(main_window, [])
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("role"))
    dlg._accept()
    assert shown
    assert dlg._result is None


def test_anchor_dialog_point_mode_returns_point_anchor(main_window):
    """The new "Point" mode builds a point anchor from a cfg.points name; the
    combo is populated with the sorted points names (populate-don't-restrict)."""
    from types import SimpleNamespace
    from gui.docks.trees_dock import _AnchorDialog
    cfg = SimpleNamespace(points={"P2": object(), "P1": object()})
    dlg = _AnchorDialog(main_window, [], cfg=cfg)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("point"))
    assert _combo_texts(dlg.point_edit) == ["P1", "P2"]  # sorted names
    dlg.point_edit.setCurrentText("P1")
    dlg._accept()
    assert dlg._result == TreeAnchor(point="P1", is_origin=False)


def test_anchor_dialog_record_kind_filter_narrows_ref_combo(main_window):
    """Denis 2026-08-31: the Config-record ref combo is narrowed by record
    kind (Rule -> only rules, etc.). The filter is a picker aid only — the
    produced TreeAnchor is still a plain ref=name (the grammar has no kind)."""
    from gui.docks.trees_dock import _AnchorDialog
    candidates = [("clone", "CL_A"), ("clone", "SHARED"),
                  ("rule", "R_B"), ("rule", "SHARED"),
                  ("coordinate", "COORD_C"), ("point", "PNT_D")]
    dlg = _AnchorDialog(main_window, candidates)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("rule"))
    assert _combo_texts(dlg.ref_combo) == ["R_B", "SHARED"]
    dlg.ref_combo.setCurrentText("R_B")
    dlg._accept()
    assert dlg._result == TreeAnchor(ref="R_B", is_origin=False, is_external=False)


def test_anchor_dialog_all_kinds_prefixed_collisions(main_window):
    """"All kinds" shows every placeable name — a name shared by 2+ sections
    once per section as {kind}:{name}, itemData carrying (kind|None, name), so
    picking a prefixed entry auto-narrows the kind filter."""
    from gui.docks.trees_dock import _AnchorDialog
    candidates = [("clone", "CL_A"), ("clone", "SHARED"),
                  ("rule", "R_B"), ("rule", "SHARED"),
                  ("coordinate", "COORD_C"), ("point", "PNT_D")]
    dlg = _AnchorDialog(main_window, candidates)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    assert dlg.kind_combo.currentData() is None  # "All kinds" by default
    assert _combo_texts(dlg.ref_combo) == [
        "CL_A", "clone:SHARED", "R_B", "rule:SHARED", "COORD_C", "PNT_D",
    ]
    assert dlg.ref_combo.itemData(0) == (None, "CL_A")
    assert dlg.ref_combo.itemData(1) == ("clone", "SHARED")
    assert dlg.ref_combo.itemData(3) == ("rule", "SHARED")

    collision_idx = _combo_texts(dlg.ref_combo).index("rule:SHARED")
    dlg.ref_combo.setCurrentIndex(collision_idx)
    assert dlg.kind_combo.currentData() == "rule"   # auto-narrowed
    assert dlg.ref_combo.currentText() == "SHARED"  # clean name


# ── _AnchorDialog edit-mode prefill (2026-08-31) ───────────────────────────

def test_anchor_dialog_prefills_auto(main_window):
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [], existing=TreeAnchor(is_auto=True))
    assert dlg.mode_combo.currentData() == "auto"


def test_anchor_dialog_prefills_role(main_window):
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [], role_candidates=["FPGA"],
                        existing=TreeAnchor(role="FPGA", anchor_sheet="S1",
                                            anchor_cluster="CL_A", anchor_pad="A1"))
    assert dlg.mode_combo.currentData() == "role"
    assert dlg.role_edit.currentText() == "FPGA"
    assert dlg.sheet_edit.currentText() == "S1"
    assert dlg.cluster_edit.currentText() == "CL_A"
    assert dlg.pad_edit.text() == "A1"


def test_anchor_dialog_prefills_point(main_window):
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [], existing=TreeAnchor(point="P1"))
    assert dlg.mode_combo.currentData() == "point"
    assert dlg.point_edit.currentText() == "P1"


def test_anchor_dialog_prefills_record_with_kind(main_window):
    """A ref anchor whose name is unambiguous across sections pre-fills the
    kind filter too — so a subsequent Save edits the same record, not a blind
    "All kinds" list."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "FPGA"), ("rule", "R_FB")],
                        existing=TreeAnchor(ref="R_FB", is_external=False))
    assert dlg.mode_combo.currentData() == "record"
    assert dlg.kind_combo.currentData() == "rule"
    assert dlg.ref_combo.currentText() == "R_FB"


def test_anchor_dialog_prefills_external(main_window):
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [], existing=TreeAnchor(ref="U3", is_external=True))
    assert dlg.mode_combo.currentData() == "external"
    assert dlg.ref_combo.currentText() == "U3"


# ── Self-reference anchor guard — dialog filter (2026-08-31, plan §1/§2) ───

def _placement_node(ref):
    """A minimal top-level kind="placement" TreeNode — the shape a tree's own
    root Entity has for the self-reference guard."""
    return TreeNode(ref=ref, kind="placement", xy=None, polar=None,
                    rotation=0.0, name=None, group=None)


def _entity_refs(dlg):
    """The plain display names currently in the dialog's ref combo."""
    return {dlg.ref_combo.itemText(i) for i in range(dlg.ref_combo.count())}


def test_anchor_dialog_excludes_own_root_entity(main_window):
    """§1 of plan_2026_08_31_anchor_self_ref_guard: a tree whose single
    top-level node IS a placement record must not be offered that record as its
    own ref anchor (a ref anchor pointing at its own root Entity can never
    resolve). Other (non-self) Entities stay available."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dlg = _AnchorDialog(main_window,
                        [("placement", "fpga"), ("placement", "CL_A"),
                         ("rule", "R_FB")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    names = _entity_refs(dlg)
    assert "fpga" not in names
    assert "CL_A" in names


def test_anchor_dialog_empty_tree_keeps_self_entity(main_window):
    """§1 regression: an EMPTY tree (no top-level nodes) has no self-reference
    yet — the Entity is still a legitimate candidate and must stay."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True), nodes=[])
    dlg = _AnchorDialog(main_window, [("placement", "fpga"), ("placement", "CL_A")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    names = _entity_refs(dlg)
    assert "fpga" in names
    assert "CL_A" in names


def test_anchor_dialog_multiple_top_level_not_filtered(main_window):
    """§edge-case: with several top-level nodes there is no single "own root
    Entity" (and the auto-anchor is unreachable) — the dialog must not filter."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="multi", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga"), _placement_node("CL_A")])
    dlg = _AnchorDialog(main_window, [("placement", "fpga"), ("placement", "CL_A")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    names = _entity_refs(dlg)
    assert "fpga" in names
    assert "CL_A" in names


def test_anchor_dialog_non_placement_root_not_filtered(main_window):
    """§edge-case: the single top-level node is NOT kind=placement — there is
    no self-referencing Entity to guard."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="rule_root", anchor=TreeAnchor(is_auto=True),
                nodes=[TreeNode(ref="R_FB", kind="rule", xy=None, polar=None,
                                rotation=0.0, name=None, group=None)])
    dlg = _AnchorDialog(main_window, [("placement", "fpga"), ("rule", "R_FB")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    names = _entity_refs(dlg)
    assert "fpga" in names


def test_anchor_dialog_all_kinds_removes_self_ref(main_window):
    """§1: in "All kinds" the self-Entity (placement "fpga") disappears — the
    leftover "rule:fpga" record is no longer a cross-section collision (the
    placement entry was dropped), so it shows as a plain "fpga" and stays
    selectable: it is NOT the self-Entity."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dlg = _AnchorDialog(main_window,
                        [("placement", "fpga"), ("rule", "fpga"), ("rule", "R_FB")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    texts = [dlg.ref_combo.itemText(i) for i in range(dlg.ref_combo.count())]
    assert "placement:fpga" not in texts
    assert "fpga" in texts
    assert "R_FB" in texts


def test_anchor_dialog_hint_when_self_ref_empties_entities(main_window):
    """§2: when the ONLY Entity candidate was the tree's own root Entity (so
    the Entity section empties BECAUSE of the self-ref exclusion), a non-modal
    hint points at the Auto mode instead of a bare empty combo."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dlg = _AnchorDialog(main_window, [("placement", "fpga"), ("rule", "R_FB")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    assert not dlg.hint_label.isHidden()
    assert "fpga" in dlg.hint_label.text()


def test_anchor_dialog_hint_hidden_when_other_entity_remains(main_window):
    """§2 regression: the hint must NOT show when the Entity section still has
    a usable (non-self) candidate — the filter is working, the list isn't a
    dead end."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dlg = _AnchorDialog(main_window, [("placement", "fpga"), ("placement", "CL_A")],
                        tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("record"))
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    assert dlg.hint_label.isHidden()


def test_anchor_dialog_hint_hidden_outside_record_mode(main_window):
    """§2 regression: outside the Config-record mode the hint is never shown
    (e.g. Auto — the very switch the hint suggests — must not carry it)."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dlg = _AnchorDialog(main_window, [("placement", "fpga")], tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("auto"))
    assert dlg.hint_label.isHidden()


# ── Self-reference save guard (_enforce_no_self_ref, plan §3) ──────────────

def _guard_dock(main_window, tree):
    """A TreesDock with no root file, holding exactly the given tree — enough
    for the pure _enforce_no_self_ref mutation tests (no disk IO)."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    dock._trees = [tree]
    return dock


def test_save_guard_switches_self_ref_anchor_to_auto(main_window):
    """§3: a tree whose explicit ref anchor points at its OWN root placement
    node (the dialog-bypass combination: anchor set while empty, then the root
    added) is silently switched to an Auto anchor by _enforce_no_self_ref."""
    tree = Tree(name="fpga_tree",
                anchor=TreeAnchor(ref="fpga", is_origin=False, is_external=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.is_auto is True
    assert tree.anchor.ref is None


def test_save_guard_does_not_touch_different_entity_anchor(main_window):
    """§edge-case: (ref X) where X is a DIFFERENT Entity than the tree's root —
    the normal working Case 1 — must not be touched."""
    tree = Tree(name="t", anchor=TreeAnchor(ref="fpga", is_origin=False),
                nodes=[_placement_node("CL_A")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor == TreeAnchor(ref="fpga", is_origin=False)


def test_save_guard_does_not_touch_external_self_named_anchor(main_window):
    """§edge-case: (ref "fpga") (external) with a placement root "fpga" is NOT
    a self-reference (external is a live refdes, never an Entity record)."""
    tree = Tree(name="t", anchor=TreeAnchor(ref="fpga", is_origin=False,
                                            is_external=True),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor == TreeAnchor(ref="fpga", is_origin=False, is_external=True)


def test_save_guard_does_not_touch_auto_anchor(main_window):
    """§edge-case: an already-auto anchor has nothing to replace."""
    tree = Tree(name="t", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.is_auto is True


def test_save_guard_does_not_touch_origin_anchor(main_window):
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.is_origin is True


def test_save_guard_does_not_touch_role_anchor(main_window):
    tree = Tree(name="t", anchor=TreeAnchor(role="FPGA", is_origin=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.role == "FPGA"


def test_save_guard_does_not_touch_point_anchor(main_window):
    tree = Tree(name="t", anchor=TreeAnchor(point="P1", is_origin=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.point == "P1"


def test_save_guard_does_not_touch_multiple_top_level(main_window):
    """§edge-case: several top-level nodes mean no single root Entity — the
    auto-anchor is unreachable, so there is nothing to switch to (leave it)."""
    tree = Tree(name="t", anchor=TreeAnchor(ref="fpga", is_origin=False),
                nodes=[_placement_node("fpga"), _placement_node("CL_A")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor == TreeAnchor(ref="fpga", is_origin=False)


def test_save_guard_notifies_via_status_bar(main_window):
    """§3: the auto-switch is non-intrusive but not silent — a status-bar notice
    naming the tree and the offending ref is shown (never a modal)."""
    tree = Tree(name="fpga_tree",
                anchor=TreeAnchor(ref="fpga", is_origin=False, is_external=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert "fpga_tree" in dock.status_label.text()
    assert "fpga" in dock.status_label.text()


def test_save_guard_roundtrip_yields_auto_anchor(main_window):
    """§3 round-trip: after the auto-switch, tree_to_dict omits the anchor key
    and load_tree recovers is_auto=True — the exact path _do_save writes to
    disk, and the same behavior as a hand-authored auto anchor."""
    from kicadstamp.config import load_tree
    from kicadstamp.trees import tree_to_dict
    tree = Tree(name="fpga_tree",
                anchor=TreeAnchor(ref="fpga", is_origin=False, is_external=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    reloaded = load_tree(tree_to_dict(tree))
    assert reloaded.anchor.is_auto is True


# ── Anchor pseudo-root label (_anchor_label / _render_tree) ────────────────

def test_anchor_label_all_modes_never_none():
    """Every TreeAnchor mode renders a human-readable label with NO "None"
    (auto/role/point carry ref=None — the pre-2026-08-31 render showed '⚓ None')."""
    from gui.docks.trees_dock import _anchor_label
    cases = [
        (TreeAnchor(is_origin=True), "origin"),
        (TreeAnchor(is_auto=True), "auto"),
        (TreeAnchor(role="FPGA"), "role"),
        (TreeAnchor(role="FPGA", anchor_sheet="S1", anchor_cluster="CL_A",
                    anchor_pad="A1"), "S1"),
        (TreeAnchor(point="P1"), "point"),
        (TreeAnchor(ref="CONN_PM5V"), "CONN_PM5V"),
        (TreeAnchor(ref="U3", is_external=True), "external"),
    ]
    for anchor, needle in cases:
        label = _anchor_label(anchor)
        assert "None" not in label
        assert needle in label


def test_render_tree_auto_anchor_label(main_window, tmp_path):
    """A tree with NO (anchor ...) loads as is_auto and its pseudo-root renders
    '⚓ (auto)' — never '⚓ None' (2026-08-31 gap)."""
    trees = {"trees": [{"name": "t", "nodes": []}]}  # no anchor key -> auto
    dock, _root = _dock_with(main_window, tmp_path, trees)
    tree_widget = dock.tabs.widget(0)
    tops = _children(tree_widget.invisibleRootItem())
    assert len(tops) == 1
    assert "auto" in tops[0].text(0)
    assert "None" not in tops[0].text(0)


def test_render_tree_role_anchor_label(main_window, tmp_path):
    """A role anchor with a sheet narrow renders role + sheet in the label."""
    trees = {"trees": [{"name": "t", "anchor": {"role": "FPGA", "sheet": "S1"},
                        "nodes": []}]}
    dock, _root = _dock_with(main_window, tmp_path, trees)
    label = _children(dock.tabs.widget(0).invisibleRootItem())[0].text(0)
    assert "role" in label and "FPGA" in label and "S1" in label
    assert "None" not in label


def test_rename_tree_enforces_unique_names(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    assert dock._current_tree().name == "power_tree"
    tree = dock._current_tree()
    other_names = {t.name for t in dock._trees if t is not tree}
    assert "misc" in other_names
    tree.name = "misc"
    assert any(t.name == tree.name for t in dock._trees if t is not tree)  # collision
    tree.name = "power_tree"  # undo, self-contained


# ── Whole-tree Delete (2026-08-27) ────────────────────────────────────────

def test_delete_tree_removes_from_list_and_marks_dirty(main_window, tmp_path, monkeypatch):
    """Confirming the Delete tree… toolbar button removes the CURRENT tree
    from self._trees, marks the dock dirty and rebuilds one fewer tab (the
    deletion itself writes nothing — Save persists it, like Add/Rename)."""
    dock, _root = _dock_with(main_window, tmp_path)  # power_tree + misc
    assert len(dock._trees) == 2
    assert dock.tabs.count() == 2
    dock.tabs.setCurrentIndex(0)  # power_tree

    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dock.delete_tree_button.click()

    assert [t.name for t in dock._trees] == ["misc"]
    assert dock._dirty is True
    assert dock.tabs.count() == 1
    assert dock.tabs.tabText(0) == "misc"


def test_delete_tree_cancel_keeps_it(main_window, tmp_path, monkeypatch):
    """Declining (confirm=No) keeps the tree and does NOT touch _dirty (still
    False — a cancelled deletion must not mark unsaved state)."""
    dock, _root = _dock_with(main_window, tmp_path)
    assert dock._dirty is False

    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    dock.delete_tree_button.click()

    assert [t.name for t in dock._trees] == ["power_tree", "misc"]
    assert dock._dirty is False
    assert dock.tabs.count() == 2


def test_delete_tree_no_current_tree_is_noop(main_window, monkeypatch):
    """With no trees loaded (placeholder tab) the button must not crash and
    must not even open a confirmation dialog."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock._trees == []

    import gui.docks.trees_dock as td_mod
    called = []
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: called.append(a) or QMessageBox.StandardButton.Yes)
    dock.delete_tree_button.click()

    assert called == []
    assert dock._dirty is False


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

    baks = list(tmp_path.glob("root.sexp.bak.*"))
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
    assert list(tmp_path.glob("root.sexp.bak.*"))
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


def test_collect_tree_refs_returns_all_refs_dfs():
    """§5 (plan_2026_08_29_fork1_rigid_redraw_override.md): collect_tree_refs
    gathers EVERY node ref of a Tree (parent-before-child DFS), independent of
    any checkbox/UI state — the "Redraw whole tree" selection source."""
    from gui.docks.trees_dock import collect_tree_refs
    tree = Tree(
        name="power_tree", anchor=TreeAnchor(ref="CONN_PM5V", is_origin=False),
        nodes=[
            TreeNode(ref="AMS1117_REG", kind="clone", xy=(5.0, 2.0), polar=None,
                     rotation=0.0, name=None, group=None,
                     children=[TreeNode(ref="C_OUT", kind=None, xy=(1.0, 0.0),
                                        polar=None, rotation=0.0, name=None,
                                        group=None, children=[])]),
            TreeNode(ref="R_AROUND", kind=None, xy=None, polar=(3.0, 45.0),
                     rotation=0.0, name=None, group=None, children=[]),
        ])
    assert collect_tree_refs(tree) == ["AMS1117_REG", "C_OUT", "R_AROUND"]


def test_redraw_whole_tree_collects_all_refs_and_calls_worker(
        main_window, tmp_path, monkeypatch):
    """§5: "Redraw whole tree" collects ALL node refs DIRECTLY from the Tree
    (parent-before-child, no reliance on checkbox state) and calls the same
    run_curated_tree_redraw_worker with the full set — identical outcome to
    manually checking every box + "Redraw selected"."""
    dock, _root = _dock_with(main_window, tmp_path)

    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, payload):
        captured["payload"] = payload
        return object()
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", fake_start)

    dock._on_redraw_whole_tree()

    assert captured
    assert captured["payload"]["tree_name"] == "power_tree"
    assert captured["payload"]["selected_refs"] == {"AMS1117_REG", "C_OUT", "R_AROUND"}
    assert captured["payload"]["trees"] is dock._trees


def test_refresh_anchor_live_position_origin_shows_trivial(main_window, tmp_path):
    """§5.1: an origin anchor is trivially (0,0)/0° — shown WITHOUT any live
    board read (no IPC needed)."""
    dock, _root = _dock_with(main_window, tmp_path, trees={"trees": [
        {"name": "misc", "anchor": {"origin": True},
         "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [100.0, 50.0]}]}]})
    dock._refresh_anchor_live_position()
    assert "(0, 0) mm" in dock.anchor_pos_label.text()
    assert "0°" in dock.anchor_pos_label.text()


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


# ── clone + anchor_point: the KeyError regression (2026-08-27), superseded by
#    the lazy-resolution fix (bug #6, 2026-08-31) ───────────────────────────

# A root config whose only clone_placement is anchored via anchor_point to a
# points: entry. Bug #6 made ClonePositionCalculator._resolve_anchor resolve
# its anchor_point LAZILY on demand (resolve_point_chain) even when the caller
# (here: the ad-hoc GUI read) passes an EMPTY resolved_points dict — so the
# live read now SUCCEEDS. The point is xy-literal, resolvable without any live
# board, so the tests run with a bare object() adapter.
ANCHOR_POINT_CFG = {
    "points": {"Origin": {"xy": [10.0, 20.0]}},
    "clone_placements": [
        {"name": "CL_AP", "cluster": "c", "cell": "t", "xy": [1.0, 2.0],
         "anchor_point": "Origin"},
    ],
    "trees": [
        {"name": "t1", "anchor": {"origin": True}, "nodes": []},
    ],
}

# The same clone+anchor_point record as the tree's own REF-ANCHOR, with a
# normal node under it — for the Reread path (an anchor is not FORK-1-checked,
# so it CAN legitimately reference a clone+anchor_point record).
ANCHOR_POINT_ANCHOR_CFG = {
    "points": {"Origin": {"xy": [10.0, 20.0]}},
    "clone_placements": [
        {"name": "CL_AP", "cluster": "c", "cell": "t", "xy": [1.0, 2.0],
         "anchor_point": "Origin"},
        {"name": "CL_OK", "cluster": "c2", "cell": "t", "xy": [5.0, 5.0]},
    ],
    "trees": [
        {"name": "t1", "anchor": {"ref": "CL_AP"},
         "nodes": [{"ref": "CL_OK"}]},
    ],
}

# A tree whose PARENT node is a kind="placement" record (an Entity). An Entity
# carries NO record-level position — its live position is resolved from the
# TREE that places it. Here t1 (origin anchor) places BOTH ENT_A and ENT_B at
# node offset 0, so the parent's live position fully resolves (the absolute
# origin) — "Read current position" for ENT_B under the ENT_A parent fills the
# offset instead of warning (plan_2026_08_31_read_position_entity_parent_live_
# resolve.md). Before that plan the placement branch artificially refused ANY
# Entity parent (the old crash-plan AssertionError -> ValidationError).
ENTITY_PARENT_CFG = {
    "entities": [
        {"name": "ENT_A", "cell": "c"},
        {"name": "ENT_B", "cell": "c"},
    ],
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "ENT_A", "kind": "placement",
                    "children": [{"ref": "ENT_B", "kind": "placement"}]}]},
    ],
}

# A tree whose (ref "ENT_A") ANCHOR resolves to an Entity that NO tree node
# PLACES (the Entity exists in config, the anchor references it, but no
# kind="placement" node anywhere references ENT_A) — a genuinely unresolvable
# Entity parent. The live read must keep failing with the materializer's own
# "not placed in any tree" text as a warning (fields untouched), never a silent
# guess or a crash.
UNPLACED_ENTITY_PARENT_CFG = {
    "entities": [
        {"name": "ENT_A", "cell": "c"},
    ],
    "clone_placements": [
        {"name": "CL_X", "cluster": "c", "cell": "t", "xy": [5.0, 5.0]},
    ],
    "trees": [
        {"name": "t1", "anchor": {"ref": "ENT_A"},
         "nodes": [{"ref": "CL_X"}]},
    ],
}

# The child Entity (fpga_flash) is NOT a placement node in any tree (its node
# would be saved by this very dialog), but its cell has a single zero-offset
# (local 0,0) component with role "FPGA" — the own-zero-slot fallback
# (plan_2026_08_31_entity_live_position_zero_slot_fallback.md): "Read current
# position" for it must resolve from that zero-slot role, not warn.
ZERO_SLOT_ENTITY_CFG = {
    "entities": [
        {"name": "ENT_A", "cell": "c"},
        {"name": "fpga_flash", "cell": "f"},
    ],
    "cells": {
        "f": {"components": [{"role": "FPGA"}]},
    },
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "ENT_A", "kind": "placement"}]},
    ],
}


def test_read_position_clone_anchor_point_resolves_on_demand(
        main_window, tmp_path, monkeypatch):
    """Bug #6 gate (GUI): a clone-kind ref anchored via anchor_point IS
    live-resolvable by the ad-hoc GUI read now (ClonePositionCalculator.
    _resolve_anchor resolves the point lazily, not from a pre-populated
    resolved_points dict) — the Read-position dialog fills the offset from the
    point's position (10,20) + the clone's own shift (1,2) = (11,22) and does
    NOT warn. Does NOT mock _resolve_live_offset — exercises the REAL path."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path, ANCHOR_POINT_CFG)
    tree = dock._current_tree()

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _build_dialog(dock, tree, None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))
    dlg.ref_combo.setCurrentText("CL_AP")

    dlg._on_read_position()  # must not raise

    assert not warnings
    assert dlg.offset_widget.x_edit.text() == "11.000"
    assert dlg.offset_widget.y_edit.text() == "22.000"
    assert dlg.rotation_edit.text() == "0.000"


def test_reread_node_flow_clone_anchor_point_anchor_resolves_on_demand(
        main_window, tmp_path, monkeypatch):
    """Bug #6 gate (GUI): the tree's own ref-anchor resolving to a
    clone+anchor_point record is live-resolvable on Reread too — the node is
    rewritten from the point-anchored parent (CL_AP = Origin(10,20)+shift(1,2)
    = (11,22)) and the child's own absolute position (CL_OK (5,5)): offset
    (-6,-17), and the dock becomes dirty. Same real-path (no _resolve_live_offset
    mock)."""
    import gui.docks.trees_dock as td_mod
    main_window.connection.board = _FakeBoard()
    dock, _root = _dock_with(main_window, tmp_path, ANCHOR_POINT_ANCHOR_CFG)
    tree = dock._current_tree()
    node = tree.nodes[0]  # CL_OK
    node.rotation = 3.0

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._reread_node_flow(tree, node)  # must not raise

    assert not warnings
    assert node.xy == (-6.0, -17.0)
    assert node.polar is None
    assert node.rotation == 0.0
    assert dock._dirty is True


def test_node_dialog_read_position_entity_parent_resolves_offset(
        main_window, tmp_path, monkeypatch):
    """plan_2026_08_31_read_position_entity_parent_live_resolve.md: an Entity
    PARENT placed by a resolvable tree (here: the origin-anchored t1, so its
    live position is the absolute origin + the node offset 0) NOW RESOLVES —
    the read fills the offset instead of warning. Both ENT_A (the parent) and
    ENT_B (the child) are placed by t1 at node offset 0 -> offset (0,0),
    rotation 0. Exercises the REAL _resolve_live_offset path (no mock); the
    dialog must not raise and must not warn."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path, ENTITY_PARENT_CFG)
    tree = dock._current_tree()
    parent = tree.nodes[0]  # ENT_A — a kind="placement" Entity node

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _build_dialog(dock, tree, parent)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    dlg.ref_combo.setCurrentText("ENT_B")

    dlg._on_read_position()  # must not raise (the old AssertionError used to escape)

    assert not warnings
    assert dlg.offset_widget.x_edit.text() == "0.000"
    assert dlg.offset_widget.y_edit.text() == "0.000"
    assert dlg.rotation_edit.text() == "0.000"


def test_node_dialog_read_position_unplaced_entity_parent_warns(
        main_window, tmp_path, monkeypatch):
    """Regression: an Entity PARENT that NO tree places (the config tree anchors
    on it via (ref ...) but no kind="placement" node references it anywhere) is
    genuinely not live-resolvable — the read shows the materializer's own
    _EntityAnchorError text ("not placed in any tree") as a QMessageBox warning
    and leaves the offset/rotation fields untouched. The fatal is preserved for
    the truly unresolvable case; only the resolvable-Entity-parent case no
    longer warns (see the resolvable test above)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path, UNPLACED_ENTITY_PARENT_CFG)
    tree = dock._current_tree()

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _build_dialog(dock, tree, None)  # parent = the tree's (ref "ENT_A") anchor
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))
    dlg.ref_combo.setCurrentText("CL_X")

    dlg._on_read_position()  # must not raise

    assert warnings
    assert any("not placed in any tree" in str(w[2]) for w in warnings)
    assert dlg.offset_widget.x_edit.text() == ""
    assert dlg.offset_widget.y_edit.text() == ""
    assert dlg.rotation_edit.text() == ""


def test_node_dialog_read_position_unplaced_entity_child_zero_slot_resolves(
        main_window, tmp_path, monkeypatch):
    """Denis's live repro (plan_2026_08_31_entity_live_position_zero_slot_
    fallback.md): the CHILD is fpga_flash — a kind="placement" Entity that is
    NOT (yet) a placement node in any tree (its node would be saved by this
    very dialog), but whose cell has a single zero-offset (local 0,0)
    component with role "FPGA". "Read current position" must resolve the
    child's OWN live position from that zero-slot role (mock board: Role=FPGA
    at (30,40)) and fill the offset relative to the parent ENT_A (origin-
    anchored tree, absolute (0,0)) — (30,40), rotation 0 — with NO warning.
    Exercises the REAL _resolve_live_offset path (no mock)."""
    import gui.docks.trees_dock as td_mod
    from unittest.mock import MagicMock

    from kipy.board_types import FootprintInstance

    from kicadstamp.constants import ROLE_FIELD_NAME
    from kicadstamp.domain.geometry import Vector2

    dock, _root = _dock_with(main_window, tmp_path, ZERO_SLOT_ENTITY_CFG)
    tree = dock._current_tree()
    parent = tree.nodes[0]  # ENT_A — a kind="placement" Entity node placed by t1

    fp = MagicMock(spec=FootprintInstance)
    fp.ref = "IC1"
    fp._role = "FPGA"
    fp.position = Vector2.from_xy_mm(30.0, 40.0)
    fp.angle_deg = 0.0
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = (
        lambda f, name: getattr(f, "_role", None) if name == ROLE_FIELD_NAME else None)
    adapter.get_selected_items.return_value = []

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _build_dialog(dock, tree, parent)
    dlg._adapter = adapter
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("placement"))
    dlg.ref_combo.setCurrentText("fpga_flash")

    dlg._on_read_position()  # must not raise

    assert not warnings
    assert dlg.offset_widget.x_edit.text() == "30.000"
    assert dlg.offset_widget.y_edit.text() == "40.000"
    assert dlg.rotation_edit.text() == "0.000"


# NOTE: a clone whose anchor_point names a point ABSENT from cfg.points is
# rejected already at CONFIG LOAD time (config/loader.py: "anchor_point
# 'Origin' not found in points") — such a config can never reach the dock's
# live read, so there is no GUI read-time warning test for it here. The
# missing-point path of the lazy resolve (reachable only for programmatically
# built configs) is covered in tests/test_anchor_point_consumers.py and
# tests/test_entity_placement.py.


# ── Denis's live case 2026-08-27: FORK-1 must not block a passive live read ──

# A new (unsaved) tree that already holds CH0_DAC_BUF as a node, while the
# record's config entry STILL carries its legacy pre-trees inline anchor
# (anchor_role/anchor_sheet/anchor_cluster: FPGA). Adding a SECOND node
# (CH1_DAC_BUF) and pressing "Read current position" used to fail with
# "Node 'CH0_DAC_BUF' is placed by a tree but its own config record already
# has an inline anchor (anchor_role)" — because _linked_base_for() ran a FULL
# link_trees(cfg, [tree]) that FORK-1-validates EVERY node, not just the one
# being read. The read is a passive live-board lookup, not a config-computed
# position, so it must resolve regardless.
DENIS_CFG = {
    "clone_placements": [
        {"name": "CH0_DAC_BUF", "cluster": "DAC_BUF", "cell": "dac_buf",
         "xy": [0.0, 25.0], "anchor_role": "FPGA", "anchor_sheet": "FPGA",
         "anchor_cluster": "FPGA"},
        {"name": "CH1_DAC_BUF", "cluster": "DAC_BUF", "cell": "dac_buf",
         "xy": [25.0, 0.0]},
    ],
    "trees": [
        {"name": "10CL06", "anchor": {"origin": True},
         "nodes": [{"ref": "CH0_DAC_BUF", "kind": "clone"}]},
    ],
}


def test_linked_base_for_anchor_ignores_existing_node_inline_anchor(
        main_window, tmp_path):
    """Regression 2026-08-27: resolving the base for a NEW top-level node
    (parent_node=None) must NOT run link_trees over the whole tree — an
    EXISTING node whose record carries a legacy inline anchor (CH0_DAC_BUF
    with anchor_role) used to FORK-1-fail the whole link and block the read.
    The anchor base resolves standalone now (origin here)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path, DENIS_CFG)
    tree = dock._current_tree()
    assert [n.ref for n in tree.nodes] == ["CH0_DAC_BUF"]

    ref, record, is_origin = td_mod._linked_base_for(dock._cfg, tree, None)
    assert is_origin is True
    assert ref is None
    assert record is None


def test_resolve_live_offset_reads_new_ref_despite_existing_node_inline_anchor(
        main_window, tmp_path, monkeypatch):
    """Regression 2026-08-27 (Denis's exact flow): "Read current position" for
    CH1_DAC_BUF — a SECOND, brand-new node added to a tree that already holds
    CH0_DAC_BUF (legacy anchor_role) — resolves through the live resolvers.
    The unrelated node's FORK-1 conflict must not block the passive read.
    link_trees() itself still rejects the conflict (test_link_trees.py)."""
    import gui.docks.trees_dock as td_mod
    from kicadstamp.domain.geometry import Vector2
    from kicadstamp.utils.units import MM

    dock, _root = _dock_with(main_window, tmp_path, DENIS_CFG)
    tree = dock._current_tree()

    monkeypatch.setattr(
        td_mod, "resolve_base_live_position",
        lambda adapter, cfg, ref, record, resolved_points, sheet_names:
        Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)))
    monkeypatch.setattr(
        td_mod, "resolve_base_rotation_deg",
        lambda adapter, cfg, ref, record, sheet_names: 0.0)

    offset_mm, rotation = td_mod._resolve_live_offset(
        dock._cfg, object(), {}, tree, None, "CH1_DAC_BUF", "clone")

    assert offset_mm == (10.0, 20.0)  # relative to the origin anchor
    assert rotation == 0.0


# ── Kind-filtered "Ref:" combo (plan_2026_08_29_trees_node_kind_filtered_combo.md) ──

# A root config with records in all 4 placeable sections, INCLUDING a name
# collision between sections (SHARED exists as BOTH a clone and a rule) — the
# case the node dialog's auto mode must show prefixed ({kind}:{name}).
KIND_FILTER_CFG = {
    "clone_placements": [
        {"name": "CL_A", "cluster": "c", "cell": "t", "xy": [1.0, 2.0]},
        {"name": "SHARED", "cluster": "c2", "cell": "t", "xy": [3.0, 4.0]},
    ],
    "chains": [
        {"name": "R_B", "net": "+3V3", "anchor_role": "FPGA", "spokes": []},
        {"name": "SHARED", "net": "+5V", "anchor_role": "FPGA", "spokes": []},
    ],
    "coordinate_placements": [
        {"name": "COORD_C", "cluster": "CHAN", "role": "R",
         "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0},
    ],
    "points": {"PNT_D": {"xy": [2.0, 2.0]}},
    "trees": [
        {"name": "t1", "anchor": {"origin": True}, "nodes": []},
    ],
}


def _combo_texts(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_all_ref_candidates_returns_kind_name_pairs(main_window, tmp_path):
    """_all_ref_candidates now returns (kind, name) pairs in build_records'
    stable section order, WITHOUT dedup by name — a name shared by two sections
    appears once per section (record_key distinguishes them)."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    assert dock._all_ref_candidates() == [
        ("clone", "CL_A"), ("clone", "SHARED"),
        ("chain", "R_B"), ("chain", "SHARED"),
        ("coordinate", "COORD_C"), ("point", "PNT_D"),
    ]


def test_all_ref_candidates_empty_without_root(main_window):
    """No root config -> no candidates (and no crash) — the anchor/name helper
    dedups a colliding name to a single entry."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock._all_ref_candidates() == []
    assert dock._all_ref_names() == []


# ── refresh_ref_candidates (plan_2026_08_31_trees_dock_stale_after_entity_add.md) ──

def test_refresh_ref_candidates_preserves_dirty_trees_and_sees_new_entity(
        main_window, tmp_path):
    """Denis's complaint: the Trees dock saw a new Entity/Cell/... only after
    an app restart. The lightweight refresh re-reads cfg/ctx from the SAME
    root so the dialogs' ref candidates see a config change — but, unlike
    set_root_file, must NEVER touch already-loaded/edited trees or the dirty
    flag (a full reset there would silently destroy an in-progress tree
    edit)."""
    dock, root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    assert ("clone", "BRAND_NEW_ENTITY") not in dock._all_ref_candidates()

    # An unsaved edit made directly on the loaded tree (not through Save) —
    # exactly the state a full set_root_file reset would wipe.
    tree = dock._trees[0]
    tree.nodes.append(TreeNode(ref="DIRTY_NODE", kind="external", xy=(9.0, 9.0),
                               polar=None, rotation=0.0, name=None,
                               group=None, children=[]))
    dock._mark_dirty()
    trees_before = [(t.name, [n.ref for n in t.nodes]) for t in dock._trees]
    assert dock._dirty is True

    # A new Entity lands in the config file AFTER the dock was loaded.
    changed = dict(KIND_FILTER_CFG)
    changed["clone_placements"] = KIND_FILTER_CFG["clone_placements"] + [
        {"name": "BRAND_NEW_ENTITY", "cluster": "c3", "cell": "t",
         "xy": [5.0, 6.0]}]
    root.write_text(dict_to_sexp(changed), encoding="utf-8")

    dock.refresh_ref_candidates()

    # Trees + dirty state are untouched...
    assert dock._dirty is True
    assert [(t.name, [n.ref for n in t.nodes]) for t in dock._trees] == trees_before
    # ...while the ref candidates now include the freshly saved Entity.
    assert ("clone", "BRAND_NEW_ENTITY") in dock._all_ref_candidates()


def test_refresh_ref_candidates_noop_without_root(main_window):
    """No root config -> the lightweight refresh is a no-op (the same guard as
    _do_save/set_root_file) — must not crash and must not fabricate
    candidates."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    dock.refresh_ref_candidates()
    assert dock._cfg is None
    assert dock._trees == []
    assert dock._all_ref_candidates() == []


def test_refresh_ref_candidates_keeps_previous_cfg_on_load_failure(
        main_window, tmp_path, caplog):
    """A transiently broken/missing root must not wipe the dialog candidates:
    on a load failure the refresh keeps the PREVIOUS cfg/ctx (set_root_file
    remains the only full-teardown path, on a real root change) and logs a
    warning."""
    dock, root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    old_cfg = dock._cfg
    old_ctx = dock._ctx

    root.unlink()  # missing file -> load_config raises ValidationError
    dock.refresh_ref_candidates()

    assert dock._cfg is old_cfg
    assert dock._ctx is old_ctx
    assert dock._all_ref_candidates() != []  # previous candidates still served
    assert any("root config failed to load" in r.getMessage()
               for r in caplog.records)


def test_all_ref_names_dedups_cross_section_collision(main_window, tmp_path):
    """The ANCHOR dialog consumes plain names and an anchor auto-resolves by
    name (a section collision is fatal there) — so SHARED appears once."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    assert dock._all_ref_names() == ["CL_A", "SHARED", "R_B", "COORD_C", "PNT_D"]


def test_node_dialog_kind_chain_lists_only_chains(main_window, tmp_path):
    """Kind = chain -> the "Ref:" combo carries ONLY chain names (plain), and
    build_node() yields an explicitly-typed chain node. (2026-09-01, plan
    rules_to_chains: kind "rule" -> "chain".)"""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("chain"))

    assert _combo_texts(dlg.ref_combo) == ["R_B", "SHARED"]
    assert dlg.ref_combo.itemData(0) == ("chain", "R_B")

    dlg.ref_combo.setCurrentText("R_B")
    dlg.offset_widget.x_edit.setText("1.0")
    dlg.offset_widget.y_edit.setText("2.0")
    built = dlg.build_node()
    assert built is not None
    assert built.ref == "R_B"
    assert built.kind == "chain"


def test_node_dialog_kind_clone_lists_only_clones(main_window, tmp_path):
    """Kind = clone -> only clone_placements names (plain) — never a rule or a
    coordinate/point leaking in."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))

    assert _combo_texts(dlg.ref_combo) == ["CL_A", "SHARED"]


def test_node_dialog_auto_unique_plain_collisions_prefixed(main_window, tmp_path):
    """Kind = auto -> names unique to one section shown plain; a name shared by
    2+ sections shown once PER section as {kind}:{name}. itemData carries the
    (kind | None, name) pair the selection handler needs."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)

    assert dlg.kind_combo.currentData() is None  # auto by default
    assert _combo_texts(dlg.ref_combo) == [
        "CL_A", "clone:SHARED", "R_B", "chain:SHARED", "COORD_C", "PNT_D",
    ]
    assert dlg.ref_combo.itemData(0) == (None, "CL_A")
    assert dlg.ref_combo.itemData(1) == ("clone", "SHARED")
    assert dlg.ref_combo.itemData(3) == ("chain", "SHARED")
    assert dlg.ref_combo.itemData(5) == (None, "PNT_D")


def test_node_dialog_auto_pick_collision_specializes_kind(main_window, tmp_path):
    """Picking a PREFIXED collision entry in auto mode must auto-set the Kind
    to that section and put the CLEAN name in the ref combo — a node with
    kind=None and a colliding ref would be fatal at link_trees ("0 or 2+
    matches"), so the pick carries the explicit kind along."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    assert dlg.kind_combo.currentData() is None

    collision_idx = _combo_texts(dlg.ref_combo).index("clone:SHARED")
    dlg.ref_combo.setCurrentIndex(collision_idx)

    assert dlg.kind_combo.currentData() == "clone"
    assert dlg.ref_combo.currentText() == "SHARED"


def test_node_dialog_auto_pick_plain_keeps_auto(main_window, tmp_path):
    """Picking a plain (unique) entry in auto mode must NOT touch the Kind —
    only prefixed collision entries specialize it."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)

    plain_idx = _combo_texts(dlg.ref_combo).index("CL_A")
    dlg.ref_combo.setCurrentIndex(plain_idx)

    assert dlg.kind_combo.currentData() is None
    assert dlg.ref_combo.currentText() == "CL_A"


def test_node_dialog_external_clears_ref_combo(main_window, tmp_path):
    """Kind = external -> the combo is emptied (free-text live refdes) with a
    hint — regardless of what records the config carries."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    assert dlg.ref_combo.count() > 0

    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("external"))

    assert dlg.ref_combo.count() == 0
    assert "external" in dlg.ref_combo.placeholderText()


def test_node_dialog_edit_kind_none_collision_ref_stays_clean(main_window, tmp_path):
    """Round-trip edit of a node whose kind=None and ref is a cross-section
    collision (SHARED): the dialog opens in auto mode and the ref stays the
    CLEAN name (not prefixed), so saving keeps the node valid."""
    dock, _root = _dock_with(main_window, tmp_path, KIND_FILTER_CFG)
    tree = dock._current_tree()
    node = TreeNode(ref="SHARED", kind=None, xy=None, polar=None, rotation=0.0,
                    name=None, group=None)

    dlg = _build_dialog(dock, tree, None, existing=node, title="Edit node")

    assert dlg.kind_combo.currentData() is None  # auto
    assert dlg.ref_combo.currentText() == "SHARED"  # clean, not "clone:SHARED"

    dlg.offset_widget.x_edit.setText("5.0")
    dlg.offset_widget.y_edit.setText("6.0")
    built = dlg.build_node()
    assert built is not None
    assert built.ref == "SHARED"
    assert built.kind is None
