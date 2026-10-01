# tests/gui/test_qapp_keepalive.py
"""Ф3.9 of plan_2026_09_27_repo_and_tests_transformation: the session
QApplication must outlive the `qapp` fixture's generator frame.

`tests/gui/conftest.py` shadows pytest-qt's `qapp` fixture, so pytest-qt's own
keepalive (a module global in `pytestqt/plugin.py`, added for exactly this) never
runs. Without one of our own the only reference is the local `app` inside the
session-scoped generator, which dies when the generator is closed at session
teardown — and destroying a QApplication makes sip walk every live wrapper, where
one stale pointer is a SIGSEGV. Measured 01.10.2026 on the 11 cells of Ф3.7 in ONE
process: 13/20 runs died with EXIT=139 (`_pytest/fixtures.py::finish` of the
session teardown, AFTER every cell had passed), 0/20 with an extra reference held
by a plugin that changed nothing else.

Two cells, because they hold two different halves (rule 35):
  * the reference EXISTS and is the application the fixture yields;
  * the application is still ALIVE when the session is over — the state the
    crash came from. The first cell alone cannot see that half: a teardown that
    cleared the global would still pass it.
"""
import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from tests.paths import REPO_ROOT


def test_the_qapp_fixture_holds_the_application_in_a_module_reference(
        qapp, qapp_keepalive_holder):
    """The reference half. `is` twice: the module reference IS the application
    the fixture handed out, and that application is what Qt reports for the
    process — the fixture must not have wrapped or copied anything.

    The module object comes from the `qapp_keepalive_holder` fixture, NOT from a
    lookup by name or by file path: `tests/gui/conftest.py` is loaded more than
    once here, and the copy pytest registered is not reachable through
    sys.modules at all (measured 01.10.2026 under `--reverse-order`:
    sys.modules["conftest"] held tests/integration_tests/conftest.py, and the only
    module with this file's path was the non-plugin `tests.gui.conftest` copy made
    by `from tests.gui.conftest import _pump`, whose fixture never runs). A
    lookup-based guard was red on healthy code exactly that way."""
    assert getattr(qapp_keepalive_holder, "_qapp_keepalive", None) is qapp, (
        "the qapp fixture left no module-level reference in its own conftest "
        "module: at session teardown the generator's local dies with it, the "
        "QApplication wrapper is destroyed while other wrappers are alive, and "
        "sip walks a stale one (SIGSEGV; 13/20 of the Ф3.7 subset measured "
        "before the fix)")
    assert QApplication.instance() is qapp, (
        "the application the qapp fixture yields is not the one Qt reports "
        "for this process")


# The witness of the second cell, written as a plugin of the INNER run. Session
# fixtures are finalized BEFORE `pytest_sessionfinish`, so by the time this hook
# runs, a `qapp` fixture that was the only owner has already let the application
# die. The verdict goes to a file: pytest's capture owns stdout at this point.
_INNER_WITNESS = '''
import os
from pathlib import Path


def pytest_sessionfinish(session, exitstatus):
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    alive = app is not None and not sip.isdeleted(app)
    Path(os.environ["QAPP_WITNESS_FILE"]).write_text(
        "alive" if alive else "dead", encoding="utf-8")
'''


def test_the_session_application_exists(qapp):
    """The TARGET of the inner run below: it must merely USE the fixture.

    It is a cell of its own, and not one of the two checks, because a FAILED
    cell keeps its traceback alive until the process exits — and that traceback
    holds the test frame, which holds the fixture value. The witness below would
    then report "alive" whatever the conftest does; measured 01.10.2026 with the
    keepalive removed, the witness was blind exactly this way."""
    assert qapp is not None


def test_the_application_is_still_alive_when_the_session_is_over(tmp_path):
    """The liveness half, in a real run.

    The inner run is a cell of THIS file (relaunched by node id), so the
    environment is the real one: tests/gui/conftest.py, shadowing pytest-qt's
    `qapp` exactly as a normal run does — a plugin passed with `-p` would LOSE
    that shadowing, because entry-point plugins are registered after `-p` ones.
    (The same reason the fixture must be fixed in the conftest, not around it.)

    A missing verdict file is a failure too: the inner run may die by SIGSEGV
    instead of reporting, and that is still "the application did not survive"."""
    witness = tmp_path / "qapp_witness.py"
    witness.write_text(_INNER_WITNESS, encoding="utf-8")
    verdict_file = tmp_path / "verdict.txt"
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(tmp_path)]),
               PYTHONDONTWRITEBYTECODE="1",
               QAPP_WITNESS_FILE=str(verdict_file))
    target = (f"{Path(__file__).resolve()}"
              f"::test_the_session_application_exists")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-header",
         "-p", "no:cacheprovider", "-p", "qapp_witness"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, env=env)

    verdict = (verdict_file.read_text(encoding="utf-8")
               if verdict_file.exists() else "<no verdict: the run did not reach "
                                             "the end of the session>")
    assert verdict == "alive", (
        f"the session was over and the application was '{verdict}' (inner run "
        f"exited {result.returncode}):\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
