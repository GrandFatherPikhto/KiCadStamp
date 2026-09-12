# tests/gui/test_ui_utils.py
"""Tests for gui/ui_utils.py — the shared busy() context manager used by
PlacerDock/ExtractDock to signal "work in progress" around long synchronous
operations (wait cursor + trigger buttons disabled), plus (2026-09-12, plan
plan_2026_09_12_no_widget_squeezing.md) the two helpers that keep a squeezed
container from squeezing the form inside it: wrap_in_scroll_area() and
resize_dialog_within_screen()."""
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import (QDialog, QFrame, QPlainTextEdit, QPushButton,
                             QScrollArea, QSizePolicy, QWidget)

from gui import ui_utils
from gui.ui_utils import (MinHeightScrollArea, busy, resize_dialog_within_screen,
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
