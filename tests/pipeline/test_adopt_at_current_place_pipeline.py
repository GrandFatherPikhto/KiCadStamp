# tests/pipeline/test_adopt_at_current_place_pipeline.py
"""Доделка 1а of plan_2026_10_07_adopt_at_current_place: the at-current-place
adoption WIRED INTO ``ApplyPipeline`` — through the REAL ``_execute`` / ``_dry_run``
on a fake adapter, not through the module.

Why a pipeline cell: the module cells prove the pass DECIDES right; only these
prove it is CALLED, at the right point of the run (before the first move, after
the registries exist) and against the real registry pair. Mutations «_execute
never calls the pass» (N1) and «the dry run never calls it» (N2) survive the
module cells alone — a deleted call would silently bring the trail back.

The rig: the cluster's SHIFT is told by the PLAN (the scripted planner hands the
command the moved record produces), not by a real move — the pass reads the
PRE-move board either way. ``remove_by_ids`` really drops the copper, so the
trail is only absent when reconcile really deleted it.
"""
from __future__ import annotations

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Cell, ClonePlacement, Config, TemplateComponentSlot,
                               TemplateVia)
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.placement.services.clone_position_calculator import (
    clone_anchor_id,
    clone_registry_identity,
)
from kicadstamp.registry import load_registry, make_registry_key
from kicadstamp.runtime_context import RuntimeContext

from tests.fakes.format3 import det_uuid

MM = 1_000_000
CELL = "dac_buf"
CLUSTER = "DAC_BUF"
# The live slot of the cell's anchor_role, and the place the cluster moves to.
FPGA_LIVE = (10.0, 10.0)
NEW_PLACE = (20.0, 20.0)


class _Fp(Footprint):
    def __init__(self, ref, role, cluster, x_mm, y_mm, nets=()):
        super().__init__(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                         position=Vector2.from_xy_mm(x_mm, y_mm),
                         layer=BoardLayer.BL_F_Cu)
        self.role = role
        self.cluster = cluster
        self.sheet_path_uuids = ()
        self.pads = [SimpleNamespace(number=str(i), net_name=n)
                     for i, n in enumerate(nets, start=1)]


class _FakeBoard:
    """Only the surface ``_execute``/``_dry_run`` and the pass read."""

    def __init__(self, footprints=(), vias=(), tracks=()):
        self.footprints = list(footprints)
        self.vias = list(vias)
        self.tracks = list(tracks)

    def get_field_value(self, fp, name):
        return fp.role if name == ROLE_FIELD_NAME else fp.cluster

    def get_footprints(self):
        return list(self.footprints)

    def get_footprint(self, ref):
        return next((f for f in self.footprints if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(getattr(fp, "pads", ()) or ())

    def get_pad_by_number(self, fp, number):
        return next((p for p in (getattr(fp, "pads", ()) or ())
                     if str(p.number) == str(number)), None)

    def get_selected_items(self):
        return []

    def get_vias(self):
        return list(self.vias)

    def get_tracks(self):
        return list(self.tracks)

    def temporarily_ignore_selection(self, flag):
        return nullcontext()

    def remove_by_ids(self, ids):
        gone = set(ids)
        self.vias = [v for v in self.vias if v.uuid not in gone]
        self.tracks = [t for t in self.tracks if t.uuid not in gone]

    def refresh_board(self):
        pass

    def reread_footprints_by_id(self, ids):
        pass


class _ScriptedPlanner:
    """The plan under test: no moves (the shift is a PLAN fact here), the exact
    via command the shifted record produces, no tracks."""

    def __init__(self, vias=()):
        self.vias = list(vias)
        # The plan_copper()/phase helpers read these two caches directly
        # (net-trace adoption); empty = "no net-trace command in this plan".
        self._net_trace_vias = []
        self._net_trace_tracks = []

    def begin_planning(self):
        pass

    def plan_items(self, items):
        """No moves at all — the SHIFT is expressed by the plan's via."""
        return []

    def plan_item(self, item):
        return []

    def plan_vias(self):
        return list(self.vias)

    def plan_tracks(self):
        return []


class _FakeBatchExecutor:
    """Creates the copper on the fake board and records it in the REAL registry —
    the truthful board+registry pair reconcile's next step reads."""

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
        return []


def _cell_obj() -> Cell:
    return Cell(name=CELL, uuid=det_uuid(f"cells:{CELL}"),
                layer="F.Cu", anchor_role="FPGA",
                components=[TemplateComponentSlot(role="FPGA", offset_along_mm=0.0,
                                                  offset_across_mm=0.0)],
                vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                                  net="N", drill_mm=0.3, diameter_mm=0.6)])


def _clone() -> ClonePlacement:
    return ClonePlacement(cluster=CLUSTER, cell=CELL, xy=(0.0, 0.0), name="dac0",
                          uuid=det_uuid("clone_placements:dac0"))


def _record_key(clone, cfg) -> str:
    anchor_id, _cell, key_part = clone_registry_identity(clone, cfg.cells)
    return make_registry_key(anchor_id, key_part, None, 0)


def _pipeline(tmp_path, *, dry_run=False):
    """A real ApplyPipeline whose adapter and planner are stand-ins; registry
    paths point at tmp files through the RuntimeContext, so the REAL registries
    persist and reconcile for real."""
    cfg = Config(layer="F.Cu", cells={CELL: _cell_obj()})
    ctx = RuntimeContext(
        registry_path=str(tmp_path / "registry" / "vias.json"),
        track_registry_path=str(tmp_path / "tracks" / "tracks.json"),
        operation_log_dir=str(tmp_path / "operational"),
    )
    pipeline = ApplyPipeline("board.sexp", dry_run=dry_run,
                             preloaded_cfg=cfg, preloaded_ctx=ctx)
    clone = _clone()
    pipeline.adapter = _FakeBoard(
        footprints=[_Fp("IC9", "FPGA", CLUSTER, *FPGA_LIVE, nets=["N"])],
        # The record's copper sits EXACTLY where the record puts it TODAY.
        vias=[Via("v_old", Vector2.from_xy_mm(*FPGA_LIVE), "N", 0.3, 0.6)])
    pipeline.items = [SimpleNamespace(kind="clone", obj=clone, label="dac0",
                                      anchor_ref=None)]
    pipeline.planner = _ScriptedPlanner(
        vias=[ViaCommand(position=Vector2.from_xy_mm(*NEW_PLACE), drill_mm=0.3,
                         diameter_mm=0.6, net_name="N", owner_ref="IC9",
                         registry_key=_record_key(clone, cfg))])
    pipeline.all_anchor_ids = {clone_anchor_id(clone)}
    return pipeline, clone, cfg


_AT = lambda x_mm, y_mm: (round(x_mm, 4), round(y_mm, 4))  # noqa: E731


def _via_at(items, place):
    return [v for v in items
            if _AT(v.position.x / MM, v.position.y / MM) == _AT(*place)]


def _registry_bytes(tmp_path):
    """(via, track) registry file bytes, or None when the file does not exist —
    "nothing was written" is then visible as None -> None, never as a crash."""
    out = []
    for rel in (("registry", "vias.json"), ("tracks", "tracks.json")):
        path = tmp_path / rel[0] / rel[1]
        out.append(path.read_bytes() if path.exists() else None)
    return tuple(out)


def test_a_shifted_cluster_leaves_no_copper_at_the_old_place(tmp_path):
    """1а-1: empty registry, the record's copper lies at its place at the CURRENT
    poses, then the cluster shifts (the plan puts the record's key at a new
    place). After the REAL run the copper is gone from the OLD place, exactly one
    copy sits at the new one, and the registry key points at that moved object.
    Mutation N1 «_execute never calls the pass» brings the trail back and turns
    this red."""
    pipeline, clone, cfg = _pipeline(tmp_path)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline._execute()

    assert _via_at(pipeline.adapter.vias, FPGA_LIVE) == [], \
        "the old place still carries copper — the trail is back"
    moved = _via_at(pipeline.adapter.vias, NEW_PLACE)
    assert len(moved) == 1
    entries = load_registry(str(tmp_path / "registry" / "vias.json"))
    assert entries[_record_key(clone, cfg)].uuid == moved[0].uuid


def test_the_dry_run_hands_the_bound_copper_over(tmp_path):
    """Part Б of plan_2026_10_08_narrowing_net_traces_cost: the dry run KEEPS its
    adoption report as ``pipeline.at_current_place``, with the (kind, key, item)
    it decided on — that is what «Select cell» then selects, so the CURRENT-place
    rule is never run a second time. The registry files stay untouched (it is a
    dry run: the report only reports).

    Mutation: stop storing it and the consumer falls back to the PLANNED place —
    exactly the live defect (0 by geometry after a read, copper on the board)."""
    pipeline, clone, cfg = _pipeline(tmp_path, dry_run=True)
    before = _registry_bytes(tmp_path)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline._dry_run()

    adoption = pipeline.at_current_place
    assert adoption is not None and adoption.adopted == 1
    kind, key, item = adoption.bound[0]
    assert kind == "via"
    assert key == _record_key(clone, cfg)
    assert item.uuid == "v_old", "the LIVE item at its CURRENT place"
    assert _registry_bytes(tmp_path) == before


def test_the_dry_run_counts_would_adopt_and_writes_nothing(tmp_path):
    """1а-2: the SAME wiring in the dry branch — the report says «would adopt 1
    record(s)» and neither registry file is touched. Mutation N2 «the dry run
    never calls the pass» drops the line and turns this red."""
    pipeline, _clone_, _cfg = _pipeline(tmp_path, dry_run=True)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        first = "\n".join(pipeline._dry_run())

    assert "Adopt at current place: would adopt 1 record(s)" in first
    before = _registry_bytes(tmp_path)

    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        second = "\n".join(pipeline._dry_run())

    assert second == first
    assert _registry_bytes(tmp_path) == before
