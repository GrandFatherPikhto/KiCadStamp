# kicadstamp/diagnostics/deepseek_mutations_remove_placer_ch1v1_2026_10_12.py
"""Mutation rig for Ч1в-1 (the trees-dock class-Б translation).

Rule 38 discipline (deepseek.md): a "translated" cell must still KILL the product
line it guards — otherwise the translation silently disarmed it. This rig spoils
ONE product line at a time and runs the very cells the slice re-pointed at the
live `entities:`/`kind placement` shape; a mutation that the cells do NOT catch
is a red flag (a disarmed cell), a mutation they DO catch is "killed".

Mutations (name, file, old, new, expected):
  M1  `_all_ref_candidates` stops filtering by `_PLACEABLE_KINDS` -> a net_trace/
      thermal_via name would leak into the node dialog's ref candidates. The
      KIND_FILTER_CFG cells (now on entities/coordinate/points) must fail.
  M2  the auto mode stops PREFIXING a cross-section name collision -> the dialog
      would show a bare colliding name (fatal at link_trees). The collision cells
      must fail.
  K1  a cosmetic comment-only edit -> must SURVIVE (control: the rig is not
      trivially red).

Run:  .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_remove_placer_ch1v1_2026_10_12.py
READ-ONLY apart from the temporary single-line edit, always reverted in finally.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PLACEABLE_LINE = "                if r.kind in _PLACEABLE_KINDS]"
_PLACEABLE_MUT = ('                if r.kind in _PLACEABLE_KINDS '
                  'and r.kind != "coordinate"]')

_PREFIX_LINE = "            if name not in seen:"
_PREFIX_MUT = "            if True:  # mutation: keep every duplicate"

# (name, file, old, new, expected, node ids)
MUTATIONS = [
    ("M1 candidates lose the coordinate section",
     "gui/docks/trees_dock.py",
     _PLACEABLE_LINE, _PLACEABLE_MUT, "killed",
     ["tests/gui/docks/test_trees_dock.py::test_all_ref_candidates_returns_kind_name_pairs"]),
    ("M2 all_ref_names stops deduping a cross-section collision",
     "gui/docks/trees_dock.py",
     _PREFIX_LINE, _PREFIX_MUT, "killed",
     ["tests/gui/docks/test_trees_dock.py::test_all_ref_names_dedups_cross_section_collision"]),
    ("K1 cosmetic comment",
     "gui/docks/trees_dock.py",
     "for the 4 placeable kinds' record names from build_records(cfg), in",
     "for the 4 placeable kinds' record names from build_records(cfg) (cosmetic), in",
     "survived",
     ["tests/gui/docks/test_trees_dock.py::test_all_ref_candidates_returns_kind_name_pairs"]),
]


def _run(ids: list[str]) -> bool:
    """True when the selected cells PASS."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-x", *ids],
        cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0


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
            green = _run(ids)
        finally:
            path.write_text(src, encoding="utf-8")
        got = "survived" if green else "killed"
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
