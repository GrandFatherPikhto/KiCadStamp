#!/usr/bin/env python3
"""Regression: the CLI import path must not pay for kipy unless the command
actually needs IPC.

kicadstamp/cli.py used to import KiCadBoardAdapter (kipy + protobuf + pynng),
kicadstamp/cli_common.py imported kipy.errors, and kicadstamp/cli_main.py
imported kicadstamp.apply_pipeline (which pulls kicadstamp.kicad.adapter) — all
at module level, so even `flatten` (a pure file operation) paid for the whole
kipy import chain. These checks run in a subprocess so kipy already being
imported by the test process (via conftest/other tests) cannot mask a
regression."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _module_imports_kipy(module: str) -> bool:
    code = f"import {module}; import sys; print('kipy' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "True"


class TestCliImportPathIsKipyFree:
    def test_cli_module_does_not_import_kipy(self):
        assert not _module_imports_kipy("kicadstamp.cli")

    def test_cli_main_module_does_not_import_kipy(self):
        assert not _module_imports_kipy("kicadstamp.cli_main")
