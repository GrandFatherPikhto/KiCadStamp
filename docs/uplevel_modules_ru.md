# Модули верхнего уровня KiCadStamp (актуальная версия)

В папке `kicadstamp/` находятся основные модули, обеспечивающие загрузку конфигурации, обработку исключений, логирование, откат операций, валидацию, реестры расстановки via и треков, извлечение шаблонов, скриптовые хелперы и точку входа CLI. Каждый модуль решает конкретную задачу и взаимодействует с остальными через чёткие интерфейсы.

---

## 1. `kicadstamp_cli.py` — точка входа (CLI, диспетчер)

**Назначение:**  
Тонкий диспетчер, который парсит аргументы командной строки и делегирует выполнение соответствующему обработчику команд. Логика команд `apply`, `extract`, `undo`, `clone-extract` вынесена в отдельные модули для тестируемости и поддержки.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `cmd_apply(args)` | Делегирует `apply_pipeline.cmd_apply()` — загружает конфиг, подключается к KiCad, выполняет валидацию, планирование и трёхфазное исполнение. |
| `cmd_extract(args)` | Делегирует `cli_extract.cmd_extract()` — извлекает шаблон из текущего выделения на плате. |
| `cmd_undo(args)` | Делегирует `undo.undo_last_operation()` — восстанавливает состояние платы до последней расстановки. |
| `cmd_clone_extract(args)` | Делегирует `cloner.extract.extract_channel()` — файловый клонер. |
| `main()` | Парсит аргументы, настраивает логирование через `logging_setup.setup_logging()`, вызывает соответствующую команду, перехватывает исключения. |

**Ключевые зависимости:**  
`apply_pipeline.cmd_apply`, `cli_extract.cmd_extract`, `undo.undo_last_operation`, `logging_setup.setup_logging`, `cloner.extract.extract_channel`.

---

## 2. `apply_pipeline.py` — Логика команды apply

**Назначение:**  
Содержит класс `ApplyPipeline` и точку входа `cmd_apply()`. Реализует полный workflow `apply`: загрузка конфига, применение CLI-фильтров (`--only`, `--cluster`), подключение к KiCad, валидация, разрешение порядка выполнения, планирование перемещений/via/треков и трёхфазное исполнение.

**Основные классы и функции:**

| Класс/Функция | Описание |
|---------------|----------|
| `ApplyPipeline` | Главный оркестратор: `load_config` → `filter_config` → `connect_adapter` → `resolve_order` → `dry_run`/`execute`. |
| `cmd_apply(args, cfg, ctx)` | Точка входа; создаёт `ApplyPipeline`, вызывает `run()`. |
| `drop_disabled_rules(cfg)` | Удаляет `enabled: false` элементы из конфига. |
| `drop_inactive_items(cfg)` | Удаляет `active: false` элементы (ортогонально `enabled`). |
| `apply_only_filter(cfg, only_names)` | Сужает конфиг до именованных правил/клонов/термовиа. |
| `apply_cluster_filter(cfg, cluster_paths)` | Сужает конфиг по пути кластера. |

**Ключевые зависимости:**  
`config.load_config`, `kicad.adapter.KiCadBoardAdapter`, `validation.run_all_checks`, `placement.dependency_order.resolve_execution_order`, `placement.planner.PlacementPlanner`, `placement.executor.BatchExecutor`, `registry.PlacementRegistry`, `registry.TrackRegistry`.

**Особенности:**  
- Трёхфазное исполнение: перемещения → refresh → via → треки.
- Поддержка `--dry-run`, `--only`, `--cluster`.
- Композиция CLI-фильтров: `--only` и `--cluster` работают как AND.

---

## 3. `cli_extract.py` — Логика команды extract

**Назначение:**  
Содержит `cmd_extract()` — реализацию команды `extract`, выделенную из монолитного CLI для тестируемости.

**Основная функция:**

| Функция | Описание |
|---------|----------|
| `cmd_extract(args)` | Загружает профиль/конфиг, вызывает `template_extraction.extract_template_from_selection()`, записывает результат в JSON, s-expr (`.sexp`) или YAML (по расширению выходного файла). |

**Ключевые зависимости:**  
`template_extraction.extract_template_from_selection`, `config.load_config`, `config.includes.load_profile`.

---

## 4. `logging_setup.py` — Настройка логирования

**Назначение:**
Содержит `setup_logging()` — настраивает уровень логирования (INFO/DEBUG), вывод в консоль и/или файл. Выделена из монолитного CLI для переиспользования скриптами и тестами.

С 2026-08-15 корневой логгер несёт только дешёвый `QueueHandler`; всё реальное
форматирование/запись выполняет ОДИН выделенный поток `QueueListener` (см.
`techdocs/handoff/plan_2026_08_15_queue_based_logging.md`). Поток, который логирует, делает только
`queue.put()` — он физически не может заблокироваться на локе хендлера, захваченном другим потоком,
застрявшим внутри `emit()` (например, зависший `close()` у `pynng` в GC-финализаторе, который раньше
вешал весь GUI). `setup_logging()` возвращает запущенный listener, и ВЫЗЫВАЮЩИЙ обязан его
остановить (GUI: `QApplication.aboutToQuit`; CLI: `finally`).

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `setup_logging(verbose, log_file)` | Настраивает логирование: уровень, консольный обработчик, опциональный файловый обработчик; возвращает запущенный `QueueListener`. |
| `get_log_listener()` | `QueueListener`, запущенный последним вызовом `setup_logging()`, или `None` (например, в юнит-тестах) — позволяет динамическим потребителям вроде `LogDock` цеплять свои хендлеры к listener'у. |

---

## 5. `runtime_context.py` — Контекст выполнения

**Назначение:**  
Определяет датакласс `RuntimeContext`, который переносит состояние прогона (имена листов и т.д.) через конвейер.

**Основной датакласс:**

| Класс | Описание |
|-------|----------|
| `RuntimeContext` | Содержит `sheet_names: Dict[str, str]` — отображение UUID листов в имена. |

---

## 6. `sheet_names.py` — Парсинг UUID листов

**Назначение:**  
Парсит `.kicad_sch` файлы для построения отображения UUID листов в их имена. Используется резолвером ролей для разрешения неоднозначности компонентов на разных листах иерархии.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `build_sheet_name_map(config_path, schematic_dir, adapter)` | Читает иерархию схемы, возвращает `{uuid: имя_листа}`. |
| `resolve_sheet_path_names(fp, sheet_names)` | Возвращает путь листа для футпринта как список имён. |

---

## 7. `i18n.py` — Интернационализация

**Назначение:**  
Настраивает gettext для русскоязычных пользовательских сообщений. Использует домен перевода `kicadstamp`. Предоставляет функцию `_()`, используемую во всём коде.

**Основные элементы:**

| Элемент | Описание |
|---------|----------|
| `_()` | Функция перевода gettext — оборачивает пользовательские строки для русской локализации. |
| `setup_i18n()` | Инициализирует gettext с путём к locale и доменом. |

**Используется в:** Всех модулях, формирующих пользовательские сообщения (42 файла по состоянию на июль 2026).

---

## 8. `author.py` — Скриптовые хелперы

**Назначение:**
Предоставляет вспомогательные функции для написания скриптов расстановки (Python-код вместо YAML-конфигов). Включает функции дампа и `apply_config()` для программного применения сгенерированных конфигов. Стандартная обёртка CLI-точки входа `cli_main()` (`--apply`/`--dry-run`) вынесена в `author_cli.py` (арх-рефакторинг 2026-08-11), чтобы этот модуль оставался чистой библиотекой.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `dump_clone_placements(clones, path)` | Сериализует список `ClonePlacement` в YAML, обрезая значения по умолчанию. |
| `dump_rules(rules, path)` | Сериализует список `Rule` в YAML, обрезая значения по умолчанию. |
| `dump_template(template_dict, path)` | Записывает словарь шаблона в JSON или YAML. |
| `apply_config(cfg, config_path, *, dry_run, ...)` | Загружает конфиг и запускает `cmd_apply` программно. |
| `cli_main(build_fn, output_path, ...)` | Стандартное тело `if __name__ == "__main__":` для скриптов расстановки — **теперь в `author_cli.py`** (вынесен 2026-08-11). |

---

## 9. `explore.py` — Запросы к плате только на чтение

**Назначение:**  
Предоставляет read-only вспомогательные функции для инспекции состояния платы. Используется в диагностических скриптах и интерактивном исследовании.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `get_footprints_by_role(adapter, role)` | Находит все футпринты с заданным значением поля `Role`. |
| `get_footprint_field(adapter, ref, field_name)` | Читает значение конкретного поля футпринта. |

---

## 10. `config/` Пакет — загрузка и хранение конфигурации

**Назначение:**  
Заменил старый монолитный `config.py`. Теперь это пакет с отдельными модулями для моделей, загрузки и включений.

**Состав пакета:**

| Модуль | Описание |
|--------|----------|
| `__init__.py` | Экспортирует все типы конфига и `load_config()`. |
| `models.py` | Датаклассы: `Config`, `SpokeTemplate`, `ManualSpoke`, `ClonePlacement`, `Rule`, `TemplateVia`, `TemplateTrack`, `TemplateComponentSlot`, `ThermalViaArrayConfig`. |
| `loader.py` | `load_config()` и вспомогательные функции `_load_*` для каждой секции конфига. Обрабатывает проверки уникальности ролей. |
| `includes.py` | Обрабатывает директивы `include:` — загружает и объединяет конфиги из нескольких файлов с обнаружением циклов и проверкой дубликатов ключей. |

**Основные датаклассы:**

| Класс | Описание |
|-------|----------|
| `ThermalViaArrayConfig` | Настройки массива термовиа под термопадом (с `anchor_ref` вместо `target_ref`). |
| `TemplateVia` | Описание via в шаблоне (локальные координаты, цепь, размеры). |
| `TemplateTrack` | Описание прямого отрезка дорожки в шаблоне: начальная и конечная точки (локальные), ширина, цепь, опциональный слой. |
| `TemplateComponentSlot` | Слот компонента в шаблоне: роль, локальные координаты, угол, список via, опциональный `net_template` и `layer`. |
| `SpokeTemplate` | Полный шаблон спицы: имя, список via, список треков, список слотов компонентов, абсолютный `layer`. |
| `ManualSpoke` | Конкретная спица: пад, шаблон, сдвиг, поворот, флаги `enabled`, `active`. |
| `Rule` | Правило для одной цепи: имя цепи, список спиц, `anchor_ref` (обязательное), флаг `active`. |
| `ClonePlacement` | Клонируемое размещение: имя, шаблон, абсолютная точка или сдвиг от якоря, угол, словари `nets`, `params`, `net_overrides`, `layer`, `mirror`, `refs`, `by_selection`, `anchor_role`, `anchor_sheet`, `anchor_pad`. |
| `Config` | Главный объект: глобальный `layer`, шаблоны, термовиа, правила, клонирования, флаги. |

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `load_config(path)` | Читает YAML, разрешает `include:` (мёржит внешние `cells:`/`rules:`/и т.д. из других файлов). Парсит все секции, возвращает `Config` и `RuntimeContext`. |
| `_load_template_via(data)` | Загружает `TemplateVia`. Проверяет, что `net` — строка. |
| `_load_template_track(data)` | Загружает `TemplateTrack`. Проверяет, что `net` — строка. |
| `_load_template_component_slot(data)` | Загружает `TemplateComponentSlot`. |
| `_load_spoke_template(name, data)` | Загружает `SpokeTemplate` с проверкой уникальности ролей. |
| `_load_manual_spoke(data)` | Загружает `ManualSpoke`. |
| `_load_clone_placement(data)` | Загружает `ClonePlacement`. Проверяет ограничения на якоря и координаты. |

**Особенности:**  
- **`include:`** — множество файлов конфига с объединением и обнаружением циклов, включая внешние файлы `cells:` (обёрнутые в ключ `cells:` — `cells_file:`/`cell_files:`, отдельный более старый механизм, были слиты в `include:` 2026-08-02).
- Проверка уникальности ролей внутри шаблона.
- `net_template` для клонирования (плейсхолдеры для цепей).
- Два режима сопоставления ролей: «по выделению» и «по цепям».
- Перекрёстная валидация `layer`/`mirror`.
- Устаревшие поля `target_ref` и `side` — фатальная ошибка.

---

## 11. `exceptions.py` — иерархия исключений

**Назначение:**  
Определяет пользовательские исключения для проекта и единую функцию форматирования фатальных ошибок. Все исключения наследуются от базового `PlacerError`.

**Классы исключений:**

| Класс | Назначение |
|-------|------------|
| `PlacerError` | Базовое исключение для всех ошибок планера. |
| `BoardNotFoundError` | Не удалось получить плату из KiCad. |
| `ComponentNotFoundError` | Компонент не найден на плате. |
| `GeometryError` | Ошибка в геометрических расчётах. |
| `ValidationError` | Фатальная ошибка предварительной проверки — программа останавливается до изменения платы. |

**Вспомогательная функция:**

| Функция | Описание |
|---------|----------|
| `format_fatal_error(title, problems)` | Форматирует список проблем в единое многострочное сообщение с рамкой из `=`. Используется в `config/loader.py` и `validation.py`. Живёт здесь, чтобы избежать циклических импортов. |

---

## 12. `net_resolution.py` — разрешение цепей для клонируемых шаблонов

**Назначение:**  
Обеспечивает трёхслойное разрешение имени цепи для `ClonePlacement`. Позволяет подставлять плейсхолдеры из `params` и применять переопределения `net_overrides`. Также предоставляет обратную параметризацию (`parametrize_net`) для `extract`.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `resolve_net(net_template, params, net_overrides)` | Принимает шаблон имени цепи (возможно с `{placeholder}`), словарь параметров и словарь переопределений. Возвращает итоговое имя цепи. |
| `parametrize_net(literal_net, net_template_map, params)` | Обратная операция для `extract`: восстанавливает паттерн с плейсхолдером из реального имени цепи. |

**Используется в:** `placement/services/clone_role_resolver.py` и `geometry/clone_geometry.py`.

---

## 12a. `net_matching.py` + `net_derive.py` — автоматическая координата сети (Фаза 0)

**Назначение:** фундамент «сеть — вычисляемая функция»
(`plan_2026_08_28_auto_nets_full_automation.md`, Фаза 0). Два ЧИСТЫХ модуля —
без adapter, без YAML, без живой платы:

- `net_matching.py` — соответствие Role↔Net между кластером-шаблоном и
  кластером-целью при фиксированной биекции ролей: WL-раскраска до
  неподвижной точки, одно глобальное паросочетание (Кун), доказательство
  единственности через Tarjan SCC по «swap»-графу. Настоящая неоднозначность
  возникает ровно там, где роль физически симметрична (`symmetric_roles` —
  продакшн-аналог разделения `net_template_pad` vs
  `net_template_same_as_role`). По доказанному safe-default КАЖДЫЙ элемент
  неоднозначной SCC — валидный ответ, поэтому построенное Куном паросочетание
  — валидный автоматический ответ, а отчёт SCC — диагностический слой, а не
  жёсткий стоп для человека. Ошибки — `ValidationError` через
  `format_fatal_error` (никакого голого `RuntimeError`, никакой тихой догадки).
- `net_derive.py` — `derive_role_nets()` превращает роли целла + данные об
  известной/исходной сети в `{role: NetDerivation(net, source)}` по трём
  приоритетам: `live_pad` (сеть уже известна на цели) → `prefix_remap`
  (иерархический `/Channel_0/` → `/Channel_1/`, семантика `TwinMap.twin_net`)
  → `kuhn`/`kuhn_scc_group`. Значение provenance позволяет диагностике Фазы 2
  сказать, ОТКУДА взялась каждая сеть. Глобальные сети намеренно вне скоупа
  (отложены в мини-дизайн Фазы 2).

**Основные точки входа:**

| Функция | Описание |
|----------|-------------|
| `match_template_to_target(template, target)` | Возвращает `(mapping, ambiguous_groups)` — глобальное паросочетание Куна + формальное доказательство неоднозначности (SCC-группы), либо `ValidationError` при неизоморфности. |
| `derive_role_nets(roles, role_source_nets, ...)` | `{role: NetDerivation}` по правилу трёх приоритетов; роли без применимого приоритета отсутствуют (fallback вызывающего). |

**Планируемые потребители (Фаза 2):** авто-вывод ролей→сетей в плейсменте
(`resolve_roles_by_nets` без ручных `nets:`), авто `net_from_role` в экстракте,
и сетевая часть переноса трассировки между симметричными кластерами.

---

## 13. `registry.py` — реестры расстановки via и треков

**Назначение:**  
Обеспечивает идемпотентность расстановки via и треков между прогонами. Сохраняет информацию о созданных объектах в JSON-файлы. При повторном запуске сверяет запланированные объекты с реальными объектами на плате, удаляет устаревшие (prune) и создаёт только новые или изменившие параметры.

**Основные классы и функции:**

| Класс/Функция | Описание |
|---------------|----------|
| `make_registry_key(anchor_id, template_name, role, via_index)` | Генерирует составной ключ для реестра via. |
| `registry_path_for_config(config_path)` | Возвращает путь к файлу реестра via. |
| `track_registry_path_for_config(config_path)` | Возвращает путь к файлу реестра треков. |
| `RegistryEntry` | Dataclass для via: UUID, позиция, цепь, параметры отверстия. |
| `TrackRegistryEntry` | Dataclass для трека: UUID, координаты, ширина, цепь, слой. |
| `PlacementRegistry` | Класс, управляющий реестром via. |
| `TrackRegistry` | Класс, управляющий реестром треков. |

**Особенности:**
- **Сверка с живыми объектами на плате** — источник истины, а не только JSON.
- Ключи реестра: `anchor_id|template_name|role|via_index` (для треков аналогично).
- Допуск на позицию: 0.01 мм.
- Отдельные реестры для via и треков.

**Используется в:** `apply_pipeline.py` (при `apply`), `executor/via_executor.py`, `executor/track_executor.py`.

---

## 14. `template_extraction.py` — извлечение шаблона из выделения

**Назначение:**  
Реализует логику команды `extract`: из текущего выделения в PCB-редакторе KiCad извлекает шаблон спицы (компоненты, via и треки) и формирует структуру для записи в файл.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `extract_template_from_selection(adapter, name, params, net_template_map, ...)` | Основная функция. Читает выделение, фильтрует треки/via по замыканию связных компонент (оставляются только те, чья компонента достигает пада КЕПТ-футпринта), проверяет роли, вычисляет origin, формирует выходной словарь. |
| `render_uncertain_comments(yaml_text, name)` | Добавляет YAML-комментарии, помечающие неопределённые значения геометрии. |

**Используется в:** `cli_extract.py` (команда `extract`).

---

## 15. `undo.py` — откат последней операции

**Назначение:**  
Реализует команду `undo`. Использует JSON-логи, создаваемые `executor/operation_logger.py`.

**Основная функция:**

| Функция | Описание |
|---------|----------|
| `undo_last_operation(json_path)` | Восстанавливает состояние платы: возвращает компоненты на исходные позиции/слой, удаляет созданные via и треки. |

**Используется в:** `kicadstamp_cli.py` (команда `undo`).

---

## 16. `validation.py` — предварительные проверки конфигурации

**Назначение:**  
Выполняет фатальные проверки конфигурации **до** любых изменений на плате. Собирает все проблемы, а не останавливается на первой.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `check_templates_and_pads_exist(adapter, cfg)` | Проверяет, что каждая включённая спица ссылается на существующий шаблон и пад. |
| `check_role_pool_sufficiency(adapter, cfg)` | Проверяет доступность компонентов по ролям. |
| `check_clone_templates_exist(cfg)` | Проверка существования шаблонов (только конфиг). |
| `check_no_duplicate_clone_anchors(cfg)` | Уникальность имён и физических якорей клонов. |
| `check_anchor_sheet_configured(cfg, sheet_names)` | Проверяет `anchor_sheet` на соответствие реальным именам листов. |
| `check_clone_nets_exist_on_board(adapter, cfg)` | Резолвит цепи via/треков и сверяет с реальными цепями платы. |
| `check_single_selection_based_clone(cfg)` | Гарантирует не более одного клона в режиме «по выделению». |
| `run_all_checks(adapter, cfg, sheet_names)` | Запускает все проверки по порядку. |

**Используется в:** `apply_pipeline.py` (перед планированием).

---

## 17. `constants.py` — глобальные константы

**Назначение:**  
Содержит глобальные константы, используемые в различных модулях проекта.

| Константа | Значение | Использование |
|-----------|----------|---------------|
| `ROLE_FIELD_NAME` | `"Role"` | Имя пользовательского поля для ролей (используется в `component_pool.py`, `template_extraction.py`, `clone_role_resolver.py`). |
| `CLUSTER_FIELD_NAME` | `"Cluster"` | Имя пользовательского поля для путей кластера. |
| `POSITION_TOLERANCE_NM` | `10_000` (0.01 мм) | Допуск по позиции для проверки «уже на месте». |
| `ANGLE_TOLERANCE_DEG` | `0.1` | Допуск по углу для проверки «уже на месте». |
| `POSITION_TOLERANCE_MM` | `0.01` | Допуск по позиции для реестра. |
| `DEFAULT_BATCH_SIZE` | `10` | Размер батча по умолчанию. |
| `DEFAULT_TIMEOUT_MS` | `20000` | Таймаут IPC по умолчанию. |
| `DEFAULT_LOG_DIR` | `"logs"` | Папка для логов по умолчанию. |
| `SPOKE_LEVEL_ROLE_PLACEHOLDER` | `"__spoke__"` | Плейсхолдер для via уровня спицы в реестре. |

---

## 18. `utils/file_cache.py` — кэши чтения (одиночный файл + уровень графа)

**Назначение:**
Мемоизирует `open()+parse` одного файла по ключу `(resolved path, mtime_ns)` — изменение mtime (обычно внешняя правка вручную) само по себе является промахом кэша, и все «сырые» читатели делят одну запись кэша на файл. Убирает избыточность чтения при старте GUI: одно конструирование `MainWindow()` раньше заново парсило один и тот же граф `include:` из YAML-файлов ~13 раз и те же файлы `.kicad_sch` по 4 раза каждый (замерено: конструирование 15.0s → ~1.1s, `yaml.safe_load` 113 → ~8 вызовов на реальном проекте — см. `techdocs/handoff/plan_2026_08_15_config_read_cache_startup.md`).

Второй слой, `cached_graph_result` (2026-08-21), мемоизирует РЕЗУЛЬТАТ вычисления всего графа (`load_config()`, `walk_include_tree()`) по ключу `(kind, resolved_root_path)` плюс mtime всех файлов, затронутых этим вычислением: при повторном вызове с неизменными файлами он перепроверяет горстку `os.stat()` и возвращает deep copy без повторного обхода/слияния/валидации. Профилирование в тот же день выявило и НАСТОЯЩЕЕ оставшееся узкое место старта: `gui/yaml_io.load_data()` был единственным «сырым» читателем, который кэш 2026-08-15 не покрывал, поэтому `RootMetadataDock` заново парсил корневой YAML вне кэша прямо перед тем, как каждый док перечитывал его через кэш (~1.9s избыточного парсинга). Маршрутизация его через `cached_file_read` сократила конструирование `MainWindow()` с ~6.0s до ~4.1s (`yaml.safe_load` 4 → 2 вызова) — см. `techdocs/handoff/deepseek/plan_2026_08_21_startup_graph_level_cache.md`.

**Основные функции:**

| Функция | Описание |
|----------|-------------|
| `cached_file_read(path, loader)` | Возвращает deep copy результата `loader(path)`, кэшируя по `(resolved path, mtime_ns)`; отсутствующий файл не кэшируется (loader обрабатывает его напрямую). |
| `invalidate_path(path)` | Сбрасывает все поколения кэша для `path` — обязателен к вызову писателем сразу после физической записи (один только mtime не различает две записи, попавшие в один тик таймера). |
| `cached_graph_result(kind, root_path, compute_fn)` | Мемоизирует одно вычисление всего графа по `(kind, root)` + набору mtime всех затронутых файлов; повторный вызов перепроверяет эти mtime и возвращает deep copy без повторного запуска `compute_fn`. |
| `invalidate_graph_path(path)` | Сбрасывает все результаты уровня графа, в чей набор файлов входит `path` — аналог `invalidate_path()` для кэша графа, вызывается из той же единственной точки записи. |

**Используется в:** `config/includes.py`, `config/loader.py` (обе оборачивают свои точки входа в `cached_graph_result`), `config_writer.py` (чтение и единственная точка записи, которая сбрасывает оба слоя), `sheet_names.py`, `gui/yaml_io.py`.

---

## Взаимосвязи модулей

```mermaid
graph TD
    CLI[kicadstamp_cli.py] --> ApplyPipe[apply_pipeline.py]
    CLI --> CliExtract[cli_extract.py]
    CLI --> Undo[undo.py]
    CLI --> LogSetup[logging_setup.py]
    CLI --> Cloner[cloner/extract.py]

    ApplyPipe --> ConfigPkg[config/ package]
    ApplyPipe --> Adapter[kicad/adapter.py]
    ApplyPipe --> Validation[validation.py]
    ApplyPipe --> Order[placement/dependency_order.py]
    ApplyPipe --> Planner[placement/planner.py]
    ApplyPipe --> Executor[placement/executor/batch_executor.py]
    ApplyPipe --> ViaRegistry[registry.PlacementRegistry]
    ApplyPipe --> TrackRegistry[registry.TrackRegistry]
    ApplyPipe --> NetResolution[net_resolution.py]
    ApplyPipe --> Constants[constants.py]
    ApplyPipe --> SheetNames[sheet_names.py]

    CliExtract --> ConfigPkg
    CliExtract --> Extract[template_extraction.py]
    CliExtract --> Adapter
    CliExtract --> NetResolution

    ConfigPkg --> Exceptions[exceptions.py]
    ConfigPkg --> Models[config/models.py]
    ConfigPkg --> Loader[config/loader.py]
    ConfigPkg --> Includes[config/includes.py: include: (external cells:/rules:/etc.)]

    Validation --> ConfigPkg
    Validation --> ComponentPool[placement/services/component_pool.py]
    Validation --> Exceptions
    Validation --> Adapter

    ViaRegistry --> ConfigPkg
    ViaRegistry --> Adapter
    ViaRegistry --> Exceptions

    TrackRegistry --> ConfigPkg
    TrackRegistry --> Adapter
    TrackRegistry --> Exceptions

    Extract --> Adapter
    Extract --> ConfigPkg
    Extract --> Exceptions

    Undo --> Adapter
    Undo --> Exceptions

    NetResolution --> Exceptions
    NetResolution --> ConfigPkg (используется ClonePlacement)
    NetResolution --> Extract (parametrize_net)

    Order --> Adapter
    Order --> ConfigPkg
```

Каждый модуль решает свою задачу и взаимодействует с другими через чётко определённые интерфейсы, что обеспечивает модульность и тестируемость. Благодаря централизованным константам, единому форматтеру ошибок и поддержке внешних файлов шаблонов проект легко поддерживать и расширять.
