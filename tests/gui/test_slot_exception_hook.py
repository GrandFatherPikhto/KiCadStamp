# tests/gui/test_slot_exception_hook.py
"""Н3 of plan_2026_09_24_slot_exception_hook — the battle hook, measured the only
way it can be: in a SUBPROCESS.

The property under test is what happens to the WHOLE PROCESS when a slot raises.
PyQt6 answers an exception escaping a slot with `qFatal()` unless `sys.excepthook`
is somebody else's, and a watchdog for that cannot live in the process it is
measuring: without the hook the watchdog would die together with the run it is
supposed to report on. Every row below therefore runs a tiny program in a
subprocess and asserts on ITS exit code, ITS stdout and the report files IT wrote.

The rig repeats the shape of tests/gui/test_qt_slot_exception_capture.py (the Кq
watchdog, rule 38), including its hardest-won detail: PYTHONPATH entries are made
ABSOLUTE before the inner run, because the pre-merge recipe
(`PYTHONPATH=$PWD:../KiCadStamp-kq-site`) contains a RELATIVE entry that the
interpreter resolves against the inner run's cwd — tmp_path, where that path does
not exist — and then the inner run silently loses the plugins it needs.

Three functions, because the cells have three different meanings (rule 35):
  * the process survives, and the switch really turns that off;
  * the hook itself behaves: one install, no escape from its own failure,
    SystemExit not dressed up as a crash;
  * a site that keeps failing is reported ONCE, with a count.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ── the inner programs ───────────────────────────────────────────────────────
# Each one is a standalone python program: no pytest, no fixtures, nothing that
# could fail for a reason other than the property under test.

_INNER_QT_SLOT = '''\
"""A slot on the UI thread; the hook is installed only when told to."""
import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from gui.slot_exception_hook import install_slot_exception_hook

if os.environ.get("PROBE_INSTALL") == "1":
    install_slot_exception_hook(report_dir=os.environ["PROBE_REPORT_DIR"])

app = QApplication.instance() or QApplication(sys.argv)


def _boom() -> None:
    raise RuntimeError("BOOM from a slot on the UI thread")


QTimer.singleShot(0, _boom)
app.processEvents()
print("SURVIVED", flush=True)
'''

_INNER_HOOK_BEHAVIOUR = '''\
"""The hook's own cells, with no Qt involved at all."""
import sys

from gui import slot_exception_hook as hook

first = hook.install_slot_exception_hook()
second = hook.install_slot_exception_hook()
print("install", first, second, sys.excepthook is hook._handle, flush=True)

# SystemExit must reach the hook that was there before us, and must NOT be logged
# as a slot failure (no report, no count for it).
seen = []


def _previous(type_, value, tb):
    seen.append(type_)


sys.excepthook = _previous
hook.uninstall_slot_exception_hook()
hook.install_slot_exception_hook()
sys.excepthook(SystemExit, SystemExit(7), None)
print("systemexit", [t.__name__ for t in seen], hook.failure_counts(), flush=True)

# The hook's own failure must not escape: it has no handler behind it any more.
hook.uninstall_slot_exception_hook()
hook.install_slot_exception_hook()


def _broken(*args, **kwargs):
    raise RuntimeError("hook internals broken")


hook.failing_site = _broken
try:
    sys.excepthook(RuntimeError, RuntimeError("boom"), None)
except BaseException as exc:                      # noqa: BLE001 — this IS the probe
    print("ESCAPED", type(exc).__name__, exc, flush=True)
else:
    print("no exception escaped the broken hook", flush=True)
'''

_INNER_DEDUP = '''\
"""One failing site, several times — how many reports and what counts."""
import os
import sys
from pathlib import Path

from gui import slot_exception_hook as hook


def _same_site() -> None:
    raise RuntimeError("BOOM repeated")


def _other_site() -> None:
    raise ValueError("BOOM elsewhere")


hook.install_slot_exception_hook(report_dir=os.environ["PROBE_REPORT_DIR"])
sites = [_same_site] * int(os.environ["PROBE_RUNS"])
if os.environ.get("PROBE_TWO_SITES") == "1":
    sites.append(_other_site)
for fn in sites:
    try:
        fn()
    except BaseException:                          # noqa: BLE001 — the probe drives it
        hook._handle(*sys.exc_info())

reports = sorted(Path(os.environ["PROBE_REPORT_DIR"]).glob("slot_exception_*.txt"))
print("reports", len(reports), flush=True)
print("counts", hook.failure_counts(), flush=True)
'''


def _inner_env(report_dir=None, **extra):
    """The inner run's environment: offscreen, an ABSOLUTE PYTHONPATH, and the
    row's own variables.

    The absolutisation is not decoration — see the module docstring: a relative
    entry of the pre-merge recipe resolves against the INNER cwd (tmp_path), where
    it points at nothing. When there is no PYTHONPATH at all (after the merge
    pytest-qt lives in the venv) the key is simply not set.
    """
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **extra)
    entries = [os.path.abspath(entry) for entry
               in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    if entries:
        env["PYTHONPATH"] = os.pathsep.join(entries)
    if report_dir is not None:
        env["PROBE_REPORT_DIR"] = str(report_dir)
    return env


def _run_inner(tmp_path, source, env, name="probe.py"):
    """Write the inner program into tmp_path and run it. Returns the proc."""
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def _report_texts(directory) -> list:
    return sorted(path.read_text(encoding="utf-8") for path
                  in Path(directory).glob("slot_exception_*.txt"))


def test_the_battle_entry_point_arms_the_hook_before_the_window():
    """Н2 — the hook must be installed by THE entry point, and BEFORE MainWindow is
    built: an exception raised in the window's own constructor is precisely the case
    a hook installed any later would miss.

    Structural on purpose — the shape tests/test_board_door_guard.py uses for the
    door's arming line: the alternative is starting a real GUI inside a test, and
    this project never does that. It is also the only cell that pins the ARMING: the
    subprocess rows below install the hook themselves in their inner programs, so
    deleting the call from kicadstamp/gui_main.py would leave every one of them green
    while the battle GUI lost the protection entirely.
    """
    source = (_REPO_ROOT / "kicadstamp" / "gui_main.py").read_text(encoding="utf-8")
    install_at = source.find("install_slot_exception_hook()")
    window_at = source.find("window = MainWindow(")

    assert "from gui.slot_exception_hook import install_slot_exception_hook" in source, (
        "kicadstamp/gui_main.py does not import the hook at all")
    assert install_at != -1, (
        "kicadstamp/gui_main.py no longer installs the Qt-slot crash hook — the "
        "battle GUI is back to losing the session on the first exception in a slot")
    assert window_at != -1, (
        "MainWindow construction is not where this cell expects it — the cell is "
        "stale, and a stale probe passes for the wrong reason")
    assert install_at < window_at, (
        "the hook is installed AFTER MainWindow is built: the window's constructor "
        "is then uncovered, and that is the one place a later install cannot help")


# ── the process survives, and the switch really turns that off ───────────────

_WITH_HOOK = {"PROBE_INSTALL": "1"}
_WITHOUT_HOOK = {"PROBE_INSTALL": "0"}
_SWITCHED_OFF = {"PROBE_INSTALL": "1", "KICADSTAMP_SLOT_EXCEPTION_HOOK": "0"}


@pytest.mark.parametrize("row_id,extra", [
    ("with-the-hook", _WITH_HOOK),
    ("control-no-hook", _WITHOUT_HOOK),
    ("switch-off-no-hook-either", _SWITCHED_OFF),
], ids=["with-the-hook", "control-no-hook", "switch-off"])
def test_a_slot_exception_no_longer_kills_the_process(tmp_path, row_id, extra):
    """Н3 — with the hook armed, an exception raised inside a Qt slot leaves the
    process ALIVE and writes a report; with the hook absent, and with
    `KICADSTAMP_SLOT_EXCEPTION_HOOK=0`, the very same program dies with
    `Fatal Python error`.

    The third row is the switch's own cell: a protection nobody can turn off for a
    diagnostic run is a protection nobody can measure around. It also proves the
    first row is not green for a reason unrelated to the hook.
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_QT_SLOT,
                      _inner_env(reports, **extra))
    output = proc.stdout + proc.stderr

    if extra.get("PROBE_INSTALL") == "1" and "KICADSTAMP_SLOT_EXCEPTION_HOOK" not in extra:
        assert proc.returncode == 0, (
            f"the session must survive the slot exception — got "
            f"returncode={proc.returncode}\n{output}")
        assert "SURVIVED" in proc.stdout, (
            f"the program must reach its last line — that is what 'the session "
            f"lives' means here\n{output}")
        assert "Fatal Python error" not in output, output
        texts = _report_texts(reports)
        assert len(texts) == 1, (
            f"exactly one report for one failing site — got {len(texts)}\n{output}")
        assert "RuntimeError: BOOM from a slot on the UI thread" in texts[0], texts[0]
        assert "traceback:" in texts[0] and "the hook itself" not in texts[0], texts[0]
        assert "was NOT completed" in texts[0], (
            "the report must say the action did not complete — a survived session "
            "is not a completed action", texts[0])
    else:
        assert proc.returncode != 0, (
            f"without the hook this program cannot end cleanly — got "
            f"returncode={proc.returncode}\n{output}")
        assert proc.returncode < 0 or proc.returncode == 134, (
            f"the control must die from a SIGNAL (SIGABRT: a negative returncode on "
            f"POSIX, 134 through a shell) — got {proc.returncode}\n{output}")
        # Deliberately NOT asserting the interpreter's own words here. A bare python
        # process does not enable faulthandler, so "Fatal Python error" is a spelling
        # that appears only when faulthandler is ON — pytest turns it on, which is why
        # the door runs (and the Кq watchdog) show that line while this program does
        # not. The property is the abnormal death above, plus the traceback PyQt6
        # itself prints into stderr before it calls qFatal.
        assert "RuntimeError: BOOM from a slot on the UI thread" in output, output
        assert _report_texts(reports) == [], (
            "a run with no hook must not write a report — nothing caught anything")


# ── the hook itself behaves ─────────────────────────────────────────────────

def test_the_hook_installs_once_never_escapes_and_does_not_swallow_systemexit(
        tmp_path):
    """Н3 — three cells of the hook's own contract, in one program: a second
    install does not wrap the first; a `SystemExit` reaches the hook that was
    there before us and is NOT counted as a slot failure; and a failure INSIDE the
    hook does not escape into the interpreter (it lands on fd 2 and the process
    lives).

    The middle cell is why the hook does not swallow SystemExit: whatever the user
    or a future writer meant by exiting, our job is not to decide for them — and a
    deliberate exit must not appear in the Log as a crash.
    """
    proc = _run_inner(tmp_path, _INNER_HOOK_BEHAVIOUR, _inner_env())
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "install True False True" in proc.stdout, (
        f"the first install must install, the second must refuse (False), and the "
        f"handler must still be ours — a wrapped hook would grow a frame per call "
        f"and move the reported site\n{output}")
    assert "systemexit ['SystemExit'] {}" in proc.stdout, (
        f"SystemExit must go to the previous hook and produce no count\n{output}")
    assert "no exception escaped the broken hook" in proc.stdout, (
        f"a failure inside the hook must not escape — it has no handler behind "
        f"it\n{output}")
    assert "the hook itself failed while reporting it" in proc.stderr, (
        f"the fallback must reach fd 2 — that is the only channel that cannot be "
        f"captured or closed under it\n{output}")


# ── a site that keeps failing is reported once ──────────────────────────────

@pytest.mark.parametrize("runs,two_sites,expect_reports,expect_counts", [
    (3, "0", 1, (3,)),
    (25, "0", 1, (25,)),
    (2, "1", 2, (2, 1)),
], ids=["three-repeats", "twenty-five-repeats", "two-different-sites"])
def test_a_failing_site_is_reported_once_whatever_its_repeat_count(
        tmp_path, runs, two_sites, expect_reports, expect_counts):
    """Н3 — the deduplication policy, measured: N failures of ONE site produce ONE
    report file and a count of N; failures of TWO sites produce two reports.

    Why this is not cosmetics: a failing eventFilter fires on EVERY event delivered
    to that widget — one broken filter produced 17 calls in 0.5 s of event pumping
    — and every Log line is appended to a QPlainTextEdit on the UI thread. Without
    the policy the survivor mechanism would trade a crash for a frozen Log.
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_DEDUP,
                      _inner_env(reports, PROBE_RUNS=str(runs),
                                 PROBE_TWO_SITES=two_sites))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert f"reports {expect_reports}" in proc.stdout, (
        f"the number of report files must be the number of DIFFERENT sites\n"
        f"{output}")
    # The inner program prints `counts {site: n, ...}` with repr(), so it is a
    # Python literal — parsed as one instead of being picked apart by a regex (the
    # first version of this line matched only the LAST entry of the dict, because
    # it required `}` right after the number).
    counts_line = next(line for line in proc.stdout.splitlines()
                       if line.startswith("counts "))
    counts = ast.literal_eval(counts_line[len("counts "):])
    assert sorted(counts.values(), reverse=True) == list(expect_counts), (
        f"each site must be counted, and the counts must be per site — got "
        f"{counts}, expected {expect_counts}\n{output}")
