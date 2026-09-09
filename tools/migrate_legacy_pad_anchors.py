#!/usr/bin/env python3
# tools/migrate_legacy_pad_anchors.py
"""One-time migration (GUARD 2, plan_2026_09_09_cell_anchor_v2 §A.5): a Cell
carrying `anchor_role` + `anchor_pad` WITHOUT `anchor_xy` is the LEGACY
rebase-by-pad form (design 2026-09-04): such a cell was MUTATED at save time so
its anchor pad sits at the stored (0,0), and cell_mount_offset() returns
(0,0) for it — there is no other derivable mount.

Phase A repurposes that exact field combination as the DECLARATIVE pad anchor
(resolved live at apply time), so any legacy cell left without `anchor_xy`
would suddenly get A = the real live pad offset (≠ (0,0)) and its whole content
would SHIFT on the board. The migration writes `anchor_xy: [0.0, 0.0]` for every
such cell — preserving today's behaviour byte-for-byte (cell_mount_offset then
returns the stored (0,0)) and freeing the "no xy" form for the new meaning.

Checks the WHOLE include: graph rooted at the given profile, so cells living in
included subsystem files are migrated too. Idempotent: a migrated cell already
carries anchor_xy, so a second run is a no-op.

Run on a COPY of a live profile (like the other one-time tools):
    tools/migrate_legacy_pad_anchors.py profiles/3ch-awg-tia-v103/config.sexp
"""
import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path


def migrate_cells_data(data: Dict[str, Any]) -> List[str]:
    """Add `anchor_xy: [0.0, 0.0]` to every cell with `anchor_role` +
    `anchor_pad` and no `anchor_xy` (the legacy rebase-by-pad form, GUARD 2).

    Mutates the nested cell entries of `data` IN PLACE (the caller owns the
    dict — read_data() returns a SHARED cached object, so callers must
    deep-copy before passing it here). Returns the sorted names of the cells
    that were migrated (empty = nothing to do). Every other shape — a cell
    with anchor_xy already set (v2, untouched), role-only, pad-only, or no
    anchor — is left exactly as-is.
    """
    cells = data.get("cells") or {}
    migrated: List[str] = []
    if not isinstance(cells, dict):
        return migrated
    for name, cell in cells.items():
        if not isinstance(cell, dict):
            continue
        if (cell.get("anchor_role") and cell.get("anchor_pad")
                and cell.get("anchor_xy") is None):
            cell["anchor_xy"] = [0.0, 0.0]
            migrated.append(str(name))
    return sorted(migrated)


def _backup(path: Path) -> None:
    """Timestamped copy of `path` next to itself (e.g. config.sexp.bak.
    20260909_101500) — a real migration run rewrites the input and the
    original must never be lost (same backup-the-write-target convention as
    tools/convert_placements.py)."""
    import shutil
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))


def migrate_file(path: Path) -> List[str]:
    """Migrate ONE physical config file (.sexp/.json): read, deep-copy (the
    cache returns a SHARED object — an in-place mutation would corrupt the
    in-process cache if write_data() failed after mutating), migrate its own
    `cells:`, and write back only when something changed (after a timestamped
    .bak). Returns the migrated cell names."""
    from kicadstamp.config_writer import read_data, write_data

    data = copy.deepcopy(read_data(path))
    migrated = migrate_cells_data(data)
    if migrated:
        _backup(path)
        write_data(path, data)
    return migrated


def _graph_files(root_path: Path) -> List[Path]:
    """Every physical file reachable from root_path via include:, deduped by
    resolved path (walk_include_tree does NOT dedupe a diamond include)."""
    from kicadstamp.config.includes import walk_include_tree

    node = walk_include_tree(str(root_path))
    seen: Dict[str, Path] = {}

    def walk(n) -> None:
        seen.setdefault(str(n.path.resolve()), n.path)
        for child in n.children:
            walk(child)

    walk(node)
    return list(seen.values())


def migrate_root(root_path: Path) -> Dict[str, List[str]]:
    """Migrate every cell with the legacy anchor_role+anchor_pad (no
    anchor_xy) combination anywhere in the include: graph rooted at
    root_path. Returns {path: [migrated cell names]} for the files that
    actually changed (empty dict = nothing to do). Raises OSError on a
    non-readable/non-writable file (same contract as config_writer)."""
    result: Dict[str, List[str]] = {}
    for path in _graph_files(root_path):
        migrated = migrate_file(path)
        if migrated:
            result[str(path)] = migrated
    return result


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add anchor_xy: [0.0, 0.0] to legacy pad-anchored cells "
                    "(anchor_role+anchor_pad without anchor_xy) across the "
                    "whole include: graph — GUARD 2, run on a COPY of a live profile")
    parser.add_argument("path", help="root config file (.sexp) of the profile to migrate")
    args = parser.parse_args(argv)
    root = Path(args.path)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1
    try:
        result = migrate_root(root)
    except Exception as e:  # noqa: BLE001 — a tool reports, not raises
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not result:
        print(f"no legacy pad-anchored cells found in {root} (nothing to migrate)")
        return 0
    for path, names in result.items():
        print(f"migrated {path}: {len(names)} cell(s) got anchor_xy=[0.0, 0.0]")
        for name in names:
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
