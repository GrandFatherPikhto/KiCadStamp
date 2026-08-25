#!/usr/bin/env python3
"""Fix the stray closing parens the first migration pass left after unwrapping
`Angle.from_degrees(...)` / `int(... * MM)` on the DTO fields."""
from pathlib import Path
import re

ROOT = Path(__file__).parent.parent / "tests"

# `X.field = value)` where value is a bare identifier/number (not a call) —
# drop the trailing paren. Field names limited to the four we rewrote.
STRAY = re.compile(
    r"^(.*\.(?:angle_deg|drill_mm|diameter_mm|width_mm)\s*=\s*(?:[A-Za-z_][\w.]*|\d+(?:\.\d+)?))\s*\)(\s*(?:#.*)?\s*)$",
    re.MULTILINE,
)


def fix(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    fixed = STRAY.sub(r"\1\2", text)
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
        return 1
    return 0


def main() -> None:
    changed = 0
    for p in sorted(ROOT.rglob("*.py")):
        if "integration_tests" in p.parts or p.name == "conftest.py":
            continue
        changed += fix(p)
    print(f"fixed {changed} test files")


if __name__ == "__main__":
    main()
