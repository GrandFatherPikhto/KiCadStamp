#!/usr/bin/env python3
"""Tests for PositionTracker.moves_from_placed() — the shared
PlacedComponentInfo -> MoveCommand conversion, including owner_ref
carry-over (2026-08-26, handoff tag_cluster_overtag)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import PlacedComponentInfo
from kicadstamp.placement.services.position_tracker import PositionTracker


def test_owner_ref_carried_1to1_into_move_command():
    """owner_ref (the placement level that resolved each component) must
    survive PlacedComponentInfo -> MoveCommand untouched — placer.py::
    _tag_cluster filters on it to avoid re-tagging nested sub-cell components
    with the top placement's Cluster."""
    adapter = MagicMock()
    tracker = PositionTracker(adapter, target_layer=BoardLayer.BL_F_Cu, skip_existing=False)

    placed = [
        PlacedComponentInfo(ref="C1", dest=Vector2.from_xy(0, 0), angle_deg=0.0,
                            layer=BoardLayer.BL_F_Cu, owner_ref="ch1_pif_dvdd"),
        PlacedComponentInfo(ref="C2", dest=Vector2.from_xy(0, 0), angle_deg=0.0,
                            layer=BoardLayer.BL_F_Cu, owner_ref="top"),
    ]
    moves = tracker.moves_from_placed(placed)

    assert len(moves) == 2
    assert moves[0].owner_ref == "ch1_pif_dvdd"
    assert moves[1].owner_ref == "top"


def test_owner_ref_default_empty_for_manual_info():
    """A PlacedComponentInfo that never set owner_ref (ManualSpoke/manual
    moves) defaults to '' — not a crash, and never matches a real
    clone placement's effective name in _tag_cluster's filter."""
    adapter = MagicMock()
    tracker = PositionTracker(adapter, target_layer=BoardLayer.BL_F_Cu, skip_existing=False)

    info = PlacedComponentInfo(ref="R1", dest=Vector2.from_xy(0, 0), angle_deg=0.0)
    assert info.owner_ref == ""

    moves = tracker.moves_from_placed([info])
    assert moves[0].owner_ref == ""
