# tests/gui/test_cell_entity_choice.py
"""Сторожа модели выпадашки «Entity» (gui/cell_entity_choice.py) — часть 2
задания plan_2026_10_05_entities_under_cells.

Слой: модуль зовётся НАПРЯМУЮ, без дока и без QApplication. Это и есть условие
модуля («ни одного импорта Qt») — если бы он тянул PyQt6, эти сторожа упали бы
на импорте.

Имена функций описывают СВОЙСТВО (правило 37), номер пункта плана — в
докстринге. Граф — настоящий (walk_include_tree + build_entity_index из части
1), чтобы связь «ячейка → её сущности» проверялась тем же индексом, которым
будет пользоваться страница, а не подделкой.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gui import settings
from gui.cell_edit_context import (
    CELL_EDIT_CONTEXT_KEY,
    remember_cell_instance,
    remembered_cell_refs,
)
from gui.cell_entity_choice import (
    LAST_ENTITY_KEY,
    MANUAL,
    SOURCE_ENTITY,
    SOURCE_SPOKE,
    WORKING_INSTANCE_KEY,
    address_matches_selection,
    build_choices,
    default_index,
    entity_address,
    explicit_kwargs,
    manual_address,
    not_the_entity_line,
    read_instance,
    remembered_last_entity,
    remember_last_entity,
    remember_working_instance,
    spoke_address,
    working_instance,
    write_applies_line,
)
from gui.docks.entity_index import build_entity_index
from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp

CELL_UUID = "00000000-0000-0000-0000-0000000000a1"
CELL = "dac_buf"


def _index(tmp_path: Path, entities: list, *, cell_name: str = CELL,
           cell_uuid: str = CELL_UUID):
    """A real one-cell graph through the ONE index of part 1."""
    root = tmp_path / "root.sexp"
    data = {
        "cells": {cell_name: {"uuid": cell_uuid,
                              "components": [{"role": "R"}]}},
        "entities": entities,
    }
    root.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")
    return build_entity_index(walk_include_tree(str(root)))


def _entity(name: str, **extra) -> dict:
    return {"name": name, "uuid": f"ent-{name}", "cell": CELL,
            "cell_uuid": CELL_UUID, **extra}


# ═══════════════════════════════════════════════════════════════════════════
# Модуль Qt-free (условие модуля)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_choice_module_has_no_qt_import():
    """Условие модуля: НИ ОДНОГО импорта Qt. Статическая проверка исходника —
    надёжнее прогона (в общем pytest-сеансе PyQt6 уже в sys.modules)."""
    import gui.cell_entity_choice as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for token in ("PyQt6", "QtCore", "QtWidgets", "QtGui"):
        assert token not in src, f"cell_entity_choice не должен упоминать {token!r}"


# ═══════════════════════════════════════════════════════════════════════════
# Подпись адреса (п.1)
# ═══════════════════════════════════════════════════════════════════════════

def test_an_entity_address_names_its_cluster_and_sheet():
    """п.1: подпись сущности — «<имя> — <кластер> on <лист>»."""
    row = entity_address(_entity("ch1", cluster="DAC_BUF", sheet="Channel_1"))
    assert row.label == "ch1 — DAC_BUF on Channel_1"
    assert (row.source, row.entity_name, row.cluster, row.sheet, row.refs) == (
        "entity", "ch1", "DAC_BUF", "Channel_1", {})


def test_an_entity_address_that_pins_refs_labels_them():
    """п.1: `refs:` у сущности — «<имя> — refs C43, C44» (значения, по порядку)."""
    row = entity_address(_entity("pinned", refs={"R_OUT": "C44", "R_IN": "C43"}))
    assert row.label == "pinned — refs C43, C44"
    assert row.refs == {"R_IN": "C43", "R_OUT": "C44"}


def test_an_entity_without_a_cluster_is_labelled_not_placed():
    """Сущность без кластера (ещё не поставлена) честно это и говорит — и НЕ
    притворяется адресом с пустым листом."""
    row = entity_address(_entity("bare"))
    assert row.label == "bare — not placed"
    assert row.cluster is None and row.sheet is None


def test_an_entity_of_another_cell_is_not_in_the_list(tmp_path):
    """Список — сущности ИМЕННО этой ячейки (связь по cell_uuid), не всего файла."""
    from gui.cell_entity_choice import entity_addresses
    idx = _index(tmp_path, [
        _entity("mine", cluster="DAC_BUF", sheet="Channel_0"),
        {"name": "theirs", "uuid": "ent-x", "cell": "other",
         "cell_uuid": "00000000-0000-0000-0000-0000000000ff"},
    ])
    assert [r.entity_name for r in entity_addresses(idx, CELL_UUID)] == ["mine"]


def test_the_entities_of_a_cell_come_from_the_part_one_index(tmp_path):
    """Список адресов — из ТОГО ЖЕ индекса части 1, в его порядке (по имени)."""
    from gui.cell_entity_choice import entity_addresses
    idx = _index(tmp_path, [
        _entity("ch2", cluster="DAC_BUF", sheet="Channel_2"),
        _entity("ch0", cluster="DAC_BUF", sheet="Channel_0"),
        _entity("ch1", cluster="DAC_BUF", sheet="Channel_1"),
    ])
    assert [r.entity_name for r in entity_addresses(idx, CELL_UUID)] == [
        "ch0", "ch1", "ch2"]


# ═══════════════════════════════════════════════════════════════════════════
# Порядок списка и выбор по умолчанию (п.1 / п.2а / п.4)
# ═══════════════════════════════════════════════════════════════════════════

def _entity_rows(*names):
    return [entity_address(_entity(n, cluster="DAC_BUF", sheet=f"Ch_{n}"))
            for n in names]


def test_the_choices_are_entities_then_spokes_then_manual():
    """п.1/п.2а: сущности → спицы → «Manual…» последним."""
    choices = build_choices(_entity_rows("ch0", "ch1"),
                            [spoke_address("MCU Vdd", "A1")])
    assert [r.source for r in choices] == [
        SOURCE_ENTITY, SOURCE_ENTITY, SOURCE_SPOKE, MANUAL]
    assert choices[-1].label == "Manual…"
    assert choices[-2].label == "MCU Vdd — pad A1"


def test_the_default_row_is_the_entity_the_page_came_from():
    """п.4: открыта из сущности — выпадашка стоит на ней (по ИМЕНИ)."""
    choices = build_choices(_entity_rows("ch0", "ch1", "ch2"))
    assert choices[default_index(choices, opened_from="ch2")].entity_name == "ch2"
    assert choices[default_index(
        choices, opened_from="ch2", last_entity="ch0")].entity_name == "ch2"


def test_the_default_row_is_the_last_entity_of_the_cell():
    """п.4: открыта с ячейки — последняя выбранная у неё сущность."""
    choices = build_choices(_entity_rows("ch0", "ch1", "ch2"))
    assert choices[default_index(choices, last_entity="ch1")].entity_name == "ch1"


def test_the_default_row_falls_back_to_the_first_entity_by_name():
    """п.4: последней сущности больше нет — первая по имени."""
    choices = build_choices(_entity_rows("ch0", "ch1", "ch2"))
    assert choices[default_index(choices, last_entity="gone")].entity_name == "ch0"


def test_a_cell_without_entities_starts_on_its_first_resolvable_spoke():
    """п.2а: «Manual…» по умолчанию — только пока у ячейки нет ни сущностей, ни
    РАЗРЕШИМЫХ спиц."""
    choices = build_choices(
        (),
        [spoke_address("MCU Vdd", "A1", resolved=False),
         spoke_address("MCU Vdd", "A2")])
    assert choices[default_index(choices)].label == "MCU Vdd — pad A2"


def test_an_unresolvable_spoke_has_a_row_but_is_never_the_default():
    """п.2а: неразрешимая спица — пункт ЕСТЬ, но умолчанием не станет, и
    строка помечена resolved=False (действие по ней даёт красную строку)."""
    bad = spoke_address("MCU Vdd", "A1", resolved=False)
    choices = build_choices((), [bad])
    assert [r.label for r in choices] == ["MCU Vdd — pad A1", "Manual…"]
    assert bad.resolved is False
    assert choices[default_index(choices)].is_manual is True


def test_a_cell_with_nothing_to_choose_is_empty_and_starts_on_manual():
    """п.3: ячейку не ставит никто — выпадашка пуста; без пункта «Manual…»
    выбирать нечего (индекс -1, а не «первый попавшийся»)."""
    assert build_choices((), (), manual=False) == []
    assert default_index([]) == -1
    only_manual = build_choices(())
    assert [r.source for r in only_manual] == [MANUAL]
    assert default_index(only_manual) == 0


# ═══════════════════════════════════════════════════════════════════════════
# «Последняя сущность ячейки» — отдельный ключ, не контекст (п.4)
# ═══════════════════════════════════════════════════════════════════════════

def test_choosing_an_entity_leaves_the_cells_remembered_context_alone(tmp_path):
    """п.4 (мутация «выбор пишет remember_cell_edit_context»): выбор в выпадашке
    НЕ трогает контекст ячейки — опознанные refs остаются на месте."""
    root = tmp_path / "root.sexp"
    root.write_text("(config)", encoding="utf-8")
    remember_cell_instance(root, CELL, SimpleNamespace(
        cluster="DAC_BUF", sheet="Channel_0", role_to_ref={"R": "C43"}))
    before = settings.state.get(CELL_EDIT_CONTEXT_KEY)

    remember_last_entity(root, CELL, "ch1")

    assert settings.state.get(CELL_EDIT_CONTEXT_KEY) == before
    assert remembered_cell_refs(root, CELL) == {"R": "C43"}


def test_choosing_an_entity_remembers_its_name_under_its_own_key(tmp_path):
    """п.4: запоминается ИМЯ сущности, под своим ключом, по корню."""
    root = tmp_path / "root.sexp"
    assert remembered_last_entity(root, CELL) is None
    remember_last_entity(root, CELL, "ch1")
    assert remembered_last_entity(root, CELL) == "ch1"
    assert "ch1" in settings.state.get(LAST_ENTITY_KEY, {}).get(str(root), {}).values()
    assert settings.state.get(CELL_EDIT_CONTEXT_KEY) in (None, {})


def test_a_malformed_last_entity_state_reads_as_nothing(tmp_path):
    """Читатель деградирует молча: битая запись — это «ничего не помним»."""
    root = tmp_path / "root.sexp"
    settings.state.set(LAST_ENTITY_KEY, {str(root): "не-словарь"})
    assert remembered_last_entity(root, CELL) is None
    assert remembered_last_entity(None, CELL) is None
    assert remembered_last_entity(root, "") is None


# ═══════════════════════════════════════════════════════════════════════════
# Явный экземпляр в payload (п.5)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_explicit_instance_is_the_rows_own_address():
    """п.5: действие берёт экземпляр ИЗ ВЫПАДАШКИ — (cluster, sheet, refs) строки."""
    row = entity_address(_entity("ch1", cluster="DAC_BUF", sheet="Channel_1"))
    assert explicit_kwargs(row) == {"cluster": "DAC_BUF", "sheet": "Channel_1"}
    pinned = entity_address(_entity("pinned", refs={"R": "C43"}))
    assert explicit_kwargs(pinned) == {"refs": {"R": "C43"}}


def test_a_manual_row_has_no_explicit_instance():
    """«Manual…» ничего не навязывает: там экземпляр — поля самой страницы."""
    assert explicit_kwargs(manual_address()) == {}
    assert explicit_kwargs(None) == {}


def test_the_explicit_instance_does_not_carry_a_missing_sheet():
    """Экземпляр без листа не выдумывает лист — ключа просто нет."""
    row = entity_address(_entity("ch1", cluster="DAC_BUF"))
    assert explicit_kwargs(row) == {"cluster": "DAC_BUF"}


# ═══════════════════════════════════════════════════════════════════════════
# Жёсткая привязка чтения к выбранной сущности (п.5) и строка записи (п.6)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_read_pinned_to_an_entity_matches_its_own_instance_only():
    """п.5: чтение, привязанное к сущности, узнаёт СВОЙ экземпляр по адресу — и
    не узнаёт чужой (правило адреса одно, selection_narrowing)."""
    row = entity_address(_entity("ch1", cluster="DAC_BUF", sheet="Channel_1"))
    assert address_matches_selection(row, "DAC_BUF", "Channel_1")
    # The board tag may REFINE the config's (the one prefix rule, not a second).
    assert address_matches_selection(row, "DAC_BUF/IC2", None)
    assert not address_matches_selection(row, "OTHER", None)
    assert not address_matches_selection(row, "DAC_BUF", "Channel_2")


def test_a_stale_or_non_entity_row_matches_nothing():
    """Строка без адреса («Manual…», сущность без кластера) ни с чем не
    совпадает — привязываться не к чему."""
    assert not address_matches_selection(manual_address(), "DAC_BUF", None)
    assert not address_matches_selection(entity_address(_entity("bare")),
                                         "DAC_BUF", None)
    assert not address_matches_selection(None, "DAC_BUF", None)
    assert not_the_entity_line("ch1") == \
        "the selection is not entity 'ch1' — nothing read"


def test_the_working_instance_is_stored_and_read_back_as_the_same_address():
    """п.1: рабочий экземпляр ячейки лежит в ОДНОМ хранилище — что выпадашка
    записала, то читатель (CellDock) и прочитает."""
    root = Path("/tmp/root.sexp")
    row = entity_address(_entity("ch1", cluster="DAC_BUF", sheet="Channel_1",
                                 refs={"R_IN": "C43"}))
    assert working_instance(root, CELL) is None

    remember_working_instance(root, CELL, row)

    back = working_instance(root, CELL)
    assert back is not None
    assert (back.entity_name, back.cluster, back.sheet, back.refs) == (
        "ch1", "DAC_BUF", "Channel_1", {"R_IN": "C43"})
    assert back.label == row.label
    assert str(root) in settings.state.get(WORKING_INSTANCE_KEY, {})


def test_the_manual_row_clears_the_working_instance():
    """п.2: «Manual…» означает «экземпляр — поля самой страницы», и залежавшаяся
    запись сущности не смеет перебивать их."""
    root = Path("/tmp/root.sexp")
    remember_working_instance(root, CELL,
                              entity_address(_entity("ch1", cluster="DAC_BUF")))
    assert working_instance(root, CELL) is not None

    remember_working_instance(root, CELL, manual_address())

    assert working_instance(root, CELL) is None
    assert settings.state.get(WORKING_INSTANCE_KEY) == {}


def test_a_read_uses_the_entity_the_page_is_on():
    """п.1: читатель (CellDock) зовёт read_instance ОДИН раз и получает экземпляр
    страницы — адрес сущности вместе с её (кластер, лист) и refs."""
    root = Path("/tmp/root.sexp")
    remember_working_instance(root, CELL, entity_address(
        _entity("ch1", cluster="DAC_BUF", sheet="Channel_1",
                refs={"R_IN": "C43"})))

    read = read_instance(root, CELL)

    assert read.is_pinned
    assert read.address.entity_name == "ch1"
    assert (read.cluster, read.sheet) == ("DAC_BUF", "Channel_1")
    assert read.refs == {"R_IN": "C43"}


def test_a_read_on_a_manual_cell_falls_back_to_the_remembered_pair():
    """«Manual…» (или ячейка, которой страница не занималась): читатель получает
    запомненный контекст и опознанные refs — как до части 2."""
    root = Path("/tmp/root.sexp")
    remember_cell_instance(root, CELL, SimpleNamespace(
        cluster="OLD_CLUSTER", sheet="Channel_0", role_to_ref={"R": "C43"}))

    read = read_instance(root, CELL)

    assert not read.is_pinned and read.address is None
    assert (read.cluster, read.sheet) == ("OLD_CLUSTER", "Channel_0")
    assert read.refs == {"R": "C43"}


def test_a_read_with_nothing_remembered_is_empty():
    """Ничего не помним — читатель идёт читать с пустым экземпляром (и получает
    тот же отказ, что и раньше), а не падает."""
    read = read_instance(Path("/tmp/root.sexp"), CELL)
    assert not read.is_pinned
    assert (read.address, read.cluster, read.sheet, read.refs) == \
        (None, None, None, None)
    assert read_instance(None, CELL).refs is None


def test_a_malformed_working_instance_reads_as_nothing():
    """Битое/чужое состояние — это «ничего не записано», а не падение: читатель
    тогда идёт к запомненному контексту, как до части 2."""
    root = Path("/tmp/root.sexp")
    settings.state.set(WORKING_INSTANCE_KEY, "не-словарь")
    assert working_instance(root, CELL) is None
    settings.state.set(WORKING_INSTANCE_KEY, {str(root): {CELL: {"cluster": "X"}}})
    assert working_instance(root, CELL) is None      # нет имени — не адрес
    assert working_instance(None, CELL) is None
    assert working_instance(root, "") is None


def test_a_write_under_an_entity_says_it_applies_to_every_entity():
    """п.6: пишется ЯЧЕЙКА — и Лог говорит это прямо, называя экземпляр, от
    которого пришла правка."""
    row = entity_address(_entity("ch1", cluster="DAC_BUF", sheet="Channel_1"))
    assert write_applies_line("dac_buf", row) == (
        "cell 'dac_buf' updated from entity 'ch1' (DAC_BUF on Channel_1) — "
        "the change applies to every entity of the cell")
    raw = entity_address(_entity("bare"))
    assert write_applies_line("dac_buf", raw) == (
        "cell 'dac_buf' updated from entity 'bare' (None on (no sheet)) — "
        "the change applies to every entity of the cell")
    assert write_applies_line("dac_buf", manual_address()) == ""
    assert write_applies_line("dac_buf", None) == ""
