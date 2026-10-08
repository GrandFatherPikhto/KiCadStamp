# tests/gui/docks/test_entities_part3_address.py
"""Сторожа ЧАСТИ 3 плана plan_2026_10_05_entities_under_cells: адрес экземпляра
идёт В АРГУМЕНТЕ, а не из невидимого хранилища `cell_working_instance` (п.2).

Слой: дверь (`gui/entity_doors.py`, индекс части 1 на настоящем конфиге в
tmp_path) + настоящий CellDock на настоящем конфиге + сама проводка DockHub.
Хранилище рабочего экземпляра ставится НА ДРУГУЮ сущность той же ячейки — тот
самый живой случай Дениса 08.10, где кнопка CellDock прочла выделение как
экземпляр ЭТОЙ сущности и отказала, а человек не видел, с чем работает.

Имена функций описывают СВОЙСТВО (правило 37), пункт плана назван в докстринге.
"""
from types import SimpleNamespace

from gui.cell_entity_choice import entity_address, remember_working_instance
from gui.dock_hub import DockHub
from gui.docks.cell_editor import CellDock
from gui.docks.entity_index import build_entity_index
from gui.entity_doors import door_address
from gui.subtract_copper import SubtractWiring
from kicadstamp.config.includes import walk_include_tree

import gui.docks.cell_editor as cell_editor_mod

from tests.gui.create_entity_helpers import write_config

_E1 = {"name": "e1", "cell": "c", "cluster": "CL1", "sheet": "S1"}
_E2 = {"name": "e2", "cell": "c", "cluster": "CL2", "sheet": "S2"}


def _config_with_two_entities(root) -> None:
    """One cell, two entities — the two channels of the live case, one of them
    pinning a refdes so the address has to carry the `refs` too."""
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [dict(_E1), dict(_E2, refs={"R": "R7"})]})


def _index(root):
    return build_entity_index(walk_include_tree(str(root)))


def _hub(index, cells_dock=None):
    return SimpleNamespace(
        config_tree_dock=SimpleNamespace(_entity_index=index),
        cells_dock=cells_dock if cells_dock is not None else SimpleNamespace())


# ═══════════════════════════════════════════════════════════════════════════
# п.2: адрес сущности разрешается по ИМЕНИ через индекс части 1
# ═══════════════════════════════════════════════════════════════════════════

def test_the_door_address_is_resolved_through_the_index(tmp_path):
    """п.2: дверь несёт ИМЯ сущности, адрес (cluster, sheet И `refs`) добывается
    здесь — тем же индексом части 1, а не догадкой по имени-подсказке."""
    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    hub = _hub(_index(root))

    row = door_address(hub, "e2")
    assert (row.entity_name, row.cluster, row.sheet) == ("e2", "CL2", "S2")
    assert row.refs == {"R": "R7"}, "пины сущности обязан нести именно индекс"

    row = door_address(hub, "e1")
    assert (row.cluster, row.sheet, row.refs) == ("CL1", "S1", None), \
        "сущность без refs — None, не {} (живой фатал 2б)"


def test_a_name_the_index_lost_falls_back_to_the_items_own_pair(tmp_path):
    """Свежее дерево над переименованной сущностью: индекс имени не знает, и
    берётся ПАРА самого пункта — ровно то, что человек видел; без пары — None
    (действие тогда работает по своим правилам, не по чужому хранилищу)."""
    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    hub = _hub(_index(root))

    row = door_address(hub, "gone", "CLX", "SX")
    assert (row.entity_name, row.cluster, row.sheet) == ("gone", "CLX", "SX")

    assert door_address(hub, "gone") is None
    assert door_address(hub, None, "CLX", "SX") is None


# ═══════════════════════════════════════════════════════════════════════════
# п.2: проводка хаба — пункт СУЩНОСТИ передаёт адрес, пункт ЯЧЕЙКИ не передаёт
# ═══════════════════════════════════════════════════════════════════════════

def test_a_cell_item_hands_no_address_and_an_entity_item_hands_its_own(tmp_path):
    """Дверь ячейки не называет сущности — вызов ровно прежний (адрес не
    передаётся); дверь сущности передаёт адрес ЭТОЙ сущности. Проводка одна на
    все три доски (reading, добавление, вычитание)."""
    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    calls = []

    def _note(kind):
        return lambda *a, **kw: calls.append((kind, a, kw))

    cells = SimpleNamespace(
        refresh_from_selection_requested=_note("refresh"),
        import_from_selection_requested=_note("import"),
        subtract_from_selection_requested=_note("subtract"))
    hub = _hub(_index(root), cells)
    path = str(root)

    DockHub._refresh_cell_from_selection(hub, "c", path)
    DockHub._import_cell_from_selection(hub, "c", path, choose_layers=True)
    DockHub._subtract_cell_from_selection(hub, "c", path)

    assert calls == [("refresh", ("c", path), {"choose_layers": False}),
                     ("import", ("c", path), {"choose_layers": True}),
                     ("subtract", ("c", path), {})], \
        "лист ЯЧЕЙКИ: вызов прежней формы, адреса нет"
    calls.clear()

    DockHub._refresh_cell_from_selection(hub, "c", path, "CL2", "S2", "e2")
    DockHub._import_cell_from_selection(hub, "c", path, "CL2", "S2", "e2",
                                        choose_layers=True)
    DockHub._subtract_cell_from_selection(hub, "c", path, "CL2", "S2", "e2")

    for kind, _args, kwargs in calls:
        row = kwargs.get("expected_address")
        assert row is not None, kind
        assert (row.entity_name, row.cluster, row.sheet) == ("e2", "CL2", "S2")

    assert calls[0][2]["choose_layers"] is False
    assert calls[1][2]["choose_layers"] is True


# ═══════════════════════════════════════════════════════════════════════════
# п.2: чтение берёт адрес ИЗ АРГУМЕНТА, хранилище не спрашивает
# ═══════════════════════════════════════════════════════════════════════════

def _capture_payloads(monkeypatch):
    payloads = []

    def _start(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        payloads.append(args[0] if args else None)
        return None

    monkeypatch.setattr(cell_editor_mod, "start_long_op", _start)
    return payloads


def test_the_read_bodies_take_the_doors_address_not_the_store(
        main_window, tmp_path, monkeypatch):
    """Живой случай Дениса: хранилище стоит на `e1`, а читать зовут `e2` — и
    читается ИМЕННО `e2` (payload воркера несёт адрес сущности), на обоих
    чтениях и на обоих ходах (быстрый и с диалогом слоёв). Без адреса (путь
    страницы) отвечает хранилище, как раньше."""
    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    dock = CellDock(main_window)
    dock.set_root_path(root)
    dock.load_entry("c")
    main_window.connection.board = SimpleNamespace(adapter=object())
    payloads = _capture_payloads(monkeypatch)

    remember_working_instance(root, "c", entity_address(dict(_E1)))
    assert dock._read_instance().cluster == "CL1", "хранилище стоит на e1"

    dock.refresh_from_selection_requested("c", root,
                                          expected_address=entity_address(dict(_E2)))
    assert payloads[-1]["expected_entity"].entity_name == "e2"
    assert payloads[-1]["remembered_cluster"] == "CL2"
    assert payloads[-1]["remembered_sheet"] == "S2"

    dock.import_from_selection_requested("c", root,
                                         expected_address=entity_address(dict(_E2)))
    assert payloads[-1]["expected_entity"].entity_name == "e2"
    assert payloads[-1]["remembered_cluster"] == "CL2"

    # The dialog leg carries the SAME address through the dialog into the read.
    calls = {}
    monkeypatch.setattr(
        cell_editor_mod, "open_cell_layers_dialog",
        lambda parent, connection, adapter, items, on_ok, widgets=(),
        on_error=None: calls.update(on_ok=on_ok) or "controller")
    dock.refresh_from_selection_requested("c", root, choose_layers=True,
                                          expected_address=entity_address(dict(_E2)))
    calls["on_ok"]({"F.Cu"}, set())
    assert payloads[-1]["expected_entity"].entity_name == "e2"

    # …and with NO address the store answers, exactly as before part 3.
    dock.refresh_from_selection_requested("c", root)
    assert payloads[-1]["expected_entity"].entity_name == "e1"
    assert payloads[-1]["remembered_cluster"] == "CL1"


def test_the_subtract_flow_takes_the_address_over_the_store(
        main_window, tmp_path, monkeypatch):
    """То же на вычитании: адрес двери становится парой (cluster, sheet) payload,
    хранилище не спрошено; без адреса — прежние поля хранилища."""
    captured = []

    def _start(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        captured.append(args[0])
        return None

    monkeypatch.setattr("gui.subtract_copper.start_long_op", _start)
    dock = SimpleNamespace(
        _main_window=SimpleNamespace(
            connection=SimpleNamespace(is_connected=True, timeout_ms=7)),
        _components=[{"role": "R"}], _vias=[], _tracks=[],
        _path=tmp_path / "root.sexp", _root_path=tmp_path / "root.sexp",
        _active_op=None,
        name_edit=SimpleNamespace(text=lambda: "c"),
        _remembered_cluster_value=lambda: "CL1",
        _remembered_sheet_value=lambda: "S1",
        subtract_copper_button=object())
    wiring = SubtractWiring(dock)

    wiring._open_with_instance(entity_address(dict(_E2)))
    assert (captured[-1]["cluster"], captured[-1]["sheet"]) == ("CL2", "S2")
    assert captured[-1]["cell_name"] == "c"

    wiring.open()
    assert (captured[-1]["cluster"], captured[-1]["sheet"]) == ("CL1", "S1"), \
        "без адреса — поля хранилища, как было"


# ═══════════════════════════════════════════════════════════════════════════
# п.1: пункты платы на листе СУЩНОСТИ, каждый называет свою сущность
# ═══════════════════════════════════════════════════════════════════════════

_ENTITY_BOARD_ITEMS = ("refresh_from_selection_action",
                       "import_from_selection_action",
                       "subtract_selection_action",
                       "refresh_from_selection_layers_action",
                       "import_from_selection_layers_action")


def _entity_leaf(hub, root, cell_name, entity_name):
    from tests.gui.create_entity_helpers import (category, file_item,
                                                find_child)

    tree = hub.config_tree_dock.tree
    cell_leaf = find_child(category(file_item(tree, root), "cells"), cell_name)
    return find_child(cell_leaf, entity_name)


def test_the_entity_leaf_carries_the_board_items_with_its_own_address(
        real_main_window, tmp_path, monkeypatch):
    """п.1: лист сущности несёт ВЕСЬ набор платы — три быстрых пункта и два
    «(choose layers)» — и каждый называет ЭТУ сущность: (cell, None, cluster,
    sheet, имя). Форма та же, что у Select ×3 (2б, п.4)."""
    from tests.gui.create_entity_helpers import context_menu_actions, open_project

    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    hub = real_main_window._dock_hub
    open_project(hub, root)

    actions = context_menu_actions(hub.config_tree_dock,
                                   _entity_leaf(hub, root, "c", "e2"), monkeypatch)
    by_name = {act.objectName(): act for _label, act in actions if act.objectName()}
    for name in _ENTITY_BOARD_ITEMS:
        assert name in by_name, sorted(by_name)

    seen = []
    for signal_name in ("cell_refresh_requested", "cell_import_requested",
                        "cell_subtract_requested",
                        "cell_refresh_layers_requested",
                        "cell_import_layers_requested"):
        getattr(hub.config_tree_dock, signal_name).connect(
            lambda *a, n=signal_name: seen.append((n, a)))

    for name in _ENTITY_BOARD_ITEMS:
        by_name[name].trigger()

    assert [name for name, _a in seen] == [
        "cell_refresh_requested", "cell_import_requested",
        "cell_subtract_requested", "cell_refresh_layers_requested",
        "cell_import_layers_requested"]
    for name, args in seen:
        assert args == ("c", None, "CL2", "S2", "e2"), (name, args)


def test_a_cell_leaf_carries_no_board_item_at_all(real_main_window, tmp_path,
                                                monkeypatch):
    """часть 3, п.3: ни одного пункта платы на листе ЯЧЕЙКИ — без исключения
    «если сущность одна». Адрес берут только там, где он ВИДЕН: под листом
    сущности (имя в пункте) или выпадашкой страницы."""
    from tests.gui.create_entity_helpers import (category, context_menu_actions,
                                                file_item, find_child,
                                                open_project)

    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    hub = real_main_window._dock_hub
    open_project(hub, root)

    tree = hub.config_tree_dock.tree
    cell = find_child(category(file_item(tree, root), "cells"), "c")
    actions = context_menu_actions(hub.config_tree_dock, cell, monkeypatch)
    names = {act.objectName() for _label, act in actions}

    for name in _ENTITY_BOARD_ITEMS + ("select_cell_components_action",
                                       "select_cell_action",
                                       "select_enclosed_copper_action"):
        assert name not in names, (name, sorted(names))
    labels = [label for label, _act in actions]
    assert "Update from selection..." not in labels, labels
    assert "Explode…" not in labels, labels


def test_the_board_item_of_an_entity_leaf_reaches_the_dock_with_the_address(
        real_main_window, tmp_path, monkeypatch):
    """Сквозь проводку: пункт листа сущности → сигнал → делегат хаба → адрес ЭТОЙ
    сущности в АРГУМЕНТЕ действия (живой случай: хранилище стоит на e1)."""
    from gui.cell_entity_choice import remember_working_instance
    from tests.gui.create_entity_helpers import context_menu_actions, open_project

    root = tmp_path / "root.sexp"
    _config_with_two_entities(root)
    hub = real_main_window._dock_hub
    open_project(hub, root)
    remember_working_instance(root, "c", entity_address(dict(_E1)))

    captured = []
    monkeypatch.setattr(
        hub.cells_dock, "refresh_from_selection_requested",
        lambda *a, **kw: captured.append((a, kw)))
    actions = dict(context_menu_actions(
        hub.config_tree_dock, _entity_leaf(hub, root, "c", "e2"), monkeypatch))

    actions["Update from selection..."].trigger()

    assert captured, "the item reached no dock entry point"
    _args, kwargs = captured[0]
    row = kwargs.get("expected_address")
    assert row is not None and row.entity_name == "e2", kwargs
    assert (row.cluster, row.sheet) == ("CL2", "S2"), kwargs
