#!/usr/bin/env python3
"""Guards for the stale-live-list defect after a prune/delete
(plan_2026_10_02_stale_live_list_after_prune.md, Claude's probe
techdocs/handoff/claude/probe_2026_10_02_registry_rename.py).

WHAT WENT WRONG. ApplyPipeline._execute() fetches the live via/track list ONCE,
feeds it to reconcile() (whose to_delete also carries pruned keys),
adapter.remove_by_ids() and the positional pre-check (filter_existing_vias /
filter_existing_tracks). After remove_by_ids() the pre-check was still given the
PRE-deletion list, so the just-deleted copper counted as "already on the board":
a command whose registry KEY changed while its geometry did not — renaming a cell
or a thermal via array — was skipped right after its copper was deleted, and the
apply left the board empty until the next run.

These cells drive the REAL ApplyPipeline._execute() with the REAL
PlacementRegistry/TrackRegistry (tmp registry paths on the RuntimeContext) and a
fake adapter; only BatchExecutor is faked (its batch/commit machinery is not the
subject — the ORDER under test lives entirely in _execute()). The planner is
scripted to hand the phase exactly the plan a rename produces (same geometry, a
new registry key) and, for the control, a genuinely re-positioned plan; it does
NOT re-implement the delete/create sequence.

Functions are named by PROPERTY (rule 37); the C-number of the plan's guard table
lives in the docstring only. Rename cells are parametrized (rule 35): the
key-only-vs-geometry table has two rows — a spoke cell and a thermal via array —
crossed with the two copper kinds (via, track), plus the "geometry really
changed" control for each kind.
"""
import uuid
from unittest.mock import patch

import pytest

# Import order matters (see tests/placement/test_via_positional_precheck.py):
# kicadstamp.registry imports placement.commands at module level, which pulls the
# placement package __init__ back into registry — importing commands FIRST
# avoids the circular-import trap.
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.registry import make_registry_key

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.runtime_context import RuntimeContext

MM = 1_000_000
_KEY_NET = "GND"
_VIA_DRILL_MM = 0.3
_VIA_DIAMETER_MM = 0.6
_TRACK_WIDTH_MM = 0.25


# ── board double: only the surface _execute() reads ──────────────────────────

class _FakeBoard:
    """In-memory board. get_vias()/get_tracks() hand out a COPY (like the real
    adapter) and remove_by_ids() really drops the items, so the difference
    between the pre- and post-deletion live list is a real one."""

    def __init__(self):
        self.vias: list = []
        self.tracks: list = []

    def get_vias(self):
        return list(self.vias)

    def get_tracks(self):
        return list(self.tracks)

    def remove_by_ids(self, ids):
        gone = set(ids)
        self.vias = [v for v in self.vias if v.uuid not in gone]
        self.tracks = [t for t in self.tracks if t.uuid not in gone]

    def refresh_board(self):
        pass

    def get_footprints(self):
        return []

    def reread_footprints_by_id(self, ids):
        pass


class _ScriptedPlanner:
    """Hands the phase the exact plan under test; the ORDER is _execute()'s."""

    def __init__(self):
        self.vias: list = []
        self.tracks: list = []
        self._net_trace_vias: list = []
        self._net_trace_tracks: list = []

    def begin_planning(self):
        pass

    def plan_vias(self):
        return list(self.vias)

    def plan_tracks(self):
        return list(self.tracks)


class _FakeBatchExecutor:
    """BatchExecutor stand-in: creates the copper on the fake board and records
    it in the REAL registry, exactly as via_executor/track_executor do after a
    successful commit — so reconcile()'s next run sees a truthful board +
    registry pair. Nothing here decides WHAT to create (that is the phase's)."""

    def __init__(self, adapter, config, batch_size=10, operation_log_dir=None):
        self.adapter = adapter

    def execute_moves(self, moves, check_collisions=True, collision_margin_mm=0.2):
        return []

    def execute_vias(self, vias, registry=None):
        for cmd in vias:
            item = Via(str(uuid.uuid4()), cmd.position, cmd.net_name,
                       cmd.drill_mm, cmd.diameter_mm)
            self.adapter.vias.append(item)
            if registry is not None:
                registry.record_created(cmd, item.uuid)
        return []

    def execute_tracks(self, tracks, registry=None):
        for cmd in tracks:
            item = Track(str(uuid.uuid4()), cmd.start, cmd.end, cmd.net_name,
                         cmd.width_mm, cmd.layer)
            self.adapter.tracks.append(item)
            if registry is not None:
                registry.record_created(cmd, item.uuid)
        return []


# ── builders ─────────────────────────────────────────────────────────────────

def _via_cmd(x_mm, y_mm, key, net_name=_KEY_NET, owner="U1"):
    return ViaCommand(position=Vector2.from_xy(int(x_mm * MM), int(y_mm * MM)),
                      drill_mm=_VIA_DRILL_MM, diameter_mm=_VIA_DIAMETER_MM,
                      net_name=net_name, owner_ref=owner, registry_key=key)


def _track_cmd(x0_mm, y0_mm, x1_mm, y1_mm, key, net_name=_KEY_NET, owner="U1"):
    return TrackCommand(start=Vector2.from_xy(int(x0_mm * MM), int(y0_mm * MM)),
                        end=Vector2.from_xy(int(x1_mm * MM), int(y1_mm * MM)),
                        width_mm=_TRACK_WIDTH_MM, net_name=net_name,
                        layer=BoardLayer.BL_F_Cu, owner_ref=owner,
                        registry_key=key)


def _pipeline(tmp_path):
    """A real ApplyPipeline whose adapter and planner are stand-ins. Registry
    paths are pointed at tmp files through the RuntimeContext, so the REAL
    registries persist and reconcile for real."""
    cfg = Config(layer='F.Cu', cells={}, chains=[], clone_placements=[])
    ctx = RuntimeContext(
        registry_path=str(tmp_path / "registry" / "vias.json"),
        track_registry_path=str(tmp_path / "tracks" / "tracks.json"),
        operation_log_dir=str(tmp_path / "operational"),
    )
    pipeline = ApplyPipeline("board.sexp", preloaded_cfg=cfg, preloaded_ctx=ctx)
    pipeline.adapter = _FakeBoard()
    pipeline.items = []
    pipeline.planner = _ScriptedPlanner()
    return pipeline


def _assert_one_copy_per_position(live_items, expected_positions, coord_of):
    """Exactly one item per planned position — not 0 (the defect: copper deleted
    and not redrawn) and not 2 (a duplicate)."""
    assert len(live_items) == len(expected_positions), (
        f"expected exactly {len(expected_positions)} copper item(s) on the board "
        f"after the key-only change, found {len(live_items)} "
        "(0 = deleted and not redrawn, more = duplicated)")
    actual = [coord_of(item) for item in live_items]
    for pos in expected_positions:
        n = sum(1 for got in actual
                if abs(got[0] - pos[0]) < 1e-6 and abs(got[1] - pos[1]) < 1e-6)
        assert n == 1, f"position {pos} has {n} copies, expected exactly 1"
    assert len(set(actual)) == len(expected_positions), (
        "two copper items landed on the same position (duplicate)")


_VIA_COORD = lambda item: (item.position.x / MM, item.position.y / MM)  # noqa: E731
_TRACK_COORD = lambda item: (item.start.x / MM, item.start.y / MM)  # noqa: E731


# ── the key-changed / geometry-same table (rule 35) ──────────────────────────

_RENAME_CELLS = [
    pytest.param("pad:17", "pad:17", "cap_pair", "cap_pair_renamed",
                 id="cell-rename-key-only"),
    pytest.param("thermal:TV_A", "thermal:TV_B", "thermal_via", "thermal_via",
                 id="thermal-array-rename-key-only"),
]


@pytest.mark.parametrize("anchor_old, anchor_new, template_old, template_new",
                         _RENAME_CELLS)
def test_renaming_a_via_key_with_identical_geometry_leaves_exactly_one_copy(
        tmp_path, anchor_old, anchor_new, template_old, template_new):
    """С1 (plan_2026_10_02_stale_live_list_after_prune.md). Renaming a cell or a
    thermal via array changes the registry key but not the geometry: apply #2
    must DELETE the old via and DRAW the new one exactly once. On the base the
    pre-check saw the pre-deletion list, so the copper was deleted and NOT
    redrawn — the board came back with 0 (verified red by the rig)."""
    pipeline = _pipeline(tmp_path)
    planner = pipeline.planner
    positions = [(10.0, 20.0), (11.0, 20.0), (12.0, 20.0)]

    def plan(anchor, template):
        return [_via_cmd(x, y, key=make_registry_key(anchor, template, None, i))
                for i, (x, y) in enumerate(positions)]

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline.all_anchor_ids = {anchor_old}
        planner.vias = plan(anchor_old, template_old)
        pipeline._execute()
        assert len(pipeline.adapter.vias) == len(positions), (
            "the first apply must actually place the copper (precondition)")

        pipeline.all_anchor_ids = {anchor_new}
        planner.vias = plan(anchor_new, template_new)
        pipeline._execute()

    _assert_one_copy_per_position(pipeline.adapter.vias, positions, _VIA_COORD)


@pytest.mark.parametrize("anchor_old, anchor_new, template_old, template_new",
                         _RENAME_CELLS)
def test_renaming_a_track_key_with_identical_geometry_leaves_exactly_one_copy(
        tmp_path, anchor_old, anchor_new, template_old, template_new):
    """С2 (plan_2026_10_02_stale_live_list_after_prune.md). The track half of С1:
    same key-only rename, same exactly-one-copy requirement, no 0 and no 2."""
    pipeline = _pipeline(tmp_path)
    planner = pipeline.planner
    starts = [(10.0, 20.0), (11.0, 20.0), (12.0, 20.0)]

    def plan(anchor, template):
        return [_track_cmd(x, y, x + 1.0, y,
                           key=make_registry_key(anchor, template, None, i))
                for i, (x, y) in enumerate(starts)]

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline.all_anchor_ids = {anchor_old}
        planner.tracks = plan(anchor_old, template_old)
        pipeline._execute()
        assert len(pipeline.adapter.tracks) == len(starts), (
            "the first apply must actually place the copper (precondition)")

        pipeline.all_anchor_ids = {anchor_new}
        planner.tracks = plan(anchor_new, template_new)
        pipeline._execute()

    _assert_one_copy_per_position(pipeline.adapter.tracks, starts, _TRACK_COORD)


# ── control: the normal delete+create path must not regress ──────────────────

def test_changed_geometry_still_deletes_the_old_and_creates_the_new_via(tmp_path):
    """С3 (plan_2026_10_02_stale_live_list_after_prune.md). The control: when the
    geometry REALLY changed under the same registry key, the old via is deleted
    and the new one created — this path was already correct on the base and must
    stay correct, so the mutation that removes the new helper cannot be mistaken
    for a broad break."""
    pipeline = _pipeline(tmp_path)
    planner = pipeline.planner
    key = make_registry_key("pad:17", "cap_pair", None, 0)
    old_pos, new_pos = (10.0, 20.0), (30.0, 20.0)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline.all_anchor_ids = {"pad:17"}
        planner.vias = [_via_cmd(*old_pos, key)]
        pipeline._execute()
        old_uuid = pipeline.adapter.vias[0].uuid

        planner.vias = [_via_cmd(*new_pos, key)]
        pipeline._execute()

    live = pipeline.adapter.vias
    assert len(live) == 1, "changed geometry must leave exactly one via"
    assert live[0].uuid != old_uuid, "the old via must have been deleted"
    assert _VIA_COORD(live[0]) == pytest.approx(new_pos)


def test_changed_geometry_still_deletes_the_old_and_creates_the_new_track(tmp_path):
    """С4 (plan_2026_10_02_stale_live_list_after_prune.md). The track control of
    С3 — same no-regression requirement for the track phase."""
    pipeline = _pipeline(tmp_path)
    planner = pipeline.planner
    key = make_registry_key("pad:17", "cap_pair", None, 0)
    old_start, new_start = (10.0, 20.0), (30.0, 20.0)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline.all_anchor_ids = {"pad:17"}
        planner.tracks = [_track_cmd(*old_start, old_start[0] + 1.0, old_start[1], key)]
        pipeline._execute()
        old_uuid = pipeline.adapter.tracks[0].uuid

        planner.tracks = [_track_cmd(*new_start, new_start[0] + 1.0, new_start[1], key)]
        pipeline._execute()

    live = pipeline.adapter.tracks
    assert len(live) == 1, "changed geometry must leave exactly one track"
    assert live[0].uuid != old_uuid, "the old track must have been deleted"
    assert _TRACK_COORD(live[0]) == pytest.approx(new_start)
