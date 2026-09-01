# gui/docks/settings_dialog.py
"""
SettingsDialog — the Settings browser as a standalone, MODAL dialog
(2026-09-01, plan project_settings_dialogs).

The Settings tab (ConfiguratorDock) was a page inside DetailDock's stack
(gui/docks/detail_panel.py) since 2026-08-15. It is now a two-pane browser
(QTreeWidget of categories on the left, the matching settings page on the
right — see gui/docks/configurator.py) hosted in this dialog, launched from
the Tools menu ("Settings..."). The "Project" and "Settings" tabs are removed
from the Detail dock entirely (user decision, 2026-09-01): project management
and machine settings are dialogs now.

This dialog is deliberately a thin shell around the ONE live ConfiguratorDock
instance owned by DockHub (self.configurator_dock), plus an OK/Apply/Cancel
button row. Settings apply EXPLICITLY (the modal contract):
- Apply  — ConfiguratorDock.apply(): writes the draft to gui_state.json and
  fires the side effects, dialog stays open;
- OK     — apply() then close;
- Cancel — ConfiguratorDock.cancel()/reject(): discards the draft (widgets are
  re-seeded from the persisted state), close without applying.
open_modal() re-seeds the widgets from the persisted state before exec(), so a
draft that was never applied (or a change made by another code path) is never
what the next opening shows.
"""
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from kicadstamp.i18n import _

from .configurator import ConfiguratorDock


class SettingsDialog(QDialog):
    """Modal window hosting the single live ConfiguratorDock settings browser,
    with OK/Apply/Cancel (explicit apply — see module docstring)."""

    def __init__(self, configurator_dock: ConfiguratorDock, main_window):
        super().__init__(main_window)
        self.configurator_dock = configurator_dock
        self.setWindowTitle(_("Settings"))
        self.setObjectName("settings_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(configurator_dock)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.ok_button = QPushButton(_("OK"))
        self.ok_button.setDefault(True)
        self.apply_button = QPushButton(_("Apply"))
        self.cancel_button = QPushButton(_("Cancel"))
        self.ok_button.clicked.connect(self._on_ok)
        self.apply_button.clicked.connect(self._on_apply)
        self.cancel_button.clicked.connect(self._on_cancel)
        button_row.addWidget(self.ok_button)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        # Sensible default — the browser's own sizeHint sizes for the largest
        # page (the Hotkeys list), but a fresh dialog benefits from a roomier
        # start.
        self.resize(780, 540)

    def open_modal(self) -> None:
        """Re-seed the widgets from the persisted state (a previous Cancel or
        an external change must not leak into this opening) and run the modal
        loop. Called by MainWindow's Tools > "Settings..." handler."""
        self.configurator_dock.reload_from_state()
        self.exec()

    def _on_apply(self) -> None:
        self.configurator_dock.apply()

    def _on_ok(self) -> None:
        self.configurator_dock.apply()
        self.accept()

    def _on_cancel(self) -> None:
        self.configurator_dock.cancel()
        self.reject()

    def reject(self) -> None:
        """Window X / Esc — same discard-the-draft semantics as the Cancel
        button (a draft that was never applied must not become the persisted
        state)."""
        self.configurator_dock.cancel()
        super().reject()
