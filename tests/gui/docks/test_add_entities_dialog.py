# tests/gui/docks/test_add_entities_dialog.py
"""Сторожа ФОРМЫ «Add entities…» (plan_2026_10_09_cells_and_entities, часть 1) —
gui/docks/add_entities.py.

Слой: сама форма, без дока и без хаба. Кандидаты подаются готовыми
(InstanceCandidate) — правило подбора живёт и стережётся в
tests/gui/docks/test_instance_candidates.py, здесь только ТАБЛИЦА и её решения:
галочки, серые занятые, счётчик неполных, конфликт имени как единственный
запрет OK.

Имена функций описывают СВОЙСТВО (правило 37), имя плана — в докстринге.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox

from gui.docks.add_entities import (
    COL_CHECK,
    COL_NAME,
    COL_STATE,
    AddEntitiesDialog,
    default_entity_names,
)
from gui.docks.instance_candidates import InstanceCandidate


def _cand(cluster, sheet, *, fits=True, reason="", entity=None):
    return InstanceCandidate(cluster=cluster, sheet=sheet, refs=("R1",),
                             fits=fits, reason=reason, entity_name=entity)


def _ok(dlg):
    return dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)


def _checked(dlg) -> bool:
    return bool(dlg._table.item(0, COL_CHECK).flags()
                & Qt.ItemFlag.ItemIsUserCheckable)


def test_default_name_is_the_cluster_in_lower_case(qapp):
    """Имя по умолчанию — кластер в нижнем регистре (FPGA_VCCIO_139 →
    fpga_vccio_139)."""
    cands = [_cand("FPGA_VCCIO_139", "Ch0")]
    assert default_entity_names(cands)[cands[0]] == "fpga_vccio_139"


def test_default_name_appends_the_sheet_when_a_cluster_repeats(qapp):
    """Кластер на нескольких листах — <кластер>_<лист>, иначе две сущности
    получили бы одно имя."""
    cands = [_cand("FPGA_VCCIO_139", "Ch0"), _cand("FPGA_VCCIO_139", "Ch1")]
    names = default_entity_names(cands)
    assert names[cands[0]] == "fpga_vccio_139_ch0"
    assert names[cands[1]] == "fpga_vccio_139_ch1"


def test_non_fitting_instances_are_not_rows_but_counted(qapp):
    """Неподходящие в таблицу не идут — идут строкой-счётчиком над ней."""
    dlg = AddEntitiesDialog(None, "c", [
        _cand("CL_OK", "Ch0"),
        _cand("CL_BAD", "Ch0", fits=False, reason="role CAP: 0 of 1"),
    ], set())
    assert dlg._table.rowCount() == 1
    assert "1 instances lack roles" in dlg._counter.text()


def test_taken_instance_is_greyed_and_not_checkable(qapp):
    """Занятый виден (серым, «already: <имя>»), но галочки у него нет."""
    dlg = AddEntitiesDialog(None, "c", [_cand("CL_A", "Ch0", entity="e1")],
                            set())
    assert dlg._table.item(0, COL_CHECK).flags() \
        & Qt.ItemFlag.ItemIsUserCheckable == Qt.ItemFlag(0)
    assert dlg._table.item(0, COL_STATE).text() == "already: e1"


def test_all_and_none_check_only_the_free_rows(qapp):
    """All/None двигают галочки ТОЛЬКО свободных строк — занятый отметить
    нельзя ничем."""
    dlg = AddEntitiesDialog(None, "c", [
        _cand("CL_A", "Ch0"),
        _cand("CL_B", "Ch0", entity="e1"),
        _cand("CL_C", "Ch0"),
    ], set())
    dlg._set_all(True)
    assert [dlg._is_checked(i) for i in range(3)] == [True, False, True]
    dlg._set_all(False)
    assert [dlg._is_checked(i) for i in range(3)] == [False, False, False]


def test_ok_is_disabled_until_something_is_checked(qapp):
    """OK недоступен, пока ничего не отмечено — «добавить ничего» не действие."""
    dlg = AddEntitiesDialog(None, "c", [_cand("CL_A", "Ch0")], set())
    assert _ok(dlg).isEnabled() is False
    dlg._set_all(True)
    assert _ok(dlg).isEnabled() is True


def test_name_already_in_the_graph_disables_ok(qapp):
    """Конфликт имени с графом — OK недоступен и есть КРАСНАЯ строка (без
    QMessageBox, правило 43)."""
    dlg = AddEntitiesDialog(None, "c", [_cand("CL_A", "Ch0")], {"taken_name"})
    dlg._set_all(True)
    dlg._table.item(0, COL_NAME).setText("taken_name")
    assert _ok(dlg).isEnabled() is False
    assert "taken_name" in dlg._hint.text()


def test_duplicate_name_inside_the_table_disables_ok(qapp):
    """Два одинаковых имени В САМОЙ таблице — тот же запрет."""
    dlg = AddEntitiesDialog(None, "c", [_cand("CL_A", "Ch0"),
                                        _cand("CL_B", "Ch0")], set())
    dlg._set_all(True)
    dlg._table.item(0, COL_NAME).setText("same")
    dlg._table.item(1, COL_NAME).setText("same")
    assert _ok(dlg).isEnabled() is False
    assert "twice" in dlg._hint.text()


def test_result_data_is_the_checked_rows_with_cluster_and_sheet(qapp):
    """result_data() — отмеченные строки (имя, кластер, лист), в порядке
    таблицы; это ровно то, что хаб пишет одной правкой."""
    dlg = AddEntitiesDialog(None, "c", [_cand("CL_A", "Ch0"),
                                        _cand("CL_B", "Ch1")], set())
    dlg._set_all(True)
    rows = dlg.result_data()
    assert [(r.name, r.cluster, r.sheet) for r in rows] == [
        ("cl_a", "CL_A", "Ch0"), ("cl_b", "CL_B", "Ch1")]
