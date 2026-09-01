# tests/gui/test_phase3_wiring.py
"""
Phase 3 (gui optimization roadmap, handoff_gui_optimization_2026_08_01.md):
real pyqtSignals replacing callable-attribute wiring (3.1) + BoardConnection
injection so docks stop reaching into main_window.connection deep (3.2).
These tests pin down the composition-root wiring in gui/main_window.py — the
Config tree's file_selected signal reaching every listener (2026-08-03,
replaced FilePickerDock's three independent role signals entirely — see
gui/docks/config_tree.py's module docstring), and the two connection-taking
docks using the injected object instead of main_window.connection.
"""
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QDialog

from gui.schema_model import SchematicComponent
from kicadstamp.config import NetTrace
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.domain.board import Track
from kicadstamp.domain.geometry import Vector2
from kicadstamp.explore import Selected
from kicadstamp.trees import TreeAnchor

from gui import settings
from gui.dock_hub import DockHub
from gui.docks.extract import ExtractDock
from gui.docks.role_cluster_tree import RoleClusterTreeDock
from gui.main_window import MainWindow

import gui.dock_hub as dock_hub_mod
import gui.docks.tree_from_selection as tfs_mod
import gui.docks.tree_from_selection_dialog as tfsd_mod
import kicadstamp.net_trace_extract as net_trace_extract_mod


def _find_item(model, text):
    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row)
            if child.text() == text:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(model.invisibleRootItem())


class _FakeSelected:
    def __init__(self, ref, role, cluster):
        self.ref, self.role, self.cluster = ref, role, cluster
        self.fp = object()


# ── 3.1: role signals propagate from the Files dock to every listener ───────

def _write(path, data=None):
    path.write_text(dict_to_sexp(data if data is not None else {}), encoding="utf-8")


def test_config_tree_file_selected_does_not_retarget_entity_docks(real_main_window, tmp_path):
    """2026-08-21 (plan flatten_and_single_file_gui): the entity docks no
    longer follow tree clicks — new records always go to the project ROOT
    file, so file_selected must NOT change their write targets."""
    target_file = tmp_path / "power.sexp"
    _write(target_file)

    real_main_window.config_tree_dock.file_selected.emit(target_file)

    assert real_main_window.extract_dock._target_path is None
    assert real_main_window.extract_dock._profile_path is None
    assert real_main_window.extract_dock._placer_path is None
    assert real_main_window.placer_dock._cells_path is None
    assert real_main_window.placer_dock._placer_path is None


def test_root_metadata_dock_restores_last_root_after_restart(qapp, tmp_path):
    """A previous session's root file must be restored on startup —
    RootMetadataDock._restore_last_root() (root ownership moved here
    2026-08-11, was ConfigTreeDock's own — see gui/docks/root_metadata.py's
    module docstring)."""
    root_file = tmp_path / "root.sexp"
    _write(root_file)

    data = settings.load()
    data["last_root_file"] = str(root_file)
    settings.save(data)

    window = MainWindow(timeout_ms=10, verbose=False)
    try:
        assert window.root_metadata_dock._path == root_file
    finally:
        window._timer.stop()
        window._selection_timer.stop()
        window._poll_worker.stop()


def test_config_tree_picks_up_a_root_restored_before_wiring_existed(qapp, tmp_path):
    """Found live 2026-08-05 (Denis: "он не видит настроек корневого
    проекта. No root file open") for the ORIGINAL ConfigTreeDock-owned
    case this now mirrors in reverse: RootMetadataDock._restore_last_root()
    runs inside ITS OWN __init__ (see test_root_metadata_dock_restores_
    last_root_after_restart above), which happens before DockHub._wire()
    ever connects root_changed — so the very first emit (if a root was
    restored on startup) fires into the void. A restored project used to
    silently open with the panel/tree stuck on its placeholder despite the
    OTHER one showing the right root. DockHub._wire() must sync explicitly
    from root_metadata_dock.root_path, not rely solely on the signal."""
    root_file = tmp_path / "root.sexp"
    _write(root_file)

    data = settings.load()
    data["last_root_file"] = str(root_file)
    settings.save(data)

    window = MainWindow(timeout_ms=10, verbose=False)
    try:
        assert window.config_tree_dock._root_path == root_file
    finally:
        window._timer.stop()
        window._selection_timer.stop()
        window._poll_worker.stop()


def test_tree_cluster_picked_fills_placer_cluster_field(real_main_window):
    """The Components-tree -> Placer wiring now goes through the
    cluster_picked signal — clicking a Cluster group node in the real window
    fills PlacerDock's Cluster field.

    real_main_window's tree_dock is wired to a LIVE BoardConnection on this
    machine, and clicking would highlight the real board through the real
    kipy adapter (our _FakeSelected.fp is not a kipy footprint). The signal
    is emitted before the board-highlight early-return, so swapping in a
    board-less connection still exercises the signal -> Placer wiring while
    keeping the live adapter untouched."""
    real_main_window.tree_dock.group_by.setCurrentIndex(1)  # Cluster grouping
    real_main_window.tree_dock._connection = SimpleNamespace(board=None)
    real_main_window.tree_dock.set_footprints([
        _FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
    ])

    top_level = _find_item(real_main_window.tree_dock.tree.model(), "Channel_1")
    real_main_window.tree_dock._on_clicked(
        real_main_window.tree_dock.tree.model().indexFromItem(top_level))

    assert real_main_window.placer_dock.cluster_edit.currentText() == "Channel_1"


def test_cell_picked_fills_placer_selected_cell(real_main_window):
    """ConfigTreeDock -> PlacerDock wiring (cell_picked -> set_selected_cell,
    see gui/docks/config_tree.py's cell_picked docstring) — clicking a Cell
    leaf in the real Config tree must reach PlacerDock's Cell field
    end-to-end, not just via a direct set_selected_cell() call (already
    covered elsewhere, but never through the actual signal). Also brings
    the merged Detail dock's Placer page to front (2026-08-03 —
    gui/docks/detail_panel.py)."""
    real_main_window.config_tree_dock.cell_picked.emit("ldo_adj")

    assert real_main_window.placer_dock._selected_cell == "ldo_adj"
    assert real_main_window.placer_dock.cell_combo.currentText() == "ldo_adj"
    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.placer_dock


def test_placement_picked_loads_into_placer_form(real_main_window):
    """ConfigTreeDock -> PlacerDock wiring (placement_picked -> load_placement,
    see gui/docks/config_tree.py's placement_picked docstring) — clicking an
    already-saved placement leaf in the real Config tree must reach
    PlacerDock's form end-to-end, not just via a direct load_placement()
    call (already covered elsewhere, but never through the actual signal)."""
    entry = {"cluster": "spoke_1", "cell": "ldo_adj", "xy": [1.5, 2.5]}
    real_main_window.config_tree_dock.placement_picked.emit(entry)

    assert real_main_window.placer_dock.cluster_edit.currentText() == "spoke_1"
    assert real_main_window.placer_dock._selected_cell == "ldo_adj"
    assert real_main_window.placer_dock.x_edit.text() == "1.5"
    assert real_main_window.placer_dock.y_edit.text() == "2.5"


def test_profile_picked_fills_extract_form_and_opens_dialog(real_main_window, tmp_path):
    """ConfigTreeDock -> ExtractDock wiring (profile_picked -> pick_profile,
    see gui/docks/config_tree.py's profile_picked docstring / ExtractDock.
    pick_profile's docstring) — clicking an Extract-profile leaf in the real
    Config tree must reach ExtractDock's form end-to-end, not just via a
    direct pick_profile() call, and open the (non-modal) Extract dialog
    (2026-08-31, plan extract_dialog_and_hide_existing.md)."""
    extractor_file = tmp_path / "profiles.sexp"
    _write(extractor_file, {"extract_profiles": {"alpha_profile": {"params": {"ROLE": "+3V3"}}}})
    real_main_window.extract_dock.set_root_path(extractor_file)

    real_main_window.config_tree_dock.profile_picked.emit("alpha_profile")

    assert real_main_window.extract_dock.profile_key_edit.text() == "alpha_profile"
    assert real_main_window._dock_hub.extract_dialog.isVisible()


def test_points_saved_refreshes_config_tree_points(real_main_window, tmp_path):
    """PointsDock -> ConfigTreeDock wiring (saved -> refresh) — same
    real-widget-state assertion style as
    test_placer_saved_refreshes_config_tree_placements/test_extract_saved_
    refreshes_config_tree_cells above."""
    points_file = tmp_path / "points.sexp"
    _write(points_file)
    real_main_window.config_tree_dock.set_root_file(points_file)
    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0

    _write(points_file, {"points": {"origin": {"xy": [0, 0]}}})
    real_main_window.points_dock.saved.emit()

    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    points = root_item.child(0)
    assert points.text(0) == "Points"
    assert points.child(0).text(0) == "origin"


def test_add_point_requested_opens_blank_form_and_dialog(real_main_window, tmp_path):
    """ConfigTreeDock's "Add point..." context-menu action ->
    DockHub._start_new_point -> the fresh blank form inside the (non-modal)
    Points dialog (2026-09-01, plan plan_2026_09_01_points_dialog.md)."""
    points_file = tmp_path / "points.sexp"
    _write(points_file)
    real_main_window.points_dock.name_edit.setText("stale")

    real_main_window.config_tree_dock.add_point_requested.emit(points_file)

    assert real_main_window.points_dock.name_edit.text() == ""
    assert real_main_window._dock_hub.points_dialog.isVisible()


def test_rules_dock_picks_up_a_root_restored_before_wiring_existed(qapp, tmp_path):
    """Same startup-order bug/fix as test_root_metadata_dock_picks_up_a_
    root_restored_before_wiring_existed above — RuleDock is the SECOND
    listener on root_file_changed (its Cell/Point combos need the whole
    include graph, see gui/docks/rules.py's module docstring), so it needs
    the exact same explicit sync in DockHub._wire()."""
    root_file = tmp_path / "root.sexp"
    _write(root_file, {"cells": {"cap_pair": {}}})

    data = settings.load()
    data["last_root_file"] = str(root_file)
    settings.save(data)

    window = MainWindow(timeout_ms=10, verbose=False)
    try:
        assert window.rules_dock._root_path == root_file
        assert "cap_pair" in [window.rules_dock.spoke_cell_combo.itemText(i)
                              for i in range(window.rules_dock.spoke_cell_combo.count())]
    finally:
        window._timer.stop()
        window._selection_timer.stop()
        window._poll_worker.stop()


def test_chain_edit_requested_fills_chain_form_and_opens_dialog(real_main_window, tmp_path):
    """ConfigTreeDock -> ChainDock wiring (chain_edit_requested -> _start_edit_
    chain) — a double click on a chains: chain node must reach ChainDock's
    chain mode end-to-end, not just via a direct load_chain() call, and open
    the (non-modal) Chain dialog (2026-09-01, plan rules_to_chains)."""
    rules_file = tmp_path / "rules.sexp"
    _write(rules_file, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA",
         "spokes": [{"pad": "17", "cell": "cap_pair"}]},
    ]})
    real_main_window.config_tree_dock.set_root_file(rules_file)

    real_main_window.config_tree_dock.chain_edit_requested.emit(
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "cap_pair"}]})

    assert real_main_window.chain_dock.net_edit.currentText() == "+3V3"
    assert real_main_window.chain_dock.anchor_role_edit.currentText() == "FPGA"
    assert real_main_window.chain_dock._chain_entry == {
        "net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "cap_pair"}]}
    assert real_main_window._dock_hub.chain_dialog.isVisible()


def test_chain_saved_refreshes_config_tree_chains(real_main_window, tmp_path):
    """ChainDock -> ConfigTreeDock wiring (saved -> refresh) — same
    real-widget-state assertion style as test_points_saved_refreshes_
    config_tree_points above. The refreshed tree shows the Chains category
    -> anchor -> chain structure."""
    rules_file = tmp_path / "rules.sexp"
    _write(rules_file)
    real_main_window.config_tree_dock.set_root_file(rules_file)
    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0

    _write(rules_file, {"chains": [{"net": "+3V3", "anchor_role": "FPGA"}]})
    real_main_window.chain_dock.saved.emit()

    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    chains_cat = root_item.child(0)
    assert chains_cat.text(0) == "Chains"
    assert chains_cat.child(0).text(0) == "Anchor: FPGA"
    assert chains_cat.child(0).child(0).text(0) == "+3V3"


def test_add_chain_requested_opens_blank_chain_form_and_dialog(real_main_window, tmp_path):
    """ConfigTreeDock's "Add chain..." context-menu action -> ChainDock, same
    shape as DockHub._start_new_placement/_start_new_point (2026-09-01, plan
    rules_to_chains: "Add chain..." in the tree, "Add net..." in the Tools
    menu)."""
    rules_file = tmp_path / "rules.sexp"
    _write(rules_file)
    real_main_window.chain_dock.net_edit.setCurrentText("stale")

    real_main_window.config_tree_dock.add_chain_requested.emit(rules_file)

    assert real_main_window.chain_dock.net_edit.currentText() == ""
    assert real_main_window._dock_hub.chain_dialog.isVisible()


def test_tools_menu_new_extract_opens_dialog_and_prepares_fresh(real_main_window):
    """2026-09-01: the Tools menu's "New Extract..." action routes to
    DockHub.new_extract -> _start_new_extract -> the same plain fresh capture
    (prepare_new_extract) the Config tree context menu's "New Extract..."
    provides. With the section-specific "Add extract profile..." removed this
    is the single Extract entry point."""
    hub = real_main_window._dock_hub
    hub.extract_dock.name_edit.setText("stale_name")
    hub.extract_dock.profile_key_edit.setText("stale_key")
    hub.extract_dock.save_profile_checkbox.setChecked(True)

    real_main_window.new_extract_action.trigger()

    assert hub.extract_dialog.isVisible()
    assert hub.extract_dock.name_edit.text() == ""
    assert hub.extract_dock.profile_key_edit.text() == ""
    assert hub.extract_dock.save_profile_checkbox.isChecked() is False


def test_new_extract_requested_opens_dialog_and_prepares_fresh(real_main_window):
    """ConfigTreeDock's "New Extract..." (new_extract_requested, 2026-08-31
    plan extract_dialog_and_hide_existing.md) -> ExtractDock.prepare_new_extract
    + opening the (non-modal) dialog: a plain fresh capture."""
    hub = real_main_window._dock_hub
    hub.extract_dock.name_edit.setText("stale_name")
    hub.extract_dock.profile_key_edit.setText("stale_key")
    hub.extract_dock.save_profile_checkbox.setChecked(True)

    hub.config_tree_dock.new_extract_requested.emit()

    assert hub.extract_dialog.isVisible()
    assert hub.extract_dock.name_edit.text() == ""
    assert hub.extract_dock.profile_key_edit.text() == ""
    assert hub.extract_dock.save_profile_checkbox.isChecked() is False


def test_successful_extract_auto_closes_the_dialog(real_main_window):
    """2026-08-31 (plan extract_dialog_and_hide_existing.md, Denis: "после
    успешного Extract диалог должен сам закрываться") — DockHub wires
    extract_dock.saved -> extract_dialog.hide; saved fires in _finish_extract
    only on success, so the dialog hides right after a successful Extract."""
    hub = real_main_window._dock_hub
    hub._open_extract_dialog()
    assert hub.extract_dialog.isVisible()

    hub.extract_dock.saved.emit()

    assert hub.extract_dialog.isVisible() is False


def test_thermal_via_picked_fills_form_and_opens_dialog(real_main_window, tmp_path):
    """ConfigTreeDock -> ThermalViaArrayDock wiring (thermal_via_picked ->
    load_entry, see gui/docks/config_tree.py's thermal_via_picked docstring) —
    clicking a Thermal via array leaf in the real Config tree must reach the
    form end-to-end and open the (non-modal) Thermal via dialog (2026-09-01,
    plan plan_2026_09_01_thermal_via_dialog.md)."""
    tva_file = tmp_path / "tva.sexp"
    _write(tva_file, {"thermal_via_arrays": [
        {"name": "fpga_thermal", "pad": "1", "anchor_ref": "U3"}]})
    real_main_window.thermal_via_dock.set_root_path(tva_file)

    real_main_window.config_tree_dock.thermal_via_picked.emit(
        {"name": "fpga_thermal", "pad": "1", "anchor_ref": "U3"})

    assert real_main_window.thermal_via_dock.name_edit.text() == "fpga_thermal"
    assert real_main_window._dock_hub.thermal_via_dialog.isVisible()


def test_add_thermal_via_requested_opens_blank_form_and_dialog(real_main_window, tmp_path):
    """ConfigTreeDock's "Add thermal via pad..." context-menu action ->
    DockHub._start_new_thermal_via -> the fresh blank form inside the (non-
    modal) Thermal via dialog."""
    tva_file = tmp_path / "tva.sexp"
    _write(tva_file)
    real_main_window.thermal_via_dock.name_edit.setText("stale")

    real_main_window.config_tree_dock.add_thermal_via_requested.emit(tva_file)

    assert real_main_window.thermal_via_dock.name_edit.text() == ""
    assert real_main_window._dock_hub.thermal_via_dialog.isVisible()


def test_successful_save_auto_closes_the_thermal_via_dialog(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_thermal_via_dialog.md, Denis: dialog
    auto-hides only after a successful Save) — DockHub wires
    thermal_via_dock.saved -> thermal_via_dialog.hide; saved fires in _on_save
    only on success, so the dialog hides right after the entry is written."""
    hub = real_main_window._dock_hub
    hub._open_thermal_via_dialog()
    assert hub.thermal_via_dialog.isVisible()

    hub.thermal_via_dock.saved.emit()

    assert hub.thermal_via_dialog.isVisible() is False


def test_tools_menu_place_thermal_vias_opens_dialog_and_prepares_fresh(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_thermal_via_dialog.md): the Tools
    menu's "Place thermal vias..." action routes to DockHub.place_thermal_vias
    -> the same fresh blank form (new_thermal_via) the Config tree context
    menu's "Add thermal via pad..." provides."""
    hub = real_main_window._dock_hub
    hub.thermal_via_dock.name_edit.setText("stale_name")
    hub.thermal_via_dock.pad_edit.setText("9")

    real_main_window.place_thermal_vias_action.trigger()

    assert hub.thermal_via_dialog.isVisible()
    assert hub.thermal_via_dock.name_edit.text() == ""
    assert hub.thermal_via_dock.pad_edit.text() == ""


# ── Points dialog routes (2026-09-01, plan plan_2026_09_01_points_dialog.md) ─


def test_points_edit_requested_opens_dialog_with_entry_loaded(real_main_window, tmp_path):
    """ConfigTreeDock -> PointsDock wiring (points_edit_requested -> load_entry,
    see gui/docks/config_tree.py's points_edit_requested docstring / PointsDock.
    load_entry's docstring) — the DOUBLE-click route: the named point must reach
    PointsDock's form end-to-end and open the (non-modal) Points dialog."""
    points_file = tmp_path / "points.sexp"
    _write(points_file, {"points": {"origin": {"xy": [1.0, 2.0]}}})
    real_main_window.points_dock.set_root_path(points_file)

    real_main_window.config_tree_dock.points_edit_requested.emit("origin")

    assert real_main_window.points_dock.name_edit.text() == "origin"
    assert real_main_window.points_dock.x_edit.text() == "1.0"
    assert real_main_window._dock_hub.points_dialog.isVisible()


def test_successful_save_auto_closes_the_points_dialog(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_points_dialog.md, Denis: dialog
    auto-hides only after a successful Save) — DockHub wires
    points_dock.saved -> points_dialog.hide; saved fires in _on_save only on
    success, so the dialog hides right after the entry is written."""
    hub = real_main_window._dock_hub
    hub._open_points_dialog()
    assert hub.points_dialog.isVisible()

    hub.points_dock.saved.emit()

    assert hub.points_dialog.isVisible() is False


def test_tools_menu_add_point_opens_dialog_and_prepares_fresh(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_points_dialog.md): the Tools menu's
    "Add point..." action routes to DockHub.new_point -> the same fresh blank
    form (new_point) the Config tree context menu's "Add point..." provides."""
    hub = real_main_window._dock_hub
    hub.points_dock.name_edit.setText("stale_name")

    real_main_window.add_point_action.trigger()

    assert hub.points_dialog.isVisible()
    assert hub.points_dock.name_edit.text() == ""


def test_tools_menu_add_net_opens_chain_dialog_fresh(real_main_window):
    """2026-09-01 (plan rules_to_chains): the Tools menu's "Add net..." action
    routes to DockHub.add_chain -> the same fresh blank chain form
    (_start_new_chain) the Config tree context menu's "Add chain..." provides.
    The menu labels a chain by its NET identity (Denis's decision)."""
    hub = real_main_window._dock_hub
    hub.chain_dock.net_edit.setCurrentText("stale_net")

    real_main_window.add_chain_action.trigger()

    assert hub.chain_dialog.isVisible()
    assert hub.chain_dock.net_edit.currentText() == ""
    assert hub.chain_dock._stack.currentWidget() is hub.chain_dock._chain_page


def test_tools_menu_add_spoke_requires_a_selected_chain(real_main_window, tmp_path):
    """2026-09-01 (plan rules_to_chains): "Add spoke..." opens the Chain dialog
    in pad mode, appending to the chain currently selected in the Config tree.
    Without a selection it just logs a hint — never a crash."""
    hub = real_main_window._dock_hub
    hub.add_spoke()
    assert not hub.chain_dialog.isVisible()  # nothing selected -> no dialog

    # With a chains: chain selected, the dialog opens in pad mode.
    chain = {"net": "+3V3", "anchor_ref": "U1", "spokes": []}
    real_main_window.config_tree_dock.selected_chain = lambda: (None, chain)

    hub.add_spoke()

    assert hub.chain_dialog.isVisible()
    assert hub.chain_dock._stack.currentWidget() is hub.chain_dock._pad_page
    assert hub.chain_dock._chain_entry == chain
    assert hub.chain_dock._pad_index is None  # append


def test_tools_menu_delete_net_removes_selected_chain(real_main_window, tmp_path):
    """2026-09-01 (plan rules_to_chains): "Delete net..." deletes the chain
    currently selected in the Config tree via delete_entry (timestamped
    backup)."""
    rules_file = tmp_path / "rules.sexp"
    _write(rules_file, {"chains": [
        {"net": "+3V3", "anchor_ref": "U1", "spokes": []},
        {"net": "GND", "anchor_ref": "U1", "spokes": []},
    ]})
    hub = real_main_window._dock_hub
    hub.config_tree_dock.set_root_file(rules_file)
    hub.config_tree_dock.selected_chain = lambda: (rules_file, {"net": "+3V3", "anchor_ref": "U1", "spokes": []})

    real_main_window.delete_chain_action.trigger()

    data = sexp_to_dict(rules_file.read_text(encoding="utf-8"))
    assert [c["net"] for c in data["chains"]] == ["GND"]
    assert len(list(tmp_path.glob("rules.sexp.bak.*"))) == 1


def test_entity_edit_requested_opens_dialog_with_entry_loaded(real_main_window, tmp_path):
    """ConfigTreeDock -> ToolsDock wiring (entity_edit_requested ->
    tools_dock.load_entity + _open_tools_dialog, 2026-09-01, plan
    plan_2026_09_01_tools_dialog_and_entity_roles.md) — the DOUBLE-click
    route: the named Entity must reach ToolsDock's form end-to-end (record +
    Role choices from its cell) and open the (non-modal) "Edit template"
    dialog."""
    cells = tmp_path / "cells.sexp"
    _write(cells, {"cells": {"pi_filter": {
        "components": [{"role": "C_IN"}, {"role": "C_OUT"}],
        "vias": [], "tracks": [], "layer": "F.Cu"}}})
    root = tmp_path / "root.sexp"
    _write(root, {"entities": [{"name": "E1", "cell": "pi_filter",
                                "nets": {"C_IN": "+3V3"}}],
                  "include": ["cells.sexp"]})
    tools_dock = real_main_window._dock_hub.tools_dock
    tools_dock.set_root_path(root)

    real_main_window.config_tree_dock.entity_edit_requested.emit("E1")

    assert tools_dock.target_combo.currentText() == "E1"
    assert tools_dock.nets_table.to_dict() == {"C_IN": "+3V3"}
    roles = [tools_dock.nets_table.key_edit.itemText(i)
             for i in range(tools_dock.nets_table.key_edit.count())]
    assert roles == ["C_IN", "C_OUT"]
    assert real_main_window._dock_hub.tools_dialog.isVisible()


def test_successful_edit_auto_closes_the_tools_dialog(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_tools_dialog_and_entity_roles.md,
    Denis: dialog auto-hides after a successful edit, "как Points") — DockHub
    wires tools_dock.saved -> tools_dialog.hide; saved fires in _do_save only
    on success, so the dialog hides right after the row is written."""
    hub = real_main_window._dock_hub
    hub._open_tools_dialog()
    assert hub.tools_dialog.isVisible()

    hub.tools_dock.saved.emit()

    assert hub.tools_dialog.isVisible() is False


def test_tools_menu_edit_template_opens_dialog(real_main_window):
    """2026-09-01 (plan plan_2026_09_01_tools_dialog_and_entity_roles.md):
    the Tools menu's "Edit template..." action routes to DockHub.edit_template
    -> the (non-modal) "Edit template" dialog."""
    hub = real_main_window._dock_hub

    real_main_window.edit_template_action.trigger()

    assert hub.tools_dialog.isVisible()


def test_tools_menu_reead_selected_between_edit_and_view(real_main_window, monkeypatch):
    """2026-08-31 (plan reead_selected_dialog.md): a Tools menu sits between
    Edit and View, and its "Re-read selected..." action routes to
    ExtractDock.re_read_selected via DockHub."""
    labels = [a.text() for a in real_main_window.menuBar().actions()]
    assert "Edit" in labels and "Tools" in labels and "View" in labels
    assert labels.index("Edit") < labels.index("Tools") < labels.index("View")

    called = []
    monkeypatch.setattr(real_main_window._dock_hub.extract_dock, "re_read_selected",
                        lambda: called.append(True))
    real_main_window.reead_selected_action.trigger()
    assert called == [True]


# ── Tools -> Extract tree... (2026-09-01, plan extract_selection_as_tree.md) ─

def _selected_tree(ref, cluster, sheet, nets):
    """A Selected footprint carrying pad nets — the inter-cluster-net source."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[sheet],
                    nets=nets, fp=object())


def test_tools_menu_extract_tree_between_edit_and_view(real_main_window, monkeypatch):
    """The Tools menu has "Extract tree..." next to Re-read/New Extract and
    routes to DockHub.extract_tree_from_selection."""
    labels = [a.text() for a in real_main_window.menuBar().actions()]
    assert "Edit" in labels and "Tools" in labels and "View" in labels
    assert labels.index("Edit") < labels.index("Tools") < labels.index("View")

    called = []
    monkeypatch.setattr(real_main_window._dock_hub, "extract_tree_from_selection",
                        lambda: called.append(True))
    real_main_window.extract_tree_action.trigger()
    assert called == [True]


def test_extract_tree_no_fully_selected_cluster_shows_message(real_main_window,
                                                              tmp_path, monkeypatch):
    """No fully-selected cluster -> a warning is shown and the dialog is NOT
    opened (same guard as Re-read)."""
    root = tmp_path / "root.sexp"
    _write(root)
    real_main_window.root_metadata_dock.set_root_file(root)
    # Replace the live BoardConnection with a fake (its snapshot is a
    # read-only property on the real one — this flow only reads it).
    real_main_window.connection = SimpleNamespace(
        board=SimpleNamespace(adapter=object()), snapshot=[], long_op_active=False)
    hub = real_main_window._dock_hub
    hub.extract_dock._selected_footprints = []
    hub.extract_dock._raw_items = []

    warnings = []
    monkeypatch.setattr(dock_hub_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]))
    constructed = []
    monkeypatch.setattr(tfsd_mod, "TreeFromSelectionDialog",
                        lambda *a, **k: constructed.append(True) or object())

    hub.extract_tree_from_selection()

    assert any("No fully selected Cluster" in w for w in warnings)
    assert constructed == []


def test_extract_tree_happy_path_saves_tree_and_nets(real_main_window,
                                                     tmp_path, monkeypatch):
    """The full flow: after OK the root config gains a trees: entry (name +
    role anchor + placement nodes with xy) and the checked inter-cluster net
    lands in net_traces:; TreesDock shows the new tree and graph_changed is
    emitted. Backup + round-trip link_trees must not crash."""
    root = tmp_path / "root.sexp"
    _write(root, {
        "entities": [
            {"name": "CH1_PIF_AVDD", "cell": "dac_pif_avdd",
             "cluster": "PIF_AVDD", "sheet": "Channel_1"},
            {"name": "CH1_PIF_CLKVDD", "cell": "dac_pif_clkvdd",
             "cluster": "PIF_CLKVDD", "sheet": "Channel_1"},
        ],
        "cells": {
            "dac_pif_avdd": {"components": [{"role": "DAC"}]},
            "dac_pif_clkvdd": {"components": [{"role": "DAC"}]},
        },
    })
    real_main_window.root_metadata_dock.set_root_file(root)
    hub = real_main_window._dock_hub
    sel1 = _selected_tree("R1", "PIF_AVDD", "Channel_1", {"1": "SHARED"})
    sel2 = _selected_tree("R2", "PIF_CLKVDD", "Channel_1", {"1": "SHARED"})
    # Replace the live BoardConnection with a fake (snapshot is a read-only
    # property on the real one — this flow only reads it).
    real_main_window.connection = SimpleNamespace(
        board=SimpleNamespace(adapter=object()),
        snapshot=[sel1, sel2], long_op_active=False)
    hub.extract_dock._selected_footprints = [sel1, sel2]
    hub.extract_dock._raw_items = [
        Track(uuid="t1", start=Vector2.from_xy(0, 0), end=Vector2.from_xy(1, 1),
              net_name="SHARED", width_mm=0.25, layer=None),
    ]

    # Auto-accepting dialog returning both clusters + the shared net.
    class _FakeDialog:
        def __init__(self, clusters, inter_nets, existing_names, **kwargs):
            self._clusters = clusters

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_clusters(self):
            return self._clusters

        def selected_nets(self):
            from gui.docks.tree_from_selection import InterClusterNet
            return [InterClusterNet(net="SHARED", track_count=1, via_count=0)]

        def tree_name(self):
            return "power_tree"

        def build_anchor(self):
            return TreeAnchor(role="DAC", anchor_sheet="Channel_1",
                              anchor_cluster="PIF_AVDD")

    monkeypatch.setattr(tfsd_mod, "TreeFromSelectionDialog", _FakeDialog)
    # Live positions (mocked) — Entity positions and the anchor base.
    monkeypatch.setattr(
        tfs_mod, "resolve_entity_live_position_mm",
        lambda adapter, cfg, entity, sheet_names, label=None: (10.0, 20.0))
    monkeypatch.setattr(
        tfs_mod, "resolve_role_anchor_base_mm",
        lambda adapter, cfg, anchor, sheet_names, label=None: (5.0, 10.0))
    # Net capture: return a real NetTrace so write_net_trace persists it.
    def _fake_extract_net_trace(adapter, *, net, anchor_role, **kwargs):
        return NetTrace(net=net, anchor_role=anchor_role)

    monkeypatch.setattr(net_trace_extract_mod, "extract_net_trace",
                        _fake_extract_net_trace)
    monkeypatch.setattr(dock_hub_mod.QMessageBox, "warning",
                        lambda *a, **k: None)
    monkeypatch.setattr(dock_hub_mod.QMessageBox, "information",
                        lambda *a, **k: None)

    graph_changed = []
    real_main_window.config_tree_dock.graph_changed.connect(
        lambda: graph_changed.append(True))

    hub.extract_tree_from_selection()

    # The write lands in the working set (staged model, 2026-09-01) — commit
    # it to disk before reading the file back.
    WORKING_SET.flush(root)

    # trees: entry + net_traces: entry in the root file.
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    trees = data.get("trees") or []
    assert any(t["name"] == "power_tree" for t in trees)
    tree = next(t for t in trees if t["name"] == "power_tree")
    assert tree["anchor"] == {"role": "DAC", "sheet": "Channel_1",
                              "cluster": "PIF_AVDD"}
    # Phase D: the checked inter-cluster net "SHARED" becomes a net_trace node.
    assert [n["ref"] for n in tree["nodes"]] == ["CH1_PIF_AVDD", "CH1_PIF_CLKVDD", "SHARED"]
    assert [n["kind"] for n in tree["nodes"]] == ["placement", "placement", "net_trace"]
    # Autopositioning: entity (10,20) - anchor base (5,10) = (5,10).
    assert tree["nodes"][0]["xy"] == [5.0, 10.0]
    nets = data.get("net_traces") or []
    assert any(n["net"] == "SHARED" for n in nets)

    # Backup + round-trip link_trees did not crash; TreesDock shows the tab.
    assert list(tmp_path.glob("root.sexp.bak*")), "backup file must exist"
    assert any(t.name == "power_tree" for t in hub.trees_dock._trees)
    assert graph_changed


def test_tree_net_trace_nets_collects_net_trace_refs():
    """Phase E: _tree_net_trace_nets returns the nets of a tree's net_trace
    nodes (used by the delete-tree cascade to find orphaned net_traces)."""
    from gui.docks.trees_dock import _tree_net_trace_nets
    from kicadstamp.trees import Tree, TreeAnchor, TreeNode

    tree = Tree(name="t", anchor=TreeAnchor(role="DAC"),
                nodes=[
                    TreeNode(ref="E1", kind="placement", xy=None, polar=None,
                             rotation=0.0, name=None, group=None),
                    TreeNode(ref="SHARED", kind="net_trace", xy=None, polar=None,
                             rotation=0.0, name=None, group=None,
                             children=[
                                 TreeNode(ref="AVDD", kind="net_trace", xy=None,
                                          polar=None, rotation=0.0, name=None, group=None),
                             ]),
                ])
    assert _tree_net_trace_nets(tree) == {"SHARED", "AVDD"}


def test_file_selected_no_longer_switches_detail_page(real_main_window, tmp_path):
    """A plain file/category click (file_selected fires with no matching leaf
    signal) NO LONGER switches the Detail dock — the old file_selected ->
    show_root() fallback was removed together with the Project tab
    (2026-09-01, plan project_settings_dialogs; RootMetadataDock now lives in
    the non-modal ProjectDialog). The dock stays on whatever page it was on."""
    real_main_window._dock_hub.detail_dock.show_placer()
    target_file = tmp_path / "power.sexp"
    _write(target_file)

    real_main_window.config_tree_dock.file_selected.emit(target_file)

    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.placer_dock


def test_cell_picked_still_switches_to_placer(real_main_window):
    """A Cell-leaf click fires file_selected THEN cell_picked in that order
    (see config_tree.py's _on_clicked) — the specific signal routes into
    Placer (2026-09-01: file_selected no longer first jumps to the removed
    Root page, but cell_picked's show_placer must still win)."""
    real_main_window.config_tree_dock.file_selected.emit(None)
    real_main_window.config_tree_dock.cell_picked.emit("ldo_adj")

    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.placer_dock


def test_placer_saved_refreshes_config_tree_placements(real_main_window, tmp_path):
    """PlacerDock -> ConfigTreeDock wiring (saved -> refresh, see
    gui/docks/config_tree.py's refresh docstring) — a successful Save must
    reach ConfigTreeDock's Clone placements category end-to-end, not just
    via a direct call (the tree would otherwise go stale after Save
    without a file reassign).

    Asserts on real widget state (the tree picking up a change made on disk
    after the fact) rather than monkeypatching refresh() — a PyQt signal
    connection captures the bound method at connect() time, so patching the
    instance attribute afterwards would not be intercepted (same caveat as
    test_fieldstool_components_changed_refreshes_tree above)."""
    placer_file = tmp_path / "placer.sexp"
    _write(placer_file)
    real_main_window.config_tree_dock.set_root_file(placer_file)
    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0

    _write(placer_file, {"clone_placements": [
        {"name": "spoke_1", "cell": "ldo_adj", "xy": [0, 0]},
    ]})
    real_main_window.placer_dock.saved.emit()

    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    placements = root_item.child(0)
    assert placements.text(0) == "Clone placements"
    assert placements.child(0).text(0) == "spoke_1"


def test_extract_saved_refreshes_config_tree_cells(real_main_window, tmp_path):
    """ExtractDock -> ConfigTreeDock wiring (saved -> refresh) — FIXED
    2026-08-04 (Denis live: "создал новый экстракт и cell, список в
    конфиге не обновляется"): ExtractDock never had a saved signal at all,
    unlike PlacerDock/ThermalViaDock, so a freshly extracted cell never
    showed up in the Config tree without an unrelated action happening to
    trigger refresh() first. Same real-widget-state assertion style as
    test_placer_saved_refreshes_config_tree_placements above."""
    cells_file = tmp_path / "cells.sexp"
    _write(cells_file)
    real_main_window.config_tree_dock.set_root_file(cells_file)
    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0

    _write(cells_file, {"cells": {"ldo_adj_2vp": {}}})
    real_main_window.extract_dock.saved.emit()

    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    cells = root_item.child(0)
    assert cells.text(0) == "Cells"
    assert cells.child(0).text(0) == "ldo_adj_2vp"


def test_extract_save_profile_writes_root_entry_and_tree_shows_leaf(
        real_main_window, tmp_path, monkeypatch):
    """2026-09-01 (gap close): a successful Extract with "Also save as
    extract_profile" writes the entry into the project root AND the Config
    tree shows the new extract_profiles leaf (saved -> refresh) — the full
    "profile appeared in the config" path, not just the writer in isolation
    (the write itself is pinned in test_extract_dock.py; this pins the
    section rendering after a save_profile Extract)."""
    import gui.docks.extract as extract_mod
    from tests.gui.test_extract_dock import (FakeBoard, FakeSelected,
                                             _fake_extract, _fake_fp, _load)

    root = tmp_path / "root.sexp"
    _write(root)
    hub = real_main_window._dock_hub
    hub.config_tree_dock.set_root_file(root)
    dock = hub.extract_dock
    dock.set_root_path(root)
    dock._connection.board = FakeBoard()

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)

    dock.set_board_selection([_fake_fp("C1")],
                             [FakeSelected("C1", "SOME_ROLE", "PIF_P5V", {"1": "+5V"})])
    dock.name_edit.setText("pif_p5v")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("pif_p5v_profile")

    dock._do_extract()

    profile = _load(root)["extract_profiles"]["pif_p5v_profile"]
    assert profile["name"] == "pif_p5v"

    root_item = hub.config_tree_dock.tree.topLevelItem(0)
    profiles = next((root_item.child(i) for i in range(root_item.childCount())
                     if root_item.child(i).text(0) == "Extract profiles"), None)
    assert profiles is not None
    assert profiles.childCount() == 1
    assert profiles.child(0).text(0) == "pif_p5v_profile"


# ── 3.2: docks use the injected BoardConnection, not main_window.connection ──

def test_extract_dock_uses_injected_connection_when_given(main_window):
    injected = SimpleNamespace()
    dock = ExtractDock(main_window, connection=injected)
    assert dock._connection is injected


def test_extract_dock_falls_back_to_main_window_connection(main_window):
    dock = ExtractDock(main_window)
    assert dock._connection is main_window.connection


def test_extract_dock_feeds_injected_adapter_into_extraction(main_window, tmp_path, monkeypatch):
    """Behavioral proof the injected connection is what extraction runs on —
    the adapter reaching extract_template_from_selection comes from the
    injected board, not from main_window.connection (which stays board-less)."""
    import gui.docks.extract as extract_mod

    cells_file = tmp_path / "cells.sexp"
    _write(cells_file)

    captured = {}

    def _fake_extract(adapter, name, params=None, items=None,
                      net_template_role=None, annotations=None, **kwargs):
        captured["adapter"] = adapter
        captured["items"] = items
        # Keyed by cell name like the real extractor — the s-expr writer
        # validates the cell record shape, so a bare {"ref": "C1"} (tolerated
        # by YAML) would make dict_to_sexp fatal on a str cell body.
        return {name: {"components": [], "vias": [], "tracks": [], "layer": "F.Cu"}}

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)

    adapter = object()
    injected = SimpleNamespace(board=SimpleNamespace(adapter=adapter))
    dock = ExtractDock(main_window, connection=injected)
    dock.set_root_path(cells_file)
    dock.name_edit.setText("c1")
    dock._raw_items = ["item1"]

    # Full success-path extract: synchronous core (the async _on_extract()
    # path would not have written captured[] by the asserts below).
    dock._do_extract()

    assert captured["adapter"] is adapter
    assert captured["items"] == ["item1"]


def test_tree_dock_uses_injected_connection_for_board_highlight(main_window):
    """Clicking a leaf in live mode highlights the selection through the
    INJECTED connection's board.adapter — main_window.connection here has no
    board at all, so any fallback to it would silently no-op."""
    selected = []

    class _FakeAdapter:
        def select_items(self, footprints):
            selected.append(footprints)

    injected = SimpleNamespace(board=SimpleNamespace(adapter=_FakeAdapter()), long_op_active=False)
    dock = RoleClusterTreeDock(main_window, connection=injected)
    fp = object()
    dock.set_footprints([_FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])
    dock._selected[0].fp = fp

    model = dock.tree.model()
    leaf = _find_item(model, "C1")
    dock._on_clicked(model.indexFromItem(leaf))

    assert selected == [[fp]]


# ── 3.1/3.2: fieldstool access goes through FieldsToolDock's delegates ──────

def test_fieldstool_components_changed_refreshes_tree(real_main_window):
    """The Fieldstool dock's components_changed signal is wired to
    tree_dock.refresh_schematic_view in MainWindow — proven behaviorally
    (attribute replacement can't intercept a PyQt signal, which captures the
    bound method at connect() time): swap the fieldstool window's component
    list, emit the signal, and assert the tree rebuilt with the new data."""
    dock = real_main_window.tree_dock
    window = real_main_window.fieldstool_dock.window
    window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    # "Not yet applied" mode filters by pending_refs (2026-08-03) — needs a
    # live snapshot value that actually disagrees with the schematic for R1
    # to show up at all.
    window.set_live_snapshot([Selected(ref="R1", role="R_B", cluster="Cl_A",
                                       sheet=[], nets={}, fp=None)])
    dock.mode_checkbox.setChecked(True)  # schematic mode -> _rebuild shows R1
    assert _find_item(dock.tree.model(), "R1") is not None

    window._components = [
        SchematicComponent("R2", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    window.set_live_snapshot([Selected(ref="R2", role="R_B", cluster="Cl_A",
                                       sheet=[], nets={}, fp=None)])
    real_main_window.fieldstool_dock.components_changed.emit()

    assert _find_item(dock.tree.model(), "R2") is not None
    assert _find_item(dock.tree.model(), "R1") is None


def test_fieldstool_pick_delegates_reach_the_window(real_main_window):
    leaf_picked = Mock()
    group_picked = Mock()
    real_main_window.fieldstool_dock.window._on_tree_leaf_picked = leaf_picked
    real_main_window.fieldstool_dock.window._on_group_picked = group_picked

    real_main_window.fieldstool_dock.pick_leaf(["R1"])
    leaf_picked.assert_called_once_with(["R1"])

    real_main_window.fieldstool_dock.pick_group("Cluster", "Channel_1/PI_FILTER", ["R1"])
    group_picked.assert_called_once_with("Cluster", "Channel_1/PI_FILTER", ["R1"])


# ── 3.3: DockHub controller owns docks + layout + wiring ──────────────────

def _teardown_hub(hub):
    """A DockHub constructed on the bare `main_window` fixture embeds a real
    fieldstool MainWindow and a LogDock (root-logger handler) that would
    otherwise leak across tests. The log_file: FileHandler (2026-08-06,
    see _on_root_file_changed_for_logging) is the same kind of leak, PLUS
    it holds an open file handle into a tmp_path a later test/pytest
    teardown may need to delete — closing it here matters on Windows,
    where an open handle blocks the delete."""
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def test_main_window_exposes_all_docks_through_the_hub(real_main_window):
    """MainWindow owns a DockHub and re-exposes each dock as a thin
    forwarding property — the public surface (tests, RoleClusterTreeDock's
    lazy fieldstool lookup) keeps working while the hub owns the docks."""
    hub = real_main_window._dock_hub
    assert isinstance(hub, DockHub)

    assert real_main_window.tree_dock is hub.tree_dock
    assert real_main_window.config_tree_dock is hub.config_tree_dock
    assert real_main_window.fieldstool_dock is hub.fieldstool_dock
    assert real_main_window.extract_dock is hub.extract_dock
    assert real_main_window.placer_dock is hub.placer_dock
    assert real_main_window.log_dock is hub.log_dock


def test_dock_hub_constructs_all_docks(main_window, tmp_path):
    """A standalone DockHub builds every dock on any QMainWindow — the
    composition root works without a real MainWindow too. (file_selected no
    longer retargets the entity docks — see
    test_config_tree_file_selected_does_not_retarget_entity_docks.)"""
    target_file = tmp_path / "power.sexp"
    _write(target_file)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock is not None
        assert hub.config_tree_dock is not None
        assert hub.fieldstool_dock is not None
        assert hub.extract_dock is not None
        assert hub.placer_dock is not None
        assert hub.root_metadata_dock is not None
        assert hub.points_dock is not None
        assert hub.rules_dock is not None
        assert hub.log_dock is not None

        hub.config_tree_dock.file_selected.emit(target_file)
        assert hub.extract_dock._target_path is None
        assert hub.placer_dock._cells_path is None
        assert hub.points_dock._path is None
        assert hub.rules_dock._path is None
    finally:
        _teardown_hub(hub)


def test_dock_hub_wires_root_changed_to_config_tree_dock(main_window, tmp_path):
    """Root ownership moved to RootMetadataDock 2026-08-11 (was
    ConfigTreeDock's — see gui/docks/root_metadata.py's module docstring):
    a plain tree click (file_selected) must NOT retarget the Project
    panel's own root (Denis, 2026-08-05, original reasoning this mirrors:
    "root-панель должна... независимо от того, выбран узел root или
    нет"); setting the root via RootMetadataDock.set_root_file() must
    reach ConfigTreeDock in the OTHER direction now — it rebuilds its tree
    from whatever root_changed carries."""
    included_file = tmp_path / "included.sexp"
    _write(included_file)
    root_file = tmp_path / "root.sexp"
    _write(root_file)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        hub.config_tree_dock.file_selected.emit(included_file)
        assert hub.root_metadata_dock._path is None

        hub.root_metadata_dock.set_root_file(root_file)
        assert hub.config_tree_dock._root_path == root_file
    finally:
        _teardown_hub(hub)


def test_dock_hub_wires_root_changed_to_rules_dock(main_window, tmp_path):
    """RuleDock is a listener on root_changed (RootMetadataDock's, moved
    here 2026-08-11 from ConfigTreeDock's old root_file_changed) — its
    Cell/Point combos need the whole include graph starting from the
    project's root."""
    root_file = tmp_path / "root.sexp"
    _write(root_file, {"cells": {"cap_pair": {}}})

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.rules_dock._root_path is None

        hub.root_metadata_dock.root_changed.emit(root_file)

        assert hub.rules_dock._root_path == root_file
        assert "cap_pair" in [hub.rules_dock.spoke_cell_combo.itemText(i)
                              for i in range(hub.rules_dock.spoke_cell_combo.count())]
    finally:
        _teardown_hub(hub)


def test_dock_hub_wires_root_changed_to_points_dock(main_window, tmp_path):
    """root_changed reaches PointsDock like every other dock — and since the
    file pickers were removed (2026-08-21) its write target IS the root."""
    sub_file = tmp_path / "sub.sexp"
    _write(sub_file, {"points": {}})
    root_file = tmp_path / "root.sexp"
    _write(root_file, {"include": ["sub.sexp"]})

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.points_dock._root_path is None

        hub.root_metadata_dock.root_changed.emit(root_file)

        assert hub.points_dock._root_path == root_file
        assert hub.points_dock._path == root_file
    finally:
        _teardown_hub(hub)


# ── GUI robustness: a BROKEN root config must never crash the window ────────

def _write_broken_root(tmp_path):
    """A root config that parses but FAILS semantically at dock refresh: a
    missing schematic_dir makes RulesDock._refresh_sheet_names ->
    collect_all_sheet_names -> build_sheet_name_map raise ValidationError."""
    root_file = tmp_path / "root.sexp"
    _write(root_file, {"schematic_dir": "no_such_schematic_dir"})
    return root_file


def test_dock_hub_starts_even_with_a_broken_restored_root(main_window, tmp_path, caplog):
    """GUI must ALWAYS start (task 2026-08-30): a restored last_root_file that
    is broken (missing schematic_dir) used to crash DockHub construction via
    the un-guarded initial sync in _wire(). Now every root notification runs
    through _safe_call, so the ValidationError is LOGGED and the window stays
    open with the root path set."""
    root_file = _write_broken_root(tmp_path)
    _seed_last_root(root_file)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        # construction succeeded — the failure was logged, never raised
        assert any("failed on the current root config" in r.message
                   for r in caplog.records)
    finally:
        _teardown_hub(hub)


def test_dock_hub_manual_broken_root_change_is_logged_not_raised(main_window, tmp_path, caplog):
    """The same guard covers a MANUAL root change (Open/Recent) in an already
    running GUI: emitting root_changed with a broken config must not raise."""
    root_file = _write_broken_root(tmp_path)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        hub.root_metadata_dock.root_changed.emit(root_file)
        assert any("failed on the current root config" in r.message
                   for r in caplog.records)
    finally:
        _teardown_hub(hub)


def test_dock_hub_attaches_a_file_handler_from_the_root_configs_log_file(main_window, tmp_path):
    """2026-08-06, found live — Denis had log_file: already set in his
    root.yaml, assumed (reasonably) it already covered GUI runs too, but
    the GUI's own setup_logging() call (kicadstamp_gui.py) never passed a
    log_file at all — only kicadstamp_cli.py's `apply` command honored it.
    Reused cli_common.peek_log_file() so the same log_file: now covers the
    GUI as well."""
    root_file = tmp_path / "root.sexp"
    # peek_log_file resolves log_file: relative to the CONFIG file's own
    # directory — a plain relative path is enough here.
    _write(root_file, {"log_file": "logs/run.log"})

    root_logger = logging.getLogger()
    original_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)  # ambient level in tests may filter INFO before it reaches handlers
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub._log_file_handler is None

        hub.root_metadata_dock.root_changed.emit(root_file)

        assert hub._log_file_handler is not None
        assert Path(hub._log_file_handler.baseFilename) == (tmp_path / "logs" / "run.log").resolve()

        logging.getLogger("kicadstamp.gui_test.log_file").info("hello from a GUI test")
        hub._log_file_handler.flush()
        assert "hello from a GUI test" in (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    finally:
        _teardown_hub(hub)
        root_logger.setLevel(original_level)


def test_dock_hub_swaps_the_file_handler_when_the_root_file_changes(main_window, tmp_path):
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    _write(first_dir / "root.sexp", {"log_file": "logs/first.log"})
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    _write(second_dir / "root.sexp", {"log_file": "logs/second.log"})

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        hub.root_metadata_dock.root_changed.emit(first_dir / "root.sexp")
        first_handler = hub._log_file_handler
        assert Path(first_handler.baseFilename) == (first_dir / "logs" / "first.log").resolve()

        hub.root_metadata_dock.root_changed.emit(second_dir / "root.sexp")

        assert hub._log_file_handler is not first_handler
        assert first_handler not in logging.getLogger().handlers  # old one detached, not leaked
        assert Path(hub._log_file_handler.baseFilename) == (second_dir / "logs" / "second.log").resolve()
    finally:
        _teardown_hub(hub)


def test_dock_hub_has_no_file_handler_when_root_config_has_no_log_file(main_window, tmp_path):
    root_file = tmp_path / "root.sexp"
    _write(root_file, {})

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        hub.root_metadata_dock.root_changed.emit(root_file)
        assert hub._log_file_handler is None
    finally:
        _teardown_hub(hub)


def test_dock_hub_file_handler_attaches_to_the_queue_listener_when_one_is_running(
        qapp, main_window, monkeypatch, tmp_path):
    """The queue-based logging rework (2026-08-15,
    plan_2026_08_15_queue_based_logging.md): when setup_logging() has
    started a QueueListener, DockHub's root-config log_file: FileHandler
    must attach to THAT listener (its single thread formats/writes
    records) instead of directly to the root logger — otherwise this
    handler would stay on the synchronous path and keep the whole
    "logging blocks the calling thread" bug class open. With no listener
    configured (the normal GUI-test environment) DockHub falls back to the
    old direct root attachment — that path is covered by the three tests
    above."""
    import queue as queue_module
    from logging.handlers import QueueListener

    from gui import dock_hub as dock_hub_mod

    root_file = tmp_path / "root.sexp"
    _write(root_file, {"log_file": "logs/run.log"})

    root = logging.getLogger()
    original_level = root.level

    # A real, started listener, constructed by hand (setup_logging() itself
    # is never called in GUI tests — see plan). DockHub's log_file: handler
    # is then attached to it via the monkeypatched get_log_listener().
    some_handler = logging.StreamHandler()
    listener = QueueListener(queue_module.Queue(), some_handler)
    listener.start()
    monkeypatch.setattr(dock_hub_mod, "get_log_listener", lambda: listener)

    hub = None
    try:
        root.setLevel(logging.DEBUG)
        hub = DockHub(main_window, connection=main_window.connection, verbose=False)

        hub.root_metadata_dock.root_changed.emit(root_file)

        # the handler went to the listener, NOT to the root logger
        assert hub._log_file_handler is not None
        assert hub._log_file_handler in listener.handlers
        assert hub._log_file_handler not in root.handlers
    finally:
        if hub is not None:
            _teardown_hub(hub)
        listener.stop()
        root.setLevel(original_level)


def test_dock_hub_injects_connection_into_connection_docks(main_window):
    """The connection passed to DockHub reaches the three docks that consume
    it (RoleClusterTreeDock, ExtractDock, and — Phase 5.1 — the embedded
    fieldstool window) — never main_window.connection."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock._connection is main_window.connection
        assert hub.extract_dock._connection is main_window.connection
        assert hub.fieldstool_dock.window.connection is main_window.connection
    finally:
        _teardown_hub(hub)


def test_dock_hub_omits_board_written_hook_on_a_plain_main_window(main_window):
    """DockHub works on any QMainWindow, not just the real one (see
    test_dock_hub_constructs_all_docks_and_wires_file_selected above) — a
    fake main_window has no request_refresh, so the hook must fall back to
    None rather than raising AttributeError."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock.on_board_written is None
        assert hub.fieldstool_dock.window.on_board_written is None
    finally:
        _teardown_hub(hub)


def test_dock_hub_wires_board_written_hook_to_request_refresh(real_main_window):
    """2026-08-03 fix: Stage/Clear all write straight to the live board, but
    the automatic poll tick never refreshes on its own once already
    connected — without this wiring, Pending changes never picks up the
    write until the user notices and clicks Refresh themselves."""
    hub = real_main_window._dock_hub
    assert hub.tree_dock.on_board_written == real_main_window.request_refresh
    assert hub.fieldstool_dock.window.on_board_written == real_main_window.request_refresh


def test_dock_hub_restore_tree_mode_after_construction(real_main_window):
    """restore_tree_mode() is deliberately NOT part of __init__ (the tree
    dock's lazy fieldstool lookup needs main_window.fieldstool_dock
    resolvable, which only happens once MainWindow bound its hub) —
    MainWindow calls it right after construction."""
    hub = real_main_window._dock_hub
    assert not hub.tree_dock.mode_checkbox.isChecked()  # empty isolated settings

    data = settings.load()
    data["tree_schematic_mode"] = True
    settings.save(data)

    hub.restore_tree_mode()
    assert hub.tree_dock.mode_checkbox.isChecked()
    assert settings.load()["tree_schematic_mode"] is True


def test_dock_hub_delegates_route_to_the_right_docks(real_main_window, monkeypatch):
    """The poll/timer delegates MainWindow drives go through the hub and hit
    exactly the right dock — this is where that coordination grows."""
    hub = real_main_window._dock_hub

    pushed = {}
    monkeypatch.setattr(hub.tree_dock, "set_footprints",
                        lambda s: pushed.setdefault("tree", []).append(s))
    monkeypatch.setattr(hub.placer_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("roles", []).append(s))
    monkeypatch.setattr(hub.placer_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("nets", []).append(b))
    monkeypatch.setattr(hub.thermal_via_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("thermal_roles", []).append(s))
    monkeypatch.setattr(hub.thermal_via_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("thermal_nets", []).append(b))
    # No separate coordinate_dock since 2026-08-12 (Group 1) — coordinate
    # mode lives inside the merged placer_dock, whose refresh_known_roles is
    # already captured above as pushed["roles"].
    monkeypatch.setattr(hub.points_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("points_roles", []).append(s))
    monkeypatch.setattr(hub.rules_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("rules_roles", []).append(s))
    monkeypatch.setattr(hub.rules_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("rules_nets", []).append(b))
    monkeypatch.setattr(hub.cells_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("cells_roles", []).append(s))
    # net_trace_dock (2026-08-21, plan net_trace_dock) — net picker from the
    # whole board's copper + anchor roles/clusters, fed on the same tick.
    monkeypatch.setattr(hub.net_trace_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("net_trace_roles", []).append(s))
    monkeypatch.setattr(hub.net_trace_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("net_trace_nets", []).append(b))
    # tools_dock (2026-09-01, plan plan_2026_09_01_tools_dialog_and_entity_
    # roles.md) — the "Edit template" dialog's Net value combos are fed from
    # the live board on the same tick (the ToolsDock never got the poll
    # before — the regression fix).
    monkeypatch.setattr(hub.tools_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("tools_nets", []).append(b))

    board, snapshot = object(), object()
    hub.push_snapshot(snapshot, board)
    assert pushed["tree"] == [snapshot]
    assert pushed["roles"] == [snapshot]
    assert pushed["nets"] == [board]
    assert pushed["thermal_roles"] == [snapshot]
    assert pushed["thermal_nets"] == [board]
    assert pushed["points_roles"] == [snapshot]
    assert pushed["rules_roles"] == [snapshot]
    assert pushed["rules_nets"] == [board]
    assert pushed["cells_roles"] == [snapshot]
    assert pushed["net_trace_roles"] == [snapshot]
    assert pushed["net_trace_nets"] == [board]
    assert pushed["tools_nets"] == [board]

    cleared = []
    monkeypatch.setattr(hub.tree_dock, "set_footprints", lambda s: cleared.append(s))
    hub.clear_components()
    assert cleared == [[]]

    highlighted = []
    monkeypatch.setattr(hub.tree_dock, "highlight_board_selection",
                        lambda refs: highlighted.append(refs))
    hub.highlight_selection({"R1"})
    assert highlighted == [{"R1"}]

    board_selected = []
    placer_selected = []
    monkeypatch.setattr(hub.extract_dock, "set_board_selection",
                        lambda items, sel: board_selected.append((items, sel)))
    # 2026-08-31 (plan placer_source_tab_gaps P.1): the selection-watch tick
    # now also reaches PlacerDock (its Cell-mode Cluster auto-fill).
    monkeypatch.setattr(hub.placer_dock, "set_board_selection",
                        lambda items, sel: placer_selected.append((items, sel)))
    hub.set_board_selection(["raw"], ["sel"])
    assert board_selected == [(["raw"], ["sel"])]
    assert placer_selected == [(["raw"], ["sel"])]

    shown, raised = [], []
    monkeypatch.setattr(hub.fieldstool_dock, "setVisible", lambda v: shown.append(v))
    monkeypatch.setattr(hub.fieldstool_dock, "raise_", lambda: raised.append(True))
    hub.open_fieldstool()
    assert shown == [True]
    assert raised == [True]


# ── graph_changed broadcast (2026-08-15, plan graph_changed_broadcast) ─────

def _seed_last_root(root: Path) -> None:
    """Point the per-test-isolated gui_state.json (see tests/gui/conftest.py's
    isolated_settings) at `root` so RootMetadataDock._restore_last_root()
    picks the project up during DockHub construction."""
    data = settings.load()
    data["last_root_file"] = str(root)
    settings.save(data)


def _spy_graph_refresh_targets(hub, monkeypatch):
    """Install call-recording spies on every target of DockHub's
    _refresh_graph_dependent_choices (the seven entity docks' set_root_path,
    trees_dock.refresh_ref_candidates — the lightweight TreesDock half — and
    root_metadata_dock.refresh_working_file_choices) — AFTER DockHub is built
    (construction itself calls set_root_path during _wire; the spies must only
    see post-construction calls). Returns {name: [recorded_arg, ...]}."""
    calls = {}
    for name in ("rules_dock", "placer_dock", "thermal_via_dock", "cells_dock",
                 "tools_dock", "points_dock", "extract_dock"):
        recorded = []
        monkeypatch.setattr(getattr(hub, name), "set_root_path",
                            lambda path, r=recorded: r.append(path))
        calls[name] = recorded
    recorded_trees = []
    monkeypatch.setattr(hub.trees_dock, "refresh_ref_candidates",
                        lambda: recorded_trees.append(True))
    calls["trees_dock"] = recorded_trees
    recorded_root = []
    monkeypatch.setattr(hub.root_metadata_dock, "refresh_working_file_choices",
                        lambda: recorded_root.append(True))
    calls["root_metadata_dock"] = recorded_root
    return calls


def test_graph_changed_refreshes_every_dock_with_a_file_combo(main_window, monkeypatch):
    """ConfigTreeDock's graph_changed must re-fetch every dock's graph-derived
    combo choices — the same handler the seven entity-dock saved signals feed —
    i.e. set_root_path on all seven entity docks plus
    trees_dock.refresh_ref_candidates and
    root_metadata_dock.refresh_working_file_choices, each exactly once per
    emit."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        targets = _spy_graph_refresh_targets(hub, monkeypatch)
        hub.config_tree_dock.graph_changed.emit()
        for name, calls in targets.items():
            assert len(calls) == 1, f"{name} not refreshed exactly once: {calls}"
    finally:
        _teardown_hub(hub)


def test_dock_saved_also_refreshes_graph_dependent_choices(main_window, monkeypatch):
    """Second trigger found at plan review: an entity dock's own Save can
    introduce a brand-new NAME directly (e.g. CellDock's "Add cell..." +
    Save), bypassing the tree entirely — so each of the seven docks' saved
    signal must ALSO fire the graph-dependent refresh, in addition to its
    existing `saved -> config_tree_dock.refresh` wiring (the tree keeps
    updating its own display; the broadcast updates everyone else)."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        targets = _spy_graph_refresh_targets(hub, monkeypatch)
        for dock_name in ("placer_dock", "thermal_via_dock", "extract_dock",
                          "points_dock", "rules_dock", "cells_dock",
                          "tools_dock"):
            getattr(hub, dock_name).saved.emit()
        for name, calls in targets.items():
            assert len(calls) == 7, f"{name} not refreshed once per dock Save: {calls}"
    finally:
        _teardown_hub(hub)


def test_new_cell_save_visible_in_rules_spoke_cell_combo(main_window, tmp_path):
    """The review-found counterpart: a Cell created DIRECTLY in CellDock (via
    new_cell + a real Save, bypassing the tree — the tree never learns about
    it from its own actions) must show up in RulesDock.spoke_cell_combo (the
    whole-graph cell-name combo) immediately. Before the fix it only appeared
    after switching the root away and back (same failure class as Denis's
    complaint, different trigger — the entity dock's Save, not a tree action)."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}, "rules": []})
    _seed_last_root(root)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        def combo_texts(combo):
            return [combo.itemText(i) for i in range(combo.count())]
        assert "brand_new_cell" not in combo_texts(hub.rules_dock.spoke_cell_combo)

        hub.cells_dock.new_cell(root)
        hub.cells_dock.name_edit.setText("brand_new_cell")
        hub.cells_dock.comp_role_edit.setCurrentText("A")
        hub.cells_dock._on_add_component()
        hub.cells_dock._on_save()

        assert "brand_new_cell" in combo_texts(hub.rules_dock.spoke_cell_combo)
    finally:
        _teardown_hub(hub)
