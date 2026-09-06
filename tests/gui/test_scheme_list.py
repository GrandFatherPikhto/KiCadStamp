# tests/gui/test_scheme_list.py
"""Scheme List Config-side GUI (plan_2026_09_05_scheme_list.md §5, P5) —
headless Qt + mock adapter, following the tests/gui patterns of
test_net_trace_dock.py / test_phase3_wiring.py:
  - SchemeListFormWidget: a scheme_lists record loads READ-ONLY (the Anchor
    block: component-ref combo + anchor_pad/anchor_rotation_deg/source_sheet
    readouts + geometry summary); nothing here ever applies to the board.
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
from PyQt6.QtWidgets import QPushButton

from gui.docks.config_tree import ConfigTreeDock
from gui.docks.scheme_list import (
    RecordSchemeListDialog,
    SchemeListDiffDialog,
    SchemeListFormWidget,
    default_scheme_list_path,
    read_scheme_list_records,
    scheme_list_duplicate_problems,
    scheme_list_to_dict,
    write_scheme_list_record,
)
from kicadstamp.config import load_scheme_list
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import Footprint, Pad, Track, Via
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
    No foreign component (no boundary nets)."""
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


def _record_dict(adapter, name="amp", anchor_ref="R1", c2_x_mm=24.0):
    record = capture_scheme_list(name, ["R1", "C1", "C2"], anchor_ref,
                                 adapter=adapter)
    # Normalise the anchor angle exactly as the caller expects (default 0).
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


# ── scheme_list_to_dict round-trip ─────────────────────────────────────────

def test_scheme_list_to_dict_round_trips_through_the_loader(main_window):
    adapter = _line_board()
    d = _record_dict(adapter)
    again = load_scheme_list(d)
    assert again.name == "amp"
    assert again.anchor_ref == "R1"
    assert [c.ref for c in again.components] == ["R1", "C1", "C2"]
    assert len(again.tracks) == 2
    assert len(again.vias) == 1
    # optional fields survive (source_sheet from the local-net prefix)
    assert again.source_sheet == "Channel_0"


# ── Load entry (Config-tree leaf click) ────────────────────────────────────

def test_load_entry_fills_anchor_block_read_only(main_window, tmp_path):
    adapter = _line_board(angle_anchor=45.0)
    d = _record_dict(adapter, anchor_ref="R1")
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)

    assert dock.name_label.text() == "Scheme List: amp"
    items = [dock.anchor_combo.itemText(i) for i in range(dock.anchor_combo.count())]
    assert items == ["R1", "C1", "C2"]
    assert dock.anchor_combo.currentText() == "R1"
    # the closed set is a view — the combo must not be editable
    assert not dock.anchor_combo.isEnabled()
    assert dock.anchor_pad_label.text() == "-"
    assert dock.source_sheet_label.text() == "Channel_0"
    assert "3 components" in dock.geometry_label.text()
    assert "2 tracks" in dock.geometry_label.text()
    assert "1 vias" in dock.geometry_label.text()


def test_load_entry_keeps_anchor_pad_and_recorded_anchor_rotation(main_window, tmp_path):
    adapter = _line_board(angle_anchor=90.0)
    # anchor_pad "1" + explicit anchor_rotation_deg (90) on the record
    d = _record_dict(adapter, anchor_ref="R1")
    d["anchor_pad"] = "1"
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    assert dock.anchor_pad_label.text() == "1"
    assert "90.0" in dock.anchor_rotation_label.text()


# ── Reread ─────────────────────────────────────────────────────────────────

def test_reread_identical_board_reports_no_changes(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    _connect_board(dock, adapter)

    result = dock._do_reread()
    assert "diff" in result
    diff = result["diff"]
    assert diff.changed is False
    assert diff.components_moved == []
    assert diff.vias_added == [] and diff.tracks_removed == []


def test_reread_reports_moved_component_and_apply_rewrites_record(main_window, tmp_path, caplog):
    adapter0 = _line_board(c2_x_mm=24.0)
    d0 = _record_dict(adapter0)
    root = _record_file(tmp_path, d0)
    dock = _make_dock(main_window, root, d0)
    # C2 moved +0.5 mm on the live board
    adapter1 = _line_board(c2_x_mm=24.5)
    _connect_board(dock, adapter1)

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
    assert comps["C2"]["offset_along_mm"] == pytest.approx(14.5)
    # the anchor sits at the origin — the .sexp writer omits default-valued
    # fields (0.0 == the loader default), so read via .get
    assert comps["R1"].get("offset_along_mm", 0.0) == pytest.approx(0.0)
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

    result = dock._do_reread()
    diff = result["diff"]
    assert diff.refs_not_found == ["C2"]
    assert diff.anchor_missing is False
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
    record = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter)

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
    record = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter)
    write_scheme_list_record(root, record)
    # re-capture same name from a moved board -> replace in place (still 1 record)
    adapter2 = _line_board(c2_x_mm=25.0)
    record2 = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter2)
    write_scheme_list_record(root, record2)
    data = read_scheme_list_records(root)
    assert len(data) == 1
    comps = {c["ref"]: c for c in data[0]["components"]}
    assert comps["C2"]["offset_along_mm"] == pytest.approx(15.0)


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
    assert captured[0]["anchor_ref"] == "R1"


# ── Dialogs (Record name/anchor; Reread diff with gated Apply) ─────────────

def test_record_dialog_collects_name_and_anchor(main_window):
    dialog = RecordSchemeListDialog(["R1", "C1", "C2"], main_window)
    dialog.name_edit.setText("psu_front")
    dialog.anchor_combo.setCurrentText("C1")
    assert dialog.result_data() == ("psu_front", "C1")


def test_diff_dialog_gates_apply_when_a_ref_is_missing(main_window, tmp_path):
    adapter = _line_board()
    d = _record_dict(adapter)
    root = _record_file(tmp_path, d)
    dock = _make_dock(main_window, root, d)
    # C2 moved on the live board -> clean diff, Apply allowed
    adapter1 = _line_board(c2_x_mm=24.5)
    _connect_board(dock, adapter1)
    clean_diff = dock._do_reread()["diff"]
    dialog = SchemeListDiffDialog("amp", clean_diff, main_window)
    apply_btn = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Apply")
    assert apply_btn.isEnabled()
    dialog.close()
    # a missing ref -> the same dialog shows the problem and disables Apply
    adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]
    _connect_board(dock, adapter)
    missing_diff = dock._do_reread()["diff"]
    assert missing_diff.refs_not_found == ["C2"]
    dialog2 = SchemeListDiffDialog("amp", missing_diff, main_window)
    apply_btn2 = next(b for b in dialog2.findChildren(QPushButton)
                      if b.text() == "Apply")
    assert not apply_btn2.isEnabled()
    dialog2.close()


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
