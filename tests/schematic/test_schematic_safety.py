# tests/test_schematic_safety.py
"""
list_kicad_processes()/kill_kicad_process() (added 2026-08-03, for
gui/kicad_processes_dialog.py) — a crashed/frozen kicad.exe left running
alongside a fresh one blocked the fresh one's IPC connection; these give a
human-facing picker enough information (PID/status/title) to pick and
force-close the right one, never an automated decision (see
kill_kicad_process's own docstring for why that distinction matters).
"""
import os
import subprocess

import pytest

from kicadstamp.schematic_safety import (KicadProcessInfo, kill_kicad_process,
                                          list_kicad_pids, list_kicad_processes)

# The Windows branch is exercised here. The psutil (non-Windows) branch is
# exercised separately at the bottom of this file via
# pytest.mark.skipif(os.name != "posix"): os.name is a process-wide value
# pathlib/pytest also consult internally, so flipping it mid-test is unsafe
# on a Windows test runner (verified live: it corrupts pytest's own
# failure-report formatting). On a POSIX runner the posix tests run for real;
# on a Windows runner they're skipped.

# A real two-process tasklist /FO CSV /V /NH capture (2026-08-03, one
# crashed/Not Responding kicad.exe left over, one fresh/Running one) —
# cp866-encoded bytes, exactly what subprocess.run returns on this console.
_REAL_TASKLIST_CSV = (
    '"kicad.exe","36828","Console","1","1 162 196 K","Not Responding",'
    '"TESSERACT\\grand","0:01:06","N/A"\r\n'
    '"kicad.exe","26736","Console","1","3 506 680 K","Running",'
    '"TESSERACT\\grand","0:00:41","3CH-AWG-TIA - Редактор '
    'печатных плат"\r\n'
).encode("cp866")


def _fake_run(returncode=0, stdout=b"", stderr=b""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
    return run


def test_list_kicad_processes_parses_windows_tasklist_output(monkeypatch):
    monkeypatch.setattr("kicadstamp.schematic_safety.os.name", "nt")
    monkeypatch.setattr("kicadstamp.schematic_safety.subprocess.run",
                        _fake_run(stdout=_REAL_TASKLIST_CSV))

    processes = list_kicad_processes()

    assert processes == [
        KicadProcessInfo(pid=26736, status="Running",
                          title="3CH-AWG-TIA - Редактор печатных плат"),
        KicadProcessInfo(pid=36828, status="Not Responding", title=None),
    ]


def test_list_kicad_processes_empty_when_none_running(monkeypatch):
    monkeypatch.setattr("kicadstamp.schematic_safety.os.name", "nt")
    monkeypatch.setattr("kicadstamp.schematic_safety.subprocess.run", _fake_run(stdout=b""))

    assert list_kicad_processes() == []


def test_list_kicad_processes_swallows_errors(monkeypatch):
    """Same "never crash the caller" discipline as list_kicad_pids() —
    a broken tasklist call just yields an empty list, not an exception."""
    monkeypatch.setattr("kicadstamp.schematic_safety.os.name", "nt")

    def _boom(*a, **k):
        raise OSError("tasklist not found")
    monkeypatch.setattr("kicadstamp.schematic_safety.subprocess.run", _boom)

    assert list_kicad_processes() == []


def test_kill_kicad_process_windows_success(monkeypatch):
    monkeypatch.setattr("kicadstamp.schematic_safety.os.name", "nt")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr("kicadstamp.schematic_safety.subprocess.run", run)

    kill_kicad_process(36828)

    assert calls == [["taskkill", "/PID", "36828", "/F"]]


def test_kill_kicad_process_windows_failure_raises_with_diagnostic(monkeypatch):
    monkeypatch.setattr("kicadstamp.schematic_safety.os.name", "nt")
    monkeypatch.setattr(
        "kicadstamp.schematic_safety.subprocess.run",
        _fake_run(returncode=1, stderr="ERROR: The process could not be terminated.".encode("cp866")))

    with pytest.raises(RuntimeError, match="could not be terminated"):
        kill_kicad_process(36828)


# ---------------------------------------------------------------- posix branch
#
# The psutil (non-Windows) branch is exercised here, guarded so a Windows
# test runner simply skips it. The branch must match the EXACT image name
# ("kicad"), not a substring (2026-08-23: a kicadstamp-family helper like
# kicadstamp_gui.py / analyze_kicad_c.py was picked as a "KiCad process" and
# force-closed by hand), and must skip zombies (a crashed kicad lingers in
# state Z until its parent reaps it while psutil still reports its name).

class _FakePsutilProc:
    def __init__(self, pid, name, status):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "status": status}


def _fake_process_iter(procs):
    def _iter(attrs=None):
        return iter(procs)
    return _iter


@pytest.mark.skipif(os.name != "posix",
                    reason="exercises the psutil (non-Windows) branch")
def test_list_kicad_pids_posix_exact_name_and_zombie_filter(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", _fake_process_iter([
        _FakePsutilProc(100, "kicad", "running"),          # exact match -> kept
        _FakePsutilProc(101, "kicad", "zombie"),           # zombie -> skipped
        _FakePsutilProc(102, "kicadstamp_gui.", "running"),  # substring only -> skipped
        _FakePsutilProc(103, "analyze_kicad_c", "running"),  # substring only -> skipped
        _FakePsutilProc(104, "KICAD", "sleeping"),         # case-insensitive exact -> kept
        _FakePsutilProc(105, "kicad", None),               # unknown status -> kept
    ]))

    assert list_kicad_pids() == [100, 104, 105]


@pytest.mark.skipif(os.name != "posix",
                    reason="exercises the psutil (non-Windows) branch")
def test_list_kicad_processes_posix_exact_name_and_zombie_filter(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", _fake_process_iter([
        _FakePsutilProc(100, "kicad", "running"),
        _FakePsutilProc(101, "kicad", "zombie"),
        _FakePsutilProc(102, "kicadstamp_gui.", "running"),
        _FakePsutilProc(103, "Kicad", "sleeping"),
    ]))

    assert list_kicad_processes() == [
        KicadProcessInfo(pid=100),
        KicadProcessInfo(pid=103),
    ]


