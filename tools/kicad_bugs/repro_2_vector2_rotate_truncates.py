"""kicad-python: Vector2.rotate truncates (int) instead of rounding.

No KiCad needed:
    python repro_2_vector2_rotate_truncates.py
"""
from kipy.geometry import Angle, Vector2

v = Vector2.from_xy(-1_800_000, 960_000)          # nm
v.rotate(Angle.from_degrees(90), Vector2.from_xy(0, 0))

print("got:     ", v.x, v.y)
print("expected:", 960_000, 1_800_000)
