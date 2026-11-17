# tests/gui/docks/test_trees_reread_board.py
"""Cells а, а1, а3–а6, г, д, е, з of plan_2026_11_16_reread_forest_from_board
(commit 1) — the WIRING of «Reread positions from board…».

Two halves are celled here:

* `build_plan` — pure arithmetic over what one read brought back. The cells pin the
  rules the plan insists on: only the anchor changes when the Origin moved, a tie
  leaves the anchor alone, «keep anchors» never touches it, and the MODULE rule
  (all content moved together → the module's `xy`; every which way → the module's
  `xy` stays and each content node is written). The marker an embedded tree is
  synced to must sit at its NEW place — the owner's anchor delta AND the module's
  own delta — or the sync lands on the old module position (Claude, 16.11);
* `read_forest_now` — ONE board pass: which reader sees a vote, which sees a node,
  and that the module CONTENT is read against the EFF base (`module_bases`), not
  against the marker.

PARITY CAVEAT (worth stating in words, or a green cell on a convenient tree lies
about the general case): `_resolve_live_offset(parent=None)` measures from the RAW
anchor (its angle, no pivot, no tree angle), while the layout measures from the
EFFECTIVE base. On a tree with its own `rotation` or a pivot the two diverge — the
divergence is measured in `done_2026_11_16_tree_anchor_read_s1.md` and is a
separate task. The top-node cell below therefore runs on a tree with neither, and
says so.
"""
import gui.docks.trees_reread_board as reread_mod
from gui.docks.trees_reread_board import build_plan, read_forest_now
from kicadstamp.domain.geometry import Vector2
from kicadstamp.forest_reread import (MODE_ABSORB, MODE_KEEP, OVERRIDE_KEEP,
                                      sync_embedded_anchor)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


# ── helpers: raw reads, spelled out per cell ────────────────────────────────

def _raw(*, angles=None, shifts=None, votes=None, nodes=(), modules=(),
         embedded=(), skips=()):
    angles = dict(angles or {})
    return {
        "angles": angles,
        "shifts": dict(shifts or {name: (0.0, 0.0) for name in angles}),
        "votes": dict(votes or {}),
        "nodes": list(nodes),
        "modules": list(modules),
        "embedded": list(embedded),
        "skips": list(skips),
    }


def _node(tree, ref, offset, stored, *, base_rot=0.0, source="anchor",
          anchor_tree="main", polar=False, rotation=0.0, stored_rotation=0.0,
          error=None):
    """`anchor_tree` is the tree whose ANCHOR moves this node's base — the OWNER,
    never the tree the node happens to live in (content of an embedded tree rides
    its owner's anchor, so the two names differ there)."""
    return {"tree": tree, "anchor_tree": anchor_tree, "ref": ref,
            "offset": offset, "rotation": rotation, "stored": stored,
            "stored_rotation": stored_rotation, "polar": polar,
            "base_rot": base_rot, "source": source, "error": error}


def _module(*, ref="power", tree="main", content="power", stored=(10.0, -40.0),
            parent_rot=0.0, eff_rot=0.0, top=(), polar=False):
    return {"tree": tree, "ref": ref, "content": content, "stored": tuple(stored),
            "polar": polar, "rotation": 0.0, "parent_rot": parent_rot,
            "eff_rot": eff_rot, "top": list(top)}


def _embedded(*, tree="power", owner="main", marker="power", angle=0.0,
              shift=(5.0, -60.0), raw_point=(0.0, 0.0), marker_mm=(6.0, -64.0),
              marker_rot=0.0, refusal=None):
    return {"tree": tree, "owner": owner, "marker": marker, "refusal": refusal,
            "angle": angle, "shift": tuple(shift), "raw_point": tuple(raw_point),
            "marker_mm": tuple(marker_mm), "marker_rot": marker_rot}


# ── а: a still board writes nothing and skips nothing ──────────────────────

def test_a_a_still_board_produces_an_empty_plan():
    raw = _raw(angles={"main": 0.0}, votes={"main": [("mh", 0.0, 0.0)]},
               nodes=[_node("main", "mh", (6.49, -84.99), (6.49, -84.99))])

    plan = build_plan(raw)

    assert plan.empty
    assert plan.anchors_moved == 0
    assert plan.synced_moved == 0


# ── а1: the Origin moved — the ANCHOR absorbs it, the nodes are not rewritten ─

def test_a1_an_origin_move_changes_only_the_anchor_row():
    """а1: every node stands 1/2 mm off its layout, so the anchor takes the delta
    and each node's own value comes out EXACTLY as stored — no node row at all."""
    raw = _raw(angles={"main": 0.0},
               votes={"main": [("mh", 1.0, 2.0), ("ic1", 1.0, 2.0)]},
               nodes=[_node("main", "mh", (1.0, 2.0), (0.0, 0.0)),
                      _node("main", "ic1", (5.0, 2.0), (4.0, 0.0))])

    plan = build_plan(raw)

    assert plan.anchors_moved == 1
    assert plan.anchors[0].now == (1.0, 2.0)
    assert plan.changed == 0


# ── а3: two equal groups — the anchor stays, every node is written ─────────

def test_a3_equal_groups_leave_the_anchor_alone_and_read_each_node():
    raw = _raw(angles={"t": 0.0},
               votes={"t": [("a", 1.0, 0.0), ("b", 1.0, 0.0),
                            ("c", 0.0, 2.0), ("d", 0.0, 2.0)]},
               nodes=[_node("t", "a", (1.0, 0.0), (0.0, 0.0))])

    plan = build_plan(raw)

    assert plan.anchors_moved == 0
    assert plan.changed == 1
    assert plan.changes[0].now == (1.0, 0.0)


# ── а5: «keep anchors» writes the nodes and never the anchor ───────────────

def test_a5_keep_anchors_writes_the_nodes_and_never_the_anchor():
    raw = _raw(angles={"t": 0.0},
               votes={"t": [("a", 1.0, 0.0), ("b", 1.0, 0.0)]},
               nodes=[_node("t", "a", (1.0, 0.0), (0.0, 0.0))])

    plan = build_plan(raw, mode=MODE_KEEP)

    assert plan.anchors_moved == 0
    assert plan.changed == 1


def test_a_per_tree_override_keeps_that_anchor_and_leaves_the_other():
    raw = _raw(angles={"a": 0.0, "b": 0.0},
               votes={"a": [("x", 1.0, 0.0)], "b": [("y", 1.0, 0.0)]})

    plan = build_plan(raw, overrides={"a": OVERRIDE_KEEP})

    assert {row.tree: row.moved for row in plan.anchors} == {"a": False,
                                                             "b": True}


# ── д: the module rule — content moved TOGETHER → the module's xy ──────────

def test_the_module_rule_writes_the_module_and_not_its_content():
    """д: both top nodes of `power` moved by (0, +3) — one rigid move of the
    module. The module's `xy` takes it; the content's `xy`s are already right
    relative to the module and must NOT go into the file."""
    raw = _raw(angles={"main": 0.0}, votes={"main": [("mh", 0.0, 0.0)]},
               modules=[_module(top=("J1", "J2"))],
               nodes=[_node("power", "J1", (0.0, 3.0), (0.0, 0.0),
                            source=["module", "power"]),
                      _node("power", "J2", (2.0, 3.0), (2.0, 0.0),
                            source=["module", "power"])])

    plan = build_plan(raw)

    assert [change.ref for change in plan.changes] == ["power"]
    assert plan.changes[0].was == (10.0, -40.0)
    assert plan.changes[0].now == (10.0, -37.0)


# ── е: content moved every which way — the module stays, nodes are written ─

def test_scattered_content_leaves_the_module_alone():
    raw = _raw(angles={"main": 0.0}, votes={"main": [("mh", 0.0, 0.0)]},
               modules=[_module(top=("J1", "J2"))],
               nodes=[_node("power", "J1", (0.0, 3.0), (0.0, 0.0),
                            source=["module", "power"]),
                      _node("power", "J2", (2.0, 0.5), (2.0, 0.0),
                            source=["module", "power"])])

    plan = build_plan(raw)

    assert [change.ref for change in plan.changes] == ["J1", "J2"]
    assert plan.changes[0].now == (0.0, 3.0)
    assert plan.changes[1].now == (2.0, 0.5)


def test_one_unreadable_content_node_means_every_which_way():
    """A module is «together» only when EVERY top node was read — one unread node
    must not let the rest drag the module's `xy` along."""
    raw = _raw(angles={"main": 0.0}, votes={"main": [("mh", 0.0, 0.0)]},
               modules=[_module(top=("J1", "J2"))],
               nodes=[_node("power", "J1", (0.0, 3.0), (0.0, 0.0),
                            source=["module", "power"]),
                      _node("power", "J2", None, (2.0, 0.0),
                            source=["module", "power"], error="mirrored")])

    plan = build_plan(raw)

    assert [change.ref for change in plan.changes] == ["J1"]


# ── а4: an embedded tree's own anchor is synced to the module marker ───────

def test_a4_an_embedded_anchor_is_synced_to_the_module_marker():
    """а4 (the live case): `power` carries its own Origin +(5, −60) while the
    module puts it at (6, −64). The sync REPLACES the shift with the difference
    from the raw point — it does not add to the old one."""
    raw = _raw(angles={"main": 0.0, "power": 0.0},
               votes={"main": [("mh", 0.0, 0.0)]},
               embedded=[_embedded()])

    plan = build_plan(raw)

    assert plan.synced_moved == 1
    assert plan.synced[0].was == (5.0, -60.0)
    assert plan.synced[0].now == (6.0, -64.0)


def test_the_marker_is_taken_AFTER_the_owner_shift_and_the_module_move():
    """Claude, 16.11: the owner's anchor moves by (1, 0) and the module by (−1, 3),
    so the marker sits at (6, −64) + (1, 0) + (−1, 3) = (6, −61). Syncing to the
    OLD marker would put `power` back on the module's previous place — the very
    «off by (−1, +4)» node this rule was written for."""
    raw = _raw(angles={"main": 0.0, "power": 0.0},
               votes={"main": [("mh", 1.0, 0.0)]},
               modules=[_module(stored=(6.0, -64.0), top=("J1",))],
               nodes=[_node("power", "J1", (0.0, 3.0), (0.0, 0.0),
                            source=["module", "power"])],
               embedded=[_embedded(shift=(0.0, 0.0))])

    plan = build_plan(raw)

    assert plan.synced[0].now == (6.0, -61.0)
    assert [change.ref for change in plan.changes] == ["power"]
    assert plan.changes[0].now == (5.0, -61.0)


def test_an_embedded_refusal_becomes_a_skip_with_its_reason():
    raw = _raw(angles={"power": 0.0},
               embedded=[_embedded(owner="", marker="",
                                   refusal="embedded anchor kept: not a point")])

    plan = build_plan(raw)

    assert plan.synced == []
    assert [skip.reason for skip in plan.skipped] == [
        "embedded anchor kept: not a point"]


# ── б/ж: what could not be read is a SKIP, never a silent absence ─────────

def test_a_node_that_could_not_be_read_is_a_skip_not_a_row():
    raw = _raw(angles={"main": 0.0},
               nodes=[_node("main", "mh", None, (0.0, 0.0), error="mirrored")])

    plan = build_plan(raw)

    assert plan.changed == 0
    assert [skip.reason for skip in plan.skipped] == ["mirrored"]


def test_a_tree_level_skip_travels_into_the_plan():
    raw = _raw(angles={}, skips=[{"tree": "x", "ref": "", "reason": "boom"}])

    plan = build_plan(raw)

    assert [skip.reason for skip in plan.skipped] == ["boom"]


# ── the read half: ONE board pass, and each node read against its OWN base ─

def _tree_node(ref, kind, *, xy=(0.0, 0.0), children=(), anchor=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children), anchor=anchor)


def _wire(monkeypatch, *, predicted, live, offsets, module_bases=None,
          anchor_poses=None):
    """The four seams of `read_forest_now`, with a call log for each."""
    calls = {"child": [], "offset": [], "anchor": [], "seen_snapshot": set()}

    def _anchor(adapter, cfg, tree, sheet_names, *, snapshot=None):
        calls["anchor"].append(tree.name)
        return (anchor_poses or {}).get(tree.name,
                                        (Vector2.from_xy(0, 0), 0.0))

    def _layout(tree, forest=None, **kwargs):
        bases = kwargs.get("module_bases")
        if bases is not None and module_bases:
            bases.update(module_bases)
        return {ref: (Vector2.from_xy_mm(x, y), deg)
                for ref, (x, y, deg) in predicted.items()}

    def _child(cfg, adapter, sheet_names, ref, kind, **kwargs):
        calls["child"].append(ref)
        calls["seen_snapshot"].add(kwargs.get("snapshot"))
        x, y, deg = live[ref]
        return Vector2.from_xy_mm(x, y), deg, False

    def _live(cfg, adapter, sheet_names, tree, parent, ref, kind, **kwargs):
        calls["offset"].append((tree.name,
                                parent.ref if parent is not None else None, ref))
        calls["seen_snapshot"].add(kwargs.get("snapshot"))
        return offsets[ref]

    monkeypatch.setattr(reread_mod, "_anchor_pose", _anchor)
    monkeypatch.setattr(reread_mod, "layout_tree_from_anchor", _layout)
    monkeypatch.setattr(reread_mod, "child_live_pose", _child)
    monkeypatch.setattr(reread_mod, "_live_offset", _live)
    return calls


def test_a1_through_the_read_a_moved_origin_leaves_only_the_anchor_row(
        monkeypatch):
    """а1 end to end: the tree is a plain Origin tree WITHOUT its own rotation or
    pivot — the parity caveat of the module docstring is about exactly that, and
    this cell would be lying on a turned tree."""
    mh = _tree_node("mh", "placement", xy=(0.0, 0.0))
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    _wire(monkeypatch, predicted={"mh": (0.0, 0.0, 0.0)},
          live={"mh": (1.0, 2.0, 0.0)}, offsets={"mh": ((1.0, 2.0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[tree], sheet_names={}, adapter=object())
    plan = build_plan(raw)

    assert raw["votes"]["main"] == [("mh", 1.0, 2.0)]
    assert raw["nodes"][0]["stored"] == (0.0, 0.0)
    assert raw["nodes"][0]["source"] == "anchor"
    assert raw["nodes"][0]["base_rot"] == 0.0
    assert plan.anchors_moved == 1
    assert plan.changed == 0


def test_the_top_node_is_read_with_no_parent_and_a_child_with_its_parent(
        monkeypatch):
    """г: the top node's base is the tree's own anchor (`parent=None` — the parity
    contract with the node Read), a child's base is its live parent."""
    child = _tree_node("u2", "placement", xy=(1.0, 0.0))
    parent = _tree_node("u1", "placement", xy=(0.0, 0.0), children=[child])
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True),
                nodes=[parent])
    calls = _wire(monkeypatch, predicted={"u1": (0.0, 0.0, 0.0),
                                          "u2": (1.0, 0.0, 0.0)},
                  live={"u1": (0.0, 0.0, 0.0), "u2": (1.0, 0.0, 0.0)},
                  offsets={"u1": ((0.0, 0.0), 0.0), "u2": ((1.0, 0.0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[tree], sheet_names={}, adapter=object())

    assert calls["offset"] == [("main", None, "u1"), ("main", "u1", "u2")]
    assert [node["source"] for node in raw["nodes"]] == ["anchor", "none"]


def test_a_mount_node_is_read_against_its_own_anchor(monkeypatch):
    """з: a mount node's base is a LIVE role, never the parent frame — so it gets
    its own `base_anchor` and never absorbs the tree anchor's move."""
    mount = _tree_node("mnt", "mount", anchor=TreeAnchor(role="FPGA"))
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mount])
    seen = {}

    calls = _wire(monkeypatch, predicted={}, live={},
                  offsets={"mnt": ((0.0, 0.0), 0.0)})
    original = reread_mod._live_offset

    def _live(cfg, adapter, sheet_names, tree_, parent, ref, kind, **kwargs):
        seen.update(kwargs)
        return original(cfg, adapter, sheet_names, tree_, parent, ref, kind,
                        **kwargs)

    monkeypatch.setattr(reread_mod, "_live_offset", _live)

    raw = read_forest_now(cfg=None, trees=[tree], sheet_names={}, adapter=object())

    assert seen["base_anchor"] is mount.anchor
    assert raw["nodes"][0]["source"] == "none"
    assert calls["offset"] == [("main", None, "mnt")]


def test_module_content_is_read_against_the_EFF_base_not_the_marker(
        monkeypatch):
    """д, the read half: `module_bases` hands out MARKER (6, −64) and EFF (1, −64)
    with a pivot of 5 mm. The content's offset is measured from EFF — measuring it
    from the marker would bake the pivot into every number it stores."""
    j1 = _tree_node("J1", "placement", xy=(0.0, 0.0))
    power = Tree(name="power", anchor=TreeAnchor(is_origin=True), nodes=[j1])
    marker = _tree_node("power", "module", xy=(30.0, -35.0))
    main = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[marker])
    bases = {"power": ("power", Vector2.from_xy_mm(6.0, -64.0), 0.0,
                       Vector2.from_xy_mm(1.0, -64.0), 0.0)}
    calls = _wire(monkeypatch, predicted={}, live={"J1": (1.0, -61.0, 0.0)},
                  offsets={}, module_bases=bases,
                  anchor_poses={"power": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, power], sheet_names={},
                          adapter=object())

    node = raw["nodes"][0]
    assert node["tree"] == "power" and node["anchor_tree"] == "main"
    assert node["source"] == ["module", "power"]
    # live (1, −61) − EFF (1, −64) = (0, +3): the pivot is NOT in the number.
    assert node["offset"] == (0.0, 3.0), (calls, node["error"])
    assert [item["tree"] for item in raw["embedded"]] == ["power"]
    assert raw["embedded"][0]["marker_mm"] == (6.0, -64.0)


def test_an_embedded_tree_is_not_laid_out_on_its_own(monkeypatch):
    """A tree placed by a module has no usable anchor of its own: its OWN layout
    is never asked for, so its content cannot be read twice (once from the anchor
    and once from the module)."""
    j1 = _tree_node("J1", "placement")
    power = Tree(name="power", anchor=TreeAnchor(is_origin=True), nodes=[j1])
    main = Tree(name="main", anchor=TreeAnchor(is_origin=True),
                nodes=[_tree_node("power", "module")])
    layouts: list = []

    def _layout(tree, forest=None, **kwargs):
        layouts.append(tree.name)
        bases = kwargs.get("module_bases")
        if bases is not None:
            bases["power"] = ("power", Vector2.from_xy_mm(0.0, 0.0), 0.0,
                              Vector2.from_xy_mm(0.0, 0.0), 0.0)
        return {}

    monkeypatch.setattr(reread_mod, "_anchor_pose",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 0.0))
    monkeypatch.setattr(reread_mod, "layout_tree_from_anchor", _layout)
    monkeypatch.setattr(reread_mod, "child_live_pose",
                        lambda *a, **k: (Vector2.from_xy(0, 0), 0.0, False))

    read_forest_now(cfg=None, trees=[main, power], sheet_names={},
                    adapter=object())

    assert layouts == ["main"]


# ── а6: the mode switch decides again WITHOUT a second board read ─────────

def test_a6_the_mode_switch_does_not_read_the_board_again(monkeypatch):
    mh = _tree_node("mh", "placement", xy=(0.0, 0.0))
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    calls = _wire(monkeypatch, predicted={"mh": (0.0, 0.0, 0.0)},
                  live={"mh": (1.0, 2.0, 0.0)},
                  offsets={"mh": ((1.0, 2.0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[tree], sheet_names={}, adapter=object())
    before = (len(calls["child"]), len(calls["offset"]), len(calls["anchor"]))

    absorbed = build_plan(raw, mode=MODE_ABSORB)
    kept = build_plan(raw, mode=MODE_KEEP)

    assert (len(calls["child"]), len(calls["offset"]),
            len(calls["anchor"])) == before
    assert absorbed.anchors_moved == 1 and kept.anchors_moved == 0
    assert absorbed.changed == 0 and kept.changed == 1


# ── the raw point a sync subtracts from is the anchor WITHOUT its own shift ─

def test_the_raw_anchor_point_drops_the_anchors_own_shift():
    """Claude, 16.11: `_anchor_base_live_position` returns the point WITH the
    shift, while `sync_embedded_anchor` wants it WITHOUT — otherwise the shift is
    counted twice and `power` (Origin +(5, −60), module at (6, −64)) would sync to
    (11, −124) instead of (6, −64)."""
    power = Tree(name="power", anchor=TreeAnchor(is_origin=True,
                                                 shift_xy=(5.0, -60.0)),
                 nodes=[])

    raw_point = reread_mod._raw_anchor_point(
        power, Vector2.from_xy_mm(5.0, -60.0), 0.0)

    assert raw_point == (0.0, 0.0)
    row = sync_embedded_anchor("power", raw_point, anchor_rot_deg=0.0,
                               module_marker_mm=(6.0, -64.0), module_rot_deg=0.0,
                               current_shift=(5.0, -60.0))
    assert row.now == (6.0, -64.0)


# ── Ф1–Ф5 приёмки `622e47bc` (17.11) ───────────────────────────────────────
#
# Every cell here runs the REAL read (`read_forest_now`) and then the REAL plan
# (`build_plan`); none of them feeds `build_plan` a hand-written dictionary. That is
# the point of Ф1: the defect was in which `source` the READ assigns, so a cell on
# the raw dictionary could never have caught it.

def _module_tree(*, marker_xy=(10.0, 10.0), nodes, name="fpga"):
    """The embedded tree and the owner that places it at `marker_xy`."""
    return (Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes)),
            Tree(name="main", anchor=TreeAnchor(is_origin=True),
                 nodes=[_tree_node(name, "module", xy=marker_xy)]))


def _module_bases(*, marker_xy=(10.0, 10.0), name="fpga"):
    return {name: (name, Vector2.from_xy_mm(*marker_xy), 0.0,
                   Vector2.from_xy_mm(*marker_xy), 0.0)}


def test_f1_a_child_of_module_content_absorbs_nothing(monkeypatch):
    """Ф1 (blocking): ALL content of `fpga` moved (0, −3) together, and `C1` sits
    correctly relative to its live parent `U1`. The table must show ONE row — the
    module's `xy` — and no `C1` row at all.

    Before the fix `source` was set from `inside` alone, so `C1` — measured from its
    LIVE parent — had the module's delta subtracted anyway and produced the false
    row `C1 (2, 0) → (2, 3)`: exactly the ≈ +3 mm that 85 nodes of the live STM32
    profile would have received."""
    c1 = _tree_node("C1", "placement", xy=(2.0, 0.0))
    u1 = _tree_node("U1", "placement", xy=(0.0, 0.0), children=[c1])
    u2 = _tree_node("U2", "placement", xy=(2.0, 0.0))
    fpga, main = _module_tree(nodes=[u1, u2])
    _wire(monkeypatch,
          predicted={"U1": (10.0, 7.0, 0.0), "U2": (12.0, 7.0, 0.0)},
          live={"U1": (10.0, 7.0, 0.0), "U2": (12.0, 7.0, 0.0)},
          offsets={"C1": ((2.0, 0.0), 0.0)},
          module_bases=_module_bases(),
          anchor_poses={"fpga": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, fpga], sheet_names={},
                          adapter=object())
    plan = build_plan(raw)

    assert [(change.tree, change.ref) for change in plan.changes] == [
        ("main", "fpga")]
    assert plan.changes[0].was == (10.0, 10.0)
    assert plan.changes[0].now == (10.0, 7.0)


def test_f1_a_child_of_module_content_absorbs_nothing_when_content_moved_apart(
        monkeypatch):
    """Ф1, the second probe: the Origin moved (0, −3) — `mh` and `U1` followed, `U2`
    was moved by hand another +1 mm — so the anchor absorbs (0, −3) and `U2` is
    written. `C1` is NOT: it already sits right relative to `U1`."""
    c1 = _tree_node("C1", "placement", xy=(2.0, 0.0))
    u1 = _tree_node("U1", "placement", xy=(0.0, 0.0), children=[c1])
    u2 = _tree_node("U2", "placement", xy=(2.0, 0.0))
    mh = _tree_node("mh", "placement", xy=(0.0, 0.0))
    fpga = Tree(name="fpga", anchor=TreeAnchor(is_origin=True), nodes=[u1, u2])
    main = Tree(name="main", anchor=TreeAnchor(is_origin=True),
                nodes=[mh, _tree_node("fpga", "module", xy=(10.0, 10.0))])
    _wire(monkeypatch,
          predicted={"mh": (0.0, 0.0, 0.0), "U1": (10.0, 10.0, 0.0),
                     "U2": (12.0, 10.0, 0.0)},
          live={"mh": (0.0, -3.0, 0.0), "U1": (10.0, 7.0, 0.0),
                "U2": (13.0, 7.0, 0.0)},
          offsets={"C1": ((2.0, 0.0), 0.0)},
          module_bases=_module_bases(),
          anchor_poses={"fpga": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, fpga], sheet_names={},
                          adapter=object())
    plan = build_plan(raw)

    assert plan.anchors_moved == 1
    assert [(change.tree, change.ref) for change in plan.changes] == [("fpga", "U2")]


def test_f2_a_mount_inside_module_content_is_read_through_its_own_anchor(
        monkeypatch):
    """Ф2: a mount node placed as content of an embedded tree has its base on its OWN
    live role — the layout puts it there (`mount_node_poses`), never off the module's
    frame. It must therefore go through `_live_offset(base_anchor=…)`, not through
    `child_live_pose` against EFF, and it absorbs nothing."""
    mount = _tree_node("mnt", "mount", anchor=TreeAnchor(role="FPGA"))
    fpga, main = _module_tree(nodes=[mount])
    calls = _wire(monkeypatch, predicted={}, live={},
                  offsets={"mnt": ((0.0, 0.0), 0.0)},
                  module_bases=_module_bases(),
                  anchor_poses={"fpga": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, fpga], sheet_names={},
                          adapter=object())

    assert "mnt" not in calls["child"]
    assert [entry[2] for entry in calls["offset"]] == ["mnt"]
    assert raw["nodes"][0]["source"] == "none"


def test_f3_a_nested_module_is_a_skip_with_its_reason(monkeypatch):
    """Ф3: a module under content has its marker on a LIVE parent, and the «moved
    together» rule is written for TOP modules only. Its content is a SKIP with the
    reason — never a row with the owner's anchor delta absorbed into it."""
    r1 = _tree_node("R1", "placement")
    sub = Tree(name="sub", anchor=TreeAnchor(is_origin=True), nodes=[r1])
    nested = _tree_node("sub", "module", xy=(2.0, 0.0))
    u1 = _tree_node("U1", "placement", xy=(0.0, 0.0), children=[nested])
    fpga, main = _module_tree(nodes=[u1])
    bases = _module_bases()
    bases.update({"sub": ("sub", Vector2.from_xy_mm(12.0, 10.0), 0.0,
                          Vector2.from_xy_mm(12.0, 10.0), 0.0)})
    _wire(monkeypatch, predicted={"U1": (10.0, 10.0, 0.0)},
          live={"U1": (10.0, 10.0, 0.0)}, offsets={}, module_bases=bases,
          anchor_poses={"fpga": (Vector2.from_xy(0, 0), 0.0),
                        "sub": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, fpga, sub], sheet_names={},
                          adapter=object())

    reasons = [skip["reason"] for skip in raw["skips"] if skip["ref"] == "sub"]
    assert len(reasons) == 1
    assert "nested module" in reasons[0]
    assert "R1" not in [node["ref"] for node in raw["nodes"]]


def test_f4_the_snapshot_reaches_every_reader(monkeypatch):
    """Ф4: one connection snapshot rides into the layout, every `_live_offset` and
    every `child_live_pose` — otherwise each role or parent resolve walks the board
    on its own."""
    mh = _tree_node("mh", "placement", xy=(0.0, 0.0))
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    calls = _wire(monkeypatch, predicted={"mh": (0.0, 0.0, 0.0)},
                  live={"mh": (0.0, 0.0, 0.0)},
                  offsets={"mh": ((0.0, 0.0), 0.0)})

    read_forest_now(cfg=None, trees=[tree], sheet_names={}, adapter=object(),
                    snapshot="SNAP")

    assert calls["seen_snapshot"] == {"SNAP"}


def test_f5_a_tree_placed_by_two_markers_is_read_once_and_skipped(monkeypatch):
    """Ф5: two module nodes naming the same tree would walk its content twice — two
    rows for one `(tree, ref)`. Its content is not read at all (one shift cannot land
    on both markers), and the skip is said ONCE, not once per marker."""
    j1 = _tree_node("J1", "placement")
    power = Tree(name="power", anchor=TreeAnchor(is_origin=True), nodes=[j1])
    main = Tree(name="main", anchor=TreeAnchor(is_origin=True),
                nodes=[_tree_node("power", "module", xy=(1.0, 0.0)),
                       _tree_node("power", "module", xy=(5.0, 0.0))])
    _wire(monkeypatch, predicted={}, live={}, offsets={},
          module_bases=_module_bases(name="power", marker_xy=(1.0, 0.0)),
          anchor_poses={"power": (Vector2.from_xy(0, 0), 0.0)})

    raw = read_forest_now(cfg=None, trees=[main, power], sheet_names={},
                          adapter=object())

    assert raw["nodes"] == []
    power_skips = [skip for skip in raw["skips"] if skip["tree"] == "power"]
    assert len(power_skips) == 1, raw["skips"]
    assert "more than one module" in power_skips[0]["reason"]
    # The votes refuse too (the same `J1` reached through two markers), and that is
    # the OWNER's skip — a different fact from the content's.
    assert any("may vote" in skip["reason"] for skip in raw["skips"])
    assert len(raw["modules"]) == 1
