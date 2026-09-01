# gui/docks/thermal_via_dialog.py
"""
ThermalViaDialog — the Thermal via form as a standalone, non-modal dialog
(2026-09-01, plan plan_2026_09_01_thermal_via_dialog.md).

The ThermalViaArrayDock was a page inside DetailDock's stack (gui/docks/
detail_panel.py) since 2026-08-03; following the standalone-dialog move for
the Extract form (2026-08-31) the form now lives in a DIALOG launched from
the Tools menu ("Place thermal vias...") and from the
Config tree context menu ("Add thermal via pad..."/a thermal_via_arrays leaf
click) instead of a permanent dock page.

This dialog is deliberately a thin shell around the ONE live
ThermalViaArrayDock instance owned by DockHub (self.thermal_via_dock): the
widget keeps receiving the ~2s board-snapshot ticks (refresh_known_roles/
refresh_known_nets), and the set_root_path/saved wiring plus the worker-thread
Redraw all target that same instance. Opening = show()+raise_()+activateWindow()
(non-modal — the user can still change the board selection while the dialog is
open); closing via the window X = the standard QDialog hide, so the instance
and its state survive for the next open. The dialog auto-hides only after a
successful Save (DockHub wires thermal_via_dock.saved -> self.hide()) — Redraw
(placement) stays open so grid geometry can be tuned against the live result.
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .thermal_via import ThermalViaArrayDock


class ThermalViaDialog(QDialog):
    """Non-modal window hosting the single live ThermalViaArrayDock widget."""

    def __init__(self, thermal_via_dock: ThermalViaArrayDock, main_window):
        super().__init__(main_window)
        self.thermal_via_dock = thermal_via_dock
        self.setWindowTitle(_("Thermal via"))
        self.setObjectName("thermal_via_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(thermal_via_dock)
        # Sensible default — the dock's own sizeHint sizes for the form's
        # current state, but a fresh dialog benefits from a roomier start.
        self.resize(560, 520)
