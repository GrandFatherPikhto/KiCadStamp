#!/usr/bin/env python3
"""П.7 tests: the operation-log directory is bound to the config
(operation_log_dir, resolved relative to the config file like log_file) instead
of silently depending on the process CWD. Covers:

  - load_config(): operation_log_dir is resolved relative to the config file;
    absent -> None (apply now falls back to <config-dir>/operational,
    2026-09-04, plan root_metadata_path_defaults — not CWD DEFAULT_LOG_DIR).
  - BatchExecutor/OperationLogger (writing side): honours config.operation_log_dir
    and writes operation_*.json there, not into CWD logs/.
  - the config-derived default paths (registry/tracks/log/operational) now live
    in SUBFOLDERS next to the config (plan root_metadata_path_defaults).
"""
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import Config, load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.utils.paths import (default_log_file_for_config,
                                    default_operation_log_dir_for_config,
                                    registry_path_for_config,
                                    track_registry_path_for_config)


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p
from kicadstamp.placement.executor import BatchExecutor
from kicadstamp.placement.executor.operation_logger import OperationLogger
from kicadstamp.placement.commands import MoveCommand

MM = 1_000_000


class TestLoadConfigResolvesOperationLogDir:
    def test_absent_defaults_to_none(self, tmp_path):
        config_file = _write(tmp_path, "board.sexp",
                             {"layer": "F.Cu", "cells": {}, "rules": []})
        cfg, ctx = load_config(str(config_file))
        # P1-3: Config keeps the RAW value; the RESOLVED path lives on ctx.
        assert cfg.operation_log_dir is None
        assert ctx.operation_log_dir is None

    def test_relative_resolved_against_config_file(self, tmp_path):
        """operation_log_dir is relative to the config file itself (like
        registry_path/log_file) — the single source of truth for apply/undo."""
        config_file = _write(tmp_path, "board.sexp",
                             {"layer": "F.Cu", "cells": {}, "rules": [],
                              "operation_log_dir": "logs"})
        cfg, ctx = load_config(str(config_file))
        # P1-3: Config keeps the RAW value; the RESOLVED path lives on ctx.
        assert cfg.operation_log_dir == "logs"
        assert ctx.operation_log_dir == str(tmp_path / "logs")


class TestOperationLoggerCreatesNestedDir:
    """Regression: OperationLogger must create the whole nested path (parents=True),
    not just the leaf dir — otherwise profiles/<profile>/logs/operational fails
    with FileNotFoundError when profiles/<profile>/logs does not exist yet."""

    def test_nested_log_dir_is_created_recursively(self, tmp_path):
        nested = tmp_path / "profiles" / "power" / "logs" / "operational"
        OperationLogger(str(nested))  # constructor creates the whole nested dir
        assert nested.is_dir()

    def test_write_operation_log_creates_nested_dir(self, tmp_path):
        nested = tmp_path / "profiles" / "power" / "logs" / "operational"
        logger = OperationLogger(str(nested))
        written = logger.write_operation_log(
            [{"ref": "J1", "x": 72.5, "y": 61.0, "angle": 270.0}],
            [],
            [],
        )
        assert written is not None
        assert written.parent == nested
        assert written.is_file()


class TestBatchExecutorUsesConfigOperationLogDir:
    """Writing side of П.7: BatchExecutor must route operation logs to
    operation_log_dir when set — regardless of the process CWD. P1-3: the
    resolved directory is passed explicitly (it lives on RuntimeContext, not
    Config), so BatchExecutor no longer reads config.operation_log_dir."""

    @staticmethod
    def _fp(ref):
        fp = MagicMock()
        fp.ref = ref
        fp.uuid = f"uuid-{ref}"
        fp.position = Vector2.from_xy(int(50 * MM), int(50 * MM))
        fp.angle_deg = 90.0
        fp.layer = BoardLayer.BL_B_Cu
        return fp

    @staticmethod
    def _adapter(fp):
        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter._board = MagicMock()
        adapter.commit_with_retry.return_value = True
        return adapter

    def test_writes_to_nested_config_bound_dir(self, tmp_path):
        """Same as the CWD test but with a deeply nested, not-yet-existing
        operation_log_dir — the FileNotFoundError regression from
        profiles/power/logs/operational."""
        bound = tmp_path / "a" / "b" / "c" / "logs" / "operational"
        cfg = Config(
            layer='B.Cu',
            cells={},
            chains=[],
            clone_placements=[],
        )
        adapter = self._adapter(self._fp("C39"))
        executor = BatchExecutor(adapter, cfg, batch_size=10,
                                 operation_log_dir=str(bound))

        move = MoveCommand(ref="C39", position=Vector2.from_xy(int(51 * MM), int(51 * MM)),
                          angle=Angle.from_degrees(180.0), layer=BoardLayer.BL_B_Cu)

        executor.execute_moves([move], check_collisions=False)
        executor.execute_tracks([])
        bound_files = list(bound.glob("operation_*.json"))
        assert len(bound_files) == 1

    def test_writes_to_config_bound_dir_not_cwd(self, tmp_path):
        bound = tmp_path / "config_logs"
        cfg = Config(
            layer='B.Cu',
            cells={},
            chains=[],
            clone_placements=[],
        )
        adapter = self._adapter(self._fp("C39"))
        executor = BatchExecutor(adapter, cfg, batch_size=10,
                                 operation_log_dir=str(bound))

        move = MoveCommand(ref="C39", position=Vector2.from_xy(int(51 * MM), int(51 * MM)),
                          angle=Angle.from_degrees(180.0), layer=BoardLayer.BL_B_Cu)

        old_cwd = os.getcwd()
        tmpdir = tempfile.mkdtemp()
        try:
            os.chdir(tmpdir)
            executor.execute_moves([move], check_collisions=False)
            executor.execute_tracks([])
            # Nothing must leak into the CWD logs/ dir...
            assert list(Path("logs").glob("*.json")) == []
            # ...and the config-bound dir is the single place the log lands.
            bound_files = list(bound.glob("operation_*.json"))
            assert len(bound_files) == 1
            data = json.loads(bound_files[0].read_text())
            assert data["moves"][0]["original_layer"] == "B.Cu"
        finally:
            os.chdir(old_cwd)


class TestConfigDerivedPathDefaults:
    """2026-09-04 (plan root_metadata_path_defaults): the config-derived
    default paths (via/track registry, log file, operation-log dir) now point
    into SUBFOLDERS next to the config — the targets apply/undo/GUI fall back
    to when the config does not set them explicitly."""

    def test_registry_and_track_defaults_live_in_subfolders(self, tmp_path):
        config_file = tmp_path / "sub" / "board.sexp"
        assert registry_path_for_config(str(config_file)) == str(
            tmp_path / "sub" / "registry" / "board.registry.json")
        assert track_registry_path_for_config(str(config_file)) == str(
            tmp_path / "sub" / "tracks" / "board.tracks.registry.json")

    def test_registry_default_keeps_config_stem_so_configs_do_not_collide(self, tmp_path):
        a = tmp_path / "a.sexp"
        b = tmp_path / "b.sexp"
        assert registry_path_for_config(str(a)) != registry_path_for_config(str(b))
        assert track_registry_path_for_config(str(a)) != track_registry_path_for_config(str(b))

    def test_log_and_operation_defaults_are_config_relative(self, tmp_path):
        config_file = tmp_path / "sub" / "board.sexp"
        assert default_log_file_for_config(str(config_file)) == str(
            tmp_path / "sub" / "logs" / "actions.log")
        assert default_operation_log_dir_for_config(str(config_file)) == str(
            tmp_path / "sub" / "operational")
