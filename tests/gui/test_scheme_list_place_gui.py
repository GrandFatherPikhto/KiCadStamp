# tests/gui/test_scheme_list_place.py
"""P6 "Place Scheme List" GUI tests (plan_2026_09_05_scheme_list.md §6,
plan_2026_09_06_scheme_list_p6_tests.md — Stages 2-3, headless Qt):
  * Section A — the pure helpers (collect_parent_candidates,
    placement_node_payload) behind the parent_combo and the placement write;
  * Section B — SchemeListPlaceFormWidget construction + validate(): empty vs
    valid form, duplicate Entity name, non-numeric rotation, free-typed
    unknown Scheme List/tree (the combos are editable+searchable);
  * Section C — _do_place(): the synchronous write path (no dialogs), driven
    like SchemeListFormWidget._do_reread is in test_scheme_list.py: top-level
    vs child node placement, rotation materialization, the Entity's
    scheme_list/sheet semantics ("in place" vs twin target), the link_trees
    round-trip and the caught-error contract (returns {"error": ...}).
  * Section D — DockHub wiring (page registered, context-menu request opens +
    presets it, Tools-menu place_scheme_list() with/without a tree selection,
    saved -> config_tree.refresh + trees_dock.reload_trees + graph_changed);
  * Section E — the ConfigTreeDock context-menu "Place..." action on a
    scheme_lists leaf.

Only reference-material read, never edited: tests/gui/test_scheme_list.py,
tests/gui/test_config_tree.py, tests/test_scheme_list_place.py (Stage 1).
"""
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import gui.docks.config_tree as config_tree_mod
import gui.docks.scheme_list_place as slp_mod
from gui.docks.config_tree import ConfigTreeDock
from gui.docks.scheme_list_place import (
    SchemeListPlaceFormWidget,
    collect_parent_candidates,
    placement_node_payload,
)
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.link_trees import link_trees

_TOP_LEVEL_LABEL = "— top level (no parent) —"


# ── Shared config builders (format-agnostic .sexp fixtures) ───────────────

def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _scheme_record(name: str = "amp", source_sheet: str = "Channel_0") -> dict:
    """A minimal VALID scheme_lists: entry (anchor_ref among its own
    components) — the same shape tests/test_scheme_list_place.py uses for the
    Stage-1 round-trip, good enough for load_config + the GUI cfg combos."""
    return {
        "name": name,
        "anchor_ref": "R1",
        "source_sheet": source_sheet,
        "anchor_rotation_deg": 0.0,
        "components": [
            {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
            {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
        ],
    }


def _project_dict(*, schemes=("amp",), extra_entities=(), extra_trees=()) -> dict:
    """A root config that loads cleanly and gives the Place page something to
    work with: scheme_lists: (the records to place), an entities:/trees: pair
    with a PARENT node so both a top-level AND a child placement resolve."""
    data = {"scheme_lists": [_scheme_record(name) for name in schemes]}
    data["entities"] = [{"name": "PARENT", "cell": "c_parent"}, *extra_entities]
    data["trees"] = [{
        "name": "main", "anchor": {"origin": True},
        "nodes": [{"ref": "PARENT", "kind": "placement", "xy": [0.0, 0.0]}],
    }, *extra_trees]
    return data


def _write_project(root: Path, **kw) -> None:
    _write(root, _project_dict(**kw))


def _find(item, text):
    """Direct child of `item` whose column-0 label == text (raises if absent)."""
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def _tree_dict(root: Path, tree_name: str = "main") -> dict:
    return next(t for t in _load(root)["trees"] if t["name"] == tree_name)


def _find_node(tree: dict, ref: str):
    """(parent_list, node_dict) where node_dict has ref == ref — tree["nodes"]
    for a top-level node, else the owning node's "children". (None, None) when
    absent (mirror of tests/test_scheme_list_place.py's helper)."""
    def walk(nodes):
        for n in nodes or []:
            if n.get("ref") == ref:
                return nodes, n
            hit = walk(n.get("children") or [])
            if hit[1] is not None:
                return hit
        return None, None
    return walk(tree.get("nodes"))


def _make_place_dock(main_window, root: Path):
    """A SchemeListPlaceFormWidget pointed at `root` (set_root_path triggers
    refresh -> load_config -> cfg combos populated)."""
    dock = SchemeListPlaceFormWidget(main_window)
    dock.set_root_path(root)
    return dock


def _fill_form(dock, *, scheme="amp", tree="main", parent_ref=None,
               name="NEWENT", rotation="45.0", x=1.5, y=2.5):
    """Drive the form into a valid Place state. parent_ref None == the
    top-level sentinel (currentData() is None)."""
    dock.scheme_list_combo.setCurrentText(scheme)
    dock.tree_combo.setCurrentText(tree)
    if parent_ref is None:
        dock.parent_combo.setCurrentIndex(0)
    else:
        idx = dock.parent_combo.findData(parent_ref)
        assert idx >= 0, f"parent {parent_ref!r} not among parent candidates"
        dock.parent_combo.setCurrentIndex(idx)
    dock.name_edit.setText(name)
    dock.rotation_edit.setText(rotation)
    dock.x_spin.setValue(x)
    dock.y_spin.setValue(y)


# ── Section A — pure helpers ──────────────────────────────────────────────

class _Node:
    """Minimal TreeNode stand-in for collect_parent_candidates (it only reads
    .ref/.name/.children)."""
    def __init__(self, ref, name=None, children=()):
        self.ref = ref
        self.name = name
        self.children = list(children)


class _Tree:
    def __init__(self, nodes=()):
        self.nodes = list(nodes)


def test_collect_parent_candidates_empty_tree_is_only_the_top_level_sentinel():
    assert collect_parent_candidates(None) == [(None, _TOP_LEVEL_LABEL)]
    assert collect_parent_candidates(_Tree()) == [(None, _TOP_LEVEL_LABEL)]


def test_collect_parent_candidates_dfs_parent_before_child_indented():
    tree = _Tree([
        _Node("P1", children=[_Node("C1", name="child-one"),
                              _Node("C2", name="C2")]),  # name == ref -> no parens
        _Node("P2", name="power"),
    ])
    assert collect_parent_candidates(tree) == [
        (None, _TOP_LEVEL_LABEL),
        ("P1", "P1"),
        # depth 1 -> two leading spaces; name shown only when != ref
        ("C1", "  C1 (child-one)"),
        ("C2", "  C2"),
        ("P2", "P2 (power)"),
    ]


def test_placement_node_payload_writes_rotation_even_zero():
    """decision 5 — rotation is written AT CREATION; a 0.0 must stay explicit
    in the raw payload (the sexp serializer strips the default later, that is
    not this helper's concern)."""
    node = placement_node_payload("E1", 1.0, 2.0, 0.0)
    assert node == {"ref": "E1", "kind": "placement", "xy": [1.0, 2.0],
                    "rotation": 0.0}
    assert "rotation" in node  # present, not dropped


def test_placement_node_payload_xy_and_nonzero_rotation():
    node = placement_node_payload("E2", 3.5, -4.25, 90.0)
    assert node["ref"] == "E2"
    assert node["kind"] == "placement"
    assert node["xy"] == [3.5, -4.25]
    assert node["rotation"] == 90.0


# ── Section B — SchemeListPlaceFormWidget construction + validate() ───────

def test_validate_empty_form_reports_all_missing_selections(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)

    problems = dock.validate()
    texts = " ".join(problems)
    assert "Select a Scheme List to place." in texts
    assert "Pick a tree to place into." in texts
    assert "Entity name is required." in texts


def test_validate_valid_form_is_empty(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, scheme="amp", tree="main", name="NEWENT", rotation="")

    assert dock.validate() == []


def test_validate_reports_existing_entity_name(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, scheme="amp", tree="main", name="PARENT", rotation="")

    problems = dock.validate()
    assert any("already exists" in p for p in problems)
    assert any("PARENT" in p for p in problems)


def test_validate_reports_non_numeric_rotation(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, scheme="amp", tree="main", name="NEWENT", rotation="abc")

    assert "Rotation must be a number." in dock.validate()


def test_validate_reports_unknown_free_typed_scheme_list(main_window, tmp_path):
    """The Scheme List combo is editable+searchable — a free-typed value that
    names no record must be rejected (a nonexistent reference would be fatal
    at the next load)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, scheme="bogus", tree="main", name="NEWENT", rotation="")

    assert any("Unknown Scheme List 'bogus'." in p for p in dock.validate())


def test_validate_reports_unknown_free_typed_tree(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, scheme="amp", tree="nope", name="NEWENT", rotation="")

    assert any("Unknown tree 'nope'." in p for p in dock.validate())


# ── Section C — _do_place() (synchronous write path) ──────────────────────

def test_do_place_top_level_appends_to_tree_nodes(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref=None, name="TOP", rotation="0", x=3.0, y=4.0)

    result = dock._do_place()
    assert "error" not in result
    assert result.get("ok") is True

    tree = _tree_dict(root)
    nodes, node = _find_node(tree, "TOP")
    # Top level -> the node sits DIRECTLY in tree["nodes"], never in someone's
    # children (decision 1 — "Нам не нужно новое дерево").
    assert nodes is tree["nodes"]
    assert node["xy"] == [3.0, 4.0]
    # 0.0 is the serializer default, so it is omitted on the round-trip (the
    # helper test above pins that the PAYLOAD still carries the explicit 0.0).
    assert node.get("rotation", 0.0) == 0.0


def test_do_place_child_appends_under_parent_children(main_window, tmp_path):
    """The KEY placement decision (design §6.2): the new node lands in the
    chosen EXISTING node's children, NOT in tree["nodes"] top level."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref="PARENT", name="CHILD", rotation="0",
               x=5.0, y=6.0)

    result = dock._do_place()
    assert "error" not in result

    tree = _tree_dict(root)
    nodes, node = _find_node(tree, "CHILD")
    assert nodes is not tree["nodes"]
    parent = next(n for n in tree["nodes"] if n["ref"] == "PARENT")
    assert node in parent["children"]
    assert node["xy"] == [5.0, 6.0]


def test_do_place_materializes_nonzero_rotation(main_window, tmp_path):
    """decision 5 — a rotation typed into the form is written ONTO the node at
    creation; a 45.0 must survive the round-trip (not 0, not absent)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref=None, name="ROT45", rotation="45.0")

    result = dock._do_place()
    assert "error" not in result

    _, node = _find_node(_tree_dict(root), "ROT45")
    assert node["rotation"] == 45.0


def test_do_place_entity_scheme_list_in_place_when_sheet_blank(main_window, tmp_path):
    """Blank target sheet = the "in place" mode: Entity carries scheme_list
    only (no sheet, cell stays None — never a copy of the geometry)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref="PARENT", name="E_A", rotation="")

    result = dock._do_place()
    assert "error" not in result

    cfg, _ = load_config(str(root))
    ent = next(e for e in cfg.entities if e.name == "E_A")
    assert ent.scheme_list == "amp"
    assert ent.cell is None
    assert ent.sheet is None


def test_do_place_entity_sheet_equal_to_source_stays_in_place(main_window, tmp_path):
    """design §5.2 p2 — picking the record's OWN source_sheet is still "in
    place"; only a genuinely different sheet is written as the twin target."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref="PARENT", name="E_SAME", rotation="")
    # The record's source_sheet is a real combo candidate -> select it.
    dock.sheet_combo.setCurrentText("Channel_0")

    result = dock._do_place()
    assert "error" not in result

    cfg, _ = load_config(str(root))
    ent = next(e for e in cfg.entities if e.name == "E_SAME")
    assert ent.sheet is None


def test_do_place_entity_other_sheet_is_written_as_twin_target(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    # A live sheet instance (Channel_1) makes the sheet combo offer a target
    # genuinely different from the record's source_sheet.
    dock._connection.snapshot = [SimpleNamespace(sheet=("Channel_1",))]
    dock._on_scheme_list_changed()  # rebuild sheet combo from the live set
    _fill_form(dock, parent_ref="PARENT", name="E_OTHER", rotation="")
    dock.sheet_combo.setCurrentText("Channel_1")

    result = dock._do_place()
    assert "error" not in result

    cfg, _ = load_config(str(root))
    ent = next(e for e in cfg.entities if e.name == "E_OTHER")
    assert ent.sheet == "Channel_1"
    assert ent.scheme_list == "amp"


def test_do_place_round_trip_link_trees_resolves_new_entity(main_window, tmp_path):
    """The GUI-path mirror of TestSchemeListEntityRoundTrip (Stage 1): after a
    real _do_place() the new node resolves to the NEW Entity, never cell=None."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref="PARENT", name="NEWENT", rotation="90.0",
               x=5.0, y=6.0)

    result = dock._do_place()
    assert "error" not in result

    cfg, _ = load_config(str(root))
    ent = next(e for e in cfg.entities if e.name == "NEWENT")
    assert ent.scheme_list == "amp"
    assert ent.cell is None

    def _all_ln(lnodes):
        for ln in lnodes:
            yield ln
            yield from _all_ln(ln.children)

    linked = link_trees(cfg, cfg.trees)[0]
    ln = next(ln for ln in _all_ln(linked.nodes) if ln.node.ref == "NEWENT")
    assert ln.record is not None
    assert ln.record.name == "NEWENT"


def test_do_place_missing_tree_returns_error_not_raise(main_window, tmp_path, monkeypatch):
    """_do_place must surface a missing owning tree as {"error": ...}, not let
    the failure escape — the find_list_entry_file None leg (scheme_list_place
    resolves the tree's owning file itself)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref=None, name="NEWENT", rotation="")

    monkeypatch.setattr(slp_mod, "find_list_entry_file", lambda *a, **k: None)
    result = dock._do_place()

    assert "error" in result
    assert "not found in the config graph" in result["error"]


def test_do_place_append_os_error_is_caught_into_error_dict(main_window, tmp_path, monkeypatch):
    """config_writer.append_tree_child_node's OSError (missing tree/parent,
    write failure) must be caught and reported, never crash the caller
    (scheme_list_place.py:588)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    _fill_form(dock, parent_ref="PARENT", name="NEWENT", rotation="")

    def _boom(*a, **k):
        raise OSError("simulated append failure")

    monkeypatch.setattr(slp_mod, "append_tree_child_node", _boom)
    result = dock._do_place()

    assert "error" in result
    assert "simulated append failure" in result["error"]


# ── Section D — DockHub wiring ────────────────────────────────────────────

@pytest.fixture
def hub(main_window):
    """A DockHub on the bare QMainWindow stub with a fake connection, cleaned
    up exactly like tests/gui/test_scheme_list.py:403's try/finally: detach the
    Log dock's root-logger handler and close the root log_file FileHandler."""
    from gui.dock_hub import DockHub

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    yield hub
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def _set_hub_root(hub, root: Path) -> None:
    """Route a project root through RootMetadataDock — the same root_changed
    broadcast DockHub._wire uses, so every dock (incl. the Place page) gets
    set_root_path/set_root_file."""
    hub.root_metadata_dock.set_root_file(root)


def test_dock_hub_registers_scheme_list_place_page(hub):
    idx = hub._scheme_list_place_page
    assert hub.config_tree_dock.right_stack.widget(idx) is hub.scheme_list_place_dock


def test_dock_hub_scheme_list_place_requested_opens_page_and_presets(hub, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    record = _scheme_record("amp")
    hub.config_tree_dock.scheme_list_place_requested.emit(record, root)

    assert (hub.config_tree_dock.right_stack.currentWidget()
            is hub.scheme_list_place_dock)
    assert hub.scheme_list_place_dock.scheme_list_combo.currentText() == "amp"


def test_dock_hub_place_scheme_list_with_no_selection_presets_nothing(hub, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)
    hub.config_tree_dock.tree.clearSelection()

    presets = []
    monkeypatch.setattr(hub.scheme_list_place_dock, "preset_scheme_list",
                        lambda name: presets.append(name))

    hub.place_scheme_list()

    # The page opens, but preset_scheme_list is NEVER called blindly.
    assert presets == []
    assert (hub.config_tree_dock.right_stack.currentWidget()
            is hub.scheme_list_place_dock)


def test_dock_hub_place_scheme_list_with_selection_presets_record(hub, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    # Emulate the real selection: the Config tree's scheme_lists leaf is the
    # item selected_scheme_list() scans for.
    root_item = hub.config_tree_dock.tree.topLevelItem(0)
    section = _find(root_item, "Scheme lists")
    leaf = _find(section, "amp")
    leaf.setSelected(True)

    hub.place_scheme_list()

    assert (hub.config_tree_dock.right_stack.currentWidget()
            is hub.scheme_list_place_dock)
    assert hub.scheme_list_place_dock.scheme_list_combo.currentText() == "amp"


def test_dock_hub_place_saved_refreshes_tree_reloads_trees_and_emits_graph_changed(
        hub, tmp_path, monkeypatch):
    """saved -> config_tree_dock.refresh + trees_dock.reload_trees +
    config_tree_dock.graph_changed (see dock_hub._wire). Asserted on REAL
    widget state — a PyQt signal connection captures the bound method at
    connect() time, so patching the instance afterwards would not intercept
    (the test_phase3_wiring.py caveat)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    # A config-tree/trees-dock change made on disk that ONLY a refresh /
    # reload_trees would pick up: a second tree appended by an external writer.
    extra_trees = [{"name": "extra", "anchor": {"origin": True}, "nodes": []}]
    _write(root, _project_dict(extra_trees=extra_trees))

    # The graph_changed broadcast fans out to every graph-derived combo dock;
    # spy them so the assertion below targets the three direct connections.
    targets = {
        "chain_dock": hub.chain_dock, "placer_dock": hub.placer_dock,
        "thermal_via_dock": hub.thermal_via_dock, "cells_dock": hub.cells_dock,
        "tools_dock": hub.tools_dock, "entity_dock": hub.entity_dock,
        "points_dock": hub.points_dock,
    }
    for _name, dock in targets.items():
        monkeypatch.setattr(dock, "set_root_path", lambda path: None)
    monkeypatch.setattr(hub.trees_dock, "refresh_ref_candidates",
                        lambda: None)
    monkeypatch.setattr(hub.root_metadata_dock, "refresh_working_file_choices",
                        lambda: None)

    graph_changed = []
    hub.config_tree_dock.graph_changed.connect(lambda: graph_changed.append(True))

    hub.scheme_list_place_dock.saved.emit()

    # config_tree_dock.refresh ran -> the config tree now shows the new tree.
    root_item = hub.config_tree_dock.tree.topLevelItem(0)
    trees_section = _find(root_item, "Trees")
    _find(trees_section, "extra")
    # trees_dock.reload_trees ran -> the Trees dock re-read the trees: section.
    assert "extra" in [t.name for t in hub.trees_dock._trees]
    # graph_changed was emitted.
    assert graph_changed == [True]


# ── Section E — ConfigTreeDock context-menu "Place..." action ─────────────

def _context_menu_actions(dock, item, monkeypatch):
    """Mirror of tests/gui/test_config_tree.py's helper — no-ops QMenu.exec
    and captures every (label, real QAction) so a test can .trigger()."""
    monkeypatch.setattr(config_tree_mod.QMenu, "exec",
                        lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        action = original_add_action(self, text, *a, **k)
        captured.append((text, action))
        return action

    monkeypatch.setattr(config_tree_mod.QMenu, "addAction", _record)
    dock._on_context_menu(dock.tree.visualItemRect(item).center())
    return captured


def test_config_tree_scheme_list_context_menu_offers_place_next_to_reread(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [_scheme_record("amp")]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    section = _find(root_item, "Scheme lists")
    leaf = _find(section, "amp")

    labels = [label for label, _action in _context_menu_actions(dock, leaf, monkeypatch)]
    assert "Reread..." in labels
    assert "Place..." in labels


def test_config_tree_scheme_list_place_action_emits_signal_with_payload(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    record = _scheme_record("amp")
    _write(root, {"scheme_lists": [record]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    section = _find(root_item, "Scheme lists")
    leaf = _find(section, "amp")

    actions = _context_menu_actions(dock, leaf, monkeypatch)
    place_action = next(action for label, action in actions if label == "Place...")

    captured = []
    dock.scheme_list_place_requested.connect(
        lambda payload, file_path: captured.append((payload, file_path)))
    place_action.trigger()

    assert len(captured) == 1
    payload, file_path = captured[0]
    assert payload["name"] == "amp"
    assert Path(file_path) == root
