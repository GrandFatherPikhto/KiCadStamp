#!/usr/bin/env python3
"""
Integration test for the placement registry end‑to‑end: ManualPositionCalculator
(generates registry_key) -> PlacementRegistry.reconcile() -> execution.
Four sequential "runs" on the same (mocked) registry state between calls —
simulating repeated tool launches.

IMPORTANT: since reconcile() started checking via existence on the LIVE board
(adapter.get_vias()) instead of blindly trusting the JSON — the mock must
reflect the real "board" state between runs: if a run created a via with UUID X,
the next run must see that via in get_vias(), otherwise reconcile() (correctly!)
treats it as stale and recreates it. Previously this test mocked get_vias() as
always empty and relied on the old (now non‑existent) "trust JSON" behaviour —
silently failing under the new architecture until the tests were run and noticed.
"""
import sys
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2, Angle
from kipy.board_types import Pad, Net
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import (
    Config, ManualSpoke, Cell,
    TemplateVia, TemplateComponentSlot, Rule
)
from kicadstamp.placement.services.manual_position_calculator import ManualPositionCalculator
from kicadstamp.registry import PlacementRegistry
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _make_live_via(uuid_str, x_mm, y_mm, net_name, drill_mm, diameter_mm):
    """Live via on the "board" — exactly the fields that PlacementRegistry._live_matches checks."""
    via = MagicMock()
    via.uuid = uuid_str
    via.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    via.net_name = net_name
    via.drill_mm = drill_mm
    via.diameter_mm = diameter_mm
    return via


def _build_cfg(power_via_offset_across=-1.5):
    cell = Cell(
        name="t",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=power_via_offset_across,
                          drill_mm=0.3, diameter_mm=0.6)],
    )
    spoke = ManualSpoke(pad="17", cell="t", rotation_deg=0.0)
    return Config(
        layer='B.Cu',
        cells={"t": cell},
        rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[spoke])],
    )


def test_registry_full_cycle_across_two_runs():
    tmpdir = tempfile.mkdtemp()
    reg_path = os.path.join(tmpdir, "test.registry.json")

    ic1 = MagicMock()
    ic1.definition.items = [_make_pad("17", 50.0, 50.0, "+3V3")]

    # Live vias "on the board" — a mutable list reflecting the state between
    # runs (creation/deletion). adapter.get_vias() always reads THIS list.
    live_vias = []

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == 'IC1' else None
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprints.return_value = []  # no components -- only spoke‑level vias
    adapter.get_vias.side_effect = lambda: list(live_vias)

    def _remove_by_id(uuid_str):
        live_vias[:] = [v for v in live_vias if v.uuid != uuid_str]
        return True
    adapter.remove_by_id.side_effect = _remove_by_id

    # --- Run 1: clean registry, via must be created ---
    cfg1 = _build_cfg(power_via_offset_across=-1.5)
    calc1 = ManualPositionCalculator(adapter, cfg1)
    _, vias1, _ = calc1.compute_raw_positions(cfg1.rules)
    assert len(vias1) == 1
    key = vias1[0].registry_key
    expected_key = f"pad:17|t|{SPOKE_LEVEL_ROLE_PLACEHOLDER}|0"
    assert key == expected_key

    reg1 = PlacementRegistry(adapter, reg_path)
    to_create1, to_delete1 = reg1.reconcile(vias1)
    assert len(to_create1) == 1
    assert to_delete1 == []
    reg1.record_created(vias1[0], "uuid-abc")
    # Simulate real via creation on the board -- otherwise the next run
    # (with the new live‑board‑as‑source‑of‑truth logic) will see "not on board"
    # and honestly recreate it, as it should.
    live_vias.append(_make_live_via("uuid-abc", 50.0, 48.5, "+3V3", 0.3, 0.6))

    # --- Run 2: same config, same registry, via is really placed -- nothing to create ---
    calc2 = ManualPositionCalculator(adapter, cfg1)
    _, vias2, _ = calc2.compute_raw_positions(cfg1.rules)
    reg2 = PlacementRegistry(adapter, reg_path)
    to_create2, to_delete2 = reg2.reconcile(vias2)
    assert len(to_create2) == 0, "config unchanged, via is really placed -- no need to recreate"
    assert to_delete2 == []

    # --- Run 3: user changed offset_across_mm -- old via must be deleted by UUID, new one marked for creation ---
    cfg3 = _build_cfg(power_via_offset_across=-3.0)  # different value!
    calc3 = ManualPositionCalculator(adapter, cfg3)
    _, vias3, _ = calc3.compute_raw_positions(cfg3.rules)
    reg3 = PlacementRegistry(adapter, reg_path)
    to_create3, to_delete3 = reg3.reconcile(vias3)
    assert len(to_create3) == 1
    assert to_delete3 == ["uuid-abc"]
    for uuid in to_delete3:  # the pipeline performs the deletions reconcile returned
        adapter.remove_by_id(uuid)
    reg3.record_created(vias3[0], "uuid-def")
    live_vias.append(_make_live_via("uuid-def", 50.0, 47.0, "+3V3", 0.3, 0.6))

    # --- Run 4: spoke removed from config entirely -- prune must delete the via ---
    adapter.reset_mock()  # resets call counts, keeps side_effect and return_value
    cfg4 = Config(
        layer='B.Cu', cells={},
        rules=[],
    )
    calc4 = ManualPositionCalculator(adapter, cfg4)
    _, vias4, _ = calc4.compute_raw_positions(cfg4.rules)
    assert vias4 == []
    reg4 = PlacementRegistry(adapter, reg_path)
    to_create4, to_delete4 = reg4.reconcile(vias4)
    assert to_create4 == []
    assert to_delete4 == ["uuid-def"]
    for uuid in to_delete4:
        adapter.remove_by_id(uuid)
    assert live_vias == [], "prune must have actually deleted the via from the 'board'"
