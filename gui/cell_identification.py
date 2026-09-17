# gui/cell_identification.py
"""Identify WHICH instance of a cell the user has just selected on the board —
the brain behind the cell-anchor page's "Fill from selection" button
(plan_2026_09_17_spoke_s1_identify_by_selection.md, design
design_2026_09_17_spoke_cell_editing.md §1/§3 Р1–Р4).

Why a selection has to be identified at all: a Cell is an abstract template, so
the editor needs to know which PLACED instance it is editing. The (Cluster,
Sheet) pair answers that for an ordinary cluster — every Role occurs once in it.
A SPOKE cell breaks that: its cluster holds the same Role many times over
(measured on the live board 2026-09-17: FPGA_PWR_BANK = C_FPGA_BULK x25 +
C_FPGA_BYPASS x25, one pair per power pin), so the cluster alone cannot say which
pair is meant. The SELECTION can, and what it produces is a "role -> refdes"
map — the identification.

Hard rule (design §1): refdes NEVER reach the config, a cell or a spoke. The map
returned here is an INTERFACE CACHE (gui_state.json, see gui/cell_edit_context),
checked against the board every time it is used.

Pure by construction — no Qt, no adapter, no board read: the worker collects the
data (the selected components, the cluster's members with their roles) and this
module decides, so the SAME function is unit-testable without a QApplication and
without KiCad.

The refusal ORDER is the diagnostic probe's own (probe_spoke_cell_identification
.probe_selection) and is not a free choice: the probe is the stage's "before /
after" ruler and stays unedited, so its classification and this one must agree by
construction. It is: nothing selected -> a component without a Role -> a Role
selected twice -> more than one Cluster -> a Role that is not the cell's ->
spoke / ordinary cluster.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _

from .docks.reead import group_selected

# The two kinds of instance the selection can resolve to (design §1: a spoke is
# "a cluster where some role of the cell occurs more than once").
KIND_CLUSTER = "cluster"
KIND_SPOKE = "spoke"


@dataclass(frozen=True)
class SelectionRecord:
    """One component of the selection (or one member of its cluster) in the
    shape the identification reads: refdes, Role, Cluster and the RESOLVED sheet
    chain.

    A plain record ON PURPOSE — it is the data two very different readers hand
    over: the worker fills it from the live board (a
    kicadstamp.domain.board.Footprint plus field reads and the sheet map), and
    the Source tab's hand-typed "Refs" field fills it from the board snapshot it
    already has (no second board read). explore.Selected satisfies the same
    contract, so a caller holding one can pass it unchanged."""

    ref: str
    role: Optional[str] = None
    cluster: Optional[str] = None
    sheet: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class Identification:
    """The instance the selection pins down.

    cluster/sheet — the working context to write on the Source tab.
    role_to_ref — {role: refdes}; ALWAYS filled, for an ordinary cluster and for
        a spoke alike (design Р2: "if the cluster is identified, the refs must be
        written down automatically"). This is what every later live read uses.
    kind — "cluster" or "spoke"; reported, never a silent mode switch (Р3).
    repeated_roles — ((role, count), ...) for a spoke: the evidence that made it
        one, so the caller can say it out loud in the Log.
    members — refs of the identified INSTANCE (the cluster's members narrowed by
        the identified sheet), so the caller can report "k of n selected"."""

    cluster: str
    sheet: Optional[str]
    role_to_ref: dict
    kind: str
    repeated_roles: tuple = ()
    members: tuple = ()


def _cell_name(cell) -> str:
    return str(getattr(cell, "name", "?"))


def _cell_roles(cell) -> set:
    """The cell's own component roles — the ONLY legitimate roles of an
    instance (never the board's)."""
    return {getattr(c, "role", None)
            for c in (getattr(cell, "components", None) or ())} - {None}


def _refs(items) -> str:
    return ", ".join(sorted(str(getattr(i, "ref", "?")) for i in items))


def _member_in_cluster(member, cluster: str) -> bool:
    """Does this member belong to the identified cluster TAG?

    A member that carries no Cluster tag at all is taken as already scoped (the
    worker reads members BY the identified tag, so they are peers by
    construction). A member that DOES carry one is checked with
    cluster_prefix_match — the project's own rule for "same cluster family" — so
    a caller that hands in the WHOLE board snapshot (the hand-typed refs path)
    can never mix a neighbouring instance into the counts."""
    member_cluster = getattr(member, "cluster", None)
    if not member_cluster:
        return True
    return cluster_prefix_match(str(member_cluster), cluster)


def _member_on_sheet(member, sheet: Optional[str]) -> bool:
    """Is this cluster member part of the instance on `sheet`?

    The project's own rule for a resolved sheet identity: `sheet in fp_sheet`
    (gui/docks/reead.fully_selected_clusters / explore.Board.select(sheet=)) —
    membership in the footprint's resolved sheet chain, never equality with its
    first segment. An unresolved sheet narrows nothing (the same best-effort
    behaviour the (Sheet, Cluster) cascade has everywhere)."""
    if not sheet:
        return True
    return sheet in (getattr(member, "sheet", None) or ())


def identify_cell_instance(cell, selected, cluster_members, entities=(),
                           sheet_names: Optional[Mapping] = None
                           ) -> Identification:
    """The instance of `cell` that `selected` pins down, or a fatal explaining
    why it pins down nothing.

    cell — a Cell-shaped object with .name and .components (.role each).
    selected — the board selection as objects with .ref/.role/.cluster/.sheet
        (explore.Selected's shape; the worker pre-resolves .sheet, so this
        function needs no adapter).
    cluster_members — the already-read members of the selected cluster with
        .ref/.role/.sheet. For an ordinary cluster they contribute the refs of
        the roles the user did not click; for a spoke they are the EVIDENCE (a
        role occurring more than once) and nothing else.
    entities / sheet_names — the config's Entity rows and sheet map, used only
        for the sheet rule, through the project's ONE grouping of a selection
        (gui/docks/reead.group_selected) — no second sheet rule is invented here.

    Refusals, in the probe's order: nothing selected; a component without a Role;
    a Role selected twice; more than one Cluster; one Cluster on more than one
    Sheet; a Role that is not part of the cell. A spoke whose selection is not
    exactly one component per cell role is refused by NAME (Р3) — half a pair
    would pin the wrong instance."""
    selected = list(selected or ())
    if not selected:
        raise ValidationError(format_fatal_error(
            _("nothing is selected on the board — select the components of ONE "
              "instance of cell {cell!r}").format(cell=_cell_name(cell)),
            [_("a spoke cell is identified by exactly ONE pair; an ordinary "
               "cluster can be identified from any part of it")]))

    untagged = [s for s in selected if not getattr(s, "role", None)]
    if untagged:
        raise ValidationError(format_fatal_error(
            _("no Role on {refs} — every selected component must be tagged with "
              "a Role before the instance can be identified").format(
                  refs=_refs(untagged)),
            [_("tag the components on the board — the “Refs” tab of this cell "
               "writes a Role per component in one go — then identify again")]))

    duplicated = sorted(role for role, count in Counter(
        s.role for s in selected).items() if count > 1)
    if duplicated:
        raise ValidationError(format_fatal_error(
            _("the same Role is selected twice ({roles}) — select exactly one "
              "component per Role").format(roles=", ".join(duplicated)),
            [_("two components of one Role say nothing about WHICH component is "
               "the anchor of this instance")]))

    clusters = {s.cluster for s in selected}
    if len(clusters) > 1:
        raise ValidationError(format_fatal_error(
            _("the selection spans several clusters ({clusters}) — an instance "
              "belongs to ONE cluster").format(
                  clusters=", ".join(sorted(str(c) for c in clusters))),
            [_("select the components of a single cluster instance, then "
               "identify again")]))
    cluster = next(iter(clusters), None)
    if not cluster:
        raise ValidationError(format_fatal_error(
            _("no Cluster on {refs} — the instance cannot be told from another "
              "one of the same cell").format(refs=_refs(selected)),
            [_("set the Cluster on the board — the “Refs” tab of this cell "
               "writes one cluster onto the whole table — then identify "
               "again")]))

    # The sheet: the project's ONE grouping of a selection by (Cluster, sheet
    # instance), so a cluster tag standing on two sheets (cloned sheets) is
    # refused rather than half-used.
    groups = group_selected(selected, entities, sheet_names)
    if len(groups) > 1:
        sheets = sorted(str(sheet) for (_cluster, sheet) in groups)
        raise ValidationError(format_fatal_error(
            _("cluster {cluster!r} stands on several sheets ({sheets}) — the "
              "selection mixes two instances").format(
                  cluster=cluster, sheets=", ".join(sheets)),
            [_("select the components of ONE sheet instance, then identify "
               "again")]))
    sheet = next(iter(groups), (cluster, None))[1]

    cell_roles = _cell_roles(cell)
    foreign = [s for s in selected if s.role not in cell_roles]
    if foreign:
        first = foreign[0]
        raise ValidationError(format_fatal_error(
            _("{ref} has Role {role!r} which is not in cell {cell!r}").format(
                ref=first.ref, role=first.role, cell=_cell_name(cell)),
            [_("cell {cell!r} has roles: {roles}").format(
                cell=_cell_name(cell),
                roles=", ".join(sorted(cell_roles)) or _("none")),
             _("check the Role on the “Refs” tab of this cell — it lists this "
               "cell's own roles")]))

    members = [m for m in (cluster_members or ())
               if _member_in_cluster(m, cluster) and _member_on_sheet(m, sheet)]
    repeated = sorted(
        (role, count) for role, count in Counter(
            m.role for m in members if getattr(m, "role", None) in cell_roles
        ).items() if count > 1)

    if repeated:
        # A SPOKE: the roles repeat inside the instance, so only the selection
        # can say which pair is meant — and it must name every role exactly once.
        missing = sorted(cell_roles - {s.role for s in selected})
        if missing or len(selected) != len(cell_roles):
            raise ValidationError(format_fatal_error(
                _("a spoke needs exactly one component per cell role: missing "
                  "{missing}").format(missing=", ".join(missing) or "?"),
                [_("select ONE pair of this cell on the board (the repeated "
                   "roles make the cluster ambiguous), then identify again")]))
        role_to_ref = {s.role: s.ref for s in selected}
        return Identification(cluster=cluster, sheet=sheet,
                              role_to_ref=role_to_ref, kind=KIND_SPOKE,
                              repeated_roles=tuple(repeated),
                              members=tuple(m.ref for m in members))

    # An ORDINARY cluster: the clicked refs are authoritative, the rest of the
    # roles of the cell are found in the instance (a partial selection is a
    # normal way to identify it). A role the cluster does not carry at all stays
    # out of the map — the caller logs its name (Р3).
    role_to_ref = {s.role: s.ref for s in selected}
    in_cluster: dict = {}
    for member in members:
        if member.role in cell_roles and member.role not in in_cluster:
            in_cluster[member.role] = member.ref
    for role in cell_roles:
        if role not in role_to_ref and role in in_cluster:
            role_to_ref[role] = in_cluster[role]
    return Identification(cluster=cluster, sheet=sheet, role_to_ref=role_to_ref,
                          kind=KIND_CLUSTER, members=tuple(m.ref for m in members))
