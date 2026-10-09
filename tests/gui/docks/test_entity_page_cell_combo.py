# tests/gui/docks/test_entity_page_cell_combo.py
"""Сторожа ШАГА 1 плана plan_2026_10_09_entity_page: ячейка сущности — КОМБОБОКС
на странице сущности (gui/entity/page.py, класс EntityPage), а не диалог и не
вторая точка входа.

Свойства (правило 37 — имя описывает свойство):
  * комбобокс показывает ячейку сущности текущей и предлагает остальные;
  * неподходящие ЧУЖИЕ ячейки — не строки списка, а одна серая строка-счётчик
    «K other cells do not fit» с причинами в подсказке (Денис, 09.10.2026);
  * выбор уходит в ОДНУ запись change_cell_flow.apply_cell_change (мутация
    «комбобокс пишет своей записью мимо apply_cell_change» должна погибнуть);
  * нет снимка (подбор не проверялся) — предложены ВСЕ ячейки;
  * Д1 плана plan_2026_10_09_entity_page: push снимка ПЕРЕСЧИТЫВАЕТ список по
    пришедшему снимку и при этом ничего не пишет;
  * старый модуль gui/docks/entity_page.py — ОДИН алиас, не вторая реализация.

Полный путь идёт через настоящий хаб (real_main_window) — комбобокс берёт адрес
и снимок у страницы, а пишет тот же поток, что и пункт «Change cell…».
"""
import gui.entity.page as page_mod
from gui.docks.change_cell_flow import cell_choices

from tests.gui.create_entity_helpers import entities_of, open_project, write_config


# ── Двойники снимка (то же, что читает snapshot_parts: ref/role/cluster/sheet/fp) ──

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


def _open(real_main_window, tmp_path, data, snapshot):
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, data)
    open_project(hub, root)
    if snapshot is not None:
        real_main_window.connection._snapshot = list(snapshot)
    return hub, root


# ── Свойства комбобокса ──────────────────────────────────────────────────

def test_the_combobox_shows_the_entities_cell_and_offers_the_rest(
        real_main_window, tmp_path):
    # The entity's own cell is c_b, which sorts AFTER c_a — so being selected
    # proves the box picks the entity's OWN cell, not merely the first row
    # (mutation «pos always 0» dies on the currentData assertion below).
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_a": {"components": [{"role": "R"}]},
                  "c_b": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_b", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    assert page.cell_combo.currentData() == "c_b", (
        "текущая ячейка сущности обязана быть выбранной")
    names = {page.cell_combo.itemData(i)
             for i in range(page.cell_combo.count())}
    assert names == {"c_a", "c_b"}, "предложены ВСЕ ячейки конфига"


def test_a_cell_that_does_not_fit_is_left_out_and_counted(
        real_main_window, tmp_path):
    """Экземпляр несёт роль R; клетка c_bad хочет CAP и это НЕ текущая ячейка —
    её НЕТ в списке, только серая строка «1 other cells do not fit» с причиной в
    подсказке (Денис, 09.10.2026: список не разрастается)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]},
                  "c_bad": {"components": [{"role": "CAP"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    names = {page.cell_combo.itemData(i)
             for i in range(page.cell_combo.count())}
    assert names == {"c_ok"}, "неподходящая ЧУЖАЯ ячейка — не строка списка"
    assert page.cell_others_label.text() == "1 other cells do not fit"
    assert "role CAP: 0 of 1" in page.cell_others_label.toolTip()
    assert page.cell_others_label.isVisibleTo(page) is True


def test_the_current_cell_that_does_not_fit_is_marked(
        real_main_window, tmp_path):
    """Текущая ячейка сущности остаётся в списке, даже не подходя — с пометкой
    «current, does not fit: <причина>» и недоступная для выбора."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]},
                  "c_bad": {"components": [{"role": "CAP"}]}},
        "entities": [{"name": "e1", "cell": "c_bad", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    assert page.cell_combo.currentData() == "c_bad"
    item = page.cell_combo.model().item(page.cell_combo.currentIndex())
    assert item.isEnabled() is False
    assert "current, does not fit: role CAP: 0 of 1" in item.text()
    assert page.cell_others_label.isVisibleTo(page) is False


def test_choosing_a_cell_goes_through_apply_cell_change(
        real_main_window, tmp_path, monkeypatch):
    """Выбор в комбобоксе зовёт ОДНУ запись change_cell_flow.apply_cell_change,
    а не пишет сам. Роняет мутацию «комбобокс пишет своей записью мимо
    apply_cell_change»."""
    hub, root = _open(real_main_window, tmp_path, {
        "cells": {"c_a": {"components": [{"role": "R"}]},
                  "c_b": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_a", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    page.load_entity("e1")

    calls = []
    monkeypatch.setattr(page_mod, "apply_cell_change",
                        lambda *a: calls.append(a))
    idx = page.cell_combo.findData("c_b")
    page._on_cell_chosen(idx)

    assert len(calls) == 1, "выбор обязан пройти через apply_cell_change"
    _hub, entity, file_path, chosen = calls[0]
    assert chosen == "c_b" and entity["name"] == "e1"
    assert entities_of(root)[0]["cell"] == "c_a", (
        "комбобокс сам ничего не пишет — запись делает apply_cell_change")


def test_choosing_a_cell_writes_it_and_emits_graph_changed_once(
        real_main_window, tmp_path):
    hub, root = _open(real_main_window, tmp_path, {
        "cells": {"c_a": {"components": [{"role": "R"}]},
                  "c_b": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_a", "cluster": "CL",
                      "sheet": "Ch0"}]}, [_Sel("R1", "CL")])
    page = hub.entity_dock
    changes = []
    hub.config_tree_dock.graph_changed.connect(lambda: changes.append(1))
    page.load_entity("e1")

    page._on_cell_chosen(page.cell_combo.findData("c_b"))

    saved = entities_of(root)[0]
    assert saved["cell"] == "c_b" and saved.get("cell_uuid")
    assert len(changes) == 1, "одна правка = один graph_changed"


def test_without_a_snapshot_every_cell_is_offered(
        real_main_window, tmp_path):
    """Нет снимка — подбор не проверялся: предложены ВСЕ ячейки и НИ ОДНА не
    серая (сироту чинят именно сменой ячейки)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]},
                  "c_bad": {"components": [{"role": "CAP"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [])
    page = hub.entity_dock
    page.load_entity("e1")

    assert page.cell_combo.count() == 2
    assert all(page.cell_combo.model().item(i).isEnabled()
               for i in range(page.cell_combo.count()))


# ── Д1: push снимка пересчитывает список ─────────────────────────────────

def test_a_push_narrows_the_cell_list_to_the_fitting_cells(
        real_main_window, tmp_path):
    """Д1: без снимка предложены ВСЕ ячейки («подбор не проверялся») → push С
    экземпляром сужает список до подходящей, а чужая неподходящая остаётся
    только серой строкой-счётчиком.

    Снимок соединения нарочно оставлен пустым: судить надо по ПРИШЕДШЕМУ снимку
    (мутации «push не пересчитывает» и «push читает connection.snapshot» краснят
    эту клетку)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]},
                  "c_bad": {"components": [{"role": "CAP"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [])
    page = hub.entity_dock
    page.load_entity("e1")
    assert page.cell_combo.count() == 2, "без снимка — все ячейки"

    page.refresh_known_roles([_Sel("R1", "CL")])

    names = {page.cell_combo.itemData(i)
             for i in range(page.cell_combo.count())}
    assert names == {"c_ok"}, "подходящая + текущая; чужая неподходящая — не строка"
    assert page.cell_others_label.text() == "1 other cells do not fit"


def test_a_push_does_not_write_the_cell(real_main_window, tmp_path, monkeypatch):
    """Д1: пересчёт комбобокса НЕ пишет — apply_cell_change за push не зовётся, а
    текущая ячейка остаётся выбранной (blockSignals в _fill_cell_combo)."""
    hub, _root = _open(real_main_window, tmp_path, {
        "cells": {"c_ok": {"components": [{"role": "R"}]},
                  "c_bad": {"components": [{"role": "CAP"}]}},
        "entities": [{"name": "e1", "cell": "c_ok", "cluster": "CL",
                      "sheet": "Ch0"}]}, [])
    page = hub.entity_dock
    page.load_entity("e1")

    calls = []
    monkeypatch.setattr(page_mod, "apply_cell_change",
                        lambda *a: calls.append(a))

    page.refresh_known_roles([_Sel("R1", "CL")])

    assert calls == [], "пересчёт комбобокса не смеет писать"
    assert page.cell_combo.currentData() == "c_ok", "текущая ячейка осталась"


# ── cell_choices — чистый слой (без Qt) ──────────────────────────────────

def test_cell_choices_without_a_snapshot_offers_everything_fit_not_checked(
        tmp_path):
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]},
                                  "c_bad": {"components": [{"role": "CAP"}]}}})
    choices = cell_choices(root, None, {"cluster": "CL", "sheet": "Ch0"})
    assert choices.orphan is True
    assert {c.name for c in choices.candidates} == {"c_ok", "c_bad"}
    assert all(c.fits for c in choices.candidates)
    assert choices.others == ()


def test_cell_choices_lists_only_fitting_cells_and_counts_the_rest(tmp_path):
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]},
                                  "c_bad": {"components": [{"role": "CAP"}]}}})
    choices = cell_choices(root, [_Sel("R1", "CL")],
                           {"cluster": "CL", "sheet": "Ch0"})
    assert choices.orphan is False
    assert [c.name for c in choices.candidates] == ["c_ok"]
    assert [c.name for c in choices.others] == ["c_bad"]
    assert choices.others[0].reason == "role CAP: 0 of 1"


def test_cell_choices_keeps_the_current_cell_even_when_it_does_not_fit(
        tmp_path):
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]},
                                  "c_bad": {"components": [{"role": "CAP"}]}}})
    choices = cell_choices(root, [_Sel("R1", "CL")],
                           {"cluster": "CL", "sheet": "Ch0", "cell": "c_bad"})
    assert [c.name for c in choices.candidates] == ["c_ok", "c_bad"]
    cur = choices.candidates[1]
    assert cur.current and not cur.fits
    assert choices.others == (), "текущая ячейка не считается «другой»"


class _Index:
    """The RAW part-1 index a DANGLING graph still carries (names + uuids)."""

    def __init__(self, names):
        self._names = list(names)

    def names_for(self, section):
        return list(self._names) if section == "cells" else []

    def target_uuid(self, section, name):
        return None


def test_cell_choices_without_a_snapshot_is_unchecked_not_read_only(tmp_path):
    """Денис, 09.10.2026: НЕТ снимка — это «подбор не проверялся», а НЕ сирота:
    комбобокс раскрывает все ячейки, но read-only НЕ включается (табы платы
    живы), иначе отключение KiCad гасило бы страницу."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]}}})
    choices = cell_choices(root, None, {"cluster": "CL", "sheet": "Ch0"})
    assert choices.state == "unchecked"
    assert choices.orphan is True and choices.read_only is False
    assert {c.name for c in choices.candidates} == {"c_ok"}


def test_cell_choices_a_snapshot_without_the_instance_is_a_board_orphan(tmp_path):
    """Снимок ЕСТЬ, но экземпляра сущности в нём нет — сирота по ПЛАТЕ: read-only
    включается (адрес читать не с чего), и комбобокс раскрывает все ячейки."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]}}})
    choices = cell_choices(root, [_Sel("R1", "OTHER")],
                           {"cluster": "CL", "sheet": "Ch0"})
    assert choices.state == "orphan_board"
    assert choices.orphan is True and choices.read_only is True
    assert {c.name for c in choices.candidates} == {"c_ok"}


def test_cell_choices_a_dangling_graph_is_a_graph_orphan(tmp_path):
    """Граф болтается (сущность называет несуществующую ячейку, load_config
    фатален) — сирота по ГРАФУ: read-only, а ячейки приходят из сырого индекса."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c_ok": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "MISSING"}]})
    choices = cell_choices(root, None, {"cluster": "CL", "sheet": "Ch0"},
                           _Index(["c_ok"]))
    assert choices.state == "orphan_graph"
    assert choices.read_only is True
    assert {c.name for c in choices.candidates} == {"c_ok"}


# ── Шим старого модуля ───────────────────────────────────────────────────

def test_the_old_module_names_the_same_page_object():
    """gui/docks/entity_page.py — ОДИН алиас: EntityInfoDock И ЕСТЬ EntityPage,
    а не вторая реализация, которая бы разошлась."""
    from gui.docks.entity_page import EntityInfoDock
    from gui.entity.page import EntityPage
    assert EntityInfoDock is EntityPage
