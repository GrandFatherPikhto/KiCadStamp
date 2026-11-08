# kicadstamp/diagnostics/run_internode_place_mutations_2026_11_08.py
"""Mutation rows for «cell copper by PLACE» (plan_2026_11_08_internode_cell_copper_by_place).

Three rounds of the same plan live here. Round 1 (commit 9ac3db53) added the read-only second
pass: the registry key is written only on REDRAW, so everything READ and not yet redrawn was
handed to the inter-node classifier as its copper — measured live on HP-Power-v099, 20 of 83
pieces. Round 2 (§2 of the acceptance) added the two decisions that pass was missing: a KEY may
point at ANOTHER record's piece (a reading shifts the record numbers), and the PLACE must ask the
LAYER too (an F.Cu record must not take the B.Cu piece lying exactly underneath it). Round 3 (§3)
put the frame's MIRROR on the physical side and closed the two holes Denis's own mutations found.

Each row removes exactly ONE decision and must turn a NAMED cell red:

  M1  only the slots with NO key are asked — a shifted key is never re-checked
      (cell_copper_place.place_pass)                                        cells а, в2
  M2  the net gate is gone — another net on that place is taken            cell «other net»
  M3  the layer is not compared — the F.Cu record takes the B.Cu piece     cells в, в2, г
  M4  the instance's MIRROR is ignored — a mirrored cell expects its own side  cell д
  M5  a slot the KEY already answered is asked again by place              cell б
  M6  the same piece is counted twice (the `taken` set is gone)            cell 5б
  M7  a non-rigid frame is trusted                                        cell 4
  M8  the place pass is gone altogether                                    cells 1, 2
  M9  the frame's mirror is left to the FIT again — `instance_frame` does not hand the cell's
      layer down (cell_copper_place.instance_frame)                        cell «the side»
  M10 the record's OWN layer is ignored, the cell's is used alone          cell «own layer»
  M11 an already-taken piece STOPS the search, so the second piece at one place is never given
      (cell_copper_place.place_pass)                                       cell «two pieces»
  M12 a cosmetic control (a comment must change nothing)

Safeguards, as in every harness of this package (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS, not a pass; the control must survive; every file is
restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_internode_place_mutations_2026_11_08`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CELL_COPPER = "kicadstamp/internode_cell_copper.py"
PLACE = "kicadstamp/cell_copper_place.py"

GUARDS = "tests/net/test_internode_cell_copper.py"

MUTATIONS = [
    ("M1 only the keyless slots are asked (a shifted key is never re-checked)", PLACE,
     "    out.asked, out.keyless = slots_asked(cell, slots, keyed, live_by_uuid, frame, tol_mm)\n",
     "    # MUTATION: a slot the key answered is never asked by place\n"
     "    out.asked = out.keyless = set(slots) - set(keyed)\n",
     ["test_a_shifted_key_still_asks_the_slot_by_place",
      "test_a_key_on_the_other_side_keeps_its_piece_and_still_asks_the_place"]),

    ("M2 the net gate is gone", PLACE,
     "    return same_net(item, record, getattr(record, \"net\", None))\n",
     "    return True  # MUTATION: the net is not compared\n",
     ["test_a_piece_on_another_net_at_the_same_place_stays_internode"]),

    ("M3 the layer is not compared", PLACE,
     "    return actual == expected\n",
     "    return True  # MUTATION: the layer is not compared\n",
     ["test_an_ownerless_piece_on_the_other_side_stays_internode",
      "test_a_key_on_the_other_side_keeps_its_piece_and_still_asks_the_place",
      "test_a_record_without_its_own_layer_takes_the_cells"]),

    ("M4 the instance's mirror is ignored", PLACE,
     "    return mirror_layer(name) if getattr(frame, \"mirror\", False) else name\n",
     "    return name  # MUTATION: a mirrored cell expects its own side\n",
     ["test_a_mirrored_instance_expects_the_other_side_of_the_board"]),

    # Measured 08.11: «make the keyed slot askable» must mean «ask EVERY slot», not «drop the
    # place test from the condition» — the latter still answers NO for a keyed slot whose piece
    # is found, i.e. it is not the mutation the plan names (it reddens а/в2 instead).
    ("M5 a slot the KEY answered is asked again by place", PLACE,
     "        points, record = _slot_parts(cell, slot)\n"
     "        item = live_by_uuid.get(uuid)\n"
     "        if (points is None or record is None or item is None\n"
     "                or not piece_at_the_place(points, item, record, cell, frame, tol_mm)):\n"
     "            asked.add(slot)\n",
     "        # MUTATION: a slot the key answered is asked again — it steals its neighbour\n"
     "        asked.add(slot)\n",
     ["test_a_keyed_piece_at_the_place_keeps_its_neighbour_internode"]),

    # The `taken` set is what a DOUBLED count would slip past: `found` is a set, so the uuids
    # cannot show it — the per-kind counters and the cell's `vias`/`tracks` are what redden.
    ("M6 the same piece is counted twice", PLACE,
     "            if uuid is not None and uuid not in taken:\n",
     "            if uuid is not None:  # MUTATION: the same piece may be counted twice\n",
     ["test_one_piece_matching_two_records_is_given_back_once"]),

    ("M7 a non-rigid frame is trusted", CELL_COPPER,
     "            if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:\n",
     "            if frame is None:  # MUTATION: a non-rigid frame is trusted\n",
     ["test_a_non_rigid_frame_gives_nothing_by_place_and_says_so_once"]),

    ("M8 the place pass is gone", CELL_COPPER,
     "        if slots and live.get(\"footprint\"):\n",
     "        if False:  # MUTATION: no second pass — a read cell owns nothing\n",
     ["test_a_keyless_record_gives_its_copper_back_by_place",
      "test_a_dead_key_gives_the_new_piece_at_the_same_place_back"]),

    ("M9 the frame's mirror is left to the fit again", PLACE,
     "                                origin_role=getattr(cell, \"anchor_role\", None),\n"
     "                                cell_layer=getattr(cell, \"layer\", None))\n",
     "                                origin_role=getattr(cell, \"anchor_role\", None))\n",
     ["test_the_frames_mirror_comes_from_the_side_the_instance_stands_on"]),

    ("M10 the record's own layer is ignored", PLACE,
     "    name = getattr(record, \"layer\", None) or getattr(cell, \"layer\", None)\n",
     "    name = getattr(cell, \"layer\", None)  # MUTATION: the record's own layer is ignored\n",
     ["test_a_record_keeps_its_own_layer_inside_a_cell_of_another_side"]),

    ("M11 a taken piece stops the search", PLACE,
     "            if uuid is not None and uuid not in taken:\n"
     "                taken.add(uuid)\n",
     "            if uuid is not None and uuid in taken:\n"
     "                break  # MUTATION: a taken piece stops the search\n"
     "            if uuid is not None and uuid not in taken:\n"
     "                taken.add(uuid)\n",
     ["test_two_pieces_at_one_place_are_both_given_back_once_each"]),

    ("M12 cosmetic control (a comment must change nothing)", PLACE,
     "    out = PlacePass()\n",
     "    out = PlacePass()  # MUTATION: cosmetic\n",
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
