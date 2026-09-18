#!/usr/bin/env python3
"""Stage 1 probe — can the cell editor read a SPOKE cell off the live board?

2026-09-17, design_2026_09_17_geometry_tree_and_user_tree (§6 В18) and the chat
plan "identify by selection" (stage 1 of the spoke work). Background: a spoke
cell's Cluster holds the same Role several times (FPGA_PWR_BANK: 4x C_FPGA_BULK +
4x C_FPGA_BYPASS), so the cell editor's live frame reader
gui/docks/live_position._live_cluster_frame — which looks every role up in the
working cluster and needs it to be UNIQUE — cannot find the instance.

For every chain of the profile this prints:

  1. the role multiplicity of each spoke cell inside its pool cluster on the live
     board (the "this is a spoke" criterion: some role occurs more than once);
  2. what today's _live_cluster_frame(cell, cluster) returns — the frame, or the
     exact error the user sees;
  3. per spoke, the pair the REDRAW gives it (pool replay: the same
     ComponentResolver.build_pools + consume_role_to_ref sequence
     ManualPositionCalculator.compute_raw_positions runs), and the frame
     _live_cluster_frame computes when its role lookup is PINNED to exactly those
     refs — i.e. what "identify by selection" would read once the refs are known;
  4. how far the redraw plan (compute_raw_positions) is from the live board for
     those refs: ~0 means the board is synced and "the pair standing at pad N" IS
     the pool's pair for pad N;
  5. the spoke's own stored origin (pad + shift, or polar) next to the frame
     origin, to see whether the frame read by refs lands on the spoke origin.

With --selection it additionally classifies the CURRENT board selection the way
the planned "Identify by selection" button would: ordinary cluster (unique
roles -> cluster/sheet) or spoke (duplicated roles -> refs), and for a spoke
which chain/pad's pool slot the selected pair is.

READ-ONLY: own KiCad socket, closed in `finally`; nothing is written to the
board, the profile or the registry. The role-lookup pin is a monkeypatch that is
always restored.

Usage:
    python -m kicadstamp.diagnostics.probe_spoke_cell_identification [config]
        [--chain NET] [--selection]
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.docks import live_position                                         # noqa: E402
from kicadstamp.cluster_matching import cluster_prefix_match                 # noqa: E402
from kicadstamp.config import chain_effective_name, load_config              # noqa: E402
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME         # noqa: E402
from kicadstamp.domain.board import Footprint                                # noqa: E402
from kicadstamp.exceptions import ValidationError                            # noqa: E402
from kicadstamp.geometry.spoke_layout import local_to_absolute               # noqa: E402
from kicadstamp.adapter_factory import create_board_adapter                       # noqa: E402
from kicadstamp.placement.services.component_resolver import ComponentResolver  # noqa: E402
from kicadstamp.placement.services.manual_position_calculator import (      # noqa: E402
    ManualPositionCalculator,
    chain_clusters_needed,
    chain_roles_needed,
    consume_role_to_ref,
)
from kicadstamp.utils.units import MM                                        # noqa: E402

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103" / "config.sexp")

# Above this the redraw plan and the board disagree (not nm rounding).
SYNC_TOLERANCE_MM = 1e-3
SYNC_TOLERANCE_DEG = 0.01


def first_line(exc: Exception) -> str:
    """The first meaningful line — format_fatal_error frames its text with
    '=====' rulers, which would otherwise be all that gets printed."""
    for line in str(exc).splitlines():
        line = line.strip()
        if line and set(line) - set("=-─ "):
            return line
    return type(exc).__name__


def fmt_mm(vec) -> str:
    return f"({vec.x / MM:+9.4f}, {vec.y / MM:+9.4f})"


def angle_delta(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


@contextlib.contextmanager
def roles_pinned_to(fp_by_ref: dict, role_to_ref: dict):
    """Make _live_cluster_frame resolve each role to the GIVEN ref instead of
    searching the cluster — the one link the planned identification replaces.
    Everything after that lookup (mirror, pad mount, rotation fit) runs as is."""
    original = live_position.resolve_footprint_by_cluster_role

    def pinned(adapter, cluster, role, label, **_kwargs):
        ref = role_to_ref.get(role)
        if ref is None or ref not in fp_by_ref:
            raise ValidationError(f"role {role!r} is not pinned to a live ref")
        return fp_by_ref[ref]

    live_position.resolve_footprint_by_cluster_role = pinned
    try:
        yield
    finally:
        live_position.resolve_footprint_by_cluster_role = original


def replay_spoke_assignment(adapter, cfg, chain, sheet_names) -> tuple[list, str | None]:
    """[(spoke, role_to_ref)] in chain order, mirroring compute_raw_positions'
    consumption exactly: retired spokes, spokes whose cell is missing and spokes
    whose pad the anchor lacks consume NOTHING (they `continue` before the pop).
    Returns (assignment, error) — error is the first-line message when the anchor
    or the pool cannot be resolved."""
    if chain.anchor_point is not None:
        return [], "anchor_point chain — replay skipped (needs resolved points)"
    try:
        anchor = ComponentResolver(adapter, cfg, sheet_names).resolve_anchor_fp(
            chain.anchor_ref, chain.anchor_role, chain.anchor_sheet,
            chain.anchor_cluster, label=f"chain {chain.net!r}")
        pools = ComponentResolver.build_pools(
            adapter, chain.net, chain_roles_needed(cfg, chain),
            chain_clusters_needed(chain))
    except ValidationError as exc:
        return [], first_line(exc)
    out = []
    for spoke in chain.spokes:
        if spoke.retired:
            continue
        cell = cfg.cells.get(spoke.cell)
        if cell is None or adapter.get_pad_by_number(anchor, spoke.pad) is None:
            continue
        try:
            out.append((spoke, consume_role_to_ref(pools[spoke.cluster], cell, spoke.pad)))
        except ValidationError as exc:
            return out, first_line(exc)
    return out, None


def role_multiplicity(adapter, footprints, cluster: str | None, roles: set[str]) -> Counter:
    counts: Counter = Counter()
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role not in roles:
            continue
        if cluster is not None:
            fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
            if not cluster_prefix_match(fp_cluster, cluster):
                continue
        counts[role] += 1
    return counts


def spoke_origin(adapter, anchor, spoke):
    pad = adapter.get_pad_by_number(anchor, spoke.pad)
    if pad is None:
        return None
    if spoke.radius_mm is not None:
        return local_to_absolute(pad.position, spoke.radius_mm, 0.0, spoke.angle_deg)
    return type(pad.position).from_xy(pad.position.x + int(spoke.shift_x_mm * MM),
                                      pad.position.y + int(spoke.shift_y_mm * MM))


def probe_chain(adapter, cfg, chain, sheet_names, fp_by_ref) -> dict:
    name = chain_effective_name(chain)
    stats = {"spokes": 0, "frames_ok": 0, "synced": 0, "today_fails": 0}
    print(f"\n=== chain {name!r}  net {chain.net!r}  anchor "
          f"{chain.anchor_role or chain.anchor_ref or chain.anchor_point!r}")
    footprints = list(fp_by_ref.values())
    if not any(not spoke.retired for spoke in chain.spokes):
        print("  no active spokes")
        return stats

    # 1 + 2: per (cell, pool cluster)
    seen = set()
    for spoke in chain.spokes:
        if spoke.retired or (spoke.cell, spoke.cluster) in seen:
            continue
        seen.add((spoke.cell, spoke.cluster))
        cell = cfg.cells.get(spoke.cell)
        if cell is None:
            print(f"  cell {spoke.cell!r}: NOT IN CONFIG")
            continue
        roles = {slot.role for slot in cell.components}
        counts = role_multiplicity(adapter, footprints, spoke.cluster, roles)
        kind = ("SPOKE (a role repeats in the cluster)"
                if any(n > 1 for n in counts.values()) else "unique roles")
        listing = ", ".join(f"{r} x{counts.get(r, 0)}" for r in sorted(roles)) or "(no roles)"
        print(f"  cell {spoke.cell!r} / cluster {spoke.cluster!r}: {listing}  -> {kind}")
        if spoke.cluster and roles:
            try:
                pos, rot, mirror = live_position._live_cluster_frame(
                    adapter, cell, spoke.cluster, "", sheet_names)
                print(f"    today's _live_cluster_frame: origin {fmt_mm(pos)} "
                      f"rot {rot:.3f} mirror {mirror}")
            except ValidationError as exc:
                stats["today_fails"] += 1
                print(f"    today's _live_cluster_frame: FAILS — {first_line(exc)}")

    # 3 + 4 + 5: per spoke
    assignment, error = replay_spoke_assignment(adapter, cfg, chain, sheet_names)
    if error:
        print(f"  pool replay stopped: {error}")
    if not assignment:
        return stats
    try:
        planned, _vias, _tracks = ManualPositionCalculator(
            adapter, cfg, sheet_names).compute_raw_positions([chain])
        planned_by_ref = {p.ref: p for p in planned}
    except ValidationError as exc:
        planned_by_ref = {}
        print(f"  compute_raw_positions failed: {first_line(exc)}")
    anchor = None
    with contextlib.suppress(ValidationError):
        anchor = ComponentResolver(adapter, cfg, sheet_names).resolve_anchor_fp(
            chain.anchor_ref, chain.anchor_role, chain.anchor_sheet,
            chain.anchor_cluster, label=f"chain {chain.net!r}")

    for spoke, role_to_ref in assignment:
        stats["spokes"] += 1
        cell = cfg.cells[spoke.cell]
        pair = ", ".join(f"{role}={ref}" for role, ref in sorted(role_to_ref.items()))
        line = f"  pad {spoke.pad:>4}: pool -> {pair}"
        try:
            with roles_pinned_to(fp_by_ref, role_to_ref):
                pos, rot, mirror = live_position._live_cluster_frame(
                    adapter, cell, spoke.cluster or "", "", sheet_names)
            stats["frames_ok"] += 1
            line += f"\n      frame by refs: origin {fmt_mm(pos)} rot {rot:8.3f} mirror {mirror}"
            origin = spoke_origin(adapter, anchor, spoke) if anchor is not None else None
            if origin is not None:
                d = ((pos.x - origin.x) ** 2 + (pos.y - origin.y) ** 2) ** 0.5 / MM
                line += (f"\n      spoke origin (pad+shift): {fmt_mm(origin)} "
                         f"rotation_deg {spoke.rotation_deg:8.3f}  |frame-origin| {d:.4f} mm, "
                         f"rot delta {angle_delta(rot, spoke.rotation_deg):.3f}°")
        except ValidationError as exc:
            line += f"\n      frame by refs: FAILS — {first_line(exc)}"
        worst_mm, worst_deg, missing = 0.0, 0.0, []
        for ref in role_to_ref.values():
            plan, live = planned_by_ref.get(ref), fp_by_ref.get(ref)
            if plan is None or live is None:
                missing.append(ref)
                continue
            dx, dy = (plan.dest.x - live.position.x) / MM, (plan.dest.y - live.position.y) / MM
            worst_mm = max(worst_mm, (dx * dx + dy * dy) ** 0.5)
            worst_deg = max(worst_deg, angle_delta(plan.angle_deg, live.angle_deg))
        synced = not missing and worst_mm <= SYNC_TOLERANCE_MM and worst_deg <= SYNC_TOLERANCE_DEG
        stats["synced"] += int(synced)
        line += (f"\n      redraw plan vs board: max {worst_mm:.4f} mm, {worst_deg:.3f}°"
                 f"{'  missing ' + ','.join(missing) if missing else ''}"
                 f"  [{'SYNCED' if synced else 'NOT SYNCED'}]")
        print(line)
    return stats


def probe_selection(adapter, cfg, sheet_names, fp_by_ref) -> None:
    print("\n=== current selection, as 'Identify by selection' would see it")
    selected = [i for i in adapter.get_selected_items() if isinstance(i, Footprint)]
    if not selected:
        print("  nothing selected (footprints only)")
        return
    roles = {fp.ref: adapter.get_field_value(fp, ROLE_FIELD_NAME) for fp in selected}
    clusters = {fp.ref: adapter.get_field_value(fp, CLUSTER_FIELD_NAME) for fp in selected}
    print("  " + ", ".join(f"{r}[{roles[r]}|{clusters[r]}]" for r in sorted(roles)))
    untagged = sorted(r for r, role in roles.items() if not role)
    if untagged:
        print(f"  REFUSE: no Role on {', '.join(untagged)}")
        return
    dup = sorted(r for r, n in Counter(roles.values()).items() if n > 1)
    if dup:
        print(f"  REFUSE: role(s) selected twice: {', '.join(dup)}")
        return
    if len(set(clusters.values())) != 1:
        print(f"  REFUSE: several clusters: {sorted(set(map(str, clusters.values())))}")
        return
    cluster = next(iter(clusters.values()))
    role_set = set(roles.values())
    counts = role_multiplicity(adapter, list(fp_by_ref.values()), cluster, role_set)
    cells = sorted(n for n, c in cfg.cells.items()
                   if {s.role for s in c.components} == role_set)
    print(f"  cluster {cluster!r}; role counts in it: "
          + ", ".join(f"{r} x{counts[r]}" for r in sorted(role_set)))
    print(f"  cells with exactly this role set: {cells or 'none'}")
    if all(n == 1 for n in counts.values()):
        print("  -> ORDINARY cluster: identify by (cluster, sheet), refs not needed")
        return
    print("  -> SPOKE: identify by refs " + ", ".join(sorted(roles)))
    wanted = set(roles)
    hits = []
    for chain in cfg.chains:
        if not any(sp.cell in cells for sp in chain.spokes if not sp.retired):
            continue
        assignment, _err = replay_spoke_assignment(adapter, cfg, chain, sheet_names)
        for spoke, role_to_ref in assignment:
            if set(role_to_ref.values()) == wanted:
                hits.append(f"chain {chain_effective_name(chain)!r} pad {spoke.pad}")
    if not cells:
        print("  pool slot of this pair: none — no cell with this role set exists yet, "
              "so no spoke can use the pair")
    else:
        print("  pool slot of this pair: " + (", ".join(hits) if hits
              else "NONE — the redraw gives these refs to no single spoke"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", nargs="?", default=str(DEFAULT_PROFILE))
    parser.add_argument("--chain", help="only the chain with this net / name")
    parser.add_argument("--selection", action="store_true",
                        help="also classify the current board selection")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(message)s")

    cfg, ctx = load_config(args.config)
    sheet_names = dict(ctx.sheet_names or {})
    print(f"profile: {args.config}")
    adapter = create_board_adapter(config_path=args.config)
    try:
        adapter.refresh_board()
        fp_by_ref = {fp.ref: fp for fp in adapter.get_footprints()}
        totals: Counter = Counter()
        for chain in cfg.chains:
            if chain.retired:
                continue
            if args.chain and args.chain not in (chain.net, chain_effective_name(chain)):
                continue
            totals.update(probe_chain(adapter, cfg, chain, sheet_names, fp_by_ref))
        print(f"\nTOTAL: spokes {totals['spokes']}, frame by refs OK {totals['frames_ok']}, "
              f"synced {totals['synced']}, today's cluster frame failing "
              f"(cell/cluster pairs) {totals['today_fails']}")
        if args.selection:
            probe_selection(adapter, cfg, sheet_names, fp_by_ref)
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
