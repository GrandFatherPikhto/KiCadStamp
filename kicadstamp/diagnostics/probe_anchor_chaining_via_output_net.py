#!/usr/bin/env python3
"""probe_anchor_chaining_via_output_net.py — read-only proof-of-concept probe:
does resolving FB_PI_FLT by its OUTPUT pad's net (unique per instance) instead
of its INPUT pad's net (shared +3V3, today's net_template_same_as_role
"C_IN_BYPASS"/"C_IN_BULK") yield exactly one candidate per PIF block — and
does feeding that resolved position as anchor_position into the EXISTING
role_narrowing._narrow_ambiguous_candidates narrow C_IN_BYPASS/C_IN_BULK's
candidates to 1 via the already-shipped step-5 proximity gate?

This is a PROOF OF CONCEPT, NOT a fix (plan_2026_09_08_anchor_chaining_proof_
script.md): it never writes to the board or config, never modifies
role_narrowing.py / clone_role_resolver.py, and reuses their functions
directly (_narrow_ambiguous_candidates, narrow_candidates_by_sheet,
resolve_sheet_path_names) instead of copying the cascade logic.

Live context (reproduced 2026-09-08 on the open 3CH-AWG-TIA-v103 board):
the three Channel_1 PIF clones materialized from the ch1_dac_buf
tree_instances declaration (pif_avdd/pif_clkvdd/pif_dvdd_channel_0__
ch1_dac_buf) all carry placement.cluster == 'DAC_BUF' (the declaration's
cluster override erases the PIF_* distinction), so their shared-net roles
C_IN_BYPASS/C_IN_BULK/FB_PI_FLT stay ambiguous (3 candidates each: C144/C149/
C153, C143/C147/C152, FB18/FB19/FB20) after the net(+3V3) and sheet(Channel_1)
steps. FB_PI_FLT is special: its OUTPUT pad (pad 2) sits on a UNIQUE
per-instance net (/Channel_1/DAC/+3V3_CLKVDD|AVDD|DVDD) — the same net its
C_OUT_* siblings carry — so grouping FB candidates by their pad-2 net should
yield one candidate per PIF block, and that FB's world position can anchor
the proximity narrowing of the two roles that have NO unique net at all.

Candidate narrowing mirrors resolve_roles_by_nets faithfully: a role's
candidates are first filtered to those whose pads carry the role's EXPECTED
INPUT net (the cell slot's net_template literal — +3V3 for all three PIF
roles here), exactly like the production code's `expected_net in nets_on_fp`,
and only then narrowed by sheet. That keeps the candidate counts comparable to
the production dry-run (3 C_IN_BYPASS / 3 C_IN_BULK / 3 FB_PI_FLT on
Channel_1) — otherwise OpAmp PIF blocks on other shared nets (+2V5/-2V5) leak
into the set.

Usage (live board open in KiCad, read-only):
    python -m kicadstamp.diagnostics.probe_anchor_chaining_via_output_net \
        --config profiles/3ch-awg-tia-v103/config.sexp \
        --only pif_avdd_channel_0__ch1_dac_buf,pif_clkvdd_channel_0__ch1_dac_buf,pif_dvdd_channel_0__ch1_dac_buf
"""
import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME  # noqa: E402
from kicadstamp.config import load_config  # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter  # noqa: E402
from kicadstamp.placement.services.role_narrowing import (  # noqa: E402
    _narrow_ambiguous_candidates,
    narrow_candidates_by_sheet,
)
from kicadstamp.utils.units import MM  # noqa: E402

# The FB_PI_FLT role and the C_IN roles whose ambiguity the concept aims to
# resolve by chaining off the FB's uniquely-resolvable position.
FB_ROLE = "FB_PI_FLT"
C_IN_ROLES = ("C_IN_BYPASS", "C_IN_BULK")
# Roles that carry the PIF block's OUTPUT net literal (/Channel_0/DAC/+3V3_*)
# — physically the net FB_PI_FLT's pad 2 sits on (the ferrite's output rail).
OUTPUT_NET_ROLES = ("C_OUT_BULK", "C_OUT_BYPASS")


def _mm(fp) -> tuple[float, float]:
    """World position of a footprint in millimetres (KiCad reports nm)."""
    return (fp.position.x / MM, fp.position.y / MM)


def _pad_net(adapter, fp, number: str) -> str:
    """Net name on a specific pad of fp, '' when the pad does not exist."""
    pad = adapter.get_pad_by_number(fp, number)
    return pad.net_name if pad is not None and pad.net_name else ""


def _describe_fp(adapter, fp, sheet_names) -> str:
    cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
    role = adapter.get_field_value(fp, ROLE_FIELD_NAME) or ""
    x, y = _mm(fp)
    return (f"  {fp.ref:7s} Role={role:20s} Cluster={cluster:22s} "
            f"pos=({x:8.3f}, {y:8.3f}) mm")


def _role_expected_input_net(cell, role: str) -> str | None:
    """The cell slot's net_template literal for `role` — the EXPECTED INPUT net
    resolve_roles_by_nets filters candidates by before any narrowing (its
    `expected_net in nets_on_fp`). Only plain literals are usable by the probe
    (no {placeholder}); a templated net_template returns None (cannot be
    resolved without the full resolver — recorded as a found-fact)."""
    for slot in cell.components:
        if slot.role == role:
            nt = slot.net_template
            if nt and "{" not in nt:
                return nt
            return None
    return None


def _expected_fb_output_net(cell, sheet: str) -> str | None:
    """The net FB_PI_FLT's pad 2 SHOULD carry for this cell on `sheet`: the
    cell's OUTPUT-role net literal (/Channel_0/DAC/+3V3_*), with the channel
    prefix re-pointed at the placement's own sheet. This is a read of the
    CELL CONFIG for the probe's expected value — NOT a resolver change: the
    output net literal already lives in C_OUT_BULK/C_OUT_BYPASS slots and is
    exactly the per-instance-unique signal the anchor-chaining idea relies on
    (see plan §0 — the ferrite's output rail is the same node as its C_OUT
    siblings). Returns None when the cell has no such literal (found-fact,
    concept cannot apply to this cell as written)."""
    if not sheet:
        return None
    for slot in cell.components:
        net_template = slot.net_template or ""
        if slot.role in OUTPUT_NET_ROLES and net_template.startswith("/Channel_"):
            return re.sub(r"^/Channel_\d+/", f"/{sheet}/", net_template)
    return None


def _candidates_for_role(adapter, cell, role: str, expected_in_net: str | None,
                         all_fps, sheet: str, sheet_names):
    """Faithful mirror of resolve_roles_by_nets candidate gathering: all
    footprints with Role == `role` whose pads carry the role's EXPECTED INPUT
    net (when the net is a plain literal), then sheet narrowing. Returns the
    (possibly empty) narrowed list."""
    if expected_in_net is None:
        return []
    matched = []
    for fp in all_fps:
        if adapter.get_field_value(fp, ROLE_FIELD_NAME) != role:
            continue
        nets_on_fp = {p.net_name for p in adapter.get_footprint_pads(fp) if p.net_name}
        if expected_in_net in nets_on_fp:
            matched.append(fp)
    if not sheet:
        return matched
    return narrow_candidates_by_sheet(matched, sheet, sheet_names)


def _group_fb_by_pad2(adapter, fbs):
    """{pad-2 net: [candidate refs...]} — the uniqueness check of plan step 3.
    Returns (groups, all_unique)."""
    groups: dict[str, list] = {}
    for fp in fbs:
        net = _pad_net(adapter, fp, "2")
        groups.setdefault(net, []).append(fp)
    all_unique = all(len(v) == 1 for v in groups.values())
    return groups, all_unique


def _run_c_in(adapter, label, role, sheet_names, candidates, clone_entity,
              anchor_position, verbose=False):
    """Run the REAL _narrow_ambiguous_candidates on one C_IN role of one clone,
    with anchor_position = the FB_PI_FLT found by output pad net. Returns the
    (before, after, note, gap_mm, gap_ok, picked_ref) tuple for the summary."""
    if verbose:
        print(f"  [{label}] role {role!r}: {len(candidates)} candidate(s) "
              f"after net+sheet narrowing:")
        for fp in sorted(candidates, key=lambda f: f.ref):
            print(_describe_fp(adapter, fp, sheet_names))

    narrowed, note = _narrow_ambiguous_candidates(
        candidates, clone_entity, adapter, set(), anchor_position,
        clone_name=label, role=role, sheet_names=sheet_names)

    # Independent gap measurement (same formula as the proximity gate): closest
    # vs second-closest candidate to the anchor, both in mm.
    with_dist = sorted(
        ((math.hypot(fp.position.x - anchor_position.x,
                     fp.position.y - anchor_position.y) / MM), fp)
        for fp in candidates)
    closest_mm = with_dist[0][0] if with_dist else float("nan")
    second_mm = with_dist[1][0] if len(with_dist) > 1 else float("nan")
    gap_ok = second_mm >= 2 * max(closest_mm, 1e-6)
    picked = narrowed[0].ref if len(narrowed) == 1 else None
    return (len(candidates), len(narrowed), note, closest_mm, second_mm,
            gap_ok, picked)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config",
                        default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--only",
                        default="pif_avdd_channel_0__ch1_dac_buf,"
                                "pif_clkvdd_channel_0__ch1_dac_buf,"
                                "pif_dvdd_channel_0__ch1_dac_buf",
                        help="comma-separated entity names, same as apply's --only")
    parser.add_argument("--verbose", action="store_true",
                        help="print every C_IN candidate before narrowing")
    args = parser.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    cfg, ctx = load_config(args.config)
    sheet_names = dict(ctx.sheet_names) if hasattr(ctx.sheet_names, "items") \
        else ctx.sheet_names
    entities_by_name = {e.name: e for e in cfg.entities}

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    try:
        all_fps = adapter.get_footprints()

        print(f"live board: {len(all_fps)} footprints\n")

        summary_rows = []
        for name in sorted(only):
            entity = entities_by_name.get(name)
            if entity is None:
                print(f"!! entity {name!r} not found in cfg.entities — skipped\n")
                continue
            cell = cfg.cells.get(entity.cell or "")
            if cell is None:
                print(f"!! entity {name!r}: cell {entity.cell!r} not found — skipped\n")
                continue
            sheet = entity.sheet
            print("=" * 100)
            print(f"CLONE {name!r}  cell={entity.cell!r}  sheet={sheet!r}  "
                  f"cluster={entity.cluster!r}")
            print(f"  cell roles: {[s.role for s in cell.components]}")

            # Expected input net (the shared net today's resolver matches on) —
            # the same literal the live FATAL reported ("on net '+3V3'").
            fb_in_net = _role_expected_input_net(cell, FB_ROLE)

            # Plan step 1: FB_PI_FLT candidates, net(+3V3) then sheet narrowed —
            # the same pool the production dry-run sees (3 on Channel_1).
            fb_cands = _candidates_for_role(
                adapter, cell, FB_ROLE, fb_in_net, all_fps, sheet, sheet_names)
            print(f"  [{name}] FB_PI_FLT (expected input net {fb_in_net!r}): "
                  f"{len(fb_cands)} candidate(s) after net+sheet narrowing — "
                  f"pad1 (input) vs pad2 (output) nets:")
            for fp in sorted(fb_cands, key=lambda f: f.ref):
                print(_describe_fp(adapter, fp, sheet_names)
                      + f"  pad1={_pad_net(adapter, fp, '1')!r}"
                        f"  pad2={_pad_net(adapter, fp, '2')!r}")

            # Plan step 3: group by pad-2 net — the output nets must be unique
            # per candidate (one FB per PIF block).
            groups, all_unique = _group_fb_by_pad2(adapter, fb_cands)
            print(f"  pad-2-net grouping ({len(groups)} group(s)): "
                  + ("ALL UNIQUE — one FB per output net"
                     if all_unique else
                     "DUPLICATES FOUND — output net does NOT separate "
                     + "; ".join(f"{net}: {[f.ref for f in fs]}"
                                 for net, fs in groups.items() if len(fs) > 1)))

            # Plan step 4: the FB_PI_FLT expected for THIS clone carries the
            # clone cell's output rail net on pad 2 (expected value from the
            # cell config — C_OUT_* net literal re-pointed at this sheet).
            expected_out = _expected_fb_output_net(cell, sheet)
            anchor_fb = None
            if expected_out is not None:
                matching = [fp for fp in fb_cands
                            if _pad_net(adapter, fp, "2") == expected_out]
                if len(matching) == 1:
                    anchor_fb = matching[0]
            print(f"  expected FB pad-2 net for this cell on {sheet!r}: "
                  f"{expected_out!r}"
                  + (f"  -> FB anchor = {anchor_fb.ref}"
                     if anchor_fb is not None
                     else "  -> NO single FB matches (concept fails for this clone)"))

            if anchor_fb is None:
                print("  !! cannot anchor-chain this clone — no unique FB_PI_FLT "
                      "by output net. Recorded as found-fact, no C_IN narrowing "
                      "attempted.\n")
                for role in C_IN_ROLES:
                    summary_rows.append((name, role, "no-anchor", 0, 0, "", 0.0,
                                         0.0, False, None))
                continue

            anchor_position = anchor_fb.position
            ax, ay = _mm(anchor_fb)
            print(f"  anchor_position = FB {anchor_fb.ref} @ ({ax:.3f}, {ay:.3f}) mm")

            # Plan steps 5-6: real proximity narrowing of the two shared-net
            # roles, anchored at the resolved FB position. Candidate gathering
            # mirrors resolve_roles_by_nets (net + sheet), so the 'before' count
            # matches the production dry-run.
            for role in C_IN_ROLES:
                in_net = _role_expected_input_net(cell, role)
                candidates = _candidates_for_role(
                    adapter, cell, role, in_net, all_fps, sheet, sheet_names)
                res = _run_c_in(adapter, name, role, sheet_names, candidates,
                                entity, anchor_position, verbose=args.verbose)
                before, after, note, c_mm, s_mm, gap_ok, picked = res
                if note:
                    print(f"  [{name}] role {role!r}: note:{note}")
                print(f"  [{name}] role {role!r}: {before} -> {after}  "
                      f"closest={c_mm:.2f} mm second={s_mm:.2f} mm  "
                      + ("NARROWED to 1" if picked is not None
                         else "STILL AMBIGUOUS (insufficient gap)"))
                summary_rows.append((name, role, "anchored", before, after, note,
                                     c_mm, s_mm, gap_ok, picked))
            print()

        # ---- Plan step 7: summary table ----
        print("\n" + "=" * 100)
        print("SUMMARY  (before = candidates after net+sheet narrowing; "
              "after = result of REAL _narrow_ambiguous_candidates "
              "with FB-PI-FLT-position anchor)")
        print(f"{'clone':44} {'role':12} {'before':>7} {'after':>6} "
              f"{'closest_mm':>11} {'second_mm':>10} {'narrowed_to':>12}")
        print("-" * 100)
        for name, role, mode, before, after, note, c_mm, s_mm, gap_ok, picked \
                in summary_rows:
            if mode == "no-anchor":
                print(f"{name:44} {role:12} {'--':>7} {'--':>6} "
                      f"{'--':>11} {'--':>10} {'NO-ANCHOR':>12}")
                continue
            narrowed_s = picked if picked is not None else "(still ambiguous)"
            print(f"{name:44} {role:12} {before:>7} {after:>6} "
                  f"{c_mm:>11.2f} {s_mm:>10.2f} {narrowed_s:>12}")

        # ---- Overall verdict ----
        print("\nVERDICT:")
        anchored = [r for r in summary_rows if r[2] == "anchored"]
        no_anchor = [r for r in summary_rows if r[2] == "no-anchor"]
        resolved_fb = len(no_anchor) == 0
        narrowed_all = all(r[7] >= 2 * max(r[6], 1e-6) and r[9] is not None
                           for r in anchored) if anchored else False
        print(f"  FB_PI_FLT resolvable uniquely by output pad net: "
              f"{'YES' if resolved_fb else 'NO (a clone had no single FB anchor)'}")
        print(f"  C_IN_BYPASS/C_IN_BULK narrowed to 1 by FB-position anchor "
              f"with >=2x gap on ALL clones: "
              f"{'YES' if narrowed_all else 'NO (check per-clone gaps above)'}")
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
