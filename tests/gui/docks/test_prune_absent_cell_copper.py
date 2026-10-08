# tests/gui/docks/test_prune_absent_cell_copper.py
"""Worker-level cells for С-1 — "Update from selection" is STRICTLY the selection
(plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-06).

Denis's word of 2026-10-06 WITHDRAWS the soft Н4 п.5 rule of 2026-10-05: a cell
record (via/track) with NO pair in the (narrowed) selection is DELETED, on EVERY
path — a clean selection and a by-cluster (mixed) one — whether its copper is
still on the board or not. The cell becomes exactly the selection. "Import from
selection" stays purely additive.

These drive the REAL ``CellDock._run_refresh_geometry`` /
``_run_import_vias_tracks`` with a real config on disk and a fake board, so the
whole worker path is exercised; the board boundary is faked, the rule is the
product's.

The soft cells this file used to hold (a record kept because its uuid was live
or because geometry found it, the "could not check" guard) asserted the OPPOSITE
and are REWRITTEN here by the word of the task's author (plan С-1 п.5), not
weakened.
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.board_layers import ALL_COPPER_LAYERS
from gui.docks.cell_editor import CellDock
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.board import Footprint, Track
from kicadstamp.domain.geometry import BoardLayer, Vector2


def _track(net, across, width=0.25):
    return {"net": net, "layer": "F.Cu", "width_mm": width,
            "start_along_mm": 0.0, "start_across_mm": across,
            "end_along_mm": 1.0, "end_across_mm": across}


def _config_data(tracks=None):
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
        "entities": [{"name": "dac0", "cell": "dac_buf", "cluster": "DAC_BUF"}],
    }


def _write_config(tmp_path, data=None):
    target = tmp_path / "root.sexp"
    target.write_text(dict_to_sexp(data if data is not None else _config_data(),
                                   format_number=2), encoding="utf-8")
    return target


def _fp(ref, role, cluster, x_mm, y_mm, chain=()):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = tuple(chain)
    fp._role = role
    fp._cluster = cluster
    return fp


def _live_track(uuid, net, x1, y1, x2, y2):
    return Track(uuid=uuid, net_name=net,
                 start=Vector2.from_xy_mm(x1, y1), end=Vector2.from_xy_mm(x2, y2),
                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)


class _Board:
    """The DAC_BUF instance on the board: its two components and the ONE live
    track that pairs record 0. ``with_extra`` adds a stray selected track no
    record describes (the additive case); ``on_board_tracks`` is what a plain
    board read returns — copper that EXISTS on the board but is NOT in the
    selection, so the strict rule must IGNORE it (С-1)."""

    def __init__(self, paired_net=None, with_extra=True, on_board_tracks=()):
        self.selected = [
            _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0", "s1")),
            _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0", "s2")),
        ]
        self.track0 = _live_track("t0", paired_net, 10.0, 10.0, 11.0, 10.0)
        self.extra = _live_track("t-extra", "GNDX", 30.0, 30.0, 31.0, 30.0)
        self.selected_all = list(self.selected) + [self.track0]
        if with_extra:
            self.selected_all.append(self.extra)
        self._board_tracks = list(on_board_tracks)
        self.selected_calls = []
        self.adapter = SimpleNamespace(
            refresh_board=lambda: None,
            get_selected_items=lambda: list(self.selected_all),
            get_field_value=lambda fp, name: (
                getattr(fp, "_role", None)
                if name == "Role" else getattr(fp, "_cluster", None)),
            get_footprints=lambda: list(self.selected),
            get_vias=lambda: [],
            get_tracks=lambda: list(self._board_tracks),
            get_footprint_pads=lambda fp: [],
            select_items=lambda items: self.selected_calls.append(list(items)),
        )


@pytest.fixture(autouse=True)
def _no_layer_warning(monkeypatch):
    monkeypatch.setattr(cell_editor_mod, "_warn_hidden_copper_layers",
                        lambda adapter: None)


def _make_dock(main_window, tmp_path, data=None):
    target = _write_config(tmp_path, data)
    dock = CellDock(main_window)
    dock.set_root_path(target)
    dock.load_entry("dac_buf")
    return dock, target


def _payload(dock, board):
    return {
        "connection": SimpleNamespace(board=board),
        "components": list(dock._components),
        "vias": list(dock._vias),
        "tracks": list(dock._tracks),
        "origin_role": None,
        "cell_layer": "F.Cu",
        "layers": ALL_COPPER_LAYERS,
        "empty_layers": [],
        "root_path": str(dock._root_path),
        "cell_name": "dac_buf",
        "remembered_cluster": None,
        "remembered_sheet": None,
    }


def _texts(result):
    return [text for text, _level in result["selection_lines"]]


def test_clean_refresh_removes_every_record_not_in_the_selection(
        main_window, tmp_path, monkeypatch):
    """С-1 п.1: a clean selection pairing ONE of the cell's three tracks deletes
    the other two — a record with no live pair is gone, no matter that its copper
    could still be on the board. The Log names each removed record and the итог."""
    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board(with_extra=False)
    messages = []
    monkeypatch.setattr(dock, "_show_message",
                        lambda text, style="": messages.append(text))

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    assert plan.removed_track_records == [dock._tracks[1], dock._tracks[2]]
    assert plan.removed_via_records == []
    assert len(plan.track_updates) == 1
    assert plan.new_track_records == []

    dock._finish_refresh_geometry(result)

    assert any("- track" in m for m in messages)
    assert any("removed 2 record(s) not in the selection" in m for m in messages)
    # C3 (Claude's С-1 acceptance): the red "no copper" line must NOT fire when the
    # selection DOES hold copper — otherwise it lies about what happened.
    assert not any("the selection has no copper" in m for m in messages)
    assert dock._tracks == [plan.track_updates[0][0]]


def test_cluster_refresh_removes_records_whose_copper_is_on_the_board(
        main_window, tmp_path):
    """С-1 п.1, by-cluster path: the two cell tracks ARE on the board (a plain
    board read returns them) but are NOT in the selection — the cell becomes the
    selection, so both records go. The old rule kept them because the board
    still carried their copper."""
    dock, _ = _make_dock(main_window, tmp_path)
    live1 = _live_track("t1", "GND", 10.0, 12.0, 11.0, 12.0)
    live2 = _live_track("t2", "GND2", 10.0, 14.0, 11.0, 14.0)
    board = _Board(with_extra=False, on_board_tracks=[live1, live2])
    # A foreign cluster sharing the selection: the by-cluster prelude must run.
    board.selected.append(_fp("P1", "PA", "PIF_AVDD", 20.0, 10.0, ("ch1", "s3")))
    board.selected.append(_fp("P2", "PB", "PIF_AVDD", 25.0, 10.0, ("ch1", "s4")))
    board.selected_all.extend(board.selected[-2:])

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    # The board really carries them — strictness never consults the board.
    assert [t.uuid for t in board.adapter.get_tracks()] == ["t1", "t2"]
    assert plan.removed_track_records == [dock._tracks[1], dock._tracks[2]]
    assert any("read instance DAC_BUF" in t for t in _texts(result))
    # C3: the by-cluster path deletes too, but the selection HAS copper — the red
    # "no copper" line must not appear.
    assert not any("the selection has no copper" in t for t in _texts(result))


def test_selection_without_copper_removes_every_record_and_says_so(
        main_window, tmp_path):
    """С-1 п.4: only COMPONENTS selected -> EVERY copper record of the cell is
    removed, and ONE RED Log line says so before the write (no dialog, А0б)."""
    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board(with_extra=False,
                   on_board_tracks=[_live_track("t1", "GND", 10.0, 12.0,
                                                11.0, 12.0)])
    board.selected_all = list(board.selected)          # components only

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    assert len(plan.removed_track_records) == 3
    # EVERY record of the cell is gone — the very same dicts (identity, not
    # order/value: the loader may normalize them).
    assert ({id(r) for r in plan.removed_track_records}
            == {id(r) for r in dock._tracks})
    assert any(level == "error" and "the selection has no copper" in text
               for text, level in result["selection_lines"])


def test_import_adds_new_copper_and_deletes_nothing(main_window, tmp_path):
    """С-1 п.2: Import stays purely ADDITIVE. The selected track pairs the cell's
    only record; the stray track no record describes becomes a NEW record —
    nothing is removed and no strict line appears."""
    dock, _ = _make_dock(main_window, tmp_path,
                         _config_data(tracks=[_track("GND", 0.0)]))
    board = _Board(paired_net="GND", with_extra=True)
    before = [dict(r) for r in dock._tracks]

    result = dock._run_import_vias_tracks(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    assert not hasattr(plan, "removed_track_records")
    assert dock._tracks == before                      # nothing was dropped
    assert len(plan.new_track_records) == 1            # the extra live track
    assert not any("not in the selection" in t for t in _texts(result))


def test_the_refresh_worker_reads_no_ui_thread_board_handle(
        main_window, tmp_path, monkeypatch):
    """Door guard (deepseek.md п.31): with the guard ARMED in ``raise`` mode, the
    refresh worker runs to completion — it never reads the guarded
    ``connection.board`` getter."""
    from gui import connection as conn_mod

    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board(with_extra=False)
    monkeypatch.setattr(conn_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
