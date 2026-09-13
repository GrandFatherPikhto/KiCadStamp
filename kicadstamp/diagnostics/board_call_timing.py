"""Times every KiCadBoardAdapter call, and every kipy round trip inside it.

Input        — nothing; the GUI's Settings > Diagnostics switch calls start()/stop()
               (see gui/docks/configurator.py), and run_gui_with_timing calls install()
               before the GUI starts.
Expected     — a JSON Lines file, one object per call, under <repo>/diagnostics/.
Live KiCad   — Partially (the GUI runs with or without it; only a live board produces calls).
Run          — not run directly; see run_gui_with_timing.py and report_board_timing.py.

Installed from OUTSIDE the production code (monkey patch, see install_patch()) so nothing in
kicadstamp/ or gui/ has to be edited or reverted, and so it can never collide with work in
flight in a shared checkout.

TWO WAYS TO RUN IT (2026-09-13, plan_2026_09_13_diagnostics_switch Э1/Э2):

  * the external launcher — `python -m kicadstamp.diagnostics.run_gui_with_timing` — install()
    patches and starts recording before the GUI is imported, so the log covers the session
    from the very first second. Use it for a clean measurement;
  * the GUI switch — Settings > Diagnostics, page switches persisted in gui_state.json, wired
    through start()/stop(). Use it when something looks odd RIGHT NOW: turn recording on,
    keep working, turn it off. Restarting the GUI would scare the oddity away, which is the
    whole reason the switch exists.

RECORDING IS A FLAG, THE PATCH IS PERMANENT (Э1). The monkey patch is installed once and never
removed — removing it from a live class that two threads are calling through is exactly the
race class this project spent weeks fixing. While recording is OFF the wrapper costs one
boolean test and calls the original; nothing is measured and no stack frame is walked.

CALL SITE — UI THREAD ONLY (Э3). The frame of the caller is recorded only when an injected
predicate says the current thread is the GUI thread (set_ui_thread_predicate), because that is
the thread whose board calls can freeze the window. That is ~0.4% of the recorded calls, so the
cost is invisible, while walking the stack for all 81k calls would not be. The predicate is
injected from OUTSIDE — this module never imports Qt: the GUI installs one, the CLI and the MCP
server install nothing (the default is None = no check at all, so the caller's site is simply
not recorded there). A thread NAME would not work: in the MCP server process and in the CLI
everything legitimately runs on MainThread.

Writes JSON Lines (one self-contained object per call, appended and flushed immediately)
rather than one JSON document, so a crash mid-session still leaves every completed line
readable.

Board DATA is never recorded — only call names, durations, and item counts.
"""
import functools
import json
import logging
import os
import sys
import threading
import time
import types
from typing import Any, Callable, Optional

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

# Not board round trips: a context manager whose cost is in __enter__/__exit__
# (timing the factory call measures nothing), and teardown.
_SKIP = {"temporarily_ignore_selection", "close"}

# Size cap of ONE recording session (Э2). 81k calls are ~11 MB in 6.5 minutes,
# i.e. of the order of a hundred megabytes an hour: a switch left on over a
# weekend must not silently fill the disk. On reaching the cap the recording
# stops itself and says so in the Log, with the reason and the path.
MAX_LOG_BYTES = 50 * 1024 * 1024

_lock = threading.Lock()
_fh = None
_path: Optional[str] = None
# The recording flag the wrapper tests — see the module docstring. False by
# default: importing this module costs nothing until start() is called.
_recording = False
# Calls written by the CURRENT session (the __install__ header row is not one of
# them) — what stop() returns and what the Log line reports.
_written = 0
# Bytes written by the CURRENT session. Deliberately not the file's total size:
# the cap bounds one recording session, so start/stop cycles in one GUI session
# each get their own budget (the file is appended to, `a`).
_bytes_written = 0
_max_bytes = MAX_LOG_BYTES
# How many adapter methods install_patch() wrapped — written into each session's
# header row (the report skips that row).
_patched_count = 0

# Injected UI-thread predicate (Э3) — None means "do not look at the caller at
# all". The GUI sets its own when it turns recording on; CLI and MCP leave it
# None and never pay for a stack walk.
_ui_thread: Optional[Callable[[], bool]] = None

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


def is_recording() -> bool:
    """True while a recording session is open — the GUI switch asks this before
    calling start(), which is what keeps a repeated Apply from opening a second
    session (and printing a second Log line, Э2/Э5.4)."""
    return _recording


def set_ui_thread_predicate(predicate: Optional[Callable[[], bool]]) -> None:
    """Install (or clear, with None) the "is this the UI thread" predicate Э3.

    Called by the GUI when recording is turned on/off. Nothing in this module
    imports Qt, so the answer has to come from outside; None — the default —
    means the caller's site is never recorded, which is what CLI and MCP want."""
    global _ui_thread
    _ui_thread = predicate


def _count(value: Any) -> Optional[int]:
    """Item count of a list-ish argument or result, or None. Deliberately
    does not touch the objects themselves."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return None


def _call_site() -> Optional[str]:
    """`file:line` of the adapter method's caller, for UI-thread calls only.

    Frame layout is fixed: 0 is this function, 1 is the wrapper, 2 is whoever
    called the patched method. ONE frame — a full stack walk per call is
    exactly what Э3 refuses to pay for."""
    try:
        frame = sys._getframe(2)
    except ValueError:  # no caller frame (should not happen through the wrapper)
        return None
    return f"{frame.f_code.co_filename}:{frame.f_lineno}"


def _write_row(row: dict, count: bool = True) -> bool:
    """Append one row and return False once the size cap has been reached.
    The caller must already hold no lock; a stopped/closed file is not an
    error here (stop() may race a call that is still finishing)."""
    global _bytes_written, _written
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        if _fh is None:
            return True
        _fh.write(line)
        _fh.flush()
        _bytes_written += len(line)
        if count:
            _written += 1
        return _bytes_written < _max_bytes


def _record(method: str, dur_ms: float, ok: bool, in_n, out_n, depth: int = 0,
            err: str = "", site: Optional[str] = None) -> None:
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
    if site is not None:
        # Two keys, one fact: this call happened on the UI thread AND here is
        # where it was made from. `ui` is what the report filters on (a site
        # string alone would be a fragile marker).
        row["ui"] = True
        row["site"] = site
    if not _write_row(row):
        _stop_at_cap()


def _stop_at_cap() -> None:
    """Stop the recording because the size cap was reached (Э2). One Log line
    with the reason and the path — never a silent stop, and never a growing
    file on disk."""
    global _fh, _recording
    with _lock:
        if _fh is None:
            return
        path = _fh.name
        _fh.close()
        _fh = None
        _recording = False
        written = _written
    logger.warning(
        _("Diagnostics: recording board calls stopped at the {limit} MB size "
          "cap, {count} calls → {path}").format(
              limit=_max_bytes // (1024 * 1024), count=written, path=path))


def _wrap(name: str, fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        # The flag test is the WHOLE cost of a disabled recorder: no timing, no
        # stack walk, no item counting (Э1, guarded by tests/test_diagnostics_switch.py).
        if not _recording:
            return fn(self, *args, **kwargs)
        predicate = _ui_thread
        ui = bool(predicate is not None and predicate())
        site = _call_site() if ui else None
        in_n = next((_count(a) for a in args if _count(a) is not None), None)
        depth = getattr(_depth, "n", 0)
        _depth.n = depth + 1
        t0 = time.perf_counter()
        try:
            result = fn(self, *args, **kwargs)
        except BaseException as exc:
            _depth.n = depth
            _record(name, (time.perf_counter() - t0) * 1000.0, False,
                    in_n, None, depth, f"{type(exc).__name__}: {exc}", site)
            raise
        _depth.n = depth
        _record(name, (time.perf_counter() - t0) * 1000.0, True, in_n,
                _count(result), depth, "", site)
        return result

    # Own marker: functools.wraps sets __wrapped__, but so does @contextmanager
    # and friends already present in the adapter, so that is not proof of ours.
    wrapper._board_timing = True
    return wrapper


def install_patch(target=None) -> int:
    """Patches every plain method of KiCadBoardAdapter. Idempotent, and the
    patch is NEVER removed (Э1): a second call is a no-op. Returns the number
    of methods patched by this call (0 when the class is patched already — the
    tally of the FIRST call is what a recording session writes into its header
    row).

    Idempotency is kept as a marker ON THE CLASS (`_board_timing_patched`), not
    as a module flag, because that also makes it true of `target` — a parameter
    that exists so tests can patch a stand-in class: the live adapter stays
    patched for the rest of the process, and a unit test must not depend on
    whether an earlier test already did that."""
    global _patched_count
    is_live = target is None
    if is_live:
        from kicadstamp.kicad.adapter import KiCadBoardAdapter
        target = KiCadBoardAdapter
    if getattr(target, "_board_timing_patched", False):
        return 0

    patched = []
    for name, attr in list(vars(target).items()):
        if name.startswith("_") or name in _SKIP:
            continue
        if not isinstance(attr, types.FunctionType):
            continue  # leaves properties and classmethods alone
        if getattr(attr, "_board_timing", False):
            continue
        setattr(target, name, _wrap(name, attr))
        patched.append(name)

    target._board_timing_patched = True
    if is_live:
        _patched_count = len(patched)
    print(f"[board_call_timing] patched {len(patched)} methods of {target.__name__}")
    return len(patched)


def start(path: Optional[str] = None, reminder: bool = False) -> str:
    """Open a recording session and return the log path. Idempotent: while a
    session is open this only returns its path — a repeated Apply must not open
    a second file, and (Э2/Э5.4) must not print a second Log line.

    `reminder=True` is the STARTUP wording (the switch was found enabled in
    gui_state.json): same single Log line, but it also says the switch was left
    on and where to turn it off — a forgotten switch must not be silent. Still
    ONE line, because the Log contract is exactly two lines per session."""
    global _fh, _path, _recording, _written, _bytes_written
    if _recording:
        return _path or ""
    install_patch()
    if path is None:
        path = os.path.join(default_log_dir(), f"board_timing_{os.getpid()}.jsonl")
    with _lock:
        _fh = open(path, "a", encoding="utf-8")
        _path = path
        _written = 0
        _bytes_written = 0
        _recording = True
    # Header row: how many methods the patch wrapped (report skips it).
    _write_row({"ts": round(time.time(), 3), "method": "__install__", "ms": 0.0,
                "ok": True, "thread": threading.current_thread().name,
                "in_n": None, "out_n": _patched_count, "depth": 0},
               count=False)
    if reminder:
        logger.info(
            _("Diagnostics: recording board calls → {path} (the switch was left "
              "ON in Settings → Diagnostics; turn it off when the data is "
              "collected)").format(path=path))
    else:
        logger.info(_("Diagnostics: recording board calls → {path}").format(path=path))
    return path


def stop() -> int:
    """Close the session and return how many calls it recorded. Idempotent:
    with no session open nothing is closed and nothing is logged (so the Log
    carries exactly one stop line per session)."""
    global _fh, _recording
    if not _recording:
        return 0
    with _lock:
        path = _path
        if _fh is not None:
            _fh.close()
        _fh = None
        _recording = False
        written = _written
    logger.info(_("Diagnostics: recording board calls stopped, {count} calls → "
                  "{path}").format(count=written, path=path))
    return written


def install(path: Optional[str] = None) -> str:
    """Patch AND start recording — the entry point run_gui_with_timing.py uses,
    kept because it is what "record my session from the first second" means.
    The GUI switch uses start()/stop() instead (Э1)."""
    return start(path)
