# gui/docks/pending.py
"""
Pending changes — 2026-08-03 redesign. Used to be a JSON-backed staging
queue (PendingRegistry) you built up by hand while KiCad was open, applied
later. Retired: it could drift out of sync with the live board on its own
(found live — Clear all/Delete selected wrote Role/Cluster straight to the
board over IPC but never staged anything, so Apply had nothing to do and
stayed disabled even though the board had genuinely changed).

New model: whatever is currently on the live board (via IPC — Clear all,
Delete selected, fieldstool's own Stage button, PlacerDock's Cluster
tagging, ANY of them) already IS the accumulated pending state — there is
nothing left to separately track. compute_pending_edits() below just diffs
that live state against the schematic's last-known Role/Cluster and returns
whatever differs; Apply writes exactly that diff into the schematic via
kicadstamp.schematic_set_fields.plan_set_edits_for_root() (unchanged). This
can never drift, because it is never stored — recomputed fresh from two
already-cached sources every time (gui.schema_model.SchematicComponent list,
refreshed by an explicit Rescan; BoardConnection.snapshot, refreshed by the
main GUI's ~2s poll), so it costs no new IPC/file reads.

Deliberately no persistence and no undo/remove-from-pending action: if the
live board's Role/Cluster genuinely differs from the schematic, that is
simply a fact about the board right now, not a staged decision to revoke —
to not apply a change, revert the field's value on the board itself (Ctrl+Z
in KiCad works immediately after an edit).
"""
import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class PendingEdit:
    ref: str
    field: str
    old_value: str  # currently in the schematic
    new_value: str  # currently on the live board
    # True when the SAME refdes means DIFFERENT symbols on the two sides
    # (board fp.sheet_path.path[-1] != schematic symbol_uuids). Such an edit
    # is shown in the Pending changes table but NEVER written by Apply —
    # edits_to_fields_cfg() drops it. Refdes-string matching alone cannot
    # see this; it would silently write the board value into the WRONG
    # schematic symbol (see recon in
    # techdocs/handoff/deepseek/handoff_2026_08_08_symbol_uuid_recon.md).
    mismatched: bool = False


def _board_symbol_uuid(s) -> str | None:
    """The board footprint's symbol uuid = fp.sheet_path.path[-1] — the same
    uuid the schematic's (symbol ...) block carries as its top-level
    (uuid ...). None when unavailable (fp absent in tests, empty path, IPC
    error) — the caller then skips the identity check instead of guessing."""
    try:
        fp = s.fp
        if fp is None:
            return None
        path = fp.sheet_path.path
        if not path:
            return None
        last = path[-1]
        return str(last.value) if hasattr(last, "value") else str(last)
    except Exception:
        return None


def _board_full_path(s) -> tuple | None:
    """The footprint's full sheet_path.path as a tuple of uuid strings — the
    same shape load_schematic_instances() keys its index with. None when
    unavailable (fp absent in tests, empty path, IPC error) — the path_index
    then simply can't match this footprint."""
    try:
        fp = s.fp
        if fp is None:
            return None
        path = fp.sheet_path.path
        if not path:
            return None
        return tuple(str(u.value) if hasattr(u, "value") else str(u) for u in path)
    except Exception:
        return None


def compute_pending_edits(components, snapshot, path_index=None) -> List[PendingEdit]:
    """components: List[gui.schema_model.SchematicComponent] (from
    load_schematic_components(), i.e. the schematic's last Rescan).
    snapshot: List[kicadstamp.explore.Selected] (BoardConnection.snapshot,
    i.e. the board's last poll tick). Only refs present in BOTH are
    comparable — a ref only on the board (not yet in this schematic tree at
    all) or only in the schematic (not currently on the board) has nothing
    to diff. A component whose OWN blocks disagree on Role/Cluster
    (SchematicComponent.divergent — a pre-existing schema inconsistency,
    not caused by this diff) is compared against its first block's value
    like everywhere else that reads .role/.cluster; if that differs from
    the board, Apply's own plan_set_edits_for_root() will simply unify all
    of that ref's blocks to the board's value, which is a reasonable
    resolution, not a bug.

    path_index (Optional[Dict[full_path_tuple, SchematicInstance]], from
    gui.schema_model.load_schematic_instances): when given, a board footprint
    whose FULL sheet_path.path matches an index key diffs against THAT
    schematic instance — its refdes/role/cluster — even when the two sides
    disagree on the refdes (re-annotation desync). This is the per-instance
    resolution from the 2026-08-08 recon. Footprints the index doesn't know
    fall back to the refdes join below. When path_index is None/empty the
    behavior is exactly the refdes join (callers that don't pass it are
    unchanged).

    Identity check (2026-08-08): in the refdes-join fallback, before comparing
    values verify the refdes means the SAME symbol on both sides. If the board
    footprint's symbol uuid (fp.sheet_path.path[-1]) is available AND the
    schematic component carries known symbol_uuids, but the board's uuid is not
    among them, the two sides disagree about what this refdes IS (re-annotation
    / revision desync). Emit a single mismatched PendingEdit for the ref instead
    of a Role/Cluster diff — visible in the table, excluded from Apply. When
    either side lacks uuid info the check is skipped (no false positives)."""
    by_ref = {c.ref: c for c in components}
    edits: List[PendingEdit] = []
    handled: set[int] = set()
    if path_index:
        for i, s in enumerate(snapshot):
            p = _board_full_path(s)
            inst = path_index.get(p) if p is not None else None
            if inst is None:
                continue
            handled.add(i)
            if (s.role or "") != (inst.role or ""):
                edits.append(PendingEdit(inst.ref, "Role", inst.role or "", s.role or ""))
            if (s.cluster or "") != (inst.cluster or ""):
                edits.append(PendingEdit(inst.ref, "Cluster", inst.cluster or "", s.cluster or ""))
    for i, s in enumerate(snapshot):
        if i in handled:
            continue
        c = by_ref.get(s.ref)
        if c is None:
            continue
        board_uuid = _board_symbol_uuid(s)
        if board_uuid and c.symbol_uuids and board_uuid not in c.symbol_uuids:
            edits.append(PendingEdit(
                s.ref,
                "Refdes/symbol mismatch",
                "schematic: " + ",".join(c.symbol_uuids),
                "board: " + board_uuid,
                mismatched=True,
            ))
            continue
        if (s.role or "") != (c.role or ""):
            edits.append(PendingEdit(s.ref, "Role", c.role or "", s.role or ""))
        if (s.cluster or "") != (c.cluster or ""):
            edits.append(PendingEdit(s.ref, "Cluster", c.cluster or "", s.cluster or ""))
    return sorted(edits, key=lambda e: (e.ref, e.field))


def edits_to_fields_cfg(edits: List[PendingEdit]) -> Dict[str, Dict[str, str]]:
    """refdes -> {field: value} — the shape
    kicadstamp.schematic_set_fields.plan_set_edits_for_root() consumes.
    Identity-mismatch edits (PendingEdit.mismatched) are dropped — applying
    them would write the board value into the WRONG schematic symbol."""
    cfg: Dict[str, Dict[str, str]] = {}
    for e in edits:
        if e.mismatched:
            continue
        cfg.setdefault(e.ref, {})[e.field] = e.new_value
    return cfg


try:
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (QAbstractItemView, QDockWidget, QHBoxLayout,
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)

    from kicadstamp.i18n import _
except ImportError:  # pragma: no cover — the functions above are usable without PyQt6
    QDockWidget = object


class PendingChangesDock(QDockWidget):
    """Read-only table of the current schematic-vs-board diff (see
    compute_pending_edits above) + an Apply button MainWindow wires (see
    gui/fieldstool_window.py) — Apply itself needs the root_sheet path and
    the KiCad-running guard, which this dock deliberately doesn't know
    about. Fed wholesale by set_edits() every time either side of the diff
    changes (Rescan, or the main GUI's ~2s poll) — never mutated in place,
    same "just show me the latest" discipline as the rest of this GUI."""

    def __init__(self, main_window):
        super().__init__(_("Pending changes"), main_window)
        self._main_window = main_window
        self.on_apply_clicked = None  # Callable[[], None], set by MainWindow
        self.on_ensure_fields_clicked = None  # Callable[[], None], set by MainWindow
        self.on_sync_clicked = None  # Callable[[], None], set by MainWindow (2026-08-27)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [_("Ref"), _("Field"), _("Schematic (current)"), _("Board (new)")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 2026-08-27 (handoff pending_dock_min_height): the dock is tabified
        # with LogDock (gui/dock_hub.py) — without an explicit minimum this
        # QTableWidget's default minimumSizeHint floored how small the dock
        # could shrink (Denis: "панель log/pending changes нельзя
        # переразмерить"). setMinimumHeight(1), NOT 0 — same Qt gotcha as
        # LogDock.text (log_dock_min_height_fix2): an explicit minimum of
        # exactly 0 is Qt's "unset" sentinel and changes nothing; verified
        # live, the container's effective layout minimum stays 110px at
        # baseline AND with setMinimumHeight(0), drops to 41px with
        # setMinimumHeight(1) — the smallest value Qt actually honors as an
        # override. QTableWidget already scrolls its own content (own
        # viewport/scrollbar) — no QScrollArea wrap, same principle as the
        # other docks.
        self.table.setMinimumHeight(1)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton(_("Apply..."))
        self.apply_button.clicked.connect(lambda: self.on_apply_clicked and self.on_apply_clicked())
        button_row.addWidget(self.apply_button)
        # Always enabled (unlike Apply, which needs a live diff) — this
        # scans the whole schematic tree for a structural gap (a component
        # missing Role/Cluster entirely, see schematic_set_fields.
        # plan_ensure_fields_for_root's docstring for how FB3 got found
        # live 2026-08-04), which has nothing to do with the board/
        # schematic diff table above.
        self.ensure_fields_button = QPushButton(_("Ensure fields..."))
        self.ensure_fields_button.clicked.connect(
            lambda: self.on_ensure_fields_clicked and self.on_ensure_fields_clicked())
        button_row.addWidget(self.ensure_fields_button)
        # "Sync from schematic" (2026-08-27, handoff pending_sync_from_
        # schematic): writes each pending edit's SCHEMATIC value back onto
        # the LIVE board — the automated equivalent of the module docstring's
        # recommended "revert the field on the board (Ctrl+Z in KiCad)"
        # workaround. Enabled only when at least one NON-mismatched edit
        # exists (mismatched edits have nothing safe to sync — see set_edits).
        self.sync_button = QPushButton(_("Sync from schematic..."))
        self.sync_button.clicked.connect(
            lambda: self.on_sync_clicked and self.on_sync_clicked())
        button_row.addWidget(self.sync_button)
        layout.addLayout(button_row)

        self.setWidget(container)
        self.set_edits([])

    def set_edits(self, edits: List[PendingEdit]) -> None:
        self.table.setRowCount(len(edits))
        for row, e in enumerate(edits):
            if e.mismatched:
                # The same refdes means different symbols on the two sides —
                # visually distinct and never auto-applied (edits_to_fields_cfg
                # drops these). The row shows the two symbol UUIDs so the user
                # can see WHY the ref is not applied.
                row_values = [e.ref, e.field, e.old_value, e.new_value]
                for col in range(4):
                    item = QTableWidgetItem(row_values[col])
                    item.setBackground(QColor("#ffdddd"))
                    self.table.setItem(row, col, item)
            else:
                self.table.setItem(row, 0, QTableWidgetItem(e.ref))
                self.table.setItem(row, 1, QTableWidgetItem(e.field))
                self.table.setItem(row, 2, QTableWidgetItem(e.old_value))
                self.table.setItem(row, 3, QTableWidgetItem(e.new_value))
        # Apply: any pending edit at all (including mismatched, whose row is
        # shown but which Apply drops — Apply's own enablement is unchanged).
        # Sync from schematic: only NON-mismatched edits have something safe
        # to sync (a mismatched row's refdes means a DIFFERENT symbol on the
        # two sides — writing the schematic value there would hit the wrong
        # component). Distinct conditions — do not reuse one for the other.
        syncable = [e for e in edits if not e.mismatched]
        self.apply_button.setEnabled(bool(edits))
        self.sync_button.setEnabled(bool(syncable))
