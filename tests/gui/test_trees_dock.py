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
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QLabel, QMessageBox, QSplitter, QStackedWidget,
                             QTreeWidget)

from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, tree_from_dict

import gui.docks.trees_dock as trees_dock_mod
from gui import settings
from gui.docks.trees_dock import (
    AnchorFormWidget,
    NodeFormWidget,
    TreesDock,
    _NodeDialog,
)

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
    assert dock2.tree_tabs.count() == 1


# ── cfg.trees / _trees identity invariant (2026-09-03, plan                 ──
# ── trees_dock_cfg_trees_desync.md) ────────────────────────────────────────

def test_set_root_file_binds_cfg_trees_to_the_same_buffer(main_window, tmp_path):
    """Invariant from the dock's very first load (2026-09-03, plan
    trees_dock_cfg_trees_desync.md): self._cfg.trees MUST be the SAME list
    object as self._trees — the redraw payloads pass "cfg" (whose trees
    ApplyPipeline reads), so the two must never be distinct copies."""
    dock, root = _dock_with(main_window, tmp_path)
    assert dock._cfg.trees is dock._trees
    assert dock._cfg.trees[0] is dock._trees[0]


def test_refresh_ref_candidates_rebinds_cfg_trees_to_edited_buffer(
        main_window, tmp_path):
    """THE reported bug (2026-09-03): an offset edit applied to a node in the
    working buffer was invisible to the next Redraw — refresh_ref_candidates
    (fired by another dock's save between Apply and Redraw) swapped self._cfg
    for fresh-from-disk objects while self._trees kept the edited ones, and
    ApplyPipeline reads positions through cfg.trees. After the fix cfg.trees is
    rebound to the SAME list as _trees, so the edit survives the refresh and is
    visible through cfg."""
    cfg = {
        "trees": [{
            "name": "fpga",
            "anchor": {"ref": "CONN_PM5V"},
            "nodes": [{"ref": "fpga_flash_fpga", "kind": "external",
                       "xy": [10.0, 20.0]}],
        }],
    }
    dock, root = _dock_with(main_window, tmp_path, cfg)
    node = dock._trees[0].nodes[0]

    # _copy_node_onto/Apply edits the node object in the WORKING buffer...
    node.xy = (30.0, 40.0)
    assert dock._cfg.trees[0].nodes[0].xy == (30.0, 40.0)
    assert dock._cfg.trees is dock._trees

    # ...then a save from another dock fires refresh_ref_candidates, which
    # re-reads cfg from disk (fresh, UN-edited objects) but must keep _trees
    # untouched and rebind cfg.trees to it.
    dock.refresh_ref_candidates()

    assert dock._cfg.trees is dock._trees
    assert dock._cfg.trees[0].nodes[0] is node           # same object, not a copy
    assert dock._cfg.trees[0].nodes[0].xy == (30.0, 40.0)  # the edit is visible


def test_reload_trees_rebinds_cfg_trees_on_clean_reload(main_window, tmp_path):
    """On a clean (non-dirty) reload _trees becomes the freshly-read list —
    cfg.trees must be rebound to THAT exact list (2026-09-03, plan
    trees_dock_cfg_trees_desync.md)."""
    dock, root = _dock_with(main_window, tmp_path)
    root.write_text(dict_to_sexp({
        "trees": [
            {"name": "power_tree", "anchor": {"ref": "CONN_PM5V"}, "nodes": []},
            {"name": "misc", "anchor": {"origin": True}, "nodes": []},
            {"name": "from_selection", "anchor": {"role": "DAC"}, "nodes": []},
        ],
    }), encoding="utf-8")

    dock.reload_trees()

    assert [t.name for t in dock._trees] == ["power_tree", "misc", "from_selection"]
    assert dock._cfg.trees is dock._trees


def test_reload_trees_rebinds_cfg_trees_after_dirty_merge(
        main_window, tmp_path):
    """reload_trees re-reads cfg and, when dirty, appends externally-added
    trees to the working buffer — after the final buffer value is set,
    cfg.trees must be rebound to it (2026-09-03, plan
    trees_dock_cfg_trees_desync.md), so the merged buffer and cfg agree."""
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
    assert dock._cfg.trees is dock._trees
    assert dock._cfg.trees[0].name == "renamed_locally"  # the dirty edit is in cfg too


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
    assert dock.tree_tabs.count() == 3


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
    assert dock.tree_tabs.count() == 1


def test_tree_widgets_have_an_explicit_minimum_width_floor(main_window, tmp_path):
    """2026-08-30, Denis: TreesDock couldn't be narrowed after being widened
    once — each QTreeWidget's natural minimumSizeHint() floored the dock's
    width, the same class of bug as LogDock's QPlainTextEdit height (commit
    9d8ddff), only horizontal. Every tab's tree widget must carry an explicit
    minimumWidth of 1 — NOT 0, which Qt treats as "unset" and silently falls
    back to minimumSizeHint() (see
    test_text_view_minimum_height_is_explicitly_overridden in test_log_panel)."""
    dock, _ = _dock_with(main_window, tmp_path)  # two real trees
    assert dock.tree_tabs.count() == 2
    for i in range(dock.tree_tabs.count()):
        widget = dock._tree_widget_of_page(dock.tree_tabs.widget(i))
        assert isinstance(widget, QTreeWidget)
        assert widget.minimumWidth() == 1


def test_placeholder_tree_widget_has_an_explicit_minimum_width_floor(main_window, tmp_path):
    """The "(no trees)" placeholder gets the same explicit width override, for
    consistency with a real tree's tab (set_root_file(None) -> placeholder)."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock.tree_tabs.count() == 1
    widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
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
    assert dock.tree_tabs.count() == 1  # placeholder, not a crash


def test_set_root_file_renders_one_tab_per_tree_with_nested_structure(main_window, tmp_path):
    """Two trees -> two tabs; the first tree's render mirrors the nested
    grammar shape (anchor pseudo-root + nodes + child)."""
    dock, _root = _dock_with(main_window, tmp_path)

    assert dock.tree_tabs.count() == 2
    assert dock.tree_tabs.tabText(0) == "power_tree"
    assert dock.tree_tabs.tabText(1) == "misc"

    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
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
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG (xy 5.0 2.0)
    text = dock.status_label.text()
    assert "AMS1117_REG" in text and "xy=" in text
    assert "5.000" in text and "2.000" in text


def test_static_preview_polar_node(main_window, tmp_path):
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND (polar 3.0 45.0)
    text = dock.status_label.text()
    assert "R_AROUND" in text and "r=" in text
    assert "3.000" in text and "45.000" in text


# ── Master-detail right panel (plan_2026_09_04_trees_dock_master_detail.md §3.2) ──

def _embedded_form(page):
    """The modal-agnostic form inside a _form_action_row wrapper page — the
    wrapper's top VBox puts the form at itemAt(0) and the Apply/Redraw row
    (a nested HBox) at itemAt(1)."""
    lay = page.layout()
    if lay is None:
        return None
    item = lay.itemAt(0)
    return item.widget() if item is not None else None


def _embedded_redraw_button(page):
    """The master-detail Redraw QPushButton inside a _form_action_row wrapper
    page — the row's second QHBoxLayout item (Apply is itemAt(0))."""
    lay = page.layout()
    if lay is None:
        return None
    row_item = lay.itemAt(1)
    row = row_item.layout() if row_item is not None else None
    if row is None:
        return None
    btn_item = row.itemAt(1)
    return btn_item.widget() if btn_item is not None else None


def test_master_detail_right_panel_is_one_selection_following_panel(main_window, tmp_path):
    """Z.3.1/Z.3.2 (REWRITTEN from the two-tab test): each real-tree page is a
    QSplitter — the tree on the left, ONE form panel (a one-visible-page
    QStackedWidget, no tab bar) on the right. With nothing selected it shows the
    ACTIVE tree's AnchorFormWidget."""
    dock, _root = _dock_with(main_window, tmp_path)
    page = dock.tree_tabs.currentWidget()
    assert isinstance(page, QSplitter)
    assert isinstance(page.widget(0), QTreeWidget)
    panel = dock._active_form_panel()
    assert isinstance(panel, QStackedWidget)
    assert panel.count() == 1  # a single visible page — no Anchor/Node tab bar
    anchor_form = _embedded_form(dock._active_form_page())
    assert isinstance(anchor_form, AnchorFormWidget)
    assert anchor_form._tree is dock._trees[0]
    # The placeholder (no trees) page stays a bare tree — no form panel.
    dock2 = TreesDock(main_window)
    dock2.set_root_file(None)
    assert dock2._active_form_panel() is None


def test_master_detail_panel_tracks_selected_real_node(main_window, tmp_path):
    """Z.3.2 (REWRITTEN from the two-tab test): a freshly loaded dock has nothing
    selected, so the panel shows the tree ANCHOR form; selecting a REAL node
    swaps it for that node's editor."""
    dock, _root = _dock_with(main_window, tmp_path)
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)

    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG — a real TreeNode
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is dock._trees[0].nodes[0]

    # A different node re-fills the SAME tab with its own editor.
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is dock._trees[0].nodes[1]


def test_master_detail_panel_returns_to_anchor_form_when_cleared(main_window, tmp_path):
    """Z.3.2 (REWRITTEN from the two-tab hint test): clearing the selection
    returns the panel to the tree ANCHOR form — never a stale editor for a node
    that is no longer selected."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])
    assert isinstance(_embedded_form(dock._active_form_page()),
                      NodeFormWidget)
    tree_widget.clearSelection()
    assert isinstance(_embedded_form(dock._active_form_page()),
                      AnchorFormWidget)


def test_master_detail_anchor_panel_rebuilds_for_new_active_tree(main_window, tmp_path):
    """§3.2: switching the tree tab rebuilds the right panel for the newly
    active tree — the Anchor tab must edit THAT tree's anchor, not the old one."""
    dock, _root = _dock_with(main_window, tmp_path)
    assert _embedded_form(dock._active_form_page())._tree is dock._trees[0]
    dock.tree_tabs.setCurrentIndex(1)  # misc
    anchor_form = _embedded_form(dock._active_form_page())
    assert isinstance(anchor_form, AnchorFormWidget)
    assert anchor_form._tree is dock._trees[1]


# ── Master-detail §3c: _touched wiring / §9.4 discard / §6.5 fixes ─────────

def test_node_form_field_edit_marks_touched_and_apply_clears(main_window, tmp_path):
    """§3c gate (note_2026_09_04_touched_wiring_still_open.md): editing a REAL
    field of a NodeFormWidget sets _touched; a successful apply() resets it."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    tree_widget.setCurrentItem(dock._node_items[tree.nodes[0].ref])  # AMS1117_REG
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._touched is False
    form.name_edit.setText("edited")
    assert form._touched is True
    assert form.apply() is True
    assert form._touched is False


def test_node_form_offset_field_marks_touched(main_window, tmp_path):
    """§3c gate: the offset AnchorOriginWidget's single fieldChanged signal
    feeds the Node form's _touched flag (no per-field wiring in the form)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    tree_widget.setCurrentItem(dock._node_items[tree.nodes[0].ref])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._touched is False
    form.offset_widget.x_edit.setText("7.25")
    assert form._touched is True


def test_anchor_form_field_edit_marks_touched_and_apply_clears(main_window, tmp_path):
    """§3c gate: editing a REAL field of an AnchorFormWidget sets _touched; a
    successful apply() resets it (Origin is a guaranteed-valid anchor)."""
    dock, _root = _dock_with(main_window, tmp_path)
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    assert form._touched is False
    form.mode_combo.setCurrentIndex(0)  # Origin (board 0,0)
    assert form._touched is True
    assert form.apply() is True
    assert form._touched is False
    assert dock._current_tree().anchor.is_origin


def test_master_detail_node_switch_warns_when_draft_discarded(main_window, tmp_path):
    """design §9.4: switching to ANOTHER node while the current editor holds
    unapplied edits replaces it (never blocks) but reports the lost draft in
    the status row, and the Node tab now shows the NEW node's editor."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG — editor shown
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    form.name_edit.setText("unsaved")
    assert form._touched is True
    tree_widget.setCurrentItem(nodes[1])  # R_AROUND — discards the draft
    assert "Unapplied changes were discarded." in dock.status_label.text()
    new_form = _embedded_form(dock._active_form_page())
    assert isinstance(new_form, NodeFormWidget)
    assert new_form._existing is dock._trees[0].nodes[1]


def test_rebuild_warns_when_active_page_form_touched(main_window, tmp_path):
    """§7.1.2/design §9.4: _rebuild_tabs() clears every page, so a touched
    form on the ACTIVE page is reported (non-blocking) before the rebuild."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    tree_widget.setCurrentItem(dock._node_items[tree.nodes[0].ref])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    form.name_edit.setText("unsaved")
    assert form._touched is True
    dock._rebuild_tabs()
    assert "Unapplied changes were discarded." in dock.status_label.text()


def test_master_detail_same_node_reselect_keeps_editor(main_window, tmp_path):
    """§7.1.5: re-selecting the SAME node (a 'Redraw selected' checkbox toggle
    re-selects its row) must NOT tear the editor down nor fake a discard."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])
    form0 = _embedded_form(dock._active_form_page())
    assert isinstance(form0, NodeFormWidget)
    dock._on_selection_changed()  # same row re-selected: the rebuild is skipped
    form1 = _embedded_form(dock._active_form_page())
    assert form1 is form0
    assert "discarded" not in dock.status_label.text()


def test_instance_tree_right_panel_is_read_only(main_window, tmp_path):
    """§7.1.4 + Z.3 (REWRITTEN from the two-tab test): a generated INSTANCE
    tree's panel never hosts an editor — it shows the same read-only stub as the
    instance context menu, and selecting a real node of the instance keeps it."""
    dock, _ = _instance_dock(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(1)  # ch1_dac_buf (the instance)
    assert dock._active_form_panel() is not None
    page = dock._active_form_page()
    assert isinstance(page, QLabel)
    assert "read-only" in page.text()
    dock._current_tree_widget().expandAll()
    node_item = dock._node_items["dac_buf__ch1_dac_buf"]
    dock._current_tree_widget().setCurrentItem(node_item)
    page = dock._active_form_page()
    assert isinstance(page, QLabel)
    assert "read-only" in page.text()


def test_master_detail_anchor_tab_apply_and_redraw(main_window, tmp_path, monkeypatch):
    """§6: the Anchor tab's Apply writes tree.anchor IN PLACE and marks the dock
    dirty; its Redraw applies first, then runs the whole-tree redraw (an anchor
    moves the whole tree — design §9.3)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    assert not tree.anchor.is_origin
    form.mode_combo.setCurrentIndex(0)  # Origin (board 0,0)
    assert form.apply() is True
    assert tree.anchor.is_origin
    assert dock._dirty is True
    redraws = []
    monkeypatch.setattr(dock, "_on_redraw_whole_tree", lambda: redraws.append(True))
    form.redraw()
    assert redraws == [True]


def test_context_edit_node_shows_node_form(main_window, tmp_path, monkeypatch):
    """Z.3 (REWRITTEN from the two-tab test): 'Edit node…' no longer opens a
    modal — triggering it selects the node, and the single right-hand panel then
    shows that node's editor."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    dock._current_tree_widget().expandAll()
    item = dock._node_items[node.ref]
    actions = dict(_context_menu_actions(dock, item, monkeypatch))
    actions["Edit node…"].trigger()
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is node


def test_context_set_anchor_shows_anchor_form(main_window, tmp_path, monkeypatch):
    """Z.3 (REWRITTEN from the two-tab test): 'Set anchor…' no longer opens a
    modal picker — triggering it selects the tree's ROOT row (Z.2), so the single
    right-hand panel switches from the node editor back to the anchor form."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dock._current_tree_widget().expandAll()
    dock._current_tree_widget().setCurrentItem(
        dock._node_items[tree.nodes[0].ref])
    assert isinstance(_embedded_form(dock._active_form_page()), NodeFormWidget)
    anchor_item = _children(dock._current_tree_widget().invisibleRootItem())[0]
    actions = dict(_context_menu_actions(dock, anchor_item, monkeypatch))
    assert "Set anchor…" in actions
    actions["Set anchor…"].trigger()
    assert dock._current_tree_widget().currentItem() is anchor_item
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)


def test_master_detail_touched_draft_survives_tree_switch(main_window, tmp_path):
    """§9.4 (on-the-spot §3c decision): the form panel is built ONCE per page, so
    an unapplied draft survives tabbing to another tree and back — no data loss
    and no false discard notice on a plain tree switch."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])  # AMS1117_REG
    form0 = _embedded_form(dock._active_form_page())
    assert isinstance(form0, NodeFormWidget)
    form0.name_edit.setText("draft")
    assert form0._touched is True
    dock.tree_tabs.setCurrentIndex(1)  # misc
    dock.tree_tabs.setCurrentIndex(0)  # back to power_tree
    form1 = _embedded_form(dock._active_form_page())
    assert form1 is form0  # the same editor object — not rebuilt
    assert form1._touched is True
    assert "discarded" not in dock.status_label.text()


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


def test_anchor_dialog_self_mode_returns_self_anchor(main_window):
    """The "Self (component this tree places)" mode must yield
    TreeAnchor(is_self=True) with every other field at its default — the only
    GUI path to a self anchor (plan tree_self_anchor, task Д.7)."""
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [("placement", "FPGA")])
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("self"))
    dlg._accept()
    assert dlg._result == TreeAnchor(is_self=True)


def test_anchor_dialog_self_mode_lists_this_trees_placement_nodes(main_window):
    """plan Д.7/Д.9.5 #15: the self mode's Node combo offers ONLY this tree's
    kind "placement" nodes, and build_anchor reads the chosen node + pad."""
    from gui.docks.trees_dock import _AnchorDialog
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True),
                nodes=[_placement_node("E1"),
                       TreeNode(ref="NT", kind="net_trace", xy=None, polar=None,
                                rotation=0.0, name=None, group=None),
                       _placement_node("E2")])
    dlg = _AnchorDialog(main_window, [], tree=tree)
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("self"))
    texts = [dlg.self_combo.itemText(i) for i in range(dlg.self_combo.count())]
    assert texts == ["E1", "E2"]
    dlg.self_combo.setCurrentText("E2")
    dlg.self_pad_edit.setText("3")
    dlg._accept()
    assert dlg._result == TreeAnchor(is_self=True, self_ref="E2", self_pad="3")


def test_anchor_dialog_role_mode_returns_role_anchor(main_window):
    """The new "Role" mode builds a role anchor: role required, sheet/cluster/
    pad optional, nothing else set (ref/is_origin/is_external/is_self all off)."""
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

def test_anchor_dialog_prefills_self(main_window):
    from gui.docks.trees_dock import _AnchorDialog
    dlg = _AnchorDialog(main_window, [], existing=TreeAnchor(is_self=True))
    assert dlg.mode_combo.currentData() == "self"


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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True), nodes=[])
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
    tree = Tree(name="multi", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="rule_root", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True),
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
    tree = Tree(name="fpga_tree", anchor=TreeAnchor(is_self=True),
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
    assert tree.anchor.is_self is True
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
    tree = Tree(name="t", anchor=TreeAnchor(is_self=True),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree.anchor.is_self is True


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


def test_save_guard_roundtrip_yields_self_anchor(main_window):
    """§3 round-trip: after the self-switch, tree_to_dict writes the canonical
    {"self": {}} and load_tree recovers is_self=True — the exact path _do_save
    writes to disk (plan Д.2 rule 2)."""
    from kicadstamp.config import load_tree
    from kicadstamp.trees import tree_to_dict
    tree = Tree(name="fpga_tree",
                anchor=TreeAnchor(ref="fpga", is_origin=False, is_external=False),
                nodes=[_placement_node("fpga")])
    dock = _guard_dock(main_window, tree)
    dock._enforce_no_self_ref()
    assert tree_to_dict(tree)["anchor"] == {"self": {}}
    reloaded = load_tree(tree_to_dict(tree))
    assert reloaded.anchor.is_self is True


# ── Anchor pseudo-root label (_anchor_label / _render_tree) ────────────────

def test_anchor_label_all_modes_never_none():
    """Every TreeAnchor mode renders a human-readable label with NO "None"
    (self/role/point carry ref=None — the pre-2026-08-31 render showed '⚓ None')."""
    from gui.docks.trees_dock import _anchor_label
    cases = [
        (TreeAnchor(is_origin=True), "origin"),
        (TreeAnchor(is_self=True), "self"),
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


def test_render_tree_self_anchor_label(main_window, tmp_path):
    """A tree with NO (anchor ...) loads as is_self and its pseudo-root renders
    '⚓ (self)' — never '⚓ None' (2026-08-31 gap; renamed in plan Д.7)."""
    trees = {"trees": [{"name": "t", "nodes": []}]}  # no anchor key -> self
    dock, _root = _dock_with(main_window, tmp_path, trees)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    tops = _children(tree_widget.invisibleRootItem())
    assert len(tops) == 1
    assert "self" in tops[0].text(0)
    assert "None" not in tops[0].text(0)


def test_render_tree_role_anchor_label(main_window, tmp_path):
    """A role anchor with a sheet narrow renders role + sheet in the label."""
    trees = {"trees": [{"name": "t", "anchor": {"role": "FPGA", "sheet": "S1"},
                        "nodes": []}]}
    dock, _root = _dock_with(main_window, tmp_path, trees)
    label = _children(dock._tree_widget_of_page(dock.tree_tabs.widget(0)).invisibleRootItem())[0].text(0)
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
    assert dock.tree_tabs.count() == 2
    dock.tree_tabs.setCurrentIndex(0)  # power_tree

    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dock._on_delete_tree()

    assert [t.name for t in dock._trees] == ["misc"]
    assert dock._dirty is True
    assert dock.tree_tabs.count() == 1
    assert dock.tree_tabs.tabText(0) == "misc"


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
    assert dock.tree_tabs.count() == 2


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


def test_redraw_whole_tree_cancelled_by_first_run_heads_up(
        main_window, tmp_path, monkeypatch):
    """Bug 3 (2026-09-05): when the first-run "adopt existing copper?" heads-up
    is declined, the redraw must NOT start — the worker is never launched."""
    dock, _root = _dock_with(main_window, tmp_path)
    called = []
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: called.append(a) or object())
    monkeypatch.setattr(dock, "_confirm_first_run_redraw", lambda: False)

    dock._on_redraw_whole_tree()

    assert not called, "Cancel on the first-run heads-up must abort the redraw"


def test_confirm_first_run_redraw_forwards_path_and_live_adapter(
        main_window, tmp_path, monkeypatch):
    """Bug 3: _confirm_first_run_redraw hands the dock's ROOT config path and
    the LIVE board adapter to confirm_first_run_adoption — the two things that
    decide whether the heads-up fires (empty registries + existing board
    copper)."""
    dock, root = _dock_with(main_window, tmp_path)
    import gui.docks.trees_dock as td_mod
    adapter = object()
    captured = {}
    monkeypatch.setattr(dock, "_live_adapter", lambda: adapter)
    monkeypatch.setattr(
        td_mod, "confirm_first_run_adoption",
        lambda parent, config_path, adapter=adapter: (
            captured.update(cfg_path=config_path, adapter=adapter) or True))

    assert dock._confirm_first_run_redraw() is True
    assert captured["cfg_path"] == str(root)
    assert captured["adapter"] is adapter


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


def test_redraw_edited_node_module_kind_uses_forest_content_worker(
        main_window, tmp_path, monkeypatch):
    """2026-09-07 round 2 (Denis: Redraw on a module node should DO something,
    not just be disabled): _redraw_edited_node special-cases kind=="module" —
    instead of run_single_node_redraw_worker (only=[ref], which ApplyPipeline
    can never resolve for a tree-name ref), it dispatches
    run_curated_forest_redraw_worker scoped to selected_refs={node.ref} — the
    SAME "checking a marker activates its content" mechanism the "Full
    redraw" menu action uses (curated_redraw_plan_forest design P3 D2), just
    scoped to this one marker instead of every tree's every node."""
    dock, _root = _module_dock(main_window, tmp_path)
    from gui.docks.trees_dock import run_curated_forest_redraw_worker
    fpga = _tree_of(dock, "fpga")
    module_node = fpga.nodes[0]  # ref "ch0_dac_buf", kind "module"

    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, payload):
        captured["worker"] = worker
        captured["payload"] = payload
        return object()
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", fake_start)

    dock._redraw_edited_node(module_node)

    assert captured
    assert captured["worker"] is run_curated_forest_redraw_worker
    assert captured["payload"]["trees"] is dock._trees
    assert captured["payload"]["selected_refs"] == {"ch0_dac_buf"}
    assert "ref" not in captured["payload"]
    assert "tree_name" not in captured["payload"]


def test_redraw_edited_node_normal_kind_uses_single_node_worker(
        main_window, tmp_path, monkeypatch):
    """Regression guard for the fix above: a normal record-backed node still
    goes through the plain single-node --only worker, unaffected."""
    dock, _root = _dock_with(main_window, tmp_path)
    from gui.docks.trees_dock import run_single_node_redraw_worker
    tree = dock._trees[0]
    node = tree.nodes[0]
    assert node.kind != "module"

    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, payload):
        captured["worker"] = worker
        captured["payload"] = payload
        return object()
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod, "start_long_op", fake_start)

    dock._redraw_edited_node(node)

    assert captured
    assert captured["worker"] is run_single_node_redraw_worker
    assert captured["payload"]["ref"] == node.ref
    assert "selected_refs" not in captured["payload"]


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


def test_node_dialog_read_position_logs_error_when_no_live_connection(
        main_window, tmp_path, monkeypatch, caplog):
    """adapter is None (not connected) -> ONE ERROR line in the Log (never a
    modal — plan_2026_09_11_no_modals_and_busy_kicad X.1), and nothing is
    written to the offset fields (no silent partial state)."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()

    def _no_boxes(*a, **k):
        raise AssertionError("a connection-state error must not open a QMessageBox")
    monkeypatch.setattr(td_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Add child", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree, parent_node=None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("clone"))
    dlg.ref_combo.setCurrentText("C_OUT")
    dlg._on_read_position()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "No live board connection" in errors[0].message
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
    monkeypatch.setattr(tp_mod, "_self_entity_record",
                        lambda cfg, tree, anchor: object())
    monkeypatch.setattr(ep_mod, "_entity_own_zero_slot_live_position",
                        lambda *a, **k: (zero_pos, 15.0))
    tree = Tree(name="t", anchor=TreeAnchor(is_self=True),
                nodes=[_placement_node("fpga")])
    pos, rot = tp_mod._anchor_base_live_position(object(), object(), tree, {})
    assert pos is zero_pos
    assert rot == 15.0


def test_anchor_base_live_auto_anchor_non_canonical_raises_clear_error(monkeypatch):
    """auto anchor on a tree without EXACTLY ONE top-level placement Entity is
    unreachable -> a clear error, never the old "Якорь None не найден" read."""
    import gui.docks.trees_dock as td_mod

    tree = Tree(name="t", anchor=TreeAnchor(is_self=True), nodes=[])
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


def test_master_detail_node_tab_apply_mutates_node_and_marks_dirty(main_window, tmp_path):
    """§6: the master-detail Node tab's Apply writes the form onto the SELECTED
    node IN PLACE and marks the dock dirty — the master-detail replacement for
    the retired modal _edit_node_flow path (plan §6; _edit_node_flow itself was
    removed in §3c/§6 because a single click already shows the editor)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]  # AMS1117_REG (clone, xy 5,2)
    node.rotation = 1.0
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    tree_widget.setCurrentItem(dock._node_items[node.ref])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    form.rotation_edit.setText("77")
    form.name_edit.setText("new_label")
    assert form.apply() is True
    assert node.ref == "AMS1117_REG"
    assert node.rotation == 77.0
    assert node.name == "new_label"
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
         "nodes": [{"ref": "ch0_dac_buf", "kind": "module", "xy": [10.0, 5.0]}]},
        # 2026-09-11 (plan_2026_09_11_tree_inner_point_and_rotation §V.3): the
        # inner point lives on the EMBEDDED TREE now, not on the module node.
        {"name": "ch0_dac_buf", "anchor": {"origin": True},
         "pivot_xy": [1.0, 2.0],
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


def test_prompt_node_wires_module_candidates_and_all_trees(main_window, tmp_path, monkeypatch):
    """2026-09-07 live-found fix: _prompt_node (the shared "Add child"/"Add
    sibling"/"Add node" path) must wire module_candidates/all_trees into
    _NodeDialog exactly like _build_node_form already does for Edit — without
    them the module Ref combo and "From child node..." are silently empty
    regardless of how many trees actually exist in the project. Spies on
    _NodeDialog itself (not build_node/exec) so a regression that drops the
    kwargs again is caught even though other module tests stub exec/build_node
    and would not notice."""
    import gui.docks.trees_dock as td_mod
    from PyQt6.QtWidgets import QDialog

    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    captured = {}

    class _SpyDialog:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(td_mod, "_NodeDialog", _SpyDialog)

    assert dock._prompt_node("Add node", fpga, parent_node=None) is None
    assert captured.get("module_candidates") == ["dac_x"]
    assert captured.get("module_candidates") == dock._module_tree_candidates(fpga)
    assert captured.get("all_trees") == dock._trees


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
    # 2026-09-11 (plan_2026_09_11_tree_settings_form §W.5): the per-node pivot
    # block is GONE — the inner point is a TREE property, edited by
    # AnchorFormWidget's "Tree settings" group. The module ref combo still
    # lists tree names.
    assert not hasattr(dlg._form, "pivot_widget")
    assert not dlg.read_position_button.isVisibleTo(dlg)

    dlg.ref_combo.setCurrentText("ch0_dac_buf")
    # A module marker always has its own (marker) offset in the parent.
    dlg.offset_widget.x_edit.setText("10.0")
    dlg.offset_widget.y_edit.setText("5.0")
    node = dlg.build_node()
    assert node is not None
    assert node.kind == "module"
    assert node.ref == "ch0_dac_buf"
    assert not hasattr(node, "pivot_xy")   # a node carries no inner point (§W.5)


def test_node_dialog_module_prefill_round_trips_the_marker_offset(main_window, tmp_path):
    """P4 п.1 (migrated 2026-09-11, plan_2026_09_11_tree_settings_form §W.5):
    editing a module node pre-fills its MARKER offset/rotation. The per-node
    pivot block is DELETED — its editor lives on the TREE."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    existing = TreeNode(ref="ch0_dac_buf", kind="module", xy=(10.0, 5.0),
                        polar=None, rotation=0.0, name=None, group=None)
    dlg = _NodeDialog(dock, [], set(), "Edit node", tree=fpga, existing=existing,
                      module_candidates=["ch0_dac_buf"], all_trees=dock._trees)
    assert not hasattr(dlg._form, "pivot_widget")
    node = dlg.build_node()
    assert node is not None
    assert node.kind == "module"
    assert node.ref == "ch0_dac_buf"
    assert node.xy == (10.0, 5.0)


def test_node_form_has_no_pivot_widgets_after_the_tree_settings_move(
        main_window, tmp_path, monkeypatch):
    """2026-09-11 (plan_2026_09_11_tree_settings_form §W.5): the per-node pivot
    block is DELETED, not merely hidden — the inner point is a TREE property,
    edited by AnchorFormWidget's "Tree settings" group. build_node carries no
    pivot field at all."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    dlg = _NodeDialog(dock, [], set(), "Add child", tree=fpga,
                      module_candidates=["ch0_dac_buf", "dac_x"],
                      all_trees=dock._trees)
    idx = dlg.kind_combo.findData("module")
    dlg.kind_combo.setCurrentIndex(idx)
    dlg.ref_combo.setCurrentText("ch0_dac_buf")
    dlg.offset_widget.x_edit.setText("10.0")
    dlg.offset_widget.y_edit.setText("5.0")

    for gone in ("pivot_widget", "pivot_by_ref_button",
                 "pivot_from_node_button", "pivot_ref_status_label",
                 "_on_pick_pivot_ref", "_on_use_child_offset", "_pivot_ref"):
        assert not hasattr(dlg._form, gone)

    node = dlg.build_node()
    assert node is not None
    assert node.kind == "module"
    assert node.xy == (10.0, 5.0)
    assert not hasattr(node, "pivot_ref")


def test_node_dialog_module_prefill_has_no_legacy_node_pivot_ref(
        main_window, tmp_path):
    """2026-09-11 (plan_2026_09_11_tree_settings_form §W.5): a pivot_ref can no
    longer sit on a node, so an Edit open neither reads nor echoes one — the
    inner point is a TREE property, edited on the root row."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    existing = TreeNode(ref="ch0_dac_buf", kind="module", xy=(10.0, 5.0),
                        polar=None, rotation=0.0, name=None, group=None)
    dlg = _NodeDialog(dock, [], set(), "Edit node", tree=fpga, existing=existing,
                      module_candidates=["ch0_dac_buf"], all_trees=dock._trees)
    assert not hasattr(dlg._form, "_pivot_ref")
    node = dlg.build_node()
    assert node is not None
    assert not hasattr(node, "pivot_ref")


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


def test_master_detail_module_node_apply_leaves_the_tree_pivot_untouched(
        main_window, tmp_path):
    """MIGRATED 2026-09-11 (plan §V.3): applying a MODULE node must NOT touch
    the TREE's inner point — they are different objects now (the node's own
    marker offset is the only thing an Apply writes)."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    embedded = _tree_of(dock, "ch0_dac_buf")
    assert embedded.pivot_xy == (1.0, 2.0)     # the fixture's inner point
    node = fpga.nodes[0]                       # the module marker
    dock.tree_tabs.setCurrentIndex(dock._trees.index(fpga))
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.currentWidget())
    tree_widget.expandAll()
    tree_widget.setCurrentItem(dock._node_items[node.ref])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    form.offset_widget.x_edit.setText("11.0")
    form.offset_widget.y_edit.setText("6.0")
    assert form.apply() is True
    assert node.xy == (11.0, 6.0)
    assert embedded.pivot_xy == (1.0, 2.0)     # UNTOUCHED
    assert embedded.rotation == 0.0
    assert dock._dirty is True


def test_master_detail_redraw_button_enabled_for_module_node(main_window, tmp_path):
    """2026-09-07 live bug (round 1): the master-detail Node tab's own Redraw
    button (_form_action_row) had NO kind guard at all — unlike the modal
    _NodeDialog (_update_redraw_state) — so clicking Redraw on a module node
    reached run_single_node_redraw_worker with only=[<tree name>], which
    ApplyPipeline can never resolve ("--only: names not found", since a module
    node's ref is an embedded TREE's name, not a config record).

    Round 1 fixed this by disabling Redraw for "module" — but Denis pointed
    out that's the wrong fix: Redraw on a module node should DO something
    (place that module's own content), not just be blocked. Round 2
    (_redraw_edited_node) routes "module" through the SAME forest-content-
    activation machinery the "Full redraw" menu action uses
    (run_curated_forest_redraw_worker, selected_refs={ref}) instead of the
    plain --only worker — so the button stays ENABLED for "module" too; see
    test_redraw_edited_node_module_kind_uses_forest_content_worker for the
    actual dispatch."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    module_node = fpga.nodes[0]  # ref "ch0_dac_buf", kind "module"
    dock.tree_tabs.setCurrentIndex(dock._trees.index(fpga))
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.currentWidget())
    tree_widget.expandAll()

    tree_widget.setCurrentItem(dock._node_items[module_node.ref])
    page = dock._active_form_page()
    form = _embedded_form(page)
    assert isinstance(form, NodeFormWidget)
    assert form.kind_combo.currentData() == "module"
    redraw_btn = _embedded_redraw_button(page)
    assert redraw_btn.isEnabled() is True


def test_master_detail_redraw_button_enabled_for_placement_node(main_window, tmp_path):
    """Regression guard for the fix above: a normal record-backed kind (here
    "placement") must still get an ENABLED Redraw button — the new guard must
    not accidentally disable everything."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    nodes = _children(_children(tree_widget.invisibleRootItem())[0])
    tree_widget.setCurrentItem(nodes[0])
    page = dock._active_form_page()
    form = _embedded_form(page)
    assert isinstance(form, NodeFormWidget)
    assert form.kind_combo.currentData() in ("placement", "clone")
    redraw_btn = _embedded_redraw_button(page)
    assert redraw_btn.isEnabled() is True


def test_master_detail_redraw_button_tracks_live_kind_changes(main_window, tmp_path):
    """The master-detail Redraw button must re-evaluate when the user changes
    Kind live in the form — not just once at construction (a stale enabled
    state would let a name neither worker can resolve back in through an
    edit). "external" (a live-board-only base, no content of its own to
    place) is the still-disabled kind now that "module" is enabled too."""
    dock, _root = _module_dock(main_window, tmp_path)
    fpga = _tree_of(dock, "fpga")
    module_node = fpga.nodes[0]  # ref "ch0_dac_buf", kind "module"
    dock.tree_tabs.setCurrentIndex(dock._trees.index(fpga))
    tree_widget = dock._tree_widget_of_page(dock.tree_tabs.currentWidget())
    tree_widget.expandAll()
    tree_widget.setCurrentItem(dock._node_items[module_node.ref])
    page = dock._active_form_page()
    form = _embedded_form(page)
    redraw_btn = _embedded_redraw_button(page)

    idx = form.kind_combo.findData("external")
    form.kind_combo.setCurrentIndex(idx)
    assert redraw_btn.isEnabled() is False

    idx = form.kind_combo.findData("placement")
    form.kind_combo.setCurrentIndex(idx)
    assert redraw_btn.isEnabled() is True


def test_render_tree_shows_module_tag(main_window, tmp_path):
    """P4 п.1: the module kind gets a "(tree)" tag next to the ref — relabeled
    2026-09-07 (Denis: "module" alone was not discoverable as "this embeds a
    whole other tree"); the underlying kind value stays "module" everywhere
    else (data/grammar unaffected, display-only)."""
    dock, _root = _module_dock(main_window, tmp_path)
    item = dock._node_items["ch0_dac_buf"]
    assert "(tree)" in item.text(0)


def test_double_click_navigation_module_and_embedded_in(main_window, tmp_path):
    """P4 п.3/п.4: double-click on a module node opens the referenced tree's
    tab; the referenced tree's own tab shows an "embedded in <parent>" pseudo
    item per embedding parent, and double-clicking it opens that parent."""
    dock, _root = _module_dock(main_window, tmp_path)
    names = [t.name for t in dock._trees]     # fpga, ch0_dac_buf, dac_x
    ch0_idx = names.index("ch0_dac_buf")
    fpga_idx = names.index("fpga")

    # The ch0 tab (module-placed) shows an "embedded in fpga" item.
    ch0_widget = dock._tree_widget_of_page(dock.tree_tabs.widget(ch0_idx))

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
    assert dock.tree_tabs.currentIndex() == fpga_idx

    # Double-click the module node (on the fpga tab) -> ch0 tab.
    dock.tree_tabs.setCurrentIndex(fpga_idx)
    mod_item = dock._node_items["ch0_dac_buf"]
    dock._on_node_activated(mod_item, 0)
    assert dock.tree_tabs.currentIndex() == ch0_idx


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
    assert dock.tree_tabs.count() == 2


def test_instance_context_menu_offers_no_structural_actions(
        main_window, tmp_path, monkeypatch):
    """P1: right-clicking a node of an INSTANCE tab offers NO Add/Edit/Delete/
    Rename/Move — only a disabled read-only note; the geometry is owned by the
    template + declaration."""
    dock, _ = _instance_dock(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(1)  # ch1_dac_buf (the instance)
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
    dock.tree_tabs.setCurrentIndex(0)  # dac_buf_tpl (the template)
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
    dock.tree_tabs.setCurrentIndex(1)  # ch1_dac_buf (the instance)
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
    widget = dock._tree_widget_of_page(dock.tree_tabs.widget(index))
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
    dock.tree_tabs.setCurrentIndex(tpl_idx)
    items = {it.text(0): it for it in _pseudo_items(dock, tpl_idx)}
    assert len(items) == 2
    assert "→ instance: ch1_dac_buf" in items
    assert "→ instance: ch2_dac_buf" in items

    # Double-click each instance item -> that instance's tab.
    dock._on_node_activated(items["→ instance: ch1_dac_buf"], 0)
    assert dock.tree_tabs.currentIndex() == names.index("ch1_dac_buf")
    dock._on_node_activated(items["→ instance: ch2_dac_buf"], 0)
    assert dock.tree_tabs.currentIndex() == names.index("ch2_dac_buf")


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

    dock.tree_tabs.setCurrentIndex(ch1_idx)
    items = _pseudo_items(dock, ch1_idx)
    assert len(items) == 1
    assert items[0].text(0) == "⇐ instance of dac_buf_tpl (sheet=Channel_1)"

    dock._on_node_activated(items[0], 0)
    assert dock.tree_tabs.currentIndex() == tpl_idx


def test_template_without_instances_shows_no_instance_items(main_window, tmp_path):
    """P2 control: an ordinary (non-template) tree shows no '→ instance' items —
    only real trees, e.g. the module-embedding example has none."""
    dock, _root = _dock_with(main_window, tmp_path)  # GRAMMAR_TREES: 2 plain trees
    for idx in range(dock.tree_tabs.count()):
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
    assert dock.tree_tabs.count() == 2
    dock.tree_tabs.setCurrentIndex(1)
    assert dock.tree_tabs.currentIndex() == 1

    # A structural edit on the OTHER (non-active) tree — exactly what every
    # node/dialog mutator does before calling _rebuild_tabs().
    tree = dock._trees[0]  # power_tree
    tree.nodes[0].children.append(TreeNode(
        ref="NEW_CHILD", kind=None, xy=(3.0, 4.0), polar=None,
        rotation=0.0, name=None, group=None))
    dock._mark_dirty()
    dock._rebuild_tabs()

    assert dock.tree_tabs.currentIndex() == 1
    assert dock.tree_tabs.tabText(dock.tree_tabs.currentIndex()) == "misc"


def test_active_tab_persists_between_dock_instances(main_window, tmp_path):
    """Switching tabs persists the active tab (by tree name) into gui_state.json
    and a brand-new dock over the same state restores it — the 'survives an app
    restart' contract."""
    dock, root = _dock_with(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(1)  # misc
    assert settings.state.get("trees_dock", {}).get("active_tab") == "misc"

    dock2 = TreesDock(main_window)  # fresh construction == app restart
    dock2.set_root_file(root)
    assert dock2.tree_tabs.currentIndex() == 1
    assert dock2.tree_tabs.tabText(dock2.tree_tabs.currentIndex()) == "misc"


def test_persist_ui_state_flushes_current_active_tab(main_window, tmp_path):
    """The MainWindow._persist_settings() flush hook reads the CURRENT widget
    state — whatever tab is active right now is what gets saved."""
    dock, _root = _dock_with(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(1)
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
    assert dock.tree_tabs.currentIndex() == 0
    assert dock.tree_tabs.tabText(0) == "power_tree"


def test_active_tab_deleted_tree_by_the_edit_falls_back_to_tab_0(main_window, tmp_path):
    """Deleting the ACTIVE tree by the same structural edit drops the tab — the
    rebuild falls back to tab 0, never a crash or a stale tab."""
    dock, _root = _dock_with(main_window, tmp_path)  # power_tree + misc
    dock.tree_tabs.setCurrentIndex(1)  # misc is the active tab
    dock._trees = [t for t in dock._trees if t.name != "misc"]
    dock._mark_dirty()
    dock._rebuild_tabs()
    assert dock.tree_tabs.count() == 1
    assert dock.tree_tabs.currentIndex() == 0
    assert dock.tree_tabs.tabText(0) == "power_tree"


def test_active_tab_persist_keeps_foreign_trees_dock_subkeys_intact(main_window, tmp_path):
    """P1 writes only trees_dock.active_tab — a pre-existing trees_dock.trees
    sub-key (P2's future payload) must survive the P1 write untouched."""
    settings.state.set("trees_dock",
                       {"trees": {"power_tree": {"anchor_expanded": True}}})
    dock, _root = _dock_with(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(1)
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
    w = dock._tree_widget_of_page(dock.tree_tabs.widget(index))
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


# ── Splitter persistence: per-tree master-detail divider (2026-09-05) ────────
# Same feature as the Config dock's splitter: each tree page's
# tree | form-panel divider is flushed on quit (persist_ui_state) and
# re-applied to the fresh page QSplitter on the next build/restart. The form
# panel (widget(1)) has stretch 0, so its width is what Qt preserves across
# dock resizes — the tree (widget(0), stretch 1) absorbs the difference.

def test_persist_ui_state_flushes_per_tree_splitter_sizes(main_window, tmp_path):
    """The quit-flush captures alpha's page splitter into its per-tree entry
    as a plain two-int pixel list (splitter_sizes), alongside expansion."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)

    page = dock.tree_tabs.widget(0)  # alpha
    page.resize(1200, 500)
    page.setSizes([300, 900])        # tree=300, form=900
    expected = list(page.sizes())

    dock.persist_ui_state()

    entry = settings.state.get("trees_dock")["trees"]["alpha"]
    assert entry["splitter_sizes"] == expected
    assert len(entry["splitter_sizes"]) == 2
    # The form panel is the fixed pane — its saved width survives a restart.
    assert abs(entry["splitter_sizes"][1] - 900) <= 5


def test_tree_splitter_sizes_restored_on_dock_recreate(main_window, tmp_path, qapp):
    """A fresh dock (== app restart) re-applies alpha's saved split to the new
    page QSplitter — once the dock is shown/laid out, the form panel (stretch 0)
    holds its saved width and the tree absorbs the rest."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"trees": {
        "alpha": {"anchor_expanded": True, "splitter_sizes": [260, 720]}}})

    dock = TreesDock(main_window)
    # A plain QWidget page now (task T), not a dock: install it as the central
    # widget so its page splitters are laid out on show().
    main_window.setCentralWidget(dock)
    dock.set_root_file(root)
    main_window.resize(1200, 600)
    main_window.show()
    qapp.processEvents()

    page = dock.tree_tabs.widget(0)  # alpha
    sizes = list(page.sizes())
    assert len(sizes) == 2
    assert abs(sizes[1] - 720) <= 2  # form-panel width preserved
    main_window.hide()


def test_tree_splitter_sizes_invalid_saved_value_is_ignored(main_window, tmp_path):
    """A malformed saved splitter_sizes value must not crash the build — the
    page falls back to the default split (same fatal-safe rule as expansion)."""
    root = tmp_path / "p2.sexp"
    root.write_text(dict_to_sexp(P2_TREES), encoding="utf-8")
    settings.state.set("trees_dock", {"trees": {
        "alpha": {"splitter_sizes": "not-a-list"},
        "beta": {"splitter_sizes": [1, 2, 3]}}})

    dock = TreesDock(main_window)
    dock.set_root_file(root)

    assert dock.tree_tabs.count() == 2
    for i in range(2):
        page = dock.tree_tabs.widget(i)
        assert isinstance(page, QSplitter)
        assert page.count() == 2


# ── node editor Position tab / mount anchor (plan_2026_09_11_tree_mount_nodes)

def test_node_dialog_hides_the_anchor_picker_for_ordinary_nodes(main_window, tmp_path):
    """The node editor is a two-tab dialog; the Position tab's mount anchor
    picker belongs to a kind "mount" node ONLY and is hidden (not just
    disabled) for every other kind — an ordinary node's base is its parent."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    assert dlg.tabs.count() == 2
    assert dlg.tabs.tabText(1) == "Position"
    assert dlg.mount_anchor_widget.isHidden() is True
    assert dlg.mount_anchor() is None


def test_node_dialog_mount_anchor_returns_tree_anchor_when_filled(main_window, tmp_path):
    """For a kind "mount" node the picker is shown and mount_anchor() returns
    the expected role-only TreeAnchor; in "parent" mode it returns None."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _build_dialog(dock, tree, None)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("mount"))
    assert dlg.mount_anchor_widget.isHidden() is False
    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("IC1")
    dlg.mount_anchor_widget.anchor_sheet_edit.setCurrentText("PWR")
    dlg.mount_anchor_widget.anchor_cluster_edit.setCurrentText("SUP")
    dlg.mount_anchor_widget.anchor_pad_edit.setText("3")
    assert dlg.mount_anchor() == TreeAnchor(
        role="IC1", is_origin=False,
        anchor_sheet="PWR", anchor_cluster="SUP", anchor_pad="3")
    dlg.mount_anchor_widget.load(mode="parent")
    assert dlg.mount_anchor() is None


def test_mount_node_without_role_refuses_build(main_window, tmp_path, monkeypatch):
    """A kind "mount" node with an EMPTY Role is a hard refusal in build_node
    (QMessageBox) — never a silently parent-based mount node."""
    import gui.docks.trees_dock as td_mod
    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg = _NodeDialog(None, [], set(), "Add node", cfg=None, adapter=None,
                      sheet_names={}, tree=None, parent_node=None)
    dlg.ref_combo.setCurrentText("m1")
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("mount"))
    dlg.offset_widget.x_edit.setText("1.0")
    dlg.offset_widget.y_edit.setText("2.0")
    assert dlg.build_node() is None
    assert any("mount node needs a Role anchor" in str(w) for w in warnings)


def test_node_dialog_prefill_restores_mount_anchor(main_window, tmp_path):
    """Edit mode: an existing MOUNT node's anchor restores the picker's Role/
    Sheet/Cluster/Pad, and mount_anchor() returns it."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = TreeNode(ref="m1", kind="mount", xy=None, polar=None,
                        rotation=0.0, name=None, group=None, children=[],
                        anchor=TreeAnchor(role="IC1", anchor_sheet="PWR",
                                          anchor_pad="3"))
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree, parent_node=None,
                      existing=existing)
    assert dlg.mount_anchor_widget.mode == "anchor"
    assert dlg.mount_anchor_widget.anchor_role_edit.currentText() == "IC1"
    assert dlg.mount_anchor_widget.anchor_sheet_edit.currentText() == "PWR"
    assert dlg.mount_anchor_widget.anchor_cluster_edit.currentText() == ""
    assert dlg.mount_anchor_widget.anchor_pad_edit.text() == "3"
    assert dlg.mount_anchor() == existing.anchor


# ── Phase B: double-click -> edit, Apply/Redraw/Close dialog (2026-09-03) ──

def test_double_click_on_plain_node_shows_node_form(main_window, tmp_path):
    """Master-detail §3.2 + Z.3 (REWRITTEN from the two-tab test): double-clicking
    a NON-module real node no longer opens the modal editor — it makes sure the
    node is selected, and the single right-hand panel then shows its editor.
    Module nodes still switch to their referenced tree's tab (next test)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    item = dock._node_items[node.ref]
    dock._on_node_activated(item, 0)
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form._existing is node


def test_double_click_on_module_node_switches_tab(main_window, tmp_path, monkeypatch):
    """Phase B does not regress module-node double-click (tab switch, not edit)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    switched = []
    monkeypatch.setattr(dock, "_switch_to_tree", lambda name: switched.append(name))
    # Reuse an existing item but give it a module node payload (only the
    # UserRole payload drives the branch).
    node = TreeNode(ref="ch0", kind="module", xy=None, polar=None, rotation=0.0,
                    name=None, group=None, children=[])
    item = dock._node_items[tree.nodes[0].ref]
    item.setData(0, Qt.ItemDataRole.UserRole, node)
    dock._on_node_activated(item, 0)
    assert switched == ["ch0"]


def test_edit_dialog_has_apply_redraw_close_and_apply_mutates_in_place(
        main_window, tmp_path, monkeypatch):
    """EDIT mode: the dialog carries Apply/Redraw/Close (no OK/Cancel); Apply
    writes the form onto the existing node IN PLACE and marks the dock dirty
    WITHOUT closing the dialog."""
    import gui.docks.trees_dock as td_mod
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    dirty = []
    monkeypatch.setattr(dock, "_mark_dirty", lambda: dirty.append(True))
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree,
                      parent_node=dock._find_parent(tree, node), existing=node)
    assert dlg.apply_button is not None
    assert dlg.redraw_button is not None
    assert dlg.close_button is not None
    assert not hasattr(dlg, "ok_button")
    dlg.name_edit.setText("renamed_via_apply")
    dlg.rotation_edit.setText("33.0")
    assert dlg._on_apply() is True
    # Applied in place, dialog still open (no accept), dock marked dirty.
    assert node.name == "renamed_via_apply"
    assert node.rotation == pytest.approx(33.0)
    assert dirty == [True]
    assert "Applied" in dlg.apply_status_label.text()


def test_edit_dialog_redraw_applies_then_requests_dock_redraw(
        main_window, tmp_path, monkeypatch):
    """Redraw = Apply + a background per-node redraw request on the dock (the
    real worker is start_long_op-driven, so here we just assert the request)."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    requested = []
    monkeypatch.setattr(dock, "_redraw_edited_node",
                        lambda n: requested.append(n))
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree,
                      parent_node=dock._find_parent(tree, node), existing=node)
    assert dlg.redraw_button.isEnabled() is True  # placement/clone kind + dock
    dlg.name_edit.setText("redrawn_via_apply")
    dlg._on_redraw()
    assert node.name == "redrawn_via_apply"
    assert requested and requested[0] is node


def test_edit_dialog_double_apply_does_not_duplicate(main_window, tmp_path):
    """Apply twice on the same dialog mutates in place — no duplication, no
    second node, dialog stays open both times."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    dlg = _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                      "Edit node", cfg=dock._cfg, adapter=None,
                      sheet_names={}, tree=tree,
                      parent_node=dock._find_parent(tree, node), existing=node)
    before_children = len(tree.nodes)
    dlg.name_edit.setText("first")
    assert dlg._on_apply() is True
    assert dlg._on_apply() is True
    assert node.name == "first"
    assert len(tree.nodes) == before_children  # still one node, mutated in place


# ── Self-anchor duplicate highlight (§2, plan_2026_09_05_tree_root_rotation_drift) ──

def _power_like_cfg_tree():
    """A "power"-shaped config: explicit (role ...) anchor CONN_PM5V + a
    top-level placement node (conn_pm5v_power) whose Entity mounts the SAME
    role/sheet/cluster — the self-anchor duplicate (the live bug); plus a
    normal neighbour node (ldo) that must NOT be marked."""
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
    cfg = Config(
        entities=[
            Entity(name="conn_pm5v_power", cell="conn_pm5v",
                   cluster="CONN_PM5V", sheet="Power"),
            Entity(name="ldo_adj_n2v5_power", cell="ldo_cell",
                   cluster="LDO_ADJ", sheet="Power"),
        ],
        cells={
            "conn_pm5v": Cell(name="conn_pm5v", components=[
                TemplateComponentSlot(role="CONN_PM5V", angle_deg=-90.0)]),
            "ldo_cell": Cell(name="ldo_cell", components=[
                TemplateComponentSlot(role="LDO_ADJ")]),
        },
        trees=[],
    )
    tree = Tree(
        name="power",
        anchor=TreeAnchor(role="CONN_PM5V", anchor_sheet="Power",
                          anchor_cluster="CONN_PM5V"),
        nodes=[
            TreeNode(ref="conn_pm5v_power", kind="placement", xy=(0.0, 0.0),
                     polar=None, rotation=0.0, name=None, group=None),
            TreeNode(ref="ldo_adj_n2v5_power", kind="placement", xy=(0.0, 0.0),
                     polar=None, rotation=0.0, name=None, group=None),
        ],
    )
    cfg.trees = [tree]
    return cfg, tree


def _render_tree_direct(dock, cfg, tree):
    """Render ONE tree into a throwaway QTreeWidget with the dock's cfg loaded
    in memory — the same path _rebuild_tabs uses per tree (_render_tree), no
    file round-trip."""
    from PyQt6.QtWidgets import QTreeWidget
    dock._cfg = cfg
    dock._trees = list(cfg.trees)
    dock._instances = {}
    widget = QTreeWidget()
    dock._render_tree(widget, tree)
    return widget


def test_anchor_duplicate_node_is_marked_in_trees_dock(main_window):
    """§2: a tree node duplicating its OWN explicit (role ...) anchor
    (conn_pm5v_power under CONN_PM5V, the "power" case) is rendered with the
    neutral duplicate accent + a tooltip, so a legacy/hand-made duplicate is
    visible in the dock instead of only by redraw drift. Its neighbours are
    not marked."""
    from gui.docks.trees_dock import _ANCHOR_DUPLICATE_BG, TreesDock
    cfg, tree = _power_like_cfg_tree()
    dock = TreesDock(main_window)
    widget = _render_tree_direct(dock, cfg, tree)  # keep alive (owns the items)

    dup = dock._node_items["conn_pm5v_power"]
    assert dup.background(0).color() == _ANCHOR_DUPLICATE_BG
    assert dup.toolTip(0)
    assert "anchor" in dup.toolTip(0)

    normal = dock._node_items["ldo_adj_n2v5_power"]
    assert normal.background(0).color() != _ANCHOR_DUPLICATE_BG
    assert normal.toolTip(0) == ""


def test_tree_without_duplicate_has_no_marked_node(main_window):
    """§2 control ("as fpga"): a role-anchored tree whose placement nodes are
    DIFFERENT parts than the anchor gets no duplicate mark at all."""
    from gui.docks.trees_dock import _ANCHOR_DUPLICATE_BG, TreesDock
    cfg, tree = _power_like_cfg_tree()
    # Point the anchor at a role NO node's cell mounts -> no node is a duplicate.
    tree.anchor = TreeAnchor(role="SOME_OTHER", anchor_sheet="Power",
                             anchor_cluster="LDO_ADJ")
    dock = TreesDock(main_window)
    widget = _render_tree_direct(dock, cfg, tree)  # keep alive (owns the items)
    for ref in ("conn_pm5v_power", "ldo_adj_n2v5_power"):
        item = dock._node_items[ref]
        assert item.background(0).color() != _ANCHOR_DUPLICATE_BG
        assert item.toolTip(0) == ""


def test_node_form_sheet_combo_comes_from_the_config(main_window, tmp_path,
                                                     monkeypatch):
    """J.3 (2026-09-10): the node editor's Position tab lists the CONFIG's sheet
    names (RuntimeContext.sheet_names, built from the schematics) — NOT the ~2s
    board snapshot. Measured live: 325 footprints, 72 roles, 34 clusters and
    ZERO sheet names (`sheet=[None, None]` on every row), so this combo was
    permanently blank and any sheet narrowing in the node editor degraded
    silently."""
    import types

    dock, _root = _module_dock(main_window, tmp_path)
    # An EMPTY board snapshot — the old _live_sheets() source can offer nothing.
    monkeypatch.setattr(dock._main_window, "connection",
                        types.SimpleNamespace(snapshot=[]), raising=False)
    dock._ctx = types.SimpleNamespace(
        sheet_names={"uuid-a": "Channel_0", "uuid-b": "OpAmp"})

    tree = _tree_of(dock, "fpga")
    form = dock._build_node_form(tree, tree.nodes[0])
    combo = form.mount_anchor_widget.anchor_sheet_edit
    assert [combo.itemText(i) for i in range(combo.count())] == \
        ["Channel_0", "OpAmp"]


def test_auto_anchor_tree_single_node_not_marked(main_window):
    """§3: an auto-anchored tree's single top-level placement node is NOT a
    duplicate (it IS the anchor source by construction — _auto_anchor_base
    needs it) — never marked, even when it mounts the role the tree is about."""
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
    from gui.docks.trees_dock import _ANCHOR_DUPLICATE_BG, TreesDock
    cfg = Config(
        entities=[Entity(name="fpga", cell="fpga_cell")],
        cells={"fpga_cell": Cell(
            name="fpga_cell", components=[TemplateComponentSlot(role="FPGA")])},
        trees=[],
    )
    tree = Tree(name="fpga", anchor=TreeAnchor(is_self=True),
                nodes=[TreeNode(ref="fpga", kind="placement", xy=(0.0, 0.0),
                                polar=None, rotation=0.0, name=None, group=None)])
    cfg.trees = [tree]
    dock = TreesDock(main_window)
    widget = _render_tree_direct(dock, cfg, tree)  # keep alive (owns the items)
    item = dock._node_items["fpga"]
    assert item.background(0).color() != _ANCHOR_DUPLICATE_BG
    assert item.toolTip(0) == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2026-09-11: tree node — honest live read + one coordinate system
# (plan_2026_09_11_tree_node_live_read_and_board_frame.md)
# ═══════════════════════════════════════════════════════════════════════════
from types import SimpleNamespace                                    # noqa: E402

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME  # noqa: E402
from kicadstamp.domain.board import Footprint                         # noqa: E402
from kicadstamp.domain.geometry import BoardLayer, Vector2            # noqa: E402
from kicadstamp.tree_position import (                                # noqa: E402
    node_position,
    relative_rotation_deg,
)


class _ClusterAdapter:
    """Minimal live-board stand-in for the placement/cluster read: refresh +
    the footprint list + the Role/Cluster field reads
    resolve_footprint_by_cluster_role touches (mirrors
    tests/gui/test_cell_anchor_view.py's _ClusterAdapter)."""

    def __init__(self, footprints):
        self._fps = list(footprints)
        self.calls = []

    def refresh_board(self):
        self.calls.append("refresh")

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None

    def get_selected_items(self):
        # ComponentResolver's role narrowing always asks for the selection.
        return []


def _live_fp(ref, role, cluster, x_mm, y_mm, angle=0.0, layer=None):
    fp = Footprint(ref=ref, uuid=f"u-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=layer or BoardLayer.BL_F_Cu)
    fp.role = role
    fp.cluster = cluster
    fp.sheet_path_uuids = []
    return fp


# A cell whose two roles are 10 mm apart, and a tree that places the cell's
# Entity at the ORIGIN — deliberately NOT where the cluster stands on the live
# board below. The old read echoed the TREE (resolve_entity_live_position), so
# it returned (0,0); the honest read returns the cluster's position (0.1).
LIVE_CLUSTER_CFG = {
    "cells": {
        "buf": {"layer": "F.Cu", "components": [
            {"role": "ORIG", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0},
            {"role": "CAP", "offset_along_mm": 10.0, "offset_across_mm": -4.0,
             "angle_deg": 0.0},
        ]},
    },
    "entities": [
        {"name": "ENT_A", "cell": "buf", "cluster": "CL"},
    ],
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "ENT_A", "kind": "placement"}]},
    ],
}

# A two-node tree (a clone parent + a clone child) used by the reread
# idempotency test — the base is MOCKED there, so the records only need to
# resolve by name/kind.
REREAD_ROT_CFG = {
    "clone_placements": [
        {"name": "PARENT", "cluster": "c", "cell": "t", "xy": [0.0, 0.0]},
        {"name": "CHILD", "cluster": "c", "cell": "t", "xy": [3.0, 2.0]},
    ],
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "PARENT", "kind": "clone",
                    "children": [{"ref": "CHILD", "kind": "clone",
                                  "xy": [3.0, 2.0], "rotation": 0.0}]}]},
    ],
}


@pytest.mark.parametrize("base_rot", [0.0, 90.0, 180.0, 270.0])
def test_read_offset_is_what_the_node_would_store_and_node_position_returns_it(
        base_rot, monkeypatch):
    """§3.1 invariant: the pair the read returns, fed to node_position against
    the SAME live base, reproduces the child's live position EXACTLY — for a
    rotated base too (today it stored the world delta, so a base-90 node landed
    somewhere else entirely)."""
    import gui.docks.trees_dock as td_mod

    base_pos = Vector2.from_xy_mm(10.0, 20.0)
    child_pos = Vector2.from_xy_mm(13.0, 22.0)
    child_deg = base_rot + 90.0

    monkeypatch.setattr(td_mod, "_resolve_probe_ref",
                        lambda *a, **k: (SimpleNamespace(kind="clone"), False))
    monkeypatch.setattr(td_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (base_pos, base_rot, False))
    monkeypatch.setattr(td_mod, "resolve_base_live_position",
                        lambda *a, **k: child_pos)
    monkeypatch.setattr(td_mod, "resolve_base_rotation_deg",
                        lambda *a, **k: child_deg)

    offset_mm, rotation = td_mod._resolve_live_offset(
        object(), object(), {}, object(), None, "CHILD", "clone")

    assert rotation == relative_rotation_deg(child_deg, base_rot)
    node = TreeNode(ref="CHILD", kind="clone", xy=offset_mm, polar=None,
                    rotation=rotation, name=None, group=None)
    got = node_position(node, base_pos, base_rot)
    # node_position composes through the project's nm-grid rotate_local_offset,
    # whose int() truncation can cost up to ONE nanometre per axis (that is the
    # tree layer's own storage grid, unchanged by this work) — the invariant is
    # exact to that grid, and, crucially, STABLE (see the idempotency test).
    assert abs(got.x - child_pos.x) <= 1
    assert abs(got.y - child_pos.y) <= 1


@pytest.mark.parametrize("base_rot", [0.0, 90.0, 180.0, 270.0])
def test_reread_is_idempotent_with_a_rotated_base(
        base_rot, main_window, tmp_path, monkeypatch):
    """§4.2: two consecutive reads of an UNMOVED board leave node.xy/
    node.rotation bit-identical. Before the fix a base-90 node gained +90 on
    the second press (the read returned the world delta)."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path, REREAD_ROT_CFG)
    tree = dock._current_tree()
    node = tree.nodes[0].children[0]           # CHILD under PARENT
    node.xy = (3.0, 2.0)
    node.rotation = 40.0

    base_pos = Vector2.from_xy_mm(100.0, 200.0)
    # The live board stands STILL across both presses — Reread only READS it.
    live_child = Vector2.from_xy_mm(103.0, 202.0)
    live_child_deg = base_rot + 40.0
    main_window.connection.board = _FakeBoard()

    monkeypatch.setattr(td_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (base_pos, base_rot, False))
    monkeypatch.setattr(td_mod, "resolve_base_live_position",
                        lambda *a, **k: live_child)
    monkeypatch.setattr(td_mod, "resolve_base_rotation_deg",
                        lambda *a, **k: live_child_deg)

    dock._reread_node_flow(tree, node)
    first = (node.xy, node.polar, node.rotation)
    assert first[1] is None
    assert first[2] == 40.0
    # what was stored is the LOCAL offset: node_position composes it back onto
    # the live position (to the engine's nm grid).
    got = node_position(node, base_pos, base_rot)
    assert abs(got.x - live_child.x) <= 1
    assert abs(got.y - live_child.y) <= 1

    # the actual bug: a SECOND press must not move the node any further.
    dock._reread_node_flow(tree, node)
    assert (node.xy, node.polar, node.rotation) == first


def test_read_position_of_a_placement_node_comes_from_the_live_cluster(
        main_window, tmp_path):
    """0.1 (the main regression): a placement node's read is taken from the
    CLUSTER standing on the board, NOT from the tree node that places the
    Entity. The tree says (0,0); the cluster stands at (50,60) — the read must
    report (50,60)."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path, LIVE_CLUSTER_CFG)
    tree = dock._current_tree()
    adapter = _ClusterAdapter([
        _live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 0.0),
        _live_fp("IC2", "CAP", "CL", 60.0, 56.0, 0.0),
    ])

    offset_mm, rotation = td_mod._resolve_live_offset(
        dock._cfg, adapter, {}, tree, None, "ENT_A", "placement")

    assert offset_mm == (50.0, 60.0)
    assert rotation == 0.0


def test_read_position_rotation_comes_from_the_live_cluster(
        main_window, tmp_path):
    """§4.4: the cluster stands rotated 90° on the board relative to what the
    config implies — node.rotation picks that up (the read is a live read, not
    a restatement of the stored rotation)."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path, LIVE_CLUSTER_CFG)
    tree = dock._current_tree()
    adapter = _ClusterAdapter([
        _live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 90.0),
        _live_fp("IC2", "CAP", "CL", 46.0, 50.0, 90.0),   # rotate90 of (10,-4)
    ])

    offset_mm, rotation = td_mod._resolve_live_offset(
        dock._cfg, adapter, {}, tree, None, "ENT_A", "placement")

    assert offset_mm == (50.0, 60.0)
    assert rotation == 90.0


def test_mirrored_live_instance_is_refused(main_window, tmp_path):
    """§2.3: the trees layer has NO mirror storage, so a mirrored live instance
    is an honest refusal (ValidationError), never a silently unmirrored read."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path, LIVE_CLUSTER_CFG)
    tree = dock._current_tree()
    adapter = _ClusterAdapter([
        _live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 0.0,
                 layer=BoardLayer.BL_B_Cu),
        _live_fp("IC2", "CAP", "CL", 60.0, 56.0, 0.0,
                 layer=BoardLayer.BL_B_Cu),
    ])

    with pytest.raises(ValidationError) as ei:
        td_mod._resolve_live_offset(dock._cfg, adapter, {}, tree, None,
                                    "ENT_A", "placement")
    assert "MIRRORED" in str(ei.value)


def test_reread_mirrored_instance_warns_and_leaves_the_node_untouched(
        main_window, tmp_path, monkeypatch):
    """§2.3: "Reread current position" on a mirrored instance shows the honest
    warning and writes NOTHING."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path, LIVE_CLUSTER_CFG)
    tree = dock._current_tree()
    node = tree.nodes[0]
    before = (node.xy, node.polar, node.rotation)

    main_window.connection.board = SimpleNamespace(adapter=_ClusterAdapter([
        _live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 0.0,
                 layer=BoardLayer.BL_B_Cu),
        _live_fp("IC2", "CAP", "CL", 60.0, 56.0, 0.0,
                 layer=BoardLayer.BL_B_Cu),
    ]))
    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)

    dock._reread_node_flow(tree, node)

    assert warnings
    assert "MIRRORED" in str(warnings[0][2])
    assert (node.xy, node.polar, node.rotation) == before
    assert dock._dirty is False


def test_offline_form_shows_raw_values_disabled_and_saves_them_unchanged(
        main_window, tmp_path):
    """§3.4: with no live base the form shows the RAW stored (base-frame)
    values and DISABLES them — opening a node and saving offline cannot
    corrupt it."""
    dock, _root = _dock_with(main_window, tmp_path)   # ref anchor, no board
    tree = dock._current_tree()
    node = TreeNode(ref="R_OFF", kind="clone", xy=(-0.5, 1.0), polar=None,
                    rotation=90.0, name=None, group=None)

    dlg = _build_dialog(dock, tree, None, existing=node, title="Edit node")

    assert dlg.offset_widget.x_edit.text() == "-0.5"
    assert dlg.offset_widget.y_edit.text() == "1.0"
    assert dlg.rotation_edit.text() == "90.0"
    assert dlg.offset_widget.isEnabled() is False
    assert dlg.rotation_edit.isEnabled() is False
    assert dlg.offset_frame_label.text() != ""

    built = dlg.build_node()
    assert built is not None
    assert built.xy == (-0.5, 1.0)
    assert built.rotation == 90.0


def test_form_shows_the_offset_in_the_board_frame_for_a_rotated_base(
        main_window, tmp_path, monkeypatch):
    """§3.1/§3.3: config (xy -0.5 1.0) with a base of 90° -> the form shows
    rotate((-0.5, 1.0), +90) = (1.0, 0.5); a no-op save is bit-identical."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = TreeNode(ref="R_FR", kind="clone", xy=(-0.5, 1.0), polar=None,
                    rotation=90.0, name=None, group=None)
    monkeypatch.setattr(td_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 90.0, False))

    dlg = _build_dialog(dock, tree, None, existing=node, title="Edit node")

    assert dlg.offset_widget.x_edit.text() == "1.0"
    assert dlg.offset_widget.y_edit.text() == "0.5"
    assert dlg.rotation_edit.text() == "180.0"
    assert dlg.offset_widget.isEnabled() is True

    built = dlg.build_node()
    assert built is not None
    assert built.xy == (-0.5, 1.0)
    assert built.rotation == 90.0


def test_form_shows_polar_offset_with_the_angle_shifted_by_the_base(
        main_window, tmp_path, monkeypatch):
    """§3.2: in polar mode the radius is UNTOUCHED and the angle is shifted by
    the base rotation — never routed through Cartesian."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = TreeNode(ref="R_POL", kind="clone", xy=None, polar=(3.0, 45.0),
                    rotation=0.0, name=None, group=None)
    monkeypatch.setattr(td_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 90.0, False))

    dlg = _build_dialog(dock, tree, None, existing=node, title="Edit node")

    assert dlg.offset_widget.radius_edit.text() == "3.0"
    assert dlg.offset_widget.angle_edit.text() == "135.0"

    built = dlg.build_node()
    assert built is not None
    assert built.xy is None
    assert built.polar == (3.0, 45.0)


def test_extract_bridge_reads_the_cell_mount_not_the_zero_slot(
        main_window, tmp_path):
    """P.0.1/P.2.1 (plan_2026_09_11_entity_live_position_mount_point.md): the
    extract bridge must measure an Entity's live position from the cell's MOUNT
    A (what the tree node and the materializer put on the target point), not
    from a component that happens to sit at the cell's stored (0, 0).

    Cell `buf` here carries `anchor_xy = (-2.258536, -5.046273)` (Denis's
    measured `pif_oa_n2v5` value) with a zero-slot component `ORIG` at (0, 0):
    the two points are |A| = 5.53 mm apart, so reading the zero-slot shifts the
    node by exactly that much. The rotated anchor (90°) makes the two paths
    disagree in BOTH components."""
    import gui.docks.trees_dock as td_mod
    from gui.docks.reead import ReReadCluster
    from gui.docks.tree_from_selection import (
        build_tree_from_clusters,
        resolve_entity_live_position_mm,
        resolve_role_anchor_base_mm,
    )
    from kicadstamp.cell_frame import rotate_ydown_mm
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
    from kicadstamp.trees import TreeAnchor

    anchor_xy = (-2.258536, -5.046273)          # the cell's MOUNT A
    slots = [("ORIG", 0.0, 0.0), ("CAP", 10.0, -4.0)]
    cell = Cell(name="buf", layer="F.Cu", anchor_xy=anchor_xy, components=[
        TemplateComponentSlot(role=role, offset_along_mm=along,
                              offset_across_mm=across, angle_deg=0.0)
        for role, along, across in slots])
    entity = Entity(name="ENT_A", cell="buf", cluster="CL")
    cfg = Config(cells={"buf": cell}, entities=[entity], trees=[])

    # Live board: the cluster is a RIGID copy of the cell turned 90°, with its
    # MOUNT A standing at (150, 60) — i.e. every slot is at
    # origin + rotate(slot_offset - A, 90).
    parent_theta = 90.0
    cluster_fps = []
    for i, (role, along, across) in enumerate(slots):
        dx, dy = rotate_ydown_mm(along - anchor_xy[0], across - anchor_xy[1],
                                 parent_theta)
        cluster_fps.append(_live_fp(f"IC{i}", role, "CL", 150.0 + dx, 60.0 + dy,
                                    parent_theta))
    cluster = _ClusterAdapter(cluster_fps)
    anchor = TreeAnchor(role="ANCH", anchor_cluster="ANC")
    anchor_adapter = _ClusterAdapter([
        _live_fp("U1", "ANCH", "ANC", 100.0, 200.0, 90.0)])

    class _Both(_ClusterAdapter):
        def get_footprints(self):
            return cluster.get_footprints() + anchor_adapter.get_footprints()

    adapter = _Both(cluster.get_footprints())

    c = ReReadCluster(cluster="CL", sheet="Channel_0", entity_name="ENT_A",
                      cell="buf", profile_key=None, refs=["IC0", "IC1"])
    positions = {"ENT_A": resolve_entity_live_position_mm(adapter, cfg, entity, {})}
    anchor_live = resolve_role_anchor_base_mm(adapter, cfg, anchor, {})
    tree, errors = build_tree_from_clusters(
        [c], "t1", anchor, cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_live[:2],
        anchor_rot_deg=anchor_live[2])
    assert errors == []
    extract_node = tree.nodes[0]

    offset_mm, rotation = td_mod._resolve_live_offset(
        cfg, adapter, {}, tree, None, "ENT_A", "placement")

    assert offset_mm[0] == pytest.approx(extract_node.xy[0], abs=2e-6)
    assert offset_mm[1] == pytest.approx(extract_node.xy[1], abs=2e-6)
    assert rotation == pytest.approx(extract_node.rotation, abs=1e-9)


def test_extract_bridge_agrees_with_the_mount_when_anchor_role_holds_it(
        main_window, tmp_path):
    """P.2.2: a cell whose MOUNT is its `anchor_role` slot (no `anchor_xy`) —
    `CAP` at (10, -4), NOT the zero-slot `ORIG`. Both paths must give the same
    xy/rotation. (This one agreed even before the fix: the old bridge preferred
    `anchor_role` too — it is the regression guard that the new mount path did
    not break it.)"""
    import gui.docks.trees_dock as td_mod
    from gui.docks.reead import ReReadCluster
    from gui.docks.tree_from_selection import (
        build_tree_from_clusters,
        resolve_entity_live_position_mm,
        resolve_role_anchor_base_mm,
    )
    from kicadstamp.cell_frame import rotate_ydown_mm
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
    from kicadstamp.trees import TreeAnchor

    cell = Cell(name="buf", layer="F.Cu", anchor_role="CAP", components=[
        TemplateComponentSlot(role="ORIG", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0),
        TemplateComponentSlot(role="CAP", offset_along_mm=10.0,
                              offset_across_mm=-4.0, angle_deg=0.0)])
    entity = Entity(name="ENT_A", cell="buf", cluster="CL")
    cfg = Config(cells={"buf": cell}, entities=[entity], trees=[])

    # Rigid copy turned -90°, with the mount (the CAP slot) at (150, 60).
    mount_mm = (150.0, 60.0)
    slots = [("ORIG", 0.0, 0.0), ("CAP", 10.0, -4.0)]
    fps = []
    for i, (role, along, across) in enumerate(slots):
        dx, dy = rotate_ydown_mm(along - 10.0, across + 4.0, -90.0)
        fps.append(_live_fp(f"IC{i}", role, "CL", mount_mm[0] + dx,
                            mount_mm[1] + dy, -90.0))
    fps.append(_live_fp("U1", "ANCH", "ANC", 100.0, 200.0, 45.0))
    adapter = _ClusterAdapter(fps)

    anchor = TreeAnchor(role="ANCH", anchor_cluster="ANC")
    c = ReReadCluster(cluster="CL", sheet="Channel_0", entity_name="ENT_A",
                      cell="buf", profile_key=None, refs=["IC0", "IC1"])
    positions = {"ENT_A": resolve_entity_live_position_mm(adapter, cfg, entity, {})}
    anchor_live = resolve_role_anchor_base_mm(adapter, cfg, anchor, {})
    tree, errors = build_tree_from_clusters(
        [c], "t1", anchor, cfg.entities, cfg, entity_positions=positions,
        anchor_base=anchor_live[:2], anchor_rot_deg=anchor_live[2])
    assert errors == []
    node = tree.nodes[0]

    offset_mm, rotation = td_mod._resolve_live_offset(
        cfg, adapter, {}, tree, None, "ENT_A", "placement")

    assert offset_mm[0] == pytest.approx(node.xy[0], abs=2e-6)
    assert offset_mm[1] == pytest.approx(node.xy[1], abs=2e-6)
    assert rotation == pytest.approx(node.rotation, abs=1e-9)


def test_extract_bridge_is_bit_exact_when_the_mount_is_the_zero_slot(
        main_window, tmp_path):
    """P.2.3 (regression): for the ordinary extracted cell — mount == the
    zero-slot component — the bridge returns EXACTLY that component's live
    position, bit for bit, i.e. the numbers did not move by a nanometre."""
    from gui.docks.tree_from_selection import resolve_entity_live_position_mm
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot

    cell = Cell(name="buf", layer="F.Cu", anchor_role="ORIG", components=[
        TemplateComponentSlot(role="ORIG", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0),
        TemplateComponentSlot(role="CAP", offset_along_mm=10.0,
                              offset_across_mm=-4.0, angle_deg=0.0)])
    entity = Entity(name="ENT_A", cell="buf", cluster="CL")
    cfg = Config(cells={"buf": cell}, entities=[entity], trees=[])
    adapter = _ClusterAdapter([
        _live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 90.0),
        _live_fp("IC2", "CAP", "CL", 46.0, 50.0, 90.0)])

    x, y, rot = resolve_entity_live_position_mm(adapter, cfg, entity, {})

    assert (x, y) == (50.0, 60.0)
    assert rot == 90.0


def test_extract_bridge_without_a_cluster_falls_back_and_logs(
        main_window, tmp_path, caplog):
    """P.2.4 / §P.1.3: an Entity with no cluster tag (an unsaved / transitional
    Entity) cannot be read from a live cluster at all — the historical zero-slot
    path is used AND the degraded read is announced in the log, never silent."""
    import logging

    from gui.docks.tree_from_selection import resolve_entity_live_position_mm
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot

    cell = Cell(name="buf", layer="F.Cu", anchor_xy=(-2.0, -5.0), components=[
        TemplateComponentSlot(role="ORIG", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0)])
    entity = Entity(name="ENT_A", cell="buf")          # no cluster
    cfg = Config(cells={"buf": cell}, entities=[entity], trees=[])
    adapter = _ClusterAdapter([_live_fp("IC1", "ORIG", "CL", 50.0, 60.0, 0.0)])

    with caplog.at_level(logging.WARNING):
        x, y, _rot = resolve_entity_live_position_mm(adapter, cfg, entity, {})

    # the historical zero-slot read, i.e. NOT the mount (-2, -5) — and it is
    # announced, not silent.
    assert (x, y) == (50.0, 60.0)
    assert any("cannot read the live mount" in r.message for r in caplog.records)
    assert any("no cluster" in r.getMessage() for r in caplog.records)


def test_reread_agrees_with_extract_tree_for_a_rotated_anchor(
        main_window, tmp_path):
    """§0.4/§4.11: on ONE and the same live geometry with a TURNED anchor, the
    node the extract path builds (`build_tree_from_clusters`) and the node the
    "Reread current position" read produces must carry the SAME xy/rotation.
    They used to be two different answers — a bug by definition, and the
    regression gate for the whole coordinate-system work."""
    import gui.docks.trees_dock as td_mod
    from gui.docks.reead import ReReadCluster
    from gui.docks.tree_from_selection import (
        build_tree_from_clusters,
        resolve_entity_live_position_mm,
        resolve_role_anchor_base_mm,
    )
    from kicadstamp.config import Config
    from kicadstamp.config.models import Cell, Entity, TemplateComponentSlot
    from kicadstamp.trees import TreeAnchor

    cell = Cell(name="buf", layer="F.Cu", anchor_role="ORIG", components=[
        TemplateComponentSlot(role="ORIG", offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=0.0),
        TemplateComponentSlot(role="CAP", offset_along_mm=10.0,
                              offset_across_mm=-4.0, angle_deg=0.0),
    ])
    entity = Entity(name="ENT_A", cell="buf", cluster="CL")
    cfg = Config(cells={"buf": cell}, entities=[entity], trees=[])

    # Live board: the role anchor ANCH is turned 90°, and the cluster CL is a
    # RIGID copy of the cell, itself turned 90°, standing at (150, 60).
    adapter = _ClusterAdapter([
        _live_fp("U1", "ANCH", "ANC", 100.0, 200.0, 90.0),
        _live_fp("IC1", "ORIG", "CL", 150.0, 60.0, 90.0),
        _live_fp("IC2", "CAP", "CL", 146.0, 50.0, 90.0),   # rotate90 of (10,-4)
    ])
    anchor = TreeAnchor(role="ANCH", anchor_cluster="ANC")

    # ── path 1: "Extract tree from selection" (the reference implementation) ─
    c = ReReadCluster(cluster="CL", sheet="Channel_0", entity_name="ENT_A",
                      cell="buf", profile_key=None, refs=["IC1", "IC2"])
    positions = {"ENT_A": resolve_entity_live_position_mm(adapter, cfg, entity, {})}
    anchor_live = resolve_role_anchor_base_mm(adapter, cfg, anchor, {})
    tree, errors = build_tree_from_clusters(
        [c], "t1", anchor, cfg.entities, cfg,
        entity_positions=positions, anchor_base=anchor_live[:2],
        anchor_rot_deg=anchor_live[2])
    assert errors == []
    extract_node = tree.nodes[0]
    assert extract_node.ref == "ENT_A"

    # ── path 2: "Reread current position" on that very Entity ────────────────
    offset_mm, rotation = td_mod._resolve_live_offset(
        cfg, adapter, {}, tree, None, "ENT_A", "placement")

    # The extract path goes through the nm-grid child_local_offset, the read
    # through the exact mm helper — they may differ by ONE nanometre, no more.
    assert offset_mm[0] == pytest.approx(extract_node.xy[0], abs=2e-6)
    assert offset_mm[1] == pytest.approx(extract_node.xy[1], abs=2e-6)
    assert rotation == pytest.approx(extract_node.rotation, abs=1e-9)


# ═══════════════════════════════════════════════════════════════════════════
# 2026-09-11: the node form's base frame FOLLOWS the selected anchor
# (plan_2026_09_11_node_form_base_frame_follows_anchor.md).
# MIGRATED 2026-09-11 to the mount-node grammar (plan_2026_09_11_tree_mount_
# nodes): the anchor now lives on a kind "mount" node, so these cases drive a
# MOUNT node's anchor picker instead of the removed own_anchor one.
# ═══════════════════════════════════════════════════════════════════════════


def _mount_node(ref, role, xy=None, rotation=0.0, **anchor_kw):
    """A kind "mount" node with the given anchor role (test helper)."""
    return TreeNode(ref=ref, kind="mount", xy=xy, polar=None,
                    rotation=rotation, name=None, group=None, children=[],
                    anchor=TreeAnchor(role=role, is_origin=False, **anchor_kw))


def _fake_base_pose(monkeypatch, *, parent, components=None):
    """Patch _resolve_node_base_pose so the PARENT base and each named
    COMPONENT (a mount node's anchor) base are distinct — the whole point of
    this suite. An unknown role raises ValidationError, the real "not on the
    live board" failure."""
    import gui.docks.trees_dock as td_mod
    components = components or {}

    def fake(cfg, adapter, sheet_names, tree, parent_node, base_anchor):
        if base_anchor is None:
            return (parent[0], parent[1], False)
        pose = components.get(base_anchor.role)
        if pose is None:
            raise ValidationError(
                "role {!r} is not on the live board".format(base_anchor.role))
        return (pose[0], pose[1], False)

    monkeypatch.setattr(td_mod, "_resolve_node_base_pose", fake)


def test_mount_anchor_switch_to_rotated_component_saves_with_the_new_base(
        main_window, tmp_path, monkeypatch):
    """Denis's exact flow (regression), on a MOUNT node. The form opens with
    anchor A at 0°, the user switches the anchor to OP_AMP whose live base is
    rotated -90°, types (30, 1) IN THE BOARD FRAME and saves. The config must
    hold board_offset_to_local_mm((30, 1), -90), and node_position against the
    SAME live base must reproduce base + (30, 1). Before the fix the cached 0°
    base was reused, so (30, 1) was stored RAW and the node flew 30 mm down."""
    from kicadstamp.tree_position import (board_offset_to_local_mm,
                                          board_rotation_to_local_deg,
                                          node_position)
    from kicadstamp.utils.units import MM

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_X", "A", xy=(5.0, 2.0), rotation=10.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy(0, 0), 0.0),
                    components={"A": (Vector2.from_xy(0, 0), 0.0),
                                "OP_AMP": (Vector2.from_xy_mm(100.0, 200.0), -90.0)})

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    assert dlg.offset_widget.x_edit.text() == "5.0"     # anchor A base, 0 deg
    assert dlg.rotation_edit.text() == "10.0"

    # Anchor switch (the field-edit path) — the debounced refresh is flushed
    # deterministically here.
    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("OP_AMP")
    dlg.mount_anchor_widget.anchor_pad_edit.setText("4")
    dlg._refresh_for_new_anchor()

    # The node STAYS PUT: abs was A(0,0)+(5,2); re-expressed from the
    # component base (100,200) => (-95,-198) in the board frame.
    assert dlg.offset_widget.x_edit.text() == "-95.0"
    assert dlg.offset_widget.y_edit.text() == "-198.0"
    # The shown ROTATION is an absolute board angle -> unchanged on the switch.
    assert dlg.rotation_edit.text() == "10.0"

    dlg.offset_widget.x_edit.setText("30")
    dlg.offset_widget.y_edit.setText("1")
    built = dlg.build_node()
    assert built is not None
    assert built.xy == board_offset_to_local_mm((30.0, 1.0), -90.0)
    assert built.rotation == board_rotation_to_local_deg(10.0, -90.0)
    got = node_position(built, Vector2.from_xy_mm(100.0, 200.0), -90.0)
    assert abs(got.x - (100.0 + 30.0) * MM) <= 1
    assert abs(got.y - (200.0 + 1.0) * MM) <= 1


def test_mount_anchor_switch_keeps_the_node_still_and_re_expresses_the_offset(
        main_window, tmp_path, monkeypatch):
    """U.1 (no field edit): the absolute position computed before and after the
    switch is the SAME, while the shown offset changed."""
    from kicadstamp.tree_position import node_position

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_Y", "A", xy=(5.0, 2.0), rotation=40.0)
    base_a = (Vector2.from_xy_mm(0.0, 0.0), 0.0)
    component = (Vector2.from_xy_mm(100.0, 200.0), 30.0)
    _fake_base_pose(monkeypatch, parent=base_a,
                    components={"A": base_a, "OP_AMP": component})

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    before = dlg.build_node()
    abs_before = node_position(before, base_a[0], base_a[1])

    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("OP_AMP")
    dlg._refresh_for_new_anchor()
    after = dlg.build_node()

    assert after is not None
    abs_after = node_position(after, component[0], component[1])
    # node_position composes through the project's nm-grid rotate_local_offset,
    # whose int() truncation can cost up to ONE nanometre per axis (the tree
    # layer's own storage grid) — the invariant is exact to that grid.
    assert abs_after.x == pytest.approx(abs_before.x, abs=2)
    assert abs_after.y == pytest.approx(abs_before.y, abs=2)
    # ...and the DISPLAYED offset did change (its base moved).
    assert dlg.offset_widget.x_edit.text() != "5.0"
    assert dlg.rotation_edit.text() == "40.0"


def test_mount_anchor_switch_keeps_the_absolute_rotation_stored_one_follows(
        main_window, tmp_path, monkeypatch):
    """U.1: the shown rotation is absolute and does NOT change; the stored
    RELATIVE rotation changes by the base-rotation difference."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_ROT", "A", xy=(0.0, 0.0), rotation=40.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 0.0),
                    components={"A": (Vector2.from_xy_mm(0, 0), 0.0),
                                "OP_AMP": (Vector2.from_xy_mm(10, 20), -90.0)})

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    assert dlg.rotation_edit.text() == "40.0"

    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("OP_AMP")
    dlg._refresh_for_new_anchor()

    assert dlg.rotation_edit.text() == "40.0"           # absolute, unchanged
    built = dlg.build_node()
    assert built.rotation == 130.0                      # 40 - (-90)


def test_mount_anchor_switch_is_symmetric(
        main_window, tmp_path, monkeypatch):
    """U.4 п.4 (migrated): mount anchor A -> B is the mirror image — the node
    stays put and the stored relative rotation stays coherent."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    base_a = (Vector2.from_xy_mm(100.0, 200.0), -90.0)
    base_b = (Vector2.from_xy_mm(0.0, 0.0), 0.0)
    existing = _mount_node("M_B", "A", xy=(1.0, -30.0), rotation=130.0)
    _fake_base_pose(monkeypatch, parent=base_b,
                    components={"A": base_a, "B": base_b})

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    # component base -90: stored local (1,-30) -> board (30,1)
    assert dlg.offset_widget.x_edit.text() == "30.0"
    assert dlg.offset_widget.y_edit.text() == "1.0"
    assert dlg.rotation_edit.text() == "40.0"           # 130 + (-90)

    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("B")
    dlg._refresh_for_new_anchor()

    # node stays put: abs = (100,200)+(30,1); from base B (0,0)
    assert dlg.offset_widget.x_edit.text() == "130.0"
    assert dlg.offset_widget.y_edit.text() == "201.0"
    built = dlg.build_node()
    assert built.xy == (130.0, 201.0)
    assert built.rotation == 40.0


def test_mount_anchor_role_change_follows_the_new_component_frame(
        main_window, tmp_path, monkeypatch):
    """U.4 п.5: changing the Role to a component at a different position/
    rotation re-resolves the base and re-expresses the offset (the field edit
    path — no mode toggle)."""
    from kicadstamp.tree_position import node_position
    from kicadstamp.utils.units import MM

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_R", "A", xy=(5.0, 0.0), rotation=0.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 0.0),
                    components={
                        "A": (Vector2.from_xy_mm(10.0, 20.0), 90.0),
                        "B": (Vector2.from_xy_mm(30.0, 5.0), 0.0)})
    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    # base A rot 90: stored local (5,0) -> board (0,-5); abs = (10,15)
    assert dlg.offset_widget.x_edit.text() == "0.0"
    assert dlg.offset_widget.y_edit.text() == "-5.0"

    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("B")
    dlg._refresh_for_new_anchor()

    # base B: board offset = abs - P_B = (10,15)-(30,5) = (-20,10)
    assert dlg.offset_widget.x_edit.text() == "-20.0"
    assert dlg.offset_widget.y_edit.text() == "10.0"
    built = dlg.build_node()
    got = node_position(built, Vector2.from_xy_mm(30.0, 5.0), 0.0)
    assert abs(got.x - 10.0 * MM) <= 1
    assert abs(got.y - 15.0 * MM) <= 1


def test_mount_anchor_unresolvable_base_disables_and_saves_raw(
        main_window, tmp_path, monkeypatch):
    """U.4 п.6: the anchor does not resolve (role not on the board) -> the
    fields are disabled with a reason and the RAW stored values are restored,
    so a save can never write numbers converted against the OLD base."""
    import gui.docks.trees_dock as td_mod
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: None)

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_NB", "MISSING", xy=(5.0, 2.0), rotation=40.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 0.0),
                    components={})               # every role fails

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")

    assert dlg.offset_widget.isEnabled() is False
    assert dlg.rotation_edit.isEnabled() is False
    assert dlg.offset_frame_label.text() != ""
    # RAW stored values restored — NOT the old-base board numbers.
    assert dlg.offset_widget.x_edit.text() == "5.0"
    assert dlg.offset_widget.y_edit.text() == "2.0"
    assert dlg.rotation_edit.text() == "40.0"
    built = dlg.build_node()
    assert built is not None
    assert built.xy == (5.0, 2.0)
    assert built.rotation == 40.0


def test_read_position_after_mount_anchor_switch_uses_the_new_base(
        main_window, tmp_path, monkeypatch):
    """U.4 п.7 (migrated): "Read current position" after the anchor switch fills
    the fields in the BOARD frame of the NEW base (no separate logic — the
    shared cache)."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_READ", "A", xy=(0.0, 0.0), rotation=0.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 0.0),
                    components={"A": (Vector2.from_xy_mm(0, 0), 0.0),
                                "OP_AMP": (Vector2.from_xy_mm(100, 200), -90.0)})
    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        lambda *a, **k: ((1.0, -30.0), 100.0))

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("OP_AMP")
    dlg._refresh_for_new_anchor()

    dlg._on_read_position()

    # local (1,-30) with base -90 -> board (30,1); relative 100 -> absolute 10
    assert dlg.offset_widget.x_edit.text() == "30.000"
    assert dlg.offset_widget.y_edit.text() == "1.000"
    assert dlg.rotation_edit.text() == "10.000"


def test_mount_anchor_fields_do_not_re_resolve_per_keystroke(
        main_window, tmp_path, monkeypatch):
    """U.2: the base is re-resolved ONCE per committed anchor change, never per
    character typed. Prefill resolves once; typing pad digits only invalidates
    the cache; the explicit refresh costs exactly one more call."""
    import gui.docks.trees_dock as td_mod

    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_K", "OP_AMP", xy=(1.0, 1.0), rotation=0.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 0.0),
                    components={"OP_AMP": (Vector2.from_xy_mm(0, 0), 0.0)})
    real = td_mod._resolve_node_base_pose
    calls = []

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(td_mod, "_resolve_node_base_pose", counting)

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    assert len(calls) == 1                       # prefill resolves once
    dlg.mount_anchor_widget.anchor_role_edit.setCurrentText("OP_AMP")
    # Kill the pending debounce so a pumping event loop cannot make the count
    # nondeterministic — the claim under test is the SYNCHRONOUS behaviour.
    dlg._anchor_refresh_timer.stop()
    baseline = len(calls)
    for text in ("4", "41", "41x"):
        dlg.mount_anchor_widget.anchor_pad_edit.setText(text)
    assert len(calls) == baseline                # keystrokes: no adapter call
    dlg._refresh_for_new_anchor()
    assert len(calls) == baseline + 1            # one per committed change


def test_unchanged_mount_anchor_keeps_the_form_bit_identical(
        main_window, tmp_path, monkeypatch):
    """U.4 п.8: with the anchor left alone the refresh is a no-op and a no-op
    save round-trips bit-for-bit."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    existing = _mount_node("M_S", "OP_AMP", xy=(-0.5, 1.0), rotation=90.0)
    _fake_base_pose(monkeypatch, parent=(Vector2.from_xy_mm(0, 0), 90.0),
                    components={"OP_AMP": (Vector2.from_xy_mm(0, 0), 90.0)})

    dlg = _build_dialog(dock, tree, None, existing=existing, title="Edit node")
    shown = (dlg.offset_widget.x_edit.text(), dlg.offset_widget.y_edit.text(),
             dlg.rotation_edit.text())
    dlg._refresh_for_new_anchor()                # no real change -> no-op
    assert (dlg.offset_widget.x_edit.text(), dlg.offset_widget.y_edit.text(),
            dlg.rotation_edit.text()) == shown
    built = dlg.build_node()
    assert built.xy == (-0.5, 1.0)
    assert built.rotation == 90.0


# ── Z.2/Z.3: root row selectable + single selection-following panel ────────
# plan_2026_09_11_trees_dock_single_panel.md — Z.2 (root selectable) + Z.3
# (one panel instead of the Anchor|Node tabs).

def test_tree_root_row_is_selectable(main_window, tmp_path):
    """Z.2: the tree's root row IS its anchor and must be selectable — it is the
    one row that answers "where does the whole tree stand"."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    assert anchor_item.flags() & Qt.ItemFlag.ItemIsSelectable
    tree_widget.setCurrentItem(anchor_item)
    assert tree_widget.currentItem() is anchor_item


def test_embedded_in_pseudo_root_stays_unselectable(main_window, tmp_path):
    """Z.2: only the anchor root row became selectable — the "⇐ embedded in"
    navigation pseudo-root must stay unselectable."""
    dock, _root = _module_dock(main_window, tmp_path)
    dock.tree_tabs.setCurrentIndex(dock._trees.index(_tree_of(dock, "ch0_dac_buf")))
    tree_widget = dock._current_tree_widget()
    embedded = [it for it in _children(tree_widget.invisibleRootItem())
                if it.text(0).startswith("⇐ embedded in")]
    assert embedded, "expected an embedded-in pseudo-root"
    assert not (embedded[0].flags() & Qt.ItemFlag.ItemIsSelectable)


def test_selecting_the_root_row_shows_the_anchor_form(main_window, tmp_path):
    """Z.2 + Z.3.2: clicking the tree's ROOT row shows the tree ANCHOR form in
    the single right-hand panel."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    tree_widget.setCurrentItem(anchor_item)
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    assert form._tree is dock._trees[0]


def test_root_node_root_selection_switches_the_single_panel(main_window, tmp_path):
    """Z.3.2: selection root -> node -> root swaps the panel content (anchor ->
    node -> anchor) while the page stays BUILT (`_panel_built`) — a content swap,
    never a structural rebuild."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    tree_widget.expandAll()
    panel = dock._active_form_panel()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    nodes = _children(anchor_item)
    tree_widget.setCurrentItem(anchor_item)
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)
    assert panel.property("_panel_built") is True
    tree_widget.setCurrentItem(nodes[0])
    assert isinstance(_embedded_form(dock._active_form_page()), NodeFormWidget)
    assert panel.property("_panel_built") is True
    tree_widget.setCurrentItem(anchor_item)
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)
    assert panel.property("_panel_built") is True


def test_panel_shows_the_mount_node_form_with_its_picker(main_window, tmp_path):
    """Z.3.2 + B1 regression (Z.6.4): selecting a kind "mount" node shows its
    NodeFormWidget with the mount anchor picker VISIBLE — the picker must survive
    the single-panel refactor."""
    trees = {"trees": [
        {"name": "t", "anchor": {"origin": True},
         "nodes": [
             {"ref": "m_ad_dac", "kind": "mount", "xy": [0.0, 0.0],
              "anchor": {"role": "AD_DAC", "pad": "3"},
              "children": [{"ref": "E1", "kind": "external", "xy": [1.0, 0.0]}]},
         ]},
    ]}
    dock, _root = _dock_with(main_window, tmp_path, trees)
    tree_widget = dock._current_tree_widget()
    tree_widget.expandAll()
    tree_widget.setCurrentItem(dock._node_items["m_ad_dac"])
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, NodeFormWidget)
    assert form.kind_combo.currentData() == "mount"
    assert form.mount_anchor_widget.isHidden() is False


def test_context_menu_on_the_root_row_offers_no_node_actions(
        main_window, tmp_path, monkeypatch):
    """Z.2/Z.6.7: with the root row selected the context menu offers the ANCHOR
    actions only, and "Set anchor…" must not treat the anchor as a node."""
    dock, _root = _dock_with(main_window, tmp_path)
    tree_widget = dock._current_tree_widget()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    tree_widget.setCurrentItem(anchor_item)
    actions = dict(_context_menu_actions(dock, anchor_item, monkeypatch))
    assert "Set anchor…" in actions and "Add node" in actions
    for forbidden in ("Add child", "Add sibling", "Edit node…",
                      "Delete node", "Rename…", "Move to…"):
        assert forbidden not in actions
    actions["Set anchor…"].trigger()          # must not raise / must not misfire
    assert isinstance(_embedded_form(dock._active_form_page()), AnchorFormWidget)


def test_double_click_on_the_root_row_is_a_noop(main_window, tmp_path):
    """Z.6.7: double-clicking the root row (no TreeNode payload) does nothing —
    no crash, nothing applied to the anchor as if it were a node."""
    dock, _root = _dock_with(main_window, tmp_path)
    anchor_item = _children(dock._current_tree_widget().invisibleRootItem())[0]
    dock._on_node_activated(anchor_item, 0)   # must not raise
    assert dock._selected_real_node(dock._current_tree()) is None


# ═══════════════════════════════════════════════════════════════════════════
# Tree settings form (plan_2026_09_11_tree_settings_form §W.2–W.8): the inner
# point (pivot-*) and the tree's own angle, edited on the ROOT row — in the
# PCB Editor frame, like every other form (design §3.9).
# ═══════════════════════════════════════════════════════════════════════════

def _settings_form(dock):
    form = _embedded_form(dock._active_form_page())
    assert isinstance(form, AnchorFormWidget)
    return form


def _one_tree_dock(main_window, tmp_path, tree):
    return _dock_with(main_window, tmp_path, {"trees": [tree]})


def _form_for_tree(main_window, tree, *, cfg=None):
    """A STANDALONE tree-settings form (no dock) — for cases whose node refs
    would not survive load_config's record linking."""
    return AnchorFormWidget(main_window, [], cfg=cfg, tree=tree)


def test_tree_settings_form_shows_pivot_and_angle_fields(main_window, tmp_path):
    """§W.8.1 item 1: selecting the root row shows the TREE settings — a
    suspension-point picker and an angle field, not just the anchor."""
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True},
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    assert form.settings_box.isVisibleTo(form)
    assert form.pivot_mode_combo.isVisibleTo(form)
    assert form.rotation_edit.isVisibleTo(form)


def test_tree_settings_noop_apply_leaves_pivot_xy_unchanged(main_window, tmp_path):
    """§W.8.1 item 2 — THE round-trip gate: open a tree with pivot-xy, apply
    nothing, and the config must not move a nanometre."""
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.5, -2.25],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert tree.pivot_xy == (1.5, -2.25)
    assert form.apply() is True
    assert tree.pivot_xy == (1.5, -2.25)
    assert tree.pivot_polar is None and tree.pivot_ref is None
    assert tree.rotation == 0.0


def test_tree_settings_noop_apply_leaves_pivot_polar_unchanged(main_window, tmp_path):
    """§W.8.1 item 3: same round trip for pivot-polar (radius untouched, angle
    exact — never via Cartesian)."""
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_polar": [3.0, 45.0],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert tree.pivot_polar == (3.0, 45.0)
    assert form.apply() is True
    assert tree.pivot_polar == (3.0, 45.0)
    assert tree.pivot_xy is None and tree.pivot_ref is None


def test_tree_settings_noop_apply_leaves_pivot_ref_unchanged(main_window, tmp_path):
    """§W.8.1 item 3: same round trip for pivot-ref (a name, nothing to
    convert). Built as a STANDALONE form because a non-external ref would not
    survive the throwaway config's record linking."""
    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "pivot_ref": "A",
        "nodes": [{"ref": "A", "kind": "placement", "xy": [1.0, 0.0]}]})
    form = _form_for_tree(main_window, tree, cfg=object())
    assert form.apply() is True
    assert tree.pivot_ref == "A"
    assert tree.pivot_xy is None and tree.pivot_polar is None


def test_tree_settings_angle_is_shown_absolute_and_saved_as_dovorot(
        main_window, tmp_path, monkeypatch):
    """§W.8.1 item 4 + §3.9: the angle field shows the ABSOLUTE plate angle
    (anchor angle + dovоrот); the config keeps only the dovоrот."""
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 90.0))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "rotation": 30.0,
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert float(form.rotation_edit.text()) == pytest.approx(120.0)  # 90 + 30
    assert form.apply() is True
    assert tree.rotation == pytest.approx(30.0)   # the dovоrот survived


def test_tree_settings_pivot_xy_is_shown_in_board_mm(main_window, tmp_path, monkeypatch):
    """§W.8.1 item 5: pivot-xy shows as a BOARD-frame vector (the five pure
    functions), the config keeps the tree frame."""
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 90.0))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.0, 2.0],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    # rotate (1, 2) by 90 in the Y-down convention -> (2, -1)
    assert float(form.pivot_widget.x_edit.text()) == pytest.approx(2.0)
    assert float(form.pivot_widget.y_edit.text()) == pytest.approx(-1.0)
    assert form.apply() is True
    assert tree.pivot_xy == (1.0, 2.0)


def test_tree_settings_inner_point_sources_are_mutually_exclusive(main_window, tmp_path):
    """§W.8.1 item 6: a node handle cancels the coordinate and vice versa."""
    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.0, 2.0],
        "nodes": [{"ref": "A", "kind": "placement", "xy": [0.0, 0.0]}]})
    form = _form_for_tree(main_window, tree, cfg=object())
    form.pivot_mode_combo.setCurrentIndex(form.pivot_mode_combo.findData("node"))
    form.pivot_ref_combo.setCurrentIndex(0)
    settings, err = form.build_settings()
    assert err is None
    assert settings["pivot_ref"] == "A" and settings["pivot_xy"] is None
    form.pivot_mode_combo.setCurrentIndex(
        form.pivot_mode_combo.findData("coordinate"))
    form.pivot_widget.x_edit.setText("1")
    form.pivot_widget.y_edit.setText("2")
    settings, err = form.build_settings()
    assert err is None
    assert settings["pivot_ref"] is None and settings["pivot_xy"] is not None


def test_tree_settings_angle_change_redisplays_pivot_without_storing(
        main_window, tmp_path, monkeypatch):
    """§W.8.2 item 7 (the W.4.1 trap): changing the ANGLE re-expresses the
    coordinate display, but the STORED pivot-xy is untouched."""
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 0.0))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.0, 2.0],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert float(form.pivot_widget.x_edit.text()) == pytest.approx(1.0)
    form.rotation_edit.setText("90")          # user edits the ANGLE only
    # display moved with the angle...
    assert float(form.pivot_widget.x_edit.text()) == pytest.approx(2.0)
    assert float(form.pivot_widget.y_edit.text()) == pytest.approx(-1.0)
    # ...but the stored value stayed put
    assert form.apply() is True
    assert tree.pivot_xy == (1.0, 2.0)
    assert tree.rotation == pytest.approx(90.0)


def test_tree_settings_coordinates_after_angle_use_the_new_angle(
        main_window, tmp_path, monkeypatch):
    """§W.8.2 item 8: after an angle change, typed board-mm coordinates are
    converted with the NEW angle (no cached base)."""
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 0.0))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.0, 2.0],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    form.rotation_edit.setText("90")
    form.pivot_widget.x_edit.setText("3")
    form.pivot_widget.y_edit.setText("4")
    assert form.apply() is True
    # board (3, 4) with eff_rot 90 -> tree frame (-4, 3)
    assert tree.pivot_xy == (-4.0, 3.0)
    assert tree.rotation == pytest.approx(90.0)


def test_tree_settings_node_picker_lists_only_handles(main_window, tmp_path):
    """§W.8.3 item 9: the picker offers record-backed nodes OUTSIDE mount
    subtrees — never mount/module/external or a pinned node."""
    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True},
        "nodes": [
            {"ref": "M1", "kind": "mount", "anchor": {"role": "R"},
             "children": [{"ref": "PINNED", "kind": "placement", "xy": [1.0, 1.0]}]},
            {"ref": "FREE", "kind": "placement", "xy": [0.0, 0.0]},
            {"ref": "EXT", "kind": "external"},
        ]})
    form = _form_for_tree(main_window, tree, cfg=object())
    offered = [form.pivot_ref_combo.itemData(i)
               for i in range(form.pivot_ref_combo.count())]
    assert offered == ["FREE"]


def test_tree_settings_empty_picker_is_explained_and_coordinate_works(
        main_window, tmp_path):
    """§W.8.3 item 11 (the working ch0_dac_buf shape): an empty list is a
    NORMAL state — clearly explained, coordinate still available, form usable."""
    tree = tree_from_dict({
        "name": "ch0_dac_buf", "anchor": {"origin": True},
        "nodes": [
            {"ref": "M1", "kind": "mount", "anchor": {"role": "AD_DAC"},
             "children": [{"ref": "pif_dvdd", "kind": "placement",
                           "xy": [0.0, 0.0]}]}]})
    form = _form_for_tree(main_window, tree, cfg=object())
    assert form.pivot_hint_label.isVisibleTo(form)
    assert form.pivot_hint_label.text() != ""
    assert form.pivot_ref_combo.isEnabled() is False
    form.pivot_mode_combo.setCurrentIndex(
        form.pivot_mode_combo.findData("coordinate"))
    form.pivot_widget.x_edit.setText("1")
    form.pivot_widget.y_edit.setText("2")
    settings, err = form.build_settings()
    assert err is None
    assert settings["pivot_xy"] is not None


def test_tree_settings_fields_disabled_when_anchor_does_not_resolve(
        main_window, tmp_path, monkeypatch):
    """§W.8.4 item 12: an unresolvable anchor disables the settings with a
    reason and Apply writes NOTHING (never numbers of a silently changed
    meaning)."""
    def _boom(*a, **k):
        raise ValidationError("no live board")

    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position", _boom)
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True}, "pivot_xy": [1.0, 2.0],
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert form.rotation_edit.isEnabled() is False
    assert form.settings_frame_label.text() != ""
    assert form.apply() is False
    assert tree.pivot_xy == (1.0, 2.0) and tree.rotation == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Anchor's own SHIFT (§X.2.3) + mode table (§X.3): board-frame display, no
# cached angle, disabled with a reason, and every mode round-trips.
# ═══════════════════════════════════════════════════════════════════════════

def test_anchor_shift_shown_in_board_mm_stored_local(main_window, tmp_path, monkeypatch):
    """X.5.2 #11: the shift field shows the BOARD frame (anchor angle 90 turns
    the stored (1, 2) into a (2, -1) board vector); the config keeps (1, 2)."""
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 90.0))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True, "shift": [1.0, 2.0]},
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert float(form.shift_x_edit.text()) == pytest.approx(2.0)
    assert float(form.shift_y_edit.text()) == pytest.approx(-1.0)
    assert form.apply() is True
    assert tree.anchor.shift_xy == (1.0, 2.0)


def test_anchor_shift_noop_apply_leaves_it_unchanged(main_window, tmp_path):
    """X.5.2 #10: open a tree with an anchor shift, apply nothing, the config
    does not move a nanometre (origin anchor -> offline angle 0)."""
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True, "shift": [5.0, -2.25]},
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert tree.anchor.shift_xy == (5.0, -2.25)
    assert form.apply() is True
    assert tree.anchor.shift_xy == (5.0, -2.25)


def test_anchor_shift_display_follows_new_anchor_not_stored(
        main_window, tmp_path, monkeypatch):
    """X.5.2 #12 (the X.2.3 trap): switching the anchor changes the DISPLAY of
    the shift (new anchor angle) but NOT the stored value; typing after the
    switch is converted by the NEW angle. No cached angle survives."""
    angle = {"v": 0.0}
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position",
                        lambda *a, **k: (Vector2.from_xy(0, 0), angle["v"]))
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True, "shift": [1.0, 2.0]},
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert float(form.shift_x_edit.text()) == pytest.approx(1.0)
    # Switch the anchor to a role whose live angle is 90 — the mode switch
    # refreshes the board frame at once (no timer).
    angle["v"] = 90.0
    form.mode_combo.setCurrentIndex(form.mode_combo.findData("role"))
    form.role_edit.setCurrentText("R")
    # Fill the role, then re-express the board frame (the field edit is
    # debounced behind a 250 ms timer in the live UI, not synchronous here).
    form._reload_settings_display()
    assert float(form.shift_x_edit.text()) == pytest.approx(2.0)
    assert float(form.shift_y_edit.text()) == pytest.approx(-1.0)
    # Typed board coords are converted by the NEW angle: board (3, 4) at 90 ->
    # tree frame (-4, 3).
    form.shift_x_edit.setText("3")
    form.shift_y_edit.setText("4")
    assert form.apply() is True
    assert tree.anchor.shift_xy == (-4.0, 3.0)


def test_anchor_shift_disabled_with_reason_when_anchor_does_not_resolve(
        main_window, tmp_path, monkeypatch):
    """X.5.2 #13: an unresolvable anchor disables the shift with a reason, and
    Apply writes nothing (the stored shift is untouched)."""
    def _boom(*a, **k):
        raise ValidationError("no live board")

    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position", _boom)
    dock, _root = _one_tree_dock(main_window, tmp_path, {
        "name": "t", "anchor": {"origin": True, "shift": [1.0, 2.0]},
        "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]})
    form = _settings_form(dock)
    tree = dock._current_tree()
    assert form.shift_x_edit.isEnabled() is False
    assert form.shift_y_edit.isEnabled() is False
    assert form.shift_reason_label.text() != ""
    assert form.apply() is False
    assert tree.anchor.shift_xy == (1.0, 2.0)


def test_every_tree_anchor_mode_roundtrips(main_window):
    """X.5.3 #15: every mode remaining in the grammar is reachable and
    round-trips without loss (adding a mode = one _TREE_ANCHOR_MODES entry)."""
    expected = {
        "origin": dict(ref=None, is_origin=True, is_external=False),
        "self": dict(is_self=True),
        "role": dict(role="R", anchor_sheet="S", anchor_cluster="C", anchor_pad="3"),
        "point": dict(point="P"),
        "record": dict(ref="REC", is_external=False),
        "external": dict(ref="U3", is_external=True),
    }
    for mode, kwargs in expected.items():
        form = AnchorFormWidget(main_window, [("placement", "REC")], cfg=object())
        assert form.mode_combo.findData(mode) >= 0
        form.mode_combo.setCurrentIndex(form.mode_combo.findData(mode))
        if mode == "role":
            form.role_edit.setCurrentText("R")
            form.sheet_edit.setCurrentText("S")
            form.cluster_edit.setCurrentText("C")
            form.pad_edit.setText("3")
        elif mode == "point":
            form.point_edit.setCurrentText("P")
        elif mode in ("record", "external"):
            form.ref_combo.setCurrentText(kwargs["ref"])
        anchor, err = form.build_anchor()
        assert err is None, mode
        assert anchor == TreeAnchor(**kwargs), mode


def test_anchor_origin_widget_fields_survive_shared_builder(main_window):
    """X.5.3 #16: the shared field builder did not change AnchorOriginWidget's
    public attribute surface (Points/Placer/Chain/ThermalVia/NetTrace rely on
    it)."""
    from gui.docks._anchor_origin import AnchorOriginWidget
    w = AnchorOriginWidget(modes=["xy", "anchor", "point", "board_origin"],
                           anchor_fields=["sheet", "pad", "cluster"], shift=True,
                           polar=True)
    for attr in ("anchor_ref_edit", "anchor_role_edit", "anchor_sheet_edit",
                 "anchor_pad_edit", "anchor_cluster_edit", "point_edit",
                 "x_edit", "y_edit", "shift_x_edit", "shift_y_edit"):
        assert getattr(w, attr) is not None, attr
    w.origin_mode_combo.setCurrentIndex(w._modes.index("anchor"))
    w.anchor_role_edit.setCurrentText("R")
    fields, err = w.build()
    assert err is None
    assert fields["role"] == "R"


def test_render_tree_marks_the_pivot_ref_handle(main_window):
    """§W.8.5 items 13/14: the node named in pivot-ref is marked in the tree
    (a handle tag, single column), and moving the ref moves the mark."""
    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "pivot_ref": "A",
        "nodes": [{"ref": "A", "kind": "placement", "xy": [0.0, 0.0]},
                  {"ref": "B", "kind": "placement", "xy": [1.0, 0.0]}]})
    dock = TreesDock(main_window)
    widget = QTreeWidget()
    dock._render_tree(widget, tree)
    assert "(handle)" in dock._node_items["A"].text(0)
    assert "(handle)" not in dock._node_items["B"].text(0)
    tree.pivot_ref = "B"
    dock._refresh_tree_marks(tree)
    assert "(handle)" not in dock._node_items["A"].text(0)
    assert "(handle)" in dock._node_items["B"].text(0)


# ═══════════════════════════════════════════════════════════════════════════
# Tree anchor/base overlay circles (З, plan_2026_09_12_tree_point_markers.md):
# the "Show tree markers" toggle on the tree-settings form. The fake adapter is
# the same duck surface gui/board_overlay.py and gui/overlay_markers.py already
# use, so created circles REALLY land on the fake board — "two circles, not
# one" is observable rather than asserted on a call log.
# ═══════════════════════════════════════════════════════════════════════════

from types import SimpleNamespace

from PyQt6.QtWidgets import QInputDialog

from kicadstamp.utils.units import MM

# This module ALREADY rebinds the name `BoardLayer` (the copper-only domain
# enum from kicadstamp.domain.geometry, used by the cluster/mirror tests above)
# — importing kipy's full BoardLayer under the same name here would silently
# shadow it for EVERY test in the file (found the hard way: the two "MIRRORED"
# tests broke), so kipy's enum gets an explicit alias.
from kipy.board_types import BoardCircle
from kipy.board_types import BoardLayer as KicadBoardLayer

import gui.overlay_markers as markers_mod

_tree_anchor_key = trees_dock_mod._tree_anchor_key
_tree_base_key = trees_dock_mod._tree_base_key

_TREE_LAYER = KicadBoardLayer.BL_Dwgs_User
_TREE_OTHER_LAYER = KicadBoardLayer.BL_User_5
_ANCHOR_XY = (100.0, 50.0)


class _OverlayBoard:
    """Duck-typed `_board` — the exact surface gui/board_overlay.py reads."""

    def __init__(self, layers=None):
        self.layers = (list(layers) if layers is not None
                       else [_TREE_LAYER, _TREE_OTHER_LAYER])
        self.shapes = []
        self.names = {_TREE_LAYER: "User.Drawings",
                      _TREE_OTHER_LAYER: "User.KiCadStamp"}

    def get_enabled_layers(self):
        return list(self.layers)

    def get_layer_name(self, layer):
        return self.names.get(layer, str(layer))

    def get_shapes(self):
        return list(self.shapes)


class _OverlayAdapter:
    """Board-mutation-free fake with the overlay-drawing duck surface (the same
    one tests/gui/test_points_dock.py uses): created shapes are registered on
    the fake board and remove_by_ids() really removes them."""

    def __init__(self, board=None):
        self._board = board if board is not None else _OverlayBoard()
        self.created = []
        self.removed = []
        self.refreshes = 0
        self.selections = []
        self._next_id = 0

    def refresh_board(self):
        self.refreshes += 1

    def create_items(self, items):
        items = list(items)
        for item in items:
            self._next_id += 1
            item.id.value = f"shape-{self._next_id}"
        self.created.extend(items)
        self._board.shapes = list(self._board.shapes) + items
        return items

    def select_items(self, items):
        self.selections.append(list(items))

    def remove_by_ids(self, uuid_strs):
        doomed = {str(u) for u in uuid_strs}
        self.removed.extend(uuid_strs)
        self._board.shapes = [s for s in self._board.shapes
                              if str(s.id.value) not in doomed]
        return True


def _tree_circles(adapter, layer=_TREE_LAYER):
    return [s for s in adapter._board.shapes
            if isinstance(s, BoardCircle) and s.layer == layer]


@pytest.fixture
def sync_long_ops(monkeypatch):
    """Run every start_long_op in trees_dock INLINE: headless tests never spin
    the worker thread, so a draw/removal is complete when the call returns."""
    def _run(connection, widgets, fn, on_success, on_error, *args):
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 — mirror the real worker contract
            on_error(str(e))
            return None
        on_success(result)
        return None
    monkeypatch.setattr(trees_dock_mod, "start_long_op", _run)


def _marker_cfg(name="t1", pivot_xy=(5.0, 0.0), rotation=0.0, point="P"):
    """A root config whose tree anchors to a points: entry at (100, 50) — an
    xy-literal point, so the anchor resolves with no board maths involved."""
    tree: dict = {"name": name, "anchor": {"point": point},
                  "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]}
    if pivot_xy is not None:
        tree["pivot_xy"] = list(pivot_xy)
    if rotation:
        tree["rotation"] = rotation
    return {"points": {point: {"xy": list(_ANCHOR_XY)}}, "trees": [tree]}


def _marker_dock(main_window, tmp_path, cfg):
    """(dock, root, adapter, active anchor form) — a TreesDock on `cfg` WITH a
    live (fake) board, and the anchor form its active page shows."""
    dock, root = _dock_with(main_window, tmp_path, cfg)
    adapter = _OverlayAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return dock, root, adapter, _settings_form(dock)


def _no_modal(monkeypatch):
    """Fail loudly if any modal is shown — a visualisation must never raise a
    dialog (Е.2.6 / З.2.4)."""
    def _boom(*_a, **_k):
        raise AssertionError("no modal must ever be shown")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, _boom)


def test_show_markers_draws_anchor_and_base_two_circles_numbers(
        main_window, tmp_path, sync_long_ops):
    """З.4.1 — pivot (5, 0), no rotation, known anchor: TWO circles, and both
    centres are NUMBERS against _anchor_base_live_position / tree_layout_base
    (not against a hand-copied literal)."""
    dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    tree = dock._current_tree()

    assert form.show_markers_button.text() == "Show tree markers"
    form._do_toggle_markers()

    circles = _tree_circles(adapter)
    assert len(circles) == 2
    anchor_pos, _arot = trees_dock_mod._anchor_base_live_position(
        adapter, dock._cfg, tree, {})
    base_pos, _brot = trees_dock_mod.tree_layout_base(
        adapter, dock._cfg, tree, {}, {t.name: t for t in dock._trees})
    assert (circles[0].center.x, circles[0].center.y) == (anchor_pos.x, anchor_pos.y)
    assert (circles[1].center.x, circles[1].center.y) == (base_pos.x, base_pos.y)
    # …and the millimetre numbers of З.1's own measurement.
    assert circles[0].center.x == int(100.0 * MM)
    assert circles[0].center.y == int(50.0 * MM)
    assert circles[1].center.x == int(95.0 * MM)
    assert circles[1].center.y == int(50.0 * MM)
    assert markers_mod.owner.has_key(_tree_anchor_key("t1"))
    assert markers_mod.owner.has_key(_tree_base_key("t1"))
    assert form.show_markers_button.text() == "Hide tree markers"


def test_show_markers_with_a_zero_pivot_draws_one_circle(
        main_window, tmp_path, sync_long_ops):
    """З.4.2 — a zero suspension point puts base == anchor, so exactly ONE
    circle is drawn and there is no tree-base key at all."""
    _dock, _root, adapter, form = _marker_dock(
        main_window, tmp_path, _marker_cfg(pivot_xy=None))

    form._do_toggle_markers()

    circles = _tree_circles(adapter)
    assert len(circles) == 1
    assert (circles[0].center.x, circles[0].center.y) == (int(100.0 * MM),
                                                          int(50.0 * MM))
    assert markers_mod.owner.has_key(_tree_anchor_key("t1"))
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))


def test_zeroing_the_pivot_then_showing_again_leaves_one_circle(
        main_window, tmp_path, sync_long_ops):
    """З.4.3 / З.2.3 — shown at pivot (5, 0), then the user zeroes the pivot
    and turns the toggle back on: ONE circle and no tree-base key."""
    dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    tree = dock._current_tree()

    form._do_toggle_markers()
    assert len(_tree_circles(adapter)) == 2

    tree.pivot_xy = None          # the user zeroes the suspension point
    form._do_toggle_markers()     # OFF
    form._do_toggle_markers()     # ON again, now with a zero pivot

    assert len(_tree_circles(adapter)) == 1
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))
    assert markers_mod.owner.has_key(_tree_anchor_key("t1"))


def test_show_drops_a_stale_tree_base_key_when_base_meets_anchor(
        main_window, tmp_path, sync_long_ops):
    """З.2.3's second half: a `tree-base` key left over from a DIFFERENT pivot
    is removed by the very draw that finds base == anchor — two circles in one
    spot are impossible even for a leftover key."""
    _dock, _root, adapter, form = _marker_dock(
        main_window, tmp_path, _marker_cfg(pivot_xy=None))
    stale = markers_mod.owner.ensure_marker(
        adapter, _tree_base_key("t1"), 1.0, 2.0)
    assert stale is not None
    assert len(_tree_circles(adapter)) == 1

    form._do_toggle_markers()

    assert len(_tree_circles(adapter)) == 1        # the stale one is gone
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))


def test_show_markers_rotation_90_moves_the_base(
        main_window, tmp_path, sync_long_ops):
    """З.4.4 — the tree's own 90° rotation moves the base by the rotated pivot
    (З.1's measurement: anchor (100, 50) -> base (100, 55))."""
    _dock, _root, adapter, form = _marker_dock(
        main_window, tmp_path, _marker_cfg(rotation=90.0))

    form._do_toggle_markers()

    circles = _tree_circles(adapter)
    assert len(circles) == 2
    assert (circles[0].center.x, circles[0].center.y) == (int(100.0 * MM),
                                                          int(50.0 * MM))
    assert (circles[1].center.x, circles[1].center.y) == (int(100.0 * MM),
                                                          int(55.0 * MM))


def test_switching_tree_takes_the_previous_trees_circles_down(
        main_window, tmp_path, sync_long_ops):
    """З.4.5 / З.2.5 — only the tree being looked at may keep its circles:
    switching tabs drops the previous tree's keys and shapes, and the new
    tree's toggle starts from "Show"."""
    cfg = {"points": {"P": {"xy": list(_ANCHOR_XY)},
                      "Q": {"xy": [10.0, 20.0]}},
           "trees": [
               {"name": "t1", "anchor": {"point": "P"}, "pivot_xy": [5.0, 0.0],
                "nodes": [{"ref": "E1", "kind": "external", "xy": [0.0, 0.0]}]},
               {"name": "t2", "anchor": {"point": "Q"},
                "nodes": [{"ref": "E2", "kind": "external", "xy": [0.0, 0.0]}]}]}
    dock, _root, adapter, form1 = _marker_dock(main_window, tmp_path, cfg)

    form1._do_toggle_markers()
    assert len(_tree_circles(adapter)) == 2
    assert markers_mod.owner.has_key(_tree_anchor_key("t1"))

    dock.tree_tabs.setCurrentIndex(1)

    assert not markers_mod.owner.has_key(_tree_anchor_key("t1"))
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))
    assert _tree_circles(adapter) == []
    form2 = _settings_form(dock)
    assert form2._tree is dock._trees[1]
    assert form2.show_markers_button.text() == "Show tree markers"

    form2._do_toggle_markers()
    assert len(_tree_circles(adapter)) == 1     # t2 has no pivot -> one circle
    assert markers_mod.owner.has_key(_tree_anchor_key("t2"))


def test_second_press_clears_our_keys_and_spares_a_foreign_namespace(
        main_window, tmp_path, sync_long_ops):
    """З.4.6 — the second press drops both tree keys and their shapes; a shape
    of a DIFFERENT namespace (the points consumer) is untouched."""
    _dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    foreign_key = "point/p1"
    foreign_uuid = markers_mod.owner.ensure_marker(adapter, foreign_key, 50.0, 50.0)

    form._do_toggle_markers()
    assert form.show_markers_button.text() == "Hide tree markers"
    assert len(_tree_circles(adapter)) == 3     # anchor + base + foreign

    form._do_toggle_markers()

    assert not markers_mod.owner.has_key(_tree_anchor_key("t1"))
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))
    assert markers_mod.owner.has_key(foreign_key)
    assert foreign_uuid not in [str(u) for u in adapter.removed]
    assert len(_tree_circles(adapter)) == 1     # only the foreign circle
    assert form.show_markers_button.text() == "Show tree markers"


def test_toggle_without_a_board_logs_and_shows_no_modal(
        main_window, tmp_path, monkeypatch, caplog):
    """З.4.7 (a) — no board: one Log line, no exception, no modal, and the
    button never "sticks"."""
    dock, _root = _dock_with(main_window, tmp_path, _marker_cfg())
    form = _settings_form(dock)
    _no_modal(monkeypatch)

    form._on_show_markers()

    assert "Not connected." in caplog.text
    assert markers_mod.owner.keys() == []
    assert form.show_markers_button.text() == "Show tree markers"


def test_toggle_with_an_unresolvable_anchor_logs_and_draws_nothing(
        main_window, tmp_path, sync_long_ops, monkeypatch, caplog):
    """З.4.7 (b) — the anchor does not resolve (its component is not on the
    board): a Log line naming the tree, no exception, no modal, no circles."""
    _dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())

    def _boom(*_a, **_k):
        raise ValidationError("anchor gone")
    monkeypatch.setattr(trees_dock_mod, "_anchor_base_live_position", _boom)
    _no_modal(monkeypatch)

    form._do_toggle_markers()

    assert _tree_circles(adapter) == []
    assert markers_mod.owner.keys() == []
    assert "did not resolve" in caplog.text
    assert form.show_markers_button.text() == "Show tree markers"


def test_toggle_dispatches_on_a_worker_with_the_button_locked(
        main_window, tmp_path, monkeypatch):
    """Both halves run through start_long_op (every position is a LIVE read),
    with the toggle locked — the same discipline every other dock long op has.
    The hide half touches OUR two keys only (remove_overlay, not a sweep)."""
    dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    captured = {}
    def fake_start(connection, widgets, worker, finish, failed, *args):
        captured.update(widgets=widgets, worker=worker, args=args)
        return object()
    monkeypatch.setattr(trees_dock_mod, "start_long_op", fake_start)

    form._on_show_markers()      # draw half

    assert captured["widgets"] == (form.show_markers_button,)
    assert captured["worker"] is trees_dock_mod._show_tree_markers_worker
    assert captured["args"][0]["tree"] is dock._current_tree()
    assert captured["args"][0]["adapter"] is adapter

    markers_mod.owner.ensure_marker(
        adapter, _tree_anchor_key("t1"), 1.0, 2.0)
    captured.clear()
    form._on_show_markers()      # hide half

    assert captured["widgets"] == (form.show_markers_button,)
    assert captured["worker"] is trees_dock_mod.board_overlay.remove_overlay


def test_root_switch_clears_both_namespaces(
        main_window, tmp_path, sync_long_ops):
    """З.4.8 / З.2.5 — a new root config clears tree-anchor AND tree-base
    entirely (a foreign namespace's key survives), while a repeat call with the
    SAME root must not clear anything."""
    dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    foreign_key = "point/p1"
    markers_mod.owner.ensure_marker(adapter, foreign_key, 50.0, 50.0)
    form._do_toggle_markers()
    assert len(_tree_circles(adapter)) == 3

    other = tmp_path / "other.sexp"
    other.write_text(dict_to_sexp(_marker_cfg(name="t9")), encoding="utf-8")
    dock.set_root_file(other)

    assert [k for k in markers_mod.owner.keys()
            if k.startswith("tree-anchor/") or k.startswith("tree-base/")] == []
    assert markers_mod.owner.has_key(foreign_key)
    assert len(_tree_circles(adapter)) == 1     # only the foreign circle

    form9 = _settings_form(dock)
    form9._do_toggle_markers()
    assert markers_mod.owner.has_key(_tree_anchor_key("t9"))

    dock.set_root_file(other)     # the SAME root — a broadcast, not a switch

    assert markers_mod.owner.has_key(_tree_anchor_key("t9"))


def test_renaming_a_tree_drops_the_old_names_circles(
        main_window, tmp_path, sync_long_ops, monkeypatch):
    """З.2.5 — the overlay keys are named after the tree, so a rename through
    the dock takes the old name's circles (key AND shape) down."""
    dock, _root, adapter, form = _marker_dock(main_window, tmp_path, _marker_cfg())
    form._do_toggle_markers()
    assert len(_tree_circles(adapter)) == 2

    tree = dock._current_tree()
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *a, **k: ("t1_renamed", True))
    dock._on_rename_tree()

    assert tree.name == "t1_renamed"
    assert not markers_mod.owner.has_key(_tree_anchor_key("t1"))
    assert not markers_mod.owner.has_key(_tree_base_key("t1"))
    assert _tree_circles(adapter) == []
