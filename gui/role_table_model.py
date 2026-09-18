# gui/role_table_model.py
"""The PURE brain of the cell editor's "Refs" tab — the rows of the role table,
the cluster group fill and the batch that is RECORDED in the override store
(plus the board batch kept for Т5а's explicit write).

Stage 2 of the spoke work (plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI delta
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md). Why the tab exists: with a SPOKE
cell Denis routes a pair first and invents the roles afterwards, and until the
components carry a Role "Fill from selection" on the Source tab has nothing to
identify. This table is the tool that puts the roles (and the cluster) into the
project's OVERRIDE STORE in ONE batch. It used to put them onto the BOARD, in
one batch so a single Ctrl+Z took the whole lot back; that write moved to the
store in Т5 (2026-09-18, plan_2026_09_18_field_overrides_store) because our
stored value OUTRANKS the board (Т2) — a board write would be invisible, since
the store would keep overriding exactly what was written — and because the store
needs no KiCad at all. Writing the values ONTO the board stays available as
Т5а's explicit, separate action, and `build_tag_updates` below is its planner.

No Qt, no adapter, no board read: the caller (gui/docks/cell_refs_tab.py) reads
the selection on a WORKER thread and feeds the resulting records in; every
decision — which rows to build, what differs from the value IN FORCE, what the
batch is, what to warn about — is a plain function here, testable without a
QApplication.

The three rules the whole tab rests on:

  * a row holds THREE things — what the USER put in the table (`role`/`cluster`,
    the "to write" cells), what the STORE says (`store_role`/`store_cluster`, the
    "Ours" side of Pending) and what the LAST BOARD READ says
    (`board_role`/`board_cluster`, plus the two `*_field_exists` facts). The value
    IN FORCE is `base_role`/`base_cluster` — ours when a record exists, the
    board's otherwise (Т2/Т5) — and only the DIFFERENCES from THAT go out, so
    re-recording an identical table is a no-op and the user's own text is never
    clobbered by a later snapshot tick;
  * an EMPTIED Role means "leave it alone" (both the record and the board), never
    "erase it" — erasing what is ON THE BOARD is the Role/Cluster panel's "Clear
    all", and erasing OUR stored entry is Т6's "forget", not this table;
  * the cluster is PER ROW ("Cluster to write", Денис 2026-09-17), with
    apply_cluster_to_all as the group fill for the everyday case "some of these
    have no cluster, the others disagree".

A row that cannot be written is reported, never silently dropped: a footprint
without a Role field is "cannot be tagged" (design §2.3 — H1-H4 on the live
board), a footprint without a Cluster field still takes its Role (the skip is
PER FIELD, like RoleClusterTreeDock._run_tag), a ref that left the board
snapshot is skipped whole, and — new in Т5 — a row with no SYMBOL UUID has no
key to record under and is refused BY NAME (С12) instead of being guessed at.
(The two `*_field_exists` facts are about the BOARD batch: a footprint with no
such field physically cannot be written there, while the store does not care.)
"""
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _

# Why a row did not reach the batch. KEYS, not sentences: the writer returns
# them, the UI turns them into the Log line (skip_label).
SKIP_NO_ROLE_FIELD = "no_role_field"
SKIP_NO_CLUSTER_FIELD = "no_cluster_field"
SKIP_NOT_ON_BOARD = "not_on_board"
# 2026-09-18 (plan_2026_09_18_field_overrides_store Т5/С12): the STORE is keyed
# by symbol uuid, so a row without one cannot be recorded at all — that is a
# LOUD refusal, never a record under an invented key.
SKIP_NO_SYMBOL_UUID = "no_symbol_uuid"


def skip_label(reason: str) -> str:
    """The human wording of a skip reason (Log only, never a fatal)."""
    if reason == SKIP_NO_ROLE_FIELD:
        return _("no Role field")
    if reason == SKIP_NO_CLUSTER_FIELD:
        return _("no Cluster field")
    if reason == SKIP_NOT_ON_BOARD:
        return _("not on the board")
    if reason == SKIP_NO_SYMBOL_UUID:
        return _("no symbol uuid — the refdes has no footprint in the last board read")
    return str(reason)


def skipped_text(skipped: Iterable) -> str:
    """"H1 (no Role field), C90 (not on the board)" — the Log tail of a write."""
    return ", ".join("{ref} ({reason})".format(ref=ref, reason=skip_label(reason))
                     for ref, reason in skipped)


# ── What the board says about one footprint ────────────────────────────────

@dataclass(frozen=True)
class BoardRecord:
    """One footprint of the last board read, in the shape the table needs.

    A plain record ON PURPOSE — two very different readers fill it: the worker
    (a live footprint plus adapter.has_field / get_field_value reads) and the
    page's own snapshot (explore.Selected, which already carries role, cluster
    and both *_field_exists flags). A hand-built object without the flags means
    "the field is there", the same default explore.Selected uses.

    `symbol_uuid` (Т5): the footprint's key in the override store
    (fp.sheet_path.path[-1], see kicadstamp.field_overrides.symbol_uuid_of) —
    None when the reader could not get it, and then the row cannot be RECORDED
    (the store has no valid key), which is a loud refusal (С12)."""
    ref: str
    role: Optional[str] = None
    cluster: Optional[str] = None
    sheet: tuple = ()
    role_field_exists: bool = True
    cluster_field_exists: bool = True
    symbol_uuid: Optional[str] = None


def record_from_item(item: Any) -> BoardRecord:
    """Any object with the ref/role/cluster/sheet shape -> a BoardRecord."""
    from kicadstamp.field_overrides import symbol_uuid_of

    fp = getattr(item, "fp", None)
    symbol_uuid = getattr(item, "symbol_uuid", None)
    if symbol_uuid is None and fp is not None:
        symbol_uuid = symbol_uuid_of(fp)
    return BoardRecord(
        ref=str(getattr(item, "ref", "") or ""),
        role=getattr(item, "role", None),
        cluster=getattr(item, "cluster", None),
        sheet=tuple(getattr(item, "sheet", None) or ()),
        role_field_exists=bool(getattr(item, "role_field_exists", True)),
        cluster_field_exists=bool(getattr(item, "cluster_field_exists", True)),
        symbol_uuid=symbol_uuid,
    )


def records_from_items(items: Iterable) -> list:
    return [record_from_item(i) for i in (items or ())]


def records_by_ref(records: Iterable) -> dict:
    return {r.ref: r for r in (records or ()) if r.ref}


# ── One row of the table ───────────────────────────────────────────────────

@dataclass
class RoleRow:
    """One component of the table: the refdes, the values the USER typed into
    the "to write" cells (`role`, `cluster`), the row's KEY in the override store
    (`symbol_uuid`) and what the last board read says (`board_role`,
    `board_cluster`, the field facts), plus OUR stored values when we have any
    (`store_role`, `store_cluster` — None means "no record").

    `board_role`/`board_cluster` are what the BOARD holds; the value IN FORCE is
    `base_role`/`base_cluster` (ours wins — Т2), and that is what the "differs"
    flags and the batch compare against, so the table can never promise a write
    the resolver would then ignore.

    `on_board` is False for a ref the snapshot does not carry (a cell opened on
    another board): the row is still shown — the table is a hint — but nothing
    can be recorded for it (no key)."""
    ref: str
    role: str = ""
    cluster: str = ""
    board_role: Optional[str] = None
    board_cluster: Optional[str] = None
    sheet: tuple = ()
    role_field_exists: bool = True
    cluster_field_exists: bool = True
    on_board: bool = True
    symbol_uuid: Optional[str] = None
    store_role: Optional[str] = None
    store_cluster: Optional[str] = None

    @property
    def base_role(self) -> Optional[str]:
        """The Role IN FORCE: our stored value when we have a record (it wins
        over the board, plan §0/Т2), else the board's."""
        return self.store_role if self.store_role is not None else self.board_role

    @property
    def base_cluster(self) -> Optional[str]:
        return (self.store_cluster if self.store_cluster is not None
                else self.board_cluster)

    @property
    def role_differs(self) -> bool:
        return (self.role or "").strip() != (self.base_role or "")

    @property
    def cluster_differs(self) -> bool:
        return (self.cluster or "").strip() != (self.base_cluster or "")


def row_from_record(record: BoardRecord) -> RoleRow:
    """A fresh row pre-filled with the value IN FORCE for that component (the
    store's, else the board's — see apply_overrides for the store half): a
    already-tagged selection opens with nothing left to record."""
    return RoleRow(
        ref=record.ref,
        role=record.role or "",
        cluster=record.cluster or "",
        board_role=record.role,
        board_cluster=record.cluster,
        sheet=tuple(record.sheet or ()),
        role_field_exists=record.role_field_exists,
        cluster_field_exists=record.cluster_field_exists,
        symbol_uuid=record.symbol_uuid,
    )


def _with_board(row: RoleRow, record: BoardRecord) -> RoleRow:
    """The row with its BOARD columns re-read (the user's own cells untouched)."""
    return replace(
        row,
        board_role=record.role,
        board_cluster=record.cluster,
        sheet=tuple(record.sheet or ()),
        role_field_exists=record.role_field_exists,
        cluster_field_exists=record.cluster_field_exists,
        on_board=True,
        symbol_uuid=record.symbol_uuid or row.symbol_uuid,
    )


# ── Building and editing the table ─────────────────────────────────────────

def replace_rows(records: Iterable) -> list:
    """"Take selection": the table becomes exactly these rows, in the order the
    board read handed them over."""
    return [row_from_record(r) for r in (records or ())]


def append_rows(rows: list, records: Iterable) -> list:
    """"Add selection": new refs are appended, a ref already present keeps its
    row — including everything the user typed into it."""
    out = list(rows or [])
    known = {r.ref for r in out}
    for record in (records or ()):
        if record.ref in known:
            continue
        out.append(row_from_record(record))
        known.add(record.ref)
    return out


def remove_rows(rows: list, refs: Iterable) -> list:
    """Drop the named rows. A table-only edit: the board and the remembered refs
    of the instance are not touched (that is what the plan's guard С2д pins)."""
    doomed = set(refs or ())
    return [r for r in (rows or []) if r.ref not in doomed]


def add_ref(rows: list, ref: str, records: Iterable) -> tuple:
    """A refdes typed into the empty placeholder row: (rows, error | None).

    Resolved against the page's SNAPSHOT, never the board — the page must not
    read the live board from the UI thread. An unknown ref and one already in
    the table are both refused by NAME."""
    ref = (ref or "").strip()
    if not ref:
        return list(rows or []), _("type a refdes first")
    if any(r.ref == ref for r in (rows or ())):
        return list(rows or []), _("{ref} is already in the table").format(ref=ref)
    record = records_by_ref(records).get(ref)
    if record is None:
        return list(rows or []), _(
            "{ref} is not in the last board read — press Refresh, or check the "
            "refdes").format(ref=ref)
    return list(rows or []) + [row_from_record(record)], None


def refresh_board_values(rows: list, records: Iterable) -> list:
    """Re-read the BOARD columns after a later snapshot tick or a write.

    The user's own cells are untouched; a row whose ref is no longer in the
    snapshot becomes unwritable rather than disappearing (the table is a hint,
    and silently dropping the user's row would lose their input)."""
    by_ref = records_by_ref(records)
    out = []
    for row in (rows or []):
        record = by_ref.get(row.ref)
        if record is None:
            out.append(replace(row, board_role=None, board_cluster=None,
                               on_board=False))
            continue
        out.append(_with_board(row, record))
    return out


def apply_overrides(rows: list, overrides) -> list:
    """Fill each row's OUR-STORE columns from the store in force (Т5).

    `overrides` is a kicadstamp.field_overrides.FieldOverrides, or None when no
    project is open — the same "no store, no third side" rule the Pending diff
    follows. A row without a symbol uuid keeps store_role/store_cluster = None
    (no key to look up), and a store without a record for the key also leaves
    them None: None is what makes base_role fall back to the board (see
    RoleRow.base_role), so an EMPTY store changes nothing about the table —
    Т7's golden property, on this side too."""
    out = []
    for row in (rows or []):
        if overrides is None or not row.symbol_uuid:
            out.append(replace(row, store_role=None, store_cluster=None))
            continue
        out.append(replace(
            row,
            store_role=overrides.get(row.symbol_uuid, ROLE_FIELD_NAME),
            store_cluster=overrides.get(row.symbol_uuid, CLUSTER_FIELD_NAME),
        ))
    return out


def apply_cluster_to_all(rows: list, cluster: str) -> tuple:
    """The group fill: put `cluster` into the "Cluster to write" cell of every
    row that CAN take it — (rows, unfilled_refs).

    Denis's case (2026-09-17): "some of them have no cluster, the others
    disagree" — one click sets them all. A footprint without a Cluster field is
    left alone and its refdes returned, so the caller can say which ones; an
    empty value is a no-op (the field doubles as "don't touch the cluster")."""
    value = (cluster or "").strip()
    out = list(rows or [])
    if not value:
        return out, []
    unfilled = []
    filled = []
    for row in out:
        if not row.cluster_field_exists:
            unfilled.append(row.ref)
            filled.append(row)
            continue
        filled.append(replace(row, cluster=value))
    return filled, unfilled


def is_table_empty(rows: Iterable) -> bool:
    """True when there is nothing but the placeholder — the state in which
    "Fill from selection" on the Source tab is ALLOWED to fill the table."""
    return not any((r.ref or "").strip() for r in (rows or ()))


def default_cluster(rows: Iterable, source_cluster: str) -> str:
    """What the shared "Cluster to write" field opens with: the one cluster
    every row agrees on, else whatever the Source tab's working Cluster says
    (a mixed or empty table falls back to the user's own working context)."""
    values = {(r.cluster or "").strip() for r in (rows or [])
              if (r.cluster or "").strip()}
    if len(values) == 1:
        return next(iter(values))
    return str(source_cluster or "")


def role_choices(cell_roles: Iterable) -> list:
    """The Role cell's suggestions: THIS cell's own roles, in the cell's own
    order (never the board's roles), duplicates dropped."""
    out = []
    for role in (cell_roles or ()):
        if role and role not in out:
            out.append(role)
    return out


# ── gui_state.json round-trip (Р2б) ────────────────────────────────────────

def table_to_state(rows: Iterable, cluster: str) -> dict:
    """What goes into gui_state.json: ONLY what the user typed — the row order,
    the refs, the shared cluster field and the values that DIFFER from the board.

    A cell that still holds the board's own value is stored EMPTY on purpose
    (Р2б: "значения с платы не хранятся — они устаревают, их даёт снимок"): it
    comes back from the snapshot on the next open, so writing it down would only
    freeze a value that may have changed on the board since. An emptied cell is
    stored as empty too, and reading it back pre-fills the board's value again —
    which is exactly the "leave it alone" the emptied cell meant."""
    rows_state = []
    for row in (rows or ()):
        if not (row.ref or "").strip():
            continue
        rows_state.append({
            "ref": row.ref,
            "role": (row.role or "") if row.role_differs else "",
            "cluster": (row.cluster or "") if row.cluster_differs else "",
        })
    return {"cluster": str(cluster or ""), "rows": rows_state}


def rows_from_state(state: Any, records: Iterable) -> tuple:
    """(rows, cluster) restored from the saved table, with every BOARD column —
    and every cell the state left empty — taken from the snapshot passed in: no
    adapter, no board read (guard С2л).

    A saved ref the snapshot does not carry is shown as unwritable rather than
    dropped, and a missing/malformed state reads as an empty table (state is a
    hint everywhere in this project, never a fatal)."""
    if not isinstance(state, dict):
        return [], ""
    saved = state.get("rows")
    if not isinstance(saved, list):
        return [], ""
    by_ref = records_by_ref(records)
    out = []
    for entry in saved:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref") or "").strip()
        if not ref:
            continue
        record = by_ref.get(ref)
        role = str(entry.get("role") or "")
        cluster = str(entry.get("cluster") or "")
        if not role and record is not None:
            role = record.role or ""
        if not cluster and record is not None:
            cluster = record.cluster or ""
        row = RoleRow(ref=ref, role=role, cluster=cluster)
        out.append(_with_board(row, record) if record is not None
                   else replace(row, on_board=False))
    return out, str(state.get("cluster") or "")


def rows_from_refs(role_to_ref: dict, records: Iterable) -> list:
    """The rows a REMEMBERED identification yields (stage 1's role -> refdes
    map): one row per identified component, its Role cell carrying the role the
    identification pinned and the Cluster cell the board's own value (the same
    pre-fill a row built from a board read gets)."""
    by_ref = records_by_ref(records)
    out = []
    for role, ref in (role_to_ref or {}).items():
        ref = str(ref or "").strip()
        if not ref:
            continue
        record = by_ref.get(ref)
        row = RoleRow(ref=ref, role=str(role or ""),
                      cluster=(record.cluster or "") if record else "")
        out.append(_with_board(row, record) if record is not None
                   else replace(row, on_board=False))
    return out


# ── The write batch (Р5) ───────────────────────────────────────────────────

@dataclass
class TagPlan:
    """What a write will do, in two shapes that must never be confused:

      * build_override_updates -> `updates` is a flat list of
        (SYMBOL UUID, field, value) triples for the OVERRIDE STORE — the table's
        own path since Т5;
      * build_tag_updates -> `updates` is (REF, field, value) triples of ONE
        set_field_values_bulk call — the explicit board write, which leaves the
        main path and comes back as the named button/key of Т5а.

    `skipped` names every row that could not be written and why, in both."""
    updates: list
    skipped: list


def build_tag_updates(rows: Iterable) -> TagPlan:
    """The batch of differences for the BOARD — kept for the explicit board
    write of Т5а (the tab itself no longer calls it: it records into the store,
    see build_override_updates). This is the ONE place that decides what reaches
    the board.

    The FOOTPRINT is found by the worker (by ref, on the live board); this
    function only says WHICH field of WHICH refdes and to what value:

      * a ref the snapshot does not carry -> skipped whole ("not on the board");
      * a footprint with NO Role field -> always listed ("cannot be tagged"),
        even when the Role cell is empty — that is the fact about the board the
        user needs to see (design §2.3);
      * a footprint with NO Cluster field -> listed only when a cluster was
        actually asked for; its Role still goes in (PER-FIELD skip);
      * everything else: only what DIFFERS from the board. An emptied Role or an
        emptied Cluster means "leave it alone"."""
    updates: list = []
    skipped: list = []
    for row in (rows or ()):
        role = (row.role or "").strip()
        cluster = (row.cluster or "").strip()
        if not row.on_board:
            skipped.append((row.ref, SKIP_NOT_ON_BOARD))
            continue
        if not row.role_field_exists:
            skipped.append((row.ref, SKIP_NO_ROLE_FIELD))
        elif role and role != (row.board_role or ""):
            updates.append((row.ref, ROLE_FIELD_NAME, role))
        if cluster and cluster != (row.board_cluster or ""):
            if row.cluster_field_exists:
                updates.append((row.ref, CLUSTER_FIELD_NAME, cluster))
            else:
                skipped.append((row.ref, SKIP_NO_CLUSTER_FIELD))
    return TagPlan(updates=updates, skipped=skipped)


def build_override_updates(rows: Iterable) -> TagPlan:
    """The batch the table RECORDS — into the override store (Т5), not onto the
    board. The tab's own path since 2026-09-18.

    SPARSE by construction (С10/С11): a row earns a record only where the value
    the user left DIFFERS from the value IN FORCE (our store's, else the
    board's) — open the table, look at it, close it, and the store gains
    nothing. An empty cell keeps the meaning it always had here ("leave this
    field alone"), so clearing a STORED value is not done from this table (that
    is Pending's "forget" and the `overrides-forget` key, Т6).

    The board's own field facts do NOT gate a record: the store needs no Role or
    Cluster field on the footprint — that requirement belongs to the board write
    (Т5а). What does gate it is the KEY: a row with no symbol uuid is refused by
    name (С12), because a record under an invented key would attach our value to
    the wrong component after the next F8."""
    updates: list = []
    skipped: list = []
    for row in (rows or ()):
        role = (row.role or "").strip()
        cluster = (row.cluster or "").strip()
        if not row.on_board:
            skipped.append((row.ref, SKIP_NOT_ON_BOARD))
            continue
        if not row.symbol_uuid:
            skipped.append((row.ref, SKIP_NO_SYMBOL_UUID))
            continue
        if role and role != (row.base_role or ""):
            updates.append((row.symbol_uuid, ROLE_FIELD_NAME, role))
        if cluster and cluster != (row.base_cluster or ""):
            updates.append((row.symbol_uuid, CLUSTER_FIELD_NAME, cluster))
    return TagPlan(updates=updates, skipped=skipped)


def can_write(rows: Iterable) -> bool:
    """The write button is active only when the batch is not empty (Р4) — and
    since Т5 the batch is the STORE's, so "nothing to write" means "nothing
    differs from what is already in force"."""
    return bool(build_override_updates(rows).updates)


# ── Warnings (Р4: a line in the status strip and the Log, never a blocker) ──

def _effective_role(row: RoleRow) -> str:
    """What this row's role WILL be after the write: the typed one, else the
    value already in force (our store's, else the board's)."""
    return (row.role or "").strip() or (row.base_role or "")


def role_table_warnings(rows: Iterable, cell_roles: Iterable,
                        cell_name: str = "") -> list:
    """Every Р4 check, as ready-to-show sentences. NONE of them blocks the
    write (only an empty batch does) — they tell the user what the table will
    mean, not that it is forbidden:

      * one role on two rows: perfectly fine for tagging several spoke pairs at
        once, but such a selection will NOT identify as one instance;
      * a role that is not a role of this cell at all;
      * a cell role that stays unassigned after the write;
      * clusters that disagree across the rows (what "Apply to all rows" is for).
    """
    rows = list(rows or ())
    cell_roles = [r for r in (cell_roles or ()) if r]
    name = cell_name or "?"
    warnings: list = []

    # "written" = the rows whose Role this write will actually RECORD (Т5): the
    # typed value differing from what is in force. The board's FIELD FACTS are
    # deliberately not part of it any more — the store needs no Role field on the
    # footprint (that gate belongs to the board write, Т5а). A ref the snapshot
    # does not carry stays out (nothing can be recorded for it, and С12 says so by
    # name in the status line), but a missing symbol uuid is NOT a reason to hide
    # the warning: it is a technicality the user cannot influence, and a silently
    # vanishing "role X on three rows" would be worse than the refusal itself.
    written = [(r.ref, (r.role or "").strip()) for r in rows
               if r.on_board and (r.role or "").strip()
               and (r.role or "").strip() != (r.base_role or "")]
    by_role: dict = {}
    for ref, role in written:
        by_role.setdefault(role, []).append(ref)
    for role, refs in sorted(by_role.items()):
        if len(refs) > 1:
            warnings.append(_(
                "role {role} on {refs} — this selection will not identify as ONE "
                "instance of cell {cell!r} (fine for tagging several spoke pairs "
                "at once)").format(role=role, refs=", ".join(refs), cell=name))

    foreign = sorted({role for _ref, role in written if role not in cell_roles})
    for role in foreign:
        warnings.append(_("role {role} is not a role of cell {cell!r}").format(
            role=role, cell=name))

    assigned = {_effective_role(r) for r in rows} - {""}
    missing = [role for role in cell_roles if role not in assigned]
    if rows and missing:
        warnings.append(_("cell role {roles} is not assigned").format(
            roles=", ".join(missing)))

    clusters = {(r.cluster or "").strip() for r in rows
                if (r.cluster or "").strip()}
    if len(clusters) > 1:
        warnings.append(_(
            "the cluster differs across rows ({clusters}) — press “Apply to all "
            "rows” to tag one cluster").format(
                clusters=", ".join(sorted(clusters))))
    return warnings
