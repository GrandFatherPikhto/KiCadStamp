# kicadstamp/diagnostics/deepseek_mutations_duplicate_refdes_guard_2026_10_12.py
"""Rule-38 mutation harness for the duplicate-refdes guard заход
(plan_2026_10_12_duplicate_refdes_guard).

Grown from ``deepseek_mutations_u32_2026_10_04.py`` (rule 38: never start the
harness from a clean sheet) — the same machinery, and the SAME protectors:

  * ``if src.count(old) != 1`` — a template that is not unique refuses to run.
    Without it a ``replace(old, new, 1)`` edits a FOREIGN line and the mutation
    reports a green guard that proved nothing (that is exactly how the Т2-1
    harness lost this protector on 22.09.2026);
  * a verdict with ZERO red tests is a MISS, not a kill;
  * restore is from a COPY, never ``git checkout`` (the tree holds this заход's
    uncommitted work).

Rows (each names the cell it kills):
  * M1 — the DOOR guard removed from ``ApplyPipeline.run`` (the item list is
    passed through untouched): killed by the door test.
  * M2 — the guard ignores an ENTRY's own refdes and reads the whole board's
    duplicates instead: killed by С3 "не мешает".
  * M3 — the detector calls a LONE footprint (one copy) a duplicate: killed by
    the "lone orphan does not refuse" test.
  * M4 — the detector's ``refs`` filter removed (the run's own roles no longer
    narrow it): killed by the detector-scope test.
  * M5 — the "Add entities" window falls back to the bare role reason: killed by
    the duplicate/orphan reason tests.
  * C1 — a cosmetic comment. MUST survive.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_duplicate_refdes_guard_2026_10_12
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PIPELINE = ROOT / "kicadstamp/apply_pipeline.py"
GUARD = ROOT / "kicadstamp/apply_duplicate_guard.py"
DETECTOR = ROOT / "kicadstamp/refdes_duplicates.py"
GUI = ROOT / "gui/docks/instance_candidates.py"

PIPELINE_TESTS = "tests/pipeline/test_duplicate_refdes_guard.py"
GUI_TESTS = "tests/gui/docks/test_duplicate_refdes_reason.py"

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("M1 the door guard is removed from run()", PIPELINE,
     "        self.items, self._duplicate_report = guard_duplicate_refdes(\n"
     "            self.adapter, self.items)",
     "        self._duplicate_report = []  # MUTATION: door guard removed",
     "die", [f"{PIPELINE_TESTS}::test_the_pipeline_door_itself_refuses_before_planning"]),
    ("M2 the guard reads the WHOLE board, not the run's refs", GUARD,
     "    wanted: set[str] = set()\n"
     "    for entry in entries:\n"
     "        wanted |= refs_of(entry)\n"
     "    if not wanted:\n"
     "        return entries, []\n"
     "    duplicates = duplicate_refdes_groups(adapter.get_footprints(), refs=wanted)\n"
     "    if not duplicates:\n"
     "        return entries, []\n"
     "    allowed: list = []\n"
     "    refused: list = []\n"
     "    for entry in entries:\n"
     "        ref = refdes_duplicate_of(refs_of(entry), duplicates)",
     "    duplicates = duplicate_refdes_groups(adapter.get_footprints())\n"
     "    if not duplicates:\n"
     "        return entries, []\n"
     "    allowed: list = []\n"
     "    refused: list = []\n"
     "    for entry in entries:\n"
     "        ref = next(iter(duplicates), None)  # MUTATION: whole board",
     "die", [f"{PIPELINE_TESTS}::test_duplicate_outside_the_items_own_refs_does_not_refuse"]),
    ("M3 a lone footprint counts as a duplicate", DETECTOR,
     "    return {ref: tuple(copies) for ref, copies in by_ref.items() if len(copies) > 1}",
     "    return {ref: tuple(copies) for ref, copies in by_ref.items() if len(copies) >= 1}  # MUTATION",
     "die", [f"{PIPELINE_TESTS}::test_lone_orphan_is_not_refused_by_the_pipeline"]),
    ("M4 the detector's refs filter is removed", DETECTOR,
     "        if wanted is not None and ref not in wanted:\n            continue",
     "        if False:  # MUTATION: the run's refs filter removed\n            continue",
     "die", [f"{PIPELINE_TESTS}::test_detector_restricts_itself_to_the_given_refs"]),
    ("M5 the dialog falls back to the bare role reason", GUI,
     "        reason = (duplicate_or_orphan_reason(members, duplicates)\n"
     "                  or role_mismatch_reason(got, want))",
     "        reason = role_mismatch_reason(got, want)  # MUTATION: duplicate reason ignored",
     "die", [GUI_TESTS]),
    ("C1 cosmetic comment (control)", GUARD,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control",
     "survive", [f"{PIPELINE_TESTS}::test_the_pipeline_door_itself_refuses_before_planning"]),
]


def _drop_pyc(path: Path) -> None:
    """Drop the bytecode of the MUTATED file — a same-second mtime with stale
    bytecode is the classic rig trap (rule 38)."""
    for cache in path.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--tb=no", *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def main() -> int:
    backups: dict[Path, Path] = {}
    for path in {m[1] for m in MUTATIONS}:
        bak = path.with_suffix(path.suffix + ".mutbak")
        shutil.copy(path, bak)
        backups[path] = bak
    failures: list[str] = []
    try:
        for mid, path, old, new, expect, targets in MUTATIONS:
            src = path.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            path.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(path)
            res = _run(targets)
            path.write_text(src, encoding="utf-8")
            _drop_pyc(path)
            # A zero-red verdict is a MUTATION MISS, not a kill (rule 38).
            killed = res.returncode != 0 and "failed" in res.stdout
            verdict = ("KILL" if killed else "MISS ") if expect == "die" \
                else ("SURVIVE" if not killed else "KILL(bad)")
            summary = (res.stdout.strip().splitlines() or ["<no output>"])[-1]
            print(f"{verdict:<9} {mid}  -> {summary}")
            if (expect == "die") != killed:
                failures.append(mid)
    finally:
        for path, bak in backups.items():
            shutil.copy(bak, path)
            _drop_pyc(path)
            bak.unlink(missing_ok=True)

    print()
    if failures:
        print("PROBLEM (surviving/missed):", *failures, sep="\n  ")
        return 1
    print("all mutations killed; sources restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
