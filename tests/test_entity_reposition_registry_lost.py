#!/usr/bin/env python3
"""BUG 3 diagnostic — "first-time entity reposition when the copper is NOT in
the registry under its current key" (2026-09-05, live repro by Denis on the
fresh profile ``profiles/3ch-awg-tia-v103-test``).

Live repro: a NEW profile was created (its ``tracks/config.tracks.registry.json``
did not exist yet) pointing at a board that ALREADY carried KiCadStamp copper
under the OLD anchor (created/registered by the source profile). The FPGA was
moved and the tree redrawn. Result observed live and confirmed by the operation
log: all 72 tracks were planned as to_create ("Запланировано треков: 72,
реально к созданию: 72"), old copper stayed at the old FPGA position AND new
copper appeared at the new position -> duplication / orphaned leftovers.

Root cause (confirmed by mock below):
  * The registry is the ONLY ownership record. reconcile() deletes strictly by
    key -> a live segment whose UUID is not registered under the current run's
    keys can never be deleted.
  * The positional pre-check (filter_existing_tracks) is SKIP-ONLY by design
    (never deletes, never adopts foreign copper) — so it cannot help delete the
    old-position copper either (and must NOT: see the safety test).

The mock reproduces the mechanism with the cell "c" / Entity "fpga" harness
(the same one used by tests/test_entity_tree_redraw_idempotent.py): 3 tracks
materialize from the entity at the anchor's live position.

Three tests:
  1. test_repro_first_reposition_empty_registry_duplicates   — asserts the
     CURRENT (buggy) symptom: live count 3 -> 6 (old 3 orphaned + 3 created).
     This is the regression snapshot; the FIX (pending Denis's choice) must
     invert it to expect 3.
  2. test_control_registered_reposition_moves_copper          — proves the
     normal path works when the registry IS present under the same path
     (old deleted by UUID, new created, live stays 3).
  3. test_safety_foreign_copper_never_deleted_on_reposition   — the guard-rail
     ANY fix must keep: copper the tool never registered (hand/foreign) must
     survive a reposition untouched (deleted=0, foreign kept + 3 new own).
"""
import os
import sys
import tempfile

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from unittest.mock import MagicMock

from kicadstamp.config import Config, Cell, Entity, TemplateComponentSlot, TemplateTrack
from kicadstamp.domain.geometry import Vector2
from kicadstamp.trees import Tree, TreeNode, TreeAnchor
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.registry import (TrackRegistry, track_registry_path_for_config,
                                 filter_existing_tracks, adopt_matching_unowned)

MM = 1_000_000


def _make_fp(x_mm: float, y_mm: float):
    """One live footprint Role/Cluster/Sheet="FPGA" at (x_mm, y_mm) mm with a
    single pad '1' on +3V3_VCCIO (for net_from_role). Its `.position` is mutated
    between runs to simulate the user moving the FPGA in KiCad."""
    fp = MagicMock()
    fp.ref = "U1"
    fp.position = Vector2.from_xy(x_mm * MM, y_mm * MM)
    fp.angle_deg = 0.0
    fp.rotation = 0.0

    def _field(field):
        return "FPGA" if field in ("Role", "Cluster", "Sheet") else None
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
    """Minimal live-board stand-in whose track list reflects real creation and
    deletion between runs (reconcile treats the live board as the source of
    truth). Identical to the harness in test_entity_tree_redraw_idempotent.py."""

    def __init__(self, fp):
        self.live_tracks = []
        self._fp = fp

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

    def get_tracks(self):
        return list(self.live_tracks)

    def get_vias(self):
        return []

    def create_track(self, start, end, width_mm, net, layer):
        t = MagicMock()
        t.start = start
        t.end = end
        t.width_mm = width_mm
        t.net_name = net.name if hasattr(net, "name") else net
        t.layer = layer
        t.uuid = None
        return t

    def create_items(self, items):
        for item in items:
            item.uuid = f"uuid-{len(self.live_tracks)}"
        return items

    def commit_with_retry(self, description, work_fn, retries=1):
        work_fn()
        return True

    def remove_by_ids(self, uuids):
        self.live_tracks[:] = [t for t in self.live_tracks
                               if t.uuid not in set(uuids)]
        return True

    def refresh_board(self):
        pass

    def temporarily_ignore_selection(self, flag):
        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return _Ctx()

    def seed_live_track(self, start, end, width_mm, net_name, layer="F.Cu"):
        """Inject a raw live segment (e.g. hand-drawn/foreign copper) WITHOUT
        any registry entry — the exact thing reconcile cannot see."""
        net = self.get_net_by_name(net_name)
        t = self.create_track(start, end, width_mm, net, layer)
        self.create_items([t])
        self.live_tracks.append(t)
        return t


def _cell():
    """Cell "c" (the fpga analogue): one FPGA slot at local (0,0) + 3 internal
    tracks with net_from_role, exactly like Cell "fpga" in the live profile."""
    return Cell(
        name="c",
        components=[
            TemplateComponentSlot(
                role="FPGA", offset_along_mm=0.0, offset_across_mm=0.0,
                angle_deg=0.0,
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


def _plan_and_create(adapter, cfg, reg_path):
    """Plan tracks for the materialized entity at the fp's CURRENT live
    position, reconcile against the registry at reg_path, apply the positional
    pre-check, and create what survives. Returns
    (live_count, created, deleted, first_registry_key)."""
    clones = materialize_entity_placements(adapter, cfg, {})
    calc = ClonePositionCalculator(adapter, cfg, {})
    _placed, _vias, tracks = calc.compute_raw_positions(clones)
    reg = TrackRegistry(adapter, reg_path)
    # Mirror the Bug 3 fix in apply_pipeline (Phase 3): adopt planned-but-
    # unregistered tracks already sitting exactly at their planned position
    # BEFORE reconcile, so a later reposition deletes instead of orphaning.
    adopt_matching_unowned(reg, tracks, live_items=adapter.get_tracks())
    to_create, to_delete = reg.reconcile(tracks)
    if to_delete:
        adapter.remove_by_ids(to_delete)
    to_create = filter_existing_tracks(to_create, adapter.get_tracks())
    for cmd in to_create:
        net = adapter.get_net_by_name(cmd.net_name)
        t = adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
        adapter.create_items([t])
        adapter.live_tracks.append(t)
        reg.record_created(cmd, t.uuid)
    first_key = tracks[0].registry_key if tracks else None
    return len(adapter.get_tracks()), len(to_create), len(to_delete), first_key


# ── 1. BUG 3 boundary: MOVE as the FIRST action in an empty-registry profile ─

def test_move_first_empty_registry_still_orphans_old_copper():
    """BUG 3's accepted limitation (denis, 2026-09-05): if the VERY FIRST action
    in a fresh-profile (empty registry) is already a move of the anchor, the old
    copper no longer matches the plan's geometry (it sits at the old position),
    so even the new always-on adoption (adopt_matching_unowned) cannot claim it:
    reconcile sees no entries, the pre-check finds no IDENTICAL geometry at the
    NEW position -> all 3 are created while the old 3 stay -> live 3 -> 6.

    This is why the fix is paired with the GUI warning "registry empty — adopt
    current copper first": the correct flow is to run one redraw WITHOUT moving
    (test_first_plain_redraw_registers_then_move_relocates below), which
    registers the existing copper, and only then move. Root-cure (registry
    follows the board across profile copies) is deliberately out of scope.
    """
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_bug3_")
    old_reg = track_registry_path_for_config(os.path.join(tmpdir, "old_profile.sexp"))
    new_reg = track_registry_path_for_config(os.path.join(tmpdir, "new_profile.sexp"))
    fp = _make_fp(65.0, -65.0)
    adapter = _MockAdapter(fp)
    cfg = _entity_tree_cfg()

    # Previous session under the OLD profile created+registered copper at OLD pos.
    live_old, created_old, deleted_old, first_key = _plan_and_create(
        adapter, cfg, old_reg)
    assert live_old == 3
    assert created_old == 3 and deleted_old == 0
    assert first_key == "name:fpga|c|__spoke__|0", first_key

    # NEW profile: empty registry. User moved the FPGA (+10,+10 mm) as the FIRST
    # action, tree redraw.
    fp.position = Vector2.from_xy(75.0, -55.0)
    live_new, created_new, deleted_new, _ = _plan_and_create(adapter, cfg, new_reg)

    # ── accepted limitation: old copper is unrecoverable by geometry ───────
    assert created_new == 3, created_new
    assert deleted_new == 0, deleted_new       # registry empty -> nothing pruned
    assert live_new == 6, (
        f"move-first with an empty registry must stay documented as live=6 "
        f"(3 orphaned + 3 new), got {live_new}")


# ── 1b. THE FIX: a first NO-MOVE redraw registers, then the move relocates ────

def test_first_plain_redraw_registers_then_move_relocates():
    """The Bug 3 fix (2026-09-05, adopt_matching_unowned): on the first redraw
    in a fresh-profile (empty registry) where the board still matches the plan
    (nothing has moved yet), the planned-but-unregistered tracks already sitting
    exactly at their planned position are CLAIMED into the registry instead of
    merely skipped by the positional pre-check. The subsequent move then works
    through the normal UUID path (delete old + create new) — no orphan, no
    duplicate.

    Step 1 (no move):  adoption registers the 3 existing segments under
        name:fpga keys -> reconcile skips them, nothing is created, live stays 3
        and new_reg now owns the copper.
    Step 2 (FPGA moved): reconcile sees the owned keys at the OLD position,
        position changed -> deletes the 3 old UUIDs + creates 3 at the new
        position -> live stays 3.
    (Before the fix this test fails: Step 1 only skipped without registering,
    so Step 2 had nothing to delete -> live 6.)"""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_bug3_fix_")
    old_reg = track_registry_path_for_config(os.path.join(tmpdir, "old_profile.sexp"))
    new_reg = track_registry_path_for_config(os.path.join(tmpdir, "new_profile.sexp"))
    fp = _make_fp(65.0, -65.0)
    adapter = _MockAdapter(fp)
    cfg = _entity_tree_cfg()

    # Old profile previously created+registered the copper at the OLD position.
    _plan_and_create(adapter, cfg, old_reg)
    assert len(adapter.get_tracks()) == 3

    # Step 1 — new profile, EMPTY registry, FPGA NOT moved yet: a plain redraw
    # must ADOPT the 3 existing segments (register them), not duplicate them.
    live1, created1, deleted1, _ = _plan_and_create(adapter, cfg, new_reg)
    assert live1 == 3, (
        f"first no-move redraw must not duplicate: live={live1}, expected 3")
    assert created1 == 0, created1
    assert deleted1 == 0, deleted1
    # The adoption must have registered the copper under the new profile's keys.
    reg = TrackRegistry(adapter, new_reg)
    assert len(reg.entries) == 3, (
        f"first no-move redraw must register the existing copper: "
        f"new_reg has {len(reg.entries)} entries, expected 3")

    # Step 2 — move the FPGA (+10,+10 mm) and redraw: the now-owned old copper
    # must be deleted by UUID and recreated at the new position (live stays 3).
    fp.position = Vector2.from_xy(75.0, -55.0)
    live2, created2, deleted2, _ = _plan_and_create(adapter, cfg, new_reg)
    assert deleted2 == 3, deleted2
    assert created2 == 3, created2
    assert live2 == 3, (
        f"move after first registration must relocate (live 3), got {live2}")


# ── 2. Control: same registry path -> normal move works (delete old + create) ──

def test_control_registered_reposition_moves_copper():
    """When the registry IS present at the SAME path as the plan, moving the
    anchor works through the normal UUID path: reconcile finds the old entries,
    sees the position changed (live match fails), deletes the old UUIDs and
    recreates at the new position -> live stays 3. Proves the registry is the
    ONLY thing that makes reposition safe; Bug 3 is purely 'registry not found'."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_bug3_ctl_")
    reg = track_registry_path_for_config(os.path.join(tmpdir, "profile.sexp"))
    fp = _make_fp(65.0, -65.0)
    adapter = _MockAdapter(fp)
    cfg = _entity_tree_cfg()

    live1, created1, deleted1, _ = _plan_and_create(adapter, cfg, reg)
    assert (live1, created1, deleted1) == (3, 3, 0)

    fp.position = Vector2.from_xy(75.0, -55.0)   # move +10,+10 mm
    live2, created2, deleted2, _ = _plan_and_create(adapter, cfg, reg)
    assert created2 == 3, created2
    assert deleted2 == 3, deleted2
    assert live2 == 3, (
        f"registered reposition must keep live==3, got {live2}")


# ── 3. Safety guard-rail: foreign (unregistered) copper must never be deleted ──

def test_safety_foreign_copper_never_deleted_on_reposition():
    """The skip-only principle any Bug 3 fix MUST keep: a live segment the tool
    never registered (hand-drawn / foreign, here a different net at a nearby
    position) is indistinguishable from orphaned tool copper by geometry alone.
    A reposition must NOT delete it: after the move it stays on the board
    (deleted=0) and only the 3 own tracks at the new position are created."""
    tmpdir = tempfile.mkdtemp(prefix="kicadstamp_bug3_safe_")
    reg = track_registry_path_for_config(os.path.join(tmpdir, "profile.sexp"))
    fp = _make_fp(65.0, -65.0)
    adapter = _MockAdapter(fp)
    cfg = _entity_tree_cfg()

    # Hand-drawn copper near the old anchor, never registered, different net.
    adapter.seed_live_track(Vector2.from_xy(60.0 * MM, -60.0 * MM),
                            Vector2.from_xy(61.0 * MM, -61.0 * MM),
                            0.3, "HAND_NET")
    assert len(adapter.get_tracks()) == 1

    fp.position = Vector2.from_xy(75.0, -55.0)
    live, created, deleted, _ = _plan_and_create(adapter, cfg, reg)
    assert created == 3, created
    assert deleted == 0, deleted
    assert live == 4, (   # 1 foreign kept + 3 own at the new position
        f"foreign copper must survive a reposition untouched, live={live}")
    live_nets = {t.net_name for t in adapter.get_tracks()}
    assert "HAND_NET" in live_nets, "foreign HAND_NET segment was deleted!"
