# kicadstamp/diagnostics/probe_inter_cluster_nets_gnd.py
"""
Probe: the inter-node UNITS the STRICT geometric rule offers for the CURRENT
board selection — one row per piece of copper between pads, with its pad
signature, the nodes it connects and the counts the dialog's third tab shows.

OBSOLETE PREDECESSOR (kept under the same filename for the paper trail): this
probe used to measure the two HEURISTICS that no longer exist — the
RULE_NETS={"GND"} name exclusion and the DEFAULT_MAX_CLUSTER_COVERAGE<=2
threshold (both were the 2026-09-01 fix for a live GND leak: GND on 6 clusters,
+3V3 on 3, real links on exactly 2). Plan_2026_09_12_internode_copper_core (stage
Э5; design §4/§14) replaced them with a STRICT RULE: copper is inter-node when
all its pads belong to 2+ of the tree's nodes. A GND BRIDGE between two nodes is
therefore offered now, while a GND pour is not (a zone is not part of the
connectivity graph at all) and a GND stub inside one cluster is not either.

The nodes it groups by are the DIALOG's nodes (the Cluster field plus its
fallback to refdes), so this probe reads the EFFECTIVE Role/Cluster values and
goes through the adapter factory WITH the profile (plan Т2а): a board-only read
could group the very copper it is judging differently from the dialog.

Read-only: connects to the live board, reads the selection, prints a report.
Run it with KiCad open, a selection made, e.g.:

    .venv/bin/python kicadstamp/diagnostics/probe_inter_cluster_nets_gnd.py [PROFILE]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103" / "config.sexp")


def main() -> int:
    from kicadstamp.domain.board import Footprint, Track, Via
    from kicadstamp.internode_copper import (
        CopperVerdict,
        classify_unit,
        find_copper_units,
    )
    from kicadstamp.adapter_factory import create_board_adapter

    config = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_PROFILE)

    # 20 s ON PURPOSE (Э4, plan_2026_09_13_timeout_sweep) — deliberately NOT
    # DEFAULT_TIMEOUT_MS: one probe reads the whole selection's copper plus the
    # board's item lists, so a low ceiling would abort the report, not the board.
    adapter = create_board_adapter(timeout_ms=20000, config_path=config)
    adapter.refresh_board()
    selected = list(adapter.get_selected_items() or [])
    if not selected:
        print("No board selection — select the components of the clusters first.")
        return 1

    items = [i for i in selected if isinstance(i, (Track, Via))]
    footprints = [i for i in selected if isinstance(i, Footprint)]
    node_by_ref = {fp.ref: (adapter.get_field_value(fp, "Cluster") or fp.ref)
                   for fp in footprints}

    print(f"selection: {len(footprints)} footprints, {len(items)} copper items")
    print(f"nodes ({len(set(node_by_ref.values()))}): "
          f"{', '.join(sorted(set(node_by_ref.values())))}")

    units, warnings = find_copper_units(adapter, items, footprints=footprints)
    for warning in warnings:
        print(f"WARNING: {warning}")

    offered = 0
    for unit in units:
        verdict = classify_unit(unit, node_by_ref)
        pads = ", ".join(f"{p.ref}.{p.pad}" for p in unit.pads) or "-"
        mark = "OFFERED" if verdict is CopperVerdict.INTERNODE else f"({verdict.value})"
        print(f"  [{mark}] net={unit.net_name!r} pads=[{pads}] "
              f"tracks={len(unit.tracks)} vias={len(unit.vias)}")
        offered += verdict is CopperVerdict.INTERNODE

    print(f"\n{offered} of {len(units)} unit(s) are inter-node copper "
          f"(the dialog's third tab and the re-read both offer exactly these).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
