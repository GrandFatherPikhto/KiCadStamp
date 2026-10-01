# tests/repo/test_coarse_mtime_option.py
"""Ф3.7 of plan_2026_09_27_repo_and_tests_transformation: `--coarse-mtime`.

The option forces, on any filesystem, the one-tick behaviour Windows gives for
free — measured 01.10.2026 (Ф3.6): `st_mtime_ns` granularity there is ~0.5 ms and
344/500 (C:) / 409/500 (repo disk D:) back-to-back write pairs landed on one
tick. Under it, every rig that writes a file TWICE must leave a LATER write
behind itself (`tests/fakes/write_later.py`), or the second read answers from the
`(path, mtime_ns)` cache: that class failed ELEVEN cells on Linux on `f3d603a`,
the Actions #656 failure among them, and it is the whole reason the CI Linux leg
now runs the suite a second time with this option.

Both cells below run the SAME inner cell in a subprocess — a plain test that
writes one file twice and asserts the stamp advanced — and differ only in whether
`--coarse-mtime` is passed. The inner run loads THIS suite's conftest as a plugin
(`-p tests.conftest`), so the option under test is the shipped one, not a copy of
it (rule 38: a copy would be a rig measuring itself, and it would keep passing
after the real option was broken).

The inner cell sleeps 20 ms before its second write ON PURPOSE: without the
option the stamp must advance on a real filesystem, and two writes back to back
can legitimately share one tick. The sleep makes that half deterministic. The
frozen half is equal BY CONSTRUCTION, sleep or no sleep, so the pair stays sharp
and the sleep does not weaken it.
"""
import os
import subprocess
import sys

from tests.paths import REPO_ROOT

_INNER_TEST = '''
import os
import time
from pathlib import Path


def test_the_second_write_to_an_existing_file(tmp_path):
    target = tmp_path / "root.sexp"
    target.write_text("first", encoding="utf-8")
    before = os.stat(target).st_mtime_ns
    time.sleep(0.02)
    target.write_text("second", encoding="utf-8")
    after = os.stat(target).st_mtime_ns
    print(f"MTIMES {before} {after}", flush=True)
    assert target.read_text(encoding="utf-8") == "second", "the bytes must land"
    assert after > before, (
        f"the second write did not advance mtime_ns: {before} -> {after}")
'''


def _inner_env():
    """PYTHONPATH must let the inner process import `tests.conftest` — the real
    option, from THIS tree. Inherited entries are absolutised for the same reason
    `tests/gui/test_slot_exception_hook._inner_env` does it: the inner run has a
    different cwd, where a relative entry resolves against nothing."""
    env = dict(os.environ)
    entries = [str(REPO_ROOT)]
    entries += [os.path.abspath(entry) for entry
                in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(entries))
    return env


def _run_inner(where, *extra):
    """The inner cell in its own directory, with this suite's conftest loaded as
    a plugin (`-p tests.conftest`): `--coarse-mtime` is then registered and
    installed by the shipped code, exactly as CI passes it."""
    where.mkdir(parents=True)
    (where / "test_the_second_write.py").write_text(_INNER_TEST, encoding="utf-8")
    # `-s`: the inner cell's `MTIMES` line is the measurement, and pytest's
    # capture would swallow it (the run's exit code alone cannot tell a frozen
    # stamp from an unfrozen one — both outcomes are asserted on below).
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest",
         "-p", "no:cacheprovider", "-q", "--no-header", "-s",
         "test_the_second_write.py", *extra],
        cwd=where, env=_inner_env(), capture_output=True, text=True, timeout=300)


def _stamps(proc):
    """The inner cell's measured pair, plus the whole output for the messages."""
    output = proc.stdout + proc.stderr
    line = next(ln for ln in output.splitlines() if ln.startswith("MTIMES "))
    _, before, after = line.split()
    return int(before), int(after), output


def test_without_the_option_a_later_write_advances_the_stamp(tmp_path):
    """The negative side of the switch: the ordinary run must NOT be frozen, or
    the reference run would stop measuring the natural filesystem.

    A blind run is failed here rather than trusted (rule 38): the inner cell has
    to be reported as `1 passed`, not merely exited 0 — a collection error also
    exits non-zero."""
    proc = _run_inner(tmp_path / "natural")
    before, after, output = _stamps(proc)

    assert "1 passed" in output, f"the inner cell did not run\n{output}"
    assert proc.returncode == 0, (
        f"a later write must advance the stamp when the option is off\n{output}")
    assert after > before, (
        f"the second write did not advance mtime_ns: {before} -> {after}\n{output}")


def test_with_the_option_a_write_to_an_existing_file_keeps_the_stamp(tmp_path):
    """The positive side: under `--coarse-mtime` the same inner cell must go RED
    on its OWN assertion — the stamp it re-reads is the one from before the
    write.

    `1 failed` is asserted, not just a non-zero exit: that is what separates "the
    option broke the cell's assertion" from "the option was not recognised" /
    "the run collected nothing". This is also the cell that goes red if the
    option is ever registered with `default=True`: then the frozen half would be
    running in the reference run as well."""
    proc = _run_inner(tmp_path / "frozen", "--coarse-mtime")
    before, after, output = _stamps(proc)

    assert "1 failed" in output, (
        f"the option must break the inner cell's own assertion, not collection "
        f"or option parsing\n{output}")
    assert proc.returncode != 0, output
    assert after == before, (
        f"under the option the stamp must not move, but the second write moved "
        f"it: {before} -> {after}\n{output}")
