# tests/gui/test_snapshot_freshness.py
"""R.4 gates for ``gui/worker.py::refresh_snapshot_then`` —
plan_2026_09_11_stale_snapshot_positions.md.

``MainWindow._poll``'s automatic tick is a deliberate no-op once the board is
connected (one request at a time on the shared kipy REQ socket), so
``BoardConnection.snapshot`` is rebuilt only by connect()/manual refresh: it is
frozen at connect time, and every GEOMETRY read that trusts it follows stale
coordinates (K.2 #5/#6). The fix rebuilds it AT THE POINT OF USE — on the
worker thread, inside ``start_long_op``, so the op is the socket's only active
owner — and never by calling the adapter from the UI thread, which was exactly
the Commit H hang (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md
§0). These tests pin that contract down on the helper itself; the flow-level
gates (fresh position wins, fully-selected detection) live next to the docks'
own tests (tests/gui/test_imprint.py, tests/gui/test_phase3_wiring.py).
"""
import logging
import threading
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QObject

import gui.worker as worker_mod
from gui.worker import (defer_while_socket_busy, refresh_snapshot_then,
                        refresh_snapshot_then_with_retry,
                        snapshot_refresh_supported)
from tests.gui.conftest import _pump


class _RefreshingConnection:
    """A BoardConnection stand-in with a LIVE board behind it: ``refresh()``
    swaps the frozen snapshot for the next one and records where it ran."""

    def __init__(self, snapshots):
        self.board = SimpleNamespace(refresh=lambda: None)
        self._pending = [list(s) for s in snapshots]
        self.snapshot = self._pending.pop(0) if self._pending else []
        self.long_op_active = False
        self.refresh_threads: list = []

    def refresh(self):
        self.refresh_threads.append(threading.current_thread().name)
        if self._pending:
            self.snapshot = self._pending.pop(0)
        return None


class _GuardWidget:
    """The guard widget start_long_op disables for the op's lifetime."""

    def __init__(self):
        self.enabled = True

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, value):
        self.enabled = value


def test_snapshot_refresh_supported_requires_a_live_board():
    """Only a board that exposes refresh() can offer fresher data — everything
    else (no connection, no board, a board stand-in) keeps the cached snapshot."""
    assert not snapshot_refresh_supported(None)
    assert not snapshot_refresh_supported(SimpleNamespace(board=None))
    assert not snapshot_refresh_supported(
        SimpleNamespace(board=SimpleNamespace(adapter=object())))
    assert snapshot_refresh_supported(
        SimpleNamespace(board=SimpleNamespace(refresh=lambda: None)))


def test_refresh_snapshot_then_rebuilds_on_the_worker_thread_before_ready(qapp):
    """R.4 #2 + #3 — exactly ONE rebuild per operation, it runs on the WORKER
    thread (never the UI thread), and on_ready() runs afterwards on the UI
    thread, so the snapshot it reads is the fresh one."""
    connection = _RefreshingConnection([["stale"], ["fresh"]])
    widget = _GuardWidget()
    log: list = []

    refresh_snapshot_then(
        connection, (widget,),
        lambda: log.append(("ready", threading.current_thread().name,
                            list(connection.snapshot))),
        lambda message: log.append(("error", message)))

    # Nothing happened inline: the continuation only runs back on the UI thread
    # once the op's completion signal is delivered (queued from the worker).
    assert log == []
    assert not widget.enabled          # held for the op's lifetime

    _pump(qapp, lambda: not connection.long_op_active)

    assert [entry[0] for entry in log] == ["ready"]
    assert connection.refresh_threads                    # the rebuild happened...
    assert len(connection.refresh_threads) == 1          # ...exactly once
    assert connection.refresh_threads[0] != threading.current_thread().name
    assert log[0][1] == threading.current_thread().name  # on_ready = UI thread
    assert log[0][2] == ["fresh"]                        # fresh data, not stale
    assert widget.enabled                                # released again


def test_refresh_snapshot_then_falls_back_without_a_live_board():
    """A connection that cannot be refreshed (no live board behind it — a
    foreign/read-only connection, the docks' own stand-ins) keeps the cached
    snapshot and the continuation runs AT ONCE, on the calling thread: the old
    synchronous behaviour, unchanged."""
    connection = SimpleNamespace(snapshot=["cached"], board=None,
                                 long_op_active=False)
    log: list = []

    controller = refresh_snapshot_then(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)))

    assert controller is None
    assert log == ["ready"]
    assert connection.long_op_active is False


def test_refresh_snapshot_then_refuses_while_another_long_op_holds_the_socket(
        caplog):
    """A second concurrent owner on the shared REQ socket is exactly the
    2026-09-08 hang, so the request is REFUSED (and logged), never queued and
    never an inline adapter call on the UI thread."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    log: list = []

    with caplog.at_level(logging.WARNING):
        controller = refresh_snapshot_then(
            connection, (), lambda: log.append("ready"),
            lambda message: log.append(("error", message)))

    assert controller is None
    assert log == []                     # neither on_ready nor on_error
    assert connection.refresh_threads == []
    assert any("refused" in r.message for r in caplog.records)


def test_refresh_snapshot_then_reports_a_refusal_through_on_refused(caplog):
    """Э1 — the two ``None`` outcomes are distinguishable: when another long op
    holds the socket, the caller's ``on_refused`` runs (and neither on_ready nor
    on_error does), so a click can react instead of dead-ending. The internal
    WARNING stays diagnostic (gui.worker)."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    log: list = []

    with caplog.at_level(logging.WARNING):
        controller = refresh_snapshot_then(
            connection, (), lambda: log.append("ready"),
            lambda message: log.append(("error", message)),
            on_refused=lambda: log.append("refused"))

    assert controller is None
    assert log == ["refused"]            # ONLY the refusal signal
    assert connection.refresh_threads == []
    assert any("refused" in r.message for r in caplog.records)


def test_refresh_snapshot_then_never_refuses_without_a_live_board():
    """Э1 — ``on_refused`` marks a REFUSAL, not the "no live board" fallback:
    there the continuation still runs at once on the cached snapshot (the old
    synchronous behaviour) and no refusal is reported."""
    connection = SimpleNamespace(snapshot=["cached"], board=None,
                                 long_op_active=False)
    log: list = []

    controller = refresh_snapshot_then(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)),
        on_refused=lambda: log.append("refused"))

    assert controller is None
    assert log == ["ready"]
    assert connection.long_op_active is False


def _capture_retries(monkeypatch) -> list:
    """Capture QTimer.singleShot calls made by gui.worker (the helper's retry)
    WITHOUT waiting on real time — the exact idiom tests/gui/test_trees_dock.py
    already uses for a deferred rebuild. Returns [(delay_ms, callback), ...]."""
    scheduled: list = []
    monkeypatch.setattr(
        worker_mod.QTimer, "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)))
    return scheduled


def test_retry_recovers_when_the_socket_frees(qapp, monkeypatch):
    """Э2 — the refused click is retried ONCE after the delay; with the socket
    free by then the rebuild runs and on_ready continues on FRESH data."""
    connection = _RefreshingConnection([["stale"], ["fresh"]])
    connection.long_op_active = True
    scheduled = _capture_retries(monkeypatch)
    log: list = []

    refresh_snapshot_then_with_retry(
        connection, (),
        lambda: log.append(("ready", list(connection.snapshot))),
        lambda message: log.append(("error", message)))

    assert len(scheduled) == 1                     # exactly one retry armed
    assert scheduled[0][0] == worker_mod.SNAPSHOT_REFRESH_RETRY_DELAY_MS
    assert log == []                               # nothing ran yet
    assert connection.refresh_threads == []

    connection.long_op_active = False              # the tick finished
    scheduled[0][1]()                              # fire the deferred retry
    _pump(qapp, lambda: not connection.long_op_active)

    assert [entry[0] for entry in log] == ["ready"]
    assert log[0][1] == ["fresh"]                  # fresh, not the cached stale
    assert len(connection.refresh_threads) == 1    # exactly ONE rebuild


def test_retry_is_single_and_falls_back_to_the_cache(monkeypatch):
    """Э2 — a socket still busy on the retry means the continuation runs on the
    CACHED snapshot (on_cached reports it) and NO third attempt is armed."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    scheduled = _capture_retries(monkeypatch)
    log: list = []

    refresh_snapshot_then_with_retry(
        connection, (),
        lambda: log.append(("ready", list(connection.snapshot))),
        lambda message: log.append(("error", message)),
        on_cached=lambda: log.append("cached"))

    assert len(scheduled) == 1
    scheduled[0][1]()                              # retry: still busy

    assert len(scheduled) == 1                     # no third attempt
    assert log == ["cached", ("ready", ["stale"])]  # report, then the cache
    assert connection.refresh_threads == []        # never rebuilt


def test_retry_skips_a_window_closed_during_the_delay(qapp, monkeypatch):
    """Э2 ловушка 3 — the timer outlives the widget: a deleted owner means the
    retry touches nothing and raises nothing (no RuntimeError, no continuation)."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    scheduled = _capture_retries(monkeypatch)
    log: list = []
    owner = QObject()

    refresh_snapshot_then_with_retry(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)), owner=owner)

    sip.delete(owner)                              # the dialog closed meanwhile
    scheduled[0][1]()                              # must be a no-op

    assert log == []                               # no on_ready, no error
    assert connection.refresh_threads == []
    assert len(scheduled) == 1


def test_on_still_busy_refuses_instead_of_the_cached_continuation(monkeypatch):
    """Э2.5 — the coordinate consumers (the pivots) keep the refusal:
    on_still_busy runs and on_ready does NOT, so no position is ever read from a
    stale snapshot."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    scheduled = _capture_retries(monkeypatch)
    log: list = []

    refresh_snapshot_then_with_retry(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)),
        on_cached=lambda: log.append("cached"),
        on_still_busy=lambda: log.append("still_busy"))

    scheduled[0][1]()                              # retry: still busy

    assert log == ["still_busy"]                   # neither cache nor on_ready
    assert connection.refresh_threads == []


def test_no_live_board_never_arms_a_retry(monkeypatch):
    """Э2 — without a refreshable board there is nothing to retry: the
    continuation runs at once and no timer is armed."""
    connection = SimpleNamespace(snapshot=["cached"], board=None,
                                 long_op_active=False)
    scheduled = _capture_retries(monkeypatch)
    log: list = []

    refresh_snapshot_then_with_retry(
        connection, (), lambda: log.append("ready"),
        lambda message: log.append(("error", message)),
        on_cached=lambda: log.append("cached"))

    assert log == ["ready"]
    assert scheduled == []


# ── Э1 — the YOUNGER twin's liveness guard (defer_while_socket_busy) ────────

def test_defer_while_socket_busy_skips_a_window_closed_during_the_delay(
        qapp, monkeypatch):
    """Э1 — ``defer_while_socket_busy`` carries the SAME liveness guard as
    ``refresh_snapshot_then_with_retry``, and until 2026-09-14 nothing pinned it:
    deleting ``if _gone(): return`` from its retry left the whole suite green
    (plan_2026_09_14_defer_helper_liveness_guard P.0). Two twins, one guard, a
    sentinel for one of them.

    The ``QTimer`` outlives the widget, so a dock the user closed during the delay
    must make the retry touch NOTHING: ``proceed`` is not started and
    ``on_still_busy`` is NOT called — the latter is the whole point, because the
    real caller's ``on_still_busy`` is ``self._warn_no_node_offset`` on the
    deleted dock, i.e. exactly the ``RuntimeError`` from an event-loop callback
    this guard exists to prevent (the same class as the live ``_DialogSizeSaver``
    ``abort()`` of 2026-09-13). No third attempt either."""
    connection = SimpleNamespace(long_op_active=True, snapshot=["cached"])
    scheduled = _capture_retries(monkeypatch)
    log: list = []
    owner = QObject()

    armed = defer_while_socket_busy(
        connection, (), lambda: log.append("proceed"),
        lambda: log.append("still_busy"), owner=owner)

    assert armed is False                          # the press was deferred...
    assert len(scheduled) == 1                     # ...with EXACTLY ONE retry
    assert scheduled[0][0] == worker_mod.SNAPSHOT_REFRESH_RETRY_DELAY_MS
    assert log == []                               # nothing ran inline

    sip.delete(owner)                              # the dock closed meanwhile
    scheduled[0][1]()                              # must be a no-op

    assert log == []                               # no proceed, NO on_still_busy
    assert len(scheduled) == 1                     # and no third attempt


# ── Э2 — the guard-WIDGETS half of ``_gone()``: a hole found by mutation ────

def test_defer_while_socket_busy_watches_the_guard_widgets_too(qapp, monkeypatch):
    """Э2 (a hole found by mutation 2026-09-14) — the liveness guard checks the
    guard ``widgets`` as well as ``owner``, and nothing pinned that half: dropping
    ``candidates.extend(widget_list)`` from ``_gone()`` left the whole suite green
    (``pytest tests/gui -q``: 2131 passed), because BOTH twins' sentinels delete
    only ``owner``. The branch is live in the older twin's caller — imprint's
    pivots put a real BUTTON in the widgets tuple (``pivot_from_selection_button``)
    — so a widget that goes away during the delay must stop the retry even when
    ``owner`` is still alive. Same no-op contract as above: no ``proceed``, no
    ``on_still_busy``, no third attempt."""
    connection = SimpleNamespace(long_op_active=True, snapshot=["cached"])
    scheduled = _capture_retries(monkeypatch)
    log: list = []
    owner = QObject()
    widget = QObject()

    armed = defer_while_socket_busy(
        connection, (widget,), lambda: log.append("proceed"),
        lambda: log.append("still_busy"), owner=owner)

    assert armed is False
    assert len(scheduled) == 1

    sip.delete(widget)                             # the guard button went away
    scheduled[0][1]()                              # must be a no-op

    assert log == []                               # no proceed, NO on_still_busy
    assert len(scheduled) == 1                     # and no third attempt


def test_retry_watches_a_guard_widget_closed_during_the_delay(qapp, monkeypatch):
    """Э2 — the SAME hole on the older twin, closed for symmetry: dropping
    ``candidates.extend(widget_list)`` from ``refresh_snapshot_then_with_retry``'s
    ``_gone()`` also left the whole suite green (its sentinel, right above the
    retry guards, deletes only ``owner``). A deleted guard widget means the retry
    runs nothing at all — no rebuild, no ``on_cached``, no ``on_ready`` — which is
    what the caller's own button (imprint's ``pivot_from_selection_button``,
    its two pivot sites) relies on when the tab it lives on is rebuilt during the
    delay."""
    connection = _RefreshingConnection([["stale"]])
    connection.long_op_active = True
    scheduled = _capture_retries(monkeypatch)
    log: list = []
    owner = QObject()
    widget = QObject()

    refresh_snapshot_then_with_retry(
        connection, (widget,), lambda: log.append("ready"),
        lambda message: log.append(("error", message)), owner=owner,
        on_cached=lambda: log.append("cached"))

    assert len(scheduled) == 1

    sip.delete(widget)                             # the guard button went away
    scheduled[0][1]()                              # must be a no-op

    assert log == []                               # no cache, no ready, no error
    assert connection.refresh_threads == []
    assert len(scheduled) == 1                     # and no third attempt
