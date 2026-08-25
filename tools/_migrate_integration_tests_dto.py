#!/usr/bin/env python3
"""One-off DTO migration for integration tests (run against a live KiCad).

Updates the adapter-facing attribute reads to the domain-DTO names introduced
by the P1-4 refactor, while leaving raw-kipy reads (adapter._board.get_tracks()
etc.) untouched.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent / "tests" / "integration_tests"

REPLACEMENTS = [
    ("str(created[0].id.value)", "created[0].uuid"),
    ("str(created2[0].id.value)", "created2[0].uuid"),
    ("str(v.id.value)", "v.uuid"),
    ("fp.reference_field.text.value", "fp.ref"),
]


def main() -> None:
    changed = 0
    for p in sorted(ROOT.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        original = text
        for old, new in REPLACEMENTS:
            text = text.replace(old, new)
        if text != original:
            p.write_text(text, encoding="utf-8")
            changed += 1
    print(f"migrated {changed} integration test files")


if __name__ == "__main__":
    main()
