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

from gui.docks.config_tree import (ConfigTreeDock, _CELL_PLACED, _CELL_UNUSED,
                                   _ROLE_CELL_MARK, _ROLE_OWN_FILE)

from tests.gui.create_entity_helpers import (category, file_item, find_child,
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
