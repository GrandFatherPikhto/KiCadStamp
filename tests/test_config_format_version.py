# tests/test_config_format_version.py
"""The FORMAT number of a config file — `plan_2026_09_24_config_format_version.md` §5.

Cells, by axis (rule 35 — one parametrized row per cell, so a failing row never
hides its neighbours):

- reading  — no number -> 1; `(version 1)`; CURRENT; CURRENT+1 -> refusal;
             `(version 0)`, `(version -1)`, `(version "two")`, `(version)` ->
             fatal with a message naming the value;
- shape    — `(version 1 2)` -> fatal; twice in the root -> fatal;
- position — the node is read WHEREVER it sits in the root (a hand edit may
             move it); the writer is the side that always puts it first (Т3);
- dict side — `take_version` POPS the key (so the include merge never sees it);
             absent -> 1; a bad value -> fatal;
- chain    — every step runs once, in order; a hole is a fatal, not a silent
             skip; a raising step propagates and leaves the input untouched;
- disk     — `read_version` parses for real, refuses a newer file, and caches by
             `(path, mtime_ns)`, so a real edit is a miss on its own.

The load-bearing row is `test_the_number_never_reaches_the_returned_dict`: the
T0 census measured 132 non-text tests that go red the moment the number leaks
into a caller's dict (plan §10.1), and rule 33 gives no permission to touch them.
"""
import os
import sys

import pytest

from kicadstamp.config import format_version as fv
from kicadstamp.config.format_version import (
    CURRENT_FORMAT,
    read_version,
    take_version,
    upgrade_data,
)
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import _strip_defaults, dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError


def _wrap(body: str) -> str:
    return "(kicadstamp-config\n" + body + ")\n"


def _bump_mtime_forward(path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# ── reading: what number does the text carry? ──────────────────────────────

@pytest.mark.parametrize("body, expected, why", [
    ("  (cells)\n", 1, "no number at all is format 1"),
    ("  (version 1)\n  (cells)\n", 1, "an explicit 1"),
    ("  (version 2)\n  (cells)\n", 2, "the current number"),
])
def test_the_number_the_text_carries_is_read_back(body, expected, why):
    found: list[int] = []
    sexp_to_dict(_wrap(body), version_out=found)
    assert (found[0] if found else 1) == expected, why


def test_the_number_never_reaches_the_returned_dict():
    """The cell the whole parse-layer decision rests on (plan §10.1): 132
    existing non-text tests compare a `sexp_to_dict` dict against an expectation
    built by hand, and any of them would go red if `version` appeared here."""
    for body in ("  (cells)\n",
                 "  (version 1)\n  (cells)\n",
                 "  (version 2)\n  (cells)\n"):
        assert "version" not in sexp_to_dict(_wrap(body))


def test_the_node_is_read_wherever_it_sits_in_the_root():
    """A hand edit may move the node; the READER must not care (the writer is the
    side that always puts it first — Т3)."""
    found: list[int] = []
    assert sexp_to_dict(_wrap("  (cells)\n  (version 2)\n"),
                        version_out=found) == {"cells": {}}
    assert found == [2]


def test_the_number_is_taken_out_even_with_apply_aliases_disabled():
    """`apply_aliases=False` is the migration converter's mode — it must still be
    free of a stray root key."""
    found: list[int] = []
    back = sexp_to_dict(_wrap("  (version 1)\n  (cells)\n"),
                        apply_aliases=False, version_out=found)
    assert found == [1] and "version" not in back


def test_a_file_newer_than_this_build_is_refused():
    """Reading an unknown-grammar file is the one thing we must not do: three
    machines share the profiles, so a newer file arrives before the pull does."""
    with pytest.raises(ValidationError) as excinfo:
        sexp_to_dict(_wrap(f"  (version {CURRENT_FORMAT + 1})\n"))
    assert f"format {CURRENT_FORMAT + 1}" in str(excinfo.value)


# ── shape: what is NOT a number ────────────────────────────────────────────

@pytest.mark.parametrize("body, why", [
    ("  (version 0)\n", "zero is not a format"),
    ("  (version -1)\n", "negative"),
    ('  (version "two")\n', "a quoted string"),
    ("  (version two)\n", "a bare atom"),
    ("  (version)\n", "no value at all"),
    ("  (version 1 2)\n", "two values under one node"),
])
def test_a_malformed_format_number_is_fatal(body, why):
    with pytest.raises(ValidationError):
        sexp_to_dict(_wrap(body))


def test_two_numbers_in_one_root_are_fatal():
    """Ambiguous — same discipline as two spellings of a renamed key."""
    with pytest.raises(ValidationError):
        sexp_to_dict(_wrap("  (version 1)\n  (version 2)\n"))


# ── the dict side (the JSON path shares it) ────────────────────────────────

@pytest.mark.parametrize("data, expected, why", [
    ({}, 1, "no key is format 1"),
    ({"version": 1}, 1, "an explicit 1"),
    ({"version": 2}, 2, "the current number"),
])
def test_take_version_reads_the_dict(data, expected, why):
    assert take_version(data, "probe.json") == expected, why


def test_take_version_removes_the_key():
    data = {"version": 2, "cells": {}}
    assert take_version(data) == 2
    assert data == {"cells": {}}


@pytest.mark.parametrize("bad", [0, -1, "two", None, 2.5, True])
def test_a_malformed_json_value_is_fatal(bad):
    with pytest.raises(ValidationError):
        take_version({"version": bad}, "probe.json")


# ── the chain ─────────────────────────────────────────────────────────────

def test_every_step_runs_exactly_once_and_in_order(monkeypatch):
    calls: list[int] = []

    def _mark(n):
        def step(data):
            calls.append(n)
            return {**data, f"step{n}": True}
        return step

    monkeypatch.setattr(fv, "CURRENT_FORMAT", 4)
    monkeypatch.setattr(fv, "STEPS", {1: _mark(1), 2: _mark(2), 3: _mark(3)})

    out = upgrade_data({}, 1, "probe.sexp")

    assert calls == [1, 2, 3]
    assert out == {"step1": True, "step2": True, "step3": True}


def test_a_file_already_at_current_runs_no_step(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(fv, "CURRENT_FORMAT", 4)
    monkeypatch.setattr(fv, "STEPS", {1: lambda d: calls.append(1) or d,
                                     2: lambda d: calls.append(2) or d,
                                     3: lambda d: calls.append(3) or d})
    assert upgrade_data({"cells": {}}, 4) == {"cells": {}}
    assert calls == []


def test_a_hole_in_the_chain_is_fatal(monkeypatch):
    """A missing converter is an internal error, never a reason to skip a step."""
    monkeypatch.setattr(fv, "CURRENT_FORMAT", 4)
    monkeypatch.setattr(fv, "STEPS", {1: fv._step_1_to_2, 3: fv._step_1_to_2})
    with pytest.raises(ValidationError) as excinfo:
        upgrade_data({}, 1, "probe.sexp")
    assert "converter" in str(excinfo.value)


def test_a_raising_step_propagates_and_leaves_the_input_alone(monkeypatch):
    """The in-memory half must be all-or-nothing: the on-disk half (Т4) decides
    what to write only after this returns."""
    original = {"cells": {"a": {}}}

    def boom(_data):
        raise RuntimeError("step failed")

    monkeypatch.setattr(fv, "CURRENT_FORMAT", 3)
    monkeypatch.setattr(fv, "STEPS", {1: fv._step_1_to_2, 2: boom})

    with pytest.raises(RuntimeError):
        upgrade_data(original, 1, "probe.sexp")
    assert original == {"cells": {"a": {}}}


def test_upgrade_refuses_a_file_newer_than_this_build():
    """Without the refusal a newer file would fall through the loop untouched
    and be read as if it were current — the exact failure the number prevents."""
    with pytest.raises(ValidationError):
        upgrade_data({}, CURRENT_FORMAT + 1, "probe.sexp")


# ── the on-disk probe ─────────────────────────────────────────────────────

def test_read_version_parses_for_real(tmp_path):
    p = tmp_path / "c.sexp"
    p.write_text(_wrap("  (version 2)\n  (cells)\n"), encoding="utf-8")
    assert read_version(p) == 2


def test_read_version_reports_1_when_there_is_no_number(tmp_path):
    p = tmp_path / "c.sexp"
    p.write_text(_wrap("  (cells)\n"), encoding="utf-8")
    assert read_version(p) == 1


def test_read_version_reads_a_json_config_too(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"version": 2, "cells": {}}', encoding="utf-8")
    assert read_version(p) == 2


def test_read_version_refuses_a_newer_file(tmp_path):
    p = tmp_path / "c.sexp"
    p.write_text(_wrap(f"  (version {CURRENT_FORMAT + 1})\n"), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_version(p)


def test_read_version_of_a_missing_file_is_1(tmp_path):
    """No error of its own: the callers already disagree on purpose about a
    missing config (includes.py fatals, config_writer returns {})."""
    assert read_version(tmp_path / "absent.sexp") == 1


def test_read_version_is_cached_by_mtime_ns(tmp_path):
    p = tmp_path / "c.sexp"
    p.write_text(_wrap("  (version 2)\n"), encoding="utf-8")
    first = p.stat()
    assert read_version(p) == 2

    p.write_text(_wrap("  (version 1)\n"), encoding="utf-8")
    os.utime(p, ns=(first.st_atime_ns, first.st_mtime_ns))
    assert read_version(p) == 2, "the same mtime_ns must be a cache HIT"

    _bump_mtime_forward(p)
    assert read_version(p) == 1, "a real edit must be a MISS on its own"


# ── the number no longer trips the include merge ───────────────────────────

def test_a_number_in_an_included_file_no_longer_fatals(tmp_path):
    """Measured before the fix (plan §10.2): `(version 1)` inside an INCLUDED
    file was a fatal — `_resolve` sees every root key of a non-root file as
    unsupported. Taking the number out at the parse site fixes it for every
    reader at once, and this is the end-to-end proof of that."""
    (tmp_path / "child.sexp").write_text(
        _wrap("  (version 1)\n  (cells)\n"), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(_wrap('  (include "child.sexp")\n'), encoding="utf-8")

    cfg, _ctx = load_config(str(root))
    assert cfg.cells == {}


def test_a_newer_number_in_an_included_file_is_refused(tmp_path):
    (tmp_path / "child.sexp").write_text(
        _wrap(f"  (version {CURRENT_FORMAT + 1})\n  (cells)\n"), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(_wrap('  (include "child.sexp")\n'), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(str(root))


# ── the pair stays lossless ───────────────────────────────────────────────

def test_adding_the_number_does_not_change_the_parsed_content():
    """The whole of the 1 -> 2 step: the text WITH the number parses to exactly
    the dict the same content without it parses to — nothing but the number
    moved. «Ничего не потерялось»."""
    body = '  (cells)\n  (layer "B.Cu")\n'
    assert sexp_to_dict(_wrap("  (version 2)\n" + body)) == sexp_to_dict(_wrap(body))


def test_the_written_number_does_not_change_the_round_trip():
    """Staged for Т3: once the writer stamps `(version CURRENT)` first, this is
    the proof the stamp costs the content nothing. Green today as well — the
    parse layer takes whatever number it finds out of the dict, and the writer
    does not stamp yet.

    Compared against `_strip_defaults`, not against `data` directly: the s-expr
    writer legitimately omits any field equal to its own dataclass default
    (design grammar §3.1) — that is the existing bijectivity contract, not a
    loss introduced by the number."""
    data = {"layer": "B.Cu", "cells": {"c": {"components": [{"role": "R1"}]}}}
    assert sexp_to_dict(dict_to_sexp(data)) == _strip_defaults(data)


def test_the_probe_cache_never_leaks_between_paths(tmp_path):
    """A cell the cache key must get right: two files, one version each."""
    a = tmp_path / "a.sexp"
    b = tmp_path / "b.sexp"
    a.write_text(_wrap("  (version 2)\n"), encoding="utf-8")
    b.write_text(_wrap("  (version 1)\n"), encoding="utf-8")
    assert (read_version(a), read_version(b)) == (2, 1)
    assert (read_version(b), read_version(a)) == (1, 2)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
