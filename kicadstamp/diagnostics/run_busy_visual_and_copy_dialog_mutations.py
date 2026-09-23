"""Mutation run for the cells step 3 added — cells on behaviour that was RIGHT
but unguarded (plan_2026_09_24_reload_store_snapshot §6.1/§6.2).

Run it by hand from the repository root (it drives pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_busy_visual_and_copy_dialog_mutations

Five mutations, two files, six target cells:

  * gui/worker.py — the three properties of the DEFERRED busy path (the visual
    is idempotent, the refusal releases it, an abandoned retry touches nothing
    and leaves the registry);
  * gui/docks/cell_editor.py — the confirmation's second operand and its default
    button.

None of them is a live defect: the mutations below are exactly the ones that
survived the WHOLE tests/gui run of 2026-09-23 (2595 tests), because the fixtures
made the thing being checked invisible — every target cell had a tracks-only or
a still-busy shape it could not reach. A named trap without a cell is not closed,
only spoken against.

Machinery, fuses and the reading of a verdict are inherited from
kicadstamp/diagnostics/run_reload_store_snapshot_mutations.py (itself from
run_socket_owner_mutations.py): the `original.count(old) != 1` refusal, TWO
controls with "at least one green or NOT A VERDICT", the exit-code verdict of a
run of ONE FULL test id (2 and 5 are NOT verdicts), and "ALSO RED" printed as a
finding instead of being folded into the kill.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 180

WORKER = "gui/worker.py"
CELL_EDITOR = "gui/docks/cell_editor.py"
WORKER_TESTS = "tests/gui/test_worker.py"
CELL_TESTS = "tests/gui/test_cell_editor.py"

W1 = f"{WORKER_TESTS}::test_a_deferred_op_leaves_the_guard_widgets_enabled"
W2 = f"{WORKER_TESTS}::test_a_refused_deferred_op_leaves_the_guard_widgets_enabled"
W3 = (f"{WORKER_TESTS}::"
      "test_an_abandoned_deferred_start_never_touches_the_dead_widget")
W4 = (f"{WORKER_TESTS}::"
      "test_an_abandoned_deferred_start_leaves_the_keep_alive_registry")
E1 = (f"{CELL_TESTS}::"
      "test_copy_placement_confirms_when_only_tracks_would_be_replaced")
E2 = f"{CELL_TESTS}::test_copy_placement_dialog_defaults_to_cancel"
CELLS = [("W1", W1), ("W2", W2), ("W3", W3), ("W4", W4), ("E1", E1), ("E2", E2)]

#: One control per file: the first is the only existing test about `_prior_enabled`
#: (it walks the DIRECT path, so it must survive the idempotence mutation), the
#: second is the copper-confirmation cell of 2026-09-23 (it has vias, so it must
#: survive the operand mutation). At least one must stay green per mutation.
CONTROLS = [
    ("в worker-файле",
     f"{WORKER_TESTS}::test_previously_disabled_guard_widget_stays_disabled"),
    ("в cell_editor-файле",
     f"{CELL_TESTS}::test_copy_placement_replaces_copper_when_confirmed"),
]

_VISUAL_IDEMPOTENCE = (
    "        if self._visual_shown:\n"
    "            return\n"
    "        self._visual_shown = True\n")

_REFUSAL_RELEASE = (
    "        self._release()\n"
    '        self.failed.emit(_("the board is busy — try again in a moment"))\n'
    "        self._retire()\n")

_OWNER_GONE_CHECK = (
    "        if qt_object_gone(self) or any(\n"
    "                qt_object_gone(w) for w in self._widgets):\n"
    "            # The dock/dialog was destroyed during the 120 ms delay. No deleted\n"
    "            # widget may be touched — but the two APPLICATION-wide visuals are\n"
    "            # not widgets and must still come down, or a stuck hourglass\n"
    "            # outlives the window that asked for the operation.\n"
    "            self._abandon()\n"
    "            return\n")

_ABANDON_RETIRE = (
    "        self._set_wait_cursor(False)\n"
    "        _notify_busy(None)\n"
    "        self._retire()\n")

_TARGET_TRACKS = (
    "                target_vias=self._vias,\n"
    "                target_tracks=self._tracks)\n")

_DEFAULT_BUTTON = (
    "                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,\n"
    "                QMessageBox.StandardButton.Cancel)\n")

MUTATIONS = [
    dict(name="w1-the-visual-is-shown-twice", rel=WORKER,
         old=_VISUAL_IDEMPOTENCE, new="        self._visual_shown = True\n",
         target=W1,
         note="w1 — _show_busy() may run twice on the deferred path and the "
              "second pass remembers the state it just created. W1 must redden."),
    dict(name="w2-the-refusal-keeps-the-visual", rel=WORKER,
         old=_REFUSAL_RELEASE,
         new=('        self.failed.emit(_("the board is busy — try again in a '
              'moment"))\n        self._retire()\n'),
         target=W2,
         note="w2 — a refused retry leaves the guard widgets disabled for the "
              "rest of the session. W2 must redden."),
    dict(name="w3-no-owner-gone-check", rel=WORKER,
         old=_OWNER_GONE_CHECK, new="", target=W3,
         note="w3 — the retry touches a widget whose C++ object is gone. W3 must "
              "redden (the touch raises)."),
    dict(name="w4-abandon-leaks-the-controller", rel=WORKER,
         old=_ABANDON_RETIRE,
         new="        self._set_wait_cursor(False)\n        _notify_busy(None)\n",
         target=W4,
         note="w4 — an abandoned deferred start leaves its controller in "
              "_ACTIVE_CONTROLLERS for ever. W4 must redden."),
    dict(name="e1-target-tracks-not-passed", rel=CELL_EDITOR,
         old=_TARGET_TRACKS,
         new="                target_vias=self._vias)\n",
         target=E1,
         note="e1 — the plan is built without the target's tracks, so a cell "
              "with tracks and NO vias copies over them without asking. E1 must "
              "redden."),
    dict(name="e2-enter-confirms-the-copy", rel=CELL_EDITOR,
         old=_DEFAULT_BUTTON,
         new=("                QMessageBox.StandardButton.Yes | "
              "QMessageBox.StandardButton.Cancel,\n"
              "                QMessageBox.StandardButton.Yes)\n"),
         target=E2,
         note="e2 — the destructive copy's DEFAULT button becomes Yes, i.e. a "
              "stray Enter confirms it. E2 must redden."),
]


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file carrying a
    pyproject.toml — WALKED, not counted (see the parent harness)."""
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
                  "ambiguous (deepseek.md §38)")
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
            print("    NOT A VERDICT: neither control stayed green — the mutation "
                  "broke the module rather than the behaviour")
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
    print(f"all {len(MUTATIONS)} mutations KILLED their target cell — the deferred "
          "busy visual, the refusal's release, the dead-owner guard, the abandoned "
          "controller and the two halves of the copy confirmation are each pinned "
          "by a cell that can fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
