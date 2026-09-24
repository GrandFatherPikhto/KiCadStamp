"""Acceptance mutations for step Т1 of plan_2026_09_24_config_format_version
(the format number module and its removal in the parse layer), Claude, 2026-09-24.

Built from claude_mutations_accept_hook_2026_09_24.py (rule 38): the pattern must
match EXACTLY once, a red run with zero FAILED lines is a miss, and a dump is only
a negative code or "Fatal Python error" with no FAILED line at all.

"die" = the step's watchdog must go red; "unknown" = probes a property the
report states but no cell was seen pinning — a survivor there is the finding.

Run against a worktree at the step's SHA with the main checkout's interpreter:
  KICADSTAMP_ACCEPT_ROOT=<tree> <main>/.venv/bin/python \\
      kicadstamp/diagnostics/claude_mutations_accept_format_t1_2026_09_24.py [prefix...]
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))
PY_BIN = os.environ.get("KICADSTAMP_PYTHON", sys.executable)

T = ["tests/test_config_format_version.py"]
FV = "kicadstamp/config/format_version.py"
SX = "kicadstamp/config/sexp_format.py"

MUTATIONS = [
    ("F1 number reaches the returned dict", SX,
     "                version_out.append(found)\n            continue\n",
     "                version_out.append(found)\n", "die"),
    ("F2 parse layer does not refuse newer", SX,
     "            refuse_newer(found, path)\n", "", "die"),
    ("F3 upgrade_data lets a newer file through", FV,
     "    refuse_newer(from_version, path)\n    version = from_version",
     "    version = from_version", "die"),
    ("F4 no number means format 2", FV,
     "    if raw is _MISSING:\n        return 1", "    if raw is _MISSING:\n        return 2", "die"),
    ("F5 true/false accepted as a number", FV,
     "    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:",
     "    if not isinstance(raw, int) or raw < 1:", "die"),
    ("F6 two root numbers allowed", SX,
     "    if seen:\n        raise _fatal(", "    if False:\n        raise _fatal(", "die"),
    ("F7 probe cache ignores mtime", FV,
     "    key = (str(p.resolve()), mtime_ns)", "    key = (str(p.resolve()), 0)", "die"),
    ("F8 hole in the chain skipped silently", FV,
     "        if step is None:\n            raise ValidationError(",
     "        if step is None:\n            break\n            raise ValidationError(", "die"),
    ("F9 JSON probe does not refuse newer", FV,
     "    refuse_newer(version, str(path))\n    return version",
     "    return version", "unknown"),
    ("F10 one-value check removed", SX,
     "    if len(child) != 2:\n        raise _fatal(", "    if False:\n        raise _fatal(", "die"),
    ("F11 zero accepted", FV,
     "not isinstance(raw, int) or raw < 1:", "not isinstance(raw, int) or raw < 0:", "die"),
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
