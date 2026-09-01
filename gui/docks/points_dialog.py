# gui/docks/points_dialog.py
"""
PointsDialog — the Points form as a standalone, non-modal dialog (2026-09-01,
plan plan_2026_09_01_points_dialog.md).

The PointsDock was a page inside DetailDock's stack (gui/docks/detail_panel.py)
since 2026-08-05; following the Thermal via dialog precedent
(plan_2026_09_01_thermal_via_dialog.md) the form now lives in a DIALOG
launched from the Tools menu
("Add point..."), from the Config tree context menu ("Add point..."), and from
a DOUBLE click on a points: leaf in the Config tree, instead of a permanent
dock page.

This dialog is deliberately a thin shell around the ONE live PointsDock
instance owned by DockHub (self.points_dock): the widget keeps receiving the
~2s board-snapshot ticks (refresh_known_roles), and the set_root_path/saved
wiring plus the worker-thread Resolve all target that same instance. Opening =
show()+raise_()+activateWindow() (non-modal — the user can still change the
board selection while the dialog is open); closing via the window X = the
standard QDialog hide, so the instance and its state survive for the next open.
The dialog auto-hides only after a successful Save (DockHub wires
points_dock.saved -> self.hide()).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .points import PointsDock


class PointsDialog(QDialog):
    """Non-modal window hosting the single live PointsDock widget."""

    def __init__(self, points_dock: PointsDock, main_window):
        super().__init__(main_window)
        self.points_dock = points_dock
        self.setWindowTitle(_("Points"))
        self.setObjectName("points_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(points_dock)
        # Sensible default — the dock's own sizeHint sizes for the form's
        # current state, but a fresh dialog benefits from a roomier start.
        self.resize(520, 560)
