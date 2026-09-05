#!/usr/bin/env python3
"""Regression: a single-spoke Redraw must not steal components from a
neighbouring spoke of the same chain/net.

Reported live (Denis, 2026-09-05): "когда перерисовываю одну спицу
(Spoke => pad) спица ворует компоненты у какой-нибудь спицы по соседству в
той же сети".

Root cause (before the fix):

- gui/docks/chain.py redraw_pad marked every OTHER spoke of the chain
  skip=True, and apply_pipeline.drop_inactive_items then REMOVED those skipped
  spokes from the chain copy (they no longer consumed the pool).
- ManualPositionCalculator builds one ComponentPool per (net, role, cluster)
  from ALL live footprints (component_pool.py), and each spoke pops "the next
  in natural order" (consume_role_to_ref). With the siblings removed there were
  fewer consumers, so the redrawn spoke popped the FIRST natural-order
  component — the one already owned by its skipped neighbour. A full chain
  redraw was stable (consumption order never changes), a partial one was not.

Fix (option (c), chosen by Denis): a "Redraw spoke" keeps the FULL chain in
the config and expresses isolation via an explicit per-run map
isolate_spokes = {chain name -> {pad}} (ApplyPipeline ->
ManualPositionCalculator/dependency_order). Every non-retired spoke STILL
consumes the shared pool in full-chain order (the inactive siblings RESERVE
their components), but only the isolated pad actually emits
geometry/vias/tracks. The isolated spoke therefore gets exactly the components
a full chain redraw would assign to it — it can never re-claim a neighbour's.
Config-authored skip=True semantics are untouched (a skipped spoke still does
NOT consume).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.board_types import Pad, FootprintInstance
from kicadstamp.domain.geometry import Vector2

from kicadstamp.config import (Config, Rule, ManualSpoke, Cell,
                               TemplateComponentSlot, chain_effective_name)
from kicadstamp.placement.services.manual_position_calculator import ManualPositionCalculator

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net_name = net_name
    return pad


def _make_anchor_fp(ref, pads):
    """pads: list of (number, x_mm, y_mm, net_name)."""
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._pads = [_make_pad(*p) for p in pads]
    return fp


def _make_pool_fp(ref, role, net_name):
    """Mock component footprint — for the pool only ref/role/net matter;
    position is set explicitly by the caller (moved by a run)."""
    fp = MagicMock(spec=FootprintInstance)
    fp.ref = ref
    fp._role = role
    fp._pads = [_make_pad("1", 0.0, 0.0, net_name), _make_pad("2", 0.0, 0.0, "GND")]
    fp.position = Vector2.from_xy(0, 0)
    fp.angle_deg = 0.0
    return fp


def _adapter(anchor_fp, pool_fps):
    all_fps = [anchor_fp] + pool_fps
    by_ref = {fp.ref: fp for fp in all_fps}

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_footprints.return_value = all_fps
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    # component_pool reads Role (+ Cluster when the spoke has a cluster; here
    # cluster is None, so only Role is consulted).
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    return adapter, by_ref


def _single_role_cell():
    return Cell(
        name="tpl", layer="F.Cu",
        components=[TemplateComponentSlot(
            role="DECOUP", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0)],
    )


def _chain_with_n_spokes(pads):
    return Rule(
        net="+3V3", anchor_ref="IC1",
        spokes=[ManualSpoke(pad=str(p), cell="tpl") for p in pads],
    )


def _anchor_for(pads):
    return _make_anchor_fp("IC1",
                           [(str(p), 50.0 + 5.0 * (i + 1), 50.0, "+3V3")
                            for i, p in enumerate(pads)])


def _move_placed_onto_slots(placed, by_ref):
    """Simulate the board after a run: each planned component now sits on its
    own spoke slot (the tool moved it there)."""
    for p in placed:
        by_ref[p.ref].position = p.dest


def test_full_chain_redraw_assigns_components_in_spoke_order():
    """Sanity: the FULL redraw deterministically assigns C1 to spoke 1, C2 to
    spoke 2 (natural order). This is the reference assignment the isolated
    redraw must not break."""
    pads = ["1", "2"]
    anchor = _anchor_for(pads)
    c1 = _make_pool_fp("C1", "DECOUP", "+3V3")
    c2 = _make_pool_fp("C2", "DECOUP", "+3V3")
    adapter, by_ref = _adapter(anchor, [c1, c2])

    chain = _chain_with_n_spokes(pads)
    cfg = Config(layer="F.Cu", cells={"tpl": _single_role_cell()}, chains=[chain])

    placed, _vias, _tracks = ManualPositionCalculator(adapter, cfg).compute_raw_positions([chain])
    assert [p.ref for p in placed] == ["C1", "C2"]  # spoke 1 -> C1, spoke 2 -> C2


def test_isolated_spoke_redraw_keeps_its_own_component():
    """THE regression: after a full redraw (C1 on spoke 1, C2 on spoke 2), an
    isolated "Redraw spoke" of spoke 2 must re-claim C2 (its full-chain
    component), NOT drag C1 away from its neighbour — and must emit ONLY that
    spoke (the sibling is reserved, not placed)."""
    pads = ["1", "2"]
    anchor = _anchor_for(pads)
    c1 = _make_pool_fp("C1", "DECOUP", "+3V3")
    c2 = _make_pool_fp("C2", "DECOUP", "+3V3")
    adapter, by_ref = _adapter(anchor, [c1, c2])

    chain = _chain_with_n_spokes(pads)
    cfg = Config(layer="F.Cu", cells={"tpl": _single_role_cell()}, chains=[chain])
    calc = ManualPositionCalculator(adapter, cfg)

    # 1) Full redraw: C1 -> spoke 1, C2 -> spoke 2 (stable assignment), and the
    #    components physically move onto their slots.
    placed, _v, _t = calc.compute_raw_positions([chain])
    _move_placed_onto_slots(placed, by_ref)

    # 2) Isolated Redraw of spoke 2 ("pad 2") — the FULL chain is passed, the
    #    isolation tells the calculator which pad actually emits.
    isolate = {chain_effective_name(chain): {"2"}}
    placed_partial, _v, _t = calc.compute_raw_positions([chain], isolate_spokes=isolate)

    assert len(placed_partial) == 1          # sibling spoke 1 is reserved, not placed
    assert placed_partial[0].ref == "C2"     # and spoke 2 keeps ITS OWN component


def test_isolated_middle_spoke_reserves_both_neighbours():
    """Three-spoke chain: isolating the middle spoke must emit ONLY it and give
    it the same component a full redraw would (C2), reserving C1 and C3 for the
    two neighbours."""
    pads = ["1", "2", "3"]
    anchor = _anchor_for(pads)
    fps = [_make_pool_fp(f"C{i}", "DECOUP", "+3V3") for i in (1, 2, 3)]
    adapter, by_ref = _adapter(anchor, fps)

    chain = _chain_with_n_spokes(pads)
    cfg = Config(layer="F.Cu", cells={"tpl": _single_role_cell()}, chains=[chain])
    calc = ManualPositionCalculator(adapter, cfg)

    placed_full, _v, _t = calc.compute_raw_positions([chain])
    assert [p.ref for p in placed_full] == ["C1", "C2", "C3"]
    _move_placed_onto_slots(placed_full, by_ref)

    isolate = {chain_effective_name(chain): {"2"}}
    placed_partial, _v, _t = calc.compute_raw_positions([chain], isolate_spokes=isolate)

    assert [p.ref for p in placed_partial] == ["C2"]


def test_dependency_order_produces_only_the_active_spoke():
    """The dependency pass must mirror the real pass: with isolation on spoke
    2, the full chain's pool is consumed (C1 reserved by spoke 1) but ONLY the
    active spoke's ref (C2) is reported as produced — otherwise the Kahn order
    would treat a reserved (never-moved) component as this run's output."""
    from kicadstamp.placement.dependency_order import resolve_execution_order

    pads = ["1", "2"]
    anchor = _anchor_for(pads)
    c1 = _make_pool_fp("C1", "DECOUP", "+3V3")
    c2 = _make_pool_fp("C2", "DECOUP", "+3V3")
    adapter, _by_ref = _adapter(anchor, [c1, c2])

    chain = _chain_with_n_spokes(pads)
    cfg = Config(layer="F.Cu", cells={"tpl": _single_role_cell()}, chains=[chain])

    # Full chain (no isolation): both refs are produced, unchanged behaviour.
    full = resolve_execution_order(adapter, cfg)
    full_chain = [it for it in full if it.kind == "chain"][0]
    assert full_chain.produces == {"C1", "C2"}

    # Isolated spoke 2: only C2 is produced (C1 is reserved, not moved).
    isolated = resolve_execution_order(
        adapter, cfg, isolate_spokes={chain_effective_name(chain): {"2"}})
    iso_chain = [it for it in isolated if it.kind == "chain"][0]
    assert iso_chain.produces == {"C2"}
