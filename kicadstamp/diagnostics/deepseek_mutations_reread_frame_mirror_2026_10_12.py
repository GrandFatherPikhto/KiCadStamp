# kicadstamp/diagnostics/deepseek_mutations_reread_frame_mirror_2026_10_12.py
"""Mutation rig for plan_2026_10_12_reread_frame_mirror (the re-read frame).

Rule 38 discipline (deepseek.md): a guard only counts when it KILLS the product
line it names. This rig spoils ONE product line at a time and runs the cells the
slice pointed at it; it keeps the two fuses of the previous rig — `count(old) != 1`
refuses an ambiguous template, and a run that produced NO test failure ("miss")
is a rig failure, not a kill.

Mutations (name, file, old, new, expected):
  M1  `frame_for_live_selection` goes back to `fit_cell_frame(pairs)` — the fit
      chooses the mirror again, exactly the defect. The К6–К9 frame cells and
      Denis's own case (К16a) must fail.
  M2  `follow_the_tree_node` computes the node's pose and then keeps the FITTED
      rotation — the frame no longer follows the node. К10 and К16b must fail.
  M3  the mirror line is dropped from `frame_warnings` — a legal mirror nobody
      mentions (rule 3 of the plan). The К6 wiring cell must fail.
  M4  the Г3 condition comes back: the "guessed" line is emitted only when the
      caller names the cell. The number-3 cell of
      test_refresh_moved_slot_lands_in_new_offsets_and_warns must fail.
  K1  a cosmetic comment-only edit -> must SURVIVE (control: the rig is not
      trivially red).

Run:  .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_reread_frame_mirror_2026_10_12.py
READ-ONLY apart from the temporary single-line edit, always reverted in finally.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_FIT = "    fit = fit_cell_frame(pairs, mirror=mirror)"
_FIT_MUT = "    fit = fit_cell_frame(pairs)  # mutation: the fit chooses again"

_TAKE = "    return dataclasses.replace(frame, rotation_deg=pose.rotation_deg), lines"
_TAKE_MUT = "    return frame, lines  # mutation: the node's pose is not taken"

_MIRROR_LINE = "    if frame.mirror:"
_MIRROR_MUT = "    if False:  # mutation: the mirror line is never printed"

_GATE_OFF = "    pose, reason = node_pose_for_instance("
_GATE_ON = ("    if not context.cell_name:  # mutation: the Г3 gate comes back\n"
            "        return frame, []\n"
            "    pose, reason = node_pose_for_instance(")

_COMMENT = "# kicadstamp/frame_node_pose.py"
_COMMENT_MUT = "# kicadstamp/frame_node_pose.py (cosmetic control)"

# (name, file, old, new, expected, node ids)
MUTATIONS = [
    ("M1 the fit chooses the mirror again",
     "kicadstamp/cell_frame.py",
     _FIT, _FIT_MUT, "killed",
     ["tests/trees/test_frame_node_pose.py",
      "tests/trees/test_cell_geometry_refresh.py"]),
    ("M2 the frame keeps the fitted rotation",
     "kicadstamp/frame_node_pose.py",
     _TAKE, _TAKE_MUT, "killed",
     ["tests/trees/test_frame_node_pose.py"]),
    ("M3 the mirror line is never printed",
     "kicadstamp/cell_frame.py",
     _MIRROR_LINE, _MIRROR_MUT, "killed",
     ["tests/trees/test_cell_geometry_refresh.py::"
      "test_the_plan_hands_the_cell_layer_to_the_frame"]),
    ("M4 the guessed line needs a named cell again",
     "kicadstamp/frame_node_pose.py",
     _GATE_OFF, _GATE_ON, "killed",
     ["tests/trees/test_frame_node_pose.py",
      "tests/trees/test_cell_geometry_refresh.py::"
      "test_refresh_moved_slot_lands_in_new_offsets_and_warns"]),
    ("K1 cosmetic comment",
     "kicadstamp/frame_node_pose.py",
     _COMMENT, _COMMENT_MUT, "survived",
     ["tests/trees/test_frame_node_pose.py"]),
]


def _run(ids: list[str]) -> str:
    """"green" (all passed) / "red" (a test FAILED) / "miss" (no verdict at all:
    a collection error, or the run did not start — never a kill)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-n", "auto", *ids],
        cwd=ROOT, capture_output=True, text=True)
    if proc.returncode == 0:
        return "green"
    text = proc.stdout + proc.stderr
    return "red" if ("failed" in text or "FAILED" in text) else "miss"


def main() -> int:
    failures = []
    for name, rel, old, new, expected, ids in MUTATIONS:
        path = ROOT / rel
        src = path.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"[REFUSE] {name}: template count != 1 for {old!r}")
            failures.append(name)
            continue
        path.write_text(src.replace(old, new), encoding="utf-8")
        try:
            verdict = _run(ids)
        finally:
            path.write_text(src, encoding="utf-8")
        # "green" IS "survived", "red" IS "killed", and "miss" stays itself: the
        # vocabulary of the plan's guards, kept apart from the rig's own states.
        got = {"green": "survived", "red": "killed"}.get(verdict, verdict)
        ok = (got == expected)
        print(f"[{'OK' if ok else 'BAD'}] {name}: expected {expected}, got {got}")
        if not ok:
            failures.append(name)
    if failures:
        print(f"FAILED mutations: {failures}")
        return 1
    print("all mutations behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
