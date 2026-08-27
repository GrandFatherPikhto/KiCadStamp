#!/usr/bin/env python3
"""One-off migrator: move old *.trees files into the root config's trees:
section (design_2026_08_27_trees_in_config_file.md §5.3, FORK-5).

Reads each old `*.trees` file through the EXISTING load_trees() (the v1
(kicadstamp-trees (version 1) ...) grammar), converts each Tree to the plain
config-dict shape via tree_to_dict, and writes the whole list into the root
config's trees: section through the single config_writer chokepoint —
replacing the whole section, preserving every other root key, backing up the
root file first (config_writer's own convention, applied here explicitly).

The old .trees file is left untouched (one-off migration, not sync) — delete
it by hand after confirming the root config loads.

Usage:
  tools/trees_to_config.py <root-config> <old.trees> [more.trees ...]
      # root-config: .yaml or .sexp — direction by extension, like
      # tools/sexp_config_convert.py.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from kicadstamp.config.loader import load_config
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import load_trees, tree_to_dict
from gui.docks.entity_delete import backup_file


def migrate(root_path: Path, trees_files: list[Path]) -> list[dict]:
    """Load every old .trees file and return the merged list of tree dicts."""
    out: list[dict] = []
    for tf in trees_files:
        if not tf.exists():
            raise FileNotFoundError(f"trees file not found: {tf}")
        for tree in load_trees(str(tf)):
            out.append(tree_to_dict(tree))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Move old *.trees files into the root config's trees: section.")
    ap.add_argument("root", help="root config file (.yaml or .sexp)")
    ap.add_argument("trees", nargs="+", help="old .trees file(s) to migrate")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"[error] root config not found: {root}", file=sys.stderr)
        return 1

    try:
        trees = migrate(root, [Path(t) for t in args.trees])
    except Exception as e:  # noqa: BLE001 — load_trees raises ValidationError
        print(f"[error] failed to read trees file(s): {e}", file=sys.stderr)
        return 1

    # backup the write target (the root config) BEFORE overwriting, matching
    # entity_delete's backup_file convention (never overwrites an earlier bak)
    backup_file(root)

    data = read_data(root)
    data["trees"] = trees
    write_data(root, data)

    # Self-verify: the written root config must actually load — catches e.g.
    # a duplicate tree name/ref introduced by merging several .trees files
    # (each old file only checked uniqueness within itself). Same "never
    # silently report success on a broken result" discipline as
    # tools/sexp_config_convert.py. Does NOT roll back — the .bak from
    # backup_file() above is the recovery point, same as that tool's
    # self-verify failure path.
    try:
        load_config(str(root))
    except ValidationError as e:
        print(f"[error] migration wrote {root}, but it now fails to load: {e}",
              file=sys.stderr)
        print(f"        a backup of the pre-migration root is next to {root} "
              f"(.bak.<timestamp>)", file=sys.stderr)
        return 1

    print(f"written {len(trees)} tree(s) into trees: of {root}")
    for tf in args.trees:
        print(f"  (old file left as-is: {tf})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
