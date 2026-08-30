# tests/gui/test_hotkeys.py
"""Tests for gui/hotkeys.py — the QAction-based hotkey infrastructure (plan
techdocs/handoff/deepseek/plan_2026_08_30_dock_toolbars_menus_hotkeys.md,
Этап 1): actions register with a stable id + default shortcut, a stored
override in gui_state.json["hotkeys"] is applied on the next build, and
set_shortcut persists + re-applies to the live action (the Settings-tab
reassignment path)."""
import pytest
from PyQt6.QtGui import QKeySequence

from gui import settings, hotkeys
from gui.hotkeys import build_action, get_shortcut, override_for, registered_hotkeys, set_shortcut

ACTION_ID = "test.action"


@pytest.fixture(autouse=True)
def _isolated_hotkey_registry():
    """The gui.hotkeys module-level registry is shared process-wide (other
    gui test files register e.g. "root_metadata.*" actions while building
    docks), so clear it around each test here — otherwise a test asserting on
    the registry contents sees entries left over by earlier test files."""
    hotkeys.HOTKEY_ACTIONS.clear()
    hotkeys._LIVE_ACTIONS.clear()
    yield
    hotkeys.HOTKEY_ACTIONS.clear()
    hotkeys._LIVE_ACTIONS.clear()


def _make(main_window, action_id=ACTION_ID, default="Ctrl+Shift+P", callback=None):
    return build_action(main_window, action_id, "Test action", default, callback)


def test_build_action_registers_and_uses_default(main_window, qapp):
    action = _make(main_window)
    assert action.objectName() == ACTION_ID
    assert action.shortcut() == QKeySequence("Ctrl+Shift+P")
    assert registered_hotkeys() == [(ACTION_ID, "Test action", "Ctrl+Shift+P")]


def test_action_added_to_parent_window(main_window, qapp):
    """build_action adds the action to `parent` — the addAction call is what
    actually makes the shortcut active (see gui/hotkeys.py docstring)."""
    action = _make(main_window)
    assert action in main_window.actions()


def test_triggered_fires_callback(main_window, qapp):
    calls = []
    action = _make(main_window, callback=lambda: calls.append(True))
    action.trigger()
    assert calls == [True]


def test_stored_override_applies_on_next_build(main_window, qapp):
    """A custom binding stored under gui_state.json["hotkeys"] wins over the
    code default when the action is next created (plan: "кастомный биндинг из
    settings.state применяется при следующем открытии")."""
    settings.state.set("hotkeys", {ACTION_ID: "Ctrl+Alt+K"})
    action = _make(main_window)
    assert action.shortcut() == QKeySequence("Ctrl+Alt+K")
    assert get_shortcut(ACTION_ID) == QKeySequence("Ctrl+Alt+K")


def test_missing_override_uses_code_default(main_window, qapp):
    """An override for a DIFFERENT action id must not leak into this one —
    absent entry for `action_id` means the code default."""
    settings.state.set("hotkeys", {"other.action": "Ctrl+9"})
    action = _make(main_window)
    assert action.shortcut() == QKeySequence("Ctrl+Shift+P")


def test_set_shortcut_persists_and_reapplies_live(main_window, qapp):
    action = _make(main_window)
    set_shortcut(ACTION_ID, "Ctrl+Alt+J")
    assert override_for(ACTION_ID) == "Ctrl+Alt+J"
    assert settings.state.get("hotkeys") == {ACTION_ID: "Ctrl+Alt+J"}
    assert action.shortcut() == QKeySequence("Ctrl+Alt+J")


def test_clear_shortcut_removes_override_and_restores_default(main_window, qapp):
    action = _make(main_window)
    set_shortcut(ACTION_ID, "Ctrl+Alt+J")
    set_shortcut(ACTION_ID, "")  # clearing the edit == back to the code default
    assert override_for(ACTION_ID) is None
    assert action.shortcut() == QKeySequence("Ctrl+Shift+P")
