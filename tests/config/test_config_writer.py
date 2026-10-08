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


import pytest

from kicadstamp.config_writer import (
    _read_data, _write_data, is_staged_write, write_report_line,
)
from kicadstamp.exceptions import ValidationError

from tests.fakes.format3 import without_identity


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
    assert without_identity(_read_data(sexp)) == {"layer": "B.Cu"}
    js = tmp_path / "cfg.json"
    _write_data(js, {"cells": {"a": {}}})
    # The round-tripped record carries a uuid under the format-3 gate; this cell
    # is about the FORMAT DISPATCH, not the uuid.
    assert without_identity(_read_data(js)) == {"cells": {"a": {}}}


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в): the format-3 writer resolves a reference's UUID against
    the ACTIVE GRAPH ROOT. These cells write a self-contained config; the root is
    a path that does NOT exist, so the stamp indexes THIS write's own records
    (config/format3._build_format3_index) — the format-3 product path, no
    on-disk graph walked. Under format 2 (< 3) the root is never consulted."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


# ── part В of plan_2026_10_08_narrowing_net_traces_cost: ONE wording rule for a
#    write's outcome — "Wrote/Overwrote ... in <file>" ONLY when the bytes really
#    reached the disk, "Staged ... not saved yet (File -> Save)" while the working
#    set holds them ───────────────────────────────────────────────────────────

@pytest.fixture
def staged(monkeypatch):
    """The GUI's staged mode (tests/conftest.py clears it after each test)."""
    from kicadstamp.config_working_set import WORKING_SET

    monkeypatch.setattr(WORKING_SET, "enabled", True)
    return WORKING_SET


def test_the_physical_line_is_returned_unchanged_when_the_file_is_written(tmp_path):
    """No working set (CLI, tests, a closed project): the dock's own sentence goes
    through untouched — every existing wording and every existing cell intact."""
    path = tmp_path / "cfg.sexp"

    assert is_staged_write(path) is False
    assert write_report_line("Wrote 'dac0' in <file>", "'dac0'", path,
                             created=True) == "Wrote 'dac0' in <file>"
    assert write_report_line("Overwrote 'dac0' in <file>", "'dac0'", path,
                             created=False) == "Overwrote 'dac0' in <file>"


def test_a_staged_new_record_says_so_and_the_file_stays_untouched(tmp_path, staged):
    """The live 19:40:39 defect: the line claimed the file, the bytes were in the
    working set and the mtime never moved. A NEW record says "Staged new"."""
    path = tmp_path / "cfg.sexp"
    _write_data(path, {"cells": {}})

    assert not path.exists(), "a staged write must not create the file"
    assert is_staged_write(path) is True
    assert write_report_line("Wrote 'dac0' in <file>", "'dac0'", path,
                             created=True) == \
        "Staged new 'dac0' — not saved yet (File → Save)"


def test_a_staged_change_says_so(tmp_path, staged):
    """The SAME rule for a record that already existed: "Staged changes to"."""
    path = tmp_path / "cfg.sexp"
    _write_data(path, {"cells": {"dac0": {}}})

    assert write_report_line("Overwrote 'dac0' in <file>", "'dac0'", path,
                             created=False) == \
        "Staged changes to 'dac0' — not saved yet (File → Save)"


def test_discarding_the_working_set_brings_the_physical_wording_back(tmp_path, staged):
    """"Staged" is a STATE, not a sticky flag: once the file is no longer held by
    the working set the dock's own sentence is true again."""
    path = tmp_path / "cfg.sexp"
    _write_data(path, {"cells": {}})
    assert is_staged_write(path) is True

    staged.clear()

    assert is_staged_write(path) is False
    assert write_report_line("Wrote 'dac0' in <file>", "'dac0'", path,
                             created=True) == "Wrote 'dac0' in <file>"
