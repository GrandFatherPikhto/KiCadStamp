# Что лежит в чём (Ячейки / Сущности / Деревья / Импринты / Медь)

Кондуит по факту прочитанного кода (`kicadstamp/config/models.py`, `kicadstamp/trees.py`,
`kicadstamp/config/format3.py`, `kicadstamp/registry.py`).

**Сверено с кодом на `cace12fedcca4cafed68b00c32c69b48b6e63d5e`, 05.10.2026** (SHA — как в шапке
`index.md`). Старая версия от 03.09 рядом: `techdocs/me/structures_2026_09_03.md` (для сравнения).

---

## Спицы (Chain + ManualSpoke)

```
Chain (было "Rule" до 2026-09-01)
  net                             — сеть, вокруг которой всё
  anchor_ref  ИЛИ anchor_role     — на чей pad садятся спицы (ровно один)
  anchor_sheet / anchor_cluster   — сузить anchor_role
  anchor_point (+ anchor_point_uuid) — альтернатива (запись points:)
  sheet / name / retired / skip / comment / uuid
  spokes: [ManualSpoke, ...]
    pad                           — какой pad анкера
    cell (+ cell_uuid)            — какая Cell тут стоит
    shift_x_mm/shift_y_mm ИЛИ radius_mm/angle_deg — сдвиг origin спицы
    rotation_deg / cluster / retired / skip
    (реальных ref НЕТ — компонент берётся из пула по (net, Role) на лету)
```

## Ячейки (Cell) — универсальная геометрия, без листа/кластера/ref

```
Cell
  name
  layer                     — F.Cu/B.Cu (по умолчанию "F.Cu")
  anchor_xy / anchor_role / anchor_pad   — для отображения/якоря; uuid — идентичность (формат 3)
  comment
  vias: [TemplateVia, ...]
    offset_along_mm / offset_across_mm, drill_mm, diameter_mm
    net  ИЛИ  net_from_role / net_from_role_pad
  components: [TemplateComponentSlot, ...]
    role                    — уникально внутри одной Cell
    offset_along_mm / offset_across_mm / angle_deg
    net_template / net_template_pad / net_template_same_as_role
    layer
    vias: [TemplateVia, ...]        <-- у КАЖДОГО компонента свои via
  tracks: [TemplateTrack, ...]
    start_along_mm/start_across_mm → end_along_mm/end_across_mm, width_mm
    net  ИЛИ  net_from_role / net_from_role_pad ; layer
  clone_placements: [CellPlacement, ...]   <-- РЕКУРСИЯ: Cell внутри Cell
    name, cell (ИЛИ role — ровно один), xy, rotation_deg, mirror, layer
    sheet / cluster, nets / params / net_overrides / refs
    (закрытая граница: внутри placement не видит ничего снаружи родительской Cell)
```

Всё локально (along/across от origin) и rotation-invariant: описано один раз при rotation_deg=0,
реальное размещение поворачивает целиком.

## Сущности (Entity) — «что», без позиции

```
Entity
  name                      — обязательно, уникально по include-графу
  cell (+ cell_uuid)  ИЛИ  imprint (+ imprint_uuid)   — ссылка на ячейку/импринт
  cluster / sheet           — идентичность экземпляра (живой поиск по Role+Sheet+Cluster)
  nets / params / net_overrides
  by_selection / refs       — резолв ролей: по выделению или явный role -> ref
  layer / mirror / retired / skip / ignore_selection / comment / uuid
```

**Позиции у Entity нет** — она появляется, только когда узел дерева (`kind="placement"`) сослался
на неё по `ref == entity.name`. Старый самостоятельный аналог — `ClonePlacement`: те же поля плюс
свои `xy`/`anchor_*`/`rotation_deg`; официально не заменён, но новое строится вокруг Entity+Tree.

## Деревья (Tree / TreeNode / TreeAnchor)

```
Tree
  name
  anchor: TreeAnchor         — база позиции ВСЕГО дерева, ровно один вариант:
    is_origin / ref / role (+anchor_sheet/anchor_cluster/anchor_pad) / point / is_auto
  nodes: [TreeNode, ...]
    TreeNode (РЕКУРСИВНЫЙ)
      ref                    — Entity.name (placement) / net_traces / другое Tree (module) / …
      kind                   — placement / net_trace / module / chain / coordinate /
                               point / external / clone(legacy) / None
      xy ИЛИ polar ; rotation ; name ; group
      children: [TreeNode, ...]   <-- смещения детей от ЭТОГО узла
      pivot_xy / pivot_polar — только kind="module"
```

**`tree_instances:`** — декларация `{template, name, sheet, cluster?, params?, anchor?, rotation?}`;
разворачивается в полноценное дерево + Entity(и) заново при КАЖДОЙ загрузке, копией не хранится.

## Импринты (`imprints:`) — захваченный отпечаток постановки

```
ImprintConfig
  name / pivot / source_sheet / scope_sheet_paths / scope_presets
  components: [ImprintComponentRecord]   ref, offset_along/across_mm, rotation_deg
  vias: [ImprintViaRecord]               offset_along/across_mm, drill_mm, diameter_mm, net
  tracks: [ImprintTrackRecord]           start/end along/across, width_mm, layer, net
  boundary_nets: [ImprintBoundaryNet]    net, action(exclude/…), external_ref
  uuid
ImprintScopePreset — name, sheet_paths: [[…]]
```

## Термо-матрицы (`thermal_via_arrays:`)

```
ThermalViaArrayConfig
  name (обязательно в YAML) / retired / skip / comment / uuid
  anchor_ref ИЛИ anchor_role(+anchor_sheet/anchor_cluster) ИЛИ anchor_point(+anchor_point_uuid)
  pad / net / rows / cols / margin_mm / pattern / drill_mm / diameter_mm
```

## Точки (`points:`)

`Point` — именованная точка (абсолютная `x_mm/y_mm` либо сдвиг от футпринта). Ссылки на неё —
`anchor_point` + `anchor_point_uuid` у Chain / ClonePlacement / ThermalViaArrayConfig.

## Межкластерная медь (`net_traces:`)

```
NetTrace
  net                       — СЕТЬ (атрибут), НЕ ключ
  anchor_role (обязательно) / anchor_sheet / anchor_cluster / anchor_pad
  name                      — ИДЕНТИЧНОСТЬ (--only/SAVE и template_name реестра); по умолчанию = net
  pads: [ "ROLE.pad", … ]   — подпись юнита: по НЕЙ матчится перечитывание, не по геометрии
  anchor_rotation_deg       — поворот анкера НА МОМЕНТ ЗАХВАТА (композиция дельты на apply)
  tracks: [TemplateTrack] / vias: [TemplateVia]   — локальные along/across от анкера
  retired / skip / comment / uuid
```

## Реестр (via / track) — ключ и UUID

Реестр — ДВА JSON-файла (via + track), путь даёт `registry_paths_for_config` (реально в
`kicadstamp/utils/paths.py:114`). Ключ записи: `anchor|template|role|index`:
- **anchor** — `name:<uuid-или-имя>` / `anchor:<ref>` / `role:…` / `point:…` / `thermal:` / `pad:` / `net:`;
- **template** — идентичность ячейки/записи (`record_key_part`: UUID в формате 3, имя в формате 2);
- **role** — роль компонента (или `__spoke__` для спиц-уровня);
- **index** — номер в списке vias/tracks.

Строит ключ `make_registry_key` (`kicadstamp/registry.py:102`); читает идентичность
`record_key_part` (`:121`).

## Плата (доменные DTO адаптера)

Авторский код читает плату через `IBoardAdapter` (доменные DTO, не kipy). У `Pad`
(`kicadstamp/domain/board.py`) с 2026-10-05 есть поле
`copper_layers: tuple[BoardLayer, ...] | None` — медные слои пада из kipy
`padstack.layers` (только медные; заполняет маппер `pad_from_kipy`). `None` =
«неизвестно» = ВСЕ медные слои (историческое чтение — старые подделки и дампы
ведут себя как сквозной пад, править `tests/fakes` не нужно). Нужно «Разносу»
(`kicadstamp/explode.py`): дорожка соединяется с падом ТОЛЬКО на слое, который у
пада есть, — SMD F.Cu не должен читаться соединённым с дорожкой B.Cu под ним по
одному XY.

## Папки и UUID (как выглядит в файле)

Идентичность записи в формате 3 — `uuid`; в s-expr рядом с именем:
`(cell "x" (uuid "…"))`, ссылки — `cell_uuid` / `anchor_point_uuid` / `imprint_uuid`.
Таблица папок — `folders` в `Config`.

## Сводная лестница вложенности

```
Tree
 └─ TreeNode (kind=placement, ref=Entity.name)
     └─ Entity (name, cluster, sheet, nets/params…)
         └─ Cell (entity.cell)
             ├─ TemplateComponentSlot (role, offset, net_template)
             │    └─ TemplateVia (свои offset/net)
             ├─ TemplateVia (на уровне cell)
             ├─ TemplateTrack
             └─ CellPlacement (рекурсия: другая Cell, свои xy/sheet/cluster/nets)
```

## Config — разделы верхнего уровня

`layer`, `cells`, `points`, `thermal_via_arrays`, `imprints`, `chains`, `entities`,
`clone_placements`, `coordinate_placements`, `net_traces`, `trees`, `tree_instances`,
`folders`, плюс служебные (`registry_path` / `track_registry_path`, `root_sheet`,
`schematic_dir`/`schematic_files`, `role_cluster_source`, `board_name`, `log_file`…).
