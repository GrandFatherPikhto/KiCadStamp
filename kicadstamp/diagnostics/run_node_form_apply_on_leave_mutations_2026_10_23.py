# kicadstamp/diagnostics/run_node_form_apply_on_leave_mutations_2026_10_23.py
"""Mutation rows for "one Apply button, and the form is accepted on every way out"
(plan_2026_10_23_node_form_without_apply.md, Ч1).

The defect was quiet: an edit lived in the widgets until somebody pressed Apply,
so leaving the node, saving, a rebuild, a dialog Close or the window's exit
dropped it without a word. Every row below removes exactly ONE decision of the
fix and must turn a named cell red:

  M1  a selection change stops accepting the form            (trees_accept.accept_before_switch)
  M2  the dialog's Close stops accepting the edit            (gui/docks/trees_dock._NodeDialog)
  M3  a refused edit no longer holds the selection           (trees_accept.accept_before_switch)
  M4  Save stops settling the form first                     (gui/save_and_discard.settle_forms)
  M5  a mount's subtree redraw takes only the node itself    (trees_accept.redraw_subtree)
  M6  Apply only accepts and never places                    (trees_accept.apply_and_redraw)
  M7  a SECOND Redraw button comes back to the panel row     (gui/docks/trees_dock._form_action_row)
  M8  Discard stops telling the acceptance to stand down     (gui/save_and_discard.discard_unsaved)
  M9  the exit stops settling the form before the question   (gui/exit_guard.confirm_unsaved_before_exit)
  M10 cosmetic control (a comment must change nothing)

Safeguards, as in every harness of this package: a template matching more than
once is refused (rule 38); a row that reddens nothing is a MISS; the control must
survive; every file is restored in `finally` and the restoration is verified.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_node_form_apply_on_leave_mutations_2026_10_23`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACCEPT = "gui/docks/trees_accept.py"
SAVE_DISCARD = "gui/save_and_discard.py"
TREES_DOCK = "gui/docks/trees_dock.py"
EXIT_GUARD = "gui/exit_guard.py"

GUARDS = ("tests/gui/docks/test_trees_accept_on_leave.py "
          "tests/gui/test_save_and_discard.py "
          "tests/gui/docks/test_trees_dock.py")

MUTATIONS = [
    ("M1 a selection change stops accepting the form", ACCEPT,
     "    if accept_form(dock, form, policy=REFUSE):\n"
     "        return True\n",
     "    if True:  # MUTATION: the switch never accepts the draft\n"
     "        return True\n",
     ["test_switching_to_another_node_accepts_the_edit"]),

    # NOTE (measured 23.10): "accept an UNTOUCHED form too" — replacing
    # `if not is_touched(form): return True` with `if False:` — is an EQUIVALENT
    # mutant: apply_node_form (and both anchor apply()s) already refuse to write
    # a form that carries no change, so no cell can see the difference. The
    # `_touched` fast path is therefore not pinned by this row; the property it
    # stands for ("a walk writes nothing") is pinned by
    # test_walking_through_nodes_without_editing_writes_nothing, which counts
    # `_mark_dirty` — and that cell is exercised by M1 and M3.
    ("M2 the dialog's Close stops accepting the edit", TREES_DOCK,
     "        if not trees_accept.accept_form(self._form._dock, self._form,\n"
     "                                        policy=trees_accept.REFUSE):\n"
     "            return\n"
     "        self.reject()\n",
     "        self.reject()  # MUTATION: Close drops the draft\n",
     ["test_the_dialog_close_accepts_the_edit"]),

    ("M3 a refused edit no longer holds the selection", ACCEPT,
     "    restore_selection(dock, form)\n",
     "    pass  # MUTATION: the selection stays where the user clicked\n",
     ["test_a_refused_edit_holds_the_selection_and_names_the_node"]),

    ("M4 Save stops settling the form first", SAVE_DISCARD,
     "    refused = trees_accept.accept_forms(dock, policy=policy)\n",
     "    refused = []  # MUTATION: Save never looks at the open form\n",
     ["test_save_refuses_the_write_when_the_form_does_not_validate"]),

    ("M5 a mount's subtree redraw takes only the node itself", ACCEPT,
     "    refs = subtree_refs(node)\n",
     "    refs = {node.ref}  # MUTATION: the children are left where they were\n",
     ["test_apply_on_a_mount_node_redraws_its_own_subtree"]),

    ("M6 Apply only accepts and never places", ACCEPT,
     "    redraw_form(dock, form)\n"
     "    return True\n",
     "    pass  # MUTATION: the edit is accepted and never placed\n"
     "    return True\n",
     ["test_apply_on_a_mount_node_redraws_its_own_subtree",
      "test_master_detail_apply_button_accepts_and_places"]),

    ("M7 a SECOND Redraw button comes back to the panel row", TREES_DOCK,
     "        row.addWidget(apply_btn)\n"
     "        row.addStretch(1)\n",
     "        row.addWidget(apply_btn)\n"
     "        row.addWidget(QPushButton(_(\"Redraw\")))  # MUTATION: the pair is back\n"
     "        row.addStretch(1)\n",
     ["test_master_detail_row_carries_one_apply_button_whatever_the_kind"]),

    ("M8 Discard stops telling the acceptance to stand down", SAVE_DISCARD,
     "    with trees_accept.discarding(dock):\n",
     "    with trees_accept.discarding(None):  # MUTATION: a discard may accept\n",
     ["test_discard_does_not_accept_and_names_the_dropped_edit"]),

    ("M9 the exit stops settling the form before the question", EXIT_GUARD,
     "    settle_forms(window, refuse=False)\n"
     "    dock = getattr(window, \"root_metadata_dock\", None)\n",
     "    dock = getattr(window, \"root_metadata_dock\", None)  "
     "# MUTATION: nothing is settled\n",
     ["test_the_exit_settles_the_form_before_the_unsaved_question"]),

    ("M10 cosmetic control (a comment must change nothing)", ACCEPT,
     "    return policy == PROCEED\n",
     "    return policy == PROCEED  # MUTATION: cosmetic\n",
     "survive"),
]


def _sweep_pycache() -> None:
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 600 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
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
                  f"{original.count(old)} times — refusing")
            misses += 1
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = _run(guards)
            failed = _failed_ids(out)
            if expect == "survive":
                if failed:
                    print(f"[MISS] {name}: a cosmetic change reddened {failed}")
                    misses += 1
                else:
                    print(f"[OK] {name}: survived, as it must")
                continue
            if not failed:
                print(f"[MISS] {name}: NO failed test — the mutation kills "
                      f"nothing (exit {code})\n{out[-800:]}")
                misses += 1
                continue
            missing = [e for e in expect if not any(e in f for f in failed)]
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
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
