# tests/gui/docks/test_trees_delete.py
"""Сторожа Ч1 of plan_2026_10_22_trees_group_delete_and_no_apply: DELETING nodes
in the trees dock — the whole SELECTION, and a copper node's `net_traces:`
record with it.

What is pinned down here, one property per cell:

  * a GROUP of copper rows goes together with its records, and nothing else in
    the file moves (C1);
  * a selected CHILD of a selected parent rides with its ancestor — it is not
    "deleted twice", and every removed row goes through the ONE removal point
    (`_detach_node`, C2/C9);
  * a record another node still references STAYS, and the Log says so (C3);
  * the Delete KEY deletes the selection, and the same key pressed in a node
    form's FIELD deletes a character instead (C4 — the reason the handler is a
    filter on the tree widget, not a dock-wide shortcut);
  * a generated `tree_instances` tree deletes nothing (C5);
  * a `copper` container takes its children AND their records (C6);
  * in a MIXED selection the "Copper" pseudo-row is silently skipped and the
    real nodes go; a selection of the pseudo-row ALONE says why nothing
    happened (C7);
  * the menu label counts the selection: "Delete node" / "Delete N nodes" (C8);
  * `remove_net_trace_records` writes the removal to the file that OWNS the
    record, includes included files, and reports a record no file carries
    instead of inventing one (C10/C11).
"""
import logging

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from kicadstamp.config_writer import read_data
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import tree_from_dict

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_delete import (delete_action_text, referenced_identities,
                                     remove_net_trace_records, run_delete_nodes)
from gui.docks.trees_dock import TreesDock

from tests.fakes.write_later import write_later


COPPER = [
    "2v5_oa__dac_buf__pif_oa_n2v5",
    "3v3_avdd__dac_buf__pif_avdd",
    "3v3_clkvdd__dac_buf__pif_clkvdd",
]


@pytest.fixture(autouse=True)
def _restore_active_graph_root():
    """У3.5 (в), the same fixture the sibling copper cells use: the ACTIVE GRAPH
    ROOT is a process-wide value and a cell that points it at its own file must
    not leak it into the next one."""
    from kicadstamp.config_working_set import active_graph_root, set_active_graph_root

    previous = active_graph_root()
    yield
    set_active_graph_root(previous)


def _record(name):
    """A minimal `net_traces:` record — its content is irrelevant to these cells,
    only its IDENTITY (the node's ref) is."""
    return {"name": name, "net": "GND", "anchor_role": "FPGA",
            "tracks": [], "vias": []}


def _identities_in(nodes):
    """Every `net_trace` ref of a fixture tree (dict shape), children included —
    the records the config must carry for the nodes to load."""
    out = []
    for node in nodes:
        if node.get("kind") == "net_trace" and node.get("ref"):
            out.append(node["ref"])
        out.extend(_identities_in(node.get("children") or []))
    return out


def _dock_with(main_window, tmp_path, nodes, records=None, name="t"):
    """A TreesDock over a throwaway root config carrying one tree of `nodes` and
    a `net_traces:` section with one record per identity in `records` (default:
    every copper ref of the fixture).

    Ф3.7: the write goes through ``write_later`` — the dock's readers are cached
    by ``(path, mtime_ns)``, and a second write on the same tick would be
    invisible to them. The ACTIVE GRAPH ROOT is pointed at the file for the same
    reason the sibling copper cells do it: the write under test goes through
    `config_writer` (i.e. the format-3 stamp), which resolves references against
    the project graph."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({
        "net_traces": [_record(r) for r in (records if records is not None
                                            else _identities_in(nodes))],
        "trees": [{"name": name, "anchor": {"origin": True}, "nodes": nodes}],
    }, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(root)
    return dock, root


def _copper_nodes(*refs):
    return [{"ref": ref, "kind": "net_trace"} for ref in refs]


def _records_in(root):
    """The identities of the `net_traces:` records the config carries NOW."""
    return [entry.get("name") or entry.get("net")
            for entry in (read_data(root).get("net_traces") or [])]


def _select(dock, *refs):
    """Select exactly the rows of `refs` (Ctrl-click equivalent)."""
    tree_widget = dock._current_tree_widget()
    tree_widget.clearSelection()
    for ref in refs:
        dock._node_items[ref].setSelected(True)


# ── C1: the group, and its records ─────────────────────────────────────────

def test_a_group_of_copper_nodes_leaves_with_their_records(
        main_window, tmp_path, caplog):
    """The reported defect: a GROUP of copper rows selected, "delete" did nothing
    to the group (and nothing to the records). Now every selected copper row
    leaves, each with its own `net_traces:` record, and the rest of the file is
    untouched."""
    caplog.set_level(logging.INFO)
    nodes = _copper_nodes(*COPPER) + [{"ref": "EXT", "kind": "external",
                                       "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, *COPPER)

    dock._delete_node_flow(tree, tree.nodes[0])   # the menu's own entry point

    assert [node.ref for node in tree.nodes] == ["EXT"], \
        "the three copper rows are gone, the ordinary one is not"
    assert _records_in(root) == [], "every orphaned record went with its node"
    assert dock._dirty is True
    assert "Deleted 3 nodes" in caplog.text
    for ref in COPPER:
        assert ref in caplog.text, "the Log names every deleted node"


def test_a_single_node_still_deletes_exactly_one(main_window, tmp_path):
    """N = 1 is the same path: one selected row, one node, one record."""
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, COPPER[0])

    dock._delete_node_flow(tree, tree.nodes[0])

    assert [node.ref for node in tree.nodes] == ["EXT"]
    assert _records_in(root) == []


# ── C2/C9: one removal point, no double deletion ───────────────────────────

def test_a_selected_child_is_not_deleted_twice(main_window, tmp_path, monkeypatch):
    """A selected parent AND its selected child: the child rides with the
    ancestor (the same `group_roots` rule the group "Move to…" uses), so exactly
    ONE row is removed — and it goes through `_detach_node`, the one removal
    point of every structural edit."""
    nodes = [{"ref": "PARENT", "kind": "external", "xy": [0.0, 0.0],
              "children": _copper_nodes(COPPER[0])}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, "PARENT", COPPER[0])

    calls = []
    original = dock._detach_node

    def _counting(tree_arg, node):
        calls.append(node.ref)
        return original(tree_arg, node)

    monkeypatch.setattr(dock, "_detach_node", _counting)

    dock._delete_node_flow(tree, tree.nodes[0])

    assert calls == ["PARENT"], "the child must not be removed a second time"
    assert tree.nodes == []
    assert _records_in(root) == [], \
        "the child's record goes with the subtree that carried it"


# ── C3: a record somebody else still references ────────────────────────────

def test_a_record_another_tree_references_stays(main_window, tmp_path, caplog):
    """The rule is "a record goes when NO node of ANY tree references it" — the
    same count `_on_delete_tree` makes. The loader forbids two nodes naming one
    record in ONE config, so a second tree is appended to the dock's forest: the
    case exists in memory (and would exist across a hand-edited graph), and the
    flow must not delete a record somebody still points at."""
    caplog.set_level(logging.INFO)
    dock, root = _dock_with(main_window, tmp_path, _copper_nodes(COPPER[0], COPPER[1]))
    tree = dock._current_tree()
    dock._trees.append(tree_from_dict({
        "name": "other_tree", "anchor": {"origin": True},
        "nodes": _copper_nodes(COPPER[1])}))
    _select(dock, COPPER[0], COPPER[1])

    dock._delete_node_flow(tree, tree.nodes[0])

    assert tree.nodes == []
    assert _records_in(root) == [COPPER[1]], \
        "the shared record stays; the one nobody else uses goes"
    assert COPPER[1] in caplog.text and "kept" in caplog.text, \
        "the Log says WHICH record was kept and why"


def test_referenced_identities_spans_every_tree():
    """The pure half of C3: every tree's copper nodes are asked, not just one."""
    from kicadstamp.trees import TreeNode

    first = tree_from_dict({"name": "a", "anchor": {"origin": True},
                            "nodes": _copper_nodes(COPPER[0])})
    second = tree_from_dict({"name": "b", "anchor": {"origin": True},
                             "nodes": _copper_nodes(COPPER[1])})
    assert referenced_identities([first, second]) == {COPPER[0], COPPER[1]}
    assert referenced_identities([]) == set()


# ── C4: the Delete key ─────────────────────────────────────────────────────

def test_the_delete_key_deletes_the_selection(main_window, tmp_path):
    """Delete with the tree focused is the same deletion the menu runs."""
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, COPPER[0])
    tree_widget = dock._current_tree_widget()
    tree_widget.setFocus()

    QTest.keyClick(tree_widget, Qt.Key.Key_Delete)

    assert [node.ref for node in tree.nodes] == ["EXT"]
    assert _records_in(root) == []


def test_the_delete_key_in_a_form_field_keeps_the_nodes(main_window, tmp_path):
    """The other half, and the reason the handler is a filter on the TREE widget:
    Delete inside a node form's field must delete a CHARACTER. A dock-wide
    `WidgetWithChildrenShortcut` would delete rows here instead."""
    nodes = [{"ref": "EXT", "kind": "external", "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, "EXT")
    form = trees_dock_mod.NodeFormWidget
    page = dock._active_form_page()
    embedded = dock._embedded_form_of(page)
    assert isinstance(embedded, form), "the panel must show the node's editor"
    embedded.name_edit.setText("abc")
    embedded.name_edit.setCursorPosition(3)

    QTest.keyClick(embedded.name_edit, Qt.Key.Key_Delete)

    assert [node.ref for node in tree.nodes] == ["EXT"], "the row must survive"
    assert embedded.name_edit.text() == "abc", \
        "with the caret at the end Delete changes nothing — the row was the point"
    assert _records_in(root) == []


# ── C5: a generated instance is read-only ──────────────────────────────────

def test_a_generated_instance_tree_deletes_nothing(main_window, tmp_path, monkeypatch,
                                                   caplog):
    """`tree_instances` trees stay READ-ONLY: their context menu has no actions,
    and the Delete KEY must refuse the same way — with a Log line, never a modal
    and never a silent no-op."""
    from types import SimpleNamespace

    caplog.set_level(logging.INFO)
    dock, root = _dock_with(main_window, tmp_path, _copper_nodes(COPPER[0]))
    tree = dock._current_tree()
    instance = SimpleNamespace(template="tmpl")
    monkeypatch.setattr(dock, "_instance_of",
                        lambda candidate: instance if candidate is tree else None)
    _select(dock, COPPER[0])

    assert run_delete_nodes(dock, tree, clicked=None) == 0

    assert [node.ref for node in tree.nodes] == [COPPER[0]]
    assert _records_in(root) == [COPPER[0]]
    assert "read-only" in caplog.text


# ── C6: the legacy copper container ────────────────────────────────────────

def test_a_copper_container_takes_its_children_and_their_records(
        main_window, tmp_path):
    """Deleting a `copper` container removes it AND the copper nodes hanging under
    it — so their records are orphaned by the very same deletion and must go too
    (Denis, 22.10: "контейнер уносит всех детей, и записи уходят вместе с ними").

    The container has NO ROW of its own (Т1.1 folds its children under the
    "Copper" pseudo-node and draws the container nowhere), so the cell hands the
    node to the flow directly — the rule under test is the RECORD half, which must
    hold however the container comes to be deleted."""
    nodes = [{"ref": "CU", "kind": "copper",
              "children": _copper_nodes(COPPER[0], COPPER[1])},
             {"ref": "EXT", "kind": "external", "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    assert "CU" not in dock._node_items, \
        "the container is not drawn — the reason this cell calls the flow directly"

    assert run_delete_nodes(dock, tree, clicked=tree.nodes[0]) == 1

    assert [node.ref for node in tree.nodes] == ["EXT"]
    assert _records_in(root) == [], "the children's records followed the subtree"


# ── C7: the pseudo-row in a mixed selection ────────────────────────────────

def _pseudo_item(dock):
    for item in [dock._current_tree_widget().topLevelItem(i)
                 for i in range(dock._current_tree_widget().topLevelItemCount())]:
        for child in [item.child(i) for i in range(item.childCount())]:
            if trees_dock_mod._is_copper_group_item(
                    child.data(0, Qt.ItemDataRole.UserRole)):
                return child
    return None


def test_the_pseudo_row_is_skipped_in_a_mixed_selection(main_window, tmp_path,
                                                        caplog):
    """The "Copper" pseudo-row is a FOLD, not a node: in a mixed selection it is
    silently skipped and the real rows are deleted (it must not abort the
    deletion, and it must not be reported as an error)."""
    caplog.set_level(logging.INFO)
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    pseudo = _pseudo_item(dock)
    assert pseudo is not None, "the fixture has copper, so the fold exists"
    dock._current_tree_widget().clearSelection()
    pseudo.setSelected(True)
    dock._node_items["EXT"].setSelected(True)

    dock._delete_node_flow(tree, tree.nodes[0])

    assert [node.ref for node in tree.nodes] == [COPPER[0]], \
        "only the real node went; the copper row and its record stay"
    assert _records_in(root) == [COPPER[0]]
    assert "Nothing to delete" not in caplog.text


def test_the_pseudo_row_alone_says_why_nothing_happened(main_window, tmp_path,
                                                        caplog):
    """A selection of the pseudo-row ALONE is the one case that gets a line: the
    key did nothing, and the user is told why (no dialog — rule 43)."""
    caplog.set_level(logging.INFO)
    dock, _root = _dock_with(main_window, tmp_path, _copper_nodes(COPPER[0]))
    pseudo = _pseudo_item(dock)
    assert pseudo is not None
    dock._current_tree_widget().clearSelection()
    pseudo.setSelected(True)

    QTest.keyClick(dock._current_tree_widget(), Qt.Key.Key_Delete)

    assert "Nothing to delete" in caplog.text
    assert [node.ref for node in dock._current_tree().nodes] == [COPPER[0]]


# ── C8: the label names what the item will do ──────────────────────────────

def test_the_delete_label_counts_the_selection(main_window, tmp_path):
    """The menu item deletes the WHOLE selection, so its label must say how many
    rows that is — one row keeps the familiar "Delete node"."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_nodes(*COPPER[:2]))
    tree = dock._current_tree()
    _select(dock, COPPER[0])
    assert delete_action_text(dock, tree, tree.nodes[0]) == "Delete node"
    _select(dock, COPPER[0], COPPER[1])
    assert delete_action_text(dock, tree, tree.nodes[0]) == "Delete 2 nodes"


def test_the_copper_menu_item_uses_the_counted_label(main_window, tmp_path,
                                                     monkeypatch):
    """The menu itself (not just the helper) carries the counted label — the
    wiring is what a regression would break."""
    dock, _root = _dock_with(main_window, tmp_path, _copper_nodes(*COPPER[:2]))
    tree = dock._current_tree()
    _select(dock, COPPER[0], COPPER[1])
    item = dock._node_items[COPPER[0]]
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

    assert "Delete 2 nodes" in captured, captured
    assert "Delete node" not in captured


# ── C10/C11: the record half, on the files it owns ─────────────────────────

def test_the_record_is_removed_from_the_file_that_owns_it(tmp_path):
    """`net_traces:` can live in an include: the removal must happen in the file
    that carries the record and leave the root alone (the same "write it back to
    its own file" rule `_stage_net_traces` follows)."""
    extra = tmp_path / "extra.sexp"
    write_later(extra, dict_to_sexp({"net_traces": [_record(COPPER[0]),
                                                    _record(COPPER[1])]},
                                    format_number=2))
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({"include": ["extra.sexp"]}, format_number=2))

    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(root)
    removed, not_found = remove_net_trace_records(root, [COPPER[0]])

    assert removed == [COPPER[0]]
    assert not_found == []
    assert [entry.get("name") for entry in read_data(extra)["net_traces"]] == [COPPER[1]]
    assert "net_traces" not in read_data(root), \
        "the root must not gain a section it never had"


def test_a_record_no_file_carries_is_reported_not_invented(tmp_path):
    """A record the files do not carry (a generated instance's copy, or a config
    edited underneath us) is REPORTED — never written, never guessed."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({"net_traces": [_record(COPPER[0])]},
                                   format_number=2))

    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(root)
    removed, not_found = remove_net_trace_records(root, [COPPER[1]])

    assert removed == []
    assert not_found == [COPPER[1]]
    assert [entry.get("name") for entry in read_data(root)["net_traces"]] == [COPPER[0]]


def test_without_a_root_config_nothing_is_written():
    """No root config (a dock without a file) = nothing to remove: the identities
    come back as `not_found` and no write is attempted."""
    assert remove_net_trace_records(None, ["x"]) == ([], ["x"])
    assert remove_net_trace_records(None, []) == ([], [])


# ── Д-1.1: the "no copper" mark of a deleted record ────────────────────────

def test_the_record_of_a_deleted_node_loses_its_no_copper_mark(main_window,
                                                               tmp_path):
    """Д-1.1 (acceptance of `18ade8f5`, Claude): `_stale_net_traces` is a mark about
    the RECORD a node names — "the last re-read did not find its copper on the
    board". The deletion takes the node AND (when orphaned) the record, so a mark
    naming that record must not survive it: a mark for a record nobody can see is
    a lie the next redraw would repeat."""
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, _root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    node = tree.nodes[0]
    dock._stale_net_traces.add(COPPER[0])
    assert dock._is_stale_net_trace(node) is True, \
        "the cell must start from a MARKED record"
    _select(dock, COPPER[0])

    dock._delete_node_flow(tree, node)

    assert COPPER[0] not in dock._stale_net_traces, \
        "the mark goes with the record it names"


# ── Д-1.2: which modifiers mean "delete" ───────────────────────────────────

@pytest.mark.parametrize("modifier", [
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.ShiftModifier,
    Qt.KeyboardModifier.AltModifier,
])
def test_a_modified_delete_does_not_delete_the_selection(main_window, tmp_path,
                                                         modifier):
    """Д-1.2: `DeleteKeyFilter`'s own promise — plain Delete (and the keypad's) is
    the deletion, Ctrl/Shift/Alt+Delete stay free for anything later. A filter that
    fired on ANY modifier would eat a future Ctrl+Delete binding before it could
    exist, and would contradict the docstring that describes it."""
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, COPPER[0])
    tree_widget = dock._current_tree_widget()
    tree_widget.setFocus()

    QTest.keyClick(tree_widget, Qt.Key.Key_Delete, modifier)

    assert [node.ref for node in tree.nodes] == [COPPER[0], "EXT"], \
        "a modified Delete must leave every row alone"
    assert _records_in(root) == [COPPER[0]]


def test_keypad_delete_deletes_the_selection(main_window, tmp_path):
    """Д-1.2 (other half): the keypad's own Delete is the SAME deletion — the filter
    allows `KeypadModifier` explicitly, and this cell is what says so."""
    nodes = _copper_nodes(COPPER[0]) + [{"ref": "EXT", "kind": "external",
                                         "xy": [0.0, 0.0]}]
    dock, root = _dock_with(main_window, tmp_path, nodes)
    tree = dock._current_tree()
    _select(dock, COPPER[0])
    tree_widget = dock._current_tree_widget()

    QTest.keyClick(tree_widget, Qt.Key.Key_Delete,
                   Qt.KeyboardModifier.KeypadModifier)

    assert [node.ref for node in tree.nodes] == ["EXT"]
    assert _records_in(root) == []
