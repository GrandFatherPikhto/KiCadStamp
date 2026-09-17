#!/usr/bin/env python3
"""Stage 5 probe — what would "Extract spoke" find for the current selection?

2026-09-17, chat plan "Extract spoke" (stage 5 of the spoke work). Select ONE
spoke pair (both components; copper is ignored here) in the PCB editor, then run
this. It walks the steps the planned dialog would take and prints each verdict:

  1. selection checks — every component has a Role, no Role twice, one Cluster;
  2. the spoke criterion — does some selected Role repeat inside that Cluster?
  3. cells whose role set equals the selection's (the "reuse an existing cell"
     candidates);
  4. the chain the pair belongs to: for every net of the selected pads, the
     chains of the profile on that net, their anchor, the anchor pad NEAREST to
     the pair, and whether a spoke already sits on that pad; for nets without a
     chain, the nearest foreign pads on the net (anchor candidates);
  5. for a pad that already has a spoke: the pair the POOL gives that spoke
     (the "on redraw this spoke will get C41, C42" line), and whether it is the
     selected pair;
  6. the offset a new spoke would store, read the way "Read current position"
     would: the cell frame by the selected refs (_live_cluster_frame with the
     role lookup pinned), minus the pad — compared with the existing spoke's
     stored shift/rotation when there is one.

READ-ONLY: own KiCad socket, closed in `finally`; writes nothing.

Usage (select a pair in KiCad first):
    python -m kicadstamp.diagnostics.probe_extract_spoke_from_selection [config]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.docks import live_position                                         # noqa: E402
from kicadstamp.cluster_matching import cluster_prefix_match                 # noqa: E402
from kicadstamp.config import chain_effective_name, load_config              # noqa: E402
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME         # noqa: E402
from kicadstamp.diagnostics.probe_spoke_cell_identification import (       # noqa: E402
    angle_delta,
    first_line,
    fmt_mm,
    replay_spoke_assignment,
    roles_pinned_to,
)
from kicadstamp.domain.board import Footprint                                # noqa: E402
from kicadstamp.exceptions import ValidationError                            # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter                       # noqa: E402
from kicadstamp.placement.services.component_resolver import ComponentResolver  # noqa: E402
from kicadstamp.utils.units import MM                                        # noqa: E402

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103" / "config.sexp")
# A net touching more footprints than this is a plane (GND-like), not a spoke rail.
PLANE_NET_MEMBERS = 40
CANDIDATES = 3


def distance_mm(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5 / MM


def main() -> int:
    config = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_PROFILE)
    cfg, ctx = load_config(config)
    sheet_names = dict(ctx.sheet_names or {})
    print(f"profile: {config}")
    adapter = KiCadBoardAdapter()
    try:
        adapter.refresh_board()
        footprints = adapter.get_footprints()
        fp_by_ref = {fp.ref: fp for fp in footprints}
        selected = [i for i in adapter.get_selected_items() if isinstance(i, Footprint)]
        if not selected:
            print("nothing selected — select one spoke pair in the PCB editor")
            return 1
        roles = {fp.ref: adapter.get_field_value(fp, ROLE_FIELD_NAME) for fp in selected}
        clusters = {fp.ref: adapter.get_field_value(fp, CLUSTER_FIELD_NAME) for fp in selected}

        print("\n1. selection: " + ", ".join(f"{r}[{roles[r]}|{clusters[r]}]" for r in sorted(roles)))
        problems = []
        if any(not v for v in roles.values()):
            problems.append("no Role on " + ", ".join(sorted(r for r, v in roles.items() if not v)))
        twice = sorted(v for v, n in Counter(roles.values()).items() if v and n > 1)
        if twice:
            problems.append("Role selected twice: " + ", ".join(twice))
        if len(set(clusters.values())) != 1:
            problems.append(f"several clusters: {sorted(set(map(str, clusters.values())))}")
        print("   " + ("; ".join(problems) if problems else "OK"))
        if problems:
            return 1
        cluster = next(iter(clusters.values()))
        role_set = set(roles.values())

        counts: Counter = Counter()
        for fp in footprints:
            role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role in role_set and cluster_prefix_match(
                    adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or "", cluster or ""):
                counts[role] += 1
        spoke = any(n > 1 for n in counts.values())
        print(f"\n2. roles inside cluster {cluster!r}: "
              + ", ".join(f"{r} x{counts[r]}" for r in sorted(role_set))
              + ("  -> SPOKE (say so in the dialog: 'role X occurs N times — extracting as a spoke')"
                 if spoke else "  -> unique roles: this is an ordinary cluster, not a spoke"))

        cells = sorted(n for n, c in cfg.cells.items() if {s.role for s in c.components} == role_set)
        print(f"\n3. cells with exactly this role set: {cells or 'none (a new cell would be created)'}")

        centre_x = sum(fp.position.x for fp in selected) / len(selected)
        centre_y = sum(fp.position.y for fp in selected) / len(selected)
        centre = type(selected[0].position).from_xy(int(centre_x), int(centre_y))
        selected_refs = {fp.ref for fp in selected}
        nets = sorted({pad.net_name for fp in selected for pad in adapter.get_footprint_pads(fp)
                       if pad.net_name})
        members: dict[str, list] = {net: [] for net in nets}
        for fp in footprints:
            if fp.ref in selected_refs:
                continue
            for pad in adapter.get_footprint_pads(fp):
                if pad.net_name in members:
                    members[pad.net_name].append((fp, pad))

        print("\n4. nets of the pair:")
        frame = None
        if cells:
            role_to_ref = {role: ref for ref, role in roles.items()}
            try:
                with roles_pinned_to(fp_by_ref, role_to_ref):
                    frame = live_position._live_cluster_frame(
                        adapter, cfg.cells[cells[0]], cluster or "", "", sheet_names)
                print(f"   cell frame by the selected refs ({cells[0]}): origin {fmt_mm(frame[0])} "
                      f"rot {frame[1]:.3f} mirror {frame[2]}")
            except ValidationError as exc:
                print(f"   cell frame by the selected refs: FAILS — {first_line(exc)}")
        for net in nets:
            owners = {fp.ref for fp, _pad in members[net]}
            if len(owners) > PLANE_NET_MEMBERS:
                print(f"   {net!r}: {len(owners)} footprints — plane net, ignored")
                continue
            chains = [c for c in cfg.chains if c.net == net and not c.retired]
            if not chains:
                near = sorted(members[net], key=lambda fp_pad: distance_mm(fp_pad[1].position, centre))
                listing = ", ".join(
                    f"{fp.ref}.{pad.number}[{adapter.get_field_value(fp, ROLE_FIELD_NAME)}] "
                    f"{distance_mm(pad.position, centre):.2f} mm" for fp, pad in near[:CANDIDATES])
                print(f"   {net!r}: no chain; nearest pads: {listing or '-'}")
                continue
            for chain in chains:
                name = chain_effective_name(chain)
                try:
                    anchor = ComponentResolver(adapter, cfg, sheet_names).resolve_anchor_fp(
                        chain.anchor_ref, chain.anchor_role, chain.anchor_sheet,
                        chain.anchor_cluster, label=f"chain {chain.net!r}")
                except ValidationError as exc:
                    print(f"   {net!r}: chain {name!r} anchor unresolved — {first_line(exc)}")
                    continue
                pads = [p for p in adapter.get_footprint_pads(anchor) if p.net_name == net]
                if not pads:
                    print(f"   {net!r}: chain {name!r} anchor {anchor.ref} has no pad on the net")
                    continue
                pad = min(pads, key=lambda p: distance_mm(p.position, centre))
                existing = next((s for s in chain.spokes if s.pad == pad.number and not s.retired), None)
                print(f"   {net!r}: chain {name!r}, anchor {anchor.ref}, nearest pad {pad.number} "
                      f"at {distance_mm(pad.position, centre):.2f} mm; "
                      + (f"spoke EXISTS on it (cell {existing.cell!r})" if existing
                         else "no spoke on it yet — Extract spoke would add one"))
                if existing is not None:
                    assignment, error = replay_spoke_assignment(adapter, cfg, chain, sheet_names)
                    slot = next((r2r for s, r2r in assignment if s is existing), None)
                    if slot is None:
                        print(f"      5. pool slot for pad {pad.number}: unavailable ({error})")
                    else:
                        same = set(slot.values()) == selected_refs
                        print(f"      5. on redraw this spoke gets "
                              + ", ".join(f"{r}={ref}" for r, ref in sorted(slot.items()))
                              + ("  (= the selected pair)" if same else "  (NOT the selected pair)"))
                if frame is not None:
                    dx = (frame[0].x - pad.position.x) / MM
                    dy = (frame[0].y - pad.position.y) / MM
                    line = (f"      6. offset read from the board: shift ({dx:+.4f}, {dy:+.4f}) mm, "
                            f"rotation {frame[1]:.3f}")
                    if existing is not None and existing.radius_mm is None:
                        line += (f"; stored spoke: shift ({existing.shift_x_mm:+.4f}, "
                                 f"{existing.shift_y_mm:+.4f}) rotation {existing.rotation_deg:.3f}"
                                 f" -> delta ({dx - existing.shift_x_mm:+.4f}, "
                                 f"{dy - existing.shift_y_mm:+.4f}) mm, "
                                 f"{angle_delta(frame[1], existing.rotation_deg):.3f}°")
                    print(line)
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
