# kicadstamp/diagnostics/run_mirrored_reread_mutations_2026_11_20.py
"""Mutation rows for "the re-read of a MIRRORED instance writes copper on the wrong layer"
(plan_2026_11_19_mirrored_reread_copper_layer).

The contract the rows attack: a re-read expresses the live geometry in the CELL's own frame,
so the LAYER has to travel the same way — `CellFrame.layer_to_cell` — and an inner layer stays
inner (utils.layers.mirror_layer, design Р16). The three readers that compare a live layer with
a cell layer all go through it: the copper matcher's grouping key, the imported track record,
and the new component record.

  M1 the copper matcher groups by the RAW live layer (an F.Cu record + its own B.Cu copper)
  M2 an imported track is written with the RAW live layer (layer "B.Cu" in an F.Cu cell)
  M3 a new component record is written with the RAW live layer
  M4 the cell-frame layer is a naive F/B swap (an INNER layer gets flipped too)
  K  cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once is REFUSED; a
row that reddens nothing is a MISS; the control must survive; every file is restored in `finally`
and verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_mirrored_reread_mutations_2026_11_20`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FRAME = "kicadstamp/cell_frame.py"
REFRESH = "kicadstamp/cell_geometry_refresh.py"

GUARDS = "tests/trees/test_cell_geometry_refresh.py"

MUTATIONS = [
    ("M1 the copper matcher groups by the RAW live layer", REFRESH,
     "        return frame.layer_to_cell(layer_to_str(item.layer))\n",
     "        return layer_to_str(item.layer)  # MUTATION: the raw live layer\n",
     ["test_a_a_mirrored_instance_pairs_its_own_tracks"]),

    ("M2 an imported track keeps the RAW live layer", REFRESH,
     "        live_layer = frame.layer_to_cell(layer_to_str(live.layer))\n",
     "        live_layer = layer_to_str(live.layer)  # MUTATION: the raw live layer\n",
     ["test_v_new_tracks_of_a_mirrored_instance_take_the_cells_frame"]),

    ("M3 a new component record keeps the RAW live layer", REFRESH,
     "            live_layer = frame.layer_to_cell(layer_to_str(fp.layer))\n",
     "            live_layer = layer_to_str(fp.layer)  # MUTATION: the raw live layer\n",
     ["test_g_a_new_component_of_a_mirrored_instance_takes_the_cells_frame"]),

    ("M4 the cell-frame layer is a naive F/B swap", FRAME,
     "        return mirror_layer(world_layer) if self.mirror else world_layer\n",
     "        return (\"B.Cu\" if world_layer == \"F.Cu\" else \"F.Cu\") \\\n"
     "            if self.mirror else world_layer  # MUTATION: inner layers flipped\n",
     ["test_b_an_inner_layer_stays_inner_on_a_mirrored_instance"]),

    ("K cosmetic control", FRAME,
     "    def layer_to_cell(self, world_layer: str) -> str:\n",
     "    # a comment changes nothing\n"
     "    def layer_to_cell(self, world_layer: str) -> str:\n",
     []),
]


def _run(guards: str = GUARDS) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards.split(), "-x", "-q", "--no-header"],
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
