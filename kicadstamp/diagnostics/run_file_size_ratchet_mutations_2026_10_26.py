# kicadstamp/diagnostics/run_file_size_ratchet_mutations_2026_10_26.py
"""Mutation rows for the size ratchet — `tests/repo/test_file_size_ceilings.py` (rule 45).

The guard is a LOCK in both directions (Denis, 26.10.2026): a file longer than its number is
red, and a file shorter than its number is red too ("опусти число до N тем же коммитом").
Three rows, one per promise, each removing exactly ONE promise so the red cell names it:

  Р1 one line MORE in a giant, no new code                 (+1 in gui/docks/trees_dock.py)
  Р2 one line LESS in a giant, the table untouched         (−1 in gui/docks/trees_dock.py)
  Р3 a NEW 801-line file under a scanned root              (gui/_size_probe_801.py)
  Р4 cosmetic control (a comment's text changes, the count does not)

Why a new file and not a row in another harness: this guard reads the PRODUCT TREE, not a
window; it needs no Qt, and its mutation is a line count. One harness per step keeps each run
short and its docstring names exactly which promise is pinned where.

Safeguards as everywhere in this package: a template matching more than once is refused
(rule 38), a row that reddens NOTHING is a MISS, the control must survive, every touched file
is restored in `finally` with the restore verified byte for byte, and the probe file is deleted
in `finally` with its absence verified (nothing may be left for the next run to inherit).

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_file_size_ratchet_mutations_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/repo/test_file_size_ceilings.py"

TREES_DOCK = "gui/docks/trees_dock.py"
PROBE = "gui/_size_probe_801.py"

#: The first two lines of the dock: a header comment nobody imports and a docstring opening.
HEAD = '# gui/docks/trees_dock.py\n"""TreesDock'
HEADER = "# gui/docks/trees_dock.py"

CELL_OVER = "test_no_file_is_over_its_ceiling[gui/docks/trees_dock.py]"
CELL_UNDER = "test_no_file_is_under_its_ceiling_either[gui/docks/trees_dock.py]"
CELL_UNLISTED = "test_every_file_over_800_lines_is_in_the_table"

MUTATIONS = [
    # Growth without a word: the number in the table stays, the file does not. The cell that
    # names the file must be the one that reddens.
    ("Р1 one line more in a giant", TREES_DOCK,
     HEAD,
     '# gui/docks/trees_dock.py\n'
     '# MUTATION: one more line, not a single new statement\n'
     '"""TreesDock',
     [CELL_OVER]),

    # Shrinking without lowering the number leaves slack, and the next growth slides through
    # it — the half of the lock that keeps the table honest.
    ("Р2 one line less, the table untouched", TREES_DOCK,
     HEAD,
     '"""TreesDock',
     [CELL_UNDER]),

    ("Р4 cosmetic control (the count must not change)", TREES_DOCK,
     HEADER,
     HEADER + "  # MUTATION: cosmetic",
     "survive"),
]

#: Р3 creates a file instead of editing one, so it cannot be a text replace: 801 lines under a
#: scanned root, exactly one over the rule's threshold.
PROBE_ROWS = 801


def _sweep_pycache() -> None:
    for directory in ROOT.rglob("__pycache__"):
        shutil.rmtree(directory, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 600 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def _verdict(name: str, failed: list, expect) -> int:
    """Print one row's verdict; return 1 for a miss, 0 for as-expected."""
    if expect == "survive":
        if failed:
            print(f"[MISS] {name}: a cosmetic change reddened {failed}")
            return 1
        print(f"[OK] {name}: survived, as it must")
        return 0
    if not failed:
        print(f"[MISS] {name}: NO failed test — the mutation kills nothing")
        return 1
    missing = [e for e in expect if not any(e in f for f in failed)]
    if missing:
        print(f"[MISS] {name}: expected these to redden, they did not: {missing}\n"
              f"  actually failed: {failed}")
        return 1
    print(f"[KILL] {name}: {len(failed)} red — {failed}")
    return 0


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            _code, out = _run(guards)
            misses += _verdict(name, _failed_ids(out), expect)
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"

    # Р3 — a NEW giant. Written and removed here, because a text replace cannot express it.
    probe = ROOT / PROBE
    assert not probe.exists(), f"{PROBE} is already there — refusing to overwrite"
    try:
        probe.write_text("\n".join(["# size probe"] * PROBE_ROWS) + "\n",
                         encoding="utf-8")
        assert len(probe.read_text(encoding="utf-8").splitlines()) == PROBE_ROWS
        _code, out = _run(guards)
        misses += _verdict("Р3 a new 801-line file", _failed_ids(out), [CELL_UNLISTED])
    finally:
        probe.unlink(missing_ok=True)
        assert not probe.exists(), f"{PROBE} was not removed — the run would leak"

    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) + 1 - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
