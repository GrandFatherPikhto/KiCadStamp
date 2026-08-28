# tests/test_schematic_config.py
"""Tests for kicadstamp/schematic_config.py::load_fields_config() — the shared
config reader for the fieldstool tools (schematic_set_fields.py /
schematic_rename_fields.py). The config format is s-expr only (2026-08-28,
yaml_removal_tooling — closes the fieldstool open question left by the CORE
plan): load_fields_config reads root_sheet from the SAME root config file as
the rest of the pipeline, and a .yaml config is now a fatal, never a silent
YAML load."""
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import FieldsToolError
from kicadstamp.schematic_config import load_fields_config


def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_fields_config_yaml_is_fatal(tmp_path):
    """A .yaml fieldstool config is no longer a format the tool reads: it is a
    fatal with the sexp_config_convert hint (2026-08-28, yaml_removal_tooling)
    — same escalation as the CORE plan, wrapped in this function's FieldsToolError
    contract."""
    p = _write(tmp_path, "config.yaml",
               "root_sheet: root.kicad_sch\nfields:\n  R1:\n    Role: X\n")
    with pytest.raises(FieldsToolError, match="YAML config support has been removed"):
        load_fields_config(p, "fields")


def test_load_fields_config_sexp(tmp_path):
    """A .sexp root config with root_sheet + section loads correctly."""
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


def test_load_fields_config_sexp_unknown_extension_is_fatal(tmp_path):
    """An unsupported extension (e.g. a typo) is fatal too — the reader is
    s-expr-only, no silent YAML/other fallback."""
    p = _write(tmp_path, "config.sepx",
               "(kicadstamp-config (root_sheet \"root.kicad_sch\"))")
    with pytest.raises(FieldsToolError, match="unrecognized config file extension"):
        load_fields_config(p, "fields")
