"""KiCad: pad numbers and pad net names stop being drawn after IPC UpdateItems on a NEW board.

Observed (Denis, 21.10.2026): on a board created in the current session (new project,
Update PCB from Schematic), moving footprints through the IPC API now and then makes
the pad numbers AND the pad net names disappear from the canvas — on every footprint.
They come back only after KiCad is restarted. The data is intact: GetItems returns every
pad with its number and net. A board opened from disk after the restart does not show it.

WRITES THE BOARD only with --write. Use a throw-away board. Steps:
  1. Start KiCad, create a new project, a few parts in the schematic, Tools -> Update PCB
     from Schematic (F8). Do NOT close/reopen the board. Zoom in until pad numbers are drawn.
  2. python repro_5_pad_numbers_vanish_after_update_items.py --write --loop 20
     -> moves EVERY footprint by +1 mm and back, 20 times (one update_items per step,
        the way an automated placement does). Watch the canvas.
  3. Control: save, restart KiCad, open the same board, repeat step 2.
Other modes: [REF] --write  (send one footprint back unchanged);
             [REF] --write --move  (move one footprint +1 mm and back, waits for Enter).

usage: python repro_5_pad_numbers_vanish_after_update_items.py [REF] [--write] [--move] [--loop N]
"""
import sys
import time

from kipy import KiCad
from kipy.geometry import Vector2

args = [a for a in sys.argv[1:] if not a.startswith("--")]
WRITE = "--write" in sys.argv
MOVE = "--move" in sys.argv
LOOP = int(sys.argv[sys.argv.index("--loop") + 1]) if "--loop" in sys.argv else 0
REF = args[0] if args and not (LOOP and args[0] == str(LOOP)) else "D1"

kicad = KiCad()
board = kicad.get_board()
print("KiCad", kicad.get_version(), "| board", board.name)


def find(ref):
    return next(f for f in board.get_footprints() if f.reference_field.text.value == ref)


def pads(fp):
    return [(p.number, p.net.name) for p in fp.definition.pads]


if not WRITE:
    for fp in board.get_footprints():
        print(f"{fp.reference_field.text.value}: {pads(fp)}")
    print("read-only run: add --write to change the board")
    sys.exit(0)

if LOOP:
    for i in range(1, LOOP + 1):
        for dx in (1_000_000, -1_000_000):
            fps = board.get_footprints()
            for fp in fps:
                pos = fp.position
                fp.position = Vector2.from_xy(pos.x + dx, pos.y)
            board.update_items(fps)
            time.sleep(0.3)
        print(f"step {i}/{LOOP}: every footprint moved +1 mm and back — numbers still drawn?")
    print("done; every footprint is back at its place")
    sys.exit(0)

fp = find(REF)
print(f"{REF} pads before: {pads(fp)}")
if MOVE:
    pos = fp.position
    fp.position = Vector2.from_xy(pos.x + 1_000_000, pos.y)
    board.update_items([fp])
    print(f"{REF} moved +1 mm — look at the canvas, then press Enter to move it back")
    input()
    fp = find(REF)
    fp.position = pos
    board.update_items([fp])
    print(f"{REF} moved back")
else:
    board.update_items([fp])  # sent back unchanged
    print(f"{REF} sent back unchanged — look at the canvas")
print(f"{REF} pads after:  {pads(find(REF))}")
