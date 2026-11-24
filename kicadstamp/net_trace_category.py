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
    """net -> [(pads signature, identity), …] of every EXTRA record this tree uses.

    The map the inter-node re-read asks before it takes a piece (п. 8, Н1 of the
    acceptance): a dead end that later grew a track to another node is INTERNODE
    now, yet part of its copper is another owner's record already.

    The match is a SUBSET on the SAME NET, never equality: an extra record is
    always written from a STUB, i.e. it has exactly ONE pad, while the bridge that
    absorbed it has two or more — equality could never fire (measured by the
    acceptance on the real form: the bridge was taken as a NEW inter-node record
    and the dead end kept its own, two owners for one piece of copper)."""
    from .config import net_trace_effective_name, net_trace_pad_signature

    out: dict = {}
    for nt in records:
        if not is_extra(nt) or net_trace_effective_name(nt) not in identities:
            continue
        out.setdefault(nt.net, []).append(
            (net_trace_pad_signature(nt), net_trace_effective_name(nt)))
    return out


def shadow_of(extras: dict, signature, net) -> str | None:
    """The identity of the EXTRA record that already owns part of this piece — or
    None. `extras` is `extra_signatures`' map, `signature`/`net` describe the FRESH
    inter-node piece.

    «Owns part of it» = the record's pads are a SUBSET of the piece's pads on the
    same net. An empty signature (a record with no `pads:` — only a hand-edited
    profile can carry one) is a subset of anything, so it shadows every piece of
    its net: the refusal is the honest answer there, and the line names the record
    so the human decides."""
    for signature_, identity in extras.get(net, ()):
        if signature_ <= frozenset(signature):
            return identity
    return None


def extra_shadow_line(identity: str) -> str:
    """One report line for the case above — the copper stays where it is written."""
    from .i18n import _

    return _(
        "the copper of this piece is written in extra record {name!r} — «Read "
        "extra copper from selection» owns it, so it is not taken as inter-node "
        "copper; delete that record, then re-read").format(name=identity)
