# tests/gui/test_fieldstool_dock.py
"""
FieldsToolDock wraps a real fieldstool MainWindow and is tabified first in
the right-hand dock group (replacing the retired BulkFieldEditorDock slot).
"""
from PyQt6.QtCore import Qt

from gui.fieldstool_window import MainWindow as FieldsToolMainWindow


def test_wraps_a_real_fieldstool_main_window(real_main_window):
    assert isinstance(real_main_window.fieldstool_dock.window, FieldsToolMainWindow)
    assert real_main_window.fieldstool_dock.widget() is real_main_window.fieldstool_dock.window


def test_fieldstool_is_first_right_hand_tab(real_main_window):
    """Fieldstool is the first (sole) right-hand dock now — the Config dock
    became a master-detail and DetailDock was removed (2026-09-05, plan
    config_qview_placer_nettrace), so fieldstool is no longer tabified with
    a Detail dock."""
    hub = real_main_window._dock_hub
    assert not hasattr(hub, "detail_dock")
    assert (real_main_window.dockWidgetArea(real_main_window.fieldstool_dock)
            == Qt.DockWidgetArea.RightDockWidgetArea)


def test_open_fieldstool_shows_and_raises_the_dock(real_main_window):
    real_main_window.fieldstool_dock.setVisible(False)
    real_main_window.open_fieldstool()
    assert real_main_window.fieldstool_dock.isVisible()
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
