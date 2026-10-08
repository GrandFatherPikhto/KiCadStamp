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

Since 08.10.2026 the two cells CARRY THE EVIDENCE (Ш1-2..Ш1-6 of
plan_2026_10_08_cell_dialog_width_windows): the Cell dialog is 1106 px on the
Windows runner and 337 px here, so the failing platform is the only probe the
project has (no Windows dev machine, and `skipif win32` is forbidden). Both
messages are assembled by the probe's own formatters — the en half from the live
dialog, the ru half from the probe's JSON — and both name the layout ROWS
(items + gaps + margins: that is how the three-button row of `ecc29e5` came to
1290 px) and the widest VISIBLE descendants. Three measured facts shape the
block, and two of them would otherwise answer with nothing:

* `isVisible()` is False for EVERY descendant of a never-shown dialog (measured:
  0 of 300), so the filter is `isVisibleTo(offender)` (45 of 300 survive) and the
  dropped count is reported — the rows keep what the filter drops, because a
  QTabWidget's minimum is computed over ALL its pages;
* a row sum without the gaps reads 295 px for a row that costs 307 px (and the
  dialog 337 px), i.e. it stays under a limit the offender is over;
* the "≥ 60 % of the offender" share is a FLAG, never a filter: the holders on
  `ecc29e5` were 386/410/494 out of 1310 px — 29/31/38 %.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from gui.docks.cell_dialog import CellDialog
from gui.docks.cell_editor import CellDock

# The evidence formatters live in the probe, not here: the ru half reads the SAME
# block out of the probe's JSON, and two copies of the wording would drift apart.
# Ф2.0: depth-independent (tests/paths.py).
from kicadstamp.diagnostics.probe_gui_min_sizes import (
    descendants_visibility, format_offender_evidence, format_widest_widgets,
    guilty_rows, platform_context, widest_descendants)
from tests.paths import REPO_ROOT as _ROOT

# Р4: 1366x768 is the smallest screen the project must live on; 1000 px leaves
# room for the window frame and the taskbar. ONE constant, as the plan asks.
MAX_DIALOG_WIDTH_PX = 1000

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


# ── С1: the dialog fits a laptop screen, in both catalogues ────────────────

def test_cell_dialog_minimum_width_fits_the_screen(main_window):
    """С1: the dialog's own floor is what decides whether it can be placed on a
    1366x768 screen at all — Qt ignores every attempt to go below it.

    This message is the only place a WINDOWS-only offender can be named: the same
    dialog measures 337 px here and 1106 px on the runner, so the failure prints
    the layout rows and the widest visible descendants rather than asking for a
    probe run on a platform the project does not own."""
    dialog = _build_cell_dialog(main_window)
    width = dialog.minimumSizeHint().width()
    assert width <= MAX_DIALOG_WIDTH_PX, (
        f"CellDialog demands {width} px of width (limit {MAX_DIALOG_WIDTH_PX}) — "
        f"on a 1366x768 laptop it hangs over the screen edge; the row of three "
        f"buttons is the usual reason:\n"
        + format_offender_evidence(
            width=width, limit=MAX_DIALOG_WIDTH_PX,
            rows=guilty_rows(dialog, dialog, MAX_DIALOG_WIDTH_PX),
            widest=widest_descendants(dialog, dialog, MAX_DIALOG_WIDTH_PX),
            visibility=descendants_visibility(dialog),
            context=platform_context("en")))


def test_cell_dialog_minimum_width_fits_the_screen_in_russian():
    """С1, the Russian half (Р7) — via the probe subprocess, because Denis works
    in Russian and only the ru catalogue can prove the ru numbers.

    The evidence comes out of the probe's JSON: on the platform where this cell
    fails the probe has already computed the rows and the descendants (it does so
    for every offender, and `isVisibleTo` is measured against the offender), and
    `format_widest_widgets()` adds the app-wide table — the difference between ONE
    stretched widget and a platform-wide change, which are fixed in different
    places."""
    report = ru_report()
    measured = report["cell_dialog"]
    assert measured is not None, "the ru probe did not measure CellDialog"
    width = measured["min_width"]
    assert width <= MAX_DIALOG_WIDTH_PX, (
        f"CellDialog demands {width} px of width in Russian "
        f"(limit {MAX_DIALOG_WIDTH_PX}):\n"
        + format_offender_evidence(
            width=width, limit=MAX_DIALOG_WIDTH_PX,
            rows=measured.get("guilty_rows"), widest=measured.get("widest"),
            visibility=measured.get("descendants"),
            context=report.get("platform_context"),
            extra=format_widest_widgets(report)))


# ── С4: a Log line that sends you to a button names the button as it looks ──

def test_the_hint_lines_name_the_menu_item_to_press_next(main_window, caplog):
    """Р6 of plan_2026_09_17_cell_dialog_min_width, re-pointed by часть 3, п.4: the
    two success lines tell the user what to do NEXT, and the CellDock buttons they
    used to name are GONE — the honest reference is the ENTITY leaf's item
    ("Update from selection…"), and the removed caption must never come back."""
    dock = CellDock(main_window)

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
        assert "Update from selection" in message, (
            f"the {where} Log line does not name the item to press: {message!r}")
        assert "Refresh geometry" not in message, (
            f"the {where} Log line still names the REMOVED button: {message!r}")


if __name__ == "__main__":  # pragma: no cover — by hand, like the probes
    raise SystemExit(pytest.main([__file__, "-v"]))
