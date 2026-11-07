# kicadstamp/config/tree_instance_refs.py
"""The two INNER references of a template that must follow the node renames —
moved verbatim out of `tree_instances.py` (whose size is under the ratchet; Н6
needed room and the giant must not grow).

Both take the SAME `ref_map` the expansion walk collects: a template's
`pivot_ref` and its `(anchor (self (ref ...)))` name nodes of the TEMPLATE, and
the copy's nodes are renamed (`placement` → `__{instance}`, `net_trace` →
`<name>__{instance}`, mount/copper unchanged). A name missing from the map is a
TEMPLATE bug and a fatal HERE, naming the template and the instance — otherwise
the generated tree would fail to load with "names no node of this tree",
blaming the copy instead of its template.

`root` (Н6) reuses the same two functions: the cut subtree is expanded by the
same walk, so the same map covers it — and a reference naming a node OUTSIDE
the copied subtree lands here as "names no node", which the caller reports with
the subtree wording.
"""
from __future__ import annotations

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

__all__ = ["rewrite_pivot_ref", "rewrite_self_ref"]


def rewrite_pivot_ref(gen: dict, template_name: str, instance_name: str,
                      ref_map: dict[str, str]) -> None:
    """Follow the node renames for a template's `pivot_ref`."""
    pivot_ref = gen.get('pivot_ref')
    if pivot_ref is None:
        return
    new_ref = ref_map.get(pivot_ref)
    if new_ref is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: template {template!r} pivot_ref {ref!r} "
              "names no node of the template tree").format(
                  name=instance_name, template=template_name, ref=pivot_ref),
            [_("the tree's inner point (pivot-ref) must name a node of the same "
               "tree — check the template's pivot-ref")]))
    gen['pivot_ref'] = new_ref


def rewrite_self_ref(gen: dict, template_name: str, instance_name: str,
                     ref_map: dict[str, str]) -> None:
    """Follow the node renames for a template's (self (ref "...")) anchor."""
    anchor = gen.get('anchor')
    if not isinstance(anchor, dict):
        return
    self_data = anchor.get('self')
    if not isinstance(self_data, dict):
        return
    ref = self_data.get('ref')
    if ref is None:
        return
    new_ref = ref_map.get(ref)
    if new_ref is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: template {template!r} self anchor ref "
              "{ref!r} names no node of the template tree").format(
                  name=instance_name, template=template_name, ref=ref),
            [_("the self anchor's (ref ...) must name a placement node of the "
               "same tree — check the template's anchor")]))
    self_data['ref'] = new_ref
