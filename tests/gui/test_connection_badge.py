# tests/gui/test_connection_badge.py
"""The status bar's persistent connection badge — plan_2026_10_14_connection_badge.

WHY THE BADGE EXISTS, and what these cells therefore hold: the connection state
used to be written into the same stretchable LEFT label as one-off messages, so
saving a project erased it (Denis, live 14.10.2026: "it gets overwritten, like
Project saved"). The badge is a separate PERMANENT widget, and a one-off message
must never reach it again — that is C6 below.

Cells are named after the plan's own table: C1 the start state, C2 a green badge
with the board's name and the count, C3 the slow poll's error, C4 the FAST tick's
error (the plan's mutation target), C5 the fast tick's success, C6 "Project
saved", C7 a connected board whose name is unknown, C8 the name changing when the
board does.

The badge is asserted through its STATE (``badge.state``) and its TEXT, never
through the painted stylesheet: the colours are cosmetic, the state is the
contract — a guard on QSS text would break on any tweak and stop being a watchman.
The two stylesheets are still compared, but against the module's OWN constants
(the same values the badge imports), so a colour change is a one-place change.
"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QStatusBar

from gui.board_nets import board_filename
from gui.connection import BoardConnection
from gui.connection_badge import (STATE_CONNECTED, STATE_DISCONNECTED,
                                  ConnectionBadge)
from gui.docks._common import BADGE_CONNECTED_STYLE, BADGE_DISCONNECTED_STYLE

BOARD = "3CH-AWG-TIA-v103.kicad_pcb"
STEM = "3CH-AWG-TIA-v103"


class _AdapterStub:
    """Exactly the adapter surface the badge's data path touches.

    The two net readers are stubbed to empty lists on purpose — their own cells
    live in tests/gui/test_ui_thread_net_reads.py; what THIS file pins is that the
    same worker pass also collects the board's filename, and that a stand-in which
    cannot answer it never fails the poll (`blows_up`/no method/None)."""

    def __init__(self, filename=None, blows_up=False):
        self._filename = filename
        self._blows_up = blows_up

    def get_all_nets(self):
        return []

    def get_tracks(self):
        return []

    def get_vias(self):
        return []

    def get_board_filename(self):
        if self._blows_up:
            raise ConnectionError("link dropped")
        return self._filename


class _BoardStub:
    """A live board stand-in: `select()` hands over `count` stand-ins, which is
    all the snapshot the badge counts ever needs (the docks never see them — the
    hub's push methods are stubbed in the cells that care)."""

    def __init__(self, count=0, filename=BOARD, adapter=None):
        self.adapter = adapter or _AdapterStub(filename)
        self._count = count

    def select(self):
        return [SimpleNamespace(ref=f"R{n}") for n in range(self._count)]

    def refresh(self):
        pass


def _window_with_a_board(real_main_window, count, filename=BOARD, monkeypatch=None):
    """A REAL MainWindow whose connection holds a board with `count` footprints.

    The three dock-facing calls of _finish_poll's success branch are stubbed: the
    subject here is the badge, and handing the docks N stand-ins would test the
    fieldstool instead (plan_2026_10_14_connection_badge Ч1)."""
    window = real_main_window
    connection = BoardConnection(timeout_ms=10)
    connection.board = _BoardStub(count, filename)
    connection._rebuild_snapshot()
    window.connection = connection
    if monkeypatch is not None:
        monkeypatch.setattr(window._dock_hub, "push_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(window._dock_hub, "push_fieldstool_snapshot",
                            lambda *a, **k: None)
        monkeypatch.setattr(window._dock_hub, "reconcile_overlay", lambda *a, **k: None)
    return window


# ── C1: the start state ───────────────────────────────────────────────────

def test_c1_a_badge_nobody_reported_to_says_not_connected(qapp):
    """No successful read has happened yet, so "Not connected" is the truth — and
    there is no error to show either (an empty tooltip, not an empty-string
    stand-in meant as an error)."""
    badge = ConnectionBadge()

    assert badge.state == STATE_DISCONNECTED
    assert badge.text() == "Not connected"
    assert badge.toolTip() == ""
    assert badge.styleSheet() == BADGE_DISCONNECTED_STYLE


# ── C2/C7/C8: what a successful poll paints ───────────────────────────────

def test_c2_a_poll_result_names_the_board_and_counts_its_components(qapp):
    """The stem of the .kicad_pcb and the snapshot's size — the draft Denis
    approved (Д2: the counter after a colon, no plural forms)."""
    badge = ConnectionBadge()

    badge.show_poll_result(board_name=BOARD, count=73)

    assert badge.state == STATE_CONNECTED
    assert badge.text() == f"{STEM} · 73 components"
    assert ".kicad_pcb" not in badge.text(), "the badge shows the board, not the file"
    assert badge.toolTip() == "Components as of the last board read"
    assert badge.styleSheet() == BADGE_CONNECTED_STYLE


def test_a_path_shaped_name_is_reduced_to_its_stem(qapp):
    """The adapter answers a BARE filename today (measured 24.09.2026), but the
    shape belongs to KiCad — so the badge takes the stem rather than trusting it."""
    badge = ConnectionBadge()

    badge.show_poll_result(board_name="/home/denis/boards/HiPiMS-v099.kicad_pcb",
                           count=3)

    assert badge.text() == "HiPiMS-v099 · 3 components"


def test_c7_a_connected_board_without_a_name_keeps_the_count(qapp):
    """Д4: a stand-in with no adapter, or a board KiCad carried with no filename —
    a legitimate value, not an error. `error is None` alone means "the poll read
    the board"."""
    badge = ConnectionBadge()

    badge.show_poll_result(count=7)

    assert badge.state == STATE_CONNECTED
    assert badge.text() == "Connected · 7 components"


def test_c8_a_switched_board_changes_the_name_the_badge_shows(real_main_window,
                                                              monkeypatch):
    window = _window_with_a_board(real_main_window, 3, monkeypatch=monkeypatch)
    window._finish_poll({"error": None, "duration_s": 0.001, "board_name": BOARD,
                         "net_names": [], "copper_net_names": []})
    assert window.connection_badge.text() == f"{STEM} · 3 components"

    window._finish_poll({"error": None, "duration_s": 0.001,
                         "board_name": "HiPiMS-v099.kicad_pcb",
                         "net_names": [], "copper_net_names": []})

    assert window.connection_badge.text() == "HiPiMS-v099 · 3 components"


# ── C3: an error goes to the tooltip, the badge stays short ───────────────

def test_c3_an_error_lands_in_the_tooltip_untouched(qapp):
    """Д1: the diagnosis moved from the left label into the tooltip — so it must
    arrive VERBATIM (a status bar one line wide cannot carry a socket error, and
    shortening it would lose the very text the left label used to show)."""
    error = ("KiCad is busy and cannot respond to API requests right now — usually "
             "an unfinished tool in the GUI (dimensioning, interactive routing)")
    badge = ConnectionBadge()

    badge.show_poll_result(error=error)

    assert badge.state == STATE_DISCONNECTED
    assert badge.text() == "Not connected"
    assert badge.toolTip() == error


def test_c3_a_slow_poll_error_turns_a_green_badge_red(real_main_window):
    """Through the real handler. Green FIRST on purpose: the badge starts red, so
    only the transition proves the error path wrote anything (the mutation that
    removes the call leaves it green and fails here)."""
    window = real_main_window
    window.connection_badge.show_poll_result(board_name=BOARD, count=5)

    window._finish_poll({"error": "KiCad is not running", "duration_s": 0.001})

    assert window.connection_badge.state == STATE_DISCONNECTED
    assert window.connection_badge.toolTip() == "KiCad is not running"
    # Д1: the left label is for one-off messages now — the connection error must
    # NOT be written back into it.
    assert "KiCad is not running" not in window.status_label.text()


# ── C2 at the window: the slow poll's success branch ──────────────────────

def test_c2_a_successful_slow_poll_turns_it_green_with_the_name_and_the_count(
        real_main_window, monkeypatch):
    window = _window_with_a_board(real_main_window, 73, monkeypatch=monkeypatch)

    window._finish_poll({"error": None, "duration_s": 0.001, "board_name": BOARD,
                         "net_names": [], "copper_net_names": []})

    assert window.connection_badge.state == STATE_CONNECTED
    assert window.connection_badge.text() == f"{STEM} · 73 components"
    assert window.status_label.text() == "", \
        "the left label is not a connection display any more"


def test_c2_end_to_end_the_name_and_the_count_come_from_the_poll(real_main_window,
                                                                 monkeypatch):
    """The WHOLE path: _run_poll (the worker body, run here on this thread) ->
    _finish_poll -> the badge. This is the cell that fails when the name stops
    being collected on the worker pass (_collect_net_names) — the C2 cell above
    hands the result dict over by hand and would stay green."""
    window = _window_with_a_board(real_main_window, 4, monkeypatch=monkeypatch)

    result = window._run_poll(manual=True)
    window._finish_poll(result)

    assert result["board_name"] == BOARD
    assert window.connection_badge.state == STATE_CONNECTED
    assert window.connection_badge.text() == f"{STEM} · 4 components"


def test_c7_a_board_whose_name_is_unknown_still_turns_the_badge_green(
        real_main_window, monkeypatch):
    window = _window_with_a_board(real_main_window, 3, filename=None,
                                  monkeypatch=monkeypatch)

    window._finish_poll({"error": None, "duration_s": 0.001, "board_name": None,
                         "net_names": [], "copper_net_names": []})

    assert window.connection_badge.state == STATE_CONNECTED
    assert window.connection_badge.text() == "Connected · 3 components"


# ── C4/C5: the fast selection tick ────────────────────────────────────────

def test_c4_the_fast_ticks_error_turns_a_green_badge_red(real_main_window):
    """THE mutation cell of the plan: remove the badge call from
    _finish_poll_selection (gui/main_window.py) and this fails — the fast tick is
    the soonest of the two writers (≤ 400 ms), so it reports its own loss instead
    of waiting for the slow poll."""
    window = real_main_window
    window.connection_badge.show_poll_result(board_name=BOARD, count=5)

    window._finish_poll_selection({"error": "socket closed", "duration_s": 0.001})

    assert window.connection_badge.state == STATE_DISCONNECTED
    assert window.connection_badge.toolTip() == "socket closed"
    assert window.action_button.text() == "Reconnect"


def test_c5_a_successful_fast_tick_leaves_a_red_badge_alone(real_main_window):
    """Only a SUCCESSFUL SLOW poll paints green (Д5): the 400 ms tick carries no
    name and no fresh count, so a green result of it would be a claim it cannot
    make."""
    window = real_main_window
    window._finish_poll({"error": "gone", "duration_s": 0.001})

    window._finish_poll_selection({"error": None, "items": [], "duration_s": 0.001})

    assert window.connection_badge.state == STATE_DISCONNECTED
    assert window.connection_badge.toolTip() == "gone"


# ── C6: one-off messages do not reach the badge ───────────────────────────

def test_c6_project_saved_does_not_touch_the_badge(real_main_window):
    """The defect the whole task exists for: a save used to erase the connection
    state, because both wrote the same label. Now the message lands in the LEFT
    label and the badge keeps its state."""
    window = real_main_window
    window.connection_badge.show_poll_result(board_name=BOARD, count=5)

    window._save_project()          # no project open — a one-off message only

    assert window.status_label.text() != "", "the message must reach the left label"
    assert window.connection_badge.state == STATE_CONNECTED
    assert window.connection_badge.text() == f"{STEM} · 5 components"


# ── the shape of the widget in the status bar ─────────────────────────────

def test_the_badge_is_a_permanent_widget_left_of_the_dirty_marker(real_main_window):
    """A PERMANENT widget (like dirty/busy/latency): a temporary status message
    cannot replace it, and it sits FIRST among them — left of the unsaved-changes
    ●, so the pair reads "connection, then project state"."""
    window = real_main_window
    badge = window.connection_badge

    assert badge.parent() is window.statusBar()
    assert isinstance(window.statusBar(), QStatusBar)
    window.statusBar().showMessage("A temporary status message")
    assert badge.text() == "Not connected"
    assert badge.state == STATE_DISCONNECTED
    window.statusBar().clearMessage()

    assert badge.x() < window.dirty_label.x(), "FIRST of the permanent widgets"


def test_the_badge_never_height_restricts_itself_and_carries_no_marker(qapp):
    """Д5/Д6: the state is the FILL, so there is no "●" to confuse with the
    unsaved-changes ● that already lives at the right edge — and no height pin
    (tests/gui/test_no_widget_height_squeezing.py scans gui/** for exactly that
    call; this is the widget-side half)."""
    badge = ConnectionBadge()

    for result in ({"error": "boom"}, {"board_name": BOARD, "count": 2}):
        badge.show_poll_result(**result)
        assert "\u25cf" not in badge.text()
        assert badge.minimumHeight() == 0
        assert badge.maximumHeight() >= badge.sizeHint().height(), \
            "the badge must keep the height its own content asks for"


# ── the worker-side source of the name ────────────────────────────────────

def test_board_filename_answers_what_the_adapter_has():
    assert board_filename(_AdapterStub(BOARD)) == BOARD


def test_board_filename_says_nothing_without_an_adapter():
    """No adapter at all (a test double), or a stand-in without the method — the
    same getattr-guarded shape the poll's own adapter read uses."""
    assert board_filename(None) is None
    assert board_filename(object()) is None


def test_board_filename_swallows_a_failed_read():
    """The ONE difference from the two net readers next to it: those are part of
    the poll's contract and their failure must surface, while a name is a nicety —
    a stand-in that raises must not kill the poll it rides on (the badge then
    shows the count alone)."""
    assert board_filename(_AdapterStub(blows_up=True)) is None


def test_the_worker_pass_collects_the_filename_with_the_net_lists(real_main_window):
    """The name travels in the SAME result dict as the two net lists, collected by
    _collect_net_names on the poll worker — which is why the badge needs no board
    read on the UI thread at all (door_lint counts this call chain as `worker`)."""
    window = _window_with_a_board(real_main_window, 2)

    collected = window._collect_net_names()

    assert collected["board_name"] == BOARD
    assert collected["net_names"] == []
    assert collected["copper_net_names"] == []


def test_the_worker_pass_survives_an_adapter_that_cannot_name_the_board(
        real_main_window):
    window = _window_with_a_board(real_main_window, 2, filename=None)
    window.connection.board.adapter = _AdapterStub(blows_up=True)

    collected = window._collect_net_names()

    assert collected["board_name"] is None


# ── C15: the badge names a connect failure the user can act on (Ч3) ─────────

def test_c15_finish_poll_shows_the_named_connect_failure(real_main_window):
    """connect() turns the "no handler for GetOpenDocuments" text into the
    sentence "no PCB editor is open", and the ONE consumer of a poll result —
    MainWindow._finish_poll — puts that text into the badge tooltip. The chain is
    what the plan asks for; asserting the setter alone would not prove it."""
    window = real_main_window
    window._record_latency = lambda *a, **k: None
    window._log_timeout_hint = lambda *a, **k: None
    window._dock_hub.clear_components = lambda: None
    window._dock_hub.push_fieldstool_connection_status = lambda *a, **k: None
    named = ("KiCad is running, but no PCB editor is open — open the board in "
             "KiCad (PCB Editor)")

    window._finish_poll({"error": named, "duration_s": 0.01})

    assert window.connection_badge.state == STATE_DISCONNECTED
    assert window.connection_badge.toolTip() == named
