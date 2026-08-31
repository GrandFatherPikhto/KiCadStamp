# gui/docks/extract_dialog.py
"""
ExtractDialog — the Extract form as a standalone, non-modal dialog
(2026-08-31, plan extract_dialog_and_hide_existing.md).

The Extract dock was merged into DetailDock's stack (gui/docks/detail_panel.py)
back on 2026-08-03; since then every extract flow — "New Extract...",
"Add extract profile..." and clicking an extract_profiles leaf — opened that
dock page. With nets auto-derived and the cluster auto-fill in place
(plan_2026_08_31_extract_new_profile_cluster_autofill.md) the form became a
simple "select -> Extract" capture, so it now lives in a DIALOG launched from
the Config tree context menu instead of a permanent dock page.

This dialog is deliberately a thin shell around the ONE live ExtractDock
instance owned by DockHub (self.extract_dock): the widget keeps receiving the
~400ms selection-watch ticks, and the set_root_path/saved wiring plus the
worker-thread extract all target that same instance. Opening = show()+raise_()
+activateWindow() (non-modal — the user can still change the board selection
while the dialog is open and watch the aliases/origin update live); closing
via the window X = the standard QDialog hide, so the instance and its state
survive for the next open. The dialog auto-hides after a successful Extract
(DockHub wires extract_dock.saved -> self.hide()).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .extract import ExtractDock


class ExtractDialog(QDialog):
    """Non-modal window hosting the single live ExtractDock widget."""

    def __init__(self, extract_dock: ExtractDock, main_window):
        super().__init__(main_window)
        self.extract_dock = extract_dock
        self.setWindowTitle(_("Extract"))
        self.setObjectName("extract_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(extract_dock)
        # Sensible default — the dock's own sizeHint sizes for the current
        # tab only, but a fresh dialog benefits from a roomier start.
        self.resize(720, 560)
