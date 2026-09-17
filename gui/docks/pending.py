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
from typing import Dict, List, Optional, Tuple

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _

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
            if (s.role or "") != (inst.role or "") and s.role_field_exists:
                edits.append(PendingEdit(inst.ref, "Role", inst.role or "", s.role or ""))
            if (s.cluster or "") != (inst.cluster or "") and s.cluster_field_exists:
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
        # 2026-08-27 (handoff pending_exclude_missing_board_fields): only emit a
        # diff when the board footprint HAS the field at all — a physically
        # absent field (role_field_exists/cluster_field_exists False) is not a
        # pending change, it's "not comparable yet" (same "only refs present on
        # BOTH sides are comparable" principle this function's docstring states,
        # extended from ref-level to field-level). The field's VALUE may still
        # be None/empty (exists but cleared) — that is a real diff, kept.
        if (s.role or "") != (c.role or "") and s.role_field_exists:
            edits.append(PendingEdit(s.ref, "Role", c.role or "", s.role or ""))
        if (s.cluster or "") != (c.cluster or "") and s.cluster_field_exists:
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


# ── Reminder: Role/Cluster values living on the board only ─────────────────
#
# 2026-09-17 (plan_2026_09_17_spoke_s3_roles_reminder; design
# design_2026_09_17_spoke_cell_editing Р5/Р6). Role/Cluster written onto the
# BOARD (Tag selected, the Refs table, Clear all) take effect immediately but
# live ONLY on the board: the next F8 (Update PCB from Schematic) with field
# updates silently overwrites them with the schematic's values, and only
# Pending changes -> Apply carries them into the schematic. F8 cannot be
# intercepted (it is KiCad's), so the reminder must stay VISIBLE while the
# condition holds — a count in the "Pending changes" tab title — plus ONE Log
# line per transition, never one per ~2s poll tick (which would drown the Log;
# and no growing widget anywhere: the user's screen is small).
#
# Both numbers come from the list set_edits() already receives (the two
# already-cached sides compute_pending_edits compared), so nothing here reads
# the board: door П3.1 forbids it, and the text below says as much.

# The two fields Apply actually transfers. A "Refdes/symbol mismatch" row is
# deliberately NOT one of them — Apply drops it (see edits_to_fields_cfg).
_REMINDER_FIELDS = (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)


def pending_reminder_state(edits: List[PendingEdit]) -> Tuple[int, int]:
    """(count, mismatches) — the two independent reminders, from one list.

    count — the Role/Cluster edits Apply would transfer, i.e. exactly what
    edits_to_fields_cfg() turns into its config, so the number the tab shows
    and the number Apply acts on can never disagree. mismatches — the
    "Refdes/symbol mismatch" rows, kept apart on purpose (Р1/Р4): they never
    travel through Apply, so folding them into `count` would promise a
    transfer that cannot happen."""
    count = 0
    mismatches = 0
    for e in edits:
        if e.mismatched:
            mismatches += 1
        elif e.field in _REMINDER_FIELDS:
            count += 1
    return count, mismatches


def reminder_log_line(prev_count: int, count: int) -> Optional[str]:
    """The ONE Log line for the pending count, or None when this update must
    stay silent (Р3/Р8). Growth only: 0->3 warns, 3->5 warns again (a NEW
    value landed on the board after the previous reminder), 3->3 and 5->3 are
    silent — otherwise every ~2s poll tick would repeat the same sentence
    forever. The caller owns the previous state (PendingChangesDock keeps it);
    a decrease means Apply or a revert on the board removed something, which
    is news the user already wanted."""
    if count <= prev_count or count <= 0:
        return None
    return _("{count} Role/Cluster value(s) are on the board but not in the schematic — Pending changes → Apply before the next Update PCB from Schematic (F8)").format(count=count)


def mismatch_log_line(prev_mismatches: int, mismatches: int) -> Optional[str]:
    """The ONE Log line about "Refdes/symbol mismatch" rows, or None (Р4/Р8).
    Appearance only: 0->2 warns once, a repeated tick and a GROWTH (2->3) stay
    silent — this is not "more work to do", it is the same bad news already on
    screen, and these rows never travel through Apply anyway. The line comes
    back only after the condition cleared and reappeared."""
    if prev_mismatches > 0 or mismatches <= 0:
        return None
    return _("{count} refdes mean a DIFFERENT symbol on the board than in the schematic (Refdes/symbol mismatch) — Apply skips them, so their Role/Cluster will not reach the schematic; check the annotation of these parts").format(count=mismatches)


try:
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout,
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)
except ImportError:  # pragma: no cover — the functions above are usable without PyQt6
    QWidget = object
    pyqtSignal = object


class PendingChangesDock(QWidget):
    """Read-only table of the current schematic-vs-board diff (see
    compute_pending_edits above) + an Apply button MainWindow wires (see
    gui/fieldstool_window.py) — Apply itself needs the root_sheet path and
    the KiCad-running guard, which this dock deliberately doesn't know
    about. Fed wholesale by set_edits() every time either side of the diff
    changes (Rescan, or the main GUI's ~2s poll) — never mutated in place,
    same "just show me the latest" discipline as the rest of this GUI.

    2026-09-05 (plan components_fieldstool_master_detail): no longer a
    QDockWidget docked at the main window's bottom — the shared single
    instance is a plain QWidget page hosted in the "Components" master-detail
    dock's left QTabWidget, and injected into the embedded fieldstool window
    (which only references it for set_edits()/the Apply callbacks, never
    parents it — a QWidget can only have one parent). A single click on a
    diff row now emits ref_activated so DockHub can show that component in
    the fieldstool pane."""

    # 2026-09-05 (plan components_fieldstool_master_detail): a single click
    # on a diff-table row — DockHub loads that ref into the embedded
    # fieldstool pane and reveals it in the Components tree.
    ref_activated = pyqtSignal(str)

    # 2026-09-17 (plan_2026_09_17_spoke_s3_roles_reminder Р6): the pending
    # count from pending_reminder_state(), for whoever hosts this panel as a
    # tab — RoleClusterTreeDock renders it into the "Pending changes" title.
    # A signal rather than a direct call: the panel knows nothing about its
    # host, and this is the channel already used right above (ref_activated).
    # Emitted on every set_edits(); the host only rewrites a title.
    pending_count_changed = pyqtSignal(int)

    def __init__(self, main_window):
        super().__init__(main_window)
        # Stable identity kept for diagnostics; no longer a QDockWidget, so
        # saveState()/restoreState() never sees it (2026-09-05 master-detail).
        self.setObjectName("pending_dock")
        self._main_window = main_window
        self.on_apply_clicked = None  # Callable[[], None], set by MainWindow
        self.on_ensure_fields_clicked = None  # Callable[[], None], set by MainWindow
        self.on_sync_clicked = None  # Callable[[], None], set by MainWindow (2026-08-27)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 4)
        # The two value columns are named by SIDE, never by "current"/"new"
        # (2026-09-16, plan_2026_09_16_commit_document_and_pending_direction
        # Э2/Т2.1, Р3): the dock compares the schematic on disk with the live
        # board and CANNOT know which side is newer — "current"/"new" claimed a
        # direction that does not exist, and on a board that lags behind the
        # schematic (the normal state while Role/Cluster are being edited in
        # eeschema) that claim read as "the board is already right".
        self.table.setHorizontalHeaderLabels(
            [_("Ref"), _("Field"), _("Schematic"), _("Board")])
        # The same honest statement where the user looks for it — one sentence,
        # no extra widget (Р5: nothing growing on a small screen).
        self.table.horizontalHeader().setToolTip(
            _("The table shows only WHAT differs between the schematic and the "
              "board — not which side is newer."))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Row click -> component selection (2026-09-05, plan
        # components_fieldstool_master_detail): a single click on any cell of
        # a diff row loads that ref into the fieldstool pane on the right and
        # reveals it in the Components tree (see _on_cell_clicked).
        self.table.cellClicked.connect(self._on_cell_clicked)
        # 2026-08-27 (handoff pending_dock_min_height, kept): without an
        # explicit minimum this QTableWidget's default minimumSizeHint floored
        # how small the panel could shrink. setMinimumHeight(1), NOT 0 — Qt
        # treats an explicit minimum of exactly 0 as "unset" and nothing
        # changes (verified live, see pending_dock_min_height). The table
        # scrolls its own content — no QScrollArea wrap.
        self.table.setMinimumHeight(1)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton(_("Apply..."))
        # Direction, stated on the button itself (Т2.1): Apply writes the BOARD
        # value INTO the schematic, so the schematic's own value is replaced —
        # the one thing a user editing the schematic must know before clicking.
        self.apply_button.setToolTip(
            _("Writes the BOARD values into the SCHEMATIC (the schematic's "
              "values are replaced)."))
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
        self.sync_button.setToolTip(
            _("Writes the SCHEMATIC values onto the live BOARD — the same as "
              "Update PCB from Schematic does for these fields."))
        self.sync_button.clicked.connect(
            lambda: self.on_sync_clicked and self.on_sync_clicked())
        button_row.addWidget(self.sync_button)
        layout.addLayout(button_row)

        # (count, mismatches) of the LAST list set_edits() saw — the Log
        # reminder is emitted on TRANSITIONS only, so the previous state has
        # to live here (Р3/Р4: a line on every update would flood the Log).
        self._reminder_state = (0, 0)

        self.set_edits([])

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        """Single click on a diff row -> ref_activated(ref) (its Ref column).
        A row without a ref means nothing to select."""
        item = self.table.item(row, 0)
        if item is not None and item.text():
            self.ref_activated.emit(item.text())

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
        self._update_reminder(edits)

    def _update_reminder(self, edits: List[PendingEdit]) -> None:
        """The reminder half of set_edits() (2026-09-17, plan
        plan_2026_09_17_spoke_s3_roles_reminder Р1–Р4/Р6): count what Apply can
        carry — and what it cannot — from the SAME list the table was just
        built from, write ONE Log line per transition, and tell the host tab
        the new count. Reads neither the board nor the schematic: the two
        sources are the ones compute_pending_edits already compared (П3.1)."""
        count, mismatches = pending_reminder_state(edits)
        prev_count, prev_mismatches = self._reminder_state
        self._reminder_state = (count, mismatches)
        for line in (reminder_log_line(prev_count, count),
                     mismatch_log_line(prev_mismatches, mismatches)):
            if line:
                logger.warning(line)
        self.pending_count_changed.emit(count)
