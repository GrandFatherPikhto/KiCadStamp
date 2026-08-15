# tests/test_clone_placement_placer_name.py
"""Tests for the ClonePlacement save/--only identity split (2026-08-15, plan
clone_placement_placer_name_split): `name` keeps its Cluster-tag meaning while
the new OPTIONAL `placer_name` field carries the config-bookkeeping identity.

Covered here (backend only — the GUI-side behaviour lives in
tests/gui/test_placer_dock.py):
- clone_placement_effective_name(): placer_name if set, else name.
- load_clone_placement(): placer_name is read (and is a known key), defaults
  to None.
- upsert_clone_placement(): identity for replace-in-place is placer_name when
  set, else name (regression: the old always-by-name behaviour).
- check_no_duplicate_clone_anchors(): the effective identity must be unique
  even when the Cluster tags differ; the old name-duplicate check still fires.
- apply_only_filter(): --only resolves clones by placer_name when set, else by
  name.
"""
import logging
from pathlib import Path

import pytest
import yaml

from kicadstamp.config import (
    Config, ClonePlacement, clone_placement_effective_name, load_clone_placement,
)
from kicadstamp.config_writer import upsert_clone_placement
from kicadstamp.validation import check_no_duplicate_clone_anchors
from kicadstamp.apply_pipeline import apply_only_filter
from kicadstamp.exceptions import PlacerError, ValidationError

logger = logging.getLogger("test_clone_placement_placer_name")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _clone(name, **kwargs):
    defaults = {"xy": (0.0, 0.0), "cell": "t"}
    defaults.update(kwargs)
    return ClonePlacement(name=name, **defaults)


# ── clone_placement_effective_name ───────────────────────────────────────

def test_effective_name_falls_back_to_name_when_placer_name_unset():
    assert clone_placement_effective_name(_clone("PIF_AVDD")) == "PIF_AVDD"


def test_effective_name_uses_placer_name_when_set():
    clone = _clone("PIF_AVDD", placer_name="CH0_PIF_AVDD")
    assert clone_placement_effective_name(clone) == "CH0_PIF_AVDD"


# ── load_clone_placement ─────────────────────────────────────────────────

def test_load_clone_placement_reads_placer_name():
    cp = load_clone_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                               "cell": "t", "xy": [0.0, 0.0]})
    assert cp.name == "PIF_AVDD"
    assert cp.placer_name == "CH0_PIF_AVDD"


def test_load_clone_placement_placer_name_defaults_to_none():
    cp = load_clone_placement({"name": "PIF_AVDD", "cell": "t", "xy": [0.0, 0.0]})
    assert cp.placer_name is None


def test_load_clone_placement_round_trips_placer_name():
    cp = load_clone_placement({"name": "PIF_AVDD", "placer_name": "CH0_PIF_AVDD",
                               "sheet": "Channel_0", "cell": "t", "xy": [1.0, 2.0]})
    assert cp.placer_name == "CH0_PIF_AVDD"
    assert cp.sheet == "Channel_0"


# ── upsert_clone_placement ───────────────────────────────────────────────

def test_upsert_clone_placement_matches_by_placer_name(tmp_path):
    """Same placer_name, DIFFERENT Cluster tag -> replaces in place, no dup —
    the exact 2026-08-15 live case (PIF_AVDD kept resurrecting next to
    CH0_PIF_AVDD)."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - name: PIF_AVDD\n    placer_name: CH0_PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(
        path, {"name": "NEW_TAG", "placer_name": "CH0_PIF_AVDD", "cell": "c2"}) is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 1
    assert data["clone_placements"][0]["name"] == "NEW_TAG"
    assert data["clone_placements"][0]["placer_name"] == "CH0_PIF_AVDD"


def test_upsert_clone_placement_without_placer_name_matches_by_name(tmp_path):
    """Regression guard: no placer_name anywhere -> old always-by-name
    behaviour, so untouched existing configs keep working."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - name: PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(path, {"name": "PIF_AVDD", "cell": "c2"}) is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 1
    assert data["clone_placements"][0]["cell"] == "c2"


def test_upsert_clone_placement_same_name_different_placer_name_appends(tmp_path):
    """Two entries may share a Cluster tag but have different placer_name —
    they are distinct identities and must NOT collide."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - name: PIF_AVDD\n    placer_name: CH0_PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(
        path, {"name": "PIF_AVDD", "placer_name": "CH1_PIF_AVDD", "cell": "c2"}) is False
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 2


# ── validation: effective identity must be unique ────────────────────────

def test_validation_catches_duplicate_placer_name_across_different_clusters():
    """Two DIFFERENT Cluster tags with the SAME explicit placer_name must be
    flagged — --only/upsert could not tell them apart."""
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", placer_name="CH0_PIF_AVDD"),
        _clone("OTHER", placer_name="CH0_PIF_AVDD", xy=(10.0, 10.0)),
    ])
    with pytest.raises(ValidationError, match="CH0_PIF_AVDD"):
        check_no_duplicate_clone_anchors(cfg)


def test_validation_ok_with_unique_placer_names():
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", placer_name="CH0_PIF_AVDD"),
        _clone("PIF_AVDD2", placer_name="CH1_PIF_AVDD", xy=(10.0, 10.0)),
    ])
    check_no_duplicate_clone_anchors(cfg)  # no raise


def test_validation_name_duplicate_check_still_fires():
    """The old `name` (Cluster tag) uniqueness check must remain untouched."""
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD"),
        _clone("PIF_AVDD", xy=(10.0, 10.0)),
    ])
    with pytest.raises(ValidationError, match="PIF_AVDD"):
        check_no_duplicate_clone_anchors(cfg)


# ── apply_only_filter: --only by effective identity ──────────────────────

def test_apply_only_matches_clone_by_placer_name():
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", placer_name="CH0_PIF_AVDD"),
        _clone("OTHER", xy=(10.0, 10.0)),
    ])
    derived = apply_only_filter(cfg, ["CH0_PIF_AVDD"], logger)
    assert [c.name for c in derived.clone_placements] == ["PIF_AVDD"]


def test_apply_only_still_matches_clone_by_name_fallback():
    cfg = Config(clone_placements=[_clone("PIF_AVDD")])
    derived = apply_only_filter(cfg, ["PIF_AVDD"], logger)
    assert [c.name for c in derived.clone_placements] == ["PIF_AVDD"]


def test_apply_only_does_not_match_by_raw_name_once_placer_name_set():
    """Once placer_name is set it IS the identity — the raw Cluster tag no
    longer addresses the entry via --only (avoids ambiguity)."""
    cfg = Config(clone_placements=[_clone("PIF_AVDD", placer_name="CH0_PIF_AVDD")])
    with pytest.raises(PlacerError):
        apply_only_filter(cfg, ["PIF_AVDD"], logger)
