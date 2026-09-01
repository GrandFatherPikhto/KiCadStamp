# tests/gui/test_settings_dialog.py
"""Tests for SettingsDialog (gui/docks/settings_dialog.py, 2026-09-01, plan
project_settings_dialogs): the MODAL dialog hosting the single live
ConfiguratorDock settings browser (category tree + pages) with OK/Apply/Cancel.
Settings apply EXPLICITLY: Apply persists and stays open, OK persists and
closes, Cancel/reject discards the draft."""
from unittest.mock import Mock

from PyQt6.QtWidgets import QDialog

from gui import settings
from gui.docks.configurator import ConfiguratorDock
from gui.docks.settings_dialog import SettingsDialog


def _make_dialog(main_window):
    configurator = ConfiguratorDock(main_window, connection=main_window.connection)
    return SettingsDialog(configurator, main_window), configurator


def test_dialog_hosts_browser_and_three_buttons(main_window):
    dialog, configurator = _make_dialog(main_window)
    assert dialog.configurator_dock is configurator
    assert dialog.layout().indexOf(configurator) >= 0
    assert dialog.ok_button.text() == "OK"
    assert dialog.apply_button.text() == "Apply"
    assert dialog.cancel_button.text() == "Cancel"
    assert dialog.objectName() == "settings_dialog"


def test_apply_button_applies_and_stays_open(main_window, qapp):
    dialog, configurator = _make_dialog(main_window)
    configurator.raw_write_checkbox.setChecked(True)
    dialog._on_apply()
    assert settings.state.get("mcp_allow_raw_write") is True
    assert dialog.isVisible() is False  # Apply does not close the dialog


def test_ok_applies_and_accepts(main_window, qapp):
    dialog, configurator = _make_dialog(main_window)
    configurator.raw_write_checkbox.setChecked(True)
    dialog._on_ok()
    assert settings.state.get("mcp_allow_raw_write") is True
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_cancel_discards_draft(main_window, qapp):
    dialog, configurator = _make_dialog(main_window)
    configurator.raw_write_checkbox.setChecked(True)
    assert settings.state.get("mcp_allow_raw_write") is None  # draft only
    dialog._on_cancel()
    assert not configurator.raw_write_checkbox.isChecked()
    assert settings.state.get("mcp_allow_raw_write") is None  # nothing persisted


def test_reject_discards_draft_like_cancel(main_window, qapp):
    """Window X / Esc (reject) has the same discard-the-draft semantics as the
    Cancel button — a draft that was never applied must not become the
    persisted state."""
    dialog, configurator = _make_dialog(main_window)
    configurator.always_on_top_checkbox.setChecked(True)
    dialog.reject()
    assert not configurator.always_on_top_checkbox.isChecked()
    assert settings.state.get("always_on_top") is None


def test_open_modal_reseeds_from_state(main_window, qapp, monkeypatch):
    """open_modal() re-seeds the browser from the persisted state before the
    modal loop — a leftover draft (from a previous cancelled opening) or a
    change made by another code path must not leak into this opening. The
    blocking exec() is monkeypatched out."""
    dialog, configurator = _make_dialog(main_window)
    configurator.always_on_top_checkbox.setChecked(True)  # leftover draft
    settings.state.set("always_on_top", False)
    monkeypatch.setattr(dialog, "exec", lambda: QDialog.DialogCode.Accepted)
    dialog.open_modal()
    assert not configurator.always_on_top_checkbox.isChecked()


def test_tools_settings_action_opens_the_dialog(real_main_window, monkeypatch):
    """Tools > "Settings..." routes into SettingsDialog.open_modal() — the
    modal entry point for the Settings browser."""
    tools_menus = [a for a in real_main_window.menuBar().actions()
                   if a.text() == "Tools"]
    assert len(tools_menus) == 1
    tools = tools_menus[0].menu()
    action = next(a for a in tools.actions() if a.text() == "Settings...")
    calls = []
    monkeypatch.setattr(real_main_window._dock_hub.settings_dialog,
                        "open_modal", lambda: calls.append(True))
    action.trigger()
    assert calls == [True]
