"""Acceptance mutations for the battle slot-exception hook
(plan_2026_09_24_slot_exception_hook), Claude, 2026-09-24. Rounds: c88dc76,
92f6fe9, 9c89434 (H13-H15 added in round 3; run H15 WITHOUT an exported
PYTHONPATH, or the tree root can come in through it and mask the mutation).

Built from claude_mutations_accept_err_2026_09_25.py (rule 38): the pattern must
match EXACTLY once, and a red run with zero FAILED lines is a miss, not a kill.

"die" = the entry's watchdog must go red; "unknown" = probes a policy the report
states but no cell was seen pinning — a survivor there is the finding.

Run against a worktree at the entry's SHA, with the main checkout's interpreter
and pytest-qt on PYTHONPATH until it is merged (plan_2026_09_24_kq_pytest_qt §4):
  KICADSTAMP_ACCEPT_ROOT=<tree> PYTHONPATH=<tree>:<abs kq-site> \\
      <main>/.venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_hook_2026_09_24.py
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))
PY_BIN = os.environ.get("KICADSTAMP_PYTHON", sys.executable)

T = ["tests/gui/test_slot_exception_hook.py"]
HOOK = "gui/slot_exception_hook.py"

MUTATIONS = [
    ("H1 battle entry no longer installs", "kicadstamp/gui_main.py",
     "    install_slot_exception_hook()\n\n",
     "    pass\n\n", "die"),
    ("H3 SystemExit swallowed", HOOK,
     "        if _goes_to_the_previous_hook(exc_type):",
     "        if False:", "die"),
    ("H4 second install wraps the first", HOOK,
     "    if _installed:\n        return False",
     "    if False:\n        return False", "die"),
    ("H5 off-switch ignored", HOOK,
     "in _OFF_VALUES:", "in ():", "die"),
    ("H6 Log line on EVERY repeat (storm)", HOOK,
     "        elif repeats in _REPEAT_STEPS:",
     "        elif True:", "unknown"),
    ("H7 repeats never speak again", HOOK,
     "        elif repeats in _REPEAT_STEPS:",
     "        elif False:", "unknown"),
    ("H8 report file on every repeat", HOOK,
     "        if repeats == 1:\n            path = _write_report",
     "        if repeats >= 1:\n            path = _write_report", "die"),
    ("H9 fallback silent", HOOK,
     "        os.write(2, (", "        (lambda *a: None)(2, (", "die"),
    ("H11 site = outermost frame", HOOK,
     "    while tb is not None and tb.tb_next is not None:\n        tb = tb.tb_next",
     "    while False:\n        tb = tb.tb_next", "unknown"),
    ("H12 report written without traceback", HOOK,
     '            "traceback:",\n', '            "tb:",\n', "die"),
    # Round 3 (92f6fe9 -> 9c89434): Н8 and Н9 got cells of their own.
    ("H13 key back to (entry, site, type)", HOOK,
     "    return (stack_sites(tb), exc_type.__name__)",
     "    return (entry_site(tb), failing_site(tb), exc_type.__name__)", "die"),
    ("H14 shown entry = wrapper lambda", HOOK,
     "        if not _is_wrapper_frame(tb.tb_frame.f_code.co_filename):",
     "        if True:", "die"),
    ("H15 inner run imports the MAIN checkout", "tests/gui/test_slot_exception_hook.py",
     "    entries = [str(_REPO_ROOT)]\n", "    entries = []\n", "die"),
]


def run(paths):
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400)
    return r.returncode, r.stdout + r.stderr


def main():
    only = sys.argv[1:]
    print(f"root: {ROOT.resolve()}")
    for name, rel, old, new, expect in MUTATIONS:
        if only and not any(name.startswith(o) for o in only):
            continue
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<40} {expect:<8} INVALID  pattern occurs {n} times")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, out = run(T)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code == 0:
                verdict, detail = "SURVIVED", "no watchdog went red"
            # A dump is a NEGATIVE code, or "Fatal" with no FAILED line at all: the
            # control rows print the INNER process's "Fatal Python error" inside
            # an ordinary assertion message (first run misread H5/H12 that way).
            elif code < 0 or ("Fatal Python error" in out and not failed):
                verdict, detail = "DUMP", f"exit {code}"
            elif not failed or "no tests ran" in out:
                verdict, detail = "MISS", f"zero FAILED at exit {code}"
            else:
                verdict = "KILLED"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:4])
            flag = ""
            if expect == "die" and verdict != "KILLED":
                flag = "  <<< FINDING: expected to die"
            if expect == "unknown" and verdict == "SURVIVED":
                flag = "  <<< FINDING: policy without a cell"
            print(f"{name:<40} {expect:<8} {verdict:<9} {detail}{flag}", flush=True)
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
