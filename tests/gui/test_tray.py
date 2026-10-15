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

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox, QSystemTrayIcon

from gui import settings
from kicadstamp.config_working_set import WORKING_SET


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
    """open_fieldstool shows/raises the Components dock, which hosts the
    embedded fieldstool pane (2026-09-05 master-detail)."""
    real_main_window.hide()
    real_main_window.open_fieldstool()
    assert real_main_window.isVisible()
    assert real_main_window.tree_dock.isVisible()


def test_quit_persists_and_calls_application_quit(real_main_window, monkeypatch, qapp):
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)
    quit_mock = Mock()
    monkeypatch.setattr(qapp, "quit", quit_mock)

    real_main_window._quit()

    persist.assert_called_once()
    quit_mock.assert_called_once()


# ── У1 (2026-10-15, Ч1 of plan_2026_10_15_extract_and_read_paths_cleanup) ──────
# The ONE unsaved-changes question of the way OUT of the program: asked by the
# title-bar X (the real-quit branch) and by _quit (tray Quit / File > Quit),
# BEFORE the "Вернуть и выйти" of the exploded clusters, and NEVER on the
# hide-to-tray path (the process lives on, no staged edit is lost).
#
# Every cell below drives the EXIT ENTRY POINT itself — that is the property:
# which way out asks, in which order, and what a Cancel does. The question's own
# wording and dialog belong to their ONE host (RootMetadataDock._confirm_discard_
# changes), so it is scripted here, exactly like
# test_file_menu_close_guard_respects_unsaved_changes does for the close path.


def _guard_stand_in(window, monkeypatch, answer):
    """Script the ONE guard host and return the list of its calls."""
    calls = []

    def _guard():
        calls.append(True)
        return answer

    monkeypatch.setattr(window.root_metadata_dock,
                        "_confirm_discard_changes", _guard)
    return calls


def _dirty(monkeypatch):
    """A dirty staged working set — the state every asking cell needs.

    is_dirty is PATCHED, not staged: these cells are about the exit path, and a
    real staged write would drag the config files (and their own guards) into the
    cell. The one cell that must see a REAL dirty set stages it for real (the
    non-destructive Discard cell below)."""
    monkeypatch.setattr(WORKING_SET, "is_dirty", lambda: True)


def _close_event(window):
    """Drive MainWindow.closeEvent as Qt does, with a fresh QCloseEvent.

    QCloseEvent starts ACCEPTED, so `event.isAccepted()` is False only when the
    handler called event.ignore() — the exact thing a cancelled exit must do."""
    event = QCloseEvent()
    window.closeEvent(event)
    return event


def test_close_event_asks_nothing_when_the_working_set_is_clean(
        real_main_window, monkeypatch):
    """Ч1-клетка 1: a clean working set closes with NO question — the guard host's
    own is_dirty() fast path answers True without a dialog. Runs the REAL host, so
    a dialog opened here would trip the autouse no-modal guard."""
    monkeypatch.setattr(WORKING_SET, "is_dirty", lambda: False)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    event = _close_event(real_main_window)

    assert event.isAccepted()
    persist.assert_called_once()


def test_close_event_asks_about_unsaved_changes_and_a_cancel_keeps_the_window(
        real_main_window, monkeypatch):
    """Ч1-клетка 4: the X with a dirty working set asks the ONE unsaved-changes
    question, and Cancel (False — the user's own Cancel, or a Save that FAILED,
    which the guard reports the same way) cancels the quit: the window stays and
    nothing is persisted.

    Mutation: drop the call in closeEvent -> red (no ask, the window closes)."""
    _dirty(monkeypatch)
    calls = _guard_stand_in(real_main_window, monkeypatch, answer=False)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    event = _close_event(real_main_window)

    assert calls, "closeEvent never asked about the unsaved changes"
    assert not event.isAccepted()
    persist.assert_not_called()


def test_close_event_closes_once_the_unsaved_changes_were_confirmed(
        real_main_window, monkeypatch):
    """Ч1-клетка 2: a confirmed answer (saved, or deliberately discarded) lets the
    real quit run — the window closes and the settings are persisted."""
    _dirty(monkeypatch)
    _guard_stand_in(real_main_window, monkeypatch, answer=True)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    event = _close_event(real_main_window)

    assert event.isAccepted()
    persist.assert_called_once()


def test_hiding_to_the_tray_never_asks_about_unsaved_changes(
        real_main_window, monkeypatch):
    """Ч1-клетка 10: with the tray enabled the X HIDES the window and asks nothing
    — the process keeps running and no staged edit is lost, so the question would
    be a lie. Mutation: ask on the tray branch -> red."""
    settings.state.set("tray_enabled", True)
    _dirty(monkeypatch)
    calls = _guard_stand_in(real_main_window, monkeypatch, answer=False)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    event = _close_event(real_main_window)

    assert calls == [], "the hide-to-tray branch must never ask"
    assert not event.isAccepted()
    assert real_main_window.isHidden()
    persist.assert_not_called()


def test_quit_asks_about_unsaved_changes_and_a_cancel_does_not_quit(
        real_main_window, monkeypatch, qapp):
    """Ч1-клетка 7: the tray/File Quit asks the SAME question, and Cancel does not
    quit — _do_quit is never reached.

    Mutation: drop the call in _quit -> red (the quit runs and the edits die)."""
    _dirty(monkeypatch)
    calls = _guard_stand_in(real_main_window, monkeypatch, answer=False)
    do_quit = Mock()
    monkeypatch.setattr(real_main_window, "_do_quit", do_quit)

    real_main_window._quit()

    assert calls, "_quit never asked about the unsaved changes"
    do_quit.assert_not_called()


def test_quit_quits_once_the_unsaved_changes_were_confirmed(
        real_main_window, monkeypatch, qapp):
    """Ч1-клетка 8: a confirmed answer lets the quit through — the settings are
    persisted and QApplication.quit() is called."""
    _dirty(monkeypatch)
    _guard_stand_in(real_main_window, monkeypatch, answer=True)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)
    quit_mock = Mock()
    monkeypatch.setattr(qapp, "quit", quit_mock)

    real_main_window._quit()

    persist.assert_called_once()
    quit_mock.assert_called_once()


def test_the_exit_question_is_asked_once_when_the_clusters_were_just_returned(
        real_main_window, monkeypatch):
    """Ч1-клетка 11: the "Вернуть и выйти" restore re-enters the exit through its
    own proceed callback (self.close()), and that SECOND entry must not ask about
    the unsaved changes again. `_explode_exit_ok` (set right before proceed) is
    what tells the guard that this very way out was already asked.

    Mutation: drop the _explode_exit_ok fast path -> red (asked twice)."""
    _dirty(monkeypatch)
    calls = _guard_stand_in(real_main_window, monkeypatch, answer=False)
    real_main_window._explode_exit_ok = True      # the restore just succeeded
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    event = _close_event(real_main_window)

    assert calls == [], "the re-entry asked the unsaved-changes question twice"
    assert event.isAccepted()
    persist.assert_called_once()


def test_a_cancelled_exit_asks_about_unsaved_changes_again(
        real_main_window, monkeypatch):
    """Ч1-клетка 12 (the other half): a Cancel of the clusters question cancels the
    way out and leaves `_explode_exit_ok` False, so the NEXT attempt asks about the
    unsaved changes again — the cancelled exit's answer is never reused."""
    _dirty(monkeypatch)
    calls = _guard_stand_in(real_main_window, monkeypatch, answer=True)
    monkeypatch.setattr(real_main_window, "_return_clusters_before_quit",
                        lambda proceed: False)
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)

    assert not _close_event(real_main_window).isAccepted()
    assert not _close_event(real_main_window).isAccepted()

    assert len(calls) == 2, "the next attempt reused the cancelled one's answer"
    persist.assert_not_called()


def test_discarding_on_the_way_out_keeps_the_staged_edits_when_the_exit_is_cancelled(
        real_main_window, monkeypatch, tmp_path, qapp):
    """Ч1-клетка 12 (the non-destructive half): with a REAL dirty working set, the
    REAL guard's Discard branch only RETURNS True — clearing the set is the root
    switch's job — so "Discard" followed by a Cancel of the clusters question
    leaves the staged edits exactly where they were (the process is still alive,
    and nothing the user did not choose was dropped).

    Mutation: clear the working set from the exit path on Discard -> red."""
    root = tmp_path / "root.sexp"
    root.write_text("(kicadstamp-config)\n", encoding="utf-8")
    WORKING_SET.stage_write(root, {"cells": {"c1": {}}})
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))
    monkeypatch.setattr(real_main_window, "_return_clusters_before_quit",
                        lambda proceed: False)      # the clusters question cancelled
    persist = Mock()
    monkeypatch.setattr(real_main_window, "_persist_settings", persist)
    try:
        event = _close_event(real_main_window)

        assert not event.isAccepted()
        assert WORKING_SET.is_dirty(), \
            "Discard on the way out must not clear the working set"
        persist.assert_not_called()
    finally:
        WORKING_SET.clear()
