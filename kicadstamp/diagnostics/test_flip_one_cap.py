#!/usr/bin/env python3
"""
test_flip_one_cap.py — minimal diagnostic test for "true" flip (KiCadStamp).

Context: simply assigning footprint.layer = BoardLayer.BL_B_Cu only changes
the data field and does NOT mirror pads/silkscreen — visually the component
remains as if on the original side.

The real flip in KiCad is the GUI action pcbnew.InteractiveEdit.flip
(TOOL_ACTION PCB_ACTIONS::flip in KiCad source, shortcut F, "Flips selected
item(s) to opposite side of board"). Via IPC it is accessible as
kicad.run_action(...) — but like any GUI action, it works through the CURRENT
SELECTION, not by taking objects directly.

Uses the KiCadStamp adapter, which encapsulates the flip and re‑reading.

Run:
    python -m kicadstamp.diagnostics.test_flip_one_cap C6
"""

import argparse
import sys
import time

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.utils.units import MM
from kipy.board_types import BoardLayer
from kicadstamp.i18n import _


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


def describe(fp):
    layer_name = "F.Cu" if fp.layer == BoardLayer.BL_F_Cu else "B.Cu" if fp.layer == BoardLayer.BL_B_Cu else str(fp.layer)
    return _("layer={layer}, pos=({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°").format(
        layer=layer_name, x=fp.position.x/1e6, y=fp.position.y/1e6,
        angle=fp.orientation.degrees)


def main():
    ap = argparse.ArgumentParser(
        description=_("Test flipping a component to the opposite side")
    )
    ap.add_argument("ref", help=_("refdes of the capacitor to test, e.g. C6"))
    ap.add_argument("--timeout-ms", type=int, default=30000, help=_("IPC timeout in ms"))
    args = ap.parse_args()

    print(_("=== Test: flip component {ref}, timeout={timeout} ms ===\n")
          .format(ref=args.ref, timeout=args.timeout_ms))

    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = step(_("create_board_adapter(...)"),
             lambda *, timeout_ms=None: create_board_adapter(
                 timeout_ms=timeout_ms, use_store=False), timeout_ms=args.timeout_ms)
    step(_("adapter.refresh_board()"), adapter.refresh_board)

    fp = step(_("adapter.get_footprint({ref!r})").format(ref=args.ref), adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(_("[error] {ref} not found on the board").format(ref=args.ref))

    print(_("\nBefore flip: {desc}\n").format(desc=describe(fp)))

    # Use the adapter for flip (it does clear_selection, add_to_selection, run_action, clear_selection)
    step(_("adapter.flip_selected([fp])"), adapter.flip_selected, [fp])

    # After flip the local object is stale — re‑read the footprint
    step(_("adapter.refresh_board()"), adapter.refresh_board)
    fp_after = step(_("adapter.get_footprint({ref!r}) (after)").format(ref=args.ref),
                    adapter.get_footprint, args.ref)

    print(_("\nAfter flip: {desc}\n").format(desc=describe(fp_after) if fp_after else _('(not found?!)')))

    if fp_after and fp_after.layer == BoardLayer.BL_B_Cu:
        print(_("Looks like it worked — the layer really changed to B.Cu."))
    else:
        print(_("Layer did NOT change — the action did not behave as expected, "
                "need to investigate further."))


if __name__ == "__main__":
    main()