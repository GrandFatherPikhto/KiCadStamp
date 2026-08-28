#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config.sexp_format import dict_to_sexp

from kicadstamp.exceptions import FieldsToolError
from kicadstamp.schematic_set_fields import (plan_ensure_fields_for_root, plan_set_edits,
                                             plan_set_edits_for_root)
from tests.fieldstool_fixtures import sch_file, symbol_block


def _write_config(tmp_path, root_sheet, fields):
    config = tmp_path / "config.sexp"
    config.write_text(dict_to_sexp({"root_sheet": root_sheet, "fields": fields}), encoding="utf-8")
    return config


def test_plan_set_replaces_existing_value(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="OLD")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R1": {"Role": "NEW"}})

    edits_by_file, file_texts, report = plan_set_edits(config)

    assert len(report) == 1
    assert report[0].kind == "replace" and report[0].old_value == "OLD" and report[0].new_value == "NEW"
    assert len(edits_by_file[str(root)]) == 1


def test_plan_set_inserts_new_property(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R1": {"Role": "NEW"}})

    edits_by_file, file_texts, report = plan_set_edits(config)

    assert len(report) == 1 and report[0].kind == "insert" and report[0].old_value is None


def test_plan_set_already_correct_is_a_noop(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="SAME")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R1": {"Role": "SAME"}})

    edits_by_file, file_texts, report = plan_set_edits(config)
    assert report == []


def test_plan_set_multi_unit_edits_both_blocks(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="A"), symbol_block(["U1"], role="A")),
                     encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"U1": {"Role": "B"}})

    edits_by_file, file_texts, report = plan_set_edits(config)
    assert len(report) == 2


def test_plan_set_missing_refdes_is_fatal(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R99": {"Role": "X"}})

    with pytest.raises(FieldsToolError, match="R99"):
        plan_set_edits(config)


def test_plan_set_multi_instance_conflict_is_fatal(tmp_path):
    """Two refdes sharing one (symbol ...) block (multi-instance sheet)
    asking for different values — the format can't express that."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50"], role="SAME")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R41": {"Role": "A"}, "R50": {"Role": "B"}})

    with pytest.raises(FieldsToolError, match="conflict"):
        plan_set_edits(config)


def test_plan_set_multi_instance_same_value_is_not_a_conflict(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50"], role="OLD")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"R41": {"Role": "NEW"}, "R50": {"Role": "NEW"}})

    edits_by_file, file_texts, report = plan_set_edits(config)
    assert len(report) == 1
    assert set(report[0].refs) == {"R41", "R50"}


def test_plan_set_edits_for_root_matches_config_based_planning(tmp_path):
    """The in-memory entry point gui/fieldstool_window.py's Apply will use
    — same planning as plan_set_edits(), no YAML file involved."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="OLD")), encoding="utf-8")

    edits_by_file, file_texts, report = plan_set_edits_for_root(str(root), {"R1": {"Role": "NEW"}})

    assert len(report) == 1
    assert report[0].old_value == "OLD" and report[0].new_value == "NEW"


def test_plan_set_missing_root_sheet_key_is_fatal(tmp_path):
    config = tmp_path / "config.sexp"
    config.write_text(dict_to_sexp({"fields": {"R1": {"Role": "X"}}}), encoding="utf-8")
    with pytest.raises(FieldsToolError, match="root_sheet"):
        plan_set_edits(config)


# ── plan_ensure_fields_for_root (2026-08-04: FB3 had Role but no Cluster
# property at all — fills a structural gap, never overwrites a value) ──────

def test_plan_ensure_fields_inserts_a_wholly_missing_field(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["FB3"], role="PI_FILTER_FB")), encoding="utf-8")

    edits_by_file, file_texts, report = plan_ensure_fields_for_root(str(root), ["Role", "Cluster"])

    assert len(report) == 1  # Role already present — only Cluster is missing
    assert report[0].field == "Cluster" and report[0].kind == "insert"
    assert report[0].old_value is None and report[0].new_value == ""


def test_plan_ensure_fields_never_touches_an_existing_value(tmp_path):
    """Unlike plan_set_edits_for_root, a present-but-different (or even
    present-but-empty) value must never be overwritten — this only ever
    fills a field that's entirely absent."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        sch_file(symbol_block(["FB1"], role="PI_FLT_FB", cluster="Pi_Filter_P5V")),
        encoding="utf-8")

    edits_by_file, file_texts, report = plan_ensure_fields_for_root(str(root), ["Role", "Cluster"])

    assert report == []


def test_plan_ensure_fields_is_a_noop_when_every_field_already_present(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["FB4"], role="", cluster="")), encoding="utf-8")

    edits_by_file, file_texts, report = plan_ensure_fields_for_root(str(root), ["Role", "Cluster"])

    assert report == []


def test_plan_ensure_fields_fills_each_block_of_a_multi_unit_symbol_independently(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        sch_file(symbol_block(["U1"], role="A"), symbol_block(["U1"])),  # 2nd block: no fields at all
        encoding="utf-8")

    edits_by_file, file_texts, report = plan_ensure_fields_for_root(str(root), ["Role", "Cluster"])

    # 1st block: only Cluster missing. 2nd block: both Role and Cluster missing.
    assert len(report) == 3
    assert sorted(r.field for r in report) == ["Cluster", "Cluster", "Role"]
