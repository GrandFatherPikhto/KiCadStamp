# kicadstamp/config/tree_instance_cluster_axis.py
"""Is a declaration's `cluster:` axis INERT? — the question the 3 -> 4 converter
must answer before it removes the key (Н9 of plan_2026_11_07_tree_instances_
dialog_and_subtree, §6.6, corrected by acceptance §10).

The axis is gone from the grammar (format 4): an instance changes the SHEET
only. A format-3 file may still carry it, and the converter's job is to drop it
where it provably changed NOTHING and to REFUSE, naming the declaration, where
it did something — a silent drop would move real geometry.

**The criterion is about VALUES, not about the shape of the template.** The axis
only ASSIGNED a value, so it changed nothing exactly when every assignment it
would have made lands on the value that is already there. The old, shape-based
criterion refused a whole template for CONTAINING a mount node and was wrong on
the one live profile (`3ch-awg-tia-v103`: five mount anchors already carry
`(cluster "DAC_BUF")`), which turned a harmless key into an unopenable profile.

Three assignments, three questions (acceptance §10 п.1–3):

  1. the generated ROLE anchor — `anchor.cluster` of a role-anchored template
     must already equal the declaration's cluster;
  2. every MOUNT/COMPONENT node whose anchor names the template's OWN sheet —
     its `anchor.cluster` must already equal the declaration's cluster; a
     MISSING cluster is a refusal too, because the axis would have ADDED it;
  3. the generated Entity copies — the template's placement records must be
     either COMPOSITE (>1 distinct cluster, where the composite guard skipped
     the per-copy override anyway) or all equal to the declaration's cluster.

A refusal names the very assignment that would have changed: the node (or role
anchor), what stands there now, and what the axis would have written.

Nothing here touches the board or the graph: a pure dict question about ONE
file's own declarations — a declaration whose template lives in ANOTHER file of
the graph is refused, because its template cannot be inspected here.
"""
from __future__ import annotations

from ..i18n import _
from .models import TreeInstance
from .tree_instance_checks import (template_entry_sheets,
                                   template_generated_clusters,
                                   template_needs_old_sheet)

# Record keys that NO LONGER EXIST but that a READER must still accept into the
# raw dict: the 3 -> 4 converter is what decides about them (drop where they
# provably changed nothing, refuse where they did not). Without this the s-expr
# parser would die on "unknown key in a record" BEFORE any converter could run,
# and the honest refusal below would be unreachable. A CURRENT-format file that
# still carries the key is refused by the loader's own known-key check — the
# list here is a READ-time allowance, never a written field.
RETIRED_RECORD_KEYS = {(TreeInstance, "cluster")}

__all__ = ["RETIRED_RECORD_KEYS", "axis_removal_refusal"]


def _shown(value) -> str:
    """A stored cluster for a refusal message: the VALUE, or `(none)` when the
    key is absent — an absent cluster is not "equal to anything": the axis would
    have ADDED it, which is a change like any other."""
    return _("(none)") if value is None else repr(value)


def _is_role_anchor(anchor) -> bool:
    return (isinstance(anchor, dict) and isinstance(anchor.get("role"), str)
            and bool(anchor.get("role")))


def _subject_ref(anchor, nodes: list) -> str | None:
    """The template's anchor subject: an explicit (self (ref "...")), else the
    single top-level placement node — the SAME rule the expansion's entry gate
    uses (`_template_root_entity_ref`) and the one self-anchor resolution
    requires. A bare (self) with several top-level nodes has no subject."""
    if isinstance(anchor, dict) and isinstance(anchor.get("self"), dict):
        ref = anchor["self"].get("ref")
        if ref:
            return ref
    if len(nodes) == 1 and isinstance(nodes[0], dict):
        kind = nodes[0].get("kind")
        if kind is None or kind == "placement":
            return nodes[0].get("ref")
    return None


def template_old_sheet(template: dict, entities_by_name: dict,
                       instance: dict) -> str | None:
    """The template's own sheet as the EXPANSION computes it, without logging:
    the declaration's `root` record first (§7.3), then the role anchor, then the
    subject record, then Н3's derivation from the placement records."""
    nodes = template.get("nodes") or []
    anchor = template.get("anchor")
    if _is_role_anchor(anchor):
        return anchor.get("sheet")
    root = instance.get("root")
    if isinstance(root, dict) and root.get("ref"):
        record = entities_by_name.get(root["ref"])
        return record.get("sheet") if isinstance(record, dict) else None
    subject = _subject_ref(anchor, nodes)
    if subject is not None:
        record = entities_by_name.get(subject)
        if isinstance(record, dict):
            return record.get("sheet")
    if template_needs_old_sheet(nodes):
        sheets, without = template_entry_sheets(nodes, entities_by_name)
        if len(sheets) == 1 and not without:
            return next(iter(sheets))
    return None


def _inside_addresses(nodes: list, old_sheet: str | None) -> list[tuple]:
    """(kind, ref, current cluster) for every MOUNT/COMPONENT node whose anchor
    names the template's OWN sheet — the addresses the axis used to rewrite."""
    out: list[tuple] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        anchor = node.get("anchor")
        if isinstance(anchor, dict) and node.get("kind") in ("mount", "component"):
            sheet = anchor.get("sheet")
            if sheet and old_sheet is not None and sheet == old_sheet:
                out.append((node.get("kind"), node.get("ref") or "?",
                            anchor.get("cluster")))
        out.extend(_inside_addresses(node.get("children") or [], old_sheet))
    return out


def axis_removal_refusal(instance: dict, templates_by_name: dict,
                         entities_by_name: dict) -> str | None:
    """None when the declaration's `cluster:` provably changed nothing, else the
    reason it cannot be removed — naming the ASSIGNMENT that would have changed.

    `templates_by_name` is the FILE's own `trees:` index: a declaration whose
    template lives in another file of the graph cannot be checked here, and
    guessing is forbidden."""
    name = instance.get("name")
    template_name = instance.get("template")
    cluster = instance.get("cluster")
    template = templates_by_name.get(template_name)
    if not isinstance(template, dict):
        return _(
            "tree_instance {name!r}: template {template!r} is not in this file, "
            "so whether cluster: did anything cannot be checked here").format(
                name=name, template=template_name)
    nodes = template.get("nodes") or []
    anchor = template.get("anchor")
    if _is_role_anchor(anchor) and anchor.get("cluster") != cluster:
        return _(
            "tree_instance {name!r}: the template's role anchor {role!r} carries "
            "cluster {was}, which cluster: {cluster!r} would have rewritten").format(
                name=name, role=anchor.get("role"),
                was=_shown(anchor.get("cluster")), cluster=cluster)
    old_sheet = template_old_sheet(template, entities_by_name, instance)
    for kind, ref, current in _inside_addresses(nodes, old_sheet):
        if current != cluster:
            return _(
                "tree_instance {name!r}: {kind} node {ref!r} of template "
                "{template!r} carries cluster {was}, which cluster: {cluster!r} "
                "would have rewritten").format(
                    name=name, kind=kind, ref=ref, template=template_name,
                    was=_shown(current), cluster=cluster)
    generated = template_generated_clusters(nodes, entities_by_name)
    if len(generated) > 1 or generated == {cluster}:
        return None
    return _(
        "tree_instance {name!r}: template {template!r} is homogeneous "
        "(clusters {clusters}), so cluster: {cluster!r} rewrote every generated "
        "copy — dropping the key would change what this instance IS").format(
            name=name, template=template_name,
            clusters=", ".join(repr(c) for c in sorted(generated)) or _("(none)"),
            cluster=cluster)
