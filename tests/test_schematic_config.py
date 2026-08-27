# tests/test_schematic_config.py
"""Tests for kicadstamp/schematic_config.py::load_fields_config() — the shared
config reader for the fieldstool tools (schematic_set_fields.py /
schematic_rename_fields.py). The parallel .sexp config format must load here
too: load_fields_config reads root_sheet from the SAME root config file as
the rest of the pipeline, so a .sexp root must work identically to the
equivalent YAML (2026-08-27, fix from handoff_2026_08_27_sexp_config_fixes.md
§2)."""
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import FieldsToolError
from kicadstamp.schematic_config import load_fields_config

import yaml


def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_fields_config_yaml(tmp_path):
    p = _write(tmp_path, "config.yaml",
               yaml.safe_dump({"root_sheet": "root.kicad_sch",
                               "fields": {"R1": {"Role": "X"}}}))
    root_sheet, entries = load_fields_config(p, "fields")
    assert root_sheet == "root.kicad_sch"
    assert entries == {"R1": {"Role": "X"}}


def test_load_fields_config_sexp(tmp_path):
    """A .sexp root config with root_sheet + section loads the same as the
    equivalent YAML (regression for the missing extension switch)."""
    data = {"root_sheet": "root.kicad_sch",
            "fields": {"R1": {"Role": "X"}, "C1": {"Value": "100n"}}}
    p = _write(tmp_path, "config.sexp", dict_to_sexp(data))

    root_sheet, entries = load_fields_config(p, "fields")

    assert root_sheet == "root.kicad_sch"
    assert entries == data["fields"]


def test_load_fields_config_sexp_renames_section(tmp_path):
    """Same reader drives schematic_rename_fields.py (renames: section) —
    .sexp works there too."""
    data = {"root_sheet": "root.kicad_sch",
            "renames": {"Role": {"old": "A", "new": "B"}}}
    p = _write(tmp_path, "config.sexp", dict_to_sexp(data))
    root_sheet, entries = load_fields_config(p, "renames")
    assert root_sheet == "root.kicad_sch"
    assert entries == data["renames"]


def test_load_fields_config_sexp_missing_root_sheet_is_fatal(tmp_path):
    p = _write(tmp_path, "config.sexp",
               dict_to_sexp({"fields": {"R1": {"Role": "X"}}}))
    with pytest.raises(FieldsToolError, match="root_sheet"):
        load_fields_config(p, "fields")


def test_load_fields_config_sexp_empty_section_is_fatal(tmp_path):
    p = _write(tmp_path, "config.sexp",
               dict_to_sexp({"root_sheet": "root.kicad_sch", "fields": {}}))
    with pytest.raises(FieldsToolError, match="fields"):
        load_fields_config(p, "fields")


def test_load_fields_config_sexp_roundtrip_equivalence(tmp_path):
    """The .sexp text round-trips: safe-loading the equivalent YAML dict and
    feeding the same dict through dict_to_sexp must give identical results —
    the two formats express the same raw dict."""
    data = {"root_sheet": "root.kicad_sch",
            "fields": {"R1": {"Role": "X"}}}
    y = _write(tmp_path, "config.yaml", yaml.safe_dump(data))
    s = _write(tmp_path, "config.sexp", dict_to_sexp(data))

    assert load_fields_config(y, "fields") == load_fields_config(s, "fields")
