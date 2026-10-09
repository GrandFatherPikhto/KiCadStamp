# gui/docks/instance_candidates.py
"""Which INSTANCES fit a cell, and which CELLS fit an instance — ONE rule,
both directions (plan_2026_10_09_cells_and_entities, part 1).

The problem this module solves
------------------------------
A cell is a cluster-agnostic template: the same cell stands on the board many
times, and the user wants to give a whole batch of its standing copies their own
`entities:` records at once ("the FPGA power cell has ~30 decoupling pairs —
adding them one by one is pointless"). To offer that batch, something has to
answer a single question: does this instance of the board FIT this cell?

The answer is deliberately narrow and pure. An INSTANCE is the (Cluster tag,
sheet) pair the project already groups board footprints by (the same key
``reead.group_selected`` uses); this module re-implements neither the grouping
nor the sheet identity — it reads the ONE helper ``reead.sheet_of``. And the fit
itself is role multiplicity ONLY:

  * every role of the cell must stand in the instance exactly as many times as
    the cell has slots of it — no more, no less (Денис, 09.10.2026: "меньше →
    `role R: 1 of 2`; больше → `role R ×3 for 2 slots`");
  * a role the instance carries that the cell does NOT have at all is HARMLESS —
    a printed board routinely has parts a hand-drawn cell does not model yet, and
    "лишняя роль" is a cell of its own in the guard table.

An instance that does not fit is NOT thrown away: it is returned with the reason
that names the offending role, so the caller can COUNT the non-fitting ones
("K instances lack roles") instead of losing them.

The records are SIMPLE (uuid, ref, role, cluster, resolved sheet chain) — never
``explore.Selected`` with its ``fp``. The design of the board cache
(design_2026_10_08_board_cache.md) puts exactly these fields in its first tier
("личность деталей"), so when the source of that tier changes, only the door
converter ``snapshot_parts`` below changes — this module and every dialog that
calls it do not.

Qt-free on purpose: the guards call these functions DIRECTLY, with no dock and
no QApplication (the same rule gui/cell_entity_choice.py follows).
"""
from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Callable, Iterable, Optional, Sequence

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.i18n import _
from kicadstamp.sheet_names import sheet_in_path

from .reead import sheet_of

__all__ = [
    "Part", "InstanceCandidate", "CellSpec", "CellCandidate", "CellChoices",
    "role_mismatch_reason", "instance_candidates", "cell_candidates",
    "choose_cells", "cell_row_label", "others_line", "others_tooltip",
    "instance_parts", "snapshot_parts",
]


@dataclasses.dataclass(frozen=True)
class Part:
    """One board footprint as the candidate rules read it.

    ``uuid`` is the footprint's own identity (``Selected.fp.uuid``), carried for
    callers that later need to address the part (never used in the fit itself).
    ``sheet`` is the RESOLVED sheet-name CHAIN (``Selected.sheet`` after
    ``snapshot_with_resolved_sheets``), not a single name — ``sheet_of`` derives
    the instance identity from it, and ``sheet_in_path`` matches a chosen name
    against it.
    """
    uuid: Optional[str]
    ref: str
    role: Optional[str] = None
    cluster: Optional[str] = None
    sheet: tuple = ()


@dataclasses.dataclass(frozen=True)
class InstanceCandidate:
    """One instance of the board considered for a cell.

    ``fits`` is the verdict; ``reason`` is EMPTY when it fits and otherwise the
    one role problem that decided it (see ``role_mismatch_reason``).
    ``entity_name`` is the entity that already covers this pair ("taken"), or
    None. A taken candidate stays in the list — the table shows it greyed out —
    but it is never checked.
    """
    cluster: str
    sheet: Optional[str]
    refs: tuple = ()
    fits: bool = True
    reason: str = ""
    entity_name: Optional[str] = None

    @property
    def taken(self) -> bool:
        return bool(self.entity_name)

    @property
    def label(self) -> str:
        """The dropdown/table line of the instance: "CLUSTER — sheet"."""
        return "{cluster} — {sheet}".format(
            cluster=self.cluster, sheet=self.sheet or _("(no sheet)"))


@dataclasses.dataclass(frozen=True)
class CellSpec:
    """One cell as a candidate rule reads it: its name, its slot roles (ONE
    entry per slot, so multiplicity is the list itself) and its identity."""
    name: str
    roles: tuple = ()
    uuid: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class CellCandidate:
    """One cell considered for an instance (the reverse direction).

    ``current`` marks the entity's OWN cell — the one it stands on now. It is
    always offered (so the user always sees where the entity is), even when it
    does not fit; then the row says so through :func:`cell_row_label`.
    """
    name: str
    uuid: Optional[str] = None
    fits: bool = True
    reason: str = ""
    current: bool = False

    @property
    def taken(self) -> bool:
        return False


@dataclasses.dataclass(frozen=True)
class CellChoices:
    """The cells a PICKER offers for ONE entity (Денис, 09.10.2026: no huge list
    of every cell).

    ``candidates`` — the rows to show, fitting ones first: every FITTING cell plus
    the entity's CURRENT cell (always, even when it does not fit — then it carries
    ``current=True`` and the row says so). ``others`` — the cells that do not fit
    and are NOT the current one; they are NOT listed, only counted (the picker's
    one grey line and its tooltip). ``orphan`` — the fit could not be checked at
    all (no snapshot / dangling graph / no instance on the board): then
    ``candidates`` is EVERY cell, fit not checked, and ``others`` is empty.
    """
    candidates: tuple = ()
    orphan: bool = False
    others: tuple = ()


def role_mismatch_reason(instance_roles: Counter,
                         cell_roles: Counter) -> str:
    """The ONE "does this role multiset fit" rule — empty when it fits.

    Exact equality per CELL role (Денис, 09.10.2026): a cell role must stand in
    the instance EXACTLY as many times as the cell has slots of it. Fewer is a
    shortfall (``role R: 1 of 2`` — 0 of N included), more is an excess
    (``role R ×3 for 2 slots``). A role the instance has that the CELL does not
    name is not looked at at all — "лишняя роль" is HARMLESS.

    Deterministic: roles are examined in sorted order, so one instance always
    reports the same first problem.
    """
    for role in sorted(cell_roles):
        want = cell_roles[role]
        got = instance_roles.get(role, 0)
        if got < want:
            return _("role {role}: {got} of {want}").format(
                role=role, got=got, want=want)
        if got > want:
            return _("role {role} ×{got} for {want} slots").format(
                role=role, got=got, want=want)
    return ""


def _role_counts(roles: Iterable) -> Counter:
    """Counter over the NON-empty role names — a part with no role contributes
    nothing (it is "extra" like any other unmodelled role)."""
    return Counter(r for r in (roles or ()) if r)


def instance_candidates(parts: Iterable[Part],
                        cell_roles: Iterable,
                        taken: Optional[Callable] = None
                        ) -> list[InstanceCandidate]:
    """Every INSTANCE on the board, with its verdict for this cell.

    ``parts`` — the whole board snapshot as :class:`Part` records.
    ``cell_roles`` — the cell's slot roles (one entry per slot).
    ``taken`` — an optional callable ``(cluster, sheet) -> entity_name|None``
    answering "is this pair already an entity of this cell". The caller binds
    the project's ONE rule, ``tree_from_selection.find_entity_for_source``
    (``find_entity_for_source(cfg, cell=..., cluster=..., sheet=...)``, which
    narrows the cell branch by the instance's own sheet); this module never
    restates it.

    Footprints without a Cluster tag do not form an instance and are skipped
    (the same convention ``group_selected`` uses). Non-fitting instances are
    RETURNED, not dropped, so the caller can count them.
    """
    want = _role_counts(cell_roles)
    groups: dict = {}
    for part in parts or ():
        if not part.cluster:
            continue
        groups.setdefault((part.cluster, sheet_of(part.sheet)), []).append(part)

    out: list[InstanceCandidate] = []
    for (cluster, sheet), members in groups.items():
        got = _role_counts(m.role for m in members)
        reason = role_mismatch_reason(got, want)
        entity_name = taken(cluster, sheet) if taken is not None else None
        refs = tuple(sorted(str(m.ref) for m in members if m.ref))
        out.append(InstanceCandidate(
            cluster=cluster, sheet=sheet, refs=refs, fits=not reason,
            reason=reason, entity_name=entity_name))
    # Fitting first, then by (cluster, sheet) — the "подходящие сверху" order,
    # stable for the table and the reverse direction alike.
    out.sort(key=lambda c: (not c.fits, c.cluster.lower(), c.sheet or ""))
    return out


def cell_candidates(instance_parts: Iterable[Part],
                    cells: Iterable[CellSpec]) -> list[CellCandidate]:
    """Every CELL of the config, with its verdict for ONE instance.

    ``instance_parts`` — the parts of the instance under consideration (see
    :func:`instance_parts`). ``cells`` — the config's cells as :class:`CellSpec`.
    The rule is the reverse of :func:`instance_candidates` and is literally the
    same function — :func:`role_mismatch_reason` — never a second copy.
    Fitting cells come first; non-fitting keep their reason for the greyed row.
    """
    got = _role_counts(p.role for p in instance_parts or ())
    out: list[CellCandidate] = []
    for cell in cells or ():
        reason = role_mismatch_reason(got, _role_counts(cell.roles))
        out.append(CellCandidate(name=cell.name, uuid=cell.uuid,
                                 fits=not reason, reason=reason))
    out.sort(key=lambda c: (not c.fits, c.name.lower()))
    return out


def choose_cells(instance_parts: Iterable[Part], cells: Iterable[CellSpec],
                 current: Optional[str] = None) -> CellChoices:
    """The ONE "which cells does a picker show" rule (Денис, 09.10.2026).

    Both the page's combobox and the "Change cell…" dialog reach it through
    ``change_cell_flow.cell_choices`` — one function, never a second copy. Only
    FITTING cells become rows, plus the entity's CURRENT cell: always offered (so
    the user always sees where the entity stands), marked ``current`` and, when it
    does not fit, saying so in its row. Every OTHER non-fitting cell is not a row
    at all — it is counted in ``others`` for the picker's one grey line. The role
    rule itself is the SAME :func:`role_mismatch_reason`.
    """
    got = _role_counts(p.role for p in instance_parts or ())
    shown: list = []
    others: list = []
    for cell in cells or ():
        reason = role_mismatch_reason(got, _role_counts(cell.roles))
        cand = CellCandidate(name=cell.name, uuid=cell.uuid, fits=not reason,
                             reason=reason, current=(cell.name == current))
        if cand.fits or cand.current:
            shown.append(cand)
        else:
            others.append(cand)
    # Fitting first, then the (non-fitting) current row — the dropdown order.
    shown.sort(key=lambda c: (not c.fits, c.name.lower()))
    others.sort(key=lambda c: c.name.lower())
    return CellChoices(candidates=tuple(shown), others=tuple(others))


def cell_row_label(cand) -> str:
    """One picker ROW (a combobox item / a dialog row): the cell name; the
    CURRENT cell that does not fit says so, with its reason."""
    if cand.current and cand.reason:
        return "{name} — {marker} {reason}".format(
            name=cand.name, marker=_("current, does not fit:"), reason=cand.reason)
    return cand.name


def others_line(others) -> str:
    """The ONE grey line under a picker: "K other cells do not fit"; empty when
    nothing was left out (the caller hides the label then)."""
    if not others:
        return ""
    return _("{count} other cells do not fit").format(count=len(others))


def others_tooltip(others, limit: int = 8) -> str:
    """The grey line's tooltip — the first reasons, one per line (the full list
    can be long; the line itself only counts)."""
    return "\n".join("{name}: {reason}".format(name=c.name, reason=c.reason)
                     for c in (others or ())[:limit])


def instance_parts(parts: Iterable[Part], cluster: str,
                   sheet: Optional[str] = None) -> list:
    """The parts of the (Cluster, sheet) instance, from the whole snapshot.

    The Cluster step is the project's SEGMENT match
    (:func:`cluster_matching.cluster_prefix_match`), the sheet step is the
    project's ONE "does this name this component" rule
    (:func:`sheet_names.sheet_in_path`) — the same pair
    ``kicadstamp.cell_instance.resolve_context_footprints`` applies on live
    footprints, here on the plain records. ``sheet`` is optional: without it the
    cluster alone selects the parts (a single-instance cell that never stored a
    sheet narrows nothing — the (Sheet, Cluster) cascade's own best-effort
    convention). A stale/unknown sheet that matches nothing falls back to the
    cluster's parts, never to an empty answer that would hide the instance.
    """
    if not cluster:
        return []
    members = [p for p in (parts or ())
               if p.cluster and cluster_prefix_match(p.cluster, cluster)]
    if sheet:
        by_sheet = [p for p in members if sheet_in_path(p.sheet, sheet)]
        # Narrow ONLY when the sheet genuinely reduces and finds something —
        # the same convention narrow_candidates_by_sheet follows; a stale sheet
        # that matches nothing never hides the instance.
        if by_sheet:
            return by_sheet
    return members


def snapshot_parts(snapshot: Iterable, sheet_names: Optional[dict] = None
                   ) -> list:
    """The board snapshot as :class:`Part` records — THE one door converter.

    The sheet chains are re-resolved through the project's ONE helper
    ``imprint.snapshot_with_resolved_sheets`` (a live GUI snapshot's chains are
    all None, because BoardConnection connects without a schematic_dir); the
    footprint identity is read as ``Selected.fp.uuid``. The helper is imported
    INSIDE the function so this module stays Qt-free at import time — its guards
    need no QApplication.

    An empty/None ``sheet_names`` is a no-op in the helper (it returns the
    snapshot untouched), so records that already carry a resolved chain pass
    through unchanged — which is what the tests build.
    """
    from .imprint import snapshot_with_resolved_sheets
    resolved = snapshot_with_resolved_sheets(list(snapshot or ()),
                                             sheet_names or {})
    out: list[Part] = []
    for s in resolved:
        fp = getattr(s, "fp", None)
        out.append(Part(
            uuid=getattr(fp, "uuid", None),
            ref=str(getattr(s, "ref", "") or ""),
            role=getattr(s, "role", None),
            cluster=getattr(s, "cluster", None),
            sheet=tuple(getattr(s, "sheet", None) or ()),
        ))
    return out
