# kicadstamp/kicad/pynng_safety.py
"""
Bounds pynng.nng.Socket.close() with an EXTERNAL timeout on a throwaway
daemon thread — same pattern as gui/connection.py's _connect_with_timeout(),
applied to the opposite end of the socket's lifecycle.

Found live 2026-08-15 via py-spy: Socket.close() (nng.py:429) calls
lib.nng_close() — a blocking native call with no timeout of its own — and
Socket.__del__ (nng.py:441) calls close() unconditionally. Any Socket
finalized by the GC, on ANY thread, at ANY allocation point, can therefore
wedge that thread forever. Observed live: a thread's routine logging.debug()
call went through QueueHandler.prepare()'s copy.copy(record) (bpo-35726),
which triggered a GC cycle that finalized a stale Req0 socket mid-copy —
since logging.Handler.handle() unconditionally holds the handler's own lock
around emit(), and root's QueueHandler is a single shared instance, every
other thread trying to log anything queued up behind that same lock. A full
GUI freeze, indistinguishable from the one the 2026-08-15 QueueHandler/
QueueListener rework (plan_2026_08_15_queue_based_logging.md) was meant to
close — that rework moved the hazard out of format()/formatTime() on the
OUTPUT handlers, but never touched this path, so the underlying hazard was
relocated, not removed.

Patching the actual blocking primitive at its single source means every
call path is covered automatically, with no per-call-site wrapping needed:
explicit close() calls AND __del__-triggered GC calls, GUI and CLI alike.

Applied once, at import time, by kicadstamp/kicad/adapter.py (the only
module in this codebase that touches kipy/pynng) — every entry point (GUI,
CLI, author_cli, tests) picks it up for free just by importing that module.

── The TWO close paths are bounded DIFFERENTLY (Ф1.13, 2026-09-30) ────────────

The single bound above was NOT enough, and the reason is the very hazard in
the paragraph :data:`~gui/connection` describes. `Socket.__del__` is reached
by the GC from INSIDE logging.Handler.handle — i.e. while that thread already
holds the handler's own lock. Bounding the close with a 2 s `join` there holds
the WHOLE PROCESS's logging for up to 2 s per finalized socket, and the
`logger.warning` on the timeout takes that same lock once more. Measured on
2026-09-30 (reverse-order run): tens of thousands of such events, a process-wide
logging convoy (every thread stuck in logging.Handler.acquire), tests with
bounded waits failing in DIFFERENT cells run to run, and the run aborting
without a summary (a core dump). It is a real production freeze too, not just a
test artefact.

So the two paths are now separated, because they want opposite things:

* **GC path** — :func:`_gc_del`, installed as ``Socket.__del__``: dispatch the
  close on a background daemon thread and RETURN. NO `join` (nothing waits for
  a finalizer's result) and NO logging of any kind, not even debug — any logging
  call goes back through the same handler lock and re-creates the convoy. A
  socket whose native close never returns is simply left on that thread; the
  count is kept in module counters read by :func:`gc_close_stats` instead of a
  log line.
* **Explicit path** — :func:`_bounded_close`, installed as ``Socket.close``:
  unchanged — bounded 2 s `join` and ONE WARNING on a hang. An explicit caller
  needs the outcome, and explicit calls are not made under the logging lock.

This is why there is no re-entrancy guard and no rate limit here: with no wait
and no write on the GC path there is nothing left to re-enter and nothing to
flood the lock with.
"""
import logging
import threading

import pynng.nng

logger = logging.getLogger(__name__)

_CLOSE_TIMEOUT_S = 2.0

#: The TRUE underlying close, captured reload-safely. A plain
#: `_original_close = Socket.close` would, on `importlib.reload`, capture our
#: OWN installed wrapper (the guard below keeps `Socket.close` as the first
#: wrapper), so both paths would then call the explicit wrapper: the GC path
#: would join and WARN, i.e. the very convoy this module exists to prevent.
#: The wrapper carries the true original on `_kicadstamp_original`, so it is
#: recovered instead of the wrapper.
_installed_close = pynng.nng.Socket.close
_original_close = getattr(_installed_close, "_kicadstamp_original",
                          _installed_close)

#: Counters for the GC path (Ф1.13). A socket whose close never returns is
#: counted, never logged: logging from that path is what caused the stall.
#:
#: The lock is an RLock, and that is a REVIEW FIX (Claude, 30.09): the finalizer
#: takes it inside the GC, so a plain Lock would self-deadlock the moment the
#: critical section ever re-entered this module from the same thread. NOTHING
#: BLOCKING MAY EVER GO UNDER THIS LOCK — no list, no log call, no join: today it
#: guards two integer increments and nothing else, which is the whole reason it
#: is safe. Lock-free would be better still, but the suggested `itertools.count()`
#: cannot be read without consuming except through `__reduce__`, which is
#: deprecated in 3.12 and removed in 3.14 (measured 30.09).
_gc_lock = threading.RLock()
_gc_started = 0
_gc_finished = 0


def gc_close_stats() -> dict:
    """``{'started': N, 'finished': M, 'abandoned': N - M}`` for the GC path.

    Diagnostics read this instead of grepping a log — a log line is the one
    thing this path must never produce.
    """
    with _gc_lock:
        started, finished = _gc_started, _gc_finished
    return {"started": started, "finished": finished,
            "abandoned": started - finished}


def _gc_del(self) -> None:
    """``Socket.__del__`` — the GC path: fire-and-forget, no join, no logging.

    See the module docstring: this runs (via GC) from inside
    ``logging.Handler.handle``, under the handler's lock, so it must neither
    wait (``join``) nor log. The abandoned-close count is in
    :func:`gc_close_stats`.
    """
    # BOTH names, not just the first: the RuntimeError branch below writes
    # _gc_finished too, and a missing `global` there would make it a LOCAL read
    # (UnboundLocalError) rather than a counter bump — caught by pyflakes.
    global _gc_started, _gc_finished
    with _gc_lock:
        _gc_started += 1

    def _run():
        global _gc_finished
        try:
            _original_close(self)
        except Exception:
            # Matches the original __del__->close() call site: a close failure
            # was already non-actionable, and especially so under GC.
            pass
        finally:
            with _gc_lock:
                _gc_finished += 1

    try:
        threading.Thread(target=_run, daemon=True,
                         name="pynng.Socket.close(gc)").start()
    except RuntimeError:
        # Interpreter shutting down (can't start new threads) — nothing left
        # to protect at this point.
        with _gc_lock:
            _gc_finished += 1


def _bounded_close(self) -> None:
    """``Socket.close`` — the EXPLICIT path: bounded wait, one WARNING on hang.

    Unchanged by Ф1.13: an explicit caller wants to know the outcome, and
    explicit calls are not made under the logging lock.
    """
    def _run():
        try:
            _original_close(self)
        except Exception:
            # Matches the original call sites: close() failures were already
            # non-actionable — never let a close error propagate out of a
            # __del__-driven GC finalize or an explicit teardown.
            pass

    try:
        thread = threading.Thread(target=_run, daemon=True, name="pynng.Socket.close")
        thread.start()
    except RuntimeError:
        # Interpreter shutting down (can't start new threads) — nothing left
        # to protect at this point.
        return
    thread.join(timeout=_CLOSE_TIMEOUT_S)
    if thread.is_alive():
        logger.warning(
            "pynng Socket.close() did not return within %.1fs — abandoning "
            "it on an orphaned thread (same tradeoff as gui/connection.py's "
            "_connect_with_timeout for dial())", _CLOSE_TIMEOUT_S)


if not getattr(pynng.nng.Socket.close, "_kicadstamp_bounded", False):
    _bounded_close._kicadstamp_bounded = True
    _bounded_close._kicadstamp_original = _original_close
    pynng.nng.Socket.close = _bounded_close

# Ф1.13: __del__ is patched SEPARATELY, so the GC path never joins and never
# logs. Assigning __del__ after the class exists must refresh CPython's
# tp_finalize slot — tests/test_pynng_safety.py proves it on a real
# finalization (del + gc.collect()), not by calling the method by hand.
if not getattr(pynng.nng.Socket.__del__, "_kicadstamp_gc_bounded", False):
    _gc_del._kicadstamp_gc_bounded = True
    pynng.nng.Socket.__del__ = _gc_del
