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
import logging
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


class _SlowFailingAdapter(_FakeAdapter):
    """get_selected_items() raises a REAL exception after a real delay — the
    fast tick's `except` arm (Э2), as opposed to _FakeAdapter's silent [].

    A genuine KiCad-gone failure, deliberately with no 'timeout' word in the
    message: the recommendation must come from the measured duration alone."""

    def get_selected_items(self):
        time.sleep(0.02)  # >= the 9 ms threshold set by timeout_ms=10 below
        raise ConnectionError("KiCad closed")


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


# ── Sentinel guards on the FAILURE path (plan_2026_09_13_failure_path_ ─────
# latency_sentinels) ────────────────────────────────────────────────────────
#
# A duration is measured on BOTH outcomes of each poll tick, but only the
# SUCCESS path was guarded. Deleting perf_counter() from either failure return
# (mutations M8/M9) left every test green while silently killing the whole
# latency/recommendation feature exactly when the user needs it — a request
# that fell over on the timeout. These two tests pin that contract down.


def test_slow_tick_failure_still_measures_and_feeds_the_window(
        real_main_window, caplog):
    """Э1 — the M9 guard. `_run_poll` has ONE return shared by success and
    failure, so a FAILED refresh must still (1) carry a measured duration,
    (2) feed the SLOW rolling window BoardConnection.latency_stats reads, and
    (3) log the "raise the timeout" recommendation once past the threshold.

    The failing board call is stubbed on the connection and returns an error
    STRING (the exact shape _run_poll handles — it never sees an exception
    from this arm). The stub deliberately does NOT disconnect: a REAL
    BoardConnection.refresh() failure drops the connection, and disconnect()
    clears the very window under test — the same trap the plan calls out for
    Э2, which would otherwise make check (2) untestable here too."""
    window, connection = _connected_real_window(real_main_window)
    connection.timeout_ms = 10  # threshold = 0.9 * 10 ms = 9 ms

    def slow_failing_refresh():
        time.sleep(0.02)  # >= 9 ms: indistinguishable from a timed-out call
        return "KiCad closed"

    connection.refresh = slow_failing_refresh
    assert connection.is_connected  # the stub keeps the session up

    with caplog.at_level(logging.WARNING, logger="gui.main_window"):
        result = window._run_poll(manual=False)
        window._finish_poll(result)

    # 1 — a duration WAS measured on the failed outcome (M9 zeroes this).
    assert result["duration_s"] is not None
    assert result["duration_s"] >= 0.02

    # 2 — it reached the SLOW window (a None duration would be skipped there).
    stats = connection.latency_stats(LATENCY_KIND_SLOW)
    assert stats is not None
    assert stats[1] >= 0.02

    # 3 — and the timeout recommendation was logged from it.
    assert "timed out" in caplog.text
    assert "IPC timeout is 10 ms" in caplog.text


def test_fast_tick_failure_still_measures_and_recommends(
        real_main_window, caplog):
    """Э2 — the M8 guard. The fast tick's `except` arm returns the error as a
    STRING and also calls disconnect(); disconnect() CLEARS both windows (its
    own accepted behaviour), so what survives to be asserted is exactly what
    the plan says: the measured duration in the returned dict and the
    recommendation it produced. Asserting a non-empty window here would be
    false by construction — hence the two "reality" assertions at the end,
    which document the reset instead of fighting it."""
    window, connection = _connected_real_window(real_main_window)
    connection.timeout_ms = 10  # threshold = 0.9 * 10 ms = 9 ms
    connection.board.adapter = _SlowFailingAdapter()

    with caplog.at_level(logging.WARNING, logger="gui.main_window"):
        result = window._run_poll_selection()
        window._finish_poll_selection(result)

    # 1 — measured on the failure path too (M8 zeroes this).
    assert result["error"] == "KiCad closed"
    assert result["duration_s"] is not None
    assert result["duration_s"] >= 0.02

    # 2 — the recommendation was still logged from that duration.
    assert "timed out" in caplog.text
    assert "IPC timeout is 10 ms" in caplog.text

    # Reality check, documented rather than assumed: the except arm dropped the
    # connection and reset_latency() emptied the window before the UI thread
    # ever saw the result — which is why the guards above key on the RESULT.
    assert connection.is_connected is False
    assert connection.latency_stats(LATENCY_KIND_FAST) is None
