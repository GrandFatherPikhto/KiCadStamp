"""Acceptance mutations for the project-is-a-directory entry, written by Claude.

Fuses kept from the house rigs (rule 38 — this file was built FROM
claude_mutations_accept_project_identity_2026_09_24.py, not from scratch, so that
the fuses come along):

  * a pattern that is NOT unique is REFUSED ("НЕДЕЙСТВИТЕЛЬНА"), never applied to
    a chance occurrence elsewhere in the file;
  * a run that collected nothing / printed no FAILED line is a MISS ("ПРОМАХ"),
    whatever the exit code — a mutation that never ran the cell it aimed at proved
    nothing, and counting it as a kill is the silent-miss failure of rule 38;
  * the verdict is per FULL test id, and the file is restored in `finally`;
  * zero red cells for a mutation means it SURVIVED ("ВЫЖИЛА") — itself a result
    to report, never something to fix by weakening a cell;
  * the bytecode cache is PURGED and writing forbidden on every run: mutations of
    equal length revert in the same second, and CPython would keep importing the
    MUTANT's .pyc (that is how the 24.09.2026 verdicts lied until the purge was
    added).

н2 AND н5 ARE DELIBERATELY DIFFERENT EDITS of the same idea (the plan's §7 allows
either wording), and the verdicts show why both exist:

  * н2 moves the registry store OUT to the parent — it kills Н3[registry] AND Н2,
    not Н3[registry] alone as this docstring first claimed: "points at the file
    that IS there" is false for an OLD profile too once the store walks up a
    level. Left in place rather than re-cut, because a mutation that reddens a
    neighbour is a FINDING, not a defect of the rig (rule 38: "красное не на той
    клетке — находка"). It leaves Н3[tracks]/Н3[overrides] green, which is the
    point of a parametrized row.
  * н5 keeps the store where it is and takes the STEM from the DIRECTORY instead
    of the config — it kills Н2 ONLY, because for a NEW project the directory name
    and the config stem coincide, so Н3 cannot see this change at all. That is the
    §4 trap in its most likely disguise, and the pair н2/н5 is the evidence that
    Н2 and Н3 are not two names for one cell.

Input        - nothing; needs the entry's working tree and its interpreter.
Expected     - one verdict line per mutation: УБИТА / ВЫЖИЛА / НЕДЕЙСТВИТЕЛЬНА /
               ПРОМАХ, with the cells that went red.
Live KiCad   - NO. Nothing here touches the board or the door.
Run          - KICADSTAMP_PYTHON=/path/to/main/.venv/bin/python \\
               /path/to/main/.venv/bin/python -m \\
               kicadstamp.diagnostics.claude_mutations_accept_project_dir_2026_09_24
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
    per machine, gitignored, never synced), so fall back to the one running this
    rig, then to an explicit override."""
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

CELLS = ["tests/test_project_is_a_directory.py", "tests/gui/test_root_metadata.py"]

# --- н1: the created config is a fixed "config.sexp", not the directory name -----
# Н1. Also kills Н5, whose row names the file (["Clean-Project.sexp"]): that is not
# a defect of the cell, the created NAME is part of "only the config file" too.
N1_OLD = """    return str(p / (p.name + ".sexp"))
"""
N1_NEW = """    return str(p / "config.sexp")
"""

# --- н2: a store lands in the PARENT of the project directory -------------------
# Н3, and only its [registry] row: tracks/overrides have their own rows, and a
# half-applied path rule is exactly what a parametrized cell is there to expose.
N2_OLD = """    return str(p.parent / "registry" / (p.stem + ".registry.json"))
"""
N2_NEW = """    return str(p.parent.parent / "registry" / (p.stem + ".registry.json"))
"""

# --- н3: creation silently overwrites an existing config ------------------------
# Н4 — the §4-adjacent destruction: the guard is removed, so the template is
# written OVER a real project's config and the current root is switched under it.
N3_OLD = """        if target.exists():
            message = _(
                "A project config already exists here: {path} — nothing was "
                "created. Open it instead, or choose another directory."
            ).format(path=target)
            self._show_message(message, _ERROR_STYLE)
            QMessageBox.warning(self, _("New project directory"), message)
            return
        try:
"""
N3_NEW = """        try:
"""

# --- н4: the empty infrastructure directories get pre-created -------------------
# Н5. The plausible "let me be helpful and lay out the project" edit — and the
# exact thing the 2026-09-11 decision removed the Files tab for.
N4_OLD = """            target.write_text("(kicadstamp-config)\\n", encoding="utf-8")
        except OSError as e:
"""
N4_NEW = """            target.write_text("(kicadstamp-config)\\n", encoding="utf-8")
            for _sub in ("logs", "registry", "tracks"):
                (target.parent / _sub).mkdir(exist_ok=True)
        except OSError as e:
"""

# --- н5: the stem of a store comes from the DIRECTORY, always -------------------
# Н2 — the plan's §4 trap in its most likely disguise ("let us unify the names"):
# an old config.sexp then looks for registry/<dir>.registry.json, which is not on
# disk, so the registry reads EMPTY and the next redraw doubles the copper. Н3
# stays GREEN under this mutation, which is the whole reason both cells exist.
N5_OLD = """    return str(p.parent / "registry" / (p.stem + ".registry.json"))
"""
N5_NEW = """    return str(p.parent / "registry" / (p.parent.name + ".registry.json"))
"""

MUTATIONS = [
    ("н1 config named config.sexp", "kicadstamp/utils/paths.py",
     N1_OLD, N1_NEW, CELLS),
    ("н2 store in the parent dir", "kicadstamp/utils/paths.py",
     N2_OLD, N2_NEW, CELLS),
    ("н3 silent overwrite", "gui/docks/root_metadata.py",
     N3_OLD, N3_NEW, CELLS),
    ("н4 pre-created infra dirs", "gui/docks/root_metadata.py",
     N4_OLD, N4_NEW, CELLS),
    ("н5 stem from the directory", "kicadstamp/utils/paths.py",
     N5_OLD, N5_NEW, CELLS),
]


def run(paths):
    """Run the aimed cells with the bytecode cache PURGED and writing forbidden.

    THE FUSE, bought the hard way on 24.09.2026 (see the identity rig's docstring):
    a mutation that reverts to the same source size within the same second keeps a
    VALID-looking .pyc, and the next run imports the MUTANT after the source is
    restored. Purging before every run costs milliseconds; a wrong verdict costs
    the whole entry.
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
                # exactly its row, and the difference is lost behind a cap.
                names = "; ".join(line.split(" ")[1].split("::", 1)[-1]
                                  for line in failed)
                print(f"{name:<34} {'УБИТА':<16} {len(failed)} красных: {names}")
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
