# tests/test_mcp_board_freshness.py
"""Cells М1–М5 of plan_2026_09_24_mcp_stale_board §9 — the MCP seam answers
from a LIVE board, not from the cache it filled when KiCad was first connected.

The defect these cells pin (measured live, not a hypothesis): the MCP server
process stays up for hours, ``ConnectionManager.execute`` handed out the adapter
without rebuilding it, and ``kicadstamp_list_footprints`` answered from the
footprint cache filled at connect time — a board hours old. The fix is one
rebuild per tool call, right before the handler runs (``mcp_server/connection.py``).

Why the fake below counts THREE different things. The Ш1 lesson, quoted in the
seam's own docstring, is that counting ADAPTER CALLS sees nothing here: the count
said "6 adapter calls" while the board was read once, in the other column. So:

  * ``refresh_count``   — how many times the board was REBUILT (caches dropped);
  * ``footprint_reads`` — how many times the footprint list was really read,
                          i.e. a cache MISS, which is the thing that costs;
  * ``field_reads``     — the same for the field map (`_field_values_cache`);
  * ``created``         — how many adapters the manager built (the test does it).

М3 is exactly the cell that a call counter cannot state: a tool that never looks
at a footprint must not pay for one. The numbers, not the names, are what make
that a statement about the BOARD rather than about the adapter.

Empty cells, named on purpose (rule 35 — an empty cell by decision is fine, an
empty cell by oversight is the next entry):

  * the FIELD-TEXT half of the §3 finding is not covered: a stale object going
    back to KiCad would also restore stale Role/Cluster text, but the domain
    ``Footprint`` DTO does not carry those fields (they are read through
    ``get_field_value``), so this fake cannot express it. The rotation half of
    the same hole IS covered — see М4;
  * the cost of one MCP call on the real 325-footprint board is not a cell: it
    is a live measurement (plan §7) and it lives in the seam's docstring;
  * `_ensure_open`'s "board vanished" branch is already pinned by
    ``tests/test_mcp_connection.py::test_recreates_when_board_vanished`` and is
    deliberately not duplicated here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2

from mcp_server import handlers
from mcp_server.connection import ConnectionManager

ROOT = Path(__file__).resolve().parent.parent

BOARD_NAME = "freshness_board.kicad_pcb"

# The values are exact in board units (nm), so a round trip through
# Vector2.from_xy_mm (int(x_mm * 1_000_000)) cannot blur an assertion.
_LIVE_R1 = {"x_mm": 1.0, "y_mm": 2.0, "angle_deg": 0.0,
            "role": "R_OLD", "cluster": "C1"}


def _board(**r1_overrides):
    """The "live board": plain records the test mutates BETWEEN calls."""
    return {"R1": {**_LIVE_R1, **r1_overrides}}


class _CachingBoardAdapter:
    """A fake with the real adapter's TWO caches and honest counters.

    ``live`` is the board itself: a dict of records from which the caches are
    rebuilt with FRESH DTOs on a miss. That is the one property the cells need —
    a read of the board must be observable as a cache miss, and a warm read must
    return exactly what the last rebuild saw (Ш1: warm read == the positions at
    the last refresh, 1.8063 mm away from the live board).

    ``refresh_board()`` mirrors the adapter: it re-mints the handle and drops
    BOTH caches, and reads nothing — invalidation itself is free (plan §2), which
    is the whole reason refreshing on every call is affordable.

    ``update_items()`` is faithful to the write path: what arrives there goes to
    KiCad WHOLE, so a stale attribute travelling inside the object lands on the
    board (§3) instead of being quietly corrected.
    """

    def __init__(self, live, *, name=BOARD_NAME, version="10.0.6",
                 fail_refresh_from=None):
        # BY REFERENCE, never a copy: this IS the board, and the test mutates it
        # between calls. A snapshot taken here would freeze the fake at
        # construction and make every freshness cell pass for the wrong reason —
        # the very mistake these cells exist to catch (made once while writing
        # them, on the first run).
        self.live = live
        self._name = name
        self._version = version
        self._fail_refresh_from = fail_refresh_from
        self.refresh_count = 0
        self.footprint_reads = 0
        self.field_reads = 0
        self.closed = 0
        self.updated = []
        self._footprints_cache = None
        self._field_values_cache = None

    # --- the adapter surface the seam and the handlers use -----------------
    def refresh_board(self):
        self.refresh_count += 1
        if (self._fail_refresh_from is not None
                and self.refresh_count >= self._fail_refresh_from):
            raise ConnectionError("kipy socket closed mid-refresh")
        self._footprints_cache = None
        self._field_values_cache = None

    def get_board_filename(self):
        return self._name

    def get_version(self):
        return self._version

    def get_footprints(self):
        if self._footprints_cache is None:
            self.footprint_reads += 1
            self._footprints_cache = [self._to_dto(ref, record)
                                      for ref, record in self.live.items()]
        return list(self._footprints_cache)

    def get_footprint(self, ref):
        return next((fp for fp in self.get_footprints() if fp.ref == ref), None)

    def get_field_value(self, fp, name):
        if self._field_values_cache is None:
            self.field_reads += 1
            self._field_values_cache = {
                "uuid-" + ref: {ROLE_FIELD_NAME: record["role"],
                                CLUSTER_FIELD_NAME: record["cluster"]}
                for ref, record in self.live.items()}
        return self._field_values_cache.get(fp.uuid, {}).get(name)

    def get_footprint_pads(self, fp):
        return []

    def get_selected_items(self):
        return []

    def get_tracks(self):
        return []

    def get_vias(self):
        return []

    def get_all_nets(self):
        return []

    def update_items(self, items):
        for item in items:
            self.updated.append(item)
            record = self.live[item.ref]
            record["x_mm"] = item.position.x / 1_000_000.0
            record["y_mm"] = item.position.y / 1_000_000.0
            record["angle_deg"] = item.angle_deg

    def commit_with_retry(self, description, fn):
        fn()
        return True

    def close(self):
        self.closed += 1

    def ping(self):
        return "pong"

    @staticmethod
    def _to_dto(ref, record):
        return Footprint(ref=ref, uuid="uuid-" + ref,
                         position=Vector2.from_xy_mm(record["x_mm"], record["y_mm"]),
                         angle_deg=record["angle_deg"],
                         layer=BoardLayer.BL_F_Cu,
                         value="VAL-" + ref)


def _manager(live, *, first_adapter_fails_second_refresh=False):
    """A ConnectionManager whose factory RECORDS every adapter it builds.

    ``first_adapter_fails_second_refresh`` makes only the first adapter lose the
    link — on its second rebuild, i.e. exactly the one the seam performs. That
    mirrors the real scenario the reconnect path exists for and keeps the
    replacement healthy.
    """
    created = []

    def factory(timeout_ms):
        fail_from = (2 if first_adapter_fails_second_refresh and not created
                     else None)
        adapter = _CachingBoardAdapter(live, name=BOARD_NAME,
                                       fail_refresh_from=fail_from)
        created.append(adapter)
        return adapter

    return ConnectionManager(timeout_ms=12345, adapter_factory=factory), created


# --- М1 ---------------------------------------------------------------------

@pytest.mark.parametrize("change", ["position", "field"])
def test_the_second_call_reports_the_board_as_it_is_now(change):
    """М1 of plan_2026_09_24_mcp_stale_board §9 — RED on the base commit.

    Two consecutive ``execute`` calls, with the board changed in between: the
    second one must return the NEW value. On the base commit it returned the one
    the cache held since the adapter was created, which is the whole defect.

    Parametrized over WHAT changed on purpose, because the two rows travel
    through DIFFERENT caches: a footprint's position/size live in
    ``_footprints_cache``, while Role/Cluster come from ``_field_values_cache``.
    One row would have claimed both and proved one.
    """
    board = _board()
    mgr, created = _manager(board)

    first = mgr.execute(lambda a: handlers.list_footprints(a))
    assert first[0]["role"] == "R_OLD"
    assert first[0]["x_mm"] == pytest.approx(1.0)  # the first answer was honest

    if change == "position":
        board["R1"].update(x_mm=42.0, y_mm=-7.5)
        second = mgr.execute(lambda a: handlers.list_footprints(a))
        assert second[0]["x_mm"] == pytest.approx(42.0), (
            "the second call served the position the cache held when the adapter "
            "was created instead of the one the board has now")
        assert second[0]["y_mm"] == pytest.approx(-7.5)
    else:
        board["R1"].update(role="R_NEW")
        second = mgr.execute(lambda a: handlers.list_footprints(a))
        assert second[0]["role"] == "R_NEW", (
            "the second call served the stale field map — the OTHER cache, "
            "dropped by the same rebuild but not by the footprint read alone")

    assert len(created) == 1, (
        "the adapter is REUSED across calls; only the board behind it is rebuilt "
        "(plan §6.3 — test_reuses_the_same_adapter_across_calls must stay true)")


# --- М2 ---------------------------------------------------------------------

@pytest.mark.parametrize("calls", [1, 3])
def test_the_board_is_rebuilt_exactly_once_per_call(calls):
    """М2 of plan_2026_09_24_mcp_stale_board §9 (§6.1) — never twice, never zero.

    The row with ONE call is the one that pins §6.1: the call which had to CREATE
    the adapter must show exactly one rebuild, because creating the adapter loads
    the board itself. Refreshing in ``_ensure_open`` as well made that counter 2
    and turned the pre-existing
    ``test_lazy_connect_creates_adapter_only_on_first_use`` red by the change
    itself — a false finding, not a defect. The row with three calls says the
    same thing the other way round: the counter tracks CALLS, not connections.

    On the base commit the 1-call row is green and the 3-call row is red
    (``refresh_count == 1`` however many calls were made).
    """
    mgr, created = _manager(_board())

    for _ in range(calls):
        assert mgr.execute(lambda a: a.ping()) == "pong"

    assert len(created) == 1, "one adapter for the whole process, as before"
    assert created[0].refresh_count == calls, (
        "the board must be rebuilt exactly once per tool call — a second rebuild "
        "in the same call means the seam refreshed on top of _ensure_open, and "
        "zero means the call was answered from the connect-time cache")


# --- М3 ---------------------------------------------------------------------

_TOOL_CALLS = {
    "list_tracks": lambda a: handlers.list_tracks(a),
    "list_vias": lambda a: handlers.list_vias(a),
    "list_nets": lambda a: handlers.list_nets(a),
    "get_selection": lambda a: handlers.get_selection(a),
    "get_board_identity": lambda a: handlers.get_board_identity(a),
    "list_footprints": lambda a: handlers.list_footprints(a),
    "get_footprint": lambda a: handlers.get_footprint(a, ref="R1"),
    "get_items_by_uuid": lambda a: handlers.get_items_by_uuid(a, uuids=["uuid-R1"]),
}

# What ONE call of the tool costs in BOARD READS of the footprint list. The three
# tools with a 0 are the point of the cell: tracks, vias and nets live behind the
# live kipy handle, so they never need the footprint cache rebuilt — but they DO
# need the board refreshed, which is why the rebuild is asserted separately.
_FOOTPRINT_READS_PER_TOOL = {
    "list_tracks": 0,
    "list_vias": 0,
    "list_nets": 0,
    "get_selection": 0,
    "get_board_identity": 0,
    "list_footprints": 1,
    "get_footprint": 1,
    "get_items_by_uuid": 1,
}


@pytest.mark.parametrize(
    "tool,expected_reads",
    [pytest.param(tool, reads, id=tool)
     for tool, reads in sorted(_FOOTPRINT_READS_PER_TOOL.items())])
def test_only_the_footprint_reading_tools_pay_a_footprint_read(tool, expected_reads):
    """М3 of plan_2026_09_24_mcp_stale_board §9 — count BOARD READS, not calls.

    The seam refreshes for EVERY tool (§6.2: always, not per a hand-written list
    of "who needs freshness"), and this cell is what keeps that promise cheap:
    invalidating the cache is free, so a tool that never touches footprints must
    show zero footprint reads even though its call rebuilt the board.

    The counter is a cache MISS inside ``get_footprints()`` — the layer where the
    cache actually lives — and not a count of adapter calls, because the adapter
    answers a warm call without touching the board at all (the Ш1 lesson: the
    call counter said 6 while the board was read once).
    """
    mgr, created = _manager(_board())

    mgr.execute(_TOOL_CALLS[tool])

    assert created[0].refresh_count == 1, (
        f"{tool} must still get a rebuilt board: freshness is per call, and the "
        "tools that do not read footprints are not exempt from it")
    assert created[0].footprint_reads == expected_reads, (
        f"{tool} paid {created[0].footprint_reads} board reads of the footprint "
        f"list, expected {expected_reads} — a tool that never looks at a "
        "footprint must not pay for one")


# --- М4 ---------------------------------------------------------------------

def test_a_raw_move_reads_the_footprint_after_the_rebuild():
    """М4 of plan_2026_09_24_mcp_stale_board §9, the §3 finding — RED on base.

    The write path takes the object it is about to push from the adapter, so a
    stale object does not merely report a wrong ``old``: KiCad is handed the WHOLE
    cached object, and the rotation the human made after the cache was filled is
    silently undone. The scenario is exactly the plan's: the cache is warm (a
    first call filled it), then the part is rotated by hand, then a raw move
    without a rotation is asked for — which has no business touching the angle.

    What is asserted, in order: ``old`` describes the board as it IS; the object
    that goes out carries that same live rotation; and the BOARD keeps it (the
    fake applies the push like KiCad would).

    The field-text half of the same hole (Role/Cluster restored from a stale
    object) is NOT covered — see the module docstring: the domain Footprint DTO
    does not carry those fields, so this fake cannot express it.
    """
    board = _board()
    mgr, created = _manager(board)

    mgr.execute(lambda a: handlers.list_footprints(a))
    assert created[0].footprint_reads == 1, "the cache is warm at this point"

    board["R1"].update(angle_deg=90.0, x_mm=11.0, y_mm=12.0)

    result = mgr.execute(lambda a: handlers.raw_move_footprint(
        a, ref="R1", x_mm=30.0, y_mm=40.0, expected_board_name=BOARD_NAME))

    assert result["old"]["rotation_deg"] == pytest.approx(90.0), (
        "`old` reported a rotation that was not true at the moment of the write")
    assert result["old"]["x_mm"] == pytest.approx(11.0), (
        "`old` reported the position the cache still held, not the live one")
    assert result["new"]["rotation_deg"] == pytest.approx(90.0), (
        "the un-rotated raw move silently undid the human's rotation: the cached "
        "object went to KiCad whole (§3)")
    assert board["R1"]["angle_deg"] == pytest.approx(90.0), (
        "the board itself must still carry the rotation the human made")
    assert result["new"]["x_mm"] == pytest.approx(30.0)
    assert created[0].refresh_count == 3, (
        "three calls so far — the connect, the warm-up read and the move")


# --- М5 ---------------------------------------------------------------------

def _imprint_freshness_harness():
    """The mutation harness that MATCHES the four GUI call sites by text.

    Imported by path, not by name: it is a diagnostics script, not a package
    module, and its own ``if __name__ == "__main__"`` guard keeps the import
    from running anything.
    """
    path = ROOT / "kicadstamp" / "diagnostics" / "run_imprint_freshness_mutations.py"
    spec = importlib.util.spec_from_file_location("_imprint_freshness_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gui_module_reexports_the_seam_and_its_call_sites_are_untouched():
    """М5 of plan_2026_09_24_mcp_stale_board §9/§5 — the rename must be invisible.

    Two halves, because either one alone is decoration:

      * the re-export works and is the SAME function object. The four GUI call
        sites import the name from ``gui.worker``, so a deletion there would
        break them at import time — and a twin definition would be two remedies
        for one property, which is what §5 forbids;
      * the four call lines are byte for byte the ones the mutation harness
        matches by TEXT. A re-spelling (wrapping, renaming, reformatting) would
        leave the harness matching nothing and reporting GREEN — the silent miss
        rule 38 warns about, indistinguishable from a healthy guard.
    """
    from gui.worker import refresh_board_before_live_read as via_gui

    from kicadstamp.board_freshness import refresh_board_before_live_read as via_core

    assert via_gui is via_core, (
        "gui/worker.py must RE-EXPORT the one seam function, not define a twin")

    harness = _imprint_freshness_harness()
    hub_text = (ROOT / harness.HUB).read_text(encoding="utf-8")
    imprint_text = (ROOT / harness.IMPRINT).read_text(encoding="utf-8")
    for template_name in ("_RECORD_CAPTURE_SEAM", "_RESOURCE_CAPTURE_SEAM",
                          "_REREAD_SEAM", "_REREAD_APPLY_SEAM"):
        template = getattr(harness, template_name)
        matches = hub_text.count(template) + imprint_text.count(template)
        assert matches == 1, (
            f"the mutation harness template {template_name} matches {matches} "
            "sites, so the mutation built from it would edit a foreign line (or "
            "nothing) and report GREEN — rule 38")

    call_line = ('refresh_board_before_live_read('
                 'getattr(payload.get("board"), "adapter", None))\n')
    call_sites = sum(path.read_text(encoding="utf-8").count(call_line)
                     for path in sorted((ROOT / "gui").rglob("*.py")))
    assert call_sites == 4, (
        "the four GUI call sites must stay byte for byte: a change here breaks "
        "the harness templates above without breaking any test that exists")


# --- extra cell (no plan number): the refresh itself may lose the link -------

def test_a_link_lost_while_rebuilding_the_board_reconnects_exactly_once():
    """Un-numbered extra cell of the same plan, guarding a decision taken while
    implementing §6: the refresh sits INSIDE the reconnect ``try``.

    Before the change a dead link could pass unnoticed, because a read the cache
    could satisfy never touched the board and therefore could not fail. Now every
    call touches the board, so the failure mode has to be handled: a link lost
    while rebuilding is the same class ``_reconnectable_errors`` exists for, and
    it must cost EXACTLY one reconnect — the fresh adapter loads the board as it
    is created, so nothing is rebuilt a second time.

    The link is lost BETWEEN calls on purpose: the first call may not rebuild
    (it CREATED the adapter, and creating it already loaded the board, §6.1), so
    the first rebuild a later call performs is the one that meets the dead link.
    """
    mgr, created = _manager(_board(), first_adapter_fails_second_refresh=True)

    assert mgr.execute(lambda a: a.ping()) == "pong"  # connects; nothing to lose yet
    assert len(created) == 1
    assert created[0].refresh_count == 1, (
        "the connecting call rebuilt once, as the creation load")

    assert mgr.execute(lambda a: a.ping()) == "pong"  # the link is dead now

    assert len(created) == 2, "one reconnect, not a loop"
    assert created[0].closed == 1, "the dead adapter was closed exactly once"
    assert created[0].refresh_count == 2, (
        "the dead adapter performed the creation load and then the failing "
        "refresh — that second rebuild is where the link was lost")
    assert created[1].refresh_count == 1, (
        "the replacement loaded the board on creation and was NOT rebuilt again "
        "before the retry (§6.1 from the other side)")
