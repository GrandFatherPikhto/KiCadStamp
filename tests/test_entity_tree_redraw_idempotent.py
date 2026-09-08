#!/usr/bin/env python3
"""Regression tests for the "duplicate tracks after Entity-through-tree redraw"
bug (plan_2026_08_31_duplicate_tracks_after_tree_redraw.md).

Root cause found on 2026-08-31: the entity-materialization path itself is
SOUND — a materialized Entity clone gets a stable registry_key
(``name:{entity.name}|{cell}|__spoke__|{index}``) and two consecutive redraws
of the same Entity are idempotent (test ``test_entity_only_two_redraws_...``).
The live duplicates came from the fpga cell being placed by TWO placements at
once — the LEGACY clone_placement (anchor ``role:...``/``point:...``) AND the
new Entity (anchor ``name:...``) — which plan the SAME physical tracks under
DIFFERENT registry keys. With ``skip_existing_components=False`` (profile
default) the positional track pre-check (``filter_existing_tracks``) was
gated OFF, so the second placement's identical tracks were created
unconditionally → literal duplicates that persisted.

Fix: the positional pre-check now runs UNCONDITIONALLY in ApplyPipeline's
Phase 3 (it is SKIP-ONLY — never deletes/adopts foreign copper), so any
planned track that already exists at the exact position/net/width/layer is
skipped regardless of its registry key.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from kicadstamp.config import (Config, Cell, Entity, ClonePlacement,
                               TemplateComponentSlot, TemplateTrack,
                               TemplateVia)
from kicadstamp.domain.geometry import Vector2, BoardLayer
from kicadstamp.trees import Tree, TreeNode, TreeAnchor
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator, clone_anchor_id,
)
from kicadstamp.registry import (PlacementRegistry, RegistryEntry,
                                 TrackRegistry, registry_path_for_config,
                                 track_registry_path_for_config)
from kicadstamp.apply_pipeline import ApplyPipeline

MM = 1_000_000


def _make_fp():
    """One live footprint Role/Cluster/Sheet="FPGA" at (65.0, -65.0) mm, with a
    single pad '1' on +3V3_VCCIO (for net_from_role)."""
    fp = MagicMock()
    fp.ref = "U1"
    fp.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    fp.angle_deg = 0.0
    fp.rotation = 0.0

    def _field(field):
        if field in ("Role", "Cluster", "Sheet"):
            return "FPGA"
        return None
    fp.get_field_value = _field

    pad = MagicMock()
    pad.number = "1"
    pad.net_name = "+3V3_VCCIO"
    fp.pads = {"1": pad}

    def _pad(num):
        return fp.pads.get(str(num))
    fp.pad = _pad
    fp.definition = MagicMock(items=[])
    return fp


class _MockAdapter:
    """A minimal live-board stand-in whose track/via lists reflect real
    creation and deletion between runs (reconcile treats the live board as the
    source of truth, so the mock MUST reflect it — see test_registry_integration).
    Tracks and vias live in SEPARATE lists; UUIDs come from ONE monotonic
    counter so a via and a track never collide on a uuid."""

    def __init__(self):
        self.live_tracks = []
        self.live_vias = []
        self._uuid_counter = 0
        self._fp = _make_fp()

    # ── footprint / role / net resolution ───────────────────────────────────
    def get_footprints(self):
        return [self._fp]

    def get_footprint(self, ref):
        return self._fp if ref == "U1" else None

    def get_footprint_by_ref(self, ref):
        return self._fp if ref == "U1" else None

    def get_field_value(self, fp, field):
        if hasattr(fp, "get_field_value"):
            return fp.get_field_value(field)
        return None

    def get_pad_by_number(self, fp, num):
        return fp.pad(num)

    def get_footprint_pads(self, fp):
        return list(fp.pads.values())

    def get_net_by_name(self, net_name):
        n = MagicMock()
        n.name = net_name
        return n

    def get_selected_items(self):
        return []

    # ── live board ──────────────────────────────────────────────────────────
    def get_tracks(self):
        return list(self.live_tracks)

    def get_vias(self):
        return list(self.live_vias)

    def create_track(self, start, end, width_mm, net, layer):
        t = MagicMock()
        t.start = start
        t.end = end
        t.width_mm = width_mm
        t.net_name = net.name if hasattr(net, "name") else net
        t.layer = layer
        t.uuid = None
        return t

    def create_via(self, position, net, drill_mm, diameter_mm):
        v = MagicMock()
        v.position = position
        v.drill_mm = drill_mm
        v.diameter_mm = diameter_mm
        v.net_name = net.name if hasattr(net, "name") else net
        v.uuid = None
        return v

    def create_items(self, items):
        for item in items:
            self._uuid_counter += 1
            item.uuid = f"uuid-{self._uuid_counter}"
        return items

    def commit_with_retry(self, description, work_fn, retries=1):
        work_fn()
        return True

    def remove_by_ids(self, uuids):
        uuids = set(uuids)
        self.live_tracks[:] = [t for t in self.live_tracks
                               if t.uuid not in uuids]
        self.live_vias[:] = [v for v in self.live_vias
                             if v.uuid not in uuids]
        return True

    def refresh_board(self):
        pass

    def temporarily_ignore_selection(self, flag):
        return _TmpCtx()


class _TmpCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _cell():
    """Cell "c" (the fpga analogue): one FPGA slot at local (0,0) + 3 internal
    tracks with net_from_role, exactly like Cell "fpga" in the live profile."""
    return Cell(
        name="c",
        components=[
            TemplateComponentSlot(
                role="FPGA", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0,
            ),
        ],
        tracks=[
            TemplateTrack(start_along_mm=-2.75, start_across_mm=10.738,
                          end_along_mm=-2.75, end_across_mm=11.7255,
                          width_mm=0.2, net_from_role="FPGA",
                          net_from_role_pad="1"),
            TemplateTrack(start_along_mm=10.738, start_across_mm=8.25,
                          end_along_mm=12.6405, end_across_mm=8.25,
                          width_mm=0.2, net_from_role="FPGA",
                          net_from_role_pad="1"),
            TemplateTrack(start_along_mm=7.75, start_across_mm=-11.6598,
                          end_along_mm=9.6802, end_across_mm=-13.59,
                          width_mm=0.2, net_from_role="FPGA",
                          net_from_role_pad="1"),
        ],
    )


def _legacy_clone():
    """The pre-migration clone_placement "FPGA" (cell "c"), anchored by role —
    registry anchor_id ``role:FPGA:...`` — while the Entity "fpga" gets
    ``name:fpga``. Both land the cell at the SAME board position."""
    return ClonePlacement(
        cluster="FPGA", cell="c", xy=(0.0, 0.0),
        anchor_role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA",
        nets={"FPGA": "+3V3_VCCIO"},
    )


def _entity_tree_cfg():
    """Entity "fpga" (cell "c") placed by a tree at the FPGA role anchor."""
    entity = Entity(name="fpga", cell="c", cluster="FPGA",
                    nets={"FPGA": "+3V3_VCCIO"})
    tree = Tree(
        name="fpga",
        anchor=TreeAnchor(role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA"),
        nodes=[
            TreeNode(ref="fpga", kind="placement", xy=(0.0, 0.0),
                     polar=None, rotation=0.0, name=None, group=None),
        ],
    )
    return Config(cells={"c": _cell()}, entities=[entity], trees=[tree])


def _plan_and_create(adapter, cfg, reg_path, clones):
    """Plan tracks for the given clones, reconcile, apply the unconditional
    positional pre-check (the fix), and create what survives. Returns
    (live_count, registry)."""
    calc = ClonePositionCalculator(adapter, cfg, {})
    _placed, _vias, tracks = calc.compute_raw_positions(clones)
    reg = TrackRegistry(adapter, reg_path)
    to_create, to_delete = reg.reconcile(tracks)
    if to_delete:
        adapter.remove_by_ids(to_delete)
    # unconditional pre-check — mirrors the apply_pipeline fix
    from kicadstamp.registry import filter_existing_tracks
    to_create = filter_existing_tracks(to_create, adapter.get_tracks())
    for cmd in to_create:
        net = adapter.get_net_by_name(cmd.net_name)
        t = adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
        adapter.create_items([t])
        adapter.live_tracks.append(t)
        reg.record_created(cmd, t.uuid)
    return len(adapter.get_tracks()), reg


def _cell_with_via():
    """Cell "c2" (the fpga analogue WITH a via): one FPGA slot at local (0,0)
    + one cell-level via with net_from_role — the same shape as a chain-produced
    via of the live profile's FPGA power-bank chains (a via that carries a
    registry_key and, until 2026-09-08, had NO positional protection at all)."""
    return Cell(
        name="c2",
        components=[
            TemplateComponentSlot(
                role="FPGA", offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0,
            ),
        ],
        vias=[
            TemplateVia(offset_along_mm=5.0, offset_across_mm=5.0,
                        net_from_role="FPGA", net_from_role_pad="1",
                        drill_mm=0.3, diameter_mm=0.6),
        ],
    )


def _via_clone_cfg():
    """A plain ClonePlacement of cell "c2" (which carries one via) at the FPGA
    role anchor — a registry-keyed via source equivalent to the chains: spokes
    that produced the live duplicates (any source of a via with a registry_key
    hits the same missing-protection gap)."""
    clone = ClonePlacement(
        cluster="FPGA", cell="c2", xy=(0.0, 0.0),
        anchor_role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA",
        nets={"FPGA": "+3V3_VCCIO"},
    )
    cfg = Config(cells={"c2": _cell_with_via()}, clone_placements=[clone])
    return cfg, [clone]


def _plan_vias_and_create(adapter, cfg, reg_path, clones,
                          pre_reset_entries=False):
    """Plan vias for the given clones and apply them — the via twin of
    _plan_and_create, mirroring the apply_pipeline Phase 2 ordering EXACTLY:
    live vias fetched ONCE -> adopt_matching_unowned -> reconcile ->
    filter_existing_vias (STRICTLY AFTER reconcile) -> create. When
    pre_reset_entries=True the registry starts EMPTY (models a lost/
    overwritten registry JSON between runs — the registry only stores the
    key->uuid index, not the source of truth) while the board keeps its
    copper. Returns (live_via_count, registry)."""
    calc = ClonePositionCalculator(adapter, cfg, {})
    _placed, vias, _tracks = calc.compute_raw_positions(clones)
    reg = PlacementRegistry(adapter, reg_path)
    if pre_reset_entries:
        reg.entries = {}
    live_vias = adapter.get_vias()
    from kicadstamp.registry import adopt_matching_unowned, filter_existing_vias
    adopt_matching_unowned(reg, vias, live_items=live_vias)
    to_create, to_delete = reg.reconcile(vias, live_items=live_vias)
    if to_delete:
        adapter.remove_by_ids(to_delete)
    # unconditional pre-check — mirrors the apply_pipeline Phase 2 fix
    to_create = filter_existing_vias(to_create, adapter.get_vias())
    for cmd in to_create:
        net = adapter.get_net_by_name(cmd.net_name)
        v = adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
        adapter.create_items([v])
        adapter.live_vias.append(v)
        reg.record_created(cmd, v.uuid)
    return len(adapter.get_vias()), reg


# ── Entity-only: two consecutive redraws are already idempotent ────────────────

def test_entity_only_two_redraws_no_growth():
    """The plan's reproduction (single Entity, two consecutive redraws, no
    movement): the materialized Entity gets STABLE registry keys
    (name:fpga|c|__spoke__|N), so the second run is skipped by reconcile — the
    track count does not grow. Confirms the entity-materialization path itself
    was never the culprit."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_")
    reg_path = track_registry_path_for_config(os.path.join(tmpdir, "board.sexp"))
    adapter = _MockAdapter()
    cfg = _entity_tree_cfg()

    clone = materialize_entity_placements(adapter, cfg, {})
    assert len(clone) == 1
    assert clone_anchor_id(clone[0]) == "name:fpga"

    count1, _ = _plan_and_create(adapter, cfg, reg_path, clone)
    count2, _ = _plan_and_create(adapter, cfg, reg_path, clone)
    assert count1 == 3
    assert count2 == 3, "repeated Entity redraw must not grow the track count"


# ── The real bug: legacy clone copper already on the board ────────────────────

def test_entity_redraw_after_legacy_clone_does_not_duplicate():
    """The live-profile state on 2026-08-31: the SAME cell is placed by the
    LEGACY clone_placement "FPGA" (registry anchor role:...) AND the Entity
    "fpga" (anchor name:...). The legacy tracks are already on the board
    (registered under role: keys). An Entity redraw then plans the same
    physical tracks under name: keys — reconcile sees them as new (different
    key), but the UNCONDITIONAL positional pre-check must skip them (identical
    position/net/width/layer), so NO duplicate is created."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_")
    reg_path = track_registry_path_for_config(os.path.join(tmpdir, "board.sexp"))
    adapter = _MockAdapter()
    cfg = _entity_tree_cfg()
    legacy = _legacy_clone()

    # Run 1 (pre-entity state): the legacy clone_placement places the cell.
    count_legacy, _ = _plan_and_create(adapter, cfg, reg_path, [legacy])
    assert count_legacy == 3

    # Run 2 (Entity redraw): the entity is materialized from the tree and
    # planned ALONGSIDE the still-present legacy clone (mirrors _resolve_order,
    # which appends materialized clones to cfg.clone_placements). Both place
    # the same cell at the same position under different keys.
    entity_clone = materialize_entity_placements(adapter, cfg, {})
    assert len(entity_clone) == 1
    count_after, _ = _plan_and_create(
        adapter, cfg, reg_path, [legacy] + entity_clone)
    # The entity's name:fpga tracks must be positionally skipped — the board
    # keeps exactly the legacy 3, NOT 6.
    assert count_after == 3, (
        f"Entity redraw duplicated the cell's tracks: {count_after} live, "
        f"expected 3")


# ── Pipeline gate: the pre-check runs even when skip_existing_components=False ─

def test_track_precheck_runs_when_skip_existing_components_false(monkeypatch):
    """The fix: filter_existing_tracks is applied in Phase 3 UNCONDITIONALLY,
    not only under the cfg.skip_existing_components gate — so a cross-key
    duplicate is prevented even for profiles (like the live one) that leave the
    flag at its default False."""
    cfg = Config(layer='F.Cu', cells={}, chains=[], clone_placements=[],
                 skip_existing_components=False)
    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg)
    pipeline.adapter = MagicMock()
    pipeline.items = []
    pipeline.planner = MagicMock()
    pipeline.planner.plan_item.return_value = []
    pipeline.planner.plan_vias.return_value = []
    pipeline.planner.plan_tracks.return_value = []
    pipeline.all_anchor_ids = set()

    calls = {"precheck": 0}

    def fake_filter_existing_tracks(to_create, live_tracks):
        calls["precheck"] += 1
        return to_create

    monkeypatch.setattr("kicadstamp.apply_pipeline.filter_existing_tracks",
                        fake_filter_existing_tracks)

    from unittest.mock import patch
    with patch("kicadstamp.apply_pipeline.BatchExecutor") as MockExec, \
         patch("kicadstamp.apply_pipeline.PlacementRegistry") as MockReg, \
         patch("kicadstamp.apply_pipeline.TrackRegistry") as MockTrackReg:
        MockReg.return_value.reconcile.return_value = ([], [])
        MockTrackReg.return_value.reconcile.return_value = ([], [])
        MockExec.return_value.execute_moves.return_value = []
        MockExec.return_value.execute_vias.return_value = []
        MockExec.return_value.execute_tracks.return_value = []
        pipeline.adapter.get_tracks.return_value = []
        pipeline.adapter.remove_by_ids.return_value = True
        pipeline._execute()

    assert calls["precheck"] == 1, (
        "positional track pre-check must run even with "
        "skip_existing_components=False")


# ── The via gap (plan_2026_09_08_via_positional_precheck.md) ──────────────────

def test_via_redraw_after_registry_loss_no_duplicate():
    """The plan's §3.2 reproduction: a via whose registry entry is LOST between
    two redraws (JSON overwritten/emptied — the registry is only the key->uuid
    index, not the source of truth) must NOT be recreated while it is still
    physically on the board. Run 1 creates the via and records it; run 2 starts
    from an EMPTY registry with the via still live. Whether adopt_matching_unowned
    (Bug 3, 2026-09-05 — claims the matching unregistered copper before
    reconcile in this exact empty-registry sub-case) or filter_existing_vias
    (this plan) closes it, the end-to-end result is the same: the board keeps
    exactly 1 via, never 2."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_")
    reg_path = registry_path_for_config(os.path.join(tmpdir, "board.sexp"))
    adapter = _MockAdapter()
    cfg, clones = _via_clone_cfg()

    count1, _ = _plan_vias_and_create(adapter, cfg, reg_path, clones)
    assert count1 == 1
    # Run 2 with the registry JSON "lost" (entries emptied in memory).
    count2, _ = _plan_vias_and_create(adapter, cfg, reg_path, clones,
                                      pre_reset_entries=True)
    assert count2 == 1, (
        f"a via whose registry entry was lost must not be recreated while "
        f"still physically present: {count2} live vias, expected 1")


def test_via_stale_registry_entry_not_recreated_when_matching_via_live():
    """The case that ONLY filter_existing_vias can close (the live-shaped one):
    the registry entry under the via's key points at a UUID that is NOT on the
    board anymore (reconcile's "registry has an entry but no such item is on
    the board — registry is out of sync... recreating as if the entry never
    existed" branch — the very "registry/live-board UUID correspondence lost"
    state of the live report), while a positionally-identical via IS live.
    adopt_matching_unowned CANNOT help here (correctly — the key is still
    present when it runs, so it refuses to touch the copper by its SAFE-BY-
    CONSTRUCTION rule), reconcile drops the stale entry and queues a create,
    and only the unconditional positional pre-check sees the matching live via
    and skips it. Without filter_existing_vias this run would create a second,
    literal duplicate."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_")
    reg_path = registry_path_for_config(os.path.join(tmpdir, "board.sexp"))
    adapter = _MockAdapter()
    cfg, clones = _via_clone_cfg()

    # Run 1: create the via and register it (registry key -> uuid-1).
    count1, reg1 = _plan_vias_and_create(adapter, cfg, reg_path, clones)
    assert count1 == 1
    (key,) = reg1.entries.keys()

    # Simulate the "UUID correspondence lost" state: the live via is now a NEW
    # object at the SAME position (uuid-2, unregistered — e.g. a run that
    # created it but crashed before record_created), while the registry still
    # points at the gone uuid-1. The board itself is otherwise unchanged.
    old = adapter.live_vias[0]
    adapter.live_vias = [_make_live_via_like(old, "uuid-2")]
    reg1.entries[key] = RegistryEntry(
        uuid="uuid-1", x_mm=old.position.x / MM, y_mm=old.position.y / MM,
        net=old.net_name, drill_mm=old.drill_mm, diameter_mm=old.diameter_mm)

    # Run 2 with the SAME adapter (via still live) — adopt cannot claim (key
    # present), reconcile drops the stale entry and queues the create, and the
    # positional pre-check is the only thing that stops a duplicate.
    count2, _ = _plan_vias_and_create(adapter, cfg, reg_path, clones)
    assert count2 == 1, (
        f"a via whose registry UUID no longer matches the live board must not "
        f"be recreated while a matching via is physically present: "
        f"{count2} live vias, expected 1")


def _make_live_via_like(template, uuid_str):
    """A copy of a live mock via at the same geometry with a different uuid."""
    v = MagicMock()
    v.position = template.position
    v.net_name = template.net_name
    v.drill_mm = template.drill_mm
    v.diameter_mm = template.diameter_mm
    v.uuid = uuid_str
    return v


def test_via_precheck_runs_when_skip_existing_components_false(monkeypatch):
    """The via analog of the track gate test above: filter_existing_vias is
    applied in Phase 2 UNCONDITIONALLY, not only under the
    cfg.skip_existing_components gate — so a profile (like the live one) that
    leaves the flag at its default False still gets the via positional
    protection."""
    cfg = Config(layer='F.Cu', cells={}, chains=[], clone_placements=[],
                 skip_existing_components=False)
    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg)
    pipeline.adapter = MagicMock()
    pipeline.items = []
    pipeline.planner = MagicMock()
    pipeline.planner.plan_item.return_value = []
    pipeline.planner.plan_vias.return_value = []
    pipeline.planner.plan_tracks.return_value = []
    pipeline.all_anchor_ids = set()

    calls = {"precheck": 0}

    def fake_filter_existing_vias(to_create, live_vias):
        calls["precheck"] += 1
        return to_create

    monkeypatch.setattr("kicadstamp.apply_pipeline.filter_existing_vias",
                        fake_filter_existing_vias)

    from unittest.mock import patch
    with patch("kicadstamp.apply_pipeline.BatchExecutor") as MockExec, \
         patch("kicadstamp.apply_pipeline.PlacementRegistry") as MockReg, \
         patch("kicadstamp.apply_pipeline.TrackRegistry") as MockTrackReg:
        MockReg.return_value.reconcile.return_value = ([], [])
        MockTrackReg.return_value.reconcile.return_value = ([], [])
        MockExec.return_value.execute_moves.return_value = []
        MockExec.return_value.execute_vias.return_value = []
        MockExec.return_value.execute_tracks.return_value = []
        pipeline.adapter.get_tracks.return_value = []
        pipeline.adapter.get_vias.return_value = []
        pipeline.adapter.remove_by_ids.return_value = True
        pipeline._execute()

    assert calls["precheck"] == 1, (
        "positional via pre-check must run even with "
        "skip_existing_components=False")
