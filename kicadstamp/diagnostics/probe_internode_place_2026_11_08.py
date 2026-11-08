# kicadstamp/diagnostics/probe_internode_place_2026_11_08.py
"""LIVE cell of plan_2026_11_08_internode_cell_copper_by_place — READ-ONLY.

What it answers: on the CURRENT selection of the open board, how many inter-node units the
classifier takes WITH the new PLACE pass and how many it took WITHOUT it. The live defect
(HP-Power-v099, 08.11) was 83 → 63 expected on tree `switch_a`: the 20 pieces that sit exactly
on a cell record's place but have no registry key (the cell was read and never redrawn).

Safety, in this order:
  1. the PROFILE IS COPIED and the probe runs on the copy (`profiles/<name>-copy`, the SAME
     depth — the config's relative paths must keep resolving). The copy is removed in `finally`;
  2. a STOP-CHECK BEFORE the first board read: the profile's `root_sheet` (resolved) against the
     board KiCad has OPEN. A mismatch aborts with nothing read (Denis's rule: `-test` in a name
     is not a sandbox — resolve the path and look at it);
  3. nothing is written: no config, no registry, no board. The "before" number is produced by
     disabling the place pass IN MEMORY (`_give_by_place` -> no-op), never by a code edit.

Run:
  .venv/bin/python -m kicadstamp.diagnostics.probe_internode_place_2026_11_08 \\
      --config profiles/<profile>/config.sexp --tree switch_a
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _open_board_name(adapter) -> str | None:
    """The board KiCad has OPEN right now — `None` when KiCad answers nothing.

    `read_open_board_name` takes the CLIENTS' `Kicad` object (one `GetOpenDocuments`
    round trip, no board read), so it is asked BEFORE `refresh_board()`. The first cut of
    this probe called it with NO argument, the TypeError was swallowed as "no answer", and
    the stop-check silently did nothing — the very shape of a guard that reads as present.
    """
    try:
        from kicadstamp.kicad.kipy_board_identity import read_open_board_name
    except Exception:  # noqa: BLE001 — a probe must be able to say "no answer"
        return None
    try:
        return read_open_board_name(adapter._kicad)
    except Exception as exc:  # noqa: BLE001
        print(f"board name: no answer ({exc.__class__.__name__}: {exc})")
        return None


def _copy_profile(config_path: Path) -> Path:
    """`profiles/<name>-copy` — SAME depth as the original (rule 34: the config's paths are
    relative, and a copy in /tmp breaks them). Removed by the caller."""
    src_dir = config_path.parent
    dst_dir = src_dir.parent / f"{src_dir.name}-copy"
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    return dst_dir / config_path.name


def _counts(adapter, cfg, tree, config_path: str, items, footprints, sheet_names,
            *, with_place: bool) -> tuple[int, list[str]]:
    """(units taken, warnings) — the classifier's own answer. `with_place=False` disables the
    place pass in MEMORY, which is exactly the "before" the defect was measured on."""
    import kicadstamp.internode_cell_copper as cell_copper
    from kicadstamp.internode_capture import internode_units

    if with_place:
        units, _discarded, warnings = internode_units(
            adapter, cfg, tree, config_path=config_path, area_items=items,
            area_footprints=footprints, sheet_names=sheet_names)
    else:
        real = cell_copper._give_by_place
        cell_copper._give_by_place = lambda *a, **k: (set(), 0, 0)
        try:
            units, _discarded, warnings = internode_units(
                adapter, cfg, tree, config_path=config_path, area_items=items,
                area_footprints=footprints, sheet_names=sheet_names)
        finally:
            cell_copper._give_by_place = real
    return len(units), list(warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True,
                        help="the profile's config.sexp (NEVER written: the probe copies it)")
    parser.add_argument("--tree", default=None,
                        help="tree name to classify (default: the config's first tree)")
    parser.add_argument("--timeout-ms", type=int, default=None)
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"no such config: {config_path}")
        return 2

    from kicadstamp.config import load_config

    cfg, ctx = load_config(str(config_path))
    root_sheet = getattr(ctx, "root_sheet", None)
    expected = Path(root_sheet).name if root_sheet else None
    print(f"profile       : {config_path}")
    print(f"root_sheet    : {root_sheet}")

    tree = next((t for t in cfg.trees if args.tree in (None, t.name)), None)
    if tree is None:
        print(f"no tree {args.tree!r} in this config: {[t.name for t in cfg.trees]}")
        return 2

    copy_config = _copy_profile(config_path)
    try:
        from kicadstamp.adapter_factory import create_board_adapter

        adapter = create_board_adapter(args.timeout_ms, config_path=str(copy_config))
        # STOP-CHECK before the FIRST board read: the identity tick is one
        # `GetOpenDocuments` round trip, not a board read.
        open_name = _open_board_name(adapter)
        print(f"open board    : {open_name}")
        if expected and open_name and Path(open_name).stem != Path(expected).stem:
            print("STOP: the open board is NOT the profile's board — nothing is read.")
            return 3
        if open_name is None:
            print("WARNING: KiCad did not answer the board name — the stop-check could not "
                  "run. READ-ONLY probe: nothing is written either way.")
        adapter.refresh_board()
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})

        selected = list(adapter.get_selected_items() or ())
        items = [i for i in selected if hasattr(i, "uuid")]
        from kicadstamp.domain.board import Footprint

        footprints = [i for i in items if isinstance(i, Footprint)]
        if not footprints:
            footprints = list(adapter.get_footprints() or ())
        area = "the current SELECTION"
        if not items:
            items = list(adapter.get_vias() or ()) + list(adapter.get_tracks() or ())
            area = "the WHOLE board (nothing is selected — the numbers are not the 83 of the plan)"

        print(f"area          : {area} ({len(items)} item(s), {len(footprints)} footprint(s))")
        before, before_lines = _counts(adapter, cfg, tree, str(copy_config), items,
                                       footprints, sheet_names, with_place=False)
        after, after_lines = _counts(adapter, cfg, tree, str(copy_config), items,
                                     footprints, sheet_names, with_place=True)
        print(f"tree          : {tree.name}")
        print(f"units BEFORE  : {before}")
        print(f"units AFTER   : {after}")
        print(f"given to cells: {before - after}")
        for line in after_lines:
            print(f"  log: {line}")

        # The per-cell breakdown — WHICH cells got their copper back, and by which proof.
        # `by_cell` counts pieces, `by_place` records (the place pass' own count).
        from kicadstamp.internode_cell_copper import tree_cell_copper

        mapping = tree_cell_copper(adapter, cfg, str(copy_config), tree,
                                   sheet_names=sheet_names)
        print(f"cell copper   : {mapping.tracks} tracks + {mapping.vias} vias, "
              f"by PLACE {mapping.by_place}, not given back {mapping.without_registry}")
        for cell_name in sorted(mapping.by_cell):
            print(f"  {cell_name}: {mapping.by_cell[cell_name]} piece(s)")
        if before == after:
            print("NOTE: the place pass changed nothing here — read the Log lines above "
                  "before concluding anything (a non-rigid frame refuses every pair by place).")
    finally:
        shutil.rmtree(copy_config.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
