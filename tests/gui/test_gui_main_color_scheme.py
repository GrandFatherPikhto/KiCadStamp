# tests/gui/test_gui_main_color_scheme.py
"""Startup color-scheme application — kicadstamp/gui_main.py's
apply_saved_color_scheme() (2026-09-03, plan color_scheme_setting): a built-in
color scheme name persisted in gui_state.json ("color_scheme", picked in
Settings > Appearance > Color scheme) is applied right after QApplication
creation (after the qt_style override) — but ONLY when it names a known
built-in scheme (gui.color_schemes.available_color_schemes()).

Anything else — absent key, None, empty string, a foreign name (a scheme that
does not exist in this build/version), or a non-string value — must be a
silent no-op: no setPalette() call, no exception, keeping today's default
behaviour. Same fatal-safety discipline apply_saved_qt_style applies to
qt_style (see test_gui_main_qt_style.py).

setPalette is exercised on a tiny fake app object, never on the real
test-session QApplication — so a passing test can't mutate the live palette of
other tests. The ONE exception is the snapshot round-trip test, which restores
the palette in a finally block.
"""
import pytest
from PyQt6.QtGui import QColor, QPalette

import kicadstamp.gui_main as gui_main
from gui import settings
from gui.color_schemes import load_color_scheme


class _FakeApp:
    """Stands in for the QApplication — records setPalette() calls without
    touching the real test-session palette."""

    def __init__(self):
        self.palettes = []

    def setPalette(self, palette):
        self.palettes.append(palette)


def test_applies_a_valid_saved_scheme(qapp):
    """A stored name that names a known built-in scheme reaches
    app.setPalette() with that scheme's QPalette — the whole point of the
    setting."""
    settings.state.set("color_scheme", "Airy")
    app = _FakeApp()
    gui_main.apply_saved_color_scheme(app)
    assert len(app.palettes) == 1
    applied = app.palettes[0]
    # Spot-check concrete roles from the Airy data (plan §1) — the applied
    # palette must be exactly the built-in Airy one, not "any palette".
    assert applied.color(QPalette.ColorGroup.Active,
                         QPalette.ColorRole.Window) == QColor("#ffffffff")
    assert applied.color(QPalette.ColorGroup.Active,
                         QPalette.ColorRole.Highlight) == QColor("#ff0986d3")


@pytest.mark.parametrize("stored", [None, "", "NoSuchScheme_zzz", 42, ["Airy"]])
def test_invalid_or_foreign_value_is_a_silent_noop(qapp, stored):
    """Absent key, None, empty string, a scheme this build doesn't know, or a
    non-string value — no setPalette() call at all and no exception."""
    app = _FakeApp()
    settings.state.set("color_scheme", stored)
    gui_main.apply_saved_color_scheme(app)  # must neither raise nor call setPalette
    assert app.palettes == []


def test_original_palette_snapshot_survives_an_override(qapp):
    """The rollback contract ConfiguratorDock.apply()'s "None" branch relies
    on (see design §2.3): gui_main.main() snapshots the pristine palette as the
    app's "original_palette" dynamic property BEFORE any override. This test
    mirrors those first lines on the real session app: overriding the palette
    must not change the snapshot, and restoring it yields the pristine
    pre-override Window role again. The palette is restored in a finally block
    so no other test sees the Airy override."""
    pristine = qapp.palette()
    qapp.setProperty("original_palette", pristine)
    try:
        qapp.setPalette(load_color_scheme("Airy"))
        original = qapp.property("original_palette")
        assert original is not None
        qapp.setPalette(original)
        window_before = pristine.color(QPalette.ColorGroup.Active,
                                       QPalette.ColorRole.Window)
        window_after = qapp.palette().color(QPalette.ColorGroup.Active,
                                            QPalette.ColorRole.Window)
        assert window_after == window_before
    finally:
        qapp.setPalette(pristine)
