# tests/gui/test_trees_dock_copper_pseudo_node.py
"""Сторожа Т1.5 of plan_2026_09_16_copper_pseudo_node: the "Copper" PSEUDO-NODE
of the trees dock — a VIEW-only row that gathers every kind="net_trace" node of
a tree in one foldable place.

What is pinned down here, per Т1.1-Т1.4:
  * the row exists only when the tree HAS copper, it is the LAST row under the
    anchor, and ALL copper nodes sit under it — wherever they lie in the file
    (root, a legacy "copper" container, deeper); in their file positions they are
    drawn nowhere, and the legacy container's own row is not drawn at all;
  * its UserRole payload is a MARKER OBJECT of its own: no place that decodes
    UserRole mistakes it for the anchor (None), for a navigation pseudo item
    (str) or for a real node (TreeNode) — the Ф1 trap the plan calls out;
  * its expansion has its OWN persisted key (copper_expanded), never
    anchor_expanded; the default is collapsed;
  * selecting it leaves the right-hand panel EMPTY and asks no exception;
  * checking it marks every copper row (Qt does NOT propagate a tristate
    parent's check state by itself — diagnostics/probe_tristate_propagation.py),
    and "Redraw selected" then gets exactly those refs, WITHOUT the pseudo row;
  * a copper node's context menu is exactly the three actions it can act on;
  * the re-read appends new copper to an EXISTING container, and to the ROOT
    when there is none — the container is no longer created;
  * the VIEW never touches the DATA: the tree serializes byte-identically before
    and after a render.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QTreeWidgetItemIterator

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import tree_to_sexp

import gui.docks.trees_dock as trees_dock_mod
from gui import settings
from gui.docks.trees_dock import TreesDock

# Т1.1: copper refs that are LEXICOGRAPHICALLY FIRST (digits sort before
# letters) — Denis' own profile shape, and the case that made the redraw-order
# bug visible. They also prove the pseudo node keeps DOCUMENT order, not a
# sorted one.
COPPER_REFS = [
    "2v5_oa__dac_buf__pif_oa_n2v5",
    "3v3_avdd__dac_buf__pif_avdd",
    "3v3_clkvdd__dac_buf__pif_clkvdd",
    "3v3_dvdd__dac_buf__pif_dvdd",
    "2v5_oa__dac_buf__pif_oa_p2v5",
]


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _dock_with(main_window, tmp_path, nodes, name="t"):
    """A TreesDock over a throwaway root config whose only tree is `name` with
    `nodes` — the s-expr section is the current way trees get in."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"trees": [
        {"name": name, "anchor": {"origin": True}, "nodes": nodes}]}),
        encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def _copper_tree_nodes():
    """Five root-level copper nodes and two ordinary ones — the shape Denis'
    `ch0_dac_buf` has today (Ф1)."""
    return ([{"ref": ref, "kind": "net_trace"} for ref in COPPER_REFS]
            + [{"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]},
               {"ref": "pif_avdd_channel_0", "kind": "external", "xy": [0.0, 0.0]}])


def _all_items(tree_widget):
    out = []
    it = QTreeWidgetItemIterator(tree_widget)
    while it.value():
        out.append(it.value())
        it += 1
    return out


def _pseudo_item(tree_widget):
    """The "Copper" pseudo-node's row, or None — found by the MARKER, which is
    the only thing that identifies it (Т1.2)."""
    for item in _all_items(tree_widget):
        if trees_dock_mod._is_copper_group_item(
                item.data(0, Qt.ItemDataRole.UserRole)):
            return item
    return None


def _pseudo_text(count):
    """The row's label for `count` copper nodes — asked of the SAME translation
    the dock uses, so the guard holds in either language."""
    return trees_dock_mod._("Copper ({count})").format(count=count)


def _context_menu_labels(dock, item, monkeypatch):
    """Runs _on_context_menu for `item` with QMenu.exec no-oped and returns the
    added labels in order (the same capture idiom tests/gui/test_trees_dock.py
    uses — the real QAction per label is what the dock connects)."""
    monkeypatch.setattr(trees_dock_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original = trees_dock_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        captured.append(text)
        return original(self, text, *a, **k)

    monkeypatch.setattr(trees_dock_mod.QMenu, "addAction", _record)
    tree_widget = dock._current_tree_widget()
    monkeypatch.setattr(tree_widget, "itemAt", lambda pos: item)
    dock._on_context_menu(QPoint(0, 0))
    return captured


# ── Т1.1: the view ────────────────────────────────────────────────────────

def test_pseudo_node_gathers_every_root_copper_node(main_window, tmp_path):
    """Т1.5.1: five copper nodes in the root + two ordinary ones -> the anchor
    has the two ordinary rows and "Copper (5)" LAST, all five copper rows sit
    under it in DOCUMENT order, and no copper row is left in the root."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    tree_widget = dock._current_tree_widget()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    rows = _children(anchor_item)

    assert [item.text(0) for item in rows] == [
        "dac_buf_channel_0 (external)",
        "pif_avdd_channel_0 (external)",
        _pseudo_text(5),
    ]
    pseudo = rows[-1]
    assert _pseudo_item(tree_widget) is pseudo
    # document order, NOT sorted: the 2v5/3v3 pair is split by position
    assert [item.text(0) for item in _children(pseudo)] == list(COPPER_REFS)
    # every copper row is reachable from the pseudo node and from nowhere else:
    # the anchor's own rows carry no TreeNode of kind net_trace
    assert [item.data(0, Qt.ItemDataRole.UserRole).kind
            for item in rows if item is not pseudo] == ["external", "external"]


def test_pseudo_node_gathers_copper_from_a_container_and_hides_the_container(
        main_window, tmp_path):
    """Т1.5.2 / Т1.1: copper in the ROOT and inside a legacy kind "copper"
    container ends up under the ONE pseudo node, and the container's own row is
    not drawn anywhere — while its node stays in the DATA untouched."""
    nodes = [
        {"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]},
        {"ref": "my_copper", "kind": "copper", "children": [
            {"ref": COPPER_REFS[0], "kind": "net_trace"}]},
        {"ref": COPPER_REFS[1], "kind": "net_trace"},
    ]
    dock, _root = _dock_with(main_window, tmp_path, nodes)
    tree_widget = dock._current_tree_widget()
    tree = dock._current_tree()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    rows = _children(anchor_item)

    assert [item.text(0) for item in rows] == [
        "dac_buf_channel_0 (external)", _pseudo_text(2)]
    pseudo = rows[-1]
    assert [item.text(0) for item in _children(pseudo)] == [
        COPPER_REFS[0], COPPER_REFS[1]]
    # the container has no row of its own anywhere in the widget
    assert all(item.text(0) != "my_copper (copper)"
               for item in _all_items(tree_widget))
    # ...and the data is exactly as it was written
    assert "my_copper" in [n.ref for n in tree.nodes]
    assert [c.ref for c in tree.nodes[1].children] == [COPPER_REFS[0]]


def test_no_copper_no_pseudo_node(main_window, tmp_path):
    """Т1.5.7: a tree without copper gets no pseudo row at all."""
    dock, _root = _dock_with(main_window, tmp_path, [
        {"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]}])
    tree_widget = dock._current_tree_widget()
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    assert [item.text(0) for item in _children(anchor_item)] == [
        "dac_buf_channel_0 (external)"]
    assert _pseudo_item(tree_widget) is None


def test_render_does_not_touch_the_data(main_window, tmp_path):
    """Т1.5.9: building the view leaves tree.nodes byte-identical — the
    pseudo-node is a view, the copper stays where the file put it."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    tree = dock._current_tree()
    before = tree_to_sexp(tree)
    dock._rebuild_tabs()  # a second render, from scratch
    assert tree_to_sexp(tree) == before
    assert [n.ref for n in tree.nodes] == [
        *COPPER_REFS, "dac_buf_channel_0", "pif_avdd_channel_0"]
    assert [n.kind for n in tree.nodes[:5]] == ["net_trace"] * 5


# ── Т1.2: the row as a row ────────────────────────────────────────────────

def test_pseudo_node_has_a_marker_of_its_own_not_none(main_window, tmp_path):
    """Т1.5.3 (Ф1 trap): the pseudo row must NOT carry UserRole None — that is
    what the anchor pseudo-root means, and the expand handler used to read it
    that way."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    pseudo = _pseudo_item(dock._current_tree_widget())
    data = pseudo.data(0, Qt.ItemDataRole.UserRole)
    assert data is not None
    assert not isinstance(data, str)
    assert not isinstance(data, trees_dock_mod.TreeNode)
    assert trees_dock_mod._is_copper_group_item(data)
    # ...and it is not a "node" for the rest of the dock either
    assert all(item is not pseudo for item in dock._node_items.values())
    assert len(dock._node_items) == 7  # 5 copper + 2 ordinary, nothing extra


def test_expanding_the_pseudo_node_persists_its_own_key(main_window, tmp_path):
    """Т1.5.3: expanding the pseudo row writes copper_expanded — never
    anchor_expanded (the anchor keeps its own, untouched)."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    pseudo = _pseudo_item(dock._current_tree_widget())
    assert pseudo.isExpanded() is False  # default: collapsed

    pseudo.setExpanded(True)
    dock._on_item_expand_changed("t", pseudo)
    entry = settings.state.get("trees_dock")["trees"]["t"]
    assert entry["copper_expanded"] is True
    assert "anchor_expanded" not in entry

    anchor_item = _children(dock._current_tree_widget().invisibleRootItem())[0]
    anchor_item.setExpanded(True)
    dock._on_item_expand_changed("t", anchor_item)
    entry = settings.state.get("trees_dock")["trees"]["t"]
    assert entry["anchor_expanded"] is True
    assert entry["copper_expanded"] is True


def test_saved_expansion_is_restored_and_captured(main_window, tmp_path):
    """Т1.2: the saved copper_expanded comes back on the next render, and the
    final-flush capture reports it (not the anchor's flag)."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    dock._update_tree_ui_state("t", lambda e: e.update({"copper_expanded": True}))
    dock._rebuild_tabs()
    tree_widget = dock._current_tree_widget()
    assert _pseudo_item(tree_widget).isExpanded() is True
    captured = dock._capture_tree_expansion(tree_widget)
    assert captured["copper_expanded"] is True
    assert captured["anchor_expanded"] is False


def test_selecting_the_pseudo_node_leaves_the_right_panel_empty(
        main_window, tmp_path):
    """Т1.5.4: selecting the pseudo row shows NOTHING on the right (not the
    anchor form, which is what "no node" means everywhere else) and raises
    nothing; double-clicking it does nothing either."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    tree_widget = dock._current_tree_widget()
    pseudo = _pseudo_item(tree_widget)

    tree_widget.setCurrentItem(pseudo)  # fires _on_selection_changed
    page = dock._active_form_page()
    assert page is not None
    assert page.property("_copper_group_page") is True
    assert dock._embedded_form_of(page) is None
    assert dock.status_label.text() == ""

    # the identity is its own, so a LATER click on the anchor row still brings
    # the anchor form back (the None->None guard must not swallow it)
    anchor_item = _children(tree_widget.invisibleRootItem())[0]
    tree_widget.setCurrentItem(anchor_item)
    assert dock._embedded_form_of(dock._active_form_page()) is not None
    assert dock._current_node_ref is None

    # double-click is a no-op, not a navigation
    tree_widget.setCurrentItem(pseudo)
    dock._on_node_activated(pseudo, 0)
    assert dock._embedded_form_of(dock._active_form_page()) is None


def test_checking_the_pseudo_node_marks_every_copper_row(main_window, tmp_path):
    """Т1.5.5: Qt does not propagate a tristate parent's check state by itself,
    so the dock does — checking "Copper" checks all five copper rows, and
    "Redraw selected" then collects exactly those refs (never the pseudo row,
    which is not in _node_items at all)."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    pseudo = _pseudo_item(dock._current_tree_widget())
    children = _children(pseudo)

    pseudo.setCheckState(0, Qt.CheckState.Checked)
    assert [c.checkState(0) for c in children] == [Qt.CheckState.Checked] * 5

    captured = []
    dock._run_curated_redraw = lambda refs, trigger=None: captured.append(refs)
    dock._on_redraw_selected()
    assert captured == [set(COPPER_REFS)]

    pseudo.setCheckState(0, Qt.CheckState.Unchecked)
    assert [c.checkState(0) for c in children] == [Qt.CheckState.Unchecked] * 5


def test_ordinary_nodes_keep_their_own_checkboxes(main_window, tmp_path):
    """The propagation is scoped to the pseudo row: checking an ORDINARY node
    still marks nothing but itself (no behaviour change outside Т1.2)."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    item = dock._node_items["dac_buf_channel_0"]
    item.setCheckState(0, Qt.CheckState.Checked)
    assert dock._node_items["pif_avdd_channel_0"].checkState(0) \
        == Qt.CheckState.Unchecked
    assert _pseudo_item(dock._current_tree_widget()).checkState(0) \
        == Qt.CheckState.Unchecked


# ── Т1.3: the copper node's menu ──────────────────────────────────────────

def test_copper_node_menu_is_exactly_the_three_useful_actions(
        main_window, tmp_path, monkeypatch):
    """Т1.5.6: a copper node offers Select copper on board / Redraw / Delete
    node and NOTHING else — no Add child/sibling, no Reread position, no
    Edit/Rename, no Move to/up/down."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    item = dock._node_items[COPPER_REFS[0]]
    labels = _context_menu_labels(dock, item, monkeypatch)
    assert labels == ["Select copper on board", "Redraw", "Delete node"]


def test_plain_node_menu_is_unchanged(main_window, tmp_path, monkeypatch):
    """Т1.5.6 (other half): an ordinary node's menu keeps every action it had."""
    dock, _root = _dock_with(main_window, tmp_path, [
        {"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]}])
    item = dock._node_items["dac_buf_channel_0"]
    labels = _context_menu_labels(dock, item, monkeypatch)
    assert labels == ["Add child", "Add sibling", "Reread current position",
                      "Edit node…", "Delete node", "Rename…", "Move to…"]


def test_pseudo_node_has_no_context_menu(main_window, tmp_path, monkeypatch):
    """Т1.2: right-clicking the pseudo row opens NOTHING — in particular not
    the anchor menu (Add node / Set anchor…), which is what a row that is
    neither a TreeNode nor a str used to fall through to."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    pseudo = _pseudo_item(dock._current_tree_widget())
    assert _context_menu_labels(dock, pseudo, monkeypatch) == []


# ── Т1.4: the re-read ─────────────────────────────────────────────────────

def _finish_reread(dock, added):
    """Runs the re-read's UI half with a stub plan: no report lines, no records
    to stage — what this guard is about is where the NEW NODES land."""
    plan = SimpleNamespace(added=list(added), updated=[], missing=[],
                           records=[])
    dock._finish_reread_internode_copper({
        "plan": plan, "cfg": dock._cfg, "tree": dock._current_tree(),
        "added": list(added), "narrowed_to_selection": True})


def test_reread_adds_to_the_root_and_creates_no_container(main_window, tmp_path,
                                                          monkeypatch):
    """Т1.5.8: without a container the new copper node goes to the ROOT (as it
    did before 2026-09-16) and no container is created — the VIEW now does the
    folding."""
    monkeypatch.setattr("kicadstamp.internode_capture.reread_report_lines",
                        lambda name, plan: [])
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    tree = dock._current_tree()
    _finish_reread(dock, ["new__a__b"])

    assert [n.ref for n in tree.nodes][-1] == "new__a__b"
    assert tree.nodes[-1].kind == "net_trace"
    assert not [n for n in tree.nodes if n.kind == "copper"]
    # the view picked it up under the pseudo node, without any container
    assert _pseudo_item(dock._current_tree_widget()).text(0) == _pseudo_text(6)


def test_reread_uses_the_existing_container(main_window, tmp_path, monkeypatch):
    """Т1.5.8 (other half): a tree that already HAS a container keeps using it —
    the fresh node goes inside, no second container appears, and nothing moves."""
    monkeypatch.setattr("kicadstamp.internode_capture.reread_report_lines",
                        lambda name, plan: [])
    nodes = [
        {"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]},
        {"ref": "my_copper", "kind": "copper", "children": [
            {"ref": COPPER_REFS[1], "kind": "net_trace"}]},
    ]
    dock, _root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _finish_reread(dock, ["new__a__b"])

    containers = [n for n in tree.nodes if n.kind == "copper"]
    assert [c.ref for c in containers] == ["my_copper"]
    assert [c.ref for c in containers[0].children] == [COPPER_REFS[1],
                                                       "new__a__b"]
    assert [n.ref for n in tree.nodes] == ["dac_buf_channel_0", "my_copper"]


def test_reread_children_target_helper(main_window, tmp_path):
    """The ONE predicate the append target is decided by: the container's own
    children list when one exists, else the tree's root list."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_tree_nodes())
    tree = dock._current_tree()
    assert TreesDock._copper_reread_children(tree) is tree.nodes

    nodes = [
        {"ref": "dac_buf_channel_0", "kind": "external", "xy": [0.0, 0.0]},
        {"ref": "my_copper", "kind": "copper", "children": [
            {"ref": COPPER_REFS[0], "kind": "net_trace"}]},
    ]
    dock2, _root2 = _dock_with(main_window, tmp_path, nodes)
    tree2 = dock2._current_tree()
    assert TreesDock._copper_reread_children(tree2) is tree2.nodes[1].children
