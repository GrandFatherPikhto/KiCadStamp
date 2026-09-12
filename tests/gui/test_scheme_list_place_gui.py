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
    _twin_sibling_sheet_names,
    collect_parent_candidates,
    placement_node_payload,
)
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.explore import Selected
from kicadstamp.link_trees import link_trees

_TOP_LEVEL_LABEL = "— top level (no parent) —"


# ── Shared config builders (format-agnostic .sexp fixtures) ───────────────

def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _scheme_record(name: str = "amp", source_sheet: str = "Channel_0") -> dict:
    """A minimal VALID scheme_lists: entry (no anchor — the record's frame is
    the region's centre, pivot defaults to it) — the same shape
    tests/test_scheme_list_place.py uses for the Stage-1 round-trip, good
    enough for load_config + the GUI cfg combos."""
    return {
        "name": name,
        "source_sheet": source_sheet,
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


# ── Section C2 — target-sheet combo: real-twin filter (2026-09-08) ─────────
#
# Two linked fixes. (1) The live Board's own sheet_names is ALWAYS {}
# (Board.connect() never passes schematic_dir, gui/connection.py), so a raw
# snapshot's Selected.sheet is a list of None — the combo re-resolves it
# against the CONFIG's sheet_names (self._ctx) first
# (snapshot_with_resolved_sheets, the same fix Record/Re-source already apply,
# plan_2026_09_08_scheme_list_place_target_sheet_unresolved.md). (2) THIS fix:
# the candidates are then filtered to REAL twin top-level sheets only — a
# top-level sheet must be a member of a 2+ inner_key group (path[1:] identical
# across 2+ channel instances), the same rule as
# scheme_list_apply._twin_sheet_uuids / channel_copy._channel_sheet_uuids.
# Single-instance sheets (FPGA/Power/MCU) and bare sub-sheets (DAC/OpAmp)
# never qualify — the combo can no longer offer a sheet Apply would reject as
# "not a twin on the board" (Denis' repro, plan_2026_09_08_scheme_list_place_
# target_sheet_twin_filter.md). Both run over the CACHED snapshot, never a
# fresh adapter call (that would race the background kipy poll — Commit H).

class _SheetFakeFp:
    """Raw-footprint stand-in exposing the sheet-UUID chain
    resolve_sheet_path_names reads (fp.sheet_path_uuids — see
    kicadstamp/sheet_names.py:171)."""

    def __init__(self, uuids):
        self.sheet_path_uuids = uuids


def _sheet_selected(ref, uuids):
    """A raw-board Selected whose .sheet is ALL None (the GUI Board's own
    resolution is always empty — Board.connect() never passes schematic_dir)
    but whose .fp keeps the real sheet UUID chain, so
    snapshot_with_resolved_sheets can rebuild the segment names."""
    return Selected(ref=ref, role=None, cluster=None,
                    sheet=[None] * (len(uuids) - 1), nets={},
                    fp=_SheetFakeFp(uuids))


def _resolved_selected(ref, names, uuids):
    """A Selected whose .sheet is ALREADY resolved to `names` (what
    snapshot_with_resolved_sheets produces) and whose .fp keeps the real
    sheet-UUID chain `uuids`. For the pure _twin_sibling_sheet_names tests,
    names/uuids must line up like a real footprint: sheet_path_uuids =
    [top_uuid, ...leaf_uuid, fp_instance_uuid] and .sheet = the resolved names
    of uuids[:-1], so .sheet[0] is the resolved name of uuids[0] (the
    top-level sheet)."""
    return Selected(ref=ref, role=None, cluster=None,
                    sheet=list(names), nets={},
                    fp=_SheetFakeFp(tuple(uuids)))


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_twin_sibling_sheet_names_returns_only_real_twin_top_levels():
    """Pure _twin_sibling_sheet_names unit test: a top-level sheet qualifies as
    a target ONLY when it is a member of a 2+ inner_key group (two channel
    instances sharing the same path[1:] chain). Single-instance sheets (FPGA)
    never qualify, and bare sub-sheet segments (DAC, here at .sheet[1]) are
    never returned — the result is top-level twin names, sorted."""
    snapshot = [
        # Channel_1 / Channel_2 twin pair: U1 and U2 live on the shared DAC
        # sub-sheet of each channel — identical inner chain (path[1:]),
        # different path[0] (the channel uuid).
        _resolved_selected("U1", ["Channel_1", "DAC"], ("ch1", "dac", "u1")),
        _resolved_selected("U2", ["Channel_1", "DAC"], ("ch1", "dac", "u2")),
        _resolved_selected("U1", ["Channel_2", "DAC"], ("ch2", "dac", "u1")),
        _resolved_selected("U2", ["Channel_2", "DAC"], ("ch2", "dac", "u2")),
        # A single-instance top-level sheet — on the board but NOT a twin.
        _resolved_selected("U9", ["FPGA"], ("fpga", "u9")),
    ]
    assert _twin_sibling_sheet_names(snapshot) == ["Channel_1", "Channel_2"]


def test_twin_sibling_sheet_names_excludes_single_members_and_shallow_rows():
    """Every footprint needs a usable hierarchy chain (len >= 2) AND its
    top-level sheet must end up in a 2+ member group — a lone row, a root-sheet
    (chain length 1) footprint and a row with no .fp all yield nothing."""
    snapshot = [
        _resolved_selected("U1", ["Power"], ("power", "u1")),   # 1 member
        _resolved_selected("U0", [], ("u0",)),                  # no hierarchy
        SimpleNamespace(ref="U2", sheet=(), fp=None),           # no fp at all
    ]
    assert _twin_sibling_sheet_names(snapshot) == []


def test_sheet_combo_offers_resolved_sibling_twins(main_window, tmp_path):
    """Regression 2026-09-08: a snapshot resolved through the config's
    sheet_names must make the Target sheet combo offer the live twin siblings
    Channel_1/Channel_2 — not just the record's source_sheet. A sibling
    qualifies only as a REAL twin (2+ members per inner_key), so the synthetic
    snapshot builds full twin pairs, the way the live board looks."""
    root = tmp_path / "root.sexp"
    _write_project(root)  # scheme "amp", source_sheet "Channel_0"
    dock = _make_place_dock(main_window, root)
    # Select the record first so the combo knows its source_sheet (Channel_0)
    # — a fresh dock opens with no Scheme List chosen yet.
    dock.scheme_list_combo.setCurrentText("amp")
    # This test config has no schematic_dir, so ctx.sheet_names is empty —
    # replace the ctx with one carrying the map a real project would have
    # resolved (_rebuild_sheet_combo reads ONLY _ctx.sheet_names).
    dock._ctx = SimpleNamespace(sheet_names={"ch1": "Channel_1",
                                             "ch2": "Channel_2"})
    # Channel_1 / Channel_2 twin pair (each channel's U1/U2 share the inner
    # chain with the other channel — 2+ members per inner_key).
    dock._connection.snapshot = [
        _sheet_selected("U1", ("ch1", "dac", "u1")),
        _sheet_selected("U1", ("ch2", "dac", "u1")),
        _sheet_selected("U2", ("ch1", "dac", "u2")),
        _sheet_selected("U2", ("ch2", "dac", "u2")),
    ]
    dock._on_scheme_list_changed()  # rebuild the sheet combo from the live set

    items = _combo_items(dock.sheet_combo)
    # "Channel_0" is the record's own source_sheet — always there. The FIX is
    # that the resolved twin siblings Channel_1/Channel_2 are now offered too
    # (and only them — no DAC/OpAmp sub-sheet noise).
    assert items == ["", "Channel_0", "Channel_1", "Channel_2"]


def test_repro_sheet_combo_offers_only_real_twin_top_levels(main_window, tmp_path):
    """Denis' repro (plan_2026_09_08_scheme_list_place_target_sheet_twin_
    filter.md §0/§6): on the `channel` record the Target sheet combo must show
    ROUNDLY its source_sheet plus the REAL twin top-level channels — NO
    FPGA/Power/MCU (single instances, never clones), NO bare DAC/OpAmp
    sub-sheets, and NO bare duplicate of the record's own source channel
    (Channel_0 is itself a real twin on the live board, but selecting it would
    be the same "in place" as the source_sheet option — _collect_payload would
    even write it as entity.sheet, i.e. an onto-sibling place against the
    source channel)."""
    root = tmp_path / "root.sexp"
    _write(root, _project_dict(schemes=("channel",)))
    # The channel record was captured from Channel_0/DAC + Channel_0/OpAmp, so
    # its source_sheet is the FULL path of the first recorded sheet.
    data = _load(root)
    data["scheme_lists"][0]["source_sheet"] = "Channel_0/DAC"
    _write(root, data)
    dock = _make_place_dock(main_window, root)
    dock.scheme_list_combo.setCurrentText("channel")
    dock._ctx = SimpleNamespace(sheet_names={
        "ch0": "Channel_0", "ch1": "Channel_1", "ch2": "Channel_2",
        "fpga": "FPGA", "power": "Power", "mcu": "MCU",
        "dac": "DAC", "opamp": "OpAmp"})
    # Channel_0/1/2 are three instances of the same channel design (the
    # record's own Channel_0 included); FPGA/Power/MCU are single instances.
    dock._connection.snapshot = [
        _sheet_selected("U1", ("ch0", "dac", "u1")),
        _sheet_selected("U1", ("ch1", "dac", "u1")),
        _sheet_selected("U1", ("ch2", "dac", "u1")),
        _sheet_selected("U2", ("ch0", "opamp", "u2")),
        _sheet_selected("U2", ("ch1", "opamp", "u2")),
        _sheet_selected("U2", ("ch2", "opamp", "u2")),
        _sheet_selected("U9", ("fpga", "u9")),
        _sheet_selected("U10", ("power", "u10")),
        _sheet_selected("U11", ("mcu", "u11")),
    ]
    dock._on_scheme_list_changed()

    assert _combo_items(dock.sheet_combo) == [
        "", "Channel_0/DAC", "Channel_1", "Channel_2"]


def test_sheet_combo_raw_all_none_snapshot_offers_only_source(main_window, tmp_path):
    """Raw board snapshot with all-None .sheet AND an empty config sheet_names
    (no schematic_dir — nothing CAN be resolved) = exactly the pre-fix reality:
    the combo must stay [""] + [source_sheet], never a crash or garbage from a
    half-resolved snapshot (old behaviour preserved when sheet_names is empty)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    dock = _make_place_dock(main_window, root)
    dock.scheme_list_combo.setCurrentText("amp")  # source_sheet becomes Channel_0
    dock._connection.snapshot = [_sheet_selected("U1", ("ch1", "u1"))]
    dock._on_scheme_list_changed()

    # ctx.sheet_names is empty for this schematic-less config -> resolution is
    # a no-op -> the all-None .sheet contributes no twin names.
    assert _combo_items(dock.sheet_combo) == ["", "Channel_0"]


def test_rebuild_sheet_combo_is_safe_before_first_refresh(main_window, tmp_path):
    """_rebuild_sheet_combo() must not require _ctx to be loaded (it is None
    before the first set_root_path/refresh) nor a snapshot attribute on the
    connection (a bare fake has none) — the getattr-guards fall back to an
    empty sheet_names map / empty snapshot and the combo degrades to [""]
    instead of crashing (the old _live_sheets() safety contract, now carried
    by the rebuilt combo path)."""
    dock = SchemeListPlaceFormWidget(main_window)  # no set_root_path -> _ctx None
    assert dock._ctx is None
    assert getattr(dock._connection, "snapshot", None) is None  # fake, no attr

    dock._on_scheme_list_changed()  # must not crash

    assert _combo_items(dock.sheet_combo) == [""]


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
    assert hub.config_tree_dock.right_page_at(idx) is hub.scheme_list_place_dock


def test_dock_hub_scheme_list_place_requested_opens_page_and_presets(hub, tmp_path):
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    record = _scheme_record("amp")
    hub.config_tree_dock.scheme_list_place_requested.emit(record, root)

    assert (hub.config_tree_dock.current_right_page()
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
    assert (hub.config_tree_dock.current_right_page()
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

    assert (hub.config_tree_dock.current_right_page()
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


# ── Section F — Re-source... wiring (plan_2026_09_06_scheme_list_sheet_
#    capture.md 5b.4): the ConfigTreeDock context-menu "Re-source..." action,
#    the DockHub signal -> delegate -> shared-flow path, and the Tools menu
#    delegate (needs a selected record — a missing selection warns, never a
#    blank dialog).

def test_config_tree_scheme_list_context_menu_offers_re_source_next_to_place(
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
    assert "Re-source..." in labels


def test_config_tree_scheme_list_re_source_action_emits_signal_with_payload(
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
    rs_action = next(action for label, action in actions if label == "Re-source...")

    captured = []
    dock.scheme_list_resource_requested.connect(
        lambda payload, file_path: captured.append((payload, file_path)))
    rs_action.trigger()

    assert len(captured) == 1
    payload, file_path = captured[0]
    assert payload["name"] == "amp"
    assert Path(file_path) == root


def test_dock_hub_scheme_list_resource_requested_reaches_shared_flow(
        hub, tmp_path, monkeypatch):
    """The _wire() connection: scheme_list_resource_requested (context menu)
    -> resource_scheme_list_record -> the shared _run_resource_scheme_list flow
    with the right-clicked record + its OWNING file."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    calls = []
    # `trigger` (Э2, plan_2026_09_12_busy_indicator): the Tools-menu leg passes
    # the QAction, this context-menu leg passes None by design.
    monkeypatch.setattr(hub, "_run_resource_scheme_list",
                        lambda entry, file_path, trigger=None:
                        calls.append((entry, file_path)))

    record = _scheme_record("amp")
    hub.config_tree_dock.scheme_list_resource_requested.emit(record, root)

    assert len(calls) == 1
    entry, file_path = calls[0]
    assert entry["name"] == "amp"
    assert Path(file_path) == root


def test_dock_hub_resource_scheme_list_without_selection_warns(hub, tmp_path, monkeypatch):
    """Tools delegate with no record selected in the Config tree -> a warning,
    NOT a blank Re-source flow (mirror of place_scheme_list's no-selection
    guard — Re-source is meaningless without a record to re-point)."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)
    hub.config_tree_dock.tree.clearSelection()

    opened = []
    monkeypatch.setattr(hub, "_run_resource_scheme_list",
                        lambda entry, file_path, trigger=None:
                        opened.append(entry))

    hub.resource_scheme_list()

    assert opened == []


def test_dock_hub_resource_scheme_list_with_selection_reaches_shared_flow(
        hub, tmp_path, monkeypatch):
    """Tools delegate with a record selected in the Config tree passes that
    record + its owning file to the shared Re-source flow."""
    root = tmp_path / "root.sexp"
    _write_project(root)
    _set_hub_root(hub, root)

    root_item = hub.config_tree_dock.tree.topLevelItem(0)
    section = _find(root_item, "Scheme lists")
    leaf = _find(section, "amp")
    leaf.setSelected(True)

    calls = []
    triggers = []
    monkeypatch.setattr(hub, "_run_resource_scheme_list",
                        lambda entry, file_path, trigger=None:
                        (calls.append((entry, file_path)),
                         triggers.append(trigger)))

    hub.resource_scheme_list()

    assert len(calls) == 1
    entry, file_path = calls[0]
    assert entry["name"] == "amp"
    assert Path(file_path) == root
    # Э2 (plan_2026_09_12_busy_indicator): this leg looks the Tools-menu QAction
    # up on the window to use as the guard widget. This fixture's window is a
    # bare QMainWindow stub with no Actions at all, so the lookup yields None and
    # the guard list is simply empty — never an AttributeError. (The real
    # window's Action is asserted in test_trees_dock_internode_reread's
    # delegate-wiring test, which builds the real MainWindow.)
    assert triggers == [None]
