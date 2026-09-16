#!/usr/bin/env python3
"""Watchdogs for the per-generation footprint field map (adapter.py's
``_field_values_for``).

Context — measured 2026-09-13 (plan_2026_09_13_placement_field_scan_cost, Э1):
``get_field_value()`` re-scanned a footprint's whole ``texts_and_fields`` list on
every single call. One 20-item apply over the 325-footprint 3ch-awg-tia-v103
board called it 52170 times — about 160 full passes over the whole board's
fields — costing 2.2 s of a 6.2 s run and ~2M ``builtins.isinstance`` calls.
The map answers from a dict built once per footprint per cache generation.

The trap this file exists for (P.1.2 of that plan): ``set_field_value()`` writes
``item.text.value`` IN PLACE, on the very ``Field`` object the map was built
from. A naive ``{field -> value}`` map therefore keeps serving the PRE-write
value to the next read — silently, with no exception, and with wrong
Role/Cluster tagging as the result. Э4.1 is that watchdog (without it the
optimization is not acceptable), Э4.2 covers the bulk path real tagging walks.

Style: ``Adapter.__new__(Adapter)`` (no real ``kipy.KiCad()`` connection) over a
stub board, the same way tests/test_kicad.py's TestFootprintsCache does it. The
stub footprints go through the REAL ``footprint_from_kipy()``, so the map's uuid
keys are the real thing.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kipy.board_types import BoardText, Field
from kipy.proto.common.commands.editor_commands_pb2 import BeginCommitResponse
from kipy.proto.common.types.base_types_pb2 import DocumentSpecifier

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.domain.board import unwrap
from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter


class _TextlessField(Field):
    """A Field that reports no text at all.

    kipy 0.7.1's ``Field.text`` ALWAYS hands back a BoardText wrapper — even a
    bare ``Field()`` gives ``value=''`` (checked against the installed
    kipy) — so the legacy scan's ``if item.text else None`` branch is only
    reachable through a field shaped like this. That makes it the one case
    where "field present" really maps to a None VALUE, i.e. the case the
    map's key-presence distinction exists for (P.1.5).
    """

    @property
    def text(self):
        return None


class _StubKipyFootprint:
    """Minimal stand-in for kipy's FootprintInstance: exactly the attributes
    ``footprint_from_kipy()`` reads, plus a counting ``texts_and_fields``
    property so a test can prove the field list is scanned once per footprint
    per generation instead of once per read (that count IS the optimization —
    the numbers above are what it buys)."""

    def __init__(self, ref, uuid_str, fields=(), plain_texts=(), raw_items=()):
        self.id = SimpleNamespace(value=uuid_str)
        self.reference_field = SimpleNamespace(text=SimpleNamespace(value=ref))
        self.value_field = None
        self.position = SimpleNamespace(x=0, y=0)
        self.orientation = SimpleNamespace(degrees=0.0)
        self.layer = "F.Cu"
        self.sheet_path = SimpleNamespace(path=[])
        self._items = []
        for name, value in fields:
            field = Field()
            field.name = name
            field.text.value = value
            self._items.append(field)
        self._items.extend(plain_texts)
        self._items.extend(raw_items)
        self.texts_and_fields_reads = 0

    @property
    def texts_and_fields(self):
        self.texts_and_fields_reads += 1
        return self._items


def _adapter(kipy_footprints):
    """Adapter wired to a stubbed board that serves `kipy_footprints` — the
    live-object side of a normal get_footprints()/refresh_board() cycle.

    Two of the stub's attributes must be REAL protos rather than Mock children
    since plan_2026_09_16_commit_document_and_pending_direction Э1: the
    transaction commands are built by the adapter itself (KiCad 10.0.7 needs the
    document inside BeginCommit/EndCommit, kicadstamp/kicad/adapter.py::
    _attach_document_header), so a write goes through board.client.send and the
    board must hand over a document to serialise and a response whose id is a
    KIID. Nothing this file is ABOUT — the field map and its cache generation —
    is affected by that."""
    adapter = Adapter.__new__(Adapter)
    adapter._board = MagicMock()
    adapter._board.get_footprints.return_value = list(kipy_footprints)
    adapter._board.document = DocumentSpecifier()
    _response = BeginCommitResponse()
    _response.id.value = "11111111-2222-3333-4444-555555555555"
    adapter._board.client.send.return_value = _response
    adapter._footprints_cache = None
    adapter._field_values_cache = None
    adapter._write_risk_checked = True  # skip check_write_crash_risk's IPC call
    return adapter


def _legacy_scan(dto_footprint, field_name):
    """The pre-2026-09-13 get_field_value() body, verbatim. The map has to
    reproduce it exactly, values included — and "present but empty" is NOT
    None for a real kipy Field (item.text is a wrapper object; only a
    genuinely absent field yields None), which is why this is compared against
    rather than assumed."""
    for item in unwrap(dto_footprint).texts_and_fields:
        if isinstance(item, Field) and item.name == field_name:
            return item.text.value if item.text else None
    return None


def _write_behind_the_adapters_back(kipy_fp, name, value):
    """Change a field the way a foreign writer would (KiCad's own edit dialog,
    another tool): straight on the live kipy object, with the adapter never
    being told. Only an invalidated map may report the new value afterwards."""
    for item in kipy_fp.texts_and_fields:
        if isinstance(item, Field) and item.name == name:
            item.text.value = value
            return
    raise AssertionError(f"stub footprint has no field {name!r}")


class TestWriteIsVisibleToTheVeryNextRead:
    """Э4.1 (the personal watchdog) and Э4.2 — the two paths that make the map
    dangerous: an in-place write must never be shadowed by its own cache."""

    def test_set_field_value_then_get_field_value_returns_the_new_value(self):
        kipy_fp = _StubKipyFootprint("R1", "uuid-R1", [("Role", "OLD_ROLE")])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]

        # Build the map from the old value (this is the read that primes it).
        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        adapter.set_field_value(fp, "Role", "NEW_ROLE")

        assert adapter.get_field_value(fp, "Role") == "NEW_ROLE"

    def test_set_field_values_bulk_then_get_field_value_returns_the_new_value(self):
        """set_field_values_bulk is how real Role/Cluster tagging writes — the
        path where a stale map would silently resolve against old tags."""
        kipy_fp = _StubKipyFootprint(
            "R1", "uuid-R1", [("Role", "OLD_ROLE"), ("Cluster", "OLD_CLUSTER")])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]

        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        adapter.set_field_values_bulk(
            [(fp, "Role", "NEW_ROLE"), (fp, "Cluster", "NEW_CLUSTER")], "test")

        assert adapter.get_field_value(fp, "Role") == "NEW_ROLE"
        assert adapter.get_field_value(fp, "Cluster") == "NEW_CLUSTER"

    def test_a_write_drops_only_that_footprints_entry(self):
        """The drop is per-footprint, not a whole-generation reset: during
        tagging only the written footprints should pay for a rebuild."""
        c1 = _StubKipyFootprint("C1", "uuid-C1", [("Role", "R1")])
        c2 = _StubKipyFootprint("C2", "uuid-C2", [("Role", "R2")])
        adapter = _adapter([c1, c2])
        fp1, fp2 = adapter.get_footprints()

        adapter.get_field_value(fp1, "Role")
        adapter.get_field_value(fp2, "Role")
        scans_of_c2 = c2.texts_and_fields_reads

        adapter.set_field_value(fp1, "Role", "NEW")

        assert adapter.get_field_value(fp2, "Role") == "R2"
        assert c2.texts_and_fields_reads == scans_of_c2  # untouched neighbour


class TestInvalidationWithTheFootprintCache:
    """Э4.3/Э4.4 — the map derives from the same kipy objects as
    _footprints_cache, so it must die wherever that cache dies (P.1.1)."""

    def test_refresh_board_drops_the_map(self):
        kipy_fp = _StubKipyFootprint("R1", "uuid-R1", [("Role", "OLD_ROLE")])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]
        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        _write_behind_the_adapters_back(kipy_fp, "Role", "CHANGED_BEHIND_BACK")
        # Negative control: a map that is genuinely caching still reports the
        # old value — otherwise this test could pass with no map at all.
        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        adapter._kicad = MagicMock()
        adapter._kicad.get_board.return_value = adapter._board
        adapter.refresh_board()

        new_fp = adapter.get_footprints()[0]
        assert adapter.get_field_value(new_fp, "Role") == "CHANGED_BEHIND_BACK"

    def test_flip_selected_drops_the_map(self):
        """flip_selected() is the second place that resets _footprints_cache —
        found live 2026-07-29 as a stale-cache bug (components back on F.Cu),
        so the map is dropped there too."""
        kipy_fp = _StubKipyFootprint("C1", "uuid-C1", [("Role", "OLD_ROLE")])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]
        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        _write_behind_the_adapters_back(kipy_fp, "Role", "CHANGED_BEHIND_BACK")

        adapter._kicad = MagicMock()
        adapter.flip_selected([fp])

        assert adapter.get_field_value(fp, "Role") == "CHANGED_BEHIND_BACK"


class TestSemanticsPreserved:
    """Э4.5/Э4.6 — the two readings the map must not "simplify" away."""

    def test_absent_and_empty_fields_stay_distinguishable(self):
        """has_field must tell "no such field" from "field present" AFTER the
        map exists — the map may store a None VALUE for the latter too, so the
        difference has to live in KEY PRESENCE (P.1.5)."""
        textless = _TextlessField()
        textless.name = "Role"
        absent = _StubKipyFootprint("R1", "uuid-R1", [("Cluster", "C1")])
        empty = _StubKipyFootprint("R2", "uuid-R2", [("Role", "")])
        without_text = _StubKipyFootprint("R3", "uuid-R3", raw_items=[textless])
        adapter = _adapter([absent, empty, without_text])
        fps = adapter.get_footprints()

        # Prime the map on every footprint first: everything asserted below is
        # then answered from the cache, which is the point of the guard. Values
        # must match the pre-optimization scan byte for byte.
        for fp in fps:
            assert adapter.get_field_value(fp, "Role") == _legacy_scan(fp, "Role")

        assert adapter.get_field_value(fps[0], "Role") is None
        assert adapter.has_field(fps[0], "Role") is False           # no field

        assert adapter.has_field(fps[1], "Role") is True            # field, empty
        assert adapter.has_field(fps[2], "Role") is True            # field, no text
        assert adapter.get_field_value(fps[2], "Role") is None      # ...still None

        # A present (empty) field is writable; an absent one stays a fatal,
        # which is the whole reason has_field is consulted before a batch
        # write.
        adapter.set_field_value(fps[1], "Role", "MCU")
        assert adapter.get_field_value(fps[1], "Role") == "MCU"

    def test_plain_board_text_in_texts_and_fields_does_not_break_the_map(self):
        """texts_and_fields mixes real Fields with plain BoardText (silkscreen
        text that has no .name at all) — the isinstance filter the old scan had
        is now the map builder's, and it must survive the move."""
        kipy_fp = _StubKipyFootprint(
            "R1", "uuid-R1", [("Role", "MCU")], plain_texts=[BoardText()])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]

        assert adapter.get_field_value(fp, "Role") == "MCU"
        assert adapter.has_field(fp, "Role") is True
        assert adapter.get_field_value(fp, "Cluster") is None


class TestCacheKeysAndCost:
    """The map's own contract: keyed by uuid (not ref), one scan per footprint
    per generation."""

    def test_footprints_sharing_a_ref_do_not_share_an_entry(self):
        """uuid is the key precisely because ref is not stable — two footprints
        carrying the same ref (re-annotation in flight) must not collide."""
        a = _StubKipyFootprint("U1", "uuid-A", [("Role", "ROLE_A")])
        b = _StubKipyFootprint("U1", "uuid-B", [("Role", "ROLE_B")])
        adapter = _adapter([a, b])

        fps = adapter.get_footprints()

        assert [adapter.get_field_value(fp, "Role") for fp in fps] == ["ROLE_A", "ROLE_B"]

    def test_footprint_without_uuid_is_answered_uncached_not_wrongly(self):
        """No uuid = no stable key: such a footprint is scanned per read rather
        than risking two of them sharing one entry."""
        a = _StubKipyFootprint("R1", "", [("Role", "ROLE_A")])
        b = _StubKipyFootprint("R2", "", [("Role", "ROLE_B")])
        adapter = _adapter([a, b])

        fps = adapter.get_footprints()

        assert [adapter.get_field_value(fp, "Role") for fp in fps] == ["ROLE_A", "ROLE_B"]

    def test_fields_are_scanned_once_per_footprint_per_generation(self):
        """The optimization itself: 60 reads (Role, Cluster, has_field x20)
        cost ONE pass over the field list; a write plus the read that follows
        it cost two more (the write's own scan, then one rebuild)."""
        kipy_fp = _StubKipyFootprint(
            "R1", "uuid-R1", [("Role", "MCU"), ("Cluster", "C1")])
        adapter = _adapter([kipy_fp])
        fp = adapter.get_footprints()[0]

        for _ in range(20):
            adapter.get_field_value(fp, "Role")
            adapter.get_field_value(fp, "Cluster")
            adapter.has_field(fp, "Role")

        assert kipy_fp.texts_and_fields_reads == 1

        adapter.set_field_value(fp, "Role", "NEW")
        adapter.get_field_value(fp, "Role")

        assert kipy_fp.texts_and_fields_reads == 3
