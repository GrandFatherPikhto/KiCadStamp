# mcp_server/tools.py
"""MCP tool schemas + thin wrappers over handlers.

The only layer that touches the MCP SDK's argument model: each wrapper takes
the raw MCP arguments, calls the corresponding SDK-free handler (through the
ConnectionManager for adapter-backed tools, or directly for tools that open
their own path such as apply_config), and raises clean errors for user-facing
failures. Registered onto the MCPServer by server.build_server().
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import handlers
from .connection import ConnectionManager

# Tool names/descriptions stay English (machine interface for LLM clients) —
# i18n policy agreed in design doc §2.4.
_DESC_GET_BOARD_IDENTITY = (
    "Read the identity of the board currently open in KiCad: the board name "
    "(stem of the .kicad_pcb) and the KiCad version. Read-only, validated."
)
_DESC_LIST_FOOTPRINTS = (
    "List footprints on the board (ref, Role/Cluster fields, position in mm, "
    "rotation in degrees, layer). Optional ref_prefix filters by reference "
    "prefix (e.g. 'U' lists every U*). Read-only, validated."
)
_DESC_GET_FOOTPRINT = (
    "Get one footprint in detail: position/rotation/layer, Role/Cluster "
    "fields, its pads (number, net, position) and the nets on its pads. "
    "Read-only, validated. Fails clearly when the ref is not on the board."
)
_DESC_GET_SELECTION = (
    "What the PCB editor currently has selected (groups expanded): refs and "
    "uuids by kind. Useful to see what was clicked in the KiCad GUI. "
    "Read-only, validated."
)
_DESC_LIST_NETS = (
    "List all board net names. Read-only, validated."
)
_DESC_APPLY_CONFIG = (
    "Apply a KiCadStamp placement config through the existing VALIDATED path — "
    "the same as the CLI 'apply' and the GUI Redraw: full pre-validation "
    "(board identity, FORK-1, 'never guess silently'), registries and "
    "dependency ordering. config_path is the .sexp/.json profile (e.g. "
    "profiles/3ch-awg-tia-v103-test/3ch-awg-tia.sexp). With dry_run=true it "
    "only plans and reports — nothing is written. Returns the dry-run report "
    "or the run's log lines; fatal validation messages are returned verbatim."
)


def register_tools(server: MCPServer, manager: ConnectionManager) -> None:
    """Register the read-only and validated-write tools onto *server*.

    Every wrapper is a one-liner into handlers (through the manager where the
    shared adapter is needed), keeping the protocol layer thin and the logic
    SDK-free.
    """

    @server.tool(name="kicadstamp_get_board_identity", description=_DESC_GET_BOARD_IDENTITY)
    def _get_board_identity() -> dict:
        return manager.execute(handlers.get_board_identity)

    @server.tool(name="kicadstamp_list_footprints", description=_DESC_LIST_FOOTPRINTS)
    def _list_footprints(ref_prefix: str | None = None) -> list[dict]:
        return manager.execute(lambda a: handlers.list_footprints(a, ref_prefix=ref_prefix))

    @server.tool(name="kicadstamp_get_footprint", description=_DESC_GET_FOOTPRINT)
    def _get_footprint(ref: str) -> dict:
        result = manager.execute(lambda a: handlers.get_footprint(a, ref=ref))
        if result is None:
            raise ValueError(f"footprint {ref!r} not found on the board")
        return result

    @server.tool(name="kicadstamp_get_selection", description=_DESC_GET_SELECTION)
    def _get_selection() -> list[dict]:
        return manager.execute(handlers.get_selection)

    @server.tool(name="kicadstamp_list_nets", description=_DESC_LIST_NETS)
    def _list_nets() -> list[str]:
        return manager.execute(handlers.list_nets)

    # Validated write — opens its OWN apply pipeline (own kipy socket), so it
    # does not go through the shared manager.
    @server.tool(name="kicadstamp_apply_config", description=_DESC_APPLY_CONFIG)
    def _apply_config(config_path: str, dry_run: bool = False,
                      only: list[str] | None = None,
                      cluster: list[str] | None = None,
                      no_selection: bool = False, timeout_ms: int = 20000,
                      batch_size: int = 10, no_collision_check: bool = False,
                      collision_margin: float = 0.2) -> str:
        return handlers.apply_config(
            config_path=config_path, dry_run=dry_run, only=only, cluster=cluster,
            no_selection=no_selection, timeout_ms=timeout_ms, batch_size=batch_size,
            no_collision_check=no_collision_check, collision_margin=collision_margin,
        )
