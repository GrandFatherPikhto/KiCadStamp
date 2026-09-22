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

Rows and their pre-fix cost on a 332-footprint fake board — the table now goes to
the LEAVES, which is the lesson learned three times over (Т2-4 measured the CALL,
Т2-4а the BRANCHES, and Кj found that branch 4 has two SUB-branches and only the
fallback one had been threaded):

  1  the node's OWN anchor     base_anchor            1 sweep   -> 0
  2  the tree's own anchor     parent_node=None       1 sweep   -> 0
  3  a MOUNT parent            kind == "mount"        2 sweeps  -> 0
  4  any other parent NODE     read_record_live_pose  2 sweeps  -> 0
  5  an ENTITY parent          sub-branch of 4        5 sweeps  -> 0   (Кj)
                               (cell+cluster)         1665 field scans -> 0

Cells left empty ON PURPOSE: nothing here asserts the sub-seams that have no
snapshot parameter of their own (tree_pivot_offset's pivot half,
resolve_entity_live_position, point chains, ClonePositionCalculator) — nor the
two whole-board reads on `_live_cluster_frame`'s FAILURE path
(role_multiplicity_in_cluster and the "is the cluster on the board at all"
check), which run once, right before a fatal message. And nothing here asserts
the ONE cost the snapshot does NOT remove: `adapter.refresh_board()` per
Entity-typed parent. All of them are named in the code beside the door's sign —
the sign does not claim them, and the re-hang's sign NAMES that refresh instead
(its own cell lives in tests/gui/test_board_door_offenders.py).
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
ENTITY_ROLES = ["U", "C1", "C2", "R1", "R2"]   # an ordinary five-slot cell
ENTITY_CLUSTER = "AD_DAC_0"
# The Entity rig's five roles must NOT sit on ANCHOR_INDEX: the anchor rig's
# footprint is one object with one Role, so an overlap would have the second rig
# overwrite the first one's fields (found by this very cell going red with the
# anchor cells — a fake board cannot hold two Roles on one footprint).
ENTITY_INDEX = 5

class _CountingAdapter:
    """Counts the reads a role resolve makes: one get_footprints() plus one
    get_field_value() per footprint — the sweep the snapshot is meant to replace."""

    def __init__(self):
        self.sweeps = 0
        self.field_scans = 0
        self.live_reads = 0
        self.board_refreshes = 0
        self._fps = [
            Footprint(ref=f"R{i}", uuid=f"uuid-{i}", layer=BoardLayer.BL_F_Cu,
                      position=Vector2.from_xy_mm(i * 1.0, i * 2.0), angle_deg=0.0)
            for i in range(FOOTPRINTS)]
        self._fields = {self._fps[ANCHOR_INDEX].uuid: {
            ROLE_FIELD_NAME: ANCHOR_ROLE, CLUSTER_FIELD_NAME: ANCHOR_CLUSTER}}
        # The ENTITY rig's cell, so its five roles resolve on BOTH paths.
        for offset, role in enumerate(ENTITY_ROLES):
            self._fields[self._fps[ENTITY_INDEX + offset].uuid] = {
                ROLE_FIELD_NAME: role, CLUSTER_FIELD_NAME: ENTITY_CLUSTER}

    def get_footprints(self):
        self.sweeps += 1
        return list(self._fps)

    def get_field_value(self, fp, field):
        self.field_scans += 1
        return self._fields.get(getattr(fp, "uuid", None), {}).get(field, "")

    def get_selected_items(self):
        return []

    def get_footprint(self, ref):
        """The adapter's CURRENT generation. Counted apart from a sweep because
        that is what it is: in the real adapter it is a scan of the cache
        `refresh_board()` filled, never a new IPC read."""
        self.live_reads += 1
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def refresh_board(self):
        """Counted, never hidden: `_live_cluster_frame` refreshes first (bug of
        2026-09-10) and that refresh is not a sweep — it DROPS the caches, so
        the next whole-board read is paid again."""
        self.board_refreshes += 1


def _snapshot(adapter):
    """One Selected per footprint — the shape `connection.snapshot` owns. It
    carries BOTH rigs (the anchor role on its own footprint, the Entity cell's
    roles in their own cluster), so each cell's snapshot is as good as the board
    it describes."""
    rows = []
    for index, fp in enumerate(adapter._fps):
        entity_offset = index - ENTITY_INDEX
        if index == ANCHOR_INDEX:
            role, cluster = ANCHOR_ROLE, ANCHOR_CLUSTER
        elif 0 <= entity_offset < len(ENTITY_ROLES):
            role, cluster = ENTITY_ROLES[entity_offset], ENTITY_CLUSTER
        else:
            role, cluster = f"DECOY_{index}", f"D{index}"
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


def _entity_rig(tree):
    """(cfg, record, entity_name, parent_node) of ONE Entity-typed parent — the
    `kind == "placement"` SUB-branch of row 4, i.e. the TYPICAL tree node.

    The cell carries what `cell_mount_offset` / `resolve_pad_mount` and the
    cell<->world frame fit actually read: `anchor_xy` None (the mount comes from
    the anchor ROLE's own slot), `anchor_pad` None (no live pad re-derivation)
    and each component's along/across offsets."""
    cell = SimpleNamespace(
        name="dac_buf", layer="F.Cu", anchor_role=ENTITY_ROLES[0], anchor_xy=None,
        anchor_pad=None,
        components=[SimpleNamespace(role=role, offset_along_mm=index * 5.0,
                                    offset_across_mm=0.0)
                    for index, role in enumerate(ENTITY_ROLES)])
    entity = SimpleNamespace(name="dac_buf_channel_0", cell="dac_buf",
                             cluster=ENTITY_CLUSTER, sheet="")
    record = SimpleNamespace(kind="placement", obj=entity, name=entity.name)
    cfg = SimpleNamespace(trees=[tree], cells={"dac_buf": cell},
                          entities={entity.name: entity})
    parent_node = td_mod.TreeNode(ref=entity.name, kind="placement", xy=None,
                                  polar=None, rotation=0.0, name=None, group=None,
                                  children=[])
    return cfg, record, entity.name, parent_node


def test_leaf_4a_an_entity_parent_resolves_from_the_snapshot(monkeypatch):
    """Cell 5 — the ENTITY sub-branch of row 4 (Кj of the acceptance).

    A `kind="placement"` parent whose Entity HAS a cell and a cluster goes to
    `_live_cluster_frame`, and until Кj that function took no `snapshot` at all:
    the identity question cost ONE whole-board sweep PER CELL ROLE (five for this
    ordinary cell, 1665 field reads on a 332-footprint board) while the snapshot
    sat in the caller's hand — the typical tree node paying the most.

    Both directions as above; and the POSITION is asserted to come from the
    adapter's own current generation (one `get_footprint` per role, the count
    `live_reads` pins), never from the snapshot's frozen `Selected.fp`.

    Mutation checks: drop the `snapshot=` from the resolver call inside
    `_live_cluster_frame` and this fails; drop it one level UP, on
    `read_record_live_pose`'s call to `_live_cluster_frame`, and it fails too
    (that is m19/m20 of the mutation run)."""
    adapter, tree = _CountingAdapter(), _tree()
    cfg, record, ref, parent_node = _entity_rig(tree)
    monkeypatch.setattr(td_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (record, False))

    pose = td_mod._resolve_node_base_pose(cfg, adapter, {}, tree, parent_node,
                                          None, snapshot=_snapshot(adapter))

    assert pose[0] is not None, "the Entity's live cluster must give a position"
    assert adapter.sweeps == 0, \
        "the Entity sub-branch swept the board even though a snapshot was given"
    assert adapter.field_scans == 0, \
        "the Entity sub-branch scanned fields even though a snapshot was given"
    assert adapter.live_reads == len(ENTITY_ROLES), \
        ("every cell role must be read back from the adapter's CURRENT "
         "generation (one get_footprint per role), not from the frozen snapshot")
    assert adapter.board_refreshes == 1, \
        "the 2026-09-10 refresh stays: it is what makes those reads current"

    swept, swept_tree = _CountingAdapter(), _tree()
    swept_cfg, _record, _ref, swept_parent = _entity_rig(swept_tree)
    monkeypatch.setattr(td_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (record, False))
    swept_pose = td_mod._resolve_node_base_pose(swept_cfg, swept, {}, swept_tree,
                                                swept_parent, None)
    assert swept_pose[0] is not None
    assert swept.sweeps == len(ENTITY_ROLES), (
        "the historical Entity path must still sweep once per cell role — "
        "otherwise this cell would pass without the threading existing")
    assert swept.live_reads == 0, \
        "without a snapshot the refs come FROM the sweep, not from get_footprint"
