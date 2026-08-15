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
"""
import logging
import threading

import pynng.nng

logger = logging.getLogger(__name__)

_CLOSE_TIMEOUT_S = 2.0

_original_close = pynng.nng.Socket.close


def _bounded_close(self) -> None:
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
    pynng.nng.Socket.close = _bounded_close
