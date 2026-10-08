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
import json
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.docks.cell_editor import CellDock
from kicadstamp.absent_copper_prune import RecordCopperMap
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import (ROLE_FIELD_NAME,
                                  SPOKE_LEVEL_ROLE_PLACEHOLDER)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.registry import make_registry_key, record_key_part


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


def _live_fp(ref, x_mm, y_mm):
    return Footprint(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     layer=BoardLayer.BL_F_Cu)


# The Role/Cluster the fake board answers per ref: the cell's own ZERO-offset
# slot (the frame's origin, the legacy rule) and the second slot DA the NON-RIGID
# cell below adds.
_FIELDS = {"Z9": ("ZERO", "DAC_BUF"), "R9": ("DA", "DAC_BUF")}


def _live_zero(x_mm=10.0, y_mm=10.0):
    """The instance's live ZERO-offset slot footprint — the cell frame's origin.

    It stands exactly on the live copper at (10, 10) the cells below put on the
    board, so the record whose stored offset is (0, 0) lands on it — and the fit
    has no second slot to get a direction from, so the residual is 0.0: RIGID,
    which is what the registry-only path requires
    (plan_2026_10_07_registry_pair_frame_check, rule 2)."""
    return _live_fp("Z9", x_mm, y_mm)


def _live_da(x_mm, y_mm):
    """The second slot's live footprint — where the NON-RIGID cell's live cluster
    stops being a rigid copy of the cell."""
    return _live_fp("R9", x_mm, y_mm)


class _Adapter:
    """The whole board the worker sees: the selection, a close(), and — for the
    REGISTRY fallback a refused tree falls back to — the two live copper lists,
    the footprints `own_instance_context` reads for the instance's refs and the
    Role/Cluster fields the cell FRAME is built from."""

    def __init__(self, selected, vias=(), tracks=(), footprints=(), fields=None):
        self._selected = list(selected)
        self._vias = list(vias)
        self._tracks = list(tracks)
        self._footprints = list(footprints)
        self._fields = dict(fields or {})
        self.closed = False

    def get_field_value(self, fp, name):
        role, cluster = self._fields.get(fp.ref, (None, None))
        return role if name == ROLE_FIELD_NAME else cluster

    def refresh_board(self):
        pass

    def get_selected_items(self):
        return list(self._selected)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_footprints(self):
        return list(self._footprints)

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
        "config_path": str(dock._root_path),
        "cell_name": "dac_buf",
        "cluster": None,
        "sheet": None,
    }


def _fake_board(monkeypatch, selected, **board):
    board.setdefault("fields", dict(_FIELDS))
    adapter = _Adapter(selected, **board)
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


def test_the_dock_hands_its_profile_to_the_worker_and_its_adapter(
        main_window, tmp_path, monkeypatch):
    """С-2а-4: the action reads the board through its OWN adapter, and that adapter
    has to be built for THIS profile — the payload used to carry no `config_path`
    at all, so `create_board_adapter` fell back to its default instead (a board
    read made with the wrong rails). The cell walks the whole chain: the dock's own
    payload (the wiring's `open`, with `start_long_op` CAPTURED instead of started)
    -> the worker -> the adapter factory."""
    dock, _ = _make_dock(main_window, tmp_path)
    payloads = []
    monkeypatch.setattr("gui.subtract_copper.start_long_op",
                        lambda connection, widgets, fn, ok, err, *args, **kwargs:
                        payloads.append(args[0]))
    monkeypatch.setattr(main_window, "connection",
                        SimpleNamespace(is_connected=True, timeout_ms=1234))

    dock._on_subtract_selected_copper()

    assert len(payloads) == 1, payloads
    assert payloads[0]["config_path"] == str(dock._root_path), payloads[0]
    assert payloads[0]["timeout_ms"] == 1234, payloads[0]

    seen = {}

    def _factory(**kwargs):
        seen.update(kwargs)
        return _Adapter([_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)])

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter", _factory)
    _fake_map(monkeypatch, {"dac0": _record_map(index=0, uuid="u0")})

    result = dock._run_subtract_from_selection(payloads[0])

    assert seen.get("config_path") == str(dock._root_path), seen
    assert seen.get("timeout_ms") == 1234, seen
    assert result.get("removed") == [("track", dock._tracks[0])], result


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


def test_the_subtraction_is_staged_into_the_working_set_without_a_manual_save(
        main_window, tmp_path, monkeypatch):
    """С-2б: the real `SubtractWiring.finish` must STAGE the change, not merely
    mutate the widget's lists. With a project open every dock edit lands in the
    ConfigWorkingSet (`_autostage` → `_on_save` → `merge_write` → the working
    set), and the project Save is what puts it on disk.

    Without the autostage the subtraction would live ONLY in the dock's memory:
    the Log would report it, the tables would show it, and the project Save would
    write the record straight back. So the cell runs the real finish on a removed
    record with NO manual Save and asserts the WORKING SET holds the cell without
    it — while the file on disk still carries it (that is what makes it staging,
    not a write)."""
    from kicadstamp.config_working_set import WORKING_SET

    dock, _ = _make_dock(main_window, tmp_path)
    _fake_board(monkeypatch, [_live_track("u1", "GND", 10.0, 12.0, 11.0, 12.0)])
    _fake_map(monkeypatch, {"dac0": _record_map(index=1, uuid="u1")})
    _messages(dock, monkeypatch)
    before = list(dock._tracks)

    # monkeypatch, never `WORKING_SET.enabled = True` (tests/repo/test_repo_hygiene.py:
    # a test does not assign into an imported name — the fixture undoes it for you).
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    try:
        result = dock._run_subtract_from_selection(_payload(dock))
        dock._finish_subtract_from_selection(result)      # the real wiring.finish

        assert dock._tracks == [before[0], before[2]]     # the widget lost it
        assert WORKING_SET.is_dirty(), "the subtraction must be staged"

        staged = WORKING_SET.staged_content(str(dock._path.resolve()))
        assert staged is not None, "nothing was staged for the cell's file"
        tracks = staged["cells"]["dac_buf"]["tracks"]
        assert len(tracks) == 2, tracks
        assert [t.get("net") for t in tracks] == [None, "GND2"], tracks

        # ...and it is STAGED, not written: the file on disk still carries it.
        on_disk = sexp_to_dict(dock._path.read_text(encoding="utf-8"))
        assert len(on_disk["cells"]["dac_buf"]["tracks"]) == 3
    finally:
        WORKING_SET.clear()


# NOTE on the plan's guard 6 («Add selected copper» — the old Import — keeps its
# cells, only the action's NAME changes): that plane needs no cell HERE. The import
# door has its own guards (tests/gui/docks/test_cell_editor.py,
# test_cell_editor_mixed_selection.py, tests/selection/), and they stay green —
# nothing in this action touches that path. The rename itself is a later commit.


# ── a REFUSED tree: the dry run plans nothing, the map falls back to the
#    REGISTRY ONLY (plan_2026_10_07_refused_tree_matching) ───────────────────
#
# The fixture is a REAL refused tree: the drift guard refuses the config below
# (asserted first), so the recording is never materialized and
# `instance_record_copper_map` returns an empty map — which is exactly what the
# cells stub it to. The registry fallback then does the matching, by the SAME
# `is_own_key` filter «Select cell» uses. One cell per row of the plan's table
# (rule 35).


def _refused_tree_data(*, component_vias=None, entities=None,
                       extra_component=None):
    component = {"role": "FPGA", "offset_along_mm": 2.0,
                 "offset_across_mm": 1.0, "angle_deg": 0.0}
    if component_vias:
        component["vias"] = component_vias
    components = [component, {"role": "ZERO", "offset_along_mm": 0.0,
                              "offset_across_mm": 0.0, "angle_deg": 0.0}]
    if extra_component is not None:
        components.append(dict(extra_component))
    return {
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": components,
            # the cell's own zero-offset slot: the legacy frame origin the
            # registry-only path needs, and NOT an anchor_role — the drift guard
            # below refuses THIS config (the tree anchors on role FPGA at (2,1))
            "vias": [],
            "tracks": [_track(None, 0.0), _track("GND", 2.0)],
            "clone_placements": [],
        }},
        "entities": list(entities if entities is not None
                         else [{"name": "dac0", "cell": "dac_buf",
                                "cluster": "DAC_BUF"}]),
        "trees": [{"name": "bad", "anchor": {"role": "FPGA"},
                   "nodes": [{"ref": "dac0", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    }


def _make_refused_dock(main_window, tmp_path, data=None):
    return _make_dock(main_window, tmp_path,
                      data if data is not None else _refused_tree_data())


def _load_ids(target):
    """(cfg, cell_identity, {entity name: identity}) from the LIFTED file."""
    from kicadstamp.config import load_config
    cfg, _ctx = load_config(str(target))
    cell_id = record_key_part("dac_buf", cfg.cells["dac_buf"].uuid)
    ents = {e.name: record_key_part(e.name, e.uuid) for e in cfg.entities}
    return cfg, cell_id, ents


def _write_registries(tmp_path, via_entries, track_entries):
    """The TWO files `registry_paths_for_config` derives from ``root.sexp``: the
    stem of the config names the file inside the `registry/` and `tracks/`
    subfolders."""
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "root.registry.json").write_text(
        json.dumps({"schema_version": 2, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "root.tracks.registry.json").write_text(
        json.dumps({"schema_version": 2, **track_entries}), encoding="utf-8")


def _via_reg_entry(uuid):
    return {"uuid": uuid, "x_mm": 10.0, "y_mm": 10.0, "net": "N",
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_reg_entry(uuid):
    return {"uuid": uuid, "start_x_mm": 10.0, "start_y_mm": 10.0,
            "end_x_mm": 11.0, "end_y_mm": 10.0, "width_mm": 0.25, "net": "N",
            "layer": "F.Cu"}


def _own_track_key(cell_id, ent_identity, index):
    return make_registry_key(f"name:{ent_identity}", cell_id, None, index)


def _board_track(uuid, net="N"):
    """A live board track for the registry map (only its uuid matters there)."""
    return _live_track(uuid, net, 10.0, 10.0, 11.0, 10.0)


def test_the_refused_tree_fixture_is_actually_refused(tmp_path):
    """The plan demands a REAL refused tree, not a hand-made empty map: the drift
    guard refuses THIS config, so its recording is not materialized — which is
    why the dry run plans nothing (the cells below stub exactly that)."""
    from kicadstamp.config import load_config
    from kicadstamp.trees import check_tree_self_anchor_drift

    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(_refused_tree_data(), format_number=2),
                      encoding="utf-8")
    cfg, _ctx = load_config(str(target))

    assert check_tree_self_anchor_drift(cfg, cfg.trees[0]) is not None


def test_a_refused_tree_subtracts_by_the_registry_and_says_registry_only(
        main_window, tmp_path, monkeypatch):
    """Row 1: the tree is refused (the stub dry run is empty), but the record's
    key IS in the registry — the record is subtracted, and a yellow line says the
    planner produced no copper and the match came from the registry alone."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u0")})
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)],
                tracks=[_board_track("u0")], footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})       # the refused tree
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    assert result.get("error") is None, result
    assert result["source"] == "registry", result
    assert result["removed"] == [("track", before[0])], result
    dock._finish_subtract_from_selection(result)

    assert dock._tracks == before[1:]
    assert any("matched by the registry only" in m for m in messages), messages
    assert not any("could not match" in m for m in messages), messages


def test_a_refused_tree_takes_a_component_via_out_of_its_slot(
        main_window, tmp_path, monkeypatch):
    """Row 2: the map key's role part names a COMPONENT — the subtraction leaves
    the component's own `vias`, not the cell's lists (С-2а-1 holds on the
    registry-only path too)."""
    data = _refused_tree_data(component_vias=[_via_record(5.0)])
    dock, target = _make_refused_dock(main_window, tmp_path, data)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path,
                      {make_registry_key(f"name:{ents['dac0']}", cell_id,
                                         "FPGA", 0): _via_reg_entry("u_c0")},
                      {})
    # the component's via 0 is stored 5 mm ACROSS, so its world place is
    # (10, 15) with the live FPGA at (12, 11) — the record's own place
    _fake_board(monkeypatch, [_live_via("u_c0", "N", 10.0, 15.0)],
                vias=[_live_via("u_c0", "N", 10.0, 15.0)],
                footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    _messages(dock, monkeypatch)
    component = dock._components[0]
    cell_vias = list(dock._vias)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("via", component["vias"][0])], result
    assert result["source"] == "registry", result
    dock._finish_subtract_from_selection(result)
    assert component["vias"] == []
    assert dock._vias == cell_vias


def test_a_record_without_a_registry_key_is_not_checked_and_says_so(
        main_window, tmp_path, monkeypatch):
    """Row 3: the selected record has NO key in the registry — it cannot be
    checked, so it is NOT subtracted and the count is named, never silent."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _cfg, cell_id, ents = _load_ids(target)
    # only track 0 has a key; track 1 (the selection) has none
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u0")})
    _fake_board(monkeypatch,
                [_live_track("u1", None, 20.0, 20.0, 21.0, 20.0)],
                tracks=[_board_track("u0")], footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [], result
    assert result["not_ours"] == 1, result
    dock._finish_subtract_from_selection(result)
    assert dock._tracks == before
    assert any("nothing to subtract" in m for m in messages), messages
    assert any("have no registry entry" in m for m in messages), messages
    assert not any("could not match" in m for m in messages), messages


def test_a_refused_tree_with_foreign_copper_is_nothing_to_subtract(
        main_window, tmp_path, monkeypatch):
    """Row 4: the selection is copper of NO record of this cell — an ordinary
    "nothing to subtract" plus the ignored count, never the red "could not
    match" line (the check DID run)."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u0")})
    _fake_board(monkeypatch,
                [_live_track("u_foreign", "GND", 30.0, 30.0, 31.0, 30.0)],
                tracks=[_board_track("u0")], footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result["removed"] == [], result
    assert result["not_ours"] == 1, result
    assert dock._tracks == before
    assert any("not records of cell" in m for m in messages), messages
    assert not any("could not match" in m for m in messages), messages


def test_a_refused_tree_with_an_empty_registry_stays_red(
        main_window, tmp_path, monkeypatch):
    """Row 5: BOTH the dry run and the registry are empty — nothing could be
    checked at all, so the red "could not match" line stays."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _write_registries(tmp_path, {}, {})                       # empty registry
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)],
                footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("empty") is True, result
    assert result["planned"] == 0, result
    assert dock._tracks == before
    assert any("could not match the selection" in m for m in messages), messages


def test_a_shifted_key_does_not_subtract_its_neighbour(
        main_window, tmp_path, monkeypatch):
    """Row 'запись k удалена, ключи сдвинуты': the registry still holds the
    copper of a record the cell no longer has, so the key's index now means the
    NEIGHBOUR. The pair does not sit where that record puts its own copper —
    nothing is subtracted and the count is named (plan ... , rule 2)."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u_del")})
    # the deleted record's copper: 4 mm from where record 0 puts its own
    _fake_board(monkeypatch,
                [_live_track("u_del", None, 10.0, 14.0, 11.0, 14.0)],
                tracks=[_live_track("u_del", None, 10.0, 14.0, 11.0, 14.0)],
                footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("empty") is True, result
    assert dock._tracks == before
    assert any("no pair of the registry could be checked" in m for m in messages), \
        messages
    assert any("do not sit where the record puts them" in m for m in messages), \
        messages
    assert not any("could not match the selection" in m for m in messages), \
        messages


def test_a_key_past_the_record_list_is_never_checked(
        main_window, tmp_path, monkeypatch):
    """Row 'ключ-сирота': the key's index is past the end of the cell's record
    list (the numbers shifted) — it names NO record, so it is never a pair, and
    the count is named with its own wording."""
    dock, target = _make_refused_dock(main_window, tmp_path)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 7):
                       _track_reg_entry("u7")})
    _fake_board(monkeypatch,
                [_live_track("u7", None, 10.0, 10.0, 11.0, 10.0)],
                tracks=[_live_track("u7", None, 10.0, 10.0, 11.0, 10.0)],
                footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("empty") is True, result
    assert dock._tracks == before
    assert any("past the end of the cell's record list" in m for m in messages), \
        messages
    assert not any("could not match the selection" in m for m in messages), \
        messages


def test_a_non_rigid_cluster_accepts_no_registry_pair_and_names_the_residual(
        main_window, tmp_path, monkeypatch):
    """Row 'нежёсткий кластер': the live cluster is NOT a rigid copy of the cell,
    so its frame cannot tell a pair from its neighbour — NO registry pair is
    accepted, nothing is subtracted, and the residual is the yellow reason
    (plan ..., rule 2)."""
    data = _refused_tree_data(extra_component={"role": "DA",
                                               "offset_along_mm": 5.0,
                                               "offset_across_mm": 1.0})
    dock, target = _make_refused_dock(main_window, tmp_path, data)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u0")})
    # DA is stored 3 mm along from the mount but stands 19 mm across on the board
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)],
                tracks=[_board_track("u0")],
                footprints=[_live_zero(), _live_da(15.0, 30.0)])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result.get("empty") is True, result
    assert dock._tracks == before
    assert any("not a rigid copy of the cell" in m for m in messages), messages
    assert any("no pair of the registry could be checked" in m for m in messages), \
        messages


def test_a_non_refused_tree_never_consults_the_registry(
        main_window, tmp_path, monkeypatch):
    """Row 6 / mutation "the registry path is enabled on a NON-empty run": when
    the dry run planned something, the map is the dry run's — the registry's own
    key (live copper of ANOTHER record) must never be subtracted, and no
    "registry only" line appears."""
    dock, target = _make_dock(main_window, tmp_path)          # a NORMAL config
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 2):
                       _track_reg_entry("u2")})
    # the dry run planned (track 1); the SELECTION is the registry's track 2
    _fake_board(monkeypatch, [_live_track("u2", "GND2", 20.0, 20.0, 21.0, 20.0)],
                tracks=[_board_track("u2", "GND2")])
    _fake_map(monkeypatch, {"dac0": _record_map(index=1, uuid="u1")})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))
    dock._finish_subtract_from_selection(result)

    assert result["source"] == "dry_run", result
    assert result["removed"] == [], result
    assert result["not_ours"] == 1, result
    assert dock._tracks == before
    assert not any("registry only" in m for m in messages), messages


def test_two_instances_take_the_refused_one_whose_registry_matches(
        main_window, tmp_path, monkeypatch):
    """Row 7: two instances, ONE refused (its registry holds the copper of the
    selection), the other's dry run planned but did not match — the REFUSED
    instance is taken (the choice is by the maps with their sources)."""
    entities = [{"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
                 "sheet": "Channel_0"},
                {"name": "dac1", "cell": "dac_buf", "cluster": "DAC_BUF",
                 "sheet": "Channel_1"}]
    data = _refused_tree_data(entities=entities)
    dock, target = _make_refused_dock(main_window, tmp_path, data)
    _cfg, cell_id, ents = _load_ids(target)
    _write_registries(tmp_path, {},
                      {_own_track_key(cell_id, ents["dac0"], 0):
                       _track_reg_entry("u0")})
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)],
                tracks=[_board_track("u0")], footprints=[_live_zero()])
    # dac0 is refused (empty); dac1 planned, but its copper is NOT selected
    _fake_map(monkeypatch, {"dac0": RecordCopperMap(),
                            "dac1": _record_map(index=1, uuid="u9")})
    _messages(dock, monkeypatch)
    before = list(dock._tracks)

    result = dock._run_subtract_from_selection(_payload(dock))

    assert result["removed"] == [("track", before[0])], result
    assert result["sheet"] == "Channel_0", result
    assert result["source"] == "registry", result


def test_the_entitys_pinned_refs_narrow_which_copper_is_this_instance(
        main_window, tmp_path, monkeypatch):
    """доделка 3а, п.2: пины сущности (`refs` в payload) сужают, какие
    `anchor:<ref>` записи суть ЭТОГО экземпляра. Пины называют `C1`, значит
    `anchor:C1` — наша медь, а `anchor:Z9` (живой слот ячейки) — НЕ наша: её медь
    из выделения игнорируется, а не вычитается.

    Дверь несёт пины в payload (тест дока `test_the_subtract_flow_...`); здесь
    проверяется, что ВОРКЕР отдаёт их в сужение
    (`registry_record_copper_map(own_refs=...)`) — без них `anchor:Z9` снова своя
    и её запись была бы вычтена (мутация M4)."""
    entity = {"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF",
              "refs": {"ZERO": "C1"}}
    dock, target = _make_refused_dock(main_window, tmp_path,
                                      _refused_tree_data(entities=[entity]))
    _cfg, cell_id, _ents = _load_ids(target)
    # TWO own keys: the pinned C1 (cell track 1) and the live zero slot Z9 (0).
    _write_registries(
        tmp_path, {},
        {make_registry_key("anchor:C1", cell_id, SPOKE_LEVEL_ROLE_PLACEHOLDER, 1):
         _track_reg_entry("u1"),
         make_registry_key("anchor:Z9", cell_id, SPOKE_LEVEL_ROLE_PLACEHOLDER, 0):
         _track_reg_entry("u0")})
    # The selection is the UNPINNED slot's copper (u0). The frame's live zero
    # slot is Z9 at (10, 10); u1 (track 1, across 2.0 mm) sits at (10, 12).
    _fake_board(monkeypatch, [_live_track("u0", None, 10.0, 10.0, 11.0, 10.0)],
                tracks=[_board_track("u0"),
                        _live_track("u1", "N", 10.0, 12.0, 11.0, 12.0)],
                footprints=[_live_zero()])
    _fake_map(monkeypatch, {"dac0": RecordCopperMap()})
    messages = _messages(dock, monkeypatch)
    before = list(dock._tracks)

    payload = _payload(dock)
    payload["cluster"] = "DAC_BUF"
    payload["refs"] = {"ZERO": "C1"}        # the entity's pins, as the door sends

    result = dock._run_subtract_from_selection(payload)
    dock._finish_subtract_from_selection(result)

    assert result["removed"] == [], result
    assert result["not_ours"] == 1, result
    assert dock._tracks == before
    assert any("not records of cell" in m for m in messages), messages
