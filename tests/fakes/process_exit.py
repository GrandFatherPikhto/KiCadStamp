# tests/fakes/process_exit.py
"""One answer to "did this process DIE, or exit cleanly?" — for both platforms.

Two GUI watchdogs run a tiny program in a subprocess and assert that the CONTROL —
the same program with the protection switched off — dies from a crash rather than
ending in a clean run (tests/gui/test_slot_exception_hook.py,
tests/gui/test_qt_slot_exception_capture.py). On POSIX that death is a SIGNAL
(PyQt6's qFatal raises SIGABRT): subprocess reports a negative returncode, or 134
through a shell. On Windows there is no signal number — the loader reports an
NTSTATUS: measured 0xC0000409 (STATUS_STACK_BUFFER_OVERRUN) on the failing
control, and abort() in the CRT exits with 3. A single `returncode < 0 or == 134`
therefore described POSIX only and failed on Windows (Ф3.1 of
plan_2026_09_27_repo_and_tests_transformation).

The helper is shared so the two files cannot drift into two different definitions
of "crashed", and so the Windows constants live in one commented place.
"""
import sys


def died_of_a_crash(returncode: int) -> bool:
    """True when `returncode` is the abnormal death of a crashed process, not a
    clean exit and not an ordinary test failure (which is 1)."""
    if sys.platform == "win32":
        # An NTSTATUS (0xC0000000 and up) is the loader's crash report; abort()
        # in the CRT is the plain 3. Anything else (0, 1, 2) is a normal exit.
        return returncode >= 0xC0000000 or returncode == 3
    # POSIX: died of a signal (negative) or the shell's 128 + SIGABRT (= 134).
    return returncode < 0 or returncode == 134
