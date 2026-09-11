# tests/gui/test_points_dock.py
"""
PointsDock tests are deliberately headless — same reasoning as
tests/gui/test_placer_dock.py/test_thermal_via_dock.py. Resolve's own
live-board math is covered by tests/test_point_resolver.py
(resolve_point_chain itself); here resolve_point_chain is monkeypatched so
these tests only check what PointsDock builds/passes/shows around it.

The overlay-circle half (Ж, plan_2026_09_11_points_markers.md) runs against
the same duck-typed fake adapter gui/board_overlay.py and gui/overlay_markers.py
already use (create_items / remove_by_ids / select_items / refresh_board /
`_board`) — created shapes REALLY land on the fake board, so "one circle per
key" is observable rather than asserted on a call log.
"""
from types import SimpleNamespace

import pytest

from kipy.board_types import BoardCircle, BoardLayer

import gui.board_overlay as board_overlay
import gui.docks.points as points_mod
import gui.overlay_markers as markers_mod
from gui.docks.points import PointsDock
from kicadstamp.config import Point, load_point
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM


def _fill_cell_defaults(data: dict) -> dict:
    """s-expr omits default-valued Cell fields (layer='F.Cu', empty
    vias/components/tracks/clone_placements lists); re-apply them so the
    raw-dict assertions stay identical to the old yaml.safe_load reads."""
    for entry in data.get("cells", {}).values():
        entry.setdefault("layer", "F.Cu")
        entry.setdefault("vias", [])
        entry.setdefault("components", [])
        entry.setdefault("tracks", [])
        entry.setdefault("clone_placements", [])
    return data


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return _fill_cell_defaults(sexp_to_dict(path.read_text(encoding="utf-8"))) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "root.sexp"
    _write(target_file, data if data is not None else {"points": {}})
    dock = PointsDock(main_window)
    dock.set_root_path(target_file)
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
    dock._root_path = tmp_path / "root.sexp"
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

    data = _load(target)
    assert data["points"] == {"origin": {"xy": [1.0, 2.0]}}
    assert data["cells"] == {"c1": {"layer": "F.Cu", "vias": [], "components": [],
                                     "tracks": [], "clone_placements": []}}
    assert any("Wrote" in r.message for r in caplog.records)


def test_save_overwrites_an_existing_point_by_name(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"points": {"origin": {"xy": [0, 0]}}})
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("5.0")
    dock.y_edit.setText("6.0")

    dock._on_save()

    assert _load(target)["points"] == {"origin": {"xy": [5.0, 6.0]}}
    assert any("Overwrote" in r.message for r in caplog.records)


# ── comment field (handoff_2026_08_27_entity_comment_field.md) ────────────

def test_build_entry_includes_comment(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")
    dock.comment_edit.setText("a point note")

    name, entry = dock._build_entry()
    assert name == "origin"
    assert entry == {"xy": [1.0, 2.0], "comment": "a point note"}


def test_comment_saves_and_loads_back(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"points": {"origin": {"xy": [0, 0]}}})
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("5.0")
    dock.y_edit.setText("6.0")
    dock.comment_edit.setText("a point note")

    dock._on_save()

    assert _load(target)["points"]["origin"] == \
        {"xy": [5.0, 6.0], "comment": "a point note"}
    dock.load_entry("origin")
    assert dock.comment_edit.text() == "a point note"


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = PointsDock(main_window)
    dock.name_edit.setText("origin")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")

    dock._on_save()
    assert any("Set the project root first" in r.message for r in caplog.records)


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

def test_point_name_autocomplete_refreshes_on_set_root_path(main_window, tmp_path):
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

# The overlay layer the fakes expose under its DEFAULT display name
# (board_overlay.OVERLAY_DEFAULT_LAYER = "User.Drawings"), so a drawn circle
# resolves a live layer exactly like on a real board.
LAYER = BoardLayer.BL_Dwgs_User
OTHER_LAYER = BoardLayer.BL_User_5


class _FakeBoard:
    """Duck-typed `_board` — the exact surface gui/board_overlay.py reads."""

    def __init__(self, layers=None, shapes=None):
        self.layers = list(layers) if layers is not None else [LAYER, OTHER_LAYER]
        self.shapes = list(shapes) if shapes is not None else []
        self.names = {LAYER: "User.Drawings", OTHER_LAYER: "User.KiCadStamp"}

    def get_enabled_layers(self):
        return list(self.layers)

    def get_layer_name(self, layer):
        return self.names.get(layer, str(layer))

    def get_shapes(self):
        return list(self.shapes)


class _FakeAdapter:
    """Board-mutation-free fake with the overlay-drawing duck surface.
    Created shapes are registered on the fake board and remove_by_ids() really
    removes them, so what idempotency and cleanup must see (a live layer) is
    what these tests see. `selections` records every select_items() call in
    order: the Resolve footprint highlight first, then one repaint per draw."""

    def __init__(self, board=None):
        self._board = board if board is not None else _FakeBoard()
        self.created = []
        self.removed = []
        self.refreshes = 0
        self.selections = []
        self._next_id = 0

    def refresh_board(self):
        self.refreshes += 1

    def create_items(self, items):
        items = list(items)
        # The real board assigns a uuid on create; a freshly built kipy shape
        # carries an EMPTY id.value, so the fake must do the same or every
        # created shape would collide on "".
        for item in items:
            self._next_id += 1
            item.id.value = f"shape-{self._next_id}"
        self.created.extend(items)
        self._board.shapes = list(self._board.shapes) + items
        return items

    def select_items(self, items):
        self.selections.append(list(items))

    def remove_by_ids(self, uuid_strs):
        doomed = {str(u) for u in uuid_strs}
        self.removed.extend(uuid_strs)
        self._board.shapes = [s for s in self._board.shapes
                              if str(s.id.value) not in doomed]
        return True


def _connect_board(dock):
    adapter = _FakeAdapter()
    dock._connection.board = SimpleNamespace(adapter=adapter)
    return adapter


def _circles(adapter, layer=LAYER):
    return [s for s in adapter._board.shapes
            if isinstance(s, BoardCircle) and s.layer == layer]


def _make_dock_with_board(main_window, tmp_path, data=None):
    dock, target = _make_dock(main_window, tmp_path, data)
    adapter = _connect_board(dock)
    return dock, target, adapter


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
    # The footprint highlight is the FIRST select_items() call; the marker
    # circle this task adds repaints right after it.
    assert adapter.selections[0] == [fp]


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


def test_set_root_path_point_name_autocomplete_covers_whole_graph(main_window, tmp_path):
    """The point-chain autocomplete now reads the WHOLE include graph (a
    point can live in any included file) — after the file pickers were
    removed (2026-08-21), there is no separate "target file" to scope it to."""
    dock, target = _make_dock(main_window, tmp_path, {"points": {"a": {"xy": [0, 0]}}})
    sub = tmp_path / "sub.sexp"
    _write(sub, {"points": {"b": {"xy": [1, 1]}}})
    root2 = tmp_path / "root2.sexp"
    _write(root2, {"include": ["root.sexp", "sub.sexp"]})

    dock.set_root_path(root2)

    assert sorted(dock.point_edit.itemText(i) for i in range(dock.point_edit.count())) == \
        ["a", "b"]


# ── Overlay circles (Ж, plan_2026_09_11_points_markers.md) ────────────────
#
# One test per item of the plan's Ж.4 checklist. The "resolved through a live
# footprint" item is a REGRESSION test on today's highlight, which must keep
# working TOGETHER with the new circle.

def _fx_xy(name, x_mm, y_mm):
    """A resolve_point_chain stub result for a bare xy point — the numbers the
    dock must translate into a circle centre."""
    return SimpleNamespace(
        position=SimpleNamespace(x=int(x_mm * MM), y=int(y_mm * MM)),
        footprint=None)


def test_resolve_draws_exactly_one_circle_at_the_computed_position(
        main_window, tmp_path, caplog):
    """Ж.4.1 — one circle, and its centre IS the resolved position (numbers).
    Ж.4.10 — the Resolve text output is unchanged by this task."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("12.5")
    dock.y_edit.setText("-3.25")

    dock._do_resolve()

    assert any("X=12.500mm Y=-3.250mm" in r.message for r in caplog.records)
    circles = _circles(adapter)
    assert len(circles) == 1
    assert circles[0].center.x == int(12.5 * MM)
    assert circles[0].center.y == int(-3.25 * MM)
    assert markers_mod.owner.has_key("point/p1")


def test_resolve_twice_moves_the_same_circle(main_window, tmp_path):
    """Ж.4.2 — a repeat Resolve MOVES the one circle of that key; the previous
    shape is removed in the same operation, never left stacked on the layer."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("2.0")
    dock._do_resolve()
    first_uuid = markers_mod.owner.uuid_for("point/p1")
    assert len(_circles(adapter)) == 1

    dock.x_edit.setText("7.5")
    dock.y_edit.setText("8.25")
    dock._do_resolve()

    circles = _circles(adapter)
    assert len(circles) == 1
    assert circles[0].center.x == int(7.5 * MM)
    assert circles[0].center.y == int(8.25 * MM)
    assert first_uuid in [str(u) for u in adapter.removed]
    assert markers_mod.owner.uuid_for("point/p1") == str(circles[0].id.value)


def test_resolve_two_different_points_gives_two_circles(main_window, tmp_path):
    """Ж.4.3 — two keys, two circles."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path)
    dock.origin_mode_combo.setCurrentIndex(0)

    dock.name_edit.setText("p1")
    dock.x_edit.setText("1.0")
    dock.y_edit.setText("1.0")
    dock._do_resolve()
    dock.name_edit.setText("p2")
    dock.x_edit.setText("2.0")
    dock.y_edit.setText("2.0")
    dock._do_resolve()

    assert len(_circles(adapter)) == 2
    assert markers_mod.owner.has_key("point/p1")
    assert markers_mod.owner.has_key("point/p2")


def test_bare_xy_point_gets_a_circle_without_any_highlight(
        main_window, tmp_path, caplog):
    """Ж.4.4 — the whole point of the task: a bare xy point has no footprint to
    highlight, and now becomes visible as a circle."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path)
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(0)
    dock.x_edit.setText("4.0")
    dock.y_edit.setText("5.0")

    dock._do_resolve()

    assert any("no footprint to highlight" in r.message for r in caplog.records)
    assert len(_circles(adapter)) == 1
    # The ONLY select_items() call is the repaint of the created circle — the
    # footprint-highlight branch never ran.
    assert len(adapter.selections) == 1
    assert isinstance(adapter.selections[0][0], BoardCircle)


def test_resolve_through_a_live_footprint_draws_circle_and_highlight(
        main_window, tmp_path, monkeypatch):
    """Ж.4.5 — regression on today's behaviour: the footprint is still
    highlighted, next to the new circle."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path)
    fp = object()
    monkeypatch.setattr(points_mod, "resolve_point_chain",
                        lambda *a, **k: SimpleNamespace(
                            position=SimpleNamespace(x=1_000_000, y=2_000_000),
                            footprint=fp))
    dock.name_edit.setText("p1")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U3")

    dock._do_resolve()

    assert adapter.selections[0] == [fp]
    circles = _circles(adapter)
    assert len(circles) == 1
    assert circles[0].center.x == int(1.0 * MM)
    assert circles[0].center.y == int(2.0 * MM)


def test_show_all_skips_one_bad_point_and_keeps_going(
        main_window, tmp_path, monkeypatch, caplog):
    """Ж.4.6 — three points, one does not resolve: two circles, a Log line
    naming the third, and NO exception."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
        "b": {"xy": [2.0, 2.0]},
        "c": {"anchor_ref": "U_MISSING"},
    }})

    def fake_resolve(adapter_arg, points, name, sheet_names=None):
        if name == "c":
            raise ValidationError("no component matching 'U_MISSING'")
        return _fx_xy(name, points[name].xy[0], points[name].xy[1])

    monkeypatch.setattr(points_mod, "resolve_point_chain", fake_resolve)

    dock._do_show_all_points()

    assert len(_circles(adapter)) == 2
    assert markers_mod.owner.has_key("point/a")
    assert markers_mod.owner.has_key("point/b")
    assert not markers_mod.owner.has_key("point/c")
    assert any("'c'" in r.message and "did not resolve" in r.message
               for r in caplog.records)


def test_show_all_reports_a_point_that_does_not_even_load(
        main_window, tmp_path, caplog):
    """An entry that fails load_point() is reported BY NAME — never dropped
    silently — and the other points still get their circles (Ж.2.2)."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "good": {"xy": [3.0, 4.0]},
        "bad": {"anchor_sheet": "Channel_1"},  # anchor_sheet without anchor_role
    }})

    dock._do_show_all_points()

    assert len(_circles(adapter)) == 1
    assert any("'bad'" in r.message and "did not resolve" in r.message
               for r in caplog.records)


def test_show_all_with_an_empty_list_logs_and_draws_nothing(
        main_window, tmp_path, caplog):
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path, {"points": {}})

    dock._do_show_all_points()

    assert any("No points to show" in r.message for r in caplog.records)
    assert _circles(adapter) == []


def test_second_press_hides_every_point_circle_and_spares_other_namespaces(
        main_window, tmp_path, monkeypatch):
    """Ж.4.7 — the second press clears the WHOLE point namespace (shape
    included) and leaves a foreign namespace's shape alone. The toggle's state
    follows the MAP, not a GUI flag (Ж.2.2)."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
    }})
    monkeypatch.setattr(points_mod, "resolve_point_chain",
                        lambda *a, **k: _fx_xy("a", 1.0, 1.0))
    foreign_key = markers_mod.cell_anchor_key("/root/x", "cellA", "marker")
    foreign_uuid = markers_mod.owner.ensure_marker(adapter, foreign_key, 50.0, 50.0)

    assert dock.show_all_button.text() == "Show all points"
    dock._do_show_all_points()
    assert markers_mod.owner.has_key("point/a")
    assert dock.show_all_button.text() == "Hide all points"
    assert len(_circles(adapter)) == 2

    dock._do_show_all_points()

    assert [k for k in markers_mod.owner.keys() if k.startswith("point/")] == []
    assert markers_mod.owner.has_key(foreign_key)
    assert foreign_uuid not in [str(u) for u in adapter.removed]
    assert len(_circles(adapter)) == 1     # only the foreign cell-anchor circle
    assert dock.show_all_button.text() == "Show all points"


def test_show_all_without_a_board_logs_and_draws_nothing(
        main_window, tmp_path, caplog):
    """Ж.4.8 — no board: one Log line, the button does nothing, no exception."""
    dock, _target = _make_dock(main_window, tmp_path,
                               {"points": {"a": {"xy": [1.0, 1.0]}}})
    assert dock._connection.board is None

    dock._do_show_all_points()

    assert any("Not connected" in r.message for r in caplog.records)
    assert markers_mod.owner.keys() == []


def test_show_all_without_a_board_shows_no_modal(main_window, tmp_path, monkeypatch):
    """Е.2.6/Ж.2.2 — a visualisation never opens a dialog (Denis: "диалоговые
    окошки с ошибками — это просто ппц")."""
    from PyQt6.QtWidgets import QMessageBox

    def _no_modal(*_a, **_k):
        raise AssertionError("no modal must ever be shown")

    for name in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, _no_modal)
    dock, _target = _make_dock(main_window, tmp_path,
                               {"points": {"a": {"xy": [1.0, 1.0]}}})

    dock._do_show_all_points()      # must not raise


def test_show_all_dispatches_on_a_worker_with_the_buttons_locked(
        main_window, tmp_path, monkeypatch):
    """Ж.2.2 — the whole list is resolved on a worker (every point is a board
    read) with the same button lock Resolve uses."""
    dock, _target, _adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
    }})
    calls = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")

    dock._on_show_all_points()

    assert calls, "show-all must be dispatched on a worker"
    connection, widgets, fn, _ok, _err, payload = calls[0]
    assert connection is dock._connection
    assert fn == dock._run_show_all_points
    assert set(widgets) == {dock.resolve_button, dock.show_all_button}
    assert payload["names"] == ["a"]


def test_hide_all_is_dispatched_on_a_worker_too(main_window, tmp_path, monkeypatch):
    dock, _target, _adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
    }})
    monkeypatch.setattr(points_mod, "resolve_point_chain",
                        lambda *a, **k: _fx_xy("a", 1.0, 1.0))
    dock._do_show_all_points()          # seeds one point key
    calls = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")

    dock._on_show_all_points()

    assert calls[0][2] == dock._run_hide_all_points


def test_root_switch_clears_the_point_namespace(main_window, tmp_path, monkeypatch):
    """Ж.4.9 — a NEW root clears the whole point namespace (state) and
    dispatches the shape removal on a worker; foreign keys stay."""
    dock, _target, adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
    }})
    monkeypatch.setattr(points_mod, "resolve_point_chain",
                        lambda *a, **k: _fx_xy("a", 1.0, 1.0))
    dock._do_show_all_points()
    uuid = markers_mod.owner.uuid_for("point/a")
    assert uuid
    foreign_key = markers_mod.cell_anchor_key("/root/x", "cellA", "marker")
    markers_mod.owner.ensure_marker(adapter, foreign_key, 50.0, 50.0)

    calls = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")
    other = tmp_path / "other.sexp"
    _write(other, {"points": {}})
    dock.set_root_path(other)

    assert [k for k in markers_mod.owner.keys() if k.startswith("point/")] == []
    assert markers_mod.owner.has_key(foreign_key)
    assert calls and calls[0][2] == board_overlay.remove_overlay
    assert calls[0][6] == [uuid]


def test_set_root_path_with_the_same_path_keeps_the_circles(
        main_window, tmp_path, monkeypatch):
    """DockHub broadcasts root_changed on graph refreshes with the UNCHANGED
    path — that must not wipe circles the user is looking at (Ж.2.3)."""
    dock, target, _adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "a": {"xy": [1.0, 1.0]},
    }})
    monkeypatch.setattr(points_mod, "resolve_point_chain",
                        lambda *a, **k: _fx_xy("a", 1.0, 1.0))
    dock._do_show_all_points()

    dock.set_root_path(target)

    assert markers_mod.owner.has_key("point/a")
    assert dock.show_all_button.text() == "Hide all points"


def test_rename_of_the_loaded_point_drops_the_old_circle(
        main_window, tmp_path, monkeypatch):
    """Ж.2.3 — a Save under another name IS a rename: the old name's key is
    dropped and its shape removal is dispatched on a worker."""
    dock, _target, _adapter = _make_dock_with_board(main_window, tmp_path, {"points": {
        "old": {"xy": [1.0, 1.0]},
    }})
    dock.load_entry("old")
    dock._do_resolve()
    assert markers_mod.owner.has_key("point/old")

    calls = []
    monkeypatch.setattr(points_mod, "start_long_op",
                        lambda *a, **k: calls.append(a) or "controller")
    dock.name_edit.setText("new")
    dock._on_save()

    assert not markers_mod.owner.has_key("point/old")
    assert markers_mod.owner.keys() == []
    assert calls and calls[0][2] == board_overlay.remove_overlay

