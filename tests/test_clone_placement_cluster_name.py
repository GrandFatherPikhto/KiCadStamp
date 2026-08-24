# tests/test_clone_placement_cluster_name.py
"""Tests for the ClonePlacement Cluster/identity split (2026-08-24, the field
rename that superseded 2026-08-15's placer_name split): `cluster` carries the
physical Cluster tag (the old `name`), and the OPTIONAL `name` field carries the
config-bookkeeping save/--only identity (the old `placer_name`).

Covered here (backend only — the GUI-side behaviour lives in
tests/gui/test_placer_dock.py):
- clone_placement_effective_name(): name if set, else cluster.
- load_clone_placement(): cluster is required, name is read (and is a known
  key), defaults to None.
- upsert_clone_placement(): identity for replace-in-place is name when set,
  else cluster (regression: the old always-by-name behaviour).
- check_no_duplicate_clone_anchors(): the effective identity must be unique
  even when the Cluster tags are shared (legitimately — a reused hierarchical
  sheet clones identical Cluster onto every instance); the old duplicate check
  on the Cluster tag itself was REMOVED.
- apply_only_filter(): --only resolves clones by name when set, else by
  cluster.
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

logger = logging.getLogger("test_clone_placement_cluster_name")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _clone(cluster, **kwargs):
    defaults = {"xy": (0.0, 0.0), "cell": "t"}
    defaults.update(kwargs)
    return ClonePlacement(cluster=cluster, **defaults)


# ── clone_placement_effective_name ───────────────────────────────────────

def test_effective_name_falls_back_to_cluster_when_name_unset():
    assert clone_placement_effective_name(_clone("PIF_AVDD")) == "PIF_AVDD"


def test_effective_name_uses_name_when_set():
    clone = _clone("PIF_AVDD", name="CH0_PIF_AVDD")
    assert clone_placement_effective_name(clone) == "CH0_PIF_AVDD"


# ── load_clone_placement ─────────────────────────────────────────────────

def test_load_clone_placement_reads_name():
    cp = load_clone_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
                               "cell": "t", "xy": [0.0, 0.0]})
    assert cp.cluster == "PIF_AVDD"
    assert cp.name == "CH0_PIF_AVDD"


def test_load_clone_placement_name_defaults_to_none():
    cp = load_clone_placement({"cluster": "PIF_AVDD", "cell": "t", "xy": [0.0, 0.0]})
    assert cp.name is None


def test_load_clone_placement_round_trips_name():
    cp = load_clone_placement({"cluster": "PIF_AVDD", "name": "CH0_PIF_AVDD",
                               "sheet": "Channel_0", "cell": "t", "xy": [1.0, 2.0]})
    assert cp.name == "CH0_PIF_AVDD"
    assert cp.sheet == "Channel_0"


# ── upsert_clone_placement ───────────────────────────────────────────────

def test_upsert_clone_placement_matches_by_name(tmp_path):
    """Same name, DIFFERENT Cluster tag -> replaces in place, no dup — the
    exact 2026-08-15 live case (PIF_AVDD kept resurrecting next to
    CH0_PIF_AVDD)."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - cluster: PIF_AVDD\n    name: CH0_PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(
        path, {"cluster": "NEW_TAG", "name": "CH0_PIF_AVDD", "cell": "c2"}) is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 1
    assert data["clone_placements"][0]["cluster"] == "NEW_TAG"
    assert data["clone_placements"][0]["name"] == "CH0_PIF_AVDD"


def test_upsert_clone_placement_without_name_matches_by_cluster(tmp_path):
    """Regression guard: no name anywhere -> old always-by-cluster behaviour,
    so untouched existing configs keep working."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - cluster: PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(path, {"cluster": "PIF_AVDD", "cell": "c2"}) is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 1
    assert data["clone_placements"][0]["cell"] == "c2"


def test_upsert_clone_placement_same_cluster_different_name_appends(tmp_path):
    """Two entries may share a Cluster tag but have different name — they are
    distinct identities and must NOT collide."""
    path = tmp_path / "root.yaml"
    _write(path, "clone_placements:\n  - cluster: PIF_AVDD\n    name: CH0_PIF_AVDD\n    cell: c1\n")
    assert upsert_clone_placement(
        path, {"cluster": "PIF_AVDD", "name": "CH1_PIF_AVDD", "cell": "c2"}) is False
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["clone_placements"]) == 2


# ── validation: effective identity must be unique ────────────────────────

def test_validation_catches_duplicate_name_across_different_clusters():
    """Two DIFFERENT Cluster tags with the SAME explicit name must be flagged —
    --only/upsert could not tell them apart."""
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", name="CH0_PIF_AVDD"),
        _clone("OTHER", name="CH0_PIF_AVDD", xy=(10.0, 10.0)),
    ])
    with pytest.raises(ValidationError, match="CH0_PIF_AVDD"):
        check_no_duplicate_clone_anchors(cfg)


def test_validation_ok_with_unique_names():
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", name="CH0_PIF_AVDD"),
        _clone("PIF_AVDD2", name="CH1_PIF_AVDD", xy=(10.0, 10.0)),
    ])
    check_no_duplicate_clone_anchors(cfg)  # no raise


def test_validation_shared_cluster_is_no_longer_a_duplicate():
    """The old duplicate check on the Cluster tag itself was REMOVED (2026-08-24):
    two instances of one reused hierarchical sheet legitimately share a Cluster
    and are distinct by their explicit name — no fatal here."""
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", name="CH0_PIF_AVDD", sheet="Channel_0"),
        _clone("PIF_AVDD", name="CH1_PIF_AVDD", sheet="Channel_1", xy=(10.0, 10.0)),
    ])
    check_no_duplicate_clone_anchors(cfg)  # no raise


# ── apply_only_filter: --only by effective identity ──────────────────────

def test_apply_only_matches_clone_by_name():
    cfg = Config(clone_placements=[
        _clone("PIF_AVDD", name="CH0_PIF_AVDD"),
        _clone("OTHER", xy=(10.0, 10.0)),
    ])
    derived = apply_only_filter(cfg, ["CH0_PIF_AVDD"], logger)
    assert [c.cluster for c in derived.clone_placements] == ["PIF_AVDD"]


def test_apply_only_still_matches_clone_by_cluster_fallback():
    cfg = Config(clone_placements=[_clone("PIF_AVDD")])
    derived = apply_only_filter(cfg, ["PIF_AVDD"], logger)
    assert [c.cluster for c in derived.clone_placements] == ["PIF_AVDD"]


def test_apply_only_does_not_match_by_raw_cluster_once_name_set():
    """Once name is set it IS the identity — the raw Cluster tag no longer
    addresses the entry via --only (avoids ambiguity)."""
    cfg = Config(clone_placements=[_clone("PIF_AVDD", name="CH0_PIF_AVDD")])
    with pytest.raises(PlacerError):
        apply_only_filter(cfg, ["PIF_AVDD"], logger)
