# tests/gui/test_create_entity_menu.py
"""Сторожа для plan_2026_09_20_create_entity_menu.md — «Создать сущность»
в контекстном меню Config-дерева.

Здесь живёт §3 плана: сторожа С1–С9 и их мутации М1–М9. Этот файл — их
общий дом, чтобы сводный tests/gui/test_config_tree.py не пухнул от каждой
правки вокруг «Create entity».

ПРАВИЛО, КОТОРОЕ ЗДЕСЬ ГЛАВНОЕ (см. §3, формулировка С1). Сторож смотрит
на РЕЗУЛЬТАТ — состояние конфига ПОСЛЕ срабатывания действия, — а не на
оформление. В частности:

  * пункт меню ищется ПО СИГНАЛУ (add_entity_requested), а не по
    переведённой подписи: текст "Create entity" зависит от локали и от
    каталогов, и сторож, привязанный к нему, будет падать по посторонним
    причинам (недокомпилированный .mo, переведённая строка, и т.п.).
    Практически «по сигналу» здесь читается так:
      - сам QAction-якорь берём по objectName "create_entity_action" —
        это стабильный, нелокализованный идентификатор, установленный в
        config_tree.py РЯДОМ с тем же `add_entity_requested.emit(...)`,
        то есть «сигнальная сторона» пункта;
      - и ДОПОЛНИТЕЛЬНО сторож явно проверяет, что найденный QAction
        эмитит ИМЕННО add_entity_requested — шпионом на сигнале. Иначе
        формулировка «по сигналу» осталась бы на словах;
  * у срабатывания проверяется запись в entities:, а не строки Log, не
    заголовки окон и не порядок кнопок.

Почему тест идёт через real_main_window, а не через ConfigTreeDock напрямую:
обработчик живёт в DockHub (gui/dock_hub.py::_create_entity_from_tree) —
он читает root_path у root_metadata_dock и пишет в файл через config_writer.
Один и тот же путь, что в реальной GUI: клик по action -> сигнал дока ->
слот хаба. Если обойти хаб, сторож перестанет ловить расхождения между
доком и обработчиком (а именно эти расхождения он и охраняет).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QDialog

import gui.docks.config_tree as config_tree_mod
import gui.docks.create_entity as create_entity_mod
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_working_set import WORKING_SET


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _find(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def _context_menu_actions(dock, item, monkeypatch):
    """Собрать КОНТЕКСТНОЕ МЕНЮ узла и вернуть список (label, QAction).

    QMenu.exec — no-op (меню реально не показывается), addAction — обёрнут,
    чтобы поймать каждый созданный QAction вместе с его подписью. Так же
    поступает tests/gui/test_config_tree.py::_context_menu_actions; копия
    здесь намеренная — тестовые файлы не импортируют друг друга."""
    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original_add_action = config_tree_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        action = original_add_action(self, text, *a, **k)
        captured.append((text, action))
        return action

    monkeypatch.setattr(config_tree_mod.QMenu, "addAction", _record)
    dock._on_context_menu(dock.tree.visualItemRect(item).center())
    return captured


def _fake_dialog(name, cluster, sheet):
    """Подмена CreateEntityDialog — форма в этом стороже не проверяется (у
    неё — свой предмет в Т2а), а нужно ровно одно: результат_data() и то,
    что диалог был «принят». Сохраняем и аргументы конструктора — они
    ниже проверяются там, где это важно (мутация М2 и т.п.)."""
    class _FakeDialog:
        def __init__(self, parent, source_kind, source_name, existing_names):
            self.source_kind = source_kind
            self.source_name = source_name
            self.existing_names = set(existing_names or ())

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_data(self):
            return (name, cluster, sheet)

    return _FakeDialog


# ── С1 (главный §3) ─────────────────────────────────────────────────────


def test_c1_create_entity_on_a_cell_leaf_writes_a_cell_entity(
        real_main_window, tmp_path, monkeypatch):
    """С1: пункт есть на узле ЯЧЕЙКИ и его срабатывание создаёт запись
    entities: с `cell: "<имя ячейки>"`. Проверяется РЕЗУЛЬТАТ, а не
    оформление: смотрим в ФАЙЛ конфига после срабатывания.

    Требование §3: пункт меню искать ПО СИГНАЛУ, а не по переведённой
    подписи. Здесь это сделано двумя шагами:
      1) QAction-якорь берём по objectName "create_entity_action" — это
         нелокализованный идентификатор, установленный в config_tree.py
         рядом с `add_entity_requested.emit(...)`, т.е. «сигнальная
         сторона» пункта. Никаких проверок текста подписи нет;
      2) на сигнал add_entity_requested ставим шпиона и явно проверяем,
         что найденный QAction эмитит ИМЕННО его (payload совпадает с
         ("cell", "my_cell", <путь к файлу>)). Это закрывает «по сигналу»
         буквально, а не описательно.

    Мутация М1, от которой этот сторож защищает: оставить пункт только у
    отпечатка (тогда на узле ячейки действия нет, и первая половина теста
    падает — но падает с понятным сообщением, а не «menu is empty» без
    объяснений).

    База 65af2d9: сегодня пункта нет вовсе. На базе сторож падает на первой
    половине («в контекстном меню узла ячейки нет ровно одного действия
    create_entity_action...»). Это — ожидаемое сообщение, дословно уйдёт в
    отчёт done_2026_09_20_create_entity_menu.md."""
    hub = real_main_window._dock_hub
    target = tmp_path / "root.sexp"
    _write(target, {"cells": {"my_cell": {"components": [{"role": "R"}]}}})

    # Проект открываем тем же путём, что реальный GUI (File -> Open Root
    # file...): set_root_file на root_metadata_dock. Это, помимо root_path,
    # шлёт root_changed во все доки — Config-дерево перестраивается само.
    hub.root_metadata_dock.set_root_file(target)
    # RootMetadataDock включает staged-режим (ConfigWorkingSet) на открытии
    # проекта — это штатное поведение GUI. Здесь же важен результат в ФАЙЛЕ:
    # работаем с чистым диском, а не с working-set, чтобы проверять
    # «запись появилась в entities:» именно в файле.
    WORKING_SET.enabled = False

    tree = hub.config_tree_dock.tree
    leaf = _find(_find(tree.topLevelItem(0), "Cells"), "my_cell")

    actions = _context_menu_actions(hub.config_tree_dock, leaf, monkeypatch)
    matching = [(text, act) for text, act in actions
                if act.objectName() == "create_entity_action"]
    assert len(matching) == 1, (
        "в контекстном меню узла ячейки нет ровно одного действия "
        "create_entity_action — ищем по objectName, а не по подписи; "
        "видели: " + repr([t for t, _ in actions]))
    _label, action = matching[0]

    # Сторож привязан к СИГНАЛУ, а не к подписи: наблюдаем payload, который
    # уйдёт в обработчик, и сверяем его с ожидаемым ("cell", <имя>, <файл>).
    emitted = []
    hub.config_tree_dock.add_entity_requested.connect(
        lambda kind, name, path: emitted.append((kind, name, str(path))))

    fake_cls = _fake_dialog("buf_entity", "CH0", "Sheet_1")
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog", fake_cls)
    action.trigger()

    assert emitted == [("cell", "my_cell", str(target.resolve()))], (
        "найденный по objectName QAction обязан эмитить add_entity_requested"
        "('cell', 'my_cell', <путь к файлу>) — сторож держится за сигнал,"
        " а не за переведённую подпись; видели: " + repr(emitted))

    entities = _load(target).get("entities") or []
    written = [e for e in entities if e.get("cell") == "my_cell"]
    assert len(written) == 1, (
        "срабатывание действия на узле ячейки должно было создать ровно "
        "одну entities:-запись с cell: 'my_cell'; сейчас entities: "
        + repr(entities))
    assert written[0]["name"] == "buf_entity"
    assert written[0]["cluster"] == "CH0"
    assert written[0]["sheet"] == "Sheet_1"
    assert "imprint" not in written[0]
