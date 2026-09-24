# mcp_server/tools.py
"""MCP tool schemas + thin wrappers over handlers.

The only layer that touches the MCP SDK's argument model: each wrapper takes
the raw MCP arguments, calls the corresponding SDK-free handler (through the
ConnectionManager for adapter-backed tools, or directly for tools that open
their own path such as apply_config), and raises clean errors for user-facing
failures. Registered onto the MCPServer by server.build_server().
"""

from __future__ import annotations

from functools import wraps

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kicadstamp.cli_common import api_error_message, connection_error_message
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.exceptions import PlacerError
from kicadstamp.i18n import _

from . import handlers
from .connection import ConnectionManager


def _tool_error(fn):
    """Convert deliberate user-facing failures into MCP ``ToolError``.

    Three families are "the tool is telling the user something", and each gets
    the project's EXISTING words for it rather than a new text:

      * PlacerError (base of ValidationError/BoardNotFoundError/... — every
        fatal the project raises deliberately with an informative message) and
        ValueError (e.g. "footprint not found"): the message IS the answer, so it
        is passed through unchanged;
      * kipy's ``ApiError`` — "we did reach KiCad and it refused": a busy KiCad
        (AS_BUSY), a machine with no PCB document open at all (kipy's
        ``get_board()`` raises "Expected to be able to retrieve at least one
        board"), an IPC timeout. It is NOT a PlacerError, so before this it left
        as a crash and the client saw only mcp's "unexpected crash" wrapper. The
        text comes from :func:`kicadstamp.cli_common.api_error_message`, the ONE
        message the CLI and fourteen GUI call sites already use — including its
        long AS_BUSY explanation, whose usual real-world cause is an unfinished
        tool in the KiCad GUI (the easiest of all failures to misread as a hang);
      * kipy's ``ConnectionError`` — "there was no answer at all": KiCad is not
        running, or the IPC link died while the call was in flight. It is a bare
        Exception subclass that SHADOWS the built-in name inside kipy, which is
        why a reconnect tuple built from the built-in one catches nothing (see
        mcp_server/connection.py). This is the case the entry grew from — close
        KiCad, call any tool — and on that path it is the SECOND failure, not the
        first: the seam has already retried once by the time it arrives here.
        Text: :func:`kicadstamp.cli_common.connection_error_message`.

    The BUILT-IN ``ConnectionError`` is deliberately NOT converted here. It
    belongs to the seam's retry (the reconnect tuple holds both classes, because
    Python raises the built-in one on OS-level socket errors); a built-in one
    reaching this wrapper means the retry itself failed with an OS-level error,
    which plan_2026_09_25_mcp_error_contract does not cover. Kept as a named empty
    cell rather than widened silently.

    Anything else is a real bug and propagates unchanged, so it stays
    distinguishable as a crash: mcp wraps it as ``UnexpectedToolError``, whose
    message is only "Error executing tool <name>", and logs the traceback.
    Widening this to ``except Exception`` would erase the "bug / deliberate
    fatal" boundary the project's exit codes are built on (cell Э4 of
    plan_2026_09_25_mcp_error_contract pins that boundary).
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (PlacerError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            # kipy's classes are imported lazily, in the same form as run_cli
            # (kicadstamp/cli_common.py): only an actual IPC failure pays for the
            # kipy import chain.
            from kipy.errors import ApiError
            from kipy.errors import ConnectionError as KipyConnectionError

            if isinstance(exc, ApiError):
                raise ToolError(api_error_message(exc)) from exc
            if isinstance(exc, KipyConnectionError):
                raise ToolError(connection_error_message(exc)) from exc
            raise

    return wrapper

# Tool names/descriptions stay English (machine interface for LLM clients) —
# i18n policy agreed in design doc §2.4.
_DESC_GET_BOARD_IDENTITY = (
    "Read the identity of the board currently open in KiCad: the board name "
    "(stem of the .kicad_pcb), the PROJECT it belongs to (name + directory) and "
    "the KiCad version. Read-only, validated."
)
_DESC_LIST_FOOTPRINTS = (
    "List footprints on the board (ref, Role/Cluster fields, position in mm, "
    "rotation in degrees, layer). Optional ref_prefix filters by reference "
    "prefix (e.g. 'U' lists every U*). Answers with {'board': {board_name, "
    "project}, 'footprints': [...]} — the board is named even when the list is "
    "EMPTY, so 'nothing matched' can never be mistaken for an answer about some "
    "other board. Read-only, validated."
)
_DESC_GET_FOOTPRINT = (
    "Get one footprint in detail: position/rotation/layer, Role/Cluster "
    "fields, its pads (number, net, position) and the nets on its pads. "
    "Answers with {'board': {board_name, project}, 'footprint': {...}} — the "
    "board is named in the answer, so a confident-looking position is never "
    "attributable to the wrong board. Read-only, validated. Fails clearly when "
    "the ref is not on the board."
)
_DESC_GET_SELECTION = (
    "What the PCB editor currently has selected (groups expanded): refs and "
    "uuids by kind. Useful to see what was clicked in the KiCad GUI. "
    "Read-only, validated."
)
_DESC_LIST_NETS = (
    "List all board net names. Read-only, validated."
)
_DESC_GET_ITEMS_BY_UUID = (
    "Resolve a list of board item uuids to detailed records (tracks, vias, "
    "footprints). Each requested uuid appears exactly once in the result: "
    "found items get their full detail (kind, net, layer, mm coordinates); "
    "items not on the board return {'kind': None, 'found': False} — never "
    "raising and never silently skipping. Answers with {'board': {board_name, "
    "project}, 'items': [...]} — the board is named even when EVERY uuid is "
    "missing, so a false 'not found' cannot pass for a legitimate answer. "
    "Prefer this over listing the whole board when the uuids already come from "
    "kicadstamp_get_selection. Read-only, validated."
)
_DESC_LIST_TRACKS = (
    "List track segments with optional net and/or layer filters (e.g. "
    "net='GND', layer='F.Cu'; layer matches the string layer name). On a "
    "large board this can be large — prefer the net/layer filters, or "
    "kicadstamp_get_items_by_uuid when you already have uuids from "
    "kicadstamp_get_selection. Read-only, validated."
)
_DESC_LIST_VIAS = (
    "List vias with an optional net filter (e.g. net='GND'). On a large "
    "board this can be large — prefer the net filter, or "
    "kicadstamp_get_items_by_uuid when you already have uuids from "
    "kicadstamp_get_selection. Read-only, validated."
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
    @_tool_error
    def _get_board_identity() -> dict:
        return manager.execute(handlers.get_board_identity)

    @server.tool(name="kicadstamp_list_footprints", description=_DESC_LIST_FOOTPRINTS)
    @_tool_error
    def _list_footprints(ref_prefix: str | None = None) -> dict:
        # ONE manager.execute for the WHOLE answer: the envelope and the payload
        # must describe the SAME instant. A second execute would refresh the board
        # in between, reproducing by hand the very race this envelope is about (the
        # board can be switched at any moment — М8).
        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "footprints": handlers.list_footprints(a, ref_prefix=ref_prefix),
        })

    @server.tool(name="kicadstamp_get_footprint", description=_DESC_GET_FOOTPRINT)
    @_tool_error
    def _get_footprint(ref: str) -> dict:
        def _build(a) -> dict:
            # The "not found" fatal is raised INSIDE the single execute, for the same
            # reason the envelope is built there: one refresh, one instant. It travels
            # out unchanged and still reaches the client as a deliberate ToolError.
            result = handlers.get_footprint(a, ref=ref)
            if result is None:
                raise ValueError(
                    _("footprint {ref!r} not found on the board").format(ref=ref))
            return {"board": handlers.board_brief(a), "footprint": result}

        return manager.execute(_build)

    @server.tool(name="kicadstamp_get_selection", description=_DESC_GET_SELECTION)
    @_tool_error
    def _get_selection() -> list[dict]:
        return manager.execute(handlers.get_selection)

    @server.tool(name="kicadstamp_list_nets", description=_DESC_LIST_NETS)
    @_tool_error
    def _list_nets() -> list[str]:
        return manager.execute(handlers.list_nets)

    @server.tool(name="kicadstamp_get_items_by_uuid", description=_DESC_GET_ITEMS_BY_UUID)
    @_tool_error
    def _get_items_by_uuid(uuids: list[str]) -> dict:
        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "items": handlers.get_items_by_uuid(a, uuids=uuids),
        })

    @server.tool(name="kicadstamp_list_tracks", description=_DESC_LIST_TRACKS)
    @_tool_error
    def _list_tracks(net: str | None = None, layer: str | None = None) -> list[dict]:
        return manager.execute(lambda a: handlers.list_tracks(a, net=net, layer=layer))

    @server.tool(name="kicadstamp_list_vias", description=_DESC_LIST_VIAS)
    @_tool_error
    def _list_vias(net: str | None = None) -> list[dict]:
        return manager.execute(lambda a: handlers.list_vias(a, net=net))

    # Validated write — opens its OWN apply pipeline (own kipy socket), so it
    # does not go through the shared manager.
    @server.tool(name="kicadstamp_apply_config", description=_DESC_APPLY_CONFIG)
    @_tool_error
    def _apply_config(config_path: str, dry_run: bool = False,
                      only: list[str] | None = None,
                      cluster: list[str] | None = None,
                      no_selection: bool = False, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                      batch_size: int = 10, no_collision_check: bool = False,
                      collision_margin: float = 0.2) -> str:
        return handlers.apply_config(
            config_path=config_path, dry_run=dry_run, only=only, cluster=cluster,
            no_selection=no_selection, timeout_ms=timeout_ms, batch_size=batch_size,
            no_collision_check=no_collision_check, collision_margin=collision_margin,
        )


# --- Raw write tools (HIGH RISK) --------------------------------------------
# Registered ONLY when KICADSTAMP_MCP_ALLOW_RAW_WRITE=1 (see server.build_server()).

_DESC_RAW_MOVE_FOOTPRINT = (
    "RAW (high-risk) write — bypasses the validated config layer entirely. "
    "Moves one footprint by ref to an absolute position/rotation directly over "
    "kipy, without registry/FORK-1/dependency-order protection. Only registered "
    "when KICADSTAMP_MCP_ALLOW_RAW_WRITE=1. The board-identity guard ALWAYS "
    "runs before writing: expected_board_name (REQUIRED) is the board you "
    "expect to be open, and the write is refused when a different board is "
    "open in KiCad; the connected board is always reported. Confirm carefully "
    "— the host's permission gate decides."
)


def register_raw_tools(server: MCPServer, manager: ConnectionManager) -> None:
    """Register the raw (env-gated) write tools onto *server*."""

    @server.tool(name="kicad_raw_move_footprint", description=_DESC_RAW_MOVE_FOOTPRINT)
    @_tool_error
    def _raw_move_footprint(ref: str, x_mm: float, y_mm: float,
                            expected_board_name: str,
                            rotation_deg: float | None = None) -> dict:
        return manager.execute(lambda a: handlers.raw_move_footprint(
            a, ref=ref, x_mm=x_mm, y_mm=y_mm,
            expected_board_name=expected_board_name, rotation_deg=rotation_deg))
