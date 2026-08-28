#!/usr/bin/env python3
"""Unit tests for the undo / clone-extract CLI wrappers in
kicadstamp/cli.py (П.5: moved out of kicadstamp_cli.py into the package CLI
module). The validation paths tested here raise a PlacerError before any
real file/board I/O happens — mirroring how kicadstamp/cli.py reports bad
input instead of sys.exit()/print() (the entry point maps PlacerError to
exit code 1 via cli_common.run_cli)."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.cli import cmd_clone_extract, cmd_undo
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import PlacerError


def _dump(data: dict) -> str:
    """clone_profiles is a free-form dict section; write it as s-expr (the
    profiles file is read via cli_extract.load_profile, .sexp/.json only since
    core_yaml_removal)."""
    return dict_to_sexp(data)


def _clone_args(**kw):
    defaults = dict(net=None, pcb=None, channel=None, output=None,
                    profiles=None, profile=None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _undo_args(**kw):
    defaults = dict(operation_log_dir=None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestCmdCloneExtractValidation:
    """Reach cmd_clone_extract's argument validation without touching the
    filesystem beyond tmp_path (no .net/.kicad_pcb parsing happens)."""

    def test_profile_with_direct_flags_is_fatal(self):
        with pytest.raises(PlacerError, match="cannot be combined"):
            cmd_clone_extract(_clone_args(net="a.net", profile="ch0"))

    def test_profile_without_profiles_file_is_fatal(self):
        with pytest.raises(PlacerError, match="--profiles"):
            cmd_clone_extract(_clone_args(profile="ch0"))

    def test_no_args_without_profile_is_fatal(self):
        with pytest.raises(PlacerError, match="need --net"):
            cmd_clone_extract(_clone_args())

    def test_profile_not_found_is_fatal(self, tmp_path):
        profiles = tmp_path / "profiles.sexp"
        profiles.write_text(_dump({
            "clone_profiles": {
                "ch0": {
                    "net": "a.net", "pcb": "b.kicad_pcb",
                    "channel": "Channel_0", "output": "o.sexp"}}}),
            encoding="utf-8")
        with pytest.raises(PlacerError, match="not found"):
            cmd_clone_extract(_clone_args(profiles=str(profiles), profile="nope"))

    def test_profile_missing_required_field_is_fatal(self, tmp_path):
        profiles = tmp_path / "profiles.sexp"
        profiles.write_text(_dump({
            "clone_profiles": {
                "ch0": {"net": "a.net"}}}),  # pcb/channel/output missing
            encoding="utf-8")
        with pytest.raises(PlacerError, match="missing required field"):
            cmd_clone_extract(_clone_args(profiles=str(profiles), profile="ch0"))

    def test_missing_profiles_file_is_fatal(self, tmp_path):
        with pytest.raises(PlacerError, match="not found"):
            cmd_clone_extract(_clone_args(profiles=str(tmp_path / "nope.sexp"),
                                          profile="ch0"))


class TestCmdUndoValidation:
    """cmd_undo must report "nothing to undo" as PlacerError (exit code 1),
    not silently succeed — it used to logger.error + return (exit code 0)."""

    def test_missing_logs_dir_raises(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(PlacerError, match="logs directory not found"):
            cmd_undo(_undo_args())

    def test_no_operation_files_raises(self, monkeypatch, tmp_path):
        (tmp_path / "logs").mkdir()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(PlacerError, match="No operation files"):
            cmd_undo(_undo_args())

    def test_operation_log_dir_override_used(self, monkeypatch, tmp_path):
        """--operation-log-dir must point cmd_undo at the config-bound dir
        instead of the CWD-relative logs/ (П.7)."""
        import kicadstamp.undo as undo_mod
        undone = []
        # cli.py imports undo_last_operation lazily inside cmd_undo, so patch
        # the module it is imported FROM.
        monkeypatch.setattr(undo_mod, "undo_last_operation",
                            lambda json_path: undone.append(json_path) or True)
        custom = tmp_path / "custom_logs"
        custom.mkdir()
        (custom / "operation_20260801_120000.json").write_text("{}", encoding="utf-8")
        cmd_undo(_undo_args(operation_log_dir=str(custom)))
        assert len(undone) == 1
        assert undone[0].parent == custom

    def test_operation_log_dir_not_found_raises(self, tmp_path):
        with pytest.raises(PlacerError, match="logs directory not found"):
            cmd_undo(_undo_args(operation_log_dir=str(tmp_path / "nope")))
