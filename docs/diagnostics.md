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
├── pad_geometry_probe.py          # KiCad's pad box vs the pad's own area; thermal-via keepout [LIVE]
├── get_selected_component.py      # Detailed info on selected components [LIVE]
├── get_selection.py               # List of selected objects [LIVE]
├── test_create_one_via.py         # Creates a single via [LIVE+WRITE]
├── transform_template.py          # Shifts a template origin, then rotates/mirrors [FILES]
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
├── board_call_timing.py           # Times every adapter call (library; also a Settings switch)
├── run_gui_with_timing.py         # Runs the GUI with every board call timed [LIVE]
├── report_board_timing.py         # Summarises a board-call timing log [FILES]
├── board_read_probe.py            # Records who reads connection.board (library; also a Settings switch)
├── run_gui_with_read_probe.py     # Runs the GUI with every board read recorded [LIVE]
├── report_board_reads.py          # Summarises a board-read log [FILES]
├── probe_placement_cost.py        # Cost breakdown of one placement's planning phase [LIVE]
├── probe_field_map_unit_cost.py   # Field map: cost of reading it vs. building it [LIVE]
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

### `run_gui_with_timing.py` / `report_board_timing.py`

Measures where a GUI session actually spends its time inside the board adapter — per call,
per thread, and (since 2026-09-13) per call site for the calls made on the UI thread. Two
steps: record, then summarise.

```bash
python -m kicadstamp.diagnostics.run_gui_with_timing   # work the docks as usual, then quit
python -m kicadstamp.diagnostics.report_board_timing
```

#### Two ways to switch the recorder on

Since 2026-09-13 (plan `plan_2026_09_13_diagnostics_switch`) the same recorder can be turned
on **while the GUI keeps running**:

- **the external launcher above** — the recorder is a monkey patch installed *before* the GUI
  starts (`board_call_timing.py`), so the log covers the session from the very first second.
  Use it for a clean measurement; no production file is edited and nothing has to be reverted;
- **Settings → Diagnostics** (the **Diagnostics** page) — for the case the tool actually exists
  for: something looks odd, you switch recording on, keep working, switch it off. Restarting the
  GUI (the only option before this page) would scare the oddity away.

The switch is persisted in `gui_state.json`, so a recording found ON starts again at the NEXT
startup — and says so in the Log (a forgotten switch is never silent). **The Log gets exactly
TWO lines per session**: "recording → path" and "stopped, N calls → path". One line per call
would drown the Log, so the data goes to JSONL only. A session stops itself at **50 MB**, with a
Log line naming the reason and the path — a switch left on over a weekend must not fill the disk.

Both ways write the same thing: JSON Lines in `<repo>/diagnostics/board_timing_<pid>.jsonl`
(gitignored, one self-contained object per line, flushed immediately — a crash mid-session still
leaves a readable log). **Board data is never recorded**: only call names, durations, item counts
and the calling thread, so a log is safe to attach to a bug report.

#### Reading the report

The report prints per-method counts with median/p90/max, the share of the session spent
inside the adapter, a per-thread split, failed calls, and the ten slowest single calls. It also
prints a **UI-thread calls** section: method, number of calls, summed and maximum time and —
where it was recorded — the **call site** (`file:line`) of the caller, outermost rows only, by
descending total time. When there is none it says so, and says which of the two reasons applies.

Three numbers are worth knowing what to do with:

- **the per-thread split** answers "does the UI thread read the board", which is what decides
  whether a synchronous read is a real freeze or a theoretical one;
- **the UI-thread section** names the offenders: the calls on the UI thread are the ones that
  can freeze the window, and a call site is what makes a fix possible without searching;
- **the slowest bulk calls** size `DEFAULT_TIMEOUT_MS` from data rather than a guess.

A call site is recorded **only for calls made on the UI thread** — the GUI installs that
predicate when it turns recording on (the recorder itself never imports Qt, and the external
launcher installs none). That is ~0.4% of the calls; walking the stack for all 81 000 of them
would not be. It is also why a thread NAME is not used: in the MCP server process and in the CLI
every call legitimately happens on `MainThread`.

Measured on a live 325-footprint board, 2026-09-13: a 272 s session spent **9.1 s (3.3%)**
inside the adapter, of which the UI thread accounted for **0.2 s (0.07%)**, and the slowest
single call of the whole session was **287 ms** against a 20 000 ms timeout. That measurement
is what retired a planned migration of every board read behind `await` — the freeze it would
have cured was not there.

---

### `run_gui_with_read_probe.py` / `report_board_reads.py`

Records **who reads the live board, from which thread and call site** — the input the follow-up
"board access" task needs to choose its enforcement (a warning in the log, a failure in tests, or
refusing to hand the board over). Two steps: record, then summarise.

```bash
python -m kicadstamp.diagnostics.run_gui_with_read_probe   # work the docks as usual, then quit
python -m kicadstamp.diagnostics.report_board_reads
```

Like the timing recorder, this one has the same two switches (2026-09-13, plan
`plan_2026_09_13_diagnostics_switch`): the external launcher above, or **Settings → Diagnostics**
while the GUI keeps running — same persistence in `gui_state.json`, same "two Log lines per
session, never one per read", same 50 MB self-stop. The probe lives in the GUI process
(`gui/connection.py`), so there `MainThread` really is the UI thread; the report marks it.

The recorder turns on a hook that already lives in `gui/connection.py`: the `board` property carries
an optional, **disabled-by-default** probe and only tests it, so no production file is edited and the
probe costs nothing until diagnostics installs it. It writes JSON Lines to
`<repo>/diagnostics/board_reads_<pid>.jsonl` (gitignored), one self-contained object per read with
the thread name and ONE stack frame — the immediate caller — never a full stack walk, because the
point is to survive a live session with tens of thousands of reads. **Board data is never recorded**:
only the thread and the call site, so a log is safe to attach to a bug report. Reads made **inside**
`gui/connection.py` itself (`self.board` in `refresh`/`disconnect`/...) are not recorded: the
counter is about *consumers* of the board, and counting the connection's own reads would only add
UI-thread noise.

The report prints reads by thread and by call site, both descending, and then repeats the
**UI-thread (MainThread) reads in their own block** — that is the thing being hunted. A read from
the UI thread is what the next task judges, and these counts make the actual offenders visible
instead of guessed.

The same step also closed the last way into the board that bypassed the adapter (`adapter._board`):
the overlay and the copper-layer list now go through four reads declared on `IBoardAdapter`
(`get_layer_name`, `get_enabled_layers`, `get_visible_layers`, `get_shapes`).

**Since 2026-09-21 there is a GUARD in the same place, not just a counter** (plan
`plan_2026_09_21_board_door_enforcement`). The probe counts; the guard refuses: reading
`connection.board` from the UI thread **without a sign** raises `UiThreadBoardReadRefused` whose
message names the CALLER's `file:line`, and the Log gets ONE line per site (the probe, when it is
on, still counts the attempt — it runs first). Out of its jurisdiction, deliberately: a value of
`None` (that is a "there is no connection" check, no socket is touched) and every thread other
than the UI one. The three sanctioned ways through are `connection.is_connected` for a presence
check, a worker (`start_long_op`) for a real board read, and `with ui_thread_board_read(reason=...)`
for a read that belongs on the UI thread — the sign is written at the call site, it is per thread
and `reason` is mandatory, so "deliberate" has to be said in words where the next reader sees it.
What counts as "the UI thread" is an INJECTED predicate
(`gui.connection.set_ui_thread_predicate`), armed by the GUI's process entry point
(`kicadstamp/gui_main.py`) and never by `MainWindow.__init__` — a test that builds a window must
not inherit the guard. Its watchdogs are `tests/test_board_door_guard.py` (one per cell of the
property table, each with a mutation that turns it red).

---

### `probe_placement_cost.py`

Where one placement run actually spends its time. Reproduces apply's planning phase on a real
config (read-only — `execute_moves()` is never called) and prints a cost breakdown: startup
steps, unit costs (refresh / cold vs. warm `get_footprints` / one targeted re-read), the
wall-clock split between the IPC socket and Python, per-adapter-method call counts, and a
cProfile top list.

Since 2026-09-13 it reproduces Phase 1 **twice in one run**: once with the legacy loop
(`refresh_board()` before every item) and once with the current one (a targeted re-read of the
previous item's footprints, through the very `ApplyPipeline._moved_footprint_uuids` production
uses). Both passes see the same board and the same config, so the difference between them is
the refresh strategy and nothing else.

```bash
python -m kicadstamp.diagnostics.probe_placement_cost profiles/3ch-awg-tia-v103/config.sexp
```

Measured 2026-09-13 on the 325-footprint `3ch-awg-tia-v103` board, 20 items in the config
(base `bf59e24`): **6.33 s** wall clock, of which `get_field_value()` took **2279.6 ms over
52170 calls** (0.04 ms each) and `builtins.isinstance` 1 991 659 calls / 1.28 s — a linear
scan of every footprint's `texts_and_fields` repeated on each read, i.e. roughly 160 full
passes over the board's fields per placement run.

After the Э1 field map (adapter `_field_values_for`, 2026-09-13) the same probe reports
**4.06 s** wall clock and **604.9 ms** for the same 52170 calls (0.01 ms each), with
`isinstance` gone from the profile entirely (total function calls 8 524 572 → 3 701 019).
What is left in that row is mostly the probe's own instrumentation, not work — see
`probe_field_map_unit_cost.py` below. The remaining seconds are elsewhere: 20 full-board IPC
re-reads (2.39 s, one per item — `refresh_board()` in `apply_pipeline.py`) plus 1.16 s of
socket wait.

Worth noting from those same two runs: execution-order resolution (`resolve execution order`)
fell from 953 ms to 146 ms, because it resolves every rule/clone_placement's anchor and so
paid the same per-read scan.

After the targeted re-read (2026-09-13, `adapter.reread_footprints_by_id` +
`ApplyPipeline._moved_footprint_uuids`) the probe's own two blocks, same run, same 325-footprint
board, 20 items, 158 planned moves:

| | A) legacy loop | B) current loop |
|---|---|---|
| wall clock | 4.47 s | **1.20 s** (−73.2%) |
| full board reads (`kipy board.get_items`) | 20 | **1** |
| targeted reads (`kipy board.get_items_by_id`) | 0 | 19 |
| waiting on the socket | 1.50 s | 0.13 s |
| `get_field_value` (the same 52170 calls) | 636.5 ms | 188.8 ms |
| unit cost: one targeted re-read (1 footprint, cache warm) | — | 1.85 ms |

The 19 targeted reads cost 97.1 ms in total (5.11 ms each, ~8 footprints per item) where the 19
full re-reads they replace cost 2.39 s. The field row improves for the same reason: the map is
now rebuilt for the ~8 footprints an item moved instead of for all 325, so a run rebuilds a few
hundred maps instead of 6500. The one remaining full read is the initial `refresh_board()`; the
count returns to one per item only on a fallback (a flip, or a moved footprint with no uuid to
ask about — see the probe's own output in that case).

---

### `probe_field_map_unit_cost.py`

Splits the leftover cost of the field map after Э1 into reading a map that is already built
vs. building it. Needs no config — it reads whatever board is open.

```bash
python -m kicadstamp.diagnostics.probe_field_map_unit_cost
```

Measured 2026-09-13, 325-footprint board: a full-board `get_field_value()` sweep costs
**0.19 ms (0.6 µs per footprint)** with the maps warm and **10.28 ms (31 µs per footprint)**
when they have to be built — so a placement run pays about **10 ms per `refresh_board()`
generation** (20 generations ≈ 206 ms) and almost nothing per call. A rebuild is expensive
because `texts_and_fields` builds a fresh `Footprint` definition wrapper, whose `__init__`
unwraps every item of the footprint (`kipy/board_types.py:1832`). That is why the follow-up
question "index by role (Э3)" was answered no: it removes *calls*, not *builds*.

---

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

### `pad_geometry_probe.py`

**Purpose:**
Shows where KiCad SAYS a pad is versus where its copper actually is, and what that does to one
`thermal_via_arrays` entry. Two sections plus one result line.

**Usage:**
```bash
python -m kicadstamp.diagnostics.pad_geometry_probe profiles/3ch-awg-tia-v103/config.sexp --thermal ad_dac_via_pad
python -m kicadstamp.diagnostics.pad_geometry_probe profiles/3ch-awg-tia-v103/config.sexp --ref IC2
```

**Output:**
- **A** — every pad of the footprint: its number, the padstack shape, the body angle and the pad's OWN
  angle, the centre of KiCad's bounding box MINUS the centre of the pad's own area (`dx dy |d|` mm) and
  the size of both. A pad without an area of its own (`custom`/`unknown`, no `size`) is flagged as such,
  and a WARNING line counts them — those are the pads the fallback cannot cover.
- **B** (`--thermal` only) — the ideal grid of that entry's pad, point by point: blocked under the OLD
  rule (KiCad's boxes as axis-aligned `Rect`s, recomputed inside the probe) and under the CURRENT one
  (`ViaPlanner._build_keepout`, the pads' own areas), and then the real `ViaPlanner.plan_vias` for this
  ONE entry (planning only) — the distance from the ideal point to the via that was placed for it and
  whether that via lies on the thermal pad's own area.
- **Result line** — `old: X of N blocked; new: Y of N blocked; placed off pad: Z`. At 0° the two rules
  must agree and Z must be 0; under a rotation that is not a multiple of 90° the OLD column is the one
  that goes wrong.

**Read-only:** no executor, no registry, no write to the board; `plan_vias` is called for its planning
result only.

**Background:** measured 15.09.2026 — for a footprint rotated to 315° KiCad returned the bounding box of
EVERY pad of that footprint shifted by the same 1.724 mm, while the pad's position, its copper size and
its padstack angle stayed correct. An IPC-applied rotation on the Linux test board did NOT reproduce
that shift (0.000 mm for every pad, before the write, right after it and after a re-read), so the
per-pad number this probe prints is what tells one stand from another. The box is also an axis-aligned
AABB around a ROTATED pad, which the same table shows as a size difference: 0.813 × 0.813 mm against
the pad's own 0.300 × 0.850 mm.

**Dependencies:**
`kicadstamp.config`, `kicadstamp.kicad.adapter`, `kicadstamp.placement.services.via_planner`,
`kicadstamp.geometry.pad_area`, `kicadstamp.geometry.keepout`, `kicadstamp.geometry.thermal_grid`.

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
- `--timeout` – IPC timeout (ms, default `DEFAULT_TIMEOUT_MS` — 5000). This probe makes a
  single cheap pad read, so unlike the five heavy batch probes next to it, it takes the shipped
  default rather than a long budget of its own.
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

### `transform_template.py`

**Purpose:**
Transforms a spoke template file: first shifts the origin to a specified element (a via or a component
of the template), then rotates and/or mirrors the whole template around that new origin.

**Usage:**
```bash
python -m kicadstamp.diagnostics.transform_template \
    -i template.sexp -o transformed.sexp --rotate 90 --set-origin-by-component-role R1
```

**Parameters:**
- `-i/--input` – the template file to read (s-expr).
- `-o/--output` – the file to write.
- `--rotate` – rotate counter-clockwise by N degrees (default 0).
- `--mirror-x` / `--mirror-y` – mirror along the X / Y axis.
- `--set-origin-by-via-index N` / `--set-origin-by-via-net NET` – shift the origin to a via.
- `--set-origin-by-component-index N` / `--set-origin-by-component-role ROLE` – shift the origin to a
  component.
- `--origin-x` / `--origin-y` – explicit origin offsets in mm (used when no origin element is given).

**Output:**
The transformed template written to `-o` as an s-expr file with a `templates:` section.

**Dependencies:**
`kicadstamp.config.sexp_format` (`sexp_to_dict`/`dict_to_sexp`); reads and writes local files only, no IPC.

**See also:**
Reading a component's fields (including `Role`) from the live board is what `get_selected_component.py`
does — it prints the refdes, value, footprint, position, angle, pads, nets and the `Role` field of the
current selection.

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

### `audit_cell_net_templates.py`

**Purpose:**
Audits Cell templates for `net_template` values that are HARDCODED absolute net
paths (`/Sheet/Group/Signal`) on Cells that are reused by Entities on more than
one distinct sheet. A Cell's `net_template` lives at the Cell level (never
sheet-parameterized), so every instance of a reused Cell shares the literal —
and when the Cell is placed on a second sheet (e.g. materialized by a
`tree_instances:` declaration), role resolution for that instance can only ever
find — and move — the LOCKED sheet's real component, silently and without an
ambiguity error. This is exactly the pattern behind a live bug (2026-09-07):
a `tree_instances`-generated `ch1_dac_buf` redrew Channel 0's real
bypass/bulk caps instead of Channel 1's. Read-only: loads each config through
`load_config` and prints a report — it never fixes anything (replacing the
literal with `net_from_role`/`net_from_role_pad`, or re-tagging the Cluster on
the schematic, is a profile-data decision for the owner).

**Usage:**
```bash
python -m kicadstamp.diagnostics.audit_cell_net_templates <config.sexp> [<another config.sexp> ...]
```

**Output:**
A human-readable report grouped by Cell: for each role whose `net_template`
starts with `/`, the full literal and the sheet it is locked to. When no
findings — a single `No hardcoded sheet-specific net_template found.` line.
Exit code is always `0` (diagnostic, not a CI gate).

**Dependencies:**
`kicadstamp.config.load_config` (offline — no live KiCad needed).

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
