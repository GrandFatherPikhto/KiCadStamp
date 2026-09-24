# gui/slot_exception_hook.py
"""An exception in a Qt-armed Python frame must not take the user's session down.

WHY THIS EXISTS. PyQt6 answers an exception that escapes a slot with `qFatal()`
for as long as `sys.excepthook` is the interpreter's default one: the process
dies with SIGABRT, the user loses the session, and after the fact there is
nothing to read. Measured 2026-09-24 (`diagnostics/probe_slot_excepthook.py`,
eight runs, one process each) — the hook below saves every shape in which Python
keeps the exception at all:

    slot on the UI thread under processEvents()            134 -> 0
    slot on a QThread                                      134 -> 0
    raise inside an eventFilter, i.e. inside Qt's own
        C++ dispatch (the shape gui/ui_utils.py:277 and
        gui/docks/_common.py:472 write their guards for)   134 -> 0
    a NON-Qt thread                                        0   -> 0   (the hook is
        not involved: threading.excepthook reports it, nothing needs saving)

AND WHAT IT DOES NOT SAVE — stated here so nobody promises more: Qt's OWN
`qFatal`, e.g. "QThread: Destroyed while thread is still running", stays 134 with
this hook installed. There is no Python exception in that path, so there is
nothing for a Python hook to catch. This module is not "the GUI never crashes";
it is "a Python failure no longer ends the session".

WHAT IT DOES. Logs CRITICAL with the traceback through the standard logging
pipeline — which the GUI already marshals to the UI thread (gui/docks/log_panel.py:
`_QtLogHandler.emit` emits a signal instead of touching the widget, so writing a
record is safe from ANY thread) — and writes one report file per failing SITE
into `<repo>/diagnostics/`, where this project's other diagnostics reports already
live (kicadstamp/diagnostics/board_read_probe.py:75 — "gitignored, and where the
report tool looks"; the GUI has no log file of its own, `setup_logging()` runs
without `log_file`). It NEVER touches a widget: after a failed slot the widget
state is unpredictable, and the hook may be running on a worker thread.

DEDUPLICATION, and why it is not cosmetics. A failing eventFilter fires on EVERY
event delivered to that widget: measured, ONE broken filter produced 17 hook calls
in 0.5 s of event pumping. Every Log line is appended to a QPlainTextEdit on the
UI thread, so an undeduplicated hook would trade one crash for a frozen Log. The
policy: ONE full entry per SITE per session (report file written once, with the
traceback of the first case), and the SAME site speaks again only when its count
crosses the next power of ten — bounded lines (about log10 of the repeats) that
keep the count visible while the storm is happening instead of hiding it.

`SystemExit` and `KeyboardInterrupt` are NOT swallowed: they are handed to the
previous hook, so a future `sys.exit()` from inside a slot keeps behaving exactly
as it does today. Checked 2026-09-24: nothing in gui/ calls sys.exit() inside a
slot — quitting goes through QApplication.quit(), which stops the event loop
instead of raising — so that cell exists for the next writer, not for a path that
exists now.

Installed by kicadstamp/gui_main.py — ONE line, right after QApplication is built
and BEFORE MainWindow, so the window's constructor is covered too — and switchable
off for a diagnostic run with `KICADSTAMP_SLOT_EXCEPTION_HOOK=0`. Never installed
at import time: tests import `gui.main_window` and friends, and a hook installed
then would hide a core dump from the Кq watchdog
(tests/gui/test_qt_slot_exception_capture.py), which measures exactly that dump.
"""
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from kicadstamp import __version__
from kicadstamp.i18n import _

LOGGER_NAME = "kicadstamp.slot_exception"
ENV_SWITCH = "KICADSTAMP_SLOT_EXCEPTION_HOOK"

# The counts at which a site that keeps failing says so again. Powers of ten, so
# the number of extra lines for a storm is log10(N) and not N.
_REPEAT_STEPS = (10, 100, 1000, 10_000, 100_000, 1_000_000)

_OFF_VALUES = ("0", "false", "no", "off")

_logger = logging.getLogger(LOGGER_NAME)

_lock = threading.Lock()
_installed = False
_previous_hook = None
_counts: dict[str, int] = {}
_report_dir_override: Path | None = None


def report_dir() -> Path:
    """Where the report files go: `<repo root>/diagnostics` — gitignored, and
    where this project already keeps its diagnostics reports
    (kicadstamp/diagnostics/board_read_probe.py:75). The GUI has no log file of
    its own (`setup_logging()` runs without `log_file`), so "next to the logs"
    means this directory.

    `install_slot_exception_hook(report_dir=...)` overrides it — the watchdog uses
    that to keep its own reports in tmp_path instead of the developer's
    diagnostics directory, and a diagnostic run can point it somewhere else."""
    return _report_dir_override or (
        Path(__file__).resolve().parent.parent / "diagnostics")


def failing_site(tb) -> str:
    """`file:line` of the frame that RAISED — the deepest one, and the identity
    the deduplication counts by. Same shape as the board door's refusal, and for
    the same reason: an address the reader can open."""
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return "<unknown>"
    return f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"


def _write_report(exc_type, exc_value, tb, site: str) -> Path | None:
    """The report file for a site's FIRST failure. Never raises: a report that
    cannot be written must not become the second failure."""
    try:
        directory = report_dir()
        directory.mkdir(parents=True, exist_ok=True)
        # MICROseconds, and a collision loop on top of them. A second-resolution
        # name cost a report the first time this ran: two DIFFERENT sites failing
        # in the same second wrote to one path and the second overwrote the first
        # — the report loss happened exactly when failures were piling up, which
        # is the one moment the reports matter.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"slot_exception_{stamp}.txt"
        attempt = 1
        while path.exists():
            attempt += 1
            path = directory / f"slot_exception_{stamp}_{attempt}.txt"
        path.write_text("\n".join([
            "# KiCadStamp — an exception was caught so the session could go on",
            "# written by gui/slot_exception_hook.py"
            " (plan_2026_09_24_slot_exception_hook)",
            f"version: {__version__}",
            f"timestamp: {datetime.now(timezone.utc).isoformat()}",
            f"thread: {threading.current_thread().name}",
            f"site: {site}",
            f"exception: {exc_type.__name__}: {exc_value}",
            "occurrence: 1 (the first for this site; later ones are counted in"
            " the Log, and this file is not rewritten)",
            "",
            "traceback:",
            "".join(traceback.format_exception(exc_type, exc_value, tb)).rstrip(),
            "",
            "NOTE: the action that raised was NOT completed. The session survived,",
            "but whatever that action was half-way through is still half-way —",
            "check the Log and the board before continuing.",
            "",
        ]), encoding="utf-8")
        return path
    except Exception:                       # noqa: BLE001 — the hook never raises
        return None


def _goes_to_the_previous_hook(exc_type) -> bool:
    """True for the two exceptions the interpreter itself acts on. Swallowing
    them would turn the hook into "quitting the app silently does nothing"."""
    return issubclass(exc_type, (SystemExit, KeyboardInterrupt))


def _fallback(exc_type, exc_value, tb) -> None:
    """Last resort INSIDE the hook: write to fd 2 with os.write, which cannot be
    captured by pytest, closed by a Qt bridge or replaced by a logging handler."""
    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, tb))
        os.write(2, ("KiCadStamp: a Qt action failed, and the hook itself failed "
                     "while reporting it:\n" + text).encode("utf-8", "replace"))
    except Exception:                       # noqa: BLE001 — nothing left to try
        pass


def _handle(exc_type, exc_value, tb) -> None:
    """The `sys.excepthook` replacement. NEVER raises: a hook that fails while
    handling a failure hands the interpreter a second one, and by then the
    handler it would reach is this same broken one. Hence one outer try/except
    and a fallback that touches nothing that can fail."""
    try:
        if _goes_to_the_previous_hook(exc_type):
            previous = _previous_hook or sys.__excepthook__
            previous(exc_type, exc_value, tb)
            return
        site = failing_site(tb)
        with _lock:
            repeats = _counts.get(site, 0) + 1
            _counts[site] = repeats
        if repeats == 1:
            path = _write_report(exc_type, exc_value, tb, site)
            if path is not None:
                report_note = _("A report was written to {path}.").format(
                    path=path)
            else:
                # The directory may be read-only or full. The Log line and the
                # console still carry the traceback, so this says that instead of
                # naming a file that is not there.
                report_note = _("The report file could not be written — the "
                                "traceback below is the only copy.")
            _logger.critical(
                _("A Qt action failed and was caught — the session continues, "
                  "but that action was NOT completed: {kind}: {error} (at {site})."
                  " {report}").format(
                      kind=exc_type.__name__, error=exc_value, site=site,
                      report=report_note),
                exc_info=(exc_type, exc_value, tb))
        elif repeats in _REPEAT_STEPS:
            _logger.error(
                _("The same Qt failure at {site} has now happened {count} times — "
                  "it keeps failing and whatever it does keeps not completing.")
                .format(site=site, count=repeats))
    except Exception as hook_error:         # noqa: BLE001 — see the docstring
        _fallback(type(hook_error), hook_error, hook_error.__traceback__)


def install_slot_exception_hook(report_dir=None) -> bool:
    """Install the hook. Returns True only when THIS call installed it — False
    when it was already installed, or when `KICADSTAMP_SLOT_EXCEPTION_HOOK=0`
    switches it off (a diagnostic run that wants the old, loud behaviour back).

    `report_dir` is where the report files go; None means the repository's
    `diagnostics/` directory (see `report_dir()`). The production call site in
    kicadstamp/gui_main.py passes nothing.

    Idempotent on purpose: a second install must not wrap our own hook, or every
    traceback would grow a frame per call and the reported site would stop being
    the line the reader has to open."""
    global _installed, _previous_hook, _report_dir_override
    if os.environ.get(ENV_SWITCH, "1").strip().lower() in _OFF_VALUES:
        return False
    if _installed:
        return False
    _previous_hook = sys.excepthook
    _counts.clear()                         # a fresh session gets fresh counts
    _report_dir_override = Path(report_dir).resolve() if report_dir else None
    sys.excepthook = _handle
    _installed = True
    return True


def uninstall_slot_exception_hook() -> None:
    """Put the previous hook back. Safe when not installed, and it does not touch
    a hook somebody else installed after us."""
    global _installed, _previous_hook, _report_dir_override
    if _installed and sys.excepthook is _handle:
        sys.excepthook = _previous_hook or sys.__excepthook__
    _installed = False
    _previous_hook = None
    _report_dir_override = None


def is_installed() -> bool:
    """True when OUR handler is the one the interpreter would call."""
    return _installed and sys.excepthook is _handle


def failure_counts() -> dict:
    """site -> how many times it fired in this session (a copy)."""
    with _lock:
        return dict(_counts)
