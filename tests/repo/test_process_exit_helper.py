# tests/repo/test_process_exit_helper.py
"""Ф3.8 of plan_2026_09_27_repo_and_tests_transformation: the property of
`tests/fakes/process_exit.py`.

The helper answers ONE question for two GUI watchdogs — did this subprocess DIE,
or exit cleanly? A CONTROL (the same program with the protection switched off)
must die abnormally, and `died_of_a_crash` is what decides that, so a too-loose
answer makes both watchdogs prove nothing.

Measured 01.10.2026 (Ф3.7 acceptance): replacing the POSIX body with `return True`
killed nothing at all. That is exactly what the helper must not do: with a body
frozen to "everything crashed", a plain `pytest` failure (1) reads as a crash and
the controls pass while proving nothing (rule 33).

Both platform branches are held on ONE machine. The helper reads `sys.platform`
itself, so that is what is patched — the Windows branch is therefore covered on
Linux too, with the constants measured on Windows in Ф3.1/Ф3.6 (not invented
here).

Rule 35: the cells are the TABLE of the helper — platform × returncode → verdict.
"""
import sys

import pytest

from tests.fakes.process_exit import died_of_a_crash

# platform, returncode, crashed?
#   POSIX  — subprocess reports a signal death as a NEGATIVE code; through a shell
#            it is 128 + SIGABRT = 134. A clean exit is 0, an ordinary pytest
#            failure is 1, pytest's usage error is 2.
#   win32  — there is no signal number. The loader reports an NTSTATUS (measured
#            0xC0000409 STATUS_STACK_BUFFER_OVERRUN on the failing control) and
#            abort() in the CRT exits with 3.
_CASES = [
    ("linux", -6, True),          # SIGABRT, as subprocess reports it
    ("linux", 134, True),         # 128 + SIGABRT, through a shell
    ("linux", 0, False),          # clean exit
    ("linux", 1, False),          # an ordinary test failure — NOT a crash
    ("linux", 2, False),          # pytest's usage error
    ("win32", 0xC0000409, True),  # STATUS_STACK_BUFFER_OVERRUN
    ("win32", 3, True),           # abort() in the CRT
    ("win32", 0, False),
    ("win32", 1, False),
    ("win32", 2, False),
]


@pytest.mark.parametrize("platform, returncode, crashed", _CASES)
def test_died_of_a_crash_splits_the_death_from_a_clean_exit(
        monkeypatch, platform, returncode, crashed):
    """The verdict for one cell of the table above. `is` on purpose: the helper
    answers a yes/no question, so a truthy non-bool would be its own bug."""
    monkeypatch.setattr(sys, "platform", platform)
    assert died_of_a_crash(returncode) is crashed, (
        f"on {platform}, returncode {returncode} ({returncode & 0xFFFFFFFF:#010x}) "
        f"must read as {'a crash' if crashed else 'NOT a crash'}")


def test_the_table_holds_both_branches_of_each_platform():
    """Rule 38: a table that quietly lost a platform or a half of its truth would
    leave a branch unheld and still look green — the verdict of the remaining
    rows would be vacuous."""
    assert {p for p, _, _ in _CASES} == {"linux", "win32"}, (
        "both platform branches must be held, or one of them is only pinned by "
        "the machine that happens to run the suite")
    assert {c for _, _, c in _CASES} == {True, False}
    for platform in ("linux", "win32"):
        codes = {rc for p, rc, _ in _CASES if p == platform}
        assert {0, 1} <= codes, (
            f"the {platform} rows must hold BOTH a clean exit and a plain "
            f"failure: got {sorted(codes)}")
