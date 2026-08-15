#!/usr/bin/env python3
"""Unit tests for the shared sheet-narrowing helper in role_narrowing.py
(2026-08-15) — narrow_candidates_by_sheet is the reusable "narrow by sheet,
but only if it actually reduces the set" step shared by:
  - _narrow_by_sheet_cluster_selection (ClonePlacement internal roles, and the
    external anchor in resolve_footprint_by_role)
  - CoordinatePlacement's own sheet: identity (resolve_footprint_by_cluster_role
    and build_coordinate_moves in coordinate_position_calculator.py)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from kipy.board_types import FootprintInstance

from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet


def _make_fp(ref, sheet_uuid):
    """resolve_sheet_path_names reads fp.sheet_path.path[:-1] (last entry is
    the component's own uuid, excluded) — see kicadstamp/sheet_names.py."""
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.sheet_path.path = [MagicMock(value=sheet_uuid), MagicMock(value=f"{ref}-own-uuid")]
    return fp


_SHEET_NAMES = {"sheet-0": "Channel_0", "sheet-1": "Channel_1"}


def test_narrows_to_candidates_on_the_requested_sheet():
    fps = [_make_fp("IC2", "sheet-0"), _make_fp("IC3", "sheet-1")]
    narrowed = narrow_candidates_by_sheet(fps, "Channel_0", _SHEET_NAMES)
    assert [fp.reference_field.text.value for fp in narrowed] == ["IC2"]


def test_noop_when_sheet_empty_or_none():
    fps = [_make_fp("IC2", "sheet-0"), _make_fp("IC3", "sheet-1")]
    assert narrow_candidates_by_sheet(fps, "", _SHEET_NAMES) is fps
    assert narrow_candidates_by_sheet(fps, None, _SHEET_NAMES) is fps


def test_noop_when_zero_or_one_candidate():
    """0-1 candidates are already decided — narrowing is a no-op (same
    convention as every other step in _narrow_by_sheet_cluster_selection)."""
    one = [_make_fp("IC2", "sheet-0")]
    assert narrow_candidates_by_sheet(one, "Channel_0", _SHEET_NAMES) is one
    assert narrow_candidates_by_sheet([], "Channel_0", _SHEET_NAMES) == []


def test_noop_when_sheet_matches_nothing():
    """'only narrow if it reduces AND finds something' — a sheet that matches
    no candidate must leave the original (possibly ambiguous) list intact, so
    the caller's fatal still names EVERY candidate; nothing is silently
    dropped."""
    fps = [_make_fp("IC2", "sheet-0"), _make_fp("IC3", "sheet-1")]
    result = narrow_candidates_by_sheet(fps, "NoSuchSheet", _SHEET_NAMES)
    assert result == fps
    assert len(result) == 2


def test_noop_when_sheet_names_empty():
    """schematic_dir/schematic_files not set -> sheet_names is empty -> no
    candidate's path resolves, so the helper must NOT wipe the list."""
    fps = [_make_fp("IC2", "sheet-0"), _make_fp("IC3", "sheet-1")]
    assert narrow_candidates_by_sheet(fps, "Channel_0", {}) is fps
