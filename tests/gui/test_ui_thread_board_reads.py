# tests/gui/test_ui_thread_board_reads.py
"""The board reads that used to run SYNCHRONOUSLY on the UI thread — one place
at a time (plan_2026_09_12_ui_thread_board_reads, stages Э1–Э4).

Two different diseases live behind the same symptom (the plan's P.0), so the
cure differs per place and this file pins each one down for what it is:

  * ILLNESS 1 — INTERLEAVING. The read goes to the SHARED board adapter while
    the ~400 ms selection-poll tick (`main_window.py`) holds its own in-flight
    kipy REQ transaction on the same socket; the symptom is
    "Error receiving reply from KiCad: Operation canceled". The cure is to
    RESPECT the token: `if socket_busy(connection): return`. Pinned here by the
    Э4.1 test per place — the token is TRUE (the tick is in flight) and the read
    must NOT reach the board.
  * ILLNESS 2 — FREEZING. The read runs on the UI thread, so the window stops
    answering with no busy indicator (that hangs off `LongOpController`). The
    cure is a worker under `start_long_op`; pinned by a test that the read
    happens on ANOTHER thread, with the token held for the whole of it and
    plain data (numbers) handed back to the UI half.

The Э4.1 probe deliberately does NOT assert on a new symbol: it is the read
seam itself (`_resolve_live_offset` / `_anchor_base_live_position` /
`confirm_first_run_adoption`'s copper calls / the resolver+adapter pair), which
records the token's state at the moment it is entered — so every "does not
touch the board" test fails on the pre-2026-09-12 code for the right reason.
"""
import logging
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

import gui.docks.trees_dock as td_mod
from gui.docks.trees_dock import TreesDock, _NodeDialog
from tests.gui.conftest import _pump

# A ref anchor (NOT origin) with ONE external node — the external kind needs no
# config record and no cluster, so the read path only ever touches the adapter.
BRANCH_TREES = {"trees": [
    {"name": "power_tree", "anchor": {"ref": "U1"},
     "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [1.0, 2.0]}]}]}


def _dock_with(main_window, tmp_path, trees=None):
    """A TreesDock pointed at a throwaway root config carrying `trees` — the
    same construction tests/gui/test_trees_dock.py::_dock_with uses."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(trees if trees is not None else BRANCH_TREES),
                    encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


def _board(main_window, adapter):
    """Point the fake connection at a board carrying `adapter` — the dock's
    _live_adapter() then hands that very object to every read."""
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    return adapter


def _spy_read(seen, result):
    """Replace one read helper with a spy that records (token, thread) — the
    Э4.1 probe — and returns `result`. `seen` stays EMPTY on code that refuses
    the read, which is exactly what the tests assert."""
    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return result
    return _spy


class _CopperAdapter:
    """Records the two whole-board copper reads the Bug-3 first-run heads-up
    makes (confirm_first_run_adoption -> get_tracks()/get_vias())."""

    def __init__(self, tracks=1, vias=0):
        self.tracks = tracks
        self.vias = vias
        self.track_calls = 0
        self.via_calls = 0

    def get_tracks(self):
        self.track_calls += 1
        return [object()] * self.tracks

    def get_vias(self):
        self.via_calls += 1
        return [object()] * self.vias


# ── Э1а :2629 — _reread_node_flow: ILLNESS 1 + ILLNESS 2 ────────────────────

def test_reread_node_flow_refuses_while_the_poll_tick_owns_the_socket(
        main_window, tmp_path, monkeypatch):
    """Э4.1 — the context-menu "Reread current position" reads the SHARED
    adapter, so while the polling tick holds it NOTHING may be sent: the read
    seam is never entered and the node keeps its stored values.

    Pre-fix this fails: the read ran synchronously on the UI thread with no
    token check at all (the live "Operation canceled" interleaving)."""
    _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    before = (node.xy, node.polar, node.rotation)

    seen = []
    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        _spy_read(seen, ((5.0, 6.0), 45.0)))
    main_window.connection.long_op_active = True

    dock._reread_node_flow(tree, node)

    assert seen == [], \
        "the reread read the board while the poll tick owned the socket"
    assert (node.xy, node.polar, node.rotation) == before
    assert dock._dirty is False
    assert main_window.connection.long_op_active is True, \
        "the refused reread must not touch the token it does not own"


def test_reread_node_flow_reads_on_a_worker_under_the_token(
        main_window, tmp_path, monkeypatch, qapp):
    """ILLNESS 2 — the read itself runs on a worker: the token is raised
    BEFORE it, held while it runs (the seam records it) and released once the
    UI half has written the node; the read is on another thread than the one
    that called the handler.

    Pre-fix this fails on the seam's own record: token FALSE (nobody raised
    it) and the main thread (the window was frozen for the read)."""
    _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    node.rotation = 1.0
    connection = main_window.connection

    seen = []

    def _spy(*args, **kwargs):
        seen.append({"token": connection.long_op_active,
                     "thread": threading.current_thread()})
        return ((5.0, 6.0), 45.0)

    monkeypatch.setattr(td_mod, "_resolve_live_offset", _spy)

    dock._reread_node_flow(tree, node)

    assert connection.long_op_active is True, \
        "the reread does not take the shared-socket token"
    # The UI half has NOT run yet — its signal is queued until the event loop
    # spins, so the node still holds its stored values here.
    assert node.xy == (1.0, 2.0)

    _pump(qapp, lambda: not connection.long_op_active)

    assert seen and seen[0]["token"] is True, \
        "the board was read without the shared-socket token"
    assert seen[0]["thread"] is not threading.main_thread(), \
        "the board was read on the UI thread"
    assert node.xy == (5.0, 6.0)
    assert node.polar is None
    assert node.rotation == 45.0
    assert dock._dirty is True
    assert connection.long_op_active is False


def test_reread_node_flow_failure_keeps_the_node_and_warns(
        main_window, tmp_path, monkeypatch, qapp):
    """Э4.3 — behaviour on refusal is unchanged: the SAME modal warning text,
    the node left untouched, the dock not marked dirty."""
    _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]
    node.rotation = 12.0
    before = (node.xy, node.polar, node.rotation)

    warnings = []
    monkeypatch.setattr(td_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)

    def _boom(*args, **kwargs):
        raise ValidationError("ref not on board")

    monkeypatch.setattr(td_mod, "_resolve_live_offset", _boom)

    dock._reread_node_flow(tree, node)
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert warnings and warnings[0][1] == _("Reread current position")
    assert "ref not on board" in str(warnings[0][2])
    assert (node.xy, node.polar, node.rotation) == before
    assert dock._dirty is False


def test_reread_node_flow_without_a_board_logs_instead_of_opening_a_worker(
        main_window, tmp_path, monkeypatch, caplog):
    """The no-connection answer stays SYNCHRONOUS (it needs no board): one Log
    error, no worker, no modal."""
    main_window.connection.board = None
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    node = tree.nodes[0]

    calls = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *a, **k: calls.append(a))

    caplog.clear()
    dock._reread_node_flow(tree, node)

    assert calls == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("No live board connection" in r.message for r in errors)


# ── Э1б :4419 — NodeFormWidget._on_read_position: ILLNESS 1 ────────────────

def _node_dialog(dock, adapter, tree, parent_node=None):
    return _NodeDialog(dock, dock._all_ref_candidates(), dock._used_refs(),
                       "Add child", cfg=dock._cfg, adapter=adapter,
                       sheet_names={}, tree=tree, parent_node=parent_node)


def test_node_form_read_position_refuses_while_the_poll_tick_owns_the_socket(
        main_window, tmp_path, monkeypatch):
    """Э4.1 — the node form's "Read current position" reads the SHARED adapter
    on the UI thread (the form lives in a modal dialog, where the plan asks for
    the token check as the minimum). With the tick in flight nothing is sent
    and no field is written.

    Pre-fix this fails: the read went straight to the board."""
    adapter = _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _node_dialog(dock, adapter, tree)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("external"))
    dlg.ref_combo.setCurrentText("R_DEBUG")

    seen = []
    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        _spy_read(seen, ((5.0, 6.0), 45.0)))
    main_window.connection.long_op_active = True

    dlg._on_read_position()

    assert seen == [], \
        "the form read the board while the poll tick owned the socket"
    assert dlg.offset_widget.x_edit.text() == "0"
    assert dlg.rotation_edit.text() == ""


def test_node_form_read_position_still_fills_the_fields_when_the_socket_is_free(
        main_window, tmp_path, monkeypatch):
    """The guard is a guard, not a broken path: with the socket free the read
    runs exactly as before and fills the board-frame offset/rotation."""
    adapter = _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    dlg = _node_dialog(dock, adapter, tree)
    dlg.kind_combo.setCurrentIndex(dlg.kind_combo.findData("external"))
    dlg.ref_combo.setCurrentText("R_DEBUG")

    seen = []
    monkeypatch.setattr(td_mod, "_resolve_live_offset",
                        _spy_read(seen, ((10.0, 5.0), 90.0)))
    assert main_window.connection.long_op_active is False

    dlg._on_read_position()

    assert len(seen) == 1
    assert dlg.offset_widget.x_edit.text() == "10.000"
    assert dlg.offset_widget.y_edit.text() == "5.000"
    assert dlg.rotation_edit.text() == "90.000"


# ── Э1в :5490 — AnchorFormWidget._conversion_base_deg: ILLNESS 1 ───────────

def test_anchor_form_base_orientation_refuses_while_the_poll_tick_owns_the_socket(
        main_window, tmp_path, monkeypatch):
    """Э4.1 — the anchor form's board-frame base (eff_rot / anchor_rot) is an
    adapter call behind a 250 ms debounce; with the tick in flight it is NOT
    made and the form falls back to its own "no live base" state (None), which
    every caller already handles safely (raw values shown, conversions
    refused) — the 9887468 trap is never re-interpreted.

    Pre-fix this fails: the base orientation was read on the UI thread."""
    adapter = _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    form = dock._build_anchor_form(tree)

    seen = []
    monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                        _spy_read(seen, (Vector2.from_xy(0, 0), 90.0)))
    main_window.connection.long_op_active = True

    assert form._conversion_base_deg() is None
    assert seen == [], \
        "the base orientation was read while the poll tick owned the socket"
    assert form._adapter is adapter


def test_anchor_form_base_orientation_resolves_when_the_socket_is_free(
        main_window, tmp_path, monkeypatch):
    """With the socket free the base resolves as before: the anchor's live
    angle plus the tree's own stored rotation (here 0.0)."""
    adapter = _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    form = dock._build_anchor_form(tree)

    seen = []
    monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                        _spy_read(seen, (Vector2.from_xy(0, 0), 90.0)))

    assert form._conversion_base_deg() == (90.0, 90.0)
    assert len(seen) == 1


# ── Э1г :3251 — TreesDock._anchor_base_mm: ILLNESS 1 ───────────────────────

def test_anchor_base_mm_refuses_while_the_poll_tick_owns_the_socket(
        main_window, tmp_path, monkeypatch):
    """Э4.1 — the "from selection" placement mode of the Instantiate dialog
    resolves the tree anchor's LIVE base through the shared adapter. With the
    tick in flight the answer is None — the existing "cannot resolve the tree
    anchor live" refusal — and no read is made.

    Pre-fix this fails: the base was read on the UI thread."""
    _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()

    seen = []
    monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                        _spy_read(seen, (Vector2.from_xy_mm(5.0, 6.0), 0.0)))
    main_window.connection.long_op_active = True

    assert dock._anchor_base_mm(tree) is None
    assert seen == [], \
        "the instantiate base was read while the poll tick owned the socket"


def test_anchor_base_mm_resolves_when_the_socket_is_free(
        main_window, tmp_path, monkeypatch):
    """With the socket free the base comes back in mm, unchanged."""
    _board(main_window, object())
    dock, _root = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()

    seen = []
    monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                        _spy_read(seen, (Vector2.from_xy_mm(5.0, 6.0), 0.0)))

    assert dock._anchor_base_mm(tree) == (5.0, 6.0)
    assert len(seen) == 1


# ── Э1д :3502 — _confirm_first_run_redraw: ILLNESS 1 ───────────────────────

def test_first_run_heads_up_does_not_read_copper_while_the_poll_tick_owns_the_socket(
        main_window, tmp_path, monkeypatch):
    """Э4.1 — the Bug-3 heads-up reads the WHOLE board's copper
    (get_tracks()+get_vias()) through the shared adapter right before the
    redraw worker starts. With the tick in flight the probe is skipped and the
    redraw proceeds SILENTLY — the same answer the existing "cannot read ->
    never block a redraw" branch gives, so no behaviour is invented.

    Pre-fix this fails: the copper was read on the UI thread (and could freeze
    the window on a large board)."""
    adapter = _CopperAdapter(tracks=3)
    _board(main_window, adapter)
    dock, _root = _dock_with(main_window, tmp_path)

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a)
                        or QMessageBox.StandardButton.Yes)
    main_window.connection.long_op_active = True

    assert dock._confirm_first_run_redraw() is True
    assert adapter.track_calls == 0, \
        "the copper was read while the poll tick owned the socket"
    assert adapter.via_calls == 0
    assert asked == [], "a probe that could not run must not nag"


def test_first_run_heads_up_still_reads_and_asks_when_the_socket_is_free(
        main_window, tmp_path, monkeypatch):
    """The guard is a guard, not a broken path: with the socket free the
    copper IS read and the heads-up IS asked — and Cancel still refuses the
    redraw."""
    adapter = _CopperAdapter(tracks=3)
    _board(main_window, adapter)
    dock, _root = _dock_with(main_window, tmp_path)

    calls = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: calls.append(a)
                        or QMessageBox.StandardButton.Yes)

    assert dock._confirm_first_run_redraw() is True
    assert adapter.track_calls == 1
    assert len(calls) == 1

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert dock._confirm_first_run_redraw() is False
