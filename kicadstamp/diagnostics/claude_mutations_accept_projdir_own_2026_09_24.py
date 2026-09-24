# kicadstamp/diagnostics/claude_mutations_accept_projdir_own_2026_09_24.py
"""Claude's OWN mutation rig for the 'project is a directory' acceptance.

Independent of the rig the entry shipped: these substitutions aim at what the
ACCEPTANCE suspected, not at what the plan declared. Rule 38 discipline is
enforced mechanically — a substitution whose `old` text does not occur EXACTLY
once is reported INVALID and never counted as a verdict.

K6 is the control: it must SURVIVE. A rig where everything dies is a rig that is
measuring the test runner, not the guards.
"""
import py_compile
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = "/home/denis/Projects/Python/KiCadStamp/.venv/bin/python"

MUTATIONS = [
    ("K1 typo in the infra list, count unchanged",
     "kicadstamp/utils/paths.py",
     '"registry", "tracks", "logs", "overrides", "operational"',
     '"registry", "tracks", "logs", "override", "operational"'),

    ("K2 infra dirs created BEFORE the refusal check",
     "kicadstamp/project_setup.py",
     """    config = Path(project_config_path_for_dir(project_dir))
    if config.exists():
        raise ProjectConfigExists(config)
    config.parent.mkdir(parents=True, exist_ok=True)""",
     """    config = Path(project_config_path_for_dir(project_dir))
    config.parent.mkdir(parents=True, exist_ok=True)
    for _n in PROJECT_INFRA_DIRS:
        (config.parent / _n).mkdir(exist_ok=True)
    if config.exists():
        raise ProjectConfigExists(config)"""),

    ("K3 backslash no longer rejected in _target_dir",
     "gui/docks/create_project_dialog.py",
     'if not name or not folder or "/" in name or "\\\\" in name:',
     'if not name or not folder or "/" in name:'),

    ("K4 _on_ok stops checking separators (button guard alone)",
     "gui/docks/create_project_dialog.py",
     '        if not name or "/" in name or "\\\\" in name:',
     '        if not name:'),

    ("K5 config named by dir STEM, not dir NAME",
     "kicadstamp/utils/paths.py",
     'return str(p / (p.name + ".sexp"))',
     'return str(p / (p.stem + ".sexp"))'),

    ("K6 CONTROL: a docstring word only",
     "kicadstamp/project_setup.py",
     "Creating a project — the one place that writes a brand-new one.",
     "Creating a project - the single place that writes a brand-new one."),
]

TARGETS = "tests/test_project_is_a_directory.py tests/gui/test_create_project_dialog.py tests/gui/test_root_metadata.py tests/gui/test_main_window.py".split()


def purge_bytecode():
    """A same-length edit restored within one second can leave CPython trusting
    the mutant's .pyc. Remove every cache outright instead of relying on mtime."""
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
            r = subprocess.run([PY, "-m", "pytest", *TARGETS, "-q", "--no-header", "-p", "no:cacheprovider"],
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
