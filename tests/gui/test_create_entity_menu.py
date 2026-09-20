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

Ниже живут сторожа С2–С8 из §3 того же плана и их мутации М2–М8. С5а и С5б
написаны каждый ДВУМЯ половинами (ячейка и отпечаток) — нарочно: падение одной
половины не должно прятать состояние другой, а мутация у каждого из них одна
(М5а / М5б). С9 — не здесь: это существующий TestCatalogCompleteness в
tests/test_i18n.py, его достаточно прогнать; отдельного сторожа он не требует.

Общие помощники объявлены НИЖЕ С1 — чтобы главный сторож читался первым.

В КОНЦЕ файла — сторожа второго, сегодняшнего плана
`plan_2026_09_20_empty_cluster_normalisation.md`: нормализация пустого поля
формы в None (С1/С2) и сквозной путь через НАСТОЯЩУЮ форму (С3). Номера там
СВОИ, снова С1–С4, поэтому функции названы с префиксом normalisation_ — в
прогоне `pytest -k c1` не имеют права смешиваться два разных «С1» из двух
разных планов. Причина, по которой этот класс дыр вообще возник: все сторожа
выше подают значения МИМО формы (подменённый CreateEntityDialog), а
нормализация живёт именно в ней — проверено правило, не проверен путь к нему.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unittest.mock import MagicMock

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

import gui.dock_hub as dock_hub_mod
import gui.docks.config_tree as config_tree_mod
import gui.docks.create_entity as create_entity_mod
from kicadstamp.config import load_config
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


# ── Общие помощники для С2–С8 ───────────────────────────────────────────


def _cells(*names) -> dict:
    """{name: <минимальная валидная ячейка>} — та же форма, что С1 пишет
    руками. Форма важна: обработчик резолвит дубликат через `load_config`, то
    есть конфиг сторожа обязан грузиться по-настоящему, а не только рисоваться
    в дереве."""
    return {name: {"components": [{"role": "R"}]} for name in names}


def _imprint(name: str = "amp", source_sheet: str = "Channel_0") -> dict:
    """Минимальная ВАЛИДНАЯ запись imprints: (без anchor — рамка записи
    берётся по центру области), та же форма, что
    tests/gui/test_imprint_place_gui.py использует для load_config."""
    return {
        "name": name,
        "source_sheet": source_sheet,
        "components": [
            {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
        ],
    }


def _open_project(hub, root) -> None:
    """Открыть проект тем же путём, что реальный GUI (File -> Open Root
    file...), и выключить staged-режим: все проверки ниже читают ФАЙЛ."""
    hub.root_metadata_dock.set_root_file(root)
    WORKING_SET.enabled = False


def _file_item(tree, path):
    """Файловый узел дерева для `path` — по payload ("file", <путь>, ...), не
    по отображаемому имени. Обход рекурсивный: включённые файлы висят детьми
    корневого файлового узла, а не отдельными верхними узлами."""
    wanted = Path(path).resolve()
    found = []

    def walk(item) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if (isinstance(data, tuple) and data and data[0] == "file"
                and Path(data[1]).resolve() == wanted):
            found.append(item)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    assert found, f"в дереве нет файлового узла для {path!r}"
    return found[0]


def _category(file_item, section):
    """Узел-категория секции — по payload ("category", <section>), НИКОГДА по
    переведённой подписи: подпись это _("Cells")/_("Imprints") и зависит от
    локали и от каталогов, то есть сторож на ней падал бы по посторонним
    причинам (ровно та причина, по которой С1 ищет сам ПУНКТ по objectName)."""
    for i in range(file_item.childCount()):
        child = file_item.child(i)
        data = child.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and data[:2] == ("category", section):
            return child
    raise AssertionError(
        f"под узлом {file_item.text(0)!r} нет категории {section!r}")


def _create_entity_action(dock, item, monkeypatch):
    """QAction "Create entity" узла — ровно один, найденный по objectName."""
    actions = _context_menu_actions(dock, item, monkeypatch)
    matching = [act for _label, act in actions
                if act.objectName() == "create_entity_action"]
    assert len(matching) == 1, (
        "нет ровно одного действия create_entity_action; видели: "
        + repr([label for label, _ in actions]))
    return matching[0]


def _click_create_entity(hub, item, monkeypatch, name, cluster, sheet) -> None:
    """Заполнить форму тремя значениями и нажать пункт меню — весь путь
    пользователя: action -> сигнал дока -> обработчик хаба."""
    action = _create_entity_action(hub.config_tree_dock, item, monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _fake_dialog(name, cluster, sheet))
    action.trigger()


def _capture_messages(monkeypatch) -> list:
    """Захват пользовательских строк хаба (тот же стаб, что в
    tests/gui/test_phase3_wiring.py): отказ обязан ГОВОРИТЬ, что случилось."""
    messages: list = []
    monkeypatch.setattr(dock_hub_mod, "show_message",
                        lambda text, style="", logger=None: messages.append(text))
    return messages


def _entities(path) -> list:
    return _load(path).get("entities") or []


def _names(path) -> list:
    return [e.get("name") for e in _entities(path)]


# ── С2 ──────────────────────────────────────────────────────────────────


def test_c2_create_entity_on_an_imprint_leaf_writes_an_imprint_entity(
        real_main_window, tmp_path, monkeypatch):
    """С2: пункт есть на узле ОТПЕЧАТКА, и его срабатывание создаёт запись
    entities: со ссылкой `imprint: "<имя записи>"` — и БЕЗ `cell:`.

    Значения полей подаются мимо формы (форма — предмет Т2а, не этого
    сторожа), поэтому вторая половина проверки — именно результат в файле.

    Мутация М2 (создавать `cell:` и на узле отпечатка) роняет сторож: запись
    получила бы `cell: "amp"`, которого в конфиге нет вовсе."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")]})
    _open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "imprints"), "amp")

    _click_create_entity(hub, leaf, monkeypatch, "amp_entity", None, "Channel_0")

    assert _names(root) == ["amp_entity"], (
        "срабатывание действия на узле отпечатка обязано создать ровно одну "
        "entities:-запись; сейчас: " + repr(_entities(root)))
    written = _entities(root)[0]
    assert written["imprint"] == "amp"
    assert written["sheet"] == "Channel_0"
    assert "cell" not in written, (
        "источник на узле отпечатка — imprint:, а не cell: (мутация М2)")


# ── С3 ──────────────────────────────────────────────────────────────────


def test_c3_imprint_entity_never_carries_cluster_refs_nets_by_selection(
        real_main_window, tmp_path, monkeypatch):
    """С3: на imprint-сущности `cluster`/`refs`/`nets`/`by_selection` (и
    `params` с `net_overrides` — тот же класс) не выставляются НИ ПРИ КАКИХ
    вводимых значениях: на ней они фатальны при загрузке (config/models.py:706).

    Форма для отпечатка поля Cluster не строит вовсе, поэтому кластер здесь
    подаётся МИМО формы — подменённым диалогом, который его возвращает.
    Гарантия не имеет права зависеть от того, что сегодняшняя форма молчит:
    раз запись фатальна, отбросить поле обязан обработчик.

    Мутация М3 (переносить кластер и на отпечаток) роняет сторож: в файле
    появляется `cluster`."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")]})
    _open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "imprints"), "amp")

    _click_create_entity(hub, leaf, monkeypatch, "amp_entity",
                         "CH_SHOULD_NOT_APPEAR", None)

    written = _entities(root)[0]
    for key in ("cluster", "refs", "nets", "net_overrides", "by_selection",
                "params"):
        assert key not in written, (
            f"на imprint-сущности {key!r} фатален при загрузке "
            f"(config/models.py:706) и не имеет права попасть в запись "
            f"(мутация М3); запись: {written!r}")

    # И сама запись обязана грузиться: конфиг с ней не должен стать фатальным.
    cfg, _ctx = load_config(str(root))
    assert [e.name for e in cfg.entities] == ["amp_entity"]


# ── С4 ──────────────────────────────────────────────────────────────────


def test_c4_name_taken_elsewhere_in_the_graph_is_refused_untouched(
        real_main_window, tmp_path, monkeypatch):
    """С4: имя обязано быть уникальным на ВЕСЬ граф include, а не на файл, в
    который пишем. Занятое имя — отказ строкой, и конфиг не тронут ВООБЩЕ.

    Устроено так, чтобы Мутация М4 (проверять только текущий файл) была
    наказуема: имя `taken` занято в КОРНЕ, а пишем мы в ВКЛЮЧЁННЫЙ файл — при
    проверке «по текущему файлу» имя выглядело бы свободным. Сравниваются
    байты обоих файлов: отказ не имеет права оставить след."""
    hub = real_main_window._dock_hub
    sub = tmp_path / "sub.sexp"
    _write(sub, {"cells": _cells("sub_cell")})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"],
                  "cells": _cells("root_cell"),
                  "entities": [{"name": "taken", "cell": "root_cell"}]})
    _open_project(hub, root)

    before_root = root.read_bytes()
    before_sub = sub.read_bytes()
    messages = _capture_messages(monkeypatch)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, sub), "cells"), "sub_cell")
    _click_create_entity(hub, leaf, monkeypatch, "taken", None, None)

    assert root.read_bytes() == before_root, "отказ не имеет права править конфиг"
    assert sub.read_bytes() == before_sub, "отказ не имеет права править конфиг"
    assert _names(sub) == [], (
        "запись не должна была появиться ни в одном файле; сейчас в sub: "
        + repr(_entities(sub)))
    assert any("taken" in m for m in messages), (
        "отказ обязан назвать занятое имя строкой; сообщения: " + repr(messages))


# ── С5 ──────────────────────────────────────────────────────────────────


def test_c5_existing_entity_for_the_same_key_wins_and_is_named(
        real_main_window, tmp_path, monkeypatch):
    """С5: подходящая сущность уже есть — вторая НЕ создаётся, а существующая
    НАЗЫВАЕТСЯ строкой. Ключ здесь (cell, cluster), поэтому форма подаёт тот
    же кластер, что и у лежащей записи.

    Мутация М5 (создавать всегда) роняет сторож: записей стало бы две."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell"),
                  "entities": [{"name": "e1", "cell": "my_cell",
                                "cluster": "CH0"}]})
    _open_project(hub, root)

    messages = _capture_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", "CH0", None)

    assert _names(root) == ["e1"], (
        "вторая сущность на тот же ключ (my_cell, CH0) создаваться не должна "
        "(мутация М5); сейчас: " + repr(_entities(root)))
    assert any("e1" in m for m in messages), (
        "человеку обязано быть сказано, КАКАЯ сущность нашлась; сообщения: "
        + repr(messages))


# ── С5а — ключ дубликата это ПАРА ───────────────────────────────────────


def test_c5a_cell_second_entity_on_the_same_cell_with_another_cluster(
        real_main_window, tmp_path, monkeypatch):
    """С5а (половина про ЯЧЕЙКУ): вторая сущность на ту же ячейку с ДРУГИМ
    кластером — законна и создаётся. Одна ячейка, два размещения.

    Мутация М5а (матчить по одному источнику, игнорируя второе поле) роняет
    сторож: обработчик отказал бы, назвав существующую."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell"),
                  "entities": [{"name": "e1", "cell": "my_cell",
                                "cluster": "CH0"}]})
    _open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", "CH1", None)

    assert sorted(_names(root)) == ["e1", "e2"], (
        "вторая сущность на (my_cell, CH1) обязана создаться — ключ это ПАРА "
        "(мутация М5а); сейчас: " + repr(_entities(root)))
    assert {"name": "e2", "cell": "my_cell", "cluster": "CH1"} in _entities(root)


def test_c5a_imprint_second_entity_for_the_same_imprint_on_another_sheet(
        real_main_window, tmp_path, monkeypatch):
    """С5а (половина про ОТПЕЧАТОК): второй сущности на тот же отпечаток
    мешает только совпадение ЦЕЛЕВОГО ЛИСТА — `sheet` на imprint-сущности это
    целевой лист twin-резолва (config/models.py:704), поэтому один отпечаток
    на двух близнецовых листах это две законные сущности.

    Мутация М5а роняет и эту половину: матч по одному `imprint` запретил бы
    вторую."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")],
                  "entities": [{"name": "e1", "imprint": "amp",
                                "sheet": "Channel_0"}]})
    _open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "imprints"), "amp")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, "Channel_1")

    assert sorted(_names(root)) == ["e1", "e2"], (
        "вторая сущность на (amp, Channel_1) обязана создаться — ключ это "
        "ПАРА (мутация М5а); сейчас: " + repr(_entities(root)))
    assert {"name": "e2", "imprint": "amp", "sheet": "Channel_1"} in _entities(root)


# ── С5б — пустое второе поле сужения не даёт ────────────────────────────


def test_c5b_cell_with_a_blank_cluster_finds_any_entity_on_the_cell(
        real_main_window, tmp_path, monkeypatch):
    """С5б (половина про ЯЧЕЙКУ): кластер не введён — сужения нет, подходит
    ЛЮБАЯ сущность на этот источник: новая не создаётся, найденная называется.
    Строже, чем могло бы быть, и намеренно (решение 20.09 в §Т3 плана):
    безымянный дубль без тега потом не отличить от оригинала — захочешь
    вторую, дай ей кластер.

    Мутация М5б (при пустом поле создавать всегда) роняет сторож."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell"),
                  "entities": [{"name": "e1", "cell": "my_cell",
                                "cluster": "CH0"}]})
    _open_project(hub, root)

    messages = _capture_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, None)

    assert _names(root) == ["e1"], (
        "при пустом кластере сужения нет — вторая сущность на ту же ячейку не "
        "создаётся (мутация М5б); сейчас: " + repr(_entities(root)))
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа; сообщения: " + repr(messages))


def test_c5b_imprint_with_a_blank_sheet_finds_any_entity_for_the_imprint(
        real_main_window, tmp_path, monkeypatch):
    """С5б (половина про ОТПЕЧАТОК): лист не введён — сужения нет, подходит
    любая сущность на этот отпечаток. Симметрично кластеру у ячейки.

    Мутация М5б роняет и эту половину."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")],
                  "entities": [{"name": "e1", "imprint": "amp",
                                "sheet": "Channel_0"}]})
    _open_project(hub, root)

    messages = _capture_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "imprints"), "amp")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, None)

    assert _names(root) == ["e1"], (
        "при пустом листе сужения нет — вторая сущность на тот же отпечаток "
        "не создаётся (мутация М5б); сейчас: " + repr(_entities(root)))
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа; сообщения: " + repr(messages))


# ── С6 ──────────────────────────────────────────────────────────────────


def test_c6_create_entity_does_not_touch_the_trees_section(
        real_main_window, tmp_path, monkeypatch):
    """С6: создание сущности НЕ добавляет узел в trees: — размещение живёт
    только в дереве и ставится только из дерева (Р22).

    Мутация М6 (ставить узел заодно) роняет сторож: секция trees: изменилась
    бы."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell"),
                  "trees": [{"name": "main", "anchor": {"origin": True},
                             "nodes": []}]})
    _open_project(hub, root)

    before_trees = _load(root).get("trees")
    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "my_entity", None, None)

    assert _names(root) == ["my_entity"], "запись обязана была появиться"
    assert _load(root).get("trees") == before_trees, (
        "создание сущности не имеет права трогать trees: (Р22; мутация М6)")


# ── С7 ──────────────────────────────────────────────────────────────────


def test_c7_create_entity_creates_no_board_adapter(
        real_main_window, tmp_path, monkeypatch):
    """С7: в этом пути нет ни одного обращения к плате. Шпион ставится на саму
    ДВЕРЬ — фабрику адаптеров и класс адаптера (правило 1 techdocs/me/door.md:
    к плате только методами адаптера, и создаются они через фабрику), причём
    ПОСЛЕ открытия проекта и до срабатывания: у проекта на открытии есть свои
    чтения, и они не предмет этого сторожа.

    Шпион ЗАПИСЫВАЕТ попытку и отдаёт безобидную подмену — он НЕ бросает
    исключение. Это не стилистика, а измеренное свойство: исключение,
    выброшенное внутрь слота Qt, глотается обработчиком исключений PyQt, слот
    считается отработавшим, и сторож, построенный на «шпионе-исключении»,
    остаётся ЗЕЛЁНЫМ на сломанном коде. Мутация М7 (прочитать плату в
    обработчике) показала это вживую, поэтому здесь проверяется ЗАПИСЬ, а не
    факт падения.

    Это же и причина, по которой сущность законно создаётся при закрытом
    KiCad: обработчик правит конфиг и только его."""
    attempts = []

    def _record_adapter(*args, **kwargs):
        attempts.append((args, kwargs))
        return MagicMock()

    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell")})
    _open_project(hub, root)

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        _record_adapter)
    monkeypatch.setattr("kicadstamp.kicad.adapter.KiCadBoardAdapter",
                        _record_adapter)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "my_entity", "CH0", None)

    assert _names(root) == ["my_entity"], (
        "создание сущности обязано пройти, не тронув плату; сейчас: "
        + repr(_entities(root)))
    assert attempts == [], (
        "создание сущности не имеет права открывать плату (мутация М7); "
        f"попытки создать адаптер: {attempts!r}")


# ── С8 ──────────────────────────────────────────────────────────────────


def test_c8_entity_lands_in_the_same_file_as_its_source(
        real_main_window, tmp_path, monkeypatch):
    """С8/Т4: сущность ложится в ТОТ ЖЕ файл, где лежит её ячейка или запись.
    Иначе перенос файла между профилями даёт висячую ссылку: запись уехала, а
    её сущность осталась в корне.

    Мутация М8 (всегда писать в корень) роняет сторож: запись появилась бы в
    root.sexp, а sub.sexp остался бы пуст."""
    hub = real_main_window._dock_hub
    sub = tmp_path / "sub.sexp"
    _write(sub, {"cells": _cells("sub_cell")})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})
    _open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = _find(_category(_file_item(tree, sub), "cells"), "sub_cell")
    _click_create_entity(hub, leaf, monkeypatch, "sub_entity", None, None)

    assert _names(sub) == ["sub_entity"], (
        "сущность обязана лечь в файл своей ячейки (sub.sexp); сейчас в sub: "
        + repr(_entities(sub)))
    assert _entities(root) == [], (
        "корневой файл не должен получить запись (мутация М8); сейчас в root: "
        + repr(_entities(root)))


# ═══════════════════════════════════════════════════════════════════════════
# Нормализация пустого поля формы — второй план дня:
# plan_2026_09_20_empty_cluster_normalisation.md (§1 Т1, сторожа С1/С2)
# ═══════════════════════════════════════════════════════════════════════════
#
# ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ. Правило «дубликатов не плодим» (Т3 плана
# create_entity_menu) работает на паре (cell, cluster) и сужено так
# (tree_from_selection.py, find_entity_for_source):
#
#     if cluster is not None and getattr(e, "cluster", None) != cluster:
#         continue
#
# То есть «поле не заполнено» обязано прийти туда как None, а не как "".
# Приводит его к None РОВНО ОДИН токен — `or None` в
# gui/docks/create_entity.py::CreateEntityDialog.result_data(). Он и есть
# предмет сторожа: приёмка Claude уронила его мутацией (CM1) и увидела, что
# ВСЕ ДВЕНАДЦАТЬ сторожей остались зелёными, потому что ни один из них не
# проходит через форму — все подают значения мимо неё. Отсюда два правила
# этого блока: значения берутся ТОЛЬКО через result_data(), и вторая половина
# (отпечаток) не должна прятать первую (ячейку).

# Форма НАСТОЯЩАЯ, зафиксированная на импорте модуля: подмены из соседних
# сторожей (monkeypatch на create_entity_mod.CreateEntityDialog) снимаются
# после каждого теста, но фиксация на импорте делает это независимым от
# порядка прогона — «форма в прогоне — та самая форма».
_RealCreateEntityDialog = create_entity_mod.CreateEntityDialog


@pytest.mark.parametrize("typed", ["", "   "])
def test_normalisation_c1_blank_cluster_on_a_cell_comes_out_as_none(
        main_window, typed):
    """С1 (нормализация, §1 Т1): через CreateEntityDialog.result_data() — не
    мимо неё — пустое поле КЛАСТЕРА у ячейки обязано выйти как None, а не как
    "".

    На этом токене держится весь механизм сужения. С "" сужение пойдёт по
    пустой строке, ни с одной существующей сущностью не совпадёт — и на ту же
    ячейку родится ВТОРАЯ сущность, хотя Т3 обещает обратное; в запись при
    этом кластер не попадёт (`if source_field == "cell" and cluster:` — "" ложна),
    то есть рядом с размеченной ляжет безымянная. Ровно тот случай, который
    задание называло «потом не отличить».

    Мутация М1 (убрать `or None` у кластера, докстринг не трогать) роняет
    сторож: на выходе "".

    Обе формы пустоты проверяются параметром: совсем пустое поле и поле из
    пробелов. Пробелы снимает .strip(), и дальше работает ровно тот же токен —
    то есть это не второй случай, а второе написание того же случая.
    """
    dlg = _RealCreateEntityDialog(main_window, "cell", "my_cell", [])
    assert dlg._cluster_edit is not None, (
        "у ячейки поле Cluster обязано быть — иначе сторожа нормализации "
        "проверяли бы не тот путь")
    dlg._cluster_edit.setText(typed)
    dlg._sheet_edit.clear()  # Т1: пусты оба поля

    name, cluster, sheet = dlg.result_data()

    assert name == "my_cell", (
        f"имя берётся из поля источника и правится: получили {name!r}")
    assert cluster is None, (
        "пустое поле кластера обязано выйти из result_data() как None, а не "
        "как пустая строка: сужение в find_entity_for_source работает на "
        "`cluster is not None`, и с \"\" вторая сущность на ту же ячейку "
        f"создастся (мутация М1); получили: {cluster!r}")
    assert sheet is None, (
        "пустое поле листа — тоже None, не пустая строка (мутация М2); "
        f"получили: {sheet!r}")


@pytest.mark.parametrize("typed", ["", "   "])
def test_normalisation_c2_blank_sheet_on_an_imprint_comes_out_as_none(
        main_window, typed):
    """С2 (нормализация, §1 Т1) — вторая половина, про ОТПЕЧАТОК, отдельной
    функцией: падение одной половины не должно прятать состояние другой
    (приём тот же, что у С5а/С5б).

    У imprint-сущности кластера нет вовсе — поля не строятся (Т2а плана
    create_entity_menu: не предлагать, а не предлагать и отбрасывать), и
    второе ключевое поле пары — `sheet`, целевой лист twin-резолва
    (config/models.py:704). Пустой лист обязан выйти как None: тем же токеном
    держится сужение пары (imprint, sheet), и с "" один отпечаток на двух
    близнецовых листах слился бы в одну сущность — или наоборот, второй лист
    перестал бы находить первый.

    Мутация М2 (убрать `or None` у листа) роняет сторож: на выходе "".
    """
    dlg = _RealCreateEntityDialog(main_window, "imprint", "amp", [])
    assert dlg._cluster_edit is None, (
        "у отпечатка поля Cluster нет вовсе (Т2а): не предлагать, а не "
        "предлагать и отбрасывать")
    assert dlg._sheet_edit is not None, "поле Sheet у отпечатка обязано быть"
    dlg._sheet_edit.setText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "amp", (
        f"имя предзаполняется именем записи: получили {name!r}")
    assert cluster is None, (
        "кластер на imprint-сущности фатален при загрузке (models.py:706) и "
        f"обязан быть None: получили {cluster!r}")
    assert sheet is None, (
        "пустое поле листа обязано выйти из result_data() как None, а не как "
        "пустая строка: сужение пары (imprint, sheet) работает на "
        f"`sheet is not None` (мутация М2); получили: {sheet!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Сквозной путь от формы — тот же план, §1 Т2 (сторож С3, мутация М3 = М1)
# ═══════════════════════════════════════════════════════════════════════════
#
# С5б плана create_entity_menu проверяет ФУНКЦИЮ: он подаёт cluster=None прямо
# в обработчик и видит, что вторая сущность не создаётся. Это правило, а не
# путь к нему: нормализация живёт в форме, и в С5б её нет вовсе. Здесь идёт
# ПОЛНЫЙ путь пользователя — настоящий CreateEntityDialog строит поля, его
# собственный result_data() отдаёт значения, обработчик хаба принимает
# решение. Ровно этот сторож обязана убивать мутация М1 (`or None` у кластера):
# с "" сужение пойдёт по пустой строке, ни с чем не совпадёт, и на ту же
# ячейку родится вторая — безымянная рядом с размеченной.

def _accepted_real_form(*, name=None, cluster=None, sheet=None):
    """Класс НАСТОЯЩЕЙ формы, которая «нажала OK». По умолчанию поля остаются
    такими, как их построил диалог (имя — имя источника, кластер и лист
    пусты); переданное сюда — то, что человек напечатал. accept() вызывается
    по-настоящему, поэтому валидация имени внутри диалога тоже отрабатывает —
    это не обход формы, а её принятие без показа окна."""
    class _AcceptedRealForm(_RealCreateEntityDialog):
        def exec(self):
            if name is not None:
                self._name_edit.setText(name)
            if cluster is not None:
                assert self._cluster_edit is not None, (
                    "кластер передан форме отпечатка, у которой поля Cluster "
                    "нет вовсе (Т2а)")
                self._cluster_edit.setText(cluster)
            if sheet is not None:
                self._sheet_edit.setText(sheet)
            self.accept()  # the user's OK: validation still runs
            return self.result()

    return _AcceptedRealForm


def test_normalisation_c3_blank_cluster_through_the_real_form_finds_the_entity(
        real_main_window, tmp_path, monkeypatch):
    """С3 (сквозной, §1 Т2): сущность на (my_cell, CH0) уже есть, пользователь
    открывает «Создать сущность» на ТОЙ ЖЕ ячейке и оставляет кластер пустым —
    вторая сущность НЕ создаётся, а найденная НАЗЫВАЕТСЯ человеку.

    Значения берутся от настоящей формы: фейка-диалога здесь нет, вместо него
    подкласс настоящего CreateEntityDialog, который просто нажимает OK. Всё
    остальное — как у пользователя: пункт меню -> сигнал дока -> обработчик
    хаба (find_entity_for_source).

    Мутация М3 (= М1, убрать `or None` у кластера) роняет этот сторож: кластер
    придёт как "", сужение не найдёт e1 (у неё CH0), и на my_cell появится
    вторая сущность — конфиг перестанет совпадать с собой до срабатывания.

    Конфиг сравнивается ПО БАЙТАМ: отказ не имеет права оставить след."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell"),
                  "entities": [{"name": "e1", "cell": "my_cell",
                                "cluster": "CH0"}]})
    _open_project(hub, root)
    before = root.read_bytes()

    action = _create_entity_action(
        hub.config_tree_dock,
        _find(_category(_file_item(hub.config_tree_dock.tree, root), "cells"),
              "my_cell"),
        monkeypatch)
    messages = _capture_messages(monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _accepted_real_form())

    action.trigger()

    assert _names(root) == ["e1"], (
        "пустой кластер в форме — сужения нет, значит подходит ЛЮБАЯ сущность "
        "на эту ячейку: вторая не создаётся (мутация М3 = М1); сейчас: "
        + repr(_entities(root)))
    assert root.read_bytes() == before, (
        "отказ не имеет права править конфиг — ни одной строки")
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа человеку; сообщения: "
        + repr(messages))


def test_normalisation_c3_blank_sheet_through_the_real_form_finds_the_entity(
        real_main_window, tmp_path, monkeypatch):
    """С3, половина про ОТПЕЧАТОК (сверх таблицы §2, но того же класса: тот же
    «путь, а не правило», только вторым ключевым полем пары служит sheet — у
    imprint-сущности кластера нет вовсе, Т2а плана create_entity_menu).

    Сущность на (amp, Channel_0) уже есть, пользователь оставляет лист пустым —
    вторая не создаётся, найденная названа. Падение этой половины не прячет
    состояние половины про ячейку: это отдельная функция.

    Мутация М2 (`or None` у листа) роняет её: sheet придёт как "", сужение
    пары (imprint, sheet) не найдёт e1."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")],
                  "entities": [{"name": "e1", "imprint": "amp",
                                "sheet": "Channel_0"}]})
    _open_project(hub, root)
    before = root.read_bytes()

    action = _create_entity_action(
        hub.config_tree_dock,
        _find(_category(_file_item(hub.config_tree_dock.tree, root),
                        "imprints"), "amp"),
        monkeypatch)
    messages = _capture_messages(monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _accepted_real_form())

    action.trigger()

    assert _names(root) == ["e1"], (
        "пустой лист в форме — сужения нет, значит подходит любая сущность на "
        "этот отпечаток: вторая не создаётся (мутация М2); сейчас: "
        + repr(_entities(root)))
    assert root.read_bytes() == before, (
        "отказ не имеет права править конфиг — ни одной строки")
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа человеку; сообщения: "
        + repr(messages))


# ═══════════════════════════════════════════════════════════════════════════
# Обратная половина того же пути: ЗАПОЛНЕННОЕ поле обязано доехать до записи
# ═══════════════════════════════════════════════════════════════════════════
#
# ЗАЧЕМ. Сторожа С1–С3 держат только пустое поле: «"" → None». Обратное
# направление — «напечатанное доехало до записи» — не держал НИ ОДИН сторож, и
# это измерено, а не выведено: 20.09.2026, 18:18, в рабочем дереве на минуту
# оказалась правка `cluster = None` (форма вообще не читает кластер — не пустой,
# а любой), и полный прогон этого файла дал **18 passed, 0 failed**. Причина та
# же, что в §0 плана: двенадцать прежних сторожей подают значения МИМО формы
# (подменённый диалог), а новые держат только пустоту. То есть человек,
# напечатавший кластер, мог молча получить сущность без кластера — и её нельзя
# было бы клонировать по кластеру; ни одна проверка бы не пикнула.
#
# Две отдельные функции (ячейка/отпечаток) — по тому же правилу, что С5а/С5б:
# падение одной половины не должно прятать состояние другой.

def test_normalisation_extra_filled_cluster_through_the_real_form_reaches_the_record(
        real_main_window, tmp_path, monkeypatch):
    """Сверх таблицы §2, но того же класса. Сущностей на (my_cell, CH1) нет,
    человек открывает «Создать сущность» на my_cell и ПЕЧАТАЕТ кластер CH1 —
    в записи обязаны оказаться и cell:, и cluster: CH1.

    Роняют сторож обе правки формы, которые оставляют его зелёным сегодня:
      * `cluster = None` (поле не читается вовсе — тот самый замер 18:18);
      * мутация М1 (`or None` убран): с "" запись создалась бы, но
        `if source_field == "cell" and cluster:` выбросил бы кластер из неё.
    Ни один из двенадцати прежних сторожей этих правок не видит."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"cells": _cells("my_cell")})
    _open_project(hub, root)

    action = _create_entity_action(
        hub.config_tree_dock,
        _find(_category(_file_item(hub.config_tree_dock.tree, root), "cells"),
              "my_cell"),
        monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _accepted_real_form(cluster="CH1"))

    action.trigger()

    assert _entities(root) == [{"name": "my_cell", "cell": "my_cell",
                                "cluster": "CH1"}], (
        "то, что человек напечатал в поле Cluster, обязано доехать до записи "
        "как есть — иначе сущность нельзя клонировать по кластеру, и никто об "
        "этом не скажет; сейчас: " + repr(_entities(root)))


def test_normalisation_extra_filled_sheet_through_the_real_form_reaches_the_record(
        real_main_window, tmp_path, monkeypatch):
    """Вторая половина: отпечаток, поле Sheet ЗАПОЛНЕНО (Channel_1 — целевой
    лист twin-резолва, config/models.py:704). В записи обязаны быть imprint: и
    sheet: Channel_1.

    Роняет сторож правка `sheet = None` — тот же класс, что `cluster = None`
    выше, только для второго ключевого поля пары."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    _write(root, {"imprints": [_imprint("amp")]})
    _open_project(hub, root)

    action = _create_entity_action(
        hub.config_tree_dock,
        _find(_category(_file_item(hub.config_tree_dock.tree, root),
                        "imprints"), "amp"),
        monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _accepted_real_form(sheet="Channel_1"))

    action.trigger()

    assert _entities(root) == [{"name": "amp", "imprint": "amp",
                                "sheet": "Channel_1"}], (
        "напечатанный лист обязан доехать до записи — это целевой лист "
        "twin-резолва, без него сущность клонируется не на тот лист; сейчас: "
        + repr(_entities(root)))
