#!/usr/bin/env python3
"""Tests for the track positional pre-check and its shared bidirectional
predicate (plan_2026_08_16_position_based_copper_idempotency.md, Этап 1):

- track_matches(): the ONE predicate used by both the UUID-path reconcile
  (TrackRegistry._live_matches) and the positional pre-check
  (filter_existing_tracks). A segment is UNORIENTED — both orientations count.
  Also compares net (full name string), width, layer.
- filter_existing_tracks(): positional pre-check, applied STRICTLY AFTER
  reconcile() to its to_create list (a pre-reconcile skip would drop the key
  from seen_keys and make prune delete the REGISTERED tool track). SKIP-ONLY —
  never removes/adopts foreign copper.
"""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.geometry import Vector2

# Import order matters here: kicadstamp.registry imports .placement.commands
# at module level, which (via the placement package __init__) pulls in
# manual_position_calculator.py, which imports back from kicadstamp.registry —
# importing something under kicadstamp.placement FIRST (as every other test
# file touching the registry already does) avoids that circular-import trap.
from kicadstamp.placement.commands import TrackCommand
from kicadstamp.registry import (TrackRegistry, TrackRegistryEntry,
                                 filter_existing_tracks, track_matches)

MM = 1_000_000


def _make_live_track(uuid_str, start_mm, end_mm, net_name="+3V3",
                     width_mm=0.25, layer=BoardLayer.BL_F_Cu):
    track = MagicMock()
    track.uuid = uuid_str
    track.start = Vector2.from_xy(int(start_mm[0] * MM), int(start_mm[1] * MM))
    track.end = Vector2.from_xy(int(end_mm[0] * MM), int(end_mm[1] * MM))
    track.net_name = net_name
    track.width_mm = width_mm
    track.layer = layer
    return track


def _track_cmd(start_mm, end_mm, net_name="+3V3", width_mm=0.25,
               layer=BoardLayer.BL_F_Cu, owner="U1", registry_key=None):
    return TrackCommand(
        start=Vector2.from_xy(int(start_mm[0] * MM), int(start_mm[1] * MM)),
        end=Vector2.from_xy(int(end_mm[0] * MM), int(end_mm[1] * MM)),
        width_mm=width_mm, net_name=net_name, layer=layer, owner_ref=owner,
        registry_key=registry_key,
    )


# ── track_matches: geometry (both orientations) + net + width + layer ─────────

def test_track_matches_direct_match():
    live = _make_live_track("u", (10.0, 20.0), (30.0, 40.0))
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0))
    assert track_matches(live, cmd)


def test_track_matches_swapped_ends():
    """Segment is unoriented — a live track with start↔end swapped is the SAME
    geometry (the latent reversal fix, Task 1.2)."""
    live = _make_live_track("u", (30.0, 40.0), (10.0, 20.0))
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0))
    assert track_matches(live, cmd)


def test_track_matches_net_mismatch():
    live = _make_live_track("u", (10.0, 20.0), (30.0, 40.0), net_name="GND")
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0), net_name="+3V3")
    assert not track_matches(live, cmd)


def test_track_matches_width_mismatch():
    live = _make_live_track("u", (10.0, 20.0), (30.0, 40.0), width_mm=0.5)
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0), width_mm=0.25)
    assert not track_matches(live, cmd)


def test_track_matches_layer_mismatch():
    live = _make_live_track("u", (10.0, 20.0), (30.0, 40.0), layer=BoardLayer.BL_B_Cu)
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0), layer=BoardLayer.BL_F_Cu)
    assert not track_matches(live, cmd)


def test_track_matches_position_mismatch():
    live = _make_live_track("u", (10.0, 20.0), (30.0, 41.0))  # end y off by 1 mm
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0))
    assert not track_matches(live, cmd)


def test_track_matches_live_net_none():
    """A live track with no net can never match a command that names one."""
    live = MagicMock()
    live.start = Vector2.from_xy(int(10 * MM), int(20 * MM))
    live.end = Vector2.from_xy(int(30 * MM), int(40 * MM))
    live.net_name = None
    live.width_mm = 0.25
    live.layer = BoardLayer.BL_F_Cu
    cmd = _track_cmd((10.0, 20.0), (30.0, 40.0), net_name="+3V3")
    assert not track_matches(live, cmd)


# ── reconcile via the new predicate: reversal no longer recreated ─────────────

def test_reversed_live_track_not_recreated():
    """Latent reversal bug fix: a REGISTERED live track flipped (start↔end)
    between runs, same UUID — the shared bidirectional predicate must match, so
    reconcile() does NOT decide "position changed -> delete+recreate"."""
    key = "pad:1|tpl|__spoke__|0"
    live_track = _make_live_track("uuid-t", (30.0, 40.0), (10.0, 20.0))  # reversed
    adapter = MagicMock()
    adapter.get_tracks.return_value = [live_track]
    adapter.remove_by_id.return_value = True

    reg_path = os.path.join(tempfile.mkdtemp(), "tracks.json")
    registry = TrackRegistry(adapter, reg_path)
    registry.entries = {key: TrackRegistryEntry(
        uuid="uuid-t", start_x_mm=10.0, start_y_mm=20.0,
        end_x_mm=30.0, end_y_mm=40.0, width_mm=0.25, net="+3V3", layer="F.Cu")}

    planned = [_track_cmd((10.0, 20.0), (30.0, 40.0), registry_key=key)]
    to_create, to_delete = registry.reconcile(planned, known_anchor_ids={"pad:1"})

    assert to_create == []
    assert to_delete == []
    assert key in registry.entries


# ── filter_existing_tracks: positional pre-check (skip-only) ─────────────────

def test_precheck_skips_unregistered_copper():
    """A command whose geometry+net+width+layer already exists among live tracks
    (manually drawn / created by another mechanism — NOT in the registry) is
    skipped: unregistered-copper idempotency without the registry."""
    start, end = (10.0, 20.0), (30.0, 40.0)
    live = _make_live_track("uuid-manual", start, end)
    cmd = _track_cmd(start, end, owner="U1")
    assert filter_existing_tracks([cmd], [live]) == []


def test_precheck_swapped_ends_match_too():
    """The pre-check uses the same bidirectional predicate — a live track with
    swapped ends still counts as already existing."""
    start, end = (10.0, 20.0), (30.0, 40.0)
    live = _make_live_track("uuid-manual", end, start)
    cmd = _track_cmd(start, end)
    assert filter_existing_tracks([cmd], [live]) == []


def test_precheck_keeps_nonexistent_track():
    """A command with no matching live track is kept."""
    start, end = (10.0, 20.0), (30.0, 40.0)
    other = _make_live_track("uuid-other", (0.0, 0.0), (1.0, 1.0), net_name="GND")
    cmd = _track_cmd(start, end)
    assert filter_existing_tracks([cmd], [other]) == [cmd]


def test_precheck_net_mismatch_kept():
    """Same geometry but a different net is NOT "already existing" — kept."""
    start, end = (10.0, 20.0), (30.0, 40.0)
    live = _make_live_track("uuid-manual", start, end, net_name="GND")
    cmd = _track_cmd(start, end, net_name="+3V3")
    assert filter_existing_tracks([cmd], [live]) == [cmd]


def test_precheck_empty_input_is_noop():
    assert filter_existing_tracks([], []) == []


# ── ordering: registered path is never touched by the pre-check ───────────────

def test_registered_path_not_touched_by_precheck():
    """A REGISTERED track correctly placed at its position, with manual copper
    now also on top of it — reconcile() already skips the registered one (it is
    not in to_create), so the pre-check has nothing to drop and nothing gets
    pruned/recreated. The tool never deletes or adopts foreign copper."""
    key = "pad:1|tpl|__spoke__|0"
    start, end = (10.0, 20.0), (30.0, 40.0)
    live_registered = _make_live_track("uuid-reg", start, end)
    live_manual = _make_live_track("uuid-manual", start, end)
    adapter = MagicMock()
    adapter.get_tracks.return_value = [live_registered, live_manual]
    adapter.remove_by_id.return_value = True

    reg_path = os.path.join(tempfile.mkdtemp(), "tracks.json")
    registry = TrackRegistry(adapter, reg_path)
    registry.entries = {key: TrackRegistryEntry(
        uuid="uuid-reg", start_x_mm=10.0, start_y_mm=20.0,
        end_x_mm=30.0, end_y_mm=40.0, width_mm=0.25, net="+3V3", layer="F.Cu")}

    cmd = _track_cmd(start, end, registry_key=key)
    to_create, to_delete = registry.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create == []                       # already correctly placed
    assert to_delete == []                       # no delete, no prune
    to_create = filter_existing_tracks(to_create, [live_registered, live_manual])
    assert to_create == []                       # pre-check: nothing to drop
    assert key in registry.entries


def test_double_run_no_duplicates():
    """Idempotency across two runs: run 1 creates a track, run 2's reconcile
    (registry path) AND pre-check (positional path) both agree it already
    exists — no duplicate is ever planned."""
    key = "pad:1|tpl|__spoke__|0"
    start, end = (10.0, 20.0), (30.0, 40.0)
    reg_path = os.path.join(tempfile.mkdtemp(), "tracks.json")

    # Run 1: board empty -> reconcile says create, pre-check agrees (nothing).
    adapter1 = MagicMock()
    adapter1.get_tracks.return_value = []
    registry1 = TrackRegistry(adapter1, reg_path)
    cmd = _track_cmd(start, end, registry_key=key)
    to_create1, _ = registry1.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create1 == [cmd]
    registry1.record_created(cmd, "uuid-t")      # executor would do this

    # Run 2: the track is now live (registered UUID path).
    live_created = _make_live_track("uuid-t", start, end)
    adapter2 = MagicMock()
    adapter2.get_tracks.return_value = [live_created]
    registry2 = TrackRegistry(adapter2, reg_path)
    to_create2, _ = registry2.reconcile([cmd], known_anchor_ids={"pad:1"})
    assert to_create2 == []                      # registered path: skip
    assert filter_existing_tracks(to_create2, [live_created]) == []

    # Run 2 positional path (unregistered copper, e.g. a manual track).
    manual = _make_live_track("uuid-manual", start, end)
    assert filter_existing_tracks([cmd], [manual]) == []
