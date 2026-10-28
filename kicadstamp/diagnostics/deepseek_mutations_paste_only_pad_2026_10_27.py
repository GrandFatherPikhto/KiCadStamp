# kicadstamp/diagnostics/deepseek_mutations_paste_only_pad_2026_10_27.py
"""Mutation rows for plan_2026_10_27_paste_only_pad_is_not_copper.

The promise (Denis, 27.10): a pad whose padstack reported its layers and none of
them is copper has NO copper anywhere — it is not a through pad, not an island
anchor and not a foreign pad. One row per half of that sentence:

  M1  a paste-only stack answers None again         -> the window is THROUGH copper
                                                       and the In2 bus under D14 is
                                                       taken again (the live defect)
  M2  `cell_pad_areas` stops skipping such a pad    -> it becomes an island anchor
                                                       again. A TRACK still does not
                                                       touch it (`_touches_pad` asks
                                                       the layers, and `()` has
                                                       none) — a VIA does, because a
                                                       via is through and the layer
                                                       is never asked: the via turns
                                                       into a one-pad dead end. The
                                                       skip is kept because «no
                                                       copper» must not depend on
                                                       who asks.
  M3  `pad_areas` stops skipping such a pad         -> it becomes a FOREIGN pad again
  M4  the copper cut answers «no copper» always     -> a REAL F.Cu pad loses its
                                                       copper (the mirror defect)
  K1  cosmetic control (a comment on a def line)    -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive, and
every touched file is restored in `finally` with the restore verified byte for byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_paste_only_pad_2026_10_27`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/explode/test_paste_only_pad_is_not_copper.py"
BOARD = "kicadstamp/domain/board.py"
CONNECTIVITY = "kicadstamp/explode_connectivity.py"

CELL_STACK = "test_a_paste_only_stack_has_no_copper_layers"
CELL_STACK_COPPER = "test_a_copper_stack_keeps_its_copper_layer_only"
CELL_UNKNOWN = "test_an_unreported_stack_stays_unknown"
CELL_PADS = "test_paste_only_pads_are_neither_cell_nor_foreign_pads"
CELL_BUS = "test_the_in2_bus_under_paste_windows_is_not_taken"
CELL_VIA = "test_a_via_in_the_paste_window_does_not_touch_the_pad"
CELL_ORDINARY = "test_copper_between_two_copper_pads_is_still_taken"

#: The one line that decides between "no copper" and "unknown".
ANSWER = "    return tuple(out)\n"

#: The two skip doors.
SKIP_CELL = ("            if not has_copper(pad):\n"
             "                continue\n")
SKIP_FOREIGN = ("                if not has_copper(pad):\n"
                "                    continue\n")

CONTROL = "def pad_from_kipy(pad: KipyPad) -> Pad:\n"

MUTATIONS = [
    # 1. The padstack answered and none of its layers is copper — read as "unknown"
    # again, i.e. as EVERY copper layer: the paste window becomes through copper.
    ("M1 a paste-only stack answers None again", BOARD, ANSWER,
     "    return tuple(out) if out else None  # MUTATION: () collapsed to None\n",
     [CELL_STACK, CELL_BUS, CELL_VIA]),

    # 2. The cell door keeps the pad: it becomes an island anchor (two «pads» make
    # the bus a candidate, and a via «touches» the window).
    ("M2 cell_pad_areas stops skipping such a pad", CONNECTIVITY, SKIP_CELL,
     "            # MUTATION: a paste-only pad is an island anchor again\n",
     [CELL_PADS, CELL_VIA]),

    # 3. The foreign door keeps the pad: the same window is a FOREIGN pad again.
    ("M3 pad_areas stops skipping such a pad", CONNECTIVITY, SKIP_FOREIGN,
     "                # MUTATION: a paste-only pad is a foreign pad again\n",
     [CELL_PADS]),

    # 4. The mirror defect: the copper cut answers "no copper" for EVERY stack, so
    # a real F.Cu pad loses its copper and the ordinary piece is not taken.
    ("M4 the copper cut answers no-copper always", BOARD, ANSWER,
     "    return ()  # MUTATION: no copper on any pad\n",
     [CELL_STACK_COPPER, CELL_ORDINARY]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", BOARD, CONTROL,
     CONTROL + "    # MUTATION: cosmetic\n",
     "survive"),
]


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
    print(f"guards : {guards}")
    print(f"mutated: {BOARD}, {CONNECTIVITY}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    cache: dict[Path, str] = {}
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = cache.setdefault(path, path.read_text(encoding="utf-8"))
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
            assert path.read_text(encoding="utf-8") == original, \
                f"restore FAILED for {rel}"

    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
