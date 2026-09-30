# tests/test_repo_hygiene.py
"""Ф1.8 of plan_2026_09_27_repo_and_tests_transformation — the standing guards.

Style follows tests/gui/test_socket_leak_guards.py: a guard is a TEST that reads the
repository, not a lint config, so it travels with the suite, runs in CI and fails
with a message that names the offender.

Every guard CHECKS that it did not go blind (rule 38: an empty scan is a failure,
not a pass) — each one counts what it read and asserts the count is plausible. A
guard that scans nothing reports a clean tree for the wrong reason, which is the
failure mode this whole step exists to avoid.

The four, and the Ф-step each one keeps closed:

  1. no `sys.path.insert` under tests/            (Ф1.5 removed all 120)
  2. `kicadstamp` is imported from THIS checkout  (the Ф1.5 worktree lesson: an
     editable install can point at another tree, and then the suite tests code
     nobody is editing)
  3. no test file redefines a fake that lives in tests/fakes/ (Ф1.4; a second copy
     is what drifts)
  4. no assignment to an attribute of an imported module/name outside monkeypatch
     (Ф1.2), with a measured exception list
"""
import ast
import importlib.util
from pathlib import Path

# Ф2.0: depth-independent (tests/paths.py). This guard MOVES in Ф2 (to tests/repo/),
# and its whole value is scanning the RIGHT directories afterwards: TESTS_DIR must
# stay the directory that holds conftest.py, not the one that holds this file.
from tests.paths import REPO_ROOT as REPO
from tests.paths import TESTS_ROOT as TESTS_DIR

FAKES_DIR = TESTS_DIR / "fakes"

#: Files allowed to assign to an attribute of an imported module or name outside
#: `monkeypatch`, each with a reason. The list is short ON PURPOSE: the guard was
#: written first and then run against the tree, and these are the two sites out of
#: six that turned out to be unable to use the fixture. The other four were fixed
#: (two were a redundant per-file reset, two a hand-rolled save/restore).
MONKEYPATCH_EXCEPTIONS = {
    "tests/conftest.py":
        "teardown of a LEAKED process-wide object, where monkeypatch would UNDO the "
        "point: the fixture stops the leaked QueueListener and then must leave "
        "_log_listener None. monkeypatch.setattr would restore the pre-test value at "
        "teardown — and that value IS the leaked listener, so the cleanup would be "
        "reverted and the leak re-installed for the next test.",
    "tests/gui/create_entity_helpers.py":
        "open_project() switches the working set off AFTER set_root_file on the REAL "
        "MainWindow has switched it on, so the assignment must follow that call, and a "
        "helper module cannot take the monkeypatch fixture without threading it through "
        "every caller (the Ф1.2 exception, quoted from that file's own docstring).",
}

def _files() -> list:
    return sorted(TESTS_DIR.rglob("*.py"))


def _sys_path_insert_calls(path: Path) -> list:
    """Real `sys.path.insert(...)` CALLS in one file, found by AST.

    Not a text search: the first version of this guard scanned lines and flagged
    tests/fakes/overrides_store.py, whose docstring MENTIONS the hack it just lost
    (Ф1.7) — a mention is not a call, and a guard that cannot tell them apart
    punishes the very note that explains the history."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "insert"
                and isinstance(func.value, ast.Attribute) and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"):
            hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return hits


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _imported_names(tree):
    """Names bound by an import here, split into modules and everything else.

    `from x import y` only binds a MODULE if `x.y` is importable as one — checked
    rather than guessed, because the distinction is what decides whether
    `y.attr = ...` mutates a module or an object that merely came from one."""
    modules, others = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if not node.module or node.level:
                continue
            for alias in node.names:
                try:
                    is_module = importlib.util.find_spec(
                        f"{node.module}.{alias.name}") is not None
                except (ImportError, ModuleNotFoundError, ValueError):
                    is_module = False
                name = alias.asname or alias.name
                (modules if is_module else others).add(name)
    return modules, others


def _offending_assignments(path: Path) -> list:
    """`<imported-name>.<attr> = ...` sites in one file, as 'relpath:line: text'."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, others = _imported_names(tree)
    rel = path.relative_to(REPO)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute):
                root = _root_name(target)
                if root in modules or root in others:
                    found.append(f"{rel}:{node.lineno}: {ast.unparse(target)}")
    return found


def test_no_sys_path_insert_is_left_under_tests():
    """Ф1.5 removed all 120 of them; pytest.ini's `pythonpath = .` plus the `tests.`
    package imports replaced each one. A new one means the import it hides might
    resolve to another checkout instead of this one."""
    files = _files()
    assert len(files) > 100, (
        f"only {len(files)} files scanned under {TESTS_DIR} — the scan went blind")

    offenders = []
    for path in files:
        offenders.extend(_sys_path_insert_calls(path))
    assert offenders == [], (
        f"sys.path is still patched in {offenders} — use `from tests.<module> import ...` "
        f"instead (pytest.ini sets pythonpath = .)")


def test_kicadstamp_is_imported_from_this_checkout(request):
    """The Ф1.5 lesson, as a standing guard: with an editable install pointing at
    another tree, the suite can pass while the code in front of you is what fails."""
    import kicadstamp

    root = Path(str(request.config.rootpath)).resolve()
    assert kicadstamp.__file__ is not None, (
        f"kicadstamp imported without a __file__ ({list(kicadstamp.__path__)}): it is a "
        f"NAMESPACE package here, so `pythonpath = .` either did not put this checkout "
        f"first or the package directory carries no __init__.py — the guard cannot say "
        f"which tree is under test, and that is a failure in its own right")
    assert (root / "kicadstamp").is_dir(), (
        f"no kicadstamp/ under rootdir {root} — the scan is looking at the wrong place")
    module_file = Path(kicadstamp.__file__).resolve()
    assert module_file.is_relative_to(root), (
        f"kicadstamp resolves to {module_file}, which is OUTSIDE this run's rootdir "
        f"{root}: the suite would be measuring another checkout while this one is "
        f"edited (pip install -e from elsewhere, or a stale .pth)")


def test_no_test_file_redefines_a_fake_that_lives_in_tests_fakes():
    """Ф1.4's rule, kept: a fake that exists in tests/fakes/ must not grow a second
    definition in a test file — the copies are what drift apart. Local doubles with
    a different name (`_FakeBoard`, `_OverlayAdapter`) are fine and expected."""
    fake_files = sorted(FAKES_DIR.glob("*.py"))
    assert fake_files, f"no modules under {FAKES_DIR} — blind scan"
    shared = set()
    for path in fake_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) \
                    and not node.name.startswith("_"):
                shared.add(node.name)
    assert len(shared) >= 10, (
        f"only {len(shared)} shared names found in tests/fakes/ ({sorted(shared)}) — the "
        f"scan went blind, and an empty `shared` would make the check below vacuous")

    scanned, offenders = 0, []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in shared:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} redefines "
                                 f"{node.name}")
    assert scanned > 100, f"only {scanned} test files scanned — blind scan"
    assert offenders == [], (
        f"{offenders} define a name that already lives in tests/fakes/ — import it from "
        f"there (or name the local double differently)")


def test_no_attribute_of_an_imported_name_is_assigned_outside_monkeypatch():
    """Ф1.2's rule, kept: a test may not reach into a module or a process-wide
    singleton by assignment. `monkeypatch.setattr` is the one mechanism that is
    undone for you, so a failure cannot leak into the next test."""
    scanned, names_seen, offenders = 0, 0, []
    for path in _files():
        rel = path.relative_to(REPO).as_posix()
        if rel in MONKEYPATCH_EXCEPTIONS:
            continue
        scanned += 1
        names_seen += sum(len(names) for names in _imported_names(
            ast.parse(path.read_text(encoding="utf-8"))))
        offenders.extend(_offending_assignments(path))

    assert scanned > 100, f"only {scanned} files scanned — blind scan"
    assert names_seen > 500, (
        f"the scan saw only {names_seen} imported names across the suite — it went "
        f"blind, and a blind guard here reports a clean tree for the wrong reason")
    assert offenders == [], (
        "these assign to an attribute of an imported module/name instead of using "
        "monkeypatch:\n  " + "\n  ".join(offenders) + "\n"
        "Use monkeypatch.setattr(...), or add the file to MONKEYPATCH_EXCEPTIONS with a "
        "measured reason saying why the fixture cannot be used there.")


def test_the_monkeypatch_exceptions_did_not_rot():
    """An exception list is honest only while its entries are still needed: if a file
    in it no longer does the flagged thing, the reason is stale and must go."""
    obsolete = []
    for rel, reason in MONKEYPATCH_EXCEPTIONS.items():
        path = REPO / rel
        assert path.is_file(), f"{rel} is in MONKEYPATCH_EXCEPTIONS but no longer exists"
        assert reason.strip(), f"{rel} carries no written reason"
        if not _offending_assignments(path):
            obsolete.append(rel)
    assert obsolete == [], (
        f"{obsolete} no longer assign to an attribute of an imported name — drop them "
        f"from MONKEYPATCH_EXCEPTIONS (an exception nobody needs hides the next one)")
