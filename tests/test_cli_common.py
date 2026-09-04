#!/usr/bin/env python3
"""Tests for kicadstamp/cli_common.py — the single owner of CLI exit codes
(PlacerError→1 / ApiError→1 / Exception→2), shared by kicadstamp_cli.py's
main() and author_cli.cli_main()."""
import logging
from pathlib import Path

import pytest
from kipy.errors import ApiError, ApiStatusCode

from kicadstamp.cli_common import (api_error_message, peek_log_file,
                                   peek_operation_log_dir, run_cli)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import PlacerError, ValidationError


def _raise(exc):
    def _fn():
        raise exc
    return _fn


class TestRunCli:
    def test_success_returns_zero(self):
        assert run_cli(lambda: None) == 0

    def test_placer_error_returns_one_and_logs(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(PlacerError("boom")))
        assert code == 1
        assert "boom" in caplog.text

    def test_validation_error_returns_one(self):
        """ValidationError subclasses PlacerError — same exit code."""
        assert run_cli(_raise(ValidationError("bad"))) == 1

    def test_api_error_returns_one(self):
        assert run_cli(_raise(ApiError("nope", code=ApiStatusCode.AS_TIMEOUT))) == 1

    def test_api_error_as_busy_logs_dedicated_message(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(ApiError("nope", code=ApiStatusCode.AS_BUSY)))
        assert code == 1
        assert "KiCad is busy" in caplog.text

    def test_unexpected_exception_returns_two(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(RuntimeError("bug")))
        assert code == 2
        assert "Unexpected error" in caplog.text

    def test_system_exit_propagates_unchanged(self):
        """SystemExit (a BaseException) is not swallowed — argparse errors and
        deliberate aborts keep their own exit code."""
        with pytest.raises(SystemExit) as exc_info:
            run_cli(_raise(SystemExit(3)))
        assert exc_info.value.code == 3


class TestApiErrorMessage:
    def test_as_busy_gets_dedicated_explanation(self):
        msg = api_error_message(ApiError("nope", code=ApiStatusCode.AS_BUSY))
        assert "KiCad is busy" in msg
        assert "not modified" in msg

    def test_other_code_gets_generic_message(self):
        msg = api_error_message(ApiError("nope", code=ApiStatusCode.AS_TIMEOUT))
        assert "KiCad returned API error" in msg


class TestPeekLogFile:
    """peek_log_file() — the CLI's cheap pre-logging read of just the config's
    log_file key (see kicadstamp_cli.py: it must NOT run a full load_config()
    there; that single validated load belongs to the apply pipeline). Fixtures
    are .sexp — since core_yaml_removal the root config is s-expr/.json and
    peek_log_file dispatches on extension like every core config reader."""

    def _dump(self, data: dict) -> str:
        return dict_to_sexp(data)

    def _write(self, tmp_path, data, name="cfg.sexp"):
        p = tmp_path / name
        p.write_text(self._dump(data), encoding="utf-8")
        return str(p)

    def test_returns_log_file_resolved_relative_to_config(self, tmp_path):
        path = self._write(tmp_path, {"log_file": "logs/run.log", "layer": "B.Cu"})
        assert peek_log_file(path) == str(tmp_path / "logs" / "run.log")

    def test_absolute_log_file_kept_as_is(self, tmp_path):
        path = self._write(tmp_path, {"log_file": "C:/tmp/run.log"})
        assert peek_log_file(path) == str(Path("C:/tmp/run.log"))

    def test_no_log_file_returns_default(self, tmp_path):
        """CHANGED (2026-09-04, plan root_metadata_path_defaults): a missing
        log_file: is no longer 'silently no file' — peek_log_file falls back to
        <config-dir>/logs/actions.log, so apply/GUI consistently write a file
        log next to the config. ``None`` is now reserved for the exception path
        (broken/unreadable config), see the two *_with_warning tests."""
        path = self._write(tmp_path, {"layer": "B.Cu"})
        assert peek_log_file(path) == str(tmp_path / "logs" / "actions.log")

    def test_missing_file_returns_none_with_warning(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            result = peek_log_file(str(tmp_path / "nope.sexp"))
        assert result is None
        assert "log_file" in caplog.text

    def test_broken_sexp_returns_none_with_warning(self, tmp_path, caplog):
        # Unbalanced parens -> sexp_to_dict raises -> the never-raise contract
        # turns it into a warning + None.
        p = tmp_path / "cfg.sexp"
        p.write_text('(kicadstamp-config (log_file "run.log"', encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = peek_log_file(str(p))
        assert result is None
        assert "log_file" in caplog.text

    def test_does_not_run_include_resolution(self, tmp_path):
        """Peek must be oblivious to include: — log_file is a root-file scalar;
        the full load (include resolution + validation) happens once in the
        pipeline. A broken child include must not poison the peek."""
        path = self._write(tmp_path, {"log_file": "run.log",
                                      "include": ["missing_child.sexp"]})
        assert peek_log_file(path) == str(tmp_path / "run.log")


class TestPeekOperationLogDir:
    """peek_operation_log_dir() — the reading side of the operation-log
    default (2026-09-04, plan root_metadata_path_defaults): apply writes
    operation_*.json to a config-relative directory (else
    <config-dir>/operational), so `undo --config` must peek the SAME directory
    before any full validated load. Same never-raise contract as peek_log_file."""

    @staticmethod
    def _write(tmp_path, data, name="cfg.sexp"):
        p = tmp_path / name
        p.write_text(dict_to_sexp(data), encoding="utf-8")
        return str(p)

    def test_explicit_resolved_relative_to_config(self, tmp_path):
        path = self._write(tmp_path, {"operation_log_dir": "logs", "layer": "B.Cu"})
        assert peek_operation_log_dir(path) == str(tmp_path / "logs")

    def test_absolute_kept_as_is(self, tmp_path):
        path = self._write(tmp_path, {"operation_log_dir": "C:/tmp/ops"})
        assert peek_operation_log_dir(path) == str(Path("C:/tmp/ops"))

    def test_absent_returns_config_relative_default(self, tmp_path):
        """No operation_log_dir key -> <config-dir>/operational (the default
        apply now writes to), never a CWD-relative fallback."""
        path = self._write(tmp_path, {"layer": "B.Cu"})
        assert peek_operation_log_dir(path) == str(tmp_path / "operational")

    def test_broken_file_returns_none_with_warning(self, tmp_path, caplog):
        p = tmp_path / "cfg.sexp"
        p.write_text('(kicadstamp-config (operation_log_dir "run"', encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = peek_operation_log_dir(str(p))
        assert result is None
        assert "operation_log_dir" in caplog.text
