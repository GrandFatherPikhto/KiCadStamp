# tests/support_env.py
"""One helper for "this cell OWNS the home directory".

`pathlib.Path.home()` reads `USERPROFILE` on Windows and `HOME` everywhere else — and on
Windows it does not look at `HOME` at all. A cell that only sets `HOME` therefore passes on
Linux and on CI's `ubuntu-latest`, and reads the runner's real `C:/Users/runneradmin` on
`windows-latest`: six cells of 22.10.2026 were red there for exactly that reason (Д-5(a) of
the Ч2а acceptance of plan_2026_10_23_checks_tab_top_level).

Setting BOTH variables is the only portable way to say "HOME is mine". Keep this the ONE
place that does it — a per-file copy is how the two halves drift apart again. Its own cell is
`tests/test_support_env.py` (a helper nobody checks is a helper that quietly loses a half).
"""
from pathlib import Path


def isolate_home(monkeypatch, path) -> Path:
    """Point the home directory of every API a test can reach at `path`.

    Sets `HOME` (Linux/macOS, and the many tools that read it) and `USERPROFILE` (what
    `Path.home()` actually uses on Windows). The directory is created, so a cell does not have
    to mkdir it first. Returns the path for convenience."""
    home = Path(path)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home
