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
    re-running anything, and "Copy" hands over the SELECTED check's own text (its bytes decoded,
    line endings normalised to `\n` — Д-5(b) of the Ч2а acceptance);
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
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow

from gui.checks_menu import build_checks_menu
from gui.checks_registry import CHECKS, DEDUPE_ROW, command_for
from gui.docks._common import style_for_level
from gui.docks.checks import ChecksPage, new_output_decoder
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

#: Д-5(b) probe (acceptance 25.10.2026): the bytes a Windows check really prints — Python's text
#: mode writes `\r\n` there, and the pipe carries those bytes wherever the GUI runs. Written
#: through `sys.stdout.buffer` on purpose: the probe must produce the SAME bytes on Linux.
CRLF_SCRIPT = '''\
"""A stand-in that prints Windows line endings — the Д-5(b) probe."""
import sys

out = sys.stdout.buffer
out.write(b"first line\\r\\nsecond line\\r\\n")
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
    # Ч1б (23.10): the copper-duplicates dock left the bottom area for a ROW of this page, so
    # the Log is the only real top-level dock left (layout change by Denis's decision, named
    # here and in test_dedupe_dock's own cell).
    assert hub.docks == [hub.log_dock]
    assert page not in real_main_window.tabifiedDockWidgets(hub.log_dock)

    real_main_window.resize(1900, 1000)
    real_main_window.show()
    assert hub.log_dock.isVisible()


def test_the_tree_has_one_row_per_check_and_the_copper_panel_last(main_window,
                                                                 tmp_path):
    """One row per registry check, in registry order, then the copper-duplicates panel — no
    invented rows before Ч2 (no kinds, no single errors: they need the scripts' `--json`).

    REPLACED (Ч1б) `test_the_tree_has_one_row_per_check_and_nothing_else`: "nothing else"
    stopped being true when the panel became the fifth row by Denis's decision."""
    from kicadstamp.i18n import _

    page = _make_page(main_window, tmp_path)

    assert page.tree.topLevelItemCount() == len(CHECKS) + 1
    titles = [page.tree.topLevelItem(i).text(0) for i in range(len(CHECKS))]
    assert titles == [f"· {check.title}" for check in CHECKS]
    panel_title = page.tree.topLevelItem(len(CHECKS)).text(0)
    assert panel_title == f"· {_('Copper duplicates')}"
    # Д-1 of the Ч2а acceptance: the duplicate GROUPS are this row's CHILDREN, and
    # before the first scan there are none — no invented groups, no second tree.
    assert page._items[DEDUPE_ROW].childCount() == 0


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
    # Ч1б: the panel's SEARCH joins the list, then "Run all", then the panel's CLEAR — the
    # order is the menu's own contract (runs together, the write apart).
    assert [a.text() for a in actions] == (
        [c.title for c in CHECKS]
        + ["Copper duplicates", "Run all", "Clear copper duplicates"])

    page.set_root_path(_profile(tmp_path, 0))
    with qtbot.waitSignal(page.finished, timeout=20000):
        actions[1].trigger()
    assert page.selected_script() == CHECKS[1].script
    assert page.state_of(CHECKS[1].script).started
    assert not page.state_of(SCRIPT).started


def test_the_menu_follows_the_page_and_the_clear_follows_the_panel(
        main_window, qtbot, tmp_path):
    """Two rules, seen through the menu (Ч1б).

    Every RUN door (each check, the panel's search, "Run all") follows the PAGE: off without
    a project, off while anything runs. "Clear copper duplicates" alone follows the PANEL:
    it stays off here even though a project is open and nothing runs, because nothing has
    been FOUND — the very condition its own button uses."""
    window = QMainWindow()
    page = _make_page(main_window, tmp_path)
    menu = build_checks_menu(window, page)
    actions = _check_actions(menu)
    run_doors = actions[:-1]
    clear = actions[-1]

    assert all(not action.isEnabled() for action in run_doors)
    assert clear.isEnabled() is False

    page.set_root_path(_profile(tmp_path, 99))
    assert all(action.isEnabled() for action in run_doors)
    assert clear.isEnabled() is False        # root, but no found list yet

    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.is_running(), timeout=20000)
    assert all(not action.isEnabled() for action in run_doors)
    assert clear.isEnabled() is False

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)
    assert all(action.isEnabled() for action in run_doors)
    assert clear.isEnabled() is False


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


def test_the_pipe_decoder_turns_crlf_into_one_newline(qapp):
    """Д-5(b) of the Ч2а acceptance: the ONE decoder the page arms for every run.

    `io.IncrementalNewlineDecoder(translate=True)` around the UTF-8 replacement decoder is the
    same translator Python's text mode uses. It HOLDS a `\\r` at the end of a chunk, so a
    `\\r` | `\\n` split across two pipe reads still comes out as ONE newline — exactly the shape
    a Windows check produces — and a lone trailing `\\r` becomes `\\n` on the final flush, which
    is what `_flush_decoder` relies on. The Д-3 promise (a cut-off multi-byte character becomes
    U+FFFD) must survive the wrapper: it is transparent."""
    decoder = new_output_decoder()

    assert decoder.decode(b"a\r") == "a"          # the CR is held, not shown yet
    assert decoder.decode(b"\nb") == "\nb"        # ... and joins the LF into ONE newline
    assert decoder.decode(b"\r\nc") == "\nc"
    assert decoder.decode(b"", True) == ""

    lone_cr = new_output_decoder()
    assert lone_cr.decode(b"tail\r") == "tail"
    assert lone_cr.decode(b"", True) == "\n"

    cut = new_output_decoder()
    assert cut.decode(b"a" + "\u2014".encode("utf-8")[:2]) == "a"
    assert cut.decode(b"", True) == "\ufffd"


def test_a_script_that_prints_crlf_reaches_the_pane_and_copy_as_lf(
        main_window, qtbot, tmp_path):
    """Д-5(b): the same thing END TO END, with the bytes a Windows check really prints.

    The pane must not mix the script's `\\r\\n` with the page's own `\\n`, and "Copy" must hand
    over the text the pane shows. Before the wrapper these were the four 22.10 cells that were
    red only on `windows-latest`."""
    page = _make_page(main_window, tmp_path, script_source=CRLF_SCRIPT)

    _run(page, qtbot, _profile(tmp_path, 0))

    assert page.state_of(SCRIPT).output == "first line\nsecond line\n"
    assert page.output.toPlainText() == "first line\nsecond line\n"
    page.copy_button.click()
    assert QApplication.clipboard().text() == "first line\nsecond line\n"


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


# ── the copper-duplicates row (Ч1б, 23.10) ──────────────────────────────────

def test_run_all_searches_for_duplicates_and_never_clears(main_window, qtbot,
                                                          tmp_path, monkeypatch):
    """Ч1б: the panel's row is queued LAST and only as a SEARCH. Clearing rewrites the board,
    so "Run all" must be unable to reach it — pinned by spying the panel's two doors and by
    watching the queue move on after the search."""
    from kicadstamp.i18n import _

    page = _make_page(main_window, tmp_path, script_source=SLOW_SCRIPT)
    page.set_root_path(_profile(tmp_path, 0))
    order, cleared = [], []

    def fake_find():
        order.append("find")
        page.dedupe.finished_op.emit()      # the search is over at once: no board here

    monkeypatch.setattr(page.dedupe, "find", fake_find)
    monkeypatch.setattr(page.dedupe, "clear", lambda: cleared.append(True))
    codes = []
    page.finished.connect(lambda script, code, _s: codes.append(script))

    page.run_all()
    qtbot.waitUntil(lambda: (not page.is_running() and not page._queue), timeout=40000)

    assert codes == [check.script for check in CHECKS]
    assert order == ["find"]
    assert cleared == []
    assert page._items[page.selected_script()].text(0).endswith(_("Copper duplicates"))


def test_the_menu_clear_is_the_panels_own_door_and_appears_only_when_usable(
        main_window, qtbot, tmp_path):
    """Ч1б: the menu's Clear item is enabled exactly when the panel's Clear button is (a root,
    a found list, nothing running) and it calls the PANEL's door — the extras really go."""
    from tests.gui.docks.test_dedupe_dock import _board as _duplicate_board

    adapter = _duplicate_board()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    window = QMainWindow()
    menu = build_checks_menu(window, page)
    clear = _check_actions(menu)[-1]

    assert clear.isEnabled() is False                       # nothing found yet
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    assert clear.isEnabled() is False                       # root, still nothing found

    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        page.dedupe.find()
    assert page.dedupe.has_groups() is True
    assert clear.isEnabled() is True
    assert clear.isEnabled() == page.dedupe.clear_button.isEnabled()

    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        clear.trigger()
    qtbot.waitUntil(lambda: adapter.removed == ["v2", "t2"], timeout=5000)
    # Clear auto-rescans (one deferred turn), and the rescan is what empties the list — the
    # item turns off with it, not before.
    qtbot.waitUntil(lambda: not page.dedupe.has_groups(), timeout=10000)
    assert clear.isEnabled() is False


def test_a_running_panel_turns_the_check_doors_off(main_window, qtbot, tmp_path):
    """Д-1 of the Ч1б acceptance, direction ONE: "something runs on the page" is ONE
    answer. While the copper panel searches (or clears), Run, Run all and `run()`
    itself are off — the check doors do not stay live next to a live board read."""
    from tests.gui.docks.test_dedupe_dock import _board as _duplicate_board

    main_window.connection.board = SimpleNamespace(adapter=_duplicate_board())
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    page.dedupe.set_root_path(tmp_path / "root.sexp")

    # The row is selected the way production selects it (the menu item and "Run all" both
    # `select(DEDUPE_ROW)` first): a scan started from ANOTHER row must not steal its
    # selection — that is pinned by `test_a_rescan_leaves_another_rows_selection_alone`.
    page.select(DEDUPE_ROW)
    page.dedupe.find()                  # started, NOT awaited: the panel is busy now
    assert page.dedupe.is_running() is True
    assert page.is_running() is True
    assert page.run_button.isEnabled() is False
    assert page.run_all_button.isEnabled() is False
    assert page.can_run() is False
    page.run(SCRIPT)                    # programmatic: must start NOTHING
    assert page._process is None
    assert page._items[DEDUPE_ROW].text(1) == "running…"

    qtbot.waitUntil(lambda: not page.dedupe.is_running(), timeout=20000)
    assert page.run_all_button.isEnabled() is True
    # The scan leaves the operator on the PANEL's row (Д-1 of the Ч2а acceptance: the
    # row is what owns Find), so "Run" is off there by kind, not by busyness — pick a
    # check row and the door is back.
    assert page.selected_script() == DEDUPE_ROW
    assert page.run_button.isEnabled() is False
    page.select(SCRIPT)
    assert page.run_button.isEnabled() is True


def test_a_rescan_leaves_another_rows_selection_alone(main_window, qtbot, tmp_path):
    """Д-4.4 of the Ч2а acceptance: rebuilding the group nodes only re-selects INSIDE the
    copper row's own subtree.

    The nodes are deleted and re-created on every scan, and Qt moves the current item to the
    parent when the current CHILD disappears (measured —
    kicadstamp/diagnostics/probe_take_children_current_2026_10_25.py), so the "a vanished group
    goes back to the row" half needs no extra call at all. The other half is the one a naive
    implementation gets wrong: a scan started while the operator is reading a CHECK's report must
    not pull them onto the panel's row."""
    from tests.gui.docks.test_dedupe_dock import _board as _duplicate_board

    main_window.connection.board = SimpleNamespace(adapter=_duplicate_board())
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    page.dedupe.set_root_path(tmp_path / "root.sexp")

    page.select(SCRIPT)
    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        page.dedupe.find()

    assert page.dedupe.group_count() == 2            # the nodes WERE rebuilt ...
    assert page._items[DEDUPE_ROW].childCount() == 2
    assert page.selected_script() == SCRIPT          # ... and the check row kept the selection
    assert page.pane.currentWidget() is page.output


def test_a_running_check_turns_the_panels_doors_off(main_window, qtbot, tmp_path):
    """Д-1, direction TWO: while a CHECK process is alive the panel's Find and Clear
    are off and `clear()` does nothing — one socket owner, one operator. The panel
    keeps a found list across the run, so its Clear comes back when the check ends."""
    from tests.gui.docks.test_dedupe_dock import _board as _duplicate_board

    main_window.connection.board = SimpleNamespace(adapter=_duplicate_board())
    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 99))       # the check that hangs
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        page.dedupe.find()
    assert page.dedupe.has_groups() is True
    assert page.dedupe.clear_button.isEnabled() is True

    page.select(SCRIPT)
    page.run(SCRIPT)
    qtbot.waitUntil(lambda: page.is_running(), timeout=20000)
    assert page.dedupe.find_button.isEnabled() is False
    assert page.dedupe.clear_button.isEnabled() is False
    assert page.dedupe.can_clear() is False
    before = page.dedupe._active_op
    page.dedupe.clear()                 # programmatic: must start NOTHING
    assert page.dedupe._active_op is before

    with qtbot.waitSignal(page.finished, timeout=15000):
        page.stop()
    qtbot.waitUntil(lambda: not page.is_running(), timeout=5000)
    assert page.dedupe.find_button.isEnabled() is True
    assert page.dedupe.clear_button.isEnabled() is True


def test_the_panel_row_shows_its_own_verdict(main_window, qtbot, tmp_path):
    """The row's glyph is the PANEL's last scan: `·` before it, `⚠ N groups` with duplicates,
    `✔` on a clean board — never a CLI exit code."""
    from tests.gui.docks.test_dedupe_dock import _board as _duplicate_board

    page = _make_page(main_window, tmp_path)
    page.set_root_path(_profile(tmp_path, 0))
    row = page._items[DEDUPE_ROW]
    assert row.text(0).startswith("· ")

    main_window.connection.board = SimpleNamespace(adapter=_duplicate_board())
    page.dedupe.set_root_path(tmp_path / "root.sexp")
    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        page.dedupe.find()
    assert row.text(0).startswith("⚠ ")
    assert row.text(1) == "2 groups"

    main_window.connection.board = SimpleNamespace(
        adapter=_duplicate_board(duplicates=False))
    with qtbot.waitSignal(page.dedupe.finished_op, timeout=20000):
        page.dedupe.find()
    assert row.text(0).startswith("✔ ")
    assert row.text(1) == "0 groups"
