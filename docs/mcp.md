# MCP Server for KiCadStamp

## Purpose

KiCadStamp ships an **MCP (Model Context Protocol) server** over **stdio** so
Claude Code / other MCP clients can see what is happening on the live KiCad
board — and act on it:

- **read** — board identity, footprints (with Role/Cluster), the current PCB
  editor selection, and board nets;
- **validated write** — apply a placement config through the same validated
  pipeline as the CLI `apply` / GUI Redraw (board identity, FORK-1, "never
  guess silently", registries, dependency ordering);
- **raw write** (opt-in) — direct kipy moves that bypass the config layer.

The server is a thin protocol layer on top of the existing
[`KiCadBoardAdapter`](kicad.md) and the apply pipeline — it never reimplements
placement logic.

## Installation

```bash
pip install -e ".[mcp]"
```

This installs the `mcp` SDK and the `kicadstamp-mcp` console script. The MCP
dependency is optional — GUI/CLI/tests do not need it.

## Registration

The server runs on stdio and is spawned by your MCP client. Two ways to
register it:

1. **Client's Settings tab (primary)** — add an MCP server with the command
   `kicadstamp-mcp` (or `.venv/bin/python -m mcp_server.server` from the repo
   root). This is a per-user, client-side setting.
2. **Repo-scoped `.mcp.json`** (auto-discovered by clients that support it) —
   the committed file at the repo root already points at
   `.venv/bin/python -m mcp_server.server`. **Windows note:** that path is
   Linux-specific (`.venv/bin/python`); on Windows register the server via the
   client's Settings tab with `.venv\Scripts\python.exe -m mcp_server.server`
   (or the `kicadstamp-mcp` console script).

## Tools (first iteration)

| Tool | Risk | What it does |
|---|---|---|
| `kicadstamp_get_board_identity` | low | Board name + KiCad version of the open board |
| `kicadstamp_list_footprints` | low | ref, Role/Cluster, position (mm), rotation, layer; optional `ref_prefix` |
| `kicadstamp_get_footprint` | low | One footprint in detail: pads (number/net/position) and the nets on them |
| `kicadstamp_get_selection` | low | What the PCB editor currently has selected (groups expanded) |
| `kicadstamp_list_nets` | low | All board net names |
| `kicadstamp_get_items_by_uuid` | low | Resolve board item uuids (tracks/vias/footprints) to detailed records; each requested uuid appears exactly once, missing ones report `found: false` |
| `kicadstamp_list_tracks` | low | Track segments with optional `net`/`layer` filters (e.g. `net='GND'`); prefer filters or `get_items_by_uuid` on large boards |
| `kicadstamp_list_vias` | low | Vias with optional `net` filter (e.g. `net='GND'`); prefer the filter or `get_items_by_uuid` on large boards |
| `kicadstamp_apply_config` | low (validated) | Run the existing validated apply pipeline on a `.sexp`/`.json` profile; `dry_run` only plans. It runs the SAME `run_apply` the CLI runs, on the config the CALL named — so THAT profile's override store is what is in force (our stored Role/Cluster wins over the board; the profile's `role_cluster_source` switch still decides — see [docs/config.md](config.md)) |
| `kicad_raw_move_footprint` | **high (raw)** | Move one footprint by ref directly over kipy; off by default; requires `expected_board_name` (mandatory board-identity guard) |

Tool names and descriptions are English only (machine interface); server log
messages and results follow the project's bilingual gettext setup.

## Errors

When a tool cannot do its job, the client gets a sentence — not a stack trace —
for every failure a user can act on:

- **KiCad is closed** (or was closed while the call was in flight): *"KiCad is
  not answering — it is probably not running, or it was closed while the call was
  in flight. Start KiCad with the board open and try again…"*, with kipy's own
  error text kept as the detail. The message is localized (the project's usual
  gettext catalogue) and lives where the CLI can use the same text later — one
  message, two surfaces.
- **KiCad is busy** (an unfinished tool in the GUI: interactive routing, the move
  tool, a dimension): the `AS_BUSY` refusal gets the project's long explanation —
  finish that tool in KiCad (Esc or right-click → Cancel), then call again; the
  board was not modified.
- **The link dies mid-call**: the server tears the dead adapter down and retries
  the call **exactly once** with a fresh one. There is no queue, no TTL and no
  retry policy beyond that single attempt; if the retry fails as well, the answer
  is the same clear message as above.

Anything else is a real defect, and it stays distinguishable: MCP's own crash
wrapper is returned (its message names only the tool, the traceback goes to the
server log), so a bug is never presented to the model as a user-facing failure.

**What a call costs.** Every tool call rebuilds the board before answering, so a
read is LIVE instead of served from the cache filled when KiCad was first
connected. Measured on the test boards: the calls that read footprints (e.g.
`kicadstamp_get_footprint`, `kicadstamp_list_footprints`,
`kicadstamp_get_items_by_uuid`) pay ONE full board read — 26–38 ms on a
332-footprint board, 36–40 ms on a 414-footprint one — while the tools that never
look at a footprint pay 0.7–10 ms. The number is a fact about the price, not a
policy: no TTL is in force.

## Security model

- **Validated** tools (read + `apply_config`) are always available and go
  through the full project protection (`run_all_checks`, `check_board_identity`,
  registry, dependency order).
- **Raw** tools are **off by default**. They are registered only when enabled —
  either via the GUI's **Settings tab** ("MCP server" group, persisted to
  `gui_state.json`) or the `KICADSTAMP_MCP_ALLOW_RAW_WRITE=1` environment
  variable (env wins).
- Every raw write runs the **board-identity guard** first (`check_board_identity`)
  — the tool requires the `expected_board_name` parameter (mandatory, not
  optional) and refuses to write when a different board is open; the connected
  board is always reported. The raw path is not a hole in the protection the
  apply path already has.
- The server does **not** add its own approval layer: a raw tool's risk is
  stated in its description and the host's permission gate decides.

## Configuration

The only server setting is the **raw-write gate**, controllable from the GUI
Settings tab (checkbox "Allow raw MCP write tools") or the env var above. There
is no separate server config file — everything else is passed per tool call
(e.g. `config_path` to `apply_config`).

## Architecture

```
mcp_server/
├── server.py       # MCPServer (mcp SDK), stdio, tool registration, raw gate
├── tools.py        # Pydantic schemas + thin wrappers; ToolError conversion
├── handlers.py     # SDK-free logic over the adapter / run_apply
└── connection.py   # one KiCadBoardAdapter per process: lazy connect, lock,
                    # reconnect on drop, close on shutdown
```

`handlers.py` and `connection.py` never import the MCP SDK; unit tests exercise
them with a fake adapter (no live KiCad). The stdio transport itself is
verified against the real test board manually.

## See also

- [`docs/kicad.md`](kicad.md) — the `kicad/` adapter layer the server drives.
- Design document: `techdocs/handoff/deepseek/design_2026_08_29_kicad_mcp_server.md`.
