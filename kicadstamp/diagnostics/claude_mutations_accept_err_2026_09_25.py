# kicadstamp/diagnostics/claude_mutations_accept_err_2026_09_25.py
"""Acceptance mutations for the MCP error-contract entry (plan_2026_09_25).

Deliberately DIFFERENT from the entry's own rig
(``run_mcp_error_contract_mutations.py``): where that one mostly REMOVES a
piece, these mostly SUBSTITUTE a look-alike, which is the failure a cell is
likeliest to miss. Three of them (K2, K4, K5) are expected to SURVIVE — they
probe claims the report makes about gaps, and a survivor there confirms the
report instead of contradicting it. A survivor that was expected to die, or a
death that was expected to survive, is the finding.

Run against a worktree at the entry's SHA, with the main checkout's
interpreter (a worktree has no .venv of its own, canon rule 41).
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get(
    "KICADSTAMP_ACCEPT_ROOT",
    "/home/denis/Projects/Python/KiCadStamp-accept-err"))


def _interpreter() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

# GUI-free targets only: tests/gui/conftest.py imports the GUI at module level,
# so a mutation that breaks a gui/* import kills pytest before collection
# (exit 4, zero FAILED lines) and nothing gets measured. Learned 2026-09-24.
T = ["tests/test_mcp_error_contract.py", "tests/test_mcp_connection.py",
     "tests/test_mcp_server.py"]

MUTATIONS = [
    # K1 — substitute a look-alike instead of removing: the tuple still has
    # THREE entries and still mentions ConnectionError, but kipy's class is
    # replaced by the built-in. The defect the entry fixed, restored quietly.
    ("K1 kipy class -> builtin look-alike", "mcp_server/connection.py",
     "            ConnectionError,\n            _get_kipy_connection_error(),",
     "            ConnectionError,\n            ConnectionError,",
     "die"),

    # K2 — reorder the two isinstance checks in _tool_error. The classes are
    # disjoint, so this MUST be harmless. A control: it proves the rig reports
    # survival honestly rather than calling everything a kill.
    ("K2 swap isinstance order", "mcp_server/tools.py",
     "            if isinstance(exc, ApiError):\n"
     "                raise ToolError(api_error_message(exc)) from exc\n"
     "            if isinstance(exc, KipyConnectionError):\n"
     "                raise ToolError(connection_error_message(exc)) from exc",
     "            if isinstance(exc, KipyConnectionError):\n"
     "                raise ToolError(connection_error_message(exc)) from exc\n"
     "            if isinstance(exc, ApiError):\n"
     "                raise ToolError(api_error_message(exc)) from exc",
     "survive"),

    # K3 — right class, WRONG text: answer an ApiError with the connection
    # message. Every cell that only checks "a ToolError came out" stays green;
    # only a cell that asserts the TEXT catches this.
    ("K3 ApiError answered with the wrong text", "mcp_server/tools.py",
     "                raise ToolError(api_error_message(exc)) from exc",
     "                raise ToolError(connection_error_message(exc)) from exc",
     "die"),

    # K4 — drop kipy's own words from the connection message, keep the advice.
    # The docstring claims BOTH halves matter ("neither is enough on its own").
    # Is that claim guarded, or only asserted?
    ("K4 drop the {e} diagnosis half", "kicadstamp/cli_common.py",
     "Start KiCad with the board open and try again. Original error: {e}\").format(e=e)",
     "Start KiCad with the board open and try again.\")",
     "unknown"),

    # K5 — convert the BUILT-IN ConnectionError too. The report calls this an
    # empty cell BY DECISION (finding 4). If it survives, that claim is true.
    ("K5 also convert the builtin", "mcp_server/tools.py",
     "            if isinstance(exc, KipyConnectionError):\n"
     "                raise ToolError(connection_error_message(exc)) from exc",
     "            if isinstance(exc, (KipyConnectionError, ConnectionError)):\n"
     "                raise ToolError(connection_error_message(exc)) from exc",
     "survive"),

    # K6 — keep api_error_message, but make AS_BUSY fall through to the generic
    # text. Narrower than the entry's rig (which replaced the whole call with
    # str(e)) and aimed squarely at the half worth having.
    ("K6 AS_BUSY loses its explanation", "kicadstamp/cli_common.py",
     "    if e.code == ApiStatusCode.AS_BUSY:",
     "    if False and e.code == ApiStatusCode.AS_BUSY:",
     "die"),

    # K7 — drop the BUILT-IN class from the tuple (the entry's own rig drops
    # kipy's). The plan insisted the built-in must stay; this checks that the
    # insistence is guarded and not just written down.
    ("K7 drop the builtin from the tuple", "mcp_server/connection.py",
     "            ConnectionError,\n            _get_kipy_connection_error(),",
     "            _get_kipy_connection_error(),",
     "die"),
]


def run(paths):
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400)
    return r.returncode, r.stdout


def main():
    print(f"корень: {ROOT}")
    print(f"{'мутация':<38} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 125)
    for name, rel, old, new, expect in MUTATIONS:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<38} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, out = run(T)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                # Zero red lines with a non-zero exit is a MISS, never a kill:
                # the mutation never reached the cell it aimed at (rule 38).
                verdict, detail = "ПРОМАХ", f"ноль красных при выходе {code}"
            else:
                verdict = "УБИТА"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:4])
            flag = ""
            if expect == "die" and verdict != "УБИТА":
                flag = "  <<< НАХОДКА: ждали смерти"
            if expect == "survive" and verdict == "УБИТА":
                flag = "  <<< НАХОДКА: ждали выживания"
            print(f"{name:<38} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
