# tests/gui/docks/test_cell_editor_mixed_selection.py
"""Worker-level cells for the MIXED-selection prelude of Update from selection
and Import vias/tracks from selection
(plan_2026_10_04_refresh_mixed_cluster_selection, plan item 3).

These drive the real ``CellDock._run_refresh_geometry`` /
``_run_import_vias_tracks`` with a real config on disk and a fake board whose
selection MIXES the edited cell's cluster with a foreign one. They prove the
selection-after-read contract: ``adapter.select_items`` receives the chosen
instance's components AND all the copper that entered the read — never the
foreign components/copper.

Headless: the workers are driven directly (no threads), the copper-layer warning
is stubbed. The config is a format-2 s-expr the reader lifts (CURRENT_FORMAT=3).
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.board_layers import ALL_COPPER_LAYERS
from gui.docks.cell_editor import CellDock
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2


def _config_data(vias=None, tracks=None):
    return {
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": [
                {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
                {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
            ],
            "vias": list(vias or []), "tracks": list(tracks or []),
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


def _via(uuid, x_mm, y_mm):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm),
               net_name=None, drill_mm=0.3, diameter_mm=0.6)


class _MixedBoard:
    """A board whose selection is DAC_BUF (the edited cell) + a foreign PIF
    cluster, plus one UNREGISTERED via (kept and read the ordinary way)."""

    def __init__(self):
        self.selected = [
            _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0", "s1")),
            _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0", "s2")),
            _fp("P1", "PA", "PIF_AVDD", 20.0, 10.0, ("ch1", "s3")),
            _fp("P2", "PB", "PIF_AVDD", 25.0, 10.0, ("ch1", "s4")),
        ]
        self.via = _via("v-unreg", 10.0, 11.0)
        self.selected_all = list(self.selected) + [self.via]
        self.selected_calls = []
        self.adapter = SimpleNamespace(
            refresh_board=lambda: None,
            get_selected_items=lambda: list(self.selected_all),
            get_field_value=lambda fp, name: (
                getattr(fp, "_role", None)
                if name == "Role" else getattr(fp, "_cluster", None)),
            get_footprint_pads=lambda fp: [],
            select_items=lambda items: self.selected_calls.append(list(items)),
        )


class _MixedTrackBoard:
    """The same DAC_BUF + foreign PIF selection, but the read copper is TRACKS:
    one live track the cell's own record pairs, one live track no record
    describes (the new copper). Tracks have no ``ref``, so the selection-after-
    read is read back by uuid."""

    def __init__(self):
        self.selected = [
            _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0", "s1")),
            _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0", "s2")),
            _fp("P1", "PA", "PIF_AVDD", 20.0, 10.0, ("ch1", "s3")),
            _fp("P2", "PB", "PIF_AVDD", 25.0, 10.0, ("ch1", "s4")),
        ]
        self.track = Track(uuid="t-unreg", net_name=None,
                           start=Vector2.from_xy_mm(10.0, 11.0),
                           end=Vector2.from_xy_mm(11.0, 11.0),
                           width_mm=0.25, layer=BoardLayer.BL_F_Cu)
        self.track_extra = Track(uuid="t-extra", net_name="GND2",
                                 start=Vector2.from_xy_mm(30.0, 30.0),
                                 end=Vector2.from_xy_mm(31.0, 30.0),
                                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)
        self.selected_all = list(self.selected) + [self.track, self.track_extra]
        self.selected_calls = []
        self.adapter = SimpleNamespace(
            refresh_board=lambda: None,
            get_selected_items=lambda: list(self.selected_all),
            get_field_value=lambda fp, name: (
                getattr(fp, "_role", None)
                if name == "Role" else getattr(fp, "_cluster", None)),
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
        "board": board,
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
    }


def test_refresh_selects_the_instance_and_the_read_copper(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    board = _MixedBoard()

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    assert len(board.selected_calls) == 1
    refs = [getattr(i, "ref", None) if isinstance(i, Footprint) else i.uuid
            for i in board.selected_calls[0]]
    assert refs == ["C1", "C2", "v-unreg"]
    # The prelude's Log lines reach the finish handler.
    assert result["selection_lines"]
    texts = [text for text, _level in result["selection_lines"]]
    assert any("read instance DAC_BUF" in text for text in texts)


def test_import_selects_the_instance_and_the_read_copper(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    board = _MixedBoard()

    result = dock._run_import_vias_tracks(_payload(dock, board))

    assert "plan" in result, result
    assert len(board.selected_calls) == 1
    refs = [getattr(i, "ref", None) if isinstance(i, Footprint) else i.uuid
            for i in board.selected_calls[0]]
    assert refs == ["C1", "C2", "v-unreg"]
    assert result["selection_lines"]


def test_clean_selection_does_not_select_or_keep_context(main_window, tmp_path):
    """A CLEAN selection (one cluster instance) is not touched by the prelude —
    no select_items, no mixed-selection Log line (today's behaviour)."""
    dock, _ = _make_dock(main_window, tmp_path)
    clean = _MixedBoard()
    clean.selected_all = list(clean.selected[:2]) + [clean.via]

    result = dock._run_refresh_geometry(_payload(dock, clean))

    assert "plan" in result, result
    assert clean.selected_calls == []
    assert not result.get("selection_lines")


def test_mixed_refresh_keeps_the_unpaired_track(main_window, tmp_path):
    """Denis 2026-10-05, the SOFT keep_unpaired mode: a MIXED read with ONE
    track the cell's records do not pair leaves that record COMPLETELY as it is
    (not deleted, not a fatal), refreshes the paired record, still adds the live
    copper no record describes, NAMES the kept record in the Log and makes the
    selection-after-read. (Supersedes the strict refusal this cell asserted
    before the decision.)"""
    paired = {"net": None, "layer": "F.Cu", "width_mm": 0.25,
              "start_along_mm": 0.0, "start_across_mm": 0.0,
              "end_along_mm": 1.0, "end_across_mm": 0.0}
    unpaired = {"net": "GND", "layer": "F.Cu", "width_mm": 0.25,
                "start_along_mm": 0.0, "start_across_mm": 2.0,
                "end_along_mm": 1.0, "end_across_mm": 2.0}
    dock, _ = _make_dock(main_window, tmp_path,
                         _config_data(tracks=[paired, unpaired]))
    board = _MixedTrackBoard()

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    # nothing doomed; the unpaired record is intact and NOT among the updates.
    assert plan.removed_via_records == [] and plan.removed_track_records == []
    kept = dock._tracks[1]
    assert kept["net"] == "GND" and kept["start_across_mm"] == 2.0
    assert all(rec is not kept for rec, _geo in plan.track_updates)
    # the paired record WAS refreshed, and the new copper is still added.
    assert len(plan.track_updates) == 1
    assert len(plan.new_track_records) == 1
    # the Log line names the kept record.
    texts = [text for text, _level in result["selection_lines"]]
    assert any("left as they are" in text for text in texts)
    assert any("GND" in text for text in texts)
    # the selection-after-read was made (instance components + read copper).
    assert len(board.selected_calls) == 1
    refs = [getattr(i, "ref", None) if isinstance(i, Footprint) else i.uuid
            for i in board.selected_calls[0]]
    assert refs == ["C1", "C2", "t-unreg", "t-extra"]


def test_clean_refresh_still_deletes_the_unpaired_track(main_window, tmp_path):
    """The CLEAN path is untouched (Denis 2026-10-05): a clean selection still
    DELETES a record with no live pair (remove_missing=True), produces no
    keep-unpaired Log line, and never re-selects. The read-back after a mixed
    read relies on exactly this."""
    unpaired = {"net": "GND", "layer": "F.Cu", "width_mm": 0.25,
                "start_along_mm": 0.0, "start_across_mm": 2.0,
                "end_along_mm": 1.0, "end_across_mm": 2.0}
    dock, _ = _make_dock(main_window, tmp_path, _config_data(tracks=[unpaired]))
    clean = _MixedTrackBoard()
    clean.selected_all = list(clean.selected[:2]) + [clean.track]

    result = dock._run_refresh_geometry(_payload(dock, clean))

    assert "plan" in result, result
    plan = result["plan"]
    assert plan.removed_track_records == list(dock._tracks)
    assert plan.unpaired_reports == []
    assert clean.selected_calls == []
    assert not result["selection_lines"]
