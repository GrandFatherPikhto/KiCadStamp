#!/usr/bin/env python3
"""Watchdogs for the item-type catalogue over kipy —
``kicadstamp/kicad/kipy_item_types.py`` (plan_2026_11_04_kipy_reference_point, Т1).

The disease (live, 04.11.2026, HP-Power-v099): KiCad 10.0.8 sends
``ReferencePoint`` (added ``// Since: 10.0.7``) and kicad-python 0.8.0 has no mapping for it, so
``unpack_any`` raises ``NotImplementedError`` — and ``Footprint.__init__`` unwraps EVERY child of
EVERY footprint, so ``Board.get_footprints()`` died whole and the GUI could not connect at all.

Style: the REAL bytes of Q1's reference point and of Q1's whole ``FootprintInstance``
(``tests/kicad/reference_point_bytes.py``, taken read-only by
``kicadstamp/diagnostics/probe_reference_point_2026_11_04.py``), every other object built through
kipy's own public API — no board, no IPC. The wrapper under audit is kipy's own ``unwrap`` and
kipy's own ``Footprint._pack``.

К7 and К8 pin SOMEBODY ELSE'S behaviour on purpose (kipy's ``unpack_any`` still failing, kipy's
own table winning over ours): when they go red, the catalogue half of the shim is due for
deletion — the module docstring says so.
"""
import base64
import importlib

import pytest
from google.protobuf.any_pb2 import Any

import kipy.board_types as kipy_board_types
import kipy.util.proto as kipy_proto_util
from kipy.board_types import BoardItem, FootprintInstance, unwrap
from kipy.proto.board import board_types_pb2
from kipy.proto.common.commands.editor_commands_pb2 import SelectionResponse
from kipy.proto.common.types import DocumentSpecifier
from kipy.proto.common.types.base_types_pb2 import KIID

from kicadstamp.kicad import kipy_item_types as catalogue
from kicadstamp.kicad.kipy_item_types import (
    _KIPY_UNPACK_ANY,
    REFERENCE_POINT_FULL_NAME,
    REFERENCE_POINT_TYPE_URL,
    ReferencePointItem,
    UnknownBoardItem,
    is_opaque_item,
    is_reference_point,
)
from kicadstamp.kicad.kipy_selection import read_raw_selection
from tests.kicad import reference_point_bytes as fx

#: A type NOBODY has heard of — the "next KiCad releases something new" case of Р2б. The payload
#: is a real protobuf message (a KIID), so the stub parses it as unknown fields exactly like the
#: live point's 102 bytes.
UNKNOWN_TYPE_URL = "type.googleapis.com/kiapi.board.types.SomeFutureThing"
UNKNOWN_PAYLOAD = KIID(value="11111111-2222-3333-4444-555555555555").SerializeToString()


# ── builders ────────────────────────────────────────────────────────────────────────────

def _any_of(message, type_url=None):
    """An ``Any`` around *message* the way KiCad sends it (a plain 1:1 pack)."""
    packed = Any()
    packed.Pack(message)
    if type_url is not None:
        packed.type_url = type_url
    return packed


def _fixture_instance() -> board_types_pb2.FootprintInstance:
    """Q1's ``FootprintInstance`` proto, parsed fresh from the fixture bytes every time."""
    instance = board_types_pb2.FootprintInstance()
    instance.ParseFromString(base64.b64decode(fx.FOOTPRINT_INSTANCE_B64))
    return instance


def _fixture_footprint():
    """Q1 as kipy hands it out: a real ``FootprintInstance`` reached through kipy's ``unwrap``."""
    return unwrap(_any_of(_fixture_instance()))


def _point_of(kipy_fp):
    points = [item for item in kipy_fp.definition.items if is_reference_point(item)]
    assert len(points) == 1, "the fixture footprint carries exactly one reference point"
    return points[0]


def _with_unknown_child(instance, type_url=UNKNOWN_TYPE_URL, payload=UNKNOWN_PAYLOAD):
    """*instance* plus one synthetic child of a type kipy cannot know."""
    instance.definition.items.add(type_url=type_url, value=payload)
    return instance


# ── К1: the symptom — a footprint with a point reads instead of raising ─────────────────

def test_a_footprint_carrying_a_reference_point_reads_through_kipy_unwrap():
    """The whole point of the shim: ``unwrap`` answers instead of ``NotImplementedError``."""
    kipy_fp = _fixture_footprint()

    assert isinstance(kipy_fp, FootprintInstance)
    point = _point_of(kipy_fp)
    assert isinstance(point, ReferencePointItem)
    assert isinstance(point, BoardItem), (
        "Board._to_concrete_items asserts isinstance(item, BoardItem) — kipy/board.py:657")


# ── К2: the schema — every field of the LIVE bytes, against section 1 of the plan ───────

def test_the_reference_point_fields_are_the_ones_kicad_10_0_7_sends():
    point = _point_of(_fixture_footprint())
    instance = _fixture_instance()

    assert point.id.value == fx.POINT_ID
    assert point.layer == fx.POINT_LAYER == board_types_pb2.BoardLayer.BL_F_Fab
    assert (point.position.x, point.position.y) == fx.POINT_POSITION_NM
    assert point.size_nm == fx.POINT_SIZE_NM == 2_000_000
    assert point.locked == fx.POINT_LOCKED
    assert point.parent.value == fx.POINT_PARENT
    assert point.parent.value == instance.id.value, (
        "the point belongs to its footprint: `parent` is Q1's own KIID (plan 8.1)")
    assert point.proto.SerializeToString() == fx.POINT_VALUE


# ── К3: risk 1 of the plan — the packed footprint is BYTE-IDENTICAL to what came in ─────

def test_kipy_packs_the_whole_footprint_back_byte_for_byte():
    """``Footprint._pack`` rebuilds ``items`` from the wrappers — measured: no drift at all.

    This is the cell that would have caught the 15.10.2026 ``lib_footprint_mismatch`` disease
    for the point: a single changed byte here means every write sends KiCad a different element.
    """
    raw = base64.b64decode(fx.FOOTPRINT_INSTANCE_B64)
    kipy_fp = unwrap(_any_of(_fixture_instance()))

    assert kipy_fp.proto.SerializeToString() == raw
    assert _point_of(kipy_fp).proto.SerializeToString() == fx.POINT_VALUE


# ── К4: Р2б — an unknown type becomes an OPAQUE item that keeps its bytes ───────────────

def test_an_unknown_type_becomes_an_opaque_item_and_its_bytes_survive():
    instance = _with_unknown_child(_fixture_instance())
    packed_in = instance.SerializeToString()

    kipy_fp = unwrap(_any_of(board_types_pb2.FootprintInstance.FromString(packed_in)))

    unknown = [item for item in kipy_fp.definition.items if is_opaque_item(item)]
    assert len(unknown) == 1
    assert isinstance(unknown[0], UnknownBoardItem)
    assert unknown[0].proto.SerializeToString() == UNKNOWN_PAYLOAD
    assert unknown[0].type_url == UNKNOWN_TYPE_URL
    assert isinstance(unknown[0].id, KIID) and unknown[0].id.value == "", (
        "an opaque item has no addressable id — and must not raise AttributeError either")
    assert kipy_fp.proto.SerializeToString() == packed_in, (
        "the element must come back out exactly as it went in")


# ── К5: the opaque item is named by what it IS, not by a list of names ──────────────────

def test_the_opaque_item_reports_its_own_type_name():
    kipy_fp = unwrap(_any_of(board_types_pb2.FootprintInstance.FromString(
        _with_unknown_child(_fixture_instance()).SerializeToString())))
    unknown = next(i for i in kipy_fp.definition.items if is_opaque_item(i))

    assert unknown.full_name == "kiapi.board.types.SomeFutureThing"
    assert unknown.short_name == "SomeFutureThing"
    assert catalogue.unknown_items(kipy_fp) == {"SomeFutureThing": 1}


# ── К6: a second import / an importlib.reload must not stack a second wrapper ───────────

def test_install_is_idempotent_and_survives_a_reload():
    catalogue.install()
    catalogue.install()

    reloaded = importlib.reload(catalogue)

    assert getattr(kipy_board_types.unpack_any, catalogue._OURS_MARKER, False), (
        "kipy.board_types must still hold OUR wrapper, not kipy's function")
    assert not getattr(reloaded._KIPY_UNPACK_ANY, catalogue._OURS_MARKER, False), (
        "the frozen original must stay kipy's own function after a reload")
    assert reloaded._KIPY_UNPACK_ANY is _KIPY_UNPACK_ANY, (
        "the reload must find the SAME original, not capture the wrapper as one")
    # And the behaviour after all that is unchanged: the live bytes still read.
    assert isinstance(_point_of(_fixture_footprint()), ReferencePointItem)

    # An opaque item built BEFORE the reload is still recognised — the mark travels with the
    # object, class identity does not (measured: an identity test silently killed Р4's log line
    # once the module had been reloaded and kipy's registry kept the pre-reload wrapper).
    unknown = next(i for i in unwrap(_any_of(board_types_pb2.FootprintInstance.FromString(
        _with_unknown_child(_fixture_instance()).SerializeToString()))).definition.items
        if is_opaque_item(i))
    assert reloaded.is_opaque_item(unknown)
    assert reloaded.unknown_items(
        unwrap(_any_of(board_types_pb2.FootprintInstance.FromString(
            _with_unknown_child(_fixture_instance()).SerializeToString())))) == {"SomeFutureThing": 1}


# ── К7: CANARY — pure kipy still cannot unpack a point (removal condition 1) ────────────

def test_the_pure_kipy_unpack_any_still_raises_for_the_reference_point():
    """Measures KIPY, not this module: the frozen original, kipy's own table, no shim in the way.

    Goes RED the day kicad-python learns ``ReferencePoint`` — that is the signal to delete the
    catalogue half (see the module docstring, removal condition 1), because kipy will then hand
    out its own class and branch 1 of our wrapper already defers to it.
    """
    packed = Any(type_url=REFERENCE_POINT_TYPE_URL, value=fx.POINT_VALUE)

    with pytest.raises(NotImplementedError):
        _KIPY_UNPACK_ANY(packed)


# ── К8: a type kipy DOES know is delegated to kipy, not replaced by our stub ────────────

def test_a_type_kipy_already_knows_is_left_to_kipy(monkeypatch):
    """The "newer kicad-python ships the type" case, without needing that kicad-python.

    Putting the URL into kipy's OWN table is exactly the state the shim must defer to — and it
    proves the wrapper never re-implements a mapping that exists (its branch 1).
    """
    monkeypatch.setitem(kipy_proto_util._any_urls, REFERENCE_POINT_TYPE_URL,
                        board_types_pb2.BoardText)
    packed = Any(type_url=REFERENCE_POINT_TYPE_URL, value=fx.POINT_VALUE)

    concrete = kipy_board_types.unpack_any(packed)

    assert concrete.DESCRIPTOR.full_name == "kiapi.board.types.BoardText", (
        "kipy's own table answered — our catalogue must not have been consulted")


# ── К9: the selection keeps everything readable, and the skipped counter stays honest ───

class _ResponseBoard:
    """The two members ``read_raw_selection`` touches: ``document`` and ``client.send``."""

    def __init__(self, items):
        self.document = DocumentSpecifier()
        self._items = items
        self.client = self

    def send(self, command, response_type):
        assert response_type is SelectionResponse
        response = SelectionResponse()
        response.items.extend(self._items)
        return response

    def _to_concrete_items(self, items):
        """kipy's own guard (board.py:654-657), so an opaque item proves it passes."""
        for item in items:
            assert isinstance(item, BoardItem)
        return list(items)


def test_the_whole_selection_survives_a_point_and_an_unknown_item():
    instance = _with_unknown_child(_fixture_instance())
    items = [
        _any_of(board_types_pb2.FootprintInstance.FromString(instance.SerializeToString())),
        Any(type_url=REFERENCE_POINT_TYPE_URL, value=fx.POINT_VALUE),   # a point on its own
        Any(type_url=UNKNOWN_TYPE_URL, value=UNKNOWN_PAYLOAD),          # nobody's type
        Any(),                                                          # a DRC marker
    ]

    read, skipped = read_raw_selection(_ResponseBoard(items))

    assert skipped == 1, "only the empty Any (a DRC marker) is unreadable over IPC"
    assert isinstance(read[0], FootprintInstance)
    assert isinstance(read[1], ReferencePointItem)
    assert is_opaque_item(read[2])
    # The standalone point is the real one, not an empty shell: same id, same parent, same bytes.
    assert read[1].id.value == fx.POINT_ID
    assert read[1].parent.value == fx.POINT_PARENT
    assert read[1].proto.SerializeToString() == fx.POINT_VALUE
    assert REFERENCE_POINT_FULL_NAME == read[1].proto.DESCRIPTOR.full_name
