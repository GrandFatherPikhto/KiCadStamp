# Использование API kicad-python в KiCadStamp

Вся официальная документация по Python-биндингам KiCad (`kicad-python`) доступна по адресу: **[https://docs.kicad.org/kicad-python-main/](https://docs.kicad.org/kicad-python-main/)**.

В проекте `KiCadStamp` все вызовы к KiCad инкапсулированы в классе `KiCadBoardAdapter` (файл `kicad/adapter.py`), а также используются в `undo.py` и `diagnostics`. Ниже перечислены все задействованные API с указанием их статуса и ссылками на документацию.

---

## 1. Подключение и базовые действия

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.KiCad` (конструктор) | [KiCad — kicad-python](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad) | Стабильный |
| `kipy.KiCad.get_board()` | [KiCad.get_board](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad.get_board) | Стабильный |
| `kipy.KiCad.run_action()` | [KiCad.run_action](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad.run_action) | **Нестабильный** (официальное предупреждение) |

---

## 2. Работа с платой и транзакциями

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board.Board` | [Board — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board) | Стабильный |
| `Board.begin_commit()` | [Board.begin_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.begin_commit) | Стабильный |
| `Board.push_commit()` | [Board.push_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.push_commit) | Стабильный |
| `Board.drop_commit()` | [Board.drop_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.drop_commit) | Стабильный |
| `Board.create_items()` | [Board.create_items](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.create_items) | Стабильный |
| `Board.update_items()` | [Board.update_items](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.update_items) | Стабильный |
| `Board.remove_items_by_id()` | [Board.remove_items_by_id](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.remove_items_by_id) | Стабильный |
| `Board.get_item_bounding_box()` | [Board.get_item_bounding_box](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_item_bounding_box) | Стабильный |
| `Board.get_selection()` | [Board.get_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_selection) | Стабильный |
| `Board.add_to_selection()` | [Board.add_to_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.add_to_selection) | Стабильный |
| `Board.clear_selection()` | [Board.clear_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.clear_selection) | Стабильный |
| `Board.get_vias()` | [Board.get_vias](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_vias) | Стабильный |
| `Board.get_tracks()` | [Board.get_tracks](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_tracks) | **Недокументированный** |
| `Board.get_zones()` | [Board.get_zones](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_zones) | Стабильный |
| `Board.get_nets()` | [Board.get_nets](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_nets) | Стабильный |

---

## 3. Геометрические примитивы

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.geometry.Vector2` | [Vector2 — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Vector2) | Стабильный |
| `Vector2.from_xy()` | [Vector2.from_xy](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Vector2.from_xy) | Стабильный |
| `kipy.geometry.Angle` | [Angle — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle) | Стабильный |
| `Angle.from_degrees()` | [Angle.from_degrees](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle.from_degrees) | Стабильный |
| `Angle.degrees` (свойство) | [Angle.degrees](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle.degrees) | Стабильный |
| `kipy.geometry.Box2` | [Box2 — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Box2) | Стабильный |

---

## 4. Работа с компонентами (футпринтами)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.FootprintInstance` | [FootprintInstance — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance) | Стабильный |
| `Board.get_footprints()` | [Board.get_footprints](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_footprints) | Стабильный |
| `FootprintInstance.reference_field` | [reference_field](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.reference_field) | Стабильный |
| `FootprintInstance.value_field` | [value_field](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.value_field) | Стабильный |
| `FootprintInstance.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.position) | Стабильный |
| `FootprintInstance.orientation` | [orientation](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.orientation) | Стабильный |
| `FootprintInstance.layer` | [layer](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.layer) | Стабильный |

---

## 5. Работа с падами (Pad)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Pad` | [Pad — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad) | Стабильный |
| `Pad.number` | [number](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.number) | Стабильный |
| `Pad.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.position) | Стабильный |
| `Pad.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.net) | Стабильный |
| `Pad.padstack` | [padstack](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.padstack) | Стабильный |

---

## 6. Работа с цепями (Net)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Net` | [Net — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net) | Стабильный |
| `Net.name` | [name](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net.name) | Стабильный |
| `Net.code` | [code](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net.code) | **Устаревшее** (deprecated) |

---

## 7. Работа с переходными отверстиями (Via)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Via` | [Via — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via) | Стабильный |
| `Via.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.position) | Стабильный |
| `Via.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.net) | Стабильный |
| `Via.drill_diameter` | [drill_diameter](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.drill_diameter) | Стабильный |
| `Via.diameter` | [diameter](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.diameter) | Стабильный |

---

## 8. Работа с дорожками (Track)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Track` | [Track — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track) | Стабильный |
| `Track.start` | [start](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.start) | Стабильный |
| `Track.end` | [end](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.end) | Стабильный |
| `Track.width` | [width](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.width) | Стабильный |
| `Track.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.net) | Стабильный |
| `Track.layer` | [layer](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.layer) | Стабильный |

---

## 9. Работа с зонами (Zone)

| Функция / класс | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Zone` | [Zone — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone) | Стабильный |
| `Zone.name` | [name](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone.name) | Стабильный |
| `Zone.outline` | [outline](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone.outline) | Стабильный |

---

## 10. Пользовательские поля (`Field`)

| Компонент | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.Field` | [Field — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Field) | Стабильный |
| `Field.name` | (не документировано явно) | Стабильный |
| `Field.text.value` | (не документировано явно) | Стабильный |

---

## 11. Вспомогательные типы и константы

| Компонент | Документация | Статус |
| :--- | :--- | :--- |
| `kipy.board_types.BoardLayer` | [BoardLayer — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.BoardLayer) | Стабильный |
| `kipy.proto.common.types.KIID` | [KIID — kicad-python](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.proto.common.types.KIID) | Стабильный |

---

# Использование нестабильных, недокументированных и устаревших API в KiCadStamp

Проект KiCadStamp решает реальные задачи автоматизации расстановки компонентов, via и треков в KiCad. Некоторые из этих задач **невозможно** выполнить, оставаясь строго в рамках стабильного публичного API. Поэтому проект сознательно использует:

- **нестабильные** (официально не гарантированные) методы;
- **недокументированные** внутренние поля;
- **устаревшие** (deprecated) свойства;
- **обходные пути** для компенсации нелогичного поведения геттеров/сеттеров.

Все такие места **документированы** в коде, а их работоспособность **проверяется** модульными тестами, что позволяет заранее узнать о поломке при обновлении KiCad или `kipy`.

---

## 1. Нестабильные API

### `kicad.run_action(action)`

**Где используется:**  
- `kicad/adapter.py` – `flip_selected()` вызывает `self._kicad.run_action("pcbnew.InteractiveEdit.flip")`.

**Зачем:**  
Это **единственный** способ выполнить «настоящий» переворот компонента с зеркалированием площадок и шёлкографии. Простое изменение поля `.layer` не даёт нужного эффекта – визуально компонент остаётся без изменений.

**Риск:**  
Метод `run_action` и имена действий официально помечены в документации `kipy` как **нестабильные** (`unstable`). Они могут быть изменены или удалены в любой версии KiCad без предупреждения.

**Альтернатива:**  
Отсутствует – без этого невозможно реализовать корректный переворот.

**Защита:**  
Тесты, использующие флип (например, `test_two_phase_execution.py`), косвенно проверяют работоспособность этого вызова.

---

## 2. Недокументированные внутренние поля

### `FootprintInstance.texts_and_fields` и `FootprintInstance.definition.items`

**Где используется:**  
- `kicad/adapter.py` – `get_field_value()` читает `fp.texts_and_fields`, фильтруя объекты типа `Field`.
- `kicad/adapter.py` – `get_footprint_pads()` читает `fp.definition.items`, отфильтровывая пады.

**Зачем:**  
Пользовательские поля (например, `Role`) доступны только через `texts_and_fields`. Это недокументированный способ, но он широко используется в сообществе.  
Пады компонента также доступны только через `definition.items`, так как `board.get_pads()` не содержит обратной ссылки на родительский футпринт.

**Риск:**  
Внутренняя структура определения футпринта может измениться, что приведёт к поломке чтения полей и падов.

**Альтернатива:**  
Для падов – геометрический маппинг (по координатам), но он ненадёжен при плотной расстановке. Для полей – парсинг `.net`-файла, но это медленнее и требует внешнего файла.

**Защита:**  
Модульные тесты (`test_kicad.py`, `test_full_pipeline_templates.py`) используют моки и проверяют чтение полей через `adapter.get_field_value()`.

---

### `Board.get_item_bounding_box()` с аргументом-списком

**Где используется:**  
- `kicad/adapter.py` – `get_bounding_boxes()` передаёт список объектов в `board.get_item_bounding_box(list(items))`.

**Зачем:**  
При передаче списка метод возвращает список `Box2` для каждого объекта, что позволяет выполнить один батч-запрос вместо множества отдельных.

**Риск:**  
Поведение метода с аргументом-списком не явно документировано, но стабильно работает во всех версиях `kipy`.

**Альтернатива:**  
Вызов `get_item_bounding_box` для каждого объекта по отдельности – неэффективно.

**Защита:**  
Тесты `test_full_pipeline_templates.py` используют этот метод для построения keepout.

---

### `Board.get_tracks()`

**Где используется:**  
- `kicad/adapter.py` – `get_tracks()` используется в `TrackRegistry` для получения всех треков на плате при сверке реестра.

**Зачем:**  
Необходимо для идемпотентного создания треков – при повторном запуске нужно проверить, какие треки уже существуют. Аналогично `get_vias()` для via.

**Риск:**  
Метод `get_tracks()` не документирован в официальной документации KiCad/kipy, но стабильно работает в текущих версиях.

**Альтернатива:**  
Отсутствует – без доступа к списку треков невозможно реализовать идемпотентность.

**Защита:**  
Интеграционные тесты (например, `test_registry.py`) проверяют работу реестра треков.

---

### `Group.proto.items`

**Где используется:**  
- `kicad/adapter.py` – `get_selected_items()` разворачивает группы, используя `Group.proto.items`.

**Зачем:**  
Свойство `.items` у группы всегда пустое (локальный кэш), реальные участники хранятся в protobuf-поле `.proto.items`. Без доступа к этому полю невозможно корректно обрабатывать выделенные группы.

**Риск:**  
Внутренняя структура protobuf может измениться.

**Альтернатива:**  
Нет – единственный способ получить участников группы.

**Защита:**
Диагностические скрипты и тесты `extract` используют выделение с группами.

---

### `KiCadClient._conn` — у kipy нет публичного `close()` (трекинг апстрим)

**Где используется:**
- `kicad/adapter.py` – `close()` добирается до `self._kicad._client._conn.close()`, чтобы явно закрыть pynng-сокет, а не оставлять это сборщику мусора.

**Зачем:**
Найдено вживую (2026-08-04): тихий Windows access violation без Python-фрейма на упавшем потоке — каждый reconnect в GUI создаёт свежий `kipy.KiCad()` (и pynng-сокет), не закрывая предыдущий, а несколько никогда не закрытых сокетов, финализированных GC в непредсказуемых точках и потоках, — правдоподобный триггер краша. В kipy 0.7.1 **нет публичного `close()`** ни на `KiCad`, ни на `KiCadClient` (проверено по `kicad.py`/`client.py`), поэтому реализация лезет в приватный `_conn`.

**Риск:**
Приватная структура (`KiCadClient._connected` / `_conn`) может измениться в любой версии kipy.

**Альтернатива:**
Нет, пока kipy не завезёт публичный close.

**Трекинг апстрим:**
- kipy 0.7.1: публичного `close()` нет (проверено по `kicad.py`/`client.py`).
- **Действие:** следить за issue-трекером kipy на предмет публичного `KiCad.close()` / `KiCadClient.close()`; когда появится — заменить доступ к приватному атрибуту на публичный вызов и удалить этот подраздел.
- **Страховка:** `TestClose` в `tests/kicad/test_kicad.py` закрепляет форму приватных атрибутов ровно под этот доступ (закрывает корректно / no-op до connect / no-op без `_conn` / глотает битый сокет).

---

## 3. Устаревшие (deprecated) API

### `Net.code`

**Где используется:**  
- В основном коде не используется (только в диагностических скриптах `diagnostics/` для отладки).

**Зачем:**  
Может быть полезно для сопоставления цепей по коду в отладочных целях.

**Риск:**  
Свойство `Net.code` помечено в документации `kipy` как **устаревшее** и будет удалено в будущих версиях.

**Альтернатива:**  
Использовать `Net.name` – так и делается во всех критических сценариях.

**Защита:**  
Не используется в основной логике, поэтому удаление `Net.code` не повлияет на работу программы.

---

## 4. Нестандартное поведение геттеров и сеттеров

### Геттеры возвращают копии объектов (no-op при присваивании атрибутам)

**Где используется:**  
В коде проекта **нигде** не используется конструкция вида `obj.attribute.x = value` – везде применяется переприсваивание всего объекта (например, `fp.position = Vector2(...)`).

**Проблема:**  
В `kipy` геттеры (например, `.position`, `.net`) возвращают **копию** объекта, а не ссылку. Присваивание атрибуту этой копии (например, `fp.position.x = 1000`) **не изменяет** оригинал – это тихий no-op.

**Подтверждение:**  
Это поведение подтверждено статическими тестами (в предыдущих версиях проекта) и учтено при написании кода.

**Альтернатива:**  
Всегда переприсваивать объект целиком: `fp.position = Vector2.from_xy(...)`.

---

### Сеттер `FootprintInstance.orientation` не принимает `float`

**Где используется:**  
В коде проекта всегда используется `Angle.from_degrees()` для установки ориентации.

**Проблема:**  
Сеттер `.orientation` ожидает объект `Angle`. Передача числа вызывает `TypeError`.

**Альтернатива:**  
Всегда использовать `Angle.from_degrees()` или `Angle.from_radians()`.

---

## Заключение

KiCadStamp **осознанно** использует API, выходящие за пределы стабильного публичного интерфейса, потому что только так можно решить реальные задачи автоматизации расстановки компонентов, via и треков. Однако все такие места:

- **чётко документированы** в комментариях к коду;
- **сопровождаются модульными тестами**, проверяющими критичные сценарии;
- **имеют обходные пути** (например, переприсваивание объектов вместо изменения атрибутов), чтобы минимизировать риски.

Таким образом, инструментарий остаётся надёжным даже в условиях нестабильного API, а при обновлении KiCad или `kipy` тесты своевременно сигнализируют о возможных проблемах.