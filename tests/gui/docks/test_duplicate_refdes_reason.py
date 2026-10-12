# tests/gui/docks/test_duplicate_refdes_reason.py
"""Дубль refdes и сирота в окне «Добавить сущности»
(plan_2026_10_12_duplicate_refdes_guard, 12.10.2026).

Денис 12.10: у экземпляра, в котором есть дубль refdes или сирота, вместо
голого «R_LED: 0 of 1» окно обязано назвать дубль — «C35 on the board twice
(one is not linked to the schematic)». Правило обнаружения — ОДНО на продукт
(`kicadstamp.refdes_duplicates`), тот же детектор, что у двери конвейера; здесь
стережётся его применение в подборе экземпляров и в подсказке счётчика.

Клетки плана: С5 — экземпляр с дублем; С6 — экземпляр с ОДИНОКОЙ сиротой (её
показывает только это окно: конвейер такую запись НЕ останавливает).

Имена функций описывают СВОЙСТВО (правило 37), имя плана — в докстринге.
"""
from gui.docks.add_entities import AddEntitiesDialog
from gui.docks.instance_candidates import (Part, duplicate_or_orphan_reason,
                                           instance_candidates)


def _part(ref, role, cluster, sheet=("Ch0",), on_schematic=True):
    return Part(uuid="u-" + ref + cluster, ref=ref, role=role, cluster=cluster,
                sheet=sheet, on_schematic=on_schematic)


def test_instance_with_a_duplicated_refdes_names_the_duplicate():
    """С5: C35 стоит на плате дважды (вторая копия — в чужом кластере, сирота) →
    причина экземпляра — строка дубля, а не «role RES: 0 of 1»."""
    parts = [
        _part("C35", "CAP", "CL_A"),                        # this instance
        _part("C35", "CAP", "CL_B", on_schematic=False),    # the orphan twin
        _part("R1", "RES", "CL_A"),
    ]

    (cand,) = [c for c in instance_candidates(parts, ("RES", "CAP"))
               if c.cluster == "CL_A"]

    assert cand.reason == ("C35 is on the board twice (one is not linked to "
                           "the schematic)")
    assert cand.fits is False


def test_instance_with_a_lone_orphan_names_the_orphan():
    """С6: сирота БЕЗ дубля — неоднозначности нет, двигать нечем ошибиться,
    поэтому конвейер её не останавливает, но окно обязано назвать сироту."""
    parts = [_part("C18", "CAP", "CL_A", on_schematic=False),
             _part("R1", "RES", "CL_A")]

    (cand,) = instance_candidates(parts, ("RES", "CAP"))

    assert cand.reason == "C18 is not linked to the schematic"
    assert cand.fits is False


def test_duplicate_in_another_instance_does_not_refuse_this_one():
    """Чужой дубль не мешает: правило читает ТОЛЬКО refdes самого экземпляра —
    та же клетка «не мешает», что и у двери конвейера."""
    parts = [
        _part("C18", "CAP", "CL_B"), _part("C18", "CAP", "CL_B"),  # other instance
        _part("R1", "RES", "CL_A"), _part("C1", "CAP", "CL_A"),
    ]

    (cand,) = [c for c in instance_candidates(parts, ("RES", "CAP"))
               if c.cluster == "CL_A"]

    assert cand.reason == "" and cand.fits is True


def test_healthier_instance_still_reports_its_plain_role_shortfall():
    """Сторож дублей не переписывает ПРАВИЛО кратности: экземпляр без дублей и
    сирот по-прежнему говорит «role CAP: 0 of 1»."""
    (cand,) = instance_candidates([_part("R1", "RES", "CL_A")], ("RES", "CAP"))
    assert cand.reason == "role CAP: 0 of 1"


def test_reason_helper_is_silent_for_a_healthy_instance():
    assert duplicate_or_orphan_reason([_part("R1", "RES", "CL_A")], {}) == ""


def test_counter_tooltip_names_the_duplicate_reason(qapp):
    """Счётчик «K instances lack roles» несёт причины в подсказке — так окно
    НАЗЫВАЕТ дубль, а не только считает неподходящие."""
    from gui.docks.instance_candidates import InstanceCandidate

    bad = InstanceCandidate(
        cluster="CL_A", sheet="Ch0", refs=("C35",), fits=False,
        reason="C35 is on the board twice (one is not linked to the schematic)")
    dlg = AddEntitiesDialog(None, "c", [bad], set())

    assert "1 instances lack roles" in dlg._counter.text()
    assert "C35 is on the board twice" in dlg._counter.toolTip()
