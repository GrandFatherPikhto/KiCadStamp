# tests/gui/test_no_worker_deleted_in_foreign_thread.py
"""С1 of plan_2026_09_18_worker_delete_deadlock.md: a Python-subclassed QObject
that was moved into a worker thread must never have its `deleteLater` handed to
a `finished` signal — that is what destroyed it in a FOREIGN thread.

Why this is a guard and not a style preference. Measured live on 2026-09-18
(py-spy `--native`, dump kept: `diagnostics/freeze_2026_09_18_boundary_dialog_pyspy.txt`),
the GUI froze for good on an Imprint write:

  * the UI thread held the GIL and waited for a Qt signal/slot mutex
    (`QComboBox::insertItem` -> `QObjectPrivate::connectImpl` ->
    `QBasicMutex::lockInternal` -> `WaitOnAddress`);
  * the dying worker thread held that same mutex class and waited for the GIL
    (`QThread::start` -> `QCoreApplicationPrivate::sendPostedEvents` ->
    `QObject::~QObject` -> sip -> `PyGILState_Ensure`).

The second stack is the defect this guard pins. `QThread::finished` is emitted
INSIDE the worker thread (Qt's `QThreadPrivate::finish`), so
`finished.connect(worker.deleteLater)` is a direct call: `deleteLater` posts a
DeferredDelete event into the WORKER thread's own queue, and `finish` drains
exactly that queue right after — destroying the Python-subclassed QObject, and
therefore taking the GIL, on a thread that is not the UI thread. Qt takes the
signal/slot mutexes from a fixed pool keyed by address, so the collision hits
unrelated objects (a brand-new QComboBox on the UI thread) and looks random
(Ф4 of the plan).

The rule (Р4): in `gui/**` there must be no `<object>.deleteLater` handed to a
call site when `<object>` is the same object some `moveToThread` sent into
another thread. The worker is destroyed by dropping the last Python reference
on the UI thread instead (LongOpController._on_thread_finished).

This is a TRIPWIRE for the literal shape, deliberately crude like
tests/gui/test_no_widget_height_squeezing.py: it matches the receiver's spelled
out name (`self._worker`) against the names `moveToThread` was called on in the
same file. A reference passed through a third variable or a dict under another
name slips through — it is not a proof, it is what makes the mistake loud
instead of silent. `test_the_scan_still_sees_the_moveToThread_sites` below keeps
it from going quietly vacuous.

Mutation check: reinstating the removed line (`gui/worker.py:286`) makes this
test fail, naming file and line (plan §4, М1) — measured on the base commit
before the fix, where it was red on exactly that line.
"""
import ast

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUI_DIR = _REPO_ROOT / "gui"

# The Qt call that hands an object to another thread.
_MOVE = "moveToThread"
# The Qt call that schedules destruction in the object's OWN thread.
_DELETE = "deleteLater"


def _expr_key(expr: ast.expr):
    """A spelled-out key for an expression, or None when it has none.

    `self._worker` -> "self._worker"; `self._pairs[0]` -> "self._pairs";
    `_LongOpWorker(...)` -> "_LongOpWorker". Calls are reduced to their target
    on purpose: the guard compares NAMES, and a name is what a reader sees."""
    if isinstance(expr, ast.Attribute):
        base = _expr_key(expr.value)
        return f"{base}.{expr.attr}" if base else None
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Call):
        return _expr_key(expr.func)
    if isinstance(expr, ast.Subscript):
        return _expr_key(expr.value)
    return None


def _move_sites(tree: ast.AST):
    """[(line, key)] for every `moveToThread` call in this module.

    A LIST, not a set: the same key legitimately appears twice in
    gui/worker.py (`self._worker` for the per-op worker at :282 and for the
    persistent poll worker at :752), and the sanity test below counts sites,
    not distinct spellings."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != _MOVE:
            continue
        key = _expr_key(func.value)
        if key:
            sites.append((node.lineno, key))
    return sites


def _moved_objects(tree: ast.AST) -> set:
    """Every expression key `moveToThread` was called on in this module."""
    return {key for _line, key in _move_sites(tree)}


def _delete_later_arguments(tree: ast.AST):
    """[(line, key)] for every `<key>.deleteLater` passed as a CALL ARGUMENT.

    A bare `widget.deleteLater()` is a direct call — the receiver is the
    current thread by construction, which is what the project does on purpose
    (gui/docks/cell_editor.py:980, gui/docks/configurator.py:955, …). What is
    dangerous is handing the bound method to a signal or a timer, because that
    deferral is what lets a THREAD decide who destroys the object."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if not isinstance(arg, ast.Attribute) or arg.attr != _DELETE:
                continue
            key = _expr_key(arg.value)
            if key:
                found.append((arg.lineno, key))
    return found


def _offenders(path: Path):
    """Yield (line, key) of every foreign-thread deleteLater in this file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    moved = _moved_objects(tree)
    if not moved:
        return []
    return [(line, key) for line, key in _delete_later_arguments(tree)
            if key in moved]


def test_no_moved_object_is_deleted_in_a_worker_thread():
    """A `finished.connect(<moved>.deleteLater)` destroys the object in the
    worker thread — the live GIL/Qt-mutex deadlock of 2026-09-18 (Р4)."""
    offenders = []
    for path in sorted(_GUI_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line, key in _offenders(path):
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: "
                f"{key}.deleteLater")

    assert offenders == [], (
        "these objects were moved into another thread by moveToThread and then "
        "had deleteLater handed to a signal/timer, so they are destroyed on a "
        "thread that is not the UI thread — that is the 2026-09-18 GIL-vs-Qt-"
        "mutex freeze (plan_2026_09_18_worker_delete_deadlock Р4). Drop the "
        "last Python reference on the UI thread instead "
        "(LongOpController._on_thread_finished):\n  " + "\n  ".join(offenders))


def test_the_scan_still_sees_the_moveToThread_sites():
    """The tripwire above is only worth its name while `moveToThread` is still
    spelled out where this scan can see it: `_LongOpWorker`
    (gui/worker.py:282) and the persistent `PollWorker`
    (gui/worker.py:752). Rename or hide either one and the test above would
    pass forever without looking at anything."""
    sites = []
    for path in sorted(_GUI_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, key in _move_sites(tree):
            sites.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: {key}")

    assert len(sites) >= 2, (
        "the scan found fewer than two moveToThread receivers — the GUI thread "
        "model moved, and this guard (С1 of plan_2026_09_18_worker_delete_"
        f"deadlock) must be re-pointed: {sites}")
