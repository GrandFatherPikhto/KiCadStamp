# `tests/` – Unit and Integration Tests for KiCadStamp

## Purpose

The `tests/` directory contains two types of tests:

1. **Unit tests** – run **without connecting to KiCad**, use mocks, and verify the logic of modules: geometry, configuration, validation, registry, template extraction, net resolution, cloning (`ClonePlacement`), template transformation, etc. They are fast, stable, and run in CI/CD.

2. **Integration tests** – run **with a real KiCad instance** and verify end‑to‑end operation via IPC: connection, via and track creation, component movement/flip, registry operation, template extraction from selection, cloning (`ClonePlacement`) in a real environment. They require KiCad to be open with an active board and are marked with `@pytest.mark.integration` to avoid accidental runs.

---

## Structure

```
tests/
├── conftest.py                       # Common fixtures for unit tests
├── test_author.py                    # Scripting helpers: prune defaults, dump round‑trip, cli_main
├── test_cli_filters.py               # CLI filters: --only, --cluster, active/drop/inactive logic
├── test_clone_anchor_id.py           # Anchor ID resolution for clones
├── test_clone_geometry.py            # ClonePlacement geometry (rotation, mirror, tracks)
├── test_clone_ignore_selection.py    # ignore_selection flag for clones
├── test_clone_placement_config.py    # ClonePlacement loading from YAML
├── test_clone_placement_integration.py # End‑to‑end ClonePlacement test (mocks)
├── test_clone_role_resolver.py       # Role resolution for cloning (selection, nets, anchor proximity)
├── test_clone_selection_conflict.py  # Conflict check for multiple clones in selection mode
├── test_config_includes.py           # include: directive merging, cycles, duplicates
├── test_dependency_order.py          # Execution order resolution by anchor_ref/anchor_role
├── test_execute_vias_owner_ref.py    # Correctness of owner_ref in logs (vias)
├── test_explore.py                   # Read‑only board query helpers
├── test_full_pipeline_templates.py   # End‑to‑end pipeline test (mocks) for ManualSpoke
├── test_i18n.py                      # gettext _() function availability and import
├── test_kicad.py                     # Adapter check (method presence)
├── test_manual_position_calculator.py # ManualPositionCalculator logic (pools, positions)
├── test_naming.py                    # Name/effective_name accessors, required name validation
├── test_net_resolution.py            # Net resolution with placeholders (net_resolution)
├── test_pad_projection.py            # Pad position prediction
├── test_registry_integration.py      # Full registry cycle (create, update, prune) with mocks
├── test_registry_pruning_granularity.py # Registry pruning precision
├── test_registry_rule_protection.py  # Registry chain protection (known_anchor_ids)
├── test_spoke_layout.py              # Local‑to‑global template coordinate transformation (spoke_layout)
├── test_template_extraction.py       # Template extraction from selection (logic, tracks)
├── test_cell_files.py            # deprecated cells_file/cell_files/templates_file/template_files → fatal
├── test_two_phase_execution.py       # Two‑phase execution (moves → refresh → vias) with mocks
├── test_undo_layer.py                # Layer saving/restoring in undo
├── test_unique_roles.py              # Role uniqueness inside templates
├── test_unknown_keys_validation.py   # check_unknown_keys for config sections
├── test_validation.py                # Pre‑validation checks for configuration
│
└── integration_tests/                # Integration tests with real KiCad
    ├── conftest.py                   # Fixtures for integration tests
    ├── test_connection.py            # Connection and basic operations
    ├── test_via_ops.py               # Via creation/deletion, registry operation
    ├── test_track_ops.py             # Track creation/deletion (API verification)
    ├── test_component_ops.py         # Component move/flip
    ├── test_extract.py               # Template extraction from selection
    └── test_registry.py              # Full registry cycle with real KiCad
```

---

## Fixtures

### For unit tests (in root `conftest.py`)

Unit tests typically use `unittest.mock` and do not require external resources, so fixtures are minimal. However, if needed, you can add:

- `tmp_path` – temporary directory (built‑in pytest fixture).
- Custom mocks for the adapter and board objects.

### For integration tests (in `integration_tests/conftest.py`)

The following fixtures are provided for working with real KiCad:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `adapter` | `session` | Single `KiCadBoardAdapter` instance for the entire session. |
| `board` | `session` | Board from the adapter. |
| `test_config` | `session` | Loaded test config from `kicadstamp_templates_example.yaml`. |
| `test_component_ref` | `function` | Refdes of a component for tests (default `C5`). |
| `test_pad_number` | `function` | Pad number for tests (default `17`). |
| `temp_via` | `function` | Creates a temporary via on GND, removes it after the test. Returns `(via_id, position, net)`. |
| `moved_component` | `function` | Moves a component 1 mm to the right and restores it after the test. Returns `(ref, original_pos, new_pos)`. |
| `flipped_component` | `function` | Flips a component to the other side and restores it. Returns `(ref, original_layer, target_layer)`. |
| `registry` | `function` | Creates a temporary placement registry in `tmp_path`. |
| `template_extraction` | `function` | Wrapper over `extract_template_from_selection` (for selection tests). |

These fixtures ensure test isolation and automatic cleanup (deleting vias, restoring positions and layers) after each test.

---

## Running Tests

### Unit tests (without KiCad)

```bash
# All unit tests
pytest tests/ -v

# Exclude integration tests (they are in a separate folder, but you can be explicit)
pytest tests/ -v -m "not integration"

# A specific file
pytest tests/test_spoke_layout.py -v
```

### Integration tests (with real KiCad)

**Important:** Before running, make sure that:
- KiCad is open and the test board is active.
- The components used in the tests (e.g., `C5`) exist on the board.
- The tests do not damage critical routing (they restore the state).

```bash
# All integration tests
pytest tests/integration_tests/ -v -m integration

# A specific file
pytest tests/integration_tests/test_via_ops.py -v -m integration

# With output capture disabled for debugging
pytest tests/integration_tests/ -v -s -m integration
```

---

## Description of Unit Tests

| File | What it tests |
|------|---------------|
| `test_author.py` | Scripting helpers: `_prune_defaults` (drops dataclass default fields), YAML round‑trip for `ClonePlacement`/`Chain`, `apply_config` namespace compatibility with `cmd_apply`, `cli_main` entry‑point behaviour. |
| `test_cli_filters.py` | CLI filter logic: `--only NAME` (narrowing by name/net), `--cluster PATH` (narrowing by cluster path), `drop_disabled_rules`, `drop_inactive_items`, `--only`/`--cluster` composition (AND), `load_profile` root defaults and `include:` resolution. |
| `test_clone_anchor_id.py` | Anchor ID resolution for clones: `clone_anchor_id()` returns correct key based on `anchor_ref`/`anchor_role`/`name`. |
| `test_clone_geometry.py` | `ClonePlacement` geometry: local‑to‑global coordinate transformation, component angles, vias and tracks, mirroring (`mirror`), net resolution via `params` and `net_overrides`. Checks fatality of vias without a `net`. |
| `test_clone_ignore_selection.py` | `ignore_selection` flag: temporarily deselects components when processing a clone that should not be affected by user selection. |
| `test_clone_placement_config.py` | Loading `ClonePlacement` from YAML, checking fields `name`, `template`, `origin_x_mm`, `origin_y_mm`, `rotation_deg`, `nets`, `params`, `net_overrides`, `enabled`. |
| `test_clone_placement_integration.py` | End‑to‑end test of `PlacementPlanner` with `ClonePlacement` (mocks): cooperation with `chains` (ManualSpoke) and clones in a single run, checking `registry_key` for vias. |
| `test_clone_role_resolver.py` | Role resolution for `ClonePlacement` in two modes: by selection (`resolve_roles_by_selection`) and by nets (`resolve_roles_by_nets`), including placeholders, `net_overrides`, ambiguity handling, and anchor proximity. |
| `test_clone_selection_conflict.py` | Check that no more than one `ClonePlacement` is in selection mode (`check_single_selection_based_clone`), and `clone_uses_selection_mode` works with `by_selection`, `nets`, `params`. |
| `test_config_includes.py` | `include:` directive: merging `clone_placements`/`chains`/`templates` from multiple files, duplicate detection, cycle/diamond detection, disabled includes, unsupported keys, dict/list misuse. |
| `test_dependency_order.py` | Execution order resolution: disabled clones skipped, no‑dependency keeps original order, producer ordered before consumer, self‑anchored is not a cycle, true cycle raises `ValidationError`. |
| `test_execute_vias_owner_ref.py` | Correctness of `owner_ref` in JSON logs (each via gets its own owner) and that `registry.record_created` is called with the correct UUID. |
| `test_explore.py` | Read‑only board query helpers: `get_footprints_by_role`, `get_footprint_field`, etc. |
| `test_full_pipeline_templates.py` | End‑to‑end pipeline test with templates (mocks): position and via calculation for `ManualSpoke`, component distribution by roles, `registry_key` check. |
| `test_i18n.py` | `_()` function availability, gettext setup, and import verification across all source files. |
| `test_kicad.py` | Presence of all `IBoardAdapter` methods in `KiCadBoardAdapter`, import, and constructor (without real IPC). |
| `test_manual_position_calculator.py` | `ManualPositionCalculator` logic: pool building, position calculation, via planning for `chains`. |
| `test_naming.py` | `chain_effective_name`/`thermal_via_array_effective_name` accessors, `name:` loading from YAML, required name validation (fatality on missing name, and on a duplicate name within `thermal_via_arrays:`), optional Chain.name, Chain.enabled/active defaults. |
| `test_net_resolution.py` | Net resolution with placeholders: substitution from `params`, application of `net_overrides`, errors on missing parameters. |
| `test_pad_projection.py` | Pad position prediction after move/rotate (without and with flip), invariance of `local_pad_offset` to angle. |
| `test_registry_integration.py` | Full registry cycle (create, update, prune) with mocks, including reconciliation with real vias. |
| `test_registry_pruning_granularity.py` | Registry pruning precision: correct identification of obsolete vs. current vias/tracks. |
| `test_registry_rule_protection.py` | Registry chain protection via `known_anchor_ids`: vias/tracks of non‑`--only` clones are not pruned. |
| `test_spoke_layout.py` | Local‑to‑global coordinate transformation for spoke templates (`spoke_layout`), including spoke‑level and component‑level vias, arbitrary number of roles. |
| `test_template_extraction.py` | Template extraction from selection: role checks, uniqueness, origin computation, track/via filtering (connected-components closure rooted at kept footprints' pads), net parametrisation (`--net-template`), origin selection by via/role. |
| `test_cell_files.py` | Deprecated `cells_file`/`cell_files`/`templates_file`/`template_files` keys are all fatal at load with a rename hint (folded into `include:` 2026-08-02 — see `test_config_includes.py` for the current mechanism). |
| `test_two_phase_execution.py` | Two‑phase execution (moves → refresh → vias) with mocks – ensures that vias are planned after moves and have the correct `registry_key`. |
| `test_undo_layer.py` | Saving and restoring the component layer in undo (`original_layer` in JSON log). |
| `test_unique_roles.py` | Uniqueness of roles inside a template (fatal error on duplicates). |
| `test_unknown_keys_validation.py` | `check_unknown_keys` validation for config sections: unknown top‑level keys, unknown keys inside `clone_placements`, `chains`, `thermal_via_arrays`, `templates`. |
| `test_validation.py` | Pre‑validation checks: template/pad existence, component pool sufficiency, uniqueness of clone anchors, net resolution for via/tracks, selection mode for clones. |

---

## Description of Integration Tests

| File | What it tests |
|------|---------------|
| `test_connection.py` | Connection to KiCad, component lookup by refdes, net lookup by name, retrieving all vias. |
| `test_via_ops.py` | Via creation/deletion, registry operation (`reconcile`, `record_created`), temporary via (`temp_via`). |
| `test_track_ops.py` | Track creation/deletion (straight copper segments) via the API – checks that `create_items` works for tracks. |
| `test_component_ops.py` | Component move by 1 mm on X and back, flip to the other side and restore. |
| `test_extract.py` | Template extraction from the current selection on the board (success with selection, error on empty selection). |
| `test_registry.py` | Full registry cycle with real KiCad: via creation, idempotency, position update (delete old, create new), prune. |

All integration tests use fixtures and restore the board to its original state after execution.

---

## Notes

- **Unit tests** do not require KiCad and can be run in any environment.
- **Integration tests** require KiCad to be open with a board and are marked with the `integration` marker – they must be run separately.
- For integration tests, it is recommended to use a test board (e.g., `test_boards/10CL006YE144C8G.kicad_pcb`) to avoid damaging the production project.
- Integration test fixtures provide automatic cleanup, but it is still a good practice to run them on a copy of the board or after saving.
- When adding new modules or features, you should extend the tests to maintain coverage.

---

## Extending Tests

If you add a new module or functionality, follow these guidelines:

- **For unit tests** – create a separate file `test_<module>.py` in the root `tests/` and use `unittest.mock` for isolation.
- **For integration tests** – add new functions to existing files in `integration_tests/` or create a new file with the `integration` marker.
- **Fixtures** for integration tests should be added to `integration_tests/conftest.py`.
- Ensure all tests pass locally before submitting changes.

---

## License

The tests are distributed under the MIT license, the same as the main project.
