# tests/gui/test_detail_panel.py

from gui.docks.detail_panel import DetailDock
from gui.docks.net_trace import NetTraceDock
from gui.docks.placer import PlacerDock


def test_pages_are_the_expected_panel_types(main_window):
    dock = DetailDock(main_window)
    assert isinstance(dock.placer_panel, PlacerDock)
    assert isinstance(dock.net_trace_panel, NetTraceDock)
    # Coordinate placements merged into PlacerDock (2026-08-12, Group 1) —
    # no separate Coordinate panel/tab anymore. Rules (2026-09-01, plan
    # rules_to_chains — the Chain form is the standalone non-modal ChainDialog
    # now), Extract (2026-08-31), Thermal via (2026-09-01), Points (2026-09-01,
    # plan plan_2026_09_01_points_dialog.md), Tools (2026-09-01, plan
    # plan_2026_09_01_tools_dialog_and_entity_roles.md), Cells (2026-09-04,
    # plan plan_2026_09_04_celldock_to_dialog.md), and Project + Settings
    # (2026-09-01, plan project_settings_dialogs) are all standalone dialogs
    # now, NOT pages.
    assert dock.stack.count() == 2


def test_placer_tab_is_shown_first(main_window):
    """Placer is the first tab now (2026-09-01, plan project_settings_dialogs)
    — the old Project tab moved out into the non-modal ProjectDialog (File >
    "Project...", gui/docks/project_dialog.py), so Placer is the default page
    on startup."""
    dock = DetailDock(main_window)
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_placer_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_placer()
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.placer_panel


def test_no_extract_tab_since_the_dock_was_removed(main_window):
    """Phase F (2026-09-01): the Extract dock (and its standalone dialog) was
    removed entirely — "Extract tree..." in DockHub is the single capture
    path. There must be no Extract page/tab left in DetailDock."""
    dock = DetailDock(main_window)
    labels = [dock.tab_bar.tabText(i) for i in range(dock.tab_bar.count())]
    assert "Extract" not in labels


def test_no_thermal_via_tab_since_it_is_a_dialog(main_window):
    """2026-09-01 (plan plan_2026_09_01_thermal_via_dialog.md): the Thermal via
    form moved out of DetailDock into a standalone dialog
    (gui/docks/thermal_via_dialog.py) — there must be no Thermal via page/tab
    left here."""
    dock = DetailDock(main_window)
    labels = [dock.tab_bar.tabText(i) for i in range(dock.tab_bar.count())]
    assert "Thermal via" not in labels


def test_no_project_or_settings_tab_since_they_are_dialogs(main_window):
    """2026-09-01 (plan project_settings_dialogs): the Project tab
    (RootMetadataDock -> non-modal ProjectDialog) and the Settings tab
    (ConfiguratorDock -> modal SettingsDialog) are both gone from the Detail
    dock — they are dialogs now (File > "Project...", Tools > "Settings...")."""
    dock = DetailDock(main_window)
    labels = [dock.tab_bar.tabText(i) for i in range(dock.tab_bar.count())]
    assert "Project" not in labels
    assert "Settings" not in labels
    assert "Points" not in labels
    assert "Tools" not in labels
    assert "Rules" not in labels  # Chain form is the standalone ChainDialog now
    assert "Cells" not in labels  # Cell editor is the CellDialog (2026-09-04)
    assert labels == ["Placer", "Net traces"]


def test_manually_clicking_a_tab_switches_the_stack(main_window):
    """Manual override — the tab bar itself, not just the auto-switch
    methods, must drive the stack (2026-08-03: Denis chose auto + manual
    selector so a panel stays reachable even without a matching tree
    click)."""
    dock = DetailDock(main_window)
    dock.tab_bar.setCurrentIndex(0)
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_coordinate_placer_switches_to_the_placer_tab(main_window):
    """2026-08-12, Group 1: the coordinate mode merged into PlacerDock, so
    show_coordinate_placer() is an alias for show_placer() — there is no
    separate Coordinate placer tab anymore."""
    dock = DetailDock(main_window)
    dock.show_coordinate_placer()
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_net_trace_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_net_trace()
    assert dock.tab_bar.currentIndex() == 1
    assert dock.stack.currentWidget() is dock.net_trace_panel


def test_no_cells_tab_or_show_cells_since_cell_editor_is_a_dialog(main_window):
    """2026-09-04 (plan plan_2026_09_04_celldock_to_dialog.md): the Cell
    editor (CellDock) is hosted in the standalone non-modal CellDialog now —
    there is no Cells tab / cells_panel / show_cells() page here anymore."""
    dock = DetailDock(main_window)
    assert not hasattr(dock, "cells_panel")
    assert not hasattr(dock, "show_cells")
    assert dock.stack.count() == 2  # Placer + Net traces


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
    dock.show_placer()
    assert dock.windowTitle() == "Detail — Placer"


def test_title_reflects_current_net_name_for_net_trace(main_window):
    dock = DetailDock(main_window)
    dock.net_trace_panel.net_edit.setCurrentText("GND")
    dock.show_net_trace()
    assert dock.windowTitle() == "Detail — Net traces: GND"


def test_title_reflects_placer_cluster_name(main_window):
    dock = DetailDock(main_window)
    dock.placer_panel.cluster_edit.setCurrentText("Channel_1")
    dock.show_placer()
    assert dock.windowTitle() == "Detail — Placer: Channel_1"


def test_title_updates_when_loading_a_different_net_on_the_same_tab(main_window):
    """QTabBar.currentChanged doesn't fire when the index doesn't change —
    show_net_trace() must call _update_title() unconditionally (see its own
    docstring), not rely solely on that signal."""
    dock = DetailDock(main_window)
    dock.net_trace_panel.net_edit.setCurrentText("first")
    dock.show_net_trace()
    assert dock.windowTitle() == "Detail — Net traces: first"

    dock.net_trace_panel.net_edit.setCurrentText("second")
    dock.show_net_trace()
    assert dock.windowTitle() == "Detail — Net traces: second"


def test_manual_tab_click_also_updates_the_title(main_window):
    dock = DetailDock(main_window)
    dock.net_trace_panel.net_edit.setCurrentText("GND")
    dock.tab_bar.setCurrentIndex(1)  # Net traces
    assert dock.windowTitle() == "Detail — Net traces: GND"


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
    dock.tab_bar.setCurrentIndex(0)  # Placer
    assert dock.stack.sizeHint() == dock.stack.currentWidget().sizeHint()
    assert dock.stack.minimumSizeHint() == dock.stack.currentWidget().minimumSizeHint()
