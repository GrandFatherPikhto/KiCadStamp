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
watchdog, rule 38) and adds one thing that watchdog did not need: the tree's OWN
root goes FIRST on the inner PYTHONPATH, ALWAYS, next to the absolutised inherited
entries. Both halves are needed, for different reasons:

  * the root (Н9): the inner programs run with `cwd=tmp_path` and `import gui.*`.
    Without the root on PYTHONPATH the interpreter falls back to the `.venv`
    editable install — the MAIN checkout — so every cell here would measure another
    tree's hook. That was an honest red while `main` had no hook module at all, and
    it turns into a FALSE GREEN the moment this branch is merged: a change verified
    in the worktree would pass on `main`'s older hook. Rule 39, exactly — a probe
    firing on the wrong sample.
  * absolute inherited entries: the pre-merge recipe
    (`PYTHONPATH=$PWD:../KiCadStamp-kq-site`) contains a RELATIVE entry that the
    interpreter resolves against the inner run's cwd — tmp_path, where that path
    does not exist — and then the inner run silently loses the plugins it needs.

The cells have several different meanings, so they are several functions (rule 35):
  * the process survives, and the switch really turns that off;
  * the hook itself behaves: one install, no escape from its own failure,
    SystemExit not dressed up as a crash;
  * a site that keeps failing is reported ONCE, with a count;
  * TWO actions through ONE shared wrapper are BOTH reported (Н8) — the wrapper is
    what makes the OUTERMOST frame non-unique, so this is the cell the narrower
    dedup keys died on;
  * the inner run imports THIS tree's code (Н9), asserted on the imported module's
    own `__file__`.
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

_INNER_SLOT_SITES = '''\
"""Real Qt slots on the UI thread, driven through Qt's own dispatch, with the
hook's Log records counted BY LEVEL.

Why through Qt and not by calling `_handle` directly: the OUTERMOST frame of the
traceback — the half Н6 adds to the dedup identity — is the slot PyQt called, and
only a real slot gives it that shape (`save_slot -> helper`). Two DIFFERENT slots
sharing ONE helper line is exactly the case the old key silenced.

Shapes:
  two-slots-one-helper — two different actions, ONE shared failing line;
  one-slot             — one action, N repeats (the storm);
  one-slot-two-lines   — one action, two different failing lines.
"""
import collections
import logging
import os
import sys
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from gui.slot_exception_hook import LOGGER_NAME, install_slot_exception_hook

install_slot_exception_hook(report_dir=os.environ["PROBE_REPORT_DIR"])
app = QApplication.instance() or QApplication(sys.argv)

# (level, message) of every record the hook emits through ITS logger. The MESSAGE
# is kept as well as the level: the cells assert that it names BOTH places, and
# they do it by looking for the two `file:line` substrings — which are ASCII and
# therefore the same whatever locale the inner process runs under.
records = []


class _Keep(logging.Handler):
    """The records the POINT of the deduplication is about: the Log lines."""

    def emit(self, record):
        records.append((record.levelname, record.getMessage()))


logging.getLogger(LOGGER_NAME).addHandler(_Keep())


def _helper(what):
    raise RuntimeError(f"helper failed for {what}")   # ONE shared failing line


def _save_slot():
    _helper("save")


def _redraw_slot():
    _helper("redraw")


_line_switch = {"n": 0}


def _two_line_slot():
    _line_switch["n"] += 1
    if _line_switch["n"] % 2:
        raise ValueError("first failure line")
    raise ValueError("second failure line")


SHAPES = {
    "two-slots-one-helper": (_save_slot, _redraw_slot),
    "one-slot": (_save_slot,),
    "one-slot-two-lines": (_two_line_slot,),
}
runs = int(os.environ["PROBE_RUNS"])
slots = SHAPES[os.environ["PROBE_SHAPE"]]
done = []

for index in range(runs):
    # Increasing intervals, so the sentinel below is guaranteed to come last: the
    # storm must be FULLY counted before the numbers are printed.
    QTimer.singleShot(index + 1, slots[index % len(slots)])
QTimer.singleShot(runs + 50, lambda: done.append(True))

while not done:
    app.processEvents()
    time.sleep(0.002)

print("levels", dict(collections.Counter(level for level, _ in records)), flush=True)
for level, message in records:
    print("record", level, message, flush=True)
'''

_INNER_WHICH_HOOK = '''\
"""Which copy of the hook did the inner interpreter import? (Н9)

Nothing about Qt: this program asks ONE question — whose
`gui/slot_exception_hook.py` is on the path — and answers it with the imported
module's own `__file__`, which is what the guard asserts on.
"""
from gui import slot_exception_hook

print("hook_file", slot_exception_hook.__file__, flush=True)
'''

_INNER_SHARED_WRAPPER = '''\
"""MODEL of the shared-wrapper shape Н8 is about — a MODEL, not the live code.

Rule 39 in its other direction: this program reproduces the DEVICE that makes the
OUTERMOST frame non-unique, and says so, instead of pretending to measure
`gui/worker.py`. The device is small and real: ONE function is the outermost frame
of every failure it arms, so every action it arms SHARES that frame, while the
exception itself is born in ONE shared helper line. `refresh_snapshot_then` is
exactly that shape — it hands the caller's continuation to `start_long_op` as ONE
`lambda _result: on_ready()` line, and PyQt calls that lambda when the worker
finishes, so the outermost frame of a failure born in the continuation is the
WRAPPER's line, the same for all ten callers. Reaching the live function needs a
connection, a controller and a worker thread; that is why the model is here and the
real thing is in `_INNER_REAL_WRAPPER` below.

Shapes (PROBE_SHAPE):
  two-callbacks — two DIFFERENT actions through ONE wrapper, ONE shared failing
                  line: the case the narrower dedup key silenced;
  one-callback  — ONE action xN through the same wrapper: the storm that must stay
                  ONE key.
"""
import collections
import functools
import logging
import os
import sys
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from gui import slot_exception_hook as hook

hook.install_slot_exception_hook(report_dir=os.environ["PROBE_REPORT_DIR"])
app = QApplication.instance() or QApplication(sys.argv)

records = []


class _Keep(logging.Handler):
    def emit(self, record):
        records.append((record.levelname, record.getMessage()))


logging.getLogger(hook.LOGGER_NAME).addHandler(_Keep())


def _helper(what):
    raise RuntimeError(f"wrapper failed for {what}")   # ONE shared failing line


def _wrapper(continuation):
    """The shared wrapper: Qt calls THIS one function (functools.partial adds no
    Python frame of its own), and it goes on into whichever continuation it was
    armed with — the shape of the one lambda line inside `start_long_op`."""
    continuation()


def _action_a():
    _helper("a")


def _action_b():
    _helper("b")


SHAPES = {
    "two-callbacks": (_action_a, _action_b),
    "one-callback": (_action_a,),
}
runs = int(os.environ["PROBE_RUNS"])
actions = SHAPES[os.environ["PROBE_SHAPE"]]
for index in range(runs):
    QTimer.singleShot(index + 1,
                      functools.partial(_wrapper, actions[index % len(actions)]))
done = []
QTimer.singleShot(runs + 50, lambda: done.append(True))

while not done:
    app.processEvents()
    time.sleep(0.002)

print("levels", dict(collections.Counter(level for level, _ in records)), flush=True)
for level, message in records:
    print("record", level, message, flush=True)
print("counts", hook.failure_counts(), flush=True)
'''

_INNER_REAL_WRAPPER = '''\
"""The REAL gui.worker.refresh_snapshot_then, reached with `start_long_op` replaced.

Why on top of the model above: the model is the shape as it is understood, this one
pins that gui/worker.py REALLY has it — that `refresh_snapshot_then` wraps the
caller's continuation in ONE line of its own and hands THAT to the machinery which
later calls it from the event loop. The replacement keeps the hand-over as the real
one does (`on_success` is what `start_long_op` connects to `controller.finished`)
and drops only the thread and the socket: the probe keeps that callable and lets Qt
call it, which is how PyQt calls it when a worker finishes — through a
`functools.partial`, so the wrapper's OWN lambda line is the OUTERMOST frame of the
traceback, the way it is in battle.

Two different actions (two callers, two continuations), ONE shared wrapper line,
ONE shared failing helper line — the Н8 case. The guard then asserts that the
report and the Log line name the ACTION as the entry and NOT the wrapper's lambda
line: that half is what `entry_site` skipping wrapper frames buys, and the reports'
tracebacks are there to prove the wrapper really was on the stack.
"""
import collections
import functools
import logging
import os
import sys
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import gui.worker as worker
from gui import slot_exception_hook as hook

hook.install_slot_exception_hook(report_dir=os.environ["PROBE_REPORT_DIR"])
app = QApplication.instance() or QApplication(sys.argv)

records = []


class _Keep(logging.Handler):
    def emit(self, record):
        records.append((record.levelname, record.getMessage()))


logging.getLogger(hook.LOGGER_NAME).addHandler(_Keep())


def _helper(what):
    raise RuntimeError(f"wrapper failed for {what}")   # ONE shared failing line


class _Connection:
    """Only what refresh_snapshot_then asks BEFORE it hands the work over: a
    refreshable snapshot and a socket nobody holds."""

    long_op_active = False
    snapshot_refresh_supported = True


armed = []


def _fake_start_long_op(connection, widgets, fn, on_success, on_error, *args,
                        **kwargs):
    """`start_long_op` without a thread: it keeps the callable the real one would
    have connected to `controller.finished`, so the probe can let Qt call it."""
    armed.append(on_success)
    return None


worker.start_long_op = _fake_start_long_op


def _on_error(message):
    print("error", message, flush=True)


def _on_ready_a():
    _helper("a")


def _on_ready_b():
    _helper("b")


def _save_slot():
    worker.refresh_snapshot_then(_Connection(), [], _on_ready_a, _on_error)


def _redraw_slot():
    worker.refresh_snapshot_then(_Connection(), [], _on_ready_b, _on_error)


# The two callers run first, exactly as the two buttons' slots would: they only
# REGISTER a continuation and return — the failure happens long after, on the event
# loop, which is why the caller's own frame is not in the traceback at all.
_save_slot()
_redraw_slot()
if len(armed) != 2:
    # A verdict must never depend on a timer race: say so loudly instead of raising
    # IndexError out of a timer callback.
    print("NOT-ARMED", len(armed), flush=True)
else:
    # Qt then calls the two continuations — the wrapper's own lambda line. The
    # partial is load-bearing: it adds no Python frame of its own, so the wrapper's
    # lambda IS the outermost frame of the traceback, exactly as when PyQt delivers
    # controller.finished. A plain probe lambda here would become the outermost
    # frame and the cell would stop measuring the wrapper at all.
    QTimer.singleShot(1, functools.partial(armed[0], None))
    QTimer.singleShot(2, functools.partial(armed[1], None))
done = []
QTimer.singleShot(60, lambda: done.append(True))

while not done:
    app.processEvents()
    time.sleep(0.002)

print("levels", dict(collections.Counter(level for level, _ in records)), flush=True)
for level, message in records:
    print("record", level, message, flush=True)
'''


def _inner_env(report_dir=None, **extra):
    """The inner run's environment: offscreen, THIS tree's root FIRST on
    PYTHONPATH, the inherited entries after it (made ABSOLUTE), and the row's own
    variables.

    The root is not optional and not conditional (Н9): the inner programs run with
    `cwd=tmp_path`, so without it `import gui.*` resolves through the `.venv`
    editable install — the MAIN checkout — and every cell below would be measuring
    another tree. See the module docstring for why that matters more after the
    merge than before it.

    The absolutisation of the inherited entries is the other half, and it is not
    decoration either: a relative entry of the pre-merge recipe resolves against
    the INNER cwd (tmp_path), where it points at nothing, and the inner run
    silently loses the plugins it needs. An entry that repeats the root is dropped,
    so the root cannot be pushed back by a duplicate the caller exported.
    """
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **extra)
    entries = [str(_REPO_ROOT)]
    entries += [os.path.abspath(entry) for entry
                in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(entries))
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


def _level_counts(stdout, output) -> dict:
    """Parse the inner program's `levels {LEVEL: n, ...}` line into a dict."""
    line = next(line for line in stdout.splitlines() if line.startswith("levels "))
    return ast.literal_eval(line[len("levels "):])


def _record_lines(stdout) -> list:
    """(level, message) of every hook record the inner program printed."""
    out = []
    for line in stdout.splitlines():
        if line.startswith("record "):
            _, level, message = line.split(" ", 2)
            out.append((level, message))
    return out


def _report_field(text, name) -> str:
    """The value of a `name: value` line of a report file."""
    prefix = name + ": "
    line = next(ln for ln in text.splitlines() if ln.startswith(prefix))
    return line[len(prefix):]


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


# ── Н6: different actions dying in ONE shared line are not silent ───────────

@pytest.mark.parametrize("shape,runs,expect_critical,expect_reports", [
    ("two-slots-one-helper", 2, 2, 2),
    ("one-slot", 25, 1, 1),
    ("one-slot-two-lines", 2, 2, 2),
], ids=["two-slots-one-helper", "one-slot-many-repeats",
        "one-slot-two-failure-lines"])
def test_two_actions_dying_in_one_shared_line_are_both_reported(
        tmp_path, shape, runs, expect_critical, expect_reports):
    """Н6 — the deduplication identity is (ENTRY frame, FAILURE frame, type), and
    this measures the half that was MISSING: two DIFFERENT slots whose exception is
    born in ONE shared helper line must each get a Log line and a report.

    Why this is a defect and not a nicety: counting by the deepest frame alone
    collapsed every failure born in one shared library line — a closed KiCad (one
    kipy `client.py` line), one `_live_adapter`, one door refusal — into ONE
    record, so the SECOND action printed nothing at all: the user pressed, nothing
    happened, and no line said so. Same shape as the "a refusal does not go silent"
    rule the door and `socket_busy` already follow.

    The third row keeps the OTHER half honest: one action failing on two DIFFERENT
    lines is two identities, not one. The second row is the storm's own cell — the
    entry fix must NOT re-open the flood (one action ×25 stays one CRITICAL).

    "The Log line and the report name BOTH places" is measured here too, not
    assumed: each report carries `entry:` (the action) and `site:` (the failing
    address), each CRITICAL line carries both `file:line` substrings, and for the
    shared-helper row the failing frame must be the ONE DEEPEST one (the helper
    line), not the outermost — reporting the outermost as the failure would name
    the slot twice and send the reader to the wrong line.
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_SLOT_SITES,
                      _inner_env(reports, PROBE_SHAPE=shape,
                                 PROBE_RUNS=str(runs)))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    levels = _level_counts(proc.stdout, output)
    assert levels.get("CRITICAL", 0) == expect_critical, (
        f"the number of CRITICAL Log lines must be the number of DIFFERENT actions "
        f"(entry+failure+type), not the number of failing lines — got "
        f"{levels}\n{output}")
    texts = _report_texts(reports)
    assert len(texts) == expect_reports, (
        f"one report per identity — got {len(texts)}, expected "
        f"{expect_reports}\n{output}")

    # Matched by SUBSTRING, never by the sentence around them: the sentence is
    # translated, the two `file:line` addresses are not.
    pairs = [(_report_field(t, "entry"), _report_field(t, "site")) for t in texts]
    critical = [message for level, message in _record_lines(proc.stdout)
                if level == "CRITICAL"]
    for entry, site in pairs:
        assert any(entry in message and site in message for message in critical), (
            f"a CRITICAL Log line must name BOTH the entry frame ({entry}) and the "
            f"failing frame ({site})\n{output}")
    if shape == "two-slots-one-helper":
        # The identity's two halves, pinned: ONE shared failing line (the deepest
        # frame — the address to open, the same for both actions) and TWO entries
        # (the actions to redo). A rule that reported the OUTERMOST frame as the
        # failure would collapse the two into the slots' own lines.
        entries = {entry for entry, _ in pairs}
        sites = {site for _, site in pairs}
        assert len(entries) == 2 and len(sites) == 1, (
            f"two slots one shared helper line: two entry frames, ONE failing "
            f"frame — got {pairs}\n{output}")
        assert entries.isdisjoint(sites), (
            f"the failing frame must be the DEEPEST one (the helper line), named "
            f"separately from the slot that was entered — got {pairs}\n{output}")


# ── Н7: the Log lines themselves, by level ─────────────────────────────────

@pytest.mark.parametrize("runs,expect_critical,expect_error", [
    (3, 1, 0),
    (25, 1, 1),
    (100, 1, 2),
], ids=["three-repeats", "twenty-five-repeats", "hundred-repeats"])
def test_a_storm_keeps_the_log_bounded_and_speaks_at_each_power_of_ten(
        tmp_path, runs, expect_critical, expect_error):
    """Н7 — the policy the report argues for ("without dedup the Log would freeze")
    had NO cell measuring Log lines: the rows above count report files and
    `failure_counts()`, not the records the `kicadstamp.slot_exception` logger
    emits — and the Log lines are the whole reason the dedup exists.

    This intercepts those records INSIDE the inner process and asserts by level:
    exactly ONE CRITICAL for the first occurrence (and one report), plus ONE ERROR
    each time the count crosses a power of ten. The growth is log10(N), not N —
    measured: 3 repeats say nothing after the first, 25 add one line (at the 10th),
    100 add two (10th and 100th). Both mutations of the policy go red on this cell:
    a line on every repeat (too many) and a policy that never speaks again (too
    few).
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_SLOT_SITES,
                      _inner_env(reports, PROBE_SHAPE="one-slot",
                                 PROBE_RUNS=str(runs)))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    levels = _level_counts(proc.stdout, output)
    assert levels.get("CRITICAL", 0) == expect_critical, (
        f"one action, {runs} repeats: exactly one CRITICAL Log line — got "
        f"{levels}\n{output}")
    assert levels.get("ERROR", 0) == expect_error, (
        f"{runs} repeats of ONE identity must speak again at 10 (and 100) only — "
        f"got {levels}\n{output}")
    assert len(_report_texts(reports)) == 1, (
        f"one report for one identity however many times it repeats\n{output}")


# ── Н8: two actions through ONE shared wrapper are both reported ────────────

@pytest.mark.parametrize("shape,runs,expect_critical,expect_reports,expect_counts", [
    ("two-callbacks", 2, 2, 2, (1, 1)),
    ("one-callback", 25, 1, 1, (25,)),
], ids=["two-callbacks-one-wrapper", "one-callback-many-repeats"])
def test_two_actions_through_one_shared_wrapper_are_both_reported(
        tmp_path, shape, runs, expect_critical, expect_reports, expect_counts):
    """Н8 — the other half of the entry-frame defect, and the reason the dedup key
    is now the WHOLE STACK: the OUTERMOST frame stops being an identity as soon as
    what PyQt calls is a SHARED WRAPPER.

    gui/worker.py's `refresh_snapshot_then` hands its continuation to
    `start_long_op` as ONE `lambda _result: on_ready()` line, shared by all ten of
    its callers (`defer_while_socket_busy` adds four more through `_retry` ->
    `proceed()`). Two different actions through such a wrapper, dying in one shared
    library line, produce ONE (outermost, deepest, type) triple — so the SECOND
    action said NOTHING: the user pressed, nothing happened, no line anywhere. This
    cell drives exactly that corner and demands two identities.

    The inner program is a MODEL of the wrapper's DEVICE, not the live
    `refresh_snapshot_then` (rule 39), and it says so itself; the live function is
    the next cell's business. The model is not a shortcut around the point: ONE
    function is the outermost frame of every failure it arms, which is precisely
    what makes the entry frame non-unique.

    The second row is the storm's own cell: ONE action x25 through the same wrapper
    must stay ONE identity, or the fix for the first row re-opens the flood the
    dedup exists to prevent.
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_SHARED_WRAPPER,
                      _inner_env(reports, PROBE_SHAPE=shape,
                                 PROBE_RUNS=str(runs)))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    levels = _level_counts(proc.stdout, output)
    assert levels.get("CRITICAL", 0) == expect_critical, (
        f"through ONE wrapper and ONE shared failing line the number of CRITICAL "
        f"Log lines must still be the number of DIFFERENT actions — got "
        f"{levels}\n{output}")
    texts = _report_texts(reports)
    assert len(texts) == expect_reports, (
        f"one report per identity — got {len(texts)}, expected "
        f"{expect_reports}\n{output}")
    counts_line = next(line for line in proc.stdout.splitlines()
                       if line.startswith("counts "))
    counts = ast.literal_eval(counts_line[len("counts "):])
    assert sorted(counts.values(), reverse=True) == list(expect_counts), (
        f"the counted identities must be per ACTION: two different actions through "
        f"one wrapper are two keys, one action xN is ONE key — got {counts}\n"
        f"{output}")
    if shape == "two-callbacks":
        # The frame that tells them apart is the ACTION, not the wrapper: every
        # traceback here starts at the SAME wrapper line and ends at the SAME
        # helper line, so the stacks can differ only between those two.
        stacks = [key[0] for key in counts]
        assert len({stack[0] for stack in stacks}) == 1, (
            f"both failures must start at the ONE wrapper frame — that is the "
            f"corner this cell exists for; got {stacks}\n{output}")
        assert len({stack[-1] for stack in stacks}) == 1, (
            f"both failures must end at the ONE shared failing line; got "
            f"{stacks}\n{output}")


def test_the_real_shared_wrapper_is_not_the_reported_entry(tmp_path):
    """Н8 — the same corner measured on the REAL
    `gui.worker.refresh_snapshot_then` instead of a model: its wrapper line must be
    ON the stack while the SHOWN entry must be the action that was pressed.

    Why the display is asserted here and not only the count: the wrapper's lambda
    line is now the outermost frame of the traceback, and a reader sent to
    `lambda _result: on_ready()` learns nothing about which action to redo. The pair
    the Log line and the report carry is the SHOWN pair, so it must name the caller
    — `entry_site` skipping gui/worker.py frames is what does that, and this is its
    cell.

    `start_long_op` is replaced, the wrapper is NOT: the substitute keeps the
    hand-over as the real one does (it holds on to the callable `start_long_op`
    would have connected to `controller.finished`), and the probe calls that same
    callable from a QTimer — which is how PyQt calls it when a worker finishes. So
    gui/worker.py runs its own code and the traceback really goes through it, which
    the report's own text is asked to prove.
    """
    reports = tmp_path / "reports"
    proc = _run_inner(tmp_path, _INNER_REAL_WRAPPER, _inner_env(reports))
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "NOT-ARMED" not in proc.stdout, (
        f"a continuation was never armed — the verdict below would then not be "
        f"about the wrapper at all\n{output}")
    assert "error" not in proc.stdout, output
    levels = _level_counts(proc.stdout, output)
    assert levels.get("CRITICAL", 0) == 2, (
        f"two different actions through the real wrapper, one shared failing line: "
        f"two CRITICAL Log lines, not one — got {levels}\n{output}")
    texts = _report_texts(reports)
    assert len(texts) == 2, (
        f"two identities, two reports — got {len(texts)}\n{output}")

    pairs = [(_report_field(t, "entry"), _report_field(t, "site")) for t in texts]
    entries = {entry for entry, _ in pairs}
    sites = {site for _, site in pairs}
    assert len(entries) == 2, (
        f"the entry must be the ACTION, and two actions are two entries — got "
        f"{pairs}\n{output}")
    assert len(sites) == 1, (
        f"one shared failing line for both actions — got {pairs}\n{output}")
    for entry in entries:
        assert "gui/worker.py" not in entry, (
            f"the entry must NOT be the wrapper's own lambda line: the reader is "
            f"told to redo an action, not a wrapper — got {entry}\n{output}")
        assert Path(entry.rsplit(":", 1)[0]).name == "probe.py", (
            f"the entry must lie inside the action (the inner program) — got "
            f"{entry}\n{output}")
    for text in texts:
        assert "gui/worker.py" in text, (
            f"the report must carry the wrapper on its stack, or this cell is not "
            f"measuring the wrapper at all:\n{text}")

    critical = [message for level, message in _record_lines(proc.stdout)
                if level == "CRITICAL"]
    for entry, site in pairs:
        assert any(entry in message and site in message for message in critical), (
            f"a CRITICAL Log line must name BOTH the entry ({entry}) and the "
            f"failing frame ({site})\n{output}")


# ── Н9: the inner run must import THIS tree, not the main checkout ──────────

def test_the_inner_run_imports_the_hook_from_this_tree(tmp_path):
    """Н9 — every cell above is worth only what the code it measures is worth: the
    inner programs run with `cwd=tmp_path` and `import gui.*`, so without this
    tree's root on their PYTHONPATH they resolve through the `.venv` editable
    install, i.e. the MAIN checkout.

    Measured 2026-09-24 in this tree, pytest-qt already in the venv, no PYTHONPATH:
    `13 failed, 1 passed` in this file, every failure `ModuleNotFoundError: No
    module named 'gui.slot_exception_hook'` — an honest red while `main` had no hook
    module at all, and a FALSE GREEN the moment the branch is merged. That is rule
    39 precisely: a probe firing on the wrong sample.

    The cell asks the inner process for the imported module's own `__file__` and
    demands it lie under THIS tree's root (the tree the guard itself belongs to),
    so the answer cannot depend on what the environment happens to export.
    """
    proc = _run_inner(tmp_path, _INNER_WHICH_HOOK, _inner_env())
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    line = next(line for line in proc.stdout.splitlines()
                if line.startswith("hook_file "))
    hook_file = Path(line[len("hook_file "):]).resolve()
    assert hook_file.is_relative_to(_REPO_ROOT), (
        f"the inner run imported the hook from OUTSIDE this tree — the cells above "
        f"would be measuring another checkout's code ({hook_file})\n{output}")
    assert hook_file == (_REPO_ROOT / "gui" / "slot_exception_hook.py").resolve(), (
        f"the inner run must import THIS tree's hook module — got {hook_file}\n"
        f"{output}")
