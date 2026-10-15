# kicadstamp/diagnostics/deepseek_mutations_reread_uuid_2026_10_15.py
"""Mutation harness for Ч1 of plan_2026_10_15_reread_copper_uuid_and_selection_any
(«Reread inter-node copper» loses the record's uuid -> the write is refused).

Three mechanisms, one commit each, one row of mutations each:

  (1) CACHE  — `read_data` must hand out the caller's OWN dict (kicadstamp/
      config_writer.py::_read_data). Guards: tests/config/test_writer_reads_a_private_dict.py
      С1..С5.
  (2) IDENTITY — the writers carry the record's identity through
      kicadstamp/config/form_identity.py (added with that commit).
  (3) STAMP HOLE — format3's uuid inheritance keyed by the section's identity
      (name, else net) instead of `name` alone (added with that commit).

Safeguards (rule 38, taken from the earlier harnesses):
  * `if original.count(old) != 1` — refuse an ambiguous template (a mutation that
    silently patches the WRONG line reports GREEN and teaches nothing);
  * a verdict with ZERO failed rows is a MISS, not a kill (`no FAILED line`,
    `exit N` = the run could not even collect the tests);
  * `PYTHONDONTWRITEBYTECODE=1` + `__pycache__` sweeping — a stale `.pyc` once
    made a mutation look green;
  * the file is restored in a `finally`, and the restoration is VERIFIED by
    comparing the text, not assumed.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_reread_uuid_2026_10_15.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CW = "kicadstamp/config_writer.py"
GUARDS_1 = "tests/config/test_writer_reads_a_private_dict.py"

# Each row: name, file, OLD, NEW, expected-to-redden test ids (substring) or None.
MUTATIONS = [
    ("М1 read_data hands out the cache's own object", CW,
     "    return copy.deepcopy(cached_file_read(path, _uncached_read))",
     "    return cached_file_read(path, _uncached_read)",
     ["test_editing_what_read_data_returned_does_not_reach_the_cache",
      "test_a_writer_splice_keeps_the_uuid_on_a_warm_cache",
      "test_a_writer_splice_keeps_the_uuid_under_the_working_set"]),
    # NB: `return copy.deepcopy(staged)` alone matches TWO staged readers in this
    # module (the other one guards with `WORKING_SET.enabled and path is not
    # None`), so the template carries the guard line that identifies _read_data.
    ("М2 the staged branch stops copying", CW,
     "    if WORKING_SET.enabled:\n"
     "        staged = WORKING_SET.staged_content(str(path.resolve()))\n"
     "        if staged is not None:\n"
     "            return copy.deepcopy(staged)",
     "    if WORKING_SET.enabled:\n"
     "        staged = WORKING_SET.staged_content(str(path.resolve()))\n"
     "        if staged is not None:\n"
     "            return staged",
     ["test_editing_a_staged_read_does_not_touch_the_staged_content"]),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str, *extra: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", guards, "-q", "-rf", "-p", "no:cacheprovider",
         *extra],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list[str]:
    ids = []
    for line in out.splitlines():
        if line.startswith("FAILED "):
            ids.append(line.split(" ", 1)[1].split(" - ")[0])
    return ids


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS_1)
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
                print(f"[MISS] {name}: NO failed test — a mutation that kills "
                      f"nothing proves nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in (expect or []) if not any(e in f for f in failed)]
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
