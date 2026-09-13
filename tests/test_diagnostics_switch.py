# tests/test_diagnostics_switch.py
"""Э1/Э3/Э4/Э5 of plan_2026_09_13_diagnostics_switch: the two recorders of
kicadstamp/diagnostics/ became switchable WHILE THE GUI KEEPS RUNNING, and the
timing report learned to name the calls made on the UI thread.

What is pinned here:

  * Э1 — recording ON/OFF is a flag the wrapper tests FIRST; while it is OFF not
    one byte is written and no stack frame is read. start()/stop() are
    idempotent, stop() returns how many calls a session recorded, and a session
    stops ITSELF at the size cap with a Log line naming the reason;
  * Э2 (the Log contract) — exactly two Log lines per session, and never one per
    call;
  * Э3 — a call site is recorded ONLY when the injected UI-thread predicate says
    so; the default (None) records none, which is the CLI/MCP model;
  * Э4 — report_board_timing prints the UI-thread section, by method and call
    site, and says plainly when there is none;
  * the same contract for board_read_probe (the "who reads the board" recorder).

No KiCad and no Qt: the wrapper is exercised on a stand-in class (the PERMANENT
patch of the live adapter is covered by its own test, which uses install_patch's
`target` parameter for exactly this reason), and the read probe goes through the
real gui.connection board property, whose hook is a plain module-level callable.
"""
import json
import logging
import threading
from pathlib import Path

import pytest

from gui import connection as connection_mod
from gui.connection import BoardConnection
from kicadstamp.diagnostics import (board_call_timing, board_read_probe,
                                    report_board_timing)


class _FakeAdapter:
    """Stand-in for KiCadBoardAdapter — the recorder only needs a plain method
    on a class. It remembers what it was called with, so a test can prove the
    wrapped call still reached the original."""

    def __init__(self):
        self.calls = []

    def ping(self, items=None):
        self.calls.append(items)
        return list(items or [])


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """A clean board-call recorder: no session, no predicate, logs in tmp_path.

    The recorder's state is PROCESS-WIDE (module globals — that is the point of
    the switch), so every test starts from the OFF state and teardown closes
    whatever the test opened. `default_log_dir` is redirected so no test writes
    into the repository's gitignored diagnostics/ directory."""
    monkeypatch.setattr(board_call_timing, "default_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(board_call_timing, "_recording", False)
    monkeypatch.setattr(board_call_timing, "_fh", None)
    monkeypatch.setattr(board_call_timing, "_path", None)
    monkeypatch.setattr(board_call_timing, "_written", 0)
    monkeypatch.setattr(board_call_timing, "_bytes_written", 0)
    monkeypatch.setattr(board_call_timing, "_max_bytes",
                        board_call_timing.MAX_LOG_BYTES)
    monkeypatch.setattr(board_call_timing, "_ui_thread", None)
    yield board_call_timing
    board_call_timing.stop()
    board_call_timing.set_ui_thread_predicate(None)


@pytest.fixture
def patched_fake(monkeypatch):
    """The recorder's wrapper installed on _FakeAdapter (never on the live
    adapter — see the module docstring)."""
    original = _FakeAdapter.ping
    _FakeAdapter.ping = board_call_timing._wrap("ping", original)
    yield _FakeAdapter
    _FakeAdapter.ping = original


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """A clean board-read probe, and a guaranteed-unhooked gui.connection."""
    monkeypatch.setattr(board_read_probe, "default_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(board_read_probe, "_recording", False)
    monkeypatch.setattr(board_read_probe, "_fh", None)
    monkeypatch.setattr(board_read_probe, "_path", None)
    monkeypatch.setattr(board_read_probe, "_written", 0)
    monkeypatch.setattr(board_read_probe, "_bytes_written", 0)
    monkeypatch.setattr(board_read_probe, "_max_bytes",
                        board_read_probe.MAX_LOG_BYTES)
    monkeypatch.setattr(connection_mod, "board_read_probe", None)
    yield board_read_probe
    board_read_probe.stop()
    connection_mod.board_read_probe = None


def _rows(path):
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _calls(path):
    return [r for r in _rows(path) if r["method"] != "__install__"]


# ── Э1/Э5.1: OFF costs one flag test and nothing else ───────────────────────

def test_recording_off_writes_nothing_and_reads_no_stack_frame(
        recorder, patched_fake, monkeypatch, tmp_path):
    """Э5.1 — the mutation this guards: drop the flag test from the wrapper and
    the disabled recorder starts timing, counting and walking the stack."""
    sites = []
    monkeypatch.setattr(recorder, "_call_site",
                        lambda: sites.append("walked") or "<site>")
    recorder.set_ui_thread_predicate(lambda: True)  # even the UI hook is armed

    adapter = patched_fake()
    assert adapter.ping([1, 2]) == [1, 2]
    assert adapter.calls == [[1, 2]]                # the original still ran
    assert recorder.is_recording() is False
    assert sites == []                              # no frame was ever read
    assert list(tmp_path.iterdir()) == []           # and not one byte was written


def test_stop_without_a_session_is_a_no_op(recorder, caplog):
    """stop() is idempotent and silent when nothing is recording — the GUI calls
    it on every Apply with the switch OFF."""
    with caplog.at_level(logging.INFO, logger=board_call_timing.__name__):
        assert recorder.stop() == 0
        assert recorder.stop() == 0
    assert caplog.records == []


# ── Э1/Э5.2: start()/stop() contract ────────────────────────────────────────

def test_start_and_stop_are_idempotent_and_stop_counts_calls(
        recorder, patched_fake, tmp_path):
    """Э5.2 — a repeated start() must not open a second file (and the count is
    what the Log line and the report rely on)."""
    first = recorder.start()
    assert first == recorder.start()            # idempotent
    assert Path(first).parent == tmp_path

    adapter = patched_fake()
    for n in (1, 2, 3):
        adapter.ping([0] * n)

    assert recorder.stop() == 3
    assert recorder.stop() == 0                 # nothing left to close
    assert [r["method"] for r in _calls(first)] == ["ping", "ping", "ping"]
    assert [r["in_n"] for r in _calls(first)] == [1, 2, 3]   # arguments counted
    assert [r["out_n"] for r in _calls(first)] == [1, 2, 3]  # results counted


def test_a_failing_call_is_recorded_with_its_error(recorder):
    """Errors are part of the timing picture (the report has a failed-calls
    section), so an exception must be recorded and re-raised unchanged."""
    call = []

    def boom(self, items=None):
        call.append(items)
        raise ValueError("no board")

    _FakeAdapter.boom = board_call_timing._wrap("boom", boom)
    path = recorder.start()
    try:
        with pytest.raises(ValueError):
            _FakeAdapter().boom([1])
    finally:
        recorder.stop()
        del _FakeAdapter.boom

    row, = _calls(path)
    assert row["ok"] is False
    assert row["err"].startswith("ValueError: no board")
    assert call == [[1]]


def test_start_patches_the_adapter_and_writes_a_header_row(recorder, patched_fake):
    """The header row carries the patched-method count; the report skips it, and
    it does not count as a recorded call."""
    path = recorder.start()
    patched_fake().ping([1])
    assert recorder.stop() == 1                 # the header is not a call
    header, = [r for r in _rows(path) if r["method"] == "__install__"]
    assert header["out_n"] >= 1                 # methods patched by install_patch


def test_install_patch_is_idempotent_and_never_removes_the_wrapper():
    """Э1 — the patch is installed ONCE and never removed (removing it from a
    live class two threads are calling through is the race this project spent
    weeks fixing). Mutation: re-patch on every call, or restore the original,
    and this fails."""
    class StandIn:
        def plain(self):
            return 1

        def _private(self):
            return 2

        @property
        def prop(self):
            return 3

    first = board_call_timing.install_patch(StandIn)
    wrapped = StandIn.plain
    assert first >= 1
    assert getattr(wrapped, "_board_timing", False) is True

    assert board_call_timing.install_patch(StandIn) == 0   # idempotent
    assert StandIn.plain is wrapped                        # nothing was removed
    # Private methods, properties and the like are left alone.
    assert getattr(StandIn._private, "_board_timing", False) is False
    assert isinstance(StandIn.prop, property)


def test_install_patch_targets_the_live_adapter_by_default():
    """Without a target the patch goes onto KiCadBoardAdapter — that is the one
    the GUI's own socket calls go through."""
    from kicadstamp.kicad.adapter import KiCadBoardAdapter

    board_call_timing.install_patch()
    assert getattr(KiCadBoardAdapter.get_footprints, "_board_timing", False) is True
    assert board_call_timing.install_patch() == 0      # and only once


# ── Э2/Э5.4: two Log lines per session, never one per call ──────────────────

def test_exactly_two_log_lines_per_session_and_none_per_call(
        recorder, patched_fake, caplog):
    """Э5.4 — 81k calls would drown the Log, so the data goes to JSONL and only
    the session's start and stop are announced."""
    with caplog.at_level(logging.INFO, logger=board_call_timing.__name__):
        path = recorder.start()
        adapter = patched_fake()
        for _ in range(50):
            adapter.ping([1])
        recorder.stop()
        recorder.stop()                 # idempotent: no third line
        recorder.start()                # a second session
        recorder.stop()

    lines = [r.getMessage() for r in caplog.records
             if r.name == board_call_timing.__name__]
    assert len(lines) == 4
    assert path in lines[0] and "recording board calls" in lines[0]
    assert "stopped, 50 calls" in lines[1]
    assert lines[2].endswith(path)
    assert "stopped, 0 calls" in lines[3]


def test_startup_reminder_is_one_line_and_says_the_switch_was_left_on(
        recorder, caplog):
    """Э2 — a forgotten switch must not be silent, but the reminder REPLACES the
    plain start line (still two lines per session, not three)."""
    with caplog.at_level(logging.INFO, logger=board_call_timing.__name__):
        path = recorder.start(reminder=True)
        recorder.stop()

    lines = [r.getMessage() for r in caplog.records
             if r.name == board_call_timing.__name__]
    assert len(lines) == 2
    assert "left ON in Settings" in lines[0]
    assert "recording board calls" in lines[0]
    assert path in lines[0]


# ── Э2/Э5.5: the size cap stops the recording by itself ─────────────────────

def test_size_cap_stops_the_recording_and_logs_the_reason(
        recorder, patched_fake, caplog, monkeypatch):
    """Э5.5 — a switch left on over a weekend must not fill the disk: the
    recording stops itself and the Log carries the reason and the path."""
    monkeypatch.setattr(recorder, "_max_bytes", 500)
    with caplog.at_level(logging.INFO, logger=board_call_timing.__name__):
        path = recorder.start()
        adapter = patched_fake()
        for _ in range(200):
            adapter.ping([1, 2, 3])

        assert recorder.is_recording() is False     # stopped by itself
        assert recorder.stop() == 0                 # nothing left to close

    assert "size cap" in caplog.text
    assert path in caplog.text
    assert Path(path).stat().st_size < 2000         # bounded, not 200 rows
    assert len(_calls(path)) < 200


# ── Э3/Э5.6: the call site is recorded for the UI thread only ───────────────

def test_no_predicate_means_no_call_site_is_ever_recorded(recorder, patched_fake):
    """Э5.6, the CLI/MCP model: nothing is installed, so the recorder must not
    spend a stack walk — the rows carry neither `ui` nor `site`."""
    path = recorder.start()
    patched_fake().ping([1])
    recorder.stop()

    row, = _calls(path)
    assert "ui" not in row and "site" not in row


def test_call_site_is_recorded_only_where_the_predicate_says_so(
        recorder, patched_fake):
    """Э5.6 — the predicate is injected from outside (this module never imports
    Qt); rows from the "UI thread" carry `ui` + the caller's `file:line`, rows
    from anywhere else carry neither."""
    path = recorder.start()
    recorder.set_ui_thread_predicate(lambda: True)
    patched_fake().ping([1])
    recorder.set_ui_thread_predicate(lambda: False)
    patched_fake().ping([1, 2])
    recorder.set_ui_thread_predicate(None)          # cleared again
    patched_fake().ping([1, 2, 3])
    recorder.stop()

    ui_row, other_row, cleared_row = _calls(path)
    assert ui_row["ui"] is True
    assert "test_diagnostics_switch.py" in ui_row["site"]
    assert "site" not in other_row and "ui" not in other_row
    assert "site" not in cleared_row and "ui" not in cleared_row


# ── Э4/Э5.7: the report names the UI-thread calls ───────────────────────────

def _timing_row(ts, method, ms, depth=0, ui=False, site=None, thread="MainThread"):
    row = {"ts": ts, "method": method, "ms": ms, "ok": True, "thread": thread,
           "in_n": 0, "out_n": 1, "depth": depth}
    if ui:
        row["ui"] = True
        row["site"] = site
    return row


def _write_log(tmp_path, rows):
    path = tmp_path / "board_timing_1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def test_report_prints_the_ui_thread_section_by_method_and_site(
        tmp_path, capsys):
    """Э5.7 — this section is the INPUT of the follow-up "fix the offenders"
    task, so the method, the counts and the call site must all be readable
    without further digging."""
    path = _write_log(tmp_path, [
        _timing_row(1.0, "get_footprints", 152.0, ui=True,
                    site="/repo/gui/docks/chain.py:412"),
        _timing_row(1.2, "get_footprints", 40.0, ui=True,
                    site="/repo/gui/docks/chain.py:412"),
        _timing_row(1.4, "get_all_nets", 37.0, ui=True,
                    site="/repo/gui/docks/thermal_via.py:88"),
        _timing_row(2.0, "update_items", 10.0, thread="Dummy-1"),
    ])

    assert report_board_timing.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "UI-thread calls" in out
    assert "/repo/gui/docks/chain.py:412" in out
    assert "/repo/gui/docks/thermal_via.py:88" in out
    # Method, count, summed and maximum time of the worst offender, one line.
    assert "get_footprints" in out and "0.2s" in out and "152.0ms" in out
    assert "3 call(s)" in out and "0.2s on the UI thread" in out


def test_report_says_none_when_the_log_carries_no_call_sites(tmp_path, capsys):
    """A log produced by the external launcher (no predicate installed) must say
    WHY the section is empty instead of leaving a blank space to interpret."""
    path = _write_log(tmp_path, [
        _timing_row(1.0, "get_footprints", 152.0),
        _timing_row(2.0, "update_items", 10.0, thread="Dummy-1"),
    ])

    assert report_board_timing.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "no row carries a call site" in out
    assert "the external launcher installs none" in out


def test_report_says_none_when_ui_rows_are_nested_only(tmp_path, capsys):
    """`ui` rows that are all NESTED cannot be named: the section counts
    outermost rows only (the same rule the rest of the report uses), so it must
    say "none" rather than double-count a nested call."""
    path = _write_log(tmp_path, [
        _timing_row(1.0, "commit_with_retry", 9.0, depth=1, ui=True,
                    site="/repo/gui/docks/x.py:1"),
        _timing_row(2.0, "push_commit", 4.0, depth=1, ui=True,
                    site="/repo/gui/docks/x.py:2"),
    ])

    assert report_board_timing.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "no board call was made from the UI thread in this log" in out


# ── board_read_probe: the same contract for the "who reads" recorder ────────

def test_read_probe_start_stop_contract(probe, tmp_path):
    """Э1 — enable()/disable() keep working (the external launcher uses them),
    and the new start()/stop() are the same thing with the same idempotency."""
    path = probe.start()
    assert path == probe.enable()               # both are idempotent

    connection = BoardConnection()
    connection.board = object()
    _ = connection.board                        # one recorded read

    assert probe.stop() == 1
    assert probe.disable() == 0                 # nothing left to close
    assert connection_mod.board_read_probe is None

    row, = [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "test_diagnostics_switch.py" in row["site"]
    assert row["thread"] == threading.current_thread().name


def test_read_probe_writes_nothing_while_stopped(probe, tmp_path):
    """Э5.1 for the reads recorder: the hook is removed on stop(), so a read
    afterwards costs one `is None` test and writes nothing."""
    path = probe.start()
    connection = BoardConnection()
    connection.board = object()
    _ = connection.board
    probe.stop()

    before = Path(path).read_text(encoding="utf-8")
    _ = connection.board
    assert Path(path).read_text(encoding="utf-8") == before


def test_read_probe_logs_two_lines_per_session_and_stops_at_the_cap(
        probe, caplog, monkeypatch):
    """Э2/Э5.4/Э5.5 for the reads recorder: two lines per session, and the size
    cap stops it by itself with the reason in the Log."""
    with caplog.at_level(logging.INFO, logger=board_read_probe.__name__):
        probe.start()
        probe.stop()
        probe.stop()

    lines = [r.getMessage() for r in caplog.records
             if r.name == board_read_probe.__name__]
    assert len(lines) == 2
    assert "recording board reads" in lines[0]
    assert "stopped, 0 reads" in lines[1]

    monkeypatch.setattr(probe, "_max_bytes", 200)
    with caplog.at_level(logging.INFO, logger=board_read_probe.__name__):
        path = probe.start()
        connection = BoardConnection()
        connection.board = object()
        for _ in range(100):
            _ = connection.board

        assert probe.is_recording() is False    # stopped by itself
        assert probe.stop() == 0

    assert "size cap" in caplog.text and path in caplog.text
    assert Path(path).stat().st_size < 1000
