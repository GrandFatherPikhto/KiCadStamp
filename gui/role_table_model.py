# gui/role_table_model.py
"""The PURE brain of the cell editor's "Refs" tab — the rows of the role table,
the cluster group fill and the batch that is written to the board.

Stage 2 of the spoke work (plan_2026_09_17_spoke_s2_role_table.md, design
design_2026_09_17_spoke_cell_editing.md §3 Р2/Р4/Р5, UI delta
plan/delta_2026_09_17_spoke_s2_refs_tab_ui.md). Why the tab exists: with a SPOKE
cell Denis routes a pair first and invents the roles afterwards, and until the
components carry a Role "Fill from selection" on the Source tab has nothing to
identify. This table is the tool that puts the roles (and the cluster) on the
board in ONE batch, so one Ctrl+Z takes the whole lot back.

No Qt, no adapter, no board read: the caller (gui/docks/cell_refs_tab.py) reads
the selection on a WORKER thread and feeds the resulting records in; every
decision — which rows to build, what differs from the board, what the batch is,
what to warn about — is a plain function here, testable without a QApplication.

The three rules the whole tab rests on:

  * a row holds TWO pairs of values — what the USER put in the table
    (`role`/`cluster`, the "to write" cells) and what the LAST BOARD READ says
    (`board_role`/`board_cluster`, plus the two `*_field_exists` facts). Only the
    DIFFERENCES leave for the board, so re-writing an identical table is a no-op
    and the user's own text is never clobbered by a later snapshot tick;
  * an EMPTIED Role means "leave the board alone", never "erase it" — erasing
    roles is the Role/Cluster panel's "Clear all", not this table;
  * the cluster is PER ROW ("Cluster to write", Денис 2026-09-17), with
    apply_cluster_to_all as the group fill for the everyday case "some of these
    have no cluster, the others disagree".

A row that cannot be written is reported, never silently dropped: a footprint
without a Role field is "cannot be tagged" (design §2.3 — H1-H4 on the live
board), a footprint without a Cluster field still takes its Role (the skip is
PER FIELD, like RoleClusterTreeDock._run_tag), and a ref that left the board
snapshot is skipped whole.
"""
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.i18n import _

# Why a row did not reach the batch. KEYS, not sentences: the worker returns
# them, the UI turns them into the Log line (skip_label).
SKIP_NO_ROLE_FIELD = "no_role_field"
SKIP_NO_CLUSTER_FIELD = "no_cluster_field"
SKIP_NOT_ON_BOARD = "not_on_board"


def skip_label(reason: str) -> str:
    """The human wording of a skip reason (Log only, never a fatal)."""
    if reason == SKIP_NO_ROLE_FIELD:
        return _("no Role field")
    if reason == SKIP_NO_CLUSTER_FIELD:
        return _("no Cluster field")
    if reason == SKIP_NOT_ON_BOARD:
        return _("not on the board")
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
    "the field is there", the same default explore.Selected uses."""
    ref: str
    role: Optional[str] = None
    cluster: Optional[str] = None
    sheet: tuple = ()
    role_field_exists: bool = True
    cluster_field_exists: bool = True


def record_from_item(item: Any) -> BoardRecord:
    """Any object with the ref/role/cluster/sheet shape -> a BoardRecord."""
    return BoardRecord(
        ref=str(getattr(item, "ref", "") or ""),
        role=getattr(item, "role", None),
        cluster=getattr(item, "cluster", None),
        sheet=tuple(getattr(item, "sheet", None) or ()),
        role_field_exists=bool(getattr(item, "role_field_exists", True)),
        cluster_field_exists=bool(getattr(item, "cluster_field_exists", True)),
    )


def records_from_items(items: Iterable) -> list:
    return [record_from_item(i) for i in (items or ())]


def records_by_ref(records: Iterable) -> dict:
    return {r.ref: r for r in (records or ()) if r.ref}


# ── One row of the table ───────────────────────────────────────────────────

@dataclass
class RoleRow:
    """One component of the table: the refdes, the values the USER typed into
    the "to write" cells (`role`, `cluster`) and what the last board read says
    (`board_role`, `board_cluster`, the field facts).

    `on_board` is False for a ref the snapshot does not carry (a cell opened on
    another board): the row is still shown — the table is a hint — but nothing
    can be written for it."""
    ref: str
    role: str = ""
    cluster: str = ""
    board_role: Optional[str] = None
    board_cluster: Optional[str] = None
    sheet: tuple = ()
    role_field_exists: bool = True
    cluster_field_exists: bool = True
    on_board: bool = True

    @property
    def role_differs(self) -> bool:
        return (self.role or "").strip() != (self.board_role or "")

    @property
    def cluster_differs(self) -> bool:
        return (self.cluster or "").strip() != (self.board_cluster or "")


def row_from_record(record: BoardRecord) -> RoleRow:
    """A fresh row pre-filled with the board's own values — the Role and the
    cluster the footprint already carries land straight in the "to write"
    cells, so a already-tagged selection opens with nothing left to write."""
    return RoleRow(
        ref=record.ref,
        role=record.role or "",
        cluster=record.cluster or "",
        board_role=record.role,
        board_cluster=record.cluster,
        sheet=tuple(record.sheet or ()),
        role_field_exists=record.role_field_exists,
        cluster_field_exists=record.cluster_field_exists,
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
    """What "Write to board" will do: `updates` is the flat list of
    (ref, field, value) triples of ONE set_field_values_bulk call, and
    `skipped` names every row that could not be written and why."""
    updates: list
    skipped: list


def build_tag_updates(rows: Iterable) -> TagPlan:
    """The batch of differences — the ONE place that decides what reaches the
    board.

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


def can_write(rows: Iterable) -> bool:
    """"Write to board" is active only when the batch is not empty (Р4)."""
    return bool(build_tag_updates(rows).updates)


# ── Warnings (Р4: a line in the status strip and the Log, never a blocker) ──

def _effective_role(row: RoleRow) -> str:
    """What this row's role WILL be after the write: the typed one, else what
    the board already has."""
    return (row.role or "").strip() or (row.board_role or "")


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

    written = [(r.ref, (r.role or "").strip()) for r in rows
               if r.on_board and r.role_field_exists and (r.role or "").strip()
               and (r.role or "").strip() != (r.board_role or "")]
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
