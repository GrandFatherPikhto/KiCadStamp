#!.venv/bin/python
"""Thin wrapper around kicadstamp.gui_main:main() — keeps the
``python kicadstamp_gui.py`` dev workflow working from a checkout. The real
GUI entry point lives in kicadstamp/gui_main.py (also the pyproject.toml
console_scripts entry point).

Usage:
    python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
"""
import sys
from pathlib import Path

# Project root isn't guaranteed to be importable when this file runs as a
# bare script from a working directory outside the repo.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent))

from kicadstamp.gui_main import main  # noqa: E402

if __name__ == "__main__":
    main()
