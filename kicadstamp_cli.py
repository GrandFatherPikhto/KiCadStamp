#!.venv/bin/python
"""Thin wrapper around kicadstamp.cli_main:main() — keeps the
``python kicadstamp_cli.py`` dev workflow working from a checkout. The real
CLI lives in kicadstamp/cli_main.py (also the pyproject.toml console_scripts
entry point).

Usage:
    python kicadstamp_cli.py apply config.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python kicadstamp_cli.py undo [--verbose]
"""
import sys
from pathlib import Path

# Project root isn't guaranteed to be importable when this file runs as a
# bare script from a working directory outside the repo.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent))

from kicadstamp.cli_main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
