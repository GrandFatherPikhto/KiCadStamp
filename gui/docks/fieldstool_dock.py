# gui/docks/fieldstool_dock.py
"""
FieldsToolDock — embeds gui.fieldstool_window.MainWindow whole, as-is,
inside one QDockWidget via setWidget(). QDockWidget.setWidget() accepts any
QWidget and QMainWindow is one, so fieldstool's own internal docking keeps
working nested here.

This dock embeds fieldstool's window with the main GUI's OWN BoardConnection
(one kipy client, one REQ socket) — the embedded window never creates or
polls a connection of its own (kipy's REQ socket allows exactly one request
in flight, so a second independent timer on the same connection would
interleave requests mid-flight). One connection, one polling loop: the main
GUI's single 2s/400ms poll feeds the embedded window through
push_live_selection()/set_connection_status()/push_live_snapshot(). A
standalone entry point (fieldstool_gui.py, its own connection + timers)
existed until 2026-08-02, retired as pure duplication of this tab.

fieldstool's own Components tree (fieldstool/gui/tree.py) was retired
2026-08-01 — the main GUI's own Components tree (gui/docks/
role_cluster_tree.py) covers the same job in its "Not yet applied" mode
when embedded here (reading this window's parsed schematic components
directly). The refresh callback that keeps that view in sync with an
explicit Rescan/Apply in this tab is wired through this dock's
components_changed signal (connected in gui/main_window.py, the composition
root) — this dock never reaches back into main_window.tree_dock itself.

2026-08-03: PendingChangesDock is no longer fieldstool's own internal dock —
DockHub builds ONE shared instance (tabbed with Log at the main window's
bottom) and injects it here, so RoleClusterTreeDock's live-board writes and
this window's own Stage share the same diff view (see gui/docks/pending.py
and gui/fieldstool_window.py's module docstrings for the redesign).
"""
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDockWidget

from kicadstamp.i18n import _
from kicadstamp.utils.paths import resolve_config_relative_path

from .. import yaml_io
from ..fieldstool_window import MainWindow as FieldsToolMainWindow
from .pending import PendingChangesDock


class FieldsToolDock(QDockWidget):
    # Fired when fieldstool's window rescans the schematic (its
    # on_components_changed hook) — the main GUI's Components tree listens
    # to refresh its "Not yet applied" view (see gui/main_window.py).
    components_changed = pyqtSignal()

    def __init__(self, main_window, connection, pending_dock: Optional[PendingChangesDock] = None):
        super().__init__(_("fieldstool"), main_window)
        # The main GUI's OWN BoardConnection (one kipy client, one REQ
        # socket): one connection + one polling loop instead of two
        # independent ones (see module docstring).
        self.window = FieldsToolMainWindow(connection=connection, pending_dock=pending_dock)
        self.window.on_components_changed = self.components_changed.emit
        self.setWidget(self.window)

    @property
    def components(self):
        """Public read-only access to fieldstool's parsed-schematic list —
        delegates to the embedded window's own public property, so the main
        GUI's tree never touches the private `_components`."""
        return self.window.components

    @property
    def pending_refs(self):
        """Refs with an outstanding schematic-vs-board discrepancy right
        now — delegates to the embedded window's own public property (see
        its docstring). The main GUI's Components tree filters its "Not yet
        applied" mode by this instead of showing every schematic component
        unconditionally."""
        return self.window.pending_refs

    def pick_group(self, field: str, value: str, refs: List[str]) -> None:
        """Route a group-node click from the main GUI's Components tree into
        fieldstool's existing _on_group_picked() staging/combo-fill logic."""
        self.window._on_group_picked(field, value, refs)

    def pick_leaf(self, refs: List[str]) -> None:
        """Route a leaf-node click from the main GUI's Components tree into
        fieldstool's existing _on_tree_leaf_picked() logic."""
        self.window._on_tree_leaf_picked(refs)

    def push_live_selection(self, refs: List[str]) -> None:
        """Route the main GUI's single 400ms live-selection tick into the
        embedded window (Phase 5.1 — its own selection timer is stopped when
        it shares the main connection, so this is now the only path)."""
        self.window.set_live_selection(refs)

    def push_live_snapshot(self, snapshot) -> None:
        """Route the main GUI's ~2s poll snapshot into the embedded window,
        so its schematic-vs-board diff (Pending changes/Apply) stays
        current (see gui.fieldstool_window.MainWindow.set_live_snapshot)."""
        self.window.set_live_snapshot(snapshot)

    def set_connection_status(self, error: Optional[str]) -> None:
        """Mirror the shared connection's state into the embedded window's
        status label (Phase 5.1 — its own connect/refresh poll is stopped)."""
        self.window.set_connection_status(error)

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed, same pattern as
        rules_dock/placer_dock/etc.'s own set_root_path — reads root_sheet
        straight out of the raw YAML (same read RootMetadataDock's own
        _populate() uses) rather than going through the full config.loader
        pipeline, since that would run whole-project validation just to read
        one scalar. Resolved relative to the root file itself, same
        convention as every other path field there."""
        root_sheet = (yaml_io.load_data(path) or {}).get("root_sheet") if path else None
        resolved = Path(resolve_config_relative_path(path.parent, root_sheet)) if root_sheet else None
        self.window.set_project_root_sheet(resolved)
