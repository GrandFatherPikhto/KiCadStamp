# tests/test_diagnostics_module_names.py
"""Guard for the bug class the 2026-09-12 rename fixed.

kicadstamp/diagnostics/test_custom_fields.py announced itself as
"transform_template.py — transform a spoke template" and kept doing so for
about six weeks (its contents were replaced by the 2026-07-30
"Renaming project to KiCadStamp" commit, and the 2026-08-28 YAML→s-expr pass
edited it without noticing). docs/diagnostics.md, docs/diagnostics_ru.md,
docs/commands.md and docs/commands_ru.md therefore described a live Role-field
probe that did not exist any more.

The invariant checked here is deliberately narrow: a diagnostics module whose
docstring's FIRST line names a ``*.py`` file must name ITSELF. The 8 modules
whose first line is a plain description opt out and are never forced into a
naming convention — the test only stops a module from claiming to be another
module.
"""
import ast
import re
from pathlib import Path

_DIAGNOSTICS_DIR = (Path(__file__).resolve().parent.parent
                    / "kicadstamp" / "diagnostics")
# "some_name.py — ..." / "some_name.py - ..." (an em/en dash or a hyphen)
_SELF_NAME_RE = re.compile(r"^([A-Za-z0-9_]+\.py)\s*[—–-]")


def _docstring_first_line(path: Path) -> str:
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    lines = docstring.strip().splitlines()
    return lines[0].strip() if lines else ""


def _modules_declaring_a_name() -> list[tuple[str, str, str]]:
    """[(actual filename, filename the docstring claims, first docstring line)]
    for every diagnostics module that opens by naming a .py file."""
    declared = []
    for path in sorted(_DIAGNOSTICS_DIR.glob("*.py")):
        first_line = _docstring_first_line(path)
        match = _SELF_NAME_RE.match(first_line)
        if match:
            declared.append((path.name, match.group(1), first_line))
    return declared


def test_diagnostics_modules_that_name_a_file_name_themselves():
    declared = _modules_declaring_a_name()
    # Sanity: the convention is alive, so the loop below is not vacuous.
    assert len(declared) > 10

    wrong = [(actual, claimed, first)
             for actual, claimed, first in declared if actual != claimed]
    assert wrong == [], (
        "diagnostics docstring names a different module than the file it lives "
        f"in: {wrong}")
