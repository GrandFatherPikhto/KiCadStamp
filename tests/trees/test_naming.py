#!/usr/bin/env python3
"""Tests for name/--only identity.

ThermalViaArrayConfig.name/ClonePlacement.name — REQUIRED in YAML (the loader
fatals if missing), no fallback to thermal_<pad>/'?'.

See --only in kicadstamp_cli.py."""
from pathlib import Path

import pytest
from kicadstamp.config import (
    ThermalViaArrayConfig,
    thermal_via_array_effective_name, load_config,
)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _cfg(**sections) -> dict:
    """Base config dict (layer/cells always present, like the old YAML MINIMAL);
    each optional section overrides the base default when given."""
    data = {"layer": "B.Cu", "cells": {}}
    for k, v in sections.items():
        if v is not None:
            data[k] = v
    return data


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return p


@pytest.fixture
def format2(monkeypatch):
    """Pin CURRENT_FORMAT to 2 (У3.5 К3, row 13).

    The name-REQUIRED cells' subject IS the format-2 naming contract: under
    format 3 the 2->3 lift MINTS a name on every unnamed record (В36), so
    "no name -> fatal" is simply false there. The cells assert the format-2
    contract — pin, do not rewire."""
    from kicadstamp.config import format_version
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)


class TestEffectiveNameAccessors:
    """thermal_via_array_effective_name — just .name (the loader guarantees it
    is set for anything actually loaded from YAML)."""

    def test_thermal_effective_name_is_plain_name(self):
        tva = ThermalViaArrayConfig(retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal")
        assert thermal_via_array_effective_name(tva) == "fpga_thermal"


def test_thermal_via_array_retired_defaults_false():
    """Constructed directly in Python (bypassing the YAML loader),
    ThermalViaArrayConfig's 'retired' field defaults to False."""
    assert ThermalViaArrayConfig().retired is False


def test_thermal_via_arrays_absent_section_is_an_empty_list(tmp_path):
    """2026-08-02: thermal_via_array (one, always-present field with a
    special retired=True-when-absent sentinel) became thermal_via_arrays (a
    real list, default_factory=list) — an absent section is now simply an
    empty list, same as an absent clone_placements:, no sentinel hack
    needed."""
    config_file = _write(tmp_path, "test.sexp", _cfg())
    cfg, _ = load_config(str(config_file))

    assert cfg.thermal_via_arrays == []


BASE_CONFIG = _cfg(
    thermal_via_arrays=[
        {"anchor_role": "FPGA", "pad": "145", "name": "fpga_thermal"},
    ],
    cells={"cap_pair_standard": {"components": [], "vias": []}},
)


class TestNameLoadedFromConfig:
    """name: actually reaches ThermalViaArrayConfig from the config, not just
    accepted by the dataclass constructor (regression check on loader.py;
    s-expr fixtures since 2026-08-28, core_yaml_removal)."""

    def test_thermal_via_array_name_loaded(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", BASE_CONFIG)
        cfg, _ = load_config(str(config_file))

        assert cfg.thermal_via_arrays[0].name == "fpga_thermal"
        assert thermal_via_array_effective_name(cfg.thermal_via_arrays[0]) == "fpga_thermal"


class TestNameRequired:
    """Without name: — fatal, not a silent fallback/'?'. Every
    thermal_via_arrays entry and every clone_placement (closes an old hole with
    a silent '?')."""

    def test_thermal_via_array_without_name_is_fatal(self, tmp_path, format2):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            thermal_via_arrays=[{"anchor_role": "FPGA", "pad": "145"}]))
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_absent_thermal_via_arrays_section_is_not_fatal(self, tmp_path):
        """Section absent from the config entirely — not the same as "present
        but without name" — nothing is being named here, no error, just an
        empty list (see test_thermal_via_arrays_absent_section_is_an_empty_list
        above)."""
        config_file = _write(tmp_path, "test.sexp", _cfg())
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays == []

    def test_clone_placement_without_name_is_fatal(self, tmp_path):
        """role: is not a ClonePlacement key (it's the old name, pre-2026-08-24
        rename) — dict_to_sexp would fatal at SERIALIZE time, so hand-write the
        s-expr: the rejection is the parser's "unknown key in a record" fatal
        (a ValidationError either way)."""
        config_file = tmp_path / "test.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (layer "B.Cu")\n'
            "  (cells)\n"
            "  (clone_placements\n"
            '    (clone_placement (role "SOMETHING") (xy 0.0 0.0))))\n',
            encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))


class TestThermalViaArraySkip:
    """ThermalViaArrayConfig.skip — orthogonal to retired (default False), the
    inline per-item counterpart of --only/--cluster (see drop_inactive_items in
    kicadstamp_cli.py, added 2026-07-29)."""

    def test_thermal_via_array_skip_true_loaded_from_config(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", _cfg(
            thermal_via_arrays=[{"anchor_role": "FPGA", "pad": "145",
                                 "name": "fpga_thermal", "skip": True}]))
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays[0].skip is True
