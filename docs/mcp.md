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
   `.venv/bin/python -m mcp_server.server`.

## Tools (first iteration)

| Tool | Risk | What it does |
|---|---|---|
| `kicadstamp_get_board_identity` | low | Board name + KiCad version of the open board |
| `kicadstamp_list_footprints` | low | ref, Role/Cluster, position (mm), rotation, layer; optional `ref_prefix` |
| `kicadstamp_get_footprint` | low | One footprint in detail: pads (number/net/position) and the nets on them |
| `kicadstamp_get_selection` | low | What the PCB editor currently has selected (groups expanded) |
| `kicadstamp_list_nets` | low | All board net names |
| `kicadstamp_apply_config` | low (validated) | Run the existing validated apply pipeline on a `.sexp`/`.json` profile; `dry_run` only plans |
| `kicad_raw_move_footprint` | **high (raw)** | Move one footprint by ref directly over kipy; off by default |

Tool names and descriptions are English only (machine interface); server log
messages and results follow the project's bilingual gettext setup.

## Security model

- **Validated** tools (read + `apply_config`) are always available and go
  through the full project protection (`run_all_checks`, `check_board_identity`,
  registry, dependency order).
- **Raw** tools are **off by default**. They are registered only when enabled —
  either via the GUI's **Settings tab** ("MCP server" group, persisted to
  `gui_state.json`) or the `KICADSTAMP_MCP_ALLOW_RAW_WRITE=1` environment
  variable (env wins).
- Every raw write runs the **board-identity guard** first (`check_board_identity`)
  and always reports the connected board — the raw path is not a hole in the
  protection the apply path already has.
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
