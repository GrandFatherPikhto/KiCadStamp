#!/usr/bin/env python3
"""--version/-V (added 2026-07-30) — kicadstamp_cli.py's own bare-config-path
convenience (sys.argv[1] not a known subcommand -> insert 'apply') would
otherwise rewrite `kicadstamp_cli.py --version` into `apply --version` and
fail as an unrecognised apply argument instead of printing the version."""
import sys

import pytest

from kicadstamp import __version__
from kicadstamp_cli import main


class TestVersionFlag:
    def test_version_string_is_well_formed(self):
        parts = __version__.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    @pytest.mark.parametrize("flag", ["--version", "-V"])
    def test_long_and_short_flag_print_version_and_exit_zero(self, flag, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["kicadstamp_cli.py", flag])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out

    def test_not_rewritten_to_apply_subcommand(self, monkeypatch, capsys):
        """Regression: without the exemption in main()'s bare-path -> 'apply'
        rewrite, this would become ['kicadstamp_cli.py', 'apply', '--version']
        and fail with an argparse error (apply has no --version), not print
        the version."""
        monkeypatch.setattr(sys, "argv", ["kicadstamp_cli.py", "--version"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
