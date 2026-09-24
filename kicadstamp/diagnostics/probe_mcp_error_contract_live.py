# kicadstamp/diagnostics/probe_mcp_error_contract_live.py
"""Live probe for the MCP error contract — §9's live run of
plan_2026_09_25_mcp_error_contract.

Read-only, no write tool is ever called: it drives
the REAL seam — mcp_server.server.build_server() with its own ConnectionManager and
a real kipy adapter — and prints exactly what an MCP client would receive.

Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_mcp_error_contract_live identity
               .venv/bin/python -m kicadstamp.diagnostics.probe_mcp_error_contract_live footprint J6

It lives with its siblings from the same two entries (probe_mcp_call_cost,
probe_document_switch, run_mcp_error_contract_mutations) rather than in the
gitignored root `diagnostics/`, where the first version of it landed: the report
cites it as the way to reproduce the live run, and a reproduction that is not in
git does not exist on the other two machines (canon rule 41).

The two phases the entry is about, run the same command twice:

  1. KiCad OPEN  -> a real answer, no traceback (board name + version; the
     footprint's LIVE position in mm, which is also the freshness property of the
     previous entry);
  2. KiCad CLOSED -> a deliberate ToolError carrying the project's own sentence
     ("KiCad is not answering — it is probably not running … Original error:
     Failed to connect to KiCad: …"), NEVER the SDK's
     `UnexpectedToolError('Error executing tool <name>')` crash wrapper and never a
     raw stack.

The verdict line at the end classifies what came out, so the run is evidence
rather than a screenshot: exit 0 = a clear answer or the expected deliberate
error, exit 1 = the defect (crash wrapper or a raw exception), exit 2 = usage.
"""
import asyncio
import sys
import time

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from mcp_server.server import build_server

_CALLS = {
    "identity": ("kicadstamp_get_board_identity", {}),
    "footprint": ("kicadstamp_get_footprint", None),   # ref from argv
    "footprints": ("kicadstamp_list_footprints", {}),
}


def _call(server, name, arguments):
    """Return (kind, payload): 'ok' with the content text, or the exception."""
    started = time.perf_counter()
    try:
        result = asyncio.run(server.call_tool(name, arguments))
    except BaseException as exc:            # noqa: BLE001 — the probe reports it
        return "exc", (exc, (time.perf_counter() - started) * 1000)
    text = "".join(getattr(c, "text", "") or "" for c in result.content)
    return "ok", (text, (time.perf_counter() - started) * 1000)


def main(argv) -> int:
    if not argv or argv[0] not in _CALLS:
        print(__doc__)
        return 2
    name, arguments = _CALLS[argv[0]]
    if arguments is None:
        if len(argv) < 2:
            print("usage: python -m kicadstamp.diagnostics.probe_mcp_error_contract_live footprint <REF>")
            return 2
        arguments = {"ref": argv[1]}

    server = build_server()          # the real factory: a live kipy adapter
    kind, payload = _call(server, name, arguments)

    print(f"tool : {name}")
    print(f"args : {arguments}")
    if kind == "ok":
        text, ms = payload
        print(f"ms   : {ms:.1f}")
        print(f"result: {text}")
        print("VERDICT: clear answer (live KiCad) — exit 0")
        return 0

    exc, ms = payload
    print(f"ms   : {ms:.1f}")
    print(f"raised: {type(exc).__module__}.{type(exc).__name__}")
    print(f"text  : {exc}")
    cause = exc.__cause__
    if cause is not None:
        print(f"cause : {type(cause).__module__}.{type(cause).__name__}: {cause}")
    if isinstance(exc, UnexpectedToolError):
        print("VERDICT: CRASH WRAPPER — the defect this entry fixed (text withheld, "
              "traceback in the server log) — exit 1")
        return 1
    if isinstance(exc, ToolError):
        print("VERDICT: deliberate ToolError with a sentence — expected when KiCad "
              "is closed — exit 0")
        return 0
    print("VERDICT: RAW EXCEPTION — the defect this entry fixed (no sentence, "
          "nothing to show the model) — exit 1")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
