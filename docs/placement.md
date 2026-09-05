# `kicadstamp/placement` – Placement Planning and Execution

## Purpose

The `placement/` directory contains the core logic for placing components, creating vias, and routing tracks. It orchestrates all stages of the process:

1. **Planning** – calculates target positions for components, vias, and tracks based on spoke templates for three types of placements:
   - **`ManualSpoke`** (`chains`) – binds to pads of the target IC, with automatic refdes selection via a role pool (`ComponentPool`). **Tracks are not supported** in this mode.
   - **`ClonePlacement`** (cloned sections) – reuses a template (`cell:`, mandatory) at multiple board locations, resolving roles either by selection or by explicit nets (`CloneRoleResolver`). Supports **tracks** as part of the template.
   - **`CoordinatePlacement`** (`coordinate_placements`) – moves one EXISTING footprint (found by an exact Cluster+Role match) to an absolute position or relative to another component's anchor. No template, no via/track, no registry — see the dedicated section below.
2. **Execution** – applies moves, creates vias, and creates tracks on the board via the KiCad adapter, split into **three phases** (moves first, then vias, then tracks), with a mandatory board reload between phases.
3. **Logging and undo** – saves operation information as JSON for the `undo` command (including tracks).
4. **Collision checking** – simplified overlap checking for components (optional); track collisions are **not checked** (rely on KiCad DRC).
5. **Idempotency** – skips already‑existing vias, tracks, and components already in place (via `skip_existing_components` and the placement registries for vias and tracks). Since 2026-08-31 the TRACK positional pre-check (`filter_existing_tracks`, see `kicadstamp/registry.py`) runs **unconditionally** in Phase 3 (not only under `skip_existing_components`): it is skip‑only (never deletes/adopts foreign copper), so a repeated redraw of the same cell through two different mechanisms — e.g. a legacy `clone_placement` AND an Entity materialized from a tree — no longer stacks literal duplicates under different registry keys.

All services use the `kicad/adapter.py` adapter, the `geometry/` utilities, and the `config/` configuration package.

## Entity and Placement (2026-08-30) — what vs. where

Since 2026-08-30 the former `ClonePlacement` family splits into TWO concepts (authoritative grammar:
`techdocs/handoff/deepseek/design_2026_08_30_entity_placement_grammar.md`):

1. **`Entity`** (config section `entities:`) — everything about a thing EXCEPT where it stands:
   `cell` (form), `nets`/`params`/`net_overrides` (electrics) and the instantiation identity
   (`cluster`/`sheet`/flags/`layer`/`mirror`/`refs`). **It carries no position fields at all** —
   `xy`/`anchor_*`/`rotation` are forbidden on an Entity.
2. **Placement = a `trees:` node** (`TreeNode`, `kind "placement"`) — the node's `ref` is the
   `Entity.name`, and its `xy`/polar/`rotation` (resolved as `parent_position + node offset`, see
   `kicadstamp/tree_position.py::node_position`) is where that Entity stands.

Trees are the ONLY store of positions; there is no separate `placements:` section. A flat single
placement is a one-node tree under `(anchor (origin))` or a component/point anchor. `kind "clone"`
was renamed to `kind "placement"` (`"clone"` still loads as an alias during the migration), and
`node.ref` for `kind "placement"` resolves to `Entity.name` instead of the old `clone_placements:` list.

At apply time `materialize_entity_placements` (`kicadstamp/placement/entity_placement.py`) walks the
linked trees and produces transient absolute `ClonePlacement`s (Entity fields + live-resolved absolute
position) that feed the SAME planner/executor pipeline as legacy `clone_placements:` — so the
Entity/Placement split is purely a data-model refactor: the execution machinery is unchanged.

A tree's `(anchor (ref ...))` may itself point at an Entity (a placement node of ANOTHER tree) —
since an Entity carries no position, the anchor base is then resolved RECURSIVELY: the placing tree's
own anchor base (origin/ref/role/another Entity ref — recursion, cycle-guarded) + that node's offset.
An Entity referenced by no placement node, by more than one, or through a chain that loops into a
cycle is a CONFIG error — fatal, never silently skipped.

A tree with NO `(anchor ...)` at all (2026-08-31, plan tree_self_anchor_from_entity; reworked
2026-09-05, design_2026_09_05 v2) gets an AUTO anchor: the single top-level `kind "placement"`
node's Entity becomes the anchor subject — its cell's MOUNT role (`anchor_role`; for a not-yet-
migrated legacy cell, the single component at the stored (0,0), the old "zero slot") — narrowed by
the Entity's OWN `sheet`/`cluster`, then resolved LIVE exactly like an explicit
`(anchor (role ...))` (an `anchor_pad` moves the base onto that pad). An explicit `(anchor ...)`
ALWAYS wins; auto applies ONLY to an absent anchor (a literal `(anchor (ref "self"))` is still the
pre-existing cycle-fatal). Config ambiguity (no mount role AND 0/2+ zero slots, or 0/2+ top-level
placement nodes) is a whole-run fatal, never a silent guess; a live role-resolution failure (role
missing or ambiguous on the board) is the usual per-tree skip.

**Migrating a legacy profile:** `tools/convert_placements.py` rewrites `clone_placements:` into
`entities:` + placement trees in place (run on a COPY — it writes a timestamped `.bak` first), and
rewrites pre-existing `kind "clone"` tree nodes to `kind "placement"`. See
[docs/commands.md](commands.md) and [docs/config.md](config.md).

### `channel-copy` (variant Б, live) — the fourth placement path

Alongside the three config-driven placement types above there is
`kicadstamp/channel_copy.py` — a TOP-LEVEL module (not part of `placement/`)
that copies the ENTIRE placement of a channel (components + vias + tracks) from
a source to one or more destination channels **through the live IPC adapter**,
with a single rigid transform (anchor + rotation + optional mirror). Its twin
map is built from `fp.sheet_path.path` UUID chains, not from Role names — so
repeated Role schemes between PIF instances inside one channel are irrelevant
(the structural limit that makes a monolithic extract cell inapplicable to a
whole channel). Idempotency is position+net based (0.01 mm / 0.1°), the
registry is never touched, and `track_matches` (the shared positional
predicate) is reused for track idempotency. CLI: `channel-copy` — see
[docs/commands.md](commands.md).

## CoordinatePlacement (the "dumb placer")

`coordinate_placements:` moves an existing footprint — identified by an exact
Cluster+Role match (Role must already be unique within one Cluster instance) —
to a target position/rotation. No template, no offsets list, no via/track, and
no registry involvement: a move is idempotent by construction. Three mutually
exclusive position modes (see `kicadstamp/config/models.py`'s
`CoordinatePlacement` docstring for the full field semantics):

- **Cartesian (absolute)** – `x_mm`/`y_mm` + a REQUIRED `rotation_deg`.
- **Polar (absolute, around a fixed centre)** – `center_x_mm`/`center_y_mm`/
  `radius_mm`/`angle_deg`; `angle_deg` becomes `rotation_deg` by default.
- **Anchor-relative** (added 2026-08-12, Group 0 consolidation — migrated 1:1
  from ClonePlacement's former `role:`/`cluster:` single-component modes, which
  are gone): `anchor_ref`/`anchor_role` (+ `anchor_sheet`/`anchor_cluster`) or
  `anchor_point` identify a DIFFERENT, stationary component/point, and
  `x_mm`/`y_mm` (Cartesian) or `radius_mm`/`angle_deg` (polar) become the
  OFFSET from that anchor — or from its `anchor_pad`. This is how you place a
  resistor "relative to a specific pad of the FPGA": `anchor_role: FPGA,
  anchor_pad: A17, radius_mm: ..., angle_deg: ...`.

`sheet` — OPTIONAL (2026-08-15), narrows Cluster+Role to ONE physical instance
when the same sheet is cloned/reused (e.g. one PI-filter section instantiated
per channel) and Cluster alone is identical across copies — a reused
hierarchical sheet clones IDENTICAL custom fields onto every instance (Denis,
live: AD_DAC/IC2 exists identically on every channel's cloned sheet). Distinct
from `anchor_sheet` in the anchor-relative mode above — that one narrows the
OTHER, anchor component, not this placement's own identity. Same (Sheet,
Cluster, Role) convention as the rest of the project.

`comment` — OPTIONAL free-form note shown in the GUI (a plain schema field, not
a YAML comment).

`anchor` (`'center'`/`'pad'`, self-referential, absolute modes only) and
`anchor_pad` (self pad in absolute modes, the ANCHOR component's pad in
anchor-relative mode — same semantics as Chain/ClonePlacement) are documented in
the `CoordinatePlacement` docstring.

When the same set of CoordinatePlacement/ClonePlacement entries must be
declared once and instantiated once per reused sheet (Channel_0/1/2), use the
`sheet_templates:` config section instead of copy-pasting the entries — see
[docs/config.md](config.md).

---

## Structure

```
placement/
├── __init__.py                 # Export public components
├── collision.py                # Component collision checking (simplified)
├── commands.py                 # Data structures for commands and component info
├── planner.py                  # Main planner (with track support)
├── interfaces.py               # IPositionCalculator and IViaPlanner interfaces
├── executor/                   # Command executors (split into modules)
│   ├── __init__.py
│   ├── base.py                 # Utilities (layer_to_str)
│   ├── batch_executor.py       # Façade combining moves, vias, and tracks
│   ├── move_executor.py        # Move execution
│   ├── via_executor.py         # Via creation
│   ├── track_executor.py       # Track creation
│   ├── flip_manager.py         # Component flip management
│   └── operation_logger.py     # JSON logging (including tracks)
└── services/                   # Service classes
    ├── __init__.py
    ├── component_pool.py       # Component selection by role and net (for ManualSpoke)
    ├── clone_role_resolver.py  # Role resolution for ClonePlacement (with anchor proximity, by_selection, refs)
    ├── clone_position_calculator.py # Position/via/track calculation for ClonePlacement (with physical anchor)
    ├── manual_position_calculator.py   # Position/via/track calculation for ManualSpoke
    └── via_planner.py          # Thermal via planning and via filtering via registry (live reconciliation)
```

---

## Files and Functions

### `__init__.py`

Exports public classes for convenient imports:
```python
from .executor import BatchExecutor
from .planner import PlacementPlanner
from .commands import MoveCommand, ViaCommand, TrackCommand, PlacedComponentInfo
```

---

### `commands.py`

Defines data transfer objects (DTOs) for passing information between components.

| Class | Fields | Description |
|-------|--------|-------------|
| `MoveCommand` | `ref`, `position`, `angle`, `layer` | Move/rotate component command. |
| `ViaCommand` | `position`, `drill_mm`, `diameter_mm`, `net_name`, `owner_ref`, `registry_key` | Via creation command. `registry_key` is used by the registry (see `registry.py`). |
| `TrackCommand` | `start`, `end`, `width_mm`, `net_name`, `layer`, `owner_ref`, `registry_key` | Track (straight copper segment) creation command. `registry_key` for the track registry (`TrackRegistry`). |
| `PlacedComponentInfo` | `ref`, `dest`, `angle_deg`, `layer` | Information about a placed component. `layer` may be `None` (inherits global layer). |

**Used in:** `planner.py`, `executor/`, `manual_position_calculator.py`, `clone_position_calculator.py`, `via_planner.py`, `registry.py`.

---

### `collision.py`

Simplified component collision checking (using circle approximations). Uses real bounding boxes via the adapter to compute radii (half‑diagonal of the bbox). **Track collisions are not checked** – this is a deliberate decision (rely on KiCad DRC).

| Function | Description |
|----------|-------------|
| `compute_radii(footprints, adapter)` | Computes radii for a list of footprints (batch request via the adapter). |
| `footprints_overlap(pos1, r1, pos2, r2, margin_mm)` | Checks overlap of two circles with a margin. |
| `check_collisions(moves, all_footprints, adapter, ignore_refs, margin_mm)` | Checks collisions between moving components and others. Returns a list of conflicting pairs (ref1, ref2, distance). |

**Used in:** `executor/move_executor.py` (optional, when enabled).  
**Note:** May produce false positives; can be disabled with `--no-collision-check`.

---

### `interfaces.py`

Defines abstract interfaces for position calculators and via planners.

| Interface | Method | Description |
|-----------|--------|-------------|
| `IPositionCalculator` | `compute_raw_positions(target_fp, chains, side)` | Calculates component/via positions for `ManualSpoke` (pad‑based). |
| `IViaPlanner` | `plan_vias(planned_components, planned_vias, target_fp, target_layer)` | Plans vias (thermal vias + registry filtering). |

**Used in:** `planner.py`, `manual_position_calculator.py`, `via_planner.py`.

---

### `planner.py`

**Class `PlacementPlanner`** – the main orchestrator. Coordinates position/via/track calculation for `chains` (via `ManualPositionCalculator`) and `clone_placements` (via `ClonePositionCalculator`). Applies skipping of already‑placed components (`skip_existing_components`). Splits planning into three phases: `plan_moves()`, `plan_vias()`, `plan_tracks()`.

| Method | Description |
|-------|-------------|
| `__init__(adapter, config)` | Initialisation, determines global layer for ManualSpoke. |
| `_already_in_place(ref, dest, angle_deg, layer)` | Checks if the component is already at the target position (layer, position, angle). Tolerances: 0.01 mm for position, 0.1° for angle. |
| `plan_moves()` | Calls `ManualPositionCalculator.compute_raw_positions()` for `chains` and `ClonePositionCalculator.compute_raw_positions()` for `clone_placements`, merges results. Applies `skip_existing_components` to components. Stores `_planned`, `_planned_vias`, `_planned_tracks` for later phases. Returns `MoveCommand[]`. |
| `plan_vias()` | Calls `ViaPlanner.plan_vias()` with the stored data. Returns `ViaCommand[]`. |
| `plan_tracks()` | Returns the stored `_planned_tracks` (no additional processing; collisions not checked). |
| `plan()` | Backward‑compatible wrapper (calls all three phases). Not recommended for production use. |

**Used in:** `kicadstamp_cli.py` to obtain the plan.

---

### `executor/` – Command Executors

The `executor/` directory is split into several modules for readability and testability.

#### `executor/base.py`
Common utilities:
- `layer_to_str(layer)` – converts a `BoardLayer` to `"F.Cu"` or `"B.Cu"`.

#### `executor/operation_logger.py`
Responsible for writing JSON operation logs for `undo`.

| Method | Description |
|-------|-------------|
| `__init__(log_dir)` | Creates the `logs/` folder. |
| `write_operation_log(move_log, via_log, track_log)` | Writes a timestamped JSON file, including tracks. |

#### `executor/flip_manager.py`
Manages component flipping via `adapter.flip_selected` with batching.

| Method | Description |
|-------|-------------|
| `flip_if_needed(moves)` | Checks which components need flipping, flips them in batches, and returns an updated `ref->footprint` dictionary. |

#### `executor/move_executor.py`
Applies component moves. Includes collision checking, flipping, and batching.

| Method | Description |
|-------|-------------|
| `execute_moves(moves, check_collisions, collision_margin_mm)` | Executes moves. Returns `(failed_refs, move_log)`. |

#### `executor/via_executor.py`
Creates vias on the board. Uses the via registry (`PlacementRegistry`) to record created vias (`registry.record_created`).

| Method | Description |
|-------|-------------|
| `execute_vias(vias, registry)` | Creates vias in batches. Returns `(failed_via_owners, via_log)`. |

#### `executor/track_executor.py`
Creates tracks on the board. Uses the track registry (`TrackRegistry`).

| Method | Description |
|-------|-------------|
| `execute_tracks(tracks, registry)` | Creates tracks in batches. Returns `(failed_track_owners, track_log)`. |

#### `executor/batch_executor.py`
A façade combining all execution phases and managing logging.

| Method | Description |
|-------|-------------|
| `__init__(adapter, config, batch_size)` | Initialisation. |
| `execute_moves(moves, ...)` | Calls `MoveExecutor.execute_moves()` and stores the move log in an internal buffer. |
| `execute_vias(vias, registry)` | Calls `ViaExecutor.execute_vias()` and stores the via log in an internal buffer. |
| `execute_tracks(tracks, registry)` | Calls `TrackExecutor.execute_tracks()` and **writes a single JSON log** (combining moves, vias, tracks). |
| `execute(moves, vias, tracks, ...)` | Backward‑compatible wrapper (calls all phases). Not recommended for production. |

**Important:** The operation log is written only after `execute_tracks()` is called (since tracks are the final phase). If there are no tracks, call `execute_tracks([])` to finalise logging.

---

### `services/`

#### `services/component_pool.py`
**Class `ComponentPool`** – selects refdes for roles in `ManualSpoke`. Built once per chain (`chain.net`) and consumed by spokes in order.

| Method | Description |
|-------|-------------|
| `__init__(adapter, net_name, roles)` | Builds the pool: reads all footprints with a `Role` field connected to `net_name`, sorted by natural numeric order. |
| `pop(role, spoke_pad)` | Takes the next component with the given role. If the pool is exhausted, raises `ValidationError`. |
| `remaining_count(role)` | Returns the number of remaining components. |

**Used in:** `manual_position_calculator.py`.

#### `services/clone_role_resolver.py`
Resolves roles for `ClonePlacement`. Supports two modes:
- **by selection** – reads roles from selected components. Only one such clone can be processed per run (due to KiCad's single‑selection limitation). Since Phase 2 step 2.3 this is effectively the explicit `by_selection: true` mode — an implicit clone (no `nets`/`params`) auto-picks by-nets when its cell auto-derives on the live board.
- **by nets** – finds components by expected net. Expected net per role (Phase 2 step 2.1): explicit
  `nets:` → cell `net_template` (with placeholders; a literal local `/Channel_0/...` net is
  auto-prefix-remapped to the target channel, `TwinMap.twin_net` semantics) → auto-derived from the
  live board (`derive_role_nets`: the unique instance's single net, or the one non-rule net shared by
  all candidates). A LITERAL cell `net_template` (no `{param}`, not `/Channel_N/...`) is a
  source-instance fallback, not a selector: when the placement has its own cluster and the live board
  gives a deterministic net for the role there, that live net wins (`derive_role_nets` live_pad,
  2026-09-05) — so a cell extracted from one power rail is safely reusable on another rail without
  dragging the source instance's components (see `tests/test_clone_reuse_cell_across_power_rails.py`).
  A cell `net_template` with an unresolved `{placeholder}` (no matching `params`) is
  NOT treated as an explicit source — it falls through to the live auto-derivation path (Phase 4 step
  4.3) instead of fataling — so `nets:`/`params:`/`net_overrides:` are OPTIONAL overrides. Since Phase 2 step
  2.3 the IMPLICIT mode (no `nets`/`params`/`by_selection`) is also by-nets whenever the whole cell
  auto-derives on the live board — mode is chosen by the availability of an unambiguous source
  instance, not by the presence of `nets`/`params`. In case of ambiguity,
  uses cascading narrowing: explicit `refs` → selection → sheet hierarchy → **physical proximity to
  the anchor** (if the distance gap is sufficient, the closest candidate is chosen). This allows
  distinguishing electrically identical filters on a common rail. The GUI's Auto-fill/Nets/Params
  narrowing (PlacerDock) uses the same `sheet` dimension for its live-board candidate search since
  2026-08-16.
- **Two matching mechanisms, never conflated** (Phase 2 step 2.2): the by-nets path NEVER applies
  net-matching (Kuhn) to INSTANCE selection — N identical instances on a shared/global net are
  disambiguated only by the cascade (sheet → Cluster → selection → proximity); if they are truly
  indistinguishable it is an honest fatal, never a Kuhn guess. Kuhn/SCC (the Role↔Net correspondence
  from `net_matching`) is a full two-cluster-snapshot mechanism used for trace-transfer verification
  (`channel_copy`/`clone-plan`), where full snapshots exist — not for picking among identical
  components in apply.

Functions:
- `clone_uses_selection_mode(clone, *, adapter, cell, sheet_names)` – determines the mode (considers `by_selection`, `nets`/`params`, and — Phase 2 step 2.3 — the availability of an unambiguous source instance on the live board: an implicit clone whose cell auto-derives is by-nets, else by-selection).
- `resolve_roles_by_selection(adapter, template, clone_name)` – by selection.
- `resolve_roles_by_nets(adapter, template, clone, anchor_position)` – by nets with anchor proximity; expected nets auto-derive from the live board when not explicit (Phase 2 step 2.1).
- `resolve_anchor_by_role(adapter, clone)` – finds the anchor by the `Role` field (alternative to `anchor_ref`).

**Used in:** `clone_position_calculator.py`.

#### `services/clone_position_calculator.py`
**Class `ClonePositionCalculator`** – calculates absolute positions of components, vias, and tracks for `ClonePlacement`. Uses `apply_clone_geometry` and `clone_role_resolver`.

| Method | Description |
|-------|-------------|
| `_resolve_anchor(clone)` | Returns the absolute anchor point (pad centre or footprint centre) or `None`. Handles `anchor_ref`/`anchor_pad` and `anchor_role`/`anchor_sheet`. |
| `compute_raw_positions(clone_placements)` | For each clone, determines the mode, obtains `role_to_ref`, calls `apply_clone_geometry` (respecting `mirror`), returns `(PlacedComponentInfo[], ViaCommand[], TrackCommand[])` with correct `registry_key` (anchor_id is based on physical binding). |

**Used in:** `planner.py`.

#### `services/manual_position_calculator.py`
**Class `ManualPositionCalculator`** – calculates component, via, and track positions for `ManualSpoke` based on IC pads. Implements `IPositionCalculator`. A `TemplateTrack.net = None` inherits `chain.net` (same convention as `TemplateVia`), which is what lets one template (e.g. `cap_pair_standard`) be reused across chains with different nets.

| Method | Description |
|-------|-------------|
| `compute_raw_positions(chains)` | For each chain, builds a `ComponentPool`, for each spoke calls `apply_spoke_geometry`, returns `(PlacedComponentInfo[], ViaCommand[], TrackCommand[])`. |

**Used in:** `planner.py`.

#### `services/via_planner.py`
**Class `ViaPlanner`** – implements `IViaPlanner`. Responsible for:
- Filtering existing vias via the registry (reconciling with real vias on the board via `adapter.get_vias()`).
- Planning thermal vias (array under the thermal pad) with free‑space search via `find_free_point`.

| Method | Description |
|-------|-------------|
| `_via_already_exists(existing_vias, position, net_name)` | Checks if a via with the given net and position exists (tolerance 0.01 mm). |
| `plan_vias(planned_components, planned_vias, target_fp, target_layer)` | Filters `planned_vias` via `skip_existing_components` (compares against real vias on the board), builds keepout, calls `_plan_thermal_vias`. |
| `_build_keepout(target_fp, planned, exclude)` | Builds keepout from pads of the IC and components. |
| `_plan_thermal_vias(planned, target_fp, keepout, existing_vias)` | Generates thermal vias with free‑space search. |

**Used in:** `planner.py` (after moves).

---

## Relationships with Other Modules

- **`kicad/adapter.py`** – board operations (reading, writing, transactions, creating vias and tracks).
- **`geometry/spoke_layout.py`** – template transformation for `ManualSpoke` (vias and tracks).
- **`geometry/clone_geometry.py`** – transformation for `ClonePlacement` (vias and tracks, with mirror).
- **`geometry/thermal_grid.py`** and **`geometry/keepout.py`** – thermal vias and keepout.
- **`config/`** – configuration package (loader.py, models.py, includes.py, __init__.py).
- **`validation.py`** – pre‑validation (including via/track nets).
- **`registry.py`** – via (`PlacementRegistry`) and track (`TrackRegistry`) registries with live reconciliation.
- **`net_resolution.py`** – net resolution with placeholders.
- **`constants.py`** – tolerances, field names, timeouts.
- **`utils/units.py`** – `MM` constant for unit conversion.

---

## Usage Notes

- **Three‑phase process** (mandatory for correct thermal via handling and idempotency):
  1. Execute `plan_moves()` → `execute_moves()`.
  2. Execute `adapter.refresh_board()`.
  3. Execute `plan_vias()` → `execute_vias()` (with via registry).
  4. Execute `plan_tracks()` → `execute_tracks()` (with track registry).
  This is implemented in `kicadstamp_cli.py:cmd_apply()`.

- **Collisions** – checked only for components (optional); tracks are not checked (rely on KiCad DRC). Disable with `--no-collision-check`.

- **Operation logging** – saved to `logs/operation_*.json` and used by `undo` (including tracks). The log is written after `execute_tracks()` is called.

- **Dry‑run** – shows moves, vias, and tracks. Thermal vias may differ slightly due to keepout, which is normal.

- **Idempotency** – enabling `skip_existing_components: true` allows safe re‑runs. The via and track registries prevent duplication (reconciling with real objects on the board).

- **Automatic refdes selection** – for `ManualSpoke` via `ComponentPool` using the `Role` field. For `ClonePlacement` – two modes (selection or nets) with disambiguation by anchor proximity (including `refs` for extreme cases).

- **Section cloning** – for repeated templates, use `clone_placements` (expected nets auto-derive from the live board since Phase 2 step 2.1; explicit `nets`/`params` remain optional overrides) and run without selection; for one‑off instances, use selection mode (no `nets`/`params`, or with `by_selection: true`) and select components in KiCad before running.

- **Tracks** – only supported in `ClonePlacement`. When extracting a template (`extract`), tracks are automatically included (if selected). When cloning, they are created together with components and vias.

- **Layer placement** – each component may have its own layer (per‑placement); for `ManualSpoke`, the global `layer` from the config is used. When mirroring (`mirror`), layers are inverted.

- **Anchor by role** – instead of `anchor_ref`, you can use `anchor_role` (the `Role` field of the anchor component). This survives re‑annotation. You can further narrow the search with `anchor_sheet` (local net prefix) or `anchor_pad`.

- **Explicit refs** – in `ClonePlacement`, you can specify `refs: {role: refdes}` as a last resort when candidates are indistinguishable by nets, selection, or proximity.
