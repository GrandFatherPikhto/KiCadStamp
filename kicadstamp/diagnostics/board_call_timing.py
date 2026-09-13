"""Times every KiCadBoardAdapter call, and every kipy round trip inside it.

Input        — nothing; install() is called by run_gui_with_timing before the GUI starts.
Expected     — a JSON Lines file, one object per call, under <repo>/diagnostics/.
Live KiCad   — Partially (the GUI runs with or without it; only a live board produces calls).
Run          — not run directly; see run_gui_with_timing.py and report_board_timing.py.

Installed from OUTSIDE the production code (monkey patch, see install()) so nothing in
kicadstamp/ or gui/ has to be edited or reverted, and so it can never collide with work in
flight in a shared checkout.

Why it exists (2026-09-12/13): the question "does the GUI freeze because it reads the board
on the UI thread" could not be answered from the code. This answered it — measured on a live
board, the adapter accounted for 3.3% of a session and the UI thread for 0.07%, which is what
retired the plan to move every board read behind `await`. Kept because the same question
recurs, and because it also sizes DEFAULT_TIMEOUT_MS from data instead of a guess.

Writes JSON Lines (one self-contained object per call, appended and flushed immediately)
rather than one JSON document, so a crash mid-session still leaves every completed line
readable.

Board DATA is never recorded — only call names, durations, and item counts.
"""
import functools
import json
import os
import threading
import time
import types
from typing import Any, Optional

# Not board round trips: a context manager whose cost is in __enter__/__exit__
# (timing the factory call measures nothing), and teardown.
_SKIP = {"temporarily_ignore_selection", "close"}

_lock = threading.Lock()
_fh = None
_installed = False

# Adapter methods nest (update_items -> commit_with_retry -> begin_commit/
# push_commit, set_field_values_bulk -> commit_with_retry, ...), so summing
# every row double-counts the same wall time. Each row carries its nesting
# depth, per thread; only depth 0 rows are disjoint in time.
_depth = threading.local()


def default_log_dir() -> str:
    """<repo root>/diagnostics — gitignored, and where the report tool looks.
    Created on demand: a fresh clone does not carry a gitignored directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "diagnostics")
    os.makedirs(out, exist_ok=True)
    return out


def _count(value: Any) -> Optional[int]:
    """Item count of a list-ish argument or result, or None. Deliberately
    does not touch the objects themselves."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return None


def _record(method: str, dur_ms: float, ok: bool, in_n, out_n, depth: int = 0,
            err: str = "") -> None:
    row = {
        "ts": round(time.time(), 3),
        "method": method,
        "ms": round(dur_ms, 2),
        "ok": ok,
        "thread": threading.current_thread().name,
        "in_n": in_n,
        "out_n": out_n,
        "depth": depth,
    }
    if err:
        row["err"] = err[:200]
    line = json.dumps(row, ensure_ascii=False)
    with _lock:
        _fh.write(line + "\n")
        _fh.flush()


def _wrap(name: str, fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        in_n = next((_count(a) for a in args if _count(a) is not None), None)
        depth = getattr(_depth, "n", 0)
        _depth.n = depth + 1
        t0 = time.perf_counter()
        try:
            result = fn(self, *args, **kwargs)
        except BaseException as exc:
            _depth.n = depth
            _record(name, (time.perf_counter() - t0) * 1000.0, False,
                    in_n, None, depth, f"{type(exc).__name__}: {exc}")
            raise
        _depth.n = depth
        _record(name, (time.perf_counter() - t0) * 1000.0, True, in_n,
                _count(result), depth)
        return result

    # Own marker: functools.wraps sets __wrapped__, but so does @contextmanager
    # and friends already present in the adapter, so that is not proof of ours.
    wrapper._board_timing = True
    return wrapper


def install(path: Optional[str] = None) -> str:
    """Patches every plain method of KiCadBoardAdapter. Returns the log path.
    Idempotent — a second call is a no-op."""
    global _fh, _installed
    if _installed:
        return _fh.name
    from kicadstamp.kicad.adapter import KiCadBoardAdapter

    if path is None:
        path = os.path.join(default_log_dir(), f"board_timing_{os.getpid()}.jsonl")
    _fh = open(path, "a", encoding="utf-8")

    patched = []
    for name, attr in list(vars(KiCadBoardAdapter).items()):
        if name.startswith("_") or name in _SKIP:
            continue
        if not isinstance(attr, types.FunctionType):
            continue  # leaves properties and classmethods alone
        if getattr(attr, "_board_timing", False):
            continue
        setattr(KiCadBoardAdapter, name, _wrap(name, attr))
        patched.append(name)

    _installed = True
    _record("__install__", 0.0, True, None, len(patched), 0)
    print(f"[board_call_timing] patched {len(patched)} methods -> {path}")
    return path
