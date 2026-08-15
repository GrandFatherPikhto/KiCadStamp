# tests/gui/test_points_dock.py
"""
PointsDock tests are deliberately headless AND board-mutation-free — same
reasoning as tests/gui/test_placer_dock.py/test_thermal_via_dock.py.
Resolve's own live-board math is covered by tests/test_point_resolver.py
(resolve_point_chain itself); here resolve_point_chain is monkeypatched so
these tests only check what PointsDock builds/passes/shows around it.
"""
from types import SimpleNamespace

import pytest
import yaml

import gui.docks.points as points_mod
from gui.docks.points import PointsDock
from kicadstamp.config import Point, load_point
from kicadstamp.exceptions import ValidationError


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _read_yaml(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, data if data is not None else {"points": {}})
    dock = PointsDock(main_window)
    dock.set_target_file(target_file)
    return dock, target_file


# ── Building the entry dict ──────────────────────────────────────────────

def test_build_entry_xy_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("10.5")
    dock.y_edit.setText("-2.0")

    name, entry = dock._build_entry()
    assert name == "origin"
    assert entry == {"xy": [10.5, -2.0]}


def test_xy_mode_requires_both_x_and_y(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("10.5")

    assert dock._build_entry() is None
    assert any("Y is required" in r.message for r in caplog.records)


def test_build_entry_anchor_mode_with_sheet_pad_cluster(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_role_edit.setCurrentText("FPGA")
    dock.anchor_sheet_edit.setCurrentText("Channel_1")
    dock.anchor_pad_edit.setText("2")
    dock.anchor_cluster_edit.setCurrentText("PI_FILTER")
    dock.shift_x_edit.setText("1.5")

    name, entry = dock._build_entry()
    assert name == "p1"
    assert entry == {
        "anchor_role": "FPGA",
        "anchor_sheet": "Channel_1",
        "anchor_cluster": "PI_FILTER",
        "anchor_pad": "2",
        "shift_x_mm": 1.5,
    }


def test_refresh_sheet_names_populates_anchor_sheet_combo(main_window, tmp_path, monkeypatch):
    """2026-08-15 (plan step 3): the point's own anchor Sheet field is
    autocompleted from the project's schematic files on root change —
    closes the module docstring's long-flagged "NOT yet a combo" note."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._root_path = tmp_path / "root.yaml"
    monkeypatch.setattr(points_mod, "collect_all_sheet_names",
                        lambda root: ["Channel_0", "Channel_1"])
    dock._refresh_sheet_names()
    assert [dock.anchor_sheet_edit.itemText(i) for i in range(dock.anchor_sheet_edit.count())] \
        == ["Channel_0", "Channel_1"]


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")
    dock.anchor_role_edit.setCurrentText("FPGA")

    assert dock._build_entry() is None
    assert any("mutually exclusive" in r.message for r in caplog.records)


def test_sheet_without_role_is_rejected_by_the_backend_validator(main_window, tmp_path):
    """anchor_sheet only narrows anchor_role — _build_entry() itself
    already can't produce this combination through the UI (Sheet is only
    read when Role, not Ref, is set — see _build_entry's anchor branch),
    so this pins down the backend validator Save/Resolve both call
    (load_point) directly, matching _load_point's own test coverage."""
    with pytest.raises(ValidationError, match="anchor_sheet without anchor_role"):
        load_point("p1", {"anchor_ref": "U3", "anchor_sheet": "Channel_1"})


def test_point_mode_requires_a_name(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("child")
    dock.origin_mode_combo.setCurrentIndex(2)

    assert dock._build_entry() is None
    assert any("Point: name is required" in r.message for r in caplog.records)


def test_build_entry_board_origin_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("board_zero")
    dock.origin_mode_combo.setCurrentIndex(3)
    dock.board_origin_kind_combo.setCurrentIndex(1)  # grid

    name, entry = dock._build_entry()
    assert name == "board_zero"
    assert entry == {"anchor_origin": "grid"}


def test_board_origin_mode_defaults_to_drill(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("board_zero")
    dock.origin_mode_combo.setCurrentIndex(3)

    name, entry = dock._build_entry()
    assert entry == {"anchor_origin": "drill"}


def test_name_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")

    assert dock._build_entry() is None
    assert any("Name is required" in r.message for r in caplog.records)


# ── Origin mode row visibility ───────────────────────────────────────────

def test_origin_mode_toggles_row_visibility(main_window, tmp_path):
    """isVisibleTo(dock), not isVisible() — main_window is never actually
    shown in these headless tests, so isVisible() would be False
    regardless of setVisible() (a widget's real visibility also depends on
    its top-level window's own shown state)."""
    dock, _ = _make_dock(main_window, tmp_path)

    def visible(row):
        return row.isVisibleTo(dock)

    # 2026-08-11: rows now live on the shared AnchorOriginWidget
    # (gui/docks/_anchor_origin.py), not PointsDock itself.
    origin = dock.origin_widget
    dock.origin_mode_combo.setCurrentIndex(0)
    assert visible(origin._xy_row) and not visible(origin._anchor_row) and not visible(origin._point_row)
    assert not visible(origin._shift_row)

    dock.origin_mode_combo.setCurrentIndex(1)
    assert visible(origin._anchor_row) and not visible(origin._xy_row)
    assert visible(origin._shift_row)

    dock.origin_mode_combo.setCurrentIndex(2)
    assert visible(origin._point_row) and not visible(origin._anchor_row)
    assert visible(origin._shift_row)

    dock.origin_mode_combo.setCurrentIndex(3)
    assert visible(origin._board_origin_row) and not visible(origin._point_row)
    assert visible(origin._shift_row)


# ── Save ──────────────────────────────────────────────────────────────────

def test_save_writes_dict_section_and_preserves_other_keys(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"c1": {"components": []}}})
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._on_save()

    data = _read_yaml(target)
    assert data["points"] == {"origin": {"xy": [1.0, 2.0]}}
    assert data["cells"] == {"c1": {"components": []}}
    assert any("Wrote" in r.message for r in caplog.records)


def test_save_overwrites_an_existing_point_by_name(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"points": {"origin": {"xy": [0, 0]}}})
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("5.0")
    dock.y_edit.setText("6.0")

    dock._on_save()

    assert _read_yaml(target)["points"] == {"origin": {"xy": [5.0, 6.0]}}
    assert any("Overwrote" in r.message for r in caplog.records)


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = PointsDock(main_window)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._on_save()
    assert any("Pick a file" in r.message for r in caplog.records)


def test_save_refreshes_point_name_autocomplete(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._on_save()

    assert [dock.point_edit.itemText(i) for i in range(dock.point_edit.count())] == ["origin"]


# ── new_point / load_entry ───────────────────────────────────────────────

def test_new_point_resets_form_and_targets_file(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("stale")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")

    dock.new_point(target)

    assert dock.name_edit.text() == ""
    assert dock.origin_mode_combo.currentIndex() == 0
    assert dock.anchor_ref_edit.text() == ""
    assert dock._path == target


def test_load_entry_xy_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"points": {"origin": {"xy": [3.0, 4.0]}}})

    dock.load_entry("origin")

    assert dock.name_edit.text() == "origin"
    assert dock.origin_mode_combo.currentIndex() == 0
    assert dock.x_edit.text() == "3.0"
    assert dock.y_edit.text() == "4.0"


def test_load_entry_anchor_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"points": {"p1": {
        "anchor_role": "FPGA", "anchor_sheet": "Channel_1",
        "anchor_cluster": "PI_FILTER", "anchor_pad": "2", "shift_y_mm": 1.5,
    }}})

    dock.load_entry("p1")

    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.anchor_role_edit.currentText() == "FPGA"
    assert dock.anchor_sheet_edit.currentText() == "Channel_1"
    assert dock.anchor_cluster_edit.currentText() == "PI_FILTER"
    assert dock.anchor_pad_edit.text() == "2"
    assert dock.shift_y_edit.text() == "1.5"


def test_load_entry_point_chain_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"points": {
        "base": {"xy": [0, 0]},
        "child": {"anchor_point": "base"},
    }})

    dock.load_entry("child")

    assert dock.origin_mode_combo.currentIndex() == 2
    assert dock.point_edit.currentText() == "base"


def test_load_entry_board_origin_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path, {"points": {
        "board_zero": {"anchor_origin": "grid"},
    }})

    dock.load_entry("board_zero")

    assert dock.origin_mode_combo.currentIndex() == 3
    assert dock.board_origin_kind_combo.currentData() == "grid"


# ── Point-name autocomplete ──────────────────────────────────────────────

def test_point_name_autocomplete_refreshes_on_set_target_file(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"points": {"a": {"xy": [0, 0]}, "b": {"xy": [1, 1]}}})
    assert [dock.point_edit.itemText(i) for i in range(dock.point_edit.count())] == ["a", "b"]


# ── refresh_known_roles ───────────────────────────────────────────────────

def test_refresh_known_roles_populates_role_and_cluster_combos(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    snapshot = [
        SimpleNamespace(role="FPGA", cluster="Channel_1/PI_FILTER"),
        SimpleNamespace(role="C_IN", cluster="Channel_1/PI_FILTER"),
    ]

    dock.refresh_known_roles(snapshot)

    assert [dock.anchor_role_edit.itemText(i) for i in range(dock.anchor_role_edit.count())] \
        == ["C_IN", "FPGA"]
    assert [dock.anchor_cluster_edit.itemText(i) for i in range(dock.anchor_cluster_edit.count())] \
        == ["Channel_1/PI_FILTER"]


# ── Resolve ───────────────────────────────────────────────────────────────

class _FakeAdapter:
    def __init__(self):
        self.selected = None

    def select_items(self, items):
        self.selected = items


def _connect_board(dock):
    adapter = _FakeAdapter()
    dock._connection.board = SimpleNamespace(adapter=adapter)
    return adapter


def test_resolve_without_connection_shows_error(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._do_resolve()

    assert any("Not connected" in r.message for r in caplog.records)


def test_resolve_with_invalid_form_does_not_touch_the_resolver(main_window, tmp_path, monkeypatch, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    _connect_board(dock)
    called = []
    monkeypatch.setattr(points_mod, "resolve_point_chain", lambda *a, **k: called.append(1))
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    # y left blank -> invalid

    dock._do_resolve()

    assert called == []
    assert any("Name is required" in r.message for r in caplog.records)


def test_resolve_shows_position_and_selects_the_footprint(main_window, tmp_path, monkeypatch, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    adapter = _connect_board(dock)
    fp = object()
    resolved = SimpleNamespace(position=SimpleNamespace(x=1_000_000, y=2_000_000), footprint=fp)
    captured = {}

    def fake_resolve(adapter_arg, points, name, sheet_names=None):
        captured["adapter"] = adapter_arg
        captured["points"] = points
        captured["name"] = name
        return resolved

    monkeypatch.setattr(points_mod, "resolve_point_chain", fake_resolve)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")

    dock._do_resolve()

    assert any("X=1.000mm Y=2.000mm" in r.message for r in caplog.records)
    assert captured["name"] == "p1"
    assert isinstance(captured["points"]["p1"], Point)
    # select_items is called inside _run_resolve itself (worker thread),
    # not by the dock — confirm the adapter it was handed is the live one.
    assert captured["adapter"] is adapter
    assert adapter.selected == [fp]


def test_resolve_shows_no_footprint_suffix_when_shift_applied(main_window, tmp_path, monkeypatch, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    _connect_board(dock)
    resolved = SimpleNamespace(position=SimpleNamespace(x=0, y=0), footprint=None)
    monkeypatch.setattr(points_mod, "resolve_point_chain", lambda *a, **k: resolved)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")
    dock.shift_x_edit.setText("1.0")

    dock._do_resolve()

    assert any("no footprint to highlight" in r.message for r in caplog.records)


def test_resolve_failure_shows_message(main_window, tmp_path, monkeypatch, caplog):
    from kicadstamp.exceptions import ValidationError

    def raise_it(*a, **k):
        raise ValidationError("boom")

    dock, _ = _make_dock(main_window, tmp_path)
    _connect_board(dock)
    monkeypatch.setattr(points_mod, "resolve_point_chain", raise_it)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")

    dock._do_resolve()

    assert any("Resolve failed" in r.message for r in caplog.records)


def test_resolve_excludes_an_unrelated_broken_other_point(main_window, tmp_path, monkeypatch):
    """gui/docks/points.py's own deliberate leniency (see module docstring)
    — an unrelated OTHER point that fails to load must simply be left out
    of the dict handed to resolve_point_chain, not raise/abort."""
    dock, _ = _make_dock(main_window, tmp_path, {"points": {
        "broken": {"anchor_sheet": "X"},  # anchor_sheet without anchor_role — invalid
    }})
    _connect_board(dock)
    captured = {}

    def fake_resolve(adapter_arg, points, name, sheet_names=None):
        captured["points"] = points
        return SimpleNamespace(position=SimpleNamespace(x=0, y=0), footprint=None)

    monkeypatch.setattr(points_mod, "resolve_point_chain", fake_resolve)
    dock.name_edit.setText("good")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._do_resolve()

    assert "broken" not in captured["points"]
    assert "good" in captured["points"]


# ── Target-file combo + set_root_path (2026-08-13, plan
# tree_to_combo_file_pickers — PointsDock was the ONLY dock without a
# set_root_path; it gained both together) ─────────────────────────────────

def _combo_index_for_filename(combo, filename):
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1


def test_set_root_path_populates_target_file_combo(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text("points: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = PointsDock(main_window)

    dock.set_root_path(root)

    assert dock._root_path == root
    names = {dock.target_file_combo.itemData(i).name for i in range(dock.target_file_combo.count())}
    assert names == {"root.yaml", "sub.yaml"}


def test_set_root_path_none_clears_the_target_file_combo(main_window):
    dock = PointsDock(main_window)
    dock.set_root_path(None)
    assert dock.target_file_combo.count() == 0


def test_picking_target_file_combo_calls_set_target_file(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text("points: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = PointsDock(main_window)
    dock.set_root_path(root)

    dock.target_file_combo.setCurrentIndex(
        _combo_index_for_filename(dock.target_file_combo, "sub.yaml"))

    assert dock._path is not None
    assert dock._path.name == "sub.yaml"


def test_set_target_file_reflects_into_the_combo_even_before_root_is_known(main_window, tmp_path):
    """ConfigTreeDock's own file_selected click must keep working exactly
    as before — even before set_root_path() (or for a file outside the
    include graph) the path is still selected as an extra combo item."""
    target = tmp_path / "points.yaml"
    _write_yaml(target, {"points": {}})
    dock = PointsDock(main_window)

    dock.set_target_file(target)

    assert dock.target_file_combo.currentData() == target
    assert dock._path == target


def test_set_root_path_does_not_clobber_point_name_autocomplete(main_window, tmp_path):
    """The point-chain autocomplete stays scoped to the dock's OWN target
    file (unchanged behaviour), while the target-file combo comes from the
    whole include graph — two independent sources, set_root_path must not
    touch the former."""
    dock, target = _make_dock(main_window, tmp_path, {"points": {"a": {"xy": [0, 0]}}})
    sub = tmp_path / "sub.yaml"
    _write_yaml(sub, {"points": {"b": {"xy": [1, 1]}}})
    root = tmp_path / "root.yaml"
    _write_yaml(root, {"points": {}, "include": ["sub.yaml"]})

    dock.set_root_path(root)

    # combo lists whole graph; autocomplete still only the targeted file
    combo_names = {dock.target_file_combo.itemData(i).name for i in range(dock.target_file_combo.count())}
    assert combo_names == {"root.yaml", "sub.yaml"}
    assert [dock.point_edit.itemText(i) for i in range(dock.point_edit.count())] == ["a"]


def test_target_file_combo_is_a_closed_picker_not_free_text(main_window):
    dock = PointsDock(main_window)
    assert not dock.target_file_combo.isEditable()
