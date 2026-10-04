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

from tests.fakes.format3 import without_identity

import gui.config_io as config_io_mod


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
        # the spoke's cell target and an explicit chain name (under the gate the
        # lift mints a name for a nameless record — the round-trip would carry
        # one the input dict does not).
        "cells": {"fpga_pwr_bank": {}},
        "chains": [
            {"name": "+3V3_VCCIO", "net": "+3V3_VCCIO", "anchor_role": "FPGA",
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
    # the round-trip carries uuid siblings under the format-3 gate; this cell is
    # about the WRITE/READ shape, not the uuid.
    assert without_identity(back) == without_identity(_strip_defaults(data))
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
    }, format_number=2), encoding="utf-8")
    merge_write(path, {"extract_profiles": {"p2": {"name": "p2", "output": "o2.yaml"}}},
                section="extract_profiles")
    data = _load(path)
    assert set(data["extract_profiles"].keys()) == {"p1", "p2"}


def test_upsert_list_entry_sexp_replaces_by_key(tmp_path):
    path = tmp_path / "cfg.sexp"
    path.write_text(dict_to_sexp({
        "thermal_via_arrays": [{"name": "A", "pad": "2"}],
    }, format_number=2), encoding="utf-8")
    assert upsert_list_entry(path, "thermal_via_arrays",
                             {"name": "A", "pad": "9"}) is True
    assert upsert_list_entry(path, "thermal_via_arrays",
                             {"name": "B", "pad": "1"}) is False
    data = _load(path)
    assert without_identity(data["thermal_via_arrays"]) == [
        {"name": "A", "pad": "9"},
        {"name": "B", "pad": "1"},
    ]


def test_upsert_clone_placement_sexp(tmp_path):
    path = tmp_path / "cfg.sexp"
    path.write_text(dict_to_sexp({
        "cells": {"dac_buf": {}},
        "clone_placements": [
            # explicit name: the upsert key is the record's name, and under the
            # gate a nameless record is minted a name that would not match.
            {"name": "CH0", "cluster": "CH0", "cell": "dac_buf", "xy": [0.0, 0.0]},
        ],
    }, format_number=2), encoding="utf-8")
    assert upsert_clone_placement(path, {"name": "CH0", "cluster": "CH0",
                                         "cell": "dac_buf", "xy": [1.0, 2.0]}) is True
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


# ── gui/config_io.load_data (read-only browse path) ───────────────────────

def test_config_io_load_data_sexp(tmp_path):
    path = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "cells": {"a": {"layer": "B.Cu"}},
    }, format_number=2))
    data = config_io_mod.load_data(path)
    assert data["layer"] == "B.Cu"
    assert data["cells"]["a"]["layer"] == "B.Cu"


def test_config_io_load_data_sexp_malformed_returns_empty(tmp_path):
    path = _write(tmp_path, "broken.sexp", "(kicadstamp-config\n")
    assert config_io_mod.load_data(path) == {}
    assert config_io_mod.load_data(None) == {}
    assert config_io_mod.load_data(tmp_path / "missing.sexp") == {}


def test_config_io_existing_keys_sexp(tmp_path):
    path = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "cells": {"a": {}, "b": {}},
    }, format_number=2))
    assert config_io_mod.existing_keys(path) == {"cells"}
    assert config_io_mod.existing_keys(path, "cells") == {"a", "b"}
