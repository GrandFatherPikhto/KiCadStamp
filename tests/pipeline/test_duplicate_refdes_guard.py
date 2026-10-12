# tests/pipeline/test_duplicate_refdes_guard.py
"""Дубль refdes на плате — сторож ОДНОЙ двери конвейера
(plan_2026_10_12_duplicate_refdes_guard, 12.10.2026).

Слой: чистые функции зовутся НАПРЯМУЮ, с ПОДДЕЛЬНЫМ адаптером — плата KiCad не
нужна вовсе, сторожу довольно списка футпринтов. Ни один тест здесь не пишет на
живую плату.

Клетки плана:
  * С1 — запись, чей разрешённый refdes стоит на плате дважды, отказывает
    ЦЕЛИКОМ (ни ходов, ни меди): `allowed` пуст;
  * С4 — запись, где дублирован ОДИН refdes из нескольких ролей, тоже
    отказывает целиком (запись атомарна: медь планируется от позиций ВСЕХ ролей);
  * С2 — запись без дублей в том же прогоне не меняется (прежний план);
  * С3 — дубль refdes, которого нет среди `produces` записи, НЕ мешает;
  * решение Дениса 12.10 — СИРОТА БЕЗ ДУБЛЯ конвейер не останавливает.

Мутации, которые эти клетки убивают (см. diagnostics/…_mutations_accept_*):
  * сторож снят со двери → падает С1/С4;
  * сторож смотрит ВСЕ футпринты, а не `produces` записи → падает С3.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.apply_duplicate_guard import (guard_duplicate_moves,
                                              guard_duplicate_plans,
                                              guard_duplicate_refdes)
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Angle, BoardLayer, Vector2
from kicadstamp.placement.commands import MoveCommand
from kicadstamp.placement.dependency_order import Item
from kicadstamp.refdes_duplicates import (duplicate_log_lines,
                                          duplicate_refdes_groups,
                                          duplicate_short_reason,
                                          orphan_short_reason)


def _fp(ref, uuid, x_mm=0.0, y_mm=0.0, chain=("sheet", "own")):
    """A domain footprint (the shape the adapter hands over). An EMPTY `chain`
    is an ORPHAN — the board bug's own shape."""
    return Footprint(ref=ref, uuid=uuid,
                     position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=0.0, layer=BoardLayer.BL_F_Cu,
                     sheet_path_uuids=tuple(chain))


class _Board:
    """The ONE adapter seam the guard reads: `get_footprints()`."""

    def __init__(self, footprints):
        self._footprints = list(footprints)
        self.reads = 0

    def get_footprints(self):
        self.reads += 1
        return list(self._footprints)


def _item(label, produces, anchor_ref=None):
    return Item(kind="clone", obj=None, label=label, anchor_ref=anchor_ref,
                produces=set(produces))


# ── С1 / С4: запись отказывает целиком ──────────────────────────────────────

def test_duplicated_refdes_refuses_the_item_whole_and_names_both_copies(caplog):
    """С1: две копии C35 (одна — сирота) → запись отказана целиком, а Лог
    называет ОБЕ копии, сироту и что делать."""
    board = _Board([
        _fp("C35", "real", 10.0, 20.0, chain=("sheet", "sym")),
        _fp("C35", "orphan", 208.075, 57.005, chain=()),
    ])
    item = _item("clone_placement 'spoke_vdd_04'", {"C35", "R7"})

    with caplog.at_level("ERROR"):
        allowed, report = guard_duplicate_refdes(board, [item])

    assert allowed == [], "запись с дублем обязана отказывать ЦЕЛИКОМ"
    assert report and len(report) == 1, "итог прогона называет отказавшую запись"
    text = caplog.text
    assert "C35" in text and "real" in text and "orphan" in text, (
        "строка Лога обязана назвать ОБЕ копии (uuid): " + text)
    assert "NOT linked to the schematic" in text, (
        "сирота обязана быть названа сиротой: " + text)
    assert "nothing of this record was moved" in text


def test_one_duplicate_among_several_roles_refuses_the_whole_record(caplog):
    """С4: из четырёх ролей дублирован ОДИН refdes — запись всё равно отказывает
    целиком, то есть ни ходов, ни меди (атомарность: медь планируется от позиций
    всех ролей). Клетка Дениса 12.10."""
    board = _Board([
        _fp("C35", "real"), _fp("C35", "orphan", chain=()),
        _fp("R7", "r7"), _fp("C1", "c1"), _fp("D1", "d1"),
    ])
    item = _item("clone_placement 'spoke_vdd_04'", {"C35", "R7", "C1", "D1"})

    with caplog.at_level("ERROR"):
        allowed, _report = guard_duplicate_refdes(board, [item])

    assert allowed == [], (
        "одного дубля из четырёх ролей достаточно для отказа ВСЕЙ записи")


# ── С2: без дублей — прежнее поведение ──────────────────────────────────────

def test_items_without_duplicates_pass_through_unchanged():
    """С2: сторож молчит, порядок и состав записей те же."""
    board = _Board([_fp("C35", "a"), _fp("R7", "b"), _fp("C1", "c")])
    items = [_item("clone_placement 'x'", {"C35", "R7"}),
             _item("clone_placement 'y'", {"C1"})]

    allowed, report = guard_duplicate_refdes(board, items)

    assert allowed == items, "порядок и состав записей не меняются"
    assert report == [], "без дублей сторож не пишет ни строки"


# ── С3: дубль ВНЕ разрешённых ролей записи — не мешает ──────────────────────

def test_duplicate_outside_the_items_own_refs_does_not_refuse():
    """С3: дубль чужого refdes не имеет права отказать запись, которая его не
    трогает. Убивает мутацию «сторож смотрит все футпринты, а не produces»."""
    board = _Board([
        _fp("C18", "orphan-1", chain=()), _fp("C18", "orphan-2", chain=()),
        _fp("C35", "real"),
    ])
    item = _item("clone_placement 'spoke_vdd_04'", {"C35"})

    allowed, report = guard_duplicate_refdes(board, [item])

    assert allowed == [item], "дубль чужого refdes не мешает"
    assert report == []


# ── Решение Дениса: сирота БЕЗ дубля конвейер не останавливает ──────────────

def test_lone_orphan_is_not_refused_by_the_pipeline():
    """Сирота без дубля — один футпринт, неоднозначности нет, двигать нечем
    ошибиться. Строку про неё показывает только окно «Добавить сущности»."""
    board = _Board([_fp("C18", "orphan", chain=()), _fp("R7", "r7")])
    item = _item("clone_placement 'x'", {"C18", "R7"})

    allowed, report = guard_duplicate_refdes(board, [item])

    assert allowed == [item]
    assert report == []


def test_the_pipeline_door_itself_refuses_before_planning(monkeypatch, caplog):
    """Дверь — это `ApplyPipeline.run`, а не только функция сторожа: тест
    подменяет шаги конвейера пустышками и проверяет, что `run()` отфильтровал
    запись ДО планирования ходов и передал отказ дальше. Убивает мутацию «сторож
    снят со двери» (правило: сторож на подделке без двери — пустышка)."""
    from kicadstamp.apply_pipeline import ApplyPipeline

    pipe = ApplyPipeline("unused.sexp")
    pipe.adapter = _Board([_fp("C35", "real"), _fp("C35", "orphan", chain=())])
    pipe.items = [_item("clone_placement 'spoke_vdd_04'", {"C35"})]
    pipe._scheme_plans = []
    for step in ("_load_config", "_filter_config", "_connect_adapter",
                 "_validate", "_resolve_order", "_create_planner"):
        monkeypatch.setattr(pipe, step, lambda: None)
    seen: dict = {}
    monkeypatch.setattr(pipe, "_execute", lambda: seen.update(
        items=list(pipe.items), report=list(pipe._duplicate_report)))

    with caplog.at_level("ERROR"):
        pipe.run()

    assert seen["items"] == [], "дверь обязана отказать запись ДО планирования ходов"
    assert seen["report"], "итог прогона обязан назвать отказавшую запись"
    assert "C35" in caplog.text


def test_anchor_ref_is_guarded_too():
    """Запись адресуется ещё и якорем (anchor_ref) — дубль этого refdes обязан
    отказать её так же, как дубль роли."""
    board = _Board([_fp("U1", "a"), _fp("U1", "b", chain=())])
    item = _item("clone_placement 'x'", {"C1"}, anchor_ref="U1")

    allowed, _report = guard_duplicate_refdes(board, [item])

    assert allowed == []


# ── Phase 0 и импринты: те же ворота, тот же детектор ───────────────────────

def _move(ref):
    return MoveCommand(ref=ref, position=Vector2.from_xy_mm(1.0, 2.0),
                       angle=Angle(0.0), layer=BoardLayer.BL_F_Cu)


def test_phase0_move_with_duplicated_refdes_is_refused():
    """Phase 0 (coordinate_placements) идёт теми же воротами."""
    board = _Board([_fp("C35", "real"), _fp("C35", "orphan", chain=())])
    moves = [_move("C35"), _move("R7")]

    allowed, report = guard_duplicate_moves(board, moves)

    assert [m.ref for m in allowed] == ["R7"]
    assert report, "итог обязан назвать отказанный ход"


def test_imprint_plan_with_duplicated_refdes_is_refused():
    """Импринт-запись атомарна так же: её медь планируется от того же региона."""
    board = _Board([_fp("R1", "a"), _fp("R1", "b", chain=())])
    plan = SimpleNamespace(entity_name="amp", moves=[_move("R1"), _move("C2")])

    allowed, report = guard_duplicate_plans(board, [plan])

    assert allowed == []
    assert report

def test_guard_without_a_board_never_refuses():
    """Юнит-сборка пайплайна зовёт шаг с adapter=None — придуманного дубля быть
    не может, поэтому сторож молчит, а не падает."""
    item = _item("clone_placement 'x'", {"C35"})

    allowed, report = guard_duplicate_refdes(None, [item])

    assert allowed == [item]
    assert report == []


# ── Детектор: сам продукт-модуль ────────────────────────────────────────────

def test_detector_returns_only_refs_that_stand_twice():
    groups = duplicate_refdes_groups(
        [_fp("C35", "a"), _fp("C35", "b", chain=()), _fp("R7", "c")])
    assert set(groups) == {"C35"}
    assert [c.uuid for c in groups["C35"]] == ["a", "b"]
    assert [c.on_schematic for c in groups["C35"]] == [True, False]


def test_detector_restricts_itself_to_the_given_refs():
    """`refs` — это и есть «по разрешённым ролям прогона»: чужой дубль не читается
    даже как дубль (он не попадает в ответ)."""
    groups = duplicate_refdes_groups(
        [_fp("C18", "a"), _fp("C18", "b"), _fp("C35", "c"), _fp("C35", "d")],
        refs={"C35"})
    assert set(groups) == {"C35"}


def test_detector_treats_an_unknown_shape_as_linked_not_orphan():
    """Форма без цепочки и без явного флага — «неизвестно»: сиротой не
    объявляется (иначе здоровый футпринт отказал бы запись)."""
    class _Opaque:
        ref = "C35"
        uuid = "u"

    groups = duplicate_refdes_groups([_Opaque(), _Opaque()])
    assert [c.on_schematic for c in groups["C35"]] == [True, True]


def test_log_lines_name_both_copies_with_uuid_position_and_the_action():
    board = _Board([_fp("C35", "real", 1.0, 2.0),
                    _fp("C35", "orphan", 208.075, 57.005, chain=())])
    copies = duplicate_refdes_groups(board.get_footprints())["C35"]

    text = "\n".join(duplicate_log_lines("C35", copies))

    assert "copy 1/2" in text and "copy 2/2" in text
    assert "real" in text and "orphan" in text
    assert "1.000, 2.000 mm" in text and "208.075, 57.005 mm" in text
    assert "NOT linked to the schematic" in text


def test_position_not_read_is_spelled_out_rather_than_zero():
    """Запись без позиции (Part окна сущностей) не имеет права показать 0.000 как
    настоящее число."""
    class _NoPosition:
        ref = "C35"
        uuid = "u"

    copies = duplicate_refdes_groups([_NoPosition(), _NoPosition()])["C35"]
    assert "position not read" in "\n".join(duplicate_log_lines("C35", copies))


@pytest.mark.parametrize("orphan, expected", [
    (True, "C35 is on the board twice (one is not linked to the schematic)"),
    (False, "C35 is on the board twice (both copies are linked to the schematic)"),
])
def test_short_reason_names_the_duplicate_both_ways(orphan, expected):
    copies = duplicate_refdes_groups([
        _fp("C35", "a"), _fp("C35", "b", chain=() if orphan else ("sheet", "s"))])
    assert duplicate_short_reason("C35", copies["C35"]) == expected


def test_orphan_short_reason_names_a_lone_orphan():
    assert orphan_short_reason("C18") == "C18 is not linked to the schematic"
