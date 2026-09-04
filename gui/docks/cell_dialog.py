# gui/docks/cell_dialog.py
"""
CellDialog — the Cell editor form (CellDock) as a standalone, non-modal
dialog (2026-09-04, plan plan_2026_09_04_celldock_to_dialog.md).

CellDock was a page inside DetailDock's stack (gui/docks/detail_panel.py)
since 2026-08-06; following the Points / Tools / Thermal via / Chain dialog
precedents the form now lives in a DIALOG opened from the Tools menu
(Tools -> Config -> "Edit Cell...") and, for editing an EXISTING cell, from
the Config tree's context menu ("Edit cell..." -> DockHub._edit_cell) — no
longer a permanent dock page.

This dialog is deliberately a thin shell around the ONE live CellDock
instance owned by DockHub (self.cells_dock): the widget keeps receiving the
~2s board-snapshot ticks (refresh_known_roles), and the set_root_path/saved
wiring plus the working-set stage target all point at that same instance.
Opening = show()+raise_()+activateWindow() (non-modal — the user can still
select on the board while the dialog is open); closing via the window X = the
standard QDialog hide, so the instance and its state survive for the next
open. Unlike the Points/Tools dialogs the Cell dialog does NOT auto-hide on
a successful Save — CellDock stages the WHOLE cell into the working set on
every field commit (a cell is edited incrementally across many commits, so
closing on each one would be wrong; File > Save commits to disk, see
gui/docks/cell_editor.py).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .cell_editor import CellDock


class CellDialog(QDialog):
    """Non-modal window hosting the single live CellDock widget."""

    def __init__(self, cell_dock: CellDock, main_window):
        super().__init__(main_window)
        self.cell_dock = cell_dock
        self.setWindowTitle(_("Edit Cell"))
        self.setObjectName("cell_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(cell_dock)
        # Sensible default — CellDock is a four-tab (Components/Vias/Tracks/
        # Nested) table editor, so a fresh dialog starts roomier than the
        # Points/Chain dialogs' 520x560.
        self.resize(720, 600)
