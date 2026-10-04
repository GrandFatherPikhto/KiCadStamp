# tests/gui/test_copper_identify.py
"""
Э3 tests for "Whose copper is this?" (plan_2026_09_12_select_copper_by_record;
design §12.2): the board SELECTION -> the `net_traces:` records that own it.

Covered:
  * tier 1 by registry uuid (exact, no geometry);
  * tier 2 by the shared predicates when the registries do not know the copper;
  * "no record" is a useful answer (the copper is free to capture);
  * copper owned by a non-net_traces mechanism is counted separately;
  * a registry identity with no config record is surfaced, not invented;
  * an unresolvable anchor is an honest reason, never an exception;
  * the worker only READS the selection;
  * the TreesDock finish path logs the answer and highlights an identified
    record's tree NODE (never the copper).
"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.config import Config, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.board import BoardLayer, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.net_trace_planner import (net_trace_anchor_id,
                                          net_trace_registry_key)
from kicadstamp.registry import make_registry_key
from kicadstamp.utils.units import MM
from tests.fakes.format3 import (det_uuid, format3,  # noqa: F401 — pytest fixture
                                 identity_value)

import gui.docks.copper_select as copper_select_mod
import gui.docks.trees_dock as trees_dock_mod
from gui.docks.copper_select import (IdentifyResult, identify_copper_report_lines,
                                     identify_selected_copper,
                                     run_identify_copper_worker)
from gui.docks.trees_dock import TreesDock


class _Reg:
    def __init__(self, entries=None):
        self.entries = entries or {}


def _vp(x_mm, y_mm):
    return Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))


def _live_track(uuid):
    return Track(uuid=uuid, start=_vp(53, 54), end=_vp(55, 56), net_name="N",
                 width_mm=0.2, layer=BoardLayer.BL_F_Cu)


def _live_via(uuid):
    return Via(uuid=uuid, position=_vp(57, 58), net_name="N",
               drill_mm=0.3, diameter_mm=0.6)


def _loose_track(uuid="loose"):
    """A track that matches NO planned net-trace geometry."""
    return Track(uuid=uuid, start=_vp(10, 10), end=_vp(11, 11), net_name="X",
                 width_mm=0.2, layer=BoardLayer.BL_F_Cu)


def _record(name="bridge", net="N", anchor_role="FPGA"):
    # У3.5 (U1): under format 3 the registry-key builder REFUSES a record with
    # no uuid (Р-У5.7) — mint one in the product migration namespace, keyed by
    # the record's identity, so tier 1/2 build the same key on both sides.
    return NetTrace(
        uuid=det_uuid(f"net_traces:{name}"),
        net=net, name=name, anchor_role=anchor_role, anchor_pad="42",
        tracks=[TemplateTrack(start_along_mm=1, start_across_mm=2,
                              end_along_mm=3, end_across_mm=4, width_mm=0.2,
                              net=net, layer="F.Cu")],
        vias=[TemplateVia(offset_along_mm=5, offset_across_mm=6, net=net,
                          drill_mm=0.3, diameter_mm=0.6)],
    )


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


def _key(nt, index=0):
    return make_registry_key(net_trace_anchor_id(nt), nt.name or nt.net, None, index)


def _entry(uuid, net="N"):
    return SimpleNamespace(uuid=uuid, net=net)


# ── pure identify: tier 1 ──────────────────────────────────────────────────

def test_tier1_identifies_by_registry_uuid_without_geometry():
    nt = _record()
    selected = [_live_track("sel-trk"), _live_via("sel-via")]
    via_reg = _Reg({_key(nt): _entry("sel-via")})
    track_reg = _Reg({_key(nt): _entry("sel-trk")})
    # No live board at all — tier 1 must not need one.
    adapter = _fake_adapter()

    result = identify_selected_copper(
        adapter, Config(net_traces=[nt]), selected,
        via_registry=via_reg, track_registry=track_reg)

    assert result.total == 2
    assert result.identified == {"bridge": 2}
    assert result.unidentified == 0


def test_registry_identity_without_config_record_is_surfaced():
    nt = _record(name="ghost")          # used only to build the key
    selected = [_live_track("sel-trk")]
    track_reg = _Reg({_key(nt): _entry("sel-trk")})
    adapter = _fake_adapter()

    result = identify_selected_copper(
        adapter, Config(net_traces=[]), selected,
        via_registry=_Reg(), track_registry=track_reg)

    # У3.5: with NO config record to translate it, the identity the product can
    # surface is whatever the key carries — the NAME in format 2, the record's
    # UUID under the gate (there is no name to resolve). identity_value spells
    # the one that applies, so the same cell passes under both formats.
    expected = identity_value("ghost", det_uuid("net_traces:ghost"))
    assert result.identified == {expected: 1}
    assert result.unknown_records == [expected]


# ── pure identify: tier 2 ──────────────────────────────────────────────────

def test_tier2_identifies_by_geometry_when_registry_empty():
    nt = _record()
    track = _live_track("hand-trk")
    via = _live_via("hand-via")
    adapter = _fake_adapter(live_tracks=[track], live_vias=[via],
                            selected=[track, via])

    result = identify_selected_copper(
        adapter, Config(net_traces=[nt]), [track, via],
        via_registry=_Reg(), track_registry=_Reg())

    assert result.identified == {"bridge": 2}
    assert result.unidentified == 0


def test_unidentified_copper_is_a_useful_answer():
    nt = _record()                       # its copper is NOT on the board
    loose = _loose_track()
    adapter = _fake_adapter(live_tracks=[loose], selected=[loose])

    result = identify_selected_copper(
        adapter, Config(net_traces=[nt]), [loose],
        via_registry=_Reg(), track_registry=_Reg())

    assert result.identified == {}
    assert result.unidentified == 1


def test_copper_owned_by_another_mechanism_is_counted_separately():
    loose = _loose_track()
    track_reg = _Reg({"anchor:U1|cell|None|0": _entry("loose")})
    adapter = _fake_adapter(live_tracks=[loose], selected=[loose])

    result = identify_selected_copper(
        adapter, Config(net_traces=[]), [loose],
        via_registry=_Reg(), track_registry=track_reg)

    assert result.owned_elsewhere == 1
    assert result.unidentified == 0
    assert result.identified == {}


def test_unresolvable_anchor_is_a_reason_not_an_exception():
    nt = _record()
    loose = _loose_track()
    adapter = _fake_adapter(live_tracks=[loose], selected=[loose])
    adapter.get_footprints.return_value = []
    adapter.get_field_value.side_effect = lambda f, name: None

    result = identify_selected_copper(
        adapter, Config(net_traces=[nt]), [loose],
        via_registry=_Reg(), track_registry=_Reg())

    assert result.unidentified == 1
    assert result.reasons              # one honest reason for the bad anchor


# ── report lines ───────────────────────────────────────────────────────────

def test_report_lines_nothing_selected():
    lines = identify_copper_report_lines(IdentifyResult(total=0))
    assert "Nothing is selected" in "\n".join(lines)


def test_report_lines_name_records_and_free_copper():
    result = IdentifyResult(total=3, identified={"bridge_one": 2},
                            unidentified=1, owned_elsewhere=0)
    lines = "\n".join(identify_copper_report_lines(result))
    assert "bridge_one: 2" in lines
    assert "belong to no net_traces record" in lines
    assert "can be captured" in lines


def test_report_lines_mark_a_tree_node_and_owned_elsewhere():
    result = IdentifyResult(total=2, identified={"bridge_one": 1},
                            unidentified=0, owned_elsewhere=1,
                            unknown_records=["ghost"],
                            reasons=["record 'bad': anchor did not resolve"])
    lines = "\n".join(identify_copper_report_lines(result, {"bridge_one"}))
    assert "(tree node)" in lines
    assert "another mechanism" in lines
    assert "ghost" in lines
    assert "anchor did not resolve" in lines


# ── the worker ─────────────────────────────────────────────────────────────

def test_worker_only_reads_the_selection(tmp_path, monkeypatch):
    nt = _record()
    track = _live_track("hand-trk")
    adapter = _fake_adapter(live_tracks=[track], selected=[track])
    # The worker builds its OWN adapter through the FACTORY — the `_live_adapter`
    # helper is gone (both of its callers leaked their socket), and the lazy import
    # inside the worker means the stand-in goes on the factory's own attribute.
    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        lambda *args, **kwargs: adapter)
    config_path = tmp_path / "root.sexp"
    config_path.write_text("", encoding="utf-8")

    result = run_identify_copper_worker(
        {"cfg": Config(net_traces=[nt]), "config_path": str(config_path),
         "sheet_names": {}})

    assert result.identified == {"bridge": 1}
    adapter.select_items.assert_not_called()   # read-only


# ── TreesDock finish + node highlight ───────────────────────────────────────

def _dock(main_window, tmp_path):
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
    }, format_number=2), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def test_finish_logs_and_highlights_the_identified_node(main_window, tmp_path, caplog):
    dock, _root_path = _dock(main_window, tmp_path)
    caplog.set_level(10)

    dock._finish_identify_selected_copper(
        IdentifyResult(total=1, identified={"bridge_one": 1}))

    assert "bridge_one: 1" in caplog.text
    assert "(tree node)" in caplog.text
    # the NODE (not the copper) is selected
    item = dock._node_items["bridge_one"]
    assert dock._current_tree_widget().currentItem() is item


def test_finish_with_empty_selection_only_logs(main_window, tmp_path, caplog):
    dock, _root_path = _dock(main_window, tmp_path)
    caplog.set_level(10)

    dock._finish_identify_selected_copper(IdentifyResult(total=0))

    assert "Nothing is selected" in caplog.text


def test_action_starts_the_worker_with_explicit_paths(main_window, tmp_path, monkeypatch):
    dock, root_path = _dock(main_window, tmp_path)
    launched = []

    def fake_start(connection, widgets, fn, on_success, on_error, payload, **kwargs):
        launched.append(payload)
        return object()

    monkeypatch.setattr(trees_dock_mod, "start_long_op", fake_start)
    dock._on_identify_selected_copper()

    assert launched[0]["config_path"] == str(root_path)
    assert launched[0]["cfg"] is dock._cfg


# ── Р-У5.8 (У5.5): the record NAME is shown, not the uuid ────────────────────

def test_format3_tier1_shows_the_record_name_not_the_uuid(format3):  # noqa: F811
    """Р-У5.8, first key-reading point: under the gate the key's `net:` anchor_id
    carries the record's UUID; the "identified" answer must name the RECORD."""
    nt = _record(name="bridge")
    nt.uuid = "nt-uuid"
    selected = [_live_track("sel-trk")]
    track_reg = _Reg({net_trace_registry_key(nt, 0): _entry("sel-trk")})

    result = identify_selected_copper(
        _fake_adapter(), Config(net_traces=[nt]), selected,
        via_registry=_Reg(), track_registry=track_reg)

    assert result.identified == {"bridge": 1}
    assert "nt-uuid" not in result.identified
    assert result.unknown_records == []


def test_format3_geometry_identity_shows_the_record_name(format3):  # noqa: F811
    """Р-У5.8, SECOND key-reading point (the geometry tier reads the key's
    template_name): under the gate that part is the record's UUID too — it must
    still be presented as the NAME."""
    nt = _record(name="bridge")
    nt.uuid = "nt-uuid"
    track = _live_track("hand-trk")
    via = _live_via("hand-via")
    adapter = _fake_adapter(live_tracks=[track], live_vias=[via],
                            selected=[track, via])

    result = identify_selected_copper(
        adapter, Config(net_traces=[nt]), [track, via],
        via_registry=_Reg(), track_registry=_Reg())

    assert result.identified == {"bridge": 2}
    assert "nt-uuid" not in result.identified


# ── Х3 (V5, У5.5): the read-only registries read the EXPLICIT path files ──────

def test_readonly_registries_read_the_explicit_paths(tmp_path):
    """Х3 (V5, У5.5): with explicit ``registry_path:``/``track_registry_path:``,
    the dock's read-only registries are THOSE files — the SAME explicit-vs-default
    decision the on-disk lift and the apply use (У5.4 Н3). A private default-path
    guess would read files the apply never touches (and highlight copper the
    apply does not manage).

    Both files hold DIFFERENT keys, so a default-path reader answers with the
    WRONG entries, not merely with none."""
    from gui.docks.copper_select import _readonly_registries
    from kicadstamp.registry import (RegistryEntry, TrackRegistryEntry,
                                     save_registry, save_track_registry)

    config = tmp_path / "root.sexp"
    config.write_text(dict_to_sexp({
        "registry_path": "alt/via.registry.json",
        "track_registry_path": "alt/trk.registry.json"}, format_number=2), encoding="utf-8")

    explicit_key = "net:explicit|explicit|__spoke__|0"
    default_key = "net:default|default|__spoke__|0"
    save_registry(str(tmp_path / "alt" / "via.registry.json"),
                  {explicit_key: RegistryEntry(
                      uuid="explicit-via", x_mm=1.0, y_mm=2.0, net="N",
                      drill_mm=0.3, diameter_mm=0.6)})
    save_track_registry(str(tmp_path / "alt" / "trk.registry.json"),
                        {explicit_key: TrackRegistryEntry(
                            uuid="explicit-trk", start_x_mm=0.0, start_y_mm=0.0,
                            end_x_mm=1.0, end_y_mm=0.0, width_mm=0.2, net="N",
                            layer="F.Cu")})
    save_registry(str(tmp_path / "registry" / "root.registry.json"),
                  {default_key: RegistryEntry(
                      uuid="default-via", x_mm=1.0, y_mm=2.0, net="N",
                      drill_mm=0.3, diameter_mm=0.6)})
    save_track_registry(str(tmp_path / "tracks" / "root.tracks.registry.json"),
                        {default_key: TrackRegistryEntry(
                            uuid="default-trk", start_x_mm=0.0, start_y_mm=0.0,
                            end_x_mm=1.0, end_y_mm=0.0, width_mm=0.2, net="N",
                            layer="F.Cu")})

    via_reg, track_reg = _readonly_registries(MagicMock(), str(config))

    assert set(via_reg.entries) == {explicit_key}
    assert set(track_reg.entries) == {explicit_key}
    assert default_key not in via_reg.entries
    assert default_key not in track_reg.entries


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 (class (в)): the format-3 writer resolves a reference's UUID against
    the ACTIVE GRAPH ROOT. These cells write a self-contained config; the root is
    a path that does NOT exist, so the stamp indexes THIS write's own records
    (config/format3._build_format3_index) — the format-3 product path, no
    on-disk graph walked. Under format 2 (< 3) the root is never consulted."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)
