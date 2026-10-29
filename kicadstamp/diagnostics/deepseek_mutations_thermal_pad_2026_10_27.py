# kicadstamp/diagnostics/deepseek_mutations_thermal_pad_2026_10_27.py
"""Mutation rows for plan_2026_10_27_thermal_via_duplicate_pad_number.

The promise (Denis, 27.10): a thermal via array lands on the LARGEST copper pad
with its number, because a TO-252 `TabPin2` (and a DPAK tab, a QFN exposed pad)
repeats a pin's number and lists the little pin FIRST. One row per half of that
sentence:

  M1  the planner calls `get_pad_by_number` again -> the FIRST pad: the grid lands
                                                     on the pin (the live defect)
  M2  the SMALLEST pad wins                       -> the same, from the other side
  M3  `>=` instead of `>` (the last of the equals) -> two equal pads pick the SECOND
  M4  the Log line on ANY count                   -> one pad says «has 1 pads»

§7.8 (29.10) added the keepout half of the same sentence — a pad that carries no
copper is not an OBSTACLE either:

  M5  the keepout without the copper check        -> the tab's paste windows push
                                                     the whole grid off the tab
  M6  the skip by the EMPTY NUMBER, not by copper -> a copper pad numbered "" stops
                                                     being an obstacle
  M7  the check only in the `target_fp` loop      -> a NEIGHBOUR's window returns
  M8  the check AFTER `pad_area_of`               -> a sizeless pad reaches KiCad's
                                                     bbox fallback
  K1  cosmetic control (a comment on a def line)  -> NOTHING may redden

Safeguards as everywhere in this package (rule 38): a template matching more than
once is refused, a row that reddens NOTHING is a MISS, the control must survive, and
every touched file is restored in `finally` with the restore verified byte for byte.

Run: `.venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_thermal_pad_2026_10_27`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = "tests/geometry/test_thermal_grid_duplicate_pad.py"
GRID = "kicadstamp/geometry/thermal_grid.py"
PLANNER = "kicadstamp/placement/services/via_planner.py"

CELL_TAB = "test_the_grid_lands_on_the_tab_when_the_pin_is_first"
CELL_TAB_FIRST = "test_the_tab_wins_even_when_it_is_listed_first"
CELL_UNIQUE = "test_a_unique_number_is_untouched"
CELL_EQUAL = "test_equal_pads_keep_the_first"
CELL_MISSING = "test_a_missing_number_keeps_the_old_error_and_text"
CELL_LOG = "test_a_repeated_number_is_said_once_in_the_log"
CELL_NO_SIZE = "test_a_single_pad_without_a_size_keeps_the_geometry_error"
CELL_PASTE = "test_a_paste_only_pad_is_never_a_candidate"
CELL_KEEPOUT_TAB = "test_the_tab_paste_windows_do_not_push_the_grid_off_the_tab"
CELL_NEIGHBOUR_WINDOW = "test_a_neighbours_paste_window_is_not_an_obstacle_either"
CELL_COPPER_EMPTY_NUMBER = "test_a_copper_pad_with_an_empty_number_is_still_an_obstacle"
CELL_NO_COPPER_NO_SIZE = \
    "test_a_pad_without_copper_and_without_a_size_is_not_measured_by_kicad"


#: The whole keepout walk of `_build_keepout` — both pad loops AND the
#: area/fallback branch. All four §7.8 rows move the copper check inside it, so the
#: template is ONE verbatim copy and every variant is DERIVED from it: hand-copied
#: indentation is exactly how a row silently stops matching the code.
KEEPOUT_BLOCK = (
    "        pad_items: list[tuple[str, Any]] = []\n"
    "        target_ref_name = target_fp.ref\n"
    "        for pad in self.adapter.get_footprint_pads(target_fp):\n"
    "            if exclude and (target_ref_name, pad.number) in exclude:\n"
    "                continue\n"
    "            if not pad_has_copper(pad):\n"
    "                continue\n"
    "            pad_items.append((target_ref_name, pad))\n"
    "        for info in planned:\n"
    "            fp = self.adapter.get_footprint(info.ref)\n"
    "            if fp is None:\n"
    "                continue\n"
    "            for pad in self.adapter.get_footprint_pads(fp):\n"
    "                if exclude and (info.ref, pad.number) in exclude:\n"
    "                    continue\n"
    "                if not pad_has_copper(pad):\n"
    "                    continue\n"
    "                pad_items.append((info.ref, pad))\n"
    "\n"
    "        clearance = int(self.cfg.via_keepout_clearance_mm * MM)\n"
    "        keepout: list[Any] = []\n"
    "        fallback: list[tuple[Any, str]] = []\n"
    "        for ref, pad in pad_items:\n"
    "            area = pad_area_of(pad)\n"
    "            if area is None:\n"
    "                fallback.append((pad, ref))\n"
    "            else:\n"
    "                # Same convention as Rect.from_bbox's clearance: grown on every\n"
    "                # side, so via_keepout_clearance_mm stays \"per side\".\n"
    "                keepout.append(area.inflated(clearance))\n"
)

#: The two skip pairs, one per loop (their indentation differs).
SKIP_TARGET = ("            if not pad_has_copper(pad):\n"
               "                continue\n")
SKIP_PLANNED = ("                if not pad_has_copper(pad):\n"
                "                    continue\n")

AREA_BRANCH = (
    "            if area is None:\n"
    "                fallback.append((pad, ref))\n"
    "            else:\n"
    "                # Same convention as Rect.from_bbox's clearance: grown on every\n"
    "                # side, so via_keepout_clearance_mm stays \"per side\".\n"
    "                keepout.append(area.inflated(clearance))\n")

#: M8: the check moved AFTER `pad_area_of` — a pad with no area still falls into
#: the bbox fallback, a pad with an area is filtered on copper only.
AREA_BRANCH_AFTER = (
    "            if area is None:\n"
    "                fallback.append((pad, ref))  # MUTATION: pad_area_of came first\n"
    "            elif pad_has_copper(pad):  # MUTATION: the check after pad_area_of\n"
    "                keepout.append(area.inflated(clearance))\n")


def _variant(*, drop_target=False, drop_planned=False, by_number=False,
             after_area=False) -> str:
    """KEEPOUT_BLOCK with one §7.8 change — asserts every piece it removes."""
    text = KEEPOUT_BLOCK
    if drop_target:
        assert SKIP_TARGET in text, "the target loop's skip is not in the block"
        text = text.replace(SKIP_TARGET, "", 1)
    if drop_planned:
        assert SKIP_PLANNED in text, "the planned loop's skip is not in the block"
        text = text.replace(SKIP_PLANNED, "", 1)
    if by_number:
        assert "if not pad_has_copper(pad):" in text
        text = text.replace("if not pad_has_copper(pad):",
                            'if str(getattr(pad, "number", "")) == "":')
    if after_area:
        assert AREA_BRANCH in text, "the area/fallback branch is not in the block"
        text = text.replace(AREA_BRANCH, AREA_BRANCH_AFTER, 1)
    return text

#: The planner's pick (M1 puts the adapter call back).
PICK = ("        pad, pads_with_number = pick_thermal_pad(\n"
        "            self.adapter.get_footprint_pads(target_fp), tva.pad)\n")

#: The comparison (M2 flips it to «smallest», M3 to «last of the equals»).
COMPARE = ("        if area > best_area:                     # strict: a tie keeps the FIRST\n"
           "            best, best_area = pad, area\n")

#: The Log line's condition (M4 makes it fire on one pad too). The line lives INSIDE
#: the `try` that wraps the grid, so it fails the same way the grid does (cell 8).
LOG_GATE = "            if pads_with_number > 1:\n"

CONTROL = "def pick_thermal_pad(pads, number) -> tuple:\n"

MUTATIONS = [
    # 1. The planner asks the adapter again: the FIRST pad (the live defect).
    ("M1 the planner calls get_pad_by_number again", PLANNER, PICK,
     "        pad = self.adapter.get_pad_by_number(target_fp, tva.pad)"
     "  # MUTATION\n"
     "        pads_with_number = 1\n",
     [CELL_TAB, CELL_TAB_FIRST]),

    # 2. The smallest pad wins — the same defect seen from the other side.
    ("M2 the smallest pad wins", GRID, COMPARE,
     "        if best is None or area < best_area:  # MUTATION: the smallest\n"
     "            best, best_area = pad, area\n",
     [CELL_TAB, CELL_TAB_FIRST]),

    # 3. `>=` instead of `>`: with equal areas the LAST one wins.
    ("M3 the last of the equals wins", GRID, COMPARE,
     "        if area >= best_area:  # MUTATION: a tie takes the last\n"
     "            best, best_area = pad, area\n",
     [CELL_EQUAL]),

    # 4. The Log line fires whatever the count.
    ("M4 the Log line on any count", PLANNER, LOG_GATE,
     "            if pads_with_number > 0:  # MUTATION: the line always\n",
     [CELL_LOG]),

    # 5. §7.8: the keepout without the copper check — the tab's own paste windows
    #    are obstacles again and the free-space search leaves the tab (live U1).
    ("M5 the keepout without the copper check", PLANNER, KEEPOUT_BLOCK,
     _variant(drop_target=True, drop_planned=True), [CELL_KEEPOUT_TAB]),

    # 6. §7.8: skipping by the EMPTY NUMBER instead of by copper — a mounting pad
    #    numbered "" stops being an obstacle.
    ("M6 the skip by the empty number", PLANNER, KEEPOUT_BLOCK,
     _variant(by_number=True), [CELL_COPPER_EMPTY_NUMBER]),

    # 7. §7.8: the check only in the `target_fp` loop — a neighbour's window is
    #    back as an obstacle.
    ("M7 the check only in the target loop", PLANNER, KEEPOUT_BLOCK,
     _variant(drop_planned=True), [CELL_NEIGHBOUR_WINDOW]),

    # 8. §7.8: the check AFTER `pad_area_of` — a pad with no copper and no size
    #    goes to KiCad's boxes, and the box comes back as an obstacle.
    ("M8 the check after pad_area_of", PLANNER, KEEPOUT_BLOCK,
     _variant(drop_target=True, drop_planned=True, after_area=True),
     [CELL_NO_COPPER_NO_SIZE]),

    # K1 — the control: a comment changes, the behaviour does not.
    ("K1 cosmetic control (a comment on a def line)", GRID, CONTROL,
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
    print(f"mutated: {GRID}, {PLANNER}")
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
