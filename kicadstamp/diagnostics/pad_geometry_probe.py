"""pad_geometry_probe.py — where KiCad says a pad is vs where its copper actually is.

Input: a root config plus EITHER `--thermal NAME` (one thermal_via_arrays entry)
OR `--ref REF` (any footprint). Reads only: nothing is written to the board, the
config or the registry — no planner commit, no Apply, no Redraw.

Expected: two sections and one result line.

  A  every pad of the footprint: its number, the shape reported by the padstack,
     the body angle and the pad's OWN angle, the centre of KiCad's bounding box
     MINUS the centre of the pad's own area (dx, dy, |d| mm) and the size of both.
     A pad whose own area cannot be built (a `custom`/`unknown` shape, no size)
     shows "no own area": its consumers fall back to KiCad's box, which is the one
     case this probe exists to expose, and the WARNING at the end of A counts them.

     Background (measured 15.09.2026, KiCad 10.0.6 + kipy 10.0.1, IC2 = AD9707 of
     profiles/3ch-awg-tia-v103): with the footprint rotated to 315°
     get_item_bounding_box comes back shifted by the same (−0.899, −1.470) mm =
     1.724 mm for EVERY pad of that footprint, while pad.position,
     padstack.copper_layers[0].size and padstack.angle stay correct; at
     0/90/180/270° the shift is 0.000. The box is also the wrong SHAPE for a
     rotated pad even when it is not shifted: it is an axis-aligned box around a
     rotated rectangle, so the 0.300x0.850 mm signal pad reads 0.813x0.813 there
     while its own area stays 0.300x0.850.

  B  (`--thermal` only) the ideal thermal-via grid of that entry's pad, point by
     point: the index, the point in mm, whether the point is BLOCKED under the old
     rule (KiCad's boxes as axis-aligned Rects, recomputed here exactly as the
     shipping code did before E2) and under the current one
     (ViaPlanner._build_keepout — the pads' own areas), and then the real
     ViaPlanner.plan_vias for this ONE entry (planned_components=[], planning
     only): the distance from the ideal point to the via that was placed for it and
     whether that via lies on the thermal pad's own area.

Result line: `old: X of N blocked; new: Y of N blocked; placed off pad: Z` — where
X/N are the blocked ideal points under each rule, and Z counts the planned thermal
vias that are NOT on the thermal pad's own area. At 0° the two rules must agree
(X == Y) and Z must be 0; under a rotation that is not a multiple of 90° the old
column is the one that goes wrong.

Live KiCad: Yes. Reads only — no executor, no registry, no write to the board or
the config; plan_vias is called for its PLANNING result, nothing is committed.

Run: python -m kicadstamp.diagnostics.pad_geometry_probe <config.sexp> --thermal NAME
     python -m kicadstamp.diagnostics.pad_geometry_probe <config.sexp> --ref REF
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace

from kicadstamp.config import load_config
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.geometry.keepout import build_keepout, point_is_clear
from kicadstamp.geometry.pad_area import pad_area_of
from kicadstamp.geometry.thermal_grid import compute_thermal_via_grid
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.utils.units import MM

# The Д1 shift measured on 15.09.2026 — printed as the reference "expected" value
# for a footprint at 315°; the probe does not apply it anywhere.
MEASURED_SHIFT_MM = 1.724


def _fmt_pair(x_mm: float, y_mm: float) -> str:
    return f"({x_mm:8.3f}, {y_mm:8.3f})"


def _own_area_note(pad) -> str:
    area = pad_area_of(pad)
    if area is None:
        return "no own area"
    return f"{2 * area.half_w / MM:.3f}x{2 * area.half_h / MM:.3f}"


def section_a(adapter, footprint) -> int:
    """Part A. Returns the number of pads without an area of their own."""
    print(f"=== A. pads of {footprint.ref} (body angle {footprint.angle_deg:.2f}°) ===")
    print("  pad  shape      pad_angle   kicad box      own area       "
          "box centre - own centre        d(mm)")
    pads = adapter.get_footprint_pads(footprint)
    boxes = list(adapter.get_bounding_boxes(pads))
    if len(boxes) < len(pads):
        boxes += [None] * (len(pads) - len(boxes))
    no_own_area = 0
    worst = 0.0
    for pad, box in zip(pads, boxes):
        area = pad_area_of(pad)
        if area is None:
            no_own_area += 1
        pad_deg = math.degrees(pad.angle_rad)
        box_size = (f"{box.size.x / MM:.3f}x{box.size.y / MM:.3f}"
                    if box is not None else "n/a")
        if box is None or area is None:
            print(f"  {str(pad.number):4} {str(pad.shape):10} {pad_deg:9.2f}  "
                  f"{box_size:14} {_own_area_note(pad):14} "
                  f"{'-- box or own area missing --':32} {'n/a':>8}")
            continue
        dx_mm = (box.pos.x + box.size.x / 2.0 - area.center.x) / MM
        dy_mm = (box.pos.y + box.size.y / 2.0 - area.center.y) / MM
        dist = (dx_mm * dx_mm + dy_mm * dy_mm) ** 0.5
        worst = max(worst, dist)
        print(f"  {str(pad.number):4} {str(pad.shape):10} {pad_deg:9.2f}  "
              f"{box_size:14} {_own_area_note(pad):14} "
              f"{_fmt_pair(dx_mm, dy_mm):30} {dist:8.3f}")
    print(f"  worst |shift| over {len(pads)} pad(s): {worst:.3f} mm "
          f"(measured reference at 315°: {MEASURED_SHIFT_MM:.3f} mm; at 0/90/180/270° expect 0.000)")
    if no_own_area:
        print(f"  WARNING: {no_own_area} pad(s) have NO area of their own (shape "
              f"custom/unknown or no size) — their consumers fall back to KiCad's box, "
              f"which is the case the fix cannot cover. Told apart by 'shape' above.")
    else:
        print("  every pad has an area of its own — no fallback to KiCad's box.")
    return no_own_area


def section_b(adapter, planner: ViaPlanner, cfg, tva) -> None:
    """Part B for ONE thermal_via_arrays entry. Planning only — nothing commits."""
    target_fp = planner._resolve_thermal_anchor(tva)
    if target_fp is None:
        print(f"=== B. thermal_via_arrays {tva.name!r}: retired — nothing to measure ===")
        return
    pad = adapter.get_pad_by_number(target_fp, tva.pad)
    if pad is None:
        print(f"=== B. thermal_via_arrays {tva.name!r}: {target_fp.ref} has no pad {tva.pad!r} ===")
        return

    points = compute_thermal_via_grid(pad, rows=tva.rows, cols=tva.cols,
                                      margin_mm=tva.margin_mm,
                                      stagger=(tva.pattern == "staggered"))
    radius = tva.diameter_mm / 2.0 * MM
    clearance = cfg.via_keepout_clearance_mm
    exclude = {(target_fp.ref, tva.pad)}

    # The OLD rule, recomputed here exactly as the shipping code did it before Э2:
    # KiCad's box for every pad of the target footprint EXCEPT the thermal pad,
    # inflated by the clearance, plus an axis-aligned square per planned via (there
    # are none here: planned_components=[]).
    ring = [p for p in adapter.get_footprint_pads(target_fp)
            if str(p.number) != str(tva.pad)]
    old_keepout = build_keepout(adapter.get_bounding_boxes(ring), clearance,
                               mm_per_unit=MM)
    new_keepout = planner._build_keepout(target_fp, [], exclude=exclude,
                                         planned_vias=[])

    # The real planning path for this ONE entry, on a config narrowed in memory to
    # it (the profile on disk is never touched).
    one_entry_cfg = replace(cfg, thermal_via_arrays=[tva])
    planned = ViaPlanner(adapter, one_entry_cfg,
                         sheet_names=planner.sheet_names).plan_vias([], [])
    thermal = [v for v in planned if v.registry_key is not None]
    area = pad_area_of(pad)

    print(f"=== B. thermal_via_arrays {tva.name!r}: {target_fp.ref} pad {tva.pad}, "
          f"{tva.rows}x{tva.cols} {tva.pattern}, margin {tva.margin_mm} mm, "
          f"via {tva.diameter_mm}/{tva.drill_mm} mm, clearance {clearance} mm ===")
    print("  idx  ideal point (mm)      old   new   nearest planned via (mm)  "
          "dist(mm)  on pad")
    old_blocked = 0
    new_blocked = 0
    for index, point in enumerate(points):
        old = not point_is_clear(point, radius, old_keepout)
        new = not point_is_clear(point, radius, new_keepout)
        old_blocked += 1 if old else 0
        new_blocked += 1 if new else 0
        nearest = None
        nearest_dist = None
        for via in thermal:
            dx = (via.position.x - point.x) / MM
            dy = (via.position.y - point.y) / MM
            dist = (dx * dx + dy * dy) ** 0.5
            if nearest_dist is None or dist < nearest_dist:
                nearest, nearest_dist = via, dist
        via_str = (_fmt_pair(nearest.position.x / MM, nearest.position.y / MM)
                   if nearest is not None else "     -- none --")
        on_pad = ("yes" if nearest is not None and area is not None
                  and area.contains(nearest.position) else
                  "n/a" if nearest is None else "NO")
        print(f"  {index:3}  {_fmt_pair(point.x / MM, point.y / MM)}  "
              f"{'BLOCK' if old else 'free ':5} {'BLOCK' if new else 'free ':5} "
              f"{via_str:26} "
              f"{(f'{nearest_dist:.3f}' if nearest_dist is not None else 'n/a'):>8}  {on_pad}")

    off_pad = [v for v in thermal
               if area is None or not area.contains(v.position)]
    print(f"  obstacles: old rule {len(old_keepout)} rect(s) from KiCad boxes; "
          f"new rule {len(new_keepout)} obstacle(s) from the pads' own areas")
    print(f"  plan_vias (planning only, planned_components=[]): "
          f"{len(thermal)} thermal via(s)")
    print(f"RESULT: old: {old_blocked} of {len(points)} blocked; "
          f"new: {new_blocked} of {len(points)} blocked; "
          f"placed off pad: {len(off_pad)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Where KiCad says a pad is vs where its copper is (read-only)")
    parser.add_argument("config", help="root config (profiles/<name>/config.sexp)")
    parser.add_argument("--thermal", metavar="NAME",
                        help="thermal_via_arrays entry to measure (section A + B)")
    parser.add_argument("--ref", metavar="REF",
                        help="footprint to measure (section A only)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MS,
                        help="IPC timeout in ms (shipped default)")
    args = parser.parse_args()

    if bool(args.thermal) == bool(args.ref):
        parser.error("give exactly one of --thermal NAME or --ref REF")

    cfg, ctx = load_config(args.config)
    sheet_names = ctx.sheet_names if ctx is not None else {}

    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = create_board_adapter(timeout_ms=args.timeout, use_store=False)
    adapter.refresh_board()
    planner = ViaPlanner(adapter, cfg, sheet_names=sheet_names)

    if args.ref:
        footprint = adapter.get_footprint(args.ref)
        if footprint is None:
            print(f"{args.ref}: no such footprint on the open board")
            return 1
        section_a(adapter, footprint)
        return 0

    tva = next((t for t in cfg.thermal_via_arrays if t.name == args.thermal), None)
    if tva is None:
        known = ", ".join(repr(t.name) for t in cfg.thermal_via_arrays) or "none"
        print(f"thermal_via_arrays entry {args.thermal!r} not found; known: {known}")
        return 1
    target_fp = planner._resolve_thermal_anchor(tva)
    if target_fp is None:
        print(f"thermal_via_arrays entry {args.thermal!r} is retired")
        return 1
    section_a(adapter, target_fp)
    print()
    section_b(adapter, planner, cfg, tva)
    return 0


if __name__ == "__main__":
    sys.exit(main())
