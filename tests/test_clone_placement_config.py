#!/usr/bin/env python3
"""Тесты на загрузку ClonePlacement (config.py, TemplatePlacer)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError

YAML_TEXT = """
cells:
  dac_channel:
    components:
      - role: DAC_IC
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
    vias:
      - offset_along_mm: 1.0
        offset_across_mm: 1.0
        net: "DAC{channel}_DB1"
clone_placements:
  - name: dac_channel_2
    cell: dac_channel
    xy: [80.0, 40.0]
    rotation_deg: 90.0
    params:
      channel: 2
  - name: mcu_section
    cell: dac_channel
    xy: [0.0, 0.0]
    net_overrides:
      "/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"
"""


def test_load_clone_placement_is_a_public_alias():
    """Phase 4.2 — gui/docks/placer.py must use a public entry point, not the
    private _load_clone_placement; the alias lives in kicadstamp.config.__all__."""
    import kicadstamp.config as config

    assert "load_clone_placement" in config.__all__
    cp = config.load_clone_placement({"name": "p", "cell": "c", "xy": [0.0, 0.0]})
    assert cp.name == "p"
    assert isinstance(cp, config.ClonePlacement)


def test_clone_placements_loaded_with_all_fields(tmp_path):
    config_file = tmp_path / "test.yaml"
    config_file.write_text(YAML_TEXT, encoding="utf-8")

    cfg, _ = load_config(str(config_file))
    assert len(cfg.clone_placements) == 2

    cp1 = cfg.clone_placements[0]
    assert cp1.name == "dac_channel_2"
    assert cp1.cell == "dac_channel"
    assert cp1.xy == (80.0, 40.0)
    assert cp1.rotation_deg == 90.0
    assert cp1.params == {"channel": 2}
    assert cp1.nets == {}
    assert cp1.net_overrides == {}
    assert cp1.retired is False

    cp2 = cfg.clone_placements[1]
    assert cp2.rotation_deg == 0.0
    assert cp2.net_overrides == {"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"}


def test_skip_defaults_false_and_can_be_set_true(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: default_skip
    cell: t
    xy: [0, 0]
  - name: explicitly_skipped
    cell: t
    xy: [0, 0]
    skip: true
"""
    config_file = tmp_path / "skip.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements[0].skip is False
    assert cfg.clone_placements[1].skip is True


def test_ignore_selection_defaults_false_and_can_be_set_true(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: default_selection
    cell: t
    xy: [0, 0]
  - name: ignores_selection
    cell: t
    xy: [0, 0]
    ignore_selection: true
"""
    config_file = tmp_path / "ignore_selection.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements[0].ignore_selection is False
    assert cfg.clone_placements[1].ignore_selection is True


def test_no_clone_placements_gives_empty_list(tmp_path):
    config_file = tmp_path / "test2.yaml"
    config_file.write_text("", encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements == []


def test_polar_clone_placement_loads(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: polar
    cell: t
    radius_mm: 5.0
    angle_deg: 37.0
"""
    config_file = tmp_path / "polar.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.radius_mm == 5.0
    assert cp.angle_deg == 37.0
    assert cp.xy == (0.0, 0.0)  # default preserved (optional alternative)


def test_clone_placement_xy_and_polar_together_is_fatal(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: both
    cell: t
    xy: [1.0, 2.0]
    radius_mm: 5.0
    angle_deg: 0.0
"""
    config_file = tmp_path / "both.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    # Message text consolidated 2026-08-12 (Group 3): the both-modes fatal
    # now comes from the shared _load_mutually_exclusive_position, which
    # renders "both xy and polar (radius_mm/angle_deg) — mutually exclusive".
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_config(str(config_file))


def test_clone_placement_partial_polar_is_fatal(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: partial
    cell: t
    radius_mm: 5.0
"""
    config_file = tmp_path / "partial.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="polar mode needs BOTH"):
        load_config(str(config_file))


# ---------- New tests for ClonePlacement fields ----------
def test_anchor_ref_without_origin(tmp_path):
    """Anchor mode: anchor_ref set, origin_x/y become optional shift."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: anchored
    cell: t
    anchor_ref: IC1
    anchor_pad: 17
    xy: [2.5, 3.7]
"""
    config_file = tmp_path / "anchor.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_ref == "IC1"
    assert cp.anchor_pad == "17"
    assert cp.xy == (2.5, 3.7)


def test_anchor_ref_without_origin_uses_default_zero(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: anchored
    cell: t
    anchor_ref: IC1
"""
    config_file = tmp_path / "anchor_no_origin.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.xy == (0.0, 0.0)


def test_anchor_role_with_anchor_sheet(tmp_path):
    """Anchor by role + sheet narrowing."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: by_role
    cell: t
    anchor_role: MCU
    anchor_sheet: Channel_0
    anchor_pad: 17
"""
    config_file = tmp_path / "anchor_role.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_role == "MCU"
    assert cp.anchor_sheet == "Channel_0"
    assert cp.anchor_pad == "17"
    assert cp.xy == (0.0, 0.0)


def test_anchor_cluster(tmp_path):
    """Anchor cluster field."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: with_cluster
    cell: t
    anchor_role: MCU
    anchor_cluster: FPGA_PWR_BANK
"""
    config_file = tmp_path / "anchor_cluster.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_cluster == "FPGA_PWR_BANK"


def test_layer_and_mirror(tmp_path):
    """Layer and mirror."""
    yaml_content = """
cells:
  t:
    layer: F.Cu
    components: []
clone_placements:
  - name: mirrored
    cell: t
    xy: [0, 0]
    layer: B.Cu
    mirror: true
"""
    config_file = tmp_path / "layer_mirror.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.layer == "B.Cu"
    assert cp.mirror is True


def test_nets_and_refs(tmp_path):
    """Explicit nets and refs."""
    yaml_content = """
cells:
  t:
    components:
      - role: A
      - role: B
clone_placements:
  - name: with_nets
    cell: t
    xy: [0, 0]
    nets:
      A: GND
      B: VCC
    refs:
      A: C1
"""
    config_file = tmp_path / "nets_refs.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.nets == {"A": "GND", "B": "VCC"}
    assert cp.refs == {"A": "C1"}


def test_by_selection_flag(tmp_path):
    """Explicit 'by selection' mode."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: selection_mode
    cell: t
    xy: [0, 0]
    by_selection: true
"""
    config_file = tmp_path / "by_selection.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.by_selection is True


def test_by_selection_and_nets_conflict_raises(tmp_path):
    """by_selection: true + nets should raise ValidationError (message in English)."""
    yaml_content = """
cells:
  t:
    components:
      - role: A
clone_placements:
  - name: conflict
    cell: t
    xy: [0, 0]
    by_selection: true
    nets:
      A: GND
"""
    config_file = tmp_path / "conflict.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="by_selection.*true.*nets"):
        load_config(str(config_file))


def test_anchor_ref_and_anchor_role_together_raises(tmp_path):
    """Mutually exclusive anchor_ref and anchor_role."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: both_anchors
    cell: t
    anchor_ref: IC1
    anchor_role: MCU
"""
    config_file = tmp_path / "both_anchors.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_ref.*anchor_role"):
        load_config(str(config_file))


def test_anchor_sheet_without_anchor_role_raises(tmp_path):
    """anchor_sheet without anchor_role is invalid."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: sheet_without_role
    cell: t
    anchor_sheet: Channel_0
"""
    config_file = tmp_path / "sheet_no_role.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_sheet.*anchor_role"):
        load_config(str(config_file))


def test_anchor_pad_without_anchor_ref_or_role_raises(tmp_path):
    """anchor_pad requires anchor_ref or anchor_role."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: pad_without_anchor
    cell: t
    anchor_pad: 17
"""
    config_file = tmp_path / "pad_no_anchor.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_pad.*anchor_ref.*anchor_role"):
        load_config(str(config_file))


def test_no_anchor_and_no_origin_raises(tmp_path):
    """If no anchor, origin_x/y must be provided."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: no_anchor_no_origin
    cell: t
"""
    config_file = tmp_path / "no_anchor_no_origin.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    # Message text is translated (see kicadstamp/i18n.py) — match either
    # locale the project ships (en/ru), not just the raw English msgid.
    with pytest.raises(ValidationError, match="no anchor.*absolute coordinates|нет ни якоря.*абсолютных координат"):
        load_config(str(config_file))


def test_role_without_cell(tmp_path):
    """Single-component placement using 'role' field instead of cell."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: single_role
    role: LED
    xy: [10.0, 20.0]
"""
    config_file = tmp_path / "role_only.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.role == "LED"
    assert cp.cell is None
    assert cp.xy == (10.0, 20.0)


def test_cluster_without_cell_or_role(tmp_path):
    """Single-component placement identified by an existing Cluster tag
    (2026-08-06) instead of cell or role."""
    yaml_content = """
clone_placements:
  - name: single_cluster
    cluster: CH2_BYPASS
    xy: [10.0, 20.0]
"""
    config_file = tmp_path / "cluster_only.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.cluster == "CH2_BYPASS"
    assert cp.cell is None
    assert cp.role is None


def test_cell_and_cluster_together_raises(tmp_path):
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: both
    cell: t
    cluster: CH2_BYPASS
    xy: [0, 0]
"""
    config_file = tmp_path / "cell_cluster.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="cell.*role.*cluster|cell/role/cluster"):
        load_config(str(config_file))


def test_role_and_cluster_together_raises(tmp_path):
    yaml_content = """
clone_placements:
  - name: both
    role: LED
    cluster: CH2_BYPASS
    xy: [0, 0]
"""
    config_file = tmp_path / "role_cluster.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="cell.*role.*cluster|cell/role/cluster"):
        load_config(str(config_file))


def test_cluster_with_nets_raises(tmp_path):
    """cluster: is an exact, unconditional field match — nets/params/
    by_selection (role-resolution mode selectors) have no meaning on top
    of it."""
    yaml_content = """
clone_placements:
  - name: single_cluster
    cluster: CH2_BYPASS
    xy: [0, 0]
    nets:
      SOME_ROLE: "+3V3"
"""
    config_file = tmp_path / "cluster_nets.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="cluster together with nets"):
        load_config(str(config_file))


def test_cluster_with_by_selection_raises(tmp_path):
    yaml_content = """
clone_placements:
  - name: single_cluster
    cluster: CH2_BYPASS
    xy: [0, 0]
    by_selection: true
"""
    config_file = tmp_path / "cluster_by_selection.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="cluster together with nets"):
        load_config(str(config_file))


def test_cell_and_role_together_raises(tmp_path):
    """cell and role are mutually exclusive."""
    yaml_content = """
cells:
  t:
    components: []
clone_placements:
  - name: both_content
    cell: t
    role: LED
"""
    config_file = tmp_path / "both_content.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="cell.*role"):
        load_config(str(config_file))


def test_neither_cell_nor_role_raises(tmp_path):
    """At least one of cell/role/cluster is required."""
    yaml_content = """
clone_placements:
  - name: no_content
    xy: [0, 0]
"""
    config_file = tmp_path / "no_content.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="neither cell, role, nor cluster"):
        load_config(str(config_file))