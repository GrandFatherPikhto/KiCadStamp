#!/usr/bin/env python3
"""
Regression tests for the 'pad:' anchor_id protection gap (found 2026-07-29):
registry.reconcile()'s known_anchor_ids protection only recognised the
'anchor:'/'role:'/'name:'/'thermal:' prefixes (ClonePlacement/thermal_via_array)
— never 'pad:' (Rule/ManualSpoke's own registry_key prefix, see
manual_position_calculator.compute_raw_positions: anchor_id = f"pad:{spoke.pad}").
A rule excluded from a run (retired: true, --only, --cluster) had its via/
track registry entries pruned unconditionally — --only/--cluster protection
never actually worked for rule-based geometry, only for clone_placements.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2

# See test_registry_pruning_granularity.py for why this import order matters
# (circular-import trap via kicadstamp.placement package __init__).
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import PlacementRegistry, RegistryEntry
from kicadstamp.config import Rule, ManualSpoke
from kicadstamp.placement.services.manual_position_calculator import rule_anchor_ids

MM = 1_000_000


def _make_live_via(uuid_str, x_mm, y_mm, net_name="GND", drill_mm=0.3, diameter_mm=0.6):
    via = MagicMock()
    via.uuid = uuid_str
    via.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    via.net_name = net_name
    via.drill_mm = drill_mm
    via.diameter_mm = diameter_mm
    return via


def test_pad_prefixed_anchor_not_processed_this_run_stays_protected():
    """A rule's spoke (pad:17) was NOT processed at all this run (excluded by
    --only/--cluster) — with 'pad:' now in the recognised prefix set AND its
    id present in known_anchor_ids, its via must NOT be pruned. Before the
    fix, 'pad:' was never in the prefix whitelist, so this was pruned
    unconditionally regardless of known_anchor_ids."""
    anchor_id = "pad:17"
    live_via = _make_live_via("uuid-pad17", 20.0, 20.0)

    adapter = MagicMock()
    adapter.get_vias.return_value = [live_via]
    adapter.remove_by_id.return_value = True

    import tempfile, os
    reg_path = os.path.join(tempfile.mkdtemp(), "test.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {
        f"{anchor_id}|fpga_cap_pair_spoke|__spoke__|0": RegistryEntry(
            uuid="uuid-pad17", x_mm=20.0, y_mm=20.0, net="GND",
            drill_mm=0.3, diameter_mm=0.6),
    }

    # This run planned NOTHING for pad 17 at all (excluded by --only/--cluster).
    to_create, to_delete = registry.reconcile([], known_anchor_ids={anchor_id})

    assert to_create == []
    assert to_delete == []
    assert f"{anchor_id}|fpga_cap_pair_spoke|__spoke__|0" in registry.entries


def test_pad_prefixed_anchor_pruned_without_protection():
    """Same as above but known_anchor_ids does NOT include pad:17 (e.g. the
    rule itself was retired: retired: true) — must still be pruned, so the
    fix doesn't accidentally protect everything unconditionally."""
    anchor_id = "pad:17"
    live_via = _make_live_via("uuid-pad17", 20.0, 20.0)

    adapter = MagicMock()
    adapter.get_vias.return_value = [live_via]
    adapter.remove_by_id.return_value = True

    import tempfile, os
    reg_path = os.path.join(tempfile.mkdtemp(), "test.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {
        f"{anchor_id}|fpga_cap_pair_spoke|__spoke__|0": RegistryEntry(
            uuid="uuid-pad17", x_mm=20.0, y_mm=20.0, net="GND",
            drill_mm=0.3, diameter_mm=0.6),
    }

    to_create, to_delete = registry.reconcile([], known_anchor_ids=set())

    assert to_create == []
    assert to_delete == ["uuid-pad17"]
    assert f"{anchor_id}|fpga_cap_pair_spoke|__spoke__|0" not in registry.entries


def test_rule_anchor_ids_one_per_non_retired_spoke():
    rule = Rule(net="+3V3_VCCIO", anchor_role="FPGA", spokes=[
        ManualSpoke(pad="17", cell="fpga_cap_pair_spoke"),
        ManualSpoke(pad="26", cell="fpga_cap_pair_spoke"),
        ManualSpoke(pad="40", cell="fpga_cap_pair_spoke", retired=True),
    ])
    assert rule_anchor_ids(rule) == {"pad:17", "pad:26"}


def test_rule_anchor_ids_empty_for_all_retired_spokes():
    rule = Rule(net="+3V3_VCCIO", anchor_role="FPGA", spokes=[])
    assert rule_anchor_ids(rule) == set()
