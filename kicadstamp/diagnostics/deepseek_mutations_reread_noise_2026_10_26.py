# kicadstamp/diagnostics/deepseek_mutations_reread_noise_2026_10_26.py
"""Mutation rows for Ч1 of plan_2026_10_26_reread_updated_noise.

The promise under test is ONE sentence (Denis, 26.10): «updated» means the copper
CHANGED — a permutation or a reversed segment is the same copper, a moved end, a
width, a layer or a lost duplicate is not. Every row removes exactly ONE part of
that promise, so the red cell names the part that broke:

  M1  the comparison goes back to `zip` by order        -> the order cells
  M2  `layer` drops out of the track element            -> the layer cell
  M3  `width_mm` drops out of the track element         -> the width cells
  M4  a tolerance WIDER than the write step (ends
      rounded to 1 digit = 0.05 mm)                     -> the "0.01 mm stays updated" cells
  M5  a 1e-3 tolerance (round to 3 digits, twenty times
      the half write step of 5e-5)                      -> the 0.0002 mm cell
  M6  the ends are no longer canonicalised              -> the reversed-segment cell
  M7  the multiset becomes a SET                        -> the lost-duplicate cell
  K1  a cosmetic control (a docstring line)             -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive,
and the touched file is restored in `finally` with the restore verified byte for
byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_reread_noise_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/net/test_internode_geometry_noise.py"
NODES = "kicadstamp/internode_nodes.py"

CELL_ORDER = "test_the_same_copper_in_another_order_is_unchanged"
CELL_VIA_ORDER = "test_vias_in_another_order_are_unchanged"
CELL_SWAP = "test_two_tracks_swapped_are_unchanged_even_at_different_widths"
CELL_DOOR_ORDER = "test_the_reread_calls_the_same_copper_in_reverse_order_unchanged"
CELL_LAYER = "test_another_layer_is_updated"
CELL_WIDTH = "test_another_width_is_updated"
CELL_WIDTH_SWAP = "test_two_tracks_trading_their_widths_are_updated"
CELL_SHIFT = "test_a_shift_is_updated"
CELL_SWAPPED_ENDS = "test_a_track_with_its_ends_swapped_is_unchanged"
CELL_DUPLICATE = "test_a_duplicate_moving_between_two_places_is_updated"

#: The two lines `same_geometry` decides with — every M-row rewrites one of them.
RETURN_BLOCK = (
    "    return (Counter(map(_track_element, old.tracks))\n"
    "            == Counter(map(_track_element, new.tracks))\n"
    "            and Counter(map(_via_element, old.vias))\n"
    "            == Counter(map(_via_element, new.vias)))\n"
)

TRACK_RETURN = "    return (first, second, track.width_mm, track.layer)\n"

CANONICALISE = ("    if second < first:\n"
                "        first, second = second, first\n")

#: A line of the module under test whose TEXT can change without any behaviour
#: changing — the control row's premise.
DOC_LINE = "def _via_element(via) -> tuple:"


MUTATIONS = [
    # 1. Back to the old comparison: same elements, pairwise by sequence.
    ("M1 comparison by order again (zip)",
     RETURN_BLOCK,
     "    return ([_track_element(t) for t in old.tracks]\n"
     "            == [_track_element(t) for t in new.tracks]\n"
     "            and [_via_element(v) for v in old.vias]\n"
     "            == [_via_element(v) for v in new.vias])\n",
     [CELL_ORDER, CELL_VIA_ORDER, CELL_SWAP, CELL_DOOR_ORDER]),

    # 2. The layer leaves the element: F.Cu and B.Cu become interchangeable.
    ("M2 layer dropped from the track element",
     TRACK_RETURN,
     "    return (first, second, track.width_mm)\n",
     [CELL_LAYER]),

    # 3. The width leaves the element: a thin and a thick track match each other.
    ("M3 width dropped from the track element",
     TRACK_RETURN,
     "    return (first, second, track.layer)\n",
     [CELL_WIDTH, CELL_WIDTH_SWAP]),

    # 4. A tolerance nobody measured, and wider than the write step: the operator's
    # own boundary (0.01 mm) is rounded away, which is exactly what must not happen.
    # (Rounded to 2 digits a 0.01 shift would still survive — the tolerance a
    # rounding to N digits really is, is 0.5 * 10^-N, so the row rounds to 1.)
    ("M4 tolerance 0.05 mm, wider than the write step (ends rounded to 1 digit)",
     TRACK_RETURN,
     "    return (tuple(round(v, 1) for v in first),\n"
     "            tuple(round(v, 1) for v in second),\n"
     "            track.width_mm, track.layer)\n",
     [CELL_SHIFT]),

    # 5. A tolerance five times the half write step: 0.0002 mm disappears.
    ("M5 tolerance 1e-3 mm (ends rounded to 3 digits)",
     TRACK_RETURN,
     "    return (tuple(round(v, 3) for v in first),\n"
     "            tuple(round(v, 3) for v in second),\n"
     "            track.width_mm, track.layer)\n",
     [CELL_SHIFT]),

    # 6. A segment becomes oriented again.
    ("M6 ends no longer canonicalised",
     CANONICALISE,
     "    if False:  # MUTATION: direction is part of the element again\n"
     "        first, second = second, first\n",
     [CELL_SWAPPED_ENDS]),

    # 7. Multiset -> set: losing one of two identical segments looks like nothing.
    ("M7 multiset becomes a set",
     RETURN_BLOCK,
     "    return (set(map(_track_element, old.tracks))\n"
     "            == set(map(_track_element, new.tracks))\n"
     "            and set(map(_via_element, old.vias))\n"
     "            == set(map(_via_element, new.vias)))\n",
     [CELL_DUPLICATE]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)",
     DOC_LINE,
     DOC_LINE + "  # MUTATION: cosmetic",
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
    print(f"mutated: {NODES}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    path = ROOT / NODES
    original = path.read_text(encoding="utf-8")
    misses = 0
    try:
        for name, old, new, expect in MUTATIONS:
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
                    f"restore FAILED for {NODES}"
    finally:
        path.write_text(original, encoding="utf-8")

    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
