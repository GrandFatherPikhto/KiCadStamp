# tools/generate_test_profile.py
"""Generate profiles/tests/test.yaml — a synthetic, large but VALID KiCadStamp
config for reproducible GUI-startup profiling, independent of any real board.

Run from the repo root:
    .venv\\Scripts\\python.exe tools\\generate_test_profile.py

The output passes kicadstamp.config.load_config() (it has no include:/schematic
references, so no .kicad_sch files are read — the cost it exercises is the
YAML parse + schema validation + tree building, which scale with entry count).

Sizes are constants below; edit and re-run to change the shape.
"""
import argparse
from pathlib import Path

import yaml

# Default entry counts — large enough to be a few thousand YAML lines and
# several hundred config records, small enough to parse in a second.
N_RULES = 400
N_CLONES = 300
N_COORDS = 300
N_THERMALS = 200
N_CELLS = 80


def build(n_rules, n_clones, n_coords, n_thermals, n_cells) -> dict:
    cells = {}
    for i in range(n_cells):
        cells[f"cell_{i}"] = {
            "components": [
                {
                    "role": f"R{i}_{j}",
                    "offset_along_mm": 0.0,
                    "offset_across_mm": 0.0,
                    "angle_deg": 0.0,
                }
                for j in range(2)
            ],
        }

    rules = []
    for i in range(n_rules):
        rules.append({
            "net": f"NET_{i % 16}",
            "name": f"rule_{i}",
            "anchor_ref": "IC1",
            "spokes": [
                {
                    "pad": str(i % 100 + 1),
                    "cell": f"cell_{i % n_cells}",
                    "shift_x_mm": 0.0,
                    "shift_y_mm": 0.0,
                    "rotation_deg": 0.0,
                }
            ],
        })

    clones = [
        {"name": f"clone_{i}", "role": f"ROLE_{i}", "xy": [float(i), 0.0]}
        for i in range(n_clones)
    ]

    coords = [
        {
            "name": f"coord_{i}",
            "cluster": f"CL_{i}",
            "role": f"ROLE_{i}",
            "x_mm": float(i),
            "y_mm": 0.0,
            "rotation_deg": 0.0,
        }
        for i in range(n_coords)
    ]

    thermals = [
        {
            "name": f"thermal_{i}",
            "anchor_ref": "IC1",
            "pad": str(i % 100 + 1),
            "net": "GND",
            "rows": 4,
            "cols": 4,
            "margin_mm": 0.5,
            "pattern": "grid",
            "drill_mm": 0.3,
            "diameter_mm": 0.5,
        }
        for i in range(n_thermals)
    ]

    return {
        "layer": "B.Cu",
        "cells": cells,
        "rules": rules,
        "clone_placements": clones,
        "coordinate_placements": coords,
        "thermal_via_arrays": thermals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate profiles/tests/test.yaml")
    parser.add_argument("--rules", type=int, default=N_RULES)
    parser.add_argument("--clones", type=int, default=N_CLONES)
    parser.add_argument("--coords", type=int, default=N_COORDS)
    parser.add_argument("--thermals", type=int, default=N_THERMALS)
    parser.add_argument("--cells", type=int, default=N_CELLS)
    args = parser.parse_args()

    out = Path("profiles/tests/test.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build(args.rules, args.clones, args.coords, args.thermals, args.cells)
    with open(out, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"Wrote {out} ({len(data['rules'])} rules, {len(data['clone_placements'])} "
          f"clone_placements, {len(data['coordinate_placements'])} coordinate_placements, "
          f"{len(data['thermal_via_arrays'])} thermal_via_arrays, {len(data['cells'])} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
