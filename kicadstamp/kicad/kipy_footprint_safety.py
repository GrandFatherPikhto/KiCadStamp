# kicadstamp/kicad/kipy_footprint_safety.py
"""Footprint-level safety shim over kipy's own setters (plan_2026_10_15_footprint_3d_model_loss).

The ONE reason this module exists: kipy's ``FootprintInstance.orientation`` setter REBUILDS
``definition.items`` out of the items it knows how to rotate and silently drops every other
item — ``Footprint3DModel`` first of all — even when the angle does not change. kicad-python
0.8.0, kipy/board_types.py:2123-2164: ``updated_items`` collects only
Field / Pad / BoardText / Zone / BoardShape, then ``self.definition.items = updated_items``.

Measured live 15.10.2026 on the ControllerSTM32F103C8T6 board: 73 of 77 footprints carry a
3D model and ALL 73 lost it, at a ZERO delta just as much as at +90 deg — so every move
through the adapter damaged the board, not just a rotation. Measured offline on kipy's own
objects the same day: everything outside that chain goes (``Footprint3DModel``,
``BoardTextBox``, ``Barcode``, ``Dimension``, ``ReferenceImage``, ``Group``, ``Track``,
``Via``), which is why the rule below is not about 3D models at all.

``set_footprint_orientation`` is that setter with the dropped items put back:

* what to restore is decided by IDENTITY — "whatever the setter left behind" — never by a
  list of type names. The same rule therefore covers every item type the setter has no arm
  for, without naming one of them, and it cannot grow stale when kipy's chain changes;
* ``BoardShape`` is the ONE exclusion, and it is deliberate: the setter's shape branch
  REPLACES a shape with a freshly built wrapper of its own choosing (a rectangle becomes a
  ``BoardPolygon`` off the orthogonal angles — board_types.py:2155-2162), so a new object
  proves nothing and a naive identity diff would append a DUPLICATE figure. The branch is
  assumed to keep working; tests/kicad/test_adapter_orientation_items.py states that
  assumption as a canary cell instead of pretending to verify it here;
* the original ORDER is rebuilt rather than appended to: the setter carries its branches'
  items forward one for one, so walking the original list and taking the survivors in order
  puts every dropped item back on its own slot. The pairing needs the branch list below;
  when kipy's arithmetic no longer matches it, the shim appends instead — order is not
  semantic for a footprint's items, a lost item is;
* a ZERO rotation never reaches the setter at all — the rebuild is the damage, and a zero
  delta would rebuild for nothing. Equality is compared AFTER ``normalize180()``, because
  KiCad may store 270 deg where a DTO says -90 and ``Angle.__eq__`` compares RAW degrees
  (kipy/geometry.py:345).

SECOND REASON, the same module: kipy's rotation ARITHMETIC truncates. ``Vector2.rotate``
(kipy/geometry.py:127) answers ``int(dy*sin + pt_x*cos) + cx``, and ``int()`` TRUNCATES, so
a coordinate that is exactly representable comes back one nanometre away whenever the float
tail goes the wrong way (``cos(90deg) = 6.1e-17``). Measured live 15.10.2026: 27 footprints
reported ``lib_footprint_mismatch`` because their F.SilkS/F.Fab/F.CrtYd coordinates drifted
by 1 nm while every pad stayed byte-identical. The patch below is PERMANENT and installed at
import — not scoped to a call — because swapping a class method out and back is a race
between the worker thread and the UI, and because the exact rotation is strictly better for
every caller: same semantics, no lost nanometre.

THIRD REASON, the same module: the REFERENCE POINTS (KiCad 10.0.7+, see
``kipy_item_types.py`` and plan_2026_11_04_kipy_reference_point). kipy's position setter has
no arm for them and its orientation setter drops them the same way it drops a 3D model, so a
moved or rotated footprint used to leave all four of HP-Power-v099's points behind. They are
carried here BY THE SAME RULE as everything else the setter has no arm for: what
``KIPY_ORIENTATION_BRANCH_TYPES`` does not cover is our responsibility, and the arithmetic is
the ONE exact rotation above (never a second copy of it). Measured live 04.11.2026: a point sits
(+5.45, -2.33) mm from its TO-247 and (+2.54, -3.15) mm from its TO-220 — the same offset for
the same body, so a point is rigidly attached to its footprint exactly like a pad.

REMOVAL CONDITIONS — FOUR, and each has its own canary:
1. the item drop: ``test_raw_kipy_orientation_setter_still_drops_the_3d_model`` in
   tests/kicad/test_adapter_orientation_items.py. When IT goes red, delete this module and
   move the sync back into the adapter (where it lived before Д-2 of the same plan), setting
   the angle inline with the plain ``kipy_fp.orientation = angle``;
2. the truncation: ``test_the_original_kipy_rotate_truncates`` in
   tests/kicad/test_kipy_vector_rotate.py. When IT goes red, kipy rounds by itself — delete
   the patch block below and the cell with it (the item guard may still be needed);
3. the point's MOVE: ``test_raw_kipy_position_setter_does_not_move_the_reference_point`` in
   tests/kicad/test_adapter_reference_point_geometry.py. When IT goes red, kipy's own position
   setter carries the point — delete ``_move_reference_points`` and its call;
4. the point's ROTATION: ``test_raw_kipy_orientation_setter_drops_the_reference_point`` in the
   same file. When IT goes red, kipy's own orientation setter rotates the point — delete
   ``_rotate_reference_points`` and its call.
"""
import logging
import math

from kipy.board_types import BoardShape, BoardText, Field, Pad, Zone
from kipy.geometry import Vector2 as KipyVector2, Angle

from ..domain.board import Footprint, layer_to_kipy
from ..i18n import _
from .kipy_item_types import is_reference_point, unknown_items

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# The rotation patch (permanent on purpose — Ч2 of the plan)
# --------------------------------------------------------------------------------------

#: Attribute marking the replacement installed below, so a reload (or a second import of a
#: copy of this module) cannot wrap the wrapper twice.
_EXACT_ROTATE_MARKER = "_kicadstamp_exact_rotate"

#: kipy's OWN ``Vector2.rotate``, bound on the first import and kept for the canary cell
#: (tests/kicad/test_kipy_vector_rotate.py), which must measure kipy and never this module.
#: NOT initialised here on purpose: this module's body re-runs on ``importlib.reload``, and
#: re-binding the name would either lose the original or capture our own replacement.


def _rotate_exact(self, angle: Angle, center: KipyVector2) -> KipyVector2:
    """Exact replacement for kipy's ``Vector2.rotate`` (kipy/geometry.py:127).

    Same signature and the same semantics as the original — mutates in place and returns
    ``self`` — with the arithmetic repaired. kipy computes ``int(dy*sin + dx*cos) + cx`` and
    ``int()`` truncates: with ``cos(90 deg) = 6.1e-17`` and ``dx = -146267 nm`` the sum is
    959999.999999999988, which ``int()`` answers as 959999. Multiples of 90 degrees need no
    trigonometry at all — integer swaps and sign flips — and every other angle is the very
    same formula with ``round()`` instead of ``int()``.
    """
    dx = self.x - center.x
    dy = self.y - center.y
    degrees = float(angle.degrees) % 360.0

    if degrees == 0.0:
        return self
    if degrees == 90.0:
        self.x = dy + center.x
        self.y = -dx + center.y
        return self
    if degrees == 180.0:
        self.x = -dx + center.x
        self.y = -dy + center.y
        return self
    if degrees == 270.0:
        self.x = -dy + center.x
        self.y = dx + center.y
        return self

    radians = math.radians(degrees)
    sin_angle = math.sin(radians)
    cos_angle = math.cos(radians)
    self.x = round(dy * sin_angle + dx * cos_angle) + center.x
    self.y = round(dy * cos_angle - dx * sin_angle) + center.y
    return self


if not getattr(KipyVector2.rotate, _EXACT_ROTATE_MARKER, False):
    # First import: capture kipy's own implementation (the canary needs it) and install the
    # replacement. A RELOAD lands on the already-marked function and skips both lines, which
    # is what keeps `_KIPY_VECTOR2_ROTATE` pointing at kipy rather than at ourselves.
    _KIPY_VECTOR2_ROTATE = KipyVector2.rotate
    setattr(_rotate_exact, _EXACT_ROTATE_MARKER, True)
    KipyVector2.rotate = _rotate_exact


# The branches kipy's orientation setter knows how to rotate — board_types.py:2138-2164,
# where `updated_items` is filled from exactly Field/Pad/BoardText/Zone/BoardShape. Used
# ONLY to pair the setter's own list with the original one when rebuilding the ORDER; what
# gets restored is decided by identity alone (see the module docstring), so a stale list here
# can cost order, never an item. tests/kicad/test_adapter_orientation_items.py pins the list
# against kipy's real behaviour (the drift canary).
KIPY_ORIENTATION_BRANCH_TYPES = (Field, Pad, BoardText, Zone, BoardShape)


#: (ref, type) pairs already reported as "stayed where they were", ONE line per pair for the
#: whole process — the GUI moves footprints on every tick of a redraw, and a line per move
#: would bury the Log exactly like the 2026-08-06 selection counter did.
_STATIONARY_REPORTED: set[tuple[str, str]] = set()


def _definition_items(kipy_fp) -> list:
    """The footprint's definition items, or ``[]`` when the object has none.

    A stand-in on a bench (and several tests' doubles) has no ``definition`` at all, and the
    sync is called with whatever a caller passes: inventing an ``AttributeError`` for an object
    that simply has nothing to carry would be this module causing the very crash it prevents.
    A REAL kipy footprint always answers a LIST — ``Footprint.items`` returns
    ``self._unwrapped_items`` (kipy/board_types.py:2019), never a sequence of another kind.
    """
    items = getattr(getattr(kipy_fp, "definition", None), "items", None)
    return items if isinstance(items, list) else []


def _move_reference_points(kipy_fp, delta: KipyVector2) -> int:
    """Carry the footprint's reference points by ``delta`` — the move kipy's setter misses.

    The rule is the module's own: what ``KIPY_ORIENTATION_BRANCH_TYPES`` does not cover, the
    setter does not move, so WE do. A point is geometry, not an opaque blob: its position is
    absolute (plan section 1) and it sits at a fixed offset from its footprint, measured live on
    two TO-247s (+5.45, −2.33 mm) and two TO-220s (+2.54, −3.15 mm).
    """
    moved = 0
    for item in _definition_items(kipy_fp):
        if is_reference_point(item) and not isinstance(item, KIPY_ORIENTATION_BRANCH_TYPES):
            item.position = item.position + delta
            moved += 1
    return moved


def _rotate_reference_points(items, delta_degrees: float, center) -> int:
    """Rotate the given reference points around the footprint's position by ``delta_degrees``.

    Same arithmetic as every other item: ``_rotate_exact``, which is the patched
    ``Vector2.rotate`` — never a second copy of the formula. A private ``Vector2`` is built
    because the wrapper hands out a COPY of the proto, so mutating it in place would be lost.
    """
    rotated = 0
    for item in items:
        if not is_reference_point(item):
            continue
        position = item.position
        _rotate_exact(position, Angle.from_degrees(delta_degrees), center)
        item.position = position
        rotated += 1
    return rotated


def _report_stationary_unknown_items(ref: str, kipy_fp) -> None:
    """ONE Log line per (footprint, unknown type) for the whole process (Р4 of the plan)."""
    for type_name, count in unknown_items(kipy_fp).items():
        key = (ref, type_name)
        if key in _STATIONARY_REPORTED:
            continue
        _STATIONARY_REPORTED.add(key)
        logger.warning(_("{ref}: {count} item(s) of an unknown type ({type}) stayed where they "
                         "were; update kicad-python").format(
                             ref=ref, count=count, type=type_name))


def set_footprint_orientation(kipy_fp, angle: Angle) -> int:
    """Set ``kipy_fp.orientation`` without losing the definition items kipy's setter drops.

    A drop-in replacement for ``kipy_fp.orientation = angle`` (an ``Angle`` is required,
    exactly as in the raw setter — a float raises ``TypeError`` there too). Returns the
    number of items put back, so a caller can report a real loss instead of staying silent:
    0 means the setter kept everything, or that no rotation was needed at all.
    """
    target = Angle.from_degrees(angle.degrees).normalize180()
    current = Angle.from_degrees(kipy_fp.orientation.degrees).normalize180()
    if target == current:
        return 0

    # kipy's OWN delta (board_types.py:2126): target minus the RAW current angle. Computed
    # here, before the setter overwrites the orientation, and used below for the reference
    # points so they turn by exactly what the pads turn by.
    raw_current_degrees = kipy_fp.orientation.degrees

    before = list(kipy_fp.definition.items)
    # A private copy: kipy's setter normalizes the Angle it is handed IN PLACE, and the
    # caller's object must not be mutated behind its back.
    kipy_fp.orientation = Angle.from_degrees(angle.degrees)
    after = list(kipy_fp.definition.items)

    survivors = {id(item) for item in after}
    missing = [item for item in before
               if not isinstance(item, BoardShape) and id(item) not in survivors]
    if not missing:
        return 0

    # The setter hands its branch items back one for one, so equal arithmetic means each
    # survivor can be paired with its own slot and the dropped items land back on theirs.
    # Any other arithmetic means kipy's chain moved (or a shape branch vanished): the
    # setter's own list stays the truth and what is gone is appended. Order is not semantic
    # for a footprint's items; losing one is, so the fallback keeps every item.
    expected_survivors = sum(1 for item in before
                             if isinstance(item, KIPY_ORIENTATION_BRANCH_TYPES))
    if len(after) == expected_survivors:
        pool = iter(after)
        kipy_fp.definition.items = [
            next(pool) if isinstance(item, KIPY_ORIENTATION_BRANCH_TYPES) else item
            for item in before
        ]
    else:
        kipy_fp.definition.items = after + missing

    # The dropped items are back on their slots but STAND UNROTATED (restoring an item is not
    # the same as moving it). The reference points are the ones whose geometry we know, so
    # they turn here, by the very delta the setter turned the pads by, about the same centre.
    _rotate_reference_points(missing, target.degrees - raw_current_degrees, kipy_fp.position)
    return len(missing)


def sync_dto_to_kipy(dto, kipy_item) -> int:
    """Copy consumer-side DTO mutations back onto the live kipy object.

    Moved out of the adapter (Д-2 of plan_2026_10_15_footprint_3d_model_loss) because it IS
    the seam this module exists for: ``adapter.py`` keeps a one-line call and no copy of the
    rule. Only ``Footprint`` is mutated by consumers before ``update_items``
    (move_executor.py sets ``position``/``angle_deg``; undo.py sets ``position``/``angle_deg``).
    Via/Track DTOs are created, not mutated; field writes (``set_field_value``) mutate the
    kipy object in place.

    Returns the number of definition items ``set_footprint_orientation`` restored, so the
    caller can report a real loss instead of staying silent (0 for any other DTO type).
    """
    if not isinstance(dto, Footprint):
        return 0
    target = KipyVector2.from_xy(dto.position.x, dto.position.y)
    # The delta has to be taken BEFORE the setter runs — the same delta kipy's own setter
    # uses internally (board_types.py:2096) and the one the reference points need.
    delta = target - kipy_item.position
    kipy_item.position = target
    _move_reference_points(kipy_item, delta)
    restored = set_footprint_orientation(kipy_item, Angle.from_degrees(dto.angle_deg))
    # Anything of a type this kipy does not know has no geometry here, so it stayed where it
    # was; say so once per footprint and type instead of silently leaving it behind (Р4).
    _report_stationary_unknown_items(dto.ref, kipy_item)
    kipy_item.layer = layer_to_kipy(dto.layer)
    return restored
