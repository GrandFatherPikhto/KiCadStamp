# Команды KiCadStamp (CLI)

Этот документ содержит полный справочник по командам и флагам `kicadstamp_cli.py`, генераторам конфигов из `tools/`, а также практические примеры для типовых сценариев. Сверено с кодом в ветке `main` (проект не ведёт номера версий/тегов, ориентируйтесь на дату/коммит).

---

## Базовый синтаксис

```bash
python kicadstamp_cli.py <команда> [параметры]
```

Если команда не указана, по умолчанию подразумевается `apply`.

---

## Команда `apply` – применить расстановку

Загружает конфиг, подключается к KiCad, выполняет валидацию, планирование и **трёхфазное исполнение** (перемещения → via → треки).

**Порядок перемещений (цепочка зависимостей).** Внутри фазы перемещений `rules`/`clone_placements` больше
не планируются все разом из одного снимка платы — якорь каждого элемента (`anchor_ref`/`anchor_role`)
резолвится относительно платы, и если этот якорь — ref, который в этом же прогоне размещает ДРУГОЙ
элемент, то сначала планируется, перемещается и фиксируется он, и только потом зависимый элемент
планируется уже против реальной, обновлённой платы. Элементы без такой зависимости (якорь — что-то, что
в этом прогоне никто не двигает, либо абсолютная координата) идут первыми, в порядке YAML. Найдено на
реальном баге (2026‑07‑27): клон, заанкоренный на роль внутри шаблона ДРУГОГО клона, вставал на её СТАРОЕ
место, а не туда, куда его в этом же прогоне должны были переставить. Если два и более элемента заанкорены
друг на друга — валидного порядка не существует, и это fatal `ValidationError` ещё до того, как что-либо
тронуто на плате (см. `kicadstamp/placement/dependency_order.py`). `apply --dry-run` печатает разрешённый
порядок, но, поскольку он ничего реально не двигает, всё равно планирует каждый элемент из одного
неизменного снимка — позиции элементов дальше по цепочке в реальном (не dry-run) apply могут получиться
другими; вывод dry-run об этом явно предупреждает.

### Синтаксис

```bash
python kicadstamp_cli.py apply <путь_к_конфигу.yaml> [опции]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--dry-run` | Только распечатать план (перемещения, via, треки), не применять изменения. |
| `--timeout-ms` | Таймаут IPC-соединения с KiCad (мс). По умолчанию `20000`. |
| `--batch-size` | Количество объектов в одной транзакции. По умолчанию `10`. |
| `--verbose` | Включить подробный вывод (DEBUG). |
| `--log-file` | Сохранять логи в указанный файл. |
| `--no-collision-check` | Отключить проверку коллизий (если даёт ложные срабатывания). |
| `--collision-margin` | Дополнительный зазор при проверке коллизий (мм). По умолчанию `0.2`. |
| `--only NAME` | Обработать только `rules`/`clone_placements`/`thermal_via_arrays` с этой идентичностью (флаг можно повторять и/или через запятую: `--only a,b --only c`). Основной способ сузить прогон (замена старому `--clone-placement`, которого больше нет — он не изолировал `rules`/`thermal_via_arrays`, только `clone_placements`, отсюда и путаница). Идентичность — `name:`, если задан, иначе `net` (только у `rules`, см. ниже); у `clone_placements`/`thermal_via_arrays` — обязателен `name:`. Всё, что не совпало, не попадает в этот прогон вообще, даже в проверки и лог — для изолированной проверки одного куска платы без шума от остальных. Незнакомое имя — фатал с подсказкой (`difflib`). |
| `--cluster PATH` | Обработать только спицы / `clone_placements` / `thermal_via_arrays`, чей `Cluster` (`anchor_cluster` / `cluster` у спицы) совпадает с этим путём или его префиксом, посегментно (`Channel_0` матчит и `Channel_0/DAC_OA`). Можно повторять и/или через запятую. Вторая, независимая ось выбора (физический экземпляр, а не имя/идентичность) — для `rules:` сужает `spokes:` внутри правила (правило выживает, если совпал хотя бы один пад, иначе выпадает целиком), для `clone_placements`/`thermal_via_arrays` — фильтр целиком. С `--only` сочетается только через AND (без режима ИЛИ) — если нужно «то ИЛИ это», запустите `apply` дважды, реестр не задвоит уже расставленное. Ничего не найдено — фатал, как и у `--only`. |

**Терминология, которая используется ниже и в коде:** `rules:` (`Rule`, часть `ManualSpoke`) — это **спицы**;
`thermal_via_arrays:` — это **термо-via**; `clone_placements:` (`ClonePlacement`) — это **клоны**. Все три —
независимые секции одного конфига, одинаково фильтруемые через `--only`/`--cluster`/`enabled`.

**`log_file:` в самом конфиге** – необязательное поле в корне YAML (как
`registry_path`), путь резолвится относительно самого файла конфига. Если задано – не нужно каждый раз
передавать `--log-file` руками для одного и того же профиля платы. CLI-флаг `--log-file`, если указан,
имеет приоритет над этим полем:
```yaml
log_file: ../logs/placer.log
```

**`include:` — разбиение профиля на файлы подсистем.** Общего назначения: мёржит `rules:`/
`clone_placements:`/`thermal_via_arrays:` (конкатенация) и `cells:`/`points:`/`extract_profiles:`/
`clone_profiles:` (объединение по ключу) из других файлов в текущий, рекурсивно, и работает **и для**
`apply` (`load_config`), **и для** `extract`/`clone-extract` (`load_profile` — `extract_profiles`/
`clone_profiles` читаются отдельным путём) — так что один файл подсистемы может нести и extract-профиль, и
clone_placement для неё вместе, или внешний файл `Cell` (оберни его содержимое в ключ `cells:` — старый
отдельный механизм `cells_file:`/`cell_files:` был слит в `include:` 2026-08-02, один способ разнести
ЛЮБУЮ секцию по файлам вместо двух):
```yaml
include:
  - subsystems/ldo.yaml
  - path: subsystems/dac_channels.yaml
    enabled: false   # весь файл пропущен — даже не открывается — пока работаете над другим
```
Каждая запись — либо строка-путь, либо `{path, enabled}` (`enabled` по умолчанию `true`). Дубликат ключа
`cells`/`extract_profiles`/`clone_profiles`, заданного в двух разных файлах, — фатал: тут файлы задуманы
как независимые, и повтор имени куда вероятнее опечатка, чем намеренный override. Повторное включение
одного и того же файла (напрямую или из двух разных веток) — тоже фатал, независимо от того, цикл это или
нет. Пути резолвятся относительно файла, который на них ссылается, а не относительно корневого конфига
или текущей директории.

**Про текущий боевой конфиг:** мастер-конфиг платы `3CH-AWG-TIA` — `profiles/3ch-awg-tia.yaml` (слиты `rules:`, `clone_placements:`, `thermal_via_arrays:`, ссылка на `profiles/templates/3ch-awg-tia.yaml` через `cells_file`). Файл `profiles/generated/10CL006YE144C8G.yaml`, который пишет `tools/generate_10cl006.py`, — самодостаточный архивный вариант (можно прогнать отдельно, но в `apply` для этой платы больше не используется).

**`name:` обязателен у каждой записи `thermal_via_arrays:` и у каждого
`clone_placements:`, но у `rules:` — НЕОБЯЗАТЕЛЕН** (правило падает на `net` как фоллбэк — это ненадолго
сделали обязательным и в тот же день откатили: `net` правила и так почти всегда уникален, а требовать
избыточный `name:` у каждой спицы не давало ничего). Используется в `--only`. Раньше `clone_placement` без
`name:` тихо становился литеральной строкой `'?'` (реальная дыра, не поведение), а запись `thermal_via_arrays` без
`name:` — тихо резолвился в `thermal_<pad>`; оба случая убраны, отсутствие `name:` для этих двух — теперь
фатал при загрузке конфига (а `name:` каждой записи `thermal_via_arrays:` ещё и должен быть уникален по
всему списку). Для `rules:` вместо этого — фатал, если **два правила резолвятся в одну и ту же
идентичность** (совпал `net`, не задан различающий `name:`) — добавьте `name:`, чтобы различить, не
полагайтесь на то, что одно из двух будет тихо выбрано:
```yaml
rules:
- net: +3V3_VCCIO
  # name: необязателен – по умолчанию net "+3V3_VCCIO"; добавляйте, только
  # если хочется более понятную метку для --only, или у двух правил один net
  anchor_role: FPGA
  enabled: true          # необязательно, по умолчанию true — см. ниже
  spokes: [...]

thermal_via_arrays:
- name: fpga_thermal   # обязательно, и уникально по всему списку
  enabled: true
  ...

clone_placements:
- name: p5v_pi_filter   # обязательно
  template: 5v_pi_filter
  ...
```

**`enabled: bool` (по умолчанию `true`) у каждой записи `rules:`/`clone_placements:`/`thermal_via_arrays:`** —
выключатель всей записи целиком. `enabled: false` побеждает всегда, применяется **до** того, как вообще
смотрят на `--only`/`--cluster` — это означает «сейчас не существует на плате», а не «исключено из этого
конкретного прогона», поэтому явное упоминание имени в командной строке это не отменяет. Используйте это,
чтобы надолго припарковать кусок конфига, не удаляя его; `--only`/`--cluster` — для разового сужения прогона
среди того, что в остальном включено.

### Примеры

#### Стандартный запуск (расстановка компонентов, via и треков)

```bash
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml
```

#### Запуск с подробным логированием в файл

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --verbose --log-file logs/placer.log
```

#### Предварительный просмотр (dry-run) – ничего не меняет

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --dry-run
```

#### Обработка только одного клона (например, для отладки)

```bash
python kicadstamp_cli.py apply templates\pi_filter_vccio.yaml --only pi_filter_vccio
```

#### Изолированный прогон одного куска платы (--only)

```bash
# Только один clone_placement, без FPGA-спиц и без термовиа в логе
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only p5v_pi_filter --dry-run

# Несколько имён/идентичностей сразу (правило по net + именованная запись thermal_via_arrays), флаг повтором или через запятую
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only +3V3_VCCIO,fpga_thermal
```

#### Сузить по физическому экземпляру вместо имени (--cluster)

```bash
# Только спицы/клоны/термовиа, чей Cluster совпадает с этим каналом (посегментное совпадение префикса)
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --cluster Channel_0 --dry-run

# Сочетание с --only — это AND, не ИЛИ: именно этот clone_placement, И только внутри этого канала
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only p5v_pi_filter --cluster Channel_0
```

#### Отключение проверки коллизий

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --no-collision-check
```

#### Увеличение таймаута для медленного KiCad

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --timeout-ms 30000
```

---

## Команда `undo` – откат последней операции

Находит последний JSON-лог в папке `logs/` и восстанавливает плату (возвращает компоненты на исходные позиции и слои, удаляет созданные via **и треки**).

### Синтаксис

```bash
python kicadstamp_cli.py undo [--verbose] [--log-file]
```

### Пример

```bash
python kicadstamp_cli.py undo --verbose
```

---

## Команда `extract` – извлечение шаблона из выделения

Создаёт шаблон спицы из текущего выделения в PCB-редакторе KiCad. Требуется, чтобы у каждого выделенного компонента было поле `Role`, причём роли должны быть уникальны. Поддерживает извлечение **треков** (дорожек) и **via** вместе с компонентами. Когда в GUI активен фильтр **"Keep only one Cluster"** (или вызывающий сузил `footprints` перед вызовом `extract_template_from_selection`), берутся только компоненты оставленного Cluster — и это теперь распространяется и на связность: трек/via включается, только если его связная компонента (совпадающие концы via, стыки трек-трек, касание via) достигает пада КЕПТ-футпринта. Трек/via, чья компонента касается только исключённого материала, отбрасывается целиком с предупреждением, а не выживает через локальное «взаимное подтверждение» трек-трек (фикс 2026-08-16 — см. `kicadstamp/template_selection.py`).

### Синтаксис

```bash
python kicadstamp_cli.py extract --name <имя_шаблона> --output <файл> [--timeout-ms] [--verbose] [--log-file] [--param KEY=VALUE] [--net-template ЛИТЕРАЛ=ПАТТЕРН] [--origin-by-via-net NET] [--origin-by-component-role ROLE] [--profiles FILE] [--profile NAME]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--name` | Имя шаблона (ключ в секции `templates`). В режиме явных флагов (не `--profile`) необязателен: если не задан, спрашивается интерактивно. |
| `--output` | Путь к выходному файлу. Расширение определяет формат: `.json` → JSON (плоский словарь), иначе YAML. |
| `--timeout-ms` | Таймаут IPC (по умолчанию 20000 мс). |
| `--verbose` | Подробный вывод. |
| `--log-file` | Сохранять логи в файл. |
| `--param KEY=VALUE` | Задаёт параметр для проверки `--net-template` (например, `channel=1`). В шаблон не пишется, нужен только для верификации. Можно указывать несколько раз. |
| `--net-template ЛИТЕРАЛ=ПАТТЕРН` | Заменяет реальную цепь на паттерн с плейсхолдером (например, `DAC1_DB1=DAC{channel}_DB1`). Можно указывать несколько раз. |
| `--origin-by-via-net NET` | Задаёт origin шаблона по позиции via с указанной цепью (вместо левого нижнего угла bbox). Фатально, если такой via нет или она не единственна. Взаимоисключающе с `--origin-by-component-role` (можно указать только один способ задания origin). |
| `--origin-by-component-role ROLE` | Задаёт origin по позиции компонента с указанной ролью. Взаимоисключающе с `--origin-by-via-net`. |
| `--origin-by-component-pad PAD` | Уточняет `--origin-by-component-role`: origin — позиция конкретного пада компонента, а не его центр. Без `--origin-by-component-role` — фатал (уточнять пад можно только у уже указанной роли). |
| `--profiles FILE` | YAML-файл с именованными профилями для `extract`. |
| `--profile NAME` | Использовать профиль из файла `--profiles` вместо явных флагов (нельзя сочетать с `--name`, `--output`, `--param`, `--net-template`, `--origin-by-*` — либо всё из профиля, либо всё явными флагами). |

**Важно:** перед запуском выделите в PCB-редакторе нужные компоненты, via и треки. Роли должны быть уникальны. Вывод (YAML или JSON) записывается обёрнутым в ключ `cells:`, готовый к тому, чтобы просто перечислить его в `include:` основного конфига.

**Неоднозначный `net_template`:** если на падах компонента совпало больше одной сети из `--net-template`/`net_template` (например, дроссель/феррит-бусина на стыке двух рельсов), `net_template` автоматически не проставляется — пишется warning в лог, и (только для YAML, не для JSON) сразу после блока этого компонента добавляется закомментированная строка-заглушка вида `# net_template: could not determine automatically — ...`, чтобы пробел был виден прямо в файле, а не только в логе. Решается либо правкой строки вручную, либо через `--net-template-role ROLE=<сеть>` при повторном запуске.

**Внутри профиля** (`extract_profiles:` в файле `--profiles`): `output:` можно задать один раз на корне файла — общее значение для всех профилей, конкретный профиль переопределяет только если ему нужен другой файл. `name:` необязателен — по умолчанию берётся ключ самого профиля, писать явно нужно только когда имя шаблона должно отличаться от имени профиля. Параметры для верификации `--net-template` — ключ `params:` (не `param:` — тот же ключ, что и у `clone_placements`, специально сделано одинаковым, чтобы не путать).

### Примеры

#### Извлечение шаблона в JSON с параметризацией цепей и origin по via

```bash
python kicadstamp_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

#### Извлечение шаблона с использованием профиля

В файле `extract_profiles.yaml`:
```yaml
# output: общий для всех профилей ниже — задайте один раз здесь, если все
# пишут в один и тот же файл; профилю, которому нужен другой файл, просто
# пропишите output: прямо в нём — это переопределит значение отсюда.
output: templates/my_filter.json

extract_profiles:
  my_filter:
    # name: не нужен — по умолчанию берётся ключ профиля ("my_filter").
    # Пишите явно, только если имя шаблона должно отличаться от имени
    # профиля (например, несколько профилей извлекают в один общий шаблон).
    params:
      PWR_IN: '+3V3'
      PWR_OUT: '+3V3_VCCIO'
    net_template:
      '+3V3_VCCIO': '{PWR_OUT}'
      '+3V3': '{PWR_IN}'
    origin_by_via_net: '+3V3_VCCIO'
```

Запуск:
```bash
python kicadstamp_cli.py extract --profiles extract_profiles.yaml --profile my_filter --verbose
```

#### Извлечение шаблона в YAML (без параметризации)

```bash
python kicadstamp_cli.py extract --name my_filter --output my_filter.yaml --verbose
```

#### Добавление шаблона в существующий конфиг (YAML)

```bash
python kicadstamp_cli.py extract --name my_filter --output 10CL006YE144C8G.yaml --verbose
```

Примечание: если шаблон с таким именем уже существует, он будет перезаписан.

---

## Команда `extract-net` – захват меди одной сети как записи `net_traces:`

НЕ тот же `extract` по выделению: захватывает ВСЮ живую медь одной сети
(дорожки + переходные отверстия) по всей плате, с привязкой к футпринту,
найденному по Role, как ЛОКАЛЬНЫЕ смещения — затем `apply --only=<net>`
переставляет эту медь ЖИВЬЁМ относительно *текущей* позиции якоря на каждом
прогоне (сдвинь якорь в KiCad -> трассировка сети последует за ним). См.
`docs/config_ru.md` → `net_traces:`.

### Синтаксис

```bash
python kicadstamp_cli.py extract-net --net <СЕТЬ> --anchor-role <РОЛЬ> \
    [--anchor-sheet <ЛИСТ>] [--anchor-cluster <КЛАСТЕР>] [--anchor-pad <ПАД>] \
    --output <файл.yaml>
```

### Опции

- `--net` (обязательно) — имя сети для захвата (например `DAC_DB0`; локальные
  иерархические сети сохраняют полную форму `/Channel_0/...`).
- `--anchor-role` (обязательно) — поле Role якорного футпринта, поиск по всей
  живой плате (тот же `resolve_footprint_by_role`, что у Rule/ClonePlacement).
  Фатал, если роли нет или она неоднозначна.
- `--anchor-sheet` — сузить поиск anchor_role по листу. ВНИМАНИЕ: у
  `extract-net` нет своего конфига, поэтому сужение по листу требует
  `schematic_dir` в ЦЕЛЕВОМ конфиге на этапе apply; здесь для снятия
  неоднозначности предпочитайте `--anchor-cluster`.
- `--anchor-cluster` — сузить по полю Cluster (префиксное совпадение).
- `--anchor-pad` — якорь на центр этого пада вместо центра футпринта.
- `--output` (обязательно) — файл YAML/JSON; добавляет/заменяет запись под
  `net_traces:` (та же сеть заменяется на месте, всё остальное сохраняется).

### Пример

```bash
python kicadstamp_cli.py extract-net --net DAC_DB0 --anchor-role FPGA \
    --anchor-pad 42 --output 3ch-awg-tia.yaml --verbose
# затем, после перемещения FPGA в KiCad:
python kicadstamp_cli.py apply 3ch-awg-tia.yaml --only DAC_DB0
```

---

## Команда `clone-extract` – снятие снимка канала (файловый клонер)

Анализирует иерархический проект (без подключения к KiCad), извлекает все компоненты, дорожки и via, принадлежащие указанному каналу, и сохраняет снимок в YAML. Полезно для изучения структуры канала перед созданием конфигурации для `ClonePlacement`.

### Синтаксис

```bash
python kicadstamp_cli.py clone-extract --net <файл.net> --pcb <файл.kicad_pcb> --channel <имя_канала> --output <файл.yaml> [--profiles FILE] [--profile NAME] [--verbose]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--net` | Путь к файлу `.net` (нетлист). |
| `--pcb` | Путь к файлу `.kicad_pcb`. |
| `--channel` | Имя канала (например, `Channel_0`). |
| `--output` | Выходной YAML-файл. |
| `--profiles FILE` | YAML-файл с именованными профилями для `clone-extract`. |
| `--profile NAME` | Использовать профиль из файла `--profiles` вместо явных флагов. |
| `--verbose` | Подробный вывод. |

### Пример

```bash
python kicadstamp_cli.py clone-extract --net my_project.net --pcb my_project.kicad_pcb --channel Channel_0 --output snapshot.yaml --verbose
```

С использованием профиля (`clone_profiles.yaml`):
```yaml
clone_profiles:
  channel0:
    net: my_project.net
    pcb: my_project.kicad_pcb
    channel: Channel_0
    output: snapshot.yaml
```

Запуск:
```bash
python kicadstamp_cli.py clone-extract --profiles clone_profiles.yaml --profile channel0 --verbose
```

После выполнения вы получите YAML-файл с информацией о канале, который можно использовать для написания шаблона и `ClonePlacement`.

---

## Команда `channel-copy` – копирование расстановки целого канала (живая плата)

Копирует ВСЮ расстановку канала (компоненты + via + дорожки) с исходного
канала на один или несколько каналов назначения, применяя ко всей конструкции
одну жёсткую трансформацию (якорь + поворот + опциональное зеркало). Карта
близнецов (какой рефд на канале назначения соответствует какому на исходном)
строится по ЖИВОЙ плате через UUID-цепочки `fp.sheet_path.path` — НЕ по именам
Role — поэтому повторяющиеся Role-схемы между PIF-инстансами внутри канала не
мешают (это структурный предел, из-за которого extract одной клеткой
неприменим к целому каналу). Это вариант **Б** тройки А/Б/В, дополняющий
`ClonePlacement`/`CoordinatePlacement` — сравнение см. в
[docs/placement_ru.md](placement_ru.md).

Идемпотентность строится на позиции+цепи (допуски 0.01 мм / 0.1°) — повторный
прогон на тот же канал назначения не создаёт дублей, а реестр расстановки не
затрагивается.

### Синтаксис

```bash
python kicadstamp_cli.py channel-copy --src <канал> --dst <канал> [--dst <канал> ...]
    (--pivot REF [--pivot-pad P] | --pivot-role ROLE | --src-point X,Y --dst-point X,Y)
    [--offset DX,DY] [--target-dst X,Y] [--angle DEG] [--mirror]
    [--include-global] [--dry-run] [--no-collision-check] [--verbose]
```

### Опции

| Флаг | Описание |
|------|-------------|
| `--src` | Имя исходного канала (например, `Channel_0`). |
| `--dst` | Имя канала назначения (например, `Channel_1`); повторяемый — несколько каналов за один запуск. |
| `--pivot REF` | Рефдес пивот-компонента на исходном канале. |
| `--pivot-role ROLE` | Пивот по полю `Role` на исходном канале (переживает переаннотацию). |
| `--pivot-pad P` | Якорь на этом паде пивота вместо его центра. |
| `--offset DX,DY` | Дополнительный сдвиг, прибавляемый к позиции пивота на канале назначения. |
| `--target-dst X,Y` | Явная точка якоря назначения (если близнец пивота ещё не размещён). |
| `--src-point X,Y` / `--dst-point X,Y` | Режим точек — без компонента. |
| `--angle DEG` | Поворот всей конструкции (градусы). |
| `--mirror` | Отразить всю конструкцию (все слои инвертируются). |
| `--include-global` | Копировать и постороннюю медь (глобальные цепи) внутри bbox исходного канала. |
| `--dry-run` | Только вывести план, не писать на плату. |
| `--no-collision-check` | Отключить проверку коллизий. |
| `--verbose` | Подробный вывод. |

### Семантика трансформации (якорь + поворот + сдвиг)

Для каждого футпринта источника (без зеркала):

```
X'   = R(angle) · (X − anchor_src) + anchor_dst
rot' = (rot + angle) mod 360°
```

С `--mirror` точка зеркалится по X относительно вертикальной оси через
`anchor_src`, а угол становится `(180° − (rot + angle)) mod 360°` — та же
конвенция, что у `ClonePlacement.mirror`. Порядок операций (задокументирован,
они не коммутируют): **сначала поворот, затем зеркало**. Все слои
инвертируются (F.Cu↔B.Cu). Маппинг цепей: локальные цепи `/Channel_0/...`
становятся `/Channel_1/...` (через `TwinMap.twin_net`); глобальные цепи
проходят как есть.

### Пример

```bash
# Копия Channel_0 -> Channel_1 со сдвигом +2,-1 мм и без поворота (сначала dry-run!)
python kicadstamp_cli.py channel-copy --src Channel_0 --dst Channel_1 --pivot C601 --offset 2,-1 --dry-run

# Реальный запуск, два канала назначения, поворот 90°
python kicadstamp_cli.py channel-copy --src Channel_0 --dst Channel_1 --dst Channel_2 \
    --pivot C601 --angle 90
```

> **Обязательный протокол первого запуска** — `channel-copy` — самая крупная
> батчевая живая запись (move + via + track) в проекте: сначала `--dry-run` и
> просмотр плана, затем запуск на тестовой плате. Это часть Definition of Done.

---

## Скрипты-утилиты (`tools/`)

### `transform_template.py` – трансформация шаблонов (опционально)

Отдельный скрипт для постобработки уже существующих шаблонов (YAML или JSON). Позволяет поворачивать, зеркалировать и переносить начало координат без повторного извлечения с платы.

#### Синтаксис

```bash
python tools/transform_template.py -i <входной_файл> -o <выходной_файл> [опции]
```

#### Опции

| Флаг | Описание |
|------|----------|
| `-i, --input` | Входной YAML/JSON-файл с шаблоном. |
| `-o, --output` | Выходной файл (формат определяется расширением). |
| `--rotate DEG` | Поворот против часовой стрелки на угол (градусы). |
| `--mirror-x` | Зеркалирование по оси X (меняет знак `across`). |
| `--mirror-y` | Зеркалирование по оси Y (меняет знак `along`). |
| `--set-origin-by-via-index N` | Перенести начало координат на via с индексом N (0-based). |
| `--set-origin-by-via-net NET` | Перенести начало на via с указанной цепью. |
| `--set-origin-by-component-index N` | Перенести начало на компонент с индексом N. |
| `--set-origin-by-component-role ROLE` | Перенести начало на компонент с указанной ролью. |
| `--origin-x X --origin-y Y` | Явно задать смещение начала координат (мм). |

**Порядок применения:** сначала перенос начала (если задан), затем поворот и зеркалирование. Это гарантирует, что целевой элемент остаётся в (0,0) после всех преобразований.

**Известное ограничение:** скрипт трансформирует только `vias` и `components`. Секция `tracks` (если она есть в шаблоне — например, у `cap_pair_standard`/`cap_pair_standard_clone` в `profiles/templates/3ch-awg-tia.yaml`) **не читается и не переносится в результат** — при трансформации шаблона с треками они молча теряются в выходном файле. Для шаблонов с треками пока не использовать, либо дописывать `tracks` в выходной файл руками.

#### Примеры

#### Поворот на 180° и перенос начала на via с цепью

```bash
python tools/transform_template.py -i template.yaml -o template_rotated.yaml --rotate 180 --set-origin-by-via-net "GND"
```

#### Зеркалирование по X и перенос начала на компонент с ролью

```bash
python tools/transform_template.py -i template.yaml -o template_mirrored.yaml --mirror-x --set-origin-by-component-role FB
```

#### Явный сдвиг начала координат

```bash
python tools/transform_template.py -i template.yaml -o template_shifted.yaml --origin-x 1.5 --origin-y -2.0
```

### `generate_10cl006.py` – генератор конфигов для 10CL006YE144C8G

Готовый к запуску скрипт (не пример, реально используется в проекте). Единый источник данных — таблицы `BANKS` (пад/сдвиг/поворот по каждому банку питания FPGA) и `CLUSTER_MAP` (net → имя `Cluster`) в самом файле — из них генерируются сразу три производных артефакта.

#### Синтаксис

```bash
python tools/generate_10cl006.py
```

Без аргументов — все пути на выход зашиты в скрипте (см. `main()`), таблицы `BANKS`/`CLUSTER_MAP`/переключатели якоря (`USE_ANCHOR_ROLE`, `THERMAL_USE_ANCHOR_ROLE`) правятся прямо в исходнике.

#### Что генерирует

| Файл | Назначение |
|------|------------|
| `profiles/generated/10CL006YE144C8G.yaml` | Rules-конфиг (`ManualSpoke`/`Rule`) — apply-ready сам по себе, использует старый инлайновый (приблизительный) `templates:`. |
| `profiles/generated/10CL006YE144C8G.clone_placements.yaml` | Эквивалентная геометрия, но как `clone_placements:` (`ClonePlacement`). **С 2026-07-26 `Rule`/`ManualSpoke` тоже умеет клонировать треки** (см. `spoke_layout.py`/`TemplateTrack`) — держать этот путь параллельно теперь имеет смысл ради резолва якоря по `anchor_pad`/`anchor_cluster` и `{power_net}`-плейсхолдеров через `params`, которых у `Rule` нет, а не ради треков. Требует шаблон `cap_pair_standard_clone` из `profiles/templates/3ch-awg-tia.yaml` (через `cells_file`). Автоматически никуда не подключается — блок копируется руками в `profiles/3ch-awg-tia.yaml` после проверки `--dry-run`. |
| `profiles/generated/10CL006YE144C8G.cluster_table.md` | Таблица `net \| pad \| cluster` (`FPGA_PWR_BANK/<pad>`) — шпаргалка для ручной простановки поля `Cluster` в Eeschema (Bulk Edit) на тех падах, для которых резолву не хватит сужения по физической близости к якорю. |

`anchor_cluster` в `clone_placements` проставлен всегда — с 2026-08-14 он сужает ТОЛЬКО якорь, а сужение ролей ВНУТРИ ячейки читает собственный Cluster размещения (`name:`, см. `docs/config.md`); в рабочих профилях `name:` совпадает с `anchor_cluster`, так что они не расходятся. Даже до разметки `Cluster` в схеме резолвер просто пропускает соответствующую ступень сужения и падает на следующую — поэтому сгенерированный файл можно сразу гонять через `apply --dry-run --verbose`, а по логу видно, для каких падов сужения по близости не хватило — только их и тегать.

#### Пример

```bash
python tools/generate_10cl006.py
# Сгенерирован: profiles/generated/10CL006YE144C8G.yaml
# Сгенерирован: profiles/generated/10CL006YE144C8G.clone_placements.yaml
# Сгенерирован: profiles/generated/10CL006YE144C8G.cluster_table.md
# Всего спиц: 24
```

### `generate_config.py` – заготовка-пример (НЕ готовый к запуску скрипт)

В отличие от `generate_10cl006.py`, это **шаблон-заготовка** для написания подобного генератора под новую микросхему, а не рабочий инструмент. `TEMPLATE` в нём заполнен литералами `[...]` (Ellipsis) вместо реальной геометрии — при запуске «как есть» падает с ошибкой сериализации YAML:

```bash
python tools/generate_config.py
# ValueError: dictionary update sequence element #0 has length 1; 2 is required
```

Используйте его как отправную точку: скопируйте, замените `TEMPLATE` на реальный шаблон (например, полученный через `extract`), заполните список `FILTERS` своими `CloneParams` (`anchor_ref`/`anchor_pad`/`origin_x`/`origin_y`/`rotation_deg`/`params`/`nets`) — и только тогда запускайте.

### `fieldstool_cli.py` – массовая простановка/переименование Role/Cluster в схеме

Правит `.kicad_sch` напрямую (без KiCad), два подкоманды: `set` (`refdes -> {Role, Cluster}`,
бывший `tools/apply_role_cluster.py`, перенесён и обобщён 2026-08-01, старый скрипт удалён) и
`rename` (переименование значения ПОВСЮДУ, без перечисления refdes: `old_value -> new_value`).
Альтернатива багованному Bulk Edit в Eeschema для массовой разметки. **Единственный писатель
`.kicad_sch` во всём проекте** — до 2026-07-26 KiCadStamp не писал в схему вообще ни разу, поэтому
у инструмента усиленные страховки: dry-run по умолчанию, `.bak` перед записью, самопроверка
результата через `sexpdata`, отказ при не-ASCII в значениях (тот же класс опечатки, что нашёлся в
`Role` живьём — кириллическая «С» вместо латинской, см. `diagnostic_charset.py` выше) и отказ при
запущенном KiCad (файл может быть открыт в Eeschema).

Библиотека живёт в `kicadstamp/` (модули `schematic_blocks.py`/`schematic_discovery.py`/
`schematic_safety.py`/`schematic_editing.py`/`schematic_set_fields.py`/`schematic_rename_fields.py`
— перенесены туда из отдельного пакета `fieldstool/` 02.08.2026, см. [docs/fieldstool_ru.md](
fieldstool_ru.md)). Есть и GUI — первый правый таб `kicadstamp_gui.py` (см.
`docs/gui_ru.md#таб-fieldstool`) — дерево Role/Cluster по схеме, отложенная (staged)
простановка/переименование, пока KiCad открыт, и явный Apply, когда KiCad закрыт (см.
`techdocs/handoff/handoff_2026_08_01_fieldstool.md`, внутренний, не в git).

#### Синтаксис

```bash
python fieldstool_cli.py set <config.yaml> [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
python fieldstool_cli.py rename <config.yaml> [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
```

#### Формат конфига

`set` (`root_sheet:` — путь к КОРНЕВОМУ листу проекта, а не к папке: остальные `.kicad_sch`
находятся обходом иерархии `(sheet (property "Sheetfile" ...))`, не плоским glob'ом директории):

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch   # относительно этого YAML
fields:
  C51:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/17
  C52:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/26
```

`rename` (значение меняется у ЛЮБОГО символа, где оно СЕЙЧАС совпадает — refdes перечислять не
нужно):

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch
renames:
  Role:
    OLD_ROLE_A: NEW_ROLE_A
  Cluster:
    Old_Cluster_Name: New_Cluster_Name
```

#### Опции

| Флаг | Описание |
|------|----------|
| `--write` | Реально записать. Без флага — только dry-run (печатает, что изменится). |
| `--allow-non-ascii` | Пропустить проверку значений на не-ASCII символы (по умолчанию — фатал). |
| `--force-with-kicad-running` | Писать, даже если обнаружен запущенный процесс KiCad. |
| `--verbose` | Подробный вывод. |

#### Что важно знать перед использованием

- **Многоюнитовые символы** (сдвоенные ОУ и т.п.) — правятся ВСЕ units этого refdes (`set`) / ВСЕ
  units, где значение сейчас совпадает (`rename`).
- **Многократный инстанс одного листа** (например, три одинаковых `Channel_N`) — Role/Cluster в
  формате `.kicad_sch` общие на весь физический символ, не per-instance. `set`: если конфиг просит
  разные значения для двух refdes, которые физически являются одним и тем же размещённым символом
  на разных инстансах листа — фатальный отказ, а не молчаливое применение одного из двух. `rename`
  этой проблемы в принципе не имеет — всегда пишет одно и то же новое значение всем совпадениям.
  - `rename`: значение из `renames:`, не найденное НИГДЕ — не фатал, а предупреждение (это либо
    опечатка, либо просто повторный запуск после уже применённого переименования — операция
    идемпотентна).
- Правки — точечная подстановка текста (regex + баланс скобок), не полный parse→dump цикл: меняются только
  байты внутри нужного значения (или точка вставки нового `property`), остальной файл побайтово не
  затрагивается — проверено `diff` на реальном файле, диф в одну строку.
- После `--write` обязательно `Update PCB from Schematic` в pcbnew — правка в `.kicad_sch` на плату сама не
  долетает.

#### Пример

```bash
python fieldstool_cli.py set roles.yaml --verbose         # сначала dry-run
python fieldstool_cli.py set roles.yaml --write            # затем реально записать
python fieldstool_cli.py rename renames.yaml --write
```

### `update_i18n.py` – пересборка каталогов переводов (gettext)

Извлекает все строки, обёрнутые в `_()` (`kicadstamp/`, `kicadstamp_cli.py`, `tools/`, `tests/`, ...) в
`messages.pot`, сливает их с существующими `locales/en/LC_MESSAGES/kicadstamp.po` и
`locales/ru/LC_MESSAGES/kicadstamp.po` (pybabel сохраняет уже переведённые строки, новые добавляет пустыми
или помечает `#, fuzzy`, если нашёл похожую), компилирует оба каталога в `.mo`. Временный `messages.pot`
удаляется в конце. Замороженные архивы (`files/`, `old/`, `arch/`, `test_sample/`) в сканирование не
попадают. Требует `pip install babel` (уже в `requirements.txt`).

#### Синтаксис

```bash
python tools/update_i18n.py
```

Без аргументов и флагов — пути и языки (`en`, `ru`) зашиты в скрипте.

#### Когда запускать

- После добавления/изменения ЛЮБОГО текста, обёрнутого в `_(...)` (новый `logger.info`, новая fatal-ошибка,
  новый argparse `help=`, и т.п.) — иначе `locales/*/kicadstamp.mo` устареет, и часть сообщений будет
  показываться на английском (fallback) даже при `LANG=ru`.
- Каталоги коммитятся в git (`.po` и скомпилированные `.mo`) — запускать нужно ДО коммита, CI/build-хука на
  это нет.

#### После запуска

- Новые/изменившиеся строки в `locales/ru/LC_MESSAGES/kicadstamp.po` появляются с пустым `msgstr ""` (нужен
  перевод) или с пометкой `#, fuzzy` (pybabel сам подобрал похожую по смыслу старую строку — **не доверять
  вслепую**, проверить и убрать метку `fuzzy`, иначе gettext игнорирует запись как черновую и показывает
  `msgid` (английский) вместо неё).
- Найти непереведённые строки: `grep -B2 'msgstr ""' locales/ru/LC_MESSAGES/kicadstamp.po` (первое
  совпадение — заголовок каталога с пустым `msgid ""`, это норма, не баг).
- Проверить fuzzy-записи: `grep -c '#, fuzzy' locales/ru/LC_MESSAGES/kicadstamp.po`.

#### Пример

```bash
python tools/update_i18n.py
# ... extracting messages from ...
# updating catalog locales\en\LC_MESSAGES\kicadstamp.po based on messages.pot
# updating catalog locales\ru\LC_MESSAGES\kicadstamp.po based on messages.pot
# compiling catalog locales\en\LC_MESSAGES\kicadstamp.po to locales\en\LC_MESSAGES\kicadstamp.mo
# compiling catalog locales\ru\LC_MESSAGES\kicadstamp.po to locales\ru\LC_MESSAGES\kicadstamp.mo
# ✅ Переводы обновлены.
```

---

## Диагностические команды (для отладки и тестирования)

Эти команды вызывают диагностические скрипты из папки `kicadstamp/diagnostics/`. Они помогают проверить работу IPC, геометрию, чтение полей, флип и т.д.

### Проверка чтения пользовательского поля `Role`

```bash
python -m kicadstamp.diagnostics.test_custom_fields C5 --field Role --verbose
```

### Тест перемещения одного компонента

```bash
# Сдвинуть на +1 мм по X
python -m kicadstamp.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Вернуть обратно
python -m kicadstamp.diagnostics.test_move_one_cap C5 --revert
```

### Тест флипа компонента

```bash
python -m kicadstamp.diagnostics.test_flip_one_cap C6
```

### Тест создания одной via

```bash
# Создать via рядом с C5
python -m kicadstamp.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Удалить последнюю созданную via
python -m kicadstamp.diagnostics.test_create_one_via --remove
```

### Тест на краш KiCad при первой записи (issue #24966)

Полное описание (параметры, гипотезы H1-H3, вывод, зависимости) вынесено в отдельный документ:
[docs/diagnose_first_write_crash_ru.md](diagnose_first_write_crash_ru.md).

```bash
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # только чтения, безопасно
python -m kicadstamp.diagnostics.diagnose_first_write_crash             # полный тест, может уронить KiCad
```

### Вывод информации о выделенных компонентах

```bash
python -m kicadstamp.diagnostics.get_selected_component
```

### Получение bounding box пада

```bash
python -m kicadstamp.diagnostics.get_pad_bbox --ref IC1 --pad 17
```

### Анализ keepout и позиций via

```bash
python -m kicadstamp.diagnostics.diagnostic_keepout 10CL006YE144C8G.yaml
```

---

## Рекомендации по использованию

1. **Перед первым запуском** выполните `extract` на существующем правильном экземпляре, чтобы получить шаблон. Используйте JSON-формат, если предпочитаете его YAML для внешнего файла.
2. **Проверяйте конфигурацию** через `dry-run`, чтобы убедиться, что позиции, via и треки расставляются так, как вы ожидаете.
3. **Для отладки** используйте `--verbose` и сохраняйте лог в файл.
4. **При обработке нескольких клонов** в режиме «по выделению» используйте `--only <имя>`, чтобы обрабатывать их по одному.
5. **Если KiCad падает** при первом запуске, закройте редактор схем или сделайте интерактивную правку в PCB перед запуском (обход issue #24966).
6. **Для изучения иерархических проектов** перед написанием `ClonePlacement` используйте `clone-extract` – это даст вам точные имена цепей и refdes близнецов.
7. **Храните шаблоны отдельно** – перечислите внешний файл в `include:` (обернув его в ключ `cells:`), чтобы избежать загромождения файла геометрией.
8. **Трансформируйте шаблоны** с помощью `transform_template.py` вместо ручного пересчёта координат.

---

## Справка по всем командам

Встроенная справка:

```bash
python kicadstamp_cli.py --help
python kicadstamp_cli.py apply --help
python kicadstamp_cli.py extract --help
python kicadstamp_cli.py undo --help
python kicadstamp_cli.py clone-extract --help
```

---

## Возможные ошибки и их решение

| Ошибка | Возможная причина | Решение |
|--------|-------------------|---------|
| `BoardNotFoundError` | KiCad не запущен или плата не открыта. | Откройте проект в KiCad и выполните `adapter.refresh_board()`. |
| `ComponentNotFoundError` | Указанный `anchor_ref` не найден на плате. | Проверьте refdes в конфиге. |
| `ValidationError: не хватает компонентов для ролей` | Недостаточно компонентов с полем `Role` для данной цепи. | Добавьте поле `Role` на нужные компоненты в схеме и выполните Update PCB. |
| `ValidationError: резолвнутая цепь via не найдена` | Опечатка в `params` или `net_overrides`. | Проверьте соответствие имён цепей в конфиге и в схеме. |
| `ConnectionError` при записи | KiCad упал (известный баг #24966) или завис. | Закройте редактор схем или сделайте интерактивную правку в PCB, затем перезапустите. |
| `Крах KiCad при первом запуске` | Открыт редактор схем и не было интерактивных правок. | Workaround: закройте схему или подвиньте компонент в PCB и сохраните. |
| `Не удаётся найти via/трек` при undo | Объект был удалён вручную. | Undo пропускает отсутствующие объекты и продолжает работу. |

---

## Набор актуальных команд (быстрый старт)

### Расстановка конденсаторов питания для FPGA (мастер-конфиг платы)

```bash
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --verbose --log-file logs/placer.log
```

### Пересборка сгенерированных конфигов/таблицы кластеров для 10CL006

```bash
python tools/generate_10cl006.py
```

Дальше — `apply profiles/3ch-awg-tia.yaml --dry-run --verbose`, чтобы проверить, что новая геометрия резолвится так, как ожидается (см. раздел `generate_10cl006.py` выше).

### Отмена расстановки

```bash
python kicadstamp_cli.py undo --verbose
```

### Извлечение шаблона в JSON (рекомендуемый формат)

```bash
python kicadstamp_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

### Применение клона с внешним файлом шаблонов

```bash
python kicadstamp_cli.py apply config_with_include.yaml --only fpga_filter_1v2_vccint
```

### Трансформация шаблона

```bash
python tools/transform_template.py -i templates/pi_filter_4.json -o templates/pi_filter_4_rotated.json --rotate 180 --set-origin-by-via-net '+3V3_VCCIO'
```

### Тестирование KiCad на краши

```bash
# Только чтения
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8

# Полный тест (чтения + запись)
python -m kicadstamp.diagnostics.diagnose_first_write_crash

# С паузой 30 секунд перед записью
python -m kicadstamp.diagnostics.diagnose_first_write_crash --delay 30
```

---

## Лицензия

Все примеры распространяются под лицензией MIT, так же как и основной проект.

