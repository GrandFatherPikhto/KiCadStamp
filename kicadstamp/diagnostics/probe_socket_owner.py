#!/usr/bin/env python3
"""probe_socket_owner.py — prints whether the two holes in the shared kipy REQ
socket's ownership are ALIVE on the code as it stands now, plus the trap-3
measurement (an inner start_long_op under an outer defer_while_socket_busy).

Ш1 of plan_2026_09_23_socket_owner_and_door_docs.md. Stand-in connection, no
KiCad, no GUI window (an offscreen QApplication only for the Qt event loop).

  Д1 (вламывание) — a "poll tick" holds the socket (long_op_active=True) and a
  start_long_op arrives. Does the worker start INTO the tick's in-flight
  transaction, i.e. are there two owners of the flag at once?

  Д2 (чужой сброс) — the same scenario run to the end. Does _release() clear a
  flag this controller did not raise, while the "tick" is still in flight?

  trap 3 — the places that defer with defer_while_socket_busy and whose
  continuation calls start_long_op. Does the inner start buy a SECOND 120 ms
  deferral, or a refusal through on_error instead of the outer on_still_busy?

The probe PRINTS, it does not assert: a hole that no longer reproduces after
the fix is exactly as informative as one that does. Read the verdict block.

Run:
    python -m kicadstamp.diagnostics.probe_socket_owner
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import gui.worker as worker_mod
from gui.worker import LongOpController, SNAPSHOT_REFRESH_RETRY_DELAY_MS


class _FlagTrackingConnection:
    """A stand-in connection that RECORDS every write to long_op_active, so the
    probe can say WHO raised the flag and WHO cleared it, not only the final
    value. Reads go through the same property socket_busy() and the controller
    use."""

    def __init__(self):
        self._long_op_active = False
        self.writes = []          # every value written, in order
        self.written_by = []      # the thread name that wrote it

    @property
    def long_op_active(self):
        return self._long_op_active

    @long_op_active.setter
    def long_op_active(self, value):
        self.writes.append(bool(value))
        self.written_by.append(threading.current_thread().name)
        self._long_op_active = bool(value)


def _pump(app, until, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            return False
        app.processEvents()
        time.sleep(0.005)
    return True


def _drain(app, controllers, timeout=3.0):
    """Wait until every controller's QThread has genuinely stopped, then join
    it. Pumping only until a side effect appears would leave a QThread running
    and the process would abort at exit with the 2026-08-03
    "QThread: Destroyed while thread is still running" (that crash is exactly
    what _ACTIVE_CONTROLLERS exists to prevent)."""
    for ctrl in controllers:
        _pump(app, lambda c=ctrl: c not in worker_mod._ACTIVE_CONTROLLERS,
              timeout=timeout)
    for ctrl in controllers:
        thread = ctrl._thread
        if thread is not None:
            thread.wait(2000)


def probe_owner_holes(app):
    connection = _FlagTrackingConnection()

    # The "poll tick" of plan §1.1: it raises the flag unconditionally BEFORE
    # its own request is in flight, and only its own _on_result clears it. Here
    # the tick owns the socket and has NOT finished.
    connection.long_op_active = True
    tick_writes = len(connection.writes)

    ran = []          # set on the worker thread if the op actually started
    results = []
    errors = []

    controller = LongOpController(connection, [])
    controller.finished.connect(results.append)
    controller.failed.connect(errors.append)

    def op():
        ran.append(connection.long_op_active)
        return "done"

    controller.start(op)

    thread_created_immediately = controller._thread is not None

    # Pump until the op finished (current code) or the deferred retry has had
    # its 120 ms (fixed code — where the op must NOT run against a busy socket);
    # then drain whatever thread was started, so the process exits cleanly.
    _pump(app, lambda: bool(results) or bool(errors),
          timeout=max(2.0, SNAPSHOT_REFRESH_RETRY_DELAY_MS / 1000.0 + 1.0))
    _drain(app, [controller])

    writes_after_start = connection.writes[tick_writes:]
    writers_after_start = connection.written_by[tick_writes:]
    flag_now = connection.long_op_active

    print("=" * 72)
    print("probe_socket_owner — Д1 (вламывание) / Д2 (чужой сброс)")
    print("=" * 72)
    print(f"tick simulation: raised long_op_active BEFORE start_long_op "
          f"(writes so far: {connection.writes[:tick_writes]})")
    print()
    print("Д1 — did a second owner break into the tick's transaction?")
    print(f"    worker QThread created synchronously in start(): "
          f"{thread_created_immediately}")
    print(f"    op function actually ran on the worker thread: {bool(ran)}")
    print(f"    flag value read from inside the op (busy?): {ran}")
    print(f"    flag writes made by start_long_op: {writes_after_start} "
          f"by {writers_after_start}")
    owners = 1 + (1 if (thread_created_immediately or ran) else 0)
    print(f"    simultaneous owners of the shared socket: {owners}")
    d1_alive = bool(ran)
    verdict_d1 = ("ALIVE — the op ran into the tick's transaction"
                  if d1_alive else
                  "not reproduced — the op did not enter a busy socket")
    print(f"    VERDICT Д1: {verdict_d1}")
    print()
    print("Д2 — did _release() clear a flag this controller did not raise?")
    print(f"    op result: {results}")
    print(f"    op refusal (on_error): {errors}")
    print(f"    long_op_active AFTER the op, tick still in flight: {flag_now}")
    d2_alive = (not flag_now) and bool(results)
    verdict_d2 = ("ALIVE — a foreign flag was cleared"
                  if d2_alive else
                  "not reproduced — the tick's flag survived")
    print(f"    VERDICT Д2: {verdict_d2}")
    print()
    print(f"SUMMARY: Д1={'ALIVE' if d1_alive else 'closed'} "
          f"Д2={'ALIVE' if d2_alive else 'closed'}")
    print("=" * 72)
    return d1_alive, d2_alive


def probe_outer_deferral(app):
    """Trap 3 measurement: the inner start_long_op lives under the outer
    defer_while_socket_busy. Counts every QTimer.singleShot armed (each is one
    120 ms deferral) and which refusal path the caller hears."""
    print()
    print("-" * 72)
    print("trap 3 — outer defer_while_socket_busy + inner start_long_op")
    print("-" * 72)

    for still_busy_on_retry in (False, True):
        connection = _FlagTrackingConnection()
        connection.long_op_active = True          # the tick holds the socket
        scheduled = []
        real_single_shot = worker_mod.QTimer.singleShot
        worker_mod.QTimer.singleShot = (
            lambda delay, cb: scheduled.append((delay, cb)))
        started = []
        ran = []
        told = []
        try:
            def proceed():
                started.append(worker_mod.start_long_op(
                    connection, (), lambda: ran.append(True) or "done",
                    lambda _r: None, lambda _m: None))

            worker_mod.defer_while_socket_busy(
                connection, (), proceed, lambda: told.append(True))

            armed_at_call = len(scheduled)
            if not still_busy_on_retry:
                connection.long_op_active = False   # the tick finished
            if scheduled:
                scheduled[0][1]()                   # fire the outer retry
            if started:
                _pump(app, lambda: bool(ran) and not connection.long_op_active,
                      timeout=2.0)
                _drain(app, started)

            print(f"  retry still busy = {still_busy_on_retry}:")
            print(f"    single-shot deferrals armed at call: {armed_at_call} "
                  f"(delay {scheduled[0][0] if scheduled else None} ms)")
            print(f"    total single-shot deferrals armed:   {len(scheduled)}")
            print(f"    inner start_long_op called:          {len(started)}")
            print(f"    outer on_still_busy called:          {bool(told)}")
            print(f"    worker ran:                          {bool(ran)}")
            print(f"    inner controllers with no thread:    "
                  f"{len([s for s in started if s._thread is None])}")
        finally:
            worker_mod.QTimer.singleShot = real_single_shot


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    probe_owner_holes(app)
    probe_outer_deferral(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
