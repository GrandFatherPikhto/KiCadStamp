# kicadstamp/logging_setup.py
"""
Logging setup for the KiCadStamp CLI.

Extracted from kicadstamp_cli.py so board scripts (via author.py) and
any other entry point can configure logging without importing the full CLI.

Since 2026-08-15 logging runs through a queue-based pipeline
(QueueHandler/QueueListener, see techdocs/handoff/plan_2026_08_15_queue_based_logging.md):
the ROOT logger carries only a cheap QueueHandler, and ALL real formatting /
writing (format()/formatTime()/handler locks) happens on ONE dedicated
listener thread. A calling thread that logs only does a queue.put() and
moves on — it can never block on a handler lock held by another thread that
got stuck inside emit() (the pynng close() hang that froze the whole GUI on
2026-08-15, confirmed by two independent py-spy dumps).

Trade-off: setup_logging() now RETURNS the started QueueListener and the
caller owns stopping it (GUI on QApplication.aboutToQuit, CLI in a finally)
— otherwise the daemon listener thread leaks and buffered records are lost
at process exit.
"""

import logging
import logging.handlers
import queue
import sys
from pathlib import Path


class _ColorFormatter(logging.Formatter):
    """Wraps ERROR/CRITICAL lines in red, WARNING in yellow — ANSI escape
    codes, only when the console stream is a real terminal (use_color), so
    redirected/piped output never gets raw escape bytes.  format_fatal_error()
    already marks each problem with '✗' — this makes the whole FATAL ERROR
    block visually impossible to miss instead of blending into a wall of
    INFO lines (found needed live: ambiguity errors from a board script were
    easy to scroll past in a long --apply log)."""
    _RED = "\033[91m"
    _YELLOW = "\033[93m"
    _RESET = "\033[0m"

    def __init__(self, fmt: str, use_color: bool):
        super().__init__(fmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._use_color:
            return message
        if record.levelno >= logging.ERROR:
            return f"{self._RED}{message}{self._RESET}"
        if record.levelno == logging.WARNING:
            return f"{self._YELLOW}{message}{self._RESET}"
        return message


# Module-level handle to the QueueListener started by the last
# setup_logging() call, so dynamic consumers (LogDock's _QtLogHandler) can
# attach/detach their handlers to the listener instead of to the root logger
# directly (which would bypass the queue and stay on the blocking path).
_log_listener: "logging.handlers.QueueListener | None" = None


def get_log_listener():
    """The QueueListener started by the last setup_logging() call, or None
    if setup_logging() hasn't run in this process (e.g. most unit tests) —
    callers (LogDock) must handle None by falling back to attaching
    directly to the root logger, same as before this queue-based rework."""
    return _log_listener


def setup_logging(verbose: bool = False,
                  log_file: str = None) -> "logging.handlers.QueueListener":
    """Configure logging: level and output to console and/or file.

    Returns the started QueueListener. The caller owns its lifecycle and
    MUST stop() it when done (GUI: QApplication.aboutToQuit; CLI: in a
    finally), so the listener thread doesn't leak and buffered records
    aren't lost at process exit.
    """
    level = logging.DEBUG if verbose else logging.INFO
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    use_color = hasattr(console.stream, "isatty") and console.stream.isatty()
    console.setFormatter(_ColorFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", use_color=use_color))

    handlers = [console]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    # Queue-based pipeline: the root keeps ONLY a QueueHandler; the real
    # formatting/writing runs on the listener's single thread. Any thread
    # that logs does a cheap queue.put() and moves on — it can never block
    # on a handler lock held by a peer stuck in emit() (the pynng close()-in-
    # GC-finalizer hang that froze the GUI, 2026-08-15).
    log_queue = queue.Queue(-1)  # unbounded: records are never dropped silently
    queue_handler = logging.handlers.QueueHandler(log_queue)

    root = logging.getLogger()
    # basicConfig() is a no-op once the root already has a handler, and a
    # repeated setup_logging() in one process (tests, reused sessions) must
    # end with ONLY the fresh QueueHandler on the root — manage the root
    # explicitly instead of relying on basicConfig's len(root.handlers)==0 gate.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)
    root.addHandler(queue_handler)

    global _log_listener
    if _log_listener is not None and _log_listener._thread is not None:
        # Re-setup in the same process: stop the previous listener so its
        # thread doesn't leak (its queue is already unreachable anyway).
        _log_listener.stop()

    # respect_handler_level=True is REQUIRED: the stdlib default (False)
    # hands every record to every handler regardless of the handler's own
    # .setLevel() (Logger.callHandlers' level check is skipped in the queue
    # path) — which would break the normal-vs-verbose console difference.
    listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True)
    listener.start()
    _log_listener = listener
    return listener
