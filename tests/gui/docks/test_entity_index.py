# tests/gui/docks/test_entity_index.py
"""Сторожа ИНДЕКСА «ячейка/импринт → их сущности» и «кто ставит ячейку»
(gui/docks/entity_index.py) — заход plan_2026_10_05_entities_under_cells.

Слой: индекс зовётся НАПРЯМУЮ, без дока и без QApplication. Это и есть условие
модуля («ни одного импорта Qt») — если бы он тянул PyQt6, эти сторожа упали бы
на импорте.

Граф — из ДВУХ файлов через include:, чтобы связь по UUID проверялась НЕ на
одном файле: сущность лежит в B, ячейка — в A.

Имена функций описывают СВОЙСТВО (правило 37), номер клетки плана — в
докстринге. Фикстуры формата 3 несут uuid ЯВНО (дерево поднимает только
до-форматные файлы; формат 3 как есть).
"""
from pathlib import Path

from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp
from gui.docks.entity_index import (
    PLACED_BY_CLONE,
    PLACED_BY_NESTED,
    PLACED_BY_SPOKE,
    build_entity_index,
)

CELL_A = "00000000-0000-0000-0000-0000000000a1"
CELL_B = "00000000-0000-0000-0000-0000000000b1"
IMP = "00000000-0000-0000-0000-0000000000c1"


def _cell(uuid: str) -> dict:
    return {"uuid": uuid, "components": [{"role": "R"}]}


def _entity(name: str, *, cell=None, cell_uuid=None, imprint=None,
            imprint_uuid=None, extra: dict | None = None) -> dict:
    rec = {"name": name, "uuid": f"ent-{name}"}
    if cell is not None:
        rec["cell"] = cell
    if cell_uuid is not None:
        rec["cell_uuid"] = cell_uuid
    if imprint is not None:
        rec["imprint"] = imprint
    if imprint_uuid is not None:
        rec["imprint_uuid"] = imprint_uuid
    if extra:
        rec.update(extra)
    return rec


def _write(path: Path, data: dict, fmt: int = 3) -> None:
    path.write_text(dict_to_sexp(data, format_number=fmt), encoding="utf-8")


def _index(tmp_path: Path, root_data: dict, sub_data: dict | None = None,
           fmt: int = 3):
    root = tmp_path / "root.sexp"
    if sub_data is not None:
        _write(tmp_path / "sub.sexp", sub_data, fmt)
        root_data = {**root_data, "include": ["sub.sexp"]}
    _write(root, root_data, fmt)
    return build_entity_index(walk_include_tree(str(root))), root


# ═══════════════════════════════════════════════════════════════════════════
# Индекс Qt-free (условие модуля)
# ═══════════════════════════════════════════════════════════════════════════

def test_index_module_has_no_qt_import():
    """Условие модуля: НИ ОДНОГО импорта Qt. Статическая проверка исходника —
    надёжнее прогона (в общем pytest-сеансе PyQt6 уже в sys.modules)."""
    import gui.docks.entity_index as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for token in ("PyQt6", "QtCore", "QtWidgets", "QtGui"):
        assert token not in src, f"entity_index не должен упоминать {token!r}"


# ═══════════════════════════════════════════════════════════════════════════
# Сущность — под своей ячейкой (п.1)
# ═══════════════════════════════════════════════════════════════════════════

def test_entity_is_indexed_under_its_cell(tmp_path):
    """С1 плана: сущность видна под ячейкой, на которую ссылается ПО UUID."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "entities": [_entity("e1", cell="c_a", cell_uuid=CELL_A)],
    })
    refs = idx.entities_for_cell(CELL_A)
    assert [r.name for r in refs] == ["e1"]
    assert idx.orphans == ()


def test_two_entities_of_one_cell_are_both_shown_sorted(tmp_path):
    """С3 (мутация «вторая сущность не показана»): обе сущности — по имени."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "entities": [
            _entity("zeta", cell="c_a", cell_uuid=CELL_A),
            _entity("alpha", cell="c_a", cell_uuid=CELL_A),
        ],
    })
    assert [r.name for r in idx.entities_for_cell(CELL_A)] == ["alpha", "zeta"]


def test_entity_is_indexed_under_its_imprint(tmp_path):
    """С4 (мутация «сущность импринта не показана»)."""
    idx, _ = _index(tmp_path, {
        "imprints": [{"name": "amp", "uuid": IMP}],
        "entities": [_entity("e1", imprint="amp", imprint_uuid=IMP)],
    })
    assert [r.name for r in idx.entities_for_imprint(IMP)] == ["e1"]
    assert idx.orphans == ()


def test_entity_in_file_b_is_attached_to_cell_in_file_a(tmp_path):
    """С2 плана: сущность в B, ячейка в A (через include:) — сущность
    привязана к ячейке, а её СОБСТВЕННЫЙ файл — B (п.4: не файл ячейки)."""
    idx, _ = _index(tmp_path,
                    {"cells": {"c_a": _cell(CELL_A)}},
                    {"entities": [_entity("e1", cell="c_a", cell_uuid=CELL_A)]})
    refs = idx.entities_for_cell(CELL_A)
    assert [r.name for r in refs] == ["e1"]
    assert refs[0].file_path.name == "sub.sexp"


# ═══════════════════════════════════════════════════════════════════════════
# Связь по UUID, НЕ по имени-подсказке (мутация 2)
# ═══════════════════════════════════════════════════════════════════════════

def test_link_uses_uuid_not_the_stale_name_hint(tmp_path):
    """Мутация «связь по имени-подсказке»: имя-подсказка ВРАНЬЁ, uuid верен —
    сущность всё равно под ячейкой. Имя-подсказка не участвует."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "entities": [_entity("e1", cell="RENAMED-AND-WRONG", cell_uuid=CELL_A)],
    })
    assert [r.name for r in idx.entities_for_cell(CELL_A)] == ["e1"]
    assert idx.orphans == ()


# ═══════════════════════════════════════════════════════════════════════════
# Сироты (п.3, мутация 5)
# ═══════════════════════════════════════════════════════════════════════════

def test_entity_with_unknown_target_is_an_orphan(tmp_path):
    """С5 (мутация «сирота пропадает из дерева совсем»): цель не найдена —
    сущность в `orphans`, а не потеряна и не привязана наугад."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "entities": [_entity("e1", cell="gone", cell_uuid="no-such-uuid")],
    })
    assert idx.entities_for_cell(CELL_A) == ()
    assert [r.name for r in idx.orphans] == ["e1"]


def test_entity_without_any_reference_is_an_orphan(tmp_path):
    """Сущность без ссылки вовсе — тоже сирота (цель не найти)."""
    idx, _ = _index(tmp_path, {"entities": [_entity("bare")]})
    assert [r.name for r in idx.orphans] == ["bare"]


# ═══════════════════════════════════════════════════════════════════════════
# «Кто ставит ячейку» (3б) — тем же проходом
# ═══════════════════════════════════════════════════════════════════════════

def test_cell_placed_by_chain_spoke(tmp_path):
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "chains": [{"net": "N", "spokes": [{"pad": "1", "cell": "c_a",
                                            "cell_uuid": CELL_A}]}],
    })
    placed = idx.placed_by_cell(CELL_A)
    assert [p.kind for p in placed] == [PLACED_BY_SPOKE]
    assert placed[0].section == "chains"


def test_cell_placed_by_clone_placement_and_nested(tmp_path):
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A),
                  "c_outer": {**_cell(CELL_B),
                              "clone_placements": [{"name": "n1", "cell": "c_a",
                                                    "cell_uuid": CELL_A}]}},
        "clone_placements": [{"name": "cp", "cell": "c_a",
                              "cell_uuid": CELL_A}],
    })
    kinds = sorted(p.kind for p in idx.placed_by_cell(CELL_A))
    assert kinds == sorted([PLACED_BY_CLONE, PLACED_BY_NESTED])


def test_cell_referenced_by_name_only_is_not_placed(tmp_path):
    """Ссылка без `*_uuid` (сырая запись) цель не называет — «кто ставит» по
    ней не строится: и тут, и на стороне сущностей правило одно — UUID."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": _cell(CELL_A)},
        "clone_placements": [{"name": "cp", "cell": "c_a"}],
    })
    assert idx.placed_by_cell(CELL_A) == ()


# ═══════════════════════════════════════════════════════════════════════════
# Инвентарь и «Point to …» (3а): имена и uuid ИЗ СЫРОГО графа
# ═══════════════════════════════════════════════════════════════════════════

def test_names_and_target_uuid_come_from_the_raw_graph(tmp_path):
    idx, _ = _index(tmp_path,
                    {"cells": {"c_a": _cell(CELL_A)}},
                    {"imprints": [{"name": "amp", "uuid": IMP}]})
    assert idx.names_for("cells") == ["c_a"]
    assert idx.names_for("imprints") == ["amp"]
    assert idx.target_uuid("cells", "c_a") == CELL_A
    assert idx.target_uuid("imprints", "amp") == IMP
    assert idx.target_uuid("cells", "missing") is None


def test_format2_graph_is_lifted_before_the_index_reads_it(tmp_path):
    """Формат 2 на диске: читатель поднимает его до 3 (uuid по имени), и
    индекс всё равно связывает сущность с ячейкой — на формате 2 uuid в
    байтах нет, но подъём их выводит детерминированно."""
    idx, _ = _index(tmp_path, {
        "cells": {"c_a": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e1", "cell": "c_a"}],
    }, fmt=2)
    target = next(iter(idx.cells_by_name.values()))
    assert target.uuid
    assert [r.name for r in idx.entities_for_cell(target.uuid)] == ["e1"]


# ═══════════════════════════════════════════════════════════════════════════
# Ромб include: файл собирается ОДИН раз (доделка 1а, п.1)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_diamond_include_is_collected_once(tmp_path):
    """Доделка 1а, п.1 — ромб include:. `walk_include_tree` строит ОТДЕЛЬНЫЙ
    узел на каждый вход в файл, поэтому `shared.sexp` достижим и через
    `a.sexp`, и через `b.sexp`. Без дедупа по пути его записи собирались бы
    дважды: сущность двоилась бы под своей ячейкой, а один и тот же
    постановщик попадал бы в «placed by» два раза.

    Ячейки и импринты двоения не показывали (их таблицы собираются
    `setdefault`), а СПИСКИ `entities` — да: ровно поэтому дедуп нужен обходу,
    а не таблицам.

    Мутация: снять пропуск по `node.path` из `_iter_nodes` — этот сторож
    краснеет (сущность и постановщик придут по два раза)."""
    _write(tmp_path / "shared.sexp", {
        "cells": {"c_a": _cell(CELL_A)},
        "entities": [_entity("e1", cell="c_a", cell_uuid=CELL_A)],
        "chains": [{"name": "ch", "net": "N",
                    "spokes": [{"pad": "1", "cell": "c_a", "cell_uuid": CELL_A}]}],
    })
    _write(tmp_path / "a.sexp", {"include": ["shared.sexp"]})
    _write(tmp_path / "b.sexp", {"include": ["shared.sexp"]})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["a.sexp", "b.sexp"]})

    idx = build_entity_index(walk_include_tree(str(root)))

    assert [r.name for r in idx.entities_for_cell(CELL_A)] == ["e1"]
    assert len(idx.placed_by_cell(CELL_A)) == 1
