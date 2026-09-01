#!/usr/bin/env python3
"""
Tests for closing a gap: multiple ClonePlacement in "by selection" mode
in one run — physically impossible (KiCad has only one selection at a time).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.domain.board import Footprint
from kicadstamp.config import Config, ClonePlacement, Cell, TemplateComponentSlot
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.validation import (
    check_single_selection_based_clone, check_clone_cells_exist, check_config_structure,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.clone_role_resolver import clone_uses_selection_mode


def _make_fp(ref, role=None, nets=None, cluster=None):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._nets = nets or []
    fp._cluster = cluster
    return fp


def _adapter(fps):
    """Live-adapter mock with Role/Cluster fields + sequential pad nets — the
    shape _auto_derive_live_net / clone_uses_selection_mode (adaptive) read."""
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = lambda fp, name: (
        fp._role if name == ROLE_FIELD_NAME
        else fp._cluster if name == CLUSTER_FIELD_NAME else None)
    adapter.get_footprint_pads.side_effect = lambda fp: [
        MagicMock(number=str(i), net_name=n) for i, n in enumerate(fp._nets, start=1)]
    adapter.get_selected_items.return_value = []
    return adapter


def _cfg(clones, cells=None):
    return Config(
        layer='B.Cu',
        cells=cells or {"t": Cell(name="t")},
        chains=[], clone_placements=clones,
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


class TestCloneUsesSelectionModeAdaptive:
    """Phase 2 step 2.3 — the implicit default (no nets/params/by_selection) is
    chosen adaptively by the availability of an unambiguous source instance on
    the live board (adapter+cell given): all-derivable -> by-nets (auto), else
    -> by-selection. The pure path (no adapter) keeps the legacy default."""

    def test_implicit_derivable_cell_is_by_nets_auto(self):
        """A repeated section whose every role has a unique live instance ->
        by-nets (auto), no manual selection needed."""
        cell = Cell(name="t", components=[TemplateComponentSlot(role="CAP_IN"),
                                          TemplateComponentSlot(role="CAP_OUT")])
        fps = [_make_fp("C1", "CAP_IN", ["+3V3"], cluster="ch1"),
               _make_fp("C2", "CAP_OUT", ["+3V3_FILTERED"], cluster="ch1")]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="ch1", cell="t", xy=(0, 0))
        assert clone_uses_selection_mode(clone, adapter=adapter, cell=cell) is False

    def test_implicit_non_derivable_cell_is_by_selection(self):
        """A one-off whose role has no unambiguous live source (two candidates
        on different nets) -> by-selection (the user selects it)."""
        cell = Cell(name="t", components=[TemplateComponentSlot(role="MCU")])
        fps = [_make_fp("U1", "MCU", ["NET_A"], cluster="c"),
               _make_fp("U2", "MCU", ["NET_B"], cluster="c")]
        adapter = _adapter(fps)
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))
        assert clone_uses_selection_mode(clone, adapter=adapter, cell=cell) is True

    def test_implicit_pure_path_keeps_legacy_default(self):
        """No adapter/cell -> the legacy default (by-selection) for config-only
        callers (validation), unchanged."""
        clone = ClonePlacement(cluster="a", cell="t", xy=(0, 0))
        assert clone_uses_selection_mode(clone) is True

    def test_role_with_net_template_is_derivable_even_without_live_instance(self):
        """A role with an explicit net_template is by-nets-viable regardless of
        the live board — a cell of such roles is derivable and defaults to
        by-nets even when the board has no tagged footprint yet."""
        cell = Cell(name="t", components=[
            TemplateComponentSlot(role="X", net_template="NET1")])
        adapter = _adapter([])  # no live footprints at all
        clone = ClonePlacement(cluster="c", cell="t", xy=(0, 0))
        assert clone_uses_selection_mode(clone, adapter=adapter, cell=cell) is False


class TestCheckSingleSelectionBasedCloneAdaptive:
    """Phase 2 step 2.3 — with a live adapter, implicit clones whose cells
    auto-derive are by-nets and must NOT be flagged as needing the single
    selection; non-derivable one-offs still are."""

    def test_two_implicit_auto_derivable_not_flagged(self):
        cells = {"a": Cell(name="a", components=[TemplateComponentSlot(role="CAP_IN")]),
                 "b": Cell(name="b", components=[TemplateComponentSlot(role="CAP_OUT")])}
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="a", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="b", xy=(0, 0)),
        ], cells=cells)
        fps = [_make_fp("C1", "CAP_IN", ["+3V3"], cluster="a"),
               _make_fp("C2", "CAP_OUT", ["+3V3_FILTERED"], cluster="b")]
        check_single_selection_based_clone(cfg, adapter=_adapter(fps))  # no raise

    def test_two_implicit_non_derivable_flagged(self):
        """Two one-offs whose roles have no unambiguous source still both need
        the (single) selection -> fatal, exactly as before."""
        cells = {"a": Cell(name="a", components=[TemplateComponentSlot(role="MCU")]),
                 "b": Cell(name="b", components=[TemplateComponentSlot(role="MCU")])}
        cfg = _cfg([
            ClonePlacement(cluster="a", cell="a", xy=(0, 0)),
            ClonePlacement(cluster="b", cell="b", xy=(0, 0)),
        ], cells=cells)
        fps = [_make_fp("U1", "MCU", ["NET_A"], cluster="a"),
               _make_fp("U2", "MCU", ["NET_B"], cluster="a"),
               _make_fp("U3", "MCU", ["NET_C"], cluster="b"),
               _make_fp("U4", "MCU", ["NET_D"], cluster="b")]
        with pytest.raises(ValidationError, match="'a'.*'b'|'b'.*'a'"):
            check_single_selection_based_clone(cfg, adapter=_adapter(fps))