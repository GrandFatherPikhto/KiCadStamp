# tests/test_adapter_commit_document.py
"""Э1 of plan_2026_09_16_commit_document_and_pending_direction: the DOCUMENT
inside BeginCommit/EndCommit.

KiCad 10.0.7 (api/proto/common/commands/editor_commands.proto, branch 10.0,
commit f3105844c6 "API: add document specifier for BeginCommit/EndCommit")
added an `ItemHeader` to both transaction commands and now refuses a headerless
BeginCommit as soon as more than one editor is open:

    KiCad returned API error: BeginCommit without a document specified is not
    allowed when multiple editors are open

kipy — installed 0.7.1 AND the latest on PyPI 0.8.0 (measured 16.09.2026) —
does not know the field at all (BeginCommit has NO fields in its descriptor,
EndCommit only id/action/message), so the adapter writes it by hand.

These tests therefore pin the BYTES that go over the wire: the field is
invisible to the generated API, and the wire is the only place it can be
observed. The expected bytes are assembled HERE, independently of the helper
under test (only the proto definitions are shared — there is no second way to
express the field).

No KiCad: the adapter is given a stand-in board with a `document` and a `client`
whose `send` records the command and answers exactly like kipy's Board does.
"""
import pytest
from google.protobuf.any_pb2 import Any

from kipy.common_types import Commit
from kipy.proto.common.commands.editor_commands_pb2 import (
    BeginCommit,
    BeginCommitResponse,
    CommitAction,
    CreateItems,
    EndCommit,
    EndCommitResponse,
)
from kipy.proto.common.types.base_types_pb2 import DocumentSpecifier, ItemHeader
# Exported by the package, the same way the adapter's own crash-risk check reads
# it (kicadstamp/kicad/adapter.py::check_write_crash_risk).
from kipy.proto.common.types import DocumentType

from kicadstamp.kicad.adapter import KiCadBoardAdapter, _attach_document_header


DOC = DocumentSpecifier()
DOC.type = DocumentType.DOCTYPE_PCB
DOC.board_filename = "/tmp/probe/board.kicad_pcb"


# ── wire helpers (independent of the adapter's own encoder) ──────────────────

def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def _header_bytes(field_number: int, document) -> bytes:
    """The length-delimited encoding of `ItemHeader(document)` at `field_number`."""
    header = ItemHeader()
    header.document.CopyFrom(document)
    payload = header.SerializeToString()
    return _varint((field_number << 3) | 2) + _varint(len(payload)) + payload


def _field_payload(raw: bytes, field_number: int) -> bytes | None:
    """The raw payload of one field, read straight off the wire — the only way
    to see a field the generated class does not know."""
    offset = 0
    while offset < len(raw):
        tag, offset = _read_varint(raw, offset)
        number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 2:
            length, offset = _read_varint(raw, offset)
            payload = raw[offset:offset + length]
            offset += length
            if number == field_number:
                return payload
        elif wire_type == 0:
            _, offset = _read_varint(raw, offset)
        elif wire_type == 5:
            offset += 4
        elif wire_type == 1:
            offset += 8
        else:                     # pragma: no cover - a wire type we never write
            raise AssertionError(f"unexpected wire type {wire_type}")
    return None


def _document_from_field(raw: bytes, field_number: int):
    payload = _field_payload(raw, field_number)
    if payload is None:
        return None
    header = ItemHeader()
    header.ParseFromString(payload)
    return header.document


# ── the stand-in board ───────────────────────────────────────────────────────

class _Client:
    """Stands in for kipy's KiCadClient: records what was sent and answers the
    two transaction responses the way kipy's Board does."""

    def __init__(self, commit_id: str = "11111111-2222-3333-4444-555555555555"):
        self.sent: list[tuple] = []
        self.commit_id = commit_id

    def send(self, command, response_type):
        self.sent.append((command, response_type))
        if response_type is BeginCommitResponse:
            response = BeginCommitResponse()
            response.id.value = self.commit_id
            return response
        return EndCommitResponse()


class _Board:
    """The public surface the adapter's transaction methods use: `document` and
    `client` (both public properties of kipy's Board)."""

    def __init__(self, document=DOC):
        self.document = document
        self.client = _Client()


def _adapter(document=DOC) -> KiCadBoardAdapter:
    """An adapter built WITHOUT kipy.KiCad() — the transaction methods only need
    the board handle, and this keeps the test offline."""
    adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)
    adapter._board = _Board(document)
    adapter._write_risk_checked = True
    return adapter


# ── Т1.3: the six guards ─────────────────────────────────────────────────────

def test_begin_commit_sends_the_document_header():
    """The BeginCommit that goes over the wire is EXACTLY the headerless command
    plus `field 1 = ItemHeader(document)` — byte for byte, assembled here."""
    adapter = _adapter()
    commit = adapter.begin_commit()

    assert len(adapter._board.client.sent) == 1
    command, response_type = adapter._board.client.sent[0]
    assert response_type is BeginCommitResponse
    assert isinstance(command, BeginCommit)
    expected = BeginCommit()
    expected.MergeFromString(_header_bytes(1, DOC))
    assert command.SerializeToString() == expected.SerializeToString()
    # …and the document reads back off the wire as the board's own document.
    assert _document_from_field(command.SerializeToString(), 1) == DOC
    # The caller gets the same return type as before (kipy's Commit).
    assert isinstance(commit, Commit)


def test_push_commit_sends_the_document_header_with_the_commit_fields():
    """EndCommit carries id/action/message as always AND `field 4 = the header`;
    the ordinary fields stay readable through the generated API."""
    adapter = _adapter()
    commit = adapter.begin_commit()
    adapter.push_commit(commit, "stage one")

    command, response_type = adapter._board.client.sent[1]
    assert response_type is EndCommitResponse
    assert isinstance(command, EndCommit)
    assert command.action == CommitAction.CMA_COMMIT
    assert command.message == "stage one"
    assert command.id == commit.id

    expected = EndCommit()
    expected.id.CopyFrom(commit.id)
    expected.action = CommitAction.CMA_COMMIT
    expected.message = "stage one"
    expected.MergeFromString(_header_bytes(4, DOC))
    assert command.SerializeToString() == expected.SerializeToString()
    assert _document_from_field(command.SerializeToString(), 4) == DOC


def test_drop_commit_sends_the_document_header_with_cma_drop():
    adapter = _adapter()
    commit = adapter.begin_commit()
    adapter.drop_commit(commit)

    command, response_type = adapter._board.client.sent[1]
    assert response_type is EndCommitResponse
    assert command.action == CommitAction.CMA_DROP
    assert command.message == ""
    assert _document_from_field(command.SerializeToString(), 4) == DOC


def test_the_header_survives_packing_into_any():
    """A future protobuf that starts dropping unknown fields would still keep
    them through Any.Pack — which is how kipy ships every command, so the field
    must be present in the packed value too."""
    adapter = _adapter()
    adapter.begin_commit()
    command, _response_type = adapter._board.client.sent[0]

    packed = Any()
    packed.Pack(command)
    assert _document_from_field(packed.value, 1) == DOC
    # …and the raw payload inside Any is byte-identical to the standalone one.
    assert _field_payload(packed.value, 1) == _field_payload(
        command.SerializeToString(), 1)


def test_a_command_that_does_know_the_field_uses_it_natively():
    """Р2: when kipy learns the field, the helper fills it through the generated
    API — the hand-rolled field number is then NOT used at all (CreateItems has
    a real `header`, and the deliberately wrong number here must not appear)."""
    command = CreateItems()
    _attach_document_header(command, 99, DOC)

    assert command.header.document == DOC
    raw = command.SerializeToString()
    assert _field_payload(raw, 99) is None
    # CreateItems.header is field 1 — filled natively, not by our encoder.
    assert _document_from_field(raw, 1) == DOC


def test_commit_with_retry_reaches_push_commit_and_returns_true():
    """End to end on the stand-in board: the commit id type stays compatible
    with kipy's Commit, and the whole begin/work/push round trip works."""
    adapter = _adapter()
    seen = []
    assert adapter.commit_with_retry("one step", lambda: seen.append("work")) is True

    assert seen == ["work"]
    commands = [cmd for cmd, _resp in adapter._board.client.sent]
    assert [type(cmd) for cmd in commands] == [BeginCommit, EndCommit]
    assert commands[1].action == CommitAction.CMA_COMMIT
    assert _document_from_field(commands[0].SerializeToString(), 1) == DOC
    assert _document_from_field(commands[1].SerializeToString(), 4) == DOC


def test_the_document_is_read_at_call_time_not_cached():
    """The docstring promise: the document comes from the CURRENT board handle
    (refresh_board replaces it), so a new board's document is picked up without
    any re-initialisation."""
    adapter = _adapter()
    other = DocumentSpecifier()
    other.type = DocumentType.DOCTYPE_PCB
    other.board_filename = "/tmp/other/board.kicad_pcb"
    adapter._board = _Board(other)

    adapter.begin_commit()
    command, _resp = adapter._board.client.sent[0]
    assert _document_from_field(command.SerializeToString(), 1) == other
    assert _document_from_field(command.SerializeToString(), 1) != DOC
