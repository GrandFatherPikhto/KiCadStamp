# KiCadStamp v1.8.0

**KiCadStamp** is a command‑line **PCB cloning and layout automation** tool for **KiCad 10**, designed as an advanced script‑based alternative to the traditional **KiCad Replicate Layout** plugin. It enables automated **block replication**, component placement, and routing of complex multi‑channel designs using **templates**, **roles**, and the IPC API.

- Moving components (capacitors, resistors, ferrites, crystals, etc.) to specified positions.
- Creating vias and **tracks** attached to the spoke as a whole or to individual components.
- **Cloning** repetitive functional blocks (PI‑filters, DAC channels, power supplies) at different board locations.
- Automatic component selection by **roles** and **nets** – no explicit refdes needed.
- Idempotency: repeated runs never duplicate already‑correctly‑placed items.
- Undo of the last operation.
- Extracting templates from the current selection with net parametrisation and custom origin selection.
- Snapshotting hierarchical channels via a file‑based cloner (`clone-extract`).

---

## Key Features

**An advanced, script‑driven alternative to the classic KiCad Replicate Layout plugin**, built for multi‑channel projects and automated design reuse via the KiCad IPC API.

- **Template‑based approach** – geometry is defined once in local coordinates and reused with arbitrary rotation/translation.
- **Automatic component selection** – roles (`LIGHT`, `HEAVY`, `PI_FILTER_C1`, etc.) replace refdes; components are picked from a pool by net and the `Role` field in the schematic.
- **ClonePlacement** – supports two modes:
  - **by selection** – for one‑off instances (e.g., a single MCU);
  - **by nets** – for repeated blocks, with net name parametrisation via placeholders and `params`. Ambiguity is resolved by physical proximity to the anchor (useful for power filters on common rails). You can also use explicit `refs` as a last resort.
- **Generalised vias and tracks** – all elements (vias, tracks, components) are defined in local coordinates and transformed uniformly (translation, rotation, mirroring).
- **Placement registry** – stores UUIDs of created vias and tracks, ensuring idempotency and automatic cleanup of obsolete entries. Reconciliation is now performed against real elements on the board (via `adapter.get_vias()`/`adapter.get_tracks()`), not only the JSON record, avoiding desynchronisation.
- **Pre‑validation** – checks config before any board modification:
  - existence of templates, pads, and anchor components;
  - component pool sufficiency by roles;
  - uniqueness of clone names and physical anchors (`anchor_ref`, `anchor_role`);
  - correctness of resolved via nets against real board nets;
  - validity of `layer`/`mirror` combinations (mirroring only when layer changes);
  - at most one selection‑based `clone_placement` per run (KiCad allows only one active selection).
- **Diagnostics** – scripts for debugging IPC, geometry, and field reading.
- **File‑based cloner** (`clone-extract`) – parses `.net` and `.kicad_pcb` without IPC, builds a twin map of channels for hierarchical projects.
- **Tracks in templates** – templates can include straight track segments (polylines are supported as a sequence of segments). Track collisions are not automatically checked (rely on KiCad DRC).
- **External template files** – templates can be stored separately as JSON or YAML (wrapped in a `cells:` key) and listed under `include:` in the main config, keeping the main file clean and diff‑friendly.
- **Splitting a profile into subsystem files** – `include:` at the root of a profile merges in one or more other YAML files (each carrying any mix of `extract_profiles`/`clone_placements`/`rules`/`cells`), recursively, with a per‑entry `enabled: false` on the include itself to switch a whole subsystem file off without touching every item inside it (see [docs/config.md](docs/config.md) for merge semantics and duplicate/cycle handling).
- **Entity/Placement model** (2026-08-30) – an `entities:` record is everything about a thing except where it stands (cell/nets/identity — no position fields at all); "where it stands" lives ONLY in a `trees:` node (`kind "placement"`, `ref` = entity name). A converter (`tools/convert_placements.py`) migrates legacy `clone_placements:` profiles to this model (see [docs/config.md](docs/config.md) and [docs/placement.md](docs/placement.md)).
- **Scripting API** – `kicadstamp.explore.Board` for ad‑hoc read‑only querying (`board.select(role=..., cluster=..., sheet=..., net=...)`), and `kicadstamp.author` for building `ClonePlacement`/`Rule` in real Python instead of hand‑writing repetitive YAML, either applied directly or dumped back to an `include:`‑ready YAML file (see [docs/python.md](docs/python.md)).

---

## Installation and Dependencies

### Requirements
- Python 3.8 or later.
- KiCad 10.0.4 or later (with IPC API enabled).
- The **kipy** library (Python wrapper for KiCad IPC).

### Installation
```bash
pip install kipy pyyaml sexpdata
```
(For diagnostics, `psutil` may be required.)

### Setting up Roles in the Schematic (Eeschema)
1. Open the symbol in Eeschema.
2. Add a field named **Role** with a value matching the role in the template (e.g., `LIGHT`, `HEAVY`).
3. Run **Update PCB from Schematic** to propagate the field to the board.
4. Verify readability with:
   ```bash
   python -m kicadstamp.diagnostics.test_custom_field C5 --field Role
   ```

---

## Key Concepts

### Why "Spoke"?
In electronics, decoupling/support components (capacitors, pi‑filters) often radiate outward from an
IC's pins, like spokes on a wheel. KiCadStamp automates building this kind of "spoke" topology, letting
you **stamp** it out by rule and role wherever it's needed:
- **Template (`SpokeTemplate`)** – the geometry of one spoke (a capacitor + via + track, or a whole filter block).
- **Spoke (`ManualSpoke`)** – a rule that takes a spoke template and attaches it to a specific pad.
- **Cloning (`ClonePlacement`)** – the next level: takes a template – one spoke or a whole bundle of them
  (e.g. a channel) – and stamps it as an independent unit anywhere on the board, not just on an IC pad.

That maps onto the tool's two names: **Spoke** is the domain shape (the radiating placement pattern),
**Stamp** is the action – the tool that replicates it by rule.

### Template (SpokeTemplate)
A template describes the **local geometry** of one "spoke" – a set of components, vias, and tracks relative to a local origin (0,0) in the `along/across` coordinate system. It contains:
- **`vias`** – vias at the spoke level (usually the power net).
- **`components`** – a list of slots, each with a `role`, local coordinates, angle, and a list of vias (usually to GND).
- **`tracks`** – straight track segments (layer, width, net).

All coordinates are defined **once** at `rotation_deg=0`; when applied, the template is rotated as a whole.

#### Template layer (`layer`)
Each template has an absolute layer (`F.Cu` or `B.Cu`), automatically set during extraction (`extract`). Components on a different layer get an explicit `layer` in their slot.

#### Net parametrisation during extraction
With the `--net-template` option, you can replace literal net names with patterns containing placeholders (e.g., `DAC1_DB1 → DAC{channel}_DB1`) at extraction time. This eliminates manual YAML editing.

### Spoke (ManualSpoke)
Attaches a template to a specific IC pin:
- `pad` – pad number of the target component.
- `shift_x_mm`, `shift_y_mm` – flat shift from the pad centre to the template origin.
- `rotation_deg` – rotation of the entire template.

**Important:** In new config versions, each rule (`rules`) must have its own `anchor_ref`. The global `target_ref` has been removed.

### Roles and Component Pool
Instead of refdes, **roles** are used in the config. For each net (`rule.net`), a pool of components is built, where each component:
- Has a `Role` field with the required value.
- Has at least one pad connected to that net.

Components are sorted in natural numeric order (`C5` < `C10`) and consumed in the order of spokes.

### Cloning (ClonePlacement)
Allows applying a template at an arbitrary point on the board, without tying to IC pads. Supports:
- **Selection mode** – reads roles from the current selection in the PCB editor. You can either omit `nets`/`params` or explicitly set `by_selection: true`. Only one such clone can be processed per run (due to KiCad's single‑selection limitation).
- **Net mode** – for each role, a net is specified (via `nets` or `net_template` with placeholders resolved by `params` and `net_overrides`). If multiple candidates are electrically indistinguishable, the tool can pick the one closest to the anchor (if the distance margin is sufficient). This is useful for power filters on a common rail.
- **Anchor by role** (`anchor_role`) – an alternative to `anchor_ref`: instead of a refdes, you can specify the `Role` field of the anchor component. This survives re‑annotation. You can further narrow the search with `anchor_sheet` (local net prefix) or `anchor_pad`.
- **Explicit refs** (`refs`) – a last‑resort override when candidates are indistinguishable by nets, selection, or proximity.

### Placement Registry (PlacementRegistry + TrackRegistry)
Separate registries for vias and tracks (JSON files next to the config). On subsequent runs:
- already correctly placed items are skipped;
- those that changed position/parameters are deleted and recreated;
- obsolete entries (keys not present in the new plan) are removed (prune).

**Important:** Reconciliation now checks against real elements on the board (`adapter.get_vias()`/`adapter.get_tracks()`), not only the JSON record, preventing desynchronisation due to manual deletions or crashes between registry write and board commit.

### Net Resolution (`net_resolution`)
For cloned templates, net names go through a three‑step resolution:
1. **Literal** – if no placeholders.
2. **Placeholder** – substitution from `params` (e.g., `{channel}` → `2`).
3. **net_overrides** – final override of the resolved name (for hierarchical paths).

During extraction, the reverse operation (`--net-template`) is available, turning literals into patterns.

The **alias‑free path is the primary one**: a via/track whose net maps
unambiguously to one selected Role is written as `net_from_role` (optionally with
`net_from_role_pad`) at extract time and resolved **live** at apply time from that
Role's real pad — `net_from_role`/`net_from_role_pad` are tried BEFORE
`net_template_map`/manual aliases, so a net that already classifies by Role needs
no alias at all. The GUI surfaces this: ExtractDock shows an "Auto-role" column
per net (nets that resolve by Role get a disabled Alias field), and PlacerDock
auto-fills a placement's `nets:` from the live board for the chosen Cluster
(only blank roles are ever filled) and hides the Params section when the Cell has
no `{placeholder}` anywhere. Manual entry (aliases/`params:`) remains only for
genuinely ambiguous (fallback) nets. The legacy
`net_template_role`/`params:`+`net:'{PLACEHOLDER}'` path is kept for backward
compatibility, but is no longer the recommended way for new cells.

---

## Configuration File Format (YAML)

Full field-by-field reference for every section (`cells`/`rules`/`clone_placements`/
`thermal_via_arrays`/`points`/`include`/`extract_profiles`) with real, currently-loading examples now
lives in its own page: [docs/config.md](docs/config.md).

---

## CLI Commands

All commands are run via `kicadstamp_cli.py`. If the subcommand is omitted, `apply` is assumed.

### `apply` – apply placement

```bash
python kicadstamp_cli.py apply config.yaml [options]
```

Options:
- `--dry-run` – only show the plan, do not apply changes.
- `--timeout-ms` – IPC timeout in ms (default 20000).
- `--batch-size` – batch size for commits (default 10).
- `--verbose` – verbose output (DEBUG).
- `--log-file` – save logs to a file.
- `--no-collision-check` – disable collision checking.
- `--collision-margin` – margin in mm (default 0.2).
- `--only NAME` – process only the `rules`/`clone_placements`/`thermal_via_arrays` with this name (repeatable); everything else is skipped entirely. `name:` is mandatory on every such entry.

### `extract` – extract template from selection (enhanced)

```bash
python kicadstamp_cli.py extract --name template_name --output config.yaml [--verbose] [--log-file] [--param KEY=VALUE] [--net-template LITERAL=PATTERN] [--origin-by-via-net NET] [--origin-by-component-role ROLE]
```

New options:
- `--param KEY=VALUE` – parameter for `--net-template` verification (e.g., `channel=1`), not written to template.
- `--net-template LITERAL=PATTERN` – replace a real net with a pattern containing placeholders (e.g., `DAC1_DB1=DAC{channel}_DB1`). Can be repeated.
- `--origin-by-via-net NET` – set origin to the position of a via on the specified net (instead of bbox). Fatal if the net is missing or ambiguous.
- `--origin-by-component-role ROLE` – set origin to the position of a component with the specified role.

**Important:** The `--output` extension determines format: `.json` → JSON, otherwise YAML. The file is written wrapped under a `cells:` key, ready to be listed directly under `include:`.

### `undo` – undo the last operation

```bash
python kicadstamp_cli.py undo [--verbose] [--log-file]
```

### `clone-extract` – snapshot a channel (file‑based cloner)

```bash
python kicadstamp_cli.py clone-extract --net project.net --pcb project.kicad_pcb --channel Channel_0 --output snapshot.sexp [--verbose]
```

---

## Usage Examples

### 1. Standard run
```bash
python kicadstamp_cli.py 10CL006YE144C8G.yaml
```

### 2. Dry run
```bash
python kicadstamp_cli.py config.yaml --dry-run
```

### 3. Process a single clone (selection mode)
```bash
python kicadstamp_cli.py config.yaml --only pi_filter_vccio
```

### 4. Extract a template with parametrisation and origin by via
Select the elements on the board, then:
```bash
python kicadstamp_cli.py extract --name my_filter --output my_filter.json --net-template "DAC1_DB1=DAC{channel}_DB1" --param channel=1 --origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD" --verbose
```

### 5. Undo
```bash
python kicadstamp_cli.py undo --verbose
```

---

## Diagnostics and Known Issues

### KiCad Bug #24966 (crash on first write via IPC)
When the Schematic Editor is open, the session's first `begin_commit()`/`push_commit()` transaction (even a
no-op one) can crash KiCad (null pointer in `API_HANDLER_EDITOR::checkForBusy`).

**Symptoms:** KiCad silently closes, client gets `ConnectionError: Error receiving reply from KiCad: Timed out`.

**Workaround:** close the schematic editor before running `apply`. The tool includes a warning and retries,
but the crash remains a KiCad defect. In practice it's specifically the *session's first* write that's
vulnerable — if the first `apply` run is done with only the PCB Editor open, opening the Schematic Editor
afterwards is usually safe. Full write-up, a related bug (#24970), and the full crash-hunting toolkit —
see [docs/crash_hunting.md](./docs/crash_hunting.md).

### Diagnostic scripts
`kicadstamp/diagnostics/` includes:
- `diagnose_first_write_crash.py` – reproduces the crash ladder, see [docs/diagnose_first_write_crash.md](./docs/diagnose_first_write_crash.md).
- `test_custom_fields.py` – checks `Role` field reading.
- `test_move_one_cap.py`, `test_flip_one_cap.py`, `test_create_one_via.py`, `test_pad_mirror_convention.py`, `get_selected_component.py`, `get_pad_bbox.py`, `diagnostic_keepout.py`.

---

## Project Structure (brief)

```
kicadstamp/
├── __init__.py
├── kicadstamp_cli.py          # CLI entry point
├── apply_pipeline.py          # cmd_apply and ApplyPipeline class
├── cli_extract.py             # cmd_extract command logic
├── logging_setup.py           # Logging configuration
├── runtime_context.py         # RuntimeContext dataclass
├── sheet_names.py             # Sheet UUID → name resolution
├── i18n.py                    # gettext internationalisation
├── author.py                  # Scripting: dump/apply helpers (explore/author)
├── explore.py                 # Board query helpers
├── config/                    # Configuration package (loader.py, models.py, includes.py)
├── constants.py               # Global constants (ROLE_FIELD_NAME, tolerances, etc.)
├── exceptions.py              # Exception hierarchy
├── validation.py              # Pre‑checks (nets, uniqueness, selection mode, layer/mirror)
├── registry.py                # Via and track registries (reconcile with live elements)
├── net_resolution.py          # Net resolution with placeholders for ClonePlacement
├── template_extraction.py     # Extract with parametrisation and custom origin
├── undo.py                    # Undo last placement operation
├── geometry/                  # Spoke_layout, keepout, thermal_grid, pad_projection, clone_geometry
├── kicad/                     # KiCad IPC adapter and IBoardAdapter interface
├── placement/                 # Planner, executors, services
│   ├── services/              # component_pool, clone_role_resolver, position_tracker, component_resolver, etc.
├── cloner/                    # File‑based cloner (extract, netlist, pcb, models, sexp)
├── diagnostics/               # Diagnostic scripts
├── utils/                     # Utilities
│   └── units.py               # MM = 1_000_000 constant
└── tests/                     # Unit and integration tests
```

---

## 📚 Technical Documentation

Detailed documentation is in the `docs/` folder:

- [Project architecture](./docs/architect.md)
- [CLI commands](./docs/commands.md)
- [Geometry utilities](./docs/geometry.md)
- [KiCad adapter](./docs/kicad.md)
- [Using kipy](./docs/kipy.md)
- [MCP server](./docs/mcp.md)
- [Placement planning and execution](./docs/placement.md)
- [YAML configuration reference](./docs/config.md)
- [Coding placement in Python: explore/author](./docs/python.md)
- [PyQt6 GUI](./docs/gui.md)
- [fieldstool: bulk Role/Cluster set/rename in .kicad_sch](./docs/fieldstool.md)
- [Tests](./docs/tests.md)
- [Top‑level modules](./docs/uplevel_modules.md)
- [File‑based cloner](./docs/cloner.md)
- [Diagnostics](./docs/diagnostics.md)
- [KiCad crash hunting toolkit (#24966 / #24970)](./docs/crash_hunting.md)
- [`diagnose_first_write_crash.py` reference](./docs/diagnose_first_write_crash.md)
- [Internationalization (i18n) — gettext/Babel](./docs/i18n.md)
- [Template rotation and transformation](./docs/rotate_template.md)
- [Module dependency diagram](./docs/diagram.md)

---

## Versioning

Single source of truth: `__version__` in [`kicadstamp/__init__.py`](./kicadstamp/__init__.py) — this README's
header and `kicadstamp_cli.py --version`/`-V` both read it, not a separate literal. Versioned by
session/stage, not by commit: MINOR bumps once per notable block of work (e.g. one architecture‑refactor
session, regardless of how many commits it took), PATCH for point fixes made between stages, MAJOR
reserved for actual breaking changes to the CLI or YAML config format.

---

## License

This project is distributed under the **MIT** license. See the `LICENSE` file for details.

---

**KiCadStamp** is not just a utility – it's a modern alternative to manual block copying in KiCad.
