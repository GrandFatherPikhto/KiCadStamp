# tests/test_sexp_config_convert.py
"""Tests for tools/sexp_config_convert.py — the bidirectional yaml <-> s-expr
config converter (parallel .sexp config format). Both directions round-trip
on a real profile, a .bak of the input is always written, and a missing input
produces a clear error."""
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import _strip_defaults, dict_to_sexp, sexp_to_dict
from kicadstamp.utils.yaml_loader import safe_load

from tools.sexp_config_convert import (
    convert_all_profiles,
    convert_file,
)

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles"
REAL_PROFILE = PROFILES_ROOT / "3ch-awg-tia-v103" / "3ch-awg-tia.yaml"


def _write(tmp_path, name, text) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_yaml_to_sexp_writes_bak_and_sexp(tmp_path):
    p = _write(tmp_path, "cfg.yaml", "layer: B.Cu\nrules:\n- net: +3V3\n"
                                     "  spokes:\n  - pad: '1'\n    cell: c1\n")
    out = convert_file(p, to_sexp=True)
    assert out == p.with_suffix(".sexp")
    assert out.exists()
    assert (tmp_path / "cfg.yaml.bak").exists()
    text = out.read_text(encoding="utf-8")
    assert text.strip().startswith("(kicadstamp-config")
    assert '"B.Cu"' in text


def test_sexp_to_yaml_writes_bak_and_yaml(tmp_path):
    p = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "cells": {"a": {"layer": "B.Cu"}},
    }))
    out = convert_file(p, to_sexp=False)
    assert out == p.with_suffix(".yaml")
    assert out.exists()
    assert (tmp_path / "cfg.sexp.bak").exists()
    data = safe_load(out.read_text(encoding="utf-8")) or {}
    assert data["layer"] == "B.Cu"
    assert data["cells"]["a"]["layer"] == "B.Cu"


def test_direction_inferred_from_extension(tmp_path):
    y = _write(tmp_path, "a.yaml", "layer: B.Cu\n")
    assert convert_file(y).suffix == ".sexp"  # yaml -> sexp by default
    s = _write(tmp_path, "b.sexp", dict_to_sexp({"layer": "B.Cu"}))
    assert convert_file(s).suffix == ".yaml"  # sexp -> yaml by default


def test_real_profile_roundtrip_both_directions(tmp_path):
    """The real profile converts yaml -> sexp -> yaml and the final yaml
    equals the default-stripped original (the s-expr format legitimately
    omits explicitly-written default values)."""
    if not REAL_PROFILE.exists():
        pytest.skip(f"real profile not present: {REAL_PROFILE}")
    import shutil
    p = tmp_path / REAL_PROFILE.name
    shutil.copy2(REAL_PROFILE, p)

    sexp_out = convert_file(p, to_sexp=True)          # yaml -> sexp
    yaml_back = convert_file(sexp_out, to_sexp=False)  # sexp -> yaml

    orig = safe_load(p.read_text(encoding="utf-8")) or {}
    back = safe_load(yaml_back.read_text(encoding="utf-8")) or {}
    assert back == _strip_defaults(orig)
    # and the .sexp itself also round-trips
    assert sexp_to_dict(sexp_out.read_text(encoding="utf-8")) == _strip_defaults(orig)


def test_missing_input_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        convert_file(tmp_path / "nope.yaml", to_sexp=True)


def test_convert_all_profiles_generates_sexp_next_to_yaml(tmp_path):
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub", "one.yaml", "layer: B.Cu\n")
    _write(tmp_path / "sub", "two.yaml", "rules:\n- net: N\n  spokes:\n  - pad: '1'\n    cell: c\n")
    # a pre-existing .sexp is left untouched (parallel format, no clobber)
    (tmp_path / "sub" / "one.sexp").write_text("(kicadstamp-config)", encoding="utf-8")

    written = convert_all_profiles(tmp_path)
    names = {p.name for p in written}
    assert names == {"two.sexp"}  # one.sexp already existed -> skipped
    assert (tmp_path / "sub" / "one.yaml").exists()  # YAML kept
    assert (tmp_path / "sub" / "two.yaml").exists()
    assert (tmp_path / "sub" / "two.sexp").exists()
    assert (tmp_path / "sub" / "one.sexp").read_text(encoding="utf-8") == "(kicadstamp-config)"
