# gui/docks/fieldstool_dock.py
"""
FieldsToolDock — owns the ONE live gui.fieldstool_window.MainWindow and
exposes it to DockHub/MainWindow (and the test suite) as a thin facade.
Since 2026-09-05 (plan components_fieldstool_master_detail) it is NO longer a
QDockWidget: the fieldstool window is embedded directly as the RIGHT pane of
the "Components" master-detail dock (RoleClusterTreeDock's QSplitter), so the
separate right-hand fieldstool dock is gone. This facade exists only to keep
the same object surface the rest of the app and tests reach for
(main_window.fieldstool_dock.window, .components_changed,
pick_leaf/pick_group, push_live_*/set_connection_status/set_root_path)
pointing at that window — a QWidget can only have one parent, so the window's
parent is the Components dock, not this facade.

The embedded window shares the main GUI's OWN BoardConnection (one kipy
client, one REQ socket) — it never creates or polls a connection of its own
(kipy's REQ socket allows exactly one request in flight, so a second
independent timer on the same connection would interleave requests
mid-flight). One connection, one polling loop: the main GUI's single
2s/400ms poll feeds the embedded window through push_live_selection()/
set_connection_status()/push_live_snapshot().

fieldstool's own Components tree (fieldstool/gui/tree.py) was retired
2026-08-01 — the main GUI's own Components tree (gui/docks/
role_cluster_tree.py) covers the same job in its "Not yet applied" mode when
embedded here (reading this window's parsed schematic components directly).
The refresh callback that keeps that view in sync with an explicit
Rescan/Apply in this tab is wired through this facade's components_changed
signal (connected in gui/dock_hub.py, the composition root) — this facade
never reaches back into main_window.tree_dock itself.

2026-08-03: PendingChangesDock is no longer fieldstool's own internal dock —
DockHub builds ONE shared instance (2026-09-05: hosted as the Components
master-detail's left "Pending" tab, see gui/docks/pending.py) and injects it
here, so RoleClusterTreeDock's live-board writes and this window's own Stage
share the same diff view (see gui/docks/pending.py and gui/fieldstool_window.
py's module docstrings for the redesign).
"""
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from kicadstamp.utils.paths import resolve_config_relative_path

from .. import yaml_io
from ..fieldstool_window import MainWindow as FieldsToolMainWindow
from .pending import PendingChangesDock


class FieldsToolDock(QObject):
    # Fired when fieldstool's window rescans the schematic (its
    # on_components_changed hook) — the main GUI's Components tree listens
    # to refresh its "Not yet applied" view (see gui/dock_hub.py).
    components_changed = pyqtSignal()

    def __init__(self, main_window, connection, pending_dock: Optional[PendingChangesDock] = None):
        super().__init__(main_window)
        # Stable identity for diagnostics; not a QDockWidget anymore, so
        # saveState()/restoreState() never sees it (2026-09-05 master-detail).
        self.setObjectName("fieldstool_dock")
        # The main GUI's OWN BoardConnection (one kipy client, one REQ
        # socket): one connection + one polling loop instead of two
        # independent ones (see module docstring). The window is a QMainWindow
        # with no parent here — RoleClusterTreeDock reparents it into the
        # "Components" dock's splitter (one parent rule, see module docstring).
        self.window = FieldsToolMainWindow(connection=connection, pending_dock=pending_dock)
        self.window.on_components_changed = self.components_changed.emit

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
        """Route a leaf-node click from the main GUI's Components tree (and a
        Pending-table row click) into fieldstool's existing
        _on_tree_leaf_picked() logic."""
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
