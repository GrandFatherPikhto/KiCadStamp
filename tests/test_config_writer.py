#!/usr/bin/env python3
"""Format-dispatch tests for kicadstamp/config_writer.py's _read_data/_write_data
— the single read/write chokepoint for every GUI dock (2026-08-28,
core_yaml_removal, plan_2026_08_28_core_yaml_removal.md):

  - .json and .sexp are the only supported config formats (parse/write
    unchanged);
  - .yaml/.yml and any OTHER extension (including a missing one) are a fatal
    OSError — NOT a bare ValidationError — so the GUI docks' `except OSError`
    contract holds (this was a live bug: the previous fix raised a bare
    ValidationError that escaped Qt slots; fixed 2026-08-28, §0.5 of the
    plan). The ValidationError is kept as __cause__ for diagnostics.
  - .yaml/.yml get the dedicated "YAML support removed — convert with
    sexp_config_convert.py" message; other extensions get the generic
    unrecognized-extension message.

The existing read/parse semantics live in tests/gui/test_dock_common.py
(.sexp/.json) and tests/gui/test_sexp_config_write.py (s-expr) — this file is
focused purely on the unsupported-format fatal behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config_writer import _read_data, _write_data
from kicadstamp.exceptions import ValidationError


def _yaml_removed_msg(exc):
    return str(exc.value)


# ── .yaml/.yml — fatal OSError with the sexp_config_convert.py message ───────


def test_yaml_read_is_os_error_with_convert_message(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(OSError) as excinfo:
        _read_data(path)
    assert "sexp_config_convert.py" in _yaml_removed_msg(excinfo)
    assert str(path) in _yaml_removed_msg(excinfo)


def test_yaml_write_is_os_error_and_creates_no_file(tmp_path):
    path = tmp_path / "cfg.yaml"
    with pytest.raises(OSError) as excinfo:
        _write_data(path, {"cells": {}})
    assert "sexp_config_convert.py" in _yaml_removed_msg(excinfo)
    assert not path.exists()


def test_yml_extension_is_os_error(tmp_path):
    path = tmp_path / "cfg.yml"
    path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(OSError) as excinfo:
        _read_data(path)
    assert "sexp_config_convert.py" in _yaml_removed_msg(excinfo)


def test_yaml_error_keeps_validation_error_as_cause(tmp_path):
    """§0.5 of the plan: the fatal must be OSError (so `except OSError` in the
    GUI docks catches it) with the ValidationError preserved as __cause__ for
    diagnostics."""
    path = tmp_path / "cfg.yaml"
    path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(OSError) as excinfo:
        _read_data(path)
    assert isinstance(excinfo.value.__cause__, ValidationError)


# ── unknown / missing extension -> fatal OSError ─────────────────────────────


def test_unknown_extension_read_is_os_error(tmp_path):
    path = tmp_path / "cfg.conf"
    path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(OSError) as excinfo:
        _read_data(path)
    msg = str(excinfo.value)
    assert str(path) in msg
    assert ".conf" in msg
    assert "use .sexp" in msg


def test_unknown_extension_write_is_os_error_and_creates_no_file(tmp_path):
    path = tmp_path / "cfg.conf"
    with pytest.raises(OSError) as excinfo:
        _write_data(path, {"cells": {}})
    msg = str(excinfo.value)
    assert str(path) in msg
    assert ".conf" in msg
    assert not path.exists()  # a bad extension must not leave an empty file behind


def test_no_extension_is_os_error(tmp_path):
    read_path = tmp_path / "cfg"  # no extension at all
    read_path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(OSError):
        _read_data(read_path)

    write_path = tmp_path / "other"  # no extension at all
    with pytest.raises(OSError):
        _write_data(write_path, {"cells": {}})
    assert not write_path.exists()


# ── .sexp / .json — still work (regression) ─────────────────────────────────


def test_sexp_and_json_work(tmp_path):
    sexp = tmp_path / "cfg.sexp"
    _write_data(sexp, {"layer": "B.Cu"})
    assert _read_data(sexp) == {"layer": "B.Cu"}
    js = tmp_path / "cfg.json"
    _write_data(js, {"cells": {"a": {}}})
    assert _read_data(js) == {"cells": {"a": {}}}
