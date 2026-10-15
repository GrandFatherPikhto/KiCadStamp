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

REMOVAL CONDITION — the day kipy stops dropping items, delete this module and put the plain
``kipy_fp.orientation = angle`` back inline. The signal is the canary cell
``test_raw_kipy_orientation_setter_still_drops_the_3d_model`` in
tests/kicad/test_adapter_orientation_items.py going RED: it pins the UPSTREAM bug, not our
behaviour, and is meant to be deleted together with this file.
"""
from kipy.board_types import BoardShape, BoardText, Field, Pad, Zone
from kipy.geometry import Angle


# The branches kipy's orientation setter knows how to rotate — board_types.py:2138-2164,
# where `updated_items` is filled from exactly Field/Pad/BoardText/Zone/BoardShape. Used
# ONLY to pair the setter's own list with the original one when rebuilding the ORDER; what
# gets restored is decided by identity alone (see the module docstring), so a stale list here
# can cost order, never an item. tests/kicad/test_adapter_orientation_items.py pins the list
# against kipy's real behaviour (the drift canary).
KIPY_ORIENTATION_BRANCH_TYPES = (Field, Pad, BoardText, Zone, BoardShape)


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
    return len(missing)
