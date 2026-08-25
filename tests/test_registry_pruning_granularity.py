#!/usr/bin/env python3
"""
Regression tests for the registry prune-granularity bug (found 2026-07-28):
known_anchor_ids protected an entry from pruning whenever its anchor_id was
still "known" (item still not retired in config) — even if the item WAS processed
this run and the specific key just wasn't part of its CURRENT plan any more
(e.g. a cell's via/track list shrank or got reordered after editing).
Real case: 3 stale tracks from an earlier ldo_adj_subsystem revision, at
indices the current cell no longer uses, stuck on the board forever.

Fix: known_anchor_ids only protects an anchor_id that was NOT seen at all this
run (genuinely excluded by --only/--cluster) — not a stale key belonging to an
anchor_id that WAS seen (genuinely orphaned, must be pruned).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2

# Import order matters here: kicadstamp.registry imports .placement.commands
# at module level, which (via the placement package __init__) pulls in
# manual_position_calculator.py, which imports back from kicadstamp.registry —
# importing something under kicadstamp.placement FIRST (as every other test
# file touching the registry already does) avoids that circular-import trap.
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import PlacementRegistry, RegistryEntry

MM = 1_000_000


def _make_live_via(uuid_str, x_mm, y_mm, net_name="GND", drill_mm=0.3, diameter_mm=0.6):
    via = MagicMock()
    via.id.value = uuid_str
    via.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    via.net.name = net_name
    via.drill_diameter = int(drill_mm * MM)
    via.diameter = int(diameter_mm * MM)
    return via


def test_orphaned_key_within_a_processed_anchor_is_pruned():
    """anchor A is processed this run (produces key ...|0), but an OLD key
    ...|5 under the SAME anchor_id is no longer part of its plan (cell
    shrank) — must be pruned even though anchor A's anchor_id is in
    known_anchor_ids (still not retired)."""
    anchor_id = "role:C_OUT_BYPASS::In_Pi_Filter_Pos:1:9.0000:-3.0000"
    live_current = _make_live_via("uuid-current", 10.0, 10.0)
    live_orphan = _make_live_via("uuid-orphan", 99.0, 99.0)

    adapter = MagicMock()
    adapter.get_vias.return_value = [live_current, live_orphan]
    adapter.remove_by_id.return_value = True

    import tempfile, os
    reg_path = os.path.join(tempfile.mkdtemp(), "test.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {
        f"{anchor_id}|tpl|__spoke__|0": RegistryEntry(
            uuid="uuid-current", x_mm=10.0, y_mm=10.0, net="GND",
            drill_mm=0.3, diameter_mm=0.6),
        f"{anchor_id}|tpl|__spoke__|5": RegistryEntry(
            uuid="uuid-orphan", x_mm=99.0, y_mm=99.0, net="GND",
            drill_mm=0.3, diameter_mm=0.6),
    }

    planned = [ViaCommand(
        position=Vector2.from_xy(10 * MM, 10 * MM), drill_mm=0.3, diameter_mm=0.6,
        net_name="GND", owner_ref="C1", registry_key=f"{anchor_id}|tpl|__spoke__|0",
    )]
    known_anchor_ids = {anchor_id}  # this clone is still not retired

    to_create, to_delete = registry.reconcile(planned, known_anchor_ids=known_anchor_ids)

    assert to_create == []  # index 0 already correctly placed
    assert to_delete == ["uuid-orphan"]
    assert f"{anchor_id}|tpl|__spoke__|5" not in registry.entries


def test_unprocessed_anchor_stays_protected():
    """anchor B was NOT processed at all this run (--only excluded it) —
    known_anchor_ids must still protect it, unchanged from before."""
    anchor_id = "role:CONN_PM5V::1:7.0000:-6.0000"
    live_via = _make_live_via("uuid-b", 20.0, 20.0)

    adapter = MagicMock()
    adapter.get_vias.return_value = [live_via]
    adapter.remove_by_id.return_value = True

    import tempfile, os
    reg_path = os.path.join(tempfile.mkdtemp(), "test.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {
        f"{anchor_id}|tpl|__spoke__|0": RegistryEntry(
            uuid="uuid-b", x_mm=20.0, y_mm=20.0, net="GND",
            drill_mm=0.3, diameter_mm=0.6),
    }

    # This run planned NOTHING for anchor B at all (excluded by --only).
    to_create, to_delete = registry.reconcile([], known_anchor_ids={anchor_id})

    assert to_create == []
    assert to_delete == []
    assert f"{anchor_id}|tpl|__spoke__|0" in registry.entries
