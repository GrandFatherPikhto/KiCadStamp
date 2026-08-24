#!/usr/bin/env python3
"""Tests for kicadstamp/config_rename.py — propagating a Role/Cluster rename
into the profile YAML files of a profile's include: graph (the same renames:
map schematic_rename_fields.py understands)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml

import fieldstool_cli
from kicadstamp.config_rename import (
    plan_profile_rename_edits,
    write_profile_files,
)
from kicadstamp.exceptions import FieldsToolError
from kicadstamp.schematic_editing import apply_edits
from tests.fieldstool_fixtures import sch_file, symbol_block


def _plan(tmp_path, root_text, include_text=None, renames=None):
    if include_text is not None:
        (tmp_path / "components.yaml").write_text(include_text, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(root_text, encoding="utf-8")
    return plan_profile_rename_edits(root, renames or {})


# ── Library-level: what gets renamed and what does not ───────────────────────

def test_rename_role_only_semantic_fields(tmp_path):
    """Only the semantic role fields change — not a comment, not a net
    template value, not a rule name, not a coordinate_placement identity
    name — even when all of them contain the exact same string."""
    comp = (
        "# OLD_ROLE appears in this comment too\n"
        "cells:\n"
        "  one:\n"
        "    components:\n"
        "    - role: OLD_ROLE\n"
        "      net_template: OLD_ROLE\n"
        "points:\n"
        "  p1:\n"
        "    anchor_role: OLD_ROLE\n"
    )
    root = (
        "include:\n"
        "- components.yaml\n"
        "rules:\n"
        "- net: GND\n"
        "  name: OLD_ROLE\n"
        "  anchor_ref: U1\n"
        "coordinate_placements:\n"
        "- cluster: C\n"
        "  role: OLD_ROLE\n"
        "  name: OLD_ROLE\n"
        "  x_mm: 1.0\n"
        "  y_mm: 1.0\n"
        "  rotation_deg: 0.0\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, comp, {"Role": {"OLD_ROLE": "NEW_ROLE"}})

    new_comp = apply_edits(texts[str(tmp_path / "components.yaml")],
                           edits[str(tmp_path / "components.yaml")])
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])

    # Semantic role fields renamed:
    assert "    - role: NEW_ROLE\n" in new_comp
    assert "    anchor_role: NEW_ROLE\n" in new_comp
    assert "  role: NEW_ROLE\n" in new_root           # coordinate_placements role
    # Non-role occurrences of the SAME string untouched:
    assert "# OLD_ROLE appears in this comment too" in new_comp
    assert "      net_template: OLD_ROLE\n" in new_comp
    # Two `name: OLD_ROLE` lines must survive: the rule name and the
    # coordinate_placements identity name (neither is a role field).
    assert new_root.count("  name: OLD_ROLE\n") == 2
    assert unmatched == []


def test_rename_cluster_and_anchor_cluster_flat(tmp_path):
    root = (
        "rules:\n"
        "- net: GND\n"
        "  anchor_role: U1\n"
        "  anchor_cluster: FPGA\n"
        "  spokes:\n"
        "  - pad: '1'\n"
        "    cluster: FPGA_PWR\n"
        "coordinate_placements:\n"
        "- cluster: FPGA\n"
        "  role: R1\n"
        "  x_mm: 0.0\n"
        "  y_mm: 0.0\n"
        "  rotation_deg: 0.0\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"FPGA": "FPGA_V2",
                                             "FPGA_PWR": "FPGA_PWR_V2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "  anchor_cluster: FPGA_V2\n" in new_root
    assert "    cluster: FPGA_PWR_V2\n" in new_root
    assert "- cluster: FPGA_V2\n" in new_root
    assert "FPGA\n" not in new_root
    assert unmatched == []


def test_rename_refs_key_not_value(tmp_path):
    root = (
        "clone_placements:\n"
        "- name: PIF\n"
        "  cell: one\n"
        "  xy: [0.0, 0.0]\n"
        "  refs: {LDO: \"U5\"}\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"LDO": "LDO_V2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "refs: {LDO_V2: \"U5\"}" in new_root
    assert "\"U5\"" in new_root                       # refdes value untouched
    assert unmatched == []


def test_rename_nets_key(tmp_path):
    root = (
        "clone_placements:\n"
        "- name: PIF\n"
        "  cell: one\n"
        "  xy: [0.0, 0.0]\n"
        "  nets:\n"
        "    C_IN: +3V3\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"C_IN": "C_IN_V2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "    C_IN_V2: +3V3\n" in new_root
    assert "+3V3" in new_root                          # net value untouched
    assert unmatched == []


def test_hierarchical_cluster_not_renamed_exact_match_only(tmp_path):
    """cluster_prefix_match() supports segment prefixes at APPLY time, but the
    rename tool is exact-value only (same as the schematic side): renaming
    'Channel_1' must NOT rewrite 'Channel_1/sub'."""
    root = (
        "rules:\n"
        "- net: GND\n"
        "  anchor_ref: U1\n"
        "  spokes:\n"
        "  - pad: '1'\n"
        "    cluster: Channel_1/sub\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"Channel_1": "Channel_1_v2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "    cluster: Channel_1/sub\n" in new_root
    assert "Channel_1_v2" not in new_root
    assert unmatched == ["Cluster: 'Channel_1'"]


def test_clone_placement_name_renamed_as_cluster_tag(tmp_path):
    """clone_placements[].name IS the Cluster TAG (unlike
    coordinate_placements[].name, which is a save identity) — so a Cluster
    rename rewrites the former and leaves the latter alone."""
    root = (
        "clone_placements:\n"
        "- name: PIF_P5V\n"
        "  cell: one\n"
        "  xy: [0.0, 0.0]\n"
        "coordinate_placements:\n"
        "- cluster: DAC\n"
        "  role: R1\n"
        "  name: channel0_r1\n"
        "  x_mm: 0.0\n"
        "  y_mm: 0.0\n"
        "  rotation_deg: 0.0\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Cluster": {"PIF_P5V": "PIF_P5V_V2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "- name: PIF_P5V_V2\n" in new_root
    assert "  name: channel0_r1\n" in new_root
    assert unmatched == []


def test_net_from_role_and_net_template_same_as_role(tmp_path):
    comp = (
        "cells:\n"
        "  one:\n"
        "    components:\n"
        "    - role: LDO\n"
        "      net_template_same_as_role: C_IN\n"
        "      vias:\n"
        "      - net_from_role: C_OUT\n"
        "    vias:\n"
        "    - net_from_role: LDO\n"
        "    tracks:\n"
        "    - net_from_role: C_IN\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, "include:\n- components.yaml\n", comp,
        {"Role": {"LDO": "LDO_V2", "C_IN": "C_IN_V2", "C_OUT": "C_OUT_V2"}})
    new_comp = apply_edits(texts[str(tmp_path / "components.yaml")],
                           edits[str(tmp_path / "components.yaml")])
    assert "    - role: LDO_V2\n" in new_comp
    assert "      net_template_same_as_role: C_IN_V2\n" in new_comp
    assert "      - net_from_role: C_OUT_V2\n" in new_comp
    assert "    - net_from_role: LDO_V2\n" in new_comp
    assert "    - net_from_role: C_IN_V2\n" in new_comp
    assert unmatched == []


def test_net_traces_anchor_and_vias(tmp_path):
    root = (
        "net_traces:\n"
        "- net: /Chan/DAC\n"
        "  anchor_role: FPGA\n"
        "  anchor_cluster: FPGA\n"
        "  vias:\n"
        "  - net_from_role: LDO\n"
        "  tracks:\n"
        "  - net_from_role: C_IN\n"
        "    layer: F.Cu\n"
    )
    edits, texts, report, unmatched = _plan(
        tmp_path, root, renames={"Role": {"FPGA": "FPGA_V2", "LDO": "LDO_V2"}})
    new_root = apply_edits(texts[str(tmp_path / "root.yaml")],
                           edits[str(tmp_path / "root.yaml")])
    assert "  anchor_role: FPGA_V2\n" in new_root
    assert "  - net_from_role: LDO_V2\n" in new_root
    # anchor_cluster FPGA is a CLUSTER name — Role rename must not touch it:
    assert "  anchor_cluster: FPGA\n" in new_root
    # C_IN is not in the renames map at all, so it stays and nothing is unmatched:
    assert "  - net_from_role: C_IN\n" in new_root
    assert unmatched == []


# ── Include-graph handling ───────────────────────────────────────────────────

def test_other_profiles_not_touched(tmp_path):
    """The rename walks only the include: graph of the given root — a sibling
    YAML that is not included stays untouched (no profiles/**/*.yaml glob)."""
    included = tmp_path / "components.yaml"
    included.write_text("cells:\n  one:\n    components:\n    - role: OLD\n",
                        encoding="utf-8")
    untouched = tmp_path / "other_profile.yaml"
    untouched.write_text("cells:\n  x:\n    components:\n    - role: OLD\n",
                         encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n- components.yaml\n", encoding="utf-8")

    plan_profile_rename_edits(root, {"Role": {"OLD": "NEW"}})
    assert "role: OLD" in untouched.read_text(encoding="utf-8")


def test_include_cycle_is_fatal(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("include:\n- b.yaml\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text("include:\n- a.yaml\n", encoding="utf-8")
    with pytest.raises(FieldsToolError, match="cycle"):
        plan_profile_rename_edits(a, {"Role": {"OLD": "NEW"}})


def test_missing_profile_is_fatal(tmp_path):
    with pytest.raises(FieldsToolError, match="not found"):
        plan_profile_rename_edits(tmp_path / "nope.yaml", {"Role": {"A": "B"}})


# ── Idempotency and write pipeline ───────────────────────────────────────────

def test_idempotent_second_plan_is_noop(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("cells:\n  one:\n    components:\n    - role: NEW\n",
                    encoding="utf-8")
    edits, _, report, unmatched = plan_profile_rename_edits(
        root, {"Role": {"OLD": "NEW"}})
    assert report == []
    assert unmatched == ["Role: 'OLD'"]


def test_write_preserves_comments_and_makes_backup(tmp_path):
    root = tmp_path / "root.yaml"
    original = (
        "# keep this comment\n"
        "cells:\n"
        "  one:\n"
        "    components:\n"
        "    - role: OLD_ROLE   # trailing comment\n"
    )
    root.write_text(original, encoding="utf-8")
    edits, texts, report, _ = plan_profile_rename_edits(
        root, {"Role": {"OLD_ROLE": "NEW_ROLE"}})

    written, failed = write_profile_files(edits, texts)
    assert written == [str(root)]
    assert failed == []

    new_text = root.read_text(encoding="utf-8")
    assert "# keep this comment" in new_text
    assert "# trailing comment" in new_text
    assert "    - role: NEW_ROLE   # trailing comment\n" in new_text
    assert (tmp_path / "root.yaml.bak").exists()
    # The backup holds the original, self-verify already re-parsed the result:
    assert (tmp_path / "root.yaml.bak").read_text(encoding="utf-8") == original


# ── CLI integration (fieldstool_cli.py rename --also-profile) ───────────────

def _make_schematic(tmp_path, role=None):
    (tmp_path / "root.kicad_sch").write_text(
        sch_file(symbol_block(["R1"], role=role)), encoding="utf-8")
    return "root.kicad_sch"


def _write_renames(tmp_path, root_sheet, renames):
    cfg = tmp_path / "renames.yaml"
    cfg.write_text(yaml.safe_dump({"root_sheet": root_sheet, "renames": renames}),
                   encoding="utf-8")
    return cfg


def _write_profile(tmp_path, text):
    profile = tmp_path / "profile.yaml"
    profile.write_text(text, encoding="utf-8")
    return profile


def test_cli_also_profile_dry_run_by_default(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path, role="NEW_ROLE")  # schematic already renamed -> no schematic edits
    profile = _write_profile(tmp_path, "rules:\n- net: GND\n  anchor_role: OLD_ROLE\n")
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"OLD_ROLE": "NEW_ROLE"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile)])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "Dry-run" in out
    assert "OLD_ROLE" in profile.read_text(encoding="utf-8")  # not written


def test_cli_also_profile_write(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path, role="NEW_ROLE")
    profile = _write_profile(tmp_path, "rules:\n- net: GND\n  anchor_role: OLD_ROLE\n")
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"OLD_ROLE": "NEW_ROLE"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile), "--write"])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "Profile files written: 1" in out
    assert "anchor_role: NEW_ROLE" in profile.read_text(encoding="utf-8")


def test_cli_profile_unmatched_warning_printed(tmp_path, monkeypatch, capsys):
    _make_schematic(tmp_path)  # no role at all -> schematic side matches nothing either
    profile = _write_profile(tmp_path, "rules: []\n")
    cfg = _write_renames(tmp_path, "root.kicad_sch", {"Role": {"NEVER_SEEN": "X"}})
    monkeypatch.setattr(sys, "argv", ["fieldstool_cli.py", "rename", str(cfg),
                                      "--also-profile", str(profile)])
    assert fieldstool_cli.main() == 0
    out = capsys.readouterr().out
    assert "[warning] profile" in out
    assert "NEVER_SEEN" in out
    assert "Nothing to change" in out
