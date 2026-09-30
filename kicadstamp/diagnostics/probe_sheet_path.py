#!/usr/bin/env python3
"""
probe_sheet_path.py — one-off check of what fp.sheet_path.path_human_readable
actually returns on a live board with cloned sheets.

Input:
    None (reads the live board).

Expected:
    For every footprint: refdes, path_human_readable, and the length of
    sheet_path.path — confirming whether KiCad stores readable sheet names in
    the PCB at all.

Live KiCad:
    Yes — requires a running KiCad with a board that has cloned sheets.

Run:
    python -m kicadstamp.diagnostics.probe_sheet_path
"""
import kipy
from kipy.board_types import FootprintInstance

kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

print(f"Всего футпринтов: {len(footprints)}\n")
for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    print(f"{ref:10s}  path_human_readable={sp.path_human_readable!r}  "
          f"path (UUID-чейн, длина)={len(sp.path)}")