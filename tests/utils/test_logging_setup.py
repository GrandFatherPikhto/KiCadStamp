# tests/test_logging_setup.py
"""Tests for kicadstamp/logging_setup.py's queue-based logging pipeline
(QueueHandler/QueueListener, 2026-08-15 — see
techdocs/handoff/plan_2026_08_15_queue_based_logging.md).

The rework moved all real formatting/writing (format()/formatTime()/handler
locks) onto ONE dedicated listener thread, so a logging call can never block
the calling thread on a handler lock held by a peer stuck in emit(). These
tests pin down the contract setup_logging() now exposes: it returns a started
QueueListener, respects each handler's own level, and stops the previous
listener on re-invocation.

Isolation: setup_logging() mutates the ROOT logger (replaces its handlers,
sets DEBUG) and starts a daemon listener thread. tests/conftest.py's autouse
_reset_logging_after_test fixture stops any leaked listener and restores the
root logger after every test, so no state here can leak into the rest of the
session (including the GUI tests, which expect get_log_listener() == None).
"""
import logging
import logging.handlers
import queue

from kicadstamp.logging_setup import get_log_listener, setup_logging


class _RecordingHandler(logging.Handler):
    """Collects emitted LogRecords in memory instead of writing anywhere."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _close_file_handlers(listener) -> None:
    """setup_logging() builds one FileHandler per log_file; listener.stop()
    does NOT close handlers, and on Windows an open handle blocks tmp_path
    cleanup — close any file handlers explicitly."""
    for handler in getattr(listener, "handlers", ()):
        if isinstance(handler, logging.FileHandler):
            handler.close()


def test_setup_logging_returns_started_listener_that_delivers_records(tmp_path):
    """The returned QueueListener is actually started: a record logged through
    the normal logging stack is queued, formatted and written by the listener
    thread to the target file — flush by stopping the listener, then read."""
    log_file = tmp_path / "delivery.log"
    listener = setup_logging(log_file=str(log_file))
    try:
        logging.getLogger("kicadstamp.test_logging_setup.delivery").info(
            "hello through the queue")
    finally:
        listener.stop()  # drains the queue and joins the thread
        _close_file_handlers(listener)
    assert "hello through the queue" in log_file.read_text(encoding="utf-8")


def test_setup_logging_respects_each_handler_level():
    """respect_handler_level=True (set by setup_logging) keeps a handler's own
    .setLevel() meaningful: an INFO record must NOT reach a WARNING-level
    handler even though the root logger/queue pass everything through DEBUG."""
    listener = setup_logging(verbose=False)  # console handler at INFO
    recorder = _RecordingHandler()
    recorder.setLevel(logging.WARNING)
    listener.handlers = listener.handlers + (recorder,)
    try:
        logging.getLogger("kicadstamp.test_logging_setup.respect").info(
            "info below warning")
    finally:
        listener.stop()
    assert recorder.records == []


def test_queue_listener_without_respect_flag_passes_every_record():
    """The flip side of the previous test: the stdlib default
    (respect_handler_level=False) hands EVERY record to EVERY handler,
    because the level check Logger.callHandlers() normally does is skipped in
    the queue path — the exact regression setup_logging() must guard against,
    and the reason the flag is mandatory."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    q = queue.Queue()
    recorder = _RecordingHandler()
    recorder.setLevel(logging.WARNING)
    listener = logging.handlers.QueueListener(q, recorder)  # default flag = False
    listener.start()
    try:
        queue_handler = logging.handlers.QueueHandler(q)
        root.handlers = [queue_handler]
        root.setLevel(logging.DEBUG)
        logging.getLogger("kicadstamp.test_logging_setup.no_respect").info(
            "info reaches a warning handler")
    finally:
        listener.stop()
        root.handlers = original_handlers
        root.setLevel(original_level)
    assert len(recorder.records) == 1


def test_repeated_setup_logging_stops_previous_listener(tmp_path):
    """A second setup_logging() in the same process must stop the previous
    listener's thread (no zombie) — happens in tests/reused sessions."""
    first = setup_logging(log_file=str(tmp_path / "first.log"))
    first_thread = first._thread
    assert first_thread is not None
    assert first_thread.is_alive()

    second = setup_logging(log_file=str(tmp_path / "second.log"))
    try:
        assert not first_thread.is_alive()  # stopped by the second call
        assert second._thread is not None
        assert second._thread.is_alive()
    finally:
        second.stop()
        _close_file_handlers(first)
        _close_file_handlers(second)


def test_get_log_listener_is_none_before_any_setup_logging():
    """The public accessor returns None until setup_logging() has run in this
    process — the contract LogDock relies on to pick the direct root path in
    tests. Guaranteed by tests/conftest.py's per-test reset, so this holds
    regardless of test order within this module."""
    assert get_log_listener() is None
