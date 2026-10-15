"""probe_footprint_definition_item_types.py — census of FootprintInstance.definition.items
on the LIVE board, plus a LOCAL measurement of which of those types kipy's orientation
setter throws away (Ч0-1 of plan_2026_10_15_footprint_3d_model_loss).

Input:
    Optional reference designators to list in detail (default: every footprint that
    loses something). Example: U3 C4 J1.

Expected:
    * a census — every item type found in any footprint definition, with the number of
      footprints carrying it and the total item count, marking the types OUTSIDE kipy's
      keep chain;
    * what the RAW kipy setter drops, measured on a LOCAL COPY of every footprint, at
      +90 deg and at 0 deg. A type lost at 0 deg is the bug on its own (the setter
      rebuilds the item list even when the angle does not change);
    * the footprints that lose items, by refdes and lost type name.

Live KiCad:
    Yes — a running KiCad with the target board open. READ-ONLY: this probe never calls
    update_items / create_items / begin_commit, so nothing reaches the board. The one
    local side effect is ``fp.proto``, which repacks the local kipy object (what kipy
    itself does on any send) and hands out a snapshot; the SNAPSHOT, never the live
    object, is what gets rotated.

Why the snapshot trick: the instrument measures kipy, so it must not ask our own
workaround (kicadstamp/kicad/kipy_footprint_safety.py) what the setter keeps — it runs
kipy's raw setter on the real items of the real board instead.

Run:
    .venv/bin/python -m kicadstamp.diagnostics.probe_footprint_definition_item_types
    .venv/bin/python -m kicadstamp.diagnostics.probe_footprint_definition_item_types U3 C4
"""
import sys
from collections import Counter

from kipy import KiCad
from kipy.board_types import BoardShape, FootprintInstance
from kipy.geometry import Angle


# kipy's own keep chain, quoted so a reader sees the split without opening site-packages
# (kicad-python 0.8.0, kipy/board_types.py:2138-2164): the setter appends ONLY these
# types to its rebuilt list and assigns that list back to definition.items.
KIPY_SETTER_KEEPS = ("Field", "Pad", "BoardText", "Zone", "BoardShape")

# The rotation the field symptom was measured at (plan, step 2: the placer turns parts).
ROTATION_DEG = 90.0

# A non-orthogonal delta makes the setter replace a rectangle with a polygon (kipy
# board_types.py:2158) — a CLASS change, not a loss, so it is reported apart.
ORTHOGONAL_DEG = 90.0


def _ref(fp) -> str:
    """Reference designator of a live FootprintInstance, or a placeholder."""
    field = getattr(fp, "reference_field", None)
    text = getattr(field, "text", None)
    return getattr(text, "value", None) or "<no ref>"


def _type_names(items) -> list:
    return [type(item).__name__ for item in items]


def _branch(item) -> str:
    """Name of the SETTER BRANCH an item falls into.

    Every BoardShape subclass is handled by ONE branch (kipy board_types.py:2155), and
    that branch REPLACES the item with a freshly built concrete wrapper (a rectangle
    becomes BoardPolygon off the orthogonal angles), so neither a class-name diff nor an
    identity diff can tell "re-wrapped" from "dropped" — both would cry loss over a shape
    that is still there. Measured 15.10.2026: an item stored as BoardRectangle comes back
    from a proto snapshot as BoardShape, and after a 45 deg rotation as BoardPolygon.
    """
    if isinstance(item, BoardShape):
        return "BoardShape"
    return type(item).__name__


def _raw_setter_loss(fp, degrees: float):
    """(lost branch names, re-wrap note): what the RAW kipy setter drops on a COPY of fp.

    Counted per branch (see _branch): the setter mutates the items it keeps, so only a
    branch it has NO arm for can lose items — which is exactly the property the
    workaround exists for.
    """
    try:
        probe = FootprintInstance(proto=fp.proto)
    except Exception as exc:                       # a proto we cannot copy is a finding
        return [f"<snapshot failed: {type(exc).__name__}: {exc}>"], None

    before = list(probe.definition.items)
    before_branches = Counter(_branch(item) for item in before)
    before_classes = {type(item).__name__ for item in before}

    try:
        probe.orientation = Angle.from_degrees(probe.orientation.degrees + degrees)
    except Exception as exc:
        # The setter rotates each kept item BEFORE rebuilding the list, so an item it
        # cannot rotate dies there and takes the whole call with it (measured 15.10.2026:
        # a default-constructed Zone has no outline polygons — kipy board_types.py:2519).
        # Reported, never fatal: one odd item must not stop the census.
        return [], f"<setter raised: {type(exc).__name__}: {exc}>"

    after_branches = Counter(_branch(item) for item in probe.definition.items)
    lost = []
    for name, count in (before_branches - after_branches).items():
        lost.extend([name] * count)

    note = None
    if degrees % ORTHOGONAL_DEG != 0:
        added = sorted({type(item).__name__ for item in probe.definition.items}
                       - before_classes)
        if added:
            note = "re-wrapped: " + ", ".join(added)
    return sorted(lost), note


def main(argv: list) -> int:
    wanted = {arg.upper() for arg in argv[1:]}

    kicad = KiCad()
    board = kicad.get_board()
    if board is None:
        print("No board is open in KiCad — open the target board and re-run.")
        return 1

    footprints = board.get_footprints()
    print(f"Footprints on the open board: {len(footprints)}")

    # --- census -----------------------------------------------------------------
    census = Counter()
    carriers = Counter()
    for fp in footprints:
        names = _type_names(fp.definition.items)
        census.update(names)
        for name in set(names):
            carriers[name] += 1

    print("\n--- definition.items census (all footprints) ---")
    print(f"{'type':24} {'items':>6} {'footprints':>11}")
    for name, count in census.most_common():
        mark = "" if name in KIPY_SETTER_KEEPS else "   <-- kipy setter DROPS this type"
        print(f"{name:24} {count:6} {carriers[name]:11}{mark}")
    if not census:
        print("  (no items at all — is the right board open?)")

    with_models = sum(
        1 for fp in footprints
        if any(type(item).__name__ == "Footprint3DModel" for item in fp.definition.items)
    )
    print(f"\nFootprints carrying at least one 3D model: {with_models} of {len(footprints)}")

    # --- what the raw setter does to a copy -------------------------------------
    print("\n--- what the RAW kipy setter drops, measured on a local copy of each footprint ---")
    print("(nothing here is sent to KiCad; +%.0f deg and 0 deg are both measured)"
          % ROTATION_DEG)

    lost_at_zero = Counter()
    lost_at_rotation = Counter()
    victims = []
    for fp in footprints:
        lost_zero, _ = _raw_setter_loss(fp, 0.0)
        lost_rot, note = _raw_setter_loss(fp, ROTATION_DEG)
        lost_at_zero.update(lost_zero)
        lost_at_rotation.update(lost_rot)
        if lost_zero or lost_rot or note:
            victims.append((_ref(fp), lost_zero, lost_rot, note))

    print(f"  at 0 deg:      {dict(lost_at_zero) or 'nothing lost'}")
    print(f"  at +{ROTATION_DEG:.0f} deg:     {dict(lost_at_rotation) or 'nothing lost'}")
    print(f"  footprints losing at least one item: {len(victims)} of {len(footprints)}")

    print("\n--- detail: footprints that lose items ---")
    shown = 0
    for ref, lost_zero, lost_rot, note in victims:
        if wanted and ref.upper() not in wanted:
            continue
        shown += 1
        parts = [f"{ref}: at 0 deg -> {lost_zero or 'nothing'}",
                 f"at +{ROTATION_DEG:.0f} deg -> {lost_rot or 'nothing'}"]
        if note:
            parts.append(note)
        print("  " + "; ".join(parts))
    if wanted and not shown:
        print(f"  (none of the requested refs lost anything: {', '.join(sorted(wanted))})")
    elif not wanted and len(victims) > 10:
        print("  (only the first 10 are listed; pass refdes arguments for the rest)")

    # --- verdict ----------------------------------------------------------------
    print("\n--- verdict ---")
    if lost_at_zero:
        print("BUG PRESENT (worst case): the setter drops items even at 0 deg ->")
        print(f"  {dict(lost_at_zero)}")
        print("  meaning: ANY update_items() through the adapter loses them, even a pure move.")
    elif lost_at_rotation:
        print("BUG PRESENT: the setter drops items when the angle changes ->")
        print(f"  {dict(lost_at_rotation)}")
    else:
        print("NOT REPRODUCED on this board: the kipy setter kept every item.")
        print("  If that is really so, upstream fixed it — the workaround in")
        print("  kicadstamp/kicad/kipy_footprint_safety.py can go.")

    lost_types = sorted(set(lost_at_zero) | set(lost_at_rotation))
    outside = [name for name in lost_types if name not in KIPY_SETTER_KEEPS]
    if outside:
        print("\nTypes the workaround must restore (outside kipy's keep chain):")
        for name in outside:
            print(f"  {name}: {census.get(name, 0)} item(s), "
                  f"{carriers.get(name, 0)} footprint(s)")
    print("\nNothing was sent to KiCad: this probe only read the board.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
