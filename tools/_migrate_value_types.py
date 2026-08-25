#!/usr/bin/env python3
"""One-off import migration: kipy value types -> domain geometry types.

Consumers keep the SAME names (Vector2/Angle/BoardLayer) and API, so this is a
pure import-line swap. Relative import depth is derived from the file location
under kicadstamp/.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent / "kicadstamp"

# Files/dirs excluded: the seam (kicad/, domain/), utils/layers.py (already
# migrated), and diagnostics/ (intentional kipy probes).
EXCLUDE_PARTS = {"kicad", "diagnostics", "domain"}


def rel_prefix(path: Path) -> str:
    depth = len(path.relative_to(ROOT).parts) - 1  # 0 for top-level, 1 for subdir
    return "." if depth == 0 else "." * (depth + 1)


GEOM = [
    "from kipy.geometry import Vector2, Angle",
    "from kipy.geometry import Angle, Vector2",
    "from kipy.geometry import Vector2",
    "from kipy.geometry import Angle",
]
BOARD = ["from kipy.board_types import BoardLayer"]


def main() -> None:
    changed = 0
    for path in sorted(ROOT.rglob("*.py")):
        if any(p in path.parts for p in EXCLUDE_PARTS):
            continue
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        prefix = rel_prefix(path)
        for line in GEOM:
            text = text.replace(line, line.replace("from kipy.geometry import ",
                                                  f"from {prefix}domain.geometry import "))
        for line in BOARD:
            text = text.replace(line, line.replace("from kipy.board_types import ",
                                                  f"from {prefix}domain.geometry import "))
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
    print(f"migrated {changed} production files")


if __name__ == "__main__":
    main()
