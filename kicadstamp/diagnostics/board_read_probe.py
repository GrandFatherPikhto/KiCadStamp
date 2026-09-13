"""Records who reads ``BoardConnection.board``, from which thread and call site.

Input        — nothing; the GUI's Settings > Diagnostics switch calls start()/stop()
               (see gui/docks/configurator.py), and run_gui_with_read_probe calls
               enable() before the GUI starts.
Expected     — a JSON Lines file, one object per board read, under <repo>/diagnostics/.
Live KiCad   — No (the hook is passive; a running GUI with a live board is what produces reads).
Run          — not run directly; see run_gui_with_read_probe.py and report_board_reads.py.

Installed from OUTSIDE the production code: the ``board`` getter in gui/connection.py already
carries the optional hook (``board_read_probe``, Э3 of plan_2026_09_13_board_access_door) and
only tests it — nothing in production ever assigns it, so the getter stays a no-op until this
module turns it on. That is also why this script needs no monkey patch.

TWO WAYS TO RUN IT (2026-09-13, plan_2026_09_13_diagnostics_switch Э1/Э2), the same pair
board_call_timing.py has:

  * the external launcher — `python -m kicadstamp.diagnostics.run_gui_with_read_probe` —
    enable() before the GUI starts, so the log covers the session from the first second;
  * the GUI switch — Settings > Diagnostics, persisted in gui_state.json and wired through
    start()/stop(). Turn it on the moment something looks odd, instead of restarting the GUI
    and scaring the oddity away.

The contract is deliberately the same as board_call_timing's: an idempotent start() that opens
the log and logs ONE line, an idempotent stop() that closes it, logs ONE line and returns the
number of reads recorded, a size cap (MAX_LOG_BYTES, one session) that stops the recording by
itself with a Log line naming the reason, and — at startup with the switch found enabled — the
reminder wording instead of the plain start line, so a forgotten switch is never silent.
Per-read logging stays impossible: the Log gets two lines per session, the data goes to JSONL.

Deliberately the cheapest possible recorder: the current thread's name and ONE stack frame (the
caller of the getter), never a full stack walk — the point is to survive a live session with
tens of thousands of reads. Board DATA is never recorded, only the thread and the call site, so
a log is safe to attach to a bug report.

Why it exists: "which thread reads the board, and from where" could not be answered from the
code for the follow-up enforcement task (a warning in the log / a failure in tests / refusing to
hand the board over). This counter produces the list of ACTUAL offenders, so that punishment is
chosen from data rather than intuition. A read from the UI thread is the thing being hunted, and
report_board_reads.py makes it stand out.
"""
import json
import logging
import os
import sys
import threading
import time
from typing import Optional

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

# Size cap of ONE recording session — the same guard (and the same reasoning) as
# board_call_timing.MAX_LOG_BYTES: a switch left on must not silently fill the
# disk. Kept as its own constant so the two recorders stay independent.
MAX_LOG_BYTES = 50 * 1024 * 1024

_lock = threading.Lock()
_fh = None
_path: Optional[str] = None
_recording = False
# Reads written by the CURRENT session — what stop() returns.
_written = 0
# Bytes written by the CURRENT session (the cap bounds one session, not the
# file's total size; the file is appended to, `a`).
_bytes_written = 0
_max_bytes = MAX_LOG_BYTES
# gui/connection.py's OWN reads (`self.board` in refresh/disconnect/...) are not
# consumers of the door — recording them would show up as UI-thread noise in the
# report. Set in start() to the real path of gui.connection.
_skip_file = None


def default_log_dir() -> str:
    """<repo root>/diagnostics — gitignored, and where the report tool looks.
    Created on demand: a fresh clone does not carry a gitignored directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "diagnostics")
    os.makedirs(out, exist_ok=True)
    return out


def is_recording() -> bool:
    """True while a session is open — the GUI switch asks this before calling
    start(), which keeps a repeated Apply from opening a second file (and from
    printing a second Log line)."""
    return _recording


def _record() -> None:
    """One board read: the thread's name and the CALLER of the getter.

    Called by the ``board`` property getter itself, so the frame layout is fixed:
    frame 0 is this function, frame 1 is the getter, frame 2 is whoever read
    ``connection.board``. One frame is enough — a full stack is expensive and the
    immediate caller is the answer being looked for."""
    global _bytes_written, _written
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
    line = json.dumps(row, ensure_ascii=False) + "\n"
    over = False
    with _lock:
        if _fh is None:
            return
        _fh.write(line)
        _fh.flush()
        _bytes_written += len(line)
        _written += 1
        over = _bytes_written >= _max_bytes
    if over:
        _stop_at_cap()


def _stop_at_cap() -> None:
    """Stop the recording because the size cap was reached — one Log line with
    the reason and the path, and the hook removed so reads stop being touched
    at all."""
    global _fh, _recording
    with _lock:
        if _fh is None:
            return
        path = _path
        _fh.close()
        _fh = None
        _recording = False
        written = _written
    _detach()
    logger.warning(
        _("Diagnostics: recording board reads stopped at the {limit} MB size "
          "cap, {count} reads → {path}").format(
              limit=_max_bytes // (1024 * 1024), count=written, path=path))


def start(path: Optional[str] = None, reminder: bool = False) -> str:
    """Attach the probe to gui.connection, open the log and return its path.
    Idempotent: while a session is open this only returns its path, so a
    repeated Apply neither reopens the file nor logs a second line.

    `reminder=True` is the STARTUP wording (the switch was found enabled in
    gui_state.json) — same single line, plus where to turn the switch off."""
    global _fh, _path, _recording, _written, _bytes_written, _skip_file
    if _recording:
        return _path or ""
    import gui.connection as connection

    if path is None:
        path = os.path.join(default_log_dir(), f"board_reads_{os.getpid()}.jsonl")
    with _lock:
        _fh = open(path, "a", encoding="utf-8")
        _path = path
        _written = 0
        _bytes_written = 0
        _recording = True
    _skip_file = os.path.abspath(connection.__file__)
    connection.board_read_probe = _record
    if reminder:
        logger.info(
            _("Diagnostics: recording board reads → {path} (the switch was left "
              "ON in Settings → Diagnostics; turn it off when the data is "
              "collected)").format(path=path))
    else:
        logger.info(_("Diagnostics: recording board reads → {path}").format(path=path))
    return path


def stop() -> int:
    """Remove the probe, close the log and return how many reads were recorded.
    Idempotent: with no session open nothing is closed and nothing is logged."""
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
    _detach()
    logger.info(_("Diagnostics: recording board reads stopped, {count} reads → "
                  "{path}").format(count=written, path=path))
    return written


def _detach() -> None:
    """Unhook the probe from gui.connection (the getter then pays one `is None`
    test per read again)."""
    import gui.connection as connection

    connection.board_read_probe = None


def enable(path: Optional[str] = None) -> str:
    """The external launcher's entry point (run_gui_with_read_probe.py):
    identical to start(), kept because "record from the first second" is what
    that script means. The GUI switch uses start()/stop() (Э1)."""
    return start(path)


def disable() -> int:
    """Alias of stop() — the name the existing launcher/scripts and tests use."""
    return stop()
