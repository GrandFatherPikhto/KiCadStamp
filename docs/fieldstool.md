# fieldstool — bulk Role/Cluster set/rename in `.kicad_sch`

"fieldstool" is the name for a small family of `.kicad_sch`-text-editing tools sharing one
underlying library — not a Python package anymore (folded into `kicadstamp`/`gui` 2026-08-02, see
[Where the code actually lives](#where-the-code-actually-lives) below):

- **`fieldstool_cli.py`** — offline CLI (`set`/`rename` subcommands).
- **The fieldstool tab** — `gui.fieldstool_window.MainWindow`, embedded as the first tab of the main
  [PyQt6 GUI](./gui.md) (`gui/docks/fieldstool_dock.py`, see
  [gui.md's fieldstool tab section](./gui.md#fieldstool-tab)) — the only way its GUI runs (a
  standalone `fieldstool_gui.py` entry point existed 2026-08-01 through 2026-08-02, retired as pure
  duplication of the embedded tab).

Both edit `.kicad_sch` **directly, as text** — not through KiCad's live IPC — because `Role`/
`Cluster` custom fields originate in the schematic symbol, and a PCB-only IPC write gets silently
reverted by KiCad's own "Update PCB from Schematic" (the main GUI used to have exactly that as a
dock, `BulkFieldEditorDock` — retired in favor of fieldstool taking its place).

## Where the code actually lives

Originally its own `fieldstool`/`fieldstool.gui` packages, dependency-free from `kicadstamp`/`gui`.
Split apart 2026-08-02 along the same fault line every module already had, once `fieldstool.gui`
turned out to be inseparable from `gui/` in practice (it always requires an injected
`gui.connection.BoardConnection`, embedded exclusively via `gui/docks/fieldstool_dock.py` — there
was no real independence left to preserve):

- **Schematic-editing library → `kicadstamp/`, flat, `schematic_` prefix** (not a subpackage — this
  project keeps `kicadstamp/` itself flat for modules at this scope): `schematic_blocks.py` (byte-
  offset span-finding in `.kicad_sch` text), `schematic_discovery.py`
  (`walk_schematic_hierarchy()`), `schematic_safety.py` (non-ASCII check, `list_kicad_pids()`),
  `schematic_editing.py` (`apply_edits()`, the `.bak`/self-verify write pipeline),
  `schematic_config.py` (shared `load_fields_config()` — the YAML config shape both
  `set` and `rename` read), `schematic_set_fields.py`/`schematic_rename_fields.py` (the
  `set`/`rename` planning logic).
  `FieldsToolError` joined `kicadstamp/exceptions.py` directly (a plain `Exception` subclass, NOT a
  `PlacerError` subclass — a different risk domain, catching one must never accidentally swallow
  the other). **`fieldstool_cli.py` uses these directly** and stays its own separate CLI/interface
  from `kicadstamp_cli.py` — a different domain (bulk schematic text edits vs. board placement),
  not worth folding into one argument parser.
- **GUI-only pieces → `gui/`, flat**: `gui/schema_model.py` (`load_schematic_components()` — flattens
  the schematic into one row per refdes for the Components tree; `fieldstool_cli.py` never needs
  this per-ref view), `gui/fieldstool_window.py` (the `MainWindow` embedded as the fieldstool tab —
  see [The fieldstool tab](#the-fieldstool-tab) below), `gui/docks/pending.py`
  (`compute_pending_edits()` + `PendingChangesDock` — a `QDockWidget`, shared with the rest of
  `gui/docks/`, tabbed with Log at the main window's bottom). Its own settings file
  (`gui/fieldstool_gui_state.json`) reuses `gui/settings.py`'s `Settings` class with a different
  path, rather than a second near-identical settings module.

None of this changed *behavior* — pure move-and-rename, same logic, same tests (relocated
alongside).

## Why this write pipeline stays separate from `kicadstamp`/`gui`'s

This is about the write pipeline being fundamentally different, not about package/process
boundaries (there are none left):

- **A different, riskier write surface.** `kicadstamp`/`gui` only ever write through KiCad's live,
  transactional IPC (`BeginCommit`/`UpdateItems`/`EndCommit` — undoable in KiCad itself).
  `schematic_editing.py` edits `.kicad_sch` as a file, directly — the same hazard class as KiCad bug
  #24966 (touching a file KiCad may have open/cached), but worse, since it doesn't go through
  KiCad's live IPC at all.
- **KiCad must be closed to apply, and reopened to see the result.** A running KiCad process does
  not hot-reload an externally-modified schematic file. Checked exhaustively (kipy 0.7.1): there is
  **no application-level quit/close/shutdown call, and no "unsaved changes" check**, anywhere in
  `kipy.KiCad`, `kipy.Board`, `kipy.Schematic`, or any of its proto command definitions. So this
  can only ever be an **instruction** to the user ("close KiCad, then Apply") — never automated.
- **Point-edit, not parse→dump.** Edits are byte-offset text splices (regex + paren-balance
  matching for block boundaries), never a full `sexpdata` parse→`dumps()` round trip — there is no
  precedent that `sexpdata.dumps()` reproduces KiCad's own formatting byte-for-byte. `sexpdata` is
  only used to *self-verify* a write after the fact (see [Safety guards](#safety-guards)).

## `fieldstool_cli.py`

```bash
python fieldstool_cli.py set roles.yaml [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
python fieldstool_cli.py rename renames.yaml [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
```

Both subcommands are dry-run by default (print what would change, touch nothing); `--write` is
required to actually edit files.

### `set` — refdes → `{field: value}`

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch   # the PROJECT's top sheet, not a folder
fields:
  C51:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/17
  C52:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/26
```

`root_sheet:` is walked recursively (`(sheet (property "Sheetfile" "...") ...)` references,
diamond/cycle-safe) to find every reachable `.kicad_sch` — not a flat directory glob, so a stray
unrelated `.kicad_sch` sitting in the same folder is never picked up by mistake.

Two ways one refdes can appear in a file, both handled:
- **Multi-unit symbol** (e.g. a dual op-amp) — one refdes spans several separate `(symbol ...)`
  blocks (one per unit); all of them get edited.
- **Multi-instance sheet** (e.g. `Channel_1`/`2`/`3` all instancing one `channel_tpl.kicad_sch`) —
  several refdes share ONE `(symbol ...)` block, so the field is shared across all of them. If the
  config asks for **different** values for two such refdes, the format can't express that — fatal,
  not a silent pick of one of the two.

### `rename` — `field → {old_value: new_value}`, no refdes needed

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch
renames:
  Role:
    OLD_ROLE_A: NEW_ROLE_A
  Cluster:
    Old_Cluster_Name: New_Cluster_Name
```

Changes a value everywhere it currently occurs, across the whole schematic tree — you don't
enumerate which refdes are affected. Simpler than `set` in one respect: it can never hit the
multi-instance conflict above, since it always writes the *same* new value to every match. An
`old_value` that matched nothing anywhere is reported as a **warning**, not a fatal error — it's
just as likely a harmless re-run (renaming is idempotent) as a typo.

`rename --also-profile <root.yaml>` additionally applies the SAME `renames:` map to the profile
config YAML files reachable through that profile's `include:` graph — the answer to "rename a
Role/Cluster on the schematic and have it propagate into the already-placed `profiles/*.yaml`
tree without a second, duplicating rename file". Only semantically-correct fields are edited:
`Role` → `role:`/`anchor_role:`/`net_from_role:`/`net_template_same_as_role:` and the KEYS of the
`refs:`/`nets:` dicts; `Cluster` → `cluster:`/`anchor_cluster:` and `name:` of `clone_placements:`
(that `name` IS the Cluster tag written onto the board's components). The edit is a byte-offset
text splice (comments/formatting preserved, `.bak` + `yaml.safe_load` self-verify), never a
parse→dump round trip. Renaming is exact-value only, mirroring the schematic side: a hierarchical
cluster literal like `Channel_1/sub` is NOT rewritten when renaming `Channel_1` — segment/prefix
renaming is deliberately out of scope (see `kicadstamp/config_rename.py`).

### Safety guards

- **Dry-run by default** — `--write` required to touch anything.
- **`.bak` + self-verify per file, independently** — before writing, the original text is copied to
  `<file>.bak`; after splicing, the result is re-parsed with `sexpdata` as a sanity check. If it
  doesn't parse, the file is restored from `.bak` and reported as failed — the rest of the batch
  still proceeds.
- **Non-ASCII refusal** (`--allow-non-ascii` to override) — guards the exact homoglyph-typo class
  that motivated this tool in the first place (a Cyrillic "С" instead of Latin "C" in a `Role`
  value).
- **Running-KiCad refusal** (`--force-with-kicad-running` to override) — a `.kicad_sch` that's
  open in Eeschema risks the edit being silently overwritten by KiCad's own next save.
- After `--write`, **"Update PCB from Schematic" in pcbnew is required** — the edit doesn't reach
  the board on its own.

## The fieldstool tab

`gui.fieldstool_window.MainWindow` is embedded whole as the first right-hand tab of the main GUI
(`gui/docks/fieldstool_dock.py`, see [gui.md's fieldstool tab
section](./gui.md#fieldstool-tab)) and shares that GUI's own `BoardConnection` and single 2s/400ms
poll — it never creates or polls a connection of its own (kipy's REQ socket allows exactly one
request in flight; a second independent timer on the same connection would interleave requests
mid-flight). This window has **no Components tree of its own** (retired 2026-08-01, along with the
separate `ComponentTreeDock` class that used to provide one — picking a target without a live board
selection exists via the main GUI's own Components tree, see below).

Splits the workflow into two phases with different KiCad requirements, matching the constraint
above:

### 1. Staging (KiCad open)

- **Pick root sheet** — points the tool at a project (same `root_sheet:` concept as the CLI).
  **Rescan** re-parses it (explicit action, not auto-polled — the schematic only changes when
  someone saves in Eeschema, not every couple of seconds) into `self._components` — one row per
  refdes (a shared multi-instance block expands to one row per member; a multi-unit refdes
  collapses to one row, flagged divergent if its units disagree on Role/Cluster — the schema allows
  this, nothing enforces it stays in sync).
- **Picking a target** — two ways, either fills the **Role**/**Cluster** combo boxes with the
  picked target(s)' EFFECTIVE current value — the live board's value when the live snapshot has
  seen this ref (2026-08-04: a target already Staged but not yet Applied has its new value only on
  the live board; re-selecting it used to show the schematic's stale, pre-Stage value, forcing
  edits "blind" — Denis live: "прописал роли... но когда кликаю эти диоды, ...роль... не видно"),
  else the parsed schematic's — uniform across all of them fills, differs clears (not left showing
  a stale value). A small note under the target label names any picked ref that still differs from
  the schematic (the same Apply diff Pending changes shows), refreshed on every poll tick too, not
  just on re-click:
  - **Select something in KiCad itself** (Eeschema *or* Pcbnew — the shared connection watches the
    PCB selection, and since PCB/schematic selection cross-probe in KiCad, a schematic-side
    selection shows up here too).
  - The main GUI's own [Components tree](./gui.md#components-tree), switched to **Not yet applied**
    mode, reads this window's `self._components` — filtered to `pending_refs` (2026-08-03: only refs
    with an actual schematic-vs-board discrepancy right now, so a component whose values already
    match doesn't stay listed as "not yet applied" forever). Click a **leaf** (one refdes) or a
    **group** node (every refdes in that Role/Cluster group at once, for a group-rename without
    retyping refdes) there instead of a live board selection. Clicking calls straight into
    this window's own `_on_tree_leaf_picked()`/`_on_group_picked()` and brings this tab to front.
- **Stage** — writes the current Role/Cluster form values straight onto the picked target(s)' live
  board footprint, over IPC (the same mechanism the main GUI's Components tree uses for **Clear
  all**/**Delete selected**) — nothing touches `.kicad_sch` yet. Pressing **Enter** in either the
  Role or Cluster field does the same thing as clicking the button (2026-08-04, Denis: "долго Stage
  жать") — deliberately not on focus-out, since losing focus also happens by clicking a different
  component, which would stage an unrelated or half-typed value with no explicit write action asked
  for. There is no separate staging queue
  to persist: whatever is currently on the live board (via Stage, Clear all, Delete selected,
  PlacerDock's Cluster tagging — any of them) already *is* the pending state (2026-08-03 redesign —
  the earlier JSON-backed queue could drift out of sync with the board, e.g. Clear all writing to
  the board but staging nothing, leaving Apply stuck disabled with no way to apply the erasure).
  A target whose footprint doesn't have the field yet (Role and/or Cluster) is skipped rather than
  blocking the rest of the batch (2026-08-04, same `has_field` guard Clear all/Delete selected use)
  — a warning names exactly which target/field was skipped; see [Ensure fields...](#2-apply-kicad-must-be-closed)
  or [Why fields must already exist on the target](#why-fields-must-already-exist-on-the-target).

### 2. Apply (KiCad must be closed)

- **Pending changes** (`gui/docks/pending.py`, tabbed with Log at the main window's bottom) shows
  the current diff: every refdes whose live-board Role/Cluster differs from the schematic's last
  Rescan, recomputed fresh on every Rescan and every ~2s poll tick — never stored, so it can't go
  stale relative to the board.
- Checks for a running KiCad process — if found, shows an **instruction** dialog ("save your work
  and close KiCad, then Apply again"). This is never automated (see [Why this write pipeline stays
  separate](#why-this-write-pipeline-stays-separate-from-kicadstampguis)).
- If KiCad is closed: plans the current diff through the exact same offline pipeline
  `fieldstool_cli.py set` uses, shows a confirmation summary, then writes (same `.bak`/self-verify
  guards as the CLI). On success, the schematic is rescanned — since it now matches the board, the
  diff comes out empty and Apply disables itself again. The success dialog reminds you to reopen
  KiCad — a running process never hot-reloads an externally-modified schematic file.
- **Ensure fields...** (same dock, next to Apply) — a separate sweep for a different problem:
  2026-08-04, found live that one component (`FB3`) had a `Role` property in the schematic but no
  `Cluster` property block at all — not caused by Clear all/Stage (those only ever write to the
  live *board* over IPC, never `.kicad_sch`, and even there a missing field is a hard stop, never
  silently created — see [Cluster/Role must already exist on the target](#why-fields-must-already-exist-on-the-target)
  below). Ensure fields walks the whole schematic tree and adds an empty `Role`/`Cluster` property
  to every component missing one outright, leaving every already-present value (even an empty one)
  completely untouched. Same KiCad-closed gate, confirmation summary, and `.bak`/self-verify write
  path as Apply — afterwards, reopen KiCad and run **Update PCB from Schematic** (F8) yourself to
  sync the newly-added fields down to the footprints; this action never touches the board.

## Why fields must already exist on the target

`kicadstamp.kicad.adapter.set_field_value()` (the live-board write Stage/Clear all/Delete
selected/PlacerDock's Cluster tagging all funnel through) is fatal if the target footprint has no
field with that name at all — it never creates one from scratch, because there is no sensible
default position/layer/schematic-symbol sync for a brand-new field on a live PCB footprint. If you
hit `FATAL ERROR: cannot set field 'Cluster'`, that refdes's footprint genuinely lacks the field —
run **Ensure fields...** above (or add it once by hand, in Symbol Properties or the library symbol
itself) and **Update PCB from Schematic** (F8) before trying again.

## Migrated from `tools/apply_role_cluster.py`

`kicadstamp.schematic_set_fields` supersedes that script (folded in 2026-08-01, not left
duplicated) — same core logic (parsing, splicing, safety guards), just reorganized into a reusable
library plus a `rename` mode that didn't exist there. `root_sheet:` (hierarchy walk) replaces its
`schematic_dir:` (flat glob) — **not** backwards compatible, update old configs.

## Tests

`tests/test_schematic_*.py` (offline core — parsing, discovery, editing, `set`, `rename`) plus
`tests/gui/test_schema_model.py`, `tests/gui/test_pending_dock.py` (the diff function + `QDockWidget`,
offscreen) and `tests/gui/test_fieldstool_window.py` (the embedded tab's own `MainWindow`, same
pattern as the rest of [`tests/gui/`](./gui.md#tests)), including a full Stage → Apply → write
round trip against a synthetic `.kicad_sch`. No live KiCad needed anywhere.
