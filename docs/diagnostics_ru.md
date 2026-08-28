# `kicadstamp/diagnostics/` – Диагностические скрипты

## Назначение

Директория `kicadstamp/diagnostics/` содержит набор диагностических и отладочных скриптов, которые помогают разработчикам и продвинутым пользователям проверять работу **KiCadStamp**, отлаживать конфигурации, анализировать геометрию и тестировать отдельные операции IPC. Скрипты используют актуальный API `kicadstamp` (адаптер, геометрию, конфигурацию) и не зависят от устаревших модулей.

Все скрипты требуют **открытого экземпляра KiCad** с активной платой и выполняются из корня проекта через `python -m`.

---

## Структура

Все диагностические скрипты живут в едином пространстве имён `kicadstamp/diagnostics/`
(включая зонды, ранее разбросанные по верхнеуровневой папке `diagnostics/`).
Дерево ниже помечает требование каждого скрипта к KiCad:

- `[LIVE]` — требуется запущенный KiCad с открытой платой.
- `[LIVE+WRITE]` — также пишет / мутирует плату.
- `[FILES]` — читает только локальные файлы, без IPC.

```
kicadstamp/diagnostics/
├── diagnose_first_write_crash.py  # Диагностика краша KiCad на первой IPC-записи (issue #24966) [LIVE]
├── diagnostic_charset.py          # Поиск не-ASCII символов (гомоглифов) в Role/Cluster по всей плате [LIVE]
├── diagnostic_keepout.py          # Анализ keepout и пересечений [LIVE]
├── get_pad_bbox.py                # Bounding box пада [LIVE]
├── get_selected_component.py      # Детальная информация о выделенных компонентах [LIVE]
├── get_selection.py               # Список выделенных объектов [LIVE]
├── test_create_one_via.py         # Создание одной via [LIVE+WRITE]
├── test_custom_fields.py          # Проверка чтения поля Role [LIVE]
├── test_flip_one_cap.py           # Проверка флипа одного компонента [LIVE+WRITE]
├── test_move_one_cap.py           # Проверка перемещения одного компонента [LIVE+WRITE]
├── test_pad_mirror_convention.py  # Проверка конвенции зеркалирования пада [LIVE]
├── diagnose_points.py             # Грубый зонд для kipy-типа "Points" [LIVE]
├── group_by_sheet_path.py         # Группировка компонентов по цепочке sheet_path UUID [LIVE]
├── kipy_uuild_resolver.py         # Список всех цепей с подключёнными refdes [LIVE]
├── local_net_ierarchy.py          # Дампит все локальные (иерархические) имена цепей [LIVE]
├── netlist_resolver.py            # Глубокий дамп атрибутов fp.sheet_path [LIVE]
├── probe_footprints_fields.py     # Чтение/запись кастомных полей на размещённом футпринте [LIVE+WRITE]
├── probe_kicad_sch_uuids.py       # Двухшаговый UUID-мост vs *.kicad_sch [FILES / LIVE шаг 2]
├── probe_path_minus_last.py       # Группировка sheet_path.path[:-1] vs {uuid: Sheetname} [LIVE]
├── probe_pi_filter_ambiguity.py   # Role/Cluster/sheet-path/цепи для refdes (неоднозначность) [LIVE]
├── probe_sheet_path_truncation.py # Группировка path[:-1]/path[1:] vs пути локальных цепей [LIVE]
├── probe_uuid_stability.py        # Переживает ли fp.id.value переаннотацию? snapshot+compare [LIVE snapshot / FILES compare]
├── probe_uuid_to_sheet_name.py    # {цепочка UUID -> человекочитаемый путь} из локальных цепей [LIVE]
├── recon_symbol_uuid_bridge.py    # UUID-мост символа: схема vs sheet_path платы (разведка) [FILES / LIVE опционально]
├── resolve_paths.py               # Человекочитаемые пути листов из .net-файла [LIVE]
├── role_resolver.py               # Сырой proto-дамп sheet_path [LIVE]
├── test_ierarchy.py               # Футпринты vs карта листов схемы [LIVE]
├── test_ierarchy_uuid.py          # Сырая форма sheet_path.path [LIVE]
├── test_sheet_path.py             # path_human_readable на живой плате [LIVE]
└── unersolved_components.py       # Поканальный разбор (Channel_0/1/2) по цепям [LIVE]
```

### Конвенция шапки

Каждый скрипт в этой директории открывается модульным docstring'ом, в котором в указанном порядке указывается:

- **Input** — что нужно скрипту (аргументы, путь конфига, живая плата, ...).
- **Expected** — что он печатает / проверяет / пишет.
- **Live KiCad** — требуется ли запущенный KiCad с открытой платой
  (`Yes`), только для части запуска (`Partially`) или не требуется вовсе (`No`).
- **Run** — каноническая команда `python -m kicadstamp.diagnostics.<скрипт> ...`.

Поле `Live KiCad` — авторитетный пофайловый маркер live-only зондов;
дерево структуры выше использует ту же легенду (`[LIVE]` / `[LIVE+WRITE]` / `[FILES]`).

---

## Описание скриптов

### `diagnose_first_write_crash.py`

Диагностика краша KiCad на первой IPC-записи (issue #24966). Полное описание, гипотезы H1-H3, параметры,
вывод и зависимости вынесены в отдельный документ, т.к. это единственный скрипт из этого набора, завязанный
на конкретный заведённый баг с целым отдельным воркфлоу охоты: **[diagnose_first_write_crash_ru.md](diagnose_first_write_crash_ru.md)**.
Описание обоих связанных багов (#24966/#24970) и остальные инструменты охоты — в
[crash_hunting_ru.md](crash_hunting_ru.md).

```bash
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # только чтения, безопасно
python -m kicadstamp.diagnostics.diagnose_first_write_crash             # полный тест, может уронить KiCad
```

---

### `diagnostic_charset.py`

**Назначение:**
Проходит по всем футпринтам платы (по умолчанию — поля, заданные константами `ROLE_FIELD_NAME` и `CLUSTER_FIELD_NAME`, т.е. `"Role"` и `"Cluster"`, список настраивается через
`--fields`) и ищет символы вне печатной ASCII (`0x20`–`0x7E`). Повод для появления скрипта — живая находка на
`3CH-AWG-TIA`: у трёх компонентов (`C3`, `C9`, `C170`) в значении `Role` первая буква оказалась
кириллической «С» (`U+0421`) вместо латинской «C» (`U+0043`) — судя по всему, раскладка клавиатуры
соскочила на русскую в момент набора значения поля в Eeschema Bulk Edit. Визуально буквы неотличимы почти в
любом шрифте, но `component_pool.py`/`clone_role_resolver.py` сравнивают `Role` строгим посимвольным
равенством — компонент с такой опечаткой не находит ни одно правило, которое ищет «правильную» (латинскую)
роль, и диагностировать это глазами практически невозможно.

**Использование:**
```bash
# Проверить ROLE_FIELD_NAME и CLUSTER_FIELD_NAME на всей плате (по умолчанию)
python -m kicadstamp.diagnostics.diagnostic_charset

# Проверить другой набор полей
python -m kicadstamp.diagnostics.diagnostic_charset --fields "Role,Cluster,Value"

# Печатать и чистые поля тоже (не только найденные проблемы)
python -m kicadstamp.diagnostics.diagnostic_charset --verbose
```

**Параметры:**
- `--fields` – список полей через запятую, без пробелов (по умолчанию `ROLE_FIELD_NAME,CLUSTER_FIELD_NAME`, т.е. `"Role,Cluster"`).
- `--timeout-ms` – таймаут IPC (по умолчанию `20000`).
- `--verbose` – логировать и «чистые» (без находок) поля тоже.

**Вывод:**  
Список находок: refdes, имя поля, значение целиком, и для каждого «плохого» символа — позиция в строке,
сам символ, кодпоинт (`U+XXXX`) и его имя по Unicode (`unicodedata.name`). Код возврата — `0`, если проблем
не найдено, `1` — если найдено хотя бы одно поле (удобно как самостоятельный шаг перед `apply` или в CI:
`python -m kicadstamp.diagnostics.diagnostic_charset || echo "есть подозрительные символы в Role/Cluster"`).

**Зависимости:**  
`kicadstamp.kicad.adapter.KiCadBoardAdapter` (`get_footprints`/`get_field_value`), `unicodedata` из
стандартной библиотеки.

---

### `diagnostic_keepout.py`

**Назначение:**  
Загружает конфиг, планирует расстановку, строит keepout из падов IC и компонентов, затем проверяет, попадают ли в keepout позиции компонентов и via. Выводит подробную информацию для отладки.

**Использование:**
```bash
python -m kicadstamp.diagnostics.diagnostic_keepout <config.sexp>
```

**Вывод:**
- Список прямоугольников keepout с координатами.
- Для каждого компонента – статус (INSIDE/CLEAR).
- Для каждой via (спицевой и компонентной) – статус.

**Зависимости:**  
`kicadstamp.config`, `kicadstamp.kicad.adapter`, `kicadstamp.placement.planner`, `kicadstamp.geometry.keepout`.

---

### `get_pad_bbox.py`

**Назначение:**  
Выводит bounding box пада (размер, позиция) и размер медного слоя (если доступен). Полезно для проверки геометрии падов.

**Использование:**
```bash
python -m kicadstamp.diagnostics.get_pad_bbox --ref IC1 --pad 17 --verbose
```

**Параметры:**
- `--ref` – refdes компонента (по умолчанию `IC1`).
- `--pad` – номер пада (если не указан, показывает все).
- `--timeout` – таймаут IPC (мс).
- `--verbose` – подробный вывод.

**Вывод:**
- Размер bbox (мм).
- Позиция bbox.
- Размер медного слоя (если доступен).

**Зависимости:**  
`kicadstamp.kicad.adapter`, `kicadstamp.geometry.thermal_grid`.

---

### `get_selected_component.py`

**Назначение:**  
Выводит детальную информацию о выделенных компонентах: refdes, номинал, футпринт, позиция, угол, размер (bbox), список падов (номера, цепи, позиции, размеры) и поле `Role`. Корректно обрабатывает группы (Group).

**Использование:**  
Выделите компоненты в PCB-редакторе, затем выполните:
```bash
python -m kicadstamp.diagnostics.get_selected_component
```

**Вывод:**  
Таблица с информацией о каждом компоненте и его падах.

**Зависимости:**  
`kicadstamp.kicad.adapter` (использует `get_selected_items`).

---

### `get_selection.py`

**Назначение:**  
Простой диагностический скрипт, выводящий список всех выделенных объектов (футпринты, пады, треки, via) с их типами и основными параметрами.

**Использование:**  
Выделите объекты в PCB-редакторе, выполните:
```bash
python -m kicadstamp.diagnostics.get_selection
```

**Вывод:**  
Список объектов с типом и ключевыми свойствами.

**Зависимости:**  
`kicadstamp.kicad.adapter` (использует `get_selected_items`).

---

### `test_create_one_via.py`

**Назначение:**  
Создаёт одну via рядом с указанным компонентом. Сохраняет UUID созданной via в файл `.last_test_via.json` для последующего удаления. Позволяет проверить работу `create_items` и транзакций.

**Использование:**
```bash
# Создать via
python -m kicadstamp.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Удалить последнюю созданную via
python -m kicadstamp.diagnostics.test_create_one_via --remove

# Удалить конкретную via по UUID
python -m kicadstamp.diagnostics.test_create_one_via --remove <uuid>
```

**Параметры:**
- `--offset-mm` – смещение от центра компонента (мм).
- `--net` – цепь via (по умолчанию `GND`).
- `--drill-mm` – диаметр сверла.
- `--diameter-mm` – внешний диаметр.
- `--timeout-ms` – таймаут IPC.

**Зависимости:**  
`kicadstamp.kicad.adapter`.

---

### `test_custom_fields.py`

**Назначение:**  
Проверяет чтение пользовательского поля компонента через IPC. Выводит все тексты и поля (`Field`) компонента, а затем ищет поле с заданным именем (по умолчанию `Role`). Это критично для проверки работы ролей.

**Использование:**
```bash
python -m kicadstamp.diagnostics.test_custom_fields C5 --field Role
```

**Параметры:**
- `--field` – имя поля для поиска (по умолчанию `Role`).
- `--timeout-ms` – таймаут IPC.
- `--verbose` – подробный вывод.

**Вывод:**  
- Список всех полей и текстов компонента.
- Значение запрошенного поля (или сообщение, что оно не найдено).

**Зависимости:**  
`kicadstamp.kicad.adapter` (использует `get_field_value`).

---

### `test_flip_one_cap.py`

**Назначение:**  
Проверяет «настоящий» флип компонента через GUI-действие `pcbnew.InteractiveEdit.flip`. Выводит состояние компонента до и после флипа. Позволяет убедиться, что флип работает корректно (слой и зеркалирование).

**Использование:**
```bash
python -m kicadstamp.diagnostics.test_flip_one_cap C6
```

**Параметры:**
- `--timeout-ms` – таймаут IPC.

**Вывод:**  
Состояние компонента (слой, позиция, угол) до и после флипа.

**Зависимости:**  
`kicadstamp.kicad.adapter` (использует `flip_selected` и `refresh_board`).

---

### `test_move_one_cap.py`

**Назначение:**  
Проверяет перемещение одного компонента на заданное расстояние по оси X. Позволяет изолировать проблемы с транзакциями (зависание `begin_commit`, `update_items`, `push_commit`).

**Использование:**
```bash
# Сдвинуть на +1 мм
python -m kicadstamp.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Вернуть обратно
python -m kicadstamp.diagnostics.test_move_one_cap C5 --revert
```

**Параметры:**
- `--delta-mm` – величина сдвига (мм).
- `--revert` – сдвиг в обратную сторону.
- `--timeout-ms` – таймаут IPC.

**Вывод:**  
Время выполнения каждого шага (подключение, begin_commit, update_items, push_commit) в миллисекундах.

**Зависимости:**  
`kicadstamp.kicad.adapter`.

---

### `test_pad_mirror_convention.py`

**Назначение:**  
Проверяет конвенцию зеркалирования локального смещения пада при флипе (используется в `geometry/pad_projection.py`). Выполняет два шага: поворот на 90° без флипа (проверка базовой формулы), затем флип и сравнение трёх кандидатов (зеркало по X, по Y, без зеркала). Возвращает компонент в исходное состояние.

**Использование:**
```bash
python -m kicadstamp.diagnostics.test_pad_mirror_convention C6 --pad 2
```

**Параметры:**
- `--pad` – номер пада для отслеживания (по умолчанию `2`).
- `--timeout-ms` – таймаут IPC.

**Вывод:**  
- Расхождение базовой формулы после поворота.
- Расстояния для трёх кандидатов после флипа.
- Победитель (зеркало по X, по Y или без зеркала).

**Зависимости:**  
`kicadstamp.kicad.adapter`, `kicadstamp.geometry.pad_projection` (вспомогательно).

---

### `probe_uuid_stability.py`

**Назначение:**  
Проверяет, переживает ли собственный UUID футпринта (`fp.id.value`) переаннотацию схемы.
`snapshot` снимает `ref`/`id`/`footprint`/`sheet_path` каждого футпринта платы в JSON; `compare`
сравнивает два снимка офлайн и определяет, изменился ли сам МНОЖЕСТВО UUID между ними — это и есть
настоящий сигнал нестабильности. Переезд refdes на тот же UUID — ожидаемое поведение при
переаннотации, показывается отдельно (с `-v`), не считается расхождением. Эмпирический результат
от 2026-08-07 — в `techdocs/handoff/handoff_2026_08_07_uuid_stability_probe.md`: UUID футпринтов
остались идентичны на двух независимых сценариях полного сброса обозначений на плате из 279
футпринтов.

**Использование:**
```bash
python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_before.json
# ... переаннотация в Eeschema, затем Update PCB from Schematic
#     (Match Method = "Re-associate by UUID/timestamp") ...
python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_after.json
python -m kicadstamp.diagnostics.probe_uuid_stability compare uuid_before.json uuid_after.json -v
```

**Параметры:**
- `snapshot <output>` – путь к выходному JSON (требует живой KiCad).
- `compare <before> <after>` – два ранее снятых JSON-снимка (офлайн, KiCad не нужен).
- `-v`, `--verbose` (только для compare) – дополнительно показать переезды refdes для UUID, которые не изменились.

**Вывод:**  
- `snapshot`: JSON-файл с метаданными снятия (время, версия KiCad, число футпринтов) и списком
  `footprints` (`ref`/`id`/`footprint`/`sheet_path` на каждую запись).
- `compare`: счётчики (до/после/общих/появившихся/пропавших/со сменой refdes), затем блок с UUID,
  присутствующими только в одном из снимков (настоящее расхождение), и, с `-v`, таблица переездов
  refdes для UUID, совпавших в обоих снимках. Код возврата — `1`, если множество UUID изменилось,
  `0` — если нет.

**Зависимости:**
`kipy` (только для `snapshot`); `compare` вообще не зависит от KiCad, только стандартная библиотека `json`/`argparse`.

---

### `recon_symbol_uuid_bridge.py`

**Назначение:**
Разведка для проблемы «Pending Changes сверяет схему и плату чисто по refdes»
([`compute_pending_edits()`](../gui/docks/pending.py) join'ит две стороны по СТРОКЕ refdes).
Проверяет, существует ли UUID-ключ, однозначно идентифицирующий физический экземпляр символа
на **обеих** сторонах: уникален ли `fp.sheet_path.path[-1]` на футпринт, уникален ли **полный**
`sheet_path.path` на экземпляр, и совпадает ли последний элемент пути платы с top-level
`(uuid ...)` блока `(symbol ...)` в `.kicad_sch`.

Эмпирический результат (2026-08-08, `3CH-AWG-TIA`, живая плата): `path[-1]` — это UUID
**мастер-символа**, общий для всех клонов мультиинстанс-листа (66 значений на 2+ refdes), то
есть **не** уникален на футпринт; **полный** `sheet_path.path` уникален на футпринт (364/364);
платный `path[-1]` совпадает с top-level UUID символа схемы (279/279 на сохранённом
`.kicad_pcb`, 358/364 на живой плате). Точный ключ 1:1 — `board path == (schematic
(instances ...) path минус root uuid) + top uuid блока`. Полное описание эксперимента с
числами и дизайн-выводом — в `techdocs/handoff/deepseek/handoff_2026_08_08_symbol_uuid_recon.md`.

**Использование:**
```bash
python -m kicadstamp.diagnostics.recon_symbol_uuid_bridge boards/3CH-AWG-TIA
```

**Параметры:**
- позиционный `project_dir` – директория проекта с `*.kicad_sch` и файлом платы
  `<name>.kicad_pcb` (по умолчанию `boards/3CH-AWG-TIA`).

**Вывод:**
- статистика схемы: блоки с top-level `(uuid ...)`, структура путей `(instances ...)`
  (длины путей, является ли последний элемент UUID листа или символа);
- статистика платы: уникальность полного пути на футпринт, разделяемость `path[-1]` по refdes;
- UUID-мост: сколько платных `path[-1]` являются UUID символов схемы;
- доля полнопутевого join'а платы против карты ключей схемы;
- счётчик рассинхрона refdes (refdes платы против refdes схемы для одного UUID символа —
  ненулевое значение доказывает, что join по строке refdes молча сопоставляет не те компоненты);
- демонстрация per-instance резолюции для мультиинстанс-символов.

**Зависимости:**
`kicadstamp.schematic_blocks.find_balanced_span` (span/regex-парсинг `.kicad_sch` и
`.kicad_pcb`, без round-trip через sexpdata); опционально `kipy` для сверки с живой платой
(сохранённый `.kicad_pcb` уже несёт авторитетный `(path ...)`).

---

## Общие рекомендации

- **Запускайте с `--verbose`** для отладки, если скрипт поддерживает этот флаг.
- **Всегда запускайте из корня проекта** с использованием `python -m kicadstamp.diagnostics.<имя_скрипта>`.
- **Убедитесь, что KiCad открыт** и активна нужная плата — если только в шапке скрипта не указано
  `Live KiCad: No` / `[FILES]` (без живой сессии запускаются только скрипты, читающие локальные
  файлы).
- Для скриптов, работающих с выделением, выделите нужные объекты в PCB-редакторе **перед** запуском.

---

## Примечания

- Скрипты **не изменяют плату** (кроме `test_move_one_cap`, `test_flip_one_cap`, `test_create_one_via` и `probe_footprints_fields`, которые могут её мутировать). Используйте их на тестовых платах или убедитесь, что у вас есть резервная копия.
- `diagnose_first_write_crash.py` плату не мутирует (запись — no-op), но на уязвимой сессии (см. issue
  #24966) сама попытка записи может **уронить процесс KiCad целиком**. Сохраните открытые файлы перед
  запуском полной лесенки (без `--until 8`).
- `test_move_one_cap`, `test_flip_one_cap` и `test_create_one_via` **не используют** реестр расстановки, поэтому они не откатываются командой `undo`.
- Для полной диагностики расстановки рекомендуется запускать `diagnostic_keepout.py` с актуальным конфигом.

---

## Расширение диагностических скриптов

Если вам необходимо добавить новый диагностический скрипт:

1. Разместите его в `kicadstamp/diagnostics/`.
2. Используйте актуальный API `kicadstamp` (адаптер, геометрию, конфигурацию).
3. Задайте конвенцию шапки: `Input` / `Expected` / `Live KiCad` / `Run`.
4. Добавьте описание в этот документ (с маркером `[LIVE]` / `[LIVE+WRITE]` / `[FILES]`).
5. Обеспечьте, чтобы скрипт не изменял плату (или предупреждал об этом), если он не предназначен для мутации.

---

## Лицензия

Диагностические скрипты распространяются под лицензией MIT, так же как и основной проект.