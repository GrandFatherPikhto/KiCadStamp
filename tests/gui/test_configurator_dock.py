# tests/gui/test_configurator_dock.py
"""Tests for the Settings tab's ConfiguratorDock (gui/docks/configurator.py,
2026-08-15, plan techdocs/handoff/plan_2026_08_15_configurator_panel.md):
always-on-top/tray checkbox relocation, highlight mode/color persistence
(QColorDialog mocked), connection-timeout persistence, and the highlight
smoke test across all three consumer widgets."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QKeySequence

from gui import settings
from gui.docks.config_tree import ConfigTreeDock
from gui.docks.configurator import ConfiguratorDock
from gui.docks.detail_panel import DetailDock
from gui.docks.role_cluster_tree import RoleClusterTreeDock

from gui.hotkeys import build_action

import gui.docks.configurator as configurator_mod


# ── Always on top / Tray — moved checkboxes, same effects ────────────────

def test_always_on_top_checkbox_emits_toggled(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.always_on_top_toggled.connect(calls.append)
    dock.always_on_top_checkbox.setChecked(True)
    dock.always_on_top_checkbox.setChecked(False)
    assert calls == [True, False]


def test_tray_checkbox_emits_toggled(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.tray_enabled_toggled.connect(calls.append)
    dock.tray_checkbox.setChecked(True)
    assert calls == [True]


def test_always_on_top_checkbox_still_sets_window_flag(real_main_window):
    """The always-on-top checkbox moved to the Settings tab, but toggling it
    must still flip the real window's always-on-top flag (DockHub wires the
    configurator's signal back onto MainWindow._set_always_on_top)."""
    checkbox = real_main_window._dock_hub.configurator_dock.always_on_top_checkbox
    flag = Qt.WindowType.WindowStaysOnTopHint
    assert not (real_main_window.windowFlags() & flag)
    checkbox.setChecked(True)
    assert real_main_window.windowFlags() & flag
    checkbox.setChecked(False)
    assert not (real_main_window.windowFlags() & flag)


# ── Highlight mode / color ───────────────────────────────────────────────

def test_highlight_defaults_to_system_mode(main_window, qapp):
    """Nothing stored yet == system mode (the default). The dock does NOT
    write "system" into storage at construction — it only stores real user
    changes, so an absent key stays absent and every reader falls back to
    the default."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.system_radio.isChecked()
    assert not dock.custom_radio.isChecked()
    assert not dock.pick_color_button.isEnabled()
    assert settings.state.get("highlight_mode", "system") == "system"
    assert settings.state.get("highlight_mode") is None


def test_selecting_custom_persists_mode_and_enables_pick(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    assert settings.state.get("highlight_mode") == "custom"
    assert dock.pick_color_button.isEnabled()


def test_selecting_system_back_persists_mode(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    dock.system_radio.setChecked(True)
    assert settings.state.get("highlight_mode") == "system"
    assert not dock.pick_color_button.isEnabled()


def test_picking_color_persists_hex_and_updates_preview(main_window, qapp, monkeypatch):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor("#ff8800"))
    dock._pick_color()
    assert settings.state.get("highlight_color") == "#ff8800"
    assert "#ff8800" in dock.color_preview.styleSheet()


def test_picking_color_cancel_keeps_previous(main_window, qapp, monkeypatch):
    """getColor() returning an invalid QColor == the user pressed Cancel —
    nothing is persisted or re-emitted."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    settings.state.set("highlight_color", "#abcdef")
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor())  # invalid -> cancel
    dock._pick_color()
    assert settings.state.get("highlight_color") == "#abcdef"


def test_highlight_changed_emitted_on_mode_and_color(main_window, qapp, monkeypatch):
    """Exactly one highlight_changed per change — radio toggling must not
    double-emit (the two radios are auto-exclusive, but only custom_radio is
    wired to the handler)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.highlight_changed.connect(lambda: calls.append(None))
    dock.custom_radio.setChecked(True)
    assert len(calls) == 1  # exactly one emit per mode toggle
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor("#112233"))
    dock._pick_color()
    assert len(calls) == 2


def test_highlight_radio_state_restored_from_settings(main_window, qapp):
    """A previously-stored custom mode is reflected into the radio buttons at
    construction (that's the whole point of persisting it)."""
    settings.state.set("highlight_mode", "custom")
    settings.state.set("highlight_color", "#123456")
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.custom_radio.isChecked()
    assert dock.pick_color_button.isEnabled()
    assert "#123456" in dock.color_preview.styleSheet()


# ── Connection timeout ───────────────────────────────────────────────────

def test_timeout_spin_persists_to_settings(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.timeout_spin.setValue(5000)
    assert settings.state.get("kicad_timeout_ms") == 5000


def test_timeout_spin_updates_connection_live(main_window, qapp):
    """The plan's chosen mechanism: write straight into connection.timeout_ms,
    which BoardConnection reads by reference on every connect(), so it takes
    effect on the next connection without disturbing any open one."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert main_window.connection.timeout_ms == 20000  # default, untouched
    dock.timeout_spin.setValue(7000)
    assert main_window.connection.timeout_ms == 7000


def test_timeout_spin_range(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.timeout_spin.minimum() >= 1000
    assert dock.timeout_spin.maximum() <= 120000


def test_timeout_restored_from_settings_and_applied(main_window, qapp):
    """A stored timeout is reflected into the spinbox at construction AND
    applied to the connection (which was built before this panel existed and
    would otherwise keep the CLI/default value)."""
    settings.state.set("kicad_timeout_ms", 9000)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.timeout_spin.value() == 9000
    assert main_window.connection.timeout_ms == 9000


# ── Highlight smoke test across the three consumer widgets ───────────────

def test_three_consumers_have_highlight_stylesheet(main_window):
    """Each of the three highlight consumers applies its stylesheet at
    construction — active Detail-dock tab, Config tree selected item,
    Components tree selected item — so the native barely-visible Windows
    selection is gone from the very first paint."""
    detail = DetailDock(main_window)
    assert "selected" in detail.tab_bar.styleSheet()
    config_tree = ConfigTreeDock(main_window)
    assert "selected" in config_tree.tree.styleSheet()
    components = RoleClusterTreeDock(main_window)
    assert "selected" in components.tree.styleSheet()


# ── Config tree: rename confirmation toggle (2026-08-25) ─────────────────

def test_rename_confirmation_defaults_to_enabled(main_window, qapp):
    """No key stored yet == the confirmation dialog stays ON (the default —
    this setting only ever ADDS the option to silence it, never changes the
    default behavior)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.rename_confirmation_checkbox.isChecked()
    assert settings.state.get("rename_confirmation_enabled") is None  # not written at construction


def test_unchecking_rename_confirmation_persists(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.rename_confirmation_checkbox.setChecked(False)
    assert settings.state.get("rename_confirmation_enabled") is False
    dock.rename_confirmation_checkbox.setChecked(True)
    assert settings.state.get("rename_confirmation_enabled") is True


def test_rename_confirmation_state_restored_on_recreation(main_window, qapp):
    """The setting survives a dock rebuild — read back from settings.state at
    construction, like every other key on this page."""
    settings.state.set("rename_confirmation_enabled", False)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert not dock.rename_confirmation_checkbox.isChecked()


# ── MCP server ────────────────────────────────────────────────────────────

def test_raw_write_checkbox_defaults_to_off(main_window, qapp):
    """No key stored yet == the raw MCP write toggle is OFF, and construction
    does not write the key (same pattern as rename_confirmation)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert not dock.raw_write_checkbox.isChecked()
    assert settings.state.get("mcp_allow_raw_write") is None


def test_toggling_raw_write_persists(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.raw_write_checkbox.setChecked(True)
    assert settings.state.get("mcp_allow_raw_write") is True
    dock.raw_write_checkbox.setChecked(False)
    assert settings.state.get("mcp_allow_raw_write") is False


def test_raw_write_state_restored_on_recreation(main_window, qapp):
    """The setting survives a dock rebuild (read back from settings.state at
    construction), matching every other key on this page."""
    settings.state.set("mcp_allow_raw_write", True)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.raw_write_checkbox.isChecked()


# ── Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ────────

def test_hotkeys_section_has_one_edit_per_registered_action(main_window, qapp):
    """The Settings tab lists one QKeySequenceEdit per registered QAction —
    the reassignment UI surface for the hotkey infrastructure."""
    build_action(main_window, "test.hotkey", "Test hotkey", "Ctrl+Shift+Q", None)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert "test.hotkey" in dock.hotkey_edits
    edit = dock.hotkey_edits["test.hotkey"]
    assert edit.keySequence() == QKeySequence("Ctrl+Shift+Q")


def test_hotkey_edit_shows_stored_override(main_window, qapp):
    """A stored override is reflected into the QKeySequenceEdit at
    construction (the whole point of persisting it)."""
    build_action(main_window, "test.hotkey", "Test hotkey", "Ctrl+Shift+Q", None)
    settings.state.set("hotkeys", {"test.hotkey": "Ctrl+Alt+H"})
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.hotkey_edits["test.hotkey"].keySequence() == QKeySequence("Ctrl+Alt+H")


def test_editing_hotkey_persists_override(main_window, qapp):
    """Rebinding in the Settings tab writes gui_state.json["hotkeys"] and
    re-applies to the live action (set_shortcut's live re-apply)."""
    build_action(main_window, "test.hotkey", "Test hotkey", "Ctrl+Shift+Q", None)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    edit = dock.hotkey_edits["test.hotkey"]
    edit.setKeySequence(QKeySequence("Ctrl+Alt+R"))
    dock._on_hotkey_edited("test.hotkey", edit)
    assert settings.state.get("hotkeys") == {"test.hotkey": "Ctrl+Alt+R"}


def test_hotkeys_refresh_picks_up_actions_registered_later(main_window, qapp):
    """The Hotkeys list is REBUILDABLE — an action registered AFTER the
    ConfiguratorDock was constructed (e.g. LogDock's, built later in DockHub)
    appears after refresh_hotkeys(), so dock construction order does not decide
    what is rebindable in Settings (the fix from review of handoff
    2026_08_30_hotkeys_pilot_and_file_menu_done).

    Uses a UNIQUE action id (not the shared "test.hotkey" the earlier tests in
    this file register) so "not registered yet" genuinely means it: the module
    level gui.hotkeys registry accumulates across tests here."""
    action_id = "test.late_registered"
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert action_id not in dock.hotkey_edits  # not registered yet
    build_action(main_window, action_id, "Late hotkey", "Ctrl+Shift+Q", None)
    dock.refresh_hotkeys()
    assert action_id in dock.hotkey_edits
    assert dock.hotkey_edits[action_id].keySequence() == QKeySequence("Ctrl+Shift+Q")
