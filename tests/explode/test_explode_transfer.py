# tests/explode/test_explode_transfer.py
"""Cells for the Р3 ownership TRANSFER (plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` Р3-3/Р3-4). DeepSeek, 2026-10-05.

The property: a transferred piece leaves its ``net_traces`` record IN THE FILE
(the ``uuid`` survives), every registry key of the record AND of its
``tree_instances`` copies is RELEASED, an emptied record is KEPT and named, and a
copy that is not 1:1 with the template refuses the edit instead of moving the
wrong piece.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.config_writer import _read_data, write_config_file
from kicadstamp.explode_transfer import NetTraceTransfer, apply_transfers
from kicadstamp.net_trace_planner import net_trace_registry_key
from kicadstamp.registry import (
    RegistryEntry,
    TrackRegistryEntry,
    load_registry,
    load_track_registry,
    save_registry,
    save_track_registry,
)
from kicadstamp.utils.paths import registry_paths_for_config


def _vias(n):
    return [{"net": "N", "offset_along_mm": float(i), "offset_across_mm": 0.0}
            for i in range(n)]


def _tracks(n):
    return [{"net": "N", "start_along_mm": float(i), "start_across_mm": 0.0,
             "end_along_mm": float(i) + 1.0, "end_across_mm": 0.0,
             "width_mm": 0.2} for i in range(n)]


@pytest.fixture(autouse=True)
def _no_format3_stamp(monkeypatch):
    """The transfer writes the config through `upsert_list_entry`, and a format-3
    write wants the project graph root — absent in a unit test, so the stamp is
    disabled here exactly as the project's own unit tests do."""
    import kicadstamp.config_working_set as ws
    monkeypatch.setattr(ws, "is_stamp_disabled", lambda: True)


def _setup(tmp_path, *, vias=2, tracks=3, copies=1, copy_tracks=None):
    cfg_path = tmp_path / "config.sexp"
    write_config_file(cfg_path, {"net_traces": [
        {"name": "rec", "uuid": "uuid-t", "net": "N",
         "anchor_role": "U1", "anchor_cluster": "C",
         "vias": _vias(vias), "tracks": _tracks(tracks)}]}, stamp=False)
    records = [SimpleNamespace(name="rec", net="N", uuid="uuid-t",
                               vias=_vias(vias), tracks=_tracks(tracks))]
    for k in range(1, copies):
        records.append(SimpleNamespace(
            name="rec", net="N", uuid=f"uuid-c{k}", vias=_vias(vias),
            tracks=_tracks(copy_tracks if copy_tracks is not None else tracks)))
    cfg = SimpleNamespace(net_traces=records, registry_path=None,
                          track_registry_path=None)
    return cfg_path, cfg, records


def _seed_registry(cfg_path, records):
    via_path, trk_path = registry_paths_for_config(str(cfg_path), None, None)
    vias, trks = {}, {}
    for nt in records:
        for i in range(len(nt.vias)):
            vias[net_trace_registry_key(nt, i)] = RegistryEntry(
                uuid=f"v{i}", x_mm=0.0, y_mm=0.0, net="N", drill_mm=0.3,
                diameter_mm=0.6)
        for i in range(len(nt.tracks)):
            trks[net_trace_registry_key(nt, i)] = TrackRegistryEntry(
                uuid=f"t{i}", start_x_mm=0.0, start_y_mm=0.0, end_x_mm=1.0,
                end_y_mm=0.0, width_mm=0.2, net="N", layer="F.Cu")
    save_registry(via_path, vias)
    save_track_registry(trk_path, trks)
    return via_path, trk_path


def _entry(cfg_path):
    return _read_data(cfg_path)["net_traces"][0]


def test_the_piece_leaves_the_file_record_and_the_uuid_survives(tmp_path):
    """Р3-4: a middle piece goes from the record's ``tracks``; the record's uuid —
    the format-3 identity a name-based replace would lose — is untouched."""
    cfg_path, cfg, _ = _setup(tmp_path)
    lines = apply_transfers(cfg_path, cfg, [NetTraceTransfer("rec", "track", 1)])
    entry = _entry(cfg_path)
    assert entry["uuid"] == "uuid-t"
    assert len(entry["tracks"]) == 2
    # pieces 0 and 2 stayed; the middle one (end_along_mm == 2.0) is gone
    assert [t["end_along_mm"] for t in entry["tracks"]] == [1.0, 3.0]
    assert len(entry["vias"]) == 2            # the other kind is untouched
    assert lines == []


def test_every_key_of_the_record_and_its_copies_is_released(tmp_path):
    """Р3-4: the registry keys are RELEASED (template + tree_instances copies) —
    the redraws reclaim copper by geometry, so a released key is what makes the
    reset-without-Save path self-heal."""
    cfg_path, cfg, records = _setup(tmp_path, copies=2)
    via_path, trk_path = _seed_registry(cfg_path, records)
    assert len(load_registry(via_path)) == 4        # 2 vias x 2 records
    assert len(load_track_registry(trk_path)) == 6  # 3 tracks x 2 records

    lines = apply_transfers(cfg_path, cfg, [NetTraceTransfer("rec", "via", 0)])

    assert load_registry(via_path) == {}
    assert load_track_registry(trk_path) == {}
    assert any("tree_instances" in line for line in lines)


def test_an_emptied_record_is_kept_and_named(tmp_path):
    """Р3-4: a record whose LAST piece moved is NOT deleted silently — a yellow
    line says so and the record stays in the file."""
    cfg_path, cfg, records = _setup(tmp_path, vias=1, tracks=1)
    lines = apply_transfers(cfg_path, cfg, [
        NetTraceTransfer("rec", "via", 0),
        NetTraceTransfer("rec", "track", 0)])
    entry = _entry(cfg_path)
    assert not entry.get("vias") and not entry.get("tracks")
    assert any("empty" in line for line in lines)


def test_a_copy_not_one_to_one_refuses_the_edit(tmp_path):
    """Р3-3 п.3: editing the FILE record by index is only correct while every copy
    is 1:1 — a copy that is not refuses the record (nothing is written)."""
    cfg_path, cfg, records = _setup(tmp_path, copies=2, copy_tracks=2)
    before = _entry(cfg_path)
    lines = apply_transfers(cfg_path, cfg, [NetTraceTransfer("rec", "track", 1)])
    assert _entry(cfg_path) == before                 # nothing written
    assert any("1:1" in line for line in lines)
