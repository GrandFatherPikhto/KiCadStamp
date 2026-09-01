# tests/gui/test_tray.py
"""
Tray checkbox lifecycle, closeEvent hide-vs-real-close branching, and the
tray menu's Quit handler.

The tray checkbox moved to the Settings browser's ConfiguratorDock 2026-08-15
(plan configurator_panel) — it is no longer a MainWindow attribute, so these
tests reach it through the real window's DockHub alias:
    real_main_window._dock_hub.configurator_dock.tray_checkbox

Since 2026-09-01 (plan project_settings_dialogs) settings apply EXPLICITLY
(modal OK/Apply): a checkbox toggle alone is a draft — the tray icon is built
only when apply() persists it (DockHub wires tray_enabled_toggled, emitted
from apply(), back onto MainWindow._set_tray_enabled). MainWindow.closeEvent
also reads the APPLIED state (settings.state), not the widget.
"""
from unittest.mock import Mock

from PyQt6.QtWidgets import QSystemTrayIcon

from gui import settings


def _tray_checkbox(window):
    return window._dock_hub.configurator_dock.tray_checkbox


def _apply_settings(window):
    window._dock_hub.configurator_dock.apply()


def test_checkbox_creates_tray_icon(real_main_window):
    assert real_main_window._tray_icon is None
    _tray_checkbox(real_main_window).setChecked(True)
    assert real_main_window._tray_icon is None  # draft only — not applied yet
    _apply_settings(real_main_window)
    assert isinstance(real_main_window._tray_icon, QSystemTrayIcon)


def test_unchecking_removes_tray_icon(real_main_window):
    _tray_checkbox(real_main_window).setChecked(True)
    _apply_settings(real_main_window)
    _tray_checkbox(real_main_window).setChecked(False)
    _apply_settings(real_main_window)
    assert real_main_window._tray_icon is None


def test_tray_enabled_persists_round_trip(real_main_window):
    _tray_checkbox(real_main_window).setChecked(True)
    _apply_settings(real_main_window)
    _tray_checkbox(real_main_window).setChecked(False)  # off again, so close() takes the real-close path
    _apply_settings(real_main_window)
    real_main_window.close()
    assert settings.load()["tray_enabled"] is False


def test_close_hides_instead_of_quitting_when_tray_checked(real_main_window, monkeypatch):
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)
    _tray_checkbox(real_main_window).setChecked(True)
    _apply_settings(real_main_window)

    real_main_window.close()

    persist.assert_not_called()
    assert real_main_window.isHidden()


def test_close_persists_and_really_closes_when_tray_unchecked(real_main_window, monkeypatch):
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    real_main_window.close()

    persist.assert_called_once()


def test_open_fieldstool_unhides_and_raises(real_main_window):
    real_main_window.hide()
    real_main_window.open_fieldstool()
    assert real_main_window.isVisible()
    assert real_main_window.fieldstool_dock.isVisible()


def test_quit_persists_and_calls_application_quit(real_main_window, monkeypatch, qapp):
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)
    quit_mock = Mock()
    monkeypatch.setattr(qapp, "quit", quit_mock)

    real_main_window._quit()

    persist.assert_called_once()
    quit_mock.assert_called_once()
