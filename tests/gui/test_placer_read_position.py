#!/usr/bin/env python3
"""PlacerDock "Read current position" — CoordinatePlacement (Single component)
button (design 2026_08_29_config_tree_read_live_position.md §1.1/§3.1).

Headless, board-mutation-free: the live resolvers (read_coordinate_live /
read_anchor_live) are monkeypatched — the test drives the dock's orchestration
(adapter check, current-form identity read, mode-aware fill, warning on
failure) exactly like test_trees_dock.py drives _resolve_live_offset. The
resolvers' own correctness is covered by tests/test_live_position.py."""
import gui.docks.placer as placer_mod
from gui.docks.live_position import LiveRead
from gui.docks.placer import PlacerDock
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM


class _FakeBoard:
    """connection.board stand-in with a live (non-None) .adapter — enough for
    the dock's connection check to pass; the adapter itself is never used
    because the resolvers are monkeypatched."""

    def __init__(self):
        self.adapter = object()


def _make_coordinate_dock(main_window, tmp_path):
    """A PlacerDock switched to Single-component (CoordinatePlacement) mode,
    rooted at a minimal valid root.sexp."""
    placer_file = tmp_path / "root.sexp"
    placer_file.write_text(dict_to_sexp({
        "clone_placements": [],
        "coordinate_placements": [],
        "cells": {},
    }), encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(placer_file)
    dock.cell_mode_combo.setCurrentIndex(1)  # Single component
    return dock, placer_file


def _set_identity(form, cluster="FPGA_FLASH", role="R_CLK"):
    form.cluster_combo.setCurrentText(cluster)
    form.role_combo.setCurrentText(role)


def _make_clone_dock(main_window, tmp_path):
    """A PlacerDock in Cell (ClonePlacement) mode with a selected cell and a
    cluster — rooted at a minimal valid root.sexp."""
    placer_file = tmp_path / "root.sexp"
    placer_file.write_text(dict_to_sexp({"clone_placements": [], "cells": {}}),
                           encoding="utf-8")
    dock = PlacerDock(main_window)
    dock.set_root_path(placer_file)
    dock._selected_cell = "pi_filter"
    dock.cluster_edit.setCurrentText("FPGA_FLASH")
    return dock, placer_file


def test_coordinate_read_position_fills_xy_and_rotation(main_window, tmp_path, monkeypatch):
    """Cartesian mode: the live (Role, Cluster) read fills x/y + rotation."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.5 * MM), int(-7.0 * MM)),
        rotation_deg=90.0, footprint=None))

    dock._on_coordinate_read_position()

    assert form.x_edit.text() == "12.500"
    assert form.y_edit.text() == "-7.000"
    assert form.rotation_edit.text() == "90.000"


def test_coordinate_read_position_polar_recomputes_radius_angle(main_window, tmp_path, monkeypatch):
    """Polar-around-centre mode: the same absolute read is expressed as
    radius/angle from the form's fixed centre (3,4 from (0,0) -> r=5, angle
    atan2(4,3) ~ 53.13 deg)."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    form.mode_combo.setCurrentIndex(1)
    form.center_x_edit.setText("0")
    form.center_y_edit.setText("0")
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(3.0 * MM), int(4.0 * MM)),
        rotation_deg=30.0, footprint=None))

    dock._on_coordinate_read_position()

    assert form.radius_edit.text() == "5.000"
    assert form.angle_edit.text() == "53.130"
    assert form.rotation_edit.text() == "30.000"


def test_coordinate_read_position_anchor_writes_offset(main_window, tmp_path, monkeypatch):
    """Anchor-relative mode: the read position is written as the OFFSET from
    the anchor's live position (component at (12,20) vs anchor at (10,20) ->
    offset (2,0))."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    form.mode_combo.setCurrentIndex(2)  # anchor-relative
    form._anchor_widget.load(mode="anchor", ref="U3")
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))
    monkeypatch.setattr(placer_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))

    dock._on_coordinate_read_position()

    assert form._offset_x_edit.text() == "2.000"
    assert form._offset_y_edit.text() == "0.000"
    assert form.rotation_edit.text() == "0.000"


def test_coordinate_read_position_warns_when_no_live_connection(main_window, tmp_path, monkeypatch):
    """No live board connection -> a warning, and NOTHING is written to the
    position fields (no silent partial state)."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    form = dock.coordinate_form
    _set_identity(form)

    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_coordinate_read_position()

    assert warnings
    assert form.x_edit.text() == ""
    assert form.rotation_edit.text() == ""


def test_coordinate_read_position_resolution_failure_leaves_untouched(main_window, tmp_path, monkeypatch):
    """A resolution fatal (0/2+ matches) -> warning, position fields untouched."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)

    def _boom(*a, **k):
        raise ValidationError("ambiguous")
    monkeypatch.setattr(placer_mod, "read_coordinate_live", _boom)
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_coordinate_read_position()

    assert warnings
    assert "ambiguous" in str(warnings[0])
    assert form.x_edit.text() == ""
    assert form.rotation_edit.text() == ""


def test_coordinate_read_position_requires_cluster_and_role(main_window, tmp_path, monkeypatch):
    """Missing identity -> warning, no resolution attempt."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_coordinate_read_position()
    assert warnings
    assert form.x_edit.text() == ""


# ── ClonePlacement (Cell mode) — the cell ORIGIN re-derived from the board ──


def test_clone_read_position_fills_origin_and_rotation(main_window, tmp_path, monkeypatch):
    """Cartesian origin (Origin tab, mode "xy"): the cell's origin + rotation
    are filled from the live read."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=45.0, footprint=None))

    dock._on_clone_read_position()

    assert dock.origin_widget.x_edit.text() == "10.000"
    assert dock.origin_widget.y_edit.text() == "20.000"
    assert dock.rotation_edit.text() == "45.000"


def test_clone_read_position_anchor_writes_shift(main_window, tmp_path, monkeypatch):
    """Origin tab in anchor mode: the origin is written as the SHIFT from the
    anchor's live position (origin (12,20) vs anchor (10,20) -> shift (2,0))."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    dock.origin_widget.load(mode="anchor", ref="U3")
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))
    monkeypatch.setattr(placer_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))

    dock._on_clone_read_position()

    assert dock.origin_widget.shift_x_edit.text() == "2.000"
    assert dock.origin_widget.shift_y_edit.text() == "0.000"
    assert dock.rotation_edit.text() == "0.000"


def test_clone_read_position_warns_when_no_live_connection(main_window, tmp_path, monkeypatch):
    """No live board connection -> a warning, and nothing is written."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_clone_read_position()
    assert warnings
    assert dock.origin_widget.x_edit.text() == ""
    assert dock.rotation_edit.text() == ""


def test_clone_read_position_resolution_failure_leaves_untouched(main_window, tmp_path, monkeypatch):
    """A resolution fatal -> warning, Origin fields untouched."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()

    def _boom(*a, **k):
        raise ValidationError("no component resolved")
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", _boom)
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_clone_read_position()

    assert warnings
    assert "no component resolved" in str(warnings[0])
    assert dock.origin_widget.x_edit.text() == ""
    assert dock.rotation_edit.text() == ""


def test_clone_read_position_requires_cluster_and_cell(main_window, tmp_path, monkeypatch):
    """Missing cluster or cell -> warning, no resolution attempt."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    dock.cluster_edit.setCurrentText("")
    warnings = []
    monkeypatch.setattr(placer_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dock._on_clone_read_position()
    assert warnings
    assert dock.origin_widget.x_edit.text() == ""
