#!/usr/bin/env python3
"""
Test for the kicad module (without a real connection to KiCad).
Checks imports and method presence in classes.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kipy.board_types import Field

from kicadstamp.kicad import KiCadBoardAdapter, IBoardAdapter
from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter


def test_import():
    """Check that imports work."""
    assert KiCadBoardAdapter is not None
    assert IBoardAdapter is not None
    print("✅ kicad import OK")


def test_adapter_has_methods():
    """Check that the adapter has all methods required by the interface."""
    # List of methods that must be present in KiCadBoardAdapter (including new ones)
    methods = [
        # Core access methods
        "refresh_board",
        "close",
        "get_footprint",
        "get_footprints",
        "get_vias",
        "get_tracks",
        "get_selected_items",
        "get_field_value",
        "has_field",
        "get_footprint_pads",
        "get_pad_by_number",
        "get_zone_by_name",
        "get_net_by_name",
        "get_all_nets",
        "get_board_origin",
        "get_bounding_boxes",
        # Transactions
        "begin_commit",
        "push_commit",
        "drop_commit",
        # Mutations
        "update_items",
        "create_items",
        "flip_selected",
        "commit_with_retry",
        "create_via",
        "create_track",
        "remove_by_id",
        # Crash risk warning
        "check_write_crash_risk",
    ]
    for method in methods:
        assert hasattr(Adapter, method), f"Method {method} is missing in KiCadBoardAdapter"
    print("✅ All interface methods are present in the adapter")


def test_init_without_connection():
    """Check that the constructor does not crash (without calling refresh_board)."""
    try:
        adapter = KiCadBoardAdapter(timeout_ms=1000)
        assert adapter is not None
        print("✅ KiCadBoardAdapter constructor works (without connection)")
    except Exception as e:
        print(f"⚠️ Constructor crashed (this may be normal if KiCad is not running): {e}")


def _make_fp(ref):
    fp = MagicMock()
    fp.reference_field.text.value = ref
    fp.id.value = f"uuid-{ref}"
    fp.orientation.degrees = 0.0
    fp.value_field = None
    fp.sheet_path.path = []
    return fp


def _fp_with_fields(**fields):
    """A fake footprint whose texts_and_fields holds real kipy Field
    objects (Field() with no proto builds a usable empty one — see
    get_field_value/set_field_value, which isinstance()-check for Field
    specifically, so a MagicMock stand-in would not match)."""
    fp = MagicMock()
    texts_and_fields = []
    for name, value in fields.items():
        f = Field()
        f.name = name
        f.text.value = value
        texts_and_fields.append(f)
    fp.texts_and_fields = texts_and_fields
    return fp


class TestHasField:
    """has_field() (added 2026-08-03): distinguishes "field missing
    entirely" from get_field_value()'s None (which also means "field
    present but empty") — needed so a batch write can skip a footprint
    instead of hitting set_field_value's fatal ValidationError mid-commit
    (found live: one footprint missing Cluster rolled back an entire
    287-component Clear all — see gui/docks/role_cluster_tree.py's
    _run_clear)."""

    def test_true_when_field_present(self):
        adapter = Adapter.__new__(Adapter)
        fp = _fp_with_fields(Role="MCU")
        assert adapter.has_field(fp, "Role") is True

    def test_true_when_field_present_but_empty(self):
        adapter = Adapter.__new__(Adapter)
        fp = _fp_with_fields(Role="")
        assert adapter.has_field(fp, "Role") is True

    def test_false_when_field_absent(self):
        adapter = Adapter.__new__(Adapter)
        fp = _fp_with_fields(Role="MCU")
        assert adapter.has_field(fp, "Cluster") is False


class TestGetBoardOrigin:
    """get_board_origin() (added 2026-08-06) — thin wrapper over kipy's
    Board.get_origin(BOT_GRID/BOT_DRILL), used by Point's anchor_origin
    (config/points.py) via point_resolver.py."""

    def test_drill_maps_to_bot_drill(self):
        from kipy.proto.board import board_commands_pb2
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()

        adapter.get_board_origin("drill")

        adapter._board.get_origin.assert_called_once_with(board_commands_pb2.BOT_DRILL)

    def test_grid_maps_to_bot_grid(self):
        from kipy.proto.board import board_commands_pb2
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()

        adapter.get_board_origin("grid")

        adapter._board.get_origin.assert_called_once_with(board_commands_pb2.BOT_GRID)


class TestFootprintsCache:
    """get_footprints() caching (added 2026-07-29): the call graph analysis
    (dependency_order.py resolves every rule/clone_placement's anchor TWICE —
    once for the dependency graph, once again to actually plan it — and each
    resolution calls get_footprints() at least once) showed dozens of
    redundant full-board IPC round trips per apply run for data that cannot
    have changed since the last refresh_board(). Uses __new__ to bypass
    __init__ (which creates a real kipy.KiCad() instance) — these tests only
    exercise the caching logic around a mocked self._board/self._kicad, no
    live KiCad connection needed."""

    def test_get_footprints_only_queries_ipc_once_per_generation(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]

        first = adapter.get_footprints()
        second = adapter.get_footprints()

        assert [fp.ref for fp in first] == ["R1", "C1"]
        assert [fp.ref for fp in second] == ["R1", "C1"]
        adapter._board.get_footprints.assert_called_once()

    def test_get_footprint_by_ref_uses_the_cache_too(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]

        found = adapter.get_footprint("C1")
        adapter.get_footprints()  # second read — must still hit the cache

        assert found.ref == "C1"
        adapter._board.get_footprints.assert_called_once()

    def test_refresh_board_clears_the_cache(self):
        adapter = Adapter.__new__(Adapter)
        adapter._kicad = MagicMock()
        adapter._footprints_cache = None
        board1 = MagicMock()
        board1.get_footprints.return_value = [_make_fp("R1")]
        board2 = MagicMock()
        board2.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]
        adapter._kicad.get_board.side_effect = [board1, board2]

        adapter.refresh_board()
        first = adapter.get_footprints()
        adapter.refresh_board()
        second = adapter.get_footprints()

        assert len(first) == 1
        assert len(second) == 2
        board1.get_footprints.assert_called_once()
        board2.get_footprints.assert_called_once()

    def test_returned_list_is_a_copy_mutating_it_does_not_corrupt_cache(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1")]

        first = adapter.get_footprints()
        first.append(_make_fp("BOGUS"))
        second = adapter.get_footprints()

        assert len(second) == 1

    def test_flip_selected_invalidates_the_cache(self):
        """Regression (found live 2026-07-29, fpga_oscill_r_pi_filter landing
        on F.Cu instead of B.Cu): flip_selected() flips server-side via a GUI
        action, it does NOT update the local FootprintInstance objects'
        .layer — a cached get_footprints() call right after it must NOT
        return the stale pre-flip list, or flip_manager.flip_if_needed()'s
        "reload after flip" re-fetch silently returns stale data, and the
        subsequent update_items() push undoes the flip."""
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._kicad = MagicMock()
        adapter._footprints_cache = None
        pre_flip_fp = _make_fp("C1")
        post_flip_fp = _make_fp("C1")
        adapter._board.get_footprints.side_effect = [[pre_flip_fp], [post_flip_fp]]

        first = adapter.get_footprints()
        adapter.flip_selected(first)
        second = adapter.get_footprints()

        assert first[0].ref == "C1"
        assert second[0].ref == "C1"
        assert first[0] is not second[0]  # cache was invalidated -> fresh DTOs
        assert adapter._board.get_footprints.call_count == 2


class TestClose:
    """close() (added 2026-08-04): explicitly closes the underlying kipy
    client's pynng socket instead of leaving that to the garbage collector —
    see the method's own docstring for the native-crash motivation (a silent
    Windows access violation with no Python frame on the crashing thread,
    found live after many reconnects piled up several never-closed sockets
    in one long-lived GUI session). kipy 0.7.1 exposes no public close(), so
    this reaches into KiCadClient's private _conn — these tests pin down
    that reach against the real private-attribute shape."""

    def test_closes_the_connection_when_connected(self):
        adapter = Adapter.__new__(Adapter)
        conn = MagicMock()
        client = MagicMock()
        client._connected = True
        client._conn = conn
        adapter._kicad = MagicMock()
        adapter._kicad._client = client

        adapter.close()

        conn.close.assert_called_once()

    def test_noop_when_never_connected(self):
        """_conn doesn't even exist on a real KiCadClient until _connect()
        has run once — must not raise AttributeError."""
        adapter = Adapter.__new__(Adapter)
        client = MagicMock()
        client._connected = False
        del client._conn  # real KiCadClient has no _conn attribute at all yet
        adapter._kicad = MagicMock()
        adapter._kicad._client = client

        adapter.close()  # must not raise

    def test_swallows_a_close_failure(self):
        """The socket may already be broken (that's often exactly why close()
        is being called) — a failure here must never propagate and block the
        caller's own cleanup/reconnect."""
        adapter = Adapter.__new__(Adapter)
        conn = MagicMock()
        conn.close.side_effect = RuntimeError("already broken")
        client = MagicMock()
        client._connected = True
        client._conn = conn
        adapter._kicad = MagicMock()
        adapter._kicad._client = client

        adapter.close()  # must not raise

    def test_noop_when_client_never_created(self):
        """A fresh kipy.KiCad() has _client = None until _connect() has run
        once — the very first close() before any connection attempt must not
        raise (the getattr default on _client is the branch that protects
        this)."""
        adapter = Adapter.__new__(Adapter)
        adapter._kicad = MagicMock()
        adapter._kicad._client = None

        adapter.close()  # must not raise

    def test_noop_when_connected_but_no_conn_object(self):
        """_connected can be True while _conn is still missing if the client
        is in a half-initialised state (e.g. a broken reconnect) — the
        getattr default must keep this branch a silent no-op too."""
        adapter = Adapter.__new__(Adapter)
        client = MagicMock()
        client._connected = True
        del client._conn  # real KiCadClient may not have _conn yet
        adapter._kicad = MagicMock()
        adapter._kicad._client = client

        adapter.close()  # must not raise


class TestSetFieldValuesBulk:
    """Regression (found live 2026-08-03, reproduced live on a real board via
    fieldstool's Stage/Clear all): `touched` used to be built with one
    append() per (footprint, field, value) triple, outside work() — a
    footprint with more than one field in the same batch (true of every
    Clear all/Delete selected call, which always sets Role AND Cluster) got
    appended more than once, so update_items() received the SAME
    FootprintInstance object multiple times in one list, and a live KiCad
    board turned that into a genuine duplicate physical footprint (verified
    in isolation: update_items([fp, fp]) creates a second footprint instead
    of a no-op double-update of the one already there)."""

    @staticmethod
    def _adapter():
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._write_risk_checked = True  # skip check_write_crash_risk's own IPC call
        return adapter

    def test_touched_list_has_each_footprint_once_even_with_two_fields_set(self):
        adapter = self._adapter()
        fp = _fp_with_fields(Role="OLD_ROLE", Cluster="OLD_CLUSTER")
        updates = [(fp, "Role", "NEW_ROLE"), (fp, "Cluster", "NEW_CLUSTER")]

        adapter.set_field_values_bulk(updates, "test")

        (touched,), _kwargs = adapter._board.update_items.call_args
        assert touched == [fp]

    def test_touched_list_preserves_first_seen_order_across_several_footprints(self):
        adapter = self._adapter()
        fp1 = _fp_with_fields(Role="R1", Cluster="C1")
        fp2 = _fp_with_fields(Role="R2", Cluster="C2")
        updates = [(fp1, "Role", "X"), (fp2, "Role", "X"),
                   (fp1, "Cluster", "Y"), (fp2, "Cluster", "Y")]

        adapter.set_field_values_bulk(updates, "test")

        (touched,), _kwargs = adapter._board.update_items.call_args
        assert touched == [fp1, fp2]

    def test_retry_does_not_accumulate_touched_across_attempts(self):
        """commit_with_retry() calls work() again on a transient failure —
        touched used to live in the enclosing function, so a retried batch
        carried over every previous attempt's entries on top of the
        per-field duplication above, compounding it further."""
        adapter = self._adapter()
        fp = _fp_with_fields(Role="OLD_ROLE", Cluster="OLD_CLUSTER")
        updates = [(fp, "Role", "NEW_ROLE"), (fp, "Cluster", "NEW_CLUSTER")]
        adapter._board.push_commit.side_effect = [Exception("not ready"), None]

        adapter.set_field_values_bulk(updates, "test")

        assert adapter._board.update_items.call_count == 2
        for (touched,), _kwargs in adapter._board.update_items.call_args_list:
            assert touched == [fp]


class TestIgnoreSelection:
    """adapter.ignore_selection / --no-selection (added 2026-07-30): a stray
    leftover GUI selection in the PCB editor feeds into role-based
    ClonePlacement resolution (resolve_roles_by_selection) and ambiguity
    narrowing (_narrow_ambiguous_candidates/resolve_footprint_by_role) as
    real input — found live: an unrelated component (J1) selected from
    earlier browsing made an otherwise-unique-by-role clone_placement fatal
    with "role X is not in the cell". ignore_selection makes
    get_selected_items() always report nothing selected, regardless of the
    live board's actual selection."""

    def test_default_reads_the_real_selection(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter.ignore_selection = False
        adapter._board.get_selection.return_value = [_make_fp("J1")]

        items = adapter.get_selected_items()

        assert len(items) == 1
        adapter._board.get_selection.assert_called_once()

    def test_ignore_selection_reports_nothing_without_querying_the_board(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter.ignore_selection = True
        adapter._board.get_selection.return_value = [_make_fp("J1")]

        items = adapter.get_selected_items()

        assert items == []
        adapter._board.get_selection.assert_not_called()


class TestSelectionLogDedup:
    """Found 2026-08-06: the GUI's live-selection timer polls
    get_selected_items() every ~400ms — logging its count unconditionally at
    DEBUG flooded the log file (many lines/sec) with nothing to say, burying
    the one Redraw actually worth reading around it. Only log when the count
    changes."""

    def test_logs_only_when_count_changes(self, caplog):
        import logging
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter.ignore_selection = False

        with caplog.at_level(logging.DEBUG, logger="kicadstamp.kicad.adapter"):
            adapter._board.get_selection.return_value = []
            adapter.get_selected_items()
            adapter.get_selected_items()
            adapter.get_selected_items()
            adapter._board.get_selection.return_value = [_make_fp("J1")]
            adapter.get_selected_items()
            adapter.get_selected_items()

        selection_logs = [r for r in caplog.records if "Selected items" in r.message]
        assert len(selection_logs) == 2  # once for count=0, once for count=1 — not 5


class TestTemporarilyIgnoreSelection:
    """adapter.temporarily_ignore_selection() — per-item counterpart of the
    plain ignore_selection flag, used by ClonePlacement.ignore_selection
    (added 2026-07-30) to scope the override to just one clone_placement's
    own resolution instead of the whole run."""

    def test_active_true_forces_true_for_the_block_and_restores_after(self):
        adapter = Adapter.__new__(Adapter)
        adapter.ignore_selection = False

        with adapter.temporarily_ignore_selection(True):
            assert adapter.ignore_selection is True

        assert adapter.ignore_selection is False

    def test_active_false_is_a_noop(self):
        adapter = Adapter.__new__(Adapter)
        adapter.ignore_selection = False

        with adapter.temporarily_ignore_selection(False):
            assert adapter.ignore_selection is False

        assert adapter.ignore_selection is False

    def test_or_composes_with_an_already_true_flag(self):
        """--no-selection already set adapter.ignore_selection = True for the
        whole run — a clone with ignore_selection: false must NOT turn it
        back off underneath an outer --no-selection."""
        adapter = Adapter.__new__(Adapter)
        adapter.ignore_selection = True

        with adapter.temporarily_ignore_selection(False):
            assert adapter.ignore_selection is True

        assert adapter.ignore_selection is True

    def test_restores_true_after_a_true_block_inside_an_already_true_run(self):
        adapter = Adapter.__new__(Adapter)
        adapter.ignore_selection = True

        with adapter.temporarily_ignore_selection(True):
            assert adapter.ignore_selection is True

        assert adapter.ignore_selection is True

    def test_exception_inside_the_block_still_restores(self):
        adapter = Adapter.__new__(Adapter)
        adapter.ignore_selection = False

        with pytest.raises(ValueError):
            with adapter.temporarily_ignore_selection(True):
                assert adapter.ignore_selection is True
                raise ValueError("boom")

        assert adapter.ignore_selection is False


def test_get_items_by_id_all_stale_batch_is_quiet(caplog):
    """Found live 2026-08-31 (fpga_flash, 30 stale registry UUIDs): KiCad
    raises ApiError "none of the requested IDs were found or valid" when the
    WHOLE requested batch is stale. That is the documented "a stale UUID is
    not an error" case — get_items_by_id must return [] WITHOUT a WARNING, or
    the Sub-placements catalog would spam the Log dock after every extract /
    re-read that refreshes a fully-covered placement's registry copper."""
    import logging

    adapter = Adapter.__new__(Adapter)
    adapter._board = MagicMock()
    adapter._board.get_items_by_id.side_effect = RuntimeError(
        "KiCad returned error: none of the requested IDs were found or valid")

    with caplog.at_level(logging.DEBUG, logger="kicadstamp.kicad.adapter"):
        assert adapter.get_items_by_id(["stale-1", "stale-2"]) == []

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_get_items_by_id_genuine_error_still_warns(caplog):
    """Only the all-stale ApiError is benign — a transport/IPC failure is a
    real error and must keep warning (returns [] so the caller degrades to
    "no copper", never crashes)."""
    import logging

    adapter = Adapter.__new__(Adapter)
    adapter._board = MagicMock()
    adapter._board.get_items_by_id.side_effect = RuntimeError("ipc timeout")

    with caplog.at_level(logging.DEBUG, logger="kicadstamp.kicad.adapter"):
        assert adapter.get_items_by_id(["u1"]) == []

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings
    assert any("Failed to look up items by id" in r.getMessage() for r in warnings)


if __name__ == "__main__":
    print("Running kicad tests (without KiCad connection)...")
    test_import()
    test_adapter_has_methods()
    test_init_without_connection()
    print("All kicad tests passed (no real IPC).")