#!/usr/bin/env python3
"""Stage 3 probe — what would a "write the roles into the schematic" reminder cost?

2026-09-17, chat plan "reminder" (stage 3 of the spoke work). Roles written onto
the board by a role table live only on the board until Pending changes -> Apply
carries them into the schematic; the next F8 with field update can wipe them.
The reminder needs the same schematic-vs-board diff Pending changes already
computes (gui.docks.pending.compute_pending_edits). This probe runs exactly that
diff outside the GUI and times every input, so the reminder's refresh strategy
(every poll tick? only after a board write?) is chosen from numbers:

  * load_schematic_components + load_schematic_instances (the schematic side);
  * explore.Board.select() over the whole board (the snapshot side);
  * compute_pending_edits itself;
  * the resulting counts per field, the refdes/symbol mismatches, and the first
    rows of the diff.

READ-ONLY: own KiCad socket, closed in `finally`; the schematic files are read,
never written; nothing in profiles/ is touched.

Usage:
    python -m kicadstamp.diagnostics.probe_pending_role_cluster_diff [config]
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.docks.pending import compute_pending_edits                     # noqa: E402
from gui.schema_model import load_schematic_components, load_schematic_instances  # noqa: E402
from kicadstamp.config import load_config                               # noqa: E402
from kicadstamp.explore import Board                                    # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter                  # noqa: E402

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103" / "config.sexp")
ROW_LIMIT = 30


def timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    print(f"  {label:44s} {time.perf_counter() - started:7.3f} s")
    return result


def main() -> int:
    config = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_PROFILE)
    cfg, ctx = load_config(config)
    root = ctx.root_sheet
    print(f"profile: {config}\nroot sheet: {root}")
    if not root:
        print("the profile has no root_sheet — Pending changes cannot diff it")
        return 1

    print("\ntimings:")
    components = timed("load_schematic_components", lambda: load_schematic_components(str(root)))
    path_index = timed("load_schematic_instances", lambda: load_schematic_instances(str(root)))
    adapter = KiCadBoardAdapter()
    try:
        board = Board(adapter, dict(ctx.sheet_names or {}))
        timed("Board.refresh (footprint list)", board.refresh)
        snapshot = timed("Board.select() (fields, nets, sheets)", lambda: list(board.select()))
        edits = timed("compute_pending_edits", lambda: compute_pending_edits(
            components, snapshot, path_index))
    finally:
        adapter.close()

    print(f"\nschematic components: {len(components)}, instances: {len(path_index)}, "
          f"board footprints: {len(snapshot)}")
    fields = Counter(e.field for e in edits if not e.mismatched)
    mismatched = [e for e in edits if e.mismatched]
    print(f"pending edits: {len(edits)} — "
          + (", ".join(f"{f} {n}" for f, n in sorted(fields.items())) or "none")
          + f"; refdes/symbol mismatches {len(mismatched)}")
    for edit in edits[:ROW_LIMIT]:
        tag = "  MISMATCH" if edit.mismatched else ""
        print(f"  {edit.ref:8s} {edit.field:8s} schematic {edit.old_value!r:28s} "
              f"-> board {edit.new_value!r}{tag}")
    if len(edits) > ROW_LIMIT:
        print(f"  … (+{len(edits) - ROW_LIMIT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
