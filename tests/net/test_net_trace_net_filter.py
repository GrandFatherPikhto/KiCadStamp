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
import logging
from types import SimpleNamespace

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


# ── доделка 2: сухое сопоставление не топит Лог строками сужения ────────────

def _two_channel_board():
    """A board with TWO footprints of the SAME role on two sheets: the record's
    ``net_from_role`` search then has candidates to narrow (Channel_0 vs
    Channel_1) — exactly the step whose INFO lines Denis saw in the thousands.

    The sheet chain carries the component's OWN uuid LAST (resolve_sheet_path_names
    drops it — the same shape the adapter hands over)."""
    ch0 = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0, ("ch0", "u-c1"))
    ch1 = _fp("C2", "DA", "DAC_BUF", 0.0, 0.0, ("ch1", "u-c2"))
    return _CountingAdapter({"C1": ("DA", "DAC_BUF"), "C2": ("DA", "DAC_BUF")},
                            footprints=[ch0, ch1])


def _named_record(name):
    """ONE record whose via names its chain by ROLE (the live shape): the net is
    resolved live through the narrowing cascade, so every question about this
    record walks the cascade."""
    nt = _record(name, net="NOT_SEEN")
    nt.anchor_sheet = "Channel_0"
    nt.vias[0].net = None
    nt.vias[0].net_from_role = "DA"
    return nt


_SHEETS = {"ch0": "Channel_0", "ch1": "Channel_1"}


def _narrowing_lines(records, level):
    return [r.getMessage() for r in records if r.levelno == level
            and "narrowed" in r.getMessage()]


def test_the_dry_net_question_does_not_grow_the_log_with_k(gate, caplog):
    """Доделка 2 of plan_2026_10_08_narrowing_net_traces_cost: ONE read asks
    ``record_nets`` of EVERY record, so K records must not mean K (or more) INFO
    lines — the live "before" was 2 184 of them for one click. The steps still
    RUN, at DEBUG.

    Mutation: drop ``quiet=True`` (from `record_nets` or from its caller) and the
    INFO lines come back with K."""
    adapter = _two_channel_board()
    records = [_named_record(f"NT{i}") for i in range(1, 6)]

    with caplog.at_level(logging.DEBUG):
        for nt in records:
            record_nets(adapter, nt, _SHEETS, quiet=True)
        five = _narrowing_lines(caplog.records, logging.INFO)
        steps = _narrowing_lines(caplog.records, logging.DEBUG)
        caplog.clear()
        record_nets(adapter, records[0], _SHEETS, quiet=True)
        one = _narrowing_lines(caplog.records, logging.INFO)

    assert five == one == [], (
        "the number of INFO lines must not grow with the number of records")
    assert len(steps) >= 5, (
        f"the narrowing must still run (at DEBUG), saw {steps!r}")


def test_the_same_question_without_quiet_still_writes_info(gate, caplog):
    """The other half of the rule: the REDRAW's planning passes nothing, so its
    narrowing lines stay at INFO exactly as before — quiet is a parameter of one
    caller, never a global switch."""
    adapter = _two_channel_board()

    with caplog.at_level(logging.INFO):
        record_nets(adapter, _named_record("NT1"), _SHEETS)

    assert _narrowing_lines(caplog.records, logging.INFO), \
        "planning for real must keep saying what it narrowed"


def _board_with_anchor():
    """The two-channel board PLUS a resolvable anchor: a record's plan then really
    runs (anchor resolves), which is what the READ-ONLY doors ask of every record
    — the shape whose per-record lines Denis read in the live Log."""
    ch0 = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0, ("ch0", "u-c1"))
    ch1 = _fp("C2", "DA", "DAC_BUF", 0.0, 0.0, ("ch1", "u-c2"))
    anchor = _fp("U1", "FPGA", None, 10.0, 10.0)
    return _CountingAdapter({"C1": ("DA", "DAC_BUF"), "C2": ("DA", "DAC_BUF"),
                             "U1": ("FPGA", None)},
                            footprints=[ch0, ch1, anchor])


def _record_lines(records, level, needle):
    return [r.getMessage() for r in records if r.levelno == level
            and needle in r.getMessage()]


def test_the_plan_itself_is_quiet_only_when_asked(gate, caplog):
    """`quiet` reaches the PLAN: on a read-only door's question the per-record
    «… planned» line and the role search's steps go to DEBUG; the redraw's own
    plan (no quiet) keeps them at INFO.

    Mutation: ignore `quiet` in plan_net_traces and the live 458-line batch comes
    back for ONE click."""
    from kicadstamp.net_trace_planner import plan_net_traces

    adapter = _board_with_anchor()
    # Literal nets on its items: the plan's OWN line is what this cell watches
    # (the cascade's DEBUG is pinned by the `record_nets` cells above).
    with caplog.at_level(logging.DEBUG):
        plan_net_traces(adapter, [_record("NT1", net="NET1")], _SHEETS, quiet=True)
        quiet_planned = _record_lines(caplog.records, logging.INFO,
                                      "net_traces entry")
        caplog.clear()
        plan_net_traces(adapter, [_record("NT1", net="NET1")], _SHEETS)
        loud_planned = _record_lines(caplog.records, logging.INFO,
                                     "net_traces entry")

    assert quiet_planned == []
    assert len(loud_planned) == 1, loud_planned


def test_the_copper_identify_door_does_not_flood_the_log(gate, caplog):
    """«Whose copper is this?» asks EVERY record the same dry question, so its
    per-record lines are not news — they go to DEBUG, and the answer stands.

    The live click of 08.10 planned 73 records through this very door and wrote
    458 INFO lines into the Log (доделка 2 of plan_2026_10_08_narrowing_net_traces_cost)."""
    from gui.docks.copper_select import identify_selected_copper

    adapter = _board_with_anchor()
    selected_via = _via("sel-v", net="OTHER")
    adapter._vias = [selected_via]
    cfg = SimpleNamespace(net_traces=[_named_record(f"NT{i}") for i in range(1, 6)])
    reg = SimpleNamespace(entries={})

    with caplog.at_level(logging.DEBUG):
        result = identify_selected_copper(
            adapter, cfg, [selected_via], via_registry=reg, track_registry=reg,
            sheet_names=_SHEETS)
        info_planned = _record_lines(caplog.records, logging.INFO, "net_traces entry")
        info_steps = _narrowing_lines(caplog.records, logging.INFO)

    assert result.total == 1
    assert info_planned == [] and info_steps == []


def test_a_record_with_one_unresolvable_piece_is_not_dropped(gate):
    """C1 of Claude's acceptance — the honest cell the earlier one could not be:
    ONE piece's net does not resolve, the OTHER piece's net is NOT the selected
    one. That record must STILL not be dropped — its registry tier knows its
    copper by uuid and needs no net at all — so its selected piece IS claimed.

    The older cell (``test_a_record_with_an_unresolvable_net_is_not_dropped``)
    was blind twice over: with a SINGLE unresolvable item the nets set is EMPTY,
    so "nothing to compare" kept the record for an unrelated reason; and its
    assertion («the via left the read») also holds when the record is SKIPPED,
    because the registry pass («another record's copper») subtracts that copper
    on its own, before the walk. This cell asks the walk ITSELF, so the mutation
    «unresolvable counts as resolved» leaves nothing claimed."""
    from kicadstamp.registry import RegistryEntry
    from kicadstamp.selection_narrowing import read_net_trace_owned

    nt = _record("NT1", net="NET1")
    nt.vias[0].net_from_role = "GONE_ROLE"    # this piece: no such role anywhere
    nt.vias[0].net = None
    nt.tracks[0].net = "NET1"                 # the other piece: another net
    selected_via = _via("sel-v", net="OTHER")
    adapter = _board()
    adapter._vias = [selected_via]
    entries = {_via_key(nt): RegistryEntry(
        uuid="sel-v", x_mm=0.0, y_mm=0.0, net="OTHER",
        drill_mm=0.3, diameter_mm=0.6)}

    owned, _notes = read_net_trace_owned(
        [selected_via], [nt, _record("NT2", net="NET2")], adapter,
        via_entries=entries, track_entries={})

    assert list(owned) == ["sel-v"], (
        "the record with the unresolvable piece must still be checked: its "
        "registry tier claims the piece by uuid")
