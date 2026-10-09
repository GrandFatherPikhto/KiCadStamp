# tests/gui/docks/test_cell_anchor_entity_dropdown.py
"""Сторожа выпадашки «Entity» на табе Source страницы ячейки — п.1 части 2
задания plan_2026_10_05_entities_under_cells.

Слой: страница зовётся как её зовёт док (load_entry), но БЕЗ живого дока: граф —
настоящий файл формата 3, индекс — тот же build_entity_index части 1, что и в
дереве. Клик по выпадашке воспроизводится индексом комбобокса (setCurrentIndex),
то есть настоящим путём виджета, а не вызовом помощника.

Имена функций описывают СВОЙСТВО (правило 37), номер пункта плана — в докстринге.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import gui.docks.cell_anchor_view as view_mod
from gui import settings
from gui.cell_edit_context import (
    CELL_EDIT_CONTEXT_KEY,
    remember_cell_instance,
    remembered_cell_edit_context,
    remembered_cell_refs,
)
import gui.docks.cell_instance_mixin as mixin_mod
from gui.cell_entity_choice import (
    LAST_ENTITY_KEY,
    remembered_last_entity,
    working_instance,
)
from gui.docks.cell_anchor_view import CellAnchorView
from gui.docks.entity_index import build_entity_index
from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp

CELL = "dac_buf"


def _entity(name, cluster=None, sheet=None, refs=None) -> dict:
    rec = {"name": name, "cell": CELL}
    if cluster is not None:
        rec["cluster"] = cluster
    if sheet is not None:
        rec["sheet"] = sheet
    if refs is not None:
        rec["refs"] = refs
    return rec


def _root(tmp_path: Path, entities=()):
    """One root file with this cell and its `entities:` — plus the part-1 index
    over it, exactly the object the Config tree would hand over.

    Authoring it as FORMAT 2 is deliberate (the same shape tests/gui/
    create_entity_helpers.write_config uses): the reader LIFTS it to 3 and mints
    the uuids, so the index links the entity by uuid — and, unlike a format-3
    file, it can be WRITTEN BACK by the guards below without the GUI's active
    graph root."""
    root = tmp_path / "root.sexp"
    data = {"cells": {CELL: {"components": [{"role": "R"}]}},
            "entities": list(entities)}
    root.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return root, build_entity_index(walk_include_tree(str(root)))


def _view(main_window, root, index, *, opened_from=None) -> CellAnchorView:
    view = CellAnchorView(main_window, connection=main_window.connection,
                          parent=main_window)
    view.entity_index_provider = lambda: index
    view.set_root_path(root)
    view.load_entry(CELL, root, opened_from=opened_from)
    return view


class _Ident:
    """A duck-typed gui.cell_identification.Identification — the same shape
    remember_cell_instance / remembered_cell_refs read."""

    def __init__(self, cluster, sheet, role_to_ref):
        self.cluster = cluster
        self.sheet = sheet
        self.role_to_ref = role_to_ref


def _capture_messages(monkeypatch) -> list:
    """Collect every message the PAGE emits.

    Two modules emit them now (rule 45): the giant for its own actions and the
    instance MIXIN for the instance ones. Both are patched — a guard that watched
    only one would silently stop seeing half of them."""
    lines: list = []

    def _sink(text, *args, **kwargs):
        lines.append(str(text))

    monkeypatch.setattr(view_mod, "show_message", _sink)
    monkeypatch.setattr(mixin_mod, "show_message", _sink)
    return lines


def _labels(view) -> list:
    picker = view._entity_picker
    return [picker.itemText(i) for i in range(picker.count())]


def _pick(view, label: str) -> None:
    """Choose a row the way the USER does — by the combo's index, so the widget's
    own signal path (never a helper) is what applies the address."""
    picker = view._entity_picker
    for i in range(picker.count()):
        if picker.itemText(i) == label:
            picker.setCurrentIndex(i)
            return
    raise AssertionError(f"нет строки {label!r} в {_labels(view)}")


# ═══════════════════════════════════════════════════════════════════════════
# Список и выбор (п.1 / п.4)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_dropdown_lists_the_cells_entities_and_manual_last(main_window,
                                                               tmp_path):
    """п.1: строки — сущности ИМЕННО этой ячейки (по имени, порядок индекса
    части 1), «Manual…» — последней; по умолчанию выбрана первая по имени."""
    root, index = _root(tmp_path, [_entity("ch1", "DAC_BUF", "Channel_1"),
                                   _entity("ch0", "DAC_BUF", "Channel_0")])
    view = _view(main_window, root, index)
    assert _labels(view) == ["ch0 — DAC_BUF on Channel_0",
                             "ch1 — DAC_BUF on Channel_1",
                             "Manual…"]
    assert view._entity_picker.current_address().entity_name == "ch0"


def test_choosing_an_entity_puts_its_address_into_the_form(main_window,
                                                           tmp_path):
    """п.1: выбор сущности кладёт её (кластер, лист) в рабочие поля, а сами поля
    становятся ТОЛЬКО ПОКАЗОМ — адрес правится в форме сущности."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    view = _view(main_window, root, index)
    # A cell WITH entities opens on one of them (п.4), so the fields are already
    # the entity's address — that is the point of the dropdown.
    assert view._cluster_combo.currentText() == "DAC_BUF"
    assert view._sheet_combo.currentText() == "Channel_0"

    _pick(view, "ch1 — DAC_BUF on Channel_1")

    assert view._cluster_combo.currentText() == "DAC_BUF"
    assert view._sheet_combo.currentText() == "Channel_1"
    assert not view._cluster_combo.isEnabled()
    assert not view._sheet_combo.isEnabled()
    assert not view._refs_edit.isEnabled()
    assert not view._fill_selection_button.isEnabled()


def test_choosing_manual_hands_the_three_fields_back(main_window, tmp_path):
    """п.2: «Manual…» — прежние поля лист/кластер/refs и «Fill from selection»
    снова в руках пользователя."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0")])
    view = _view(main_window, root, index)
    _pick(view, "Manual…")
    assert view._cluster_combo.isEnabled()
    assert view._sheet_combo.isEnabled()
    assert view._refs_edit.isEnabled()
    assert view._fill_selection_button.isEnabled()


def test_the_page_opens_on_the_entity_it_was_opened_from(main_window, tmp_path):
    """п.4: открыта ИЗ сущности (её «Edit cell…» / щелчок / пункт меню) —
    выпадашка стоит на ней, а не на первой по имени."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    view = _view(main_window, root, index, opened_from="ch1")
    assert view._entity_picker.current_address().entity_name == "ch1"
    assert view._cluster_combo.currentText() == "DAC_BUF"
    assert view._sheet_combo.currentText() == "Channel_1"


def test_the_page_reopens_on_the_last_entity_chosen_for_the_cell(main_window,
                                                                 tmp_path):
    """п.4: открыта С ЯЧЕЙКИ — последняя выбранная у неё сущность (запоминается
    ИМЯ); обновление формы (переключение таба) выбора не отменяет."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    view = _view(main_window, root, index)
    _pick(view, "ch1 — DAC_BUF on Channel_1")
    assert remembered_last_entity(root, CELL) == "ch1"

    view._reload_form()                     # a tab switch / a save
    assert view._entity_picker.current_address().entity_name == "ch1"

    again = _view(main_window, root, index)  # reopened from the CELL
    assert again._entity_picker.current_address().entity_name == "ch1"


def test_a_cell_nobody_places_has_nothing_to_choose(main_window, tmp_path):
    """п.3: ячейку не ставит НИКТО и сущностей у неё нет — выпадашка ПУСТА, даже
    «Manual…» не предлагается: работать с такой ячейкой на плате нечем, и это
    состояние отдано режиму «только чтение» части 1 («create an entity…»), а не
    полям, которые всё равно ничего не прочитают."""
    root, index = _root(tmp_path, [])
    view = _view(main_window, root, index)
    assert _labels(view) == []
    assert view._entity_picker.current_address() is None


# ═══════════════════════════════════════════════════════════════════════════
# Экземпляр — из выпадашки, контекст ячейки не тронут (п.4 / п.5)
# ═══════════════════════════════════════════════════════════════════════════

def test_the_working_instance_is_the_entitys_not_the_cells_remembered_refs(
        main_window, tmp_path):
    """п.5 (мутация «явный экземпляр игнорируется»): с выбранной сущностью
    рабочий экземпляр — ЕЁ refs, а не опознанные refs ячейки; возврат на
    «Manual…» отдаёт поля контексту ячейки."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0",
                                           refs={"R_OUT": "C44"})])
    remember_cell_instance(root, CELL,
                           _Ident("OLD_CLUSTER", None, {"R": "C43"}))

    view = _view(main_window, root, index)

    assert view.active_refs() == {"R_OUT": "C44"}
    assert view._refs_edit.text() == "C44"
    _pick(view, "Manual…")
    assert view.active_refs() == {"R": "C43"}
    assert view._refs_edit.text() == "C43"


def test_an_entity_without_refs_sends_none_to_the_marker_worker(main_window,
                                                               tmp_path):
    """Доделка 2б, п.1 (блокер, живой дефект «Place marker»): сущность с парой
    (кластер, лист) и БЕЗ refs отдаёт странице `active_refs() is None`, и воркер
    ставит маркер ПО КЛАСТЕРУ. Пустая карта `{}` — вторая половина этой же
    клетки — тот самый фатал «stale identification», который Денис поймал
    вживую: `_live_cluster_frame` ветвится по `is not None`.

    Подделка платы берётся у соседа (tests/gui/docks/test_cell_anchor_view.py) —
    одна оснастка на два файла, второй подделки здесь не заводим."""
    import gui.board_overlay as bo
    from kicadstamp.exceptions import ValidationError
    from tests.gui.docks.test_cell_anchor_view import (_OverlayAdapter, _cell,
                                                       _live_cluster_fps)
    view_mod.settings.state.set(bo.OVERLAY_LAYER_KEY, "User.KiCadStamp")
    root, index = _root(tmp_path, [_entity("ch1", cluster="CL")])
    view = _view(main_window, root, index)

    refs = view.active_refs()          # ровно то, что страница отдаёт воркеру
    assert refs is None

    key = view_mod.overlay_markers.cell_anchor_key("/r", CELL, "marker")
    assert view_mod._ensure_marker_worker(
        _OverlayAdapter(_live_cluster_fps()), _cell(), "CL", "", {}, key,
        "User.KiCadStamp", refs) is not None

    with pytest.raises(ValidationError):
        view_mod._ensure_marker_worker(
            _OverlayAdapter(_live_cluster_fps()), _cell(), "CL", "", {}, key,
            "User.KiCadStamp", {})


def test_choosing_an_entity_never_writes_the_cells_remembered_context(
        main_window, tmp_path):
    """п.4 (мутация «выбор пишет remember_cell_edit_context»): выбор сущности
    оставляет контекст ячейки НЕТРОНУТЫМ — опознанные refs на месте, а имя
    сущности ложится под свой ключ."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    remember_cell_instance(root, CELL,
                           _Ident("OLD_CLUSTER", None, {"R": "C43"}))
    before = settings.state.get(CELL_EDIT_CONTEXT_KEY)

    view = _view(main_window, root, index)
    _pick(view, "ch1 — DAC_BUF on Channel_1")

    assert settings.state.get(CELL_EDIT_CONTEXT_KEY) == before
    assert remembered_cell_edit_context(root, CELL) == ("OLD_CLUSTER", None)
    assert remembered_cell_refs(root, CELL) == {"R": "C43"}
    assert remembered_last_entity(root, CELL) == "ch1"
    assert str(root) in settings.state.get(LAST_ENTITY_KEY, {})


# ═══════════════════════════════════════════════════════════════════════════
# Проводка дока дерева (п.1: индекс — ОДИН)
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# Привязка к выбранной сущности: запись говорит, чтение отказывает (п.5/п.6)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_write_under_an_entity_tells_the_user_it_applies_to_every_entity(
        main_window, tmp_path, monkeypatch):
    """п.6: запись уходит в ЯЧЕЙКУ (один на все экземпляры) — и Лог говорит это
    прямо, называя сущность, от которой пришла правка."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0")])
    # A format-3 write resolves reference uuids against the ACTIVE graph root
    # (kicadstamp.config_working_set — the GUI raises it when a project opens);
    # the reader has already LIFTED this file to format 3 on disk, so a guard that
    # writes must raise it too. monkeypatch puts it back afterwards.
    import kicadstamp.config_working_set as working_set
    monkeypatch.setattr(working_set, "_active_graph_root", root)
    view = _view(main_window, root, index)
    lines = _capture_messages(monkeypatch)

    view._write_entry(view._current_entry(), "Set as anchor")

    assert any("applies to every entity of the cell" in line for line in lines), \
        lines
    assert any("from entity 'ch0'" in line for line in lines), lines


def test_a_read_of_another_instance_is_refused_while_an_entity_is_chosen(
        main_window, tmp_path, monkeypatch):
    """п.5: с выбранной сущностью чтение привязано к ней — выделение ДРУГОГО
    экземпляра даёт красную строку, и в поля ничего не попадает."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0")])
    view = _view(main_window, root, index)
    # The worker reads the SELECTION off the adapter first, and only then the
    # (stubbed) reader runs — so the fake board still has to answer that one call.
    main_window.connection.board = SimpleNamespace(
        adapter=SimpleNamespace(get_selected_items=lambda: []))
    monkeypatch.setattr(view_mod, "read_anchor_source",
                        lambda *a, **k: {"kind": "footprint", "cluster": "OTHER",
                                         "role": "R", "pad": None})
    lines = _capture_messages(monkeypatch)

    view._on_read_from_selection()

    # часть 3, п.6: the refusal names the selection's own address too; this read
    # carries a cluster and no sheet, so the sheet half says so.
    assert lines == ["the selection is OTHER / (no sheet), not entity 'ch0' — "
                     "nothing read"], lines
    assert view._cluster_combo.currentText() == "DAC_BUF"   # untouched
    assert view._role_combo.currentText() == ""             # nothing read in
    assert view._entity_picker.current_address().entity_name == "ch0"


def test_a_pick_publishes_the_working_instance_the_cell_dock_reads(
        main_window, tmp_path):
    """п.1: страница ПУБЛИКУЕТ рабочий экземпляр в ОДНО хранилище — по нему
    CellDock строит payload в момент нажатия; своей копии он не держит, поэтому
    разъехаться с выпадашкой не может."""
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    view = _view(main_window, root, index)
    assert working_instance(root, CELL).entity_name == "ch0"   # первая по имени

    _pick(view, "ch1 — DAC_BUF on Channel_1")

    row = working_instance(root, CELL)
    assert (row.entity_name, row.cluster, row.sheet) == \
        ("ch1", "DAC_BUF", "Channel_1")

    _pick(view, "Manual…")

    assert working_instance(root, CELL) is None, \
        "«Manual…» — экземпляр задают поля самой страницы"


def test_a_door_that_names_an_entity_moves_the_open_page_too(main_window,
                                                            tmp_path):
    """2в, п.2 (мутация «дверь не трогает открытую страницу»): страница открыта
    на этой ячейке и стоит на ch0; дверь называет ch1 → И выпадашка, И хранилище
    идут на ch1. Иначе следующий «Place marker» на странице читал бы ch0, а
    кнопка CellDock — ch1."""
    from types import SimpleNamespace
    from gui.entity_doors import pin_door_instance
    root, index = _root(tmp_path, [_entity("ch0", "DAC_BUF", "Channel_0"),
                                   _entity("ch1", "DAC_BUF", "Channel_1")])
    view = _view(main_window, root, index)
    assert view._entity_picker.current_address().entity_name == "ch0"
    hub = SimpleNamespace(
        cell_anchor_view=view,
        config_tree_dock=SimpleNamespace(_entity_index=index),
        root_metadata_dock=SimpleNamespace(root_path=root))

    assert pin_door_instance(hub, CELL, "ch1") is True

    assert view._entity_picker.current_address().entity_name == "ch1"
    assert view.active_refs() == view._entity_picker.current_address().refs
    assert working_instance(root, CELL).entity_name == "ch1"


def test_the_hub_hands_the_page_the_trees_entity_index(real_main_window):
    """Проводка: страница берёт индекс у дока дерева — своего обхода графа у неё
    нет (иначе это был бы второй индекс, который модуль части 1 запрещает)."""
    hub = real_main_window._dock_hub
    sentinel = object()
    hub.config_tree_dock._entity_index = sentinel
    assert hub.cell_anchor_view.entity_index_provider() is sentinel
