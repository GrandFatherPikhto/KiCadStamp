#!/usr/bin/env python3
"""Tests for kicadstamp/config_rename.py — propagating a Role/Cluster rename
into the profile config files of a profile's include: graph (the same renames:
map schematic_rename_fields.py understands). Config format is s-expr (2026-08-28,
yaml_removal_tooling): plan_profile_rename_edits mutates dicts and returns
{path: mutated_data} instead of byte-offset Edits — there are no comments in
.sexp to preserve, so no text splicing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import fieldstool_cli
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_rename import (
    plan_profile_rename_edits,
    write_profile_files,
)
from kicadstamp.exceptions import FieldsToolError
from tests.fieldstool_fixtures import sch_file, symbol_block


def _write(path, data):
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _plan(tmp_path, root_data, include_data=None, renames=None):
    if include_data is not None:
        _write(tmp_path / "components.sexp", include_data)
    root = tmp_path / "root.sexp"
    _write(root, root_data)
    return plan_profile_rename_edits(root, renames or {})


# ── Library-level: what gets renamed and what does not ───────────────────────

def test_rename_role_only_semantic_fields(tmp_path):
    """Only the semantic role fields change — not a net template value, not a
    rule name, not a coordinate_placement identity name — even when all of
    them contain the exact same string. (.sexp has no comments to preserve.)"""
    comp = {
        "cells": {
            "one": {
                "components": [
                    {"role": "OLD_ROLE", "net_template": "OLD_ROLE"},
                ],
            },
        },
        "points": {"p1": {"anchor_role": "OLD_ROLE"}},
    }
    root = {
        "include": ["components.sexp"],
        "rules": [
            {"net": "GND", "name": "OLD_ROLE", "anchor_ref": "U1"},
        ],
        "coordinate_placements": [
            {"cluster": "C", "role": "OLD_ROLE", "name": "OLD_ROLE",
             "x_mm": 1.0, "y_mm": 1.0, "rotation_deg": 0.0},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, comp, {"Role": {"OLD_ROLE": "NEW_ROLE"}})

    comp_data = mutated[str(tmp_path / "components.sexp")]
    root_data = mutated[str(tmp_path / "root.sexp")]
    # Semantic role fields renamed:
    assert comp_data["cells"]["one"]["components"][0]["role"] == "NEW_ROLE"
    assert comp_data["points"]["p1"]["anchor_role"] == "NEW_ROLE"
    assert root_data["coordinate_placements"][0]["role"] == "NEW_ROLE"
    # Non-role occurrences of the SAME string untouched:
    assert comp_data["cells"]["one"]["components"][0]["net_template"] == "OLD_ROLE"
    # Two `name: OLD_ROLE` must survive: the rule name and the coordinate
    # placement identity name (neither is a role field).
    assert root_data["rules"][0]["name"] == "OLD_ROLE"
    assert root_data["coordinate_placements"][0]["name"] == "OLD_ROLE"
    assert unmatched == []


def test_rename_cluster_and_anchor_cluster_flat(tmp_path):
    root = {
        "rules": [
            {"net": "GND", "anchor_role": "U1", "anchor_cluster": "FPGA",
             "spokes": [{"pad": "1", "cluster": "FPGA_PWR"}]},
        ],
        "coordinate_placements": [
            {"cluster": "FPGA", "role": "R1", "x_mm": 0.0, "y_mm": 0.0,
             "rotation_deg": 0.0},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"FPGA": "FPGA_V2",
                                             "FPGA_PWR": "FPGA_PWR_V2"}})
    def _values(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from _values(v)
        elif isinstance(obj, list):
            for x in obj:
                yield from _values(x)
        else:
            yield obj

    root_data = mutated[str(tmp_path / "root.sexp")]
    assert root_data["rules"][0]["anchor_cluster"] == "FPGA_V2"
    assert root_data["rules"][0]["spokes"][0]["cluster"] == "FPGA_PWR_V2"
    assert root_data["coordinate_placements"][0]["cluster"] == "FPGA_V2"
    # no exact "FPGA" value remains anywhere
    assert "FPGA" not in list(_values(root_data))
    assert unmatched == []


def test_rename_refs_key_not_value(tmp_path):
    root = {
        "clone_placements": [
            {"cluster": "PIF", "cell": "one", "xy": [0.0, 0.0],
             "refs": {"LDO": "U5"}},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"LDO": "LDO_V2"}})
    refs = mutated[str(tmp_path / "root.sexp")]["clone_placements"][0]["refs"]
    assert refs == {"LDO_V2": "U5"}  # refdes value untouched
    assert unmatched == []


def test_rename_nets_key(tmp_path):
    root = {
        "clone_placements": [
            {"cluster": "PIF", "cell": "one", "xy": [0.0, 0.0],
             "nets": {"C_IN": "+3V3"}},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"C_IN": "C_IN_V2"}})
    nets = mutated[str(tmp_path / "root.sexp")]["clone_placements"][0]["nets"]
    assert nets == {"C_IN_V2": "+3V3"}  # net value untouched
    assert unmatched == []


def test_hierarchical_cluster_not_renamed_exact_match_only(tmp_path):
    """cluster_prefix_match() supports segment prefixes at APPLY time, but the
    rename tool is exact-value only (same as the schematic side): renaming
    'Channel_1' must NOT rewrite 'Channel_1/sub'."""
    root = {
        "rules": [
            {"net": "GND", "anchor_ref": "U1",
             "spokes": [{"pad": "1", "cluster": "Channel_1/sub"}]},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"Channel_1": "Channel_1_v2"}})
    # no change at all -> the file is NOT in mutated_by_file
    assert mutated == {}
    assert unmatched == ["Cluster: 'Channel_1'"]


def test_clone_placement_cluster_renamed_name_not(tmp_path):
    """clone_placements[].cluster IS the Cluster TAG (2026-08-24 split, like
    coordinate_placements[].cluster); its `name` is the save/--only identity
    and must NOT be rewritten by a Cluster rename."""
    root = {
        "clone_placements": [
            {"cluster": "PIF_P5V", "name": "ch0_pif", "cell": "one",
             "xy": [0.0, 0.0]},
        ],
        "coordinate_placements": [
            {"cluster": "DAC", "role": "R1", "name": "channel0_r1",
             "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"PIF_P5V": "PIF_P5V_V2"}})
    root_data = mutated[str(tmp_path / "root.sexp")]
    assert root_data["clone_placements"][0]["cluster"] == "PIF_P5V_V2"
    assert root_data["clone_placements"][0]["name"] == "ch0_pif"  # identity untouched
    assert root_data["coordinate_placements"][0]["name"] == "channel0_r1"
    assert unmatched == []


def test_net_from_role_and_net_template_same_as_role(tmp_path):
    comp = {
        "cells": {
            "one": {
                "components": [
                    {"role": "LDO", "net_template_same_as_role": "C_IN",
                     "vias": [{"net_from_role": "C_OUT"}]},
                ],
                "vias": [{"net_from_role": "LDO"}],
                "tracks": [{"net_from_role": "C_IN"}],
            },
        },
    }
    mutated, report, unmatched = _plan(
        tmp_path, {"include": ["components.sexp"]}, comp,
        {"Role": {"LDO": "LDO_V2", "C_IN": "C_IN_V2", "C_OUT": "C_OUT_V2"}})
    comp_data = mutated[str(tmp_path / "components.sexp")]
    cell = comp_data["cells"]["one"]
    assert cell["components"][0]["role"] == "LDO_V2"
    assert cell["components"][0]["net_template_same_as_role"] == "C_IN_V2"
    assert cell["components"][0]["vias"][0]["net_from_role"] == "C_OUT_V2"
    assert cell["vias"][0]["net_from_role"] == "LDO_V2"
    assert cell["tracks"][0]["net_from_role"] == "C_IN_V2"
    assert unmatched == []


def test_net_traces_anchor_and_vias(tmp_path):
    root = {
        "net_traces": [
            {"net": "/Chan/DAC", "anchor_role": "FPGA", "anchor_cluster": "FPGA",
             "vias": [{"net_from_role": "LDO"}],
             "tracks": [{"net_from_role": "C_IN", "layer": "F.Cu"}]},
        ],
    }
    mutated, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"FPGA": "FPGA_V2", "LDO": "LDO_V2"}})
    nt = mutated[str(tmp_path / "root.sexp")]["net_traces"][0]
    assert nt["anchor_role"] == "FPGA_V2"
    assert nt["vias"][0]["net_from_role"] == "LDO_V2"
    # anchor_cluster FPGA is a CLUSTER name — Role rename must not touch it:
    assert nt["anchor_cluster"] == "FPGA"
    # C_IN is not in the renames map at all, so it stays and nothing is unmatched:
    assert nt["tracks"][0]["net_from_role"] == "C_IN"
    assert unmatched == []


# ── Include-graph handling ───────────────────────────────────────────────────

def test_other_profiles_not_touched(tmp_path):
    """The rename walks only the include: graph of the given root — a sibling
    config that is not included stays untouched (no profiles/**/*.sexp glob)."""
    included = tmp_path / "components.sexp"
    _write(included, {"cells": {"one": {"components": [{"role": "OLD"}]}}})
    untouched = tmp_path / "other_profile.sexp"
    _write(untouched, {"cells": {"x": {"components": [{"role": "OLD"}]}}})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["components.sexp"]})

    mutated, _report, _unmatched = plan_profile_rename_edits(
        root, {"Role": {"OLD": "NEW"}})
    assert str(untouched) not in mutated
    # untouched file on disk is byte-identical
    assert untouched.read_text(encoding="utf-8") == \
        dict_to_sexp({"cells": {"x": {"components": [{"role": "OLD"}]}}})


def test_include_cycle_is_fatal(tmp_path):
    a = tmp_path / "a.sexp"
    _write(a, {"include": ["b.sexp"]})
    b = tmp_path / "b.sexp"
    _write(b, {"include": ["a.sexp"]})
    with pytest.raises(FieldsToolError, match="cycle"):
        plan_profile_rename_edits(a, {"Role": {"OLD": "NEW"}})


def test_missing_profile_is_fatal(tmp_path):
    with pytest.raises(FieldsToolError, match="not found"):
        plan_profile_rename_edits(tmp_path / "nope.sexp", {"Role": {"A": "B"}})


# ── Idempotency and write pipeline ───────────────────────────────────────────

def test_idempotent_second_plan_is_noop(tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {"one": {"components": [{"role": "NEW"}]}}})
    mutated, report, unmatched = plan_profile_rename_edits(
        root, {"Role": {"OLD": "NEW"}})
    assert mutated == {}
    assert report == []
    assert unmatched == ["Role: 'OLD'"]


def test_write_makes_backup_and_self_verifies(tmp_path):
    """The write pipeline (.sexp): a .bak is created BEFORE the write, the
    dict is written via dict_to_sexp and re-parsed (sexp_to_dict) as a
    self-verify. There are no comments in .sexp to preserve (2026-08-28,
    yaml_removal_tooling), so that YAML-era property is simply gone."""
    root = tmp_path / "root.sexp"
    original = {"cells": {"one": {"components": [{"role": "OLD_ROLE"}]}}}
    _write(root, original)
    mutated, _report, _ = plan_profile_rename_edits(
        root, {"Role": {"OLD_ROLE": "NEW_ROLE"}})

    written, failed = write_profile_files(mutated)
    assert written == [str(root)]
    assert failed == []

    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert data["cells"]["one"]["components"][0]["role"] == "NEW_ROLE"
    assert (tmp_path / "root.sexp.bak").exists()
    # The backup holds the original parsed dict (re-parses cleanly).
    assert sexp_to_dict((tmp_path / "root.sexp.bak").read_text(encoding="utf-8")) == original


# ── CLI integration (fieldstool_cli.py rename --also-profile) ───────────────

def _make_schematic(tmp_path, role=None):
    (tmp_path / "root.kicad_sch").write_text(
        sch_file(symbol_block(["R1"], role=role)), encoding="utf-8")
    return "root.kicad_sch"


def _write_renames(tmp_path, root_sheet, renames):
    cfg = tmp_path / "renames.sexp"
    _write(cfg, {"root_sheet": root_sheet, "renames": renames})
    return cfg


def _write_profile(tmp_path, data):
    profile = tmp_path / "profile.sexp"
    _write(profile, data)
    return profile


def test_cli_also_profile_dry_run_by_default(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path, role="NEW_ROLE")  # schematic already renamed -> no schematic edits
    profile = _write_profile(tmp_path, {"rules": [{"net": "GND", "anchor_role": "OLD_ROLE"}]})
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"OLD_ROLE": "NEW_ROLE"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile)])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "Dry-run" in out
    # not written: file still has the old role
    assert "OLD_ROLE" in profile.read_text(encoding="utf-8")


def test_cli_also_profile_write(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path, role="NEW_ROLE")
    profile = _write_profile(tmp_path, {"rules": [{"net": "GND", "anchor_role": "OLD_ROLE"}]})
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"OLD_ROLE": "NEW_ROLE"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile), "--write"])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "Profile files written: 1" in out
    assert "NEW_ROLE" in profile.read_text(encoding="utf-8")
    assert "OLD_ROLE" not in profile.read_text(encoding="utf-8")


def test_cli_profile_unmatched_warning_printed(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path)  # no role at all -> schematic side matches nothing either
    profile = _write_profile(tmp_path, {"rules": []})
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"NEVER_SEEN": "X"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile)])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "[warning] profile" in out
    assert "NEVER_SEEN" in out
    assert "Nothing to change" in out
