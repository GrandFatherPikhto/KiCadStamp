# tests/gui/docks/test_add_entities_menu.py
"""Сторожа МЕНЮ и ОБРАБОТЧИКА «Add entities…» (plan_2026_10_09_cells_and_entities,
часть 1) — пункт на листе ячейки, поток в gui/docks/add_entities_flow.py и
запись в конфиг.

Слой: полный путь пользователя — ConfigTreeDock -> сигнал -> делегат хаба ->
поток -> диалог (подменённый) -> файл. Правило подбора и таблица формы
стерегутся в своих файлах (test_instance_candidates.py, test_add_entities_dialog.py).

Сторож смотрит на РЕЗУЛЬТАТ (что в конфиге) и на ДВЕРЬ (какой дверной вызов
сделан), а не на переводы подписей: пункт ищется по objectName
"add_entities_action", сигнал проверяется шпионом (как в test_create_entity_menu).

Имена функций описывают СВОЙСТВО (правило 37), имя плана — в докстринге.
"""
import logging

import pytest
from PyQt6.QtWidgets import QDialog

import gui.docks.add_entities_flow as flow_mod
from gui.docks.add_entities import ChosenEntity

from tests.gui.create_entity_helpers import (
    category,
    context_menu_actions,
    entities_of,
    file_item,
    find_child,
    minimal_cells,
    open_project,
    write_config,
)


# ── Снимок: простые stand-ins (Selected-подобные, без PyQt6) ─────────────

class _Fp:
    def __init__(self, uuid):
        self.uuid = uuid


class _Sel:
    """Достаточно того, что читает snapshot_parts: ref/role/cluster/sheet/fp."""

    def __init__(self, ref, cluster, role="R", sheet=("Ch0",)):
        self.ref = ref
        self.role = role
        self.cluster = cluster
        self.sheet = sheet
        self.fp = _Fp("u-" + ref)


def _setup(hub, tmp_path, extra=None):
    root = tmp_path / "root.sexp"
    data = {"cells": minimal_cells("my_cell")}
    data.update(extra or {})
    write_config(root, data)
    open_project(hub, root)
    return root


def _set_snapshot(hub, monkeypatch, sels):
    monkeypatch.setattr(hub.main_window.connection, "_snapshot", list(sels))


def _door(hub, monkeypatch, *, run=True):
    """Двойник ДВЕРИ: запоминает on_ready и (по умолчанию) сразу его зовёт —
    'пересборка снимка в воркере, затем продолжение в потоке UI'. Живой платы в
    прогоне нет, поэтому on_ready в бою идёт на кэшированном снимке."""
    calls = []

    def _fake(on_ready=None):
        calls.append(on_ready)
        if run and on_ready is not None:
            on_ready()

    monkeypatch.setattr(hub, "refresh_snapshot_and_push", _fake)
    return calls


def _accepting_dialog(monkeypatch, made, name_fn=None):
    """Подмена формы: принимает и возвращает по одной строке на каждый
    СВОБОДНЫЙ подходящий экземпляр. По умолчанию имя — по кластеру; тест, где
    ОДИН кластер стоит на нескольких листах, подаёт своё имя — иначе две строки
    получили бы одно имя, и вторая была бы отброшена как дубликат."""
    make_name = name_fn or (lambda c: "ent_" + c.cluster)

    class _Dialog:
        def __init__(self, parent, cell_name, candidates, existing_names):
            made["candidates"] = list(candidates)
            self._rows = [
                ChosenEntity(name=make_name(c), cluster=c.cluster,
                             sheet=c.sheet)
                for c in candidates if c.fits and not c.taken]

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_data(self):
            return list(self._rows)

    monkeypatch.setattr(flow_mod, "AddEntitiesDialog", _Dialog)


def _counting_write(monkeypatch):
    """Считает вызовы write_data потока — 'все отмеченные ОДНОЙ правкой'."""
    from kicadstamp import config_writer
    calls = []
    real = config_writer.write_data

    def _spy(path, data):
        calls.append(path)
        return real(path, data)

    monkeypatch.setattr(flow_mod, "write_data", _spy)
    return calls


# ── Меню ─────────────────────────────────────────────────────────────────

def test_cell_leaf_has_one_add_entities_action_emitting_the_signal(
        real_main_window, tmp_path, monkeypatch):
    """На листе ЯЧЕЙКИ есть ровно один пункт «Add entities…», найденный по
    objectName, и он эмитит add_entities_requested(имя ячейки, путь)."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path)
    dock = hub.config_tree_dock
    leaf = find_child(category(file_item(dock.tree, root), "cells"), "my_cell")

    actions = context_menu_actions(dock, leaf, monkeypatch)
    matching = [act for _label, act in actions
                if act.objectName() == "add_entities_action"]
    assert len(matching) == 1, (
        "на листе ячейки нет ровно одного add_entities_action; видели: "
        + repr([label for label, _ in actions]))

    emitted = []
    hub.config_tree_dock.add_entities_requested.connect(
        lambda name, path: emitted.append((name, str(path))))
    matching[0].trigger()
    assert emitted == [("my_cell", str(root.resolve()))]


# ── Обработчик: запись ───────────────────────────────────────────────────

def test_checked_instances_are_written_in_one_edit(
        real_main_window, tmp_path, monkeypatch):
    """Два подходящих экземпляра -> две записи entities:, ОДНОЙ правкой
    write_data, один graph_changed. Каждая запись несёт cell/cluster/sheet."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path)
    _set_snapshot(hub, monkeypatch, [_Sel("R1", "CL_A"), _Sel("R2", "CL_B")])
    _door(hub, monkeypatch)
    made = {}
    _accepting_dialog(monkeypatch, made)
    writes = _counting_write(monkeypatch)

    changes = []
    hub.config_tree_dock.graph_changed.connect(lambda: changes.append(1))

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    rows = {e["name"]: e for e in entities_of(root)}
    assert set(rows) == {"ent_CL_A", "ent_CL_B"}
    assert rows["ent_CL_A"]["cell"] == "my_cell"
    assert rows["ent_CL_A"]["cluster"] == "CL_A"
    assert rows["ent_CL_A"]["sheet"] == "Ch0"
    assert len(writes) == 1, "две записи обязаны лечь ОДНОЙ правкой, не двумя"
    assert len(changes) == 1, "одна правка = один graph_changed"


def test_taken_instance_is_not_written(real_main_window, tmp_path, monkeypatch):
    """Экземпляр, у которого уже есть сущность этой ячейки, не предлагается и
    не пишется (правило find_entity_for_source)."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path, extra={"entities": [
        {"name": "existing_e", "cell": "my_cell", "cluster": "CL_A"}]})
    _set_snapshot(hub, monkeypatch, [_Sel("R1", "CL_A")])
    _door(hub, monkeypatch)
    made = {}
    _accepting_dialog(monkeypatch, made)

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    taken = [c for c in made["candidates"] if c.cluster == "CL_A"][0]
    assert taken.taken and taken.entity_name == "existing_e"
    assert [e["name"] for e in entities_of(root)] == ["existing_e"]


def test_non_fitting_instance_is_not_written(
        real_main_window, tmp_path, monkeypatch):
    """Экземпляр без нужной роли ячейки в таблицу (и в запись) не идёт."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path)
    _set_snapshot(hub, monkeypatch,
                  [_Sel("R1", "CL_OK"), _Sel("X1", "CL_BAD", role="OTHER")])
    _door(hub, monkeypatch)
    made = {}
    _accepting_dialog(monkeypatch, made)

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    assert [c.cluster for c in made["candidates"] if c.fits] == ["CL_OK"]
    assert [e["name"] for e in entities_of(root)] == ["ent_CL_OK"]


def test_no_tree_nodes_are_added(real_main_window, tmp_path, monkeypatch):
    """Размещение живёт только в деревьях: создание сущностей НЕ добавляет
    узлов (п.6 части 1 — развилка ждёт слова Дениса)."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path)
    from tests.gui.create_entity_helpers import load_config_data
    before = load_config_data(root).get("trees")
    _set_snapshot(hub, monkeypatch, [_Sel("R1", "CL_A")])
    _door(hub, monkeypatch)
    _accepting_dialog(monkeypatch, {})

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    assert load_config_data(root).get("trees") == before


# ── Дверь: диалог открывается ПОСЛЕ запроса пересборки снимка ─────────────

def test_dialog_opens_only_after_the_snapshot_rebuild_request(
        real_main_window, tmp_path, monkeypatch):
    """Обработчик сначала ЗАПРАШИВАЕТ пересборку снимка (дверь), и только
    внутри on_ready строит и открывает диалог. Дверь, которая НЕ зовёт
    on_ready, обязана оставить диалог несозданным."""
    hub = real_main_window._dock_hub
    _setup(hub, tmp_path)
    _set_snapshot(hub, monkeypatch, [_Sel("R1", "CL_A")])
    calls = _door(hub, monkeypatch, run=False)
    made = {}
    _accepting_dialog(monkeypatch, made)

    flow_mod.add_entities_from_tree(hub, "my_cell", hub.root_metadata_dock.root_path)
    assert len(calls) == 1 and "candidates" not in made, (
        "диалог построен ДО запроса свежего снимка — дверь обойдена")

    calls[0]()  # the worker's completion: now the dialog may open
    assert "candidates" in made


def test_open_log_names_fit_taken_and_lack(
        real_main_window, tmp_path, monkeypatch, caplog):
    """Строка Лога при открытии: «N instances fit cell X, M taken, K lack
    roles» — N/M/K числами."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path, extra={"entities": [
        {"name": "existing_e", "cell": "my_cell", "cluster": "CL_B"}]})
    _set_snapshot(hub, monkeypatch, [
        _Sel("R1", "CL_A"), _Sel("R2", "CL_B"), _Sel("X1", "CL_BAD", role="X")])
    _door(hub, monkeypatch)
    _accepting_dialog(monkeypatch, {})

    with caplog.at_level(logging.INFO):
        flow_mod.add_entities_from_tree(hub, "my_cell", root)

    assert any("2 instances fit cell my_cell, 1 taken, 1 lack roles" in r.message
               for r in caplog.records), \
        "Лог обязан назвать, сколько подходит / занято / без ролей"

# ── Д1 приёмки: «занято» обязано видеть ЛИСТ ────────────────────────────

def test_entity_on_another_sheet_does_not_take_the_instance(
        real_main_window, tmp_path, monkeypatch):
    """Д1: один кластер на нескольких листах — сущность на Channel_0 НЕ делает
    занятыми Channel_1/Channel_2. Ветка `cell` в find_entity_for_source сужается
    и по листу (sheet_in_path); иначе «Add entities» на канальной ячейке
    упёрлось бы сюда, и добавить Channel_1/2 было бы нельзя."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path, extra={"entities": [
        {"name": "dac_buf_channel_0", "cell": "my_cell", "cluster": "DAC_BUF",
         "sheet": "Channel_0"}]})
    _set_snapshot(hub, monkeypatch, [
        _Sel("R1", "DAC_BUF", sheet=("Channel_0",)),
        _Sel("R2", "DAC_BUF", sheet=("Channel_1",)),
        _Sel("R3", "DAC_BUF", sheet=("Channel_2",))])
    _door(hub, monkeypatch)
    made = {}
    _accepting_dialog(monkeypatch, made,
                      name_fn=lambda c: "ent_%s_%s" % (c.cluster, c.sheet))
    writes = _counting_write(monkeypatch)

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    by_sheet = {c.sheet: c for c in made["candidates"]}
    assert by_sheet["Channel_0"].taken, "Channel_0 уже занят — это верно"
    assert not by_sheet["Channel_1"].taken, (
        "сущность на Channel_0 не имеет права занимать Channel_1 (мутация "
        "«лист снова не участвует»)")
    assert not by_sheet["Channel_2"].taken
    assert {e["name"] for e in entities_of(root)} == {
        "dac_buf_channel_0", "ent_DAC_BUF_Channel_1", "ent_DAC_BUF_Channel_2"}
    assert len(writes) == 1, "обе новые записи — одной правкой"


def test_entity_without_a_sheet_takes_every_sheet(
        real_main_window, tmp_path, monkeypatch):
    """Сущность БЕЗ листа — «на любом листе» (строго, как было): занимает ВСЕ
    экземпляры кластера, ни один не предлагается к добавлению."""
    hub = real_main_window._dock_hub
    root = _setup(hub, tmp_path, extra={"entities": [
        {"name": "any_sheet", "cell": "my_cell", "cluster": "DAC_BUF"}]})
    _set_snapshot(hub, monkeypatch, [
        _Sel("R1", "DAC_BUF", sheet=("Channel_0",)),
        _Sel("R2", "DAC_BUF", sheet=("Channel_1",))])
    _door(hub, monkeypatch)
    made = {}
    _accepting_dialog(monkeypatch, made)

    flow_mod.add_entities_from_tree(hub, "my_cell", root)

    assert all(c.taken for c in made["candidates"]), (
        "сущность без листа обязана занимать все листы кластера")
    assert [e["name"] for e in entities_of(root)] == ["any_sheet"]
