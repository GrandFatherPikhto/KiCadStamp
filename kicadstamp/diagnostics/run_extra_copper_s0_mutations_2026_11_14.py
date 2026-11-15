# kicadstamp/diagnostics/run_extra_copper_s0_mutations_2026_11_14.py
"""Mutation rows for §0 of plan_2026_11_10_extra_copper_by_selection — the AREA of
an inter-node copper re-read (and §0 п.6, the local-name rule in the doors).

The live defect (HP-Power, 10.11 13:44): «Select inter-node copper» selects tracks
and vias, the next «Reread inter-node copper» took its pad BOUNDARIES from that
selection, every unit became UNMOORED and six records were declared «no longer on
the board». Each row removes exactly ONE decision and must turn a NAMED cell red:

  M1  the pads come from the selection again             (reread_area)
  M2  a record outside the selection is `missing` again  (internode_capture)
  M3  the report's area line is not written              (reread_report_lines)
  M4  the «places module(s)» line is not written          (reread_report_lines)
  M5  the Reread door skips the local-name rule           (internode_capture)
  M6  the auto-numbering door skips it                    (trees_dock._prompt_node)
  M7  a cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is
REFUSED; a row that reddens nothing is a MISS; the control must survive; every file
is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_extra_copper_s0_mutations_2026_11_14`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

AREA = "kicadstamp/reread_area.py"
CAPTURE = "kicadstamp/internode_capture.py"
REPORT = "kicadstamp/reread_report.py"
DOCK = "gui/docks/trees_dock.py"

GUARDS = ("tests/net/test_extra_copper_s0.py "
          "tests/gui/docks/test_trees_extra_copper_s0.py "
          "tests/net/test_internode_capture.py")

MUTATIONS = [
    ("M1 the pads come from the selection again", AREA,
     "    footprints = list(adapter.get_footprints())\n",
     "    footprints = [i for i in chosen if type(i).__name__ == \"Footprint\"]  # MUTATION\n",
     ["test_0a_a_medley_selection_gives_the_same_units_as_the_whole_board",
      "test_0v_a_selection_narrows_the_copper_and_never_the_pads"]),

    ("M2 a record outside the selection is `missing` again", CAPTURE,
     "        if narrowed and not any(sig & selection_pads for sig in candidates):\n",
     "        if False:  # MUTATION: not-checked reads as not-found\n",
     ["test_0b_a_record_outside_the_selection_is_not_missing"]),

    ("M3 the report's area line is not written", REPORT,
     "    if plan.narrowed:\n",
     "    if False:  # MUTATION: the report never names the area\n",
     ["test_0g_the_report_names_the_area_in_both_modes"]),

    ("M4 the «places module(s)» line is not written", REPORT,
     "    if plan.module_refs:\n",
     "    if False:  # MUTATION: modules are not named\n",
     ["test_0d_a_tree_with_modules_names_them_after_the_header"]),

    ("M5 the Reread door skips the local-name rule", CAPTURE,
     "        if old is None and record.name:\n",
     "        if False:  # MUTATION: a generated name may hit a local one\n",
     ["test_06_a_generated_name_avoids_a_local_name_of_the_tree"]),

    ("M6 the auto-numbering door skips the local-name rule", DOCK,
     "                node.ref = free_node_ref(node.kind, tree.name, node.ref,\n"
     "                                         tree.nodes, taken=self._used_refs(),\n"
     "                                         start=1)\n",
     "                node.ref = node.ref  # MUTATION: no local-name check\n",
     ["test_the_auto_number_skips_a_local_name_of_the_same_tree"]),

    ("M7 cosmetic control (a comment must change nothing)", AREA,
     "    chosen = list(selected or [])\n",
     "    chosen = list(selected or [])  # MUTATION: cosmetic\n",
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
