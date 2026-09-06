# kicadstamp/domain/geometry.py
"""Domain value types for board geometry, decoupling KiCadStamp from kipy.

P1-4 follow-up (completed 2026-08-25): the kipy geometric/enum *value* types
(``Vector2``, ``Angle``, ``BoardLayer``, ``Box2``) are replaced by domain types
of the same name and API, so consumers no longer import ``kipy.geometry`` /
``kipy.board_types`` at all. Only the adapter seam (``kicad/adapter.py``)
converts kipy <-> domain.

The types deliberately mirror kipy's exact semantics (nanometre integers,
KiCad's Y-down rotation, in-place ``rotate``) so the migration is a pure import
swap for consumers; the kipy<->domain conversion happens only in the adapter
seam.
"""

from __future__ import annotations

import math
from enum import Enum


def _normalize_angle_radians(angle: float) -> float:
    """Normalise an angle to [0, 2*pi) — same as kipy.geometry."""
    while angle < 0.0:
        angle += 2 * math.pi
    while angle >= 2 * math.pi:
        angle -= 2 * math.pi
    return angle


class Vector2:
    """A 2D point/vector in nanometres — API-compatible with kipy.Vector2."""

    __slots__ = ("x", "y")

    def __init__(self, x: int = 0, y: int = 0):
        self.x = int(x)
        self.y = int(y)

    def __repr__(self) -> str:
        return f"Vector2({self.x}, {self.y})"

    @classmethod
    def from_xy(cls, x_nm: int, y_nm: int) -> "Vector2":
        return cls(int(x_nm), int(y_nm))

    @classmethod
    def from_xy_mm(cls, x_mm: float, y_mm: float) -> "Vector2":
        return cls(int(x_mm * 1_000_000), int(y_mm * 1_000_000))

    def __hash__(self) -> int:
        return hash((self.x, self.y))

    def __eq__(self, other) -> bool:
        if isinstance(other, Vector2):
            return self.x == other.x and self.y == other.y
        return NotImplemented

    def __add__(self, other: "Vector2") -> "Vector2":
        return Vector2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vector2") -> "Vector2":
        return Vector2(self.x - other.x, self.y - other.y)

    def __neg__(self) -> "Vector2":
        return Vector2(-self.x, -self.y)

    def __mul__(self, scalar: float) -> "Vector2":
        return Vector2(int(float(self.x) * scalar), int(float(self.y) * scalar))

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y)

    def angle(self) -> float:
        """Direction of the vector, in radians."""
        return math.atan2(self.y, self.x)

    def angle_degrees(self) -> float:
        return math.degrees(self.angle())

    def rotate(self, angle: "Angle", center: "Vector2") -> "Vector2":
        """Rotate in place by ``angle`` degrees around ``center`` (KiCad's
        Y-down convention, identical to kipy.Vector2.rotate) and return self."""
        pt_x = self.x - center.x
        pt_y = self.y - center.y
        rotation = _normalize_angle_radians(angle.to_radians())

        sin_angle = math.sin(rotation)
        cos_angle = math.cos(rotation)

        self.x = int(pt_y * sin_angle + pt_x * cos_angle) + center.x
        self.y = int(pt_y * cos_angle - pt_x * sin_angle) + center.y
        return self


class Angle:
    """An angle in degrees — API-compatible with kipy.Angle."""

    __slots__ = ("degrees",)

    def __init__(self, degrees: float = 0.0):
        self.degrees = degrees

    def __repr__(self) -> str:
        return f"Angle({self.degrees})"

    @classmethod
    def from_degrees(cls, degrees: float) -> "Angle":
        return cls(degrees)

    def __eq__(self, other) -> bool:
        if isinstance(other, Angle):
            return self.degrees == other.degrees
        return NotImplemented

    def __add__(self, other: "Angle") -> "Angle":
        return Angle(self.degrees + other.degrees)

    def __sub__(self, other: "Angle") -> "Angle":
        return Angle(self.degrees - other.degrees)

    def __neg__(self) -> "Angle":
        return Angle(-self.degrees)

    def __mul__(self, scalar: float) -> "Angle":
        return Angle(self.degrees * scalar)

    def to_radians(self) -> float:
        return math.radians(self.degrees)

    def normalize(self) -> "Angle":
        """Normalise to [0, 360)."""
        while self.degrees < 0.0:
            self.degrees += 360.0
        while self.degrees >= 360.0:
            self.degrees -= 360.0
        return self

    def normalize180(self) -> "Angle":
        """Normalise to [-180, 180)."""
        while self.degrees <= -180.0:
            self.degrees += 360.0
        while self.degrees > 180.0:
            self.degrees -= 360.0
        return self


class BoardLayer(Enum):
    """A copper board layer — the full copper set of kipy's BoardLayer
    (BL_F_Cu, BL_In1_Cu..BL_In30_Cu, BL_B_Cu).

    Values are domain-internal (the adapter maps kipy <-> domain explicitly;
    nothing in the domain depends on them matching kipy's numbering). The two
    historical members keep their values (BL_F_Cu == 0, BL_B_Cu == 32 — 31 is
    left unused) so existing 2-layer logic is untouched.

    Added 2026-09-06 (plan_2026_09_05_scheme_list.md Step 0): the project
    boards are 4-copper-layer (F.Cu/In1.Cu/In2.Cu/B.Cu) and capture reads
    "all copper layers of the real stack" — a binary F/B enum made
    domain/board.py::_layer_from_kipy silently map inner-layer tracks to F.Cu.
    """

    BL_F_Cu = 0
    BL_In1_Cu = 1
    BL_In2_Cu = 2
    BL_In3_Cu = 3
    BL_In4_Cu = 4
    BL_In5_Cu = 5
    BL_In6_Cu = 6
    BL_In7_Cu = 7
    BL_In8_Cu = 8
    BL_In9_Cu = 9
    BL_In10_Cu = 10
    BL_In11_Cu = 11
    BL_In12_Cu = 12
    BL_In13_Cu = 13
    BL_In14_Cu = 14
    BL_In15_Cu = 15
    BL_In16_Cu = 16
    BL_In17_Cu = 17
    BL_In18_Cu = 18
    BL_In19_Cu = 19
    BL_In20_Cu = 20
    BL_In21_Cu = 21
    BL_In22_Cu = 22
    BL_In23_Cu = 23
    BL_In24_Cu = 24
    BL_In25_Cu = 25
    BL_In26_Cu = 26
    BL_In27_Cu = 27
    BL_In28_Cu = 28
    BL_In29_Cu = 29
    BL_In30_Cu = 30
    BL_B_Cu = 32


class Box2:
    """A bounding box — API-compatible with kipy.Box2 (pos/size/inflate)."""

    __slots__ = ("pos", "size")

    def __init__(self, pos: Vector2 | None = None, size: Vector2 | None = None):
        self.pos = pos if pos is not None else Vector2(0, 0)
        self.size = size if size is not None else Vector2(0, 0)

    def __repr__(self) -> str:
        return f"Box2(pos={self.pos}, size={self.size})"

    def center(self) -> Vector2:
        return Vector2(self.pos.x + self.size.x // 2, self.pos.y + self.size.y // 2)

    def inflate(self, amount: int) -> "Box2":
        new_width = self.size.x + amount
        new_height = self.size.y + amount
        self.pos = Vector2(self.pos.x - (new_width - self.size.x) // 2,
                           self.pos.y - (new_height - self.size.y) // 2)
        self.size = Vector2(new_width, new_height)
        return self
