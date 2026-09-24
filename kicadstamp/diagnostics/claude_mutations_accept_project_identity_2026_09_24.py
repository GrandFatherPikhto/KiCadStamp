"""Acceptance mutations for the project-identity entry, written by Claude.

Fuses kept from the house rigs (rule 38 — this file was built FROM
claude_mutations_accept_mcp_2026_09_24.py, not from scratch, precisely so that the
fuses come along):

  * a pattern that is NOT unique is REFUSED ("НЕДЕЙСТВИТЕЛЬНА"), never applied to a
    chance occurrence elsewhere in the file;
  * a run that collected nothing / printed no FAILED line is a MISS ("ПРОМАХ"),
    whatever the exit code — a mutation that never ran the cell it aimed at proved
    nothing, and counting it as a kill is the silent-miss failure of rule 38;
  * the verdict is per FULL test id, and the file is restored in `finally`;
  * zero red cells for a mutation means it SURVIVED ("ВЫЖИЛА") — which is itself a
    result to report, never something to fix by weakening the cell.

MUTATION m4 IS NOT THE ONE THE PLAN WROTE. The plan proposed "read through
`Board.get_project()` instead of `document`". Statically checked 24.09.2026, that
mutation costs NOTHING: `Board.get_project()` is local (`kipy/board.py:276-278` ->
`kipy/project.py:32-39`, which only copies the specifier and rewrites its own `type`),
so it adds no kipy round trip at all and П4 would stay green — a mutation that survives
by construction, which is a MISS dressed as a proof. m4 below is the mutation П4
really protects against: ask the LIVE document list instead of the stored specifier.
That is a plausible thing to write ("the stored one may be stale, let me ask the live
one"), it FIXES the staleness the entry documented, and it puts a network round trip on
every call of every enveloped tool.

Input        - nothing; needs the entry's working tree and its interpreter.
Expected     - one verdict line per mutation: УБИТА / ВЫЖИЛА / НЕДЕЙСТВИТЕЛЬНА /
               ПРОМАХ, with the cells that went red.
Live KiCad   - NO.
Run          - /path/to/main/.venv/bin/python -m kicadstamp.diagnostics.\\
               claude_mutations_accept_project_identity_2026_09_24
"""
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _interpreter() -> str:
    """The project interpreter.

    A git WORKTREE has no .venv of its own and must not grow one (rule 41: .venv is
    per machine, gitignored, never synced), so fall back to the one running this rig,
    then to an explicit override."""
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

IDENTITY_T = ["tests/test_board_project_identity.py"]
IDENTITY_AND_HANDLERS = ["tests/test_board_project_identity.py",
                         "tests/test_mcp_handlers.py"]

# --- m1: the getter answers the project NAME only, without the directory ---------
# П1, and the empty-directory row specifically: "proj" alone is half an identity, and
# the defensive branch exists because a half-empty project is the case the design
# could not reproduce live.
M1_OLD = """        if not name or not path:
            return None
        return (name, path)
"""
M1_NEW = """        if not name:
            return None
        return (name, "")
"""

# --- m2: the board name dropped from ONE of the three reading tools --------------
# П2, and the reason the cell checks all three: a HALF-applied remedy is worse than
# none, because it creates false trust. Aimed at get_items_by_uuid only.
M2_OLD = """        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "items": handlers.get_items_by_uuid(a, uuids=uuids),
        })
"""
M2_NEW = """        return manager.execute(lambda a: {
            "items": handlers.get_items_by_uuid(a, uuids=uuids),
        })
"""

# --- m3: no board raises instead of answering "nothing" --------------------------
# П3. The two lines are given together because `if self._board is None: return None`
# alone occurs twice in adapter.py (get_board_filename and get_board_project), and a
# non-unique pattern must be REFUSED rather than applied to the wrong twin.
M3_OLD = """        if self._board is None:
            return None
        project = self._board.document.project
"""
M3_NEW = """        if self._board is None:
            raise RuntimeError("no board")
        project = self._board.document.project
"""

# --- m4: read the identity from the LIVE document list, not the stored specifier --
# П4. See the docstring: this replaces the plan's m4, which could not cost anything.
M4_OLD = """        project = self._board.document.project
        name, path = project.name, project.path
"""
M4_NEW = """        from kipy.proto.common.types import DocumentType
        live = self._kicad.get_open_documents(DocumentType.DOCTYPE_PCB)[0]
        name, path = live.project.name, live.project.path
"""

# --- m5: lift `import kipy` to the top of the seam -------------------------------
# П5 — an EXISTING guard (test_importing_the_seam_does_not_pull_kipy), re-run rather
# than duplicated under a new number.
M5_OLD = "from kicadstamp.board_freshness import refresh_board_before_live_read\n"
M5_NEW = ("import kipy  # mutation: the seam must stay kipy-free at import\n"
          "from kicadstamp.board_freshness import refresh_board_before_live_read\n")

# --- m6: rename a pre-existing key of the identity answer ------------------------
# П6 — clients read these names, which is exactly why the cell pins them.
M6_OLD = """        "connected": board_name is not None,
        "board_name": board_name,
"""
M6_NEW = """        "connected": board_name is not None,
        "board_file": board_name,
"""

# --- m7: assemble the envelope in a SECOND execute (ДОПОЛНЕНИЕ 1, Т4) ------------
# The text is taken VERBATIM from the acceptance rig
# (kicadstamp/diagnostics/claude_mutations_accept_pi_2026_09_24.py, row L1), not
# reinvented: the addition was written after that rig's L1 SURVIVED every cell of this
# entry, and the point is to close exactly that hole. Two executes = two refreshes, so
# the envelope and the payload can describe DIFFERENT instants - the race route Г was
# accepted on, and П7 is the cell that has to see it.
M7_OLD = """        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "footprints": handlers.list_footprints(a, ref_prefix=ref_prefix),
        })"""
M7_NEW = """        board = manager.execute(handlers.board_brief)
        return {"board": board,
                "footprints": manager.execute(
                    lambda a: handlers.list_footprints(a, ref_prefix=ref_prefix))}"""

# --- m8: `if not project` -> `if project is None` (ДОПОЛНЕНИЕ 1, Т5) ---------------
# Also verbatim from the acceptance rig (row L5). On the REACHABLE input the two agree,
# which is why the Т5 cell pins the empty container: that is where the difference shows,
# and the death must be an assertion about the payload rather than an IndexError.
M8_OLD = "    if not project:\n        return None"
M8_NEW = "    if project is None:\n        return None"

MUTATIONS = [
    ("m1 project name only", "kicadstamp/kicad/adapter.py",
     M1_OLD, M1_NEW, IDENTITY_T),
    ("m2 envelope dropped from 1 of 3", "mcp_server/tools.py",
     M2_OLD, M2_NEW, IDENTITY_T),
    ("m3 no board raises", "kicadstamp/kicad/adapter.py",
     M3_OLD, M3_NEW, IDENTITY_T),
    ("m4 identity from the live list", "kicadstamp/kicad/adapter.py",
     M4_OLD, M4_NEW, IDENTITY_T),
    ("m5 top-level import kipy in seam", "mcp_server/connection.py",
     M5_OLD, M5_NEW, ["tests/test_mcp_error_contract.py"]),
    ("m6 board_name renamed", "mcp_server/handlers.py",
     M6_OLD, M6_NEW, IDENTITY_AND_HANDLERS),
    ("m7 envelope in a 2nd execute", "mcp_server/tools.py",
     M7_OLD, M7_NEW, IDENTITY_T),
    ("m8 absent project is None", "mcp_server/handlers.py",
     M8_OLD, M8_NEW, IDENTITY_T),
]


def run(paths):
    """Run the aimed tests with the bytecode cache PURGED and writing forbidden.

    THE FUSE, and it was bought the hard way on 24.09.2026. Mutation m6 renames
    `"board_name"` to `"board_file"` — the SAME length — and the revert lands in the
    same second, so CPython's pyc check (source mtime in whole seconds + source size)
    finds the MUTANT's cache still valid and keeps importing it after the source is
    restored. The full suite then reported two red cells that had nothing to do with
    the code on disk. This is the same genre of silent lie as a non-unique pattern: it
    does not crash, it answers — so the rig must not leave it standing. Purging before
    every run costs milliseconds; a wrong verdict costs the whole entry.
    """
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400, env=env)
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
            failed = [line for line in out.splitlines() if line.startswith("FAILED")]
            if code == 0:
                print(f"{name:<34} {'ВЫЖИЛА':<16} ни один сторож не покраснел")
            elif not failed or "no tests ran" in out:
                # ZERO red lines with a NON-ZERO exit is a MISS, never a kill —
                # whatever the exit code. A mutation that broke an import (or
                # collection) never ran the cell it aimed at.
                print(f"{name:<34} {'ПРОМАХ':<16} ноль красных при выходе {code} — "
                      f"замер не состоялся")
            else:
                # ALL red cells, not the first few: a mutation that kills its own
                # cell AND neighbours is a different finding from one that kills
                # exactly its row, and the difference is lost behind a [:6] cap.
                names = "; ".join(line.split(" ")[1].split("::")[-1]
                                  for line in failed)
                print(f"{name:<34} {'УБИТА':<16} {len(failed)} красных: {names}")
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
