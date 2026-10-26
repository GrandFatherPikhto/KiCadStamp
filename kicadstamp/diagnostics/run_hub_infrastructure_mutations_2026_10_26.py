# kicadstamp/diagnostics/run_hub_infrastructure_mutations_2026_10_26.py
"""Mutation rows for Ч1 of plan_2026_10_22_dock_hub_reorganization — the constructor cut into
ten named steps, and the infrastructure moved to `gui/hub_infrastructure.py`.

Guards: `tests/gui/test_hub_composition_steps.py` (Ч1-С1…С7) plus the TWO existing cells the
moved rules answer to, named explicitly (the whole of `test_phase3_wiring.py` would only make
the run slow — the row that matters there is the swap):

  Ч1-M1 `RootLogFile.swap` keeps the previous FileHandler attached     (hub_infrastructure.py)
  Ч1-M2 the root change no longer CLEARS the working set               (hub_infrastructure.py)
  Ч1-M3 the root change no longer tells the window about the dirt      (hub_infrastructure.py)
  Ч1-M4 a composition step disappears from the constructor             (dock_hub.py)
  Ч1-M5 the diagnostics reminder disappears from the tail              (dock_hub.py)
  Ч1-M6 cosmetic control (a comment must change nothing)

What these rows do NOT claim: M4/M5 redden STRUCTURAL cells (the shape of `__init__` and the
order inside `_finish_composition`), not behaviour. That is the honest limit — the split is
the deliverable of this part, so its shape is what a cell pins.

NOT a row here, deliberately: the plan's "move `refresh_hotkeys()` before `LogDock`". Measured
on the base: `ConfiguratorDock` already refreshes its own hotkeys page at construction and
NOTHING registers a hotkey after it, so that move takes no promise away and would report a
MISS — the same self-deception the Ч2б-2 step recorded twice (rule 38).

Safeguards as everywhere in this package: a template matching more than once is refused, a row
that reddens NOTHING is a MISS, the control must survive, and every file is restored in
`finally` with the restore verified by text.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_hub_infrastructure_mutations_2026_10_26`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INFRA = "gui/hub_infrastructure.py"
HUB = "gui/dock_hub.py"

GUARDS = (
    "tests/gui/test_hub_composition_steps.py "
    "tests/gui/test_phase3_wiring.py::test_dock_hub_swaps_the_file_handler_when_the_root_file_changes "
    "tests/config/test_config_format3_wiring.py::test_gui_root_change_sets_and_clears_the_active_root"
)

CELL_SWAPS = "test_the_root_log_file_keeps_exactly_one_handler_across_swaps"
CELL_HUB_SWAP = "test_dock_hub_swaps_the_file_handler_when_the_root_file_changes"
CELL_DIRTY = "test_the_dirty_indicator_hears_a_root_change"
CELL_STEPS = "test_the_constructor_is_named_steps_and_the_finish_comes_last"
CELL_TAIL = "test_the_finish_wires_then_refreshes_hotkeys_then_speaks_about_diagnostics"

MUTATIONS = [
    # The ONE rule of the new owner: a swap detaches the old handler. Without it one process
    # writes two files, and the cell that says so is the pair (unit + the hub's own swap cell).
    ("Ч1-M1 the previous FileHandler stays attached", INFRA,
     "        if self.current is not None:\n"
     "            detach_handler(self.current)\n"
     "            self.current = None\n",
     "        if self.current is not None:\n"
     "            logging.debug(\"the previous handler stays\")  # MUTATION\n"
     "            self.current = None\n",
     [CELL_SWAPS, CELL_HUB_SWAP]),

    # "A root switch starts with a clean working set" — the half the format-3 cell does NOT
    # hold (it asserts the active graph root only), so this row is what keeps it alive.
    ("Ч1-M2 the working set is not cleared", INFRA,
     "    WORKING_SET.enabled = path is not None\n"
     "    WORKING_SET.clear()\n"
     "    set_active_graph_root(path)\n",
     "    WORKING_SET.enabled = path is not None\n"
     "    set_active_graph_root(path)  # MUTATION: no clear\n",
     [CELL_DIRTY]),

    ("Ч1-M3 the window is not told about the dirt", INFRA,
     "    set_active_graph_root(path)\n"
     "    host._update_dirty_indicator()\n",
     "    set_active_graph_root(path)\n"
     "    logging.debug(\"the dirty indicator is not told\")  # MUTATION\n",
     [CELL_DIRTY]),

    ("Ч1-M4 a composition step disappears", HUB,
     "        self._expose_docks_for_view_menu()\n",
     "        # MUTATION: the docks list is never exposed\n",
     [CELL_STEPS]),

    ("Ч1-M5 the diagnostics reminder disappears", HUB,
     "        self.configurator_dock.sync_diagnostics_recording(reminder=True)\n",
     "        pass  # MUTATION: no diagnostics reminder\n",
     [CELL_TAIL]),

    ("Ч1-M6 cosmetic control (a comment must change nothing)", INFRA,
     "# gui/hub_infrastructure.py\n",
     "# gui/hub_infrastructure.py  # MUTATION: cosmetic\n",
     "survive"),
]


def _sweep_pycache() -> None:
    for directory in ROOT.rglob("__pycache__"):
        shutil.rmtree(directory, ignore_errors=True)


def _run(guards: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen")
    _sweep_pycache()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *guards.split(), "-q", "-rf",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT: the guard run did not finish within 900 s"
    return proc.returncode, proc.stdout + proc.stderr


def _failed_ids(out: str) -> list:
    return [line.split(" ", 1)[1].split(" - ")[0]
            for line in out.splitlines() if line.startswith("FAILED ")]


def _verdict(name: str, failed: list, expect) -> int:
    if expect == "survive":
        if failed:
            print(f"[MISS] {name}: a cosmetic change reddened {failed}")
            return 1
        print(f"[OK] {name}: survived, as it must")
        return 0
    if not failed:
        print(f"[MISS] {name}: NO failed test — the mutation kills nothing")
        return 1
    missing = [e for e in expect if not any(e in f for f in failed)]
    if missing:
        print(f"[MISS] {name}: expected these to redden, they did not: {missing}\n"
              f"  actually failed: {failed}")
        return 1
    print(f"[KILL] {name}: {len(failed)} red — {failed}")
    return 0


def main() -> int:
    guards = os.environ.get("GUARDS", GUARDS)
    print(f"guards: {guards}")
    code, out = _run(guards)
    if code != 0:
        print("BASELINE IS RED — fix that first:\n", out[-3000:])
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
            _code, out = _run(guards)
            misses += _verdict(name, _failed_ids(out), expect)
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8")
            assert restored == original, f"restore FAILED for {rel}"
    _sweep_pycache()
    print(f"\nverdict: {len(MUTATIONS) - misses} as expected, {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
