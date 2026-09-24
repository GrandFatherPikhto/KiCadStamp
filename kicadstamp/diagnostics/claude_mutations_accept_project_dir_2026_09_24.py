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

THE ENTRY HAS TWO STEPS, AND THE RIG HAS A ROW FOR BOTH. Step 1 (Denis: "создаётся
проект директорией") is н1/н2/н5. Step 2 (Denis, same day: "В File будет «Создать
проект»... автоматически создаётся директория с нужной инфраструктурой") added the
dialog, the File menu and the five infrastructure directories — н3/н4/н6/н7/н8/н9.

н4 IS THE FLIPPED ONE, and it is the whole reason the flip must be visible here: the
old requirement forbade pre-created infrastructure, so the mutation was "helpfully
create the directories". Denis reversed it on 2026-09-24, so the mutation is now the
opposite — "skip the loop" — and a cell that still asserted "only the config file"
would have gone RED on correct code. A guard is only meaningful against the
requirement it was written for.

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

CORE = ["tests/test_project_is_a_directory.py"]
DIALOG = ["tests/gui/test_create_project_dialog.py"]
DOCK = ["tests/gui/test_root_metadata.py"]
MENU = ["tests/gui/test_main_window.py"]
CREATION = CORE + DIALOG + DOCK

# --- н1: the created config is a fixed "config.sexp", not the project name -------
# Kills the name cells AND, because a listing names the file, the whole-listing row:
# that is expected, not a defect of the cell.
N1_OLD = """    return str(p / (p.name + ".sexp"))
"""
N1_NEW = """    return str(p / "config.sexp")
"""

# --- н2: a store lands in the PARENT of the project directory -------------------
# Its own row [registry] plus Н2, because "points at the file that IS there" is
# false for an OLD profile too once the store walks up a level. Left as measured
# (rule 38: a neighbour going red is a finding, not a rig defect).
N2_OLD = """    return str(p.parent / "registry" / (p.stem + ".registry.json"))
"""
N2_NEW = """    return str(p.parent.parent / "registry" / (p.stem + ".registry.json"))
"""

# --- н3: create_project("tak") overwrites instead of refusing -------------------
# Kills the core refusal cell and the dialog's refusal cell — the §4-adjacent
# destruction, and the row that must stay red now that the guard belongs to the
# widget and to the setup module rather than to the dock.
N3_OLD = """    config = Path(project_config_path_for_dir(project_dir))
    if config.exists():
        raise ProjectConfigExists(config)
"""
N3_NEW = """    config = Path(project_config_path_for_dir(project_dir))
"""

# --- н4: THE FLIPPED ROW — the infrastructure loop is skipped -------------------
# Step 1 asserted exactly the opposite. Denis reversed the requirement on
# 2026-09-24 ("автоматически создаётся директория с нужной инфраструктурой"), so
# now the OMISSION is the bug and this mutation must die on three cells: the
# whole-listing row, the per-directory row [logs], and the end-to-end dialog row.
N4_OLD = """    for name in PROJECT_INFRA_DIRS:
        (config.parent / name).mkdir(exist_ok=True)
"""
N4_NEW = ""

# --- н5: the stem of a store comes from the DIRECTORY, always -------------------
# Н2 — the plan's §4 trap in its most likely disguise ("let us unify the names"):
# an old config.sexp then looks for registry/<dir>.registry.json, which is not on
# disk, so the registry reads EMPTY and the next redraw doubles the copper. Н3
# stays GREEN under this mutation, which is the whole reason both cells exist.
N5_OLD = """    return str(p.parent / "registry" / (p.stem + ".registry.json"))
"""
N5_NEW = """    return str(p.parent / "registry" / (p.parent.name + ".registry.json"))
"""

# --- н6: the dialog stops validating the folder ---------------------------------
# "mkdir(parents=True) will make it anyway" is the plausible slip, and it silently
# turns a typo in the Folder field into a new tree of directories. Kills the
# missing-folder row.
N6_OLD = """        if not Path(folder).is_dir():
            QMessageBox.warning(self, _("Create Project"),
                                _("Pick an existing folder first."))
            return
"""
N6_NEW = ""

# --- н7: the dialog stops refusing a name with a path separator -----------------
# The escape hatch this closes: a name like "../../somewhere/Proj" walking out of
# the folder the user picked. Kills the separator row, which calls _on_ok DIRECTLY
# (the disabled button is not the guard).
N7_OLD = """        name, folder = self._project_text(), self._folder_text()
        if not name or "/" in name or "\\\\" in name:
"""
N7_NEW = """        name, folder = self._project_text(), self._folder_text()
        if not name:
"""

# --- н8: File > Recent project stops reading the dock's list --------------------
# A frozen/emptied view of recent_root_files. Kills the rebuilt-from-the-list row;
# the empty-list row still passes, which is the point of having both.
N8_OLD = """        self.recent_project_menu.clear()
        recent = settings.state.get("recent_root_files", [])
"""
N8_NEW = """        self.recent_project_menu.clear()
        recent = []
"""

# --- н9: the infrastructure list quietly loses a row ----------------------------
# Exactly what the drift cell exists to catch: a project then comes out missing a
# directory that a consumer will write into. Kills the drift cell, the whole-listing
# row and the per-directory row [logs].
N9_OLD = 'PROJECT_INFRA_DIRS = ("registry", "tracks", "logs", "overrides", "operational")'
N9_NEW = 'PROJECT_INFRA_DIRS = ("registry", "tracks", "overrides", "operational")'

MUTATIONS = [
    ("н1 config named config.sexp", "kicadstamp/utils/paths.py",
     N1_OLD, N1_NEW, CREATION),
    ("н2 store in the parent dir", "kicadstamp/utils/paths.py",
     N2_OLD, N2_NEW, CREATION),
    ("н3 create_project overwrites", "kicadstamp/project_setup.py",
     N3_OLD, N3_NEW, CREATION),
    ("н4 infra loop skipped", "kicadstamp/project_setup.py",
     N4_OLD, N4_NEW, CREATION),
    ("н5 stem from the directory", "kicadstamp/utils/paths.py",
     N5_OLD, N5_NEW, CREATION),
    ("н6 no folder validation", "gui/docks/create_project_dialog.py",
     N6_OLD, N6_NEW, DIALOG),
    ("н7 no separator guard", "gui/docks/create_project_dialog.py",
     N7_OLD, N7_NEW, DIALOG),
    ("н8 recent menu not rebuilt", "gui/main_window.py",
     N8_OLD, N8_NEW, MENU),
    ("н9 infra list loses a row", "kicadstamp/utils/paths.py",
     N9_OLD, N9_NEW, CREATION),
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
