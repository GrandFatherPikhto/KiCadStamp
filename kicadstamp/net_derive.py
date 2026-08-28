# kicadstamp/net_derive.py
"""net_derive.py — the "net is a computed function" contract (Phase 0).

`derive_role_nets()` turns a cell's roles + the source/known net evidence into
{role: NetDerivation(net, source)} for a TARGET cluster — the automatic net
coordinate (plan_2026_08_28_auto_nets_full_automation.md, Phase 0 step 0.2).
Pure: callers (Phase 2) gather the live data from the adapter; this module only
combines it by the three-priority rule below. No adapter, no YAML, no board.

Priority order (each derivation carries its provenance so diagnostics can say
WHERE a net came from — that provenance is what Phase 2's SCC-as-diagnostic
report attaches to):
  1. live_pad       — the role's net is already known ON the target (a live
                      read / resolve_net_from_role of a resolved instance of
                      that role on the target).
  2. prefix_remap   — the role's SOURCE-cluster net is remapped to the target
                      by its hierarchical prefix (/Channel_0/X -> /Channel_1/X,
                      TwinMap.twin_net semantics) when the source and target
                      are symmetric twin clusters.
  3. kuhn / kuhn_scc_group — the role's source net is mapped to the target by
                      kicadstamp.net_matching (Kuhn + Tarjan SCC); provenance
                      is kuhn_scc_group when the source net sits in an
                      ambiguous SCC (any member is a valid answer — safe
                      default), otherwise plain kuhn.

Global/rule nets (GND/VCC/...) are deliberately NOT derived here: they are the
same by name on both clusters, and the global-net instance disambiguation is
deferred to the Phase 2 mini-design (the documented risk that Kuhn must not
silently "smear" a global case into its local-net matching logic).

A role with no applicable priority is simply ABSENT from the result — the
caller decides the fallback (never a silent guess).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# Provenance values — the single source of truth for the contract.
LIVE_PAD = "live_pad"
PREFIX_REMAP = "prefix_remap"
KUHN = "kuhn"
KUHN_SCC_GROUP = "kuhn_scc_group"


@dataclass(frozen=True)
class NetDerivation:
    """One derived net and its provenance."""
    net: str
    source: str                       # LIVE_PAD | PREFIX_REMAP | KUHN | KUHN_SCC_GROUP
    ambiguous_group: frozenset[str] | None = None  # only for KUHN_SCC_GROUP


def _scc_group_for(net: str, scc_groups: list[frozenset[str]] | None) -> frozenset[str] | None:
    if not scc_groups:
        return None
    for group in scc_groups:
        if net in group:
            return group
    return None


def derive_role_nets(
    *,
    roles: Iterable[str],
    role_source_nets: dict[str, str],
    live_pad_nets: dict[str, str] | None = None,
    source_prefix: str | None = None,
    target_prefix: str | None = None,
    kuhn_mapping: dict[str, str] | None = None,
    kuhn_scc_groups: list[frozenset[str]] | None = None,
) -> dict[str, NetDerivation]:
    """Derive {role: NetDerivation} for a target cluster.

    Args:
        roles: cell roles to derive.
        role_source_nets: role -> net in the SOURCE/known context (the evidence
            priorities 2-3 remap from).
        live_pad_nets: role -> net already known ON the target (priority 1).
        source_prefix/target_prefix: hierarchical prefixes for priority 2
            (e.g. "/Channel_0/" -> "/Channel_1/"). Only applied when a role's
            source net starts with source_prefix.
        kuhn_mapping: source net -> target net from net_matching's
            match_template_to_target (priority 3).
        kuhn_scc_groups: ambiguous source-net SCC groups (for provenance).

    Returns: {role: NetDerivation} — roles with no applicable priority are
        absent (the caller decides the fallback; never a silent guess).
    """
    live_pad_nets = live_pad_nets or {}
    kuhn_mapping = kuhn_mapping or {}
    kuhn_scc_groups = kuhn_scc_groups or []

    out: dict[str, NetDerivation] = {}
    for role in roles:
        # Priority 1 — live read on the target.
        if role in live_pad_nets:
            out[role] = NetDerivation(live_pad_nets[role], LIVE_PAD)
            continue

        src = role_source_nets.get(role)
        # Priority 2 — hierarchical prefix remap of the source net.
        if (src is not None and source_prefix and target_prefix
                and src.startswith(source_prefix)):
            out[role] = NetDerivation(
                target_prefix + src[len(source_prefix):], PREFIX_REMAP)
            continue

        # Priority 3 — Kuhn/SCC correspondence of the source net.
        if src is not None and src in kuhn_mapping:
            group = _scc_group_for(src, kuhn_scc_groups)
            out[role] = NetDerivation(
                kuhn_mapping[src], KUHN_SCC_GROUP if group else KUHN, group)
            continue

        # No priority applies — leave the role out (caller's fallback).
    return out


__all__ = [
    "LIVE_PAD",
    "PREFIX_REMAP",
    "KUHN",
    "KUHN_SCC_GROUP",
    "NetDerivation",
    "derive_role_nets",
]
