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

from gui import settings
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


# ── Whole-tree actions (2026-09-03: moved to the Tools → Trees menu) ───────

def test_no_whole_tree_action_buttons(main_window):
    """2026-09-03 (plan plan_2026_09_03_trees_menu_tools.md): every whole-tree
    action (Create/Rename/Delete tree, Anchor position, Redraw selected/whole)
    moved to the top-level menu Tools → Trees — the dock itself exposes no
    action buttons or "⋯" overflow. Only the read-only status labels
    (anchor_pos_label / dirty_label) and the handler call points remain."""
    dock = TreesDock(main_window)
    for attr in ("add_tree_button", "rename_tree_button", "delete_tree_button",
                 "redraw_button", "redraw_whole_button", "anchor_pos_button",
                 "more_button", "open_button", "new_button"):
        assert not hasattr(dock, attr), (
            f"whole-tree action button {attr} should have moved to Tools → Trees")
    # The read-only indicators stay.
    assert hasattr(dock, "anchor_pos_label")
    assert hasattr(dock, "dirty_label")
    # The handlers behind the moved Tools actions stay callable on the dock
    # (DockHub / the Tools-menu QActions forward here).
    assert callable(dock._on_create_tree)
    assert callable(dock._on_rename_tree)
    assert callable(dock._on_delete_tree)
    assert callable(dock._refresh_anchor_live_position)
    assert callable(dock._on_redraw_selected)
    assert callable(dock._on_redraw_whole_tree)


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


def test_anchor_dialog_sheet_combo_lists_names_not_uuid_keys(main_window):
    """Regression 2026-09-02 (sheet_names is a {uuid: Sheetname} dict): the
    Role-mode Sheet combo must show the READABLE sheet names (dict values,
    Channel_0/…), never the uuid keys — `list(dict)` returns keys, and
    anchor_sheet is matched against the readable names at apply time."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(
        main_window, [],
        sheet_names={"sheet-1111-0000": "Channel_0", "sheet-2222-0000": "Channel_1"})
    assert _combo_texts(dlg.sheet_edit) == ["Channel_0", "Channel_1"]


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
    """Confirming Tools → Trees → Delete tree… (dock._on_delete_tree) removes
    the CURRENT tree from self._trees, marks the dock dirty and rebuilds one
    fewer tab (the deletion itself writes nothing — Save persists it, like
    Create/Rename)."""
    dock, _root = _dock_with(main_window, tmp_path)  # power_tree + misc
    assert len(dock._trees) == 2
    assert dock.tabs.count() == 2
    dock.tabs.setCurrentIndex(0)  # power_tree

    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dock._on_delete_tree()

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
    dock._on_delete_tree()

    assert [t.name for t in dock._trees] == ["power_tree", "misc"]
    assert dock._dirty is False
    assert dock.tabs.count() == 2


def test_delete_tree_no_current_tree_is_noop(main_window, monkeypatch):
    """With no trees loaded (placeholder tab) the handler must not crash and
    must not even open a confirmation dialog."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock._trees == []

    import gui.docks.trees_dock as td_mod
    called = []
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: called.append(a) or QMessageBox.StandardButton.Yes)
    dock._on_delete_tree()

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


def test_run_forest_redraw_collects_all_trees_and_calls_forest_worker(
        main_window, tmp_path, monkeypatch):
    """P3b (plan 2026-09-02 P3 п.3): the FULL redraw collects EVERY node ref of
    EVERY tree (records AND module markers — checking markers activates their
    content) and calls the FOREST worker (no tree_name payload), through the
    Tools menu only — no new dock button."""
    dock, _root = _dock_with(main_window, tmp_path)
    from gui.docks.trees_dock import (
        collect_tree_refs, run_curated_forest_redraw_worker)

    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, payload):
        captured["worker"] = worker
        captured["payload"] = payload
        return object()
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", fake_start)

    dock._run_forest_redraw()

    assert captured
    assert captured["worker"] is run_curated_forest_redraw_worker
    assert "tree_name" not in captured["payload"]
    assert captured["payload"]["trees"] is dock._trees
    expected: set[str] = set()
    for tree in dock._trees:
        expected.update(collect_tree_refs(tree))
    assert captured["payload"]["selected_refs"] == expected


def test_run_forest_redraw_no_trees_shows_hint(main_window, tmp_path, monkeypatch):
    """A forest redraw with no trees loaded is a no-op status hint (no worker)."""
    dock, _root = _dock_with(main_window, tmp_path)
    dock._trees = []
    called = []
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: called.append(a) or object())

    dock._run_forest_redraw()

    assert not called
    assert "Nothing to redraw" in dock.status_label.text()


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


# ── tree-anchor live base: every anchor mode (2026-09-02) ──────────────────

def test_anchor_base_live_role_anchor_resolves_via_role_resolver(monkeypatch):
    """Regression 2026-09-02 (дерево с role-якорем Role=FPGA/Cluster=FPGA,
    "Считать текущее положение" верхнего узла): a role anchor must resolve its
    base LIVE through ComponentResolver — a ref-less anchor used to fall
    through to ref=None -> "Якорь None не найден на плате"."""
    import kicadstamp.tree_position as tp_mod

    anchor_pos = object()

    class _Fp:
        position = anchor_pos
        angle_deg = 90.0

    class _Resolver:
        def __init__(self, *a, **k):
            pass

        def resolve_anchor_fp(self, *a, **k):
            return _Fp()

    monkeypatch.setattr(tp_mod, "ComponentResolver", _Resolver)
    tree = Tree(name="t", anchor=TreeAnchor(role="FPGA", anchor_cluster="FPGA"),
                nodes=[])
    pos, rot = tp_mod._anchor_base_live_position(object(), object(), tree, {})
    assert pos is anchor_pos
    assert rot == 90.0


def test_anchor_base_live_role_anchor_pad_reads_pad_position(monkeypatch):
    """A role anchor with an explicit pad moves the base to that pad."""
    import kicadstamp.tree_position as tp_mod

    pad_pos = object()

    class _Fp:
        position = object()
        angle_deg = 0.0

    class _Resolver:
        def __init__(self, *a, **k):
            pass

        def resolve_anchor_fp(self, *a, **k):
            return _Fp()

    monkeypatch.setattr(tp_mod, "ComponentResolver", _Resolver)
    monkeypatch.setattr(tp_mod, "resolve_anchor_pad_position",
                        lambda *a, **k: pad_pos)
    tree = Tree(name="t", anchor=TreeAnchor(role="R", anchor_pad="2"), nodes=[])
    pos, _rot = tp_mod._anchor_base_live_position(object(), object(), tree, {})
    assert pos is pad_pos


def test_anchor_base_live_auto_anchor_uses_root_entity_zero_slot(monkeypatch):
    """auto anchor (no explicit (anchor ...)): the base is the root Entity's
    cell zero-slot — the materializer's derivation, now reused for the GUI
    live base."""
    import kicadstamp.tree_position as tp_mod
    import kicadstamp.placement.entity_placement as ep_mod

    zero_pos = object()
    monkeypatch.setattr(tp_mod, "_root_entity_record", lambda cfg, tree: object())
    monkeypatch.setattr(ep_mod, "_entity_own_zero_slot_live_position",
                        lambda *a, **k: (zero_pos, 15.0))
    tree = Tree(name="t", anchor=TreeAnchor(is_auto=True),
                nodes=[_placement_node("fpga")])
    pos, rot = tp_mod._anchor_base_live_position(object(), object(), tree, {})
    assert pos is zero_pos
    assert rot == 15.0


def test_anchor_base_live_auto_anchor_non_canonical_raises_clear_error(monkeypatch):
    """auto anchor on a tree without EXACTLY ONE top-level placement Entity is
    unreachable -> a clear error, never the old "Якорь None не найден" read."""
    import gui.docks.trees_dock as td_mod

    tree = Tree(name="t", anchor=TreeAnchor(is_auto=True), nodes=[])
    try:
        td_mod._anchor_base_live_position(object(), object(), tree, {})
    except ValidationError as e:
        assert "EXACTLY ONE" in str(e)
    else:
        raise AssertionError("expected ValidationError for a non-canonical auto tree")


def test_anchor_base_live_point_anchor_resolves_chain(monkeypatch):
    import kicadstamp.tree_position as tp_mod

    point_pos = object()

    class _Resolved:
        position = point_pos

    monkeypatch.setattr(tp_mod, "resolve_point_chain",
                        lambda *a, **k: _Resolved())

    class _Cfg:
        points = {}

    tree = Tree(name="t", anchor=TreeAnchor(point="P1"), nodes=[])
    pos, rot = tp_mod._anchor_base_live_position(object(), _Cfg(), tree, {})
    assert pos is point_pos
    assert rot is None


def test_anchor_base_live_ref_anchor_still_resolves(monkeypatch):
    """The pre-existing ref path is unchanged by the new anchor-mode dispatch."""
    import kicadstamp.tree_position as tp_mod

    ref_pos = object()
    monkeypatch.setattr(tp_mod, "build_records", lambda cfg: [])
    monkeypatch.setattr(tp_mod, "_build_by_name_index", lambda records: {})
    monkeypatch.setattr(tp_mod, "_resolve_anchor_ref",
                        lambda anchor, by_name: (object(), True))  # external
    monkeypatch.setattr(tp_mod, "resolve_base_live_position",
                        lambda *a, **k: ref_pos)
    monkeypatch.setattr(tp_mod, "resolve_base_rotation_deg", lambda *a, **k: 30.0)
    tree = Tree(name="t", anchor=TreeAnchor(ref="U3", is_external=True), nodes=[])
    pos, rot = tp_mod._anchor_base_live_position(object(), object(), tree, {})
    assert pos is ref_pos
    assert rot == 30.0


def test_prompt_node_returns_none_when_build_node_failed(main_window, tmp_path, monkeypatch):
    """Regression 2026-09-02 (live crash — the whole GUI died): the node
    dialog's OK accept()s unconditionally, so build_node() runs AFTER exec() in
    _prompt_node; a build_node() that returned None (used ref / empty ref / bad
    offset — it already showed a warning) used to crash on node.ref. Now it is
    treated like a cancel."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    monkeypatch.setattr(td_mod._NodeDialog, "exec",
                        lambda self: td_mod.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(td_mod._NodeDialog, "build_node", lambda self: None)
    assert dock._prompt_node("Add node", tree, parent_node=None) is None


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


def test_anchor_base_live_origin_ignores_existing_node_inline_anchor(
        main_window, tmp_path):
    """Regression 2026-08-27 (retargeted 2026-09-02 onto
    _anchor_base_live_position, the old _linked_base_for is gone): resolving
    the base for a NEW top-level node (parent_node=None) must NOT run
    link_trees over the whole tree — an EXISTING node whose record carries a
    legacy inline anchor (CH0_DAC_BUF with anchor_role) used to FORK-1-fail
    the whole link and block the read. The origin anchor base resolves
    standalone: (0,0)/0° with no config/board work."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path, DENIS_CFG)
    tree = dock._current_tree()
    assert [n.ref for n in tree.nodes] == ["CH0_DAC_BUF"]

    pos, rot = td_mod._anchor_base_live_position(None, dock._cfg, tree, {})
    assert (pos.x, pos.y) == (0, 0)
    assert rot == 0.0


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


# ═══════════════════════════════════════════════════════════════════════════
# Module kind GUI — plan 2026-09-02 P4 (tree-name refs, pivot fields,
# _used_refs exclusion, auto-numbering bypass, double-click navigation)
# ═══════════════════════════════════════════════════════════════════════════

MODULE_TREES = {
    "trees": [
        {"name": "fpga", "anchor": {"origin": True},
         "nodes": [{"ref": "ch0_dac_buf", "kind": "module", "xy": [10.0, 5.0],
                    "pivot_xy": [1.0, 2.0]}]},
        {"name": "ch0_dac_buf", "anchor": {"origin": True},
         "nodes": [{"ref": "D0", "xy": [0.0, 0.0]}]},
        {"name": "dac_x", "anchor": {"origin": True}, "nodes": []},
    ],
}

CYCLE_TREES = {
    "trees": [
        {"name": "a", "anchor": {"origin": True},
         "nodes": [{"ref": "b", "kind": "module", "xy": [0.0, 0.0]}]},
        {"name": "b", "anchor": {"origin": True},
         "nodes": [{"ref": "c", "kind": "module", "xy": [0.0, 0.0]}]},
        {"name": "c", "anchor": {"origin": True}, "nodes": []},
        {"name": "d", "anchor": {"origin": True},
         "nodes": [{"ref": "a", "kind": "module", "xy": [0.0, 0.0]}]},
    ],
}


def _module_dock(main_window, tmp_path):
    return _dock_with(main_window, tmp_path, trees=MODULE_TREES)


def _tree_of(dock, name):
    return next(t for t in dock._trees if t.name == name)


def test_used_refs_excludes_module_refs(main_window, tmp_path):
    """P4 п.1b: kind=="module" refs (child TREE names) are NOT "used record
    refs" — a second parent embedding the same child must never be flagged."""
    dock, _root = _module_dock(main_window, tmp_path)
    used = dock._used_refs()
    assert "D0" in used               # a real node ref of ch0_dac_buf
    assert "ch0_dac_buf" not in used  # the module marker ref is a tree name


def test_module_tree_candidates_exclude_self_dup_and_cycle(main_window, tmp_path):
    """P4 п.1: the module candidate list for a tree excludes itself, trees it
    already embeds (per-parent dup -> config fatal), and trees that would close
    a module cycle (they already reach the current tree)."""
    dock, _root = _dock_with(main_window, tmp_path, trees=CYCLE_TREES)
    # a embeds b; d embeds a -> adding a->d would cycle; only c is safe.
    assert dock._module_tree_candidates(_tree_of(dock, "a")) == ["c"]
    # c: every other tree reaches c transitively (a->b->c, b->c, d->a->b->c).
    assert dock._module_tree_candidates(_tree_of(dock, "c")) == []
    # d embeds a (dup excluded); adding d->b or d->c is safe.
    assert dock._module_tree_candidates(_tree_of(dock, "d")) == ["b", "c"]

    # no-cycle sanity: fpga may embed the standalone dac_x, not ch0 (dup).
    dock2, _root2 = _module_dock(main_window, tmp_path)
    assert dock2._module_tree_candidates(_tree_of(dock2, "fpga")) == ["dac_x"]


def test_node_dialog_module_kind_lists_trees_and_builds_pivot(main_window, tmp_path):
    """P4 п.1: kind==module lists the CHILD TREE NAMES (not records), shows the
    pivot widget, and build_node returns a module TreeNode with pivot fields."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    dlg = _NodeDialog(dock, [], set(), "Add child", tree=fpga,
                      module_candidates=["ch0_dac_buf", "dac_x"],
                      all_trees=dock._trees)
    idx = dlg.kind_combo.findData("module")
    assert idx >= 0
    dlg.kind_combo.setCurrentIndex(idx)
    texts = [dlg.ref_combo.itemText(i) for i in range(dlg.ref_combo.count())]
    assert "ch0_dac_buf" in texts and "dac_x" in texts
    # The dialog is not shown, so visibility means "not explicitly hidden
    # w.r.t. the dialog" (isVisibleTo), not Qt's on-screen isVisible().
    assert dlg.pivot_widget.isVisibleTo(dlg)
    assert not dlg.read_position_button.isVisibleTo(dlg)

    dlg.ref_combo.setCurrentText("ch0_dac_buf")
    # A module marker always has its own (marker) offset in the parent.
    dlg.offset_widget.x_edit.setText("10.0")
    dlg.offset_widget.y_edit.setText("5.0")
    dlg.pivot_widget.load(x=1.5, y=-2.0)
    node = dlg.build_node()
    assert node is not None
    assert node.kind == "module"
    assert node.ref == "ch0_dac_buf"
    assert node.pivot_xy == (1.5, -2.0)
    assert node.pivot_polar is None


def test_node_dialog_module_prefill_round_trips_pivot(main_window, tmp_path):
    """P4 п.1: editing a module node pre-fills the pivot fields from the
    existing node (pivot survives an Edit open/rebuild)."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    existing = TreeNode(ref="ch0_dac_buf", kind="module", xy=(10.0, 5.0),
                        polar=None, rotation=0.0, name=None, group=None,
                        pivot_xy=(1.0, 2.0))
    dlg = _NodeDialog(dock, [], set(), "Edit node", tree=fpga, existing=existing,
                      module_candidates=["ch0_dac_buf"], all_trees=dock._trees)
    node = dlg.build_node()
    assert node is not None
    assert node.kind == "module"
    assert node.ref == "ch0_dac_buf"
    assert node.pivot_xy == (1.0, 2.0)
    assert node.xy == (10.0, 5.0)


def test_prompt_node_module_ref_not_auto_numbered(main_window, tmp_path, monkeypatch):
    """P4 п.1a: a NEW module node's ref (a child tree name) is NEVER
    auto-numbered to ref_1 — it is chosen from the tree-name list, not a
    free-typed record needing dedup."""
    from PyQt6.QtWidgets import QDialog

    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    built = TreeNode(ref="ch0_dac_buf", kind="module", xy=(0.0, 0.0), polar=None,
                     rotation=0.0, name=None, group=None)
    monkeypatch.setattr(_NodeDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_NodeDialog, "build_node", lambda self: built)

    dock._add_node_flow(fpga)

    refs = [n.ref for n in fpga.nodes]
    assert refs.count("ch0_dac_buf") == 2   # original + new, BOTH unrenamed
    assert not any("_1" in r for r in refs)


def test_prompt_node_module_ref_not_auto_numbered_on_record_collision(
        main_window, tmp_path, monkeypatch):
    """P4 п.1a (the CONSTRUCTIVE bypass, not just the _used_refs side effect):
    a module node whose child-TREE name happens to equal an UNRELATED ordinary
    (non-module) node's ref elsewhere keeps its exact tree name. Without the
    kind guard in _prompt_node, _unique_ref would rename it to {tree}_1, which
    would then point at a nonexistent tree (fatal at the next Save, P1)."""
    from PyQt6.QtWidgets import QDialog

    trees = {"trees": [
        # tree "net" already has an ORDINARY node whose ref == "GND" (a live
        # refdes) — so "GND" IS in _used_refs; the module candidate below is
        # the separate TREE named "GND".
        {"name": "net", "anchor": {"origin": True},
         "nodes": [{"ref": "GND", "kind": "external", "xy": [1.0, 1.0]}]},
        {"name": "GND", "anchor": {"origin": True}, "nodes": []},
    ]}
    dock, _root = _dock_with(main_window, tmp_path, trees=trees)
    net = next(t for t in dock._trees if t.name == "net")
    assert "GND" in dock._used_refs()      # the ordinary node's ref is "used"

    built = TreeNode(ref="GND", kind="module", xy=(0.0, 0.0), polar=None,
                     rotation=0.0, name=None, group=None)
    monkeypatch.setattr(_NodeDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_NodeDialog, "build_node", lambda self: built)

    dock._add_node_flow(net)

    refs = [n.ref for n in net.nodes]
    assert refs.count("GND") == 2           # ordinary + module, both "GND"
    assert not any("_1" in r for r in refs)  # the module was NOT renamed


def test_edit_node_flow_module_copies_pivot(main_window, tmp_path, monkeypatch):
    """P4 п.1: _edit_node_flow copies pivot_xy/pivot_polar onto the existing
    module node (the Edit round-trip the plan calls out explicitly)."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    node = fpga.nodes[0]            # module node, pivot_xy (1,2)
    built = TreeNode(ref="ch0_dac_buf", kind="module", xy=(0.0, 0.0), polar=None,
                     rotation=0.0, name=None, group=None, pivot_xy=(3.0, 4.0))
    monkeypatch.setattr(dock, "_prompt_node", lambda *a, **k: built)
    dock._edit_node_flow(fpga, node)
    assert node.pivot_xy == (3.0, 4.0)
    assert node.pivot_polar is None
    assert dock._dirty is True


def test_render_tree_shows_module_tag(main_window, tmp_path):
    """P4 п.1: the module kind gets its "(module)" tag next to the ref."""
    dock, _root = _module_dock(main_window, tmp_path)
    item = dock._node_items["ch0_dac_buf"]
    assert "(module)" in item.text(0)


def test_double_click_navigation_module_and_embedded_in(main_window, tmp_path):
    """P4 п.3/п.4: double-click on a module node opens the referenced tree's
    tab; the referenced tree's own tab shows an "embedded in <parent>" pseudo
    item per embedding parent, and double-clicking it opens that parent."""
    dock, _root = _module_dock(main_window, tmp_path)
    names = [t.name for t in dock._trees]     # fpga, ch0_dac_buf, dac_x
    ch0_idx = names.index("ch0_dac_buf")
    fpga_idx = names.index("fpga")

    # The ch0 tab (module-placed) shows an "embedded in fpga" item.
    ch0_widget = dock.tabs.widget(ch0_idx)

    emb_items = []
    def walk(parent):
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == "fpga":
                emb_items.append(item)
            walk(item)

    walk(ch0_widget.invisibleRootItem())
    assert emb_items, "ch0 tab must show an 'embedded in fpga' pseudo item"

    # Double-click the embedder item -> fpga tab.
    dock._on_node_activated(emb_items[0], 0)
    assert dock.tabs.currentIndex() == fpga_idx

    # Double-click the module node (on the fpga tab) -> ch0 tab.
    dock.tabs.setCurrentIndex(fpga_idx)
    mod_item = dock._node_items["ch0_dac_buf"]
    dock._on_node_activated(mod_item, 0)
    assert dock.tabs.currentIndex() == ch0_idx


# ── tree_instances: read-only + save protection (2026-09-02, P1/F3) ────────

INSTANCE_CFG = {
    "entities": [
        {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"},
        {"name": "pif_avdd", "cell": "c_pif", "cluster": "PIF_AVDD"},
    ],
    "trees": [{
        "name": "dac_buf_tpl", "anchor": {"role": "DAC_BUF"},
        "nodes": [{
            "ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
            "children": [{"ref": "pif_avdd", "kind": "placement",
                          "xy": [0.5, 0.0]}],
        }],
    }],
    "tree_instances": [
        {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
    ],
}


def _instance_dock(main_window, tmp_path):
    """A TreesDock on a root config whose trees: section is generated from one
    template tree + one tree_instances: declaration (the materialized instance
    is an ordinary cfg.trees entry / tab, marked read-only via the index)."""
    root = tmp_path / "inst.sexp"
    root.write_text(dict_to_sexp(INSTANCE_CFG), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def test_instance_trees_are_indexed_read_only_but_still_tabs(main_window, tmp_path):
    """P1: instance trees stay ordinary tabs (redraw/embedding see them), but
    the tree_instances index marks them read-only; the template stays editable."""
    dock, _ = _instance_dock(main_window, tmp_path)
    assert [t.name for t in dock._trees] == ["dac_buf_tpl", "ch1_dac_buf"]
    assert set(dock._instances) == {"ch1_dac_buf"}
    assert dock._instance_of(dock._trees[0]) is None       # template: editable
    assert dock._instance_of(dock._trees[1]) is not None   # instance: read-only
    assert dock.tabs.count() == 2


def test_instance_context_menu_offers_no_structural_actions(
        main_window, tmp_path, monkeypatch):
    """P1: right-clicking a node of an INSTANCE tab offers NO Add/Edit/Delete/
    Rename/Move — only a disabled read-only note; the geometry is owned by the
    template + declaration."""
    dock, _ = _instance_dock(main_window, tmp_path)
    dock.tabs.setCurrentIndex(1)  # ch1_dac_buf (the instance)
    dock._current_tree_widget().expandAll()
    node_item = dock._node_items["dac_buf__ch1_dac_buf"]
    actions = dict(_context_menu_actions(dock, node_item, monkeypatch))
    labels = set(actions)
    for forbidden in ("Add child", "Add sibling", "Edit node…",
                      "Delete node", "Rename…", "Move to…"):
        assert forbidden not in labels
    assert len(actions) == 1
    assert "read-only" in next(iter(labels))


def test_template_context_menu_still_offers_structural_actions(
        main_window, tmp_path, monkeypatch):
    """P1 control: the TEMPLATE (a hand-written tree, Q3) keeps the full node
    menu — read-only applies to generated instances only."""
    dock, _ = _instance_dock(main_window, tmp_path)
    dock.tabs.setCurrentIndex(0)  # dac_buf_tpl (the template)
    dock._current_tree_widget().expandAll()
    node_item = dock._node_items["dac_buf"]
    assert node_item.text(0).startswith("dac_buf")
    actions = dict(_context_menu_actions(dock, node_item, monkeypatch))
    labels = set(actions)
    assert {"Add child", "Add sibling", "Edit node…", "Delete node"} <= labels


def test_rename_delete_tree_are_guarded_on_instance(
        main_window, tmp_path, monkeypatch):
    """P1: the Tools → Trees Rename/Delete tree actions (dock handlers) refuse
    an instance tab (an explanatory message, no dialog, no buffer change)."""
    import gui.docks.trees_dock as td_mod
    dock, _ = _instance_dock(main_window, tmp_path)
    dock.tabs.setCurrentIndex(1)  # ch1_dac_buf (the instance)
    infos = []
    monkeypatch.setattr(td_mod.QMessageBox, "information",
                        lambda *a, **k: infos.append(a)
                        or QMessageBox.StandardButton.Ok)
    dock._on_rename_tree()
    dock._on_delete_tree()
    assert infos, "instance rename/delete must explain the read-only state"
    assert [t.name for t in dock._trees] == ["dac_buf_tpl", "ch1_dac_buf"]


def test_save_does_not_persist_instance_trees(main_window, tmp_path, monkeypatch):
    """P1/F3: _do_save writes only the hand-written (template) trees: — the
    generated instance is NEVER persisted (the untouched tree_instances:
    declaration regenerates it), so a Save/reload cycle never duplicates it."""
    import gui.docks.trees_dock as td_mod
    from kicadstamp.config.sexp_format import sexp_to_dict
    dock, root = _instance_dock(main_window, tmp_path)
    monkeypatch.setattr(td_mod.QMessageBox, "warning", lambda *a, **k: None)
    dock._do_save()

    raw = sexp_to_dict(root.read_text(encoding="utf-8"))
    written = [t.get("name") for t in raw.get("trees", [])]
    assert written == ["dac_buf_tpl"], "the instance must not be written as a literal tree"
    assert any(i.get("name") == "ch1_dac_buf" for i in raw.get("tree_instances", []))

    cfg, _ = load_config(str(root))
    names = [t.name for t in cfg.trees]
    assert sorted(names) == ["ch1_dac_buf", "dac_buf_tpl"]  # regenerated exactly once


# ── tree_instances: navigation (2026-09-02, P2) ─────────────────────────────

INSTANCE_CFG2 = {**INSTANCE_CFG, "tree_instances": [
    {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
    {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2"},
]}


def _pseudo_items(dock, index):
    """All pseudo (double-click navigation) items on a tab: items carrying a
    plain tree NAME (str) in UserRole — the "embedded in"/"instance of"/"→
    instance" navigation items, never real node/anchor items."""
    widget = dock.tabs.widget(index)
    out = []
    def walk(parent):
        for i in range(parent.childCount()):
            item = parent.child(i)
            if isinstance(item.data(0, Qt.ItemDataRole.UserRole), str):
                out.append(item)
            walk(item)
    walk(widget.invisibleRootItem())
    return out


def test_template_tab_shows_instance_items_and_switches(main_window, tmp_path):
    """P2: a template tab shows one "→ instance: {name}" pseudo item per
    tree_instances declaration; double-clicking each switches to that instance
    tab (instances are ordinary tabs in self._trees)."""
    root = tmp_path / "inst2.sexp"
    root.write_text(dict_to_sexp(INSTANCE_CFG2), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    names = [t.name for t in dock._trees]
    assert names == ["dac_buf_tpl", "ch1_dac_buf", "ch2_dac_buf"]

    tpl_idx = names.index("dac_buf_tpl")
    dock.tabs.setCurrentIndex(tpl_idx)
    items = {it.text(0): it for it in _pseudo_items(dock, tpl_idx)}
    assert len(items) == 2
    assert "→ instance: ch1_dac_buf" in items
    assert "→ instance: ch2_dac_buf" in items

    # Double-click each instance item -> that instance's tab.
    dock._on_node_activated(items["→ instance: ch1_dac_buf"], 0)
    assert dock.tabs.currentIndex() == names.index("ch1_dac_buf")
    dock._on_node_activated(items["→ instance: ch2_dac_buf"], 0)
    assert dock.tabs.currentIndex() == names.index("ch2_dac_buf")


def test_instance_tab_shows_back_item_and_switches_to_template(main_window, tmp_path):
    """P2: an instance tab has one top "⇐ instance of {template} (sheet=…)"
    pseudo item; double-clicking it switches back to the template tab."""
    root = tmp_path / "inst.sexp"
    root.write_text(dict_to_sexp(INSTANCE_CFG), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    names = [t.name for t in dock._trees]
    tpl_idx = names.index("dac_buf_tpl")
    ch1_idx = names.index("ch1_dac_buf")

    dock.tabs.setCurrentIndex(ch1_idx)
    items = _pseudo_items(dock, ch1_idx)
    assert len(items) == 1
    assert items[0].text(0) == "⇐ instance of dac_buf_tpl (sheet=Channel_1)"

    dock._on_node_activated(items[0], 0)
    assert dock.tabs.currentIndex() == tpl_idx


def test_template_without_instances_shows_no_instance_items(main_window, tmp_path):
    """P2 control: an ordinary (non-template) tree shows no '→ instance' items —
    only real trees, e.g. the module-embedding example has none."""
    dock, _root = _dock_with(main_window, tmp_path)  # GRAMMAR_TREES: 2 plain trees
    for idx in range(dock.tabs.count()):
        for it in _pseudo_items(dock, idx):
            assert not it.text(0).startswith("→ instance:")


# ── UI-state persistence: active tab ───────────────────────────────────────
# (2026-09-03, plan tree_ui_state_persistence P1 — the active tab must survive
# structural rebuilds AND app restarts; gui_state.json is isolated per-test by
# tests/gui/conftest.py's autouse isolated_settings fixture.)

def test_structural_edit_keeps_nonzero_active_tab(main_window, tmp_path):
    """Bug fix: before 2026-09-03 _rebuild_tabs() unconditionally jumped to
    tab 0 after EVERY structural edit. An Add-node on tree 0 while tab 1
    (misc) is active must leave the user on misc (by name), not reset to 0."""
    dock, _root = _dock_with(main_window, tmp_path)  # power_tree + misc
    assert dock.tabs.count() == 2
    dock.tabs.setCurrentIndex(1)
    assert dock.tabs.currentIndex() == 1

    # A structural edit on the OTHER (non-active) tree — exactly what every
    # node/dialog mutator does before calling _rebuild_tabs().
    tree = dock._trees[0]  # power_tree
    tree.nodes[0].children.append(TreeNode(
        ref="NEW_CHILD", kind=None, xy=(3.0, 4.0), polar=None,
        rotation=0.0, name=None, group=None))
    dock._mark_dirty()
    dock._rebuild_tabs()

    assert dock.tabs.currentIndex() == 1
    assert dock.tabs.tabText(dock.tabs.currentIndex()) == "misc"


def test_active_tab_persists_between_dock_instances(main_window, tmp_path):
    """Switching tabs persists the active tab (by tree name) into gui_state.json
    and a brand-new dock over the same state restores it — the 'survives an app
    restart' contract."""
    dock, root = _dock_with(main_window, tmp_path)
    dock.tabs.setCurrentIndex(1)  # misc
    assert settings.state.get("trees_dock", {}).get("active_tab") == "misc"

    dock2 = TreesDock(main_window)  # fresh construction == app restart
    dock2.set_root_file(root)
    assert dock2.tabs.currentIndex() == 1
    assert dock2.tabs.tabText(dock2.tabs.currentIndex()) == "misc"


def test_persist_ui_state_flushes_current_active_tab(main_window, tmp_path):
    """The MainWindow._persist_settings() flush hook reads the CURRENT widget
    state — whatever tab is active right now is what gets saved."""
    dock, _root = _dock_with(main_window, tmp_path)
    dock.tabs.setCurrentIndex(1)
    dock.persist_ui_state()  # the app-quit flush
    assert settings.state.get("trees_dock", {}).get("active_tab") == "misc"


def test_persist_ui_state_is_a_safe_noop_without_trees(main_window):
    """Final flush with no trees loaded (placeholder tab) writes nothing and
    never crashes — MainWindow calls persist_ui_state() on every quit."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    dock.persist_ui_state()
    assert settings.state.get("trees_dock") is None


def test_active_tab_unknown_persisted_name_falls_back_to_tab_0(main_window, tmp_path):
    """Fatal-safety: a persisted active_tab that does not name any loaded tree
    (renamed/deleted/foreign project) must not crash — fall back to tab 0."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(GRAMMAR_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"active_tab": "no_such_tree"})
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock.tabs.currentIndex() == 0
    assert dock.tabs.tabText(0) == "power_tree"


def test_active_tab_deleted_tree_by_the_edit_falls_back_to_tab_0(main_window, tmp_path):
    """Deleting the ACTIVE tree by the same structural edit drops the tab — the
    rebuild falls back to tab 0, never a crash or a stale tab."""
    dock, _root = _dock_with(main_window, tmp_path)  # power_tree + misc
    dock.tabs.setCurrentIndex(1)  # misc is the active tab
    dock._trees = [t for t in dock._trees if t.name != "misc"]
    dock._mark_dirty()
    dock._rebuild_tabs()
    assert dock.tabs.count() == 1
    assert dock.tabs.currentIndex() == 0
    assert dock.tabs.tabText(0) == "power_tree"


def test_active_tab_persist_keeps_foreign_trees_dock_subkeys_intact(main_window, tmp_path):
    """P1 writes only trees_dock.active_tab — a pre-existing trees_dock.trees
    sub-key (P2's future payload) must survive the P1 write untouched."""
    settings.state.set("trees_dock",
                       {"trees": {"power_tree": {"anchor_expanded": True}}})
    dock, _root = _dock_with(main_window, tmp_path)
    dock.tabs.setCurrentIndex(1)
    saved = settings.state.get("trees_dock", {})
    assert saved.get("active_tab") == "misc"
    assert saved["trees"] == {"power_tree": {"anchor_expanded": True}}


# ── UI-state persistence: per-tree expand/collapse (P2) ─────────────────────
# (2026-09-03, plan tree_ui_state_persistence P2 — which anchors/nodes are
# expanded is saved per tree name and restored on rebuilds and app restarts.)

P2_TREES = {"trees": [
    {"name": "alpha", "anchor": {"origin": True},
     "nodes": [
         {"ref": "N1", "kind": "external",
          "children": [{"ref": "N1a", "kind": "external"}]},
         {"ref": "N2", "kind": "external",
          "children": [{"ref": "N2a", "kind": "external"}]},
     ]},
    {"name": "beta", "anchor": {"origin": True},
     "nodes": [
         {"ref": "M1", "kind": "external",
          "children": [{"ref": "M1a", "kind": "external"}]},
     ]},
]}


def _p2_anchor_and_nodes(dock, index):
    """(anchor_item, [direct node items]) of one rendered tree tab — P2_TREES
    trees are plain (no back/embedded/instance pseudo items), so the anchor is
    the only top-level item and its children are the top-level nodes."""
    w = dock.tabs.widget(index)
    anchor = _children(w.invisibleRootItem())[0]
    return anchor, _children(anchor)


def test_expansion_persists_and_restores_across_dock_recreate(main_window, tmp_path):
    """Expand the anchor + 2 nodes on tree alpha, leave tree beta collapsed; a
    fresh dock over the same gui_state.json restores exactly that state."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)

    anchor_a, nodes_a = _p2_anchor_and_nodes(dock, 0)  # alpha
    anchor_a.setExpanded(True)
    nodes_a[0].setExpanded(True)  # N1
    nodes_a[1].setExpanded(True)  # N2
    # Per-event persistence: each expansion was written as it happened.
    saved = settings.state.get("trees_dock", {}).get("trees", {})
    assert saved["alpha"]["anchor_expanded"] is True
    assert set(saved["alpha"]["expanded_refs"]) == {"N1", "N2"}

    # A fresh dock (== app restart) over the same state restores by name.
    dock2 = TreesDock(main_window)
    dock2.set_root_file(root)
    anchor_a2, nodes_a2 = _p2_anchor_and_nodes(dock2, 0)
    assert anchor_a2.isExpanded() is True
    assert nodes_a2[0].isExpanded() is True   # N1
    assert nodes_a2[1].isExpanded() is True   # N2
    anchor_b2, _ = _p2_anchor_and_nodes(dock2, 1)  # beta untouched
    assert anchor_b2.isExpanded() is False


def test_expansion_survives_in_session_rebuild(main_window, tmp_path):
    """A structural rebuild (which before this phase collapsed every tree) must
    re-apply the saved expansion instead of resetting to the Qt default."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)

    anchor_a, nodes_a = _p2_anchor_and_nodes(dock, 0)
    anchor_a.setExpanded(True)
    nodes_a[0].setExpanded(True)  # N1

    dock._mark_dirty()
    dock._rebuild_tabs()

    anchor_a2, nodes_a2 = _p2_anchor_and_nodes(dock, 0)
    assert anchor_a2.isExpanded() is True
    assert nodes_a2[0].isExpanded() is True  # N1 restored, not re-collapsed


def test_collapse_removes_ref_and_stale_entries_are_fatal_safe(main_window, tmp_path):
    """Collapsing a node removes its ref from the persisted entry. A seeded
    entry may reference a node that no longer exists — it is simply ignored on
    restore, never an error."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"trees": {
        "alpha": {"anchor_expanded": True,
                  "expanded_refs": ["N1", "GHOST_DELETED"]}}})

    dock = TreesDock(main_window)
    dock.set_root_file(root)
    anchor_a, nodes_a = _p2_anchor_and_nodes(dock, 0)
    assert anchor_a.isExpanded() is True
    assert nodes_a[0].isExpanded() is True   # N1 present -> expanded
    assert nodes_a[1].isExpanded() is False  # GHOST_DELETED has no item -> ignored

    nodes_a[0].setExpanded(False)  # collapse N1 -> its ref leaves the entry
    entry = settings.state.get("trees_dock")["trees"]["alpha"]
    assert set(entry["expanded_refs"]) == {"GHOST_DELETED"}


def test_unknown_tree_name_state_is_ignored_defaults_collapsed(main_window, tmp_path):
    """A persisted entry whose tree NAME is not among the loaded trees (deleted/
    renamed since, or a foreign project) is ignored — that tree renders at the
    Qt default (collapsed), same as a first run."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"trees": {
        "gamma": {"anchor_expanded": True, "expanded_refs": ["X"]}}})

    dock = TreesDock(main_window)
    dock.set_root_file(root)
    anchor_a, nodes_a = _p2_anchor_and_nodes(dock, 0)
    assert anchor_a.isExpanded() is False
    assert all(not n.isExpanded() for n in nodes_a)


def test_persist_ui_state_flushes_all_tree_expansion(main_window, tmp_path):
    """persist_ui_state() (the MainWindow quit-flush) captures EVERY rendered
    tree's expansion straight from the widgets — the collapsed default of a
    tree the user never touched AND a stale ref whose node is gone is dropped."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"trees": {
        "alpha": {"anchor_expanded": True, "expanded_refs": ["N1", "GHOST"]}}})
    dock = TreesDock(main_window)
    dock.set_root_file(root)

    dock.persist_ui_state()

    saved = settings.state.get("trees_dock")["trees"]
    assert saved["alpha"]["anchor_expanded"] is True
    assert set(saved["alpha"]["expanded_refs"]) == {"N1"}  # GHOST dropped
    assert "beta" in saved and saved["beta"]["anchor_expanded"] is False


# ── node editor Position tab / own_anchor (plan tree_node_own_anchor 2026-09-03)

def test_node_dialog_has_position_tab_default_relative_to_parent(main_window, tmp_path):
    """The node editor is a two-tab dialog; the new Position tab defaults to
    "Relative to parent" (= own_anchor None, today's behaviour), and the
    component fields are disabled in that mode."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    assert dlg.tabs.count() == 2
    assert dlg.tabs.tabText(1) == "Position"
    assert dlg.relative_to_parent_radio.isChecked()
    assert not dlg.relative_to_component_radio.isChecked()
    assert not dlg.own_anchor_role_combo.isEnabled()
    assert dlg.own_anchor() is None


def test_node_dialog_own_anchor_returns_tree_anchor_when_component(main_window, tmp_path):
    """Selecting "Relative to component" + Role/Sheet/Cluster/Pad -> own_anchor()
    returns the expected role-only TreeAnchor; switching back to parent -> None."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    dlg.relative_to_component_radio.setChecked(True)
    assert dlg.own_anchor_role_combo.isEnabled()
    dlg.own_anchor_role_combo.setCurrentText("IC1")
    dlg.own_anchor_sheet_combo.setCurrentText("PWR")
    dlg.own_anchor_cluster_combo.setCurrentText("SUP")
    dlg.own_anchor_pad_edit.setText("3")
    assert dlg.own_anchor() == TreeAnchor(
        role="IC1", is_origin=False,
        anchor_sheet="PWR", anchor_cluster="SUP", anchor_pad="3")
    dlg.relative_to_parent_radio.setChecked(True)
    assert dlg.own_anchor() is None


def test_node_dialog_component_without_role_refuses_build(main_window, tmp_path, monkeypatch):
    """"Relative to component" with an EMPTY Role is a hard refusal in
    build_node (QMessageBox) — never a silent downgrade to the parent base."""
    import gui.docks.trees_dock as td_mod
    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _NodeDialog(None, [], set(), "Add node", cfg=None, adapter=None,
                      sheet_names={}, tree=None, parent_node=None)
    dlg.ref_combo.setCurrentText("NEW1")
    dlg.offset_widget.x_edit.setText("1.0")
    dlg.offset_widget.y_edit.setText("2.0")
    dlg.relative_to_component_radio.setChecked(True)
    assert dlg.build_node() is None
    assert any("Pick a component" in str(w) for w in warnings)


def test_node_dialog_prefill_restores_own_anchor(main_window, tmp_path):
    """Edit mode: an existing node's own_anchor restores the "Relative to
    component" radio + Role/Sheet/Cluster/Pad, and own_anchor() returns it."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = TreeNode(ref="E1", kind="placement", xy=(1.0, 0.0), polar=None,
                        rotation=0.0, name=None, group=None, children=[],
                        own_anchor=TreeAnchor(role="IC1", anchor_sheet="PWR",
                                              anchor_pad="3"))
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree, parent_node=None,
                      existing=existing)
    assert dlg.relative_to_component_radio.isChecked()
    assert dlg.own_anchor_role_combo.currentText() == "IC1"
    assert dlg.own_anchor_sheet_combo.currentText() == "PWR"
    assert dlg.own_anchor_cluster_combo.currentText() == ""
    assert dlg.own_anchor_pad_edit.text() == "3"
    assert dlg.own_anchor() == existing.own_anchor
