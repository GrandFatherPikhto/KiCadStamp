#!/usr/bin/env python3
"""
Regression test for a bug found on 2026-07-15: execute_vias() used to write
an APPROXIMATE owner_ref (always from the first element of the batch) for EACH
via created in the batch — regardless of which actual command it corresponded to.
Plus a check that PlacementRegistry.record_created() is actually called for each
created via with the correct UUID.

Updated for the new architecture (2026-07-23): the log is now written in
execute_tracks, so the test calls execute_tracks([]) after executing vias.
"""
import sys
import json
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2

from kicadstamp.config import Config
from kicadstamp.placement.executor import BatchExecutor
from kicadstamp.placement.commands import ViaCommand

MM = 1_000_000


def _make_via(x_mm, net_name, owner_ref, registry_key=None):
    return ViaCommand(
        position=Vector2.from_xy(int(x_mm * MM), 0),
        drill_mm=0.3, diameter_mm=0.6, net_name=net_name, owner_ref=owner_ref,
        registry_key=registry_key,
    )


def test_owner_ref_matches_actual_command_not_first_in_batch():
    """Batch of 2 vias with DIFFERENT owner_ref — each must get its OWN
    owner_ref in the log, not the owner_ref of the first batch element."""
    net = MagicMock()
    net.name = "GND"

    created_via_1 = MagicMock()
    created_via_1.uuid = "uuid-1"
    created_via_1.position = Vector2.from_xy(int(1 * MM), 0)
    created_via_1.diameter_mm = 0.6
    created_via_1.drill_mm = 0.3
    created_via_1.net_name = "GND"

    created_via_2 = MagicMock()
    created_via_2.uuid = "uuid-2"
    created_via_2.position = Vector2.from_xy(int(2 * MM), 0)
    created_via_2.diameter_mm = 0.6
    created_via_2.drill_mm = 0.3
    created_via_2.net_name = "GND"

    adapter = MagicMock()
    adapter.get_net_by_name.return_value = net
    adapter.create_items.return_value = [created_via_1, created_via_2]
    adapter.commit_with_retry.side_effect = lambda desc, work: (work(), True)[1]

    # Minimal config (needed only to pass to child executors)
    cfg = Config(
        layer='F.Cu',
        cells={},
        chains=[],
    )

    old_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        os.chdir(tmpdir)
        executor = BatchExecutor(adapter, cfg, batch_size=10)

        via_a = _make_via(1, "GND", owner_ref="C5")
        via_b = _make_via(2, "GND", owner_ref="C30")  # DIFFERENT owner_ref!

        # Execute vias and then call execute_tracks([]) to trigger log writing
        executor.execute_vias([via_a, via_b])
        executor.execute_tracks([])  # <-- log is written here

        log_files = list(Path("logs").glob("*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text())
        owners = [v["owner_ref"] for v in data["created_vias"]]
        assert owners == ["C5", "C30"], f"FAIL: owner_refs mixed up, got {owners}"
    finally:
        os.chdir(old_cwd)


def test_registry_record_created_called_with_correct_uuid_per_via():
    net = MagicMock()
    net.name = "GND"

    created_via_1 = MagicMock()
    created_via_1.uuid = "uuid-1"
    created_via_1.position = Vector2.from_xy(int(1 * MM), 0)
    created_via_1.diameter_mm = 0.6
    created_via_1.drill_mm = 0.3
    created_via_1.net_name = "GND"

    created_via_2 = MagicMock()
    created_via_2.uuid = "uuid-2"
    created_via_2.position = Vector2.from_xy(int(2 * MM), 0)
    created_via_2.diameter_mm = 0.6
    created_via_2.drill_mm = 0.3
    created_via_2.net_name = "GND"

    adapter = MagicMock()
    adapter.get_net_by_name.return_value = net
    adapter.create_items.return_value = [created_via_1, created_via_2]
    adapter.commit_with_retry.side_effect = lambda desc, work: (work(), True)[1]

    cfg = Config(
        layer='F.Cu',
        cells={},
        chains=[],
    )

    via_a = _make_via(1, "GND", owner_ref="C5", registry_key="pad:17|t|HEAVY|0")
    via_b = _make_via(2, "GND", owner_ref="C30", registry_key="pad:17|t|LIGHT|0")

    registry = MagicMock()

    old_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        os.chdir(tmpdir)
        executor = BatchExecutor(adapter, cfg, batch_size=10)
        executor.execute_vias([via_a, via_b], registry=registry)
        # Log is not needed for this test, but if we called execute_tracks it would be written.
        # We only check record_created calls.
        assert registry.record_created.call_count == 2
        registry.record_created.assert_any_call(via_a, "uuid-1")
        registry.record_created.assert_any_call(via_b, "uuid-2")
    finally:
        os.chdir(old_cwd)
