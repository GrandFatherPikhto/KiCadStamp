# tests/gui/test_detail_panel.py
from gui.docks.cell_editor import CellDock
from gui.docks.detail_panel import DetailDock
from gui.docks.extract import ExtractDock
from gui.docks.placer import PlacerDock
from gui.docks.points import PointsDock
from gui.docks.root_metadata import RootMetadataDock
from gui.docks.rules import RuleDock
from gui.docks.thermal_via import ThermalViaArrayDock


def test_pages_are_the_expected_panel_types(main_window):
    dock = DetailDock(main_window)
    assert isinstance(dock.extract_panel, ExtractDock)
    assert isinstance(dock.placer_panel, PlacerDock)
    assert isinstance(dock.root_panel, RootMetadataDock)
    assert isinstance(dock.thermal_via_panel, ThermalViaArrayDock)
    assert isinstance(dock.points_panel, PointsDock)
    assert isinstance(dock.rules_panel, RuleDock)
    assert isinstance(dock.cells_panel, CellDock)
    assert dock.stack.count() == 7


def test_project_tab_is_shown_first(main_window):
    """Project first (2026-08-11, Denis: "сделай док Проект первым. А то
    он не понятно, где стоит") — the control-tower panel (root ownership +
    Working file combobox, see gui/docks/root_metadata.py) is also the tab
    shown by default on startup."""
    dock = DetailDock(main_window)
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.root_panel


def test_show_placer_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_placer()
    assert dock.tab_bar.currentIndex() == 2
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_root_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_root()
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.root_panel


def test_show_extract_switches_back(main_window):
    dock = DetailDock(main_window)
    dock.show_placer()
    dock.show_extract()
    assert dock.tab_bar.currentIndex() == 1
    assert dock.stack.currentWidget() is dock.extract_panel


def test_manually_clicking_a_tab_switches_the_stack(main_window):
    """Manual override — the tab bar itself, not just the auto-switch
    methods, must drive the stack (2026-08-03: Denis chose auto + manual
    selector so a panel stays reachable even without a matching tree
    click)."""
    dock = DetailDock(main_window)
    dock.tab_bar.setCurrentIndex(2)
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_thermal_via_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_thermal_via()
    assert dock.tab_bar.currentIndex() == 3
    assert dock.stack.currentWidget() is dock.thermal_via_panel


def test_show_points_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_points()
    assert dock.tab_bar.currentIndex() == 4
    assert dock.stack.currentWidget() is dock.points_panel


def test_show_rules_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_rules()
    assert dock.tab_bar.currentIndex() == 5
    assert dock.stack.currentWidget() is dock.rules_panel


def test_show_cells_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_cells()
    assert dock.tab_bar.currentIndex() == 6
    assert dock.stack.currentWidget() is dock.cells_panel


# ── Raise-on-switch + window title (2026-08-06) ──────────────────────────

def test_show_placer_raises_and_shows_the_dock(main_window):
    """Found live — Denis: "неплохо бы подсвечивать, какой док сейчас
    активен. А то вообще, не видно, кто и что" — a plain tree click used to
    switch the internal tab without ever bringing DetailDock itself to the
    front of its own tabified group (fieldstool). Checked via monkeypatched
    setVisible/raise_ rather than real isVisible() — these tests never
    show() the top-level window, so real OS-level visibility isn't a
    meaningful signal here."""
    dock = DetailDock(main_window)
    calls = []
    dock.setVisible = lambda v: calls.append(("setVisible", v))
    dock.raise_ = lambda: calls.append(("raise_",))

    dock.show_placer()

    assert ("setVisible", True) in calls
    assert ("raise_",) in calls


def test_title_reflects_page_with_no_current_entity(main_window):
    dock = DetailDock(main_window)
    dock.show_extract()
    assert dock.windowTitle() == "Detail — Extract"


def test_title_reflects_current_entity_name_for_cells(main_window):
    dock = DetailDock(main_window)
    dock.cells_panel.name_edit.setText("composite")
    dock.show_cells()
    assert dock.windowTitle() == "Detail — Cells: composite"


def test_title_reflects_placer_cluster_name(main_window):
    dock = DetailDock(main_window)
    dock.placer_panel.cluster_edit.setCurrentText("Channel_1")
    dock.show_placer()
    assert dock.windowTitle() == "Detail — Placer: Channel_1"


def test_title_updates_when_loading_a_different_entity_on_the_same_tab(main_window):
    """QTabBar.currentChanged doesn't fire when the index doesn't change —
    show_cells() must call _update_title() unconditionally (see its own
    docstring), not rely solely on that signal."""
    dock = DetailDock(main_window)
    dock.cells_panel.name_edit.setText("first")
    dock.show_cells()
    assert dock.windowTitle() == "Detail — Cells: first"

    dock.cells_panel.name_edit.setText("second")
    dock.show_cells()
    assert dock.windowTitle() == "Detail — Cells: second"


def test_manual_tab_click_also_updates_the_title(main_window):
    dock = DetailDock(main_window)
    dock.rules_panel.name_edit.setText("my_rule")
    dock.tab_bar.setCurrentIndex(5)  # Rules
    assert dock.windowTitle() == "Detail — Rules: my_rule"
