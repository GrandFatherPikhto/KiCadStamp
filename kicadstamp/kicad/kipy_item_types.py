# kicadstamp/kicad/kipy_item_types.py
"""The ONE place where this project teaches kipy a board-item type it does not know.

WHY (plan_2026_11_04_kipy_reference_point). KiCad 10.0.7 added ``ReferencePoint`` to the board
API (``api/proto/board/board_types.proto``, ``// Since: 10.0.7``), and the stock KiCad 10
footprints ``Package_TO_SOT_THT:TO-247-3_Vertical`` / ``TO-220-2_Vertical`` carry one on F.Fab.
kicad-python 0.8.0 was generated against 10.0.6 and has no mapping for it, so
``kipy.util.proto.unpack_any`` raises ``NotImplementedError`` — and ``Footprint.__init__``
unwraps EVERY child of EVERY footprint (kipy/board_types.py:1994), so the whole
``Board.get_footprints()`` answer dies. Measured live 04.11.2026 on HP-Power-v099: the GUI could
not connect at all ("Missing type mapping for type.googleapis.com/kiapi.board.types.ReferencePoint,
can't unpack it" every 5 s), and four footprints (Q1, Q2, D4, D8) carry the point.

TWO STAGES, and neither of them throws anything away:

* **the catalogue.** ``ReferencePoint`` becomes a REAL message here — a hand-built
  ``FileDescriptorProto`` (six fields, section 1 of the plan) added to the DEFAULT descriptor
  pool kipy's own generated modules use, so its class comes out of
  ``message_factory.GetMessageClass`` exactly like kipy's own types. The wrapper
  ``ReferencePointItem`` gives it ``id``/``position``/``layer``/``size_nm``/``locked``/``parent``.
  If kipy ALREADY knows the type (a newer kicad-python) the pool lookup finds it first and this
  module takes kipy's own class and does nothing else — the same "mechanism, not a version
  check" rule the other shims here follow;
* **anything else.** A ``type_url`` nobody maps becomes an OPAQUE item: an empty stub message
  under that full name (created on first sight, cached), parsed from the ``Any`` value. protobuf
  keeps unknown fields, so ``Any.Pack`` puts the very same ``type_url`` and the very same bytes
  back — the element survives a move or a rotation byte for byte. The rule is therefore the
  MECHANISM ("a type I was not generated against"), not a list of names: the next type KiCad adds
  cannot take the connection down again.

WHERE THE SEAM IS, and why it is the FUNCTION rather than the table. ``kipy.util.proto`` keeps
``_any_urls`` and raises BEFORE any wrapper is reached for a URL it does not know
(``util/proto.py:56-58``) — a table alone can never implement the second stage. So the function
is wrapped once and every name binding in kipy is replaced: ``kipy.util.proto.unpack_any`` (the
definition), ``kipy.util.unpack_any`` (the re-export at ``util/__init__.py:24``), and
``kipy.board_types.unpack_any`` / ``kipy.schematic_types.unpack_any`` (both bound it BY VALUE at
import — ``board_types.py:58`` / ``schematic_types.py:42`` — and both ``unwrap``s call their own
name). kipy's ``_any_urls`` is never edited: a URL kipy knows is delegated to kipy's own code
untouched, which is also what keeps the canary below an honest measurement of kipy.

WHAT A WRAPPER MUST INHERIT. ``Board._to_concrete_items`` asserts ``isinstance(item, BoardItem)``
(kipy/board.py:657) and ``kipy.board_types.unwrap`` looks the wrapper up in
``_proto_to_object`` — so the board registration happens here, on a ``BoardItem`` subclass.

THE SCHEMATIC SIDE IS BEST-EFFORT, and that is MEASURED, not defensive noise: on this venv
``import kipy.schematic_types`` raises ``ImportError: cannot import name 'BusEntryType'`` —
kicad-python 0.8.0's own schematic module does not match the pb2 files it ships. A broken
schematic half must never take the BOARD read down, so the schematic module is imported lazily,
inside a guard, and its absence only skips its two registrations (a board element cannot arrive
through it anyway).

REMOVAL CONDITIONS — three, each with its own canary, none of them ours:
1. ``test_the_pure_kipy_unpack_any_still_raises_for_the_reference_point``
   (tests/kicad/test_kipy_item_types.py) calls the FROZEN original ``_KIPY_UNPACK_ANY``. When IT
   goes red, kicad-python learned the type: the catalogue half (the descriptor, the wrapper and
   the reference-point branch) is dead and must go — the delegation branch below already hands
   the type to kipy without being asked;
2. ``test_raw_kipy_position_setter_does_not_move_the_reference_point``
   (tests/kicad/test_adapter_reference_point_geometry.py). When IT goes red, kipy's own position
   setter moves the point and ``kipy_footprint_safety.sync_dto_to_kipy`` must stop moving it;
3. ``test_raw_kipy_orientation_setter_drops_the_reference_point`` (same file). When IT goes red,
   kipy's orientation setter rotates the point itself and the rotation half of
   ``set_footprint_orientation`` must go.

The offline instrument that proved this route before a line of it existed is
``kicadstamp/diagnostics/probe_reference_point_2026_11_04.py`` (it rebuilds the same descriptor
on purpose — an instrument must not ask the thing it measures).
"""
from __future__ import annotations

import logging
from typing import Optional

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.any_pb2 import Any
from google.protobuf.message import DecodeError, Message

import kipy.board_types as _kipy_board_types
import kipy.util as _kipy_util
import kipy.util.proto as _kipy_util_proto
from kipy.board_types import BoardItem
from kipy.geometry import Vector2 as KipyVector2
from kipy.proto.common.types import base_types_pb2
from kipy.wrapper import Item as _KipyItem

logger = logging.getLogger(__name__)

#: The message KiCad 10.0.7 sends and kicad-python 0.8.0 has no mapping for.
REFERENCE_POINT_FULL_NAME = "kiapi.board.types.ReferencePoint"
REFERENCE_POINT_TYPE_URL = "type.googleapis.com/" + REFERENCE_POINT_FULL_NAME

#: Every ``Any`` KiCad produces carries this prefix (measured live: all four points on
#: HP-Power-v099). Used both to strip it and to rebuild it from an opaque item's own type.
_TYPE_URL_PREFIX = "type.googleapis.com/"

#: The name OUR file gets in the descriptor pool. Deliberately NOT a KiCad file name — the pool
#: already holds the real ``board/board_types.proto``, and a name that looks like KiCad's would
#: collide the moment a newer kicad-python ships the type upstream.
_OUR_FILE_NAME = "kicadstamp/api10_board_types_reference_point.proto"

#: The two files already in the pool that define every type the message refers to — the SHORT
#: names from the descriptors (``board_types_pb2.py:11``, ``base_types_pb2.py:11``), not the
#: ``api/proto/...`` source paths.
_DEPENDENCIES = ("common/types/base_types.proto", "board/board_types.proto")

#: Section 1 of the plan: id=1, layer=2, position=3, size=4, locked=5, parent=6.
_SCHEMA_FIELDS = (
    ("id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.KIID"),
    ("layer", 2, descriptor_pb2.FieldDescriptorProto.TYPE_ENUM, ".kiapi.board.types.BoardLayer"),
    ("position", 3, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.Vector2"),
    ("size", 4, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.Distance"),
    ("locked", 5, descriptor_pb2.FieldDescriptorProto.TYPE_ENUM, ".kiapi.common.types.LockedState"),
    ("parent", 6, descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE, ".kiapi.common.types.KIID"),
)

#: Module attribute of ``kipy.util.proto`` marking a FINISHED install, so a reload (or a second
#: copy of this module) cannot wrap kipy twice. A dict cannot carry attributes, hence the module.
_INSTALLED_MARKER = "_kicadstamp_item_types_installed"

#: Attribute on the ORIGINAL function, so the marker survives our module being reloaded.
_ORIGINAL_MARKER = "_kicadstamp_original_unpack_any"

#: Attribute on our replacement — the second, function-level idempotency guard.
_OURS_MARKER = "_kicadstamp_item_types_unpack"

#: CLASS attribute marking an opaque wrapper. Identity of the CLASS is not a safe test: an
#: ``importlib.reload`` of this module builds new classes while kipy's ``_proto_to_object``
#: keeps handing out the ones registered before the reload, so ``isinstance`` would start
#: answering "no" for items that ARE ours (measured: it silently killed the Р4 log line and a
#: whole cell). The mark travels WITH the object; a descriptor name does the same job for the
#: reference point.
_OPAQUE_MARKER = "_kicadstamp_opaque_item"

_REFERENCE_POINT_CLASS: Optional[type] = None
_STUB_CLASSES: dict[str, type] = {}
_SCHEMATIC_MODULE = None
_SCHEMATIC_PROBED = False


def build_file_proto() -> descriptor_pb2.FileDescriptorProto:
    """The ``ReferencePoint`` message of the plan's section 1, as a FileDescriptorProto.

    Assembled in memory on purpose: shipping a ``.proto`` file would mean shipping a protoc step
    (and a build dependency) for six fields of one message.
    """
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = _OUR_FILE_NAME
    file_proto.package = "kiapi.board.types"
    file_proto.syntax = "proto3"
    file_proto.dependency.extend(_DEPENDENCIES)

    message = file_proto.message_type.add()
    message.name = "ReferencePoint"
    for name, number, field_type, type_name in _SCHEMA_FIELDS:
        field = message.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type
        field.type_name = type_name
    return file_proto


def schematic_types():
    """``kipy.schematic_types``, or None when the installed kipy cannot even import it.

    MEASURED 04.11.2026 (kicad-python 0.8.0 in this venv): the import raises
    ``ImportError: cannot import name 'BusEntryType' from ...schematic_types_pb2`` — kipy's own
    schematic module does not match the generated files it ships. Every use of it here is
    conditional for that reason alone; the board path is what this module exists for.
    """
    global _SCHEMATIC_MODULE, _SCHEMATIC_PROBED
    if not _SCHEMATIC_PROBED:
        _SCHEMATIC_PROBED = True
        try:
            import kipy.schematic_types as module
        except Exception as exc:                                # noqa: BLE001 — measured
            logger.debug("kipy.schematic_types is unusable (%s: %s) — its registrations are "
                         "skipped", type(exc).__name__, exc)
            module = None
        _SCHEMATIC_MODULE = module
    return _SCHEMATIC_MODULE


# --------------------------------------------------------------------------------------
# The message class — kipy's own when it has one, ours when it does not
# --------------------------------------------------------------------------------------

def reference_point_class() -> type:
    """The ``kiapi.board.types.ReferencePoint`` message class of the DEFAULT pool.

    ORDER IS THE RULE (plan 7.2): ``FindMessageTypeByName`` first. A newer kicad-python that
    defines the type itself is taken as is — and ``AddSerializedFile`` refusing a duplicate
    SYMBOL (``TypeError``, measured) is then never even reached. Adding the SAME file twice is
    accepted silently by protobuf, so a reload needs no extra care beyond the cache below.
    """
    global _REFERENCE_POINT_CLASS
    if _REFERENCE_POINT_CLASS is not None:
        return _REFERENCE_POINT_CLASS

    pool = descriptor_pool.Default()
    try:
        descriptor = pool.FindMessageTypeByName(REFERENCE_POINT_FULL_NAME)
    except KeyError:
        descriptor = None
    if descriptor is None:
        added = pool.AddSerializedFile(build_file_proto().SerializeToString())
        descriptor = added.message_types_by_name["ReferencePoint"]

    _REFERENCE_POINT_CLASS = message_factory.GetMessageClass(descriptor)
    return _REFERENCE_POINT_CLASS


class ReferencePointItem(BoardItem):
    """A KiCad ``ReferencePoint`` — the fab-layer mark that says "place this part by hand here".

    Geometry is the footprint's, exactly like a pad's: ``position`` is ABSOLUTE (KiCad stores a
    footprint's children that way, kipy/board_types.py:2094-2095) and moves/rotates with it. The
    arithmetic itself is NOT here — ``kipy_footprint_safety`` owns it, because it owns the same
    rule for every other item type and the one exact ``Vector2.rotate``.
    """

    def __init__(self, proto: Optional[Message] = None, proto_ref: Optional[Message] = None):
        self._proto = proto if proto is not None else reference_point_class()()

    def __repr__(self) -> str:
        return (f"ReferencePointItem(id={self.id.value}, pos={self.position}, "
                f"layer={self.layer})")

    @property
    def position(self) -> KipyVector2:
        return KipyVector2(self._proto.position)

    @position.setter
    def position(self, position: KipyVector2) -> None:
        self._proto.position.CopyFrom(position.proto)

    @property
    def layer(self) -> int:
        return self._proto.layer

    @property
    def size_nm(self) -> int:
        return self._proto.size.value_nm

    @property
    def locked(self) -> int:
        return self._proto.locked

    @property
    def parent(self):
        """The footprint this point belongs to — KiCad sets it, and live measurement has it
        equal to the owning ``FootprintInstance.id`` on all four points of HP-Power-v099."""
        return self._proto.parent if self._proto.HasField("parent") else None


# --------------------------------------------------------------------------------------
# Everything else: an OPAQUE item that keeps its own bytes
# --------------------------------------------------------------------------------------

class UnknownBoardItem(BoardItem):
    """A board item whose type this kipy was not generated against.

    Nothing about it is known here — not even that it HAS an ``id`` field (the stub message is
    empty, so ``BoardItem.id`` would raise ``AttributeError`` on it). What IS known is its name
    and its bytes: ``proto`` returns the stub, and ``Any.Pack`` puts the same ``type_url`` and
    the same value back, which is what keeps a moved footprint from losing the element.
    """

    #: What :func:`is_opaque_item` answers — class identity breaks across an ``importlib.reload``
    #: of this module while kipy's registry keeps the pre-reload wrapper (see ``_OPAQUE_MARKER``).
    _kicadstamp_opaque_item = True

    def __init__(self, proto: Optional[Message] = None, proto_ref: Optional[Message] = None):
        self._proto = proto

    def __repr__(self) -> str:
        return (f"UnknownBoardItem({self.full_name}, "
                f"{len(self._proto.SerializeToString())} bytes)")

    @property
    def full_name(self) -> str:
        return self._proto.DESCRIPTOR.full_name

    @property
    def short_name(self) -> str:
        return self.full_name.rsplit(".", 1)[-1]

    @property
    def type_url(self) -> str:
        return _TYPE_URL_PREFIX + self.full_name

    @property
    def id(self) -> base_types_pb2.KIID:
        """An EMPTY KIID: whether the real message has an ``id`` at all is unknown here.

        Deliberately not ``self.proto.id`` (what ``BoardItem`` does): the stub message has no
        fields, and an ``AttributeError`` out of a read path is exactly the disease this module
        exists to end. Callers that need a real UUID filter by their own DTO type — nothing in
        this project treats an unknown item as an addressable entity.
        """
        return base_types_pb2.KIID()


class UnknownSchematicItem(_KipyItem):
    """The schematic-side twin of :class:`UnknownBoardItem`.

    Based on ``kipy.wrapper.Item`` rather than on ``SchematicItem`` because
    ``kipy.schematic_types`` may not even import (see :func:`schematic_types`); nothing in that
    module asserts a base class on what ``unwrap`` returns, only that a wrapper exists.
    """

    _kicadstamp_opaque_item = True

    def __init__(self, proto: Optional[Message] = None, proto_ref: Optional[Message] = None):
        self._proto = proto

    def __repr__(self) -> str:
        return (f"UnknownSchematicItem({self.full_name}, "
                f"{len(self._proto.SerializeToString())} bytes)")

    @property
    def full_name(self) -> str:
        return self._proto.DESCRIPTOR.full_name

    @property
    def short_name(self) -> str:
        return self.full_name.rsplit(".", 1)[-1]

    @property
    def type_url(self) -> str:
        return _TYPE_URL_PREFIX + self.full_name

    @property
    def id(self) -> base_types_pb2.KIID:
        return base_types_pb2.KIID()


def _stub_class_for(type_url: str) -> type:
    """The empty message class standing in for ONE unknown ``type_url`` (cached).

    An empty message keeps every field KiCad sent as an UNKNOWN field, and protobuf keeps
    unknown fields across parse+serialize — measured on the real 102-byte point and on all 84
    footprints of HP-Power-v099 (plan 8.1, risk 1: zero drift).
    """
    full_name = (type_url[len(_TYPE_URL_PREFIX):]
                 if type_url.startswith(_TYPE_URL_PREFIX) else type_url)
    cached = _STUB_CLASSES.get(full_name)
    if cached is not None:
        return cached

    pool = descriptor_pool.Default()
    try:
        descriptor = pool.FindMessageTypeByName(full_name)
    except KeyError:
        descriptor = None
    if descriptor is None:
        file_proto = descriptor_pb2.FileDescriptorProto()
        file_proto.name = f"kicadstamp/unknown/{full_name}.proto"
        package, _, short_name = full_name.rpartition(".")
        file_proto.package = package
        file_proto.syntax = "proto3"
        message = file_proto.message_type.add()
        message.name = short_name
        descriptor = pool.AddSerializedFile(
            file_proto.SerializeToString()).message_types_by_name[short_name]

    stub = message_factory.GetMessageClass(descriptor)
    _STUB_CLASSES[full_name] = stub
    # unwrap() looks its wrapper up in its OWN module's table, so each side needs an entry with
    # a wrapper of the base class IT accepts.
    _kipy_board_types._proto_to_object.setdefault(stub, UnknownBoardItem)
    schematic = schematic_types()
    if schematic is not None:
        schematic._proto_to_object.setdefault(stub, UnknownSchematicItem)
    logger.debug("Opaque item type registered: %s", full_name)
    return stub


# --------------------------------------------------------------------------------------
# The seam: kipy's unpack_any, wrapped
# --------------------------------------------------------------------------------------

def _unpack_any(raw_any: Any) -> Message:
    """``kipy.util.proto.unpack_any`` with the two stages of this module in front of it.

    kipy's own table is consulted FIRST and its own function answers for a type it knows — the
    shim never re-implements a mapping that exists. Everything else is parsed here, by name, so
    a "KiCad is newer than kipy" element can no longer take down a whole board read.
    """
    if len(raw_any.type_url) == 0:
        raise ValueError("Can't unpack empty Any protobuf message")
    if raw_any.type_url in _kipy_util_proto._any_urls:
        return _KIPY_UNPACK_ANY(raw_any)

    if raw_any.type_url == REFERENCE_POINT_TYPE_URL:
        concrete = reference_point_class()()
    else:
        concrete = _stub_class_for(raw_any.type_url)()

    try:
        concrete.ParseFromString(raw_any.value)
    except DecodeError:
        # The SAME wording kipy uses for a value it cannot parse (util/proto.py:64): a garbage
        # payload is a real error and must stay loud — an opaque item whose bytes were lost
        # would go back to KiCad emptied, which is the disease this module exists to end.
        raise ValueError(
            f"Can't unpack {raw_any.type_url}.  Incompatible change on KiCad side?") from None
    return concrete


def is_reference_point(item) -> bool:
    """True for a KiCad ``ReferencePoint`` — by DESCRIPTOR full name, never by class.

    Class identity would stop matching the moment kicad-python ships the type itself (then the
    wrapper is ITS class, not :class:`ReferencePointItem`); the descriptor name is the same in
    both worlds, and it is the thing KiCad actually sends.
    """
    proto = getattr(item, "proto", None)
    descriptor = getattr(proto, "DESCRIPTOR", None)
    return descriptor is not None and descriptor.full_name == REFERENCE_POINT_FULL_NAME


def is_opaque_item(item) -> bool:
    """True for an item whose type nobody maps — by the MARK on its class, never by identity."""
    return getattr(type(item), _OPAQUE_MARKER, False)


def unknown_items(kipy_item) -> dict[str, int]:
    """``{short type name: count}`` for the opaque children of one footprint.

    Counted by the same mechanism the wrapper uses — "this object carries the opaque mark" — so a
    type kipy learns later stops being reported the moment its own wrapper takes over.
    """
    items = getattr(getattr(kipy_item, "definition", None), "items", None)
    if not isinstance(items, list):
        # A stand-in without a definition (a bench double) has nothing to report; a REAL kipy
        # footprint always answers a list (kipy/board_types.py:2019).
        return {}
    counts: dict[str, int] = {}
    for item in items:
        if is_opaque_item(item):
            counts[item.short_name] = counts.get(item.short_name, 0) + 1
    return counts


def install() -> None:
    """Bind the wrapper into kipy. Idempotent, and safe to re-run after a module reload."""
    if getattr(_kipy_util_proto, _INSTALLED_MARKER, False):
        return

    if getattr(_kipy_util_proto, _ORIGINAL_MARKER, None) is None:
        # The ORIGINAL lives on kipy's module, not ours: our body re-runs on importlib.reload,
        # and a reload must not capture our own wrapper as "the original" (the canary cell
        # would then measure us instead of kipy).
        setattr(_kipy_util_proto, _ORIGINAL_MARKER, _kipy_util_proto.unpack_any)

    # The catalogue half: the wrapper has to exist in _proto_to_object even when the class is
    # kipy's own, otherwise unwrap() answers `assert wrapper is not None`.
    _kipy_board_types._proto_to_object.setdefault(
        reference_point_class(), ReferencePointItem)

    replacement = _unpack_any
    setattr(replacement, _OURS_MARKER, True)
    if not getattr(_kipy_util_proto.unpack_any, _OURS_MARKER, False):
        _kipy_util_proto.unpack_any = replacement
        _kipy_util.unpack_any = replacement
        _kipy_board_types.unpack_any = replacement
        schematic = schematic_types()
        if schematic is not None:
            schematic.unpack_any = replacement

    setattr(_kipy_util_proto, _INSTALLED_MARKER, True)
    logger.debug("kipy item-type catalogue installed (%s, file %s)",
                 REFERENCE_POINT_FULL_NAME, _OUR_FILE_NAME)


#: kipy's OWN ``unpack_any``, bound before the first replacement and kept for the canary cell
#: (tests/kicad/test_kipy_item_types.py), which must measure kipy and never this module.
#: Read off kipy's module so a reload of THIS file cannot lose the original.
_KIPY_UNPACK_ANY = getattr(_kipy_util_proto, _ORIGINAL_MARKER, None) or _kipy_util_proto.unpack_any

install()
