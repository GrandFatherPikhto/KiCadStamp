# tests/gui/test_gui_widget_parents.py
"""С1 of plan_2026_09_17_spoke_s2a_fixes.md: every CELL-EDITOR widget a GUI test
builds carries a Qt parent.

Why this is a guard and not a style preference. A QWidget created without a
parent is owned by Python alone, so it is destroyed from inside the garbage
collector; a widget that pulls in a whole editor page (CellAnchorView, and with
it the "Refs" tab, its table, its delegates and its fonts) makes that
destruction expensive enough to matter — the full Windows run died with
`0xC0000409` (abort), far away from the offending test, while the same suite was
green on Linux (measured 2026-09-17, plan 2а §1.1). Handing the widget to the
test's own QMainWindow gives Qt the ownership, exactly as adding it to a tab
widget does in the real page: that is the one-line fix, and this guard is what
keeps it from being forgotten again.

The plan's first form of this guard was to cover `test_cell_refs_tab.py` only
(18 sites were found without a parent: 3 there, 15 predating stage 2). Denis
chose the stronger form — the whole `tests/gui`, no exception list. After stage
2 EVERY `CellAnchorView` carries a Refs tab, so the other sites are the same
accumulation waiting to happen.

An AST, not a text search: a call spread over several lines has to be caught
too, and a widget name mentioned in a docstring must not be.

Mutation check: drop `parent=main_window` from any `CellAnchorView` in
`tests/gui` and this test fails, naming file and line (plan 2а mutations М5 and
М5б).
"""
import ast

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUI_TESTS = _REPO_ROOT / "tests" / "gui"

# The two widgets that pull a whole editor page into a test process.
_WIDGETS = ("CellAnchorView", "RefsTabWidget")


def _called_name(node: ast.Call):
    """The plain name of the call's target, else None — `module.fn(...)` and
    `self.fn(...)` are not what this guard is about."""
    func = node.func
    return func.id if isinstance(func, ast.Name) else None


def _calls_without_parent(path: Path):
    """Yield (line, name) of every _WIDGETS(...) call in the file that passes no
    `parent=` — the shape that leaves the widget to the garbage collector."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in _WIDGETS:
            continue
        if any(kw.arg == "parent" for kw in node.keywords):
            continue
        yield node.lineno, name


def test_every_cell_editor_widget_in_the_gui_tests_has_a_qt_parent():
    offenders = []
    for path in sorted(_GUI_TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line, name in _calls_without_parent(path):
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: {name}(...)")

    assert offenders == [], (
        "these CellAnchorView/RefsTabWidget calls have no Qt parent, so the "
        "garbage collector owns the widget — pass parent=main_window, the same "
        "reason the _tab helper in test_cell_refs_tab.py does it "
        "(plan_2026_09_17_spoke_s2a_fixes Р2; without this the full Windows run "
        "aborts with 0xC0000409):\n  " + "\n  ".join(offenders))
