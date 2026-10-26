# gui/hub_infrastructure.py
"""The plumbing around the docks — NOT docks. Moved out of `gui/dock_hub.py` (Ч1 of
plan_2026_10_22_dock_hub_reorganization, rule 45: a giant only shrinks).

Two things live here, both of them "one owner, one place" rules that used to sit inside a
2900-line class:

  * `RootLogFile` — the ONE owner of the root config's own `log_file:` FileHandler. The
    handler is re-peeked on EVERY root change (never cached), and the swap detaches the
    previous handler from BOTH the root logger and the live QueueListener before attaching
    the new one, so a project switch cannot leave two files written by one process.
  * The config working set's listener/timer/dirty indicator: staging is ON whenever a
    project is open, the active graph root follows the same event, and every staged write
    refreshes the trees once (QTimer-debounced, so a burst of stages coalesces).

Why functions taking a "hub-shaped" host instead of a class: the test suite calls
`DockHub._on_root_changed_for_working_set(_Fake(), path)` as an UNBOUND method on a fake that
has nothing but `_update_dirty_indicator` (tests/config/test_config_format3_wiring.py). That
cell holds the `connect(...)` line stowed, it is not ours to edit (п. 33), and a host object
with two attributes is exactly what a module function needs.

The hub keeps one-line delegates under its OLD names — `_on_root_file_changed_for_logging`,
`_on_root_changed_for_working_set`, `_on_working_set_changed`, `_refresh_from_working_set`,
`_update_dirty_indicator` — because signals, `_safe_call` labels and the suite call those
names. `DockHub._log_file_handler` stays a read-only PROPERTY over `RootLogFile.current`:
twelve test teardowns and `tests/gui/conftest.py` read it, and a refactor may not edit them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer

from kicadstamp.cli_common import peek_log_file
from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root
from kicadstamp.logging_setup import get_log_listener


def attach_handler(handler) -> None:
    """Attach the root-config log_file: FileHandler either to the live QueueListener (when
    setup_logging() has started one) or directly to the ROOT logger when no listener exists
    (unit tests, no setup_logging call) — idempotent across both paths, so a swap can call it
    on every re-peek. Since 2026-08-15 (queue-based logging rework, see
    techdocs/handoff/plan_2026_08_15_queue_based_logging.md) the handler's
    formatting/writing runs on the listener's single thread, so logging can never block the
    calling thread on a handler lock."""
    if handler is None:
        return
    listener = get_log_listener()
    if listener is not None:
        if handler not in listener.handlers:
            listener.handlers = listener.handlers + (handler,)
    else:
        root = logging.getLogger()
        if handler not in root.handlers:
            root.addHandler(handler)


def detach_handler(handler) -> None:
    """Detach the root-config log_file: FileHandler from BOTH the ROOT logger and the live
    QueueListener (whichever path it was attached through) — idempotent, so the swap on a
    root-file change and teardown fixtures can call it freely."""
    if handler is None:
        return
    root = logging.getLogger()
    if handler in root.handlers:
        root.removeHandler(handler)
    listener = get_log_listener()
    if listener is not None and handler in listener.handlers:
        listener.handlers = tuple(
            h for h in listener.handlers if h is not handler)


class RootLogFile:
    """The root config's own `log_file:` FileHandler — ONE handler, re-peeked per root.

    `current` is the handler the process holds right now (None before a root or when the
    config names no log_file:). `swap(path)` is the WHOLE rule: drop the old one, peek the
    new path fresh, attach. Keeping the state in an object is what lets the hub expose
    `_log_file_handler` as a read-only property over it instead of owning the field."""

    def __init__(self) -> None:
        self.current: Optional[logging.Handler] = None

    def swap(self, path) -> None:
        """Attaches a FileHandler using the CURRENT root config's own log_file:
        (Config.log_file). Re-peeked fresh on every root-file change (never cached), matching
        kicadstamp_cli.py's own per-invocation freshness — same cli_common.peek_log_file()
        helper, so a typo/missing log_file: is handled exactly the same way the CLI already
        handles it (a logged warning, never a raise). DEBUG level regardless of the GUI's own
        console/LogDock verbosity, same as the CLI's file handler
        (kicadstamp/logging_setup.py)."""
        if self.current is not None:
            detach_handler(self.current)
            self.current = None
        if path is None:
            return
        log_file = peek_log_file(str(path))
        if log_file is None:
            return
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as e:
            logging.warning(f"Could not open log_file {log_file!r}: {e}")
            return
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        attach_handler(handler)
        self.current = handler


# ── Config working set (2026-09-01, plan project_save_model) ─────────────────

def on_root_changed(host, path) -> None:
    """Enable/clear the config working set when the project root changes: staging is ON
    whenever a project is open (so every dock edit lands in the working set, not on disk),
    OFF/cleared when the project closes. A root switch starts with a clean working set; the
    unsaved-changes guard lives in RootMetadataDock.set_root_file/close_project.

    The ACTIVE GRAPH ROOT (У4.1, variant B) is set in the SAME place: the format-3 writer
    stamp reads it to resolve reference UUIDs, and it is visible from worker threads exactly
    like WORKING_SET.

    `host` is deliberately narrow — a bound DockHub, or the bare `_Fake` of
    tests/config/test_config_format3_wiring.py (which has only `_update_dirty_indicator`)."""
    WORKING_SET.enabled = path is not None
    WORKING_SET.clear()
    set_active_graph_root(path)
    host._update_dirty_indicator()


def on_working_set_changed(host) -> None:
    """Every staged write (or clear) — reflect the dirty state immediately and schedule a
    debounced refresh so the tree/collectors show the staged content."""
    host._update_dirty_indicator()
    host._ws_refresh_timer.start()


def refresh_from_working_set(host) -> None:
    """Debounced: the working set changed (staged or flushed) — rebuild the trees and
    graph-derived combos from the current (staged) state."""
    host._safe_call("config_tree_dock.refresh", host.config_tree_dock.refresh)
    host._refresh_graph_dependent_choices()


def update_dirty_indicator(host) -> None:
    """Mirror the working set's dirty state into MainWindow's status-bar ● and File > Save
    text (getattr-guarded: DockHub is also built against a plain QMainWindow in tests)."""
    fn = getattr(host.main_window, "_update_dirty_indicator", None)
    if fn is not None:
        fn()


def install_working_set(host) -> QTimer:
    """The debounced refresh timer (a burst of stages in one event-loop turn coalesces into
    one rebuild) and the subscription every stage/clear notifies. Returns the timer so the
    hub keeps holding it (as it always did) and tests can see it."""
    timer = QTimer(host.main_window)
    timer.setSingleShot(True)
    timer.timeout.connect(host._refresh_from_working_set)
    WORKING_SET.add_listener(host._on_working_set_changed)
    return timer
