# tests/gui/test_log_panel.py
import logging
import threading

from tests.gui.conftest import _pump


def test_logging_from_a_background_thread_is_queued_not_touched_synchronously(qapp, log_dock):
    """Regression (found live 2026-08-04): a Windows access violation with a
    full Python traceback landing in append_line() — a logger call made from
    inside a gui/worker.py-backgrounded operation (PlacerDock's Redraw, via
    apply_pipeline.py) ran _QtLogHandler.emit() on THAT worker thread, which
    used to call append_line() (a QWidget touch) directly — undefined
    behavior off the UI thread in Qt. Logging is now routed through a
    pyqtSignal, which Qt automatically queues onto the receiver's (UI)
    thread whenever the emitting thread differs. Proven here two ways: the
    text is NOT yet present right after the background thread finishes
    logging (the old, buggy behavior would have appended it synchronously,
    right there), and only appears once the UI event loop gets a chance to
    process the queued signal."""
    test_logger = logging.getLogger("kicadstamp.gui_test.background_thread")

    def worker():
        test_logger.info("logged from a background thread")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert "logged from a background thread" not in log_dock.text.toPlainText()

    _pump(qapp, lambda: "logged from a background thread" in log_dock.text.toPlainText())

    assert "logged from a background thread" in log_dock.text.toPlainText()


def test_level_filtering_and_verbose_toggle(log_dock):
    test_logger = logging.getLogger("kicadstamp.gui_test.level_filtering")

    test_logger.debug("hidden debug line")
    test_logger.info("visible info line")
    test_logger.warning("visible warning line")

    text = log_dock.text.toPlainText()
    assert "hidden debug line" not in text
    assert "visible info line" in text
    assert "visible warning line" in text

    log_dock.verbose_checkbox.setChecked(True)
    test_logger.debug("now visible debug line")
    assert "now visible debug line" in log_dock.text.toPlainText()

    log_dock.verbose_checkbox.setChecked(False)
    test_logger.debug("hidden again")
    assert "hidden again" not in log_dock.text.toPlainText()


def test_clear_button_empties_the_panel(log_dock):
    test_logger = logging.getLogger("kicadstamp.gui_test.clear")
    test_logger.info("something")
    assert "something" in log_dock.text.toPlainText()

    log_dock.text.clear()
    assert log_dock.text.toPlainText() == ""


def test_multiline_error_message_preserves_line_breaks(log_dock):
    """Regression (found live 2026-08-06): appendHtml() used to collapse \\n
    in colored ERROR/WARNING messages (e.g. format_fatal_error()'s bulleted,
    multi-line dump) into one run-on line — HTML outside a whitespace-
    preserving container ignores \\n and repeated leading spaces. Fixed by
    wrapping in <div style="white-space:pre-wrap">, verified live against
    QTextDocument to preserve \\n identically to <pre> without its forced
    monospace font."""
    test_logger = logging.getLogger("kicadstamp.gui_test.multiline")
    test_logger.error("line one\n  bullet two")

    text = log_dock.text.toPlainText()
    assert "line one\n  bullet two" in text


def test_find_selects_matching_text(log_dock):
    log_dock.text.appendPlainText("a needle in a haystack")
    log_dock.find_edit.setText("needle")
    log_dock._find(backward=False)
    assert log_dock.text.textCursor().hasSelection()


def test_verbose_checkbox_seeded_from_constructor_flag(main_window):
    import logging as logging_module

    from gui.docks.log_panel import LogDock

    root = logging_module.getLogger()
    original_level = root.level
    root.setLevel(logging_module.DEBUG)
    dock = LogDock(main_window, verbose=True)
    try:
        assert dock.verbose_checkbox.isChecked()
    finally:
        dock.remove_handler()
        root.setLevel(original_level)


def test_remove_handler_detaches_idempotently_and_stops_receiving(main_window):
    """Phase 4.3 — remove_handler() is the single teardown path for the
    root-logger handler: it detaches the handler, is safe to call twice
    (closeEvent + destroyed + fixture teardown can all fire), and a
    detached handler no longer feeds the panel."""
    from gui.docks.log_panel import LogDock

    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    dock = LogDock(main_window, verbose=False)
    try:
        assert dock._handler in root.handlers
        dock.remove_handler()
        assert dock._handler not in root.handlers
        # idempotent — a second call (e.g. from the destroyed signal after
        # closeEvent already ran) must be a no-op
        dock.remove_handler()
        assert dock._handler not in root.handlers
        # detached handler no longer feeds the panel
        logging.getLogger("kicadstamp.gui_test.removed").info("after removal")
        assert "after removal" not in dock.text.toPlainText()
    finally:
        dock.remove_handler()
        root.setLevel(original_level)


def test_close_removes_handler_and_reshow_readds(real_main_window):
    """Phase 4.3 — closing the dock detaches its root-logger handler (so a
    window torn down while the dock is closed can't leak it), and showing
    the dock again re-attaches it, so closing/reopening from the View menu
    keeps the panel live."""
    root = logging.getLogger()
    dock = real_main_window.log_dock
    assert dock._handler in root.handlers

    real_main_window.show()
    dock.close()
    assert dock._handler not in root.handlers

    dock.show()
    assert dock._handler in root.handlers
    # teardown of real_main_window also calls remove_handler() — idempotent
    dock.remove_handler()
    assert dock._handler not in root.handlers


def test_handler_attaches_to_queue_listener_when_one_is_running(qapp, main_window, monkeypatch):
    """The queue-based logging rework (2026-08-15, plan_2026_08_15_queue_based_logging.md):
    when setup_logging() has started a QueueListener, LogDock's handler must
    attach to THAT listener (its single thread formats/writes records) instead
    of directly to the root logger — otherwise the Qt handler would stay on
    the synchronous path and keep the whole "logging blocks the calling
    thread" bug class open. With no listener configured (the normal GUI-test
    environment) LogDock falls back to the old direct root attachment — that
    path is covered by every other test in this file."""
    import queue as queue_module
    from logging.handlers import QueueListener

    from gui.docks import log_panel as log_panel_mod
    from gui.docks.log_panel import LogDock

    root = logging.getLogger()
    original_level = root.level

    # A real, started listener, constructed by hand (setup_logging() itself
    # is never called in GUI tests — see plan). LogDock's handler is then
    # attached to it via the monkeypatched get_log_listener().
    some_handler = logging.StreamHandler()
    listener = QueueListener(queue_module.Queue(), some_handler)
    listener.start()
    monkeypatch.setattr(log_panel_mod, "get_log_listener", lambda: listener)

    dock = None
    try:
        root.setLevel(logging.DEBUG)
        dock = LogDock(main_window, verbose=False)

        # the handler went to the listener, NOT to the root logger
        assert dock._handler in listener.handlers
        assert dock._handler not in root.handlers

        # a record pushed onto the real queue is formatted/written by the
        # listener thread and reaches the panel via the dock's Qt signal
        # (queued onto the UI thread — same path the background-thread test
        # above exercises)
        record = logging.LogRecord(
            name="kicadstamp.gui_test.listener_path",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="via the queue listener",
            args=None,
            exc_info=None,
        )
        listener.queue.put(record)
        _pump(qapp, lambda: "via the queue listener" in dock.text.toPlainText())

        # remove_handler() detaches from the listener and is idempotent
        dock.remove_handler()
        assert dock._handler not in listener.handlers
        assert dock._handler not in root.handlers
        dock.remove_handler()
    finally:
        if dock is not None:
            dock.remove_handler()
        listener.stop()
        root.setLevel(original_level)
