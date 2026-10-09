# tests/gui/docks/test_instance_candidates.py
"""Сторожа функции «подходят ли экземпляр и ячейка» (plan_2026_10_09_cells_and_entities,
часть 1) — gui/docks/instance_candidates.py.

Слой: чистые функции зовутся НАПРЯМУЮ, без дока. Модуль обязан быть без Qt —
если бы он тянул PyQt6 на импорте, эти сторожа упали бы.

ГЛАВНАЯ клетка здесь — правило кратности ролей (Денис, 09.10.2026): число
деталей экземпляра с ролью R обязано РАВНЯТЬСЯ числу слотов R в ячейке;
меньше — не подходит (`role R: 1 of 2`), больше — не подходит
(`role R ×3 for 2 slots`); лишняя роль экземпляра, которой в ячейке НЕТ, —
НЕ мешает. Три клетки на это (меньше / больше / лишняя роль) —
test_role_multiplicity_is_exact_per_cell_role, параметризацией, чтобы падение
одной строки не прятало остальные (правило 35).

Имена функций описывают СВОЙСТВО (правило 37), имя плана и клетки — в докстринге.
"""
from collections import Counter

import pytest

from gui.docks.instance_candidates import (
    CellSpec,
    Part,
    cell_candidates,
    instance_candidates,
    instance_parts,
    role_mismatch_reason,
    snapshot_parts,
)


def _p(ref, role, cluster, sheet=("Ch0",)):
    return Part(uuid="u-" + ref, ref=ref, role=role, cluster=cluster, sheet=sheet)


# ── Клетка: кратность ролей (ядро правила) ──────────────────────────────────

@pytest.mark.parametrize("instance_roles, cell_roles, fits, reason", [
    # Cell "RES,CAP" — one slot of each: an exact match fits.
    (["RES", "CAP"], ["RES", "CAP"], True, ""),
    # Shortfall: one RES where the cell wants two.
    (["RES"], ["RES", "RES"], False, "role RES: 1 of 2"),
    # Missing role (0 of N) is the extreme of the shortfall.
    (["RES"], ["RES", "CAP"], False, "role CAP: 0 of 1"),
    # Excess: three RES where the cell wants two.
    (["RES", "RES", "RES"], ["RES", "RES"], False, "role RES ×3 for 2 slots"),
    # A role the CELL does not name is HARMLESS — "лишняя роль".
    (["RES", "CAP", "EXTRA"], ["RES", "CAP"], True, ""),
    # ...and a part with NO role is "extra" too.
    (["RES", "CAP", ""], ["RES", "CAP"], True, ""),
])
def test_role_multiplicity_is_exact_per_cell_role(
        instance_roles, cell_roles, fits, reason):
    """Клетки «меньше» / «больше» / «лишняя роль» правила кратности.

    Роль экземпляра, которой в ячейке нет, не мешает (`fits`); роль ячейки
    обязана стоять ровно столько раз, сколько слотов. Параметризация даёт
    отдельный тест на строку: падение «лишней роли» не прячет «меньше».
    """
    got = role_mismatch_reason(Counter(r for r in instance_roles if r),
                               Counter(cell_roles))
    assert (got == "") is fits, (got, reason)
    assert got == reason


# ── Клетка: три кластера на двух листах (полный список с состояниями) ────────

PARTS = [
    _p("C1", "CAP", "FPGA_VCCIO_1"), _p("R1", "RES", "FPGA_VCCIO_1"),
    # lacks CAP
    _p("R2", "RES", "FPGA_VCCIO_2"),
    # excess CAP
    _p("C2", "CAP", "FPGA_VCCIO_3"), _p("C3", "CAP", "FPGA_VCCIO_3"),
    _p("R3", "RES", "FPGA_VCCIO_3"),
    # fits, on ANOTHER sheet, and already an entity of the cell (taken)
    _p("R4", "RES", "FPGA_VCCIO_4", ("Ch1",)),
    _p("C4", "CAP", "FPGA_VCCIO_4", ("Ch1",)),
]


def test_three_clusters_on_two_sheets_give_exactly_the_expected_states():
    """Клетка плана: три-четыре кластера на двух листах — один занят, один без
    роли, один с лишней ролью, один подходит → ровно ожидаемый список.

    Одной строкой на экземпляр: (кластер, лист, fits, reason, taken)."""
    out = instance_candidates(
        PARTS, ("RES", "CAP"),
        taken=lambda cluster, sheet: ("fpga_vccio_4"
                                      if cluster == "FPGA_VCCIO_4" else None))
    rows = {(c.cluster, c.sheet): (c.fits, c.reason, c.taken) for c in out}
    assert rows == {
        ("FPGA_VCCIO_1", "Ch0"): (True, "", False),
        ("FPGA_VCCIO_2", "Ch0"): (False, "role CAP: 0 of 1", False),
        ("FPGA_VCCIO_3", "Ch0"): (False, "role CAP ×2 for 1 slots", False),
        ("FPGA_VCCIO_4", "Ch1"): (True, "", True),
    }
    # Fitting first — "подходящие сверху" is the order the dialog relies on.
    assert [c.fits for c in out] == [True, True, False, False]
    taken = next(c for c in out if c.cluster == "FPGA_VCCIO_4")
    assert taken.entity_name == "fpga_vccio_4"


def test_non_fitting_instances_are_returned_not_dropped():
    """«Неполные считаются, а не теряются»: не подходящие обязаны быть В СПИСКЕ
    (со своим reason), иначе строку-счётчик «K instances lack roles» не из чего
    собрать."""
    out = instance_candidates([_p("R2", "RES", "CL_X")], ("RES", "CAP"))
    assert len(out) == 1
    assert out[0].fits is False
    assert out[0].reason  # a reason the counter can NAME


def test_taken_instance_is_flagged_but_still_offered():
    """Занятый остаётся в списке с именем сущности — таблица покажет его серым,
    но не выкинет (иначе пользователь не увидит, что пара уже занята)."""
    out = instance_candidates(
        [_p("R1", "RES", "CL_A")], ("RES",),
        taken=lambda cluster, sheet: "already_there")
    assert out[0].taken is True
    assert out[0].entity_name == "already_there"


def test_parts_without_cluster_form_no_instance():
    """Деталь без Cluster-тега не образует экземпляр (то же соглашение, что у
    reead.group_selected) — иначе в таблицу попал бы «безымянный» инстанс."""
    out = instance_candidates([_p("R1", "RES", None)], ("RES",))
    assert out == []


# ── Клетка: кластер — сегмент, не подстрока (A/B против A/B2) ────────────────

def test_cluster_segment_not_substring_separates_instances():
    """A/B и A/B2 — ДВА разных экземпляра (сегментное сравнение, не подстрока:
    Channel_1 не должен совпасть с Channel_10). Заодно instance_parts на A/B не
    затягивает детали A/B2."""
    parts = [_p("R1", "RES", "A/B"), _p("R2", "RES", "A/B2")]
    out = instance_candidates(parts, ("RES",))
    assert sorted(c.cluster for c in out) == ["A/B", "A/B2"]
    assert all(c.fits for c in out)
    assert [p.ref for p in instance_parts(parts, "A/B")] == ["R1"]


def test_cluster_prefix_matches_a_refined_board_tag():
    """Тег платы может УТОЧНИТЬ кластер конфига (A/B при wanted A) — сегментный
    префикс обязан его найти, иначе экземпляр не находится вовсе."""
    parts = [_p("R1", "RES", "A/B")]
    assert [p.ref for p in instance_parts(parts, "A")] == ["R1"]


# ── Клетка: лист (участвует в ключе экземпляра и в отборе) ──────────────────

def test_sheet_none_still_forms_an_instance():
    """Деталь без разрешённого листа — экземпляр с листом None (ключ (кластер,
    None)); подпись честная, а не пустая."""
    out = instance_candidates([_p("R1", "RES", "CL", ())], ("RES",))
    assert out[0].sheet is None
    assert "(no sheet)" in out[0].label


def test_instance_parts_narrows_by_sheet_only_when_it_reduces():
    """Лист сужает детали ТОЛЬКО когда что-то находит (то же, что
    narrow_candidates_by_sheet); лист, не совпавший ни с чем, НЕ обнуляет
    экземпляр."""
    parts = [_p("R1", "RES", "CL", ("Ch0",)), _p("R2", "RES", "CL", ("Ch1",))]
    assert [p.ref for p in instance_parts(parts, "CL", "Ch1")] == ["R2"]
    assert sorted(p.ref for p in instance_parts(parts, "CL", "NoSuch")) == \
        ["R1", "R2"]


# ── Обратное направление на ТЕХ ЖЕ данных ──────────────────────────────────

def test_cell_candidates_is_the_same_rule_reversed():
    """cell_candidates — та же функция role_mismatch_reason, обратная сторона:
    на деталях одного экземпляра выдаёт ячейки со своим вердиктом. Лишняя роль
    экземпляра (CAP при ячейке только RES) не мешает и здесь."""
    inst = instance_parts(PARTS, "FPGA_VCCIO_1")
    out = cell_candidates(inst, [
        CellSpec("res_cap", ("RES", "CAP")),
        CellSpec("res_only", ("RES",)),
        CellSpec("two_res", ("RES", "RES")),
    ])
    rows = {c.name: (c.fits, c.reason) for c in out}
    assert rows == {
        "res_cap": (True, ""),
        "res_only": (True, ""),          # CAP here is the harmless extra
        "two_res": (False, "role RES: 1 of 2"),
    }
    assert [c.fits for c in out] == [True, True, False]


# ── Дверь: снимок → простые записи (роль В СИЛЕ, uuid из .fp) ────────────────

def test_snapshot_parts_carries_role_in_force_and_uuid():
    """Конвертер не перечитывает плату: он берёт роль В СИЛЕ (с нашими
    переопределениями, что уже лежит в Selected.role) и uuid из Selected.fp —
    иначе подбор шёл бы по сырой плате, а не по тому, что видит пользователь.

    Пустая карта листов делает snapshot_with_resolved_sheets no-op, поэтому
    подставные объекты (без PyQt6) проходят конвертер как есть."""
    class _Fp:
        uuid = "u-1"

    class _Selected:
        ref = "R1"
        role = "OVERRIDDEN_ROLE"
        cluster = "CL_A"
        sheet = ("Ch0",)
        fp = _Fp()

    parts = snapshot_parts([_Selected()], {})
    assert len(parts) == 1
    assert parts[0] == Part(uuid="u-1", ref="R1", role="OVERRIDDEN_ROLE",
                            cluster="CL_A", sheet=("Ch0",))


def test_snapshot_parts_resolves_a_hierarchical_sheet_to_names():
    """Клетка C2 приёмки части 1: деталь с ИЕРАРХИЧЕСКИМ листом (цепочка uuid)
    обязана приехать в Part.sheet ИМЕНАМИ из sheet_names — ровно это делает
    импорт snapshot_with_resolved_sheets внутри конвертера. Без него у живого
    снимка (BoardConnection соединяется без schematic_dir) Selected.sheet — всё
    None, и подбор экземпляров шёл бы без листа.

    Снимок — НАСТОЯЩИЙ explore.Selected (snapshot_with_resolved_sheets зовёт
    dataclasses.replace), fp — подставной носитель цепочки uuid."""
    from kicadstamp.explore import Selected

    class _Fp:
        uuid = "u-sym"
        sheet_path_uuids = ("u-root", "u-sheet", "u-sym")

    sel = Selected(ref="R1", role="RES", cluster="CL_A", sheet=[],
                   nets={}, fp=_Fp())
    parts = snapshot_parts([sel], {"u-root": "Top", "u-sheet": "Channel_0"})
    assert parts[0].sheet == ("Top", "Channel_0"), (
        "цепочка листа обязана разрешиться в ИМЕНА (мутация «snapshot_parts без "
        "snapshot_with_resolved_sheets»), сейчас: " + repr(parts[0].sheet))
    assert parts[0].uuid == "u-sym"
