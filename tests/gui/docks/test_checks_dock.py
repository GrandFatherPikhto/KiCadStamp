# tests/gui/docks/test_checks_dock.py
"""Cells for `gui/docks/checks.py` — the Checks tab (plan_2026_10_22_checks_tab).

The tab is a LAUNCHER, so what is pinned here is the launcher's contract, not a check's logic:

  * it is a real dock TABIFIED WITH THE LOG, with Qt's own toggleViewAction in the View menu —
    the same shape the copper-duplicates panel has;
  * the pane shows the script's OWN bytes, stdout and stderr together, and `Copy` hands over
    exactly those bytes (the dock's own warning line is not in them);
  * the exit code colours the status line: 0 green/clean, 1 yellow/problems found, 2 red/refused;
  * a run that cannot start or is stopped is red with the reason — never a window (rule 43);
  * one run at a time; no project → buttons disabled;
  * a start never lands in the middle of the GUI's own IPC transaction: a busy socket defers
    once, and a second refusal says "KiCad is busy" instead of running half-blind;
  * one Log line per run.

No KiCad and no board: the "script" is a tiny stand-in placed under `<tmp>/tools/`, which takes
the exit code from the profile directory's name (`profile_exit_1` → 1, `profile_exit_99` → 99 =
starts, prints and then hangs for the Stop cell). The Production path is unchanged — the dock is
handed `scripts_root`/`python` as seams, exactly what `checks_registry.command_for` takes.
"""
import logging
import sys

import pytest
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication

from gui.checks_registry import CHECKS
from gui.docks._common import style_for_level
from gui.docks.checks import ChecksDock
from kicadstamp.config_working_set import WORKING_SET


FAKE_SCRIPT = '''\
"""Stand-in for a tools/check_*.py: prints to BOTH streams, then exits with the code encoded
in its profile directory's name (profile_exit_1 -> 1). No KiCad, no board, no imports."""
import sys
import time
from pathlib import Path

profile = Path(sys.argv[1])
code = int(profile.parent.name.rsplit("_", 1)[-1])
print(f"first line for {profile.parent.name}")
sys.stderr.write("warning from stderr\\n")
if code == 99:
    time.sleep(60)
raise SystemExit(code)
'''

SCRIPT = "check_placement.py"
OUTPUT_0 = "first line for profile_exit_0\nwarning from stderr\n"
OUTPUT_1 = "first line for profile_exit_1\nwarning from stderr\n"


@pytest.fixture(autouse=True)
def _clean_working_set():
    """The working set is process-global: a leak from another cell would add the warning line
    to every "the pane is the script's bytes" assertion here."""
    WORKING_SET.clear()
    yield
    WORKING_SET.clear()


def _make_dock(main_window, tmp_path, *, with_script=True) -> ChecksDock:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    if with_script:
        (tools / SCRIPT).write_text(FAKE_SCRIPT, encoding="utf-8")
    return ChecksDock(main_window, connection=main_window.connection,
                      scripts_root=tmp_path, python=sys.executable)


def _profile(tmp_path, code) -> "object":
    """A profile directory whose NAME carries the exit code the stand-in must use."""
    profile = tmp_path / f"profile_exit_{code}"
    profile.mkdir()
    config = profile / "config.sexp"
    config.write_text("(kicadstamp-config)\n", encoding="utf-8")
    return config


def _run(dock, qtbot, profile) -> None:
    dock.set_root_path(profile)
    with qtbot.waitSignal(dock.finished, timeout=20000):
        dock.check_buttons[SCRIPT].click()
    qtbot.waitUntil(lambda: not dock.is_running(), timeout=5000)


# ── the dock is a real, tabified dock with a View-menu action ────────────────

def test_panel_is_tabified_with_the_log_and_has_its_view_menu_action(real_main_window):
    """The panel opens/closes through Qt's own toggleViewAction, like the duplicates panel.

    WHICH tab is current is not readable off `isVisible()` (Qt keeps every tabified dock
    "visible" inside the stack); that half is measured where it has a consequence —
    tests/gui/test_dock_sizing.py's exact `resizeDocks` delta, which only holds while the LOG
    is the area's current dock (see `log_dock.raise_()` in gui/dock_hub.py)."""
    hub = real_main_window._dock_hub
    dock = hub.checks_dock

    assert dock in hub.docks
    assert dock in real_main_window.tabifiedDockWidgets(hub.log_dock)

    view_menu = next(action.menu()
                     for action in real_main_window.menuBar().actions()
                     if action.text().replace("&", "") == "View")
    assert dock.toggleViewAction() in view_menu.actions()

    real_main_window.resize(1900, 1000)
    real_main_window.show()
    assert hub.log_dock.isVisible()


# ── the pane is the script's bytes; the code colours the status ──────────────

def test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow(main_window, qtbot,
                                                               tmp_path, caplog):
    """stdout AND stderr, in the order the script wrote them — and the yellow "problems found"
    of exit 1, plus the ONE Log line the run owes."""
    dock = _make_dock(main_window, tmp_path)
    profile = _profile(tmp_path, 1)

    with caplog.at_level(logging.INFO):
        _run(dock, qtbot, profile)

    assert dock._output == OUTPUT_1
    assert dock.output.toPlainText() == OUTPUT_1
    assert "problems found" in dock.status_label.text()
    assert dock.status_label.styleSheet() == style_for_level("warn")
    assert any(line.startswith("Check check_placement.py: exit 1 (")
               for line in caplog.messages), caplog.messages


def test_exit_zero_is_green_and_exit_two_is_red(main_window, qtbot, tmp_path):
    """The codes the three scripts actually speak: 0 clean, 2 refused (wrong board open)."""
    clean = _make_dock(main_window, tmp_path)
    _run(clean, qtbot, _profile(tmp_path, 0))
    assert "clean" in clean.status_label.text()
    assert clean.status_label.styleSheet() == style_for_level("success")

    refused = _make_dock(main_window, tmp_path)
    _run(refused, qtbot, _profile(tmp_path, 2))
    assert "refused" in refused.status_label.text()
    assert refused.status_label.styleSheet() == style_for_level("error")


def test_copy_hands_over_the_script_bytes(main_window, qtbot, tmp_path):
    """Byte for byte: exactly what the script printed, with the stderr line in it."""
    dock = _make_dock(main_window, tmp_path)
    _run(dock, qtbot, _profile(tmp_path, 0))

    dock.copy_button.click()

    assert QApplication.clipboard().text() == OUTPUT_0


def test_a_dirty_working_set_warns_in_the_pane_but_not_in_the_copied_text(
        main_window, qtbot, tmp_path, monkeypatch):
    """The scripts read the profile FROM DISK. Unsaved config edits are therefore invisible —
    said in ONE line at the top of the pane, and deliberately NOT part of "Copy": what is
    copied stays the CLI's own report, the thing to paste into a bug report."""
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    WORKING_SET.stage_write(tmp_path / "config.sexp", {"cells": {}})
    dock = _make_dock(main_window, tmp_path)

    _run(dock, qtbot, _profile(tmp_path, 0))

    pane = dock.output.toPlainText()
    assert pane.startswith("The check reads the SAVED profile")
    assert pane.endswith(OUTPUT_0)
    assert "SAVED profile" not in dock._output
    dock.copy_button.click()
    assert QApplication.clipboard().text() == OUTPUT_0


# ── refusals, one-run-at-a-time, Stop ───────────────────────────────────────

def test_no_project_disables_every_check_button(main_window, tmp_path):
    """Without a project there is no argument to pass — the buttons say so by being disabled."""
    dock = _make_dock(main_window, tmp_path)
    assert all(not button.isEnabled() for button in dock.check_buttons.values())
    assert not dock.stop_button.isEnabled()

    dock.set_root_path(_profile(tmp_path, 0))
    assert all(button.isEnabled() for button in dock.check_buttons.values())

    dock.set_root_path(None)
    assert all(not button.isEnabled() for button in dock.check_buttons.values())


def test_a_busy_socket_defers_once_then_says_kicad_is_busy(main_window, qtbot, tmp_path):
    """The GUI's own long operation (or the poll tick) may hold the shared socket: the script
    reads the LIVE board, so a start never lands inside that transaction. ONE deferred retry,
    then an honest "busy" line — and nothing started."""
    dock = _make_dock(main_window, tmp_path)
    dock.set_root_path(_profile(tmp_path, 0))
    main_window.connection.long_op_active = True
    try:
        dock.check_buttons[SCRIPT].click()
        assert not dock.is_running()
        qtbot.waitUntil(lambda: "busy" in dock.status_label.text(), timeout=5000)
        assert not dock.is_running()
        assert dock.status_label.styleSheet() == style_for_level("error")
    finally:
        main_window.connection.long_op_active = False


def test_a_tree_without_the_script_is_a_red_line_not_a_window(main_window, tmp_path):
    """`tools/` missing (an installed package): one red status line with the reason. Rule 43 —
    no modal anywhere on this path."""
    dock = _make_dock(main_window, tmp_path, with_script=False)
    dock.set_root_path(_profile(tmp_path, 0))

    dock.check_buttons[SCRIPT].click()

    assert not dock.is_running()
    assert "was not found" in dock.status_label.text()
    assert dock.status_label.styleSheet() == style_for_level("error")


def test_stop_kills_the_running_check_and_says_so(main_window, qtbot, tmp_path):
    """exit_99 hangs after its first line: "Stop" must really kill it, and the status must
    not pretend the run finished on its own."""
    dock = _make_dock(main_window, tmp_path)
    dock.set_root_path(_profile(tmp_path, 99))
    dock.check_buttons[SCRIPT].click()
    qtbot.waitUntil(lambda: dock._output != "", timeout=20000)
    assert dock.is_running() and dock.stop_button.isEnabled()

    with qtbot.waitSignal(dock.finished, timeout=15000):
        dock.stop_button.click()

    assert not dock.is_running()
    assert "stopped" in dock.status_label.text()
    assert dock.status_label.styleSheet() == style_for_level("error")


def test_a_second_press_cannot_start_a_second_process(main_window, qtbot, tmp_path):
    """One run per tab — enforced by the handler too, not only by the disabled buttons."""
    dock = _make_dock(main_window, tmp_path)
    dock.set_root_path(_profile(tmp_path, 99))
    dock.check_buttons[SCRIPT].click()
    qtbot.waitUntil(lambda: dock._output != "", timeout=20000)

    dock._on_check(CHECKS[0])          # programmatic: the buttons are disabled right now

    assert len(dock.findChildren(QProcess)) == 1
    with qtbot.waitSignal(dock.finished, timeout=15000):
        dock.stop_button.click()
    qtbot.waitUntil(lambda: not dock.is_running(), timeout=5000)
