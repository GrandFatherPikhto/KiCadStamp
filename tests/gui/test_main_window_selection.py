# tests/gui/test_main_window_selection.py
"""MainWindow._poll_board_selection()'s perf contract (1.1 in
techdocs/handoff/handoff_gui_optimization_2026_08_01.md): the 400ms
selection-watch tick must NEVER call board.select() — it builds its
`selected` list from the cached BoardConnection.snapshot (rebuilt only by
the ~2s poll / manual Refresh) — and must early-exit when neither the raw
selection nor the snapshot changed, so ExtractDock's per-selection widget
rebuilds aren't churned for nothing.

These tests use the real MainWindow (real_main_window fixture) with its
real BoardConnection, but a fake board behind it: no live KiCad needed,
exactly like the other tests/gui/ tests.
"""
import gui.main_window as main_window_mod
from gui.connection import BoardConnection
from kicadstamp.explore import Selected
from tests.gui.conftest import _pump


class _FakeFootprint:
    """Looks like the domain Footprint well enough for the tick — the test
    monkeypatches main_window_mod.Footprint to this class so the refs
    comprehension's isinstance() check treats it as a footprint. (The
    selection signature in kicadstamp.explore uses the REAL domain Footprint,
    so these fakes key by type name there — still deterministic, which is all
    the early-exit tests rely on.)"""

    def __init__(self, ref):
        self.ref = ref


class _FakeVia:
    """A non-footprint item (vias/tracks shape the tick's signature too)."""

    def __init__(self, net_name):
        self.net_name = net_name


class _FakeAdapter:
    def __init__(self, items):
        self._items = items
        self.get_selected_items_calls = 0

    def get_selected_items(self):
        self.get_selected_items_calls += 1
        return list(self._items)


class _FakeBoard:
    def __init__(self, items, snapshot):
        self.adapter = _FakeAdapter(items)
        self._snapshot = snapshot
        self.select_calls = 0

    def select(self):
        self.select_calls += 1
        return list(self._snapshot)


def _selected(ref, role="ROLE_A", cluster="C1"):
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[], nets={}, fp=None)


def _connected_window(real_main_window, monkeypatch, items, snapshot):
    """Stops the real timers (the test drives the tick synchronously),
    monkeypatches the module's FootprintInstance so the fakes count as
    footprints, and wires a fake board into a real BoardConnection."""
    real_main_window._timer.stop()
    real_main_window._selection_timer.stop()
    monkeypatch.setattr(main_window_mod, "Footprint", _FakeFootprint)

    board = _FakeBoard(items, snapshot)
    connection = BoardConnection(timeout_ms=10)
    connection.board = board
    connection._rebuild_snapshot()
    # The setup's own cache-build select() shouldn't count against the
    # tick's "0 calls" contract — reset the counter so assertions below only
    # see calls made from _poll_board_selection()/later manual refreshes.
    board.select_calls = 0
    real_main_window.connection = connection
    return real_main_window, board


def _record_set_board_selection(real_main_window, monkeypatch):
    calls = []
    # Phase F (2026-09-01): ExtractDock is removed — the selection-watch tick
    # feeds DockHub.set_board_selection (which stores the state for
    # "Extract tree..." and feeds PlacerDock).
    monkeypatch.setattr(real_main_window._dock_hub, "set_board_selection",
                        lambda raw, sel: calls.append((raw, list(sel))))
    return calls


def _tick(window, qapp):
    """_poll_board_selection() now dispatches its IPC through start_long_op
    (2026-08-03 fix) — fire it, then pump until the background op has fully
    released the socket (see conftest._pump's docstring for why that guard,
    not "the effect is visible", is the right one to wait on)."""
    window._poll_board_selection()
    _pump(qapp, lambda: not window.connection.long_op_active)


def test_selection_tick_uses_cached_snapshot_not_board_select(real_main_window, monkeypatch, qapp):
    """The headline acceptance of 1.1: zero board.select() calls from the
    400ms tick — `selected` is built by ref against the cached snapshot."""
    fp = _FakeFootprint("R1")
    window, board = _connected_window(real_main_window, monkeypatch,
                                      items=[fp], snapshot=[_selected("R1")])
    calls = _record_set_board_selection(window, monkeypatch)

    _tick(window, qapp)

    assert board.select_calls == 0
    assert board.adapter.get_selected_items_calls == 1  # the one cheap IPC, still polled
    assert len(calls) == 1
    raw, sel = calls[0]
    assert raw == [fp]
    assert [s.ref for s in sel] == ["R1"]


def test_selection_tick_early_exits_when_nothing_changed(real_main_window, monkeypatch, qapp):
    """Same selection + same snapshot version -> the second tick must not
    touch ExtractDock at all (and must not call board.select() either)."""
    fp = _FakeFootprint("R1")
    window, board = _connected_window(real_main_window, monkeypatch,
                                      items=[fp], snapshot=[_selected("R1")])
    calls = _record_set_board_selection(window, monkeypatch)

    _tick(window, qapp)
    _tick(window, qapp)
    _tick(window, qapp)

    assert len(calls) == 1  # only the first tick pushed the selection
    assert board.select_calls == 0


def test_selection_tick_updates_when_selection_changes(real_main_window, monkeypatch, qapp):
    """A different raw selection (new footprint + a via) must repush to
    ExtractDock even though the snapshot version is unchanged — the guard
    keys on the whole raw selection, not just footprint refs."""
    fp1 = _FakeFootprint("R1")
    window, board = _connected_window(real_main_window, monkeypatch,
                                      items=[fp1], snapshot=[_selected("R1")])
    calls = _record_set_board_selection(window, monkeypatch)

    _tick(window, qapp)

    board.adapter._items = [_FakeFootprint("R2"), _FakeVia("GND")]
    board._snapshot = [_selected("R2", role="ROLE_B", cluster="C2")]
    window.connection._rebuild_snapshot()  # a manual Refresh would do this

    _tick(window, qapp)

    assert len(calls) == 2
    assert [s.ref for s in calls[1][1]] == ["R2"]
    # The only select() is the one _rebuild_snapshot() did for the manual
    # Refresh — the ticks themselves never call it.
    assert board.select_calls == 1


def test_selection_tick_updates_when_snapshot_refreshed_same_refs(real_main_window, monkeypatch, qapp):
    """Same footprint refs but a refreshed snapshot (version bumped, e.g. a
    Role/Cluster edit picked up by a manual Refresh) must re-push fresh
    Selected objects to ExtractDock — not early-exit on the refs alone."""
    fp = _FakeFootprint("R1")
    window, board = _connected_window(real_main_window, monkeypatch,
                                      items=[fp], snapshot=[_selected("R1")])
    calls = _record_set_board_selection(window, monkeypatch)

    _tick(window, qapp)

    board._snapshot = [_selected("R1", role="EDITED_ROLE", cluster="EDITED")]
    window.connection._rebuild_snapshot()

    _tick(window, qapp)

    assert len(calls) == 2
    assert calls[1][1][0].role == "EDITED_ROLE"
    assert board.select_calls == 1


def test_selection_tick_feeds_embedded_fieldstool_targets(real_main_window, monkeypatch, qapp):
    """Phase 5.1 — the single 400ms tick feeds the embedded fieldstool's
    live-selection cross-probe too (its own timer is stopped when it shares
    the main connection, so this is now the only path)."""
    fp = _FakeFootprint("R1")
    window, board = _connected_window(real_main_window, monkeypatch,
                                      items=[fp], snapshot=[_selected("R1")])
    fs_window = real_main_window.fieldstool_dock.window

    _tick(window, qapp)

    assert fs_window._current_targets == ["R1"]
