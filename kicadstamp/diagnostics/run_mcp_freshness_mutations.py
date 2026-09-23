"""Mutation run for the MCP board-freshness cells — Ш2/§10 of
plan_2026_09_24_mcp_stale_board (the acceptance half of steps 1+2).

Run it by hand from the repository root (it drives pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_mcp_freshness_mutations

The cells are М1–М5 plus one un-numbered cell in
tests/test_mcp_board_freshness.py. Two halves of evidence are required, and the
other half was taken first: the М-cells were SEEN RED on the base commit
(35ab638) before the fix — М1[position], М1[field], М2[3], М4, М5 and the
reconnect cell, with М3's rows green there by construction (they exist to kill
м3, not to describe the defect). This harness is the second half: it proves each
cell still BITES after the fix, by putting the OLD code back.

Machinery, fuses and the reading of a verdict are inherited from
run_imprint_freshness_mutations.py, itself inherited from
diagnostics/run_board_door_offender_mutations.py — kept verbatim, because they
protect against failures that are invisible:

  * ``original.count(old) != 1`` — REFUSE an ambiguous template. Two of the
    mutations below edit the SAME three lines (м1 removes the refresh, м4 moves
    it after ``fn``), and the seam's three-line block is one ``if`` away from
    the reconnect path's copy. Without this fuse ``replace(old, new, 1)`` edits
    a foreign line and the mutation reports GREEN: a silent miss is
    indistinguishable from a healthy guard, which is the worst failure of all
    because it soothes instead of shouting (deepseek.md §38);
  * a CONTROL id unrelated to every mutated body runs alongside the expected
    ones. If the control goes red the mutation broke the module (an import error
    reddens everything) and the run is reported as NOT A VERDICT, never as a
    killing;
  * the verdict is read off the EXIT CODE of a run of ONE FULL test id: 0 green,
    1 red, 2 (interrupted/collection error) and 5 ("no tests ran") are NOT
    verdicts;
  * a red on an id we did not expect is a FINDING, printed as one.

ONE DECLARED DEVIATION FROM THE PLAN'S §10, and it is a finding, not a
convenience. The plan words м4 as "вернуть чтение fp ДО освежения" — in the
architecture this step created, the refresh moved OUT of the write handler and
into the seam (`ConnectionManager.execute`), so "read before the refresh" can
only mean "run ``fn`` before the seam's refresh". м4 is that reversal. It
reddens М4 (the cell it exists for) AND both М1 rows, because a reading tool run
before the refresh is stale for exactly the same reason — that pair is declared
in expect_red rather than hidden, and м1 (refresh removed altogether) remains the
sharper tool for the read path alone.

Every wait here is explicit (a pytest run at 300 s), so this module needs no
entry in the timeout sweep's exception list, and it builds no adapter of its own
— no live KiCad is touched, no profile is read, nothing is written to a board.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 300

CONNECTION = "mcp_server/connection.py"
WORKER = "gui/worker.py"

_CELLS = "tests/test_mcp_board_freshness.py::"
M1_POSITION = _CELLS + "test_the_second_call_reports_the_board_as_it_is_now[position]"
M1_FIELD = _CELLS + "test_the_second_call_reports_the_board_as_it_is_now[field]"
M2_ONE = _CELLS + "test_the_board_is_rebuilt_exactly_once_per_call[1]"
M2_THREE = _CELLS + "test_the_board_is_rebuilt_exactly_once_per_call[3]"
M3_TRACKS = _CELLS + ("test_only_the_footprint_reading_tools_pay_a_footprint_read"
                      "[list_tracks]")
M3_VIAS = _CELLS + ("test_only_the_footprint_reading_tools_pay_a_footprint_read"
                    "[list_vias]")
M3_FOOTPRINTS = _CELLS + ("test_only_the_footprint_reading_tools_pay_a_footprint_read"
                          "[list_footprints]")
M4 = _CELLS + "test_a_raw_move_reads_the_footprint_after_the_rebuild"
M5 = _CELLS + ("test_the_gui_module_reexports_the_seam_and_its_call_sites_are_untouched")
M6_RECONNECT = _CELLS + ("test_a_link_lost_while_rebuilding_the_board_reconnects_exactly_once")
# The sibling file's cell: м2 must redden it WITHOUT touching it — that is the
# whole point of §6.1 (a second refresh per call is not a local change, it is a
# change to a guard that was written before this step).
LEGACY_ONE_REFRESH = ("tests/test_mcp_connection.py::"
                      "test_lazy_connect_creates_adapter_only_on_first_use")
CONTROL = ("tests/test_mcp_connection.py::test_reuses_the_same_adapter_across_calls")


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file that carries a
    pyproject.toml.

    WALKED, not counted (`Path(__file__).parent.parent` is right for
    diagnostics/ and WRONG for kicadstamp/diagnostics/), so the subprocess
    always runs pytest where the ids above exist. Inherited verbatim from the
    imprint harness: there, a wrong root made every mutation come back "green"
    by absence."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise SystemExit(f"pyproject.toml not found above {here}")


ROOT = _repo_root()

# The seam's refresh, as it stands after the fix (three lines, and the reconnect
# path's copy says `fresh`, so this block is unique in the file).
_FRESHNESS_BLOCK = (
    "                if not just_loaded:\n"
    "                    refresh_board_before_live_read(adapter)\n"
)

# The seam's refresh WITHOUT its guard — what §6.1 warns about word for word:
# "_ensure_open" already loaded the board on the path that created the adapter,
# so refreshing unconditionally makes the counter 2 after ONE call.
_FRESHNESS_BLOCK_UNGUARDED = (
    "                refresh_board_before_live_read(adapter)\n"
    "                return fn(adapter)\n"
)

# Where the board is LOADED. This is the site a first tool call passes through,
# and it is the only refresh site that every call passes exactly once (see м3's
# note: the seam is not reached while `just_loaded` is True).
_CREATION_LOAD = (
    "            adapter = self._factory(self._timeout_ms)\n"
    "            adapter.refresh_board()\n"
)

# The re-export, which is what keeps the four GUI call sites working (§5).
_REEXPORT_BLOCK = (
    "from kicadstamp.board_freshness import (  # noqa: F401  (re-export, see above)\n"
    "    refresh_board_before_live_read,\n"
    ")\n"
)

MUTATIONS = [
    dict(name="m1-no-refresh-in-execute", rel=CONNECTION,
         old=_FRESHNESS_BLOCK + "                return fn(adapter)\n",
         new="                return fn(adapter)\n",
         expect_red=[M1_POSITION, M1_FIELD, M2_THREE, M4, M6_RECONNECT],
         expect_green=[LEGACY_ONE_REFRESH, M2_ONE, M3_FOOTPRINTS],
         note="м1 — the whole defect, put back: the tool call is answered from "
              "the connect-time cache again. М3's reading row and the one-call "
              "row stay green because nothing else moved."),
    dict(name="m2-refresh-twice-on-the-connecting-path-too", rel=CONNECTION,
         old=_FRESHNESS_BLOCK + "                return fn(adapter)\n",
         new=_FRESHNESS_BLOCK_UNGUARDED,
         expect_red=[LEGACY_ONE_REFRESH, M2_ONE, M2_THREE, M6_RECONNECT],
         expect_green=[M1_POSITION, M1_FIELD],
         note="м2 — §6.1's own trap, in the form the plan words it: the refresh "
              "becomes UNCONDITIONAL, so the call that created the adapter "
              "refreshes on top of the load that creation already did. The "
              "freshness rows cannot tell the difference (more refresh is still "
              "fresh); the COUNTERS can, and one of them belongs to a test "
              "written before this step — reddening it would have been the "
              "change's fault, not a finding. NOTE, and it cost a first run: "
              "adding the refresh to the REUSE path is NOT this mutation — the "
              "connecting call never takes that path, so the one-call counter "
              "and the legacy guard both stayed green."),
    dict(name="m3-full-rebuild-instead-of-invalidating", rel=CONNECTION,
         old=_CREATION_LOAD,
         new=_CREATION_LOAD + "            adapter.get_footprints()\n",
         control="tests/test_mcp_connection.py::test_execute_after_close_raises",
         expect_red=[M3_TRACKS, M3_VIAS],
         expect_green=[M3_FOOTPRINTS, M2_ONE, M1_POSITION],
         note="м3 — «refresh fully where invalidating is enough»: the eager read "
              "stands in for a full Board.refresh() (it IS the board read that "
              "makes one expensive), placed at the LOAD site. TWO FINDINGS, both "
              "measured on a first run: (a) the seam is the WRONG site for it — "
              "the rows of М3 are the connecting call, which never reaches the "
              "seam while `just_loaded` is True, so a seam-side eager read left "
              "them green; the site had to move, not the cells; (b) with the "
              "eager read at the load site, `test_reuses_the_same_adapter_across_"
              "calls` goes red — the sibling file's fake has no `get_footprints`, "
              "so an eager rebuild turns every adapter double written before it "
              "into a lie. The control below therefore moved OFF the seam path (it "
              "still proves the module imports and the manager's own guard works). "
              "Both findings are arguments for the fix's shape: invalidate, do not "
              "rebuild."),
    dict(name="m4-move-the-read-before-the-refresh", rel=CONNECTION,
         old=_FRESHNESS_BLOCK + "                return fn(adapter)\n",
         new=("                if not just_loaded:\n"
              "                    stale = fn(adapter)\n"
              "                    refresh_board_before_live_read(adapter)\n"
              "                    return stale\n"
              "                return fn(adapter)\n"),
         expect_red=[M4, M1_POSITION, M1_FIELD],
         expect_green=[M2_ONE, M3_TRACKS, M6_RECONNECT],
         note="м4 — the plan's shape, translated to this architecture (see the "
              "module docstring): `fn` runs BEFORE the seam's refresh, i.e. the "
              "write path reads its footprint from yesterday. М4 (the cell §3 "
              "exists for) and both М1 rows go red together — a FINDING the plan "
              "does not predict, declared here instead of narrowed away."),
    dict(name="m5-drop-the-reexport", rel=WORKER,
         old=_REEXPORT_BLOCK, new="",
         expect_red=[M5],
         expect_green=[M1_POSITION, M2_ONE, LEGACY_ONE_REFRESH],
         note="м5 — the rename stops being invisible: the four GUI call sites "
              "and the mutation harness that matches their text would break at "
              "import, which is exactly what the re-export exists to prevent."),
]


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
        mutated = original.replace(old, mutation["new"], 1)
        control_id = mutation.get("control", CONTROL)

        path.write_text(mutated, encoding="utf-8")
        try:
            control_status, control_evidence = _run_one(control_id)
            results = [(test_id, *_run_one(test_id))
                       for test_id in mutation["expect_red"]]
            untouched = [(test_id, *_run_one(test_id))
                         for test_id in mutation.get("expect_green", [])]
        finally:
            path.write_text(original, encoding="utf-8")

        print(f"\n=== {mutation['name']}  ({mutation['note']})")
        print(f"    control {control_id.split('::')[-1]}: {control_status.upper()}"
              f" | {control_evidence}")
        if control_status != "green":
            print("    NOT A VERDICT: the control cell did not stay green, so the "
                  "mutation broke the module rather than the behaviour — a red id "
                  "below would prove nothing")
            problems.append(f"{mutation['name']}: control was {control_status}")
            continue

        for test_id, status, evidence in results:
            cell = test_id.split("::")[-1]
            print(f"    {'KILLED' if status == 'red' else status.upper()}: {cell}")
            print(f"        | {evidence}")
            if status != "red":
                problems.append(f"{mutation['name']} -> {cell} ({status})")
        # The mutation must be SURGICAL: a sibling row that guards another
        # property has no business failing here. A sibling that went red too
        # would mean the edit touched more than the template named — the silent
        # miss this harness exists to make visible.
        for test_id, status, evidence in untouched:
            cell = test_id.split("::")[-1]
            print(f"    {'untouched (green)' if status == 'green' else 'TOUCHED ' + status.upper()}"
                  f": {cell}")
            print(f"        | {evidence}")
            if status != "green":
                problems.append(f"{mutation['name']} -> {cell} "
                                f"(expected green, got {status})")

    print("\n" + "=" * 72)
    if problems:
        print("!! not everything went as expected:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    print(f"all {len(MUTATIONS)} mutations KILLED a guard on every expected cell — "
          "the seam's one refresh per call, the freshness of both caches, the "
          "write path's read order and the re-export are pinned by tests that can "
          "fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
