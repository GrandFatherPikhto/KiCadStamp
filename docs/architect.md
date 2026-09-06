# KiCadStamp Architecture (Current Version)

## 1. Overview

**KiCadStamp** is a command‑line tool for automated placement of components, vias, and tracks on PCBs in **KiCad 10** using **spoke templates**. It is an advanced script‑based alternative to the KiCad Replicate Layout plugin, designed for complex multi‑channel projects and automated design reuse via the IPC API.

The tool connects to a running KiCad instance via IPC (the `kipy` library) and performs:

- Moving components to specified positions according to spoke templates.
- Creating vias at the spoke level and at the component level.
- **Creating tracks** (straight copper segments) that are part of the template – they are cloned together with components and vias.
- Undo of the last operation (restores the board to its original state).
- Extracting templates from the current PCB selection (command `extract`) with support for JSON output and net parametrisation.
- **Template cloning** (`ClonePlacement`) – reusing a single template at multiple board locations with different nets and parameters.
- **Disambiguation** of component selection by physical proximity to the anchor.

The core concept is a **spoke** – a set of components, vias, and tracks that belong to a single IC pin or to an arbitrary point on the board (for cloned sections). The entire geometry of a spoke is described in **local coordinates (along, across)** as a **template**, which can be applied to any pad of the target IC (with shift and rotation) or to any board location (for ClonePlacement).

---

## 2. Architecture Principles

| Principle | Description |
|-----------|-------------|
| **Separation of concerns** | Clear layering: CLI, configuration, validation, geometry, KiCad adapter, business logic, registry, logging, undo. |
| **Separation of data and logic** | Templates (geometry) are machine‑generated data (only via `extract`); placement (cloning logic) is human‑written Python or hand‑authored `.sexp`. Templates are stored separately (external files listed under `include:`, wrapped in a `cells:` key). |
| **Encapsulation of IPC complexity** | All KiCad calls are concentrated in the adapter, which exposes a uniform `IBoardAdapter` interface. |
| **Extensibility** | New via types, roles, templates, and tracks can be added easily thanks to the modular structure. |
| **Safety** | Three‑phase execution (moves → vias → tracks) with intermediate board reload; pre‑validation before any modifications; logging and undo. |
| **Idempotency** | Repeated runs do not duplicate already correctly placed items (via registry reconciliation against live board objects). |
| **Determinism** | Automatic component selection from pools by role and net, sorted by natural numeric order; disambiguation by anchor proximity. |
| **Physical IR** | The internal model stores only physical facts (coordinates, pad geometry); semantics (banks, roles) are stored as tags/metadata, not baked into the structure. |

---

## 3. Project Structure

```
Repo root
├── pyproject.toml                     # packaging (PEP 621): metadata, deps, console scripts
├── kicadstamp_cli.py                  # thin wrapper -> kicadstamp.cli_main:main (dev workflow)
├── kicadstamp_gui.py                  # thin wrapper -> kicadstamp.gui_main:main (dev workflow)
├── fieldstool_cli.py                  # offline .kicad_sch bulk field editor (set/rename)
├── requirements*.txt                  # runtime / dev / diagnostics split
├── tests/                             # unit + GUI tests (integration in tests/integration_tests)
└── kicadstamp/
    ├── __init__.py                    # imports __version__ only (no import-time side effects)
    ├── _version.py                    # single source of truth for __version__
    ├── cli_main.py / gui_main.py      # package entry points (console scripts)
    ├── apply_pipeline.py              # cmd_apply / ApplyPipeline
    ├── cli.py / cli_common.py / cli_extract.py   # CLI command wrappers + exit-code owner
    ├── author.py / author_cli.py      # scripting helpers + board-script CLI
    ├── channel_copy.py / flatten.py   # channel copy + include-graph flattening
    ├── anchor_graph.py                # static anchor-dependency graph
    ├── cluster_matching.py / net_resolution.py / net_from_role_resolver.py
    ├── net_trace_extract.py / net_trace_planner.py
    ├── schematic_blocks.py / schematic_config.py / schematic_discovery.py /
    │   schematic_editing.py / schematic_rename_fields.py / schematic_safety.py /
    │   schematic_set_fields.py
    ├── template_extraction.py / template_extraction_render.py / template_selection.py
    ├── extract_writer.py / config_writer.py / config_rename.py
    ├── explore.py / validation.py / registry.py / undo.py / sheet_names.py
    ├── runtime_context.py / exceptions.py / constants.py / i18n.py / logging_setup.py
    ├── domain/                        # board DTOs + geometry value types (the kipy-free public types)
    ├── config/                        # entries, includes, loader, models, points, sheet_templates
    ├── geometry/                      # clone_geometry, keepout, pad_projection, spoke_layout, thermal_grid
    ├── kicad/                         # adapter, interfaces, pynng_safety
    ├── placement/                     # planner, collision, commands, dependency_order + executor/ + services/
    ├── cloner/                        # file-based cloner (extract, models, netlist, pcb, sexp)
    ├── utils/                         # file_cache, layers, paths, units, yaml_loader
    └── diagnostics/                   # diagnostic probes (a second diagnostics/ also exists at repo root)
```

---

## 4. Architecture Layers (bottom‑up)

### 4.1. KiCad Access Layer (`kicad/`)

- **`adapter.py`** – implements `KiCadBoardAdapter`, encapsulating all `kipy` calls. Provides methods for searching components, pads, zones, nets; getting bounding boxes; transactions (commit/rollback); creating vias **and tracks**; executing flips; reading user fields; retrieving selection with group expansion; deleting objects by UUID.
- **`interfaces.py`** – defines the abstract `IBoardAdapter` interface, enabling easy substitution (e.g., for testing with mocks).
- **`domain/` (types outside `kicad/`)** – `board.py` (DTOs: Footprint/Pad/Via/Track/Net/Zone) and `geometry.py` (value types: Vector2/Angle/BoardLayer/Box2). The adapter converts kipy → domain at the boundary, so `IBoardAdapter` and every consumer use these domain types, never `kipy.*`.

### 4.2. Geometry Utilities (`geometry/`)

Modules that work with coordinates, independent of KiCad:

- **`keepout.py`** – `Rect` (AABB) class and functions for building keepout areas, checking point clearance, finding free positions.
- **`pad_projection.py`** – predicts pad position after moving/rotating the component, accounting for flipping.
- **`spoke_layout.py`** – transforms template local coordinates (`along/across`) to absolute board coordinates for `ManualSpoke` (pad‑based). Generates vias and **tracks** using `chain.net` as default net.
- **`clone_geometry.py`** – similar transformation for `ClonePlacement`, but without pad binding: uses an absolute origin `(origin_x, origin_y)`, resolves via/track nets via `net_resolution` (with placeholder support). Supports mirroring (`mirror`).
- **`thermal_grid.py`** – generates thermal via grids under pads.

### 4.3. Business Logic (`placement/`)

- **`planner.py`** – `PlacementPlanner` is the main orchestrator. It calls `ManualPositionCalculator` for `chains` and `ClonePositionCalculator` for `clone_placements` (which also returns tracks). Then it builds move, via, and track commands. Implements skipping already‑placed components.
- **`executor/`** – split into modules for readability and testability:
  - `FlipManager` – flip management.
  - `MoveExecutor` – move execution.
  - `ViaExecutor` – via creation.
  - `TrackExecutor` – track creation.
  - `OperationLogger` – writes JSON logs (including tracks).
  - `BatchExecutor` – façade orchestrating all phases and logging.
- **`interfaces.py`** – defines `IPositionCalculator` and `IViaPlanner` to allow easy replacement of implementations.
- **`services/`**:
  - `ComponentPool` – collects components by `Role` field and net, sorts them, assigns to spokes (for `ManualSpoke`).
  - `CloneRoleResolver` – resolves roles for `ClonePlacement` in two modes: by selection (reads roles from selected components) and by nets (explicit net assignment with placeholders via `net_resolution`). In net mode, **disambiguation** is performed by physical proximity to the anchor (if multiple candidates exist, the closest one is chosen if the distance margin is sufficient). Supports `anchor_role`, `anchor_sheet`, `by_selection`, and explicit `refs`.
  - `ManualPositionCalculator` – implements `IPositionCalculator` for `chains`: builds a pool, applies `apply_spoke_geometry`, returns `PlacedComponentInfo` and `ViaCommand`.
  - `ClonePositionCalculator` – implements calculation for `clone_placements`, using `apply_clone_geometry` and `clone_role_resolver`, returns components, vias, and tracks.
  - `ViaPlanner` – implements `IViaPlanner`: handles only thermal vias and existing‑via filtering via the registry (`skip_existing_components`).

### 4.4. Configuration and Validation

- **`config/` package** (`__init__.py`, `loader.py`, `models.py`, `includes.py`) – defines dataclasses for all config structures (`Config`, `SpokeTemplate`, `ManualSpoke`, `ClonePlacement`, `TemplateVia`, `TemplateTrack`, `TemplateComponentSlot`, etc.) and the `load_config()` function. The `includes.py` module handles `include:` directives for splitting configs across multiple files, including external template files (wrapped in a `cells:` key, merged with any inline `cells:`) – this allows moving heavy geometry out of the main config.
- **`validation.py`** – pre‑validation checks: template/pad existence, component pool sufficiency, clone correctness (no more than one selection‑based clone per run), **resolution of via/track nets against actual board nets**, uniqueness of clone names/physical anchors, validity of `layer`/`mirror` combinations. Throws `ValidationError` with a clear list of issues.

### 4.5. Placement Registry (`registry.py`)

- Ensures idempotency of vias and tracks across runs.
- Stores via/track information (UUID, position, net, parameters for vias; start/end, width, layer for tracks) in JSON files with composite keys `anchor_id|template_name|role|via_index` (and similar for tracks).
- For `ManualSpoke`, `anchor_id = pad:{pad}`; for `ClonePlacement`, `anchor_id` can be `name:{clone.name}`, `anchor:{ref}:{pad}`, or `role:{role}:{sheet}:{pad}` depending on the anchor type.
- On subsequent runs, **compares planned vias/tracks with real objects on the board** (`adapter.get_vias()`, `adapter.get_tracks()`) rather than only the JSON record. This prevents desynchronisation due to manual deletions or crashes.
- Removes obsolete vias/tracks (prune) and creates only those that are new or have changed position/parameters.

### 4.6. Net Resolution for Cloning (`net_resolution.py`)

- Implements three‑step net name resolution for `ClonePlacement`: literal → placeholder substitution from `params` → `net_overrides`.
- Also provides **reverse parametrisation** (`parametrize_net`) for `extract`: given a literal net name and a `net_template_map`, it reconstructs the placeholder pattern and verifies round‑trip.

### 4.7. Template Extraction (`template_extraction.py`)

- Implements the `extract` command: from the current PCB selection, extracts a spoke template (components, vias, and **tracks**) and builds a structure ready for serialisation.
- Supports **JSON output** (when extension is `.json`) – writes `{cells: {template_name: {...}}}`, ready to be listed directly under `include:`.
- Supports **net parametrisation** via `--net-template` and `--param`: replaces literal net names with placeholder patterns.
- Supports **choosing origin** not by bbox but by a specific via or component (`--origin-by-via-net`, `--origin-by-component-role`).
- Automatically determines the template layer and sets explicit layers for elements lying on other layers.

### 4.8. Template Transformation (`utils/transform_template.py`)

- A separate script for post‑processing existing templates (s-expr, `.sexp`). Allows **rotation**, **mirroring** (X or Y), and **origin shift** to a specified via (by index or net) or component (by index or role), or explicit offset.
- Order: first origin shift (if any), then rotation and mirroring – ensuring the target element ends up at (0,0) after all transformations.

### 4.9. Undo (`undo.py`)

- `undo_last_operation()` reads the latest JSON log and restores the board (moves components back to original positions/layers, removes created vias and tracks).

### 4.10. User Interface (CLI)

- **`kicadstamp_cli.py`** – the main executable, handling argument parsing, config loading, KiCad connection, validation, planning, and execution (three phases: moves → vias → tracks), plus `undo`, `extract`, `clone-extract`, and the optional `--only` flag for processing only the named `chains`/`clone_placements`/`thermal_via_arrays`.

---

## 5. Module Interaction (Workflow)

```
1. CLI start (kicadstamp_cli.py)
   │
   ├── Parse arguments (argparse)
   ├── Setup logging
   │
2. Config loading (load_config)
   │
   ├── Read config (.sexp)
   ├── Resolve include: (merges external cells:/chains:/clone_placements:/etc.)
   ├── Merge with inline templates
   ├── Check role uniqueness in templates
   ├── Build Config object
   │
3. Connect to KiCad
   │
   ├── Create KiCadBoardAdapter
   ├── adapter.refresh_board() — retrieve board
   │
4. Validation (run_all_checks)
   │
   ├── check_clone_templates_exist()
   ├── check_single_selection_based_clone()
   ├── check_templates_and_pads_exist()
   ├── check_role_pool_sufficiency()
   ├── check_clone_nets_exist_on_board()  # via/track nets + role expected nets (nets:/net_template, Phase 2 step 4.1) against real nets
   │
5. Planning (PlacementPlanner)
   │
   ├── plan_moves()
   │   ├── For each rule:
   │   │   ├── Build ComponentPool
   │   │   ├── For each spoke:
   │   │   │   ├── pop() – get refdes for each role
   │   │   │   ├── apply_spoke_geometry() – compute positions (and vias)
   │   │   │   └── generate ViaCommand and PlacedComponentInfo
   │   │
   │   ├── For each clone_placement:
   │   │   ├── Determine mode (selection or nets)
   │   │   ├── Call clone_role_resolver to get role_to_ref (with anchor proximity)
   │   │   ├── apply_clone_geometry() – compute positions, vias, tracks
   │   │   └── generate PlacedComponentInfo, ViaCommand, TrackCommand
   │   │
   │   └── Return lists of MoveCommand, ViaCommand, TrackCommand
   │
   ├── (If --dry-run – print plan and exit)
   │
6. Execution (three phases)
   │
   ├── Phase 1: Moves
   │   ├── executor.execute_moves()
   │   │   ├── Collect original states (for undo)
   │   │   ├── Check collisions (optional)
   │   │   ├── Flip if layer changed
   │   │   ├── Move in batches via commit_with_retry
   │   │   └── Save move log
   │   │
   │   ├── adapter.refresh_board() – reload board
   │   │
   ├── Phase 2: Vias
   │   ├── planner.plan_vias() – apply registry filtering and thermal vias
   │   ├── executor.execute_vias()
   │   │   ├── Create vias in batches
   │   │   ├── Collect UUIDs (for undo and registry)
   │   │   └── Record in via registry (record_created)
   │   │
   ├── Phase 3: Tracks
   │   ├── planner.plan_tracks() – simply return planned tracks (no keepout)
   │   ├── executor.execute_tracks()
   │   │   ├── Create tracks in batches
   │   │   ├── Collect UUIDs (for undo and registry)
   │   │   └── Record in track registry
   │   │
   └── Write single JSON operation log (including moves, vias, tracks)
       └── Print result (success/errors)
```

---

## 6. Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **Decouple vias/tracks from live board** | All vias and tracks (except thermal vias) are computed solely from template geometry. This makes them independent of the current board state and allows planning before moves. |
| **Three‑phase execution** | Required for correct thermal via handling and for idempotency (vias/tracks are created after moves to check their existence in final positions). |
| **Idempotency via live‑board reconciliation** | The registry stores UUIDs, but on subsequent runs it compares against real vias/tracks on the board, not just the JSON. Prevents desynchronisation after manual edits or crashes. |
| **Automatic refdes selection by roles** | Eliminates the need to write component names in the config, making it compact and resilient to re‑annotation. |
| **Disambiguation by anchor proximity** | In net mode, if multiple candidates exist for a role, the closest to the anchor is chosen (if the distance margin is sufficient). Solves the common issue of power filters sharing a common rail. |
| **Template cloning with tracks** | Copies not only components and vias but also routing segments, preserving connectivity and reducing manual rerouting. |
| **External template files (via `include:`)** | Heavy geometry (especially tracks) is moved out of the main config, keeping it readable. Templates are generated by `extract` and never edited manually. |
| **JSON format for templates** | Simplifies serialisation and eliminates hand‑authored quoting/indentation issues. |
| **Template transformation** | Allows adapting a template to different orientations without re‑extracting. |
| **Logging and undo** | Every operation is saved as JSON, enabling rollback (including deletion of tracks). |
| **Modular and integration tests** | Unit tests with mocks protect against regressions; integration tests with real KiCad validate end‑to‑end operation. |

---

## 7. Design Patterns Used

| Pattern | Application |
|---------|-------------|
| **Adapter** | `KiCadBoardAdapter` provides a unified interface for `kipy`. |
| **Command** | `MoveCommand`, `ViaCommand`, `TrackCommand`, `PlacedComponentInfo` encapsulate actions. |
| **Builder** | `apply_spoke_geometry` and `apply_clone_geometry` build `SpokeLayout` from parts (vias, components, tracks). |
| **Memento** | JSON logs store state for undo. |
| **Template Method** | `BatchExecutor` uses a common template for moves, vias, and tracks (batches, transactions). |
| **Dependency Injection** | All services receive `adapter` and `config` via constructor. |
| **DTO** | Dataclasses in `config.py` and `commands.py` are pure DTOs. |
| **Strategy** | Two role‑resolution strategies in `clone_role_resolver` (selection vs nets). |
| **Factory** | `load_config` creates config objects; `create_via`, `create_track` in the adapter. |

---

## 8. Use of Unstable and Undocumented APIs

| Element | Status | Reason |
|---------|--------|--------|
| `kicad.run_action("pcbnew.InteractiveEdit.flip")` | **Unstable** | The only way to perform a true flip with pad and silk‑screen mirroring. |
| `Group.proto.items` | **Undocumented** | Needed to get group members (`.items` is always empty). |
| `footprint.texts_and_fields` | **Undocumented** | Used to read user fields (`Field`). |
| `footprint.definition.items` | **Undocumented** | Used to obtain component pads. |
| `board.remove_items_by_id()` | **New, potentially unstable** | Added recently (July 2025), used in the registry to delete vias and tracks. |
| `board.get_tracks()` | **Undocumented** | Used to retrieve all tracks on the board for registry reconciliation. |

All such uses are **documented in the code** and covered by unit tests.

---

## 9. Dependencies and Requirements

- **Python 3.8+** (recommended 3.9+).
- **KiCad 10.0.4+** with IPC API enabled.
- **`kipy`** – official Python client for KiCad IPC (version 0.7.1 or newer).
- Additional packages: `pyyaml`, `sexpdata`, `json5` (optional), `tomli` (optional).

---

## 10. Architecture Evolution (Discussion Summary)

During the architectural discussions (May–July 2026), the following decisions were reached and are now reflected in the current architecture:

1. **Separation of templates and placement** – templates (geometry) are moved to separate files; placement logic is written in Python or hand‑authored `.sexp` referencing the external template files.
2. **Rejection of lock‑file** – replaced by a `diff` command (plan) and explicit `plan`/`apply` distinction (planned for the future).
3. **Physical IR** – the internal model stores only coordinates and geometry; semantics (roles, banks) are stored as tags/metadata.
4. **Support for tracks** – added as part of templates to preserve connectivity when cloning (v1.22.0).
5. **Disambiguation by anchor proximity** – implemented in `clone_role_resolver`.
6. **JSON for templates** – `extract` can save templates in JSON, simplifying serialisation and integration via `include:`.
7. **Live‑board reconciliation** – `PlacementRegistry` and `TrackRegistry` now check against real objects on the board.
8. **Explicit `by_selection`, `anchor_role`, `refs`** – improved control for cloning.

These decisions ensure flexibility, reliability, and ease of use for complex projects.

---

## 11. Conclusion

The architecture of KiCadStamp provides:

- **Flexibility** – easy addition of new templates, roles, via/track types, and cloned sections.
- **Reliability** – three‑phase execution, validation, idempotency with live‑board reconciliation, registry, and undo.
- **Usability** – configuration without explicit refdes, automatic component selection, template extraction from selection, cloning of repeated sections including tracks.
- **Testability** – clear layering, interfaces, unit and integration tests.
- **Scalability** – external template files, JSON support, template transformation.

This makes the tool suitable for real‑world PCB development projects, especially in multi‑channel systems with high repetition rates.
