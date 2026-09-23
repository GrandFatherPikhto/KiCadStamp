"""Acceptance mutations for 35ab638, written by Claude — NOT a rerun of the
Daemon's own rig. Each one removes a guard the entry claims is now pinned and
asks whether the promised cell, BY FULL ID, goes red.

Safety rails kept from the house rigs (rule 38): a pattern that does not occur
EXACTLY once is refused rather than substituted; a run that collected no tests
is a MISS, not a kill; the verdict is per full test id; a red on a DIFFERENT
cell is reported as a finding instead of being counted as a win.
"""
import subprocess, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY_BIN = str(ROOT / ".venv" / "bin" / "python")

MUTATIONS = [
    ("A1 idempotence of _show_busy", "gui/worker.py",
     "        if self._visual_shown:\n            return\n",
     "",
     ["tests/gui/test_worker.py::test_a_deferred_op_leaves_the_guard_widgets_enabled"]),

    ("A2 _release in _refuse_busy", "gui/worker.py",
     '        self._release()\n        self.failed.emit(_("the board is busy',
     '        self.failed.emit(_("the board is busy',
     ["tests/gui/test_worker.py::test_a_refused_deferred_op_leaves_the_guard_widgets_enabled"]),

    ("A3 _retire in _abandon", "gui/worker.py",
     "        _notify_busy(None)\n        self._retire()\n",
     "        _notify_busy(None)\n",
     ["tests/gui/test_worker.py::test_an_abandoned_deferred_start_leaves_the_keep_alive_registry"]),

    ("A4 forget before rebuild", "gui/connection.py",
     "        self._board.forget_role_cluster_values()\n        self._rebuild_snapshot()",
     "        self._rebuild_snapshot()",
     ["tests/test_overrides_store_reload.py"]),

    ("A5 reprojection in set_project_config", "gui/connection.py",
     "        # path below, so the same one call, under the same capability check.\n"
     "        self._reproject_snapshot_after_store_change()",
     "",
     ["tests/test_overrides_store_reload.py"]),

    ("A6 distribution to the docks", "gui/dock_hub.py",
     '        self._safe_call("components tree rows after a store write",\n'
     "                        self.tree_dock.set_footprints, snapshot)",
     "",
     ["tests/test_overrides_store_reload.py"]),
]


def run(paths):
    r = subprocess.run([PY_BIN, "-m", "pytest", *paths, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=400)
    return r.returncode, r.stdout


def main():
    print(f"{'мутация':<42} {'вердикт':<12} что покраснело")
    print("-" * 100)
    for name, rel, old, new, targets in MUTATIONS:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<42} {'НЕДЕЙСТВИТЕЛЬНА':<12} шаблон встречается {n} раз — не подставляю")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, out = run(targets)
            tail = [l for l in out.splitlines() if l.startswith("FAILED")]
            collected_nothing = "no tests ran" in out or "error" in out.lower() and code == 2
            if collected_nothing:
                verdict = "ПРОМАХ"
                detail = "прогон не собрал тесты — замер не состоялся"
            elif code == 0:
                verdict = "ВЫЖИЛА"
                detail = "ни один сторож не покраснел"
            else:
                verdict = "УБИТА"
                detail = f"{len(tail)} красных: " + "; ".join(t.split(" ")[1].split("::")[-1] for t in tail[:4])
            print(f"{name:<42} {verdict:<12} {detail}")
        finally:
            f.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
