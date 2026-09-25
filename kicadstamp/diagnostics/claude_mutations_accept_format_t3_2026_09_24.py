"""Acceptance mutations for step Т3 of plan_2026_09_24_config_format_version
(the writer stamps the number; every config write has one home, write_config_file,
which owns the `.bak` and the newer-refusal), Claude, 2026-09-24.
Round 1: d6b2119 + 02820c2 (the Т5 guard). Round 2: fa3ff0e (Т3b: Д4 the GUI
Save through the one writer, Д5 serialize-first, the four empty cells) —
W4-W7 re-anchored on the new `(stale or always_backup) and backup` condition, X1-X7 added.
Round 3: f2c165a (the raw path of the three converters) — Y1-Y7 added.

Built from claude_mutations_accept_format_t2_2026_09_24.py (stale-.pyc guard
included) (rule 38): the pattern must match EXACTLY once, a red run with zero
FAILED lines is a miss, and a dump is only a negative code or "Fatal Python
error" with no FAILED line at all.

"die" = the step's watchdog must go red; "unknown" = probes a property the
report states but no cell was seen pinning — a survivor there is the finding.

Run against a worktree at the step's SHA with the main checkout's interpreter:
  KICADSTAMP_ACCEPT_ROOT=<tree> <main>/.venv/bin/python \\
      kicadstamp/diagnostics/claude_mutations_accept_format_t3_2026_09_24.py [prefix...]
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))
PY_BIN = os.environ.get("KICADSTAMP_PYTHON", sys.executable)

T = [
    "tests/test_config_format_version.py", "tests/test_config_includes.py",
    "tests/test_sexp_config_loading.py", "tests/test_sexp_config_roundtrip.py",
    "tests/test_config_working_set.py", "tests/test_project_is_a_directory.py",
    "tests/test_migrate_legacy_pad_anchors.py", "tests/test_author.py",
    "tests/test_cli_extract_core.py", "tests/test_extract_writer.py",
    "tests/test_template_extraction.py",
    "tests/gui/test_imprint.py", "tests/gui/test_include_recovery.py",
    "tests/gui/test_create_project_dialog.py", "tests/gui/test_dock_common.py",
    "tests/test_convert_rules_to_chains.py", "tests/test_sexp_config_convert.py",
    "tests/test_tree_mount_conversion.py", "tests/test_converter_safety.py",
]
SX = "kicadstamp/config/sexp_format.py"
CW = "kicadstamp/config_writer.py"

MUTATIONS = [
    ("W1 dict_to_sexp stamps no number", SX,
     "    root.append([sym(VERSION_KEY), format_number])",
     "    pass", "die"),
    ("W2 JSON written without the number", CW,
     "        out = {VERSION_KEY: number}",
     "        out = {}", "die"),
    ("W3 JSON lets the dict's version win", CW,
     "        out.update({k: v for k, v in data.items() if k != VERSION_KEY})",
     "        out.update(data)", "unknown"),
    ("W4 no .bak when the file is older", CW,
     "        if (stale or always_backup) and backup:",
     "        if always_backup:", "die"),
    ("W5 .bak on every write", CW,
     "        if (stale or always_backup) and backup:",
     "        if True:", "die"),
    ("W6 writer overwrites a newer file", CW,
     "        except ValidationError as e:\n            raise OSError(str(e)) from e\n        if (stale",
     "        except ValidationError as e:\n            stale = False\n        if (stale", "die"),
    ("W7 newer refusal escapes as ValidationError", CW,
     "            raise OSError(str(e)) from e\n        if (stale",
     "            raise\n        if (stale", "die"),
    ("W8 sexp writer freezes the number", SX,
     "        format_number = current_format()",
     "        format_number = 2", "unknown"),
    ("W9 JSON writer freezes the number", CW,
     "        number = current_format() if format_number is None else format_number",
     "        number = 2 if format_number is None else format_number", "unknown"),
    ("W10 create_project writes a literal again", "kicadstamp/project_setup.py",
     "    write_config_file(config, {})",
     "    config.write_text(\"(kicadstamp-config)\\n\", encoding=\"utf-8\")", "unknown"),
    ("W11 imprint storage bypasses the writer", "gui/docks/imprint.py",
     "        write_config_file(path, {})",
     "        from kicadstamp.config.sexp_format import dict_to_sexp\n\n"
     "        path.write_text(dict_to_sexp({}), encoding=\"utf-8\")", "die"),
    ("W12 include recovery bypasses the writer", "gui/include_recovery.py",
     "    write_config_file(path, data)",
     "    path.write_text(dict_to_sexp(data), encoding=\"utf-8\")", "die"),
    ("W13 GUI chokepoint writes by itself again", CW,
     "    write_config_file(path, data)\n\n\n# Public aliases",
     "    with open(path, \"w\", encoding=\"utf-8\") as f:\n"
     "        f.write(_serialize(path, data))\n\n\n# Public aliases", "unknown"),
    ("W14 migration tool loses its copy", "tools/migrate_legacy_pad_anchors.py",
     "        write_config_file(path, data, always_backup=True)",
     "        write_config_file(path, data)", "die"),
    ("W15 a tracked .sexp loses its number", "kicadstamp_templates_example.sexp",
     "  (version 2)\n", "", "die"),
    ("W16 migration tool writes before the copy", "tools/migrate_legacy_pad_anchors.py",
     "        write_config_file(path, data, always_backup=True)",
     "        from kicadstamp.utils.safe_write import write_text_atomic\n"
     "        from kicadstamp.config_writer import _serialize\n"
     "        write_text_atomic(path, _serialize(path, data))", "die"),
    # Round 2 (fa3ff0e): the GUI global Save now goes through the writer (Д4).
    ("X1 Save skips the newer guard", "kicadstamp/config_working_set.py",
     "        if errors:\n            return errors\n\n        # 3.",
     "\n        # 3.", "die"),
    ("X2 Save writes a file whose copy failed", "kicadstamp/config_working_set.py",
     "                if resolved in backup_failed:\n"
     "                    continue  # its copy did not come off — do not write it\n",
     "", "die"),
    ("X3 backup=False ignored (second copy)", CW,
     "        if (stale or always_backup) and backup:",
     "        if stale or always_backup:", "die"),
    ("X4 backup=False also skips newer check", CW,
     "    if target.exists():\n        try:\n            stale = read_version",
     "    if target.exists() and backup:\n        try:\n            stale = read_version",
     "unknown"),
    ("X5 copy before serialize again (Д5)", CW,
     "    text = _serialize(target, data, format_number=format_number)\n"
     "    if target.exists():\n        try:\n            stale = read_version(target) < current_format()\n"
     "        except ValidationError as e:\n            raise OSError(str(e)) from e\n"
     "        if (stale or always_backup) and backup:\n            backup_file(target)\n",
     "    if target.exists():\n        try:\n            stale = read_version(target) < current_format()\n"
     "        except ValidationError as e:\n            raise OSError(str(e)) from e\n"
     "        if (stale or always_backup) and backup:\n            backup_file(target)\n"
     "    text = _serialize(target, data, format_number=format_number)\n", "die"),
    ("X6 Save writes by itself again", "kicadstamp/config_working_set.py",
     "                        write_config_file(path, self._staged[resolved],\n"
     "                                          backup=False)",
     "                        path.write_text(_serialize(path, self._staged[resolved]),\n"
     "                                        encoding=\"utf-8\")", "die"),
    ("X7 Save takes no .history copy", "kicadstamp/config_working_set.py",
     "                    backup_to_history(path, root_dir)\n",
     "                    pass\n", "die"),
    # Round 3 (f2c165a): a converter writes back the number it READ.
    ("Y1 rule converter stamps the current number", "tools/convert_rules_to_chains.py",
     "    write_config_file(path, data, format_number=version, backup=False)",
     "    write_config_file(path, data, backup=False)", "die"),
    ("Y2 rule converter reads with the lift", "tools/convert_rules_to_chains.py",
     "                                version_out=found, upgrade=False) or {}",
     "                                version_out=found) or {}", "unknown"),
    ("Y3 tree converter stamps the current number", "kicadstamp/tree_mount_convert.py",
     "    new_text = dict_to_sexp(converted, format_number=version)",
     "    new_text = dict_to_sexp(converted)", "die"),
    ("Y4 tree converter reads with the lift", "kicadstamp/tree_mount_convert.py",
     "                        version_out=found_version, upgrade=False)",
     "                        version_out=found_version)", "unknown"),
    ("Y5 translator stamps the current number", "tools/sexp_config_convert.py",
     "        write_config_file(path, data, format_number=version, backup=False)",
     "        write_config_file(path, data, backup=False)", "die"),
    ("Y6 JSON rule converter forgets the number", "tools/convert_rules_to_chains.py",
     "            version = take_version(data, str(path))\n",
     "            version = 1\n", "unknown"),
    ("Y7 translator takes a second copy", "tools/sexp_config_convert.py",
     "        write_config_file(path, data, format_number=version, backup=False)",
     "        write_config_file(path, data, format_number=version)", "unknown"),
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
