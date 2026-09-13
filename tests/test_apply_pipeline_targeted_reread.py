#!/usr/bin/env python3
"""Phase 1's targeted re-read — what apply_pipeline re-reads between items, and
what it deliberately does not (plan_2026_09_13_targeted_footprint_reread Э2,
watchdogs Э3.7/Э3.8).

Before this, ``_execute()`` called ``refresh_board()`` before every item but the
first, which dropped the whole footprint cache so ``get_footprints()`` pulled all
325 footprints over IPC to learn the ~8 positions the previous item had moved
(measured: 2.39 s of a 4.06 s placement). Now only the footprints the previous
item's MoveCommands named are re-read — and the data still comes from KiCad,
never from the local object the move mutated.

The three "nothing to re-read" cases are all deliberate and all covered here:
no moves at all; a ref MoveExecutor itself skips; and a footprint with no uuid
to ask KiCad about (which falls back to a full read instead of guessing).

Style: the MagicMock-adapter + patched-executor harness of
tests/test_apply_pipeline_coordinate_placements.py and
tests/test_entity_tree_redraw_idempotent.py — _execute() is driven directly, so
connect/validate/resolve never run.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config
from kicadstamp.domain.geometry import Angle, BoardLayer, Vector2
from kicadstamp.placement.commands import MoveCommand


def _item(label):
    """A dependency-order item, as Phase 1 reads it: label (logging) and
    anchor_ref."""
    return SimpleNamespace(label=label, anchor_ref=None)


def _move(ref):
    return MoveCommand(ref=ref, position=Vector2.from_xy(0, 0),
                       angle=Angle.from_degrees(0.0), layer=BoardLayer.BL_F_Cu)


def _dto(ref, uuid_str):
    """Only the two DTO attributes the uuid resolution reads."""
    return SimpleNamespace(ref=ref, uuid=uuid_str)


def _pipeline(items, adapter):
    cfg = Config(layer='F.Cu', cells={}, chains=[], clone_placements=[])
    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg)
    pipeline.adapter = adapter
    pipeline.items = items
    pipeline.planner = MagicMock()
    pipeline.planner.plan_vias.return_value = []
    pipeline.planner.plan_tracks.return_value = []
    pipeline.all_anchor_ids = set()
    return pipeline


def _adapter_with(footprints):
    adapter = MagicMock()
    adapter.get_footprints.return_value = list(footprints)
    return adapter


def _run_execute(pipeline, move_results=()):
    """Run _execute() with the executors and registries faked out (the pattern
    the other apply_pipeline tests use). move_results is the list of
    failed-refs lists the executor returns, one per execute_moves() call."""
    remaining = list(move_results)
    with patch("kicadstamp.apply_pipeline.BatchExecutor") as MockExec, \
         patch("kicadstamp.apply_pipeline.PlacementRegistry") as MockReg, \
         patch("kicadstamp.apply_pipeline.TrackRegistry") as MockTrackReg:
        MockReg.return_value.reconcile.return_value = ([], [])
        MockTrackReg.return_value.reconcile.return_value = ([], [])
        MockExec.return_value.execute_moves.side_effect = (
            lambda moves, **kw: remaining.pop(0) if remaining else [])
        MockExec.return_value.execute_vias.return_value = []
        MockExec.return_value.execute_tracks.return_value = []
        pipeline.adapter.get_vias.return_value = []
        pipeline.adapter.get_tracks.return_value = []
        pipeline.adapter.remove_by_ids.return_value = True
        pipeline._execute()


# ── Э3.7 — no moves, no re-reading, and no per-item full refresh ─────────────

class TestItemsWithoutMovesCostNothing:
    def test_an_item_with_no_moves_is_not_reread_and_causes_no_full_refresh(self):
        """Э3.7. Not an optimisation on a hunch: if the item touched nothing, the
        board is exactly as the cache describes it, so there is nothing to
        update. The one remaining refresh_board() is the phase-boundary reload
        before the vias phase (apply_pipeline.py), which is not in the loop."""
        adapter = _adapter_with([_dto("R1", "uuid-R1")])
        pipeline = _pipeline([_item("first"), _item("second")], adapter)
        pipeline.planner.plan_item.side_effect = [[_move("R1")], []]

        _run_execute(pipeline)

        assert [c.args[0] for c in adapter.reread_footprints_by_id.call_args_list] == [
            ["uuid-R1"]]
        assert adapter.refresh_board.call_count == 1, (
            "Phase 1 must not refill the whole footprint cache per item")

    def test_an_item_with_no_moves_does_not_force_a_full_read_for_the_next_one(self):
        """Э3.7, the half the sibling above structurally cannot see.

        full_refresh_needed is consumed on the NEXT loop iteration (the
        `if full_refresh_needed: refresh_board()` at the top of the for), so an
        empty item can only be observed with an item AFTER it. With three items
        (a move, then no moves, then no moves) a "no moves" answer that wrongly
        set the flag — `return [], True` instead of `return [], False` — would
        make the third iteration call refresh_board() for a board the cache
        already describes. The board must be read exactly once, at the phase
        boundary before the vias phase."""
        adapter = _adapter_with([_dto("R1", "uuid-R1")])
        pipeline = _pipeline(
            [_item("first"), _item("no-moves"), _item("third")], adapter)
        pipeline.planner.plan_item.side_effect = [[_move("R1")], [], []]

        _run_execute(pipeline)

        assert adapter.refresh_board.call_count == 1, (
            "an item with no moves must not arm a full board read later")
        assert [c.args[0] for c in adapter.reread_footprints_by_id.call_args_list] == [
            ["uuid-R1"]]

    def test_every_ref_the_item_moved_is_reread_once_and_in_order(self):
        adapter = _adapter_with([
            _dto("R1", "uuid-R1"), _dto("R2", "uuid-R2"), _dto("R3", "uuid-R3"),
        ])
        pipeline = _pipeline([_item("first"), _item("second")], adapter)
        pipeline.planner.plan_item.side_effect = [
            [_move("R2"), _move("R1"), _move("R2")], []]

        _run_execute(pipeline)

        assert [c.args[0] for c in adapter.reread_footprints_by_id.call_args_list] == [
            ["uuid-R2", "uuid-R1"]]


# ── Э3.8 — a failed move is re-read too, and before the next item plans ──────

class TestFailedMovesAreStillReread:
    def test_a_failed_move_is_reread_before_the_next_item_is_planned(self):
        """Э3.8. execute_moves() reports a whole BATCH as failed, and we cannot
        know how much of it KiCad applied before refusing — only KiCad can say.
        So the refs of a failed move go into the re-read as well, and the re-read
        happens BEFORE the next item is planned (the order is the whole point:
        the next item must read the post-move board)."""
        adapter = _adapter_with([_dto("R1", "uuid-R1")])
        pipeline = _pipeline([_item("first"), _item("second")], adapter)
        events = []

        def plan(item):
            events.append(("plan", item.label))
            return [_move("R1")] if item.label == "first" else []

        def reread(uuids):
            events.append(("reread", list(uuids)))

        pipeline.planner.plan_item.side_effect = plan
        adapter.reread_footprints_by_id.side_effect = reread

        _run_execute(pipeline, move_results=[["R1"]])

        assert events == [
            ("plan", "first"),
            ("reread", ["uuid-R1"]),
            ("plan", "second"),
        ]
        assert adapter.refresh_board.call_count == 1


# ── The two cases where a uuid cannot stand for the move ────────────────────

class TestWhenTheMoveCannotBePinnedToUuids:
    def test_a_moved_footprint_without_a_uuid_forces_a_full_refresh(self):
        """No uuid means KiCad cannot be asked about it, and the local object is
        not evidence of what KiCad did — so the next item gets a full read
        instead of a targeted one that would silently miss this move."""
        adapter = _adapter_with([_dto("R1", "")])
        pipeline = _pipeline([_item("first"), _item("second")], adapter)
        pipeline.planner.plan_item.side_effect = [[_move("R1")], []]

        _run_execute(pipeline)

        assert adapter.reread_footprints_by_id.call_count == 0
        assert adapter.refresh_board.call_count == 2, (
            "the phase-boundary reload plus the honest full fallback")

    def test_a_ref_the_cache_does_not_know_is_not_reread(self):
        """MoveExecutor skips a ref it cannot find in the same list and logs it,
        so nothing was written for it — a re-read would be a round trip for a
        move that never happened."""
        adapter = _adapter_with([])          # R1 is not in the cache at all
        pipeline = _pipeline([_item("first"), _item("second")], adapter)
        pipeline.planner.plan_item.side_effect = [[_move("R1")], []]

        _run_execute(pipeline)

        assert adapter.reread_footprints_by_id.call_count == 0
        assert adapter.refresh_board.call_count == 1
