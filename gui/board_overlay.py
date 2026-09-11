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
defaults / stroke / marker geometry are module-level constants here that serve
as the SETTINGS DEFAULTS (Phase D): drawing reads them through the accessor
functions below, which look the values up in gui_state.json (Settings >
"Board overlay" writes the same keys).

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
from typing import Any, NamedTuple

from kipy.board_types import BoardCircle, BoardLayer, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from . import settings

# ── Overlay geometry defaults (Phase D: these are now SETTINGS DEFAULTS). ──
# Phase C put the geometry here as module constants precisely so Phase D could
# route the reads through gui_state.json without losing the defaults. The
# constants stay as the DEFAULTS; drawing reads them via the accessor
# functions below (the Settings dialog's "Board overlay" page writes the same
# keys, so configurator.py, cell_anchor_view.py and this module share ONE
# source of truth). The layer is a plain KiCad display name; the real layer
# enum is resolved from the LIVE board via resolve_overlay_layer() (never
# hardcoded as an enum value — the user-layer set is not fixed, §0.7).
OVERLAY_DEFAULT_LAYER = "User.Drawings"
# Bbox rectangle outline width, mm.
OVERLAY_BBOX_STROKE_MM = 0.15
# Marker circle radius, mm (Denis called the earlier trial 1.5 mm "здоровенный").
OVERLAY_MARKER_RADIUS_MM = 0.3
# Marker circle outline width, mm.
OVERLAY_MARKER_STROKE_MM = 0.1

# gui_state.json keys (settings.state) the overlay geometry is stored under.
OVERLAY_LAYER_KEY = "overlay_layer"
OVERLAY_BBOX_STROKE_KEY = "overlay_bbox_stroke_mm"
OVERLAY_MARKER_RADIUS_KEY = "overlay_marker_radius_mm"
OVERLAY_MARKER_STROKE_KEY = "overlay_marker_stroke_mm"

# LEGACY gui_state.json key: the pre-owner flat map
# `{root: {cell: {"marker": uuid, "bbox": uuid}}}`. Nothing writes it any more
# — gui/overlay_markers.py owns the marker map (key `overlay_markers`) and
# reads this one ONCE to migrate it (migrate_legacy_state), so no uuid is lost.
OVERLAY_STATE_KEY = "cell_anchor_overlay"


def overlay_layer_name() -> str:
    """The configured overlay layer's DISPLAY name (e.g. 'User.KiCadStamp') —
    OVERLAY_DEFAULT_LAYER when the key is absent."""
    return str(settings.state.get(OVERLAY_LAYER_KEY, OVERLAY_DEFAULT_LAYER))


def overlay_bbox_stroke_mm() -> float:
    """The configured bbox outline width in mm (OVERLAY_BBOX_STROKE_MM when
    the key is absent)."""
    return float(settings.state.get(OVERLAY_BBOX_STROKE_KEY, OVERLAY_BBOX_STROKE_MM))


def overlay_marker_radius_mm() -> float:
    """The configured marker radius in mm (OVERLAY_MARKER_RADIUS_MM when the
    key is absent)."""
    return float(settings.state.get(OVERLAY_MARKER_RADIUS_KEY, OVERLAY_MARKER_RADIUS_MM))


def overlay_marker_stroke_mm() -> float:
    """The configured marker outline width in mm (OVERLAY_MARKER_STROKE_MM
    when the key is absent)."""
    return float(settings.state.get(OVERLAY_MARKER_STROKE_KEY, OVERLAY_MARKER_STROKE_MM))


# kipy layer-enum member name lookup (value -> 'BL_User_5', ...), used ONLY to
# classify a layer as user-drawn; the user-visible name always comes from the
# LIVE board's get_layer_name() (never hardcoded — user layers can be renamed).
_LAYER_NAMES = {value: name for name, value in BoardLayer.items()}

# Sweepable-by-default user layers OTHER than BL_User_N (the probe's set) —
# the four KiCad "user-drawing" layers. The overlay must never sweep real
# board content (copper/silkscreen/Edge.Cuts/fab), so only user layers are
# ever offered or swept.
_USER_LAYER_NAMES = frozenset(
    ("BL_Dwgs_User", "BL_Cmts_User", "BL_Eco1_User", "BL_Eco2_User"))


def _is_user_layer(layer) -> bool:
    """True for a USER layer (BL_User_N + the Dwgs/Cmts/Eco user layers) —
    the ONLY layers safe to draw an overlay on and to sweep. This mirrors the
    reference probe's user_layers() filter (probe_board_overlay.py:70-76)."""
    name = _LAYER_NAMES.get(layer)
    return bool(name) and (name.startswith("BL_User_") or name in _USER_LAYER_NAMES)


def _board(adapter):
    """The adapter's live-board handle the shape/layer reads go through."""
    return adapter._board


def _layer_display(adapter, layer) -> str:
    """The LIVE display name of a layer (get_layer_name), falling back to the
    raw value for a layer that is not on this board at all."""
    try:
        return _board(adapter).get_layer_name(layer)
    except Exception:  # noqa: BLE001 — a display string must never fail
        return str(layer)


def overlay_layers(adapter) -> list[tuple[Any, str]]:
    """[(layer, KiCad's display name)] for every ENABLED USER layer of the
    live board, read from the board itself.

    The set is NOT fixed: KiCad 10 allows BL_User_1..BL_User_45 and user
    layers can be renamed (Denis added 'User.KiCadStamp' as BL_User_5), so
    any UI listing them must read the board rather than hardcode a list
    (§0.7). The display name (get_layer_name) is what the user sees and what
    resolve_overlay_layer() matches against.

    ONLY user layers are returned (BL_User_* plus the Dwgs/Cmts/Eco user
    layers — the reference probe's user_layers() filter): the overlay is
    drawn on and swept from a dedicated user layer, and this list is what a
    Phase-D layer combo would offer, so a user can never be offered (and
    later sweep) Edge.Cuts / a silkscreen / a copper layer.
    """
    return [(layer, _board(adapter).get_layer_name(layer))
            for layer in _board(adapter).get_enabled_layers()
            if _is_user_layer(layer)]


def resolve_overlay_layer(adapter, wanted: str):
    """The layer enum for a KiCad display name like 'User.Drawings' or
    'User.KiCadStamp', read LIVE from the board — None when no enabled layer
    has that display name."""
    for layer, display in overlay_layers(adapter):
        if display == wanted:
            return layer
    return None


def require_overlay_layer(adapter, layer_name: str):
    """Resolve `layer_name` (a KiCad display name) to the LIVE layer enum,
    raising the shared "enable the layer first" fatal when it is not enabled
    on this board. Used by the draw workers AND the whole-layer sweep — all of
    which run on the worker thread, so the live read is safe here."""
    layer = resolve_overlay_layer(adapter, layer_name)
    if layer is None:
        raise ValidationError(format_fatal_error(
            _("Overlay layer {layer!r} is not enabled on this board — enable "
              "it in KiCad first (or pick another overlay layer).")
            .format(layer=layer_name),
            [_("the overlay is drawn as real KiCad graphics on a user layer; "
               "without the layer enabled nothing can be drawn")]))
    return layer


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


class OverlayShape(NamedTuple):
    """One overlay graphic on a layer, seen through the owner's eyes — the
    uuid plus just enough shape information for reconciliation. Raw kipy
    objects never leave this module (see the module docstring)."""

    uuid: str
    kind: str                                # "circle" | "rect" | "other"
    center_mm: tuple[float, float] | None    # circles only


def list_overlay_shapes(adapter, layer) -> list[OverlayShape]:
    """Every graphic shape on `layer`, with its uuid and (for circles) centre
    in world mm. READ-ONLY, and the SINGLE layer traversal this module has:
    sweep_layer() and the markers owner both go through here, so the sweep and
    the owner can never disagree about what is on the layer (E.2.3 of
    plan_2026_09_11_overlay_markers_owner.md).

    There is NO user-layer guard here — nothing is deleted. sweep_layer()
    keeps its own guard, unchanged."""
    adapter.refresh_board()
    shapes: list[OverlayShape] = []
    for s in _board(adapter).get_shapes():
        if s.layer != layer:
            continue
        if isinstance(s, BoardCircle):
            # gotcha 3: the radius is a method; reconciliation needs only the
            # centre, so no radius read is done here.
            shapes.append(OverlayShape(
                str(s.id.value), "circle",
                (s.center.x / MM, s.center.y / MM)))
        elif isinstance(s, BoardRectangle):
            shapes.append(OverlayShape(str(s.id.value), "rect", None))
        else:
            shapes.append(OverlayShape(str(s.id.value), "other", None))
    return shapes


def sweep_layer(adapter, layer) -> int:
    """Guaranteed cleanup: delete EVERY graphic shape on `layer` and return
    how many were removed. The safe path against uuid leaks — call this on the
    dedicated overlay user layer, never on a copper/real-content layer.

    Guard: REFUSES (fatal ValidationError) to sweep a layer that is not in
    the enabled USER-layer set — sweeping Edge.Cuts / a silkscreen / a copper
    layer would delete real board content (the very data-loss path a Phase-D
    "sweep the overlay layer" button must never be able to reach). The sweep
    and the markers owner share list_overlay_shapes(), so they always see the
    same set of shapes."""
    known = {lay for lay, _name in overlay_layers(adapter)}
    if layer not in known:
        display = _layer_display(adapter, layer)
        raise ValidationError(format_fatal_error(
            _("sweep_layer: refusing to delete graphics on layer “{layer}” — "
              "only USER layers can be swept").format(layer=display),
            [_("choose the dedicated overlay user layer (e.g. "
               "User.KiCadStamp); a sweep on copper, silkscreen or Edge.Cuts "
               "would erase real board content")]))
    shapes = list_overlay_shapes(adapter, layer)
    if not shapes:
        return 0
    adapter.remove_by_ids([s.uuid for s in shapes])
    adapter.select_items([])  # gotcha 2 — repaint after the delete
    return len(shapes)


def sweep_layer_by_name(adapter, layer_name: str) -> int:
    """Whole-layer guaranteed cleanup for the Settings/Tools "Remove entire
    overlay" button: resolve the KiCad display name and delete EVERY graphic
    shape on that user layer. The underlying sweep_layer() user-layer guard
    is NOT weakened — a name that does not resolve to an enabled USER layer
    is a fatal (nothing is swept), so the button can never erase real board
    content by pointing the sweep at copper/silkscreen/Edge.Cuts."""
    layer = require_overlay_layer(adapter, layer_name)
    return sweep_layer(adapter, layer)
