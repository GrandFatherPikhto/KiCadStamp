# tests/test_external_point_materialization.py
"""Tests for Б3.1 (plan_2026_09_11_external_point_materialization): the tree's
OUTER point (the "tree anchor") becomes a real anchor —

  * X.1 — a `(point ...)` anchor MATERIALIZES (it used to raise "future phase"
    only at Apply, while reading already worked: the worst of both worlds), and
    the materialized result equals the live layout (ONE resolver, two paths);
  * X.2 — the anchor has its OWN `(shift x y)`, stored in LOCAL mm of the base
    and rotated by the anchor's angle, so the channel rotation invariance holds
    (design §3.8). `Point.shift_x_mm` keeps its own board-absolute convention
    and is NOT touched.

Point anchors over a literal-`xy` (or chained) `points:` entry resolve OFFLINE,
so most of these tests need no adapter at all — which is the whole point of the
inner/outer split (design §I).
"""
import inspect

import pytest

from kicadstamp.config import Cell, Config, Entity, Point
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.tree_position import (
    _anchor_base_live_position,
    anchor_shift_offset_nm,
    layout_tree_from_anchor,
    resolve_point_chain,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM


def _node(ref, xy=None, kind="placement", rotation=0.0, children=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=children or [])


def _cfg_with_point(anchor, node_xy=(1.0, 0.0), **point_kw):
    """One Entity cell 'E1' + one tree placed by a point anchor whose points:
    entry 'P' is literal xy (offline-resolvable)."""
    return Config(
        cells={"E1": Cell(name="E1")},
        entities=[Entity(name="E1", cell="E1")],
        trees=[Tree(name="t", anchor=anchor,
                    nodes=[_node(ref="E1", xy=node_xy)])],
        points={"P": Point(name="P", xy=(10.0, 20.0), **point_kw)},
    )


# ═══════════════════════════════════════════════════════════════════════════
# X.1 — materialization by a (point ...) anchor
# ═══════════════════════════════════════════════════════════════════════════

def test_point_anchor_tree_materializes_at_point_position():
    """X.5.1 #1: a tree with (anchor (point ...)) APPLIES (today it raises
    '… not wired … future phase'). Position = point + node offset."""
    cfg = _cfg_with_point(TreeAnchor(point="P"))
    clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["E1"]
    assert clones[0].xy == (11.0, 20.0)
    assert clones[0].rotation_deg == 0.0


def test_point_anchor_is_not_a_per_tree_skip_anymore(caplog):
    """X.5.1 #1 (guard): the (point ...) tree is NOT skipped once the point
    resolves — the old "future phase" per-tree skip must be gone."""
    import logging
    cfg = _cfg_with_point(TreeAnchor(point="P"))
    with caplog.at_level(logging.WARNING,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(None, cfg, {})
    assert [c.name for c in clones] == ["E1"]
    assert "future phase" not in caplog.text
    assert "skipped" not in caplog.text


def test_point_anchor_live_layout_equals_materialization():
    """X.5.1 #6 / X.1.4 — THE main test: the LIVE layout of a point-anchored
    tree and its MATERIALIZATION are one result. One resolver, two paths."""
    cfg = _cfg_with_point(TreeAnchor(point="P"), node_xy=(1.5, -2.5))
    tree = cfg.trees[0]
    live = layout_tree_from_anchor(tree, None, adapter=None, cfg=cfg,
                                   sheet_names={})
    live_pos, live_rot = live["E1"]
    clones = materialize_entity_placements(None, cfg, {})
    clone = clones[0]
    assert clone.xy == (live_pos.x / MM, live_pos.y / MM)
    assert clone.rotation_deg == live_rot


def test_point_anchor_angle_comes_from_tree_rotation():
    """X.5.1 #3/#4: a Point has no orientation, so tree.rotation is the SOLE
    source of content rotation — 0 keeps today's behaviour, 30 turns the tree
    around its suspension point (pivot (0,0) here)."""
    cfg0 = _cfg_with_point(TreeAnchor(point="P"))
    assert materialize_entity_placements(None, cfg0, {})[0].rotation_deg == 0.0

    cfg30 = _cfg_with_point(TreeAnchor(point="P"))
    cfg30.trees[0].rotation = 30.0
    clone = materialize_entity_placements(None, cfg30, {})[0]
    assert clone.rotation_deg == 30.0
    # The node offset (1, 0) is turned into the tree's 30° frame, so the
    # position is NOT (11, 20) anymore.
    assert clone.xy != (11.0, 20.0)
    rot = rotate_local_offset(1.0, 0.0, 30.0)
    assert clone.xy[0] == pytest.approx(10.0 + rot.x / MM, abs=1e-6)
    assert clone.xy[1] == pytest.approx(20.0 + rot.y / MM, abs=1e-6)


def test_cascade_has_no_silent_zero_rotation_substitution():
    """X.5.1 #5 (X.1.3): the old `base_rot if base_rot is not None else 0.0`
    silent-zero substitution is gone from gui/docks/cascade.py."""
    import gui.docks.cascade as cascade
    src = inspect.getsource(cascade)
    # The replacement call is present...
    assert "tree_layout_base(" in src
    # ...and no EXECUTABLE line still carries the silent zero (it now survives
    # only inside the explanatory comment, which is fine).
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("base_rot if base_rot is not None else 0.0" in ln
                   for ln in code_lines)


# ═══════════════════════════════════════════════════════════════════════════
# X.2 — the anchor's OWN (shift x y): LOCAL mm of the base
# ═══════════════════════════════════════════════════════════════════════════

def test_anchor_shift_offset_angle_zero_is_unrotated():
    """X.5.2 #7: shift (5, 0) on an anchor with angle 0 -> +5 mm in board X."""
    off = anchor_shift_offset_nm(TreeAnchor(role="R", shift_xy=(5.0, 0.0)), 0.0)
    assert (off.x, off.y) == (5 * MM, 0)


def test_anchor_shift_offset_rotates_with_base_angle():
    """X.5.2 #8 — THE §3.8 regression: the SAME (5, 0) shift on a base turned
    -90° must NOT stay (5, 0); it follows the base's own frame (Y-down, so a
    -90° member points its local +X along board +Y)."""
    off = anchor_shift_offset_nm(TreeAnchor(role="R", shift_xy=(5.0, 0.0)), -90.0)
    assert (off.x, off.y) != (5 * MM, 0)
    assert (off.x, off.y) == (0, 5 * MM)


def test_anchor_shift_offset_matches_rotate_local_offset():
    """The helper is a faithful delegate of the project's one rotation
    primitive — never its own re-implementation."""
    anchor = TreeAnchor(role="R", shift_xy=(1.25, -3.5))
    off = anchor_shift_offset_nm(anchor, 37.0)
    expected = rotate_local_offset(1.25, -3.5, 37.0)
    assert (off.x, off.y) == (expected.x, expected.y)


def test_anchor_shift_offset_none_is_zero():
    assert anchor_shift_offset_nm(TreeAnchor(role="R"), 45.0) == Vector2.from_xy(0, 0)
    assert anchor_shift_offset_nm(None, 45.0) == Vector2.from_xy(0, 0)


def test_role_anchor_shift_rotates_with_live_angle(monkeypatch):
    """End-to-end: the LIVE base of a role anchor picks up its own shift at the
    resolved footprint's angle (the shift is not a separate board-absolute
    translation)."""
    import kicadstamp.tree_position as tp_mod

    class _Fp:
        position = Vector2.from_xy(100 * MM, 200 * MM)
        angle_deg = -90.0

    class _Resolver:
        def __init__(self, *a, **k):
            pass

        def resolve_anchor_fp(self, *a, **k):
            return _Fp()

    monkeypatch.setattr(tp_mod, "ComponentResolver", _Resolver)
    tree = Tree(name="t", anchor=TreeAnchor(role="R", shift_xy=(5.0, 0.0)),
                nodes=[])
    pos, rot = _anchor_base_live_position(object(), object(), tree, {})
    assert rot == -90.0
    assert (pos.x, pos.y) == (100 * MM + 0, 200 * MM + 5 * MM)


def test_anchor_shift_adds_on_top_of_point_absolute_shift():
    """X.5.2 #9: point resolved by its OWN rules first (including the point's
    own board-absolute shift), THEN the anchor's LOCAL shift is added."""
    cfg = _cfg_with_point(TreeAnchor(point="P", shift_xy=(5.0, 0.0)))
    # The point itself carries its own (board-absolute) shift of (2, 3) mm —
    # a chained point so the combination is loadable (xy + shift is not).
    cfg.points = {
        "base": Point(name="base", xy=(10.0, 20.0)),
        "P": Point(name="P", anchor_point="base", shift_x_mm=2.0, shift_y_mm=3.0),
    }
    clones = materialize_entity_placements(None, cfg, {})
    # base (10,20) + point shift (2,3) = (12,23); + anchor shift (5,0) = (17,23);
    # + node offset (1,0) = (18,23).
    assert clones[0].xy == (18.0, 23.0)


def test_point_chain_still_flat_for_existing_consumers():
    """X.5.2 #14: Point's own convention is unchanged — a point's shift is a
    flat, board-absolute translation applied by resolve_point_chain (spokes/
    rules/via arrays keep their behaviour)."""
    points = {
        "base": Point(name="base", xy=(10.0, 20.0)),
        "P": Point(name="P", anchor_point="base", shift_x_mm=2.0, shift_y_mm=3.0),
    }
    resolved = resolve_point_chain(None, points, "P", {})
    assert (resolved.position.x, resolved.position.y) == (12 * MM, 23 * MM)


def test_anchor_shift_alone_without_base_is_rejected():
    """A shift with no base is meaningless (mirrors the s-expr mode-count
    fatal) — never a silent drop."""
    from kicadstamp.trees import tree_from_dict
    with pytest.raises(ValidationError, match="shift needs a base"):
        tree_from_dict({"name": "t", "anchor": {"shift": [5.0, 0.0]}, "nodes": []})
