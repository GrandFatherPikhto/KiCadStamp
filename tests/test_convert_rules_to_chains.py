# tests/test_convert_rules_to_chains.py
"""Tests for tools/convert_rules_to_chains.py — the one-time migration of the
legacy `rules:` config key to `chains:` (2026-09-01, Rule -> Chain rename).

The loader already READS the legacy key (config/aliases.py normalizes it), so
this tool is about CANONICALIZING existing profiles on disk: rename `rules:` to
`chains:`, back up each rewritten file, idempotent on re-run, and never touch a
file that doesn't carry the legacy key.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

from tools.convert_rules_to_chains import (
    convert_file,
    convert_profile,
    convert_rules_to_chains,
)


def _write_sexp(path: Path, data: dict) -> Path:
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _read_sexp(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def test_convert_rules_to_chains_renames_key_in_place():
    data = {"cells": {}, "rules": [{"net": "GND"}]}
    convert_rules_to_chains(data)
    assert data == {"cells": {}, "chains": [{"net": "GND"}]}


def test_convert_rules_to_chains_noop_without_rules():
    data = {"cells": {}, "chains": [{"net": "GND"}]}
    convert_rules_to_chains(data)
    assert data == {"cells": {}, "chains": [{"net": "GND"}]}


def test_convert_rules_to_chains_both_keys_is_fatal():
    with pytest.raises(ValueError, match="both"):
        convert_rules_to_chains({"rules": [], "chains": []})


def test_convert_file_renames_rules_to_chains_and_backs_up(tmp_path):
    p = _write_sexp(tmp_path / "cfg.sexp", {"cells": {}, "rules": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": []}]})

    bak = convert_file(p)

    assert bak is not None
    data = _read_sexp(p)
    assert data["chains"][0]["net"] == "+3V3"
    # a timestamped .bak of the original was written
    baks = list(tmp_path.glob("cfg.sexp.bak.*"))
    assert len(baks) == 1
    assert baks[0] == bak
    # the backup still carries the LEGACY key (raw, un-normalized — the
    # converter's own reader and the normalizing reader both see it, but
    # sexp_to_dict's default apply_aliases=True would already map it; use
    # apply_aliases=False like the converter does to see the raw key).
    assert "rules" in sexp_to_dict(baks[0].read_text(encoding="utf-8"),
                                   apply_aliases=False)


def test_convert_file_idempotent_when_already_chains(tmp_path):
    p = _write_sexp(tmp_path / "cfg.sexp", {"cells": {}, "chains": [
        {"net": "GND"}]})

    bak = convert_file(p)

    assert bak is None
    assert list(tmp_path.glob("cfg.sexp.bak.*")) == []  # no backup, no write
    assert _read_sexp(p)["chains"][0]["net"] == "GND"


def test_convert_profile_walks_include_graph(tmp_path):
    """A legacy `rules:` key in an INCLUDED file is converted too (rules
    routinely live in separate include: files)."""
    _write_sexp(tmp_path / "sub.sexp", {"rules": [{"net": "N"}]})
    root = _write_sexp(tmp_path / "root.sexp", {
        "include": ["sub.sexp"],
        "rules": [{"net": "M"}]})

    changed = convert_profile(root)

    assert set(changed) == {str(root), str(tmp_path / "sub.sexp")}
    assert _read_sexp(root)["chains"][0]["net"] == "M"
    assert _read_sexp(tmp_path / "sub.sexp")["chains"][0]["net"] == "N"
    # each converted file's backup path is reported and exists on disk
    for bak in changed.values():
        assert bak.exists()
    assert len(list(tmp_path.glob("root.sexp.bak.*"))) == 1
    assert len(list(tmp_path.glob("sub.sexp.bak.*"))) == 1


def test_convert_profile_reports_empty_when_no_legacy_key(tmp_path):
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {}, "chains": []})
    changed = convert_profile(root)
    assert changed == {}
    assert list(tmp_path.glob("root.sexp.bak.*")) == []
