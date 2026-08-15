# Top-level Modules of KiCadStamp (Current Version)

The `kicadstamp/` folder contains the core modules responsible for configuration loading, exception handling, logging, undo, validation, placement registries for vias and tracks, template extraction, scripting helpers, and the CLI entry point. Each module addresses a specific task and interacts with others through well-defined interfaces.

---

## 1. `kicadstamp_cli.py` – CLI Entry Point (Dispatcher)

**Purpose:**  
Thin dispatcher that parses command-line arguments and delegates to the appropriate command handler. The actual logic for `apply`, `extract`, `undo`, `clone-extract` has been extracted into separate modules for testability and maintainability.

**Main functions:**

| Function | Description |
|----------|-------------|
| `cmd_apply(args)` | Delegates to `apply_pipeline.cmd_apply()` — loads config, connects to KiCad, runs validation, planning, and **three-phase** execution. |
| `cmd_extract(args)` | Delegates to `cli_extract.cmd_extract()` — extracts a template from the current selection on the board. |
| `cmd_undo(args)` | Delegates to `undo.undo_last_operation()` — restores board state before the last placement. |
| `cmd_clone_extract(args)` | Delegates to `cloner.extract.extract_channel()` — file-based cloner. |
| `main()` | Parses arguments (supports implicit `apply`), sets up logging via `logging_setup.setup_logging()`, invokes the appropriate command, catches exceptions. |

**Key dependencies:**  
`apply_pipeline.cmd_apply`, `cli_extract.cmd_extract`, `undo.undo_last_operation`, `logging_setup.setup_logging`, `cloner.extract.extract_channel`.

---

## 2. `apply_pipeline.py` – Apply Command Logic

**Purpose:**  
Contains the `ApplyPipeline` class and the `cmd_apply()` entry point. Handles the full `apply` workflow: loading config, applying CLI filters (`--only`, `--cluster`), connecting to KiCad, running validation, resolving execution order, planning moves/vias/tracks, and executing them in three phases.

**Main classes and functions:**

| Class/Function | Description |
|----------------|-------------|
| `ApplyPipeline` | Main orchestrator: `load_config` → `filter_config` → `connect_adapter` → `resolve_order` → `dry_run`/`execute`. |
| `cmd_apply(args, cfg, ctx)` | Entry point; creates `ApplyPipeline`, calls `run()`. |
| `drop_disabled_rules(cfg)` | Removes `enabled: false` items from config. |
| `drop_inactive_items(cfg)` | Removes `active: false` items (orthogonal to `enabled`). |
| `apply_only_filter(cfg, only_names)` | Narrows config to only the named rules/clones/thermal vias. |
| `apply_cluster_filter(cfg, cluster_paths)` | Narrows config by cluster path. |

**Key dependencies:**  
`config.load_config`, `kicad.adapter.KiCadBoardAdapter`, `validation.run_all_checks`, `placement.dependency_order.resolve_execution_order`, `placement.planner.PlacementPlanner`, `placement.executor.BatchExecutor`, `registry.PlacementRegistry`, `registry.TrackRegistry`.

**Features:**  
- Three-phase execution: moves → refresh → vias → tracks.
- Support for `--dry-run`, `--only`, `--cluster`.
- CLI filter composition: `--only` and `--cluster` compose as AND.

---

## 3. `cli_extract.py` – Extract Command Logic

**Purpose:**  
Contains `cmd_extract()` — the implementation of the `extract` command, extracted from the monolithic CLI for testability.

**Main function:**

| Function | Description |
|----------|-------------|
| `cmd_extract(args)` | Loads profile/config, calls `template_extraction.extract_template_from_selection()`, writes output as JSON or YAML. |

**Key dependencies:**  
`template_extraction.extract_template_from_selection`, `config.load_config`, `config.includes.load_profile`.

---

## 4. `logging_setup.py` – Logging Configuration

**Purpose:**
Contains `setup_logging()` — configures logging level (INFO/DEBUG), console and/or file output. Extracted from the monolithic CLI to be reusable by scripts and tests.

Since 2026-08-15 the root logger carries only a cheap `QueueHandler`; all real
formatting/writing runs on ONE dedicated `QueueListener` thread (see
`techdocs/handoff/plan_2026_08_15_queue_based_logging.md`). A thread that logs only does a
`queue.put()` — it can never block on a handler lock held by a peer stuck inside `emit()`
(e.g. a hung `pynng` `close()` in a GC finalizer, which previously froze the whole GUI).
`setup_logging()` returns the started listener and the CALLER must stop it (GUI:
`QApplication.aboutToQuit`; CLI: `finally`).

**Main functions:**

| Function | Description |
|----------|-------------|
| `setup_logging(verbose, log_file)` | Configures logging: level, console handler, optional file handler; returns the started `QueueListener`. |
| `get_log_listener()` | The `QueueListener` started by the last `setup_logging()` call, or `None` (e.g. unit tests) — lets dynamic consumers like `LogDock` attach their handlers to the listener. |

---

## 5. `runtime_context.py` – Runtime Context

**Purpose:**  
Defines the `RuntimeContext` dataclass that carries per-run state (sheet names, etc.) through the pipeline.

**Main dataclass:**

| Class | Description |
|-------|-------------|
| `RuntimeContext` | Holds `sheet_names: Dict[str, str]` — mapping from sheet UUID to sheet name, used by clone role resolver. |

---

## 6. `sheet_names.py` – Sheet UUID Parsing

**Purpose:**  
Parses `.kicad_sch` files to build a mapping from sheet UUIDs to sheet names. This mapping is used by the clone role resolver to disambiguate components on different hierarchy sheets.

**Main functions:**

| Function | Description |
|----------|-------------|
| `build_sheet_name_map(config_path, schematic_dir, adapter)` | Reads the schematic hierarchy, returns `{uuid: sheet_name}`. |
| `resolve_sheet_path_names(fp, sheet_names)` | Returns the sheet path for a footprint as a list of names. |

---

## 7. `i18n.py` – Internationalisation

**Purpose:**  
Sets up gettext for Russian-language user-facing messages. Uses `kicadstamp` translation domain. Provides the `_()` function used throughout the codebase.

**Main elements:**

| Element | Description |
|---------|-------------|
| `_()` | gettext translation function — wraps user-facing strings for Russian localisation. |
| `setup_i18n()` | Initialises gettext with locale path and domain. |

**Used in:** All source modules that produce user-facing messages (42 files as of July 2026).

---

## 8. `author.py` – Scripting Helpers

**Purpose:**
Provides helper functions for writing placement scripts (Python code instead of YAML configs). Includes dump functions and `apply_config()` for applying generated configs. The standard `--apply`/`--dry-run` CLI entry-point wrapper `cli_main()` was split out into `author_cli.py` (arch refactor 2026-08-11) so this module stays a pure library.

**Main functions:**

| Function | Description |
|----------|-------------|
| `dump_clone_placements(clones, path)` | Serialises `ClonePlacement` list to YAML, pruning defaults. |
| `dump_rules(rules, path)` | Serialises `Rule` list to YAML, pruning defaults. |
| `dump_template(template_dict, path)` | Writes a template dictionary as JSON or YAML. |
| `apply_config(cfg, config_path, *, dry_run, ...)` | Loads config and runs `cmd_apply` programmatically. |
| `cli_main(build_fn, output_path, ...)` | Standard `if __name__ == "__main__":` body for placement scripts — **now in `author_cli.py`** (split out 2026-08-11). |

---

## 9. `explore.py` – Read-only Board Querying

**Purpose:**  
Provides read-only helper functions for inspecting the board state. Used in diagnostic scripts and interactive exploration.

**Main functions:**

| Function | Description |
|----------|-------------|
| `get_footprints_by_role(adapter, role)` | Finds all footprints with a given `Role` field value. |
| `get_footprint_field(adapter, ref, field_name)` | Reads a specific field value from a footprint. |

---

## 10. `config/` Package – Configuration Loading and Storage

**Purpose:**  
Replaced the old monolithic `config.py`. Now a package with separate modules for models, loading, and includes.

**Package contents:**

| Module | Description |
|--------|-------------|
| `__init__.py` | Exports all config types and `load_config()`. |
| `models.py` | Dataclasses: `Config`, `SpokeTemplate`, `ManualSpoke`, `ClonePlacement`, `Rule`, `TemplateVia`, `TemplateTrack`, `TemplateComponentSlot`, `ThermalViaArrayConfig`. |
| `loader.py` | `load_config()` and `_load_*` helper functions for each config section. Handles role uniqueness checks. |
| `includes.py` | Handles `include:` directives — loads and merges configs from multiple files with cycle detection and duplicate key checks. |

**Main dataclasses:**

| Class | Description |
|-------|-------------|
| `ThermalViaArrayConfig` | Thermal via array settings (uses `anchor_ref` instead of `target_ref`). |
| `TemplateVia` | Via slot in a template (local coordinates, net, dimensions). |
| `TemplateTrack` | Straight track segment in a template: start/end points (local), width, net, optional layer. |
| `TemplateComponentSlot` | Component slot in a template: role, local coordinates, angle, list of vias, optional `net_template` and `layer`. |
| `SpokeTemplate` | Complete spoke template: name, list of vias, list of tracks, list of component slots, absolute `layer`. |
| `ManualSpoke` | Specific spoke: pad, template, shift, rotation, `enabled` flag, `active` flag. |
| `Rule` | Rule for one net: net name, list of spokes, `anchor_ref` (mandatory), `active` flag. |
| `ClonePlacement` | Cloned placement: name, template, absolute point or shift from anchor, angle, dicts `nets`, `params`, `net_overrides`, `layer`, `mirror`, `refs`, `by_selection`, `anchor_role`, `anchor_sheet`, `anchor_pad`. |
| `Config` | Main object: global `layer`, templates, thermal vias, rules, clones, flags. |

**Main functions:**

| Function | Description |
|----------|-------------|
| `load_config(path)` | Reads YAML, resolves `include:` (merges external `cells:`/`rules:`/etc. from other files). Parses all sections, returns a `Config` object and `RuntimeContext`. |
| `_load_template_via(data)` | Loads `TemplateVia`. Checks that `net` is a string. |
| `_load_template_track(data)` | Loads `TemplateTrack`. Checks that `net` is a string. |
| `_load_template_component_slot(data)` | Loads `TemplateComponentSlot`. |
| `_load_spoke_template(name, data)` | Loads `SpokeTemplate` with role uniqueness check. |
| `_load_manual_spoke(data)` | Loads `ManualSpoke`. |
| `_load_clone_placement(data)` | Loads `ClonePlacement`. Checks anchor and coordinate constraints. |

**Features:**  
- **`include:`** – multiple config files with merging and cycle detection, including external `cells:` files (wrapped in a `cells:` key — `cells_file:`/`cell_files:`, a separate older mechanism, were folded into `include:` on 2026-08-02).
- Role uniqueness check inside a template.
- `net_template` for cloning (placeholders for nets).
- Two role resolution modes: "by selection" and "by nets".
- Cross-validation of `layer`/`mirror`.
- Deprecated fields `target_ref` and `side` cause fatal error.

---

## 11. `exceptions.py` – Exception Hierarchy

**Purpose:**  
Defines custom exceptions for the project and a common fatal error formatting function. All exceptions inherit from the base `PlacerError`.

**Exception classes:**

| Class | Purpose |
|-------|---------|
| `PlacerError` | Base exception for all placer errors. |
| `BoardNotFoundError` | Failed to obtain the board from KiCad. |
| `ComponentNotFoundError` | Component not found on the board. |
| `GeometryError` | Geometry calculation error. |
| `ValidationError` | Fatal pre‑validation error — the program stops before modifying the board. |

**Helper function:**

| Function | Description |
|----------|-------------|
| `format_fatal_error(title, problems)` | Formats a list of problems into a single multi‑line message with a border of `=`. Used both in `config/loader.py` and `validation.py`. Lives here to avoid circular imports. |

---

## 12. `net_resolution.py` – Net Resolution for Cloned Templates

**Purpose:**  
Provides three‑layer net name resolution for `ClonePlacement`. Allows substitution of placeholders from `params` and application of `net_overrides`. Also provides **reverse parametrisation** (`parametrize_net`) for `extract`.

**Main functions:**

| Function | Description |
|----------|-------------|
| `resolve_net(net_template, params, net_overrides)` | Takes a net name template (possibly with `{placeholder}`), a params dict for substitution, and a net_overrides dict. Returns the final net name. |
| `parametrize_net(literal_net, net_template_map, params)` | Reverse operation for `extract`: reconstructs the pattern with placeholders from a real net name. |

**Used in:** `placement/services/clone_role_resolver.py` and `geometry/clone_geometry.py`.

---

## 13. `registry.py` – Placement Registries for Vias and Tracks

**Purpose:**  
Ensures idempotency of via and track placement across runs. Stores information about created objects in JSON files. On subsequent runs, reconciles planned objects against **real objects on the board**, removes obsolete ones (prune), and creates only new or changed objects.

**Main classes and functions:**

| Class/Function | Description |
|----------------|-------------|
| `make_registry_key(anchor_id, template_name, role, via_index)` | Generates a composite key for the via registry. |
| `registry_path_for_config(config_path)` | Returns the path to the via registry file. |
| `track_registry_path_for_config(config_path)` | Returns the path to the track registry file. |
| `RegistryEntry` | Dataclass for vias: UUID, position, net, drill/diameter parameters. |
| `TrackRegistryEntry` | Dataclass for tracks: UUID, start/end coordinates, width, net, layer. |
| `PlacementRegistry` | Class managing the via registry. |
| `TrackRegistry` | Class managing the track registry. |

**Features:**
- **Reconciliation against live board objects** – source of truth, not just JSON.
- Registry keys: `anchor_id|template_name|role|via_index` (similar for tracks).
- Position tolerance: 0.01 mm.
- Separate registries for vias and tracks.

**Used in:** `apply_pipeline.py` (during `apply`), `executor/via_executor.py`, and `executor/track_executor.py`.

---

## 14. `template_extraction.py` – Template Extraction from Selection

**Purpose:**  
Implements the `extract` command logic: from the current selection in the KiCad PCB editor, extracts a spoke template (components, vias, **and tracks**) and builds a structure ready for file output.

**Main functions:**

| Function | Description |
|----------|-------------|
| `extract_template_from_selection(adapter, name, params, net_template_map, ...)` | Main function. Reads selection, filters tracks, checks roles, computes origin, builds output dictionary. |
| `render_uncertain_comments(yaml_text, name)` | Adds YAML comments marking uncertain geometry values. |

**Used in:** `cli_extract.py` (`extract` command).

---

## 15. `undo.py` – Undo Last Operation

**Purpose:**  
Implements the `undo` command. Uses JSON logs created by `executor/operation_logger.py`.

**Main function:**

| Function | Description |
|----------|-------------|
| `undo_last_operation(json_path)` | Restores board state: returns components to original positions/layers, deletes created vias and tracks. |

**Used in:** `kicadstamp_cli.py` (`undo` command).

---

## 16. `validation.py` – Pre‑validation Checks

**Purpose:**  
Performs fatal checks on the configuration **before** any board modifications. Collects all problems rather than stopping at the first one.

**Main functions:**

| Function | Description |
|----------|-------------|
| `check_templates_and_pads_exist(adapter, cfg)` | Ensures every enabled spoke references an existing template and valid pad. |
| `check_role_pool_sufficiency(adapter, cfg)` | Checks component availability per role. |
| `check_clone_templates_exist(cfg)` | Config-only template existence check. |
| `check_no_duplicate_clone_anchors(cfg)` | Uniqueness of clone names and physical anchors. |
| `check_anchor_sheet_configured(cfg, sheet_names)` | Validates `anchor_sheet` references against actual sheet names. |
| `check_clone_nets_exist_on_board(adapter, cfg)` | Resolves via/track nets and checks against actual board nets. |
| `check_single_selection_based_clone(cfg)` | Ensures at most one clone in selection mode. |
| `run_all_checks(adapter, cfg, sheet_names)` | Runs all checks in order. |

**Used in:** `apply_pipeline.py` (before planning).

---

## 17. `constants.py` – Global Constants

**Purpose:**  
Holds global constants used across various modules.

| Constant | Value | Usage |
|----------|-------|-------|
| `ROLE_FIELD_NAME` | `"Role"` | Custom field name for roles (used in `component_pool.py`, `template_extraction.py`, `clone_role_resolver.py`). |
| `CLUSTER_FIELD_NAME` | `"Cluster"` | Custom field name for cluster paths. |
| `POSITION_TOLERANCE_NM` | `10_000` (0.01 mm) | Position tolerance for "already in place" checks. |
| `ANGLE_TOLERANCE_DEG` | `0.1` | Angle tolerance for "already in place" checks. |
| `POSITION_TOLERANCE_MM` | `0.01` | Position tolerance for registry. |
| `DEFAULT_BATCH_SIZE` | `10` | Default batch size for transactions. |
| `DEFAULT_TIMEOUT_MS` | `20000` | Default IPC timeout. |
| `DEFAULT_LOG_DIR` | `"logs"` | Default log directory. |
| `SPOKE_LEVEL_ROLE_PLACEHOLDER` | `"__spoke__"` | Placeholder for spoke‑level vias in registry. |

---

## 18. `utils/file_cache.py` – Single-File Read Cache

**Purpose:**
Memoizes a single file's `open()+parse` by `(resolved path, mtime_ns)` — a changed mtime (typically an external hand-edit) is a cache miss on its own, and every raw reader shares one cache entry per file. Kills the GUI's startup redundancy: one `MainWindow()` construction used to re-parse the same `include:` graph of YAML files ~13× and the same `.kicad_sch` files 4× each (profiled: 15.0s → ~1.1s construction, `yaml.safe_load` 113 → ~8 calls on the real project — see `techdocs/handoff/plan_2026_08_15_config_read_cache_startup.md`).

**Main functions:**

| Function | Description |
|----------|-------------|
| `cached_file_read(path, loader)` | Returns a deep copy of `loader(path)`, keyed by `(resolved path, mtime_ns)`; never caches a missing file (loader handles it directly). |
| `invalidate_path(path)` | Drops every cached generation of `path` — must be called by writers right after the physical write (mtime alone can't distinguish two writes landing in the same timer tick). |

**Used in:** `config/includes.py`, `config/loader.py`, `config_writer.py` (read + the single write chokepoint), `sheet_names.py`.

---

## Module Interconnections

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
    NetResolution --> ConfigPkg (used by ClonePlacement)
    NetResolution --> Extract (parametrize_net)

    Order --> Adapter
    Order --> ConfigPkg
```

Each module addresses a specific task and interacts with others through clearly defined interfaces, ensuring modularity and testability. Thanks to centralised constants, a unified error formatter, and support for external template files, the project is easy to maintain and extend.
