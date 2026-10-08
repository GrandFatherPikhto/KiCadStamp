# tests/gui/docks/test_entities_under_cells.py
"""Сторожа ДЕРЕВА: сущности — детьми своей ячейки/импринта
(plan_2026_10_05_entities_under_cells, часть 1, пункты 1–8).

Слой: дерево строится НАСТОЯЩИМ ConfigTreeDock на конфиге в tmp_path. Общие
строители — tests/gui/create_entity_helpers.py (Т1: по копии на слой не
держим). Имена функций описывают СВОЙСТВО (правило 37), пункт плана назван в
докстринге.

Три сторожа, охранявшие СТАРУЮ форму раздела «Entities», переведены на новое
поведение в СВОИХ файлах, а не ослаблены:
  * tests/gui/docks/test_config_tree.py::test_cell_leaf_with_comment_shows_glyph
    _and_tooltip и ::test_cell_leaf_without_comment_is_plain — фикстуре дана
    сущность, чтобы ячейка не несла пометку «без сущности»; утверждения не
    тронуты (предмет — глиф комментария, не пометка);
  * tests/gui/test_select_enclosed_copper.py::test_entities_leaf_has_the_item
    _and_sends_its_explicit_instance — узел сущности берётся под её ячейкой;
    payload и утверждения не тронуты.
"""
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyle

import gui.docks.config_tree as config_tree_mod
from gui.docks.config_tree import (ConfigTreeDock, _CELL_PLACED, _CELL_UNUSED,
                                   _ROLE_CELL_MARK, _ROLE_OWN_FILE)
from kicadstamp.config.sexp_format import sexp_to_dict
from kicadstamp.config_working_set import format3_stamp_disabled

from tests.gui.create_entity_helpers import (category, context_menu_actions,
                                             file_item, find_child,
                                             minimal_imprint, open_project,
                                             write_config)


def _dock(main_window, root):
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    return dock


def _has_category(item, section) -> bool:
    """Whether a section category node exists under `item` — by its payload,
    never by the translated label."""
    for i in range(item.childCount()):
        data = item.child(i).data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and data[:2] == ("category", section):
            return True
    return False


def _mark(leaf):
    return leaf.data(0, _ROLE_CELL_MARK)


def _icon_image(item):
    """The item's icon AS PIXELS — the only honest way to say WHICH standard
    icon sits on it: a QIcon is not comparable, and `isNull()` says only "some"."""
    return item.icon(0).pixmap(16).toImage()


def _standard_image(dock, standard):
    """The same pixels for a QStyle.StandardPixmap, from the SAME style the dock
    draws with — a second style() call would compare two different themes."""
    return dock.style().standardIcon(standard).pixmap(16).toImage()


# ═══════════════════════════════════════════════════════════════════════════
# Сущность — ребёнок своей ячейки/импринта; раздела «Entities» нет (п.1, 3)
# ═══════════════════════════════════════════════════════════════════════════

def test_entity_shows_under_its_cell(main_window, tmp_path):
    """С1 плана: сущность — ребёнок ячейки, а раздела «Entities» нет."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c"}]})
    dock = _dock(main_window, root)
    fi = file_item(dock.tree, root)

    cell = find_child(category(fi, "cells"), "c")
    assert find_child(cell, "e1").text(0) == "e1"
    assert not _has_category(fi, "entities"), "раздела Entities быть не должно"


def test_two_entities_of_one_cell_are_both_shown_sorted(main_window, tmp_path):
    """С3 (мутация «вторая сущность не показана»): обе, по имени."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "zeta", "cell": "c"},
                                     {"name": "alpha", "cell": "c"}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    names = [cell.child(i).text(0) for i in range(cell.childCount())]
    assert names == ["alpha", "zeta"]


def test_entity_shows_under_its_imprint(main_window, tmp_path):
    """С4 (мутация «сущность импринта не показана»)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"imprints": [minimal_imprint("amp")],
                        "entities": [{"name": "e1", "imprint": "amp"}]})
    dock = _dock(main_window, root)
    fi = file_item(dock.tree, root)
    imp = find_child(category(fi, "imprints"), "amp")
    assert find_child(imp, "e1").text(0) == "e1"
    assert not _has_category(fi, "entities")


# ═══════════════════════════════════════════════════════════════════════════
# Сирота-сущность — в разделе «Entities» своего файла, с подсказкой (п.3, 3а)
# ═══════════════════════════════════════════════════════════════════════════

def test_orphan_stays_in_its_files_entities_section_with_a_hint(
        main_window, tmp_path):
    """С5 (мутация «сирота пропадает из дерева совсем»): лист в разделе
    Entities, подсказка называет близкое имя (close_name_hint)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"good": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "god"}]})
    dock = _dock(main_window, root)
    fi = file_item(dock.tree, root)

    leaf = find_child(category(fi, "entities"), "e1")
    tip = leaf.toolTip(0)
    assert "refers to a missing cell 'god'" in tip, tip
    assert "'good'" in tip, (
        "подсказка обязана прийти из close_name_hint (difflib), не своим код: " + tip)


def test_entities_section_absent_when_there_are_no_orphans(main_window, tmp_path):
    """С10 (мутация «раздел Entities показан при отсутствии сирот»)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c"}]})
    dock = _dock(main_window, root)
    assert not _has_category(file_item(dock.tree, root), "entities")


# ═══════════════════════════════════════════════════════════════════════════
# Ячейка без сущности (3б): пометка, вид — по графу (мутация 8)
# ═══════════════════════════════════════════════════════════════════════════

def test_unused_cell_without_entity_is_marked(main_window, tmp_path):
    """С8: неиспользуемая ячейка без сущности помечена, подсказка зовёт
    создать сущность."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")

    assert _mark(cell) == _CELL_UNUSED
    assert _icon_image(cell) == _standard_image(
        dock, QStyle.StandardPixmap.SP_MessageBoxWarning), (
        "жёлтый «!» — именно Warning: само наличие значка ничего не значит")
    assert "is not placed" in cell.toolTip(0)


def test_cell_placed_without_entity_is_marked_as_placed(main_window, tmp_path):
    """С8 (вторая клетка таблицы): вид «ставится БЕЗ сущности» — по графу
    (спица цепочки), и подсказка называет, кто ставит."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "chains": [{"name": "ch1", "net": "N",
                                    "spokes": [{"pad": "1", "cell": "c"}]}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")

    assert _mark(cell) == _CELL_PLACED
    assert _icon_image(cell) == _standard_image(
        dock, QStyle.StandardPixmap.SP_MessageBoxInformation), (
        "синее «i» — именно Information")
    tip = cell.toolTip(0)
    assert "placed by" in tip and "ch1" in tip, tip


def test_cell_with_entity_is_not_marked(main_window, tmp_path):
    """С8 (обратная клетка): ячейка С сущностью — без пометки."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c"}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")

    assert _mark(cell) is None
    assert cell.icon(0).isNull()


# ═══════════════════════════════════════════════════════════════════════════
# Лист сущности несёт СВОЙ файл — задел п.4 (отдельные клетки в Т3)
# ═══════════════════════════════════════════════════════════════════════════

def test_entity_leaf_carries_its_own_declaring_file(main_window, tmp_path):
    """Сущность в B под ячейкой из A: лист несёт СВОЙ файл B, не файл A —
    отсюда все пути маршрутизации берут цель записи (п.4)."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "ent_b.sexp"
    write_config(sub, {"entities": [{"name": "e1", "cell": "c"}]})
    write_config(root, {"include": ["ent_b.sexp"],
                        "cells": {"c": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)

    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    leaf = find_child(cell, "e1")
    own = leaf.data(0, _ROLE_OWN_FILE)
    assert own is not None
    assert Path(own[0]).name == "ent_b.sexp", own


# ═══════════════════════════════════════════════════════════════════════════
# Свой файл записи — ОДНО правило для всех путей (п.4; мутация 1 и файловый блок)
# ═══════════════════════════════════════════════════════════════════════════

def _cross_file_dock(main_window, tmp_path):
    """Root A (with the cell) INCLUDES file B (with the entity) — the exact
    shape п.4 guards. Returns (dock, root_a, sub_b, entity_leaf)."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "ent_b.sexp"
    write_config(sub, {"entities": [{"name": "e1", "cell": "c"}]})
    write_config(root, {"include": ["ent_b.sexp"],
                        "cells": {"c": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    return dock, root, sub, find_child(cell, "e1")


def _read(path):
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def test_rename_target_and_identity_of_cross_file_entity_use_its_own_file(
        main_window, tmp_path):
    """С2 (п.4, мутация 1): и цель правки, и личность выделения — файл B."""
    dock, _root, _sub, leaf = _cross_file_dock(main_window, tmp_path)

    target = dock._rename_target_for_item(leaf)
    assert target[1:] == ("entities", "e1")
    assert Path(target[0]).name == "ent_b.sexp", target

    ident = dock._item_identity(leaf)
    assert ident[0] == "leaf" and ident[2] == "entities"
    assert Path(ident[1]).name == "ent_b.sexp", ident


def test_rename_of_cross_file_entity_writes_to_b_and_leaves_a_intact(
        main_window, tmp_path, monkeypatch):
    """С2 (клетка «переименование»): запись уходит в B, файл A цел."""
    from kicadstamp import config_working_set

    dock, root, sub, leaf = _cross_file_dock(main_window, tmp_path)
    before_a = root.read_bytes()
    # A format-3 write resolves reference UUIDs against the ACTIVE graph root;
    # a bare ConfigTreeDock tests only the dock, so the root is supplied here.
    monkeypatch.setattr(config_working_set, "active_graph_root", lambda: root)
    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("e2", True)))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_rename(*dock._rename_target_for_item(leaf))

    assert [e["name"] for e in _read(sub).get("entities") or []] == ["e2"]
    assert root.read_bytes() == before_a, "файл A не должен быть тронут"


def test_delete_of_cross_file_entity_writes_to_b_and_leaves_a_intact(
        main_window, tmp_path, monkeypatch):
    """С2 (клетка «удаление»): запись уходит в B, файл A цел."""
    from kicadstamp import config_working_set

    dock, root, sub, leaf = _cross_file_dock(main_window, tmp_path)
    before_a = root.read_bytes()
    monkeypatch.setattr(config_working_set, "active_graph_root", lambda: root)
    monkeypatch.setattr(
        config_tree_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: config_tree_mod.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_delete(*dock._rename_target_for_item(leaf))

    assert not (_read(sub).get("entities") or [])
    assert root.read_bytes() == before_a, "файл A не должен быть тронут"


def test_export_of_cross_file_entity_targets_b(main_window, tmp_path):
    """С2 (клетка «экспорт»): источник экспорта — файл B."""
    dock, _root, _sub, leaf = _cross_file_dock(main_window, tmp_path)
    leaf.setSelected(True)
    items = dock._selected_export_items()
    assert [Path(it.source_path).name for it in items] == ["ent_b.sexp"]


def test_file_block_names_the_entitys_own_file_and_acts_on_it(
        main_window, tmp_path, monkeypatch):
    """С2 (клетка «файловый блок»): пункты называют B и действуют на B."""
    dock, _root, _sub, leaf = _cross_file_dock(main_window, tmp_path)
    actions = context_menu_actions(dock, leaf, monkeypatch)
    labels = [label for label, _ in actions]
    assert any(l.startswith("Remove this file (ent_b.sexp)") for l in labels), labels
    assert any(l.startswith("Add included file (ent_b.sexp)") for l in labels), labels

    seen = []
    monkeypatch.setattr(dock, "_remove_file",
                        lambda fp, pp: seen.append((Path(fp).name, Path(pp).name)))
    for label, act in actions:
        if label.startswith("Remove this file"):
            act.trigger()
    assert seen and seen[0][0] == "ent_b.sexp", seen


def test_file_block_is_plain_when_the_entity_file_is_the_visible_one(
        main_window, tmp_path, monkeypatch):
    """Файл тот же — текст прежний (имя файла НЕ добавляется). Здесь и ячейка,
    и сущность живут в B; лист визуально под B, значит прятать нечего."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "both_b.sexp"
    write_config(sub, {"cells": {"c": {"components": [{"role": "R"}]}},
                       "entities": [{"name": "e1", "cell": "c"}]})
    write_config(root, {"include": ["both_b.sexp"]})
    dock = _dock(main_window, root)

    sub_item = file_item(dock.tree, sub)
    cell = find_child(category(sub_item, "cells"), "c")
    leaf = find_child(cell, "e1")
    labels = [label for label, _ in context_menu_actions(dock, leaf, monkeypatch)]
    assert "Remove this file" in labels, labels
    assert not any("(" in l and "this file" in l for l in labels), labels


# ═══════════════════════════════════════════════════════════════════════════
# Сирота-сущность: меню из двух пунктов и «Point to …» (3а)
# ═══════════════════════════════════════════════════════════════════════════

def _orphan_dock(main_window, tmp_path):
    """An orphan entity in file B naming a MISSING cell 'god'; a cell 'good'
    exists in root A. The graph is dangling — load_config would be FATAL, which
    is exactly the case the raw index exists for."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "ent_b.sexp"
    write_config(sub, {"entities": [{"name": "e1", "cell": "god"}]})
    write_config(root, {"include": ["ent_b.sexp"],
                        "cells": {"good": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)
    leaf = find_child(category(file_item(dock.tree, sub), "entities"), "e1")
    return dock, root, sub, leaf


def test_orphan_menu_is_only_point_and_delete(main_window, tmp_path, monkeypatch):
    """3а: у сироты — ровно «Point to cell…» и «Delete entity»; ни Rename, ни
    платных пунктов, ни «Edit cell...»."""
    dock, _root, _sub, leaf = _orphan_dock(main_window, tmp_path)
    labels = [label for label, _ in context_menu_actions(dock, leaf, monkeypatch)]

    assert "Point to cell…" in labels, labels
    assert "Delete entity" in labels, labels
    for forbidden in ("Rename...", "Delete...", "Select cell",
                      "Select cell components", "Select enclosed copper",
                      "Explode…", "Edit cell..."):
        assert forbidden not in labels, (forbidden, labels)


def test_point_to_cell_writes_name_and_uuid_into_the_entitys_own_file(
        main_window, tmp_path, monkeypatch):
    """3а (мутация 6): «Point to cell…» пишет cell И cell_uuid в СВОЙ файл B
    (uuid — из СЫРОГО индекса, не из load_config), файл A цел; после refresh()
    сущность уходит под найденную ячейку."""
    dock, root, sub, leaf = _orphan_dock(main_window, tmp_path)
    before_a = root.read_bytes()
    monkeypatch.setattr(
        config_tree_mod.QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("good", True)))

    actions = context_menu_actions(dock, leaf, monkeypatch)
    point = next(act for label, act in actions if label.startswith("Point to cell"))
    # The writer's format-3 stamp RESOLVES a new reference by name, so it would
    # fill the uuid even if the tree forgot it. Disable the stamp here, so this
    # guard pins the uuid the TREE writes (mutation "writes the name only").
    with format3_stamp_disabled():
        point.trigger()

    entity = _read(sub)["entities"][0]
    assert entity["cell"] == "good"
    assert entity["cell_uuid"] == dock._entity_index.target_uuid("cells", "good")
    assert "imprint" not in entity and "imprint_uuid" not in entity
    assert root.read_bytes() == before_a, "файл A не должен быть тронут"

    # After refresh the entity is attached under the cell in A, and the orphan
    # section in B is gone.
    assert not _has_category(file_item(dock.tree, sub), "entities")
    cell = find_child(category(file_item(dock.tree, root), "cells"), "good")
    assert find_child(cell, "e1")


def test_point_preselects_the_close_name_hint(main_window, tmp_path, monkeypatch):
    """3а: в выборе ПРЕДВЫБРАНА подсказка близкого имени (close_name)."""
    from kicadstamp import config_working_set

    dock, root, _sub, leaf = _orphan_dock(main_window, tmp_path)
    monkeypatch.setattr(config_working_set, "active_graph_root", lambda: root)
    seen = {}

    def _get_item(parent, title, label, items, current=0, editable=True, *a, **k):
        seen["names"] = list(items)
        seen["current"] = current
        return ("good", False)  # declined — nothing is written

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getItem",
                        staticmethod(_get_item))
    actions = context_menu_actions(dock, leaf, monkeypatch)
    next(act for label, act in actions if label.startswith("Point to cell")).trigger()

    assert seen["names"][seen["current"]] == "good", seen


# ═══════════════════════════════════════════════════════════════════════════
# Ячейка без сущности: неиспользуемая (3б) — меню из двух, страница read-only
# ═══════════════════════════════════════════════════════════════════════════

def test_unused_cell_menu_is_only_create_entity_and_delete(
        main_window, tmp_path, monkeypatch):
    """3б (мутация 9): у неиспользуемой ячейки без сущности — только «Create
    entity» и «Delete…»; ни «Edit cell...», ни платных пунктов, ни Rename."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    labels = [label for label, _ in context_menu_actions(dock, cell, monkeypatch)]

    assert "Create entity" in labels, labels
    assert "Delete..." in labels, labels
    for forbidden in ("Edit cell...", "Cell anchor...", "Update from selection...",
                      "Add selected copper...", "Subtract selected copper...",
                      "Select cell", "Select cell components", "Explode…",
                      "Rename...", "Copy placement from cell..."):
        assert forbidden not in labels, (forbidden, labels)


def test_placed_cell_without_entity_keeps_the_full_menu(
        main_window, tmp_path, monkeypatch):
    """3б: ячейка, поставленная спицей БЕЗ сущности, работает как сегодня —
    правится, имеет прежние пункты (чтобы не сломать живые спицы)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "chains": [{"name": "ch1", "net": "N",
                                    "spokes": [{"pad": "1", "cell": "c"}]}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    labels = [label for label, _ in context_menu_actions(dock, cell, monkeypatch)]

    for expected in ("Create entity", "Edit cell...", "Update from selection...",
                     "Rename...", "Delete..."):
        assert expected in labels, (expected, labels)


# 2б, п.3: the page's board buttons, by THEIR OWN names.
#
# `isEnabled()` alone is NOT enough: a disabled ANCESTOR (the tab widget) makes
# every child read as disabled, so a guard on it would be green for the wrong
# reason — exactly what Denis refused ("сторож, зелёный чужим эффектом, не
# годится"). Qt marks the widget that was disabled BY ITS OWN CALL with
# WA_ForceDisabled, which stays False when only the parent is off, so the second
# half of the guard reads THAT flag — and the mutation "the gate no longer
# touches the buttons" turns it red.
_BOARD_BUTTONS = ("_read_selection_button", "_fill_selection_button",
                  "_set_anchor_button", "_clear_anchor_button",
                  "_place_marker_button", "_show_bbox_button",
                  "_read_marker_button", "_remove_marker_button",
                  "_hide_bbox_button", "_remove_overlay_button")
# The buttons the PAGE itself turns ON for a valid cell (`_reload_form`): for
# them "off" has ONE explanation — the gate.
_PAGE_ENABLED_BUTTONS = ("_read_selection_button", "_fill_selection_button",
                         "_set_anchor_button", "_clear_anchor_button",
                         "_place_marker_button", "_show_bbox_button")


def test_click_on_unused_cell_opens_the_cell_page_read_only(
        real_main_window, tmp_path):
    """3б + 2б, п.3: щелчок по неиспользуемой ячейке открывает страницу ячейки
    ТОЛЬКО ДЛЯ ЧТЕНИЯ — поля недоступны, видна строка-подсказка, и КНОПКИ ПЛАТЫ
    выключены САМИ (одна точка гейта), а не только «вся вкладка»."""
    from PyQt6.QtCore import Qt
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}}})
    open_project(hub, root)
    leaf = find_child(category(file_item(hub.config_tree_dock.tree, root), "cells"), "c")

    hub.config_tree_dock._on_clicked(leaf, 0)

    view = hub.cell_anchor_view
    assert not view._tabs.isEnabled(), "поля должны быть недоступны"
    assert not view._read_only_gate.note.isHidden()
    for name in _BOARD_BUTTONS:
        assert not getattr(view, name).isEnabled(), name
    for name in _PAGE_ENABLED_BUTTONS:
        assert getattr(view, name).testAttribute(
            Qt.WidgetAttribute.WA_ForceDisabled), \
            f"{name} выключена НЕ гейтом — сторож был бы зелёным чужим эффектом"


def test_click_on_cell_with_entity_opens_the_cell_page_editable(
        real_main_window, tmp_path):
    """Обратная клетка: ячейка С сущностью открывается как прежде — правимой,
    и кнопки платы гейт НЕ трогает (их включает сама страница по своим правилам)."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c"}]})
    open_project(hub, root)
    cell = find_child(category(file_item(hub.config_tree_dock.tree, root), "cells"), "c")

    hub.config_tree_dock._on_clicked(cell, 0)

    view = hub.cell_anchor_view
    assert view._tabs.isEnabled()
    assert view._read_only_gate.note.isHidden()
    assert view._read_selection_button.isEnabled()


# ═══════════════════════════════════════════════════════════════════════════
# Доделка 1а: ромб include: и выделение из другого файла (п.1, п.2)
# ═══════════════════════════════════════════════════════════════════════════

def _diamond_root(tmp_path) -> Path:
    """root → a, b, и ОБА подключают shared.sexp: одна и та же запись
    достижима двумя ветками — тот самый ромб."""
    write_config(tmp_path / "shared.sexp",
                 {"cells": {"c": {"components": [{"role": "R"}]},
                            "c2": {"components": [{"role": "R"}]}},
                  "entities": [{"name": "e1", "cell": "c"}],
                  "chains": [{"name": "ch", "net": "N",
                              "spokes": [{"pad": "1", "cell": "c2"}]}]})
    write_config(tmp_path / "a.sexp", {"include": ["shared.sexp"]})
    write_config(tmp_path / "b.sexp", {"include": ["shared.sexp"]})
    root = tmp_path / "root.sexp"
    write_config(root, {"include": ["a.sexp", "b.sexp"]})
    return root


def test_a_diamond_include_shows_the_entity_and_the_placer_once(
        main_window, tmp_path):
    """Доделка 1а, п.1 (дерево) — файл, подключённый двумя ветками, не должен
    давать ДВУХ одинаковых листьев под одной ячейкой и не должен повторять
    одного постановщика в подсказке «placed by» (часть 2, п.2а: ячейку ставят
    только спицы — «is placed by chain(s) ch», и свойство то же).

    Мутация: снять пропуск по `node.path` из `_iter_nodes` — этот сторож
    краснеет (два листа `e1`; цепочка в подсказке дважды)."""
    root = _diamond_root(tmp_path)
    dock = _dock(main_window, root)
    cells = category(file_item(dock.tree, tmp_path / "shared.sexp"), "cells")

    with_entity = find_child(cells, "c")
    assert with_entity.childCount() == 1, (
        "под ячейкой — ОДИН лист сущности; два значит, что файл собран дважды")

    placed = find_child(cells, "c2")
    assert placed.toolTip(0).count("chain(s) ch") == 1, placed.toolTip(0)


def test_the_selection_of_an_entity_from_another_file_survives_refresh(
        main_window, tmp_path):
    """Доделка 1а, п.2 — сущность показана под ячейкой из ДРУГОГО файла, и её
    выделение обязано пережить `refresh()`: дерево перестраивается с нуля, а
    лист живёт под чужим файловым узлом.

    Идентичность листа берётся из СВОЕГО файла сущности (`_ROLE_OWN_FILE`), а
    не из файла видимого предка — иначе после перестройки лист не нашёлся бы.

    Мутация: в `_item_identity` взять файл родителя вместо `_ROLE_OWN_FILE` —
    этот сторож краснеет (выделение не восстановилось)."""
    write_config(tmp_path / "a.sexp",
                 {"cells": {"c": {"components": [{"role": "R"}]}}})
    entity_file = tmp_path / "b.sexp"
    write_config(entity_file, {"entities": [{"name": "e1", "cell": "c"}]})
    root = tmp_path / "root.sexp"
    write_config(root, {"include": ["a.sexp", "b.sexp"]})

    dock = _dock(main_window, root)
    cell = find_child(
        category(file_item(dock.tree, tmp_path / "a.sexp"), "cells"), "c")
    find_child(cell, "e1").setSelected(True)

    dock.refresh()

    selected = dock.tree.selectedItems()
    assert len(selected) == 1, [i.text(0) for i in selected]
    own = Path(selected[0].data(0, _ROLE_OWN_FILE)[0]).resolve()
    assert own == entity_file.resolve()


def test_the_selection_picks_its_own_file_among_same_named_entities(
        main_window, tmp_path):
    """Доделка 1а, п.2, второй случай — две сущности с ОДНИМ ИМЕНЕМ в разных
    файлах под одной ячейкой: файл в идентичности листа различает их, поэтому
    после `refresh()` выделена ТА ЖЕ (ровно одна), а не обе.

    Мутация: в `_item_identity` взять файл родителя — обе сущности получают
    одну идентичность, и выделенными окажутся обе (сторож краснеет)."""
    write_config(tmp_path / "a.sexp",
                 {"cells": {"c": {"components": [{"role": "R"}]}},
                  "entities": [{"name": "e", "cell": "c"}]})
    second = tmp_path / "b.sexp"
    write_config(second, {"entities": [{"name": "e", "cell": "c"}]})
    root = tmp_path / "root.sexp"
    write_config(root, {"include": ["a.sexp", "b.sexp"]})

    dock = _dock(main_window, root)
    cell = find_child(
        category(file_item(dock.tree, tmp_path / "a.sexp"), "cells"), "c")
    leaves = [cell.child(i) for i in range(cell.childCount())]
    wanted = next(leaf for leaf in leaves
                  if Path(leaf.data(0, _ROLE_OWN_FILE)[0]).resolve()
                  == second.resolve())
    wanted.setSelected(True)

    dock.refresh()

    selected = dock.tree.selectedItems()
    assert len(selected) == 1, [i.text(0) for i in selected]
    own = Path(selected[0].data(0, _ROLE_OWN_FILE)[0]).resolve()
    assert own == second.resolve()


# ═══════════════════════════════════════════════════════════════════════════
# Доделка 1а: подсказка без повторов, значок сироты, пункты меню (п.4–п.6)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_placed_by_hint_names_each_placer_once(main_window, tmp_path):
    """Доделка 1а, п.4 — три спицы ОДНОЙ цепочки ставят одну ячейку: подсказка
    называет постановщика ОДИН раз, с числом. На живом профиле
    `mcu_pwr_bank` это читалось как «spoke of chain: MCU Vdd» трижды.

    Часть 2 (п.2а) сменила СЛОВА подсказки для такой ячейки: её ставит спица, и
    «no entity» в ней не при чём — теперь «is placed by chain(s) MCU Vdd (3)».
    Свойство, ради которого сторож написан, то же: одна цепочка — одно упоминание,
    и число на виду (мутация «снять группировку по имени» — краснеет)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "chains": [{"name": "MCU Vdd", "net": "N",
                                    "spokes": [{"pad": str(n), "cell": "c"}
                                               for n in (1, 2, 3)]}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")

    tooltip = cell.toolTip(0)
    assert tooltip.count("MCU Vdd") == 1, tooltip
    assert "(3)" in tooltip, tooltip


def test_a_cell_placed_only_by_chains_names_the_chains(main_window, tmp_path):
    """п.2а: ячейку ставят ТОЛЬКО спицы — подсказка говорит «is placed by
    chain(s)», без «has no entity»: спица — законный адрес экземпляра, и слово
    «сирота» к такой ячейке не относится (жёлтый «!» тоже остаётся незанятым)."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "chains": [
                            {"name": "MCU Vdd", "net": "N1",
                             "spokes": [{"pad": "A1", "cell": "c"},
                                        {"pad": "A2", "cell": "c"}]},
                            {"name": "FPGA PWR", "net": "N2",
                             "spokes": [{"pad": "B1", "cell": "c"}]}]})
    dock = _dock(main_window, root)
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")

    assert _mark(cell) == _CELL_PLACED
    assert _icon_image(cell) == _standard_image(
        dock, QStyle.StandardPixmap.SP_MessageBoxInformation)
    tip = cell.toolTip(0)
    assert "is placed by chain(s)" in tip, tip
    assert "MCU Vdd (2)" in tip and "FPGA PWR" in tip, tip
    assert "has no entity" not in tip, tip


def test_a_cell_placed_by_anything_but_chains_keeps_the_no_entity_wording(
        main_window, tmp_path):
    """Смешанный случай: помимо спицы ячейку ставит clone_placement — тут
    «no entity» и есть суть, и подсказка называет ВИДЫ постановщиков, как в части 1."""
    from gui.docks.entity_index import PLACED_BY_CLONE, PlacedBy

    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}}})
    dock = _dock(main_window, root)

    hint = dock._placed_hint("c", [
        PlacedBy("spoke", "chains", {"name": "MCU Vdd"}),
        PlacedBy(PLACED_BY_CLONE, "clone_placements", {"cluster": "CL"})])

    assert "has no entity" in hint and "spoke of chain" in hint, hint


def test_an_orphan_entity_carries_a_mark_not_only_a_hint(main_window, tmp_path):
    """Доделка 1а, п.5 — у сироты-сущности была ТОЛЬКО подсказка, а п.3 задания
    требует и пометку, как у пометок ячеек. Значок — Critical: висячая ссылка
    в формате 3 не даёт загрузиться всему графу, это тяжелее «ячейка нигде не
    стоит» (warning).

    Мутация: снять `setIcon` в `_add_orphan_entities` — сторож краснеет."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "missing"}]})
    dock = _dock(main_window, root)
    leaf = find_child(category(file_item(dock.tree, root), "entities"), "e1")

    assert _icon_image(leaf) == _standard_image(
        dock, QStyle.StandardPixmap.SP_MessageBoxCritical), (
        "красный значок — именно Critical: висячая ссылка в формате 3 роняет "
        "загрузку графа, и подмена на Warning обязана краснеть")
    assert leaf.toolTip(0), "и подсказка — обе половины п.3"


# Пункт меню × его сигнал: одна таблица на оба источника (п.6).
_MENU_POINTS = (
    ("select_cell_components_action", "cell_select_components_requested"),
    ("select_cell_action", "cell_select_requested"),
    ("select_enclosed_copper_action", "cell_select_enclosed_requested"),
)


@pytest.mark.parametrize("source", ["cell", "entity"])
@pytest.mark.parametrize("action_name,signal_name", _MENU_POINTS,
                         ids=[point[0] for point in _MENU_POINTS])
def test_every_select_point_sends_the_instance_of_its_own_source(
        main_window, tmp_path, monkeypatch, source, action_name, signal_name):
    """Доделка 1а, п.6 — ПОВЕДЕНЧЕСКАЯ клетка на каждый пункт «Select …» из
    обоих меню: 3 пункта × 2 источника одной таблицей.

    Пункт ЯЧЕЙКИ шлёт (имя, файл СВОЕГО узла, None, None, None): экземпляр
    выбирается потом, по контексту. Пункт СУЩНОСТИ шлёт (cell, None, cluster,
    sheet, имя СуЩНОСТИ) — файл None (файл сущности не должен стать целью записи
    ячейки), экземпляр берётся из записи сущности, а пятое поле (2б, п.4) —
    ИМЯ этой сущности, которым дверь публикует рабочий экземпляр.

    Прежние сторожа читали ТЕКСТ исходника, поэтому соседний пункт с тем же
    хвостом их удовлетворял; здесь ловится СИГНАЛ.

    Мутация: `lambda: None` у пункта ячейки (`entity_tree.py`) или `c=None,
    s=None` у пункта сущности (`config_tree.py`) — краснеет строка именно этого
    пункта."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c",
                                      "cluster": "CL", "sheet": "S1"}]})
    dock = _dock(main_window, root)
    cells = category(file_item(dock.tree, root), "cells")
    if source == "cell":
        item = find_child(cells, "c")
    else:
        item = find_child(find_child(cells, "c"), "e1")

    seen: list = []
    getattr(dock, signal_name).connect(lambda *a: seen.append(a))
    action = next(
        act for _label, act in context_menu_actions(dock, item, monkeypatch)
        if act.objectName() == action_name)
    action.trigger()

    assert len(seen) == 1, seen
    name, file_arg, cluster, sheet, entity = seen[0]
    assert name == "c"
    if source == "cell":
        assert Path(file_arg).resolve() == root.resolve()
        assert (cluster, sheet) == (None, None)
        assert entity is None, "у пункта ЯЧЕЙКИ пришпиливать нечего"
    else:
        assert file_arg is None, "файл сущности не должен стать целью записи"
        assert (cluster, sheet) == ("CL", "S1")
        assert entity == "e1", "дверь сущности называет СВОЮ сущность (2б, п.4)"


def _signal_recorder(dock, signal_name):
    """Collect every payload a dock emits on `signal_name` (a list the guard
    reads afterwards) — the menu is driven by a real QAction, so the guard sees
    what the tree really sends, not what its source text says."""
    seen: list = []
    getattr(dock, signal_name).connect(lambda *a: seen.append(a))
    return seen


def test_the_entity_leaf_opens_the_cell_page_on_that_entity(
        main_window, tmp_path, monkeypatch):
    """2б, п.4: у листа-сущности ОДИН пункт «Edit cell...», и он называет эту
    сущность — страница открывается НА НЕЙ (`opened_from`), а не на последней
    или первой сущности ячейки. У листа ЯЧЕЙКИ такого пункта нет: дверь ячейки
    экземпляр не называет."""
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c",
                                      "cluster": "CL", "sheet": "S1"},
                                     {"name": "e2", "cell": "c",
                                      "cluster": "CL2", "sheet": "S2"}]})
    dock = _dock(main_window, root)
    cells = category(file_item(dock.tree, root), "cells")
    seen = _signal_recorder(dock, "cell_anchor_entity_requested")

    actions = context_menu_actions(dock, find_child(find_child(cells, "c"),
                                                    "e2"), monkeypatch)
    action = next((act for _label, act in actions
                   if act.objectName() == "edit_cell_for_entity_action"), None)
    assert action is not None, [label for label, _act in actions]
    action.trigger()

    assert len(seen) == 1, seen
    name, file_arg, entity = seen[0]
    assert (name, entity) == ("c", "e2")
    assert Path(file_arg).resolve() == root.resolve()

    cell_actions = context_menu_actions(dock, find_child(cells, "c"), monkeypatch)
    assert not any(act.objectName() == "edit_cell_for_entity_action"
                   for _label, act in cell_actions), \
        "пункт «на этой сущности» бывает только у листа-сущности"


def test_the_entity_door_publishes_that_entity_as_the_working_instance(tmp_path):
    """2б, п.4 (мутация «выбор по последней»): дверь сущности пишет СВОЮ
    сущность в ОДНО хранилище рабочего экземпляра, поэтому действие, которое
    идёт следом, читает её канал — а не тот, на котором страница стояла
    последней (`read_instance` отвечает из хранилища в момент использования)."""
    from types import SimpleNamespace
    from gui.cell_entity_choice import read_instance, working_instance
    from gui.dock_hub import _pin_door_instance
    from gui.docks.entity_index import build_entity_index
    from kicadstamp.config.includes import walk_include_tree
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c",
                                      "cluster": "CL1", "sheet": "S1"},
                                     {"name": "e2", "cell": "c",
                                      "cluster": "CL2", "sheet": "S2"}]})
    index = build_entity_index(walk_include_tree(str(root)))
    hub = SimpleNamespace(
        config_tree_dock=SimpleNamespace(_entity_index=index),
        root_metadata_dock=SimpleNamespace(root_path=root))

    # the cell page was last on e1 (a pick, or an earlier door) …
    _pin_door_instance(hub, "c", "e1")
    assert read_instance(root, "c").cluster == "CL1"

    # … and now the door of e2 fires: the store follows the DOOR, not the page.
    _pin_door_instance(hub, "c", "e2")
    row = working_instance(root, "c")
    assert row is not None and row.entity_name == "e2"
    assert read_instance(root, "c").cluster == "CL2"

    # A name the graph no longer knows clears the record — the door's own
    # explicit (cluster, sheet) is then in charge, never a stale entity.
    _pin_door_instance(hub, "c", "gone")
    assert working_instance(root, "c") is None

    # A CELL leaf names no entity: nothing is written at all.
    _pin_door_instance(hub, "c", None)
    assert working_instance(root, "c") is None
