#!/usr/bin/env python3
"""
test_create_one_via.py — minimal diagnostic test for create_items() (KiCadStamp).

Purpose: test CREATING a new object (Via) via IPC.
Places one via on GND next to the specified capacitor (offset-mm from the
capacitor centre outward).

Uses the KiCadStamp adapter.

Run:
    python -m kicadstamp.diagnostics.test_create_one_via C5 --offset-mm 1.2
    python -m kicadstamp.diagnostics.test_create_one_via --remove   # delete the last created via
"""

import argparse
import sys
import json
import time
from pathlib import Path

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.utils.units import MM
from kipy.geometry import Vector2
from kicadstamp.i18n import _

STATE_FILE = Path(__file__).parent / ".last_test_via.json"


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
        description=_("Place a via next to a component or delete the last created via")
    )
    ap.add_argument("ref", nargs="?", help=_("refdes of the capacitor to place the via next to"))
    ap.add_argument("--offset-mm", type=float, default=1.2,
                    help=_("offset of the via from the capacitor centre, in mm"))
    ap.add_argument("--net", default="GND", help=_("net name"))
    ap.add_argument("--drill-mm", type=float, default=0.3, help=_("drill diameter in mm"))
    ap.add_argument("--diameter-mm", type=float, default=0.6, help=_("via diameter in mm"))
    ap.add_argument("--timeout-ms", type=int, default=30000,
                    help=_("IPC timeout in ms"))
    ap.add_argument("--remove", nargs="?", const="__last__", default=None, metavar="UUID",
                    help=_("delete a via instead of creating one. Without a value, takes the id "
                           "of the last via created by this script from .last_test_via.json"))
    args = ap.parse_args()

    adapter = step(_("KiCadBoardAdapter(...)"), KiCadBoardAdapter, timeout_ms=args.timeout_ms)
    step(_("adapter.refresh_board()"), adapter.refresh_board)

    if args.remove:
        remove_id = args.remove
        if remove_id == "__last__":
            if not STATE_FILE.exists():
                sys.exit(_("[error] no saved id in {file} — pass --remove <uuid> explicitly")
                         .format(file=STATE_FILE))
            remove_id = json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]
            print(_("Taking id from {file}: {id}\n").format(file=STATE_FILE.name, id=remove_id))

        # Use the adapter to delete by UUID
        commit = step(_("adapter.begin_commit()"), adapter.begin_commit)
        try:
            step(_("adapter.remove_by_id(remove_id)"), adapter.remove_by_id, remove_id)
            step(_("adapter.push_commit(commit, ...)"), adapter.push_commit, commit,
                 "test_create_one_via: remove")
            print(_("\nVia {id} deleted.").format(id=remove_id))
            if STATE_FILE.exists() and remove_id == json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]:
                STATE_FILE.unlink()
        except Exception:
            step(_("adapter.drop_commit(commit)"), adapter.drop_commit, commit)
            raise
        return

    if not args.ref:
        sys.exit(_("specify a capacitor refdes (or --remove <uuid> to delete)"))

    fp = step(_("adapter.get_footprint({ref!r})").format(ref=args.ref), adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(_("[error] {ref} not found on the board").format(ref=args.ref))

    net = step(_("adapter.get_net_by_name({net!r})").format(net=args.net), adapter.get_net_by_name, args.net)
    if net is None:
        sys.exit(_("[error] net {net!r} not found on the board").format(net=args.net))

    pos = fp.position
    via_pos = Vector2.from_xy(int(pos.x + args.offset_mm * MM), int(pos.y))
    print(_("\n{ref} at ({x:.3f}, {y:.3f}) mm, via will be at ({vx:.3f}, {vy:.3f}) mm, net={net}\n")
          .format(ref=args.ref, x=pos.x/MM, y=pos.y/MM,
                  vx=via_pos.x/MM, vy=via_pos.y/MM, net=args.net))

    via = adapter.create_via(via_pos, net, args.drill_mm, args.diameter_mm)

    commit = step(_("adapter.begin_commit()"), adapter.begin_commit)
    try:
        created = step(_("adapter.create_items([via])"), adapter.create_items, [via])
        step(_("adapter.push_commit(commit, ...)"), adapter.push_commit, commit,
             f"test_create_one_via: near {args.ref}")
        created_id = created[0].uuid if created else None
        print(_("\nDone. Via created, id={id}").format(id=created_id))
        if created_id:
            STATE_FILE.write_text(json.dumps({"id": created_id, "ref": args.ref}), encoding="utf-8")
            print(_("id saved to {file} — to delete it, just run:\n"
                    "  python -m kicadstamp.diagnostics.test_create_one_via --remove")
                  .format(file=STATE_FILE.name))
    except Exception:
        step(_("adapter.drop_commit(commit)"), adapter.drop_commit, commit)
        raise


if __name__ == "__main__":
    main()