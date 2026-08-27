# kicadstamp/config/points.py
"""
points.py — the Point dataclass, kept out of models.py deliberately (see
handoff_2026_07_31_consolidated.md §7 item 5): Point is a named, reusable
anchor definition, a config-schema entity like Cell/Rule/ClonePlacement, but
one the project expects more of over time — kept in its own small file
rather than folded into the growing models.py.

No validation logic here — same split as the rest of config/ (loader.py
validates, models.py only describes shape).
"""
from dataclasses import dataclass



@dataclass
class Point:
    """
    A named, reusable anchor + optional shift, no rotation (rotation is
    always an operation applied by whoever USES a point, not a property of
    the point itself — see handoff_2026_07_31_consolidated.md).

    Exactly one of {anchor_ref, anchor_role, anchor_point, xy, anchor_origin}
    is the base:
      - anchor_ref/anchor_role(+anchor_sheet+anchor_cluster)/anchor_pad —
        same fields, same resolution, as Rule/ClonePlacement/
        ThermalViaArrayConfig. No {placeholder} substitution in anchor_sheet
        (Point has no params field — same convention as Rule/
        ThermalViaArrayConfig, not ClonePlacement's richer one).
      - anchor_point — chain to another point by name.
      - xy — literal absolute board coordinate, no anchor at all. (0, 0) here
        is the DRAWING SHEET's corner, not any physical board reference —
        for that, use anchor_origin below instead of guessing at a literal.
      - anchor_origin — the board's own live origin marker, read via kipy:
        'grid' (Place > Set Grid Origin, visual only, no exported file uses
        it) or 'drill' (Place > Drill/Place Origin, the auxiliary axis —
        drill/position files are always relative to it, Gerbers optionally
        via their own plot-dialog checkbox). Unlike xy, this is a LIVE board
        property, not a config-file literal — same "coordinate, no
        footprint" shape as xy (see point_resolver.py's resolve_point), so
        it is just as usable as an anchor_point chain BASE, but Rule/
        ThermalViaArrayConfig still cannot anchor to it directly (no pads to
        look up on a board origin — see _point_is_footprint_eligible).
    shift_x_mm/shift_y_mm layer on top of anchor_ref/anchor_role/anchor_point/
    anchor_origin (not on top of xy — fatal if both are set, just edit the
    literal coordinate instead), same board-absolute-mm convention as
    ManualSpoke.shift_x_mm/shift_y_mm (not relative to any anchor rotation,
    there is none here).
    """
    name: str
    anchor_ref: str | None = None
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_pad: str | None = None
    anchor_point: str | None = None
    xy: tuple[float, float] | None = None
    anchor_origin: str | None = None  # 'grid' | 'drill'
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
    # Optional free-form note shown in the GUI (handoff_2026_08_27_entity_comment_field.md).
    comment: str | None = None
