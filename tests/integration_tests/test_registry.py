"""
Integration test of the placement registry (registry.py) against a real KiCad.

Checks:
- First run: a via is created, the registry remembers its UUID.
- Second run (config unchanged): the via is skipped.
- Run with a changed position: the old via is removed, a new one is created.
- Run with a spoke removed: the via is removed (prune).
"""

import pytest
import json
from kicadstamp.domain.geometry import Vector2
from kicadstamp.utils.units import MM
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import PlacementRegistry


@pytest.mark.integration
def test_registry_full_cycle(adapter, tmp_path):
    """Full registry cycle: create, skip, update, prune."""
    reg_path = tmp_path / "test.registry.json"
    registry = PlacementRegistry(adapter, str(reg_path))

    # 1. Build a via command
    net = adapter.get_net_by_name("GND")
    pos = Vector2.from_xy(int(50 * MM), int(50 * MM))
    via_cmd = ViaCommand(
        position=pos,
        drill_mm=0.3, diameter_mm=0.6,
        net_name="GND",
        owner_ref="IC1",
        registry_key="test|via|0"
    )

    # First reconcile call — must create it
    to_create, to_delete = registry.reconcile([via_cmd])
    assert len(to_create) == 1
    assert to_create[0] is via_cmd
    assert to_delete == []

    # Create the via on the board
    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([adapter.create_via(pos, net, 0.3, 0.6)])
        adapter.push_commit(commit, "test: create via")
        created_uuid = created[0].uuid
    except Exception:
        adapter.drop_commit(commit)
        raise

    # Record it in the registry
    registry.record_created(via_cmd, created_uuid)

    # Check that the registry holds the entry
    assert via_cmd.registry_key in registry.entries
    entry = registry.entries[via_cmd.registry_key]
    assert entry.uuid == created_uuid
    assert abs(entry.x_mm - 50.0) < 0.01
    assert entry.y_mm == 50.0
    assert entry.net == "GND"

    # 2. Second run — the via already exists, must be skipped
    to_create_2, _ = registry.reconcile([via_cmd])
    assert len(to_create_2) == 0

    # 3. Change the via position in the config (build a new command with a different position)
    new_pos = Vector2.from_xy(int(51 * MM), int(51 * MM))
    via_cmd_updated = ViaCommand(
        position=new_pos,
        drill_mm=0.3, diameter_mm=0.6,
        net_name="GND",
        owner_ref="IC1",
        registry_key="test|via|0"  # the same key
    )

    # Reconcile must detect the change, delete the old via and return the new one for creation
    to_create_3, to_delete_3 = registry.reconcile([via_cmd_updated])
    assert len(to_create_3) == 1
    assert to_create_3[0] is via_cmd_updated
    assert to_delete_3 == [created_uuid]

    # Check that the old via is marked for deletion and its registry entry is removed
    assert via_cmd.registry_key not in registry.entries

    # Create the new via
    commit2 = adapter.begin_commit()
    try:
        created2 = adapter.create_items([adapter.create_via(new_pos, net, 0.3, 0.6)])
        adapter.push_commit(commit2, "test: create updated via")
        new_uuid = created2[0].uuid
    except Exception:
        adapter.drop_commit(commit2)
        raise

    registry.record_created(via_cmd_updated, new_uuid)
    assert via_cmd.registry_key in registry.entries
    assert registry.entries[via_cmd.registry_key].uuid == new_uuid

    # 4. Prune: drop the key from the config (do not pass via_cmd_updated)
    to_create_4, to_delete_4 = registry.reconcile([])
    assert len(to_create_4) == 0
    assert to_delete_4 == [new_uuid]
    # The registry entry must be removed, and the via marked for deletion from the board
    assert via_cmd.registry_key not in registry.entries

    # Check that the registry file was updated (saved)
    assert reg_path.exists()
    with open(reg_path, "r") as f:
        data = json.load(f)
        assert data == {}  # empty


@pytest.mark.integration
def test_registry_persists_across_runs(adapter, tmp_path):
    """Check that the registry persists between runs."""
    reg_path = tmp_path / "test.registry.json"
    net = adapter.get_net_by_name("GND")
    pos = Vector2.from_xy(int(50 * MM), int(50 * MM))
    via_cmd = ViaCommand(
        position=pos,
        drill_mm=0.3, diameter_mm=0.6,
        net_name="GND",
        owner_ref="IC1",
        registry_key="persist|key|0"
    )

    # First run
    registry1 = PlacementRegistry(adapter, str(reg_path))
    to_create1, _ = registry1.reconcile([via_cmd])
    assert len(to_create1) == 1

    # Create the via
    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([adapter.create_via(pos, net, 0.3, 0.6)])
        adapter.push_commit(commit, "test: persist via")
        uuid = created[0].uuid
    except Exception:
        adapter.drop_commit(commit)
        raise
    registry1.record_created(via_cmd, uuid)

    # Second run (a new registry instance with the same file)
    registry2 = PlacementRegistry(adapter, str(reg_path))
    to_create2, _ = registry2.reconcile([via_cmd])
    assert len(to_create2) == 0  # the via must be skipped

    # Delete the via manually for cleanup
    adapter.remove_by_id(uuid)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: cleanup")
    except Exception:
        adapter.drop_commit(commit2)
        raise
