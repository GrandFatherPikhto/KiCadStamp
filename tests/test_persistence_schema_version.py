#!/usr/bin/env python3
"""Tests for persistence schema_version (P2, 2026-08-25 architecture audit item
"no persistence versioning"): registry.json/.tracks.registry.json and
operation_*.json now carry a schema_version field so a future incompatible
format change is detected loudly instead of being silently mis-parsed."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.persistence import (
    REGISTRY_SCHEMA_VERSION,
    OPERATION_LOG_SCHEMA_VERSION,
    check_schema_version,
)
from kicadstamp.registry import (
    RegistryEntry,
    TrackRegistryEntry,
    load_registry,
    save_registry,
    load_track_registry,
    save_track_registry,
)
from kicadstamp.placement.executor.operation_logger import OperationLogger
from kicadstamp.undo import undo_last_operation


def _via_entry(uuid="v1"):
    return RegistryEntry(uuid=uuid, x_mm=1.0, y_mm=2.0, net="GND",
                         drill_mm=0.3, diameter_mm=0.6)


def _track_entry(uuid="t1"):
    return TrackRegistryEntry(uuid=uuid, start_x_mm=0.0, start_y_mm=0.0,
                              end_x_mm=1.0, end_y_mm=1.0, width_mm=0.25,
                              net="GND", layer="F.Cu")


class TestCheckSchemaVersion:
    def test_missing_field_is_accepted_as_legacy(self):
        check_schema_version(None, REGISTRY_SCHEMA_VERSION, "x.json", "registry")

    def test_matching_version_is_accepted(self):
        check_schema_version(1, REGISTRY_SCHEMA_VERSION, "x.json", "registry")

    def test_future_version_raises(self):
        with pytest.raises(ValueError, match="schema_version 2"):
            check_schema_version(2, REGISTRY_SCHEMA_VERSION, "x.json", "registry")


class TestRegistrySchemaVersion:
    def test_save_registry_writes_schema_version(self, tmp_path):
        path = tmp_path / "r.registry.json"
        save_registry(str(path), {"pad:1|c|__spoke__|0": _via_entry()})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == REGISTRY_SCHEMA_VERSION
        assert "pad:1|c|__spoke__|0" in data

    def test_load_registry_accepts_legacy_flat_file(self, tmp_path):
        path = tmp_path / "r.registry.json"
        path.write_text(json.dumps({
            "pad:1|c|__spoke__|0": {
                "uuid": "v1", "x_mm": 1.0, "y_mm": 2.0, "net": "GND",
                "drill_mm": 0.3, "diameter_mm": 0.6,
            },
        }), encoding="utf-8")
        entries = load_registry(str(path))
        assert list(entries) == ["pad:1|c|__spoke__|0"]

    def test_load_registry_filters_schema_version_key(self, tmp_path):
        path = tmp_path / "r.registry.json"
        save_registry(str(path), {"pad:1|c|__spoke__|0": _via_entry()})
        entries = load_registry(str(path))
        assert "schema_version" not in entries
        assert "pad:1|c|__spoke__|0" in entries

    def test_load_registry_rejects_future_version(self, tmp_path):
        path = tmp_path / "r.registry.json"
        path.write_text(json.dumps({
            "schema_version": 999,
            "pad:1|c|__spoke__|0": {
                "uuid": "v1", "x_mm": 1.0, "y_mm": 2.0, "net": "GND",
                "drill_mm": 0.3, "diameter_mm": 0.6,
            },
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version 999"):
            load_registry(str(path))

    def test_track_registry_roundtrip_and_schema_version(self, tmp_path):
        path = tmp_path / "t.tracks.registry.json"
        save_track_registry(str(path), {"pad:1|c|__spoke__|0": _track_entry()})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == REGISTRY_SCHEMA_VERSION
        entries = load_track_registry(str(path))
        assert "schema_version" not in entries
        assert "pad:1|c|__spoke__|0" in entries

    def test_load_track_registry_accepts_legacy_flat_file(self, tmp_path):
        path = tmp_path / "t.tracks.registry.json"
        path.write_text(json.dumps({
            "pad:1|c|__spoke__|0": {
                "uuid": "t1", "start_x_mm": 0.0, "start_y_mm": 0.0,
                "end_x_mm": 1.0, "end_y_mm": 1.0, "width_mm": 0.25,
                "net": "GND", "layer": "F.Cu",
            },
        }), encoding="utf-8")
        entries = load_track_registry(str(path))
        assert list(entries) == ["pad:1|c|__spoke__|0"]


class TestOperationLogSchemaVersion:
    def test_write_operation_log_has_schema_version(self, tmp_path):
        logger = OperationLogger(str(tmp_path))
        written = logger.write_operation_log(
            [{"ref": "J1", "x": 72.5, "y": 61.0, "angle": 270.0}], [], [])
        assert written is not None
        data = json.loads(written.read_text(encoding="utf-8"))
        assert data["schema_version"] == OPERATION_LOG_SCHEMA_VERSION

    def test_undo_accepts_legacy_log_without_schema_version(self, tmp_path):
        log = tmp_path / "operation.json"
        log.write_text(json.dumps({
            "moves": [{"ref": "C1", "uuid": "uuid-1", "original_layer": "F.Cu",
                       "original_position": {"x": 1000000, "y": 2000000},
                       "original_angle_deg": 90.0}],
            "created_vias": [{"uuid": "via-1"}],
            "created_tracks": [{"uuid": "track-1"}],
        }), encoding="utf-8")
        adapter = MagicMock()
        fp = MagicMock()
        fp.layer = BoardLayer.BL_F_Cu  # equals original_layer -> no flip
        adapter.get_footprint_by_id.return_value = fp
        adapter.remove_by_id.return_value = True
        assert undo_last_operation(log, adapter=adapter) is True

    def test_undo_rejects_future_version(self, tmp_path):
        log = tmp_path / "operation.json"
        log.write_text(json.dumps({
            "schema_version": 999,
            "moves": [],
            "created_vias": [],
            "created_tracks": [],
        }), encoding="utf-8")
        adapter = MagicMock()
        with pytest.raises(ValueError, match="schema_version 999"):
            undo_last_operation(log, adapter=adapter)
