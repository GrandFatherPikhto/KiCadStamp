#!/usr/bin/env python3
"""Тесты на загрузку ClonePlacement (config.py, TemplatePlacer)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _write(tmp_path, name: str, placements, with_cell: bool = True) -> Path:
    """Write clone_placements (with an optional empty cell 't') as s-expr and
    return the path."""
    data: dict = {}
    if with_cell:
        data["cells"] = {"t": {"components": []}}
    data["clone_placements"] = placements
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


def test_load_clone_placement_is_a_public_alias():
    """Phase 4.2 — gui/docks/placer.py must use a public entry point, not the
    private _load_clone_placement; the alias lives in kicadstamp.config.__all__."""
    import kicadstamp.config as config

    assert "load_clone_placement" in config.__all__
    cp = config.load_clone_placement({"cluster": "p", "cell": "c", "xy": [0.0, 0.0]})
    assert cp.cluster == "p"
    assert isinstance(cp, config.ClonePlacement)


def test_clone_placements_loaded_with_all_fields(tmp_path):
    config_file = _write(tmp_path, "test.sexp", [
        {"cluster": "dac_channel_2", "cell": "dac_channel",
         "xy": [80.0, 40.0], "rotation_deg": 90.0, "params": {"channel": 2}},
        {"cluster": "mcu_section", "cell": "dac_channel", "xy": [0.0, 0.0],
         "net_overrides": {"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"}},
    ], with_cell=False)

    cfg, _ = load_config(str(config_file))
    assert len(cfg.clone_placements) == 2

    cp1 = cfg.clone_placements[0]
    assert cp1.cluster == "dac_channel_2"
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
    config_file = _write(tmp_path, "skip.sexp", [
        {"cluster": "default_skip", "cell": "t", "xy": [0, 0]},
        {"cluster": "explicitly_skipped", "cell": "t", "xy": [0, 0],
         "skip": True},
    ])
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements[0].skip is False
    assert cfg.clone_placements[1].skip is True


def test_ignore_selection_defaults_false_and_can_be_set_true(tmp_path):
    config_file = _write(tmp_path, "ignore_selection.sexp", [
        {"cluster": "default_selection", "cell": "t", "xy": [0, 0]},
        {"cluster": "ignores_selection", "cell": "t", "xy": [0, 0],
         "ignore_selection": True},
    ])
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements[0].ignore_selection is False
    assert cfg.clone_placements[1].ignore_selection is True


def test_no_clone_placements_gives_empty_list(tmp_path):
    config_file = tmp_path / "test2.sexp"
    config_file.write_text(dict_to_sexp({"cells": {"t": {"components": []}}}),
                           encoding="utf-8")
    cfg, _ = load_config(str(config_file))
    assert cfg.clone_placements == []


def test_polar_clone_placement_loads(tmp_path):
    config_file = _write(tmp_path, "polar.sexp", [
        {"cluster": "polar", "cell": "t", "radius_mm": 5.0, "angle_deg": 37.0},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.radius_mm == 5.0
    assert cp.angle_deg == 37.0
    assert cp.xy == (0.0, 0.0)  # default preserved (optional alternative)


def test_clone_placement_xy_and_polar_together_is_fatal(tmp_path):
    config_file = _write(tmp_path, "both.sexp", [
        {"cluster": "both", "cell": "t", "xy": [1.0, 2.0],
         "radius_mm": 5.0, "angle_deg": 0.0},
    ])
    # Message text consolidated 2026-08-12 (Group 3): the both-modes fatal
    # now comes from the shared _load_mutually_exclusive_position, which
    # renders "both xy and polar (radius_mm/angle_deg) — mutually exclusive".
    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_config(str(config_file))


def test_clone_placement_partial_polar_is_fatal(tmp_path):
    config_file = _write(tmp_path, "partial.sexp", [
        {"cluster": "partial", "cell": "t", "radius_mm": 5.0},
    ])
    with pytest.raises(ValidationError, match="polar mode needs BOTH"):
        load_config(str(config_file))


# ---------- New tests for ClonePlacement fields ----------
def test_anchor_ref_without_origin(tmp_path):
    """Anchor mode: anchor_ref set, origin_x/y become optional shift."""
    config_file = _write(tmp_path, "anchor.sexp", [
        {"cluster": "anchored", "cell": "t", "anchor_ref": "IC1",
         "anchor_pad": "17", "xy": [2.5, 3.7]},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_ref == "IC1"
    assert cp.anchor_pad == "17"
    assert cp.xy == (2.5, 3.7)


def test_anchor_ref_without_origin_uses_default_zero(tmp_path):
    config_file = _write(tmp_path, "anchor_no_origin.sexp", [
        {"cluster": "anchored", "cell": "t", "anchor_ref": "IC1"},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.xy == (0.0, 0.0)


def test_anchor_role_with_anchor_sheet(tmp_path):
    """Anchor by role + sheet narrowing."""
    config_file = _write(tmp_path, "anchor_role.sexp", [
        {"cluster": "by_role", "cell": "t", "anchor_role": "MCU",
         "anchor_sheet": "Channel_0", "anchor_pad": "17"},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_role == "MCU"
    assert cp.anchor_sheet == "Channel_0"
    assert cp.anchor_pad == "17"
    assert cp.xy == (0.0, 0.0)


def test_anchor_cluster(tmp_path):
    """Anchor cluster field."""
    config_file = _write(tmp_path, "anchor_cluster.sexp", [
        {"cluster": "with_cluster", "cell": "t", "anchor_role": "MCU",
         "anchor_cluster": "FPGA_PWR_BANK"},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_cluster == "FPGA_PWR_BANK"


def test_layer_and_mirror(tmp_path):
    """Layer and mirror."""
    config_file = _write(tmp_path, "layer_mirror.sexp", [
        {"cluster": "mirrored", "cell": "t", "xy": [0, 0],
         "layer": "B.Cu", "mirror": True},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.layer == "B.Cu"
    assert cp.mirror is True


def test_nets_and_refs(tmp_path):
    """Explicit nets and refs."""
    config_file = _write(tmp_path, "nets_refs.sexp", [
        {"cluster": "with_nets", "cell": "t", "xy": [0, 0],
         "nets": {"A": "GND", "B": "VCC"}, "refs": {"A": "C1"}},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.nets == {"A": "GND", "B": "VCC"}
    assert cp.refs == {"A": "C1"}


def test_by_selection_flag(tmp_path):
    """Explicit 'by selection' mode."""
    config_file = _write(tmp_path, "by_selection.sexp", [
        {"cluster": "selection_mode", "cell": "t", "xy": [0, 0],
         "by_selection": True},
    ])
    cfg, _ = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.by_selection is True


def test_by_selection_and_nets_conflict_raises(tmp_path):
    """by_selection: true + nets should raise ValidationError (message in English)."""
    config_file = _write(tmp_path, "conflict.sexp", [
        {"cluster": "conflict", "cell": "t", "xy": [0, 0],
         "by_selection": True, "nets": {"A": "GND"}},
    ])
    with pytest.raises(ValidationError, match="by_selection.*true.*nets"):
        load_config(str(config_file))


def test_anchor_ref_and_anchor_role_together_raises(tmp_path):
    """Mutually exclusive anchor_ref and anchor_role."""
    config_file = _write(tmp_path, "both_anchors.sexp", [
        {"cluster": "both_anchors", "cell": "t",
         "anchor_ref": "IC1", "anchor_role": "MCU"},
    ])
    with pytest.raises(ValidationError, match="anchor_ref.*anchor_role"):
        load_config(str(config_file))


def test_anchor_sheet_without_anchor_role_raises(tmp_path):
    """anchor_sheet without anchor_role is invalid."""
    config_file = _write(tmp_path, "sheet_no_role.sexp", [
        {"cluster": "sheet_without_role", "cell": "t",
         "anchor_sheet": "Channel_0"},
    ])
    with pytest.raises(ValidationError, match="anchor_sheet.*anchor_role"):
        load_config(str(config_file))


def test_anchor_pad_without_anchor_ref_or_role_raises(tmp_path):
    """anchor_pad requires anchor_ref or anchor_role."""
    config_file = _write(tmp_path, "pad_no_anchor.sexp", [
        {"cluster": "pad_without_anchor", "cell": "t", "anchor_pad": "17"},
    ])
    with pytest.raises(ValidationError, match="anchor_pad.*anchor_ref.*anchor_role"):
        load_config(str(config_file))


def test_no_anchor_and_no_origin_raises(tmp_path):
    """If no anchor, origin_x/y must be provided."""
    config_file = _write(tmp_path, "no_anchor_no_origin.sexp", [
        {"cluster": "no_anchor_no_origin", "cell": "t"},
    ])
    # Message text is translated (see kicadstamp/i18n.py) — match either
    # locale the project ships (en/ru), not just the raw English msgid.
    with pytest.raises(ValidationError, match="no anchor.*absolute coordinates|нет ни якоря.*абсолютных координат"):
        load_config(str(config_file))


def test_cell_is_required_role_rejected(tmp_path):
    """cell: is MANDATORY since 2026-08-12 (Group 0 consolidation) — the
    role:/cluster: single-component modes migrated 1:1 to coordinate_placements'
    anchor-relative mode. A role:-only clone_placement is rejected up front.
    NOTE: `role` is not a ClonePlacement field, so in s-expr the rejection is
    the PARSER's "unknown key in a record" fatal (the YAML loader's
    check_unknown_keys "unknown fields" message is a loader-only nicety)."""
    config_file = tmp_path / "role_only.sexp"
    config_file.write_text(
        "(kicadstamp-config\n"
        "  (cells\n    (cell \"t\"))\n"
        "  (clone_placements\n"
        '    (clone_placement (cluster "single_role") (role "LED")'
        " (xy 10.0 20.0))))\n",
        encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown key"):
        load_config(str(config_file))


def test_missing_cell_raises(tmp_path):
    config_file = _write(tmp_path, "no_content.sexp", [
        {"cluster": "no_content", "xy": [0, 0]},
    ], with_cell=False)
    with pytest.raises(ValidationError, match="without cell"):
        load_config(str(config_file))


def test_role_field_is_rejected(tmp_path):
    """role: is no longer a ClonePlacement key (2026-08-12, Group 0) — the
    single-component mode it belonged to moved to coordinate_placements. In
    s-expr the rejection is the parser's "unknown key in a record" fatal
    (same protection, different message from the YAML loader)."""
    config_file = tmp_path / "role_field.sexp"
    config_file.write_text(
        "(kicadstamp-config\n"
        "  (cells\n    (cell \"t\"))\n"
        "  (clone_placements\n"
        '    (clone_placement (cluster "both_content") (cell "t")'
        ' (role "LED"))))\n',
        encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown key"):
        load_config(str(config_file))


def test_cluster_field_is_required(tmp_path):
    """cluster: is now REQUIRED (2026-08-24, the old `name:` field renamed) —
    an entry without it is fatal."""
    config_file = _write(tmp_path, "missing_cluster.sexp", [
        {"cell": "t", "xy": [0, 0]},
    ])
    with pytest.raises(ValidationError, match="without cluster"):
        load_config(str(config_file))
