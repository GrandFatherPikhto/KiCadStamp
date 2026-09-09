# gui/board_overlay.py
"""Board overlay drawing for the cell-anchor editor — Phase C of
plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md.

Why this is a GUI module (not kicadstamp/): it is visualisation FOR an
editor, not part of the board model. It draws a cell's bbox rectangle and an
anchor marker as REAL KiCad board graphics (on a user layer) so KiCad's own
canvas owns rendering, zooming, layer colours, visibility and even the
"pick an arbitrary point" interaction (the user drags the marker with KiCad's
own tools; we read its new position back by uuid). No own Qt canvas exists.

This file is a direct port of the measured reference implementation
kicadstamp/diagnostics/probe_board_overlay.py — the calls were verified live
on KiCad 10.0.6-rc2 (see done_2026_09_09_cell_anchor_live_probes.md). The
three gotchas (§0.5 of the plan) are baked into the helpers:

  1. a shape without `attributes.stroke.width` has stroke width 0 and KiCad
     renders NOTHING (the object is created and sits in get_shapes(), it is
     simply invisible) — every builder always sets a non-zero stroke width;
  2. KiCad does NOT repaint the canvas after an IPC edit — every create and
     every delete is followed by adapter.select_items(...) which forces the
     redraw, otherwise the user sees "nothing happened";
  3. BoardCircle.radius is a METHOD, and is set via `radius_point` (a point
     ON the circle), not a numeric property.

Per-shape colour is deliberately NOT part of the API (§0.6): KiCad returns
shapes without a colour, graphics take their LAYER's colour, so the only
user-facing knob is "which layer" (see Phase D — settings). The overlay layer
defaults / stroke / marker geometry are module-level constants here; Phase D
will replace them with settings reads.

Raw kipy objects (BoardRectangle/BoardCircle/BoardLayer/Vector2) are confined
to this module. Every function takes a duck-typed `adapter` (the real
KiCadBoardAdapter in production, a fake in tests) exposing:
  - create_items(items) -> iterable of created objects (each with .id.value)
  - remove_by_ids(uuid_strs) -> bool
  - select_items(items) -> None (forces the KiCad repaint)
  - refresh_board() -> None (re-reads the live board)
  - `_board` with get_enabled_layers(), get_layer_name(layer), get_shapes()
No method is added to kicadstamp/kicad/interfaces.py — it is an ABC, and
every new abstract method would break the test doubles across the project.
"""
from typing import Any

from kipy.board_types import BoardCircle, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

from kicadstamp.utils.units import MM

# ── Phase-C default overlay geometry (Phase D moves these into settings). ──
# Default layer for overlay graphics — a plain KiCad name; the real layer is
# resolved from the LIVE board via resolve_overlay_layer() (never hardcoded
# as an enum value — the user-layer set is not fixed, §0.7).
OVERLAY_DEFAULT_LAYER = "User.Drawings"
# Bbox rectangle outline width, mm.
OVERLAY_BBOX_STROKE_MM = 0.15
# Marker circle radius, mm (Denis called the earlier trial 1.5 mm "здоровенный").
OVERLAY_MARKER_RADIUS_MM = 0.3
# Marker circle outline width, mm.
OVERLAY_MARKER_STROKE_MM = 0.1


def _board(adapter):
    """The adapter's live-board handle the shape/layer reads go through."""
    return adapter._board


def overlay_layers(adapter) -> list[tuple[Any, str]]:
    """[(layer, KiCad's display name)] for every ENABLED layer of the live
    board, read from the board itself.

    The set is NOT fixed: KiCad 10 allows BL_User_1..BL_User_45 and user
    layers can be renamed (Denis added 'User.KiCadStamp' as BL_User_5), so
    any UI listing them must read the board rather than hardcode a list
    (§0.7). The display name (get_layer_name) is what the user sees and what
    resolve_overlay_layer() matches against.
    """
    return [(layer, _board(adapter).get_layer_name(layer))
            for layer in _board(adapter).get_enabled_layers()]


def resolve_overlay_layer(adapter, wanted: str):
    """The layer enum for a KiCad display name like 'User.Drawings' or
    'User.KiCadStamp', read LIVE from the board — None when no enabled layer
    has that display name."""
    for layer, display in overlay_layers(adapter):
        if display == wanted:
            return layer
    return None


def _make_rectangle(x1_mm, y1_mm, x2_mm, y2_mm, layer, stroke_mm) -> BoardRectangle:
    rect = BoardRectangle()
    rect.top_left = KipyVector2.from_xy(int(x1_mm * MM), int(y1_mm * MM))
    rect.bottom_right = KipyVector2.from_xy(int(x2_mm * MM), int(y2_mm * MM))
    rect.layer = layer
    # gotcha 1: without a non-zero stroke width KiCad renders nothing.
    rect.attributes.stroke.width = int(stroke_mm * MM)
    return rect


def _make_marker(x_mm, y_mm, radius_mm, layer, stroke_mm) -> BoardCircle:
    circle = BoardCircle()
    circle.center = KipyVector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    # gotcha 3: the radius is expressed as a POINT on the circle, not a number.
    circle.radius_point = KipyVector2.from_xy(int((x_mm + radius_mm) * MM),
                                              int(y_mm * MM))
    circle.layer = layer
    circle.attributes.stroke.width = int(stroke_mm * MM)
    return circle


def _create_and_repaint(adapter, items) -> list:
    """create_items() then force the KiCad redraw by selecting what we just
    drew (gotcha 2) — returns the created objects (each carries .id.value)."""
    created = list(adapter.create_items(items))
    adapter.select_items(created)
    return created


def _uuids(objects) -> list[str]:
    return [str(o.id.value) for o in objects]


def draw_bbox(adapter, layer, x1_mm, y1_mm, x2_mm, y2_mm,
              stroke_mm: float = OVERLAY_BBOX_STROKE_MM) -> str:
    """Draw the cell's bbox as a board rectangle on `layer`; returns the
    created shape's uuid (for later remove_overlay). Coordinates are world
    mm (the caller already mapped the cell's local bbox into the board
    frame)."""
    created = _create_and_repaint(
        adapter, [_make_rectangle(x1_mm, y1_mm, x2_mm, y2_mm, layer, stroke_mm)])
    return _uuids(created)[0]


def draw_marker(adapter, layer, x_mm, y_mm,
                radius_mm: float = OVERLAY_MARKER_RADIUS_MM,
                stroke_mm: float = OVERLAY_MARKER_STROKE_MM) -> str:
    """Draw the anchor marker circle at (x_mm, y_mm) on `layer`; returns the
    created circle's uuid. The user may then drag it with KiCad's own tools;
    read_marker() reads the new position back."""
    created = _create_and_repaint(
        adapter, [_make_marker(x_mm, y_mm, radius_mm, layer, stroke_mm)])
    return _uuids(created)[0]


def read_marker(adapter, uuid: str) -> tuple[float, float] | None:
    """The CURRENT centre of the marker circle with this uuid, in world mm —
    the call a "pick an arbitrary point" flow makes after the user has
    dragged the marker. None when the shape is no longer on the board (the
    user deleted it in KiCad, or it was swept)."""
    adapter.refresh_board()
    for s in _board(adapter).get_shapes():
        # gotcha 3: radius is a method — irrelevant here, we only need the
        # centre; filter by uuid to avoid a get_items_by_id() KIID round-trip.
        if isinstance(s, BoardCircle) and str(s.id.value) == uuid:
            return (s.center.x / MM, s.center.y / MM)
    return None


def remove_overlay(adapter, uuids: list[str]) -> bool:
    """Delete the overlay shapes by uuid. Returns remove_by_ids()'s success
    flag. Forces the KiCad redraw after the deletion (gotcha 2) by clearing
    the GUI selection — the drawn shapes no longer exist to select."""
    if not uuids:
        return True
    ok = adapter.remove_by_ids(uuids)
    adapter.select_items([])  # gotcha 2 — repaint after the delete
    return ok


def sweep_layer(adapter, layer) -> int:
    """Guaranteed cleanup: delete EVERY graphic shape on `layer` (the
    get_shapes() filter verified live in §0.7) and return how many were
    removed. The safe path against uuid leaks — call this on the dedicated
    overlay layer, never on a copper/real-content layer."""
    adapter.refresh_board()
    shapes = [s for s in _board(adapter).get_shapes() if s.layer == layer]
    if not shapes:
        return 0
    adapter.remove_by_ids(_uuids(shapes))
    adapter.select_items([])  # gotcha 2 — repaint after the delete
    return len(shapes)
