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


def test_yaml_to_sexp_first_conversion_writes_no_bak(tmp_path):
    """A fresh conversion (no pre-existing output) writes NO .bak — the
    input file is never modified and there is nothing at the output path to
    lose. (The .bak only exists when it protects a pre-existing output, see
    test_pre_existing_output_is_backed_up_before_overwrite.)"""
    p = _write(tmp_path, "cfg.yaml", "layer: B.Cu\nchains:\n- net: +3V3\n"
                                     "  spokes:\n  - pad: '1'\n    cell: c1\n")
    out = convert_file(p, to_sexp=True)
    assert out == p.with_suffix(".sexp")
    assert out.exists()
    assert not (tmp_path / "cfg.yaml.bak").exists()
    assert not (tmp_path / "cfg.sexp.bak").exists()
    text = out.read_text(encoding="utf-8")
    assert text.strip().startswith("(kicadstamp-config")
    assert '"B.Cu"' in text


def test_sexp_to_yaml_first_conversion_writes_no_bak(tmp_path):
    p = _write(tmp_path, "cfg.sexp", dict_to_sexp({
        "layer": "B.Cu",
        "cells": {"a": {"layer": "B.Cu"}},
    }))
    out = convert_file(p, to_sexp=False)
    assert out == p.with_suffix(".yaml")
    assert out.exists()
    assert not (tmp_path / "cfg.sexp.bak").exists()
    assert not (tmp_path / "cfg.yaml.bak").exists()
    data = safe_load(out.read_text(encoding="utf-8")) or {}
    assert data["layer"] == "B.Cu"
    assert data["cells"]["a"]["layer"] == "B.Cu"


def test_pre_existing_output_is_backed_up_before_overwrite(tmp_path):
    """Regression (found in review, 2026-08-27): convert_file() used to back
    up the INPUT file (which it never modifies) — a pre-existing OUTPUT file
    was silently destroyed with no recoverable backup. Now the pre-existing
    output's OLD content must survive as <out>.bak, and the output itself
    must hold the NEW converted content."""
    p = _write(tmp_path, "foo.yaml", "layer: B.Cu\n")
    old_sexp = "(kicadstamp-config\n  (layer \"F.Cu\"))\n"  # hand-written / older
    (tmp_path / "foo.sexp").write_text(old_sexp, encoding="utf-8")

    out = convert_file(p, to_sexp=True)

    # old output content preserved as .bak of the OUTPUT (not the input)
    bak = tmp_path / "foo.sexp.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == old_sexp
    # input untouched, no bogus input .bak
    assert not (tmp_path / "foo.yaml.bak").exists()
    # output holds the NEW converted content
    assert sexp_to_dict(out.read_text(encoding="utf-8")) == _strip_defaults(
        safe_load(p.read_text(encoding="utf-8")) or {})


def test_pre_existing_output_backed_up_sexp_to_yaml(tmp_path):
    p = _write(tmp_path, "foo.sexp", dict_to_sexp({"layer": "B.Cu"}))
    old_yaml = "layer: F.Cu\n"
    (tmp_path / "foo.yaml").write_text(old_yaml, encoding="utf-8")

    convert_file(p, to_sexp=False)

    bak = tmp_path / "foo.yaml.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == old_yaml
    assert not (tmp_path / "foo.sexp.bak").exists()


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
    _write(tmp_path / "sub", "two.yaml", "chains:\n- net: N\n  spokes:\n  - pad: '1'\n    cell: c\n")
    # a pre-existing .sexp is left untouched (parallel format, no clobber)
    (tmp_path / "sub" / "one.sexp").write_text("(kicadstamp-config)", encoding="utf-8")

    written = convert_all_profiles(tmp_path)
    names = {p.name for p in written}
    assert names == {"two.sexp"}  # one.sexp already existed -> skipped
    assert (tmp_path / "sub" / "one.yaml").exists()  # YAML kept
    assert (tmp_path / "sub" / "two.yaml").exists()
    assert (tmp_path / "sub" / "two.sexp").exists()
    assert (tmp_path / "sub" / "one.sexp").read_text(encoding="utf-8") == "(kicadstamp-config)"
