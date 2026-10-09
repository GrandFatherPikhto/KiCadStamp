# tests/gui/docks/test_entity_page_read_only.py
"""Сторожа read-only страницы СУЩНОСТИ (ШАГ 5, Т5-4 плана
plan_2026_10_09_entity_page; свойство перенесено со снятой страницы ЯЧЕЙКИ).

Правило (Денис, 09.10.2026): сирота по ГРАФУ (ячейки нет) и сирота по ПЛАТЕ
(экземпляра сущности нет в снимке) открывают страницу ТОЛЬКО ДЛЯ ЧТЕНИЯ — табы,
трогающие плату (Explode / Refs / Anchor), выключены, а «Справка» с комбобоксом
ячейки ЖИВА (сироту чинят именно ею). «НЕТ СНИМКА» сиротой НЕ считается: табы
платы включены, иначе отключённый KiCad гасил бы рабочую страницу.

Д1 (plan_2026_10_09_entity_page): приход снимка (push_known_lists →
DockHub.push_snapshot → EntityPage.refresh_known_roles) — это ПЕРЕСЧЁТ: страница
судит по ПРИШЕДШЕМУ снимку, а не по вердикту открытия и не по копии соединения.

Имена описывают свойство (правило 37); движок гейта — gui/entity/read_only.py.
"""
from types import SimpleNamespace

from tests.gui.create_entity_helpers import open_project, write_config


class _Fp:
    def __init__(self, uuid):
        self.uuid = uuid


class _Sel:
    def __init__(self, ref, cluster, role="R", sheet=("Ch0",)):
        self.ref = ref
        self.role = role
        self.cluster = cluster
        self.sheet = sheet
        self.fp = _Fp("u-" + ref)


def _open(window, tmp_path, data, snapshot):
    hub = window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, data)
    open_project(hub, root)
    if snapshot is not None:
        window.connection._snapshot = list(snapshot)
    return hub, root


def _board_tabs(page):
    return (page._explode_page, page._refs_tab, page._anchor_tab)


def _tabs_on(page) -> bool:
    tabs = page.tabs
    return all(tabs.isTabEnabled(tabs.indexOf(w)) for w in _board_tabs(page))


def test_a_board_orphan_turns_the_board_tabs_off_but_keeps_the_combobox(
        real_main_window, tmp_path):
    """Сирота по ПЛАТЕ (снимок есть, экземпляра сущности в нём нет): табы платы
    выключены, «Справка» с комбобоксом ЖИВА (мутация «гейт выключает весь
    виджет» роняет эту клетку), подсказка видна."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "OTHER")])
    page = hub.entity_dock
    page.load_entity("e1")

    assert _tabs_on(page) is False, "табы платы у сироты обязаны быть выключены"
    assert page.cell_combo.isEnabled() is True, \
        "комбобокс ячейки — то, чем чинят сироту: он обязан быть доступен"
    assert page._read_only_gate.note.isVisibleTo(page) is True
    assert "not on the board" in page._read_only_gate.note.text()


def test_a_graph_orphan_turns_the_board_tabs_off(real_main_window, tmp_path):
    """Сирота по ГРАФУ (сущность называет ячейку, которой нет) — тот же read-only,
    но подсказка называет ушедшую ячейку."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "MISSING", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    assert _tabs_on(page) is False
    assert "missing from the project" in page._read_only_gate.note.text()


def test_no_snapshot_keeps_the_board_tabs_on(real_main_window, tmp_path):
    """НЕТ снимка — «подбор не проверялся», а НЕ сирота: табы платы ВКЛ."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [])
    page = hub.entity_dock
    page.load_entity("e1")

    assert _tabs_on(page) is True, "без снимка страница НЕ read-only"
    assert page._read_only_gate.note.isVisibleTo(page) is False


def test_a_healthy_entity_keeps_the_board_tabs_on(real_main_window, tmp_path):
    """Экземпляр сущности есть в снимке — обычная страница, подсказки нет."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    assert _tabs_on(page) is True
    assert page._read_only_gate.note.isVisibleTo(page) is False


def test_a_read_only_open_on_a_board_tab_falls_back_to_the_reference(
        real_main_window, tmp_path):
    """Если выключенный таб был ОТКРЫТ, read-only уводит вид на «Справку»: перед
    пользователем не остаётся мёртвого таба."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"},
                     {"name": "e2", "cell": "c_ok", "cluster": "CL2",
                      "sheet": "Ch1"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")                       # healthy -> tabs on
    page.tabs.setCurrentWidget(page._refs_tab)   # the user sits on a board tab
    page.load_entity("e2")                       # e2's instance is not on the board

    assert page.tabs.currentIndex() == 0, "вид ушёл с выключенного таба"
    assert page.tabs.currentWidget() not in _board_tabs(page)


def test_an_entity_with_no_record_on_disk_is_read_only(real_main_window, tmp_path):
    """Несохранённая сущность (записи в файле нет — `_load_entity_dict` → None)
    тоже read-only: подсказка зовёт сохранить, табы платы выключены."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")            # healthy first -> tabs on
    page.load_entity("ghost")         # no such record on disk at all

    assert _tabs_on(page) is False
    assert "not saved in the project yet" in page._read_only_gate.note.text()


# ── Д1: push — это ПЕРЕСЧЁТ по пришедшему снимку ─────────────────────────────

def test_a_push_with_the_instance_turns_the_board_tabs_back_on(
        real_main_window, tmp_path):
    """Д1: сирота по ПЛАТЕ (снимок без экземпляра) → push С экземпляром: табы
    платы ВКЛ, подсказка скрыта — БЕЗ повторного открытия сущности.

    Снимок соединения нарочно оставлен СТАРЫМ (сиротским): страница обязана
    судить по ПРИШЕДШЕМУ снимку (мутация «push читает connection.snapshot» и
    мутация «push не пересчитывает» краснят эту клетку)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "OTHER")])
    page = hub.entity_dock
    page.load_entity("e1")
    assert _tabs_on(page) is False, "исходно — сирота по плате"

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert page._cell_choices.state == "checked"
    assert _tabs_on(page) is True, "свежий снимок с экземпляром возвращает табы"
    assert page._read_only_gate.note.isVisibleTo(page) is False


def test_a_push_without_the_instance_turns_the_board_tabs_off(
        real_main_window, tmp_path):
    """Д1, обратная сторона: здоровая страница → push БЕЗ экземпляра: табы ВЫКЛ,
    подсказка называет плату — страница не остаётся «рабочей» по старому
    вердикту."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")
    assert _tabs_on(page) is True

    page.refresh_known_roles([_Sel("R1", "OTHER")])

    assert _tabs_on(page) is False
    assert "not on the board" in page._read_only_gate.note.text()


def test_a_push_keeps_an_unchecked_page_on(real_main_window, tmp_path):
    """Д1: «подбор не проверялся» (снимка не было) → push С экземпляром: табы
    остаются ВКЛ (состояние становится проверенным, а не сиротским)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [])
    page = hub.entity_dock
    page.load_entity("e1")
    assert page._cell_choices.state == "unchecked" and _tabs_on(page) is True

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert page._cell_choices.state == "checked"
    assert _tabs_on(page) is True
    assert page._read_only_gate.note.isVisibleTo(page) is False


def test_a_push_on_a_page_without_a_loaded_entity_changes_nothing(
        real_main_window, tmp_path):
    """Д1: страница НЕ открыта (запись не загружена) → push ничего не ломает:
    комбобокс пуст, табы платы живы — судить нечего."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "OTHER")])
    page = hub.entity_dock

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert page.cell_combo.count() == 0, "загруженной записи нет — заполнять нечего"
    assert _tabs_on(page) is True, "пустая страница в read-only не уходит"


def test_a_push_keeps_an_unsaved_entity_read_only(real_main_window, tmp_path):
    """Д1: несохранённая («ghost») сущность остаётся read-only и после push —
    её read-only держит ОТСУТСТВИЕ записи, а не снимок; комбобокс пуст."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")
    page.load_entity("ghost")
    assert _tabs_on(page) is False

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert _tabs_on(page) is False
    assert "not saved in the project yet" in page._read_only_gate.note.text()
    assert page.cell_combo.count() == 0


def test_a_push_does_not_read_the_board(real_main_window, tmp_path):
    """Д1: пересчёт берёт ТОЛЬКО пришедший снимок — адаптер платы на UI-потоке не
    зовётся (дверь, правило 31)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "OTHER")])
    page = hub.entity_dock
    page.load_entity("e1")

    class _RecordingAdapter:
        def __init__(self):
            self.calls = []

        def refresh_board(self):
            self.calls.append("refresh_board")

        def get_selected_items(self):
            self.calls.append("get_selected_items")
            return []

        def get_footprints(self):
            self.calls.append("get_footprints")
            return []

    adapter = _RecordingAdapter()
    real_main_window.connection.board = SimpleNamespace(adapter=adapter)

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert adapter.calls == [], f"UI-поток читал плату: {adapter.calls}"
