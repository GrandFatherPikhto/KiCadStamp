#!/usr/bin/env python3
"""
Тесты на geometry/spoke_layout.py — локальные примитивы развёртки
(along/across -> абсолютные координаты платы) через (сдвиг, поворот).

Спицевая apply_spoke_geometry снята вместе с механизмом спиц
(plan_2026_10_08_remove_spokes). Свойства, которые она стерегла (развёртка
ячейки из нуля, via обоих уровней от нуля, полярный origin, resolved_mount,
net_from_role, зеркало), не потеряны — их несут клоны:
tests/geometry/test_clone_geometry.py и tests/geometry/test_cell_anchor_geometry.py.
Здесь остаются примитивы — общий язык развёртки для клонов и деревьев.
"""
import math

from kicadstamp.domain.geometry import Vector2

from kicadstamp.geometry.spoke_layout import rotate_local_offset, local_to_absolute

MM = 1_000_000


def _real_rotate(x_mm, y_mm, angle_deg):
    """Ручной расчёт по РЕАЛЬНОЙ формуле kipy Vector2.rotate() (не переизобретаем — сверяем)."""
    theta = math.radians(angle_deg)
    rx = y_mm * math.sin(theta) + x_mm * math.cos(theta)
    ry = y_mm * math.cos(theta) - x_mm * math.sin(theta)
    return rx, ry


class TestRotateLocalOffset:
    def test_zero_rotation_is_identity(self):
        v = rotate_local_offset(1.0, -2.0, 0.0)
        assert abs(v.x / MM - 1.0) < 1e-6
        assert abs(v.y / MM - (-2.0)) < 1e-6

    def test_matches_real_kipy_formula(self):
        for angle in (30.0, 90.0, 137.0, 270.0):
            v = rotate_local_offset(1.5, -0.7, angle)
            ex, ey = _real_rotate(1.5, -0.7, angle)
            assert abs(v.x / MM - ex) < 1e-3, f"angle={angle}: x не сходится"
            assert abs(v.y / MM - ey) < 1e-3, f"angle={angle}: y не сходится"


class TestLocalToAbsolute:
    """local_to_absolute = origin + rotate(along, across, rotation_deg) — the ONE
    local->world primitive the clone/tree expansion paths share. Carried over
    from the removed apply_spoke_geometry polar cells (a polar origin was just
    this primitive with along=radius, across=0)."""

    def test_zero_rotation_is_plain_translation(self):
        origin = Vector2.from_xy(50 * MM, 50 * MM)
        p = local_to_absolute(origin, 1.0, -1.5, 0.0)
        assert abs((p.x - origin.x) / MM - 1.0) < 1e-6
        assert abs((p.y - origin.y) / MM - (-1.5)) < 1e-6

    def test_rotation_matches_real_kipy_formula(self):
        origin = Vector2.from_xy(10 * MM, 20 * MM)
        for angle in (30.0, 90.0, 137.0, 270.0):
            p = local_to_absolute(origin, 1.5, -0.7, angle)
            ex, ey = _real_rotate(1.5, -0.7, angle)
            assert abs(p.x / MM - (10.0 + ex)) < 1e-3, f"angle={angle}: x не сходится"
            assert abs(p.y / MM - (20.0 + ey)) < 1e-3, f"angle={angle}: y не сходится"

    def test_polar_radius_along_x_rotated(self):
        """The polar origin convention: 'radius along +X, rotated by angle_deg'."""
        origin = Vector2.from_xy(0, 0)
        p = local_to_absolute(origin, 5.0, 0.0, 90.0)
        ex, ey = _real_rotate(5.0, 0.0, 90.0)
        assert abs(p.x / MM - ex) < 1e-3
        assert abs(p.y / MM - ey) < 1e-3
