#!/usr/bin/env python3
"""
generate_10cl006.py — config generator for 10CL006YE144C8G, one
data source (BANKS/CLUSTER_MAP) into three derived artefacts:

  1. rules‑based config (ManualSpoke) — as before, apply‑ready,
     profiles/generated/10CL006YE144C8G.sexp.
  2. clone_placements‑based config (ClonePlacement) — same geometry,
     but via cloning, because Rule/ManualSpoke cannot clone tracks
     (see discussion) —
     profiles/generated/10CL006YE144C8G.clone_placements.sexp.
     Not automatically included (no include mechanism in configs) —
     the clone_placements: block is copied manually into
     profiles/3ch-awg-tia.sexp after dry‑run testing.
  3. pad -> cluster table (for manual Cluster field assignment in
     Eeschema Bulk Edit, if proximity‑based resolution is insufficient
     for some spokes) — previously a separate script
     generate_cluster_table.py, now merged here —
     profiles/generated/10CL006YE144C8G.cluster_table.md.

IMPORTANT about the template: rules use the templates_file template
"cap_pair_standard" (net: null on common vias — inherits rule.net, only
Rule can do that), clone_placements use "cap_pair_standard_clone"
(net: "{power_net}" — resolved via params, only ClonePlacement can do that).
These are TWO SEPARATE entries in profiles/templates/3ch-awg-tia.sexp,
deliberately not one — Rule does not resolve "{placeholder}" at all, while
ClonePlacement fatally fails on vias without a net. They are used in parallel
(rules are still production, clone_placements are for testing).
"""
from dataclasses import asdict
from kicadstamp.config.sexp_format import dict_to_sexp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import Rule, ManualSpoke, ThermalViaArrayConfig, ClonePlacement
from kicadstamp.i18n import _

TEMPLATE_NAME = "cap_pair_standard"
CLONE_TEMPLATE_NAME = "cap_pair_standard_clone"

BANKS = {
    "+3V3_VCCIO": [
        ('17', 0.0, -0.5, 90.0), ('26', 0.0, -2.4, 90.0),
        ('40', 1.3, 0.0, 180.0), ('47', 2.5, 0.0, 180.0),
        ('56', 0.3, 0.0, 180.0), ('62', 2.0, 0.0, 180.0),
        ('81', 0.0, -1.0, 270.0), ('93', 0.0, 0.0, 270.0),
        ('117', -2.0, 0.0, 0.0), ('122', -1.8, 0.0, 0.0),
        ('130', 0.0, 0.0, 0.0), ('139', 0.0, 0.0, 0.0),
    ],
    "+1V2_VCCINT": [
        ('5', 0.0, 0.0, 90.0), ('29', 0.0, -1.5, 90.0),
        ('45', 1.0, 0.0, 180.0), ('61', -0.0, 0.0, 180.0),
        ('78', 0.0, 0.0, 270.0), ('102', 0.0, 1.0, 270.0),
        ('116', -0.3, 0.0, 0.0), ('134', -0.3, 0.0, 0.0),
    ],
    "+1V2_VCCD_PLL": [
        ('37', 0.5, 0.0, 180.0), ('109', -1.5, 0.0, 0.0),
    ],
    "+2V5_VCCA": [
        ('35', 0.0, -2.0, 90.0), ('107', 0.0, 1.0, 270.0),
    ],
}

# net -> cluster name (top level)
# One cluster per physical FPGA instance (not per power rail — rails are
# already separated by rule.net/params.power_net; Cluster is only needed to
# distinguish THIS specific FPGA from another identical one on the same board/
# project, if any). Set on the board as FPGA_PWR_BANK.
# build_cluster_table() refines it to FPGA_PWR_BANK/<pad> — in case physical
# proximity to the anchor is insufficient to resolve a spoke
# (see clone_role_resolver.py narrowing cascade).
CLUSTER_MAP = {
    "+3V3_VCCIO": "FPGA_PWR_BANK",
    "+1V2_VCCINT": "FPGA_PWR_BANK",
    "+1V2_VCCD_PLL": "FPGA_PWR_BANK",
    "+2V5_VCCA": "FPGA_PWR_BANK",
}

# --------------------- Anchor for rules/clones (spokes) ---------------------
ANCHOR_REF = "IC1"          # used if USE_ANCHOR_ROLE = False
ANCHOR_ROLE = "FPGA"        # used if USE_ANCHOR_ROLE = True
USE_ANCHOR_ROLE = True      # switch to False to use ANCHOR_REF

# --------------------- Anchor for thermal vias ---------------------
THERMAL_ANCHOR_REF = "IC1"          # used if THERMAL_USE_ANCHOR_ROLE = False
THERMAL_ANCHOR_ROLE = "FPGA"        # used if THERMAL_USE_ANCHOR_ROLE = True
THERMAL_USE_ANCHOR_ROLE = True      # switch to False to use THERMAL_ANCHOR_REF


def build_rules():
    rules = []
    for net, spokes_table in BANKS.items():
        cluster = CLUSTER_MAP.get(net)
        spokes = [
            ManualSpoke(
                pad=pad,
                template=TEMPLATE_NAME,
                shift_x_mm=sx,
                shift_y_mm=sy,
                rotation_deg=rot,
                cluster=cluster,
            )
            for pad, sx, sy, rot in spokes_table
        ]
        if USE_ANCHOR_ROLE:
            rules.append(Rule(net=net, spokes=spokes, anchor_role=ANCHOR_ROLE))
        else:
            rules.append(Rule(net=net, spokes=spokes, anchor_ref=ANCHOR_REF))
    return rules


def build_cluster_table():
    """[(net, pad, cluster_name), ...] in BANKS order (matches rules/clone_placements
    order, for easy cross‑check). cluster_name = FPGA_PWR_BANK/<pad>, same as
    set in anchor_cluster in build_clone_placements()."""
    rows = []
    for net, spokes_table in BANKS.items():
        cluster_prefix = CLUSTER_MAP[net]
        for pad, *_rest in spokes_table:
            rows.append((net, pad, f"{cluster_prefix}/{pad}"))
    return rows


def build_clone_placements():
    """
    clone_placements equivalent of build_rules() — same geometry
    (pad -> anchor_pad, shift -> origin, rotation), but for ClonePlacement,
    which can clone tracks (Rule/ManualSpoke cannot).

    anchor_cluster is ALWAYS set (FPGA_PWR_BANK/<pad>, from
    build_cluster_table()) — this is safe even if Cluster is not yet assigned
    in Eeschema: if no candidate matches such a Cluster, the resolver simply
    skips that narrowing step (see clone_role_resolver._narrow_ambiguous_candidates
    — narrow remains unchanged if by_cluster is empty) and falls back to the next
    (selection, then physical proximity). So the generated file can be run with
    --dry-run BEFORE marking Cluster in the schematic — if proximity is enough
    for all spokes, tagging is not needed at all; only those pads that fail with
    ambiguity in the dry‑run need explicit tagging.

    Requires cap_pair_standard_clone (not cap_pair_standard!) in
    profiles/templates/3ch-awg-tia.sexp — there common vias/tracks are on
    "{power_net}", resolved via params below.
    """
    placements = []
    for net, spokes_table in BANKS.items():
        cluster_prefix = CLUSTER_MAP[net]
        net_safe = net.lstrip('+')
        for pad, sx, sy, rot in spokes_table:
            kwargs = dict(
                name=f"fpga_{net_safe}_{pad}",
                template=CLONE_TEMPLATE_NAME,
                origin_x_mm=sx,
                origin_y_mm=sy,
                rotation_deg=rot,
                anchor_pad=pad,
                params={"power_net": net},
                anchor_cluster=f"{cluster_prefix}/{pad}",
            )
            if USE_ANCHOR_ROLE:
                kwargs["anchor_role"] = ANCHOR_ROLE
            else:
                kwargs["anchor_ref"] = ANCHOR_REF
            placements.append(ClonePlacement(**kwargs))
    return placements


def write_sexp(data, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(dict_to_sexp(data))
    print(_("Generated: {path}").format(path=path))


def main():
    # --- 1. rules‑based config (as before) ---
    thermal_via = ThermalViaArrayConfig(
        enabled=True,
        anchor_ref=THERMAL_ANCHOR_REF if not THERMAL_USE_ANCHOR_ROLE else None,
        anchor_role=THERMAL_ANCHOR_ROLE if THERMAL_USE_ANCHOR_ROLE else None,
        pad='145',
        net='GND',
        rows=4, cols=4, margin_mm=0.5, pattern='grid',
        drill_mm=0.3, diameter_mm=0.5,
    )

    # This inline templates: is the old approximate one (manually tuned
    # coordinates, before a real extract from the live board existed). The real,
    # board‑extracted template lives in profiles/templates/3ch-awg-tia.sexp
    # (cap_pair_standard / cap_pair_standard_clone) and is used through
    # templates_file in profiles/3ch-awg-tia.sexp — this inline one is kept only
    # for backward compatibility with profiles/generated/10CL006YE144C8G.sexp,
    # if anyone still runs it separately.
    templates = {
        "cap_pair_standard": {
            "vias": [
                {"offset_along_mm": 0.0, "offset_across_mm": 1.5,
                 "drill_mm": 0.3, "diameter_mm": 0.6},
                {"offset_along_mm": -1.0, "offset_across_mm": -5.2,
                 "drill_mm": 0.3, "diameter_mm": 0.6},
            ],
            "components": [
                {"role": "C_OUT_BYPASS", "offset_along_mm": -1.0, "offset_across_mm": 1.0,
                 "angle_deg": 270.0,
                 "vias": [{"offset_along_mm": -1.0, "offset_across_mm": 2.7,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}]},
                {"role": "C_OUT_BULK", "offset_along_mm": -1.0, "offset_across_mm": -2.0,
                 "angle_deg": 90.0,
                 "vias": [{"offset_along_mm": -1.0, "offset_across_mm": -4.2,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}]},
            ],
        }
    }

    rules_config = {
        "layer": "B.Cu",
        "thermal_via_arrays": [asdict(thermal_via)],
        "templates": templates,
        "rules": [asdict(r) for r in build_rules()],
    }
    write_sexp(rules_config, "profiles/generated/10CL006YE144C8G.sexp")

    # --- 2. clone_placements‑based config (new) ---
    clone_config = {"clone_placements": [asdict(c) for c in build_clone_placements()]}
    write_sexp(clone_config, "profiles/generated/10CL006YE144C8G.clone_placements.sexp")

    # --- 3. pad -> cluster table (merged from generate_cluster_table.py) ---
    rows = build_cluster_table()
    table_path = Path("profiles/generated/10CL006YE144C8G.cluster_table.md")
    with open(table_path, "w", encoding="utf-8") as f:
        f.write("| net | pad | cluster |\n")
        f.write("|---|---|---|\n")
        for net, pad, cluster in rows:
            f.write(f"| {net} | {pad} | {cluster} |\n")
    print(_("Generated: {path}").format(path=table_path))
    print(_("Total spokes: {count}").format(count=len(rows)))


if __name__ == "__main__":
    main()