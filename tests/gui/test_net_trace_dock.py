# tests/gui/test_net_trace_dock.py
"""
NetTraceDock tests — headless Qt + mock adapter, plan
techdocs/handoff/deepseek/plan_2026_08_21_net_trace_dock.md §2:
  - the net picker collects unique net names from the WHOLE board's copper
    (tracks+vias), NOT the selection — the direct regression guard against the
    old ExtractDock "Origin: Via net" selection-scoped combo (review finding 5);
  - Extract writes a correct record under net_traces:; errors (no copper / no
    anchor) surface as an explicit Log message and leave the file untouched;
  - clicking a tree leaf fills the form from the saved record (load_entry);
  - Save edits anchor/retired/skip and NEVER touches tracks:/vias:;
  - Redraw runs apply --only=<net> (the ApplyPipeline's `only` is verified).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

from kicadstamp.domain.geometry import Vector2
from kicadstamp.domain.geometry import BoardLayer

import gui.docks.net_trace as net_trace_mod
from gui.docks.net_trace import NetTraceDock
from kicadstamp.utils.units import MM


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _read_yaml(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, data if data is not None else {})
    dock = NetTraceDock(main_window)
    dock.set_root_path(target_file)
    return dock, target_file


def _make_fp(ref, role, x_mm, y_mm):
    fp = MagicMock()
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    return fp


def _make_track(sx, sy, ex, ey, net, layer=BoardLayer.BL_F_Cu):
    t = MagicMock()
    t.net_name = net
    t.start = Vector2.from_xy(int(sx * MM), int(sy * MM))
    t.end = Vector2.from_xy(int(ex * MM), int(ey * MM))
    t.width_mm = 0.15
    t.layer = layer
    return t


def _make_via(x, y, net):
    v = MagicMock()
    v.net_name = net
    v.position = Vector2.from_xy(int(x * MM), int(y * MM))
    v.drill_mm = 0.3
    v.diameter_mm = 0.6
    return v


def _connect_board(dock, fps, tracks, vias, roles=None):
    """Fills the dock's connection with a mock adapter serving the given
    footprints/tracks/vias (roles via fp._role)."""
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = (
        roles if roles is not None
        else (lambda fp, name: getattr(fp, "_role", None) if name == "Role" else None))
    adapter.get_selected_items.return_value = []
    adapter.get_tracks.return_value = tracks
    adapter.get_vias.return_value = vias
    adapter.get_pad_by_number.side_effect = lambda fp, num: None
    dock._connection.board = SimpleNamespace(adapter=adapter)
    return adapter


# ── Net picker (whole board copper, NOT the selection) ───────────────────────

def test_net_picker_collects_unique_nets_from_whole_board_copper(main_window):
    dock = NetTraceDock(main_window)
    adapter = MagicMock()
    adapter.get_tracks.return_value = [
        _make_track(0, 0, 1, 1, "/Channel_0/DAC_DB2"),
        _make_track(0, 0, 1, 1, "/Channel_0/DAC_DB2"),  # duplicate net
        _make_track(0, 0, 1, 1, "GND"),
    ]
    adapter.get_vias.return_value = [
        _make_via(0, 0, "/Channel_0/DAC_DB3"),
        _make_via(0, 0, "GND"),  # already seen via tracks
    ]
    board = SimpleNamespace(adapter=adapter)

    dock.refresh_known_nets(board)

    items = [dock.net_edit.itemText(i) for i in range(dock.net_edit.count())]
    assert items == ["/Channel_0/DAC_DB2", "/Channel_0/DAC_DB3", "GND"]


def test_net_picker_ignores_pad_only_nets_without_copper(main_window):
    """get_all_nets() would include pad-only nets — the picker must NOT (there
    is nothing to capture for them)."""
    dock = NetTraceDock(main_window)
    adapter = MagicMock()
    adapter.get_tracks.return_value = []
    adapter.get_vias.return_value = []
    board = SimpleNamespace(adapter=adapter)
    dock.refresh_known_nets(board)
    assert dock.net_edit.count() == 0


def test_net_picker_empty_when_board_disconnected(main_window):
    dock = NetTraceDock(main_window)
    dock.refresh_known_nets(None)
    assert dock.net_edit.count() == 0


# ── Extract ──────────────────────────────────────────────────────────────────

def test_extract_writes_record_under_net_traces(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    fpga = _make_fp("U1", "FPGA", 50, 50)
    _connect_board(
        dock, [fpga],
        [_make_track(53, 54, 55, 56, "DAC_DB0")],
        [_make_via(57, 58, "DAC_DB0")])
    dock.net_edit.setCurrentText("DAC_DB0")
    dock.anchor_widget.load(mode="anchor", role="FPGA")

    result = dock._do_extract()

    assert "error" not in result
    data = _read_yaml(target)
    assert len(data["net_traces"]) == 1
    entry = data["net_traces"][0]
    assert entry["net"] == "DAC_DB0"
    assert entry["anchor_role"] == "FPGA"
    assert len(entry["tracks"]) == 1
    assert len(entry["vias"]) == 1
    assert any("Wrote net trace" in r.message for r in caplog.records)


def test_extract_no_copper_reports_error_and_leaves_file_untouched(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    _connect_board(dock, [_make_fp("U1", "FPGA", 50, 50)], [], [])
    dock.net_edit.setCurrentText("DAC_DB0")
    dock.anchor_widget.load(mode="anchor", role="FPGA")

    result = dock._do_extract()

    assert "error" in result
    assert any("no copper" in r.message for r in caplog.records)
    assert "net_traces" not in _read_yaml(target)  # file untouched


def test_extract_missing_anchor_reports_error(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    _connect_board(dock, [], [_make_track(1, 1, 2, 2, "DAC_DB0")], [])
    dock.net_edit.setCurrentText("DAC_DB0")
    dock.anchor_widget.load(mode="anchor", role="FPGA")

    result = dock._do_extract()

    assert "error" in result
    assert any("not found" in r.message for r in caplog.records)
    assert "net_traces" not in _read_yaml(target)


# ── Load entry (tree leaf click) ─────────────────────────────────────────────

def test_load_entry_fills_form(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.load_entry({
        "net": "/Channel_0/DAC_DB2",
        "anchor_role": "FPGA", "anchor_sheet": "Channel_0",
        "anchor_cluster": "FPGA", "anchor_pad": "42",
        "retired": True, "skip": False,
        "tracks": [{"start_along_mm": 1.0}],  # geometry not shown, ignored
    })
    assert dock.net_edit.currentText() == "/Channel_0/DAC_DB2"
    assert dock.anchor_widget.anchor_role_edit.currentText() == "FPGA"
    assert dock.anchor_widget.anchor_sheet_edit.currentText() == "Channel_0"
    assert dock.anchor_widget.anchor_cluster_edit.currentText() == "FPGA"
    assert dock.anchor_widget.anchor_pad_edit.text() == "42"
    assert dock.retired_checkbox.isChecked()
    assert not dock.skip_checkbox.isChecked()


# ── Save ─────────────────────────────────────────────────────────────────────

def test_save_updates_anchor_retired_and_preserves_geometry(main_window, tmp_path, caplog):
    """Save edits the controllable fields and MUST NOT drop the saved record's
    tracks:/vias: (machine-written geometry)."""
    dock, target = _make_dock(main_window, tmp_path, data={
        "net_traces": [{
            "net": "/Channel_0/DAC_DB2", "anchor_role": "FPGA",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "net": "/Channel_0/DAC_DB2", "layer": "F.Cu"}],
            "vias": [],
        }]
    })
    dock.load_entry({
        "net": "/Channel_0/DAC_DB2", "anchor_role": "FPGA",
        "tracks": [{"start_along_mm": 1.0}], "vias": [],
    })
    dock.anchor_widget.load(mode="anchor", role="FPGA", cluster="FPGA_PERIPH")
    dock.retired_checkbox.setChecked(True)

    dock._on_save()

    data = _read_yaml(target)
    assert len(data["net_traces"]) == 1
    entry = data["net_traces"][0]
    assert entry["anchor_cluster"] == "FPGA_PERIPH"
    assert entry["retired"] is True
    # geometry preserved from the saved record
    assert len(entry["tracks"]) == 1
    assert entry["tracks"][0]["net"] == "/Channel_0/DAC_DB2"
    assert entry["tracks"][0]["layer"] == "F.Cu"
    assert any("Overwrote" in r.message for r in caplog.records)


def test_save_requires_anchor_role(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("DAC_DB0")
    dock.anchor_widget.clear()
    dock._on_save()
    assert any("Anchor" in r.message for r in caplog.records)
    assert "net_traces" not in _read_yaml(target)


# ── Redraw ───────────────────────────────────────────────────────────────────

def test_redraw_runs_apply_with_only_net(main_window, tmp_path, monkeypatch):
    dock, target = _make_dock(main_window, tmp_path, data={
        "net_traces": [{
            "net": "/Channel_0/DAC_DB2", "anchor_role": "FPGA",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "net": "/Channel_0/DAC_DB2", "layer": "F.Cu"}],
            "vias": [],
        }]
    })
    dock.load_entry({
        "net": "/Channel_0/DAC_DB2", "anchor_role": "FPGA",
        "tracks": [{"start_along_mm": 1.0}], "vias": [],
    })

    captured = {}

    class _FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return None

    # Patch via the MODULE object (not a dotted string) so the fake is
    # guaranteed to be what _run_redraw resolves — a missed patch would let
    # the REAL ApplyPipeline connect to the live KiCad and mutate the board,
    # which a headless test must never do.
    monkeypatch.setattr(net_trace_mod, "ApplyPipeline", _FakePipeline)
    result = dock._do_redraw()

    assert "error" not in result
    assert captured["only"] == ["/Channel_0/DAC_DB2"]
    # the merged record carries the saved geometry (not lost in Redraw) —
    # the pipeline receives it via preloaded_cfg (ApplyPipeline's kwarg name).
    nt = captured["preloaded_cfg"].net_traces[0]
    assert len(nt.tracks) == 1


def test_extract_preserves_existing_retired_flag(main_window, tmp_path, caplog):
    """Review fix 2026-08-21: a re-Extract refreshes the GEOMETRY of an
    already-saved record but must NOT silently clear its hand-set retired:
    (same net, record already marked retired: true)."""
    dock, target = _make_dock(main_window, tmp_path, data={
        "net_traces": [{"net": "DAC_DB0", "anchor_role": "FPGA", "retired": True}]
    })
    _connect_board(dock, [_make_fp("U1", "FPGA", 50, 50)],
                   [_make_track(53, 54, 55, 56, "DAC_DB0")], [])
    dock.net_edit.setCurrentText("DAC_DB0")
    dock.anchor_widget.load(mode="anchor", role="FPGA")

    result = dock._do_extract()

    assert "error" not in result
    entry = _read_yaml(target)["net_traces"][0]
    assert entry["retired"] is True  # survived the re-extract
    assert len(entry["tracks"]) == 1  # geometry refreshed
