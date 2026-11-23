# kicadstamp/diagnostics/run_nested_clone_sheet_mutations_2026_11_23.py
"""Mutation rows for «a component in a NESTED clone has its own instance» —
plan_2026_11_23_nested_clone_sheet_instances (Т1–Т3).

The contract the rows attack: an instance is the SHORTEST chain prefix whose
sheet file occurs exactly once under it (Р1), written as the joined names
(Р2), matched by `sheet_in_path` as a PREFIX (Р3), with the old first-segment
answer kept wherever there is no hierarchy (Р4), and one yellow line for an OLD
address that now names several instances (Р5).

  M1 the instance is always the first segment
  M2 the address keeps only the deepest name (no path)
  M3 экз counts direct children only (no descent)
  M4 a multi-segment address matches any segment, anywhere
  M5 the hierarchy is dropped on the way to the caller (reead)
  M6 Р4 removed: no hierarchy means «no clones»
  M7 the Р5 line is never printed
  M8 экз is counted over schematic_files instead of the root walk (Denis)
  M9 Imprint compares strings again instead of sheet_in_path (Denis)
  K  cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than
once is REFUSED; a row that reddens nothing is a MISS; the control must
survive; every file is restored in `finally` and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_nested_clone_sheet_mutations_2026_11_23`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SHEETS = "kicadstamp/sheet_names.py"
REEAD = "gui/docks/reead.py"
VALID = "kicadstamp/validation.py"
IMPRINT = "kicadstamp/imprint_apply.py"

INSTANCES = "tests/schematic/test_nested_clone_instances.py"
HIERARCHY = "tests/schematic/test_sheet_hierarchy.py"

GUARDS = f"{INSTANCES} {HIERARCHY}"


def _only(expr: str, files: str = INSTANCES) -> str:
    """A per-row guard: ONE file and ONE cell expression, so `-x` cannot stop
    the run on a NEIGHBOUR cell before the row's own cell is reached."""
    return f"{files} -k {expr}"

ADDRESS_TAIL = (
    "        prefix = [sheet_names.get(uuid) for uuid in chain[:index + 1]]\n"
    "        if any(not segment for segment in prefix):\n"
    "            return None\n"
    "        return \"/\".join(prefix)\n")

MUTATIONS = [
    ("M1 the instance is always the first segment", SHEETS,
     ADDRESS_TAIL,
     "        prefix = [sheet_names.get(uuid) for uuid in chain[:index + 1]]\n"
     "        if any(not segment for segment in prefix):\n"
     "            return None\n"
     "        return \"/\".join(prefix[:1])      # MUTATION\n",
     ["test_the_two_nested_clones_get_two_different_addresses"],
     _only("the_two_nested_clones_get_two_different_addresses")),

    ("M2 the address keeps only the deepest name", SHEETS,
     ADDRESS_TAIL,
     "        prefix = [sheet_names.get(uuid) for uuid in chain[:index + 1]]\n"
     "        if any(not segment for segment in prefix):\n"
     "            return None\n"
     "        return prefix[-1]                 # MUTATION: no path\n",
     ["test_a_clone_of_a_clone_names_every_hop"],
     _only("clone_of_a_clone")),

    # The plan named ш4б for this row, but that cell CANNOT see the mutant: an
    # undercounted экз only pushes the answer DEEPER, and ш4б's correct answer
    # is already the full chain. The cell that distinguishes it is the channel
    # whose expected answer is ONE name (k = 0): direct-children-only makes
    # экз(ch, dac) = 0 and the address grows to «Channel_0/DAC».
    ("M3 экз counts direct children only", SHEETS,
     "        for child in self.children.get(file, ()):\n"
     "            if child in chain or child == file:\n",
     "        for child in ():                      # MUTATION: no descent\n"
     "            if child in chain or child == file:\n",
     ["test_one_shared_channel_file_still_gives_one_name"],
     _only("shared_channel_file_still_gives_one_name")),

    ("M4 a multi-segment address matches any segment, anywhere", SHEETS,
     "    if \"/\" in sheet:\n"
     "        wanted = tuple(part for part in sheet.split(\"/\") if part)\n"
     "        if not wanted:\n"
     "            return False\n"
     "        return len(names) >= len(wanted) and all(\n"
     "            names[i] == part for i, part in enumerate(wanted))\n",
     "    if \"/\" in sheet:\n"
     "        wanted = tuple(part for part in sheet.split(\"/\") if part)\n"
     "        if not wanted:\n"
     "            return False\n"
     "        return all(part in names for part in wanted)   # MUTATION\n",
     ["test_a_multi_segment_address_matches_a_prefix_and_nothing_else"],
     _only("multi_segment_address_matches_a_prefix")),

    ("M5 the hierarchy is dropped on the way to the caller", REEAD,
     "    raw = getattr(fp, \"fp\", None)\n"
     "    if raw is not None and getattr(sheet_names, \"hierarchy\", None) is not None:\n",
     "    raw = getattr(fp, \"fp\", None)\n"
     "    if False:                                 # MUTATION: Р4 always\n",
     ["test_reead_accepts_a_fully_selected_nested_clone"],
     _only("reead_accepts_a_fully_selected_nested_clone")),

    ("M6 Р4 removed: no hierarchy means «no clones»", SHEETS,
     "    if hierarchy is None:\n"
     "        return sheet_of_names(names)\n",
     "    if hierarchy is None:\n"
     "        return None                           # MUTATION: Р4 gone\n",
     ["test_the_same_component_on_a_plain_dict_keeps_the_old_answer"],
     _only("plain_dict_keeps_the_old_answer")),

    ("M7 the Р5 line is never printed", VALID,
     "        key = (kind, name, sheet, tuple(matches))\n"
     "        if key in _ambiguous_warned:\n"
     "            continue\n",
     "        key = (kind, name, sheet, tuple(matches))\n"
     "        if True:                              # MUTATION: never say it\n"
     "            continue\n",
     ["test_an_old_address_that_now_names_two_instances_gets_one_yellow_line"],
     _only("one_yellow_line")),

    ("M8 экз is counted over schematic_files instead of the root walk", SHEETS,
     "            self._hierarchy = build_sheet_hierarchy(self._root_sheet)\n",
     "            self._hierarchy = build_sheet_hierarchy(\n"
     "                self._schematic_files[0] if self._schematic_files else None)\n",
     ["test_the_hierarchy_does_not_need_schematic_files"],
     _only("does_not_need_schematic_files", HIERARCHY)),

    ("M9 Imprint compares strings again", IMPRINT,
     "    onto = bool(dst_name) and not sheet_in_path(src_chain, dst_name)\n",
     "    onto = bool(dst_name) and dst_name != src_name    # MUTATION\n",
     ["test_an_imprint_inside_the_channel_it_names_is_in_place"],
     _only("inside_the_channel_it_names")),

    ("K cosmetic control", SHEETS,
     "def _address_of(chain: Iterable[str], hierarchy: SheetHierarchy,\n",
     "# a comment changes nothing\n"
     "def _address_of(chain: Iterable[str], hierarchy: SheetHierarchy,\n",
     []),
]


def _run(guards: str = GUARDS) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards.split(), "-x", "-q",
         "--no-header", "-p", "no:randomly"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    red = 0
    misses = []
    for row in MUTATIONS:
        name, rel, old, new, expected = row[:5]
        guards = row[5] if len(row) > 5 else GUARDS
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[SKIP/MISS] {name}: matches {hits} times — template must match exactly once")
            misses.append(name)
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            code, out = _run(guards)
        finally:
            path.write_text(text, encoding="utf-8")
            restored = path.read_text(encoding="utf-8") == text
        if not restored:
            print(f"[FAIL] {name}: the file was NOT restored")
            return 2
        if expected:
            failed = [cell for cell in expected if f"FAILED {cell}" not in out
                      and f"::{cell}" not in out]
            if failed or code == 0:
                print(f"[MISS] {name}: nothing went red ({failed or 'whole run green'})")
                misses.append(name)
            else:
                red += 1
                print(f"[red] {name}")
        else:
            if code == 0:
                print(f"[control survives] {name}")
            else:
                print(f"[MISS] {name}: the cosmetic control reddened the run")
                misses.append(name)
    print(f"\nverdict: {red} as expected, {len(misses)} miss(es)")
    for miss in misses:
        print(f"  - {miss}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
