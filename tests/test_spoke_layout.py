#!/usr/bin/env python3
"""
Тесты на geometry/spoke_layout.py — развёртка шаблона спицы (локальные
along/across) в абсолютные координаты платы через (сдвиг, поворот).

KiCadStamp, обобщённые via: TemplateVia используется и на уровне спицы
(была power_via), и на уровне компонента (была GND via) — ОБА случая
чистая геометрия от нуля спицы, никакой зависимости от реального пада
компонента.
"""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kipy.geometry import Vector2

from kicadstamp.config import (
    ManualSpoke, Cell, TemplateVia, TemplateComponentSlot, TemplateTrack
)
from kicadstamp.geometry.spoke_layout import (
    apply_spoke_geometry, rotate_local_offset, local_to_absolute,
)

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


class TestApplySpokeGeometry:
    def _template(self):
        return Cell(
            name="t",
            vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.5)],  # была power_via
            components=[
                TemplateComponentSlot(
                    role="HEAVY", offset_along_mm=1.0, offset_across_mm=-1.0, angle_deg=90.0,
                    vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND")],
                ),
                TemplateComponentSlot(
                    role="LIGHT", offset_along_mm=1.0, offset_across_mm=2.0, angle_deg=270.0,
                    vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=1.3, net="GND")],
                ),
            ],
        )

    def test_zero_rotation_local_equals_absolute_offset(self):
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", rotation_deg=0.0)
        role_to_ref = {"HEAVY": "C5", "LIGHT": "C30"}
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND", role_to_ref=role_to_ref)

        heavy = next(c for c in layout.components if c.role == "HEAVY")
        assert heavy.ref == "C5"
        assert abs((heavy.position.x - pad_pos.x) / MM - 1.0) < 1e-6
        assert abs((heavy.position.y - pad_pos.y) / MM - (-1.0)) < 1e-6
        assert heavy.angle_deg == 90.0

    def test_spoke_level_via_present(self):
        """Via уровня спицы (была power_via) -- одна на весь список layout.vias."""
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", rotation_deg=0.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="+3V3",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        assert len(layout.vias) == 1
        via = layout.vias[0]
        assert abs((via.position.x - pad_pos.x) / MM - 0.0) < 1e-6
        assert abs((via.position.y - pad_pos.y) / MM - (-1.5)) < 1e-6
        assert via.net == "+3V3"  # net=None в шаблоне -> взят rule_net

    def test_component_level_via_computed_from_spoke_origin_not_component_position(self):
        """
        КЛЮЧЕВОЕ: via компонента (была GND via) считается от НУЛЯ СПИЦЫ,
        а НЕ от позиции самого компонента -- геометрически другая точка
        отсчёта, хоть числа и заданы в шаблоне "около" компонента.
        """
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", rotation_deg=0.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        heavy = next(c for c in layout.components if c.role == "HEAVY")
        assert len(heavy.vias) == 1
        via = heavy.vias[0]
        # via.offset (0.0, -1.0) от НУЛЯ СПИЦЫ (pad_pos), а НЕ от heavy.position (1.0,-1.0)
        expected_x = pad_pos.x + int(0.0 * MM)
        expected_y = pad_pos.y + int(-1.0 * MM)
        assert via.position.x == expected_x
        assert via.position.y == expected_y
        assert via.net == "GND"
        # Явно НЕ должна совпадать с позицией компонента + тот же оффсет
        # относительно component.position -- разные точки отсчёта.
        assert (via.position.x, via.position.y) != (heavy.position.x, heavy.position.y)

    def test_same_template_different_rotation_gives_consistent_math(self):
        """Один шаблон, два разных поворота — оба должны совпасть с
        независимым ручным расчётом, включая via обоих уровней."""
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        tpl = self._template()
        role_to_ref = {"HEAVY": "C5", "LIGHT": "C30"}

        for rotation_deg, shift_x, shift_y in [(90.0, 0.0, 0.0), (270.0, 0.4, 0.0)]:
            spoke = ManualSpoke(pad="1", cell="t", rotation_deg=rotation_deg,
                               shift_x_mm=shift_x, shift_y_mm=shift_y)
            layout = apply_spoke_geometry(pad_pos, spoke, tpl, rule_net="GND", role_to_ref=role_to_ref)

            origin_x_mm = 50.0 + shift_x
            origin_y_mm = 50.0 + shift_y

            ex, ey = _real_rotate(0.0, -1.5, rotation_deg)  # via уровня спицы
            assert abs(layout.vias[0].position.x / MM - (origin_x_mm + ex)) < 1e-3
            assert abs(layout.vias[0].position.y / MM - (origin_y_mm + ey)) < 1e-3

            heavy = next(c for c in layout.components if c.role == "HEAVY")
            light = next(c for c in layout.components if c.role == "LIGHT")
            assert heavy.angle_deg == 90.0 + rotation_deg
            assert light.angle_deg == 270.0 + rotation_deg

    def test_missing_vias_gives_empty_list(self):
        pad_pos = Vector2.from_xy(0, 0)
        tpl = Cell(name="minimal", components=[
            TemplateComponentSlot(role="SOLO", offset_along_mm=1.0)
        ])
        spoke = ManualSpoke(pad="1", cell="minimal")
        layout = apply_spoke_geometry(pad_pos, spoke, tpl, rule_net="GND", role_to_ref={"SOLO": "C1"})

        assert layout.vias == []
        assert len(layout.components) == 1
        assert layout.components[0].vias == []

    def test_role_without_resolved_ref_is_skipped(self):
        pad_pos = Vector2.from_xy(0, 0)
        tpl = self._template()
        layout = apply_spoke_geometry(pad_pos, spoke=ManualSpoke(pad="1", cell="t"),
                                      cell=tpl, rule_net="GND", role_to_ref={"HEAVY": "C5"})
        assert len(layout.components) == 1
        assert layout.components[0].role == "HEAVY"

    def test_multiple_vias_per_component_slot(self):
        """Генерализация: несколько via на одном компоненте -- не ограничено одной GND via."""
        pad_pos = Vector2.from_xy(0, 0)
        tpl = Cell(name="t2", components=[
            TemplateComponentSlot(role="SOLO", offset_along_mm=1.0, vias=[
                TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND"),
                TemplateVia(offset_along_mm=0.0, offset_across_mm=1.0, net="GND"),
                TemplateVia(offset_along_mm=0.5, offset_across_mm=0.0, net="+3V3"),
            ]),
        ])
        layout = apply_spoke_geometry(pad_pos, ManualSpoke(pad="1", cell="t2"),
                                      tpl, rule_net="GND", role_to_ref={"SOLO": "C1"})
        assert len(layout.components[0].vias) == 3
        nets = [v.net for v in layout.components[0].vias]
        assert nets == ["GND", "GND", "+3V3"]

    def test_arbitrary_number_of_roles_not_limited_to_two(self):
        """Шаблон на 3 роли (имитация кристалла: XTAL + 2 конденсатора нагрузки)."""
        pad_pos = Vector2.from_xy(0, 0)
        tpl = Cell(name="crystal", components=[
            TemplateComponentSlot(role="XTAL", offset_along_mm=0.0, offset_across_mm=0.0),
            TemplateComponentSlot(role="LOAD_CAP_1", offset_along_mm=-1.0, offset_across_mm=1.0),
            TemplateComponentSlot(role="LOAD_CAP_2", offset_along_mm=1.0, offset_across_mm=1.0),
        ])
        role_to_ref = {"XTAL": "Y1", "LOAD_CAP_1": "C15", "LOAD_CAP_2": "C16"}
        layout = apply_spoke_geometry(pad_pos, ManualSpoke(pad="1", cell="crystal"),
                                      tpl, rule_net="GND", role_to_ref=role_to_ref)
        assert len(layout.components) == 3
        refs = {c.ref for c in layout.components}
        assert refs == {"Y1", "C15", "C16"}

    def test_polar_mode_origin_matches_local_to_absolute(self):
        """Polar radius/angle must define the spoke origin via the SAME
        local_to_absolute primitive as CoordinatePlacement — "radius along X,
        rotated by angle_deg" around the pad centre."""
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", radius_mm=5.0, angle_deg=37.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        expected = local_to_absolute(pad_pos, 5.0, 0.0, 37.0)
        assert layout.origin.x == expected.x
        assert layout.origin.y == expected.y

    def test_polar_90_degrees_places_spoke_at_radius(self):
        """Concrete geometric case: radius 5 at angle 90 must land the spoke
        origin at pad + rotate((5,0), 90), and cell contents follow from it."""
        pad_pos = Vector2.from_xy(0, 0)
        spoke = ManualSpoke(pad="1", cell="t", radius_mm=5.0, angle_deg=90.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        ox, oy = _real_rotate(5.0, 0.0, 90.0)
        assert abs(layout.origin.x / MM - ox) < 1e-3
        assert abs(layout.origin.y / MM - oy) < 1e-3
        heavy = next(c for c in layout.components if c.role == "HEAVY")
        hx, hy = _real_rotate(1.0, -1.0, 0.0)
        assert abs(heavy.position.x / MM - (ox + hx)) < 1e-3
        assert abs(heavy.position.y / MM - (oy + hy)) < 1e-3

    def test_polar_angle_does_not_become_component_rotation(self):
        """Unlike CoordinatePlacement, a spoke's polar angle_deg must NOT leak
        into rotation_deg — it only positions the origin; the cell keeps its
        own rotation_deg (default 0 here, so slot angles pass through)."""
        pad_pos = Vector2.from_xy(0, 0)
        spoke = ManualSpoke(pad="1", cell="t", radius_mm=3.0, angle_deg=120.0, rotation_deg=0.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        heavy = next(c for c in layout.components if c.role == "HEAVY")
        light = next(c for c in layout.components if c.role == "LIGHT")
        assert heavy.angle_deg == 90.0
        assert light.angle_deg == 270.0

    def test_polar_mode_combines_with_rotation_deg(self):
        """radius/angle positions the origin; rotation_deg still rotates the
        cell contents as before — the two compose, they don't interfere."""
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", radius_mm=4.0, angle_deg=30.0, rotation_deg=90.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template(), rule_net="GND",
                                      role_to_ref={"HEAVY": "C5", "LIGHT": "C30"})
        ox, oy = _real_rotate(4.0, 0.0, 30.0)
        assert abs(layout.origin.x / MM - (50.0 + ox)) < 1e-3
        assert abs(layout.origin.y / MM - (50.0 + oy)) < 1e-3
        heavy = next(c for c in layout.components if c.role == "HEAVY")
        assert heavy.angle_deg == 90.0 + 90.0


class TestSpokeLevelTracks:
    """ManualSpoke теперь расставляет и tracks: (не только via/components) --
    net=None в TemplateTrack наследует rule_net, той же конвенцией, что и
    у TemplateVia (см. TestApplySpokeGeometry.test_spoke_level_via_present).
    Это критично: один и тот же шаблон (cap_pair_standard) переиспользуется
    несколькими Rule с РАЗНЫМ net -- литерал сломал бы 3 из 4 правил."""

    def _template_with_tracks(self):
        return Cell(
            name="t",
            layer="B.Cu",
            tracks=[
                TemplateTrack(start_along_mm=-1.0, start_across_mm=1.5,
                              end_along_mm=-1.0, end_across_mm=2.7,
                              width_mm=0.65, net="GND"),
                TemplateTrack(start_along_mm=-1.0, start_across_mm=-1.1,
                              end_along_mm=-2.0, end_across_mm=-2.1,
                              width_mm=0.65, net=None),
            ],
        )

    def test_track_net_none_inherits_rule_net(self):
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", rotation_deg=0.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template_with_tracks(),
                                      rule_net="+1V2_VCCINT", role_to_ref={})
        assert len(layout.tracks) == 2
        gnd_track = next(t for t in layout.tracks if t.width_mm == 0.65 and t.net == "GND")
        power_track = next(t for t in layout.tracks if t.net != "GND")
        assert gnd_track.net == "GND"  # литерал не тронут
        assert power_track.net == "+1V2_VCCINT"  # None -> rule_net этого конкретного правила

    def test_same_template_different_rule_net_gives_different_track_net(self):
        """Тот же шаблон, два разных Rule -- силовой трек следует за rule_net каждого."""
        pad_pos = Vector2.from_xy(0, 0)
        tpl = self._template_with_tracks()
        for net in ("+3V3_VCCIO", "+2V5_VCCA"):
            layout = apply_spoke_geometry(pad_pos, ManualSpoke(pad="1", cell="t"),
                                          tpl, rule_net=net, role_to_ref={})
            power_track = next(t for t in layout.tracks if t.net != "GND")
            assert power_track.net == net

    def test_track_geometry_matches_local_to_absolute(self):
        pad_pos = Vector2.from_xy(50 * MM, 50 * MM)
        spoke = ManualSpoke(pad="1", cell="t", rotation_deg=90.0)
        layout = apply_spoke_geometry(pad_pos, spoke, self._template_with_tracks(),
                                      rule_net="GND", role_to_ref={})
        gnd_track = next(t for t in layout.tracks if t.net == "GND")
        ex_start, ey_start = _real_rotate(-1.0, 1.5, 90.0)
        ex_end, ey_end = _real_rotate(-1.0, 2.7, 90.0)
        assert abs((gnd_track.start.x - pad_pos.x) / MM - ex_start) < 1e-3
        assert abs((gnd_track.start.y - pad_pos.y) / MM - ey_start) < 1e-3
        assert abs((gnd_track.end.x - pad_pos.x) / MM - ex_end) < 1e-3
        assert abs((gnd_track.end.y - pad_pos.y) / MM - ey_end) < 1e-3

    def test_track_layer_inherits_template_layer_when_unset(self):
        pad_pos = Vector2.from_xy(0, 0)
        layout = apply_spoke_geometry(pad_pos, ManualSpoke(pad="1", cell="t"),
                                      self._template_with_tracks(), rule_net="GND", role_to_ref={})
        assert all(t.layer == "B.Cu" for t in layout.tracks)

    def test_missing_tracks_gives_empty_list(self):
        """Шаблон без tracks: вообще -- не должен падать, просто пустой список."""
        pad_pos = Vector2.from_xy(0, 0)
        tpl = Cell(name="no_tracks", vias=[TemplateVia(offset_along_mm=0.0)])
        layout = apply_spoke_geometry(pad_pos, ManualSpoke(pad="1", cell="no_tracks"),
                                      tpl, rule_net="GND", role_to_ref={})
        assert layout.tracks == []
