#!/usr/bin/env python3
"""One-off import migration for tests: kipy value types -> domain geometry."""
from pathlib import Path

ROOT = Path(__file__).parent.parent / "tests"

DOMAIN = "kicadstamp.domain.geometry"


def migrate_line(line: str) -> list[str]:
    """Return the replacement line(s) for one import line."""
    # kipy.geometry -> domain geometry (same names)
    for name in ("Vector2, Angle", "Angle, Vector2", "Vector2", "Angle"):
        if line == f"from kipy.geometry import {name}":
            return [f"from {DOMAIN} import {name}"]

    if line.startswith("from kipy.board_types import "):
        names = [n.strip() for n in line[len("from kipy.board_types import "):].split(",") if n.strip()]
        if "BoardLayer" in names:
            rest = [n for n in names if n != "BoardLayer"]
            out = []
            if rest:
                out.append("from kipy.board_types import " + ", ".join(rest))
            out.append(f"from {DOMAIN} import BoardLayer")
            return out

    return [line]


def main() -> None:
    changed = 0
    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "conftest.py":
            # conftest has kipy imports that the fixtures need; migrate too.
            pass
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        modified = False
        for line in lines:
            replacements = migrate_line(line)
            out.extend(replacements)
            if replacements != [line]:
                modified = True
        if modified:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
            changed += 1
    print(f"migrated {changed} test files")


if __name__ == "__main__":
    main()
