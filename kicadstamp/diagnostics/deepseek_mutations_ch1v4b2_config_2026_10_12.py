# kicadstamp/diagnostics/deepseek_mutations_ch1v4b2_config_2026_10_12.py
"""Rule-38 mutation harness for the Ч1в-4b-2 slice (plan_2026_10_08_remove_placer,
catalog ``tests/config``): the root ``clone_placements:`` carrier of these cells
was translated onto live sections (coordinate_placements / net_traces / entities /
nested Cell.clone_placements) — this harness proves the translation did NOT
defang them.

Grown from ``deepseek_mutations_duplicate_refdes_guard_2026_10_12.py`` (rule 38:
never start the harness from a clean sheet) — the same machinery and the SAME
protectors:

  * ``if src.count(old) != 1`` — a template that is not unique refuses to run
    (without it ``replace(old, new, 1)`` edits a FOREIGN line and the mutation
    reports a green guard that proved nothing);
  * a verdict with ZERO red tests is a MISS, not a kill;
  * restore is from a COPY, never ``git checkout`` (the tree holds this slice's
    uncommitted work).

Rows (each names the cell it kills):
  * M1 — ``config_rename._visit_cell_placement`` stops renaming refs:/nets: keys
    (the translated carrier): killed by the nested-refs/nets rename cells.
  * M2 — ``config_rename._visit_placement_kind`` stops renaming ``cluster:``:
    killed by the coordinate-placement cluster/name cell.
  * M3 — ``name_hint.close_name_hint`` always returns "": killed by the
    cell-reference typo hint cell (now carried by an ENTITY's ``cell:``).
  * M4 — ``nested_anchor_id`` stops composing the ``<outer>/<nested>`` path:
    killed by the through-real-step form check (the nested ``name:`` form
    disappears). NOTE (rule 39): the FIRST M4 candidate — collapsing
    ``entity_anchor_id`` onto one literal — was a MISS: that function feeds
    ``known_anchor_ids`` only, while the PLANNED key comes from
    ``clone_anchor_id``, so the form check never saw it.
  * C1 — a cosmetic comment. MUST survive.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_ch1v4b2_config_2026_10_12
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RENAME = ROOT / "kicadstamp/config_rename.py"
NAME_HINT = ROOT / "kicadstamp/config/name_hint.py"
CLONE_CALC = ROOT / "kicadstamp/placement/services/clone_position_calculator.py"

RENAME_TESTS = "tests/config/test_config_rename.py"
HINT_TESTS = "tests/config/test_format3_reference_hint.py"
THROUGH_TESTS = "tests/config/test_format3_through_real_step.py"

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("M1 nested refs:/nets: keys are no longer renamed", RENAME,
     "def _visit_cell_placement(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:\n"
     "    # Nested CellPlacement: role:/refs:/nets:, closed boundary (no anchor_*).\n"
     "    ctx.rename_value(entry, \"role\", f\"{location}.role\", \"Role\")\n"
     "    for field in (\"refs\", \"nets\"):",
     "def _visit_cell_placement(entry: Dict[str, Any], location: str, ctx: _Ctx) -> None:\n"
     "    # Nested CellPlacement: role:/refs:/nets:, closed boundary (no anchor_*).\n"
     "    ctx.rename_value(entry, \"role\", f\"{location}.role\", \"Role\")\n"
     "    for field in ():  # MUTATION: refs/nets no longer renamed",
     "die", [f"{RENAME_TESTS}::test_rename_refs_key_not_value",
             f"{RENAME_TESTS}::test_rename_nets_key"]),
    ("M2 the Cluster TAG is no longer renamed", RENAME,
     "    ctx.rename_value(entry, \"role\", f\"{location}.role\", \"Role\")\n"
     "    ctx.rename_value(entry, \"cluster\", f\"{location}.cluster\", \"Cluster\")",
     "    ctx.rename_value(entry, \"role\", f\"{location}.role\", \"Role\")\n"
     "    # MUTATION: ctx.rename_value(entry, \"cluster\", ..., \"Cluster\") removed",
     "die", [f"{RENAME_TESTS}::test_coordinate_placement_cluster_renamed_name_not"]),
    ("M3 the close-name suggestion is dropped", NAME_HINT,
     "    suggestion = close_name(name, known_names)\n"
     "    return (_(\" (did you mean {suggestion!r}?)\").format(suggestion=suggestion)\n"
     "            if suggestion else \"\")",
     "    suggestion = close_name(name, known_names)\n"
     "    return \"\"  # MUTATION: suggestion dropped",
     "die", [f"{HINT_TESTS}::test_a_cell_reference_typo_gets_the_same_hint"]),
    ("M4 the nested key path is not composed", CLONE_CALC,
     "    return f\"{anchor_id}/{nested_name}\"",
     "    return anchor_id  # MUTATION: nested path collapsed onto the outer key",
     "die", [f"{THROUGH_TESTS}::test_a_format2_graph_lifted_on_open_keeps_the_second_apply_empty"]),
    ("C1 cosmetic comment (control)", NAME_HINT,
     "import difflib",
     "import difflib  # control",
     "survive", [f"{HINT_TESTS}::test_a_cell_reference_typo_gets_the_same_hint"]),
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
            proc = _run(targets)
            red = proc.returncode != 0
            if expect == "die" and not red:
                failures.append(f"{mid}: MISS — no target cell went red")
            elif expect == "survive" and red:
                failures.append(f"{mid}: control was killed:\n{proc.stdout[-2000:]}")
            else:
                print(f"{mid}: {'KILLED' if red else 'survived'} "
                      f"({len(targets)} target(s))")
            path.write_text(src, encoding="utf-8")
            _drop_pyc(path)
    finally:
        for path, bak in backups.items():
            path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink(missing_ok=True)
            _drop_pyc(path)
    if failures:
        print("\n".join(failures))
        return 1
    print("all mutations behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
