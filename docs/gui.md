# PyQt6 GUI

A persistent window meant to stay open alongside KiCad while you work — not a one-shot script like
the CLI. Wraps `kicadstamp.explore`/`kicadstamp.author`/`KiCadBoardAdapter`/`ApplyPipeline`
directly; nothing new is reimplemented here that the CLI doesn't already do. For the underlying
extraction/placement mechanics themselves, see [docs/config.md](config.md) (YAML shape) and
[docs/commands.md](commands.md) (CLI equivalents of every action below).

## Launching

```bash
python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
```

`--verbose` seeds the Log dock's Verbose checkbox (see below) so DEBUG-level detail is visible
from the first run instead of having to turn it on after something goes wrong.

## Layout

Eight docks, tabbed into two groups plus a status bar:

- **Left** (tabbed): **Components** (Role/Cluster tree) and **Cells** (extracted Cell list).
- **Right** (tabbed): **fieldstool**, **Files**, **Extract**, **Placer**.
- **Bottom**: **Log**.
- **Status bar**: connection state, Reconnect/Refresh button, Always on top checkbox, Tray icon
  checkbox, Open fieldstool button, KiCad processes... button.

The **KiCad processes...** button opens a picker listing every running `kicad.exe` (PID, Windows
"Not Responding"/"Running" status, window title) — a shortcut for "look in Task Manager, pick the
stuck one, force-close it by hand" (added after a crashed/frozen KiCad process, left running
alongside a fresh one, blocked the fresh one's IPC connection). Deliberately never automatic: kipy
has no way to check any KiCad process for unsaved changes, so closing one is always something a
human picks and confirms here, never a heuristic decision the tool makes on its own (see
`gui/kicad_processes_dialog.py`).

Nothing here pushes updates from KiCad — kipy 0.7.1 has no selection/board-change events, so
"live" means polled: a slow timer (~2s) only reconnects while disconnected and never rebuilds the
tree on its own (an earlier version did, and the visible flicker on an idle board was worse than
useless); a fast timer (~400ms) tracks the board's own selection and reflects it back into the
tree. Rebuilding the full snapshot happens only on an explicit action — the status-bar button
(**Reconnect** while disconnected, **Refresh** while connected).

## Components tree

Two data sources, one tree, toggled by the **Not yet applied** checkbox:

- **Unchecked (default) — live board.** Groups the live footprint snapshot by **Role** (flat) or
  **Cluster** (hierarchical, split on `/` — `Channel_1/PI_FILTER` nests under `Channel_1`, matching
  the segment-prefix matching used throughout the config system). Click a leaf (one component) or a
  group (everything under it) to select it **on the real board**; the reverse also works — selecting
  something in KiCad's own PCB editor highlights it here. Clicking a **Cluster group node** (only in
  Cluster grouping, only a group — not a leaf) also fills the Placer dock's Cluster field. Since
  2026-08-13 a leaf in **Cluster** grouping shows its role next to the ref — `C1 (C_IN)` (Denis:
  "в дереве Components дописывать кроме Рефа — роль (если есть)"): the role isn't visible anywhere
  else in that mode, while in **Role** grouping it's already the parent group, so it isn't repeated.
- **Checked — not yet applied (schematic).** Same tree, same grouping/filter UI, but the data comes
  from the [fieldstool tab](#fieldstool-tab)'s own already-parsed `.kicad_sch` component list — and,
  since 2026-08-03, only the refs that currently have an actual Role/Cluster discrepancy between the
  schematic and the live board (the same diff Pending changes shows). A component whose schematic and
  board values already agree — including right after a successful Apply — no longer shows up here at
  all (found live: components used to stay listed even with nothing left to apply, which read as a
  bug once Pending changes existed alongside this view). A component never seen on the live board this
  session (no live snapshot entry to compare against) is also not shown, even if it's genuinely on the
  schematic — there's nothing to diff it against. Divergent multi-unit refs (units disagreeing on
  Role/Cluster within the schematic itself) get a ⚠ marker. Clicking a leaf or group here stages that
  target into fieldstool (same as clicking used to inside fieldstool's own, now-retired, internal
  tree) and brings the fieldstool tab to front. Refreshes automatically whenever fieldstool's own
  Rescan runs, or the schematic-vs-board diff changes (a fresh poll tick, or a Stage/Clear all write).

The grouping choice and the live/schematic toggle are both remembered across restarts. **Filter**
matches ref/role/cluster in either mode; **regex** switches from substring to a case-insensitive
regex (an invalid pattern just flags the field red, it doesn't crash or hide everything).

## Cells tab

A flat list of Cell names read from whatever file is assigned the **Cells** role in Files (see
below). Click one to feed the **Placer** dock's Cell field.

## fieldstool tab

The first right-hand tab embeds [fieldstool](fieldstool.md)'s own GUI whole, wrapped in one dock
(`gui/docks/fieldstool_dock.py`) — there is no second process/window, this is now the only way
fieldstool runs (a standalone `fieldstool_gui.py` entry point existed until 2026-08-02, retired as
pure duplication of this tab). Its **Pending changes** dock (2026-08-03: the schematic-vs-board
Role/Cluster diff — see [fieldstool.md](fieldstool.md#2-apply-kicad-must-be-closed)) is shared with
the main window, tabbed with **Log** at the bottom, not a dock local to this tab anymore. It shares
this GUI's own `BoardConnection` and single 2s/400ms poll (one kipy client, one REQ socket — a
second independent timer on the same connection would interleave requests mid-flight). It has no
Components tree of its own — the main [Components tree](#components-tree)'s "Not yet applied" mode
covers that job when embedded here (fieldstool's own tree, `fieldstool/gui/tree.py`, was retired
2026-08-01).

This replaced **Bulk edit** (also retired 2026-08-01), which used to set Role/Cluster directly over
live PCB IPC from this tab's slot with no further persistence step — that write was PCB-only and got
silently reverted by KiCad's own "Update PCB from Schematic", since `Role`/`Cluster` actually
originate in the schematic symbol. fieldstool's own Stage button (and the main Components tree's
Clear all/Delete selected) write over the same kind of live IPC today too, but Apply's schematic
diff is what actually persists the change into `.kicad_sch` — the missing step Bulk edit never had.
fieldstool edits `.kicad_sch` directly instead, which survives that resync — see
[fieldstool.md](fieldstool.md) for the full design and why it needs KiCad closed to Apply.

## Files

A file tree (default root: `boards/`, changeable) for picking YAML/JSON config files, plus three
named **roles** other docks read their target file from:

| Role | Consumed by | What goes there |
|---|---|---|
| **Cells** | Extract (writes), Placer/Cells tab (reads) | `extract`'s output goes into this file's `cells:` key. |
| **Extractor** | Extract | The structured root config `extract_profiles:` entries get written into. |
| **Placer** | Extract (wiring only), Placer | The structured root config `clone_placements:` entries get written into — the file you'd point a real `apply` run at. |

To assign a role: click a file in the tree, then **"Use selected"** on the role's row.

**All three roles can share one file** — all three are the same "structured root config" shape
(`extract_profiles:`/`cells:`/`include:`/`clone_placements:` as sibling keys, since `cells_file:`/
`cell_files:` were folded into `include:` on 2026-08-02 — see [docs/config.md](config.md)). A
dedicated file per role is just the default habit, not a requirement enforced anywhere.

## Config tree

A tree mirroring the actual `include:` file graph from a single root config file — pick it via
**Open Root file...**/**New Root file...**/the **Recent** dropdown. Every file node shows its own
sections (Cells/Clone placements/Thermal via arrays/Points/Rules/Extract profiles/Clone profiles)
and its own included files, recursively.

Right-click any entry for:
- **Rename...** — renames the entry; for Cells/Points, also rewrites every reference to it
  (`cell:`/`anchor_point:`) anywhere in the whole include: graph, not just the file it's declared in.
  `F2` on a leaf does the same (see [docs/hotkeys.md](hotkeys.md)).
- **Delete...** — removes the entry, after backing up the whole file it lived in (timestamped, next
  to the original — a repeated delete never overwrites an earlier backup). For Cells/Points, the
  whole include: graph is scanned for references first; if any are found, the confirmation lists
  them and asks whether to delete those referencing entries too (declining cancels the delete
  entirely rather than leaving a dangling reference).
- **Export.../Export selected...** — select one or more entries (multi-select is enabled just for
  this) and copy them into a separate file via a Save dialog. The originals are left untouched. If
  the target file already has content, you're asked whether to merge the exported entries into it
  or overwrite the whole file.

Right-click a file node for **Add cell.../Add point.../Add rule.../Add placer.../Add thermal via
pad.../Add extract profile.../Add included file...**, plus **Remove this file** (soft-disables its
`include:` entry, doesn't delete the file) when it's not the root. Since 2026-08-13 the "Add ..."
block is **section-aware**: right-clicking a category or a leaf shows only THAT section's own Add
action (cells → Add cell, extract_profiles → Add extract profile, ...); Clone profiles shows none
(read-only, no GUI edit form); a file header still shows all of them (Denis's decision — otherwise a
fresh file with no sections yet couldn't create its first entity). "Add extract profile..." isn't a
blank form like the other Add-actions (an extract profile's params come from a real board selection)
— it points the Extract dock at the file, pre-checks "Also save as extract_profile" and focuses the
profile-key field, so the profile is saved as a side effect of the next real Extract.

Clicking any node also switches the Detail dock to that node's own panel (Rule → Rules, file →
Project, ...). Since 2026-08-21 the entity docks no longer ask **which file to write to** — every
new record (Rule/ClonePlacement/CoordinatePlacement/ThermalViaArray/Point/Cell/ExtractProfile/
NetTrace) is written to the project's ONE root file (the file shown in the Project panel), with no
file picker in the form. The `include:` graph is still fully supported for READING: the Config tree
and every dock's autocomplete/list show entries from ANY included file. To move a piece of config
into a separate file, use the tree's context-menu **Export...** (see below).

Since 2026-08-15 every dock's file/name combos stay live: a file added/removed via the tree's
"Add included file..."/"Remove this file", an entry renamed/deleted there, OR a brand-new entity
created by an entity dock's own Save (e.g. CellDock's "Add cell..." + Save) is immediately visible
in every other dock's combo — no root reassignment, no GUI restart (a `graph_changed` broadcast,
see plan_2026_08_15_graph_changed_broadcast.md).

## Anchor tree

The **Anchor tree** tab (tabbed with the Config tree, same left group) shows the SAME config
records, regrouped by their ANCHOR edges instead of by file/section — "which record anchors on which
other record". Built PURELY from the config (no live board): one node = one config record. Roots are
records with no anchor (absolute placements), external `anchor_ref` targets (the FPGA-like case,
shown as `REF (external)`), and `points:` entries with no anchor of their own (`xy`/`anchor_origin`).
Points are ordinary records here: a point that chains to another point (`anchor_point`), anchors on a
produced role (`anchor_role`), or on an external ref sits under its own parent. A record whose anchor
is produced by another record appears as that record's child; a record with Sheet metadata appears
under
a synthetic **Sheet** folder inside its parent's branch (with its generated `Sheet_` prefix stripped,
e.g. `DAC_BUF` under `Channel_0`), giving the `Channel_0_DAC_BUF`/`Channel_1_DAC_BUF`/
`Channel_2_DAC_BUF` grouping from the plan's FPGA example.

Right-click a node for:
- **Redraw** — one record (the existing Redraw/`--only`).
- **Redraw dependents** — the cascade: this record plus EVERY record transitively anchored on it,
  redrawn in topological order (a parent always before the records anchored on it), each via its own
  `ApplyPipeline --only` run so a dependent reads its anchor's POST-move position. Per-record
  success/failure and the order are written to the Log dock. The same action is also a **Redraw
  dependents** button on the Placer dock.

A record anchored on a role TWO records both produce gets BOTH of them as parents and is therefore
shown (duplicated) under each — the tree is technically a DAG at that point, rendered as a tree the
usual way. The static anchor resolution lives in
[`kicadstamp/anchor_graph.py`](kicadstamp/anchor_graph.py) and the shared cascade in
[`gui/docks/cascade.py`](gui/docks/cascade.py).

## Trees

The **Trees** tab (tabbed with the Config tree and Anchor tree, same left group) is a hand-authored
editor for the OPTIONAL `trees:` section of the ROOT config — unlike the Anchor tree, which is an
automatic read-only view of the config's anchor graph. One tab per tree, each a nested node list
relative to the tree's own anchor. The toolbar offers **Add tree…**, **Rename tree…** and **Delete
tree…** (the whole-tree counterpart of a node's "Delete node", confirmed with Yes/No — No by
default), plus **Save** and **Redraw selected**. Structural editing happens through each node's
context menu (Add child / Add sibling / Reread current position / Edit node… / Delete node / Rename…
/ Move to…); the tree anchor pseudo-root's menu carries **Add node** and **Set anchor…**. The
**Set anchor…** dialog picks between **Origin (board 0,0)**, **Config record** (a name from the
config, resolved at Save) and **External refdes** (a live-board component outside the config) — the
external choice is stored with an explicit `external` marker so it is NEVER resolved against a
config record name: a refdes that happens to match a config record (e.g. a stale
`coordinate_placement` named `"fpga"`) cannot hijack the anchor (2026-08-28). Node
offsets are typed by hand or read from the live board via **Read current position** in the
Add/Edit-node dialog — a passive live-board read that never validates the whole tree's FORK-1
invariant, so an unrelated existing node with a conflicting inline anchor does not block it. Nothing
reaches the disk until **Save**, which replaces the whole root `trees:` section through the single
config_writer chokepoint (a fresh `.bak` is made first); linking/validation runs at Save via
`kicadstamp.link_trees`.

The Add/Edit-node dialog's **Ref:** combo is **Kind**-filtered: choosing a concrete **Kind**
(`clone`/`rule`/`coordinate`/`point`) lists only that section's record names, while **auto** shows
all placeable names — a name unique to one section plainly, and a name shared by 2+ sections once
per section prefixed `{kind}:{name}` (e.g. `rule:X`, `clone:X`). Picking such a prefixed entry
auto-sets the **Kind** to that section and keeps the clean name — a node left in auto with a
colliding ref would be fatal at link time ("0 or 2+ matches"). **External** keeps the combo
free-text for a live-board refdes (2026-08-29,
plan_2026_08_29_trees_node_kind_filtered_combo.md).

Adding a node whose record still carries its own inline anchor (`anchor_ref`/`anchor_role`/
`anchor_point`/`anchor_origin`) is always allowed — **Save never blocks on it** (FORK-1 no longer
runs at link/Save time). **Redraw selected** (or **Redraw whole tree**) on such a node now REDRAWS
it — with an informational, non-blocking warning: the record's own `anchor_role` keeps working for
the regular (non-tree) Apply/Redraw exactly as before, and this tree redraw moves it only TEMPORARILY
via a non-persistent override, never rewriting the record (2026-08-29,
plan_2026_08_29_fork1_rigid_redraw_override.md — REVERSES the pre-2026-08-29 rule that skipped such
nodes). So a channel's `CH0/1/2_DAC_BUF` can live in the `fpga` tree and be redrawn as a rigid group
WITHOUT stripping its `anchor_role` first.

The toolbar also offers **Redraw whole tree** — every node of the current tree in one click, with no
manual checkbox marking — and **Anchor position** — a read-only indicator of the current tree
anchor's live absolute position/rotation on the board (origin anchor: trivially (0,0)/0°; requires a
live KiCad connection; "unavailable" otherwise).

**Redraw selected is a rigid group** (2026-08-29, plan_2026_08_29_tree_live_rigid_redraw.md): a node
the tree owns (no inline anchor) is placed at its LIVE-captured offset from its parent, re-projected
into the parent's CURRENT position/rotation — so moving/rotating the anchor (or a parent node) and
redrawing the selected dependents moves them together, the offset rotating WITH the parent. The
offset is read live from the board at redraw time (not from the stored `xy`/`polar`, which remain a
fallback for a node with no live presence yet); the record's own fields are never rewritten — the
move is applied via a per-run, non-persistent position override (Option 1, see the plan's §3/§4).

## Detail dock

Extract/Placer/Project/Thermal via/Points/Rules/Net traces/Cells/Settings below all live as tabs
inside one shared **Detail** dock, not as separate docks — switching is both automatic (a
Config-tree click routes to the matching tab) and manual (click the tab bar directly). Every
automatic switch also
raises Detail to the front of its own tabified group (it shares screen space with fieldstool) and
updates its window title to name the page and, where there's a single obvious current entity, its
name too — e.g. "Detail — Cells: composite", or just "Detail — Extract" for pages with no single
current entity (added 2026-08-06, found live — Denis: "неплохо бы подсвечивать, какой док сейчас
активен. А то вообще, не видно, кто и что" — a plain tree click used to switch the tab silently if
Detail wasn't already the visible group).

## Settings

**Settings** (the last Detail-dock tab, 2026-08-15, plan `configurator_panel`) hosts pure GUI/app
settings for THIS MACHINE — a GUI facade over [`gui/settings.py`](gui/settings.py)'s
`gui_state.json`, deliberately NOT project config. The "Project" tab (RootMetadataDock) edits the
project YAML in the version-controlled project file; this tab never touches it. Everything here is
local per-machine state (the same storage `last_root_file`/`window_geometry`/`tree_group_by` already
use), this tab just adds GUI editing on top of a few more keys.

- **Always on top** / **Tray icon** — the two checkboxes that used to sit directly in the status
  bar moved here (2026-08-15); the actual window-flag / tray-icon LOGIC is unchanged in
  `MainWindow` (`_set_always_on_top`/`_set_tray_enabled`) — the checkboxes just re-emit their
  toggles and `DockHub` wires them back. The status bar is now the status label plus the
  Reconnect/Open fieldstool/KiCad processes... buttons only.
- **Highlight color** — one highlight scheme applied to ALL THREE highlight places: the Detail
  dock's active tab, the Config tree's selected item, and the Components tree's selected item.
  **System palette** uses the OS theme's `palette(highlight)`; **Custom** (via **Pick color...**)
  uses a literal color. Stored as `highlight_mode` (`"system"`/`"custom"`) + `highlight_color`
  (hex) in `gui_state.json`, applied at startup and re-applied live on change. Before this, both
  trees were bare native-styled `QTreeView`s whose selection was barely visible on Windows (the
  same "еле видно" bug found during this discussion).
- **KiCad connection timeout** — the ONE user-facing timeout (`DEFAULT_TIMEOUT_MS`,
  `kicadstamp/constants.py`), editable in milliseconds. Written straight into
  `connection.timeout_ms`, which `BoardConnection` reads on every connect, so it takes effect on
  the NEXT connection without disturbing an open one. The internal protective timings
  (`_CONNECT_TIMEOUT_GRACE_S`, pynng-safety's `_CLOSE_TIMEOUT_S`, the single-instance ping) are
  deliberately NOT exposed — one of them literally just closed a live GUI freeze (see
  `handoff_2026_08_15_pynng_close_timeout.md`).

## Extract

Builds a `Cell` from whatever's currently selected on the board (components, vias, tracks) and
writes it into the Cells file — the GUI equivalent of `kicadstamp_cli.py extract`.

**Origin**/**Net aliases**/**Net template role**/**Sub-placements**/**Existing** below live in a
tab widget (2026-08-04: previously stacked in one long column, whose minimum height was the SUM of
every section's own — the dock couldn't shrink below that even when most of it didn't apply right
now). A `QTabWidget` only sizes for the current page, so the dock resizes freely; **Net template
role**'s and **Sub-placements**' tabs are hidden outright (not just their content) until they
actually apply.

- **Write target** — a successful extraction writes the Cell into the project root file's `cells:`
  section (and, with "Also save as extract_profile", the profile recipe into that same file's
  `extract_profiles:` section). The former Cell-file/Profile-file/Placer-file dropdowns are gone
  (2026-08-21): everything the Extract dock produces now lands in the one root file.
- **Cell name** — defaults to the current selection's Cluster, slugified (`PWR/DAC0` →
  `pwr_dac0`), if nothing's been extracted from this Cluster before; if an existing Cells/
  Extractor key already matches, that wins instead. Never overwrites something you've typed.
- **Origin** — Bounding box (default, lower-left corner of the selection) / Component role (+
  optional pad) / Via net.
- **Net aliases** — a `QTableWidget` (2026-08-06, previously a hand-rolled grid — Denis: "у нас в
  экстракторе net-aliases, не таблица"), one row per net found on the selected components' pads.
  Rows themselves aren't user-added/removed — the net set is dictated entirely by the current board
  selection and rebuilt on every selection-watch tick; only the **Alias** and **Rule net** cells
  within each row are editable. A non-empty alias becomes a `{PLACEHOLDER}` in the written Cell
  (feeds `params:` for round-trip resolution — see [docs/config.md](config.md) on
  `net_template`/`params`). Each row also has a **"Rule net (null)"** checkbox (2026-08-05), mutually
  exclusive with the alias field — checking it writes that net's via/track as `net: null` instead, so
  a cell placed via `rules:`/ManualSpoke inherits whichever Rule's own net it's placed under (see
  [docs/config.md](config.md) on `rule_nets:`) — the mechanism for reusing the SAME cell across
  several Rules on different power rails, which `{PLACEHOLDER}` aliasing can't do here (ManualSpoke
  has no `params:` to resolve a template against).
- **Net template role** — appears only when a component's pads touch **2 or more already-aliased
  nets** (a bridging part — inductor, ferrite bead, fuse spanning two rails). The tool can't guess
  which one is "the" role's net_template in that case; extraction is blocked until you pick.
- **Sub-placements** (2026-08-25) — appears when an area-select sweeps up an existing,
  already-extracted top-level `clone_placement` (e.g. a PIF power-filter) together with the new
  cell's own components. Instead of copying that placement's geometry **flat** into the new cell
  (which would silently desynchronize the copy from the original placement the moment either is
  re-placed on another channel), the dock detects it: for every top-level `clone_placement` in the
  Placer file's config it resolves the placement's live board items (via the same
  `resolve_clone_board_items` the Re-extract feature uses) and checks whether the WHOLE set is
  covered by the current selection — only a fully-covered placement is a candidate (a partial
  overlap is probably a geometric coincidence and stays on the old path). The tab lists each
  candidate (placement name, its cell, how many components/vias/tracks matched) with a checkbox
  **on by default**; extracting with it checked writes the placement as a `clone_placements:`
  reference into the new composite cell (`name`/`cell`/`xy`/`rotation_deg`/`mirror`/`layer`, xy
  = the placement's world origin converted into the new cell's local frame) and **excludes** its
  board-items from the new cell's flat `components:`/`vias:`/`tracks:` — the same geometry is
  never both referenced and copied. Unchecking restores the old flat behavior. With the Cluster
  filter on, a fully-covered placement's own via/tracks are no longer silently dropped by the
  registry filter (they become part of the reference); foreign/partially-covered placements are
  still dropped as before. A selection covered ENTIRELY by Sub-placements is a legitimate
  **pure-composite** extract: the new cell gets only `clone_placements:` (empty flat lists) and
  the flat extractor is skipped. The cell's origin (bbox/component-role/via-net) is always
  derived from the FULL pre-exclusion selection, so the Sub-placement `xy` and the flat geometry
  share one coordinate system even when the origin component itself belongs to an excluded
  Sub-placement.
- **Existing (click to reuse a name)** — two lists (Cells/Profiles) read from the currently
  assigned files. Clicking an entry reuses its name outright and pulls its saved net aliases,
  net-template-role picks, and origin settings back into the form (matched by alias, not by the
  literal net text, so it still works when reusing a profile for an analogous Cluster on a
  different rail — e.g. `+2V5` vs `-2V5`). Also happens automatically when the current selection's
  Cluster slug matches an existing key.
- **Also save as extract_profile** — additionally writes a replayable recipe (name/output/params/
  origin/net_template_role) into the Extractor file's `extract_profiles:` section, so the same
  extraction can be re-run later from the CLI (`kicadstamp_cli.py extract --profile <key>`)
  without retyping the alias mapping.
- **Re-extract from current board state** (2026-08-25) — for an ALREADY saved Cell/extract profile:
  pick it in the **Existing** lists, then pick the **Placement** (the `clone_placement` whose
  `cell:` is that Cell) in the combo, and the dock re-captures that placement's live components +
  registered vias/tracks straight from the board and re-writes the Cell — no manual re-selection in
  pcbnew. The combo lists every `clone_placement` referencing the picked Cell; the button stays
  disabled when no placement uses that Cell (a bare Cell never placed through a `clone_placement`
  has nothing to re-extract from). Everything else (origin/net aliases/`raw_selection` recipe from
  the saved profile) is reused unchanged — only the source of the extracted items differs.
- The extracted Cell and its profile recipe are both written into the project root file, so the
  root file is immediately ready to use what was just extracted (no separate `include:` wiring).

## Placer

Builds and applies a `ClonePlacement` — the GUI equivalent of `kicadstamp_cli.py apply --only
<name>`. **This dock moves real footprints on the live board.**

**Source**/**Nets**/**Net overrides**/**Refs**/**Origin** live in a tab widget (2026-08-06, Denis:
"в пласере точно надо табом. Он может быть длинный!" — same "a stacked `QVBoxLayout`'s minimum
height is the SUM of every section's own" fix Extract/Root/Rules/Cells already got). Nets/Net
overrides/Refs started out as sections stacked inside one "Nets" tab, split into three sibling tabs
the same day (Denis, live: even tabbed, Params+Nets+Net overrides+Refs together still didn't fit —
Params stays paired with Nets, since both feed the same by-nets role resolution step and that
pairing itself was explicitly liked as-is; Net overrides and Refs are rarer and earn their own tabs
instead of competing for the same vertical space). Redraw/Save and the message label stay outside
the tabs — they act on the whole placement, not one tab.

- **Write target** — the placement is written into the project root file's `clone_placements:`
  (or `coordinate_placements:`) section. The former Cells-file/Placer-file dropdowns are gone
  (2026-08-21); the **Cell** combo inside Source still picks a Cell from the WHOLE `include:` graph.
- **Source** — **Cell** (default), **Role**, or **Cluster** (all added 2026-08-06, Denis: "путь
  потрясающе длинный: создать экстрактор, извлечь шаблон, сделать cell и только потом, placement") —
  Role/Cluster both skip Extract/Cell entirely for a genuine single-component placement; neither
  ever writes or reads a `cells:` entry (`ClonePlacement.role`/`.cluster`, already supported by the
  backend — this toggle is just their first GUI surface). Only good for a bare component with no
  via/track/second component of its own — for anything with real content, Cell + Extract is still
  the right path.
  - *Role* — pick a Role directly (same autocompleted combo as Anchor's own Role field below). Role
    is a CATEGORY, not unique — many components routinely share one — so if it's ambiguous on the
    board, Redraw resolves it the same way a real cell's role slots do (selection, then the
    placement's own Cluster — its `name` — narrowing; since 2026-08-14 `anchor_cluster` narrows
    only the anchor), or fails loud listing every candidate.
  - *Cluster* — same idea, but finds its target by an ALREADY-ASSIGNED Cluster PCB field instead
    (tag it first via the Components tree's Role/Cluster editing or fieldstool) — same-day pushback
    on Role alone, Denis: "Условие уникальности у нас касается кластера, а не роли... ОДНУ деталь
    надо размещать просто по кластеру. Роль там не при делах". No selection/narrowing: an exact
    match is either unique (used directly) or a tagging mistake, fatal either way. This mode also
    reuses the picked Cluster value as the placement's own name — the "Cluster:" name row below
    (see next bullet) hides entirely in this mode (found live 2026-08-06, Denis: "Зачем нам два поля
    Existing Cluster и Cluster?" — a second, independently-typed name risked silently retagging the
    component to something else on Redraw, since Cluster tags are meant to already be unique).
- **Cell** — a closed-set dropdown (not `configure_searchable()` — same "an editable combo on a
  field that must match an existing key is a freeze risk, and semantically wrong anyway" lesson as
  CellDock's own anchor_role_combo) populated from the currently picked Cells file's `cells:` keys.
  Also settable by clicking a Cell in the Config tree's Cells category (`set_selected_cell`) — both
  paths go through the same method, so either one keeps the other in sync. Added 2026-08-06 (Denis,
  live: "в пласере давай сделаем имя целла по выпадающему комбо-боксу... не удобно" — going to the
  Config tree for every single pick was the friction). Hidden in Role/Cluster mode.
- **Sheet** (added 2026-08-15) — the placement's OWN sheet, OPTIONAL: narrows ambiguous
  Cluster+Role inside the cell when this cell is cloned across reused sheets. A searchable combo
  autocompleted from the project's schematic files (`schematic_dir`/`schematic_files`, via
  `RuntimeContext.sheet_names`) on root change — a picker, not a whitelist, the same "populate,
  don't restrict" pattern as Cluster/Role/Nets (2026-08-15, see
  plan_2026_08_15_sheet_combo_everywhere.md). Ordered ABOVE "Cluster:" — the same (Sheet, Cluster,
  Role) convention as Single-component mode below.
- **Cluster** — the placement's Cluster TAG (the `name:` key written onto the board's components;
  also what gets clicked from the Components tree, see above). Since 2026-08-15 it is no longer the
  save identity — that moved to **Placer name** below. Hidden in Cluster *source* mode (see above) —
  the picked Existing Cluster value is reused as the name instead, nothing left to ask for here.
- **Placer name** (added 2026-08-15) — the placement's SAVE/`--only` identity (the optional
  `placer_name:` key in `clone_placements:`), separate from the Cluster tag: this is what
  `upsert_clone_placement` matches on to "replace this saved entry" vs "append a new one", and what
  `--only` addresses. Auto-fills from Cluster ONLY while creating a brand new placement; once the
  entry is saved it stays fixed, so editing Cluster on an already-saved placement no longer spawns a
  duplicate. Only needed when you want to be able to re-tag Cluster on a saved entry — leave it
  equal to Cluster and it is omitted from the file entirely.
- **Single component** (Source combo — a `coordinate_placements:` entry, no `cell:`) — its
  **Sheet**/**Cluster**/**Role**/**Name** identity fields live here on the Source tab (since
  2026-08-13, Denis: "Cluster, Role, Name надо на первый таб перенести" — they used to be on the
  Coordinate tab, mixed with the positioning fields, which was confusing to find). **Sheet** (added
  2026-08-15) is OPTIONAL — narrows Cluster+Role to one physical instance when the same sheet is
  cloned/reused and Cluster alone is identical across copies (distinct from the Anchor widget's
  `anchor_sheet`, which narrows the OTHER, anchor component). Both Sheet fields are searchable combos
  autocompleted from the project's schematic files on root change — a picker, not a whitelist (see
  plan_2026_08_15_sheet_combo_everywhere.md). The **Coordinate** tab then keeps only
  "where to put it" (Mode/X Y/Anchor/...). The "Cluster:" label intentionally matches the Cell-mode
  name row above — a different field, never visible at the same time.
- **Nets / Net overrides / Refs tabs** — all three tabs are hidden entirely (removed from the tab
  bar via `setTabVisible`, not just their contents blanked) in Role/Cluster mode (a synthetic
  one-component cell has no via/track net fields to template in the first place, and Role/Cluster's
  default resolution — by selection — never reads `nets:`/`refs:` at all, only "by nets" mode does;
  see `_on_cell_mode_changed`'s own docstring for why hiding them together avoids a silent no-op
  trap):
  - **Nets tab** —
    - **Params** — one row per `{PLACEHOLDER}` found anywhere in the picked Cell's own YAML (auto-
      discovered, not hand-typed) — the literal net each placeholder should resolve to for *this*
      instance.
    - **Nets** (added 2026-08-06) — role → literal net, takes priority over the cell's own
      `net_template:` for by-nets role resolution. Editable table (add/update by key, remove
      selected row) — Role column autocompletes from the PICKED CELL's own `components:` roles (not
      every role on the live board — `nets:`/`refs:` are only ever consulted for a role that's
      actually one of the cell's own components, see `resolve_roles_by_nets` in
      [docs/config.md](config.md); found live 2026-08-06 that a board-wide list was misleadingly
      broad, fixed same day).
    - **Auto-fill from board** — the "Auto-fill from board" button (and its silent auto-trigger on
      every Cell/Cluster pick, plan 2026-08-13) resolves each role on the LIVE board by Cluster
      prefix plus the cell's `net_template_pad`/`net_template_same_as_role` hints (see
      [docs/config.md](config.md)), pre-fills the blank Nets rows, and in the same worker run
      computes the per-role candidate-net narrowing for the Nets role-key combobox AND the Params
      comboboxes. Since 2026-08-16 it ALSO narrows by THIS placement's own **Sheet** (`sheet_edit`,
      i.e. `clone.sheet`) — the same (Sheet, Cluster, Role) convention the apply-time resolvers use —
      so a cell reused across hierarchical sheets (the live DAC_BUF repro: three `AD_DAC`+`DAC_BUF`
      instances, identical Cluster/Role written on the sheet FILE) narrows to the right instance
      instead of falling back to the full board net list. Empty/unknown `sheet_names` (no Placer file
      picked, or `schematic_dir` unresolved) is a silent no-op — the same full-list fallback as
      before, never a wrong guess. Since 2026-08-27, when a Params combo's narrowing resolves to
      exactly ONE candidate the value is selected automatically (only while the field is still
      blank — never overwriting a value the user already entered; a still-ambiguous 2+ candidate
      list is left blank, the same no-guess discipline as the Nets rows). Since 2026-08-28 (Phase 2
      step 2.4) the backend is the LIVE auto-derivation (`suggest_role_nets_live`): the hint-based
      `net_template_pad`/`net_template_same_as_role` suggestions are combined with the APPLY-side
      `_auto_derive_live_net` (live_pad), so a role WITHOUT cell hints is also filled whenever the
      live board gives a deterministic single net — a unique instance's one net, or the ONE net
      shared by all its candidates on this cluster (e.g. several C_IN_BULK on +3V3 in one PI-filter).
      The Nets table shows these auto-values and remains an OVERRIDE editor — the user can replace
      any row.
  - **Net overrides tab** (added 2026-08-06) — resolved net → final override name, applied AFTER
    Params/net_template substitution (see `resolve_net` in [docs/config.md](config.md)). Both columns
    autocomplete from the live board's actual net names.
  - **Refs tab** (added 2026-08-06, closes the last GUI gap this dock's own docstring used to flag) —
    role → explicit ref, bypasses role search entirely — last resort, breaks on re-annotation. Role
    column autocompletes from the picked cell's own `components:` roles, same reasoning as Nets above.
- **Origin**:
  - *Absolute XY* — a literal board position.
  - *Anchor (ref/role)* — position relative to an existing component: Ref **or** Role (mutually
    exclusive), optional Sheet, optional Pad, optional Anchor cluster (narrows which same-Role
    component is meant, when there's more than one). Role and Anchor cluster are pick-from-list
    combo boxes, autocompleted from the live board; Sheet is a searchable combo autocompleted from
    the project's schematic files (a picker, not a whitelist); Ref is plain free text (this project
    prefers Role over refdes — Role survives re-annotation, refdes doesn't — Ref exists mainly for
    the rare case it's actually needed).
  - *Point* — position relative to a named `points:` entry, autocompleted from the whole project
    (every `points:` key reachable via `include:`, not just this file's own).
  - Anchor/Point modes also take a flat XY **shift**.
- **Rotation / Layer / Mirror** — as in `ClonePlacement`'s own fields (see
  [docs/config.md](config.md)).
- **Redraw** — builds the placement, validates it, and actually runs it against the live board
  (loading the *real*, full Placer config first, so any other already-saved placement's vias/
  tracks are protected — not a synthetic single-placement preview). On success, the components
  that were actually placed are tagged `Cluster=<name>` (nothing else in the pipeline does this —
  see [docs/config.md](config.md) on `Cluster` being read-only during `apply`). Since 2026-08-26
  only the placement's OWN-level components are tagged: for a composite cell (nested
  `clone_placements:`), components resolved by a nested `CellPlacement` keep their own Cluster
  (`PIF_DVDD`, ...) instead of being re-tagged with the top placement's name (live bug
  tag_cluster_overtag — a Redraw wiped every nested sub-cell component's Cluster field). Change a
  field, click Redraw again — idempotent, safe to repeat.
- **Redraw & Save** (2026-08-25) — Redraw, then — only if it actually succeeded — Save, in one
  click. Redraw runs on a worker thread; Save waits for its real completion (never a naive
  `_on_redraw(); _on_save()`, so they can't race), and is skipped with a clear Log message when
  Redraw failed.
- **Save** — separately, writes the current form into the project root file's `clone_placements:` list
  (replacing an existing entry of the same name, never duplicating). Redraw does **not** save by
  itself — look, adjust, Redraw again, and only Save once you're happy with the result.
- **Select on board** (2026-08-25) — resolves the current form's placement to its live board items
  (its components plus every via/track the registry records under this placement's anchor) and
  highlights exactly those in pcbnew — a visual check of what this placement really owns, without
  moving anything. Nothing found (not placed yet) is a short Log message, never a crash.
- **Undo** (2026-08-25) — confirms first, then undoes the NEWEST `operation_*.json` in the whole
  project's operation-log directory (the same pick as the CLI `kicadstamp undo` — not necessarily
  the operation this Placer form ran). Moved components are restored and created vias/tracks are
  removed; the log file is deleted so it can't be undone twice. No logs -> a "nothing to undo"
  message with no confirmation dialog. The directory comes from the config's `operation_log_dir`
  (project root as fallback), else `logs/` — same resolution as `kicadstamp undo`.

Not covered by the GUI yet (still reachable by hand-editing the saved YAML): `by_selection` mode.
`anchor_sheet` narrowing WAS in this deferred list — closed 2026-08-15: every Sheet field is now a
searchable combo sourced from the project's schematic files (see
plan_2026_08_15_sheet_combo_everywhere.md), including ClonePlacement's Origin tab and
ThermalViaArrayConfig's anchor (both had the field in the model, only the form never reached it).

## Project

(Tab labeled "Project" — Denis, 2026-08-05: "давай не root, а project"; the panel underneath is
still called RootMetadataDock in code, since it edits the project's ROOT config file, same concept
the Config tree's "Open Root file..." uses — only the displayed label changed.)

Edits the project's root-config-only scalar keys: Layer/Place components/Skip existing components
(shown above the tabs, as general project settings), then three tabs — **Files** (Registry path/
Track registry path/Log file/Operation log dir), **Schematics** (Schematic dir/Schematic files),
**Via** (the four `via_search_*`/`via_keepout_clearance_mm` fields) — split 2026-08-05 for the same
"dock too tall to resize" reason as Extract's own tabs above.

Always targets the project's single root file — the one opened via "Open Root file..."/"New Root
file..."/the Recent dropdown (see the Config tree in `gui/docks/config_tree.py`) — regardless of
which included file is currently browsed in that tree. Browsing into an included file does not
retarget this panel: these fields are only valid on an actual root (an included file setting any of
them is fatal at load — see [docs/config.md](config.md)), and a project only ever has one.

## Points

Edits a named `points:` entry (see [docs/config.md](config.md) on the Point schema) — a reusable
anchor other `anchor_point:` references (Placer's own Point origin mode, Rule/ThermalViaArrayConfig)
point at by name. Added 2026-08-05 after noticing how closely Point's own shape already matches
Placer's Origin widget.

- **Origin** — **Absolute XY** / **Anchor (ref/role)**, now including a **Sheet** field (Denis:
  "нужен anchor_sheet в этой панели") — a searchable combo autocompleted from the project's
  schematic files, not a whitelist — alongside Ref/Role/Pad/Anchor cluster / **Point** (chain to
  another point by name — this field IS autocompleted, from the current file's own `points:` keys,
  closing the "points:-name autocomplete" gap the Placer section above still has for its own Point
  field) / **Board origin** (added 2026-08-06, Denis: "точка 0,0 -- это левый верхний угол листа,
  никак не origin" — reads the board's own LIVE origin marker via kipy instead of a guessed-at
  literal: **Drill/place**, the auxiliary axis drill/position files are always relative to (and
  Gerbers optionally, via their own plot-dialog option), or **Grid**, visual-only, Place > Set Grid
  Origin).
- **Shift X/Y** — flat mm offset on top of the Anchor/Point/Board-origin base (not available on
  Absolute XY — there, just edit the coordinate directly).
- **Resolve** — computes where this point (and whatever it chains through) resolves to RIGHT NOW,
  without writing anything or moving anything on the board (a Point has no physical effect of its
  own, unlike Placer's Redraw) — shows the literal X/Y in mm, and, if it resolved through a live
  footprint, selects that footprint on the board (the same highlight the Components tree's own
  click-to-select already uses). An unrelated OTHER point in the same file that's currently broken
  is silently skipped rather than blocking this preview — deliberately more lenient than a real
  `apply` run's all-or-nothing config validation. Sheet-based narrowing is not yet wired into this
  preview specifically (it needs the project's `schematic_dir`, a second file dependency this first
  pass deferred) — Sheet is still saved correctly for a real `apply` run, which does build that
  narrowing properly.
- **Save** — writes into the project root file's `points:` section (a dict keyed by name, unlike
  Placer/Thermal via's list-of-dicts sections — an existing name is replaced in place, not
  duplicated).

## Rules

Edits a `rules:` entry (see [docs/config.md](config.md) on Rule/ManualSpoke) — one shared anchor
(no `xy` mode here, unlike Points/Placer — only **Anchor (ref/role, + Sheet/Cluster)** or **Point**;
Sheet here is the same searchable combo autocompleted from the project's schematic files, not a
whitelist) plus an ORDERED list of spokes, each placing a Cell at a specific pad of that anchor
with its own hand-tuned shift/rotation. Added 2026-08-05 after Denis connected `fpga_spokes.sexp`/
`fpga_cap_pair_spoke.sexp` to a real project and hit the long-standing "Rules has no edit form" gap.

**Net**/**Origin**/**Spoke** live in a tab widget (2026-08-05, same "a stacked `QVBoxLayout`'s
minimum height is the SUM of every section's own" fix as Extract/Project): Net carries the rule's
own Net/Name/Retired/Skip; Origin carries the anchor-mode combo and its two rows; Spoke carries
every field the detail row below the table writes into. The spokes table itself, its move/Add/
Update/Remove row, and Redraw/Save stay outside the tabs — they act on the whole rule, not one tab.

- **Spokes table + detail row below** — picked over putting spokes in the shared Config tree
  (a spoke has no name field for a tree leaf label, spoke ORDER is semantically significant — the
  component pool consumes spokes in list order — and a table's columns show every spoke's shift/
  rotation/cluster at a glance). The table itself is read-only; all editing goes through the row
  below it and its own **Add spoke** / **Update selected** / **Remove selected** / **Move up** /
  **Move down** buttons — a table row can never drift from what was actually validated and stored.
  Every one of these, plus any spoke field's *editing finished* (blur / Enter / combo pick),
  writes the rule to disk immediately (2026-08-20) — no separate Save for spoke-level work, and a
  failed write is reported in the Log dock, never silent.
- **Cell** (per spoke) is a searchable combo listing every `cells:` key reachable from the
  project's root via `include:` — not just this file's own, since a spoke's cell routinely lives in
  a different file than the rule using it. **Point** (the rule's own anchor, not per-spoke — a
  spoke always anchors to a pad on THIS rule's own anchor) is populated the same whole-graph way.
  Both need the project's root, wired the same way Project's own panel does.
- **Redraw rule** — the whole rule, all non-skipped spokes, same replace-by-identity +
  `ApplyPipeline(only=[...])` shape as Thermal via's own Redraw.
- **Redraw selected spoke** — same, but every OTHER spoke in the copy handed to the pipeline gets a
  temporary `skip: true` injected (never written back — Save is unaffected) — sound because spoke
  resolution shares ONE component pool per net across the whole rule, so a single spoke can't be
  resolved in total isolation, but the pipeline can be told to skip every spoke except the one
  you're checking, which `skip:` already exists to do.
- **Save** — since 2026-08-20 this button's only remaining job is the rule's OWN **Net/Origin**
  fields (spoke edits autosave themselves, see the Spokes bullet above). It writes the whole rule
  into the project root file's `rules:` list, matched by name if set, else net (`rules:` is the one list
  section without a required `name:` — see [docs/config.md](config.md)'s `rule_effective_name`).
- **Bulk set Cell for net…** (2026-08-20) — sets `cell:` on EVERY spoke of every rule on the
  chosen net at once, even when those rules live in different included files (a net's rules
  routinely do — the dialog's net picker and preview walk the whole include: graph). A dialog
  previews the exact rules/pads that will change BEFORE applying; a partial write failure is
  reported explicitly (which rules wrote, which didn't) — never a silent half-applied change. If
  the currently-loaded rule is on that net, its form is reloaded from disk afterwards.

## Net traces

Edits a `net_traces:` record (see [docs/config.md](config.md)'s `net_traces:` section) — the GUI
face of `extract-net`/`apply --only=<net>` (plan `techdocs/handoff/deepseek/plan_2026_08_21_
net_trace_dock.md`). Added 2026-08-21.

- **Net picker** — a searchable combo listing every net with COPPER on the live board, sourced from
 `adapter.get_tracks()` + `adapter.get_vias()` over the WHOLE board — NOT the mouse selection
 (this deliberately closes the GUI gap the review's finding 5 was about: the old ExtractDock
 "Origin: Via net" combo is selection-scoped; a net can now be picked by name with nothing
 selected). Pad-only nets with no copper are excluded.
- **Anchor block** — the shared `AnchorOriginWidget`, anchor-role mode with sheet/pad/cluster
 fields. `net_traces` anchors by Role ONLY — filling Ref is rejected with an explicit message.
- **Extract** — captures the picked net's live copper (whole-board search) and writes it under
  `net_traces:` in the project root file, then refreshes the Config tree. Geometry (`tracks:`/`vias:`) is
 machine-written and shown read-only by design — edit it by re-extracting, not by hand (same rule
 as `cells:`).
- **Save** — edits the controllable fields (net/anchor/retired/skip) of an already-saved record and
 PRESERVES its machine-written geometry (a Save must never silently erase `tracks:`/`vias:`).
- **Redraw** — `apply --only=<net>` for the loaded record (same `ApplyPipeline` mechanism RuleDock's
 Redraw uses), so a moved anchor re-places the captured copper live.
- Clicking a `net_traces:` leaf in the Config tree loads that record into the form.

## Cells

Edits a `cells:` entry (see [docs/config.md](config.md) on `Cell`) — Components/Vias/Tracks (local
`along`/`across` offsets from the cell's own `(0,0)`) plus, recursively, nested `clone_placements:`
referencing other cells/roles. Added 2026-08-06 after Denis hit a real bug caused by the ONLY
existing way to create a cell — Config tree's **Add cell...** wrote a raw `{"components": []}` stub
straight to YAML with no form behind it at all ("создавать экстрактор под один компонент, прости,
тупняк" — a full select-on-board-and-extract round trip was the only way to add so much as one
component slot by hand).

Four tabs, same "table + detail row below" shape as Rules' own Spoke editor, one pair per kind
(Components/Vias/Tracks/Nested cells) rather than one tree merging all four — none of the four share
a common set of columns, so a merged tree would still need the detail form below to switch shape on
selection, buying nothing over separate tabs. (Denis initially proposed a tree given nested cells can
recurse — that tree is Config tree's own Cells category, which now shows a composite cell's nested
`clone_placements:` as child nodes for read-only navigation, not this dock's internal editor.)

- **Name**/**Layer** — the cell's own identity and absolute layer (`F.Cu`/`B.Cu`).
- **Anchor** — **(none)** / **XY** / **Role** (`+Pad`, optional) — **display-only metadata**, see
  `Cell.anchor_xy`/`anchor_role`/`anchor_pad` in [docs/config.md](config.md): marks which point of
  the cell's own local `(0,0)` already IS the origin by construction, never changes how any offset
  resolves. **Role** is a searchable combo sourced from THIS cell's own current Components list (not
  the live board) — it must name one of them.
- **Components** — Role (searchable, autocompleted from the live board's `Role` field, same source
  as Rule's own anchor-role combo), Offset along/across, Angle, Layer (inherit/`F.Cu`/`B.Cu`), Net
  template (for `clone_placements:`'s by-nets role matching only).
- **Vias**/**Tracks** — the cell's own top-level (spoke-level) vias/tracks, same fields as
  [docs/config.md](config.md)'s `TemplateVia`/`TemplateTrack`.
- **Nested cells** — one entry per `CellPlacement`: Name, **Cell** (searchable, every `cells:` key
  reachable from the project's root, same source as Rule's own spoke-cell combo) **or** **Role**,
  X/Y, Rotation, Mirror, Layer.
- **Scope cuts, deliberate** (shipped in one sitting, not because they don't matter): per-component
  vias (`TemplateComponentSlot.vias`) are not editable here — only the cell's own top-level Vias tab;
  a pre-existing per-component via list still round-trips untouched if the row isn't otherwise
  edited. Nested cells only expose their core fields — `nets:`/`params:`/`net_overrides:`/`refs:` are
  preserved verbatim if already present, not editable from this form.
- No Redraw/Resolve — unlike Rule/Point/Placer, a Cell has no anchor of its own on the live board; it
  only ever gets a physical position in the context of a placement/spoke that references it.
- **Save** — writes into the project root file's `cells:` section (a dict keyed by name, same shape as
  Points).

Config tree's **Add cell...** now opens this form blank (`new_cell()`) instead of writing a stub —
same shape as every other Add-entity action. Editing an EXISTING cell's content is a separate
action, right-click **Edit cell...**, deliberately not the same click as a plain left-click on a
Cell leaf (which keeps its original meaning, "pick this cell as a placement's content" — Placer's
own Cell field), so opening a placement form and opening the cell editor never fight over one click.

## Log

A read-only, copyable, searchable panel fed by a `logging.Handler` attached to the **root**
logger — every `logger.info`/`warning` anywhere in the backend shows up here, not just things this
GUI writes itself. **Verbose** toggles this panel's own level between INFO and DEBUG (the
console/file logging `kicadstamp_gui.py` was launched with, if any, is untouched). **Find** /
**Prev** / **Next** search the accumulated text; **Clear** empties it. **Auto-scroll** (checked by
default, 2026-08-15) force-scrolls the panel to the bottom after every new line while it's on —
Qt's own `QPlainTextEdit` only auto-scrolls when the view was already at the bottom before
appending, so scrolling up to read history used to make the log look stuck during a live error.
Uncheck it to get that plain Qt behavior back (the panel stops yanking the view down while you
read).

Since 2026-08-15 the panel's `logging.Handler` is attached to the live `QueueListener` started by
`setup_logging()` (queue-based logging, see `techdocs/handoff/plan_2026_08_15_queue_based_logging.md`)
when one exists — its `emit()` then runs on the listener's single thread and can never block the
thread that issued the log call; with no listener configured it attaches directly to the root
logger, as before.

This panel is in-memory only (capped, lost if the process is killed/crashes). If the currently open
root config sets `log_file:` (see [docs/config.md](config.md)), the GUI now ALSO writes everything
(DEBUG level, regardless of Verbose) to that file — same convention `kicadstamp_cli.py apply`
already used, previously CLI-only (found live 2026-08-06: a `log_file:` already sitting in a
project's root.sexp was silently never honored by the GUI). Re-attached fresh on every root-file
change (Open/New/Recent), so it always points at the CURRENTLY open project.

Since 2026-08-15 that root-config `log_file:` handler is attached the same queue-aware way as the
panel's own handler above — to the live `QueueListener` when one is running, so its writing also
happens on the listener's single thread and can never block the GUI thread on a handler lock; with
no listener configured it attaches directly to the root logger, as before.

## Tray icon

The **Tray icon** status-bar checkbox creates an OS tray icon with the app's real icon
(`images/kicadstamp.ico`, base64-embedded in `gui/app_icon.py` — the same icon as the window /
Windows taskbar, no images/ file dependency at runtime) and a menu: **Show/Hide**, **Open
fieldstool**, **Quit**.
While checked, closing the window via its title-bar X hides it instead of quitting — reachable
again from the tray (single click/double-click, or the Show/Hide menu item). Unchecked, closing
behaves exactly as without a tray at all — a real quit. The tray menu's **Quit** always does a real
quit either way.

A single-instance guard (`gui/single_instance.py`, `QLocalServer`/`QLocalSocket`-based) means
running `kicadstamp_gui.py` a second time while one is already running doesn't open a second
window — it raises the existing one instead and exits immediately. This guard is always active,
independent of the Tray icon checkbox.

The checkbox state persists across restarts (`gui_state.json`'s `tray_enabled`) — if it was checked
in an earlier session, a later launch starts with it already checked, so the title-bar X hides
instead of quits from the very first close, with no on-screen reminder that this is what will
happen. On Windows specifically, a freshly-shown tray icon commonly lands in the hidden/overflow
tray (the "^" arrow next to the clock) rather than the visible row — "window vanished, no icon
anywhere I can see" does **not** mean the process died; check the overflow arrow first. If the icon
still can't be found, re-running `kicadstamp_gui.py` (the single-instance guard above) raises the
existing hidden window without starting a second process — no need to hunt it down in Task Manager.

## Open fieldstool

The status-bar **Open fieldstool** button (and the tray menu's identical item) un-hides the main
window if it was tray-hidden and brings the [fieldstool tab](#fieldstool-tab) to front — useful if
another right-hand tab is currently active, or if that dock was individually closed.

## What's remembered between restarts

Plain JSON in `gui/gui_state.json` (gitignored, human-readable, deliberately not Qt's own
`QSettings`/`saveGeometry()` blob): window position/size, Always on top, Tray icon, Components tree
grouping and its live/"Not yet applied" toggle, the Files dock's root directory and last click, and
all three file-role assignments (Cells/Extractor/Placer). fieldstool's own tab keeps its own
separate state file (`gui/fieldstool_gui_state.json`).

## Tests

`tests/gui/` — offscreen (`QT_QPA_PLATFORM=offscreen`, set automatically), no live KiCad
connection needed, part of the default `pytest` run. Board-mutating logic (Placer's Redraw) is
tested with `ApplyPipeline`/`PlacementPlanner` mocked — it verifies the dock builds the right
config and calls the pipeline correctly, never that it actually moves anything. See
`tests/gui/conftest.py` for the fixtures: `qapp`, `main_window` (a bare stub, for dock-level tests),
`real_main_window` (the real `MainWindow`, needed for tray/close/fieldstool-embedding tests),
`fieldstool_window` (a real `gui.fieldstool_window.MainWindow` with a fake connection, for the
fieldstool tab's own staging/Apply logic standalone), `isolated_settings`, `log_dock`.
