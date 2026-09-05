# tests/gui/test_fieldstool_dock.py
"""
FieldsToolDock is a non-dock facade over the ONE live fieldstool window,
which since 2026-09-05 (plan components_fieldstool_master_detail) is embedded
directly as the RIGHT pane of the "Components" master-detail dock
(RoleClusterTreeDock's QSplitter) — the separate right-hand fieldstool dock
and the bottom Pending dock are gone (Pending is the Components dock's second
left tab).
"""
from PyQt6.QtWidgets import QSplitter, QTabWidget

from gui.fieldstool_window import MainWindow as FieldsToolMainWindow


def test_wraps_a_real_fieldstool_main_window(real_main_window):
    assert isinstance(real_main_window.fieldstool_dock.window, FieldsToolMainWindow)


def test_fieldstool_window_is_embedded_in_the_components_dock(real_main_window):
    """The fieldstool window is no longer a right-hand dock — it is the right
    pane of the Components master-detail dock's splitter (widget(1)), with the
    Pending page as the second left tab (tabs on top)."""
    hub = real_main_window._dock_hub
    tree_dock = hub.tree_dock
    assert not hasattr(hub, "detail_dock")
    # Fieldstool/Pending are pages of the Components dock, not top-level docks.
    assert hub.fieldstool_dock not in hub.docks
    assert hub.pending_dock not in hub.docks
    # Master-detail structure: QSplitter { QTabWidget(tabs on TOP){ Components,
    # Pending } | fieldstool_window }.
    assert isinstance(tree_dock.splitter, QSplitter)
    assert tree_dock.splitter.count() == 2
    assert tree_dock.splitter.widget(1) is hub.fieldstool_dock.window
    left_tabs = tree_dock.splitter.widget(0)
    assert isinstance(left_tabs, QTabWidget)
    assert left_tabs.tabPosition() == QTabWidget.TabPosition.North
    assert left_tabs.count() == 2
    assert left_tabs.widget(0) is tree_dock._tree_page
    assert left_tabs.widget(1) is hub.pending_dock


def test_open_fieldstool_shows_and_raises_the_components_dock(real_main_window):
    real_main_window.tree_dock.setVisible(False)
    real_main_window.open_fieldstool()
    assert real_main_window.tree_dock.isVisible()
    assert real_main_window.isVisible()


# ── Phase 5.1: one connection, one polling loop ─────────────────────────────

def test_embedded_window_shares_main_connection(real_main_window):
    """The embedded fieldstool window always receives the main GUI's own
    BoardConnection (one kipy client, one REQ socket) — it never creates or
    polls a connection of its own (REQ single-in-flight)."""
    window = real_main_window.fieldstool_dock.window
    assert window.connection is real_main_window.connection


def test_embedded_window_live_selection_push_sets_targets(real_main_window):
    """Phase 5.1 — the main GUI's single 400ms selection tick drives the
    embedded window's target label through the dock delegate."""
    window = real_main_window.fieldstool_dock.window
    real_main_window._dock_hub.push_fieldstool_selection(["R2", "R1"])
    assert window._current_targets == ["R1", "R2"]
    assert window.stage_button.isEnabled()


def test_embedded_window_empty_live_selection_is_noop(real_main_window):
    """Phase 5.1 — an empty selection keeps the last targets (the
    cross-probe's existing behavior), not clearing them."""
    window = real_main_window.fieldstool_dock.window
    window._set_targets(["R1"])
    real_main_window._dock_hub.push_fieldstool_selection([])
    assert window._current_targets == ["R1"]


def test_embedded_window_connection_status_reaches_label(real_main_window):
    """Phase 5.1 — the shared connection's state is mirrored into the
    embedded window's status label (its own connect/refresh poll is off)."""
    window = real_main_window.fieldstool_dock.window
    real_main_window._dock_hub.push_fieldstool_connection_status(None)
    assert "Connected" in window.status_label.text()
    real_main_window._dock_hub.push_fieldstool_connection_status("boom")
    assert "boom" in window.status_label.text()
