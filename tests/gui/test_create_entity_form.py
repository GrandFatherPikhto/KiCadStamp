# tests/gui/test_create_entity_form.py
"""Сторожа ФОРМЫ «Создать сущность» — слой, который читает САМУ форму.

Заход: plan_2026_09_21_guard_file_by_property.md, Т1/Т2/Т3. Здесь ровно одна
дверь наружу — CreateEntityDialog.result_data() (для имени ещё accept() →
_validate()). Всё, что идёт через меню, обработчик хаба и запись в конфиг,
живёт в tests/gui/test_create_entity_menu.py; общие строители — в
tests/gui/create_entity_helpers.py (Т1: дублей на слои не держим).

Имена функций описывают СВОЙСТВО и не несут номера плана (правило 37): номер
С-сторожа и имя плана названы в докстринге каждой функции.

МАТРИЦА КЛЕТОК (Т2). Предмет — три поля формы на двух ветках:

  | поле    | направление | ветка            | ожидание                       |
  |---------|-------------|------------------|--------------------------------|
  | имя     | пусто       | ячейка/отпечаток | не уходит из формы (сообщение) |
  | имя     | набрано     | ячейка/отпечаток | доезжает обрезанным            |
  | кластер | пусто       | ячейка           | None                           |
  | кластер | набрано     | ячейка           | доезжает обрезанным            |
  | кластер | —           | отпечаток        | поля нет вовсе, на выходе None |
  | лист    | пусто       | ячейка           | None                           |
  | лист    | пусто       | отпечаток        | None                           |
  | лист    | набрано     | ячейка           | доезжает обрезанным            |
  | лист    | набрано     | отпечаток        | доезжает обрезанным            |

Каждая клетка существует отдельным адресом ниже. Обе формы пустоты ("" и
"   ") и обе формы набранного ("CH0" и " CH0 ") — ПАРАМЕТРЫ, а не отдельные
функции. Ветки с разным СМЫСЛОМ («лист у ячейки» / «лист у отпечатка»,
«набранный лист у ячейки» / «у отпечатка») живут РАЗНЫМИ функциями: оснастка
мутаций различает сторожей только по имени функции (failing_guards срезает
параметр), и на слиянии веток утверждение М7 «С7 умирает, С6 зелёный» дало бы
ложное «collaterally killed» и вердикт WRONG. Отдельные функции здесь — не
стилистика, а требование ворот.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from PyQt6.QtWidgets import QDialog

from tests.gui.create_entity_helpers import (
    RealCreateEntityDialog,
    capture_warnings,
)


def _form_after(parent, source_kind, source_name, actions):
    """Настоящая форма ПОСЛЕ действий пользователя: `actions` — список
    ("name"|"cluster"|"sheet", значение) в том порядке, в каком их делали."""
    dlg = RealCreateEntityDialog(parent, source_kind, source_name, [])
    for field, value in actions:
        {"name": dlg._name_edit, "cluster": dlg._cluster_edit,
         "sheet": dlg._sheet_edit}[field].setText(value)
    return dlg


# ═══════════════════════════════════════════════════════════════════════════
# ПУСТО → None. Каждая ветка — свой адрес (М2 требует различать их по имени).
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("typed", ["", "   "])
def test_a_blank_cluster_on_a_cell_comes_out_as_none(main_window, typed):
    """С1 плана empty_cluster_normalisation, клетка «кластер | пусто | ячейка»:
    через CreateEntityDialog.result_data() — не мимо неё — пустое поле
    КЛАСТЕРА у ячейки обязано выйти как None, а не как "".

    С "" сужение в find_entity_for_source пойдёт по пустой строке, ни с одной
    существующей сущностью не совпадёт — и на ту же ячейку родится ВТОРАЯ
    сущность, хотя Т3 обещает обратное; в запись кластер при этом не попадёт
    (`if source_field == "cell" and cluster:` — "" ложна).

    Лист здесь тоже держится (assert sheet is None) и ОСТАВЛЕН НА МЕСТЕ
    намеренно: именно им убивается мутация М2 (§Т4 задания: свой адрес новой
    клетке — добавлением, а не переносом этого assert'а).

    Мутация М1 (убрать `or None` у кластера) роняет сторож: на выходе "".
    """
    dlg = RealCreateEntityDialog(main_window, "cell", "my_cell", [])
    assert dlg._cluster_edit is not None, (
        "у ячейки поле Cluster обязано быть — иначе сторожа нормализации "
        "проверяли бы не тот путь")
    dlg._cluster_edit.setText(typed)
    dlg._sheet_edit.clear()  # пусты оба поля

    name, cluster, sheet = dlg.result_data()

    assert name == "my_cell", (
        f"имя берётся из поля источника и правится: получили {name!r}")
    assert cluster is None, (
        "пустое поле кластера обязано выйти из result_data() как None, а не "
        "как пустая строка: сужение в find_entity_for_source работает на "
        "`cluster is not None`, и с \"\" вторая сущность на ту же ячейку "
        f"создастся (мутация М1); получили: {cluster!r}")
    assert sheet is None, (
        "пустое поле листа — тоже None, не пустая строка: этот assert здесь "
        "намеренно держит мутацию М2 и с места не снимался (задание §Т4); "
        f"получили: {sheet!r}")


@pytest.mark.parametrize("typed", ["", "   "])
def test_a_blank_sheet_on_a_cell_comes_out_as_none(main_window, typed):
    """Клетка «лист | пусто | ячейка» — свой адрес, ДОБАВЛЕННЫЙ заходом
    plan_2026_09_21_guard_file_by_property.md (§Т2/§Т4).

    Преемника среди прежних функций у неё нет: до этого захода пустой лист у
    ячейки проверялся внутри сторожа пустого кластера (С1), и своего адреса
    клетка не имела. Оснастка мутаций различает сторожей только по имени
    функции, поэтому асимметрия «ячейка / отпечаток» у листа не могла быть
    измерена отдельно — ровно на такой асимметрии дыра выжила в прошлый раз
    (§2 задания). Сторож добавлен РЯДОМ с С1, а не вместо его assert'а.

    Роняют его: М2 (снятый `or None` у листа) — оба параметра; М6л/М8 (снятый
    .strip()) — параметр "   ", потому что `"   " or None` это "   ", то есть
    непусто.
    """
    dlg = RealCreateEntityDialog(main_window, "cell", "my_cell", [])
    assert dlg._sheet_edit is not None, "поле Sheet у формы обязано быть"
    dlg._sheet_edit.setText(typed)

    _name, _cluster, sheet = dlg.result_data()

    assert sheet is None, (
        "пустое поле ЛИСТА у ЯЧЕЙКИ обязано выйти из result_data() как None, "
        "а не как пустая строка: лист у ячейки сужает резолв роли "
        "(build_role_anchor / anchor_sheet) и проверяется на `sheet is not "
        f"None`; набрано {typed!r}, получили {sheet!r}")


@pytest.mark.parametrize("typed", ["", "   "])
def test_a_blank_sheet_on_an_imprint_comes_out_as_none(main_window, typed):
    """С2 плана empty_cluster_normalisation, клетка «лист | пусто | отпечаток».

    У imprint-сущности кластера нет вовсе — поля не строятся (Т2а плана
    create_entity_menu: не предлагать, а не предлагать и отбрасывать), и
    второе ключевое поле пары — `sheet`, целевой лист twin-резолва
    (config/models.py:704). Пустой лист обязан выйти как None: тем же токеном
    держится сужение пары (imprint, sheet), и с "" один отпечаток на двух
    близнецовых листах слился бы в одну сущность — или наоборот, второй лист
    перестал бы находить первый.

    Мутация М2 (убрать `or None` у листа) роняет сторож: на выходе "".
    """
    dlg = RealCreateEntityDialog(main_window, "imprint", "amp", [])
    assert dlg._cluster_edit is None, (
        "у отпечатка поля Cluster нет вовсе (Т2а): не предлагать, а не "
        "предлагать и отбрасывать")
    assert dlg._sheet_edit is not None, "поле Sheet у отпечатка обязано быть"
    dlg._sheet_edit.setText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "amp", (
        f"имя предзаполняется именем записи: получили {name!r}")
    assert cluster is None, (
        "кластер на imprint-сущности фатален при загрузке (models.py:706) и "
        f"обязан быть None: получили {cluster!r}")
    assert sheet is None, (
        "пустое поле листа обязано выйти из result_data() как None, а не как "
        "пустая строка: сужение пары (imprint, sheet) работает на "
        f"`sheet is not None` (мутация М2); получили: {sheet!r}")


# ═══════════════════════════════════════════════════════════════════════════
# НАБРАНО → доезжает обрезанным. Тоже каждая ветка своим адресом.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("typed,expected", [("CH0", "CH0"), (" CH0 ", "CH0")])
def test_a_typed_cluster_on_a_cell_reaches_result_data(main_window, typed,
                                                       expected):
    """С5 плана empty_cluster_normalisation, клетка «кластер | набрано |
    ячейка»: человек НАБРАЛ кластер у ячейки — он обязан выйти из формы как
    есть, а с пробелами по краям — обрезанным, но не выброшенным.

    Ловит обе правки, оставлявшие прогон зелёным:
      * М5 — безусловное `cluster = None`: набранное выбрасывается вовсе;
      * М6 — снятый .strip() при живом `or None`: ловится ТОЛЬКО случаем
        " CH0 " (в остальных случаях .strip() нечего делать).
    """
    dlg = RealCreateEntityDialog(main_window, "cell", "my_cell", [])
    assert dlg._cluster_edit is not None, (
        "у ячейки поле Cluster обязано быть — иначе сторож проверял бы не тот "
        "путь")
    dlg._cluster_edit.setText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "my_cell", (
        f"имя берётся из поля источника и правится: получили {name!r}")
    assert cluster == expected, (
        "то, что человек напечатал в поле Cluster, обязано доехать из формы "
        "как есть (мутация М5: `cluster = None`), а пробелы по краям — "
        f"обрезаться (мутация М6: снятый .strip()); набрано {typed!r}, "
        f"получили {cluster!r}, ждали {expected!r}")


@pytest.mark.parametrize(
    "typed,expected", [("Channel_1", "Channel_1"), (" Channel_1 ", "Channel_1")])
def test_a_typed_sheet_on_a_cell_reaches_result_data(main_window, typed,
                                                     expected):
    """С7 плана typed_value_survives_the_form, клетка «лист | набрано |
    ячейка»: человек НАБРАЛ лист у ЯЧЕЙКИ — он обязан выйти из формы как
    есть, а с пробелами по краям — обрезанным, но не выброшенным.

    Отдельной функцией от «набранного листа у отпечатка» (приём С5а/С5б, и
    прямое требование оснастки): мутация М7 (лист обнуляется ТОЛЬКО на
    ячейковой ветке) обязана убить эту функцию и НЕ тронуть ту — на слиянии
    веток вердикт стал бы WRONG, и дыра прошлого захода переоткрылась бы под
    другим номером.

    Последствие потери здесь тише, чем у отпечатка (там сущность клонируется
    не на тот лист), и потому опаснее: у ячейки лист сужает резолв роли, а
    роль с потерянным листом не падает — она начинает разрешаться
    неоднозначно, молча и позже.

    Роняют сторож обе правки:
      * М7 — лист обнуляется ТОЛЬКО на ячейковой ветке (`None if
        self._cluster_edit is not None else ...`): набранное выбрасывается;
      * М8 — снятый .strip() при живом `or None`: ловится ТОЛЬКО случаем
        " Channel_1 ".
    """
    dlg = RealCreateEntityDialog(main_window, "cell", "my_cell", [])
    assert dlg._cluster_edit is not None, (
        "у ячейки поле Cluster обязано быть — иначе сторож стоял бы не на той "
        "ветке формы, где живёт дыра М7")
    dlg._sheet_edit.setText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "my_cell", (
        f"имя берётся из поля источника и правится: получили {name!r}")
    assert cluster is None, (
        "кластер здесь не набирался и обязан быть None — иначе сторож ловил бы "
        f"чужую правку, а не лист: получили {cluster!r}")
    assert sheet == expected, (
        "напечатанный лист у ЯЧЕЙКИ обязан доехать из формы как есть (мутация "
        "М7: лист обнулён только на ячейковой ветке), а пробелы по краям — "
        "обрезаться (мутация М8: снятый .strip()); набрано "
        f"{typed!r}, получили {sheet!r}, ждали {expected!r}")


@pytest.mark.parametrize(
    "typed,expected", [("Channel_1", "Channel_1"), (" Channel_1 ", "Channel_1")])
def test_a_typed_sheet_on_an_imprint_reaches_result_data(main_window, typed,
                                                         expected):
    """С6 плана empty_cluster_normalisation, клетка «лист | набрано |
    отпечаток»: набранный ЛИСТ обязан доехать — это целевой лист twin-резолва
    (config/models.py:704), и потеря его значит сущность клонируется не на тот
    лист.

    Отдельной функцией — падение половины про ячейку не должно прятать эту, и
    ровно эта функция служит утверждению М7 «must_survive». Роняют её те же
    М5л/М6л (и М8) по полю листа: безусловное `sheet = None` и снятый .strip().
    """
    dlg = RealCreateEntityDialog(main_window, "imprint", "amp", [])
    assert dlg._cluster_edit is None, (
        "у отпечатка поля Cluster нет вовсе (Т2а плана create_entity_menu)")
    dlg._sheet_edit.setText(typed)

    name, cluster, sheet = dlg.result_data()

    assert name == "amp", (
        f"имя предзаполняется именем записи: получили {name!r}")
    assert cluster is None, (
        "кластер на imprint-сущности фатален при загрузке (models.py:706) и "
        f"обязан быть None: получили {cluster!r}")
    assert sheet == expected, (
        "напечатанный лист обязан доехать из формы как есть (мутация М5л: "
        "`sheet = None`), а пробелы по краям — обрезаться (мутация М6л/М8: "
        f"снятый .strip()); набрано {typed!r}, получили {sheet!r}, "
        f"ждали {expected!r}")


@pytest.mark.parametrize(
    "typed,expected", [("my_cell", "my_cell"), ("  my_cell  ", "my_cell")])
def test_a_typed_name_reaches_result_data_trimmed(main_window, typed, expected):
    """С8 плана typed_value_survives_the_form, клетка «имя | набрано |
    ячейка/отпечаток»: набранное ИМЯ обязано доехать из формы ОБРЕЗАННЫМ — и
    обрезанным именно тогда, когда оно НЕПУСТО, а не только тогда, когда
    обнуляется.

    Имя предзаполнено именем источника, поэтому человек его стирает и печатает
    своё — сторож и берёт случай «набрано поверх предзаполнения».

    Почему это не косметика: дубликат имени ловится ТОЧНЫМ совпадением строки
    (name_exists_in_list_section), так что " my_cell " и "my_cell" для неё —
    два разных имени.

    .strip() при ПУСТОМ имени держит отдельный сторож («имя из одних
    пробелов»): М9 обязана уронить ИМЕННО ЭТУ функцию и оставить тот зелёным,
    и этим доказывается, что проверено ровно то, чего он не видел.

    Роняют сторож две правки:
      * М9 — .strip() работает ровно тогда, когда обнуляет
        (`_raw = self._name_edit.text(); name = _raw if _raw.strip()
        else _raw.strip()`): ловится ТОЛЬКО случаем "  my_cell  ";
      * М10 — .strip() снят у имени целиком (контроль: красны и этот сторож,
        и сторож пустого имени).
    """
    dlg = RealCreateEntityDialog(main_window, "cell", "seed_cell", [])
    dlg._name_edit.setText(typed)
    assert dlg._name_edit.text() == typed, (
        "форма обязана держать ровно то, что набрали, — иначе сторож проверял "
        f"бы не тот случай: в поле {dlg._name_edit.text()!r}")

    name, _cluster, _sheet = dlg.result_data()

    assert name == expected, (
        "набранное имя обязано доехать из формы обрезанным по краям (мутация "
        "М9: .strip() работает только когда обнуляет; М10: .strip() снят "
        f"целиком); набрано {typed!r}, получили {name!r}, ждали {expected!r}")


# ═══════════════════════════════════════════════════════════════════════════
# ИМЯ: пустое из формы не выходит
# ═══════════════════════════════════════════════════════════════════════════
#
# ЗАМЕР, А НЕ РАССУЖДЕНИЕ. У имени, в отличие от кластера и листа, нет токена
# `or None`: `name = self._name_edit.text().strip()` — и это верно. Пустое имя
# держит не нормализация, а гейт `if not name:` в _validate, и вопрос звучит
# так: не выходит ли пустое имя из формы при КАКОМ-НИБУДЬ порядке действий.
# Ответ получен перебором (заказы полей — параметром, живой клик по кнопке OK —
# отдельным сторожем).
#
# Почему перебора достаточно: наружу ведёт ровно одна дверь — accept(). На
# кнопке OK стоит сигнал QDialogButtonBox.accepted, подключённый в __init__ к
# self.accept; Enter жмёт ту же кнопку (она default); `done(Accepted)` мимо
# accept() пользователю недоступен, а крестик/Esc дают reject.

# Заказы действий, которыми пользователь может получить пустое имя. Каждый —
# отдельный случай параметра, поэтому падение одного не прячет остальные.
EMPTY_NAME_ORDERS = [
    ("имя стёрто и оставлено пустым", "cell", "my_cell", [("name", "")]),
    ("имя из одних пробелов", "cell", "my_cell", [("name", "   ")]),
    ("имя стёрто, кластер заполнен", "cell", "my_cell",
     [("name", ""), ("cluster", "CH1")]),
    ("имя стёрто, лист заполнен", "cell", "my_cell",
     [("name", ""), ("sheet", "Sheet_1")]),
    ("имя набрано и стёрто", "cell", "my_cell",
     [("name", "buf"), ("name", "")]),
    ("имя стёрто последним действием, лист заполнен первым", "cell",
     "my_cell", [("sheet", "Sheet_1"), ("name", "")]),
    ("поле источника пусто (ячейка без имени)", "cell", "", []),
    ("отпечаток без имени", "imprint", "", []),
]


@pytest.mark.parametrize(
    "kind,source,actions",
    [(k, s, a) for _label, k, s, a in EMPTY_NAME_ORDERS],
    ids=[label for label, _k, _s, _a in EMPTY_NAME_ORDERS])
def test_an_empty_name_never_leaves_the_form(main_window, monkeypatch, kind,
                                             source, actions):
    """С4 плана empty_cluster_normalisation, Т3: пустое имя не уходит из формы
    НИ ПРИ КАКОМ порядке действий. Проверка идёт через настоящий accept() и
    подтверждается тремя вещами сразу: диалог не принят, человеку СКАЗАНО,
    почему, и форма не выдумала имя сама (иначе сторож проверял бы не тот
    случай).

    Мутация М4 (пропускать пустое имя — снять `if not name:` в _validate)
    роняет сторож: диалог принялся бы с пустым именем, и дальше в конфиг ушла
    бы entities:-запись без имени — а её потом не назвать ни в дереве, ни по
    кластеру. Мутация М10 (снять .strip() у имени целиком) роняет его на
    параметре «имя из одних пробелов».
    """
    warnings = capture_warnings(monkeypatch)
    dlg = _form_after(main_window, kind, source, actions)

    dlg.accept()

    assert dlg.result() != QDialog.DialogCode.Accepted, (
        f"порядок действий {actions!r}: форма приняла пустое имя (мутация М4); "
        f"result_data() = {dlg.result_data()!r}")
    assert warnings, (
        "отказ обязан ГОВОРИТЬ, что имени нет: молчащая кнопка — это «не "
        f"работает», а не «введите имя»; порядок действий {actions!r}")
    assert dlg.result_data()[0] == "", (
        "форма не имеет права выдумывать имя вместо пустого — иначе сторож "
        f"проверял бы не тот случай: {dlg.result_data()!r}")


def test_a_filled_name_is_accepted(main_window):
    """КОНТРОЛЬ против ложной зелени сторожа пустого имени: без него все его
    случаи были бы зелёными и в том случае, если бы _validate отказывал
    ВСЕГДА («кнопка никогда не работает»). Непустое имя — принимается, имя
    уходит как есть."""
    dlg = _form_after(main_window, "cell", "my_cell", [])

    dlg.accept()

    assert dlg.result() == QDialog.DialogCode.Accepted, (
        "с непустым именем форма обязана приниматься, иначе сторож пустого "
        "имени — сторож сломанной кнопки")
    assert dlg.result_data()[0] == "my_cell"


def test_the_ok_button_refuses_an_empty_name(main_window, monkeypatch):
    """Не только прямой accept(): кнопка OK идёт тем же путём — сигнал
    accepted → self.accept() → _validate. Здесь по ней КЛИКАЮТ, как человек, а
    не зовут accept() вручную: иначе сторож держался бы за внутренний вызов, а
    не за ту дверь, которой пользуется пользователь (Enter жмёт эту же кнопку —
    она default).

    Роняет его мутация М4 (снятый гейт `if not name:`).
    """
    from PyQt6.QtWidgets import QDialogButtonBox

    warnings = capture_warnings(monkeypatch)
    dlg = _form_after(main_window, "cell", "my_cell", [("name", "")])
    ok = dlg.findChild(QDialogButtonBox).button(
        QDialogButtonBox.StandardButton.Ok)
    assert ok is not None, "у формы обязана быть кнопка OK"

    ok.click()

    assert dlg.result() != QDialog.DialogCode.Accepted, (
        "кнопка OK обязана идти через accept()/_validate, а не закрывать "
        "диалог напрямую — иначе пустое имя выходит из формы мимо гейта "
        "(мутация М4)")
    assert warnings, "человеку обязано быть сказано, что имени нет"
