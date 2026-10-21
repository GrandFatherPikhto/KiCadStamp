"""KiCad: UpdateItems drops the repeated closing point of a footprint polygon.

WRITES THE BOARD (one update_items call that changes nothing). Open the sample
board (Test02) in the PCB editor, make U1 fresh from the library first (right click
U1 -> Update Footprint from Library; its pin-1 polygon then has 4 points), then:
    python repro_3_update_items_drops_polygon_closing_point.py [REF]
REF defaults to U1 (SOT-223-3_TabPin2, the footprint whose polygon repeats its first point).
"""
import sys

from kipy import KiCad
from kipy.board_types import BoardPolygon

args = [a for a in sys.argv[1:] if not a.startswith("--")]
REF = args[0] if args else "U1"

kicad = KiCad()
board = kicad.get_board()
print("KiCad", kicad.get_version(), "| board", board.name)


def find(ref):
    return next(f for f in board.get_footprints() if f.reference_field.text.value == ref)


def polygon_points(fp):
    return [len(p.outline) for s in fp.definition.shapes if isinstance(s, BoardPolygon)
            for p in s.polygons]


fp = find(REF)
print(f"{REF} polygon points before: {polygon_points(fp)}")

board.update_items([fp])  # sent back unchanged

print(f"{REF} polygon points after:  {polygon_points(find(REF))}")
