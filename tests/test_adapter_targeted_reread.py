#!/usr/bin/env python3
"""Watchdogs for the targeted footprint re-read —
``KiCadBoardAdapter.reread_footprints_by_id`` (plan_2026_09_13_targeted_
footprint_reread, Э3.1–Э3.6) plus the duplicate-field-name scan guard (Э4.2).

Context — measured 2026-09-13 by ``kicadstamp.diagnostics.probe_placement_cost``
on the live 325-footprint 3ch-awg-tia-v103 board: Phase 1 of one 20-item apply
called ``refresh_board()`` before EVERY item (apply_pipeline.py), so the next
``get_footprints()`` pulled all 325 footprints over IPC (~110 ms each) to learn
the handful of positions the previous item had changed — 2.39 s of a 4.06 s run,
at 158 moves for the whole run (~8 per item, ~2.5% of the data).

What must NOT change while that becomes cheaper (P.1 of the plan):

* the data still comes from KiCad, never from the local object a move just
  mutated (``move_executor.py:64`` sets ``fp.position = cmd.position`` and only
  then pushes it — that is what we ASKED for, not what KiCad DID);
* ``get_footprints()``'s ORDER is observable (it decides which of two ambiguous
  candidates wins and how error messages list them), so a replacement must be in
  place, never an append/re-sort;
* the field map (``_field_values_cache``) is keyed by uuid and was built from the
  OLD object, so replacing a footprint MUST drop that footprint's map entry.
  Without the drop the map answers with pre-move fields — silently, no
  exception, exactly the disease plan_2026_09_13_placement_field_scan_cost
  closed for writes. Э3.1 is that watchdog.

Style: real kipy ``FootprintInstance`` objects (populated through public setters)
over a stub board, so ``isinstance(item, FootprintInstance)`` — and therefore
``board_item_from_kipy`` → ``footprint_from_kipy`` — is exercised for real, not
faked. That is what lets Э3.5 assert the two read paths produce the same DTO.
"""
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path
from unittest.mock import MagicMock

from kipy.board_types import BoardLayer, Field, FootprintInstance
from kipy.geometry import Vector2

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter


# ── Stubs ────────────────────────────────────────────────────────────────────

def _field(name, value):
    """A real kipy Field carrying a value (see the adapter's own
    set_field_value docstring for why Field objects, not mocks, are used)."""
    f = Field()
    f.name = name
    f.text.value = value
    return f


class _KipyFootprint(FootprintInstance):
    """A REAL kipy FootprintInstance with the counting ``texts_and_fields`` the
    field-map watchdogs need. Everything the converters read (id, reference
    field, definition items, position, layer) is set through kipy's own public
    API, so this object is indistinguishable from one that came off the wire."""

    def __init__(self, ref, uuid_str, fields=(), position=(0, 0)):
        super().__init__()
        self.id.value = uuid_str
        self.reference_field = _field("Reference", ref)
        self.definition.items = [_field(name, value) for name, value in fields]
        self.position = Vector2.from_xy(*position)
        self.layer = BoardLayer.BL_F_Cu
        self.texts_and_fields_reads = 0

    @property
    def texts_and_fields(self):
        # Counts SCANS OF THE FIELD LIST, which is what the map exists to avoid
        # repeating (see tests/test_adapter_field_cache.py).
        self.texts_and_fields_reads += 1
        return super().texts_and_fields


class _StubBoard:
    """The live-board side of both read paths: ``get_footprints()`` is the full
    read, ``get_items_by_id()`` the targeted one (KiCad silently omits ids it
    has no live item for — hence the ``if k.value in self.by_uuid`` filter)."""

    def __init__(self, footprints=()):
        self.footprints = list(footprints)
        # uuid -> kipy object the targeted read answers with. Tests fill this in
        # to control exactly what comes back.
        self.by_uuid = {}
        self.calls_full = 0
        self.calls_by_id = []

    def get_footprints(self):
        self.calls_full += 1
        return list(self.footprints)

    def get_items_by_id(self, kiids):
        self.calls_by_id.append([k.value for k in kiids])
        return [self.by_uuid[k.value] for k in kiids if k.value in self.by_uuid]


def _observable_shape(dto):
    """A DTO's declared fields minus its opaque kipy back-reference.

    ``Footprint`` is a dataclass declared with ``eq=False`` (identity
    comparison, not value comparison — see domain/board.py), so ``==`` cannot be
    used as the "same type and shape" check Э3.5 asks for. This mapping can."""
    return {f.name: getattr(dto, f.name)
            for f in dataclass_fields(dto) if f.name != "_kipy"}


def _adapter(board):
    """Adapter wired to `board` without a real kipy.KiCad() connection — the
    tests/test_adapter_field_cache.py pattern, plus the `_kicad` handle
    refresh_board() needs (so a fallback can be detected)."""
    adapter = Adapter.__new__(Adapter)
    adapter._board = board
    adapter._kicad = MagicMock()
    adapter._kicad.get_board.return_value = board
    adapter._footprints_cache = None
    adapter._field_values_cache = None
    adapter._write_risk_checked = True  # skip check_write_crash_risk's IPC call
    return adapter


# ── Э3.1 — the field map must not survive a replacement ──────────────────────

class TestReplacedFootprintsLoseTheirFieldMap:
    def test_a_replaced_footprint_is_answered_from_the_new_object_not_the_map(self):
        """The trap of this whole feature: the field map is keyed by uuid, so a
        replacement under the SAME uuid would keep answering from the map built
        off the old object — new object, old fields, silently."""
        live = _KipyFootprint("R1", "uuid-R1", [("Role", "OLD_ROLE")])
        board = _StubBoard([live])
        adapter = _adapter(board)
        fp = adapter.get_footprints()[0]
        # Prime the map (this read is what makes the trap reachable at all).
        assert adapter.get_field_value(fp, "Role") == "OLD_ROLE"

        board.by_uuid["uuid-R1"] = _KipyFootprint(
            "R1", "uuid-R1", [("Role", "NEW_ROLE")])

        adapter.reread_footprints_by_id(["uuid-R1"])

        replaced = adapter.get_footprints()[0]
        assert replaced is not fp, "the entry itself must be the fresh object"
        assert adapter.get_field_value(replaced, "Role") == "NEW_ROLE"
        assert adapter.has_field(replaced, "Role") is True

    def test_the_replacement_is_the_only_entry_that_loses_its_map(self):
        """Э3.2 (field side): the drop is per-footprint. A neighbour must keep
        BOTH its cached object and its cached map — not pay for a rescan."""
        a = _KipyFootprint("R1", "uuid-R1", [("Role", "A_ROLE")])
        b = _KipyFootprint("R2", "uuid-R2", [("Role", "B_ROLE")])
        board = _StubBoard([a, b])
        adapter = _adapter(board)
        fp_a, fp_b = adapter.get_footprints()
        assert adapter.get_field_value(fp_a, "Role") == "A_ROLE"
        assert adapter.get_field_value(fp_b, "Role") == "B_ROLE"
        scans_of_b = b.texts_and_fields_reads

        board.by_uuid["uuid-R1"] = _KipyFootprint(
            "R1", "uuid-R1", [("Role", "A_ROLE")])

        adapter.reread_footprints_by_id(["uuid-R1"])

        assert adapter.get_field_value(fp_b, "Role") == "B_ROLE"
        assert b.texts_and_fields_reads == scans_of_b, (
            "a footprint nobody moved must not be rescanned")


# ── Э3.2/Э3.3 — what is replaced, and where it lands ─────────────────────────

class TestOnlyTheNamedFootprintsAreTouchedInPlace:
    def test_unnamed_footprints_keep_their_very_same_objects(self):
        a = _KipyFootprint("R1", "uuid-R1", [("Role", "A_ROLE")])
        b = _KipyFootprint("R2", "uuid-R2", [("Role", "B_ROLE")])
        board = _StubBoard([a, b])
        adapter = _adapter(board)
        fp_a, fp_b = adapter.get_footprints()

        board.by_uuid["uuid-R1"] = _KipyFootprint(
            "R1", "uuid-R1", [("Role", "A_ROLE")])

        adapter.reread_footprints_by_id(["uuid-R1"])

        now = adapter.get_footprints()
        assert now[0] is not fp_a          # the named one IS replaced
        assert now[1] is fp_b              # the other one is untouched in place

    def test_the_order_of_get_footprints_never_changes(self):
        """Э3.3. The order is observable behaviour (ambiguity resolution, error
        listings), so a replacement that appended or re-sorted would silently
        change which candidate wins."""
        from kicadstamp.domain.board import footprint_from_kipy

        live = [
            _KipyFootprint(f"R{i}", f"uuid-R{i}", [("Role", f"ROLE_{i}")])
            for i in range(1, 6)
        ]
        board = _StubBoard(live)
        adapter = _adapter(board)
        before_dtos = adapter.get_footprints()
        before = [fp.ref for fp in before_dtos]

        # A fresh object for the MIDDLE one, at a new position — so an entry
        # that was appended instead of replaced, or lost, shows up twice.
        moved = _KipyFootprint("R3", "uuid-R3", [("Role", "ROLE_3")],
                               position=(30, 30))
        board.by_uuid["uuid-R3"] = moved

        adapter.reread_footprints_by_id(["uuid-R3"])

        after = adapter.get_footprints()
        assert [fp.ref for fp in after] == before
        assert after[2] is not before_dtos[2], (
            "the re-read entry must replace the old object, not be added to it")
        assert after[2]._kipy is moved, "the middle slot holds the RE-READ object"
        assert _observable_shape(after[2]) == _observable_shape(footprint_from_kipy(moved))
        assert [fp.ref for fp in after] == ["R1", "R2", "R3", "R4", "R5"]


# ── Э3.4 — a uuid that does not come back means the whole board is re-read ───

class TestUnansweredUuidsFallBackToAFullRead:
    def test_a_partly_answered_request_is_not_kept_as_partial_truth(self):
        """Э3.4. get_items_by_id silently omits ids with no live item, so "one
        of two came back" is indistinguishable from "one moved and one is
        gone". Patching only what answered would leave the caller reading a
        board that no longer exists."""
        a = _KipyFootprint("R1", "uuid-R1", [("Role", "A")])
        b = _KipyFootprint("R2", "uuid-R2", [("Role", "B")])
        board = _StubBoard([a, b])
        adapter = _adapter(board)
        old = adapter.get_footprints()

        board.by_uuid["uuid-R1"] = _KipyFootprint("R1", "uuid-R1", [("Role", "A2")])
        # The full read would see a board where BOTH changed.
        board.footprints = [
            _KipyFootprint("R1", "uuid-R1", [("Role", "A2")]),
            _KipyFootprint("R2", "uuid-R2", [("Role", "B2")]),
        ]

        adapter.reread_footprints_by_id(["uuid-R1", "uuid-R2"])

        now = adapter.get_footprints()
        assert [fp.ref for fp in now] == ["R1", "R2"]
        assert now[0] is not old[0] and now[1] is not old[1], (
            "neither entry may survive a partial answer")
        assert adapter.get_field_value(now[1], "Role") == "B2"

    def test_an_all_stale_batch_falls_back_to_a_full_read(self):
        """The other way get_items_by_id says "no": an empty result (KiCad's own
        "none of the requested IDs were found or valid" is normalised to [] by
        get_items_by_id — see its docstring)."""
        a = _KipyFootprint("R1", "uuid-R1", [("Role", "A")])
        board = _StubBoard([a])
        adapter = _adapter(board)
        old = adapter.get_footprints()

        board.by_uuid = {}                       # nothing answers anymore
        board.footprints = [_KipyFootprint("R1", "uuid-R1", [("Role", "A2")])]

        adapter.reread_footprints_by_id(["uuid-R1"])

        now = adapter.get_footprints()
        assert now[0] is not old[0]
        assert adapter.get_field_value(now[0], "Role") == "A2"


# ── Х.2 — a uuid the CACHE does not know falls back to a full read ──────────

class TestAnUnknownUuidFallsBackToAFullRead:
    def test_a_uuid_absent_from_the_cache_forces_a_full_board_read(self):
        """Х.2 (plan_2026_09_13_three_tails). The FIRST guard of
        reread_footprints_by_id: a requested uuid that the current cache
        generation does not contain cannot be promised a fresh object, so the
        WHOLE board is re-read instead of a partial substitution.

        Why it is worth pinning even though the pipeline cannot reach it: the
        only caller (_moved_footprint_uuids) takes the uuids from this very
        cache, so "a uuid the cache does not know" does not occur today. But the
        failure mode without the guard is a SILENT skip — nothing is replaced
        and no full read happens, so the next item plans against a stale entry.
        This test drives the adapter directly because the pipeline cannot build
        the case.

        The trap it guards against is subtle: KiCad CAN still answer for the
        uuid (a live item the cache never learned about), so "it came back" is
        not proof a targeted replacement is safe — only that it did not 404."""
        known = _KipyFootprint("R1", "uuid-R1", [("Role", "A")])
        board = _StubBoard([known])
        adapter = _adapter(board)
        old = adapter.get_footprints()          # the cache knows uuid-R1 only

        # uuid-R99 is a live item KiCad will answer for, yet the cache has
        # never seen it — exactly the "uuid absent from the cache" case.
        board.by_uuid["uuid-R99"] = _KipyFootprint("R99", "uuid-R99", [("Role", "Z")])
        # The board the full read would reveal no longer matches the cache.
        board.footprints = [_KipyFootprint("R99", "uuid-R99", [("Role", "Z")])]

        adapter.reread_footprints_by_id(["uuid-R99"])

        assert board.calls_by_id == [], (
            "no IPC may be spent asking about a uuid the cache already denies")
        assert adapter._kicad.get_board.call_count == 1, (
            "one full board read is the only honest answer for an unknown uuid")
        now = adapter.get_footprints()
        assert [fp.ref for fp in now] == ["R99"], (
            "the cache must describe the board, not the stale generation")
        assert now[0] is not old[0]


# ── Э3.5 — the two read paths must stay the same converter ───────────────────

class TestTargetedReadMatchesTheFullRead:
    def test_type_shape_and_values_are_identical_to_a_full_read(self):
        """Э3.5 (P.1.4). Both paths must go through the same conversion, or a
        later change to one converter silently splits the two kinds of object
        the rest of the code treats as one."""
        live = _KipyFootprint("R1", "uuid-R1",
                              [("Role", "MCU"), ("Cluster", "C1")],
                              position=(5, 7))
        board = _StubBoard([live])
        adapter = _adapter(board)
        from_full = adapter.get_footprints()[0]

        re_read = _KipyFootprint("R1", "uuid-R1",
                                 [("Role", "MCU"), ("Cluster", "C1")],
                                 position=(5, 7))
        board.by_uuid["uuid-R1"] = re_read

        adapter.reread_footprints_by_id(["uuid-R1"])
        from_targeted = adapter.get_footprints()[0]

        assert type(from_targeted) is type(from_full)
        assert (list(_observable_shape(from_targeted))
                == list(_observable_shape(from_full)))
        assert _observable_shape(from_targeted) == _observable_shape(from_full)
        # ...and it is a DTO over the live object, not the live object itself.
        assert from_targeted._kipy is re_read


# ── Э3.6 — an already-dropped cache is never rebuilt here ────────────────────

class TestADroppedCacheIsNeverRebuilt:
    def test_a_none_cache_costs_no_ipc_at_all(self):
        """Э3.6 (P.1.6). flip_selected() drops both caches on purpose; the next
        get_footprints() re-reads the board wholesale anyway, so a targeted
        read must not resurrect a cache (nor spend a round trip doing nothing)."""
        board = _StubBoard([])
        adapter = _adapter(board)

        adapter.reread_footprints_by_id(["uuid-R1"])

        assert board.calls_by_id == []
        assert board.calls_full == 0
        assert adapter._kicad.get_board.call_count == 0
        assert adapter._footprints_cache is None

    def test_an_empty_list_never_touches_the_board(self):
        live = _KipyFootprint("R1", "uuid-R1", [("Role", "MCU")])
        board = _StubBoard([live])
        adapter = _adapter(board)
        fp = adapter.get_footprints()[0]

        adapter.reread_footprints_by_id([])

        assert board.calls_by_id == []
        assert adapter.get_footprints()[0] is fp


# ── Э1 — one round trip for the whole list ──────────────────────────────────

class TestOneRequestForTheWholeList:
    def test_all_uuids_go_out_together_and_a_repeat_is_sent_once(self):
        a = _KipyFootprint("R1", "uuid-R1", [("Role", "A")])
        b = _KipyFootprint("R2", "uuid-R2", [("Role", "B")])
        board = _StubBoard([a, b])
        adapter = _adapter(board)
        adapter.get_footprints()

        board.by_uuid["uuid-R1"] = _KipyFootprint("R1", "uuid-R1", [("Role", "A2")])
        board.by_uuid["uuid-R2"] = _KipyFootprint("R2", "uuid-R2", [("Role", "B2")])

        adapter.reread_footprints_by_id(["uuid-R1", "uuid-R2"])

        assert len(board.calls_by_id) == 1, (
            "N footprints must not become N IPC round trips")
        assert sorted(board.calls_by_id[0]) == ["uuid-R1", "uuid-R2"]

        # A repeated uuid is one request, not a request plus a spurious
        # "did not come back" fallback (which would cost a FULL re-read).
        adapter.reread_footprints_by_id(["uuid-R2", "uuid-R2"])
        assert board.calls_by_id[1] == ["uuid-R2"]
        assert board.calls_full == 1


# ── Э4.2 — the duplicate-field-name scan guard ──────────────────────────────

class TestDuplicateFieldNameKeepsTheFirstOccurrence:
    def test_a_footprint_carrying_one_field_name_twice_returns_the_first(self):
        """_scan_field_values keeps its `and item.name not in values` guard for
        exactly this: a (pathological) footprint with one field name twice. The
        pre-2026-09-13 get_field_value returned on the FIRST match, so the map
        must too — a plain dict comprehension would silently flip the winner,
        with no test and no symptom."""
        live = _KipyFootprint("R1", "uuid-R1",
                              [("Role", "FIRST"), ("Role", "SECOND")])
        board = _StubBoard([live])
        adapter = _adapter(board)
        fp = adapter.get_footprints()[0]

        assert adapter.get_field_value(fp, "Role") == "FIRST"

        # ...and the winner does not move when the map is rebuilt for the same
        # footprint by a targeted re-read of an identical object.
        board.by_uuid["uuid-R1"] = _KipyFootprint(
            "R1", "uuid-R1", [("Role", "FIRST"), ("Role", "SECOND")])
        adapter.reread_footprints_by_id(["uuid-R1"])

        assert adapter.get_field_value(adapter.get_footprints()[0], "Role") == "FIRST"
