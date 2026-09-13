# tests/gui/test_diagnostics_switch_gui.py
"""Э2/Э3/Э5.3 of plan_2026_09_13_diagnostics_switch: the Settings > Diagnostics
page — the two switches that turn the recorders of kicadstamp/diagnostics/ on and
off while the GUI keeps running.

What is pinned here:

  * the page exists as the last category, with two INDEPENDENT checkboxes, and
    simply CONSTRUCTING the dock (or cancelling a draft) starts nothing;
  * apply() — and only apply() — starts/stops the recorders, from the persisted
    state, exactly like every other setting on this dialog;
  * a repeated Apply with a switch already ON does not open a second session (and
    prints no second Log line);
  * the switch survives the dialog being recreated (the persisted state, not the
    widget, is the source of truth);
  * DockHub syncs the recorders ONCE at startup, with the reminder wording, so a
    switch left ON is never silent;
  * the UI-thread predicate Э3 is injected by the GUI together with the switch and
    cleared when recording stops (the recorder itself never imports Qt).

The recorders are PROCESS-WIDE, so the autouse fixture below guarantees that no
test leaves one recording into every later test of the session — and that no test
writes into the repository's gitignored diagnostics/ directory.
"""
import logging

import pytest
from PyQt6.QtWidgets import QLabel

from gui import settings
from gui.docks.configurator import (DIAGNOSTICS_BOARD_CALLS_KEY,
                                    DIAGNOSTICS_BOARD_READS_KEY,
                                    ConfiguratorDock)
from gui.worker import is_ui_thread
from kicadstamp.diagnostics import board_call_timing, board_read_probe


@pytest.fixture(autouse=True)
def recorders_off(tmp_path, monkeypatch):
    """Off-state, tmp logs, and a spied-out install_patch().

    install_patch() patches the LIVE adapter permanently by design (Э1), which is
    covered by tests/test_diagnostics_switch.py — a GUI test has no business
    leaving that patch on the shared class. Everything else is the real
    machinery."""
    for module in (board_call_timing, board_read_probe):
        monkeypatch.setattr(module, "default_log_dir", lambda: str(tmp_path))
        monkeypatch.setattr(module, "_recording", False)
        monkeypatch.setattr(module, "_fh", None)
        monkeypatch.setattr(module, "_path", None)
    monkeypatch.setattr(board_call_timing, "install_patch", lambda target=None: 0)
    monkeypatch.setattr(board_call_timing, "_ui_thread", None)
    yield
    board_call_timing.stop()
    board_call_timing.set_ui_thread_predicate(None)
    board_read_probe.stop()


def _dock(main_window):
    return ConfiguratorDock(main_window, connection=main_window.connection)


# ── The page itself ─────────────────────────────────────────────────────────

def test_page_is_last_and_both_switches_start_off(main_window, qapp):
    dock = _dock(main_window)
    last = dock.tree.topLevelItem(dock.tree.topLevelItemCount() - 1)
    assert last.text(0) == "Diagnostics"
    assert dock.stack.widget(dock.stack.count() - 1) is dock.diagnostics_page
    assert not dock.board_calls_checkbox.isChecked()
    assert not dock.board_reads_checkbox.isChecked()

    hint = "\n".join(lbl.text() for lbl in
                     dock.diagnostics_page.findChildren(QLabel))
    assert "report_board_timing" in hint          # how to read the data
    assert "docs/diagnostics.md" in hint


def test_constructing_and_cancelling_a_draft_starts_nothing(main_window, qapp):
    """The modal contract: a widget change is a draft. Constructor and Cancel
    (reload_from_state) must never start a recording — only apply() does."""
    dock = _dock(main_window)
    dock.board_calls_checkbox.setChecked(True)
    dock.board_reads_checkbox.setChecked(True)
    dock.cancel()

    assert not board_call_timing.is_recording()
    assert not board_read_probe.is_recording()
    assert not dock.board_calls_checkbox.isChecked()


def test_persisted_switches_seed_the_widgets_of_a_recreated_dialog(
        main_window, qapp):
    """Э5.3 — the state survives a recreated dialog (it lives in gui_state.json),
    and seeding the widgets alone still starts nothing."""
    settings.state.set(DIAGNOSTICS_BOARD_CALLS_KEY, True)
    settings.state.set(DIAGNOSTICS_BOARD_READS_KEY, True)

    dock = _dock(main_window)
    assert dock.board_calls_checkbox.isChecked()
    assert dock.board_reads_checkbox.isChecked()
    assert not board_call_timing.is_recording()   # DockHub does that, not this
    assert not board_read_probe.is_recording()


# ── apply(): the switches actually drive the recorders ──────────────────────

def test_apply_starts_both_recorders_and_the_ui_thread_predicate(
        main_window, qapp):
    """Э5.3/Э3 — apply() is what starts a recording, and it is the GUI that hands
    the recorder its UI-thread predicate (the recorder never imports Qt)."""
    dock = _dock(main_window)
    dock.board_calls_checkbox.setChecked(True)
    dock.board_reads_checkbox.setChecked(True)

    assert not board_call_timing.is_recording()   # draft only
    dock.apply()

    assert settings.state.get(DIAGNOSTICS_BOARD_CALLS_KEY) is True
    assert settings.state.get(DIAGNOSTICS_BOARD_READS_KEY) is True
    assert board_call_timing.is_recording()
    assert board_read_probe.is_recording()
    assert board_call_timing._ui_thread is is_ui_thread


def test_unchecking_and_applying_stops_both_recorders(main_window, qapp):
    dock = _dock(main_window)
    dock.board_calls_checkbox.setChecked(True)
    dock.board_reads_checkbox.setChecked(True)
    dock.apply()

    dock.board_calls_checkbox.setChecked(False)
    dock.board_reads_checkbox.setChecked(False)
    dock.apply()

    assert not board_call_timing.is_recording()
    assert not board_read_probe.is_recording()
    assert board_call_timing._ui_thread is None     # nothing is checked anymore
    assert settings.state.get(DIAGNOSTICS_BOARD_CALLS_KEY) is False


def test_repeated_apply_does_not_open_a_second_session(
        main_window, qapp, monkeypatch):
    """A second Apply with the switch already ON must be a no-op — otherwise the
    Log would get a third line for the same session and the file would be opened
    twice (start() is idempotent for exactly this reason)."""
    started = []
    real_start = board_call_timing.start

    def counting_start(path=None, reminder=False):
        started.append(path)
        return real_start(path, reminder)

    monkeypatch.setattr(board_call_timing, "start", counting_start)

    dock = _dock(main_window)
    dock.board_calls_checkbox.setChecked(True)
    dock.apply()
    first_path = started[0]
    dock.apply()
    dock.apply()

    assert started == [first_path]
    assert board_call_timing.is_recording()
    assert board_call_timing.stop() == 0    # nothing was ever recorded


# ── Startup: a forgotten switch is never silent ─────────────────────────────

def test_startup_sync_uses_the_reminder_wording_once(main_window, qapp, caplog):
    """Э2 — the startup path (DockHub calls sync_diagnostics_recording(reminder=
    True)) starts the recording that was found ON and says so, naming the path."""
    settings.state.set(DIAGNOSTICS_BOARD_CALLS_KEY, True)
    dock = _dock(main_window)

    with caplog.at_level(logging.INFO, logger=board_call_timing.__name__):
        dock.sync_diagnostics_recording(reminder=True)
        dock.sync_diagnostics_recording(reminder=True)   # still one line

    lines = [r.getMessage() for r in caplog.records
             if r.name == board_call_timing.__name__]
    assert len(lines) == 1
    assert "left ON in Settings" in lines[0]
    assert board_call_timing.is_recording()


def test_dock_hub_syncs_recording_once_at_startup(monkeypatch, request):
    """The wiring itself: DockHub must ask the ConfiguratorDock to sync the
    recorders once the docks exist, with the startup wording (reminder=True) —
    a switch left ON has to start recording again on the NEXT run, not only when
    the Settings dialog is opened.

    The real MainWindow is requested by name so the spy is in place BEFORE it is
    constructed (request.getfixturevalue registers the fixture's own teardown)."""
    calls = []
    monkeypatch.setattr(ConfiguratorDock, "sync_diagnostics_recording",
                        lambda self, reminder=False: calls.append(reminder))

    request.getfixturevalue("real_main_window")

    assert calls == [True]
