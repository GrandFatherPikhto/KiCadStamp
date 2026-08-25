# tests/conftest.py
"""
Forces English gettext output for the whole test suite, regardless of the
calling shell's locale — kicadstamp/__init__.py calls setup_i18n() exactly
once, at first import of the kicadstamp package, reading these same env
vars (see kicadstamp/i18n.py's detect_language() precedence: LANGUAGE >
LC_ALL > LC_MESSAGES > LANG). Most modules bind `_` at import time (`from
kicadstamp.i18n import _`), so whichever language wins at that ONE import
is what every test importing kicadstamp afterwards is stuck with — on a
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

import pytest

os.environ.pop("LANGUAGE", None)
os.environ["LC_ALL"] = "en_US.UTF-8"
os.environ["LANG"] = "en_US.UTF-8"

# Explicit i18n init (P1-1, 2026-08-25): kicadstamp/__init__.py no longer
# calls setup_i18n() at import — entry points do. Tests import library
# modules directly, so set up the translation function here (English) before
# any test module imports kicadstamp.
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
