# tests/gui/test_entry_points.py
"""
Phase 5.3 — the GUI entry point (kicadstamp_gui.py) must stay importable as
a plain module after the sys.path cleanup: loading it defines a callable
main() without spawning an event loop, and the `if __package__` guard (a
bare module import has __package__ == "") still puts the project root on
sys.path so the internal `gui`/`kicadstamp` imports resolve.

fieldstool_gui.py (the standalone fieldstool entry point) was retired
2026-08-02 — fieldstool is now only ever embedded, see gui/docks/
fieldstool_dock.py.
"""
import importlib.util

import pytest

# Ф2.0: depth-independent (tests/paths.py).
from tests.paths import REPO_ROOT as PROJECT_ROOT


def _load_entry_point(name: str):
    path = PROJECT_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot build spec for {name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["kicadstamp_gui"])
def test_gui_entry_point_imports_and_exposes_main(name):
    module = _load_entry_point(name)
    assert callable(module.main), f"{name}.main should be callable"
