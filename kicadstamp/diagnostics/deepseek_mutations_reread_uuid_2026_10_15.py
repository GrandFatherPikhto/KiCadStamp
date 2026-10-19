# kicadstamp/diagnostics/deepseek_mutations_reread_uuid_2026_10_15.py
"""Mutation harness for Ч1 of plan_2026_10_15_reread_copper_uuid_and_selection_any
(«Reread inter-node copper» loses the record's uuid -> the write is refused).

Three mechanisms, one commit each, one row of mutations each:

  (1) CACHE  — `read_data` must hand out the caller's OWN dict (kicadstamp/
      config_writer.py::_read_data). Guards: tests/config/test_writer_reads_a_private_dict.py
      С1..С5.
  (2) IDENTITY — the writers carry the record's identity through
      kicadstamp/config/form_identity.py (added with that commit).
  (3) STAMP HOLE — format3's uuid inheritance keyed by the section's identity
      (name, else net) instead of `name` alone (added with that commit).

Safeguards (rule 38, taken from the earlier harnesses):
  * `if original.count(old) != 1` — refuse an ambiguous template (a mutation that
    silently patches the WRONG line reports GREEN and teaches nothing);
  * a verdict with ZERO failed rows is a MISS, not a kill (`no FAILED line`,
    `exit N` = the run could not even collect the tests);
  * `PYTHONDONTWRITEBYTECODE=1` + `__pycache__` sweeping — a stale `.pyc` once
    made a mutation look green;
  * the file is restored in a `finally`, and the restoration is VERIFIED by
    comparing the text, not assumed.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_reread_uuid_2026_10_15.py`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# доделка Р (19.10): the private-dict reader moved verbatim out of config_writer
# into config/file_read.py (config_writer keeps the re-export), so the two CACHE
# mutation templates below live in THAT file now.
CW = "kicadstamp/config/file_read.py"
FI = "kicadstamp/config/form_identity.py"
GUARDS_1 = "tests/config/test_writer_reads_a_private_dict.py"
GUARDS_2 = ("tests/config/test_form_identity.py "
            "tests/config/test_net_trace_write_keeps_uuid.py "
            "tests/gui/docks/test_reread_net_trace_identity.py")
GUARDS_3 = ("tests/net/test_net_trace_write_names_the_record.py "
            "tests/gui/docks/test_rename_save_in_place.py")
F3 = "kicadstamp/config/format3.py"
NTE = "kicadstamp/net_trace_extract.py"
NTDOCK = "gui/docks/net_trace.py"

# Each row: name, file, OLD, NEW, expected-to-redden test ids (substring) or None.
MUTATIONS = [
    ("М1 read_data hands out the cache's own object", CW,
     "    return copy.deepcopy(cached_file_read(path, _uncached_read))",
     "    return cached_file_read(path, _uncached_read)",
     ["test_editing_what_read_data_returned_does_not_reach_the_cache",
      "test_a_writer_splice_keeps_the_uuid_on_a_warm_cache",
      "test_a_writer_splice_keeps_the_uuid_under_the_working_set"]),
    # NB: `return copy.deepcopy(staged)` alone matches TWO staged readers in this
    # module (the other one guards with `WORKING_SET.enabled and path is not
    # None`), so the template carries the guard line that identifies _read_data.
    ("М2 the staged branch stops copying", CW,
     "    if WORKING_SET.enabled:\n"
     "        staged = WORKING_SET.staged_content(str(path.resolve()))\n"
     "        if staged is not None:\n"
     "            return copy.deepcopy(staged)",
     "    if WORKING_SET.enabled:\n"
     "        staged = WORKING_SET.staged_content(str(path.resolve()))\n"
     "        if staged is not None:\n"
     "            return staged",
     ["test_editing_a_staged_read_does_not_touch_the_staged_content"]),

    # (2) IDENTITY — form_identity.keep_identity. Guards: the helper's own cells
    # (tests/config/test_form_identity.py) + the two writer cells.
    ("М3 the identity is not carried onto the fresh dict", FI,
     "    for key in STORAGE_ONLY_KEYS:\n"
     "        if new_entry.get(key) is None and old_entry.get(key) is not None:\n"
     "            new_entry[key] = old_entry[key]",
     "    for key in STORAGE_ONLY_KEYS:\n"
     "        if False:  # MUTATION: the stored identity is not carried\n"
     "            new_entry[key] = old_entry[key]",
     # The isolated cells (С6b / С9b) are the ones that FAIL here: the plain
     # end-to-end cells stay green, because the stamp inherits the uuid by name
     # from the file it just read — measured, and the reason those two cells
     # exist (rule 39).
     ["test_keep_identity_carries_the_stored_uuid_onto_a_fresh_dict",
      "test_keep_identity_copies_and_carries_only_the_declared_keys",
      "test_replace_entry_replaces_in_place_keeping_the_stored_identity",
      "test_the_writer_itself_carries_the_uuid_without_the_stamp",
      "test_the_reread_carries_the_uuid_without_the_stamp"]),

    # (3) THE NAME — layer 1 (the stamp's П2 backstop) and layer 2 (the writer
    # gives the name). Guards: tests/net/test_net_trace_write_names_the_record.py
    # С17–С22.
    ("М4 the stamp stops guarding П2", F3,
     "    for section, name, _uuid, index in records:\n"
     "        check_record_has_a_name(section, name, index, path)",
     "    for section, name, _uuid, index in records:\n"
     "        if False:  # MUTATION: the stamp writes what the loader refuses\n"
     "            check_record_has_a_name(section, name, index, path)",
     # ONLY the direct-write cell. The writer cell (С17) stays green on purpose:
     # layer 2 names the record BEFORE the stamp sees it, so that cell guards the
     # naming, not the backstop — measured, and the reason the two cells exist.
     ["test_the_stamp_refuses_an_unnamed_record_and_leaves_the_file_alone"]),
    ("М5 the writer stops naming a nameless record", NTE,
     "    nt = _name_for_format3(nt, net_traces)",
     "    nt = nt  # MUTATION: no name is given",
     ["test_a_nameless_capture_of_a_new_net_is_written_named",
      "test_a_nameless_capture_replaces_the_one_record_of_that_net",
      "test_a_name_minted_by_the_converter_counts_as_the_records_name"]),
    ("М6 the net search is gone (every capture mints a fresh name)", NTE,
     "    same_net = _entries_with_net({\"net_traces\": net_traces}, nt.net)",
     "    same_net = []  # MUTATION: the net's own record is not found",
     ["test_a_nameless_capture_replaces_the_one_record_of_that_net",
      "test_a_name_minted_by_the_converter_counts_as_the_records_name",
      "test_several_records_of_the_net_are_refused_and_the_file_is_untouched"]),
    # The THIRD saver found by an existing guard: the Net-trace dock's form Save.
    ("М7 the form Save stops naming a legacy record", NTDOCK,
     '                entry["name"] = entry["net"]',
     '                pass  # MUTATION: the legacy record stays nameless',
     ["test_a_legacy_record_saved_from_the_form_gets_its_name"]),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str, *extra: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider", *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        # A GUI cell that hangs must be reported, not waited for (rule 24).
        return -1, "TIMEOUT: the guard run did not finish within 300 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list[str]:
    ids = []
    for line in out.splitlines():
        if line.startswith("FAILED "):
            ids.append(line.split(" ", 1)[1].split(" - ")[0])
    return ids


def main() -> int:
    # One run for the whole plan: every mutation is checked against the UNION of
    # the guards, so a row cannot look green just because its own guard was not
    # in the run (that is how М1/М2 reported MISS when given only GUARDS_2).
    guards = os.environ.get("GUARDS", GUARDS_1 + " " + GUARDS_2 + " " + GUARDS_3)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-2000:])
        return 2
    print("baseline green")

    misses = 0
    for name, rel, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[SKIP/MISS] {name}: the template matches "
                  f"{original.count(old)} times — refusing (rule 38)")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(guards)
            failed = _failed_ids(out)
            if not failed:
                print(f"[MISS] {name}: NO failed test — a mutation that kills "
                      f"nothing proves nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in (expect or []) if not any(e in f for f in failed)]
            if missing:
                print(f"[MISS] {name}: expected these to redden, they did not: "
                      f"{missing}\n  actually failed: {failed}")
                misses += 1
            else:
                print(f"[KILL] {name}: {len(failed)} red — {failed}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} killed, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
