# Кодинг расстановки на Python: `explore`/`author`

Два дополнительных, необязательных модуля для использования KiCadStamp как библиотеки вместо (или
вместе с) CLI/YAML-воркфлоу — ни один не меняет формат YAML-конфига или пайплайн `apply`/`extract`,
оба — тонкие обёртки над уже существующим. Про сам синтаксис YAML — [docs/config_ru.md](config_ru.md);
про команды CLI — [docs/commands_ru.md](commands_ru.md).

У этой страницы две части: справочник по API (`kicadstamp.explore`/`kicadstamp.author`), затем
разбор в лоб реального скрипта из этого репозитория
(`boards/3ch-awg-tia/scripts/dac_channels.py`).

---

## `kicadstamp.explore` — read-only запросы к плате

Вырос из повторяющегося паттерна: отвечать на «какие компоненты с `Role=X`», «какая цепь на этом
паде», «под каким инстансом листа (`Channel_0`/`Channel_1`/...) этот футпринт» — каждый раз новым
одноразовым скриптом. `Board.select()` заменяет это одним переиспользуемым вызовом.

```python
from kicadstamp.explore import Board

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.sexp",
                       schematic_dir="../../../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet      nets
# ----  ------  -------  ---------  ----
# IC2   AD_DAC  -        Channel_0  ...
# IC3   AD_DAC  -        Channel_1  ...
# IC4   AD_DAC  -        Channel_2  ...

# та же неоднозначность, которая без внимания приводит к настоящему фаталу
# в apply: роль повторяется дважды на канал — select() показывает это сразу,
# а не роняет прогон посередине.
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref  role      cluster  sheet      nets
# ---  --------  -------  ---------  ----
# R33  R_TERM_P  -        Channel_0  ...
# R39  R_TERM_P  -        Channel_0  ...

# лазейка: .fp — сырой FootprintInstance, для всего, что не покрыто выше
comp = board.select(ref="IC2")[0]
comp.nets           # {'21': '/Channel_0/DAC/DAC_OUT_P', ...}
comp.fp.position     # сырой объект kipy (нанометры)
```

Фильтры `select()` (все опциональны, комбинируются через И):

| Фильтр | Что матчит |
|---|---|
| `ref` | точный refdes |
| `role` | точное значение поля `Role` |
| `cluster` | **сегментный префикс** — тот же алгоритм, что у реального резолвера `anchor_cluster` (`Channel_1` матчит `Channel_1/1V2_PLL`, но не `Channel_10`) |
| `sheet` | принадлежность резолвленной цепочке инстансов листа футпринта |
| `net` | любой пад на этой цепи |

`Board` — **стабильный снимок**, снятый при `connect()`/`refresh()` — никогда не обновляется сам.
Вызывай `board.refresh()` после любого изменения платы (ручная правка в KiCad, или скриптовый прогон
`apply_config()`/`cli_main(..., --apply)`), прежде чем доверять следующему `select()`.

`select_items(...)` возвращает сырой смешанный список (футпринты/via/треки), как вернул бы
`get_selected_items()`, собранный по фильтрам вместо живого GUI-выделения — передавай прямо в
`template_extraction.extract_template_from_selection(items=...)` для скриптового `extract` (одного
`net` недостаточно, чтобы различить компоненты на одной цепи в разных физических инстансах общей цепи
вроде `GND` — нормально для частого случая, когда имя цепи уже уникально, иначе держи живое
GUI-выделение для этой конкретной подсистемы).

---

## `kicadstamp.author` — кодинг расстановки вместо копипаста YAML

Ручной, поканальный `clone_placements` — ровно то место, где заводится копипаст-ошибка (не тот ключ
`nets:`, дублированный `anchor_pad:`, имя листа, скопированное у соседнего канала) — цикл `for` так
ошибиться физически не может. `ClonePlacement`/`Rule` (`kicadstamp.config`) — обычные dataclass'ы,
собирай их напрямую, реальными Python-переменными вместо YAML-подстановки `{placeholder}`:

```python
from kicadstamp.config import ClonePlacement

clones = [
    ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": f"/Channel_{i}/DAC/DAC_OUT_P"},
        xy=(0.0, 25.0 - 25.0 * i), rotation_deg=270.0 - 90.0 * i,
        retired=False, skip=False,
    )
    for i, ch in enumerate(["Channel_0", "Channel_1", "Channel_2"])
]
```

`xy=` — обычный 2-кортеж (не `origin_x_mm=`/`origin_y_mm=` — переименованы 2026-07-31, см. раздел про
`clone_placements:` в [docs/config_ru.md](config_ru.md)). `retired=`/`skip=` по умолчанию `False` в
dataclass — прописывать их явно выше нужно только для наглядности, не обязательно.

**Вариант (a) — стандартная точка входа (`cli_main`):**

Использует каждый скрипт под `boards/*/scripts/*.py` — одно место для шаблонного argparse-кода
`--apply`/`--dry-run`/`--verbose`, вместо того чтобы каждый скрипт изобретал его заново. `cli_main`
живёт в `kicadstamp.author_cli` (вынесен из `kicadstamp.author` 2026-08-11, чтобы модуль author
оставался чистой библиотекой):

```python
# boards/3ch-awg-tia/scripts/my_subsystem.py
from pathlib import Path
from kicadstamp.author_cli import cli_main
from kicadstamp.config import ClonePlacement

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "generated" / "my_subsystem.sexp"

def build() -> list:
    return [ClonePlacement(...), ...]

if __name__ == "__main__":
    cli_main(build, str(OUTPUT), str(HERE.parent / "profiles/power.sexp"), description=__doc__)
```

```bash
python boards/3ch-awg-tia/scripts/my_subsystem.py             # только пишет OUTPUT, плату не трогает
python boards/3ch-awg-tia/scripts/my_subsystem.py --apply --dry-run --verbose   # план, без записи
python boards/3ch-awg-tia/scripts/my_subsystem.py --apply                      # пишет OUTPUT, затем применяет
```

`root_config_path` (третий аргумент, `.../profiles/power.sexp` выше) — то, что реально загружается и
применяется при `--apply` — именно он несёт `schematic_dir`/`registry_path`, и (через
`include:`) от него ожидается подхватить сам `OUTPUT`, чтобы реестр видел ПОЛНЫЙ конфиг платы, а не
срез одного этого скрипта (частичный `Config`, собранный из одного скрипта, небезопасен для вычистки
реестра — см. раздел «Как сделать неправильно» ниже).

**Вариант (b) — низкоуровневые кусочки**, если `cli_main` не подходит (например, вообще не нужен
гейтинг через `--apply`):

```python
from kicadstamp.author import dump_clone_placements, dump_rules, dump_template, apply_config

dump_clone_placements(clones, "boards/3ch-awg-tia/generated/dac_channels.sexp")   # {'clone_placements': [...]}
dump_rules(rules, "boards/3ch-awg-tia/generated/fpga_spokes.sexp")                # {'rules': [...]}
dump_template({"my_cell": {"vias": [...], "components": [...]}}, "templates/my_cell.sexp")

# прямо в живой пайплайн apply, минуя шаг с генерируемым YAML вовсе:
from kicadstamp.config import load_config
cfg, ctx = load_config("boards/3ch-awg-tia/profiles/power.sexp")
cfg.clone_placements.extend(clones)
apply_config(cfg, "boards/3ch-awg-tia/profiles/power.sexp", ctx=ctx, dry_run=True)
```

Аргумент `config_path` у `apply_config` — **не косметика**, та же логика, что у `root_config_path`
выше: когда `cfg.registry_path`/`cfg.track_registry_path` не заданы, они выводятся из него. Одноразовый
путь-заглушка тут перепутал бы или столкнул реестры между несвязанными скриптовыми прогонами.

---

## Как сделать неправильно: пропустить шаг с генерируемым YAML

Соблазнительно пропустить запись `OUTPUT` и пойти прямиком из `build()` в `apply_config()` с `Config`,
собранным только из `clones` этого одного скрипта. Не делай так — вычистке `registry.reconcile()`
(`known_anchor_ids`) нужен **полный** `cfg.clone_placements` (все подсистемы, через `include:`), чтобы
знать, что ещё должно существовать; `Config`, собранный из среза одного скрипта, заставит вычистку
решить, что via/треки ВСЕХ ОСТАЛЬНЫХ подсистем устарели, и удалить их. Всегда: записать сгенерированный
YAML → загрузить настоящий корневой конфиг (который его `include:`-ит) → применить. `cli_main` уже
делает ровно это.

---

## Разбор в лоб: реальный скрипт, `boards/3ch-awg-tia/scripts/dac_channels.py`

Повторяет реальный скрипт в репозитории — читай его параллельно с этим разбором (`AD_DAC_LAYOUT`/
`PASSIVE_LAYOUT`/`OP_AMPS` — таблицы поиска по каналам, не формулы, поскольку DAC каждого канала сидит
на разной стороне FPGA).

### Шаг 1 — сначала посмотреть, потом писать

Перед тем как писать хоть строчку конфига расстановки, используй `explore`, чтобы увидеть, с чем
реально имеешь дело — не угадывай имена Role/Cluster/цепей и не предполагай, что роль уникальна:

```python
from kicadstamp.explore import Board

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.sexp",
                       schematic_dir="../../../test_boards/3CH-AWG-TIA")
board.select(role="AD_DAC").show()
```

### Шаг 2 — выразить повтор через цикл, проверить, что сдвиги не нужно переугадывать на каждый канал

`xy:` — **плоский сдвиг от якоря, никогда не поворачивается автоматически** движком (см. примечание про
три смысла `xy:` в [docs/config_ru.md](config_ru.md)). Скопировать числа сдвига с Channel_0 на
Channel_1/2 с другим поворотом молча поставило бы пассив не туда. `dac_channels.py` решает это,
поворачивая проверенную базовую линию Channel_0 тем же примитивом, что использует сам движок
(`kicadstamp.domain.geometry.Vector2.rotate()`, совпадает с `rotate_local_offset` в `geometry/spoke_layout.py`) —
посчитано один раз, потом визуально проверено живьём в KiCad, не угадано на каждый канал вручную:

```python
AD_DAC_LAYOUT = {
    0: (0.0, 25.0, 270.0),
    1: (25.0, 0.0, 0.0),
    2: (0.0, -25.0, 90.0),
}

def build() -> list:
    clones = []
    for channel, (x, y, rot) in AD_DAC_LAYOUT.items():
        clones.append(ClonePlacement(
            name=f"channel_{channel}_ad9707", role="AD_DAC",
            anchor_role="FPGA", anchor_sheet=f"Channel_{channel}",
            nets={"AD_DAC": f"/Channel_{channel}/DAC/DAC_OUT_P"},
            xy=(x, y), rotation_deg=rot,
        ))
    # ... PASSIVE_LAYOUT/OP_AMPS в той же форме таблицы по каналам
    return clones
```

Цикл `for` физически не может допустить ошибки, которые приходят от копипаста трёх похожих блоков
YAML руками: не тот ключ `nets:`, задвоенная строка `anchor_pad:`, имя листа, скопированное у соседнего
канала — все реальные баги, пойманные при написании этой самой подсистемы руками до того, как её
заскриптовали.

### Шаг 3 — попробовать

```bash
python boards/3ch-awg-tia/scripts/dac_channels.py --apply --dry-run --verbose
```

Затем снова `board.refresh()` + `board.select(...)`, чтобы подтвердить результат тем же инструментом,
которым исследовали неоднозначность в Шаге 1 — замыкает вопрос «сделало ли это то, что я имел в виду»
без открытия KiCad.

### Шаг 4 — применить по-настоящему, держать сгенерированный YAML в git

```bash
python boards/3ch-awg-tia/scripts/dac_channels.py --apply
```

`OUTPUT` (`boards/3ch-awg-tia/generated/dac_channels.sexp`) коммитится — плоский, диффабельный s-expr,
хотя его и написал Python-скрипт. `boards/3ch-awg-tia/profiles/dac_channels.sexp` подхватывает его
через `include:`, обычным путём. Скрипт тоже остаётся в репозитории — повторный запуск после реальной
правки платы (или расширение до 4-го канала) перегенерирует тот же файл, а не правит его руками.

---

## Второй реальный пример: read-only генерация, `build_p3v3_ldo_cell.py`

Не каждому скрипту нужен `cli_main`/`--apply` вообще — `boards/3ch-awg-tia/scripts/build_p3v3_ldo_cell.py`
только читает живую плату (`kicadstamp.explore.Board`, никогда ничего не мутирует), чтобы измерить
реальные позиции падов, а затем пишет определение `Cell` через `dump_template()`:

```python
from kicadstamp.author import dump_template
from kicadstamp.explore import Board
from kicadstamp.utils.units import MM

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.sexp")
ldo_fp = board.select(role="LDO_3V3")[0].fp
origin_x_mm, origin_y_mm = ldo_fp.position.x / MM, ldo_fp.position.y / MM
# ... измерить другие живые позиции падов, вычесть origin ...

dump_template({"p3v3_ldo_composite": {"clone_placements": [...]}},
              "boards/3ch-awg-tia/profiles/templates/p3v3_ldo_composite.sexp")
```

Это общий паттерн для всего, что зависит от геометрии и не выводится безопасно из чисел в YAML одной
арифметикой (сдвиг центр-компонента-к-паду, размеры футпринта) — измеряй против реальной платы через
`explore`, не угадывай руками. Полное обоснование — в докстринге самого скрипта (он существует именно
потому, что у `CellPlacement`, вложенного типа ячейки, вообще нет полей живого якоря — только
буквальный `xy:` относительно родительской ячейки — так что превращение резолвленной якорем позиции в
это буквальное число требует настоящего измерения).

---

## См. также

- [docs/config_ru.md](config_ru.md) — YAML-схема, которую эти Python-объекты отражают поле в поле.
- [docs/commands_ru.md](commands_ru.md) — CLI (`apply`/`extract`), который эти скрипты оборачивают или заменяют.
- [docs/placement_ru.md](placement_ru.md) — что реально делает `apply_config()` после вызова
  (порядок зависимостей, реестр, обработка коллизий) — тот же пайплайн в обоих случаях.
