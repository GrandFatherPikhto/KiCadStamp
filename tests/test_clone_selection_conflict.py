#!/usr/bin/env python3
"""
Tests for closing a gap: multiple ClonePlacement in "by selection" mode
in one run — physically impossible (KiCad has only one selection at a time).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import Config, ClonePlacement, Cell
from kicadstamp.validation import (
    check_single_selection_based_clone, check_clone_cells_exist, check_config_structure,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_role_resolver import clone_uses_selection_mode


def _cfg(clones, cells=None):
    return Config(
        layer='B.Cu',
        cells=cells or {"t": Cell(name="t")},
        rules=[], clone_placements=clones,
    )


class TestCloneUsesSelectionMode:
    def test_no_nets_no_params_is_selection_mode(self):
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0))
        assert clone_uses_selection_mode(c) is True

    def test_nets_present_is_not_selection_mode(self):
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0), nets={"X": "GND"})
        assert clone_uses_selection_mode(c) is False

    def test_params_present_is_not_selection_mode(self):
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0), params={"channel": 1})
        assert clone_uses_selection_mode(c) is False

    def test_by_selection_explicit_true_overrides_nets(self):
        """Explicit by_selection: true forces selection mode even if nets are non‑empty."""
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0),
                           nets={"X": "GND"}, by_selection=True)
        assert clone_uses_selection_mode(c) is True

    def test_by_selection_false_with_empty_nets_and_params_implicit_selection(self):
        """by_selection: false with empty nets/params — still selection mode (old behaviour)."""
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0), by_selection=False)
        assert clone_uses_selection_mode(c) is True

    def test_anchor_role_does_not_affect_selection_mode(self):
        """anchor_role does not change the mode; it is determined by nets/params/by_selection."""
        c = ClonePlacement(cluster="a", cell="t", xy=(0, 0),
                           anchor_role="SOME_ROLE")
        assert clone_uses_selection_mode(c) is True

        c2 = ClonePlacement(cluster="b", cell="t", xy=(0, 0),
                            anchor_role="SOME_ROLE", nets={"X": "GND"})
        assert clone_uses_selection_mode(c2) is False


class TestCheckSingleSelectionBasedClone:
    def test_single_selection_based_passes(self):
        cfg = _cfg([ClonePlacement(cluster="a", cell="t", xy=(0, 0))])
        check_single_selection_based_clone(cfg)

    def test_two_selection_based_raises_with_both_names(self):
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0)),
        ])
        with pytest.raises(ValidationError, match="'a'.*'b'|'b'.*'a'"):
            check_single_selection_based_clone(cfg)

    def test_selection_and_nets_based_do_not_conflict(self):
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0), nets={"X": "GND"}),
        ])
        check_single_selection_based_clone(cfg)

    def test_retired_clone_not_counted(self):
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0), retired=True),
        ])
        check_single_selection_based_clone(cfg)

    def test_three_selection_based_still_fatal(self):
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="c", cell="t", xy=(0, 0)),
        ])
        with pytest.raises(ValidationError):
            check_single_selection_based_clone(cfg)

    def test_by_selection_true_with_nets_counts_as_selection_based(self):
        """Even with nets, by_selection: true makes it count as selection‑based."""
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0), nets={"X": "GND"}, by_selection=True),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0), nets={"Y": "GND"}),
        ])
        # a is selection‑based (by_selection), b is nets‑based => no conflict
        check_single_selection_based_clone(cfg)

        cfg2 = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0), by_selection=True),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0), by_selection=True),
        ])
        # two with by_selection => fatal
        with pytest.raises(ValidationError):
            check_single_selection_based_clone(cfg2)


class TestCheckConfigStructureExcludesSelectionMode:
    """Regression, 2026-08-12: check_config_structure() runs on the FULL config,
    before --only narrows cfg.clone_placements (see apply_pipeline._filter_config).
    If it included check_single_selection_based_clone, two selection-based clones
    that were only ever meant to run one-at-a-time via --only NAME would fatal on
    EVERY run, regardless of --only — defeating --only's own documented purpose.
    """
    def test_two_selection_based_clones_do_not_fail_config_structure(self):
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="t", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="t", xy=(0, 0)),
        ])
        check_config_structure(cfg)  # must not raise
        with pytest.raises(ValidationError):
            check_single_selection_based_clone(cfg)  # still caught, just not here


class TestCheckCloneCellsExist:
    def test_existing_cell_passes(self):
        cfg = _cfg([ClonePlacement(cluster="a", cell="t", xy=(0, 0))])
        check_clone_cells_exist(cfg)

    def test_missing_cell_raises(self):
        cfg = _cfg([ClonePlacement(cluster="a", cell="does_not_exist", xy=(0, 0))])
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_clone_cells_exist(cfg)

    def test_retired_clone_missing_cell_not_checked(self):
        cfg = _cfg([ClonePlacement(cluster="a", cell="does_not_exist", xy=(0, 0),
                                   retired=True)])
        check_clone_cells_exist(cfg)  # should not raise – retired