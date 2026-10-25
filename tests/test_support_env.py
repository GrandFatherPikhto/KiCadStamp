# tests/test_support_env.py
"""The cell for `tests/support_env.isolate_home` — one assertion per contract.

Why a cell for a test helper: Д-5(a) of the Ч2а acceptance was six cells that set only `HOME`
and were red on `windows-latest` (where `Path.home()` reads `USERPROFILE`). A helper that
silently kept only one half would look exactly as green on Linux as the broken cells did, so
the helper itself is pinned here — both names, one directory, created."""
import os
from pathlib import Path

from tests.support_env import isolate_home


def test_isolate_home_sets_both_names_and_creates_the_directory(monkeypatch, tmp_path):
    """`HOME` and `USERPROFILE` must name the SAME directory — that is what makes a cell read
    the same home on Linux, macOS and Windows."""
    target = tmp_path / "home"

    returned = isolate_home(monkeypatch, target)

    assert returned == target
    assert Path(os.environ["HOME"]) == target
    assert Path(os.environ["USERPROFILE"]) == target, \
        "Path.home() reads USERPROFILE on Windows — HOME alone is not isolation"
    assert target.is_dir()


def test_isolate_home_overrides_whatever_the_runner_had(monkeypatch, tmp_path):
    """A runner with its OWN home (GitHub's is `C:/Users/runneradmin`) must not leak in."""
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "runner-profile"))

    isolate_home(monkeypatch, tmp_path / "mine")

    assert Path(os.environ["HOME"]) == tmp_path / "mine"
    assert Path(os.environ["USERPROFILE"]) == tmp_path / "mine"
