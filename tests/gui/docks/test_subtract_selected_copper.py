# tests/gui/docks/test_subtract_selected_copper.py
"""С-2 «Subtract selected copper» — the worker cells (CellDock path).

plan_2026_10_06_prune_absent_cell_copper, С-2 (Denis 2026-10-06). The action that
removes the cell records the CURRENT selection names — and touches nothing else.

Boundary faked, product real: `create_board_adapter` (the selection read) and
`instance_record_copper_map` (the instance's dry run) are stubbed, so no board and
no socket is touched; the WORKER, the DECISION rule (kicadstamp/subtract_selection)
and the DOCK's own worker/finish halves are the product's.

What the core cells (tests/selection/test_subtract_selection.py) already pin and
these do not repeat: which TIER paired a record (registry uuid or exact geometry) —
here the map is the boundary, so a cell only asserts that the record whose live
copper IS in the selection goes, and that nothing else does.
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.docks.cell_editor import CellDock
from kicadstamp.absent_copper_prune import RecordCopperMap
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2


def _track(net, across, width=0.25):
    return {"net": net, "layer": "F.Cu", "width_mm": width,
            "start_along_mm": 0.0, "start_across_mm": across,
            "end_along_mm": 1.0, "end_across_mm": across}


def _via_record(across):
    return {"net": "N", "offset_along_mm": 0.0, "offset_across_mm": across,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _config_with_component_via():
    """A cell with a via at BOTH levels, the SAME index 0 in each (С-2а-1): the
    cell's own `vias[0]` and the slot of role DA carrying its own `vias[0]`."""
    return {
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": [{"role": "DA", "vias": [_via_record(5.0)]}],
            "vias": [_via_record(0.0)],
            "tracks": [_track(None, 0.0)],
            "clone_placements": [],
        }},
        "entities": [{"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF"}],
    }


def _config_data(tracks=None, entities=None):
    return {
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": [
                {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
                {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
            ],
            "vias": [],
            "tracks": list(tracks if tracks is not None
                           else [_track(None, 0.0), _track("GND", 2.0),
                                 _track("GND2", 4.0)]),
            "clone_placements": [],
        }},
        "entities": list(entities if entities is not None
                         else [{"name": "dac0", "cell": "dac_buf",
                                "cluster": "DAC_BUF"}]),
    }


@pytest.fixture(autouse=True)
def _restore_active_graph_root():
    """У3.5 (в), the same rig as tests/gui/docks/test_cell_editor.py: the format-3
    writer resolves a reference's UUID against the process-wide ACTIVE GRAPH ROOT.
    `_write_config` points it at the file it just wrote; this restores the
    previous value so the root never leaks between cells."""
    from kicadstamp.config_working_set import (active_graph_root,
                                               set_active_graph_root)

    previous = active_graph_root()
    yield
    set_active_graph_root(previous)


def _write_config(tmp_path, data=None):
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(data if data is not None else _config_data(),
                                   format_number=2), encoding="utf-8")
    # The Save cell reads the file the dock SAVED: that write is a format-3 one,
    # and it resolves references against this graph root (see the fixture above).
    from kicadstamp.config_working_set import set_active_graph_root
    set_active_graph_root(target)
    return target


def _live_track(uuid, net, x1, y1, x2, y2):
    return Track(uuid=uuid, net_name=net,
                 start=Vector2.from_xy_mm(x1, y1), end=Vector2.from_xy_mm(x2, y2),
                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _live_via(uuid, net, x, y):
    return Via(uuid=uuid, net_name=net, position=Vector2.from_xy_mm(x, y),
               drill_mm=0.3, diameter_mm=0.6)


class _Adapter:
    """The whole board the worker sees: the selection, and a close()."""

    def __init__(self, selected):
        self._selected = list(selected)
        self.closed = False

    def refresh_board(self):
        pass

    def get_selected_items(self):
        return list(self._selected)

    def close(self):
        self.closed = True


def _make_dock(main_window, tmp_path, data=None):
    target = _write_config(tmp_path, data)
    dock = CellDock(main_window)
    dock.set_root_path(target)
    dock.load_entry("dac_buf")
    return dock, target


def _payload(dock):
    return {
        "timeout_ms": 50,
        "components": list(dock._components),
        "vias": list(dock._vias),
        "tracks": list(dock._tracks),
        "root_path": str(dock._root_path),
        "cell_name": "dac_buf",
        "cluster": None,
        "sheet": None,
    }


def _fake_board(monkeypatch, selected):
    adapter = _Adapter(selected)
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kwargs: adapter)
    return adapter


def _fake_map(monkeypatch, maps: dict, default=None):
    """`instance_record_copper_map` by record name — the dry-run boundary."""
    def _stub(config_path, record_name, timeout_ms, cell_identity):
        return maps.get(record_name, default if default is not None
                        else RecordCopperMap())
    monkeypatch.setattr("kicadstamp.absent_copper_prune.instance_record_copper_map",
                        _stub)


def _record_map(index=0, uuid="u0", kind="track", role=None, planned=3):
    return RecordCopperMap(
        by_record={(kind, role or SPOKE_LEVEL_ROLE_PLACEHOLDER, index): uuid},
        planned=planned)


def _messages(dock, monkeypatch):
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))
    return messages


def _refresh_calls(dock, monkeypatch):
    """Every component-table rebuild the finish half performed (С-2а-1: the
    component's OWN list changed, so the table has to be rebuilt from it)."""
    calls = []
    real = dock._refresh_components_table

    def _spy():
        calls.append(True)
        real()

    monkeypatch.setattr(dock, "_refresh_components_table", _spy)
    return calls


def test_the_record_the_selection_names_is_removed(main_window, tmp_path, monkeypatch):
    """A selection whose copper IS a record of the cell (the map names it): that
    record goes, with its «- track …» line and the «subtracted» summary; the cell's
    other records are untouched."""
    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0")})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result.get("removed") == [("track", before[0])], result
    assert result["not_ours"] == 0
    dock._finish_subtract_from_selection(result)

    assert dock._tracks == before[1:]
    assert any(m.startswith("- track") for m in messages)
    assert any("subtracted 1 record(s)" in m for m in messages)


def test_the_other_records_of_the_cell_survive(main_window, tmp_path, monkeypatch):
    """Only the named record goes: the cell keeps the rest, and the summary counts
    exactly one."""
    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u1", "GND", 10.0, 12.0, 11.0, 12.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=1, uuid="u1")})
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("track", before[1])]
    dock._finish_subtract_from_selection(result)
    assert dock._tracks == [before[0], before[2]]


def test_selected_copper_that_is_not_a_record_of_the_cell_is_ignored(
        main_window, tmp_path, monkeypatch):
    """The selection holds copper the cell's map never named (a foreign track next
    to a record, hand-drawn copper): NOTHING is removed and the Log says so — the
    ignore is never silent.

    С-2а-1 adds the negative half of "the tables are rebuilt": with NOTHING
    removed there is nothing to rebuild them for."""
    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u-foreign", "GND", 30.0, 30.0, 31.0, 30.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0")})
    messages = _messages(dock, monkeypatch)
    refreshes = _refresh_calls(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == []
    assert result["not_ours"] == 1
    dock._finish_subtract_from_selection(result)

    assert dock._tracks == before
    assert any("not records of cell" in m for m in messages)
    assert not any("subtracted" in m for m in messages)
    assert refreshes == []


def test_an_empty_dry_run_removes_nothing_and_says_it_could_not_match(
        main_window, tmp_path, monkeypatch):
    """A refused tree / an unrealized record / a chain-only placement plans no
    command: the action must NOT delete anything, and the red line names the reason
    class instead of pretending the cell has no copper."""
    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result.get("empty") is True, result
    dock._finish_subtract_from_selection(result)

    assert dock._tracks == before
    assert any("could not match the selection" in m for m in messages)


def test_only_components_selected_is_reported_and_removes_nothing(
        main_window, tmp_path, monkeypatch):
    """Copper only: a selection of components alone subtracts nothing and says so."""
    dock, _ = _make_dock(main_window, tmp_path)
    fp = Footprint(ref="C1", uuid="uuid-C1",
                   position=Vector2.from_xy_mm(10.0, 10.0), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    _fake_board(monkeypatch, [fp])
    _fake_map(monkeypatch, {"dac0": _record_map()})
    messages = _messages(dock, monkeypatch)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("no_copper") is True
    assert any("no copper is selected" in m for m in messages)
    assert len(dock._tracks) == 3


def test_two_instances_take_the_one_whose_copper_is_selected(
        main_window, tmp_path, monkeypatch):
    """Two records place the cell (two channels): the instance whose dry run matched
    the selected copper is the one — its record goes."""
    data = _config_data(entities=[
        {"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_0"},
        {"name": "dac1", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_1"}])
    dock, _ = _make_dock(main_window, tmp_path, data)
    _fake_board(monkeypatch, [_live_track("u1", None, 10.0, 14.0, 11.0, 14.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0"),
                            "dac1": _record_map(index=2, uuid="u1")})
    _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("track", before[2])], result
    assert result["sheet"] == "Channel_1"


def test_two_instances_both_matching_is_a_refusal(main_window, tmp_path,
                                                  monkeypatch):
    """Both instances' copper in one selection is an ambiguity: the caller REFUSES
    red and removes nothing (never a guess — deleting the wrong instance's record
    would take copper from a live one)."""
    data = _config_data(entities=[
        {"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_0"},
        {"name": "dac1", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_1"}])
    dock, _ = _make_dock(main_window, tmp_path, data)
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0),
                              _live_track("u1", None, 10.0, 14.0, 11.0, 14.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0"),
                            "dac1": _record_map(index=2, uuid="u1")})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("ambiguous"), result
    assert dock._tracks == before
    assert any("matches 2 instances" in m for m in messages)


def test_several_instances_none_matching_is_nothing_to_subtract(
        main_window, tmp_path, monkeypatch):
    """С-2а-2 (plan_2026_10_06_prune_absent_cell_copper): several records place the
    cell, the runs DID plan, and the selection matches NONE of them. That is not
    "could not match" — the check ran; the copper is simply not this cell's. The
    answer must be the single-instance one: "nothing to subtract" plus the ignored
    count."""
    data = _config_data(entities=[
        {"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_0"},
        {"name": "dac1", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_1"}])
    dock, _ = _make_dock(main_window, tmp_path, data)
    _fake_board(monkeypatch, [_live_track("u-foreign", "GND", 30.0, 30.0, 31.0, 30.0)])
    # ONE candidate planned nothing at all, the other DID plan — with the old
    # "empty whenever labels are empty" answer the first candidate was enough to
    # turn the whole action into the red "planned nothing".
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(),
                            "dac1": _record_map(index=2, uuid="u1")})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result.get("empty") is not True, result
    assert result["removed"] == [], result
    assert result["not_ours"] == 1, result
    dock._finish_subtract_from_selection(result)

    assert dock._tracks == before
    assert any("nothing to subtract" in m for m in messages)
    assert any("not records of cell" in m for m in messages)
    assert not any("could not match" in m for m in messages)


def test_several_instances_all_runs_empty_say_it_could_not_match(
        main_window, tmp_path, monkeypatch):
    """The other side of С-2а-2: when EVERY candidate's dry run planned nothing the
    check could not run at all — THAT is the red "could not match" answer, and
    nothing is removed (the cell is not told the selection is foreign, because
    nobody looked)."""
    data = _config_data(entities=[
        {"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_0"},
        {"name": "dac1", "cell": "dac_buf", "cluster": "DAC_BUF",
         "sheet": "Channel_1"}])
    dock, _ = _make_dock(main_window, tmp_path, data)
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(), "dac1": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("empty") is True, result
    assert dock._tracks == before
    assert any("could not match the selection" in m for m in messages)


def test_the_worker_needs_no_ui_thread_board_handle(main_window, tmp_path,
                                                    monkeypatch):
    """Door guard (deepseek.md п.31): with the guard ARMED in ``raise`` mode the
    worker runs to completion — it reads no ``connection.board`` (its selection
    read builds its OWN adapter; the UI half checks ``is_connected``)."""
    from gui import connection as conn_mod

    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0")})
    monkeypatch.setattr(conn_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result.get("removed"), result


# ── the TWO LEVELS of a cell's copper (С-2а-1) ──────────────────────────────
#
# The table this closes: which list the record leaves × which level it came from.
#
#   level \ kind   | via                                  | track
#   ---------------+--------------------------------------+------------------------
#   cell           | self._vias (existing cells 1/2)      | self._tracks (same cells)
#   component      | the slot's own `vias` (the cells    | IMPOSSIBLE — a component
#                  | below)                               | slot carries no tracks
#   (nothing)      | nothing, no table rebuild            | same
#
# The component-track cell is EMPTY BY CONSTRUCTION (TemplateComponentSlot has
# `vias` only), so `plan_subtraction` can never resolve a component-track key to a
# record — an empty cell by decision, not by oversight.

def test_a_component_via_is_removed_from_the_component_list(
        main_window, tmp_path, monkeypatch):
    """С-2а-1 (plan_2026_10_06_prune_absent_cell_copper), the BLOCKER cell: the map
    key's role part says the record is a COMPONENT's via, so it has to leave THAT
    list. The defect: the finish half dropped from `self._vias`/`self._tracks`
    alone, the Log still said "subtracted 1", and the next Save wrote the via
    straight back."""
    dock, _ = _make_dock(main_window, tmp_path, _config_with_component_via())
    component = dock._components[0]
    cell_via = dock._vias[0]
    _fake_board(monkeypatch, [_live_via("u_comp", "N", 10.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(
        by_record={("via", "DA", 0): "u_comp"}, planned=2)})
    messages = _messages(dock, monkeypatch)
    refreshes = _refresh_calls(dock, monkeypatch)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("via", component["vias"][0])], result
    dock._finish_subtract_from_selection(result)

    assert component["vias"] == []          # the component lost its own via
    assert dock._vias == [cell_via]         # the cell's own via stayed
    assert refreshes, "the component table must be rebuilt from the slot"
    assert any(m.startswith("- via") for m in messages)
    assert any("subtracted 1 record(s)" in m for m in messages)


def test_save_after_subtracting_a_component_via_writes_it_out(
        main_window, tmp_path, monkeypatch):
    """С-2а-1, the observable result (Denis's own cell): after the subtraction and
    a Save into tmp_path the component in the FILE carries no via — while the
    cell's own via is still written. A drop from the cell's lists alone leaves the
    via in the file, and the next redraw resurrects it."""
    dock, target = _make_dock(main_window, tmp_path, _config_with_component_via())
    _fake_board(monkeypatch, [_live_via("u_comp", "N", 10.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(
        by_record={("via", "DA", 0): "u_comp"}, planned=2)})
    messages = _messages(dock, monkeypatch)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)
    dock._on_save()                          # the user's Save

    entry = sexp_to_dict(target.read_text(encoding="utf-8"))["cells"]["dac_buf"]
    assert not entry["components"][0].get("vias"), messages
    assert len(entry["vias"]) == 1           # the cell's own via is still written


def test_the_cell_via_with_the_same_index_is_not_the_components(
        main_window, tmp_path, monkeypatch):
    """The mirror of the blocker cell: the SAME index 0 at the two levels names two
    different records, so selecting the CELL's own via must drop it from the cell's
    list and leave the component's via alone (the flat-list defect would have taken
    the wrong one)."""
    dock, _ = _make_dock(main_window, tmp_path, _config_with_component_via())
    component = dock._components[0]
    cell_via = dock._vias[0]
    _fake_board(monkeypatch, [_live_via("u_cell", "N", 10.0, 10.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(
        by_record={("via", SPOKE_LEVEL_ROLE_PLACEHOLDER, 0): "u_cell",
                   ("via", "DA", 0): "u_comp"}, planned=2)})
    _messages(dock, monkeypatch)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("via", cell_via)], result
    dock._finish_subtract_from_selection(result)

    assert dock._vias == []
    assert component["vias"]                 # the component's own via stayed


# NOTE on the plan's guard 6 («Add selected copper» — the old Import — keeps its
# cells, only the action's NAME changes): that plane needs no cell HERE. The import
# door has its own guards (tests/gui/docks/test_cell_editor.py,
# test_cell_editor_mixed_selection.py, tests/selection/), and they stay green —
# nothing in this action touches that path. The rename itself is a later commit.
