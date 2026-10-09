# tests/gui/docks/test_entity_page_refs_tab.py
"""Сторожа ШАГА 3 плана plan_2026_10_09_entity_page: таб «Refs» переехал со
страницы ЯЧЕЙКИ на страницу СУЩНОСТИ (gui/entity/page.py, класс EntityPage),
и его контекст теперь идёт ОТ ЯЧЕЙКИ ЗАПИСИ сущности.

Свойства (правило 37 — имя описывает свойство):
  * таб ОДИН: он на странице сущности, а страница ячейки его больше не несёт
    (мутация «таб остался на ячейке» / «два таба»);
  * роли — роли ВСЕЙ ячейки ЗАПИСИ сущности, в порядке ячейки (Р2), и следуют
    за сменой сущности;
  * `refresh_known_roles` кормит таб записями СНИМКА, плата на UI-потоке не
    читается (дверь, правило 31);
  * `reload_overrides` перечитывает ФАЙЛ хранилища и отдаёт свежий объект табу
    (мутация «reload не перецелен»);
  * запись таба поднимает ОБА хука страницы (store → on_overrides_written,
    board → on_board_written) — та самая «одна запись сообщает всем», ради
    которой переезд и делался;
  * смена корня переключает и роли, и хранилище;
  * сущность-импринт (cell=None) не падает: ролей нет, таб говорит «выбери
    ячейку».

Headless: страница — обычный QWidget на conftest-двойнике main_window, снимок
синтетический, плата — шпион, который НЕ должен вызываться на UI-потоке.
"""
from types import SimpleNamespace

from gui.docks.cell_refs_tab import RefsTabWidget
from gui.entity.page import EntityPage
from gui.cell_edit_context import remember_role_table
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.field_overrides import (SOURCE_CELL_TABLE, load_field_overrides)
from kicadstamp.utils.paths import overrides_path_for_config

CELL = "cell_a"


# ── Двойники платы (только шпионаж: UI-поток не смеет их звать) ──────────────

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


def _record(ref, role, cluster=""):
    """Одна запись снимка — форма, которую читает role_table_model."""
    return SimpleNamespace(ref=ref, role=role, cluster=cluster,
                           sheet=("Ch0",), symbol_uuid="uuid-" + ref)


# ── Оснастка ─────────────────────────────────────────────────────────────────

def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")


def _project(tmp_path, name, cells, entities, sheet_file="root.sexp"):
    root = tmp_path / sheet_file
    _write(root, {"cells": cells, "entities": entities})
    return root


def _page(main_window, root):
    """Страница сущности с ОДНИМ табом Refs, отданным так же, как это делает
    DockHub: сначала set_root_path, потом add_refs_tab."""
    page = EntityPage(main_window)
    page.set_root_path(root)
    tab = RefsTabWidget(main_window, connection=main_window.connection,
                        parent=main_window)
    page.add_refs_tab(tab)
    return page


# ── Таб ОДИН и на странице сущности ──────────────────────────────────────────

def test_the_refs_tab_is_on_the_entity_page(main_window, tmp_path):
    """Таб «Refs» стоит на странице сущности (мутация «таб не переехал»)."""
    root = _project(tmp_path, "p", {"cell_a": {"components": [{"role": "R"}]}},
                    [{"name": "e1", "cell": "cell_a"}])
    page = _page(main_window, root)

    titles = [page.tabs.tabText(i) for i in range(page.tabs.count())]
    assert "Refs" in titles
    assert page._refs_tab is not None


# ── Роли — роли ВСЕЙ ячейки записи, в порядке ячейки (Р2) ─────────────────────

def test_the_roles_are_the_entity_cells_roles_in_cell_order(main_window,
                                                            tmp_path):
    """Роли идут от ячейки ЗАПИСИ сущности и в порядке компонентов ячейки
    (мутация «роли взяты не у ячейки» / «по алфавиту»)."""
    root = _project(tmp_path, "p", {"cell_a": {"components": [
        {"role": "C_BYPASS"}, {"role": "C_BULK"}]}},
        [{"name": "e1", "cell": "cell_a"}])
    page = _page(main_window, root)

    page.load_entity("e1")

    assert page._refs_tab._role_combo_items() == ["C_BYPASS", "C_BULK"]


def test_the_roles_follow_the_loaded_entity(main_window, tmp_path):
    """Смена сущности меняет роли: адрес берётся у ЗАПИСИ, а не откуда-то ещё."""
    root = _project(tmp_path, "p", {
        "cell_a": {"components": [{"role": "R_A"}]},
        "cell_b": {"components": [{"role": "R_B"}]}},
        [{"name": "e1", "cell": "cell_a"}, {"name": "e2", "cell": "cell_b"}])
    page = _page(main_window, root)

    page.load_entity("e1")
    assert page._refs_tab._role_combo_items() == ["R_A"]

    page.load_entity("e2")
    assert page._refs_tab._role_combo_items() == ["R_B"]


# ── Снимок: таб кормится записями, плата не читается ─────────────────────────

def test_refresh_feeds_the_tab_board_columns_without_reading_the_board(
        main_window, tmp_path):
    """`refresh_known_roles` кладёт снимок в таб (board-колонки), а плата на
    UI-потоке не трогается."""
    root = _project(tmp_path, "p", {"cell_a": {"components": [{"role": "C_BULK"}]}},
                    [{"name": "e1", "cell": "cell_a"}])
    remember_role_table(root, "cell_a", {
        "cluster": "",
        "rows": [{"ref": "C74", "role": "C_BULK", "cluster": ""}]})
    adapter = _RecordingAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    page = _page(main_window, root)
    page.load_entity("e1")

    page.refresh_known_roles([_record("C74", "C_BULK")])

    assert page._refs_tab.row_refs() == ["C74"]
    assert page._refs_tab.rows[0].board_role == "C_BULK"
    assert adapter.calls == [], f"UI-поток читал плату: {adapter.calls}"


# ── reload_overrides: перечитать ФАЙЛ и отдать табу ──────────────────────────

def test_reload_overrides_serves_the_fresh_store_to_the_tab(main_window,
                                                            tmp_path):
    """Запись, сделанная ДРУГОЙ панелью в файл, доезжает до таба через
    `reload_overrides` (мутация «reload не перецелен на страницу сущности»)."""
    root = _project(tmp_path, "p", {"cell_a": {"components": [{"role": "R"}]}},
                    [{"name": "e1", "cell": "cell_a"}])
    page = _page(main_window, root)
    page.load_entity("e1")

    other = load_field_overrides(overrides_path_for_config(str(root)))
    other.set("uuid-R1", "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CELL_TABLE)
    other.save()

    page.reload_overrides()

    assert (page._refs_tab._overrides.get("uuid-R1", ROLE_FIELD_NAME)
            == "R_WRITTEN")


# ── Оба хука: запись таба доезжает до владельцев ─────────────────────────────

def test_a_refs_write_reaches_both_page_hooks(main_window, tmp_path):
    """Запись в хранилище из таба поднимает ОБА хука страницы — store-половину
    и board-половину (то, ради чего переезд: «одна запись сообщает всем»)."""
    root = _project(tmp_path, "p", {"cell_a": {"components": [{"role": "C_BULK"}]}},
                    [{"name": "e1", "cell": "cell_a"}])
    remember_role_table(root, "cell_a", {
        "cluster": "",
        "rows": [{"ref": "C74", "role": "C_BULK", "cluster": ""}]})
    page = _page(main_window, root)
    page.load_entity("e1")
    # Плата несёт ДРУГУЮ роль — значит таблице есть что записать.
    page.refresh_known_roles([_record("C74", "C_OLD")])

    fired = []
    page.on_overrides_written = lambda: fired.append("store")
    page.on_board_written = lambda: fired.append("board")

    page._refs_tab.write_to_store()

    assert fired == ["store", "board"]


# ── Смена корня переключает и роли, и хранилище ──────────────────────────────

def test_a_root_change_rescopes_the_tab(main_window, tmp_path):
    """Новый проект — другие роли и другое хранилище."""
    root_a = _project(tmp_path, "a", {"cell_a": {"components": [{"role": "R_A"}]}},
                      [{"name": "ea", "cell": "cell_a"}], sheet_file="a.sexp")
    root_b = _project(tmp_path, "b", {"cell_b": {"components": [{"role": "R_B"}]}},
                      [{"name": "eb", "cell": "cell_b"}], sheet_file="b.sexp")
    page = _page(main_window, root_a)
    page.load_entity("ea")
    assert page._refs_tab._role_combo_items() == ["R_A"]
    store_a = page._overrides

    page.set_root_path(root_b)
    page.load_entity("eb")

    assert page._refs_tab._role_combo_items() == ["R_B"]
    assert page._overrides is not store_a


# ── Сущность-импринт (cell=None) не падает ───────────────────────────────────

def test_an_imprint_entity_has_no_roles_and_does_not_crash(main_window,
                                                           tmp_path):
    """У сущности без ячейки ролей нет — таб говорит «выбери ячейку», без
    исключения (страница сироты открывается)."""
    root = tmp_path / "root.sexp"
    _write(root, {
        "imprints": [{"name": "im", "uuid": "u-im"}],
        "entities": [{"name": "e_imp", "imprint": "im"}]})
    page = _page(main_window, root)

    page.load_entity("e_imp")

    assert page._refs_tab._role_combo_items() == []
    assert "Pick a Cell" in page._refs_tab.status_text()
