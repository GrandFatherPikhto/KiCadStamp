# tests/gui/test_placer_entity_mode.py
"""
PlacerDock Entity source mode (2026-08-30, Entity/Placement split, phase
5.2 redesign): the "Entity" Source pick edits an Entity record — name +
cell + electrical/identity fields, NO position (that lives only in the
trees: node; config/entries.py fatals on any positional key by design).

Stage 1 = the Entity record itself (pick + save, no position). Stage 2 =
the Origin tab now edits the Entity's trees: node (its PLACEMENT): picking
a placed Entity loads the node's position, saving writes/updates it, a
blank origin leaves the Entity "не размещено".

Headless like the rest of test_placer_dock.py: these tests exercise the
combo population from the include graph, the pick-into-form load, the
no-position _build_entity_dict and the _do_save_entity validate-then-upsert
path against real config files on disk — never touching a live board.
"""
from pathlib import Path

from gui.docks.placer import PlacerDock
from kicadstamp.config import load_entity
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _make_entity_dock(main_window, tmp_path, entities=None, cells=None, trees=None):
    """Root config with an included cells file + an entities: section (and an
    optional trees: section for the stage-2 placement-node tests) — the same
    include-graph shape the real project uses (an Entity lives wherever its
    record does, possibly in an included file)."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": cells or {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                             "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [],
            "tracks": [],
            "layer": "F.Cu",
        }
    }})
    root_data = {
        "clone_placements": [],
        "include": ["cells.sexp"],
        "entities": entities or [
            {"name": "E1", "cell": "pi_filter", "cluster": "CL1",
             "nets": {"C_IN": "+3V3"}, "refs": {"C_IN": "C5"}},
            {"name": "E2", "cell": "pi_filter"},
        ],
    }
    if trees is not None:
        root_data["trees"] = trees
    root_file = tmp_path / "root.sexp"
    _write(root_file, root_data)
    dock = PlacerDock(main_window)
    dock.set_root_path(root_file)
    return dock, root_file


def _switch_to_entity(dock) -> None:
    dock.cell_mode_combo.setCurrentIndex(2)


def _contains_node(tree_dict, ref) -> bool:
    """True if `tree_dict` holds a node with ref == ref (recursive)."""
    for n in tree_dict.get("nodes") or []:
        if n.get("ref") == ref:
            return True
        if _contains_node(n, ref):
            return True
    return False


def _find_node_in_trees(trees, ref):
    """The node dict with ref == ref across every tree, or None."""
    for tree in trees:
        def walk(nodes):
            for n in nodes or []:
                if n.get("ref") == ref:
                    return n
                hit = walk(n.get("children") or [])
                if hit:
                    return hit
            return None

        hit = walk(tree.get("nodes"))
        if hit:
            return hit
    return None


# ── Source mode toggle ────────────────────────────────────────────────────

def test_entity_is_the_third_source_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    assert dock.cell_mode_combo.count() == 3
    assert not dock.is_entity
    assert not dock.is_coordinate
    _switch_to_entity(dock)
    assert dock.is_entity
    # Entity row replaces the Cell/name rows; the Coordinate tab does not.
    # Phase 5.2 stage 3: Nets/Net overrides/Refs moved to the Tools dock; the
    # Placer's own override tabs are gone entirely (2026-09-05) — only the
    # Origin tab (the trees: node) and the Coordinate tab (coordinate mode)
    # remain.
    assert dock._entity_row.isHidden() is False
    assert dock._cell_row.isHidden() is True
    assert dock._name_row.isHidden() is True
    assert dock._coordinate_identity_row.isHidden() is True
    assert dock._tabs.isTabVisible(dock._origin_tab_index)
    assert not dock._tabs.isTabVisible(dock._coordinate_tab_index)


def test_cell_mode_hides_the_entity_row(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    assert dock._entity_row.isHidden() is False
    dock.cell_mode_combo.setCurrentIndex(0)  # back to Cell
    assert dock._entity_row.isHidden() is True
    assert dock._cell_row.isHidden() is False


# ── Entity combo population ───────────────────────────────────────────────

def test_entity_choices_populated_from_the_include_graph(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    items = [dock.entity_combo.itemText(i) for i in range(dock.entity_combo.count())]
    assert items == ["E1", "E2"]


def test_broken_root_leaves_entity_combo_empty(main_window, tmp_path):
    root_file = tmp_path / "root.sexp"
    root_file.write_text("(this is (not valid (sexp", encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(root_file)  # must not raise (2026-08-28 hardening)
    assert dock.entity_combo.count() == 0


# ── Picking an Entity loads its fields ────────────────────────────────────

def test_picking_entity_loads_its_fields_into_the_form(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    assert dock._selected_cell == "pi_filter"
    assert dock.placer_name_edit.text() == "E1"
    assert dock.cluster_edit.currentText() == "CL1"
    # The entity's stored override fields are remembered for carry-forward on
    # save (the Placer no longer has GUI to edit them — 2026-09-05).
    assert dock._loaded_override_fields["nets"] == {"C_IN": "+3V3"}
    assert dock._loaded_override_fields["refs"] == {"C_IN": "C5"}
    assert dock._loaded_entity_identity == "E1"


def test_entity_pick_unknown_name_is_ignored(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock._on_entity_picked("NOPE")  # must not raise, must not claim an identity
    assert dock._selected_cell is None
    assert dock._loaded_entity_identity is None


# ── Save payload: an Entity, never a position ─────────────────────────────

def test_entity_build_payload_has_no_position_fields(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    entry = dock._build_entry_dict()
    assert entry == {
        "name": "E1", "cell": "pi_filter", "cluster": "CL1",
        "nets": {"C_IN": "+3V3"}, "refs": {"C_IN": "C5"},
    }
    for forbidden in ("xy", "anchor_ref", "anchor_role", "anchor_point",
                      "rotation_deg", "radius_mm", "angle_deg"):
        assert forbidden not in entry
    entity = load_entity(entry)  # the real backend validator accepts it
    assert entity.name == "E1"
    assert entity.cell == "pi_filter"


def test_entity_save_requires_a_name(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    assert dock._build_entry_dict() is None


def test_entity_save_writes_entities_section_and_replaces_in_place(main_window, tmp_path):
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    dock.cluster_edit.setCurrentText("CL1_NEW")
    dock._do_save()
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    assert by_name["E1"]["cluster"] == "CL1_NEW"
    assert set(by_name) == {"E1", "E2"}  # E2 untouched, no duplicate E1


def test_entity_save_rejects_a_positional_key(main_window, tmp_path):
    """_build_entity_dict never emits a position, but the save path must
    still refuse one if it ever arrives (load_entity fatals) — the same
    no-position guarantee config/entries.py enforces. Nothing is written."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    before = _load(root_file)
    dock._do_save_entity({"name": "E1", "cell": "pi_filter", "xy": [1.0, 2.0]})
    assert _load(root_file) == before


def test_entity_save_rename_deletes_old_record_defensively(main_window, tmp_path):
    """Saving under a name different from the loaded identity must remove
    the old record (mirrors the clone path's rename, 2026-08-15)."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")  # loads E1, records the identity
    assert dock._loaded_entity_identity == "E1"
    dock._do_save_entity({"name": "E3", "cell": "pi_filter", "cluster": "CL1"})
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    assert set(by_name) == {"E2", "E3"}
    assert by_name["E3"]["cluster"] == "CL1"


# ── Window-title identity ─────────────────────────────────────────────────

def test_current_entity_name_reflects_entity_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E2")
    assert dock.current_entity_name == "E2"


def test_current_entity_name_falls_back_to_clone_name_outside_entity_mode(main_window, tmp_path):
    dock, _ = _make_entity_dock(main_window, tmp_path)
    assert dock.current_entity_name == ""  # blank clone Cluster in Cell mode


# ── Stage 2: Origin tab = the Entity's trees: node ─────────────────────────

def test_entity_pick_loads_placed_node_origin(main_window, tmp_path):
    """A placed Entity (its trees: node exists) loads the node's position
    into the Origin tab — Origin edits the tree node, not an Entity field."""
    dock, _ = _make_entity_dock(main_window, tmp_path, trees=[
        {"name": "flat", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [5.0, 2.0]}]},
    ])
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    assert dock.origin_widget.mode == "xy"
    assert float(dock.x_edit.text()) == 5.0
    assert float(dock.y_edit.text()) == 2.0
    assert "Placed" in dock._placement_status_label.text()


def test_entity_pick_unplaced_clears_origin(main_window, tmp_path):
    """An Entity with no trees: node is legally "не размещено" — the Origin
    position widgets are cleared and the status label says so."""
    dock, _ = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E2")  # no node anywhere
    assert dock.origin_widget.mode == "xy"
    assert dock.x_edit.text() == ""
    assert dock.y_edit.text() == ""
    assert "Not placed" in dock._placement_status_label.text()


def test_entity_save_writes_placement_node(main_window, tmp_path):
    """Setting an origin and saving writes the Entity's trees: node (kind
    placement) under a matching (origin)-anchored tree."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E2")  # unplaced
    dock.x_edit.setText("10")
    dock.y_edit.setText("20")
    dock._do_save()
    data = _load(root_file)
    trees = data.get("trees") or []
    node = _find_node_in_trees(trees, "E2")
    assert node is not None
    assert node["kind"] == "placement"
    assert node["xy"] == [10.0, 20.0]
    flat = next(t for t in trees if _contains_node(t, "E2"))
    assert flat["anchor"] == {"origin": True}


def test_entity_save_blank_origin_skips_node_write(main_window, tmp_path):
    """An Entity saved with a blank Origin tab stays unplaced — no node is
    written (deleting a node to unplace is TreesDock's job)."""
    dock, root_file = _make_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E2")
    dock._do_save()
    data = _load(root_file)
    assert _find_node_in_trees(data.get("trees") or [], "E2") is None


def test_entity_save_updates_existing_node_position(main_window, tmp_path):
    dock, root_file = _make_entity_dock(main_window, tmp_path, trees=[
        {"name": "flat", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [5.0, 2.0]}]},
    ])
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")  # loads 5.0, 2.0
    dock.x_edit.setText("7")
    dock.y_edit.setText("8")
    dock.rotation_edit.setText("90")
    dock._do_save()
    data = _load(root_file)
    trees = data.get("trees") or []
    assert len(trees) == 1  # still one tree, not a duplicate
    node = _find_node_in_trees(trees, "E1")
    assert node["xy"] == [7.0, 8.0]
    assert node["rotation"] == 90.0


def test_entity_save_moves_node_to_matching_anchor_tree(main_window, tmp_path):
    """Changing the Origin's anchor MOVES the node to the tree whose anchor
    matches — the position source stays exactly one (link_trees invariant)."""
    dock, root_file = _make_entity_dock(main_window, tmp_path, trees=[
        {"name": "flat", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [5.0, 2.0]}]},
    ])
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")
    dock.origin_widget.origin_mode_combo.setCurrentIndex(
        dock.origin_widget._modes.index("point"))
    dock.point_edit.setCurrentText("P1")
    dock._do_save()
    data = _load(root_file)
    trees = data.get("trees") or []
    point_tree = next(t for t in trees if t.get("anchor") == {"point": "P1"})
    assert _contains_node(point_tree, "E1")
    flat = next(t for t in trees if t.get("anchor") == {"origin": True})
    assert not _contains_node(flat, "E1")  # moved, not duplicated


def test_entity_save_clears_fields_removed_in_the_form(main_window, tmp_path):
    """2026-08-30 review fix: the merge must preserve from disk ONLY the
    fields ToolsDock owns (nets/net_overrides/refs) — a field the user
    CLEARED in the form (absent from the payload, since _build_entity_dict
    omits falsy optionals) must be cleared on disk, not resurrected from the
    old record by a full-dict merge."""
    dock, root_file = _make_entity_dock(main_window, tmp_path, entities=[
        # mirror: True requires a layer (loader cross-check) or the config
        # won't LOAD at all and the entity picker stays empty.
        {"name": "E1", "cell": "pi_filter", "cluster": "CH0",
         "comment": "old note", "mirror": True, "layer": "B.Cu",
         "nets": {"C_IN": "+3V3"}},
    ])
    _switch_to_entity(dock)
    dock.entity_combo.setCurrentText("E1")  # loads cluster/comment/mirror/nets
    dock.cluster_edit.setCurrentText("")
    dock.placer_comment_edit.setText("")
    dock.mirror_checkbox.setChecked(False)
    dock._do_save()
    data = _load(root_file)
    by_name = {e["name"]: e for e in data["entities"]}
    e1 = by_name["E1"]
    assert "cluster" not in e1  # cleared, not resurrected
    assert "comment" not in e1  # cleared, not resurrected
    assert "mirror" not in e1   # cleared, not resurrected
    assert e1["nets"] == {"C_IN": "+3V3"}  # Tools-owned field preserved


# ── P6 Stage 4: a scheme_list-based Entity (cell=None) in the Source combo ──

def _make_scheme_entity_dock(main_window, tmp_path):
    """An Entity-mode placer root that ALSO carries a scheme_lists: record and
    a scheme_list-based Entity (S1, cell=None) beside the cell-based E1 — the
    Stage-4 .cell audit case: a cell=None Entity sharing the Source combo with
    cell-based ones. The root must LOAD (scheme_list refs resolve), so the
    record is included."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file, {"cells": {
        "pi_filter": {"components": [{"role": "C_IN", "offset_along_mm": 0,
                                       "offset_across_mm": 0, "angle_deg": 0,
                                       "net_template": "{PWR_IN}"}],
                       "vias": [], "tracks": [], "layer": "F.Cu"}}})
    root_file = tmp_path / "root.sexp"
    _write(root_file, {
        "clone_placements": [],
        "include": ["cells.sexp"],
        "scheme_lists": [{
            "name": "psu", "anchor_ref": "R1", "source_sheet": "Channel_0",
            "anchor_rotation_deg": 0.0,
            "components": [{"ref": "R1", "offset_along_mm": 0.0,
                            "offset_across_mm": 0.0, "rotation_deg": 0.0}],
        }],
        "entities": [
            {"name": "E1", "cell": "pi_filter", "cluster": "CL1"},
            {"name": "S1", "scheme_list": "psu", "sheet": "Channel_1"},
        ],
    })
    dock = PlacerDock(main_window)
    dock.set_root_path(root_file)
    return dock, root_file


def test_scheme_list_entity_pick_is_readonly_and_save_refused(main_window, tmp_path, caplog):
    """P6 Stage 4 .cell audit — a scheme_list-based Entity (cell=None) is in
    the Source combo but Placer's cell-based Entity form cannot represent it:
    picking it loads read-only (no crash, no cell), and a Save is refused with
    a clear message — the record is NEVER silently rewritten into a cell-less
    Entity (which load_entity would reject) or dropped."""
    dock, root_file = _make_scheme_entity_dock(main_window, tmp_path)
    _switch_to_entity(dock)
    names = [dock.entity_combo.itemText(i) for i in range(dock.entity_combo.count())]
    assert names == ["E1", "S1"]
    dock.entity_combo.setCurrentText("S1")

    # Read-only load: the scheme_list identity is remembered, no cell picked.
    assert dock._selected_cell is None
    assert dock._loaded_entity_scheme_list == "psu"
    assert dock._loaded_entity_identity == "S1"
    assert dock.placer_name_edit.text() == "S1"
    assert dock.sheet_edit.currentText() == "Channel_1"

    # Save is refused (no payload) with the scheme_list-specific message —
    # the root config stays byte-identical (no rewrite, no drop).
    before = _load(root_file)
    assert dock._build_entry_dict() is None
    dock._do_save()
    assert _load(root_file) == before
    assert "Scheme List placement" in caplog.text

    # Switching back to a cell-based Entity clears the scheme_list identity.
    dock.entity_combo.setCurrentText("E1")
    assert dock._selected_cell == "pi_filter"
    assert dock._loaded_entity_scheme_list is None
