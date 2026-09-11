#!/usr/bin/env python3
"""Write-safety tests for the one-way config writers (task В.1 of
plan_2026_09_11_tree_instances_and_converter_safety).

The bug being pinned: `open(target, "w")` TRUNCATES the target before the new
content is even computed, so any serialization error left a 0-byte config on
disk. Measured live in `convert_config_file` (in-place mode, the mode the CLI
recommends): a node still carrying the removed `pivot_xy` made `dict_to_sexp`
refuse AFTER the truncation — 252321 bytes in, 0 bytes out, reproduced twice.

Both writers (`convert_config_file`, `flatten_config`) now serialize to a
STRING and re-parse it with the NORMAL reader BEFORE touching the target, back
up an in-place target, and write atomically (temp file + os.replace).

`tests/test_tree_mount_conversion.py` (the Б1 acceptance set) and
`tests/fixtures/trees_and_overlay/expected_geometry.json` are FROZEN and are
only ever READ here — never edited.
"""
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.flatten import flatten_config
from kicadstamp.tree_mount_convert import convert_config_file
from kicadstamp.utils.safe_write import backup_file, write_text_atomic

FIXTURES = Path(__file__).parent / "fixtures" / "trees_and_overlay"

# A tree node carrying the REMOVED per-node `pivot-xy`, which the normal reader
# refuses (`trees._dict_node` leftover fatal -> tree_from_dict -> dict_to_sexp).
# `_move_pivots` only ever looks at kind "module" nodes, so a placement node
# keeps its pivot and the SERIALIZER is what fails — exactly the original bug.
_BROKEN_CONFIG = """(kicadstamp-config
  (trees
    (tree
      (name "t")
      (anchor (origin))
      (node (ref "E1") (kind placement) (xy 0 0) (pivot-xy 1 2))
    )
  )
)
"""


def _backups(dir_path: Path) -> list[Path]:
    return sorted(dir_path.glob("*.bak.*"))


# ── the converter (В.1) ────────────────────────────────────────────────────

def test_a_serialization_failure_leaves_the_source_untouched(tmp_path):
    """The regression that destroyed a real profile: serialization refuses the
    converted dict (leftover pivot-xy on a node), the source must stay
    byte-for-byte intact and NOTHING may be created next to it."""
    source = tmp_path / "config.sexp"
    source.write_text(_BROKEN_CONFIG, encoding="utf-8")
    before = source.read_bytes()

    with pytest.raises(ValidationError):
        convert_config_file(root=str(source))

    assert source.read_bytes() == before
    assert _backups(tmp_path) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.sexp"]


def test_the_output_is_readable_by_the_normal_reader(tmp_path):
    """§В.1.2 step 2: the self-verify uses sexp_to_dict WITHOUT raw_trees=True,
    so a converter output the standard loader refuses fails before the write."""
    out = tmp_path / "converted.sexp"
    convert_config_file(root=str(FIXTURES / "config.sexp"), output=str(out))
    parsed = sexp_to_dict(out.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict) and "trees" in parsed


def test_in_place_writes_a_backup_but_output_does_not(tmp_path):
    in_place = tmp_path / "in_place.sexp"
    in_place.write_bytes((FIXTURES / "config.sexp").read_bytes())
    original = in_place.read_bytes()

    convert_config_file(root=str(in_place))

    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original      # the recovery point
    assert in_place.read_bytes() != original        # actually converted

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    root = out_dir / "root.sexp"
    root.write_bytes((FIXTURES / "config.sexp").read_bytes())
    root_before = root.read_bytes()
    convert_config_file(root=str(root), output=str(out_dir / "flat.sexp"))
    assert root.read_bytes() == root_before
    assert _backups(out_dir) == []                  # --output needs no backup


def test_a_successful_conversion_still_yields_the_same_config(tmp_path):
    """Regression on the Б1 acceptance fixture (READ only): the rewritten write
    path must not change WHAT is converted."""
    out = tmp_path / "converted.sexp"
    convert_config_file(root=str(FIXTURES / "config.sexp"), output=str(out))
    produced = sexp_to_dict(out.read_text(encoding="utf-8"))
    committed = sexp_to_dict(
        (FIXTURES / "config.converted.sexp").read_text(encoding="utf-8"))
    assert produced == committed


# ── flatten (В.1.3 — the same hole, checked and fixed) ─────────────────────

def _minimal_project(tmp_path: Path) -> Path:
    (tmp_path / "cells.sexp").write_text(
        dict_to_sexp({"cells": {"c": {"components": []}}}), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["cells.sexp"], "layer": "B.Cu"}),
                    encoding="utf-8")
    return root


def test_flatten_in_place_writes_a_backup_and_output_does_not(tmp_path):
    root = _minimal_project(tmp_path)
    original = root.read_bytes()

    flatten_config(root=str(root))

    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original

    second = tmp_path / "second"
    second.mkdir()
    root2 = _minimal_project(second)
    before = root2.read_bytes()
    flatten_config(root=str(root2), output=str(second / "flat.sexp"))
    assert root2.read_bytes() == before
    assert _backups(second) == []


def test_flatten_serialization_failure_leaves_the_root_untouched(tmp_path,
                                                                 monkeypatch):
    root = _minimal_project(tmp_path)
    before = root.read_bytes()

    def _boom(_data):
        raise ValidationError("serialization refused")

    monkeypatch.setattr("kicadstamp.flatten.dict_to_sexp", _boom)
    with pytest.raises(ValidationError):
        flatten_config(root=str(root))

    assert root.read_bytes() == before
    assert _backups(tmp_path) == []


# ── the helpers themselves ─────────────────────────────────────────────────

def test_write_text_atomic_replaces_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "x.sexp"
    write_text_atomic(target, "first\n")
    assert target.read_text(encoding="utf-8") == "first\n"
    write_text_atomic(target, "second\n")
    assert target.read_text(encoding="utf-8") == "second\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.sexp"]


def test_write_text_atomic_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "x.sexp"
    write_text_atomic(target, "hi\n")
    assert target.read_text(encoding="utf-8") == "hi\n"


def test_backup_file_never_clobbers_an_earlier_backup(tmp_path):
    target = tmp_path / "x.sexp"
    target.write_text("one\n", encoding="utf-8")
    first = backup_file(target)
    target.write_text("two\n", encoding="utf-8")
    second = backup_file(target)
    assert first != second
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
