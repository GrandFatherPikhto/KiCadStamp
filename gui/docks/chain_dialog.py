# gui/docks/chain_dialog.py
"""
ChainDialog — the Chain form as a standalone, non-modal dialog (2026-09-01,
plan plan_2026_09_01_rules_to_chains.md).

The old RuleDock was a page inside DetailDock's stack (gui/docks/detail_panel.py)
since 2026-08-05; following the Points / Tools / Thermal via / Extract dialog
precedents the form now lives in a DIALOG launched from the Tools menu
("Add net..." / "Add spoke..."), from the Config tree context menu, and from a
DOUBLE click on a chains: chain node or pad leaf in the Config tree, instead of
a permanent dock page.

This dialog is deliberately a thin shell around the ONE live ChainDock
instance owned by DockHub (self.chain_dock): the widget keeps receiving the
~2s board-snapshot ticks (refresh_known_roles/refresh_known_nets), and the
set_root_path/saved wiring plus the worker-thread Redraw target that same
instance. Opening = show()+raise_()+activateWindow() (non-modal — the user can
still change the board selection while the dialog is open); closing via the
window X = the standard QDialog hide, so the instance and its state survive for
the next open. The dialog auto-hides only after a successful Save (DockHub
wires chain_dock.saved -> self.hide()) — Redraw (placement) stays open so pad
geometry can be tuned against the live result.
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from kicadstamp.i18n import _

from .chain import ChainDock


class ChainDialog(QDialog):
    """Non-modal window hosting the single live ChainDock widget."""

    def __init__(self, chain_dock: ChainDock, main_window):
        super().__init__(main_window)
        self.chain_dock = chain_dock
        self.setWindowTitle(_("Chain"))
        self.setObjectName("chain_dialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(chain_dock)
        # Sensible default — the dock's own sizeHint sizes for the form's
        # current state, but a fresh dialog benefits from a roomier start.
        self.resize(520, 560)
