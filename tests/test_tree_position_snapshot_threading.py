# tests/test_tree_position_snapshot_threading.py
"""`snapshot=` reaches EVERY branch that resolves a ROLE anchor — the several
branches of `_resolve_node_base_pose`, measured branch by branch (Т2-4а of
plan_2026_09_22_live_adapter_class).

Why this file exists at all: Т2-4 measured the CALL CLASS ("one re-hang"), and
that call reaches ONE branch of the four. The money is spent by the BRANCH, so a
cell here names a branch (rule 35: one cell per branch, not one per call), and
each cell proves BOTH directions:

  with a snapshot     the branch resolves the role WITHOUT a board sweep
  without a snapshot  the same branch sweeps (the historical path, unchanged)

The second half is what keeps the first from being vacuous: a branch that never
swept anyway would pass it without the threading existing at all.

Rows and their pre-fix cost on a 332-footprint fake board (the probe's table,
diagnostics/probe_2026_09_22_rehang_offset_cost.py):

  1  the node's OWN anchor     base_anchor           1 sweep   -> 0
  2  the tree's own anchor     parent_node=None      1 sweep   -> 0
  3  a MOUNT parent            kind == "mount"       2 sweeps  -> 0
  4  any other parent NODE     read_record_live_pose 2 sweeps  -> 0

Cells left empty ON PURPOSE: nothing here asserts the sub-seams that have no
snapshot parameter of their own (tree_pivot_offset's pivot half, entity
placement, point chains, ClonePositionCalculator). They are named in the code
beside the door's sign and in the handoff — the sign does not claim them.
"""
from types import SimpleNamespace

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2

import gui.docks.trees_dock as td_mod

FOOTPRINTS = 12                 # enough for a sweep to be visible, cheap to build
ANCHOR_ROLE = "R_CLK"
ANCHOR_CLUSTER = "FPGA_OSCILL"
ANCHOR_INDEX = 3


class _CountingAdapter:
    """Counts the reads a role resolve makes: one get_footprints() plus one
    get_field_value() per footprint — the sweep the snapshot is meant to replace."""

    def __init__(self):
        self.sweeps = 0
        self.field_scans = 0
        self.live_reads = 0
        self._fps = [
            Footprint(ref=f"R{i}", uuid=f"uuid-{i}", layer=BoardLayer.BL_F_Cu,
                      position=Vector2.from_xy_mm(i * 1.0, i * 2.0), angle_deg=0.0)
            for i in range(FOOTPRINTS)]
        self._fields = {self._fps[ANCHOR_INDEX].uuid: {
            ROLE_FIELD_NAME: ANCHOR_ROLE, CLUSTER_FIELD_NAME: ANCHOR_CLUSTER}}

    def get_footprints(self):
        self.sweeps += 1
        return list(self._fps)

    def get_field_value(self, fp, field):
        self.field_scans += 1
        return self._fields.get(getattr(fp, "uuid", None), {}).get(field, "")

    def get_selected_items(self):
        return []

    def get_footprint(self, ref):
        self.live_reads += 1
        return next((fp for fp in self._fps if fp.ref == ref), None)


def _snapshot(adapter):
    """One Selected per footprint — the shape `connection.snapshot` owns."""
    rows = []
    for index, fp in enumerate(adapter._fps):
        role = ANCHOR_ROLE if index == ANCHOR_INDEX else f"DECOY_{index}"
        cluster = ANCHOR_CLUSTER if index == ANCHOR_INDEX else f"D{index}"
        rows.append(SimpleNamespace(role=role, cluster=cluster, fp=fp))
    return rows


def _tree():
    node = td_mod.TreeNode(ref="R_DEBUG", kind="external", xy=(1.0, 2.0),
                           polar=None, rotation=0.0, name=None, group=None,
                           children=[])
    return td_mod.Tree(
        name="probe_tree",
        anchor=td_mod.TreeAnchor(role=ANCHOR_ROLE,
                                 anchor_cluster=ANCHOR_CLUSTER),
        nodes=[node])


def _cfg(tree):
    return SimpleNamespace(trees=[tree])


def _expected_position_mm():
    return (ANCHOR_INDEX * 1.0, ANCHOR_INDEX * 2.0)


def _assert_resolved_anchor(pose):
    """The pose is the anchor footprint's (position, angle) — asserted rather than
    only "no exception", so a branch that quietly resolved nothing fails here."""
    pos = pose[0]
    assert (pos.x / 1_000_000, pos.y / 1_000_000) == _expected_position_mm()


def test_branch_1_the_nodes_own_anchor_resolves_from_the_snapshot():
    """Cell 1 — `base_anchor is not None` (a node's "Relative to component")."""
    adapter, tree = _CountingAdapter(), _tree()
    cfg = _cfg(tree)
    base_anchor = td_mod.TreeAnchor(role=ANCHOR_ROLE,
                                    anchor_cluster=ANCHOR_CLUSTER)

    _assert_resolved_anchor(td_mod._resolve_node_base_pose(
        cfg, adapter, {}, tree, None, base_anchor, snapshot=_snapshot(adapter)))
    assert adapter.sweeps == 0, "the snapshot branch swept the board"

    swept = _CountingAdapter()
    _assert_resolved_anchor(td_mod._resolve_node_base_pose(
        cfg, swept, {}, tree, None, base_anchor))
    assert swept.sweeps == 1, "the historical path must still sweep"


def test_branch_2_the_trees_own_anchor_resolves_from_the_snapshot():
    """Cell 2 — `parent_node is None` (the tree's own anchor, every mode)."""
    adapter, tree = _CountingAdapter(), _tree()
    cfg = _cfg(tree)

    _assert_resolved_anchor(td_mod._resolve_node_base_pose(
        cfg, adapter, {}, tree, None, None, snapshot=_snapshot(adapter)))
    assert adapter.sweeps == 0, "the snapshot branch swept the board"

    swept = _CountingAdapter()
    _assert_resolved_anchor(td_mod._resolve_node_base_pose(
        cfg, swept, {}, tree, None, None))
    assert swept.sweeps == 1, "the historical path must still sweep"


def test_branch_3_a_mount_parent_resolves_from_the_snapshot():
    """Cell 3 — a MOUNT parent: `tree_layout_base`'s anchor half AND the mount's
    own live resolver, i.e. the two sweeps the first pass left behind."""
    adapter, tree = _CountingAdapter(), _tree()
    cfg = _cfg(tree)
    mount = td_mod.TreeNode(ref="M1", kind="mount", xy=(1.0, 2.0), polar=None,
                            rotation=0.0, name=None, group=None, children=[],
                            anchor=td_mod.TreeAnchor(role=ANCHOR_ROLE,
                                                     anchor_cluster=ANCHOR_CLUSTER))

    td_mod._resolve_node_base_pose(cfg, adapter, {}, tree, mount, None,
                                   snapshot=_snapshot(adapter))
    assert adapter.sweeps == 0, "a mount parent still swept the board"

    swept = _CountingAdapter()
    td_mod._resolve_node_base_pose(cfg, swept, {}, tree, mount, None)
    assert swept.sweeps == 2, \
        "the historical mount path sweeps twice (tree base + the mount's own)"


def test_branch_4_a_record_parent_resolves_from_the_snapshot(monkeypatch):
    """Cell 4 — any other parent node, i.e. the branch that reaches
    `read_record_live_pose` (the record dispatcher: position AND rotation, two
    resolves per base).

    `_resolve_probe_ref` is stubbed here (it resolves the config's own records — a
    different mechanism than the cost this cell pins). The FIRST version of this
    cell drove `read_record_live_pose` directly — the seam, not the branch — and
    mutation m14 proved it: removing the dock's `snapshot=` left it green. Same
    trap as the probe of Т2-4, one level down; the cell drives the BRANCH now."""
    adapter, tree = _CountingAdapter(), _tree()
    cfg = _cfg(tree)
    record = SimpleNamespace(kind="chain", name="probe_chain", anchor_ref=None,
                             anchor_role=ANCHOR_ROLE, anchor_sheet=None,
                             anchor_cluster=ANCHOR_CLUSTER,
                             obj=SimpleNamespace())
    parent_node = td_mod.TreeNode(ref="probe_chain", kind="chain", xy=None,
                                  polar=None, rotation=0.0, name=None, group=None,
                                  children=[])
    monkeypatch.setattr(td_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (record, False))

    pose = td_mod._resolve_node_base_pose(cfg, adapter, {}, tree, parent_node,
                                          None, snapshot=_snapshot(adapter))
    _assert_resolved_anchor(pose)
    assert adapter.sweeps == 0, "a record parent still swept the board"

    swept = _CountingAdapter()
    td_mod._resolve_node_base_pose(cfg, swept, {}, tree, parent_node, None)
    assert swept.sweeps == 2, \
        "the historical record path sweeps twice (position + rotation)"
