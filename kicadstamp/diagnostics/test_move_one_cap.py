#!/usr/bin/env python3
"""
test_move_one_cap.py — minimal diagnostic test for IPC writes (KiCadStamp).

Purpose: isolate begin_commit() hanging to the limit — take ONE capacitor,
shift it by 1mm along X, commit. If this also hangs, the problem is not in
batch size/commit but in something more fundamental (stuck transaction from a
previous run, broken KiCad session state, etc.) — then a full KiCad restart is
definitely needed.

Uses the KiCadStamp adapter to interact with the board.

Run:
    python -m kicadstamp.diagnostics.test_move_one_cap C5 --delta-mm 1.0
    python -m kicadstamp.diagnostics.test_move_one_cap C5 --revert
"""

import argparse
import sys
import time

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.utils.units import MM
from kipy.geometry import Vector2
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


def main():
    ap = argparse.ArgumentParser(
        description=_("Test moving a component by a given delta along X")
    )
    ap.add_argument("ref", help=_("refdes of the capacitor to test, e.g. C5"))
    ap.add_argument("--delta-mm", type=float, default=1.0,
                    help=_("how many mm to shift along X"))
    ap.add_argument("--revert", action="store_true",
                    help=_("shift in the opposite direction (revert)"))
    ap.add_argument("--timeout-ms", type=int, default=30000,
                    help=_("IPC timeout in ms"))
    args = ap.parse_args()

    delta = -args.delta_mm if args.revert else args.delta_mm

    print(_("=== Test: move {ref} by {delta:+.2f} mm along X, timeout={timeout} ms ===\n")
          .format(ref=args.ref, delta=delta, timeout=args.timeout_ms))

    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = step(_("create_board_adapter(...)"),
             lambda *, timeout_ms=None: create_board_adapter(
                 timeout_ms=timeout_ms, use_store=False), timeout_ms=args.timeout_ms)
    step(_("adapter.refresh_board()"), adapter.refresh_board)

    fp = step(_("adapter.get_footprint({ref!r})").format(ref=args.ref), adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(_("[error] {ref} not found on the board").format(ref=args.ref))

    old_pos = fp.position
    new_pos = Vector2.from_xy(int(old_pos.x + delta * MM), int(old_pos.y))
    print(_("\nCurrent position of {ref}: ({x:.3f}, {y:.3f}) mm")
          .format(ref=args.ref, x=old_pos.x/MM, y=old_pos.y/MM))
    print(_("New position:            ({x:.3f}, {y:.3f}) mm\n")
          .format(x=new_pos.x/MM, y=new_pos.y/MM))

    commit = step(_("adapter.begin_commit()"), adapter.begin_commit)

    try:
        fp.position = new_pos
        step(_("adapter.update_items([fp])"), adapter.update_items, [fp])
        step(_("adapter.push_commit(commit, ...)"), adapter.push_commit, commit,
             f"test_move_one_cap: {args.ref}")
        print(_("\nDone. {ref} moved by {delta:+.2f} mm along X.")
              .format(ref=args.ref, delta=delta))
        print(_("To revert: python -m kicadstamp.diagnostics.test_move_one_cap "
                "{ref} --delta-mm {d} --revert")
              .format(ref=args.ref, d=args.delta_mm))
    except Exception:
        step(_("adapter.drop_commit(commit) (rollback after error)"), adapter.drop_commit, commit)
        raise


if __name__ == "__main__":
    main()