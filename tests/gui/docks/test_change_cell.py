# tests/gui/docks/test_change_cell.py
"""Сторожа части 2 «Change cell…» (plan_2026_10_09_cells_and_entities):
выпадашка подходящих ячеек, запись cell/cell_uuid одной правкой и СТРОКА ЛОГА О
МЕДИ с числом N.

Три слоя:
  * диалог gui/docks/change_cell.py — зовётся напрямую (offscreen);
  * поток gui/docks/change_cell_flow.py — полный путь через хаб и дверь;
  * число N — прямая клетка на old_layout_copper_count с ПОДДЕЛКОЙ реестров:
    две сущности ОДНОЙ ячейки, у каждой свои ключи; N обязан считать ключи
    ТОЛЬКО этой сущности (правило is_own_key), а не все ключи старой ячейки.

Имена функций описывают СВОЙСТВО (правило 37), имя плана — в докстринге.
"""
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox

import gui.docks.change_cell_flow as flow_mod
from gui.docks.change_cell import ChangeCellDialog
from gui.docks.instance_candidates import CellCandidate
from kicadstamp.config import load_config

from tests.gui.create_entity_helpers import (
    category, context_menu_actions, file_item, find_child, open_project,
    write_config,
)


# ── Диалог ───────────────────────────────────────────────────────────────

def _row(name, fits=True, reason=""):
    return CellCandidate(name=name, uuid="u-" + name, fits=fits, reason=reason)


def _ok(dlg):
    return dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)


def _labelled(dlg, object_name):
    return [w for w in dlg.children()
            if getattr(w, "objectName", lambda: "")() == object_name]


def test_fitting_cells_are_selectable_and_unfitting_are_greyed_with_reason(qapp):
    """Подходящие — сверху и выбираемы; неподходящие — серые, с ПРИЧИНОЙ;
    ни одна ячейка не спрятана."""
    dlg = ChangeCellDialog(None, [_row("good"),
                                  _row("bad", fits=False, reason="role CAP: 1 of 2")])
    assert dlg._list.count() == 2
    good, bad = dlg._list.item(0), dlg._list.item(1)
    assert good.flags() & Qt.ItemFlag.ItemIsSelectable
    assert not (bad.flags() & Qt.ItemFlag.ItemIsSelectable)
    assert "role CAP: 1 of 2" in bad.text()
    assert dlg.result_data() == "good", "первая ПОДХОДЯЩАЯ предвыбрана"


def test_orphan_offers_every_cell_with_the_fit_warning(qapp):
    """У сироты (экземпляра нет) — ВСЕ ячейки выбираемы, и жёлтая строка говорит,
    что подбор не проверялся."""
    dlg = ChangeCellDialog(None, [_row("a"), _row("b", fits=False, reason="role X")],
                           orphan=True)
    assert dlg.result_data() == "a"
    assert all(dlg._list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable
               for i in range(dlg._list.count()))
    warning = _labelled(dlg, "change_cell_fit_warning")
    assert warning and "no instance on the board" in warning[0].text()
    assert "(no sheet)" not in warning[0].text()


def test_ok_is_disabled_without_a_selectable_choice(qapp):
    """Некуда менять — OK недоступен."""
    dlg = ChangeCellDialog(None, [_row("bad", fits=False, reason="role R: 0 of 1")])
    assert dlg.result_data() is None
    assert _ok(dlg).isEnabled() is False


# ── Число N: ТОЛЬКО медь этой сущности (is_own_key) ──────────────────────

def _two_entities_cfg(tmp_path):
    root = tmp_path / "root.sexp"
    write_config(root, {
        "cells": {"c_src": {"components": [{"role": "R"}]},
                  "c_dst": {"components": [{"role": "R"}]}},
        "entities": [
            {"name": "e_a", "cell": "c_src", "cluster": "CL", "sheet": "Ch0"},
            {"name": "e_b", "cell": "c_src", "cluster": "CL", "sheet": "Ch1"}]})
    cfg, _ctx = load_config(str(root))
    return root, cfg


def _fake_registries(cfg, monkeypatch):
    """Подделка реестров: у e_a и e_b ОДНОЙ ячейки — свои ключи; плюс роль-ключ
    адреса e_a и ключ ДРУГОЙ ячейки (не должен попасть)."""
    from kicadstamp.registry import record_key_part
    by_name = {e.name: e for e in cfg.entities}
    id_a = record_key_part("e_a", by_name["e_a"].uuid)
    id_b = record_key_part("e_b", by_name["e_b"].uuid)
    cid_src = record_key_part("c_src", cfg.cells["c_src"].uuid)
    cid_dst = record_key_part("c_dst", cfg.cells["c_dst"].uuid)

    class _E:
        def __init__(self, uuid):
            self.uuid = uuid

    via = {
        f"name:{id_a}|{cid_src}|__spoke__|0": _E("u1"),      # e_a
        f"role:R:Ch0:CL|{cid_src}|R|1": _E("u3"),            # e_a (адрес)
        f"name:{id_b}|{cid_src}|__spoke__|0": _E("u2"),      # e_b
        f"name:{id_a}|{cid_dst}|__spoke__|0": _E("u4"),      # ДРУГАЯ ячейка
    }
    monkeypatch.setattr("kicadstamp.registry.load_registry_entries",
                        lambda config_path, _cfg=None: (via, {}, {}))


def _entity_dict(cfg, name):
    e = next(x for x in cfg.entities if x.name == name)
    return {"name": e.name, "cell": e.cell, "cluster": e.cluster,
            "sheet": e.sheet}


def test_n_counts_only_this_entity_not_the_whole_old_cell(tmp_path, monkeypatch):
    """Две сущности ОДНОЙ ячейки: N(e_a) = 2 (её name:-ключ и её role:-ключ), а
    не 3 (ключ e_b — не её). Мутация «N по всем ключам старой ячейки» гибнет
    здесь."""
    root, cfg = _two_entities_cfg(tmp_path)
    _fake_registries(cfg, monkeypatch)
    assert flow_mod.old_layout_copper_count(cfg, root, _entity_dict(cfg, "e_a")) == 2
    assert flow_mod.old_layout_copper_count(cfg, root, _entity_dict(cfg, "e_b")) == 1


def test_n_is_zero_when_no_copper_of_the_old_layout_is_recorded(tmp_path, monkeypatch):
    """N = 0 — законный ответ (своей строкой в Логе), а не «не посчитали»."""
    root, cfg = _two_entities_cfg(tmp_path)
    _fake_registries(cfg, monkeypatch)
    entity = _entity_dict(cfg, "e_b")
    entity["sheet"] = "Ch9"          # такого адреса в реестрах нет
    assert flow_mod.old_layout_copper_count(cfg, root, entity) == 0


# ── Поток: запись одной правкой + строка Лога с числом ───────────────────

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


def _accepting_change(chosen, made):
    class _Dialog:
        def __init__(self, parent, cells, *, orphan=False):
            made["cells"] = list(cells)
            made["orphan"] = orphan

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_data(self):
            return chosen

    return _Dialog


def test_change_cell_offers_fitting_first_and_writes_one_edit(
        real_main_window, tmp_path, monkeypatch, caplog):
    """Экземпляр подходит c_dst и не подходит c_src (лишняя роль) → в выпадашке
    первым идёт c_dst; OK пишет cell+cell_uuid ОДНОЙ правкой, один graph_changed,
    и Лог называет медь старой раскладки числом (0 — СВОЕЙ строкой)."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {
        "cells": {"c_src": {"components": [{"role": "C"}]},
                  "c_dst": {"components": [{"role": "R"}]}},
        "entities": [{"name": "e_a", "cell": "c_src", "cluster": "CL",
                      "sheet": "Ch0"},
                     {"name": "e_b", "cell": "c_src", "cluster": "CL",
                      "sheet": "Ch1"}]})
    open_project(hub, root)
    monkeypatch.setattr(hub.main_window.connection, "_snapshot",
                        [_Sel("R1", "CL")])
    monkeypatch.setattr(hub, "refresh_snapshot_and_push",
                        lambda on_ready=None: on_ready and on_ready())
    cfg = load_config(str(root))[0]
    _fake_registries(cfg, monkeypatch)

    made = {}
    monkeypatch.setattr(flow_mod, "ChangeCellDialog",
                        _accepting_change("c_dst", made))
    changes = []
    hub.config_tree_dock.graph_changed.connect(lambda: changes.append(1))

    entity = {"name": "e_a", "cell": "c_src", "cluster": "CL", "sheet": "Ch0"}
    with caplog.at_level(logging.INFO):
        flow_mod.change_cell_for_entity(hub, entity, root)

    assert made["cells"], "диалог обязан получить список ячеек"
    assert made["cells"][0].name == "c_dst" and made["cells"][0].fits
    assert made["orphan"] is False
    assert any(c.name == "c_src" and not c.fits for c in made["cells"]), \
        "старая ячейка без роли R остаётся в списке серой, с причиной"
    saved = _entities_of(root)[0]
    assert saved["cell"] == "c_dst"
    assert saved.get("cell_uuid"), "cell_uuid обязан быть записан"
    assert len(changes) == 1, "одна правка = один graph_changed"
    assert any("replaces its copper (2 pieces of the old layout)" in r.message
               for r in caplog.records), \
        "Лог обязан назвать медь старой раскладки ЧИСЛОМ"


def _entities_of(root):
    from kicadstamp.config.sexp_format import sexp_to_dict
    return sexp_to_dict(root.read_text(encoding="utf-8")).get("entities") or []


def test_change_cell_menu_item_exists_on_a_healthy_entity(
        real_main_window, tmp_path, monkeypatch):
    """Пункт «Change cell…» (objectName change_cell_action) есть и у ЗДОРОВОЙ
    сущности, а не только у сироты — обобщение старого «Point to cell…»."""
    hub = real_main_window._dock_hub
    root = tmp_path / "root.sexp"
    write_config(root, {"cells": {"c": {"components": [{"role": "R"}]}},
                        "entities": [{"name": "e1", "cell": "c"}]})
    open_project(hub, root)
    dock = hub.config_tree_dock
    cell = find_child(category(file_item(dock.tree, root), "cells"), "c")
    leaf = find_child(cell, "e1")   # сущность — ребёнок СВОЕЙ ячейки
    actions = context_menu_actions(dock, leaf, monkeypatch)
    change = [act for _l, act in actions
              if act.objectName() == "change_cell_action"]
    assert len(change) == 1, ("у здоровой сущности нет ровно одного "
                              "change_cell_action; видели: "
                              + repr([l for l, _ in actions]))
