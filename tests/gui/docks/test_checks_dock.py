# tests/gui/docks/test_checks_dock.py
"""Cells for `gui/docks/checks.py` — the Checks PAGE (plan_2026_10_23_checks_tab_top_level).

The page is a LAUNCHER, so what is pinned here is the launcher's contract, not a check's logic:

  * it is the FOURTH page of the left tab row (Components | Config | Trees | Checks) and NOT a
    bottom dock any more — `hub.docks` is back to Log + Copper duplicates. This cell REPLACES
    the 22.10 one that pinned "tabified with the Log and has a View-menu action": a change of
    property by Denis's decision (23.10), not a lost assertion;
  * the tree has one row per check, nothing else, and the row's glyph/duration follow the LAST
    run's exit code;
  * every check keeps its OWN output: selecting another row shows that check's report without
    re-running anything, and "Copy" hands over the SELECTED check's own bytes;
  * one run at a time; "Run all" queues every check and starts the next only after the previous
    finished; "Stop" drops the rest of the queue and says so;
  * the "Checks" menu sits between Tools and View, has one item per check plus "Run all", and an
    item selects + runs ITS OWN check;
  * the fourth check (`merge_duplicate_mounts.py`) is a DRY run — `--write` is never passed;
  * a start never lands in the middle of the GUI's own IPC transaction (a busy socket defers
    once); one Log line per run; no modal windows (rule 43).

No KiCad and no board: the "scripts" are tiny stand-ins placed under `<tmp>/tools/`, taking
their exit code from the profile directory's name (`profile_exit_1` → 1, `profile_exit_99` →
starts, prints and then hangs for the Stop cell).
"""
import logging
import sys

import pytest
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow

from gui.checks_menu import build_checks_menu
from gui.checks_registry import CHECKS, command_for
from gui.docks._common import style_for_level
from gui.docks.checks import ChecksPage
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

#: The same stand-in, but slow enough to be OBSERVED — and it CANNOT be run twice at once: it
#: takes a lock file next to the profile and exits 7 if a sibling already holds it. That makes
#: "Run all is a queue, not a fan-out" measurable from the OUTSIDE (exit codes), instead of
#: trusting the page's own bookkeeping.
SLOW_SCRIPT = FAKE_SCRIPT.replace("if code == 99:", '''\\
lock = profile.parent / "running.lock"
if lock.exists():
    raise SystemExit(7)                 # a SECOND check ran alongside this one
lock.write_text("x")
try:
    time.sleep(0.15)
finally:
    lock.unlink()
if code == 99:''')

#: Д-1 probe (acceptance 22.10.2026): "a", then the FIRST byte of "—", a flush and a pause,
#: then the rest of the character and "b". A pipe delivers that as two reads.
SPLIT_SCRIPT = '''\
"""A multi-byte character split across two pipe reads — the Д-1 probe."""
import sys
import time

out = sys.stdout.buffer
dash = "—".encode("utf-8")
out.write(b"a" + dash[:1])
out.flush()
time.sleep(0.5)
out.write(dash[1:] + b"b\\n")
out.flush()
'''

#: Д-3 probe (acceptance 23.10.2026): "a", then the first TWO bytes of "—" — and the process
#: exits. The rest of the character never comes, so the decoder still holds `e2 80` when the run
#: finishes: a crashed check, a Stop or a truncated report look exactly like this.
TRUNCATED_SCRIPT = '''\
"""A run that ends in the MIDDLE of a multi-byte character — the Д-3 probe."""
import sys

out = sys.stdout.buffer
out.write(b"a" + "—".encode("utf-8")[:2])
out.flush()
'''

SCRIPT = "check_placement.py"
SECOND = "check_config_records.py"
MERGE = "merge_duplicate_mounts.py"
OUTPUT_0 = "first line for profile_exit_0\nwarning from stderr\n"
OUTPUT_1 = "first line for profile_exit_1\nwarning from stderr\n"


@pytest.fixture(autouse=True)
def _clean_working_set():
    """The working set is process-global: a leak from another cell would add the warning line
    to every "the pane is the script's bytes" assertion here."""
    WORKING_SET.clear()
    yield
    WORKING_SET.clear()


def _make_page(main_window, tmp_path, *, with_script=True,
               script_source=FAKE_SCRIPT, python=None) -> ChecksPage:
    """A page over a temp `tools/` tree. EVERY registry script is written with the same source,
    so "Run all" and the menu can start any of them without a second fixture.

    `python` is the interpreter seam: the "failed to start" cell points it at a path that does
    not exist, which is what `QProcess` reports as `FailedToStart`."""
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    if with_script:
        for check in CHECKS:
            (tools / check.script).write_text(script_source, encoding="utf-8")
    return ChecksPage(main_window, connection=main_window.connection,
                      scripts_root=tmp_path, python=python or sys.executable)


def _profile(tmp_path, code) -> "object":
    """A profile directory whose NAME carries the exit code the stand-in must use."""
    profile = tmp_path / f"profile_exit_{code}"
    profile.mkdir()
    config = profile / "config.sexp"
    config.write_text("(kicadstamp-config)\n", encoding="utf-8")
    return config


def _run(page, qtbot, profile, script=SCRIPT) -> None:
    """Select `script` and press the REAL "Run" button — the door the user has."""
    page.set_root_path(profile)
    page.select(script)
    with qtbot.waitSignal(page.finished, timeout=20000):
        page.run_button.click()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)


def _rows(page):
    return {page.tree.topLevelItem(i).data(0, 0x0100): page.tree.topLevelItem(i)
            for i in range(page.tree.topLevelItemCount())}


def _check_actions(menu):
    """Every action of the menu except the separators, in menu order."""
    return [action for action in menu.actions() if not action.isSeparator()]


# ── place: the fourth left tab, not a dock ──────────────────────────────────

def test_the_page_is_the_fourth_left_tab_and_not_a_bottom_dock(real_main_window):
    """Plan §1 (Denis, 23.10): Checks stands in the SAME row as Components / Config / Trees.

    This replaces the 22.10 cell `test_panel_is_tabified_with_the_log_and_has_its_view_menu_action`
    — a change of property decided by Denis, named here so the loss is visible, not silent."""
    hub = real_main_window._dock_hub
    page = hub.checks_page

    assert [hub.left_tabs.tabText(i) for i in range(hub.left_tabs.count())] == \
        ["Components", "Config", "Trees", "Checks"]
    assert hub.left_tabs.widget(3) is page
    assert not isinstance(page, QDockWidget)
    assert hub.docks == [hub.log_dock, hub.dedupe_dock]
    assert page not in real_main_window.tabifiedDockWidgets(hub.log_dock)

    real_main_window.resize(1900, 1000)
    real_main_window.show()
    assert hub.log_dock.isVisible()


def test_the_tree_has_one_row_per_check_and_nothing_else(main_window, tmp_path):
    """One row per registry check, in registry order — no invented rows before Ч2 (no kinds,
    no single errors: they need the scripts' `--json`)."""
    page = _make_page(main_window, tmp_path)

    assert page.tree.topLevelItemCount() == len(CHECKS)
    titles = [page.tree.topLevelItem(i).text(0) for i in range(len(CHECKS))]
    assert titles == [f"· {check.title}" for check in CHECKS]


# ── per-check output and Copy ───────────────────────────────────────────────

def test_each_check_keeps_its_own_output_and_copy_follows_the_selection(
        main_window, qtbot, tmp_path):
    """Run A, then B; select A → A's report; select B → B's report. Copy is the SELECTED
    check's, never "whichever ran last"."""
    page = _make_page(main_window, tmp_path)
    _run(page, qtbot, _profile(tmp_path, 1), SCRIPT)
    _run(page, qtbot, _profile(tmp_path, 0), SECOND)

    page.select(SCRIPT)
    assert page.output.toPlainText() == OUTPUT_1
    assert "problems found" in page.status_label.text()
    page.copy_button.click()
    assert QApplication.clipboard().text() == OUTPUT_1

    page.select(SECOND)
    assert page.output.toPlainText() == OUTPUT_0
    assert "clean" in page.status_label.text()
    page.copy_button.click()
    assert QApplication.clipboard().text() == OUTPUT_0


@pytest.mark.parametrize("code,glyph", [(0, "✔"), (1, "⚠"), (2, "✖")])
def test_the_row_glyph_follows_the_exit_code(main_window, qtbot, tmp_path, code, glyph):
    """The row's verdict IS the exit code's vocabulary (`exit_level`): 0 clean, 1 problems,
    2 refused — and the duration of that run rides in the second column."""
    page = _make_page(main_window, tmp_path)
    _run(page, qtbot, _profile(tmp_path, code))

    row = page._items[SCRIPT]
    assert row.text(0).startswith(f"{glyph} ")
    assert row.text(1).endswith(" s")


# ── one run at a time, "Run all", Stop ──────────────────────────────────────

def test_a_running_check_marks_its_row_and_stops_the_other_doors(
        main_window, qtbot, tmp_path):
    """While A runs: A's row says so, and NOTHING may start a second process — not the Run
    button, not `run()` from code, not "Run all", not a menu item."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 99))
    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.is_running(), timeout=20000)

    assert page._items[SCRIPT].text(1) == "running…"
    assert not page.run_button.isEnabled()
    assert not page.run_all_button.isEnabled()
    assert not page.can_run()
    page.run(SECOND)
    assert page.selected_script() == SCRIPT
    assert len(page.findChildren(QProcess)) == 1

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)


def test_run_all_runs_the_checks_one_after_another(main_window, qtbot, tmp_path, monkeypatch):
    """Plan §8: "Run all" is a QUEUE — every check in registry order, the next one started by
    the previous one's `finished`, not in parallel with it."""
    page = _make_page(main_window, tmp_path, script_source=SLOW_SCRIPT)
    page.set_root_path(_profile(tmp_path, 0))
    seen = []
    codes = []
    page.finished.connect(lambda script, code, _s: (seen.append(script), codes.append(code)))

    with qtbot.waitSignal(page.finished, timeout=20000):
        page.run_all_button.click()

    # The first one is over and the SECOND is already running: that is the sequencing.
    assert page.is_running()
    assert page.selected_script() == CHECKS[1].script

    qtbot.waitUntil(lambda: len(seen) == len(CHECKS), timeout=40000)
    assert seen == [check.script for check in CHECKS]
    # Nobody ever ran ALONGSIDE anybody: the lock the stand-in takes was free for every run,
    # so every run exited 0 (a parallel sibling would have exited 7).
    assert codes == [0] * len(CHECKS)


def test_stop_mid_queue_stops_the_current_and_drops_the_rest(main_window, qtbot, tmp_path):
    """Stop during "Run all": the running check is killed, nothing else starts, and the status
    says the rest was cancelled — a silent drop would look like "all clean"."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 99))
    page.run_all()
    qtbot.waitUntil(lambda: page.is_running(), timeout=20000)

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)

    assert "stopped" in page.status_label.text()
    assert "cancelled" in page.status_label.text()
    for later in CHECKS[1:]:
        assert not page.state_of(later.script).started
        assert page._items[later.script].text(0).startswith("· ")
    assert len(page.findChildren(QProcess)) == 1


# ── a refused start ends the whole "Run all" ────────────────────────────────

def test_a_busy_socket_twice_cancels_the_rest_of_the_run(main_window, qtbot, tmp_path):
    """Д-1 (acceptance 23.10): the FIRST check of "Run all" meeting a busy socket twice ends the
    WHOLE run — the rest never starts, the status line says the remainder was cancelled, and a
    later single Run does NOT resume the dropped queue."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    main_window.connection.long_op_active = True
    try:
        page.run_all()
        qtbot.waitUntil(lambda: "busy" in page.status_label.text(), timeout=5000)
        assert "cancelled" in page.status_label.text()
        assert not page.is_running()
        for later in CHECKS[1:]:
            assert not page.state_of(later.script).started
    finally:
        main_window.connection.long_op_active = False

    seen = []
    page.finished.connect(lambda script, _code, _s: seen.append(script))
    with qtbot.waitSignal(page.finished, timeout=20000):
        page.run(SECOND)
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)
    assert seen == [SECOND]                      # exactly the one asked for — no old queue
    assert not page.state_of(CHECKS[2].script).started


def test_a_missing_script_cancels_the_rest_of_the_run(main_window, tmp_path):
    """Д-1: no `tools/` — the first check refuses, so "Run all" is over before it began; the
    status line names the reason AND the cancelled remainder, and no process was ever made."""
    page = _make_page(main_window, tmp_path, with_script=False)
    page.set_root_path(_profile(tmp_path, 0))

    page.run_all()

    assert not page.is_running()
    assert "was not found" in page.status_label.text()
    assert "cancelled" in page.status_label.text()
    for later in CHECKS[1:]:
        assert not page.state_of(later.script).started
    assert page.findChildren(QProcess) == []


def test_a_start_that_fails_cancels_the_rest_of_the_run(main_window, qtbot, tmp_path):
    """Д-1: `FailedToStart` (an interpreter that does not exist) on the first check of "Run all"
    — same ending as a manifest refusal: nothing else starts, and it is said out loud."""
    page = _make_page(main_window, tmp_path, python=str(tmp_path / "no-such-python"))
    page.set_root_path(_profile(tmp_path, 0))

    with qtbot.waitSignal(page.finished, timeout=20000):
        page.run_all()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)

    assert "could not start" in page.status_label.text()
    assert "cancelled" in page.status_label.text()
    for later in CHECKS[1:]:
        assert not page.state_of(later.script).started


# ── the fourth check and the menu ───────────────────────────────────────────

def test_the_merge_check_is_a_dry_run_and_its_code_colours_the_row(
        main_window, qtbot, tmp_path):
    """`merge_duplicate_mounts.py` joins the registry (plan §7) — and the GUI runs it WITHOUT
    `--write`: the page reports duplicates, it never rewrites the profile."""
    assert any(check.script == MERGE for check in CHECKS)
    page = _make_page(main_window, tmp_path)     # writes every registry script under tools/
    argv, reason = command_for(next(c for c in CHECKS if c.script == MERGE),
                               _profile(tmp_path, 0),
                               root=tmp_path, python=sys.executable)
    assert argv is not None, reason
    assert "--write" not in argv

    _run(page, qtbot, _profile(tmp_path, 1), MERGE)
    assert page._items[MERGE].text(0).startswith("⚠ ")


def test_the_checks_menu_sits_between_tools_and_view_and_runs_its_own_check(
        main_window, real_main_window, qtbot, tmp_path):
    """Plan §8 (Denis, 23.10): the menu is between Tools and View, one item per check plus a
    separator and "Run all", and an item runs ITS OWN check — never the first one.

    The PLACE is measured on the real window (it owns the menu bar); the "item runs its own
    check" half on a temp page, because the window's page points at the real `tools/`."""
    titles = [action.text().replace("&", "")
              for action in real_main_window.menuBar().actions()]
    assert titles.index("Checks") == titles.index("Tools") + 1
    assert titles.index("View") == titles.index("Checks") + 1

    window = QMainWindow()
    page = _make_page(main_window, tmp_path)
    menu = build_checks_menu(window, page)
    actions = _check_actions(menu)
    assert [a.text() for a in actions] == [c.title for c in CHECKS] + ["Run all"]

    page.set_root_path(_profile(tmp_path, 0))
    with qtbot.waitSignal(page.finished, timeout=20000):
        actions[1].trigger()
    assert page.selected_script() == CHECKS[1].script
    assert page.state_of(CHECKS[1].script).started
    assert not page.state_of(SCRIPT).started


def test_the_menu_is_disabled_without_a_project_and_while_a_check_runs(
        main_window, qtbot, tmp_path):
    """Both facts the page already owns, seen through the menu: no project → nothing enabled;
    a live run → nothing enabled (including "Run all")."""
    window = QMainWindow()
    page = _make_page(main_window, tmp_path)
    menu = build_checks_menu(window, page)
    actions = _check_actions(menu)

    assert all(not action.isEnabled() for action in actions)

    page.set_root_path(_profile(tmp_path, 99))
    assert all(action.isEnabled() for action in actions)

    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.is_running(), timeout=20000)
    assert all(not action.isEnabled() for action in actions)

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)
    assert all(action.isEnabled() for action in actions)


# ── the pane is the script's bytes; the code colours the status ─────────────

def test_the_pane_is_the_scripts_own_bytes_and_exit_1_is_yellow(main_window, qtbot,
                                                               tmp_path, caplog):
    """stdout AND stderr, in the order the script wrote them — and the yellow "problems found"
    of exit 1, plus the ONE Log line the run owes."""
    page = _make_page(main_window, tmp_path)

    with caplog.at_level(logging.INFO):
        _run(page, qtbot, _profile(tmp_path, 1))

    assert page.state_of(SCRIPT).output == OUTPUT_1
    assert page.output.toPlainText() == OUTPUT_1
    assert "problems found" in page.status_label.text()
    assert page.status_label.styleSheet() == style_for_level("warn")
    assert any(line.startswith("Check check_placement.py: exit 1 (")
               for line in caplog.messages), caplog.messages


def test_exit_zero_is_green_and_exit_two_is_red(main_window, qtbot, tmp_path):
    """The codes the four scripts actually speak: 0 clean, 2 refused (wrong board open)."""
    clean = _make_page(main_window, tmp_path)
    _run(clean, qtbot, _profile(tmp_path, 0))
    assert "clean" in clean.status_label.text()
    assert clean.status_label.styleSheet() == style_for_level("success")

    refused = _make_page(main_window, tmp_path)
    _run(refused, qtbot, _profile(tmp_path, 2))
    assert "refused" in refused.status_label.text()
    assert refused.status_label.styleSheet() == style_for_level("error")


def test_a_character_split_across_two_pipe_reads_survives(main_window, qtbot, tmp_path):
    """Д-1 (acceptance 22.10.2026) — the byte-for-byte promise at its sharpest.

    The probe writes `a` + the FIRST byte of "—", flushes, waits 0.5 s, then the rest + `b`.
    QProcess hands that over as two reads, and decoding each chunk on its own turned the two
    halves into U+FFFD: the pane and "Copy" carried "a���b" instead of "a—b". ONE incremental
    decoder per run is what keeps the character whole.
    """
    page = _make_page(main_window, tmp_path, script_source=SPLIT_SCRIPT)

    _run(page, qtbot, _profile(tmp_path, 0))

    assert page.state_of(SCRIPT).output == "a—b\n"
    assert page.output.toPlainText() == "a—b\n"
    page.copy_button.click()
    assert QApplication.clipboard().text() == "a—b\n"


def test_a_character_cut_off_by_the_run_end_is_shown_as_a_replacement_char(
        main_window, qtbot, tmp_path):
    """Д-3 (acceptance 23.10.2026) — the decoder's tail is FLUSHED, not dropped.

    The probe writes `a` + the first two bytes of "—" and exits; the rest of the character never
    arrives. That is the real shape of a check that crashed, was stopped, or whose output was cut
    off mid-character: `final=True` in `_flush_decoder` is the only thing that turns the buffered
    `e2 80` into U+FFFD in the report, the pane and "Copy".
    """
    page = _make_page(main_window, tmp_path, script_source=TRUNCATED_SCRIPT)

    _run(page, qtbot, _profile(tmp_path, 0))

    assert page.state_of(SCRIPT).output == "a\ufffd"
    assert page.output.toPlainText() == "a\ufffd"
    page.copy_button.click()
    assert QApplication.clipboard().text() == "a\ufffd"


def test_copy_hands_over_the_selected_scripts_bytes(main_window, qtbot, tmp_path):
    """Byte for byte: exactly what the script printed, with the stderr line in it."""
    page = _make_page(main_window, tmp_path)
    _run(page, qtbot, _profile(tmp_path, 0))

    page.copy_button.click()

    assert QApplication.clipboard().text() == OUTPUT_0


def test_a_dirty_working_set_warns_in_the_pane_but_not_in_the_copied_text(
        main_window, qtbot, tmp_path, monkeypatch):
    """The scripts read the profile FROM DISK. Unsaved config edits are therefore invisible —
    said in ONE line at the top of the pane, and deliberately NOT part of "Copy"."""
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    WORKING_SET.stage_write(tmp_path / "config.sexp", {"cells": {}})
    page = _make_page(main_window, tmp_path)

    _run(page, qtbot, _profile(tmp_path, 0))

    pane = page.output.toPlainText()
    assert pane.startswith("The check reads the SAVED profile")
    assert pane.endswith(OUTPUT_0)
    assert "SAVED profile" not in page.state_of(SCRIPT).output
    page.copy_button.click()
    assert QApplication.clipboard().text() == OUTPUT_0


# ── refusals ────────────────────────────────────────────────────────────────

def test_no_project_disables_every_run_door(main_window, tmp_path):
    """Without a project there is no argument to pass — the doors say so by being disabled."""
    page = _make_page(main_window, tmp_path)
    assert not page.run_button.isEnabled()
    assert not page.run_all_button.isEnabled()
    assert not page.stop_button.isEnabled()

    page.set_root_path(_profile(tmp_path, 0))
    assert page.run_button.isEnabled()
    assert page.run_all_button.isEnabled()

    page.set_root_path(None)
    assert not page.run_button.isEnabled()
    assert not page.run_all_button.isEnabled()


def test_a_busy_socket_defers_once_then_says_kicad_is_busy(main_window, qtbot, tmp_path):
    """The GUI's own long operation (or the poll tick) may hold the shared socket: the script
    reads the LIVE board, so a start never lands inside that transaction. ONE deferred retry,
    then an honest "busy" line — and nothing started."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    main_window.connection.long_op_active = True
    try:
        page.run_button.click()
        assert not page.is_running()
        qtbot.waitUntil(lambda: "busy" in page.status_label.text(), timeout=5000)
        assert not page.is_running()
        assert page.status_label.styleSheet() == style_for_level("error")
    finally:
        main_window.connection.long_op_active = False


def test_a_tree_without_the_scripts_is_a_red_line_not_a_window(main_window, tmp_path):
    """`tools/` missing (an installed package): one red status line with the reason. Rule 43 —
    no modal anywhere on this path."""
    page = _make_page(main_window, tmp_path, with_script=False)
    page.set_root_path(_profile(tmp_path, 0))

    page.select(SCRIPT)
    page.run(SCRIPT)

    assert not page.is_running()
    assert "was not found" in page.status_label.text()
    assert page.status_label.styleSheet() == style_for_level("error")


def test_stop_kills_the_running_check_and_says_so(main_window, qtbot, tmp_path):
    """exit_99 hangs after its first line: "Stop" must really kill it, and the status must
    not pretend the run finished on its own."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 99))
    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.state_of(SCRIPT).output != "", timeout=20000)
    assert page.is_running() and page.stop_button.isEnabled()

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop_button.click()

    assert not page.is_running()
    assert "stopped" in page.status_label.text()
    assert page.status_label.styleSheet() == style_for_level("error")


def test_a_second_press_cannot_start_a_second_process(main_window, qtbot, tmp_path):
    """One run per page — enforced by `run()` itself, not only by the disabled buttons."""
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 99))
    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.state_of(SCRIPT).output != "", timeout=20000)

    page.run(SECOND)                    # programmatic: the buttons are disabled right now

    assert len(page.findChildren(QProcess)) == 1
    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)
