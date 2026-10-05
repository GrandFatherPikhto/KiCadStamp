# tests/cli/test_cli_explode.py
"""Cells for the ``kicadstamp explode`` CLI (plan
``plan_2026_10_05_explode_r1_core.md`` §4).

The instance rule without --cluster, the refusal texts, the plan being
read-only, and ``status`` without a journal. The real board work is stubbed at
its two doors (`_adapter`, and the plan/journal functions), because those are
covered by tests/explode/.
"""
import pytest

from types import SimpleNamespace

from kicadstamp import explode_cli
from kicadstamp import explode_journal as journal_mod
from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.exceptions import PlacerError
from kicadstamp.explode import ExplodePlan, MovedInstance, NetTracePiece


def _cfg(entities=(), clones=()):
    return SimpleNamespace(entities=list(entities), clone_placements=list(clones))


# ── instance resolution without --cluster ───────────────────────────────────

def test_explicit_cluster_is_taken_as_is():
    assert explode_cli._resolve_instance(_cfg(), "c", "K", "S") == ("K", "S")
    assert explode_cli._resolve_instance(_cfg(), "c", "K", None) == ("K", None)


def test_one_config_record_chooses_it():
    e = SimpleNamespace(cell="c", cluster="K", sheet="S")
    assert explode_cli._resolve_instance(_cfg([e]), "c", None, None) == ("K", "S")


def test_no_record_refuses():
    with pytest.raises(PlacerError):
        explode_cli._resolve_instance(_cfg(), "c", None, None)


def test_several_records_refuse_with_the_list():
    e1 = SimpleNamespace(cell="c", cluster="A", sheet=None)
    e2 = SimpleNamespace(cell="c", cluster="B", sheet="S")
    with pytest.raises(PlacerError) as exc:
        explode_cli._resolve_instance(_cfg([e1, e2]), "c", None, None)
    assert "A" in str(exc.value) and "B/S" in str(exc.value)


# ── plan printing ───────────────────────────────────────────────────────────

def _plan():
    return ExplodePlan(
        cell_name="dac_buf", cluster="CELL", sheet=None, instance=(),
        area=Box2(pos=Vector2(0, 0), size=Vector2(2_000_000, 2_000_000)),
        instances=(MovedInstance(cluster="PIF", sheet=None, refs=("C1",),
                                 footprints=(object(),), copper=(),
                                 vector=(1_000_000, 0)),),
        table=(NetTracePiece(record="rec", kind="track", net="N", layer="F.Cu",
                             length_mm=3.2, uuid="u", touches="PIF",
                             ticked=True, item=object()),),
        moves=())


def test_format_plan_names_instances_and_ticks():
    text = "\n".join(explode_cli._format_plan(_plan()))
    assert "PIF" in text and "[x]" in text and "touches: PIF" in text


def test_plan_command_is_read_only(monkeypatch):
    """`plan` returns the lines and touches no journal."""
    import kicadstamp.config.loader as loader
    from kicadstamp import explode as explode_mod

    class _A:
        def close(self):
            pass

    monkeypatch.setattr(explode_cli, "_adapter", lambda p: _A())
    monkeypatch.setattr(loader, "load_config",
                        lambda p: (SimpleNamespace(entities=[],
                                                   clone_placements=[],
                                                   cells={}),
                                   SimpleNamespace(sheet_names={})))
    monkeypatch.setattr(explode_mod, "plan_explode", lambda *a, **k: _plan())
    args = SimpleNamespace(config="c.sexp", cell="dac_buf", cluster="K",
                           sheet=None, margin=5.0, gap=5.0,
                           explode_command="plan")
    lines = explode_cli.cmd_explode(args)
    assert lines and any("PIF" in line for line in lines)


# ── status ──────────────────────────────────────────────────────────────────

def test_status_without_a_journal(monkeypatch):
    class _A:
        def close(self):
            pass

    monkeypatch.setattr(explode_cli, "_adapter", lambda p: _A())
    monkeypatch.setattr(journal_mod, "journal_status", lambda a, *x, **k: None)
    assert "no explode journal" in explode_cli._status(
        SimpleNamespace(config="c.sexp"))[0]


def test_status_with_a_journal_names_it(monkeypatch):
    class _A:
        def close(self):
            pass

    journal = {"time": "2026-10-05 12:00:00", "cell": "dac_buf",
               "cluster": "CELL", "sheet": None, "items": {"a": 1, "b": 2}}
    monkeypatch.setattr(explode_cli, "_adapter", lambda p: _A())
    monkeypatch.setattr(journal_mod, "journal_status", lambda a, *x, **k: journal)
    text = explode_cli._status(SimpleNamespace(config="c.sexp"))[0]
    assert "dac_buf" in text and "2 item" in text
