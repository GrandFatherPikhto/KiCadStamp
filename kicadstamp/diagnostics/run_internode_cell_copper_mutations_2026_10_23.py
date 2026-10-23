# kicadstamp/diagnostics/run_internode_cell_copper_mutations_2026_10_23.py
"""Mutation rows for "cell copper given back to the cells" (plan_2026_10_23_internode_without_cell_copper).

The defect was quiet: a cell's own via and B.Cu leg were selected and re-recorded as inter-node
copper — one piece of copper with two owners. Every row below removes exactly ONE decision of the
fix and must turn a named cell red:

  M1  the classifier never subtracts (the step is gone)        (internode_capture._classify_area)
  M2  the SELECT door refuses a pair it cannot place           (absent_copper_prune.registry_record_copper_map)
  M3  cutting is a no-op: the unit comes back whole            (internode_cell_copper.cut_cell_copper)
  M4  a piece that is cell copper entirely is kept anyway      (internode_cell_copper.subtract_units)
  M5  the board is read again for every node                   (internode_cell_copper.tree_cell_copper)
  M6  the run says nothing about what it gave back             (internode_cell_copper.subtract_units)
  M7  cosmetic control (a comment must change nothing)

Safeguards, as in every harness of this package: a template matching more than once is refused
(rule 38); a row that reddens nothing is a MISS; the control must survive; every file is restored
in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_internode_cell_copper_mutations_2026_10_23`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CAPTURE = "kicadstamp/internode_capture.py"
CELL_COPPER = "kicadstamp/internode_cell_copper.py"
ABSENT = "kicadstamp/absent_copper_prune.py"

GUARDS = ("tests/net/test_internode_cell_copper.py "
          "tests/selection/test_registry_pair_frame.py")

MUTATIONS = [
    ("M1 the classifier never subtracts", CAPTURE,
     "    (out.units, out.cell_copper, out.subtracted, out.cell_only,\n"
     "     cell_lines) = subtract_units(adapter, cfg, tree, config_path, out.units,\n"
     "                                  sheet_names=_sn)\n",
     "    cell_lines = []  # MUTATION: the units keep the cells' own copper\n",
     ["test_the_classifier_is_where_the_subtraction_happens",
      "test_a_piece_that_is_cell_copper_entirely_is_counted_and_dropped"]),

    # NOTE: flipping only `frame_check`/`place_check` in `tree_cell_copper` is an EQUIVALENT
    # mutant — the select door keeps a pair it cannot place either way (it only counts
    # `disagreed`). The real rule lives in the map, so the row mutates THAT.
    ("M2 the select door refuses a pair it cannot place", ABSENT,
     "            if place_check:\n"
     "                continue                 # the STRICT door never claims it\n",
     "            if True:  # MUTATION: the select door refuses too\n"
     "                continue\n",
     ["test_the_select_door_keeps_a_pair_the_place_cannot_confirm"]),

    ("M3 cutting is a no-op", CELL_COPPER,
     "    return dataclasses.replace(unit, tracks=tracks, vias=vias,\n"
     "                               track_pads=track_pads, via_pads=via_pads), given\n",
     "    return unit, given  # MUTATION: nothing is cut out\n",
     ["test_a_bridge_loses_the_cells_via_and_bcu_track_and_keeps_its_pads"]),

    ("M4 an all-cell piece is kept", CELL_COPPER,
     "        if given and not trimmed.tracks and not trimmed.vias:\n",
     "        if False:  # MUTATION: a piece of pure cell copper is 'inter-node'\n",
     ["test_a_piece_that_is_cell_copper_entirely_is_counted_and_dropped"]),

    ("M5 the board is read again for every node", CELL_COPPER,
     "                live=live, registry=registry, frame_check=False)\n",
     "                live=None, registry=registry, frame_check=False)  "
     "# MUTATION: one read per node\n",
     ["test_one_board_read_per_run_however_many_nodes_the_tree_has"]),

    ("M6 the run says nothing about what it gave back", CELL_COPPER,
     "    lines = list(mapping.warnings)\n",
     "    lines = []  # MUTATION: the run says nothing about the cells\n",
     ["test_the_classifier_is_where_the_subtraction_happens"]),

    ("M7 cosmetic control (a comment must change nothing)", CELL_COPPER,
     "    out = CellCopperMap()\n",
     "    out = CellCopperMap()  # MUTATION: cosmetic\n",
     "survive"),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 300 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


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
            code, out = _run(guards)
            failed = _failed_ids(out)
            if expect == "survive":
                if failed:
                    print(f"[MISS] {name}: a cosmetic change reddened {failed}")
                    misses += 1
                else:
                    print(f"[OK] {name}: survived, as it must")
                continue
            if not failed:
                print(f"[MISS] {name}: NO failed test — the mutation kills "
                      f"nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in expect if not any(e in f for f in failed)]
            if missing:
                print(f"[MISS] {name}: expected these to redden, they did not: "
                      f"{missing}\n  actually failed: {failed}")
                misses += 1
            else:
                print(f"[KILL] {name}: {len(failed)} red — {failed}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
