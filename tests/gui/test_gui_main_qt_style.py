# tests/gui/test_gui_main_qt_style.py
"""Startup Qt-style application — kicadstamp/gui_main.py's apply_saved_qt_style()
(2026-09-03, plan qt_style_setting): a style name persisted in gui_state.json
("qt_style", picked in Settings > Appearance > Style) is applied right after
QApplication creation — but ONLY when it is a non-empty string that exists on
THIS machine/Qt build (QStyleFactory.keys()).

Anything else — absent key, None, empty string, a foreign name (e.g.
gui_state.json synced from another OS), or a non-string value — must be a
silent no-op: no setStyle() call, no exception, keeping today's default
behaviour. Same fatal-safety discipline _restore_window_state already applies
to window_geometry/dock_state.

setStyle is exercised on a tiny fake app object, never on the real test-session
QApplication — so a passing test can't mutate the live style of other tests.
"""
import pytest
from PyQt6.QtWidgets import QStyleFactory

import kicadstamp.gui_main as gui_main
from gui import settings


class _FakeApp:
    """Stands in for the QApplication — records setStyle() calls without
    touching the real test-session style."""

    def __init__(self):
        self.styles = []

    def setStyle(self, name):
        self.styles.append(name)


def test_applies_a_valid_saved_style(qapp):
    """A stored name that exists on THIS build reaches app.setStyle() — the
    whole point of the setting."""
    valid = QStyleFactory.keys()[0]
    settings.state.set("qt_style", valid)
    app = _FakeApp()
    gui_main.apply_saved_qt_style(app)
    assert app.styles == [valid]


@pytest.mark.parametrize("stored", [None, "", "NoSuchStyle_zzz", 42, ["Fusion"]])
def test_invalid_or_foreign_value_is_a_silent_noop(qapp, stored):
    """Absent key, None, empty string, a style this build doesn't know, or a
    non-string value — no setStyle() call at all and no exception."""
    app = _FakeApp()
    settings.state.set("qt_style", stored)
    gui_main.apply_saved_qt_style(app)  # must neither raise nor call setStyle
    assert app.styles == []
