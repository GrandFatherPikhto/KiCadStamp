# `tests/` – Unit and Integration Tests for KiCadStamp

## Purpose

The `tests/` directory holds three KINDS of tests, and every test carries exactly
one of the three markers that say which kind it is — `gui` / `integration` / `unit`,
attached by PATH, not by hand (see [Running Tests](#running-tests)):

1. **Unit tests** (`unit`) — no Qt window and no KiCad: pure Python plus doubles.
   Geometry, configuration, validation, registry, template extraction, net
   resolution, cloning, the s-expr layer. Fast, deterministic, they run in CI.

2. **GUI tests** (`gui`, everything under `tests/gui/`) — need a Qt application
   (offscreen) and the window/dock fixtures, still no KiCad: the docks, the hub,
   the worker, the overlay, the settings.

3. **Integration tests** (`integration`, `tests/integration_tests/`) — need a
   RUNNING KiCad with an active board and its IPC socket: connection, via/track
   creation, move/flip, registry, extraction from a selection. They are excluded
   from CI on purpose (`pytest.ini` says so, and the CI command repeats it).

One suite, one `pytest.ini`, one shared reset. The markers exist so that a command
can NAME what it is covering instead of assuming it from a directory.

---

## Structure

Five groups, by what a test NEEDS:

* `tests/conftest.py` — the two autouse resets (process state, then logging), the
  marker hook, `records_from`, and the forced-English `setup_i18n()` call that runs
  at collection time before any test module imports the package (so an English
  assertion is safe even on a Russian-locale machine);
* `tests/paths.py` — the ONE file that turns the suite's own location into
  `TESTS_ROOT`, `REPO_ROOT` and `FIXTURES_DIR`. No test derives a tree path from its
  own depth any more, and that is what made the Ф2 split a pure `git mv`: a file can
  move between directories without its paths changing meaning;
* `tests/fakes/` — the shared doubles, IMPORTED rather than redefined, plus the
  conformance cell that pins each of them against the real interface;
* `tests/fixtures/` — data on disk (profile snapshots), read by path;
* `tests/gui/` — needs Qt (offscreen): the window, the hub, the door, the worker, and
  one file per dock under `tests/gui/docks/`;
* `tests/integration_tests/` — needs a RUNNING KiCad with a live board.

The flat `tests/test_*.py` of the Ф1 era is gone: Ф2 moved every file into a domain
directory mirroring `kicadstamp/`. Two of them mirror nothing in the package and exist
for the suite's own sake — `repo/` (guards that read the repository: source text, the
marker partition, the fake conformance, the board door) and `cli/` (the entry points
and the `tools/` converters). The markers survived the move unchanged: `gui` is still
attached by PATH, so a cell inside `tests/gui/docks/` is still `gui`.

The rule about where a NEW test goes lives in [Extending Tests](#extending-tests).
The per-module description tables further down list bare file NAMES on purpose: each
file now sits under the domain directory named after the module it tests, and the tree
below is the index.

```
tests/
├── conftest.py          # the autouse resets, the marker hook, records_from
├── paths.py             # TESTS_ROOT / REPO_ROOT / FIXTURES_DIR — the only file that knows the depth
├── fakes/               # shared doubles + the conformance cell
├── fixtures/            # data on disk (profile snapshots), read by path
├── config/              # kicadstamp/config/*, the s-expr writer, validation
├── domain/              # kicadstamp/domain/* (board and geometry DTOs)
├── geometry/            # pads, cell anchors, spoke layout, keepouts
├── placement/           # placement services, the executor, registry, undo, persistence
├── pipeline/            # apply_pipeline.py and the two-phase execution
├── trees/               # trees, link_trees, tree_position, cells, entity placement
├── cloner/              # kicadstamp/cloner/* and channel_copy
├── schematic/           # schematic_*.py — the file-level schematic layer
├── imprint/             # imprint_*.py
├── overrides/           # field overrides and the override store
├── net/                 # net resolution, net traces, internode copper
├── extraction/          # templates, spokes, the extract writer
├── cli/                 # CLI entry points, author_cli, the tools/ converters
├── kicad/               # the IPC adapter, adapter_factory, explore, pynng safety
├── mcp/                 # mcp_server/*
├── repo/                # guards that read the REPOSITORY itself
├── gui/                 # Qt, offscreen: the window, the hub, the door, the worker
│   └── docks/           # …and one file per dock (59 of the 107 gui cells)
└── integration_tests/   # a RUNNING KiCad with a live board (9 files + conftest.py)
```

---

## Fixtures

### The shared resets (every test, every kind)

`tests/conftest.py` carries TWO autouse fixtures.
`_reset_process_singletons` puts the process-wide mutable state back before AND
after every test: the config working set (listeners, contents, `enabled`), the
`file_cache` caches, the format probe cache, the s-expr hint cache, and
`gui.connection`'s door state (predicate, probe, the refusal mode, the
once-per-site memory, the sign depth). It exists because isolation used to be
copied per file and the copies drifted — a second copy of that reset in a test
file is a DEFECT now, not a precaution (it is what `tests/gui/conftest.py` used to
carry and no longer does).
`_reset_logging_after_test` is teardown-only and stops a logging LISTENER a test
leaked; it is described together with `records_from` below, because both exist for
the same reason — a test must not decide anything from what a NEIGHBOUR left
running.

### Logging: `records_from(logger_name)`

Recipes that judge a Log line by LEVEL go through this fixture, not through
`caplog.records`:

```python
warnings = [r for r in records_from("kicadstamp.kicad.adapter")
            if r.levelno >= logging.WARNING]
```

caplog attaches its handler at the ROOT logger, so the raw list holds every
record of the test — from any module and any THREAD. A cell that judges by level
over that whole list depends on what the rest of the process logged: an earlier
test abandons a socket, a background thread logs the pynng
"Socket.close() did not return" WARNING, and the cell fails on a neighbour's
record (measured in a reverse-order run, which is why the fixture exists). Use the
same logger name the cell already passes to `caplog.at_level(logger=...)`.

`_reset_logging_after_test` stops a listener a test leaked and detaches its queue
handler, so the GUI cells that expect `get_log_listener() is None` are not
poisoned by the CLI ones.

### For integration tests (in `integration_tests/conftest.py`)

The following fixtures are provided for working with real KiCad:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `adapter` | `session` | Single `KiCadBoardAdapter` instance for the entire session. |
| `board` | `session` | Board from the adapter. |
| `test_config` | `session` | Loaded test config from `kicadstamp_templates_example.sexp`. |
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

### The CI command — the reference run

```bash
python -m pytest --ignore=tests/integration_tests -m "not integration" -q
```

Run it inside the project's virtualenv (`source .venv/bin/activate`; the README says
how) — `python` there is the venv's interpreter, while a bare machine may only have
`python3`.

This is the command the project treats as the reference: it is what CI runs, and it
is what a "more tests pass than before" claim is measured with.

**A bare `pytest` no longer runs the integration cells.** `pytest.ini` now carries
`addopts = -m "not integration"`, so they are deselected BY CONSTRUCTION — the
`--ignore` above is a second belt, and the two spellings collect the SAME items. The
warning this paragraph replaces was earned: before that line, `testpaths = tests`
made a bare `pytest` COLLECT `tests/integration_tests/` as well, and with KiCad open on
a real board those cells WRITE TO THE LIVE BOARD. An explicit `-m` still wins over
`addopts` (pytest applies `addopts` first and the command line last), so
`-m integration` keeps selecting them on purpose — on a test board, with the board
saved, as the section below says. One consequence to know: `pytest
tests/integration_tests/` WITHOUT `-m integration` now collects ZERO cells — the path
and the `-m` filter multiply. That is the protection working, not a breakage.

### The three kinds by name

The marker already excludes integration, so `--ignore` above is a second belt and
the two spellings collect the SAME items:

```bash
python -m pytest -m unit -q            # pure Python, no Qt and no KiCad
python -m pytest -m gui -q             # Qt, offscreen
python -m pytest -m integration -q     # WRITES to the live board — on purpose only
```

The third line is the only command in this document that touches hardware: never reach
for it casually — on a test board, with the board saved, and after reading the warning
above.

One file, or one cell:

```bash
python -m pytest tests/geometry/test_spoke_layout.py -q
python -m pytest tests/geometry/test_spoke_layout.py::test_local_to_global -q
```

### The reverse-order run

```bash
python -m pytest --reverse-order -q
```

`--reverse-order` (an option in `tests/conftest.py`) reverses the FILE order of the
collected cells — the same run the bash-only pipeline
`python -m pytest $(find tests -name 'test_*.py' … | sort -r)` used to produce, now
one command on Linux, macOS AND Windows (that pipeline could not run on Windows at
all — Ф3.1). `tests/repo/test_reverse_order_option.py` proves the order really
reverses, so a `--reverse-order` that quietly did nothing cannot pass.

Order independence is a property the suite must KEEP, not a hope: the shared reset
and the marker hook exist so that a cell does not depend on what ran before it. A
green forward run next to a red reverse run means one of two things, and the
failure itself names which:

* a cell judges GLOBAL state it did not create — the class-A shape, where
  `caplog.records` is read by LEVEL over the whole root logger and a neighbour's
  record (measured: the pynng socket WARNING from a background thread) decides the
  verdict. The fix is `records_from` (see [Fixtures](#fixtures)).
* a cell has a RACE of its own — a worker thread it starts and does not wait for
  (measured: `threading.current_thread()` inside a `QThread` is a `_DummyThread`).

Both were real and both are fixed; the reverse run is part of acceptance now, not a
diagnostic to be reached for after the fact.

### Test plugins (required — the suite refuses to start without them)

The two test plugins are declared in `pytest.ini` under `required_plugins`
(`pytest-qt`, `pytest-timeout`), so a checkout that lacks them does not run a
weakened suite — it stops with `ERROR: Missing required plugins`:

- **pytest-qt** is not optional for the GUI tests. PyQt6 calls `qFatal()` when an
  exception escapes a Qt slot while `sys.excepthook` is still the default one —
  a core dump, `Fatal Python error: Aborted`, `EXIT=134`. The whole run is gone,
  and only the tests that had already finished reported anything. pytest-qt
  replaces the hook on every test phase and turns such an exception into a
  `FAILED` that names the file and line of the refusal (for the board door, the
  line that has to change). Declared `qt_api = pyqt6` on purpose: the measured
  behaviour is PyQt6's, and a machine may also carry PySide6.
- **pytest-timeout** is required because `timeout = 60` in `pytest.ini` does
  nothing at all without it: a hanging GUI test would be killed by the outer
  `timeout <N>` command instead, and the name of the test that hung would be
  lost.

Install them with `pip install -e ".[dev]"`; the same pins live in
`requirements.txt`, which is what CI installs.

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

ABRIDGED as well — the same Ф0-era core, so read it as "what these older cells pin",
not as a coverage map. A test added since then is described by its own docstring and
by whatever `tests/` holds today.

| File | What it tests |
|------|---------------|
| `test_author.py` | Scripting helpers: `_prune_defaults` (drops dataclass default fields), s-expr round‑trip for `ClonePlacement`/`Chain`, `apply_config` namespace compatibility with `cmd_apply`, `cli_main` entry‑point behaviour. |
| `test_cli_filters.py` | CLI filter logic: `--only NAME` (narrowing by name/net), `--cluster PATH` (narrowing by cluster path), `drop_disabled_rules`, `drop_inactive_items`, `--only`/`--cluster` composition (AND), `load_profile` root defaults and `include:` resolution. |
| `test_clone_anchor_id.py` | Anchor ID resolution for clones: `clone_anchor_id()` returns correct key based on `anchor_ref`/`anchor_role`/`name`. |
| `test_clone_geometry.py` | `ClonePlacement` geometry: local‑to‑global coordinate transformation, component angles, vias and tracks, mirroring (`mirror`), net resolution via `params` and `net_overrides`. Checks fatality of vias without a `net`. |
| `test_clone_ignore_selection.py` | `ignore_selection` flag: temporarily deselects components when processing a clone that should not be affected by user selection. |
| `test_clone_placement_config.py` | Loading `ClonePlacement` from s-expr, checking fields `cluster`, `cell`, `xy`, `rotation_deg`, `nets`, `params`, `net_overrides`, `retired`/`skip`/`ignore_selection`, `layer`/`mirror`, anchor fields. |
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
| `test_naming.py` | `chain_effective_name`/`thermal_via_array_effective_name` accessors, `name:` loading from s-expr, required name validation (fatality on missing name, and on a duplicate name within `thermal_via_arrays:`), optional Chain.name, Chain.retired/skip defaults. |
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

The directory holds 9 test files plus `conftest.py`. Six of them are described below;
the three the table does not cover are `test_apply_no_manual_nets.py` (a full apply
with NO hand-typed nets — every net resolved from the live board),
`test_pending_symbol_uuid.py` (the refdes/symbol identity guard on real data; its
schematic half is FILE-based and needs no KiCad at all, which is why it is the one
cell here that FAILS rather than errors when no board is reachable) and
`test_reextract_pad_numbering.py` (pad-numbering stability of the bridging roles,
re-extracted from the live board).

| File | What it tests |
|------|---------------|
| `test_live_adapter_connection.py` | Connection to KiCad, component lookup by refdes, net lookup by name, retrieving all vias. It was named `test_connection.py` until `a541ef7`, which renamed it to kill a basename collision with `tests/gui/test_connection.py`. |
| `test_via_ops.py` | Via creation/deletion, registry operation (`reconcile`, `record_created`), temporary via (`temp_via`). |
| `test_track_ops.py` | Track creation/deletion (straight copper segments) via the API – checks that `create_items` works for tracks. |
| `test_component_ops.py` | Component move by 1 mm on X and back, flip to the other side and restore. |
| `test_extract.py` | Template extraction from the current selection on the board (success with selection, error on empty selection). |
| `test_registry.py` | Full registry cycle with real KiCad: via creation, idempotency, position update (delete old, create new), prune. |

All integration tests use fixtures and restore the board to its original state after execution.

---

## Notes

- **Unit and GUI tests** need no KiCad: any machine with the dev extra installed
  runs them, and CI runs them on Linux and Windows.
- **Integration tests** need KiCad open with a board and are excluded from CI.
  Board directories are MACHINE-LOCAL and gitignored (`.gitignore` lists
  `test_boards/` and `boards/**`), so a fresh checkout carries no board at all —
  that is exactly why a bare `pytest` reports errors here. Prefer a test board
  (e.g. `test_boards/10CL006YE144C8G.kicad_pcb`) over a production project; the
  fixtures restore the board, but run them on a copy or after saving anyway.
- The three markers partition the collection exactly: `gui` + `unit` +
  `integration` = everything, and `tests/repo/test_marker_contract.py` fails if the
  partition ever stops being a partition (or if one kind stops being marked).

---

## Extending Tests

**Where a new test goes** — by what it needs, and the marker follows the path:

* pure logic → `tests/<domain>/test_<module>.py`, where `<domain>` mirrors the package
  or module under test (`kicadstamp/config/loader.py` → a new `tests/config/test_loader.py`;
  `kicadstamp/trees.py` → `tests/trees/`). When two domains could claim it, the module
  the file IMPORTS decides — not the count of imports, and not the domain whose helpers
  it happens to use;
* a dock, the window, a widget → `tests/gui/docks/` for one dock, `tests/gui/` for the
  window, the hub, the door, the worker, and for a guard that spans MANY docks. A guard
  whose subject is the window, the sockets or the UI thread stays in `tests/gui/`,
  whatever docks it imports;
* a guard that reads the REPOSITORY itself (source text, the marker partition, the fake
  conformance, the board door, the timeout sweep) → `tests/repo/`;
* live KiCad → `tests/integration_tests/` plus `@pytest.mark.integration` (the
  path already marks it; the decorator is the belt to the path's braces).

**Two rules that follow from the layout:**

* **never derive a tree path from `Path(__file__)`.** Import `tests.paths`
  (`REPO_ROOT`, `FIXTURES_DIR`) instead. A test one level deeper silently reads a
  different directory, and where that path only feeds a message the suite stays green
  while the guard goes blind;
* **a test file's BASENAME must stay unique across the whole suite.** pytest imports
  collected modules by basename, so two `test_loader.py` under different domains collide
  with `import file mismatch`. That is why Ф2 moved files without renaming any.

**Where a new DOUBLE or HELPER goes:**

* a double used by MORE than one file → `tests/fakes/`, and add a cell for it to
  `tests/repo/test_fakes_conformance.py` (the cell pins the double against the real
  interface, in both directions: it must invent nothing, and the names it
  declares as gaps must really exist on the concrete class);
* a double used by ONE file → that file, named with a leading underscore
  (`_FakeBoard`) — `tests/fakes/` is for what is shared, and a name that exists
  there must not be redefined in a test file;
* a builder (data, text, a wired board) → `tests/fakes/`, by purpose, not the
  `tests/` root: `schematic_text.py`, `overrides_store.py`, `live_board.py`;
* a pytest FIXTURE → the `conftest.py` of the level that needs it.

**Four rules a new test must not break** (`tests/repo/test_repo_hygiene.py` guards
them, and every guard fails loudly if its own scan goes blind):

1. no `sys.path.insert` under `tests/` — `pythonpath = .` replaced all 120 of them;
2. `kicadstamp` must resolve INSIDE the run's rootdir (an editable install from
   another checkout makes the suite measure code nobody is editing);
3. a name that lives in `tests/fakes/` must not be defined again in a test file;
4. no assignment to an attribute of an imported module or name outside
   `monkeypatch` (the few exceptions are listed there, each with a measured
   reason, and the list is checked for rot).

**Byte snapshots: when they are legitimate.** Exact BYTES may be compared only
where the bytes ARE the subject: the s-expr WRITER (`test_sexp_config_roundtrip.py`),
a stored fixture, or the format-version guard. Everywhere else compare by MEANING
(`sexp_to_dict` plus the `_strip_defaults` normalization the config layer itself
uses) and say in a comment which of the two the cell is doing. The reason is
measured, not stylistic: Ф1.3 classified all 84 byte assertions in the suite — 26
semantic, 7 genuine writer/template ones, 51 living in files that never touch the
bytes. A snapshot of a value the writer never produces turns a rename into a red
test.

**Acceptance rigs (mutation checks — not part of the suite).**
`kicadstamp/diagnostics/claude_mutations_accept_*.py`: 13 harnesses, each
applying one named mutation to the product and reporting whether a guard went red
(`KILLED` / `SURVIVED` / `INVALID`). Run them BY HAND, against a checkout that is
NOT the tree they are started from:

```bash
KICADSTAMP_ACCEPT_ROOT=<a separate checkout> \
KICADSTAMP_PYTHON="$PWD/.venv/bin/python" \
  "$PWD/.venv/bin/python" kicadstamp/diagnostics/<rig>.py
```

Two measured traps: with the accept root equal to the current tree, the rig whose
mutation is "the inner run imports the MAIN checkout" becomes a no-op and reports
SURVIVED; and an INVALID row ("pattern occurs 0 times") means the MUTATION is
stale, not that the suite is strong. The pre-Ф1 reference is 127 killed / 7
survived / 6 invalid.

**After Ф2 the rigs' `T` lists name TEST PATHS that moved** (they were written for the
flat layout), so a rig run today reports INVALID rows for files it can no longer find.
That is a STALE RIG, not a weakened guard, and it is deliberate: Ф2 did not re-pin any
rig, and re-pinning the last rig of each walk is Ф3 work. The pre-Ф1 numbers above
therefore describe the suite as it was BEFORE the domain split, and a rig must not be
run to "confirm" Ф2 before its `T` list is re-pinned.

---

## License

The tests are distributed under the MIT license, the same as the main project.
