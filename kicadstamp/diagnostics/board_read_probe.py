"""Records who reads ``BoardConnection.board``, from which thread and call site.

Input        — nothing; enable() installs a hook into gui.connection (called by
               run_gui_with_read_probe before the GUI starts).
Expected     — a JSON Lines file, one object per board read, under <repo>/diagnostics/.
Live KiCad   — No (the hook is passive; a running GUI with a live board is what produces reads).
Run          — not run directly; see run_gui_with_read_probe.py and report_board_reads.py.

Installed from OUTSIDE the production code: the ``board`` getter in gui/connection.py already
carries the optional hook (``board_read_probe``, Э3 of plan_2026_09_13_board_access_door) and
only tests it — nothing in production ever assigns it, so the getter stays a no-op until this
module turns it on. That is also why this script needs no monkey patch.

Deliberately the cheapest possible recorder: the current thread's name and ONE stack frame (the
caller of the getter), never a full stack walk — the point is to survive a live session with
tens of thousands of reads. Board DATA is never recorded, only the thread and the call site, so
a log is safe to attach to a bug report.

Why it exists: "which thread reads the board, and from where" could not be answered from the
code for the follow-up enforcement task (a warning in the log / a failure in tests / refusing to
hand the board over). This counter produces the list of ACTUAL offenders, so that punishment is
chosen from data rather than intuition. A read from the UI thread (MainThread) is the thing
being hunted, and report_board_reads.py makes it stand out.
"""
import json
import os
import sys
import threading
import time
from typing import Optional

_lock = threading.Lock()
_fh = None
# gui/connection.py's OWN reads (`self.board` in refresh/disconnect/...) are not
# consumers of the door — recording them would show up as UI-thread noise in the
# report. Set in enable() to the real path of gui.connection.
_skip_file = None


def default_log_dir() -> str:
    """<repo root>/diagnostics — gitignored, and where the report tool looks.
    Created on demand: a fresh clone does not carry a gitignored directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "diagnostics")
    os.makedirs(out, exist_ok=True)
    return out


def _record() -> None:
    """One board read: the thread's name and the CALLER of the getter.

    Called by the ``board`` property getter itself, so the frame layout is fixed:
    frame 0 is this function, frame 1 is the getter, frame 2 is whoever read
    ``connection.board``. One frame is enough — a full stack is expensive and the
    immediate caller is the answer being looked for."""
    try:
        frame = sys._getframe(2)
    except ValueError:  # no caller frame (should not happen through the getter)
        frame = None
    if frame is not None and _skip_file is not None and \
            os.path.abspath(frame.f_code.co_filename) == _skip_file:
        return  # an internal read by the connection itself, not a consumer
    site = (f"{frame.f_code.co_filename}:{frame.f_lineno}" if frame is not None
            else "<unknown>")
    row = {
        "ts": round(time.time(), 3),
        "thread": threading.current_thread().name,
        "site": site,
    }
    line = json.dumps(row, ensure_ascii=False)
    with _lock:
        if _fh is not None:
            _fh.write(line + "\n")
            _fh.flush()


def enable(path: Optional[str] = None) -> str:
    """Installs the probe into gui.connection and returns the log path.
    Idempotent enough for a single diagnostic run (a second call reopens/keeps
    writing); there is no production path that calls it twice."""
    global _fh, _skip_file
    import gui.connection as connection

    if path is None:
        path = os.path.join(default_log_dir(), f"board_reads_{os.getpid()}.jsonl")
    _fh = open(path, "a", encoding="utf-8")
    _skip_file = os.path.abspath(connection.__file__)
    connection.board_read_probe = _record
    print(f"[board_read_probe] recording board reads -> {path}")
    return path


def disable() -> None:
    """Removes the probe and closes the log (idempotent)."""
    global _fh
    import gui.connection as connection

    connection.board_read_probe = None
    with _lock:
        if _fh is not None:
            _fh.close()
            _fh = None
