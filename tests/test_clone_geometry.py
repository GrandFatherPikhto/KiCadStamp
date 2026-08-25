#!/usr/bin/env python3
"""Тесты на geometry/clone_geometry.py — геометрия применения ClonePlacement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.domain.geometry import Vector2
from kicadstamp.config import ClonePlacement, Cell, TemplateVia, TemplateTrack, TemplateComponentSlot
from kicadstamp.geometry.clone_geometry import apply_clone_geometry, clone_layout_origin, clone_shift_mm
from kicadstamp.geometry.spoke_layout import local_to_absolute, rotate_local_offset
from kicadstamp.exceptions import ValidationError

MM = 1_000_000


def _pi_filter_template() -> Cell:
    return Cell(
        name="pi_filter",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND")],
        components=[
            TemplateComponentSlot(role="CAP_IN", offset_along_mm=-1.0, offset_across_mm=0.0, angle_deg=0.0),
            TemplateComponentSlot(
                role="CAP_OUT", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=180.0,
                vias=[TemplateVia(offset_along_mm=1.0, offset_across_mm=1.0, net="GND")],
            ),
        ],
    )


class TestApplyCloneGeometry:
    def test_origin_is_direct_no_shift(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(50.0, 50.0))
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        assert layout.origin.x == int(50.0 * MM)
        assert layout.origin.y == int(50.0 * MM)

    def test_polar_mode_origin_matches_local_to_absolute(self):
        """Polar radius/angle define the shift via the same local_to_absolute
        primitive as CoordinatePlacement — "radius along X, rotated by angle"."""
        clone = ClonePlacement(cluster="f1", cell="pi_filter", xy=(0.0, 0.0),
                               radius_mm=5.0, angle_deg=37.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        expected = local_to_absolute(Vector2.from_xy(0, 0), 5.0, 0.0, 37.0)
        assert layout.origin.x == expected.x
        assert layout.origin.y == expected.y

    def test_polar_mode_no_anchor_is_absolute_position(self):
        """No anchor + polar = absolute point at radius/angle from board origin."""
        clone = ClonePlacement(cluster="f1", cell="pi_filter", xy=(0.0, 0.0),
                               radius_mm=4.0, angle_deg=90.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        expected = local_to_absolute(Vector2.from_xy(0, 0), 4.0, 0.0, 90.0)
        assert layout.origin.x == expected.x
        assert layout.origin.y == expected.y

    def test_polar_angle_does_not_become_component_rotation(self):
        """Unlike CoordinatePlacement, polar angle_deg must NOT leak into
        rotation_deg — it only positions the origin."""
        clone = ClonePlacement(cluster="f1", cell="pi_filter", xy=(0.0, 0.0),
                               radius_mm=3.0, angle_deg=120.0, rotation_deg=0.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert cap_out.angle_deg == 180.0  # slot angle + rotation_deg (0), NOT + angle_deg

    def test_polar_mode_with_nonzero_parent_rotation_nested_cell(self):
        """THE risky case (plan "КЛЮЧЕВОЕ различие"): a polar shift inside a
        rotated parent frame must rotate by angle_deg + parent_rotation_deg —
        exactly like xy does — or nested Cells silently misplace."""
        anchor = Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))
        clone = ClonePlacement(cluster="f1", cell="pi_filter", xy=(0.0, 0.0),
                               radius_mm=5.0, angle_deg=30.0)
        layout = apply_clone_geometry(
            clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"},
            anchor_position=anchor, parent_rotation_deg=90.0)
        expected_shift = rotate_local_offset(5.0, 0.0, 30.0 + 90.0)
        assert layout.origin.x == anchor.x + expected_shift.x
        assert layout.origin.y == anchor.y + expected_shift.y
        # cell contents rotate by parent + own rotation
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert cap_out.angle_deg == 180.0 + 90.0

    def test_roles_mapped_and_angle_includes_rotation(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(50.0, 50.0),
                              rotation_deg=90.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        cap_in = next(c for c in layout.components if c.role == "CAP_IN")
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert cap_in.ref == "C10"
        assert cap_out.ref == "C11"
        # Угол компонента = угол слота + rotation_deg (без mirror)
        assert cap_out.angle_deg == 180.0 + 90.0

    def test_spoke_and_component_level_vias_both_resolved(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(0.0, 0.0))
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        assert len(layout.vias) == 1
        assert layout.vias[0].net == "GND"
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert len(cap_out.vias) == 1
        assert cap_out.vias[0].net == "GND"

    def test_net_placeholder_resolved_via_params(self):
        tpl = Cell(name="dac", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="DAC{channel}_DB1")
        ])
        clone = ClonePlacement(cluster="dac2", cell="dac", xy=(0.0, 0.0),
                              params={"channel": 2})
        layout = apply_clone_geometry(clone, tpl, {})
        assert layout.vias[0].net == "DAC2_DB1"

    def test_net_overrides_applied(self):
        tpl = Cell(name="mcu", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="/STM32F4xx/BOOT0")
        ])
        clone = ClonePlacement(cluster="mcu2", cell="mcu", xy=(0.0, 0.0),
                              net_overrides={"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"})
        layout = apply_clone_geometry(clone, tpl, {})
        assert layout.vias[0].net == "/STM32F4xx_2/BOOT0"

    def test_via_without_net_raises_fatal(self):
        """Нет rule_net, на который можно упасть, в отличие от ManualSpoke — via без net фатальна."""
        tpl = Cell(name="bad", vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net=None)])
        clone = ClonePlacement(cluster="x", cell="bad", xy=(0.0, 0.0))
        with pytest.raises(ValidationError):
            apply_clone_geometry(clone, tpl, {})

    def test_role_without_resolved_ref_is_skipped(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(0.0, 0.0))
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10"})  # CAP_OUT не разрешена
        assert len(layout.components) == 1
        assert layout.components[0].role == "CAP_IN"

    # ---------- Новые тесты для mirror и anchor_position ----------
    def test_mirror_flips_geometry_and_angle(self):
        """Проверяем, что mirror=True зеркалирует X-координаты и меняет угол по формуле 180−φ."""
        tpl = Cell(
            name="simple",
            components=[
                TemplateComponentSlot(role="A", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=45.0)
            ]
        )
        clone = ClonePlacement(cluster="mirror_test", cell="simple", xy=(10.0, 20.0),
                               rotation_deg=30.0)
        role_to_ref = {"A": "C1"}

        # Без mirror
        layout_no = apply_clone_geometry(clone, tpl, role_to_ref, mirror=False)
        comp_no = layout_no.components[0]
        # С mirror
        layout_mirror = apply_clone_geometry(clone, tpl, role_to_ref, mirror=True)
        comp_mirror = layout_mirror.components[0]

        # X-координата зеркалируется относительно origin (10,20)
        origin_x = int(10.0 * MM)
        expected_x = origin_x - (comp_no.position.x - origin_x)  # отражение
        assert comp_mirror.position.x == expected_x
        assert comp_mirror.position.y == comp_no.position.y  # Y не меняется

        # Угол: 180 − (45 + 30) = 105°
        expected_angle = (180.0 - (45.0 + 30.0)) % 360.0
        assert abs(comp_mirror.angle_deg - expected_angle) < 1e-6

    def test_anchor_position_shifts_origin(self):
        """Если задан anchor_position, origin = anchor_position + (origin_x, origin_y) (плоский сдвиг)."""
        tpl = Cell(name="single", components=[TemplateComponentSlot(role="A")])
        clone = ClonePlacement(cluster="anchor_test", cell="single",
                               xy=(5.0, 7.0))
        anchor = Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))
        layout = apply_clone_geometry(clone, tpl, {"A": "C1"}, anchor_position=anchor)
        # origin должен быть (100+5, 200+7) мм
        assert layout.origin.x == int((100.0 + 5.0) * MM)
        assert layout.origin.y == int((200.0 + 7.0) * MM)

    def test_flat_shift_is_not_rotated_by_anchors_own_orientation(self):
        """
        xy: — плоский вектор, не поворачивается вместе с anchor'ом: apply_clone_geometry
        никогда не крутит clone.xy по ориентации самого anchor'а, только по
        parent_rotation_deg (используется исключительно для ВЛОЖЕННЫХ Cell,
        Phase 4). А ClonePositionCalculator.compute_raw_positions() — реальный
        путь для ЛЮБОГО top-level clone_placements: (в т.ч. anchor_role/
        anchor_cluster/anchor_pad-цепочек между независимыми клонами) — всегда
        зовёт его с parent_rotation_deg=0.0 (см. clone_position_calculator.py:360).
        Поэтому если anchor сам повёрнут/подвинут, зависимый клон это отражает
        только через СМЕЩЕНИЕ anchor_position, но его собственный xy: остаётся
        буквальным вектором в мировых координатах — при этом же самом xy:
        поворот anchor'а НЕ меняет относительное направление смещения так,
        как это интуитивно ожидалось бы для "чего-то, что едет вместе с ним".
        """
        tpl = Cell(name="single", components=[TemplateComponentSlot(role="A")])
        clone = ClonePlacement(cluster="anchor_test", cell="single", xy=(5.0, 0.0))

        anchor_0deg = Vector2.from_xy(0, 0)
        layout_0 = apply_clone_geometry(clone, tpl, {"A": "C1"}, anchor_position=anchor_0deg)

        # Тот же anchor, но физически повёрнутый на 180° вокруг себя (например,
        # anchor — это сам ClonePlacement с rotation_deg: 180.0) — anchor_position
        # (его мировая точка привязки) не меняется, только его ориентация.
        # "Честный" плоский сдвиг, повёрнутый вместе с anchor'ом, лёг бы в
        # противоположную сторону: (-5, 0). Код этого не делает.
        layout_180 = apply_clone_geometry(clone, tpl, {"A": "C1"}, anchor_position=anchor_0deg,
                                          parent_rotation_deg=0.0)
        assert layout_180.components[0].position.x == layout_0.components[0].position.x
        assert layout_180.components[0].position.x == int(5.0 * MM)

    def test_unredrawn_sibling_can_collide_after_shared_anchor_moves(self):
        """
        Два клона на одном anchor'е (напр. J1/CONN_PM5V), с xy: подобранными так,
        чтобы стоять на безопасном удалении (5mm) друг от друга. Anchor
        физически подвинули на 4mm. Один клон переRedraw'or'ен (anchor_position
        уже новый), второй — ещё нет (в реестре/на плате остался со старым
        anchor_position, как Ldo_Adj_n2v5 в реальном логе — 'not processed in
        this run (--only filtered ...), but it is still in the config — NOT
        pruned'). Итог: расстояние между ними схлопывается с 5mm до 1mm —
        воспроизводит ровно то, что видно на скриншотах ('всё в кучу'), без
        какого-либо бага в геометрии/registry — просто Redraw применяется
        строго к одному clone_placement, а не ко всей anchor-семье.
        """
        tpl = Cell(name="single", components=[TemplateComponentSlot(role="A")])
        clone_a = ClonePlacement(cluster="A", cell="single", xy=(0.0, 0.0))
        clone_b = ClonePlacement(cluster="B", cell="single", xy=(5.0, 0.0))

        anchor_old = Vector2.from_xy(0, 0)
        anchor_new = Vector2.from_xy(int(4.0 * MM), 0)

        # A только что переRedraw'ен -> видит новый anchor_position.
        layout_a = apply_clone_geometry(clone_a, tpl, {"A": "C_A"}, anchor_position=anchor_new)
        # B ещё не тронут в этом прогоне -> его последняя посчитанная позиция
        # всё ещё привязана к СТАРОМУ anchor_position.
        layout_b = apply_clone_geometry(clone_b, tpl, {"A": "C_B"}, anchor_position=anchor_old)

        pos_a = layout_a.components[0].position
        pos_b = layout_b.components[0].position
        dist_mm = abs(pos_a.x - pos_b.x) / MM

        assert dist_mm == pytest.approx(1.0)  # было 5mm, стало 1mm после сдвига anchor'а на 4mm
        assert dist_mm < 2.0  # типичный клиренс для мелкого SMD — уже коллизия


def test_clone_shift_mm_polar_equals_cartesian():
    """clone_shift_mm converts a polar offset to its Cartesian equivalent —
    the single identity used by the registry / duplicate-anchor check."""
    polar = ClonePlacement(cluster="f", cell="t", xy=(0.0, 0.0), radius_mm=5.0, angle_deg=0.0)
    cart = ClonePlacement(cluster="f", cell="t", xy=(5.0, 0.0))
    assert clone_shift_mm(polar) == (5.0, 0.0)
    assert clone_shift_mm(cart) == (5.0, 0.0)

    polar90 = ClonePlacement(cluster="f", cell="t", xy=(0.0, 0.0), radius_mm=5.0, angle_deg=90.0)
    expected = rotate_local_offset(5.0, 0.0, 90.0)
    ox, oy = clone_shift_mm(polar90)
    assert abs(ox - expected.x / MM) < 1e-6
    assert abs(oy - expected.y / MM) < 1e-6


class TestNetFromRoleInGeometry:
    """net_from_role / net_from_role_pad on via/track (plan step 3): the net
    comes from the pre-resolved role-net map, never from the live board here."""

    def test_via_takes_net_from_resolved_role_nets(self):
        tpl = Cell(name="r", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                        net_from_role="CAP_IN")])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        layout = apply_clone_geometry(
            clone, tpl, {"CAP_IN": "C10"},
            resolved_role_nets={("CAP_IN", None): "+3V3"})
        assert layout.vias[0].net == "+3V3"

    def test_via_with_pad(self):
        tpl = Cell(name="r", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                        net_from_role="LDO", net_from_role_pad="2")])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        layout = apply_clone_geometry(
            clone, tpl, {"LDO": "U1"},
            resolved_role_nets={("LDO", "2"): "+3V3"})
        assert layout.vias[0].net == "+3V3"

    def test_track_takes_net_from_resolved_role_nets(self):
        tpl = Cell(name="r", tracks=[
            TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                          end_along_mm=1.0, end_across_mm=1.0,
                          net_from_role="CAP_OUT")])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        layout = apply_clone_geometry(
            clone, tpl, {"CAP_OUT": "C11"},
            resolved_role_nets={("CAP_OUT", None): "+3V3"})
        assert layout.tracks[0].net == "+3V3"

    def test_component_slot_via_takes_net_from_role(self):
        tpl = Cell(name="r", components=[
            TemplateComponentSlot(role="CAP_OUT", vias=[
                TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                            net_from_role="CAP_OUT")])])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        layout = apply_clone_geometry(
            clone, tpl, {"CAP_OUT": "C11"},
            resolved_role_nets={("CAP_OUT", None): "+1V2"})
        assert layout.components[0].vias[0].net == "+1V2"

    def test_missing_resolved_key_is_fatal(self):
        """Internal consistency: calculator resolves every net_from_role BEFORE
        geometry, so a missing key means the hook wasn't wired — fatal, not a
        silent empty-net fallback."""
        tpl = Cell(name="r", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                        net_from_role="CAP_IN")])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        with pytest.raises(ValidationError, match="not resolved"):
            apply_clone_geometry(clone, tpl, {"CAP_IN": "C10"},
                                 resolved_role_nets={})

    def test_net_from_role_without_resolved_map_is_fatal(self):
        tpl = Cell(name="r", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0,
                        net_from_role="CAP_IN")])
        clone = ClonePlacement(cluster="x", cell="r", xy=(0.0, 0.0))
        with pytest.raises(ValidationError, match="not resolved"):
            apply_clone_geometry(clone, tpl, {"CAP_IN": "C10"})


class TestCloneLayoutOrigin:
    """clone_layout_origin must return EXACTLY the `layout.origin` that
    apply_clone_geometry computes (same anchor + shift composition) — it is
    what ExtractDock's Sub-placements xy derives from (2026-08-25), so a
    divergence here would silently misplace a referenced sub-placement."""

    def test_anchored_cartesian_matches_apply_origin(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(5.0, 2.0))
        anchor = Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))

        layout = apply_clone_geometry(clone, _pi_filter_template(), {},
                                      anchor_position=anchor)
        assert clone_layout_origin(clone, anchor) == layout.origin

    def test_absolute_cartesian_matches_apply_origin(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(5.0, 2.0))

        layout = apply_clone_geometry(clone, _pi_filter_template(), {})
        assert clone_layout_origin(clone, None) == layout.origin

    def test_polar_matches_apply_origin(self):
        clone = ClonePlacement(cluster="filter1", cell="pi_filter", xy=(0.0, 0.0),
                               radius_mm=5.0, angle_deg=37.0)
        anchor = Vector2.from_xy(int(10.0 * MM), int(20.0 * MM))

        layout = apply_clone_geometry(clone, _pi_filter_template(), {},
                                      anchor_position=anchor)
        assert clone_layout_origin(clone, anchor) == layout.origin
