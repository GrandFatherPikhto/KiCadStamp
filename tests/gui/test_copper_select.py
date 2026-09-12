# tests/gui/test_copper_select.py
"""
Э2 GUI tests for "Select copper on board" (plan_2026_09_12_select_copper_by_record;
design §12.1): a `net_traces:` record -> its live copper, highlighted in the PCB
editor.

Covered:
  * resolve_record(): by identity, by a unique net, and an honest error for an
    ambiguous net (two bridges of one net are legal since 2026-09-12);
  * the Log report: tier named, partial/missing counts, nothing-found with its
    reason, and the "previous selection was replaced" note;
  * the worker selects exactly the matched copper and is READ-ONLY otherwise
    (the registries are opened but never saved);
  * the TreesDock context menu offers the action on a kind="net_trace" node
    (and not on a placement node), and its finish path logs the report;
  * the NetTraceDock button acts on the record the flat list OPENED (identity,
    so a two-bridge net is never guessed) and logs; a missing board is a Log
    message, not a crash.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import Config, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.board import BoardLayer, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.utils.units import MM

import gui.docks.copper_select as copper_select_mod
import gui.docks.net_trace as net_trace_mod
import gui.docks.trees_dock as trees_dock_mod
from gui.docks.copper_select import (resolve_record, select_copper_report_lines,
                                     _tier_label)
from gui.docks.net_trace import NetTraceDock
from gui.docks.trees_dock import TreesDock


# ── helpers ────────────────────────────────────────────────────────────────

def _record(name="bridge", net="N", anchor_role="FPGA"):
    return NetTrace(
        net=net, name=name, anchor_role=anchor_role, anchor_pad="42",
        tracks=[TemplateTrack(start_along_mm=1, start_across_mm=2,
                              end_along_mm=3, end_across_mm=4, width_mm=0.2,
                              net=net, layer="F.Cu")],
        vias=[TemplateVia(offset_along_mm=5, offset_across_mm=6, net=net,
                          drill_mm=0.3, diameter_mm=0.6)],
    )


def _vp(x_mm, y_mm):
    return Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))


def _live_track(uuid="hand-trk"):
    return Track(uuid=uuid, start=_vp(53, 54), end=_vp(55, 56), net_name="N",
                 width_mm=0.2, layer=BoardLayer.BL_F_Cu)


def _live_via(uuid="hand-via"):
    return Via(uuid=uuid, position=_vp(57, 58), net_name="N",
               drill_mm=0.3, diameter_mm=0.6)


def _fake_adapter(live_tracks=(), live_vias=(), selected=()):
    fp = MagicMock()
    fp.ref = "U1"
    fp.position = _vp(50, 50)
    fp.angle_deg = 0.0
    fp._role = "FPGA"
    pad42 = MagicMock()
    pad42.position = _vp(52, 52)
    adapter = MagicMock()
    adapter.get_footprints.return_value = [fp]
    adapter.get_field_value.side_effect = (
        lambda f, name: getattr(f, "_role", None) if name == "Role" else None)
    adapter.get_pad_by_number.side_effect = lambda f, num: (
        pad42 if str(num) == "42" else None)
    adapter.get_tracks.return_value = list(live_tracks)
    adapter.get_vias.return_value = list(live_vias)
    adapter.get_selected_items.return_value = list(selected)
    return adapter


def _report(**overrides):
    base = {"identity": "bridge", "found": 2, "expected": 2, "missing": 0,
            "tracks": 1, "vias": 1, "tier": "by geometry", "reason": None,
            "previous_selection": 1}
    base.update(overrides)
    return base


# ── resolve_record ─────────────────────────────────────────────────────────

def test_resolve_record_by_identity():
    cfg = Config(net_traces=[_record("a"), _record("b")])
    record, error = resolve_record(cfg, identity="b")
    assert record.name == "b" and error is None


def test_resolve_record_by_unique_net():
    cfg = Config(net_traces=[_record("a")])
    record, error = resolve_record(cfg, identity=None, net="N")
    assert record.name == "a" and error is None


def test_resolve_record_ambiguous_net_is_an_honest_error():
    """Two bridges of one net: never guess which one the user meant."""
    cfg = Config(net_traces=[_record("a"), _record("b")])
    record, error = resolve_record(cfg, identity=None, net="N")
    assert record is None
    assert "2 net_traces records" in error


def test_resolve_record_missing_identity():
    cfg = Config(net_traces=[_record("a")])
    record, error = resolve_record(cfg, identity="ghost")
    assert record is None and "ghost" in error


# ── report lines ───────────────────────────────────────────────────────────

def test_report_lines_found_name_the_tier():
    lines = "\n".join(select_copper_report_lines(_report()))
    assert "selected 2 of 2" in lines
    assert "by geometry" in lines          # "copper not adopted yet" is visible
    assert "previous board selection was replaced" in lines


def test_report_lines_nothing_found_is_not_an_error():
    lines = "\n".join(select_copper_report_lines(
        _report(found=0, missing=2, tier=None, reason="anchor did not resolve")))
    assert "not found" in lines
    assert "anchor did not resolve" in lines
    assert "previous board selection was replaced" in lines


def test_report_lines_zero_expected():
    lines = "\n".join(select_copper_report_lines(
        _report(found=0, expected=0, missing=0, tier=None)))
    assert "no tracks or vias" in lines


def test_tier_label_combines_both():
    assert "registry" in _tier_label(1, 0)
    assert "geometry" in _tier_label(0, 1)
    assert "registry" in _tier_label(1, 1) and "geometry" in _tier_label(1, 1)
    assert _tier_label(0, 0) is None


# ── the worker ─────────────────────────────────────────────────────────────

def test_worker_selects_the_matched_copper_and_is_read_only(tmp_path, monkeypatch):
    adapter = _fake_adapter(live_tracks=[_live_track()], live_vias=[_live_via()],
                            selected=["something"])
    monkeypatch.setattr(copper_select_mod, "_live_adapter", lambda: adapter)
    config_path = tmp_path / "root.sexp"
    config_path.write_text("", encoding="utf-8")
    via_reg = tmp_path / "registry" / "root.registry.json"
    trk_reg = tmp_path / "tracks" / "root.tracks.registry.json"

    report = copper_select_mod.run_select_record_copper_worker(
        {"record": _record(), "config_path": str(config_path), "sheet_names": {}})

    selected = adapter.select_items.call_args[0][0]
    assert {i.uuid for i in selected} == {"hand-trk", "hand-via"}
    assert report["found"] == 2 and report["missing"] == 0
    assert report["tracks"] == 1 and report["vias"] == 1
    assert report["tier"] == "by geometry"
    assert report["previous_selection"] == 1
    # READ-ONLY: the registries were opened but never written.
    assert not via_reg.exists() and not trk_reg.exists()


def test_worker_with_nothing_found_clears_the_selection(tmp_path, monkeypatch):
    adapter = _fake_adapter()
    monkeypatch.setattr(copper_select_mod, "_live_adapter", lambda: adapter)
    config_path = tmp_path / "root.sexp"
    config_path.write_text("", encoding="utf-8")

    report = copper_select_mod.run_select_record_copper_worker(
        {"record": _record(), "config_path": str(config_path), "sheet_names": {}})

    assert adapter.select_items.call_args[0][0] == []
    assert report["found"] == 0 and report["missing"] == 2
    assert report["tier"] is None


# ── TreesDock: context menu + finish path ───────────────────────────────────

def _root(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "cells": {"c": {"components": [{"role": "R", "offset_along_mm": 0.0,
                                        "offset_across_mm": 0.0}]}},
        "entities": [{"name": "e_a", "cell": "c", "cluster": "A"}],
        "net_traces": [{"net": "N", "name": "bridge_one",
                        "anchor_role": "A", "tracks": [], "vias": []}],
        "trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
            {"ref": "e_a", "kind": "placement", "xy": [0.0, 0.0]},
            {"ref": "bridge_one", "kind": "net_trace"},
        ]}],
    }), encoding="utf-8")
    return root


def _dock(main_window, tmp_path):
    root = _root(tmp_path)
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


class _FakeQMenu:
    instances: list = []

    def __init__(self, parent=None):
        self.labels = []
        self.actions = []
        _FakeQMenu.instances.append(self)

    def addAction(self, text):
        self.labels.append(text)
        action = SimpleNamespace(triggered=SimpleNamespace(connect=lambda cb: None))
        self.actions.append(action)
        return action

    def addSeparator(self):
        pass

    def exec(self, *args):
        return None


def _menu_for(dock, tree, target_node, monkeypatch):
    _FakeQMenu.instances = []
    item = dock._node_items[target_node.ref]
    fake_widget = MagicMock()
    fake_widget.itemAt.return_value = item
    monkeypatch.setattr(trees_dock_mod, "QMenu", _FakeQMenu)
    monkeypatch.setattr(dock, "_current_tree", lambda: tree)
    monkeypatch.setattr(dock, "_current_tree_widget", lambda: fake_widget)
    dock._on_context_menu(SimpleNamespace())
    return _FakeQMenu.instances[-1]


def test_context_menu_offers_select_copper_on_net_trace_node(main_window, tmp_path, monkeypatch):
    dock, _root_path = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    node = next(n for n in tree.nodes if n.kind == "net_trace")

    menu = _menu_for(dock, tree, node, monkeypatch)

    assert "Select copper on board" in menu.labels


def test_context_menu_does_not_offer_it_on_a_placement_node(main_window, tmp_path, monkeypatch):
    dock, _root_path = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    node = next(n for n in tree.nodes if n.kind == "placement")

    menu = _menu_for(dock, tree, node, monkeypatch)

    assert "Select copper on board" not in menu.labels


def test_tree_action_starts_worker_and_logs_the_report(main_window, tmp_path, monkeypatch, caplog):
    dock, root_path = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    node = next(n for n in tree.nodes if n.kind == "net_trace")
    launched = []

    def fake_start(connection, widgets, fn, on_success, on_error, payload):
        launched.append(payload)
        on_success(_report(identity="bridge_one"))
        return object()

    monkeypatch.setattr(trees_dock_mod, "start_long_op", fake_start)
    caplog.set_level(10)
    dock._on_select_copper_by_record(node)

    assert launched[0]["record"].name == "bridge_one"
    assert launched[0]["config_path"] == str(root_path)
    assert "selected 2 of 2" in caplog.text
    assert "previous board selection was replaced" in caplog.text


def test_tree_action_unknown_record_logs_error_and_launches_nothing(
        main_window, tmp_path, monkeypatch, caplog):
    dock, _root_path = _dock(main_window, tmp_path)
    unknown = SimpleNamespace(ref="ghost", kind="net_trace")
    launched = []
    monkeypatch.setattr(trees_dock_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    caplog.set_level(10)

    dock._on_select_copper_by_record(unknown)

    assert launched == []
    assert "ghost" in caplog.text


# ── NetTraceDock: the button ────────────────────────────────────────────────

def _dock_with_two_bridges(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"net_traces": [
        {"net": "N", "name": "bridge_one", "anchor_role": "A", "tracks": [], "vias": []},
        {"net": "N", "name": "bridge_two", "anchor_role": "A", "tracks": [], "vias": []},
    ]}), encoding="utf-8")
    dock = NetTraceDock(main_window)
    dock.set_root_path(root)
    dock._connection.board = SimpleNamespace(adapter=MagicMock())
    return dock


def test_dock_button_uses_the_opened_record_identity(main_window, tmp_path, monkeypatch):
    """Two bridges of one net: the button must act on the record the flat list
    OPENED, never resolve the net ambiguously."""
    dock = _dock_with_two_bridges(main_window, tmp_path)
    dock.load_entry({"net": "N", "name": "bridge_two", "anchor_role": "A"})
    launched = []

    def fake_start(connection, widgets, fn, on_success, on_error, payload):
        launched.append(payload)
        return object()

    monkeypatch.setattr(net_trace_mod, "start_long_op", fake_start)
    dock._on_select_copper()

    assert launched and launched[0]["record"].name == "bridge_two"


def test_dock_button_without_board_is_a_log_message(main_window, tmp_path, monkeypatch, caplog):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"net_traces": []}), encoding="utf-8")
    dock = NetTraceDock(main_window)
    dock.set_root_path(root)
    dock._connection.board = None
    launched = []
    monkeypatch.setattr(net_trace_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    caplog.set_level(10)

    dock._on_select_copper()

    assert launched == []
    assert "Connect to KiCad first" in caplog.text


def test_dock_finish_logs_the_report(main_window, tmp_path, caplog):
    dock = _dock_with_two_bridges(main_window, tmp_path)
    caplog.set_level(10)
    dock._finish_select_copper(_report(identity="bridge_one"))

    assert "selected 2 of 2" in caplog.text
    assert "previous board selection was replaced" in caplog.text
