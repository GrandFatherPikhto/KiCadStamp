#!/usr/bin/env python3
from pathlib import Path
from unittest.mock import patch

import pytest

from kicadstamp.schematic_editing import apply_edits, check_kicad_not_running, write_files
from tests.fakes.schematic_text import sch_file, symbol_block


def test_apply_edits_replaces_span():
    text = "abcXYZdef"
    out = apply_edits(text, [(3, 6, "123")])
    assert out == "abc123def"


def test_apply_edits_insertion_at_same_point_preserves_order():
    text = "abcdef"
    out = apply_edits(text, [(3, 3, "X"), (3, 3, "Y")])
    assert out == "abcXYdef"


def test_apply_edits_overlapping_raises():
    with pytest.raises(ValueError):
        apply_edits("abcdef", [(0, 3, "X"), (2, 4, "Y")])


def test_check_kicad_not_running_passes_when_no_pids():
    with patch("kicadstamp.schematic_editing.list_kicad_pids", return_value=[]):
        check_kicad_not_running(force=False)  # must not raise


def test_check_kicad_not_running_raises_when_pids_found():
    with patch("kicadstamp.schematic_editing.list_kicad_pids", return_value=[1234]):
        with pytest.raises(RuntimeError, match="1234"):
            check_kicad_not_running(force=False)


def test_check_kicad_not_running_force_overrides():
    with patch("kicadstamp.schematic_editing.list_kicad_pids", return_value=[1234]):
        check_kicad_not_running(force=True)  # must not raise


def test_write_files_backs_up_splices_and_self_verifies(tmp_path):
    path = tmp_path / "f.kicad_sch"
    original = sch_file(symbol_block(["R1"], role="OLD"))
    path.write_text(original, encoding="utf-8")

    from kicadstamp.schematic_blocks import find_property_value_span, iter_symbol_blocks
    block = iter_symbol_blocks(str(path), original)[0]
    span_text = original[block.start:block.end]
    vs, ve = find_property_value_span(span_text, "Role")
    edit = (block.start + vs, block.start + ve, "NEW")

    written, failed = write_files({str(path): [edit]}, {str(path): original})

    assert written == [str(path)]
    assert failed == []
    assert '"Role" "NEW"' in path.read_text(encoding="utf-8")
    bak = Path(str(path) + ".bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original


def test_write_files_restores_backup_on_unparseable_result(tmp_path):
    path = tmp_path / "f.kicad_sch"
    original = sch_file(symbol_block(["R1"], role="OLD"))
    path.write_text(original, encoding="utf-8")

    # An edit that corrupts the S-expression (unbalanced paren) must be
    # caught by the sexpdata self-verify and rolled back.
    bad_edit = (10, 10, "(unbalanced")
    written, failed = write_files({str(path): [bad_edit]}, {str(path): original})

    assert written == []
    assert failed == [str(path)]
    assert path.read_text(encoding="utf-8") == original


def test_write_files_skips_files_with_no_edits(tmp_path):
    path = tmp_path / "f.kicad_sch"
    original = sch_file(symbol_block(["R1"]))
    path.write_text(original, encoding="utf-8")

    written, failed = write_files({str(path): []}, {str(path): original})
    assert written == [] and failed == []
    assert not Path(str(path) + ".bak").exists()
