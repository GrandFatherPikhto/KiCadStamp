# tests/test_board_project_identity.py
"""Cells П1-П4 and П6 of plan_2026_09_24_project_identity_from_ipc.

Numbering belongs to the PLAN, not to this file (rule 37): each docstring names the
plan and its cell, and the function names describe the PROPERTY, carrying no number.

What the entry is about. NO answer of the footprint-reading MCP tools named the board
it described: one and the same `get_footprint("J6")` answered confidently first about
3CH-AWG-TIA-v103 and then about HiPiMS-v099, and the two answers were told apart only
by uuid - by coordinates a wrong answer is indistinguishable from a right one (М8 of
design_2026_09_24_project_identity_and_kicad_pro). The remedy has two halves: the
project the IPC already carries (М5), raised to the adapter, and an envelope that
signs every answer at the TOOLS layer - including the EMPTY ones, because a wrong
"nothing matched" reads as a legitimate answer, which is the quietest form of the same
defect.

The price is part of the contract and is asserted as a PROPERTY here (rule 35), not as
a one-off number: reading the identity must cost the board nothing, or the remedy would
hang a full board read on every call of every enveloped tool. The cells below use the
same TWO fuses as the live probe - counting AT THE KIPY BOUNDARY (counting adapter
calls reads zero where the board was read; the Ш1 lesson) and a POSITIVE CONTROL, so a
zero produced by a broken check cannot pass for a measurement (rule 39).

Where the other cells live: П2's identity half is the extended pin in
tests/test_mcp_handlers.py::test_board_identity_connected (extended deliberately,
agreed 24.09.2026, equality preserved), and П5 is an EXISTING guard
(tests/test_mcp_error_contract.py::test_importing_the_seam_does_not_pull_kipy) which
this entry re-runs instead of duplicating under a new number.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from kicadstamp.domain.board import Footprint, Track
from kicadstamp.domain.geometry import BoardLayer, Vector2

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from mcp_server import handlers
from mcp_server.server import build_server

BOARD = "3CH-AWG-TIA-v103.kicad_pcb"
PROJECT = ("3CH-AWG-TIA-v103",
           "/home/denis/Projects/KiCad/3CH-AWG-TIA/3CH-AWG-TIA-v103")


@pytest.fixture(autouse=True)
def _isolate_gui_settings(tmp_path, monkeypatch):
    """Point the MCP server's raw-write gate at a throwaway gui_state.json.

    Same discipline as tests/test_mcp_server.py: build_server() reads the GUI store,
    and no test may read or touch the developer's real one."""
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "SETTINGS_PATH", tmp_path / "gui_state.json")


# --- stands -------------------------------------------------------------------

def _adapter_over_a_real_document(board_filename=BOARD, project=PROJECT):
    """A REAL KiCadBoardAdapter (via ``__new__``) over a REAL DocumentSpecifier.

    No mocks on the value under test: П1 is about what the adapter reads off the
    protobuf MESSAGE, so the message is kipy's own class. ``__new__`` bypasses
    ``__init__`` (which would build a live ``kipy.KiCad()``) - the same style
    tests/test_kicad.py already uses for adapter-level cells.
    """
    from kipy.proto.common.types import DocumentSpecifier

    document = DocumentSpecifier()
    document.board_filename = board_filename
    if project is not None:
        document.project.name, document.project.path = project
    board = SimpleNamespace(name=board_filename, document=document)
    adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)
    adapter._board = board
    return adapter


class _FakeAdapter:
    """The adapter surface the identity envelope and the three reading tools use.

    Duck-typed on purpose: the envelope lives at the TOOLS layer, so all it needs is
    "an adapter-shaped object" - exactly what the manager hands it."""

    def __init__(self, footprints=(), tracks=(), board_name=BOARD, project=PROJECT):
        self._footprints = list(footprints)
        self._tracks = list(tracks)
        self._board_name = board_name
        self._project = project

    def refresh_board(self):
        pass

    def get_board_filename(self):
        return self._board_name

    def get_board_project(self):
        return self._project

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_field_value(self, fp, name):
        return None

    def get_footprint_pads(self, fp):
        return []

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return []


def _fp(ref):
    return Footprint(ref=ref, uuid="uuid-" + ref,
                     position=Vector2(x=1_500_000, y=2_500_000),
                     angle_deg=0.0, layer=BoardLayer.BL_F_Cu, value="VAL-" + ref)


def _payload(server, tool, arguments=None):
    """Call a tool the way an MCP client does and DECODE the JSON it answers with.

    Decoded rather than grepped: these cells are about the SHAPE of the answer (which
    keys, nested how), and a substring assertion cannot tell a signed answer from an
    unsigned one. М8 is exactly the story of a check that could not tell them apart."""
    result = asyncio.run(server.call_tool(tool, arguments or {}))
    text = "".join(getattr(c, "text", "") or "" for c in result.content)
    assert result.is_error is not True, text
    return json.loads(text) if text else None


# --- П1: the adapter answers both halves of the project, or nothing ------------

@pytest.mark.parametrize("project,expected", [
    (("proj", "/tmp/proj"), ("proj", "/tmp/proj")),
    (("", "/tmp/proj"), None),
    (("proj", ""), None),
    (("", ""), None),
], ids=["both-present", "empty-name", "empty-dir", "both-empty"])
def test_adapter_answers_both_halves_of_the_project_or_nothing(project, expected):
    """П1 of plan_2026_09_24_project_identity_from_ipc (RED on the base commit: the
    method does not exist there at all).

    The table is field x direction (rule 35): both halves present -> the pair; a
    HALF-empty project -> "nothing" and not a half-answer. Half an identity answers
    nothing about the one question this value exists for ("which board is this
    about"), and the project-less board could not be reproduced live at all (Т1(д)) -
    which is why the getter is written DEFENSIVELY and its docstring carries the
    "not verified live" note instead of a claim."""
    adapter = _adapter_over_a_real_document(project=project)
    assert adapter.get_board_project() == expected


def test_a_board_without_a_project_answers_nothing_like_a_missing_board():
    """П3 of plan_2026_09_24_project_identity_from_ipc: "nothing to say" is the getter's
    answer for a missing project, the same way get_board_filename() answers for a
    missing board - callers must never have to know two contracts.

    The two halves are asserted TOGETHER on purpose: the board here really IS present
    (its filename answers), only the project is not, and that is the distinction the
    defensive branch has to keep."""
    adapter = _adapter_over_a_real_document(project=None)
    assert adapter.get_board_project() is None
    assert adapter.get_board_filename() == BOARD


def test_no_board_at_all_answers_nothing_and_raises_nothing():
    """П3, the other direction: with no board handle both halves of the identity answer
    "nothing" and neither raises."""
    adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)
    adapter._board = None
    assert adapter.get_board_project() is None
    assert adapter.get_board_filename() is None


# --- П2: every answer names the board it describes -----------------------------

def test_list_footprints_answer_is_signed_with_the_board():
    """П2 of plan_2026_09_24_project_identity_from_ipc (RED on the base commit: the
    answer is a bare list and carries no identity).

    П6 rides along in the same cell, because the two are the same assertion: the
    envelope is ADDED around the records, and the records keep the keys they had."""
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(footprints=[_fp("R1")]))
    answer = _payload(server, "kicadstamp_list_footprints")
    assert answer["board"] == {
        "board_name": BOARD,
        "project": {"name": PROJECT[0], "path": PROJECT[1]},
    }
    assert [fp["ref"] for fp in answer["footprints"]] == ["R1"]
    assert set(answer["footprints"][0]) == {
        "ref", "role", "cluster", "x_mm", "y_mm", "rotation_deg", "layer",
    }


def test_list_footprints_empty_answer_is_signed_too():
    """П2, the QUIET half: nothing matched, and the answer still says WHICH BOARD said
    so.

    This is the case that made the envelope live at the tools layer instead of inside
    the record briefs: on the base commit an empty list serialises to an EMPTY response
    (pinned by tests/test_mcp_server.py::test_dispatch_list_tracks_no_match_returns_
    empty_list), so a "nothing matched" about the wrong board is indistinguishable
    from a right one - a wrong "not found" reads as a legitimate answer."""
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(footprints=[_fp("U1")]))
    answer = _payload(server, "kicadstamp_list_footprints", {"ref_prefix": "J"})
    assert answer["footprints"] == []
    assert answer["board"]["board_name"] == BOARD


def test_get_footprint_answer_is_signed_with_the_board():
    """П2, third tool: the single-footprint answer names the board too (this is the
    very call М8 used, and the one whose coordinates could not be attributed)."""
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(footprints=[_fp("U1")]))
    answer = _payload(server, "kicadstamp_get_footprint", {"ref": "U1"})
    assert answer["board"]["board_name"] == BOARD
    assert answer["footprint"]["ref"] == "U1"
    assert answer["footprint"]["uuid"] == "uuid-U1"


def test_get_items_by_uuid_answer_is_signed_with_the_board():
    """П2, the third reading tool (all three are checked - a half-applied remedy is
    worse than none, because it creates false trust)."""
    track = Track(uuid="t-1", start=Vector2(0, 0), end=Vector2(1_000_000, 0),
                  net_name="GND", width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(tracks=[track]))
    answer = _payload(server, "kicadstamp_get_items_by_uuid", {"uuids": ["t-1"]})
    assert answer["board"]["board_name"] == BOARD
    assert answer["items"][0]["uuid"] == "t-1"


def test_get_items_by_uuid_all_missing_answer_is_signed_too():
    """П2: every uuid missing - the answer still names the board it looked on."""
    server = build_server(adapter_factory=lambda ms: _FakeAdapter())
    answer = _payload(server, "kicadstamp_get_items_by_uuid", {"uuids": ["a", "b"]})
    assert [item["found"] for item in answer["items"]] == [False, False]
    assert answer["board"]["board_name"] == BOARD


@pytest.mark.parametrize("board_name", ["HiPiMS-v099.kicad_pcb", BOARD])
def test_the_envelope_follows_the_adapter_it_answered_from(board_name):
    """П2: the envelope names the board the PAYLOAD came from - never a remembered one.

    Parametrised over two boards because that is the whole point: the same tool called
    against two different boards must not be able to answer with the other one's
    identity. A cell that only ever saw one board would pass on an implementation that
    returns a constant."""
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(
        footprints=[_fp("J6")], board_name=board_name))
    answer = _payload(server, "kicadstamp_get_footprint", {"ref": "J6"})
    assert answer["board"]["board_name"] == board_name


# --- П4: the identity costs the board NOTHING (property, rule 35) -------------

class _NeverTouchTheKicadClient:
    """An object that raises on ANY attribute access.

    П4's requirement expressed as a PROPERTY rather than as a number: if the identity
    read touches the client AT ALL - a document query, a version, a refresh - the cell
    fails, and no cache can hide it (the adapter caches, so counting ADAPTER calls
    reads zero exactly where the board was read; the Ш1 lesson)."""

    def __getattr__(self, name):
        raise AssertionError(
            f"the identity read reached the kipy client (attribute {name!r}) - "
            f"П4 of plan_2026_09_24_project_identity_from_ipc says it must not")


class _KipyBoundaryCounter:
    """Counts round trips where they happen: kipy's own functions, not the adapter's.

    The same instrument as kicadstamp.diagnostics.probe_project_identity, for the same
    reason - and with the same obligation: it needs a POSITIVE CONTROL, because a
    counter that reads zero on everything proves nothing (rule 39)."""

    def __init__(self):
        self.total = 0
        self._originals = []

    def __enter__(self):
        import kipy
        from kipy.board import Board

        original_docs = kipy.KiCad.get_open_documents
        original_board_read = Board.get_footprints
        counter = self

        def counting_docs(kicad_self, doc_type):
            counter.total += 1
            return original_docs(kicad_self, doc_type)

        def counting_board_read(board_self, *args, **kwargs):
            counter.total += 1
            return original_board_read(board_self, *args, **kwargs)

        kipy.KiCad.get_open_documents = counting_docs
        Board.get_footprints = counting_board_read
        self._originals = [(kipy.KiCad, "get_open_documents", original_docs),
                           (Board, "get_footprints", original_board_read)]
        return self

    def __exit__(self, *exc_info):
        for owner, name, original in self._originals:
            setattr(owner, name, original)
        self._originals = []
        return False


def test_the_identity_read_never_touches_the_kicad_client():
    """П4, first row: read the identity with the client REPLACED by a sentinel that
    raises on any attribute access, and demand the answer anyway."""
    adapter = _adapter_over_a_real_document()
    adapter._kicad = _NeverTouchTheKicadClient()
    assert handlers.board_brief(adapter) == {
        "board_name": BOARD,
        "project": {"name": PROJECT[0], "path": PROJECT[1]},
    }


def test_the_identity_cost_counted_at_the_kipy_boundary_is_zero():
    """П4, second row: the plan's own instrument - count AT THE KIPY BOUNDARY, not by
    adapter calls, because the adapter caches (the Ш1 lesson quoted in
    refresh_board_before_live_read's docstring).

    The positive control is not decoration: without it a typo in the patch would make
    the zero above look like a measurement, and the price this cell exists for (a full
    board read on every enveloped tool call) would go unnoticed - which is how the
    defect this entry fixes stayed unnoticed in the first place."""
    adapter = _adapter_over_a_real_document()
    adapter._kicad = _NeverTouchTheKicadClient()
    with _KipyBoundaryCounter() as counter:
        handlers.board_brief(adapter)
    assert counter.total == 0

    control = _KipyBoundaryCounter()
    with control:
        with pytest.raises(Exception):
            import kipy

            kipy.board.Board.get_footprints(SimpleNamespace())
    assert control.total == 1, (
        "the counter cannot see a boundary call at all - the zero above is a broken "
        "check, not a reading")


def test_the_envelope_carries_no_version_round_trip():
    """П4, third row: ``get_version()`` IS a real IPC round trip, so it must stay OUT of
    the envelope - with it inside, every call of every enveloped tool would pay a
    network round trip for a field most of them do not even show.

    Asserted as the payload's exact key set (the shape, not a comment), with the kipy
    client replaced by the raising sentinel so the price cannot be paid quietly."""
    adapter = _adapter_over_a_real_document()
    adapter._kicad = _NeverTouchTheKicadClient()
    assert set(handlers.board_brief(adapter)) == {"board_name", "project"}
