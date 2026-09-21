# tests/gui/create_entity_helpers.py
"""Общие строители сторожей «Создать сущность» — один дом на два слоя.

Заход plan_2026_09_21_guard_file_by_property.md (Т1) разделил сторожа по
слоям: tests/gui/test_create_entity_form.py читает САМУ форму, а
tests/gui/test_create_entity_menu.py идёт через меню и обработчик хаба до
байтов конфига. Оба слоя строят одно и то же — конфиг в tmp_path, узлы дерева
по payload, пункт меню по objectName, настоящую форму, нажавшую OK — и Т1
запрещает держать по копии на слой: копии расходятся (прежний файл держал
копию `_context_menu_actions`, «намеренную» — см. tests/gui/test_config_tree.py;
теперь приём один).

Модуль не называется test_* — pytest его не собирает; тесты забирают
помощники как `from tests.gui.create_entity_helpers import ...` — то же
соглашение, что у tests/fieldstool_fixtures.py (и он же и есть прецедент
импорта соседнего модуля через `tests.…`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtCore import Qt

import gui.dock_hub as dock_hub_mod
import gui.docks.config_tree as config_tree_mod
import gui.docks.create_entity as create_entity_mod
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_working_set import WORKING_SET


# ── Конфиг в tmp_path ───────────────────────────────────────────────────

def write_config(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def load_config_data(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def entities_of(path) -> list:
    return load_config_data(path).get("entities") or []


def names_of(path) -> list:
    return [e.get("name") for e in entities_of(path)]


def minimal_cells(*names) -> dict:
    """{name: <минимальная валидная ячейка>} — та же форма, что сторожа пишут
    руками. Форма важна: обработчик резолвит дубликат через `load_config`, то
    есть конфиг сторожа обязан грузиться по-настоящему, а не только рисоваться
    в дереве."""
    return {name: {"components": [{"role": "R"}]} for name in names}


def minimal_imprint(name: str = "amp", source_sheet: str = "Channel_0") -> dict:
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


def open_project(hub, root) -> None:
    """Открыть проект тем же путём, что реальный GUI (File -> Open Root
    file...) — set_root_file на root_metadata_dock; он, помимо root_path, шлёт
    root_changed во все доки, и Config-дерево перестраивается само. Staged-
    режим (ConfigWorkingSet) выключается: все проверки читают ФАЙЛ, а не
    working-set."""
    hub.root_metadata_dock.set_root_file(root)
    WORKING_SET.enabled = False


# ── Узлы дерева: по payload, НИКОГДА по подписи ─────────────────────────

def find_child(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def file_item(tree, path):
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


def category(parent_item, section):
    """Узел-категория секции — по payload ("category", <section>), НИКОГДА по
    переведённой подписи: подпись это _("Cells")/_("Imprints") и зависит от
    локали и от каталогов, то есть сторож на ней падал бы по посторонним
    причинам."""
    for i in range(parent_item.childCount()):
        child = parent_item.child(i)
        data = child.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and data[:2] == ("category", section):
            return child
    raise AssertionError(
        f"под узлом {parent_item.text(0)!r} нет категории {section!r}")


# ── Контекстное меню: пункт по objectName, не по подписи ────────────────

def context_menu_actions(dock, item, monkeypatch):
    """Собрать КОНТЕКСТНОЕ МЕНЮ узла и вернуть список (label, QAction).

    QMenu.exec — no-op (меню реально не показывается), addAction — обёрнут,
    чтобы поймать каждый созданный QAction вместе с его подписью. ЕДИНСТВЕННАЯ
    копия приёма на оба слоя (Т1)."""
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


def create_entity_action(dock, item, monkeypatch):
    """QAction "Create entity" узла — ровно один, найденный по objectName, а
    не по переведённой подписи."""
    actions = context_menu_actions(dock, item, monkeypatch)
    matching = [act for _label, act in actions
                if act.objectName() == "create_entity_action"]
    assert len(matching) == 1, (
        "нет ровно одного действия create_entity_action; видели: "
        + repr([label for label, _ in actions]))
    return matching[0]


# ── Строки хаба и предупреждения формы ──────────────────────────────────

def capture_hub_messages(monkeypatch) -> list:
    """Захват пользовательских строк хаба (тот же стаб, что в
    tests/gui/test_phase3_wiring.py): отказ обязан ГОВОРИТЬ, что случилось."""
    messages: list = []
    monkeypatch.setattr(dock_hub_mod, "show_message",
                        lambda text, style="", logger=None: messages.append(text))
    return messages


def capture_warnings(monkeypatch) -> list:
    """Перехват QMessageBox.warning — он модальный и в offscreen-прогоне иначе
    просто повис бы. Тот же приём, что в tests/gui/test_extract_cluster_dialog.
    py::test_empty_name_rejected_without_accept: пишем текст и отдаём Ok."""
    warnings: list = []
    monkeypatch.setattr(
        create_entity_mod.QMessageBox, "warning",
        lambda *a, **k: warnings.append(a[2])
        or create_entity_mod.QMessageBox.StandardButton.Ok)
    return warnings


# ── Настоящая форма ─────────────────────────────────────────────────────

# Форма НАСТОЯЩАЯ, зафиксированная на импорте модуля: подмены из соседних
# сторожей (monkeypatch на create_entity_mod.CreateEntityDialog) снимаются
# после каждого теста, но фиксация на импорте делает это независимым от
# порядка прогона — «форма в прогоне — та самая форма».
RealCreateEntityDialog = create_entity_mod.CreateEntityDialog


def accepted_real_form(*, name=None, cluster=None, sheet=None):
    """Класс НАСТОЯЩЕЙ формы, которая «нажала OK». По умолчанию поля остаются
    такими, как их построил диалог (имя — имя источника, кластер и лист
    пусты); переданное сюда — то, что человек напечатал. accept() вызывается
    по-настоящему, поэтому валидация имени внутри диалога тоже отрабатывает —
    это не обход формы, а её принятие без показа окна."""
    class _AcceptedRealForm(RealCreateEntityDialog):
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


__all__ = [
    "RealCreateEntityDialog", "accepted_real_form", "capture_hub_messages",
    "capture_warnings", "category", "context_menu_actions",
    "create_entity_action", "entities_of", "file_item", "find_child",
    "load_config_data", "minimal_cells", "minimal_imprint", "names_of",
    "open_project", "write_config",
]
