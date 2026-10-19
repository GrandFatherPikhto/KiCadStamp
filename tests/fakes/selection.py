"""Raw ``GetSelection`` answers for adapter-level selection tests.

The adapter reads the editor selection through kiCad's ``GetSelection`` command
and unwraps the ``google.protobuf.Any`` items ONE at a time (see
``KiCadBoardAdapter._read_raw_selection``). A ``MagicMock`` cannot represent the
one shape that matters here — an EMPTY ``type_url``, which is what KiCad returns
for every DRC marker (``PCB_MARKER``) in the selection — so these helpers build
REAL protobuf messages:

* :func:`packed_footprint` — a readable ``FootprintInstance`` carrying a ref;
* :func:`unreadable_item` — the empty ``Any`` a DRC marker arrives as;
* :func:`unknown_type_item` — a ``type_url`` kipy has no mapping for (KiCad
  newer than the kipy it was generated against);
* :func:`selection_response` — the ``SelectionResponse`` the fake client sends
  back;
* :func:`board_answering` — a fake kipy ``Board`` wired to that response, with
  the one private seam the adapter uses (``_to_concrete_items``) standing in as
  the identity conversion.

Read-only test data: nothing here touches a live KiCad.
"""
from unittest.mock import MagicMock

from google.protobuf.any_pb2 import Any as ProtoAny

from kipy.proto.board import board_types_pb2
from kipy.proto.common.commands.editor_commands_pb2 import SelectionResponse
from kipy.proto.common.types.base_types_pb2 import DocumentSpecifier
from kipy.util.proto import pack_any

# A type_url kipy 0.8.0 has no entry for in kipy.util.proto._any_urls — the
# shape a newer KiCad would produce for a type this kipy predates.
UNKNOWN_TYPE_URL = "type.googleapis.com/kiapi.common.types.FutureBoardItem"


def packed_footprint(ref: str) -> ProtoAny:
    """A readable ``FootprintInstance`` item whose reference field says *ref*.

    The proto nesting is kipy's own: FootprintInstance.reference_field is a
    ``Field`` whose ``text`` is a ``BoardText`` over a common ``Text`` (whose
    string field is ``text``) — the path ``Field.text.value`` reads through the
    wrappers in ``kicadstamp.domain.board.footprint_from_kipy``."""
    proto = board_types_pb2.FootprintInstance()
    proto.id.value = f"uuid-{ref}"
    proto.reference_field.text.text.text = ref
    return pack_any(proto)


def unreadable_item() -> ProtoAny:
    """The empty Any a DRC marker comes back as: no ``type_url`` at all."""
    return ProtoAny()


def unknown_type_item() -> ProtoAny:
    """A non-empty ``type_url`` kipy cannot unpack."""
    return ProtoAny(type_url=UNKNOWN_TYPE_URL)


def selection_response(*items: ProtoAny) -> SelectionResponse:
    response = SelectionResponse()
    response.items.extend(items)
    return response


def board_answering(*items: ProtoAny) -> MagicMock:
    """A fake kipy ``Board`` whose client answers ``GetSelection`` with *items*.

    ``document`` must be a real ``DocumentSpecifier`` because the adapter does
    ``command.header.document.CopyFrom(board.document)``; ``_to_concrete_items``
    is the identity here — concrete-shape conversion is kipy's own business and
    is exercised by kipy, not by this seam."""
    board = MagicMock()
    board.document = DocumentSpecifier()
    board.client.send.return_value = selection_response(*items)
    board._to_concrete_items.side_effect = lambda items: list(items)
    return board
