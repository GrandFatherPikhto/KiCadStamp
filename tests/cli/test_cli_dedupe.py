# tests/cli/test_cli_dedupe.py
"""Cells for the `kicadstamp dedupe` command (plan_2026_10_01_dedupe_into_
kicadstamp, К2/§2.2 and §2.4).

Three promises the command makes, all pinned here:
  1. DEFAULT IS REPORT-ONLY — the old tools/dedupe_vias_tracks.py deleted by
     default; that is deliberately reversed, and remove_by_id must not be
     called even once without --apply;
  2. --apply is REFUSED without --config, and refused BEFORE the board is read,
     because a removal that cannot be journaled must not happen (§2.4);
  3. --config is NOT an addressing flag on this command: the adapter is always
     bare, so the flag must not reach the factory as a config_path — that is
     what the --help text promises, and a promise is what gets pinned.

The board stand-in is the shared tests/fakes/adapter.FakeAdapter, whose
remove_by_id records every call (so "deleted the extras" is distinguishable
from "touched the board at all").
"""
import logging
import sys
from types import SimpleNamespace

import pytest

import kicadstamp.cli as cli_mod
import kicadstamp.cli_main as cli_main_mod
from kicadstamp.board_dedupe import (ACTION_LOGGER_NAME, apply_summary,
                                     find_track_duplicate_groups,
                                     find_via_duplicate_groups, format_report)
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import PlacerError
from kicadstamp.utils.units import MM
from tests.fakes.adapter import FakeAdapter


def _mm(value: float) -> int:
    return int(round(value * MM))


def _via(uuid: str, net, x_mm: float, y_mm: float) -> Via:
    return Via(uuid=uuid, position=Vector2(_mm(x_mm), _mm(y_mm)),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


def _track(uuid: str, net, layer: BoardLayer, start_mm, end_mm) -> Track:
    return Track(uuid=uuid,
                 start=Vector2(_mm(start_mm[0]), _mm(start_mm[1])),
                 end=Vector2(_mm(end_mm[0]), _mm(end_mm[1])),
                 net_name=net, width_mm=0.25, layer=layer)


def _board() -> FakeAdapter:
    """One duplicate via pair, one duplicate track pair, and one lone item of
    each kind that must NOT be touched."""
    return FakeAdapter(
        vias=[_via("v1", "GND", 1.0, 2.0), _via("v2", "GND", 1.0, 2.0),
              _via("v3", "VCC", 9.0, 9.0)],
        tracks=[_track("t1", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0)),
                _track("t2", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0)),
                _track("t4", "SIG", BoardLayer.BL_F_Cu, (5.0, 5.0), (6.0, 6.0))],
    )


def _args(**extra) -> SimpleNamespace:
    base = dict(apply=False, config=None, timeout_ms=13, verbose=False)
    base.update(extra)
    return SimpleNamespace(**base)


def _patch_factory(monkeypatch, adapter, calls=None):
    """Route the command's adapter creation to `adapter`. The command imports
    create_board_adapter lazily, so the patch hits the FACTORY's own attribute —
    and `calls` records how it was called (the bare-mode promise)."""
    def _fake(timeout_ms=None, **kwargs):
        if calls is not None:
            calls.append({"timeout_ms": timeout_ms, **kwargs})
        return adapter
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _fake)


# ── default is report-only ──────────────────────────────────────────────────

def test_report_only_never_calls_remove_by_id(monkeypatch):
    """К2 (§2.2) — without --apply the command only LOOKS. remove_by_id is not
    called once, which is the difference between this command and the tool it
    replaces (that one deleted unless told --dry-run)."""
    adapter = _board()
    _patch_factory(monkeypatch, adapter)

    lines = cli_mod.cmd_dedupe(_args())

    assert adapter.removed == []
    assert adapter.refresh_count == 1
    assert len(lines) == 1


def test_the_printed_report_is_the_very_text_the_gui_copies(monkeypatch):
    """К2 (§2.2/§2.3) — the CLI output and the GUI's "Копировать" are one text:
    built by board_dedupe.format_report, not assembled again here."""
    adapter = _board()
    _patch_factory(monkeypatch, adapter)

    lines = cli_mod.cmd_dedupe(_args())
    expected = format_report(find_via_duplicate_groups(adapter.get_vias()),
                             find_track_duplicate_groups(adapter.get_tracks()))

    assert lines == [expected]
    assert "Duplicate via groups: 1" in expected
    assert "Total: 2 extra duplicate item(s); one copy per group is kept." in expected


def test_report_does_not_require_a_config(monkeypatch):
    """К2 (§2.2) — reading needs no profile: a report with no --config is a
    legal, complete answer (the flag is about the journal, not the scan)."""
    _patch_factory(monkeypatch, _board())
    lines = cli_mod.cmd_dedupe(_args(config=None))
    assert "Duplicate track groups: 1" in lines[0]


# ── --apply: refused without a profile, journaled with one ──────────────────

def test_apply_without_config_is_refused_before_the_board_is_read(monkeypatch):
    """К2 (§2.2/§2.4) — the refusal happens BEFORE create_board_adapter is even
    called: the factory is never reached, so no board is read and no deletion
    could have happened. A deletion that cannot be journaled does not occur."""
    calls = []
    _patch_factory(monkeypatch, _board(), calls)

    with pytest.raises(PlacerError) as ei:
        cli_mod.cmd_dedupe(_args(apply=True, config=None))

    assert calls == [], "the board was read despite the refusal"
    assert "--apply needs --config" in str(ei.value)


def test_apply_removes_only_the_extras_and_journals_each(caplog, monkeypatch):
    """К2 (§2.2/§2.4) — with --apply exactly the extras are deleted (the lone
    v3/t4 survive), and each deletion writes one actions.log line naming the
    UUID removed and the UUID kept."""
    adapter = _board()
    _patch_factory(monkeypatch, adapter)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        lines = cli_mod.cmd_dedupe(_args(apply=True, config="prof.sexp"))

    assert adapter.removed == ["v2", "t2"]
    assert lines[-1] == apply_summary(2, 2)

    journal = [r.getMessage() for r in caplog.records
               if r.name == ACTION_LOGGER_NAME]
    assert len(journal) == 2
    assert "removed via uuid=v2 net='GND'" in journal[0]
    assert "kept=v1" in journal[0]
    assert "removed track uuid=t2 net='SIG'" in journal[1]
    assert "kept=t1" in journal[1]


def test_the_config_flag_never_reaches_the_factory(monkeypatch):
    """К2 (§2.2) — the --help text says --config names ONLY the journal profile
    and that the adapter is always bare. This pins that promise in code: even
    WITH --config the factory is asked for use_store=False and no config_path,
    so the flag cannot change which copper is found."""
    calls = []
    _patch_factory(monkeypatch, _board(), calls)

    cli_mod.cmd_dedupe(_args(apply=True, config="prof.sexp"))

    assert calls == [{"timeout_ms": 13, "use_store": False}]


# ── the wiring ──────────────────────────────────────────────────────────────

def test_dedupe_is_a_real_subcommand():
    """К2 — it must be in _SUBCOMMANDS, otherwise the bare-config rewrite turns
    `kicadstamp dedupe` into `kicadstamp apply dedupe` and the command dies as an
    unrecognized argument."""
    assert "dedupe" in cli_main_mod._SUBCOMMANDS


def test_help_documents_the_apply_and_config_contract(monkeypatch, capsys):
    """К2 (§2.2) — `dedupe --help` must say out loud that --apply requires a
    profile and that --config is only the journal. A flag whose help hides this
    is how a user deletes copper with no record of it."""
    monkeypatch.setattr(sys, "argv", ["kicadstamp", "dedupe", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        cli_main_mod.main()
    assert exit_info.value.code == 0
    # argparse wraps the help text — compare with newlines collapsed.
    help_text = " ".join(capsys.readouterr().out.split())
    assert "REQUIRES --config" in help_text
    assert "actions.log" in help_text
    assert "always bare" in help_text


# NOTE: the ONE-LINE summary passed as `help=` to add_parser("dedupe", ...) is
# deliberately NOT asserted here. It is shown by the PARENT parser only, and
# `kicadstamp --help` never reaches it: the bare-config shorthand rewrites an
# unknown first token to `apply` (pre-existing behaviour, unrelated to dedupe),
# so `kicadstamp --help` prints apply's help. Asserting it would mean either a
# source-text grep or an unreachable path; the contract it would restate is
# already pinned by the --apply/--config help cells above, where a user reads it.


def test_main_prints_the_report_and_deletes_nothing_without_apply(
        monkeypatch, capsys):
    """К2 — the end-to-end default: dispatch prints the core report and the board
    is untouched. This is the shape Denis runs day to day."""
    adapter = _board()
    _patch_factory(monkeypatch, adapter)
    monkeypatch.setattr(sys, "argv", ["kicadstamp", "dedupe"])

    assert cli_main_mod.main() == 0

    out = capsys.readouterr().out
    assert "Duplicate via groups: 1" in out
    assert adapter.removed == []
