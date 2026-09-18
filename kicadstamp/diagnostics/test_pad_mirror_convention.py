#!/usr/bin/env python3
"""
test_pad_mirror_convention.py — the only empirical test capable of finally
confirming or refuting the assumption in pad_projection.predict_pad_position()
about mirroring the local pad offset along X when flipping to the other side
of the board.

Uses the KiCadStamp adapter and pad_projection geometry.

Run:
    python -m kicadstamp.diagnostics.test_pad_mirror_convention C6 --pad 2
"""

import argparse
import sys
import time

from kipy.board_types import BoardLayer, Pad
from kipy.geometry import Vector2, Angle

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.geometry.pad_projection import local_pad_offset, predict_pad_position
from kicadstamp.utils.units import MM
from kicadstamp.i18n import _

MM = 1_000_000


def step(label, func, *args, **kwargs):
    print(_("[...] {label}").format(label=label), flush=True)
    t0 = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(_("[OK]  {label} — {elapsed} ms").format(label=label, elapsed=elapsed), flush=True)
        return result
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(_("[ERR] {label} — {elapsed} ms — {type}: {e}")
              .format(label=label, elapsed=elapsed, type=type(e).__name__, e=e), flush=True)
        raise


def find_fp(adapter, ref):
    return adapter.get_footprint(ref)


def find_pad(adapter, fp, pad_number):
    return next((p for p in adapter.get_footprint_pads(fp) if p.number == pad_number), None)


def rotate_component(adapter, ref, delta_deg):
    """Rotates the component by delta_deg relative to its current angle (no flip)."""
    fp = find_fp(adapter, ref)
    if fp is None:
        raise ValueError(_("Component {ref} not found").format(ref=ref))
    commit = adapter.begin_commit()
    try:
        new_angle = Angle.from_degrees(fp.orientation.degrees + delta_deg)
        fp.orientation = new_angle
        adapter.update_items([fp])
        adapter.push_commit(commit, _("test_pad_mirror_convention: rotate {ref} by {delta:+.1f}°")
                            .format(ref=ref, delta=delta_deg))
    except Exception:
        adapter.drop_commit(commit)
        raise


def flip_component(adapter, ref):
    fp = find_fp(adapter, ref)
    if fp is None:
        raise ValueError(_("Component {ref} not found").format(ref=ref))
    adapter.flip_selected([fp])


def main():
    ap = argparse.ArgumentParser(
        description=_("Test pad mirroring convention when flipping a component")
    )
    ap.add_argument("ref", help=_("refdes of the component to test, not a critical one (e.g. C6)"))
    ap.add_argument("--pad", default="2",
                    help=_("pad number to track (default GND, usually '2')"))
    ap.add_argument("--timeout-ms", type=int, default=30000,
                    help=_("IPC timeout in ms"))
    args = ap.parse_args()

    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = step(_("create_board_adapter(...)"),
             lambda *, timeout_ms=None: create_board_adapter(
                 timeout_ms=timeout_ms, use_store=False), timeout_ms=args.timeout_ms)
    step(_("adapter.refresh_board()"), adapter.refresh_board)

    fp0 = find_fp(adapter, args.ref)
    if fp0 is None:
        sys.exit(_("[error] {ref} not found on the board").format(ref=args.ref))
    pad0 = find_pad(adapter, fp0, args.pad)
    if pad0 is None:
        sys.exit(_("[error] {ref} has no pad {pad}").format(ref=args.ref, pad=args.pad))

    orig_pos = fp0.position
    orig_angle = fp0.orientation.degrees
    orig_layer = fp0.layer
    local_offset = local_pad_offset(fp0, pad0)

    print(_("\n=== Initial state of {ref} ===").format(ref=args.ref))
    print(_("position=({x:.3f},{y:.3f}) mm angle={angle:.1f}° layer={layer}")
          .format(x=orig_pos.x/1e6, y=orig_pos.y/1e6, angle=orig_angle,
                  layer='F.Cu' if orig_layer==BoardLayer.BL_F_Cu else 'B.Cu'))
    print(_("local offset of pad {pad}: ({x:.3f}, {y:.3f}) mm\n")
          .format(pad=args.pad, x=local_offset.x/1e6, y=local_offset.y/1e6))

    # --- Step 1: rotate by 90°, NO flip ---
    print(_("=== Step 1: rotate by +90°, NO flip (check base formula) ==="))
    rotate_component(adapter, args.ref, 90.0)
    adapter.refresh_board()
    fp1 = find_fp(adapter, args.ref)
    pad1 = find_pad(adapter, fp1, args.pad)

    origin = Vector2.from_xy(0, 0)
    predicted_1 = fp1.position + local_offset.rotate(Angle.from_degrees(fp1.orientation.degrees), origin)
    real_1 = pad1.position
    dist_1_mm = ((predicted_1.x - real_1.x)**2 + (predicted_1.y - real_1.y)**2)**0.5 / 1e6
    print(_("Predicted: ({x:.3f}, {y:.3f}) mm").format(x=predicted_1.x/1e6, y=predicted_1.y/1e6))
    print(_("Real:      ({x:.3f}, {y:.3f}) mm").format(x=real_1.x/1e6, y=real_1.y/1e6))
    if dist_1_mm < 0.01:
        print(_("Difference: {d:.4f} mm -- OK, base formula works").format(d=dist_1_mm))
    else:
        print(_("Difference: {d:.4f} mm !! BASE FORMULA FAILS, further flip test is meaningless").format(d=dist_1_mm))
    print()

    # --- Step 2: flip to the other side ---
    print(_("=== Step 2: flip to the other side (test mirroring assumption) ==="))
    flip_component(adapter, args.ref)
    adapter.refresh_board()
    fp2 = find_fp(adapter, args.ref)
    pad2 = find_pad(adapter, fp2, args.pad)

    real_2 = pad2.position
    final_angle = fp2.orientation.degrees

    candidate_x_mirror = fp2.position + Vector2.from_xy(-local_offset.x, local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_y_mirror = fp2.position + Vector2.from_xy(local_offset.x, -local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_no_mirror = fp2.position + local_offset.rotate(Angle.from_degrees(final_angle), origin)

    dist_x = ((candidate_x_mirror.x - real_2.x)**2 + (candidate_x_mirror.y - real_2.y)**2)**0.5 / 1e6
    dist_y = ((candidate_y_mirror.x - real_2.x)**2 + (candidate_y_mirror.y - real_2.y)**2)**0.5 / 1e6
    dist_none = ((candidate_no_mirror.x - real_2.x)**2 + (candidate_no_mirror.y - real_2.y)**2)**0.5 / 1e6

    print(_("Real pad position after flip: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
          .format(x=real_2.x/1e6, y=real_2.y/1e6, angle=final_angle))
    print(_("Candidate 'mirror X' (current code): deviation {d:.4f} mm").format(d=dist_x))
    print(_("Candidate 'mirror Y':                 deviation {d:.4f} mm").format(d=dist_y))
    print(_("Candidate 'no mirror':                deviation {d:.4f} mm").format(d=dist_none))

    results = [(_("mirror X (current code)"), dist_x),
               (_("mirror Y"), dist_y),
               (_("no mirror"), dist_none)]
    winner = min(results, key=lambda r: r[1])
    print(_("\n>>> WINNER: {name} (deviation {d:.4f} mm)").format(name=winner[0], d=winner[1]))
    if winner[0].startswith(_("mirror X")):
        print(_(">>> The current code in pad_projection.py is ALREADY CORRECT, no changes needed."))
    else:
        print(_(">>> The code in pad_projection.py needs fixing: currently mirrors X, "
                "but should do {winner}.").format(winner=winner[0]))

    # --- Restore original state ---
    print(_("\n=== Restoring original state ==="))
    flip_component(adapter, args.ref)
    adapter.refresh_board()
    rotate_component(adapter, args.ref, -90.0)
    adapter.refresh_board()
    fp_final = find_fp(adapter, args.ref)
    print(_("Final state: angle={angle:.1f}° (was {orig:.1f}°), layer={layer} (was {orig_layer})")
          .format(angle=fp_final.orientation.degrees, orig=orig_angle,
                  layer='F.Cu' if fp_final.layer==BoardLayer.BL_F_Cu else 'B.Cu',
                  orig_layer='F.Cu' if orig_layer==BoardLayer.BL_F_Cu else 'B.Cu'))


if __name__ == "__main__":
    main()