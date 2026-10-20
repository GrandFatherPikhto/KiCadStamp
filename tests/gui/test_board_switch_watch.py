#!/usr/bin/env python3
"""В of plan_2026_10_15_honest_live_read: the automatic tick notices a BOARD
SWITCH in KiCad — one cheap request, a full refresh only on a change.

Denis's correction to Ч0 (20.10): the stored `Board.name` cannot see a switch, so
the tick must ASK; the automatic tick used to be a no-op because a full refresh is
expensive, so В removes the early return only for a request whose price is known
(kicadstamp.diagnostics.probe_identity_tick_cost measured 0.127 ms median versus
~30 ms for a refresh plus the board read it forces).

The DECISION lives on `BoardConnection.refresh_if_board_changed` (board access
belongs to the connection, which the UI-thread door exempts); this file pins both
halves — the decision and the result shape `_finish_poll` consumes:

  В1  same board → no refresh at all;
  В2  another board → ONE refresh, its error travels back;
  В3  a kipy refusal (no PCB frame / mid-switch) is "nothing to say" — no error,
      no disconnect, no refresh;
  В4  an absent board never asks KiCad at all;
  В5  the tick's result shape: quiet / changed-with-names / changed-but-failed;
  В6  `MainWindow._poll` hands the identity tick to the poll worker (not the
      plain refresh tick) while connected, and the plain tick while not;
  В7  `_finish_poll` on an `identity_only` result touches no widget and feeds no
      latency window.
"""
import pytest
from types import SimpleNamespace

from gui import board_switch_watch, connection as connection_mod, slow_poll
from gui.board_switch_watch import identity_tick
from gui.connection import BoardConnection


class _Adapter:
    def __init__(self, name):
        self._name = name
        self._kicad = object()
        self.closed = False

    def get_board_filename(self):
        return self._name

    def close(self):
        self.closed = True


class _Board:
    def __init__(self, name):
        self.adapter = _Adapter(name)

    def refresh(self):
        pass


def _connection(name="A.kicad_pcb"):
    connection = BoardConnection.__new__(BoardConnection)
    connection.board = _Board(name) if name is not None else None
    return connection


# ── В1/В2: the decision — one request, a refresh only on a change ───────────

def test_the_same_board_is_not_refreshed(monkeypatch):
    """В1: the name KiCad reports equals the one the connection holds, so the
    tick ends there: no refresh (which would drop the caches and rebuild the
    snapshot), nothing else. The connection never learns a new name."""
    monkeypatch.setattr(connection_mod, "read_open_board_name",
                        lambda _kicad: "A.kicad_pcb")
    connection = _connection()
    refreshes = []
    monkeypatch.setattr(connection, "refresh",
                        lambda: refreshes.append(1) or None)

    assert connection.refresh_if_board_changed() == (False, None)
    assert refreshes == []


def test_another_board_is_re_read_once(monkeypatch):
    """В2: the editor reports a different board — ONE `refresh()` (which also
    rebuilds the snapshot), and the refresh's own error comes back with it."""
    monkeypatch.setattr(connection_mod, "read_open_board_name",
                        lambda _kicad: "B.kicad_pcb")
    connection = _connection()
    refreshes = []

    def _refresh():
        refreshes.append(1)
        return "KiCad is busy"

    monkeypatch.setattr(connection, "refresh", _refresh)

    assert connection.refresh_if_board_changed() == (True, "KiCad is busy")
    assert refreshes == [1]


def test_a_path_shaped_name_is_the_same_board(monkeypatch):
    """В2 (shape): the comparison is by basename — KiCad owns the shape of this
    field, and a path-shaped answer must not read as a different board."""
    monkeypatch.setattr(connection_mod, "read_open_board_name",
                        lambda _kicad: "/home/denis/boards/A.kicad_pcb")
    connection = _connection()
    monkeypatch.setattr(connection, "refresh",
                        lambda: pytest.fail("the same board was re-read"))

    assert connection.refresh_if_board_changed() == (False, None)


# ── В3: a refusal is not a lost connection ──────────────────────────────────

def test_a_refused_request_says_nothing_and_disconnects_nothing(monkeypatch):
    """В3: kipy answers `GetOpenDocuments` with an ApiError while the PCB editor
    has no frame or is switching documents (measured: probe_document_switch).
    A courtesy check must survive that — no error, no refresh, no reconnect."""
    def _refuse(_kicad):
        raise RuntimeError("no handler available")

    monkeypatch.setattr(connection_mod, "read_open_board_name", _refuse)
    connection = _connection()
    monkeypatch.setattr(connection, "refresh",
                        lambda: pytest.fail("a refusal re-read the board"))

    assert connection.refresh_if_board_changed() == (False, None)
    assert connection.board is not None


# ── В4: nothing to ask about ────────────────────────────────────────────────

def test_an_absent_board_never_reaches_kicad(monkeypatch):
    """В4: between a disconnect and the next connect there is no board — the
    plain tick owns connecting, so this one must not ask anything."""
    def _boom(_kicad):
        raise AssertionError("no board handle existed to ask")

    monkeypatch.setattr(connection_mod, "read_open_board_name", _boom)

    assert _connection(name=None).refresh_if_board_changed() == (False, None)


# ── В5: the result shape `_finish_poll` consumes ────────────────────────────

class _TickConnection:
    def __init__(self, changed=False, error=None):
        self._answer = (changed, error)

    def refresh_if_board_changed(self):
        return self._answer


def _names():
    return {"net_names": ["GND"], "copper_net_names": ["VCC"],
            "board_name": "B.kicad_pcb"}


def _tick_window(connection):
    """The window shape `slow_poll.identity_and_names` touches — its connection
    and, for the collecting cases, the collector stubbed per cell."""
    return SimpleNamespace(connection=connection)


def test_a_quiet_tick_collects_nothing(monkeypatch):
    """В5 (quiet): the ordinary answer is `identity_only` — no net-name
    collection (that is IPC), no `board_name`, nothing for the badge."""
    monkeypatch.setattr(
        slow_poll, "collect_net_names",
        lambda _window: pytest.fail("a quiet tick collected net names"))

    result = slow_poll.identity_and_names(_tick_window(_TickConnection()))

    assert result["identity_only"] is True
    assert result["error"] is None
    assert "board_name" not in result


def test_a_changed_board_brings_the_net_names_with_it(monkeypatch):
    """В5 (changed): the full result a normal tick carries — `_finish_poll`'s
    success path must run unchanged, or the badge would name the old board and
    the docks' net combos would go empty."""
    monkeypatch.setattr(slow_poll, "collect_net_names", lambda _window: _names())

    result = slow_poll.identity_and_names(
        _tick_window(_TickConnection(changed=True)))

    assert result["board_changed"] is True
    assert result["error"] is None
    assert result["board_name"] == "B.kicad_pcb"
    assert result["net_names"] == ["GND"]
    assert "identity_only" not in result


def test_a_failed_re_read_keeps_its_error_and_collects_no_names(monkeypatch):
    """В5 (failure half): the switch was real but the re-read failed. The error
    travels to the badge exactly like a normal tick's — and the net names are
    NOT collected from a board that just went away."""
    monkeypatch.setattr(
        slow_poll, "collect_net_names",
        lambda _window: pytest.fail(
            "net names were collected from a board that just went away"))

    result = slow_poll.identity_and_names(
        _tick_window(_TickConnection(changed=True, error="KiCad is gone")))

    assert result["board_changed"] is True
    assert result["error"] == "KiCad is gone"


# ── В6: the wiring of the automatic tick ───────────────────────────────────

def _spy_submit(monkeypatch, window):
    """Record what `_poll` hands to the poll worker — WITHOUT replacing the
    worker object itself (the fixture's teardown stops its real thread)."""
    submitted = []

    def _submit(connection, fn, args, on_success, on_error):
        submitted.append((fn, args, on_success))

    monkeypatch.setattr(window._poll_worker, "submit", _submit)
    return submitted


def test_the_automatic_tick_checks_the_identity_instead_of_returning(
        real_main_window, monkeypatch):
    """В6 (the change itself): while CONNECTED, an automatic tick submits the
    identity check — the early return it replaces is what let the badge and the
    snapshot keep describing the board of the connection moment."""
    window = real_main_window
    window.connection.board = _Board("A.kicad_pcb")
    submitted = _spy_submit(monkeypatch, window)

    window._poll()

    assert len(submitted) == 1
    fn, args, on_success = submitted[0]
    assert fn is slow_poll.identity_and_names
    assert args == (window,)
    assert on_success == window._finish_poll


def test_a_disconnected_tick_still_only_tries_to_connect(real_main_window,
                                                         monkeypatch):
    """В6 (the half that must not change): with no board, the automatic tick is
    the plain connect/refresh pass, exactly as before."""
    window = real_main_window
    window.connection.board = None
    submitted = _spy_submit(monkeypatch, window)

    window._poll()

    assert len(submitted) == 1
    assert submitted[0][0] is slow_poll.run_poll
    assert submitted[0][1] == (window, False)


def test_a_busy_socket_skips_the_identity_tick_too(real_main_window, monkeypatch):
    """В6 (the existing rule, kept): a long op owns the shared socket, so the
    tick does not even submit — its request would interleave into that
    transaction."""
    window = real_main_window
    window.connection.board = _Board("A.kicad_pcb")
    window.connection.long_op_active = True
    submitted = _spy_submit(monkeypatch, window)

    window._poll()

    assert submitted == []


# ── В7: the quiet answer touches nothing ───────────────────────────────────

def test_the_identity_result_is_not_reflected_into_any_widget(real_main_window,
                                                              monkeypatch):
    """В7: an `identity_only` result ends `_finish_poll` at once — the badge is
    not repainted, nothing is pushed to the docks, and no latency window is fed
    (the identity tick is not a refresh; mixing its 0.1 ms into that window
    would quietly improve the refresh statistic)."""
    window = real_main_window
    touched = []
    monkeypatch.setattr(
        window.connection_badge, "show_poll_result",
        lambda *a, **k: touched.append(("badge", a, k)))
    monkeypatch.setattr(
        window, "_record_latency",
        lambda *a, **k: touched.append(("latency", a, k)))
    monkeypatch.setattr(
        window._dock_hub, "push_snapshot",
        lambda *a, **k: touched.append(("push", a, k)))

    window._finish_poll({"identity_only": True, "error": None,
                         "duration_s": 0.000127})

    assert touched == []
