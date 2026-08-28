# `kicadstamp/diagnostics/` – Diagnostic Scripts

## Purpose

The `kicadstamp/diagnostics/` directory contains a set of diagnostic and debugging scripts that help
developers and advanced users verify **KiCadStamp**'s behaviour, debug configurations, analyse geometry,
and test individual IPC operations. The scripts use the current `kicadstamp` API (adapter, geometry,
config) and do not depend on legacy modules.

All scripts require a **running KiCad instance** with an active board and are run from the project root
via `python -m`.

---

## Structure

All diagnostic scripts live in the **single namespace** `kicadstamp/diagnostics/`
(including the probes previously scattered in the top-level `diagnostics/` folder).
The tree below marks each script's KiCad requirement:

- `[LIVE]` — requires a running KiCad with the board open.
- `[LIVE+WRITE]` — also writes / mutates the board.
- `[FILES]` — reads local files only, no IPC.

```
kicadstamp/diagnostics/
├── diagnose_first_write_crash.py  # Diagnoses the KiCad crash on the first IPC write (issue #24966) [LIVE]
├── diagnostic_charset.py          # Finds non-ASCII characters (homoglyphs) in Role/Cluster board-wide [LIVE]
├── diagnostic_keepout.py          # Keepout and overlap analysis [LIVE]
├── get_pad_bbox.py                # Pad bounding box [LIVE]
├── get_selected_component.py      # Detailed info on selected components [LIVE]
├── get_selection.py               # List of selected objects [LIVE]
├── test_create_one_via.py         # Creates a single via [LIVE+WRITE]
├── test_custom_fields.py          # Verifies reading the Role field [LIVE]
├── test_flip_one_cap.py           # Verifies flipping a single component [LIVE+WRITE]
├── test_move_one_cap.py           # Verifies moving a single component [LIVE+WRITE]
├── test_pad_mirror_convention.py  # Verifies the pad-mirroring convention [LIVE]
├── diagnose_points.py             # Brute-force probe for the kipy "Points" type [LIVE]
├── group_by_sheet_path.py         # Groups components by their sheet_path UUID chain [LIVE]
├── kipy_uuild_resolver.py         # Lists every net with the refs connected [LIVE]
├── local_net_ierarchy.py          # Dumps all local (hierarchical) net names [LIVE]
├── netlist_resolver.py            # Deep dump of fp.sheet_path attributes [LIVE]
├── probe_footprints_fields.py     # Read/write of custom fields on a placed footprint [LIVE+WRITE]
├── probe_kicad_sch_uuids.py       # Two-step UUID bridge vs *.kicad_sch [FILES / LIVE step 2]
├── probe_path_minus_last.py       # sheet_path.path[:-1] grouping vs {uuid: Sheetname} [LIVE]
├── probe_pi_filter_ambiguity.py   # Role/Cluster/sheet-path/nets for refs (ambiguity) [LIVE]
├── probe_sheet_path_truncation.py # path[:-1]/path[1:] grouping vs local-net paths [LIVE]
├── probe_uuid_stability.py        # fp.id.value survives re-annotation? snapshot+compare [LIVE snapshot / FILES compare]
├── probe_uuid_to_sheet_name.py    # {UUID chain -> human path} from local nets [LIVE]
├── recon_symbol_uuid_bridge.py    # Symbol-uuid bridge: schematic vs board sheet_path (recon) [FILES / LIVE optional]
├── resolve_paths.py               # Human sheet paths from a .net file [LIVE]
├── role_resolver.py               # Raw proto dump of sheet_path [LIVE]
├── test_ierarchy.py               # Footprints vs schematic sheet map [LIVE]
├── test_ierarchy_uuid.py          # Raw sheet_path.path form [LIVE]
├── test_sheet_path.py             # path_human_readable on a live board [LIVE]
└── unersolved_components.py       # Per-component channel (Channel_0/1/2) by nets [LIVE]
```

### Header convention

Every script in this directory opens with a module docstring stating, in this order:

- **Input** — what the script needs (arguments, config path, a live board, ...).
- **Expected** — what it prints / verifies / writes.
- **Live KiCad** — whether a running KiCad with the board open is required
  (`Yes`), only for part of the run (`Partially`), or not at all (`No`).
- **Run** — the canonical `python -m kicadstamp.diagnostics.<script> ...` command.

The `Live KiCad` field is the authoritative per-file marker of live-only probes;
the structure tree above uses the same legend (`[LIVE]` / `[LIVE+WRITE]` / `[FILES]`).

---

## Script descriptions

### `diagnose_first_write_crash.py`

Diagnoses the KiCad crash on the first IPC write (issue #24966). The full description, hypotheses H1-H3,
parameters, output, and dependencies live in a separate document, since this is the only script in this
set tied to one specific filed bug with its own dedicated hunting workflow:
**[diagnose_first_write_crash.md](diagnose_first_write_crash.md)**. A description of both related bugs
(#24966/#24970) and the rest of the hunting toolkit lives in [crash_hunting.md](crash_hunting.md).

```bash
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # reads only, safe
python -m kicadstamp.diagnostics.diagnose_first_write_crash             # full test, may crash KiCad
```

---

### `diagnostic_charset.py`

**Purpose:**
Walks every footprint on the board (by default the `ROLE_FIELD_NAME` and `CLUSTER_FIELD_NAME` constants — `"Role"` and `"Cluster"` — configurable via
`--fields`) and looks for characters outside printable ASCII (`0x20`–`0x7E`). The script exists because of
a live finding on `3CH-AWG-TIA`: three components (`C3`, `C9`, `C170`) had a `ROLE_FIELD_NAME` value whose first
letter was the Cyrillic "С" (`U+0421`) instead of the Latin "C" (`U+0043`) — apparently the keyboard
layout had switched to Russian mid-way through typing the field value in Eeschema Bulk Edit. The letters
are visually indistinguishable in almost any font, but `component_pool.py`/`clone_role_resolver.py`
compare fields (`ROLE_FIELD_NAME`/`CLUSTER_FIELD_NAME`) with strict character-by-character equality — a component with this typo matches no rule
looking for the "correct" (Latin) role, and the mismatch is essentially impossible to spot by eye.

**Usage:**
```bash
# Check ROLE_FIELD_NAME and CLUSTER_FIELD_NAME board-wide (default)
python -m kicadstamp.diagnostics.diagnostic_charset

# Check a different set of fields
python -m kicadstamp.diagnostics.diagnostic_charset --fields "Role,Cluster,Value"

# Also print clean fields (not just findings)
python -m kicadstamp.diagnostics.diagnostic_charset --verbose
```

**Parameters:**
- `--fields` – comma-separated list of fields, no spaces (default `ROLE_FIELD_NAME,CLUSTER_FIELD_NAME` i.e. `"Role,Cluster"`).
- `--timeout-ms` – IPC timeout (default `20000`).
- `--verbose` – also log "clean" fields (no findings).

**Output:**
A list of findings: refdes, field name, the value in full, and for each "bad" character — its position in
the string, the character itself, its codepoint (`U+XXXX`), and its Unicode name (`unicodedata.name`).
Exit code is `0` if nothing was found, `1` if at least one field had a finding (handy as a standalone step
before `apply` or in CI:
`python -m kicadstamp.diagnostics.diagnostic_charset || echo "suspicious characters found in Role/Cluster fields"`).

**Dependencies:**
`kicadstamp.kicad.adapter.KiCadBoardAdapter` (`get_footprints`/`get_field_value`), `unicodedata` from the
standard library.

---

### `diagnostic_keepout.py`

**Purpose:**
Loads the config, plans the placement, builds keepout from IC and component pads, then checks whether
component and via positions fall inside the keepout. Prints detailed information for debugging.

**Usage:**
```bash
python -m kicadstamp.diagnostics.diagnostic_keepout <config.sexp>
```

**Output:**
- A list of keepout rectangles with coordinates.
- Status (INSIDE/CLEAR) for each component.
- Status for each via (spoke and component).

**Dependencies:**
`kicadstamp.config`, `kicadstamp.kicad.adapter`, `kicadstamp.placement.planner`, `kicadstamp.geometry.keepout`.

---

### `get_pad_bbox.py`

**Purpose:**
Prints a pad's bounding box (size, position) and the copper layer's size (if available). Useful for
verifying pad geometry.

**Usage:**
```bash
python -m kicadstamp.diagnostics.get_pad_bbox --ref IC1 --pad 17 --verbose
```

**Parameters:**
- `--ref` – component refdes (default `IC1`).
- `--pad` – pad number (shows all if omitted).
- `--timeout` – IPC timeout (ms).
- `--verbose` – verbose output.

**Output:**
- Bbox size (mm).
- Bbox position.
- Copper layer size (if available).

**Dependencies:**
`kicadstamp.kicad.adapter`, `kicadstamp.geometry.thermal_grid`.

---

### `get_selected_component.py`

**Purpose:**
Prints detailed information about the selected components: refdes, value, footprint, position, angle,
size (bbox), the list of pads (numbers, nets, positions, sizes), and the `Role` field. Handles groups
(Group) correctly.

**Usage:**
Select components in the PCB editor, then run:
```bash
python -m kicadstamp.diagnostics.get_selected_component
```

**Output:**
A table with information about each component and its pads.

**Dependencies:**
`kicadstamp.kicad.adapter` (uses `get_selected_items`).

---

### `get_selection.py`

**Purpose:**
A simple diagnostic script that lists all selected objects (footprints, pads, tracks, vias) with their
types and key parameters.

**Usage:**
Select objects in the PCB editor, then run:
```bash
python -m kicadstamp.diagnostics.get_selection
```

**Output:**
A list of objects with type and key properties.

**Dependencies:**
`kicadstamp.kicad.adapter` (uses `get_selected_items`).

---

### `test_create_one_via.py`

**Purpose:**
Creates a single via next to a given component. Saves the UUID of the created via to
`.last_test_via.json` for later removal. Lets you verify `create_items` and transactions work.

**Usage:**
```bash
# Create a via
python -m kicadstamp.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Remove the last created via
python -m kicadstamp.diagnostics.test_create_one_via --remove

# Remove a specific via by UUID
python -m kicadstamp.diagnostics.test_create_one_via --remove <uuid>
```

**Parameters:**
- `--offset-mm` – offset from the component's center (mm).
- `--net` – the via's net (default `GND`).
- `--drill-mm` – drill diameter.
- `--diameter-mm` – outer diameter.
- `--timeout-ms` – IPC timeout.

**Dependencies:**
`kicadstamp.kicad.adapter`.

---

### `test_custom_fields.py`

**Purpose:**
Verifies reading a component's custom field via IPC. Prints all texts and fields (`Field`) of the
component, then looks for a field with a given name (default `Role`). Critical for verifying that roles
work correctly.

**Usage:**
```bash
python -m kicadstamp.diagnostics.test_custom_fields C5 --field Role
```

**Parameters:**
- `--field` – name of the field to look for (default `Role`).
- `--timeout-ms` – IPC timeout.
- `--verbose` – verbose output.

**Output:**
- A list of all of the component's fields and texts.
- The value of the requested field (or a message that it wasn't found).

**Dependencies:**
`kicadstamp.kicad.adapter` (uses `get_field_value`).

---

### `test_flip_one_cap.py`

**Purpose:**
Verifies a "real" component flip via the GUI action `pcbnew.InteractiveEdit.flip`. Prints the component's
state before and after the flip. Lets you confirm the flip works correctly (layer and mirroring).

**Usage:**
```bash
python -m kicadstamp.diagnostics.test_flip_one_cap C6
```

**Parameters:**
- `--timeout-ms` – IPC timeout.

**Output:**
Component state (layer, position, angle) before and after the flip.

**Dependencies:**
`kicadstamp.kicad.adapter` (uses `flip_selected` and `refresh_board`).

---

### `test_move_one_cap.py`

**Purpose:**
Verifies moving a single component a given distance along the X axis. Lets you isolate transaction
problems (`begin_commit`, `update_items`, `push_commit` hanging).

**Usage:**
```bash
# Move by +1 mm
python -m kicadstamp.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Move it back
python -m kicadstamp.diagnostics.test_move_one_cap C5 --revert
```

**Parameters:**
- `--delta-mm` – shift amount (mm).
- `--revert` – shift in the opposite direction.
- `--timeout-ms` – IPC timeout.

**Output:**
Execution time for each step (connect, begin_commit, update_items, push_commit) in milliseconds.

**Dependencies:**
`kicadstamp.kicad.adapter`.

---

### `test_pad_mirror_convention.py`

**Purpose:**
Verifies the mirroring convention for a pad's local offset on flip (used in
`geometry/pad_projection.py`). Runs two steps: a 90° rotation without flipping (checks the base formula),
then a flip and comparison of three candidates (mirror across X, mirror across Y, no mirror). Restores
the component to its original state afterwards.

**Usage:**
```bash
python -m kicadstamp.diagnostics.test_pad_mirror_convention C6 --pad 2
```

**Parameters:**
- `--pad` – pad number to track (default `2`).
- `--timeout-ms` – IPC timeout.

**Output:**
- Base-formula discrepancy after the rotation.
- Distances for the three candidates after the flip.
- The winner (mirror across X, across Y, or no mirror).

**Dependencies:**
`kicadstamp.kicad.adapter`, `kicadstamp.geometry.pad_projection` (helper).

---

### `probe_uuid_stability.py`

**Purpose:**
Checks whether a footprint's own UUID (`fp.id.value`) survives schematic re-annotation. `snapshot`
captures every board footprint's `ref`/`id`/`footprint`/`sheet_path` to JSON; `compare` diffs two
snapshots offline and reports whether the *set* of UUIDs changed between them — that is the actual
instability signal. A refdes moving onto the same UUID is expected after re-annotation and is
reported separately (with `-v`), not treated as a discrepancy. See the 2026-08-07 empirical result
in `techdocs/handoff/handoff_2026_08_07_uuid_stability_probe.md`: footprint UUIDs stayed identical
across two independent full-reset re-annotation scenarios on a 279-footprint board.

**Usage:**
```bash
python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_before.json
# ... re-annotate in Eeschema, then Update PCB from Schematic
#     (Match Method = "Re-associate by UUID/timestamp") ...
python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_after.json
python -m kicadstamp.diagnostics.probe_uuid_stability compare uuid_before.json uuid_after.json -v
```

**Parameters:**
- `snapshot <output>` – output JSON path (requires live KiCad).
- `compare <before> <after>` – two previously captured snapshot JSON files (offline, no KiCad needed).
- `-v`, `--verbose` (compare only) – also list refdes remaps for UUIDs that stayed the same.

**Output:**
- `snapshot`: a JSON file with capture metadata (timestamp, KiCad version, footprint count) plus a
  `footprints` list (`ref`/`id`/`footprint`/`sheet_path` per entry).
- `compare`: counts (before/after/common/added/removed/ref-changed), then a block listing any UUIDs
  present in one snapshot but not the other (the real discrepancy), and, with `-v`, a table of
  refdes remaps for UUIDs that matched in both. Exit code `1` if the UUID set changed, `0` if it
  didn't.

**Dependencies:**
`kipy` (`snapshot` only); `compare` has no KiCad dependency at all, standard library `json`/`argparse` only.

---

### `recon_symbol_uuid_bridge.py`

**Purpose:**
Reconnaissance for the "Pending Changes matches schematic-vs-board purely by refdes"
problem ([`compute_pending_edits()`](../gui/docks/pending.py) joins the two sides by the
refdes STRING). It checks whether a UUID join key uniquely identifies a physical symbol
instance on BOTH sides: is `fp.sheet_path.path[-1]` unique per footprint, is the FULL
`sheet_path.path` unique per instance, and does the board's last path element equal the
`(symbol ...)` block's top-level `(uuid ...)` in `.kicad_sch`?

Empirical result (2026-08-08, `3CH-AWG-TIA`, live board): `path[-1]` is the **master-symbol
uuid** shared by all clones of a multi-instance sheet (66 values shared by 2+ refdes), so it
is NOT unique per footprint; the **full** `sheet_path.path` IS unique per footprint
(364/364); the board `path[-1]` equals the schematic top-level symbol uuid (279/279 on the
saved `.kicad_pcb`, 358/364 live). The exact 1:1 join key is
`board path == (schematic (instances ...) path minus root uuid) + block top uuid`. The full
experiment, with numbers and design conclusion, is in
`techdocs/handoff/deepseek/handoff_2026_08_08_symbol_uuid_recon.md`.

**Usage:**
```bash
python -m kicadstamp.diagnostics.recon_symbol_uuid_bridge boards/3CH-AWG-TIA
```

**Parameters:**
- positional `project_dir` – project directory holding the `*.kicad_sch` files and the
  `<name>.kicad_pcb` board file (default `boards/3CH-AWG-TIA`).

**Output:**
- schematic stats: blocks with top-level `(uuid ...)`, `(instances ...)` path structure
  (path lengths, whether the path-last is a sheet or a symbol uuid);
- board stats: full-path uniqueness per footprint, path-last sharing across refdes;
- uuid bridge: how many board `path[-1]` are schematic symbol uuids;
- full-path join rate of board footprints against the schematic key map;
- refdes desync count (board refdes vs schematic refdes for the same symbol uuid — a
  non-zero number proves the refdes-string join silently mismatches components);
- per-instance resolution demo for multi-instance symbols.

**Dependencies:**
`kicadstamp.schematic_blocks.find_balanced_span` (span/regex parsing of `.kicad_sch` and
`.kicad_pcb`, no sexpdata round-trip); optional `kipy` for the live-board cross-check
(the saved `.kicad_pcb` already carries the authoritative `(path ...)`).

---

## General recommendations

- **Run with `--verbose`** for debugging, if the script supports the flag.
- **Always run from the project root** using `python -m kicadstamp.diagnostics.<script_name>`.
- **Make sure KiCad is open** with the relevant board active — unless the script's header says
  `Live KiCad: No` / `[FILES]` (the only scripts that run without a live session are the local-file
  readers).
- For scripts that work with the selection, select the relevant objects in the PCB editor **before**
  running them.

---

## Notes

- The scripts **do not modify the board** (except for `test_move_one_cap`, `test_flip_one_cap`,
  `test_create_one_via`, and `probe_footprints_fields`, which can mutate it). Use them on test
  boards or make sure you have a backup.
- `diagnose_first_write_crash.py` does not mutate the board (the write is a no-op), but on an affected
  session (see issue #24966) the write attempt itself can **crash the KiCad process entirely**. Save open
  files before running the full ladder (without `--until 8`).
- `test_move_one_cap`, `test_flip_one_cap`, and `test_create_one_via` **do not use** the placement
  registry, so they are not undone by the `undo` command.
- For a full placement diagnosis, run `diagnostic_keepout.py` with the actual config.

---

## Extending the diagnostic scripts

To add a new diagnostic script:

1. Place it in `kicadstamp/diagnostics/`.
2. Use the current `kicadstamp` API (adapter, geometry, config).
3. Give it the header convention: `Input` / `Expected` / `Live KiCad` / `Run`.
4. Add a description to this document (with the `[LIVE]` / `[LIVE+WRITE]` / `[FILES]` marker).
5. Make sure the script doesn't modify the board (or warns about it), unless it's meant to mutate.

---

## License

The diagnostic scripts are distributed under the MIT license, same as the main project.
