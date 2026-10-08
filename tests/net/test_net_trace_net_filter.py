# tests/net/test_net_trace_net_filter.py
"""Part А2 cells (plan ``plan_2026_10_08_narrowing_net_traces_cost``): a
``net_traces:`` record that CANNOT own anything selected is not planned at all.

A record's copper lies on ITS net, so a record of another net can never own a
piece of the selection: planning it was pure cost (one board read + a handful of
Log lines per record — the live 19:41:36 click planned all 73 records of the
project for a ONE-cell read). The cells below pin:

  * only the records of the selected nets reach ``find_live_copper``;
  * a selection with NO copper never walks the records and never reads the board;
  * a record whose net CANNOT be resolved live is NOT dropped — its registry tier
    knows its copper by uuid (its piece is still subtracted);
  * the nets come from the planner's own resolver (``record_nets`` ->
    ``_item_net_name``), so the filter can never disagree with the plan.
"""
import pytest

import kicadstamp.net_trace_planner as planner_mod
from kicadstamp.config import format_version
from kicadstamp.net_trace_planner import record_nets
from kicadstamp.domain.board import Track
from kicadstamp.domain.geometry import BoardLayer, Vector2
from tests.net.test_net_trace_single_board_read import (  # noqa: F401 — helpers
    _CountingAdapter, _cfg, _fp, _narrow, _record, _track, _via,
    _write_registries,
)
from tests.fakes.format3 import det_uuid


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


def _planned_records(monkeypatch):
    """Names of the records that actually reach the matcher (the plan step)."""
    seen = []
    original = planner_mod.find_live_copper

    def _counted(adapter, nt, **kwargs):
        seen.append(str(getattr(nt, "net", "?")))
        return original(adapter, nt, **kwargs)

    monkeypatch.setattr(planner_mod, "find_live_copper", _counted)
    return seen


def _board(footprints_extra=()):
    fp = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0, ("ch0",))
    fields = {"C1": ("DA", "DAC_BUF")}
    return _CountingAdapter(fields, footprints=[fp, *footprints_extra])


# ── только цепи выделения ───────────────────────────────────────────────────

def test_only_the_records_of_the_selected_net_are_planned(gate, tmp_path,
                                                          monkeypatch):
    """K=3 records, the selection carries NET2 -> exactly ONE record is planned.

    Mutation: drop the net filter and all three are planned (the live 292-read
    shape comes straight back)."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1", net="NET1"), _record("NT2", net="NET2"),
                _record("NT3", net="NET3")])
    adapter = _board()
    seen = _planned_records(monkeypatch)

    prelude = _narrow(tmp_path, adapter, cfg, vias=[_via("sel-v", net="NET2")],
                      tracks=[])

    assert prelude is not None and prelude.refusal is None
    assert seen == ["NET2"], seen
    assert adapter.reads == [1, 1], adapter.reads


def test_a_selection_without_copper_neither_reads_nor_walks(gate, tmp_path,
                                                           monkeypatch):
    """Components only: not one record is planned and the board is not read.

    Mutation: walk anyway (the old behaviour) and both counters move."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1", net="NET1"), _record("NT2", net="NET2")])
    adapter = _board()
    seen = _planned_records(monkeypatch)

    prelude = _narrow(tmp_path, adapter, cfg, vias=[], tracks=[])

    assert prelude is not None and prelude.refusal is None
    assert seen == [], seen
    assert adapter.reads == [0, 0], adapter.reads


def test_a_piece_without_a_net_switches_the_filter_off(gate, tmp_path,
                                                       monkeypatch):
    """A selected piece with NO net cannot be told apart by net: every record is
    planned again (its uuid may be owned by any of them) — never dropped."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1", net="NET1"), _record("NT2", net="NET2")])
    adapter = _board()
    seen = _planned_records(monkeypatch)

    _narrow(tmp_path, adapter, cfg, vias=[_via("sel-v", net=None)], tracks=[])

    assert seen == ["NET1", "NET2"], seen


# ── неразрешимая цепь записи не отбрасывается молча ────────────────────────

def test_a_record_with_an_unresolvable_net_is_not_dropped(gate, tmp_path):
    """The record names a ``net_from_role`` this board has no role for, so its net
    cannot be resolved — and its registry tier still knows its copper by uuid.
    The selected via must therefore be SUBTRACTED, not quietly left in the read.

    Mutation: treat "unresolvable" as "different net" and the via stays."""
    _write_registries(tmp_path)
    nt = _record("NT1", net="NET1")
    nt.vias[0].net_from_role = "GONE_ROLE"
    nt.vias[0].net = None
    cfg = _cfg([nt])
    selected_via = _via("sel-v", net="OTHER")
    adapter = _board()
    _write_registries(tmp_path, via_entries={
        _via_key(nt): {"uuid": "sel-v", "x_mm": 0.0, "y_mm": 0.0, "net": "OTHER",
                       "drill_mm": 0.3, "diameter_mm": 0.6}})

    prelude = _narrow(tmp_path, adapter, cfg, vias=[selected_via], tracks=[])
    adapter_vias = [i.uuid for i in prelude.vias]

    assert adapter_vias == [], "the registry-owned piece must still be subtracted"


def _via_key(nt):
    from kicadstamp.net_trace_planner import net_trace_registry_key
    return net_trace_registry_key(nt, 0)


# ── одна итоговая строка Лога (А3) ─────────────────────────────────────────

def test_one_summary_line_names_what_was_checked_and_skipped(gate, tmp_path,
                                                            caplog):
    """А3: ONE INFO line per walk — how many records were checked, how many the
    net filter saved, and how many own selected copper (records, not pieces).

    Mutation: drop the line, or report the pieces instead of the records, and
    this cell fails (the selection owns exactly ONE record here)."""
    import logging

    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1", net="NET1"), _record("NT2", net="NET2"),
                _record("NT3", net="NET3")])
    anchor = _fp("U1", "FPGA", None, 10.0, 10.0)
    adapter = _board(footprints_extra=[anchor])
    adapter._fields["U1"] = ("FPGA", None)
    selected_via = _via("sel-v", net="NET2", x_mm=10.0, y_mm=10.0)
    adapter._vias = [selected_via]

    with caplog.at_level(logging.INFO):
        _narrow(tmp_path, adapter, cfg, vias=[selected_via], tracks=[])

    lines = [r.getMessage() for r in caplog.records
             if "own selected copper" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "1 record(s) checked" in lines[0], lines
    assert "2 skipped by net" in lines[0], lines
    assert "1 own selected copper" in lines[0], lines


# ── разрешитель цепей — планировщика ────────────────────────────────────────

def test_record_nets_uses_the_planners_own_resolver(gate):
    """A literal net: the resolver answers with the record's/items net, needs no
    anchor and never touches the board's copper."""
    nt = _record("NT1", net="NET1")
    adapter = _CountingAdapter({})
    nets, resolvable = record_nets(adapter, nt, {})
    assert (nets, resolvable) == ({"NET1"}, True)
    assert adapter.reads == [0, 0]
