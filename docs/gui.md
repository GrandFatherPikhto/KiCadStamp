# PyQt6 GUI

A persistent window meant to stay open alongside KiCad while you work — not a one-shot script like
the CLI. Wraps `kicadstamp.explore`/`kicadstamp.author`/`KiCadBoardAdapter`/`ApplyPipeline`
directly; nothing new is reimplemented here that the CLI doesn't already do. For the underlying
extraction/placement mechanics themselves, see [docs/config.md](config.md) (`.sexp` shape) and
[docs/commands.md](commands.md) (CLI equivalents of every action below).

## Launching

```bash
python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
```

`--verbose` seeds the Log dock's Verbose checkbox (see below) so DEBUG-level detail is visible
from the first run instead of having to turn it on after something goes wrong.

## Layout

Four top-level docks plus a status bar (2026-09-05: the right-hand **fieldstool**
dock and the bottom **Pending changes** dock were folded into the Components
master-detail):

- **Left** (group tabs at the bottom of the group): **Components**, **Config**,
  **Trees** — each a master-detail dock:
  - **Components** — since 2026-09-05 a splitter: left a tab widget whose tabs
    sit ON TOP — **Components** (the Role/Cluster tree) and **Pending changes**
    (the schematic-vs-board diff) — and the embedded **fieldstool** window as
    the right pane.
  - **Config** — the include-graph tree (left) + its context QView (right).
  - **Trees** — one tab per tree, each a tree | form-panel splitter (ONE panel
    that follows the selection — no Anchor/Node tabs).
- **Bottom**: **Log**.
- **Status bar**: connection state, Reconnect/Refresh button, Always on top /
  Tray icon (Settings), Open fieldstool button, KiCad processes... button.

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

The window has a **menu bar** with two top-level menus built by FUNCTION, not per dock: **File**
(Open/New/**Save**/**Discard unsaved changes...**/Recent/Close/Quit — see
[docs/hotkeys.md](hotkeys.md)) and **View** (2026-08-27, one checkable entry per top-level dock, so a
closed dock can be brought back without restarting).

## Widget height and scrolling (2026-09-12)

**A field's height is not cosmetics.** Qt computes the height of a `QComboBox`,
`QLineEdit` or `QSpinBox` from the current font, the screen DPI and the platform
style — the number you see on your own machine is not the number your users see.
Pinned to a constant it looks perfect for the author and is broken for everyone
else (clipped text, a half-visible cursor).

The rules:

- **Forbidden**: `setFixedHeight`/`setMaximumHeight` on an input field; a height
  or vertical padding for one through `setStyleSheet`; a vertical
  `Fixed`/`Maximum` size policy used to make a field smaller.
- **Allowed**, and used: height limits on widgets that scroll their own content
  — `QListWidget` (`gui/docks/entity_page.py:100`), `QPlainTextEdit`
  (`gui/docks/log_panel.py:169`), `QTableWidget` (`gui/docks/pending.py:241`) —
  and the declared maximums on the summary lists in
  `gui/fieldstool_window.py:651`/`:774` and `gui/docks/root_metadata.py:309`.
- **The main rule**: `setMinimumHeight(1)` on a container is legal only when a
  `QScrollArea` sits between it and the form's fields — or when the widget
  itself is a `QAbstractScrollArea` that scrolls its own content. Without the
  scroll area Qt stops clipping the container and starts shrinking its children
  proportionally, fields included. Measured on the real Trees dock
  (`diagnostics/probe_trees_dock_form_squeeze.py`; the container is the widget
  pinned to a minimum height of 1, as `gui/dock_hub.py:166` does):

  | container height | first combo, no wrap | fields that lost height |
  |---|---|---|
  | 800 px | 25 px | 0 of 8 |
  | 500 px | 25 px | 5 of 8 |
  | 320 px | 15 px | 8 of 8 |
  | 200 px | 0 px | 8 of 8 |
  | 120 px | 0 px | 8 of 8 |

  With the wrap (Config right pages and Trees form panels): 25 px and 0 of 8 at
  every height — the content scrolls instead of the widgets shrinking.
- `1`, never `0`: Qt treats an explicit `0` as "unset" and falls back to the
  layout's own `minimumSizeHint` (`gui/docks/log_panel.py:157`).
- **When the content does not fit: scroll the content, never squeeze the
  widgets.**

One implementation, `gui/ui_utils.wrap_in_scroll_area()` (with its
`MinHeightScrollArea`: `setWidgetResizable(True)`, minimum height 1, `NoFrame`,
as-needed bars, vertical `Ignored` + horizontal `Preferred`), is used by the
Config right pages (`ConfigTreeDock._wrap_right_page`) and by the Trees dock's
form panels.

A dialog's DESIRED height stays the desired height, but it may not exceed the
screen: `gui/ui_utils.resize_dialog_within_screen()`, used by the four fixed-size
dialogs (Cell 720x600, Project 560x620, Tools 520x560, Settings 780x540 — 620 px
plus the window frame and the task bar does not fit a laptop display at 125 %
scaling). With no screen at all (headless runs) it degrades to a plain
`resize()`.

Since 2026-09-12 (`plan_2026_09_12_node_dialog_usability` §Э3) the dialogs also
REMEMBER the size they were left at: `gui/ui_utils.restore_dialog_size()` (called
once where the size is decided, with the old constant as its fallback) reads
`gui_state.json["dialog_size:<ClassName>"]` — the class name, never the
translated title — and `gui/ui_utils.persist_dialog_size()` stores it on every
hide/close. Hiding is the one path every dialog has (`accept()`/`reject()` and the
window X both hide; a non-modal dialog closed with the X never emits
`finished()`), hence the event filter rather than a signal. The restored size goes
through the SAME screen cap as a constant — a size saved on a big monitor cannot
hang off a laptop. Only the SIZE is remembered; a position on another monitor
layout would put the window off-screen. Covers Cell/Project/Tools/Settings/
InstantiateCell/TreeInstances/ExtractCluster/TreeFromSelection dialogs and the
node dialog.

Guard: `tests/gui/test_no_widget_height_squeezing.py` — an ast tripwire over
`gui/**/*.py` for height calls on field-looking receivers, plus a behavioural
test that squeezes the real central widget to 120 px and requires every visible
field to keep its height. Probes, kept: `diagnostics/probe_min_height_squeeze.py`
(the synthetic mechanism) and `diagnostics/probe_trees_dock_form_squeeze.py`
(the real Trees dock, before/after tables).

## Save model (staging, 2026-09-01)

Every config edit in the GUI lands in an in-memory **working set** first
(`kicadstamp/config_working_set.py`) — the config tree, the name collectors, `load_config` and
Redraw all read the staged state immediately ("it worked"), but **nothing is written to the config
files until File > Save (Ctrl+S) commits the whole project**. This closes the old gap where the
Trees editor had a controlled Save+backup flow while every other dock wrote its config files in
real time.

- **File > Save (Ctrl+S)** flushes the working set to disk atomically: it validates the staged graph
  (`load_config`) BEFORE writing anything, so a cross-file inconsistency aborts with nothing written;
  backs each existing dirty file into `.history/` (next to the root config, timestamped, never
  overwritten); writes each file via a temp + `os.replace()`; then invalidates caches and refreshes
  the docks.
- **File > Discard unsaved changes...** drops the working set and reloads every dock from disk.
  Deliberately does NOT roll back Redraw steps already applied to the live board (that is what
  KiCad's own Ctrl+Z / the operation-log Undo button are for).
- A **●** in the status bar (and on the File > Save item) marks the project as having unsaved
  config changes. Switching or closing the project with a dirty working set asks
  Save/Discard/Cancel first.
- The per-dock Save buttons are GONE (2026-09-01, step 6): every dock auto-stages its record on the
  form's commit points — a field's blur/Enter, a combo pick, a checkbox toggle, an Add/Update/Remove
  row action, or a Trees structural edit. The Placer auto-stages too, with a **silent-skip**: an
  invalid partial placement is never staged (so the working set never holds an invalid record and
  the global Save's graph validation stays intact) and no error is spammed on every blur — the
  record stages as soon as its fields are complete.

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
  Rescan runs, or the schematic-vs-board diff changes (a rebuilt board snapshot — the main GUI's
  manual Refresh, or the Rescan itself, which rebuilds it first; the automatic ~2s tick is a no-op
  once connected — or a Stage/Clear all write).

The grouping choice and the live/schematic toggle are both remembered across restarts. **Filter**
matches ref/role/cluster in either mode; **regex** switches from substring to a case-insensitive
regex (an invalid pattern just flags the field red, it doesn't crash or hide everything).

**Writing Role/Cluster from the live board (live mode only — the write row is disabled in the
"Not yet applied" schematic mode, which has no real footprint to write to).** Between the tree and
the mode checkbox sit three controls. **Delete selected** and **Clear all** (2026-08-03) blank out
Role AND Cluster on the board footprints — Delete selected on whatever the tree currently has
selected (a leaf or a whole group), Clear all on every footprint in the live snapshot behind a
confirmation dialog (its blast radius is the whole board). **Tag selected** (2026-09-08, plan
role_cluster_selection_tagging) is the SET-side counterpart: type a Role and/or a Cluster value
(two editable combo boxes, either independently optional — an empty field means "don't touch it",
NOT "erase it"; if both are empty the button does nothing) and write it onto every footprint in the
current tree selection. Both fields in one click become ONE commit, so KiCad's Ctrl+Z undoes the
whole batch; a footprint missing a field you're actually writing is skipped and reported, it never
rolls back the batch. The combo boxes offer as suggestions the sorted unique Role/Cluster values
already present in the live snapshot — there is no separate fixed vocabulary, the board is its own
source of known values, and a value you just typed stays on the board and in the suggestions. Those
suggestion lists (every dock's Role/Cluster combos, the working-context Cluster combo, the tree
dialog candidates) are re-read from a board snapshot REBUILT on the worker thread at the point of use
— switching a Config right-QView page, or opening a Tree node/anchor dialog (2026-09-11,
plan_2026_09_11_stale_snapshot_role_lists.md) — instead of freezing at connect time (the automatic
poll tick is a no-op once connected); the components TREE rows themselves and the status-bar counter
still update only on the manual Refresh. A live KiCad connection is what feeds them; offline they are
simply empty and every combo stays a free-text picker. Since 2026-09-12 that distribution is also
ORDERED so a list refresh can never sit on the path of an unrelated click: `on_ready` (the Tree node
dialog) runs FIRST and the eight docks are repopulated after it (2026-09-12,
plan_2026_09_12_combo_refresh_deadlock.md). The same fix constrains any combo repopulation:
`set_combo_items` exits early when the list did not change, and silences not only the combo but also
its INTERNAL line edit — `blockSignals` on an editable combo never covered the QLineEdit inside it,
and Qt's own record insertion (`insertItems` → `rowsInserted` → `setCurrentIndex` → that line edit's
`textChanged`) leaked a Python slot call out of a refresh, which re-entered Qt on a non-recursive
signal mutex and froze the whole GUI. That does NOT mean "never touch a combo's internal line edit":
`currentTextChanged`/`currentIndexChanged` never fire for text typed by hand that is not in the item
list, so the internal line edit's `editingFinished` is the ONLY signal that reaches a dock's
autostage for a hand-typed role/cluster (measured 2026-09-12: 0 `currentIndexChanged` emissions for
free-typed text, `diagnostics/probe_combo_line_edit_signals.py`). The rule is about WHICH signal, not
whether: on a combo's internal line edit connect only user-driven signals (`editingFinished`,
`returnPressed`); never connect `textChanged`, which Qt also emits while it rebuilds the model. Both
halves live in code as `own_line_edits()` (any signal, `textChanged` included) and
`combo_line_edits()` (user-driven only) in `gui/docks/_common.py`, with a guard test that fails if a
raw `findChildren(QLineEdit)` scan appears anywhere else in `gui/`. This
covers "one Cluster for a whole group" (Role left empty), "a narrowed subgroup, one Role" (Cluster
left empty), or both at once — authoring Role/Cluster no longer requires the offline
fieldstool/.kicad_sch round-trip.

Since 2026-09-05 (plan components_fieldstool_master_detail) the Components dock is a
**master-detail**: the tree (and the shared **Pending changes** view) live in a left tab widget with
the tabs **on top**, and the embedded [fieldstool pane](#fieldstool-tab) is the right half of a
splitter. Selecting a component — clicking a tree leaf/group in either mode, or a **Pending
changes** row — switches the fieldstool pane to that component (its target + Role/Cluster
prefills).

## Cells tab

A flat list of Cell names read from whatever file is assigned the **Cells** role in Files (see
below). Click one to feed the **Placer** dock's Cell field.

## fieldstool tab

Since 2026-09-05 (plan components_fieldstool_master_detail) fieldstool is the **right pane of the
Components master-detail dock** — there is no separate right-hand dock anymore, and no second
process/window (this is the only way fieldstool runs; a standalone `fieldstool_gui.py` entry point
existed until 2026-08-02, retired as pure duplication). The whole [fieldstool](fieldstool.md) GUI
window (`gui/fieldstool_window.py::MainWindow`) is embedded as-is — `gui/docks/fieldstool_dock.py`
is now only a thin non-dock facade over that window. Its **Pending changes** view (2026-08-03: the
schematic-vs-board Role/Cluster diff — see [fieldstool.md](fieldstool.md#2-apply-kicad-must-be-closed))
is the Components dock's second LEFT tab (the shared single instance, no longer a bottom dock next
to Log). Clicking a Pending row shows that component in the fieldstool pane and reveals it in the
Components tree. It shares this GUI's own `BoardConnection` and single 2s/400ms poll (one kipy
client, one REQ socket — a second independent timer on the same connection would interleave
requests mid-flight). It has no Components tree of its own — the main
[Components tree](#components-tree)'s "Not yet applied" mode covers that job when embedded here
(fieldstool's own tree, `fieldstool/gui/tree.py`, was retired 2026-08-01).

This replaced **Bulk edit** (also retired 2026-08-01), which used to set Role/Cluster directly over
live PCB IPC from this tab's slot with no further persistence step — that write was PCB-only and got
silently reverted by KiCad's own "Update PCB from Schematic", since `Role`/`Cluster` actually
originate in the schematic symbol. fieldstool's own Stage button (and the main Components tree's
Clear all/Delete selected) write over the same kind of live IPC today too, but Apply's schematic
diff is what actually persists the change into `.kicad_sch` — the missing step Bulk edit never had.
fieldstool edits `.kicad_sch` directly instead, which survives that resync — see
[fieldstool.md](fieldstool.md) for the full design and why it needs KiCad closed to Apply.

## Files

A file tree (default root: `boards/`, changeable) for picking `.sexp`/JSON config files, plus three
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
sections (Cells/Clone placements/Thermal via arrays/Points/Chains/Extract profiles/Clone profiles)
and its own included files, recursively.

Since 2026-09-03 (plan tree_ui_state_persistence) the branches you manually COLLAPSE are remembered:
every refresh (a Save anywhere in the app rebuilds the tree) re-expands everything by default — a new
entry is always visible — and then re-collapses exactly the branches you had collapsed; the same
state survives app restarts (stored as deviations in `gui_state.json`).

Since 2026-09-01 (plan rules_to_chains) the **Chains** category is a NESTED tree, not a flat list:
category → **anchor** (grouped by `anchor_ref`/`anchor_role`/`anchor_point`) → **chain** (labelled
by its net or name) → **pad** leaves (one per spoke, sorted by pad number). There is no separate
table of pads anywhere — a pad is a leaf. Since 2026-09-05 (design
config_qview_chain_entity_pages) the Config dock is a master-detail and the tree clicks drive the
right QView pages: a SINGLE click on a **pad** leaf opens the **spoke editor** there with **Apply**
(commit the spoke) and **Redraw** (apply the current spoke to the board) buttons; a SINGLE click on
a **chain** node shows a clickable **pad list** in the chains-navigation QView; a SINGLE click on an
**anchor** node shows its clickable **chain list**; a DOUBLE click on a chain node opens the editor
in **chain mode**. The context menu adds chains-specific actions: on a chain node **Add spoke...** /
**Redraw chain** / **Bulk set Cell for net...**, on a pad leaf **Redraw spoke** / **Delete pad...**,
on an anchor node **Redraw chains...** (redraws all chains under that anchor).

Since 2026-08-30 (Entity/Placement split, phase 5.6) each file node also shows **Entities** and
**Trees** categories. Since 2026-09-05 a single click on an **Entities** leaf opens the Config
right-QView **Entity page** (the record's "Справка": Name/Cell/Sheet/Cluster read-only, Comment
editable, plus a clickable placements list that jumps to the entity's tree in TreesDock); a
**double click** on an Entities leaf opens the non-modal "Edit template" dialog with that Entity
loaded (its electrical fields, see the [Tools](#tools) section); the **Trees** category is
navigation-only — editing lives in the Trees dock.

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

Right-click a file node for **Add cell.../Add point.../Add chain.../Add placer.../Add thermal via
pad.../Add included file...**, plus **Remove this file** (soft-disables its `include:` entry,
doesn't delete the file) when it's not the root. Since 2026-08-13 the "Add ..." block is
**section-aware**: right-clicking a category or a leaf shows only THAT section's own Add action
(cells → Add cell, chains → Add chain, ...); Clone profiles and Extract profiles show none (no
dedicated Add form — profiles are a CLI/config-only section since the Extract dock was removed in
Phase F, 2026-09-01); a file header still shows all of them (Denis's decision — otherwise a fresh
file with no sections yet couldn't create its first entity). Since 2026-09-01 the single capture
entry point is the Tools menu's **Extract tree...** (see below): it auto-derives the `cells:` records
from the fully-selected clusters and captures inter-cluster copper as `net_traces:`, so the old
standalone Extract dialog / **New Extract...** entry points are gone.

Clicking a file/category switches the Detail dock to that node's own panel (a Cells leaf → Placer,
...; a plain file click no longer jumps to a Project page since 2026-09-01 — the Project tab moved
into a dialog). Chains nodes are NOT switched by a click — they are edited via DOUBLE click in the
Chain dialog (see below). Since 2026-08-21 the entity docks no longer ask **which file to write
to** — every new record (Chain/ClonePlacement/CoordinatePlacement/ThermalViaArray/Point/Cell/
ExtractProfile/NetTrace) is written to the project's ONE root file (the file shown in the Project
panel), with no file picker in the form. The `include:` graph is still fully supported for READING:
the Config tree and every dock's autocomplete/list show entries from ANY included file. To move a
piece of config into a separate file, use the tree's context-menu **Export...** (see below).

Since 2026-08-15 every dock's file/name combos stay live: a file added/removed via the tree's
"Add included file..."/"Remove this file", an entry renamed/deleted there, OR a brand-new entity
created by an entity dock's own Save (e.g. CellDock's "Add cell..." + Save) is immediately visible
in every other dock's combo — no root reassignment, no GUI restart (a `graph_changed` broadcast,
see plan_2026_08_15_graph_changed_broadcast.md).

## Trees

The **Trees** tab (tabbed with the Config tree, same left group) is a hand-authored editor for the
OPTIONAL `trees:` section of the ROOT config. One tab per tree, each a nested node list
relative to the tree's own anchor. Since 2026-09-03 (plan plan_2026_09_03_trees_menu_tools.md) the
dock has NO whole-tree action toolbar — every whole-tree action lives in the top-level menu
**Tools → Trees**: **Extract tree…** / **Extract cluster…** (new trees from the board selection),
**Create tree…** (an empty manual tree — the old dock "Add tree…"), **Rename tree…** / **Delete
tree…** (the whole-tree counterpart of a node's "Delete node", confirmed with Yes/No — No by
default), **Anchor position**, **Redraw selected** / **Redraw whole tree**, **Full redraw (all trees
and modules)…** and **Instances…**. The dock keeps the tabs, the checkbox subtree selection and the
read-only status row (anchor live position + unsaved-changes ●). Structural editing happens through each node's
context menu (Add child / Add sibling / Reread current position / Edit node… / Delete node / Rename…
/ Move to…); the tree ROOT row's menu keeps **Add node** and a **Set anchor…** shortcut that selects
that root row. Since 2026-09-04 (plan plan_2026_09_04_trees_dock_master_detail.md) every real-tree tab
is a **master-detail** splitter: the node list on the LEFT, ONE form panel on the RIGHT. **UPDATED
2026-09-11 (plan_2026_09_11_trees_dock_single_panel.md):** that panel used to be a FIXED two-tab widget
(**Anchor** / **Node**); the tab strip is gone because it read as "this node's anchor". The panel now
FOLLOWS THE SELECTION: select the tree's ROOT row (its anchor — now SELECTABLE) or clear the selection
to edit the tree ANCHOR; select a REAL node (any kind, `mount` included) to edit that node. A single
click loads an editor, the panel's rows are **Apply** / **Redraw** (no OK/Cancel — it stays open).
Editing a generated INSTANCE tree shows the same read-only notice as its context menu instead of an
editor. The anchor editor covers all six anchor modes: **Origin (board 0,0)**, **Config record** (a name
from the config, resolved at Save; a **Kind** filter narrows the ref list to one section —
Entity/Chain/Coordinate/Point/Clone/All — a picker aid only, the anchor grammar has no kind),
**External refdes** (a live-board component outside the config), **Self (component this tree places)**
(RENAMED 2026-09-11 from the old "Auto (derive from Entity's own cell)", same combo index — plan
tree_self_anchor, task Д.7: an optional **Node** = one of this tree's `kind "placement"` nodes (else the
single top-level one), plus an optional **Pad**; the base is read LIVE from that component),
**Role** (role + optional sheet/cluster/pad) and **Point** (a `points:` entry name). The external
choice is stored with an explicit `external` marker so it is NEVER resolved against a config record
name: a refdes that happens to match a config record (e.g. a stale `coordinate_placement` named
`"fpga"`) cannot hijack the anchor (2026-08-28). Because a ref anchor pointing at the tree's OWN root
Entity can never resolve (a guaranteed self-reference), the Config-record list excludes that Entity
whenever the tree already roots it with its single top-level `kind="placement"` node (2026-08-31) —
and if that exclusion empties the Entity section, a hint points to the Self mode instead of a bare
empty combo. Save also guards the paths the dialog cannot see (an anchor set while the tree was still
empty and the root node added/edited in afterwards, or a hand-edited `.sexp`): the same self-reference
is silently switched to a Self anchor with a status-bar/log notice. Editing an existing anchor is
free: the anchor form always pre-fills the current anchor's mode and fields (only **Create tree…**
still asks for an anchor in a modal — a not-yet-created tree has no tab yet), so a small
tweak (e.g. changing a role anchor's sheet) doesn't rebuild the anchor from scratch. Node
offsets are typed by hand or read from the live board via **Read current position** in the
Add/Edit-node dialog — a passive live-board read that never validates the whole tree's FORK-1
invariant, so an unrelated existing node with a conflicting inline anchor does not block it. An Entity
parent (a `kind="placement"` node) resolves its live position/rotation from the tree that places it —
the same recursive anchor-base + node-path composition the Apply-time materializer uses — so the
offset preview works for a resolvable Entity parent too. An Entity that no tree places (yet) falls
back to its own cell's single zero-offset (local 0,0) component's role (the same derivation as the
self-anchor), so "Read current position" for e.g. fpga_flash works even BEFORE the node that places
it is saved; only an Entity with NEITHER a placement node NOR a readable zero-offset component (or
one placed twice / in a cycle) warns with the materializer's own fatal text (2026-08-31). Nothing
reaches the disk until **Save**, which replaces the whole root `trees:` section through the single
config_writer chokepoint (a fresh `.bak` is made first); linking/validation runs at Save via
`kicadstamp.link_trees`.

**UPDATE 2026-09-11 (plan_2026_09_11_tree_settings_form):** the root row's form is now the TREE
SETTINGS form — the anchor editor PLUS a **Tree settings** group carrying the tree's own inner point
(the "suspension point") and its own angle. The suspension point is a 3-way choice: **Tree origin
(0,0)** (the default), a **Coordinate (xy/polar)** in the tree's own frame, or a **Node of this
tree** (`pivot-ref`). The node list offers ONLY nodes that follow the tree — never `mount`,
`module`, `external`, or a node hanging under a mount node — so it can be legitimately EMPTY (every
positioned node pinned to a live component, as in `ch0_dac_buf`); the form then says so in place
and points at the coordinate, which stays fully usable. Coordinate and node are mutually exclusive.
Like a node's offset, the settings are shown in the PCB Editor frame (design §3.9): the **Angle
(board deg)** field is the ABSOLUTE plate angle (anchor angle + the stored dovоrот) and the
coordinate is a board-frame millimetre vector, while the config keeps only the dovоrот and the
tree-frame coordinate. Changing the angle re-expresses the coordinate DISPLAY without touching the
stored value, and typing a coordinate converts it with the angle in force at that moment (no cached
base — the `9887468` class of bug). When the anchor does not resolve live the settings fields are
disabled with a reason and Apply writes nothing. The node serving as the handle is marked in the
tree list with a blue accent and a `(handle)` tag (never a second column), and the mark follows the
`pivot-ref` when it changes.

**UPDATE 2026-09-11 (Б3.1, plan_2026_09_11_external_point_materialization):** the anchor block also
carries the anchor's OWN **Shift X/Y** — stored in LOCAL mm of the base and shown in the PCB Editor frame
(board mm), converted at the ANCHOR's angle (the shift lives in the base frame, before the tree's
dovоrот; never a cached angle), and disabled with a reason when the anchor does not resolve. A
`(point ...)` anchor now APPLIES, not only reads; at a point/origin anchor the tree's own angle is the
sole source of content rotation.

**UPDATE 2026-09-12 (plan_2026_09_12_tree_point_markers, З):** the tree-settings form carries ONE
**Show tree markers / Hide tree markers** toggle. Pressed, it draws two circles on the board overlay
(the layer/colour from Settings → Board overlay) for the tree SELECTED right now: the **anchor** (where
the tree hangs — the point it is moved and rotated by) and the **base** (the origin of the tree's own
local frame, from which every node's `xy` is measured and which used to be invisible). The vector
between the two circles IS the rotated suspension point made visible, which is how a `pivot-xy` number
can finally be checked against the board. With a zero suspension point at a zero angle the two positions
coincide and only the anchor circle is drawn. Both positions are LIVE reads, so the whole operation runs
on a worker, and the button's label is read from the overlay owner's key map (not from a widget flag that
could drift). The circles belong to ONE tree at a time — switching to another tree (or renaming/deleting
it, or loading a different root config) takes the previous tree's circles down. No board, or an anchor
that does not resolve, is one line in the Log and nothing else — never a dialog; circles are pure
visualisation and are never written to the config. The suspension point itself has NO separate circle: by
construction it always lands exactly on the anchor (`resolve_module_effective_base` inverts the pivot onto
the marker), so a second circle there would sit on top of the anchor.

Since 2026-09-03 the node editor is a TWO-TAB form — shown in the
master-detail panel for a selected node, and inside the modal Add dialog while no node exists
yet: **General**
(everything above — Kind/Ref/Offset/Pivot/Rotation/Read current position/Name/Group) and a **Position**
tab. **UPDATE 2026-09-11 (plan_2026_09_11_tree_mount_nodes):** the Position tab used to offer a
per-node `own_anchor` ("Relative to component") — that grammar was REMOVED, because a node's base is now
ALWAYS its parent (one rule, no exceptions), which is what makes the drawn tree and the computed tree
agree. The shared `AnchorOriginWidget` picker in that tab now belongs to a **kind "mount"** node only and
is HIDDEN for every other kind: a mount node is a POINT OF REFERENCE — it places nothing itself, carries
the role anchor (**Role**, optionally narrowed by **Sheet**/**Cluster**, shifted onto a specific
**Pad**) and its children are laid from that live component's position/rotation. Pick **Kind = mount** in
the Add/Edit form to create one, or run the `convert-trees` CLI command on a config still written in the
old `own_anchor` grammar (see `docs/commands.md`). A mount node's base is live-only: with no KiCad
connection the offset/rotation fields are disabled and the raw values are shown, and a mount node WITHOUT
a Role is refused on save. Since 2026-09-10 (plan marker_frame_and_sheets J.3) the node editor's **Sheet**
combo is fed from the PROJECT's sheet map (`RuntimeContext.sheet_names`, built from the schematics),
like the anchor dialog's own Sheet field always was — NOT from the ~2s board snapshot, whose
`Selected.sheet` was empty for every footprint (measured live: 325 footprints, 72 roles, 34 clusters,
0 sheet names), which left this combo permanently blank. **Read current position** diffs against the
node's parent — the one base — and is hidden for a mount node (whose position IS its anchor).

Since 2026-09-11 (plan tree_node_live_read_and_board_frame) the editor and the STORED data speak
deliberately different frames. The **Offset** row (labeled *Offset (board frame)*) and the
**Rotation** field show BOARD-frame values — x right, y down, rotation as the ABSOLUTE board angle —
so the user never has to rotate axes in their head because the node's base (a parent node, the tree
anchor, or a "Relative to component" anchor) happens to be turned. The CONFIG keeps storing them in
the BASE's LOCAL frame (a local offset and a rotation relative to the base — see `docs/config.md`,
`trees:`), which is what makes one tree reusable by channels whose copies are rotated differently. The
conversion runs in exactly TWO places — when the form loads and when it saves — is bit-exact at
multiples of 90° (opening and closing a node never moves the config), and in Polar mode touches only
the angle (the radius is unchanged). The **Pivot (embedded tree's own frame)** row is deliberately NOT
converted: a module's pivot lives in the EMBEDDED tree's own frame, not the board frame (the label says
so). With no live connection — or a base that cannot be resolved — the form falls back to the RAW
stored values and DISABLES the offset/rotation fields with a one-line explanation, so an offline
rename + Save cannot corrupt a node. **Read current position** now reads a `kind="placement"` node from
the LIVE CLUSTER of its Entity's cell (the same reader the board overlay uses), never from the tree
node that places it — a cluster moved by hand in KiCad is read where it actually stands; a MIRRORED
instance is refused with an explicit warning (the trees layer has no mirror storage) and writes nothing.

**UPDATE 2026-09-12 (plan_2026_09_12_node_form_mount_parent_base):** a node whose PARENT is a mount node
now resolves its base through the SAME mount seam every layout walk uses (`mount_node_base`, fed the tree's
own `tree_layout_base`) instead of looking the mount's local name up as a config record — its offset and
rotation fields are editable and shown in the mount's frame again (before this fix EVERY child of every
mount node reported "No live board connection", with KiCad plainly connected). Two consequences worth
knowing: the **Position** tab is now shown ONLY for a `kind "mount"` node (that tab holds nothing else, so
an ordinary node no longer gets an empty tab at all), and a DISABLED offset/rotation now names the real
cause — the role/ref that did not resolve, also written to the Log — instead of always blaming the
connection. "No live board connection" is shown only when there really is no board.

Since 2026-09-11 (plan node_form_base_frame_follows_anchor) the frame FOLLOWS the selected anchor.
Changing the Position tab's base — switching **Relative to parent** <-> **Relative to component**, or
editing the anchor Role/Sheet/Cluster/Pad — re-resolves the base and re-expresses the DISPLAYED offset
through it so the NODE STAYS WHERE IT IS: the absolute position is preserved and the shown offset
changes by exactly how much the base moved. The shown **Rotation** is an ABSOLUTE board angle, so it
does not change at all on such a switch; only the RELATIVE angle the config stores changes (by the
difference of the two bases' rotations). The resolve stays cheap and lazy — the base is re-read ONCE per
committed anchor change, not per keystroke (typing only invalidates the cache; a short debounce coalesces
it). If the new anchor cannot be resolved live, the fields are disabled with an explanation and the RAW
stored values are restored, so a Save can never write board-frame numbers as a different frame.

Since 2026-09-04 a SINGLE click on a non-module tree node already loads its editor onto the
master-detail panel, so double-clicking such a node no longer opens a modal — it just makes sure the
node is selected (the panel then shows its editor); a module node / a "⇐ embedded in" /
"→ instance:" pseudo item still jumps to the referenced tree's tab. The node editor's **Apply**
writes the form onto the node in place (marks the dock dirty, stays open — nothing reaches disk until
Save); **Redraw** applies first, then places that node's REAL component on the live board at its
(edited) config position — one background ApplyPipeline `--only` run, never blocking the UI. Un-applied
edits are discarded — with a non-blocking "Unapplied changes were discarded." notice in the status row
(2026-09-04, design §9.4) — when you select ANOTHER node, or when a structural edit rebuilds the tree.
The generic cascade "Redraw dependents" for an arbitrary node is intentionally NOT restored (design
§9.2): tree redraws stay selected / whole-tree / forest only. The ADD dialogs (Add / Add child / Add
sibling / Add node) keep their modal OK/Cancel — there is no node to apply/redraw before it exists,
and a brand-new tree may not have a tab yet.

Since 2026-09-12 (`plan_2026_09_12_node_dialog_usability`):

- **OK validates before it closes.** The node dialog's OK runs `build_node()`
  first and accepts only when it built a node — a refused Ref/offset leaves the
  window OPEN with everything typed, so one edit is enough to retry (before,
  OK was wired straight to `accept()` and validation ran after `exec()`, when the
  input was already gone). Cancel/Close stays the unconditional reject, exactly
  like the edit mode's Close.
- **X/Y start at 0** in ADD mode (an empty xy field is an error, and "the node
  sits on its base" is the common case); EDIT mode keeps the node's own values,
  and the polar pair is untouched.
- **Parent:** on the EDIT form re-hangs the node under another **mount** node of
  the same tree (or back to the top level). The offset is re-expressed through
  the new parent's base at the moment the combo changes, so the node does NOT
  move physically — the binding changed, not the place. When the new parent's
  base cannot be resolved on the live board nothing is recalculated: the Log says
  so and the move needs an explicit confirmation. A node is never offered its own
  subtree (no cycles), and a node that IS the tree's `pivot-ref` is offered no
  mount parent at all — a node under a mount ancestor has a LIVE base and cannot
  be the inner point (`_validate_tree_pivot_ref` would refuse the config at the
  next load).

Since 2026-09-12 (`plan_2026_09_12_move_to_recalculates_offset`):

- **Re-hanging a node keeps it where it is — through EITHER path.** The context menu's
  **Move to…** and the EDIT form's **Parent** combo (mount nodes only, see above) now run the
  SAME recalculation: the stored `xy`/`polar` are re-expressed in the new parent's base frame and
  the stored `rotation` follows that base's angle, so the node does NOT move on the board — the
  config can no longer change a node's place just because its binding changed. A polar node stays
  polar (only its numbers change), and the node's whole SUBTREE travels with it untouched: the
  children are stored relative to the node that moved, whose pose is unchanged.
- **An unresolvable base asks instead of moving.** When the new parent's base cannot be resolved
  on the live board (no connection, a component that is not there), nothing can be held still, so
  both paths say so in the Log and ask for an explicit confirmation ("Re-hang … WITHOUT
  recalculating its offset?"); answering **No** leaves the tree and the node exactly as they were.
  No path re-hangs silently any more.
- **Move to… never offers a config-killing parent.** Re-hanging the tree's `pivot-ref` node under
  a `mount` node — or under anything hanging from one — is not offered at all: such a node does
  not follow the tree, and the loader rejects the config at the next load
  (`_validate_tree_pivot_ref`).

Since 2026-09-03 (plan tree_ui_state_persistence) the ACTIVE tab and the per-tree expanded/collapsed
state are remembered too: a rebuild no longer resets you to the first tab or collapses every tree
back — whichever tree tab was active and which nodes were expanded are restored by name/ref, and the
whole state survives app restarts (`gui_state.json`).

The **Ref:** (Add/Edit-node) and **Set anchor…** candidate lists refresh automatically the moment the
include graph changes or an entity dock saves a new/renamed Entity/Cell/Chain/... — no app restart or
root reassignment needed (2026-08-31). The refresh only re-reads the config behind those lists; trees
you are currently editing are left completely untouched, including their unsaved edits.

The Add/Edit-node dialog's **Ref:** combo is **Kind**-filtered: choosing a concrete **Kind**
(`clone`/`chain`/`coordinate`/`point`) lists only that section's record names, while **auto** shows
all placeable names — a name unique to one section plainly, and a name shared by 2+ sections once
per section prefixed `{kind}:{name}` (e.g. `rule:X`, `clone:X`). Picking such a prefixed entry
auto-sets the **Kind** to that section and keeps the clean name — a node left in auto with a
colliding ref would be fatal at link time ("0 or 2+ matches"). **External** keeps the combo
free-text for a live-board refdes (2026-08-29,
plan_2026_08_29_trees_node_kind_filtered_combo.md).

Since 2026-08-30 (phase 5.5) the Add-node dialog auto-numbers a NEW node's ref when the typed ref is
not one of the config's placeable record names AND is already used by another node somewhere in the
trees: `_unique_ref` appends `_1`/`_2`/… so the tree never saves a duplicate ref (which would be
fatal at link time).

Adding a node whose record still carries its own inline anchor (`anchor_ref`/`anchor_role`/
`anchor_point`/`anchor_origin`) is always allowed — **Save never blocks on it** (FORK-1 no longer
runs at link/Save time). **Redraw selected** (or **Redraw whole tree**) on such a node now REDRAWS
it — with an informational, non-blocking warning: the record's own `anchor_role` keeps working for
the regular (non-tree) Apply/Redraw exactly as before, and this tree redraw moves it only TEMPORARILY
via a non-persistent override, never rewriting the record (2026-08-29,
plan_2026_08_29_fork1_rigid_redraw_override.md — REVERSES the pre-2026-08-29 chain that skipped such
nodes). So a channel's `CH0/1/2_DAC_BUF` can live in the `fpga` tree and be redrawn as a rigid group
WITHOUT stripping its `anchor_role` first.

**Redraw whole tree** (Tools → Trees) redraws every node of the current tree in one click, with no
manual checkbox marking. On the first redraw in a profile whose copper registry is still EMPTY while
the board already carries copper, KiCadStamp asks "adopt existing copper into the registry?" (Bug 3,
2026-09-05): the redraw registers any existing copper that matches the layout as owned, so a later
move relocates it instead of leaving leftovers. Recommended flow: run one redraw WITHOUT moving
first. **Anchor position** (Tools → Trees) refreshes the read-only indicator of the
current tree anchor's live absolute position/rotation on the board, shown in the dock's status row
(origin anchor: trivially (0,0)/0°; requires a live KiCad connection; "unavailable" otherwise).

**Redraw selected is a rigid group** (2026-08-29, plan_2026_08_29_tree_live_rigid_redraw.md): a node
the tree owns (no inline anchor) is placed at its LIVE-captured offset from its parent, re-projected
into the parent's CURRENT position/rotation — so moving/rotating the anchor (or a parent node) and
redrawing the selected dependents moves them together, the offset rotating WITH the parent. The
offset is read live from the board at redraw time (not from the stored `xy`/`polar`, which remain a
fallback for a node with no live presence yet); the record's own fields are never rewritten — the
move is applied via a per-run, non-persistent position override (Option 1, see the plan's §3/§4).

**Module embedding (2026-09-02, plan_2026_09_02_tree_module_embedding.md):** choosing **Kind =
module** in the Add/Edit-node dialog embeds ANOTHER tree as a rigid sub-layout. The **Ref:** list
switches to the NAMES of the other trees — excluding the current one, any tree this one already
embeds, and any tree that would close a module cycle — and the node's offset/rotation position the
embedded tree's marker. A second, module-only **Pivot (embedded tree's own frame)** offset set says
which point
INSIDE the referenced tree's own local frame must land exactly on the marker (blank = the referenced
tree's origin); **From child node...** copies an existing child-tree node's offset there. Module refs
are NEVER auto-numbered and are NOT counted as "used" record refs (the same child tree may be
embedded by several different parents; only a duplicate inside ONE parent is invalid — a config
fatal at Save). Double-clicking a module node opens the referenced tree's tab; a tree that others
embed shows one **"⇐ embedded in {parent}"** item per embedding parent (double-click navigates back).
The module-aware FULL redraw — every tree's records plus every active module's content, laid out from
each tree's live anchor (role/auto/origin/point/ref) — runs from **Tools → Trees → Full redraw (all
trees and modules)…** (menu only, no dock button); the per-tree **Redraw selected / Redraw whole
tree** items in the same **Tools → Trees** submenu stay single-tree.

**Tree instances (`tree_instances:`, 2026-09-02, plan_2026_09_02_tree_instances.md; optional `cluster:`
axis 2026-09-03, plan tree_instances_cluster):** a tree + its Entity records can be declared once as a
TEMPLATE and instantiated per schematic sheet (and, optionally, per cluster) by `tree_instances:`
declarations (see the config docs). Each generated instance shows here as an ordinary tab whose nodes
are a deep copy of the template — refs suffixed `__{instance.name}`, the instance's `sheet` (and, when
the declaration sets it, its `cluster`) substituted into the copied Entities and the role anchor.
Instance tabs are **READ-ONLY**: their
node context menu offers no Add/Edit/Delete/Rename/Move (the geometry is owned by the template + the
declaration), their master-detail panel shows the same read-only notice instead of an editor, and
Rename/Delete tree refuse them — but **Redraw** works normally (instances are fully
placeable trees). An instance tab shows one top **"⇐ instance of {template} (sheet={sheet})"** item; a
template tree shows one **"→ instance: {name}"** item per instance — double-click navigates either way
(the same navigation primitive as "⇐ embedded in"). **Save never writes generated instances** as literal
`trees:` entries — the untouched `tree_instances:` section regenerates them on every load (no
duplication). Manage the short declarations with **Tools → Trees → Instances…**: pick a template
(generated instances can't themselves be templates) and edit its
{name, sheet, cluster?, rotation?, anchor?} rows — the
Cluster column is OPTIONAL (blank = inherit the template's own cluster unchanged); a non-empty value is
substituted into the generated copies' `cluster` and the role anchor, mirroring `sheet`. The dialog
only persists the declarations, the instances materialize on the next load.

Two more OPTIONAL columns (2026-09-12, plan tree_instance_own_place §И.5) put the instance's OWN place
in the same table:

- **Rotation** — a plain number (degrees), blank = inherit the template's own angle. It is written to
  the declaration as a number and REPLACES the template's angle (see the config docs);
- **Anchor** — a one-line summary of the declaration's own `anchor` (written in the anchor grammar's
  own keywords, e.g. `point p_ch1`, `role AD_DAC / Channel_0`, `origin`, `—` when not set) plus a
  "…" button. The button opens the **same anchor form the tree's own Anchor tab uses**
  (`AnchorFormWidget`), so there is no second anchor editor: every mode (origin / config record /
  external refdes / self / role / point) and its optional `shift` are reachable. Its "Inherit from
  template" button drops the row's own anchor (the key is removed from the declaration, the instance
  goes back to standing where the template's anchor resolves). A blank Rotation cell and an inherited
  anchor are both OMITTED on save — never written as `null`/`""`. The form's tree-settings box and the
  anchor's `shift` row are hidden here (a declaration has no suspension point or angle of its own; an
  existing `shift` is preserved verbatim).

**Instantiate from Cell… (2026-09-03, plan instantiate_from_entity):** add ONE more physical group of
the same kind INTO the current tree without duplicating geometry — pick an EXISTING Cell (e.g.
pif_p2v5_vcca) as the group's internal layout (its components/vias/tracks live in the Cell once; the
new Entity just references it via cell:), name the new Entity for the new cluster (e.g. PIF_1V2_VCCINT)
and address it by Sheet/Cluster. The placement node is offset by xy from the tree's anchor (the tree
must have an anchor set): entered manually, or — the opt-in "take from selection" checkbox — derived
from the current board selection (the geometric center of the selected group's footprints when they all
belong to ONE cluster; several clusters in the selection is an error; no selection → manual). The new
Entity carries no role-pinning (refs): the new cluster's roles resolve at Apply by (Cluster, Sheet).
Entry points: Tools → Trees → "Instantiate from Cell..." and the current tree anchor's context menu.
Everything is staged — nothing reaches disk until File > Save.

The same dialog has a second tab, **Extract new cell from selection** (2026-09-04, plan
instantiate_new_cell_from_selection): when the current board selection holds exactly ONE FULLY
selected cluster (the same detection "Extract cluster..." uses — a partial selection is never
captured silently, cf. plan_2026_09_03_fpga_oscill_missing_copper_and_cell_import.md), the tab lets
you EXTRACT the cluster's layout as a brand-new Cell right here — no separate "Extract cluster..."
round-trip — and add the placement node in one click. The cluster's Cluster/Sheet auto-fill the
addressing; the new Cell name defaults to the cluster slug (editable; a name already in `cfg.cells` is
refused). The Cell's internal geometry origin ("Geometry origin") is either "Relative to zero-slot"
(default — the portable convention, works with any node placement) or "Absolute (selection center),
rotation 0", whose origin is the SAME geometric center the "take from selection" positioning uses, so
the total position reproduces the live one exactly. "Absolute" is only guaranteed together with "take
from selection" (the dialog warns, not blocks — the Cell can be edited by hand later).

By default the zero-slot origin is picked AUTOMATICALLY (the role present exactly once among the
cluster's selected footprints — see `cluster_origin_role`). Since 2026-09-04 (plan
`extract_origin_pad_restore`) you can OPTIONALLY override it by hand with the "Override origin
(Role/Pad)…" checkbox on the same tab (zero-slot mode only — under "Absolute" the override is hidden
because that geometry mode has its own origin). Tick it to reveal the shared Anchor/Role picker
pre-filled with the roles REALLY present in the selected cluster, then pick the role that should land
at the Cell's local (0,0) and optionally narrow it to a specific pad. This restores the pad-origin
picker the retired Extract dock had before Phase F. Unchecked = today's automatic behavior unchanged.

## Detail dock

> **2026-09-05 relayout (plan config_qview_placer_nettrace):** Placer and Net Trace are no
> longer Detail-dock pages — the Config dock is now a master-detail (tree left, context QStack
> right), and Placer / NetTrace are its right QViews, switched by the tree selection. DetailDock
> was removed. Placer's action buttons are **Redraw** and **Select on board** only (Redraw
> dependents / Redraw & Save / Undo were removed); the cell-anchor source is a set of tabs.

Placer/Net traces below all live as tabs inside one shared **Detail** dock,
not as separate docks — switching is both automatic (a Config-tree click routes to the matching tab)
and manual (click the tab bar directly). Chains (2026-09-01, plan rules_to_chains), Extract
(2026-08-31), Thermal via (2026-09-01), Points (2026-09-01, plan
`plan_2026_09_01_points_dialog.md`), Tools (2026-09-01, plan
`plan_2026_09_01_tools_dialog_and_entity_roles.md`), Cells (2026-09-04, plan
`plan_2026_09_04_celldock_to_dialog.md`), and Project + Settings (2026-09-01, plan
`project_settings_dialogs`) are NOT tabs here — they moved to standalone dialogs: see the
[Chains](#chains), [Extract](#extract), [Points](#points), [Tools](#tools), [Cells](#cells) and
[Project](#project) sections, and **Tools → Settings...** for the Settings dialog.
Every
automatic switch also
raises Detail to the front of its own tabified group (it shares screen space with fieldstool) and
updates its window title to name the page and, where there's a single obvious current entity, its
name too — e.g. "Detail — Net traces: GND", or just "Detail — Placer" for pages with no single
current entity (added 2026-08-06, found live — Denis: "неплохо бы подсвечивать, какой док сейчас
активен. А то вообще, не видно, кто и что" — a plain tree click used to switch the tab silently if
Detail wasn't already the visible group).

## Settings

**Settings** (2026-08-15, plan `configurator_panel`; reworked 2026-09-01, plan
`project_settings_dialogs`) is a standalone **modal dialog** — open it from the main menu's **Tools →
Settings...**. It hosts pure GUI/app settings for THIS MACHINE — a GUI facade over
[`gui/settings.py`](gui/settings.py)'s `gui_state.json`, deliberately NOT project config. The
"Project" dialog (RootMetadataDock) edits the project config in the version-controlled project file;
this dialog never touches it. Everything here is local per-machine state (the same storage
`last_root_file`/`window_geometry`/`tree_group_by` already use).

The dialog is a two-pane browser: a **category tree on the left** (General / Appearance / KiCad /
Config tree / Hotkeys / MCP server / Board overlay) and the matching settings page on the right. Settings apply
**explicitly** via the **OK / Apply / Cancel** buttons — a widget change is only a draft until
**Apply** (persists and stays open) or **OK** (persists and closes) commits it; **Cancel** (or the
window X) discards the draft. Side effects (always-on-top flag, tray icon, highlight re-apply,
connection timeout, hotkey rebinding) fire only on Apply/OK.

- **Always on top** / **Tray icon** (the **General** page) — the two checkboxes that used to sit
  directly in the status bar moved to Settings (2026-08-15); the actual window-flag / tray-icon
  LOGIC is unchanged in `MainWindow` (`_set_always_on_top`/`_set_tray_enabled`) — the browser just
  re-emits their toggles from `apply()` and `DockHub` wires them back. The status bar is now the
  status label plus the Reconnect/Open fieldstool/KiCad processes... buttons only.
- **Style** (the **Appearance** page, 2026-09-03, plan `qt_style_setting`) — pick which Qt style
  name renders the whole GUI. **System default** (the first, special entry) means "never call
  `setStyle()`" — today's behaviour, the OS/Qt platform-theme integration untouched. The other
  entries are the styles available on THIS machine/Qt build (`QStyleFactory.keys()`, e.g.
  `Fusion`/`Windows`) — never a hardcoded list, so the set differs per OS. Stored as
  `gui_state.json["qt_style"]` (absent/`None` = System default) and applied BOTH at startup
  (`kicadstamp/gui_main.py`'s `apply_saved_qt_style`) AND live on Apply/OK
  (`QApplication.setStyle()`). Fatal-safe like `window_geometry`/`dock_state`: a stored name that
  does not exist on this machine (e.g. `gui_state.json` synced from another OS) is silently
  ignored and falls back to System default — never breaks. Known Qt limitation (not ours): after a
  style was switched live within one session, switching back to System default cannot reliably
  restore the very first style — the hint under the combo recommends a restart for a fully clean
  result.
- **Color scheme** (the **Appearance** page, inside the **Style** group, 2026-09-03, plan
  `color_scheme_setting`) — the second, independent knob for the same "white-on-white" Linux
  problem: a built-in **QPalette** override applied on top of the style. **None** (the first entry)
  means "no palette override" — the system/theme palette. The only built-in scheme today is **Airy**,
  the exact palette qt6ct's `airy` scheme uses (values embedded in `gui/color_schemes.py` — copied
  verbatim from `/usr/share/qt6ct/colors/airy.conf`, NOT read from disk at runtime, so it works on
  any machine/build; the same "sew into the app" principle as the icon). Stored as
  `gui_state.json["color_scheme"]` (absent/`None` = no override) and applied BOTH at startup
  (`apply_saved_color_scheme`, after the qt_style override) AND live on Apply/OK
  (`QApplication.setPalette()`). Fatal-safe like `qt_style`: a stored name that names no built-in
  scheme (e.g. `gui_state.json` synced from another machine/version) is silently ignored and falls
  back to None — never breaks. Unlike `qt_style`, a switch back to **None** DOES restore the
  original palette cleanly (the pristine palette is snapshotted as the app's `original_palette`
  property at startup, before any override). Custom colors follow a non-native palette most
  faithfully under the Fusion style (informational hint only — the style is never switched
  automatically).
- **Highlight color** (the **Appearance** page) — one highlight scheme applied to ALL THREE
  highlight places: the Detail
  dock's active tab, the Config tree's selected item, and the Components tree's selected item.
  **System palette** uses the OS theme's `palette(highlight)`; **Custom** (via **Pick color...**)
  uses a literal color. Stored as `highlight_mode` (`"system"`/`"custom"`) + `highlight_color`
  (hex) in `gui_state.json`, applied at startup and re-applied live on change. Before this, both
  trees were bare native-styled `QTreeView`s whose selection was barely visible on Windows (the
  same "еле видно" bug found during this discussion).
- **KiCad connection timeout** (the **KiCad** page) — the ONE user-facing timeout (`DEFAULT_TIMEOUT_MS`,
  `kicadstamp/constants.py`), editable in milliseconds. Written straight into
  `connection.timeout_ms`, which `BoardConnection` reads on every connect, so it takes effect on
  the NEXT connection without disturbing an open one. The internal protective timings
  (`_CONNECT_TIMEOUT_GRACE_S`, pynng-safety's `_CLOSE_TIMEOUT_S`, the single-instance ping) are
  deliberately NOT exposed — one of them literally just closed a live GUI freeze (see
  `handoff_2026_08_15_pynng_close_timeout.md`).
- **Hotkeys** (the **Hotkeys** page, 2026-08-30, plan `dock_toolbars_menus_hotkeys` Этап 1) — one
  key-sequence editor per QAction-based hotkey (so far the Project dock's five:
  Open/New/Save/Add.../Remove — see `gui/hotkeys.py`). Rebinding writes
  `gui_state.json["hotkeys"]` as `{action_id: shortcut}` and re-applies to the live action on
  Apply/OK; an empty editor restores the code default. The full list lives in
  [docs/hotkeys.md](hotkeys.md).
- **MCP server** (the **MCP server** page) — the `kicad_raw_move_footprint` raw-write gate the
  headless MCP server reads from `gui_state.json` (see [docs/mcp.md](mcp.md)); the same effect as
  the `KICADSTAMP_MCP_ALLOW_RAW_WRITE=1` env var.
- **Board overlay** (the **Board overlay** page, 2026-09-09, Phase D of
  `plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md`) — the geometry of the
  cell-anchor editor's drawn bbox/marker overlay. **Overlay layer** — the KiCad USER layer the
  overlay is drawn on (default `User.Drawings`; the combo is filled LIVE from the open board's
  enabled user layers via `board_overlay.overlay_layers()` — never a hardcoded list — and shows
  the remembered value when KiCad is not connected). **Bbox line width** / **Marker radius** /
  **Marker line width** are in mm (defaults 0.15 / 0.3 / 0.1). There is deliberately NO colour
  setting: overlay graphics take their LAYER's colour (measured fact §0.6) — the page's hint
  recommends a dedicated user layer (Denis uses `User.KiCadStamp`) so colour/visibility are set
  once in KiCad and whole-layer cleanup is safe. The values persist to `gui_state.json`
  (`overlay_layer`/`overlay_bbox_stroke_mm`/`overlay_marker_radius_mm`/`overlay_marker_stroke_mm`)
  and the drawing reads them via `board_overlay.overlay_*()` — changing a value here changes the
  next drawn overlay. The same page carries the **Remove entire overlay layer** button — the
  guaranteed cleanup that sweeps EVERY graphic shape off the chosen layer (confirmed first), for
  recovering from lost overlay uuids.

## Extract

The GUI's dedicated Extract dock/dialog was removed in Phase F (2026-09-01) — its function is
absorbed by **Tools → Trees → Extract tree...** below (auto-derives `cells:` from the fully-selected
clusters and captures inter-cluster copper as `net_traces:`), and manual `cells:` editing stays in
the standalone Cell dialog (**Tools → Config → Edit Cell...**, or the Config tree's right-click
**Edit cell...** — see the [Cells](#cells) section). `extract_profiles:` remain a CLI/config-only
concept:
`kicadstamp_cli.py extract --profile` (`cli_extract.py`, `extract_writer.py`) still consumes them.

The old **Tools → Re-read selected...** was merged into **Extract tree...**: its dialog lists every
FULLY-selected cluster (all its board components in the selection, matched by Cluster tag + sheet)
with its Entity and Cell, checkboxes on by default; on OK each checked cluster is re-captured — its
current positions are re-captured into the cell using the matching extract_profiles recipe
(params/origin/net_template_role/rule_nets) when one exists, else auto. Foreign copper swept in by
the area-select is dropped by the extractor's connectivity filter (the cluster's OWN applied copper
is kept — no registry dependency). This is how the three PIF_AVDD channels (Channel_0/1/2, same
cell) are told apart: the selection's sheet picks the instance.

A cluster counts as **fully selected** only when ALL four conditions hold: every selected footprint
carries a Cluster tag; its sheet resolves; the (Cluster, sheet) pair exists in the board snapshot; and
EVERY board component of that pair is in the selection. When nothing qualifies, the message names the
real cause(s) instead of always telling you to select more — one line per dropped group: the untagged
footprints, a sheet that did not resolve, an unknown (Cluster, sheet) pair, or "selected N of M,
missing: R...". The most common cause is a config WITHOUT `schematic_dir` (`schematic_dir:` /
`schematic_files:` at the project root): without it sheet names cannot be resolved anywhere, so a
Cluster placed once per channel cannot be told apart and sheet-based narrowing is disabled
project-wide — the message says so explicitly, and the same fact is logged ONCE when such a config is
loaded with a live board. On-screen Ref lists are capped (the full list goes to the Log). The same
concrete cause is shown on **Instantiate from Cell...** tab 2's strict gate.

The main menu's **Tools → Trees → Extract tree...** (2026-09-01) builds a NEW tree from the current
selection — there is no "extract into tree" (the extract never writes `trees:`); this is the
selection's own tree. Select a group of clusters on the board, then run it. The fully-selected-cluster
detection and the geometry payloads are built from a board snapshot REBUILT on the worker thread first
(R.2.2, plan_2026_09_11_stale_snapshot_positions.md — one shared point, also used by "Extract
cluster..."): a component added to a cluster in KiCad after connecting is honoured, so a selection that
merely LOOKED complete no longer extracts a Cell missing it, and the recorded positions are current.
A modal dialog has three
tabs: **Clusters** (the FULLY-selected clusters, checkboxes on by default, with a live ΔX/ΔY offset
preview once an anchor is chosen), **Anchor** (a root-cluster combo that prefills Sheet/Cluster/Role
from the cluster's own Entity — the "existing cluster anchor" — or explicit Sheet/Cluster/Role/Pad
narrowing into a `TreeAnchor`), and **Tracks and vias between clusters** (inter-node copper — the
selected copper BETWEEN PADS whose pads belong to 2+ of the checked clusters — with one row per UNIT
of copper and a "select all / deselect all" master; each row is labelled with the net and the nodes it
connects, so two bridges of one net are two visibly different rows). The rule is STRICT and geometric
(2026-09-12, plan `plan_2026_09_12_internode_copper_core`; design §4): no net-name exception (a GND
bridge between two nodes IS offered) and no "ubiquitous rail" threshold — a piece of copper is judged
by the pads it actually reaches, and a ZONE is never part of it (a pour connects by overlap, not by
routing, so it would fuse every cluster into one unit). On OK each checked cluster becomes a top-level
`kind="placement"` node with
`ref` = its Entity's name and, when the anchor's live rotation is resolved, `xy` = the offset in the
anchor's LOCAL frame and `rotation` = the Entity's own angle relative to the anchor — (live mount
angle − baked mount angle) − anchor angle (autopositioning, like "Reread current position": the tree
freezes the current geometry relative to the anchor even when the anchor is not at 0°, so a redraw no
longer double-rotates the node; without a live read the node is saved without `xy` and positions are
read live at apply). That live Entity position is measured at the cell's **MOUNT A** — the point the
tree node and the materializer put on the target position (`cell_mount_offset`: `anchor_xy`, else the
`anchor_role` component's centre, else the stored (0,0)), read through the same live-cluster frame the
per-node "Read current position" uses (2026-09-11, plan_2026_09_11_entity_live_position_mount_point) —
never at a component that merely happens to sit at the cell's stored (0,0). The checked UNITS are
captured as `net_traces:` records alongside (a `name` identity, its `pads`, and a `(role, pad)`
reference per element — the same capture the re-read uses, so a created and a re-read tree hold the
same copper), and each becomes a top-level `kind="net_trace"` node whose `ref` IS the record's
identity. The new tree
is written into the root config's `trees:` section through the same `config_writer` chokepoint
(backup + round-trip `link_trees` check); TreesDock and the Config tree refresh immediately. Rows
whose cluster has no Entity or a missing cell are marked and block OK; an empty/duplicate tree name
or a missing Role also blocks OK.

A cluster whose (role, sheet, cluster) identity equals the chosen EXPLICIT role anchor is the tree's
OWN anchor subject. REWRITTEN 2026-09-11 (plan `tree_self_anchor`, task Д.5, replacing the 2026-09-08
auto-root reparenting of plan `extract_tree_self_anchor_as_auto_root`): instead of dropping it or making
it the sole top-level auto root, "Extract tree" NAMES it as the tree's own self anchor —
`(anchor (self (ref "<entity>") [(pad ...)]))` — and it becomes an ordinary TOP-LEVEL node at
`xy=(0,0)`, `rotation=0` (it is the point its siblings are measured from). Every other checked cluster
AND every checked inter-cluster `net_trace` stays TOP-LEVEL (no reparenting), which the self anchor
allows because it NAMES its subject; all numeric offsets are unchanged (the anchor base already equalled
the subject's own live position by definition of the match, and a zero-offset root and a top-level node
compute identical children). An `anchor_pad` on the matched anchor is NO LONGER skipped — it is carried
onto the self anchor as `(self (ref ...) (pad ...))`, the cluster is included and no warning is
returned. More than one checked cluster matching the anchor is still a config conflict and blocks the
build. A self anchor is never treated as a duplicate (its subject node is the anchor source by
construction). A self-anchor duplicate that already exists in a
tree (hand-made, or from an older extract before this rule — e.g. `conn_pm5v_power` under the
CONN_PM5V anchor of the "power" tree) is highlighted in the Trees dock with a neutral background +
tooltip ("this node duplicates the tree's own anchor — safe to delete"), so it is visible without
waiting for the redraw drift on the live board.

**Tools → Trees → Reread inter-node copper** (2026-09-12, plan
`plan_2026_09_12_internode_copper_core`) re-reads the CURRENT tree's copper between pads from the live
board — for the very case the old flow could not handle at all: the copper was captured ONCE, when the
tree was built, and re-reading it meant rebuilding the tree and losing the placement. It matches fresh
copper to the stored `net_traces:` records by their **pad set** (never by geometry — that is what is
being refreshed), ADDS the units it finds that have no record yet (new top-level `kind="net_trace"`
nodes, `ref` = the new record's identity) and refreshes the geometry of the ones it does. It NEVER
deletes: a record whose copper is no longer on the board is reported in the Log and its node is marked
"no copper" in the tree (a session mark, computed on the fly, nothing written to the config) — removing
it stays a human decision, because "the copper is gone" cannot be told apart from "I pulled it out for a
minute". There is NO dialog at all: the outcome is a list in the Log, the edits land in the working set
immediately and **File → Save** persists them (the tree section and the touched records each into the
file that already owns them, so an included `net_traces:` section never migrates into the root). The
board read runs on a worker, and the AREA is the current board SELECTION when there is one, else the
WHOLE board — the tree already knows its nodes, clusters and pads, so no selection is needed for the
common case.

**Tools → Trees → Whose copper is this?** (2026-09-12, plan
`plan_2026_09_12_select_copper_by_record`) answers the reverse question: which `net_traces:` records own
the copper you have SELECTED on the live board. It is READ-ONLY — it reads your selection and never
changes it (answering a question by destroying the question would be wrong) — runs on a worker, and
reports in the Log, never a dialog. The search has two tiers, and the Log names the one that identified
each piece: **by registry** first (the registry stores the uuid of every piece KiCadStamp placed, so the
answer is exact and needs no anchor), then **by geometry** (the same `track_matches`/`via_matches`
predicates `apply` uses) for hand-drawn copper the registry has not adopted yet — "by geometry" therefore
means "apply has not seen this copper yet". Copper belonging to no `net_traces:` record is reported
separately: that is a USEFUL answer, not an error — it means the copper is free to capture. Copper owned
by another mechanism (a rule/cell) is named as such, and a registry identity with no record in the config
is surfaced rather than invented. When an identified record is a tree node, that NODE is selected in the
Trees dock (the copper selection is left alone). If nothing is selected, the Log says so and nothing else
happens.

The other direction — highlight a record's copper — is the contextual **Select copper on board** on a
`kind="net_trace"` node, and the NetTraceDock's **Select on board** button (see the Net traces section):
READ-ONLY (selection is editor UI state, not a board edit). It reports how many of the record's expected
pieces were found, which tier found them, how many are missing, the reason when the anchor does not
resolve, and an explicit note that the previous selection was replaced — "not found" is a normal answer
with its reason, not an error.

The main menu's **Tools → Trees → Extract cluster...** (2026-09-03) is the NARROWER sibling of
**Extract tree...** for the case where you want ONLY one fully-selected Cluster as a standalone, flat
Entity (+ its Cell generated from the cluster's own selection when it doesn't exist yet) — with NO
tree node, NO anchor and NO `net_traces:` capture. It uses the SAME cluster detection as **Extract
tree...** (a fully-selected Cluster = all its board components in the selection, matched by Cluster
tag + sheet), then shows a small single-cluster modal dialog: pick the Cluster, and the Entity name
is prefilled — auto-derived when new (editable, with the usual duplicate-name validation), read-only
when an Entity for this Cluster + sheet already exists (reused, never duplicated). On OK the Entity
is written to the root config's `entities:` (and the new Cell to `cells:`) through the same
`config_writer` chokepoint; the Config tree refreshes immediately. An Entity stores no position, so
placing this one (a manual tree node, a `tree_instances:` template, ...) is a separate, later step —
"Extract cluster..." never writes a position. The same flat extraction is also one click away under
**Tools → Config → Extract cluster (by selection)** (2026-09-06) — the identical DockHub delegate,
a second independent menu entry next to "Edit Cell..."; the Tools → Trees entry stays as-is.

Just like the Instantiate-from-Cell tab above, the new Cell's origin is picked AUTOMATICALLY by
default (the cluster role present exactly once). Since 2026-09-04 (plan `extract_origin_pad_restore`)
the same dialog offers an OPTIONAL "Override origin (Role/Pad)…" checkbox to override it by hand:
the Role picker is pre-filled with the roles of the currently selected Cluster, and an optional Pad
narrows the origin to that specific pad — the same pad-origin picker the retired Extract dock had.
Unchecked = today's automatic behavior unchanged.

The main menu's **Tools → Place thermal vias...** (2026-09-01) and the Config tree context menu's
"Add thermal via pad..." (plus a click on a `thermal_via_arrays` leaf) open the standalone
(non-modal) **Thermal via** dialog — a fresh blank form for "Add ...", the entry pre-loaded for a
leaf click. It hosts the same form as the old Detail-dock page: pick the anchor (ref/role + cluster,
or a named Point), pad, net and grid geometry, then **Redraw** to place the vias on the live board
(ApplyPipeline `only=[name]` — the dialog stays open for iterative tuning) and **Save** to write
the `thermal_via_arrays:` entry (auto-closes the dialog on success).

**Origin**/**Net aliases**/**Net template role**/**Sub-placements**/**Existing** below live in a
tab widget (2026-08-04: previously stacked in one long column, whose minimum height was the SUM of
every section's own — the dock couldn't shrink below that even when most of it didn't apply right
now). A `QTabWidget` only sizes for the current page, so the dock resizes freely; **Net template
role**'s and **Sub-placements**' tabs are hidden outright (not just their content) until they
actually apply. Since 2026-08-31 nets are auto-derived at extract (a role's `net_template` is
written from its own non-rule pad nets — see below), so the **Net aliases**, **Net template role**
and **Existing** tabs are hidden by default too (profiles/cells are picked from the Config tree);
the **Show advanced net settings** checkbox reveals them for manual overrides.

- **Write target** — a successful extraction writes the Cell into the project root file's `cells:`
  section (and, with "Also save as extract_profile", the profile recipe into that same file's
  `extract_profiles:` section). The former Cell-file/Profile-file/Placer-file dropdowns are gone
  (2026-08-21): everything the Extract dock produces now lands in the one root file.
- **Cell name** — defaults to the current selection's Cluster, slugified (`PWR/DAC0` →
  `pwr_dac0`), if nothing's been extracted from this Cluster before; if an existing Cells/
  Extractor key already matches, that wins instead. Never overwrites something you've typed.
- **Origin** — Bounding box (default, lower-left corner of the selection) / Component role (+
  optional pad, and — 2026-08-31 — optional **Cluster**/ **Sheet** to narrow an ambiguous role
  that lives in several Clusters/Channels) / Via net.
- **Net aliases** — a `QTableWidget` (2026-08-06, previously a hand-rolled grid — Denis: "у нас в
  экстракторе net-aliases, не таблица"), one row per net found on the selected components' pads.
  Rows themselves aren't user-added/removed — the net set is dictated entirely by the current board
  selection and rebuilt on every selection-watch tick; only the **Alias** and **Chain net** cells
  within each row are editable. A non-empty alias becomes a `{PLACEHOLDER}` in the written Cell
  (feeds `params:` for round-trip resolution — see [docs/config.md](config.md) on
  `net_template`/`params`). Each row also has a **"Chain net (null)"** checkbox (2026-08-05), mutually
  exclusive with the alias field — checking it writes that net's via/track as `net: null` instead, so
  a cell placed via `chains:`/ManualSpoke inherits whichever Chain's own net it's placed under (see
  [docs/config.md](config.md) on `rule_nets:`) — the mechanism for reusing the SAME cell across
  several Rules on different power rails, which `{PLACEHOLDER}` aliasing can't do here (ManualSpoke
  has no `params:` to resolve a template against).
- **Net template role** — appears only when a component's pads touch **2 or more distinct non-rule
  nets** (a bridging part — inductor, ferrite bead, fuse spanning two rails). Since 2026-08-31 the
  role's `net_template` is auto-derived — the first (by sort) net that is aliased (parametrized),
  or the first non-rule net as a literal otherwise — so extraction is no longer blocked by default
  and this tab is hidden; it only appears (when the advanced setting is on) so you can override the
  auto-default with a manual pick.
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
  Since 2026-08-31 (plan placer_source_tab_gaps P.1) it ALSO auto-fills from the CURRENT board
  selection, like ExtractDock does for Cell names: select a whole Cluster's components on the live
  board and its name fills into this field — only in Cell mode, only while the field is blank and
  not user-owned (never overwriting a typed/picked value), and then silently triggers the
  Nets/Params auto-fill pipeline.
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
    - **Params** — one row per `{PLACEHOLDER}` found anywhere in the picked Cell's own config (auto-
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
      any row. A Params row that stays blank may be a deliberate limitation, not a stale auto-fill
      (2026-08-31, plan placer_source_tab_gaps P.3): a placeholder is only narrowable through a role
      whose `net_template:` is EXACTLY `{KEY}` — a compound template like `/…/…/+3V3` can't be
      reverse-mapped to one net, so that field shows a tooltip explaining it must be picked by hand.
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
  - **Read current position** — fills the current live origin/rotation. Since 2026-08-31 (plan
    placer_source_tab_gaps P.2), if the Anchor/Point identity fields are filled but the mode combo
    is still on the default Absolute (XY), the mode is auto-switched to the filled Anchor/Point set
    (silently, no dialog) so the read expresses the origin as the SHIFT from that anchor instead of
    silently writing absolute coordinates.
- **Cell anchor** — REMOVED from the Placer (Фаза B of
  `plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md`, 2026-09-10). It recorded the
  cell's mount point by COMPUTING a numeric `anchor_xy` at save time — the mechanism §0.1 of that
  plan established as wrong (a Role+Pad anchor is a REFERENCE resolved at apply time, not a number).
  The anchor is now edited in the Config tree's dedicated **Cell anchor...** page (see the
  "Cell anchor..." section below); this Placer form only ever positions THIS placement (Origin page).
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
- **Save** — separately, writes the current form into the project root file's `clone_placements:` list
  (replacing an existing entry of the same name, never duplicating). Redraw does **not** save by
  itself — look, adjust, Redraw again, and only Save once you're happy with the result.
- **Select on board** (2026-08-25) — resolves the current form's placement to its live board items
  (its components plus every via/track the registry records under this placement's anchor) and
  highlights exactly those in pcbnew — a visual check of what this placement really owns, without
  moving anything. Nothing found (not placed yet) is a short Log message, never a crash.
Not covered by the GUI yet (still reachable by hand-editing the saved config): `by_selection` mode.
`anchor_sheet` narrowing WAS in this deferred list — closed 2026-08-15: every Sheet field is now a
searchable combo sourced from the project's schematic files (see
plan_2026_08_15_sheet_combo_everywhere.md), including ClonePlacement's Origin tab and
ThermalViaArrayConfig's anchor (both had the field in the model, only the form never reached it).

## Entity mode (PlacerDock, 2026-08-30)

The Entity/Placement split (phase 5.2) added an **Entity** source to the Placer dock. Picking an
Entity (from the whole `include:` graph) loads the `entities:` record into the form — **Source**
edits the "what" (Cell/Nets/Params/Cluster/Sheet/... — electrical and identity fields; there is NO
position section here, an Entity never carries `xy`/anchor/rotation), and the **Origin** tab edits
the position, which is written into the `trees:` node that places this Entity.

- **Save (Entity)** — validates and writes the `entities:` record through `upsert_entity` into the
  file the Entity actually lives in (an Entity in an included file is updated in place, never
  duplicated into the root). The merge preserves the electrical fields (`nets`/`net_overrides`/
  `refs`) even when the form cleared them (2026-08-30 merge-preserve fix).
- **Origin (Entity)** — the same position widget as the ClonePlacement path (Absolute XY / Anchor /
  Point). Saving calls `upsert_entity_placement` (`kicadstamp/config_writer.py`): it finds the tree
  whose anchor matches the picked origin (or creates a single-node tree named after the Entity) and
  writes/updates the `kind "placement"` node whose `ref` is the Entity name. A status label reports
  "Placed under tree …" or "Not placed — set an origin to place it." (no tree node yet = legitimately
  not placed).
- **Tabs** — in Entity mode the Placer's Nets/Net overrides/Refs tabs are hidden (they moved to the
  **Tools** dialog, next section); the legacy Cell/ClonePlacement mode keeps them.

## Tools (Nets / Net overrides / Refs, 2026-08-30)

Phase 5.3 moved the Entity's three electrical editors OUT of the Placer dock into the **Tools** form
(gui/docks/tools.py). Since 2026-09-01 (plan `plan_2026_09_01_tools_dialog_and_entity_roles.md`)
this is a standalone **non-modal dialog** (not a Detail-dock page): open it from the main menu's
**Tools → Edit template...**, or by a **double click** on an **Entities** leaf in the Config tree
(which loads that Entity into the form — a single click on an Entities leaf keeps switching the
Placer into Entity mode). The dialog auto-closes after a successful edit.

- **Nets** — `role → net` (Params stays in the Placer's Source tab — both feed the same by-nets
  role resolution).
- **Net overrides** — `resolved net → override`.
- **Refs** — `role → explicit refdes`.

The dialog is Entity-targeted exactly like the Placer's Entity mode: pick an Entity (graph-wide),
edit the three dicts, each row write validates through `load_entity` and writes back via the same
merge-safe `upsert_entity` into the Entity's own file — so the Tools dialog and the Placer's
Source/Origin edit the same record without clobbering each other. Since 2026-09-01 the **Role**
combos are scoped to the picked Entity's OWN cell components (`entity.cell` →
`cell.components[].role`, the same chain as PlacerDock's Cell mode) and the **Net/Override** combos
are fed from the live board's net names (`refresh_known_nets`, the same ~2s poll as the Placer's own
tabs) — previously these combos were empty free text.

## Project

**Project** (2026-09-01, plan `project_settings_dialogs`) is a standalone **non-modal dialog** —
open it from the main menu's **File → Project...**. It hosts the whole RootMetadataDock widget
(displayed "Project" since 2026-08-05, Denis: "давай не root, а project"; the panel underneath is
still called RootMetadataDock in code, since it edits the project's ROOT config file, same concept
the Config tree's "Open Root file..." uses). Open/New/Recent moved INTO this dialog too (2026-09-01)
— the File menu now only has **Project...**, Save, Discard, Close, Quit; the `Ctrl+O`/`Ctrl+N`
hotkeys stay app-wide regardless of the dialog's visibility.

Edits the project's root-config-only scalar keys: Layer/Place components/Skip existing components,
the **KiCad project** (`*.kicad_pro`) picker and the read-only schematic-sheets list (all shown
above the tabs, as general project settings), then a single **Via** tab (the four
`via_search_*`/`via_keepout_clearance_mm` fields).

The **Files** tab (registry_path/track_registry_path/log_file/operation_log_dir) was removed
2026-09-11: all four have a computed default next to the config file and every consumer creates the
target on demand, so the tab was redundant. The keys stay valid in the config file — the dock just
no longer writes them.

The **Schematics** tab was replaced the same day by the KiCad project picker: `root_sheet` is
derived from the picked `*.kicad_pro` (same directory/basename, extension `.kicad_sch`), and the
**Reload schematic sheets** button walks that hierarchy and REPLACES `schematic_files` with the
reachable files (relative to the config), also clearing `schematic_dir`. Nothing is recomputed
automatically — only the button does it.

Always targets the project's single root file — the one opened via "Open Root file..."/"New Root
file..."/the Recent dropdown inside this dialog — regardless of which included file is currently
browsed in the Config tree. Browsing into an included file does not retarget this panel: these
fields are only valid on an actual root (an included file setting any of them is fatal at load — see
[docs/config.md](config.md)), and a project only ever has one.

Since 2026-09-04 (plan `root_metadata_path_defaults`), the four path fields
`registry_path`/`track_registry_path`/`log_file`/`operation_log_dir` show their COMPUTED default
(for the currently open root file) as a grey placeholder while left empty — nothing is written to
the config unless you actually type a value. Those defaults now point into SUBFOLDERS next to the
config instead of beside it: `registry/<config-stem>.registry.json`, `tracks/<config-stem>.tracks.
registry.json`, `logs/actions.log`, and `operational/` for the `operation_*.json` undo logs. Config
backups made on Save live in a hidden `.history/` next to the root config.

## Points

Edits a named `points:` entry (see [docs/config.md](config.md) on the Point schema) — a reusable
anchor other `anchor_point:` references (Placer's own Point origin mode, Chain/ThermalViaArrayConfig)
point at by name. Added 2026-08-05 after noticing how closely Point's own shape already matches
Placer's Origin widget.

Since 2026-09-01 (plan `plan_2026_09_01_points_dialog.md`) this is a standalone **non-modal dialog**
(not a Detail-dock page): open it from the main menu's **Tools → Add point...**, from the Config
tree context menu's "Add point...", or by a **double click** on a `points:` leaf in the Config tree
(which loads that point into the form — a single click on a points leaf does nothing). The dialog
hosts the same live form as the old Detail-dock page; it auto-closes after a successful Save.

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
- **Point circles** (2026-09-11, plan `plan_2026_09_11_points_markers.md`) — Resolve ALSO draws the
  point as a marker circle on the overlay user layer (the **Settings → Board overlay** layer,
  `User.Drawings` by default; the colour is the LAYER's), so a bare xy point — the one case with no
  footprint to highlight — becomes visible on the board. The circles belong to the SAME keyed owner
  as the cell-anchor overlay (`gui/overlay_markers.py`, key `point/<name>`): resolving the same point
  again MOVES its one circle instead of stacking a second one. **Show all points** is the single
  toggle for the whole flat list — it draws a circle for every point that resolves (a point that does
  not resolve is skipped with a Log line naming it, never cancelling the rest) and, on the second
  press (the label then reads **Hide all points**), drops the whole `point` namespace; the label
  always follows what the map actually owns, never a separate flag. Without a live board the button
  only writes a Log line — no dialog. Circles are pure visualisation: nothing is written to the
  config, renaming a point here (or switching the project root) removes its circle, and a circle left
  behind by a point deleted in the Config tree is an orphan the owner's reconcile reports on the next
  connect.
- **Read from board** (2026-09-12, plan `plan_2026_09_12_point_read_from_marker.md`) — a circle is a
  real KiCad graphic, so it can be **dragged with the mouse** in the PCB editor: move it where the
  point belongs, press **Read from board**, and the form is filled from the circle's new centre (read
  back through the same keyed owner the cell-anchor editor's own "Read position" uses). WHERE the
  number goes depends on what the point IS, never on the panel's own mode: a literal-xy point gets its
  **xy replaced** (the dragged position IS the new literal), an anchored point (Anchor/Point/Board
  origin) gets its **Shift X/Y recomputed from the base** — `dragged − (resolved − old shift)` — so
  reading twice never makes the point creep, and a shift is never written on top of an `xy` (that
  combination is fatal in the schema). The button only FILLS THE FORM: nothing reaches the config
  until the usual Save, exactly like every other field here. No circle for this point yet, or the one
  on the board was deleted in KiCad → one Log line telling you to Resolve first (the read never draws
  a circle silently — you must see where the numbers came from); no live board → one Log line, no
  dialog. The read goes to the board on a worker, never on the UI thread.
- **Save** — writes into the project root file's `points:` section (a dict keyed by name, unlike
  Placer/Thermal via's list-of-dicts sections — an existing name is replaced in place, not
  duplicated).

## Chains

Edits a `chains:` entry (see [docs/config.md](config.md) on Chain/ManualSpoke) — one shared anchor
(no `xy` mode here, unlike Points/Placer — only **Anchor (ref/role, + Sheet/Cluster)** or **Point**;
Sheet here is the same searchable combo autocompleted from the project's schematic files, not a
whitelist) plus an ORDERED list of spokes, each placing a Cell at a specific pad of that anchor
with its own hand-tuned shift/rotation. Added 2026-08-05 (as "Rules") after Denis connected
`fpga_spokes.sexp`/`fpga_cap_pair_spoke.sexp` to a real project and hit the long-standing "Rules
has no edit form" gap.

2026-09-05 (design config_qview_chain_entity_pages): the Chain editor is now a page of the Config
dock's right QView (it used to be a standalone ChainDialog — removed; before that a Detail-dock
page). The pads are leaves in the tree (category → anchor → chain → pad). One widget, TWO modes:
- **Chain mode** — Net/Name/Comment + **Origin** (anchor-mode combo + Sheet/Cluster, or Point) +
  **Retired**/**Skip**. Reachable by double-clicking a chains: chain node (the chains-nav QView
  also shows the chain's pad list on a single click).
- **Pad mode** — one spoke's fields: **Pad**/**Cell**/**Cluster**/**Mode** (Cartesian/Polar)/
  **Shift X,Y** (or **Radius+Angle** in Polar)/**Rotation**/**Retired**/**Skip**, plus the page's
  **Apply** (commit the spoke) and **Redraw** (apply the current spoke to the board) buttons. A
  SINGLE click on a chains: pad leaf opens it; **Add spoke...** (context menu / Tools menu) opens
  it blank to append a pad to the selected chain.

The pads' ORDER is still semantically significant (the component pool consumes a chain's spokes in
list order), but the tree shows them sorted by pad number — editing a pad rewrites its whole parent
chain via `upsert_list_entry` (a pad is not a standalone record).

- **Cell** (per spoke) is a searchable combo listing every `cells:` key reachable from the
  project's root via `include:` — not just this file's own, since a spoke's cell routinely lives in
  a different file than the chain using it. Both the Cell combo and the Point anchor are populated
  from the whole include graph (root path), wired the same way Project's own panel does.
- **Redraw chain** (context menu on a chain node) — the whole chain, all non-skipped spokes, same
  replace-by-identity + `ApplyPipeline(only=[...])` shape as Thermal via's own Redraw.
- **Redraw spoke** (context menu on a pad leaf) — redraws exactly one pad, but the FULL chain (all
  spokes) goes to the pipeline with the isolation expressed as a per-run `isolate_spokes` map
  (chain name -> the selected pad), never written back — Save is unaffected. Spoke resolution
  shares ONE component pool per net across the whole chain, so every non-redrawn sibling still
  RESERVES its own components in full-chain order (not placed, but not stealable either): the
  redrawn spoke keeps exactly the components a full chain redraw would assign to it and never
  drags a neighbour's component (fix 2026-09-05 — previously the siblings got a temporary
  `skip: true`, `drop_inactive_items` removed them from the chain and the isolated spoke silently
  popped the neighbour's first natural-order component). Config-authored `skip: true` on a spoke is
  unaffected (it still frees that spoke's share of the pool).
- **Redraw chains...** (context menu on an ANCHOR node, 2026-09-01, Denis: "если корневой
  компонент, то вообще все его спицы") — redraws EVERY chain under that anchor in ONE
  `ApplyPipeline` run.
- **Auto-save** — the chain's own Net/Name/Origin/Retired/Skip fields and every pad-mode field
  write on their commit points (2026-09-01, plan project_save_model): blur/Enter/combo pick
  persists immediately. It writes the whole chain into its file's `chains:` list, matched by name
  if set, else net (`chains:` is the one list section without a required `name:` — see
  [docs/config.md](config.md)'s `chain_effective_name`). A failed write is reported in the Log
  dock, never silent.
- **Bulk set Cell for net...** (context menu, 2026-08-20) — sets `cell:` on EVERY spoke of every
  chain on the chosen net at once, even when those chains live in different included files. A
  dialog previews the exact chains/pads that will change BEFORE applying; a partial write failure
  is reported explicitly — never a silent half-applied change.

**Tools menu** (2026-09-01, plan rules_to_chains) — chains are labelled by their NET identity
(Denis's decision; inside the code it is still `Chain`):
- **Add net...** — opens the Chain dialog in chain mode with a fresh blank form.
- **Add spoke...** — opens it in pad mode, appending to the chain currently SELECTED in the Config
  tree (a message in the Log dock asks to pick one first if none is selected).
- **Delete net...** — deletes the currently SELECTED chain from its file (timestamped backup).

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
 machine-written and shown read-only by design — edit it by re-extracting, not by hand (same chain
 as `cells:`).
- **Save** — edits the controllable fields (net/anchor/retired/skip) of an already-saved record and
 PRESERVES its machine-written geometry (a Save must never silently erase `tracks:`/`vias:`).
- **Redraw** — `apply --only=<net>` for the loaded record (same `ApplyPipeline` mechanism RuleDock's
  Redraw uses), so a moved anchor re-places the captured copper live.
- **Select on board** — highlights the loaded record's live copper on the board (READ-ONLY: selection is
  editor UI state, not a board edit). It acts on the record the Config tree last OPENED (its identity),
  falling back to the form's net only when it is unique; Extract clears that remembered identity, because
  a fresh record is unnamed. The outcome goes to the Log: how many pieces of the expected ones were
  selected, the tier that found them, and a note that the previous selection was replaced.
- Clicking a `net_traces:` leaf in the Config tree loads that record into the form.

## Scheme Lists

The Scheme List feature records a real, already-routed board region as a NAMED snapshot — an explicit
list of literal refdes plus the copper that reaches their pads (see [docs/config.md](config.md)'s
`scheme_lists:` section). Design and plans:
`techdocs/handoff/deepseek/design_2026_09_05_scheme_list.md`,
`techdocs/handoff/deepseek/plan_2026_09_05_scheme_list.md` (§5-§7) and
`techdocs/handoff/deepseek/plan_2026_09_06_scheme_list_sheet_capture.md` (5a-5c). Added 2026-09-06.
Recording/re-syncing a snapshot never touches the live board — only a tree node's Redraw does. The
Config side lives on two pages of the Config dock's right QView: the read-only **record page** (record
+ Reread) and the separate **"Place Scheme List"** page that turns one record into a tree Entity.

### Recording a record — Tools → Scheme Lists → Record...

Captures a named Scheme List from the live board through a **three-tab** dialog:

- **"By sheet" (primary, the default tab)** — the WHOLE live hierarchy as ONE tree (QTreeWidget,
  Commit E): every real sheet is a checkable node — the top sheets first (Channel_0/1/2, FPGA, MCU,
  Power…), nested under their parents down to the deepest sheets. Container sheets with no footprints
  of their own (e.g. Channel_0 grouping DAC/OpAmp) are included, so the tree mirrors the schematic
  instead of a flat list of leaf paths. ALL sheets start UNCHECKED — you tick what to record. A
  node's checkbox is a BRANCH toggle (Commit D): checking a sheet turns on its whole subtree (tick
  Channel_0 → DAC/OpAmp follow), unchecking excludes the whole subtree; a parent with a MIXED subtree
  shows a partial checkbox and is itself still read (its own DIRECT footprints stay in the capture —
  only the unchecked nodes are excluded). Container paths are stored in the scope too (they add no
  refs today but keep a future Reread aware of the branch). Every node's tooltip shows its full
  "/"-joined path. The captured refs are the union of the DIRECT footprints of every sheet that is not
  unchecked. The record's frame is the captured region's CENTRE and its pivot is chosen on the dialog's
  **Pivot / Anchor** tab (below), defaulting to that centre (design_2026_09_07_scheme_list_pivot.md).
- **"By selection" (secondary)** — the pre-existing mode: the refs are the CURRENT board selection
  (shown read-only with a count), kept for irregular regions that do not line up with sheet boundaries.
- **"Pivot / Anchor" (third tab, Commit F)** — the record's pivot set AT CREATION: an x/y pair in the
  record's CENTRE-frame (mm offsets from the region centre, `(0, 0)` = the centre), defaulting to
  `(0,0)`. **Centre** writes 0/0 into the fields; **"Take from selection"** reads the CURRENT live
  board selection AT CLICK TIME (Commit G — the selection is polled live even while the modal dialog is
  open, so a component selected on the board after Record was launched is honoured) and fills x/y as
  the pivot in the centre-frame of the refs the ACTIVE source tab would record (selected component
  centre minus the recorded region's centre; needs a live KiCad connection). The recorded region's
  centre is read from the polled full-board snapshot, never from a blocking board IPC on the shared
  KiCad socket (Commit H). That snapshot, in turn, is REBUILT FIRST — on the worker thread, inside the
  same exclusive-socket long op every other live read uses — so the click cannot compute from
  coordinates frozen at connect/manual-refresh time (R.2.1,
  plan_2026_09_11_stale_snapshot_positions.md); a connection without a live board behind it keeps the
  cached snapshot, exactly as before. The dialog OK
  (Record/Re-source) STORES these fields as the new record's pivot — there is no separate Apply in the
  dialog, and OK stays disabled while the x/y fields do not hold numbers. This third tab is NOT a
  source: visiting it never changes the chosen source ("By sheet"/"By selection"), and pressing OK
  straight from it records from the source that was active before the visit
  (plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md).

Both tabs end the same way: a unique record name, a duplicate pre-check BEFORE the expensive capture
(a duplicate name, or a ref already recorded in ANOTHER Scheme List), a worker-thread capture (never
blocking the UI) and — when the connectivity closure dropped copper that reached only excluded
footprints — a boundary-net dialog (v1: each such net is excluded as a whole connected component; the
dialog shows which outside footprint dragged each net). The new record is written to the fixed
`scheme_lists.sexp` next to the profile (auto-`include:`d on first use; a profile that already has
the legacy `scheme_lists.json` writes there instead). A **"By sheet"** record
additionally stores the CHECKED leaf paths as its `scope_sheet_paths` — the source a later Reread
recomputes the current scope from; a **"By selection"** record stores no scope. Nothing is applied to
the board. The **"By sheet"** tab also has an OPTIONAL **"Save as preset"** field: a non-empty name
stores the current checklist as a NAMED preset IN the record's `scope_presets` library (the same name
overwrites it), so a later Reread can switch back to that variant without re-recording (design §9 п.12).

### Re-sourcing a record — Tools → Scheme Lists → Re-source...

Re-sources an EXISTING record from a DIFFERENT source under the SAME name. Reached from the record the
user right-clicked in the Config tree (context-menu **Re-source...**) or the one currently SELECTED
there (Tools → Scheme Lists → **Re-source...**; with no selection the Tools action warns). The dialog
is the SAME three-tab Record dialog with the name pinned read-only, an explicit in-dialog warning that
the record's refs/geometry are REPLACED (every Entity already placed from the record picks up the new
geometry on its next Apply/Redraw — the sheet it currently comes from does NOT update by itself; Place
onto it too if it should follow) and an OK button labeled **Re-source**. Its **Pivot / Anchor** tab is
PREFILLED from the record's stored `pivot`, so re-sourcing a record with a saved pivot keeps that pivot
unless the user changes it (re-sourcing changes the geometry source, it does not reset the pivot to the
centre). The rewritten record stays in the file that already owns it. Re-source keeps the record's
existing `scope_presets` library INTACT: an empty "Save as preset" field leaves it untouched; a
non-empty one overwrites only the same-name preset with the current checklist.

### Rereading a record — Reread

For the record currently loaded in the record page; three entry points (the page's **Reread** button /
the record's context-menu **Reread...** / Tools → Scheme Lists → **Reread...** — the latter two load
the selected record and run the same flow). Reread compares the record against the live board AND can
now change the record's REF SET itself, not only diff fixed positions:

- **"By sheet" record** — the current scope is recomputed AUTOMATICALLY from the record's stored
  `scope_sheet_paths` over the live snapshot (one-click Reread, no dialog): a ref that appeared on a
  recorded sheet is **added to the scope**, one that left the recorded sheets is **removed from the
  scope**.
- **"By selection" record** — the scope is the CURRENT board selection at the moment of the click:
  re-select the (possibly changed) set on the board first, then click Reread. An empty selection shows
  a warning instead of a silent diff.
- **Named presets** — a "By sheet" record carrying `scope_presets` shows a **Preset** combo on the
  record page (default "(current)" = the stored `scope_sheet_paths`, i.e. the 5c behavior). Pick a
  saved preset to compute THIS Reread's scope from ITS paths instead; Apply then makes that preset the
  record's NEW stored `scope_sheet_paths` — the `scope_presets` library itself is never rewritten by
  Apply (only Record/Re-source "Save as preset" edits it).

The diff dialog lists what changed — component(s) no longer on the board, components moved (compared
in a translation-invariant way through a transient reference recorded component, so moving one part
does not report the whole frame drifting), component(s) added to the scope, component(s) removed from
the scope, vias & tracks added & removed, new/gone boundary nets — and **Apply** rewrites the stored
record in its own file, all in one explicit confirmation (no per-piece copper validation). Apply is
disabled while a recorded component is missing from the board (the record cannot be faithfully
re-synced). Nothing is applied to the board.

### The record page (Config tree → `scheme_lists:` leaf)

Clicking a `scheme_lists:` leaf in the Config tree opens the record in the Config dock's right QView.
Since Commit F the page is a **two-tab** page:

- **"Record summary" tab** — read-only: the `source_sheet` readout, a recorded-geometry summary, the
  **Reread** button and — for a "By sheet" record with a `scope_presets` library — a **Preset** combo
  for switching which saved checklist this Reread uses.
- **"Pivot / Anchor" tab** — the editable pivot block (Commit B1 + B2): an x/y pair in the record's
  CENTRE-frame (mm offsets from the recorded region's centre, `(0, 0)` = the centre) prefilled from the
  stored `pivot`, a **Centre** quick-set that writes 0/0 into the fields, a **"Take from selection"**
  button that reads the centre of the CURRENT live board selection and writes it into x/y as the pivot
  in the record's centre-frame (selected centre minus the LIVE centre of the recorded region —
  recomputed from the recorded components that are actually on the board; a warning is shown when some
  are missing; needs a live KiCad connection) and an **Apply** that SAVES the pivot into the record's
  owning file — a pure config write, no live board. "Take from selection" only PREFILLS the fields as a
  preview — nothing is written until **Apply** is pressed. The recorded region's centre comes from the
  polled full-board snapshot, which is REBUILT on the worker thread before the click reads any position
  (R.2.1, plan_2026_09_11_stale_snapshot_positions.md): a component moved in KiCad is honoured at once,
  still without any direct board IPC on the GUI thread (Commit H).

The record itself is edited by the Pivot Apply above, by re-recording (Record...), re-sourcing
(Re-source...) or re-syncing (Reread), never by hand.

### Placing a record — Tools → Scheme Lists → Place... (the "Place Scheme List" page)

The Config side of CLONING a record onto a (possibly twin) sheet. The separate **"Place Scheme List"**
QView page in the Config dock's right side (a plain Config right-page, deliberately NOT a tab of
"Instantiate from Cell...") is opened by Tools → Scheme Lists → **Place...** (pre-filled with the record
selected in the Config tree, if any) or a record's context-menu **Place...** (pre-filled with that
record). The form holds:

- **Scheme List** — which recorded snapshot to place (searchable combo of `cfg.scheme_lists`).
- **Target sheet** — leave empty (or equal to the record's `source_sheet`) to place the record "in
  place" on the sheet it was captured from. The other offered values are the REAL twin top-level
  sheets on the live board only (2+ channel instances sharing the same sub-sheet structure, computed
  from the cached board snapshot — never a fresh board call) — single-instance sheets
  (FPGA/Power/MCU) and sub-sheets (DAC/OpAmp) are never offered, because they are not valid
  `entity.sheet` targets for the onto-sibling apply (the same twin rule
  `scheme_list_apply` uses at Redraw time).
- **Tree / Parent node** — the EXISTING tree the new node is appended to (generated `tree_instances`
  are read-only and excluded) and the parent node inside it, DFS-listed with a "— top level (no
  parent) —" sentinel (top level = offset relative to the tree anchor). A new tree is NEVER created.
- **X/Y offset (mm)** — the node's `xy`: offset relative to the chosen parent (or the tree anchor for
  a top-level node); the opt-in **"Take from selection"** checkbox fills them from the live board
  selection's centre.
- **Rotation (deg)** and **Entity name** — the rotation is written onto the node at creation; the
  Entity name must be non-empty and unique.

**Place** creates a NEW `scheme_list:`-based Entity (carrying only `name`/`scheme_list`/`sheet` — it
references the record by name and never copies its geometry) plus a `placement` node appended under the
chosen tree/parent. The live board is untouched until the tree node is **Redraw**-ed (twin resolution
+ net remap happen then). Reread, by contrast, only rewrites the stored snapshot and never places
anything.

## Cells

Edits a `cells:` entry (see [docs/config.md](config.md) on `Cell`) — Components/Vias/Tracks (local
`along`/`across` offsets from the cell's own `(0,0)`) plus, recursively, nested `clone_placements:`
referencing other cells/roles. Added 2026-08-06 after Denis hit a real bug caused by the ONLY
existing way to create a cell — Config tree's **Add cell...** wrote a raw `{"components": []}` stub
straight to the config with no form behind it at all ("создавать экстрактор под один компонент, прости,
тупняк" — a full select-on-board-and-extract round trip was the only way to add so much as one
component slot by hand).

Since 2026-09-04 (plan `plan_2026_09_04_celldock_to_dialog.md`) this form is a standalone
**non-modal dialog**, not a Detail-dock tab anymore — open it from the main menu's **Tools → Config →
Edit Cell...** (the Config submenu is the future home of Config-related Tools actions), or load a
specific cell via the Config tree's Cells category (right-click **Edit cell...** / **Add cell...**,
which open the same dialog pre-loaded / blank — see below).

Four tabs, same "table + detail row below" shape as Rules' own Spoke editor, one pair per kind
(Components/Vias/Tracks/Nested cells) rather than one tree merging all four — none of the four share
a common set of columns, so a merged tree would still need the detail form below to switch shape on
selection, buying nothing over separate tabs. (Denis initially proposed a tree given nested cells can
recurse — that tree is Config tree's own Cells category, which now shows a composite cell's nested
`clone_placements:` as child nodes for read-only navigation, not this dock's internal editor.)

- **Name**/**Layer** — the cell's own identity and absolute layer (`F.Cu`/`B.Cu`).
- **Anchor** — **(none)** / **Role** (`+Pad`, optional) — the cell's **mount point A** in its stored
  bbox-local frame (design_2026_09_05 v2), read by `cell_mount_offset`
  (kicadstamp/geometry/cell_anchor.py) at every cell consumer — see
  `Cell.anchor_xy`/`anchor_role`/`anchor_pad` in [docs/config.md](config.md). **Role** is a closed
  combo sourced from THIS cell's own current Components list (not the live board) — it must name one
  of them. The combo is keyed by `currentData()` strings (`none`/`role`), so its item ORDER can never
  silently break a reader. Since Фаза B (2026-09-10) this form owns ONLY the offline Role anchor: the
  old **XY** mode and the **Take coordinates from selection** button are GONE (computing a numeric
  `anchor_xy` at save time was the rejected mechanism). A STORED `anchor_xy` is not edited here — it
  is carried through verbatim on save (never dropped: it WINS over Role/Pad, and losing it would move
  the cell's content) and is edited in the Config tree's **Cell anchor...** page.
- **Components** — Role (searchable, autocompleted from the live board's `Role` field, same source
  as Chain's own anchor-role combo), Offset along/across, Angle, Layer (inherit/`F.Cu`/`B.Cu`), Net
  template (for `clone_placements:`'s by-nets role matching only).
- **Vias**/**Tracks** — the cell's own top-level (spoke-level) vias/tracks, same fields as
  [docs/config.md](config.md)'s `TemplateVia`/`TemplateTrack`.
- **Nested cells** — one entry per `CellPlacement`: Name, **Cell** (searchable, every `cells:` key
  reachable from the project's root, same source as Chain's own spoke-cell combo) **or** **Role**,
  X/Y, Rotation, Mirror, Layer.
- **Scope cuts, deliberate** (shipped in one sitting, not because they don't matter): per-component
  vias (`TemplateComponentSlot.vias`) are not editable here — only the cell's own top-level Vias tab;
  a pre-existing per-component via list still round-trips untouched if the row isn't otherwise
  edited. Nested cells only expose their core fields — `nets:`/`params:`/`net_overrides:`/`refs:` are
  preserved verbatim if already present, not editable from this form.
- No Redraw/Resolve — unlike Chain/Point/Placer, a Cell has no anchor of its own on the live board; it
  only ever gets a physical position in the context of a placement/spoke that references it.
- **Save** — writes into the project root file's `cells:` section (a dict keyed by name, same shape as
  Points).

Config tree's **Add cell...** now opens this form blank (`new_cell()`) inside the same non-modal
dialog instead of writing a stub — same shape as every other Add-entity action. Editing an EXISTING
cell's content is a separate action, right-click **Edit cell...**, deliberately not the same click as
a plain left-click on a Cell leaf (which keeps its original meaning, "pick this cell as a placement's
content" — Placer's own Cell field), so opening a placement form and opening the cell editor never
fight over one click.

**Cell anchor...** (2026-09-09, Phase C of `plan_2026_09_09_cell_anchor_v2_declarative_and_
board_overlay.md`) — a NEW dedicated anchor editor, separate from the full Cell dialog above: right
click a Cell leaf in the Cells category → **Cell anchor...** opens it as a Config right-QView page
(the full cell form keeps editing Components/Vias/Tracks/Nested; this page edits ONLY the anchor).
It is the v2 declarative anchor UI — the anchor is a REFERENCE resolved at apply time (Phase A's
`resolve_pad_mount`), no board round-trip at save time. Three tabs:

- **Source** (the first tab) hosts ONLY the **working context — Sheet (optional) / Cluster** of the
  placed instance. The **Role anchor** tab holds **Role** (closed combo from THIS cell's own
  components, narrowed by the working Cluster — the roles actually present on that cluster) and an
  optional **Pad**. **Read from selection** understands the three selection cases: a selected **pad** →
  its owner footprint's Role + Cluster and the pad number; a selected **footprint** → Role + Cluster
  (Pad untouched); a selected **Via** → an explicit "this is the Marker anchor tab's case" message,
  never "nothing selected". A selection spanning SEVERAL clusters is a fatal listing them (never
  "take the first"). **Set as anchor** writes `anchor_role` (+`anchor_pad`) and REMOVES any stale
  `anchor_xy` (GUARD 1 — so the live pad resolution actually runs). Role-only = the component's stored
  centre (offline — the live board is needed ONLY for Read from selection; picking Role/Pad by hand
  and saving works with no board, proven by test).
- **Marker anchor** — draws the cell's bbox rectangle and a draggable marker circle as REAL KiCad
  graphics on the overlay layer (the **Settings → Board overlay** layer, `User.Drawings` by default;
  the stroke/radius come from the same page too; colour comes from the LAYER, no colour setting), all
  IPC on the worker thread. The world frame is derived from the LIVE CLUSTER alone (2026-09-10, plan
  `overlay_frame_from_cluster`): the cell's roles are resolved to live footprints by the working
  (Cluster, Sheet), the reference slot is `anchor_role`'s (else the first resolved), and the frame
  comes from that footprint through the pure inverse `clone_origin_from_component` — mirror/rotation
  are read from the live footprints themselves, never from a record. Neither a placement, nor the
  trees take part (a cell that was just extracted — an Entity with no tree node yet — gets an overlay
  like any other, and the frame can no longer follow a stale placement instead of the cluster on the
  board). A cluster that is not on the board, or a cell whose roles do not resolve in it, is an honest
  error naming the cluster/role — never "place the cell first". **Place marker** puts the marker at
  the current anchor (or bbox centre when there is no anchor), you drag it with KiCad's own tools,
  **Read position** converts the dragged world point into the cell's bbox frame (reusing
  `world_pos_to_cell_local_offset`) and writes `anchor_xy`, clearing any Role/Pad anchor.

  The drawn shapes are OWNED by `gui/overlay_markers.py` (2026-09-11, task Е of
  `plan_2026_09_11_overlay_markers_owner.md`): a NAMESPACED KEY map in `gui_state.json` under
  `overlay_markers`, e.g. `cell-anchor/<root>/<cell>/marker` and `.../bbox` (other namespaces so far:
  `point/<name>` — the [Points](#points) dock's own circles — and, later, `tree-inner/<tree>`). A KEY
  owns EXACTLY ONE shape — **Place marker**/**Show bbox**
  replace the key's previous shape inside the same worker operation (Denis: "Если он есть, его не
  надо рисовать ещё!"), so pressing the button twice leaves ONE marker, and two circles for one key
  are impossible by construction. A crash leftover within half a marker radius of the point being
  drawn is replaced instead of stacked. The old flat `cell_anchor_overlay` map is migrated INTO the
  new keys once (no uuid is lost) — nothing disappears after the update.

  On connect and on every manual Refresh the map is RECONCILED with the live layer: keys whose shape
  is gone are dropped; shapes no key owns are logged and LEFT UNTOUCHED, because they may be
  something you drew yourself on the same layer.

  **Overlay cleanup** (Phase D, 2026-09-09): the drawn marker/bbox are an editing aid, removed
  explicitly when done — the Marker anchor tab's **Remove overlay** button (this cell's keys), when
  you leave the anchor page (open another Config tree node) or open a different cell/root, and at GUI
  exit (every tracked uuid, bounded + best-effort). The guaranteed recovery from a leaked shape is the
  Settings → Board overlay **Remove entire overlay layer** button, which sweeps the WHOLE chosen
  user layer (confirmed first; only that layer is affected) and forgets the owner's map.

**Remembered cell context** (2026-09-09, Phase E of the same plan): when a Cell is created
(`Extract cluster...` / `Extract tree...`) or its anchor is re-read with the Role anchor tab's
**Read from selection**, the (Cluster, Sheet) of the instance it was taken from is remembered in
`gui_state.json` (key `cell_edit_context`, scoped by the root config path — the cell name is a
Cluster-tag slug, so the same name in two profiles means different boards). Two consumers:
  - the **Cell anchor...** page prefills its working **Sheet/Cluster** from that context when the
    cell is opened, so the Role combo is already narrowed to the cluster the cell was last worked
    in — no click on the board required;
  - the Cell dialog gains a **Select cluster of this cell on the board** button next to
    Refresh geometry / Import vias/tracks: it highlights the whole remembered (Cluster, Sheet)
    instance on the live board, so those whole-cluster operations no longer need a manual hunt.
It is a HINT, never a source of truth: a remembered cluster/sheet that no longer resolves on the
current board (renamed / deleted / another board) leaves the fields empty and selects nothing — no
fatal, no hard dependency (stale remembered values are the norm, not an edge case).

Two Phase C gaps in the same page are fixed in this phase: the **Sheet** combo lists the project's
readable sheet names (the `ctx.sheet_names` VALUES, not the uuid-path keys), and the working
**Cluster** combo is now populated from the live-board snapshot via `DockHub.push_snapshot` (an
editable picker — fill, never restrict, so a typed cluster not in the list still works).

**Refresh geometry from selection** (2026-09-03) — a button in the Cell dialog AND a
right-click **Update from selection...** action on a Cell leaf in the Config tree's Cells category
(one click from the tree — no need to open the dialog and hunt for the button first). It re-reads an
ALREADY-saved cell's geometry (Components' offsets/angle, Vias' offsets, Tracks' start/end/width)
from the CURRENT board selection and writes it back into the cell — the way to pull a geometry
change you made by hand in one physical instance back into the shared template, so Redraw applies it
to every instance. Select the whole cluster on the board first, then run it.

Only the geometric numbers change. `role`, `net_template`/`net_template_pad`/
`net_template_same_as_role`, `net_from_role`/`net_from_role_pad`, `params`, `layer` and any other
semantic key are copied over untouched — this is explicitly NOT a re-extract, and it never guesses a
net. The origin is the cell's single zero-offset (local `(0,0)`) component, resolved live from the
selection. Vias/tracks are matched by resolved net (a `net_from_role` via/track via its role's real
pad, a plain literal as-is); a parametrized literal net (a `{placeholder}` written at extract time)
is matched by its SHAPE, not by guessing the parameter value; a `net: null` via/track (rule-net
convention) is matched by pure position against whatever copper the named nets left unclaimed.
TRACKS are further grouped by their effective copper layer (`layer:` on the record, else the cell's own
`layer:`) since 2026-09-10 — an F.Cu record is never paired with a B.Cu live track of the same net, and
a NEW track record keeps its `layer:` key when the live track sits on the other side. The pairing is
globally nearest (both endpoints, orientation-free) with a deterministic tie-break, so the outcome does
not depend on the order records happen to sit in the file. A missing/extra component role stays a fatal
— that check is what catches a partial/foreign selection BEFORE any copper disappears.

Since 2026-09-05, live via/track copper the cell's current records do not describe is ADDED to the cell
as NEW records (the drawn-copper case — a cell saved before the copper was routed, then re-read after
you drew it; e.g. `pif_p5v`), and since 2026-09-10 the run is SYMMETRIC: a RECORD with no live
counterpart is REMOVED from the cell (Denis, 2026-09-10: "перечитывание целла — целиком на моей совести.
В лог говорим: добавили то-то, удалили то-то"). The selection is therefore the truth for the cell's
copper: updates + additions + removals in ONE run (nothing is written to disk until the project
**Save**). A new record's net is classified by the extractor's own heuristic (a net a selected role's
pad carries -> `net_from_role`(+pad), else a plain literal net — never `net: null`), its geometry
relative to the same zero-offset origin.

2026-09-10 (plan marker_frame_and_sheets J.1) — the re-read expresses the live geometry in the CELL's
own frame, and that frame is DERIVED FROM THE DATA: rotation and mirror are fitted from the cell's
stored offsets against the live deltas of every matched role AT ONCE (`kicadstamp/cell_frame.py` — the
very same transform the marker/bbox overlay uses), never from one component's angle (a two-pin part is
symmetric, its angle ambiguous by 180°; measured 2026-09-10: four capacitors gave +90, FB_PI_FLT -90).
A turned instance is therefore NOT an error, and its rotation is no longer baked INTO the cell — the
failure Denis hit, where the next Redraw applied that rotation a second time, swapped x and y, and left
the marker "at the old position". Slot angles are written as `live angle - theta`, and `anchor_xy` is
NOT read or written here (the cell's frame does not change, so the anchor stays truthful). A cluster
that is genuinely not a rigid copy of the cell — a role moved by hand on the board, ~0.8 mm in the
live case — still re-reads: every OTHER slot keeps its stored offset and the Log gets honest lines:
"the selection is not a rigid copy of this cell (worst deviation X mm) — the geometry was re-read
anyway", plus "the instance is rotated N° — the geometry was expressed in the cell's own frame,
anchor_xy is left unchanged" for a turned one.

A clean run APPLIES the plan directly — since 2026-09-06 there is NO preview dialog, and since
2026-09-10 **"Update from selection..." does not open the Cell dialog either** (Denis: clicking refresh
in the flat Config list popped an Edit Cell window nobody asked for; the result is reported in the Log
and staged for Save, exactly like "Copy placement from cell..."). One Log line per added and per
removed record names WHAT changed — `+ track <net> <layer> w=<width> (x,y) -> (x,y)`,
`- via net_from_role <role>/<pad> (x,y)` — followed by the summary ("Updated cell ... — N record(s)
updated, M via/track record(s) added, K record(s) removed. Save to write the change."); a selection
that already matches reports "Nothing changed". Mutation/autostage go through the same path as a manual
row Update/Add. **"Edit cell..." still opens its dialog** — that is that action's own purpose.

**Import vias/tracks from selection** (2026-09-03) — the additive counterpart of Refresh: a button
right next to it in the Cell dialog AND a right-click **Import from selection...** action on a Cell leaf
in the Config tree's Cells category. It backfills an EXISTING cell with NEW via/track records for live
copper the cell's current records do not describe — the way to add vias/tracks that were missing from
the original extraction (e.g. not yet routed when the cell was extracted, the `fpga_oscill` case)
without touching what is already there. Select the whole cluster on the board first, then run it.

Refresh and Import now BOTH turn "copper the cell does not describe" into NEW records — the difference
is the rest of the run: **Update from selection...** is a full sync (it ALSO re-reads the geometry of
the existing records — components and already-saved vias/tracks — from the selection), while **Import**
is purely ADDITIVE — it never modifies or removes an existing record and cannot add components. Choose
Import when you only want to backfill new copper without touching the already-saved geometry; choose
**Update from selection...** when the whole cluster should be pulled up to date (moved parts/routes AND
new copper together). The same symmetric role match and the same named-net/count checks stay fatal in
both — a wrong/incomplete cluster is rejected exactly the same way. A new record's net is classified by
the extractor's own heuristic (a net a selected role's pad carries -> `net_from_role`(+pad), else a
plain literal net — Import never writes `net: null`, 2026-09-04), and its geometry is relative to the
same zero-offset origin.

Since 2026-09-11 (plan plan_2026_09_11_nested_cell_placement_live_read.md) **Update from selection...**
also re-reads the cell's **NESTED clone_placements** (the "Nested cells" tab) — the one part of a cell
that used to have to be typed by hand, in the cell's CANONICAL frame while looking at a ROTATED instance
on the board. A nested placement is identified by ITS OWN cell's roles, resolved on the live board by the
very resolver Apply uses (role + expected net + the placement's own Sheet/Cluster narrowing, falling back
to the current selection and then to proximity to the position the parent's live frame predicts) — never
by guessing. Its live origin and rotation are then expressed in the PARENT's frame: `xy` through the
parent cell frame's `point_to_cell`, `rotation_deg` through `angle_to_cell`, `mirror` from the reference
footprint's side against the NESTED cell's own layer. A `role:`-only nested placement goes through the
same path (its synthesized one-slot cell). Nothing is invented: a nested placement whose instance cannot
be identified unambiguously — not on the board, its cell is missing from the config, it shares a role
NAME with the parent cell, its roles resolve to a component the parent already claims, or the parent
instance is mirrored (a composite cell cannot be mirrored at all, so a relative mirror has no defined
composition) — is left COMPLETELY untouched with one honest line in the Log. One Log line per CHANGED
placement names it with its old -> new xy/rotation/mirror, the same reporting rule the added/removed
copper already uses.

A clean plan opens a read-only **preview dialog** listing the NEW records (Kind / Position / Net) —
**Apply** appends them to the loaded cell in memory and auto-stages it exactly like a manual row Add;
nothing is written to disk until the project **Save**.

**Copy placement from cell...** (2026-09-06) — the OFFLINE sibling of Refresh/Import, reached from the
Config tree's cell context menu (right-click a Cell leaf, **Copy placement from cell...**, like Update/
Import from selection). Instead of reading live copper from a board selection it copies the PLACEMENT of
another cell onto the right-clicked cell. Use it to restore the missing copper/geometry of a structurally
identical "twin" cell — e.g. the negative PI filter `pif_n5v`, extracted with its components but no
vias/tracks, rebuilt from the fully-routed positive twin `pif_p5v`. No live board is needed — a purely
config-level, one-shot action, not a live link.

The action opens a MINIMAL dialog holding only a **combobox of the fitting donor cells** (Denis,
2026-09-06) plus Copy/Cancel — no preview tables. The combobox lists only cells that FIT the target:
every component role of a listed donor is already present among the target's components (the donor's
role set is a SUBSET of the target's), so a listed donor can only overlay geometry onto existing target
slots and can never drag in a role the target lacks. On **Copy** the operation is validated again and
applies:
- **Components** — the donor's geometry is OVERLAID onto the target slot with the SAME role
  (`offset_along_mm`/`offset_across_mm`/`angle_deg`/`layer`, plus the donor slot's per-component vias).
  The target's own `net_template`/`net_template_pad`/`net_template_same_as_role` are never touched (they
  are rail-correct for the target); an identical donor geometry contributes no change at all.
- **Vias/tracks** — ADDITIVE append of the donor's records as-is. They must be `net_from_role`(+pad)
  (role-relative — the actual net is defined by the placing Entity instance at apply time, so copying
  them across rails re-resolves them to the target's own nets) or a rule-net literal (GND); a literal
  rail-specific / parametrized / net-less record makes the whole copy a fatal — shown as a warning, never
  a silent copy of garbage. Every `net_from_role` role must exist among the target's components, or the
  copy is refused with the missing roles listed.
The copy overlays the geometry and appends the copper to the loaded cell in memory and auto-stages it;
nothing is written to disk until the project **Save**.

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

Since 2026-09-11 a **lost KiCad connection is reported HERE, not in a dialog**
(plan `2026_09_11_no_modals_and_busy_kicad`): every "no live board connection / connect KiCad first"
guard in the docks — the Chain/Rules anchor's *Read current position*, Placer's two *Read current
position* buttons, Trees' *Reread current position* and the node form's, the Scheme List pivot's
*Take from selection*, DockHub's two Scheme-List Tools flows, the fieldstool's Stage/Sync and the
Board-overlay sweep — writes **one ERROR line** into this panel (red, see the level colours) and
still REFUSES the operation. Only form VALIDATION keeps its dialog ("Ref is required", a bad number
in a field, …), because that is a direct answer to what you just typed.

A **busy KiCad** (`KiCad is busy and cannot respond to API requests right now` — usually an
unfinished tool in the GUI: dimensioning, interactive routing, the move tool) also lands here as one
ERROR line with the actionable explanation ("finish the tool — Esc or right-click → Cancel — and run
it again; the board was not modified") instead of a ~20-line Python traceback. The traceback is
still written, but at DEBUG level only, so **Verbose** shows it when you actually need it. That
covers every long operation (Extract / Redraw / Apply, the Scheme List capture, the fieldstool's
writes) plus the redraw chain's per-record failures. `_mutating_call` (the adapter's write wrapper)
also finally RETRIES a write when KiCad answers AS_BUSY — it used to match the text `not ready`,
which the real message never contains, so the retry silently never fired (2026-09-11).

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
window if it was tray-hidden and shows/raises the **Components** dock — which hosts the embedded
[fieldstool pane](#fieldstool-tab) on the right of its splitter — even if another left-group tab
(Config/Trees) is active or the Components dock was individually closed.

## What's remembered between restarts

Plain JSON in `gui/gui_state.json` (gitignored, human-readable, deliberately not Qt's own
`QSettings`/`saveGeometry()` blob): window position/size, Always on top, Tray icon, Components tree
grouping and its live/"Not yet applied" toggle, the Files dock's root directory and last click, and
all three file-role assignments (Cells/Extractor/Placer). Since 2026-09-05 the master-detail
splitter positions are remembered too — the Config dock's divider (config-tree width vs. its right
QView) is flushed on quit and restored the first time the dock is shown, each Trees-dock page's
divider is remembered per tree, and the **Components** dock's own divider + active left tab
(`components_splitter_sizes` / `components_left_tab`: tree|Pending vs. the fieldstool pane) are
flushed on quit and restored on first show. The embedded fieldstool window keeps its own separate
state file (`gui/fieldstool_gui_state.json`).

## Tests

`tests/gui/` — offscreen (`QT_QPA_PLATFORM=offscreen`, set automatically), no live KiCad
connection needed, part of the default `pytest` run. Board-mutating logic (Placer's Redraw) is
tested with `ApplyPipeline`/`PlacementPlanner` mocked — it verifies the dock builds the right
config and calls the pipeline correctly, never that it actually moves anything. See
`tests/gui/conftest.py` for the fixtures: `qapp`, `main_window` (a bare stub, for dock-level tests),
`real_main_window` (the real `MainWindow`, needed for tray/close/fieldstool-embedding tests),
`fieldstool_window` (a real `gui.fieldstool_window.MainWindow` with a fake connection, for the
fieldstool tab's own staging/Apply logic standalone), `isolated_settings`, `log_dock`.
