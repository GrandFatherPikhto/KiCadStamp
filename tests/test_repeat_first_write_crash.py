# tests/test_repeat_first_write_crash.py
"""Tests for tools/repeat_first_write_crash.py.

The script itself drives a real KiCad instance, so it can't be run in CI.
What CAN be tested in isolation is the process-verification logic added
2026-08-22: _list_kicad_pids() (the tasklist/psutil enumeration) and
wait_until_dead() (polling that list until every kicad process is gone).
Both are tested here against mocked PID sources, with time fully mocked so
the tests don't actually sleep.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_tool_module():
    """Load tools/repeat_first_write_crash.py by file path — tools/ is not a
    package, so it can't be imported by module name."""
    path = Path(__file__).resolve().parents[1] / "tools" / "repeat_first_write_crash.py"
    spec = importlib.util.spec_from_file_location("repeat_first_write_crash", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool():
    return _load_tool_module()


# ---------------------------------------------------------- _list_kicad_pids


def test_list_kicad_pids_windows_parses_tasklist_csv(monkeypatch, tool):
    fake_stdout = (
        '"kicad.exe","1234","Console","1","12,345 K"\r\n'
        '"kicad.exe","5678","Console","1","12,345 K"\r\n'
    )

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        assert cmd[0] == "tasklist"
        assert capture_output and text and timeout == 10
        return type("R", (), {"stdout": fake_stdout})()

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    assert tool._list_kicad_pids() == [1234, 5678]


def test_list_kicad_pids_windows_ignores_non_kicad_lines(monkeypatch, tool):
    fake_stdout = (
        '"kicad.exe","1234","Console","1","12,345 K"\r\n'
        '"notepad.exe","9999","Console","1","5,000 K"\r\n'
    )

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        return type("R", (), {"stdout": fake_stdout})()

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    assert tool._list_kicad_pids() == [1234]


def test_list_kicad_pids_psutil_filters_by_name(monkeypatch, tool):
    class _Proc:
        def __init__(self, pid, name):
            self.pid = pid
            self.info = {"name": name}

    class _FakePsutil:
        def process_iter(self, attrs):
            return [
                _Proc(101, "kicad"),
                _Proc(202, "KICAD_NIGHTLY"),
                _Proc(303, "notepad.exe"),
                _Proc(404, None),
            ]

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil())

    assert tool._list_kicad_pids() == [101, 202]


def test_list_kicad_pids_returns_empty_on_enumeration_error(monkeypatch, tool):
    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        raise OSError("tasklist not available")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    assert tool._list_kicad_pids() == []


# ------------------------------------------------------------- wait_until_dead


def test_wait_until_dead_true_when_nothing_running(monkeypatch, tool):
    monkeypatch.setattr(tool, "_list_kicad_pids", lambda: [])

    assert tool.wait_until_dead(timeout_s=1.0, poll_s=0.5) is True


def test_wait_until_dead_true_when_process_disappears(monkeypatch, tool):
    calls = {"n": 0}

    def fake_pids():
        calls["n"] += 1
        return [111] if calls["n"] == 1 else []

    monkeypatch.setattr(tool, "_list_kicad_pids", fake_pids)
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(tool.time, "monotonic", lambda: 0.0)

    assert tool.wait_until_dead(timeout_s=5.0, poll_s=0.5) is True
    assert calls["n"] == 2


def test_wait_until_dead_false_when_survivor_persists(monkeypatch, tool):
    t = {"now": 0.0}

    def fake_monotonic():
        t["now"] += 0.6
        return t["now"]

    monkeypatch.setattr(tool, "_list_kicad_pids", lambda: [999])
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(tool.time, "monotonic", fake_monotonic)

    # Timeout 1.0s, clock advances 0.6s per call: first poll sees a survivor
    # (remaining 0.4s > 0), second poll crosses the deadline -> False.
    assert tool.wait_until_dead(timeout_s=1.0, poll_s=0.5) is False


def test_wait_until_dead_true_when_survivor_dies_at_deadline(monkeypatch, tool):
    """Edge case behind the final re-check: the loop times out with a
    survivor, but the process dies right at the deadline, so the last
    (post-loop) enumeration sees it gone -> True."""
    t = {"now": 0.0}

    def fake_monotonic():
        t["now"] += 0.6
        return t["now"]

    calls = {"n": 0}

    def fake_pids():
        calls["n"] += 1
        # First two calls are the in-loop polls (survivor), the third is the
        # post-deadline re-check (gone).
        return [777] if calls["n"] <= 2 else []

    monkeypatch.setattr(tool, "_list_kicad_pids", fake_pids)
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(tool.time, "monotonic", fake_monotonic)

    assert tool.wait_until_dead(timeout_s=1.0, poll_s=0.5) is True
