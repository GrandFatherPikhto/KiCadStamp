# tests/gui/test_detail_panel.py

from gui.docks.cell_editor import CellDock
from gui.docks.configurator import ConfiguratorDock
from gui.docks.detail_panel import DetailDock
from gui.docks.net_trace import NetTraceDock
from gui.docks.placer import PlacerDock
from gui.docks.points import PointsDock
from gui.docks.root_metadata import RootMetadataDock
from gui.docks.rules import RuleDock
from gui.docks.thermal_via import ThermalViaArrayDock
from gui.docks.tools import ToolsDock


def test_pages_are_the_expected_panel_types(main_window):
    dock = DetailDock(main_window)
    assert isinstance(dock.placer_panel, PlacerDock)
    assert isinstance(dock.root_panel, RootMetadataDock)
    assert isinstance(dock.thermal_via_panel, ThermalViaArrayDock)
    assert isinstance(dock.points_panel, PointsDock)
    assert isinstance(dock.rules_panel, RuleDock)
    assert isinstance(dock.net_trace_panel, NetTraceDock)
    assert isinstance(dock.cells_panel, CellDock)
    assert isinstance(dock.configurator_panel, ConfiguratorDock)
    assert isinstance(dock.tools_panel, ToolsDock)
    # Coordinate placements merged into PlacerDock (2026-08-12, Group 1) —
    # no separate Coordinate panel/tab anymore. Extract is NOT a page since
    # 2026-08-31 (plan extract_dialog_and_hide_existing.md) — it moved to a
    # standalone dialog (gui/docks/extract_dialog.py). Settings is the 8th
    # tab; Net traces sits between Rules and Cells; Tools between Cells and
    # Settings.
    assert dock.stack.count() == 9


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
    assert dock.tab_bar.currentIndex() == 1
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_root_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_root()
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.root_panel


def test_no_extract_tab_since_it_is_a_dialog(main_window):
    """2026-08-31 (plan extract_dialog_and_hide_existing.md): the Extract form
    moved out of DetailDock into a standalone dialog (gui/docks/extract_dialog
    .py) — there must be no Extract page/tab left here, and the first
    non-Project page is Placer."""
    dock = DetailDock(main_window)
    labels = [dock.tab_bar.tabText(i) for i in range(dock.tab_bar.count())]
    assert "Extract" not in labels
    assert labels[1] == "Placer"


def test_manually_clicking_a_tab_switches_the_stack(main_window):
    """Manual override — the tab bar itself, not just the auto-switch
    methods, must drive the stack (2026-08-03: Denis chose auto + manual
    selector so a panel stays reachable even without a matching tree
    click)."""
    dock = DetailDock(main_window)
    dock.tab_bar.setCurrentIndex(1)
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_thermal_via_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_thermal_via()
    assert dock.tab_bar.currentIndex() == 2
    assert dock.stack.currentWidget() is dock.thermal_via_panel


def test_show_coordinate_placer_switches_to_the_placer_tab(main_window):
    """2026-08-12, Group 1: the coordinate mode merged into PlacerDock, so
    show_coordinate_placer() is an alias for show_placer() — there is no
    separate Coordinate placer tab anymore."""
    dock = DetailDock(main_window)
    dock.show_coordinate_placer()
    assert dock.tab_bar.currentIndex() == 1
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_points_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_points()
    assert dock.tab_bar.currentIndex() == 3
    assert dock.stack.currentWidget() is dock.points_panel


def test_show_rules_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_rules()
    assert dock.tab_bar.currentIndex() == 4
    assert dock.stack.currentWidget() is dock.rules_panel


def test_show_net_trace_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_net_trace()
    assert dock.tab_bar.currentIndex() == 5
    assert dock.stack.currentWidget() is dock.net_trace_panel


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
    dock.show_root()
    assert dock.windowTitle() == "Detail — Project"


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
    dock.tab_bar.setCurrentIndex(4)  # Rules
    assert dock.windowTitle() == "Detail — Rules: my_rule"


def test_show_tools_switches_tab_and_stack(main_window):
    """Tools (2026-08-30, phase 5.2 stage 3) — the Entity's electrical
    fields, between Cells and Settings."""
    dock = DetailDock(main_window)
    dock.show_tools()
    assert dock.tab_bar.currentIndex() == 7
    assert dock.stack.currentWidget() is dock.tools_panel


def test_show_settings_switches_tab_and_stack(main_window):
    """Settings is the 9th tab (2026-08-15 plan configurator_panel, +1 for
    Net traces 2026-08-21, +1 for Tools 2026-08-30, -1 for Extract moved to
    its own dialog 2026-08-31) — its show_X() page-switch follows the same
    pattern as every other page."""
    dock = DetailDock(main_window)
    dock.show_settings()
    assert dock.tab_bar.currentIndex() == 8
    assert dock.stack.currentWidget() is dock.configurator_panel


def test_tab_bar_has_highlight_stylesheet(main_window):
    """Smoke test for the highlight scheme (plan configurator_panel) — the
    Detail dock's active-tab highlight is applied at construction."""
    dock = DetailDock(main_window)
    assert "selected" in dock.tab_bar.styleSheet()


# ── No internal scroll area (2026-08-30, Denis: "убираем скроллы внутри
#    доков") ──────────────────────────────────────────────────────────────

def test_stack_is_not_wrapped_in_a_scroll_area(main_window):
    """Denis, 2026-08-30: remove the internal scrolls. The 2026-08-27
    QScrollArea wrap is gone — the stack sits directly in the dock's layout,
    and the app-wide `* { min-width: 0 }` stylesheet (see
    _common.apply_compact_field_minimums) already lets every page/tab shrink
    to its absolute minimum, so nothing overflows off-screen."""
    dock = DetailDock(main_window)
    assert not hasattr(dock, "scroll_area")
    assert dock.stack.parent() is dock.widget()  # direct child of the container


def test_stack_size_hints_follow_the_current_page(main_window):
    """_StackedPages (2026-08-30): the stack's size hints track the CURRENT
    page only, so the dock sizes to the page you are actually on (not the
    tallest one) even without a scroll area."""
    dock = DetailDock(main_window)
    dock.tab_bar.setCurrentIndex(1)  # Extract
    assert dock.stack.sizeHint() == dock.stack.currentWidget().sizeHint()
    assert dock.stack.minimumSizeHint() == dock.stack.currentWidget().minimumSizeHint()
