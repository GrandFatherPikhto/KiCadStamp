# tests/repo/test_dialog_sizes.py
"""Every window of `gui/` must REMEMBER ITS SIZE — the guard for part Б of
plan_2026_11_07_dialog_sizes_and_row_shift.

The live complaint (Denis, 07.11): «окно якоря в Instances… каждый раз
открывается слопнутым — там, кстати, много таких окошек, которые не
сохраняются». The census behind the plan had been made by FILE, and that is
exactly the check that missed the window it was about: `gui/docks/
instances_dialog.py` DID call restore/persist — for its main dialog — while the
anchor window it opens was a bare `QDialog`. So this guard reads the BODY: the
class's own methods, or the function that builds an anonymous `QDialog`.

Three rules, each with a reason:

  * `class X(QDialog)` — its body calls `remember_dialog_size(...)` (both
    halves in one call) or the pair `restore_dialog_size` + `persist_dialog_size`.
    A half-pair is a failure on purpose: a dialog that restores but never
    persists looks right for exactly one launch, which is the defect itself.
  * `x = QDialog(...)` — an ANONYMOUS window has no class name to key its size
    on, so `dialog_size_key` would fall back to "QDialog" and every such window
    in the app would share one entry. The call must pass an explicit `key=`.
  * `_EXEMPT` — a window that deliberately has no size of its own, WITH the
    reason in words. An empty reason is a failure: an exemption without a reason
    is an unguarded dialog wearing a waiver.

The blind-guard alarm (rule 38) is the `>= MIN_DIALOGS` cell: a scan that
suddenly reads three windows must fail, not pass quietly.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.paths import REPO_ROOT as REPO

GUI_ROOT = "gui"

#: Windows the scan must keep finding (measured 07.11.2026: 21 classes + 3
#: anonymous `QDialog`s). A LOWER bound, not a target: it is here so a broken
#: scan cannot pass for a clean tree.
MIN_DIALOGS = 24

#: (path relative to the repo, window name) -> WHY it needs no remembered size.
_EXEMPT: dict[tuple[str, str], str] = {}

_REMEMBER = "remember_dialog_size"
_RESTORE = "restore_dialog_size"
_PERSIST = "persist_dialog_size"


def _called_names(node: ast.AST) -> set[str]:
    """Every function name CALLED anywhere under `node`."""
    out = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            out.add(inner.func.id)
    return out


def _calls_have_key(node: ast.AST, name: str) -> bool:
    """True when at least one call to `name` passes a `key=` keyword."""
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id == name):
            if any(kw.arg == "key" for kw in inner.keywords):
                return True
    return False


def _is_qdialog(node: ast.AST) -> bool:
    """`QDialog` written bare, or as `some.module.QDialog`."""
    if isinstance(node, ast.Name):
        return node.id == "QDialog"
    return isinstance(node, ast.Attribute) and node.attr == "QDialog"


def scan_dialogs(root: Path) -> list[dict]:
    """One entry per window found under `<root>/gui` — CLASSES and anonymous
    `QDialog(...)` assignments, with what the guard needs to judge them."""
    entries: list[dict] = []
    for path in sorted((root / GUI_ROOT).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # a broken file is some other guard's business
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    _is_qdialog(base) for base in node.bases):
                calls = set()
                for item in node.body:
                    calls |= _called_names(item)
                entries.append({
                    "file": rel,
                    "name": node.name,
                    "kind": "class",
                    "remembered": _REMEMBER in calls,
                    "paired": {_RESTORE, _PERSIST} <= calls,
                    "keyed": False,      # a class name IS the key
                })
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and _is_qdialog(node.value.func):
                target = node.targets[0]
                name = getattr(target, "id", None) or ast.unparse(target)
                # The enclosing function is the "body" of an anonymous window:
                # the builder IS where its size is decided.
                owner = _enclosing_function(tree, node.lineno)
                owner_node = owner if owner is not None else node
                calls = _called_names(owner_node)
                entries.append({
                    "file": rel,
                    "name": name,
                    "kind": "anonymous",
                    "remembered": _REMEMBER in calls,
                    "paired": {_RESTORE, _PERSIST} <= calls,
                    "keyed": _calls_have_key(owner_node, _REMEMBER),
                })
    return entries


def _enclosing_function(tree: ast.AST, lineno: int):
    """The innermost FunctionDef whose span contains `lineno`, or None."""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


def problems(entries: list[dict], exempt=None) -> list[str]:
    """Human-readable faults — what a reviewer (or the run) has to act on.

    `exempt` defaults to the module's `_EXEMPT` table and is injectable, so the
    classifier cells below can test a waiver WITHOUT mutating the real table
    (a mutated global would be a second source of truth, rule 38)."""
    exempt = _EXEMPT if exempt is None else exempt
    out: list[str] = []
    for entry in entries:
        label = f"{entry['file']}: {entry['name']} ({entry['kind']})"
        key = (entry["file"], entry["name"])
        if key in exempt:
            if not exempt[key].strip():
                out.append(f"{label}: exempt with an EMPTY reason — say why")
            continue
        if not (entry["remembered"] or entry["paired"]):
            out.append(
                f"{label}: no remembered size — call remember_dialog_size"
                f"(dialog, W, H) (restore-only looks fine for exactly one "
                f"launch, which is the defect)")
            continue
        if entry["kind"] == "anonymous" and not entry["keyed"]:
            out.append(
                f"{label}: an anonymous QDialog with no explicit key= — every "
                f"such window would share `dialog_size:QDialog`")
    return out


SOURCE_OK_CLASS = """
from PyQt6.QtWidgets import QDialog
from gui.ui_utils import remember_dialog_size


class Wired(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        remember_dialog_size(self, 600, 400)
"""

SOURCE_BARE_CLASS = """
from PyQt6.QtWidgets import QDialog


class Bare(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("no size of my own")
"""

SOURCE_HALF_PAIR = """
from PyQt6.QtWidgets import QDialog
from gui.ui_utils import restore_dialog_size


class RestoresOnly(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        restore_dialog_size(self, 600, 400)
"""

SOURCE_ANONYMOUS_KEYED = """
from PyQt6.QtWidgets import QDialog
from gui.ui_utils import remember_dialog_size


def build(parent):
    dialog = QDialog(parent)
    remember_dialog_size(dialog, 600, 400, key="KeyedAnonymous")
    return dialog
"""

SOURCE_ANONYMOUS_UNKEYED = """
from PyQt6.QtWidgets import QDialog
from gui.ui_utils import remember_dialog_size


def build(parent):
    dialog = QDialog(parent)
    remember_dialog_size(dialog, 600, 400)
    return dialog
"""
def _scan_source(tmp_path: Path, source: str) -> list[dict]:
    (tmp_path / GUI_ROOT).mkdir(exist_ok=True)
    (tmp_path / GUI_ROOT / "win.py").write_text(source, encoding="utf-8")
    return scan_dialogs(tmp_path)




def test_every_dialog_in_the_gui_remembers_its_size():
    """The ratchet on the real tree. Mutation: drop the remember call from any
    one window and this names it."""
    entries = scan_dialogs(REPO)
    assert not problems(entries), (
        "windows that forget their size (plan_2026_11_07_dialog_sizes_and_"
        "row_shift, part Б):\n  " + "\n  ".join(problems(entries)))


def test_the_scan_finds_the_whole_census():
    """Blind-guard alarm (rule 38): a scan that reads three windows must fail."""
    entries = scan_dialogs(REPO)
    assert len(entries) >= MIN_DIALOGS, (
        f"the scan found {len(entries)} windows (measured {MIN_DIALOGS} on "
        f"07.11.2026) — a guard that reads half the tree passes for the wrong "
        f"reason")
    kinds = {entry["kind"] for entry in entries}
    assert kinds == {"class", "anonymous"}, (
        f"only {sorted(kinds)} windows found — the anonymous QDialog branch "
        f"went blind")


def test_a_class_that_remembers_passes(tmp_path):
    assert not problems(_scan_source(tmp_path, SOURCE_OK_CLASS))


def test_a_class_with_no_size_call_fails(tmp_path):
    found = problems(_scan_source(tmp_path, SOURCE_BARE_CLASS))
    assert len(found) == 1 and "Bare" in found[0]


def test_a_half_pair_fails(tmp_path):
    """Restore without persist is the exact shape of a dialog that «остаётся
    слопнутым со второго раза»."""
    found = problems(_scan_source(tmp_path, SOURCE_HALF_PAIR))
    assert len(found) == 1 and "RestoresOnly" in found[0]


def test_an_anonymous_dialog_needs_an_explicit_key(tmp_path):
    assert not problems(_scan_source(tmp_path, SOURCE_ANONYMOUS_KEYED))
    found = problems(_scan_source(tmp_path, SOURCE_ANONYMOUS_UNKEYED))
    assert len(found) == 1 and "key" in found[0]


def test_an_exemption_needs_a_reason(tmp_path):
    entries = _scan_source(tmp_path, SOURCE_BARE_CLASS)
    key = ("gui/win.py", "Bare")
    assert not problems(
        entries, {key: "a fixed-size splash with no content to scroll"})
    found = problems(entries, {key: "   "})
    assert found and "EMPTY reason" in found[0]
