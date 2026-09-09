#!/usr/bin/env python3
"""
probe_board_overlay.py — can KiCad itself draw (and un-draw) a cell's bbox and
an anchor marker for us, instead of us building a Qt canvas?

Written 2026-09-09 for the cell-anchor design discussion; measured answers in
techdocs/handoff/claude/done_2026_09_09_cell_anchor_live_probes.md, consumed by
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md (Phase C's
gui/board_overlay.py is meant to be built on exactly these calls).

The cell's bbox frame is otherwise INVISIBLE: its corner is the corner of a
cloud of CENTRES (footprint and via centres — see template_selection._bbox_origin)
and therefore lies on no real object, so "type coordinates relative to it" is
blind work. Drawing it as real board graphics hands rendering, zooming, layer
colours and visibility to KiCad.

What it established (KiCad 10.0.6-rc2):

  - create_items([BoardRectangle]) / ([BoardCircle]) works and returns the
    object with a uuid; remove_by_ids([uuid]) deletes it; NO new domain DTO is
    needed (unwrap passes raw kipy objects through and board_item_from_kipy
    returns unknown types unchanged); no commit is required.
  - A shape the user has DRAGGED reads back by uuid at its new position — so a
    draggable marker is a usable "pick an arbitrary point" mechanism, with all
    of KiCad's snapping, and without hijacking the grid/drill origin.
  - Sweeping a whole layer works, which makes cleanup safe when the overlay
    lives on its own user layer.

Three gotchas, each of which cost real time:

  1. A default-constructed shape has stroke width 0 and KiCad renders NOTHING —
     the object is created and sits in get_shapes(), it is simply invisible.
     Always set attributes.stroke.width.
  2. KiCad does NOT repaint the canvas after an IPC edit. select_items() forces
     the redraw; without it the user sees "nothing happened".
  3. BoardCircle.radius is a METHOD, not a property, and is set via
     radius_point. Board.get_items_by_id() wants KIID messages, not strings —
     filtering get_shapes() by uuid is simpler.
  4. Per-shape colour is IGNORED: attributes.stroke.color accepts r/g/b/a but
     KiCad returns the shape without it. Graphics take their LAYER's colour, so
     the user-facing knob is "which layer", not a colour picker.

WRITES TO THE BOARD (graphics only, on the chosen user layer).

    python -m kicadstamp.diagnostics.probe_board_overlay --layers
    python -m kicadstamp.diagnostics.probe_board_overlay --around 180.1625 133.4975
    python -m kicadstamp.diagnostics.probe_board_overlay --around 180 133 --keep
    python -m kicadstamp.diagnostics.probe_board_overlay --sweep
"""
import argparse
import sys
import time

from kipy.board_types import BoardCircle, BoardLayer, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.utils.units import MM

_LAYER_NAMES = {value: name for name, value in BoardLayer.items()}


def user_layers(adapter):
    """(layer, KiCad's display name) for every enabled user layer.

    The set is NOT fixed: KiCad 10 allows BL_User_1..BL_User_45 and they can be
    renamed (Denis added 'User.KiCadStamp' as BL_User_5), so any UI listing
    them must read the board rather than hardcode a list.
    """
    out = []
    for layer in adapter._board.get_enabled_layers():
        name = _LAYER_NAMES.get(layer, str(layer))
        if name.startswith("BL_User_") or name in (
                "BL_Dwgs_User", "BL_Cmts_User", "BL_Eco1_User", "BL_Eco2_User"):
            out.append((layer, adapter._board.get_layer_name(layer)))
    return out


def resolve_layer(adapter, wanted):
    """Layer by KiCad display name ('User.KiCadStamp') or enum name."""
    for layer, display in user_layers(adapter):
        if display == wanted or _LAYER_NAMES.get(layer) == wanted:
            return layer
    return None


def make_rectangle(x1_mm, y1_mm, x2_mm, y2_mm, layer, stroke_mm):
    rect = BoardRectangle()
    rect.top_left = KipyVector2.from_xy(int(x1_mm * MM), int(y1_mm * MM))
    rect.bottom_right = KipyVector2.from_xy(int(x2_mm * MM), int(y2_mm * MM))
    rect.layer = layer
    rect.attributes.stroke.width = int(stroke_mm * MM)   # gotcha 1
    return rect


def make_marker(x_mm, y_mm, radius_mm, layer, stroke_mm):
    circle = BoardCircle()
    circle.center = KipyVector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    # gotcha 3: the radius is expressed as a POINT on the circle, not a number.
    circle.radius_point = KipyVector2.from_xy(int((x_mm + radius_mm) * MM),
                                              int(y_mm * MM))
    circle.layer = layer
    circle.attributes.stroke.width = int(stroke_mm * MM)
    return circle


def repaint(adapter, shapes):
    """Force the PCB editor to redraw — see gotcha 2."""
    adapter.select_items(shapes)


def shapes_on(adapter, layer):
    adapter.refresh_board()
    return [s for s in adapter._board.get_shapes() if s.layer == layer]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", default="User.Drawings",
                        help="KiCad display name of the layer to draw on")
    parser.add_argument("--layers", action="store_true",
                        help="just list the enabled user layers and exit")
    parser.add_argument("--sweep", action="store_true",
                        help="delete every graphic shape on the layer and exit")
    parser.add_argument("--around", nargs=2, type=float, metavar=("X_MM", "Y_MM"),
                        help="draw a bbox and a marker around this point")
    parser.add_argument("--half-mm", type=float, default=4.5,
                        help="half-size of the bbox rectangle")
    parser.add_argument("--radius-mm", type=float, default=0.3)
    parser.add_argument("--stroke-mm", type=float, default=0.15)
    parser.add_argument("--keep", action="store_true",
                        help="leave the overlay on the board")
    parser.add_argument("--pause-s", type=float, default=8.0)
    args = parser.parse_args()

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    if args.layers:
        print("enabled user layers (live from the board):")
        for layer, display in user_layers(adapter):
            print(f"  {_LAYER_NAMES.get(layer, layer):16} -> {display!r}")
        # Origins read fine over IPC too, but were rejected as a point-picking
        # mechanism: the grid origin is the user's own working tool.
        for kind in ("grid", "drill"):
            v = adapter.get_board_origin(kind)
            print(f"  {kind} origin: ({v.x/MM:.4f}, {v.y/MM:.4f}) mm")
        return 0

    layer = resolve_layer(adapter, args.layer)
    if layer is None:
        print(f"layer {args.layer!r} is not enabled on this board; try --layers")
        return 1
    print(f"layer: {adapter._board.get_layer_name(layer)!r}")

    if args.sweep:
        ours = shapes_on(adapter, layer)
        print(f"shapes on this layer: {len(ours)}")
        if not ours:
            return 0
        ok = adapter.remove_by_ids([str(s.id.value) for s in ours])
        print(f"sweep -> {ok}; left: {len(shapes_on(adapter, layer))}")
        return 0 if ok else 1

    if args.around is None:
        parser.error("give --around X_MM Y_MM (or --layers / --sweep)")
    cx, cy = args.around
    h = args.half_mm

    items = [
        make_rectangle(cx - h, cy - h, cx + h, cy + h, layer, args.stroke_mm),
        make_marker(cx, cy, args.radius_mm, layer, args.stroke_mm),
    ]
    created = adapter.create_items(items)
    uuids = [str(c.id.value) for c in created]
    print(f"create_items() -> {len(created)}: {uuids}")

    mine = [s for s in shapes_on(adapter, layer) if str(s.id.value) in uuids]
    repaint(adapter, mine)
    print(f"drawn on: {[adapter._board.get_layer_name(s.layer) for s in mine]}")

    if args.keep:
        print("--keep: left on the board. Drag the marker, then re-run with "
              "--read to see the new position, or --sweep to clean up.")
        return 0

    print(f"\nLook at the board now ({args.pause_s}s) — is the overlay visible?")
    time.sleep(args.pause_s)

    # Read the marker back: this is the call a "pick an arbitrary point" flow
    # makes after the user has dragged it with KiCad's own tools.
    for s in shapes_on(adapter, layer):
        if isinstance(s, BoardCircle) and str(s.id.value) in uuids:
            print(f"marker now at ({s.center.x/MM:.4f}, {s.center.y/MM:.4f}) mm "
                  f"r={s.radius()/MM:.3f} mm")   # gotcha 3: radius is a method

    ok = adapter.remove_by_ids(uuids)
    print(f"remove_by_ids({len(uuids)}) -> {ok}; "
          f"left on layer: {len(shapes_on(adapter, layer))}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
