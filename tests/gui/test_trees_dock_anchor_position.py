# tests/gui/test_trees_dock_anchor_position.py
"""Tools → Trees → "Anchor position": the read-only live anchor indicator now
runs on a worker (plan_2026_09_12_anchor_position_on_worker, stages Э1–Э5).

Before 2026-09-12 `TreesDock._refresh_anchor_live_position` built a
`KiCadBoardAdapter` and read the board SYNCHRONOUSLY on the UI thread, with no
`long_op_active` token at all — the only such place in the GUI (the four other
adapter constructions all live inside worker functions). What is pinned down
HERE:

  * the read goes through `start_long_op`, so the token is held for the whole
    read and released afterwards (Э4.1);
  * the Tools-menu QAction `anchor_position_action` is the guard widget (Э4.2);
  * the worker touches NO widget and returns PLAIN data (Э4.3);
  * a failing read still shows "unavailable" and logs a warning — on BOTH paths
    (the worker's own exception and start_long_op's on_error) — and never
    crashes the dock (Э4.4);
  * the two early exits (no tree / origin anchor) stay SYNCHRONOUS and start no
    worker at all (Э4.5).
"""
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtGui import QAction

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeAnchor

import gui.docks.trees_dock as td_mod
from gui.docks.trees_dock import TreesDock
from gui.worker import start_long_op as real_start_long_op
from tests.gui.conftest import _pump

# An anchor that is NOT origin and NOT self — the only case that needs the board
# (a ref anchor; the worker is monkeypatched in the tests that get this far).
REF_ANCHOR_TREES = {"trees": [
    {"name": "power_tree", "anchor": {"ref": "U1"},
     "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [100.0, 50.0]}]}]}

ORIGIN_TREES = {"trees": [
    {"name": "misc", "anchor": {"origin": True},
     "nodes": [{"ref": "R_DEBUG", "kind": "external", "xy": [100.0, 50.0]}]}]}

UNAVAILABLE_TEXT = _("anchor: live position unavailable")


def _dock_with(main_window, tmp_path, trees):
    """A TreesDock pointed at a throwaway root config carrying `trees` — the
    same construction tests/gui/test_trees_dock.py::_dock_with uses."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(trees), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


class _FakeAdapter:
    """Stands in for KiCadBoardAdapter in the worker: records how it was built
    and whether the board was refreshed, touches nothing else."""

    def __init__(self, timeout_ms=None):
        self.timeout_ms = timeout_ms
        self.refreshed = False

    def refresh_board(self) -> None:
        self.refreshed = True


def _tree(ref=None):
    return Tree(name="t", anchor=TreeAnchor(ref=ref), nodes=[])


# ── Э4.1: the read runs under start_long_op ────────────────────────────────

def test_reading_runs_under_start_long_op(main_window, tmp_path, monkeypatch,
                                          qapp):
    """The whole read is bracketed by `long_op_active`: raised BEFORE the read
    starts, held while it runs, cleared once the operation finishes. This is the
    token that keeps the ~400ms polling tick out of the read's in-flight
    transaction — on the pre-2026-09-12 synchronous code nothing ever raised it,
    so this test fails there.

    The probe is the adapter itself (NOT the worker function): the adapter is
    what actually reads the board, and it records the token's state where it is
    built and where it reads — so the test needs no knowledge of the new worker
    symbol and fails on the old code for the RIGHT reason."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    connection = main_window.connection
    seen = {}

    class _TokenWatchingAdapter:
        def __init__(self, timeout_ms=None):
            seen["token_when_built"] = connection.long_op_active

        def refresh_board(self):
            seen["token_when_reading"] = connection.long_op_active
            raise ValidationError("no board in this test")

    monkeypatch.setattr(td_mod, "KiCadBoardAdapter", _TokenWatchingAdapter)

    dock._refresh_anchor_live_position()

    assert connection.long_op_active is True, \
        "the anchor read does not take the shared-socket token"
    _pump(qapp, lambda: not connection.long_op_active)

    assert seen["token_when_built"] is True, \
        "the adapter was built outside the operation's token"
    assert seen["token_when_reading"] is True, \
        "the board was read without the shared-socket token"
    assert connection.long_op_active is False, \
        "the token was not released after the read"


def test_finish_writes_the_live_position_to_the_label(
        main_window, tmp_path, monkeypatch, qapp):
    """The UI-thread half writes the numbers the worker returned — a ref anchor
    is named, the mode-generic readout is used when there is no ref."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    connection = main_window.connection

    monkeypatch.setattr(td_mod, "run_anchor_live_position_worker",
                        lambda _payload: {"available": True, "x": 1_000_000,
                                          "y": 2_000_000, "rotation": 90.0,
                                          "ref": "U1"})

    dock._refresh_anchor_live_position()
    _pump(qapp, lambda: not connection.long_op_active)

    assert dock.anchor_pos_label.text() == _(
        "anchor {ref!r}: ({x:.3f}, {y:.3f}) mm @ {rot}°").format(
            ref="U1", x=1.0, y=2.0, rot="90.0")


def test_finish_without_a_ref_shows_the_mode_generic_readout(
        main_window, tmp_path, monkeypatch, qapp):
    """A role/point/auto anchor has no ref to name — the label falls back to the
    mode-generic line (and a None rotation renders as an em dash, never 0)."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    connection = main_window.connection

    monkeypatch.setattr(td_mod, "run_anchor_live_position_worker",
                        lambda _payload: {"available": True, "x": 0,
                                          "y": 0, "rotation": None, "ref": None})

    dock._refresh_anchor_live_position()
    _pump(qapp, lambda: not connection.long_op_active)

    assert dock.anchor_pos_label.text() == _(
        "anchor: ({x:.3f}, {y:.3f}) mm @ {rot}°").format(
            x=0.0, y=0.0, rot="—")


# ── Э4.2: the menu QAction is the guard widget ─────────────────────────────

def test_dock_hub_hands_the_anchor_position_action_down(real_main_window,
                                                        monkeypatch):
    """DockHub.anchor_position passes ITS OWN QAction down to the dock — the
    entry the user clicked, exactly like the other Tools → Trees flows."""
    hub = real_main_window._dock_hub
    seen = []
    monkeypatch.setattr(hub.trees_dock, "_refresh_anchor_live_position",
                        lambda *args, **kwargs: seen.append(args))

    hub.anchor_position()

    assert seen == [(real_main_window.anchor_position_action,)]


def test_anchor_position_action_is_disabled_while_the_read_runs(
        main_window, tmp_path, monkeypatch, qapp):
    """The action handed in reaches start_long_op as the ONLY guard widget: it
    is disabled for the whole read (a second click on the menu entry is
    therefore refused while the shared socket is held) and enabled again once
    the read finishes."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    action = QAction("Anchor position")
    assert action.isEnabled()

    widgets_seen, enabled_seen = [], []

    def spy_start_long_op(connection, widgets, fn, on_success, on_error,
                          *args, **kwargs):
        widgets_seen.append(list(widgets))
        return real_start_long_op(connection, widgets, fn, on_success, on_error,
                                  *args, **kwargs)

    def fake_worker(_payload):
        enabled_seen.append(action.isEnabled())
        return {"available": False, "reason": "no board here"}

    monkeypatch.setattr(td_mod, "start_long_op", spy_start_long_op)
    monkeypatch.setattr(td_mod, "run_anchor_live_position_worker", fake_worker)

    dock._refresh_anchor_live_position(action)
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert widgets_seen == [[action]]
    assert enabled_seen == [False], "the QAction stayed enabled while the read ran"
    assert action.isEnabled(), "the QAction was not restored after the read"


def test_anchor_position_action_stays_disabled_when_it_was_disabled(
        main_window, tmp_path, monkeypatch, qapp):
    """A guard widget that was DISABLED before the read comes back disabled —
    start_long_op restores the prior state, it does not blindly enable."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    action = QAction("Anchor position")
    action.setEnabled(False)

    monkeypatch.setattr(td_mod, "run_anchor_live_position_worker",
                        lambda _payload: {"available": False, "reason": "n/a"})

    dock._refresh_anchor_live_position(action)
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert not action.isEnabled()

    dock._refresh_anchor_live_position()
    _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert not action.isEnabled(), \
        "a trigger-less call must not touch the menu action"


# ── Э4.3: the worker touches no widget ─────────────────────────────────────

def test_worker_returns_plain_data_and_touches_no_widget(monkeypatch):
    """The worker is a plain function: no dock, no Qt parent, no widget — and
    what it hands back is plain data (numbers, a string/None), never a board
    object or a widget. Its own adapter is built with the unchanged 20s
    timeout."""
    adapter_seen = []
    monkeypatch.setattr(td_mod, "KiCadBoardAdapter",
                        lambda timeout_ms=None: adapter_seen.append(
                            _FakeAdapter(timeout_ms)) or adapter_seen[-1])
    monkeypatch.setattr(td_mod, "_anchor_base_live_position",
                        lambda adapter, cfg, tree, sheet_names: (
                            Vector2.from_xy(1_000_000, -2_500_000), 90.0))

    result = td_mod.run_anchor_live_position_worker(
        {"cfg": object(), "tree": _tree(ref="U1"), "sheet_names": {}})

    assert result == {"available": True, "x": 1_000_000, "y": -2_500_000,
                      "rotation": 90.0, "ref": "U1"}
    assert all(isinstance(v, (int, float, str, bool, type(None)))
               for v in result.values()), "the result is not plain data"
    assert adapter_seen[0].timeout_ms == 20000
    assert adapter_seen[0].refreshed is True


# ── Э4.4: a failed read stays "unavailable" ────────────────────────────────

def test_worker_turns_a_failed_board_read_into_unavailable(monkeypatch, caplog):
    """An exception INSIDE the worker becomes a normal result with a reason —
    not a failed operation — and leaves one warning in the Log (Э2)."""
    monkeypatch.setattr(td_mod, "KiCadBoardAdapter", _FakeAdapter)

    def boom(*_args, **_kwargs):
        raise ValidationError("anchor 'U1' is not on the board")

    monkeypatch.setattr(td_mod, "_anchor_base_live_position", boom)

    with caplog.at_level(logging.WARNING):
        result = td_mod.run_anchor_live_position_worker(
            {"cfg": object(), "tree": _tree(ref="U1"), "sheet_names": {}})

    assert result["available"] is False
    assert "not on the board" in result["reason"]
    assert any("unavailable" in record.getMessage()
               for record in caplog.records
               if record.levelno == logging.WARNING)


def test_failed_read_shows_unavailable_and_never_crashes_the_dock(
        main_window, tmp_path, monkeypatch, caplog, qapp):
    """The full flow with a broken board read: the dock shows the same
    "unavailable" it always showed, the reason lands in the Log as a warning,
    and nothing propagates out of the finish path (the indicator never crashes
    the dock — its own docstring contract)."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)
    monkeypatch.setattr(td_mod, "KiCadBoardAdapter", _FakeAdapter)

    def boom(*_args, **_kwargs):
        raise ValidationError("KiCad IPC failure")

    monkeypatch.setattr(td_mod, "_anchor_base_live_position", boom)

    with caplog.at_level(logging.WARNING):
        dock._refresh_anchor_live_position()
        _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert dock.anchor_pos_label.text() == UNAVAILABLE_TEXT
    assert any("unavailable" in record.getMessage()
               for record in caplog.records
               if record.levelno == logging.WARNING)
    # the dock is still usable — the next read is refused only by the token,
    # never by a dead indicator
    assert main_window.connection.long_op_active is False


def test_on_error_path_shows_unavailable_and_logs(main_window, tmp_path,
                                                  monkeypatch, caplog, qapp):
    """The OTHER failure path: the worker itself blows up (an unexpected error
    _LongOpWorker routes to on_error). The label still says "unavailable" and the
    warning is still logged."""
    dock, _root = _dock_with(main_window, tmp_path, REF_ANCHOR_TREES)

    def boom(_payload):
        raise RuntimeError("kicad went away")

    monkeypatch.setattr(td_mod, "run_anchor_live_position_worker", boom)

    with caplog.at_level(logging.WARNING):
        dock._refresh_anchor_live_position()
        _pump(qapp, lambda: not main_window.connection.long_op_active)

    assert dock.anchor_pos_label.text() == UNAVAILABLE_TEXT
    assert any("unavailable" in record.getMessage()
               for record in caplog.records
               if record.levelno == logging.WARNING)
    assert dock._active_op is None


# ── Э4.5: the early exits stay synchronous ─────────────────────────────────

def test_no_tree_clears_the_label_without_a_worker(main_window, monkeypatch):
    """No tree to read → the label is cleared on the spot; no worker, no
    indicator flash, no socket."""
    dock = TreesDock(main_window)
    dock.set_root_file(None)
    assert dock._current_tree() is None
    dock.anchor_pos_label.setText("stale")

    calls = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *args, **kwargs: calls.append(args))

    dock._refresh_anchor_live_position()

    assert dock.anchor_pos_label.text() == ""
    assert calls == []
    assert main_window.connection.long_op_active is False


def test_origin_anchor_is_shown_without_a_worker(main_window, tmp_path,
                                                 monkeypatch):
    """An origin anchor is trivially (0,0)/0° — answered synchronously, with no
    worker and no busy indicator (Э3)."""
    dock, _root = _dock_with(main_window, tmp_path, ORIGIN_TREES)

    calls = []
    monkeypatch.setattr(td_mod, "start_long_op",
                        lambda *args, **kwargs: calls.append(args))

    dock._refresh_anchor_live_position()

    assert dock.anchor_pos_label.text() == _("anchor (origin): (0, 0) mm @ 0°")
    assert calls == []
    assert main_window.connection.long_op_active is False
