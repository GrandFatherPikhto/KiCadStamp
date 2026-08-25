#!/usr/bin/env python3
"""
Regression test for a bug found on 2026-07-15: executor.py never wrote
original_layer into the JSON operation log, so undo.py always reverted to
the hard‑coded default 'F.Cu' — which happened to match the first run
(the component was indeed on F.Cu), but broke on the second run without an
undo in between (component already on B.Cu — undo incorrectly flipped it back
to F.Cu).

Also: str(BoardLayer.BL_B_Cu) gives the raw number ('34'), not 'B.Cu' —
if the layer were simply taken via str(), the 'B.Cu' in ... check in undo.py
would never work. An explicit conversion is needed.

Updated: now uses a real Config instead of MagicMock, removed the obsolete
target_ref field.
"""
import sys
import json
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import Config
from kicadstamp.placement.executor.base import layer_to_str as _layer_to_str
from kicadstamp.placement.executor import BatchExecutor
from kicadstamp.placement.commands import MoveCommand

MM = 1_000_000


class TestLayerToStr:
    def test_gives_real_strings_not_raw_enum_numbers(self):
        """str(BoardLayer.BL_B_Cu) gives '34', not 'B.Cu' — check that
        _layer_to_str gives what undo.py actually expects."""
        assert _layer_to_str(BoardLayer.BL_F_Cu) == "F.Cu"
        assert _layer_to_str(BoardLayer.BL_B_Cu) == "B.Cu"


class TestOriginalLayerCapture:
    def test_original_layer_written_correctly_when_already_on_back(self):
        """Component already on B.Cu before this run (second run without undo
        in between) — original_layer in the log must be 'B.Cu', not the
        incorrect default 'F.Cu'."""
        fp = MagicMock()
        fp.ref = "C39"
        fp.uuid = "uuid-C39"
        fp.position = Vector2.from_xy(int(50 * MM), int(50 * MM))
        fp.angle_deg = 90.0
        fp.layer = BoardLayer.BL_B_Cu

        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter._board = MagicMock()
        adapter.commit_with_retry.return_value = True

        cfg = Config(
            layer='B.Cu',
            cells={},
            rules=[],
            clone_placements=[],
        )

        executor = BatchExecutor(adapter, cfg, batch_size=10)

        move = MoveCommand(ref="C39", position=Vector2.from_xy(int(51 * MM), int(51 * MM)),
                          angle=Angle.from_degrees(180.0), layer=BoardLayer.BL_B_Cu)

        old_cwd = os.getcwd()
        tmpdir = tempfile.mkdtemp()
        try:
            os.chdir(tmpdir)
            executor.execute_moves([move], check_collisions=False)
            executor.execute_tracks([])
            log_files = list(Path("logs").glob("*.json"))
            assert len(log_files) == 1
            data = json.loads(log_files[0].read_text())
            assert data["moves"][0]["original_layer"] == "B.Cu"
        finally:
            os.chdir(old_cwd)
