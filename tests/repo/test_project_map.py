# tests/repo/test_project_map.py
"""Guards for tools/project_map.py (plan_2026_10_05_project_map.md, Part 1).

The generator is stdlib-only and parses source with `ast`; these cells drive it
on the REAL repo root (known module, API/`__all__`, reverse edges, >800 flag) and
on a TEMP tree (a module with a syntax error, an in-function import). Output is
always written to tmp_path — NEVER to techdocs/.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "project_map", ROOT / "tools" / "project_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load()


def _module_line(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"| {name} |"):
            return line
    raise AssertionError(f"module row not found: {name}")


def _reverse_line(text: str, module: str) -> str:
    """The `- imported by:` line of `module` in the map's Imports section.

    The module's heading appears twice (Public API and Imports), so the search is
    scoped to the Imports section — the same section the live and the temp-tree
    cells below read."""
    body = text.split("## Imports (internal)\n", 1)[1].split("\n## ", 1)[0]
    block = body.split(f"### {module}\n", 1)[1].split("\n### ", 1)[0]
    for line in block.splitlines():
        if line.startswith("- imported by:"):
            return line
    raise AssertionError(f"no reverse-edge line for {module}")


# ── the real repo ───────────────────────────────────────────────────────────

def test_known_module_has_docstring_and_api():
    text = pm.build_map(ROOT)
    assert "| kicadstamp/selection_narrowing.py |" in text
    assert "Narrow a MIXED board selection" in text
    assert "- `choose_instance` — " in text


def test_private_outside_all_is_not_in_api():
    text = pm.build_map(ROOT)
    # `_is_own_key` is private and NOT listed in selection_narrowing's __all__
    assert "- `_is_own_key` — " not in text


def test_reverse_edge_names_every_known_importer():
    """The live map NAMES its importers: the `- imported by:` line of the kernel's
    read rule contains each KNOWN importer.

    Deliberately a SUBSET — the subject is "the map shows the reverse edge", not
    "the module has exactly these importers": pinning the full set made this guard
    redden on every legitimate new import (доделка 2 of Р3а-4). The EXACT property
    is pinned on a TEMP tree below, where the importer set is the test's own."""
    text = pm.build_map(ROOT)
    line = _reverse_line(text, "kicadstamp/selection_narrowing.py")
    for importer in ("gui.docks.cell_anchor_view", "gui.docks.cell_editor",
                     "gui.explode_guard", "gui.mixed_selection",
                     "gui.select_cell"):
        assert importer in line, f"{importer} missing from: {line}"


def test_reverse_edge_is_exact_on_a_temp_tree(tmp_path):
    """The EXACT property, on a tree the test owns: `a` imports `b` -> b's own
    line is exactly "imported by: a" while `a` (imported by nobody) says "—".

    This is what keeps the SUBSET cell above honest: drop the reverse edges from
    the generator and THIS cell goes red (a mutation row of Р3а-4)."""
    _write(tmp_path, "kicadstamp/b.py", '"""b."""\n')
    _write(tmp_path, "kicadstamp/a.py", '"""a."""\n\nfrom . import b\n')
    text = pm.build_map(tmp_path)
    assert _reverse_line(text, "kicadstamp/b.py") == "- imported by: kicadstamp.a"
    assert _reverse_line(text, "kicadstamp/a.py") == "- imported by: —"


def test_big_file_is_flagged_and_small_is_not():
    text = pm.build_map(ROOT)
    assert "⚠ >800" in _module_line(text, "gui/docks/trees_dock.py")
    assert "⚠ >800" not in _module_line(text, "kicadstamp/selection_narrowing.py")


def test_output_is_byte_stable():
    assert pm.build_map(ROOT) == pm.build_map(ROOT)


def test_module_rows_are_sorted():
    text = pm.build_map(ROOT)
    section = text.split("### kicadstamp/\n", 1)[1].split("\n### ", 1)[0]
    rows = [ln.split(" | ")[0][2:] for ln in section.splitlines()
            if ln.startswith("| kicadstamp/")]
    assert rows and rows == sorted(rows)


# ── a temp tree: `__all__`, in-function import, syntax error ────────────────

def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_all_exports_a_private_name(tmp_path):
    _write(tmp_path, "kicadstamp/m.py",
           '"""doc."""\n__all__ = ["_priv", "pub"]\n\n'
           'def _priv():\n    """p"""\n\n\n'
           'def pub():\n    """q"""\n\n\n'
           'def _hidden():\n    """h"""\n')
    text = pm.build_map(tmp_path)
    assert "- `_priv` — " in text
    assert "- `pub` — " in text
    assert "- `_hidden` — " not in text


def test_import_inside_a_function_is_an_edge(tmp_path):
    _write(tmp_path, "kicadstamp/b.py", '"""b."""\n')
    _write(tmp_path, "kicadstamp/a.py",
           '"""a."""\n\n\ndef go():\n    from . import b\n    return b\n')
    text = pm.build_map(tmp_path)
    assert "- imports: kicadstamp.b" in text


def test_syntax_error_is_reported_not_raised(tmp_path):
    _write(tmp_path, "kicadstamp/bad.py", "def broken(:\n    pass\n")
    text = pm.build_map(tmp_path)  # must not raise
    assert "не разобран:" in text
    assert "kicadstamp/bad.py" in text
