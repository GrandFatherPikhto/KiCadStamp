# kicadstamp/diagnostics/claude_probe_cell_refresh_stale_dock_2026_10_03.py
"""
Probe: does the context menu's "Update from selection..." re-read a cell
that was REWRITTEN on disk while CellDock still holds it?

Live case 2026-10-03 (techdocs/handoff/claude/diag_2026_10_03_cell_refresh_stale_dock.md):
cell 'fpga' was loaded into CellDock with 7 roles, then deleted and
re-extracted with 14 roles and saved. Refresh then reported the 7 new roles
as "in the selection but not in the cell".

Suspect: gui/docks/cell_editor.py refresh_from_selection_requested calls
load_entry ONLY when name or path differ from the loaded ones.

Headless, no KiCad. The refresh read itself is replaced by a recorder that
captures the role list the dock WOULD hand to build_refresh_plan
(self._components) — that list is exactly the payload "components" of
_read_refresh_from_selection.

Cases:
  C0 control — first load: the recorder sees the on-disk roles.
  C1 grown   — the cell is rewritten on disk with MORE roles (the live case).
  C2 shrunk  — the cell is rewritten on disk with FEWER roles.
  C3 deleted — the cell is removed from disk entirely.
  C4 control — the Cells-tree click path (load_entry directly) after C1:
               the dock does see the new roles there.

Exit code: 0 = every case sees the disk, 1 = the stale-dock bug reproduced
(at least one of C1-C3 used the old list), 2 = the rig itself is broken
(a control failed).

Run:  .venv/Scripts/python -m kicadstamp.diagnostics.claude_probe_cell_refresh_stale_dock_2026_10_03
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from gui.docks.cell_editor import CellDock  # noqa: E402
from kicadstamp.config.sexp_format import dict_to_sexp  # noqa: E402

OLD_ROLES = ["FPGA", "CH0_R_TERM_P", "CH0_R_TERM_N"]
NEW_ROLES = OLD_ROLES + ["DAC0_SPI_CS_PU", "FPGA_NCFG_PU"]


def _cell(roles):
    return {"layer": "F.Cu", "components": [
        {"role": r, "offset_along_mm": float(i), "offset_across_mm": 0.0,
         "angle_deg": 0.0, "net_template": "GND"}
        for i, r in enumerate(roles)]}


def _write(path: Path, cells: dict) -> None:
    """Write, then push mtime forward: the readers cache by (path, mtime_ns),
    and on Windows two writes can land on the same tick (same reason as
    tests/fakes/write_later.py)."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(dict_to_sexp({"cells": cells}), encoding="utf-8")
    after = path.stat().st_mtime_ns
    if after <= before:
        os.utime(path, ns=(before + 10_000_000, before + 10_000_000))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    window = QMainWindow()
    window.connection = SimpleNamespace(board=None, is_connected=False, snapshot=[])

    tmp = Path(tempfile.mkdtemp(prefix="probe_stale_dock_"))
    root = tmp / "root.sexp"
    _write(root, {"fpga": _cell(OLD_ROLES), "other": _cell(["X"])})

    dock = CellDock(window)
    dock.set_root_path(root)

    seen = []
    dock._on_refresh_geometry = lambda: seen.append([c.get("role") for c in dock._components])

    def refresh():
        seen.clear()
        dock.refresh_from_selection_requested("fpga", dock._path or root)
        return seen[0] if seen else None

    results = []

    def case(tag, got, want, control=False):
        ok = got == want
        results.append((tag, ok, control))
        print(f"{tag:<11} {'OK   ' if ok else 'STALE'} dock={got} disk={want}")

    # C0 — first load through the menu path (name differs -> load_entry runs).
    case("C0 control", refresh(), OLD_ROLES, control=True)

    # C1 — grown on disk, dock still holds 'fpga'.
    _write(root, {"fpga": _cell(NEW_ROLES), "other": _cell(["X"])})
    case("C1 grown", refresh(), NEW_ROLES)

    # C4 — the tree-click path reloads unconditionally.
    dock.load_entry("fpga", root)
    case("C4 control", [c.get("role") for c in dock._components], NEW_ROLES, control=True)

    # C2 — shrunk on disk.
    _write(root, {"fpga": _cell(OLD_ROLES[:2]), "other": _cell(["X"])})
    case("C2 shrunk", refresh(), OLD_ROLES[:2])

    # C3 — deleted on disk: the honest answer is "no such cell" (refresh must
    # not run on a ghost). Reload leaves _components empty -> recorder sees [].
    dock.load_entry("fpga", root)
    _write(root, {"other": _cell(["X"])})
    case("C3 deleted", refresh(), [])

    if not all(ok for _, ok, control in results if control):
        print("RIG BROKEN: a control failed")
        return 2
    stale = [tag for tag, ok, control in results if not control and not ok]
    print("REPRODUCED: " + ", ".join(stale) if stale else "NOT reproduced")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
