# tests/test_pynng_safety.py
"""kicadstamp/kicad/pynng_safety.py — the import-time patch that bounds
pynng.nng.Socket.close() with an external timeout.

Motivation (found live 2026-08-15 via py-spy, see the module's docstring):
Socket.__del__ calls close() unconditionally, and a Socket finalized by the
GC — mid-copy.copy() inside QueueHandler.prepare(), on ANY logging call —
could wedge that thread's handler lock forever, freezing the whole GUI.
These tests pin down the patch's four contracts: fast closes stay fast,
hanging closes never block the caller, the patch is applied on import, and
re-applying it never double-wraps."""
import gc
import importlib
import logging
import threading
import time

import pynng.nng

import kicadstamp.kicad.pynng_safety as pynng_safety


def _wait_for_thread_exit(name, timeout=5.0):
    """Waits for the named helper thread to leave threading.enumerate().

    _bounded_close() does its work on a throwaway daemon thread, so "the
    caller returned" never implies "the thread finished". The hanging-close
    test below releases its mock after its assertions and must then also prove
    that thread actually left — otherwise the next edit could quietly restore
    a thread parked for the rest of the run
    (plan_2026_09_12_parked_test_threads.md)."""
    deadline = time.monotonic() + timeout
    while True:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


class _DummySocket:
    """Stand-in for a pynng.nng.Socket — only the close() path is exercised,
    so no real native socket is needed."""

    def __init__(self):
        self.closed = False


def test_close_returns_promptly_when_underlying_close_is_fast(monkeypatch):
    called = []

    def _fast_close(self):
        called.append(self)

    monkeypatch.setattr(pynng_safety, "_original_close", _fast_close)

    dummy = _DummySocket()
    start = time.monotonic()
    pynng_safety._bounded_close(dummy)
    elapsed = time.monotonic() - start

    assert called == [dummy]
    # A fast close must stay fast — the daemon-thread hop is negligible.
    assert elapsed < 0.5


def test_close_does_not_block_when_underlying_close_hangs(monkeypatch):
    monkeypatch.setattr(pynng_safety, "_CLOSE_TIMEOUT_S", 0.2)

    never_finish = threading.Event()

    def _hanging_close(self):
        # Simulates lib.nng_close() never returning on a wedged native
        # socket — the exact defect this patch exists to survive. It stays
        # parked well past the caller's timeout (that is what is being
        # proven) until this test releases it below — but not for the rest of
        # the run.
        never_finish.wait()

    monkeypatch.setattr(pynng_safety, "_original_close", _hanging_close)

    dummy = _DummySocket()
    start = time.monotonic()
    pynng_safety._bounded_close(dummy)
    elapsed = time.monotonic() - start

    # Bounded by _CLOSE_TIMEOUT_S, nowhere near "forever". Allow generous
    # slack so a slow CI box never flakes, but the caller clearly did NOT
    # block on the hanging native call.
    assert elapsed < 2.0
    # The caller returned while the native close is STILL parked — proving the
    # timeout path, not a fast close, is what returned.
    assert never_finish.is_set() is False

    # 2026-09-12 (plan_2026_09_12_parked_test_threads.md): the order here is
    # strict — every assertion above (including is_set() is False) is already
    # on record, and only NOW is the mock released so the orphaned
    # "pynng.Socket.close" daemon thread can finish instead of staying parked
    # next to Qt for the rest of the run. Nothing above this line is weakened:
    # is_set() is False is a statement about the moment, not about eternity.
    never_finish.set()
    assert _wait_for_thread_exit("pynng.Socket.close")


def test_socket_close_and_del_are_patched_on_import():
    # Importing the module installs BOTH bounds: the explicit close (bounded
    # wait + ONE WARNING on a hang) and __del__ (fire-and-forget, no join and
    # NO logging — Ф1.13). Req0 inherits close AND __del__ from Socket, so the
    # two swaps cover every call site.
    assert pynng.nng.Socket.close is pynng_safety._bounded_close
    assert getattr(pynng.nng.Socket.close, "_kicadstamp_bounded", False) is True
    assert pynng.nng.Socket.__del__ is pynng_safety._gc_del
    assert getattr(pynng.nng.Socket.__del__,
                   "_kicadstamp_gc_bounded", False) is True


def test_patch_is_idempotent_on_repeated_import():
    first_close = pynng.nng.Socket.close
    first_del = pynng.nng.Socket.__del__

    importlib.reload(pynng_safety)

    # Re-importing must NOT re-wrap: the current close is still the SAME
    # function object installed by the first import (the module guards the
    # swap with the _kicadstamp_bounded flag), so no double wrapper / no
    # recursion when a socket is actually closed. Same for the __del__ bound
    # added in Ф1.13 (its own _kicadstamp_gc_bounded flag).
    assert pynng.nng.Socket.close is first_close
    assert pynng.nng.Socket.__del__ is first_del
    # ...and the fresh module body's saved _original_close is NOT its own
    # _bounded_close, i.e. the reload did not start wrapping the wrapper.
    assert pynng_safety._original_close is not pynng_safety._bounded_close
    # The STRONG form of the same invariant (Ф1.13): the saved original must not
    # be the installed WRAPPER either — otherwise the reload-created module would
    # send both close paths through the explicit wrapper, and the GC path would
    # join and WARN (the convoy). `is not _bounded_close` above passes even in
    # that broken state (old wrapper != new function), so it is not enough.
    assert pynng_safety._original_close is not pynng.nng.Socket.close


# ── Ф1.13: the GC path must neither wait nor write to the log ────────────────


def _release_after(monkeypatch, timeout=0.2):
    """Patch _original_close to a close that hangs until the returned Event is
    set. The caller sets it, so no test parks a thread for the rest of the run."""
    release = threading.Event()

    def _hanging_close(self):
        release.wait()

    monkeypatch.setattr(pynng_safety, "_original_close", _hanging_close)
    return release


def test_gc_path_returns_immediately_and_writes_nothing_when_close_hangs(
        monkeypatch, caplog, records_from):
    """Ф1.13 cell 1. Socket.__del__ is reached (via GC) from INSIDE
    logging.Handler.handle — i.e. under that handler's lock. A `join` there
    stalls every thread that logs, and a warning takes the same lock again; that
    pair is the 2026-09-30 convoy. So the GC path must return at once and must
    write NOTHING, not even at DEBUG."""
    release = _release_after(monkeypatch)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="kicadstamp.kicad.pynng_safety")

    start = time.monotonic()
    pynng_safety._gc_del(_DummySocket())
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"the GC path waited {elapsed:.3f}s — it must not join"
    # The background close is still parked (proving the timeout path, not a
    # fast close, is what returned); only NOW is the mock released, so the
    # orphaned "pynng.Socket.close(gc)" thread can finish instead of parking
    # for the rest of the run.
    release.set()
    assert _wait_for_thread_exit("pynng.Socket.close(gc)")
    assert records_from("kicadstamp.kicad.pynng_safety") == [], (
        "the GC close path wrote to the log — any logging call goes back "
        "through the same handler lock the GC-triggered close may already hold")


def test_explicit_close_still_times_out_and_warns_exactly_once(
        monkeypatch, caplog, records_from):
    """Ф1.13 cell 2. The EXPLICIT path is unchanged: a bounded wait and exactly
    ONE WARNING on a hang. An explicit caller needs the outcome, and explicit
    calls are not made under the logging lock."""
    monkeypatch.setattr(pynng_safety, "_CLOSE_TIMEOUT_S", 0.2)
    release = _release_after(monkeypatch)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="kicadstamp.kicad.pynng_safety")

    start = time.monotonic()
    pynng_safety._bounded_close(_DummySocket())
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    warnings = [r for r in records_from("kicadstamp.kicad.pynng_safety")
                if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "did not return" in warnings[0].getMessage()
    debug = [r for r in records_from("kicadstamp.kicad.pynng_safety")
             if r.levelno == logging.DEBUG]
    assert debug == [], "the explicit path logs at most the one WARNING"

    release.set()
    assert _wait_for_thread_exit("pynng.Socket.close")


def test_del_patch_fires_on_real_finalization(caplog, records_from):
    """Ф1.13 cell 3. Assigning __del__ AFTER the class exists must refresh
    CPython's tp_finalize slot — proved on a REAL finalization (`del` +
    gc.collect()), never by calling the method by hand. If the slot were not
    refreshed, the whole Ф1.13 fix would be inert in production."""
    # NOT `is pynng_safety._gc_del`: a reload (the idempotency cell above)
    # re-runs the module body, so the module attribute is a fresh function while
    # the INSTALLED one stays the first — the flag is what identifies it.
    assert getattr(pynng.nng.Socket.__del__,
                   "_kicadstamp_gc_bounded", False) is True, (
        "Socket.__del__ is not the bounded GC close")

    before = pynng_safety.gc_close_stats()["started"]
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="kicadstamp.kicad.pynng_safety")

    # A REAL Socket INSTANCE, but built without __init__: a fully built socket
    # opens native resources whose teardown is the very thing under bound, and
    # this cell is about the FINALIZER SLOT, not about nng_close. `__new__` gives
    # a genuine instance the interpreter will finalize for real; the GC close
    # then runs `_original_close`, whose own `hasattr(self, "_socket")` guard
    # makes it a no-op — so no native call is made and no thread parks.
    sock = pynng.nng.Socket.__new__(pynng.nng.Socket)
    del sock
    deadline = time.monotonic() + 2.0
    while (pynng_safety.gc_close_stats()["started"] == before
           and time.monotonic() < deadline):
        gc.collect()
        time.sleep(0.01)

    assert pynng_safety.gc_close_stats()["started"] > before, (
        "the patched __del__ never ran on a real finalization — the assignment "
        "did not refresh the finalizer slot, so the GC close path is inert")
    assert records_from("kicadstamp.kicad.pynng_safety") == (
        []), "the GC finalization path wrote to the log"
    assert _wait_for_thread_exit("pynng.Socket.close(gc)")


def test_gc_path_writes_nothing_through_any_logger(monkeypatch, caplog):
    """Ф1.13 review cell (M6). "Our logger stayed quiet" is NOT the property that
    matters: a handler's lock is shared by EVERY logger, so a record sent to the
    ROOT logger — or to any other — takes the same lock the GC-triggered close may
    already hold. Capture at the root and judge by the RECORD's source file and by
    the thread that ran the close, never by logger name."""
    close_thread = []
    release = threading.Event()

    def _hanging_close(self):
        close_thread.append(threading.get_ident())
        release.wait()

    monkeypatch.setattr(pynng_safety, "_original_close", _hanging_close)

    records = []

    class _RootTap(logging.Handler):
        def emit(self, record):
            records.append((record.name, record.pathname,
                            threading.get_ident(), record.getMessage()))

    tap = _RootTap()
    root = logging.getLogger()
    root.addHandler(tap)
    try:
        caplog.clear()
        caplog.set_level(logging.DEBUG)  # every logger, every level
        pynng_safety._gc_del(_DummySocket())
        release.set()
        assert _wait_for_thread_exit("pynng.Socket.close(gc)")
    finally:
        root.removeHandler(tap)

    assert close_thread, "the close never ran — the scan went blind (rule 38)"
    from_our_source = [r for r in records if r[1].endswith("pynng_safety.py")]
    assert from_our_source == [], (
        "the GC close path logged (any logger is enough to take the handler lock "
        "the GC-triggered close may already hold): " + repr(from_our_source[:3]))
    from_close_thread = [r for r in records if r[2] == close_thread[0]]
    assert from_close_thread == [], (
        "a record was emitted from the very thread running the GC close: "
        + repr(from_close_thread[:3]))
