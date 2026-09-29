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
_TESTS_ROOT = Path(__file__).resolve().parent
_GUI_DIR = _TESTS_ROOT / "gui"
_INTEGRATION_DIR = _TESTS_ROOT / "integration_tests"


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
