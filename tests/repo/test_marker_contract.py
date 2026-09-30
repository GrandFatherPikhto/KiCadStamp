# tests/test_marker_contract.py
"""Ф1.6 of plan_2026_09_27_repo_and_tests_transformation: the marker partition
is a CONTRACT, so it is checked from inside the suite.

`gui` / `integration` / `unit` are attached by the
``pytest_collection_modifyitems`` hook in tests/conftest.py — every item gets
exactly one. A hook that silently stops marking is INVISIBLE in a green run:
`-m gui` would then select nothing and a command would look successful. That is
not hypothetical — it was measured while landing the hook:

    baseline                       integration=23  gui=2652  unit=3345  (6020 total)
    integration branch dropped     integration=23  gui=2652  unit=3368
    gui branch dropped             integration=23  gui=0     unit=5999

The first mutation is invisible in the integration count (all 23 items in
tests/integration_tests/ also carry the decorator by hand, which is why the
path-marking is redundant for TODAY'S items and load-bearing for the next
undecorated one), and the second turns `-m gui` into a command that selects
nothing. So the partition is asserted from pytest's own collected items rather
than trusted.

Ф1.12 added `addopts = -m "not integration"` to pytest.ini, so a NON-EMPTY
`markexpr` no longer means "a user filtered the run": it is now the DEFAULT of
every bare run. The whole-suite cell used to treat any non-empty `markexpr` as a
user filter and skip — which, with `addopts` in place, would have made the ONE
guard against "the hook stopped marking a whole kind" go quiet on every default
run. So the skip decision compares the effective `markexpr` against the DEFAULT
the ini imposes, not against the empty string, and `_whole_suite_skip_reason` is
a PURE function so a cell can pin its value without the whole suite being
collected here. A second cell collects in a subprocess to prove the protection
itself: a bare run must collect ZERO integration cells.
"""
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

MARKERS = ("gui", "integration", "unit")

#: The `-m` expression pytest.ini's `addopts` imposes — the DEFAULT of a bare run.
#: Pinned by `test_the_default_run_does_not_skip_the_three_kind_check`, so a change
#: to it is a loud failure rather than a silent mis-comparison below.
_EXPECTED_DEFAULT_MARKEXPR = "not integration"

# Ф2.0: depth-independent (tests/paths.py). This file MOVES in Ф2 (to tests/repo/),
# and both roots must NOT follow it: the suite's root is the directory that holds
# conftest.py, and the integration directory hangs off THAT, not off this file.
from tests.paths import REPO_ROOT as _REPO_ROOT
from tests.paths import TESTS_ROOT

_TESTS_ROOT = TESTS_ROOT
_INTEGRATION_DIR = _TESTS_ROOT / "integration_tests"
# `N/M tests collected` when something was deselected, plain `N tests collected`
# when nothing was (the no-filter run) — both carry the SELECTED count in the
# first number, which is the only one this file needs.
_COLLECT_SUMMARY = re.compile(r"(\d+)(?:/\d+)? tests? collected")

# Debug breadcrumbs: what each subprocess actually printed, included in a failure
# message so a surprising count names its own cause instead of only its value.
_COLLECT_RUNS: list = []


def _markers_of(item):
    """The NAMES of the gui / integration / unit markers this item really carries.

    Collected into a SET, not a list: a marker can be attached twice to one item
    (the hand-written `@pytest.mark.integration` on the nine files in
    tests/integration_tests/ plus the hook's own copy) and `iter_markers()` yields
    both objects. `item.keywords` collapses them, `iter_markers()` does not — so a
    cell that counts Mark objects reports "two markers" for an item that has one
    name twice. Measured: the 23 integration items read ['integration',
    'integration'] until the hook stopped stacking the second copy.

    `item.iter_markers()`, NOT `item.keywords`: "gui" is ALSO a keyword pytest
    derives from the DIRECTORY name (tests/gui/), so `set(MARKERS) & set(
    item.keywords)` treats the directory as if it were a marker. The first version
    of this file did that, and the moment the hook stopped marking gui it reported
    2652 gui items as carrying "gui AND unit" — a false alarm produced by the scan
    itself, which also hid the real symptom (gui=0) behind the wrong message. On a
    clean tree it passed only because a set collapses the marker and the directory
    keyword into one entry. pytest's own `-m` filter reads real markers, which is
    why `-m gui` collected 0 in that same state — the two must agree, so this reads
    the same source.
    """
    return sorted({m.name for m in item.iter_markers() if m.name in MARKERS})


def _addopts_tokens(addopts) -> list:
    """`addopts` as a token list.

    `config.getini("addopts")` returns a LIST (pytest's "args" ini type tokenizes
    the raw string with shlex), so it is taken as-is; a plain string is split. A
    quoted value survives as ONE token (`['-m', 'not integration']`), which is why
    the value can be read as "the token after `-m`" without re-splitting.
    """
    return list(addopts) if not isinstance(addopts, str) else shlex.split(addopts)


def _markexpr_from_addopts(addopts) -> str:
    """The `-m` expression inside `addopts`, or "" when there is none.

    Quoted values survive as a single token, so `-m "not integration"` is read as
    one expression; `-m<expr>` written without a space is handled too.
    """
    tokens = _addopts_tokens(addopts)
    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("-m") and len(token) > 2:
            return token[2:]
    return ""


def _excludes_integration(markexpr: str) -> bool:
    """True when a `-m` expression deselects the integration kind — the shape
    pytest.ini's `addopts` has. Deliberately simple: the exact default string is
    pinned by `test_the_default_run_does_not_skip_the_three_kind_check`, so a
    rewrite of `addopts` fails loudly instead of silently changing this answer."""
    return " ".join(markexpr.split()) == _EXPECTED_DEFAULT_MARKEXPR


def _whole_suite_skip_reason(markexpr, default_markexpr, missing) -> str | None:
    """Why `test_the_whole_suite_still_has_all_three_kinds` must not judge here,
    or None when it should. PURE (Ф1.12): a cell pins its value on the DEFAULT
    inputs without the whole suite being collected inside the current run."""
    if markexpr != default_markexpr:
        return (f"a -m filter is in force ({markexpr!r} differs from the default "
                f"{default_markexpr!r}): whole kinds are deselected by design, so "
                f"the three-way split cannot be checked")
    if missing:
        names = sorted(p.name for p in missing)[:3]
        return (f"this run collected only part of the suite — {len(missing)} test "
                f"file(s) on disk were not collected ({names} …), so the three-way "
                f"split cannot be checked from here")
    return None


def _collected(markexpr: str | None) -> int:
    """How many cells a COLLECT-ONLY pytest run selects, measured in a subprocess.

    `markexpr=None` means "do not pass `-m`": the run then gets pytest.ini's
    `addopts`, which is exactly what a bare `pytest` receives. `--collect-only`
    guarantees NO cell executes, which is what makes this safe to point at
    tests/integration_tests/ — those cells write to a live board when run.
    """
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q",
               "-p", "no:cacheprovider"]
    if markexpr is not None:
        command += ["-m", markexpr]
    result = subprocess.run(command, cwd=_REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"collect-only run failed ({' '.join(command)}):\n{result.stdout}\n"
        f"{result.stderr}")
    _COLLECT_RUNS.append(f"{' '.join(command)} →\n{result.stdout[-800:]}")
    matches = _COLLECT_SUMMARY.findall(result.stdout)
    assert matches, (
        f"no 'N/M tests collected' summary in the output of {' '.join(command)}:\n"
        f"{result.stdout}")
    return int(matches[-1])


def test_every_collected_item_carries_exactly_one_of_the_three_markers(request):
    seen = [(item.nodeid, _markers_of(item)) for item in request.session.items]
    # Rule 38: an empty scan must FAIL, not pass quietly.
    assert seen, "no items collected — the scan went blind"

    wrong = [(nodeid, found) for nodeid, found in seen if len(found) != 1]
    assert not wrong, (
        f"{len(wrong)} item(s) do not carry exactly one of {MARKERS}: {wrong[:5]}"
        f"{' …' if len(wrong) > 5 else ''} — the conftest hook attaches them by "
        f"path; an item with none would be selected by no `-m` filter at all, and an "
        f"item with two would be selected by both")


def test_the_default_run_does_not_skip_the_three_kind_check(request):
    """Ф1.12, and the reason this file is not blinded by its own protection.

    With `addopts = -m "not integration"` in force, a DEFAULT run has a non-empty
    `markexpr` — the exact state the whole-suite cell used to read as "a filter is
    in force" and skip. The check must therefore compare against the DEFAULT, not
    against "". This cell pins, on the default inputs only, that no skip reason
    exists, so the non-vacuity guard still runs on every bare `pytest`.
    """
    default = _markexpr_from_addopts(request.config.getini("addopts"))
    assert default == _EXPECTED_DEFAULT_MARKEXPR, (
        f"pytest.ini's addopts imposes -m {default!r}, expected "
        f"{_EXPECTED_DEFAULT_MARKEXPR!r} — the Ф1.12 protection is not in place, "
        f"so a bare `pytest` would collect the live-board cells")
    assert _whole_suite_skip_reason(default, default, set()) is None, (
        "the default configuration is treated as a user filter, so "
        "test_the_whole_suite_still_has_all_three_kinds would SKIP on every default "
        "run — the one guard against 'the hook stopped marking a whole kind' would "
        "go quiet")


def test_a_bare_run_collects_no_integration_cells():
    """Ф1.12's protection, by CONSTRUCTION: a bare `pytest` must collect ZERO
    integration-marked cells, because running them writes to a live board.

    COLLECTION ONLY — three `--collect-only` subprocesses, none of which executes a
    cell. The explicit `-m integration` run still selects the 23 (a CLI `-m` wins
    over `addopts`), which is how the deselected kind is counted at all: a run that
    found nothing must be a FAILURE (rule 38), not a pass.
    """
    total = _collected("integration or not integration")
    integration = _collected("integration")
    bare = _collected(None)

    assert total > 0 and integration > 0, (
        f"the scan went blind: total={total}, integration={integration} — the "
        f"collect-only runs found nothing to compare\n{_COLLECT_RUNS}")
    assert bare == total - integration, (
        f"a bare run collected {bare} cells; with {integration} integration cells "
        f"of {total} total it should have collected {total - integration} — "
        f"`addopts = -m \"not integration\"` is missing or not in force, and a bare "
        f"`pytest` would RUN the live-board cells\n{_COLLECT_RUNS}")


def test_the_whole_suite_still_has_all_three_kinds(request):
    """Non-vacuity of the partition: if the hook stopped marking one kind, that
    kind's count would be 0 and `-m <kind>` would select nothing while looking
    green. Only meaningful when the WHOLE suite was collected — a `-m`/`-k` filter,
    a path argument (even `pytest tests/gui`) or `--lf` removes whole kinds by
    design, and asserting there would make the cell lie. It skips in those cases
    EXPLICITLY, and it CHECKS that the full set of test files on disk was collected
    rather than assuming it (the first version of this cell did not, and went red on
    `pytest tests/test_marker_contract.py` with gui=0/integration=0).

    Ф1.12: the DEFAULT `-m "not integration"` deselects a whole kind on purpose, so
    it is not "a user filter" (compared in `_whole_suite_skip_reason`) and the
    integration files are not "missing" — the check below excludes them only when
    the default itself does. The integration kind's own non-vacuity is then covered
    by `test_a_bare_run_collects_no_integration_cells`, which counts it directly.
    """
    default = _markexpr_from_addopts(request.config.getini("addopts"))
    collected_files = {Path(str(item.path)).resolve() for item in request.session.items}
    on_disk = {p.resolve() for p in _TESTS_ROOT.rglob("test_*.py")}
    if _excludes_integration(default):
        on_disk = {p for p in on_disk if not p.is_relative_to(_INTEGRATION_DIR)}
    missing = on_disk - collected_files

    reason = _whole_suite_skip_reason(request.config.option.markexpr, default, missing)
    if reason:
        pytest.skip(reason)

    expected = tuple(name for name in MARKERS
                     if not (_excludes_integration(default) and name == "integration"))

    counts = {name: 0 for name in MARKERS}
    for item in request.session.items:
        for name in _markers_of(item):
            counts[name] += 1

    assert sum(counts.values()) == len(request.session.items), (
        f"the markers do not partition the suite: {counts} over "
        f"{len(request.session.items)} items")
    for name in expected:
        assert counts[name] > 0, (
            f"no collected item carries {name!r} — the hook stopped marking that "
            f"kind, and `-m {name}` would select nothing while looking green "
            f"(counts: {counts})")
