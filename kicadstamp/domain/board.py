# kicadstamp/domain/board.py
"""Domain DTOs for board entities + kipy mappers.

P1-4 ("ideal" scope) of the 2026-08-25 architecture audit: replace the kipy
board *entity* types that used to leak through `IBoardAdapter`
(``FootprintInstance``, ``Via``, ``Track``, ``Pad``, ``Net``, ``Zone``) with
domain DTOs, so consumers no longer import those kipy classes.

Scope of THIS stage: only the board **entity** types are decoupled. The
geometric/enum *value* types (``Vector2``, ``Angle``, ``Box2``, ``BoardLayer``)
are deliberately kept as kipy types for now — they are stable, well-tested
value objects, and replacing them is a separate follow-up.

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

from kipy.board_types import BoardLayer as KipyBoardLayer, FootprintInstance, Net as KipyNet, Pad as KipyPad, Track as KipyTrack, Via as KipyVia, Zone as KipyZone
from kipy.geometry import Vector2 as KipyVector2

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
    "net_from_kipy",
    "pad_from_kipy",
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
    layer: BoardLayer | None = None
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
    ``angle_rad`` is the padstack rotation in radians.
    """

    number: Any  # kipy reports str/int/float depending on the pad — kept as-is
    net_name: str | None
    position: Vector2
    layer: BoardLayer | None = None
    size: Vector2 | None = None
    angle_rad: float = 0.0
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
    layer: BoardLayer | None = None
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


def _layer_from_kipy(layer) -> BoardLayer:
    return BoardLayer.BL_B_Cu if layer == KipyBoardLayer.BL_B_Cu else BoardLayer.BL_F_Cu


def net_from_kipy(net: KipyNet) -> Net:
    return Net(name=net.name, code=getattr(net, "code", 0), _kipy=net)


def zone_from_kipy(zone: KipyZone) -> Zone:
    layer = getattr(zone, "layer", None)
    return Zone(name=zone.name,
                layer=_layer_from_kipy(layer) if layer is not None else None,
                _kipy=zone)


def footprint_from_kipy(fp: FootprintInstance) -> Footprint:
    sheet_path = getattr(fp, "sheet_path", None)
    path = getattr(sheet_path, "path", None) or ()
    sheet_path_uuids = tuple(str(u.value) for u in path)
    return Footprint(
        ref=fp.reference_field.text.value if fp.reference_field else "",
        uuid=str(fp.id.value),
        position=_point_from_kipy(fp.position),
        angle_deg=fp.orientation.degrees,
        layer=_layer_from_kipy(fp.layer),
        value=fp.value_field.text.value if fp.value_field else None,
        sheet_path_uuids=sheet_path_uuids,
        _kipy=fp,
    )


def pad_from_kipy(pad: KipyPad) -> Pad:
    padstack = getattr(pad, "padstack", None)
    size = None
    angle_rad = 0.0
    if padstack is not None:
        copper = getattr(padstack, "copper_layers", None)
        if copper:
            size = _point_from_kipy(copper[0].size)
        angle = getattr(padstack, "angle", None)
        if angle is not None:
            angle_rad = angle.to_radians()
    layer = getattr(pad, "layer", None)
    return Pad(
        number=pad.number,
        net_name=pad.net.name if pad.net else None,
        position=_point_from_kipy(pad.position),
        layer=_layer_from_kipy(layer) if layer is not None else None,
        size=size,
        angle_rad=angle_rad,
        _kipy=pad,
    )


def via_from_kipy(via: KipyVia) -> Via:
    layer = getattr(via, "layer", None)
    return Via(
        uuid=str(via.id.value),
        position=_point_from_kipy(via.position),
        net_name=via.net.name if via.net else None,
        drill_mm=via.drill_diameter / MM,
        diameter_mm=via.diameter / MM,
        layer=_layer_from_kipy(layer) if layer is not None else None,
        _kipy=via,
    )


def track_from_kipy(track: KipyTrack) -> Track:
    return Track(
        uuid=str(track.id.value),
        start=_point_from_kipy(track.start),
        end=_point_from_kipy(track.end),
        net_name=track.net.name if track.net else None,
        width_mm=track.width / MM,
        layer=_layer_from_kipy(track.layer),
        _kipy=track,
    )


def board_item_from_kipy(item: Any) -> Any:
    """Map one mixed board item (from ``get_selected_items``/``get_vias``/
    ``get_tracks``) to its DTO; leave non-entity items (BoardText, drawings,
    etc.) untouched."""
    if isinstance(item, FootprintInstance):
        return footprint_from_kipy(item)
    if isinstance(item, KipyVia):
        return via_from_kipy(item)
    if isinstance(item, KipyTrack):
        return track_from_kipy(item)
    return item
