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
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

import gui.docks.config_tree as config_tree_mod
from gui.docks.config_tree import ConfigTreeDock
from gui import settings
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

# The component offsets are all 0.0 — the s-expr default, so they round-trip
# OMITTED (dict_to_sexp drops default-valued fields). Keeping them out of the
# constant makes _load(root) == MINIMAL_CELL exact (2026-08-28, .sexp migration).
MINIMAL_CELL = {
    "cells": {
        "one_role": {
            "components": [{"role": "THE_ROLE"}],
        },
    },
}

# A root with EVERY recognized section present (one leaf each) — used by the
# context-menu filtering tests (2026-08-13, plan context_menu_by_section) so
# every category/leaf exists in the same tree.
ALL_SECTIONS = {
    "cells": {"my_cell": {}},
    "extract_profiles": {"my_profile": {"params": {}}},
    "clone_profiles": {"my_clone": {"params": {}}},
    "thermal_via_arrays": [{"name": "my_tva"}],
    "clone_placements": [{"name": "my_placement", "cell": "my_cell"}],
    "coordinate_placements": [{"name": "my_coord", "cluster": "CHAN", "role": "R"}],
    "points": {"my_point": {"xy": [1.0, 2.0]}},
    "chains": [{"net": "+3V3"}],
}


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _find(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def test_root_file_own_sections_shown_directly(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.text(0) == "root.sexp"
    cells = _find(root_item, "Cells")
    assert cells.child(0).text(0) == "one_role"


def test_master_detail_splitter_holds_tree_and_right_stack(main_window):
    """S-A (plan config_qview_placer_nettrace): ConfigTreeDock is now a
    master-detail — QSplitter(tree left, context QStack right), with an empty
    placeholder page 0 and no right pages until DockHub adds them."""
    dock = ConfigTreeDock(main_window)
    assert dock.splitter.widget(0) is dock.tree
    assert dock.splitter.widget(1) is dock.right_stack
    assert dock.right_stack.count() == 1            # just the placeholder
    assert dock.current_right_page_index() == 0
    # No selection -> placeholder page stays current.
    dock.set_current_page(-1)
    assert dock.current_right_page_index() == 0


def test_add_right_page_and_routing(main_window):
    """S-A: DockHub appends Placer/NetTrace as right pages; set_current_page
    routes by tree selection (page index), and a category/file selection
    (index < 1) falls back to the placeholder."""
    dock = ConfigTreeDock(main_window)
    placer = QWidget()
    net_trace = QWidget()
    placer_index = dock.add_right_page(placer)
    net_index = dock.add_right_page(net_trace)
    assert placer_index == 1
    assert net_index == 2
    assert dock.right_page_count() == 3

    dock.set_current_page(placer_index)
    assert dock.right_stack.currentWidget() is placer
    dock.set_current_page(net_index)
    assert dock.right_stack.currentWidget() is net_trace
    # Category/file click -> placeholder.
    dock.set_current_page(0)
    assert dock.right_stack.currentWidget() is dock.right_stack.widget(0)
    dock.set_current_page(-5)
    assert dock.current_right_page_index() == 0


def test_show_page_raises_the_dock(main_window):
    """S-A: show_page switches the page AND makes the dock visible (raise-on-
    switch, the DetailDock.show_X convention it replaces)."""
    dock = ConfigTreeDock(main_window)
    page = QWidget()
    idx = dock.add_right_page(page)
    dock.show_page(idx)
    assert dock.right_stack.currentWidget() is page
    # Visible flag requested (the dock has no shown parent in a unit test, so
    # isVisible() stays False — but it must not be marked hidden).
    assert not dock.isHidden()


def test_splitter_sizes_persist_on_quit_flush(main_window):
    """Splitter position persistence (2026-09-05): persist_ui_state flushes
    the tree | right-QView divider even with no project open, so the user's
    chosen split survives the next launch."""
    dock = ConfigTreeDock(main_window)
    dock.add_right_page(QWidget())
    assert dock.splitter.count() == 2
    dock.splitter.resize(1200, 500)
    dock.splitter.setSizes([300, 900])
    saved = list(dock.splitter.sizes())

    dock.persist_ui_state()  # _root_path is None -> only the splitter is flushed

    assert settings.state.get("config_splitter_sizes") == saved


def test_restore_splitter_sizes_reapplies_the_saved_split(main_window):
    """A fresh dock starts at its default split, then restore_splitter_sizes()
    puts the handle back where the previous run left it (a 'restart')."""
    dock = ConfigTreeDock(main_window)
    dock.add_right_page(QWidget())
    dock.splitter.resize(1200, 500)
    dock.splitter.setSizes([300, 900])
    saved = list(dock.splitter.sizes())
    settings.state.set("config_splitter_sizes", saved)

    fresh = ConfigTreeDock(main_window)
    fresh.splitter.resize(1200, 500)
    fresh.splitter.setSizes([900, 300])  # a different split
    fresh.restore_splitter_sizes()

    restored = list(fresh.splitter.sizes())
    assert abs(restored[0] - saved[0]) <= 2


def test_restore_splitter_sizes_ignores_invalid_saved_values(main_window, caplog):
    """A missing / wrong-arity / non-numeric persisted value is ignored — the
    default split stays and startup never crashes (the same fatal-safe rule as
    the dock-layout blob)."""
    dock = ConfigTreeDock(main_window)
    dock.add_right_page(QWidget())
    dock.splitter.resize(1200, 500)
    dock.splitter.setSizes([700, 500])
    before = list(dock.splitter.sizes())

    dock.restore_splitter_sizes()  # key absent
    assert list(dock.splitter.sizes()) == before

    settings.state.set("config_splitter_sizes", "nope")
    dock.restore_splitter_sizes()  # not a list
    assert list(dock.splitter.sizes()) == before

    settings.state.set("config_splitter_sizes", [10, 20, 30])
    dock.restore_splitter_sizes()  # wrong arity
    assert list(dock.splitter.sizes()) == before

    settings.state.set("config_splitter_sizes", [10, "x"])
    dock.restore_splitter_sizes()  # non-numeric
    assert list(dock.splitter.sizes()) == before


def test_included_file_becomes_a_nested_file_node_not_merged_in(main_window, tmp_path):
    _write(tmp_path / "sub.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 1  # nothing of its own, just sub.sexp
    sub_item = root_item.child(0)
    assert sub_item.text(0) == "sub.sexp"
    assert _find(sub_item, "Cells").child(0).text(0) == "one_role"


def test_nested_includes_recurse(main_window, tmp_path):
    _write(tmp_path / "c.sexp", MINIMAL_CELL)
    _write(tmp_path / "b.sexp", {"include": ["c.sexp"]})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["b.sexp"]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    b_item = dock.tree.topLevelItem(0).child(0)
    assert b_item.text(0) == "b.sexp"
    c_item = b_item.child(0)
    assert c_item.text(0) == "c.sexp"
    assert _find(c_item, "Cells").child(0).text(0) == "one_role"


def test_clicking_a_cell_leaf_fires_cell_picked(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == ["one_role"]


def test_clicking_a_placement_leaf_fires_full_dict(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"clone_placements": [{"name": "spoke_1", "cell": "ldo_adj", "xy": [0, 0]}]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Clone placements").child(0)
    picked = []
    dock.placement_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == [{"name": "spoke_1", "cell": "ldo_adj", "xy": [0, 0]}]


def test_clicking_a_chain_node_fires_no_signal_no_form_yet(main_window, tmp_path):
    """2026-09-01, plan rules_to_chains: a chains: node (anchor/chain/pad)
    is edited via DOUBLE click or the context menu — a single click does
    nothing (same as points/entities after 2026-09-01)."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{"net": "+3V3", "anchor_ref": "U1", "spokes": []}]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chains_cat = _find(dock.tree.topLevelItem(0), "Spokes")
    anchor = _find(chains_cat, "Anchor: U1")
    chain = _find(anchor, "+3V3")
    assert chain.text(0) == "+3V3"
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.placement_picked.connect(picked.append)
    dock.tree.itemClicked.emit(chain, 0)

    assert picked == []


def test_clicking_a_file_or_category_header_fires_no_signal(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

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
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0  # empty cells: section, nothing shown

    _write(root, MINIMAL_CELL)
    dock.refresh()

    root_item = dock.tree.topLevelItem(0)
    assert _find(root_item, "Cells").child(0).text(0) == "one_role"


def test_a_true_cycle_shows_as_a_single_error_item_not_a_crash(main_window, tmp_path):
    _write(tmp_path / "a.sexp", {"include": ["b.sexp"]})
    _write(tmp_path / "b.sexp", {"include": ["a.sexp"]})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["a.sexp"]})

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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

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
    _write(tmp_path / "sub.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    sub_item = dock.tree.topLevelItem(0).child(0)
    file_path, parent_path = dock._file_context_for_item(sub_item)
    assert file_path == (tmp_path / "sub.sexp").resolve()
    assert parent_path == root.resolve()


def test_add_cell_emits_request_instead_of_writing_directly(main_window, tmp_path):
    """2026-08-06 — Add cell used to write a raw {"components": []} stub
    straight to YAML with no form behind it (the exact root cause of a live
    bug, see gui/docks/cell_editor.py's module docstring); now it defers to
    CellDock's own Save path, same shape as Add point/Add thermal via pad/
    Add placer above."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_cell_requested.connect(requested.append)
    dock.add_cell_requested.emit(root)

    assert requested == [root]
    assert _load(root) == {"cells": {}}


def test_composite_cell_shows_nested_clone_placements_as_children(main_window, tmp_path):
    """2026-08-06 — the "tree" Denis actually meant once CellDock's own
    internal editor was built as tabs instead (see gui/docks/cell_editor.py):
    a composite cell's nested clone_placements: show as read-only child
    nodes under its own Cells leaf."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"leaf": {}, "composite": {"clone_placements": [{"name": "inner_cell", "cell": "leaf"}, {"name": "inner_role", "role": "SOME_ROLE"}]}}})

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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.cell_edit_requested.connect(lambda name, path: requested.append((name, path)))
    dock.cell_edit_requested.emit("one_role", root)

    assert requested == [("one_role", root)]


def test_update_from_selection_emits_name_and_file(main_window, tmp_path, monkeypatch):
    """"Update from selection..." (context menu, 2026-09-03, plan
    cell_geometry_refresh) — the cell leaf's context action emits
    cell_refresh_requested with the SAME (name, file_path) as "Edit cell...",
    so CellDock can load the right cell and refresh its geometry."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.cell_refresh_requested.connect(lambda name, path: requested.append((name, path)))

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    actions = dict(_context_menu_actions(dock, leaf, monkeypatch))
    assert "Update from selection..." in actions
    actions["Update from selection..."].trigger()

    assert requested == [("one_role", root.resolve())]


def test_import_from_selection_emits_name_and_file(main_window, tmp_path, monkeypatch):
    """"Import from selection..." (context menu, 2026-09-03, plan
    fpga_oscill_missing_copper_and_cell_import §B.3) — the cell leaf's context
    action emits cell_import_requested with the SAME (name, file_path) shape as
    "Update from selection...", so CellDock can load the right cell and
    backfill NEW via/track records (the additive counterpart of refresh)."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.cell_import_requested.connect(lambda name, path: requested.append((name, path)))

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    actions = dict(_context_menu_actions(dock, leaf, monkeypatch))
    assert "Import from selection..." in actions
    actions["Import from selection..."].trigger()

    assert requested == [("one_role", root.resolve())]


def test_rename_action_present_for_a_leaf_absent_for_a_category(main_window, tmp_path):
    """2026-08-04, Denis: "А мы можем добавить конекстное меню в конфиг
    чтобы переименовать плэейсменты, целлы, профили извлечения и т.д.?" —
    Rename only makes sense on an actual entry, not a category header or
    the file node itself."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_rename(root, "cells", "one_role")

    data = _load(root)
    assert "renamed_cell" in data["cells"]
    assert "one_role" not in data["cells"]
    assert _find(dock.tree.topLevelItem(0), "Cells").child(0).text(0) == "renamed_cell"


def test_rename_cell_cascades_a_reference_in_another_file(main_window, tmp_path, monkeypatch):
    _write(tmp_path / "cells.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["cells.sexp"], "clone_placements": [{"name": "spoke_1", "cell": "one_role"}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda self_, title, text: shown.append(text)))

    dock._on_rename(tmp_path / "cells.sexp", "cells", "one_role")

    assert _load(root)["clone_placements"][0]["cell"] == \
        "renamed_cell"
    assert len(shown) == 1 and "root.sexp" in shown[0]  # summary names the other changed file


def test_rename_declined_leaves_the_file_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))

    dock._on_rename(root, "cells", "one_role")

    assert "one_role" in _load(root)["cells"]


def test_rename_collision_shows_a_warning_and_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"a": {}, "b": {}}})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("b", True)))
    warned = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(1)))

    dock._on_rename(root, "cells", "a")

    assert warned == [1]
    assert _load(root)["cells"] == {"a": {}, "b": {}}


def test_add_thermal_via_pad_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"thermal_via_arrays": []})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_thermal_via_requested.connect(requested.append)
    dock.add_thermal_via_requested.emit(root)

    assert requested == [root]
    # nothing written — Add thermal via pad defers to ThermalViaArrayDock's
    # own Save path (2026-08-03, same reasoning as Add placer)
    assert _load(root) == {"thermal_via_arrays": []}


def test_thermal_via_leaf_click_emits_thermal_via_picked(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"thermal_via_arrays": [{"name": "fpga_thermal", "pad": "1"}]})

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
    root = tmp_path / "root.sexp"
    _write(root, {"coordinate_placements": [{"cluster": "X", "role": "R1"}]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.coordinate_placements_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Coordinate placements").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"cluster": "X", "role": "R1"}]


def test_add_point_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"points": {}})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_point_requested.connect(requested.append)
    dock.add_point_requested.emit(root)

    assert requested == [root]
    # nothing written — Add point defers to PointsDock's own Save path,
    # same reasoning as Add thermal via pad/Add placer above.
    assert _load(root) == {"points": {}}


def test_points_leaf_double_click_emits_points_edit_requested(main_window, tmp_path):
    """2026-09-01 (plan plan_2026_09_01_points_dialog.md): a DOUBLE click on
    a points: leaf opens the Points edit dialog. points: is a DICT section
    (see _entries()), so the payload is just the name; PointsDock.load_entry()
    re-reads the file for the data. A single click on a points: leaf now does
    NOTHING — the old points_picked signal was removed from ConfigTreeDock
    entirely (the Points form lives in a dialog opened by double click, not a
    DetailDock page)."""
    root = tmp_path / "root.sexp"
    _write(root, {"points": {"origin": {"xy": [0, 0]}}})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.points_edit_requested.connect(requested.append)
    leaf = _find(dock.tree.topLevelItem(0), "Points").child(0)
    dock._on_double_clicked(leaf, 0)

    assert requested == ["origin"]
    # The old single-click routing signal is gone — a points: leaf has no
    # points_picked anymore (only the double-click points_edit_requested).
    assert not hasattr(dock, "points_picked")


def test_entities_leaf_double_click_emits_entity_edit_requested(main_window, tmp_path):
    """2026-09-01 (plan plan_2026_09_01_tools_dialog_and_entity_roles.md): a
    DOUBLE click on an Entities leaf opens the "Edit template" dialog
    (ToolsDock) pre-loaded with that Entity. entities: is a LIST section, so
    the payload is the full dict — _on_double_clicked extracts the name, the
    same way _on_clicked does for entity_picked. The single click keeps its
    original meaning (entity_picked -> Placer Entity source)."""
    root = tmp_path / "root.sexp"
    _write(root, {"entities": [{"name": "E1", "cell": "pi_filter"}]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.entity_edit_requested.connect(requested.append)
    leaf = _find(dock.tree.topLevelItem(0), "Entities").child(0)
    dock._on_double_clicked(leaf, 0)

    assert requested == ["E1"]


def test_add_chain_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"chains": []})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_chain_requested.connect(requested.append)
    dock.add_chain_requested.emit(root)

    assert requested == [root]
    # nothing written — Add chain defers to ChainDock's own Save path, same
    # reasoning as Add thermal via pad/Add placer/Add point above.
    assert _load(root) == {"chains": []}


def test_chain_node_double_click_emits_chain_edit_requested(main_window, tmp_path):
    """A chains: CHAIN node is opened by a DOUBLE click (2026-09-01, plan
    rules_to_chains) — the payload is the full chain dict (like the old
    rule_picked) but routed through chain_edit_requested, not a single click."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{"net": "+3V3", "anchor_role": "FPGA"}]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.chain_edit_requested.connect(requested.append)
    chain = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: FPGA").child(0)
    dock._on_double_clicked(chain, 0)

    assert requested == [{"net": "+3V3", "anchor_role": "FPGA"}]


def test_entities_and_trees_categories_with_entity_click(main_window, tmp_path):
    """Phase 5.6: the config tree shows Entities + Trees categories, and
    clicking an Entity leaf emits entity_picked with the Entity's NAME (so
    Placer's Entity source can load it)."""
    root = tmp_path / "root.sexp"
    _write(root, {
        "entities": [{"name": "E1", "cell": "my_cell"}],
        "trees": [{"name": "t1", "anchor": {"origin": True},
                   "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 1.0]}]}],
    })
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    # both new categories are present (_find raises if missing)
    entities_cat = _find(dock.tree.topLevelItem(0), "Entities")
    _find(dock.tree.topLevelItem(0), "Trees")

    picked = []
    dock.entity_picked.connect(picked.append)
    leaf = entities_cat.child(0)
    dock._on_clicked(leaf, 0)
    assert picked == ["E1"]


def test_add_placer_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"clone_placements": []})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_placer_requested.connect(requested.append)
    dock.add_placer_requested.emit(root)

    assert requested == [root]
    # nothing written — Add placer defers to PlacerDock's own Save path
    assert _load(root) == {"clone_placements": []}


def test_add_included_file_creates_missing_file_and_wires_include(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})
    new_file = tmp_path / "power.sexp"
    assert not new_file.exists()

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_file), "")))
    dock._add_included_file(root)

    assert new_file.exists()
    assert _load(root)["include"] == ["power.sexp"]
    assert dock.tree.topLevelItem(0).child(0).text(0) == "power.sexp"


def test_add_included_file_rejects_a_file_with_root_only_keys(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})
    bad_file = tmp_path / "bad.sexp"
    _write(bad_file, {"layer": "B.Cu", "cells": {}})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(bad_file), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    dock._add_included_file(root)

    assert "include" not in _load(root)


def test_remove_file_disables_include_after_confirmation(main_window, tmp_path, monkeypatch):
    _write(tmp_path / "sub.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    assert dock.tree.topLevelItem(0).childCount() == 1

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._remove_file(tmp_path / "sub.sexp", root)

    data = _load(root)
    assert data["include"] == [{"path": "sub.sexp", "enabled": False}]
    # walk_include_tree skips disabled includes -> sub.sexp no longer shown
    assert dock.tree.topLevelItem(0).childCount() == 0


def test_remove_file_declined_leaves_include_untouched(main_window, tmp_path, monkeypatch):
    _write(tmp_path / "sub.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dock._remove_file(tmp_path / "sub.sexp", root)

    assert _load(root)["include"] == ["sub.sexp"]
    assert dock.tree.topLevelItem(0).childCount() == 1


def test_context_menu_has_no_remove_action_for_root(main_window, tmp_path, monkeypatch):
    """Root has no parent to remove itself from — the menu built for it
    must omit "Remove this file" entirely, not just disable it."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

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
    ("Spokes", "Add chain..."),
])
def test_category_context_menu_shows_only_its_own_add_action(
        main_window, tmp_path, monkeypatch, category_label, expected_add):
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
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
    ("Spokes", "Add chain..."),
])
def test_leaf_context_menu_shows_only_its_sections_add_action(
        main_window, tmp_path, monkeypatch, category_label, expected_add):
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), category_label).child(0)
    labels = _context_menu_labels(dock, leaf, monkeypatch)

    assert _add_labels(labels) == [expected_add, "Add included file..."]


def test_extract_profiles_category_and_leaf_have_no_add_action(main_window, tmp_path, monkeypatch):
    """Phase F (2026-09-01): the Extract dock (and its "New Extract..." entry
    point) was removed — extract_profiles: is now a CLI/config-only section.
    Its category/leaf must show NO section Add action and NO "New Extract..."
    at all (like clone_profiles) — only the unconditional "Add included file...".
    Both menus are built inside ONE addAction-capture session (re-patching
    between two calls would make the second capture's "original" the first
    patch — see _context_menu_labels' caller note)."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
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

    assert "Add extract profile..." not in captured
    assert "New Extract..." not in captured
    assert _add_labels(captured) == ["Add included file...", "Add included file..."]
    assert "Add cell..." not in captured
    assert "Add chain..." not in captured


def test_clone_profiles_category_has_no_add_actions(main_window, tmp_path, monkeypatch):
    """clone_profiles is read-only (no GUI edit form, deliberate scope limit)
    — its category must show NO Add action at all, not even the wrong ones."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    category = _find(dock.tree.topLevelItem(0), "Clone profiles")
    labels = _context_menu_labels(dock, category, monkeypatch)

    assert _add_labels(labels) == ["Add included file..."]


def test_file_header_context_menu_shows_all_six_add_actions(
        main_window, tmp_path, monkeypatch):
    """Denis's explicit decision: a file header (incl. the root) must still
    offer ALL the Add actions — otherwise a fresh file with no sections yet
    couldn't create its first entity. (2026-09-01: extract_profiles no longer
    has a section Add action — profiles are a CLI/config-only section now,
    Phase F — so the count is six, not seven.)"""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    labels = _context_menu_labels(dock, dock.tree.topLevelItem(0), monkeypatch)

    for label in ("Add cell...", "Add thermal via pad...", "Add coordinate placement...",
                  "Add placer...", "Add point...", "Add chain..."):
        assert label in labels


def test_nested_cell_child_context_menu_shows_all_add_actions(
        main_window, tmp_path, monkeypatch):
    """Read-only nested cell children carry no UserRole data at all — their
    section is unknown, so the Add block falls back to ALL actions (same as
    a file header)."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"composite": {"clone_placements": [{"name": "inner_cell", "cell": "leaf"}]}}})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    composite = _find(_find(dock.tree.topLevelItem(0), "Cells"), "composite")
    nested = composite.child(0)
    labels = _context_menu_labels(dock, nested, monkeypatch)

    assert "Add cell..." in labels
    assert "Add chain..." in labels


def test_add_section_for_item_leaf_category_and_file_header(main_window, tmp_path):
    """The helper driving the filtering: leaf -> its section, category -> its
    section (tagged 2026-08-13), file header -> None (all Add actions)."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
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
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(root, "cells", "one_role")

    assert _load(root)["cells"] == {}
    assert list(tmp_path.glob("root.sexp.bak.*"))
    assert dock.tree.topLevelItem(0).childCount() == 0  # empty Cells section, no leaf shown


def test_delete_in_staged_mode_drops_leaf_from_tree_immediately(
        main_window, tmp_path, monkeypatch):
    """Regression (plan 2026_09_04_staged_delete_stale_tree_and_save_hotkey,
    Bug A — Denis: Delete leaves the entity/cell in the tree until Save/restart):
    in the LIVE staged mode (ConfigWorkingSet enabled) a Config-tree Delete must
    drop the leaf from the tree RIGHT AWAY. The delete used to stay invisible
    because a graph-cached result recomputed over staged content never recorded
    the staged file in its trace, so stage_write's invalidate_graph_path() could
    not evict it and the tree rebuild kept reading the pre-delete state. The
    deletion stays STAGED (dirty ●, disk untouched) — File > Save remains the
    only commit (Denis's requirement)."""
    from kicadstamp.config_working_set import WORKING_SET

    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    WORKING_SET.enabled = True
    try:
        # Make the root STAGED (like any earlier edit this session) and rebuild
        # the tree once over the staged content — exactly the moment after which
        # the stale graph entry used to lose the staged file from its trace.
        WORKING_SET.stage_write(root, _load(root))
        dock.refresh()
        dock._on_delete(root, "cells", "one_role")

        # Leaf gone from the tree immediately...
        assert dock.tree.topLevelItem(0).childCount() == 0
        # ...and the deletion is STAGED only: dirty ● set, disk file untouched.
        assert WORKING_SET.is_dirty()
        assert "one_role" in _load(root)["cells"]
    finally:
        WORKING_SET.enabled = False
        WORKING_SET.clear()


def test_delete_declined_leaves_the_file_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    dock._on_delete(root, "cells", "one_role")

    assert "one_role" in _load(root)["cells"]
    assert not list(tmp_path.glob("root.sexp.bak.*"))


def test_delete_with_a_reference_asks_about_cascade_and_removes_both_on_yes(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {**MINIMAL_CELL, "clone_placements": [{"name": "spoke_1", "cell": "one_role"}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda self_, title, text, *a, **k:
                                     (shown.append(text), QMessageBox.StandardButton.Yes)[1]))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(root, "cells", "one_role")

    data = _load(root)
    assert data["cells"] == {}
    assert data["clone_placements"] == []  # cascade removed the referencing spoke too
    assert len(shown) == 1 and "spoke_1" in shown[0]  # dialog listed the reference


def test_delete_with_a_reference_declined_cancels_the_whole_delete(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, {**MINIMAL_CELL, "clone_placements": [{"name": "spoke_1", "cell": "one_role"}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))

    dock._on_delete(root, "cells", "one_role")

    data = _load(root)
    assert "one_role" in data["cells"]  # nothing removed at all
    assert data["clone_placements"][0]["cell"] == "one_role"
    assert not list(tmp_path.glob("root.sexp.bak.*"))


# ── Export (2026-08-05) ──────────────────────────────────────────────────

def test_export_multi_select_enabled(main_window):
    from PyQt6.QtWidgets import QAbstractItemView
    dock = ConfigTreeDock(main_window)
    assert dock.tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection


def test_selected_export_items_ignores_file_and_category_headers(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
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
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"a": {}, "b": {}}})
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
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    target = tmp_path / "out.sexp"
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_export(dock._selected_export_items())

    assert "one_role" in _load(target)["cells"]
    assert _load(root) == \
        MINIMAL_CELL  # the source is untouched — pure copy


def test_on_export_to_a_non_empty_file_merges_when_merge_is_chosen(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    target = tmp_path / "out.sexp"
    _write(target, {"cells": {"existing": {}}})
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

    data = _load(target)
    assert set(data["cells"].keys()) == {"existing", "one_role"}


def test_on_export_to_a_non_empty_file_overwrites_when_overwrite_is_chosen(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    target = tmp_path / "out.sexp"
    _write(target, {"cells": {"existing": {}}, "include": ["somewhere.sexp"]})
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

    data = _load(target)
    assert set(data["cells"].keys()) == {"one_role"}
    assert "include" not in data


def test_on_export_cancelled_dialog_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    target = tmp_path / "out.sexp"
    _write(target, {"cells": {"existing": {}}})
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

    data = _load(target)
    assert data["cells"] == {"existing": {}}  # untouched — Cancel aborted the export


def test_clone_placement_leaf_shows_name_when_set(main_window, tmp_path):
    """A clone_placements leaf whose entry carries name shows THAT (the save/
    --only identity) in the tree — not the raw Cluster tag `cluster`. Entries
    without name fall back to cluster as before."""
    root = tmp_path / "root.sexp"
    _write(root, {"clone_placements": [{"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD", "cell": "ldo_adj", "xy": [0, 0]}, {"cluster": "plain", "cell": "ldo_adj", "xy": [0, 0]}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    category = _find(dock.tree.topLevelItem(0), "Clone placements")
    texts = [category.child(i).text(0) for i in range(category.childCount())]
    assert "CH0_PIF_AVDD" in texts
    assert "plain" in texts


# ── graph_changed broadcast (2026-08-15, plan graph_changed_broadcast) ────

def test_rename_emits_graph_changed_once(main_window, tmp_path, monkeypatch):
    """A successful rename changes an entry's NAME in the graph — every other
    dock's graph-derived name combos must hear about it (via DockHub's
    broadcast), so graph_changed fires exactly once here."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))
    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_rename(root, "cells", "one_role")

    assert emitted == [True]


def test_delete_emits_graph_changed_once(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(root, "cells", "one_role")

    assert emitted == [True]


def test_add_included_file_emits_graph_changed_once(main_window, tmp_path, monkeypatch):
    """The exact Denis complaint (plan graph_changed_broadcast): adding a
    brand-new file to the graph must tell every OTHER dock's file combo,
    not just refresh this tree's own display."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})
    new_file = tmp_path / "power.sexp"
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))
    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_file), "")))

    dock._add_included_file(root)

    assert emitted == [True]


def test_remove_file_emits_graph_changed_once(main_window, tmp_path, monkeypatch):
    _write(tmp_path / "sub.sexp", MINIMAL_CELL)
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    dock._remove_file(tmp_path / "sub.sexp", root)

    assert emitted == [True]


def test_export_does_not_emit_graph_changed(main_window, tmp_path, monkeypatch):
    """Export copies content to a separate file WITHOUT wiring it into
    include: (Denis: "Перенос пока не делаем") — the graph's shape does not
    change, so graph_changed must NOT fire (a second, separate
    _add_included_file() is what eventually changes the graph)."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    target = tmp_path / "out.sexp"
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))
    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_export(dock._selected_export_items())

    assert emitted == []


# ── Rename confirmation toggle (2026-08-25) ──────────────────────────────

def test_rename_confirmation_shown_when_enabled(main_window, tmp_path, monkeypatch):
    """Default (key absent == enabled): a successful Rename still shows the
    modal QMessageBox.information summary."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda self_, title, text: shown.append(text)))

    dock._on_rename(root, "cells", "one_role")

    assert len(shown) == 1  # the confirmation dialog was shown exactly once


def test_rename_confirmation_silent_when_disabled(main_window, tmp_path, monkeypatch, caplog):
    """Setting OFF: the rename still happens (file updated + graph_changed
    emitted), but the modal confirmation is NOT shown — the same summary goes
    to the Log dock instead."""
    settings.state.set("rename_confirmation_enabled", False)
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda self_, title, text: shown.append(text)))
    emitted = []
    dock.graph_changed.connect(lambda: emitted.append(True))

    dock._on_rename(root, "cells", "one_role")

    assert shown == []  # no modal popup
    assert "renamed_cell" in _load(root)["cells"]
    assert emitted == [True]  # the rest of the rename flow is untouched
    assert any("Renamed" in r.message for r in caplog.records)  # summary in the Log


# ── F2 = Rename shortcut (2026-08-25) ────────────────────────────────────

def test_f2_rename_calls_on_rename_with_context_menu_args(main_window, tmp_path, monkeypatch):
    """F2 on a selected leaf routes through _on_rename with exactly the
    (file_path, section, old_name) the context menu's "Rename..." would use —
    both go through the same _rename_target_for_item."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)  # "my_cell"

    calls = []
    monkeypatch.setattr(dock, "_on_rename",
                        lambda file_path, section, old_name: calls.append(
                            (str(file_path), section, old_name)))

    dock.tree.setCurrentItem(leaf)
    dock._on_rename_shortcut()

    assert calls == [(str(root), "cells", "my_cell")]


def test_f2_rename_noop_without_selection(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    calls = []
    monkeypatch.setattr(dock, "_on_rename", lambda *a: calls.append(a))

    dock.tree.setCurrentItem(None)
    dock._on_rename_shortcut()  # must not raise, must not call _on_rename

    assert calls == []


def test_f2_rename_noop_on_non_leaf(main_window, tmp_path, monkeypatch):
    """F2 on a category header / file node is normal tree navigation — no
    rename, no error message."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    calls = []
    monkeypatch.setattr(dock, "_on_rename", lambda *a: calls.append(a))

    category = _find(dock.tree.topLevelItem(0), "Cells")
    dock.tree.setCurrentItem(category)
    dock._on_rename_shortcut()

    assert calls == []


def test_f2_shortcut_is_widget_scoped(main_window, tmp_path):
    """F2 is scoped to the tree (WidgetWithChildrenShortcut), NOT the window —
    so it never steals F2 from other widgets that may gain their own shortcut."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    assert dock._rename_shortcut.context() == config_tree_mod.Qt.ShortcutContext.WidgetWithChildrenShortcut


# ── comment marker (handoff_2026_08_27_entity_comment_field.md §5) ─────────

def test_cell_leaf_with_comment_shows_glyph_and_tooltip(main_window, tmp_path):
    """comment on a DICT-section (cells) entry: _entries() yields the bare
    name as payload, so the marker must come from raw.get(name) — the exact
    regression the handoff flagged (payload is a str, not the record dict)."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"noted": {"comment": "a cell note"}}})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    assert leaf.text(0) == "📝 noted"
    assert leaf.toolTip(0) == "a cell note"


def test_cell_leaf_without_comment_is_plain(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"plain": {}}})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    assert leaf.text(0) == "plain"
    assert leaf.toolTip(0) == ""


def test_chains_chain_node_with_comment_shows_glyph_and_tooltip(main_window, tmp_path):
    """comment on a chains: entry — the marker goes on the CHAIN node (the
    nested tree's second level, under its anchor), straight from the dict."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{"net": "+3V3", "anchor_ref": "U1", "comment": "a chain note", "spokes": []}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chains_cat = _find(dock.tree.topLevelItem(0), "Spokes")
    chain = _find(_find(chains_cat, "Anchor: U1"), "📝 +3V3")
    assert chain.text(0) == "📝 +3V3"
    assert chain.toolTip(0) == "a chain note"


def test_chains_chain_node_without_comment_is_plain(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{"net": "+3V3", "anchor_ref": "U1", "spokes": []}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chains_cat = _find(dock.tree.topLevelItem(0), "Spokes")
    chain = _find(_find(chains_cat, "Anchor: U1"), "+3V3")
    assert chain.text(0) == "+3V3"
    assert chain.toolTip(0) == ""


# ── Selection survives refresh() (2026-08-27) ─────────────────────────────

def test_selection_survives_refresh_for_leaf(main_window, tmp_path):
    """A selected leaf stays selected after refresh() rebuilds the tree —
    every dock's saved signal feeds refresh() (gui/dock_hub.py), which used
    to drop the selection on EVERY Save (handoff config_tree_preserve_
    selection)."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)
    assert dock.tree.selectedItems()

    dock.refresh()

    selected = dock.tree.selectedItems()
    assert len(selected) == 1
    assert selected[0].text(0) == "one_role"


def test_selection_survives_refresh_for_file_and_category(main_window, tmp_path):
    """File and category nodes survive refresh() too — exercising all 3
    _item_identity branches (file / category / leaf), not only the leaf."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    file_item = dock.tree.topLevelItem(0)
    cells_item = _find(file_item, "Cells")
    file_item.setSelected(True)
    cells_item.setSelected(True)
    assert len(dock.tree.selectedItems()) == 2

    dock.refresh()

    selected_texts = sorted(i.text(0) for i in dock.tree.selectedItems())
    assert selected_texts == ["Cells", "root.sexp"]


def test_selection_survives_refresh_for_commented_leaf(main_window, tmp_path):
    """The comment glyph is stripped back to the entry name when recovering
    identity (the label is "📝 noted", the identity must match "noted" after
    the rebuild) — exercises _COMMENT_GLYPH handling in _item_identity."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"noted": {"comment": "a note"}}})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    assert leaf.text(0) == "📝 noted"
    leaf.setSelected(True)

    dock.refresh()

    selected = dock.tree.selectedItems()
    assert len(selected) == 1
    assert selected[0].text(0) == "📝 noted"


def test_selection_degrades_gracefully_when_entry_deleted(main_window, tmp_path):
    """Best-effort contract: an identity that no longer exists (the entry was
    deleted/renamed in the underlying file before refresh) is simply not
    re-selected — never a crash, selection ends up empty."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    leaf.setSelected(True)

    _write(root, {"cells": {"other": {}}})
    dock.refresh()  # must not raise

    assert dock.tree.selectedItems() == []


# ── Chains nested tree (2026-09-01, plan rules_to_chains) ────────────────────
# The Chains category renders category -> anchor -> chain -> pad leaves (NOT a
# flat list and NOT a pad table — the whole point of the modernization).

def test_chains_nested_tree_renders_anchor_chain_pads(main_window, tmp_path):
    """Chains category -> anchor group -> chain node -> pad leaves. The chain
    carries anchor_ref: U1 and two spokes; the pads are LEAVES with the pad
    number as label and the cell as tooltip (no separate table anywhere)."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{
        "net": "+3V3", "anchor_ref": "U1",
        "spokes": [{"pad": "1", "cell": "fpga"}, {"pad": "3", "cell": "cap"}],
    }]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chains_cat = _find(dock.tree.topLevelItem(0), "Spokes")
    anchor = _find(chains_cat, "Anchor: U1")
    chain = _find(anchor, "+3V3")

    assert chain.childCount() == 2  # pads are leaves, not a table
    pad1 = _find(chain, "1")
    assert pad1.data(0, Qt.ItemDataRole.UserRole) == (
        "pad", "chains", chain.data(0, Qt.ItemDataRole.UserRole)[2], 0)
    assert pad1.toolTip(0) == "cell fpga"
    pad3 = _find(chain, "3")
    assert pad3.data(0, Qt.ItemDataRole.UserRole) == (
        "pad", "chains", chain.data(0, Qt.ItemDataRole.UserRole)[2], 1)
    assert pad3.toolTip(0) == "cell cap"


def test_chains_sorts_anchors_then_chains_then_pads(main_window, tmp_path):
    """Anchors sort by key, chains by effective name, pads by pad number —
    regardless of the source list order."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [
        {"net": "B", "anchor_ref": "U2",
         "spokes": [{"pad": "3", "cell": "c"}, {"pad": "1", "cell": "c"}]},
        {"net": "A", "anchor_ref": "U1", "spokes": [{"pad": "2", "cell": "c"}]},
        {"name": "Z_named", "net": "A", "anchor_ref": "U1",
         "spokes": [{"pad": "5", "cell": "c"}]},
    ]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chains_cat = _find(dock.tree.topLevelItem(0), "Spokes")
    assert chains_cat.childCount() == 2  # two anchors
    anchor1 = chains_cat.child(0)
    anchor2 = chains_cat.child(1)
    assert anchor1.text(0) == "Anchor: U1"
    assert anchor2.text(0) == "Anchor: U2"
    # U1 has two chains: effective name "A" (net) then "Z_named" (name beats
    # net but sorts after) — sorted by effective name.
    assert anchor1.child(0).text(0) == "A"
    assert anchor1.child(1).text(0) == "Z_named"
    # U2's single chain "B" has its pads sorted 1 then 3.
    assert anchor2.child(0).child(0).text(0) == "1"
    assert anchor2.child(0).child(1).text(0) == "3"


def test_chains_pad_and_chain_double_click_emit_edit_requests(main_window, tmp_path):
    """Double click routes by node kind: CHAIN node -> chain_edit_requested
    (whole chain dict), PAD leaf -> pad_edit_requested (chain dict, pad index).
    Single click on either fires NO pick signal (see the single-click test)."""
    root = tmp_path / "root.sexp"
    chain_data = {"net": "+3V3", "anchor_role": "FPGA",
                  "spokes": [{"pad": "1", "cell": "c"}, {"pad": "2", "cell": "c"}]}
    _write(root, {"chains": [chain_data]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    anchor = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: FPGA")
    chain = _find(anchor, "+3V3")
    pad1 = _find(chain, "1")

    chain_requests = []
    dock.chain_edit_requested.connect(chain_requests.append)
    pad_requests = []
    dock.pad_edit_requested.connect(
        lambda chain, idx: pad_requests.append((chain, idx)))

    dock._on_double_clicked(chain, 0)
    assert chain_requests == [chain_data]
    dock._on_double_clicked(pad1, 0)
    assert pad_requests == [(chain_data, 0)]

    # Single clicks fire neither.
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.placement_picked.connect(picked.append)
    dock._on_clicked(chain, 0)
    dock._on_clicked(pad1, 0)
    assert picked == []


def test_chains_pad_single_click_emits_pad_picked(main_window, tmp_path):
    """A SINGLE click on a chains: PAD leaf opens the spoke editor (2026-09-05,
    design config_qview_chain_entity_pages §4): pad_picked(chain_dict,
    pad_index). Chain and anchor single clicks fire no pick signal — only the
    pad (the leaf-level edit unit) routes."""
    root = tmp_path / "root.sexp"
    chain_data = {"net": "+3V3", "anchor_role": "FPGA",
                  "spokes": [{"pad": "1", "cell": "c"}, {"pad": "2", "cell": "c"}]}
    _write(root, {"chains": [chain_data]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    anchor = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: FPGA")
    chain = _find(anchor, "+3V3")
    pad1 = _find(chain, "1")

    picked = []
    dock.pad_picked.connect(lambda c, idx: picked.append((c, idx)))
    dock._on_clicked(pad1, 0)
    assert picked == [(chain_data, 0)]

    dock._on_clicked(chain, 0)
    dock._on_clicked(anchor, 0)
    assert len(picked) == 1  # only the pad leaf emitted pad_picked


def test_chain_and_anchor_single_click_emit_nav_signals(main_window, tmp_path):
    """A SINGLE click on a chains: CHAIN node -> chain_picked(chain); on a
    chains: ANCHOR node -> anchor_picked(anchor_key, [chain dicts]) — the
    chains-navigation QView drill (2026-09-05, design
    config_qview_chain_entity_pages §4/§8.2)."""
    root = tmp_path / "root.sexp"
    c1 = {"net": "+3V3", "anchor_role": "FPGA",
          "spokes": [{"pad": "1", "cell": "c"}]}
    c2 = {"net": "+1V2", "anchor_role": "FPGA", "spokes": []}
    _write(root, {"chains": [c1, c2]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    anchor = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: FPGA")
    chain1 = _find(anchor, "+3V3")

    chains_picked = []
    anchors_picked = []
    dock.chain_picked.connect(chains_picked.append)
    dock.anchor_picked.connect(lambda key, chains: anchors_picked.append((key, chains)))

    dock._on_clicked(chain1, 0)
    assert chains_picked == [c1]

    dock._on_clicked(anchor, 0)
    assert len(anchors_picked) == 1
    key, chains = anchors_picked[0]
    assert key == "FPGA"
    # Anchor child chains come in the tree's sorted order — compare by net set.
    assert {c["net"] for c in chains} == {"+3V3", "+1V2"}


def test_chains_pad_selection_survives_refresh(main_window, tmp_path):
    """A selected PAD leaf stays selected across refresh() — its identity is
    (file, "chains", parent chain effective name, pad index), rebuilt from the
    fresh chain dict after the tree rebuild."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [{"net": "+3V3", "anchor_ref": "U1",
                              "spokes": [{"pad": "1", "cell": "c"},
                                         {"pad": "2", "cell": "c"}]}]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chain = _find(_find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1"), "+3V3")
    pad2 = _find(chain, "2")
    pad2.setSelected(True)
    assert len(dock.tree.selectedItems()) == 1

    dock.refresh()

    selected = dock.tree.selectedItems()
    assert len(selected) == 1
    assert selected[0].text(0) == "2"
    assert selected[0].data(0, Qt.ItemDataRole.UserRole)[0] == "pad"


def test_chain_context_menu_has_spoke_redraw_and_bulk(main_window, tmp_path, monkeypatch):
    """Chain node's EXTRA context actions (beyond the generic Add chain... /
    Rename.../Delete... from the shared blocks): Add spoke..., Redraw chain,
    Bulk set Cell for net... — plus the generic Add chain... from the Add block.
    Their triggered QActions emit add_pad_requested / chain_redraw_requested /
    bulk_set_cell_requested (Redraw/Bulk moved OUT of the old RuleDock buttons,
    plan rules_to_chains)."""
    root = tmp_path / "root.sexp"
    chain_data = {"net": "+3V3", "anchor_ref": "U1", "spokes": []}
    _write(root, {"chains": [chain_data]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chain = _find(_find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1"), "+3V3")
    actions = _context_menu_actions(dock, chain, monkeypatch)
    labels = [text for text, _ in actions]

    for expected in ("Add spoke...", "Redraw chain", "Bulk set Cell for net..."):
        assert expected in labels, labels

    by_label = dict(actions)
    add_pad = []
    dock.add_pad_requested.connect(add_pad.append)
    by_label["Add spoke..."].trigger()
    assert add_pad == [chain_data]

    redraw = []
    dock.chain_redraw_requested.connect(redraw.append)
    by_label["Redraw chain"].trigger()
    assert redraw == [chain_data]

    bulk = []
    dock.bulk_set_cell_requested.connect(bulk.append)
    by_label["Bulk set Cell for net..."].trigger()
    assert bulk == ["+3V3"]


def test_pad_context_menu_has_redraw_spoke_and_delete_pad(main_window, tmp_path, monkeypatch):
    """Pad leaf's context actions: Redraw spoke + Delete pad... (the pad has no
    Rename/Delete — it is not a standalone record)."""
    root = tmp_path / "root.sexp"
    chain_data = {"net": "+3V3", "anchor_ref": "U1",
                  "spokes": [{"pad": "1", "cell": "fpga"},
                             {"pad": "2", "cell": "c"}]}
    _write(root, {"chains": [chain_data]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    chain = _find(_find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1"), "+3V3")
    pad1 = _find(chain, "1")
    actions = _context_menu_actions(dock, pad1, monkeypatch)
    labels = [text for text, _ in actions]

    assert "Redraw spoke" in labels, labels
    assert "Delete pad..." in labels, labels
    assert "Rename..." not in labels  # pads have no name of their own

    redraw = []
    dock.pad_redraw_requested.connect(
        lambda chain, idx: redraw.append((chain, idx)))
    dict(actions)["Redraw spoke"].trigger()
    assert redraw == [(chain_data, 0)]


def test_anchor_context_menu_has_add_chain_and_redraw_chains(main_window, tmp_path, monkeypatch):
    """Anchor node: the generic Add chain... (its own Add block) PLUS the
    anchor-level "Redraw chains..." that redraws every chain under this anchor
    in ONE batch action (2026-09-01, Denis: "если корневой компонент, то
    вообще все его спицы") — it emits anchor_redraw_requested with the list
    of chain dicts. Pads/Delete are per-chain, not per-anchor."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [
        {"net": "+3V3", "anchor_ref": "U1", "spokes": []},
        {"net": "GND", "anchor_ref": "U1", "spokes": []},
    ]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    anchor = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1")
    actions = _context_menu_actions(dock, anchor, monkeypatch)
    labels = [text for text, _ in actions]

    assert "Add chain..." in labels
    assert "Redraw chains..." in labels
    assert "Add spoke..." not in labels
    assert "Redraw chain" not in labels  # the anchor uses the batch "Redraw chains..."
    assert "Delete pad..." not in labels

    redraws = []
    dock.anchor_redraw_requested.connect(redraws.append)
    dict(actions)["Redraw chains..."].trigger()
    assert len(redraws) == 1
    assert [c["net"] for c in redraws[0]] == ["+3V3", "GND"]


# ── Collapse-state persistence (2026-09-03, plan tree_ui_state_persistence P3) ──
# Branches the user collapsed survive refresh() (which expandAll()s every time)
# and app restarts. Stored as DEVIATIONS in gui_state.json["config_tree_collapsed"]
# (a flat list of _item_identity tuples, Path at position 1 -> str at the JSON
# boundary). gui_state.json is isolated per test by conftest's isolated_settings.

def test_collapsed_category_persists_and_restores_after_refresh(main_window, tmp_path):
    """Collapsing a category branch writes it to config_tree_collapsed; a later
    refresh() (a Save elsewhere -> full rebuild + expandAll) re-collapses exactly
    that branch, leaving everything else expanded."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    cells = _find(root_item, "Cells")
    assert cells.isExpanded() is True          # default: everything expanded
    cells.setExpanded(False)                    # user collapses Cells
    assert ["category", str(root), "cells"] in settings.state.get(
        "config_tree_collapsed")

    dock.refresh()                              # rebuild elsewhere in the app
    root_item2 = dock.tree.topLevelItem(0)
    assert _find(root_item2, "Cells").isExpanded() is False      # restored
    assert _find(root_item2, "Points").isExpanded() is True      # untouched


def test_collapsed_state_restored_on_fresh_dock(main_window, tmp_path):
    """A fresh ConfigTreeDock over the same gui_state.json (== app restart)
    restores the collapsed branches."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    _find(dock.tree.topLevelItem(0), "Points").setExpanded(False)
    assert ["category", str(root), "points"] in settings.state.get(
        "config_tree_collapsed")

    dock2 = ConfigTreeDock(main_window)         # app restart
    dock2.set_root_file(root)
    assert _find(dock2.tree.topLevelItem(0), "Points").isExpanded() is False
    assert _find(dock2.tree.topLevelItem(0), "Cells").isExpanded() is True


def test_expanding_a_collapsed_branch_removes_the_deviation(main_window, tmp_path):
    """Re-expanding a branch the user collapsed removes it from the deviations
    list — the tree returns to the 'everything expanded' default."""
    root = tmp_path / "root.sexp"
    _write(root, ALL_SECTIONS)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    points = _find(dock.tree.topLevelItem(0), "Points")
    points.setExpanded(False)
    assert ["category", str(root), "points"] in settings.state.get(
        "config_tree_collapsed")
    points.setExpanded(True)                    # user re-expands
    assert ["category", str(root), "points"] not in settings.state.get(
        "config_tree_collapsed")


def test_stale_and_broken_collapsed_entries_are_fatal_safe(main_window, tmp_path):
    """A persisted collapsed identity whose record no longer exists (renamed/
    deleted) is simply not found on restore; broken rows are skipped. Never a
    crash, and nothing unrelated gets collapsed."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    settings.state.set("config_tree_collapsed", [
        ["category", str(root), "no_such_section"],   # stale section
        "not-a-list",                                  # broken row
        123,                                           # broken row
    ])
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)                           # must not crash
    assert _find(dock.tree.topLevelItem(0), "Cells").isExpanded() is True


def test_collapsing_one_chain_restores_just_that_chain(main_window, tmp_path):
    """chains: nested structure (category -> anchor -> chain -> pads): collapsing
    ONE chain node is restored for exactly that chain — its sibling under the
    same anchor stays expanded."""
    root = tmp_path / "root.sexp"
    _write(root, {"chains": [
        {"net": "+3V3", "anchor_ref": "U1",
         "spokes": [{"pad": "1", "cell": "c"}]},
        {"net": "GND", "anchor_ref": "U1",
         "spokes": [{"pad": "2", "cell": "c"}]},
    ]})
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    anchor = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1")
    chain_3v3 = _find(anchor, "+3V3")
    chain_gnd = _find(anchor, "GND")
    assert chain_3v3.isExpanded() is True and chain_gnd.isExpanded() is True
    chain_3v3.setExpanded(False)                       # collapse only +3V3
    assert ["chain", str(root), "chains", "+3V3"] in settings.state.get(
        "config_tree_collapsed")

    dock.refresh()
    anchor2 = _find(_find(dock.tree.topLevelItem(0), "Spokes"), "Anchor: U1")
    assert _find(anchor2, "+3V3").isExpanded() is False
    assert _find(anchor2, "GND").isExpanded() is True  # sibling untouched


def test_persist_ui_state_flushes_collapsed_from_widget(main_window, tmp_path):
    """persist_ui_state() (the MainWindow quit-flush) re-reads the CURRENT
    widgets — a collapse not yet persisted (out-of-band divergence) is still
    saved on quit."""
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    cells = _find(dock.tree.topLevelItem(0), "Cells")
    cells.setExpanded(False)
    dock._collapsed.clear()                            # simulate divergence
    dock.persist_ui_state()
    assert ["category", str(root), "cells"] in settings.state.get(
        "config_tree_collapsed")
