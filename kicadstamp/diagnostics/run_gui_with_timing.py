#!/usr/bin/env python3
"""Launches the normal GUI with every KiCadBoardAdapter call timed.

Input        — the same arguments kicadstamp_gui.py takes; they are passed through.
Expected     — the GUI as usual, plus <repo>/diagnostics/board_timing_<pid>.jsonl.
Live KiCad   — Partially (the GUI starts either way; a live board is what produces calls).
Run          — python -m kicadstamp.diagnostics.run_gui_with_timing

Work the docks as usual for ten minutes or so — an Extract, a tree read, selection, a
placement — then quit and summarise with:

    python -m kicadstamp.diagnostics.report_board_timing

Nothing is written anywhere near the KiCad project; the log lands in <repo>/diagnostics/,
which is gitignored. Production code is untouched: the timing is a monkey patch installed
here, before the GUI is imported (see board_call_timing.install).
"""
from kicadstamp.diagnostics.board_call_timing import install

install()

from kicadstamp.gui_main import main  # noqa: E402  — must follow install()


if __name__ == "__main__":
    main()
