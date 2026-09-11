"""Tests for the mount-node grammar and the own_anchor -> mount converter
(plan_2026_09_11_tree_mount_nodes, tasks Y.6 / Y.7 / Y.9).

The frozen fixture pair under tests/fixtures/trees_and_overlay/ is a real
profile snapshot (Denis's 3ch-awg-tia-v103 config, reduced to its 5 cells / 5
entities / 1 tree — see the plan's Y.7.1): `config.sexp` is the PRE-migration
grammar, `config.converted.sexp` the converter's output, and
`expected_geometry.json` the geometry fingerprint captured from the BEFORE
config (kicadstamp/diagnostics/tree_mount_baseline.py) against a deterministic
fake board.

The core acceptance claim (Y.7.2) is that the conversion is BIT-EXACT for the
layout AND for every materialized cell component — that is the first test
below.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kipy.board_types import FootprintInstance

from kicadstamp.cell_frame import CellFrame
from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot, load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.tree_mount_convert import convert_trees_dict
from kicadstamp.tree_position import layout_tree_from_base
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, check_mount_anchor_drift
from kicadstamp.utils.units import MM

FIXTURES = Path(__file__).parent / "fixtures" / "trees_and_overlay"

_ORIGIN = Vector2.from_xy(0, 0)
_BASE_ANGLE = 30.0

# (ref, role, cluster, x_mm, y_mm, angle_deg, {pad: (dx_mm, dy_mm)}) — MUST
# stay identical to kicadstamp/diagnostics/tree_mount_baseline.py, or the
# frozen expected_geometry.json stops describing this board.
_FAKE_FOOTPRINTS = (
    ("IC2", "AD_DAC", "DAC_BUF", 220.55, 113.4881, _BASE_ANGLE,
     {"3": (0.0, 0.0), "11": (1.7, 3.2), "18": (4.9, 2.0)}),
    ("U7", "OP_AMP", "DAC_BUF", 232.575, 116.3506, _BASE_ANGLE,
     {"4": (-1.95, -4.225), "8": (0.0, 0.0)}),
)


def _fake_adapter():
    """The deterministic board the baseline was captured with."""
    footprints = []
    for ref, role, cluster, x_mm, y_mm, angle, pads in _FAKE_FOOTPRINTS:
        fp = MagicMock(spec=FootprintInstance)
        fp.ref = ref
        fp._role = role
        fp._cluster = cluster
        fp.position = Vector2.from_xy_mm(x_mm, y_mm)
        fp.angle_deg = angle
        fp._pads = [
            MagicMock(number=num, net_name="X",
                      position=Vector2.from_xy_mm(x_mm + dx, y_mm + dy))
            for num, (dx, dy) in pads.items()
        ]
        footprints.append(fp)

    adapter = MagicMock()
    adapter.get_footprints.return_value = footprints
    adapter.has_field.return_value = True

    def _field(fp, name):
        if name == "Role":
            return getattr(fp, "_role", None)
        if name == CLUSTER_FIELD_NAME:
            return getattr(fp, "_cluster", None)
        return None

    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    return adapter


def _round(value) -> float:
    return round(float(value), 6)


def _snapshot(config_path: str) -> dict:
    """Geometry fingerprint: every tree node's absolute pose under a zero base,
    plus every materialized cell component in world mm."""
    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    adapter = _fake_adapter()

    data: dict = {"trees": {}, "clones": {}}
    for tree in cfg.trees:
        laid = layout_tree_from_base(
            tree, _ORIGIN, 0.0, None,
            adapter=adapter, cfg=cfg, sheet_names=sheet_names)
        data["trees"][tree.name] = {
            ref: [_round(pos.x / MM), _round(pos.y / MM), _round(rot)]
            for ref, (pos, rot) in sorted(laid.items())
        }

    for clone in materialize_entity_placements(adapter, cfg, sheet_names):
        cell = cfg.cells[clone.cell]
        frame = CellFrame(
            placement_origin=Vector2.from_xy(
                int(clone.xy[0] * MM), int(clone.xy[1] * MM)),
            rotation_deg=clone.rotation_deg,
            mirror=bool(clone.mirror),
            mount=cell_mount_offset(cell),
        )
        components = {}
        for slot in cell.components:
            wx, wy = frame.point_to_world_mm(
                slot.offset_along_mm or 0.0, slot.offset_across_mm or 0.0)
            components[slot.role] = [_round(wx), _round(wy)]
        data["clones"][clone.name] = {
            "cell": clone.cell,
            "xy": [_round(clone.xy[0]), _round(clone.xy[1])],
            "rotation_deg": _round(clone.rotation_deg),
            "mirror": bool(clone.mirror),
            "components": components,
        }
    return data


# ── Y.7.2: the acceptance test ─────────────────────────────────────────────

def test_converted_fixture_reproduces_the_frozen_baseline_bit_exactly():
    """The MAIN acceptance claim: loaded through the NEW parser, the converted
    fixture yields the same node layout AND the same materialized cell
    components as the PRE-migration fixture did (captured into
    expected_geometry.json by the diagnostics script). A 30°-rotated base is
    baked into the fake board, so a conversion that lost the base rotation
    would fail here rather than pass silently."""
    expected = json.loads(
        (FIXTURES / "expected_geometry.json").read_text(encoding="utf-8"))
    actual = _snapshot(str(FIXTURES / "config.converted.sexp"))
    assert actual["trees"] == expected["trees"]
    assert actual["clones"] == expected["clones"]


# ── Y.9.5: the converter ───────────────────────────────────────────────────

def test_converter_wraps_every_own_anchor_node_in_a_mount_node():
    data = sexp_to_dict((FIXTURES / "config.sexp").read_text(encoding="utf-8"),
                        raw_trees=True)
    converted, report = convert_trees_dict(data)
    assert report["trees_touched"] == 1
    assert report["mounts_created"] == 5
    assert report["nodes_wrapped"] == 5
    node = converted["trees"][0]["nodes"][0]
    assert node["kind"] == "mount"
    assert node["ref"] == "AD_DAC_pad3"
    assert node["anchor"] == {"role": "AD_DAC", "sheet": "Channel_0",
                              "cluster": "DAC_BUF", "pad": "3"}
    child = node["children"][0]
    assert child["ref"] == "pif_dvdd_channel_0"
    assert child["xy"] == [-1.5, 0.0]
    assert "anchor" not in child


def test_converter_groups_identical_anchors_under_one_mount():
    """Same role+sheet+cluster+pad = the same reference point, so the nodes
    share ONE mount node (plan §Y.6.2)."""
    data = {"trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
        {"ref": "E1", "kind": "placement", "xy": [0, 0],
         "anchor": {"role": "A", "pad": "1"}},
        {"ref": "E2", "kind": "placement", "xy": [1, 0],
         "anchor": {"role": "A", "pad": "1"}},
    ]}]}
    converted, report = convert_trees_dict(data)
    nodes = converted["trees"][0]["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["kind"] == "mount"
    assert nodes[0]["ref"] == "A_pad1"
    assert [c["ref"] for c in nodes[0]["children"]] == ["E1", "E2"]
    assert report["mounts_created"] == 1
    assert report["grouped"] == 1


def test_converter_splits_different_pads_into_separate_mounts():
    data = {"trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
        {"ref": "E1", "kind": "placement", "xy": [0, 0],
         "anchor": {"role": "A", "pad": "1"}},
        {"ref": "E2", "kind": "placement", "xy": [1, 0],
         "anchor": {"role": "A", "pad": "2"}},
    ]}]}
    converted, report = convert_trees_dict(data)
    nodes = converted["trees"][0]["nodes"]
    assert [n["ref"] for n in nodes] == ["A_pad1", "A_pad2"]
    assert report["mounts_created"] == 2


def test_converter_inserts_the_mount_at_the_nested_position():
    """A nested own-anchor node's mount node lands in the SAME place in the
    hierarchy (plan §Y.6.4) — nesting, not a reparenting."""
    data = {"trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
        {"ref": "P", "kind": "placement", "xy": [0, 0], "children": [
            {"ref": "E1", "kind": "placement", "xy": [1, 1],
             "anchor": {"role": "R", "pad": "2"}},
        ]},
    ]}]}
    converted, _report = convert_trees_dict(data)
    parent = converted["trees"][0]["nodes"][0]
    assert parent["ref"] == "P"
    nested = parent["children"][0]
    assert nested["kind"] == "mount"
    assert nested["ref"] == "R_pad2"
    assert nested["children"][0]["ref"] == "E1"


def test_converter_renames_a_colliding_mount_name_and_reports_it():
    """A mount name already taken in the tree gets a numeric suffix AND the
    rename is reported — never silently (plan §Y.6.3)."""
    data = {"trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
        {"ref": "A", "kind": "placement", "xy": [0, 0]},
        {"ref": "E1", "kind": "placement", "xy": [1, 0],
         "anchor": {"role": "A"}},
    ]}]}
    converted, report = convert_trees_dict(data)
    assert converted["trees"][0]["nodes"][1]["ref"] == "A_2"
    assert report["renames"] == [("t", "A", "A_2")]


def test_converter_is_idempotent_on_an_already_converted_config():
    data = sexp_to_dict(
        (FIXTURES / "config.converted.sexp").read_text(encoding="utf-8"),
        raw_trees=True)
    converted, report = convert_trees_dict(data)
    assert report["trees_touched"] == 0
    assert converted == data


def test_converter_stops_on_an_unknown_module_ref_with_a_pivot():
    """Б3.2 §В.2.2 (2026-09-11): a module node whose ref names NEITHER a
    trees: entry NOR a tree_instances: instance is now a STOP, never the old
    silent `continue` that let the serializer fail with an unrelated message
    AFTER the target had been truncated (task В.1).

    This test USED to pin that silent-continue behaviour (Б1's
    test_converter_leaves_the_tree_anchor_and_pivot_untouched). Exactly this
    one unit test was rewritten with Denis's explicit approval on 2026-09-11;
    the frozen acceptance test above and expected_geometry.json are untouched,
    and the tree's own (anchor ...) is still covered by the acceptance and
    idempotency tests."""
    data = {"trees": [{"name": "t", "anchor": {"role": "FPGA"}, "nodes": [
        {"ref": "child_tree", "kind": "module", "pivot_xy": [1, 2]},
        {"ref": "E1", "kind": "placement", "xy": [0, 0],
         "anchor": {"role": "A"}},
    ]}]}
    with pytest.raises(ValidationError, match="references neither a trees"):
        convert_trees_dict(data)


# ── Y.9.3: the drift guard ─────────────────────────────────────────────────

def _drift_cfg(*, tree_nodes, entities, cells):
    return Config(cells=cells, entities=list(entities),
                  trees=[Tree(name="t", anchor=TreeAnchor(role="FPGA"),
                              nodes=list(tree_nodes))])


def _mount(ref, role, **anchor_kw):
    return TreeNode(ref=ref, kind="mount", xy=None, polar=None, rotation=0.0,
                    name=None, group=None, children=[],
                    anchor=TreeAnchor(role=role, is_origin=False, **anchor_kw))


def _placed_cfg(*, mount_kwargs=None, sheet=None, cluster=None):
    cell = Cell(name="c", components=[TemplateComponentSlot(role="R")])
    entity = Entity(name="E1", cell="c", sheet=sheet, cluster=cluster)
    placed = TreeNode(ref="E1", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])
    return _drift_cfg(tree_nodes=[placed, _mount("m1", "R", **(mount_kwargs or {}))],
                      entities=[entity], cells={"c": cell})


def test_drift_guard_accepts_a_mount_anchor_on_a_role_the_tree_places():
    """REVERSED 2026-09-11 (plan_2026_09_11_internal_mount §Г.4): a mount on a
    role of a cell THIS tree places is now the legitimate INTERNAL method — the
    base is computed from the tree's own layout, never read back from the board,
    so it cannot drift. The guard no longer fatals on this shape."""
    check_mount_anchor_drift(_placed_cfg())          # must NOT raise


def test_drift_guard_allows_a_role_from_outside_the_tree():
    """A role no cell of this tree owns is a legitimate LIVE base."""
    cell = Cell(name="c", components=[TemplateComponentSlot(role="R")])
    entity = Entity(name="E1", cell="c")
    placed = TreeNode(ref="E1", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])
    cfg = _drift_cfg(tree_nodes=[placed, _mount("m1", "OTHER")],
                     entities=[entity], cells={"c": cell})
    check_mount_anchor_drift(cfg)          # must not raise


def test_drift_guard_honours_the_sheet_narrowing():
    """A narrower anchor only matches a placed Entity of the SAME sheet: a
    different sheet is the LIVE (external) method, the same sheet the now-legal
    INTERNAL one — both accepted."""
    check_mount_anchor_drift(
        _placed_cfg(sheet="Channel_0", mount_kwargs={"anchor_sheet": "Channel_1"}))
    check_mount_anchor_drift(
        _placed_cfg(sheet="Channel_0",
                    mount_kwargs={"anchor_sheet": "Channel_0"}))


def test_drift_guard_honours_the_cluster_narrowing():
    """A cluster on a DIFFERENT placed Entity is external; the same cluster (or
    an UNSET field, matching any) is internal — all legal now that the internal
    method has a defined base."""
    check_mount_anchor_drift(
        _placed_cfg(cluster="A", mount_kwargs={"anchor_cluster": "B"}))
    check_mount_anchor_drift(
        _placed_cfg(cluster="A", mount_kwargs={"anchor_cluster": "A"}))
    check_mount_anchor_drift(_placed_cfg(cluster="A"))


def test_drift_guard_fatals_on_ambiguity_not_on_the_internal_shape():
    """With TWO nodes placing cells that carry the role and no sheet/cluster to
    narrow them, the base is ambiguous — a fatal listing the candidates (this is
    the redirected strictness §Г.2)."""
    cell = Cell(name="c", components=[TemplateComponentSlot(role="R")])
    entities = [Entity(name="E1", cell="c"), Entity(name="E2", cell="c")]
    placed_a = TreeNode(ref="E1", kind="placement", xy=(0.0, 0.0), polar=None,
                        rotation=0.0, name=None, group=None, children=[])
    placed_b = TreeNode(ref="E2", kind="placement", xy=(1.0, 0.0), polar=None,
                        rotation=0.0, name=None, group=None, children=[])
    cfg = _drift_cfg(tree_nodes=[placed_a, placed_b, _mount("m1", "R")],
                     entities=entities, cells={"c": cell})
    with pytest.raises(ValidationError, match="more than one cell"):
        check_mount_anchor_drift(cfg)


def test_drift_guard_deliberately_ignores_the_tree_own_role_anchor():
    """The tree's OWN (role ...) anchor is NOT checked — an EMPIRICAL finding
    (2026-09-11, found by this very test suite): the extract / self-anchor
    pattern anchors a tree on the component its own root cell is built around
    (real case: `dac_buf_tpl` anchored on role `DAC_BUF`, which its placed cell
    also contains). That component is the tree's REFERENCE, not something the
    tree moves, so an extension of the guard to the tree anchor FATALLY rejected
    two real configs (tests/test_clone_role_resolver.py). Only the mount-node
    case was specified (plan §Y.3) and only that one is enforced."""
    cell = Cell(name="c", components=[TemplateComponentSlot(role="FPGA")])
    entity = Entity(name="E1", cell="c")
    placed = TreeNode(ref="E1", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])
    cfg = Config(cells={"c": cell}, entities=[entity],
                 trees=[Tree(name="t", anchor=TreeAnchor(role="FPGA"),
                             nodes=[placed])])
    check_mount_anchor_drift(cfg)          # must NOT raise


def test_the_internal_mount_shape_is_accepted_by_load_config(tmp_path):
    """The guard runs at LOAD time (config/loader.py). Since the internal shape
    is now legal, such a config LOADS — and this proves the mount grammar still
    round-trips through load_config."""
    text = dict_to_sexp({
        "cells": {"c": {"components": [{"role": "R", "offset_along_mm": 0.0,
                                        "offset_across_mm": 0.0}]}},
        "entities": [{"name": "E1", "cell": "c"}],
        "trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
            {"ref": "E1", "kind": "placement", "xy": [0, 0]},
            {"ref": "m1", "kind": "mount", "anchor": {"role": "R"}},
        ]}],
    })
    path = tmp_path / "cfg.sexp"
    path.write_text(text, encoding="utf-8")
    cfg, _ctx = load_config(str(path))          # must not raise
    assert cfg.trees and cfg.trees[0].nodes[1].kind == "mount"


# ── Y.9.4: the mount node's display tag ────────────────────────────────────

def test_mount_kind_has_a_display_tag():
    from gui.docks.trees_dock import _KIND_TAGS
    assert _KIND_TAGS["mount"]


def test_the_frozen_fixture_set_is_complete():
    """Z.1 (plan_2026_09_11_trees_dock_single_panel): all THREE frozen fixture
    files must be present. expected_geometry.json was silently gitignored once —
    the acceptance test then failed everywhere but the author's machine — so this
    pins the whole set."""
    for name in ("config.sexp", "config.converted.sexp", "expected_geometry.json"):
        assert (FIXTURES / name).is_file(), f"missing frozen fixture: {name}"
