# tests/net/test_extra_copper_s0.py
"""§0 of plan_2026_11_10_extra_copper_by_selection: the Reread's AREA.

The live trap (HP-Power, 10.11 13:44): «Select inter-node copper» selects tracks
and vias, never footprints; the next «Reread inter-node copper» took its pad
BOUNDARIES from that selection, so the classifier saw zero pads — every unit
became UNMOORED and six records were declared «no longer on the board». The rule
(§0 п.1–3): a selection narrows the COPPER only, the pads are always the whole
board's; a record none of whose pads the selection touches is `not_in_selection`,
NOT `missing`; the report's first line names the area. §0 п.5 adds the «this tree
places modules, their copper is re-read on their own tabs» line, and §0 п.6 makes
every door that puts a node into a tree ask the local-name rule first.

§0.1 (the acceptance of `38bff313`) closes the two holes that rule left: the
«touches the selection» test compared a set of FROZENSETS with a set of STRINGS
(always empty, so any selection hid a real `missing`), and the generated name
avoided the tree's LOCAL names only — never the record names the config already
holds, so a record written under a taken name was dropped in silence.

The board/config doubles are the ones tests/net/test_internode_capture.py uses.
"""
import dataclasses
from types import SimpleNamespace

import pytest

from kicadstamp.config import Config, Entity, NetTrace, TemplateTrack
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import (CapturedTrace, RereadPlan,
                                          apply_reread_plan,
                                          plan_internode_reread,
                                          reread_report_lines)
from kicadstamp.reread_area import read_reread_area
from kicadstamp.trees import Tree, TreeAnchor, TreeNode, tree_from_dict, tree_to_dict
from kicadstamp.trees_local_refs import free_node_ref, local_name_refusal


def _fp(ref, role=None, cluster=None, x_mm=0.0, y_mm=0.0):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6), y=int(y_mm * 1e6)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    return fp


def _pad(number, net, x_mm, y_mm):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=SimpleNamespace(x=int(x_mm * 1e6),
                                                    y=int(y_mm * 1e6)))


def _track(x1, y1, x2, y2, net, layer=BoardLayer.BL_F_Cu, width=0.25):
    return Track(uuid=f"t{x1}{y1}{x2}{y2}",
                 start=SimpleNamespace(x=int(x1 * 1e6), y=int(y1 * 1e6)),
                 end=SimpleNamespace(x=int(x2 * 1e6), y=int(y2 * 1e6)),
                 net_name=net, width_mm=width, layer=layer)


def _via(x, y, net):
    return Via(uuid=f"v{x}{y}",
               position=SimpleNamespace(x=int(x * 1e6), y=int(y * 1e6)),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


class _AreaBoard:
    """A board double with the WHOLE-board halves `read_reread_area` needs."""

    def __init__(self, footprints, pads_by_ref, tracks=(), vias=(), selected=()):
        self._fps = list(footprints)
        self._pads = pads_by_ref
        self._tracks = list(tracks)
        self._vias = list(vias)
        self._selected = list(selected)

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_selected_items(self):
        return list(self._selected)

    def get_field_value(self, fp, name):
        return {"Role": getattr(fp, "_role", None),
                "Cluster": getattr(fp, "_cluster", None)}.get(name)

    def get_footprint(self, ref):
        return next((f for f in self._fps if f.ref == ref), None)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, num):
        for pad in self._pads.get(fp.ref, []):
            if str(pad.number) == str(num):
                return pad
        return None

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            box = SimpleNamespace()
            box.pos = SimpleNamespace(x=int(it.position.x - 0.3e6),
                                      y=int(it.position.y - 0.3e6))
            box.size = SimpleNamespace(x=600_000, y=600_000)
            box.inflate = lambda _d: None
            out.append(box)
        return out


def _nodes(**extra):
    nodes = [TreeNode(ref="e_a", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[]),
             TreeNode(ref="e_b", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])]
    return nodes + list(extra.get("nodes", []))


def _tree(net_trace_refs=("n__a__b",), nodes=(), name="t") -> Tree:
    all_nodes = _nodes(nodes=list(nodes))
    all_nodes += [TreeNode(ref=r, kind="net_trace", xy=None, polar=None,
                           rotation=0.0, name=None, group=None, children=[])
                  for r in net_trace_refs]
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=all_nodes)


def _cfg(net_traces=()) -> Config:
    return Config(
        entities=[Entity(name="e_a", cell="c", cluster="A"),
                  Entity(name="e_b", cell="c", cluster="B")],
        net_traces=list(net_traces))


def _existing(name="n__a__b", pads=("A.1", "B.1"), **over):
    fields = dict(net="N", anchor_role="A", anchor_pad="1", anchor_cluster="A",
                  anchor_rotation_deg=0.0, pads=list(pads),
                  tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                                        end_along_mm=10.0, end_across_mm=0.0,
                                        width_mm=0.25, layer="F.Cu",
                                        net_from_role="A", net_from_role_pad="1")],
                  name=name)
    fields.update(over)
    return NetTrace(**fields)


def _area():
    """R1 at (10,10), R2 at (20,10): one track between them, one via beside it."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    track = _track(10, 10, 20, 10, "N")
    via = _via(10, 10, "N")
    return _AreaBoard(fps, pads, tracks=[track], vias=[via]), fps, [track, via]


# ── 0в: THE owner of the area ──────────────────────────────────────────────

def test_0v_a_selection_narrows_the_copper_and_never_the_pads():
    """§0 п.1: the pads of a read are the WHOLE board's, whatever is selected —
    taking them from the selection is the defect. Copper items that are not a
    track or a via (a footprint in the selection) are ignored."""
    board, fps, items = _area()
    fp = fps[0]
    board._selected = [items[0], fp]

    area = read_reread_area(board, board.get_selected_items())

    assert area.items == [items[0]]            # only the selected TRACK
    assert area.footprints == fps              # pads: the whole board
    assert area.narrowed is True
    assert area.track_count == 1 and area.via_count == 0


def test_0v_without_a_selection_the_area_is_the_whole_board():
    board, fps, items = _area()
    area = read_reread_area(board, [])
    assert area.items == items
    assert area.footprints == fps
    assert area.narrowed is False
    assert (area.track_count, area.via_count) == (1, 1)


# ── 0а: the selection of «Select inter-node copper» reads as the whole board ─

def test_0a_a_medley_selection_gives_the_same_units_as_the_whole_board():
    """The trap's own shape: the selection is EXACTLY what «Select inter-node
    copper» highlights (tracks and vias, zero footprints). The read must find the
    same unit as the whole-board read and keep the record `unchanged` — not
    UNMOORED, not `missing`."""
    board, fps, items = _area()
    cfg = _cfg([_existing()])
    tree = _tree()

    # The selection is EXACTLY what «Select inter-node copper» highlights, and the
    # area comes from THE owner — the same call the worker makes.
    board._selected = list(items)
    area = read_reread_area(board, board.get_selected_items())
    narrow = plan_internode_reread(
        board, cfg, tree, area_items=area.items, area_footprints=area.footprints,
        sheet_names={}, narrowed=area.narrowed, config_path=None)
    wide = plan_internode_reread(
        board, cfg, tree, area_items=items, area_footprints=fps,
        sheet_names={}, config_path=None)

    assert narrow.unchanged == ["n__a__b"] == wide.unchanged
    assert narrow.missing == [] and wide.missing == []
    assert not narrow.discarded.get("UNMOORED")
    assert narrow.not_in_selection == []


# ── 0б: a record the selection does not touch is NOT «missing» ─────────────

def test_0b_a_record_outside_the_selection_is_not_missing():
    """One record's copper is selected and matches; the other record's pads are
    not in the read copper at all. Before §0 п.2 the second one went to `missing`
    («the copper is no longer on the board») and was marked stale."""
    board, fps, items = _area()
    other = _existing(name="gone__a__c", pads=("Z.9",))
    cfg = _cfg([_existing(), other])
    tree = _tree(net_trace_refs=("n__a__b", "gone__a__c"))

    plan = plan_internode_reread(
        board, cfg, tree, area_items=items[:1], area_footprints=fps,
        sheet_names={}, narrowed=True, config_path=None)

    assert plan.unchanged == ["n__a__b"]
    assert plan.missing == []                       # NOT «the copper is gone»
    assert plan.not_in_selection == ["gone__a__c"]  # «not checked»
    text = "\n".join(reread_report_lines("t", plan))
    assert "not in the selection: 1 record(s) — not checked" in text


def test_0b2_a_record_the_selection_TOUCHES_is_still_missing():
    """§0.1 дыра 1: `candidates & selection_pads` intersected a set of
    FROZENSETS with a set of STRINGS, so the intersection was always empty and
    ANY selection turned every unfound record into «not checked» — a real loss
    hidden by the very mode meant to narrow the read. Here the read copper
    touches `A.1`, one of the record's pads, and that record has no copper left:
    it is `missing`, and the report says «not found»."""
    board, fps, items = _area()
    touched = _existing(name="gone__a__b", pads=("A.1", "Z.9"))
    cfg = _cfg([_existing(), touched])
    tree = _tree(net_trace_refs=("n__a__b", "gone__a__b"))

    plan = plan_internode_reread(
        board, cfg, tree, area_items=items[:1], area_footprints=fps,
        sheet_names={}, narrowed=True, config_path=None)

    assert plan.unchanged == ["n__a__b"]
    assert plan.missing == ["gone__a__b"]           # NOT «not checked»
    assert plan.not_in_selection == []
    assert "not found:  gone__a__b" in "\n".join(reread_report_lines("t", plan))


# ── 0г: the report's first line names the area ────────────────────────────

def test_0g_the_report_names_the_area_in_both_modes():
    board, fps, items = _area()
    tree = _tree(net_trace_refs=())
    narrow = plan_internode_reread(
        board, _cfg(), tree, area_items=items[:1], area_footprints=fps,
        sheet_names={}, narrowed=True, config_path=None)
    wide = plan_internode_reread(
        board, _cfg(), tree, area_items=items, area_footprints=fps,
        sheet_names={}, config_path=None)

    narrow_lines = reread_report_lines("t", narrow)
    wide_lines = reread_report_lines("t", wide)
    assert narrow_lines[1] == "  area: selection — 1 track(s), 0 via(s)"
    assert wide_lines[1] == "  area: whole board"


# ── 0д: a tree that places modules says where their copper is re-read ─────

def test_0d_a_tree_with_modules_names_them_after_the_header():
    board, fps, items = _area()
    modules = [TreeNode(ref="switch_a", kind="module", xy=None, polar=None,
                        rotation=0.0, name=None, group=None, children=[]),
               TreeNode(ref="switch_b", kind="module", xy=None, polar=None,
                        rotation=0.0, name=None, group=None, children=[])]
    plan = plan_internode_reread(
        board, _cfg(), _tree(net_trace_refs=(), nodes=modules, name="main"),
        area_items=items, area_footprints=fps, sheet_names={}, config_path=None)

    lines = reread_report_lines("main", plan)
    assert lines[0] == "Tree 'main': inter-node copper re-read."
    assert lines[2] == (
        "  Tree 'main' places module(s) switch_a, switch_b — their inter-node "
        "copper is re-read on their own tabs")

    plain = plan_internode_reread(
        board, _cfg(), _tree(net_trace_refs=()), area_items=items,
        area_footprints=fps, sheet_names={}, config_path=None)
    assert not any("places module(s)" in line for line in reread_report_lines("t", plain))


# ── §0 п.6: the generated record name must not be a LOCAL name of the tree ──

def test_06_a_generated_name_avoids_a_local_name_of_the_tree():
    """A copper container called like the record the re-read is about to create:
    the plan must store the record under ANOTHER name (the local-name rule's own
    answer), and the resulting tree must load."""
    board, fps, items = _area()
    container = TreeNode(ref="n__a__b", kind="copper", xy=None, polar=None,
                         rotation=0.0, name=None, group=None, children=[])
    tree = _tree(net_trace_refs=(), nodes=[container])

    plan = plan_internode_reread(
        board, _cfg(), tree, area_items=items, area_footprints=fps,
        sheet_names={}, config_path=None)

    fresh = plan.added[0].record.name
    assert fresh == "n__a__b_2"
    assert local_name_refusal(tree.name, fresh, tree.nodes) is None
    # ... and a node of that name beside the container is a FILE THE LOADER READS.
    tree.nodes.append(TreeNode(ref=fresh, kind="net_trace", xy=None, polar=None,
                               rotation=0.0, name=None, group=None, children=[]))
    tree_from_dict(tree_to_dict(tree))       # the loader's local-name rule passes


def test_06_free_node_ref_is_the_rule_every_door_asks():
    """(§0 п.6) ONE helper, so a door never compares names itself: the ref comes
    back unchanged when the rule allows it, and `_2`, `_3`, … otherwise.
    `taken` (§0.1) is the second half of the SAME decision: the config's record
    names are as unusable as a local clash — and `start` keeps a node's own
    numbering (`_1`) apart from a record's (`_2`, `generate_trace_name`'s)."""
    container = TreeNode(ref="x", kind="copper", xy=None, polar=None,
                         rotation=0.0, name=None, group=None, children=[])
    nodes = [container]
    assert free_node_ref("net_trace", "t", "other", nodes) == "other"
    assert free_node_ref("net_trace", "t", "x", nodes) == "x_2"
    # a TAKEN name is skipped exactly like a local clash is
    assert free_node_ref("net_trace", "t", "x", nodes, taken={"x_2"}) == "x_3"
    assert free_node_ref("net_trace", "t", "y", nodes, taken={"y"}) == "y_2"
    # a node's auto-number counts from `_1` (phase 5.5)
    assert free_node_ref("net_trace", "t", "x", nodes, start=1) == "x_1"
    # it walks down into children, and a same-kind duplicate is a clash too
    nested = dataclasses.replace(container, children=[])
    parent = TreeNode(ref="p", kind="mount", xy=None, polar=None, rotation=0.0,
                      name=None, group=None, children=[nested])
    assert free_node_ref("copper", "t", "x", [parent]) == "x_2"
    # ... and a MOUNT clashes with a MOUNT only (16.11.2026: a mount and a record
    # of one name are different things — plan_2026_11_14_mount_and_record_may_
    # share_a_name — so `free_node_ref("mount", …)` beside a copper `x` keeps `x`)
    assert free_node_ref("mount", "t", "x", [parent]) == "x"
    nested_mount = TreeNode(ref="x", kind="mount", xy=None, polar=None,
                            rotation=0.0, name=None, group=None, children=[],
                            anchor=TreeAnchor(ref="R1"))
    holder = TreeNode(ref="p", kind="mount", xy=None, polar=None, rotation=0.0,
                      name=None, group=None, children=[nested_mount])
    assert free_node_ref("mount", "t", "x", [holder]) == "x_2"


# ── §0.1: a new record never takes a name the config already holds ─────────

def test_06v_a_new_record_skips_a_record_name_the_config_already_has():
    """§0.1 дыра 2: the copper container `n__a__b` forces a suffix and the
    config ALREADY holds a record `n__a__b_2` (other pads). Before `taken=`, the
    plan handed out that very name, the report said «added», and
    `apply_reread_plan` dropped the new record in silence — the copper was gone.
    Now the rule skips a taken name, so the record is `n__a__b_3` and the written
    config carries BOTH."""
    board, fps, items = _area()
    container = TreeNode(ref="n__a__b", kind="copper", xy=None, polar=None,
                         rotation=0.0, name=None, group=None, children=[])
    tree = _tree(net_trace_refs=("n__a__b_2",), nodes=[container])
    cfg = _cfg([_existing(name="n__a__b_2", pads=("C.1", "D.1"))])

    plan = plan_internode_reread(
        board, cfg, tree, area_items=items, area_footprints=fps,
        sheet_names={}, config_path=None)

    assert [c.identity for c in plan.added] == ["n__a__b_3"]
    written = apply_reread_plan(cfg, plan)
    assert [nt.name for nt in written.net_traces] == ["n__a__b_2", "n__a__b_3"]


def test_06d_apply_refuses_to_drop_an_added_record_under_a_taken_name():
    """§0.1: the safety net. A plan whose `added` record repeats a name the
    config has is a PLANNER bug — the write must raise with that name instead of
    skipping the record (and losing the copper the report calls «added»)."""
    cfg = _cfg([_existing(name="n__a__b")])
    plan = RereadPlan()
    plan.added.append(CapturedTrace(record=_existing(name="n__a__b"),
                                    identity="n__a__b", signature=frozenset(),
                                    track_count=1, via_count=0))

    with pytest.raises(ValueError, match="n__a__b"):
        apply_reread_plan(cfg, plan)
