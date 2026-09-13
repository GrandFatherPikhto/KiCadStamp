# tests/gui/test_ipc_timeout_and_latency.py
"""plan_2026_09_13_ipc_timeout_and_latency — the IPC timeout, the reconnect
interval and the board-call latency readouts.

Covered here (Э1–Э4б, the Э5 list):
  * the lowered DEFAULT_TIMEOUT_MS / DEFAULT_RECONNECT_INTERVAL_MS constants;
  * gui_main.resolve_timeout_ms's priority (explicit flag > saved setting >
    default) — including the "flag equal to the default is still explicit"
    guard against comparing with DEFAULT_TIMEOUT_MS instead of default=None;
  * the rolling latency window on BoardConnection (median-not-mean, and the
    disconnect() reset);
  * MainWindow recording a poll tick's measured duration and showing the
    status-bar latency label;
  * the Settings > KiCad page: reconnect spinbox seed/persist/emit and the
    latency readout;
  * gui.worker.timeout_recommendation — decided by TIME, never by the error
    text.
"""
import time
from types import SimpleNamespace

from gui import settings
from gui.connection import (BoardConnection, LATENCY_KIND_FAST,
                            LATENCY_KIND_SLOW)
from gui.docks.configurator import ConfiguratorDock
from gui.worker import start_long_op, timeout_recommendation

import kicadstamp.gui_main as gui_main
from kicadstamp.constants import (DEFAULT_RECONNECT_INTERVAL_MS,
                                  DEFAULT_TIMEOUT_MS)

from tests.gui.conftest import _pump


class _FakeAdapter:
    def __init__(self):
        self.get_selected_items_calls = 0
        self.closed = 0

    def get_selected_items(self):
        self.get_selected_items_calls += 1
        return []

    def close(self):
        self.closed += 1


class _FakeBoard:
    def __init__(self):
        self.adapter = _FakeAdapter()

    def select(self):
        return []

    def refresh(self):
        pass


def _connected_real_window(real_main_window):
    """Stop the real timers (the tick is driven synchronously), swap in a real
    BoardConnection backed by a fake board — the same shape
    test_main_window_selection.py uses."""
    real_main_window._timer.stop()
    real_main_window._selection_timer.stop()
    connection = BoardConnection(timeout_ms=10)
    connection.board = _FakeBoard()
    connection._rebuild_snapshot()
    real_main_window.connection = connection
    return real_main_window, connection


# ── Э1 / Э4а: the defaults ────────────────────────────────────────────────

def test_default_timeout_is_5000():
    """Cheap guard against a silent rollback of the measured default (Э1)."""
    assert DEFAULT_TIMEOUT_MS == 5000


def test_default_reconnect_interval_is_5000():
    """Э4а — Denis's chosen default."""
    assert DEFAULT_RECONNECT_INTERVAL_MS == 5000


# ── Э4: resolve_timeout_ms priority ───────────────────────────────────────

def test_saved_timeout_reaches_startup(qapp):
    """5а — THE test that was missing and let the defect live since 15.08:
    with no flag, the app must start with the SAVED timeout, not the
    default."""
    settings.state.set("kicad_timeout_ms", 9000)
    assert gui_main.resolve_timeout_ms(None) == 9000


def test_default_timeout_when_nothing_is_saved(qapp):
    assert gui_main.resolve_timeout_ms(None) == DEFAULT_TIMEOUT_MS


def test_explicit_flag_beats_saved_setting(qapp):
    settings.state.set("kicad_timeout_ms", 9000)
    assert gui_main.resolve_timeout_ms(1234) == 1234


def test_flag_equal_to_default_is_still_explicit(qapp):
    """6 (second half) — the guard against comparing the flag with the default
    instead of declaring default=None: passing exactly DEFAULT_TIMEOUT_MS is a
    real choice and must win over the saved setting."""
    settings.state.set("kicad_timeout_ms", 9000)
    assert gui_main.resolve_timeout_ms(DEFAULT_TIMEOUT_MS) == DEFAULT_TIMEOUT_MS


def test_invalid_saved_timeout_falls_back(qapp):
    settings.state.set("kicad_timeout_ms", "not-an-int")
    assert gui_main.resolve_timeout_ms(None) == DEFAULT_TIMEOUT_MS


# ── Э2: the rolling latency window ────────────────────────────────────────

def test_window_reports_median_and_max():
    connection = BoardConnection()
    for value in (0.003, 0.001, 0.002):
        connection.record_latency(LATENCY_KIND_FAST, value)
    median_s, max_s = connection.latency_stats(LATENCY_KIND_FAST)
    assert median_s == 0.002
    assert max_s == 0.003
    assert connection.has_latency is True


def test_window_median_ignores_a_single_outlier():
    """7 — the mutation guard: replacing the median with a mean must fail HERE.
    A window of ~1 ms calls with one 287 ms stuck tick keeps a ~1 ms median,
    while a mean would be dragged an order of magnitude up."""
    connection = BoardConnection()
    for _ in range(20):
        connection.record_latency(LATENCY_KIND_FAST, 0.001)
    connection.record_latency(LATENCY_KIND_FAST, 0.287)

    median_s, max_s = connection.latency_stats(LATENCY_KIND_FAST)
    assert median_s == 0.001
    assert max_s == 0.287
    values = [0.001] * 20 + [0.287]
    assert median_s != sum(values) / len(values)


def test_record_latency_ignores_none_and_unknown_kinds():
    connection = BoardConnection()
    connection.record_latency(LATENCY_KIND_FAST, None)
    connection.record_latency("nonsense", 0.01)
    assert connection.latency_stats(LATENCY_KIND_FAST) is None
    assert connection.has_latency is False


def test_disconnect_clears_the_window():
    """3 — a dropped connection must not leave the previous session's numbers
    looking current."""
    connection = BoardConnection()
    board = _FakeBoard()
    connection.board = board
    connection.record_latency(LATENCY_KIND_FAST, 0.002)
    assert connection.has_latency is True

    connection.disconnect()

    assert connection.latency_stats(LATENCY_KIND_FAST) is None
    assert connection.has_latency is False


# ── Э2/Э3: MainWindow records the tick and shows the label ────────────────

def test_poll_tick_records_its_duration_and_shows_the_label(
        real_main_window, qapp):
    """2/4 — the fast tick's measured duration lands in the window (median/max
    become available) and the status-bar label turns non-empty."""
    window, connection = _connected_real_window(real_main_window)
    assert window.latency_label.text() == ""  # nothing measured yet

    window._poll_board_selection()
    _pump(qapp, lambda: not connection.long_op_active)

    assert connection.latency_stats(LATENCY_KIND_FAST) is not None
    assert window.latency_label.text() != ""


def test_latency_label_is_empty_without_a_connection(real_main_window, qapp):
    """4 — no connection => an empty label, even with samples recorded (they
    are cleared by disconnect, and the label keys on is_connected)."""
    window, connection = _connected_real_window(real_main_window)
    window._poll_board_selection()
    _pump(qapp, lambda: not connection.long_op_active)
    assert window.latency_label.text() != ""

    connection.disconnect()
    window._update_latency_label()

    assert connection.latency_stats(LATENCY_KIND_FAST) is None
    assert window.latency_label.text() == ""


# ── Э3/Э4/Э4а: the Settings > KiCad page ──────────────────────────────────

def test_reconnect_spin_defaults_to_5000(main_window, qapp):
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.reconnect_spin.value() == DEFAULT_RECONNECT_INTERVAL_MS
    assert settings.state.get("reconnect_interval_ms") is None  # draft only


def test_reconnect_spin_persists_and_emits(main_window, qapp):
    """8 — the setting is seeded/saved like the timeout, and apply() re-emits
    it so DockHub can retime the live timer."""
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    received = []
    dock.reconnect_interval_changed.connect(received.append)

    dock.reconnect_spin.setValue(7000)
    assert settings.state.get("reconnect_interval_ms") is None  # draft only
    dock.apply()

    assert settings.state.get("reconnect_interval_ms") == 7000
    assert received == [7000]


def test_reconnect_spin_seeded_from_state(main_window, qapp):
    settings.state.set("reconnect_interval_ms", 8000)
    dock = ConfiguratorDock(main_window, connection=main_window.connection)
    assert dock.reconnect_spin.value() == 8000


def test_reconnect_interval_applied_live(real_main_window):
    """Э4а — DockHub wires apply()'s signal to MainWindow.set_reconnect_interval,
    which retimes the running timer."""
    real_main_window.set_reconnect_interval(7000)
    assert real_main_window._reconnect_interval_ms == 7000
    assert real_main_window._timer.interval() == 7000


def test_read_reconnect_interval_uses_settings_with_fallback(real_main_window):
    settings.state.set("reconnect_interval_ms", 7000)
    assert real_main_window._read_reconnect_interval() == 7000
    settings.state.set("reconnect_interval_ms", "garbage")
    assert real_main_window._read_reconnect_interval() == DEFAULT_RECONNECT_INTERVAL_MS


def test_kicad_page_shows_the_measured_latency(main_window, qapp):
    """Э3 — the page's readout names both windows' median and max."""
    connection = BoardConnection()
    connection.record_latency(LATENCY_KIND_FAST, 0.0012)
    connection.record_latency(LATENCY_KIND_SLOW, 0.160)
    dock = ConfiguratorDock(main_window, connection=connection)

    dock.refresh_latency_info()

    text = dock.latency_info_label.text()
    assert "1.2" in text      # fast median, one decimal
    assert "160" in text      # slow max/median, whole ms


def test_kicad_page_latency_readout_without_data(main_window, qapp):
    connection = BoardConnection()
    dock = ConfiguratorDock(main_window, connection=connection)
    dock.refresh_latency_info()
    assert dock.latency_info_label.text()  # a "no data yet" wording, not empty


# ── Э4б: the timeout recommendation is decided by TIME ────────────────────

def test_recommendation_helper_threshold():
    assert timeout_recommendation(0.05, 1000) is None
    assert timeout_recommendation(0.9, 1000) is not None
    assert timeout_recommendation(None, 1000) is None
    assert timeout_recommendation(1.0, None) is None


def test_long_op_slow_failure_gets_a_recommendation(qapp):
    """9 (first half) — a call that ran past the threshold produced the extra
    recommendation line, naming the configured timeout."""
    connection = SimpleNamespace(long_op_active=False, timeout_ms=100)
    errors = []

    def slow_fail():
        time.sleep(0.095)  # >= 90% of the 100 ms timeout
        raise ConnectionError("KiCad closed")  # deliberately no 'timeout' word

    controller = start_long_op(connection, (), slow_fail, lambda _r: None,
                               errors.append)
    thread = controller._thread
    _pump(qapp, lambda: errors)
    assert thread.wait(2000), "worker thread did not finish"

    assert errors, "no error was reported"
    message = errors[0]
    assert "KiCad closed" in message
    assert "\n" in message       # the recommendation is a second line
    assert "100" in message      # it names the configured timeout


def test_long_op_fast_failure_gets_no_recommendation(qapp):
    """9 (second half) — a genuine, fast failure (e.g. KiCad closed) must NOT
    sprout a timeout recommendation."""
    connection = SimpleNamespace(long_op_active=False, timeout_ms=100)
    errors = []

    def boom():
        raise ValueError("kaboom")

    controller = start_long_op(connection, (), boom, lambda _r: None,
                               errors.append)
    thread = controller._thread
    _pump(qapp, lambda: errors)
    assert thread.wait(2000), "worker thread did not finish"

    assert errors == ["kaboom"]


def test_recommendation_is_decided_by_time_not_by_error_text(qapp):
    """10 — the guard against parsing the error string: an error whose TEXT
    says nothing about timeouts still gets the recommendation, because the
    decision is made purely on the measured duration."""
    connection = SimpleNamespace(long_op_active=False, timeout_ms=200)
    errors = []

    def slow_fail():
        time.sleep(0.19)  # >= 90% of 200 ms
        raise ConnectionError("The device is not available")  # no time words

    controller = start_long_op(connection, (), slow_fail, lambda _r: None,
                               errors.append)
    thread = controller._thread
    _pump(qapp, lambda: errors)
    assert thread.wait(2000), "worker thread did not finish"

    assert errors
    assert "The device is not available" in errors[0]
    assert "\n" in errors[0]      # a recommendation line was appended
    assert "200" in errors[0]
