# kicadstamp/domain/board.py
"""Domain DTOs for board entities + kipy mappers.

P1-4 ("ideal" scope) of the 2026-08-25 architecture audit: replace the kipy
board *entity* types that used to leak through `IBoardAdapter`
(``FootprintInstance``, ``Via``, ``Track``, ``Pad``, ``Net``, ``Zone``) with
domain DTOs, so consumers no longer import those kipy classes.

The geometric/enum *value* types (``Vector2``, ``Angle``, ``Box2``,
``BoardLayer``) are ALSO decoupled — see ``kicadstamp/domain/geometry.py``
(completed 2026-08-25). The whole seam (entity DTOs + value types) is now
domain-only, and kipy is confined to ``adapter.py``.

Write-path round-trip: each mutable DTO carries an opaque ``_kipy`` back
reference to the live kipy object. Consumers MUST NOT touch ``_kipy``; it
exists only so the adapter can push mutations back (``update_items``) or hand
the object to KiCad (``create_items``/``select_items``/``flip_selected``)
without a UUID lookup table. The ``unwrap()`` helper below is the single place
that extracts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kipy.board_types import BoardLayer as KipyBoardLayer, FootprintInstance, Net as KipyNet, Pad as KipyPad, PadStackShape as KipyPadStackShape, Track as KipyTrack, Via as KipyVia, Zone as KipyZone
from kipy.geometry import Vector2 as KipyVector2

from ..constants import (
    PAD_SHAPE_CHAMFERED,
    PAD_SHAPE_CIRCLE,
    PAD_SHAPE_CUSTOM,
    PAD_SHAPE_OVAL,
    PAD_SHAPE_RECT,
    PAD_SHAPE_ROUNDRECT,
    PAD_SHAPE_TRAPEZOID,
    PAD_SHAPE_UNKNOWN,
)
from ..utils.units import MM
from .geometry import BoardLayer, Vector2

__all__ = [
    "Footprint",
    "Net",
    "Pad",
    "Track",
    "Via",
    "Zone",
    "board_item_from_kipy",
    "footprint_from_kipy",
    "layer_from_kipy",
    "layer_to_kipy",
    "net_from_kipy",
    "pad_from_kipy",
    "pad_shape_from_kipy",
    "track_from_kipy",
    "unwrap",
    "via_from_kipy",
    "zone_from_kipy",
]


def unwrap(item: Any) -> Any:
    """Return the live kipy object backing a DTO, or the item itself.

    The single place allowed to reach into ``_kipy``. Only domain DTOs are
    unwrapped; anything else (raw kipy objects, test doubles) passes through
    unchanged — this keeps plain Mock/SimpleNamespace fakes working in tests
    where a ``_kipy`` attribute would otherwise auto-create.
    """
    if isinstance(item, (Footprint, Via, Track, Net, Zone, Pad)):
        return item._kipy if item._kipy is not None else item
    return item


# --- Net -------------------------------------------------------------------

@dataclass
class Net:
    """A board net (``get_net_by_name``/``get_all_nets``)."""

    name: str
    code: int = 0
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Zone ------------------------------------------------------------------

@dataclass(eq=False)
class Zone:
    """A board zone (``get_zone_by_name``)."""

    name: str
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Footprint -------------------------------------------------------------

@dataclass(eq=False)
class Footprint:
    """A placed footprint (``get_footprint``/``get_footprints``).

    Position is in board units (nm), matching ``Vector2``; ``angle_deg`` is
    the orientation in degrees; ``layer`` is a kipy ``BoardLayer``.
    """

    ref: str
    uuid: str
    position: Vector2
    angle_deg: float
    layer: BoardLayer
    value: str | None = None
    # sheet_path.path UUIDs as plain strings (hierarchical-sheet identity;
    # used by sheet_names.resolve_sheet_path_names and channel_copy).
    sheet_path_uuids: tuple[str, ...] = ()
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Pad -------------------------------------------------------------------

@dataclass(eq=False)
class Pad:
    """A footprint pad (``get_footprint_pads``/``get_pad_by_number``).

    ``size`` is the copper-layer size in board units (nm) — the
    ``padstack.copper_layers[0].size`` value thermal-grid keepout needs;
    ``angle_rad`` is the padstack rotation in radians, ABSOLUTE (measured
    15.09.2026: a footprint at 90° reports padstack angles of 180° — the
    footprint's own rotation is already included).

    ``shape``/``offset``/``trapezoid_delta`` complete the same copper layer, so
    the pad's own copper area can be derived with NO further board access
    (geometry/pad_area.py): KiCad's bounding box for a pad of a footprint
    rotated by an angle that is not a multiple of 90° comes back shifted
    (measured 15.09.2026, plan_2026_09_15_pad_geometry_thermal_vias → Д1), so it
    cannot be used as "where the copper is". All three default to None, which
    reads as "a plain rectangle with no offset" — a test double that predates
    these fields (tests/test_via_planner.py sets only
    number/position/size/angle_rad) keeps working with no edits.
    """

    number: Any  # kipy reports str/int/float depending on the pad — kept as-is
    net_name: str | None
    position: Vector2
    size: Vector2 | None = None
    angle_rad: float = 0.0
    # One of constants.PAD_SHAPE_*; None = not reported by the source (treated
    # as a plain rectangle by geometry/pad_area.py).
    shape: str | None = None
    offset: Vector2 | None = None            # nm, in the PAD's own axes; None = (0, 0)
    trapezoid_delta: Vector2 | None = None   # nm, trapezoid only
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Via -------------------------------------------------------------------

@dataclass(eq=False)
class Via:
    """A via (``get_vias``/``create_via``).

    ``drill_mm``/``diameter_mm`` are already converted from kipy's nanometre
    ``drill_diameter``/``diameter`` fields (consumers previously divided by
    ``MM`` inline).
    """

    uuid: str
    position: Vector2
    net_name: str | None
    drill_mm: float
    diameter_mm: float
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Track -----------------------------------------------------------------

@dataclass(eq=False)
class Track:
    """A track segment (``get_tracks``/``create_track``).

    ``width_mm`` is converted from kipy's nanometre ``width`` field.
    """

    uuid: str
    start: Vector2
    end: Vector2
    net_name: str | None
    width_mm: float
    layer: BoardLayer
    _kipy: Any = field(default=None, repr=False, compare=False)


# --- Mappers (kipy -> DTO) -------------------------------------------------

def _point_from_kipy(v) -> Vector2:
    return Vector2(v.x, v.y)


# Full-copper mapping between kipy's BoardLayer and the domain BoardLayer.
# Member names are identical on both sides (BL_F_Cu .. BL_B_Cu incl. the inner
# BL_In1_Cu..BL_In30_Cu copper layers), so the tables are built by name rather
# than hard-coding kipy's numeric values. Added 2026-09-06
# (plan_2026_09_05_scheme_list.md Step 0): the old binary mapper silently
# collapsed every inner layer to F.Cu, which would corrupt capture of a real
# stack's inner-layer copper.
_COPPER_LAYER_MEMBER_NAMES = (
    ["BL_F_Cu"] + [f"BL_In{i}_Cu" for i in range(1, 31)] + ["BL_B_Cu"]
)


def _build_copper_layer_maps():
    kipy_to_domain = {}
    domain_to_kipy = {}
    for member_name in _COPPER_LAYER_MEMBER_NAMES:
        domain_layer = BoardLayer[member_name]
        kipy_value = KipyBoardLayer.Value(member_name)
        kipy_to_domain[kipy_value] = domain_layer
        domain_to_kipy[domain_layer] = kipy_value
    return kipy_to_domain, domain_to_kipy


_KIPY_CU_TO_DOMAIN, _DOMAIN_CU_TO_KIPY = _build_copper_layer_maps()


def layer_from_kipy(layer) -> BoardLayer:
    """Map a kipy copper layer to the domain BoardLayer.

    Explicit for every copper layer (F.Cu/In1.Cu..In30.Cu/B.Cu) — real
    inner-layer copper is no longer silently collapsed to F.Cu (the old binary
    mapper's bug). Anything NOT one of those 33 members keeps the historical
    F.Cu fallback instead of raising: the table already covers every kipy
    copper member, so an unknown value is only ever a non-copper sentinel or a
    test double — never real routed copper.
    """
    try:
        return _KIPY_CU_TO_DOMAIN[layer]
    except (KeyError, TypeError):
        return BoardLayer.BL_F_Cu


def layer_to_kipy(layer: BoardLayer):
    """Map a domain BoardLayer back to the kipy copper layer value (write path).

    The inverse of :func:`layer_from_kipy` — one shared source of truth so the
    adapter's ``create_track``/``update_items`` cannot drift from the reader.
    """
    try:
        return _DOMAIN_CU_TO_KIPY[layer]
    except KeyError:
        raise ValueError(f"not a copper layer: {layer!r}") from None


def net_from_kipy(net: KipyNet) -> Net:
    return Net(name=net.name, code=getattr(net, "code", 0), _kipy=net)


def zone_from_kipy(zone: KipyZone) -> Zone:
    return Zone(name=zone.name, _kipy=zone)


def footprint_from_kipy(fp: FootprintInstance) -> Footprint:
    sheet_path = getattr(fp, "sheet_path", None)
    path = getattr(sheet_path, "path", None) or ()
    sheet_path_uuids = tuple(str(u.value) for u in path)
    return Footprint(
        ref=fp.reference_field.text.value if fp.reference_field else "",
        uuid=str(fp.id.value),
        position=_point_from_kipy(fp.position),
        angle_deg=fp.orientation.degrees,
        layer=layer_from_kipy(fp.layer),
        value=fp.value_field.text.value if fp.value_field else None,
        sheet_path_uuids=sheet_path_uuids,
        _kipy=fp,
    )


# kipy's PadStackShape -> the domain vocabulary (constants.PAD_SHAPE_*), covering
# the whole enum. Anything unmapped (a future PSS_* member, a sentinel) becomes
# PAD_SHAPE_UNKNOWN, which has no area of its own: the caller then falls back to
# KiCad's bounding box and warns where that box may be shifted
# (geometry/pad_area.warn_bbox_fallback).
_KIPY_PAD_SHAPE_TO_DOMAIN = {
    KipyPadStackShape.PSS_RECTANGLE: PAD_SHAPE_RECT,
    KipyPadStackShape.PSS_ROUNDRECT: PAD_SHAPE_ROUNDRECT,
    KipyPadStackShape.PSS_CHAMFEREDRECT: PAD_SHAPE_CHAMFERED,
    KipyPadStackShape.PSS_OVAL: PAD_SHAPE_OVAL,
    KipyPadStackShape.PSS_CIRCLE: PAD_SHAPE_CIRCLE,
    KipyPadStackShape.PSS_TRAPEZOID: PAD_SHAPE_TRAPEZOID,
    KipyPadStackShape.PSS_CUSTOM: PAD_SHAPE_CUSTOM,
    KipyPadStackShape.PSS_UNKNOWN: PAD_SHAPE_UNKNOWN,
}


def pad_shape_from_kipy(shape: Any) -> str:
    """Map a kipy PadStackShape onto constants.PAD_SHAPE_*.

    Total by design: this runs on the read path of every pad of every footprint,
    so an unrecognised value must degrade to PAD_SHAPE_UNKNOWN (fallback
    geometry), never raise.
    """
    try:
        return _KIPY_PAD_SHAPE_TO_DOMAIN[shape]
    except (KeyError, TypeError):
        return PAD_SHAPE_UNKNOWN


def pad_from_kipy(pad: KipyPad) -> Pad:
    padstack = getattr(pad, "padstack", None)
    size = None
    shape = None
    offset = None
    trapezoid_delta = None
    angle_rad = 0.0
    if padstack is not None:
        copper = getattr(padstack, "copper_layers", None)
        if copper:
            layer = copper[0]
            size = _point_from_kipy(layer.size)
            # A layer that does not report its shape at all (a hand-made test
            # double) keeps `None` = "plain rectangle"; a layer that reports an
            # unmapped shape gets PAD_SHAPE_UNKNOWN = "no area of its own".
            raw_shape = getattr(layer, "shape", None)
            if raw_shape is not None:
                shape = pad_shape_from_kipy(raw_shape)
            raw_offset = getattr(layer, "offset", None)
            if raw_offset is not None:
                offset = _point_from_kipy(raw_offset)
            raw_delta = getattr(layer, "trapezoid_delta", None)
            if raw_delta is not None:
                trapezoid_delta = _point_from_kipy(raw_delta)
        angle = getattr(padstack, "angle", None)
        if angle is not None:
            angle_rad = angle.to_radians()
    return Pad(
        number=pad.number,
        net_name=pad.net.name if pad.net else None,
        position=_point_from_kipy(pad.position),
        size=size,
        angle_rad=angle_rad,
        shape=shape,
        offset=offset,
        trapezoid_delta=trapezoid_delta,
        _kipy=pad,
    )


def via_from_kipy(via: KipyVia) -> Via:
    return Via(
        uuid=str(via.id.value),
        position=_point_from_kipy(via.position),
        net_name=via.net.name if via.net else None,
        drill_mm=via.drill_diameter / MM,
        diameter_mm=via.diameter / MM,
        _kipy=via,
    )


def track_from_kipy(track: KipyTrack) -> Track:
    return Track(
        uuid=str(track.id.value),
        start=_point_from_kipy(track.start),
        end=_point_from_kipy(track.end),
        net_name=track.net.name if track.net else None,
        width_mm=track.width / MM,
        layer=layer_from_kipy(track.layer),
        _kipy=track,
    )


def board_item_from_kipy(item: Any) -> Any:
    """Map one mixed board item (from ``get_selected_items``/``get_vias``/
    ``get_tracks``) to its DTO; leave non-entity items (BoardText, drawings,
    etc.) untouched.

    A selected PAD is mapped too (Фаза B of plan_2026_09_09_cell_anchor_v2,
    §0.3): ``get_selected_items`` returns raw kipy Pads when the user clicks a
    pad, and this function used to leave them opaque (``ref=None`` /
    ``net_name=None`` for every consumer) even though ``pad_from_kipy`` and the
    Pad DTO already existed."""
    if isinstance(item, FootprintInstance):
        return footprint_from_kipy(item)
    if isinstance(item, KipyPad):
        return pad_from_kipy(item)
    if isinstance(item, KipyVia):
        return via_from_kipy(item)
    if isinstance(item, KipyTrack):
        return track_from_kipy(item)
    return item
