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
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2


def _config_data(vias=None):
    return {
        "cells": {"dac_buf": {
            "layer": "F.Cu",
            "components": [
                {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
                {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
                 "angle_deg": 0.0},
            ],
            "vias": list(vias or []), "tracks": [], "clone_placements": [],
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


def test_mixed_refresh_does_not_delete_unpaired_records(main_window, tmp_path):
    """N1(a): in a MIXED selection a record with no live pair is NOT deleted —
    the plan refuses (remove_missing=False keeps today's count fatal, never the
    old silent deletion) and the record is intact; the Log says why."""
    unpaired = {"offset_along_mm": 1.0, "offset_across_mm": 2.0, "net": "GND",
                "drill_mm": 0.3, "diameter_mm": 0.6}
    dock, _ = _make_dock(main_window, tmp_path, _config_data(vias=[unpaired]))
    board = _MixedBoard()

    result = dock._run_refresh_geometry(_payload(dock, board))

    # No plan and no deletion: the cell's own record is untouched.
    assert "plan" not in result, result
    assert "error" in result, result
    assert len(dock._vias) == 1 and dock._vias[0]["net"] == "GND"
    texts = [text for text, _level in result.get("selection_lines") or []]
    assert any("not deleted" in text for text in texts)
