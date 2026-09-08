#!/usr/bin/env python3
"""Tests for the via positional pre-check and its shared predicate
(plan_2026_09_08_via_positional_precheck.md):

- via_matches(): the ONE predicate used by both the UUID-path reconcile
  (PlacementRegistry._live_matches) and the positional pre-check
  (filter_existing_vias). Compares position (within POSITION_TOLERANCE_MM) +
  net + drill + diameter — mirrors track_matches, extended to vias 2026-09-08.
- filter_existing_vias(): positional pre-check, applied STRICTLY AFTER
  reconcile() to its to_create list (a pre-reconcile skip would drop the key
  from seen_keys and make prune delete the REGISTERED via). SKIP-ONLY —
  never removes/adopts foreign copper. Mirrors filter_existing_tracks
  (2026-08-31); vias never got that protection until this plan.
"""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import Vector2

# Import order matters here: kicadstamp.registry imports .placement.commands
# at module level, which (via the placement package __init__) pulls in
# manual_position_calculator.py, which imports back from kicadstamp.registry —
# importing something under kicadstamp.placement FIRST (as every other test
# file touching the registry already does) avoids that circular-import trap.
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import (PlacementRegistry, RegistryEntry,
                                 filter_existing_vias, via_matches)

MM = 1_000_000


def _make_live_via(uuid_str, pos_mm, net_name="+3V3", drill_mm=0.3,
                   diameter_mm=0.6):
    via = MagicMock()
    via.uuid = uuid_str
    via.position = Vector2.from_xy(int(pos_mm[0] * MM), int(pos_mm[1] * MM))
    via.net_name = net_name
    via.drill_mm = drill_mm
    via.diameter_mm = diameter_mm
    return via


def _via_cmd(pos_mm, net_name="+3V3", drill_mm=0.3, diameter_mm=0.6,
             owner="U1", registry_key=None):
    return ViaCommand(
        position=Vector2.from_xy(int(pos_mm[0] * MM), int(pos_mm[1] * MM)),
        drill_mm=drill_mm, diameter_mm=diameter_mm, net_name=net_name,
        owner_ref=owner, registry_key=registry_key,
    )


# ── via_matches: position + net + drill + diameter ────────────────────────────

def test_via_matches_direct_match():
    live = _make_live_via("u", (10.0, 20.0))
    cmd = _via_cmd((10.0, 20.0))
    assert via_matches(live, cmd)


def test_via_matches_position_mismatch():
    live = _make_live_via("u", (10.0, 21.0))  # y off by 1 mm
    cmd = _via_cmd((10.0, 20.0))
    assert not via_matches(live, cmd)


def test_via_matches_net_mismatch():
    live = _make_live_via("u", (10.0, 20.0), net_name="GND")
    cmd = _via_cmd((10.0, 20.0), net_name="+3V3")
    assert not via_matches(live, cmd)


def test_via_matches_drill_mismatch():
    live = _make_live_via("u", (10.0, 20.0), drill_mm=0.2)
    cmd = _via_cmd((10.0, 20.0), drill_mm=0.3)
    assert not via_matches(live, cmd)


def test_via_matches_diameter_mismatch():
    live = _make_live_via("u", (10.0, 20.0), diameter_mm=0.8)
    cmd = _via_cmd((10.0, 20.0), diameter_mm=0.6)
    assert not via_matches(live, cmd)


def test_via_matches_live_net_none():
    """A live via with no net can never match a command that names one."""
    live = MagicMock()
    live.position = Vector2.from_xy(int(10 * MM), int(20 * MM))
    live.net_name = None
    live.drill_mm = 0.3
    live.diameter_mm = 0.6
    cmd = _via_cmd((10.0, 20.0), net_name="+3V3")
    assert not via_matches(live, cmd)


# ── reconcile via the new predicate: registered via is not recreated ──────────

def test_registered_live_via_matches_not_recreated():
    """A REGISTERED live via matching the shared predicate must be skipped by
    reconcile() — the via_matches() extraction is behavior-preserving for the
    UUID path, so an already-correctly-placed via is never deleted/recreated."""
    key = "pad:1|tpl|__spoke__|0"
    pos = (10.0, 20.0)
    live_via = _make_live_via("uuid-v", pos)
    adapter = MagicMock()
    adapter.get_vias.return_value = [live_via]
    adapter.remove_by_id.return_value = True

    reg_path = os.path.join(tempfile.mkdtemp(), "vias.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {key: RegistryEntry(
        uuid="uuid-v", x_mm=10.0, y_mm=20.0, net="+3V3",
        drill_mm=0.3, diameter_mm=0.6)}

    planned = [_via_cmd(pos, registry_key=key)]
    to_create, to_delete = registry.reconcile(planned, known_anchor_ids={"pad:1"})

    assert to_create == []
    assert to_delete == []
    assert key in registry.entries


# ── filter_existing_vias: positional pre-check (skip-only) ───────────────────

def test_precheck_skips_unregistered_copper():
    """A command whose position+net+drill+diameter already exists among live
    vias (created by another mechanism / a previous run — NOT in the registry)
    is skipped: unregistered-copper idempotency without the registry."""
    pos = (10.0, 20.0)
    live = _make_live_via("uuid-manual", pos)
    cmd = _via_cmd(pos, owner="U1")
    assert filter_existing_vias([cmd], [live]) == []


def test_precheck_keeps_nonexistent_via():
    """A command with no matching live via is kept."""
    pos = (10.0, 20.0)
    other = _make_live_via("uuid-other", (0.0, 0.0), net_name="GND")
    cmd = _via_cmd(pos)
    assert filter_existing_vias([cmd], [other]) == [cmd]


def test_precheck_net_mismatch_kept():
    """Same position but a different net is NOT "already existing" — kept."""
    pos = (10.0, 20.0)
    live = _make_live_via("uuid-manual", pos, net_name="GND")
    cmd = _via_cmd(pos, net_name="+3V3")
    assert filter_existing_vias([cmd], [live]) == [cmd]


def test_precheck_empty_input_is_noop():
    assert filter_existing_vias([], []) == []


# ── ordering: registered path is never touched by the pre-check ───────────────

def test_registered_path_not_touched_by_precheck():
    """A REGISTERED via correctly placed at its position, with manual copper
    now also on top of it — reconcile() already skips the registered one (it is
    not in to_create), so the pre-check has nothing to drop and nothing gets
    pruned/recreated. The tool never deletes or adopts foreign copper."""
    key = "pad:1|tpl|__spoke__|0"
    pos = (10.0, 20.0)
    live_registered = _make_live_via("uuid-reg", pos)
    live_manual = _make_live_via("uuid-manual", pos)
    adapter = MagicMock()
    adapter.get_vias.return_value = [live_registered, live_manual]
    adapter.remove_by_id.return_value = True

    reg_path = os.path.join(tempfile.mkdtemp(), "vias.json")
    registry = PlacementRegistry(adapter, reg_path)
    registry.entries = {key: RegistryEntry(
        uuid="uuid-reg", x_mm=10.0, y_mm=20.0, net="+3V3",
        drill_mm=0.3, diameter_mm=0.6)}

    cmd = _via_cmd(pos, registry_key=key)
    to_create, to_delete = registry.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create == []                       # already correctly placed
    assert to_delete == []                       # no delete, no prune
    to_create = filter_existing_vias(to_create, [live_registered, live_manual])
    assert to_create == []                       # pre-check: nothing to drop
    assert key in registry.entries


def test_double_run_no_duplicates():
    """Idempotency across two runs: run 1 creates a via, run 2's reconcile
    (registry path) AND pre-check (positional path) both agree it already
    exists — no duplicate is ever planned."""
    key = "pad:1|tpl|__spoke__|0"
    pos = (10.0, 20.0)
    reg_path = os.path.join(tempfile.mkdtemp(), "vias.json")

    # Run 1: board empty -> reconcile says create, pre-check agrees (nothing).
    adapter1 = MagicMock()
    adapter1.get_vias.return_value = []
    registry1 = PlacementRegistry(adapter1, reg_path)
    cmd = _via_cmd(pos, registry_key=key)
    to_create1, _ = registry1.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create1 == [cmd]
    registry1.record_created(cmd, "uuid-v")      # executor would do this

    # Run 2: the via is now live (registered UUID path).
    live_created = _make_live_via("uuid-v", pos)
    adapter2 = MagicMock()
    adapter2.get_vias.return_value = [live_created]
    registry2 = PlacementRegistry(adapter2, reg_path)
    to_create2, _ = registry2.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create2 == []                      # registered path: skip
    assert filter_existing_vias(to_create2, [live_created]) == []

    # Run 2 positional path (unregistered copper, e.g. a manual via).
    manual = _make_live_via("uuid-manual", pos)
    assert filter_existing_vias([cmd], [manual]) == []
