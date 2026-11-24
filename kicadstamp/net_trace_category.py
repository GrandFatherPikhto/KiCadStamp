# kicadstamp/net_trace_category.py
"""Which OWNER a `net_traces:` record belongs to — the `(category ...)` field.

A piece of copper on a board has exactly ONE owner
(plan_2026_11_10_extra_copper_by_selection.md, «Решение: третий хозяин меди»):

  * a CELL — copper inside one tree node, written by the cell refresh;
  * INTER-NODE — pad to pad across two or more tree nodes, written by «Reread
    inter-node copper»;
  * EXTRA — what the classifier does NOT take as inter-node (a dead end into a
    pour, a stitch), written ONLY by «Read extra copper from selection».

This module owns the third one's MARKER and nothing else. `NetTrace` carries it
as a plain optional field, so:

  * `category` ABSENT means INTER-NODE — every profile written before this field
    existed keeps its exact meaning, byte for byte, and the format number is NOT
    bumped (an optional field with a default was added the same way by
    `TreeInstance.root`, `d6f85e31`);
  * the s-expr writer/parser read the record dataclass's own fields
    (`config/sexp_format.py` is schema-driven), so `(category "extra")` needs no
    grammar work at all — the round trip is covered by a cell, not by a second
    reader.

Why the value is a STRING and not a bool: a future owner (a zone stitch, И3)
gets its own word without changing the shape of the field."""
from __future__ import annotations

#: The only category value that exists today. `None` on a record means
#: INTER-NODE — the historical meaning, never written.
CATEGORY_EXTRA = "extra"


def is_extra(record) -> bool:
    """True when `record` is EXTRA copper (written by the extra-copper door).

    THE one test every caller uses instead of comparing the literal — the
    inter-node re-read asks it to leave such a record alone (п. 8), the dock
    asks it to build the «Extra copper (n)» pseudo-row, the clone expansion
    asks it to carry the category over."""
    return getattr(record, "category", None) == CATEGORY_EXTRA


def inter_node_records(records, identities) -> list:
    """The INTER-NODE records among `records`: referenced by one of the tree's
    `kind "net_trace"` nodes (`identities`) AND not extra (п. 8).

    THE filter the re-read builds its match maps and its `missing` list from, so
    an extra record is never matched, never refreshed and never called «the
    copper is no longer on the board» — another door wrote it, another door
    re-reads it."""
    from .config import net_trace_effective_name

    return [nt for nt in records
            if net_trace_effective_name(nt) in identities and not is_extra(nt)]


def extra_signatures(records, identities) -> dict:
    """`pads:` signature -> identity of every EXTRA record this tree references.

    The map that lets the inter-node re-read say «that copper is already written
    in extra record X» instead of taking it (п. 8): a dead end that later grew a
    track to another node is INTERNODE now, but the piece is another owner's
    record already, so this door names it and leaves it."""
    from .config import net_trace_effective_name, net_trace_pad_signature

    out: dict = {}
    for nt in records:
        signature = net_trace_pad_signature(nt)
        if signature and is_extra(nt) and net_trace_effective_name(nt) in identities:
            out.setdefault(signature, net_trace_effective_name(nt))
    return out


def extra_shadow_line(identity: str) -> str:
    """One report line for the case above — the copper stays where it is written."""
    from .i18n import _

    return _(
        "the copper between these pads is written in extra record {name!r} — "
        "«Read extra copper from selection» owns it, so it is not taken as "
        "inter-node copper").format(name=identity)
