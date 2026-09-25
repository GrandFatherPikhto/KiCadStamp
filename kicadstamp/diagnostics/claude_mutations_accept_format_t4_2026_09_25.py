"""Acceptance mutations for step Т4 of plan_2026_09_24_config_format_version
(the on-disk lift of the include: graph from load_config, plus «Уточнения к Т4»
У1-У4), Claude, 2026-09-25. Round 1: c978eba (branch config-format-upgrade-on-disk). Round 2: 43b2a5e (the M12
cell, the M6/M13 wording, Т7 docs) — M13 re-anchored on the line itself, the
comment after it changed.

Built from claude_mutations_accept_format_t3_2026_09_24.py (stale-.pyc guard
included) (rule 38): the pattern must match EXACTLY once, a red run with zero
FAILED lines is a miss, and a dump is only a negative code or "Fatal Python
error" with no FAILED line at all.

"die" = the step's watchdog must go red; "unknown" = probes a property the
report states but no cell was seen pinning — a survivor there is the finding.

Run against a worktree at the step's SHA with the main checkout's interpreter:
  KICADSTAMP_ACCEPT_ROOT=<tree> <main>/.venv/bin/python \\
      kicadstamp/diagnostics/claude_mutations_accept_format_t4_2026_09_25.py [prefix...]
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))
PY_BIN = os.environ.get("KICADSTAMP_PYTHON", sys.executable)

T = [
    "tests/test_config_format_upgrade_on_disk.py", "tests/test_config_format_version.py",
    "tests/test_config_working_set.py", "tests/test_config_includes.py",
    "tests/test_sexp_config_loading.py",
]
U = "kicadstamp/config/upgrade_on_disk.py"
CMP = "            if _strip_defaults(parsed_back) != _strip_defaults(content):"
PRE = "        if read_version(path) < current_format():\n            outdated.append(path)"

MUTATIONS = [
    ("M1 check compares raw dicts (У2)", U, CMP,
     "            if parsed_back != content:", "die"),
    ("M2 lift while the working set is dirty (У3)", U,
     "    if WORKING_SET.is_dirty():", "    if False:", "die"),
    ("M3 lift by inserting the line (У1)", U,
     "            text = serialize_config(path, content)",
     "            text = path.read_text(encoding=\"utf-8\").replace(\n"
     "                \"(kicadstamp-config\\n\", f\"(kicadstamp-config\\n  (version {current_format()})\\n\", 1)",
     "die"),
    ("M4 check before the write disabled", U, CMP, "            if False:", "die"),
    ("M5 no .bak", U, "            backup = backup_file(path)",
     "            backup = None", "die"),
    ("M6 newer file skipped, the rest lifted", U, PRE,
     "        try:\n            if read_version(path) < current_format():\n"
     "                outdated.append(path)\n        except ValidationError:\n            continue",
     "die"),
    ("M7 a current file is rewritten too", U,
     "        if read_version(path) < current_format():",
     "        if read_version(path) <= current_format():", "die"),
    ("M8 diamond not deduped", U,
     "        if resolved not in seen:\n            seen.add(resolved)\n            files.append(Path(resolved))",
     "        seen.add(resolved)\n        files.append(Path(resolved))", "die"),
    ("M9 a failed write escapes the load", U,
     "        except (OSError, ValidationError) as e:",
     "        except ValidationError as e:", "die"),
    ("M10 no WARNING line", U,
     "        logger.warning(_(", "        logger.debug(_(", "die"),
    ("M11 load_config does not lift on disk", "kicadstamp/config/loader.py",
     "    upgrade_graph_on_disk(path)\n", "", "die"),
    ("M12 writer ignores serialized_text", "kicadstamp/config_writer.py",
     "    text = (serialized_text if serialized_text is not None\n"
     "            else _serialize(target, data, format_number=format_number))",
     "    text = _serialize(target, data, format_number=format_number)", "die"),
    ("M13 a missing include file breaks the sweep", U,
     "        if not path.exists():\n            continue", "        if False:\n            continue", "unknown"),
    ("M14 the WARNING names no backup", U,
     "new=current_format(), backup=backup))", "new=current_format(), backup=\"\"))", "die"),
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
