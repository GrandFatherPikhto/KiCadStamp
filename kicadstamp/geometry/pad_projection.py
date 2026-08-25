# kicadstamp/geometry/pad_projection.py
"""
pad_projection.py — predicts where a specific pad of a component will end up
if the component is moved to a new position dest and rotated to a new angle
angle_deg (already accounting for back‑layer mirroring if applied to the angle
elsewhere, at a higher level).

Previously this logic (and its bug) existed in TWO places simultaneously:
power_pin_orienter.py (for choosing facing) and was implicitly assumed (but
NOT applied) in via_planner.py (for stitching via position — there it simply
used the "old absolute pad offset, shifted as‑is", which placed vias on pads
whenever the angle changed). Now there is one source for both consumers — if
the flip convention turns out wrong, it is fixed in one place, not two.

IMPORTANT: needs_flip=True (mirroring the local offset along X) is currently
an empirically UNCONFIRMED assumption. See
diagnose/test_pad_mirror_convention.py — a one‑time but definitive test on a
real board, comparing this prediction with what KiCad actually shows after a
real flip+rotation.
"""
from ..domain.board import Footprint, Pad
from kipy.geometry import Vector2, Angle


def local_pad_offset(fp: Footprint, pad: Pad) -> Vector2:
    """
    Returns the pad offset relative to the footprint centre in the footprint's
    OWN UNROTATED coordinate system — i.e. a constant geometry fact independent
    of the current angle. Obtained by "cancelling" the current rotation angle
    from the known absolute offset.
    """
    origin = Vector2.from_xy(0, 0)
    diff = pad.position - fp.position
    return diff.rotate(Angle.from_degrees(-fp.angle_deg), origin)


def predict_pad_position(
    fp: Footprint,
    pad: Pad,
    dest: Vector2,
    angle_deg: float,
    needs_flip: bool,
) -> Vector2:
    """
    Predicts the ABSOLUTE position of the pad if fp is moved to dest and
    rotated to angle_deg (the angle itself is already final, including any
    mirroring for the back layer if required — nothing additional is done with
    the angle here).

    needs_flip: True if fp physically moves to the opposite side of the board
    in this run (the current fp.layer differs from the target layer). In that
    case the local pad offset is mirrored along the X axis BEFORE applying the
    new rotation — this is how the board is "seen from the back side".
    """
    origin = Vector2.from_xy(0, 0)
    offset = local_pad_offset(fp, pad)
    if needs_flip:
        offset = Vector2.from_xy(-offset.x, offset.y)
    rotated = offset.rotate(Angle.from_degrees(angle_deg), origin)
    return dest + rotated