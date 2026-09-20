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
from kicadstamp.exceptions import ValidationError
from kicadstamp.field_overrides import sheet_path_uuids_of, symbol_uuid_of
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
    # The THIRD side (plan_2026_09_18_field_overrides_store Т4): OUR stored
    # value for this (symbol uuid, field), or None when we have no record.
    # None is NOT the same as "": with no record the board's value is what
    # Apply writes (the pre-store rule, unchanged); with a record OUR value is
    # what Apply writes (С16), because that is the value the resolver acts on.
    our_value: Optional[str] = None
    # The CONFLICT marker (Т4), and deliberately NOT `mismatched` above: this one
    # means "the board has moved away from BOTH the schematic and our value"
    # (our A, board B, schematic C) — the same symbol on all sides, so applying
    # is safe and В3 allows it. `mismatched` would have made Apply DROP the row
    # (edits_to_fields_cfg), which would silently lose the edit — hence a field
    # of its own, shown in the table and carried by Apply.
    board_moved: bool = False
    # The KEY our record is stored under (Т6): `our_value` says WHAT we recorded,
    # this says for WHICH symbol — and "forget this row" must be addressed by it,
    # never by the refdes (see kicadstamp/field_overrides.py's module docstring).
    # None on a hand-built edit and on rows whose identity chain did not resolve.
    symbol_uuid: Optional[str] = None


def _board_symbol_uuid(s) -> str | None:
    """The board footprint's symbol uuid — delegated to the ONE rule
    (kicadstamp.field_overrides.symbol_uuid_of).

    This used to be a second hand-rolled reader of `fp.sheet_path.path`, broken
    exactly like the store's own copy (plan_2026_09_20_symbol_uuid_wrong_
    attribute.md, Т2): a kipy attribute on a domain object, swallowed by
    `except Exception`, answering None forever — which is why `mismatched` below
    never fired since 2026-08-08. The shape of a board footprint is not this
    module's business."""
    return symbol_uuid_of(getattr(s, "fp", None))


def _board_full_path(s) -> tuple | None:
    """The footprint's FULL sheet-path uuid chain as a tuple — the shape
    load_schematic_instances() keys its index with. None when there is nothing to
    join with (no footprint, an empty chain) — the path_index then simply can't
    match this footprint.

    Through the shared chain reader for the same reason as _board_symbol_uuid
    above: it used to read the kipy attribute by hand and was broken the same
    way, so the per-instance join (the 2026-08-08 recon) never matched."""
    chain = sheet_path_uuids_of(getattr(s, "fp", None))
    return tuple(chain) if chain else None


def _store_value(store, symbol_uuid, field) -> Optional[str]:
    """Our stored value for this (symbol uuid, field), or None when there is no
    store, no uuid to key it by, or no record. None means "we have nothing to
    say", never an empty value — an empty stored value is a real instruction
    ("this component has no Role") and comes back as ""."""
    if store is None or not symbol_uuid:
        return None
    return store.get(symbol_uuid, field)


def _board_value(selected, field: str):
    """The PHYSICAL value for the Board column (Т5г/С27).

    Since Т5г `Selected.role`/`.cluster` are the values IN FORCE — our stored
    value when the store has one, the board's otherwise. The diff must NOT compare
    the store with itself: the entire reason the Board column exists is to show
    what physically lies on the board, so that a board edit made after the note
    was taken stays visible (Т4).

    `Selected` carries that as `board_role`/`board_cluster`, filled from the SAME
    read (explore.Board._both → FieldOverrideAdapter.get_field_values), so the two
    sides of this row can never describe different instants.

    Falls back to the effective value for a hand-built object without the pair
    (tests, other callers) — the single-truth world this diff lived in before Т5г.
    """
    physical = getattr(selected, f"board_{field}", None)
    return getattr(selected, field) if physical is None else physical


def _emit_side(edits: List[PendingEdit], ref: str, field: str, sch_value,
               board_value, board_has_field: bool, symbol_uuid, store) -> None:
    """ONE side's row, if it earns one. The rule, with the three sides s (schematic),
    b (board) and o (ours, None when we have no record):

      * no record — the two-sided rule of the pre-store code, byte for byte: a row
        iff the board HAS the field and its value differs from the schematic. The
        `board_has_field` gate is the 2026-08-27 fix (a physically absent field is
        "not comparable yet", not a pending change);
      * record — ALSO a row when the schematic still disagrees with OUR value: Apply
        writes ours (С16), so the schematic is about to change even when the board
        and the schematic agree with each other and only we disagree;
      * the board moved (all three pairwise different) — flagged, never hidden and
        never dropped: the user decides, В3 allows applying (С4/С17)."""
    sch = sch_value or ""
    board = board_value or ""
    ours = _store_value(store, symbol_uuid, field)
    if ours is None:
        if board_has_field and sch != board:
            edits.append(PendingEdit(ref, field, sch, board))
        return
    if sch == ours and not (board_has_field and sch != board):
        return
    moved = sch != ours and board != ours and board != sch
    edits.append(PendingEdit(ref, field, sch, board, False, ours, moved,
                             symbol_uuid))


def compute_pending_edits(components, snapshot, path_index=None,
                          store=None) -> List[PendingEdit]:
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
    either side lacks uuid info the check is skipped (no false positives).

    store (Optional[kicadstamp.field_overrides.FieldOverrides]): OUR values, the
    third side (plan_2026_09_18_field_overrides_store Т4). It is keyed by SYMBOL
    UUID — the same identity the per-instance join uses — so a re-annotated board
    (F8) cannot attach our value to a different component. store=None is the
    pre-store diff, byte for byte: every call site that doesn't pass one keeps
    today's behaviour (Т7), and so does an EMPTY store, because a side with no
    record adds nothing to the comparison. Note what this function does NOT do:
    it never decides who wins — the resolver does that (Т2); this is the diff
    view, and it shows all three sides as they are."""
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
            # The symbol uuid IS the last hop of the full path the index is keyed
            # by (see schema_model._full_key) — the same key our store uses.
            symbol_uuid = p[-1] if p else None
            _emit_side(edits, inst.ref, ROLE_FIELD_NAME, inst.role,
                       _board_value(s, "role"),
                       s.role_field_exists, symbol_uuid, store)
            _emit_side(edits, inst.ref, CLUSTER_FIELD_NAME, inst.cluster,
                       _board_value(s, "cluster"),
                       s.cluster_field_exists, symbol_uuid, store)
    for i, s in enumerate(snapshot):
        if i in handled:
            continue
        c = by_ref.get(s.ref)
        if c is None:
            continue
        board_uuid = _board_symbol_uuid(s)
        if board_uuid and c.symbol_uuids and board_uuid not in c.symbol_uuids:
            # A mismatched ref never consults the store: this row is about which
            # SYMBOL the refdes means, not about values, and its Ours cell stays
            # empty on purpose (there is no "ours" for an unknown identity).
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
        # be None/empty (exists but cleared) — that is a real diff, kept. OUR
        # value needs no such gate: it lives in a file, not on the board.
        _emit_side(edits, s.ref, ROLE_FIELD_NAME, c.role, _board_value(s, "role"),
                   s.role_field_exists, board_uuid, store)
        _emit_side(edits, s.ref, CLUSTER_FIELD_NAME, c.cluster,
                   _board_value(s, "cluster"),
                   s.cluster_field_exists, board_uuid, store)
    return sorted(edits, key=lambda e: (e.ref, e.field))


def edits_to_fields_cfg(edits: List[PendingEdit]) -> Dict[str, Dict[str, str]]:
    """refdes -> {field: value} — the shape
    kicadstamp.schematic_set_fields.plan_set_edits_for_root() consumes.
    Identity-mismatch edits (PendingEdit.mismatched) are dropped — applying
    them would write the board value into the WRONG schematic symbol.

    With a record of ours, OUR value is what goes into the schematic (С16), not
    the board's: that is the value the resolver acts on ("note R_FB, press
    apply" must write R_FB), and it is what makes a record retirable at all
    (В4/Т6 — a record leaves when the schematic matches it, which can never
    happen while Apply writes the board's older value). A row where the board
    moved (PendingEdit.board_moved) is NOT dropped: the symbol is the same, so
    В3 allows applying."""
    cfg: Dict[str, Dict[str, str]] = {}
    for e in edits:
        if e.mismatched:
            continue
        cfg.setdefault(e.ref, {})[e.field] = (
            e.new_value if e.our_value is None else e.our_value)
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

# Row tints (Т4, 2026-09-18). Two DIFFERENT statements, so two colours — never
# one for both: a mismatch row is "Apply will NOT carry this" (a refusal, red),
# a moved-board row is "Apply WILL carry this, and the board has moved away"
# (a heads-up, amber). Reusing the red for our conflict would tell the user the
# opposite of what happens.
_ROW_COLOURS = {
    "mismatch": "#ffdddd",
    "board_moved": "#ffe9b8",
}


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
    is news the user already wanted.

    2026-09-18 (Т4): the sentence no longer says the values "are on the board
    but not in the schematic". Since the store (Т1/Т2) the count also covers rows
    where the schematic and the board AGREE and only our stored value differs —
    those values are in neither place, and the old wording would have described
    them wrongly. What is true in every case is that the SCHEMATIC is about to
    change when Apply runs (that is what edits_to_fields_cfg writes, С16)."""
    if count <= prev_count or count <= 0:
        return None
    return _("{count} Role/Cluster value(s) would change in the schematic — Pending changes → Apply before the next Update PCB from Schematic (F8)").format(count=count)


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
    from PyQt6.QtCore import Qt, pyqtSignal
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QMenu,
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)
except ImportError:  # pragma: no cover — the functions above are usable without PyQt6
    QWidget = object
    pyqtSignal = object


class PendingChangesDock(QWidget):
    """Read-only table of the current THREE-sided diff — schematic / OUR stored
    value / board (see compute_pending_edits above) + an Apply button MainWindow
    wires (see gui/fieldstool_window.py) — Apply itself needs the root_sheet path
    and the KiCad-running guard, which this dock deliberately doesn't know
    about. Fed wholesale by set_edits() every time either side of the diff
    changes (Rescan, or the main GUI's ~2s poll) — never mutated in place,
    same "just show me the latest" discipline as the rest of this GUI.

    The store reaches compute_pending_edits through the window that owns it (see
    gui/fieldstool_window.py's set_overrides_store): this dock is a view, it
    never loads a file and never touches the board.

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
        # The store in force, pushed in by the fieldstool window (Т6): this dock
        # only ever FORGETS from it (the row menu), which is why it holds the
        # object rather than a path — whoever owns it re-reads the file first.
        self._overrides = None
        # Fired after a successful forget (Т6): the store file changed, so every
        # other holder of it must re-read — the window wires this to its own
        # reload + recompute, exactly like the Refs table's hook (Т5).
        self.on_overrides_written = None
        # The rows as last set — the context menu addresses a row by index.
        self._edits: List[PendingEdit] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 5)
        # The value columns are named by SIDE, never by "current"/"new"
        # (2026-09-16, plan_2026_09_16_commit_document_and_pending_direction
        # Э2/Т2.1, Р3): the dock compares the schematic on disk with the live
        # board and CANNOT know which side is newer — "current"/"new" claimed a
        # direction that does not exist, and on a board that lags behind the
        # schematic (the normal state while Role/Cluster are being edited in
        # eeschema) that claim read as "the board is already right".
        # Since Т4 (2026-09-18) there is a THIRD side: OUR stored value. The
        # column is ALWAYS present, never hidden when the store is empty — a
        # conditional column would need a guard of its own ("hidden exactly when
        # the store has no records, not when there is no conflict") and it raises
        # the question "where did the column go?". One empty column on a clean
        # profile is cheaper than that.
        self.table.setHorizontalHeaderLabels(
            [_("Ref"), _("Field"), _("Schematic"), _("Ours"), _("Board")])
        # The same honest statement where the user looks for it — one sentence,
        # no extra widget (Р5: nothing growing on a small screen).
        self.table.horizontalHeader().setToolTip(
            _("The table shows only WHAT differs between the schematic, OUR "
              "stored value and the board — not which side is newer."))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Row click -> component selection (2026-09-05, plan
        # components_fieldstool_master_detail): a single click on any cell of
        # a diff row loads that ref into the fieldstool pane on the right and
        # reveals it in the Components tree (see _on_cell_clicked).
        self.table.cellClicked.connect(self._on_cell_clicked)
        # Т6: a right-click on a row offers "forget" for OUR record of it.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
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
        # Since Т4 the exception our store introduced is stated right there too:
        # where WE have a value, OURS is what gets written (С16).
        self.apply_button.setToolTip(
            _("Writes the BOARD values into the SCHEMATIC (the schematic's "
              "values are replaced) — except where WE have a stored value: that "
              "one wins and is written instead."))
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

    # ── Т6: forgetting OUR record of a row ────────────────────────────────

    def set_overrides(self, store) -> None:
        """Install the override store in force (or None when no project is open).
        Held, not re-read — the writing side re-reads the file before it saves."""
        self._overrides = store

    def row_actions(self, edit: PendingEdit) -> list:
        """The context menu of one row: ``[(label, enabled, callback)]``.

        Kept as data rather than built inside the menu so the rule can be guarded
        without opening a popup: the ONE action is offered for every row and
        DISABLED where there is nothing to forget (no store, no record, or no
        symbol uuid to key it by) — an entry that disappears instead reads as a
        bug, the same reasoning the always-present "Ours" column follows."""
        can_forget = (self._overrides is not None
                      and edit.our_value is not None
                      and bool(edit.symbol_uuid))
        return [(_("Forget this record (Т6)"), can_forget,
                 lambda: self.forget_row(edit))]

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._edits):
            return
        actions = self.row_actions(self._edits[row])
        if not actions:
            return
        menu = QMenu(self)
        for label, enabled, callback in actions:
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def forget_row(self, edit: PendingEdit) -> None:
        """Drop OUR record of this row (Т6) — the "передумал" the user asked for.

        The AUTOMATIC half of Т6 is С5 (a record the schematic already carries is
        dropped where the schematic is read: the fieldstool window's Rescan/Apply
        and `overrides-apply --to schematic`). This one is for the case nothing
        about the schematic has an opinion on. The store is re-read from its FILE
        first: the other holder in this process may have recorded since we were
        handed our copy, and a save built on a stale picture would drop that."""
        store = self._overrides
        if store is None or not edit.symbol_uuid:
            return
        path = getattr(store, "path", None)
        if path is not None:
            from kicadstamp.field_overrides import load_field_overrides
            store = load_field_overrides(str(path))
            self._overrides = store
        dropped = store.forget(edit.symbol_uuid, edit.field)
        if not dropped:
            logger.info(_("nothing to forget for %(ref)s %(field)s — the record "
                          "is already gone"), {"ref": edit.ref, "field": edit.field})
            return
        try:
            store.save()
        except (OSError, ValidationError) as e:
            logger.error(_("Could not save the override store: {error}").format(
                error=e))
            return
        logger.info(_("forgot our stored %(field)s for %(ref)s — the board and "
                      "the schematic are untouched"), {"ref": edit.ref,
                                                       "field": edit.field})
        if self.on_overrides_written:
            self.on_overrides_written()

    def set_edits(self, edits: List[PendingEdit]) -> None:
        self._edits = list(edits or ())
        self.table.setRowCount(len(edits))
        for row, e in enumerate(edits):
            row_values = [e.ref, e.field, e.old_value,
                          "" if e.our_value is None else e.our_value,
                          e.new_value]
            items = [QTableWidgetItem(text) for text in row_values]
            if e.mismatched:
                # The same refdes means different symbols on the two sides —
                # visually distinct and never auto-applied (edits_to_fields_cfg
                # drops these). The row shows the two symbol UUIDs so the user
                # can see WHY the ref is not applied. Its Ours cell stays empty:
                # a mismatched row is about identity, not about values, so there
                # is no "ours" to show for it.
                for item in items:
                    item.setBackground(QColor(_ROW_COLOURS["mismatch"]))
            elif e.board_moved:
                # Т4's conflict marker: the board moved away from BOTH the
                # schematic and our value. A colour of its own, NOT the mismatch
                # one — this row still travels through Apply (С17), so it must
                # not read as "Apply will not carry this". The tooltip says who
                # wins, because that is exactly what the user cannot tell from
                # three disagreeing columns.
                for item in items:
                    item.setBackground(QColor(_ROW_COLOURS["board_moved"]))
                items[0].setToolTip(_(
                    "The board has moved away from both the schematic and OUR "
                    "stored value — Apply still writes OUR value."))
            for col, item in enumerate(items):
                self.table.setItem(row, col, item)
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
