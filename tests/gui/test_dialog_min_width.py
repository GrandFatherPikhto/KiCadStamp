# tests/gui/test_dialog_min_width.py
"""Horizontal counterpart of tests/gui/test_no_widget_height_squeezing.py
(2026-09-17, plan plan_2026_09_17_cell_dialog_min_width.md).

Denis, live: "Это поправить надо!" — the Cell dialog could not be made narrower
than 1310 px on Windows (measured `minimumSizeHint()`; Qt never lays a widget out
below it, so neither `resize()` nor `resize_dialog_within_screen` can help). On a
1366x768 laptop the dialog hung over the screen edge with part of the buttons
unreachable. Measured cause: ONE row of three long-captioned buttons inside
CellDock — 386 + 410 + 494 px, i.e. a 1290 px QHBoxLayout (the tabs need 502 px,
they were never to blame).

Р4 of the plan sets the width floor for dialogs and docks at 1000 px: 1366x768 is
the smallest screen the project must live on, and 1000 leaves room for the window
frame and the taskbar. Р1 moves the meaning of each button into its tooltip and
keeps the caption to two or three words. Р7: the guard runs in BOTH catalogues —
Denis works in Russian, so an English-only number would prove nothing.

Р7's technical consequence is the reason the Russian half is a SUBPROCESS: a GUI
module binds `_` at import time (`from kicadstamp.i18n import _`), so by the time
a test runs, `LANGUAGE=ru` can no longer reach it. `probe_gui_min_sizes --json`
does the ordering right in its own process, and this file only reads its numbers —
one source of truth for the measurement instead of a second widget-building copy.

Measured with the probe on `ecc29e5` (Windows 11, offscreen, PyQt 6.11, base of
this task): CellDialog 1310 px (en) / 1226 px (ru); the next widest widget in the
whole app is RoleClusterTreeDock at 898 px (en) / 970 px (ru) — so the Cell dialog
was the only offender, and this guard stays on it (plan §4).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QPushButton

from gui.docks.cell_dialog import CellDialog
from gui.docks.cell_editor import CellDock

# Р4: 1366x768 is the smallest screen the project must live on; 1000 px leaves
# room for the window frame and the taskbar. ONE constant, as the plan asks.
MAX_DIALOG_WIDTH_PX = 1000

_ROOT = Path(__file__).resolve().parents[2]

# The three buttons of the row (gui/docks/cell_editor.py). Captions are Denis's
# words of 2026-09-17; the tooltip carries the phrase the button used to be named
# with — that is the whole point of Р1, so both halves are pinned here.
SHORT_CAPTIONS = {
    "refresh_geometry_button": ("Refresh geometry", "Перечитать геометрию"),
    "import_vias_tracks_button": ("Import copper", "Импорт меди"),
    "select_cluster_button": ("Select cluster", "Выделить кластер"),
}
FULL_CAPTIONS = {
    "refresh_geometry_button": ("Refresh geometry from selection",
                                "Обновить геометрию по выделению"),
    "import_vias_tracks_button": ("Import vias/tracks from selection",
                                  "Импорт via/track из выделения"),
    "select_cluster_button": ("Select cluster of this cell on the board",
                              "Выделить на плате кластер этой ячейки"),
}

_RU_REPORT: dict | None = None


def _build_cell_dialog(main_window):
    """The Cell dialog exactly as the app opens it: the live CellDock inside its
    thin dialog shell. `main_window` is passed as the Qt parent ON PURPOSE — a
    parentless widget is collected by the garbage collector and on Windows that
    takes the whole pytest process down (plan 2а §1.1)."""
    return CellDialog(CellDock(main_window), main_window)


def ru_report() -> dict:
    """The probe's report in Russian, from a SUBPROCESS (see the module docstring).

    `LANGUAGE` is put in the child's environment before it imports anything, and
    the probe itself calls setup_i18n() before importing gui.*; nothing of the sort
    can be done inside this already-running process."""
    global _RU_REPORT
    if _RU_REPORT is not None:
        return _RU_REPORT

    out = Path(tempfile.mkdtemp(prefix="cell_dialog_min_width_")) / "min_sizes_ru.json"
    env = dict(os.environ)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        env.pop(var, None)
    env["LANGUAGE"] = "ru"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-m", "kicadstamp.diagnostics.probe_gui_min_sizes",
         "--lang", "ru", "--json", str(out)],
        cwd=str(_ROOT), env=env, capture_output=True, timeout=300)
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"the ru probe failed:\n{output}"
    assert out.exists(), f"the ru probe wrote no JSON:\n{output}"

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["language"] == "ru", (
        f"the probe did not switch catalogue: LANGUAGE={report['language_env']!r}, "
        f"language={report['language']!r}")
    _RU_REPORT = report
    return report


def _ru_button(report: dict, attr: str) -> dict:
    for button in report["cell_dialog_buttons"]:
        if button.get("attr") == attr:
            assert not button.get("missing"), f"{attr} is missing from CellDock"
            return button
    raise AssertionError(f"{attr} was not measured by the probe")


# ── С1: the dialog fits a laptop screen, in both catalogues ────────────────

def test_cell_dialog_minimum_width_fits_the_screen(main_window):
    """С1: the dialog's own floor is what decides whether it can be placed on a
    1366x768 screen at all — Qt ignores every attempt to go below it."""
    dialog = _build_cell_dialog(main_window)
    width = dialog.minimumSizeHint().width()
    assert width <= MAX_DIALOG_WIDTH_PX, (
        f"CellDialog demands {width} px of width (limit {MAX_DIALOG_WIDTH_PX}) — "
        f"on a 1366x768 laptop it hangs over the screen edge; the row of three "
        f"buttons is the usual reason (see probe_gui_min_sizes.py)")


def test_cell_dialog_minimum_width_fits_the_screen_in_russian():
    """С1, the Russian half (Р7) — via the probe subprocess, because Denis works
    in Russian and only the ru catalogue can prove the ru numbers."""
    measured = ru_report()["cell_dialog"]
    assert measured is not None, "the ru probe did not measure CellDialog"
    assert measured["min_width"] <= MAX_DIALOG_WIDTH_PX, (
        f"CellDialog demands {measured['min_width']} px of width in Russian "
        f"(limit {MAX_DIALOG_WIDTH_PX})")


# ── С2: short caption, meaning in the tooltip, in both catalogues ──────────

def test_every_button_has_a_short_caption_and_the_full_phrase_on_hover(main_window):
    """С2: Р1 trades the long caption for a tooltip, and that trade is only honest
    while the tooltip actually carries the phrase the caption gave up."""
    dock = CellDock(main_window)
    for attr, captions in SHORT_CAPTIONS.items():
        button = getattr(dock, attr, None)
        assert isinstance(button, QPushButton), f"{attr} is not a button"
        assert button.text() in captions, (
            f"{attr} caption is {button.text()!r}, expected one of {captions!r}")
        tooltip = button.toolTip()
        assert tooltip, f"{attr} lost its tooltip — the full phrase lives nowhere"
        assert tooltip in FULL_CAPTIONS[attr], (
            f"{attr} tooltip is {tooltip!r}; it must be the phrase the button used "
            f"to be named with, i.e. one of {FULL_CAPTIONS[attr]!r}")


def test_every_button_is_short_and_explained_in_russian_too():
    """С2, the Russian half (Р7): short Russian captions are a REQUIREMENT (a long
    translation would defeat the whole task on Denis's screen), and the Russian
    tooltip is the full Russian phrase."""
    report = ru_report()
    for attr, captions in SHORT_CAPTIONS.items():
        button = _ru_button(report, attr)
        assert button["text"] in captions, (
            f"{attr} caption is {button['text']!r} in Russian, "
            f"expected one of {captions!r} — long captions are the bug")
        assert button["tooltip"], f"{attr} has no tooltip in the ru catalogue"
        assert button["tooltip"] in FULL_CAPTIONS[attr], (
            f"{attr} tooltip is {button['tooltip']!r} in Russian, expected one of "
            f"{FULL_CAPTIONS[attr]!r}")


# ── С4: a Log line that sends you to a button names the button as it looks ──

def test_the_hint_lines_name_the_caption_the_button_actually_shows(main_window,
                                                                   caplog):
    """С4/Р6: two success lines tell the user which button to press next. Sending
    them to a caption that is no longer on screen is the same untruth the caption
    change was made to remove, so the line must quote the CURRENT caption.

    The caption is read off the WIDGET, not written into the test twice: rename
    the button without touching these lines and this guard fails, which is the
    whole point (Р6 exists because code and message drifted apart)."""
    dock = CellDock(main_window)
    caption = dock.refresh_geometry_button.text()
    assert caption, "the button has no caption at all"

    caplog.clear()
    dock._finish_select_cluster_on_board({"identified": True, "selected": 2})
    identified = caplog.text
    caplog.clear()
    dock._finish_select_cluster_on_board({"selected": 4,
                                          "cluster": "MCU_PWR_BANK"})
    by_cluster = caplog.text

    for message, where in ((identified, "identified refs"),
                           (by_cluster, "cluster by name")):
        assert message.strip(), f"the {where} branch logged nothing"
        assert caption in message, (
            f"the {where} Log line does not name the button's current caption "
            f"{caption!r}: {message!r}")
        for old_phrase in FULL_CAPTIONS["refresh_geometry_button"]:
            assert old_phrase not in message, (
                f"the {where} Log line still sends the user to the caption that "
                f"no longer exists ({old_phrase!r}): {message!r}")


if __name__ == "__main__":  # pragma: no cover — by hand, like the probes
    raise SystemExit(pytest.main([__file__, "-v"]))
