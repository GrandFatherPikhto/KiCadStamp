"""probe_reference_point_2026_11_04.py — the tracked probe of Ч0.5 / Т1 of
plan_2026_11_04_kipy_reference_point.

Two halves, ONE run:

1. OFFLINE (no KiCad, no board, no IPC) — does the descriptor-pool trick actually
   work: one hand-built ``FileDescriptorProto`` for ``kiapi.board.types.ReferencePoint``
   added to the DEFAULT pool kipy already uses, ``message_factory.GetMessageClass``
   on it, a parse+serialize round trip, and BOTH duplicate cases (the same file name
   again — accepted silently, per section 8.1 of the plan — and the same SYMBOL in a
   file with another name — ``TypeError: duplicate symbol``, the "kicad-python added
   the type by itself" case the shim must not walk into);
2. LIVE, READ-ONLY — a census of ``FootprintInstance.definition.items`` read as RAW
   protos (``GetItems`` + ``board_types_pb2``, never ``kipy.board_types.unwrap``, which
   is exactly what dies on this board), the four reference points with every field,
   the geometry each of them has relative to its footprint, the expected bytes of Q1,
   and the traceback of ``board.get_footprints()`` — the failure Ч0.1 named from the
   code alone.

WHY RAW PROTOS. ``Footprint.__init__`` unwraps every child of every footprint
(``kipy/board_types.py:1994``), so ``board.get_footprints()`` on a board carrying a
``ReferencePoint`` raises before a single footprint reaches the caller — including the
existing ``probe_footprint_definition_item_types.py``. This probe therefore sends the
very same command kipy sends and parses the ANSWER with kipy's own generated classes.

IT MEASURES KIPY, NOT US. Nothing here imports ``kicadstamp.kicad.adapter`` or
``kipy_item_types``: the shim installs itself on those imports, and an instrument that pulled
it in would report its own workaround as the library's behaviour. That is why the
``board.get_footprints()`` traceback at the end is EVIDENCE — it is what a bare kicad-python
0.8.0 does with this board, and it must keep doing it until upstream learns the type.

NOTHING IS WRITTEN TO THE BOARD: no update_items / create_items / begin_commit /
save. The only file this probe may write is the fixture module, and only when
``--write-fixture PATH`` is passed explicitly.

Run:
    .venv/bin/python -m kicadstamp.diagnostics.probe_reference_point_2026_11_04
    .venv/bin/python -m kicadstamp.diagnostics.probe_reference_point_2026_11_04 \\
        --write-fixture tests/kicad/reference_point_bytes.py

Exit codes: 0 — every live number matches section 8.1 of the plan;
1 — no board / wrong board / KiCad not reachable;
2 — the board answered but a number DIFFERS from 8.1 (the plan's СТОП).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import time
import traceback

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.any_pb2 import Any

from kipy.proto.board import board_types_pb2
from kipy.proto.common.commands.editor_commands_pb2 import GetItems, GetItemsResponse
from kipy.proto.common.types import KiCadObjectType
from kipy.proto.common.types.base_types_pb2 import LockedState

#: The board this probe is allowed to read — Денис's REAL board. Anything else is refused
#: rather than measured: the plan's numbers were taken on this one.
EXPECTED_BOARD = "HP-Power-v099.kicad_pcb"

REFERENCE_POINT_FULL_NAME = "kiapi.board.types.ReferencePoint"
REFERENCE_POINT_TYPE_URL = "type.googleapis.com/" + REFERENCE_POINT_FULL_NAME

#: The name OUR file carries in the descriptor pool. Deliberately NOT a KiCad file name: the
#: pool also holds the real ``board/board_types.proto``, and a name that looks like KiCad's
#: would collide the moment a newer kicad-python ships the same type upstream.
OUR_FILE_NAME = "kicadstamp/api10_board_types_reference_point.proto"

#: The two files already in the pool that define every type the message refers to. Measured
#: from kipy's own generated modules (``base_types_pb2.py:11``, ``board_types_pb2.py:11``) —
#: these are the SHORT names from the descriptor, not the ``api/proto/...`` source paths.
DEPENDENCIES = ("common/types/base_types.proto", "board/board_types.proto")

#: Appendix B of the plan's section 1, as numbers: id=1, layer=2, position=3, size=4,
#: locked=5, parent=6.
SCHEMA_FIELDS = (
    ("id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.KIID"),
    ("layer", 2, descriptor_pb2.FieldDescriptorProto.TYPE_ENUM, ".kiapi.board.types.BoardLayer"),
    ("position", 3, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.Vector2"),
    ("size", 4, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.Distance"),
    ("locked", 5, descriptor_pb2.FieldDescriptorProto.TYPE_ENUM, ".kiapi.common.types.LockedState"),
    ("parent", 6, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.KIID"),
)

#: Section 8.1 of the plan — the numbers Claude measured live. This probe EXISTS to reproduce
#: them independently, so a difference is a real finding and the run stops (exit code 2).
EXPECTED_CENSUS = {
    "Field": 175,
    "Pad": 216,
    "BoardGraphicShape": 795,
    "BoardText": 84,
    "Footprint3DModel": 80,
    "ReferencePoint": 4,
}
EXPECTED_FOOTPRINTS = 84
EXPECTED_VALUE_LEN = 102
#: ref -> (footprint nm, angle deg, point nm, sha256[:16])
EXPECTED_POINTS = {
    "Q1": ((163_155_000, 73_810_000), 0.0, (168_605_000, 71_480_000), "5e1fe60c236e1181"),
    "Q2": ((163_155_000, 118_500_000), 0.0, (168_605_000, 116_170_000), "41e4eabd83e34012"),
    "D4": ((186_190_000, 105_405_000), 0.0, (188_730_000, 102_255_000), "ca228f06f10e6dbe"),
    "D8": ((186_190_000, 109_955_000), 0.0, (188_730_000, 106_805_000), "5127fffacb53715b"),
}
EXPECTED_SIZE_NM = 2_000_000
EXPECTED_F_FAB = board_types_pb2.BoardLayer.BL_F_Fab
#: Read from the ENUM, never written as a literal: ``LockedState`` is LS_UNKNOWN=0,
#: LS_UNLOCKED=1, LS_LOCKED=2, so a hard-coded ``0`` here reads as "unknown" and made the
#: first run of this probe report four false mismatches (04.11.2026).
EXPECTED_UNLOCKED = LockedState.LS_UNLOCKED

#: The refdes the fixture is taken from (plan 8.2 п.1).
FIXTURE_REF = "Q1"

_MISMATCHES: list[str] = []


def _check(what: str, actual, expected) -> bool:
    """Compare one live number with 8.1, print, and remember a mismatch (exit code 2)."""
    if actual == expected:
        print(f"  OK   {what}: {actual}")
        return True
    print(f"  DIFF {what}: got {actual!r}, plan 8.1 says {expected!r}")
    _MISMATCHES.append(what)
    return False


# ---------------------------------------------------------------------------------------
# Офлайн-часть: файл-дескриптор, пул, GetMessageClass, оба случая дубля
# ---------------------------------------------------------------------------------------

def build_file_proto() -> descriptor_pb2.FileDescriptorProto:
    """The ``ReferencePoint`` message of the plan's section 1, as a FileDescriptorProto.

    Assembled in memory on purpose: shipping a ``.proto`` file in this repo would mean
    shipping a protoc step (and a build dependency) for six fields of one message.
    """
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = OUR_FILE_NAME
    file_proto.package = "kiapi.board.types"
    file_proto.syntax = "proto3"
    file_proto.dependency.extend(DEPENDENCIES)

    message = file_proto.message_type.add()
    message.name = "ReferencePoint"
    for name, number, field_type, type_name in SCHEMA_FIELDS:
        field = message.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type
        field.type_name = type_name
    return file_proto


def _find_existing(pool) -> object | None:
    """The pool's own type descriptor, or None when nobody defined the message yet.

    THE ORDER MATTERS (plan 7.2): ``AddSerializedFile`` refusing a duplicate SYMBOL is what
    happens only when the symbol arrives in a file with ANOTHER name, so asking the pool
    first is the one check that works for both "kipy learned the type" and "we ran twice".
    """
    try:
        return pool.FindMessageTypeByName(REFERENCE_POINT_FULL_NAME)
    except KeyError:
        return None


def offline_section() -> type | None:
    """Prove the pool route works, and print what both duplicate cases really do."""
    print("\n=== 1. OFFLINE — descriptor pool, GetMessageClass, duplicate cases")
    pool = descriptor_pool.Default()
    print(f"pool: {pool!r} (the DEFAULT pool kipy's generated modules use)")

    existing = _find_existing(pool)
    if existing is not None:
        print(f"pool ALREADY defines {REFERENCE_POINT_FULL_NAME} — taking it as is "
              f"(the shim's Р2а would do nothing)")
        return message_factory.GetMessageClass(existing)

    payload = build_file_proto().SerializeToString()
    print(f"file name in the pool: {OUR_FILE_NAME}")
    print(f"dependencies: {', '.join(DEPENDENCIES)}")
    descriptor = pool.AddSerializedFile(payload)
    print(f"AddSerializedFile: ok, package={descriptor.package!r}")

    cls = message_factory.GetMessageClass(descriptor.message_types_by_name["ReferencePoint"])
    print(f"GetMessageClass: {cls.__name__} full_name={cls.DESCRIPTOR.full_name}")

    # Round trip on a message we build ourselves: the same path every live element takes
    # (Any -> value bytes -> our class -> serialize).
    sample = cls()
    sample.id.value = "11111111-2222-3333-4444-555555555555"
    sample.layer = EXPECTED_F_FAB
    sample.position.x_nm = 168_605_000
    sample.position.y_nm = 71_480_000
    sample.size.value_nm = EXPECTED_SIZE_NM
    packed = Any()
    packed.Pack(sample)
    reparsed = cls()
    reparsed.ParseFromString(packed.value)
    same_bytes = reparsed.SerializeToString() == sample.SerializeToString()
    print(f"round trip: type_url={packed.type_url!r} "
          f"type_url_ok={packed.type_url == REFERENCE_POINT_TYPE_URL} "
          f"bytes_identical={same_bytes}")

    print("\n--- duplicate case A: THE SAME FILE NAME again (plan 8.1: silently accepted)")
    try:
        again = pool.AddSerializedFile(payload)
        print(f"  no exception; same descriptor object: {again is descriptor}")
    except Exception as exc:                                    # noqa: BLE001 — measured
        print(f"  {type(exc).__name__}: {exc}")

    print("--- duplicate case B: the SAME SYMBOL in a file with ANOTHER name "
          "(what a newer kicad-python looks like)")
    other = descriptor_pb2.FileDescriptorProto()
    other.CopyFrom(build_file_proto())
    other.name = "somebody-else/reference_point.proto"
    try:
        pool.AddSerializedFile(other.SerializeToString())
        print("  no exception — the plan's 8.1 expected TypeError; the shim's "
              "FindMessageTypeByName order still covers it")
    except Exception as exc:                                    # noqa: BLE001 — measured
        print(f"  {type(exc).__name__}: {exc}")
    return cls


# ---------------------------------------------------------------------------------------
# Живая часть: сырые прото, БЕЗ unwrap
# ---------------------------------------------------------------------------------------

def _short_name(type_url: str) -> str:
    return type_url.rsplit(".", 1)[-1] if type_url else "<empty>"


def _ref_of(instance) -> str:
    """Refdes of a RAW ``FootprintInstance`` proto.

    Three levels, and each is a different message (measured on kipy's own descriptors):
    ``FootprintInstance.reference_field`` is a ``Field``, ``Field.text`` is a
    ``kiapi.board.types.BoardText``, and ``BoardText.text`` is a ``kiapi.common.types.Text``
    whose ``text`` field is the STRING. kipy's wrapper chain (``Field.text.value``) hides two
    of the three levels; this probe reads protos, not wrappers.
    """
    return instance.reference_field.text.text.text


def _read_footprint_instances(board) -> list:
    """Every FootprintInstance of the board as a RAW proto — the read kipy cannot finish.

    Byte-for-byte the request ``Board.get_footprints()`` sends (kipy ``board.py:800-805``);
    only the answer is parsed here instead of unwrapped.
    """
    command = GetItems()
    command.header.document.CopyFrom(board.document)
    command.types.append(KiCadObjectType.KOT_PCB_FOOTPRINT)
    response = board.client.send(command, GetItemsResponse)

    instances = []
    for raw in response.items:
        instance = board_types_pb2.FootprintInstance()
        instance.ParseFromString(raw.value)
        instances.append(instance)
    print(f"raw footprints read: {len(instances)} (status={response.status})")
    return instances


def _describe_point(cls, child_any, ref: str) -> dict:
    """Every field of one ReferencePoint, from the raw Any, through the dynamic class."""
    point = cls()
    point.ParseFromString(child_any.value)
    layer_name = board_types_pb2.BoardLayer.Name(point.layer)
    return {
        "ref": ref,
        "type_url": child_any.type_url,
        "value": bytes(child_any.value),
        "id": point.id.value,
        "layer": point.layer,
        "layer_name": layer_name,
        "position": (point.position.x_nm, point.position.y_nm),
        "size_nm": point.size.value_nm,
        "locked": point.locked,
        "parent": point.parent.value,
        "fields_present": sorted(f.name for f, _ in point.ListFields()),
    }


def _report_point(cls, instance, child_any) -> dict:
    ref = _ref_of(instance)
    point = _describe_point(cls, child_any, ref)
    footprint_nm = (instance.position.x_nm, instance.position.y_nm)
    angle = instance.orientation.value_degrees
    dx = point["position"][0] - footprint_nm[0]
    dy = point["position"][1] - footprint_nm[1]

    print(f"\n  {ref}: footprint {footprint_nm[0]}, {footprint_nm[1]} nm "
          f"angle {angle:g} deg layer {board_types_pb2.BoardLayer.Name(instance.layer)}")
    print(f"      point {point['position'][0]}, {point['position'][1]} nm "
          f"({point['layer_name']}, size {point['size_nm']} nm, "
          f"locked {point['locked']}, id {point['id']})")
    print(f"      point - footprint: {dx / 1e6:+.2f}, {dy / 1e6:+.2f} mm")
    print(f"      parent: {point['parent'] or '<unset>'}  footprint id: {instance.id.value}  "
          f"parent_is_footprint={point['parent'] == instance.id.value}")
    print(f"      value: {len(point['value'])} bytes sha256[:16]="
          f"{hashlib.sha256(point['value']).hexdigest()[:16]}")
    print(f"      fields seen: {', '.join(point['fields_present'])}")

    point.update(footprint_nm=footprint_nm, angle=angle, ref=ref, raw_instance=instance)
    return point


def live_section(cls) -> dict:
    """Read HP-Power-v099, census the item types, print the four points and the traceback."""
    print("\n=== 2. LIVE — HP-Power-v099, READ-ONLY")
    from kipy import KiCad

    board = KiCad().get_board()
    if board is None:
        print("No board is open in KiCad — open HP-Power-v099 and re-run.")
        raise SystemExit(1)
    print(f"board: {board.name!r}")
    if board.name != EXPECTED_BOARD:
        print(f"REFUSING: the plan's numbers were taken on {EXPECTED_BOARD!r}. "
              f"Nothing was read.")
        raise SystemExit(1)

    instances = _read_footprint_instances(board)

    print("\n--- census of definition.items, by type_url")
    census: dict[str, int] = {}
    by_ref: dict[str, list] = {}
    for instance in instances:
        ref = _ref_of(instance)
        for child in instance.definition.items:
            name = _short_name(child.type_url)
            census[name] = census.get(name, 0) + 1
            if name == "ReferencePoint":
                by_ref.setdefault(ref, []).append(child)
    for name, count in sorted(census.items(), key=lambda kv: -kv[1]):
        print(f"  {name:24s} {count}")
    unknown = sorted(set(census) - set(EXPECTED_CENSUS))
    print(f"types the plan did not see: {unknown or 'none'}")

    print("\n--- the reference points, every field")
    points = {}
    for ref, children in by_ref.items():
        instance = next(i for i in instances if _ref_of(i) == ref)
        for child in children:
            points[ref] = _report_point(cls, instance, child)

    print("\n--- the traceback of board.get_footprints() (the failure Ч0.1 named)")
    try:
        board.get_footprints()
        print("  no exception — kipy learned the type; the catalogue half of "
              "kicadstamp/kicad/kipy_item_types.py is now due for deletion")
    except Exception:                                           # noqa: BLE001 — measured
        traceback.print_exc(limit=12)

    print("\n--- comparison with the plan's 8.1")
    _check("footprints on the board", len(instances), EXPECTED_FOOTPRINTS)
    for name, count in EXPECTED_CENSUS.items():
        _check(f"census[{name}]", census.get(name, 0), count)
    for ref, (fp_nm, angle, point_nm, sha_prefix) in EXPECTED_POINTS.items():
        point = points.get(ref)
        if point is None:
            print(f"  DIFF {ref}: no reference point found")
            _MISMATCHES.append(f"{ref}.missing")
            continue
        _check(f"{ref}.footprint_nm", point["footprint_nm"], fp_nm)
        _check(f"{ref}.angle", point["angle"], angle)
        _check(f"{ref}.point_nm", point["position"], point_nm)
        _check(f"{ref}.sha256[:16]", hashlib.sha256(point["value"]).hexdigest()[:16], sha_prefix)
        _check(f"{ref}.value_len", len(point["value"]), EXPECTED_VALUE_LEN)
        _check(f"{ref}.layer", point["layer"], EXPECTED_F_FAB)
        _check(f"{ref}.size_nm", point["size_nm"], EXPECTED_SIZE_NM)
        _check(f"{ref}.locked", point["locked"], EXPECTED_UNLOCKED)
        _check(f"{ref}.parent_is_footprint", point["parent"] == point_id_of(instances, ref), True)

    return {"points": points, "instances": instances}


def point_id_of(instances, ref: str) -> str:
    """The KIID of one footprint instance by refdes (the point's ``parent`` must equal it)."""
    return next(i.id.value for i in instances if _ref_of(i) == ref)


# ---------------------------------------------------------------------------------------
# Фикстура
# ---------------------------------------------------------------------------------------

FIXTURE_TEMPLATE = '''"""Raw bytes of ONE ``ReferencePoint`` and of a whole ``FootprintInstance`` — {ref} of
{board}.

TAKEN BY `kicadstamp/diagnostics/probe_reference_point_{stamp}.py` on {taken}, READ-ONLY
(the board was never written to). Verified against the plan's section 8.1: every other live
number of that run is asserted by the same probe.

DO NOT EDIT. The cells in `tests/kicad/test_kipy_item_types.py` assert that kipy's own
``Footprint._pack`` (kipy/board_types.py:1999-2005) reproduces these bytes EXACTLY — a drift
here is the `lib_footprint_mismatch` disease of 15.10.2026, not a fixture to refresh.
"""

BOARD = {board!r}
REF = {ref!r}
TAKEN = {taken!r}

TYPE_URL = {type_url!r}

#: The ``Any.value`` of the reference point, byte for byte ({point_len} bytes).
POINT_VALUE = bytes.fromhex({point_hex!r})
POINT_SHA256 = {point_sha!r}

#: Every field of the point, so a cell can assert the schema without re-parsing by hand.
POINT_ID = {point_id!r}
POINT_LAYER = {point_layer!r}
POINT_POSITION_NM = ({point_x!r}, {point_y!r})
POINT_SIZE_NM = {point_size!r}
POINT_LOCKED = {point_locked!r}
POINT_PARENT = {point_parent!r}

#: The whole ``FootprintInstance`` proto of {ref} ({instance_len} bytes, base64 for git's sake).
FOOTPRINT_INSTANCE_B64 = (
{instance_b64}
)
FOOTPRINT_INSTANCE_SHA256 = {instance_sha!r}
FOOTPRINT_INSTANCE_LEN = {instance_len!r}

#: The footprint's own pose, as the point's rotation pivot (cells Д1/Д2/Д3).
FOOTPRINT_POSITION_NM = ({fp_x!r}, {fp_y!r})
FOOTPRINT_ANGLE_DEG = {fp_angle!r}
'''


def _wrap_b64(base64_text: str, width: int = 88) -> str:
    chunks = [base64_text[i:i + width] for i in range(0, len(base64_text), width)]
    return "\n".join(f"    {chunk!r}" for chunk in chunks)


def write_fixture(path: str, live: dict, cls) -> None:
    """Write the fixture module from the bytes just read (the ONE thing this probe files)."""
    points = live["points"]
    point = points.get(FIXTURE_REF)
    if point is None:
        print(f"\nfixture: {FIXTURE_REF} carries no reference point — nothing written")
        return
    instance = point["raw_instance"]
    instance_bytes = instance.SerializeToString()
    text = FIXTURE_TEMPLATE.format(
        ref=FIXTURE_REF,
        board=EXPECTED_BOARD,
        stamp="2026_11_04",
        taken=time.strftime("%Y-%m-%d %H:%M %Z"),
        type_url=point["type_url"],
        point_len=len(point["value"]),
        point_hex=point["value"].hex(),
        point_sha=hashlib.sha256(point["value"]).hexdigest(),
        point_id=point["id"],
        point_layer=point["layer"],
        point_x=point["position"][0],
        point_y=point["position"][1],
        point_size=point["size_nm"],
        point_locked=point["locked"],
        point_parent=point["parent"],
        instance_len=len(instance_bytes),
        instance_b64=_wrap_b64(base64.b64encode(instance_bytes).decode("ascii")),
        instance_sha=hashlib.sha256(instance_bytes).hexdigest(),
        fp_x=point["footprint_nm"][0],
        fp_y=point["footprint_nm"][1],
        fp_angle=point["angle"],
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"\nfixture written: {path} "
          f"({len(point['value'])} point bytes, {len(instance_bytes)} instance bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-fixture", metavar="PATH", default=None,
                        help="also write the fixture module (e.g. "
                             "tests/kicad/reference_point_bytes.py)")
    parser.add_argument("--offline-only", action="store_true",
                        help="only the descriptor-pool half — no KiCad needed")
    args = parser.parse_args(argv)

    cls = offline_section()
    if cls is None:
        print("\nthe pool route FAILED — stopping before the live read")
        return 2
    if args.offline_only:
        return 0

    live = live_section(cls)
    if args.write_fixture:
        write_fixture(args.write_fixture, live, cls)

    print("\n=== RESULT")
    if _MISMATCHES:
        print(f"MISMATCHES against plan 8.1: {len(_MISMATCHES)} — {', '.join(_MISMATCHES)}")
        return 2
    print("all live numbers match plan 8.1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
