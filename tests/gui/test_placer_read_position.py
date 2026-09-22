#!/usr/bin/env python3
"""PlacerDock "Read current position" — CoordinatePlacement (Single component)
button (design 2026_08_29_config_tree_read_live_position.md §1.1/§3.1).

Headless, board-mutation-free: the live resolvers (read_coordinate_live /
read_anchor_live) are monkeypatched — the test drives the dock's orchestration
(adapter check, current-form identity read, mode-aware fill, warning on
failure) exactly like test_trees_dock.py drives _resolve_live_offset. The
resolvers' own correctness is covered by tests/test_live_position.py."""
import logging

import pytest

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


@pytest.fixture
def armed_door(monkeypatch):
    """The door's guard ARMED in the test rig's mode (a violation raises) — the
    same shape tests/gui/test_board_door_offenders.py uses. Only the two Ш1e tests
    below need it: they are about the read of the door itself."""
    from gui import connection as connection_mod
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)


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


def test_coordinate_read_position_identifies_from_the_connections_snapshot(
        main_window, tmp_path, monkeypatch):
    """Ш1 (plan_2026_09_22_board_door_finish) — the dock hands the connection's
    OWN polled snapshot to the resolver, so the identity comes from the list the
    dock already displays instead of from a whole-board sweep. Pinned by
    IDENTITY, not by equality: a fresh copy of the list would be a different
    object and would not be the polled data.

    Mutation check: drop `snapshot=connection.snapshot` from the call and the
    captured keyword is None."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    polled = object()                     # not a list: only identity is asserted
    main_window.connection.snapshot = polled
    seen = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return LiveRead(position=Vector2.from_xy(0, 0), rotation_deg=0.0,
                        footprint=None)
    monkeypatch.setattr(placer_mod, "read_coordinate_live", _capture)

    dock._on_coordinate_read_position()

    assert seen.get("snapshot") is polled


def _real_door(main_window, board=None) -> "BoardConnection":
    """Give the stub window a REAL BoardConnection (the `main_window` fixture's
    `_FakeConnection` is a plain attribute bag — it has no door, so a test about
    the door's SIGN cannot see anything through it). Used by the two Ш1e tests
    below; every other test in this file is about orchestration and keeps the
    fake."""
    from gui.connection import BoardConnection
    connection = BoardConnection()
    if board is not None:
        connection.board = board
    main_window.connection = connection
    return connection


def test_the_position_read_is_signed_for_the_door(main_window, tmp_path, monkeypatch,
                                                  armed_door):
    """Ш1e — the read of `connection.board` here is DELIBERATE and short (one
    `get_footprint(ref)`, 0.3 ms measured on the live board 22.09.2026), so it
    carries the door's sign instead of moving to a worker. With the door ARMED
    over a REAL connection the call must simply work.

    Mutation check: drop the `with ui_thread_board_read(...)` wrapper and this
    fails with a refusal naming gui/docks/placer.py (mutation M8 of
    diagnostics/run_board_door_s1_mutations.py)."""
    _real_door(main_window, _FakeBoard())
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    form = dock.coordinate_form
    _set_identity(form)
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(0, 0), rotation_deg=0.0, footprint=None))

    dock._on_coordinate_read_position()          # must not raise

    assert form.x_edit.text() == "0.000"


def test_the_position_read_is_refused_while_the_socket_is_busy(
        main_window, tmp_path, monkeypatch):
    """Ш1e / door rule 3 — the read still touches the SHARED adapter (the resolved
    footprint's live position), so while the ~400ms selection tick or a long op
    owns that socket the click is refused instead of being interleaved into its
    in-flight transaction. Nothing is resolved and nothing is written.

    Mutation check: drop the `if socket_busy(connection): return` and the
    resolver is reached (the spy records the call)."""
    connection = _real_door(main_window, _FakeBoard())
    connection.long_op_active = True
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    form = dock.coordinate_form
    _set_identity(form)
    calls = []
    monkeypatch.setattr(placer_mod, "read_coordinate_live",
                        lambda *a, **k: calls.append(a) or LiveRead(
                            position=Vector2.from_xy(0, 0), rotation_deg=0.0,
                            footprint=None))

    dock._on_coordinate_read_position()

    assert calls == []
    assert form.x_edit.text() == ""


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


def test_coordinate_read_position_logs_error_when_no_live_connection(
        main_window, tmp_path, monkeypatch, caplog):
    """No live board connection -> ONE ERROR line in the Log (never a modal —
    plan_2026_09_11_no_modals_and_busy_kicad X.1), and NOTHING is written to
    the position fields (no silent partial state). The missing connection is
    board STATE, not user input, so it must not open a dialog."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    form = dock.coordinate_form
    _set_identity(form)

    def _no_boxes(*a, **k):
        raise AssertionError("a connection-state error must not open a QMessageBox")
    monkeypatch.setattr(placer_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()
    dock._on_coordinate_read_position()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "No live board connection" in errors[0].message
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


def test_clone_read_position_logs_error_when_no_live_connection(
        main_window, tmp_path, monkeypatch, caplog):
    """No live board connection -> ONE ERROR line in the Log (never a modal),
    and nothing is written — same rule as the coordinate read above."""
    dock, _ = _make_clone_dock(main_window, tmp_path)

    def _no_boxes(*a, **k):
        raise AssertionError("a connection-state error must not open a QMessageBox")
    monkeypatch.setattr(placer_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()
    dock._on_clone_read_position()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "No live board connection" in errors[0].message
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


# ── 2026-08-31 (plan placer_source_tab_gaps P.2): auto-switch Origin mode ──
# The Origin tab's anchor/point identity fields used to be silently IGNORED
# when the mode combo was left on the default Absolute (xy) — "Read current
# position" wrote ABSOLUTE coordinates with no warning (Денис live repro:
# numbers like (64.074, -47.592) instead of the small offset next to the FPGA
# anchor). The fix auto-switches the mode to the filled anchor/point set.


def test_clone_read_position_anchor_fields_filled_mode_xy_auto_switches(main_window, tmp_path, monkeypatch):
    """Regression: anchor Role filled + mode still "xy" -> the mode combo
    silently switches to "anchor" and the origin is written as the SHIFT from
    the anchor (not absolute)."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    ow = dock.origin_widget
    ow.anchor_role_edit.setCurrentText("FPGA")  # fill anchor, leave mode "xy"
    assert ow.mode == "xy"
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))
    monkeypatch.setattr(placer_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))

    dock._on_clone_read_position()

    assert ow.mode == "anchor"
    assert ow.shift_x_edit.text() == "2.000"
    assert ow.shift_y_edit.text() == "0.000"
    assert dock.rotation_edit.text() == "0.000"


def test_clone_read_position_point_fields_filled_mode_xy_auto_switches(main_window, tmp_path, monkeypatch):
    """Same auto-switch for the Point identity set."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    ow = dock.origin_widget
    ow.point_edit.setCurrentText("P_ORIGIN")  # fill point, leave mode "xy"
    assert ow.mode == "xy"
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))
    monkeypatch.setattr(placer_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))

    dock._on_clone_read_position()

    assert ow.mode == "point"
    assert ow.shift_x_edit.text() == "2.000"
    assert ow.shift_y_edit.text() == "0.000"


def test_clone_read_position_blank_anchor_mode_xy_still_absolute(main_window, tmp_path, monkeypatch):
    """Regression: blank anchor fields + mode "xy" -> absolute coordinates
    written exactly as before, zero interference from the auto-switch."""
    dock, _ = _make_clone_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    assert dock.origin_widget.mode == "xy"
    monkeypatch.setattr(placer_mod, "read_clone_origin_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=45.0, footprint=None))

    dock._on_clone_read_position()

    assert dock.origin_widget.mode == "xy"
    assert dock.origin_widget.x_edit.text() == "12.000"
    assert dock.origin_widget.y_edit.text() == "20.000"
    assert dock.rotation_edit.text() == "45.000"


def test_coordinate_read_position_anchor_filled_absolute_mode_auto_switches(main_window, tmp_path, monkeypatch):
    """The coordinate form's same class of bug: anchor widget filled but the
    mode combo still on an ABSOLUTE mode (0 Cartesian) -> auto-switch to the
    anchor-relative mode (2) and write the offset."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    form._anchor_widget.load(mode="anchor", ref="U3")  # fill anchor, leave mode 0
    assert form.mode_combo.currentIndex() == 0
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))
    monkeypatch.setattr(placer_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(10.0 * MM), int(20.0 * MM)),
        rotation_deg=0.0, footprint=None))

    dock._on_coordinate_read_position()

    assert form.mode_combo.currentIndex() == 2
    assert form._offset_x_edit.text() == "2.000"
    assert form._offset_y_edit.text() == "0.000"


def test_coordinate_read_position_blank_anchor_absolute_still_absolute(main_window, tmp_path, monkeypatch):
    """Regression: blank anchor widget + mode 0 -> absolute coordinates as
    before, no auto-switch."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    _set_identity(form)
    assert form.mode_combo.currentIndex() == 0
    monkeypatch.setattr(placer_mod, "read_coordinate_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.5 * MM), int(-7.0 * MM)),
        rotation_deg=90.0, footprint=None))

    dock._on_coordinate_read_position()

    assert form.mode_combo.currentIndex() == 0
    assert form.x_edit.text() == "12.500"
    assert form.y_edit.text() == "-7.000"
    assert form.rotation_edit.text() == "90.000"
