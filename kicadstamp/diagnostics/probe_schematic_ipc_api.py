#!/usr/bin/env python3
"""
probe_schematic_ipc_api.py — простукивает НЕдокументированный SCH-слой IPC-API KiCad.

Зачем это нужно:
    fieldstool (kicadstamp.schematic_*) работает со схемами ОФЛАЙН — байтовым
    сплайсингом .kicad_sch через sexpdata. Но у kipy 0.7.1 есть класс
    `kipy.schematic.Schematic` (get_items / get_hierarchy / get_as_string /
    begin_commit ...), помеченный «versionadded 0.7.0 (KiCad 11)», и готового
    `get_schematic()` в kipy нет. Этот скрипт эмпирически выясняет:

      1) в каком состоянии находится сам schematic-слой установленного kipy
         (импортируется ли `kipy.schematic`, что реально лежит в сгенерированных
         proto-модулях, есть ли типы Symbol/SchematicField) — «простукивание
         библиотеки»;
      2) что РЕАЛЬНО поддерживает подключённый инстанс KiCad (на практике 10.x)
         по IPC для схем: типы документов от get_open_documents(), отвечает ли
         живой KiCad на сырые команды GetItems / SaveDocumentToString /
         GetTitleBlockInfo, и какие типы объектов возвращаются (в т.ч. символы
         и поля);
      3) можно ли через IPC РЕДАКТИРОВАТЬ/СОЗДАВАТЬ поля на символах (интерес
         проекта — Role/Cluster): отдельный раздел + попытка записать тестовое
         поле `TestRule` за флагом --try-write.

    Практическая ценность: вердикт скрипта — карта «что доступно на живой схеме
    по IPC» — нужен, чтобы решить, можно ли частично заменить файловый парсинг
    схематики живым IPC (или хотя бы собрать иерархию листов / текст схемы оттуда,
    а в перспективе — писать поля).

Безопасность:
    По умолчанию скрипт СТРОГО READ-ONLY: только GetVersion / GetOpenDocuments /
    GetItems / GetTitleBlockInfo / SaveDocumentToString и чтение свойств.
    НИКАКИХ save/revert/create/update/remove/set_title_block. За флагом --try-write:
      * begin_commit() → drop_commit() БЕЗ изменения объектов (транзакция
        открывается и отбрасывается);
      * попытка записать тестовое поле `TestRule` на ПЕРВОМ символе схемы — с
        немедленным восстановлением исходных полей; выполняется ТОЛЬКО если
        высокоуровневый kipy.schematic вообще импортируется (в текущей установке
        0.7.1 — нет, поэтому печатается точная причина блокировки, а не мутация).

Требования:
    Запущенный KiCad с ОТКРЫТОЙ схемой (для осмысленного прогона) и работающим
    IPC-сокетом. Подключение для Tier A (состояние библиотеки) не нужно, но
    смысл пробы — в сочетании с живым KiCad.

Запуск:
    python -m kicadstamp.diagnostics.probe_schematic_ipc_api
    python -m kicadstamp.diagnostics.probe_schematic_ipc_api --timeout-ms 30000
    python -m kicadstamp.diagnostics.probe_schematic_ipc_api --all-types
    python -m kicadstamp.diagnostics.probe_schematic_ipc_api --try-write

Коды выхода:
    0 — скрипт отработал (включая случай «схем не открыто» и «все команды упали» —
        это ДАННЫЕ пробы, не ошибка скрипта);
    1 — не удалось подключиться к KiCad по IPC;
    2 — непредвиденная ошибка скрипта.
"""
import argparse
import io
import logging
import sys
import time
import traceback

# Та самая грабля с прошлого раза: print() в перенаправленный (>) файл на
# Windows берёт кодировку консоли (обычно cp1251), а не UTF-8 — форсируем явно
# (тот же приём, что в остальных диагностиках, напр. probe_uuid_to_sheet_name.py).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import kipy  # noqa: E402
from kipy.proto.common.types import (  # noqa: E402
    DocumentSpecifier,
    DocumentType,
    KiCadObjectType,
    base_types_pb2,
)
from kipy.proto.common.commands import editor_commands_pb2  # noqa: E402

logger = logging.getLogger("probe_schematic_ipc_api")

# Счётчики для итоговой сводки
_stats = {"ok": 0, "fail": 0}

# Результаты GetItems по типам — чтобы раздел про поля не дублировал IPC-запросы
_sch_item_counts: dict[str, int | None] = {}

# На KiCad 10 GetItems не существует как команда («no handler available») —
# флаг поднимается при любой упавшей пробе GetItems, чтобы итог различал
# «сервер не умеет» и «библиотека не умеет».
_server_no_getitems = False


# ── Утилиты вывода ──────────────────────────────────────────────────────────────


def _short(value, limit: int = 140) -> str:
    """repr() с обрезкой — чтобы не вываливать в консоль гигантские объекты."""
    s = repr(value)
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _summarize(value) -> str:
    """Короткое описание результата пробы: списки — count + первые элементы."""
    if value is None:
        return "None"
    if isinstance(value, (list, tuple)):
        items = ", ".join(_short(v, 60) for v in value[:3])
        more = "..." if len(value) > 3 else ""
        return f"len={len(value)} [{items}{more}]"
    if isinstance(value, str):
        return f"str len={len(value)} {_short(value[:80], 100)}"
    return _short(value)


def probe(label: str, fn):
    """Выполняет одну пробу, ловит любые исключения и печатает [OK]/[FAIL].

    Возвращает результат (или None при ошибке) — чтобы вызывающий мог
    продолжить цепочку проб."""
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 — проба должна пережить ЛЮБУЮ ошибку API
        _stats["fail"] += 1
        msg = str(e).replace("\n", " ")[:200]
        print(f"  [FAIL] {label}: {type(e).__name__}: {msg}")
        return None
    _stats["ok"] += 1
    print(f"  [OK]   {label}: {_summarize(result)}")
    return result


def _enum_members(enum_cls):
    """Имя -> значение для proto-энума (items() есть у google.protobuf enum)."""
    try:
        return dict(enum_cls.items())
    except Exception:  # noqa: BLE001
        return {}


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ── Tier A: состояние schematic-слоя БИБЛИОТЕКИ kipy (без живого KiCad) ────────


def probe_library_surface() -> bool:
    """Импортируется ли высокоуровневый kipy.schematic. Возвращает True/False.

    В установленном kipy 0.7.1 (на момент написания) он НЕ импортируется:
    kipy/schematic_types.py ждёт `BusEntryType`, которого нет в
    schematic_types_pb2.py — враппер и сгенерированные proto разошлись
    (враппер — из более свежего, KiCad-11-эры API)."""
    section("Tier A: состояние schematic-слоя библиотеки kipy")
    try:
        from kipy.schematic import Schematic  # noqa: F401
    except Exception as e:  # noqa: BLE001
        _stats["fail"] += 1
        print(f"  [FAIL] import kipy.schematic: {type(e).__name__}: {str(e).replace(chr(10), ' ')[:300]}")
        print("         -> высокоуровневый враппер НЕ доступен; Tier B пойдёт сырыми командами")
        return False
    _stats["ok"] += 1
    print("  [OK]   import kipy.schematic — высокоуровневый Schematic-враппер доступен")
    return True


def probe_schematic_protos() -> None:
    """Что реально лежит в сгенерированных proto-модулях schematic-пакета kipy
    и в общих editor_commands — включая типы, нужные для РЕДАКТИРОВАНИЯ ПОЛЕЙ."""
    import kipy.proto.schematic.schematic_commands_pb2 as cmds_pb
    import kipy.proto.schematic.schematic_types_pb2 as types_pb

    cmd_members = [n for n in dir(cmds_pb) if not n.startswith("_")]
    type_members = [n for n in dir(types_pb) if not n.startswith("_")]
    print(f"  schematic_commands_pb2: {len(cmd_members)} членов -> {cmd_members}")
    print(f"  schematic_types_pb2:    {len(type_members)} членов -> {type_members}")
    print(f"  BusEntryType в schematic_types_pb2:            {hasattr(types_pb, 'BusEntryType')}")
    print(f"  GetSchematicHierarchy в schematic_commands_pb2: {hasattr(cmds_pb, 'GetSchematicHierarchy')}")

    # Типы, без которых редактирование/создание полей через IPC невозможно
    print("  Типы для ПОЛЕЙ СИМВОЛОВ в schematic_types_pb2:")
    for name in ("SchematicField", "Symbol", "SchematicSymbol", "SchematicSymbolInstance",
                 "SchematicSymbolAttributes"):
        print(f"    {name}: {hasattr(types_pb, name)}")

    obj_types = _enum_members(KiCadObjectType)
    print(f"  KiCadObjectType: KOT_SCH_SYMBOL={ 'KOT_SCH_SYMBOL' in obj_types }, "
          f"KOT_SCH_FIELD={ 'KOT_SCH_FIELD' in obj_types }")

    print("  Общие (editor_commands) команды, доступные для сырой пробы:")
    for name in ("GetItems", "GetItemsResponse", "GetItemsById", "GetItemsByIdResponse",
                 "SaveDocumentToString", "SavedDocumentResponse", "GetTitleBlockInfo",
                 "SetTitleBlockInfo", "CreateItems", "UpdateItems", "DeleteItems"):
        print(f"    editor_commands_pb2.{name}: {hasattr(editor_commands_pb2, name)}")


# ── Общие пробы на подключённом KiCad ──────────────────────────────────────────


def probe_kicad_surface(kc) -> None:
    """Что видно на объекте KiCad: ищем скрытые/недокументированные методы,
    связанные со схемой/документами/проектом."""
    section("Объект KiCad: поверхность (dir, отфильтровано)")
    names = sorted(n for n in dir(kc) if not n.startswith("__"))
    interesting = [n for n in names
                   if any(k in n.lower() for k in ("schem", "doc", "project", "board"))]
    print("  Члены, похожие на SCH/документы/проект:")
    for n in interesting:
        attr = getattr(kc, n)
        kind = "method" if callable(attr) else type(attr).__name__
        print(f"    {n}  ({kind})")
    if not interesting:
        print("    (не найдено — вероятно, ничего, кроме стандартных get_board/get_project)")
    logger.debug("dir(kc) = %s", names)


def probe_schematic_getters(kc) -> None:
    """Активно ищем ЛЮБОЙ schematic-getter на объекте KiCad — «а вдруг найдётся
    какой-нибудь schematic IPC API»: перебираем правдоподобные имена методов и
    пробуем вызвать (без аргументов). Отсутствие метода или ошибка вызова — тоже
    данные: это часть простукивания недокументированной поверхности."""
    section("Поиск любого schematic-getter на объекте KiCad")
    for name in ("get_schematic", "get_schematic_documents", "get_schematic_document",
                 "get_document", "get_documents", "get_editable_document",
                 "open_document", "get_editor"):
        fn = getattr(kc, name, None)
        if fn is None:
            print(f"  [--]   kc.{name}(): метода нет")
            continue
        probe(f"kc.{name}()", lambda f=fn: f())


def probe_documents(kc) -> list:
    """Перечисляет DocumentType и для каждого типа спрашивает открытые документы.
    Возвращает (schematic_docs, pcb_project): список DocumentSpecifier для
    SCHEMATIC-документов и project PCB-документа (для уточнённых проб Tier B2)."""
    section("Открытые документы (get_open_documents по каждому типу)")
    doc_types = _enum_members(DocumentType)
    if not doc_types:
        probe("DocumentType.items() (интроспекция энума)", lambda: _enum_members(DocumentType))
        print("  !!! не удалось перечислить DocumentType — пробуем по одному числовому значению")
        doc_types = {"DOCTYPE_PCB": 0, "DOCTYPE_SCHEMATIC": 1, "DOCTYPE_PROJECT": 2}
    print(f"  Значения DocumentType ({len(doc_types)}): {', '.join(sorted(doc_types))}")

    schematic_docs = []
    pcb_project: dict | None = None
    for name, value in sorted(doc_types.items()):
        docs = probe(f"get_open_documents({name})", lambda v=value: list(kc.get_open_documents(v)))
        if not docs:
            continue
        for i, doc in enumerate(docs):
            print(f"    doc[{i}]: {_short(doc, 200)}")
            for field in ("type", "sheet_path"):
                try:
                    print(f"      .{field} = {_short(getattr(doc, field))}")
                except Exception as e:  # noqa: BLE001
                    print(f"      .{field} = <err {type(e).__name__}>")
            try:
                proj = doc.project
                print(f"      .project.name = {_short(proj.name)}  .project.path = {_short(proj.path)}")
            except Exception as e:  # noqa: BLE001
                print(f"      .project = <err {type(e).__name__}>")
            if "PCB" in name:
                try:
                    if doc.project.name:
                        pcb_project = {"name": doc.project.name, "path": doc.project.path}
                except Exception:  # noqa: BLE001
                    pass
            if "SCHEMATIC" in name:
                schematic_docs.append(doc)
    return schematic_docs, pcb_project


# ── Tier B: сырые команды через client.send (обход сломанного враппера) ───────


def _send(client, command, response_type):
    """Обёртка над низкоуровневым client.send (тот же путь, что у kipy.schematic)."""
    return client.send(command, response_type)


def probe_raw_commands(client, doc, all_types: bool) -> None:
    """Сырые read-only команды на открытый schematic-документ, не зависящие от
    сломанного высокоуровневого враппера kipy.schematic."""
    section("Tier B: сырые команды через client.send (read-only)")

    obj_types = _enum_members(KiCadObjectType)
    if not obj_types:
        print("  !!! не удалось перечислить KiCadObjectType")
        return
    sch_types = {n: v for n, v in obj_types.items() if "KOT_SCH_" in n}
    print(f"  Всего KiCadObjectType: {len(obj_types)}, SCH-типов: {len(sch_types)}")

    # GetItems по каждому SCH-типу — что живой KiCad реально отдаёт по схеме
    for name in sorted(sch_types):
        def _get_items(n=name, v=sch_types[name]):
            global _server_no_getitems
            cmd = editor_commands_pb2.GetItems()
            cmd.header.document.CopyFrom(doc)
            cmd.types.append(v)
            try:
                resp = _send(client, cmd, editor_commands_pb2.GetItemsResponse)
            except Exception as e:
                if "no handler available" in str(e):
                    _server_no_getitems = True
                raise
            type_names = [it.type_url.rsplit(".", 1)[-1] for it in resp.items]
            _sch_item_counts[n] = len(resp.items)
            return f"status={resp.status} count={len(resp.items)} item_types={type_names[:8]}"
        probe(f"GetItems({name})", _get_items)

    if all_types:
        for name, value in sorted(obj_types.items()):
            if name in sch_types:
                continue
            def _get_items_all(n=name, v=value):
                global _server_no_getitems
                cmd = editor_commands_pb2.GetItems()
                cmd.header.document.CopyFrom(doc)
                cmd.types.append(v)
                try:
                    resp = _send(client, cmd, editor_commands_pb2.GetItemsResponse)
                except Exception as e:
                    if "no handler available" in str(e):
                        _server_no_getitems = True
                    raise
                type_names = [it.type_url.rsplit(".", 1)[-1] for it in resp.items]
                return f"status={resp.status} count={len(resp.items)} item_types={type_names[:8]}"
            probe(f"GetItems({name})", _get_items_all)

    # Весь текст схемы по IPC — прямой кандидат на замену файлового чтения
    def _save_to_string():
        cmd = editor_commands_pb2.SaveDocumentToString()
        cmd.document.CopyFrom(doc)
        resp = _send(client, cmd, editor_commands_pb2.SavedDocumentResponse)
        return resp.contents
    probe("SaveDocumentToString (весь текст схемы по IPC)", _save_to_string)

    def _title_block():
        cmd = editor_commands_pb2.GetTitleBlockInfo()
        cmd.document.CopyFrom(doc)
        return _send(client, cmd, base_types_pb2.TitleBlockInfo)
    probe("GetTitleBlockInfo", _title_block)


# ── Поля символов: что отдаёт сервер и можно ли писать ─────────────────────────


def probe_fields_summary() -> None:
    """Интерпретация результатов GetItems для полей/символов + блокировки записи."""
    section("Поля символов: что получилось (по данным GetItems выше)")
    sym_count = _sch_item_counts.get("KOT_SCH_SYMBOL")
    field_count = _sch_item_counts.get("KOT_SCH_FIELD")
    print(f"  GetItems(KOT_SCH_SYMBOL)  -> count={sym_count}")
    print(f"  GetItems(KOT_SCH_FIELD)   -> count={field_count}")
    if _server_no_getitems:
        print("  GetItems как команда НЕ существует на этом сервере KiCad "
              "(\"no handler available\") — чтение/запись полей через item-API невозможна "
              "на этой версии KiCad, независимо от библиотеки.")
    elif sym_count:
        print("  Сервер ОТДАЁТ символы по IPC, но в локальной схеме kipy нет сообщения "
              "Symbol/SchematicSymbolInstance — распаковать/прочитать их поля текущий kipy не может.")
    else:
        print("  Символы по IPC сервер не отдал (None = команда упала; 0 = пусто).")
    print("  ВЫВОД: редактирование/создание полей через IPC заблокировано ДВОЙНО:")
    print("    1) сервер KiCad 10 не регистрирует GetItems (item-API — это KiCad 11);")
    print("    2) библиотека kipy 0.7.1 сломана: враппер не импортируется (BusEntryType),")
    print("       и в schematic_types_pb2 нет типов Symbol/SchematicField.")
    print("  Офлайн-сплайсинг .kicad_sch (kicadstamp.schematic_*) остаётся рабочим путём.")
    print("  Отдельно: SaveDocumentToString/GetTitleBlockInfo имеют handler и падают только на "
          "неполном DocumentSpecifier (пустые project/sheet_path) — см. Tier B2 ниже.")


def _copy_document_specifier(doc, *, sheet_path: str | None = None,
                             project_name: str | None = None,
                             project_path: str | None = None) -> DocumentSpecifier:
    """Копия DocumentSpecifier с подстановкой sheet_path/project — для Tier B2."""
    d = DocumentSpecifier()
    d.CopyFrom(doc)
    if sheet_path is not None:
        # sheet_path — это сообщение SheetPath (поля: path: KIID, path_human_readable: string);
        # для корневого листа человекочитаемый путь — '/'. Прямое присваивание сообщению
        # не разрешено ("Assignment not allowed to message field"), заполняем поле внутри.
        d.sheet_path.path_human_readable = sheet_path
    if project_name is not None:
        d.project.name = project_name
    if project_path is not None:
        d.project.path = project_path
    return d


def probe_corrected_document_specifier(client, doc, pcb_project) -> None:
    """SaveDocumentToString/GetTitleBlockInfo на KiCad 10 имеют handler, но
    get_open_documents(DOCTYPE_SCHEMATIC) вернул документ с ПУСТЫМИ project и
    sheet_path — сервер отвечает «document is not open». Пробуем уточнённые
    варианты спецификатора (read-only): sheet_path='/', project из PCB-документа.
    Если хоть один вариант сработает — значит текст схемы (и title block) можно
    получать по IPC даже на KiCad 10."""
    section("Tier B2: уточнённый DocumentSpecifier для SaveDocumentToString/GetTitleBlockInfo")
    variants: dict[str, DocumentSpecifier] = {
        "sheet_path='/'": _copy_document_specifier(doc, sheet_path="/"),
    }
    if pcb_project:
        variants["sheet_path='/' + project(из PCB)"] = _copy_document_specifier(
            doc, sheet_path="/",
            project_name=pcb_project.get("name", ""),
            project_path=pcb_project.get("path", ""),
        )
        variants["project(из PCB), sheet_path пустой"] = _copy_document_specifier(
            doc, project_name=pcb_project.get("name", ""),
            project_path=pcb_project.get("path", ""),
        )
    else:
        print("  (нет project из PCB-документа — нечем заполнить project спецификатора)")

    for label, spec in variants.items():
        def _save(s=spec):
            cmd = editor_commands_pb2.SaveDocumentToString()
            cmd.document.CopyFrom(s)
            resp = _send(client, cmd, editor_commands_pb2.SavedDocumentResponse)
            return resp.contents
        probe(f"SaveDocumentToString [{label}]", _save)

        def _tb(s=spec):
            cmd = editor_commands_pb2.GetTitleBlockInfo()
            cmd.document.CopyFrom(s)
            return _send(client, cmd, base_types_pb2.TitleBlockInfo)
        probe(f"GetTitleBlockInfo [{label}]", _tb)


def probe_field_write_attempt(client, doc, schematic_importable: bool) -> None:
    """Попытка записать тестовое поле `TestRule` на первом символе — за --try-write.

    Выполняется ТОЛЬКО когда высокоуровневый kipy.schematic импортируется
    (сейчас нет). Иначе печатаем точную причину блокировки, не трогая схему.
    При реальной попытке: снимок полей -> добавить TestRule -> update_items ->
    немедленное восстановление исходных полей (тоже update_items).
    """
    section("Поля символов: попытка записать тестовое поле 'TestRule' (--try-write)")

    if not schematic_importable:
        print("  Блокировка на уровне библиотеки (см. Tier A):")
        print("    - import kipy.schematic падает (нет BusEntryType в schematic_types_pb2);")
        print("    - в schematic_types_pb2 нет типов SchematicField/Symbol/SchematicSymbolInstance,")
        print("      которыми высокоуровневый враппер собирает сообщения для Create/UpdateItems.")
        print("  -> Нечем собрать сообщение «добавить поле» — запись TestRule через IPC НЕВОЗМОЖНА")
        print("     в текущей установке kipy 0.7.1, независимо от того, что умеет сервер KiCad.")
        print("  Что нужно, чтобы проба реально сработала: обновить kipy (или перегенерировать")
        print("  schematic_types/schematic_commands proto из свежего KiCad API) — тогда этот код")
        print("  запишет и вернёт поле сам.")
        return

    from kipy.schematic import Schematic  # дошли только если импорт рабочий

    sch = probe("Schematic(client, doc)", lambda: Schematic(client, doc))
    if sch is None:
        return

    symbols = probe("sch.get_symbols()", lambda: sch.get_symbols())
    if not symbols:
        print("  Символов на схеме нет (или get_symbols упал) — не на ком создавать поле.")
        return

    sym = symbols[0]
    ref = "?"
    try:
        ref = sym.reference_field.text.value
    except Exception:  # noqa: BLE001
        pass
    print(f"  Цель: первый символ (ref={ref}). Снимок исходных полей и восстановление после пробы.")

    # Снимок исходных user_fields (объекты SchematicField — вернём их же по ссылкам)
    before_fields = []
    try:
        before_fields = list(sym.user_fields)
    except Exception as e:  # noqa: BLE001
        print(f"  (не удалось прочитать user_fields: {type(e).__name__}: {e})")

    def _write_test_field():
        from kipy.schematic_types import SchematicField
        value = f"probe-{int(time.time())}"
        new = SchematicField()
        new.name = "TestRule"
        new.text.value = value
        # заменяем/добавляем TestRule, остальные поля не трогаем
        sym.user_fields = [f for f in sym.user_fields if f.name != "TestRule"] + [new]
        sch.update_items([sym])
        return value

    written = probe("запись поля TestRule (update_items + commit)", _write_test_field)

    if written is not None:
        # Немедленное восстановление исходных полей — той же операцией update_items
        def _restore():
            sym.user_fields = list(before_fields)
            sch.update_items([sym])
            return "restored"

        probe("восстановление исходных полей (update_items)", _restore)
        print("  Примечание: поле было записано и тут же откачено; в файле схемы его не осталось.")


# ── Высокоуровневый Schematic (если kipy.schematic импортируется) ──────────────


def probe_schematic_wrapper(client, doc, try_write: bool) -> None:
    """Пробы на хендле kipy.schematic.Schematic — только если враппер доступен.
    В текущей установке kipy 0.7.1 эта ветка не выполняется (см. Tier A)."""
    section("Высокоуровневый Schematic (kipy.schematic)")
    from kipy.schematic import Schematic

    sch = probe("Schematic(client, doc)", lambda: Schematic(client, doc))
    if sch is None:
        return

    probe("sch.name", lambda: sch.name)
    probe("sch.document", lambda: sch.document)
    probe("sch.client (тип)", lambda: type(sch.client).__name__)

    read_only_methods = [
        ("get_project", ()),
        ("get_hierarchy", ()),
        ("get_as_string", ()),
        ("get_selection_as_string", ()),
        ("get_title_block", ()),
        ("get_symbols", ()),
        ("get_sheet_symbols", ()),
        ("get_labels", ()),
        ("get_groups", ()),
        ("get_lines", ()),
        ("get_text", ()),
        ("get_shapes", ()),
        ("get_images", ()),
    ]
    for name, args in read_only_methods:
        fn = getattr(sch, name, None)
        if fn is None:
            print(f"  [SKIP] {name}: метода нет на объекте")
            continue
        probe(f"sch.{name}()", lambda f=fn, a=args: f(*a))

    # Единственное "пишущее" исключение — за флагом: begin_commit → drop_commit,
    # без создания/изменения объектов (push_commit НЕ вызывается).
    if try_write:
        section("Try-write: begin_commit() -> drop_commit() (без изменений объектов)")
        commit = probe("sch.begin_commit()", lambda: sch.begin_commit())
        if commit is not None:
            probe("sch.drop_commit(commit)", lambda: sch.drop_commit(commit))
        print("  Примечание: push_commit НЕ вызывался — на плату/файл ничего не попало.")


# ── Точка входа ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Простукивание недокументированного SCH-слоя IPC-API KiCad через kipy")
    # 20 s ON PURPOSE (Э4, plan_2026_09_13_timeout_sweep) — deliberately NOT
    # DEFAULT_TIMEOUT_MS: this probe calls UNDOCUMENTED SCH-layer IPC methods
    # whose latency is unknown by definition, and a blocking one only ends when
    # the socket gives up — so the budget stays generous on purpose.
    parser.add_argument("--timeout-ms", type=int, default=20000,
                        help="таймаут IPC в мс (по умолчанию 20000)")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG-логирование (в т.ч. внутренности kipy)")
    parser.add_argument("--all-types", action="store_true",
                        help="пробовать GetItems по ВСЕМ KiCadObjectType, не только KOT_SCH_*")
    parser.add_argument("--try-write", action="store_true",
                        help="проверить begin_commit()/drop_commit() и попытку записи тестового поля "
                             "TestRule на первом символе (с немедленным восстановлением)")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    print("=== KiCadStamp: probe_schematic_ipc_api ===")
    print(f"flags: timeout_ms={args.timeout_ms} all_types={args.all_types} try_write={args.try_write}")

    try:
        kc = kipy.KiCad(timeout_ms=args.timeout_ms)
    except Exception as e:  # noqa: BLE001
        print(f"НЕ удалось подключиться к KiCad по IPC: {type(e).__name__}: {e}")
        print("Убедитесь, что KiCad запущен и IPC-сокет доступен (KICAD_API_SOCKET).")
        return 1

    # Версии
    section("Версии")
    probe("kc.get_version()", lambda: kc.get_version())
    probe("kc.get_api_version()", lambda: kc.get_api_version())
    probe("kc.ping()", lambda: kc.ping())

    probe_kicad_surface(kc)
    probe_schematic_getters(kc)

    schematic_importable = probe_library_surface()
    probe_schematic_protos()

    schematic_docs, pcb_project = probe_documents(kc)

    if not schematic_docs:
        print("\n=== Итог ===")
        print("Открытых SCHEMATIC-документов не найдено — Tier B (сырые команды на схему) "
              "и раздел про поля выполнить не на чем.")
        print("Откройте схему в KiCad и запустите снова.")
        print(f"пробы: [OK]={_stats['ok']} [FAIL]={_stats['fail']}")
        return 0

    client = getattr(kc, "_client", None)
    if client is None:
        print("\n[FAIL] нет kc._client — нельзя отправить сырые команды")
        print(f"пробы: [OK]={_stats['ok']} [FAIL]={_stats['fail']}")
        return 0

    doc = schematic_docs[0]
    probe_raw_commands(client, doc, all_types=args.all_types)
    probe_fields_summary()
    probe_corrected_document_specifier(client, doc, pcb_project)

    if args.try_write:
        probe_field_write_attempt(client, doc, schematic_importable=schematic_importable)

    if schematic_importable:
        probe_schematic_wrapper(client, doc, try_write=args.try_write)

    print("\n=== Итог ===")
    print(f"пробы: [OK]={_stats['ok']} [FAIL]={_stats['fail']}")
    print("FAIL на SCH-командах при живой схеме почти наверняка означает «команда "
          "недоступна в этой версии KiCad/kipy» (класс Schematic помечен KiCad 11).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — любой сбой пробы не должен валиться молча
        print("\n=== АВАРИЙНЫЙ ВЫХОД (exit 2): непредвиденная ошибка скрипта ===")
        traceback.print_exc()
        sys.exit(2)
