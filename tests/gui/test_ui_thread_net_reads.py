# tests/gui/test_ui_thread_net_reads.py
"""plan_2026_09_13_ui_thread_net_reads — the net-name lists leave the UI thread.

The defect this pins down (P.0 of the plan, measured live 2026-09-13): five
adapter calls — three identical get_all_nets() plus net_trace's
get_tracks()/get_vias() — ran ON THE UI THREAD, inside
MainWindow._finish_poll -> DockHub.push_snapshot, which handed the live board
handle to four docks so each one could read KiCad for itself. With a 5 s IPC
timeout behind every call, that is the window freeze the 2026-08-08/09-08 S.1
rule exists to prevent.

What is guaranteed now (Э1–Э2):
  * the poll WORKER collects both lists once per successful tick
    (_run_poll -> _collect_net_names, gui/board_nets.py) and carries them
    back in the same result dict the docks already receive;
  * push_snapshot distributes LISTS — the board handle is not passed on;
  * an empty list empties a combo exactly as a None board used to;
  * net_trace keeps taking COPPER nets (tracks + vias), never get_all_nets().

Э4.4/Э4.6 are the two guards with mutations behind them (see
diagnostics/mutation_check_ui_thread_net_reads.py) — the poisoning test and the
"never asks for all_nets" collector test.
"""
import inspect
import threading
from types import SimpleNamespace

from gui.board_nets import board_net_names, copper_net_names
from gui.dock_hub import DockHub

from tests.gui.conftest import _pump


# ── Fakes ────────────────────────────────────────────────────────────────────

class _Net:
    def __init__(self, name):
        self.name = name


class _Track:
    def __init__(self, net_name):
        self.net_name = net_name


class _Via:
    def __init__(self, net_name):
        self.net_name = net_name


class _Adapter:
    """Serves the board reads and records WHICH THREAD each one ran on — the
    whole point of Э1 is that they come from the poll worker, not the UI."""

    def __init__(self):
        self.threads = []

    def get_all_nets(self):
        self.threads.append(threading.current_thread())
        return [_Net("+3V3"), _Net("GND"), _Net("GND"), _Net("")]

    def get_tracks(self):
        self.threads.append(threading.current_thread())
        return [_Track("GND"), _Track("/N2")]

    def get_vias(self):
        self.threads.append(threading.current_thread())
        return [_Via("/N3")]

    def get_selected_items(self):
        self.threads.append(threading.current_thread())
        return []

    def close(self):
        pass


class _Board:
    def __init__(self):
        self.adapter = _Adapter()

    def select(self):
        return []

    def refresh(self):
        pass


class _PoisonedAdapter:
    """Blows up on ANY read — installed on the connection the moment the worker
    is done, so a dock that still reaches the board (directly or through its
    own ``_connection`` reference) fails loudly instead of quietly freezing the
    window. This is the Э4.4 guard."""

    def __getattr__(self, name):
        raise AssertionError(
            f"the board adapter was read on the UI thread: {name}")


def _connected_window(real_main_window):
    """The real window, its OWN connection backed by the fake board.

    The connection OBJECT is deliberately not replaced: the docks capture a
    reference to it at construction (NetTraceDock._connection), and the Э4.4
    guard needs them to see the poisoned board."""
    real_main_window._timer.stop()
    real_main_window._selection_timer.stop()
    connection = real_main_window.connection
    board = _Board()
    connection.board = board
    connection._rebuild_snapshot()
    return real_main_window, connection, board


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


# ── Э4.1 / Э4.2: the worker collects, and only on success ────────────────────

def test_run_poll_returns_both_net_lists_on_success(real_main_window):
    """Э4.1 — the lists ride back in the same result dict the UI thread already
    consumes, and they are the board's nets / the copper nets respectively."""
    window, connection, _board = _connected_window(real_main_window)

    result = window._run_poll(manual=True)

    assert result["error"] is None
    assert result["net_names"] == ["+3V3", "GND"]
    assert result["copper_net_names"] == ["/N2", "/N3", "GND"]


def test_the_net_lists_are_collected_on_the_poll_worker_thread(
        real_main_window, qapp, monkeypatch):
    """Э1, end to end: a real manual Refresh through the real PollWorkerHandle
    reads the adapter OFF the UI thread — and by the time the UI half has run,
    the four docks show those names without ever asking KiCad themselves."""
    window, connection, board = _connected_window(real_main_window)
    # Overlay housekeeping is a different concern (and its own UI-thread board
    # reference — reported separately); stubbed so the fake adapter is only
    # asked what this test is about.
    monkeypatch.setattr(window._dock_hub, "reconcile_overlay",
                        lambda connection: None)

    window._poll(manual=True)
    _pump(qapp, lambda: not connection.long_op_active)

    assert board.adapter.threads, "the collector never ran"
    ui_thread = qapp.thread()
    assert all(t is not ui_thread for t in board.adapter.threads), (
        "a board read happened on the UI thread")

    hub = window._dock_hub
    assert _combo_items(hub.thermal_via_dock.net_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.chain_dock.net_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.tools_dock.nets_table.value_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.net_trace_dock.net_edit) == ["/N2", "/N3", "GND"]


def test_a_failed_tick_collects_no_net_names(real_main_window):
    """Э4.2 — a failed tick must not even try: refresh() has already dropped the
    connection, so there is no board to ask (and the docks get nothing)."""
    window, connection, board = _connected_window(real_main_window)
    connection.refresh = lambda: "KiCad closed"  # keeps the session up

    result = window._run_poll(manual=True)

    assert result["error"] == "KiCad closed"
    assert "net_names" not in result
    assert "copper_net_names" not in result
    assert board.adapter.threads == [], "a failed tick read the board anyway"


# ── Э4.3: the hub hands out lists, not the board ─────────────────────────────

def test_push_snapshot_hands_the_four_docks_lists(real_main_window, monkeypatch):
    """Э4.3 — every one of the four net consumers receives the COLLECTED list;
    the board handle is not a parameter of push_snapshot any more."""
    hub = real_main_window._dock_hub
    got = {}
    for dock_name, key in (("thermal_via_dock", "thermal_via"),
                           ("chain_dock", "chain"),
                           ("net_trace_dock", "net_trace"),
                           ("tools_dock", "tools")):
        monkeypatch.setattr(
            getattr(hub, dock_name), "refresh_known_nets",
            lambda arg, key=key: got.__setitem__(key, arg))
    monkeypatch.setattr(hub, "push_known_lists", lambda snapshot: None)

    hub.push_snapshot([], ["+3V3", "GND"], ["GND"])

    assert got == {"thermal_via": ["+3V3", "GND"],
                   "chain": ["+3V3", "GND"],
                   "net_trace": ["GND"],
                   "tools": ["+3V3", "GND"]}
    assert "board" not in inspect.signature(DockHub.push_snapshot).parameters


# ── Э4.4: the guard on the whole change ──────────────────────────────────────

def test_refresh_path_never_reads_the_board_from_a_dock(
        real_main_window, monkeypatch):
    """Э4.4 — the guard that matters. The worker collects; from the moment the
    UI half starts, the board is POISONED, so any dock that reaches for it
    raises instead of freezing the window. The combos still get filled, from
    the lists that travelled in the result.

    Mutation: hand the four docks the board again (or let one of them keep its
    own adapter read) -> this test fails."""
    window, connection, _board = _connected_window(real_main_window)
    monkeypatch.setattr(window._dock_hub, "reconcile_overlay",
                        lambda connection: None)

    result = window._run_poll(manual=True)
    assert result["net_names"] == ["+3V3", "GND"]

    connection.board = SimpleNamespace(adapter=_PoisonedAdapter())

    window._finish_poll(result)

    hub = window._dock_hub
    assert _combo_items(hub.thermal_via_dock.net_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.chain_dock.net_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.tools_dock.nets_table.value_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.net_trace_dock.net_edit) == ["/N2", "/N3", "GND"]


# ── Э4.5: an empty list behaves like the old None board ──────────────────────

def test_empty_list_clears_the_combos_like_a_missing_board(real_main_window):
    """Э4.5 — the tick that could not read a board hands empty lists; every one
    of the four combos goes empty, with no error and no exception."""
    hub = real_main_window._dock_hub

    hub.push_snapshot([], ["+3V3", "GND"], ["GND"])
    assert _combo_items(hub.chain_dock.net_edit) == ["+3V3", "GND"]
    assert _combo_items(hub.net_trace_dock.net_edit) == ["GND"]

    hub.push_snapshot([], [], [])

    assert _combo_items(hub.thermal_via_dock.net_edit) == []
    assert _combo_items(hub.chain_dock.net_edit) == []
    assert _combo_items(hub.net_trace_dock.net_edit) == []
    assert _combo_items(hub.tools_dock.nets_table.value_edit) == []
    assert _combo_items(hub.tools_dock.nets_table.key_edit) == []


# ── Э4.6: net_trace keeps copper, never all_nets() ───────────────────────────

def test_the_copper_list_never_asks_for_all_nets():
    """Э4.6 — the two lists are NOT the same thing, and the copper one does not
    even ask the board for the padded answer. A pad-only net has no copper to
    capture, so offering it in net_trace's picker would be a lie.

    Mutation: implement copper_net_names as board_net_names (or call
    get_all_nets() in it) -> the poisoned get_all_nets raises here."""
    class _NoAllNets(_Adapter):
        def get_all_nets(self):
            raise AssertionError("get_all_nets() must not feed the copper list")

    assert copper_net_names(_NoAllNets()) == ["/N2", "/N3", "GND"]

    adapter = _Adapter()
    all_nets = board_net_names(adapter)
    copper = copper_net_names(adapter)
    assert all_nets == ["+3V3", "GND"]
    assert copper == ["/N2", "/N3", "GND"]
    assert "+3V3" in all_nets and "+3V3" not in copper
