# kicadstamp/diagnostics/deepseek_mutations_micro_segments_2026_10_19.py
"""Mutation harness for Ч1 of plan_2026_10_15_micro_track_redraw_duplicates.

ONE rule (kicadstamp/degenerate_segments.py), three consumers. Exactly three
rows, one per consumer — a rule removed at one call site must redden THAT
consumer's cell and nothing else:

  * «Дубли меди»  — board_dedupe.find_track_duplicate_groups must keep the
    degenerate segment out of every group (tests/domain/test_micro_segment_consumers.py);
  * track_matches — a degenerate live/planned segment must never match;
  * the read path — `_items_from_unit` must collapse the stub and re-point its
    neighbours (the same rule, not a second copy).

Safeguards (rule 38, as in the earlier harnesses): a template that matches more
than once is refused; a row with zero failed tests is a MISS; the file is
restored in `finally` and the restoration is verified by text.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_micro_segments_2026_10_19.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEDUPE = "kicadstamp/board_dedupe.py"
REGISTRY = "kicadstamp/registry.py"
NODES = "kicadstamp/internode_nodes.py"
RULES = "kicadstamp/degenerate_segments.py"
TEMPLATE = "kicadstamp/template_extraction.py"
GUARDS = ("tests/domain/test_micro_segment_consumers.py "
          "tests/domain/test_degenerate_segments.py "
          "tests/domain/test_board_dedupe.py "
          "tests/placement/test_track_positional_precheck.py "
          "tests/extraction/test_template_extraction.py")

MUTATIONS = [
    # ── the three original consumers ───────────────────────────────────────
    ("М1 «Дубли меди» let the degenerate segment into the groups", DEDUPE,
     "    groupable = [t for t in tracks if not is_degenerate_segment(t.start, t.end)]",
     "    groupable = list(tracks)  # MUTATION: every segment may be grouped",
     ["test_the_fact_is_no_duplicate_group_at_all",
      "test_the_report_lists_short_segments_and_deletes_nothing",
      "test_boundary_19_20_21_um"]),

    ("М2 the positional pre-check answers for stubs again", REGISTRY,
     "    positional_pool = [live for live in live_tracks\n"
     "                       if not is_degenerate_segment(live.start, live.end)]\n"
     "    kept: list[TrackCommand] = []\n"
     "    skipped = 0\n"
     "    for cmd in to_create:\n"
     "        if (not is_degenerate_segment(cmd.start, cmd.end)\n"
     "                and any(track_matches(live, cmd) for live in positional_pool)):",
     "    positional_pool = list(live_tracks)  # MUTATION: stubs answer positionally\n"
     "    kept: list[TrackCommand] = []\n"
     "    skipped = 0\n"
     "    for cmd in to_create:\n"
     "        if any(track_matches(live, cmd) for live in positional_pool):",
     ["test_positional_precheck_never_pairs_a_degenerate_command",
      "test_positional_precheck_ignores_a_degenerate_neighbour",
      "test_positional_path_never_pairs_a_degenerate_segment"]),

    ("М3 the read path stores the stub instead of collapsing it", NODES,
     "    collapsed = collapse_degenerate_segments(\n"
     "        [(t.start, t.end) for t in unit.tracks],\n"
     "        attractors=[v.position for v in unit.vias] + [anchor_point])",
     "    from .degenerate_segments import CollapseResult as _CR  # MUTATION\n"
     "    collapsed = _CR([(t.start, t.end) for t in unit.tracks],\n"
     "                    list(range(len(unit.tracks))), [])",
     ["test_items_from_unit_drops_the_stub_and_keeps_the_chain_connected",
      "test_items_from_unit_pulls_the_stub_ends_onto_a_touched_via"]),

    # ── acceptance Д-1: the guard belongs to the POSITIONAL path only ──────
    ("Д-1 the guard is back on the uuid path (track_matches)", REGISTRY,
     "    live_start_x_mm, live_start_y_mm = live_track.start.x / MM, live_track.start.y / MM",
     "    if (is_degenerate_segment(live_track.start, live_track.end)\n"
     "            or is_degenerate_segment(cmd.start, cmd.end)):\n"
     "        return False  # MUTATION: the positional rule is applied by uuid\n"
     "    live_start_x_mm, live_start_y_mm = live_track.start.x / MM, live_track.start.y / MM",
     ["test_uuid_path_keeps_a_degenerate_segment_with_the_same_geometry",
      "test_track_matches_still_compares_geometry_for_the_uuid_path"]),

    # ── acceptance Д-2: touching stubs are ONE group ───────────────────────
    ("Д-2 each touching stub collapses into its own point", RULES,
     "    groups = _touching_groups(segments, degenerate)",
     "    groups = [[index] for index in degenerate]  # MUTATION: no grouping",
     ["test_a_chain_of_three_stubs_collapses_into_one_point",
      "test_two_15um_stubs_meet_at_one_point"]),

    # ── acceptance Д-3: pads are attractors where they are known ───────────
    ("Д-3 the cell path stops passing pad centres", TEMPLATE,
     "        attractors=pad_points + [v.position for v in vias] + [origin])",
     "        attractors=[v.position for v in vias] + [origin])  # MUTATION: pads dropped",
     ["test_degenerate_stub_collapses_into_a_touched_pad"]),
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


def _failed_ids(out: str) -> list[str]:
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
                  f"{original.count(old)} times — refusing (rule 38)")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(guards)
            failed = _failed_ids(out)
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
    print(f"\nverdict: {len(MUTATIONS) - misses} killed, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
