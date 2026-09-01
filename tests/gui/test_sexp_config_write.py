# tests/gui/test_sexp_config_write.py
"""Write-path tests for the parallel .sexp config format: the GUI docks'
single write chokepoint (kicadstamp/config_writer.py's _read_data/_write_data,
re-exported via gui/docks/_common.py) must save .sexp files that read back
the same dict — and a broken .sexp on the write path must surface as OSError,
matching the existing YAML contract (see test_dock_common.py)."""
from pathlib import Path

import pytest

from gui.docks._common import (
    merge_write,
    read_data,
    upsert_clone_placement,
    upsert_list_entry,
)
from kicadstamp.config.sexp_format import dict_to_sexp

import gui.yaml_io as yaml_io_mod


def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _load(path) -> dict:
    """read_data with a fresh cache (the cached_file_read layer would
    otherwise serve the pre-write state)."""
    from kicadstamp.utils.file_cache import invalidate_path
    invalidate_path(path)
    return read_data(path)


# ── write_data / read_data round-trip ──────────────────────────────────────

def test_write_data_sexp_roundtrips(tmp_path):
    """_write_data(path.sexp, dict) writes s-expr text that _read_data parses
    back into the same dict (default-stripped canonical form — the format
    omits default-valued fields)."""
    path = tmp_path / "cfg.sexp"
    data = {
        "layer": "B.Cu",
        "place_components": False,
        "chains": [
            {"net": "+3V3_VCCIO", "anchor_role": "FPGA",
             "spokes": [{"pad": "17", "cell": "fpga_pwr_bank",
                         "shift_x_mm": 1.2, "shift_y_mm": -1.5}]},
        ],
    }
    from kicadstamp.config_writer import _write_data
    _write_data(path, data)
    text = path.read_text(encoding="utf-8")
    assert text.strip().startswith("(kicadstamp-config")
    assert 'place_components false' in text
    assert '"B.Cu"' in text
    back = _load(path)
    from kicadstamp.config.sexp_format import _strip_defaults
    assert back == _strip_defaults(data)
    assert back["place_components"] is False


def test_read_data_sexp_missing_file_returns_empty(tmp_path):
    assert read_data(tmp_path / "nope.sexp") == {}


# ── merge_write / upsert on .sexp paths ────────────────────────────────────

def test_merge_write_sexp_preserves_other_keys(tmp_path):
    """merge_write on a .sexp path merges only the target section's dict and
    leaves every OTHER top-level key untouched (the YAML contract, now on
    s-expr). Note: cell \"a\" keeps a NON-default layer — a default-valued
    field (e.g. layer F.Cu) is legitimately omitted by the s-expr writer."""
    path = tmp_path / "cfg.sexp"
    path.write_text("(kicadstamp-config\n"
                    "  (cells\n"
                    "    (cell \"a\" (layer \"B.Cu\"))))\n", encoding="utf-8")
    overwritten = merge_write(path, {"cells": {"new_cell": {"layer": "B.Cu"}}},
                              section="cells")
    assert overwritten is False
    data = _load(path)
    assert set(data["cells"].keys()) == {"a", "new_cell"}
    assert data["cells"]["a"]["layer"] == "B.Cu"
    assert data["cells"]["new_cell"]["layer"] == "B.Cu"


def test_merge_write_sexp_section_merges_nested(tmp_path):
    path = tmp_path / "cfg.sexp"
    path.write_text(dict_to_sexp({
        "extract_profiles": {"p1": {"name": "p1", "output": "o1.yaml"}},
    }), encoding="utf-8")
    merge_write(path, {"extract_profiles": {"p2": {"name": "p2", "output": "o2.yaml"}}},
                section="extract_profiles")
    data = _load(path)
    assert set(data["extract_profiles"].keys()) == {"p1", "p2"}


def test_upsert_list_entry_sexp_replaces_by_key(tmp_path):
    path = tmp_path / "cfg.sexp"
    path.write_text(dict_to_sexp({
        "thermal_via_arrays": [{"name": "A", "pad": "2"}],
    }), encoding="utf-8")
    assert upsert_list_entry(path, "thermal_via_arrays",
                             {"name": "A", "pad": "9"}) is True
    assert upsert_list_entry(path, "thermal_via_arrays",
                             {"name": "B", "pad": "1"}) is False
    data = _load(path)
    assert data["thermal_via_arrays"] == [
        {"name": "A", "pad": "9"},
        {"name": "B", "pad": "1"},
    ]


def test_upsert_clone_placement_sexp(tmp_path):
    path = tmp_path / "cfg.sexp"
    path.write_text(dict_to_sexp({
        "clone_placements": [
            {"cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0]},
        ],
    }), encoding="utf-8")
    assert upsert_clone_placement(path, {"cluster": "CH0", "cell": "dac_buf",
                                         "xy": [1.0, 2.0]}) is True
    data = _load(path)
    assert data["clone_placements"][0]["xy"] == [1.0, 2.0]


# ── broken .sexp on the write path -> OSError (not ValidationError) ────────

def test_merge_write_sexp_raises_os_error_on_malformed(tmp_path):
    """Same contract as YAML (test_dock_common.py): a malformed .sexp file on
    the write path surfaces as OSError, never as the raw ValidationError —
    every write-path caller catches OSError per _read_data's docstring."""
    path = _write(tmp_path, "broken.sexp", "(kicadstamp-config\n")
    with pytest.raises(OSError):
        merge_write(path, {"cell": {"x": 1}})


def test_merge_write_sexp_raises_os_error_on_invalid_top_level(tmp_path):
    path = _write(tmp_path, "broken2.sexp", "(not-a-config)\n")
    with pytest.raises(OSError):
        merge_write(path, {"cell": {"x": 1}})


# ── gui/yaml_io.load_data (read-only browse path) ─────────────────────────

def test_yaml_io_load_data_sexp(tmp_path):
    path = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "cells": {"a": {"layer": "B.Cu"}},
    }))
    data = yaml_io_mod.load_data(path)
    assert data["layer"] == "B.Cu"
    assert data["cells"]["a"]["layer"] == "B.Cu"


def test_yaml_io_load_data_sexp_malformed_returns_empty(tmp_path):
    path = _write(tmp_path, "broken.sexp", "(kicadstamp-config\n")
    assert yaml_io_mod.load_data(path) == {}
    assert yaml_io_mod.load_data(None) == {}
    assert yaml_io_mod.load_data(tmp_path / "missing.sexp") == {}


def test_yaml_io_existing_keys_sexp(tmp_path):
    path = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "cells": {"a": {}, "b": {}},
    }))
    assert yaml_io_mod.existing_keys(path) == {"cells"}
    assert yaml_io_mod.existing_keys(path, "cells") == {"a", "b"}
