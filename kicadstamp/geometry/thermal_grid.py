# kicadstamp/geometry/thermal_grid.py

from ..domain.board import Pad
from ..domain.geometry import Vector2
from ..utils.units import MM
from ..exceptions import GeometryError
from ..i18n import _
from .pad_area import pad_angle_deg, pad_shape_center
from .spoke_layout import rotate_local_offset

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
