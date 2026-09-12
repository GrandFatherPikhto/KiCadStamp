# tests/test_internode_capture.py
"""Э4 tests: capture / re-read a tree's inter-node copper as net_traces records
(plan_2026_09_12_internode_copper_core, stage Э4; design §4/§6/§11/§16).

Pure planning against a mock adapter: unit identity by pad SET, added/updated/
unchanged/missing bookkeeping, the legacy (no signature) fallback by net, the
(role, pad) representation of what is written, and the guarantee that nothing
is ever removed.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.config import Config, Entity, NetTrace, TemplateTrack, TemplateVia
from kicadstamp.domain.board import BoardLayer, Footprint, Track, Via
from kicadstamp.internode_capture import (
    apply_reread_plan,
    plan_internode_reread,
    reread_report_lines,
    tree_net_trace_identities,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


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


def test_zones_are_reported_as_not_read():
    board, fps, items = _area()
    plan = plan_internode_reread(board, _cfg(), _tree(net_trace_refs=()),
                                 area_items=items, area_footprints=fps,
                                 zone_count=3)
    assert any("zones are not read" in w for w in plan.warnings)
    assert any("zones are not read" in line
               for line in reread_report_lines("t", plan))
