# `tests/` – Модульные и интеграционные тесты KiCadStamp

## Назначение

Директория `tests/` содержит два типа тестов:

1. **Модульные тесты** (unit tests) – выполняются **без подключения к KiCad**, используют моки и проверяют логику модулей: геометрию, конфигурацию, валидацию, реестр, извлечение шаблонов, разрешение цепей, клонирование (`ClonePlacement`), преобразование шаблонов и т.д. Они быстрые, стабильные и запускаются в CI/CD.

2. **Интеграционные тесты** (integration tests) – выполняются **с реальным KiCad** и проверяют сквозную работу через IPC: подключение, создание via и треков, перемещение/флип компонентов, работу реестра, извлечение шаблонов из выделения, клонирование (`ClonePlacement`) в реальном окружении. Они требуют открытого KiCad с активной платой и отмечены маркером `@pytest.mark.integration`, чтобы не запускаться случайно.

---

## Структура

```
tests/
├── conftest.py                       # Общие фикстуры для модульных тестов
├── test_author.py                    # Скриптовые хелперы: prune defaults, dump round‑trip, cli_main
├── test_cli_filters.py               # CLI-фильтры: --only, --cluster, active/drop/inactive логика
├── test_clone_anchor_id.py           # Разрешение anchor ID для клонов
├── test_clone_geometry.py            # Геометрия ClonePlacement (поворот, зеркало, треки)
├── test_clone_ignore_selection.py    # Флаг ignore_selection для клонов
├── test_clone_placement_config.py    # Загрузка ClonePlacement из YAML
├── test_clone_placement_integration.py # Сквозной тест ClonePlacement (моки)
├── test_clone_role_resolver.py       # Разрешение ролей для клонирования (выделение, цепи, близость к якорю)
├── test_clone_selection_conflict.py  # Проверка конфликта нескольких клонов в режиме выделения
├── test_config_includes.py           # Директивы include: объединение, циклы, дубликаты
├── test_dependency_order.py          # Разрешение порядка выполнения по anchor_ref/anchor_role
├── test_execute_vias_owner_ref.py    # Корректность owner_ref в логах (via)
├── test_explore.py                   # Хелперы запросов к плате только на чтение
├── test_full_pipeline_templates.py   # Сквозной тест конвейера (моки) для ManualSpoke
├── test_i18n.py                      # Доступность функции _() и импорт gettext
├── test_kicad.py                     # Проверка адаптера (наличие методов)
├── test_manual_position_calculator.py # Логика ManualPositionCalculator (пулы, позиции)
├── test_naming.py                    # Доступоры name/effective_name, валидация обязательных имён
├── test_net_resolution.py            # Разрешение цепей с плейсхолдерами (net_resolution)
├── test_pad_projection.py            # Предсказание позиции пада
├── test_registry_integration.py      # Полный цикл реестра (создание, обновление, prune) на моках
├── test_registry_pruning_granularity.py # Точность pruning реестра
├── test_registry_rule_protection.py  # Защита реестра через known_anchor_ids
├── test_spoke_layout.py              # Преобразование локальных координат шаблона (spoke_layout)
├── test_template_extraction.py       # Извлечение шаблона из выделения (логика, треки)
├── test_cell_files.py            # устаревшие cells_file/cell_files/templates_file/template_files → фатал
├── test_two_phase_execution.py       # Двухфазное выполнение (moves → refresh → vias) на моках
├── test_undo_layer.py                # Сохранение и восстановление слоя в undo
├── test_unique_roles.py              # Уникальность ролей в шаблоне
├── test_unknown_keys_validation.py   # Проверка check_unknown_keys для секций конфига
├── test_validation.py                # Предварительные проверки конфигурации
│
└── integration_tests/                # Интеграционные тесты с реальным KiCad
    ├── conftest.py                   # Фикстуры для интеграционных тестов
    ├── test_connection.py            # Подключение и базовые операции
    ├── test_via_ops.py               # Создание/удаление via, работа реестра
    ├── test_track_ops.py             # Создание/удаление треков (проверка API)
    ├── test_component_ops.py         # Перемещение/флип компонентов
    ├── test_extract.py               # Извлечение шаблона из выделения
    └── test_registry.py              # Полный цикл реестра с реальным KiCad
```

---

## Фикстуры

### Для модульных тестов (в корневой `conftest.py`)

Обычно модульные тесты используют `unittest.mock` и не требуют внешних ресурсов, поэтому фикстуры минимальны. Однако при необходимости можно добавить:

- `tmp_path` – временная директория (встроенная фикстура pytest).
- Кастомные моки для адаптера и объектов платы.

### Для интеграционных тестов (в `integration_tests/conftest.py`)

Созданы следующие фикстуры для работы с реальным KiCad:

| Фикстура | Область | Описание |
|----------|---------|----------|
| `adapter` | `session` | Один экземпляр `KiCadBoardAdapter` на всю сессию. |
| `board` | `session` | Доска из адаптера. |
| `test_config` | `session` | Загруженный тестовый конфиг из `kicadstamp_templates_example.yaml`. |
| `test_component_ref` | `function` | Refdes компонента для тестов (по умолчанию `C5`). |
| `test_pad_number` | `function` | Номер пада для тестов (по умолчанию `17`). |
| `temp_via` | `function` | Создаёт временную via на GND, удаляет после теста. Возвращает `(via_id, position, net)`. |
| `moved_component` | `function` | Перемещает компонент на 1 мм вправо, возвращает обратно после теста. Возвращает `(ref, original_pos, new_pos)`. |
| `flipped_component` | `function` | Переворачивает компонент на другую сторону, возвращает обратно. Возвращает `(ref, original_layer, target_layer)`. |
| `registry` | `function` | Создаёт временный реестр расстановки в `tmp_path`. |
| `template_extraction` | `function` | Обёртка над `extract_template_from_selection` (для тестов выделения). |

Эти фикстуры обеспечивают изоляцию тестов и автоматическую очистку (удаление via, восстановление позиций и слоёв) после каждого теста.

---

## Запуск тестов

### Модульные тесты (без KiCad)

```bash
# Все модульные тесты
pytest tests/ -v

# Исключить интеграционные тесты (они в отдельной папке, но можно явно)
pytest tests/ -v -m "not integration"

# Конкретный файл
pytest tests/test_spoke_layout.py -v
```

### Интеграционные тесты (с реальным KiCad)

**Важно:** перед запуском убедитесь, что:
- KiCad открыт и активна тестовая плата.
- Компоненты, используемые в тестах (например, `C5`), существуют на плате.
- Тесты не нарушают критичную разводку (они восстанавливают состояние).

```bash
# Все интеграционные тесты
pytest tests/integration_tests/ -v -m integration

# Конкретный файл
pytest tests/integration_tests/test_via_ops.py -v -m integration

# С отключением перехвата вывода для отладки
pytest tests/integration_tests/ -v -s -m integration
```

---

## Описание модульных тестов

| Файл | Что тестирует |
|------|---------------|
| `test_author.py` | Скриптовые хелперы: `_prune_defaults` (удаление полей со значениями по умолчанию), YAML round‑trip для `ClonePlacement`/`Rule`, совместимость `apply_config` с `cmd_apply`, поведение `cli_main`. |
| `test_cli_filters.py` | Логику CLI-фильтров: `--only NAME` (сужение по имени/цепи), `--cluster PATH` (сужение по пути кластера), `drop_disabled_rules`, `drop_inactive_items`, композицию `--only`/`--cluster` (AND), `load_profile` и корневые значения по умолчанию. |
| `test_clone_anchor_id.py` | Разрешение anchor ID для клонов: `clone_anchor_id()` возвращает ключ на основе `anchor_ref`/`anchor_role`/`name`. |
| `test_clone_geometry.py` | Геометрию `ClonePlacement`: преобразование локальных координат в абсолютные, углы компонентов, via и треки, зеркалирование (`mirror`), разрешение цепей через `params` и `net_overrides`. Проверяет фатальность via без `net`. |
| `test_clone_ignore_selection.py` | Флаг `ignore_selection`: временное снятие выделения при обработке клона. |
| `test_clone_placement_config.py` | Загрузку `ClonePlacement` из YAML, проверку полей `name`, `template`, `origin_x_mm`, `origin_y_mm`, `rotation_deg`, `nets`, `params`, `net_overrides`, `enabled`. |
| `test_clone_placement_integration.py` | Сквозной тест `PlacementPlanner` с `ClonePlacement` (моки): совместная работа с `rules` (ManualSpoke) и клонами в одном прогоне, проверка `registry_key` для via. |
| `test_clone_role_resolver.py` | Разрешение ролей для `ClonePlacement` двумя режимами: по выделению (`resolve_roles_by_selection`) и по цепям (`resolve_roles_by_nets`), включая плейсхолдеры, `net_overrides`, обработку неоднозначности и близость к якорю. |
| `test_clone_selection_conflict.py` | Проверку, что в конфиге не более одного `ClonePlacement` в режиме «по выделению» (`check_single_selection_based_clone`), а также работу `clone_uses_selection_mode` с учётом `by_selection`, `nets`, `params`. |
| `test_config_includes.py` | Директиву `include:`: объединение `clone_placements`/`rules`/`templates` из нескольких файлов, обнаружение дубликатов, циклов и алмазов, отключённые include, неподдерживаемые ключи, неверное использование dict/list. |
| `test_dependency_order.py` | Разрешение порядка выполнения: отключённые клоны пропускаются, без зависимостей сохраняется исходный порядок, producer упорядочен перед consumer, self-якорь не цикл, настоящий цикл вызывает `ValidationError`. |
| `test_execute_vias_owner_ref.py` | Корректность `owner_ref` в JSON-логах (каждая via получает свой владелец) и вызов `registry.record_created` с правильным UUID. |
| `test_explore.py` | Хелперы запросов к плате только на чтение: `get_footprints_by_role`, `get_footprint_field`. |
| `test_full_pipeline_templates.py` | Сквозной тест конвейера с шаблонами (моки): расчёт позиций и via для `ManualSpoke`, распределение компонентов по ролям, проверка `registry_key`. |
| `test_i18n.py` | Доступность функции `_()`, настройка gettext, проверка импорта во всех исходных файлах. |
| `test_kicad.py` | Наличие всех методов интерфейса `IBoardAdapter` в `KiCadBoardAdapter`, импорт и конструктор (без реального IPC). |
| `test_manual_position_calculator.py` | Логику `ManualPositionCalculator`: построение пула, расчёт позиций, планирование via для `rules`. |
| `test_naming.py` | Доступоры `rule_effective_name`/`thermal_via_array_effective_name`, загрузка `name:` из YAML, проверка обязательности имён (фатальность при отсутствии и при повторе имени внутри `thermal_via_arrays:`), опциональность Rule.name, значения по умолчанию enabled/active. |
| `test_net_resolution.py` | Разрешение цепей с плейсхолдерами: подстановка из `params`, применение `net_overrides`, ошибки при отсутствии параметров. |
| `test_pad_projection.py` | Предсказание позиции пада после перемещения/поворота (без флипа и с флипом), инвариантность `local_pad_offset` к углу. |
| `test_registry_integration.py` | Полный цикл реестра (создание, обновление, prune) на моках, включая сверку с реальными via. |
| `test_registry_pruning_granularity.py` | Точность pruning реестра: корректное определение устаревших vs. актуальных via/треков. |
| `test_registry_rule_protection.py` | Защиту реестра через `known_anchor_ids`: via/треки клонов не из `--only` не удаляются. |
| `test_spoke_layout.py` | Геометрическое преобразование локальных координат шаблона в глобальные (`spoke_layout`), включая via уровня спицы и компонента, произвольное количество ролей. |
| `test_template_extraction.py` | Извлечение шаблона из выделения: проверка ролей, уникальности, вычисление origin, фильтрация треков/via (замыкание связных компонент, укоренённое на падах КЕПТ-футпринтов), параметризация цепей (`--net-template`), выбор origin по via/роли. |
| `test_cell_files.py` | Устаревшие ключи `cells_file`/`cell_files`/`templates_file`/`template_files` — все фатальны при загрузке с подсказкой на переименование (слиты в `include:` 2026-08-02 — см. `test_config_includes.py` про актуальный механизм). |
| `test_two_phase_execution.py` | Двухфазное выполнение (moves → refresh → vias) на моках – гарантирует, что via планируются после перемещений и имеют корректный `registry_key`. |
| `test_undo_layer.py` | Сохранение и восстановление слоя компонента при undo (`original_layer` в JSON-логе). |
| `test_unique_roles.py` | Проверка уникальности ролей внутри шаблона (фатальная ошибка при дублировании). |
| `test_unknown_keys_validation.py` | Проверку `check_unknown_keys` для секций конфига: неизвестные ключи на верхнем уровне, внутри `clone_placements`, `rules`, `thermal_via_arrays`, `templates`. |
| `test_validation.py` | Предварительные проверки конфигурации: существование шаблонов и падов, достаточность компонентов по ролям, уникальность якорей клонов, резолв цепей via/треков, режим выделения для клонов. |

---

## Описание интеграционных тестов

| Файл | Что тестирует |
|------|---------------|
| `test_connection.py` | Подключение к KiCad, поиск компонента по refdes, поиск цепи по имени, получение всех via. |
| `test_via_ops.py` | Создание/удаление via, работа реестра (`reconcile`, `record_created`), временная via (`temp_via`). |
| `test_track_ops.py` | Создание/удаление треков (прямых дорожек) через API – проверяет работоспособность `create_items` для треков. |
| `test_component_ops.py` | Перемещение компонента на 1 мм по X и обратно, флип на другую сторону и восстановление. |
| `test_extract.py` | Извлечение шаблона из текущего выделения на плате (успех при выделении, ошибка при пустом выделении). |
| `test_registry.py` | Полный цикл реестра с реальным KiCad: создание via, идемпотентность, обновление позиции (удаление старой, создание новой), prune. |

Все интеграционные тесты используют фикстуры и восстанавливают исходное состояние платы после выполнения.

---

## Примечания

- **Модульные тесты** не требуют KiCad и могут запускаться в любом окружении.
- **Интеграционные тесты** требуют открытого KiCad с платой и отмечены маркером `integration` – их нужно запускать отдельно.
- Для интеграционных тестов рекомендуется использовать тестовую плату (например, `test_boards/10CL006YE144C8G.kicad_pcb`), чтобы не повредить рабочий проект.
- Фикстуры интеграционных тестов обеспечивают автоматическую очистку, но всё же стоит запускать их на копии платы или после сохранения.
- При добавлении новых модулей или функций необходимо дополнять тесты, чтобы поддерживать покрытие.

---

## Расширение тестов

Если вы добавляете новый модуль или функциональность, следуйте этим рекомендациям:

- **Для модульных тестов** создавайте отдельный файл `test_<module>.py` в корне `tests/` и используйте `unittest.mock` для изоляции.
- **Для интеграционных тестов** добавляйте новые функции в существующие файлы в `integration_tests/` или создавайте новый файл с маркером `integration`.
- **Фикстуры** для интеграционных тестов добавляйте в `integration_tests/conftest.py`.
- Убедитесь, что все тесты проходят локально перед отправкой изменений.

---

## Лицензия

Тесты распространяются под лицензией MIT, так же как и основной проект.
