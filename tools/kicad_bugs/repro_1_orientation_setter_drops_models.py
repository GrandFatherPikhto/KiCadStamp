"""kicad-python: FootprintInstance.orientation setter drops 3D models.

Open the sample board in the PCB editor, then:
    python repro_1_orientation_setter_drops_models.py          # local only, board untouched
    python repro_1_orientation_setter_drops_models.py --write  # also send it back with update_items
"""
import sys

from kipy import KiCad

REF = "U3"

kicad = KiCad()
board = kicad.get_board()
print("KiCad", kicad.get_version())


def find(ref):
    return next(f for f in board.get_footprints() if f.reference_field.text.value == ref)


fp = find(REF)
print(f"{REF} before: {len(fp.definition.models)} 3D model(s), {len(fp.definition.items)} items")

fp.orientation = fp.orientation  # the same angle: nothing should change

print(f"{REF} after:  {len(fp.definition.models)} 3D model(s), {len(fp.definition.items)} items")

if "--write" in sys.argv:
    board.update_items([fp])
    fp = find(REF)
    print(f"{REF} on the board after update_items: {len(fp.definition.models)} 3D model(s)")
