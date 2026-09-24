# tests/gui/test_qt_slot_exception_capture.py
"""С1 of plan_2026_09_24_kq_pytest_qt — an exception raised inside a Qt slot must
arrive as a RED cell that NAMES itself, never as a core dump.

Why a SUBPROCESS. The property under test is what happens to the WHOLE PROCESS
when a slot raises: PyQt6 answers an exception escaping a slot with `qFatal()`
whenever `sys.excepthook` is still the default one, and that is a SIGABRT —
measured 2026-09-24 on main: `Fatal Python error: Aborted`, EXIT=134, the run
gone from the 15th cell on. pytest-qt replaces the hook on every test phase, so
with the plugin loaded the same slot yields a FAILED naming the file and line of
the refusal. A watchdog for that cannot live in the same process as the thing it
measures: were the plugin missing, the watchdog would die together with the run
it is supposed to report on. Every row below therefore runs a tiny pytest
session in a subprocess and asserts on ITS outcome.

The rig repeats the measurement of diagnostics/probe_slot_excepthook.py (saved
2026-09-24): a slot on the UI thread under `processEvents()` — the same shape as
tests/gui/conftest.py's `_pump` — and a slot on a QThread.

The third row is the CONTROL (deepseek.md §39): with `-p no:pytest-qt` the very
same slot test must die with a `Fatal Python error` dump. Without that row, a
green C1 on a machine where the inner subprocess never raises at all would look
exactly like a real green. The control MEANS something only together with the
row above it: the catch row passes only when the plugin is actually loadable in
the inner run (its wording can come from nowhere else), so the pair proves the
flag disabled a plugin that was there.

WHY PYTHONPATH IS ABSOLUTISED BELOW — measured, not guessed: the pre-merge
recipe is `PYTHONPATH=$PWD:../KiCadStamp-kq-site` (plan §4), and a RELATIVE entry
is resolved by the interpreter against ITS OWN working directory. The inner run's
directory is tmp_path, where `../KiCadStamp-kq-site` is not, so pytest-qt was
unreachable there: the first run of this very file had BOTH catch rows aborting
with a dump while the control passed — passing for the wrong reason, since
`-p no:pytest-qt` disables nothing when the plugin was never loadable.

Numbers live in the docstrings, names describe the property (rule 37).
"""
import os
import subprocess
import sys
from typing import NamedTuple

import pytest


_UI_THREAD_SLOT = '''\
"""A slot on the UI thread, driven by processEvents() — the _pump shape."""
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)


def _boom() -> None:
    raise RuntimeError("BOOM from a slot on the UI thread")


def test_exception_inside_a_qt_slot() -> None:
    QTimer.singleShot(0, _boom)
    app.processEvents()
'''


_QTHREAD_SLOT = '''\
"""A slot on a QThread — the probe's own shape (worker.go.connect(worker.run))."""
import sys
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)


class _Worker(QObject):
    go = pyqtSignal()

    def run(self) -> None:
        raise RuntimeError("BOOM from a slot on a QThread")


def test_exception_inside_a_slot_on_a_worker_thread() -> None:
    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    worker.go.connect(worker.run)
    thread.start()
    worker.go.emit()                      # queued into the worker's event loop
    deadline = time.monotonic() + 0.5     # let that loop deliver the call
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    thread.quit()
    assert thread.wait(5000), "the worker thread did not stop"
'''


def _inner_env():
    """The environment for the inner run: offscreen plus an ABSOLUTE PYTHONPATH.

    The outer run's own PYTHONPATH entries are resolved against the outer cwd (a
    relative `../KiCadStamp-kq-site` works there); the inner run has a different
    cwd, so a relative entry would silently stop resolving and take the plugin
    with it. Every entry is made absolute against THIS process's cwd — the same
    resolution the outer run performed. When there is no PYTHONPATH at all (after
    the merge, pytest-qt lives in the venv) the key is simply not set.
    """
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    entries = [os.path.abspath(entry) for entry
               in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    if entries:
        env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


class _Row(NamedTuple):
    """One cell of the table: where the slot raises × the plugin, and what the
    inner run must therefore look like."""

    case_id: str
    inner_test: str
    source: str
    extra_args: tuple
    caught_by_the_plugin: bool


_ROWS = [
    _Row("ui-thread", "test_exception_inside_a_qt_slot", _UI_THREAD_SLOT, (), True),
    _Row("qthread", "test_exception_inside_a_slot_on_a_worker_thread",
         _QTHREAD_SLOT, (), True),
    # The control: the plugin is explicitly disabled although it is installed and
    # importable, so this row measures the hook — not whether the plugin is there.
    _Row("ui-thread-control-no-plugin", "test_exception_inside_a_qt_slot",
         _UI_THREAD_SLOT, ("-p", "no:pytest-qt"), False),
]


@pytest.mark.parametrize("row", _ROWS, ids=[row.case_id for row in _ROWS])
def test_an_exception_in_a_qt_slot_is_a_named_failure_not_a_dump(tmp_path, row):
    """С1 (plan_2026_09_24_kq_pytest_qt) — with the plugin loaded the exception is
    caught and reported as a FAILED naming the inner test; with `-p no:pytest-qt`
    the same slot dumps the process.

    Mutation check: pass `-p no:pytest-qt` to a row that expects the catch and it
    goes red on `Fatal Python error`; pass no `-p` flag to the control row and it
    goes red on the missing dump. Both directions are pinned, which is what makes
    the pair a measurement rather than a story."""
    inner_dir = tmp_path / row.case_id
    inner_dir.mkdir()
    # Own config on purpose: the repo's pytest.ini carries testpaths,
    # norecursedirs and required_plugins, none of which belongs in an inner run.
    # PYTHONPATH is inherited (that is how pytest-qt is reachable before the
    # plugin is merged into the venv — plan §4).
    (inner_dir / "pytest.ini").write_text("[pytest]\nqt_api = pyqt6\n",
                                          encoding="utf-8")
    inner_test = inner_dir / "test_the_slot_raises.py"
    inner_test.write_text(row.source, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", inner_test.name, "-q", "--no-header",
         "-p", "no:cacheprovider", *row.extra_args],
        cwd=inner_dir, env=_inner_env(),
        capture_output=True, text=True, timeout=120)

    output = proc.stdout + proc.stderr
    inner_id = f"{inner_test.name}::{row.inner_test}"

    if row.caught_by_the_plugin:
        assert proc.returncode == 1, (
            f"the inner run must FAIL, not crash or pass — got "
            f"returncode={proc.returncode}\n{output}")
        assert f"FAILED {inner_id}" in output, (
            f"the refusal must name the inner test that raised — {inner_id} is "
            f"not among the FAILED lines\n{output}")
        assert "exceptions caught in qt event loop" in output.lower(), (
            f"pytest-qt's own wording must be there — it is what proves the "
            f"exception was seen by the plugin, not by a plain pytest failure"
            f"\n{output}")
        assert "BOOM" in output, (
            f"the TEXT of the exception must travel to the report, or a reader "
            f"cannot tell WHICH slot raised\n{output}")
        assert "Fatal Python error" not in output, (
            f"a caught slot exception must not dump the process — the plugin was "
            f"loaded, so this is the property itself failing\n{output}")
    else:
        assert proc.returncode != 0, (
            f"an uncaught slot exception cannot end in a clean run — got "
            f"returncode={proc.returncode}\n{output}")
        assert proc.returncode < 0 or proc.returncode == 134, (
            f"the control must die abnormally (SIGABRT: negative returncode on "
            f"POSIX, 134 through a shell) — got {proc.returncode}\n{output}")
        assert "Fatal Python error" in output, (
            f"without pytest-qt the message must be the interpreter's own dump — "
            f"that is the symptom this whole plan exists to remove\n{output}")
        assert "FAILED" not in proc.stdout, (
            f"the control must NOT look like a test failure: that is exactly the "
            f"confusion (dump read as a healthy run) the harness had\n{output}")
