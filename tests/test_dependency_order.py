#!/usr/bin/env python3
"""
Tests for dependency_order.py — the level-by-level (Kahn's algorithm)
ordering that fixes the p5v_led_spoke bug (2026-07-27): an item anchored on a
ref that ANOTHER item in the same apply run is about to move must be planned
AFTER that other item, not against a stale pre-run snapshot.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import (
    Config, ClonePlacement, Cell, TemplateComponentSlot, Point,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.dependency_order import resolve_execution_order, _build_items

MM = 1_000_000


def _make_pad(number, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.net.name = net_name
    return pad


def _make_fp(ref, role=None, x_mm=0.0, y_mm=0.0, nets=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    fp._pads = [_make_pad("1", n) for n in nets]
    return fp


def _adapter_for(fps):
    by_ref = {fp.reference_field.text.value: fp for fp in fps}
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_selected_items.return_value = []
    return adapter


def _clone(name, anchor_ref, cell, nets):
    return ClonePlacement(name=name, cell=cell, xy=(0.0, 0.0),
                          anchor_ref=anchor_ref, nets=nets)


def _cfg(clones=None, points=None):
    producer_tpl = Cell(
        name="producer_tpl",
        components=[TemplateComponentSlot(role="PRODUCED_ROLE", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    consumer_tpl = Cell(
        name="consumer_tpl",
        components=[TemplateComponentSlot(role="OTHER_ROLE", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    return Config(
        layer='F.Cu',
        cells={"producer_tpl": producer_tpl, "consumer_tpl": consumer_tpl},
        points=points or {},
        rules=[],
        clone_placements=clones or [],
    )


def test_cell_mode_clone_produces_cell_roles():
    """A plain cell:-mode clone (the ONLY mode since 2026-08-12, Group 0
    consolidation — the cluster:/role: single-component modes migrated 1:1 to
    coordinate_placements' anchor-relative mode) resolves its cell's roles as
    its produced refs — the cluster-tag path it used to test was removed with
    that mode."""
    tagged = _make_fp("J1", role="PRODUCED_ROLE", nets=["NET_A"])
    other = _make_fp("J2", role="SOMETHING_ELSE")

    clone = ClonePlacement(name="Conn_PM5V", cell="producer_tpl",
                           xy=(0.0, 0.0), nets={"PRODUCED_ROLE": "NET_A"})
    cfg = _cfg([clone])

    adapter = _adapter_for([tagged, other])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == ["clone_placement 'Conn_PM5V'"]
    assert items[0].produces == {"J1"}


def test_disabled_clone_is_skipped_entirely():
    """A disabled clone_placement anchored on a role that doesn't exist on the
    board at all would fatal if resolved (see resolve_footprint_by_role) — it
    must be skipped BEFORE anchor resolution is even attempted, not just
    excluded from execution later (compute_raw_positions already no-ops for
    it, but _build_items used to still call resolve_clone_anchor_ref on it
    unconditionally)."""
    anchor1 = _make_fp("ANCHOR1")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])

    clone_enabled = _clone("clone_a", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_disabled = ClonePlacement(
        name="clone_disabled", cell="consumer_tpl", xy=(0.0, 0.0),
        anchor_role="NONEXISTENT_ROLE", retired=True,
    )
    cfg = _cfg([clone_enabled, clone_disabled])

    adapter = _adapter_for([anchor1, p1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == ["clone_placement 'clone_a'"]


def test_no_dependencies_keeps_original_order():
    """Two clones anchored on stable, pre-existing components — neither
    produces the other's anchor — order must be unchanged (stable sort)."""
    anchor1 = _make_fp("ANCHOR1")
    anchor2 = _make_fp("ANCHOR2")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
    c1 = _make_fp("C1", role="OTHER_ROLE", nets=["NET_B"])

    clone_a = _clone("clone_a", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_b = _clone("clone_b", "ANCHOR2", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    cfg = _cfg([clone_a, clone_b])

    adapter = _adapter_for([anchor1, anchor2, p1, c1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == [
        "clone_placement 'clone_a'", "clone_placement 'clone_b'"
    ]


def test_producer_ordered_before_consumer_regardless_of_yaml_order():
    """clone_consumer is anchored on P1 — the ref clone_producer moves.
    Declared FIRST in YAML (wrong order) — resolve_execution_order must still
    put the producer first. This is the exact p5v_led_spoke/p5v_pi_filter shape."""
    anchor1 = _make_fp("ANCHOR1")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
    c1 = _make_fp("C1", role="OTHER_ROLE", nets=["NET_B"])

    clone_producer = _clone("producer", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_consumer = _clone("consumer", "P1", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    # Declared in the WRONG order: consumer before its producer.
    cfg = _cfg([clone_consumer, clone_producer])

    adapter = _adapter_for([anchor1, p1, c1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == [
        "clone_placement 'producer'", "clone_placement 'consumer'"
    ]


def test_self_anchored_item_is_not_a_cycle():
    """Found live (p5v_led_spoke): a clone anchored on its OWN role/pad — the
    anchor component is ALSO one of the cell's own role slots (extracted
    with itself as origin). That's a benign self-reference, not a real
    cross-item dependency, and must not be flagged as a cycle."""
    anchor_and_role = _make_fp("R1", role="PRODUCED_ROLE", nets=["NET_A"])

    clone_self = _clone("self_anchored", "R1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    cfg = _cfg([clone_self])

    adapter = _adapter_for([anchor_and_role])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == ["clone_placement 'self_anchored'"]


def test_cycle_raises_validation_error():
    """clone_a is anchored on what clone_b produces, and clone_b is anchored
    on what clone_a produces — no valid order exists, must fail loudly before
    any board mutation."""
    p_out = _make_fp("P_OUT", role="PRODUCED_ROLE", nets=["NET_A"])
    c_out = _make_fp("C_OUT", role="OTHER_ROLE", nets=["NET_B"])

    clone_a = _clone("clone_a", "C_OUT", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_b = _clone("clone_b", "P_OUT", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    cfg = _cfg([clone_a, clone_b])

    adapter = _adapter_for([p_out, c_out])

    with pytest.raises(ValidationError, match="dependency cycle"):
        resolve_execution_order(adapter, cfg)


class TestPointItems:
    """Phase 3: Point as a real dependency-graph node (see
    handoff_2026_07_31_consolidated.md) — a point produces a NAMESPACED
    token ("point:<name>", not a bare ref) so it can never collide with a
    real refdes of the same name; anchor_point: on a consumer resolves to
    that same namespaced token as its dependency."""

    def test_point_item_appears_with_correct_label_and_kind(self):
        anchor1 = _make_fp("ANCHOR1")
        pt = Point(name="my_point", anchor_ref="ANCHOR1")
        cfg = _cfg(points={"my_point": pt})
        adapter = _adapter_for([anchor1])

        items = resolve_execution_order(adapter, cfg)

        assert [it.label for it in items] == ["point 'my_point'"]
        assert items[0].kind == 'point'
        assert items[0].obj is pt

    def test_point_produces_namespaced_token_not_bare_name(self):
        anchor1 = _make_fp("ANCHOR1")
        pt = Point(name="P1", anchor_ref="ANCHOR1")
        cfg = _cfg(points={"P1": pt})
        adapter = _adapter_for([anchor1])

        items = _build_items(adapter, cfg)

        point_item = next(it for it in items if it.kind == 'point')
        assert point_item.produces == {"point:P1"}

    def test_clone_anchor_point_resolves_to_namespaced_dependency_token(self):
        anchor1 = _make_fp("ANCHOR1")
        p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
        pt = Point(name="my_point", anchor_ref="ANCHOR1")
        clone_consumer = ClonePlacement(
            name="consumer", cell="producer_tpl", xy=(0.0, 0.0),
            anchor_point="my_point", nets={"PRODUCED_ROLE": "NET_A"},
        )
        cfg = _cfg(clones=[clone_consumer], points={"my_point": pt})
        adapter = _adapter_for([anchor1, p1])

        items = _build_items(adapter, cfg)

        clone_item = next(it for it in items if it.kind == 'clone')
        assert clone_item.anchor_ref == "point:my_point"

    def test_three_level_chain_producer_then_point_then_consumer(self):
        """Point's own anchor (P1) is produced by another clone_placement in
        this run — the point must be ordered after that producer, and the
        point's own consumer must be ordered after the point. Declared out
        of order on purpose; this is airtight against a broken/missing
        anchor_point namespacing — without it, `consumer` would incorrectly
        land in level 0 alongside `producer` instead of level 2."""
        anchor1 = _make_fp("ANCHOR1")
        p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
        c1 = _make_fp("C1", role="OTHER_ROLE", nets=["NET_B"])

        clone_producer = _clone("producer", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
        pt = Point(name="my_point", anchor_ref="P1")  # P1 is produced by clone_producer
        clone_consumer = ClonePlacement(
            name="consumer", cell="consumer_tpl", xy=(0.0, 0.0),
            anchor_point="my_point", nets={"OTHER_ROLE": "NET_B"},
        )
        cfg = _cfg(clones=[clone_consumer, clone_producer], points={"my_point": pt})
        adapter = _adapter_for([anchor1, p1, c1])

        items = resolve_execution_order(adapter, cfg)

        assert [it.label for it in items] == [
            "clone_placement 'producer'", "point 'my_point'", "clone_placement 'consumer'"
        ]

    def test_point_chain_orders_correctly_regardless_of_yaml_order(self):
        anchor1 = _make_fp("ANCHOR1")
        point_a = Point(name="a", anchor_ref="ANCHOR1")
        point_b = Point(name="b", anchor_point="a", shift_x_mm=1.0)
        # dict insertion order deliberately wrong: b before a
        cfg = _cfg(points={"b": point_b, "a": point_a})
        adapter = _adapter_for([anchor1])

        items = resolve_execution_order(adapter, cfg)

        assert [it.label for it in items] == ["point 'a'", "point 'b'"]

    def test_point_to_point_cycle_raises_validation_error(self):
        point_a = Point(name="a", anchor_point="b")
        point_b = Point(name="b", anchor_point="a")
        cfg = _cfg(points={"a": point_a, "b": point_b})
        adapter = _adapter_for([])

        with pytest.raises(ValidationError, match="dependency cycle"):
            resolve_execution_order(adapter, cfg)
