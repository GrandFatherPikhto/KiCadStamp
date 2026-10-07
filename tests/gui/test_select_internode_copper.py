# tests/gui/test_select_internode_copper.py
"""Т5 guards of plan_2026_10_05_tree_reread_modules (Denis, 2026-10-07):
"Select inter-node copper" (В1) and "Select recorded inter-node copper" (В2).

В1 highlights what a WHOLE-BOARD re-read would take, so the classifier must be
ONE place: the guard compares В1's selection to `internode_capture.internode_units`
(the SAME step `plan_internode_reread` runs). В2 highlights the live copper of the
tree's records through the SAME `copper_select.record_live_items` the node action
uses. The live board is a small in-memory double; the workers are called directly
(plain data in / plain data out), so no Qt and no KiCad are needed for В1/В2.

Test names describe the PROPERTY (rule §37); each docstring names the Т5 cell.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.config import Config, Entity, NetTrace
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import internode_units, plan_internode_reread
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.select_internode_copper import (
    report_select_inter_node_copper,
    report_select_recorded_inter_node_copper,
    run_select_inter_node_copper_worker,
    run_select_recorded_inter_node_copper_worker,
)


# ── board + config doubles ─────────────────────────────────────────────────

def _fp(ref, role, cluster, x_mm, y_mm, path=("S",)):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6), y=int(y_mm * 1e6)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp.sheet_path_uuids = tuple(path) + (f"own-{ref}",)
    return fp


def _pad(number, net, x_mm, y_mm):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=SimpleNamespace(x=int(x_mm * 1e6),
                                                    y=int(y_mm * 1e6)))


def _track(x1, y1, x2, y2, net, uuid):
    return Track(uuid=uuid,
                 start=SimpleNamespace(x=int(x1 * 1e6), y=int(y1 * 1e6)),
                 end=SimpleNamespace(x=int(x2 * 1e6), y=int(y2 * 1e6)),
                 net_name=net, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _via(x, y, net, uuid):
    return Via(uuid=uuid, position=SimpleNamespace(x=int(x * 1e6),
                                                   y=int(y * 1e6)),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


class _Board:
    """A whole-board reading adapter double: footprints, tracks, vias, pads and
    the ONE selection write (``select_items``)."""

    def __init__(self, fps, pads, tracks, vias=()):
        self._fps = list(fps)
        self._pads = pads
        self._tracks = list(tracks)
        self._vias = list(vias)
        self.selected: list = []
        self.closed = False
        self.raise_on_select = False

    def refresh_board(self):
        pass

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_selected_items(self):
        return []

    def select_items(self, items):
        if self.raise_on_select:
            raise RuntimeError("select failed")
        self.selected = list(items)

    def get_field_value(self, fp, name):
        return {"Role": getattr(fp, "_role", None),
                "Cluster": getattr(fp, "_cluster", None)}.get(name)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, num):
        for pad in self._pads.get(fp.ref, []):
            if str(pad.number) == str(num):
                return pad
        return None

    def get_bounding_boxes(self, items):
        return [SimpleNamespace(
            pos=SimpleNamespace(x=it.position.x - 300_000,
                                y=it.position.y - 300_000),
            size=SimpleNamespace(x=600_000, y=600_000),
            inflate=lambda _d: None) for it in items]

    def close(self):
        self.closed = True


def _node(ref, kind="placement"):
    return TreeNode(ref=ref, kind=kind, xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])


def _tree(name, nodes):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _payload(cfg, tree, sheet_names=None):
    return {"cfg": cfg, "tree": tree, "config_path": "root", "timeout_ms": 1000,
            "sheet_names": sheet_names or {}}


def _install_adapter(monkeypatch, board):
    """Point the worker's OWN adapter factory at `board` (a new adapter IS a new
    socket — the worker builds one; here it is our double)."""
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda **kwargs: board)


def _area_uuid_set(board):
    return {p.uuid for p in board.selected}


# ── В1: exactly what a whole-board re-read would take ──────────────────────

def _one_bridge_board():
    """R1/R3 (SAME Cluster A — one node) with their own copper (CLUSTER), R2
    (Cluster B) bridged to R1 (INTERNODE), FPX (outside any node — FOREIGN) and
    a dead-end track off R2 (STUB). Only the R1<->R2 bridge is worth taking."""
    fps = [_fp("R1", "RA", "A", 10.0, 10.0), _fp("R3", "RC", "A", 10.0, 25.0),
           _fp("R2", "RB", "B", 30.0, 10.0), _fp("FPX", "RX", "X", 50.0, 15.0)]
    pads = {"R1": [_pad("1", "N1", 10.0, 10.0), _pad("2", "N2", 10.0, 15.0)],
            "R3": [_pad("1", "N2", 10.0, 25.0)],
            "R2": [_pad("1", "N1", 30.0, 10.0), _pad("2", "N3", 30.0, 15.0),
                   _pad("3", "N4", 30.0, 20.0)],
            "FPX": [_pad("1", "N3", 50.0, 15.0)]}
    tracks = [_track(10.0, 10.0, 30.0, 10.0, "N1", "bridge"),
              _track(10.0, 15.0, 10.0, 25.0, "N2", "inner"),
              _track(30.0, 15.0, 50.0, 15.0, "N3", "foreign"),
              _track(30.0, 20.0, 30.0, 28.0, "N4", "stub")]
    return _Board(fps, pads, tracks), fps, tracks


def _one_bridge_cfg():
    return Config(entities=[Entity(name="e_a", cell="c", cluster="A", sheet="S"),
                            Entity(name="e_b", cell="c", cluster="B", sheet="S")],
                  trees=[_tree("t", [_node("e_a"), _node("e_b")])])


def test_select_inter_node_copper_selects_exactly_the_bridge(monkeypatch):
    """Т5 cell 1 — only the inter-node bridge is selected; the cluster's own
    copper, the foreign piece and the stub are counted, never selected. Mutation
    'selection takes CLUSTER pieces too' turns this red."""
    board, fps, tracks = _one_bridge_board()
    _install_adapter(monkeypatch, board)
    cfg = _one_bridge_cfg()
    result = run_select_inter_node_copper_worker(
        _payload(cfg, cfg.trees[0], {"S": "S"}))

    assert [t.uuid for t in board.selected] == ["bridge"]
    assert result["pieces"] == 1 and result["tracks"] == 1
    assert result["discarded"] == {"cluster": 1, "foreign": 1, "stub": 1}


def test_select_inter_node_copper_equals_what_a_whole_board_reread_takes(monkeypatch):
    """Т5 cell 2 (the invariant) — the uuid set В1 selects equals the copper of
    the pieces `plan_internode_reread` (whole board) takes, because both run the
    SAME `internode_units`. Mutation 'the reread grows its own classifier' (or
    the extractor being bypassed) turns this red."""
    board, fps, tracks = _one_bridge_board()
    _install_adapter(monkeypatch, board)
    cfg = _one_bridge_cfg()
    tree = cfg.trees[0]
    sheets = {"S": "S"}
    result = run_select_inter_node_copper_worker(_payload(cfg, tree, sheets))

    units, _discarded, _warnings = internode_units(
        board, cfg, tree, area_items=list(tracks), area_footprints=fps,
        sheet_names=sheets)
    reread_uuids = {p.uuid for u in units for p in (*u.tracks, *u.vias)}
    assert _area_uuid_set(board) == reread_uuids
    assert result["pieces"] == len(units) == 1

    plan = plan_internode_reread(board, cfg, tree, area_items=list(tracks),
                                 area_footprints=fps, sheet_names=sheets)
    assert len(plan.added) == result["pieces"]      # one fresh record per piece


def test_another_instances_bridge_of_the_same_cluster_is_not_selected(monkeypatch):
    """Т5 cell 3 — the tree references ONLY Channel_0's nodes; the SAME Cluster
    tags on Channel_1 belong to another instance and stay FOREIGN. Mutation 'the
    node key is the label again (channels merge)' turns the Channel_1 bridge
    INTERNODE and this red."""
    fps, pads, tracks = [], {}, []
    for ch, (y, cluster) in enumerate([(10.0, "Channel_0"), (30.0, "Channel_1")]):
        dac = _fp(f"IC{ch}", "AD_DAC", "DAC_BUF", 10.0, y, (f"ch{ch}",))
        out = _fp(f"R{ch}", "AD_OUT", "DAC_OUT", 30.0, y, (f"ch{ch}",))
        fps += [dac, out]
        pads[dac.ref] = [_pad("1", f"X{ch}", 10.0, y)]
        pads[out.ref] = [_pad("1", f"X{ch}", 30.0, y)]
        tracks.append(_track(10.0, y, 30.0, y, f"X{ch}", f"bridge{ch}"))
    board = _Board(fps, pads, tracks)
    _install_adapter(monkeypatch, board)
    cfg = Config(
        entities=[Entity(name="e_dac", cell="c", cluster="DAC_BUF",
                         sheet="Channel_0"),
                  Entity(name="e_out", cell="c", cluster="DAC_OUT",
                         sheet="Channel_0")],
        trees=[_tree("t", [_node("e_dac"), _node("e_out")])])
    result = run_select_inter_node_copper_worker(
        _payload(cfg, cfg.trees[0], {"ch0": "Channel_0", "ch1": "Channel_1"}))
    assert result["pieces"] == 1
    assert [t.uuid for t in board.selected] == ["bridge0"]
    assert result["discarded"].get("foreign") == 1


def test_a_worker_that_takes_nothing_names_the_trees_nodes(monkeypatch, caplog):
    """Т5 cell 3b (the yellow line) — nothing taken gives a WARNING that names
    the tree's node keys, never an empty 'selected 0'."""
    import logging

    board, fps, _tracks = _one_bridge_board()
    _install_adapter(monkeypatch, board)
    cfg = _one_bridge_cfg()
    # An empty area: the whole board has no copper at all here.
    board._tracks = []
    result = run_select_inter_node_copper_worker(_payload(cfg, cfg.trees[0]))
    assert result["pieces"] == 0 and result["node_keys"]
    with caplog.at_level(logging.WARNING):
        report_select_inter_node_copper(result)
    assert "no inter-node copper of tree 't'" in caplog.text
    assert "A/S" in caplog.text and "B/S" in caplog.text


# ── В2: the live copper of the tree's records ──────────────────────────────

def _two_record_tree_cfg():
    records = [NetTrace(net="N", name="recA", anchor_role="RA", anchor_pad="1",
                        tracks=[]),
               NetTrace(net="M", name="recB", anchor_role="RB", anchor_pad="1",
                        tracks=[])]
    tree = _tree("t", [_node("recA", "net_trace"), _node("recB", "net_trace")])
    return Config(entities=[], net_traces=records, trees=[tree]), tree


def test_select_recorded_copper_selects_the_live_one_and_names_the_missing(
        monkeypatch, caplog):
    """Т5 cell 4 — two records; one has copper on the board, the other does not.
    The first's copper is selected (through the SAME record_live_items the node
    action uses), the second is named. Mutation 'B2 skips the second record'
    turns the missing line (and the count) red."""
    import logging

    from gui.docks import copper_select

    board = _Board([], {}, [])
    _install_adapter(monkeypatch, board)
    live = _track(10.0, 10.0, 20.0, 10.0, "N", "liveA")
    board._tracks = [live]

    def fake_readonly(adapter, config_path):
        return SimpleNamespace(entries={}), SimpleNamespace(entries={})

    by_name = {
        "recA": SimpleNamespace(found=[live], identity="recA"),
        "recB": SimpleNamespace(found=[], identity="recB"),
    }

    def fake_record_live_items(adapter, record, *, via_registry, track_registry,
                               sheet_names=None):
        return by_name[record.name]

    monkeypatch.setattr(copper_select, "_readonly_registries", fake_readonly)
    monkeypatch.setattr(copper_select, "record_live_items", fake_record_live_items)

    cfg, tree = _two_record_tree_cfg()
    result = run_select_recorded_inter_node_copper_worker(_payload(cfg, tree))
    assert result["records"] == 2 and result["selected_records"] == 1
    assert [t.uuid for t in board.selected] == ["liveA"]
    assert result["missing"] == ["recB"]

    with caplog.at_level(logging.INFO):
        report_select_recorded_inter_node_copper(result)
    assert "Selected recorded copper of 1 record(s) of tree 't'" in caplog.text
    assert "recB" in caplog.text


# ── the socket contract: the worker ALWAYS closes its own adapter ──────────

def test_the_worker_closes_its_own_adapter_on_both_paths(monkeypatch):
    """Т5 cell 6 (part) — the worker's OWN adapter is closed in the `finally`,
    on the happy path AND when the selection raises. Mutation 'worker without a
    finally: close()' turns the second half red."""
    board, fps, _tracks = _one_bridge_board()
    _install_adapter(monkeypatch, board)
    cfg = _one_bridge_cfg()
    run_select_inter_node_copper_worker(_payload(cfg, cfg.trees[0], {"S": "S"}))
    assert board.closed is True

    board2, _fps, _t = _one_bridge_board()
    board2.raise_on_select = True
    _install_adapter(monkeypatch, board2)
    with pytest.raises(RuntimeError):
        run_select_inter_node_copper_worker(_payload(cfg, cfg.trees[0], {"S": "S"}))
    assert board2.closed is True


def test_the_recorded_worker_closes_its_own_adapter_on_both_paths(monkeypatch):
    """Доделка 4а / C4 — the В2 worker's OWN adapter is closed in the `finally`,
    on the happy path AND when the selection raises (the same socket contract the
    В1 cell above pins). Mutation 'B2 worker without finally close' turns the
    second half red."""
    from gui.docks import copper_select

    def fake_readonly(adapter, config_path):
        return SimpleNamespace(entries={}), SimpleNamespace(entries={})

    live = _track(10.0, 10.0, 20.0, 10.0, "N", "liveA")

    def fake_record_live_items(adapter, record, *, via_registry, track_registry,
                               sheet_names=None):
        return SimpleNamespace(found=[live] if record.name == "recA" else [],
                               identity=record.name)

    monkeypatch.setattr(copper_select, "_readonly_registries", fake_readonly)
    monkeypatch.setattr(copper_select, "record_live_items", fake_record_live_items)
    cfg, tree = _two_record_tree_cfg()

    board = _Board([], {}, [])
    board._tracks = [live]
    _install_adapter(monkeypatch, board)
    run_select_recorded_inter_node_copper_worker(_payload(cfg, tree))
    assert board.closed is True

    board2 = _Board([], {}, [])
    board2.raise_on_select = True
    _install_adapter(monkeypatch, board2)
    with pytest.raises(RuntimeError):
        run_select_recorded_inter_node_copper_worker(_payload(cfg, tree))
    assert board2.closed is True


# ── the GUI wiring: worker on the socket, no UI-thread board read, refusal ──

def _minimal_root(tmp_path):
    """A loadable root with one tree of two placement nodes (Clusters A/B on
    sheet S) — enough for the dock's select entries to reach the worker."""
    from kicadstamp.config.sexp_format import dict_to_sexp

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "cells": {"c": {"components": [{"role": "R", "offset_along_mm": 0.0,
                                        "offset_across_mm": 0.0}]}},
        "entities": [{"name": "e_a", "cell": "c", "cluster": "A", "sheet": "S"},
                     {"name": "e_b", "cell": "c", "cluster": "B", "sheet": "S"}],
        "trees": [{"name": "t", "anchor": {"origin": True},
                   "nodes": [{"ref": "e_a", "kind": "placement",
                              "xy": [0.0, 0.0]},
                             {"ref": "e_b", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    }, format_number=2), encoding="utf-8")
    return root


def _dock_with_tree(main_window, tmp_path):
    from gui.docks.trees_dock import TreesDock

    dock = TreesDock(main_window)
    dock.set_root_file(_minimal_root(tmp_path))
    return dock


def test_the_dock_wires_the_copper_selection_through_start_long_op(
        main_window, tmp_path, monkeypatch):
    """Т5 cell 6 (the door) — В1/В2 hand the work to start_long_op (a worker owns
    the socket) and the UI thread NEVER reads the board: with the door armed to
    RAISE on a UI-thread `.board` read, both entries run and queue a worker.
    Mutation 'the dock reads the board directly' turns this red."""
    import gui.connection as connection_mod
    import gui.worker as worker_mod
    from PyQt6.QtGui import QAction

    dock = _dock_with_tree(main_window, tmp_path)
    started = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or object())
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)

    dock._on_select_inter_node_copper(QAction("a"))
    dock._on_select_recorded_inter_node_copper(QAction("b"))
    assert len(started) == 2


def test_a_read_only_template_instance_refuses_both_select_actions(
        main_window, tmp_path, monkeypatch):
    """Т5 cell 5 — a template instance (read-only) refuses both select actions
    exactly like the re-read does, BEFORE any worker starts (no `_warn…` message
    here: the guard is stubbed positive)."""
    import gui.worker as worker_mod
    from PyQt6.QtGui import QAction

    dock = _dock_with_tree(main_window, tmp_path)
    monkeypatch.setattr(dock, "_warn_read_only_instance", lambda tree: True)
    started = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: started.append(a) or object())

    dock._on_select_inter_node_copper(QAction("a"))
    dock._on_select_recorded_inter_node_copper(QAction("b"))
    assert started == []
