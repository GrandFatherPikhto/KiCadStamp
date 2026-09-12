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
import importlib
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


def test_socket_close_is_patched_on_import():
    # Importing the module is the patch; both the explicit path and the
    # __del__/GC path go through Socket.close, so this single swap covers
    # every call site (Req0 inherits close/__del__ from Socket unchanged).
    assert pynng.nng.Socket.close is pynng_safety._bounded_close
    assert getattr(pynng.nng.Socket.close, "_kicadstamp_bounded", False) is True


def test_patch_is_idempotent_on_repeated_import():
    first_close = pynng.nng.Socket.close

    importlib.reload(pynng_safety)

    # Re-importing must NOT re-wrap: the current close is still the SAME
    # function object installed by the first import (the module guards the
    # swap with the _kicadstamp_bounded flag), so no double wrapper / no
    # recursion when a socket is actually closed.
    assert pynng.nng.Socket.close is first_close
    # ...and the fresh module body's saved _original_close is NOT its own
    # _bounded_close, i.e. the reload did not start wrapping the wrapper.
    assert pynng_safety._original_close is not pynng_safety._bounded_close
