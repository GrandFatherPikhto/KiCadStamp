# tests/gui/test_ui_utils.py
"""Tests for gui/ui_utils.py — the shared busy() context manager used by
PlacerDock/ExtractDock to signal "work in progress" around long synchronous
operations (wait cursor + trigger buttons disabled), plus (2026-09-12, plan
plan_2026_09_12_no_widget_squeezing.md) the two helpers that keep a squeezed
container from squeezing the form inside it: wrap_in_scroll_area() and
resize_dialog_within_screen(). The remembered-dialog-size pair lives here too,
so since 2026-09-13 (plan_2026_09_13_dialog_size_saver_crash.md) this file also
guards its event filter against Qt delivering Hide AFTER the dialog's C++ object
is gone — the case that used to abort the process."""
import logging

import PyQt6.sip as sip
from PyQt6.QtCore import QEvent, QRect
from PyQt6.QtWidgets import (QDialog, QFrame, QPlainTextEdit, QPushButton,
                             QScrollArea, QSizePolicy, QWidget)

from gui import settings, ui_utils
from gui.ui_utils import (MinHeightScrollArea, busy, persist_dialog_size,
                          resize_dialog_within_screen, restore_dialog_size,
                          wrap_in_scroll_area)


def test_busy_disables_and_restores_buttons(qapp):
    button = QPushButton()
    assert button.isEnabled()
    with busy((button,)):
        assert not button.isEnabled()
    assert button.isEnabled()


def test_busy_restores_prior_disabled_state(qapp):
    """A button that was already disabled before busy() must stay disabled
    afterwards — busy() must not flip it back on (e.g. Extract's button when
    nothing is selected)."""
    button = QPushButton()
    button.setEnabled(False)
    with busy((button,)):
        assert not button.isEnabled()
    assert not button.isEnabled()


def test_busy_restores_state_on_exception(qapp):
    """Even if the wrapped operation raises, buttons/cursor must be restored
    (the finally branch), so a failed Redraw/Extract doesn't leave the UI
    locked.""" 
    button = QPushButton()
    try:
        with busy((button,)):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert button.isEnabled()


# ── wrap_in_scroll_area (the ONE scroll wrap, 2026-09-12) ───────────────────

def test_wrap_in_scroll_area_installs_every_measured_setting(qapp):
    """The five settings are a measured invariant, not a taste call: without
    them the wrap either does not help (no widgetResizable), grows a frame
    ("распухание") or collapses a whole pane (horizontal `Ignored`)."""
    page = QWidget()
    area = wrap_in_scroll_area(page)

    assert isinstance(area, MinHeightScrollArea)
    assert area.widget() is page
    assert area.widgetResizable() is True
    # 1, not 0 — Qt treats an explicit 0 as "unset" (log_panel.py:157).
    assert area.minimumHeight() == 1
    assert area.frameShape() == QFrame.Shape.NoFrame
    assert area.horizontalScrollBarPolicy().name == "ScrollBarAsNeeded"
    assert area.verticalScrollBarPolicy().name == "ScrollBarAsNeeded"
    assert area.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
    assert area.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored


def test_wrap_in_scroll_area_leaves_a_self_scrolling_widget_alone(qapp):
    """Wrapping a QPlainTextEdit/table/tree in a second scroll area is the
    project's documented anti-pattern (log_panel.py:157, pending.py:240)."""
    view = QPlainTextEdit()
    assert wrap_in_scroll_area(view) is view


def test_scroll_wrap_keeps_the_content_width_floor(qapp):
    """A plain QScrollArea reports a small, content-INDEPENDENT minimum width,
    which once shrank the whole left dock area — the wrap must be horizontally
    transparent."""
    page = QWidget()
    page.setMinimumWidth(300)
    area = wrap_in_scroll_area(page)
    assert area.minimumSizeHint().width() == page.minimumSizeHint().width()
    assert area.minimumSizeHint().height() == 1


# ── resize_dialog_within_screen (Э4, 2026-09-12) ────────────────────────────

class _FakeScreen:
    """Just the one call the helper makes — no QScreen to fake."""

    def __init__(self, width: int, height: int):
        self._available = QRect(0, 0, width, height)

    def availableGeometry(self) -> QRect:
        return self._available


class _Dialog(QDialog):
    """A real dialog with the screen-supplying methods pinned, so the headless
    path is exercised deterministically."""

    def __init__(self, screen):
        super().__init__()
        self._screen = screen

    def screen(self):
        return self._screen


def test_resize_dialog_keeps_the_desired_size_when_it_fits(qapp):
    dialog = _Dialog(_FakeScreen(1920, 1080))
    resize_dialog_within_screen(dialog, 780, 540)
    assert (dialog.width(), dialog.height()) == (780, 540)


def test_resize_dialog_is_capped_by_the_available_geometry(qapp):
    """620 px plus the window frame and the task bar does not fit a laptop
    screen at 125 % scaling — the dialog would hang off the display."""
    dialog = _Dialog(_FakeScreen(1024, 600))
    resize_dialog_within_screen(dialog, 780, 620)
    assert (dialog.width(), dialog.height()) == (780, 600)


def test_resize_dialog_without_a_screen_just_resizes(qapp, monkeypatch):
    """`self.screen()` is None before the window is shown and primaryScreen()
    can be None headless — the helper must degrade to a plain resize, not
    crash."""
    monkeypatch.setattr(ui_utils, "_primary_screen", lambda: None)
    dialog = _Dialog(None)
    resize_dialog_within_screen(dialog, 520, 560)
    assert (dialog.width(), dialog.height()) == (520, 560)


# ── remembered dialog sizes (Э3, 2026-09-12) ────────────────────────────────

def test_dialog_size_key_is_the_class_name(qapp):
    """The key is the CLASS name, never the (translated, changing) title."""
    dialog = _Dialog(None)
    assert ui_utils.dialog_size_key(dialog) == "dialog_size:_Dialog"
    assert ui_utils.dialog_size_key(dialog, "Other") == "dialog_size:Other"


def test_persist_dialog_size_stores_the_size_on_hide(qapp):
    """The stored size is the one the dialog was LEFT at — its size when it is
    hidden (the one close path every dialog has, modal or not)."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    assert settings.state.get("dialog_size:_Dialog") is None  # nothing yet

    dialog.resize(640, 480)
    dialog.show()
    expected = [dialog.width(), dialog.height()]
    dialog.hide()
    assert settings.state.get("dialog_size:_Dialog") == expected


def test_restore_dialog_size_prefers_the_remembered_size(qapp):
    settings.state.set("dialog_size:_Dialog", [700, 500])
    dialog = _Dialog(_FakeScreen(1920, 1080))
    restore_dialog_size(dialog, 720, 600)
    assert (dialog.width(), dialog.height()) == (700, 500)


def test_restore_dialog_size_uses_the_default_without_a_remembered_one(qapp):
    dialog = _Dialog(_FakeScreen(1920, 1080))
    restore_dialog_size(dialog, 720, 600)
    assert (dialog.width(), dialog.height()) == (720, 600)


def test_restore_dialog_size_without_default_or_remembered_leaves_qt_alone(qapp):
    """A dialog with no size constant keeps Qt's own size — restoring must not
    invent one."""
    dialog = _Dialog(None)
    dialog.resize(333, 222)
    restore_dialog_size(dialog)
    assert (dialog.width(), dialog.height()) == (333, 222)


def test_restore_dialog_size_is_capped_by_the_screen(qapp, monkeypatch):
    """Э3.2: 780x900 saved on a big monitor must not hang off a laptop screen.
    The remembered size goes through the SAME cap a constant does, and the cap
    stays reachable through the _primary_screen seam."""
    monkeypatch.setattr(ui_utils, "_primary_screen",
                        lambda: _FakeScreen(1024, 600))
    settings.state.set("dialog_size:_Dialog", [780, 900])
    dialog = _Dialog(None)      # dialog.screen() is None -> the seam is used
    restore_dialog_size(dialog, 720, 600)
    assert (dialog.width(), dialog.height()) == (780, 600)


def test_restore_dialog_size_ignores_a_corrupt_remembered_value(qapp):
    """A hand-edited/corrupt gui_state.json must never keep a dialog from
    opening — a bad size is simply "nothing remembered"."""
    settings.state.set("dialog_size:_Dialog", ["x", None])
    dialog = _Dialog(None)
    dialog.resize(333, 222)
    restore_dialog_size(dialog)
    assert (dialog.width(), dialog.height()) == (333, 222)


def test_restore_dialog_size_rejects_a_nonpositive_remembered_value(qapp):
    settings.state.set("dialog_size:_Dialog", [0, 0])
    dialog = _Dialog(None)
    dialog.resize(333, 222)
    restore_dialog_size(dialog)
    assert (dialog.width(), dialog.height()) == (333, 222)


# ── the size saver must survive its dialog (2026-09-13) ─────────────────────
#
# plan_2026_09_13_dialog_size_saver_crash.md: Qt delivers Hide from the
# dialog's OWN destruction path, i.e. after the C++ object is gone while the
# Python wrapper (held by the filter's own `self._dialog`) is still alive.
# QWidget.width() then raises RuntimeError from inside Qt's C++ event dispatch,
# where nothing can catch it — Qt aborts the process (measured: 4 aborted runs
# of 5 on pytest tests/gui/test_phase3_wiring.py). These are the guards.

def test_size_saver_never_touches_a_deleted_dialog(qapp):
    """The dialog's C++ object is gone, the filter is still asked about
    Hide/Close: save() must be a no-op, not a RuntimeError.

    sip.delete() is the deterministic form of what the crash does with timing
    (diagnostics/probe_deleted_dialog_hide_filter.py shows the same state,
    isdeleted == True, reached by Qt itself): the C++ object goes, both Python
    wrappers stay, and the filter gets called."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    saver = dialog._dialog_size_saver

    sip.delete(dialog)
    assert sip.isdeleted(dialog)

    saver.save()                                   # pre-fix: RuntimeError
    assert saver.eventFilter(dialog, QEvent(QEvent.Type.Hide)) is False
    assert saver.eventFilter(dialog, QEvent(QEvent.Type.Close)) is False
    assert settings.state.get("dialog_size:_Dialog") is None


def test_size_saver_survives_qt_destroying_the_dialog(qapp):
    """The same guard through Qt's own destruction path — deleteLater() plus a
    DeferredDelete drain, i.e. what the failing run does when the dialog's
    wrapper is collected while a Hide is still on its way to the filter."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    saver = dialog._dialog_size_saver
    dialog.resize(660, 500)
    dialog.show()
    qapp.processEvents()

    dialog.deleteLater()
    # deleteLater() alone is not enough — the DeferredDelete event has to be
    # drained explicitly (same gotcha as plan_2026_09_13_widget_teardown_leak;
    # a plain processEvents() does not deliver it). Qt6 dropped the
    # ProcessEventsFlag spelling for this, so sendPostedEvents is the way.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(dialog)

    # Whatever Qt still delivers from here on must be a no-op.
    qapp.processEvents()
    assert saver.eventFilter(dialog, QEvent(QEvent.Type.Hide)) is False
    assert settings.state.get("dialog_size:_Dialog") is None


def test_size_saver_still_stores_a_live_dialogs_size(qapp):
    """The counter-guard: a live dialog's Hide MUST still store the size — the
    dead-object check must not degrade into "never save anything"."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    saver = dialog._dialog_size_saver
    dialog.resize(640, 480)
    dialog.show()
    qapp.processEvents()
    expected = [dialog.width(), dialog.height()]

    assert saver.eventFilter(dialog, QEvent(QEvent.Type.Hide)) is False
    assert settings.state.get("dialog_size:_Dialog") == expected


def test_size_saver_observes_without_swallowing_other_events(qapp):
    """The filter observes only: it returns False for everything (it has no
    right to eat events) and stores nothing for an event that is not a
    Hide/Close."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    saver = dialog._dialog_size_saver
    dialog.resize(640, 480)
    dialog.show()
    qapp.processEvents()

    assert saver.eventFilter(dialog, QEvent(QEvent.Type.Show)) is False
    assert settings.state.get("dialog_size:_Dialog") is None


# ── a failing settings write must not reach Qt's C++ stack (Х2, 2026-09-14) ──
#
# plan_2026_09_13_three_unsentinelled_guards Х2: `try/except Exception` +
# logger.exception around settings.state.set() is the SECOND arm of the
# "nothing leaves eventFilter" contract — the first arm (sip.isdeleted, above)
# covers the dead dialog, this one covers a settings write that raises (a JSON
# payload it cannot serialise, a file it cannot decode). Qt answers an exception
# escaping an event filter with abort(), so removing this try/except is
# invisible to the four guards above: measured 2026-09-14 — only 21 tests green.

def test_size_saver_survives_a_failing_settings_write(qapp, monkeypatch, caplog):
    """A settings.state.set() that RAISES must neither escape eventFilter (Qt
    aborts the process when one does) nor be silently swallowed — it has to be
    logged, so that "log instead of abort" is not degraded into "pass".

    The contract is about the EXIT FROM eventFilter, not from save(): that is
    where the exception would meet Qt's C++ dispatch, so the assertion is made
    on the filter's return value."""
    dialog = _Dialog(_FakeScreen(1920, 1080))
    persist_dialog_size(dialog)
    saver = dialog._dialog_size_saver
    dialog.resize(640, 480)
    dialog.show()
    qapp.processEvents()

    def _refuse_to_write(key, value):
        raise RuntimeError("settings file is not even decodable")

    monkeypatch.setattr(settings.state, "set", _refuse_to_write)

    with caplog.at_level(logging.ERROR):
        assert saver.eventFilter(dialog, QEvent(QEvent.Type.Hide)) is False
        assert saver.eventFilter(dialog, QEvent(QEvent.Type.Close)) is False

    assert settings.state.get("dialog_size:_Dialog") is None
    assert any("Cannot remember the size of dialog key" in record.getMessage()
               and "dialog_size:_Dialog" in record.getMessage()
               for record in caplog.records), (
        "the failed settings write was swallowed silently — it must be LOGGED "
        "(logger.exception), or 'log instead of abort' becomes 'pass instead "
        "of abort': " + repr([r.getMessage() for r in caplog.records]))
