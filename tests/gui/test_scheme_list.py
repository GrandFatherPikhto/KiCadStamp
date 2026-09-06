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
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QTabWidget

from gui.docks.config_tree import ConfigTreeDock
from gui.docks.scheme_list import (
    RecordSchemeListDialog,
    SchemeListDiffDialog,
    SchemeListFormWidget,
    default_scheme_list_path,
    live_sheet_paths,
    read_scheme_list_records,
    record_refs_for,
    refs_on_sheet,
    reread_scope_refs,
    scheme_list_duplicate_problems,
    scheme_list_to_dict,
    sheet_paths_under,
    write_scheme_list_record,
)
from kicadstamp.config import load_config, load_scheme_list
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import Footprint, Pad, Track, Via
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
    sheet_names = _stamp_sheet(adapter)
    record = capture_scheme_list(name, ["R1", "C1", "C2"], anchor_ref,
                                 adapter=adapter, sheet_names=sheet_names)
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


def _select(dock, *refs) -> None:
    """Emulate the polled live board selection (DockHub.set_board_selection,
    5c.4) — the Reread scope of a "By selection"-record is the CURRENT
    selection at click time, so the tests feed the recorded refs (or a
    changed set) as the selection before _do_reread/_do_reread_apply."""
    dock.set_board_selection([], [SimpleNamespace(ref=r) for r in refs])


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
    # only the PRESENT refs can be re-selected (5c.4) — C2 stays a refs_not_found
    _select(dock, "R1", "C1")

    result = dock._do_reread()
    diff = result["diff"]
    assert diff.refs_not_found == ["C2"]
    # a physically-absent recorded ref is NEVER double-counted as removed-from-scope
    assert diff.refs_removed_from_scope == []
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

def test_record_dialog_by_selection_collects_name_and_anchor(main_window):
    """The secondary "By selection" tab keeps the pre-existing P2 behavior:
    result_data returns (name, anchor_ref, None, None), the anchor picked from
    the caller's OWN selection refs (Stage 5a.3 regression guard)."""
    snapshot = [SimpleNamespace(ref="R1", sheet=["Channel_0"]),
                SimpleNamespace(ref="C1", sheet=["Channel_0"]),
                SimpleNamespace(ref="C2", sheet=["Channel_0"])]
    dialog = RecordSchemeListDialog(snapshot, ["R1", "C1", "C2"], main_window)
    assert isinstance(dialog.tabs, QTabWidget)
    assert dialog.tabs.tabText(0) == "By sheet"
    assert dialog.is_by_sheet()  # "By sheet" is the first/default tab
    dialog.tabs.setCurrentIndex(1)  # -> "By selection"
    dialog.name_edit.setText("psu_front")
    dialog.selection_anchor_combo.setCurrentText("C1")
    assert dialog.result_data() == ("psu_front", "C1", None, None)
    assert not dialog.is_by_sheet()


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


# ── 5a.3 — RecordSchemeListDialog (two tabs) ───────────────────────────────

def test_record_dialog_two_tabs_with_by_sheet_default(main_window):
    dialog = RecordSchemeListDialog(_snap(*_HIER), ["C4"], main_window)
    assert dialog.tabs.count() == 2
    assert dialog.tabs.tabText(0) == "By sheet"
    assert dialog.tabs.tabText(1) == "By selection"
    assert dialog.is_by_sheet()


def test_record_dialog_by_sheet_leaf_hides_checklist_and_uses_root_refs(main_window):
    snapshot = _snap(("R1", ("Top",)), ("C1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    assert dialog.sheet_combo.count() == 1
    assert not dialog._root_has_subsheets   # leaf -> nothing to prune
    assert dialog._checked_sheet_paths() == [("Top",)]
    refs = {dialog.sheet_anchor_combo.itemText(i)
            for i in range(dialog.sheet_anchor_combo.count())}
    assert refs == {"R1", "C1"}
    assert dialog._ok_button.isEnabled()


def test_record_dialog_by_sheet_all_checked_offers_every_subtree_ref(main_window):
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    # default root is the first sorted sheet ("Other",) — pick "Top" explicitly
    dialog.sheet_combo.setCurrentText("Top")
    assert dialog._root_has_subsheets
    checked = dialog._checked_sheet_paths()
    assert checked == [("Top",), ("Top", "Ch0"), ("Top", "Ch0", "Amp"),
                       ("Top", "Ch1")]
    refs = {dialog.sheet_anchor_combo.itemText(i)
            for i in range(dialog.sheet_anchor_combo.count())}
    assert refs == {"R1", "C1", "C2", "C3", "U1"}
    assert dialog._ok_button.isEnabled()


def test_record_dialog_by_sheet_unchecking_a_sub_sheet_drops_its_refs(main_window):
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    dialog.sheet_combo.setCurrentText("Top")
    items = [dialog.sheet_checklist.item(i)
             for i in range(dialog.sheet_checklist.count())]
    ch0 = next(it for it in items
               if it.data(Qt.ItemDataRole.UserRole) == ("Top", "Ch0"))
    ch0.setCheckState(Qt.CheckState.Unchecked)
    refs = {dialog.sheet_anchor_combo.itemText(i)
            for i in range(dialog.sheet_anchor_combo.count())}
    assert refs == {"R1", "C3", "U1"}          # Ch0's C1/C2 gone
    assert ("Top", "Ch0") not in dialog._checked_sheet_paths()


def test_record_dialog_by_sheet_unchecking_everything_disables_ok(main_window):
    snapshot = _snap(*_HIER)
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    dialog.sheet_combo.setCurrentText("Top")
    for i in range(dialog.sheet_checklist.count()):
        dialog.sheet_checklist.item(i).setCheckState(Qt.CheckState.Unchecked)
    assert dialog._checked_sheet_paths() == []
    assert dialog.sheet_anchor_combo.count() == 0
    assert not dialog._ok_button.isEnabled()


def test_record_dialog_ok_gated_per_active_tab(main_window):
    # By sheet has R1; the "By selection" selection is empty.
    snapshot = _snap(("R1", ("Top",)))
    dialog = RecordSchemeListDialog(snapshot, [], main_window)
    assert dialog.is_by_sheet() and dialog._ok_button.isEnabled()
    dialog.tabs.setCurrentIndex(1)  # By selection, no selection -> disabled
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
    payload = {"name": "amp", "refs": ["R1", "C1"], "anchor_ref": "R1",
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

        class _FakeDialog:
            def __init__(self, snapshot, selection_refs, parent):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return True

            def result_data(self):
                # User checked Top + Ch0 + its nested Amp, but NOT Ch1.
                return ("amp", "R1", ("Top",), [("Top",), ("Top", "Ch0"),
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

        assert len(payloads) == 1
        # Ch1's C3 is excluded; R1/C1/C2/U1 are the checked-sheet union.
        assert payloads[0]["refs"] == ["C1", "C2", "R1", "U1"]
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
            def __init__(self, snapshot, selection_refs, parent):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return False

            def result_data(self):
                return ("amp", "C1", None, None)

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
        {"name": "amp", "anchor_ref": "R1",
         "components": [{"ref": "R1", "offset_along_mm": 0.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0},
                        {"ref": "C1", "offset_along_mm": 10.0,
                         "offset_across_mm": 0.0, "rotation_deg": 0.0}]},
        {"name": "psu", "anchor_ref": "X1",
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
        assert dialog.tabs.count() == 2
        # result_data keeps the pinned name regardless of the active tab.
        assert dialog.result_data()[0] == "amp"
        dialog.tabs.setCurrentIndex(1)  # By selection
        dialog.selection_anchor_combo.setCurrentText("C4")
        assert dialog.result_data() == ("amp", "C4", None, None)
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
    """5b.3 worker — capture from the NEW source + write REPLACES the record
    under the same name IN THE FILE THAT OWNS IT; the other record in the file
    is untouched and an Entity already placed on the record still resolves
    (round-trip through load_config + link_trees)."""
    from gui.dock_hub import DockHub

    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [
            {"name": "amp", "anchor_ref": "R1", "source_sheet": "Channel_0",
             "anchor_rotation_deg": 0.0,
             "components": [
                 {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0},
                 {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0},
                 {"ref": "C2", "offset_along_mm": 14.0, "offset_across_mm": 0.0,
                  "rotation_deg": 0.0}]},
            {"name": "keep", "anchor_ref": "K1", "anchor_rotation_deg": 0.0,
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
    hub = DockHub.__new__(DockHub)  # worker method — no __init__ side effects
    payload = {"board": SimpleNamespace(adapter=new_adapter), "name": "amp",
               "refs": ["R5", "C5", "C6"], "anchor_ref": "C5",
               "root": str(root), "target_path": str(root),
               "sheet_names": names}
    result = hub._run_resource_capture(payload)

    assert "error" not in result, result
    assert Path(result["path"]) == root
    records = {e["name"]: e for e in _load(root)["scheme_lists"]}
    assert set(records) == {"amp", "keep"}
    amp = records["amp"]
    # NEW refs/anchor/source_sheet/geometry, from the NEW source.
    assert [c["ref"] for c in amp["components"]] == ["R5", "C5", "C6"]
    assert amp["anchor_ref"] == "C5"
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
        {"name": "amp", "anchor_ref": "R1",
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
                         fixed_name=None):
                seen["fixed_name"] = fixed_name

            def exec(self):
                return QDialog.DialogCode.Accepted

            def is_by_sheet(self):
                return True

            def result_data(self):
                # User checked Top + Ch0 + its nested Amp, but NOT Ch1.
                return ("amp", "R1", ("Top",),
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

        entry = {"name": "amp", "anchor_ref": "R1",
                 "components": [{"ref": "R1"}]}
        hub.resource_scheme_list_record(entry, root)

        assert seen.get("fixed_name") == "amp"
        assert len(payloads) == 1
        p = payloads[0]
        assert p["name"] == "amp"
        # Ch1's C3 is excluded — the capture set is the checked-sheet union.
        assert p["refs"] == ["C1", "C2", "R1", "U1"]
        assert p["anchor_ref"] == "R1"
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


def test_reread_scope_refs_by_sheet_recomputes_from_stored_paths():
    """5c.4 — the CURRENT scope of a "By sheet"-record is recomputed from the
    STORED scope_sheet_paths against the live snapshot (refs_on_sheet union) —
    no board selection is consulted."""
    snapshot = _snap(("R1", ("Channel_0",)), ("C1", ("Channel_0",)),
                     ("C2", ("Channel_0",)), ("X1", ("Other",)))
    stored = load_scheme_list({"name": "amp", "anchor_ref": "R1",
                               "scope_sheet_paths": [["Channel_0"]],
                               "components": [{"ref": "R1"}, {"ref": "C1"}]})
    assert reread_scope_refs(stored, snapshot, ["X1"]) == ["C1", "C2", "R1"]
    # a narrowed checklist (only a sub-leaf) narrows the scope accordingly
    stored2 = load_scheme_list({"name": "amp", "anchor_ref": "R1",
                                "scope_sheet_paths": [["Channel_0", "Sub"]],
                                "components": [{"ref": "R1"}]})
    assert reread_scope_refs(stored2, snapshot, []) == []


def test_reread_scope_refs_by_selection_uses_current_selection():
    """5c.4 — the CURRENT scope of a "By selection"-record (no scope_sheet_
    paths) IS the current board selection (possibly a CHANGED set); an empty
    selection yields an empty scope (the caller warns, does not diff silently)."""
    stored = load_scheme_list({"name": "amp", "anchor_ref": "R1",
                               "components": [{"ref": "R1"}, {"ref": "C1"}]})
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
