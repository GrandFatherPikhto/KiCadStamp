# gui/docks/project_dialog.py
"""
ProjectDialog — the Project form (RootMetadataDock) as a standalone,
non-modal dialog (2026-09-01, plan project_settings_dialogs).

RootMetadataDock was a page inside DetailDock's stack (gui/docks/detail_panel.py)
since 2026-08-03. Following the Extract-dialog (plan_2026_08_31_extract_dialog_
and_hide_existing.md) and Thermal-via-dialog (plan_plan_2026_09_01_thermal_via_
dialog.md) precedents, the form now lives in a DIALOG launched from the File
menu ("Project...") instead of a permanent dock page. The "Project" and
"Settings" tabs are removed from the Detail dock entirely (user decision,
2026-09-01): project management and machine settings are dialogs now.

This dialog is deliberately a thin shell around the ONE live RootMetadataDock
instance owned by DockHub (self.root_metadata_dock): the widget keeps its
root_changed broadcast and Working-file combobox (both feed every other dock
through DockHub's wiring), and the File > Save/Discard/Close actions target the
same instance. Opening = show()+raise_()+activateWindow() (non-modal — the user
can keep using the main window / other dialogs while it's open); closing via
the window X = the standard QDialog hide, so the instance and its state
survive for the next open. The Ctrl+O/Ctrl+N hotkeys (action_open/action_new)
stay app-wide regardless of the dialog's visibility (they are registered on
the main window — see gui/hotkeys.py).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .root_metadata import RootMetadataDock


class ProjectDialog(QDialog):
    """Non-modal window hosting the single live RootMetadataDock widget."""

    def __init__(self, root_metadata_dock: RootMetadataDock, main_window):
        super().__init__(main_window)
        self.root_metadata_dock = root_metadata_dock
        self.setWindowTitle(_("Project"))
        self.setObjectName("project_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(root_metadata_dock)
        # Sensible default — the dock's own sizeHint sizes for the form's
        # current state, but a fresh dialog benefits from a roomier start.
        self.resize(560, 620)
