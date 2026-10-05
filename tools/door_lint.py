"""Static census of board reads that bypass the board door's UI-thread rules.

The door (docs/board_door.md, techdocs/me/door.md) is ``connection.board``. A
read of it from the UI thread must be the LAST resort: presence via
``connection.is_connected``, data via ``connection.snapshot``, work via the
worker (``start_long_op``), and only then ``with ui_thread_board_read(reason=…)``.
The runtime guard on the getter only sees the paths a session happens to walk;
this lint reads the whole of ``gui/`` and sorts EVERY ``.board`` read into:

* ``worker``  — inside a function handed to the worker (the ``fn`` argument of
  ``start_long_op`` / ``PollWorkerHandle.submit`` / ``LongOpController.start``,
  a lambda passed there, or the target of a ``partial`` passed there);
* ``signed``  — inside ``with ui_thread_board_read(...)``;
* ``suspect`` — anything else: a UI-thread read (or a helper both threads call).

Static by design, so it is a CENSUS, not a proof: a helper read from both
threads is a suspect even when only its worker caller is live, and a worker fn
reached through an indirection it cannot follow is a suspect too. The ratchet
(``tools/door_lint_baseline.py``, a plain ``{file: count}`` literal —
a .py because the repo ignores ``*.json`` and ``*.txt``) holds today's suspect count per file: a file
may not GROW, a new file must have none; a shrink is lowered with
``--update-baseline``.

Usage::

    python tools/door_lint.py                   # report suspects, check the ratchet
    python tools/door_lint.py --all             # also list worker / signed reads
    python tools/door_lint.py --update-baseline # write today's counts

Exit code 1 when the ratchet is broken. Standard library only.
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("gui",)
# The door itself: the getter, its setter and its guard live here.
EXEMPT_FILES = {"gui/connection.py"}
BASELINE = ROOT / "tools" / "door_lint_baseline.py"

SIGN = "ui_thread_board_read"
# (callable name, index of the worker-fn positional argument)
WORKER_ENTRIES = {"start_long_op": 2, "submit": 1}


def _call_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_name(func) -> str:
    """'self._active_op' for ``self._active_op.start`` — used to accept
    ``<...controller...>.start(fn)`` and nothing else named ``start``."""
    if isinstance(func, ast.Attribute):
        return ast.unparse(func.value).lower()
    return ""


def _fn_targets(node):
    """(names, inline_nodes) a worker-fn argument resolves to: a Name/Attribute
    gives its last name; ``partial(f, ...)`` gives f; a lambda is the inline
    worker body itself."""
    if isinstance(node, ast.Lambda):
        return set(), [node]
    if isinstance(node, ast.Call) and _call_name(node.func) == "partial" and node.args:
        return _fn_targets(node.args[0])
    name = _call_name(node)
    return ({name} if name else set()), []


class _Census(ast.NodeVisitor):
    def __init__(self):
        self.worker_names: set[str] = set()
        self.worker_nodes: list = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        index = None
        if name in WORKER_ENTRIES:
            index = WORKER_ENTRIES[name]
        elif name == "start" and "controller" in _receiver_name(node.func):
            index = 0
        if index is not None and len(node.args) > index:
            names, inline = _fn_targets(node.args[index])
            self.worker_names |= names
            self.worker_nodes.extend(inline)
        self.generic_visit(node)


def _is_board_read(node) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "board" \
            and isinstance(node.ctx, ast.Load):
        return True
    return (isinstance(node, ast.Call) and _call_name(node.func) == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "board")


def _parents(tree) -> dict:
    out = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[child] = parent
    return out


def _signed(node, parents) -> bool:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.With, ast.AsyncWith)):
            for item in cur.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and _call_name(expr.func) == SIGN:
                    return True
        cur = parents.get(cur)
    return False


def _enclosing(node, parents, census) -> tuple[str, bool]:
    """(qualified function name, is_worker) of the innermost enclosing def /
    worker lambda."""
    chain = []
    worker = False
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Lambda) and any(cur is w for w in census.worker_nodes):
            worker = True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            chain.append(cur.name)
            if not isinstance(cur, ast.ClassDef) and cur.name in census.worker_names \
                    and len(chain) == 1:
                worker = True
        cur = parents.get(cur)
    return ".".join(reversed(chain)) or "<module>", worker


def _innermost_def(node, parents):
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        if isinstance(cur, ast.Lambda):
            return cur
        cur = parents.get(cur)
    return None


def _propagate_workers(tree, parents, census) -> None:
    """A helper called ONLY from worker functions (in this module) is a worker
    function too — fixpoint over the module's own calls (``self.name(...)`` /
    ``name(...)``). A helper with ANY non-worker caller stays non-worker: that
    is exactly the "both threads call it" case the census must report."""
    defs = {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    callers: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in defs:
                callers.setdefault(name, []).append(_innermost_def(node, parents))
    worker_lambdas = {id(w) for w in census.worker_nodes}

    def _is_worker(caller) -> bool:
        if caller is None:
            return False
        if isinstance(caller, ast.Lambda):
            return id(caller) in worker_lambdas
        return caller in census.worker_names

    changed = True
    while changed:
        changed = False
        for name, cs in callers.items():
            if name not in census.worker_names and cs and all(map(_is_worker, cs)):
                census.worker_names.add(name)
                changed = True


def scan(root: Path = ROOT) -> list[dict]:
    rows = []
    for top in SCAN_DIRS:
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in EXEMPT_FILES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError as e:
                rows.append({"file": rel, "line": e.lineno or 0, "func": "?",
                             "kind": "suspect", "code": f"SyntaxError: {e.msg}"})
                continue
            census = _Census()
            census.visit(tree)
            parents = _parents(tree)
            _propagate_workers(tree, parents, census)
            lines = path.read_text(encoding="utf-8").splitlines()
            for node in ast.walk(tree):
                if not _is_board_read(node):
                    continue
                func, worker = _enclosing(node, parents, census)
                kind = ("worker" if worker else
                        "signed" if _signed(node, parents) else "suspect")
                rows.append({"file": rel, "line": node.lineno, "func": func,
                             "kind": kind,
                             "code": lines[node.lineno - 1].strip()[:100]})
    return rows


def _load_baseline() -> dict:
    """The ``SUSPECTS`` literal of the baseline module, read with ``ast`` (never
    imported/executed)."""
    try:
        tree = ast.parse(BASELINE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SUSPECTS" for t in node.targets):
            return dict(ast.literal_eval(node.value))
    return {}


def _write_baseline(counts) -> None:
    lines = ['"""tools/door_lint.py ratchet: suspect board reads per file — may only',
             'shrink. Rewritten by `python tools/door_lint.py --update-baseline`."""',
             "", "SUSPECTS = {"]
    lines += [f"    {rel!r}: {n}," for rel, n in sorted(counts.items())]
    lines += ["}"]
    BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check(rows, baseline) -> list[str]:
    counts = Counter(r["file"] for r in rows if r["kind"] == "suspect")
    problems = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            problems.append(f"{rel}: {n} suspect board read(s), baseline {allowed}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true",
                    help="also list worker and signed reads")
    ap.add_argument("--update-baseline", action="store_true",
                    help="write today's suspect counts as the new baseline")
    args = ap.parse_args(argv)

    rows = scan()
    shown = rows if args.all else [r for r in rows if r["kind"] == "suspect"]
    for r in shown:
        print(f"{r['kind']:8} {r['file']}:{r['line']}  {r['func']}  |  {r['code']}")
    kinds = Counter(r["kind"] for r in rows)
    print(f"\nboard reads in gui/: {len(rows)} — worker {kinds['worker']}, "
          f"signed {kinds['signed']}, suspect {kinds['suspect']}")

    if args.update_baseline:
        counts = Counter(r["file"] for r in rows if r["kind"] == "suspect")
        _write_baseline(counts)
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        return 0

    problems = check(rows, _load_baseline())
    if problems:
        print("\nRATCHET BROKEN (a file grew, or a new file reads the board off the "
              "worker without the sign):")
        for p in problems:
            print("  " + p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
