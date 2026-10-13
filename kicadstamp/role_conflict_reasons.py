# kicadstamp/role_conflict_reasons.py
"""Why a role could not be resolved inside the entity's OWN Cluster — the ONE
wording of that refusal (2026-10-13, plan
plan_2026_10_13_cluster_confined_roles.md).

Why this module exists
----------------------
An entity/record carrying a `cluster` may only be resolved to components of
THAT cluster. The FILTER lives in placement/services/role_narrowing.py
(`own_cluster_candidates`); the WORDS live here, so that the apply-side
resolvers (`clone_role_resolver.resolve_roles_by_nets` /
`resolve_roles_by_selection`) and the read-only "Read current position" path
(`gui/docks/live_position.py`) say exactly the same thing — one text, one place,
never a second copy per caller.

The live incident behind it (Denis, 2026-10-13): a redraw of `spoke_vdd_02_mcu`
dragged C27/C29 out of the NEIGHBOURING `SPOKE_VDDA` cluster, because the role's
baked net_template became the selector once the role in its own cluster turned
out to be ambiguous. The old sentence ("role 'C_IN_BULK' occurs 2 times in
cluster 'SPOKE_VDD_02' — this is a spoke cell: select one pair and use “Fill from
selection”") named neither the components nor the real fix, and spoke cells were
withdrawn on 2026-10-12 — hence the word and the advice are gone.

What the refusal distinguishes (plan, rule 3)
--------------------------------------------
* the cluster is not on the board AT ALL -> tag this instance's components;
* the role exists, but in ANOTHER cluster or with an EMPTY Cluster field -> the
  component is NAMED (ref + short uuid + position) and explicitly NOT taken;
* ONE refdes stands twice on the board (two copies of one symbol) -> "delete the
  extra copy"; a net can never tell two copies of one symbol apart;
* several DIFFERENT refdes carry the role -> "give them distinct roles in the
  schematic" (the R9/D5 "R_PROT" case).

Duck-typed and Qt-free, like its neighbour `refdes_duplicates.py`: the domain
Footprint and the GUI's records are read through the SAME reader
(`duplicate_copy_of`) and the duplicated-refdes DETECTOR is reused
(`duplicate_refdes_groups`), never re-implemented here.
"""
from __future__ import annotations

from typing import Iterable

from .cluster_matching import cluster_prefix_match
from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .i18n import _
from .refdes_duplicates import DuplicateCopy, duplicate_copy_of, duplicate_refdes_groups

__all__ = [
    "SHORT_UUID_LENGTH", "short_uuid_text", "component_descriptor",
    "has_refdes_copies", "narrowed_by_hint", "role_not_in_cluster_problems",
    "role_conflict_problems", "role_scope_problems",
]

#: How much of a uuid a human is asked to compare (see short_uuid_text).
SHORT_UUID_LENGTH = 8


def short_uuid_text(value) -> str:
    """The first ``SHORT_UUID_LENGTH`` characters of a uuid, with an ellipsis.

    A full uuid is a value nobody reads: the refusal only has to make two copies
    of one refdes distinguishable by eye (the live C31 pair differed in nothing
    else), so the long tail is cut."""
    if not value:
        return _("(no uuid)")
    text = str(value)
    return text if len(text) <= SHORT_UUID_LENGTH else text[:SHORT_UUID_LENGTH] + "…"


def component_descriptor(adapter, record) -> str:
    """One component as the refusal names it: ref, short uuid, position.

    ``C31 (uuid a1b2c3d4…, at 84.646, 108.750 mm)`` — the shape is used both for
    the components that are NOT taken and for the copies of a duplicated refdes
    inside one cluster, so a user can point at the footprint on the board."""
    copy: DuplicateCopy = duplicate_copy_of(record)
    return _("{ref} (uuid {uuid}, {position})").format(
        ref=copy.ref or _("(no refdes)"),
        uuid=short_uuid_text(copy.uuid),
        position=copy.position_text,
    )


def _copy_text(copy: DuplicateCopy) -> str:
    """One COPY of a duplicated refdes (see component_descriptor's shape).

    The orphan fact comes from the shared reader, not from a guess here: an
    untagged-of-schematic copy is the usual reason a refdes stands twice (the
    live C31/C32 incident), and saying so turns "delete a copy" into an action."""
    orphan = _(" — NOT linked to the schematic") if not copy.on_schematic else ""
    return _("uuid {uuid}, {position}{orphan}").format(
        uuid=short_uuid_text(copy.uuid), position=copy.position_text, orphan=orphan)


def _in_cluster(adapter, record, cluster: str) -> bool:
    """Does this record carry the working cluster (the project's ONE matcher,
    cluster_prefix_match — segment prefix, case-insensitive)?"""
    return cluster_prefix_match(
        adapter.get_field_value(record, CLUSTER_FIELD_NAME) or '', cluster)


def role_not_in_cluster_problems(adapter, role: str, cluster: str) -> list[str]:
    """The refusal lines for "the own cluster holds NO component of this role".

    The wording follows the board's own facts (plan, rule 2/3):
      * the cluster is not tagged on ANYTHING -> the fix is tagging;
      * the role lives in another cluster or without a Cluster field -> name it
        and say why it is not taken (the near miss must not read as a mystery,
        and the tagged component must never be silently taken instead).
    """
    problems: list[str] = [
        _("role {role!r}: no component with this role carries the working cluster "
          "{cluster!r}").format(role=role, cluster=cluster),
    ]
    board = list(adapter.get_footprints())
    if not any(_in_cluster(adapter, fp, cluster) for fp in board):
        problems.append(
            _("nothing on this board carries the cluster tag {cluster!r} — tag this "
              "instance's components with it (Fieldstool or the schematic), or fix "
              "the working Cluster on the Source tab").format(cluster=cluster))
    for fp in board:
        if adapter.get_field_value(fp, ROLE_FIELD_NAME) != role:
            continue
        if _in_cluster(adapter, fp, cluster):
            continue
        foreign = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
        if foreign:
            problems.append(
                _("  {component} carries this role but stands in cluster {cluster!r} — "
                  "it is NOT taken").format(
                    component=component_descriptor(adapter, fp), cluster=foreign))
        else:
            problems.append(
                _("  {component} carries this role but has no Cluster field — "
                  "it is NOT taken").format(component=component_descriptor(adapter, fp)))
    return problems


def narrowed_by_hint(clone) -> str:
    """What a narrowing cascade had ALREADY used up, said out loud: the entity's
    own sheet/Cluster (the two "own identity" fields the internal-role cascade
    narrows by, see role_narrowing.py) — or the honest "neither is set" branch.

    Moved here 2026-10-13 (plan plan_2026_10_13_cluster_confined_roles) so the
    wording lives with the rest of the refusal text, and
    clone_role_resolver.py — a giant — only shrinks."""
    sheet = getattr(clone, "sheet", None)
    cluster = getattr(clone, "cluster", None)
    if sheet or cluster:
        narrowed_by = ", ".join(
            (_("this placement's sheet {sheet!r}").format(sheet=sheet) if sheet else "",
             _("this placement's Cluster {cluster!r}").format(cluster=cluster) if cluster else "")
        ).strip(", ")
        return _(" (already narrowed by {narrowed_by}, but not enough)").format(
            narrowed_by=narrowed_by)
    return _(" (neither this placement's sheet nor its Cluster set — if these components are "
             "physically different instances, one of them would narrow to one)")


def has_refdes_copies(candidates: Iterable) -> bool:
    """Does ONE refdes stand more than once among these candidates?

    The check a caller MUST make BEFORE any narrowing (plan, cascade step 2): a
    net, a sheet, the current selection and physical proximity can never tell two
    copies of one symbol apart, so letting the cascade look at them would pick
    "the closest copy" — a silent guess. The detector itself stays in
    refdes_duplicates.py; this is only its boolean face for the resolvers."""
    return bool(duplicate_refdes_groups(candidates))


def role_conflict_problems(adapter, role: str, cluster: str,
                           candidates: Iterable, extra: str = "") -> list[str]:
    """The refusal lines for "several components of this role DO carry the own
    cluster" (the plan's two cases, told apart by the detector, not by a guess):

      * one refdes twice -> delete the extra copy (a net cannot separate two
        copies of one symbol, so this is never narrowed, only reported);
      * several distinct refdes -> distinct roles are needed in the schematic.

    ``extra`` — one ALREADY-WORDED line the caller adds when the narrowing it ran
    did not separate the candidates (the placement's own sheet/Cluster, the
    current selection, physical proximity): the caller owns what IT tried, this
    module owns how the components are named."""
    candidates = list(candidates)
    duplicates = duplicate_refdes_groups(candidates)
    if duplicates:
        return [
            _("role {role!r}: {ref} stands on the board {count} times — delete the "
              "extra copy: {copies}").format(
                role=role, ref=ref, count=len(copies),
                copies="; ".join(_copy_text(copy) for copy in copies))
            for ref, copies in sorted(duplicates.items())
        ]
    components = ", ".join(
        component_descriptor(adapter, fp)
        for fp in sorted(candidates, key=lambda fp: getattr(fp, "ref", "") or ""))
    problems = [
        _("role {role!r}: {count} different components in cluster {cluster!r} carry "
          "this role — give them distinct roles in the schematic: {components}").format(
            role=role, count=len(candidates), cluster=cluster, components=components),
    ]
    if extra:
        problems.append(extra)
    return problems


def role_scope_problems(adapter, role: str, cluster: str | None,
                        own_candidates: Iterable) -> list[str]:
    """The ONE entry point both paths call: the refusal lines for a role that
    could not be resolved inside its own cluster — empty when there is nothing to
    refuse.

    ``own_candidates`` — the components of this role INSIDE the entity's own
    cluster (``own_cluster_candidates``), already filtered by the caller, which
    is also why this function never filters again: one rule, one place.

    An empty/None ``cluster`` (refs-only or sheet-only addressing) is not this
    rule's business — no problems. One own candidate is the answer, not a
    conflict."""
    own = list(own_candidates)
    if not cluster or len(own) == 1:
        return []
    if not own:
        return role_not_in_cluster_problems(adapter, role, cluster)
    return role_conflict_problems(adapter, role, cluster, own)
