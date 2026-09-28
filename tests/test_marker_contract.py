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
"""
from pathlib import Path

import pytest

MARKERS = ("gui", "integration", "unit")

_TESTS_ROOT = Path(__file__).resolve().parent


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


def test_the_whole_suite_still_has_all_three_kinds(request):
    """Non-vacuity of the partition: if the hook stopped marking one kind, that
    kind's count would be 0 and `-m <kind>` would select nothing while looking
    green. Only meaningful when the WHOLE suite was collected — a `-m`/`-k` filter,
    a path argument (even `pytest tests/gui`) or `--lf` removes whole kinds by
    design, and asserting there would make the cell lie. It skips in those cases
    EXPLICITLY, and it CHECKS that the full set of test files on disk was collected
    rather than assuming it (the first version of this cell did not, and went red on
    `pytest tests/test_marker_contract.py` with gui=0/integration=0)."""
    collected_files = {Path(str(item.path)).resolve() for item in request.session.items}
    on_disk = {p.resolve() for p in _TESTS_ROOT.rglob("test_*.py")}
    if request.config.option.markexpr:
        pytest.skip(f"a -m filter is in force "
                    f"({request.config.option.markexpr!r}): whole kinds are "
                    f"deselected by design, so the three-way split cannot be checked")
    missing = on_disk - collected_files
    if missing:
        names = sorted(p.name for p in missing)[:3]
        pytest.skip(f"this run collected only part of the suite — {len(missing)} test "
                    f"file(s) on disk were not collected ({names} …), so the three-way "
                    f"split cannot be checked from here")

    counts = {name: 0 for name in MARKERS}
    for item in request.session.items:
        for name in _markers_of(item):
            counts[name] += 1

    assert sum(counts.values()) == len(request.session.items), (
        f"the markers do not partition the suite: {counts} over "
        f"{len(request.session.items)} items")
    for name in MARKERS:
        assert counts[name] > 0, (
            f"no collected item carries {name!r} — the hook stopped marking that "
            f"kind, and `-m {name}` would select nothing while looking green "
            f"(counts: {counts})")
