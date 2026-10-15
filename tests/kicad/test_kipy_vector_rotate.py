#!/usr/bin/env python3
"""Watchdogs for the exact rotation patch — Ч2 of plan_2026_10_15_footprint_3d_model_loss.

The disease (live, 15.10.2026, after the 3D-model fix landed): 27 footprints came back from
the placer with ``lib_footprint_mismatch`` while EVERY pad of every footprint stayed
byte-identical — only the graphics drifted, by one nanometre (``0.96 -> 0.959999``,
``-0.146267 -> -0.146266``). Cause: kipy's ``Vector2.rotate`` (geometry.py:127) answers
``int(dy*sin + dx*cos) + cx``, and ``int()`` TRUNCATES a value that is exactly representable
but arrives as 959999.99999999988, because ``cos(90 deg) = 6.1e-17``. The whole orientation
setter funnels through that one function — pads, fields, texts, zones and every shape class —
so one repaired funnel fixes all of them. The patch lives in
``kicadstamp/kicad/kipy_footprint_safety.py`` and is installed at import.

WHY THE NUMBERS LOOK ARBITRARY — they are not, and this file's own history is the reason.
The float error added to the computed coordinate is ``neighbour * 6.1e-17``, while doubles near
that coordinate are spaced ``coordinate * 2.2e-16`` apart: the error can only survive into
``int()`` when ``neighbour > ~1.8 * coordinate``, i.e. when the OTHER axis is a couple of
millimetres out. Measured: the C_0603 coordinates from the report (±0.96 mm against
±0.146267 mm) are NOT enough — the perturbation is under half a double step and the sum is
exactly an integer already; a point like (-1.8 mm, 0.96 mm) IS enough and truncates to
959999. A cell written with "nice" coordinates alone would therefore have been GREEN against
the unfixed kipy, which is worse than no cell.

Real kipy objects, no board, no IPC.
"""
import importlib
import math

from kipy.board_types import FootprintInstance, Pad, Field
from kipy.geometry import Vector2 as KipyVector2, Angle as KipyAngle

from kicadstamp.kicad import kipy_footprint_safety as shim
from kicadstamp.kicad.kipy_footprint_safety import (
    _EXACT_ROTATE_MARKER,
    _KIPY_VECTOR2_ROTATE,
    set_footprint_orientation,
)

ORIGIN = KipyVector2.from_xy(0, 0)

#: The coordinates the live report named: the C_0603 silk lines sit at ±0.146267 mm and its
#: fab rectangles at ±0.96 mm (nanometres, the way KiCad stores them).
FACT_CORNERS = [
    (960_000, 146_267), (960_000, -146_267), (-960_000, 146_267), (-960_000, -146_267),
    (146_267, 960_000), (-146_267, 960_000), (146_267, -960_000), (-146_267, -960_000),
]

#: The point that REALLY reproduces the report: the neighbouring axis is 1.8 mm, so the float
#: error (~1.1e-10) tops half the spacing of the doubles near 0.96 mm (~5.8e-11).
TRUNCATION_POINT = (-1_800_000, 960_000)


def _exact_quarter(x, y, degrees):
    """A quarter turn written out HERE, on purpose: the cell must never ask the patched
    function what the answer should be."""
    if degrees == 90:
        return (y, -x)
    if degrees == 180:
        return (-x, -y)
    if degrees == 270:
        return (-y, x)
    raise ValueError(f"not a quarter turn: {degrees}")


def _rotated(x, y, degrees):
    """(x, y) rotated about the origin by the INSTALLED rotation."""
    v = KipyVector2.from_xy(x, y)
    v.rotate(KipyAngle.from_degrees(degrees), ORIGIN)
    return (v.x, v.y)


def _rotated_by(rotate, x, y, degrees):
    """The same, through a GIVEN rotate function — the canary passes kipy's ORIGINAL."""
    v = KipyVector2.from_xy(x, y)
    rotate(v, KipyAngle.from_degrees(degrees), ORIGIN)
    return (v.x, v.y)


def test_quarter_turns_of_the_reported_coordinates_are_exact():
    """Cell 1 — the report's own numbers, at 90/180/270: exact, and a round trip is
    byte-identical. This also pins the sign/axis convention of the quarter-turn arms: a
    swapped or mis-signed arm moves a corner away from where the library puts it."""
    for degrees in (90, 180, 270):
        for x, y in FACT_CORNERS:
            assert _rotated(x, y, degrees) == _exact_quarter(x, y, degrees), (x, y, degrees)
            back = KipyVector2.from_xy(x, y)
            back.rotate(KipyAngle.from_degrees(degrees), ORIGIN)
            back.rotate(KipyAngle.from_degrees(-degrees), ORIGIN)
            assert (back.x, back.y) == (x, y), f"round trip lost a nanometre at {degrees} deg"


def test_the_nanometre_is_not_lost_where_the_float_error_is_real():
    """Cell 2 — the point that reproduces the live drift, at all three quarter turns.

    Removing or mis-writing the quarter-turn arms has to show up HERE: the fallback formula
    with ``round()`` happens to stay exact for quarter turns too (measured), so a cell that
    leaned on the fallback would not have noticed the arms going away."""
    x, y = TRUNCATION_POINT
    assert _rotated(x, y, 90) == (960_000, 1_800_000)
    assert _rotated(x, y, 180) == (1_800_000, -960_000)
    assert _rotated(x, y, 270) == (-960_000, -1_800_000)


def test_a_pad_goes_through_the_setter_exactly():
    """Cell 3 — the patch has to reach the REAL path: the orientation setter rotates pads
    through ``Vector2.rotate``, so a pad sitting on the reproducing point is one nanometre
    short without the patch and exact with it."""
    fp = FootprintInstance()
    fp.id.value = "uuid-pad"
    fp.position = KipyVector2.from_xy(0, 0)
    fp.orientation = KipyAngle.from_degrees(0.0)
    ref_field = Field()
    ref_field.name = "Reference"
    ref_field.text.value = "U9"
    fp.reference_field = ref_field
    pad = Pad()
    pad.position = KipyVector2.from_xy(*TRUNCATION_POINT)
    fp.definition.items = [ref_field, pad]

    set_footprint_orientation(fp, KipyAngle.from_degrees(90.0))

    moved = next(item for item in fp.definition.items if isinstance(item, Pad))
    assert (moved.position.x, moved.position.y) == (960_000, 1_800_000)


def test_a_non_orthogonal_angle_rounds_instead_of_truncating():
    """Cell 4 — at 45 degrees no arm can help, so the fallback is kipy's own formula with
    ``round()``. (0, 1 mm) answers 707106.78 nm on both axes: round() -> 707107, the
    truncation -> 707106."""
    x, y = 0, 1_000_000
    radians = math.radians(45)
    expected = round(y * math.sin(radians) + x * math.cos(radians))
    assert _rotated(x, y, 45) == (expected, expected)
    assert expected == 707_107, "the point must be one where round() and int() disagree"


def test_the_patch_is_installed_at_import_permanent_and_reload_safe():
    """Cell 5 — conditions of the chosen form: installed at import, NOT scoped to a call (a
    swap-and-restore would race the worker thread against the UI), and a reload must neither
    wrap the wrapper nor lose kipy's own implementation, which the canary reads."""
    assert getattr(KipyVector2.rotate, _EXACT_ROTATE_MARKER, False), \
        "the exact rotation must be installed as soon as the module is imported"

    _rotated(*TRUNCATION_POINT, 90)          # any call at all...
    assert getattr(KipyVector2.rotate, _EXACT_ROTATE_MARKER, False), \
        "the patch is permanent: a call must not restore kipy's rotate"

    saved = shim._KIPY_VECTOR2_ROTATE
    importlib.reload(shim)
    assert getattr(KipyVector2.rotate, shim._EXACT_ROTATE_MARKER, False), \
        "a reload must not undo the patch"
    assert shim._KIPY_VECTOR2_ROTATE is saved, (
        "a reload re-captured the rotation: the canary would then measure our replacement "
        "instead of kipy's own")


def test_the_original_kipy_rotate_truncates():
    """CANARY on kipy's arithmetic, not on ours.

    GREEN while kipy truncates — that is exactly what the patch in
    kicadstamp/kicad/kipy_footprint_safety.py repairs. RED the day kipy rounds by itself: then
    delete the patch block (and this cell with it — the item guard has its own canary and may
    still be needed).
    """
    assert _rotated_by(_KIPY_VECTOR2_ROTATE, *TRUNCATION_POINT, 90) == (959_999, 1_800_000), (
        "kipy's Vector2.rotate no longer loses the nanometre — remove the patch in "
        "kicadstamp/kicad/kipy_footprint_safety.py and this cell with it")
