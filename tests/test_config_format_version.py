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
import ast
import json
import os
import sys
from pathlib import Path

import pytest

from kicadstamp.config import format_version as fv
from kicadstamp.config.format_version import (
    CURRENT_FORMAT,
    lift_loaded_dict,
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


def test_a_newer_file_is_refused_by_name_even_with_a_section_after_it():
    """Д2 of the Т2 acceptance (the surviving mutation G5). The refusal has to
    name the FILE. Every other "newer" cell has nothing after `(version N)`, so
    the root loop never rebinds `path` and the message text was never checked —
    which is exactly why the mutation that restores the defect lived."""
    with pytest.raises(ValidationError) as excinfo:
        sexp_to_dict(_wrap(f"  (version {CURRENT_FORMAT + 1})\n  (cells)\n"),
                     path="/tmp/profiles/p/c.sexp")
    text = str(excinfo.value)
    assert "c.sexp" in text
    assert "<cells>" not in text


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
        def step(data, _ctx):
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
    monkeypatch.setattr(fv, "STEPS", {1: lambda d, _c: calls.append(1) or d,
                                     2: lambda d, _c: calls.append(2) or d,
                                     3: lambda d, _c: calls.append(3) or d})
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

    def boom(_data, _ctx):
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


# ── Т2: the lift is the READER's job, by construction (decision A) ─────────

def _stepped(monkeypatch, *, current=3, mark="lifted"):
    """A fake chain with CURRENT=current and one MARKING step per version below
    it, so a cell can SEE whether the lift ran."""
    calls: list[int] = []

    def _mark(n):
        def step(data, _ctx):
            calls.append(n)
            return {**data, mark: n}
        return step

    monkeypatch.setattr(fv, "STEPS", {v: _mark(v) for v in range(1, current)})
    monkeypatch.setattr(fv, "CURRENT_FORMAT", current)
    return calls


def test_the_parse_lifts_by_default_even_when_nobody_asks_for_the_number(monkeypatch):
    """The cell the trap decision (A) rests on: a caller that never passes
    `version_out` still receives content of the CURRENT format, so it cannot
    take format-2 content for current once a real step exists."""
    calls = _stepped(monkeypatch, current=3)
    back = sexp_to_dict(_wrap("  (version 2)\n  (cells)\n"))
    assert calls == [2], "the 2 -> 3 step must have run"
    assert back == {"cells": {}, "lifted": 2}


def test_the_raw_flag_leaves_the_content_alone_but_still_reports_the_number(monkeypatch):
    """A converter gets the file as written, plus the number it carried — the
    two facts it needs to put the number back when it rewrites."""
    calls = _stepped(monkeypatch, current=3)
    found: list[int] = []
    back = sexp_to_dict(_wrap("  (version 2)\n  (cells)\n"),
                        version_out=found, upgrade=False)
    assert calls == []
    assert back == {"cells": {}}
    assert found == [2]


def test_a_step_that_needs_the_file_path_refuses_a_bare_string(monkeypatch):
    """Денис's caveat 2: a step building an identity (UUID) seeds it from the
    profile path, so a parse of a bare string must REFUSE, not invent a seed —
    two machines seeding differently would mint two UUIDs for one record."""
    def _needs_path(data, ctx):
        if ctx.path == "<config>":
            fv.refuse_step(1, "the profile path", ctx.path)
        return data

    monkeypatch.setattr(fv, "STEPS", {1: _needs_path})
    monkeypatch.setattr(fv, "CURRENT_FORMAT", 2)

    with pytest.raises(ValidationError, match="cannot run here"):
        sexp_to_dict(_wrap("  (cells)\n"))
    assert sexp_to_dict(_wrap("  (cells)\n"),
                        path="profiles/p/config.sexp") == {"cells": {}}


def test_a_step_that_needs_the_board_refuses_at_parse_time(monkeypatch):
    """The zero-origin step (Р38) changes the MEANING of zero and needs the live
    board — it cannot run inside a read at all, and says so."""
    def _needs_board(data, ctx):
        if ctx.at_parse_time:
            fv.refuse_step(1, "the live board", ctx.path)
        return data

    monkeypatch.setattr(fv, "STEPS", {1: _needs_board})
    monkeypatch.setattr(fv, "CURRENT_FORMAT", 2)

    with pytest.raises(ValidationError, match="cannot run here"):
        sexp_to_dict(_wrap("  (cells)\n"), path="profiles/p/config.sexp")
    assert upgrade_data({"cells": {}}, 1, "profiles/p/config.sexp",
                        at_parse_time=False) == {"cells": {}}


def test_the_number_on_disk_is_reported_even_when_there_is_none():
    """`version_out` answers what the FILE says (1 = no number), not what this
    build understands — Т4's sweep reads it to decide what to write."""
    found: list[int] = []
    sexp_to_dict(_wrap("  (cells)\n"), version_out=found)
    assert found == [1]


def test_read_version_does_not_lift(monkeypatch, tmp_path):
    """The probe wants what is ON DISK, not a transformed copy: lifting here
    would pay a whole step (and, at 2 -> 3, mint UUIDs) just to read one int."""
    calls = _stepped(monkeypatch, current=3)
    p = tmp_path / "c.sexp"
    p.write_text(_wrap("  (version 2)\n  (cells)\n"), encoding="utf-8")
    assert read_version(p) == 2
    assert calls == []


@pytest.mark.parametrize("suffix", [".sexp", ".json"])
def test_read_version_refuses_a_newer_file_in_both_formats(tmp_path, suffix):
    """F9 of the Т1 acceptance: only the .sexp row of this cell existed, so the
    JSON branch's refusal could be deleted with nothing going red (rule 35 —
    a row naming two branches must be checked on both)."""
    p = tmp_path / f"c{suffix}"
    if suffix == ".sexp":
        p.write_text(_wrap(f"  (version {CURRENT_FORMAT + 1})\n"), encoding="utf-8")
    else:
        p.write_text(json.dumps({"version": CURRENT_FORMAT + 1}), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_version(p)


def test_the_writer_writes_the_number_first_and_only_once():
    """The plan's Т3 cell, landed early because the raw path needs the
    parameter: a `version` key inside the dict never duplicates the node."""
    text = dict_to_sexp({"version": 1, "cells": {}}, format_number=2)
    assert text.splitlines()[1].strip() == "(version 2)"
    assert text.count("(version") == 1


def test_the_writer_stamps_the_current_number_by_default():
    """Т3 flipped this default: every writer stamps the number itself, so a new
    file is born in the current format and no write site can forget. (Т2 shipped
    the opposite default on purpose — the plumbing landed with its first user —
    and this row IS the flip.)"""
    assert dict_to_sexp({}).splitlines()[1].strip() == f"(version {CURRENT_FORMAT})"
    assert dict_to_sexp({"cells": {}}).count("(version") == 1
    # The number still is NEVER taken from the dict.
    assert dict_to_sexp({"version": 1, "cells": {}}).count("(version") == 1


def test_a_raw_rewrite_puts_back_the_number_it_read():
    """Денис's caveat 1: a raw reader that writes back must restore the SAME
    number it read — stamping the CURRENT one on unlifted content would be
    harmless at 1 -> 2 and corruption at 2 -> 3."""
    found: list[int] = []
    data = sexp_to_dict(_wrap("  (version 1)\n  (cells)\n"),
                        version_out=found, upgrade=False)
    assert found == [1]
    again = dict_to_sexp(data, format_number=found[0])
    assert again.splitlines()[1].strip() == "(version 1)"
    assert sexp_to_dict(again, version_out=[]) == {"cells": {}}


def test_a_json_config_is_lifted_inside_the_parse_too(monkeypatch):
    """Both formats go through the same decision, or they drift apart."""
    calls = _stepped(monkeypatch, current=3, mark="lifted")
    assert lift_loaded_dict({"version": 2, "cells": {}}, "p.json") == {
        "cells": {}, "lifted": 2}
    assert calls == [2]


def test_lift_loaded_dict_reports_the_number_while_leaving_the_content_alone():
    data = {"version": 2, "cells": {}}
    assert lift_loaded_dict(data, "p.json", upgrade=False) == {"cells": {}}


def _adds_a_cell(n):
    """A step whose effect is observable through a KNOWN key, so the lift can be
    asserted on the loaded Config instead of on a mock.

    It marks ONLY a file that itself declares `cells:` — an include graph has
    several files, and a step that created cells everywhere would collide in the
    dict-section merge (measured: "duplicate cells key", my own cell's bug, not
    the code's)."""
    def step(data, _ctx):
        if "cells" not in data:
            return data
        cells = dict(data["cells"])
        cells[f"lifted{n}"] = {"components": []}
        return {**data, "cells": cells}
    return step


@pytest.mark.parametrize("kind", ["json-root", "json-included"])
def test_a_json_config_is_lifted_where_it_is_parsed(monkeypatch, tmp_path, kind):
    """Every reader, both formats. The JSON side goes through lift_loaded_dict,
    and for an INCLUDED file the lift has to happen BEFORE the merge — the
    cell's key arrives through `cells`, which the merge knows how to carry."""
    monkeypatch.setattr(fv, "CURRENT_FORMAT", 3)
    monkeypatch.setattr(fv, "STEPS", {1: fv._step_1_to_2, 2: _adds_a_cell(2)})

    if kind == "json-root":
        root = tmp_path / "root.json"
        root.write_text(json.dumps({"version": 2, "cells": {}}), encoding="utf-8")
    else:
        (tmp_path / "child.json").write_text(
            json.dumps({"version": 2, "cells": {}}), encoding="utf-8")
        root = tmp_path / "root.sexp"
        root.write_text(_wrap('  (include "child.json")\n'), encoding="utf-8")

    cfg, _ctx = load_config(str(root))
    assert "lifted2" in cfg.cells, f"the JSON parse did not lift ({kind})"


def test_a_reader_that_opens_the_profile_itself_lifts_too(monkeypatch, tmp_path):
    """Not only the shared load path: a reader that opens the profile on its own
    must lift as well, or the two disagree about the content of one file."""
    from kicadstamp.adapter_factory import _read_root_dict

    monkeypatch.setattr(fv, "CURRENT_FORMAT", 3)
    monkeypatch.setattr(fv, "STEPS", {1: fv._step_1_to_2, 2: _adds_a_cell(2)})

    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"version": 2, "cells": {}}), encoding="utf-8")
    assert "lifted2" in _read_root_dict(p)["cells"]


# ── the reader table (Д1 of the Т2 acceptance) ─────────────────────────────
# Every reader × every starting format, with COUNTING steps: each step must run
# exactly once, and only for the versions BELOW the file's own. Д1 was found by
# a probe (diagnostics/probe_format_double_lift.py) precisely because this was a
# case and not a rule — config_writer._read_data lifted a .sexp a second time,
# so a file already at the current format went through the whole chain again.
_READER_NAMES = [
    "includes._load_config_file",
    "config_writer._read_data",
    "config_writer._load_data_tolerant",
    "config_io.load_data",
    "adapter_factory._read_root_dict",
    "cli_common._read_root_yaml",
    "net_trace_extract.read_net_trace_flags",
    "format_version.read_version",
]

# The probe reads the file as it IS — it must run no step at all.
_READERS_THAT_DO_NOT_LIFT = {"format_version.read_version"}


def _call_reader(name: str, path: Path):
    """One call per reader. Imports are local: several of these modules sit on
    the CLI's early path on purpose and import the config package lazily."""
    if name == "includes._load_config_file":
        from kicadstamp.config.includes import _load_config_file
        return _load_config_file(path)
    if name == "config_writer._read_data":
        from kicadstamp.config_writer import _read_data
        return _read_data(Path(path))
    if name == "config_writer._load_data_tolerant":
        from kicadstamp.config_writer import _load_data_tolerant
        return _load_data_tolerant(Path(path))
    if name == "config_io.load_data":
        from gui.config_io import load_data
        return load_data(Path(path))
    if name == "adapter_factory._read_root_dict":
        from kicadstamp.adapter_factory import _read_root_dict
        return _read_root_dict(Path(path))
    if name == "cli_common._read_root_yaml":
        from kicadstamp.cli_common import _read_root_yaml
        return _read_root_yaml(Path(path))
    if name == "net_trace_extract.read_net_trace_flags":
        from kicadstamp.net_trace_extract import read_net_trace_flags
        return read_net_trace_flags(str(path), "N")
    if name == "format_version.read_version":
        from kicadstamp.config.format_version import read_version
        return read_version(path)
    raise AssertionError(name)  # pragma: no cover


@pytest.mark.parametrize("reader_name", _READER_NAMES)
@pytest.mark.parametrize("suffix", [".sexp", ".json"])
@pytest.mark.parametrize("fmt", [1, 2, 3])
def test_every_reader_runs_each_step_exactly_once(monkeypatch, tmp_path,
                                                  reader_name, suffix, fmt):
    calls: list[str] = []

    def _count(name):
        def step(data, _ctx):
            calls.append(name)
            return data
        return step

    monkeypatch.setattr(fv, "CURRENT_FORMAT", 3)
    monkeypatch.setattr(fv, "STEPS", {1: _count("1->2"), 2: _count("2->3")})

    p = tmp_path / f"f{fmt}{suffix}"
    if suffix == ".sexp":
        body = "" if fmt == 1 else f"  (version {fmt})\n"
        p.write_text(f"(kicadstamp-config\n{body}  (cells)\n)\n", encoding="utf-8")
    else:
        payload = {"cells": {}} if fmt == 1 else {"version": fmt, "cells": {}}
        p.write_text(json.dumps(payload), encoding="utf-8")

    _call_reader(reader_name, p)

    if reader_name in _READERS_THAT_DO_NOT_LIFT:
        expected: list[str] = []
    else:
        expected = [n for v, n in ((1, "1->2"), (2, "2->3")) if v >= fmt]
    assert calls == expected, (
        f"{reader_name} on a format-{fmt} {suffix} file ran {calls}, "
        f"expected {expected}")


# ── the structural cell ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCANNED_ROOTS = ("kicadstamp", "gui", "mcp_server", "tools")
# NOT scanned: kicadstamp/diagnostics/ — measuring rigs, not ship code (rule 34
# keeps them in the tree on purpose, and a rig may read raw whenever it likes).
_SCAN_SKIP_DIRS = {"diagnostics", "__pycache__"}

# Callers allowed to read the file's OWN bytes (upgrade=False), keyed by
# (path relative to the repo root, enclosing function).
#
# ONE entry in Т2: the on-disk probe. It reads the number precisely BECAUSE it
# must not transform anything — it reports what the file carries, and Т4 decides
# whether to write. Lifting there would pay a whole step just to read one int.
#
# The three format converters (tools/convert_rules_to_chains.py,
# tools/sexp_config_convert.py, kicadstamp/tree_mount_convert.py) are NOT here
# yet, and that is deliberate: a raw reader that writes back must be able to put
# the number it READ back (Денис, caveat 1), and that needs
# `dict_to_sexp(format_number=...)` — Т3's parameter. Until then they take the
# default lift, which at 1 -> 2 is the identity. Half-wired would be worse: the
# number would be dropped on rewrite with nothing to catch it.
_RAW_READERS: set[tuple[str, str]] = {
    ("kicadstamp/config/format_version.py", "_read_version_uncached"),
}


# Callers that parse text with NO file behind it, so they cannot pass `path=`
# (Д3 of the Т2 acceptance). Empty by design: every config parse in the ship
# code reads a real file, whose path is ALWAYS available — and a step that needs
# it (UUID, format 3) refuses "<config>" rather than inventing a seed. A truly
# pathless call would have to be listed here with its reason.
_PATHLESS_READERS: set[tuple[str, str]] = set()


def _sexp_to_dict_call_sites() -> list[tuple[str, str, bool, bool]]:
    """[(relative path, enclosing function, passes upgrade=False, passes
    path=)] for every `sexp_to_dict(...)` call in the ship code."""
    sites: list[tuple[str, str, bool, bool]] = []

    class _Finder(ast.NodeVisitor):
        def __init__(self, relpath: str) -> None:
            self.relpath = relpath
            self.owners: list[str] = []

        def visit_FunctionDef(self, node):
            self.owners.append(node.name)
            self.generic_visit(node)
            self.owners.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            called = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute) else None)
            if called == "sexp_to_dict":
                raw = any(kw.arg == "upgrade"
                          and isinstance(kw.value, ast.Constant)
                          and kw.value.value is False
                          for kw in node.keywords)
                has_path = any(kw.arg == "path" for kw in node.keywords)
                sites.append((self.relpath,
                              self.owners[-1] if self.owners else "<module>",
                              raw, has_path))
            self.generic_visit(node)

    for root_name in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root_name).rglob("*.py")):
            if _SCAN_SKIP_DIRS.intersection(path.parts):
                continue
            _Finder(str(path.relative_to(_REPO_ROOT))).visit(
                ast.parse(path.read_text(encoding="utf-8")))
    return sites


def test_only_documented_callers_read_the_raw_file():
    """Т2's structural cell (plan §Т2). The default now LIFTS, so the only way
    to receive content of an older format is to say `upgrade=False` — and that
    has to be a conscious, listed decision, because such a caller also owes the
    file the number it read (Денис, caveat 1)."""
    sites = _sexp_to_dict_call_sites()
    # Sanity by ANCHOR, not by count: a scan that has gone blind (19 call sites
    # measured 24.09.2026, diagnostics excluded) must FAIL rather than pass the
    # cell vacuously (rule 38).
    anchors = {
        ("kicadstamp/config/includes.py", "_load_config_file"),
        ("kicadstamp/config/format_version.py", "_read_version_uncached"),
    }
    found = {(relpath, owner) for relpath, owner, _, _ in sites}
    assert anchors <= found, (
        f"the scan did not even see {sorted(anchors - found)} — a blind scan "
        "must not pass this cell")

    raw = {(relpath, owner) for relpath, owner, is_raw, _ in sites if is_raw}
    assert raw == _RAW_READERS, (
        "these callers read the file RAW without being on the documented list: "
        f"{sorted(raw - _RAW_READERS)}")


def test_every_config_parse_names_the_file_it_reads():
    """Д3 of the Т2 acceptance. A file-based parse that does not pass `path=`
    loses two things: the refusal names "<config>" instead of the file, and a
    step that needs the profile path (UUID, format 3) refuses although the path
    was right there."""
    no_path = {(relpath, owner)
               for relpath, owner, _, has_path in _sexp_to_dict_call_sites()
               if not has_path}
    assert no_path == _PATHLESS_READERS, (
        "these callers parse a file without passing path=: "
        f"{sorted(no_path - _PATHLESS_READERS)}")


# ── Т3: the ONE config writer, and its `.bak` contract ─────────────────────

def test_write_config_file_stamps_the_number_for_a_new_file(tmp_path):
    from kicadstamp.config_writer import write_config_file

    p = tmp_path / "new.sexp"
    write_config_file(p, {"cells": {}})
    assert p.read_text(encoding="utf-8").splitlines()[1].strip() == (
        f"(version {CURRENT_FORMAT})")
    assert list(tmp_path.glob("new.sexp.bak.*")) == [], "a fresh file has nothing to lose"


def test_write_config_file_takes_one_bak_when_the_file_is_older(tmp_path):
    """The `.bak` the whole decision (Denis, 24.09.2026) is about: this write
    changes the FORMAT, so the old-format bytes survive nowhere else — the reader
    already lifted the content in memory."""
    from kicadstamp.config_writer import write_config_file

    p = tmp_path / "old.sexp"
    original = "(kicadstamp-config\n  (cells)\n)\n"
    p.write_text(original, encoding="utf-8")

    write_config_file(p, {"cells": {}})

    baks = list(tmp_path.glob("old.sexp.bak.*"))
    assert len(baks) == 1
    assert baks[0].read_text(encoding="utf-8") == original, "the PREVIOUS bytes"
    assert f"(version {CURRENT_FORMAT})" in p.read_text(encoding="utf-8")


def test_write_config_file_leaves_a_current_file_without_a_bak(tmp_path):
    from kicadstamp.config_writer import write_config_file

    p = tmp_path / "cur.sexp"
    p.write_text(_wrap(f"  (version {CURRENT_FORMAT})\n  (cells)\n"), encoding="utf-8")

    write_config_file(p, {"cells": {}})

    assert list(tmp_path.glob("*.bak.*")) == []


def test_write_config_file_can_be_asked_for_the_copy_always(tmp_path):
    """A one-time migration changes CONTENT irreversibly, so it must be
    reversible whatever the file's format is — that is what `always_backup` is
    for, and why the migration tool no longer takes its own copy (two copies of
    one file was measured there)."""
    from kicadstamp.config_writer import write_config_file

    p = tmp_path / "mig.sexp"
    original = _wrap(f"  (version {CURRENT_FORMAT})\n  (cells)\n")
    p.write_text(original, encoding="utf-8")

    write_config_file(p, {"cells": {}}, always_backup=True)

    baks = list(tmp_path.glob("mig.sexp.bak.*"))
    assert len(baks) == 1
    assert baks[0].read_text(encoding="utf-8") == original


def test_write_config_file_writes_json_with_the_number_first(tmp_path):
    """The JSON rule of Т3, read back by the product's OWN reader: the number
    goes in first and comes out on read, so both formats behave alike."""
    from kicadstamp.config_writer import read_data, write_config_file

    p = tmp_path / "c.json"
    write_config_file(p, {"cells": {"a": {}}})

    raw = json.loads(p.read_text(encoding="utf-8"))
    assert list(raw)[0] == "version"
    assert raw["version"] == CURRENT_FORMAT
    assert read_data(p) == {"cells": {"a": {}}}


def test_write_config_file_refuses_to_overwrite_a_file_that_became_newer(tmp_path):
    """A newer file (another machine lifted it and Syncthing delivered it) must
    stop the write — our content is older, so writing would destroy what we do
    not know. OSError, not a bare ValidationError: this path is reached from Qt
    slots, where an escaping non-OSError aborts PyQt6."""
    from kicadstamp.config_writer import write_config_file

    p = tmp_path / "newer.sexp"
    text = _wrap(f"  (version {CURRENT_FORMAT + 1})\n  (cells)\n")
    p.write_text(text, encoding="utf-8")

    with pytest.raises(OSError):
        write_config_file(p, {"cells": {}})
    assert p.read_text(encoding="utf-8") == text, "untouched"


# ── the structural cell for the WRITER side ────────────────────────────────

# Config-text writes allowed to bypass write_config_file, keyed by
# (path relative to the repo root, enclosing function) -> the reason.
# The scan below finds `X.write_text(dict_to_sexp(...))` and
# `f.write(dict_to_sexp(...))`; it does NOT find a write of a text built earlier
# (config_rename.write_profile_files and schematic_editing both write a
# pre-built string, and both take their own unconditional `.bak` — the former
# writes profile configs, the latter a schematic's text, which is not a config).
_CONFIG_WRITE_BYPASSES: dict[tuple[str, str], str] = {
    ("gui/include_recovery.py", "_create_empty"): (
        "creates the missing include file and deliberately does NOT create "
        "parent directories (documented: a missing parent is more likely a wrong "
        "path than a directory worth inventing). The format number still comes "
        "from dict_to_sexp."),
    ("tools/convert_rules_to_chains.py", "_write_raw"): (
        "the raw rule->chain converter: it reads WITHOUT aliases and writes back "
        "the number it READ (Т3b), so it cannot go through the config writer"),
    ("tools/sexp_config_convert.py", "_write_dict"): (
        "the s-expr <-> YAML converter: its output is the OTHER format, not a "
        "config-graph file"),
    ("tools/generate_config.py", "<module>"): "one-time generator (see the row below)",
    ("tools/generate_test_profile.py", "main"): (
        "a one-time generator that AUTHORS a fresh file from scratch; it still "
        "gets the CURRENT number from dict_to_sexp inside"),
    ("tools/generate_10cl006.py", "write_sexp"): "one-time generator (see above)",
}


def _config_text_write_sites() -> set[tuple[str, str]]:
    """(relpath, enclosing function) for every `write_text(dict_to_sexp(...))` /
    `write(dict_to_sexp(...))` in the ship code."""
    sites: set[tuple[str, str]] = set()

    class _Finder(ast.NodeVisitor):
        def __init__(self, relpath: str) -> None:
            self.relpath = relpath
            self.owners: list[str] = []

        def visit_FunctionDef(self, node):
            self.owners.append(node.name)
            self.generic_visit(node)
            self.owners.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            is_write = (isinstance(func, ast.Attribute)
                        and func.attr in ("write_text", "write"))
            if is_write and node.args:
                first = node.args[0]
                inner = first.func if isinstance(first, ast.Call) else None
                called = (inner.id if isinstance(inner, ast.Name)
                          else inner.attr if isinstance(inner, ast.Attribute) else None)
                if called == "dict_to_sexp":
                    sites.add((self.relpath,
                               self.owners[-1] if self.owners else "<module>"))
            self.generic_visit(node)

    for root_name in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root_name).rglob("*.py")):
            if _SCAN_SKIP_DIRS.intersection(path.parts):
                continue
            _Finder(str(path.relative_to(_REPO_ROOT))).visit(
                ast.parse(path.read_text(encoding="utf-8")))
    return sites


def test_every_config_write_goes_through_the_one_config_writer():
    """Т3's structural cell (plan §Т3 + Denis's `.bak` decision). The writer owns
    the number, the atomic write and the `.bak`; a site that writes config text
    itself must be a conscious, listed decision — otherwise the fourth path added
    tomorrow is the one that forgets."""
    found = _config_text_write_sites()
    assert found == set(_CONFIG_WRITE_BYPASSES), (
        "these sites write config text outside write_config_file without being "
        f"listed: {sorted(found - set(_CONFIG_WRITE_BYPASSES))}; listed but not "
        f"found: {sorted(set(_CONFIG_WRITE_BYPASSES) - found)}")


def test_every_tracked_sexp_carries_the_current_format():
    """Т5's guard (plan §Т5): every `git ls-files '*.sexp'` carries
    `(version CURRENT)`. That is why the six fixtures were lifted in the SAME
    commit as the default flip, and it will force 2 -> 3 and 3 -> 4 to lift their
    own fixtures too, instead of letting every test run rewrite them and litter
    `.bak` files into the tree."""
    import subprocess

    names = subprocess.check_output(["git", "ls-files", "*.sexp"], text=True).split()
    assert names, "the file list is empty — this cell would pass vacuously"
    missing = [
        name for name in names
        if f"(version {CURRENT_FORMAT})" not in
        (_REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    assert missing == [], f"these tracked .sexp do not carry (version {CURRENT_FORMAT}): {missing}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
