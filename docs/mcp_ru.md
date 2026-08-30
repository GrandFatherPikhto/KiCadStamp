# MCP-сервер для KiCadStamp

## Назначение

KiCadStamp содержит **MCP-сервер (Model Context Protocol)** поверх **stdio**,
чтобы Claude Code и другие MCP-клиенты могли видеть, что происходит на живой
плате KiCad, и воздействовать на неё:

- **чтение** — идентичность платы, футпринты (с Role/Cluster), текущее выделение
  в PCB-редакторе, сети платы;
- **validated write** — применение конфига расстановки через тот же validated-
  конвейер, что CLI `apply` / GUI Redraw (board identity, FORK-1, «никогда не
  гадать молча», registry, dependency order);
- **raw write** (опционально) — прямые перемещения через kipy в обход конфиг-слоя.

Сервер — тонкий протокольный слой поверх существующего
[`KiCadBoardAdapter`](kicad_ru.md) и apply-конвейера; логику расстановки он не
повторяет.

## Установка

```bash
pip install -e ".[mcp]"
```

Устанавливает SDK `mcp` и консольную команду `kicadstamp-mcp`. Зависимость MCP
опциональная — для GUI/CLI/тестов она не нужна.

## Регистрация

Сервер работает на stdio и запускается MCP-клиентом. Два способа:

1. **Таб «Настройки» клиента (основной)** — добавить MCP-сервер с командой
   `kicadstamp-mcp` (или `.venv/bin/python -m mcp_server.server` из корня
   репозитория). Это персональная настройка клиента.
2. **`.mcp.json` в репозитории** (автоподхват клиентами, которые его
   поддерживают) — закоммиченный файл в корне уже указывает на
   `.venv/bin/python -m mcp_server.server`. **Примечание для Windows:** этот
   путь Linux-специфичен (`.venv/bin/python`); на Windows регистрируйте сервер
   через таб «Настройки» клиента с `.venv\Scripts\python.exe -m
   mcp_server.server` (или консольной командой `kicadstamp-mcp`).

## Инструменты (первая итерация)

| Инструмент | Риск | Что делает |
|---|---|---|
| `kicadstamp_get_board_identity` | низкий | Имя платы + версия KiCad |
| `kicadstamp_list_footprints` | низкий | ref, Role/Cluster, позиция (мм), поворот, слой; опция `ref_prefix` |
| `kicadstamp_get_footprint` | низкий | Футпринт детально: pads (номер/нет/позиция) и сети на них |
| `kicadstamp_get_selection` | низкий | Что сейчас выделено в PCB-редакторе (с раскрытием групп) |
| `kicadstamp_list_nets` | низкий | Все сети платы |
| `kicadstamp_get_items_by_uuid` | низкий | Детализация объектов платы по uuid (tracks/vias/footprints); каждый запрошенный uuid встречается ровно один раз, отсутствующие — `found: false` |
| `kicadstamp_list_tracks` | низкий | Сегменты дорожек с опциями `net`/`layer` (например `net='GND'`); на больших платах предпочитай фильтры или `get_items_by_uuid` |
| `kicadstamp_list_vias` | низкий | Via с опцией `net` (например `net='GND'`); на больших платах предпочитай фильтр или `get_items_by_uuid` |
| `kicadstamp_apply_config` | низкий (validated) | Прогон существующего validated-конвейера по профилю `.sexp`/`.json`; `dry_run` — только план |
| `kicad_raw_move_footprint` | **высокий (raw)** | Перемещение одного футпринта по ref напрямую через kipy; выключен по умолчанию; требует `expected_board_name` (обязательный guard идентичности платы) |

Имена и описания инструментов — только английские (машинный интерфейс);
сообщения сервера в логах и результаты следуют двуязычной gettext-системе
проекта.

## Модель безопасности

- **Validated** инструменты (чтение + `apply_config`) доступны всегда и проходят
  всю защиту проекта (`run_all_checks`, `check_board_identity`, registry,
  dependency order).
- **Raw** инструменты **выключены по умолчанию**. Регистрируются только при
  включении — либо в **табе «Настройки» GUI** (группа «MCP server», сохраняется
  в `gui_state.json`), либо переменной окружения `KICADSTAMP_MCP_ALLOW_RAW_WRITE=1`
  (env-флаг приоритетнее).
- Перед каждой сырой записью выполняется **guard идентичности платы**
  (`check_board_identity`) — инструмент требует параметр `expected_board_name`
  (обязательный, не опциональный) и отказывает в записи, если открыта другая
  плата; подключённая плата всегда сообщается. Raw-путь не является дырой в
  защите, которая уже есть у apply.
- Сервер **не добавляет** собственный слой подтверждения: риск raw-инструмента
  честно указан в описании, а решение принимает permission-гейт хоста.

## Конфигурация

Единственная настройка сервера — **гейт raw-write**: чекбокс «Allow raw MCP
write tools» в табе «Настройки» GUI или env-флаг выше. Отдельного конфиг-файла
сервера нет — остальное передаётся аргументами при каждом вызове (например,
`config_path` для `apply_config`).

## Архитектура

```
mcp_server/
├── server.py       # MCPServer (mcp SDK), stdio, регистрация инструментов, гейт raw
├── tools.py        # Pydantic-схемы + тонкие обёртки; конвертация в ToolError
├── handlers.py     # SDK-free логика поверх адаптера / run_apply
└── connection.py   # один KiCadBoardAdapter на процесс: lazy connect, lock,
                    # reconnect при дропе, close на shutdown
```

`handlers.py` и `connection.py` не импортируют MCP SDK; юнит-тесты гоняют их с
фейковым адаптером (без живого KiCad). Сам stdio-транспорт проверяется вручную
на тестовой плате.

## См. также

- [`docs/kicad_ru.md`](kicad_ru.md) — слой адаптера `kicad/`, которым управляет сервер.
- Дизайн-документ: `techdocs/handoff/deepseek/design_2026_08_29_kicad_mcp_server.md`.
