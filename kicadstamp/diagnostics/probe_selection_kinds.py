#!/usr/bin/env python3
"""
probe_selection_kinds.py — what does KiCad actually hand us for a selection,
and can a selected Pad be traced back to its footprint?

Written 2026-09-09 for the cell-anchor design discussion; the measured answers
are recorded in techdocs/handoff/claude/done_2026_09_09_cell_anchor_live_probes.md
and drive plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md.

What it answered (KiCad 10.0.6-rc2, 325-footprint board):

  1. A PAD *is* selectable over IPC as its own item (kipy.board_types.Pad with
     number/net/position/id) — so "click the pad in KiCad -> read Role + pad
     number" needs no coordinates at all and the cell anchor can stay fully
     declarative (anchor_role + anchor_pad).
  2. A Pad carries NO reference to its owning footprint. The owner is found by
     matching the pad uuid against every footprint's cached pads — 2 ms over
     325 footprints, because get_footprint_pads() does not go to the API
     (pads live in footprint.definition.items).
  3. board_item_from_kipy() does NOT map Pad, so adapter.get_selected_items()
     hands callers a raw kipy object with ref=None/net_name=None. Both anchor
     call sites filter the selection with `isinstance(i, Footprint)`, so a
     selected pad reads as "nothing selected" — the recurring complaint.

Read-only. Run with KiCad open and something selected:

    python -m kicadstamp.diagnostics.probe_selection_kinds
"""
import sys
import time

from kipy.board_types import Pad as KipyPad

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import board_item_from_kipy, unwrap
from kicadstamp.kicad.adapter import KiCadBoardAdapter


def describe(index, raw, dto):
    """One selected item: its raw kipy class, the DTO we map it to, identity."""
    print(f"  [{index}] raw kipy : {type(raw).__module__}.{type(raw).__name__}")
    print(f"       DTO      : {type(dto).__module__}.{type(dto).__name__}"
          + ("   (UNMAPPED — passed through unchanged)" if dto is raw else ""))
    for attr in ("ref", "number", "name", "net", "net_name", "uuid", "layer"):
        value = getattr(dto, attr, None)
        if value is not None:
            print(f"       {attr:9}: {value!r}")
    position = getattr(dto, "position", None)
    if position is not None:
        print(f"       position : ({position.x/1e6:.4f}, {position.y/1e6:.4f}) mm")


def find_pad_owner(adapter, pad_uuid):
    """The footprint owning this pad, found by pad uuid.

    A Pad hands us no parent reference, but get_footprint_pads() reads the
    already-cached footprint definition rather than the API, so a full scan is
    local work — measured at 2 ms over 325 footprints.
    """
    t0 = time.perf_counter()
    footprints = adapter.get_footprints()
    t1 = time.perf_counter()
    owner = None
    for fp in footprints:
        for item in unwrap(fp).definition.items:
            if isinstance(item, KipyPad) and str(item.id.value) == pad_uuid:
                owner = fp
                break
        if owner is not None:
            break
    t2 = time.perf_counter()
    print(f"\n  owner lookup over {len(footprints)} footprints: "
          f"fetch {1000*(t1-t0):.0f} ms, scan {1000*(t2-t1):.0f} ms")
    return owner


def main():
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    raw_selection = list(adapter._board.get_selection())
    print(f"raw get_selection() -> {len(raw_selection)} item(s)")
    for i, raw in enumerate(raw_selection):
        describe(i, raw, board_item_from_kipy(raw))

    # The adapter's own view — what every GUI caller actually sees (groups
    # expanded into their members).
    items = adapter.get_selected_items()
    print(f"\nadapter.get_selected_items() -> {len(items)} item(s)")
    for i, item in enumerate(items):
        print(f"  [{i}] {type(item).__name__}"
              f" ref={getattr(item, 'ref', None)!r}"
              f" net={getattr(item, 'net_name', None)!r}")

    # A selected pad is the interesting case: resolve its owner and read the
    # fields the cell anchor actually needs (Role, Cluster, pad number).
    for raw in raw_selection:
        if not isinstance(raw, KipyPad):
            continue
        owner = find_pad_owner(adapter, str(raw.id.value))
        if owner is None:
            print("  owner NOT FOUND by pad uuid")
            continue
        print(f"  owner ref : {owner.ref}")
        print(f"  Role      : {adapter.get_field_value(owner, ROLE_FIELD_NAME)}")
        print(f"  Cluster   : {adapter.get_field_value(owner, CLUSTER_FIELD_NAME)}")
        print(f"  pad number: {raw.number!r}")
        print(f"  layer     : {owner.layer}   angle: {owner.angle_deg}")

    if not raw_selection:
        print("\nNothing is selected — select something in the PCB editor first.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
