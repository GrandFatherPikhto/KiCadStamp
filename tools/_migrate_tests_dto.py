#!/usr/bin/env python3
"""One-off mechanical migration of test fakes from kipy attribute names to the
domain-DTO attribute names introduced by the P1-4 DTO refactor.

Reproducible (re-runnable) and idempotent: each replacement only fires when the
old pattern is present. Does NOT touch integration_tests/ (needs live KiCad) nor
conftest files.
"""
from pathlib import Path
import re

ROOT = Path(__file__).parent.parent / "tests"

# (pattern, replacement) — applied in order. `re.sub` with plain strings.
REPLACEMENTS = [
    ("reference_field.text.value", "ref"),
    ("reference_field", "ref"),           # any remaining bare `fp.reference_field`
    (".orientation = Angle.from_degrees(", ".angle_deg = "),
    (".drill_diameter = int(", ".drill_mm = "),
    (".diameter = int(", ".diameter_mm = "),
    (".width = int(", ".width_mm = "),
    (".id.value", ".uuid"),
    (".net.name", ".net_name"),
    (".net = None", ".net_name = None"),
]

# strip the `* MM)` suffix left after the drill/diameter/width rewrites, e.g.
# `.drill_mm = 0.3 * MM)` -> `.drill_mm = 0.3)`  (value becomes mm, no multiply)
def _fix_mm_multipliers(text: str) -> str:
    return re.sub(r"(\.(?:drill_mm|diameter_mm|width_mm) = [^\n;]*?)\s*\*\s*MM\s*\)", r"\1)", text)


def migrate_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    text = _fix_mm_multipliers(text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return 1
    return 0


def main() -> None:
    changed = 0
    for path in sorted(ROOT.rglob("*.py")):
        if "integration_tests" in path.parts:
            continue
        if path.name == "conftest.py":
            continue
        changed += migrate_file(path)
    print(f"migrated {changed} test files")


if __name__ == "__main__":
    main()
