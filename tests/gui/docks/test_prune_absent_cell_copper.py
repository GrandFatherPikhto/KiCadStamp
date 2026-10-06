# tests/gui/docks/test_prune_absent_cell_copper.py
"""Worker-level cells for the CORRECTED Н4 п.5 — a cell record whose copper left
the board (plan_2026_10_06_prune_absent_cell_copper; Denis 2026-10-05/06).

These drive the REAL ``CellDock._run_refresh_geometry`` /
``_run_import_vias_tracks`` with a real config on disk and a fake board whose
selection holds ONE of the cell's three tracks. They prove the rule Denis
stated:

  * a record whose copper is NOT found by the registry uuid but IS found by
    GEOMETRY (the dry run of this instance's recording) is KEPT and named;
  * a record the REGISTRY still knows as live is KEPT even when geometry misses;
  * a record found by NEITHER is DELETED, with the "(by registry and by
    geometry)" summary line;
  * a dry run that yielded nothing deletes NOTHING ("could not check");
  * Import stays purely ADDITIVE (it never deletes).

The geometry half drives a real ApplyPipeline of its own, so these cells stub
``cell_editor_mod.instance_copper_presence`` with a crafted
``BoardCopperPresence`` — the board boundary is faked, the worker path and the
pure rule are real.
"""
from types import SimpleNamespace

import pytest

import gui.docks.cell_editor as cell_editor_mod
from gui.board_layers import ALL_COPPER_LAYERS
from gui.docks.cell_editor import CellDock
from kicadstamp.absent_copper_prune import BoardCopperPresence
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.registry import make_registry_key, record_key_part


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
    """DAC_BUF instance on the board: its two components, ONE selected track
    that pairs record 0, and ONE extra selected track no record describes. The
    board ALSO carries a track uuid the registry may name (guard 2)."""

    def __init__(self, board_tracks=(), paired_net=None):
        self.selected = [
            _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0", "s1")),
            _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0", "s2")),
        ]
        self.track0 = _live_track("t0", paired_net, 10.0, 10.0, 11.0, 10.0)
        self.extra = _live_track("t-extra", "GNDX", 30.0, 30.0, 31.0, 30.0)
        self.selected_all = list(self.selected) + [self.track0, self.extra]
        self._board_tracks = list(board_tracks)
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
        "board": board,
        "timeout_ms": 50,
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


def _presence(monkeypatch, indices=(), checked=True):
    def _stub(*_a, **_k):
        if not checked:
            return BoardCopperPresence()
        return BoardCopperPresence(
            on_board=frozenset(("track", SPOKE_LEVEL_ROLE_PLACEHOLDER, i)
                               for i in indices),
            planned=3, checked=True)
    monkeypatch.setattr(cell_editor_mod, "instance_copper_presence", _stub)


def _texts(result):
    return [text for text, _level in result["selection_lines"]]


def test_geometry_keeps_a_record_whose_registry_uuid_is_stale(
        main_window, tmp_path, monkeypatch):
    """Дефект 2: the second track's stored geometry is on the board (the dry run
    matched it by geometry) although the registry no longer knows its uuid -> the
    record is KEPT and named. The third track is on the board by NEITHER half ->
    DELETED, and the summary says so. This is the cell the whole plan exists
    for: the old rule deleted every record whose uuid went stale."""
    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board()
    _presence(monkeypatch, indices=(1,))

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    kept = dock._tracks[1]
    gone = dock._tracks[2]
    assert plan.removed_track_records == [gone]
    assert all(rec is not kept for rec, _geo in plan.track_updates)
    texts = _texts(result)
    assert any("left as they are" in t and "GND" in t for t in texts)
    assert any("removed 1 record(s)" in t and "by registry and by geometry" in t
               for t in texts)


def test_registry_keeps_a_record_geometry_missed(main_window, tmp_path,
                                                 monkeypatch):
    """Guard 2: the third record's registry uuid is LIVE even though geometry did
    NOT match it -> the registry half alone keeps it. Only the record neither
    half found is deleted."""
    dock, target = _make_dock(main_window, tmp_path)
    cfg, _ctx = load_config(str(target))
    cell_identity = record_key_part("dac_buf", cfg.cells["dac_buf"].uuid)
    key = make_registry_key("anchor:C1", cell_identity, None, 2)
    entries = {key: SimpleNamespace(uuid="u-live")}
    monkeypatch.setattr("gui.mixed_selection.load_registry_entries",
                        lambda config_path, cfg=None: ({}, entries,
                                                       {"u-live": key}))
    board = _Board(board_tracks=[_live_track("u-live", "GND2", 40.0, 40.0,
                                             41.0, 40.0)])
    _presence(monkeypatch, indices=())            # geometry matches nothing

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    kept_registry = dock._tracks[2]
    gone = dock._tracks[1]
    assert plan.removed_track_records == [gone]
    assert all(rec is not kept_registry for rec, _geo in plan.track_updates)
    assert any("GND2" in t and "left as they are" in t for t in _texts(result))


def test_a_dry_run_that_yielded_nothing_deletes_nothing(main_window, tmp_path,
                                                        monkeypatch):
    """Guard 3: the geometry half could not run (the dry run produced no command
    — a refused tree, a chain-only placement, an unrealized record). The rule
    deletes NOTHING and says which record it could not check."""
    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board()
    _presence(monkeypatch, checked=False)

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    assert plan.removed_track_records == []
    texts = _texts(result)
    assert any("could not check the board for record" in t for t in texts)
    assert not any("removed " in t and "by registry and by geometry" in t
                   for t in texts)


def test_import_never_deletes_and_stays_additive(main_window, tmp_path,
                                                 monkeypatch):
    """Guard 4: the SAME call of the rule through the IMPORT door — Import never
    removes a record, and its call receives the cell's OWN record lists (a broken
    import call is a TypeError, killing the mutation). The cell has ONE track so
    the strictly-additive Import plan has no unpaired record (a missing pair is a
    count fatal there)."""
    dock, _ = _make_dock(main_window, tmp_path,
                         _config_data(tracks=[_track("GND", 0.0)]))
    board = _Board(paired_net="GND")
    _presence(monkeypatch, indices=())
    before = [dict(r) for r in dock._tracks]

    result = dock._run_import_vias_tracks(_payload(dock, board))

    assert "plan" in result, result
    plan = result["plan"]
    assert not hasattr(plan, "removed_track_records")
    assert dock._tracks == before                      # nothing was dropped
    assert len(plan.new_track_records) == 1            # the extra live track
    assert not any("removed " in t for t in _texts(result))


def test_the_refresh_worker_reads_no_ui_thread_board_handle(
        main_window, tmp_path, monkeypatch):
    """Door guard (deepseek.md п.31): with the guard ARMED in ``raise`` mode, the
    refresh worker runs to completion — it never reads the guarded
    ``connection.board`` getter (its geometry half builds its OWN adapter)."""
    from gui import connection as conn_mod

    dock, _ = _make_dock(main_window, tmp_path)
    board = _Board()
    _presence(monkeypatch, indices=(1,))
    monkeypatch.setattr(conn_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(conn_mod, "ui_thread_read_refusal", conn_mod.UI_READ_RAISE)

    result = dock._run_refresh_geometry(_payload(dock, board))

    assert "plan" in result, result
