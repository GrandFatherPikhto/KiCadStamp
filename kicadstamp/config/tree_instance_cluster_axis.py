# kicadstamp/config/tree_instance_cluster_axis.py
"""Is a declaration's `cluster:` axis INERT? — the question the 3 -> 4 converter
must answer before it removes the key (Н9 of plan_2026_11_07_tree_instances_
dialog_and_subtree, §6.6).

The axis is gone from the grammar (format 4): an instance changes the SHEET
only. A format-3 file may still carry it, and the converter's job is to drop it
where it provably changed NOTHING and to REFUSE, naming the declaration, where
it did something — a silent drop would move real geometry.

The criterion (§6.6), read off the code that USED the key:

  * the template must NOT be role-anchored — the axis was substituted into the
    generated role anchor too, and that anchor narrows an EXTERNAL live search;
  * the template must have no MOUNT or COMPONENT node whose anchor names the
    template's OWN sheet, for the same reason (the axis was substituted into
    that address as well);
  * the clusters the template's placement records generate must be either
    COMPOSITE (>1 distinct value, where the per-copy override was skipped by the
    composite guard) or exactly the declaration's cluster (where the override
    rewrote every copy to the value it already had).

Everything else is "the axis mattered", and the honest answer there is a
refusal. Nothing here touches the board or the graph: a pure dict question about
ONE file's own declarations — a declaration whose template lives in ANOTHER
file of the graph is refused, because its template cannot be inspected here.
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
    if (isinstance(anchor, dict) and isinstance(anchor.get("role"), str)
            and anchor.get("role")):
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


def _inside_addresses(nodes: list, old_sheet: str | None) -> list[str]:
    """The refs of MOUNT/COMPONENT nodes whose anchor names the template's OWN
    sheet — the addresses the axis used to narrow."""
    out: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        anchor = node.get("anchor")
        if isinstance(anchor, dict) and node.get("kind") in ("mount", "component"):
            sheet = anchor.get("sheet")
            if sheet and old_sheet is not None and sheet == old_sheet:
                out.append(node.get("ref") or "?")
        out.extend(_inside_addresses(node.get("children") or [], old_sheet))
    return out


def axis_removal_refusal(instance: dict, templates_by_name: dict,
                         entities_by_name: dict) -> str | None:
    """None when the declaration's `cluster:` provably changed nothing, else the
    reason it cannot be removed (one line, naming the declaration).

    `templates_by_name` is the FILE's own `trees:` index: a declaration whose
    template lives in another file of the graph cannot be checked here, and
    guessing is forbidden."""
    name = instance.get("name")
    template_name = instance.get("template")
    template = templates_by_name.get(template_name)
    if not isinstance(template, dict):
        return _(
            "tree_instance {name!r}: template {template!r} is not in this file, "
            "so whether cluster: did anything cannot be checked here").format(
                name=name, template=template_name)
    anchor = template.get("anchor")
    if (isinstance(anchor, dict) and isinstance(anchor.get("role"), str)
            and anchor.get("role")):
        return _(
            "tree_instance {name!r}: template {template!r} is role-anchored, so "
            "cluster: narrowed the generated role anchor as well").format(
                name=name, template=template_name)
    nodes = template.get("nodes") or []
    inside = _inside_addresses(nodes, template_old_sheet(
        template, entities_by_name, instance))
    if inside:
        return _(
            "tree_instance {name!r}: template {template!r} addresses {refs} on "
            "its own sheet, so cluster: narrowed those searches too").format(
                name=name, template=template_name,
                refs=", ".join(repr(ref) for ref in inside))
    generated = template_generated_clusters(nodes, entities_by_name)
    if len(generated) > 1 or generated == {instance.get("cluster")}:
        return None
    return _(
        "tree_instance {name!r}: template {template!r} is homogeneous "
        "(clusters {clusters}), so cluster: {cluster!r} rewrote every generated "
        "copy — dropping the key would change what this instance IS").format(
            name=name, template=template_name,
            clusters=", ".join(repr(c) for c in sorted(generated)) or _("(none)"),
            cluster=instance.get("cluster"))
