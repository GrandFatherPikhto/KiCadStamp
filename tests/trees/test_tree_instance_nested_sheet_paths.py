# tests/trees/test_tree_instance_nested_sheet_paths.py
"""Cells of plan_2026_11_27_tree_instances_nested_sheet_paths (Т1–Т3).

The case: `Ch_L` is a POINT-anchored template with one placement and six
neighbouring `net_trace` records at the top; its eight placement records live on
`ChannelL`, `ChannelL/ArmP`, `ChannelL/ArmN`. Creating `Ch_R` (sheet `ChannelR`,
its own place) must keep the tree's composition AND the two arms' addresses.

What is pinned here, property by property:

  * Н1 — the template's own sheet (S) is read off its single top-level placement
    even when copper sits beside it (before: "the template's own sheet cannot be
    derived" — the live FATAL of 27.11);
  * Н2 — a copied Entity's sheet keeps its path under the instance's sheet:
    `ChannelL -> ChannelR`, `ChannelL/ArmP -> ChannelR/ArmP`, `ChannelL/ArmN ->
    ChannelR/ArmN`; the same for all six `net_trace.anchor_sheet` values (Н3);
  * Н3 — a mount/component on a NESTED child sheet follows (and its role is
    checked as INSIDE by `outward_problem`), an address outside the template
    stays verbatim;
  * Р2's rule itself — segment boundaries, `/`, multi-segment source and target,
    external addresses, the flat/no-source fallback — in `follows_source` /
    `rebase_sheet_path` / `entity_sheet` / `trace_anchor_sheet` / `role_address`;
  * Р3 — the input dict is never mutated, a second expansion gives the same
    result, and the template tree keeps its own addresses.

The graph is `tests/trees/nested_sheet_fixture.py` (synthetic, no `profiles/`).
"""
import copy

import pytest

from kicadstamp.config import load_config, load_config_lenient
from kicadstamp.config.sheet_instance_policy import (
    entity_sheet, follows_source, rebase_sheet_path, role_address,
    trace_anchor_sheet)
from kicadstamp.config.tree_instance_checks import derive_old_sheet
from kicadstamp.config.tree_instances import _expand_template, expand_tree_instances
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.internode_nodes import RoleResolver

from tests.trees.nested_sheet_fixture import (
    PLACEMENTS, RECORDS, SOURCE, TARGET, by_name, declare,
    nested_template_graph)


# ── Р2: the rule itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("address,source,expected", [
    ("ChannelL", "ChannelL", True),
    ("ChannelL/ArmP", "ChannelL", True),
    ("ChannelL/ArmN/Stage", "ChannelL", True),
    ("ChannelLeft/ArmP", "ChannelL", False),      # a longer NAME, not a child
    ("ChannelL_ArmP", "ChannelL", False),         # no separator, no child
    ("Power", "ChannelL", False),
    ("/", "ChannelL", False),
    ("ArmP", "ChannelL", False),
    (None, "ChannelL", False),
    ("/", "/", True),                             # the root is only itself
    ("/A", "/", False),                           # ... and has no children here
    ("ChannelL/ArmP", None, False),               # nothing to compare against
])
def test_follows_source_tests_segments_not_text(address, source, expected):
    assert follows_source(address, source) is expected


@pytest.mark.parametrize("address,source,target,expected", [
    ("ChannelL", "ChannelL", "ChannelR", "ChannelR"),
    ("ChannelL/ArmP", "ChannelL", "ChannelR", "ChannelR/ArmP"),
    ("ChannelL/ArmN/Stage", "ChannelL", "ChannelR", "ChannelR/ArmN/Stage"),
    ("ChannelLeft/ArmP", "ChannelL", "ChannelR", None),
    ("Power", "ChannelL", "ChannelR", None),
    ("/", "ChannelL", "ChannelR", None),
    # multi-segment source AND target keep the same law
    ("A/B/C", "A/B", "X/Y", "X/Y/C"),
    ("A/B", "A/B", "X/Y", "X/Y"),
    ("A/BB", "A/B", "X/Y", None),
    # the root as a TARGET never produces a doubled separator
    ("ChannelL/ArmP", "ChannelL", "/", "ArmP"),
    ("ChannelL", "ChannelL", "/", "/"),
    # no instance sheet -> no rewrite, the caller falls back on its own
    ("ChannelL/ArmP", "ChannelL", "", None),
])
def test_rebase_keeps_segment_boundaries(address, source, target, expected):
    assert rebase_sheet_path(address, source, target) == expected


# ── Н1: where the template's own sheet comes from ─────────────────────────

def test_the_single_top_placement_gives_the_source_beside_copper(caplog):
    graph = nested_template_graph()
    entities, _ = by_name(graph)
    with caplog.at_level("INFO"):
        sheet = derive_old_sheet(graph["trees"][0], entities, "Ch_R")
    assert sheet == SOURCE
    assert any("single top-level placement node" in r.message
               for r in caplog.records)


def test_two_top_placements_are_not_a_source():
    """The narrow rule never guesses: a second top-level placement makes the
    template span records, so Р1 does not apply and Н3 decides (None here)."""
    graph = nested_template_graph()
    nodes = graph["trees"][0]["nodes"]
    nodes.append({"ref": "arm_in_channell_armp", "xy": [0.0, 0.0]})
    entities, _ = by_name(graph)
    assert derive_old_sheet(graph["trees"][0], entities, "Ch_R") is None


def test_a_placement_outside_the_source_is_not_a_source():
    """Every placement record must sit on the source or below it — a sister
    sheet means the template is not a subtree of that sheet."""
    graph = nested_template_graph()
    entities, _ = by_name(graph)
    entities["gain_bridge_channell"] = dict(entities["gain_bridge_channell"],
                                            sheet="Other")
    assert derive_old_sheet(graph["trees"][0], entities, "Ch_R") is None


# ── Н1+Н2+Н3: the expansion of the live case ──────────────────────────────

def _expand(graph=None, name="Ch_R", sheet=TARGET):
    graph = graph if graph is not None else nested_template_graph()
    entities, records = by_name(graph)
    return _expand_template(graph["trees"][0], "Ch_L", name, sheet,
                            entities, records, decl_anchor=declare()["anchor"])


def test_the_whole_case_expands_without_a_root():
    """Н1: eight Entity copies and six net_trace copies, no `root:` needed —
    before this change the expansion died on "the template's own sheet cannot
    be derived"."""
    _tree, entities, traces = _expand()
    assert len(entities) == 8
    assert len(traces) == 6


def test_every_entity_keeps_its_place_in_the_channel():
    """Н2: the arm's address follows the instance, at ANY depth."""
    _tree, entities, _traces = _expand()
    sheets = {e["name"]: e["sheet"] for e in entities}
    expected = {
        f"{ref}__Ch_R": sheet.replace(SOURCE, TARGET, 1)
        for ref, sheet, _cluster, _role in PLACEMENTS
    }
    assert sheets == expected
    assert sheets["arm_in_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_in_channell_armn__Ch_R"] == "ChannelR/ArmN"
    assert sheets["arm_fb_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_fb_channell_armn__Ch_R"] == "ChannelR/ArmN"


def test_the_six_anchors_keep_their_arm():
    """Н3: every copied record's `anchor_sheet` follows the instance — the LIVE
    search of its anchor role is what keeps the two arms' copper apart."""
    _tree, _entities, traces = _expand()
    got = {t["name"].removesuffix("__Ch_R"): t["anchor_sheet"] for t in traces}
    expected = {name: sheet.replace(SOURCE, TARGET, 1)
                for name, _role, sheet, _cluster in RECORDS}
    assert got == expected
    assert sorted(got.values()) == [
        "ChannelR", "ChannelR/ArmN", "ChannelR/ArmN",
        "ChannelR/ArmP", "ChannelR/ArmP", "ChannelR/ArmP"]


def test_a_mount_on_a_child_sheet_follows_and_one_outside_stays():
    """Н3: `x_7` anchors on `ChannelL/ArmP` (inside — follows); `power_in`
    anchors on `Power` (board-wide — kept verbatim)."""
    tree, _entities, _traces = _expand()
    mounts = {n["ref"]: n["anchor"]["sheet"] for n in tree["nodes"][0]["children"]
              if n.get("kind") == "mount"}
    assert mounts["x_7"] == "ChannelR/ArmP"
    assert mounts["power_in"] == "Power"


def test_a_component_on_a_child_sheet_follows_and_one_outside_stays():
    tree, _entities, _traces = _expand()
    parts = {n["ref"]: n["anchor"]["sheet"] for n in tree["nodes"][0]["children"]
             if n.get("kind") == "component"}
    assert parts["ext_arm"] == "ChannelR/ArmN"
    assert parts["ext_power"] == "Power"


def test_the_template_itself_is_not_rewritten_and_the_input_is_not_mutated():
    """Р3: the whole input graph is byte-identical after the expansion, and the
    second expansion is identical to the first — no accumulating renames."""
    graph = nested_template_graph()
    before = copy.deepcopy(graph)
    first_tree, first_entities, first_traces = _expand(graph)
    assert graph == before
    second_tree, second_entities, second_traces = _expand(graph)
    assert (second_tree, second_entities, second_traces) == \
        (first_tree, first_entities, first_traces)


# ── the fallbacks: no source, and a record outside the template ───────────

def test_without_a_source_the_record_takes_the_instance_sheet_silently():
    """A template that never says where its sheet is keeps the historical flat
    behaviour (and says nothing — there is nothing to warn about)."""
    assert entity_sheet("ChannelL/ArmP", None, TARGET, ref="r",
                        instance_name="Ch_R") == TARGET
    assert trace_anchor_sheet("ChannelL/ArmP", None, TARGET, identity="n",
                              instance_name="Ch_R") == TARGET
    assert role_address("ChannelL/ArmP", None, TARGET, ref="m",
                        instance_name="Ch_R") == "ChannelL/ArmP"


def test_a_record_outside_the_template_gets_the_instance_sheet_with_a_line(caplog):
    with caplog.at_level("WARNING"):
        sheet = entity_sheet("Other/ArmP", SOURCE, TARGET, ref="r__Ch_R",
                             instance_name="Ch_R")
    assert sheet == TARGET
    assert any("not the template's own" in r.message for r in caplog.records)


def test_an_entity_without_a_sheet_keeps_the_flat_rule_silently(caplog):
    with caplog.at_level("WARNING"):
        assert entity_sheet(None, SOURCE, TARGET, ref="r", instance_name="Ch_R") \
            == TARGET
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# ── Р4/Т3: a real load, and the lookup that tells the two arms apart ──────

def _write_constrained(tmp_path, graph, declarations):
    data = dict(graph)
    data["tree_instances"] = declarations
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    return path


def test_a_real_load_creates_the_second_channel(tmp_path):
    """Save/reopen through the LOADER: the declaration survives, the instance is
    expanded with the nested addresses, and the source keeps its own."""
    path = _write_constrained(tmp_path, nested_template_graph(), [declare()])
    cfg, _ctx = load_config(str(path))

    by_sheet = {}
    for entity in cfg.entities:
        if entity.name.endswith("__Ch_R"):
            by_sheet.setdefault(entity.sheet, []).append(entity.name)
    assert len(by_sheet["ChannelR"]) == 4
    assert len(by_sheet["ChannelR/ArmP"]) == 2
    assert len(by_sheet["ChannelR/ArmN"]) == 2
    left = {e.sheet for e in cfg.entities if not e.name.endswith("__Ch_R")}
    assert left == {SOURCE, f"{SOURCE}/ArmP", f"{SOURCE}/ArmN"}
    assert len([t for t in cfg.net_traces
                if (t.name or "").endswith("__Ch_R")]) == 6


def test_the_lookup_tells_the_two_arms_apart_and_never_the_left_one(tmp_path):
    """The instance address is what makes the RIGHT arm resolve: with the flat
    sheet alone the two arms collapse, and the template's own components must not
    be captured at all."""
    from kicadstamp.trees import find_role_placement_matches

    path = _write_constrained(tmp_path, nested_template_graph(), [declare()])
    cfg, _ctx = load_config(str(path))
    tree = next(t for t in cfg.trees if t.name == "Ch_R")

    right = find_role_placement_matches(cfg, tree, "R_LOAD_1",
                                        sheet="ChannelR/ArmP")
    assert [m.entity.name for m in right] == ["arm_in_channell_armp__Ch_R"]
    right_n = find_role_placement_matches(cfg, tree, "R_EQ",
                                          sheet="ChannelR/ArmN")
    assert [m.entity.name for m in right_n] == ["arm_fb_channell_armn__Ch_R"]
    # the flat instance sheet names BOTH arms' records — that is the ambiguity
    # the nested address removes, and the template's own parts are not in it
    assert find_role_placement_matches(cfg, tree, "R_LOAD_1",
                                       sheet="ChannelR") == []
    assert find_role_placement_matches(cfg, tree, "R_LOAD_1",
                                       sheet=SOURCE) == []


def test_expand_tree_instances_is_the_same_expansion(tmp_path):
    """The loader and `expand_tree_instances` are ONE expander: the graph it
    returns carries the same addresses the load produced."""
    graph = nested_template_graph()
    graph["tree_instances"] = [declare()]
    out = expand_tree_instances(copy.deepcopy(graph))
    sheets = {e["name"]: e["sheet"] for e in out["entities"]
              if e["name"].endswith("__Ch_R")}
    assert sheets["arm_in_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_in_channell_armn__Ch_R"] == "ChannelR/ArmN"


# ── Т03 (full): role, cluster, pad and net of the six copies ──────────────

def test_the_six_copies_keep_role_cluster_pad_and_net():
    """Only the ADDRESS moves: the anchor role/cluster/pad and the literal net
    are copied verbatim (the same copper, on the instance's sheet)."""
    _tree, _entities, traces = _expand()
    source = {name: (role, cluster) for name, role, _sheet, cluster in RECORDS}
    for trace in traces:
        name = trace["name"].removesuffix("__Ch_R")
        role, cluster = source[name]
        assert (trace["anchor_role"], trace["anchor_cluster"]) == (role, cluster)
        assert trace["anchor_pad"] == "1"
        assert trace["pads"] == [f"{role}.1"]
        assert trace["net"] == f"/{SOURCE}/{name}"


# ── Т04: BOTH address kinds follow a nested child sheet ───────────────────

@pytest.mark.parametrize("ref", ["x_7", "ext_arm"])
def test_nested_role_addresses_follow_the_channel(ref):
    """`kind` is irrelevant to the rule: a mount AND a component anchored on
    `ChannelL/ArmP` both come out as `ChannelR/ArmP`; the template's own record
    keeps its left address (the input is never rewritten)."""
    graph = nested_template_graph()
    children = graph["trees"][0]["nodes"][0]["children"]
    for node in children:
        if node.get("ref") == ref:
            node["anchor"]["sheet"] = f"{SOURCE}/ArmP"
    tree, _entities, _traces = _expand(graph)
    copied = {n["ref"]: n["anchor"]["sheet"]
              for n in tree["nodes"][0]["children"] if n.get("ref") == ref}
    assert copied == {ref: f"{TARGET}/ArmP"}
    own = next(n for n in children if n.get("ref") == ref)
    assert own["anchor"]["sheet"] == f"{SOURCE}/ArmP"


# ── Т05: external and look-alike addresses stay external ──────────────────

@pytest.mark.parametrize("address", ["Power", f"{SOURCE}2/ArmP"])
@pytest.mark.parametrize("kind,ref", [("mount", "x_7"), ("component", "ext_arm"),
                                      ("net_trace", "in_ac__arm_in__opamp_channel")])
def test_external_and_similar_prefix_addresses_stay_external(kind, ref, address):
    """A board-wide anchor and a name that only LOOKS like a prefix are kept —
    never re-pointed at `ChannelR`, never treated as `ChannelL` + `2/ArmP`. ALL
    THREE address kinds are driven through the EXPANSION (the Т05 matrix)."""
    graph = nested_template_graph()
    if kind == "net_trace":
        record = next(r for r in graph["net_traces"] if r["name"] == ref)
        record["anchor_sheet"] = address
        _tree, _entities, traces = _expand(graph)
        got = {t["name"].removesuffix("__Ch_R"): t["anchor_sheet"]
               for t in traces}
        assert got[ref] == address
        return
    for node in graph["trees"][0]["nodes"][0]["children"]:
        if node.get("ref") == ref:
            node["anchor"]["sheet"] = address
    tree, _entities, _traces = _expand(graph)
    copied = {n["ref"]: n["anchor"]["sheet"]
              for n in tree["nodes"][0]["children"] if n.get("ref") == ref}
    assert copied == {ref: address}


def test_a_record_on_an_external_sheet_keeps_its_anchor(caplog):
    """The old code flattened a `net_traces:` anchor onto the instance sheet; an
    anchor that belongs to the BOARD (here `Power`) is not the instance's to move —
    it stays verbatim, with a line saying so."""
    graph = nested_template_graph()
    record = graph["net_traces"][0]
    record["anchor_sheet"] = "Power"
    with caplog.at_level("WARNING"):
        _tree, _entities, traces = _expand(graph)
    got = {t["name"].removesuffix("__Ch_R"): t["anchor_sheet"] for t in traces}
    assert got[record["name"]] == "Power"
    assert any("kept verbatim" in r.message for r in caplog.records)


# ── Т06: a multi-segment source and target keep the deeper suffix ─────────

def test_multisegment_source_and_target_keep_deeper_suffix():
    """S = Rack/ChannelL, D = Rack2/ChannelR, address S/ArmP/Stage: exactly
    Rack2/ChannelR/ArmP/Stage — no `//`, no lost `Stage`, no last-name swap."""
    graph = {
        "cells": {"c_opamp": {"components": [{"role": "OPAMP"}]}},
        "points": {"Origin": {"xy": [0.0, 0.0]}},
        "entities": [
            {"name": "board", "cell": "c_opamp", "cluster": "OPAMP_CHANNEL",
             "sheet": "Rack/ChannelL"},
            {"name": "arm", "cell": "c_opamp", "cluster": "ARM_IN",
             "sheet": "Rack/ChannelL/ArmP/Stage"},
        ],
        "trees": [{
            "name": "Ch_L",
            "anchor": {"point": "Origin", "shift": [0.0, 0.0]},
            "nodes": [{
                "ref": "board", "kind": "placement", "xy": [0.0, 0.0],
                "rotation": 0.0,
                "children": [{"ref": "arm", "kind": "placement",
                              "xy": [1.0, 1.0]}],
            }],
        }],
        "net_traces": [],
    }
    entities, records = by_name(graph)
    _tree, copied, _traces = _expand_template(
        graph["trees"][0], "Ch_L", "Ch_R", "Rack2/ChannelR", entities, records,
        decl_anchor={"point": "Origin", "shift": [0.0, 0.0]})
    sheets = {e["name"]: e["sheet"] for e in copied}
    assert sheets == {"board__Ch_R": "Rack2/ChannelR",
                      "arm__Ch_R": "Rack2/ChannelR/ArmP/Stage"}
    assert not [s for s in sheets.values() if "//" in s]


# ── Т07: two unrelated placement roots refuse atomically ──────────────────

def _two_root_graph() -> dict:
    graph = nested_template_graph()
    graph["cells"]["c_other"] = {"components": [{"role": "OTHER_PART"}]}
    graph["entities"].append({"name": "other_root", "cell": "c_other",
                              "cluster": "OTHER", "sheet": "Other"})
    graph["trees"][0]["nodes"].insert(0, {
        "ref": "other_root", "kind": "placement", "xy": [0.0, 0.0],
        "rotation": 0.0,
        "children": [{"ref": "other_in", "kind": "mount",
                      "anchor": {"role": "OTHER_PART", "sheet": "Other",
                                 "pad": "1"}}],
    })
    return graph


def test_two_unrelated_placement_roots_refuse_atomically(tmp_path):
    """No source is GUESSED: strict names the instance and the reason, lenient
    records the same refusal and adds not one record; the input stays as it was."""
    graph = _two_root_graph()
    before = copy.deepcopy(graph)
    path = _write_constrained(tmp_path, graph, [declare()])

    with pytest.raises(ValidationError, match="own sheet cannot be derived") as exc:
        load_config(str(path))
    # Р1/M-6: the refusal NAMES what it saw instead of letting the user guess.
    for candidate in ("ChannelL/ArmP", "ChannelL/ArmN", "Other"):
        assert candidate in str(exc.value), candidate
    cfg, _ctx, failures = load_config_lenient(str(path))
    assert [(f["name"], "own sheet cannot be derived" in f["error"])
            for f in failures] == [("Ch_R", True)]
    assert "Ch_R" not in {t.name for t in cfg.trees}
    assert not [e.name for e in cfg.entities if e.name.endswith("__Ch_R")]
    assert graph == before


# ── Т11: the endpoint resolver never picks the left arm ───────────────────

#: (channel, arm) -> the LEFT ref of that arm's R_LOAD_1 / R_EQ.
_ARM_REFS = {("chL", "armP"): "R101", ("chL", "armN"): "R102",
             ("chR", "armP"): "R201", ("chR", "armN"): "R202"}
_EQ_REFS = {("chL", "armP"): "R111", ("chL", "armN"): "R112",
            ("chR", "armP"): "R211", ("chR", "armN"): "R212"}
#: record -> the ref its (role, sheet, cluster) must resolve to, by the LIVE
#: resolver — the literal table the policy is not allowed to compute.
_EXPECTED = {
    "in_ac__arm_in__opamp_channel": "R202",
    "x_p__arm_fb__opamp_channel": "R211",
    "x_n__arm_fb__opamp_channel": "R212",
    "fb_n__arm_fb__gain_bridge": "R300",
    "in_ac__arm_in__opamp_channel_2": "R201",
    "fb_p__arm_fb__gain_bridge": "R250",
}


def _four_arm_board():
    """Both channels' arms plus one single-instance role per side — the board
    the SAME Role/Cluster pairs live on (Т11's 'наличие строки sheet ничего не
    доказывает')."""
    from tests.net.test_internode_capture_modules import _Board, _fp, _pad

    names = {"chL": "ChannelL", "chR": "ChannelR",
             "armP": "ArmP", "armN": "ArmN"}
    fps = []
    for (ch, arm), ref in _ARM_REFS.items():
        fps.append(_fp(ref, "R_LOAD_1", "ARM_IN", 0.0, float(len(fps)),
                       (ch, arm)))
    for (ch, arm), ref in _EQ_REFS.items():
        fps.append(_fp(ref, "R_EQ", "ARM_FB", 0.0, float(len(fps)), (ch, arm)))
    fps.append(_fp("R300", "C_BLOCK_N", "GAIN_BRIDGE", 0.0, 10.0, ("chR",)))
    fps.append(_fp("R250", "C_EQ_3", "ARM_FB", 0.0, 11.0, ("chR", "armP")))
    pads = {fp.ref: [_pad("1", f"N_{fp.ref}", 0.0, 0.0)] for fp in fps}
    return _Board(fps, pads), names


def test_right_arm_lookup_and_trace_endpoints_never_pick_left(tmp_path):
    """The copied `anchor_sheet` is what the LIVE resolver narrows by: every one
    of the six records comes down to its RIGHT ref, never to the left arm's —
    and the flat `ChannelR` leaves the two arms ambiguous. The ENDPOINTS are then
    resolved by the planner's own reader: each copy's track nets the pad of ITS
    arm, and the same record made flat cannot name a single component at all."""
    import dataclasses

    from kicadstamp.net_trace_planner import net_trace_piece_net, net_trace_refs

    _tree, _entities, traces = _expand()
    board, names = _four_arm_board()
    resolver = RoleResolver(board, names)
    resolved = {}
    for trace in traces:
        record = trace["name"].removesuffix("__Ch_R")
        expected = _EXPECTED[record]
        assert resolver.resolves_to(trace["anchor_role"], trace["anchor_sheet"],
                                    trace["anchor_cluster"], expected), record
        for left in ("R101", "R102", "R111", "R112"):
            assert not resolver.resolves_to(
                trace["anchor_role"], trace["anchor_sheet"],
                trace["anchor_cluster"], left), (record, left)
        resolved[record] = expected
    assert set(resolved.values()) == {"R201", "R202", "R211", "R212",
                                      "R250", "R300"}
    flat = {fp.ref for fp in resolver.narrowed("R_LOAD_1", "ChannelR", "ARM_IN")}
    assert flat == {"R201", "R202"}
    assert not resolver.resolves_to("R_LOAD_1", "ChannelR", "ARM_IN", "R201")

    # ── the ENDPOINTS, through the REAL loaded copies and the planner's reader ──
    cfg, _ctx = load_config(str(_write_constrained(
        tmp_path, nested_template_graph(), [declare()])))
    for record, expected in _EXPECTED.items():
        nt = next(t for t in cfg.net_traces if t.name == f"{record}__Ch_R")
        assert net_trace_piece_net(board, nt, "track", 0, names) == \
            f"N_{expected}", record
        assert net_trace_refs(board, nt, names) == {expected}, record
        if nt.anchor_role in {"R_LOAD_1", "R_EQ"}:     # the role lives on BOTH
            # arms: with the flat sheet it names two components and the reader
            # refuses to pick — the endpoint half of Н3/М03.
            assert net_trace_piece_net(board, dataclasses.replace(
                nt, anchor_sheet="ChannelR"), "track", 0, names) is None, record
        else:                                          # a unique role still resolves
            assert net_trace_piece_net(board, dataclasses.replace(
                nt, anchor_sheet="ChannelR"), "track", 0, names) == f"N_{expected}"


# ── Т13: an explicit Root copies only its subtree ─────────────────────────

def test_explicit_root_still_copies_only_its_subtree():
    """Root is NOT a hidden "copy the whole tree": the six SIBLING records are
    outside the chosen subtree and stay out (Н1 was not fixed by widening Root)."""
    graph = nested_template_graph()
    entities, records = by_name(graph)
    _tree, copied, traces = _expand_template(
        graph["trees"][0], "Ch_L", "Ch_R", TARGET, entities, records,
        decl_anchor=declare()["anchor"],
        decl_root={"ref": "opamp_channel_channell"},
        cells=graph["cells"])
    assert len(copied) == 8
    assert traces == []


# ── Т14: flat instances keep their existing contract (role/self/point) ───

_FLAT_POINT_GRAPH = {
    "cells": {"c_dac": {}},
    "points": {"Origin": {"xy": [0.0, 0.0]}},
    "entities": [{"name": "dac_buf", "cell": "c_dac"},
                 {"name": "pif_avdd", "cell": "c_dac"}],
    "trees": [{"name": "tpl",
               "anchor": {"point": "Origin", "shift": [0.0, 0.0]},
               "nodes": [{"ref": "dac_buf", "kind": "placement",
                          "xy": [1.0, 2.0], "rotation": 90.0,
                          "children": [{"ref": "pif_avdd", "kind": "placement",
                                        "xy": [0.5, 0.0]}]}]}],
    "tree_instances": [
        {"template": "tpl", "name": "ch0", "sheet": "S0",
         "anchor": {"point": "Origin", "shift": [0.0, 0.0]}},
        {"template": "tpl", "name": "ch1", "sheet": "S1",
         "anchor": {"point": "Origin", "shift": [0.0, 0.0]}}],
}


@pytest.mark.parametrize("mode", ["role", "self", "point"])
def test_flat_instances_keep_their_existing_contract(tmp_path, mode):
    """A template whose records say NO sheet keeps the historical flat rule (the
    instance sheet, silently) and the template's own geometry — for ALL THREE
    anchor modes of the old contract (role / self / point)."""
    from tests.trees.test_tree_instances import _template_data, _write

    if mode == "point":
        path = _write(tmp_path, "flat_point.sexp",
                      copy.deepcopy(_FLAT_POINT_GRAPH))
        template_name = "tpl"
    else:
        data = _template_data(
            [{"template": "dac_buf_tpl", "name": "ch0", "sheet": "S0"},
             {"template": "dac_buf_tpl", "name": "ch1", "sheet": "S1"}],
            anchor={"self": {}} if mode == "self" else None)
        path = _write(tmp_path, "flat.sexp", data)
        template_name = "dac_buf_tpl"
    cfg, _ctx = load_config(str(path))

    names = {"dac_buf", "pif_avdd"}
    for instance, sheet in (("ch0", "S0"), ("ch1", "S1")):
        got = {(e.name, e.sheet) for e in cfg.entities
               if e.name in {f"{n}__{instance}" for n in names}}
        assert got == {(f"{n}__{instance}", sheet) for n in names}, mode
    template = next(t for t in cfg.trees if t.name == template_name)
    for instance in ("ch0", "ch1"):
        copy_tree = next(t for t in cfg.trees if t.name == instance)
        assert copy_tree.nodes[0].xy == template.nodes[0].xy == (1.0, 2.0)
        assert copy_tree.nodes[0].rotation == template.nodes[0].rotation == 90.0
        assert copy_tree.nodes[0].children[0].xy == (0.5, 0.0)


# ── M-6: the ambiguous refusal NAMES the sheets it saw ────────────────────

def test_source_candidates_lists_sheets_and_sheetless_records():
    """Р1's refusal names the candidates instead of asking the user to guess —
    the ONE helper behind that line lists every sheet and every sheetless ref."""
    from kicadstamp.config.tree_instance_checks import source_candidates

    graph = nested_template_graph()
    entities, _records = by_name(graph)
    assert source_candidates(graph["trees"][0]["nodes"], entities) == (
        "the template's placement records name: "
        "ChannelL, ChannelL/ArmN, ChannelL/ArmP")
    entities["gain_bridge_channell"] = {"name": "gain_bridge_channell"}
    assert "a record with no sheet: gain_bridge_channell" in \
        source_candidates(graph["trees"][0]["nodes"], entities)
