# tests/gui/docks/test_create_entity_form.py
"""Сторожа ФОРМЫ «Создать сущность» — слой, который читает САМУ форму.

Заходы: plan_2026_09_21_guard_file_by_property.md (Т1/Т2/Т3, форма с двумя
полями Cluster/Sheet) и plan_2026_10_09_cells_and_entities, часть 3, Т3.1 —
два поля у ЯЧЕЙКИ СВЁРНУТЫ в ОДНУ редактируемую выпадашку «КЛАСТЕР — лист»
(два независимых поля давали бы пару, которой нет на плате). Поэтому клетки
«поле Cluster» / «поле Sheet у ячейки» переписаны под выпадашку; СВОЙСТВО
нормализации (пусто → None; набрано → доезжает обрезанным) сохранено, адреса
остались раздельными.

Здесь ровно одна дверь наружу — CreateEntityDialog.result_data() (для имени
ещё accept() → _validate()). Всё, что идёт через меню, обработчик хаба и запись
в конфиг, живёт в tests/gui/docks/test_create_entity_menu.py; общие строители —
в tests/gui/create_entity_helpers.py.

Имена функций описывают СВОЙСТВО и не несут номера плана (правило 37).

МАТРИЦА КЛЕТОК (Т2 + Т3.1). Предмет — три поля формы на двух ветках:

  | поле     | направление | ветка            | ожидание                       |
  |----------|-------------|------------------|--------------------------------|
  | имя      | пусто       | ячейка/отпечаток | не уходит из формы (сообщение) |
  | имя      | набрано     | ячейка           | доезжает обрезанным            |
  | имя      | набрано     | отпечаток        | доезжает обрезанным            |
  | пара     | пусто       | ячейка           | (None, None)                   |
  | кластер  | набрано     | ячейка           | левая половина доезжает        |
  | лист у яч.| набрано     | ячейка           | правая половина доезжает       |
  | кластер  | —           | отпечаток        | поля нет вовсе, на выходе None |
  | лист     | пусто       | отпечаток        | None                           |
  | лист     | набрано     | отпечаток        | доезжает обрезанным            |

Плюс Т3.1: свободные подходящие сверху; занятый серый и невыбираемый;
неподходящие не строки + серая строка-счётчик; ровно один свободный —
предвыбран, два — нет; без снимка — форма открыта, жёлтая строка; ручная пара
не с платы — жёлтая строка, но OK проходит; у отпечатка кластера нет, лист — из
списка.
"""

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialog, QDialogButtonBox

from gui.docks.instance_candidates import InstanceCandidate
from kicadstamp.i18n import _

from tests.gui.create_entity_helpers import (
    RealCreateEntityDialog,
    capture_warnings,
    instance_line,
)

_GREY = QColor("#888888")


def _cand(cluster, sheet, *, fits=True, reason="", entity_name=None):
    return InstanceCandidate(cluster=cluster, sheet=sheet, refs=(),
                             fits=fits, reason=reason, entity_name=entity_name)


def _form(parent, source_kind, source_name, *, candidates=(), sheet_names=(),
          snapshot_available=False):
    return RealCreateEntityDialog(parent, source_kind, source_name, [],
                                  candidates=candidates, sheet_names=sheet_names,
                                  snapshot_available=snapshot_available)


# ═══════════════════════════════════════════════════════════════════════════
# НОРМАЛИЗАЦИЯ ЯЧЕЙКИ: одна выпадашка → (cluster, sheet)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("typed,cluster,sheet", [
    ("", None, None),
    ("   ", None, None),
    ("CH0", "CH0", None),
    ("  CH0  ", "CH0", None),
    ("CH0 — Sheet_1", "CH0", "Sheet_1"),
    ("  CH0 —  Sheet_1 ", "CH0", "Sheet_1"),
])
def test_the_cell_pair_survives_the_one_combobox(main_window, typed, cluster,
                                                 sheet):
    """Клетка Т3.1: пара ячейки набирается ОДНОЙ строкой «КЛАСТЕР — лист» и
    разбирается parse_instance_label. Пусто (в т.ч. из пробелов) → (None,
    None), а не пустые строки: "" в find_entity_for_source сузило бы по пустой
    строке и родило бы вторую сущность на ту же ячейку; набранное доезжает
    ОБРЕЗАННЫМ по краям каждой половины."""
    dlg = _form(main_window, "cell", "my_cell")
    assert dlg._instance_combo is not None, (
        "у ячейки обязана быть выпадашка экземпляра — иначе сторожа проверяли "
        "бы не тот путь")
    dlg._instance_combo.setEditText(typed)

    name, got_cluster, got_sheet = dlg.result_data()

    assert name == "my_cell"
    assert (got_cluster, got_sheet) == (cluster, sheet), (
        f"набрано {typed!r} → ждали ({cluster!r}, {sheet!r}), получили "
        f"({got_cluster!r}, {got_sheet!r})")


def test_a_typed_name_on_a_cell_reaches_result_data_trimmed(main_window):
    """Имя на ветке ЯЧЕЙКИ доезжает обрезанным (прежний С8, сохранён под
    выпадашку): дубликат имени ловится ТОЧНЫМ совпадением, так что « my » и
    «my» для него — два имени."""
    dlg = _form(main_window, "cell", "seed_cell")
    dlg._name_edit.setText("  my_cell  ")

    name, _cluster, _sheet = dlg.result_data()

    assert name == "my_cell", (name,)


def test_a_typed_name_on_an_imprint_reaches_result_data_trimmed(main_window):
    """Имя на ветке ОТПЕЧАТКА — отдельным адресом: падение одной половины не
    прячет другую, а снятый .strip() только на этой ветке (прежняя М11) обязан
    её уронить, не трогая ячейковую."""
    dlg = _form(main_window, "imprint", "amp")
    dlg._name_edit.setText("  my_imprint  ")

    name, _cluster, _sheet = dlg.result_data()

    assert name == "my_imprint", (name,)


# ═══════════════════════════════════════════════════════════════════════════
# НОРМАЛИЗАЦИЯ ОТПЕЧАТКА: листа кластера нет, лист — своя выпадашка
# ═══════════════════════════════════════════════════════════════════════════

def test_an_imprint_has_no_instance_combobox_and_a_sheet_list(main_window):
    """Т3.1: у imprint-сущности кластера нет ВООБЩЕ (кластер на ней фатален при
    загрузке, models.py:706), поэтому выпадашки экземпляра у неё нет; лист —
    редактируемая выпадашка из переданных ctx.sheet_names."""
    dlg = _form(main_window, "imprint", "amp",
                sheet_names=["Channel_0", "Channel_1"])
    assert dlg._instance_combo is None, (
        "у отпечатка выпадашки экземпляра быть не должно (Т2а/Т3.1)")
    combo = dlg._sheet_combo
    assert combo is not None and combo.isEditable()
    assert [combo.itemText(i) for i in range(combo.count())] == \
        ["Channel_0", "Channel_1"]
    assert dlg.result_data()[1] is None, "у отпечатка кластер обязан быть None"


@pytest.mark.parametrize("typed,expected", [
    ("", None),
    ("   ", None),
    ("Channel_1", "Channel_1"),
    (" Channel_1 ", "Channel_1"),
])
def test_a_blank_sheet_on_an_imprint_comes_out_as_none(main_window, typed,
                                                       expected):
    """Клетка «лист | … | отпечаток»: пустое (в т.ч. из пробелов) → None, не
    пустая строка — сужение пары (imprint, sheet) работает на `sheet is not
    None`, и с "" один отпечаток на двух близнецовых листах слился бы в одну
    сущность. Набранное доезжает обрезанным."""
    dlg = _form(main_window, "imprint", "amp",
                sheet_names=["Channel_0", "Channel_1"])
    dlg._sheet_combo.setEditText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "amp"
    assert cluster is None
    assert sheet == expected, (typed, sheet, expected)


# ═══════════════════════════════════════════════════════════════════════════
# Т3.1: строки выпадашки — подбор, а не список наугад
# ═══════════════════════════════════════════════════════════════════════════

def test_free_rows_come_first_and_the_taken_is_grey_and_not_selectable(
        main_window):
    """Клетка плана: свободный подходящий экземпляр — строка сверху; ЗАНЯТЫЙ —
    серый и НЕВЫБИРАЕМЫЙ, с подписью «already: <сущность>» (иначе его можно
    было бы выбрать и создать второй раз)."""
    free = _cand("FPGA_VCCIO_1", "Ch0")
    taken = _cand("FPGA_VCCIO_4", "Ch1", entity_name="fpga_vccio_4")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[free, taken], snapshot_available=True)

    combo = dlg._instance_combo
    assert [combo.itemText(i) for i in range(combo.count())] == \
        [free.label, dlg._taken_label(taken)]
    assert "already: fpga_vccio_4" in combo.itemText(1)

    model = combo.model()
    taken_item = model.item(1)
    assert not (taken_item.flags() & Qt.ItemFlag.ItemIsSelectable), (
        "занятая строка не имеет права выбираться")
    assert taken_item.foreground().color() == _GREY, (
        "занятая строка обязана быть серой")


def test_non_fitting_instances_are_not_rows_but_one_grey_counter(main_window):
    """Клетка плана: неподходящий экземпляр — НЕ строка, а ОДНА серая строка-
    счётчик под выпадашкой (Денис: «зачем этот огромный список»); он посчитан,
    а не потерян."""
    free = _cand("CL_A", "Ch0")
    other = _cand("CL_B", "Ch0", fits=False, reason="role CAP: 0 of 1")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[free, other], snapshot_available=True)

    combo = dlg._instance_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert all("CL_B" not in lab for lab in labels), labels
    assert dlg._others_label.text() == _(
        "{count} other instances lack roles").format(count=1)
    assert dlg._others_label.isVisibleTo(dlg), (
        "счётчик неполных обязан быть показан, когда они есть")


def test_no_counter_line_when_every_instance_fits(main_window):
    """Обратная сторона счётчика: подходящие все — серая строка не показана и
    пуста."""
    free = _cand("CL_A", "Ch0")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[free], snapshot_available=True)
    assert dlg._others_label.text() == ""
    assert not dlg._others_label.isVisibleTo(dlg)


def test_a_single_free_instance_is_preselected_but_two_are_not(main_window):
    """Клетка плана: РОВНО один свободный подходящий — выбран; два — не
    предвыбран (пользователь обязан выбрать сам)."""
    one = _cand("CL_A", "Ch0")
    dlg1 = _form(main_window, "cell", "my_cell",
                 candidates=[one], snapshot_available=True)
    assert dlg1._instance_combo.currentText() == one.label
    assert dlg1.result_data()[1:] == ("CL_A", "Ch0")

    two = _cand("CL_B", "Ch1")
    dlg2 = _form(main_window, "cell", "my_cell",
                 candidates=[one, two], snapshot_available=True)
    assert dlg2._instance_combo.currentIndex() == -1
    assert dlg2._instance_combo.currentText() == ""


def test_a_taken_only_board_preselects_nothing(main_window):
    """Занятый НЕ предвыбирается даже когда он единственный: предвыбор — только
    среди СВОБОДНЫХ."""
    taken = _cand("CL_A", "Ch0", entity_name="e1")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[taken], snapshot_available=True)
    assert dlg._instance_combo.currentIndex() == -1


# ═══════════════════════════════════════════════════════════════════════════
# Т3.1: жёлтая строка — предупреждение, никогда не запрет
# ═══════════════════════════════════════════════════════════════════════════

def test_a_pair_not_on_the_board_warns_yellow_but_ok_still_passes(main_window):
    """Клетка плана: ручная пара, которой нет на плате, — ЖЁЛТАЯ строка «not on
    the board — fit not checked», и OK ВСЁ РАВНО проходит (предупреждение, не
    запрет)."""
    free = _cand("CL_A", "Ch0")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[free], snapshot_available=True)
    dlg._instance_combo.setEditText(instance_line("CL_X", "Ch9"))

    assert dlg._fit_warning.text() == _("not on the board — fit not checked")
    assert dlg._fit_warning.isVisibleTo(dlg)

    dlg._name_edit.setText("e")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted, (
        "жёлтая строка — не запрет: пара не с платы имеет право быть записана")
    assert dlg.result_data() == ("e", "CL_X", "Ch9")


def test_a_bad_pair_hides_the_warning_again(main_window):
    """Обратная сторона: как только набрана пара, которую плата несёт, жёлтая
    строка гаснет."""
    free = _cand("CL_A", "Ch0")
    dlg = _form(main_window, "cell", "my_cell",
                candidates=[free], snapshot_available=True)
    dlg._instance_combo.setEditText(instance_line("CL_X", "Ch9"))
    assert dlg._fit_warning.isVisibleTo(dlg)
    dlg._instance_combo.setEditText(free.label)
    assert dlg._fit_warning.text() == ""


def test_without_a_snapshot_the_dialog_opens_with_the_yellow_line(main_window):
    """Клетка плана: KiCad закрыт (снимка нет) — диалог ВСЁ РАВНО открывается
    (создание сущности без платы законно, Т4/С7), выпадашка пуста и
    редактируема, жёлтая строка говорит «no board snapshot — fit not checked»."""
    dlg = _form(main_window, "cell", "my_cell",
                candidates=(), snapshot_available=False)
    assert dlg._instance_combo.count() == 0
    assert dlg._instance_combo.isEditable(), (
        "без снимка экземпляр вводят руками — выпадашка обязана быть "
        "редактируемой")
    assert dlg._fit_warning.text() == _("no board snapshot — fit not checked")
    assert dlg._fit_warning.isVisibleTo(dlg)


# ═══════════════════════════════════════════════════════════════════════════
# ИМЯ: пустое из формы не выходит
# ═══════════════════════════════════════════════════════════════════════════
#
# ЗАМЕР, А НЕ РАССУЖДЕНИЕ. У имени нет токена `or None`: `name =
# self._name_edit.text().strip()`. Пустое имя держит гейт `if not name:` в
# _validate, и вопрос звучит так: не выходит ли пустое имя из формы при
# КАКОМ-НИБУДЬ порядке действий. Наружу ведёт ровно одна дверь — accept().

EMPTY_NAME_ORDERS = [
    ("имя стёрто и оставлено пустым", "cell", "my_cell", [("name", "")]),
    ("имя из одних пробелов", "cell", "my_cell", [("name", "   ")]),
    ("имя набрано и стёрто", "cell", "my_cell",
     [("name", "buf"), ("name", "")]),
    ("поле источника пусто (ячейка без имени)", "cell", "", []),
    ("отпечаток без имени", "imprint", "", []),
]


def _form_after(parent, source_kind, source_name, actions):
    """Настоящая форма ПОСЛЕ действий пользователя: `actions` — список
    ("name"|"instance", значение) в том порядке, в каком их делали."""
    dlg = RealCreateEntityDialog(parent, source_kind, source_name, [])
    for field, value in actions:
        if field == "name":
            dlg._name_edit.setText(value)
        elif field == "instance":
            dlg._instance_combo.setEditText(value)
    return dlg


@pytest.mark.parametrize(
    "kind,source,actions",
    [(k, s, a) for _label, k, s, a in EMPTY_NAME_ORDERS],
    ids=[label for label, _k, _s, _a in EMPTY_NAME_ORDERS])
def test_an_empty_name_never_leaves_the_form(main_window, monkeypatch, kind,
                                             source, actions):
    """Т3/С4: пустое имя не уходит из формы НИ ПРИ КАКОМ порядке действий.
    Проверка идёт через настоящий accept() и подтверждается тремя вещами
    сразу: диалог не принят, человеку СКАЗАНО почему, и форма не выдумала имя
    сама."""
    warnings = capture_warnings(monkeypatch)
    dlg = _form_after(main_window, kind, source, actions)

    dlg.accept()

    assert dlg.result() != QDialog.DialogCode.Accepted, (
        f"порядок действий {actions!r}: форма приняла пустое имя; "
        f"result_data() = {dlg.result_data()!r}")
    assert warnings, (
        "отказ обязан ГОВОРИТЬ, что имени нет: молчащая кнопка — это «не "
        f"работает», а не «введите имя»; порядок действий {actions!r}")
    assert dlg.result_data()[0] == "", (
        "форма не имеет права выдумывать имя вместо пустого — иначе сторож "
        f"проверял бы не тот случай: {dlg.result_data()!r}")


def test_a_filled_name_is_accepted(main_window):
    """КОНТРОЛЬ против ложной зелени сторожа пустого имени: без него все его
    случаи были бы зелёными и в том случае, если бы _validate отказывал ВСЕГДА
    («кнопка никогда не работает»). Непустое имя — принимается."""
    dlg = _form_after(main_window, "cell", "my_cell", [])

    dlg.accept()

    assert dlg.result() == QDialog.DialogCode.Accepted, (
        "с непустым именем форма обязана приниматься, иначе сторож пустого "
        "имени — сторож сломанной кнопки")
    assert dlg.result_data()[0] == "my_cell"


def test_the_ok_button_refuses_an_empty_name(main_window, monkeypatch):
    """Не только прямой accept(): кнопка OK идёт тем же путём — сигнал accepted
    → self.accept() → _validate. Здесь по ней КЛИКАЮТ, как человек."""
    warnings = capture_warnings(monkeypatch)
    dlg = _form_after(main_window, "cell", "my_cell", [("name", "")])
    ok = dlg.findChild(QDialogButtonBox).button(
        QDialogButtonBox.StandardButton.Ok)
    assert ok is not None, "у формы обязана быть кнопка OK"

    ok.click()

    assert dlg.result() != QDialog.DialogCode.Accepted, (
        "кнопка OK обязана идти через accept()/_validate, а не закрывать "
        "диалог напрямую — иначе пустое имя выходит из формы мимо гейта")
    assert warnings, "человеку обязано быть сказано, что имени нет"
