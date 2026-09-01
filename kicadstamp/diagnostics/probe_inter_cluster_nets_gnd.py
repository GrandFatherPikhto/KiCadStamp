#!/usr/bin/env python3
"""probe_inter_cluster_nets_gnd.py — live check of the "Extract tree..." third-tab
inter-cluster net list on a REAL selection that contains a shared GND.

Background (2026-09-01 review): detect_inter_cluster_nets() in
gui/docks/tree_from_selection.py gathers, per fully-selected Cluster, the FULL
pad-net inventory of its footprints from the snapshot's Selected.nets (i.e.
GND/+3V3 and any other rail that happens to sit on a pad) and offers a net as an
inter-cluster capture candidate when its name appears on pads of 2+ Clusters AND
some selected track/via carries it. Originally the only suppression was
`rule_nets` (the registered chains' net: fields) — so if GND was NOT registered
as a Chain, a couple of selected GND stitching vias put "GND" into the list,
even though it is a ubiquitous rail, not a point-to-point inter-cluster link.

Live verification (3CH-AWG-TIA, 6 fully-selected Channel_0 Clusters): GND leaked
with 32 tracks / 25 vias under rule_nets=() AND rule_nets=chains (GND is not a
chain in this profile). A second leak — +3V3 (3 tracks / 1 via) — is NOT fixed by
RULE_NETS alone (RULE_NETS={"GND"} only). The distinguishing signal: real
point-to-point links sit on pads of EXACTLY 2 selected Clusters (coverage=2),
while GND sits on all 6 and +3V3 on 3.

The 2026-09-01 fix in detect_inter_cluster_nets():
  - RULE_NETS ({"GND"}) is ALWAYS subtracted (same default as the Cells/Extract
    dock), so GND is never offered even without a registered Chain;
  - a net on pads of MORE than `max_cluster_coverage` (default
    DEFAULT_MAX_CLUSTER_COVERAGE = 2) selected Clusters is a ubiquitous rail and
    is not offered — this removes +3V3-style global rails without config.

This probe replicates the exact data flow of
gui/dock_hub.extract_tree_from_selection() on the live board and prints:

  1. the INPUT data (so the noise is traceable):
       - the raw selection (counts of footprints/tracks/vias, and any other
         selected objects);
       - the nets of the SELECTED copper (raw_items): net -> #tracks/#vias;
       - per fully-selected Cluster: the pad-net inventory used by
         detect_inter_cluster_nets (this is where GND always appears);
  2. the third-tab inter-cluster list as the CURRENT (fixed) code computes it,
     under scenarios that isolate the two exclusions:
       - default (rule_nets = (), coverage > 2 = rail) — GND and +3V3 should be
         GONE, real 2-cluster links should remain;
       - rule_nets = chains' net: fields       — the config as actually loaded
                                                 (dock_hub passes cfg.rules);
       - coverage-rule OFF (max_cluster_coverage=10) — proves the coverage rule
                                                 is what removes +3V3: it comes
                                                 back; GND stays gone (RULE_NETS
                                                 still subtracted).

Read-only: connects to the live board, reads fields/pads/nets/selection, writes
nothing. Requires KiCad running with the board open and the intended Clusters
fully selected (all components of a (Cluster, sheet) instance).

Run:
    python -m kicadstamp.diagnostics.probe_inter_cluster_nets_gnd \
        [--profile profiles/3ch-awg-tia-v103-01/3ch-awg-tia.sexp]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config  # noqa: E402
from kicadstamp.domain.board import Footprint, Track, Via  # noqa: E402
from kicadstamp.explore import Board  # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter  # noqa: E402
from kicadstamp.net_resolution import RULE_NETS  # noqa: E402
from gui.docks.reead import fully_selected_clusters  # noqa: E402
from gui.docks.tree_from_selection import (  # noqa: E402
    DEFAULT_MAX_CLUSTER_COVERAGE,
    _cluster_nets,
    detect_inter_cluster_nets,
)

DEFAULT_PROFILE = str(Path(__file__).resolve().parents[2]
                      / "profiles" / "3ch-awg-tia-v103-01" / "3ch-awg-tia.sexp")


def _selected_copper_nets(raw_items) -> dict[str, list[int]]:
    """net -> [tracks, vias] over the SELECTED tracks/vias only — the exact
    counts detect_inter_cluster_nets' final filter tallies from raw_items."""
    counts: dict[str, list[int]] = {}
    for item in raw_items:
        if isinstance(item, Track):
            if item.net_name:
                counts.setdefault(item.net_name, [0, 0])[0] += 1
        elif isinstance(item, Via):
            if item.net_name:
                counts.setdefault(item.net_name, [0, 0])[1] += 1
    return counts


def _print_third_tab(label: str, nets) -> None:
    print(f"  [{label}]")
    if not nets:
        print("    (empty — no inter-cluster capture candidates)")
        return
    for n in nets:
        gnd = "  <-- GND" if n.net == "GND" else ""
        print(f"    {n.net:<32} tracks={n.track_count:<3} vias={n.via_count}{gnd}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="config file to load entities/chains/sheet names from")
    args = parser.parse_args()

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    print(f"connected to live board; profile: {args.profile}")

    cfg, ctx = load_config(str(args.profile))
    sheet_names = dict(ctx.sheet_names or {})
    chains = list(cfg.rules)  # backward-compat alias for cfg.chains (2026-09-01)
    chain_nets = {c.net for c in chains if c.net}
    print(f"chains in config: {len(chains)}; chain rule_nets: "
          f"{sorted(chain_nets) if chain_nets else '(empty)'}")
    print(f"RULE_NETS default: {sorted(RULE_NETS)}")
    print(f"max_cluster_coverage default: {DEFAULT_MAX_CLUSTER_COVERAGE}")

    # ── 1. INPUT DATA (exact dock_hub flow) ────────────────────────────────
    raw_items = adapter.get_selected_items()
    print(f"\n=== raw selection (adapter.get_selected_items): {len(raw_items)} items ===")
    kinds = Counter(
        "footprint" if isinstance(i, Footprint)
        else "track" if isinstance(i, Track)
        else "via" if isinstance(i, Via)
        else "other" for i in raw_items)
    print(f"  kinds: {dict(kinds)}")
    for item in raw_items:
        if not isinstance(item, (Footprint, Track, Via)):
            print(f"  OTHER (ignored by detect): {item!r}")

    board = Board(adapter, sheet_names)
    board.refresh()
    snapshot = board.select()  # full-board Selected inventory == GUI connection.snapshot
    print(f"  snapshot (full board): {len(snapshot)} footprints")

    selected_refs = {f.ref for f in raw_items if isinstance(f, Footprint)}
    selected_fps = [s for s in snapshot if s.ref in selected_refs]
    print(f"  selected footprints (Selected-wrapped): {len(selected_fps)}")

    clusters = fully_selected_clusters(
        selected_fps, snapshot, list(cfg.entities), (), sheet_names=sheet_names)
    clusters = [c for c in clusters if c.cluster and "\n" not in c.cluster]
    print(f"\n=== fully-selected Clusters: {len(clusters)} ===")
    if not clusters:
        print("  NONE — select ALL components of 2+ Clusters (Cluster tag + sheet) first.")
        return
    for c in clusters:
        print(f"  {c.cluster:<24} sheet={c.sheet!r:<12} entity={c.entity_name!r} "
              f"cell={c.cell!r} refs={sorted(c.refs)}")

    print("\n=== per-Cluster pad-net inventory (what _cluster_nets feeds "
          "detect_inter_cluster_nets) ===")
    for c, nets in zip(clusters, _cluster_nets(clusters, snapshot)):
        print(f"  {c.cluster:<24} ({len(nets)} nets): {sorted(nets)}")

    copper = _selected_copper_nets(raw_items)
    print(f"\n=== SELECTED copper nets (raw_items tracks/vias): "
          f"{len(copper)} nets ===")
    if not copper:
        print("  (no selected tracks/vias)")
    for net, (nt, nv) in sorted(copper.items()):
        print(f"  {net:<32} tracks={nt:<3} vias={nv}")

    # ── 2. THIRD-TAB LIST — scenarios isolating the two exclusions ─────────
    print("\n=== inter-cluster net list (third tab) ===")

    def _nets(rule_nets, max_cov):
        return detect_inter_cluster_nets(raw_items, clusters, snapshot,
                                         rule_nets=rule_nets,
                                         max_cluster_coverage=max_cov)

    scenarios = [
        ("default (rule_nets=(), coverage>2=rail)",
         (), DEFAULT_MAX_CLUSTER_COVERAGE),
        ("config as loaded (rule_nets=chains)",
         chain_nets, DEFAULT_MAX_CLUSTER_COVERAGE),
        ("coverage-rule OFF (max_cluster_coverage=10)",
         (), 10),
    ]
    for label, rule_nets, max_cov in scenarios:
        _print_third_tab(label, _nets(rule_nets, max_cov))

    def _has(nets, name):
        return any(n.net == name for n in nets)

    default = _nets((), DEFAULT_MAX_CLUSTER_COVERAGE)
    cov_off = _nets((), 10)
    print(f"\ndefault:      GND={'YES' if _has(default, 'GND') else 'no'}"
          f"   +3V3={'YES' if _has(default, '+3V3') else 'no'}"
          f"   candidates={[n.net for n in default]}")
    print(f"coverage OFF: GND={'YES' if _has(cov_off, 'GND') else 'no'}"
          f"   +3V3={'YES' if _has(cov_off, '+3V3') else 'no'}"
          f"   candidates={[n.net for n in cov_off]}")
    print("expected after the 2026-09-01 fix: GND is never offered (RULE_NETS "
          "always subtracted, as in the Cells/Extract dock); +3V3 is offered "
          "only when the coverage rule is off (it sits on 3+ selected Clusters "
          "= a ubiquitous rail, not a point-to-point link).")


if __name__ == "__main__":
    main()
