# kicadstamp/diagnostics/run_check_json_mutations_2026_10_26.py
"""Mutation rows for Ч2б-1 — the `--json PATH` report of the four `tools/check_*.py` scripts.

Guards: tests/checks/test_check_json_schema.py (cells C1–C6). Each row removes ONE promise
and must turn a NAMED cell red:

  M48 `--json` prints the report to STDOUT and writes no file  (kicadstamp/check_report.py `write`)
  M49 `result.exit` is always 0                                (tools/check_placement.py, report call)
  M50 a problem's `text` is assembled a SECOND time            (tools/check_config_records.py)
  M51 `key` carries the clock instead of the finding           (kicadstamp/check_report.py `problem`)
  M52 `refs` carry the library id instead of the part          (tools/check_footprints_vs_library.py)
  M57 `entity_nodes` looks on ANCHORS again (Д-1)              (tools/check_placement.py)
  M58 a problem is listed TWICE, counted once (Д-2)            (tools/check_footprints_vs_library.py)
  M59 cosmetic control (a comment must change nothing)

The plan numbers M53–M55 belong to Ч2б-2 (the GUI tree of sections/problems): there is no
code for them yet, so they are NOT here — a row whose template matches nothing is a MISS,
and a "row" for absent code would be a lie. They arrive with that step.

Safeguards as everywhere in this package: a template that matches more than once is refused
(rule 38), a row that reddens NOTHING is a MISS, the control must survive, and every file is
restored in `finally` with the restore verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_check_json_mutations_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REPORT = "kicadstamp/check_report.py"
PLACEMENT = "tools/check_placement.py"
RECORDS = "tools/check_config_records.py"
FOOTPRINTS = "tools/check_footprints_vs_library.py"

GUARDS = "tests/checks/test_check_json_schema.py"

CELL_C1 = "test_json_mode_leaves_the_human_report_byte_identical"
CELL_C2 = "test_json_result_matches_the_exit_code"
CELL_C3 = "test_every_problem_text_appears_verbatim_in_the_report"
CELL_C4 = "test_kinds_are_from_the_vocabulary_and_keys_are_stable"
CELL_C6 = "test_refs_of_every_check_exist_on_its_fixture"
CELL_C4_DEFAULT_KEY = "test_a_problem_without_a_key_gets_one_from_its_kind_and_text"
CELL_ENTITY_NODE = "test_the_entity_to_node_is_the_placement_node_of_its_name"
CELL_PLACEMENT_NODE = "test_the_placement_to_node_is_the_entitys_placement_node"
CELL_ADDS_UP = "test_the_sections_add_up_to_the_problem_count"

MUTATIONS = [
    ("M48 --json prints to stdout instead of writing a file", REPORT,
     '    target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\\n",\n'
     '                      encoding="utf-8")\n',
     '    print(json.dumps(data, ensure_ascii=False, indent=1))  # MUTATION: stdout\n',
     [CELL_C1]),

    ("M49 result.exit is always 0", PLACEMENT,
     '            result={"exit": exit_code, "problems": len(issues),\n',
     '            result={"exit": 0, "problems": len(issues),  # MUTATION: always clean\n',
     [CELL_C2]),

    ("M50 a problem's text is assembled a second time", RECORDS,
     '                found["entities"].append(check_report.problem(\n'
     '                    verdict, line, key=f"{verdict}|{e.name}|{role}", refs=refs,\n',
     '                found["entities"].append(check_report.problem(\n'
     '                    verdict, f"{verdict} {role}",'
     '  # MUTATION: a second wording\n'
     '                    key=f"{verdict}|{e.name}|{role}", refs=refs,\n',
     [CELL_C3]),

    # The DEFAULT key is what every script passes `key=` around today, so the mutation is
    # pinned by the cell that exercises the fallback directly (measured: the parametrized
    # C4 stayed green, because no fixture uses the default path).
    ("M51 key carries the clock", REPORT,
     '    return {"key": key or f"{kind}|{text}", "kind": kind, "text": text,\n',
     '    return {"key": key or f"{kind}|{time.time()}|{text}", "kind": kind,'
     ' "text": text,  # MUTATION\n',
     [CELL_C4_DEFAULT_KEY]),

    ("M52 refs carry the library id, not the part", FOOTPRINTS,
     '                "MODELS" if got[0] < want[0] else "POLY", line,\n'
     '                key=f"DMG|{ref}|" + ("|".join(problems)), refs=[ref],\n',
     '                "MODELS" if got[0] < want[0] else "POLY", line,\n'
     '                key=f"DMG|{ref}|" + ("|".join(problems)), refs=[lib_id],'
     '  # MUTATION: not a ref\n',
     [CELL_C6]),

    # Д-1 (26.10): the address is the placement node whose ref IS the entity's name. Looking
    # on ANCHORS is exactly what found ZERO addresses on the live profile (73 of 73 are
    # placement nodes), so the mutation must redden BOTH cells that assert the address.
    ("M57 entity_nodes looks on anchors again", PLACEMENT,
     '            ref = getattr(node, "ref", None)\n'
     '            if ref:\n'
     '                found[ref].append({"tree": tree_name, "node": ref})\n',
     '            anchor = getattr(node, "anchor", None)  # MUTATION: the anchor lookup\n'
     '            role = getattr(anchor, "role", None) if anchor is not None else None\n'
     '            if role:\n'
     '                found[role].append({"tree": tree_name, "node": node.ref})\n',
     [CELL_ENTITY_NODE, CELL_PLACEMENT_NODE]),

    # Д-2 (26.10): `result.problems` is the sum over the sections. A finding listed twice is
    # the cheapest way to break that without touching a single printed line.
    ("M58 a problem is listed twice, counted once", FOOTPRINTS,
     '                key=f"DMG|{ref}|" + ("|".join(problems)), refs=[ref],\n'
     '                where={"ref": ref, "library": lib_id}))\n',
     '                key=f"DMG|{ref}|" + ("|".join(problems)), refs=[ref],\n'
     '                where={"ref": ref, "library": lib_id}))\n'
     '            found["footprints"].append(found["footprints"][-1])'
     '  # MUTATION: listed twice\n',
     [CELL_ADDS_UP]),

    ("M59 cosmetic control (a comment must change nothing)", REPORT,
     '    target = Path(path)\n',
     '    target = Path(path)  # MUTATION: cosmetic\n',
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
