# Сквозные договоры — читать ДО задания и ДО приёмки

Claude, 05.10.2026, по слову Дениса («может нам в map декларативно занести?»). Повод: задание Р2 «Разноса» не
назвало дверь, и Р2 полезла к плате с UI-потока. Здесь — договоры, которые действуют в КАЖДОЙ задаче, независимо от
того, назвало их задание или нет. Одна строка суть + где держится в коде + сторож + канон. Подробности — в каноне;
расходится с кодом — прав код, сводку поправить.

Отличие от `map.md` §2.1: там «у правила один хозяин-функция»; здесь — «как вообще можно делать X» (порядок,
запреты, кто сторожит).

## Плата

| договор | суть | где в коде | сторож | канон |
|---|---|---|---|---|
| **Дверь** | GUI берёт плату ТОЛЬКО через `connection.board`; хендл не кэшировать в доке, `board`/адаптер по цепочке в обход не передавать | геттер `BoardConnection.board` (`gui/connection.py`) | `tests/repo/test_board_access_door.py` (никто не лезет в `adapter._board`, и строкой через `getattr` тоже); `tests/repo/test_board_door_guard.py` (страж на геттере) | `docs/board_door.md` (+ `_ru`); `techdocs/me/door.md`; `deepseek.md` п.31, `claude.md` п.15 |
| **UI-поток: порядок средств** | 1) `connection.is_connected` — не `connection.board is not None`; 2) `connection.snapshot` (пустой снимок — не снимок: `if snapshot:`); 3) воркер `start_long_op`; 4) знак `with ui_thread_board_read(reason=…)` — последним и с замером в коде | `gui/connection.py` (`is_connected`, `snapshot`, `ui_thread_board_read`); `gui/worker.py` (`start_long_op`) | страж на геттере: стенд — `raise`, бой — `log` (`kicadstamp/gui_main.py` `set_ui_thread_predicate(is_ui_thread, refusal="log")`), строка ERROR раз на место за сессию; **статический перепис — `tools/door_lint.py`** (кандидаты `.board` вне воркера и без знака, храповик) | `door.md`; п.31 / п.15 |
| **Сторож новой фичи — на настоящей двери** | клетка «эта фича не читает плату с UI-потока» пишется на НАСТОЯЩЕМ `BoardConnection` с предикатом в режиме `raise` (фикстура `ui_thread` в `test_board_door_guard.py`); на подделке без двери — пустышка | — | — | п.31 / п.15 |
| **`raise` в бою нельзя** | исключение внутри Qt-слота роняет PyQt6 (`EXIT=134`, измерено дважды); вооружённый GUI вживую НЕ запускать | `gui_main.py` | — | `door.md` |
| **Один владелец сокета** | всё по общему сокету — через `start_long_op`; прямое обращение спрашивает `socket_busy(connection)` и отказывается / откладывается (`defer_while_socket_busy`, одна попытка), отказ не молчит | `gui/worker.py` (`socket_busy` — ЕДИНСТВЕННОЕ определение «занято», `defer_while_socket_busy`, `LongOpController`) | — | `door.md` «Как сериализован сокет» |
| **Свой сокет — свой `finally`** | операция, строящая свой адаптер (конвейер, перерисовка, CLI), закрывает его на обоих путях | `kicadstamp/adapter_factory.py` `create_board_adapter` | — | `door.md` п.4 |
| **Таймаут воркера** | из `worker_timeout_ms(connection)`, не константой | `gui/connection.py` | `tests/repo/test_timeout_sweep.py` | `door.md` п.5 |
| **Плата — методами адаптера** | новый вид чтения — метод на шов `IBoardAdapter` + реализация | `kicadstamp/kicad/interfaces.py` `IBoardAdapter` | `test_board_access_door.py` | `door.md` п.1 |
| **Заслонка «разнесено»** | пока кластеры разнесены, `start_long_op` без `allowed_while_exploded=True` не запускает операцию (красная строка); признак — из журнала, не из памяти | `gui/worker.py` `set_long_op_gate`; `gui/explode_guard.py` | `tests/gui/test_explode_worker_gate.py` | `design_2026_10_05_explode_cluster.md` РЗ7 (Р2/Р2а — в работе) |

## Конфиг, реестр, формат

| договор | суть | где в коде | сторож | канон |
|---|---|---|---|---|
| **Один писатель конфига** | любая запись конфига — `write_config_file`; писатель берёт словарь через `read_data`, который отдаёт КОПИЮ — править ОБЩИЙ объект кэша чтения нельзя (штанп читает тот же кэш как «предыдущую версию файла») | `kicadstamp/config_writer.py` (`write_config_file`, `_read_data`) | `tests/config/test_config_format_version.py` `test_every_config_write_goes_through_the_one_config_writer`; `tests/config/test_writer_reads_a_private_dict.py` (клетка-воспроизведение) | `map.md` §2.1; `plan_2026_10_15_reread_copper_uuid_and_selection_any.md` (Ч1-коммит 1) |
| **Формат 3: личность = UUID** | у каждой записи `uuid`; ключ реестра несёт его (`record_key_part`); отката к имени нет (фатал) | `kicadstamp/registry.py` `record_key_part`, `make_registry_key` | клетки формата 3 | `structures.md`; `plan_2026_10_05_uuid_tails.md` |
| **Правка записи — с её uuid** | «подменить запись по имени» теряет uuid — нельзя. РЕДАКТИРУЕМЫЙ путь (форма / «Перерисовать»): личность подменяемой записи — `form_identity.identify` (свой uuid + ссылочные `*_uuid`); пути КОПИРОВАНИЯ (перечитывание меди, Extract tree, `write_net_trace`): `form_identity.keep_identity` (ключи хранения со старого словаря) + `replace_entry` (найти по идентичности, заменить, иначе добавить); путь СОХРАНЕНИЯ формы — `upsert_list_entry` (штамп наследует uuid по полному имени) | `kicadstamp/config/form_identity.py` (`identify`, `keep_identity`, `replace_entry`); `kicadstamp/config_writer.py` `upsert_list_entry` | `tests/config/test_form_identity.py`, `tests/config/test_writer_reads_a_private_dict.py`, `tests/config/test_net_trace_write_keeps_uuid.py`, `tests/gui/docks/test_reread_net_trace_identity.py`, `tests/gui/docks/test_form_identity_redraw.py` | `plan_2026_10_05_uuid_tails.md` ч.0; `plan_2026_10_15_reread_copper_uuid_and_selection_any.md` (Ч1-коммит 2) |
| **Запись формата 3 обязана иметь имя — и на ЗАПИСИ** | безымянная запись не грузится (П2); штанп проверяет ТОЙ ЖЕ функцией, что загрузчик, и отказывает ВСЕЙ записи — файл не тронут; имя сохраняющему даёт личность секции (для `net_traces` — сеть: единственная запись сети отдаёт своё имя, иначе `generate_trace_name`, несколько записей → отказ «какую обновить») | `kicadstamp/config/format3.py` `check_record_has_a_name` (загрузчик + `stamp_format3`); `kicadstamp/net_trace_extract.py` `_name_for_format3`, `NetTraceNetAmbiguous`; `gui/docks/net_trace.py` (форма) | `tests/net/test_net_trace_write_names_the_record.py`, `tests/gui/docks/test_rename_save_in_place.py` | там же, Ч1-коммит 3 |
| **Ключ реестра строит одна функция** | ячейка — `record_key_part(cell_name, cell.uuid)`; `net_traces` — `net_trace_registry_key(nt, i)`; в подделках тестов ключи — ТОЙ ЖЕ функцией, не руками | `registry.py`, `net_trace_planner.py`, `explode.py` `cell_identity` | оснастки Р1а (X19) | `map.md` §2.1 |
| **Своя / чужая медь** | только `is_own_key` | `kicadstamp/selection_narrowing.py` | — | `map.md` §2.1 |
| **Перерисовка забирает ничью медь по геометрии** | `adopt_net_trace_copper` / `adopt_matching_unowned` до `reconcile`, на каждом прогоне — поэтому отпущенный ключ безопасен | `kicadstamp/apply_pipeline.py` | — | Р3 «Разноса» |

## Слои и файлы

| договор | суть | где | сторож | канон |
|---|---|---|---|---|
| **Ядро без Qt и без `gui/`** | `kicadstamp/` не импортирует `gui/` (зависимость только `gui → kicadstamp`); низкий слой (`gui/worker.py`) не импортирует верхний — связь хуком (`set_busy_reporter`, `set_long_op_gate`) | — | **сторожа нет** (см. `index.md` — граф импортов) | — |
| **Файл ≤ 800 строк** | новое в гиганта не дописывается — новый модуль; в гиганте — только проводка | — | сторож — этап А0а (не введён) | `plan_2026_10_04_roadmap_trees_and_formats.md` Д8 |
| **Без окон-отказов** | отказ = красная строка в Лог; в открытой форме — надпись в форме; разрешены только `QMessageBox.question` (подтверждения, выход) | — | сторож — этап А0б (не введён) | там же, А0б |
| **i18n** | msgid — английский; `en` msgstr пустой (фолбэк), `ru` — перевод; каталоги точечно, НЕ полный `pybabel update`; docs EN + RU (`*.md` / `*_ru.md`) | `kicadstamp/i18n.py` | `tests/repo/test_i18n_import_guard.py`, сторожа каталогов | `deepseek.md` п.9–10, `claude.md` п.16–17, `techdocs/me/i18troubles.md` |
| **Состояние машины — вне `profiles/`** | журналы, восстановление — `~/.local/state/kicadstamp/…` (Windows `%LOCALAPPDATA%`); `profiles/` и `techdocs/` возит Syncthing между машинами | `kicadstamp/explode_journal.py` `journal_dir` | клетка журнала Р1 | дизайн «Разноса» РЗ5 |

## Алгоритмы — не изобретать заново

Денис 05.10: «иногда Демон забывает про этот алгоритм, в итоге — уйма переделок». Задача задевает порядок
расстановки, сети целевого кластера или связность меди — ВЗЯТЬ готовое, не писать свой цикл / свою эвристику.

| договор | суть | где в коде | сторож | канон |
|---|---|---|---|---|
| **Порядок «кто от кого зависит» — алгоритм Кана** | новая зависимость «родитель раньше ребёнка» = новый именованный `EdgeProvider` в список; НИКОГДА новый цикл Кана и новый ключ сортировки; связь знает своего поставщика — цикл называет виновника | `kicadstamp/order_pass.py` `run_order_pass` (деревья, `tree_position`) | `tests/pipeline/test_order_pass.py` | докстринг `order_pass.py`; `plan_2026_09_17_order_pass_and_component_node` |
| **Ещё два Кана (под вопросом — копии?)** | уровни выполнения apply — `kicadstamp/placement/dependency_order.py` `resolve_execution_order`; каскад «перерисовать зависимых» — `kicadstamp/anchor_graph.py` `topological_order`. Новое — только через `order_pass`; сведение трёх в один — отдельной задачей | см. слева | `tests/placement/test_dependency_order.py`, `tests/trees/test_anchor_graph.py` | — |
| **Сети целевого кластера — вычисляются, не угадываются** | `derive_role_nets`: приоритет 1 — живой пад на цели (`live_pad`); 2 — перенос иерархического префикса (`/Channel_0/X` → `/Channel_1/X`, `prefix_remap`); 3 — **Кун** (`kuhn` / `kuhn_scc_group`). Роль без применимого приоритета — ОТСУТСТВУЕТ в ответе, решает вызывающий; молчаливой догадки нет. Глобальные сети (GND/VCC…) здесь не выводятся. У каждого ответа — происхождение | `kicadstamp/net_derive.py` `derive_role_nets`; зовёт `placement/services/clone_role_resolver.py` — сейчас только с приоритетом 1 (`live_pad_nets`) | `tests/net/test_net_derive.py` | докстринг `net_derive.py`; `techdocs/net_resolving/plan/arch/plan_2026_08_28_auto_nets_full_automation.md` |
| **Кун: шаблонная сеть ↔ сеть цели** | глобально, не жадно: Вайсфейлер–Леман до неподвижной точки сужает кандидатов → Кун (дополняющие пути) — ОДНО совершенное паросочетание на весь граф → Тарьян (SCC) доказывает единственность; SCC > 1 = группа неразличимых сетей, ЛЮБОЙ член — верный ответ (безопасное умолчание). Симметричные двухвыводные R/C — пины как неупорядоченное множество. **Кун сопоставляет СЕТИ и никогда не выбирает ЭКЗЕМПЛЯР детали** — одинаковые кандидаты различает каскад расстановки (`clone_role_resolver`, debug-строка «net-matching (Kuhn) is never applied to instance selection») | `kicadstamp/net_matching.py` `match_template_to_target`; зовёт `kicadstamp/cloner/plan.py` (файловый клонер); в `derive_role_nets` — вход `kuhn_mapping` (приоритет 3), в расстановке пока не подаётся | `tests/cloner/test_net_matching.py` | докстринг `net_matching.py` |
| **Связность меди на ПЛАТЕ — по форме** | дорожка — капсула (ширина), переходное — диск (диаметр, сквозное), дорожка–дорожка только на своём слое; ловит концы в нескольких мкм и Т-стык; пад — по площадке в его осях. Точное совпадение точек — НЕ связность (урок Р1б) | `kicadstamp/geometry/copper_connect.py`, `PadArea.segment_touches`; граф — `kicadstamp/explode_connectivity.py` | `tests/geometry/test_copper_connect.py`, `tests/explode/` | `plan_2026_10_05_explode_r1_core.md` Р1б |
| **Связность меди ЯЧЕЙКИ в конфиге (под вопросом)** | `cell_copper_components` сшивает только совпавшие концы (`eps` 1e-3 мм) в осях ячейки — Т-стык не видит; для сохранённых точных координат это работает, но правило другое, чем на плате | `kicadstamp/geometry/cell_copper_connectivity.py`; зовёт `kicadstamp/validation.py` | — | — |

## Расхождения, найденные при сведении (05.10)

- `door.md` ссылается на `diagnostics/live_ui_thread_offenders.py` (живой перепис UI-поточных чтений в offscreen) —
  **в репозитории его нет и в истории git нет**. Статический перепис теперь — `tools/door_lint.py`.
- `door.md` «Как сериализован сокет»: `socket_busy` в `gui/worker.py:166` — сейчас `:235` (номера уехали, суть та же).
