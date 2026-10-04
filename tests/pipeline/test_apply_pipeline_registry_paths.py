# tests/pipeline/test_apply_pipeline_registry_paths.py
"""H6 (У5.5, plan §6 «хвост приёмки У5.4»): which two FILES the REAL
``ApplyPipeline._execute()`` reads and writes its copper registries through.

The У5.4 Н3 fix made the on-disk lift and the apply agree on ONE explicit-vs-
default decision (``kicadstamp.utils.paths.registry_paths_for_config``). The
existing equivalence cell exercised that decision through a MIRROR helper
(``_registry_paths``) — the real ``_execute`` path was never driven, so a
mutation that made ``_execute`` ignore an explicit ``registry_path:`` and fall
back to the defaults was invisible (H6 survived). These cells drive the REAL
``_execute`` with a fake adapter and a scripted planner: the explicit files must
be created and the DEFAULT files must stay absent.

The explicit value can arrive two ways, so the table is parametrized by its
SOURCE (rule 35): the loader has already resolved the config key onto the
``RuntimeContext`` (the product path), or the raw ``Config.registry_path`` /
``Config.track_registry_path`` are all ``_execute`` has (``ctx is None`` — the
``author.py`` path). Both must land on the same two files.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.registry import make_registry_key
from kicadstamp.runtime_context import RuntimeContext
from kicadstamp.utils.paths import (registry_path_for_config,
                                    track_registry_path_for_config)

MM = 1_000_000


class _FakeBoard:
    """Only the surface ``_execute`` reads/writes; remove_by_ids() really drops
    items so a prune would be visible."""

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
    """Hands the phase one via and one track with a registry_key; the ORDER and
    the registry I/O under test are ``_execute``'s, not this stand-in's."""

    def __init__(self, vias, tracks):
        self.vias = list(vias)
        self.tracks = list(tracks)
        self._net_trace_vias: list = []
        self._net_trace_tracks: list = []

    def begin_planning(self):
        pass

    def plan_vias(self):
        return list(self.vias)

    def plan_tracks(self):
        return list(self.tracks)


class _FakeBatchExecutor:
    """Creates the copper and records it into the REAL registry, exactly as the
    real executors do after a successful commit."""

    def __init__(self, adapter, config, batch_size=10, operation_log_dir=None):
        self.adapter = adapter

    def execute_moves(self, moves, check_collisions=True, collision_margin_mm=0.2):
        return []

    def execute_vias(self, vias, registry=None):
        for cmd in vias:
            item = Via("v-" + str(len(self.adapter.vias)), cmd.position,
                       cmd.net_name, cmd.drill_mm, cmd.diameter_mm)
            self.adapter.vias.append(item)
            if registry is not None:
                registry.record_created(cmd, item.uuid)
        return []

    def execute_tracks(self, tracks, registry=None):
        for cmd in tracks:
            item = Track("t-" + str(len(self.adapter.tracks)), cmd.start, cmd.end,
                         cmd.net_name, cmd.width_mm, cmd.layer)
            self.adapter.tracks.append(item)
            if registry is not None:
                registry.record_created(cmd, item.uuid)
        return []


def _via_cmd(x_mm, y_mm, key):
    return ViaCommand(position=Vector2.from_xy(int(x_mm * MM), int(y_mm * MM)),
                      drill_mm=0.3, diameter_mm=0.6, net_name="GND",
                      owner_ref="U1", registry_key=key)


def _track_cmd(x0_mm, y0_mm, x1_mm, y1_mm, key):
    return TrackCommand(start=Vector2.from_xy(int(x0_mm * MM), int(y0_mm * MM)),
                        end=Vector2.from_xy(int(x1_mm * MM), int(y1_mm * MM)),
                        width_mm=0.25, net_name="GND", layer=BoardLayer.BL_F_Cu,
                        owner_ref="U1", registry_key=key)


def _run(config_path, cfg, ctx):
    key = make_registry_key("pad:1", "cell", None, 0)
    pipeline = ApplyPipeline(config_path, preloaded_cfg=cfg, preloaded_ctx=ctx)
    pipeline.adapter = _FakeBoard()
    pipeline.items = []
    pipeline.all_anchor_ids = {"pad:1"}
    pipeline.planner = _ScriptedPlanner([_via_cmd(10, 20, key)],
                                        [_track_cmd(10, 20, 11, 20, key)])
    with patch("kicadstamp.apply_pipeline.BatchExecutor", _FakeBatchExecutor):
        pipeline._execute()


@pytest.mark.parametrize("explicit_from", ["ctx", "cfg"])
def test_execute_uses_the_explicit_registry_paths(tmp_path, explicit_from):
    """H6: an explicit ``registry_path:``/``track_registry_path:`` must be the
    files the REAL apply writes — whether the loader put them on the
    ``RuntimeContext`` (the product path) or they still live on the ``Config``
    (``ctx is None``). The default files must stay ABSENT, or the explicit value
    was ignored."""
    root = tmp_path / "root.sexp"
    root.write_text("", encoding="utf-8")
    explicit_via = tmp_path / "alt" / "via.registry.json"
    explicit_trk = tmp_path / "alt" / "trk.registry.json"

    if explicit_from == "cfg":
        cfg = Config(layer="F.Cu", cells={}, chains=[], clone_placements=[],
                     registry_path=str(explicit_via),
                     track_registry_path=str(explicit_trk))
        ctx = None
    else:
        cfg = Config(layer="F.Cu", cells={}, chains=[], clone_placements=[])
        ctx = RuntimeContext(registry_path=str(explicit_via),
                             track_registry_path=str(explicit_trk),
                             operation_log_dir=str(tmp_path / "operational"))

    _run(str(root), cfg, ctx)

    assert explicit_via.exists(), "the explicit via registry was not written"
    assert explicit_trk.exists(), "the explicit track registry was not written"
    assert not Path(registry_path_for_config(str(root))).exists(), (
        "the DEFAULT via registry was written — the explicit value was ignored")
    assert not Path(track_registry_path_for_config(str(root))).exists(), (
        "the DEFAULT track registry was written — the explicit value was ignored")
