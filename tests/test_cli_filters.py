#!/usr/bin/env python3
"""Tests for the pure (no KiCad adapter) apply filters: retired/--only/--cluster —
kicadstamp/apply_pipeline.py:drop_disabled_rules/apply_only_filter/apply_cluster_filter.
CONTRACT: every filter returns a DERIVED Config (copy-on-write via
dataclasses.replace) and never mutates the input object — a preloaded cfg (e.g.
the GUI's shared object) is never the config applied/modified by a run.
Order matters: retired wins UNCONDITIONALLY, before --only/--cluster (see the
Rule docstring in config/models.py) — --only cannot resurrect a retired rule."""
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import (Config, Rule, ManualSpoke, ClonePlacement, ThermalViaArrayConfig,
                              CoordinatePlacement)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.apply_pipeline import (
    _split_comma_values, _matches_any_cluster, _compute_all_anchor_ids,
    drop_disabled_rules, drop_inactive_items, apply_only_filter, apply_cluster_filter,
)
from kicadstamp.cli_extract import load_profile, EXTRACT_PROFILE_KNOWN_KEYS, CLONE_EXTRACT_PROFILE_KNOWN_KEYS
from kicadstamp.exceptions import PlacerError, ValidationError

logger = logging.getLogger("test_cli_filters")


def _cfg(rules=None, clone_placements=None, thermal_via_arrays=None, coordinate_placements=None):
    return Config(chains=rules or [], clone_placements=clone_placements or [],
                  thermal_via_arrays=thermal_via_arrays or [],
                  coordinate_placements=coordinate_placements or [])


class TestSplitCommaValues:
    def test_none_or_empty(self):
        assert _split_comma_values(None) == []
        assert _split_comma_values([]) == []

    def test_repeated_flag(self):
        assert _split_comma_values(["a", "b"]) == ["a", "b"]

    def test_comma_within_one_occurrence(self):
        assert _split_comma_values(["a,b"]) == ["a", "b"]

    def test_mixed_repeat_and_comma(self):
        assert _split_comma_values(["a,b", "c"]) == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert _split_comma_values([" a , b "]) == ["a", "b"]


class TestMatchesAnyCluster:
    def test_none_candidate_never_matches(self):
        assert _matches_any_cluster(None, ["Channel_0"]) is False

    def test_exact_match(self):
        assert _matches_any_cluster("Channel_0", ["Channel_0"]) is True

    def test_segment_prefix_match(self):
        assert _matches_any_cluster("Channel_0/DAC_OA/OA", ["Channel_0"]) is True

    def test_no_false_prefix_on_partial_segment(self):
        assert _matches_any_cluster("Channel_10", ["Channel_1"]) is False

    def test_matches_any_of_several_wanted(self):
        assert _matches_any_cluster("Channel_2/X", ["Channel_0", "Channel_2"]) is True


class TestDropDisabledRules:
    def test_retired_rule_dropped(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", retired=True),
            Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", retired=False),
        ])
        cfg = drop_disabled_rules(cfg, logger)
        assert [r.net for r in cfg.rules] == ["+3V3_VCCIO"]

    def test_only_cannot_resurrect_retired_rule(self):
        """retired:true wins unconditionally — --only naming the very same
        rule must NOT bring it back (it's not even "not found", it plain
        doesn't exist for this run, same as if deleted from the YAML)."""
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", retired=True),
        ])
        cfg = drop_disabled_rules(cfg, logger)
        with pytest.raises(PlacerError):
            apply_only_filter(cfg, ["GND"], logger)
        assert cfg.rules == []


class TestComputeAllAnchorIds:
    """_compute_all_anchor_ids feeds registry.reconcile()'s known_anchor_ids
    protection — each thermal_via_arrays entry must contribute its OWN id
    (thermal:<name>), independently of its siblings, so retiring/narrowing
    one array can never affect another array's already-placed vias."""

    def test_each_active_thermal_via_array_contributes_its_own_id(self):
        cfg = _cfg(thermal_via_arrays=[
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch1_thermal"),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch2_thermal"),
        ])
        ids = _compute_all_anchor_ids(cfg)
        assert ids == {"thermal:ad9707_ch1_thermal", "thermal:ad9707_ch2_thermal"}

    def test_retired_thermal_via_array_excluded_others_kept(self):
        cfg = _cfg(thermal_via_arrays=[
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch1_thermal", retired=True),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch2_thermal", retired=False),
        ])
        ids = _compute_all_anchor_ids(cfg)
        assert ids == {"thermal:ad9707_ch2_thermal"}


class TestDropInactiveItems:
    """skip: true — the inline counterpart of --only/--cluster (skip this
    run, but do NOT prune from the registry, unlike retired: true). See
    Rule/ClonePlacement/ThermalViaArrayConfig.skip in config/models.py."""

    def test_skipped_rule_dropped_non_skipped_rule_kept(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")],
                 anchor_role="FPGA", skip=True),
            Rule(net="+3V3_VCCIO", spokes=[ManualSpoke(pad="2", cell="t")],
                 anchor_role="FPGA", skip=False),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert [r.net for r in cfg.rules] == ["+3V3_VCCIO"]

    def test_skipped_spoke_narrows_rule_without_dropping_it(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=False),
            ManualSpoke(pad="2", cell="t", skip=True),
        ], anchor_role="FPGA")])
        cfg = drop_inactive_items(cfg, logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]

    def test_rule_dropped_entirely_if_all_spokes_skipped(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=True),
        ], anchor_role="FPGA")])
        cfg = drop_inactive_items(cfg, logger)
        assert cfg.rules == []

    def test_original_rule_object_not_mutated(self):
        original = Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=False),
            ManualSpoke(pad="2", cell="t", skip=True),
        ], anchor_role="FPGA")
        cfg = _cfg(rules=[original])
        cfg = drop_inactive_items(cfg, logger)
        assert len(original.spokes) == 2

    def test_skipped_clone_placement_removed(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(cluster="a", xy=(0.0, 0.0), cell="t", skip=True),
            ClonePlacement(cluster="b", xy=(0.0, 0.0), cell="t", skip=False),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert [c.cluster for c in cfg.clone_placements] == ["b"]

    def test_skipped_thermal_via_array_dropped_for_this_run(self):
        cfg = _cfg(thermal_via_arrays=[ThermalViaArrayConfig(
            retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal", skip=True,
        )])
        cfg = drop_inactive_items(cfg, logger)
        assert cfg.thermal_via_arrays == []

    def test_one_of_several_thermal_via_arrays_skipped_others_kept(self):
        """2026-08-02: thermal_via_arrays is now a real list — each entry's
        skip: must be independent of its siblings, same as rules/
        clone_placements (the AD9707-per-channel motivating case: skipping
        one channel's thermal vias must not touch any other channel's)."""
        cfg = _cfg(thermal_via_arrays=[
            ThermalViaArrayConfig(anchor_role="FPGA", pad="145", name="fpga_thermal", skip=True),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch1_thermal", skip=False),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch2_thermal", skip=False),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert [t.name for t in cfg.thermal_via_arrays] == ["ad9707_ch1_thermal", "ad9707_ch2_thermal"]

    def test_skip_false_everywhere_is_noop(self):
        cfg = _cfg(
            rules=[Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")], anchor_role="FPGA")],
            clone_placements=[ClonePlacement(cluster="a", xy=(0.0, 0.0), cell="t")],
            thermal_via_arrays=[ThermalViaArrayConfig(retired=False, anchor_role="FPGA", pad="145", name="th")],
        )
        cfg = drop_inactive_items(cfg, logger)
        assert len(cfg.rules) == 1
        assert len(cfg.clone_placements) == 1
        assert len(cfg.thermal_via_arrays) == 1
        assert cfg.thermal_via_arrays[0].retired is False

    def test_skipped_coordinate_placement_dropped_for_this_run(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="X", role="R1", x_mm=0.0, y_mm=0.0, rotation_deg=0.0, skip=True),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert cfg.coordinate_placements == []

    def test_retired_coordinate_placement_dropped_for_this_run(self):
        """Unlike Rule (retired handled by drop_disabled_rules, a separate
        step run BEFORE this one), CoordinatePlacement has no registry to
        protect — retired and skip both just mean "not this run", handled
        together right here (see drop_inactive_items' own comment)."""
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="X", role="R1", x_mm=0.0, y_mm=0.0, rotation_deg=0.0,
                                retired=True),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert cfg.coordinate_placements == []

    def test_one_of_several_coordinate_placements_skipped_others_kept(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="X", role="R1", x_mm=0.0, y_mm=0.0, rotation_deg=0.0, skip=True),
            CoordinatePlacement(cluster="X", role="R2", x_mm=1.0, y_mm=1.0, rotation_deg=0.0),
        ])
        cfg = drop_inactive_items(cfg, logger)
        assert [cp.role for cp in cfg.coordinate_placements] == ["R2"]

    def test_skip_true_does_not_affect_known_anchor_ids_computation_order(self):
        """drop_inactive_items only mutates cfg — it must NOT be confused with
        drop_disabled_rules: a rule with retired=False, skip=True still
        contributes to rule_anchor_ids's input set (cfg.rules) at the point
        known_anchor_ids is computed in cmd_apply, i.e. BEFORE this function
        runs. This test just documents that drop_inactive_items itself makes
        no such distinction — it purely filters on .skip."""
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")],
                 anchor_role="FPGA", retired=False, skip=True),
        ])
        assert cfg.rules[0].retired is False
        cfg = drop_inactive_items(cfg, logger)
        assert cfg.rules == []


class TestApplyOnlyFilter:
    def test_no_only_names_is_noop(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[], anchor_role="FPGA")])
        cfg = apply_only_filter(cfg, [], logger)
        assert len(cfg.rules) == 1

    def test_matches_by_net_when_name_unset(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA"),
            Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA"),
        ])
        cfg = apply_only_filter(cfg, ["GND"], logger)
        assert [r.net for r in cfg.rules] == ["GND"]

    def test_matches_by_explicit_name(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", name="fpga_gnd"),
        ])
        cfg = apply_only_filter(cfg, ["fpga_gnd"], logger)
        assert len(cfg.rules) == 1

    def test_only_selects_one_of_several_thermal_via_arrays_by_name(self):
        cfg = _cfg(thermal_via_arrays=[
            ThermalViaArrayConfig(anchor_role="FPGA", pad="145", name="fpga_thermal"),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch1_thermal"),
        ])
        cfg = apply_only_filter(cfg, ["ad9707_ch1_thermal"], logger)
        assert [t.name for t in cfg.thermal_via_arrays] == ["ad9707_ch1_thermal"]

    def test_matches_clone_placement_by_name(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(cluster="p5v_pi_filter", xy=(0.0, 0.0), cell="t"),
            ClonePlacement(cluster="other", xy=(0.0, 0.0), cell="t"),
        ])
        cfg = apply_only_filter(cfg, ["p5v_pi_filter"], logger)
        assert [c.cluster for c in cfg.clone_placements] == ["p5v_pi_filter"]

    def test_unknown_name_exits_fatal(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[], anchor_role="FPGA")])
        with pytest.raises(PlacerError):
            apply_only_filter(cfg, ["typo_name"], logger)

    def test_matches_coordinate_placement_by_default_name(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="FPGA_PERIPH", role="R18", x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
            CoordinatePlacement(cluster="FPGA_PERIPH", role="R19", x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
        ])
        cfg = apply_only_filter(cfg, ["FPGA_PERIPH/R18"], logger)
        assert [cp.role for cp in cfg.coordinate_placements] == ["R18"]

    def test_matches_coordinate_placement_by_explicit_name(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="X", role="R1", name="my_row",
                                x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
        ])
        cfg = apply_only_filter(cfg, ["my_row"], logger)
        assert len(cfg.coordinate_placements) == 1

    def test_only_does_not_match_retired_coordinate_placement(self):
        """Retired coordinate_placements must not be selectable by --only —
        matches the thermal_via_arrays `not t.retired` guard (2026-08-12,
        Group 2 fix: the coordinate block skipped the guard, so a direct
        apply_only_filter call could resurrect a retired row)."""
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="X", role="R1", name="dead",
                                x_mm=0.0, y_mm=0.0, rotation_deg=0.0, retired=True),
        ])
        with pytest.raises(PlacerError):
            apply_only_filter(cfg, ["dead"], logger)


class TestApplyClusterFilter:
    def test_no_cluster_paths_is_noop(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
        ], anchor_role="FPGA")])
        cfg = apply_cluster_filter(cfg, [], logger)
        assert len(cfg.rules[0].spokes) == 1

    def test_narrows_spokes_within_rule(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
            ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")])
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]

    def test_rule_dropped_entirely_if_no_spoke_matches(self):
        # A matching clone_placement keeps the overall filter from fataling
        # on "matched nothing anywhere" — isolates just the rule-dropping behaviour.
        cfg = _cfg(
            rules=[Rule(net="GND", spokes=[
                ManualSpoke(pad="1", cell="t", cluster="Channel_1"),
            ], anchor_role="FPGA")],
            clone_placements=[ClonePlacement(cluster="ch0", xy=(0.0, 0.0),
                                             cell="t", anchor_cluster="Channel_0")],
        )
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert cfg.rules == []

    def test_original_rule_object_not_mutated(self):
        """dataclasses.replace makes a copy — the caller's original Rule.spokes
        list must stay untouched (relevant if the same cfg is reused/logged)."""
        original = Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
            ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")
        cfg = _cfg(rules=[original])
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(original.spokes) == 2

    def test_clone_placement_narrowed_by_anchor_cluster(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(cluster="ch0", xy=(0.0, 0.0), cell="t",
                          anchor_cluster="Channel_0"),
            ClonePlacement(cluster="ch1", xy=(0.0, 0.0), cell="t",
                          anchor_cluster="Channel_1"),
        ])
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert [c.cluster for c in cfg.clone_placements] == ["ch0"]

    def test_thermal_via_array_narrowed_by_anchor_cluster(self):
        # A matching clone_placement keeps the overall filter from fataling
        # on "matched nothing anywhere" — isolates just the thermal behaviour.
        cfg = _cfg(
            clone_placements=[ClonePlacement(cluster="ch0", xy=(0.0, 0.0),
                                             cell="t", anchor_cluster="Channel_0")],
            thermal_via_arrays=[ThermalViaArrayConfig(
                retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal",
                anchor_cluster="Channel_1",
            )],
        )
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert cfg.thermal_via_arrays == []

    def test_cluster_narrows_among_several_thermal_via_arrays(self):
        cfg = _cfg(thermal_via_arrays=[
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch1_thermal",
                                  anchor_cluster="Channel_1"),
            ThermalViaArrayConfig(anchor_role="AD9707", pad="7", name="ad9707_ch2_thermal",
                                  anchor_cluster="Channel_2"),
        ])
        cfg = apply_cluster_filter(cfg, ["Channel_1"], logger)
        assert [t.name for t in cfg.thermal_via_arrays] == ["ad9707_ch1_thermal"]

    def test_no_match_anywhere_exits_fatal(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")])
        with pytest.raises(PlacerError):
            apply_cluster_filter(cfg, ["Channel_9"], logger)

    def test_coordinate_placement_narrowed_by_its_own_cluster_field(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="Channel_0", role="R1", x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
            CoordinatePlacement(cluster="Channel_1", role="R2", x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
        ])
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert [cp.role for cp in cfg.coordinate_placements] == ["R1"]

    def test_coordinate_placement_cluster_match_is_prefix_not_exact(self):
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="Channel_0/Sub", role="R1",
                                x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
        ])
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(cfg.coordinate_placements) == 1

    def test_cluster_does_not_match_retired_coordinate_placement(self):
        """Same `not retired` guard as thermal_via_arrays in the cluster
        filter (2026-08-12, Group 2 fix)."""
        cfg = _cfg(coordinate_placements=[
            CoordinatePlacement(cluster="Channel_0", role="R1",
                                x_mm=0.0, y_mm=0.0, rotation_deg=0.0, retired=True),
        ])
        with pytest.raises(PlacerError):
            apply_cluster_filter(cfg, ["Channel_0"], logger)

    def test_only_and_cluster_compose_as_and(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[
                ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
                ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
            ], anchor_role="FPGA", name="fpga_gnd"),
            Rule(net="+3V3_VCCIO", spokes=[
                ManualSpoke(pad="3", cell="t", cluster="Channel_0"),
            ], anchor_role="FPGA"),
        ])
        cfg = apply_only_filter(cfg, ["fpga_gnd"], logger)
        cfg = apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]


class TestFiltersDeriveNewConfig:
    """T3.2 contract: each filter returns a DERIVED Config (copy-on-write via
    dataclasses.replace) and never mutates the caller's input object — so a
    preloaded cfg (e.g. the GUI's shared object) is never the config that gets
    applied or modified by a run. Chaining the filters in the pipeline's order
    must leave the ORIGINAL cfg (and its rules/clone_placements/
    thermal_via_arrays) completely untouched."""

    @staticmethod
    def _full_cfg():
        return _cfg(
            rules=[
                Rule(net="GND", spokes=[
                    ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
                    ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
                ], anchor_role="FPGA", name="fpga_gnd"),
                Rule(net="+3V3_VCCIO", spokes=[
                    ManualSpoke(pad="3", cell="t", cluster="Channel_0"),
                ], anchor_role="FPGA"),
            ],
            clone_placements=[
                ClonePlacement(cluster="ch0", xy=(0.0, 0.0), cell="t",
                              anchor_cluster="Channel_0"),
                ClonePlacement(cluster="ch1", xy=(0.0, 0.0), cell="t",
                              anchor_cluster="Channel_1"),
            ],
            thermal_via_arrays=[
                ThermalViaArrayConfig(anchor_role="AD9707", pad="7",
                                      name="ad9707_ch1_thermal", anchor_cluster="Channel_1"),
                ThermalViaArrayConfig(anchor_role="AD9707", pad="7",
                                      name="ad9707_ch2_thermal", anchor_cluster="Channel_2"),
            ],
            coordinate_placements=[
                CoordinatePlacement(cluster="Channel_0", role="R1",
                                    x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
                CoordinatePlacement(cluster="Channel_1", role="R2",
                                    x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
            ],
        )

    def test_dropping_filters_always_return_a_new_config_object(self):
        base = self._full_cfg()
        assert drop_disabled_rules(base) is not base
        assert drop_inactive_items(base) is not base

    def test_drop_disabled_rules_always_derives_fresh_object_even_when_noop(self):
        """drop_disabled_rules ALWAYS returns a fresh derived Config (even when
        nothing is retired), so the pipeline's self.cfg can never be the caller's
        (e.g. GUI's) original object."""
        cfg = _cfg(rules=[Rule(net="GND", spokes=[], anchor_role="FPGA", retired=False)])
        result = drop_disabled_rules(cfg, logger)
        assert result is not cfg
        assert [r.net for r in result.rules] == ["GND"]

    def test_pipeline_chain_leaves_original_config_untouched(self):
        """The exact _filter_config chain — drop_disabled_rules →
        drop_inactive_items → apply_only_filter → apply_cluster_filter — must
        leave the ORIGINAL input cfg object and its lists exactly as they were;
        the applied config is the final derived object, never the input."""
        cfg = self._full_cfg()
        original_rules = list(cfg.rules)
        original_clones = list(cfg.clone_placements)
        original_tvas = list(cfg.thermal_via_arrays)
        original_coords = list(cfg.coordinate_placements)

        derived = drop_disabled_rules(cfg)
        derived = drop_inactive_items(derived)
        derived = apply_only_filter(derived, ["fpga_gnd"])
        derived = apply_cluster_filter(derived, ["Channel_0"])

        # The config that would be applied is a DIFFERENT object...
        assert derived is not cfg
        # ...and the input cfg still holds its original content untouched.
        assert cfg.rules == original_rules
        assert cfg.clone_placements == original_clones
        assert cfg.thermal_via_arrays == original_tvas
        assert cfg.coordinate_placements == original_coords
        assert len(cfg.rules) == 2
        assert len(cfg.rules[0].spokes) == 2

        # The derived config reflects the chain: only fpga_gnd / Channel_0 survive.
        assert [r.net for r in derived.rules] == ["GND"]
        assert [s.pad for s in derived.rules[0].spokes] == ["1"]

    def test_noop_filters_return_input_object_unchanged(self):
        """The two no-op filters (no --only / no --cluster) return their input
        UNCHANGED (same object) — safe because by then the pipeline's cfg is
        already a fresh derived object from drop_disabled_rules."""
        cfg = self._full_cfg()
        assert apply_only_filter(cfg, []) is cfg
        assert apply_cluster_filter(cfg, []) is cfg


class TestLoadProfileRootDefaults:
    """root_defaults on load_profile — a field set once at the file's root
    (sibling to top_key) fills in for any profile that doesn't set it itself;
    a profile that does set it keeps its own value. Added 2026-07-27 so
    extract_profiles entries stop repeating the same output: in every block."""

    def _write(self, tmp_path, data):
        p = tmp_path / "profiles.sexp"
        p.write_text(dict_to_sexp(data), encoding="utf-8")
        return str(p)

    def test_root_default_fills_missing_field(self, tmp_path):
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert prof["output"] == "shared.yaml"

    def test_profile_own_value_wins_over_root_default(self, tmp_path):
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a", "output": "own.yaml"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert prof["output"] == "own.yaml"

    def test_no_root_defaults_requested_unchanged(self, tmp_path):
        """Old call sites (e.g. clone-extract) that don't pass root_defaults
        see no behaviour change — a root-level output: is simply not merged in."""
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a")
        assert "output" not in prof

    def test_missing_root_field_just_absent(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert "output" not in prof


class TestLoadProfileIncludes:
    """load_profile() resolves include: (kicadstamp/config/includes.py) the
    same way load_config() does, so a subsystem file's extract_profiles/
    clone_profiles are visible here too — not just rules/clone_placements."""

    def test_extract_profiles_from_include_are_visible(self, tmp_path):
        (tmp_path / "sub.sexp").write_text(
            dict_to_sexp({"extract_profiles": {"b": {"name": "b"}}}),
            encoding="utf-8")
        path = tmp_path / "profiles.sexp"
        path.write_text(dict_to_sexp({"include": ["sub.sexp"]}), encoding="utf-8")

        prof = load_profile(str(path), "extract_profiles", "b")
        assert prof["name"] == "b"


class TestLoadProfileErrors:
    """load_profile() must raise PlacerError (not sys.exit) when the profiles
    file or the named profile is missing — library functions report failure via
    exceptions, the CLI layer maps them to exit codes."""

    def test_missing_profiles_file_raises_placer_error(self):
        with pytest.raises(PlacerError, match="not found"):
            load_profile("no_such_profiles.sexp", "extract_profiles", "a")

    def test_missing_profile_raises_placer_error(self, tmp_path):
        path = tmp_path / "profiles.sexp"
        path.write_text(dict_to_sexp({"extract_profiles": {"a": {"name": "a"}}}),
                        encoding="utf-8")
        with pytest.raises(PlacerError, match="profile 'b' not found"):
            load_profile(str(path), "extract_profiles", "b")


class TestLoadProfileKnownKeys:
    """known_keys param on load_profile() — regression coverage for the exact
    bug that motivated it (see check_unknown_keys/_EXTRACT_PROFILE_KNOWN_KEYS
    docstrings): a dash instead of underscore ('origin-by-via-net' instead of
    'origin_by_via_net') was previously silently ignored — dict.get() just
    returns None, origin quietly fell back to the selection bbox instead of
    the intended via, no error at all. The fix (check_unknown_keys wired into
    load_profile) had no direct test until now."""

    def _write(self, tmp_path, data):
        p = tmp_path / "profiles.sexp"
        p.write_text(dict_to_sexp(data), encoding="utf-8")
        return str(p)

    def test_dash_typo_in_extract_profile_is_fatal(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        with pytest.raises(ValidationError, match="origin-by-via-net"):
            load_profile(path, "extract_profiles", "a", known_keys=EXTRACT_PROFILE_KNOWN_KEYS)

    def test_suggests_close_match_for_extract_profile(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        with pytest.raises(ValidationError, match="origin_by_via_net"):
            load_profile(path, "extract_profiles", "a", known_keys=EXTRACT_PROFILE_KNOWN_KEYS)

    def test_all_known_extract_profile_fields_load_fine(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {
                    "name": "a", "output": "out.yaml", "params": {"channel": 1},
                    "net_template": {"DAC1_DB1": "DAC{channel}_DB1"},
                    "net_template_role": {"PI_FILTER_FB": "+5V_DIRTY"},
                    "origin_by_via_net": "GND",
                    "origin_by_component_role": "FPGA",
                    "origin_by_component_pad": "3",
                },
            },
        })
        prof = load_profile(path, "extract_profiles", "a", known_keys=EXTRACT_PROFILE_KNOWN_KEYS)
        assert prof["origin_by_via_net"] == "GND"

    def test_without_known_keys_typo_is_silently_ignored(self, tmp_path):
        """Documents the OLD (still-reachable if a caller omits known_keys)
        behaviour, for contrast with the fatal above — not a recommendation."""
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        prof = load_profile(path, "extract_profiles", "a")
        assert "origin_by_via_net" not in prof
        assert "origin-by-via-net" in prof

    def test_dash_typo_in_clone_profile_is_fatal(self, tmp_path):
        path = self._write(tmp_path, {
            "clone_profiles": {
                "a": {"net": "n.net", "pcb": "b.kicad_pcb", "channel": "Channel_0",
                      "out-put": "out.yaml"},
            },
        })
        with pytest.raises(ValidationError, match="out-put"):
            load_profile(path, "clone_profiles", "a", known_keys=CLONE_EXTRACT_PROFILE_KNOWN_KEYS)

    def test_all_known_clone_profile_fields_load_fine(self, tmp_path):
        path = self._write(tmp_path, {
            "clone_profiles": {
                "a": {"net": "n.net", "pcb": "b.kicad_pcb", "channel": "Channel_0",
                      "output": "out.yaml"},
            },
        })
        prof = load_profile(path, "clone_profiles", "a", known_keys=CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
        assert prof["output"] == "out.yaml"
