# tests/gui/test_placer_select_on_board.py
"""PlacerDock "Select on board" WITHOUT coordinates — plan Фаза F
(plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md §F).

Headless, board-mutation-free: the highlight resolver
(board_items_resolver.resolve_clone_board_items) is monkeypatched with a spy
that records the placement PlacerDock hands it. These tests prove the
read-only path REACHES the resolver when the Origin tab was never filled (the
live "X обязателен" regression, §F.1), for BOTH source modes, and that
Save/Redraw keep their full strictness (§F.3).

The identity guarantee the placeholder relies on: clone_anchor_id() must not
depend on the substituted absolute origin — only on the placement's identity
(name / anchor). Covered below, including the anchor_point branch (§F.3).
"""
from types import SimpleNamespace

import gui.docks.placer as placer_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import load_clone_placement, load_coordinate_placement
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.placement.services.clone_position_calculator import clone_anchor_id


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


class _FakeBoard:
    """connection.board stand-in with a live (non-None) .adapter — enough for
    the dock's connection check to pass; the adapter itself is never really
    used because resolve_clone_board_items is monkeypatched."""

    def __init__(self):
        self.adapter = object()


def _make_cell_dock(main_window, tmp_path):
    """Cell (ClonePlacement) mode, cell+cluster+sheet filled, Origin tab left
    on the default Absolute XY with EMPTY X/Y (the exact live broken state)."""
    cells = tmp_path / "cells.sexp"
    _write(cells, {"cells": {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                            "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [], "tracks": [], "layer": "F.Cu",
        }
    }})
    placer = tmp_path / "root.sexp"
    _write(placer, {"clone_placements": [], "include": ["cells.sexp"]})
    dock = PlacerDock(main_window)
    dock.set_root_path(placer)
    dock._selected_cell = "pi_filter"
    dock.cluster_edit.setCurrentText("PIF_3V3_VDD")
    dock.sheet_edit.setCurrentText("MCU")
    return dock, placer


def _make_coordinate_dock(main_window, tmp_path):
    """Single-component (CoordinatePlacement) mode, identity filled, position
    left empty (Cartesian-absolute with blank X/Y)."""
    placer = tmp_path / "root.sexp"
    _write(placer, {"clone_placements": [], "coordinate_placements": [], "cells": {}})
    dock = PlacerDock(main_window)
    dock.set_root_path(placer)
    dock.cell_mode_combo.setCurrentIndex(1)  # Single component
    form = dock.coordinate_form
    form.cluster_combo.setCurrentText("PIF_3V3_VDD")
    form.role_combo.setCurrentText("C_IN")
    form.sheet_edit.setCurrentText("MCU")
    return dock, placer


def _spy_resolver(monkeypatch):
    """Replace the highlight resolver with a spy returning [] (nothing on the
    board) and return the list it appends each received placement to."""
    seen = []

    def _fake(adapter, cfg, ctx, placement, **kwargs):
        seen.append(placement)
        return []

    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        _fake)
    return seen


# ── §F.3 — the highlight path no longer requires coordinates ──────────────

def test_select_on_board_clone_reaches_resolver_without_origin(
        main_window, tmp_path, monkeypatch, caplog):
    """§F.1 regression: Cell mode, empty Origin (Absolute XY), "Select on
    board" must reach the resolver with the placement's identity instead of
    failing with "X is required."."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    assert dock.origin_widget.mode == "xy"
    assert dock.x_edit.text() == "" and dock.y_edit.text() == ""
    seen = _spy_resolver(monkeypatch)

    dock._on_select_on_board()

    assert len(seen) == 1
    clone = seen[0]
    assert clone.cell == "pi_filter"
    assert clone.cluster == "PIF_3V3_VDD"
    assert clone.sheet == "MCU"
    assert not any("X is required" in r.message for r in caplog.records)


def test_select_on_board_coordinate_reaches_resolver_without_origin(
        main_window, tmp_path, monkeypatch, caplog):
    """Same for the CoordinatePlacement path: identity (cluster/role/sheet)
    only — the empty Cartesian position must not block the highlight."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    assert form.mode_combo.currentIndex() == 0     # Cartesian-absolute
    assert form.x_edit.text() == "" and form.y_edit.text() == ""
    seen = _spy_resolver(monkeypatch)

    dock._on_select_on_board()

    assert len(seen) == 1
    cp = seen[0]
    assert cp.cluster == "PIF_3V3_VDD"
    assert cp.role == "C_IN"
    assert cp.sheet == "MCU"


def test_highlight_placeholder_entry_is_a_valid_coordinate_placement(
        main_window, tmp_path):
    """The soft entry itself must load through the SAME validator the CLI/YAML
    path uses — a placeholder position, not a broken record."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    entry = dock._build_entry_dict(for_highlight=True)
    cp = load_coordinate_placement(entry)
    assert cp.cluster == "PIF_3V3_VDD"
    assert cp.role == "C_IN"
    assert cp.sheet == "MCU"
    assert (cp.x_mm, cp.y_mm) == (0.0, 0.0)


# ── §F.3 — the placeholder must NOT change clone_anchor_id ────────────────

def test_highlight_placeholder_does_not_change_clone_anchor_id(
        main_window, tmp_path):
    """An absolute (un-anchored) clone keys on name: — so the placeholder xy
    substituted for the empty Origin tab yields the SAME registry identity as
    a real absolute origin."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    hi = dock._build_entry_dict(for_highlight=True)
    assert hi["xy"] == [0.0, 0.0]

    dock.x_edit.setText("10")
    dock.y_edit.setText("-20")
    full = dock._build_entry_dict()
    assert full["xy"] == [10.0, -20.0]

    c_hi = load_clone_placement(hi)
    c_full = load_clone_placement(full)
    assert clone_anchor_id(c_hi) == clone_anchor_id(c_full)
    assert clone_anchor_id(c_hi) == "name:PIF_3V3_VDD"


def test_anchor_point_identity_ignores_form_xy(main_window, tmp_path):
    """§F.3, the anchor_point branch: the registry key is the POINT NAME + the
    shift, never the (hidden, unused) Absolute-XY form fields — filling them
    must not move the identity."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    dock.origin_widget.origin_mode_combo.setCurrentIndex(2)   # Point
    dock.point_edit.setCurrentText("origin_point")

    entry = dock._build_entry_dict()
    assert entry["anchor_point"] == "origin_point"
    key = clone_anchor_id(load_clone_placement(entry))
    assert key == "point:origin_point:0.0000:0.0000"

    dock.x_edit.setText("5")
    dock.y_edit.setText("6")
    entry2 = dock._build_entry_dict()
    assert clone_anchor_id(load_clone_placement(entry2)) == key


# ── §F.3 — Save/Redraw keep the full strictness ───────────────────────────

def test_strict_build_still_requires_absolute_xy(main_window, tmp_path, caplog):
    """Save/Redraw (for_highlight=False) still fatal on an empty Absolute XY
    origin — only the read-only highlight relaxed it."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    assert dock._build_entry_dict() is None
    assert any("X is required" in r.message for r in caplog.records)
    # Control: the same state is fine for the highlight path.
    assert dock._build_entry_dict(for_highlight=True)["xy"] == [0.0, 0.0]


def test_highlight_anchor_mode_without_identity_still_fails(
        main_window, tmp_path, caplog):
    """The anchor IS part of clone_anchor_id, so even the highlight path must
    not guess it — an empty Anchor mode keeps the identity fatal."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    dock.origin_widget.origin_mode_combo.setCurrentIndex(1)   # Anchor
    assert dock._build_entry_dict(for_highlight=True) is None
    assert any("Ref or Role" in r.message for r in caplog.records)


# ── §F.4 — the stale-mode message ─────────────────────────────────────────

def test_origin_mode_hint_when_anchor_filled_but_mode_is_xy(
        main_window, tmp_path, caplog):
    """Filling the Anchor fields while the Origin mode is still Absolute XY
    must advise switching the mode — not "X is required."."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    dock.anchor_role_edit.setCurrentText("C_IN")
    assert dock.origin_widget.mode == "xy"

    assert dock._build_entry_dict() is None
    assert any("switch the Origin mode" in r.message for r in caplog.records)
    assert not any("X is required" in r.message for r in caplog.records)
