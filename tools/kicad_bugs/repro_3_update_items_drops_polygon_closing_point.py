"""KiCad: UpdateItems drops the repeated closing point of a footprint polygon.

WRITES THE BOARD (one update_items call that changes nothing). Open the sample
board in the PCB editor (U3 fresh from the library: Tools -> Update Footprints
From Library first), then:
    python repro_3_update_items_drops_polygon_closing_point.py
"""
from kipy import KiCad
from kipy.board_types import BoardPolygon

REF = "U3"

kicad = KiCad()
board = kicad.get_board()
print("KiCad", kicad.get_version())


def find(ref):
    return next(f for f in board.get_footprints() if f.reference_field.text.value == ref)


def polygon_points(fp):
    return [len(p.outline) for s in fp.definition.shapes if isinstance(s, BoardPolygon)
            for p in s.polygons]


fp = find(REF)
print(f"{REF} polygon points before: {polygon_points(fp)}")

board.update_items([fp])  # sent back unchanged

print(f"{REF} polygon points after:  {polygon_points(find(REF))}")
