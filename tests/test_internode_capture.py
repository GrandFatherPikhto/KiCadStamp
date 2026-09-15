# tests/test_internode_capture.py
"""Э4 tests: capture / re-read a tree's inter-node copper as net_traces records
(plan_2026_09_12_internode_copper_core, stage Э4; design §4/§6/§11/§16).

Pure planning against a mock adapter: unit identity by pad SET, added/updated/
unchanged/missing bookkeeping, the legacy (no signature) fallback by net, the
(role, pad) representation of what is written, and the guarantee that nothing
is ever removed.

The last section is the nested-sheet work of
plan_2026_09_15_internode_copper_sheets_and_nets (С1-С3, С8-С12): three
channels carrying the SAME Cluster and Role tags (one reused hierarchical sheet
per channel), the DAC/OpAmp components one level deeper than the PIF
capacitors. A component is matched by ANY segment of its sheet path (Э1), a NEW
record stores its TREE NODE's sheet (Э3), and the report names what the
classification did not take (Э4).
"""
import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.config import Config, Entity, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import (
    apply_reread_plan,
    capture_units,
    plan_internode_reread,
    reread_report_lines,
    tree_net_trace_identities,
)
from kicadstamp.placement.services.clone_role_resolver import resolve_footprint_by_role
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.docks.reead import ReReadCluster
from gui.docks.tree_from_selection import detect_inter_cluster_nets


# ── board double ──────────────────────────────────────────────────────────

def _fp(ref, role=None, cluster=None, x_mm=0.0, y_mm=0.0, angle=0.0):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6), y=int(y_mm * 1e6)),
                   angle_deg=angle, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    return fp


def _pad(number, net, x_mm, y_mm):
    return SimpleNamespace(number=str(number), net_name=net,
                           position=SimpleNamespace(x=int(x_mm * 1e6),
                                                    y=int(y_mm * 1e6)))


def _track(x1, y1, x2, y2, net, layer=BoardLayer.BL_F_Cu, width=0.25):
    return Track(uuid=f"t{x1}{y1}", start=SimpleNamespace(x=int(x1 * 1e6), y=int(y1 * 1e6)),
                 end=SimpleNamespace(x=int(x2 * 1e6), y=int(y2 * 1e6)),
                 net_name=net, width_mm=width, layer=layer)


def _via(x, y, net):
    return Via(uuid=f"v{x}{y}", position=SimpleNamespace(x=int(x * 1e6), y=int(y * 1e6)),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


class _Board:
    def __init__(self, footprints, pads_by_ref):
        self._fps = list(footprints)
        self._pads = pads_by_ref

    def get_footprints(self):
        return list(self._fps)

    def get_selected_items(self):
        return []

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
            b = SimpleNamespace()
            b.pos = SimpleNamespace(x=int(it.position.x - 0.3e6),
                                    y=int(it.position.y - 0.3e6))
            b.size = SimpleNamespace(x=600_000, y=600_000)
            b.inflate = lambda _d: None
            out.append(b)
        return out


# ── config fixtures ───────────────────────────────────────────────────────

def _tree(net_trace_refs=("n__a__b",)) -> Tree:
    nodes = [TreeNode(ref="e_a", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[]),
             TreeNode(ref="e_b", kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])]
    nodes += [TreeNode(ref=r, kind="net_trace", xy=None, polar=None, rotation=0.0,
                       name=None, group=None, children=[])
              for r in net_trace_refs]
    return Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _cfg(net_traces=(), entities=None) -> Config:
    return Config(
        entities=entities or [Entity(name="e_a", cell="c", cluster="A"),
                              Entity(name="e_b", cell="c", cluster="B")],
        net_traces=list(net_traces))


def _area():
    """R1 (node A) at (10,10), R2 (node B) at (20,10), one track between them."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    items = [_track(10, 10, 20, 10, "N")]
    return _Board(fps, pads), fps, items


def _existing(name="n__a__b", pads=("A.1", "B.1"), named=True, **over):
    fields = dict(net="N", anchor_role="A", anchor_pad="1", anchor_cluster="A",
                  anchor_rotation_deg=0.0, pads=list(pads),
                  tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                                        end_along_mm=10.0, end_across_mm=0.0,
                                        width_mm=0.25, layer="F.Cu",
                                        net_from_role="A", net_from_role_pad="1")])
    if named:
        fields["name"] = name
    fields.update(over)
    return NetTrace(**fields)


# ── tree walking ──────────────────────────────────────────────────────────

def test_tree_net_trace_identities_reads_node_refs():
    tree = _tree(("spi__a__b", "i2c__a__b"))
    assert tree_net_trace_identities(tree) == ["spi__a__b", "i2c__a__b"]


# ── a NEW unit becomes a named record ─────────────────────────────────────

def test_new_unit_is_captured_as_a_named_record():
    board, fps, items = _area()
    cfg = _cfg()
    plan = plan_internode_reread(board, cfg, _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps)

    assert len(plan.added) == 1
    record = plan.added[0].record
    assert record.name == "n__a__b"                 # <net leaf>__<nodes A-Z>
    assert record.pads == ["A.1", "B.1"]
    assert record.anchor_role == "A" and record.anchor_pad == "1"
    assert record.anchor_cluster == "A"
    # (role, pad)-based: no literal net on the copper at all
    assert record.tracks[0].net is None
    assert record.tracks[0].net_from_role == "A"
    assert record.tracks[0].net_from_role_pad == "1"
    # local offsets measured from the anchor pad (a raw board-frame difference)
    assert (record.tracks[0].start_along_mm, record.tracks[0].start_across_mm) == (0.0, 0.0)
    assert (record.tracks[0].end_along_mm, record.tracks[0].end_across_mm) == (10.0, 0.0)
    assert plan.updated == [] and plan.missing == []


def test_mid_chain_via_takes_the_units_pad_reference():
    """A via that touches no pad of its own still gets a (role, pad) reference —
    the unit's anchor pad, because the whole unit is one net (design §15).
    The via joins the chain at a layer-change point between two track ends
    (a via floating in the MIDDLE of one straight segment is not connected to
    it: copper joins at coincident endpoints, not by landing on a segment)."""
    board, fps, _ = _area()
    items = [_track(10, 10, 15, 10, "N"), _via(15, 10, "N"),
             _track(15, 10, 20, 10, "N")]
    plan = plan_internode_reread(board, _cfg(), _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps)
    record = plan.added[0].record
    assert len(record.tracks) == 2 and len(record.vias) == 1
    assert record.vias[0].net is None
    assert record.vias[0].net_from_role == "A"
    assert record.vias[0].net_from_role_pad == "1"


def test_cluster_copper_and_foreign_copper_are_not_captured():
    """Only INTERNODE units are stored: copper inside one tree node, and copper
    reaching a component outside the tree, are both left alone."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B"),
           _fp("R9", role="X", cluster="ELSEWHERE")]
    pads = {"R1": [_pad("1", "N", 10, 10), _pad("2", "N", 12, 10)],
            "R2": [_pad("1", "N", 20, 10)],
            "R9": [_pad("1", "N", 30, 10)]}
    items = [_track(10, 10, 12, 10, "N"),      # both ends inside node A
             _track(20, 10, 30, 10, "N")]      # A/B -> a foreign component
    plan = plan_internode_reread(_Board(fps, pads), _cfg(), _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps)
    assert plan.added == []


def test_unit_without_a_role_on_its_pad_is_skipped_with_a_warning():
    fps = [_fp("R1", role=None, cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    plan = plan_internode_reread(_Board(fps, pads), _cfg(), _tree(net_trace_refs=()),
                                 area_items=[_track(10, 10, 20, 10, "N")],
                                 area_footprints=fps)
    assert plan.added == []
    assert any("no Role field" in w for w in plan.warnings)


# ── matching: pad set first, net as the legacy bridge ─────────────────────

def test_unchanged_when_the_stored_geometry_already_matches():
    board, fps, items = _area()
    cfg = _cfg([_existing()])
    plan = plan_internode_reread(board, cfg, _tree(),
                                 area_items=items, area_footprints=fps)
    assert plan.unchanged == ["n__a__b"]
    assert plan.added == [] and plan.updated == [] and plan.missing == []


def test_updated_when_the_geometry_moved():
    board, fps, items = _area()
    cfg = _cfg([_existing(tracks=[TemplateTrack(
        start_along_mm=0.0, start_across_mm=0.0, end_along_mm=99.0,
        end_across_mm=0.0, width_mm=0.25, layer="F.Cu",
        net_from_role="A", net_from_role_pad="1")])])
    plan = plan_internode_reread(board, cfg, _tree(),
                                 area_items=items, area_footprints=fps)
    assert len(plan.updated) == 1
    fresh, counts = plan.updated[0]
    assert fresh.identity == "n__a__b"          # the name is never changed
    assert fresh.record.tracks[0].end_along_mm == 10.0
    assert counts == (1, 0, 1, 0)
    assert plan.added == [] and plan.missing == []


def test_missing_record_is_reported_and_kept():
    """The board has no copper for this record (its stored pad set matches no
    fresh unit) — it is reported, never deleted."""
    board, fps, items = _area()
    cfg = _cfg([_existing(name="gone__a__b", pads=("A.1", "Z.9"))])
    plan = plan_internode_reread(board, cfg, _tree(net_trace_refs=("gone__a__b",)),
                                 area_items=items, area_footprints=fps)
    assert plan.missing == ["gone__a__b"]
    assert plan.added and plan.added[0].identity.startswith("n__")
    after = apply_reread_plan(cfg, plan)
    # nothing removed: the missing record AND the fresh one are both there
    assert [nt.name for nt in after.net_traces] == ["gone__a__b", "n__a__b"]


def test_legacy_record_is_matched_by_net_and_keeps_literal_nets():
    """No stored pads: -> the one identity a legacy record has (its net). The
    fresh copy stays legacy (literal nets) but gains the signature, so the NEXT
    re-read is exact."""
    board, fps, items = _area()
    legacy = NetTrace(net="N", anchor_role="A", anchor_pad="1",
                      tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                                            end_along_mm=99.0, end_across_mm=0.0,
                                            width_mm=0.25, layer="F.Cu", net="N")])
    plan = plan_internode_reread(board, _cfg([legacy]), _tree(net_trace_refs=("N",)),
                                 area_items=items, area_footprints=fps)
    assert len(plan.updated) == 1
    fresh, _counts = plan.updated[0]
    assert fresh.identity == "N"
    assert fresh.record.name is None                     # still legacy
    assert fresh.record.tracks[0].net == "N"             # literal, as before
    assert fresh.record.pads == ["A.1", "B.1"]           # now exact next time


def test_one_stored_record_cannot_satisfy_two_fresh_units():
    """Two separate bridges between the same pads (two units) need two records;
    the second one is ADDED rather than reusing the matched record."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10), _pad("2", "N", 10, 20)],
            "R2": [_pad("1", "N", 20, 10), _pad("2", "N", 20, 20)]}
    items = [_track(10, 10, 20, 10, "N"), _track(10, 20, 20, 20, "N")]
    plan = plan_internode_reread(_Board(fps, pads), _cfg(), _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps)
    assert len(plan.added) == 2
    assert plan.added[0].signature != plan.added[1].signature


# ── applying the plan ─────────────────────────────────────────────────────

def test_apply_plan_replaces_in_place_and_appends_without_removing():
    board, fps, items = _area()
    first = _existing(name="first__a__b", pads=("A.1", "B.1"),
                      tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                                            end_along_mm=99.0, end_across_mm=0.0,
                                            width_mm=0.25, layer="F.Cu",
                                            net_from_role="A", net_from_role_pad="1")])
    other = NetTrace(net="OTHER", anchor_role="Z")
    cfg = _cfg([other, first])
    plan = plan_internode_reread(board, cfg, _tree(net_trace_refs=("first__a__b",)),
                                 area_items=items, area_footprints=fps)
    after = apply_reread_plan(cfg, plan)
    # position preserved (the update stays where it was), the rest untouched
    assert [nt.net for nt in after.net_traces] == ["OTHER", "N"]
    assert after.net_traces[1].name == "first__a__b"
    assert after.net_traces[1].tracks[0].end_along_mm == 10.0
    assert cfg.net_traces[1].tracks[0].end_along_mm == 99.0   # input untouched


# ── report ────────────────────────────────────────────────────────────────

def test_report_lines_list_every_category_with_full_names():
    board, fps, items = _area()

    # a matching record (unchanged) + a record the board no longer has
    cfg = _cfg([_existing(name="match__a__b"),
                _existing(name="gone__a__c", pads=("A.1", "Z.9"))])
    plan = plan_internode_reread(board, cfg,
                                 _tree(net_trace_refs=("match__a__b", "gone__a__c")),
                                 area_items=items, area_footprints=fps)
    text = "\n".join(reread_report_lines("fpga", plan))
    assert "Tree 'fpga': inter-node copper re-read." in text
    assert "unchanged:  1" in text
    assert ("not found:  gone__a__c — the copper is no longer on the board, "
            "the record is kept") in text

    # a fresh tree: the same copper is ADDED (full name, never abbreviated)
    plan2 = plan_internode_reread(board, _cfg(), _tree(net_trace_refs=()),
                                  area_items=items, area_footprints=fps)
    text2 = "\n".join(reread_report_lines("fpga", plan2))
    assert "added:      n__a__b" in text2


def test_report_lines_show_the_before_and_after_counts():
    board, fps, items = _area()
    cfg = _cfg([_existing(tracks=[TemplateTrack(
        start_along_mm=0.0, start_across_mm=0.0, end_along_mm=99.0,
        end_across_mm=0.0, width_mm=0.25, layer="F.Cu",
        net_from_role="A", net_from_role_pad="1")])])
    plan = plan_internode_reread(board, cfg, _tree(),
                                 area_items=items, area_footprints=fps)
    text = "\n".join(reread_report_lines("fpga", plan))
    assert "updated:    n__a__b (was 1 tracks / 0 via, now 1 / 0)" in text


# ── Э5: the dialog's capture and the re-read are ONE mechanism ────────────

def test_created_and_reread_copper_are_identical():
    """THE cross-cutting contract of Э5 (the design's "one mechanism"): the
    copper a tree CREATES — through the very path the "Extract tree" dialog
    uses, detect_inter_cluster_nets + capture_units — and the copper a RE-READ
    finds must be the SAME. Otherwise a created and a re-read tree would carry
    different material and nobody would notice until it hurt (the same disease
    the "Move to…" fix cured). Here the re-read of the same board finds the
    created record UNCHANGED: no addition, no update, no new node."""
    board, fps, items = _area()
    clusters = [ReReadCluster(cluster="A", sheet="Ch", entity_name=None,
                              cell="a", profile_key=None, refs=["R1"]),
                ReReadCluster(cluster="B", sheet="Ch", entity_name=None,
                              cell="b", profile_key=None, refs=["R2"])]
    rows = detect_inter_cluster_nets(items + fps, clusters, adapter=board)
    assert len(rows) == 1

    # node_sheet_by_ref is the dialog's own knowledge of its clusters' sheets
    # (Э3): these clusters carry none, and the tree's nodes carry none either —
    # the two must agree, which is the whole point of this test.
    captures, warnings = capture_units(
        board, [rows[0].unit], area_footprints=fps,
        node_by_ref={"R1": "A", "R2": "B"},
        node_sheet_by_ref={"R1": None, "R2": None}, existing_names=[])
    assert warnings == [] and len(captures) == 1
    created = captures[0]

    # what the dialog would have built: the two cluster nodes + a net_trace
    # node whose ref IS the created record's identity
    tree = _tree(net_trace_refs=(created.identity,))
    plan = plan_internode_reread(board, _cfg([created.record]), tree,
                                 area_items=items, area_footprints=fps)
    assert plan.added == [] and plan.updated == [] and plan.missing == []
    assert plan.unchanged == [created.identity]
    assert created.record.pads == ["A.1", "B.1"]
    assert created.record.name == "n__a__b"


def test_zones_are_reported_as_not_read():
    board, fps, items = _area()
    plan = plan_internode_reread(board, _cfg(), _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps,
                                 zone_count=3)
    assert any("zones are not read" in w for w in plan.warnings)
    assert any("zones are not read" in line
               for line in reread_report_lines("t", plan))


# ── nested sheets: the live shape (Э1/Э3/Э4 of
# plan_2026_09_15_internode_copper_sheets_and_nets) ───────────────────────
#
# Three channels, the SAME Cluster and Role tags in every one of them (the
# schematic reuses one hierarchical sheet per channel), and the DAC/OpAmp
# components one level DEEPER than the PIF capacitors:
#
#   C134   C_OUT_BYPASS  PIF_DVDD   ['Channel_0']
#   IC2    AD_DAC        DAC_BUF    ['Channel_0', 'DAC']
#   R43    AD_OUT        DAC_OUT    ['Channel_0', 'DAC']
#   C155 / IC3 / R44     same tags  ['Channel_1' ...]
#   C176 / IC4 / R45     same tags  ['Channel_2' ...]
#
# The tree's nodes carry the CHANNEL sheet (Channel_0) — that is the value
# Entity.sheet holds, and the value a NEW record must store as its anchor_sheet
# (Э3): the leaf 'DAC' exists in all three channels and narrows nothing.

_X_STEP_MM = 100.0


def _sheet_map(channels=3) -> dict[str, str]:
    """{uuid: name} for the fixture. Every channel has its own 'Channel_N' with
    a 'DAC' and an 'OpAmp' sub-sheet inside it — the uuids are per-channel, the
    NAMES repeat, exactly like the live project."""
    names: dict[str, str] = {}
    for ch in range(channels):
        names[f"ch{ch}"] = f"Channel_{ch}"
        names[f"dac{ch}"] = "DAC"
        names[f"opamp{ch}"] = "OpAmp"
    return names


def _nested_fp(ref, role, cluster, x_mm, y_mm, channel=0, sub=None):
    """A footprint on 'Channel_N', or one level deeper on a sub-sheet of it. The
    LAST uuid of sheet_path_uuids is the component's own — exactly the one
    resolve_sheet_path_names cuts off."""
    fp = _fp(ref, role=role, cluster=cluster, x_mm=x_mm, y_mm=y_mm)
    uuids = [f"ch{channel}"]
    if sub:
        uuids.append(f"{sub}{channel}")
    uuids.append(f"own-{ref}")
    fp.sheet_path_uuids = tuple(uuids)
    return fp


def _nested_board():
    """(board, footprints) for the three channels: the capacitor at x+10, the
    DAC at x+20 (its pads at y=10 and y=20) and R43 at x+30, y=20."""
    fps: list = []
    pads: dict = {}
    for ch in range(3):
        x = _X_STEP_MM * ch
        cap = _nested_fp(f"C{134 + ch * 21}", "C_OUT_BYPASS", "PIF_DVDD",
                         x + 10, 10, ch)
        dac = _nested_fp(f"IC{2 + ch}", "AD_DAC", "DAC_BUF", x + 20, 10, ch,
                         sub="dac")
        out = _nested_fp(f"R{43 + ch}", "AD_OUT", "DAC_OUT", x + 20, 20, ch,
                         sub="dac")
        fps += [cap, dac, out]
        pads[cap.ref] = [_pad("1", "+3V3_DVDD", x + 10, 10)]
        pads[dac.ref] = [_pad("3", "+3V3_DVDD", x + 20, 10),
                         _pad("1", "OA_OUT", x + 20, 20)]
        pads[out.ref] = [_pad("1", "OA_OUT", x + 30, 20)]
    return _Board(fps, pads), fps


def _bridge_pif_to_dac(ch):
    """The channel's +3V3_DVDD bridge: the PIF capacitor on 'Channel_N' to the
    NESTED DAC pad."""
    x = _X_STEP_MM * ch
    return _track(x + 10, 10, x + 20, 10, "+3V3_DVDD")


def _bridge_dac_to_out(ch):
    """The channel's OA_OUT bridge between TWO nested components (both on
    ['Channel_N', 'DAC'])."""
    x = _X_STEP_MM * ch
    return _track(x + 20, 20, x + 30, 20, "OA_OUT")


def _channel_entities(channels=(0,)):
    """One Entity per node: the tree node's sheet is the CHANNEL, not the
    nested sub-sheet the component itself lives on."""
    out = []
    for ch in channels:
        for cluster in ("PIF_DVDD", "DAC_BUF", "DAC_OUT"):
            out.append(Entity(name=f"{cluster.lower()}_{ch}", cell="c",
                              cluster=cluster, sheet=f"Channel_{ch}"))
    return out


def _tree_of(entities, net_trace_refs=()):
    nodes = [TreeNode(ref=e.name, kind="placement", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])
             for e in entities]
    nodes += [TreeNode(ref=r, kind="net_trace", xy=None, polar=None,
                       rotation=0.0, name=None, group=None, children=[])
              for r in net_trace_refs]
    return Tree(name="ch0_dac_buf", anchor=TreeAnchor(is_origin=True), nodes=nodes)


def _nested_cfg(entities=(), net_traces=()) -> Config:
    return Config(entities=list(entities), net_traces=list(net_traces))


def test_c1_a_nested_component_matches_the_node_of_any_path_segment():
    """С1 — a component on ['Channel_0', 'DAC'] belongs to the node whose sheet
    is 'Channel_0': the sheet is one of the SEGMENTS of its path, not only its
    leaf. With the leaf rule this bridge is found by NOBODY (every unit is
    FOREIGN) and the re-read reports an empty result — the live complaint."""
    board, fps = _nested_board()
    entities = _channel_entities((0,))
    plan = plan_internode_reread(board, _nested_cfg(entities), _tree_of(entities),
                                 area_items=[_bridge_pif_to_dac(0)],
                                 area_footprints=fps, sheet_names=_sheet_map())
    assert [c.identity for c in plan.added] == ["3v3_dvdd__dac_buf__pif_dvdd"]
    record = plan.added[0].record
    assert record.pads == ["AD_DAC.3", "C_OUT_BYPASS.1"]
    assert record.anchor_role == "C_OUT_BYPASS" and record.anchor_pad == "1"
    assert record.anchor_cluster == "PIF_DVDD"
    assert record.anchor_sheet == "Channel_0"
    # only channel 0's three components match these nodes; 1 and 2 are FOREIGN
    assert plan.matched_components == 3 and plan.unmatched_components == 6
    text = "\n".join(reread_report_lines("ch0_dac_buf", plan))
    assert "not taken:" not in text
    assert "no component of the area matched any node" not in text


def test_c2_the_same_cluster_on_another_channel_is_not_this_node():
    """С2 — 'Channel_1' has the SAME Cluster tag, so matching by Cluster alone
    would swallow its copper. It must stay foreign: the node says Channel_0."""
    board, fps = _nested_board()
    entities = _channel_entities((0,))
    plan = plan_internode_reread(
        board, _nested_cfg(entities), _tree_of(entities),
        area_items=[_bridge_pif_to_dac(0), _bridge_pif_to_dac(1)],
        area_footprints=fps, sheet_names=_sheet_map())
    assert [c.identity for c in plan.added] == ["3v3_dvdd__dac_buf__pif_dvdd"]
    assert plan.discarded.get("foreign") == 1
    captured = {pad for c in plan.added for pad in c.record.pads}
    assert "C_OUT_BYPASS.1" in captured and "AD_DAC.3" in captured


def test_c3_two_nodes_of_one_cluster_matching_one_component_are_reported_not_chosen():
    """С3 — two nodes of ONE Cluster on 'Channel_0' and 'DAC': a component on
    ['Channel_0', 'DAC'] matches BOTH. The design's rule is not to guess: the
    component is left unmatched and the report names it and both nodes."""
    board, fps = _nested_board()
    entities = [Entity(name="dac_buf_ch0", cell="c", cluster="DAC_BUF",
                       sheet="Channel_0"),
                Entity(name="dac_buf_far", cell="c", cluster="DAC_BUF",
                       sheet="DAC")]
    plan = plan_internode_reread(board, _nested_cfg(entities), _tree_of(entities),
                                 area_items=[_bridge_dac_to_out(0)],
                                 area_footprints=fps, sheet_names=_sheet_map())
    assert plan.added == []
    assert any("IC2" in w for w in plan.warnings)
    text = "\n".join(reread_report_lines("ch0_dac_buf", plan))
    assert "IC2" in text
    assert "DAC_BUF/Channel_0" in text and "DAC_BUF/DAC" in text


def test_a_sheet_key_outranks_the_sheet_less_key():
    """Э1 priority — a node WITH a sheet beats a sheet-less node of the same
    Cluster: the component names the sheet-specific node, so its record anchors
    on 'Channel_0' rather than on nothing."""
    board, fps = _nested_board()
    entities = [Entity(name="dac_buf_ch0", cell="c", cluster="DAC_BUF",
                       sheet="Channel_0"),
                Entity(name="dac_out_ch0", cell="c", cluster="DAC_OUT",
                       sheet="Channel_0"),
                Entity(name="dac_buf_nosheet", cell="c", cluster="DAC_BUF",
                       sheet=None)]
    plan = plan_internode_reread(board, _nested_cfg(entities), _tree_of(entities),
                                 area_items=[_bridge_dac_to_out(0)],
                                 area_footprints=fps, sheet_names=_sheet_map())
    assert len(plan.added) == 1
    assert plan.added[0].record.anchor_sheet == "Channel_0"


def test_c8_a_new_record_stores_the_node_sheet_and_its_anchor_narrows_to_one():
    """С8 — the record stores the TREE NODE's sheet ('Channel_0'), and the
    resolver's own sheet -> cluster cascade then narrows the anchor role to
    exactly the channel-0 component. The leaf ('DAC') exists in all three
    channels: it would leave three candidates and apply would refuse the record."""
    board, fps = _nested_board()
    entities = [e for e in _channel_entities((0,))
                if e.cluster in ("DAC_BUF", "DAC_OUT")]
    plan = plan_internode_reread(board, _nested_cfg(entities), _tree_of(entities),
                                 area_items=[_bridge_dac_to_out(0)],
                                 area_footprints=fps, sheet_names=_sheet_map())
    assert len(plan.added) == 1
    record = plan.added[0].record
    assert record.anchor_role == "AD_DAC" and record.anchor_pad == "1"
    assert record.anchor_cluster == "DAC_BUF"
    assert record.anchor_sheet == "Channel_0"       # NOT the leaf 'DAC'
    fp = resolve_footprint_by_role(board, record.anchor_role, record.anchor_sheet,
                                   record.anchor_cluster, _sheet_map(),
                                   label="test")
    assert fp.ref == "IC2"


def test_c9_the_dialog_path_stores_the_cluster_sheet_too():
    """С9 — the Extract dialog's own capture path (detect_inter_cluster_nets +
    capture_units) stores the same anchor_sheet: its clusters are keyed by the
    channel sheet, and that is what it hands to capture_units."""
    board, fps = _nested_board()
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["IC2"]),
                ReReadCluster(cluster="DAC_OUT", sheet="Channel_0",
                              entity_name=None, cell="dac_out",
                              profile_key=None, refs=["R43"])]
    rows = detect_inter_cluster_nets([_bridge_dac_to_out(0)] + fps, clusters,
                                     adapter=board)
    assert len(rows) == 1
    captures, warnings = capture_units(
        board, [rows[0].unit], area_footprints=fps,
        node_by_ref={"IC2": "DAC_BUF", "R43": "DAC_OUT"},
        node_sheet_by_ref={"IC2": "Channel_0", "R43": "Channel_0"},
        sheet_names=_sheet_map(), existing_names=[])
    assert warnings == [] and len(captures) == 1
    assert captures[0].record.anchor_role == "AD_DAC"
    assert captures[0].record.anchor_sheet == "Channel_0"   # NOT the leaf 'DAC'


def test_an_anchor_whose_node_is_unknown_is_skipped_with_a_warning():
    """Э3 — a caller that cannot name the anchor's node gets a SKIP, never a
    silent record anchored on a sheet that does not narrow."""
    board, fps = _nested_board()
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["IC2"]),
                ReReadCluster(cluster="DAC_OUT", sheet="Channel_0",
                              entity_name=None, cell="dac_out",
                              profile_key=None, refs=["R43"])]
    rows = detect_inter_cluster_nets([_bridge_dac_to_out(0)] + fps, clusters,
                                     adapter=board)
    captures, warnings = capture_units(
        board, [rows[0].unit], area_footprints=fps,
        node_by_ref={"IC2": "DAC_BUF", "R43": "DAC_OUT"},
        node_sheet_by_ref={"R43": "Channel_0"},      # the anchor pad's node missing
        sheet_names=_sheet_map(), existing_names=[])
    assert captures == []
    assert any("belongs to no node" in w for w in warnings)


def test_c10_an_existing_record_keeps_its_own_anchor_sheet():
    """С10 — Э3 is about NEW records only: every branch that matched an existing
    record (unchanged and updated) keeps the identity it was stored with,
    anchor_sheet included. Here the stored sheet differs from the node's sheet
    AND still resolves, because the anchor role exists once on the board."""
    board, fps = _unique_role_board()
    entities = [Entity(name="dac_buf_ch0", cell="c", cluster="DAC_BUF",
                       sheet="Channel_0"),
                Entity(name="oa_ch0", cell="c", cluster="OA", sheet="Channel_0")]
    unit = _track(120.0, 20.0, 120.0, 30.0, "OA_OUT")       # U7.4 -> U8.1
    fresh = plan_internode_reread(board, _nested_cfg(entities), _tree_of(entities),
                                  area_items=[unit], area_footprints=fps,
                                  sheet_names=_sheet_map())
    assert len(fresh.added) == 1
    created = fresh.added[0].record
    assert created.anchor_role == "OA" and created.anchor_pad == "4"
    assert created.anchor_sheet == "Channel_0"          # a NEW record: node sheet

    stored = dataclasses.replace(created, name="kept__oa", anchor_sheet="OpAmp")
    cfg = _nested_cfg(entities, [stored])
    tree = _tree_of(entities, net_trace_refs=("kept__oa",))

    # unchanged: the stored geometry already matches the board
    same = plan_internode_reread(board, cfg, tree, area_items=[unit],
                                 area_footprints=fps, sheet_names=_sheet_map())
    assert same.unchanged == ["kept__oa"]
    assert apply_reread_plan(cfg, same).net_traces[0].anchor_sheet == "OpAmp"

    # updated: the geometry moved — the refreshed record keeps the identity
    moved = dataclasses.replace(
        stored, tracks=[dataclasses.replace(stored.tracks[0], end_along_mm=99.0)])
    plan = plan_internode_reread(board, _nested_cfg(entities, [moved]), tree,
                                 area_items=[unit], area_footprints=fps,
                                 sheet_names=_sheet_map())
    assert len(plan.updated) == 1
    refreshed, _counts = plan.updated[0]
    assert refreshed.record.anchor_sheet == "OpAmp"     # its own, not 'Channel_0'
    assert refreshed.record.anchor_role == "OA" and refreshed.record.name == "kept__oa"


def _unique_role_board():
    """U7 (nested on 'Channel_0'/'OpAmp', Role OA — ONCE on the board, so it
    resolves with or without a sheet) and U8 (nested on 'Channel_0'/'DAC', Role
    AD_DAC). U7 sorts FIRST among the bridge's pads, so it IS the unit's anchor:
    a stored record anchored on U7 keeps its own sheet 'OpAmp' — a sheet that
    differs from its node's 'Channel_0' and still resolves live, which is what
    makes "an existing record keeps its anchor_sheet" testable (С10)."""
    u7 = _nested_fp("U7", "OA", "OA", 120.0, 10.0, 0, sub="opamp")
    u8 = _nested_fp("U8", "AD_DAC", "DAC_BUF", 120.0, 30.0, 0, sub="dac")
    pads = {"U7": [_pad("4", "OA_OUT", 120.0, 20.0)],
            "U8": [_pad("1", "OA_OUT", 120.0, 30.0)]}
    return _Board([u7, u8], pads), [u7, u8]


def test_c11_the_report_names_what_was_discarded_and_when_nothing_matched():
    """С11 — the report's new lines: the discarded counters (only non-zero ones),
    and — when NOT ONE component of the area matched a node — the line that
    names the tree and what its nodes wait for. Both must be absent when they
    have nothing to say (see С1)."""
    board, fps = _nested_board()
    entities = _channel_entities((0,))
    plan = plan_internode_reread(
        board, _nested_cfg(entities), _tree_of(entities),
        area_items=[_bridge_pif_to_dac(0), _bridge_pif_to_dac(1)],
        area_footprints=fps, sheet_names=_sheet_map())
    text = "\n".join(reread_report_lines("ch0_dac_buf", plan))
    assert "not taken:" in text and "foreign 1" in text
    assert "no component of the area matched any node" not in text

    far = [Entity(name="dac_buf_far", cell="c", cluster="DAC_BUF",
                  sheet="Sheet_9")]
    plan2 = plan_internode_reread(board, _nested_cfg(far), _tree_of(far),
                                  area_items=[_bridge_pif_to_dac(0)],
                                  area_footprints=fps, sheet_names=_sheet_map())
    assert plan2.matched_components == 0 and plan2.unmatched_components == 9
    text2 = "\n".join(reread_report_lines("ch0_dac_buf", plan2))
    assert "no component of the area matched any node of tree 'ch0_dac_buf'" in text2
    assert "DAC_BUF/Sheet_9" in text2
    assert "not taken:" in text2 and "foreign 1" in text2


def test_c12_created_and_reread_copper_are_identical_on_nested_sheets():
    """С12 — the "one mechanism" contract (Э5 of the core plan) on the nested
    shape: the copper the dialog CREATES and the copper a RE-READ finds must be
    the same record, or a re-read would rewrite what the dialog just wrote."""
    board, fps = _nested_board()
    entities = [e for e in _channel_entities((0,))
                if e.cluster in ("DAC_BUF", "DAC_OUT")]
    clusters = [ReReadCluster(cluster="DAC_BUF", sheet="Channel_0",
                              entity_name=None, cell="dac_buf",
                              profile_key=None, refs=["IC2"]),
                ReReadCluster(cluster="DAC_OUT", sheet="Channel_0",
                              entity_name=None, cell="dac_out",
                              profile_key=None, refs=["R43"])]
    rows = detect_inter_cluster_nets([_bridge_dac_to_out(0)] + fps, clusters,
                                     adapter=board)
    captures, _warnings = capture_units(
        board, [rows[0].unit], area_footprints=fps,
        node_by_ref={"IC2": "DAC_BUF", "R43": "DAC_OUT"},
        node_sheet_by_ref={"IC2": "Channel_0", "R43": "Channel_0"},
        sheet_names=_sheet_map(), existing_names=[])
    created = captures[0]

    plan = plan_internode_reread(
        board, _nested_cfg(entities, [created.record]),
        _tree_of(entities, net_trace_refs=(created.identity,)),
        area_items=[_bridge_dac_to_out(0)], area_footprints=fps,
        sheet_names=_sheet_map())
    assert plan.added == [] and plan.updated == [] and plan.missing == []
    assert plan.unchanged == [created.identity]
