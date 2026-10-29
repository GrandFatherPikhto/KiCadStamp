# kicadstamp/geometry/thermal_grid.py

from ..domain.board import Pad, pad_has_copper
from ..domain.geometry import Vector2
from ..utils.units import MM
from ..exceptions import GeometryError
from ..i18n import _
from .pad_area import pad_angle_deg, pad_shape_center
from .spoke_layout import rotate_local_offset


def pick_thermal_pad(pads, number) -> tuple:
    """``(pad, count)`` — the pad a THERMAL array with this number must land on.

    ``count`` is how many pads carry that number AT ALL (the number a Log line
    reports); ``pad`` is the LARGEST of them by copper area (width × height of its
    copper layer), or ``None`` when no pad carries the number at all.

    Why not simply «the first pad» (what ``get_pad_by_number`` answers): a TO-252
    `TabPin2`, a DPAK/D2PAK, a SOT-223 tab and many QFN exposed pads repeat a pin's
    NUMBER, and the file lists the little pin FIRST — the grid then lands on the pin
    instead of the tab (measured on v103: U2 pad «2» is 2.2×1.2 first and 6.4×5.8
    second; 16 of 16 points fell inside the pin, none inside the tab).

    A pad that carries NO copper at all (a paste-only opening, `pad_has_copper`) or
    that has no size to measure is NOT a candidate. When no candidate is left but
    the number IS present, the FIRST such pad is returned — the historical answer,
    so a lone pad without a size still fails where it always did (`get_pad_size`
    raises its own GeometryError) instead of being reported as a missing pad.
    A tie in area keeps the FIRST candidate: the order of the file decides, never a
    float comparison accident."""
    named = [pad for pad in (pads or ())
             if str(getattr(pad, "number", "")) == str(number)]
    best = None
    best_area = -1.0
    for pad in named:
        if not pad_has_copper(pad):
            continue
        size = getattr(pad, "size", None)
        if size is None:
            continue
        area = float(size.x) * float(size.y)
        if area > best_area:                     # strict: a tie keeps the FIRST
            best, best_area = pad, area
    if best is None and named:
        return named[0], len(named)
    return best, len(named)


def get_pad_size(pad: Pad) -> tuple:
    """Returns (width, height) of the padstack copper layer."""
    size = pad.size
    if size is None:
        raise GeometryError(_("pad has no copper layers in its padstack"))
    return size.x, size.y

def compute_thermal_via_grid(pad: Pad, rows: int, cols: int, margin_mm: float, stagger: bool = False) -> list[Vector2]:
    """Returns a list of absolute via positions.

    The local grid (rows/columns/stagger/margin) is laid out in the PAD's own
    axes and then turned by the pad's absolute angle with the project's own
    rotation primitive — rotate_local_offset (geometry/spoke_layout.py), the
    same Y-down formula as cell_frame.rotate_ydown_mm. The inline
    `x·cos − y·sin, x·sin + y·cos` this replaces turned the array the OTHER way
    round (Д3, 2026-09-15): invisible while the grid maps onto itself (an
    unstaggered grid under 0/90/180/270°, a square one also under 45°), visible
    for a staggered grid under 90°/270° and for a rectangular one under 45°.
    The anchor to reality (measured live): pad 1 of IC2 sits at (−2.45, −1.75) mm
    with the footprint at 0° and at (−1.75, +2.45) mm at 90° — exactly the
    rotate_ydown_mm rule.

    The grid is centred on the centre of the pad SHAPE — position + the pad's own
    offset rotated by the pad angle (pad_area.pad_shape_center), never on the
    hole: an offset pad has its copper elsewhere (Д3, second half).
    """
    if rows < 1 or cols < 1:
        raise GeometryError(_("rows and cols must be >= 1"))

    width, height = get_pad_size(pad)
    margin = margin_mm * MM
    usable_w = width - 2 * margin
    usable_h = height - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise GeometryError(_("margin_mm={margin_mm} is too large for a pad {width}x{height} mm").format(
            margin_mm=margin_mm, width=f"{width/MM:.2f}", height=f"{height/MM:.2f}"))

    local_points = []
    for r in range(rows):
        y = 0 if rows == 1 else -usable_h/2 + usable_h * r / (rows - 1)
        row_offset = (usable_w / (cols * 2)) if (stagger and cols > 1 and r % 2 == 1) else 0
        for c in range(cols):
            x = 0 if cols == 1 else -usable_w/2 + usable_w * c / (cols - 1)
            local_points.append((x + row_offset, y))

    center = pad_shape_center(pad)
    if center is None:
        # A pad with no position cannot be on a board at all — an invariant of
        # the DTO, checked rather than assumed: a grid silently centred on
        # (0, 0) would be far worse than a fatal.
        raise GeometryError(_("pad has no position"))
    angle_deg = pad_angle_deg(pad)
    abs_points = []
    for lx, ly in local_points:
        rotated = rotate_local_offset(lx / MM, ly / MM, angle_deg)
        abs_points.append(Vector2.from_xy(center.x + rotated.x, center.y + rotated.y))
    return abs_points
