"""Acceptance mutations for step Т2 of plan_2026_09_24_config_format_version
(lifting the content where it is parsed, by construction), Claude, 2026-09-24.
Rounds: 6bdd453 (G1-G7), 9362dd5 (D1, D3, D4 added for the fixes).

Built from claude_mutations_accept_format_t1_2026_09_24.py (stale-.pyc guard included) (rule 38): the pattern must
match EXACTLY once, a red run with zero FAILED lines is a miss, and a dump is only
a negative code or "Fatal Python error" with no FAILED line at all.

"die" = the step's watchdog must go red; "unknown" = probes a property the
report states but no cell was seen pinning — a survivor there is the finding.

Run against a worktree at the step's SHA with the main checkout's interpreter:
  KICADSTAMP_ACCEPT_ROOT=<tree> <main>/.venv/bin/python \\
      kicadstamp/diagnostics/claude_mutations_accept_format_t2_2026_09_24.py [prefix...]
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))
PY_BIN = os.environ.get("KICADSTAMP_PYTHON", sys.executable)

T = ["tests/test_config_format_version.py", "tests/test_config_includes.py", "tests/test_sexp_config_loading.py", "tests/test_sexp_config_roundtrip.py"]
FV = "kicadstamp/config/format_version.py"
SX = "kicadstamp/config/sexp_format.py"

MUTATIONS = [
    ("G1 parse does not lift by default", SX,
     "                 upgrade: bool = True) -> dict:",
     "                 upgrade: bool = False) -> dict:", "die"),
    ("G2 JSON lift never lifts", FV,
     "    if not upgrade:\n        return data\n    return upgrade_data(data, version, path, at_parse_time=True)",
     "    return data", "die"),
    ("G3 parse claims it is NOT parse time", SX,
     "    return upgrade_data(out, version, file_path, at_parse_time=True)",
     "    return upgrade_data(out, version, file_path, at_parse_time=False)", "unknown"),
    ("G4 writer lets the dict's version through", SX,
     "        if key == VERSION_KEY:\n            continue\n        child = _root_child_to_sexp",
     "        child = _root_child_to_sexp", "die"),
    ("G5 newer refusal names a section, not the file", SX,
     "    refuse_newer(version, file_path)\n", "    refuse_newer(version, path)\n", "die"),
    ("G6 JSON probe does not refuse newer (F9)", FV,
     "    refuse_newer(version, str(path))\n    return version",
     "    return version", "die"),
    ("G7 included JSON file not lifted", "kicadstamp/config/includes.py",
     "            return lift_loaded_dict(\n                normalize_section_aliases(json.load(f) or {}), str(path))",
     "            return normalize_section_aliases(json.load(f) or {})", "unknown"),
    # Round 2 (9362dd5): the fixes Д1-Д3 got cells of their own.
    ("D1 the GUI write reader lifts .sexp twice again", "kicadstamp/config_writer.py",
     "                return sexp_to_dict(f.read(), path=str(p))",
     "                return lift_loaded_dict(\n"
     "                    sexp_to_dict(f.read(), path=str(p)), str(p))", "die"),
    ("D3 a file parse without path=", "kicadstamp/cli_extract.py",
     "            data = sexp_to_dict(f.read(), path=str(p)) or {}",
     "            data = sexp_to_dict(f.read()) or {}", "die"),
    ("D4 one table reader drops the JSON lift", "kicadstamp/adapter_factory.py",
     "                return lift_loaded_dict(json.load(handle) or {}, str(p))",
     "                return json.load(handle) or {}", "die"),
]


def _drop_pyc(source: pathlib.Path) -> None:
    """Delete the cached bytecode of `source`.

    Python validates a .pyc by the source's SIZE and its mtime in WHOLE
    SECONDS. A mutation that keeps the size (one character: `< 1` -> `< 0`) and
    lands in the same second as the previous restore is then run from STALE
    bytecode — measured 24.09.2026: F11 read SURVIVED on the first full run and
    KILLED on every rerun. A stale cache can fake a kill as easily as a
    survival, so every rig that mutates in place needs this."""
    for cached in source.parent.glob(f"__pycache__/{source.stem}.*.pyc"):
        cached.unlink()


def run(paths):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400,
                       env=env)
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
            _drop_pyc(f)
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
            _drop_pyc(f)


if __name__ == "__main__":
    main()
