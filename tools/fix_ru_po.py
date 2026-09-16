#!/usr/bin/env python3
"""Surgical RU translation fill for locales/ru/LC_MESSAGES/kicadstamp.po.

Background (2026-08-08): the last tools/update_i18n.py run pulled in a backlog
of strings that were previously never in the catalog — 401 entries with an
empty msgstr and 240 with a '#, fuzzy' flag (pybabel auto-matched old text to a
similar-but-different msgid). fuzzy entries are NOT translations: pybabel
compile ignores them, so the runtime falls back to English.

This script ONLY touches entries that are fuzzy or have an empty msgstr. Every
other entry is left byte-for-byte untouched. Placeholders ({count}, {ref},
{name!r}, {x:.3f}, ...) are preserved identically between msgid and msgstr.

Run: python tools/fix_ru_po.py   (from the repo root)
"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PATH = "locales/ru/LC_MESSAGES/kicadstamp.po"

# --------------------------------------------------------------------------
# msgid -> RU msgstr. Keys are the exact unquoted msgid text.
# --------------------------------------------------------------------------
T = {
    # kicadstamp_cli.py
    "Ignore the current PCB editor selection for the whole run — role-based ClonePlacements (role: without nets:/params:) and ambiguity narrowing normally fall back to whatever is selected in KiCad; a stray leftover selection then either fatals or silently changes the resolved candidate. With this flag every such lookup behaves as if nothing were selected.":
    "Игнорировать текущее выделение в редакторе PCB на весь прогон — ролевые ClonePlacements (role: без nets:/params:) и сужение неоднозначности по умолчанию опираются на выделенное в KiCad; случайно оставшееся выделение тогда либо фаталит, либо молча меняет выбранного кандидата. С этим флагом каждый такой поиск ведёт себя так, будто ничего не выделено.",
    "Directory with operation_*.json undo logs (default: logs/ next to the current working directory)":
    "Каталог с undo-логами operation_*.json (по умолчанию: logs/ рядом с текущим рабочим каталогом)",
    "Write this via/track net as null instead of its literal name (e.g. '+3V3') — at apply time a ManualSpoke-placed cell's via/track with net: null inherits the enclosing Rule's own net (spoke_layout.py's 'via.net or rule_net'), so this makes the cell reusable across Rules on different nets. Can be repeated. Fatal if the same net is also in --param/--net-template.":
    "Записать цепь этой via/трека как null вместо буквального имени (например, '+3V3') — при применении via/трек шаблона, размещённого через ManualSpoke, с net: null наследует цепь объемлющего правила (via.net or rule_net в spoke_layout.py), поэтому шаблон можно переиспользовать между разными правилами на разных цепях. Флаг можно повторять. Фатально, если та же цепь указана и в --param/--net-template.",
    "Note: the first argument was taken as a config path for 'apply' (bare-config shorthand). If you meant a subcommand, spell it exactly: apply, undo, extract, clone-extract.":
    "Примечание: первый аргумент был принят как путь к конфигу для 'apply' (сокращение «голый конфиг»). Если вы имели в виду подкоманду, напишите её точно: apply, undo, extract, clone-extract.",
    "Extra clearance for collision check in mm":
    "Дополнительный зазор при проверке коллизий, мм",
    "Process only rules/clone_placements/thermal_via_arrays with this identity (rule name if set, else its net; clone_placement/thermal_via_arrays entry name). Repeatable and/or comma-separated (--only a,b --only c). Everything else is ignored in this run.":
    "Обработать только rules/clone_placements/thermal_via_arrays с такой идентичностью (имя правила, если задано, иначе его цепь; имя записи clone_placement/thermal_via_arrays). Флаг можно повторять и/или указывать через запятую (--only a,b --only c). Всё остальное в этом прогоне игнорируется.",
    "Take net/pcb/channel/output from profile NAME in --profiles file (cannot combine with explicit flags)":
    "Взять net/pcb/channel/output из профиля NAME в файле --profiles (нельзя сочетать с явными флагами)",
    "Extract spoke cell from current selection":
    "Извлечь шаблон спицы из текущего выделения",
    "Take name/output/param/net-template/origin-by-* from profile NAME in --profiles file (cannot combine with explicit flags)":
    "Взять name/output/param/net-template/origin-by-* из профиля NAME в файле --profiles (нельзя сочетать с явными флагами)",

    # gui/docks/trees_dock.py — the mount node's point marker
    # (plan_2026_09_16_mount_point_marker)
    # "Read from board" is NOT here: that msgid already exists (the PointsDock
    # button) and is translated there — the mount form reuses the same wording.
    "Show point on board": "Показать точку на плате",
    "Remove from board": "Убрать с платы",
    "Mount point: the figures could not be removed from the board ({error})":
    "Точка mount: фигуры не удалось убрать с платы ({error})",
    "Mount point: the marker was not drawn — the overlay layer {layer!r} is not enabled on this board, or the board read failed ({error}).":
    "Точка mount: маркер не нарисован — слой оверлея {layer!r} не включён на этой плате, либо не удалось прочитать плату ({error}).",
    "Mount point: the marker was drawn, but the base square was not ({error}).":
    "Точка mount: маркер нарисован, а квадратик базы — нет ({error}).",
    "Mount point: no point is shown on the board — press “Show point on board” first.":
    "Точка mount: на плате не показана точка — сначала нажмите «Показать точку на плате».",
    "Mount point: the marker is not on the board any more — the offset was not read.":
    "Точка mount: маркера больше нет на плате — смещение не прочитано.",

    # gui/fieldstool_window.py / main_window.py
    "Rescan": "Пересканировать",
    "Not connected": "Нет подключения",
    "Nothing selected": "Ничего не выбрано",
    "Role:": "Роль:",
    "Cluster:": "Кластер:",
    "{count} selected: {refs}": "Выбрано: {count}: {refs}",
    "Not yet applied to schematic: {refs}": "Ещё не применено к схеме: {refs}",
    "Set Role and/or Cluster first.": "Сначала задайте Role и/или Cluster.",
    "Connect to KiCad first.": "Сначала подключитесь к KiCad.",
    "These targets have no such field on their footprint yet — nothing was written for them (use Ensure fields... below, or add the field by hand, then Update PCB from Schematic):\n{refs}":
    "У этих целей на футпринте ещё нет такого поля — для них ничего не записано (используйте Ensure fields... ниже, либо добавьте поле вручную и сделайте Update PCB from Schematic):\n{refs}",
    "Not connected: {error}": "Нет подключения: {error}",
    "About to write {count} change(s):": "Будет записано изменений: {count}:",
    "KiCad appears to be running. Save your work and close KiCad, then click Apply again — this tool never closes KiCad for you (see docs/fieldstool.md for why).":
    "Похоже, KiCad запущен. Сохраните работу и закройте KiCad, затем нажмите Apply снова — этот инструмент сам никогда не закрывает KiCad (почему — см. docs/fieldstool.md).",
    "Cannot ensure fields": "Не удалось добавить поля",
    "Refresh": "Обновить",
    "KiCadStamp": "KiCadStamp",
    "Quit": "Выйти",
    " (see Log for details)": " (подробности в журнале)",
    "name (referenced by cell: elsewhere)": "имя (на него ссылаются cell: в другом месте)",
    "pad (optional)": "пад (необязательно)",
    "optional — TemplatePlacer role matching": "необязательно — сопоставление роли TemplatePlacer",
    "Add": "Добавить",
    "Drill:": "Сверло:",
    "Diameter:": "Диаметр:",
    "Width:": "Ширина:",
    "Rotation (deg):": "Поворот (град):",
    "Offset across": "Смещение поперёк",
    "Component added — remember to Save the cell.": "Компонент добавлен — не забудьте сохранить шаблон.",
    "Drill": "Сверло",
    "Via removed — remember to Save the cell.": "Via удалена — не забудьте сохранить шаблон.",
    "Pick a file in the Config tree first.": "Сначала выберите файл в дереве Config.",
    "Rules": "Правила",
    "Clone placements": "Клонируемые расстановки",
    "Cells": "Шаблоны",
    "Points": "Точки",
    "Recent...": "Недавние...",
    "No root file open": "Корневой файл не открыт",
    "Delete...": "Удалить...",
    "Add cell...": "Добавить шаблон...",
    "Add thermal via pad...": "Добавить термопад via...",
    "Add included file...": "Добавить включаемый файл...",
    "Remove this file": "Удалить этот файл",
    "If any CLI command uses --only/--profile {old!r}, update that separately — this only rewrites YAML files, it can't see command-line usage.":
    "Если какая-то CLI-команда использует --only/--profile {old!r}, обновите её отдельно — здесь переписываются только YAML-файлы, командную строку скрипт не видит.",
    "{file}: {entries}": "{file}: {entries}",
    "y": "д",
    "Project": "Проект",
    "Detail — {label}": "Детали — {label}",
    "cell name (key under cells:)": "имя шаблона (ключ в cells:)",
    "Cell name:": "Имя шаблона:",
    "pick a file (or browse it in the Config tree)": "выберите файл (или найдите его в дереве Config)",
    "Bounding box (default)": "Ограничивающий прямоугольник (по умолчанию)",
    "Net aliases (blank = keep literal):": "Псевдонимы цепей (пусто = оставить как есть):",
    "Net": "Цепь",
    "Alias": "Псевдоним",
    "Rule net (null)": "Цепь правила (null)",
    "Net template role (bridging component — pick which aliased net is the template):":
    "Роль шаблона цепи (связующий компонент — укажите, какая из алиасированных цепей является шаблоном):",
    "Profile key:": "Ключ профиля:",
    "No placer file picked (pick one in the Config tree, optional)":
    "Файл расстановки не выбран (выберите в дереве Config, необязательно)",
    "Extract to file": "Извлечь в файл",
    "Alias {alias!r} used for both {a!r} and {b!r} — each alias needs a distinct net.":
    "Псевдоним {alias!r} используется и для {a!r}, и для {b!r} — у каждого псевдонима должна быть своя цепь.",
    "Origin: pick a via net.": "Начало: выберите цепь via.",
    "{count} field(s) could not be determined automatically: {details}":
    "Полей, которые не удалось определить автоматически: {count}: {details}",
    "Log": "Журнал",
    "Verbose": "Подробно",
    "Pending changes": "Ожидающие изменения",
    "Board (new)": "Плата (новое)",
    "pick a cell": "выберите шаблон",
    "Existing Cluster:": "Существующий Cluster:",
    "Nets (role -> literal net, priority over the cell's own net_template):":
    "Цепи (роль -> буквальная цепь, приоритет над собственным net_template шаблона):",
    "Override": "Переопределение",
    "resolved net name": "разрешённое имя цепи",
    "Anchor (ref/role)": "Якорь (ref/роль)",
    "Ref:": "Ref:",
    "Anchor cluster:": "Якорный кластер:",
    "Pick a Cells file in Files first.": "Сначала выберите файл шаблонов в Files.",
    "name (referenced by anchor_point elsewhere)": "имя (на него ссылаются anchor_point в другом месте)",
    "{label} is required.": "{label} обязателен.",
    "{name!r} already exists in {section}: somewhere in this include: graph":
    "{name!r} уже существует в {section}: где-то в графе include:",
    "Collapse all": "Свернуть все",
    "Delete selected": "Удалить выбранное",
    "This refdes' units disagree on Role/Cluster — edit carefully.":
    "Единицы этого refdes расходятся по Role/Cluster — правьте осторожно.",
    "Nothing selected.": "Ничего не выбрано.",
    "Cleared Role/Cluster on {count} component(s).": "Очищены Role/Cluster у компонентов: {count}.",
    "Cleared Role/Cluster on all {count} component(s).": "Очищены Role/Cluster у всех компонентов: {count}.",
    "Skipped {count} without Role/Cluster field: {refs}":
    "Пропущено без поля Role/Cluster: {count}: {refs}",
    "Clear failed: {error}": "Очистка не удалась: {error}",
    "Track registry path:": "Путь реестра треков:",
    "Log file:": "Файл журнала:",
    "Operation log dir:": "Каталог журнала операций:",
    "Place components": "Разместить компоненты",
    "Add schematic file": "Добавить файл схемы",
    "Spoke": "Спица",
    "Net is required.": "Требуется цепь.",
    "Failed to load file: {error}": "Не удалось загрузить файл: {error}",
    "name (used by --only, must be unique)": "имя (используется в --only, должно быть уникальным)",
    "Rows:": "Строки:",
    "Drill (mm):": "Сверло (мм):",
    "Diameter (mm):": "Диаметр (мм):",
    "Margin": "Поле",
    " (maybe you meant {suggestion!r}?)": " (возможно, вы имели в виду {suggestion!r}?)",
    "[error] --only: names not found:\n{lines}\nAvailable: {all}":
    "[ошибка] --only: имена не найдены:\n{lines}\nДоступны: {all}",
    "Rule {name!r}: no spokes match --cluster {paths}, rule dropped":
    "Правило {name!r}: ни одна спица не подошла под --cluster {paths}, правило отброшено",
    "[error] --cluster {paths}: matched nothing among rules' spokes, clone_placements, or thermal_via_arrays":
    "[ошибка] --cluster {paths}: не найдено совпадений среди спиц правил, clone_placements или thermal_via_arrays",
    "{label}: anchor {ref!r} failed to move earlier in this run — this item's placement is based on its OLD position":
    "{label}: якорь {ref!r} не удалось переместить ранее в этом прогоне — размещение этого элемента основано на его СТАРОЙ позиции",
    "⚠️ Some operations failed – check the log.": "⚠️ Некоторые операции завершились с ошибками – проверьте журнал.",
    "Profile {profile!r} from {profiles}: name={name}, output={output}":
    "Профиль {profile!r} из {profiles}: name={name}, output={output}",
    "❌ Failed to undo operation.": "❌ Не удалось откатить операцию.",
    "[error] profile {name!r} not found in {top_key!r} of file {path!r}. Available: {avail}":
    "[ошибка] профиль {name!r} не найден в {top_key!r} файла {path!r}. Доступны: {avail}",
    " — did you mean {suggestion!r}?": " — возможно, вы имели в виду {suggestion!r}?",
    "placer file wiring failed: {error}": "не удалось связать файл расстановки: {error}",
    "{what} {template!r} has a placeholder with no parameter":
    "у {what} {template!r} есть плейсхолдер без параметра",
    "Failed to read track registry {path}: {type}: {e} — treating registry as empty (all tracks will be created anew)":
    "Не удалось прочитать реестр треков {path}: {type}: {e} — считаю реестр пустым (все треки будут созданы заново)",
    "  {key}: registry has an entry (uuid {uuid}), but no such item is on the board — registry is out of sync (manually deleted, Undo, PCB reloaded from git, or previous run crashed between registry write and board commit); recreating as if the entry never existed":
    "  {key}: в реестре есть запись (uuid {uuid}), но такого элемента на плате НЕТ — реестр рассинхронизирован (удалён вручную, Undo, PCB перечитана из git, или прошлый прогон упал между записью в реестр и коммитом на плату); пересоздаю, как будто записи не было",
    "  {key}: already correctly placed (checked against live item {uuid}), skipped":
    "  {key}: уже стоит правильно (проверено по живому элементу {uuid}), пропуск",
    "  {key}: not processed in this run (--only filtered {anchor_id!r}), but it is still in the config — NOT pruned":
    "  {key}: не обработан в этом прогоне (--only отфильтровал {anchor_id!r}), но он есть в конфиге — НЕ удаляется",
    "a net can't be both \"always the enclosing Rule's own net\" and \"always resolved from this param\" — pick one per net":
    "цепь не может быть одновременно «всегда собственной цепью объемлющего правила» и «всегда разрешаться из этого параметра» — выберите одно для каждой цепи",
    "{count} selected objects — not footprint, via, or track, ignored (cell only supports these)":
    "Выделено объектов: {count} — не футпринт, не via и не трек, проигнорированы (шаблон поддерживает только их)",
    "Origin ({desc}): ({x:.3f}, {y:.3f}) mm": "Начало ({desc}): ({x:.3f}, {y:.3f}) мм",
    "Mixed selection: {back} on B.Cu, {front} on F.Cu; cell layer = {layer}, deviating components will have explicit layer":
    "Смешанное выделение: {back} на B.Cu, {front} на F.Cu; слой шаблона = {layer}, у выбивающихся будет явный layer",
    "  track: ({sx},{sy}) -> ({ex},{ey}), net={net}{layer}":
    "  трек: ({sx},{sy}) -> ({ex},{ey}), цепь={net}{layer}",
    "rule (net {net!r})": "правило (цепь {net!r})",
    "spoke (pad {pad}, net {net!r}): cell {cell!r} not found in cells":
    "спица (пад {pad}, цепь {net!r}): шаблон {cell!r} не найден в cells",
    "spoke (cell {cell!r}, net {net!r}): {anchor!r} has no pad {pad!r}":
    "спица (шаблон {cell!r}, цепь {net!r}): у {anchor!r} нет пада {pad!r}",
    "spoke references a non‑existent cell or pad": "спица ссылается на несуществующий шаблон или пад",
    "net {net!r}, role {role!r}{cluster}: need {needed}, found {available} (check the Role and Cluster fields in the schematic and the actual net connection)":
    "цепь {net!r}, роль {role!r}{cluster}: нужно {needed}, найдено {available} (проверьте поля Role и Cluster в схеме и реальное подключение к цепи)",
    "Role pool sufficiency checks passed": "Проверка достаточности пулов по ролям: всё сходится",
    "clone_placement {name!r}: cell {cell!r} not found in cells":
    "clone_placement {name!r}: шаблон {cell!r} не найден в cells",
    "cell {owner!r}: nested clone_placement {name!r}: cell {cell!r} not found in cells":
    "шаблон {owner!r}: вложенный clone_placement {name!r}: шаблон {cell!r} не найден в cells",
    "clone_placement references a non‑existent cell": "clone_placement ссылается на несуществующий шаблон",
    "{cycle} — a cell cannot contain itself, directly or through nesting":
    "{cycle} — шаблон не может содержать сам себя, напрямую или через вложенность",
    "role:{role}": "роль:{role}",
    "{this!r} and {other!r} both point to the same anchor with the same offset (cell/role={content!r}, anchor_point={point!r}, origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; likely a copy‑paste typo (if this is intentional, give them different xy)":
    "{this!r} и {other!r} оба указывают на один и тот же якорь с одинаковым смещением (cell/role={content!r}, anchor_point={point!r}, origin=({ox}, {oy}) мм) — реестр перепутает их via/треки; похоже на опечатку copy-paste (если это намеренно, задайте им разный xy)",
    "{this!r} and {other!r} both point to the same anchor with the same offset (cell/role={content!r}, anchor_ref={ref!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; likely a copy‑paste typo (if this is intentional, give them different xy)":
    "{this!r} и {other!r} оба указывают на один и тот же якорь с одинаковым смещением (cell/role={content!r}, anchor_ref={ref!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) мм) — реестр перепутает их via/треки; похоже на опечатку copy-paste (если это намеренно, задайте им разный xy)",
    "anchor_sheet/sheet_names check passed": "Проверка anchor_sheet/sheet_names: всё сходится",
    "found {count}: {names} — KiCad has only one selection at a time, so processing all at once is impossible":
    "найдено {count}: {names} — в KiCad активно только одно выделение, обработать все сразу нельзя",
    "got: {net!r} (offset_along_mm={along}, offset_across_mm={across})":
    "получено: {net!r} (offset_along_mm={along}, offset_across_mm={across})",
    "layer must be absolute: 'F.Cu' or 'B.Cu'": "layer должен быть абсолютным: 'F.Cu' или 'B.Cu'",
    "deprecated field 'side' in slot {role!r}": "устаревшее поле 'side' в слоте {role!r}",
    "on slot {role!r}": "у слота {role!r}",
    "role appears twice in cell {name!r}": "роль повторяется дважды в шаблоне {name!r}",
    "role {role!r} appears {count} times in components of this cell – roles inside a cell must be unique (see anchor_id/cell_name/role in the placement registry)":
    "роль {role!r} встречается {count} раз в components этого шаблона — роли внутри шаблона должны быть уникальны (см. anchor_id/cell_name/role в реестре расстановки)",
    "deprecated field 'reference_side' in cell {name!r}": "устаревшее поле 'reference_side' в шаблоне {name!r}",
    "renamed (see discussion v116): use layer: F.Cu or layer: B.Cu – absolute cell layer, as extracted":
    "переименовано (см. обсуждение v116): используйте layer: F.Cu или layer: B.Cu — абсолютный слой шаблона, как извлечено",
    "in cell {name!r}": "в шаблоне {name!r}",
    "name {dup!r} appears {count} times — nested clone_placement names must be unique within their cell (used to build the registry key for nested content)":
    "имя {dup!r} встречается {count} раз — имена вложенных clone_placement должны быть уникальны внутри своего шаблона (используются для построения ключа реестра вложенного содержимого)",
    "these are mutually exclusive ways to mark the cell's own local (0,0) — pick one":
    "это взаимоисключающие способы задать собственный локальный (0,0) шаблона — выберите один",
    "got: {xy!r}": "получено: {xy!r}",
    "anchor_pad without anchor_role in cell {name!r}": "anchor_pad без anchor_role в шаблоне {name!r}",
    "anchor_pad only narrows anchor_role — it is not an anchor by itself":
    "anchor_pad только сужает неоднозначность anchor_role — сам по себе якорем не является",
    "anchor_role must name one of this cell's own components: {roles}":
    "anchor_role должен называть один из собственных компонентов этого шаблона: {roles}",
    "every nested clone_placement must have a name — used to build the registry key for its content, write name: <string>":
    "каждый вложенный clone_placement должен иметь имя — оно используется для построения ключа реестра его содержимого, напишите name: <строка>",
    " (cell placements are closed-boundary — no anchor_ref/anchor_role/anchor_sheet/anchor_cluster/anchor_pad/by_selection/ignore_selection here, only xy: relative to the parent cell's own (0,0))":
    " (размещения шаблонов замкнуты по границе — здесь нет anchor_ref/anchor_role/anchor_sheet/anchor_cluster/anchor_pad/by_selection/ignore_selection, только xy: относительно собственного (0,0) родительского шаблона)",
    "cell and role together in nested clone_placement {name!r} of cell {cell_name!r}":
    "cell и role одновременно во вложенном clone_placement {name!r} шаблона {cell_name!r}",
    "these are mutually exclusive ways to define the content: either a reference to another cell (cell), or a single-component placement by role (role), not both":
    "это взаимоисключающие способы задать содержимое: либо ссылка на другой шаблон (cell), либо однокомпонентное размещение по роли (role), не оба сразу",
    "neither cell nor role set in nested clone_placement {name!r} of cell {cell_name!r}":
    "ни cell, ни role не заданы во вложенном clone_placement {name!r} шаблона {cell_name!r}",
    "xy must be a 2-element [x, y] list in nested clone_placement {name!r} of cell {cell_name!r}":
    "xy должен быть списком из 2 элементов [x, y] во вложенном clone_placement {name!r} шаблона {cell_name!r}",
    "in nested clone_placement {name!r} of cell {cell_name!r}":
    "во вложенном clone_placement {name!r} шаблона {cell_name!r}",
    "unknown fields in point {name!r}": "неизвестные поля в точке {name!r}",
    "anchor_ref/anchor_role, anchor_point, xy, and anchor_origin are mutually exclusive — pick exactly one way to define this point's base position":
    "anchor_ref/anchor_role, anchor_point, xy и anchor_origin взаимоисключающие — выберите ровно один способ задать базовую позицию этой точки",
    "mutually exclusive: either by refdes (anchor_ref) or by Role field (anchor_role), not both":
    "взаимоисключающие: либо по refdes (anchor_ref), либо по полю Role (anchor_role), не оба сразу",
    "anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself":
    "anchor_sheet только сужает неоднозначность anchor_role — сам по себе якорем не является",
    "anchor_pad without anchor_ref/anchor_role in point {name!r}":
    "anchor_pad без anchor_ref/anchor_role в точке {name!r}",
    "unknown fields in spoke (pad {pad!r}) of rule (net {net!r})":
    "неизвестные поля в спице (пад {pad!r}) правила (цепь {net!r})",
    "anchor_sheet without anchor_role in rule (net {net!r})":
    "anchor_sheet без anchor_role в правиле (цепь {net!r})",
    "anchor_point={point!r} names a points: entry that already carries its own anchor — mutually exclusive with anchor_ref/anchor_role":
    "anchor_point={point!r} называет запись points:, у которой уже есть собственный якорь — взаимоисключающе с anchor_ref/anchor_role",
    "rule (net {net!r}) without anchor_ref/anchor_role/anchor_point":
    "правило (цепь {net!r}) без anchor_ref/anchor_role/anchor_point",
    " (e.g. 'pad' won't work; use 'anchor_pad')": " (например, 'pad' не сработает; используйте 'anchor_pad')",
    "more than one of cell/role/cluster set in clone_placement {name!r}":
    "задано больше одного из cell/role/cluster в clone_placement {name!r}",
    "these are mutually exclusive ways to define the content: a ready-made cell (cell), a single-component placement by Role field (role), or a single-component placement by an existing Cluster field (cluster) — pick exactly one":
    "это взаимоисключающие способы задать содержимое: готовый шаблон (cell), однокомпонентное размещение по полю Role (role) или однокомпонентное размещение по существующему полю Cluster (cluster) — выберите ровно один",
    "need cell: <name from cells:> (ready-made cell), role: <ROLE> (single component found by its Role field), or cluster: <CLUSTER> (single component found by an already-assigned Cluster field)":
    "нужен cell: <имя из cells:> (готовый шаблон), role: <ROLE> (один компонент, найденный по полю Role) или cluster: <CLUSTER> (один компонент, найденный по уже присвоенному полю Cluster)",
    "anchor_sheet={sheet!r} is set but anchor_role is missing – anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself":
    "anchor_sheet={sheet!r} задан, но anchor_role отсутствует — anchor_sheet только сужает неоднозначность anchor_role, сам по себе якорем не является",
    "anchor_point together with anchor_ref/anchor_role in clone_placement {name!r}":
    "anchor_point вместе с anchor_ref/anchor_role в clone_placement {name!r}",
    "no anchor and no absolute coordinates in clone_placement {name!r}":
    "нет ни якоря, ни абсолютных координат в clone_placement {name!r}",
    "deprecated field 'side' in clone_placement {name!r}":
    "устаревшее поле 'side' в clone_placement {name!r}",
    "side is now set by an explicit pair: layer: F.Cu|B.Cu (where we place – fact) + mirror: true (how we place – operation, only meaningful when the layer changes relative to the cell)":
    "сторона теперь задаётся явной парой: layer: F.Cu|B.Cu (куда кладём — факт) + mirror: true (как кладём — операция, имеет смысл только при смене слоя относительно шаблона)",
    "renamed to xy: [x, y] — write xy: [{x}, {y}] instead":
    "переименовано в xy: [x, y] — пишите xy: [{x}, {y}]",
    "in clone_placement {name!r}": "в clone_placement {name!r}",
    "renamed for consistency: use anchor_ref": "переименовано для единообразия: используйте anchor_ref",
    "thermal_via_arrays entry without name": "запись thermal_via_arrays без name",
    "every thermal_via_arrays entry must have a name – used in --only (kicadstamp_cli.py) for isolated runs, and to tell entries apart; write name: <any understandable string>, e.g. name: fpga_thermal":
    "каждая запись thermal_via_arrays должна иметь name — используется в --only (kicadstamp_cli.py) для изолированных прогонов и чтобы различать записи; пишите name: <понятная строка>, например name: fpga_thermal",
    "unknown fields in thermal_via_arrays entry {name!r}":
    "неизвестные поля в записи thermal_via_arrays {name!r}",
    "each entry must be either a file path string, or a mapping {{path: <str>, enabled: <bool>}}":
    "каждая запись должна быть либо строкой с путём к файлу, либо отображением {{path: <str>, enabled: <bool>}}",
    "check for a stray/misplaced list (e.g. list items left without a wrapping 'clone_placements:'/'rules:' key)":
    "проверьте на лишний/не на месте список (например, элементы списка остались без оборачивающего ключа 'clone_placements:'/'rules:')",
    "{section!r} entries are a YAML list ('- name: ...'); {dict_sections} are mappings — check for a mixed-up section key":
    "записи {section!r} — это YAML-список ('- name: ...'); {dict_sections} — это отображения — проверьте, не перепутан ли ключ раздела",
    "include: {file!r} disabled, skipped (not opened)": "include: {file!r} отключён, пропущен (не открывался)",
    "include: file {file!r} not found": "include: файл {file!r} не найден",
    "deprecated field 'target_ref' at root of config": "устаревшее поле 'target_ref' в корне конфига",
    "at root of config": "в корне конфига",
    "generalized to a list 2026-08-02 (a second IC needing thermal vias — AD9707 — showed up): rename to 'thermal_via_arrays:' and wrap the single block in a YAML list ('- name: ...'), e.g.\nthermal_via_arrays:\n  - name: {name}\n    ...":
    "обобщено до списка 2026-08-02 (появилась вторая ИС, которой нужны термо-via — AD9707): переименуйте в 'thermal_via_arrays:' и оберните единственный блок в YAML-список ('- name: ...'), например\nthermal_via_arrays:\n  - name: {name}\n    ...",
    "renamed to cells_file:/cell_files: (the class became Cell, was SpokeTemplate), and those were themselves folded into include: on 2026-08-02 — see the 'cells_file'/'cell_files' error below for the current way to do this":
    "переименовано в cells_file:/cell_files: (класс стал Cell, был SpokeTemplate), а сами они 2026-08-02 были свёрнуты в include: — текущий способ см. в ошибке 'cells_file'/'cell_files' ниже",
    "mirror without layer change in clone_placement {name!r}":
    "mirror без смены слоя в clone_placement {name!r}",
    "layer changed without mirror in clone_placement {name!r}":
    "слой сменён без mirror в clone_placement {name!r}",
    "cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – flipped footprints on non‑flipped sites are nonsense; add mirror: true, or remove the layer override":
    "шаблон {cell!r} на {cell_layer}, слой размещения {place_layer} — перевёрнутые футпринты на неперевёрнутых местах бессмысленны; добавьте mirror: true или уберите переопределение слоя",
    " (did you mean {suggestion!r}?)": " (возможно, вы имели в виду {suggestion!r}?)",
    "{owner}: anchor_point {name!r} not found in points:{hint}":
    "{owner}: anchor_point {name!r} не найден в points:{hint}",
    "point {name!r} has a shift, is xy-literal, or chains to one that does — {owner} needs a live component to look up a specific pad from, a bare coordinate is not enough. Use this point with a clone_placement instead, or give it shift_x_mm=0/shift_y_mm=0 and no xy":
    "у точки {name!r} есть сдвиг, она литеральна по xy или ссылается на такую — {owner} нужен живой компонент, чтобы взять конкретный пад; голой координаты недостаточно. Используйте эту точку с clone_placement, либо задайте shift_x_mm=0/shift_y_mm=0 и уберите xy",
    "point {name!r}": "точка {name!r}",
    "clone_placement {name!r}": "clone_placement {name!r}",
    "thermal_via_arrays entry {name!r}": "запись thermal_via_arrays {name!r}",
    "No planned components or vias!": "Нет запланированных компонентов или via!",
    "Final state: angle={angle:.1f}° (was {orig:.1f}°), layer={layer} (was {orig_layer})":
    "Финальное состояние: угол={angle:.1f}° (было {orig:.1f}°), слой={layer} (было {orig_layer})",
    "via without net in cell {cell!r} ({name!r})": "via без цепи в шаблоне {cell!r} ({name!r})",
    "via at (along={along}, across={across}) has no net — ClonePlacement has no default rule net (unlike ManualSpoke), so every via in a cloned cell must have a net explicitly set":
    "via на (along={along}, across={across}) не имеет цепи — у ClonePlacement нет цепи правила по умолчанию (в отличие от ManualSpoke), поэтому каждая via в клонируемом шаблоне должна иметь явно заданную цепь",
    "track without net in cell {cell!r} ({name!r})": "трек без цепи в шаблоне {cell!r} ({name!r})",
    "Footprint {ref} not found": "Футпринт {ref} не найден",
    "Found footprint by uuid {uuid}": "Футпринт найден по uuid {uuid}",
    "Selected items (including groups expanded): {count}":
    "Выделено объектов (с учётом раскрытия групп): {count}",
    "  {ref}: bounding box unavailable, using fallback radius {radius}mm":
    "  {ref}: bounding box недоступен, используется запасной радиус {radius}мм",
    "cell {cell!r} has its own nested clone_placements — mirroring a composite cell as a whole isn't implemented yet, only leaf cells can be mirrored; remove mirror: true on whatever placement resolves to this cell":
    "у шаблона {cell!r} есть собственные вложенные clone_placements — зеркалирование составного шаблона целиком пока не реализовано, зеркалить можно только листовые шаблоны; уберите mirror: true на том размещении, которое разрешается в этот шаблон",
    "{field} is meant to be unique per instance — fix the tagging: {refs}":
    "{field} должен быть уникален на экземпляр — исправьте разметку: {refs}",
    "{ref}: no {field!r} field": "{ref}: нет поля {field!r}",
    "role {role!r} appears twice in selection: {ref1!r} and {ref2!r}":
    "роль {role!r} встречается дважды в выделении: {ref1!r} и {ref2!r}",
    "[{name}] role {role!r} -> {ref} (unique on whole board, no selection needed)":
    "[{name}] роль {role!r} -> {ref} (уникальна на всей плате, выделение не нужно)",
    "role {role!r} is in cell, not found in selection, and ambiguous on board ({count} candidates: {refs}){note} — set anchor_cluster, OR select the desired instance on the board before running":
    "роль {role!r} есть в шаблоне, не найдена в выделении и неоднозначна на плате ({count} кандидатов: {refs}){note} — задайте anchor_cluster, ИЛИ выделите нужный экземпляр на плате перед запуском",
    "[{name}] mapped by selection: {count} roles": "[{name}] сопоставлено по выделению: {count} ролей",
    "[{name}] role {role!r} -> {ref} (explicit refs)": "[{name}] роль {role!r} -> {ref} (явные refs)",
    "anchor_sheet {sheet!r}": "anchor_sheet {sheet!r}",
    "anchor_cluster {cluster!r}": "anchor_cluster {cluster!r}",
    " (already narrowed by {narrowed_by}, but not enough)":
    " (уже сужено по {narrowed_by}, но недостаточно)",
    " (neither anchor_sheet nor Cluster set — if these components are physically different instances, one of them would narrow to one)":
    " (не заданы ни anchor_sheet, ни Cluster — если эти компоненты физически разные экземпляры, один из них сузился бы до одного)",
    "Pool {net!r}/{role!r}{suffix}: {refs}": "Пул {net!r}/{role!r}{suffix}: {refs}",
    "\nNot enough components with role {role!r} on net {net!r} for spoke on pad {pad} — pool exhausted. Check the {field!r} field in the schematic: perhaps you forgot to mark another component, or it is not physically on this net.":
    "\nНе хватает компонентов с ролью {role!r} на цепи {net!r} для спицы на паде {pad} — пул исчерпан. Проверьте поле {field!r} в схеме: возможно, вы забыли пометить ещё один компонент, или он физически не на этой цепи.",
    "{label}: {ref} has no pad {pad!r}": "{label}: у {ref} нет пада {pad!r}",
    "check anchor_pad — pad numbers are strings as in KiCad ('1', '17', 'A3')":
    "проверьте anchor_pad — номера падов это строки, как в KiCad ('1', '17', 'A3')",
    "no such ref on the board (typo? component not yet in PCB?)":
    "такого ref нет на плате (опечатка? компонент ещё не перенесён в PCB?)",
    "{label}: anchor {anchor!r} not found on board": "{label}: якорь {anchor!r} не найден на плате",
    "this should have been caught at load time (config/loader.py) — please report":
    "это должно было быть поймано при загрузке (config/loader.py) — пожалуйста, сообщите",
    "set anchor_ref/anchor_role, anchor_point, xy, or anchor_origin — should have been caught at load time (config/loader.py)":
    "задайте anchor_ref/anchor_role, anchor_point, xy или anchor_origin — это должно было быть поймано при загрузке (config/loader.py)",
    "anchor_point {name!r} not found": "anchor_point {name!r} не найден",
    "Skipped {count} components already at target position":
    "Пропущено компонентов, уже находящихся в целевой позиции: {count}",
    "[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_sheet {sheet!r}":
    "[{label}] роль {role_str!r}: {count} кандидатов сужено до {narrowed} по anchor_sheet {sheet!r}",
    "[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_cluster {cluster!r}":
    "[{label}] роль {role_str!r}: {count} кандидатов сужено до {narrowed} по anchor_cluster {cluster!r}",
    "{label}: anchor_point {point!r} has no footprint — this should have been caught at load time (config/loader.py)":
    "{label}: у anchor_point {point!r} нет футпринта — это должно было быть поймано при загрузке (config/loader.py)",
    "Skipped {count} spoke/component vias already present on the board":
    "Пропущено via спиц/компонентов, уже присутствующих на плате: {count}",
    "Thermal pad: {error}": "Термопад: {error}",
    "Field": "Поле",
    "Schematic (current)": "Схема (текущее)",
    "Ensure fields...": "Добавить поля...",
    "Add / update": "Добавить / обновить",
    "Role (single component, no cell)": "Role (один компонент, без шаблона)",
    "Cluster (existing tag, single component)": "Cluster (существующий тег, один компонент)",
    "Source:": "Источник:",
    "Source": "Источник",
    "Params (placeholder -> literal net, for by-nets role resolution):":
    "Параметры (плейсхолдер -> буквальная цепь, для резолва роли по цепям):",
    "Nets": "Цепи",
    "Net overrides (resolved net -> final override):":
    "Переопределения цепей (разрешённая цепь -> итоговое переопределение):",
    "Resolved net": "Разрешённая цепь",
    "Net overrides": "Переопределения цепей",
    "Refs (role -> explicit ref, bypasses search entirely — last resort):":
    "Refs (роль -> явный ref, полностью минует поиск — последнее средство):",
    "e.g. C12": "например, C12",
    "Refs": "Refs",
    "Absolute XY": "Абсолютный XY",
    "e.g. U3 (refdes — mostly avoided in this project)":
    "например, U3 (refdes — в этом проекте в основном избегается)",
    "shift X mm (0)": "сдвиг X мм (0)",
    "shift Y mm (0)": "сдвиг Y мм (0)",
    "Shift X:": "Сдвиг X:",
    "Shift Y:": "Сдвиг Y:",
    "(cell default)": "(по умолчанию шаблона)",
    "Redraw": "Перерисовать",
    "literal net for {{{name}}}": "буквальная цепь для {{{name}}}",
    "Pick an existing Cluster first.": "Сначала выберите существующий Cluster.",
    "Cluster name is required.": "Требуется имя кластера.",
    "Shift X": "Сдвиг X",
    "Shift Y": "Сдвиг Y",
    "Anchor: set Ref or Role.": "Якорь: задайте Ref или Role.",
    "Anchor: Ref and Role are mutually exclusive — set one.":
    "Якорь: Ref и Role взаимоисключающие — задайте одно.",
    "Point: name is required.": "Точка: требуется имя.",
    "Pick a Placer file in Files first.": "Сначала выберите файл расстановки в Files.",
    "Pick a Cells file in Files first.": "Сначала выберите файл шаблонов в Files.",
    "Cell {cell!r} isn't reachable from the Placer file's include: — extract/save it and make sure include: is wired (see Extract).":
    "Шаблон {cell!r} недостижим из include: файла расстановки — извлеките/сохраните его и убедитесь, что include: подключён (см. Extract).",
    "Placement failed: {error}": "Размещение не удалось: {error}",
    "Placed, but tagging Cluster failed: {error}": "Размещено, но назначение Cluster не удалось: {error}",
    "Placed {name!r} ({count} component(s) tagged Cluster={name!r}).":
    "Размещено {name!r} (у компонентов: {count} назначен Cluster={name!r}).",
    "Placer: tag Cluster={name}": "Расстановщик: назначить Cluster={name}",
    "sheet name (narrows an ambiguous Role, optional)":
    "имя листа (сужает неоднозначную Role, необязательно)",
    "Sheet:": "Лист:",
    "Drill/place (drill/position files, optional for Gerbers)":
    "Drill/place (файлы сверловки/позиций, для Gerbers необязательно)",
    "Grid (visual only — Place > Set Grid Origin)": "Grid (только визуально — Place > Set Grid Origin)",
    "Kind:": "Вид:",
    "Resolve": "Разрешить",
    " (no footprint to highlight)": " (нет футпринта для подсветки)",
    "X={x:.3f}mm Y={y:.3f}mm{suffix}": "X={x:.3f}мм Y={y:.3f}мм{suffix}",
    "{name!r} not found in {section}: of {path}": "{name!r} не найден в {section}: файла {path}",
    "{name!r} already exists in {section}: of {path}": "{name!r} уже существует в {section}: файла {path}",
    "Filter (ref/role/cluster)...": "Фильтр (ref/роль/кластер)...",
    "regex": "regex",
    "Clear Role and Cluster on ALL {count} component(s) currently on the board? This is a single commit — undo-able in KiCad with Ctrl+Z.":
    "Очистить Role и Cluster у ВСЕХ компонентов ({count}), сейчас находящихся на плате? Это один коммит — в KiCad отменяется через Ctrl+Z.",
    " and {more} more": " и ещё {more}",
    "Schematic dir:": "Каталог схемы:",
    "Registry path:": "Путь реестра:",
    "Via keepout clearance (mm):": "Зазор keepout для via (мм):",
    "Via search step (mm):": "Шаг поиска via (мм):",
    "Via search max radius (mm):": "Максимальный радиус поиска via (мм):",
    "Via search directions:": "Направления поиска via:",
    "No project file open": "Проектный файл не открыт",
    "(relative to this YAML)": "(относительно этого YAML)",
    "Add...": "Добавить...",
    "Remove": "Удалить",
    "Schematic files:": "Файлы схем:",
    "Files": "Файлы",
    "Schematics": "Схемы",
    "Via": "Via",
    "Open or create a project (root) file first.": "Сначала откройте или создайте проектный (корневой) файл.",
    "{label} {text!r} is not an integer.": "{label}: {text!r} не является целым числом.",
    "Nothing to save — every field is still at its default.":
    "Нечего сохранять — все поля всё ещё со значениями по умолчанию.",
    "Saved root metadata to {path}": "Метаданные корневого файла сохранены в {path}",
    "optional — defaults to net for --only": "необязательно — по умолчанию цепь для --only",
    "Retired": "Снято с использования",
    "Skip": "Пропустить",
    "pad number on the rule's own anchor": "номер пада на собственном якоре правила",
    "Move up": "Выше",
    "Move down": "Ниже",
    "Add spoke": "Добавить спицу",
    "Redraw rule": "Перерисовать правило",
    "Redraw selected spoke": "Перерисовать выбранную спицу",
    "Pad is required.": "Требуется пад.",
    "Cell is required.": "Требуется шаблон.",
    "Spoke added — remember to Save the rule.": "Спица добавлена — не забудьте сохранить правило.",
    "Pick a spoke row first.": "Сначала выберите строку спицы.",
    "Spoke updated — remember to Save the rule.": "Спица обновлена — не забудьте сохранить правило.",
    "Spoke removed — remember to Save the rule.": "Спица удалена — не забудьте сохранить правило.",
    "Margin (mm):": "Поле (мм):",
    "Pattern:": "Паттерн:",
    "Rows": "Строки",
    "Cols": "Столбцы",
    "ClonePlacement {name!r}: skip=true, skipped this run (existing via/tracks stay protected)":
    "ClonePlacement {name!r}: skip=true, пропущен в этом прогоне (существующие via/треки остаются защищёнными)",
    "Rule {name!r}: skip=true, skipped this run (existing via/tracks stay protected)":
    "Правило {name!r}: skip=true, пропущено в этом прогоне (существующие via/треки остаются защищёнными)",
    "Rule {name!r}: spoke on pad {pad} skip=true, skipped this run":
    "Правило {name!r}: спица на паде {pad} skip=true, пропущена в этом прогоне",
    "Rule {name!r}: no non-skipped spokes left, skipped this run (existing via/tracks stay protected)":
    "Правило {name!r}: не осталось непропущенных спиц, пропущено в этом прогоне (существующие via/треки остаются защищёнными)",
    "thermal_via_arrays {name!r}: skip=true, skipped this run (existing vias stay protected)":
    "thermal_via_arrays {name!r}: skip=true, пропущен в этом прогоне (существующие via остаются защищёнными)",
    "--no-selection: current PCB editor selection will be ignored for this run":
    "--no-selection: текущее выделение в редакторе PCB будет игнорироваться в этом прогоне",
    "Resolving item execution order (dependency chain — see dependency_order.py)...":
    "Разрешение порядка выполнения элементов (цепочка зависимостей — см. dependency_order.py)...",
    "Execution order: {order}": "Порядок выполнения: {order}",
    "Order: {order}": "Порядок: {order}",
    "(items later in the dependency chain above are ALSO planned from the CURRENT board, not the post-move board of their prerequisite — a real apply may place them differently; rerun without --dry-run for the true chained result)":
    "(элементы далее по цепочке зависимостей выше также планируются от ТЕКУЩЕЙ платы, а не от платы после перемещения их предусловия — реальный apply может разместить их иначе; для истинного результата с учётом цепочки запустите без --dry-run)",
    "Could not read log_file from config {path}: {e}":
    "Не удалось прочитать log_file из конфига {path}: {e}",
    "{path} is not valid {kind}: {error}": "{path} не является корректным {kind}: {error}",
    "{section}: in {path} is not a list — refusing to touch it":
    "{section}: в {path} не является списком — не трогаю",
    "include: in {path} is not a list — refusing to touch it":
    "include: в {path} не является списком — не трогаю",
    "Cell written, but profile write failed: {error}":
    "Шаблон записан, но запись профиля не удалась: {error}",
    "{action} profile {key!r} in {path}": "{action} профиль {key!r} в {path}",
    "overwrote": "перезаписал",
    "wrote": "записал",
    "added {rel!r} to include: in {path}": "добавлен {rel!r} в include: в {path}",
    "skipped adding to include: — {path} has root-config-only key(s) {keys} that include: can't merge (move them to the Placer file itself, or point Placer at this same file)":
    "пропущено добавление в include: — в {path} есть ключи, доступные только корневому конфигу, {keys}, которые include: не может слить (перенесите их в сам файл расстановки или укажите Placer на этот же файл)",
    "net(s) {nets} are in both --rule-net and --param/--net-template":
    "цепь(и) {nets} указаны и в --rule-net, и в --param/--net-template",
    "could not determine automatically — {count} matching nets on pads ({nets}) — fill in manually or use --net-template-role {role}=<net>":
    "не удалось определить автоматически — на падах {count} подходящих цепей ({nets}) — заполните вручную или используйте --net-template-role {role}=<цепь>",
    "cycle among cell definitions": "цикл среди определений шаблонов",
    "every component slot needs role: <ROLE> – roles MUST be unique within a cell":
    "каждый слот компонента требует role: <ROLE> — роли ДОЛЖНЫ быть уникальны внутри шаблона",
    "must be 'grid' (Place > Set Grid Origin, visual only) or 'drill' (Place > Drill/Place Origin — the auxiliary axis drill/position files use, and Gerbers optionally via their own plot option)":
    "должно быть 'grid' (Place > Set Grid Origin, только визуально) или 'drill' (Place > Drill/Place Origin — используется файлами сверловки/позиций вспомогательной оси, а Gerbers — опционально через собственную опцию вывода)",
    "point {name!r} has no anchor": "у точки {name!r} нет якоря",
    "set exactly one of: anchor_ref/anchor_role (+ optional anchor_sheet/anchor_cluster/anchor_pad), anchor_point (chain to another point), xy (literal absolute coordinate), or anchor_origin (the board's own live grid/drill-place origin)":
    "задайте ровно одно из: anchor_ref/anchor_role (+ необязательно anchor_sheet/anchor_cluster/anchor_pad), anchor_point (ссылка на другую точку), xy (литеральная абсолютная координата) или anchor_origin (собственное живое начало grid/drill-place платы)",
    "point {name!r} has more than one anchor base": "у точки {name!r} больше одного базового якоря",
    "shift on a literal xy point {name!r}": "сдвиг на литеральной xy-точке {name!r}",
    "xy is already an absolute coordinate — edit it directly instead of combining it with shift_x_mm/shift_y_mm":
    "xy уже является абсолютной координатой — редактируйте её напрямую, а не комбинируйте с shift_x_mm/shift_y_mm",
    "xy must be a 2-element [x, y] list in point {name!r}":
    "xy должен быть списком из 2 элементов [x, y] в точке {name!r}",
    "cluster: finds its single target by an exact, already-unique Cluster field match — nets/params/by_selection (selection-vs-nets role resolution) have no meaning here, remove them":
    "cluster: находит единственную цель по точному, уже уникальному совпадению поля Cluster — nets/params/by_selection (резол роли по выделению-и-цепям) здесь не имеют смысла, уберите их",
    "anchor_point already resolves to a full position — anchor_pad has no meaning on top of it; set anchor_pad on the points: entry itself instead":
    "anchor_point уже разрешается в полную позицию — anchor_pad поверх неё не имеет смысла; задайте anchor_pad на самой записи points:",
    "include: invalid entry {entry!r} in {source!r}": "include: недопустимая запись {entry!r} в {source!r}",
    "{section!r} entries are a YAML mapping ('key: {{...}}'); {list_sections} are lists — check for a mixed-up section key":
    "записи {section!r} — это YAML-отображение ('key: {{...}}'); {list_sections} — это списки — проверьте, не перепутан ли ключ раздела",
    "include: {file!r} has top-level key(s) not supported inside an included file: {keys}":
    "include: {file!r} содержит ключи верхнего уровня, не поддерживаемые внутри включаемого файла: {keys}",
    "include: only merges {list_sections} (lists) and {dict_sections} (mappings) from an included file — anything else (e.g. layer:, schematic_dir:, registry_path:) has no defined way to merge across multiple included files and was previously silently dropped. Move {keys} to the root config file instead":
    "include: сливает из включаемого файла только {list_sections} (списки) и {dict_sections} (отображения) — для всего остального (например, layer:, schematic_dir:, registry_path:) нет определённого способа слияния между несколькими включаемыми файлами, и раньше это молча отбрасывалось. Перенесите {keys} в корневой файл конфига",
    "include: cycle detected — {file!r} is included from {source!r}, but is already being resolved higher up the same include chain":
    "include: обнаружен цикл — {file!r} включается из {source!r}, но уже разрешается выше по той же цепочке include:",
    "the include: chain loops back on itself — remove one of the include: entries that closes the loop":
    "цепочка include: замыкается сама на себя — уберите одну из записей include:, замыкающую цикл",
    "include: {file!r} already resolved elsewhere in the tree (diamond) — reusing, not re-merging":
    "include: {file!r} уже разрешён в другом месте дерева (ромб) — переиспользую, не сливаю повторно",
    "include: duplicate {section} key {key!r}": "include: дублирующийся ключ {section} {key!r}",
    "defined in {a!r} and again via include {b!r}": "определён в {a!r} и повторно через include {b!r}",
    "include: merged {file!r} into {source!r}": "include: {file!r} слит в {source!r}",
    "every thermal_via_arrays entry needs a unique name: — --only cannot tell same-named entries apart otherwise":
    "каждая запись thermal_via_arrays должна иметь уникальное name: — иначе --only не сможет различить записи с одинаковым именем",
    "deprecated fields 'templates_file'/'template_files'": "устаревшие поля 'templates_file'/'template_files'",
    "folded into include: 2026-08-02 (one mechanism for splitting ANY section across files — rules:/clone_placements:/thermal_via_arrays:/cells:/points:/extract_profiles:/clone_profiles: — instead of cells having its own separate, differently-shaped mechanism): list the external file(s) under include: instead, and add a 'cells:' key wrapping what used to be that file's whole content, e.g.\ninclude:\n  - templates/a.yaml\n  - templates/b.yaml\n(each of those files needs 'cells:' at its own top level now, same shape as an inline cells: block here)":
    "свёрнуто в include: 2026-08-02 (один механизм для разнесения ЛЮБОГО раздела по файлам — rules:/clone_placements:/thermal_via_arrays:/cells:/points:/extract_profiles:/clone_profiles: — вместо отдельного, иначе устроенного механизма для шаблонов): перечислите внешний(ие) файл(ы) под include:, а то, что было содержимым файла целиком, оберните ключом 'cells:', например\ninclude:\n  - templates/a.yaml\n  - templates/b.yaml\n(в каждом таком файле теперь должен быть свой 'cells:' на верхнем уровне — той же формы, что и встроенный блок cells: здесь)",
    "{owner}: anchor_point {name!r} has no footprint to anchor on":
    "{owner}: у anchor_point {name!r} нет футпринта, на который можно опереться",
    "Closing previous kipy connection failed (ignored)": "Не удалось закрыть предыдущее kipy-подключение (игнорируется)",
    "cannot set field {field!r}": "не удалось задать поле {field!r}",
    "{ref} has no field {field!r} on its footprint — add the field once in the schematic/footprint editor first, this tool never creates one from scratch":
    "у {ref} на футпринте нет поля {field!r} — сначала добавьте поле один раз в редакторе схемы/футпринтов, этот инструмент никогда не создаёт поле с нуля",
    "dependency cycle among rules/clone_placements": "цикл зависимостей среди rules/clone_placements",
    "{count} item(s) form a cycle through their anchors: {items}":
    "элементов: {count} образуют цикл через свои якоря: {items}",
    "break the cycle: at least one of these must anchor on something outside this set (a fixed, pre-existing component, or an absolute coordinate)":
    "разорвите цикл: хотя бы один из этих элементов должен опираться на что-то вне этого набора (фиксированный, уже существующий компонент или абсолютную координату)",
    "  [{name}] anchor: point {point!r}": "  [{name}] якорь: точка {point!r}",
    "mirror of a composite cell {cell!r} is not supported yet":
    "зеркалирование составного шаблона {cell!r} пока не поддерживается",
    "{name}: no component tagged {field}={cluster!r}": "{name}: нет компонента с тегом {field}={cluster!r}",
    "tag the target component's {field} field first (RoleClusterTreeDock or fieldstool), or check for a typo":
    "сначала назначьте поле {field} целевому компоненту (RoleClusterTreeDock или fieldstool), либо проверьте на опечатку",
    "{name}: {count} components tagged {field}={cluster!r}, expected exactly one":
    "{name}: компонентов с тегом {field}={cluster!r}: {count}, ожидался ровно один",
    "a point cannot (transitively) chain to itself via anchor_point":
    "точка не может (транзитивно) ссылаться сама на себя через anchor_point",
    "thermal_via_arrays entry {name!r}: retired, skipped":
    "запись thermal_via_arrays {name!r}: снята с использования, пропущена",

    # ---- GUI common / misc ----
    "KiCadStamp GUI": "KiCadStamp GUI",
    "fieldstool": "fieldstool",
    "No root sheet picked": "Корневой лист не выбран",
    "Pick root sheet...": "Выбрать корневой лист...",
    "Pick the project's root .kicad_sch": "Выберите корневой .kicad_sch проекта",
    "Rescan failed": "Пересканирование не удалось",
    "Could not set fields": "Не удалось задать поля",
    "Some fields were skipped": "Некоторые поля пропущены",
    "Confirm apply": "Подтвердить применение",
    "Pick a root sheet first.": "Сначала выберите корневой лист.",
    "Close KiCad first": "Сначала закройте KiCad",
    "Cannot apply": "Не удалось применить",
    "Every pending value already matches the schematic.":
    "Все ожидающие значения уже совпадают со схемой.",
    "Some files failed": "Некоторые файлы не записались",
    "Restored from .bak: {failed}": "Восстановлено из .bak: {failed}",
    "Applied": "Применено",
    "{count} file(s) written. Reopen KiCad to see the updated schematic — a running KiCad process does not hot-reload an externally-modified file.":
    "Записано файлов: {count}. Переоткройте KiCad, чтобы увидеть обновлённую схему — запущенный KiCad не перечитывает изменённый извне файл.",
    "KiCad appears to be running. Save your work and close KiCad, then click Ensure fields again — this tool never closes KiCad for you (see docs/fieldstool.md for why).":
    "Похоже, KiCad запущен. Сохраните работу и закройте KiCad, затем нажмите Ensure fields снова — этот инструмент сам никогда не закрывает KiCad (почему — см. docs/fieldstool.md).",
    "Every component already has {role!r} and {cluster!r}.":
    "У каждого компонента уже есть {role!r} и {cluster!r}.",
    "Fields added": "Поля добавлены",
    "{count} file(s) written. Reopen KiCad and run Update PCB from Schematic (F8) to sync the new fields down to the board.":
    "Записано файлов: {count}. Переоткройте KiCad и выполните Update PCB from Schematic (F8), чтобы синхронизировать новые поля на плату.",
    "KiCad processes": "Процессы KiCad",
    "Select a KiCad process to force-close. This never happens automatically — only what you pick and confirm here, e.g. a crashed/\"Not Responding\" session that's still blocking a fresh KiCad's connection.":
    "Выберите процесс KiCad для принудительного закрытия. Само это не происходит никогда — только то, что вы выбрали и подтвердили здесь, например зависшая/«Not Responding» сессия, которая всё ещё блокирует подключение нового KiCad.",
    "Close": "Закрыть",
    "No KiCad process found.": "Процессы KiCad не найдены.",
    "PID {pid}": "PID {pid}",
    "Pick a process from the list first.": "Сначала выберите процесс из списка.",
    "Force-close KiCad": "Принудительно закрыть KiCad",
    "Force-close KiCad process {pid}? Any unsaved changes in that process are lost — this cannot be undone.":
    "Принудительно закрыть процесс KiCad {pid}? Все несохранённые изменения в этом процессе будут потеряны — это необратимо.",
    "Could not close PID {pid}: {error}": "Не удалось закрыть PID {pid}: {error}",
    "Always on top": "Поверх всех окон",
    "KiCad processes...": "Процессы KiCad...",
    "No file picked (pick one in the Config tree)": "Файл не выбран (выберите в дереве Config)",
    "Layer:": "Слой:",
    "XY": "XY",
    "Role": "Role",
    "Anchor:": "Якорь:",
    "X mm": "X мм",
    "Y mm": "Y мм",
    "X:": "X:",
    "Y:": "Y:",
    "Save": "Сохранить",
    "offset along mm (0)": "смещение вдоль, мм (0)",
    "Offset along:": "Смещение вдоль:",
    "offset across mm (0)": "смещение поперёк, мм (0)",
    "Offset across:": "Смещение поперёк:",
    "angle deg (0)": "угол, град (0)",
    "Angle:": "Угол:",
    "Remove selected": "Удалить выбранное",
    "Components": "Компоненты",
    "optional — blank means the rule's own net": "необязательно — пусто означает собственную цепь правила",
    "Net:": "Цепь:",
    "drill mm (0.3)": "сверло, мм (0.3)",
    "Start along:": "Начало вдоль:",
    "Start across:": "Начало поперёк:",
    "End along:": "Конец вдоль:",
    "End across:": "Конец поперёк:",
    "width mm (0.25)": "ширина, мм (0.25)",
    "name (registry key, must be unique in this cell)": "имя (ключ реестра, должно быть уникально в этом шаблоне)",
    "Cell": "Шаблон",
    "Content:": "Содержимое:",
    "Cell:": "Шаблон:",
    "Nested cells": "Вложенные шаблоны",
    "{label}: {text!r} is not a number.": "{label}: {text!r} не является числом.",
    "Role is required.": "Требуется Role.",
    "Offset along": "Смещение вдоль",
    "Component updated — remember to Save the cell.": "Компонент обновлён — не забудьте сохранить шаблон.",
    "Component removed — remember to Save the cell.": "Компонент удалён — не забудьте сохранить шаблон.",
    "Via added — remember to Save the cell.": "Via добавлена — не забудьте сохранить шаблон.",
    "Pick a via row first.": "Сначала выберите строку via.",
    "Via updated — remember to Save the cell.": "Via обновлена — не забудьте сохранить шаблон.",
    "Start across": "Начало поперёк",
    "End along": "Конец вдоль",
    "End across": "Конец поперёк",
    "Width": "Ширина",
    "Track added — remember to Save the cell.": "Трек добавлен — не забудьте сохранить шаблон.",
    "Pick a track row first.": "Сначала выберите строку трека.",
    "Track updated — remember to Save the cell.": "Трек обновлён — не забудьте сохранить шаблон.",
    "Track removed — remember to Save the cell.": "Трек удалён — не забудьте сохранить шаблон.",
    "Nested cell: name is required.": "Вложенный шаблон: требуется имя.",
    "Pick a Cell first.": "Сначала выберите шаблон.",
    "Pick a Role first.": "Сначала выберите Role.",
    "X": "X",
    "Y": "Y",
    "Rotation": "Поворот",
    "Nested cell added — remember to Save the cell.": "Вложенный шаблон добавлен — не забудьте сохранить шаблон.",
    "Pick a nested-cell row first.": "Сначала выберите строку вложенного шаблона.",
    "Nested cell updated — remember to Save the cell.": "Вложенный шаблон обновлён — не забудьте сохранить шаблон.",
    "Nested cell removed — remember to Save the cell.": "Вложенный шаблон удалён — не забудьте сохранить шаблон.",
    "Name is required.": "Требуется имя.",
    "Anchor X": "Якорь X",
    "Anchor Y": "Якорь Y",
    "Anchor XY requires both X and Y.": "Для якоря XY нужны и X, и Y.",
    "Anchor: pick a Role first.": "Якорь: сначала выберите Role.",
    "{action} {name!r} in {path}": "{action} {name!r} в {path}",
    "Overwrote": "Перезаписал",
    "Wrote": "Записал",
    "Clone profiles": "Профили клонирования",
    "Config": "Конфиг",
    "Open Root file...": "Открыть корневой файл...",
    "New Root file...": "Новый корневой файл...",
    "New Root file": "Новый корневой файл",
    "Edit cell...": "Изменить шаблон...",
    "Rename...": "Переименовать...",
    "Export...": "Экспорт...",
    "Add point...": "Добавить точку...",
    "Add rule...": "Добавить правило...",
    "New name for {old!r}:": "Новое имя для {old!r}:",
    "Rename failed": "Переименование не удалось",
    "Renamed {old!r} to {new!r} — {count} file(s) updated: {files}":
    "Переименовано {old!r} в {new!r} — обновлено файлов: {count}: {files}",
    "{name!r} is still referenced by:\n{refs}\n\nAlso delete these referencing entries? Cancel leaves everything untouched.":
    "На {name!r} всё ещё ссылаются:\n{refs}\n\nУдалить также эти ссылающиеся записи? Отмена оставляет всё без изменений.",
    "Delete {name!r} from {section}: in {file}?": "Удалить {name!r} из {section}: в {file}?",
    "Deleted {name!r}. Backed up: {backups}.": "Удалено {name!r}. Резервная копия: {backups}.",
    "Also removed references from: {files}.": "Также убраны ссылки из: {files}.",
    "Deleted": "Удалено",
    "Export to...": "Экспорт в...",
    "Export": "Экспорт",
    "{name} already has content — merge the exported entries into it, or overwrite the whole file?":
    "В {name} уже есть содержимое — слить экспортируемые записи в него или перезаписать весь файл?",
    "Merge": "Слить",
    "Overwrite": "Перезаписать",
    "Export failed": "Экспорт не удался",
    "Exported": "Экспортировано",
    "Exported {count} entr{suffix} to {name}": "Экспортировано записей: {count} в {name}",
    "Add included file": "Добавить включаемый файл",
    "Cannot include": "Не удалось включить",
    "{name} has root-config-only key(s) {keys} that include: can't merge — move them out, or point Root at this file directly instead.":
    "В {name} есть ключи, доступные только корневому конфигу, {keys}, которые include: не может слить — вынесите их или укажите Root прямо на этот файл.",
    "Remove file": "Удалить файл",
    "Remove {name!r} from {parent!r}'s include:? The file itself is not deleted — this can be undone later by adding it again.":
    "Удалить {name!r} из include: файла {parent!r}? Сам файл не удаляется — позже это можно отменить, добавив его снова.",
    "Detail": "Детали",
    "Extract": "Извлечение",
    "Placer": "Расстановщик",
    "Detail — {label}: {name}": "Детали — {label}: {name}",
    "Via net": "Цепь via",
    "Origin:": "Начало:",
    "Origin": "Начало",
    "Cells:": "Шаблоны:",
    "Profiles:": "Профили:",
    "Existing": "Существующие",
    "Also save as extract_profile": "Также сохранить как extract_profile",
    "profile key (defaults to cell name)": "ключ профиля (по умолчанию имя шаблона)",
    "Key this extraction is saved under in extract_profiles: (only used if 'Also save as extract_profile' is checked) — separate from Cell name, the two can differ. Saves a replayable recipe (net aliases, origin, ...) you can reuse later via 'kicadstamp_cli.py extract --profile <key>' or by clicking it in Existing -> Profiles, instead of retyping the alias mapping by hand. Defaults to the Cell name if left blank.":
    "Ключ, под которым это извлечение сохраняется в extract_profiles: (используется только если отмечено 'Also save as extract_profile') — отдельно от имени шаблона, они могут различаться. Сохраняет воспроизводимый рецепт (псевдонимы цепей, начало, ...), который можно переиспользовать через 'kicadstamp_cli.py extract --profile <ключ>' или кликом в Existing -> Profiles, вместо ручного ввода карты псевдонимов. Пусто = по умолчанию имя шаблона.",
    "{fp} component(s), {other} via/track(s) selected": "Выделено: {fp} компонент(ов), via/треков: {other}",
    "{fp} component(s) selected": "Выделено компонентов: {fp}",
    "Selection spans multiple Clusters: {clusters}": "Выделение охватывает несколько кластеров: {clusters}",
    "alias, e.g. PWR_IN": "псевдоним, например, PWR_IN",
    "Write this via/track net as null instead of a literal — at apply time a ManualSpoke-placed cell inherits the enclosing Rule's own net for it, so the cell can be reused across Rules on different nets.":
    "Записать цепь этой via/трека как null вместо литерала — при применении шаблон, размещённый через ManualSpoke, наследует для неё собственную цепь объемлющего правила, поэтому шаблон можно переиспользовать между разными правилами на разных цепях.",
    "Cell name is required.": "Требуется имя шаблона.",
    "'Also save as extract_profile' is checked, but no profile file is picked.":
    "Отмечено 'Also save as extract_profile', но файл профиля не выбран.",
    "Not connected.": "Нет подключения.",
    "Find...": "Найти...",
    "Prev": "Назад",
    "Next": "Вперёд",
    "Add / update": "Добавить / обновить",
    "Shift X:": "Сдвиг X:",
    "Shift Y:": "Сдвиг Y:",
    "Placer: tag Cluster={name}": "Расстановщик: назначить Cluster={name}",
    "Cols": "Столбцы",
    "Pattern:": "Паттерн:",
    "Margin (mm):": "Поле (мм):",
    "Rows": "Строки",
    "Row(s):": "Строки:",
    "Fill": "Заполнение",
    "Anchor X": "Якорь X",
    "Anchor Y": "Якорь Y",
    "Absolute XY": "Абсолютный XY",
    "Anchor (ref/role)": "Якорь (ref/роль)",
    "Ref:": "Ref:",
    "Anchor cluster:": "Якорный кластер:",
    "e.g. U3 (refdes — mostly avoided in this project)": "например, U3 (refdes — в этом проекте в основном избегается)",
    "Shift X": "Сдвиг X",
    "Shift Y": "Сдвиг Y",
    "Rotation (deg):": "Поворот (град):",
    "Cell:": "Шаблон:",
    "Nested cells": "Вложенные шаблоны",
    "Pick a Cell first.": "Сначала выберите шаблон.",
    "Pick a Role first.": "Сначала выберите Role.",
    "Sheet:": "Лист:",
    "Kind:": "Вид:",
    "Resolve": "Разрешить",
    "Move up": "Выше",
    "Move down": "Ниже",
    "Add spoke": "Добавить спицу",
    "Redraw rule": "Перерисовать правило",
    "Redraw selected spoke": "Перерисовать выбранную спицу",
    "Pad is required.": "Требуется пад.",
    "Cell is required.": "Требуется шаблон.",
    "Spoke": "Спица",
    "Retired": "Снято с использования",
    "Skip": "Пропустить",
    "Save": "Сохранить",
    "Add": "Добавить",
    "Drill": "Сверло",
    "Width": "Ширина",
    "Name is required.": "Требуется имя.",
    "Rotation": "Поворот",
    "Anchor: pick a Role first.": "Якорь: сначала выберите Role.",
    "Overwrote": "Перезаписал",
    "Wrote": "Записал",
    "Export": "Экспорт",
    "Merge": "Слить",
    "Overwrite": "Перезаписать",
    "Deleted": "Удалено",
    "Remove file": "Удалить файл",
    "Detail": "Детали",
    "Extract": "Извлечение",
    "Placer": "Расстановщик",
    "Project": "Проект",
    "Log": "Журнал",
    "Field": "Поле",
    "Schematic (current)": "Схема (текущее)",
    "Board (new)": "Плата (новое)",
    "Pending changes": "Ожидающие изменения",
    "Ensure fields...": "Добавить поля...",
    "Rescan": "Пересканировать",
    "Rules": "Правила",
    "Clone placements": "Клонируемые расстановки",
    "Cells": "Шаблоны",
    "Points": "Точки",
    "Config": "Конфиг",
    "Files": "Файлы",
    "Schematics": "Схемы",
    "Via": "Via",
    "Collapse all": "Свернуть все",
    "Delete selected": "Удалить выбранное",
    "regex": "regex",
    "Refresh": "Обновить",
    "Quit": "Выйти",
    "KiCadStamp": "KiCadStamp",
    "Always on top": "Поверх всех окон",
    "Recent...": "Недавние...",
    "New Root file...": "Новый корневой файл...",
    "Open Root file...": "Открыть корневой файл...",
    "Add cell...": "Добавить шаблон...",
    "Add point...": "Добавить точку...",
    "Add rule...": "Добавить правило...",
    "Add included file...": "Добавить включаемый файл...",
    "Rename...": "Переименовать...",
    "Delete...": "Удалить...",
    "Export...": "Экспорт...",
    "Edit cell...": "Изменить шаблон...",
    "New Root file": "Новый корневой файл",
    "Cannot include": "Не удалось включить",
    "Add included file": "Добавить включаемый файл",
    "Remove this file": "Удалить этот файл",
    "Remove file": "Удалить файл",
    "Export to...": "Экспорт в...",
    "Export failed": "Экспорт не удался",
    "Exported": "Экспортировано",
    "Rename failed": "Переименование не удалось",
    "Saved root metadata to {path}": "Метаданные корневого файла сохранены в {path}",
    "Add schematic file": "Добавить файл схемы",
    "Add...": "Добавить...",
    "Remove": "Удалить",
    "Schematic dir:": "Каталог схемы:",
    "Registry path:": "Путь реестра:",
    "Track registry path:": "Путь реестра треков:",
    "Log file:": "Файл журнала:",
    "Operation log dir:": "Каталог журнала операций:",
    "Place components": "Разместить компоненты",
    "Schematic files:": "Файлы схем:",
    "No project file open": "Проектный файл не открыт",
    "Via keepout clearance (mm):": "Зазор keepout для via (мм):",
    "Via search step (mm):": "Шаг поиска via (мм):",
    "Via search max radius (mm):": "Максимальный радиус поиска via (мм):",
    "Via search directions:": "Направления поиска via:",
    "Open or create a project (root) file first.": "Сначала откройте или создайте проектный (корневой) файл.",
    "Nothing to save — every field is still at its default.": "Нечего сохранять — все поля всё ещё со значениями по умолчанию.",
    "Overwrote": "Перезаписал",
    "Wrote": "Записал",
    "Find...": "Найти...",
    "Prev": "Назад",
    "Next": "Вперёд",
    "Log": "Журнал",
    "Verbose": "Подробно",
    "No root sheet picked": "Корневой лист не выбран",
    "Pick root sheet...": "Выбрать корневой лист...",
    "Pick the project's root .kicad_sch": "Выберите корневой .kicad_sch проекта",
    "Rescan failed": "Пересканирование не удалось",
    "Could not set fields": "Не удалось задать поля",
    "Some fields were skipped": "Некоторые поля пропущены",
    "Confirm apply": "Подтвердить применение",
    "Pick a root sheet first.": "Сначала выберите корневой лист.",
    "Close KiCad first": "Сначала закройте KiCad",
    "Cannot apply": "Не удалось применить",
    "Every pending value already matches the schematic.": "Все ожидающие значения уже совпадают со схемой.",
    "Some files failed": "Некоторые файлы не записались",
    "Restored from .bak: {failed}": "Восстановлено из .bak: {failed}",
    "Applied": "Применено",
    "KiCad processes": "Процессы KiCad",
    "Select a KiCad process to force-close. This never happens automatically — only what you pick and confirm here, e.g. a crashed/\"Not Responding\" session that's still blocking a fresh KiCad's connection.":
    "Выберите процесс KiCad для принудительного закрытия. Само это не происходит никогда — только то, что вы выбрали и подтвердили здесь, например зависшая/«Not Responding» сессия, которая всё ещё блокирует подключение нового KiCad.",
    "Close": "Закрыть",
    "No KiCad process found.": "Процессы KiCad не найдены.",
    "PID {pid}": "PID {pid}",
    "Pick a process from the list first.": "Сначала выберите процесс из списка.",
    "Force-close KiCad": "Принудительно закрыть KiCad",
    "Force-close KiCad process {pid}? Any unsaved changes in that process are lost — this cannot be undone.":
    "Принудительно закрыть процесс KiCad {pid}? Все несохранённые изменения в этом процессе будут потеряны — это необратимо.",
    "Could not close PID {pid}: {error}": "Не удалось закрыть PID {pid}: {error}",
    "KiCad processes...": "Процессы KiCad...",
    "KiCadStamp GUI": "KiCadStamp GUI",
    "fieldstool": "fieldstool",
    "No root file open": "Корневой файл не открыт",
    "X mm": "X мм",
    "Y mm": "Y мм",
    "X:": "X:",
    "Y:": "Y:",
    "Anchor:": "Якорь:",
    "Role:": "Роль:",
    "Cluster:": "Кластер:",
    "Layer:": "Слой:",
    "XY": "XY",
    "Role": "Role",
    "Cell": "Шаблон",
    "Components": "Компоненты",
    "Net:": "Цепь:",
    "Drill:": "Сверло:",
    "Diameter:": "Диаметр:",
    "Width:": "Ширина:",
    "Angle:": "Угол:",
    "Offset along:": "Смещение вдоль:",
    "Offset across:": "Смещение поперёк:",
    "Start along:": "Начало вдоль:",
    "Start across:": "Начало поперёк:",
    "End along:": "Конец вдоль:",
    "End across:": "Конец поперёк:",
    "Rows:": "Строки:",
    "Drill (mm):": "Сверло (мм):",
    "Diameter (mm):": "Диаметр (мм):",
    "Margin (mm):": "Поле (мм):",
    "Pattern:": "Паттерн:",
    "Cell name:": "Имя шаблона:",
    "Profile key:": "Ключ профиля:",
    "Origin:": "Начало:",
    "Origin": "Начало",
    "Via net": "Цепь via",
    "Sheet:": "Лист:",
    "Kind:": "Вид:",
    "Source:": "Источник:",
    "Source": "Источник",
    "Nets": "Цепи",
    "Refs": "Refs",
    "Override": "Переопределение",
    "Resolved net": "Разрешённая цепь",
    "Net overrides": "Переопределения цепей",
    "Shift X": "Сдвиг X",
    "Shift Y": "Сдвиг Y",
    "Rotation": "Поворот",
    "Redraw": "Перерисовать",
    "Redraw rule": "Перерисовать правило",
    "Redraw selected spoke": "Перерисовать выбранную спицу",
    "Add spoke": "Добавить спицу",
    "Move up": "Выше",
    "Move down": "Ниже",
    "Skip": "Пропустить",
    "Retired": "Снято с использования",
    "Rows": "Строки",
    "Cols": "Столбцы",
    "Existing": "Существующие",
    "Profiles:": "Профили:",
    "Cells:": "Шаблоны:",
    "Nested cells": "Вложенные шаблоны",
    "Nested cell: name is required.": "Вложенный шаблон: требуется имя.",
    "Nested cell added — remember to Save the cell.": "Вложенный шаблон добавлен — не забудьте сохранить шаблон.",
    "Nested cell updated — remember to Save the cell.": "Вложенный шаблон обновлён — не забудьте сохранить шаблон.",
    "Nested cell removed — remember to Save the cell.": "Вложенный шаблон удалён — не забудьте сохранить шаблон.",
    "Pick a nested-cell row first.": "Сначала выберите строку вложенного шаблона.",
    "Name is required.": "Требуется имя.",
    "Anchor X": "Якорь X",
    "Anchor Y": "Якорь Y",
    "Anchor XY requires both X and Y.": "Для якоря XY нужны и X, и Y.",
    "Component added — remember to Save the cell.": "Компонент добавлен — не забудьте сохранить шаблон.",
    "Component updated — remember to Save the cell.": "Компонент обновлён — не забудьте сохранить шаблон.",
    "Component removed — remember to Save the cell.": "Компонент удалён — не забудьте сохранить шаблон.",
    "Via added — remember to Save the cell.": "Via добавлена — не забудьте сохранить шаблон.",
    "Via updated — remember to Save the cell.": "Via обновлена — не забудьте сохранить шаблон.",
    "Via removed — remember to Save the cell.": "Via удалена — не забудьте сохранить шаблон.",
    "Pick a via row first.": "Сначала выберите строку via.",
    "Track added — remember to Save the cell.": "Трек добавлен — не забудьте сохранить шаблон.",
    "Track updated — remember to Save the cell.": "Трек обновлён — не забудьте сохранить шаблон.",
    "Track removed — remember to Save the cell.": "Трек удалён — не забудьте сохранить шаблон.",
    "Pick a track row first.": "Сначала выберите строку трека.",
    "Offset along": "Смещение вдоль",
    "Offset across": "Смещение поперёк",
    "Start across": "Начало поперёк",
    "End along": "Конец вдоль",
    "End across": "Конец поперёк",
    "Width": "Ширина",
    "Drill": "Сверло",
    "Rotation (deg):": "Поворот (град):",
    "Add / update": "Добавить / обновить",
    "Role (single component, no cell)": "Role (один компонент, без шаблона)",
    "Cluster (existing tag, single component)": "Cluster (существующий тег, один компонент)",
    "Params (placeholder -> literal net, for by-nets role resolution):": "Параметры (плейсхолдер -> буквальная цепь, для резолва роли по цепям):",
    "Net aliases (blank = keep literal):": "Псевдонимы цепей (пусто = оставить как есть):",
    "Net overrides (resolved net -> final override):": "Переопределения цепей (разрешённая цепь -> итоговое переопределение):",
    "Refs (role -> explicit ref, bypasses search entirely — last resort):": "Refs (роль -> явный ref, полностью минует поиск — последнее средство):",
    "e.g. C12": "например, C12",
    "e.g. U3 (refdes — mostly avoided in this project)": "например, U3 (refdes — в этом проекте в основном избегается)",
    "shift X mm (0)": "сдвиг X мм (0)",
    "shift Y mm (0)": "сдвиг Y мм (0)",
    "Shift X:": "Сдвиг X:",
    "Shift Y:": "Сдвиг Y:",
    "(cell default)": "(по умолчанию шаблона)",
    "literal net for {{{name}}}": "буквальная цепь для {{{name}}}",
    "Pick an existing Cluster first.": "Сначала выберите существующий Cluster.",
    "Cluster name is required.": "Требуется имя кластера.",
    "Anchor: set Ref or Role.": "Якорь: задайте Ref или Role.",
    "Anchor: Ref and Role are mutually exclusive — set one.": "Якорь: Ref и Role взаимоисключающие — задайте одно.",
    "Point: name is required.": "Точка: требуется имя.",
    "Pick a Placer file in Files first.": "Сначала выберите файл расстановки в Files.",
    "Pick a Cells file in Files first.": "Сначала выберите файл шаблонов в Files.",
    "Placement failed: {error}": "Размещение не удалось: {error}",
    "Placed, but tagging Cluster failed: {error}": "Размещено, но назначение Cluster не удалось: {error}",
    "Placed {name!r} ({count} component(s) tagged Cluster={name!r}).": "Размещено {name!r} (у компонентов: {count} назначен Cluster={name!r}).",
    "Placer: tag Cluster={name}": "Расстановщик: назначить Cluster={name}",
    "sheet name (narrows an ambiguous Role, optional)": "имя листа (сужает неоднозначную Role, необязательно)",
    "Drill/place (drill/position files, optional for Gerbers)": "Drill/place (файлы сверловки/позиций, для Gerbers необязательно)",
    "Grid (visual only — Place > Set Grid Origin)": "Grid (только визуально — Place > Set Grid Origin)",
    "Resolve": "Разрешить",
    " (no footprint to highlight)": " (нет футпринта для подсветки)",
    "X={x:.3f}mm Y={y:.3f}mm{suffix}": "X={x:.3f}мм Y={y:.3f}мм{suffix}",
    "{name!r} not found in {section}: of {path}": "{name!r} не найден в {section}: файла {path}",
    "{name!r} already exists in {section}: of {path}": "{name!r} уже существует в {section}: файла {path}",
    "Filter (ref/role/cluster)...": "Фильтр (ref/роль/кластер)...",
    "Clear Role and Cluster on ALL {count} component(s) currently on the board? This is a single commit — undo-able in KiCad with Ctrl+Z.":
    "Очистить Role и Cluster у ВСЕХ компонентов ({count}), сейчас находящихся на плате? Это один коммит — в KiCad отменяется через Ctrl+Z.",
    " and {more} more": " и ещё {more}",
    "Skipped {count} without Role/Cluster field: {refs}": "Пропущено без поля Role/Cluster: {count}: {refs}",
    "Clear failed: {error}": "Очистка не удалась: {error}",
    "Nothing selected.": "Ничего не выбрано.",
    "This refdes' units disagree on Role/Cluster — edit carefully.": "Единицы этого refdes расходятся по Role/Cluster — правьте осторожно.",
    "Not connected: {error}": "Нет подключения: {error}",
    "Not connected": "Нет подключения",
    "Not yet applied to schematic: {refs}": "Ещё не применено к схеме: {refs}",
    "Set Role and/or Cluster first.": "Сначала задайте Role и/или Cluster.",
    "Connect to KiCad first.": "Сначала подключитесь к KiCad.",
    "About to write {count} change(s):": "Будет записано изменений: {count}:",
    "Cannot ensure fields": "Не удалось добавить поля",
    "Fields added": "Поля добавлены",
    "Every component already has {role!r} and {cluster!r}.": "У каждого компонента уже есть {role!r} и {cluster!r}.",
    "Cannot apply": "Не удалось применить",
    "Some files failed": "Некоторые файлы не записались",
    "Restored from .bak: {failed}": "Восстановлено из .bak: {failed}",
    "Applied": "Применено",
    "Confirm apply": "Подтвердить применение",
    "Pick a root sheet first.": "Сначала выберите корневой лист.",
    "Close KiCad first": "Сначала закройте KiCad",
    "No root sheet picked": "Корневой лист не выбран",
    "Pick root sheet...": "Выбрать корневой лист...",
    "Pick the project's root .kicad_sch": "Выберите корневой .kicad_sch проекта",
    "Rescan failed": "Пересканирование не удалось",
    "Could not set fields": "Не удалось задать поля",
    "Some fields were skipped": "Некоторые поля пропущены",
    "KiCad appears to be running. Save your work and close KiCad, then click Apply again — this tool never closes KiCad for you (see docs/fieldstool.md for why).":
    "Похоже, KiCad запущен. Сохраните работу и закройте KiCad, затем нажмите Apply снова — этот инструмент сам никогда не закрывает KiCad (почему — см. docs/fieldstool.md).",
    "KiCad appears to be running. Save your work and close KiCad, then click Ensure fields again — this tool never closes KiCad for you (see docs/fieldstool.md for why).":
    "Похоже, KiCad запущен. Сохраните работу и закройте KiCad, затем нажмите Ensure fields снова — этот инструмент сам никогда не закрывает KiCad (почему — см. docs/fieldstool.md).",
    "{count} file(s) written. Reopen KiCad to see the updated schematic — a running KiCad process does not hot-reload an externally-modified file.":
    "Записано файлов: {count}. Переоткройте KiCad, чтобы увидеть обновлённую схему — запущенный KiCad не перечитывает изменённый извне файл.",
    "{count} file(s) written. Reopen KiCad and run Update PCB from Schematic (F8) to sync the new fields down to the board.":
    "Записано файлов: {count}. Переоткройте KiCad и выполните Update PCB from Schematic (F8), чтобы синхронизировать новые поля на плату.",
    "These targets have no such field on their footprint yet — nothing was written for them (use Ensure fields... below, or add the field by hand, then Update PCB from Schematic):\n{refs}":
    "У этих целей на футпринте ещё нет такого поля — для них ничего не записано (используйте Ensure fields... ниже, либо добавьте поле вручную и сделайте Update PCB from Schematic):\n{refs}",
    "Field": "Поле",
    "Schematic (current)": "Схема (текущее)",
    "Board (new)": "Плата (новое)",
    "Pending changes": "Ожидающие изменения",
    "Ensure fields...": "Добавить поля...",
    "About to write {count} change(s):": "Будет записано изменений: {count}:",
    "Cannot ensure fields": "Не удалось добавить поля",
    "KiCadStamp GUI": "KiCadStamp GUI",
    "fieldstool": "fieldstool",
    "Always on top": "Поверх всех окон",
    "KiCad processes...": "Процессы KiCad...",
    "KiCad processes": "Процессы KiCad",
    "Could not close PID {pid}: {error}": "Не удалось закрыть PID {pid}: {error}",
    "Close": "Закрыть",
    "No KiCad process found.": "Процессы KiCad не найдены.",
    "PID {pid}": "PID {pid}",
    "Pick a process from the list first.": "Сначала выберите процесс из списка.",
    "Force-close KiCad": "Принудительно закрыть KiCad",
    "Force-close KiCad process {pid}? Any unsaved changes in that process are lost — this cannot be undone.":
    "Принудительно закрыть процесс KiCad {pid}? Все несохранённые изменения в этом процессе будут потеряны — это необратимо.",
    "Select a KiCad process to force-close. This never happens automatically — only what you pick and confirm here, e.g. a crashed/\"Not Responding\" session that's still blocking a fresh KiCad's connection.":
    "Выберите процесс KiCad для принудительного закрытия. Само это не происходит никогда — только то, что вы выбрали и подтвердили здесь, например зависшая/«Not Responding» сессия, которая всё ещё блокирует подключение нового KiCad.",
    "Refresh": "Обновить",
    "Quit": "Выйти",
    "KiCadStamp": "KiCadStamp",
    "X": "X",
    "Y": "Y",
    "Save": "Сохранить",
    "Add": "Добавить",
    "Remove selected": "Удалить выбранное",
    "Layer:": "Слой:",
    "XY": "XY",
    "Role": "Role",
    "Cell": "Шаблон",
    "Components": "Компоненты",
    "Net:": "Цепь:",
    "Anchor:": "Якорь:",
    "X mm": "X мм",
    "Y mm": "Y мм",
    "X:": "X:",
    "Y:": "Y:",
    "Name is required.": "Требуется имя.",
    "Role is required.": "Требуется Role.",
    "Anchor X": "Якорь X",
    "Anchor Y": "Якорь Y",
    "Anchor XY requires both X and Y.": "Для якоря XY нужны и X, и Y.",
    "Anchor: pick a Role first.": "Якорь: сначала выберите Role.",
    "Pick a file in the Config tree first.": "Сначала выберите файл в дереве Config.",
    "Pick a Cell first.": "Сначала выберите шаблон.",
    "Pick a Role first.": "Сначала выберите Role.",
    "Net is required.": "Требуется цепь.",
    "Pad is required.": "Требуется пад.",
    "Cell is required.": "Требуется шаблон.",
    "Cluster name is required.": "Требуется имя кластера.",
    "Point: name is required.": "Точка: требуется имя.",
    "Cell name is required.": "Требуется имя шаблона.",
    "Nested cell: name is required.": "Вложенный шаблон: требуется имя.",
    "Role is required.": "Требуется Role.",
    "Pick a via row first.": "Сначала выберите строку via.",
    "Pick a track row first.": "Сначала выберите строку трека.",
    "Pick a nested-cell row first.": "Сначала выберите строку вложенного шаблона.",
    "Pick a spoke row first.": "Сначала выберите строку спицы.",
    "Pick a file in the Config tree first.": "Сначала выберите файл в дереве Config.",
    "Pick a Cells file in Files first.": "Сначала выберите файл шаблонов в Files.",
    "Pick a Placer file in Files first.": "Сначала выберите файл расстановки в Files.",
    "Pick an existing Cluster first.": "Сначала выберите существующий Cluster.",
    "Anchor: set Ref or Role.": "Якорь: задайте Ref или Role.",
    "Anchor: Ref and Role are mutually exclusive — set one.": "Якорь: Ref и Role взаимоисключающие — задайте одно.",
    "Net overrides (resolved net -> final override):": "Переопределения цепей (разрешённая цепь -> итоговое переопределение):",
    "Resolved net": "Разрешённая цепь",
    "Override": "Переопределение",
    "Net overrides": "Переопределения цепей",
    "Refs": "Refs",
    "Absolute XY": "Абсолютный XY",
    "Anchor (ref/role)": "Якорь (ref/роль)",
    "Ref:": "Ref:",
    "Anchor cluster:": "Якорный кластер:",
    "e.g. C12": "например, C12",
    "e.g. U3 (refdes — mostly avoided in this project)": "например, U3 (refdes — в этом проекте в основном избегается)",
    "shift X mm (0)": "сдвиг X мм (0)",
    "shift Y mm (0)": "сдвиг Y мм (0)",
    "Shift X:": "Сдвиг X:",
    "Shift Y:": "Сдвиг Y:",
    "(cell default)": "(по умолчанию шаблона)",
    "literal net for {{{name}}}": "буквальная цепь для {{{name}}}",
    "Nets (role -> literal net, priority over the cell's own net_template):": "Цепи (роль -> буквальная цепь, приоритет над собственным net_template шаблона):",
    "Params (placeholder -> literal net, for by-nets role resolution):": "Параметры (плейсхолдер -> буквальная цепь, для резолва роли по цепям):",
    "Net aliases (blank = keep literal):": "Псевдонимы цепей (пусто = оставить как есть):",
    "Net template role (bridging component — pick which aliased net is the template):": "Роль шаблона цепи (связующий компонент — укажите, какая из алиасированных цепей является шаблоном):",
    "Cells:": "Шаблоны:",
    "Profiles:": "Профили:",
    "Existing": "Существующие",
    "Also save as extract_profile": "Также сохранить как extract_profile",
    "profile key (defaults to cell name)": "ключ профиля (по умолчанию имя шаблона)",
    "Origin": "Начало",
    "Origin:": "Начало:",
    "Via net": "Цепь via",
    "pick a file (or browse it in the Config tree)": "выберите файл (или найдите его в дереве Config)",
    "Bounding box (default)": "Ограничивающий прямоугольник (по умолчанию)",
    "Cell name:": "Имя шаблона:",
    "cell name (key under cells:)": "имя шаблона (ключ в cells:)",
    "Extract to file": "Извлечь в файл",
    "Profile key:": "Ключ профиля:",
    "No placer file picked (pick one in the Config tree, optional)": "Файл расстановки не выбран (выберите в дереве Config, необязательно)",
    "Alias {alias!r} used for both {a!r} and {b!r} — each alias needs a distinct net.": "Псевдоним {alias!r} используется и для {a!r}, и для {b!r} — у каждого псевдонима должна быть своя цепь.",
    "Origin: pick a via net.": "Начало: выберите цепь via.",
    "{count} field(s) could not be determined automatically: {details}": "Полей, которые не удалось определить автоматически: {count}: {details}",
    "Selection spans multiple Clusters: {clusters}": "Выделение охватывает несколько кластеров: {clusters}",
    "alias, e.g. PWR_IN": "псевдоним, например, PWR_IN",
    "Write this via/track net as null instead of a literal — at apply time a ManualSpoke-placed cell inherits the enclosing Rule's own net for it, so the cell can be reused across Rules on different nets.":
    "Записать цепь этой via/трека как null вместо литерала — при применении шаблон, размещённый через ManualSpoke, наследует для неё собственную цепь объемлющего правила, поэтому шаблон можно переиспользовать между разными правилами на разных цепях.",
    "'Also save as extract_profile' is checked, but no profile file is picked.": "Отмечено 'Also save as extract_profile', но файл профиля не выбран.",
    "Not connected.": "Нет подключения.",
    "{fp} component(s), {other} via/track(s) selected": "Выделено: {fp} компонент(ов), via/треков: {other}",
    "{fp} component(s) selected": "Выделено компонентов: {fp}",
    "Cell {cell!r} isn't reachable from the Placer file's include: — extract/save it and make sure include: is wired (see Extract).":
    "Шаблон {cell!r} недостижим из include: файла расстановки — извлеките/сохраните его и убедитесь, что include: подключён (см. Extract).",
    "Placement failed: {error}": "Размещение не удалось: {error}",
    "Placed, but tagging Cluster failed: {error}": "Размещено, но назначение Cluster не удалось: {error}",
    "Placed {name!r} ({count} component(s) tagged Cluster={name!r}).": "Размещено {name!r} (у компонентов: {count} назначен Cluster={name!r}).",
    "sheet name (narrows an ambiguous Role, optional)": "имя листа (сужает неоднозначную Role, необязательно)",
    "Drill/place (drill/position files, optional for Gerbers)": "Drill/place (файлы сверловки/позиций, для Gerbers необязательно)",
    "Grid (visual only — Place > Set Grid Origin)": "Grid (только визуально — Place > Set Grid Origin)",
    "Resolve": "Разрешить",
    " (no footprint to highlight)": " (нет футпринта для подсветки)",
    "X={x:.3f}mm Y={y:.3f}mm{suffix}": "X={x:.3f}мм Y={y:.3f}мм{suffix}",
    "{name!r} not found in {section}: of {path}": "{name!r} не найден в {section}: файла {path}",
    "{name!r} already exists in {section}: of {path}": "{name!r} уже существует в {section}: файла {path}",
    "Filter (ref/role/cluster)...": "Фильтр (ref/роль/кластер)...",
    "Clear Role and Cluster on ALL {count} component(s) currently on the board? This is a single commit — undo-able in KiCad with Ctrl+Z.":
    "Очистить Role и Cluster у ВСЕХ компонентов ({count}), сейчас находящихся на плате? Это один коммит — в KiCad отменяется через Ctrl+Z.",
    " and {more} more": " и ещё {more}",
    "Skipped {count} without Role/Cluster field: {refs}": "Пропущено без поля Role/Cluster: {count}: {refs}",
    "Clear failed: {error}": "Очистка не удалась: {error}",
    "Nothing selected.": "Ничего не выбрано.",
    "This refdes' units disagree on Role/Cluster — edit carefully.": "Единицы этого refdes расходятся по Role/Cluster — правьте осторожно.",
    "Not connected: {error}": "Нет подключения: {error}",
    "Not connected": "Нет подключения",
    "Not yet applied to schematic: {refs}": "Ещё не применено к схеме: {refs}",
    "Set Role and/or Cluster first.": "Сначала задайте Role и/или Cluster.",
    "Connect to KiCad first.": "Сначала подключитесь к KiCad.",
    "About to write {count} change(s):": "Будет записано изменений: {count}:",
    "Cannot ensure fields": "Не удалось добавить поля",
    "Fields added": "Поля добавлены",
    "Every component already has {role!r} and {cluster!r}.": "У каждого компонента уже есть {role!r} и {cluster!r}.",
    "Cannot apply": "Не удалось применить",
    "Some files failed": "Некоторые файлы не записались",
    "Restored from .bak: {failed}": "Восстановлено из .bak: {failed}",
    "Applied": "Применено",
    "Confirm apply": "Подтвердить применение",
    "Pick a root sheet first.": "Сначала выберите корневой лист.",
    "Close KiCad first": "Сначала закройте KiCad",
    "No root sheet picked": "Корневой лист не выбран",
    "Pick root sheet...": "Выбрать корневой лист...",
    "Pick the project's root .kicad_sch": "Выберите корневой .kicad_sch проекта",
    "Rescan failed": "Пересканирование не удалось",
    "Could not set fields": "Не удалось задать поля",
    "Some fields were skipped": "Некоторые поля пропущены",
}

# Second batch: the remaining problematic entries (uncovered by the first pass).
T2 = {
    "Process only spokes/clone_placements/thermal_via_arrays entries whose Cluster (anchor_cluster / spoke cluster) matches this path or prefix (segment-wise, e.g. 'Channel_0' also matches 'Channel_0/DAC_OA'). Repeatable and/or comma-separated. Combines with --only via AND (run apply twice for OR).":
    "Обработать только записи spokes/clone_placements/thermal_via_arrays, чей Cluster (anchor_cluster / cluster спицы) совпадает с этим путём или его префиксом (по сегментам: 'Channel_0' также подходит для 'Channel_0/DAC_OA'). Флаг можно повторять и/или указывать через запятую. Сочетается с --only через И (для ИЛИ запустите apply дважды).",
    "Cell name (key in cells:)": "Имя шаблона (ключ в cells:)",
    "Parameter for --net-template verification (e.g. channel=1); can be repeated; not written to the cell, only round-trip check":
    "Параметр для проверки --net-template (например, channel=1); можно повторять; в шаблон не пишется, только round-trip проверка",
    "Mapping real net -> pattern with {placeholder} (e.g. 'DAC1_DB1=DAC{channel}_DB1'); can be repeated; fills net_template for roles and parametrizes via.net at extraction":
    "Соответствие реальной цепи -> паттерну с {placeholder} (например, 'DAC1_DB1=DAC{channel}_DB1'); можно повторять; заполняет net_template для ролей и параметризует via.net при извлечении",
    "Stage": "Постановить в очередь",
    "Nothing to stage": "Нечего ставить в очередь",
    "Set Role/Cluster on {count} component(s)": "Задать Role/Cluster у компонентов: {count}",
    "Connected": "Подключено",
    "No root sheet": "Нет корневого листа",
    "Nothing to apply": "Нечего применять",
    "Nothing to add": "Нечего добавлять",
    "Force-close selected": "Принудительно закрыть выбранное",
    "Reconnect": "Переподключиться",
    "Connected — {count} components": "Подключено — компонентов: {count}",
    "(inherit cell layer)": "(наследовать слой шаблона)",
    "Name:": "Имя:",
    "Pad:": "Пад:",
    "Net template:": "Шаблон цепи:",
    "Update selected": "Обновить выбранное",
    "diameter mm (0.6)": "диаметр, мм (0.6)",
    "Vias": "Via",
    "Tracks": "Треки",
    "Mirror": "Mirror",
    "Angle": "Угол",
    "Pick a component row first.": "Сначала выберите строку компонента.",
    "Diameter": "Диаметр",
    "Start along": "Начало вдоль",
    "Write failed: {error}": "Запись не удалась: {error}",
    "Thermal via arrays": "Массивы термо-via",
    "Extract profiles": "Профили извлечения",
    "Open Root file": "Открыть корневой файл",
    "Export selected...": "Экспортировать выбранное...",
    "Add placer...": "Добавить расстановку...",
    "Rename": "Переименовать",
    "Renamed": "Переименовано",
    "Delete {name!r}": "Удалить {name!r}",
    "ies": "и",
    "Thermal via": "Термо-via",
    "Cell file:": "Файл шаблонов:",
    "Component role": "Роль компонента",
    "Net aliases": "Псевдонимы цепей",
    "Net template role": "Роль шаблона цепи",
    "Profile file:": "Файл профиля:",
    "Placer file: {path}": "Файл расстановки: {path}",
    "Origin: pick a component role.": "Начало: выберите роль компонента.",
    "Net template role: role {role!r} bridges 2+ aliased nets — pick which one is the template.":
    "Роль шаблона цепи: роль {role!r} связывает 2+ алиасированные цепи — укажите, какая является шаблоном.",
    "Extract failed: {error}": "Извлечение не удалось: {error}",
    "Clear": "Очистить",
    "Ref": "Ref",
    "Apply...": "Применить...",
    "Cluster / clone_placement name": "Имя Cluster / clone_placement",
    "ROLE": "ROLE",
    "override net name": "переопределить имя цепи",
    "Point": "Точка",
    "Point:": "Точка:",
    "Failed to load Placer file: {error}": "Не удалось загрузить файл расстановки: {error}",
    "Board origin": "Начало платы",
    "Resolve failed: {error}": "Разрешение не удалось: {error}",
    "Cluster": "Cluster",
    "Clear all": "Очистить все",
    "Nothing to clear.": "Нечего очищать.",
    "Clear Role/Cluster on {count} component(s)": "Очистить Role/Cluster у компонентов: {count}",
    "Root sheet:": "Корневой лист:",
    "Skip existing components": "Пропускать существующие компоненты",
    "{label} {text!r} is not a number.": "{label}: {text!r} не является числом.",
    "Spokes:": "Спицы:",
    "Placed {name!r}.": "Размещено {name!r}.",
    "pad number on the anchor footprint": "номер пада на футпринте якоря",
    "Cols:": "Столбцы:",
    "{label}: {text!r} is not an integer.": "{label}: {text!r} не является целым числом.",
    "Rule {name!r} (net {net!r}): retired=true, skipped entirely":
    "Правило {name!r} (цепь {net!r}): retired=true, пропущено целиком",
    "  {name!r} — not found among rules, clone_placements, or thermal_via_arrays{hint}":
    "  {name!r} — не найдено среди rules, clone_placements или thermal_via_arrays{hint}",
    "--only {requested}: rules={rules}, clone_placements={clones}, thermal_via_arrays={thermal} (everything else is ignored in this run)":
    "--only {requested}: rules={rules}, clone_placements={clones}, thermal_via_arrays={thermal} (всё остальное в этом прогоне игнорируется)",
    "--cluster {paths}: rules={rules} (spokes narrowed), clone_placements={clones}, thermal_via_arrays={thermal}":
    "--cluster {paths}: rules={rules} (спицы сужены), clone_placements={clones}, thermal_via_arrays={thermal}",
    "Loading config: {config}": "Загрузка конфига: {config}",
    "Connecting to KiCad (timeout {timeout} ms)": "Подключение к KiCad (таймаут {timeout} мс)",
    "  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°": "  {ref}: ({x:.3f}, {y:.3f}) мм, угол={angle:.1f}°",
    "  via for {owner}: ({x:.3f}, {y:.3f}) mm, net={net}": "  via для {owner}: ({x:.3f}, {y:.3f}) мм, цепь={net}",
    "  track for {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, width={w} mm":
    "  трек для {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) мм, цепь={net}, ширина={w} мм",
    "  {label}: {count} moves": "  {label}: перемещений: {count}",
    "Failed to move: {refs}": "Не удалось переместить: {refs}",
    "Planned vias: {total}, actually to create (registry filtered already correctly placed): {to_create}":
    "Запланировано via: {total}, фактически создать (реестр отфильтровал уже стоящие правильно): {to_create}",
    "Failed to create vias near: {refs}": "Не удалось создать via рядом с: {refs}",
    "Planned tracks: {total}, actually to create (registry filtered already correctly placed): {to_create}":
    "Запланировано треков: {total}, фактически создать (реестр отфильтровал уже стоящие правильно): {to_create}",
    "Failed to create tracks near: {refs}": "Не удалось создать треки рядом с: {refs}",
    "[error] --profile cannot be combined with --name/--output/--param/--net-template/--net-template-role/--rule-net/--origin-by-*: either all from profile or all as explicit flags, not mixed.":
    "[ошибка] --profile нельзя сочетать с --name/--output/--param/--net-template/--net-template-role/--rule-net/--origin-by-*: либо всё из профиля, либо всё явными флагами, не смешивая.",
    "[error] profile {profile!r} missing required field {field!r}":
    "[ошибка] в профиле {profile!r} отсутствует обязательное поле {field!r}",
    "Cell name (key under cells:): ": "Имя шаблона (ключ в cells:): ",
    "--param {item!r} — need format KEY=VALUE": "--param {item!r} — нужен формат KEY=VALUE",
    "--net-template {item!r} — need format LITERAL=PATTERN": "--net-template {item!r} — нужен формат LITERAL=PATTERN",
    "--net-template-role {item!r} — need format ROLE=LITERAL": "--net-template-role {item!r} — нужен формат ROLE=LITERAL",
    "[{channel}] footprints: {fp}, segments: {seg}, vias: {vias} -> {output}":
    "[{channel}] футпринты: {fp}, сегменты: {seg}, via: {vias} -> {output}",
    "Undoing operation from {file}": "Откат операции из {file}",
    "KiCad is busy and cannot respond right now. Usually this means an unfinished tool is running in the GUI (dimensioning, interactive routing, move tool, etc.) — finish it (Esc or right-click -> Cancel) and run the command again. The board was not modified.":
    "KiCad занят и сейчас не отвечает. Обычно это значит, что в GUI работает незавершённый инструмент (размеры, интерактивная трассировка, перемещение и т.п.) — завершите его (Esc или правый клик -> Cancel) и запустите команду снова. Плата не изменена.",
    "KiCad returned API error: {e}": "KiCad вернул ошибку API: {e}",
    "Error: {e}": "Ошибка: {e}",
    "[error] profiles file {path!r} not found": "[ошибка] файл профилей {path!r} не найден",
    "unknown fields in {top_key} {name!r} of {path!r}": "неизвестные поля в {top_key} {name!r} файла {path!r}",
    "Template {name!r} already exists in {output} — will be overwritten":
    "Шаблон {name!r} уже существует в {output} — будет перезаписан",
    "✅ Template {name!r} written to {output}": "✅ Шаблон {name!r} записан в {output}",
    "  FATAL ERROR: {title}": "  ФАТАЛЬНАЯ ОШИБКА: {title}",
    "unrecognised keys are silently ignored – common source of quiet bugs{extra}: {problems}":
    "нераспознанные ключи молча игнорируются — частый источник тихих багов{extra}: {problems}",
    "missing parameter {param} — add it to params of this clone_placement, or remove the placeholder":
    "отсутствует параметр {param} — добавьте его в params этого clone_placement или уберите плейсхолдер",
    "--net-template for {literal!r} fails round‑trip check": "--net-template для {literal!r} не проходит round-trip проверку",
    "pattern {pattern!r} with params={params} resolves to {check!r}, not to {literal!r} — typo in pattern or wrong parameter passed via --param":
    "паттерн {pattern!r} с params={params} разрешается в {check!r}, а не в {literal!r} — опечатка в паттерне или неверный параметр через --param",
    "Failed to read registry {path}: {type}: {e} — treating registry as empty (all vias will be created anew)":
    "Не удалось прочитать реестр {path}: {type}: {e} — считаю реестр пустым (все via будут созданы заново)",
    "  {key}: position/parameters changed, deleting old item ({uuid}) and creating a new one":
    "  {key}: позиция/параметры изменились, удаляю старый элемент ({uuid}) и создаю новый",
    "  prune: {key} no longer appears in config, deleting ({uuid})":
    "  prune: {key} больше нет в конфиге, удаляю ({uuid})",
    "Failed to parse {path} as .kicad_sch: {type}: {e} — skipped, sheet_name dictionary will be incomplete":
    "Не удалось разобрать {path} как .kicad_sch: {type}: {e} — пропущено, словарь имён листов будет неполным",
    "schematic_dir {dir!r} not found": "schematic_dir {dir!r} не найден",
    "expected directory {path} (relative to the config file {config!r})":
    "ожидалась директория {path} (относительно файла конфига {config!r})",
    "schematic_files: file {file!r} not found": "schematic_files: файл {file!r} не найден",
    "expected at {path} (relative to the config file {config!r})":
    "ожидалось в {path} (относительно файла конфига {config!r})",
    "sheet_names: scanned {count} .kicad_sch files, {sheets} sheets in dictionary":
    "sheet_names: просканировано файлов .kicad_sch: {count}, листов в словаре: {sheets}",
    "Tracks in selection: {total}, taken into cell: {kept} (the rest extend beyond the selection, see warning above)":
    "Треков в выделении: {total}, взято в шаблон: {kept} (остальные выходят за выделение, см. предупреждение выше)",
    "{ref}: no {field!r} field — every selected component must have a Role for template extraction":
    "{ref}: нет поля {field!r} — для извлечения шаблона у каждого выделенного компонента должен быть Role",
    "role {role!r} appears twice in selection: {ref1!r} and {ref2!r} — roles must be unique":
    "роль {role!r} встречается дважды в выделении: {ref1!r} и {ref2!r} — роли должны быть уникальны",
    "via on net {net!r}": "via на цепи {net!r}",
    "component with role {role!r}": "компонент с ролью {role!r}",
    "Cell layer: {layer}": "Слой шаблона: {layer}",
    "--net-template-role for role {role!r} asks for net {literal!r}, but it is not on any pad of {ref}":
    "--net-template-role для роли {role!r} запрашивает цепь {literal!r}, но её нет ни на одном паде {ref}",
    "actual nets on pads: {nets} — check typo in --net-template-role or in the role itself":
    "фактические цепи на падах: {nets} — проверьте опечатку в --net-template-role или в самой роли",
    "--net-template-role for role {role!r} asks for net {literal!r}, which is not in net_template_map":
    "--net-template-role для роли {role!r} запрашивает цепь {literal!r}, которой нет в net_template_map",
    "add {literal!r} to --net-template/net_template (or to params if it equals a parameter value) — otherwise there is no pattern to build":
    "добавьте {literal!r} в --net-template/net_template (или в params, если это значение параметра) — иначе не из чего строить паттерн",
    "  {ref} (role {role}): {count} nets from --net-template on pads ({nets}) — net_template not set, fill it manually in the resulting YAML, or use --net-template-role {role}=<net> in advance":
    "  {ref} (роль {role}): на падах {count} цепей из --net-template ({nets}) — net_template не задан, заполните его вручную в итоговом YAML или заранее укажите --net-template-role {role}=<цепь>",
    "  {ref} (role {role}): along={along}, across={across}, angle={angle}{layer}{net}":
    "  {ref} (роль {role}): along={along}, across={across}, угол={angle}{layer}{net}",
    ", layer={layer}": ", слой={layer}",
    ", net_template={nt}": ", net_template={nt}",
    "  via: along={along}, across={across}, net={net}": "  via: along={along}, across={across}, цепь={net}",
    "Extracted cell {name!r}: {comp} components, {vias} spoke‑level vias, {tracks} tracks":
    "Извлечён шаблон {name!r}: компонентов: {comp}, via уровня спиц: {vias}, треков: {tracks}",
    "  track ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) mm, net={net}: {missing} does not match anything else in the selection — probably extends beyond the intended area, skipped":
    "  трек ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) мм, цепь={net}: {missing} не соответствует ничему другому в выделении — вероятно, выходит за нужную область, пропущен",
    "--origin-by-via-net {net!r} not found in selection": "--origin-by-via-net {net!r} не найден в выделении",
    "among {count} selected vias, none is on net {net!r}": "среди выделенных via ({count}) ни одна не на цепи {net!r}",
    "--origin-by-via-net {net!r} is ambiguous": "--origin-by-via-net {net!r} неоднозначен",
    "selection contains {count} vias on this net: {pos} — refine the selection (keep only one such via) or use --origin-by-component-role instead":
    "в выделении {count} via на этой цепи: {pos} — уточните выделение (оставьте одну такую via) или используйте --origin-by-component-role",
    "--origin-by-component-pad {pad!r} not found": "--origin-by-component-pad {pad!r} не найден",
    "component with role {role!r} ({ref}) has no pad {pad!r} — pad numbers are strings as in KiCad":
    "у компонента с ролью {role!r} ({ref}) нет пада {pad!r} — номера падов это строки, как в KiCad",
    "--origin-by-component-role {role!r} not found in selection": "--origin-by-component-role {role!r} не найден в выделении",
    "among {count} selected components, none has role {role!r}": "среди выделенных компонентов ({count}) ни один не имеет роли {role!r}",
    "Component {ref} not found, skipping": "Компонент {ref} не найден, пропуск",
    "Restoring {ref} to layer {layer} (flip)": "Возврат {ref} на слой {layer} (flip)",
    "Restored {ref} to position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°":
    "Вернул {ref} в позицию ({x:.3f}, {y:.3f}) мм, угол {angle:.1f}°",
    "Deleted via with UUID {uuid}": "Удалена via с UUID {uuid}",
    "Deleted track with UUID {uuid}": "Удалён трек с UUID {uuid}",
    "File {name} deleted.": "Файл {name} удалён.",
    "Failed to delete file {name}: {e}": "Не удалось удалить файл {name}: {e}",
    "rule (net {net!r}): anchor {anchor!r} not found on board":
    "правило (цепь {net!r}): якорь {anchor!r} не найден на плате",
    "Cell/pad checks for spokes: all references valid": "Проверки шаблонов/падов для спиц: все ссылки корректны",
    " (cluster {cluster!r})": " (кластер {cluster!r})",
    "not enough components for cell roles": "не хватает компонентов для ролей шаблона",
    "Clone cell existence checks passed": "Проверки существования клонируемых шаблонов пройдены",
    "Cell definition cycle checks passed": "Проверки циклов в определениях шаблонов пройдены",
    "name {name!r} appears twice in clone_placements — names must be unique":
    "имя {name!r} встречается дважды в clone_placements — имена должны быть уникальны",
    "{this!r} and {other!r} both point to the same anchor with the same offset (cell/role={content!r}, anchor_role={role!r}, anchor_sheet={sheet!r}, anchor_cluster={cluster!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; likely a copy‑paste typo (if this is intentional, give them different xy)":
    "{this!r} и {other!r} оба указывают на один и тот же якорь с одинаковым смещением (cell/role={content!r}, anchor_role={role!r}, anchor_sheet={sheet!r}, anchor_cluster={cluster!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) мм) — реестр перепутает их via/треки; похоже на опечатку copy-paste (если это намеренно, задайте им разный xy)",
    "clone_placements with anchor_sheet: {users}": "clone_placements с anchor_sheet: {users}",
    "{name!r}, {where}: via.net {net_name!r} resolves to {resolved!r}, but that net does not exist on the board{suggestion}":
    "{name!r}, {where}: via.net {net_name!r} разрешается в {resolved!r}, но такой цепи нет на плате{suggestion}",
    "via of role {role!r}": "via роли {role!r}",
    "solution: either set retired: true on all but one, or run apply separately for each using --only NAME":
    "решение: либо задайте retired: true всем, кроме одного, либо запускайте apply отдельно для каждого через --only NAME",
    "Channel {channel!r} not found; available: {avail}": "Канал {channel!r} не найден; доступны: {avail}",
    "Snapshot of {channel} written: {output}": "Снимок {channel} записан: {output}",
    "netlist: {comps} components, channels with local nets: {channels}, global nets: {global_nets}":
    "netlist: компонентов: {comps}, каналов с локальными цепями: {channels}, глобальных цепей: {global_nets}",
    "incomplete twin group [{key}]: present only in {channels}": "неполная группа-двойник [{key}]: есть только в {channels}",
    "total incomplete groups: {count} — these components cannot be cloned by mapping":
    "всего неполных групп: {count} — эти компоненты нельзя клонировать сопоставлением",
    "channels: {count} ({names}); complete twin groups: {complete} of {total}":
    "каналы: {count} ({names}); полных групп-двойников: {complete} из {total}",
    "Reading board: {path}": "Чтение платы: {path}",
    "Board: {fp} footprints, {seg} segments, {vias} vias; nets in copper: {nets}":
    "Плата: футпринтов: {fp}, сегментов: {seg}, via: {vias}; цепей в меди: {nets}",
    "{channel}: {fp} footprints, {seg} segments, {vias} vias; foreign in bbox: {fseg} segs, {fvia} vias":
    "{channel}: футпринтов: {fp}, сегментов: {seg}, via: {vias}; чужих в bbox: сегментов: {fseg}, via: {fvia}",
    "via.net must be a string, not {type}": "via.net должна быть строкой, а не {type}",
    "looks like broken YAML – e.g. net_overrides accidentally nested under this via's net instead of being a top-level field of clone_placement (net_overrides is a sibling of cell/params, not under via)":
    "похоже на битый YAML — например, net_overrides случайно вложен в цепь этой via, вместо того чтобы быть полем верхнего уровня clone_placement (net_overrides — сосед cell/params, а не вложен в via)",
    "track.net must be a string, not {type}": "track.net должен быть строкой, а не {type}",
    "got: {net!r} (start_along_mm={along}, start_across_mm={across})":
    "получено: {net!r} (start_along_mm={along}, start_across_mm={across})",
    "invalid layer={value!r} {where}": "некорректный layer={value!r} {where}",
    "component slot without a role": "слот компонента без роли",
    "relative 'side' is deprecated (see discussion v116): layer is now absolute – write layer: F.Cu or layer: B.Cu, or remove the field to inherit the cell layer":
    "относительный 'side' устарел (см. обсуждение v116): layer теперь абсолютный — пишите layer: F.Cu или layer: B.Cu, либо уберите поле, чтобы наследовать слой шаблона",
    "name appears twice among clone_placements of cell {name!r}":
    "имя встречается дважды среди clone_placements шаблона {name!r}",
    "anchor_xy together with anchor_role in cell {name!r}": "anchor_xy вместе с anchor_role в шаблоне {name!r}",
    "anchor_xy must be a 2-element [x, y] list in cell {name!r}":
    "anchor_xy должен быть списком из 2 элементов [x, y] в шаблоне {name!r}",
    "anchor_role {role!r} is not a component of cell {name!r}":
    "anchor_role {role!r} не является компонентом шаблона {name!r}",
    "nested clone_placement without name in cell {cell!r}": "вложенный clone_placement без имени в шаблоне {cell!r}",
    "unknown fields in nested clone_placement {name!r} of cell {cell!r}":
    "неизвестные поля во вложенном clone_placement {name!r} шаблона {cell!r}",
    "need either cell: <name from cells:>, or role: <ROLE> for a single-component placement without a separate cell":
    "нужен либо cell: <имя из cells:>, либо role: <ROLE> для однокомпонентного размещения без отдельного шаблона",
    "invalid anchor_origin {value!r} in point {name!r}": "некорректный anchor_origin {value!r} в точке {name!r}",
    "anchor_ref and anchor_role together in point {name!r}": "anchor_ref и anchor_role вместе в точке {name!r}",
    "anchor_sheet without anchor_role in point {name!r}": "anchor_sheet без anchor_role в точке {name!r}",
    "anchor_pad={pad!r} is set but no anchor specified": "anchor_pad={pad!r} задан, но якорь не указан",
    "unknown fields in rule (net {net!r})": "неизвестные поля в правиле (цепь {net!r})",
    "anchor_ref and anchor_role together in rule (net {net!r})": "anchor_ref и anchor_role вместе в правиле (цепь {net!r})",
    "anchor_point together with anchor_ref/anchor_role in rule (net {net!r})":
    "anchor_point вместе с anchor_ref/anchor_role в правиле (цепь {net!r})",
    "a spoke rule must have an anchor – anchor_ref: <ref> (component whose pads are listed in spokes), anchor_role: <ROLE> (survives re‑annotation), or anchor_point: <name from points:>":
    "правило спицы должно иметь якорь — anchor_ref: <ref> (компонент, пады которого перечислены в спицах), anchor_role: <ROLE> (переживает переаннотацию) или anchor_point: <имя из points:>",
    "unknown fields in clone_placement {name!r}": "неизвестные поля в clone_placement {name!r}",
    "neither cell, role, nor cluster set in clone_placement {name!r}":
    "ни cell, ни role, ни cluster не заданы в clone_placement {name!r}",
    "cluster together with nets/params/by_selection in clone_placement {name!r}":
    "cluster вместе с nets/params/by_selection в clone_placement {name!r}",
    "anchor_ref and anchor_role together in clone_placement {name!r}":
    "anchor_ref и anchor_role вместе в clone_placement {name!r}",
    "anchor_sheet without anchor_role in clone_placement {name!r}":
    "anchor_sheet без anchor_role в clone_placement {name!r}",
    "anchor_point together with anchor_pad in clone_placement {name!r}":
    "anchor_point вместе с anchor_pad в clone_placement {name!r}",
    "anchor_pad without anchor_ref/anchor_role in clone_placement {name!r}":
    "anchor_pad без anchor_ref/anchor_role в clone_placement {name!r}",
    "anchor_pad={pad!r} is set but no anchor specified – use anchor_ref: IC1 or anchor_role: SOME_ROLE":
    "anchor_pad={pad!r} задан, но якорь не указан — используйте anchor_ref: IC1 или anchor_role: SOME_ROLE",
    "either set xy: [x, y] (absolute point on board), or anchor_ref/anchor_role (+ optionally anchor_pad), or anchor_point, for anchor‑based placement":
    "задайте либо xy: [x, y] (абсолютная точка на плате), либо anchor_ref/anchor_role (+ необязательно anchor_pad), либо anchor_point — для размещения на основе якоря",
    "deprecated fields 'origin_x_mm'/'origin_y_mm' in clone_placement {name!r}":
    "устаревшие поля 'origin_x_mm'/'origin_y_mm' в clone_placement {name!r}",
    "xy must be a 2-element [x, y] list in clone_placement {name!r}":
    "xy должен быть списком из 2 элементов [x, y] в clone_placement {name!r}",
    "by_selection: true with non-empty nets in clone_placement {name!r}":
    "by_selection: true с непустыми nets в clone_placement {name!r}",
    "deprecated field 'target_ref' in thermal_via_arrays": "устаревшее поле 'target_ref' в thermal_via_arrays",
    "anchor_point together with anchor_ref/anchor_role in thermal_via_arrays entry {name!r}":
    "anchor_point вместе с anchor_ref/anchor_role в записи thermal_via_arrays {name!r}",
    "{file!r}: top level must be a YAML mapping, got {type}": "{file!r}: верхний уровень должен быть YAML-отображением, получено {type}",
    "{file!r}: {section!r} must be a list, got {type}": "{file!r}: {section!r} должен быть списком, получено {type}",
    "{file!r}: {section!r} must be a mapping, got {type}": "{file!r}: {section!r} должно быть отображением, получено {type}",
    "expected at {path} (relative to {source!r}, not the current working directory)":
    "ожидалось в {path} (относительно {source!r}, а не текущего рабочего каталога)",
    "Loading configuration from {path}": "Загрузка конфигурации из {path}",
    "global target_ref has been removed (see discussion v117): each spoke rule now has its own anchor – write anchor_ref: <ref> inside the rule in rules; each thermal_via_arrays entry has its own anchor_ref field":
    "глобальный target_ref удалён (см. обсуждение v117): теперь у каждого правила спицы свой якорь — пишите anchor_ref: <ref> внутри правила в rules; у каждой записи thermal_via_arrays своё поле anchor_ref",
    "deprecated field 'thermal_via_array' at root of config": "устаревшее поле 'thermal_via_array' в корне конфига",
    "duplicate name(s) in thermal_via_arrays: {names}": "дублирующиеся имена в thermal_via_arrays: {names}",
    "deprecated field(s) 'cells_file'/'cell_files' at root of config":
    "устаревшие поля 'cells_file'/'cell_files' в корне конфига",
    "{count} rules resolve to the same --only identity {name!r} (anchors: {anchors})":
    "правил: {count} разрешаются в одну и ту же идентичность --only {name!r} (якоря: {anchors})",
    "give at least one of them an explicit name: to disambiguate (e.g. name: {name}_a) – --only cannot tell them apart otherwise":
    "дайте хотя бы одному из них явное name: для различения (например, name: {name}_a) — иначе --only не сможет их различать",
    "cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – mirror without changing side is physically meaningless: either set layer to {opposite}, or remove mirror":
    "шаблон {cell!r} на {cell_layer}, слой размещения {place_layer} — mirror без смены стороны физически бессмыслен: либо задайте layer {opposite}, либо уберите mirror",
    "known points: {names}": "известные точки: {names}",
    "Config loaded: layer={layer}, cells={cells}, points={points}, rules={rules}, spokes={spokes}, clone_placements={clones}":
    "Конфиг загружен: layer={layer}, cells={cells}, points={points}, rules={rules}, spokes={spokes}, clone_placements={clones}",
    "Failed to get kicad PIDs: {e}": "Не удалось получить PID процессов kicad: {e}",
    "(from env) {p} exists={exists}": "(из env) {p} существует={exists}",
    "{p} (mtime {age:.1f} min ago)": "{p} (mtime {age:.1f} мин назад)",
    "scanning {root}: {e}": "сканирование {root}: {e}",
    "--- environment snapshot [{tag}] ---": "--- снимок окружения [{tag}] ---",
    "  {var} = {val!r}  <-- LINGERS IN ENVIRONMENT (candidate for stale!)":
    "  {var} = {val!r}  <-- ОСТАЛСЯ В ОКРУЖЕНИИ (кандидат на устаревший!)",
    "  {var} = {val!r}": "  {var} = {val!r}",
    "  kicad.exe PID: {pids}  <-- MORE THAN ONE INSTANCE (hypothesis H2: zombie!)":
    "  PID kicad.exe: {pids}  <-- БОЛЕЕ ОДНОГО ЭКЗЕМПЛЯРА (гипотеза H2: зомби!)",
    "  kicad.exe PID: {pids}": "  PID kicad.exe: {pids}",
    "  socket: {s}": "  сокет: {s}",
    "===== STEP {num}: {name} =====": "===== ШАГ {num}: {name} =====",
    "step {num} OK in {dt:.3f}s": "шаг {num} OK за {dt:.3f}с",
    "step {num} FAIL in {dt:.3f}s: {type}: {e}": "шаг {num} ПРОВАЛЕН за {dt:.3f}с: {type}: {e}",
    "!!! kicad.exe PID {died} DIED after step {step} !!!": "!!! процесс kicad.exe {died} УМЕР после шага {step} !!!",
    "PID set changed: was {old}, now {new}": "Набор PID изменился: было {old}, стало {new}",
    "pulse after step {step}: ping OK ({dt:.0f} ms), PID {pids}":
    "импульс после шага {step}: ping OK ({dt:.0f} мс), PID {pids}",
    "pulse after step {step}: ping FAIL — {type}: {e}; PID {pids}":
    "импульс после шага {step}: ping ПРОВАЛЕН — {type}: {e}; PID {pids}",
    "kicad={v}, api={api}": "kicad={v}, api={api}",
    "open PCB documents: {count}": "открытых PCB-документов: {count}",
    "board received: {ok}": "плата получена: {ok}",
    "{total} footprints; write candidate: {ref}": "футпринтов: {total}; кандидат на запись: {ref}",
    "{ref}: pos=({x:.3f}, {y:.3f}) mm, angle={angle:.1f}, layer={layer}, pads={pads}":
    "{ref}: позиция=({x:.3f}, {y:.3f}) мм, угол={angle:.1f}, слой={layer}, пады={pads}",
    "begin_commit() OK, sending no‑op update_items([{ref}]) inside transaction...":
    "begin_commit() OK, отправляю no-op update_items([{ref}]) внутри транзакции...",
    "begin_commit -> no‑op update_items({ref}) -> push_commit completed fully":
    "begin_commit -> no-op update_items({ref}) -> push_commit завершён полностью",
    "log: {path}": "журнал: {path}",
    "python {version}; arguments: {args}": "python {version}; аргументы: {args}",
    "kipy {version}": "kipy {version}",
    "ladder broke at step {num} ({name}) — see verdict above": "лесенка оборвалась на шаге {num} ({name}) — см. вердикт выше",
    "pausing {delay}s before write (test H1)...": "пауза {delay}с перед записью (тест H1)...",
    "  [{num}] {name}: {verdict} ({dt:.3f}s)": "  [{num}] {name}: {verdict} ({dt:.3f}с)",
    "full log: {path}": "полный журнал: {path}",
    "comma‑separated, no spaces (default: {default})": "через запятую, без пробелов (по умолчанию: {default})",
    "{ref}.{field} = {value!r} — clean": "{ref}.{field} = {value!r} — чисто",
    "\n\nChecked footprints: {count}, fields per component: {fields}\n":
    "\n\nПроверено футпринтов: {count}, полей на компонент: {fields}\n",
    "\n=== FOUND {count} field(s) with suspicious characters ===\n":
    "\n=== НАЙДЕНО {count} полей с подозрительными символами ===\n",
    "{ref}.{field} = {value!r}": "{ref}.{field} = {value!r}",
    "    position {pos}: {ch!r} U+{cp:04X} ({name})": "    позиция {pos}: {ch!r} U+{cp:04X} ({name})",
    "Loading config: {path}": "Загрузка конфига: {path}",
    "No active thermal_via_arrays entry — keepout diagnostics skipped":
    "Нет активной записи thermal_via_arrays — диагностика keepout пропущена",
    "Built {count} keepout rectangles": "Построено прямоугольников keepout: {count}",
    "  [{i}] X: {xmin:.3f}..{xmax:.3f} mm, Y: {ymin:.3f}..{ymax:.3f} mm":
    "  [{i}] X: {xmin:.3f}..{xmax:.3f} мм, Y: {ymin:.3f}..{ymax:.3f} мм",
    "  {ref:6} pos=({x:7.3f}, {y:7.3f}) mm  -> {status}": "  {ref:6} позиция=({x:7.3f}, {y:7.3f}) мм  -> {status}",
    "  via for {owner:6} ({x:7.3f}, {y:7.3f}) mm  -> {status}":
    "  via для {owner:6} ({x:7.3f}, {y:7.3f}) мм  -> {status}",
    "Component {ref} not found": "Компонент {ref} не найден",
    "{ref} has no pads": "у {ref} нет падов",
    "Pad {pad} not found on {ref}": "Пад {pad} не найден на {ref}",
    "Retrieved {count} bounding boxes": "Получено bounding box: {count}",
    "Pad {num}: bounding box missing": "Пад {num}: bounding box отсутствует",
    "Pad {num}: size {w:.3f} x {h:.3f} mm, position ({x:.3f}, {y:.3f}) mm":
    "Пад {num}: размер {w:.3f} x {h:.3f} мм, позиция ({x:.3f}, {y:.3f}) мм",
    "Copper layer of pad {num}: {w:.3f} x {h:.3f} mm": "Медный слой пада {num}: {w:.3f} x {h:.3f} мм",
    "Selected components: {count}": "Выделено компонентов: {count}",
    "\n{w:.3f} x {h:.3f} mm": "\n{w:.3f} x {h:.3f} мм",
    "[{ref}]": "[{ref}]",
    "  Value:        {val}": "  Номинал:     {val}",
    "  Footprint:    {fp}": "  Футпринт:    {fp}",
    "  Position:     ({x:.3f}, {y:.3f}) mm": "  Позиция:     ({x:.3f}, {y:.3f}) мм",
    "  Angle:        {angle:.1f}°": "  Угол:        {angle:.1f}°",
    "  Size:         {size}": "  Размер:      {size}",
    "  Role:         {role}": "  Роль:        {role}",
    "{pw:.2f} x {ph:.2f} mm": "{pw:.2f} x {ph:.2f} мм",
    "    {pnum}: net={net:<15} pos=({px:.3f}, {py:.3f}) mm size={psize}":
    "    {pnum}: цепь={net:<15} позиция=({px:.3f}, {py:.3f}) мм размер={psize}",
    "[...] {label}": "[...] {label}",
    "[OK]  {label} — {elapsed} ms": "[OK]  {label} — {elapsed} мс",
    "[ERR] {label} — {elapsed} ms — {type}: {e}": "[ОШИБКА] {label} — {elapsed} мс — {type}: {e}",
    "[error] no saved id in {file} — pass --remove <uuid> explicitly":
    "[ошибка] в {file} нет сохранённого id — передайте --remove <uuid> явно",
    "Taking id from {file}: {id}": "Беру id из {file}: {id}",
    "\n\nVia {id} deleted.": "\n\nVia {id} удалена.",
    "adapter.get_footprint({ref!r})": "adapter.get_footprint({ref!r})",
    "[error] {ref} not found on the board": "[ошибка] {ref} не найден на плате",
    "adapter.get_net_by_name({net!r})": "adapter.get_net_by_name({net!r})",
    "[error] net {net!r} not found on the board": "[ошибка] цепь {net!r} не найдена на плате",
    "\n{ref} at ({x:.3f}, {y:.3f}) mm, via will be at ({vx:.3f}, {vy:.3f}) mm, net={net}\n":
    "\n{ref} в ({x:.3f}, {y:.3f}) мм, via будет в ({vx:.3f}, {vy:.3f}) мм, цепь={net}\n",
    "\n\nDone. Via created, id={id}\n": "\n\nГотово. Via создана, id={id}\n",
    "id saved to {file} — to delete it, just run:":
    "id сохранён в {file} — чтобы удалить её, просто выполните:",
    "  python -m kicadstamp.diagnostics.test_create_one_via --remove":
    "  python -m kicadstamp.diagnostics.test_create_one_via --remove",
    "Element not found: {spec}": "Элемент не найден: {spec}",
    "Saved: {path}": "Сохранено: {path}",
    "layer={layer}, pos=({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°":
    "слой={layer}, позиция=({x:.3f}, {y:.3f}) мм, угол={angle:.1f}°",
    "=== Test: flip component {ref}, timeout={timeout} ms ===":
    "=== Тест: флип компонента {ref}, таймаут={timeout} мс ===",
    "\n\nBefore flip: {desc}\n": "\n\nДо флипа: {desc}\n",
    "adapter.get_footprint({ref!r}) (after)": "adapter.get_footprint({ref!r}) (после)",
    "\nAfter flip: {desc}\n": "\nПосле флипа: {desc}\n",
    "=== Test: move {ref} by {delta:+.2f} mm along X, timeout={timeout} ms ===":
    "=== Тест: перемещение {ref} на {delta:+.2f} мм по X, таймаут={timeout} мс ===",
    "\n\nCurrent position of {ref}: ({x:.3f}, {y:.3f}) mm":
    "\n\nТекущая позиция {ref}: ({x:.3f}, {y:.3f}) мм",
    "New position:            ({x:.3f}, {y:.3f}) mm": "Новая позиция:            ({x:.3f}, {y:.3f}) мм",
    "\n\nDone. {ref} moved by {delta:+.2f} mm along X.\n": "\n\nГотово. {ref} перемещён на {delta:+.2f} мм по X.\n",
    "To revert: python -m kicadstamp.diagnostics.test_move_one_cap {ref} --delta-mm {d} --revert":
    "Для отката: python -m kicadstamp.diagnostics.test_move_one_cap {ref} --delta-mm {d} --revert",
    "test_pad_mirror_convention: rotate {ref} by {delta:+.1f}°":
    "test_pad_mirror_convention: поворот {ref} на {delta:+.1f}°",
    "[error] {ref} has no pad {pad}": "[ошибка] у {ref} нет пада {pad}",
    "\n=== Initial state of {ref} ===\n": "\n=== Исходное состояние {ref} ===\n",
    "position=({x:.3f},{y:.3f}) mm angle={angle:.1f}° layer={layer}":
    "позиция=({x:.3f},{y:.3f}) мм угол={angle:.1f}° слой={layer}",
    "local offset of pad {pad}: ({x:.3f}, {y:.3f}) mm": "локальное смещение пада {pad}: ({x:.3f}, {y:.3f}) мм",
    "\nPredicted: ({x:.3f}, {y:.3f}) mm": "\nПредсказано: ({x:.3f}, {y:.3f}) мм",
    "Real:      ({x:.3f}, {y:.3f}) mm": "Реально:    ({x:.3f}, {y:.3f}) мм",
    "Difference: {d:.4f} mm -- OK, base formula works": "Разница: {d:.4f} мм -- ОК, базовая формула работает",
    "Difference: {d:.4f} mm !! BASE FORMULA FAILS, further flip test is meaningless":
    "Разница: {d:.4f} мм !! БАЗОВАЯ ФОРМУЛА НЕ РАБОТАЕТ, дальнейший тест флипа бессмыслен",
    "Real pad position after flip: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°":
    "Реальная позиция пада после флипа: ({x:.3f}, {y:.3f}) мм, угол={angle:.1f}°",
    "Candidate 'mirror X' (current code): deviation {d:.4f} mm":
    "Кандидат 'mirror X' (текущий код): отклонение {d:.4f} мм",
    "Candidate 'mirror Y':                 deviation {d:.4f} mm":
    "Кандидат 'mirror Y':                 отклонение {d:.4f} мм",
    "Candidate 'no mirror':                deviation {d:.4f} mm":
    "Кандидат 'без mirror':                отклонение {d:.4f} мм",
    "\n>>> WINNER: {name} (deviation {d:.4f} mm)": "\n>>> ПОБЕДИТЕЛЬ: {name} (отклонение {d:.4f} мм)",
    ">>> The code in pad_projection.py needs fixing: currently mirrors X, but should do {winner}.":
    ">>> Коду в pad_projection.py нужна правка: сейчас зеркалится X, а должно быть {winner}.",
    "track (along={s_along},{s_across} -> {e_along},{e_across}) has no net — every track in a cloned cell must have a net, just like vias":
    "трек (along={s_along},{s_across} -> {e_along},{e_across}) не имеет цепи — каждый трек в клонируемом шаблоне должен иметь цепь, как и via",
    "margin_mm={margin_mm} is too large for a pad {width}x{height} mm":
    "margin_mm={margin_mm} слишком велик для пада {width}x{height} мм",
    "Initialising KiCadBoardAdapter with timeout {timeout} ms":
    "Инициализация KiCadBoardAdapter с таймаутом {timeout} мс",
    "Found footprint {ref}": "Найден футпринт {ref}",
    "Footprint with uuid {uuid} not found": "Футпринт с uuid {uuid} не найден",
    "Retrieved {count} footprints": "Получено футпринтов: {count}",
    "Retrieved {count} vias": "Получено via: {count}",
    "Retrieved {count} tracks": "Получено треков: {count}",
    "Setting GUI selection to {count} items": "Установка выделения в GUI: {count} элементов",
    "Found zone {name}": "Найдена зона {name}",
    "Zone {name} not found": "Зона {name} не найдена",
    "Found net {name}": "Найдена цепь {name}",
    "Net {name} not found": "Цепь {name} не найдена",
    "Retrieved {count} nets": "Получено цепей: {count}",
    "Committing transaction: {desc}": "Фиксация транзакции: {desc}",
    "Transaction committed: {desc}": "Транзакция зафиксирована: {desc}",
    "Checking open schematics failed: {e}": "Проверка открытых схем не удалась: {e}",
    "{op}: KiCad not ready to respond (busy/modal dialog?), retrying in {wait:.1f}s [{attempt}/{retries}]":
    "{op}: KiCad не готов отвечать (занят/модальный диалог?), повтор через {wait:.1f}с [{attempt}/{retries}]",
    "{op}: connection to KiCad broke during write — KiCad probably crashed (known crash on first API write with schematic open: issue #24966; workaround: move a component + Ctrl+S in pcbnew before running). Original error: {e}":
    "{op}: соединение с KiCad оборвалось при записи — вероятно, KiCad упал (известный краш на первой записи в API при открытой схеме: issue #24966; обход: переместите компонент + Ctrl+S в pcbnew перед запуском). Исходная ошибка: {e}",
    "Updating {count} items": "Обновление элементов: {count}",
    "Creating {count} items": "Создание элементов: {count}",
    "Created {count} items": "Создано элементов: {count}",
    "Flipping {count} footprints via GUI action": "Флип футпринтов через GUI-действие: {count}",
    "Attempt {attempt}/{total} for {desc}": "Попытка {attempt}/{total} для {desc}",
    "Failed to roll back transaction {desc}: {e}": "Не удалось откатить транзакцию {desc}: {e}",
    "Error in transaction {desc} (attempt {attempt}): {type}: {e}":
    "Ошибка в транзакции {desc} (попытка {attempt}): {type}: {e}",
    "Creating via at ({x:.3f}, {y:.3f}) mm, net={net}": "Создание via в ({x:.3f}, {y:.3f}) мм, цепь={net}",
    "Creating track ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}":
    "Создание трека ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) мм, цепь={net}",
    "Failed to delete object {uuid}: {type}: {e}": "Не удалось удалить объект {uuid}: {type}: {e}",
    "{name}: cell {cell!r} not found in cells, skipping": "{name}: шаблон {cell!r} не найден в cells, пропуск",
    "Planner initialised: layer={layer}, anchors in rules: {anchors}":
    "Планировщик инициализирован: слой={layer}, якорей в правилах: {anchors}",
    "ClonePlacement: {count} components, {vias} vias, {tracks} tracks":
    "ClonePlacement: компонентов: {count}, via: {vias}, треков: {tracks}",
    "plan_moves completed: {count} moves": "plan_moves завершено: перемещений: {count}",
    "Flipping {count} components": "Флип компонентов: {count}",
    "  flipped {count} items (batch {batch})": "  перевёрнуто элементов: {count} (батч {batch})",
    "Detected {count} potential collisions:": "Обнаружено потенциальных коллизий: {count}:",
    "  {ref1} and {ref2} overlap (distance {dist:.2f} mm)":
    "  {ref1} и {ref2} пересекаются (расстояние {dist:.2f} мм)",
    "Moving in {count} batches": "Перемещение в {count} батчей",
    "  {ref} not found, skipping": "  {ref} не найден, пропуск",
    "  updated {count} footprints": "  обновлено футпринтов: {count}",
    "Move batch {idx}/{total}": "Батч перемещения {idx}/{total}",
    "  move batch {idx} failed": "  батч перемещения {idx} не удался",
    "  move batch {idx} completed ({count} items)": "  батч перемещения {idx} завершён ({count} элементов)",
    "Operation log saved to {file}": "Журнал операции сохранён в {file}",
    "Failed to save operation log: {e}": "Не удалось сохранить журнал операции: {e}",
    "Creating tracks in {count} batches": "Создание треков в {count} батчей",
    "  net {net} not found for track for {owner}": "  цепь {net} не найдена для трека для {owner}",
    "  created {count} tracks": "  создано треков: {count}",
    "Track batch {idx}/{total}": "Батч треков {idx}/{total}",
    "  track batch {idx} failed": "  батч треков {idx} не удался",
    "  track batch {idx} completed ({count} items)": "  батч треков {idx} завершён ({count} элементов)",
    "Creating vias in {count} batches": "Создание via в {count} батчей",
    "  net {net} not found for via for {owner}": "  цепь {net} не найдена для via для {owner}",
    "  created {count} vias": "  создано via: {count}",
    "Via batch {idx}/{total}": "Батч via {idx}/{total}",
    "  via batch {idx} failed": "  батч via {idx} не удался",
    "  via batch {idx} completed ({count} items)": "  батч via {idx} завершён ({count} элементов)",
    "  [{name}] anchor: centre of {ref} ({x:.3f}, {y:.3f}) mm":
    "  [{name}] якорь: центр {ref} ({x:.3f}, {y:.3f}) мм",
    "  [{name}] anchor: pad {ref}.{pad} ({x:.3f}, {y:.3f}) mm":
    "  [{name}] якорь: пад {ref}.{pad} ({x:.3f}, {y:.3f}) мм",
    "  [{name}] cell {tpl!r} on {layer}{mirror_suffix}": "  [{name}] шаблон {tpl!r} на {layer}{mirror_suffix}",
    "  [{name}] spoke‑level via: ({x:.3f}, {y:.3f}) mm, net={net}":
    "  [{name}] via уровня спицы: ({x:.3f}, {y:.3f}) мм, цепь={net}",
    "  [{name}] track: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, layer={layer}":
    "  [{name}] трек: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) мм, цепь={net}, слой={layer}",
    "  [{name}] {ref} (role {role}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°":
    "  [{name}] {ref} (роль {role}): позиция ({x:.3f}, {y:.3f}) мм, угол {angle:.1f}°",
    "[{name}] cluster {cluster!r} -> {ref}": "[{name}] кластер {cluster!r} -> {ref}",
    "{ref}: role {role!r} is not in the cell (cell roles: {roles})":
    "{ref}: роли {role!r} нет в шаблоне (роли шаблона: {roles})",
    "role {role!r} is in cell but not found anywhere on board":
    "роль {role!r} есть в шаблоне, но нигде не найдена на плате",
    "selection does not match cell composition ({name!r})": "выделение не соответствует составу шаблона ({name!r})",
    "refs: role {role!r} does not exist in cell {cell!r}": "refs: роли {role!r} нет в шаблоне {cell!r}",
    "refs: component {ref!r} (role {role!r}) not found on board":
    "refs: компонент {ref!r} (роль {role!r}) не найден на плате",
    "role {role!r}: no net for mapping (neither in nets of {name!r}, nor in cell net_template) — in 'by nets' mode, a net is required for every role":
    "роль {role!r}: нет цепи для сопоставления (ни в nets у {name!r}, ни в net_template шаблона) — в режиме 'by nets' для каждой роли нужна цепь",
    "role {role!r}: NO component with this role on the board at all (check the Role field in the schematic, and that Update PCB from Schematic was run)":
    "роль {role!r}: на плате вообще НЕТ компонента с этой ролью (проверьте поле Role в схеме и что был выполнен Update PCB from Schematic)",
    "role {role!r}: component(s) {refs} with this role exist on the board, but none is on net {expected!r} — they are actually on {found} (check params/net name or the schematic connection)":
    "роль {role!r}: компоненты {refs} с этой ролью есть на плате, но ни один не на цепи {expected!r} — фактически они на {found} (проверьте params/имя цепи или подключение в схеме)",
    "role {role!r}: ambiguity — {count} components on net {net!r}{cluster_hint}{note}: {refs}. Solutions: set anchor_sheet and/or anchor_cluster (if assigned in the schematic), OR select the desired instance on the board before running, OR split roles by net names in the schematic (e.g. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1), OR use explicit refs: {{ {role}: {first_ref} }}":
    "роль {role!r}: неоднозначность — компонентов на цепи {net!r}: {count}{cluster_hint}{note}: {refs}. Решения: задайте anchor_sheet и/или anchor_cluster (если назначены в схеме), ИЛИ выделите нужный экземпляр на плате перед запуском, ИЛИ разделите роли по именам цепей в схеме (например, DAC_PI_3V3_C1 против DAC_PI_AVDD_C1), ИЛИ используйте явные refs: {{ {role}: {first_ref} }}",
    "net‑based mapping failed ({name!r})": "сопоставление по цепям не удалось ({name!r})",
    "[{name}] mapped by nets: {count} roles": "[{name}] сопоставлено по цепям: {count} ролей",
    "{label}: anchor_role {role!r} not found on any component on the board":
    "{label}: anchor_role {role!r} не найден ни на одном компоненте на плате",
    "{label}: anchor_role {role!r} is ambiguous": "{label}: anchor_role {role!r} неоднозначен",
    "candidates: {count} — {refs}. Solutions: refine anchor_sheet and/or anchor_cluster, OR select the desired instance on the board before running, OR use explicit anchor_ref instead of anchor_role: {first_ref!r}":
    "кандидатов: {count} — {refs}. Решения: уточните anchor_sheet и/или anchor_cluster, ИЛИ выделите нужный экземпляр на плате перед запуском, ИЛИ используйте явный anchor_ref вместо anchor_role: {first_ref!r}",
    " (cluster={cluster})": " (кластер={cluster})",
    "\nCell (pad {pad}) requires role {role!r}, but the pool for net {net!r} does not know this role at all (check the list of roles passed when building the pool).":
    "\nШаблон (пад {pad}) требует роль {role!r}, но пул для цепи {net!r} вообще не знает этой роли (проверьте список ролей, переданный при построении пула).",
    "rule (net {net!r}): anchor_point {point!r} has no footprint":
    "правило (цепь {net!r}): у anchor_point {point!r} нет футпринта",
    "Spoke on pad {pad}: cell {cell!r} not found in cells, spoke skipped":
    "Спица на паде {pad}: шаблон {cell!r} не найден в cells, спица пропущена",
    "{anchor} has no pad {pad}, spoke skipped": "у {anchor} нет пада {pad}, спица пропущена",
    "  spoke‑level via (pad {pad}): ({x:.3f}, {y:.3f}) mm, net={net}":
    "  via уровня спицы (пад {pad}): ({x:.3f}, {y:.3f}) мм, цепь={net}",
    "  spoke‑level track (pad {pad}): ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, layer={layer}":
    "  трек уровня спицы (пад {pad}): ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) мм, цепь={net}, слой={layer}",
    "  {ref} (role {role}, pad {pad}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°":
    "  {ref} (роль {role}, пад {pad}): позиция ({x:.3f}, {y:.3f}) мм, угол {angle:.1f}°",
    "    via {ref}: ({x:.3f}, {y:.3f}) mm, net={net}": "    via {ref}: ({x:.3f}, {y:.3f}) мм, цепь={net}",
    "anchor_point cycle at {name!r}": "цикл anchor_point у {name!r}",
    "  {ref}: already in place, move skipped (skip_existing_components)":
    "  {ref}: уже на месте, перемещение пропущено (skip_existing_components)",
    "[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by current selection":
    "[{label}] роль {role_str!r}: {count} кандидатов сужено до {narrowed} по текущему выделению",
    " (closest to anchor {name!r}: {ref} at {d:.2f} mm, second — {d2:.2f} mm)":
    " (ближайший к якорю {name!r}: {ref} на {d:.2f} мм, второй — {d2:.2f} мм)",
    "[{name}] role {role!r}: {count} candidates narrowed to 1 by physical proximity to anchor ({ref}, {d:.2f} mm, second closest — {d2:.2f} mm, sufficient gap)":
    "[{name}] роль {role!r}: {count} кандидатов сужено до 1 по физической близости к якорю ({ref}, {d:.2f} мм, второй ближайший — {d2:.2f} мм, зазор достаточен)",
    "[{name}] role {role!r}: cannot narrow by proximity — {d:.2f} mm vs {d2:.2f} mm, insufficient gap":
    "[{name}] роль {role!r}: нельзя сузить по близости — {d:.2f} мм против {d2:.2f} мм, зазор недостаточен",
    "{label}: neither anchor_ref nor anchor_role set": "{label}: не заданы ни anchor_ref, ни anchor_role",
    "  via for {owner}: already exists, skipped": "  via для {owner}: уже существует, пропуск",
    "No thermal_via_arrays entries — thermal planning skipped":
    "Нет записей thermal_via_arrays — планирование термо-via пропущено",
    "plan_vias completed: {count} vias": "plan_vias завершено: via: {count}",
    "Planning thermal vias for {ref}, pad {pad}": "Планирование термо-via для {ref}, пад {pad}",
    "Thermal pad: {ref} has no pad {pad}": "Термопад: у {ref} нет пада {pad}",
    "Keepout for {name!r}: {count} rectangles": "Keepout для {name!r}: прямоугольников: {count}",
    "Thermal via: no free spot found for ({x:.3f}, {y:.3f}) mm, point skipped":
    "Термо-via: свободное место не найдено для ({x:.3f}, {y:.3f}) мм, точка пропущена",
    "Skipped {count} thermal vias already present on the board":
    "Пропущено термо-via, уже присутствующих на плате: {count}",
    "Planned {count} thermal vias on {pad}": "Запланировано термо-via на {pad}: {count}",
    "Generated: {path}": "Сгенерировано: {path}",
    "Total spokes: {count}": "Всего спиц: {count}",
}

T.update(T2)

# Third batch: multi-line msgids whose exact leading/trailing \n differ from
# the first-pass keys — keyed precisely against the live .po text.
T3 = {
    "\nChecked footprints: {count}, fields per component: {fields}":
        "\nПроверено футпринтов: {count}, полей на компонент: {fields}",
    "Selected components: {count}\n": "Выделено компонентов: {count}\n",
    "\n{w:.3f} x {h:.3f} mm": "\n{w:.3f} x {h:.3f} мм",
    "Taking id from {file}: {id}\n\n": "Беру id из {file}: {id}\n\n",
    "Via {id} deleted.\n": "Via {id} удалена.\n",
    "\nDone. Via created, id={id}": "\nГотово. Via создана, id={id}",
    "id saved to {file} — to delete it, just run:\n  python -m kicadstamp.diagnostics.test_create_one_via --remove":
        "id сохранён в {file} — чтобы удалить её, просто выполните:\n  python -m kicadstamp.diagnostics.test_create_one_via --remove",
    "=== Test: flip component {ref}, timeout={timeout} ms ===":
        "=== Тест: флип компонента {ref}, таймаут={timeout} мс ===",
    "\nBefore flip: {desc}\n": "\nДо флипа: {desc}\n",
    "=== Test: move {ref} by {delta:+.2f} mm along X, timeout={timeout} ms ===":
        "=== Тест: перемещение {ref} на {delta:+.2f} мм по X, таймаут={timeout} мс ===",
    "\nCurrent position of {ref}: ({x:.3f}, {y:.3f}) mm":
        "\nТекущая позиция {ref}: ({x:.3f}, {y:.3f}) мм",
    "New position:            ({x:.3f}, {y:.3f}) mm":
        "Новая позиция:            ({x:.3f}, {y:.3f}) мм",
    "\nDone. {ref} moved by {delta:+.2f} mm along X.\n":
        "\nГотово. {ref} перемещён на {delta:+.2f} мм по X.\n",
    "\n=== Initial state of {ref} ===\n": "\n=== Исходное состояние {ref} ===\n",
    "local offset of pad {pad}: ({x:.3f}, {y:.3f}) mm":
        "локальное смещение пада {pad}: ({x:.3f}, {y:.3f}) мм",
    "\nPredicted: ({x:.3f}, {y:.3f}) mm": "\nПредсказано: ({x:.3f}, {y:.3f}) мм",
}
T.update(T3)

# Fourth batch: the three refdes/symbol-mismatch strings added in the
# refdes/symbol guard task (re-extracted by update_i18n after the git revert).
T4 = {
    "Skipped mismatches": "Пропущено рассинхронов",
    "{count} ref(s) are refdes/symbol mismatches: the schematic and the board disagree on what that refdes is (different symbol UUIDs). Re-annotate the schematic / Update PCB from Schematic to re-sync, then Apply again.":
        "{count} поз. — рассинхрон refdes/символ: схема и плата расходятся в том, что такое этот refdes (разные UUID символов). Переаннотируйте схему / Update PCB from Schematic для повторной синхронизации и нажмите Apply снова.",
    "{count} ref(s) skipped: the schematic and the board disagree on what that refdes is (different symbol UUIDs). Only verified matches are applied.":
        "{count} поз. пропущено: схема и плата расходятся в том, что такое этот refdes (разные UUID символов). Применены только проверенные совпадения.",
}
T.update(T4)

# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def unescape(block):
    """Decode the quoted-string blocks of a msgid/msgstr: handle \\n, \\t,
    \\", \\\\ and other backslash escapes the same way msgfmt does."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    out = []
    for p in parts:
        s = ""
        i = 0
        while i < len(p):
            c = p[i]
            if c == "\\" and i + 1 < len(p):
                nxt = p[i + 1]
                if nxt == "n":
                    s += "\n"
                elif nxt == "t":
                    s += "\t"
                elif nxt == '"':
                    s += '"'
                elif nxt == "\\":
                    s += "\\"
                else:
                    s += nxt
                i += 2
            else:
                s += c
                i += 1
        out.append(s)
    return "".join(out)


def quote(s: str) -> str:
    # single-line quoted msgstr with escapes (valid .po)
    return ('"' + s.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t") + '"')


# normalized msgid -> translation (outer newlines stripped; they are re-added
# per-entry from the original msgid at write time)
TN = {k.strip("\n"): v.strip("\n") for k, v in T.items()}

lines = open(PATH, encoding="utf-8").read().split("\n")
i = 0
changed = 0
uncovered = []
while i < len(lines):
    line = lines[i]
    if not line.startswith("msgid "):
        i += 1
        continue
    # collect msgid block
    msgid_raw = line[6:].strip()
    j = i + 1
    while j < len(lines) and lines[j].startswith('"'):
        msgid_raw += lines[j].strip()
        j += 1
    # msgstr block follows
    if j >= len(lines) or not lines[j].startswith("msgstr "):
        i = j
        continue
    msgstr_raw = lines[j][7:].strip()
    k = j + 1
    while k < len(lines) and lines[k].startswith('"'):
        msgstr_raw += lines[k].strip()
        k += 1
    msgid = unescape(msgid_raw)
    msgstr = unescape(msgstr_raw)
    if msgid == "":
        # the .po header entry (msgid "") — never translated
        i = k
        continue
    # determine if this entry needs attention: a real "#, fuzzy" flag above,
    # or an empty msgstr. Other flag lines (#, python-format, #, python-brace-
    # format, ...) must NOT trigger a rewrite — those entries already carry a
    # translation and are out of scope.
    fuzzy_flag = (i > 0 and lines[i - 1].startswith("#, ")
                  and "fuzzy" in lines[i - 1])
    needs = fuzzy_flag or (msgstr == "")
    if not needs:
        i = k
        continue
    # Normalized lookup: strip outer newlines from both the file's msgid and
    # the dict keys, then re-attach the msgid's exact leading/trailing \n to
    # the translation. This makes multi-line diagnostic strings match even
    # when the .po wraps/places \n slightly differently than the dict key.
    core = msgid.strip("\n")
    lead_nl = len(msgid) - len(msgid.lstrip("\n"))
    trail_nl = len(msgid) - len(msgid.rstrip("\n"))
    new_str = TN.get(core)
    if new_str is not None:
        new_str = "\n" * lead_nl + new_str + "\n" * trail_nl
    else:
        uncovered.append(msgid)
        i = k
        continue
    # remove the fuzzy flag line (keep other flags)
    if fuzzy_flag:
        flags = [x.strip() for x in lines[i - 1][2:].split(",")]
        other = [f for f in flags if f != "fuzzy"]
        if other:
            lines[i - 1] = "#, " + ", ".join(other)
        else:
            # drop the flag line entirely
            lines[i - 1] = ""
    # replace msgstr block
    replacement = ["msgstr " + quote(new_str)]
    lines[j:k] = replacement
    changed += 1
    # fix indices: the msgstr block is now 1 line
    i = j + 1

if uncovered:
    print("UNCOVERED (%d):" % len(uncovered))
    with open("/tmp/uncovered.txt", "w", encoding="utf-8") as f:
        for u in uncovered:
            f.write(u + "\n")
    for u in uncovered:
        print("  -", u)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write("\n".join(lines))
print("done. changed entries:", changed)
