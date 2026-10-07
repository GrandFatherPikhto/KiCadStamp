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

from PyQt6.QtCore import Qt

import gui.docks.config_tree as config_tree_mod
from gui.docks.config_tree import (ConfigTreeDock, _CELL_PLACED, _CELL_UNUSED,
                                   _ROLE_CELL_MARK, _ROLE_OWN_FILE)
from kicadstamp.config.sexp_format import sexp_to_dict

from tests.gui.create_entity_helpers import (category, context_menu_actions,
                                             file_item, find_child,
                                             minimal_imprint, write_config)


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
    assert not cell.icon(0).isNull(), "у пометки должен быть значок"
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
    from kicadstamp import config_working_set

    dock, root, sub, leaf = _orphan_dock(main_window, tmp_path)
    before_a = root.read_bytes()
    monkeypatch.setattr(config_working_set, "active_graph_root", lambda: root)
    monkeypatch.setattr(
        config_tree_mod.QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("good", True)))

    actions = context_menu_actions(dock, leaf, monkeypatch)
    point = next(act for label, act in actions if label.startswith("Point to cell"))
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
