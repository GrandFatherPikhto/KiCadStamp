# tests/conftest.py
"""
Forces English gettext output for the whole test suite, regardless of the
calling shell's locale — this file calls setup_i18n() itself, once, before
any test module imports kicadstamp (see below; kicadstamp/__init__.py no
longer does this at import — P1-1, 2026-08-25), reading these same env vars
(see kicadstamp/i18n.py's detect_language() precedence: LANGUAGE > LC_ALL >
LC_MESSAGES > LANG). Most modules bind `_` at import time (`from
kicadstamp.i18n import _`), so whichever language wins at that ONE call is
what every test importing kicadstamp afterwards is stuck with — on a
machine/shell with LANG=ru_RU.UTF-8 (common on this project's dev
machines), that meant tests asserting a hardcoded English substring
against format_fatal_error()'s output (or anything built from it, e.g. the
dry-run report) failed even though nothing was actually broken.

Set at module level, not inside a fixture, so it runs during conftest
collection — before any test module (and therefore before `import
kicadstamp` anywhere) is imported. Same pattern tests/gui/conftest.py
already uses for QT_QPA_PLATFORM=offscreen.

tests/test_i18n.py is unaffected: it monkeypatches these exact vars and
calls setup_i18n() again explicitly inside each test to exercise ru/en/
other locales on demand — this module-level default only decides what the
FIRST, implicit import sees.
"""
import logging
import logging.handlers
import os
from pathlib import Path

import pytest

os.environ.pop("LANGUAGE", None)
os.environ["LC_ALL"] = "en_US.UTF-8"
os.environ["LANG"] = "en_US.UTF-8"

# See module docstring above for why this call lives here.
from kicadstamp.i18n import setup_i18n

setup_i18n()


@pytest.fixture(autouse=True)
def _reset_logging_after_test():
    """The queue-based logging rework (2026-08-15, see
    techdocs/handoff/plan_2026_08_15_queue_based_logging.md) made
    setup_logging() replace the ROOT logger's handlers with a QueueHandler
    and start a daemon QueueListener thread kept in a module-global. Tests
    that run a CLI/author entry point (kicadstamp_cli.main(),
    author_cli.cli_main()) therefore leak that thread and leave
    _log_listener set — which would silently switch LogDock's handler onto
    the listener path in later GUI tests (they expect get_log_listener() ==
    None and direct root attachment, see tests/gui/test_log_panel.py).
    Teardown here stops any leaked listener and resets the root logger so no
    test can contaminate another."""
    yield
    import kicadstamp.logging_setup as logging_setup

    listener = logging_setup._log_listener
    if listener is not None and listener._thread is not None:
        # _thread is None once stop() has already been called (a test that
        # stopped its own listener) — QueueListener.stop() is NOT idempotent.
        listener.stop()
    logging_setup._log_listener = None

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.handlers.QueueHandler):
            root.removeHandler(handler)
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def _reset_process_singletons():
    """One reset for every process-wide mutable singleton a test can leave behind.

    Plan: techdocs/handoff/deepseek/plan/plan_2026_09_27_repo_and_tests_transformation.md
    §5.1; order-dependence measured in plan/f0_report_2026_09_28_tests.md. Before
    this, isolation was copied per file (tests/gui/conftest.py had its own
    WORKING_SET teardown, tests/test_board_door_guard.py its own _refused_sites
    reset) and a test that FAILED before restoring leaked into the next FILE.

    Deliberately import-safe: each module is reset only when it is ALREADY in
    sys.modules, so this fixture never forces an import and cannot turn a
    lazy-import test (test_cli_lazy_imports, the seam's "does not pull kipy"
    cells) into a tautology or a break.

    Product code is NOT touched: the singletons that have no product-level reset
    (format_version._probe_cache, sexp_format._HINTS_CACHE, file_cache's caches)
    are cleared here, in the rig, and are named as a separate task in the F0
    report (invariant 5 of the plan).
    """
    import sys

    def _reset() -> None:
        ws_mod = sys.modules.get("kicadstamp.config_working_set")
        if ws_mod is not None:
            ws = ws_mod.WORKING_SET
            for fn in list(ws._listeners):
                ws.remove_listener(fn)
            ws.clear()
            ws.enabled = False

        fc = sys.modules.get("kicadstamp.utils.file_cache")
        if fc is not None:
            fc._cache.clear()
            fc._keys_by_path.clear()
            fc._graph_cache.clear()
            fc._graph_keys_by_path.clear()

        fv = sys.modules.get("kicadstamp.config.format_version")
        if fv is not None:
            fv._probe_cache.clear()

        sf = sys.modules.get("kicadstamp.config.sexp_format")
        if sf is not None:
            sf._HINTS_CACHE.clear()

        # gui/connection.py: the door's arming and its once-per-site Log dedup.
        # A test that arms the predicate and refuses once would otherwise silence
        # the NEXT test's expected ERROR line (test_board_door_guard counts it).
        conn = sys.modules.get("gui.connection")
        if conn is not None:
            conn.board_read_probe = None
            conn.ui_thread_predicate = None
            conn.ui_thread_read_refusal = conn.UI_READ_LOG
            conn._refused_sites.clear()
            conn._ui_read_sign.depth = 0

    _reset()
    yield
    _reset()


# ── Ф1.6 of plan_2026_09_27_repo_and_tests_transformation: the three markers ──
# Ф2.0: the marker hook decides by PATH, so the directories it compares against must
# not be derived from this file's own depth either — tests/paths.py fixes the depth
# in ONE place, which is what makes the Ф2 moves below safe.
from tests.paths import TESTS_ROOT

_TESTS_ROOT = TESTS_ROOT
_GUI_DIR = _TESTS_ROOT / "gui"
_INTEGRATION_DIR = _TESTS_ROOT / "integration_tests"


def pytest_addoption(parser):
    """`--reverse-order` — the order-independence run, one command for both
    platforms (Ф3.1 of plan_2026_09_27_repo_and_tests_transformation).

    docs/tests.md's reverse run used to be a shell pipeline
    (`find tests -name 'test_*.py' … | sort -r`), which is bash-only: on Windows
    there was no equivalent. Reversing the order of the COLLECTED cells here makes
    the same command work everywhere. Within one file the author's order is kept.

    `--coarse-mtime` — the Windows-tick run (Ф3.7 of the same plan): the whole
    run under the frozen-stamp probe, so the class of failure that only Windows
    used to produce is caught on Linux deterministically. See the freeze below.
    """
    parser.addoption(
        "--reverse-order", action="store_true", default=False,
        help="collect the cells in reverse FILE order (the order-independence "
             "check of docs/tests.md)")
    parser.addoption(
        "--coarse-mtime", action="store_true", default=False,
        help="force the Windows one-tick filesystem for the WHOLE run: after "
             "every Path.write_text/write_bytes onto an EXISTING file, that "
             "file's previous (atime_ns, mtime_ns) is restored")


# ── Ф3.7: `--coarse-mtime` — the Windows tick, forced on any filesystem ──────
#
# WHAT IT SIMULATES. A reader cached by `(path, mtime_ns)` cannot see a second
# write that lands on the SAME stamp. On Windows that is the rule, not the
# exception — measured 01.10.2026 (Ф3.6): `st_mtime_ns` granularity ~0.5 ms, and
# 344/500 (C:) / 409/500 (repo disk D:) back-to-back write pairs landed on one
# tick. On Linux it takes a forced one-tick filesystem to show up, and doing that
# is how Ф3.7 found the class: ELEVEN cells failed under this probe on `f3d603a`
# that never fail naturally there, the Actions #656 failure among them. The
# product under the same probe was clean (its writer invalidates its caches).
#
# WHAT IT IS FOR. Two jobs, both only possible with the freeze ON by choice:
#   * the CI Linux leg runs the suite a SECOND time with this option, so the
#     Windows class is caught on every push instead of waiting for the Windows
#     leg (and a future product writer that forgets its cache invalidation is
#     caught on Linux too);
#   * it is the mutoscope for `tests/fakes/write_later.py`: with the freeze on, a
#     rig that writes a file twice must leave a LATER write behind itself, or its
#     own cell goes red. That is why the option must NOT be on by default — with
#     it always on, the rigs would be measured in the frozen world only, and the
#     reference run would stop measuring the natural filesystem.
#
# DECLARED LIMITATION — the same one the probe had, and the same one its 11-cell
# inventory was found under: only `Path.write_text` and `Path.write_bytes` are
# patched. A write through `open()` is invisible to this option, so the class it
# covers is exactly the class it was measured with.
_coarse_mtime_installed = False


def _freeze_mtime_after_write(original):
    """Wrap ONE writer: the write really happens, but a file that ALREADY
    existed keeps the `(atime_ns, mtime_ns)` it had before it.

    No clock is faked and no content is withheld — only the stamp is held back,
    which is precisely what a coarse filesystem does to two writes inside one
    tick. atime is restored next to mtime so the file's metadata carries the
    PREVIOUS write's stamps and nothing of the fake's own."""
    def wrapper(self, *args, **kwargs):
        try:
            stat = os.stat(self)
            previous = (stat.st_atime_ns, stat.st_mtime_ns)
        except OSError:
            # A path that does not exist yet (or unreadable metadata) has no
            # previous stamp to hold back: it is a plain write.
            previous = None
        result = original(self, *args, **kwargs)
        if previous is not None:
            os.utime(self, ns=previous)
        return result
    return wrapper


def _install_coarse_mtime() -> None:
    """Patch the two writers. Idempotent: `pytest_configure` runs once per run,
    but conftest modules can be imported more than once by pytest's own
    conftest handling, and stacking two wrappers would restore the stamp twice
    (harmless) while making the intent harder to read."""
    global _coarse_mtime_installed
    if _coarse_mtime_installed:
        return
    Path.write_text = _freeze_mtime_after_write(Path.write_text)
    Path.write_bytes = _freeze_mtime_after_write(Path.write_bytes)
    _coarse_mtime_installed = True


def pytest_configure(config):
    """Install the freeze for the WHOLE run when `--coarse-mtime` is given.

    A conftest hook rather than a separate plugin module on purpose: the option
    is a property of THIS suite's rigs, and it is the same file the rigs' other
    knobs live in. `tests/repo/test_coarse_mtime_option.py` measures the result
    in a subprocess that loads this very conftest (`-p tests.conftest`), so the
    option is exercised as it ships, not as a copy."""
    if config.getoption("coarse_mtime"):
        _install_coarse_mtime()


def pytest_collection_modifyitems(config, items):
    """Attach exactly ONE of `gui` / `integration` / `unit` to every collected item.

    pytest has no "default marker", so `unit` — everything that is neither of the
    other two — cannot be declared in pytest.ini; it can only be derived. And it is
    derived from the PATH rather than from hand-written decorators, which is what
    makes `-m integration` mean the same thing as the directory it lives in:

      * `-m "not integration"` used to trust the nine files that carry the
        decorator by hand. A test inside tests/integration_tests/ that nobody had
        decorated would be SELECTED by that expression and then fail on a missing
        KiCad — which is exactly why CI also passes --ignore=tests/integration_tests.
        With the marker attached here the two agree, and `-m gui` / `-m unit` split
        the rest the way the directories do.
      * the decorator itself is left alone: it still marks those nine files, and the
        hook above does not stack a second copy of the same marker on top of it.

    Rule 38's spirit: if this ever marked nothing, `-m gui` would collect zero tests
    and a command would look green while covering nothing. The three counts measured
    when the hook landed are in
    techdocs/handoff/deepseek/handoff/step_2026_09_28_F1_6_markers.md.
    """
    for item in items:
        path = Path(str(item.path)).resolve()
        if path.is_relative_to(_INTEGRATION_DIR):
            # The nine files in tests/integration_tests/ also carry the decorator by
            # hand, and adding a second copy of the SAME marker on top is not free:
            # `item.keywords` collapses the two into one entry, but
            # `item.iter_markers()` yields both, so a consumer counting markers sees
            # "integration" twice (tests/test_marker_contract.py found exactly that —
            # 23 items reported as carrying ['integration', 'integration']).
            if item.get_closest_marker("integration") is None:
                item.add_marker(pytest.mark.integration)
        elif path.is_relative_to(_GUI_DIR):
            item.add_marker(pytest.mark.gui)
        else:
            item.add_marker(pytest.mark.unit)

    if config.getoption("reverse_order"):
        # Reverse FILE order, keeping each file's own cell order (a stable sort):
        # the shape the old `find … | sort -r` produced, without the shell.
        items.sort(key=lambda item: item.path.as_posix(), reverse=True)


# ── Ф1.11: `caplog.records` is EVERYTHING, so scope it to the logger under test ──


@pytest.fixture
def records_from(caplog):
    """`caplog.records`, narrowed to ONE logger — the Ф1.11 decoupling.

    caplog attaches its handler at the ROOT logger, so `caplog.records` holds every
    record logged during the test, from ANY module and ANY thread. Judging by LEVEL
    over that whole list — `len(warnings) == 2`, `... == []`, `not any(r.levelno >=
    WARNING)`, `records[-1].levelname` — makes a cell depend on what the rest of the
    process happened to log. In a reverse-order run two cells failed on

        "pynng Socket.close() did not return within 2.0s — abandoning it on an
         orphaned thread"

    (kicadstamp/kicad/pynng_safety.py, logged from a BACKGROUND thread by a socket an
    EARLIER test had left behind). Nothing about those two cells' subject was wrong:
    they were counting a neighbour's warning.

    Usage — with the same name the cell already gives `caplog.at_level(logger=...)`:

        [r for r in records_from("kicadstamp.kicad.adapter") if r.levelno >= ...]
    """
    def _records(logger_name):
        return [r for r in caplog.records
                if r.name == logger_name or r.name.startswith(logger_name + ".")]
    return _records
