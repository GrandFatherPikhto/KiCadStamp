# tests/gui/test_configurator_dock.py
"""Tests for the Settings browser's ConfiguratorDock (gui/docks/configurator.py,
2026-08-15, plan techdocs/handoff/plan_2026_08_15_configurator_panel.md;
reworked 2026-09-01, plan project_settings_dialogs): the two-pane category
tree + pages, and the EXPLICIT apply model (OK/Apply/Cancel, hosted in the
modal SettingsDialog) — a widget change is only a draft; apply() persists to
gui_state.json and fires the side effects, cancel()/reload_from_state()
discards the draft."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QKeySequence
from PyQt6.QtWidgets import QStyleFactory

from gui import hotkeys
from gui import settings
from gui.docks.config_tree import ConfigTreeDock
from gui.docks.configurator import ConfiguratorDock
from gui.docks.detail_panel import DetailDock
from gui.docks.role_cluster_tree import RoleClusterTreeDock

from gui.hotkeys import build_action

import gui.docks.configurator as configurator_mod


# ── Category tree / pages (2026-09-01, plan project_settings_dialogs) ─────

def test_tree_lists_expected_categories(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    labels = [dock.tree.topLevelItem(i).text(0)
              for i in range(dock.tree.topLevelItemCount())]
    assert labels == ["General", "Appearance", "KiCad", "Config tree",
                      "Hotkeys", "MCP server"]
    assert dock.stack.count() == len(labels)


def test_selecting_a_category_switches_the_page(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.tree.setCurrentItem(dock.tree.topLevelItem(4))  # Hotkeys
    assert dock.stack.currentIndex() == 4
    assert dock.stack.currentWidget() is dock.hotkeys_page
    dock.tree.setCurrentItem(dock.tree.topLevelItem(0))  # General
    assert dock.stack.currentWidget() is dock.general_page


# ── Always on top / Tray — moved checkboxes, applied via apply() ──────────

def test_apply_emits_always_on_top_toggled(main_window, qapp):
    """A checkbox toggle alone is only a draft — always_on_top_toggled fires
    from apply() (the modal OK/Apply contract), not from setChecked."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.always_on_top_toggled.connect(calls.append)
    dock.always_on_top_checkbox.setChecked(True)
    assert calls == []  # draft only
    dock.apply()
    assert calls == [True]
    dock.always_on_top_checkbox.setChecked(False)
    dock.apply()
    assert calls == [True, False]


def test_apply_emits_tray_enabled_toggled(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.tray_enabled_toggled.connect(calls.append)
    dock.tray_checkbox.setChecked(True)
    assert calls == []  # draft only
    dock.apply()
    assert calls == [True]


def test_always_on_top_checkbox_still_sets_window_flag(real_main_window):
    """The always-on-top checkbox moved to the Settings browser, and toggling
    it alone is a draft — but apply() must still flip the real window's
    always-on-top flag (DockHub wires the configurator's signal back onto
    MainWindow._set_always_on_top)."""
    configurator = real_main_window._dock_hub.configurator_dock
    checkbox = configurator.always_on_top_checkbox
    flag = Qt.WindowType.WindowStaysOnTopHint
    assert not (real_main_window.windowFlags() & flag)
    checkbox.setChecked(True)
    assert not (real_main_window.windowFlags() & flag)  # draft only
    configurator.apply()
    assert real_main_window.windowFlags() & flag
    checkbox.setChecked(False)
    configurator.apply()
    assert not (real_main_window.windowFlags() & flag)


# ── Highlight mode / color ────────────────────────────────────────────────

def test_highlight_defaults_to_system_mode(main_window, qapp):
    """Nothing stored yet == system mode (the default). The dock does NOT
    write "system" into storage at construction — it only stores real user
    changes on apply(), so an absent key stays absent and every reader falls
    back to the default."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.system_radio.isChecked()
    assert not dock.custom_radio.isChecked()
    assert not dock.pick_color_button.isEnabled()
    assert settings.state.get("highlight_mode", "system") == "system"
    assert settings.state.get("highlight_mode") is None


def test_selecting_custom_enables_pick_but_persists_only_on_apply(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    assert dock.pick_color_button.isEnabled()
    assert settings.state.get("highlight_mode") is None  # draft only
    dock.apply()
    assert settings.state.get("highlight_mode") == "custom"


def test_selecting_system_back_applies(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    dock.system_radio.setChecked(True)
    assert not dock.pick_color_button.isEnabled()
    dock.apply()
    assert settings.state.get("highlight_mode") == "system"


def test_picking_color_updates_preview_and_apply_persists(main_window, qapp, monkeypatch):
    """The picked color updates the draft + preview immediately, but is
    written to settings.state only on apply() (OK/Apply contract)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor("#ff8800"))
    dock._pick_color()
    assert "#ff8800" in dock.color_preview.styleSheet()
    assert settings.state.get("highlight_color") is None  # draft only
    dock.apply()
    assert settings.state.get("highlight_color") == "#ff8800"


def test_picking_color_cancel_keeps_previous(main_window, qapp, monkeypatch):
    """getColor() returning an invalid QColor == the user pressed Cancel —
    the draft is left untouched and apply() persists the previous color."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.custom_radio.setChecked(True)
    settings.state.set("highlight_color", "#abcdef")
    dock.reload_from_state()  # seed the draft from the persisted color
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor())  # invalid -> cancel
    dock._pick_color()
    dock.apply()
    assert settings.state.get("highlight_color") == "#abcdef"


def test_highlight_changed_emitted_once_per_apply(main_window, qapp, monkeypatch):
    """highlight_changed fires exactly once per apply(), NOT per toggle/pick
    (the modal OK/Apply contract — side effects are deferred until the user
    confirms)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    calls = []
    dock.highlight_changed.connect(lambda: calls.append(None))
    dock.custom_radio.setChecked(True)
    assert len(calls) == 0  # draft only
    monkeypatch.setattr(configurator_mod.QColorDialog, "getColor",
                        lambda *a, **k: QColor("#112233"))
    dock._pick_color()
    assert len(calls) == 0  # still draft
    dock.apply()
    assert len(calls) == 1  # exactly one emit per apply()


def test_highlight_radio_state_restored_from_settings(main_window, qapp):
    """A previously-stored custom mode is reflected into the radio buttons at
    construction (that's the whole point of persisting it)."""
    settings.state.set("highlight_mode", "custom")
    settings.state.set("highlight_color", "#123456")
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.custom_radio.isChecked()
    assert dock.pick_color_button.isEnabled()
    assert "#123456" in dock.color_preview.styleSheet()


# ── Qt style (2026-09-03, plan qt_style_setting) ─────────────────────────

class _RecordingApp:
    """Stands in for QApplication.instance() in apply() tests — records the
    setStyle() calls without touching the real test-session style."""

    def __init__(self, calls):
        self._calls = calls

    def setStyle(self, name):
        self._calls.append(name)


class _ForbiddingApp:
    """setStyle() must never be called on it — raises if it is."""

    def setStyle(self, name):
        raise AssertionError("setStyle must not be called for System default")


def test_style_combo_lists_system_default_first_then_all_styles(main_window, qapp):
    """First combo item is the special "System default"; the rest are the styles
    available on THIS build (QStyleFactory.keys()), sorted — never a hardcoded
    list (the set differs per OS/Qt build)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    expected = ["System default"] + sorted(QStyleFactory.keys())
    actual = [dock.style_combo.itemText(i) for i in range(dock.style_combo.count())]
    assert actual == expected


def test_style_defaults_to_system_default(main_window, qapp):
    """Nothing stored yet == "System default" (index 0), and construction does
    NOT write the key (same pattern as highlight_mode/rename_confirmation)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.style_combo.currentIndex() == 0
    assert settings.state.get("qt_style") is None


def test_style_restored_from_settings(main_window, qapp):
    """A stored style name that exists on this build is reflected into the
    combo at construction (the whole point of persisting it)."""
    valid = sorted(QStyleFactory.keys())[0]
    settings.state.set("qt_style", valid)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.style_combo.currentIndex() != 0
    assert dock.style_combo.currentText() == valid


def test_style_unknown_name_quietly_falls_back_to_system_default(main_window, qapp):
    """A stored value that names no style on THIS machine (e.g. gui_state.json
    synced from another OS) must not break the dialog — quiet fallback to
    "System default" (index 0), same fatal-safety as the startup path."""
    settings.state.set("qt_style", "NoSuchStyle_zzz")
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.style_combo.currentIndex() == 0
    assert dock.style_combo.currentText() == "System default"


def test_apply_concrete_style_persists_and_applies_live(main_window, qapp, monkeypatch):
    """Choosing a concrete style on Apply/OK: persisted as qt_style AND applied
    live via QApplication.instance().setStyle() (the dialog's immediate-apply
    contract — no restart needed)."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    valid = sorted(QStyleFactory.keys())[0]
    calls = []
    monkeypatch.setattr(configurator_mod.QApplication, "instance",
                        lambda: _RecordingApp(calls))
    dock.style_combo.setCurrentText(valid)
    assert settings.state.get("qt_style") is None  # draft only
    dock.apply()
    assert settings.state.get("qt_style") == valid
    assert calls == [valid]


def test_apply_system_default_clears_key_and_skips_setstyle(main_window, qapp,
                                                            monkeypatch):
    """Switching back to "System default" clears qt_style (None) and does NOT
    call setStyle() — Qt gives no guaranteed restore-to-default API after a
    live switch within this session (design §2.4), so we leave the running
    style untouched rather than attempt a non-guaranteed rollback."""
    valid = sorted(QStyleFactory.keys())[0]
    settings.state.set("qt_style", valid)  # previously chosen
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    monkeypatch.setattr(configurator_mod.QApplication, "instance",
                        lambda: _ForbiddingApp())
    dock.style_combo.setCurrentIndex(0)  # System default
    dock.apply()
    assert settings.state.get("qt_style") is None


# ── Connection timeout ────────────────────────────────────────────────────

def test_timeout_spin_persists_only_on_apply(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.timeout_spin.setValue(5000)
    assert settings.state.get("kicad_timeout_ms") is None  # draft only
    dock.apply()
    assert settings.state.get("kicad_timeout_ms") == 5000


def test_timeout_apply_updates_connection(main_window, qapp):
    """The plan's chosen mechanism: write straight into connection.timeout_ms,
    which BoardConnection reads by reference on every connect(), so it takes
    effect on the next connection without disturbing any open one. Happens on
    apply(), not on the spinbox edit."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert main_window.connection.timeout_ms == 20000  # default, untouched
    dock.timeout_spin.setValue(7000)
    assert main_window.connection.timeout_ms == 20000  # draft only
    dock.apply()
    assert main_window.connection.timeout_ms == 7000


def test_timeout_spin_range(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.timeout_spin.minimum() >= 1000
    assert dock.timeout_spin.maximum() <= 120000


def test_timeout_restored_from_settings_and_applied_on_apply(main_window, qapp):
    """A stored timeout is reflected into the spinbox at construction; the
    connection is updated only when the user applies (modal contract)."""
    settings.state.set("kicad_timeout_ms", 9000)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.timeout_spin.value() == 9000
    assert main_window.connection.timeout_ms == 20000  # not applied yet
    dock.apply()
    assert main_window.connection.timeout_ms == 9000


# ── Highlight smoke test across the three consumer widgets ────────────────

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


def test_unchecking_rename_confirmation_applies(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.rename_confirmation_checkbox.setChecked(False)
    assert settings.state.get("rename_confirmation_enabled") is None  # draft only
    dock.apply()
    assert settings.state.get("rename_confirmation_enabled") is False
    dock.rename_confirmation_checkbox.setChecked(True)
    dock.apply()
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


def test_toggling_raw_write_applies(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.raw_write_checkbox.setChecked(True)
    assert settings.state.get("mcp_allow_raw_write") is None  # draft only
    dock.apply()
    assert settings.state.get("mcp_allow_raw_write") is True
    dock.raw_write_checkbox.setChecked(False)
    dock.apply()
    assert settings.state.get("mcp_allow_raw_write") is False


def test_raw_write_state_restored_on_recreation(main_window, qapp):
    """The setting survives a dock rebuild (read back from settings.state at
    construction), matching every other key on this page."""
    settings.state.set("mcp_allow_raw_write", True)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.raw_write_checkbox.isChecked()


# ── Draft / Cancel / reload (OK/Cancel/Apply contract) ───────────────────

def test_cancel_discards_draft(main_window, qapp):
    """Cancel (or a reload) re-seeds the widgets from the persisted state, so
    a draft that was never applied can never become the applied state."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.always_on_top_checkbox.setChecked(True)
    dock.raw_write_checkbox.setChecked(True)
    assert settings.state.get("always_on_top") is None  # draft only
    dock.cancel()
    assert not dock.always_on_top_checkbox.isChecked()
    assert not dock.raw_write_checkbox.isChecked()
    assert settings.state.get("always_on_top") is None  # nothing persisted


def test_reload_from_state_reseeds_widgets(main_window, qapp):
    settings.state.set("always_on_top", True)
    settings.state.set("rename_confirmation_enabled", False)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    dock.always_on_top_checkbox.setChecked(False)  # draft
    dock.rename_confirmation_checkbox.setChecked(True)  # draft
    dock.reload_from_state()
    assert dock.always_on_top_checkbox.isChecked()
    assert not dock.rename_confirmation_checkbox.isChecked()


# ── Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ────────

def test_hotkeys_section_has_one_edit_per_registered_action(main_window, qapp):
    """The Settings page lists one QKeySequenceEdit per registered QAction —
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


def test_editing_hotkey_applies_override(main_window, qapp):
    """Rebinding in the Settings browser writes gui_state.json["hotkeys"] and
    re-applies to the live action (set_shortcut's live re-apply) — but only
    on apply(), matching the modal OK/Apply contract."""
    build_action(main_window, "test.hotkey", "Test hotkey", "Ctrl+Shift+Q", None)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    edit = dock.hotkey_edits["test.hotkey"]
    edit.setKeySequence(QKeySequence("Ctrl+Alt+R"))
    assert settings.state.get("hotkeys") is None  # draft only
    dock.apply()
    assert settings.state.get("hotkeys") == {"test.hotkey": "Ctrl+Alt+R"}
    assert hotkeys.get_shortcut("test.hotkey") == QKeySequence("Ctrl+Alt+R")


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


def test_hotkeys_refresh_removes_previous_rows(main_window, qapp):
    """refresh_hotkeys() must actually DELETE the previous rows' widgets, not
    just rebuild the hotkey_edits dict. Each row is added via addLayout (not
    addWidget), so a naive `item.widget()` cleanup returns None for the row and
    never fires — every rebuild (and this method runs TWICE per startup:
    ConfiguratorDock.__init__ + DockHub after _wire) would leave the old
    QKeySequenceEdit/QLabel as orphan children that stack over the rebuilt
    rows (regression for the review finding on 2b87f66)."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QKeySequenceEdit

    # Deterministic: the module-level registry accumulates across tests in
    # this file (and earlier files register root_metadata.* actions).
    hotkeys.HOTKEY_ACTIONS.clear()
    hotkeys._LIVE_ACTIONS.clear()

    build_action(main_window, "test.a", "Action A", "Ctrl+A", None)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert len(dock.findChildren(QKeySequenceEdit)) == 1

    build_action(main_window, "test.b", "Action B", "Ctrl+B", None)
    dock.refresh_hotkeys()
    # Deliver the deleteLater() DeferredDelete events so the old row is gone —
    # processEvents() does NOT deliver DeferredDelete at the outermost loop
    # level, only sendPostedEvents(DeferredDelete) does (verified against the
    # repro from the 2b87f66 review).
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert set(dock.hotkey_edits) == {"test.a", "test.b"}
    assert len(dock.findChildren(QKeySequenceEdit)) == 2  # old row deleted
