#!/usr/bin/env python3
"""
probe_ierarchy_uuid.py — prints each footprint's sheet_path.path raw form.

Input:
    None (reads the live board).

Expected:
    For every footprint: refdes and the repr of sheet_path.path, to see whether
    it is a tuple of UUID objects (and how to stringify them).

Live KiCad:
    Yes — requires a running KiCad with the board open.

Run:
    python -m kicadstamp.diagnostics.probe_ierarchy_uuid
"""
import kipy
kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    # path — это кортеж UUID? Или список объектов?
    print(f"{ref:10s}  path={sp.path!r}")  # посмотрим, что там
    # если это список UUID, можно попробовать вывести их строковое представление