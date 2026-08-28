#!/usr/bin/env python3
"""Tests for the deprecated cells_file:/cell_files: keys (kicadstamp/config/loader.py) —
both were folded into include: 2026-08-02 (see
handoff_2026_08_02_cells_include_unification.md): an external Cell file is now just
another file listed under include:, wrapped in a cells: key, same mechanism/shape as
rules:/clone_placements:/etc. (see test_config_includes.py for that mechanism's own
tests, including cells: merging across multiple included files).

Renamed from templates_file/template_files 2026-08-01 (the class became Cell, was
SpokeTemplate — these were the one file-list key pair left behind, see
techdocs/handoff/handoff_2026_08_01_metalanguage_p2_p3.md), and themselves removed in
favour of include: 2026-08-02 — this file now only covers the fatal-with-rename-hint
behaviour for all three now-dead key names."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def test_old_templates_file_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"templates_file": "ext.yaml"}), encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*templates_file"):
        load_config(str(root))


def test_old_template_files_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"template_files": ["ext.yaml"]}), encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*template_files"):
        load_config(str(root))


def test_old_cells_file_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells_file": "ext.yaml"}), encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*cells_file"):
        load_config(str(root))


def test_old_cell_files_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cell_files": ["ext.yaml"]}), encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*cell_files"):
        load_config(str(root))
