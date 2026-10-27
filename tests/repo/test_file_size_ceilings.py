# tests/repo/test_file_size_ceilings.py
"""Rule 45 with a ratchet — the guard 45.4 promised (`tests/repo/`, «этап А0а»).

Rule 45: a file is never more than 800 lines (comments and docstrings counted). Until 26.10
that rule was held by a human reading the diff, and it slipped: acceptance of Ч2б-2 found
`gui/docks/trees_dock.py` 7124 → 7147 and `gui/dock_hub.py` 2892 → 2905 — "giants only shrink"
broken while every leg was green. Denis, 26.10.2026: **a lock in BOTH directions.**

  * a file LONGER than its number is red — and the number may only be raised by Denis's word
    (growth of a giant is a decision, not a fix);
  * a file SHORTER than its number is red too — "опусти число до N тем же коммитом". Without
    this half the table would keep slack, and the next growth would slide through it silently.

What this guard is NOT: it cannot tell a justified growth from an unjustified one. Raising a
number is a one-line diff — visible in review, not forbidden by the machine. A guard that
claimed more than that would lie about what it measures.

Counting convention: `text.splitlines()` — the same as `tools/project_map.py`
(`ModuleInfo.total_lines`), so the table and the map's `⚠ >800` flag always agree. Not
`wc -l`: a file without a trailing newline (`kicadstamp/kicad/adapter.py` today) then counts
one line less than the text it has, and the lock would punish adding the newline.

Scope is exactly the four product roots Denis named on 26.10 — `kicadstamp/`, `gui/`,
`tools/`, `mcp_server/`. `tests/` is deliberately NOT scanned (a new cell in a big test module
is ordinary work, not a growing giant); locking it is a separate decision. Root-level entry
scripts (`kicadstamp_cli.py` and friends) are outside the scanned roots by the same decision.

Why 29 entries and not the 26 the map flags: `docs/map/index.md` excludes `kicadstamp/diagnostics/`
from its detail on purpose ("reported as a counter"), and the map's package list never learned
`kicadstamp/placement/services/` at all — so three real giants (897, 874, 935) carry no flag.
The ratchet takes its numbers from a MEASUREMENT, never from the map: a guard that scans only
what the map shows would report a clean tree for the wrong reason (rule 38).
"""
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT as REPO

#: Rule 45's own threshold. A file at or under it needs no entry.
BIG = 800

#: The scanned product roots — exactly what Denis named on 26.10.2026.
SCANNED_ROOTS = ("kicadstamp", "gui", "tools", "mcp_server")

#: Every product file over `BIG` lines, with the size measured on the commit that introduced
#: this guard (base `8597b65c`, 26.10.2026). The number is the file's CURRENT size, not a
#: ceiling with slack: shrinking a file means lowering its number in the same commit, and
#: growing one means raising it — by Denis's word, in a diff that says so.
#: Entries are never deleted, even when a file drops under `BIG`: a retired giant stays locked.
CEILINGS = {
    "gui/dock_hub.py": 2879,
    "gui/docks/cell_editor.py": 2777,
    "gui/docks/cell_refs_tab.py": 1165,
    "gui/docks/config_tree.py": 1586,
    "gui/docks/configurator.py": 971,
    "gui/docks/imprint.py": 1907,
    "gui/docks/points.py": 982,
    "gui/docks/role_cluster_tree.py": 1213,
    "gui/docks/root_metadata.py": 905,
    "gui/docks/tree_from_selection.py": 845,
    "gui/docks/trees_dock.py": 7124,
    "gui/fieldstool_window.py": 1240,
    "gui/main_window.py": 1052,
    "gui/worker.py": 1103,
    "kicadstamp/apply_pipeline.py": 1149,
    "kicadstamp/cell_geometry_refresh.py": 1461,
    "kicadstamp/channel_copy.py": 947,
    "kicadstamp/config/entries.py": 1581,
    "kicadstamp/config/models.py": 1167,
    "kicadstamp/config/sexp_format.py": 1363,
    "kicadstamp/config/tree_instances.py": 1198,
    "kicadstamp/config_writer.py": 844,
    "kicadstamp/diagnostics/net_from_role_audit.py": 897,
    "kicadstamp/diagnostics/probe_gui_min_sizes.py": 874,
    "kicadstamp/kicad/adapter.py": 1141,
    "kicadstamp/placement/entity_placement.py": 988,
    "kicadstamp/placement/services/clone_role_resolver.py": 935,
    "kicadstamp/tree_position.py": 1926,
    "kicadstamp/trees.py": 2381,
}

#: Measured on the base commit: 587 product modules, 29 of them over `BIG`. The count is a
#: blind-guard alarm, not a target: a scan that reads nothing must fail, not pass (rule 38).
MEASURED_MODULES = 587


def _line_count(path: Path) -> int:
    """Lines of TEXT in one file — the map's own convention (see the module docstring)."""
    return len(path.read_text(encoding="utf-8").splitlines())


def _product_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files.extend(sorted((REPO / root).rglob("*.py")))
    return files


def _over_big() -> dict[str, int]:
    return {p.relative_to(REPO).as_posix(): _line_count(p)
            for p in _product_files() if _line_count(p) > BIG}


@pytest.mark.parametrize("rel", sorted(CEILINGS))
def test_no_file_is_over_its_ceiling(rel: str) -> None:
    """A giant may only shrink. Growing one is a decision, not a fix."""
    ceiling = CEILINGS[rel]
    actual = _line_count(REPO / rel)
    assert actual <= ceiling, (
        f"{rel} grew: {ceiling} → {actual} lines (+{actual - ceiling}). "
        f"Rule 45: a giant only shrinks — take the change into a new module, or get Denis's "
        f"word to raise {rel} to {actual} in this table AND in the same commit.")


@pytest.mark.parametrize("rel", sorted(CEILINGS))
def test_no_file_is_under_its_ceiling_either(rel: str) -> None:
    """The other half of the lock: after a shrink the number comes DOWN in the same commit.

    Without it the table keeps slack, and the next growth slides under a stale ceiling — the
    ratchet would then be a decoration (this is the failure mode the 26.10 decision named)."""
    ceiling = CEILINGS[rel]
    actual = _line_count(REPO / rel)
    assert actual >= ceiling, (
        f"{rel} shrank: {ceiling} → {actual} lines ({actual - ceiling}). "
        f"Lower its number in CEILINGS to {actual} in THIS commit — otherwise the next "
        f"{ceiling - actual} added lines would pass unseen.")


def test_every_file_over_800_lines_is_in_the_table() -> None:
    """A new giant must not be born unnoticed: the table is the whole set, not a sample."""
    missing = sorted(set(_over_big()) - set(CEILINGS))
    assert not missing, (
        "these files are over 800 lines and not in CEILINGS — split them, or add them with "
        f"their measured size (Denis's word for a NEW giant): {missing}")


def test_the_table_names_only_files_that_exist() -> None:
    """A rename or a deletion has to touch the table; a stale path locks nothing."""
    gone = [rel for rel in sorted(CEILINGS) if not (REPO / rel).is_file()]
    assert not gone, (
        f"CEILINGS names files that are not there: {gone} — remove the entry (or fix the path) "
        f"in the same commit that moved the file")


def test_the_scan_reads_the_whole_product_tree() -> None:
    """The blind-guard alarm (rule 38): an empty or half scan is a failure, not a pass."""
    for root in SCANNED_ROOTS:
        assert (REPO / root).is_dir(), f"scanned root is gone: {root}"
    files = _product_files()
    assert len(files) >= MEASURED_MODULES - 50, (
        f"the scan read only {len(files)} product modules (measured {MEASURED_MODULES} on the "
        f"base commit) — a guard that reads half the tree passes for the wrong reason")
