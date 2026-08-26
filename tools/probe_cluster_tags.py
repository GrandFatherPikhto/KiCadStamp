#!/usr/bin/env python3
"""
probe_cluster_tags.py — read-only audit: for one or more top-level
ClonePlacements, resolves roles exactly like Placer's Redraw would (the same
ClonePositionCalculator machinery, including recursion into nested
CellPlacements) and checks whether each resolved component's LIVE Cluster
field on the board matches what the config declares for the placement level
that owns it — WITHOUT moving anything or writing any field.

Why this exists (2026-08-26): dac_buf (a reusable composite cell) had two
Cluster-related live bugs the same day — _tag_cluster tagging every resolved
component (including nested sub-cells') with the TOP placement's Cluster
(handoff tag_cluster_overtag, fixed 153151e), and, separately, a reused
composite resolving the WRONG channel's components entirely (handoff
cell_placement_sheet_inherit / cell_placement_net_sheet_template, fixed
cf1041a/36ef950). Both needed manual live-board recovery via
set_field_values_bulk. This tool answers "is the board's Cluster field
correct right now", on demand, without re-running Redraw (which also moves
components) — by reusing PlacedComponentInfo.owner_ref (2026-08-26, the same
field _tag_cluster itself now filters by), so a composite's nested sub-cells
are checked against THEIR OWN cluster:, not the top-level one.

Never writes to the board — no --dry-run flag needed, there is nothing to
apply.

Usage:
  python tools/probe_cluster_tags.py --config profiles/3ch-awg-tia-v103/3ch-awg-tia.yaml
  python tools/probe_cluster_tags.py --config ... --match DAC_BUF
  python tools/probe_cluster_tags.py --config ... --match CH0_DAC_BUF --match CH1_DAC_BUF

Exit code: 1 if any mismatch was found, 0 otherwise (0 also when nothing
matched --match — see the printed message in that case).
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadstamp.config import load_config, clone_placement_effective_name
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.placement.services.clone_position_calculator import ClonePositionCalculator


def _expected_clusters(cfg, top) -> Dict[str, str | None]:
    """Maps every placement_label reachable from `top` (itself, plus every
    nested CellPlacement recursively) to the Cluster the config declares for
    it — the SAME identity ClonePositionCalculator._resolve_one_level uses as
    owner_ref on each resolved PlacedComponentInfo: the top-level's own
    components carry clone_placement_effective_name(top); a nested
    CellPlacement's components carry its own .name (flat, not path-prefixed,
    matching placement_label's own convention)."""
    expected: Dict[str, str | None] = {clone_placement_effective_name(top): top.cluster}

    def walk(cell_name: str, seen: frozenset) -> None:
        if cell_name in seen:
            return  # defensive only — load_config already rejects real cycles
        seen = seen | {cell_name}
        cell = cfg.cells.get(cell_name)
        if cell is None:
            return
        for cp in cell.clone_placements:
            expected[cp.name] = cp.cluster
            if cp.cell:
                walk(cp.cell, seen)

    if top.cell:
        walk(top.cell, frozenset())
    return expected


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="Path to the profile YAML")
    ap.add_argument("--match", action="append", default=[],
                     help="Only audit top-level clone_placements whose name or "
                          "Cluster equals this (repeatable); default: all, non-retired")
    args = ap.parse_args()

    cfg, ctx = load_config(args.config)
    sheet_names = ctx.sheet_names or {}

    tops = [cp for cp in cfg.clone_placements if not cp.retired]
    if args.match:
        wanted = set(args.match)
        tops = [cp for cp in tops
                if clone_placement_effective_name(cp) in wanted or cp.cluster in wanted]
    if not tops:
        print("No matching top-level clone_placements found.")
        return

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    calc = ClonePositionCalculator(adapter, cfg, sheet_names=sheet_names)

    checked = 0
    mismatches = 0
    for top in tops:
        top_label = clone_placement_effective_name(top)
        expected = _expected_clusters(cfg, top)
        try:
            placed, _vias, _tracks = calc.compute_raw_positions([top])
        except Exception as e:
            print(f"[{top_label}] FAILED to resolve: {e}")
            continue

        print(f"[{top_label}] {len(placed)} component(s) resolved")
        by_owner: Dict[str, List[str]] = {}
        for info in placed:
            by_owner.setdefault(info.owner_ref, []).append(info.ref)

        for owner_ref in sorted(by_owner):
            exp = expected.get(owner_ref)
            for ref in sorted(by_owner[owner_ref]):
                fp = adapter.get_footprint(ref)
                actual = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) if fp else None
                checked += 1
                if exp is None:
                    status = "(no expected Cluster declared)"
                elif actual != exp:
                    status = f"MISMATCH — board has {actual!r}"
                    mismatches += 1
                else:
                    status = "ok"
                print(f"    {ref:<8} owner={owner_ref:<20} expected={exp!r:<20} {status}")

    print(f"\n{checked} component(s) checked, {mismatches} mismatch(es).")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
