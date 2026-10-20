# kicadstamp/refdes_duplicates.py
"""Duplicate refdes on the live board — ONE detector for every door.

Why this module exists (plan_2026_10_12_duplicate_refdes_guard, 2026-10-12)
-------------------------------------------------------------------------
Denis remade the MCU power section in the schematic, re-annotated it and updated
the board WITHOUT "Delete footprints with no symbols". The old footprints stayed
behind as ORPHANS (``sheet_path_uuids == ()``), and four of them (C31, C32, C35,
C36) kept refdes that the NEW, schematic-linked footprints also carry. Every
consumer that addresses a footprint by its BARE refdes then picks a random copy:
Redraw moved the orphan instead of the real one (``move_executor.py:27``,
``flip_manager.py:20/29`` — ``{fp.ref: fp for fp in all_fps}``). That is not a
KiCad bug, and it is not fixed by rewriting those 32 ref-keyed dictionaries: a
duplicate refdes has no single right answer, so the ONE rule implemented here is
"refuse the record, name both copies, say which one is the orphan".

The rule (the plan's "Правило")
------------------------------
An ACTION on a footprint addressed by refdes is NOT performed when that refdes
stands on the live board more than once. ONE guard in ONE door of the pipeline
(``ApplyPipeline``), taking the run's OWN resolved refdes (``Item.produces`` and
``Item.anchor_ref``) — never the whole board, so a duplicate no record of this
run touches can never refuse anything.

An ORPHAN WITH NO DUPLICATE is NOT a refusal: one footprint is unambiguous,
there is nothing to pick wrongly, so the pipeline keeps moving it. The only
place such an orphan is surfaced is the "Add entities" window (see
``duplicate_short_reason`` / ``orphan_short_reason``).

Duck-typed ON PURPOSE
---------------------
The pipeline hands this module domain ``Footprint`` objects (the adapter's own
reads); the "Add entities" window hands it ``gui.docks.instance_candidates.Part``
records. Both carry ``ref``, ``uuid`` and ``sheet_path_uuids`` — the chain is
read through the project's ONE reader (``field_overrides.sheet_path_uuids_of``),
never by reaching for the attribute here. A footprint ALSO carries ``position``;
when it does, the Log line names it in millimetres.

Qt-free and read-only: the pipeline's door guard and the GUI candidate rule call
the SAME functions, so they can never disagree about what a duplicate is.
"""
from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

from .field_overrides import sheet_path_uuids_of
from .i18n import _
from .utils.units import MM

__all__ = [
    "DuplicateCopy", "duplicate_copy_of", "duplicate_refdes_groups",
    "duplicate_log_lines", "duplicate_refusal_lines", "duplicate_short_reason",
    "orphan_short_reason", "duplicate_summary_line", "refdes_duplicate_of",
]


@dataclasses.dataclass(frozen=True)
class DuplicateCopy:
    """ONE copy of a refdes that stands on the board more than once.

    ``uuid`` is the copy's own identity (``None`` when the record carries none),
    ``x_mm``/``y_mm`` its position in millimetres (``None`` when the record shape
    has no position — the GUI's ``Part`` records do not), and ``on_schematic``
    False for an ORPHAN (an empty ``sheet_path_uuids`` chain).
    """
    ref: str
    uuid: Optional[str]
    x_mm: Optional[float]
    y_mm: Optional[float]
    on_schematic: bool

    @property
    def position_text(self) -> str:
        """The copy's position as the Log names it — or an explicit "not read",
        never a silent 0.000 (a made-up position would read as a real one)."""
        if self.x_mm is None or self.y_mm is None:
            return _("position not read")
        return _("at {x:.3f}, {y:.3f} mm").format(x=self.x_mm, y=self.y_mm)


def _refdes_of(record) -> Optional[str]:
    """The record's bare refdes as text, or None when it carries none."""
    ref = getattr(record, "ref", None)
    return str(ref) if ref else None


def _uuid_text(record) -> Optional[str]:
    value = getattr(record, "uuid", None)
    return str(value) if value else None


def _on_schematic(record) -> bool:
    """True unless the record PROVES it is an orphan.

    Three shapes, deliberately different:
      * an explicit ``on_schematic`` flag (the GUI's ``Part`` records carry it
        because a hand-built one has no chain to read) — taken as the answer;
      * a ``sheet_path_uuids`` chain (the domain Footprint) — empty means the
        footprint is not linked to the schematic;
      * neither — an UNKNOWN shape, and "unknown" is NEVER called an orphan on a
        guess: a phantom orphan would refuse a healthy record.
    """
    explicit = getattr(record, "on_schematic", None)
    if explicit is not None:
        return bool(explicit)
    # BOTH footprint shapes count as "the chain is known" (the domain Footprint
    # and kipy's raw sheet_path) — only an object with NEITHER is foreign.
    known = (getattr(record, "sheet_path_uuids", None) is not None
             or getattr(record, "sheet_path", None) is not None)
    if known:
        return bool(sheet_path_uuids_of(record))
    return True


def _position_mm(record) -> tuple[Optional[float], Optional[float]]:
    """The record's position in millimetres, or (None, None) when the shape has
    no readable ``position`` (see the module docstring: the GUI's Part has none).
    ``Vector2`` is in nanometres, the same unit every planner reads.

    A coordinate that is not a NUMBER counts as "not read" (a test double whose
    ``position.x`` is itself a mock, or a foreign record shape): the sentence
    that names a component must render, never raise — "position not read" is the
    honest answer there (found 2026-10-13, the refusals of
    role_conflict_reasons rendered the position of MagicMock footprints)."""
    pos = getattr(record, "position", None)
    if pos is None:
        return None, None
    x = getattr(pos, "x", None)
    y = getattr(pos, "y", None)
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None, None
    return x / MM, y / MM


def duplicate_copy_of(record) -> DuplicateCopy:
    """ONE live record as a ``DuplicateCopy`` — the shared reader of the three
    identity fields (refdes, uuid, position) plus the schematic link.

    Split out 2026-10-13 (plan plan_2026_10_13_cluster_confined_roles.md) so the
    refusal of a role that is not unique inside its own cluster
    (``role_conflict_reasons.py``) names its components through the SAME reader
    as the duplicate-refdes guard below — one place that decides what a copy IS,
    never a second copy of the ``position``/``uuid``/``sheet_path`` reading. The
    group loop below is the code it always was, with this call in the middle."""
    x_mm, y_mm = _position_mm(record)
    return DuplicateCopy(
        ref=_refdes_of(record) or "",
        uuid=_uuid_text(record),
        x_mm=x_mm,
        y_mm=y_mm,
        on_schematic=_on_schematic(record),
    )


def duplicate_refdes_groups(records: Iterable, refs: Iterable | None = None
                            ) -> dict[str, tuple[DuplicateCopy, ...]]:
    """{ref: its copies} for every refdes that stands on the board more than
    once — the ONE detector of this class.

    ``records`` — the live board as the caller read it (domain footprints, or
    the GUI's ``Part`` records). ``refs`` — restrict the answer to these refdes
    (the run's OWN resolved roles): a duplicate outside the set is never even
    looked at, which is what keeps an unrelated orphan from refusing a record
    that never touches it. ``None`` means "the whole board" (the GUI window).

    Records without a refdes are skipped: they cannot be addressed by refdes at
    all, so they are not an ambiguity of this kind.
    """
    wanted = None if refs is None else {str(r) for r in refs}
    by_ref: dict[str, list[DuplicateCopy]] = {}
    for record in records or ():
        copy = duplicate_copy_of(record)
        if not copy.ref:
            continue
        if wanted is not None and copy.ref not in wanted:
            continue
        by_ref.setdefault(copy.ref, []).append(copy)
    return {ref: tuple(copies) for ref, copies in by_ref.items() if len(copies) > 1}


def duplicate_log_lines(ref: str, copies: Iterable[DuplicateCopy]) -> list[str]:
    """The Log lines for ONE refused refdes: the headline, every copy, and what
    to do — the plan's "называет обе копии и какая не связана со схемой".

    Both copies are named (uuid + position), an ORPHAN copy says so in words,
    and the closing line names the action. One call per refused refdes."""
    copies = tuple(copies)
    lines = [_("{ref!r} stands on the board {count} times — the record is "
               "refused, nothing of it is moved:")
             .format(ref=ref, count=len(copies))]
    for index, copy in enumerate(copies, 1):
        orphan = _(" — NOT linked to the schematic") if not copy.on_schematic else ""
        lines.append(_("  copy {index}/{count}: uuid {uuid}, {position}{orphan}")
                     .format(index=index, count=len(copies),
                             uuid=copy.uuid or _("(no uuid)"),
                             position=copy.position_text, orphan=orphan))
    lines.append(_("  delete the orphan (Update PCB from Schematic with "
                   "“Delete footprints with no symbols”) or re-annotate; "
                   "nothing of this record was moved."))
    return lines


def duplicate_short_reason(ref: str, copies: Iterable[DuplicateCopy]) -> str:
    """The ONE short sentence a window shows for a DUPLICATED refdes — the
    "Add entities" row's reason, replacing the bare "role R: 0 of 1".

    Two copies, at least one an orphan: "R5 is on the board twice (one is not
    linked to the schematic)". Two schematic-linked copies: the same sentence
    without the orphan clause (a re-annotation left two live copies)."""
    copies = tuple(copies)
    if any(not copy.on_schematic for copy in copies):
        return _("{ref} is on the board twice (one is not linked to the "
                 "schematic)").format(ref=ref)
    return _("{ref} is on the board twice (both copies are linked to the "
             "schematic)").format(ref=ref)


def orphan_short_reason(ref: str) -> str:
    """The ONE short sentence for an ORPHAN WITH NO DUPLICATE — a footprint
    whose ``sheet_path_uuids`` chain is empty and whose refdes is unique.

    Shown ONLY by the "Add entities" window: the pipeline does NOT refuse such a
    record (one footprint, no ambiguity — see the module docstring)."""
    return _("{ref} is not linked to the schematic").format(ref=ref)


def refdes_duplicate_of(refs: Iterable,
                        duplicates: dict[str, tuple[DuplicateCopy, ...]]
                        ) -> Optional[str]:
    """The first refdes of ``refs`` that stands on the board twice, or None.

    Deterministic (sorted) so one record always refuses for the same refdes."""
    if not duplicates:
        return None
    for ref in sorted({str(r) for r in refs or () if r}):
        if ref in duplicates:
            return ref
    return None


def duplicate_refusal_lines(records: Iterable, refs: Iterable) -> list[str]:
    """The refusal of a READ-ONLY door addressed by refdes: the guard's own Log
    lines, or [] when every ref in ``refs`` is unique on the board.

    Ч4 of plan_2026_10_15_extract_and_read_paths_cleanup: "Select cell" /
    "Select cell components" and the cell overlay's identified-pair read pick a
    footprint BY REFDES, exactly like the apply pipeline — and ``get_footprint``
    silently returns the first copy. A duplicated refdes has no single right
    answer there either, so those reads now answer the way the write door does:
    refuse, name both copies, say which one is an orphan, say what to do.

    ONE place, ONE wording: this is a thin composition of the detector's own
    three functions (``duplicate_refdes_groups`` -> ``refdes_duplicate_of`` ->
    ``duplicate_log_lines``), so a reading door can never invent a second way to
    describe a duplicate. ``refs`` narrows the scan exactly like the pipeline's
    door guard does: a duplicate the read never addresses refuses nothing."""
    wanted = [str(ref) for ref in (refs or ()) if ref]
    if not wanted:
        return []
    duplicates = duplicate_refdes_groups(records, wanted)
    ref = refdes_duplicate_of(wanted, duplicates)
    if ref is None:
        return []
    return duplicate_log_lines(ref, duplicates[ref])


def duplicate_summary_line(labels: Iterable[str]) -> str:
    """The run's closing line naming every refused record — empty when nothing
    was refused (the caller then prints nothing, same convention as the other
    run summaries in apply_pipeline)."""
    labels = sorted(str(label) for label in labels or ())
    if not labels:
        return ""
    return _("Refused (duplicate refdes on the board): {labels}").format(
        labels="; ".join(labels))
