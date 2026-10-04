# kicadstamp/diagnostics/claude_probe_r70_copper_frames_2026_10_04.py
"""READ-ONLY probe for decisions R70/R71/R72 of
techdocs/handoff/claude/design_2026_10_04_geometry_and_user_trees.md
(all copper stored in the axes of its parent), Claude, 2026-10-04.

Question: when the stored copper records move from board axes to the axes of
their anchor part (field `frame part`), do any numbers have to change? They do
NOT when every anchor part stands at 0 deg on F.Cu — then part axes ARE board
axes. So, for every chain / net_trace / thermal_via_array of a profile:

  * which live footprint is its anchor, at what angle, on which side;
  * chains: how many spokes carry a non-zero rotation_deg / use polar mode;
  * net_traces: the stored capture-time anchor_rotation_deg vs the live angle
    (a net_trace is ALREADY a "snapshot" record: raw board-frame deltas plus
    the anchor angle at capture — kicadstamp/config/models.py NetTrace).

Reads the board only (its own kipy socket, closed at the end); writes nothing.
Run it against a COPY of the profile at the same depth (claude.md p.28):

    .venv/bin/python kicadstamp/diagnostics/claude_probe_r70_copper_frames_2026_10_04.py \
        profiles/<profile>-probecopy/config.sexp

An anchor that does not resolve is reported as a row, not skipped: "not found"
is a finding about the profile, not an absence of anchors (rule 39).
"""
import sys
from collections import Counter

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.config.loader import load_config
from kicadstamp.placement.services.clone_role_resolver import resolve_footprint_by_role


def _side(fp) -> str:
    name = getattr(fp.layer, "name", str(fp.layer))
    return "B" if "B_Cu" in name or "B.Cu" in name or name.endswith("BL_B_Cu") else "F"


def _resolve(adapter, sheet_names, *, ref=None, role=None, sheet=None, cluster=None,
             point=None, label=""):
    """(footprint | None, how) — the anchor footprint of one record."""
    if point:
        return None, f"point:{point}"
    try:
        if ref:
            fp = adapter.get_footprint(ref)
            return fp, (f"ref {ref}" if fp else f"ref {ref}: NOT ON BOARD")
        if role:
            fp = resolve_footprint_by_role(adapter, role, sheet, cluster,
                                           sheet_names, label)
            return fp, f"role {role}" + (f" sheet {sheet}" if sheet else "") + \
                (f" cluster {cluster}" if cluster else "")
    except Exception as e:  # a resolve failure is a row, never a skip
        return None, f"UNRESOLVED ({type(e).__name__}: {str(e).splitlines()[0][:70]})"
    return None, "no anchor fields"


def main(config_path: str) -> None:
    cfg, ctx = load_config(config_path)
    sheet_names = getattr(ctx, "sheet_names", None) or getattr(cfg, "sheet_names", None) or {}
    adapter = create_board_adapter(config_path=config_path)
    adapter.refresh_board()  # the adapter holds no board until the first refresh
    rows = []  # (kind, name, how, ref, angle, side, extra)
    try:
        for ch in cfg.chains:
            if ch.retired:
                continue
            fp, how = _resolve(adapter, sheet_names, ref=ch.anchor_ref, role=ch.anchor_role,
                               sheet=ch.anchor_sheet, cluster=ch.anchor_cluster,
                               point=ch.anchor_point, label=ch.name or ch.net)
            live = [s for s in ch.spokes if not s.retired]
            rot = sum(1 for s in live if abs(s.rotation_deg) > 1e-9)
            polar = sum(1 for s in live if s.radius_mm is not None)
            rows.append(("chain", ch.name or ch.net, how, fp,
                         f"spokes {len(live)}, rot!=0 {rot}, polar {polar}"))
        for nt in cfg.net_traces:
            if nt.retired:
                continue
            fp, how = _resolve(adapter, sheet_names, role=nt.anchor_role, sheet=nt.anchor_sheet,
                               cluster=nt.anchor_cluster, label=nt.name or nt.net)
            cap = nt.anchor_rotation_deg
            extra = f"tracks {len(nt.tracks)}, vias {len(nt.vias)}, captured angle " + \
                ("None (legacy)" if cap is None else f"{cap:.3f}")
            if fp is not None and cap is not None:
                extra += f", delta {fp.angle_deg - cap:+.3f}"
            rows.append(("net_trace", nt.name or nt.net, how, fp, extra))
        for tva in cfg.thermal_via_arrays:
            if getattr(tva, "retired", False):
                continue
            fp, how = _resolve(adapter, sheet_names, ref=getattr(tva, "anchor_ref", None),
                               role=getattr(tva, "anchor_role", None),
                               sheet=getattr(tva, "anchor_sheet", None),
                               cluster=getattr(tva, "anchor_cluster", None),
                               point=getattr(tva, "anchor_point", None),
                               label=getattr(tva, "name", "") or "")
            rows.append(("thermal", getattr(tva, "name", "?"), how, fp, ""))
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            close()

    print(f"profile: {config_path}")
    print(f"{'kind':<10} {'record':<34} {'ref':<8} {'angle':>8} {'side':<4} anchor / notes")
    print("-" * 120)
    tally = Counter()
    for kind, name, how, fp, extra in rows:
        if fp is None:
            print(f"{kind:<10} {name[:34]:<34} {'-':<8} {'-':>8} {'-':<4} {how}  {extra}")
            tally[(kind, "unresolved")] += 1
            continue
        ang = fp.angle_deg % 360.0
        side = _side(fp)
        print(f"{kind:<10} {name[:34]:<34} {fp.ref:<8} {ang:>8.3f} {side:<4} {how}  {extra}")
        zero = abs(ang) < 1e-6 or abs(ang - 360.0) < 1e-6
        tally[(kind, "0deg F" if zero and side == "F" else "NOT 0deg F")] += 1
    print("-" * 120)
    for (kind, bucket), n in sorted(tally.items()):
        print(f"{kind:<10} {bucket:<12} {n}")
    print("layer repr sample:", next((repr(r[3].layer) for r in rows if r[3] is not None), "-"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: claude_probe_r70_copper_frames_2026_10_04.py <config.sexp of a COPY>")
    main(sys.argv[1])
