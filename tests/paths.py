# tests/paths.py
"""Tree-absolute paths for the test suite — the ONE place whose depth is fixed.

Ф2 of plan_2026_09_27_repo_and_tests_transformation moves test FILES between
directories, one domain per commit. Any module that computed the repository root
from its own depth (`Path(__file__).resolve().parent.parent`) reads a DIFFERENT
directory the moment it moves one level deeper. That failure is quiet: most such
paths are only evidence for a message, so the suite stays green while a guard starts
reading nothing — the exact "guard went blind" failure the plan's rule 38 exists for.

So the depth is declared HERE, once, for files directly under `tests/`. A test that
needs a tree-absolute path imports it:

    from tests.paths import REPO_ROOT, FIXTURES_DIR

and then survives any move, because nothing about it depends on its own location.

`test_repo_hygiene.py`'s rule "no `sys.path.insert`" applies to this module like any
other: the import works through pytest.ini's `pythonpath = .`, which puts the run's
rootdir on `sys.path` — the mechanism Ф1.5 established for the whole suite.
"""
from pathlib import Path

#: `tests/` — the package this module lives in.
TESTS_ROOT = Path(__file__).resolve().parent

#: The run's repository root — what `pytest.ini` puts on `sys.path` via `pythonpath = .`.
REPO_ROOT = TESTS_ROOT.parent

#: The shared fixtures directory (`tests/fixtures/`), which does NOT move in Ф2.
FIXTURES_DIR = TESTS_ROOT / "fixtures"

# ── Blind-path guard (rule 38): a wrong root must fail HERE, loudly ─────────────
# If this module is ever moved deeper (or the suite is started from a tree without
# a checkout), every importer would silently get a plausible-looking but wrong path.
# Naming the missing anchors turns that into a refusal to run instead of a suite
# that measures nothing.
_REQUIRED = (
    (REPO_ROOT / "kicadstamp", "the shipped package"),
    (REPO_ROOT / "pytest.ini", "the suite's pytest config"),
    (TESTS_ROOT / "conftest.py", "the shared test fixtures"),
    (FIXTURES_DIR, "the shared fixtures directory"),
)
_MISSING = [f"{path} ({why})" for path, why in _REQUIRED if not path.exists()]

if _MISSING:
    raise RuntimeError(
        "tests/paths.py resolved the tree to wrong directories, so every test that "
        "imports it would read a nonexistent path (a blind scan, not an empty one). "
        "Missing: " + "; ".join(_MISSING) + ". If this module moved, TESTS_ROOT must "
        "stay pointed at the directory that holds conftest.py, and REPO_ROOT one level "
        "above it."
    )
