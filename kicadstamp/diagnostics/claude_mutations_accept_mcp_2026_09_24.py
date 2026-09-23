"""Acceptance mutations for 0984ff8 (MCP freshness), written by Claude.
Fuses kept from the house rigs: a pattern that is not unique is REFUSED,
a run that collected nothing is a MISS, the verdict is per full test id.
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
def _interpreter() -> str:
    """The project interpreter. A git WORKTREE has no .venv of its own and must
    not grow one (canon rule 41: .venv is per machine, gitignored, never synced),
    so fall back to the one running this rig, then to the main checkout's."""
    import os
    import sys
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

REFRESH = "                if not just_loaded:\n                    refresh_board_before_live_read(adapter)\n"
FRESH_T = ["tests/test_mcp_board_freshness.py"]
BOTH = ["tests/test_mcp_board_freshness.py", "tests/test_mcp_connection.py"]

MUTATIONS = [
    ("B1 no refresh in execute", "mcp_server/connection.py", REFRESH, "", FRESH_T),
    ("B2 refresh unconditionally", "mcp_server/connection.py", REFRESH,
     "                refresh_board_before_live_read(adapter)\n", BOTH),
    ("B3 refresh OUTSIDE the try", "mcp_server/connection.py",
     "            try:\n" + REFRESH,
     "            if not just_loaded:\n                refresh_board_before_live_read(adapter)\n            try:\n",
     FRESH_T),
    ("B4 just_loaded always False", "mcp_server/connection.py",
     "            return adapter, True\n", "            return adapter, False\n", BOTH),
    ("B5 drop the gui re-export", "gui/worker.py",
     "from kicadstamp.board_freshness import (  # noqa: F401  (re-export, see above)\n    refresh_board_before_live_read,",
     "from kicadstamp.board_freshness import (  # noqa: F401\n    _absent_on_purpose,",
     # NOT tests/gui/*: that package's conftest imports the GUI at module level,
     # so a mutation that breaks `gui/worker.py`'s import kills pytest BEFORE
     # collection (exit 4, zero FAILED lines) and the rig can only call it a
     # MISS. Aimed at a GUI-free file the same mutant reddens the intended cell
     # by id. Found by the Daemon while running this rig, 2026-09-24.
     ["tests/test_mcp_board_freshness.py"]),
]


def run(paths):
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400)
    return r.returncode, r.stdout


def main():
    print(f"{'мутация':<34} {'вердикт':<16} что покраснело")
    print("-" * 110)
    for name, rel, old, new, targets in MUTATIONS:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<34} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, out = run(targets)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code == 0:
                print(f"{name:<34} {'ВЫЖИЛА':<16} ни один сторож не покраснел")
            elif not failed or "no tests ran" in out:
                # ZERO red lines with a NON-ZERO exit is a MISS, never a kill —
                # whatever the exit code. A mutation that broke the import (or
                # collection) never ran the cell it aimed at, so it proved
                # nothing. Counting it as a kill is the silent-miss failure of
                # rule 38, and this rig printed exactly that until the fuse was
                # widened from "code == 2" to "no FAILED line at all".
                print(f"{name:<34} {'ПРОМАХ':<16} ноль красных при выходе {code} — замер не состоялся")
            else:
                names = "; ".join(l.split(" ")[1].split("::")[-1] for l in failed[:5])
                print(f"{name:<34} {'УБИТА':<16} {len(failed)} красных: {names}")
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
