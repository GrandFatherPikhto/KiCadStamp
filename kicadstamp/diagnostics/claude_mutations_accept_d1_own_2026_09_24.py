# kicadstamp/diagnostics/claude_mutations_accept_d1_own_2026_09_24.py
"""Claude's OWN rig for ДОПОЛНЕНИЕ 1 (Д-1/Д-2/Д-3), acceptance of 2026-09-24.

K3/K4 of the previous rig went INVALID once the separator checks moved into the
shared _name_problem() helper — an INVALID is not a verdict, so they are
RETARGETED here rather than counted. K7/K8 aim at the entry's OWN insight (read
the RAW last component, because Path("x/.") is already Path("x")): a finding is
only closed when a cell holds it, not when a comment explains it.

K10 is the control and must SURVIVE.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = "/home/denis/Projects/Python/KiCadStamp/.venv/bin/python"

MUTATIONS = [
    ("K3' backslash dropped from the shared helper",
     "gui/docks/create_project_dialog.py",
     '        if "/" in name or "\\\\" in name:',
     '        if "/" in name:'),

    ("K4' _on_ok stops consulting the helper (button guard alone)",
     "gui/docks/create_project_dialog.py",
     """        problem = self._name_problem(name)
        if not name or problem:""",
     """        problem = None
        if not name:"""),

    ("K7 core checks the NORMALISED name, not the raw component",
     "kicadstamp/utils/paths.py",
     '    raw = str(project_dir).replace("\\\\", "/").rstrip("/")\n    if raw.rsplit("/", 1)[-1] in (".", ".."):',
     '    raw = Path(project_dir).name\n    if raw in (".", ".."):'),

    ("K8 core refuses '.' but no longer '..'",
     "kicadstamp/utils/paths.py",
     'if raw.rsplit("/", 1)[-1] in (".", ".."):',
     'if raw.rsplit("/", 1)[-1] in (".",):'),

    ("K9 root refusal moved AFTER _remember_recent",
     "gui/docks/root_metadata.py",
     """        if path is not None and path.suffix.lower() != _ROOT_SUFFIX:
            self._show_message(
                _("Only a .sexp config can be opened as a project root: {path}")
                .format(path=path), _ERROR_STYLE)
            return
        if WORKING_SET.is_dirty() and not self._confirm_discard_changes():
            return
        if path is not None:
            self._remember_recent(path)""",
     """        if WORKING_SET.is_dirty() and not self._confirm_discard_changes():
            return
        if path is not None:
            self._remember_recent(path)
        if path is not None and path.suffix.lower() != _ROOT_SUFFIX:
            self._show_message(
                _("Only a .sexp config can be opened as a project root: {path}")
                .format(path=path), _ERROR_STYLE)
            return"""),

    ("K10 CONTROL: a docstring word only",
     "kicadstamp/project_setup.py",
     "Creating a project — the one place that writes a brand-new one.",
     "Creating a project - the single place that writes a brand-new one."),
]

TARGETS = "tests/test_project_is_a_directory.py tests/gui/test_create_project_dialog.py tests/gui/test_root_metadata.py tests/gui/test_main_window.py".split()


def purge_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for label, rel, old, new in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{label}: INVALID — `old` occurs {n} times, expected exactly 1")
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            purge_bytecode()
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as e:
                print(f"{label}: INVALID — mutant does not compile: {e}")
                continue
            r = subprocess.run([PY, "-m", "pytest", *TARGETS, "-q", "--no-header",
                                "-p", "no:cacheprovider"],
                               cwd=ROOT, capture_output=True, text=True, env=env, timeout=400)
            out = r.stdout
            tail = [l for l in out.splitlines() if " passed" in l or " failed" in l or "error" in l.lower()]
            failed = [l.split("::", 1)[-1] for l in out.splitlines() if l.startswith("FAILED")]
            summary = tail[-1] if tail else "(no summary line)"
            if "error" in summary.lower() and "failed" not in summary:
                verdict = "MISS — suite did not assemble"
            elif not failed and r.returncode != 0:
                verdict = "MISS — non-zero exit with no FAILED lines"
            elif failed:
                verdict = f"KILLED by {len(failed)} cell(s)"
            else:
                verdict = "SURVIVED"
            print(f"{label}: {verdict}")
            print(f"    {summary}")
            for f in failed[:6]:
                print(f"      red: {f}")
        finally:
            path.write_text(original, encoding="utf-8")
            purge_bytecode()


if __name__ == "__main__":
    run()
