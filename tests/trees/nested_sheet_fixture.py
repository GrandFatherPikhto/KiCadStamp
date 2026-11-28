# tests/trees/nested_sheet_fixture.py
"""The `Ch_L` shape of plan_2026_11_27_tree_instances_nested_sheet_paths, synthetic.

Built from the live structure the plan records (Denis' PreAmp, 27.11) so the cells
do not depend on the git-ignored `profiles/` tree or on an absolute path:

  * the template is POINT-anchored (it declares its own place), with SEVEN
    top-level nodes — ONE placement plus SIX neighbouring `net_trace` records.
    Requiring exactly one top-level node made such a template unable to yield its
    own sheet (Н1);
  * eight placement records on THREE sheets: `ChannelL`, `ChannelL/ArmP`,
    `ChannelL/ArmN` — the two arms of one channel, the shape that a flat
    substitution collapses into one leaf (Н2);
  * four mounts hang off the OPAMP pads; ONE of them anchors on a NESTED child
    sheet (`ChannelL/ArmP`) and one is an EXTERNAL reference (`Power`), so the
    mount rule's "inside vs outside" is exercised both ways (Н3);
  * the six `net_traces:` records carry the real `anchor_sheet` values from the
    plan's table (`ChannelL/ArmN`, `ChannelL/ArmP`, `ChannelL`).

Everything is plain data — the cells feed it to `_expand_template` in memory and
write a temporary `.sexp` when a real load is wanted.
"""
from __future__ import annotations

import copy

SOURCE = "ChannelL"
TARGET = "ChannelR"

#: The eight placement records: (ref, sheet, cluster, role).
PLACEMENTS = [
    ("opamp_channel_channell", SOURCE, "OPAMP_CHANNEL", "OPAMP"),
    ("arm_in_channell_armp", f"{SOURCE}/ArmP", "ARM_IN", "R_LOAD_1"),
    ("arm_in_channell_armn", f"{SOURCE}/ArmN", "ARM_IN", "R_LOAD_1"),
    ("arm_fb_channell_armp", f"{SOURCE}/ArmP", "ARM_FB", "R_EQ"),
    ("arm_fb_channell_armn", f"{SOURCE}/ArmN", "ARM_FB", "R_EQ"),
    ("gain_bridge_channell", SOURCE, "GAIN_BRIDGE", "C_BLOCK_N"),
    ("spoke_arm_n12v_channell", SOURCE, "SPOKE_N12V", "C_SPOKE_N"),
    ("spoke_arm_p12v_channell", SOURCE, "SPOKE_P12V", "C_SPOKE_P"),
]

#: The six records: (name, anchor_role, anchor_sheet, anchor_cluster) — the
#: plan's own table of the live anchors.
RECORDS = [
    ("in_ac__arm_in__opamp_channel", "R_LOAD_1", f"{SOURCE}/ArmN", "ARM_IN"),
    ("x_p__arm_fb__opamp_channel", "R_EQ", f"{SOURCE}/ArmP", "ARM_FB"),
    ("x_n__arm_fb__opamp_channel", "R_EQ", f"{SOURCE}/ArmN", "ARM_FB"),
    ("fb_n__arm_fb__gain_bridge", "C_BLOCK_N", SOURCE, "GAIN_BRIDGE"),
    ("in_ac__arm_in__opamp_channel_2", "R_LOAD_1", f"{SOURCE}/ArmP", "ARM_IN"),
    ("fb_p__arm_fb__gain_bridge", "C_EQ_3", f"{SOURCE}/ArmP", "ARM_FB"),
]


def _cell_name(role: str) -> str:
    """The cell that places exactly ONE role — a mount anchor must resolve to a
    single placement under the tree."""
    return f"c_{role.lower()}"


def _placement(ref: str, xy=(0.0, 0.0), children=None) -> dict:
    # `kind` is explicit: the 2->3 converter refuses a node whose kind it cannot
    # read (it could not tell which section the ref points into).
    node = {"ref": ref, "kind": "placement", "xy": list(xy), "rotation": 0.0}
    if children:
        node["children"] = children
    return node


def _mount(ref: str, sheet: str, pad: str, children=None,
           role: str = "OPAMP") -> dict:
    node = {"ref": ref, "kind": "mount",
            "anchor": {"role": role, "sheet": sheet, "pad": pad}}
    if children:
        node["children"] = children
    return node


def _component(ref: str, sheet: str, role: str = "R_EQ") -> dict:
    return {"ref": ref, "kind": "component",
            "anchor": {"role": role, "sheet": sheet}}


def nested_template_graph() -> dict:
    """A fresh copy of the synthetic Ch_L graph (entities/trees/net_traces)."""
    # ONE CELL PER ROLE: a mount anchor that resolves a role must find exactly
    # one placement under the tree, so a shared cell placing every role would
    # make every mount ambiguous (the loader's drift guard refuses that).
    entities = [
        # `role` is deliberately NOT an Entity field: a part's Role lives on the
        # board, the record names the cell/cluster and the sheet.
        {"name": ref, "cell": _cell_name(role), "cluster": cluster,
         "sheet": sheet}
        for ref, sheet, cluster, role in PLACEMENTS
    ]
    records = [
        {"name": name, "net": f"/{SOURCE}/{name}", "anchor_role": role,
         "anchor_sheet": sheet, "anchor_cluster": cluster,
         "anchor_pad": "1", "pads": [f"{role}.1"], "tracks": [], "vias": []}
        for name, role, sheet, cluster in RECORDS
    ]
    nodes = [
        _placement("opamp_channel_channell", children=[
            _mount("in_ac_3", SOURCE, "3",
                   children=[_placement("arm_in_channell_armp", (1.0, 2.0))]),
            _mount("in_ac_5", SOURCE, "5",
                   children=[_placement("arm_in_channell_armn", (3.0, 4.0))]),
            _mount("x_1", SOURCE, "1",
                   children=[_placement("arm_fb_channell_armp", (5.0, 6.0))]),
            # a mount on a NESTED child sheet — inside the template, and the case
            # the exact-equality rule used to call "external" (Н3). Its role is
            # placed ON that child sheet (R_EQ by the arm there).
            _mount("x_7", f"{SOURCE}/ArmP", "7", role="R_EQ",
                   children=[_placement("arm_fb_channell_armn", (7.0, 8.0))]),
            # a board-wide reference: NOT a child of the template's sheet
            {"ref": "power_in", "kind": "mount",
             "anchor": {"role": "POWER", "sheet": "Power", "pad": "1"}},
            _placement("gain_bridge_channell", (9.0, 10.0)),
            _placement("spoke_arm_n12v_channell", (11.0, 12.0)),
            _placement("spoke_arm_p12v_channell", (13.0, 14.0)),
            # a component on a child sheet (follows) and one outside it (kept)
            _component("ext_arm", f"{SOURCE}/ArmN"),
            _component("ext_power", "Power"),
        ]),
    ]
    nodes += [{"ref": name, "kind": "net_trace"} for name, *_rest in RECORDS]
    tree = {"name": "Ch_L",
            "anchor": {"point": "Origin", "shift": [63.91, -40.0]},
            "nodes": nodes}
    # `Origin` is a points: record — the 2->3 lift resolves a point-anchored
    # declaration against it.
    cells = {_cell_name(role): {"components": [{"role": role}]}
             for _ref, _sheet, _cluster, role in PLACEMENTS}
    return {"cells": cells,
            "points": {"Origin": {"xy": [0.0, 0.0]}},
            "entities": entities,
            "trees": [tree], "net_traces": copy.deepcopy(records)}


def declare(name: str = "Ch_R", sheet: str = TARGET,
            shift=(63.91, 40.0)) -> dict:
    """The declaration row `Instances…` would write for the case: no `root`, its
    OWN point anchor — exactly Denis' working case (Р4)."""
    return {"template": "Ch_L", "name": name, "sheet": sheet,
            "anchor": {"point": "Origin", "shift": list(shift)}}


def by_name(graph: dict) -> tuple[dict, dict]:
    """(entities by name, net_traces by identity) — the raw-dict indexes
    `_expand_template` takes."""
    return ({e["name"]: e for e in graph["entities"]},
            {r["name"]: r for r in graph["net_traces"]})
