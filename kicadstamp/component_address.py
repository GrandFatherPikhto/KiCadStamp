# kicadstamp/component_address.py
"""The ADDRESS of a kind "component" tree node (plan_2026_09_17 Э3).

A component node places ONE live component directly — no cell, no Entity, no
config record. Which component is named by the node's own nested (anchor ...)
child (trees._parse_component_anchor), either:

    (anchor (role "AD_DAC") (sheet "Channel_0") (cluster "DAC_BUF") (pad "11"))
    (anchor (ref "IC7"))

This module is the ONE place that turns such an address into a LIVE footprint,
so every consumer (the materializer, the rigid-group capture, the GUI form's
"where is it now") resolves the same way and none of them re-implements the
addressing:

  * a (role ...) address goes through `resolve_footprint_by_role` — the SAME
    search Rule/ClonePlacement/NET TRACES use, with its sheet/cluster narrowing
    cascade (the project's one "which component does this role mean" rule);
  * a (ref ...) address goes through `resolve_footprint_by_ref` — the same
    exact-refdes lookup the anchor paths use.

A `pad` in the address does NOT take part in the resolution: it says "seat the
component BY THIS PAD into the node's own point", which is a placement concern
(the CoordinatePlacement's own anchor='pad' mode), not an addressing one.
"""
import logging

from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .placement.services.clone_role_resolver import resolve_footprint_by_role
from .placement.services.component_resolver import resolve_footprint_by_ref
from .sheet_names import resolve_sheet_path_names

logger = logging.getLogger(__name__)

__all__ = [
    "component_address_label",
    "component_anchor",
    "component_sheet_hint",
    "resolve_component_footprint",
]


def component_anchor(node):
    """The node's address (a TreeAnchor), or None when this is not a component
    node / it carries no anchor. A component node WITHOUT an address cannot be
    loaded (trees._parse_node fatals), so None here means "not a component
    node" in every real flow."""
    if getattr(node, "kind", None) != "component":
        return None
    return getattr(node, "anchor", None)


def component_address_label(node) -> str:
    """What an error message calls this node's address — the node's own ref (a
    local name), because that is the identity the user typed and the identity
    `--only` works by."""
    return getattr(node, "ref", "?")


def resolve_component_footprint(adapter, node, sheet_names=None):
    """The live footprint a component node places, or a fatal naming the node.

    Raises ValidationError (the project's `format_fatal_error` block, so the
    caller surfaces it the same way every other resolution failure is) when the
    address names nothing on the board or names several components — the
    resolution never guesses, exactly like the role resolvers it delegates to.
    The underlying resolver's own ValidationError is re-raised unchanged, so its
    honest message ("... not found", "... ambiguous, narrow with sheet/cluster")
    reaches the user verbatim."""
    anchor = component_anchor(node)
    if anchor is None:
        raise ValidationError(format_fatal_error(
            _("Tree node {ref!r} is not a component node (or has no address)")
            .format(ref=component_address_label(node)),
            [_("a component node needs an (anchor (ref ...)) or an "
               "(anchor (role ...)) — the node itself places nothing else")]))
    label = component_address_label(node)
    if anchor.ref is not None:
        return resolve_footprint_by_ref(adapter, anchor.ref, label)
    return resolve_footprint_by_role(adapter, anchor.role, anchor.anchor_sheet,
                                     anchor.anchor_cluster, sheet_names or {},
                                     label)


def component_sheet_hint(fp, anchor, sheet_names=None) -> str | None:
    """The sheet name a transient CoordinatePlacement should carry to narrow the
    SAME footprint back from the board at apply time.

    The address's own `sheet` when it has one (that is what the user wrote),
    else the leaf of the resolved footprint's hierarchical path — which its own
    path always contains, so the narrowing can never drop the very footprint the
    address just resolved to (see role_narrowing.narrow_candidates_by_sheet:
    a sheet that matches nothing leaves the candidate list untouched)."""
    if anchor is not None and anchor.anchor_sheet:
        return anchor.anchor_sheet
    names = [n for n in resolve_sheet_path_names(fp, sheet_names or {}) if n]
    return names[-1] if names else None
