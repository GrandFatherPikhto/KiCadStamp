# kicadstamp/diagnostics/claude_mutations_accept_pi_2026_09_24.py
"""Acceptance mutations for the project-identity entry (plan_2026_09_24).

Aimed AWAY from the entry's own rig: that one removes or renames pieces, these
attack the two CONDITIONS the route-Г envelope was accepted on — "assembled
inside ONE execute" and "carries nothing that costs a round trip" — plus the
empty-answer hole the route was chosen to close. Two are declared expected to
survive, so the rig proves it reports survival honestly.

The bytecode guard is the entry's own finding (2026-09-24): a same-length
mutation restored within the same second leaves CPython convinced the mutant's
.pyc is still valid, and the run then reports reds that no longer exist on disk.
"""
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get(
    "KICADSTAMP_ACCEPT_ROOT",
    "/home/denis/Projects/Python/KiCadStamp-accept-pi"))


def _interpreter() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    return os.environ.get("KICADSTAMP_PYTHON") or sys.executable


PY_BIN = _interpreter()
T = ["tests/test_board_project_identity.py", "tests/test_mcp_handlers.py",
     "tests/test_mcp_server.py", "tests/test_mcp_board_freshness.py"]

MUTATIONS = [
    # L1 — the race: two executes instead of one. Each refreshes the board, so
    # the envelope and the payload can describe DIFFERENT instants. This is the
    # condition route Г was accepted on; if it survives, the condition is
    # unguarded and the cure can rot silently.
    ("L1 envelope in a SECOND execute", "mcp_server/tools.py",
     """        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "footprints": handlers.list_footprints(a, ref_prefix=ref_prefix),
        })""",
     """        board = manager.execute(handlers.board_brief)
        return {"board": board,
                "footprints": manager.execute(
                    lambda a: handlers.list_footprints(a, ref_prefix=ref_prefix))}""",
     "die"),

    # L2 — the other condition: put the KiCad version in the envelope. It is a
    # real IPC round trip, so this hangs a network call on every enveloped call.
    ("L2 version rides in the envelope", "mcp_server/handlers.py",
     """    return {
        "board_name": adapter.get_board_filename(),
        "project": _project_payload(adapter),
    }""",
     """    return {
        "board_name": adapter.get_board_filename(),
        "project": _project_payload(adapter),
        "kicad_version": adapter.get_version(),
    }""",
     "die"),

    # L3 — sign only non-empty answers. The quietest form of the defect: an
    # empty list on the wrong board reads as a legitimate "nothing matched".
    ("L3 empty answers go unsigned", "mcp_server/tools.py",
     """        return manager.execute(lambda a: {
            "board": handlers.board_brief(a),
            "footprints": handlers.list_footprints(a, ref_prefix=ref_prefix),
        })""",
     """        def _b(a):
            fps = handlers.list_footprints(a, ref_prefix=ref_prefix)
            return {"footprints": fps} if not fps else {
                "board": handlers.board_brief(a), "footprints": fps}
        return manager.execute(_b)""",
     "die"),

    # L4 — a plausible typo: path takes the name. The signature still LOOKS
    # complete; only a cell that reads the path apart from the name notices.
    ("L4 project path := project name", "mcp_server/handlers.py",
     '    return {"name": project[0], "path": project[1]}',
     '    return {"name": project[0], "path": project[0]}',
     "die"),

    # L5 — "not project" -> "project is None". An EMPTY tuple then passes and
    # becomes {"name": "", "path": ""} instead of None. Declared unknown: the
    # plan asked for defensive behaviour but never said which of the two shapes
    # an absent project must take.
    ("L5 empty project becomes empty strings", "mcp_server/handlers.py",
     "    if not project:\n        return None",
     "    if project is None:\n        return None",
     "unknown"),

    # L6 — control: reorder the two envelope keys. JSON objects are unordered
    # and every cell compares by value, so this MUST be harmless.
    ("L6 swap the two envelope keys", "mcp_server/handlers.py",
     """    return {
        "board_name": adapter.get_board_filename(),
        "project": _project_payload(adapter),
    }""",
     """    return {
        "project": _project_payload(adapter),
        "board_name": adapter.get_board_filename(),
    }""",
     "survive"),
]


def _purge_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run(paths):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400, env=env)
    return r.returncode, r.stdout


def main():
    print(f"корень: {ROOT}")
    print(f"{'мутация':<36} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 122)
    for name, rel, old, new, expect in MUTATIONS:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<36} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
            continue
        try:
            _purge_bytecode()
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, out = run(T)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                verdict, detail = "ПРОМАХ", f"ноль красных при выходе {code}"
            else:
                verdict = "УБИТА"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:3])
            flag = ""
            if expect == "die" and verdict != "УБИТА":
                flag = "  <<< НАХОДКА: ждали смерти"
            if expect == "survive" and verdict == "УБИТА":
                flag = "  <<< НАХОДКА: ждали выживания"
            print(f"{name:<36} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _purge_bytecode()


if __name__ == "__main__":
    main()
