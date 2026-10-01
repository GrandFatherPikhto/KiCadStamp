# tests/repo/test_i18n_import_guard.py
"""A module that calls gettext's ``_()`` must import ``_`` (Ф3.10 of
plan_2026_09_27_repo_and_tests_transformation).

This cell replaces the ad-hoc checker ``tools/_check_i18n.py``, which did the
same job by hand — and only for ``kicadstamp/**``. The property is what makes
``_("...")`` resolvable at import time: without the import, ``_`` is an unbound
name, so the module raises at import — not a missing catalogue entry, an
``AttributeError``/``NameError`` before anything runs. It is checked across all
three shipping packages: ``kicadstamp/``, ``gui/`` and ``mcp_server/``.

Import forms are taken from the code, not invented: ``kicadstamp/`` uses a
relative import (``from .i18n import _`` / ``from ..i18n import _``), while
``gui/`` and ``mcp_server/`` use the absolute one (``from kicadstamp.i18n
import _``). The guard accepts a binding of ``_`` from any module whose last
component is ``i18n``, so all three forms pass.

Rule 38: the scan proves it did not go blind — it must walk a plausible number
of modules AND at least one of them must actually call ``_()``. A renamed or
moved package would otherwise leave this guard green over an empty scan.
"""
import ast
from pathlib import Path

# Ф2.0: depth-independent (tests/paths.py) — this file lives in tests/repo/.
from tests.paths import REPO_ROOT

# The shipping packages whose modules must resolve `_` at import time (Ф3.10).
_SCANNED_PACKAGES = ("kicadstamp", "gui", "mcp_server")


def _scanned_sources() -> list[Path]:
    sources: list[Path] = []
    for package in _SCANNED_PACKAGES:
        sources.extend(sorted((REPO_ROOT / package).rglob("*.py")))
    return sources


def _calls_gettext(tree: ast.AST) -> list[int]:
    """Line numbers of real ``_("...")`` calls in one module.

    Mirrors ``tools/_check_i18n.py``'s ``_\\s*\\(["\\']`` — but on the AST, so a
    mention inside a comment or docstring is not mistaken for a call. The first
    argument must be a string literal, which is the only thing gettext accepts.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_"):
            continue
        if (node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            lines.append(node.lineno)
    return lines


def _imports_gettext(tree: ast.AST) -> bool:
    """True if the module binds the name ``_`` from an i18n module.

    Covers every form the tree actually uses: ``from .i18n import _``,
    ``from ..i18n import _`` and ``from kicadstamp.i18n import _`` — plus a
    defensive ``import <...>.i18n as _``.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module and node.module.split(".")[-1] == "i18n"
                    and any((alias.asname or alias.name) == "_"
                            for alias in node.names)):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname == "_" and alias.name.split(".")[-1] == "i18n":
                    return True
    return False


def _offenders() -> list[str]:
    """Modules that call ``_()`` but never import ``_`` (relpath + first line)."""
    offenders: list[str] = []
    for path in _scanned_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = _calls_gettext(tree)
        if calls and not _imports_gettext(tree):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(
                f"{rel} (first call at line {calls[0]}, {len(calls)} total)")
    return offenders


def test_every_module_that_calls_gettext_imports_it():
    """Ф3.10: the module calling ``_()`` is the module that imports ``_``."""
    offenders = _offenders()
    assert offenders == [], (
        "these modules call _() but never import _ — the name is unbound at "
        "import time. Import it (`from .i18n import _` / "
        "`from kicadstamp.i18n import _`):\n  " + "\n  ".join(offenders))


def test_the_scan_actually_walks_the_tree():
    """Antidote to a vacuous pass (rule 38): the three packages must be walked,
    and the gettext callers detected — otherwise the guard above is green over
    nothing."""
    sources = _scanned_sources()
    assert len(sources) > 100, (
        f"the scan walked only {len(sources)} modules — a package was renamed "
        "or moved, so the guard above measured nothing")

    callers = [
        path for path in sources
        if _calls_gettext(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert callers, (
        "no scanned module calls _() — the gettext scan has gone blind")
