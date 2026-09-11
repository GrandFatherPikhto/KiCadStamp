"""Replicate the GUI's "fully selected Cluster" detection OUTSIDE the GUI.

Denis 2026-09-11: "Extract cluster..." on a fully selected PIF_DVDD answers
"No fully selected Cluster found". fully_selected_clusters() drops a group at
FOUR different gates and reports one single message, so this probe prints the
outcome of every gate for the CURRENT live selection.

Read-only: Board.connect() + adapter.get_selected_items(). Connects the same
way gui/connection.py does (no schematic_dir), so Selected.sheet chains are
None and the sheet must be re-resolved from the config's RuntimeContext —
exactly what gui/docks/reead.py::_sheet_chain does.

Usage:  python -m kicadstamp.diagnostics.probe_fully_selected_clusters [config]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.explore import Board

from gui.docks.reead import (
    _sheet_chain,
    fully_selected_clusters,
    group_selected,
    instance_sheet,
    match_entity,
)

DEFAULT_CONFIG = "profiles/3ch-awg-tia/config.sexp"


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    print(f"config        : {config_path}")
    print(f"sheet_names   : {len(sheet_names)} entries "
          f"{'(EMPTY -> every sheet will be None)' if not sheet_names else ''}")
    print(f"entities      : {[e.name for e in cfg.entities]}")

    board = Board.connect()
    snapshot = board.select()
    print(f"snapshot      : {len(snapshot)} footprints")

    items = board.adapter.get_selected_items()
    sel_refs = {getattr(i, "ref", None) for i in items if getattr(i, "ref", None)}
    print(f"live selection: {len(sel_refs)} footprint(s) {sorted(sel_refs)}")

    # main_window.py:919 — the GUI's cached selection is the SNAPSHOT's objects
    # filtered by ref, NOT the live footprints.
    by_ref = {s.ref: s for s in snapshot}
    selected = [by_ref[r] for r in sel_refs if r in by_ref]
    missing = sorted(r for r in sel_refs if r not in by_ref)
    print(f"in snapshot   : {len(selected)}; MISSING from snapshot: {missing}")

    print("\n-- per selected footprint (as the GUI sees it) --")
    for s in sorted(selected, key=lambda x: x.ref):
        chain = _sheet_chain(s, sheet_names)
        print(f"  {s.ref:<6} cluster={s.cluster!r:<24} role={s.role!r:<18} "
              f"chain={chain} entity={getattr(match_entity(cfg.entities, s.cluster or '', chain), 'name', None)!r} "
              f"sheet={instance_sheet(s, cfg.entities, sheet_names)!r}")

    groups = group_selected(selected, cfg.entities, sheet_names)
    print(f"\n-- groups: {len(groups)} --")
    for (cluster, sheet), members in groups.items():
        refs = sorted(m.ref for m in members)
        print(f"  ({cluster!r}, {sheet!r}) -> {refs}")
        if not sheet:
            print("     DROPPED: sheet is None/empty (gate 1)")
            continue
        snap_members = [s for s in snapshot
                        if s.cluster == cluster and sheet in _sheet_chain(s, sheet_names)]
        print(f"     snapshot members ({len(snap_members)}): "
              f"{sorted(s.ref for s in snap_members)}")
        if not snap_members:
            print("     DROPPED: no snapshot member matches cluster+sheet (gate 2)")
            continue
        not_sel = sorted(s.ref for s in snap_members if s.ref not in sel_refs)
        if not_sel:
            print(f"     DROPPED: not fully selected (gate 3) — missing {not_sel}")
        else:
            print("     OK: fully selected")

    result = fully_selected_clusters(selected, snapshot, cfg.entities, (),
                                     sheet_names=sheet_names)
    print(f"\nfully_selected_clusters -> {len(result)}")
    for c in result:
        print(f"  cluster={c.cluster!r} sheet={c.sheet!r} entity={c.entity_name!r} "
              f"cell={c.cell!r} refs={c.refs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
