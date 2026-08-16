# KiCadStamp Commands (CLI)

This document provides a complete reference for `kicadstamp_cli.py` commands and flags, config generators from `tools/`, and practical examples for typical scenarios. Verified against the code in the `main` branch (the project does not maintain version numbers/tags; refer to the commit date).

---

## Basic Syntax

```bash
python kicadstamp_cli.py <command> [options]
```

If no command is given, `apply` is assumed.

---

## `apply` – apply placement

Loads the configuration, connects to KiCad, performs validation, planning, and **three‑phase execution** (moves → vias → tracks).

**Move ordering (dependency chain).** Within the moves phase, `rules`/`clone_placements` are not all
planned from one snapshot any more — each item's anchor (`anchor_ref`/`anchor_role`) is resolved against
the board, and if that anchor is a ref that ANOTHER item in the same run is about to place, the producer
is planned, moved, and committed first; only then is the dependent item planned against the real,
post‑move board. Items with no such dependency (anchored on something nobody in this run moves, or on an
absolute coordinate) go first, in their YAML order. Found via a real bug (2026‑07‑27): a clone anchored on
a role inside another clone's own template landed at that role's OLD position, not where the same run was
about to move it to. A config where two or more items anchor on each other's output has no valid order and
is a fatal `ValidationError` before anything is touched on the board (see
`kicadstamp/placement/dependency_order.py`). `apply --dry-run` prints the resolved order but, since it
never actually moves anything, still plans every item from one unchanged snapshot — positions for items
later in the chain may come out different in a real (non‑dry‑run) apply; the dry‑run output says so.

### Syntax

```bash
python kicadstamp_cli.py apply <config.yaml> [options]
```

### Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Only print the plan (moves, vias, tracks), do not apply changes. |
| `--timeout-ms` | IPC timeout in milliseconds (default: `20000`). |
| `--batch-size` | Number of objects per transaction (default: `10`). |
| `--verbose` | Enable verbose output (DEBUG). |
| `--log-file` | Save logs to the specified file. |
| `--no-collision-check` | Disable collision checking (if false positives occur). |
| `--collision-margin` | Extra clearance for collision checking in mm (default: `0.2`). |
| `--only NAME` | Process only the `rules`/`clone_placements`/`thermal_via_arrays` with this identity (flag can be repeated, and/or comma-separated: `--only a,b --only c`). The main way to narrow a run (replaces the old `--clone-placement`, which is gone – it never isolated `rules`/`thermal_via_arrays`, only `clone_placements`, hence the confusion). The identity is the entry's `name:` if set, else its `net` for a rule (see below); mandatory for `clone_placements`/`thermal_via_arrays`. Everything that doesn't match is excluded from this run entirely, not even touched by validation/logging – for checking one section of the board in isolation, without noise from the rest. An unknown name is fatal, with a `difflib`-based suggestion. |
| `--cluster PATH` | Process only spokes / `clone_placements` / `thermal_via_arrays` entries whose `Cluster` (`anchor_cluster` / a spoke's `cluster`) matches this path or a prefix of it, segment-wise (`Channel_0` also matches `Channel_0/DAC_OA`). Repeatable and/or comma-separated. A second, independent selection axis (physical instance, not name/identity) – for a `rules:` entry it narrows `spokes:` inside the rule (the rule survives if at least one spoke matches, is dropped entirely otherwise), for `clone_placements`/`thermal_via_arrays` it's a whole-entry match. Combines with `--only` via AND only (no OR mode) – run `apply` twice if you need "this OR that"; the registry makes repeat runs safe (already-placed items aren't duplicated). Matches nothing → fatal, same as `--only`. |

**Terminology used below and in the code:** `rules:` (`Rule`, part of `ManualSpoke`) are called **spokes**;
`thermal_via_arrays:` are the **thermal vias**; `clone_placements:` (`ClonePlacement`) are the **clones**. All
three are independent, uniformly `--only`/`--cluster`/`enabled`-filterable sections of one config.

**`log_file:` in the config itself** – an optional root‑level YAML field (like
`registry_path`), resolved relative to the config file itself. If set, you don't need to pass
`--log-file` by hand every time for the same board profile. The CLI flag `--log-file`, if given, takes
priority over this field:
```yaml
log_file: ../logs/placer.log
```

**`include:` – splitting a profile into subsystem files.** General‑purpose: merges `rules:`/
`clone_placements:`/`thermal_via_arrays:` (concatenated) and `cells:`/`points:`/`extract_profiles:`/
`clone_profiles:` (merged by key) from other files into the current one, recursively, and works for
**both** `apply` (`load_config`) and `extract`/`clone-extract` (`load_profile`, since
`extract_profiles`/`clone_profiles` are read through a separate code path) — so one subsystem file can
carry the extract profile and the clone_placement for that subsystem together, or an external `Cell`
file (wrap its content in a `cells:` key — the old separate `cells_file:`/`cell_files:` mechanism was
folded into `include:` on 2026-08-02, one way to split ANY section across files instead of two):
```yaml
include:
  - subsystems/ldo.yaml
  - path: subsystems/dac_channels.yaml
    enabled: false   # whole file skipped — not even opened — while iterating on something else
```
Each entry is either a path string, or `{path, enabled}` (`enabled` defaults to `true`). A duplicate
`cells`/`extract_profiles`/`clone_profiles` key defined in two different files is fatal – these are meant
to be separate files, so a repeated name is far more likely a mistake. A file included twice (directly, or
reached from two different branches) is fatal too, whether or not it's a true cycle. Paths are resolved
relative to the file that references them, not the top‑level config or the current working directory.

**About the current production config:** the master config for the `3CH-AWG-TIA` board is `profiles/3ch-awg-tia.yaml` (merged `rules:`, `clone_placements:`, `thermal_via_arrays:`, with a reference to `profiles/templates/3ch-awg-tia.yaml` via `cells_file`). The file `profiles/generated/10CL006YE144C8G.yaml` written by `tools/generate_10cl006.py` is a self‑contained archival version (can be run separately, but is no longer used in `apply` for this board).

**`name:` is mandatory on every `thermal_via_arrays:` entry and every
`clone_placements:` entry, but OPTIONAL on `rules:` entries** (a rule falls back to its `net` – this was
briefly made mandatory for rules too, then deliberately reverted the same day: a rule's `net` is already a
perfectly good, usually-unique identity, and forcing a redundant `name:` on every single rule added no
value). Used by `--only`. A `clone_placement` without `name:` used to silently become the literal string
`'?'` (a real hole, not a feature), and a `thermal_via_arrays` entry without one used to silently fall back
to `thermal_<pad>` – both gone, missing `name:` is fatal at config-load time for these two (and every
`thermal_via_arrays` entry's `name:` must also be unique across the whole list). For `rules:`, the
loader instead fatals if **two rules resolve to the same effective identity** (same `net`, no distinguishing
`name:`) – add a `name:` to disambiguate, don't rely on one being picked silently:
```yaml
rules:
- net: +3V3_VCCIO
  # name: optional – defaults to net "+3V3_VCCIO"; add one only if you want a
  # more readable --only label, or two rules share the same net
  anchor_role: FPGA
  enabled: true          # optional, default true – see below
  spokes: [...]

thermal_via_arrays:
- name: fpga_thermal   # mandatory, and unique across the list
  enabled: true
  ...

clone_placements:
- name: p5v_pi_filter   # mandatory
  template: 5v_pi_filter
  ...
```

**`enabled: bool` (default `true`) on every `rules:`/`clone_placements:`/`thermal_via_arrays:` entry** –
whole-entry on/off switch. `enabled: false` always wins, applied **before** `--only`/`--cluster` are even
looked at – it means "does not exist on the board right now", not "excluded from this particular run", so
it cannot be un-done by naming the entry explicitly on the command line. Use it to permanently park a
section of the config without deleting it; use `--only`/`--cluster` for a one-off narrowed run of things
that stay otherwise enabled.

### Examples

#### Standard run (place components, vias, and tracks)

```bash
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml
```

#### Run with verbose logging to a file

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --verbose --log-file logs/placer.log
```

#### Preview (dry‑run) – does not modify the board

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --dry-run
```

#### Process only one clone (e.g., for debugging)

```bash
python kicadstamp_cli.py apply templates\pi_filter_vccio.yaml --only pi_filter_vccio
```

#### Isolated run of a single board section (--only)

```bash
# Only one clone_placement, no FPGA spokes or thermal vias in the log
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only p5v_pi_filter --dry-run

# Multiple names/identities at once (a rule via its net + a named thermal_via_arrays entry), repeat flag or comma
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only +3V3_VCCIO,fpga_thermal
```

#### Narrow by physical instance instead of by name (--cluster)

```bash
# Only spokes/clones/thermal-vias whose Cluster matches this channel (segment-prefix match)
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --cluster Channel_0 --dry-run

# Combine with --only – AND, not OR: this clone_placement, AND only within this channel
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --only p5v_pi_filter --cluster Channel_0
```

#### Disable collision checking

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --no-collision-check
```

#### Increase timeout for slow KiCad sessions

```bash
python kicadstamp_cli.py apply 10CL006YE144C8G.yaml --timeout-ms 30000
```

---

## `undo` – undo the last operation

Finds the most recent JSON log in the `logs/` folder and restores the board (moves components back to their original positions and layers, removes created vias **and tracks**).

### Syntax

```bash
python kicadstamp_cli.py undo [--verbose] [--log-file]
```

### Example

```bash
python kicadstamp_cli.py undo --verbose
```

---

## `extract` – extract a template from the current selection

Creates a spoke template from the current selection in the PCB editor. Each selected component must have a unique `Role` field. Supports extraction of **tracks** and **vias** together with components. When the GUI's **"Keep only one Cluster"** filter is active (or the caller narrows `footprints` before invoking `extract_template_from_selection`), only components of the kept Cluster are taken — and this now propagates to connectivity as well: a track/via is included only if its connected component (via coincident endpoints, track-to-track joints, or touching a via) reaches a pad of a KEPT footprint. A track/via whose component only touches excluded-cluster material is dropped as a whole with a warning, instead of surviving via local track-to-track "mutual validation" (2026-08-16 fix — see `kicadstamp/template_selection.py`).

### Syntax

```bash
python kicadstamp_cli.py extract --name <template_name> --output <file> [--timeout-ms] [--verbose] [--log-file] [--param KEY=VALUE] [--net-template LITERAL=PATTERN] [--origin-by-via-net NET] [--origin-by-component-role ROLE] [--origin-by-component-pad PAD] [--profiles FILE] [--profile NAME]
```

### Options

| Flag | Description |
|------|-------------|
| `--name` | Name of the template (key in the `templates` section). Optional in direct-flags mode (not `--profile`): if omitted, prompted for interactively. |
| `--output` | Output file path. The extension determines the format: `.json` → JSON (flat dictionary), otherwise YAML. |
| `--timeout-ms` | IPC timeout in milliseconds (default: `20000`). |
| `--verbose` | Enable verbose output. |
| `--log-file` | Save logs to a file. |
| `--param KEY=VALUE` | Sets a parameter for verifying `--net-template` (e.g., `channel=1`). Not written to the template, only used for round‑trip validation. Can be repeated. |
| `--net-template LITERAL=PATTERN` | Replaces a real net name with a pattern containing placeholders (e.g., `DAC1_DB1=DAC{channel}_DB1`). Can be repeated. |
| `--origin-by-via-net NET` | Sets the template origin to the position of the via with the specified net (instead of the bbox lower‑left corner). Fatal if no such via exists or if there is more than one. Mutually exclusive with `--origin-by-component-role` (you can specify only one origin method). |
| `--origin-by-component-role ROLE` | Sets the origin to the position of the component with the specified role. Mutually exclusive with `--origin-by-via-net`. |
| `--origin-by-component-pad PAD` | Refines `--origin-by-component-role`: origin is the position of the specific pad of that component, not its centre. Without `--origin-by-component-role` it is fatal (you can only specify a pad for an already specified role). |
| `--profiles FILE` | YAML file with named profiles for `extract`. |
| `--profile NAME` | Use a profile from the `--profiles` file instead of explicit flags (cannot be combined with `--name`, `--output`, `--param`, `--net-template`, `--origin-by-*` – either everything from the profile or all explicit flags). |

**Important:** Before running, select the desired components, vias, and tracks in the PCB editor. Roles must be unique. The output (YAML or JSON) is written wrapped under a `cells:` key, ready to be listed directly under `include:` in the main configuration.

**Uncertain `net_template`:** when a component's pads match more than one net from `--net-template`/`net_template` (e.g. an inductor/ferrite bead bridging two rails), `net_template` cannot be set automatically — a warning is logged, and (YAML output only, not JSON) a commented placeholder line is written right after that component's block, e.g. `# net_template: could not determine automatically — ...`, so the gap is visible in the file itself, not only in the log. Resolve it either by editing the line manually or via `--net-template-role ROLE=<net>` on the next run.

**Inside a profile** (`extract_profiles:` in the `--profiles` file): `output:` can be set once at the file's
root – a shared default for every profile, a specific profile only needs to set its own if it writes
somewhere else. `name:` is optional – it defaults to the profile's own key, set it explicitly only when the
template name must differ from the profile name. The parameters for `--net-template` verification use the
key `params:` (not `param:` – deliberately the same key name as `clone_placements` uses, to remove that
mismatch as a copy‑paste trap).

### Examples

#### Extract a template to JSON with net parametrisation and origin by via

```bash
python kicadstamp_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

#### Extract using a profile

In `extract_profiles.yaml`:
```yaml
# output: shared by every profile below – set it once here if they all write
# to the same file; a profile that needs a different one just sets its own
# output: directly, overriding this value.
output: templates/my_filter.json

extract_profiles:
  my_filter:
    # name: not needed – defaults to the profile's own key ("my_filter").
    # Set it explicitly only if the template name must differ from the
    # profile name (e.g. several profiles feeding one shared template).
    params:
      PWR_IN: '+3V3'
      PWR_OUT: '+3V3_VCCIO'
    net_template:
      '+3V3_VCCIO': '{PWR_OUT}'
      '+3V3': '{PWR_IN}'
    origin_by_via_net: '+3V3_VCCIO'
```

Run:
```bash
python kicadstamp_cli.py extract --profiles extract_profiles.yaml --profile my_filter --verbose
```

#### Extract a template to YAML (no parametrisation)

```bash
python kicadstamp_cli.py extract --name my_filter --output my_filter.yaml --verbose
```

#### Add a template to an existing config (YAML)

```bash
python kicadstamp_cli.py extract --name my_filter --output 10CL006YE144C8G.yaml --verbose
```

Note: if a template with the same name already exists, it will be overwritten.

---

## `clone-extract` – snapshot a channel (file‑based cloner)

Analyzes a hierarchical project (without IPC) and extracts all components, tracks, and vias belonging to the specified channel, saving the snapshot as YAML. Useful for studying the channel structure before writing a ClonePlacement configuration.

### Syntax

```bash
python kicadstamp_cli.py clone-extract --net <file.net> --pcb <file.kicad_pcb> --channel <channel_name> --output <file.yaml> [--profiles FILE] [--profile NAME] [--verbose]
```

### Options

| Flag | Description |
|------|-------------|
| `--net` | Path to the `.net` file (netlist). |
| `--pcb` | Path to the `.kicad_pcb` file. |
| `--channel` | Channel name (e.g., `Channel_0`). |
| `--output` | Output YAML file. |
| `--profiles FILE` | YAML file with named profiles for `clone-extract`. |
| `--profile NAME` | Use a profile from the `--profiles` file instead of explicit flags. |
| `--verbose` | Enable verbose output. |

### Example

```bash
python kicadstamp_cli.py clone-extract --net my_project.net --pcb my_project.kicad_pcb --channel Channel_0 --output snapshot.yaml --verbose
```

Using a profile (`clone_profiles.yaml`):
```yaml
clone_profiles:
  channel0:
    net: my_project.net
    pcb: my_project.kicad_pcb
    channel: Channel_0
    output: snapshot.yaml
```

Run:
```bash
python kicadstamp_cli.py clone-extract --profiles clone_profiles.yaml --profile channel0 --verbose
```

The resulting YAML file contains a complete overview of the channel, which can be used to create a template and ClonePlacement entries.

---

## Utility scripts (`tools/`)

### `transform_template.py` – template transformation utility (optional)

A separate script for post‑processing existing templates (YAML or JSON). It allows rotating, mirroring, and shifting the origin without re‑extracting from the board.

#### Syntax

```bash
python tools/transform_template.py -i <input_file> -o <output_file> [options]
```

#### Options

| Flag | Description |
|------|-------------|
| `-i, --input` | Input YAML/JSON template file. |
| `-o, --output` | Output file (format determined by extension). |
| `--rotate DEG` | Rotate counter‑clockwise by angle (degrees). |
| `--mirror-x` | Mirror along X axis (flips `across` sign). |
| `--mirror-y` | Mirror along Y axis (flips `along` sign). |
| `--set-origin-by-via-index N` | Shift origin to the via at index N (0‑based). |
| `--set-origin-by-via-net NET` | Shift origin to the via with the given net. |
| `--set-origin-by-component-index N` | Shift origin to the component at index N. |
| `--set-origin-by-component-role ROLE` | Shift origin to the component with the given role. |
| `--origin-x X --origin-y Y` | Explicit origin offset in mm. |

**Order of application:** first origin shift (if specified), then rotation and mirroring. This ensures that the target element ends up at (0,0) after all transformations.

**Known limitation:** the script transforms only `vias` and `components`. The `tracks` section (if present – e.g., in `cap_pair_standard` / `cap_pair_standard_clone` in `profiles/templates/3ch-awg-tia.yaml`) **is not read or propagated** to the output – when transforming a template with tracks, they are silently lost in the output file. For templates containing tracks, do not use this script, or manually add the `tracks` section to the output file.

#### Examples

#### Rotate 180° and shift origin to the via with net "GND"

```bash
python tools/transform_template.py -i template.yaml -o template_rotated.yaml --rotate 180 --set-origin-by-via-net "GND"
```

#### Mirror along X and shift origin to the component with role "FB"

```bash
python tools/transform_template.py -i template.yaml -o template_mirrored.yaml --mirror-x --set-origin-by-component-role FB
```

#### Explicit origin shift

```bash
python tools/transform_template.py -i template.yaml -o template_shifted.yaml --origin-x 1.5 --origin-y -2.0
```

### `generate_10cl006.py` – config generator for 10CL006YE144C8G

A ready‑to‑run script (not an example, actually used in the project). A single source of data – the `BANKS` table (pad/shift/rotation per power rail of the FPGA) and `CLUSTER_MAP` (net → `Cluster` name) inside the file – from which three derived artefacts are generated.

#### Syntax

```bash
python tools/generate_10cl006.py
```

No arguments – output paths are hard‑coded inside the script (see `main()`); the `BANKS`/`CLUSTER_MAP` tables and anchor toggles (`USE_ANCHOR_ROLE`, `THERMAL_USE_ANCHOR_ROLE`) are edited directly in the source.

#### What it generates

| File | Purpose |
|------|---------|
| `profiles/generated/10CL006YE144C8G.yaml` | Rules‑based config (`ManualSpoke`/`Rule`) – self‑contained and apply‑ready, uses the old inline (approximate) `templates:`. |
| `profiles/generated/10CL006YE144C8G.clone_placements.yaml` | Equivalent geometry as `clone_placements:` (`ClonePlacement`). **Since 2026-07-26, `Rule`/`ManualSpoke` can also clone tracks** (see `spoke_layout.py`/`TemplateTrack`) – keeping this path around is now worthwhile for anchor resolution via `anchor_pad`/`anchor_cluster` and `{power_net}` placeholders through `params`, which `Rule` does not resolve, not for tracks. Requires template `cap_pair_standard_clone` from `profiles/templates/3ch-awg-tia.yaml` (via `cells_file`). Not automatically included – copy the block manually into `profiles/3ch-awg-tia.yaml` after verifying with `--dry-run`. |
| `profiles/generated/10CL006YE144C8G.cluster_table.md` | Table `net \| pad \| cluster` (`FPGA_PWR_BANK/<pad>`) – a cheat sheet for manually setting the `Cluster` field in Eeschema (Bulk Edit) for those pads for which proximity‑based resolution is not sufficient. |

`anchor_cluster` in `clone_placements` is always set — since 2026-08-14 it narrows ONLY the anchor, while the narrowing of roles INSIDE the cell reads the placement's OWN Cluster (`name:`, see `docs/config.md`); in the working profiles `name:` equals `anchor_cluster`, so the two stay in sync. Even before `Cluster` is assigned in the schematic the resolver simply skips the corresponding narrowing step and falls back to the next one, so the generated file can be run with `apply --dry-run --verbose` before marking `Cluster` in Eeschema; the log will show which pads need explicit tagging.

#### Example

```bash
python tools/generate_10cl006.py
# Generated: profiles/generated/10CL006YE144C8G.yaml
# Generated: profiles/generated/10CL006YE144C8G.clone_placements.yaml
# Generated: profiles/generated/10CL006YE144C8G.cluster_table.md
# Total spokes: 24
```

### `generate_config.py` – template stub (NOT a ready‑to‑run script)

Unlike `generate_10cl006.py`, this is a **template stub** for writing a similar generator for a new chip, not a working tool. The `TEMPLATE` in it is filled with ellipsis `[...]` instead of real geometry – running it "as is" fails with a YAML serialisation error:

```bash
python tools/generate_config.py
# ValueError: dictionary update sequence element #0 has length 1; 2 is required
```

Use it as a starting point: copy it, replace `TEMPLATE` with a real template (e.g., obtained via `extract`), fill the `FILTERS` list with your own `CloneParams` (`anchor_ref`/`anchor_pad`/`origin_x`/`origin_y`/`rotation_deg`/`params`/`nets`) – and only then run it.

### `update_i18n.py` – rebuild the gettext translation catalogs

Extracts every string wrapped in `_()` (`kicadstamp/`, `kicadstamp_cli.py`, `tools/`, `tests/`, ...) into
`messages.pot`, merges it into the existing `locales/en/LC_MESSAGES/kicadstamp.po` and
`locales/ru/LC_MESSAGES/kicadstamp.po` (pybabel keeps already‑translated strings, adds new ones empty or
marks them `#, fuzzy` if it found a similar old one), then compiles both catalogs to `.mo`. The temporary
`messages.pot` is removed at the end. Frozen archives (`files/`, `old/`, `arch/`, `test_sample/`) are
excluded from the scan. Requires `pip install babel` (already in `requirements.txt`).

#### Syntax

```bash
python tools/update_i18n.py
```

No arguments or flags – paths and languages (`en`, `ru`) are hard‑coded in the script.

#### When to run it

- After adding or changing ANY text wrapped in `_(...)` (a new `logger.info`, a new fatal error, a new
  argparse `help=`, etc.) – otherwise `locales/*/kicadstamp.mo` goes stale and some messages keep showing in
  English (fallback) even under `LANG=ru`.
- The catalogs are committed to git (`.po` and the compiled `.mo`) – run this BEFORE committing; there is no
  CI/build hook that does it for you.

#### After running it

- New/changed strings in `locales/ru/LC_MESSAGES/kicadstamp.po` show up with an empty `msgstr ""` (needs
  translation) or marked `#, fuzzy` (pybabel guessed a similar old string by itself – **don't trust it
  blindly**, review it and remove the `fuzzy` marker, otherwise gettext treats the entry as a draft and shows
  the `msgid` (English) instead).
- Find untranslated strings: `grep -B2 'msgstr ""' locales/ru/LC_MESSAGES/kicadstamp.po` (the first match is
  the catalog header with an empty `msgid ""` too – that's expected, not a bug).
- Check fuzzy entries: `grep -c '#, fuzzy' locales/ru/LC_MESSAGES/kicadstamp.po`.

#### Example

```bash
python tools/update_i18n.py
# ... extracting messages from ...
# updating catalog locales\en\LC_MESSAGES\kicadstamp.po based on messages.pot
# updating catalog locales\ru\LC_MESSAGES\kicadstamp.po based on messages.pot
# compiling catalog locales\en\LC_MESSAGES\kicadstamp.po to locales\en\LC_MESSAGES\kicadstamp.mo
# compiling catalog locales\ru\LC_MESSAGES\kicadstamp.po to locales\ru\LC_MESSAGES\kicadstamp.mo
# ✅ Translations updated.
```

---

## Diagnostic commands (debugging and testing)

These commands execute diagnostic scripts located in `kicadstamp/diagnostics/`. They help test IPC, geometry, field reading, flipping, etc.

### Check reading of the `Role` custom field

```bash
python -m kicadstamp.diagnostics.test_custom_fields C5 --field Role --verbose
```

### Test moving a single component

```bash
# Shift by +1 mm along X
python -m kicadstamp.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Revert the shift
python -m kicadstamp.diagnostics.test_move_one_cap C5 --revert
```

### Test component flip

```bash
python -m kicadstamp.diagnostics.test_flip_one_cap C6
```

### Test creating a single via

```bash
# Create a via next to C5
python -m kicadstamp.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Remove the last created via
python -m kicadstamp.diagnostics.test_create_one_via --remove
```

### Test for KiCad crash on first write (issue #24966)

Full reference (parameters, hypotheses, output, dependencies) moved to a standalone document:
[docs/diagnose_first_write_crash.md](diagnose_first_write_crash.md).

```bash
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # read-only, safe
python -m kicadstamp.diagnostics.diagnose_first_write_crash             # full test, may crash KiCad
```

### Display information about selected components

```bash
python -m kicadstamp.diagnostics.get_selected_component
```

### Get a pad's bounding box

```bash
python -m kicadstamp.diagnostics.get_pad_bbox --ref IC1 --pad 17
```

### Analyze keepout and via positions

```bash
python -m kicadstamp.diagnostics.diagnostic_keepout 10CL006YE144C8G.yaml
```

---

## Usage recommendations

1. **Before the first run** – use `extract` on a correctly placed instance to obtain a template. Use JSON format if you prefer it over YAML for the external file.
2. **Check your configuration** with `--dry-run` to verify positions, vias, and tracks.
3. **For debugging** – enable `--verbose` and log to a file.
4. **When handling multiple clones in selection mode** – use `--only <name>` to process them one at a time.
5. **If KiCad crashes** on the first run – close the schematic editor or make an interactive edit in PCB before launching (workaround for issue #24966).
6. **For hierarchical projects** – use `clone-extract` before writing ClonePlacement to get exact net names and twin refdes.
7. **Store templates separately** – list the external file under `include:` (wrapped in a `cells:` key) to keep geometry out of the main file.
8. **Transform templates** with `transform_template.py` instead of manual coordinate recalculation.

---

## Built‑in help

```bash
python kicadstamp_cli.py --help
python kicadstamp_cli.py apply --help
python kicadstamp_cli.py extract --help
python kicadstamp_cli.py undo --help
python kicadstamp_cli.py clone-extract --help
```

---

## Common errors and solutions

| Error | Possible cause | Solution |
|-------|----------------|----------|
| `BoardNotFoundError` | KiCad is not running or no board is open. | Open the project in KiCad and call `adapter.refresh_board()`. |
| `ComponentNotFoundError` | The specified `anchor_ref` is not found on the board. | Check the refdes in your config. |
| `ValidationError: not enough components for roles` | Not enough components with the `Role` field for the given net. | Add the `Role` field to the required components in the schematic and run Update PCB. |
| `ValidationError: resolved via net not found` | Typo in `params` or `net_overrides`. | Verify net names in the config against the schematic. |
| `ConnectionError` during write | KiCad crashed (known issue #24966) or is stuck. | Close the schematic editor or make an interactive edit in PCB, then restart. |
| `KiCad crash on first launch` | Schematic editor open and no interactive edits made. | Workaround: close the schematic or move a component in PCB and save. |
| `Cannot find via/track` during undo | The object was manually deleted. | Undo skips missing objects and continues. |

---

## Quick command examples

### Place decoupling capacitors for an FPGA (master config for the board)

```bash
python kicadstamp_cli.py apply profiles/3ch-awg-tia.yaml --verbose --log-file logs/placer.log
```

### Regenerate generated configs/cluster table for 10CL006

```bash
python tools/generate_10cl006.py
```

Then run `apply profiles/3ch-awg-tia.yaml --dry-run --verbose` to verify that the new geometry resolves as expected (see the `generate_10cl006.py` section above).

### Undo placement

```bash
python kicadstamp_cli.py undo --verbose
```

### Extract a template to JSON (recommended format)

```bash
python kicadstamp_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

### Apply a clone using an external template file

```bash
python kicadstamp_cli.py apply config_with_include.yaml --only fpga_filter_1v2_vccint
```

### Transform a template

```bash
python tools/transform_template.py -i templates/pi_filter_4.json -o templates/pi_filter_4_rotated.json --rotate 180 --set-origin-by-via-net '+3V3_VCCIO'
```

### Test KiCad for crashes

```bash
# Read‑only
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8

# Full test (reads + write)
python -m kicadstamp.diagnostics.diagnose_first_write_crash

# With a 30‑second pause before write
python -m kicadstamp.diagnostics.diagnose_first_write_crash --delay 30
```

---

## License

All examples are distributed under the MIT license, the same as the main project.
