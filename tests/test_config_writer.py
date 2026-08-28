#!/usr/bin/env python3
"""Format-dispatch tests for kicadstamp/config_writer.py's _read_data/_write_data
— the single read/write chokepoint for every GUI dock (2026-08-28,
loud-format-dispatch fix, plan_2026_08_28_config_writer_loud_format_dispatch.md):

  - .json/.sexp/.yaml/.yml are named cases (the parse/write itself is
    unchanged);
  - .yaml/.yml additionally logs a deprecation warning ONCE per resolved path
    per process (the project's main format is s-expr; YAML is a legacy
    fallback) — not once per read/write call;
  - any OTHER extension (including a missing one) is a fatal ValidationError
    naming the path and the extension, instead of a silent YAML fallback.

The existing read/parse semantics live in tests/gui/test_dock_common.py
(YAML/JSON) and tests/gui/test_sexp_config_write.py (s-expr) — this file is
focused purely on the new dispatch/warning/fatal behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml

import kicadstamp.config_writer as config_writer
from kicadstamp.config_writer import _read_data, _write_data
from kicadstamp.exceptions import ValidationError


@pytest.fixture(autouse=True)
def _fresh_yaml_warned_paths():
    """The once-per-path warning set is process-global — each test must start
    (and end) with it empty so "warns exactly once" assertions are about THIS
    test's paths, not leftovers from an earlier one."""
    config_writer._yaml_warned_paths.clear()
    yield
    config_writer._yaml_warned_paths.clear()


def _yaml_warnings(caplog):
    return [r for r in caplog.records if "reading/writing as YAML" in r.getMessage()]


# ── .yaml/.yml — behavior unchanged, deprecation warning once per path ──────


def test_yaml_read_warns_once_per_path(caplog, tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("cells: {}\n", encoding="utf-8")
    _read_data(path)
    _read_data(path)  # cache HIT — must not re-warn
    assert len(_yaml_warnings(caplog)) == 1


def test_yaml_write_warns_once_per_path(caplog, tmp_path):
    path = tmp_path / "cfg.yaml"
    _write_data(path, {"cells": {"a": {}}})
    _write_data(path, {"cells": {"b": {}}})  # cache invalidated, but no re-warn
    assert len(_yaml_warnings(caplog)) == 1


def test_yaml_read_then_write_warns_once(caplog, tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("cells: {}\n", encoding="utf-8")
    _read_data(path)
    _write_data(path, {"cells": {"a": {}}})
    assert len(_yaml_warnings(caplog)) == 1


def test_yml_extension_recognized_as_yaml(caplog, tmp_path):
    path = tmp_path / "cfg.yml"
    path.write_text("cells:\n  a: {}\n", encoding="utf-8")
    assert _read_data(path)["cells"]["a"] == {}
    _write_data(path, {"cells": {"a": {}, "b": {}}})
    assert len(_yaml_warnings(caplog)) == 1
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(data["cells"]) == {"a", "b"}


# ── unknown / missing extension -> fatal ValidationError ─────────────────────


def test_unknown_extension_read_is_fatal(tmp_path):
    path = tmp_path / "cfg.conf"
    path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        _read_data(path)
    msg = str(excinfo.value)
    assert str(path) in msg
    assert ".conf" in msg
    assert "use .sexp" in msg


def test_unknown_extension_write_is_fatal_and_creates_no_file(tmp_path):
    path = tmp_path / "cfg.conf"
    with pytest.raises(ValidationError) as excinfo:
        _write_data(path, {"cells": {}})
    msg = str(excinfo.value)
    assert str(path) in msg
    assert ".conf" in msg
    assert not path.exists()  # a bad extension must not leave an empty file behind


def test_no_extension_is_fatal(tmp_path):
    read_path = tmp_path / "cfg"  # no extension at all
    read_path.write_text("cells: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        _read_data(read_path)

    write_path = tmp_path / "other"  # no extension at all
    with pytest.raises(ValidationError):
        _write_data(write_path, {"cells": {}})
    assert not write_path.exists()


# ── .sexp / .json — no warning, behavior unchanged (regression sanity) ──────


def test_sexp_and_json_do_not_warn(caplog, tmp_path):
    sexp = tmp_path / "cfg.sexp"
    _write_data(sexp, {"layer": "B.Cu"})
    _read_data(sexp)
    js = tmp_path / "cfg.json"
    _write_data(js, {"cells": {}})
    _read_data(js)
    assert _yaml_warnings(caplog) == []
