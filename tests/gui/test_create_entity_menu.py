# tests/gui/test_create_entity_menu.py
"""Сторожа МЕНЮ и ЗАПИСИ «Создать сущность» — слой, который идёт через
обработчик хаба до байтов конфига.

Заходы: plan_2026_09_20_create_entity_menu.md (меню и обработчик хаба) и
plan_2026_09_20_empty_cluster_normalisation.md / plan_2026_09_21_typed_value_
survives_the_form.md (их сквозные сторожа «через настоящую форму до записи»).
Заход plan_2026_09_21_guard_file_by_property.md разделил файл по слоям: всё,
что читает САМУ форму (result_data(), accept(), _validate), переехало в
tests/gui/test_create_entity_form.py; здесь остались меню, обработчик хаба,
запись в конфиг, дубликаты, «адаптер не создаётся», «trees: не тронут»,
«запись легла в тот же файл» и сквозные сторожа нормализации.

Имена функций описывают СВОЙСТВО и не несут номера плана (правило 37): номер
С-сторожа и имя плана названы в докстринге каждой функции. Прежний порядок был
хронологическим (С5, С6, С7, С8 раньше С3, С4 — в конце); теперь он логический:
сначала «пункт меню пишет запись», потом дубликаты и ключ пары, потом
«не трогать деревья/плату» и «файл записи», и только затем сквозные сторожа
нормализации.

ПРАВИЛО, КОТОРОЕ ЗДЕСЬ ГЛАВНОЕ (см. С1 плана create_entity_menu). Сторож
смотрит на РЕЗУЛЬТАТ — состояние конфига ПОСЛЕ срабатывания действия, — а не
на оформление. В частности:

  * пункт меню ищется ПО СИГНАЛУ (add_entity_requested), а не по переведённой
    подписи: QAction-якорь берётся по objectName "create_entity_action" (это
    нелокализованный идентификатор, установленный в config_tree.py РЯДОМ с тем
    же `add_entity_requested.emit(...)`), и ДОПОЛНИТЕЛЬНО сторож проверяет
    шпионом на сигнале, что найденный QAction эмитит ИМЕННО его;
  * у срабатывания проверяется запись в entities:, а не строки Log, не заголовки
    окон и не порядок кнопок.

Почему тесты идут через real_main_window, а не через ConfigTreeDock напрямую:
обработчик живёт в DockHub (gui/dock_hub.py::_create_entity_from_tree) — он
читает root_path у root_metadata_dock и пишет в файл через config_writer. Один
и тот же путь, что в реальной GUI: клик по action -> сигнал дока -> слот хаба.
Если обойти хаб, сторож перестанет ловить расхождения между доком и
обработчиком (а именно эти расхождения он и охраняет).

Общие строители — в tests/gui/create_entity_helpers.py (Т1: по копии на слой не
держим).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unittest.mock import MagicMock

from PyQt6.QtWidgets import QDialog

import gui.docks.create_entity as create_entity_mod
from kicadstamp.config import load_config

from tests.gui.create_entity_helpers import (
    RealCreateEntityDialog,
    accepted_real_form,
    capture_hub_messages,
    capture_warnings,
    category,
    context_menu_actions,
    create_entity_action,
    entities_of,
    file_item,
    find_child,
    minimal_cells,
    minimal_imprint,
    names_of,
    open_project,
    write_config,
)


def _fake_dialog(name, cluster, sheet):
    """Подмена CreateEntityDialog — форма в этих сторожах не проверяется (у
    неё свой предмет в tests/gui/test_create_entity_form.py), а нужно ровно
    одно: result_data() и то, что диалог был «принят». Сохраняем и аргументы
    конструктора — они ниже проверяются там, где это важно."""
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


def _click_create_entity(hub, item, monkeypatch, name, cluster, sheet) -> None:
    """Заполнить форму тремя значениями и нажать пункт меню — весь путь
    пользователя: action -> сигнал дока -> обработчик хаба."""
    action = create_entity_action(hub.config_tree_dock, item, monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        _fake_dialog(name, cluster, sheet))
    action.trigger()


# ═══════════════════════════════════════════════════════════════════════════
# Пункт меню пишет запись (С1, С2) и не тащит на отпечаток поля ячейки (С3)
# ═══════════════════════════════════════════════════════════════════════════

def test_create_entity_on_a_cell_leaf_writes_a_cell_entity(
        real_main_window, tmp_path, monkeypatch):
    """С1 плана create_entity_menu: пункт есть на узле ЯЧЕЙКИ и его
    срабатывание создаёт запись entities: с `cell: "<имя ячейки>"`.
    Проверяется РЕЗУЛЬТАТ, а не оформление: смотрим в ФАЙЛ конфига после
    срабатывания.

    Требование §3: пункт меню искать ПО СИГНАЛУ, а не по переведённой подписи.
    Здесь это сделано двумя шагами:
      1) QAction-якорь берём по objectName "create_entity_action" — это
         нелокализованный идентификатор, установленный в config_tree.py рядом с
         `add_entity_requested.emit(...)`, т.е. «сигнальная сторона» пункта;
      2) на сигнал add_entity_requested ставим шпиона и явно проверяем, что
         найденный QAction эмитит ИМЕННО его (payload совпадает с
         ("cell", "my_cell", <путь к файлу>)).

    Мутация М1, от которой этот сторож защищает: оставить пункт только у
    отпечатка (тогда на узле ячейки действия нет).
    """
    hub = real_main_window._dock_hub
    target = tmp_path / "root.sexp"
    write_config(target, {"cells": {"my_cell": {"components": [{"role": "R"}]}}})
    open_project(hub, target)

    tree = hub.config_tree_dock.tree
    leaf = find_child(find_child(tree.topLevelItem(0), "Cells"), "my_cell")

    actions = context_menu_actions(hub.config_tree_dock, leaf, monkeypatch)
    matching = [(text, act) for text, act in actions
                if act.objectName() == "create_entity_action"]
    assert len(matching) == 1, (
        "в контекстном меню узла ячейки нет ровно одного действия "
        "create_entity_action — ищем по objectName, а не по подписи; "
        "видели: " + repr([t for t, _ in actions]))
    _label, action = matching[0]

    # Сторож привязан к СИГНАЛУ, а не к подписи: наблюдаем payload, который
    # уйдёт в обработчик, и сверяем его с ожидаемым.
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

    entities = entities_of(target)
    written = [e for e in entities if e.get("cell") == "my_cell"]
    assert len(written) == 1, (
        "срабатывание действия на узле ячейки должно было создать ровно "
        "одну entities:-запись с cell: 'my_cell'; сейчас entities: "
        + repr(entities))
    assert written[0]["name"] == "buf_entity"
    assert written[0]["cluster"] == "CH0"
    assert written[0]["sheet"] == "Sheet_1"
    assert "imprint" not in written[0]


def test_create_entity_on_an_imprint_leaf_writes_an_imprint_entity(
        real_main_window, tmp_path, monkeypatch):
    """С2 плана create_entity_menu: пункт есть на узле ОТПЕЧАТКА, и его
    срабатывание создаёт запись entities: со ссылкой `imprint: "<имя записи>"`
    — и БЕЗ `cell:`.

    Значения полей подаются мимо формы (форма — предмет файла
    test_create_entity_form.py, не этого сторожа), поэтому вторая половина
    проверки — именно результат в файле.

    Мутация М2 (создавать `cell:` и на узле отпечатка) роняет сторож: запись
    получила бы `cell: "amp"`, которого в конфиге нет вовсе.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "imprints"), "amp")

    _click_create_entity(hub, leaf, monkeypatch, "amp_entity", None, "Channel_0")

    assert names_of(root) == ["amp_entity"], (
        "срабатывание действия на узле отпечатка обязано создать ровно одну "
        "entities:-запись; сейчас: " + repr(entities_of(root)))
    written = entities_of(root)[0]
    assert written["imprint"] == "amp"
    assert written["sheet"] == "Channel_0"
    assert "cell" not in written, (
        "источник на узле отпечатка — imprint:, а не cell: (мутация М2)")


def test_an_imprint_entity_never_carries_cluster_refs_nets_or_by_selection(
        real_main_window, tmp_path, monkeypatch):
    """С3 плана create_entity_menu: на imprint-сущности `cluster`/`refs`/`nets`/
    `by_selection` (и `params` с `net_overrides` — тот же класс) не выставляются
    НИ ПРИ КАКИХ вводимых значениях: на ней они фатальны при загрузке
    (config/models.py:706).

    Форма для отпечатка поля Cluster не строит вовсе, поэтому кластер здесь
    подаётся МИМО формы — подменённым диалогом, который его возвращает.
    Гарантия не имеет права зависеть от того, что сегодняшняя форма молчит:
    раз запись фатальна, отбросить поле обязан обработчик.

    Мутация М3 (переносить кластер и на отпечаток) роняет сторож: в файле
    появляется `cluster`.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "imprints"), "amp")

    _click_create_entity(hub, leaf, monkeypatch, "amp_entity",
                         "CH_SHOULD_NOT_APPEAR", None)

    written = entities_of(root)[0]
    for key in ("cluster", "refs", "nets", "net_overrides", "by_selection",
                "params"):
        assert key not in written, (
            f"на imprint-сущности {key!r} фатален при загрузке "
            f"(config/models.py:706) и не имеет права попасть в запись "
            f"(мутация М3); запись: {written!r}")

    # И сама запись обязана грузиться: конфиг с ней не должен стать фатальным.
    cfg, _ctx = load_config(str(root))
    assert [e.name for e in cfg.entities] == ["amp_entity"]


# ═══════════════════════════════════════════════════════════════════════════
# Дубликаты: имя на весь граф (С4) и ключ пары (С5/С5а/С5б)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_name_taken_elsewhere_in_the_graph_is_refused_untouched(
        real_main_window, tmp_path, monkeypatch):
    """С4 плана create_entity_menu: имя обязано быть уникальным на ВЕСЬ граф
    include, а не на файл, в который пишем. Занятое имя — отказ строкой, и
    конфиг не тронут ВООБЩЕ.

    Устроено так, чтобы мутация М4 (проверять только текущий файл) была
    наказуема: имя `taken` занято в КОРНЕ, а пишем мы в ВКЛЮЧЁННЫЙ файл — при
    проверке «по текущему файлу» имя выглядело бы свободным. Сравниваются
    байты обоих файлов: отказ не имеет права оставить след.
    """
    hub = real_main_window._dock_hub
    sub = tmp_path / "sub.sexp"
    write_config(sub, {"cells": minimal_cells("sub_cell")})
    root = tmp_path / "root.sexp"
    write_config(root, {"include": ["sub.sexp"],
                        "cells": minimal_cells("root_cell"),
                        "entities": [{"name": "taken", "cell": "root_cell"}]})
    open_project(hub, root)

    before_root = root.read_bytes()
    before_sub = sub.read_bytes()
    messages = capture_hub_messages(monkeypatch)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, sub), "cells"), "sub_cell")
    _click_create_entity(hub, leaf, monkeypatch, "taken", None, None)

    assert root.read_bytes() == before_root, "отказ не имеет права править конфиг"
    assert sub.read_bytes() == before_sub, "отказ не имеет права править конфиг"
    assert names_of(sub) == [], (
        "запись не должна была появиться ни в одном файле; сейчас в sub: "
        + repr(entities_of(sub)))
    assert any("taken" in m for m in messages), (
        "отказ обязан назвать занятое имя строкой; сообщения: " + repr(messages))


def test_an_existing_entity_for_the_same_key_wins_and_is_named(
        real_main_window, tmp_path, monkeypatch):
    """С5 плана create_entity_menu: подходящая сущность уже есть — вторая НЕ
    создаётся, а существующая НАЗЫВАЕТСЯ строкой. Ключ здесь (cell, cluster),
    поэтому форма подаёт тот же кластер, что и у лежащей записи.

    Мутация М5 (создавать всегда) роняет сторож: записей стало бы две.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell"),
                        "entities": [{"name": "e1", "cell": "my_cell",
                                      "cluster": "CH0"}]})
    open_project(hub, root)

    messages = capture_hub_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", "CH0", None)

    assert names_of(root) == ["e1"], (
        "вторая сущность на тот же ключ (my_cell, CH0) создаваться не должна "
        "(мутация М5); сейчас: " + repr(entities_of(root)))
    assert any("e1" in m for m in messages), (
        "человеку обязано быть сказано, КАКАЯ сущность нашлась; сообщения: "
        + repr(messages))


def test_a_second_entity_on_the_same_cell_with_another_cluster_is_created(
        real_main_window, tmp_path, monkeypatch):
    """С5а (половина про ЯЧЕЙКУ) плана create_entity_menu: вторая сущность на
    ту же ячейку с ДРУГИМ кластером — законна и создаётся. Одна ячейка, два
    размещения.

    Мутация М5а (матчить по одному источнику, игнорируя второе поле) роняет
    сторож: обработчик отказал бы, назвав существующую.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell"),
                        "entities": [{"name": "e1", "cell": "my_cell",
                                      "cluster": "CH0"}]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", "CH1", None)

    assert sorted(names_of(root)) == ["e1", "e2"], (
        "вторая сущность на (my_cell, CH1) обязана создаться — ключ это ПАРА "
        "(мутация М5а); сейчас: " + repr(entities_of(root)))
    assert {"name": "e2", "cell": "my_cell", "cluster": "CH1"} in entities_of(root)


def test_a_second_entity_for_the_same_imprint_on_another_sheet_is_created(
        real_main_window, tmp_path, monkeypatch):
    """С5а (половина про ОТПЕЧАТОК) плана create_entity_menu: второй сущности
    на тот же отпечаток мешает только совпадение ЦЕЛЕВОГО ЛИСТА — `sheet` на
    imprint-сущности это целевой лист twin-резолва (config/models.py:704),
    поэтому один отпечаток на двух близнецовых листах это две законные
    сущности.

    Мутация М5а роняет и эту половину: матч по одному `imprint` запретил бы
    вторую.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")],
                        "entities": [{"name": "e1", "imprint": "amp",
                                      "sheet": "Channel_0"}]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "imprints"), "amp")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, "Channel_1")

    assert sorted(names_of(root)) == ["e1", "e2"], (
        "вторая сущность на (amp, Channel_1) обязана создаться — ключ это "
        "ПАРА (мутация М5а); сейчас: " + repr(entities_of(root)))
    assert {"name": "e2", "imprint": "amp",
            "sheet": "Channel_1"} in entities_of(root)


def test_a_blank_cluster_finds_any_entity_on_the_cell(
        real_main_window, tmp_path, monkeypatch):
    """С5б (половина про ЯЧЕЙКУ) плана create_entity_menu: кластер не введён —
    сужения нет, подходит ЛЮБАЯ сущность на этот источник: новая не создаётся,
    найденная называется. Строже, чем могло бы быть, и намеренно (решение
    20.09 в §Т3 плана): безымянный дубль без тега потом не отличить от
    оригинала — захочешь вторую, дай ей кластер.

    Мутация М5б (при пустом поле создавать всегда) роняет сторож.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell"),
                        "entities": [{"name": "e1", "cell": "my_cell",
                                      "cluster": "CH0"}]})
    open_project(hub, root)

    messages = capture_hub_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, None)

    assert names_of(root) == ["e1"], (
        "при пустом кластере сужения нет — вторая сущность на ту же ячейку не "
        "создаётся (мутация М5б); сейчас: " + repr(entities_of(root)))
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа; сообщения: " + repr(messages))


def test_a_blank_sheet_finds_any_entity_for_the_imprint(
        real_main_window, tmp_path, monkeypatch):
    """С5б (половина про ОТПЕЧАТОК) плана create_entity_menu: лист не введён —
    сужения нет, подходит любая сущность на этот отпечаток. Симметрично
    кластеру у ячейки.

    Мутация М5б роняет и эту половину.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")],
                        "entities": [{"name": "e1", "imprint": "amp",
                                      "sheet": "Channel_0"}]})
    open_project(hub, root)

    messages = capture_hub_messages(monkeypatch)
    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "imprints"), "amp")
    _click_create_entity(hub, leaf, monkeypatch, "e2", None, None)

    assert names_of(root) == ["e1"], (
        "при пустом листе сужения нет — вторая сущность на тот же отпечаток "
        "не создаётся (мутация М5б); сейчас: " + repr(entities_of(root)))
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа; сообщения: " + repr(messages))


# ═══════════════════════════════════════════════════════════════════════════
# Что действие НЕ трогает: деревья (С6), плату (С7), и куда оно пишет (С8)
# ═══════════════════════════════════════════════════════════════════════════

def test_create_entity_does_not_touch_the_trees_section(
        real_main_window, tmp_path, monkeypatch):
    """С6 плана create_entity_menu: создание сущности НЕ добавляет узел в
    trees: — размещение живёт только в дереве и ставится только из дерева
    (Р22).

    Мутация М6 (ставить узел заодно) роняет сторож: секция trees: изменилась
    бы.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell"),
                        "trees": [{"name": "main", "anchor": {"origin": True},
                                   "nodes": []}]})
    open_project(hub, root)

    from tests.gui.create_entity_helpers import load_config_data
    before_trees = load_config_data(root).get("trees")
    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "my_entity", None, None)

    assert names_of(root) == ["my_entity"], "запись обязана была появиться"
    assert load_config_data(root).get("trees") == before_trees, (
        "создание сущности не имеет права трогать trees: (Р22; мутация М6)")


def test_create_entity_creates_no_board_adapter(
        real_main_window, tmp_path, monkeypatch):
    """С7 плана create_entity_menu: в этом пути нет ни одного обращения к
    плате. Шпион ставится на саму ДВЕРЬ — фабрику адаптеров и класс адаптера
    (правило 1 techdocs/me/door.md: к плате только методами адаптера, и
    создаются они через фабрику), причём ПОСЛЕ открытия проекта и до
    срабатывания: у проекта на открытии есть свои чтения, и они не предмет
    этого сторожа.

    Шпион ЗАПИСЫВАЕТ попытку и отдаёт безобидную подмену — он НЕ бросает
    исключение. Это не стилистика, а измеренное свойство: исключение,
    выброшенное внутрь слота Qt, глотается обработчиком исключений PyQt, слот
    считается отработавшим, и сторож, построенный на «шпионе-исключении»,
    остаётся ЗЕЛЁНЫМ на сломанном коде. Мутация М7 (прочитать плату в
    обработчике) показала это вживую, поэтому здесь проверяется ЗАПИСЬ, а не
    факт падения.

    Это же и причина, по которой сущность законно создаётся при закрытом
    KiCad: обработчик правит конфиг и только его.
    """
    attempts = []

    def _record_adapter(*args, **kwargs):
        attempts.append((args, kwargs))
        return MagicMock()

    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell")})
    open_project(hub, root)

    monkeypatch.setattr("kicadstamp.adapter_factory.create_board_adapter",
                        _record_adapter)
    monkeypatch.setattr("kicadstamp.kicad.adapter.KiCadBoardAdapter",
                        _record_adapter)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, root), "cells"), "my_cell")
    _click_create_entity(hub, leaf, monkeypatch, "my_entity", "CH0", None)

    assert names_of(root) == ["my_entity"], (
        "создание сущности обязано пройти, не тронув плату; сейчас: "
        + repr(entities_of(root)))
    assert attempts == [], (
        "создание сущности не имеет права открывать плату (мутация М7); "
        f"попытки создать адаптер: {attempts!r}")


def test_an_entity_lands_in_the_same_file_as_its_source(
        real_main_window, tmp_path, monkeypatch):
    """С8/Т4 плана create_entity_menu: сущность ложится в ТОТ ЖЕ файл, где
    лежит её ячейка или запись. Иначе перенос файла между профилями даёт
    висячую ссылку: запись уехала, а её сущность осталась в корне.

    Мутация М8 (всегда писать в корень) роняет сторож: запись появилась бы в
    root.sexp, а sub.sexp остался бы пуст.
    """
    hub = real_main_window._dock_hub
    sub = tmp_path / "sub.sexp"
    write_config(sub, {"cells": minimal_cells("sub_cell")})
    root = tmp_path / "root.sexp"
    write_config(root, {"include": ["sub.sexp"]})
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    leaf = find_child(category(file_item(tree, sub), "cells"), "sub_cell")
    _click_create_entity(hub, leaf, monkeypatch, "sub_entity", None, None)

    assert names_of(sub) == ["sub_entity"], (
        "сущность обязана лечь в файл своей ячейки (sub.sexp); сейчас в sub: "
        + repr(entities_of(sub)))
    assert entities_of(root) == [], (
        "корневой файл не должен получить запись (мутация М8); сейчас в root: "
        + repr(entities_of(root)))


# ═══════════════════════════════════════════════════════════════════════════
# Сквозные сторожа нормализации: от НАСТОЯЩЕЙ формы до байтов конфига.
# Планы empty_cluster_normalisation (С3, С4) и typed_value_survives_the_form.
# ═══════════════════════════════════════════════════════════════════════════
#
# ПОЧЕМУ ОНИ ЗДЕСЬ, А НЕ В ФАЙЛЕ ФОРМЫ. Их предмет — не выход result_data(), а
# РЕШЕНИЕ ОБРАБОТЧИКА и ЗАПИСЬ: «вторая сущность не создалась, найденная
# названа, конфиг совпал по байтам». Правило сужения живёт в форме, и эти
# сторожа идут ПОЛНЫМ путём пользователя (настоящий CreateEntityDialog ->
# сигнал дока -> find_entity_for_source -> файл), а не мимо формы.

def test_a_blank_cluster_through_the_real_form_finds_the_entity(
        real_main_window, tmp_path, monkeypatch):
    """С3 плана empty_cluster_normalisation: сущность на (my_cell, CH0) уже
    есть, пользователь открывает «Создать сущность» на ТОЙ ЖЕ ячейке и
    оставляет кластер пустым — вторая сущность НЕ создаётся, а найденная
    НАЗЫВАЕТСЯ человеку.

    Значения берутся от настоящей формы: фейка-диалога здесь нет, вместо него
    подкласс настоящего CreateEntityDialog, который просто нажимает OK.

    Мутация М1 (убрать `or None` у кластера) роняет этот сторож: кластер
    придёт как "", сужение не найдёт e1 (у неё CH0), и на my_cell появится
    вторая сущность — конфиг перестанет совпадать с собой до срабатывания.
    Конфиг сравнивается ПО БАЙТАМ: отказ не имеет права оставить след.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell"),
                        "entities": [{"name": "e1", "cell": "my_cell",
                                      "cluster": "CH0"}]})
    open_project(hub, root)
    before = root.read_bytes()

    action = create_entity_action(
        hub.config_tree_dock,
        find_child(category(file_item(hub.config_tree_dock.tree, root),
                            "cells"), "my_cell"),
        monkeypatch)
    messages = capture_hub_messages(monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        accepted_real_form())

    action.trigger()

    assert names_of(root) == ["e1"], (
        "пустой кластер в форме — сужения нет, значит подходит ЛЮБАЯ сущность "
        "на эту ячейку: вторая не создаётся (мутация М1); сейчас: "
        + repr(entities_of(root)))
    assert root.read_bytes() == before, (
        "отказ не имеет права править конфиг — ни одной строки")
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа человеку; сообщения: "
        + repr(messages))


def test_a_blank_sheet_through_the_real_form_finds_the_entity(
        real_main_window, tmp_path, monkeypatch):
    """С3 плана empty_cluster_normalisation, половина про ОТПЕЧАТОК: сущность
    на (amp, Channel_0) уже есть, пользователь оставляет лист пустым — вторая
    не создаётся, найденная названа. Падение этой половины не прячет состояние
    половины про ячейку: это отдельная функция.

    Мутация М2 (`or None` у листа) роняет её: sheet придёт как "", сужение
    пары (imprint, sheet) не найдёт e1.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")],
                        "entities": [{"name": "e1", "imprint": "amp",
                                      "sheet": "Channel_0"}]})
    open_project(hub, root)
    before = root.read_bytes()

    action = create_entity_action(
        hub.config_tree_dock,
        find_child(category(file_item(hub.config_tree_dock.tree, root),
                            "imprints"), "amp"),
        monkeypatch)
    messages = capture_hub_messages(monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        accepted_real_form())

    action.trigger()

    assert names_of(root) == ["e1"], (
        "пустой лист в форме — сужения нет, значит подходит любая сущность на "
        "этот отпечаток: вторая не создаётся (мутация М2); сейчас: "
        + repr(entities_of(root)))
    assert root.read_bytes() == before, (
        "отказ не имеет права править конфиг — ни одной строки")
    assert any("e1" in m for m in messages), (
        "найденная сущность обязана быть названа человеку; сообщения: "
        + repr(messages))


def test_a_typed_cluster_through_the_real_form_reaches_the_record(
        real_main_window, tmp_path, monkeypatch):
    """Сверх таблицы плана empty_cluster_normalisation, но того же класса:
    сущностей на (my_cell, CH1) нет, человек открывает «Создать сущность» на
    my_cell и ПЕЧАТАЕТ кластер CH1 — в записи обязаны оказаться и cell:, и
    cluster: CH1.

    Роняют сторож обе правки формы, которые оставляли его зелёным:
      * `cluster = None` (поле не читается вовсе — замер 20.09, 18:18);
      * мутация М5 (безусловное `cluster = None`).
    Ни один из двенадцати прежних сторожей этих правок не видел.
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell")})
    open_project(hub, root)

    action = create_entity_action(
        hub.config_tree_dock,
        find_child(category(file_item(hub.config_tree_dock.tree, root),
                            "cells"), "my_cell"),
        monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        accepted_real_form(cluster="CH1"))

    action.trigger()

    assert entities_of(root) == [{"name": "my_cell", "cell": "my_cell",
                                  "cluster": "CH1"}], (
        "то, что человек напечатал в поле Cluster, обязано доехать до записи "
        "как есть — иначе сущность нельзя клонировать по кластеру, и никто об "
        "этом не скажет; сейчас: " + repr(entities_of(root)))


def test_a_typed_sheet_through_the_real_form_reaches_the_record(
        real_main_window, tmp_path, monkeypatch):
    """Вторая половина: отпечаток, поле Sheet ЗАПОЛНЕНО (Channel_1 — целевой
    лист twin-резолва, config/models.py:704). В записи обязаны быть imprint: и
    sheet: Channel_1.

    Роняет сторож правка `sheet = None` — тот же класс, что `cluster = None`
    выше, только для второго ключевого поля пары (мутация М5л).
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")]})
    open_project(hub, root)

    action = create_entity_action(
        hub.config_tree_dock,
        find_child(category(file_item(hub.config_tree_dock.tree, root),
                            "imprints"), "amp"),
        monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        accepted_real_form(sheet="Channel_1"))

    action.trigger()

    assert entities_of(root) == [{"name": "amp", "imprint": "amp",
                                  "sheet": "Channel_1"}], (
        "напечатанный лист обязан доехать до записи — это целевой лист "
        "twin-резолва, без него сущность клонируется не на тот лист; сейчас: "
        + repr(entities_of(root)))


def test_an_empty_name_through_the_real_form_writes_nothing(
        real_main_window, tmp_path, monkeypatch):
    """С4 плана empty_cluster_normalisation, Т3: имя стёрто в настоящей форме,
    и хаб не пишет НИЧЕГО.

    Контроль против ложной зелени: отменённый диалог дал бы ровно то же
    «ничего не записано», поэтому зелёный сторож держится ещё и на непустом
    списке предупреждений. Отмена молчит, гейт имени — говорит.

    Мутация М4 (пропускать пустое имя) роняет сторож: форма принялась бы, и
    хаб записал бы запись с пустым именем — своего второго гейта на имя у него
    нет (name_exists_in_list_section("") на пустом конфиге даёт False).
    """
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": minimal_cells("my_cell")})
    open_project(hub, root)
    before = root.read_bytes()

    action = create_entity_action(
        hub.config_tree_dock,
        find_child(category(file_item(hub.config_tree_dock.tree, root),
                            "cells"), "my_cell"),
        monkeypatch)
    warnings = capture_warnings(monkeypatch)
    monkeypatch.setattr(create_entity_mod, "CreateEntityDialog",
                        accepted_real_form(name=""))

    action.trigger()

    assert root.read_bytes() == before, (
        "пустое имя не имеет права доехать до конфига (мутация М4); сейчас: "
        + repr(entities_of(root)))
    assert warnings, (
        "человеку обязано быть сказано, что имени нет — иначе «ничего не "
        "записано» неотличимо от отмены")
