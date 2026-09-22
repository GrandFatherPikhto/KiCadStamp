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

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

import gui.docks.placer as placer_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import load_clone_placement, load_coordinate_placement
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.placement.services.clone_position_calculator import clone_anchor_id
from tests.gui.conftest import _pump


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
        qapp, main_window, tmp_path, monkeypatch, caplog):
    """§F.1 regression: Cell mode, empty Origin (Absolute XY), "Select on
    board" must reach the resolver with the placement's identity instead of
    failing with "X is required.".

    Ш3 (plan_2026_09_22_board_door_finish): that resolver now runs on a WORKER
    under start_long_op, so the test waits for the token instead of asserting
    straight after the click (the property — the identity REACHES the resolver —
    is the same one, and _pump on the token is what makes it observable)."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    assert dock.origin_widget.mode == "xy"
    assert dock.x_edit.text() == "" and dock.y_edit.text() == ""
    seen = _spy_resolver(monkeypatch)

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert len(seen) == 1
    clone = seen[0]
    assert clone.cell == "pi_filter"
    assert clone.cluster == "PIF_3V3_VDD"
    assert clone.sheet == "MCU"
    assert not any("X is required" in r.message for r in caplog.records)


def test_select_on_board_coordinate_reaches_resolver_without_origin(
        qapp, main_window, tmp_path, monkeypatch, caplog):
    """Same for the CoordinatePlacement path: identity (cluster/role/sheet)
    only — the empty Cartesian position must not block the highlight. Same Ш3
    wait on the token."""
    dock, _ = _make_coordinate_dock(main_window, tmp_path)
    main_window.connection.board = _FakeBoard()
    form = dock.coordinate_form
    assert form.mode_combo.currentIndex() == 0     # Cartesian-absolute
    assert form.x_edit.text() == "" and form.y_edit.text() == ""
    seen = _spy_resolver(monkeypatch)

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

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


# ── Э2 (plan_2026_09_12_ui_thread_board_reads) — the shared-socket token ───

class _RecordingAdapter:
    """Stands in for the SHARED board adapter: records the one write this path
    is allowed to make (select_items) so a refused run can be told from a real
    one. Since Ш3 each row is (items, was_on_ui_thread): the write must leave the
    UI thread exactly like the read does."""

    def __init__(self):
        self.selected: list = []

    def select_items(self, items):
        on_ui_thread = QThread.currentThread() == QApplication.instance().thread()
        self.selected.append((list(items), on_ui_thread))


def _spy_resolver_items(monkeypatch, items):
    """The resolver is this action's board READ — the Э4.1 probe records every
    time it is entered and returns `items` so the write below is really reached
    on the un-refused path."""
    seen = []

    def _fake(adapter, cfg, ctx, placement, **kwargs):
        seen.append(placement)
        return list(items)

    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        _fake)
    return seen


def test_select_on_board_refuses_while_the_poll_tick_owns_the_socket(
        qapp, main_window, tmp_path, monkeypatch):
    """Э2/Э4.1 — `_on_select_on_board` resolves through the SHARED board
    adapter, so while the ~400 ms selection-poll tick holds that socket the
    click must reach neither the resolver nor select_items. The docstring used
    to claim this discipline ("same discipline as refresh_known_roles") while
    `long_op_active` was never checked anywhere in the file.

    Pre-fix this fails: the resolver ran on the UI thread with no token check."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    seen = _spy_resolver_items(monkeypatch, [object()])

    main_window.connection.long_op_active = True
    dock._on_select_on_board()

    assert seen == [], \
        "the highlight read the board while the poll tick owned the socket"
    assert adapter.selected == [], "the board was written to while a tick was in flight"
    assert main_window.connection.long_op_active is True, \
        "the refused click must not touch the token it does not own"

    # The guard is a guard, not a broken path: with the socket free the SAME
    # click reaches the resolver and highlights what it returned (on the worker
    # now — Ш3 — so the token is what we wait on).
    main_window.connection.long_op_active = False
    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert len(seen) == 1
    assert len(adapter.selected) == 1


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


# ── Ш3 (plan_2026_09_22_board_door_finish) — the read runs on a WORKER ─────
# The property: the resolver AND the highlight (select_items) leave the UI thread;
# the answer travels back as data and only the UI half touches the screen. Cells:
# the read is off the UI thread, the write is too, the success line survives the
# round trip, a refusal the worker can NAME stays a name (never the traceback
# path), a failure it cannot name lands in the Log instead of escaping into the
# Qt slot, and the door read that takes the adapter is signed.
# Each cell has a mutation in diagnostics/run_board_door_s3_mutations.py.

def _spy_resolver_on_thread(monkeypatch, items):
    """The resolver spy that also records WHICH THREAD it was entered on —
    (placement, on_ui_thread) rows. The thread is the whole point of Ш3: this
    read must not happen on the UI thread any more."""
    seen = []

    def _fake(adapter, cfg, ctx, placement, **kwargs):
        on_ui_thread = QThread.currentThread() == QApplication.instance().thread()
        seen.append((placement, on_ui_thread))
        return list(items)

    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        _fake)
    return seen


def test_the_highlight_read_runs_off_the_ui_thread(
        qapp, main_window, tmp_path, monkeypatch):
    """Cell 1 — the resolver is entered on a WORKER thread. Measured 21.09.2026
    its sweep cost 333 get_field_value() on the UI thread.

    Mutation check: put the resolver call back in _on_select_on_board (the
    synchronous shape) and the recorded flag becomes True (mutation M1 of
    diagnostics/run_board_door_s3_mutations.py)."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    seen = _spy_resolver_on_thread(monkeypatch, [object()])

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert len(seen) == 1
    assert seen[0][1] is False, "the highlight read still ran on the UI thread"


def test_the_highlight_write_runs_off_the_ui_thread(
        qapp, main_window, tmp_path, monkeypatch):
    """Cell 2 — select_items is the one board WRITE this path makes (134–225 ms
    measured, the most expensive single UI-thread action found): it must leave
    the UI thread as well.

    Mutation check: call adapter.select_items from the UI half and the recorded
    flag becomes True (mutation M2)."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    _spy_resolver_on_thread(monkeypatch, [object()])

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert len(adapter.selected) == 1
    assert adapter.selected[0][1] is False, "select_items still ran on the UI thread"


def test_the_success_line_survives_the_worker_round_trip(
        qapp, main_window, tmp_path, monkeypatch, caplog):
    """Cell 3 — count comes back as data and the UI half writes the same success
    line the synchronous version wrote (the name is computed on the UI side, so
    the worker never reads a widget).

    Mutation check: make the worker return count 0 and the success line vanishes
    (the WARN line takes its place) — mutation M3."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=_RecordingAdapter())
    _spy_resolver_on_thread(monkeypatch, [object()])

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert any("Selected 1 item(s) on the board for 'PIF_3V3_VDD'" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_a_resolver_refusal_becomes_an_error_line_not_a_crash(
        qapp, main_window, tmp_path, monkeypatch, caplog):
    """Cell 4 — a ValidationError from the resolver (nothing tagged, ambiguous)
    is turned into data and shown with the resolver's OWN wording, on the normal
    path. It must not take the generic traceback path: that would log a raw
    stack for what is a user's tagging mistake.

    Mutation check: let the ValidationError escape the worker instead of
    returning {"error": ...} and this fails on the traceback half (mutation M4)."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=_RecordingAdapter())

    def _ambiguous(*a, **k):
        from kicadstamp.exceptions import ValidationError
        raise ValidationError("ambiguous")
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        _ambiguous)

    dock._on_select_on_board()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert any("ambiguous" in r.message for r in caplog.records)
    assert not any("Long operation failed" in r.message for r in caplog.records), \
        "a refusal the worker can name must not go through the traceback path"


def test_an_unexpected_worker_failure_reaches_the_log_instead_of_escaping(
        qapp, main_window, tmp_path, monkeypatch, caplog):
    """Cell 5 — a failure the worker cannot name (a KiCad IPC error, say) lands
    in the Log through start_long_op's failure path. The old synchronous handler
    had no such path at all: the exception escaped into the Qt slot that called
    it, and an unhandled exception in a slot is a core dump (EXIT=134, measured
    21.09.2026).

    Mutation check: pass a no-op on_error instead of _on_select_on_board_failed
    and the Log line disappears (mutation M5)."""
    dock, _ = _make_cell_dock(main_window, tmp_path)
    main_window.connection.board = SimpleNamespace(adapter=_RecordingAdapter())

    def _boom(*a, **k):
        raise RuntimeError("board went away")
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        _boom)

    dock._on_select_on_board()          # must not raise
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert any("board went away" in r.message for r in caplog.records), \
        [r.message for r in caplog.records]


def test_the_highlight_door_read_is_signed(
        qapp, main_window, tmp_path, monkeypatch):
    """Cell 6 — the handler still reads connection.board on the UI side (to hand
    the adapter to the worker), and that read carries the door's sign. Pinned with
    the door ARMED over a REAL connection: a stand-in connection has no door at
    all, so it could not see the sign (the lesson of Ш1's mutation M8).

    Mutation check: drop the `with ui_thread_board_read(...)` wrapper and this
    fails with a refusal naming gui/docks/placer.py (mutation M6)."""
    from gui import connection as connection_mod
    from gui.connection import BoardConnection
    monkeypatch.setattr(connection_mod, "ui_thread_predicate", lambda: True)
    monkeypatch.setattr(connection_mod, "ui_thread_read_refusal",
                        connection_mod.UI_READ_RAISE)
    dock, _ = _make_cell_dock(main_window, tmp_path)
    connection = BoardConnection()
    connection.board = SimpleNamespace(adapter=_RecordingAdapter())
    main_window.connection = connection
    _spy_resolver_on_thread(monkeypatch, [])

    dock._on_select_on_board()          # must not raise
    _pump(qapp, lambda: not connection.long_op_active)
