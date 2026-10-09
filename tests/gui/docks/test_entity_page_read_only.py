# tests/gui/docks/test_entity_page_read_only.py
"""Сторожа read-only страницы СУЩНОСТИ (ШАГ 5, Т5-4 плана
plan_2026_10_09_entity_page; свойство перенесено со снятой страницы ЯЧЕЙКИ).

Правило (Денис, 09.10.2026): сирота по ГРАФУ (ячейки нет) и сирота по ПЛАТЕ
(экземпляра сущности нет в снимке) открывают страницу ТОЛЬКО ДЛЯ ЧТЕНИЯ — табы,
трогающие плату (Explode / Refs / Anchor), выключены, а «Справка» с комбобоксом
ячейки ЖИВА (сироту чинят именно ею). «НЕТ СНИМКА» сиротой НЕ считается: табы
платы включены, иначе отключённый KiCad гасил бы рабочую страницу.

Имена описывают свойство (правило 37); движок гейта — gui/entity/read_only.py.
"""
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
