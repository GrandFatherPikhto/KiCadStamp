#!/usr/bin/env python3
"""Tests for the opt-in "config targets board X, but board Y is open in KiCad"
guard (2026-08-20): Config.board_name + validation.check_board_identity() +
KiCadBoardAdapter.get_board_filename(). See the plan
techdocs/handoff/deepseek/plan_2026_08_20_board_identity_check.md — the real
incident where a stale schematic_dir pointed at a previous board revision and
the mismatch surfaced as an unrelated-looking fatal deep in Extract."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadstamp.config import Config, load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.validation import (
    _path_basename_stem,
    check_board_identity,
    run_all_checks,
)


def _adapter(live: str | None) -> MagicMock:
    adapter = MagicMock()
    adapter.get_board_filename.return_value = live
    return adapter


class TestConfigBoardNameLoad:
    def test_board_name_is_read_from_sexp(self, tmp_path):
        config_file = tmp_path / "test.sexp"
        config_file.write_text(dict_to_sexp({
            "board_name": "3CH-AWG-TIA-v102", "cells": {}}), encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.board_name == "3CH-AWG-TIA-v102"

    def test_board_name_absent_defaults_to_none(self, tmp_path):
        config_file = tmp_path / "test.sexp"
        config_file.write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.board_name is None

    def test_unknown_keys_validation_still_passes_with_board_name(self, tmp_path):
        """board_name is a root-level key; a typo'd root key elsewhere in the
        same file must still be caught the same way it was before this field
        existed (per-entry unknown-key checks are untouched). Written by hand
        (dict_to_sexp would fatal on the unknown `retierd` key at serialize
        time — this must reach load_config's per-entry unknown-key check)."""
        config_file = tmp_path / "test.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (board_name "3CH-AWG-TIA-v102")\n'
            "  (rules\n"
            "    (rule\n"
            '      (net "+3V3")\n'
            '      (anchor_role "FPGA")\n'
            "      (spokes\n"
            '        (spoke (pad "17") (cell "t") (retierd false)))))\n'
            "  (cells))\n",
            encoding="utf-8")
        with pytest.raises(ValidationError, match="retierd"):
            load_config(str(config_file))


class TestCheckBoardIdentity:
    def test_opt_in_skipped_when_board_name_unset(self):
        cfg = Config()
        check_board_identity(cfg, _adapter(live="anything.kicad_pcb"))  # no raise

    def test_skipped_when_adapter_not_connected(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        check_board_identity(cfg, _adapter(live=None))  # no raise

    def test_matching_board_passes(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        check_board_identity(cfg, _adapter(live="3CH-AWG-TIA-v102.kicad_pcb"))

    def test_matching_board_is_case_insensitive(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        check_board_identity(cfg, _adapter(live="3ch-awg-tia-v102.KICAD_PCB"))

    def test_board_name_with_extension_matches_live_without_it(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102.kicad_pcb")
        check_board_identity(cfg, _adapter(live="3CH-AWG-TIA-v102"))

    def test_live_full_path_is_compared_by_basename_only(self):
        # The config and the live board live in unrelated directory trees, and
        # paths differ across Windows/Linux — only the basename stem matters.
        # The Windows path (backslashes) must be parsed the same way on Linux
        # (PosixPath) as on Windows, or this guard misfires (2026-08-20).
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        check_board_identity(cfg, _adapter(live=r"D:\Projects\KiCad\3CH-AWG-TIA\3CH-AWG-TIA-v102\3CH-AWG-TIA-v102.kicad_pcb"))

    def test_board_name_full_path_matches_live_full_path(self):
        # board_name may itself arrive as a full path (either separator style);
        # both sides go through _path_basename_stem, so this must pass.
        cfg = Config(board_name=r"D:\Projects\KiCad\3CH-AWG-TIA\3CH-AWG-TIA-v102\3CH-AWG-TIA-v102.kicad_pcb")
        check_board_identity(cfg, _adapter(live="/home/denis/kicad/3CH-AWG-TIA-v102.kicad_pcb"))


class TestPathBasenameStem:
    def test_windows_path_backslashes(self):
        assert _path_basename_stem(r"D:\Projects\KiCad\3CH-AWG-TIA\3CH-AWG-TIA-v102\3CH-AWG-TIA-v102.kicad_pcb") == "3CH-AWG-TIA-v102"

    def test_posix_path_forward_slashes(self):
        assert _path_basename_stem("/home/denis/kicad/3CH-AWG-TIA-v102.kicad_pcb") == "3CH-AWG-TIA-v102"

    def test_mixed_separators(self):
        assert _path_basename_stem(r"D:\Projects/KiCad\3CH-AWG-TIA-v102.kicad_pcb") == "3CH-AWG-TIA-v102"

    def test_bare_filename(self):
        assert _path_basename_stem("3CH-AWG-TIA-v102.kicad_pcb") == "3CH-AWG-TIA-v102"

    def test_multiple_dots_kept_in_stem(self):
        assert _path_basename_stem("3CH.AWG-TIA-v102.kicad_pcb") == "3CH.AWG-TIA-v102"

    def test_mismatch_is_fatal_with_both_names(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        adapter = _adapter(live="3CH-AWG-TIA-v101.kicad_pcb")
        with pytest.raises(ValidationError) as exc_info:
            check_board_identity(cfg, adapter)
        text = str(exc_info.value)
        assert "connected board does not match this config" in text
        assert "3CH-AWG-TIA-v102" in text and "3CH-AWG-TIA-v101.kicad_pcb" in text


class TestRunAllChecksOrdering:
    def test_board_identity_checked_first_in_chain(self):
        """With a config that has nothing else to validate (no rules/clones/
        cells/coordinate_placements), a board mismatch must be the error that
        run_all_checks reports — i.e. the check runs BEFORE all others (a
        wrong board makes every other check misleading)."""
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        adapter = _adapter(live="3CH-AWG-TIA-v101.kicad_pcb")
        with pytest.raises(ValidationError) as exc_info:
            run_all_checks(adapter, cfg)
        assert "connected board does not match this config" in str(exc_info.value)

    def test_matching_board_passes_through_run_all_checks(self):
        cfg = Config(board_name="3CH-AWG-TIA-v102")
        adapter = _adapter(live="3CH-AWG-TIA-v102.kicad_pcb")
        run_all_checks(adapter, cfg)  # no raise
