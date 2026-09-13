#!/usr/bin/env python3
"""Launches the normal GUI with every read of BoardConnection.board recorded.

Input        — the same arguments kicadstamp_gui.py takes; they are passed through.
Expected     — the GUI as usual, plus <repo>/diagnostics/board_reads_<pid>.jsonl.
Live KiCad   — Partially (the GUI starts either way; a live board is what produces reads).
Run          — python -m kicadstamp.diagnostics.run_gui_with_read_probe

Work the docks as usual for five minutes or so — an Extract, a tree read, selection,
a placement — then quit and summarise with:

    python -m kicadstamp.diagnostics.report_board_reads

Nothing is written anywhere near the KiCad project; the log lands in <repo>/diagnostics/,
which is gitignored. Production code is untouched: the hook already lives in
gui/connection.py (disabled by default) and this script is the only thing that turns it on.
"""
from kicadstamp.diagnostics.board_read_probe import enable

enable()

from kicadstamp.gui_main import main  # noqa: E402  — must follow enable()


if __name__ == "__main__":
    main()
