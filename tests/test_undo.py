# tests/test_undo.py
"""Tests for kicadstamp.undo.undo_last_operation — dependency injection of
the board adapter (P0-5 of the 2026-08-25 architecture audit: the function
used to construct its own KiCadBoardAdapter, making it untestable without a
live KiCad)."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from kipy.board_types import BoardLayer

from kicadstamp.undo import undo_last_operation


def _write_operation_log(tmp_path: Path) -> Path:
    log = tmp_path / "operation.json"
    log.write_text(json.dumps({
        "moves": [{
            "ref": "C1",
            "uuid": "uuid-1",
            "original_layer": "F.Cu",
            "original_position": {"x": 1000000, "y": 2000000},
            "original_angle_deg": 90.0,
        }],
        "created_vias": [{"uuid": "via-1"}],
        "created_tracks": [{"uuid": "track-1"}],
    }), encoding="utf-8")
    return log


def test_undo_last_operation_uses_injected_adapter(tmp_path):
    log = _write_operation_log(tmp_path)

    adapter = MagicMock()
    fp = MagicMock()
    fp.layer = BoardLayer.BL_F_Cu  # equals original_layer -> no flip
    adapter.get_footprint_by_id.return_value = fp
    adapter.remove_by_id.return_value = True

    assert undo_last_operation(log, adapter=adapter) is True

    adapter.refresh_board.assert_called_once()
    adapter.get_footprint_by_id.assert_called_with("uuid-1")
    adapter.update_items.assert_called_once_with([fp])
    assert [c.args[0] for c in adapter.remove_by_id.call_args_list] == ["via-1", "track-1"]
    assert not log.exists()  # operation file removed after a successful undo
