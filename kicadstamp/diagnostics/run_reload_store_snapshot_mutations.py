"""Mutation run for the reload→snapshot guards — steps 1–2 of
plan_2026_09_24_reload_store_snapshot.md.

Run it by hand from the repository root of the tree the fix lives in (it drives
pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_reload_store_snapshot_mutations

The cells under test are Н1–Н8 in tests/test_overrides_store_reload.py (plus the
two halves С3 was split into). They were seen RED on the base commit before the
fix, which is one half of the evidence; this harness is the other half: it proves
each cell still BITES after the fix, by putting the old behaviour back one cell
at a time.

Machinery, fuses and the reading of a verdict are inherited — nothing here was
written from scratch (deepseek.md §38) — from
kicadstamp/diagnostics/run_socket_owner_mutations.py, which in turn inherited them
from diagnostics/run_board_door_offender_mutations.py:

  * ``original.count(old) != 1`` — REFUSE an ambiguous template. Without it
    ``replace(old, new, 1)`` can edit a foreign line while the mutation reports
    GREEN: a silent miss is indistinguishable from a healthy guard, and that is
    the worst failure of all because it soothes instead of shouting. This run
    needs the fuse more than its parent did: the four ``.clear()`` calls the fix
    added are a PREFIX of the eight inside ``Board.refresh()``, so a four-line
    template would match TWICE and would silently mutate ``refresh()`` instead;
  * a CONTROL cell unrelated to the mutated body runs alongside. TWO are used
    here, because no single one survives all seven mutations honestly: м5 makes an
    incapable board raise, which legitimately reddens the stand-in-heavy cells,
    and м7 reaches the same stub through an attribute it does not have. At least
    ONE control must stay green or the run is NOT A VERDICT (a module that no
    longer imports reddens everything);
  * the verdict is read off the EXIT CODE of a run of ONE FULL test id: 0 green,
    1 with a FAILED line red; 2 (collection error/interrupted) and 5 ("no tests
    ran") are NOT verdicts;
  * a red on a cell that is not the mutation's TARGET is printed as ALSO RED and
    reported, never quietly folded into the kill.

The price rows (Н3/Н4) are parametrized, so their rows are selected by the base
node id — one family per line, all four rows measured together.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 180

CONNECTION = "gui/connection.py"
EXPLORE = "kicadstamp/explore.py"
HUB = "gui/dock_hub.py"
TESTS = "tests/test_overrides_store_reload.py"

N1 = f"{TESTS}::test_a_store_write_reaches_the_handed_over_snapshot"
N2 = f"{TESTS}::test_a_warm_snapshot_comes_back_with_the_new_role_after_a_write"
N3 = f"{TESTS}::test_the_reprojection_opens_no_board_door"
N4 = f"{TESTS}::test_the_reprojection_does_not_make_the_board_ask_again"
N5 = f"{TESTS}::test_a_project_switch_reprojects_the_snapshot_too"
N6 = f"{TESTS}::test_a_board_that_cannot_reproject_makes_the_reload_a_silent_no_op"
N7 = f"{TESTS}::test_the_write_event_hands_the_fresh_snapshot_to_the_docks"
N8 = f"{TESTS}::test_the_write_event_never_falls_into_a_board_refresh"
SOCKET_HALF = f"{TESTS}::test_a_store_reload_never_touches_the_socket"
SNAPSHOT_HALF = f"{TESTS}::test_a_store_reload_reprojects_the_snapshot_without_a_board_read"
CELLS = [("С3-сокет", SOCKET_HALF), ("С3-снимок", SNAPSHOT_HALF),
         ("Н1", N1), ("Н2", N2), ("Н3", N3), ("Н4", N4),
         ("Н5", N5), ("Н6", N6), ("Н7", N7), ("Н8", N8)]
#: One control per risk: the first proves the affected module still behaves for
#: its ORIGINAL cells, the second lives in a file no mutation touches at all.
CONTROLS = [
    ("в файле правки",
     f"{TESTS}::test_c2_a_record_forgotten_by_another_holder_stops_being_served"),
    ("в чужом файле",
     "tests/test_overrides_snapshot_and_diff.py::"
     "test_a_hand_built_selected_keeps_the_pre_t5g_meaning"),
]

# Each template is the exact block the fix added; putting the OLD behaviour back
# means deleting it (м1/м4/м6) or reverting it to its pre-fix shape (м2/м3/м5/м7).
_BIND_AND_REPROJECT = (
    "        binder(load_field_overrides(str(path)))\n"
    "        # The file is now in memory: the values in force are the new ones, while\n"
    "        # the snapshot still holds the resolution against the previous store.\n"
    "        self._reproject_snapshot_after_store_change()\n")

_REPROJECT_BODY = (
    "        self._board.forget_role_cluster_values()\n"
    "        self._rebuild_snapshot()\n")

_FOUR_CLEARS = (
    '        change)."""\n'
    "        self._role_cache.clear()\n"
    "        self._cluster_cache.clear()\n"
    "        self._board_role_cache.clear()\n"
    "        self._board_cluster_cache.clear()\n")

_PROJECT_SWITCH_REPROJECT = (
    "        # The rebind alone leaves the snapshot describing the PREVIOUS profile\n"
    "        # (plan_2026_09_24_reload_store_snapshot §3.3): same defect as the write\n"
    "        # path below, so the same one call, under the same capability check.\n"
    "        self._reproject_snapshot_after_store_change()\n")

_CAPABILITY_GATE = (
    "        if not self.override_reprojection_supported:\n"
    "            return\n"
    "        self._board.forget_role_cluster_values()\n")

_DISTRIBUTION = (
    "        snapshot = list(getattr(self._connection, \"snapshot\", None) or [])\n"
    "        self._safe_call(\"known-value lists after a store write\",\n"
    "                        self.push_known_lists, snapshot)\n"
    "        self._safe_call(\"components tree rows after a store write\",\n"
    "                        self.tree_dock.set_footprints, snapshot)\n")

MUTATIONS = [
    dict(name="m1-reload-without-a-reprojection", rel=CONNECTION,
         old=_BIND_AND_REPROJECT,
         new="        binder(load_field_overrides(str(path)))\n",
         target=N1,
         note="м1 — the store is re-read and the snapshot is left as it was: the "
              "defect itself. Н1 must redden."),
    dict(name="m2-rebuild-without-forgetting", rel=CONNECTION,
         old=_REPROJECT_BODY,
         new="        self._rebuild_snapshot()\n",
         target=N2,
         note="м2 — the snapshot IS rebuilt, but against warm Board caches (the "
              "naive fix of §2.2). Н2 must redden; Н1 is expected to stay GREEN, "
              "because it runs on a COLD board."),
    dict(name="m3-forget-every-cache", rel=EXPLORE,
         old=_FOUR_CLEARS,
         new=_FOUR_CLEARS +
             "        self._role_exists_cache.clear()\n"
             "        self._cluster_exists_cache.clear()\n"
             "        self._nets_cache.clear()\n"
             "        self._sheet_cache.clear()\n",
         target=N4,
         note="м3 — the method is broadened to all eight caches, including the "
              "ones the store cannot make stale. Н4 must redden."),
    dict(name="m4-project-switch-without-a-reprojection", rel=CONNECTION,
         old=_PROJECT_SWITCH_REPROJECT, new="", target=N5,
         note="м4 — a project switch rebinds the store and leaves the snapshot on "
              "the previous profile. Н5 must redden."),
    dict(name="m5-no-capability-check", rel=CONNECTION,
         old=_CAPABILITY_GATE,
         new="        self._board.forget_role_cluster_values()\n",
         target=N6,
         note="м5 — the capability is assumed instead of asked for: an incapable "
              "board raises instead of standing still. Н6 must redden."),
    dict(name="m6-no-distribution", rel=HUB, old=_DISTRIBUTION, new="        pass\n",
         target=N7,
         note="м6 — the snapshot is rebuilt and never handed to the docks: it "
              "stays invisible. Н7 must redden."),
    dict(name="m7-distribution-through-a-refresh", rel=HUB, old=_DISTRIBUTION,
         new=("        self._safe_call(\"snapshot refresh after a store write\",\n"
              "                        self.refresh_snapshot_and_push)\n"),
         target=N8,
         note="м7 — the distribution falls into refresh_snapshot_and_push, i.e. a "
              "full IPC re-read on a worker. Н8 must redden (and Н7 with it)."),
]


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file that carries a
    pyproject.toml. WALKED, not counted — `parent.parent` is correct for
    diagnostics/ and WRONG for kicadstamp/diagnostics/, where that mistake would
    make every mutation answer "green" by absence."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise SystemExit(f"pyproject.toml not found above {here}")


ROOT = _repo_root()


def _run_one(test_id: str) -> tuple:
    """('green' | 'red' | 'no-verdict', one evidence line) for ONE FULL test id."""
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=PYTEST_TIMEOUT_S)
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in output.strip().splitlines() if line.strip()]
    evidence = lines[-1] if lines else "<no output>"
    if proc.returncode == 0:
        return "green", evidence
    if proc.returncode == 1 and "FAILED" in output:
        return "red", evidence
    return "no-verdict", f"exit {proc.returncode} | {evidence}"


def main() -> int:
    print(f"repo root: {ROOT}")
    problems = []
    for mutation in MUTATIONS:
        path = ROOT / mutation["rel"]
        original = path.read_text(encoding="utf-8")
        old = mutation["old"]
        count = original.count(old)
        if count != 1:
            print(f"!! {mutation['name']}: the template matches {count} sites in "
                  f"{mutation['rel']} — refusing to run a mutation whose edit is "
                  "ambiguous (it would edit a foreign line and report GREEN, "
                  "deepseek.md §38)")
            return 2
        path.write_text(original.replace(old, mutation["new"], 1), encoding="utf-8")
        try:
            controls = [(name, test_id, *_run_one(test_id))
                        for name, test_id in CONTROLS]
            row = [(label, test_id, *_run_one(test_id)) for label, test_id in CELLS]
        finally:
            path.write_text(original, encoding="utf-8")

        print(f"\n=== {mutation['name']}  ({mutation['note']})")
        for name, test_id, status, evidence in controls:
            print(f"    control [{name}] {test_id.split('::')[-1]}: "
                  f"{status.upper()} | {evidence}")
        if not any(status == "green" for _n, _t, status, _e in controls):
            print("    NOT A VERDICT: neither control stayed green, so the mutation "
                  "broke the module rather than the behaviour — a red id below "
                  "would prove nothing")
            problems.append(f"{mutation['name']}: no control stayed green")
            continue

        target_label = next(label for label, test_id in CELLS
                            if test_id == mutation["target"])
        target_status = "no-verdict"
        for label, test_id, status, evidence in row:
            marker = "TARGET " if test_id == mutation["target"] else "       "
            verdict = ("KILLED" if status == "red" and label == target_label
                       else "ALSO RED" if status == "red"
                       else status.upper())
            print(f"    {marker}{label}: {verdict}")
            print(f"           | {evidence}")
            if test_id == mutation["target"]:
                target_status = status
        if target_status != "red":
            problems.append(f"{mutation['name']} -> {target_label} ({target_status})")

    print("\n" + "=" * 72)
    if problems:
        print("!! not everything went as expected:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    print(f"all {len(MUTATIONS)} mutations KILLED their target cell — the "
          "reprojection, the forgotten pair, the untouched caches, the project "
          "switch, the capability gate and the distribution are each pinned by a "
          "cell that can fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
