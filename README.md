# KiCadStamp v2.0.0

**KiCadStamp** automates component placement and block cloning on **KiCad 10** printed circuit boards.
It connects to a running KiCad over the IPC API and makes repeatable what otherwise has to be done by
hand hundreds of times: placing components, dropping vias, laying tracks and moving whole functional
blocks from one place to another.

It is an **advanced alternative to the KiCad Replicate Layout plugin**, aimed at complex multi-channel
boards, hierarchical schematics and the reuse of finished routing: replicating board sections, copying
channels, cloning pi filters and power rails, and carrying routing across identical blocks.

It ships a graphical interface (PyQt6), a command line, and an MCP server for use from Claude Code.

The core idea is that **components are chosen by role, not by refdes**. A block is described once in
local coordinates and bound to the board through roles and nets, so re-annotating the schematic breaks
nothing.

---

## What it does

- **Cells** — a block's geometry in local coordinates: components, vias, tracks. A cell can be rotated,
  shifted, mirrored and applied anywhere on the board.
- **Role-based lookup** — instead of `C12`/`R7` you write roles (`PI_FILTER_C1`, `HEAVY`), and the actual
  instances come from a pool keyed by the schematic's `Role` field and by net.
- **Placement trees (`trees:`)** — where each instance actually sits. A tree node references an entity and
  carries the position; the entity itself has no position at all.
- **Section cloning** — by selection (a one-off instance) or by nets (a block repeated many times, with
  net names parametrised).
- **Extraction from the board** — select a block in KiCad, read it into a cell, then apply it anywhere.
- **Placement registry** — remembers the UUIDs of created vias and tracks and reconciles against the live
  board, so a repeat run updates instead of duplicating and cleans up what went stale.
- **Up-front validation** — the whole config is checked before the first board edit: cells, pads and
  anchors exist, pools hold enough components, names are unique, nets resolve.
- **Undo** of the last operation.
- **Schematic-side work** — bulk setting and renaming of `Role`/`Cluster` fields directly in `.kicad_sch`
  (fieldstool), without IPC and with KiCad closed.
- **File-based cloner** — parses `.net` and `.kicad_pcb` without IPC and builds a twin map of channels for
  hierarchical projects.

DRC is deliberately out of scope: track collisions are KiCad's own job.

---

## Installation

### Requirements

- **Python 3.10** or newer.
- **KiCad 10.0.4** or newer with the IPC API enabled (*Preferences → Plugins → Enable IPC API server*).
- For the GUI, a working Qt stack (installed along with the dependencies).

### Install

```bash
git clone <repo>
cd KiCadStamp
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

Dependencies are installed automatically and version-pinned. The ones that carry the runtime:
`kicad-python==0.7.1` (the KiCad IPC wrapper, imported as `kipy`), `PyQt6==6.11.0`, `sexpdata==1.0.2`,
`pynng==0.9.0`, `protobuf==5.29.6`.

Optional extras:

```bash
pip install -e ".[dev]"           # pytest, babel, pyflakes
pip install -e ".[diagnostics]"   # numpy, scipy, psutil, rich, watchdog
pip install -e ".[mcp]"           # mcp — only needed for the MCP server
```

### Entry points

Installing gives you three commands:

| Command | What it starts |
|---|---|
| `kicadstamp` | the command-line interface |
| `kicadstamp-gui` | the graphical interface |
| `kicadstamp-mcp` | the MCP server (stdio) |

Running from a source checkout without installing, the equivalent scripts live in the repository root:
`kicadstamp_cli.py`, `kicadstamp_gui.py`, `fieldstool_cli.py`.

---

## Quick start

### 1. Setting up roles in the schematic (Eeschema)

A component takes part in placement only if it carries a custom `Role` field:

1. Open the symbol in Eeschema.
2. Add a field named **Role** whose value matches the role in the cell (for example `LIGHT`, `HEAVY`).
3. Run **Update PCB from Schematic** so the field reaches the board.
4. Check that the field is readable: select the component in KiCad and run
   ```bash
   python -m kicadstamp.diagnostics.get_selected_component
   ```
   The script prints refdes, value, footprint, position, angle, pads, nets and the `Role` field.

You do not have to set roles on dozens of components by hand — that is what fieldstool is for (a tab in
the GUI, or `fieldstool_cli.py`); see [docs/fieldstool.md](./docs/fieldstool.md).

### 2. Running

```bash
kicadstamp-gui                          # graphical interface
kicadstamp apply --config profiles/my/config.sexp --dry-run
```

`--dry-run` prints the plan without touching the board. That is the right first run against a config you
do not know yet.

---

## Key concepts

A short glossary. The details live in [docs/config.md](./docs/config.md) and
[docs/placement.md](./docs/placement.md).

**Role** — a component field in the schematic. A role says *what the component does* in the block, not
what it is called.

**Cluster** — a second component field grouping the instances of one block. Role plus cluster identify a
component without relying on its refdes.

**Cell** — a block described in local coordinates: components by role, vias, tracks. A cell has **no board
position of its own** — it is a template.

**Cell anchor** — the point of the cell that lands on the board (`anchor_xy`/`anchor_role`/`anchor_pad`).
It is expressed in the cell's own frame and resolved to live coordinates when applied.

**Entity** — everything about a thing except where it stands: which cell, its electrics, its identity. An
entity has no position fields at all.

**Tree (`trees:`)** — where it stands. A tree node references an entity and carries coordinates; nodes
nest, and a child's coordinates are measured from its parent.

**ManualSpoke** — a via with a track from a component pad, written by hand inside a chain (`chains:`).

**ClonePlacement** — the historical way to place a clone (by selection or by nets). Still supported, but
new profiles should prefer the Entity + Tree model; `tools/convert_placements.py` migrates the old ones.

**Scheme List** — a snapshot of a set of board components that can be re-read and re-placed as a whole.

**Registry** — a journal of created vias and tracks with their UUIDs. It is what makes a repeat run
idempotent: it updates rather than duplicates.

---

## Configuration format

The config is an **s-expression** (`.sexp`), like KiCad's own formats. A file with any other extension is
rejected with a fatal error. (`.json` is still read as well — it backs a legacy `scheme_lists.json` side
file; the GUI now creates its own `scheme_lists.sexp` the first time a Scheme List is recorded, and keeps
using an existing `scheme_lists.json` as-is where a profile already has one.)

A profile can be split across several files: `include:` at the root pulls in other `.sexp`/`.json` files
recursively, and each may carry any combination of sections. The `flatten` command folds such a graph back
into one self-contained file.

The full reference, with examples taken from a live profile, is [docs/config.md](./docs/config.md).

---

## CLI commands

| Command | Purpose |
|---|---|
| `apply` | apply the placement described by a config to the open board |
| `undo` | undo the last operation |
| `extract` | extract a cell from the current board selection |
| `extract-net` | capture one net's copper (tracks + vias) as a `net_traces:` record |
| `clone-extract` | snapshot a channel to `.sexp` (file-based cloner, no IPC) |
| `clone-plan` | generate a ready `clone_placements:` block for a channel clone |
| `channel-copy` | copy a whole channel's placement from one channel to another via a twin map |
| `flatten` | merge an `include:` graph into one self-contained file |
| `convert-trees` | rewrite `trees:` from the removed `own_anchor` grammar to mount nodes |

Every flag is documented in [docs/commands.md](./docs/commands.md).

Example:

```bash
kicadstamp apply --config profiles/my/config.sexp --dry-run   # plan only
kicadstamp apply --config profiles/my/config.sexp             # apply
kicadstamp undo --verbose                                     # roll back
```

---

## Graphical interface

```bash
kicadstamp-gui [--timeout-ms 20000] [--verbose]
```

This is the main way to work with a project. On the left, three trees — **Components** (board and
schematic components), **Config** (the config's structure) and **Trees** (placement trees); on the right,
a context panel that follows the selected node; along the bottom, the log.

Edits are not written to disk as you make them: they accumulate in a working set, and **File → Save**
commits them all at once.

See [docs/gui.md](./docs/gui.md) for the details and [docs/hotkeys.md](./docs/hotkeys.md) for the
keyboard shortcuts.

---

## MCP server

```bash
pip install -e ".[mcp]"
kicadstamp-mcp
```

An MCP server over stdio: Claude Code and other MCP clients can see the live board and act on it — read
the board identity, footprints with their roles and clusters, the current selection and the board's nets;
apply a config through the same validated pipeline as `apply`; and, when explicitly enabled, move items
directly.

See [docs/mcp.md](./docs/mcp.md).

---

## Project layout

```
KiCadStamp/
├── kicadstamp/            # core: config, planning, execution, IPC
│   ├── config/            # config loading and model, the include: graph
│   ├── domain/            # board DTOs (Footprint, Track, Via, Pad, Net, Zone)
│   ├── kicad/             # the KiCad IPC adapter and the IBoardAdapter interface
│   ├── placement/         # planner, executors, services
│   ├── geometry/          # geometry: layout, keepout, cloning
│   ├── cloner/            # file-based cloner (.net/.kicad_pcb, no IPC)
│   ├── diagnostics/       # diagnostic scripts
│   ├── cell_*.py          # cells: frame, geometry, placement copying
│   ├── trees.py, link_trees.py, tree_position.py     # placement trees
│   ├── net_*.py           # net resolution, matching and traces
│   ├── schematic_*.py     # .kicad_sch handling (fieldstool)
│   ├── config_working_set.py  # the staged-edit model
│   └── registry.py        # via and track registry
├── gui/                   # PyQt6 GUI: docks, trees, editors, board overlay
├── mcp_server/            # MCP server (stdio)
├── docs/                  # documentation, bilingual (en + _ru)
├── tests/                 # tests
├── tools/                 # utilities and profile converters
├── locales/               # gettext translation catalogues
├── diagnostics/           # throwaway probes and reproductions
└── packaging/             # distribution builds
```

---

## Diagnostics and known issues

### KiCad crash on the first IPC write (#24966 / #25322)

With the schematic editor open, the **first transaction of a session**
(`begin_commit()`/`push_commit()`, even an empty one) can crash KiCad — a null pointer in
`API_HANDLER_EDITOR::checkForBusy`.

**Symptoms:** KiCad closes silently and the client gets
`ConnectionError: Error receiving reply from KiCad: Timed out`.

**Workaround:** close the schematic editor before the first write. The code carries a warning
(`check_write_crash_risk`) and retries with a delay, but the crash is still possible — it is a KiCad
defect. It is specifically the *first write of a session* that is exposed: if the first `apply` runs with
only the PCB Editor open, opening the Schematic Editor afterwards is usually safe.

`#25322` is the same family seen from the schematic side. The full write-up and the crash-hunting toolkit
are in [docs/crash_hunting.md](./docs/crash_hunting.md).

### Diagnostic scripts

In `kicadstamp/diagnostics/`:

- `diagnose_first_write_crash.py` — a read/write ladder for pinning the crash down, see
  [docs/diagnose_first_write_crash.md](./docs/diagnose_first_write_crash.md);
- `test_move_one_cap.py`, `test_flip_one_cap.py`, `test_create_one_via.py` — minimal operation tests;
- `test_pad_mirror_convention.py` — an empirical check of how pads mirror on flip;
- `get_selected_component.py` — details of the selected components, including the `Role` field;
- `get_pad_bbox.py`, `diagnostic_keepout.py` — helpers.

An overview is in [docs/diagnostics.md](./docs/diagnostics.md).

---

## 📚 Technical documentation

Every page is bilingual: `docs/<topic>.md` is English, `docs/<topic>_ru.md` is Russian.

- [Project architecture](./docs/architect.md)
- [CLI commands](./docs/commands.md)
- [`.sexp` configuration reference](./docs/config.md)
- [PyQt6 GUI](./docs/gui.md)
- [Keyboard shortcuts](./docs/hotkeys.md)
- [MCP server](./docs/mcp.md)
- [Planning and execution](./docs/placement.md)
- [Geometry utilities](./docs/geometry.md)
- [Rotating and transforming cells](./docs/rotate_template.md)
- [KiCad adapter](./docs/kicad.md)
- [Using kipy](./docs/kipy.md)
- [Coding placement in Python: explore/author](./docs/python.md)
- [fieldstool: Role/Cluster in `.kicad_sch`](./docs/fieldstool.md)
- [File-based cloner](./docs/cloner.md)
- [Diagnostics](./docs/diagnostics.md)
- [Hunting KiCad crashes](./docs/crash_hunting.md)
- [`diagnose_first_write_crash.py` reference](./docs/diagnose_first_write_crash.md)
- [Top-level modules](./docs/uplevel_modules.md)
- [Module dependency diagram](./docs/diagram.md)
- [Internationalisation (i18n) — gettext/Babel](./docs/i18n.md)
- [Tests](./docs/tests.md)

---

## Versioning

The single source of truth is `__version__` in [`kicadstamp/_version.py`](./kicadstamp/_version.py): this
README's heading and the `--version`/`-V` flag of every entry point read the version from there instead of
keeping a literal of their own.

We count by stages, not by commits: **MINOR** goes up by one per noticeable block of work (one refactoring
session is one step, however many commits it contains), **PATCH** covers point fixes between stages, and
**MAJOR** is reserved for genuine breaking changes to the CLI or to the config format (`.sexp`/`.json`).

**2.0.0** — `.sexp` became the config format (2026-08-28) and placement moved to the Entity + Tree model.

---

## Keywords

KiCad, KiCad 10, KiCad IPC API, kipy, PCB automation, replicate layout, board section replication,
channel cloning, copy placement, multi-channel PCB, hierarchical schematics, repeated blocks,
component placement, vias, via stitching, thermal vias, template-based routing, Role/Cluster,
placement generation, Python, PyQt6, MCP, Claude Code.

---

## License

This project is distributed under the **MIT** license. See the `LICENSE` file for details.
