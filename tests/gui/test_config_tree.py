# tests/gui/test_config_tree.py
"""Tests for ConfigTreeDock (gui/docks/config_tree.py) — one tree mirroring
the actual include: file graph from a single root file (2026-08-03, GUI
tree roadmap Этап 1/2, corrected same day from an earlier flat,
non-recursive version — see handoff_2026_08_03_gui_tree_risks_resolved.md
and the config-architecture-brainstorm memory)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import yaml
from PyQt6.QtWidgets import QMessageBox

import gui.docks.config_tree as config_tree_mod
from gui.docks.config_tree import ConfigTreeDock

MINIMAL_CELL = """
cells:
  one_role:
    components:
      - role: THE_ROLE
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
"""

# A root with EVERY recognized section present (one leaf each) — used by the
# context-menu filtering tests (2026-08-13, plan context_menu_by_section) so
# every category/leaf exists in the same tree.
ALL_SECTIONS_YAML = """
cells:
  my_cell:
    components: []
extract_profiles:
  my_profile:
    params: {}
clone_profiles:
  my_clone:
    params: {}
thermal_via_arrays:
  - name: my_tva
clone_placements:
  - name: my_placement
    cell: my_cell
coordinate_placements:
  - name: my_coord
    cluster: CHAN
    role: R
points:
  my_point:
    xy: [1.0, 2.0]
rules:
  - net: +3V3
"""


def _find(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def test_root_file_own_sections_shown_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.text(0) == "root.yaml"
    cells = _find(root_item, "Cells")
    assert cells.child(0).text(0) == "one_role"


def test_included_file_becomes_a_nested_file_node_not_merged_in(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 1  # nothing of its own, just sub.yaml
    sub_item = root_item.child(0)
    assert sub_item.text(0) == "sub.yaml"
    assert _find(sub_item, "Cells").child(0).text(0) == "one_role"


def test_nested_includes_recurse(main_window, tmp_path):
    (tmp_path / "c.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - c.yaml\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - b.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    b_item = dock.tree.topLevelItem(0).child(0)
    assert b_item.text(0) == "b.yaml"
    c_item = b_item.child(0)
    assert c_item.text(0) == "c.yaml"
    assert _find(c_item, "Cells").child(0).text(0) == "one_role"


def test_clicking_a_cell_leaf_fires_cell_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == ["one_role"]


def test_clicking_a_placement_leaf_fires_full_dict(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: ldo_adj\n    xy: [0, 0]\n",
        encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Clone placements").child(0)
    picked = []
    dock.placement_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == [{"name": "spoke_1", "cell": "ldo_adj", "xy": [0, 0]}]


def test_clicking_an_extract_profile_leaf_fires_profile_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("extract_profiles:\n  alpha:\n    params: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Extract profiles").child(0)
    picked = []
    dock.profile_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == ["alpha"]


def test_clicking_a_rules_leaf_fires_no_signal_no_form_yet(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_ref: U1\n    spokes: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Rules").child(0)
    assert leaf.text(0) == "+3V3"
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.placement_picked.connect(picked.append)
    dock.profile_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == []


def test_clicking_a_file_or_category_header_fires_no_signal(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.tree.itemClicked.emit(root_item, 0)  # file header
    dock.tree.itemClicked.emit(_find(root_item, "Cells"), 0)  # category header

    assert picked == []


def test_no_root_file_assigned_yields_an_empty_tree(main_window):
    dock = ConfigTreeDock(main_window)
    assert dock.tree.topLevelItemCount() == 0


def test_refresh_picks_up_a_change_made_on_disk(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0  # empty cells: section, nothing shown

    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock.refresh()

    root_item = dock.tree.topLevelItem(0)
    assert _find(root_item, "Cells").child(0).text(0) == "one_role"


def test_a_true_cycle_shows_as_a_single_error_item_not_a_crash(main_window, tmp_path):
    (tmp_path / "a.yaml").write_text("include:\n  - b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - a.yaml\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - a.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    assert dock.tree.topLevelItemCount() == 1
    assert "cycle detected" in dock.tree.topLevelItem(0).text(0)


# ── Context menu (2026-08-03) — file-level actions, same set regardless ──
# of whether the file header, a category, or a leaf was right-clicked. ────

def test_file_context_resolves_from_a_leaf_and_a_category(main_window, tmp_path):
    """_file_context_for_item must find the same file whether the click
    landed on the file header, a category under it, or a specific leaf —
    Denis: "Если выбран файл или его десцендант..." """
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    cells_category = _find(root_item, "Cells")
    leaf = cells_category.child(0)

    for item in (root_item, cells_category, leaf):
        file_path, parent_path = dock._file_context_for_item(item)
        assert file_path == root.resolve()
        assert parent_path is None  # root has no parent


def test_file_context_for_a_nested_included_file(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    sub_item = dock.tree.topLevelItem(0).child(0)
    file_path, parent_path = dock._file_context_for_item(sub_item)
    assert file_path == (tmp_path / "sub.yaml").resolve()
    assert parent_path == root.resolve()


def test_add_cell_emits_request_instead_of_writing_directly(main_window, tmp_path):
    """2026-08-06 — Add cell used to write a raw {"components": []} stub
    straight to YAML with no form behind it (the exact root cause of a live
    bug, see gui/docks/cell_editor.py's module docstring); now it defers to
    CellDock's own Save path, same shape as Add point/Add thermal via pad/
    Add placer above."""
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_cell_requested.connect(requested.append)
    dock.add_cell_requested.emit(root)

    assert requested == [root]
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"cells": {}}


def test_composite_cell_shows_nested_clone_placements_as_children(main_window, tmp_path):
    """2026-08-06 — the "tree" Denis actually meant once CellDock's own
    internal editor was built as tabs instead (see gui/docks/cell_editor.py):
    a composite cell's nested clone_placements: show as read-only child
    nodes under its own Cells leaf."""
    root = tmp_path / "root.yaml"
    root.write_text("""
cells:
  leaf:
    components: []
  composite:
    clone_placements:
      - name: inner_cell
        cell: leaf
      - name: inner_role
        role: SOME_ROLE
""", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    composite_leaf = _find(_find(dock.tree.topLevelItem(0), "Cells"), "composite")
    assert composite_leaf.childCount() == 2
    assert composite_leaf.child(0).text(0) == "inner_cell (cell:leaf)"
    assert composite_leaf.child(1).text(0) == "inner_role (role:SOME_ROLE)"

    leaf_leaf = _find(_find(dock.tree.topLevelItem(0), "Cells"), "leaf")
    assert leaf_leaf.childCount() == 0


def test_edit_cell_emits_name_and_file(main_window, tmp_path):
    """"Edit cell..." (context menu, 2026-08-06) — CellDock listens via
    load_entry(), see gui/dock_hub.py's _edit_cell."""
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.cell_edit_requested.connect(lambda name, path: requested.append((name, path)))
    dock.cell_edit_requested.emit("one_role", root)

    assert requested == [("one_role", root)]


def test_rename_action_present_for_a_leaf_absent_for_a_category(main_window, tmp_path):
    """2026-08-04, Denis: "А мы можем добавить конекстное меню в конфиг
    чтобы переименовать плэейсменты, целлы, профили извлечения и т.д.?" —
    Rename only makes sense on an actual entry, not a category header or
    the file node itself."""
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    cells_category = _find(dock.tree.topLevelItem(0), "Cells")
    leaf = cells_category.child(0)

    assert leaf.data(0, config_tree_mod.Qt.ItemDataRole.UserRole)[0] == "leaf"
    # Category headers were untagged (None) before 2026-08-13 — the context
    # menu now needs their section (plan context_menu_by_section), so they
    # carry ("category", section).
    assert cells_category.data(0, config_tree_mod.Qt.ItemDataRole.UserRole) == ("category", "cells")


def test_rename_cell_updates_the_dict_key_and_refreshes_the_tree(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_rename(root, "cells", "one_role")

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert "renamed_cell" in data["cells"]
    assert "one_role" not in data["cells"]
    assert _find(dock.tree.topLevelItem(0), "Cells").child(0).text(0) == "renamed_cell"


def test_rename_cell_cascades_a_reference_in_another_file(main_window, tmp_path, monkeypatch):
    (tmp_path / "cells.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "include:\n  - cells.yaml\n"
        "clone_placements:\n  - name: spoke_1\n    cell: one_role\n",
        encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda self_, title, text: shown.append(text)))

    dock._on_rename(tmp_path / "cells.yaml", "cells", "one_role")

    assert yaml.safe_load(root.read_text(encoding="utf-8"))["clone_placements"][0]["cell"] == \
        "renamed_cell"
    assert len(shown) == 1 and "root.yaml" in shown[0]  # summary names the other changed file


def test_rename_declined_leaves_the_file_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))

    dock._on_rename(root, "cells", "one_role")

    assert "one_role" in yaml.safe_load(root.read_text(encoding="utf-8"))["cells"]


def test_rename_collision_shows_a_warning_and_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells:\n  a: {}\n  b: {}\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("b", True)))
    warned = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(1)))

    dock._on_rename(root, "cells", "a")

    assert warned == [1]
    assert yaml.safe_load(root.read_text(encoding="utf-8"))["cells"] == {"a": {}, "b": {}}


def test_add_thermal_via_pad_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("thermal_via_arrays: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_thermal_via_requested.connect(requested.append)
    dock.add_thermal_via_requested.emit(root)

    assert requested == [root]
    # nothing written — Add thermal via pad defers to ThermalViaArrayDock's
    # own Save path (2026-08-03, same reasoning as Add placer)
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"thermal_via_arrays": []}


def test_thermal_via_leaf_click_emits_thermal_via_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "thermal_via_arrays:\n  - name: fpga_thermal\n    pad: '1'\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.thermal_via_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Thermal via arrays").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"name": "fpga_thermal", "pad": "1"}]


def test_coordinate_placement_empty_cluster_role_has_no_fallback_display_name():
    """2026-08-12, Group 2 fix: the display-name fallback checked key
    PRESENCE ("cluster" in e and "role" in e), so `cluster: null` rendered as
    a literal "None/ROLE". Non-empty values are required now — the same rule
    rename.py's entry_effective_name() enforces, which _entries now routes
    through (2026-08-13 review, bug 4: one formula, not an inline copy)."""
    entries = list(ConfigTreeDock._entries([
        {"name": "named"},
        {"cluster": None, "role": "ROLE"},
        {"cluster": "X", "role": None},
        {"cluster": "X", "role": "R1"},
    ], "coordinate_placements"))
    assert [name for name, _ in entries] == ["X/R1", "named"]


def test_coordinate_placements_leaf_click_emits_the_entry_dict(main_window, tmp_path):
    """2026-08-12, Group 1: coordinate_placements became a normal
    named-records section — a leaf click carries the FULL entry dict (like
    placement_picked), loaded into the merged PlacerDock's coordinate mode.
    This replaces the Group 2 shape where a leaf emitted the file path (and
    a path-less leaf could emit None, crashing load_from_file's
    None.exists() — the crash this new payload is immune to by design)."""
    root = tmp_path / "root.yaml"
    root.write_text(
        "coordinate_placements:\n  - cluster: X\n    role: R1\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.coordinate_placements_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Coordinate placements").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"cluster": "X", "role": "R1"}]


def test_add_point_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("points: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_point_requested.connect(requested.append)
    dock.add_point_requested.emit(root)

    assert requested == [root]
    # nothing written — Add point defers to PointsDock's own Save path,
    # same reasoning as Add thermal via pad/Add placer above.
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"points": {}}


def test_points_leaf_click_emits_points_picked_with_the_name(main_window, tmp_path):
    """points: is a DICT section (see _entries()) — unlike
    thermal_via_picked's full-dict payload above, the click only carries
    the name; PointsDock.load_entry() re-reads the file for the data."""
    root = tmp_path / "root.yaml"
    root.write_text("points:\n  origin:\n    xy: [0, 0]\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.points_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Points").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == ["origin"]


def test_add_rule_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("rules: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_rule_requested.connect(requested.append)
    dock.add_rule_requested.emit(root)

    assert requested == [root]
    # nothing written — Add rule defers to RuleDock's own Save path, same
    # reasoning as Add thermal via pad/Add placer/Add point above.
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"rules": []}


def test_rule_leaf_click_emits_rule_picked(main_window, tmp_path):
    """rules: is a LIST section (see _entries()) — like thermal_via_picked,
    the payload is already the full dict."""
    root = tmp_path / "root.yaml"
    root.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_role: FPGA\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.rule_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Rules").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"net": "+3V3", "anchor_role": "FPGA"}]


def test_add_placer_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("clone_placements: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_placer_requested.connect(requested.append)
    dock.add_placer_requested.emit(root)

    assert requested == [root]
    # nothing written — Add placer defers to PlacerDock's own Save path
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"clone_placements": []}


def test_add_included_file_creates_missing_file_and_wires_include(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")
    new_file = tmp_path / "power.yaml"
    assert not new_file.exists()

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_file), "")))
    dock._add_included_file(root)

    assert new_file.exists()
    assert yaml.safe_load(root.read_text(encoding="utf-8"))["include"] == ["power.yaml"]
    assert dock.tree.topLevelItem(0).child(0).text(0) == "power.yaml"


def test_add_included_file_rejects_a_file_with_root_only_keys(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("layer: B.Cu\ncells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(bad_file), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    dock._add_included_file(root)

    assert "include" not in yaml.safe_load(root.read_text(encoding="utf-8"))


def test_remove_file_disables_include_after_confirmation(main_window, tmp_path, monkeypatch):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    assert dock.tree.topLevelItem(0).childCount() == 1

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._remove_file(tmp_path / "sub.yaml", root)

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert data["include"] == [{"path": "sub.yaml", "enabled": False}]
    # walk_include_tree skips disabled includes -> sub.yaml no longer shown
    assert dock.tree.topLevelItem(0).childCount() == 0


def test_remove_file_declined_leaves_include_untouched(main_window, tmp_path, monkeypatch):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dock._remove_file(tmp_path / "sub.yaml", root)

    assert yaml.safe_load(root.read_text(encoding="utf-8"))["include"] == ["sub.yaml"]
    assert dock.tree.topLevelItem(0).childCount() == 1


def test_context_menu_has_no_remove_action_for_root(main_window, tmp_path, monkeypatch):
    """Root has no parent to remove itself from — the menu built for it
    must omit "Remove this file" entirely, not just disable it."""
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = {}
    original_add_action = config_tree_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        captured.setdefault("labels", []).append(text)
        return original_add_action(self, text, *a, **k)

    monkeypatch.setattr(config_tree_mod.QMenu, "addAction", _record)

    root_item = dock.tree.topLevelItem(0)
    dock._on_context_menu(dock.tree.visualItemRect(root_item).center())

    assert "Remove this file" not in captured["labels"]
    assert "Add cell..." in captured["labels"]


# ── "Add ..." filtered by section (2026-08-13, plan context_menu_by_section) ─

def _context_menu_labels(dock, item, monkeypatch):
    """Runs _on_context_menu for `item` and returns the added action labels —
    QMenu.exec is no-oped so the menu is never actually shown. Call it ONCE
    per test (or within one addAction-capture session): calling it twice in a
    row re-patches addAction and the second call's captured "original" is the
    first patch, cross-contaminating the two captured lists."""
    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction
    monkeypatch.setattr(config_tree_mod.QMenu, "addAction",
                        lambda self, text, *a, **k: (captured.append(text),
                                                     original_add_action(self, text, *a, **k))[1])
    dock._on_context_menu(dock.tree.visualItemRect(item).center())
    return captured


def _context_menu_actions(dock, item, monkeypatch):
    """Like _context_menu_labels but also captures the real QAction so a
    test can .trigger() it — the 2026-08-14 "Add ..." crash regression test
    (the label-only helpers never call .trigger(), so the lambda capture bug
    slipped through)."""
    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        action = original_add_action(self, text, *a, **k)
        captured.append((text, action))
        return action

    monkeypatch.setattr(config_tree_mod.QMenu, "addAction", _record)
    dock._on_context_menu(dock.tree.visualItemRect(item).center())
    return captured


def _add_labels(labels):
    """The context menu's "Add ..." block (incl. the unconditional "Add
    included file...") — Rename/Delete/Edit/Export never start with 'Add '."""
    return [label for label in labels if label.startswith("Add ")]


@pytest.mark.parametrize("category_label, expected_add", [
    ("Cells", "Add cell..."),
    ("Thermal via arrays", "Add thermal via pad..."),
    ("Coordinate placements", "Add coordinate placement..."),
    ("Clone placements", "Add placer..."),
    ("Points", "Add point..."),
    ("Rules", "Add rule..."),
])
def test_category_context_menu_shows_only_its_own_add_action(
        main_window, tmp_path, monkeypatch, category_label, expected_add):
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    category = _find(dock.tree.topLevelItem(0), category_label)
    labels = _context_menu_labels(dock, category, monkeypatch)

    assert _add_labels(labels) == [expected_add, "Add included file..."]


@pytest.mark.parametrize("category_label, expected_add", [
    ("Cells", "Add cell..."),
    ("Thermal via arrays", "Add thermal via pad..."),
    ("Coordinate placements", "Add coordinate placement..."),
    ("Clone placements", "Add placer..."),
    ("Points", "Add point..."),
    ("Rules", "Add rule..."),
])
def test_leaf_context_menu_shows_only_its_sections_add_action(
        main_window, tmp_path, monkeypatch, category_label, expected_add):
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), category_label).child(0)
    labels = _context_menu_labels(dock, leaf, monkeypatch)

    assert _add_labels(labels) == [expected_add, "Add included file..."]


def test_extract_profiles_category_and_leaf_show_add_extract_profile_only(
        main_window, tmp_path, monkeypatch):
    """The new action (2026-08-13): an Extract profiles category/leaf shows
    ONLY "Add extract profile..." from the Add block — the other five are
    section-specific and must not leak in. Both menus are built inside ONE
    addAction-capture session (re-patching between two calls would make the
    second capture's "original" the first patch — see _context_menu_labels'
    caller note)."""
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction
    monkeypatch.setattr(config_tree_mod.QMenu, "addAction",
                        lambda self, text, *a, **k: (captured.append(text),
                                                     original_add_action(self, text, *a, **k))[1])

    category = _find(dock.tree.topLevelItem(0), "Extract profiles")
    dock._on_context_menu(dock.tree.visualItemRect(category).center())
    dock._on_context_menu(dock.tree.visualItemRect(category.child(0)).center())

    add_labels = _add_labels(captured)
    assert add_labels.count("Add extract profile...") == 2  # one per menu
    assert add_labels.count("Add included file...") == 2
    assert len(add_labels) == 4
    assert "Add cell..." not in captured
    assert "Add rule..." not in captured


def test_clone_profiles_category_has_no_add_actions(main_window, tmp_path, monkeypatch):
    """clone_profiles is read-only (no GUI edit form, deliberate scope limit)
    — its category must show NO Add action at all, not even the wrong ones."""
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    category = _find(dock.tree.topLevelItem(0), "Clone profiles")
    labels = _context_menu_labels(dock, category, monkeypatch)

    assert _add_labels(labels) == ["Add included file..."]


def test_file_header_context_menu_shows_all_seven_add_actions(
        main_window, tmp_path, monkeypatch):
    """Denis's explicit decision: a file header (incl. the root) must still
    offer ALL the Add actions — otherwise a fresh file with no sections yet
    couldn't create its first entity."""
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    labels = _context_menu_labels(dock, dock.tree.topLevelItem(0), monkeypatch)

    for label in ("Add cell...", "Add thermal via pad...", "Add coordinate placement...",
                  "Add placer...", "Add point...", "Add rule...", "Add extract profile..."):
        assert label in labels


def test_nested_cell_child_context_menu_shows_all_add_actions(
        main_window, tmp_path, monkeypatch):
    """Read-only nested cell children carry no UserRole data at all — their
    section is unknown, so the Add block falls back to ALL actions (same as
    a file header)."""
    root = tmp_path / "root.yaml"
    root.write_text("""cells:
  composite:
    clone_placements:
      - name: inner_cell
        cell: leaf
""", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    composite = _find(_find(dock.tree.topLevelItem(0), "Cells"), "composite")
    nested = composite.child(0)
    labels = _context_menu_labels(dock, nested, monkeypatch)

    assert "Add cell..." in labels
    assert "Add rule..." in labels


def test_add_section_for_item_leaf_category_and_file_header(main_window, tmp_path):
    """The helper driving the filtering: leaf -> its section, category -> its
    section (tagged 2026-08-13), file header -> None (all Add actions)."""
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    root_item = dock.tree.topLevelItem(0)

    cells_category = _find(root_item, "Cells")
    assert dock._add_section_for_item(cells_category.child(0)) == "cells"
    assert dock._add_section_for_item(cells_category) == "cells"
    assert dock._add_section_for_item(_find(root_item, "Clone profiles")) == "clone_profiles"
    assert dock._add_section_for_item(root_item) is None  # file header


def test_add_action_trigger_emits_request_not_checked(main_window, tmp_path, monkeypatch):
    """2026-08-14 crash regression: QAction.triggered emits a positional
    `bool checked`, and PyQt fed it into the old `lambda sig=signal: ...`
    (overwriting the default) -> AttributeError: 'bool' object has no
    attribute 'emit' on every "Add ..." click. The lambda now leads with
    `checked=False`; triggering the real action must fire add_cell_requested
    with the resolved file path."""
    root = tmp_path / "root.yaml"
    root.write_text(ALL_SECTIONS_YAML, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_cell_requested.connect(requested.append)

    cells_category = _find(dock.tree.topLevelItem(0), "Cells")
    actions = dict(_context_menu_actions(dock, cells_category, monkeypatch))
    actions["Add cell..."].trigger()

    assert requested == [root.resolve()]


# ── Delete (2026-08-05) ───────────────────────────────────────────────────

def test_delete_action_present_for_a_leaf(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction
    monkeypatch.setattr(config_tree_mod.QMenu, "addAction",
                        lambda self, text, *a, **k: (captured.append(text),
                                                     original_add_action(self, text, *a, **k))[1])

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    dock._on_context_menu(dock.tree.visualItemRect(leaf).center())

    assert "Delete..." in captured


def test_delete_without_references_confirms_and_removes_with_backup(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(root, "cells", "one_role")

    assert yaml.safe_load(root.read_text(encoding="utf-8"))["cells"] == {}
    assert list(tmp_path.glob("root.yaml.bak.*"))
    assert dock.tree.topLevelItem(0).childCount() == 0  # empty Cells section, no leaf shown


def test_delete_declined_leaves_the_file_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    dock._on_delete(root, "cells", "one_role")

    assert "one_role" in yaml.safe_load(root.read_text(encoding="utf-8"))["cells"]
    assert not list(tmp_path.glob("root.yaml.bak.*"))


def test_delete_with_a_reference_asks_about_cascade_and_removes_both_on_yes(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(
        MINIMAL_CELL + "\nclone_placements:\n  - name: spoke_1\n    cell: one_role\n",
        encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda self_, title, text, *a, **k:
                                     (shown.append(text), QMessageBox.StandardButton.Yes)[1]))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(root, "cells", "one_role")

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert data["cells"] == {}
    assert data["clone_placements"] == []  # cascade removed the referencing spoke too
    assert len(shown) == 1 and "spoke_1" in shown[0]  # dialog listed the reference


def test_delete_with_a_reference_declined_cancels_the_whole_delete(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(
        MINIMAL_CELL + "\nclone_placements:\n  - name: spoke_1\n    cell: one_role\n",
        encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))

    dock._on_delete(root, "cells", "one_role")

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert "one_role" in data["cells"]  # nothing removed at all
    assert data["clone_placements"][0]["cell"] == "one_role"
    assert not list(tmp_path.glob("root.yaml.bak.*"))


# ── Export (2026-08-05) ──────────────────────────────────────────────────

def test_export_multi_select_enabled(main_window):
    from PyQt6.QtWidgets import QAbstractItemView
    dock = ConfigTreeDock(main_window)
    assert dock.tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection


def test_selected_export_items_ignores_file_and_category_headers(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    cells_category = _find(root_item, "Cells")
    leaf = cells_category.child(0)
    root_item.setSelected(True)
    cells_category.setSelected(True)
    leaf.setSelected(True)

    items = dock._selected_export_items()

    assert len(items) == 1
    assert items[0].section == "cells" and items[0].name == "one_role"


def test_export_action_label_switches_to_plural_for_multiple_leaves(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(
        "cells:\n  a: {}\n  b: {}\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    cells_category = _find(dock.tree.topLevelItem(0), "Cells")
    cells_category.child(0).setSelected(True)
    cells_category.child(1).setSelected(True)

    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction
    monkeypatch.setattr(config_tree_mod.QMenu, "addAction",
                        lambda self, text, *a, **k: (captured.append(text),
                                                     original_add_action(self, text, *a, **k))[1])

    dock._on_context_menu(dock.tree.visualItemRect(cells_category.child(0)).center())

    assert "Export selected..." in captured


def test_on_export_to_a_new_file_merges_without_prompting(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    target = tmp_path / "out.yaml"
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_export(dock._selected_export_items())

    assert "one_role" in yaml.safe_load(target.read_text(encoding="utf-8"))["cells"]
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == \
        yaml.safe_load(MINIMAL_CELL)  # the source is untouched — pure copy


def test_on_export_to_a_non_empty_file_merges_when_merge_is_chosen(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("cells:\n  existing: {}\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "exec", lambda self: None)
    # QMessageBox.buttons() is NOT insertion order — Qt reorders by platform
    # convention (confirmed live: Merge/Cancel/Overwrite, not Merge/
    # Overwrite/Cancel) — match by the button's own text instead of index.
    monkeypatch.setattr(config_tree_mod.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == "Merge"))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_export(dock._selected_export_items())

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert set(data["cells"].keys()) == {"existing", "one_role"}


def test_on_export_to_a_non_empty_file_overwrites_when_overwrite_is_chosen(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("cells:\n  existing: {}\ninclude:\n  - somewhere.yaml\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(config_tree_mod.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == "Overwrite"))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_export(dock._selected_export_items())

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert set(data["cells"].keys()) == {"one_role"}
    assert "include" not in data


def test_on_export_cancelled_dialog_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    target = tmp_path / "out.yaml"
    target.write_text("cells:\n  existing: {}\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(config_tree_mod.QMessageBox, "clickedButton",
                        lambda self: self.button(QMessageBox.StandardButton.Cancel))

    dock._on_export(dock._selected_export_items())

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["cells"] == {"existing": {}}  # untouched — Cancel aborted the export


def test_clone_placement_leaf_shows_placer_name_when_set(main_window, tmp_path):
    """2026-08-15, plan config_tree_rename_placer_name_aware: a clone_
    placements leaf whose entry carries placer_name shows THAT (the save/
    --only identity) in the tree — not the raw Cluster tag `name`. Entries
    without placer_name fall back to name as before."""
    root = tmp_path / "root.yaml"
    root.write_text(
        "clone_placements:\n"
        "  - name: PIF_AVDD\n    placer_name: CH0_PIF_AVDD\n    cell: ldo_adj\n    xy: [0, 0]\n"
        "  - name: plain\n    cell: ldo_adj\n    xy: [0, 0]\n",
        encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    category = _find(dock.tree.topLevelItem(0), "Clone placements")
    texts = [category.child(i).text(0) for i in range(category.childCount())]
    assert "CH0_PIF_AVDD" in texts
    assert "plain" in texts
