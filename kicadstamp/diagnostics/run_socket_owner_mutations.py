"""Mutation run for the shared-socket ownership guards — Ш1 of
plan_2026_09_23_socket_owner_and_door_docs.md.

Run it by hand from the repository root (it drives pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_socket_owner_mutations

The step's guards are С1–С5 in tests/gui/test_worker.py. The two holes were seen
ALIVE on the base commit (main = 30252ad) before the fix — that is the one half
of the evidence (kicadstamp/diagnostics/probe_socket_owner.py). This harness is
the other half: it proves each guard still BITES after the fix, by putting the
old behaviour back one cell at a time.

Machinery, fuses and the reading of a verdict are inherited from
diagnostics/run_board_door_offender_mutations.py (and the younger
kicadstamp/diagnostics/run_imprint_freshness_mutations.py), with the fuses that
step learned the hard way kept:

  * ``original.count(old) != 1`` — REFUSE an ambiguous template. Without it,
    ``replace(old, new, 1)`` can edit a foreign line and the mutation reports
    GREEN: a silent miss is indistinguishable from a healthy guard, which is the
    worst of all failures because it soothes instead of shouting (deepseek.md
    §38);
  * a CONTROL id unrelated to every mutated body runs alongside. If the control
    goes red, the mutation broke the module (an import error reddens everything)
    and the run is NOT A VERDICT, never a killing;
  * the verdict is read off the EXIT CODE of a run of ONE FULL test id: 0 green,
    1 with a FAILED line red; 2 (interrupted/collection error) and 5 ("no tests
    ran") are NOT verdicts;
  * a red on a cell that is not the mutation's TARGET is printed as a finding
    and explained in the report, never quietly folded into the kill.

Every cell here guards the SAME start()/_release() seam, so a mutation of that
seam legitimately reddens several cells; the harness therefore runs ALL five per
mutation and prints the whole row, instead of pretending the target is the only
cell that can move.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 300

WORKER = "gui/worker.py"
TESTS = "tests/gui/test_worker.py"

C1 = f"{TESTS}::test_a_busy_socket_defers_the_op_and_does_not_seize_the_flag"
C2 = f"{TESTS}::test_the_deferred_op_runs_once_the_socket_frees"
C3 = f"{TESTS}::test_a_second_busy_socket_refuses_through_one_on_error"
C4 = f"{TESTS}::test_a_refused_controller_leaves_the_keep_alive_registry"
C5 = f"{TESTS}::test_a_controller_that_did_not_acquire_never_clears_the_flag"
CELLS = [("С1", C1), ("С2", C2), ("С3", C3), ("С4", C4), ("С5", C5)]
CONTROL = f"{TESTS}::test_controller_holds_and_releases_socket_around_op"

# Each template is the exact block the fix added; putting the OLD behaviour back
# means deleting it (m1/m2) or reverting it to its pre-fix form (m3/m4).
_BUSY_CHECK_IN_START = (
    "        if socket_busy(self._connection):\n"
    "            # Do NOT _acquire(): raising the flag here is exactly the intrusion\n"
    "            # of Д1 (a second REQ inside the tick's in-flight transaction). Show\n"
    "            # the user the op began, arm the single retry, and wait for a free\n"
    "            # socket — the retry starts the worker or refuses through failed.\n"
    "            self._show_busy()\n"
    "            QTimer.singleShot(SNAPSHOT_REFRESH_RETRY_DELAY_MS,\n"
    "                              self._deferred_start)\n"
    "            return\n")

_BUSY_CHECK_IN_RETRY = (
    "        if socket_busy(self._connection):\n"
    "            self._refuse_busy()\n"
    "            return\n")

_CONDITIONAL_CLEAR = (
    "        if self._connection is not None and self._acquired:\n"
    "            self._connection.long_op_active = False\n")

_RETIRE_ON_REFUSAL = (
    '        self._release()\n'
    '        self.failed.emit(_("the board is busy — try again in a moment"))\n'
    '        self._retire()\n')

MUTATIONS = [
    dict(name="m1-start-without-the-busy-check", rel=WORKER,
         old=_BUSY_CHECK_IN_START, new="", target=C1,
         note="м1 — put back the intrusion: start() acquires even while the poll "
              "tick holds the socket. С1 must redden."),
    dict(name="m2-retry-runs-regardless", rel=WORKER,
         old=_BUSY_CHECK_IN_RETRY, new="", target=C3,
         note="м2 — the single retry starts the worker even if the socket is STILL "
              "busy. С3 must redden."),
    dict(name="m3-unconditional-flag-clear", rel=WORKER,
         old=_CONDITIONAL_CLEAR,
         new="        if self._connection is not None:\n"
             "            self._connection.long_op_active = False\n",
         target=C5,
         note="м3 — _release() clears a token it did not raise again. С5 must "
              "redden."),
    dict(name="m4-refusal-does-not-retire", rel=WORKER,
         old=_RETIRE_ON_REFUSAL,
         new='        self._release()\n'
             '        self.failed.emit(_("the board is busy — try again in a moment"))\n',
         target=C4,
         note="м4 — the refusal leaves the controller in _ACTIVE_CONTROLLERS. С4 "
              "must redden."),
]


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file that carries a
    pyproject.toml. WALKED, not counted — `parent.parent` is correct for
    diagnostics/ and WRONG for kicadstamp/diagnostics/ (see the younger
    run_imprint_freshness_mutations.py, where that mistake would have made every
    mutation answer "green" by absence)."""
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
            control_status, control_evidence = _run_one(CONTROL)
            row = [(label, test_id, *_run_one(test_id)) for label, test_id in CELLS]
        finally:
            path.write_text(original, encoding="utf-8")

        print(f"\n=== {mutation['name']}  ({mutation['note']})")
        print(f"    control {CONTROL.split('::')[-1]}: {control_status.upper()}"
              f" | {control_evidence}")
        if control_status != "green":
            print("    NOT A VERDICT: the control cell did not stay green, so the "
                  "mutation broke the module rather than the behaviour — a red id "
                  "below would prove nothing")
            problems.append(f"{mutation['name']}: control was {control_status}")
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
    print(f"all {len(MUTATIONS)} mutations KILLED their target cell — the refusal, "
          "the single retry, the conditional clear and the registry exit are each "
          "pinned by a test that can fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
