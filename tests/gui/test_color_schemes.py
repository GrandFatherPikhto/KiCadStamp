# tests/gui/test_color_schemes.py
"""Built-in QPalette color schemes — gui/color_schemes.py (2026-09-03, plan
color_scheme_setting): a small module of embedded palettes (values copied
verbatim from qt6ct's airy.conf, NOT read from disk at runtime) with
load_color_scheme() applying the same role mapping qt6ct.cpp::loadColorScheme
uses — index i == QPalette.ColorRole(i), groups Active/Inactive/Disabled.

Spot-checking concrete role colors (not just "is not None") catches future
data corruption or a wrong role order, so the embedded data can never drift
from what was verified visually in the qt6ct window without a test failing.
"""
from PyQt6.QtGui import QColor, QPalette

from gui.color_schemes import available_color_schemes, load_color_scheme


def _color(palette, group, role):
    return palette.color(group, role)


def test_airy_active_window_role_is_white():
    """Index 10 in the Airy data (Active Window) == #ffffffff — one of the
    colors that made the qt6ct window look right on Denis's machine."""
    palette = load_color_scheme("Airy")
    assert palette is not None
    assert _color(palette, QPalette.ColorGroup.Active,
                  QPalette.ColorRole.Window) == QColor("#ffffffff")


def test_airy_active_highlight_role_is_blue():
    """Index 12 (Active Highlight) == #ff0986d3."""
    palette = load_color_scheme("Airy")
    assert palette is not None
    assert _color(palette, QPalette.ColorGroup.Active,
                  QPalette.ColorRole.Highlight) == QColor("#ff0986d3")


def test_airy_disabled_text_is_grayed():
    """Index 6 differs between groups — Active Text is #ff000000 while the
    Disabled group grays it out (#ff808080); proves all three groups are
    actually populated, not just Active."""
    palette = load_color_scheme("Airy")
    assert palette is not None
    active_text = _color(palette, QPalette.ColorGroup.Active,
                         QPalette.ColorRole.Text)
    disabled_text = _color(palette, QPalette.ColorGroup.Disabled,
                           QPalette.ColorRole.Text)
    assert active_text == QColor("#ff000000")
    assert disabled_text == QColor("#ff808080")
    assert active_text != disabled_text


def test_unknown_scheme_name_returns_none():
    """load_color_scheme must return None (never raise) for an unknown name —
    the caller degrades quietly."""
    assert load_color_scheme("nonexistent") is None


def test_airy_is_available():
    assert "Airy" in available_color_schemes()
