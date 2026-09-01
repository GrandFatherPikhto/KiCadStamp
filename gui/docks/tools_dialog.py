# gui/docks/tools_dialog.py
"""
ToolsDialog — the Entity electrical-fields form (ToolsDock) as a standalone,
non-modal dialog (2026-09-01, plan plan_2026_09_01_tools_dialog_and_entity_roles.md).

The ToolsDock was a page inside DetailDock's stack (gui/docks/detail_panel.py)
since 2026-08-30 (phase 5.2 stage 3); following the Thermal via / Extract /
Points dialog precedents (plan_2026_09_01_thermal_via_dialog.md /
plan_2026_08_31_extract_dialog_and_hide_existing.md /
plan_2026_09_01_points_dialog.md) the form now lives in a DIALOG launched from
the Tools menu ("Edit template...") and from a DOUBLE click on an Entities leaf
in the Config tree, instead of a permanent dock page.

This dialog is deliberately a thin shell around the ONE live ToolsDock
instance owned by DockHub (self.tools_dock): the widget keeps receiving the
~2s board-snapshot ticks (refresh_known_nets), and the set_root_path/saved
wiring all target that same instance. Opening = show()+raise_()+activateWindow()
(non-modal — the user can still change the board selection while the dialog is
open); closing via the window X = the standard QDialog hide, so the instance
and its state survive for the next open. The dialog auto-hides after a
successful edit (DockHub wires tools_dock.saved -> self.hide()).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .tools import ToolsDock


class ToolsDialog(QDialog):
    """Non-modal window hosting the single live ToolsDock widget."""

    def __init__(self, tools_dock: ToolsDock, main_window):
        super().__init__(main_window)
        self.tools_dock = tools_dock
        self.setWindowTitle(_("Edit template"))
        self.setObjectName("tools_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tools_dock)
        # Sensible default — the dock's own sizeHint sizes for the form's
        # current state, but a fresh dialog benefits from a roomier start.
        self.resize(520, 560)
