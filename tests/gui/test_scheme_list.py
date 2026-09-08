# tests/gui/test_scheme_list.py
"""Scheme List Config-side GUI (plan_2026_09_05_scheme_list.md §5, P5;
design_2026_09_07_scheme_list_pivot.md — the record carries a `pivot` in the
region's CENTRE frame and has NO anchor component) — headless Qt + mock
adapter, following the tests/gui patterns of test_net_trace_dock.py /
test_phase3_wiring.py:
  - SchemeListFormWidget: a scheme_lists record loads READ-ONLY (the pivot /
    source_sheet readouts + geometry summary); nothing here ever applies to
    the board.
  - Reread: identical board -> "no differences"; a moved component -> the
    diff; explicit Apply rewrites the stored record in its owning file.
  - Storage helpers: scheme_list_to_dict round-trips through the loader; a
    write auto-creates scheme_lists.json + include: on first use and upserts
    by name afterwards; duplicate pre-checks fire before capture.
  - ConfigTreeDock: the scheme_lists section shows one leaf per record and a
    single click emits scheme_list_picked (-> DockHub opens the right page).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel,
                             QPushButton, QTabWidget)

from gui.docks.config_tree import ConfigTreeDock
from gui.docks.scheme_list import (
    BoundaryNetDialog,
    RecordSchemeListDialog,
    SchemeListDiffDialog,
    SchemeListFormWidget,
    all_sheet_paths,
    boundary_net_rows,
    choose_boundary_actions,
    default_scheme_list_path,
    live_record_centre_mm,
    live_sheet_paths,
    missing_record_refs,
    pivot_centre_frame_from_selection,
    read_scheme_list_records,
    record_refs_for,
    refs_on_sheet,
    reread_scope_refs,
    scheme_list_duplicate_problems,
    scheme_list_to_dict,
    sheet_paths_under,
    sheet_subtree_plan,
    snapshot_with_resolved_sheets,
    write_scheme_list_record,
)
from kicadstamp.config import load_config, load_scheme_list
from kicadstamp.config.models import SchemeListBoundaryNet
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.domain.board import Footprint, Pad, Track, Via
from kicadstamp.explore import Selected
from kicadstamp.link_trees import link_trees
from kicadstamp.domain.geometry import BoardLayer, Box2, Vector2
from kicadstamp.scheme_list_capture import SchemeListDiff, capture_scheme_list
from kicadstamp.utils.units import MM

F = BoardLayer.BL_F_Cu
IN1 = BoardLayer.BL_In1_Cu
_V5 = "/Channel_0/AMP/+5V"


def _mm_xy(x_mm, y_mm):
    return Vector2.from_xy_mm(x_mm, y_mm)


def _fp(ref, x_mm, y_mm, angle=0.0, layer=F):
    return Footprint(ref=ref, uuid=f"uuid-{ref}", position=_mm_xy(x_mm, y_mm),
                     angle_deg=angle, layer=layer)


def _stamp_sheet(adapter, sheet_uuid="sch-ch0", name="Channel_0") -> dict:
    """Give every footprint of `adapter` a resolved top-level sheet path
    (sheet_uuid -> name) and return the {uuid: name} map capture's source_sheet
    derivation needs (5a.2) — replaces the removed network-prefix hack."""
    for fp in adapter._fps:
        fp.sheet_path_uuids = (sheet_uuid, fp.uuid)
    return {sheet_uuid: name}


def _pad(fp_ref, x_mm, y_mm, net, number="1"):
    return Pad(number=number, net_name=net, position=_mm_xy(x_mm, y_mm),
               size=Vector2.from_xy_mm(1.0, 1.0))


def _track(x1, y1, x2, y2, net, layer=F, width=0.25):
    return Track(uuid=f"t-{x1}-{y1}-{x2}-{y2}", start=_mm_xy(x1, y1),
                 end=_mm_xy(x2, y2), net_name=net, width_mm=width, layer=layer)


def _via(x_mm, y_mm, net, drill=0.3, diam=0.6):
    return Via(uuid=f"v-{x_mm}-{y_mm}", position=_mm_xy(x_mm, y_mm),
               net_name=net, drill_mm=drill, diameter_mm=diam)


class FakeAdapter:
    """Mock board adapter (mirrors tests/test_scheme_list_capture.py's) — the
    capture/diff read through get_footprints/get_tracks/get_vias/
    get_footprint_pads/get_bounding_boxes only."""

    def __init__(self, footprints, tracks, vias, pads_by_ref):
        self._fps = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)
        self._pads = dict(pads_by_ref)

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            if isinstance(it, Footprint):
                half = int(2.0 * MM)
            elif isinstance(it, Pad):
                # Real pad boxes are the closure filter's ANCHOR set — without
                # them capture falls back to the both-ends rule and drops the
                # (perfectly valid) line copper (mirror of
                # tests/test_scheme_list_capture.py's adapter).
                half = int(0.5 * MM)
            elif isinstance(it, Via):
                half = max(int((it.diameter_mm / 2) * MM), int(0.25 * MM))
            else:
                out.append(None)
                continue
            p = it.position
            out.append(Box2(pos=Vector2.from_xy(p.x - half, p.y - half),
                            size=Vector2.from_xy(2 * half, 2 * half)))
        return out


def _line_board(c2_x_mm=24.0, angle_anchor=0.0):
    """R1(10,10) --F.Cu--> C1(20,10, 90 deg) --In1.Cu--> C2(c2_x,10), via at C1.
    No foreign component (no boundary nets). `angle_anchor` just tilts R1 (the
    test boards' tilt knob — there is no anchor component any more)."""
    r1 = _fp("R1", 10, 10, angle=angle_anchor)
    c1 = _fp("C1", 20, 10, angle=90.0)
    c2 = _fp("C2", c2_x_mm, 10)
    pads = {
        "R1": [_pad("R1", 10, 10, _V5)],
        "C1": [_pad("C1", 20, 10, _V5)],
        "C2": [_pad("C2", c2_x_mm, 10, _V5)],
    }
    t1 = _track(10, 10, 20, 10, _V5, layer=F)
    t2 = _track(20, 10, c2_x_mm, 10, _V5, layer=IN1)
    v1 = _via(20, 10, _V5)
    return FakeAdapter([r1, c1, c2], [t1, t2], [v1], pads)


def _record_dict(adapter, name="amp", c2_x_mm=24.0):
    """Capture R1/C1/C2 from a live `adapter` and serialise to the record dict
    (centre-frame offsets, pivot default (0,0))."""
    sheet_names = _stamp_sheet(adapter)
    record = capture_scheme_list(name, ["R1", "C1", "C2"],
                                 adapter=adapter, sheet_names=sheet_names)
    return scheme_list_to_dict(record)


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _record_file(tmp_path, record_dict, name="root.sexp") -> Path:
    root = tmp_path / name
    _write(root, {"scheme_lists": [record_dict]})
    return root


def _make_dock(main_window, root_path, record_dict):
    dock = SchemeListFormWidget(main_window)
    dock.set_root_path(root_path)
    dock.load_entry(record_dict)
    return dock


def _connect_board(dock, adapter) -> None:
    dock._connection.board = SimpleNamespace(adapter=adapter)


def _select(dock, *refs) -> None:
    """Emulate the polled live board selection (DockHub.set_board_selection,
    5c.4) — the Reread scope of a "By selection"-record is the CURRENT
    selection at click time, so the tests feed the recorded refs (or a
    changed set) as the selection before _do_reread/_do_reread_apply."""
    dock.set_board_selection([], [SimpleNamespace(ref=r) for r in refs])


def _selection_from(*footprints) -> list:
    """A live selection of RAW Footprints (each with .ref + .fp) — the shape
    the dock's set_board_selection stores and the B2 "Take from selection"
    pivot math reads (s.fp.position via selected_center_mm). Distinct from
    `_select`'s ref-only items, which carry no .fp and therefore no position
    (Commit B2 needs real positions)."""
    return [SimpleNamespace(ref=fp.ref, fp=fp) for fp in footprints]


# ── scheme_list_to_dict round-trip ─────────────────────────────────────────

def test_scheme_list_to_dict_round_trips_through_the_loader(main_window):
    adapter = _line_board()
    d = _record_dict(adapter)
    again = load_scheme_list(d)
    assert again.name == "amp"
    assert again.pivot == (0.0, 0.0)  # centre default; (0,0) is not written
    assert [c.ref for c in again.components] == ["R1", "C1", "C2"]
    assert len(again.tracks) == 2
    assert len(again.vias) == 1
    # optional fields survive (source_sheet from the local-net prefix)
    assert again.source_sheet == "Channel_0"


# ── Load entry (Config-tree leaf click) ────────────────────────────────────

def test_load_entry_fills_pivot_edits_and_source_sheet(main_window, tmp_path):
    adapter = _line_board(angle_anchor=45.0)
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)

    assert dock.name_label.text() == "Scheme List: amp"
    # pivot defaults to the centre (0,0) — the edits prefill 0.00/0.00
    assert dock.pivot_x_edit.text() == "0.00"
    assert dock.pivot_y_edit.text() == "0.00"
    assert dock.source_sheet_label.text() == "Channel_0"
    assert "3 components" in dock.geometry_label.text()
    assert "2 tracks" in dock.geometry_label.text()
    assert "1 vias" in dock.geometry_label.text()


def test_load_entry_prefills_nondefault_pivot_edits(main_window, tmp_path):
    adapter = _line_board(angle_anchor=90.0)
    d = _record_dict(adapter)
    d["pivot"] = [1.5, -2.25]  # a user-chosen pivot in the centre frame
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.pivot_x_edit.text() == "1.50"
    assert dock.pivot_y_edit.text() == "-2.25"


# ── Pivot editing (Commit B1: x/y edits + "Centre" + "Apply") ──────────────

def test_pivot_centre_button_writes_0_0_into_edits(main_window, tmp_path):
    adapter = _line_board(angle_anchor=90.0)
    d = _record_dict(adapter)
    d["pivot"] = [1.5, -2.25]
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.pivot_x_edit.text() == "1.50"

    dock.pivot_centre_button.click()

    assert dock.pivot_x_edit.text() == "0.00"
    assert dock.pivot_y_edit.text() == "0.00"


def test_pivot_apply_writes_nondefault_pivot_into_record_file(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)  # no pivot key yet -> default (0,0)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    emitted = []
    dock.saved.connect(lambda: emitted.append(True))

    dock.pivot_x_edit.setText("3.5")
    dock.pivot_y_edit.setText("-1.25")
    dock.pivot_apply_button.click()

    assert emitted == [True]
    data = _load(root)
    entry = data["scheme_lists"][0]
    assert entry["pivot"] == [3.5, -1.25]
    assert load_scheme_list(entry).pivot == (3.5, -1.25)


def test_pivot_apply_centre_leaves_record_without_pivot_key(main_window, tmp_path):
    """(0,0) is the centre default — scheme_list_to_dict omits it, so applying
    a centre pivot writes a record WITHOUT the pivot key (and removing a
    previously-stored non-default pivot drops the key)."""
    adapter = _line_board()
    d = _record_dict(adapter)
    d["pivot"] = [1.5, -2.25]
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)

    dock.pivot_centre_button.click()
    dock.pivot_apply_button.click()

    data = _load(root)
    entry = data["scheme_lists"][0]
    assert "pivot" not in entry
    assert load_scheme_list(entry).pivot == (0.0, 0.0)


def test_pivot_apply_invalid_number_is_reported_without_write(main_window, tmp_path, caplog):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    before = root.read_text(encoding="utf-8")

    dock.pivot_x_edit.setText("abc")  # not a number
    dock.pivot_y_edit.setText("1")
    dock.pivot_apply_button.click()

    assert any("must be numbers" in r.message for r in caplog.records)
    assert root.read_text(encoding="utf-8") == before  # file untouched


def test_pivot_apply_without_loaded_record_is_reported_no_crash(main_window, tmp_path, caplog):
    root = _record_file(tmp_path, _record_dict(_line_board()))
    dock = SchemeListFormWidget(main_window)
    dock.set_root_path(root)  # nothing loaded -> _entry empty, _path None

    dock.pivot_x_edit.setText("1")
    dock.pivot_y_edit.setText("1")
    dock.pivot_apply_button.click()  # must not crash

    assert any("Load a Scheme List record first." in r.message for r in caplog.records)


# ── Pivot "Take from selection" (Commit B2) ─────────────────────────────────
# Pure helpers first (no dock, no Qt), then the button's GUI wiring. The
# record R1/C1/C2 is captured from _line_board: R1(10,10), C1(20,10),
# C2(24,10) -> the region's centre (midpoint of extents) is (17,10). NOTE:
# selected_center_mm is the MEAN of the SELECTED positions, not the midpoint
# of extents — selecting R1+C1+C2 has mean x = 18, NOT the region centre 17;
# the "(0,0) when the selected centre == region centre" case must select
# R1+C2 (mean x = (10+24)/2 = 17).

def _fps_by_ref(adapter) -> dict:
    """The adapter's live footprints keyed by ref (B2 helper)."""
    return {fp.ref: fp for fp in adapter.get_footprints()}


def test_live_record_centre_mm_is_midpoint_of_present_refs(main_window):
    adapter = _line_board()
    snap = _fp_snapshot(adapter)  # the BoardConnection.snapshot-shaped cache
    centre = live_record_centre_mm(["R1", "C1", "C2"], snap)
    assert centre is not None
    assert centre[0] == pytest.approx(17.0)  # (10 + 24) / 2
    assert centre[1] == pytest.approx(10.0)


def test_live_record_centre_mm_none_when_no_recorded_ref_present(main_window):
    adapter = _line_board()
    adapter._fps = []  # the live board has none of the recorded refs (edge 1)
    assert live_record_centre_mm(["R1", "C1", "C2"],
                                 _fp_snapshot(adapter)) is None
    # partial presence still yields a centre (over the PRESENT refs)
    adapter._fps = [_fp("R1", 10, 10), _fp("C1", 20, 10)]
    centre = live_record_centre_mm(["R1", "C1", "C2"],
                                   _fp_snapshot(adapter))
    assert centre is not None
    assert centre[0] == pytest.approx(15.0)


def test_pivot_from_selection_centre_point_gives_0_0(main_window):
    """Selected centre == the region centre -> the pivot is the (0,0) centre
    default. Select R1+C2 (mean x = 17, exactly the region centre) — NOT
    R1+C1+C2 whose mean x = 18 differs from the midpoint 17."""
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    sel = _selection_from(fps["R1"], fps["C2"])
    snap = _fp_snapshot(adapter)
    pivot = pivot_centre_frame_from_selection(["R1", "C1", "C2"], snap, sel)
    assert pivot[0] == pytest.approx(0.0)
    assert pivot[1] == pytest.approx(0.0)


def test_pivot_from_selection_shifted_point_is_centre_minus_region_centre(
        main_window):
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    # selection = R1 -> centre (10,10); region centre (17,10) -> pivot (-7,0)
    sel = _selection_from(fps["R1"])
    snap = _fp_snapshot(adapter)
    pivot = pivot_centre_frame_from_selection(["R1", "C1", "C2"], snap, sel)
    assert pivot[0] == pytest.approx(-7.0)
    assert pivot[1] == pytest.approx(0.0)


def test_pivot_from_selection_no_recorded_ref_on_board_is_fatal(main_window):
    """Edge 1 — none of the recorded refs on the live board: the region centre
    cannot be computed, so we raise instead of guessing."""
    adapter = _line_board()
    adapter._fps = []
    snap = _fp_snapshot(adapter)  # empty snapshot = no recorded ref present
    with pytest.raises(ValidationError):
        pivot_centre_frame_from_selection(["R1", "C1", "C2"], snap, [])


def test_pivot_from_selection_empty_selection_is_fatal(main_window):
    """Edge 3 — the selection is empty: the selected centre cannot be computed,
    so we raise instead of guessing."""
    adapter = _line_board()
    snap = _fp_snapshot(adapter)
    with pytest.raises(ValidationError):
        pivot_centre_frame_from_selection(["R1", "C1", "C2"], snap, [])


def test_missing_record_refs_reports_only_absent_refs(main_window):
    adapter = _line_board()
    assert missing_record_refs(["R1", "C1", "C2"],
                               _fp_snapshot(adapter)) == []
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]
    assert missing_record_refs(["R1", "C1", "C2"],
                               _fp_snapshot(adapter)) == ["C2"]
    # a ref never present on the board is reported too (sorted)
    assert missing_record_refs(["R1", "C9", "C2"],
                               _fp_snapshot(adapter)) == ["C2", "C9"]


def test_pivot_helpers_take_a_snapshot_not_an_adapter(main_window):
    """Commit H regression guard — the pivot helpers read positions from the
    footprint SNAPSHOT (Selected-like objects with .ref + .fp), never from a
    live adapter. The snapshot entries below expose ONLY .ref/.fp — no
    .get_footprints or any other adapter method — so a helper that regressed
    to calling adapter methods would raise AttributeError, proving a GUI-
    thread click can never fire a second adapter.get_footprints() IPC on the
    shared kipy REQ socket (plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_
    fix.md §0)."""
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    snap = [SimpleNamespace(ref=s.ref, fp=s.fp) for s in _fp_snapshot(adapter)]
    sel = _selection_from(fps["R1"])
    assert live_record_centre_mm(["R1", "C1", "C2"], snap) == pytest.approx(
        (17.0, 10.0))
    assert missing_record_refs(["R1", "C1", "C2"], snap) == []
    pivot = pivot_centre_frame_from_selection(["R1", "C1", "C2"], snap, sel)
    assert pivot[0] == pytest.approx(-7.0)


def test_pivot_take_from_selection_fills_fields_from_live_selection(
        main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)  # records R1/C1/C2, pivot defaults to (0,0)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)
    # Commit H — positions come from the polled full-board snapshot cache
    # (connection.snapshot), not from a direct adapter IPC.
    dock._connection.snapshot = _fp_snapshot(adapter)
    fps = _fps_by_ref(adapter)
    dock.set_board_selection([], _selection_from(fps["R1"]))  # live sel = R1

    dock.pivot_from_selection_button.click()

    # R1 centre (10,10) minus region centre (17,10) -> pivot (-7,0)
    assert float(dock.pivot_x_edit.text()) == pytest.approx(-7.0)
    assert float(dock.pivot_y_edit.text()) == pytest.approx(0.0)


def test_pivot_take_from_selection_missing_recorded_ref_warns_and_computes(
        main_window, tmp_path, caplog):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]  # C2 missing
    _connect_board(dock, adapter)
    # Commit H — the snapshot mirrors the LIVE board (C2 already gone).
    dock._connection.snapshot = _fp_snapshot(adapter)
    fps = _fps_by_ref(adapter)
    dock.set_board_selection([], _selection_from(fps["R1"]))

    dock.pivot_from_selection_button.click()

    # present refs R1/C1 -> region centre x = 15; pivot = 10 - 15 = -5
    assert float(dock.pivot_x_edit.text()) == pytest.approx(-5.0)
    assert any("not all recorded components are on the board" in r.message
               for r in caplog.records)


def test_pivot_take_from_selection_without_loaded_record_is_reported_no_crash(
        main_window, tmp_path, caplog):
    root = _record_file(tmp_path, _record_dict(_line_board()))
    dock = SchemeListFormWidget(main_window)
    dock.set_root_path(root)  # nothing loaded -> _entry empty, _path None
    _connect_board(dock, _line_board())
    dock.set_board_selection([], _selection_from(_line_board().get_footprints()[0]))

    dock.pivot_from_selection_button.click()  # must not crash

    assert any("Load a Scheme List record first." in r.message
               for r in caplog.records)
    assert dock.pivot_x_edit.text() == ""


def test_pivot_take_from_selection_requires_live_board(main_window, tmp_path, caplog):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)  # board NOT connected
    dock.set_board_selection([], _selection_from(adapter.get_footprints()[0]))

    dock.pivot_from_selection_button.click()  # must not crash

    assert any("Connect to KiCad first." in r.message for r in caplog.records)
    assert dock.pivot_x_edit.text() == "0.00"  # fields untouched


def test_pivot_take_from_selection_is_preview_only_no_write_no_saved(
        main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)
    dock._connection.snapshot = _fp_snapshot(adapter)
    fps = _fps_by_ref(adapter)
    dock.set_board_selection([], _selection_from(fps["R1"]))
    before = root.read_text(encoding="utf-8")
    emitted = []
    dock.saved.connect(lambda: emitted.append(True))

    dock.pivot_from_selection_button.click()

    # the click only PREFILLS the fields — no file write, no saved.emit;
    # the explicit Apply (Save pivot) is still required to persist.
    assert root.read_text(encoding="utf-8") == before
    assert emitted == []
    assert float(dock.pivot_x_edit.text()) == pytest.approx(-7.0)


def test_record_page_take_from_selection_never_calls_adapter_for_positions(
        main_window, tmp_path):
    """Commit H regression guard — the record page's 'Take from selection'
    reads the recorded refs' positions from connection.snapshot, never from
    adapter.get_footprints(): an adapter that records every get_footprints()
    call must see NONE from the click (a GUI-thread adapter IPC is exactly the
    hang this fix removes — plan_2026_09_08_..._hang_fix.md §0)."""
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)

    calls = []

    class _GuardedAdapter(FakeAdapter):
        def get_footprints(self):
            calls.append("get_footprints")
            return super().get_footprints()

    _connect_board(dock, _GuardedAdapter(adapter.get_footprints(), [], [], {}))
    dock._connection.snapshot = _fp_snapshot(adapter)  # the polled cache
    fps = _fps_by_ref(adapter)
    dock.set_board_selection([], _selection_from(fps["R1"]))

    dock.pivot_from_selection_button.click()

    assert calls == []  # the adapter was never reached for footprint positions
    assert float(dock.pivot_x_edit.text()) == pytest.approx(-7.0)


# ── Reread ─────────────────────────────────────────────────────────────────

def test_reread_identical_board_reports_no_changes(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)
    # "By selection"-record (no scope_sheet_paths): Reread's scope is the
    # CURRENT board selection (5c.4) — select the recorded region itself.
    _select(dock, "R1", "C1", "C2")

    result = dock._do_reread()
    assert "diff" in result
    diff = result["diff"]
    assert diff.changed is False
    assert diff.components_moved == []
    assert diff.components_added == [] and diff.refs_removed_from_scope == []
    assert diff.vias_added == [] and diff.tracks_removed == []


def test_reread_reports_moved_component_and_apply_rewrites_record(main_window, tmp_path, caplog):
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _record_dict(adapter0)
    root = _record_file(tmp_path, d0)
    dock = _make_dock(main_window, root, d0)
    # C2 moved +0.5 mm on the live board
    adapter1 = _line_board(c2_x_mm=24.5)
    _connect_board(dock, adapter1)
    # same recorded region as the CURRENT selection -> the fixed-set diff
    _select(dock, "R1", "C1", "C2")

    result = dock._do_reread()
    diff = result["diff"]
    moved = {c.ref for c in diff.components_moved}
    assert moved == {"C2"}
    assert diff.changed is True

    # explicit Apply re-captures and rewrites the stored record in place
    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result
    data = _load(root)
    entry = data["scheme_lists"][0]
    comps = {c["ref"]: c for c in entry["components"]}
    # centre-frame offsets of the re-capture over R1/C1/C2 (centre x = 17.25)
    assert comps["C2"]["offset_along_mm"] == pytest.approx(7.25)
    assert comps["R1"]["offset_along_mm"] == pytest.approx(-7.25)
    # and the diff against the SAME live board is now clean
    dock.load_entry(data["scheme_lists"][0])
    result2 = dock._do_reread()
    assert result2["diff"].changed is False


def test_reread_missing_ref_is_reported_not_silent(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]
    _connect_board(dock, adapter)
    # only the PRESENT refs can be re-selected (5c.4) — C2 stays a refs_not_found
    _select(dock, "R1", "C1")

    result = dock._do_reread()
    diff = result["diff"]
    assert diff.refs_not_found == ["C2"]
    # a physically-absent recorded ref is NEVER double-counted as removed-from-scope
    assert diff.refs_removed_from_scope == []
    assert diff.changed is True


def test_reread_requires_live_board(main_window, tmp_path, caplog):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)  # board not connected
    assert dock._do_reread() == {}
    assert any("Connect to KiCad first." in r.message for r in caplog.records)


# ── Storage helpers (Record... / Reread Apply write path) ──────────────────

def test_write_record_auto_creates_json_and_includes_it(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {})
    adapter = _line_board()
    record = capture_scheme_list("amp", ["R1", "C1", "C2"], adapter=adapter)

    written = write_scheme_list_record(root, record)

    assert written == default_scheme_list_path(root)
    assert written.exists()
    json_data = json.loads(written.read_text(encoding="utf-8"))
    assert json_data["scheme_lists"][0]["name"] == "amp"
    root_data = _load(root)
    assert root_data["include"] == ["scheme_lists.json"]

    # A second record write must not duplicate the include line.
    write_scheme_list_record(root, record)
    root_data = _load(root)
    assert root_data["include"] == ["scheme_lists.json"]


def test_write_record_upserts_by_name_into_existing_json(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, {})
    adapter = _line_board()
    record = capture_scheme_list("amp", ["R1", "C1", "C2"], adapter=adapter)
    write_scheme_list_record(root, record)
    # re-capture same name from a moved board -> replace in place (still 1 record)
    adapter2 = _line_board(c2_x_mm=25.0)
    record2 = capture_scheme_list("amp", ["R1", "C1", "C2"], adapter=adapter2)
    write_scheme_list_record(root, record2)
    data = read_scheme_list_records(root)
    assert len(data) == 1
    comps = {c["ref"]: c for c in data[0]["components"]}
    # centre-frame: centre x = (10+25)/2 = 17.5 -> C2 offset 7.5
    assert comps["C2"]["offset_along_mm"] == pytest.approx(7.5)


def test_duplicate_problems_catches_name_and_ref_before_capture(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)

    problems = scheme_list_duplicate_problems(root, "amp", ["R1", "ZZ9"])
    texts = " ".join(problems)
    assert "amp" in texts            # duplicate name
    assert "R1" in texts             # ref already in another record
    # clean name + foreign refs -> no problems
    assert scheme_list_duplicate_problems(root, "other", ["QQ1", "QQ2"]) == []


# ── ConfigTreeDock: scheme_lists section + single-click routing ────────────

def _find(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def test_config_tree_shows_scheme_lists_section_and_click_emits_signal(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    section = _find(root_item, "Scheme lists")
    leaf = _find(section, "amp")
    assert leaf is not None

    captured = []
    dock.scheme_list_picked.connect(captured.append)
    dock._on_clicked(leaf, 0)
    assert len(captured) == 1
    assert isinstance(captured[0], dict)
    assert captured[0]["name"] == "amp"


# ── Dialogs (Record name; Reread diff with gated Apply) ────────────────────

def test_record_dialog_by_selection_collects_name_only(main_window):
    """The secondary "By selection" tab: result_data returns (name, None, None)
    — no anchor is picked at Record time (the frame is the region centre, the
    pivot defaults to it, design_2026_09_07_scheme_list_pivot.md)."""
    snapshot = [SimpleNamespace(ref="R1", sheet=["Channel_0"]),
                SimpleNamespace(ref="C1", sheet=["Channel_0"]),
                SimpleNamespace(ref="C2", sheet=["Channel_0"])]
    dialog = RecordSchemeListDialog(snapshot, ["R1", "C1", "C2"], main_window)
    assert isinstance(dialog.tabs, QTabWidget)
    assert dialog.tabs.tabText(0) == "By sheet"
    assert dialog.is_by_sheet()  # "By sheet" is the first/default tab
    dialog.tabs.setCurrentIndex(1)  # -> "By selection"
    dialog.name_edit.setText("psu_front")
    assert dialog.result_data() == ("psu_front", None, None)
    assert not dialog.is_by_sheet()
    # the selection is shown read-only and OK is enabled (refs present)
    assert "R1" in dialog.selection_refs_label.text()
    assert dialog._ok_button.isEnabled()


def test_diff_dialog_gates_apply_when_a_ref_is_missing(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    # C2 moved on the live board -> clean diff, Apply allowed
    adapter1 = _line_board(c2_x_mm=24.5)
    _connect_board(dock, adapter1)
    _select(dock, "R1", "C1", "C2")
    clean_diff = dock._do_reread()["diff"]
    dialog = SchemeListDiffDialog("amp", clean_diff, main_window)
    apply_btn = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Apply")
    assert apply_btn.isEnabled()
    dialog.close()
    # a missing ref -> the same dialog shows the problem and disables Apply
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]
    _connect_board(dock, adapter)
    _select(dock, "R1", "C1")
    missing_diff = dock._do_reread()["diff"]
    assert missing_diff.refs_not_found == ["C2"]
    dialog2 = SchemeListDiffDialog("amp", missing_diff, main_window)
    apply_btn2 = next(b for b in dialog2.findChildren(QPushButton)
                      if b.text() == "Apply")
    assert not apply_btn2.isEnabled()
    dialog2.close()


# ── G1: per-net boundary dialog (choose_boundary_actions) ───────────────────
# plan_2026_09_06_boundary_truncate.md §5-G1 — the v1 bool
# confirm_boundary_exclusions is replaced by a per-net Exclude|Truncate dialog
# whose OK returns a dict: {net: "truncate"} for the nets switched off Exclude,
# {} = all-exclude (v1 record-with-exclusions), None = Cancel. The dialog only
# READS the widgets — it never applies anything (the two-phase Record flow that
# honours a truncate choice is G2).

def _boundary_net(net, external_ref=None, action="exclude"):
    return SchemeListBoundaryNet(net=net, action=action,
                                 external_ref=external_ref)


def _two_boundary_nets():
    return [_boundary_net("NET1", external_ref="R9"),
            _boundary_net("NET2")]


def test_boundary_net_rows_filters_real_models_and_keeps_diagnostics():
    """The pure helper drops anything that is not a SchemeListBoundaryNet and
    keeps net + external_ref — the input rows the dialog renders."""
    rows = boundary_net_rows([_boundary_net("NET1", external_ref="R9"),
                              _boundary_net("NET2"), "stray-not-a-model"])
    assert [(r.net, r.external_ref) for r in rows] == [
        ("NET1", "R9"), ("NET2", None)]
    assert boundary_net_rows([]) == []


def test_boundary_dialog_builds_one_exclude_default_row_per_net(main_window):
    dialog = BoundaryNetDialog(boundary_net_rows(_two_boundary_nets()),
                               main_window)
    try:
        assert dialog.windowTitle() == "Boundary nets"
        assert len(dialog._combos) == 2
        for _net, combo in dialog._combos:
            assert combo.currentData() == "exclude"  # default Exclude
            assert [combo.itemText(i) for i in range(combo.count())] == [
                "Exclude", "Truncate"]
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        assert any(b.text() == "Record"
                   for b in dialog.findChildren(QPushButton))
        assert box.button(QDialogButtonBox.StandardButton.Cancel) is not None
    finally:
        dialog.close()


def test_boundary_dialog_shows_external_ref_diagnostics(main_window):
    dialog = BoundaryNetDialog(boundary_net_rows(_two_boundary_nets()),
                               main_window)
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        # a net dragged by an external component shows "NET1 (touched by R9)"
        assert any("NET1" in t and "R9" in t for t in texts)
        # a net with no external ref is shown bare
        assert any(t == "NET2" for t in texts)
    finally:
        dialog.close()


def test_boundary_dialog_selected_actions_reflect_combo_choices(main_window):
    dialog = BoundaryNetDialog(boundary_net_rows(_two_boundary_nets()),
                               main_window)
    try:
        assert dialog.selected_actions() == {}  # all-exclude default
        by_net = dict(dialog._combos)
        by_net["NET1"].setCurrentIndex(1)  # -> Truncate
        assert dialog.selected_actions() == {"NET1": "truncate"}
        by_net["NET2"].setCurrentIndex(1)
        assert dialog.selected_actions() == {"NET1": "truncate",
                                             "NET2": "truncate"}
    finally:
        dialog.close()


def test_choose_boundary_actions_cancel_returns_none(main_window, monkeypatch):
    import gui.docks.scheme_list as sl_mod
    monkeypatch.setattr(sl_mod.BoundaryNetDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    assert choose_boundary_actions(main_window, _two_boundary_nets()) is None


def test_choose_boundary_actions_all_exclude_returns_empty_dict(
        main_window, monkeypatch):
    import gui.docks.scheme_list as sl_mod
    monkeypatch.setattr(sl_mod.BoundaryNetDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    assert choose_boundary_actions(main_window, _two_boundary_nets()) == {}


def test_choose_boundary_actions_truncate_choice_returns_dict(
        main_window, monkeypatch):
    import gui.docks.scheme_list as sl_mod

    def _accepted_after_flipping_first_row(self):
        # emulate the user switching the first net to Truncate before OK
        self._combos[0][1].setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(sl_mod.BoundaryNetDialog, "exec",
                        _accepted_after_flipping_first_row)
    assert choose_boundary_actions(main_window,
                                   _two_boundary_nets()) == {"NET1": "truncate"}


def test_choose_boundary_actions_empty_boundary_nets_returns_empty():
    """No boundary nets -> {} without opening any dialog (the Record flow only
    calls this on a non-empty list anyway)."""
    assert choose_boundary_actions(None, []) == {}


# ── G2: Record two-phase capture (plan §5-G2) ───────────────────────────────
# The phase-1 worker returns the record + the original payload; the phase-1
# finish shows the per-net dialog (G1) and either writes phase-1 (no boundary
# nets / all-exclude — v1 single-pass regression) or launches phase-2 with the
# SAME payload + boundary_net_actions; the phase-2 finish writes THAT result
# without ever re-opening the dialog.

def _record_with_boundary(seed: str, boundary_raw):
    """A minimal, loadable Scheme List record carrying raw boundary_nets — a
    phase-1/phase-2 stand-in for the Record flow tests (the loader normalises
    raw dicts into SchemeListBoundaryNet, so a phase-2 record can differ from
    phase-1 by its action="truncate"). No anchor field — the centre-frame
    record only needs its components."""
    return load_scheme_list({
        "name": f"amp{seed}",
        "components": [{"ref": "R1", "offset_along_mm": 0.0,
                        "offset_across_mm": 0.0, "rotation_deg": 0.0}],
        "boundary_nets": boundary_raw,
    })


def _record_hub(main_window):
    """A DockHub stand-in for _finish_record_capture / _write_record_result /
    _finish_record_capture_phase2 — no __init__ side effects, only the members
    those methods touch."""
    from gui.dock_hub import DockHub
    hub = DockHub.__new__(DockHub)
    hub.main_window = main_window
    hub._scheme_active_op = None
    hub.config_tree_dock = SimpleNamespace(
        refresh=lambda: None,
        graph_changed=SimpleNamespace(emit=lambda: None))
    return hub


def test_run_record_capture_forwards_boundary_net_actions(main_window, monkeypatch):
    """G2 worker: the payload's optional boundary_net_actions reaches
    capture_scheme_list; a payload WITHOUT the key forwards None (v1
    byte-identical). The original payload rides back in the result so phase-1's
    finish can re-run phase-2 with the same Record payload."""
    import kicadstamp.scheme_list_capture as cap_mod
    from gui.dock_hub import DockHub

    hub = DockHub.__new__(DockHub)
    seen = {}
    monkeypatch.setattr(cap_mod, "capture_scheme_list",
                        lambda **kw: seen.update(kw) or object())
    payload = {"name": "amp", "refs": ["R1"],
               "board": SimpleNamespace(adapter=None), "root": ".",
               "boundary_net_actions": {"NET1": "truncate"}}
    result = hub._run_record_capture(payload)
    assert result["payload"] is payload
    assert seen.get("boundary_net_actions") == {"NET1": "truncate"}

    seen2 = {}
    monkeypatch.setattr(cap_mod, "capture_scheme_list",
                        lambda **kw: seen2.update(kw) or object())
    hub._run_record_capture({"name": "amp", "refs": ["R1"],
                             "board": SimpleNamespace(adapter=None),
                             "root": "."})
    assert seen2.get("boundary_net_actions") is None


def test_finish_record_capture_no_boundary_nets_writes_without_dialog(
        main_window, tmp_path, monkeypatch):
    """G2 v1 regression: a record with NO boundary nets is written straight
    away — the per-net dialog is never opened."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    hub = _record_hub(main_window)
    record = _record_with_boundary("A", [])
    calls = []
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: calls.append(a) or {})
    hub._finish_record_capture({"record": record, "root": str(root),
                                "payload": {}})
    assert calls == []
    data = read_scheme_list_records(root)
    assert [e["name"] for e in data] == ["ampA"]
    # an empty boundary_nets list is not written (scheme_list_to_dict omits it)
    assert data[0].get("boundary_nets", []) == []


def test_finish_record_capture_all_exclude_single_pass_and_writes(
        main_window, tmp_path, monkeypatch):
    """G2: all-exclude (dialog {} ) writes the ALREADY-captured phase-1 record
    with NO second IPC (the v1 record-with-exclusions regression)."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _record_with_boundary("A", [{"net": "NET1", "action": "exclude",
                                          "external_ref": "R9"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions", lambda *a, **k: {})
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    hub._finish_record_capture({"record": record, "root": str(root),
                                "payload": {}})
    assert launched == []  # no phase-2
    data = read_scheme_list_records(root)
    assert [e["name"] for e in data] == ["ampA"]
    assert data[0]["boundary_nets"] == [{"net": "NET1", "action": "exclude",
                                         "external_ref": "R9"}]


def test_finish_record_capture_cancel_writes_nothing(
        main_window, tmp_path, monkeypatch):
    """G2: dialog Cancel (None) writes nothing and launches no phase-2."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _record_with_boundary("A", [{"net": "NET1", "action": "exclude"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions", lambda *a, **k: None)
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    hub._finish_record_capture({"record": record, "root": str(root),
                                "payload": {}})
    assert launched == []
    assert not default_scheme_list_path(root).exists()
    assert read_scheme_list_records(root) == []


def test_finish_record_capture_truncate_launches_phase2_with_actions(
        main_window, tmp_path, monkeypatch):
    """G2: a truncate choice launches phase-2 with the SAME Record payload +
    boundary_net_actions (nothing is written by phase-1 itself)."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _record_with_boundary("A", [{"net": "NET1", "action": "exclude"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: {"NET1": "truncate"})
    payloads = []
    monkeypatch.setattr(
        worker_mod, "start_long_op",
        lambda _c, _w, worker, on_success, on_error, payload:
            payloads.append(payload) or object())
    payload = {"name": "ampA", "refs": ["R1"], "root": str(root)}
    hub._finish_record_capture({"record": record, "root": str(root),
                                "payload": payload})
    assert len(payloads) == 1
    p = payloads[0]
    assert p["boundary_net_actions"] == {"NET1": "truncate"}
    # the phase-1 fields are preserved for the re-capture
    assert p["name"] == "ampA" and p["refs"] == ["R1"]
    # phase-2 has not been written yet (only launched)
    assert not default_scheme_list_path(root).exists()


def test_finish_record_capture_phase2_writes_phase2_result_without_dialog(
        main_window, tmp_path, monkeypatch):
    """G2: the phase-2 finish writes the phase-2 record (here: the net already
    resolved action="truncate") and never re-opens the decision dialog."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    hub = _record_hub(main_window)
    record2 = _record_with_boundary("A", [{"net": "NET1", "action": "truncate",
                                           "external_ref": "R9"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: AssertionError("dialog must not reopen"))
    hub._finish_record_capture_phase2({"record": record2, "root": str(root),
                                       "payload": {}})
    data = read_scheme_list_records(root)
    assert [e["name"] for e in data] == ["ampA"]
    assert data[0]["boundary_nets"] == [{"net": "NET1", "action": "truncate",
                                         "external_ref": "R9"}]


# ── G3: Re-source two-phase capture (plan §5-G3) ───────────────────────────
# Same shape as G2 but for the Re-source flow: phase-1 worker captures ONLY
# (no write — G3 split capture and write so a truncate choice can reach
# capture before the record is persisted) and returns the record + root +
# target_path + the original payload; the phase-1 finish shows the per-net
# dialog (G1) and either writes phase-1 (no boundary nets / all-exclude — v1
# single-pass) or launches phase-2 with the SAME payload +
# boundary_net_actions; the phase-2 finish writes THAT result to the OWNING
# file (target_path). Re-source never moves a record to the default
# scheme_lists.json (the §7 invariant). The _record_hub stand-in (defined in
# the G2 section above) is reused — it exposes exactly the members the
# Re-source finish methods touch.

def _resource_owner(tmp_path, amp_components=None):
    """A root profile including an OWNER .sexp that carries the record being
    re-sourced ("amp") plus a neighbour ("keep") — the file a Re-source
    phase-1 finish writes INTO (target_path). (.sexp, NOT .json: _write emits
    s-expr text, and the storage helpers pick the format by file extension —
    a .json-named file holding s-expr would fail to parse on the write path,
    and _write_resource_result's OSError branch opens a blocking QMessageBox
    in headless tests.)"""
    root = tmp_path / "root.sexp"
    owner = tmp_path / "owner.sexp"
    if amp_components is None:
        amp_components = [{"ref": "R1", "offset_along_mm": 0.0,
                           "offset_across_mm": 0.0, "rotation_deg": 0.0}]
    _write(root, {"include": ["owner.sexp"]})
    _write(owner, {"scheme_lists": [
        {"name": "amp", "components": amp_components},
        {"name": "keep", "components": [{"ref": "K1", "offset_along_mm": 0.0,
                                         "offset_across_mm": 0.0,
                                         "rotation_deg": 0.0}]}]})
    return root, owner


def _resource_boundary_record(boundary_raw, name="amp"):
    """A minimal loadable Scheme List record carrying raw boundary_nets, named
    after the EXISTING record a Re-source replaces (the owner-file upsert is by
    name, so the phase-2/phase-1 record must carry the record's own name)."""
    record = _record_with_boundary("X", boundary_raw)
    record.name = name
    return record


def test_run_resource_capture_forwards_boundary_net_actions_and_no_write(
        main_window, monkeypatch):
    """G3 worker: phase-1 Re-source capture returns record + root + target_path
    + the original payload and forwards the payload's optional
    boundary_net_actions to capture_scheme_list; a payload WITHOUT the key
    forwards None (v1 byte-identical). Phase-1 NEVER writes — the write is a
    separate finish step (G3 split)."""
    import kicadstamp.scheme_list_capture as cap_mod
    from gui.dock_hub import DockHub

    hub = DockHub.__new__(DockHub)
    seen = {}
    monkeypatch.setattr(cap_mod, "capture_scheme_list",
                        lambda **kw: seen.update(kw) or object())
    payload = {"name": "amp", "refs": ["R5"],
               "board": SimpleNamespace(adapter=None),
               "root": ".", "target_path": "/own/amp.json",
               "boundary_net_actions": {"NET1": "truncate"}}
    result = hub._run_resource_capture(payload)
    assert result["payload"] is payload
    assert result["target_path"] == "/own/amp.json"
    assert seen.get("boundary_net_actions") == {"NET1": "truncate"}

    seen2 = {}
    monkeypatch.setattr(cap_mod, "capture_scheme_list",
                        lambda **kw: seen2.update(kw) or object())
    hub._run_resource_capture({"name": "amp", "refs": ["R5"],
                               "board": SimpleNamespace(adapter=None),
                               "root": "."})
    assert seen2.get("boundary_net_actions") is None


def test_finish_resource_capture_no_boundary_nets_writes_to_target_without_dialog(
        main_window, tmp_path, monkeypatch):
    """G3 v1 regression: a Re-source record with NO boundary nets is written
    straight to the OWNING file (target_path) — the per-net dialog is never
    opened and nothing lands in the default scheme_lists.json."""
    root, owner = _resource_owner(tmp_path)
    owner_before = _load(owner)
    import gui.dock_hub as dh_mod
    hub = _record_hub(main_window)
    record = _resource_boundary_record([])
    calls = []
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: calls.append(a) or {})
    hub._finish_resource_capture({"record": record, "root": str(root),
                                  "target_path": str(owner), "payload": {}})
    assert calls == []
    # the record was REPLACED in the owner file (fresh capture — the new refs),
    # the neighbour is untouched.
    data = {e["name"]: e for e in _load(owner)["scheme_lists"]}
    assert set(data) == {"amp", "keep"}
    assert data["keep"] == owner_before["scheme_lists"][1]
    assert [c["ref"] for c in data["amp"]["components"]] == ["R1"]
    assert not default_scheme_list_path(root).exists()


def test_finish_resource_capture_all_exclude_single_pass_and_writes_owner(
        main_window, tmp_path, monkeypatch):
    """G3: all-exclude (dialog {}) writes the ALREADY-captured phase-1 record
    with NO second IPC, INTO THE OWNING FILE (target_path) — the record keeps
    its name and the neighbour in that file is untouched."""
    root, owner = _resource_owner(tmp_path)
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _resource_boundary_record([{"net": "NET1", "action": "exclude",
                                         "external_ref": "R9"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions", lambda *a, **k: {})
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    hub._finish_resource_capture({"record": record, "root": str(root),
                                  "target_path": str(owner), "payload": {}})
    assert launched == []  # no phase-2
    data = {e["name"]: e for e in _load(owner)["scheme_lists"]}
    assert set(data) == {"amp", "keep"}
    # The .sexp writer omits DEFAULT-valued fields (unlike the .json writer
    # G2 uses): an exclude boundary net is persisted WITHOUT its action key —
    # the loader re-defaults it to exclude on read, so the v1 exclusion
    # decision is preserved semantically.
    assert data["amp"]["boundary_nets"] == [
        {"net": "NET1", "external_ref": "R9"}]
    amp_loaded = load_scheme_list(data["amp"])
    assert [(bn.net, bn.action)
            for bn in amp_loaded.boundary_nets] == [("NET1", "exclude")]
    # nothing was written to the DEFAULT scheme_lists.json
    assert not default_scheme_list_path(root).exists()


def test_finish_resource_capture_cancel_writes_nothing(
        main_window, tmp_path, monkeypatch):
    """G3: dialog Cancel (None) writes nothing — the owner file is untouched —
    and launches no phase-2."""
    root, owner = _resource_owner(tmp_path)
    owner_before = _load(owner)
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _resource_boundary_record([{"net": "NET1", "action": "exclude"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions", lambda *a, **k: None)
    launched = []
    monkeypatch.setattr(worker_mod, "start_long_op",
                        lambda *a, **k: launched.append(a) or object())
    hub._finish_resource_capture({"record": record, "root": str(root),
                                  "target_path": str(owner), "payload": {}})
    assert launched == []
    assert _load(owner) == owner_before  # untouched


def test_finish_resource_capture_truncate_launches_phase2_with_actions(
        main_window, tmp_path, monkeypatch):
    """G3: a truncate choice launches phase-2 with the SAME Re-source payload +
    boundary_net_actions and the phase-2 finish (nothing is written by phase-1
    itself)."""
    root = tmp_path / "root.sexp"
    _write(root, {})
    import gui.dock_hub as dh_mod
    import gui.worker as worker_mod
    hub = _record_hub(main_window)
    record = _resource_boundary_record([{"net": "NET1", "action": "exclude"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: {"NET1": "truncate"})
    seen = {}
    monkeypatch.setattr(
        worker_mod, "start_long_op",
        lambda _c, _w, worker, on_success, on_error, payload:
            seen.update(worker=worker, success=on_success, payload=payload)
            or object())
    payload = {"name": "amp", "refs": ["R5"], "root": str(root),
               "target_path": str(root)}
    hub._finish_resource_capture({"record": record, "root": str(root),
                                  "target_path": str(root), "payload": payload})
    assert seen["worker"] == hub._run_resource_capture
    assert seen["success"] == hub._finish_resource_capture_phase2
    p = seen["payload"]
    assert p["boundary_net_actions"] == {"NET1": "truncate"}
    # the phase-1 fields are preserved for the re-capture
    assert p["name"] == "amp" and p["refs"] == ["R5"]
    assert p["target_path"] == str(root)
    # phase-2 has not been written yet (only launched)
    assert _load(root).get("scheme_lists") is None


def test_finish_resource_capture_phase2_writes_to_owner_without_dialog(
        main_window, tmp_path, monkeypatch):
    """G3: the phase-2 finish writes the phase-2 record (here: the net already
    resolved action="truncate") to the OWNING FILE (target_path) and never
    re-opens the decision dialog."""
    root, owner = _resource_owner(tmp_path)
    import gui.dock_hub as dh_mod
    hub = _record_hub(main_window)
    record2 = _resource_boundary_record(
        [{"net": "NET1", "action": "truncate", "external_ref": "R9"}])
    monkeypatch.setattr(dh_mod, "choose_boundary_actions",
                        lambda *a, **k: AssertionError("dialog must not reopen"))
    hub._finish_resource_capture_phase2({"record": record2, "root": str(root),
                                         "target_path": str(owner),
                                         "payload": {}})
    data = {e["name"]: e for e in _load(owner)["scheme_lists"]}
    assert set(data) == {"amp", "keep"}
    assert data["amp"]["boundary_nets"] == [
        {"net": "NET1", "action": "truncate", "external_ref": "R9"}]
    assert not default_scheme_list_path(root).exists()


# ── DockHub wiring (page registered + single click opens it) ───────────────

def test_dock_hub_registers_scheme_list_page_and_routes_pick(main_window, tmp_path):
    from gui.dock_hub import DockHub

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        idx = hub._scheme_list_page
        assert hub.config_tree_dock.right_stack.widget(idx) is hub.scheme_list_dock
        adapter = _line_board()
        d = _record_dict(adapter)
        hub.config_tree_dock.scheme_list_picked.emit(d)
        assert hub.config_tree_dock.right_stack.currentWidget() is hub.scheme_list_dock
        assert hub.scheme_list_dock._entry.get("name") == "amp"
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            import logging
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


# ── Stage 5a: "By sheet" helpers + the two-tab Record dialog ───────────────
# plan_2026_09_06_scheme_list_sheet_capture.md 5a — the pure sheet-scope
# helpers (5a.1) and the two-tab RecordSchemeListDialog + DockHub ref
# derivation (5a.3). Synthetic snapshot rows are SimpleNamespace(ref, sheet)
# Selected stand-ins — the plan's headless style.

def _snap(*rows):
    """[(ref, path_tuple), ...] -> a synthetic Selected snapshot (list)."""
    return [SimpleNamespace(ref=ref, sheet=list(path)) for ref, path in rows]


_HIER = [
    ("R1", ("Top",)),
    ("C1", ("Top", "Ch0")),
    ("C2", ("Top", "Ch0")),
    ("C3", ("Top", "Ch1")),
    ("U1", ("Top", "Ch0", "Amp")),
    ("C4", ("Other",)),
]


# ── Commit H — footprint-bearing snapshots (live-position source) ───────────
# plan_2026_09_08_scheme_list_pivot_direct_ipc_hang_fix.md: "Take from
# selection" now reads the recorded refs' positions from the full-board
# footprint SNAPSHOT (BoardConnection.snapshot — Selected.ref + Selected.fp),
# never from a direct adapter.get_footprints() IPC on the GUI thread. The two
# helpers below build that snapshot shape from a FakeAdapter so the pivot
# tests exercise the same cache the real GUI feeds the helpers with.

def _fp_snapshot(adapter):
    """A footprint-bearing snapshot (Selected-like .ref + .fp) mirroring
    BoardConnection.snapshot — the cache the pivot helpers now read positions
    from instead of the adapter (the record page reads
    connection.snapshot directly)."""
    return [SimpleNamespace(ref=fp.ref, fp=fp)
            for fp in adapter.get_footprints()]


def _snap_live(adapter, rows=_HIER):
    """A footprint-bearing dialog snapshot: `rows` [(ref, path), ...] (default
    the whole _HIER) merged with each row's raw .fp from `adapter` — rows whose
    ref is not on the adapter keep their sheet identity but carry fp=None (they
    define the "By sheet" tree yet contribute no live position, like a real
    snapshot whose recorded refs are a subset of the board). Mirrors
    BoardConnection.snapshot (Selected.ref + Selected.fp)."""
    by_ref = {fp.ref: fp for fp in adapter.get_footprints()}
    return [SimpleNamespace(ref=ref, sheet=list(path), fp=by_ref.get(ref))
            for ref, path in rows]


# ── 5a.1 — pure helpers ────────────────────────────────────────────────────

def test_live_sheet_paths_dedups_keeps_nesting_and_sorts():
    snapshot = _snap(*_HIER)
    paths = live_sheet_paths(snapshot)
    # full path tuples, sorted; a nested path stays distinct from its parent
    assert paths == [("Other",), ("Top",), ("Top", "Ch0"),
                     ("Top", "Ch0", "Amp"), ("Top", "Ch1")]


def test_live_sheet_paths_dedups_same_sheet_and_skips_unresolved():
    snapshot = _snap(("R1", ("Top",)), ("R2", ("Top",)),       # same sheet -> one
                     ("C1", ("Top", None)), ("X1", (None,)))   # None segments -> skip
    assert live_sheet_paths(snapshot) == [("Top",)]


def test_live_sheet_paths_empty_snapshot_is_empty():
    assert live_sheet_paths([]) == []


# ── 2026-09-07 fix — Record/Re-source's "By sheet" tab was structurally
# always empty: Board.connect() (gui/connection.py) never passes
# schematic_dir, so a live Board's OWN sheet_names is always {} and every
# Selected.sheet is a list of None, which live_sheet_paths() filters out
# entirely. snapshot_with_resolved_sheets() re-resolves .sheet against the
# ALREADY-loaded config-based ctx.sheet_names before the snapshot reaches
# RecordSchemeListDialog (plan_2026_09_07_scheme_list_sheet_names_empty.md).

def test_snapshot_with_resolved_sheets_resolves_against_config_map():
    fp = _fp("U1", 0, 0)
    fp.sheet_path_uuids = ("sch-top", "sch-ch0", fp.uuid)
    stale = Selected(ref="U1", role=None, cluster=None, sheet=[None, None],
                     nets={}, fp=fp)
    resolved = snapshot_with_resolved_sheets(
        [stale], {"sch-top": "Top", "sch-ch0": "Ch0"})
    assert resolved[0].sheet == ["Top", "Ch0"]
    # the input snapshot itself is untouched (a copy is returned)
    assert stale.sheet == [None, None]


def test_snapshot_with_resolved_sheets_empty_map_is_a_no_op():
    # No `.fp` on these rows at all — an empty sheet_names must not even try
    # to read it (every caller upstream of this helper now runs it
    # unconditionally, including tests that build snapshot rows without fp).
    snapshot = _snap(*_HIER)
    assert snapshot_with_resolved_sheets(snapshot, {}) is snapshot


def test_sheet_paths_under_returns_root_and_all_descendants():
    paths = [("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp"),
             ("Top", "Ch1"), ("Other",)]
    assert sheet_paths_under(paths, ("Top",)) == [
        ("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp"), ("Top", "Ch1")]
    assert sheet_paths_under(paths, ("Top", "Ch0")) == [
        ("Top", "Ch0"), ("Top", "Ch0", "Amp")]
    # leaf -> only itself
    assert sheet_paths_under(paths, ("Top", "Ch1")) == [("Top", "Ch1")]
    # a path that is NOT a descendant is excluded
    assert ("Other",) not in sheet_paths_under(paths, ("Top",))
    # root absent from `paths` -> nothing
    assert sheet_paths_under(paths, ("Missing",)) == []


def test_refs_on_sheet_is_direct_membership_not_recursive():
    snapshot = _snap(*_HIER)
    assert refs_on_sheet(snapshot, ("Top",)) == ["R1"]          # NOT C1/C2
    assert refs_on_sheet(snapshot, ("Top", "Ch0")) == ["C1", "C2"]
    assert refs_on_sheet(snapshot, ("Top", "Ch0", "Amp")) == ["U1"]
    assert refs_on_sheet(snapshot, ("Other",)) == ["C4"]
    assert refs_on_sheet(snapshot, ("Empty",)) == []


# ── Commit C — sheet_subtree_plan (Qt-free plan of the "By sheet" tree) ────

def test_sheet_subtree_plan_nests_candidates_under_the_root():
    """_HIER under "Top": Top -> Ch0 -> Amp and Top -> Ch1. Every sheet here has
    its own footprints (Ch0 has C1/C2), so NO structural node appears."""
    plan = sheet_subtree_plan(("Top",), [("Top",), ("Top", "Ch0"),
                                         ("Top", "Ch0", "Amp"), ("Top", "Ch1")])
    assert [n["name"] for n in plan] == ["Top"]
    top = plan[0]
    assert top["path"] == ("Top",)
    assert [c["name"] for c in top["children"]] == ["Ch0", "Ch1"]
    ch0 = top["children"][0]
    assert ch0["path"] == ("Top", "Ch0")
    assert [g["name"] for g in ch0["children"]] == ["Amp"]
    assert ch0["children"][0]["path"] == ("Top", "Ch0", "Amp")


def test_sheet_subtree_plan_inserts_structural_intermediate_without_footprints():
    """Top/Sub has NO footprints of its own (not a candidate), but Top/Sub/Leaf
    does -> a STRUCTURAL "Sub" branch keeps the nested Leaf under its parent
    instead of hanging it directly off Top (the visual gap of the old flat
    indented list, Commit C)."""
    plan = sheet_subtree_plan(("Top",), [("Top",), ("Top", "Sub", "Leaf")])
    top = plan[0]
    assert top["path"] == ("Top",)
    sub = top["children"][0]
    assert sub["path"] == ("Top", "Sub")
    assert [c["name"] for c in sub["children"]] == ["Leaf"]
    assert sub["children"][0]["path"] == ("Top", "Sub", "Leaf")


def test_sheet_subtree_plan_mid_root_builds_only_its_subtree():
    plan = sheet_subtree_plan(("Top", "Ch0"), [("Top", "Ch0"),
                                               ("Top", "Ch0", "Amp")])
    assert [n["name"] for n in plan] == ["Ch0"]
    assert plan[0]["children"][0]["path"] == ("Top", "Ch0", "Amp")


def test_sheet_subtree_plan_leaf_or_empty_candidates_are_bare():
    # A leaf root (only itself) yields the bare root node; empty -> nothing.
    assert sheet_subtree_plan(("Top",), [("Top",)]) == \
        [{"path": ("Top",), "name": "Top", "children": []}]
    assert sheet_subtree_plan(("Top",), []) == []


def test_all_checked_rows_union_matches_naive_prefix_filter():
    """The composition regression (plan 5a.1): summing refs_on_sheet over ALL
    rows under a root equals what a naive whole-snapshot prefix filter would
    give — i.e. the all-checked checklist is the old "take the whole subtree"
    behavior, expressed as a special case of the new mechanism."""
    snapshot = _snap(*_HIER)
    paths = live_sheet_paths(snapshot)
    rows = sheet_paths_under(paths, ("Top",))
    union = sorted({r for p in rows for r in refs_on_sheet(snapshot, p)})
    naive = sorted({s.ref for s in snapshot
                    if tuple(s.sheet)[:len(("Top",))] == ("Top",)})
    assert union == naive
    assert "C4" not in union  # the Other sheet stays outside the subtree


# ── Commit E — all_sheet_paths (full hierarchy incl. container sheets) ─────

def test_all_sheet_paths_includes_container_sheets_as_prefixes():
    """Channel_0 carries no footprint of its own but has DAC/OpAmp below — it
    MUST still appear (as a prefix) so the By-sheet tree can show the hierarchy
    (the live-board case: 9 leaf paths, containers missing -> flat combobox)."""
    snapshot = _snap(("U1", ("Ch0", "DAC")), ("U2", ("Ch0", "OpAmp")))
    assert all_sheet_paths(snapshot) == [("Ch0",), ("Ch0", "DAC"),
                                         ("Ch0", "OpAmp")]


def test_all_sheet_paths_matches_live_paths_when_every_container_has_refs():
    snapshot = _snap(*_HIER)
    # Here Top/Ch0 already have footprints, so prefixes add nothing new.
    assert all_sheet_paths(snapshot) == live_sheet_paths(snapshot)


def test_all_sheet_paths_skips_unresolved_and_empty():
    assert all_sheet_paths([]) == []
    snapshot = _snap(("R1", ("Top", None)), ("X1", (None,)))
    assert all_sheet_paths(snapshot) == []


# ── 5a.3 — RecordSchemeListDialog (two tabs, NO anchor pick) ───────────────

def test_record_dialog_two_tabs_with_by_sheet_default(main_window):
    """Commit F: the dialog now carries THREE tabs — the two source tabs plus
    the Pivot/Anchor tab (defaults to 0,0 = record centre)."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window)
    assert dialog.tabs.count() == 3
    assert dialog.tabs.tabText(0) == "By sheet"
    assert dialog.tabs.tabText(1) == "By selection"
    assert dialog.tabs.tabText(2) == "Pivot / Anchor"
    assert dialog.is_by_sheet()
    assert dialog.pivot_value() == (0.0, 0.0)


def test_record_dialog_by_sheet_defaults_unchecked_ok_disabled_until_tick(main_window):
    """Commit E: EVERY sheet starts UNCHECKED (you tick what to record) — OK is
    disabled until at least one sheet is marked; marking a sheet captures its
    DIRECT refs."""
    snapshot = _snap(("R1", ("Top",)), ("C1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    assert dialog._checked_sheet_paths() == []
    assert dialog._checked_refs() == []
    assert not dialog._ok_button.isEnabled()
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)
    assert dialog._checked_sheet_paths() == [("Top",)]
    assert dialog._checked_refs() == ["C1", "R1"]
    assert dialog._ok_button.isEnabled()


def test_record_dialog_by_sheet_marking_a_top_sheet_marks_its_whole_subtree(main_window):
    """Commit E + D: marking a top sheet (Top) cascades to its whole subtree —
    Ch0/Amp/Ch1 become Checked, capture = the whole Top subtree's direct refs."""
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)
    assert _tree_item_by_path(dialog, ("Top", "Ch0")).checkState(0) \
        == Qt.CheckState.Checked
    assert _tree_item_by_path(dialog, ("Top", "Ch0", "Amp")).checkState(0) \
        == Qt.CheckState.Checked
    assert dialog._checked_sheet_paths() == [
        ("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp"), ("Top", "Ch1")]
    assert dialog._checked_refs() == ["C1", "C2", "C3", "R1", "U1"]
    assert dialog._ok_button.isEnabled()


def _tree_walk(item):
    """Every QTreeWidgetItem under `item`, depth-first (the dialog's sheet-tree
    traversal shared by the Commit C GUI tests)."""
    out = []
    for i in range(item.childCount()):
        child = item.child(i)
        out.append(child)
        out.extend(_tree_walk(child))
    return out


def _tree_item_by_path(dialog, path):
    """The dialog.sheet_tree node whose path == `path`, or None. A candidate
    (capturable) sheet stores its path in UserRole; a STRUCTURAL branch stores
    it in UserRole+1 (UserRole stays empty so capture logic skips it)."""
    for item in _tree_walk(dialog.sheet_tree.invisibleRootItem()):
        if (item.data(0, Qt.ItemDataRole.UserRole) == path
                or item.data(0, Qt.ItemDataRole.UserRole + 1) == path):
            return item
    return None


def _tree_candidate_items(dialog):
    """The dialog.sheet_tree nodes that are capturable sheets (carry a path —
    structural branches have no path data and no checkbox)."""
    return [it for it in _tree_walk(dialog.sheet_tree.invisibleRootItem())
            if it.data(0, Qt.ItemDataRole.UserRole) is not None]


def test_record_dialog_by_sheet_unchecking_a_parent_drops_the_whole_branch(main_window):
    """Commit D/E: unchecking Ch0 (after marking Top) excludes its WHOLE subtree
    — Ch0's C1/C2 AND the nested Amp (U1); Top stays Partial (Ch1 on) so its own
    R1 is still read."""
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)  # whole Top subtree on
    ch0 = _tree_item_by_path(dialog, ("Top", "Ch0"))
    ch0.setCheckState(0, Qt.CheckState.Unchecked)  # branch off (Amp too)
    assert _tree_item_by_path(dialog, ("Top", "Ch0", "Amp")).checkState(0) \
        == Qt.CheckState.Unchecked
    assert dialog._checked_refs() == ["C3", "R1"]  # Top(partial) R1 + Ch1 C3
    assert ("Top", "Ch0") not in dialog._checked_sheet_paths()
    assert ("Top", "Ch0", "Amp") not in dialog._checked_sheet_paths()


def test_record_dialog_by_sheet_partial_parent_is_still_read(main_window):
    """Commit D/E: excluding ONE child (Amp) leaves Ch0 and Top PartiallyChecked
    and they are STILL read (their direct refs stay in); only the excluded child
    drops out."""
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)  # whole subtree on
    amp = _tree_item_by_path(dialog, ("Top", "Ch0", "Amp"))
    amp.setCheckState(0, Qt.CheckState.Unchecked)
    ch0 = _tree_item_by_path(dialog, ("Top", "Ch0"))
    assert ch0.checkState(0) == Qt.CheckState.PartiallyChecked
    assert _tree_item_by_path(dialog, ("Top",)).checkState(0) \
        == Qt.CheckState.PartiallyChecked
    # Ch0's own C1/C2 stay (it is read), U1 is out, everything else in.
    assert dialog._checked_sheet_paths() == [("Top",), ("Top", "Ch0"), ("Top", "Ch1")]
    assert dialog._checked_refs() == ["C1", "C2", "C3", "R1"]


def test_record_dialog_by_sheet_container_without_own_refs_is_checkable(main_window):
    """Commit E core case: Ch0 (a top-level sheet) carries NO footprint of its
    own but has DAC/OpAmp below — it is still a CHECKABLE node; marking it
    cascades to its sheets (Commit D) and its OWN path is stored in the scope
    (0 direct refs), while the capture refs come from the marked descendants."""
    snapshot = _snap(("U_DAC", ("Ch0", "DAC")), ("U_OP", ("Ch0", "OpAmp")))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    ch0 = _tree_item_by_path(dialog, ("Ch0",))
    assert ch0 is not None
    assert (ch0.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    ch0.setCheckState(0, Qt.CheckState.Checked)  # cascade: DAC/OpAmp on
    assert _tree_item_by_path(dialog, ("Ch0", "DAC")).checkState(0) \
        == Qt.CheckState.Checked
    assert dialog._checked_sheet_paths() == [("Ch0",), ("Ch0", "DAC"),
                                             ("Ch0", "OpAmp")]
    assert dialog._checked_refs() == ["U_DAC", "U_OP"]
    assert dialog._ok_button.isEnabled()


def test_record_dialog_by_sheet_top_level_sheets_are_tree_roots(main_window):
    """Commit E: each TOP-LEVEL sheet is its own root of the single tree (no
    root combobox any more) — marking one does not touch the other."""
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    roots = [dialog.sheet_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
             for i in range(dialog.sheet_tree.topLevelItemCount())]
    assert roots == [("Other",), ("Top",)]
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)
    assert _tree_item_by_path(dialog, ("Other",)).checkState(0) \
        == Qt.CheckState.Unchecked  # the other root is untouched


def test_record_dialog_by_sheet_labels_and_tooltips(main_window):
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    ch0 = _tree_item_by_path(dialog, ("Top", "Ch0"))
    assert ch0 is not None
    assert ch0.text(0) == "Ch0"          # leaf label, not the whole path
    assert ch0.toolTip(0) == "Top/Ch0"   # full path disambiguates


def test_record_dialog_by_sheet_unchecking_everything_disables_ok(main_window):
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)  # OK becomes enabled...
    assert dialog._ok_button.isEnabled()
    for it in _tree_candidate_items(dialog):     # ...then clear everything
        it.setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog._checked_sheet_paths() == []
    assert dialog._checked_refs() == []
    assert not dialog._ok_button.isEnabled()


def test_record_dialog_branch_state_all_on_all_off_mixed(main_window):
    """_branch_state: all-on -> Checked, all-off -> Unchecked, mixed -> Partial."""
    from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem
    tree = QTreeWidget()
    top = QTreeWidgetItem(tree.invisibleRootItem(), ["Top"])
    a = QTreeWidgetItem(top, ["A"])
    b = QTreeWidgetItem(top, ["B"])
    for it in (top, a, b):
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsUserTristate)
        it.setData(0, Qt.ItemDataRole.UserRole, "sheet")
    top.setCheckState(0, Qt.CheckState.Checked)
    a.setCheckState(0, Qt.CheckState.Checked)
    b.setCheckState(0, Qt.CheckState.Checked)
    assert RecordSchemeListDialog._branch_state(top) == Qt.CheckState.Checked
    a.setCheckState(0, Qt.CheckState.Unchecked)  # mixed -> PartiallyChecked
    assert RecordSchemeListDialog._branch_state(top) == Qt.CheckState.PartiallyChecked
    b.setCheckState(0, Qt.CheckState.Unchecked)
    top.setCheckState(0, Qt.CheckState.Unchecked)  # everything off
    assert RecordSchemeListDialog._branch_state(top) == Qt.CheckState.Unchecked


def test_record_dialog_ok_gated_per_active_tab(main_window):
    snapshot = _snap(("R1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    # By sheet: nothing marked yet -> OK off; marking the sheet -> OK on.
    assert dialog.is_by_sheet()
    assert not dialog._ok_button.isEnabled()
    top = _tree_item_by_path(dialog, ("Top",))
    top.setCheckState(0, Qt.CheckState.Checked)
    assert dialog._ok_button.isEnabled()
    dialog.tabs.setCurrentIndex(1)  # By selection, no selection -> off
    assert not dialog.is_by_sheet()
    assert not dialog._ok_button.isEnabled()


# ── 5a.3 — DockHub ref derivation + capture pass-through ───────────────────

def test_record_refs_for_by_sheet_limited_to_checked_sheets():
    snapshot = _snap(*_HIER)
    # Ch1 (C3) unchecked -> its refs must NOT appear in the capture set
    assert record_refs_for(snapshot, True, [("Top",), ("Top", "Ch0"),
                                            ("Top", "Ch0", "Amp")],
                           ["X1", "X2"]) == ["C1", "C2", "R1", "U1"]
    # all checked -> the whole subtree under Top
    assert record_refs_for(snapshot, True,
                           [("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp"),
                            ("Top", "Ch1")], []) == ["C1", "C2", "C3", "R1", "U1"]


def test_record_refs_for_by_selection_uses_selection_unchanged():
    snapshot = _snap(*_HIER)
    assert record_refs_for(snapshot, False, None, ["X1", "X2"]) == ["X1", "X2"]
    assert record_refs_for(snapshot, False, None, ["C4"]) == ["C4"]


def test_run_record_capture_honours_payload_refs_and_sheet_names():
    """_run_record_capture (synchronous, no worker) must capture exactly the
    payload's refs and feed sheet_names through for the source_sheet
    derivation — the pass-through that makes a By-sheet-limited payload real."""
    from gui.dock_hub import DockHub
    from kicadstamp.scheme_list_capture import capture_scheme_list  # noqa: F401

    hub = DockHub.__new__(DockHub)  # no __init__ side effects
    adapter = _line_board()
    names = _stamp_sheet(adapter)
    payload = {"name": "amp", "refs": ["R1", "C1"],
               "board": SimpleNamespace(adapter=adapter),
               "root": ".", "sheet_names": names}
    result = hub._run_record_capture(payload)
    record = result["record"]
    assert {c.ref for c in record.components} == {"R1", "C1"}
    assert record.source_sheet == "Channel_0"


def test_record_scheme_list_by_sheet_payload_refs_match_checked_sheets(
        main_window, tmp_path, monkeypatch):
    """record_scheme_list(): in "By sheet" mode the worker payload's refs are
    really the union over the CHECKED sheets (unchecking a sub-sheet excludes
    its refs from the capture). The dialog and worker are faked; the payload
    construction itself is exercised synchronously."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub
    from PyQt6.QtWidgets import QDialog

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [],
                  "entities": [{"name": "PARENT", "cell": "c_parent"}],
                  "trees": [{"name": "main", "anchor": {"origin": True},
                             "nodes": [{"ref": "PARENT", "kind": "placement",
                                        "xy": [0.0, 0.0]}]}]})
    # DockHub is built with the board still None (the hub-fixture pattern) —
    # the live snapshot + adapter are attached only for the Record call below.
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)

        captured = {}

        class _FakeDialog:
            def __init__(self, snapshot, selection_refs, parent,
                         *, adapter=None, selected_footprints=None,
                         pivot_initial=None, selection_provider=None,
                         snapshot_provider=None):
                captured["selection_provider"] = selection_provider
                captured["snapshot_provider"] = snapshot_provider

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return True

            def pivot_value(self):
                return (0.0, 0.0)

            def preset_name_to_save(self):
                return None

            def result_data(self):
                # User checked Top + Ch0 + its nested Amp, but NOT Ch1.
                return ("amp", ("Top",), [("Top",), ("Top", "Ch0"),
                                          ("Top", "Ch0", "Amp")])

        payloads = []
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog", _FakeDialog)
        # record_scheme_list imports start_long_op lazily (`from .worker import
        # start_long_op`) — patch the worker module, not dock_hub's namespace.
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.record_scheme_list()

        # Commit G — the dialog receives a LIVE selection provider (a view over
        # the hub's current selection, not an open-time snapshot), so "Take from
        # selection" honours a component selected while the dialog is open.
        assert captured["selection_provider"] is not None
        assert captured["selection_provider"]() == []
        hub._selection_footprints = [SimpleNamespace(ref="IC2")]
        assert [s.ref for s in captured["selection_provider"]()] == ["IC2"]
        # Commit H — the dialog also receives a LIVE full-board snapshot
        # provider (a view over connection.snapshot), so "Take from selection"
        # reads the recorded refs' CURRENT positions, not the open-time copy.
        assert captured["snapshot_provider"] is not None
        assert [s.ref for s in captured["snapshot_provider"]()] == [
            s.ref for s in _snap(*_HIER)]
        connection.snapshot = [SimpleNamespace(ref="Moved1")]
        assert [s.ref for s in captured["snapshot_provider"]()] == ["Moved1"]

        assert len(payloads) == 1
        # Ch1's C3 is excluded; R1/C1/C2/U1 are the checked-sheet union.
        assert payloads[0]["refs"] == ["C1", "C2", "R1", "U1"]
        # no anchor_ref is carried in the payload (no anchor in the dialog)
        assert "anchor_ref" not in payloads[0]
        # Commit F — the Pivot/Anchor tab's value rides in the payload.
        assert payloads[0]["pivot"] == [0.0, 0.0]
        # 5c.1 — the CHECKED leaf paths are persisted as the record's scope.
        assert payloads[0]["scope_sheet_paths"] == [
            ["Top"], ["Top", "Ch0"], ["Top", "Ch0", "Amp"]]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


def test_record_scheme_list_by_selection_payload_matches_selection_refs(
        main_window, tmp_path, monkeypatch):
    """record_scheme_list(): in "By selection" mode the payload's refs are the
    current board selection — identical to the pre-Stage-5a Record behavior
    (regression guard, design §2)."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub
    from PyQt6.QtWidgets import QDialog

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [],
                  "entities": [{"name": "PARENT", "cell": "c_parent"}],
                  "trees": [{"name": "main", "anchor": {"origin": True},
                             "nodes": [{"ref": "PARENT", "kind": "placement",
                                        "xy": [0.0, 0.0]}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = []
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)
        hub._selection_footprints = [SimpleNamespace(ref="C1"),
                                     SimpleNamespace(ref="R1")]

        class _FakeDialog:
            def __init__(self, snapshot, selection_refs, parent,
                         *, adapter=None, selected_footprints=None,
                         pivot_initial=None, selection_provider=None,
                         snapshot_provider=None):
                # Commit H — connection.snapshot is live when the dialog opens.
                assert snapshot_provider is not None
                assert [s.ref for s in snapshot_provider()] == []

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return False

            def pivot_value(self):
                return (0.0, 0.0)

            def preset_name_to_save(self):
                return None

            def result_data(self):
                return ("amp", None, None)

        payloads = []
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog", _FakeDialog)
        # record_scheme_list imports start_long_op lazily (`from .worker import
        # start_long_op`) — patch the worker module, not dock_hub's namespace.
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.record_scheme_list()

        assert len(payloads) == 1
        assert payloads[0]["refs"] == ["C1", "R1"]  # the board selection
        # 5c.1 — a "By selection" Record persists NO scope (None).
        assert payloads[0]["scope_sheet_paths"] is None
        # Commit F — the Pivot/Anchor tab's value rides in the payload.
        assert payloads[0]["pivot"] == [0.0, 0.0]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


# ── Stage 5b: Re-source (exclude_name, fixed-name dialog, worker) ───────────
# plan_2026_09_06_scheme_list_sheet_capture.md 5b — re-source re-points an
# EXISTING record at a different source under the SAME name:
#   5b.1 scheme_list_duplicate_problems(..., exclude_name) — the record being
#        replaced is not a duplicate of itself (its own refs are freed);
#   5b.2 RecordSchemeListDialog(fixed_name=...) — pinned read-only name, a
#        distinct "Re-source" title/OK and an in-dialog warning;
#   5b.3 DockHub worker — capture from the NEW source + write REPLACES the
#        record in the file that OWNS it; the UI flow opens the fixed-name
#        dialog, derives refs from the CHECKED sheets and passes exclude_name
#        + the owning file as target_path.

def test_duplicate_problems_exclude_name_allows_self_replacement(main_window, tmp_path):
    """5b.1 — a re-sourced record is not a duplicate of itself: its own name +
    its own (old) refs pass with exclude_name; a ref owned by ANOTHER record
    still blocks. Without exclude_name the old Record behavior is unchanged."""
    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [
        {"name": "amp",
         "components": [{"ref": "R1", "offset_along_mm": 0.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0},
                        {"ref": "C1", "offset_along_mm": 10.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0}]},
        {"name": "psu",
         "components": [{"ref": "X1", "offset_along_mm": 0.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0}]},
    ]})
    # Without exclude_name its own name AND its own refs are both duplicates
    # (the behavior Record... relies on).
    assert len(scheme_list_duplicate_problems(root, "amp", ["R1", "C1"])) == 2
    # Re-source passes exclude_name=the record itself -> clean.
    assert scheme_list_duplicate_problems(
        root, "amp", ["R1", "C1"], exclude_name="amp") == []
    # A ref owned by ANOTHER record stays fatal even for Re-source.
    other = scheme_list_duplicate_problems(root, "amp", ["X1"],
                                           exclude_name="amp")
    assert len(other) == 1 and "X1" in other[0]


def test_resource_dialog_fixed_name_read_only_title_and_re_source_ok(main_window):
    """5b.2 — Re-source mode of RecordSchemeListDialog: the name is pinned
    read-only, the title/OK say "Re-source" and both source tabs stay
    available (re-sourcing can come from either mode)."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window,
                                    fixed_name="amp")
    try:
        assert dialog.name_edit.isReadOnly()
        assert dialog.name_edit.text() == "amp"
        assert dialog.windowTitle() == "Re-source Scheme List 'amp'"
        assert dialog._ok_button.text() == "Re-source"
        assert dialog.tabs.count() == 3  # By sheet + By selection + Pivot/Anchor
        # result_data keeps the pinned name regardless of the active tab.
        assert dialog.result_data()[0] == "amp"
        dialog.tabs.setCurrentIndex(1)  # By selection
        assert dialog.result_data() == ("amp", None, None)
    finally:
        dialog.close()


def test_record_dialog_without_fixed_name_keeps_record_behavior(main_window):
    """5b.2 regression guard — omitting fixed_name leaves the Record dialog's
    editable name, original title and OK label untouched."""
    dialog = RecordSchemeListDialog([], [], main_window)
    try:
        assert not dialog.name_edit.isReadOnly()
        assert dialog.windowTitle() == "Record Scheme List"
        assert dialog._ok_button.text() == "OK"
    finally:
        dialog.close()


def _line_board_ch1():
    """A DIFFERENT clone — refs R5/C5/C6 on Channel_1 — the "new source" a
    Re-source points an existing record at (other refs + geometry + sheet than
    the record it replaces)."""
    r5 = _fp("R5", 10, 10, angle=45.0)
    c5 = _fp("C5", 20, 10)
    c6 = _fp("C6", 27, 10)
    net = "/Channel_1/AMP/+5V"
    pads = {"R5": [_pad("R5", 10, 10, net)],
            "C5": [_pad("C5", 20, 10, net)],
            "C6": [_pad("C6", 27, 10, net)]}
    t1 = _track(10, 10, 20, 10, net, layer=F)
    t2 = _track(20, 10, 27, 10, net, layer=IN1)
    v1 = _via(20, 10, net)
    return FakeAdapter([r5, c5, c6], [t1, t2], [v1], pads)


def test_run_resource_capture_replaces_record_under_same_name(main_window, tmp_path):
    """5b.3 worker, G3-split — the phase-1 capture worker ONLY captures (no
    write) and returns the fresh record + root + target_path; the phase-1
    finish (this record has no boundary nets -> write straight away) then
    REPLACES the record under the same name IN THE FILE THAT OWNS IT; the other
    record in the file is untouched and an Entity already placed on the record
    still resolves (round-trip through load_config + link_trees)."""
    from gui.dock_hub import DockHub

    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [
            {"name": "amp", "source_sheet": "Channel_0",
             "components": [
                 {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0},
                 {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0},
                 {"ref": "C2", "offset_along_mm": 14.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0}]},
            {"name": "keep",
             "components": [
                 {"ref": "K1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0}]},
        ],
        # An Entity already PLACED on "amp" — after the record is replaced it
        # must still resolve (it points at the record by NAME, not by content).
        "entities": [{"name": "E_AMP", "scheme_list": "amp"}],
        "trees": [{"name": "main", "anchor": {"origin": True},
                   "nodes": [{"ref": "E_AMP", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    })
    keep_before = next(e for e in _load(root)["scheme_lists"]
                       if e["name"] == "keep")

    new_adapter = _line_board_ch1()
    names = _stamp_sheet(new_adapter, sheet_uuid="sch-ch1", name="Channel_1")
    hub = DockHub.__new__(DockHub)  # worker/finish — no __init__ side effects
    hub.main_window = main_window
    hub._scheme_active_op = None
    hub.config_tree_dock = SimpleNamespace(
        refresh=lambda: None,
        graph_changed=SimpleNamespace(emit=lambda: None))
    payload = {"board": SimpleNamespace(adapter=new_adapter), "name": "amp",
               "refs": ["R5", "C5", "C6"],
               "root": str(root), "target_path": str(root),
               "sheet_names": names}
    result = hub._run_resource_capture(payload)

    # G3: phase-1 captures ONLY — the owner file is NOT written by the worker.
    assert "error" not in result, result
    assert "record" in result
    assert result["target_path"] == str(root)
    before = {e["name"]: e for e in _load(root)["scheme_lists"]}
    assert [c["ref"] for c in before["amp"]["components"]] == ["R1", "C1", "C2"]

    # The phase-1 finish (no boundary nets -> write straight to the owner file)
    # persists the replacement.
    hub._finish_resource_capture(result)
    records = {e["name"]: e for e in _load(root)["scheme_lists"]}
    assert set(records) == {"amp", "keep"}
    amp = records["amp"]
    # NEW refs/source_sheet/geometry, from the NEW source (no anchor field).
    assert [c["ref"] for c in amp["components"]] == ["R5", "C5", "C6"]
    assert "anchor_ref" not in amp
    assert amp["source_sheet"] == "Channel_1"
    # the untouched record stayed identical.
    assert records["keep"] == keep_before

    # round-trip: record replaced, the placed Entity still resolves via
    # link_trees (Stage-1 round-trip style).
    cfg, _ = load_config(str(root))

    def _all_ln(lnodes):
        for ln in lnodes:
            yield ln
            yield from _all_ln(ln.children)

    linked = link_trees(cfg, cfg.trees)[0]
    ln = next(ln for ln in _all_ln(linked.nodes) if ln.node.ref == "E_AMP")
    assert ln.record is not None
    assert ln.record.name == "E_AMP"


def test_run_resource_scheme_list_payload_uses_fixed_name_checked_refs_and_owner(
        main_window, tmp_path, monkeypatch):
    """5b.3 UI flow — resource_scheme_list_record opens the fixed-name dialog,
    derives refs from the CHECKED sheets only (a partial checklist — Ch1
    excluded), runs the duplicate pre-checks with exclude_name = the record
    itself and sends the record's OWNING file as target_path."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub
    from PyQt6.QtWidgets import QDialog

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [
        {"name": "amp",
         "components": [{"ref": "R1", "offset_along_mm": 0.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)
        hub._selection_footprints = [SimpleNamespace(ref="C4")]

        seen = {}

        class _FakeDialog:
            def __init__(self, snapshot, selection_refs, parent,
                         fixed_name=None, *, adapter=None,
                         selected_footprints=None, pivot_initial=None,
                         selection_provider=None, snapshot_provider=None):
                seen["fixed_name"] = fixed_name
                seen["selection_provider"] = selection_provider
                seen["snapshot_provider"] = snapshot_provider
                # Commit F — Re-source pre-fills the Pivot/Anchor tab from the
                # stored record's pivot, so leaving it alone KEEPS the pivot.
                seen["pivot_initial"] = pivot_initial

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return True

            def pivot_value(self):
                # Commit F — leaving the pre-filled pivot tab untouched KEEPS
                # the stored pivot (the real dialog returns pivot_initial as-is).
                return tuple(seen.get("pivot_initial") or (0.0, 0.0))

            def preset_name_to_save(self):
                return None

            def result_data(self):
                # User checked Top + Ch0 + its nested Amp, but NOT Ch1.
                return ("amp", ("Top",),
                        [("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp")])

        payloads = []
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog", _FakeDialog)
        # start_long_op is imported lazily inside _run_resource_scheme_list —
        # patch the worker module, not dock_hub's namespace.
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        entry = {"name": "amp", "pivot": [2.5, -1.0],
                 "components": [{"ref": "R1"}]}
        hub.resource_scheme_list_record(entry, root)

        assert seen.get("fixed_name") == "amp"
        # Commit G — the Re-source dialog also gets the LIVE selection provider.
        assert seen.get("selection_provider") is not None
        # Commit H — and the LIVE full-board snapshot provider (connection.
        # snapshot, refreshed under the modal loop) for the recorded refs'
        # positions in "Take from selection".
        assert seen.get("snapshot_provider") is not None
        assert [s.ref for s in seen["snapshot_provider"]()] == [
            s.ref for s in _snap(*_HIER)]
        connection.snapshot = [SimpleNamespace(ref="Moved2")]
        assert [s.ref for s in seen["snapshot_provider"]()] == ["Moved2"]
        # Commit F — the Re-source dialog is pre-filled from the stored pivot...
        assert seen.get("pivot_initial") == [2.5, -1.0]
        assert len(payloads) == 1
        p = payloads[0]
        assert p["name"] == "amp"
        # Ch1's C3 is excluded — the capture set is the checked-sheet union.
        assert p["refs"] == ["C1", "C2", "R1", "U1"]
        assert "anchor_ref" not in p
        # ...and the untouched pivot is carried through to the payload (Re-source
        # re-points geometry, it does not reset the pivot to the centre).
        assert p["pivot"] == [2.5, -1.0]
        assert p["target_path"] == str(root)
        # 5c.1 — a "By sheet" Re-source stores the NEW checked paths as scope.
        assert p["scope_sheet_paths"] == [
            ["Top"], ["Top", "Ch0"], ["Top", "Ch0", "Amp"]]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


# ── Stage 5c: Reread with a changeable REF SET ──────────────────────────────
# plan_2026_09_06_scheme_list_sheet_capture.md 5c — Reread recomputes the
# CURRENT scope and adds/removes refs (no strict per-piece validation, design
# §4): "By sheet"-records recompute it from the stored scope_sheet_paths over
# the live snapshot (one click, no dialog); "By selection"-records take it from
# the CURRENT board selection (empty -> warning, no crash). Reread-Apply must
# preserve the stored scope_sheet_paths.

def _add_fp_to(adapter, ref, x_mm, y_mm=10.0, angle=0.0):
    """Append one footprint (pad only — no extra copper) to a _line_board
    adapter, so a capture can resolve it as a recorded/added component."""
    fp = _fp(ref, x_mm, y_mm, angle=angle)
    adapter._fps.append(fp)
    adapter._pads[ref] = [_pad(ref, x_mm, y_mm, _V5)]
    return fp


def _stored(components=None, scope_sheet_paths=None):
    """A stored-record dict for the 5c pure helpers (records carry no anchor
    field in the centre-frame format)."""
    d = {"name": "amp", "components": components or [{"ref": "R1"},
                                                     {"ref": "C1"}]}
    if scope_sheet_paths is not None:
        d["scope_sheet_paths"] = scope_sheet_paths
    return load_scheme_list(d)


def test_reread_scope_refs_by_sheet_recomputes_from_stored_paths():
    """5c.4 — the CURRENT scope of a "By sheet"-record is recomputed from the
    STORED scope_sheet_paths against the live snapshot (refs_on_sheet union) —
    no board selection is consulted."""
    snapshot = _snap(("R1", ("Channel_0",)), ("C1", ("Channel_0",)),
                     ("C2", ("Channel_0",)), ("X1", ("Other",)))
    stored = _stored(scope_sheet_paths=[["Channel_0"]])
    assert reread_scope_refs(stored, snapshot, ["X1"]) == ["C1", "C2", "R1"]
    # a narrowed checklist (only a sub-leaf) narrows the scope accordingly
    stored2 = _stored(components=[{"ref": "R1"}],
                      scope_sheet_paths=[["Channel_0", "Sub"]])
    assert reread_scope_refs(stored2, snapshot, []) == []


def test_reread_scope_refs_by_selection_uses_current_selection():
    """5c.4 — the CURRENT scope of a "By selection"-record (no scope_sheet_
    paths) IS the current board selection (possibly a CHANGED set); an empty
    selection yields an empty scope (the caller warns, does not diff silently)."""
    stored = _stored()
    assert reread_scope_refs(stored, [], ["C4", "R1"]) == ["C4", "R1"]
    assert reread_scope_refs(stored, [], []) == []


def test_reread_by_selection_without_selection_warns_not_crash(
        main_window, tmp_path, caplog):
    """5c.4 — a "By selection"-record Reread with NO current board selection is
    a warning (no crash, no silent fixed-set diff)."""
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)
    # no _select(...) — the dock's tracked selection is empty
    result = dock._do_reread()
    assert result == {}  # _collect_reread_payload aborted on the empty scope
    assert any("select footprints on the board first" in r.message
               for r in caplog.records)


def test_reread_by_selection_added_ref_lands_in_record_after_apply(
        main_window, tmp_path):
    """5c.4 Apply — a ref newly selected (present on the board, absent from the
    stored record) is components_added in the diff and really lands in the
    rewritten record's components after Apply (no per-item confirm dialog)."""
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _record_dict(adapter0)  # R1/C1/C2, "By selection"-style (no scope)
    root = _record_file(tmp_path, d0)
    dock = _make_dock(main_window, root, d0)
    adapter1 = _line_board(c2_x_mm=24.0)
    _add_fp_to(adapter1, "C9", 30.0)  # the board gained a NEW component
    _connect_board(dock, adapter1)
    _select(dock, "R1", "C1", "C2", "C9")  # C9 now in the current selection

    diff = dock._do_reread()["diff"]
    assert {c.ref for c in diff.components_added} == {"C9"}
    assert diff.refs_removed_from_scope == []
    assert diff.changed is True

    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result
    entry = _load(root)["scheme_lists"][0]
    assert "C9" in {c["ref"] for c in entry["components"]}


def test_reread_by_selection_removed_ref_falls_out_after_apply(
        main_window, tmp_path):
    """5c.4 Apply — a recorded ref that is present on the board but OUT of the
    current selection is refs_removed_from_scope and really falls OUT of the
    rewritten record's components after Apply (no per-item confirm dialog)."""
    adapter = _line_board(c2_x_mm=24.0)
    d = _record_dict(adapter)  # R1/C1/C2
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)
    _select(dock, "R1", "C1")  # C2 left the current selection

    diff = dock._do_reread()["diff"]
    assert diff.refs_removed_from_scope == ["C2"]
    assert {c.ref for c in diff.components_moved} == set()
    assert diff.changed is True

    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result
    entry = _load(root)["scheme_lists"][0]
    assert {c["ref"] for c in entry["components"]} == {"R1", "C1"}


def test_reread_by_sheet_recomputes_scope_without_selection_and_apply_keeps_it(
        main_window, tmp_path):
    """5c.4 — a "By sheet"-record Reread stays ONE-CLICK: the scope is
    recomputed from the STORED scope_sheet_paths against the live snapshot (no
    selection/dialog), so a NEW footprint that appeared on the recorded leaf is
    picked up as components_added — and Apply preserves the scope in the
    rewritten record (it must not silently degrade to a selection-scoped one)."""
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _record_dict(adapter0)  # R1/C1/C2, source_sheet Channel_0
    d0["scope_sheet_paths"] = [["Channel_0"]]
    root = _record_file(tmp_path, d0)
    dock = _make_dock(main_window, root, d0)

    adapter1 = _line_board(c2_x_mm=24.0)
    _add_fp_to(adapter1, "C9", 30.0)  # C9 now ALSO on Channel_0
    _connect_board(dock, adapter1)
    dock._connection.snapshot = _snap(("R1", ("Channel_0",)),
                                      ("C1", ("Channel_0",)),
                                      ("C2", ("Channel_0",)),
                                      ("C9", ("Channel_0",)))
    # NO _select(...) — the By-sheet scope comes from the stored paths.

    diff = dock._do_reread()["diff"]
    assert {c.ref for c in diff.components_added} == {"C9"}
    assert diff.refs_removed_from_scope == []
    assert diff.changed is True

    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result
    entry = _load(root)["scheme_lists"][0]
    assert entry["scope_sheet_paths"] == [["Channel_0"]]
    assert {c["ref"] for c in entry["components"]} >= {"C9"}


def test_reread_apply_keeps_placed_entity_resolvable(main_window, tmp_path):
    """5c round-trip gate (plan) — a record already Placed (Entity with
    scheme_list: "amp") still resolves through load_config + link_trees after a
    Reread-Apply that REMOVED a ref from the scope: the record is rewritten to
    the smaller set, but the Entity points at it by NAME, so the link survives."""
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _record_dict(adapter0)  # R1/C1/C2
    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [d0],
        "entities": [{"name": "E_AMP", "scheme_list": "amp"}],
        "trees": [{"name": "main", "anchor": {"origin": True},
                   "nodes": [{"ref": "E_AMP", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    })
    entry = _load(root)["scheme_lists"][0]
    dock = _make_dock(main_window, root, entry)
    _connect_board(dock, adapter0)
    _select(dock, "R1", "C1")  # C2 removed from the current scope

    diff = dock._do_reread()["diff"]
    assert diff.refs_removed_from_scope == ["C2"]
    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result

    # the record was rewritten to the smaller set...
    rec = {e["name"]: e for e in _load(root)["scheme_lists"]}["amp"]
    assert {c["ref"] for c in rec["components"]} == {"R1", "C1"}
    # ...and the already-placed Entity still resolves via link_trees.
    cfg, _ = load_config(str(root))
    linked = link_trees(cfg, cfg.trees)[0]

    def _walk(lnodes):
        for ln in lnodes:
            yield ln
            yield from _walk(ln.children)

    ln = next(ln for ln in _walk(linked.nodes) if ln.node.ref == "E_AMP")
    assert ln.record is not None
    assert ln.record.name == "E_AMP"


# ── Named presets (plan_2026_09_06_scheme_list_named_presets.md §6-§10) ─────
# The optional "Save as preset" Record/Re-source field + the record page's
# preset_combo + Reread over a selected preset (Apply makes it the NEW stored
# scope_sheet_paths; the scope_presets LIBRARY itself is never touched by
# Apply — only an explicit Record/Re-source "Save as preset" edits it).

def test_reread_scope_refs_active_preset_paths_override_stored():
    """§9 — an active_scope_paths argument OVERRIDES the stored scope for THIS
    Reread; None (the "(current)" sentinel / no presets) is byte-identical to
    5c (uses stored.scope_sheet_paths)."""
    snapshot = _snap(("R1", ("Channel_0",)), ("C1", ("Channel_0",)),
                     ("C2", ("Channel_0",)), ("X1", ("Other",)),
                     ("C9", ("Channel_0", "Sub")))
    stored = _stored(scope_sheet_paths=[["Channel_0"]])
    assert reread_scope_refs(stored, snapshot, ["X1"]) == ["C1", "C2", "R1"]
    # sentinel/None == 5c byte-identical.
    assert reread_scope_refs(stored, snapshot, ["X1"],
                             active_scope_paths=None) == ["C1", "C2", "R1"]
    # a preset narrowing the scope to a sub-leaf -> only that leaf's refs.
    assert reread_scope_refs(stored, snapshot, ["X1"],
                             active_scope_paths=[["Channel_0", "Sub"]]) == ["C9"]


def test_record_dialog_preset_name_to_save_is_by_sheet_only(main_window):
    """§6 — the optional "Save as preset" field reports its non-empty (stripped)
    text only on the "By sheet" tab; empty -> None; on "By selection" ALWAYS
    None regardless of the text still sitting in the tab-1 field."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window)
    try:
        assert dialog.preset_name_to_save() is None  # empty by default
        dialog.save_preset_edit.setText("  full  ")
        assert dialog.preset_name_to_save() == "full"
        dialog.tabs.setCurrentIndex(1)  # "By selection"
        assert not dialog.is_by_sheet()
        assert dialog.preset_name_to_save() is None
    finally:
        dialog.close()


def test_record_dialog_save_preset_field_is_optional_no_ok_gating(main_window):
    """§6 — the preset field is FULLY optional: it must not change the OK
    gating (5a regression guard)."""
    snapshot = _snap(("R1", ("Top",)), ("C1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    try:
        # Commit E: sheets start UNCHECKED — mark one so OK gating is on, then
        # prove the preset field neither enables nor disables it.
        top = _tree_item_by_path(dialog, ("Top",))
        top.setCheckState(0, Qt.CheckState.Checked)
        assert dialog.is_by_sheet() and dialog._ok_button.isEnabled()
        dialog.save_preset_edit.setText("anything")
        assert dialog._ok_button.isEnabled()
    finally:
        dialog.close()


def _preset_fake_dialog(preset_name, by_sheet=True, checked=None):
    """Build a RecordSchemeListDialog stand-in that reports a fixed "Save as
    preset" field value and (default) the full Top/Ch0/Amp checked checklist."""
    from PyQt6.QtWidgets import QDialog

    class _D:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def is_by_sheet(self):
            return by_sheet

        def pivot_value(self):
            return (0.0, 0.0)

        def preset_name_to_save(self):
            return preset_name

        def result_data(self):
            return ("amp", ("Top",),
                    checked or [("Top",), ("Top", "Ch0"),
                                ("Top", "Ch0", "Amp")])

    return _D


def test_record_scheme_list_by_sheet_save_preset_adds_first_preset(
        main_window, tmp_path, monkeypatch):
    """§7 (Record) — a non-empty "Save as preset" field makes the CURRENT
    checked checklist the brand-new record's FIRST named preset in the
    worker payload (start-from-[] semantics: a new record has no prior
    library)."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [],
                  "entities": [{"name": "PARENT", "cell": "c_parent"}],
                  "trees": [{"name": "main", "anchor": {"origin": True},
                             "nodes": [{"ref": "PARENT", "kind": "placement",
                                        "xy": [0.0, 0.0]}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)
        payloads = []
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                            _preset_fake_dialog("full"))
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.record_scheme_list()

        assert len(payloads) == 1
        assert payloads[0]["scope_presets"] == [
            {"name": "full", "sheet_paths": [["Top"], ["Top", "Ch0"],
                                             ["Top", "Ch0", "Amp"]]}]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


def test_resource_scheme_list_without_preset_save_keeps_existing_library(
        main_window, tmp_path, monkeypatch):
    """§7 (Re-source) — when the "Save as preset" field is EMPTY, the existing
    record's scope_presets library SURVIVES the Re-source untouched (Re-source
    changes the geometry source, it does not wipe saved checklist variants)."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [{"name": "amp",
                                    "components": [{"ref": "R1"}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)
        payloads = []
        entry = {"name": "amp", "components": [{"ref": "R1"}],
                 "scope_presets": [
                     {"name": "full",
                      "sheet_paths": [["Top"], ["Top", "Ch0"]]},
                     {"name": "ch0-only", "sheet_paths": [["Top", "Ch0"]]}]}
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                            _preset_fake_dialog(None))
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.resource_scheme_list_record(entry, root)

        assert len(payloads) == 1
        assert payloads[0]["scope_presets"] == [
            {"name": "full", "sheet_paths": [["Top"], ["Top", "Ch0"]]},
            {"name": "ch0-only", "sheet_paths": [["Top", "Ch0"]]},
        ]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


def test_resource_scheme_list_save_preset_overwrites_only_same_name(
        main_window, tmp_path, monkeypatch):
    """§7 (Re-source) — a "Save as preset" whose name matches an EXISTING
    preset overwrites ONLY that entry; the rest of the library stays intact."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [{"name": "amp",
                                    "components": [{"ref": "R1"}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)
        payloads = []
        entry = {"name": "amp", "components": [{"ref": "R1"}],
                 "scope_presets": [
                     {"name": "full",
                      "sheet_paths": [["Top"], ["Top", "Ch0"]]},
                     {"name": "ch0-only", "sheet_paths": [["Top", "Ch0"]]}]}
        # User re-saves "full" from a NARROWER current checklist (only Ch0).
        checked = [("Top",), ("Top", "Ch0")]
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                            _preset_fake_dialog("full", checked=checked))
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.resource_scheme_list_record(entry, root)

        assert len(payloads) == 1
        # ch0-only is untouched; "full" is REPLACED by the current checklist.
        assert payloads[0]["scope_presets"] == [
            {"name": "ch0-only", "sheet_paths": [["Top", "Ch0"]]},
            {"name": "full", "sheet_paths": [["Top"], ["Top", "Ch0"]]},
        ]
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()


def test_run_record_capture_carries_scope_presets_into_record():
    """§7 — the record worker converts the payload's scope_presets dict list
    into SchemeListScopePreset records (never interprets their content)."""
    from gui.dock_hub import DockHub

    hub = DockHub.__new__(DockHub)  # worker method — no __init__ side effects
    adapter = _line_board()
    payload = {"name": "amp", "refs": ["R1", "C1", "C2"],
               "board": SimpleNamespace(adapter=adapter), "root": ".",
               "scope_presets": [{"name": "full",
                                  "sheet_paths": [["Channel_0"],
                                                  ["Channel_0", "Sub"]]}]}
    record = hub._run_record_capture(payload)["record"]
    assert [(p.name, p.sheet_paths) for p in record.scope_presets] == [
        ("full", [["Channel_0"], ["Channel_0", "Sub"]])]


def _by_sheet_record_dict_with_presets(adapter):
    """A "By sheet" record dict (stored scope = [Channel_0]) carrying a two-
    entry named-presets library (full = stored + the Sub leaf, ch0-only)."""
    d = _record_dict(adapter)
    d["scope_sheet_paths"] = [["Channel_0"]]
    d["scope_presets"] = [
        {"name": "full", "sheet_paths": [["Channel_0"], ["Channel_0", "Sub"]]},
        {"name": "ch0-only", "sheet_paths": [["Channel_0"]]},
    ]
    return d


def test_record_page_preset_combo_hidden_without_presets(main_window, tmp_path):
    """§8 — a record with NO scope_presets shows NO preset combo (nothing to
    switch between); the page is 5c-identical."""
    adapter = _line_board()
    d0 = _record_dict(adapter)
    root = _record_file(tmp_path, d0)
    dock = _make_dock(main_window, root, d0)
    assert not dock.preset_combo.isVisibleTo(dock)
    assert dock.preset_combo.count() == 0


def test_record_page_preset_combo_shows_sentinel_then_presets(main_window, tmp_path):
    """§8 — a record WITH scope_presets shows the combo: the "(current)"
    sentinel (data None = stored scope, the default) first, then one entry
    per preset carrying its sheet_paths as item data."""
    adapter = _line_board()
    d = _by_sheet_record_dict_with_presets(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.preset_combo.isVisibleTo(dock)
    assert dock.preset_combo.count() == 3  # sentinel + 2 presets
    assert dock.preset_combo.currentIndex() == 0  # sentinel by default
    assert dock.preset_combo.currentData() is None  # sentinel data
    assert dock.preset_combo.itemData(1) == [["Channel_0"], ["Channel_0", "Sub"]]
    assert dock.preset_combo.itemData(2) == [["Channel_0"]]


def test_record_page_clear_resets_preset_combo(main_window, tmp_path):
    """§8 — clear() blanks the preset selector too (no stale entries left from
    a previously loaded record)."""
    adapter = _line_board()
    d = _by_sheet_record_dict_with_presets(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.preset_combo.count() == 3
    dock.clear()
    assert dock.preset_combo.count() == 0
    assert not dock.preset_combo.isVisibleTo(dock)


def test_reread_preset_switch_changes_scope_and_apply_makes_it_current(
        main_window, tmp_path):
    """§8/§10 — with a NAMED preset selected, Reread's scope comes from THAT
    preset (not stored.scope_sheet_paths); an explicit Apply re-captures under
    the preset's paths AND makes them the NEW stored scope_sheet_paths, while
    the scope_presets LIBRARY stays fully intact. Round-trip canary: a placed
    Entity still resolves after the rewrite."""
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _by_sheet_record_dict_with_presets(adapter0)  # stored scope Channel_0
    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [d0],
        "entities": [{"name": "E_AMP", "scheme_list": "amp"}],
        "trees": [{"name": "main", "anchor": {"origin": True},
                   "nodes": [{"ref": "E_AMP", "kind": "placement",
                              "xy": [0.0, 0.0]}]}],
    })
    entry = _load(root)["scheme_lists"][0]
    dock = _make_dock(main_window, root, entry)

    adapter1 = _line_board(c2_x_mm=24.0)
    _add_fp_to(adapter1, "C9", 30.0)  # C9 sits on Channel_0's Sub leaf
    _connect_board(dock, adapter1)
    dock._connection.snapshot = _snap(("R1", ("Channel_0",)),
                                      ("C1", ("Channel_0",)),
                                      ("C2", ("Channel_0",)),
                                      ("C9", ("Channel_0", "Sub")))

    # Default (sentinel "(current)"): scope = stored [Channel_0] -> C9 (Sub
    # leaf) is NOT part of the scope yet (5c byte-identical regression).
    diff_sentinel = dock._do_reread()["diff"]
    assert diff_sentinel.changed is False

    # Switch to the "full" preset (stored paths + the Sub leaf) -> C9 is now
    # in the CURRENT scope for THIS Reread.
    dock.preset_combo.setCurrentText("full")
    diff = dock._do_reread()["diff"]
    assert {c.ref for c in diff.components_added} == {"C9"}
    assert diff.changed is True

    apply_result = dock._do_reread_apply()
    assert "error" not in apply_result, apply_result
    rec = {e["name"]: e for e in _load(root)["scheme_lists"]}["amp"]
    # Apply made the PRESET's paths the new stored scope...
    assert rec["scope_sheet_paths"] == [["Channel_0"], ["Channel_0", "Sub"]]
    # ...C9 landed in the record...
    assert "C9" in {c["ref"] for c in rec["components"]}
    # ...and the preset LIBRARY is fully intact (untouched by Apply).
    assert rec["scope_presets"] == [
        {"name": "full", "sheet_paths": [["Channel_0"], ["Channel_0", "Sub"]]},
        {"name": "ch0-only", "sheet_paths": [["Channel_0"]]},
    ]
    # Round-trip canary: the placed Entity still resolves via link_trees.
    cfg, _ = load_config(str(root))
    linked = link_trees(cfg, cfg.trees)[0]

    def _walk(lnodes):
        for ln in lnodes:
            yield ln
            yield from _walk(ln.children)

    ln = next(ln for ln in _walk(linked.nodes) if ln.node.ref == "E_AMP")
    assert ln.record is not None
    assert ln.record.name == "E_AMP"


# ── Commit F — Pivot/Anchor tab in the Record/Re-source dialog ─────────────
# plan_2026_09_07_scheme_list_commit_f_pivot_tab_in_record.md: the pivot is
# chosen AT CREATION — the dialog's THIRD tab (x/y mm in the record's centre-
# frame, default (0,0) = centre, "Centre", "Take from selection"); the dialog
# OK (Record/Re-source) stores those fields as the record's pivot (there is no
# separate Apply in the dialog). The saved-record page keeps the same block on
# its own "Pivot / Anchor" tab next to the read-only "Record summary" tab.

def test_record_dialog_pivot_tab_prefills_pivot_initial(main_window):
    """Commit F — Record: no pivot_initial -> the (0,0) centre default; a
    supplied initial (Re-source prefill) lands in the x/y fields verbatim."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window)
    try:
        assert dialog.pivot_value() == (0.0, 0.0)
        assert dialog.pivot_centre_button.isEnabled()
    finally:
        dialog.close()
    prefilled = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window,
                                       pivot_initial=(3.5, -2.0))
    try:
        assert prefilled.pivot_value() == (3.5, -2.0)
    finally:
        prefilled.close()


def test_record_dialog_pivot_centre_button_writes_0_0(main_window):
    """Commit F — 'Centre' writes the (0,0) centre default into the fields."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window,
                                    pivot_initial=(3.5, -2.0))
    try:
        dialog.pivot_x_edit.setText("9")
        dialog.pivot_y_edit.setText("8")
        dialog.pivot_centre_button.click()
        assert dialog.pivot_value() == (0.0, 0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_take_from_selection_fills_using_selection(
        main_window):
    """Commit F — 'Take from selection' reads the CURRENT board selection and
    fills x/y as the pivot in the centre-frame of the refs the dialog would
    record (the live adapter + selected footprints are the dialog's new
    optional context). By-selection mode: refs = the selection R1/C1/C2 whose
    live centre (17,10); live selection = R1 centre (10,10) -> pivot (-7,0).
    The recorded refs' positions come from the dialog's footprint-bearing
    SNAPSHOT (_snap_live), not from the adapter (Commit H)."""
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    sel = _selection_from(fps["R1"])  # live board selection = R1 only
    dialog = RecordSchemeListDialog(_snap_live(adapter), ["R1", "C1", "C2"],
                                    main_window, adapter=adapter,
                                    selected_footprints=sel)
    try:
        dialog.tabs.setCurrentIndex(1)  # By selection — refs = the selection
        dialog.pivot_from_selection_button.click()
        x, y = dialog.pivot_value()
        assert x == pytest.approx(-7.0)
        assert y == pytest.approx(0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_take_from_selection_no_adapter_warns(
        main_window, monkeypatch):
    """Commit F — without a live adapter the handler warns and leaves the
    (0,0) default untouched (it never guesses a pivot)."""
    import gui.docks.scheme_list as sl_mod
    warns = []
    monkeypatch.setattr(sl_mod.QMessageBox, "warning",
                        lambda parent, title, text: warns.append(text))
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window)
    try:
        assert not dialog.pivot_from_selection_button.isEnabled()  # no adapter
        dialog._on_pivot_from_selection()
        assert warns and "Connect to KiCad first." in warns[0]
        assert dialog.pivot_value() == (0.0, 0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_invalid_numbers_disable_ok(main_window):
    """Commit F — a malformed Pivot/Anchor tab gates OK OFF even when a sheet
    is checked (a bad pivot must never reach the record) and pivot_value()
    raises ValidationError for it."""
    snapshot = _snap(("R1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    try:
        _tree_item_by_path(dialog, ("Top",)).setCheckState(
            0, Qt.CheckState.Checked)
        assert dialog._ok_button.isEnabled()
        dialog.pivot_x_edit.setText("abc")  # not a number
        assert not dialog._ok_button.isEnabled()
        with pytest.raises(ValidationError):
            dialog.pivot_value()
        dialog.pivot_x_edit.setText("1.5")
        assert dialog._ok_button.isEnabled()
    finally:
        dialog.close()


def test_resource_dialog_pivot_tab_prefills_stored_pivot(main_window):
    """Commit F — Re-source pre-fills the Pivot/Anchor tab from the stored
    record's pivot, so an untouched dialog KEEPS the pivot on re-source."""
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window,
                                    fixed_name="amp",
                                    pivot_initial=[2.0, 1.5])
    try:
        assert dialog.pivot_value() == (2.0, 1.5)
    finally:
        dialog.close()


def test_record_page_has_record_summary_and_pivot_anchor_tabs(
        main_window, tmp_path):
    """Commit F — the saved-record page is a TWO-tab page: the read-only
    "Record summary" tab (source/preset/geometry/Reread) and the "Pivot /
    Anchor" tab (the editable pivot block from Commit B1/B2)."""
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.page_tabs.count() == 2
    assert dock.page_tabs.tabText(0) == "Record summary"
    assert dock.page_tabs.tabText(1) == "Pivot / Anchor"
    # the summary tab still renders the loaded record
    dock.page_tabs.setCurrentIndex(0)
    assert dock.source_sheet_label.text() == "Channel_0"
    assert dock.pivot_x_edit.text() == "0.00"


def test_record_page_pivot_tab_apply_still_saves(main_window, tmp_path):
    """Commit F — the pivot editor moved onto its own tab but 'Apply' (Save
    pivot) still rewrites the record's owning file from that tab."""
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    dock.page_tabs.setCurrentIndex(1)  # the Pivot / Anchor tab
    dock.pivot_x_edit.setText("1.25")
    dock.pivot_y_edit.setText("-0.5")
    dock.pivot_apply_button.click()
    entry = _load(root)["scheme_lists"][0]
    assert entry["pivot"] == [1.25, -0.5]
    assert load_scheme_list(entry).pivot == (1.25, -0.5)


# ── Commit G — "Take from selection" reads the LIVE selection at click time ─
# plan_2026_09_07_scheme_list_commit_g_live_selection_pivot.md: the Record/
# Re-source dialog used the open-time selection snapshot, so a component
# selected on the board while the dialog is open was invisible (Denis repro:
# checked Channel_0, selected IC2 on the board -> pivot stayed 0,0). The
# dialog now receives a selection_provider (a live view over the hub's polled
# selection) and reads it at click time.

def test_record_dialog_pivot_take_from_selection_reads_live_selection(
        main_window):
    """Commit G — select a component on the board AFTER the dialog is open (the
    provider's backing list changes) and 'Take from selection' honours it:
    checked Top subtree defines the recorded region, then selecting R1 live
    fills pivot = R1(10,10) - region centre (17,10) = (-7, 0) instead of 0,0."""
    adapter = _line_board()  # R1(10,10) C1(20,10) C2(24,10) -> centre (17,10)
    fps = _fps_by_ref(adapter)
    live: list = []  # nothing is selected when the dialog opens
    dialog = RecordSchemeListDialog(_snap_live(adapter), [], main_window,
                                    adapter=adapter,
                                    selection_provider=lambda: list(live))
    try:
        # By-sheet (default tab): tick a sheet so the region is defined.
        _tree_item_by_path(dialog, ("Top",)).setCheckState(
            0, Qt.CheckState.Checked)
        # ...then the user selects R1 on the board WHILE the dialog is open.
        live[:] = _selection_from(fps["R1"])
        dialog.pivot_from_selection_button.click()
        x, y = dialog.pivot_value()
        assert x == pytest.approx(-7.0)
        assert y == pytest.approx(0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_take_from_selection_reads_live_snapshot(
        main_window):
    """Commit H — the dialog's recorded-ref positions also come from a LIVE
    full-board snapshot (snapshot_provider), not the open-time copy: moving C2
    on the board AFTER the dialog is open shifts the region centre, and the
    second click honours the NEW position (R1/C1/C2 at (10,20,34) -> centre 22
    -> pivot 10 - 22 = -12 instead of -7). Reading self._snapshot (the static
    constructor copy) would keep returning -7, so the assert distinguishes the
    live source from a stale one."""
    adapter = _line_board()  # R1(10,10) C1(20,10) C2(24,10) -> centre (17,10)
    fps = _fps_by_ref(adapter)
    static = _snap_live(adapter)  # the board as it was when the dialog opened
    live_snap = list(static)      # connection.snapshot — mutated by the test
    dialog = RecordSchemeListDialog(static, ["R1", "C1", "C2"], main_window,
                                    adapter=adapter,
                                    selected_footprints=_selection_from(
                                        fps["R1"]),
                                    selection_provider=lambda: list(
                                        _selection_from(fps["R1"])),
                                    snapshot_provider=lambda: list(live_snap))
    try:
        dialog.tabs.setCurrentIndex(1)  # By selection — refs = R1/C1/C2
        dialog.pivot_from_selection_button.click()
        x0, _y0 = dialog.pivot_value()
        assert x0 == pytest.approx(-7.0)
        # ...then C2 is MOVED to x=34 while the dialog is open (a fresh board
        # whose rows replace the live snapshot's; the static copy keeps C2@24).
        moved = _line_board(c2_x_mm=34.0)
        by_ref = {f.ref: f for f in moved.get_footprints()}
        live_snap[:] = [SimpleNamespace(ref=s.ref, sheet=list(s.sheet),
                                        fp=by_ref.get(s.ref))
                        for s in live_snap]
        dialog.pivot_from_selection_button.click()
        x1, y1 = dialog.pivot_value()
        assert x1 == pytest.approx(-12.0)  # 10 - midpoint(10, 34)
        assert y1 == pytest.approx(0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_take_from_selection_falls_back_to_snapshot(
        main_window):
    """Commit G + H regression guard — without selection_provider /
    snapshot_provider the dialog keeps using the OPEN-TIME static copies (the
    constructor `snapshot` for positions, the selected_footprints for the
    selection): tests/Re-source callers that pass the static list still behave
    as before."""
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    dialog = RecordSchemeListDialog(_snap_live(adapter), ["R1", "C1", "C2"],
                                    main_window, adapter=adapter,
                                    selected_footprints=_selection_from(
                                        fps["R1"]))
    try:
        dialog.tabs.setCurrentIndex(1)  # By selection — refs = the selection
        dialog.pivot_from_selection_button.click()
        x, y = dialog.pivot_value()
        assert x == pytest.approx(-7.0)  # R1 - region centre (R1/C1/C2)
        assert y == pytest.approx(0.0)
    finally:
        dialog.close()


# ── 2026-09-08 — the Pivot/Anchor tab must not flip is_by_sheet() ────────────
# plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md: is_by_sheet()
# used to answer literally "the current tab is index 0", so standing on the
# THIRD tab (Pivot/Anchor, index 2) — which is NOT a source — made the whole
# dialog silently behave as "By selection". The source is now a tracked state
# that only a 0<->1 tab switch changes; these tests reproduce Denis' repro and
# its dock_hub consequence by REALLY switching to tab 2 before acting.

def test_record_dialog_pivot_tab_keeps_by_sheet_source_take_from_selection_fills(
        main_window, monkeypatch):
    """§0.1 repro — checked a sheet on "By sheet", moved to the Pivot/Anchor
    tab (index 2) and clicked 'Take from selection': with the tab-based source
    the dialog read the (here empty) open-time selection, gated on an empty set
    and warned 'No footprints to record' with x/y staying 0,0. The tracked
    source keeps the By-sheet checklist authoritative on tab 2, so the pivot is
    filled from the checked sheet's region and no warning fires."""
    import gui.docks.scheme_list as sl_mod
    warns = []
    monkeypatch.setattr(sl_mod.QMessageBox, "warning",
                        lambda parent, title, text: warns.append(text))
    adapter = _line_board()  # R1(10,10) C1(20,10) C2(24,10) -> centre (17,10)
    fps = _fps_by_ref(adapter)
    dialog = RecordSchemeListDialog(_snap_live(adapter), [], main_window,
                                    adapter=adapter,
                                    selection_provider=lambda: list(
                                        _selection_from(fps["R1"])))
    try:
        # "By sheet" (the default tab): tick the whole Top subtree — the region.
        _tree_item_by_path(dialog, ("Top",)).setCheckState(
            0, Qt.CheckState.Checked)
        assert dialog.is_by_sheet()
        assert dialog._checked_refs() == ["C1", "C2", "C3", "R1", "U1"]
        assert dialog._ok_button.isEnabled()
        # The user then opens the Pivot/Anchor tab (natural flow: fill the
        # pivot last) and clicks 'Take from selection'.
        dialog.tabs.setCurrentIndex(2)
        assert dialog.tabs.currentIndex() == 2
        assert dialog.is_by_sheet()            # tracked, not the open tab
        assert dialog._checked_refs() == ["C1", "C2", "C3", "R1", "U1"]
        assert dialog._ok_button.isEnabled()   # OK not gated off by the visit
        dialog.pivot_from_selection_button.click()
        assert not warns                       # no 'No footprints to record'
        x, y = dialog.pivot_value()
        assert x == pytest.approx(-7.0)        # R1(10,10) - centre (17,10)
        assert y == pytest.approx(0.0)
    finally:
        dialog.close()


def test_record_dialog_pivot_tab_keeps_by_selection_source(main_window):
    """§1.2 symmetric — after choosing "By selection" (tab 1) the visit to the
    Pivot/Anchor tab (index 2) must NOT revert the source to "By sheet":
    is_by_sheet() stays False, the capturable refs stay the selection, and
    result_data() keeps the no-paths selection contract."""
    adapter = _line_board()
    fps = _fps_by_ref(adapter)
    dialog = RecordSchemeListDialog(_snap_live(adapter), ["R1", "C1", "C2"],
                                    main_window, adapter=adapter,
                                    selected_footprints=_selection_from(
                                        fps["R1"]))
    try:
        dialog.tabs.setCurrentIndex(1)  # "By selection" — refs = the selection
        assert not dialog.is_by_sheet()
        assert dialog._checked_refs() == ["C1", "C2", "R1"]
        dialog.tabs.setCurrentIndex(2)  # Pivot/Anchor — NOT a source
        assert dialog.tabs.currentIndex() == 2
        assert not dialog.is_by_sheet()             # source unchanged
        assert dialog._checked_refs() == ["C1", "C2", "R1"]
        assert dialog.result_data() == ("", None, None)  # no sheet paths
        dialog.pivot_from_selection_button.click()
        x, y = dialog.pivot_value()
        assert x == pytest.approx(-7.0)  # R1 - centre of the selection's region
        assert y == pytest.approx(0.0)
    finally:
        dialog.close()


def test_record_dialog_ok_stays_enabled_after_pivot_tab_visit_with_by_sheet_source(
        main_window):
    """§0.2 OK gate — with a non-empty "By sheet" checklist, moving to the
    Pivot/Anchor tab (index 2) must NOT disable OK (the old code re-derived
    _checked_refs() as the empty selection on tab 2 and switched OK off)."""
    dialog = RecordSchemeListDialog(_snap(("R1", ("Top",))), [], main_window)
    try:
        _tree_item_by_path(dialog, ("Top",)).setCheckState(
            0, Qt.CheckState.Checked)
        assert dialog._ok_button.isEnabled()
        dialog.tabs.setCurrentIndex(2)  # Pivot/Anchor
        assert dialog.tabs.currentIndex() == 2
        assert dialog.is_by_sheet()     # source still "By sheet"
        assert dialog._ok_button.isEnabled()
    finally:
        dialog.close()


def test_record_scheme_list_ok_from_pivot_tab_uses_by_sheet_source(
        main_window, tmp_path, monkeypatch):
    """§0.3 — the most serious consequence (silent corruption): pressing OK
    DIRECTLY from the Pivot/Anchor tab (the natural flow — fill the pivot last,
    then OK). dock_hub's record_scheme_list() reads result_data()/is_by_sheet()
    only AFTER exec() returns; with the tab-based source a dialog left on tab 2
    would report "By selection", derive refs from the (here empty) board
    selection and abort with 'No footprints to record' instead of recording the
    checked "By sheet" region. Uses the REAL dialog (exec only switches to the
    Pivot tab before returning Accepted) so the tracked source is exercised."""
    import logging

    import gui.dock_hub as dock_hub_mod
    from gui.dock_hub import DockHub
    from PyQt6.QtWidgets import QDialog

    root = tmp_path / "root.sexp"
    _write(root, {"scheme_lists": [],
                  "entities": [{"name": "PARENT", "cell": "c_parent"}],
                  "trees": [{"name": "main", "anchor": {"origin": True},
                             "nodes": [{"ref": "PARENT", "kind": "placement",
                                        "xy": [0.0, 0.0]}]}]})
    connection = main_window.connection
    hub = DockHub(main_window, connection=connection, verbose=False)
    try:
        connection.snapshot = _snap(*_HIER)
        connection.board = SimpleNamespace(adapter=FakeAdapter([], [], [], {}))
        hub.root_metadata_dock.set_root_file(root)

        real_cls = dock_hub_mod.RecordSchemeListDialog

        class _DialogOkFromPivot(real_cls):
            """The real dialog; exec() emulates the user typing a unique name,
            ticking the whole Top subtree, then pressing OK straight from the
            Pivot/Anchor tab (index 2)."""

            def exec(self):
                self.name_edit.setText("amp")
                top = _tree_item_by_path(self, ("Top",))
                assert top is not None
                top.setCheckState(0, Qt.CheckState.Checked)
                self.tabs.setCurrentIndex(2)  # Pivot/Anchor — the ACTIVE tab
                return QDialog.DialogCode.Accepted

        payloads = []
        warns = []
        # A regression (source lost on the Pivot tab -> empty refs) would make
        # dock_hub pop a REAL modal "No footprints to record" warning — record
        # it instead so the test fails fast rather than hangs headless.
        monkeypatch.setattr(dock_hub_mod.QMessageBox, "warning",
                            lambda parent, title, text: warns.append(text))
        monkeypatch.setattr(dock_hub_mod, "RecordSchemeListDialog",
                            _DialogOkFromPivot)
        # record_scheme_list imports start_long_op lazily (`from .worker import
        # start_long_op`) — patch the worker module, not dock_hub's namespace.
        import gui.worker as worker_mod
        monkeypatch.setattr(
            worker_mod, "start_long_op",
            lambda _c, _w, worker, on_success, on_error, payload:
                payloads.append(payload) or object())

        hub.record_scheme_list()

        assert len(payloads) == 1
        # refs = the CHECKED Top subtree union — NOT the empty board selection;
        # staying on the Pivot tab at OK must not change the recorded source.
        assert payloads[0]["refs"] == ["C1", "C2", "C3", "R1", "U1"]
        assert payloads[0]["scope_sheet_paths"] == [
            ["Top"], ["Top", "Ch0"], ["Top", "Ch0", "Amp"], ["Top", "Ch1"]]
        assert not warns  # no "No footprints to record" — the source was kept
    finally:
        hub.log_dock.remove_handler()
        if hub._log_file_handler is not None:
            logging.getLogger().removeHandler(hub._log_file_handler)
            hub._log_file_handler.close()
