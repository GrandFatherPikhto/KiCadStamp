"""probe_reference_point_write_2026_11_04.py — WRITE probe, Т2 of plan_2026_11_04_kipy_reference_point.

WHAT THIS IS. The one instrument in this repo that WRITES a board ON PURPOSE: it asks KiCad 10.0.8
what the IPC API lets us do with a ``ReferencePoint`` — create it, move it, commit+undo it, delete
it, and (optionally) put one inside a footprint. It is a MEASUREMENT, not a product: whether any of
this becomes a KiCadStamp feature (a tree-start marker, a cell anchor, "take the coordinates from
the point") is Denis's decision on this report.

WHY IT IS SAFE. It works on exactly ONE board — ``Test02.kicad_pcb`` — and the name check is the
FIRST thing it does after connecting, before a single write:

* any other board (HP-Power-v099 above all) → a refusal and ``SystemExit(1)``, nothing written;
* everything it creates it also deletes, and it tries again in ``finally``;
* the UUID of every created point is PRINTED as it is minted, so a run that dies badly leaves
  Denis a name to remove by hand.

WHO RUNS IT. Denis, with Test02 open in KiCad (like ``repro_3``/``repro_5`` of
``techdocs/bugreport/2026_10_15_footprint_update/plan.md``). The offline half — ``--self-check`` —
needs no KiCad at all and is what the author of this probe verifies.

THE CELLS (each prints «what we sent / what came back»):

  1. create a board point (F.Fab, inside the outline, size 1 mm) — does KiCad mint a UUID, do
     position/layer/size come back as sent, does ``GetItems(56)`` see it;
  2. move it by +1 mm on X (``update_items``) — exactly 1 000 000 nm, same UUID, other fields intact;
  3. ROTATION: there is no angle field to set — the message's own field list is printed, so
     «rotate a point» can only ever mean «rotate its POSITION around something»;
  4. one ``begin_commit``/``push_commit`` around create+move — Denis presses Ctrl+Z ONE time and
     the probe reports what an undo of a whole transaction really takes with it;
  5. ``remove_items`` — ``GetItems(56)`` no longer returns it;
  6. ``--in-footprint REF`` — a point appended to ``definition.items`` of a footprint on Test02:
     does it appear, is ``parent`` the footprint's KIID, and does «Update Footprint from Library»
     drop it (Denis watches the editor; the probe waits for Enter and re-reads);
  7. the NAME of KiCad object type 56 — kicad-python 0.8.0 has no member for it and
     ``KiCadObjectType.Name(56)`` raises, so the raw number is used and that fact is printed.

Run (KiCad open on ~/Projects/KiCad/Tests/Test02/Test02.kicad_pcb):
    .venv/bin/python -m kicadstamp.diagnostics.probe_reference_point_write_2026_11_04
    ... --at 30 40            # a point position in mm, when the outline centre is not where you want it
    ... --in-footprint R1     # cell 6
    ... --self-check          # offline, no KiCad, nothing written anywhere
"""
from __future__ import annotations

import argparse
import sys
import traceback

from kipy.geometry import Vector2 as KipyVector2
from kipy.proto.board import board_types_pb2
from kipy.proto.common.commands.editor_commands_pb2 import GetItems, GetItemsResponse
from kipy.proto.common.types.base_types_pb2 import KIID

from kicadstamp.kicad import kipy_item_types

#: THE safety rule of this probe, in one place: one board, named explicitly. Test02 is Denis's
#: scratch board; everything else he owns is out of bounds for a probe that writes.
EXPECTED_BOARD = "Test02.kicad_pcb"
EXPECTED_PATH_HINT = "~/Projects/KiCad/Tests/Test02/"

#: KiCad's object type of a board point. kicad-python 0.8.0 has NO member for it
#: (``KiCadObjectType.Name(56)`` raises ``ValueError``), so it only exists as a number — and that
#: number is the one the read-only probe of Т1 measured. Imported from there rather than typed a
#: second time: one measurement, one place (rule 44).
from kicadstamp.diagnostics.probe_reference_point_2026_11_04 import (  # noqa: E402
    BOARD_POINT_TYPE_CODE,
)

#: The move of cell 2, and the size of every point this probe creates (1 mm — visible on F.Fab).
MOVE_DX_NM = 1_000_000
POINT_SIZE_NM = 1_000_000

#: Commit message of cell 4 — it is what Denis sees in the undo history.
COMMIT_MESSAGE = "KiCadStamp probe: reference point create+move"


# ---------------------------------------------------------------------------------------
# The guard, and the offline half
# ---------------------------------------------------------------------------------------

def board_name_allowed(name: str | None) -> bool:
    """True only for THE board this probe is allowed to write. Nothing else is a judgement call."""
    return name == EXPECTED_BOARD


def _self_check() -> int:
    """The offline half: the schema, the wrapper, and the guard — no KiCad, nothing written.

    Deliberately exercises the SAME functions the live cells use (``_make_point``,
    ``board_name_allowed``), so a green self-check says something about the run to come.
    """
    print("=== SELF-CHECK (offline, no KiCad)")
    failures: list[str] = []

    fields = list(kipy_item_types.reference_point_class().DESCRIPTOR.fields)
    names = [field.name for field in fields]
    print(f"ReferencePoint fields: {names}")
    if "angle" in names or "orientation" in names:
        failures.append("the message HAS an angle field — cell 3's premise is wrong")
    print(f"cell 3 premise: no angle field -> {'ok' if not failures else 'FAIL'}")

    point = _make_point(10_000_000, 20_000_000)
    if point.layer != board_types_pb2.BoardLayer.BL_F_Fab:
        failures.append(f"layer is {point.layer}, expected BL_F_Fab")
    if (point.position.x, point.position.y) != (10_000_000, 20_000_000):
        failures.append(f"position is {point.position}, expected (10 000 000, 20 000 000)")
    if point.size_nm != POINT_SIZE_NM:
        failures.append(f"size is {point.size_nm}, expected {POINT_SIZE_NM}")
    packed = point.proto.SerializeToString()
    reparsed = kipy_item_types.reference_point_class()()
    reparsed.ParseFromString(packed)
    if reparsed.SerializeToString() != packed:
        failures.append("parse+serialize changed the bytes")
    if not kipy_item_types.is_reference_point(point):
        failures.append("is_reference_point() says no about our own wrapper")
    print(f"built point: layer={board_types_pb2.BoardLayer.Name(point.layer)} "
          f"pos={point.position} size_nm={point.size_nm} bytes={len(packed)}")

    print("\nthe guard (this is what keeps every other board safe):")
    for name, expected in (
        (EXPECTED_BOARD, True),
        ("HP-Power-v099.kicad_pcb", False),
        ("3CH-AWG-TIA-v103.kicad_pcb", False),
        ("Test02-OLD.kicad_pcb", False),
        (None, False),
        ("", False),
    ):
        got = board_name_allowed(name)
        print(f"  {name!r:32s} -> {got} (expected {expected})")
        if got != expected:
            failures.append(f"board_name_allowed({name!r}) answered {got}")

    print(f"\nRESULT: {'all of it passed' if not failures else 'FAILED'}")
    for failure in failures:
        print(f"  - {failure}")
    return 0 if not failures else 2


# ---------------------------------------------------------------------------------------
# Building and reading a point
# ---------------------------------------------------------------------------------------

def _make_point(x_nm: int, y_nm: int, size_nm: int = POINT_SIZE_NM):
    """A ``ReferencePointItem`` ready for ``create_items`` — F.Fab, no net, no copper."""
    message = kipy_item_types.reference_point_class()()
    message.layer = board_types_pb2.BoardLayer.BL_F_Fab
    message.position.x_nm = x_nm
    message.position.y_nm = y_nm
    message.size.value_nm = size_nm
    return kipy_item_types.ReferencePointItem(proto=message)


def _read_points(board) -> list:
    """Every board-level point KiCad has right now — the raw answer, parsed by OUR class.

    The request is the one the T1 probe already uses (``GetItems`` with the bare type code 56);
    ``kipy.board_types.unwrap`` would have to know the type, which is the whole point of Т1.
    """
    command = GetItems()
    command.header.document.CopyFrom(board.document)
    command.types.append(BOARD_POINT_TYPE_CODE)
    response = board.client.send(command, GetItemsResponse)
    points = []
    for raw in response.items:
        message = kipy_item_types.reference_point_class()()
        message.ParseFromString(raw.value)
        points.append(message)
    return points


def _describe(message) -> str:
    layer = board_types_pb2.BoardLayer.Name(message.layer)
    parent = message.parent.value if message.HasField("parent") else "<unset>"
    uuid = message.id.value or "<unset>"
    return (f"id={uuid} layer={layer} pos=({message.position.x_nm}, {message.position.y_nm}) "
            f"size={message.size.value_nm} locked={message.locked} parent={parent}")


def _find(points, uuid: str):
    return next((point for point in points if point.id.value == uuid), None)


def _report(what: str, sent, got) -> None:
    print(f"  {what}:")
    print(f"    sent: {sent}")
    print(f"    got : {got}")


# ---------------------------------------------------------------------------------------
# The live cells
# ---------------------------------------------------------------------------------------

def _inside_position(board, override) -> tuple[int, int] | None:
    """A position inside the board outline, in nm — or None when the outline cannot be read.

    APPROXIMATION, stated as one: the average of the Edge.Cuts bounding boxes' centres. On a
    rectangular test board that is the board centre; on a weird outline it may be outside the
    copper, which is why ``--at MM MM`` exists. A true point-in-polygon test is not worth
    building for a probe whose operator is watching the screen.
    """
    if override is not None:
        return (int(round(override[0] * 1_000_000)), int(round(override[1] * 1_000_000)))
    try:
        shapes = [shape for shape in board.get_shapes()
                  if shape.layer == board_types_pb2.BoardLayer.BL_Edge_Cuts]
        if not shapes:
            return None
        boxes = board.get_item_bounding_box(shapes)
        centres = [box.center() for box in boxes]
        return (sum(int(c.x) for c in centres) // len(centres),
                sum(int(c.y) for c in centres) // len(centres))
    except Exception as exc:                                    # noqa: BLE001 — a courtesy read
        print(f"  outline read failed: {type(exc).__name__}: {exc}")
        return None


def _cell_1_create(board, position, created_ids: set) -> None:
    print("\n=== CELL 1 — create a point on the board (create_items)")
    x_nm, y_nm = position
    sent = _make_point(x_nm, y_nm)
    print(f"  sending: layer=BL_F_Fab pos=({x_nm}, {y_nm}) size={POINT_SIZE_NM}")
    created = board.create_items(sent)
    if not created:
        print("  RESULT: KiCad returned NOTHING — create did not work")
        return
    item = created[0]
    print(f"  returned wrapper: {item!r}")
    uuid = item.id.value
    print(f"  UUID minted by KiCad: {uuid or '<EMPTY — nothing to clean up by name>'}")
    if uuid:
        created_ids.add(uuid)
    _report("as sent / as created",
            f"pos=({x_nm}, {y_nm}) size={POINT_SIZE_NM}",
            f"pos={item.position} size={getattr(item, 'size_nm', '?')}")
    points = _read_points(board)
    print(f"  GetItems({BOARD_POINT_TYPE_CODE}) now returns {len(points)} point(s)")
    found = _find(points, uuid) if uuid else None
    print(f"  RESULT: {'WORKS' if found is not None else 'does NOT work'} — "
          f"{_describe(found) if found is not None else 'the new point is not in the answer'}")


def _cell_2_move(board, uuid: str) -> None:
    print("\n=== CELL 2 — move it by +1 mm on X (update_items)")
    if not uuid:
        print("  SKIPPED: cell 1 minted no UUID")
        return
    before = _find(_read_points(board), uuid)
    if before is None:
        print("  SKIPPED: the point is not on the board")
        return
    points = board.get_items_by_id([KIID(value=uuid)])
    if not points:
        print("  SKIPPED: get_items_by_id does not answer for this UUID")
        return
    item = points[0]
    target_x = before.position.x_nm + MOVE_DX_NM
    item.position = KipyVector2.from_xy(target_x, before.position.y_nm)
    board.update_items([item])
    after = _find(_read_points(board), uuid)
    if after is None:
        print("  RESULT: the point DISAPPEARED after update_items")
        return
    moved = after.position.x_nm - before.position.x_nm
    print(f"  before: {_describe(before)}")
    print(f"  after : {_describe(after)}")
    print(f"  moved by {moved} nm (asked {MOVE_DX_NM} nm) — "
          f"{'EXACT' if moved == MOVE_DX_NM else 'NOT exact'}")
    same = (after.id.value == before.id.value and after.layer == before.layer
            and after.size.value_nm == before.size.value_nm
            and after.locked == before.locked
            and (after.parent.value if after.HasField('parent') else '')
            == (before.parent.value if before.HasField('parent') else ''))
    print(f"  everything else unchanged: {same}")
    print(f"  RESULT: {'WORKS' if moved == MOVE_DX_NM and same else 'does NOT work as asked'}")


def _cell_3_no_angle() -> None:
    print("\n=== CELL 3 — rotating a point: there is no angle to set")
    names = [field.name for field in kipy_item_types.reference_point_class().DESCRIPTOR.fields]
    print(f"  ReferencePoint fields: {names}")
    print("  RESULT: " + ("no `angle`/`orientation` field — «rotate the point» can only mean "
                          "«rotate its POSITION around something»"
                          if not {"angle", "orientation"} & set(names)
                          else "the message DOES carry an angle — this cell's premise is wrong"))


def _cell_4_commit_undo(board, position, created_ids: set) -> None:
    print("\n=== CELL 4 — one transaction (begin_commit … push_commit) and ONE Ctrl+Z")
    x_nm, y_nm = position
    commit = board.begin_commit()
    item = _make_point(x_nm, y_nm)
    created = board.create_items(item)
    if not created:
        board.drop_commit(commit)
        print("  create inside the transaction returned nothing — commit dropped, SKIPPED")
        return
    uuid = created[0].id.value
    if uuid:
        created_ids.add(uuid)
    created[0].position = KipyVector2.from_xy(x_nm + MOVE_DX_NM, y_nm)
    board.update_items([created[0]])
    board.push_commit(commit, COMMIT_MESSAGE)
    print(f"  transaction pushed: create + move, message {COMMIT_MESSAGE!r}")
    print(f"  UUID of the point inside it: {uuid or '<EMPTY>'}")
    before = _find(_read_points(board), uuid) if uuid else None
    print(f"  before undo: {_describe(before) if before is not None else 'not on the board'}")
    print("\n  >>> PRESS Ctrl+Z IN KiCad ONCE NOW (do not touch anything else), then press Enter here.")
    try:
        input("  >>> [Enter] after ONE Ctrl+Z: ")
    except EOFError:
        print("  (no terminal — continuing without waiting)")
    after = _find(_read_points(board), uuid) if uuid else None
    if after is None:
        print("  RESULT: the point is GONE — ONE undo took the whole transaction (create+move)")
    else:
        print(f"  RESULT: the point is still there — the undo took only part of it: "
              f"{_describe(after)}")
        print(f"          (it was created at x={x_nm}, moved to x={x_nm + MOVE_DX_NM}; "
              f"what survived says which step the undo covered)")


def _cell_5_delete(board, uuid: str) -> None:
    print("\n=== CELL 5 — delete it (remove_items)")
    if not uuid:
        print("  SKIPPED: no UUID")
        return
    points = board.get_items_by_id([KIID(value=uuid)])
    if not points:
        print("  SKIPPED: get_items_by_id does not answer for this UUID")
        return
    board.remove_items(points)
    after = _find(_read_points(board), uuid)
    print(f"  RESULT: {'WORKS — GetItems(56) no longer returns it' if after is None else 'STILL THERE'}")


def _footprint_by_ref(board, ref: str):
    for footprint in board.get_footprints():
        if footprint.reference_field and footprint.reference_field.text.value == ref:
            return footprint
    return None


def _cell_6_in_footprint(board, ref: str, created_ids: set) -> None:
    print(f"\n=== CELL 6 — a point INSIDE the footprint {ref} (update_items of the footprint)")
    footprint = _footprint_by_ref(board, ref)
    if footprint is None:
        print(f"  SKIPPED: no footprint {ref!r} on this board")
        return
    origin = footprint.position
    point = _make_point(int(origin.x), int(origin.y) + 2_000_000)
    before_count = len(footprint.definition.items)
    footprint.definition.items = list(footprint.definition.items) + [point]
    board.update_items([footprint])
    reread = _footprint_by_ref(board, ref)
    points = [item for item in reread.definition.items
              if kipy_item_types.is_reference_point(item)]
    print(f"  definition.items: {before_count} -> {len(reread.definition.items)}, "
          f"reference points in it: {len(points)}")
    if not points:
        print("  RESULT: the point did NOT end up inside the footprint")
        return
    uuid = points[-1].id.value
    if uuid:
        created_ids.add(uuid)
    print(f"  read back: {_describe(points[-1].proto)}")
    print(f"  parent == footprint id: "
          f"{points[-1].proto.parent.value == reread.id.value if points[-1].proto.HasField('parent') else 'NO parent field set'}")
    print("\n  >>> NOW, IN KiCad: look at the point in the editor, then run «Update Footprint from")
    print("  >>> Library» on that footprint and press Enter here. The probe re-reads it.")
    try:
        input("  >>> [Enter] after Update Footprint from Library: ")
    except EOFError:
        print("  (no terminal — continuing without waiting)")
    reread = _footprint_by_ref(board, ref)
    still = [item for item in reread.definition.items
             if kipy_item_types.is_reference_point(item) and item.id.value == uuid]
    print(f"  after «Update Footprint from Library»: "
          f"{'STILL INSIDE' if still else 'GONE (KiCad rewrote the definition)'}")
    _remove_point_from_footprint(board, ref, uuid)


def _remove_point_from_footprint(board, ref: str, uuid: str) -> None:
    try:
        footprint = _footprint_by_ref(board, ref)
        if footprint is None:
            return
        kept = [item for item in footprint.definition.items
                if not (kipy_item_types.is_reference_point(item) and item.id.value == uuid)]
        if len(kept) == len(footprint.definition.items):
            return
        footprint.definition.items = kept
        board.update_items([footprint])
        print(f"  cleanup: removed the point {uuid} from {ref}'s definition")
    except Exception as exc:                                    # noqa: BLE001 — cleanup, never fatal
        print(f"  cleanup FAILED for {ref}/{uuid}: {type(exc).__name__}: {exc} — "
              f"remove it by hand")


def _delete_ids(board, ids: set) -> None:
    """Remove every point this run created — by UUID, so a half-broken run still cleans up."""
    if not ids:
        return
    print("\n=== CLEANUP")
    for uuid in list(ids):
        if not uuid:
            continue
        try:
            items = board.get_items_by_id([KIID(value=uuid)])
            if items:
                board.remove_items(items)
                print(f"  removed {uuid}")
            else:
                print(f"  {uuid} is already gone")
        except Exception as exc:                                # noqa: BLE001 — cleanup, never fatal
            print(f"  cleanup FAILED for {uuid}: {type(exc).__name__}: {exc} — "
                  f"remove it by hand in KiCad")
        finally:
            ids.discard(uuid)
    left = len(_read_points(board))
    print(f"  points left on the board now: {left} "
          f"(this board may have carried some before the run — the count is a reading, not a promise)")


# ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-check", action="store_true",
                        help="offline: schema, wrapper and the board-name guard — no KiCad")
    parser.add_argument("--at", nargs=2, type=float, metavar=("X_MM", "Y_MM"), default=None,
                        help="position of the created point, in mm (default: the outline centre)")
    parser.add_argument("--in-footprint", metavar="REF", default=None,
                        help="cell 6: also put a point INSIDE this footprint")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()

    from kipy import KiCad

    board = KiCad().get_board()
    if board is None:
        print(f"No board is open in KiCad — open {EXPECTED_PATH_HINT}{EXPECTED_BOARD} and re-run.")
        return 1
    print(f"board: {board.name!r}")
    if not board_name_allowed(board.name):
        print(f"REFUSING: this probe WRITES and works only on {EXPECTED_BOARD!r} "
              f"({EXPECTED_PATH_HINT}). Nothing was written.")
        return 1
    print("board accepted (this is the ONE board this probe may write)")

    position = _inside_position(board, args.at)
    if position is None:
        print("The board outline could not be read — pass --at X_MM Y_MM and re-run. "
              "Nothing was written.")
        return 1
    print(f"point position: {position[0]}, {position[1]} nm "
          f"({position[0] / 1e6:.3f}, {position[1] / 1e6:.3f} mm)")

    created_ids: set[str] = set()
    try:
        _cell_1_create(board, position, created_ids)
        first = next(iter(created_ids), "")
        _cell_2_move(board, first)
        _cell_3_no_angle()
        _cell_4_commit_undo(board, position, created_ids)
        for uuid in sorted(created_ids):
            _cell_5_delete(board, uuid)
            created_ids.discard(uuid)
        if args.in_footprint:
            _cell_6_in_footprint(board, args.in_footprint, created_ids)
    except Exception:                                           # noqa: BLE001 — a probe reports
        print("\n=== THE RUN DIED — traceback, then cleanup")
        traceback.print_exc()
    finally:
        _delete_ids(board, created_ids)
    print("\n=== DONE — every point this run created was deleted (see CLEANUP above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
