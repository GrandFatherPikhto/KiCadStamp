# tests/placement/test_scheduler_key_protection.py
"""У5.2 (plan §6, mandatory cell added after the У5.1б acceptance, closes the
surviving mutation B4): every registry key the REAL scheduler emits must be
PROTECTED from an `--only` prune.

The linkage that B4 broke is "the key a planner builds" == "the anchor_id
`apply_pipeline._compute_all_anchor_ids` protects": the two must go through the
SAME builder. A mutation that re-spells the nested key at ONE call site
(`_resolve_one_level`) makes the planner emit a key no protection set contains,
and `reconcile` then prunes the nested cell's copper. No test saw it because the
format of the nested key was never checked THROUGH the scheduler.

Here one config carries EVERY key form the У5.0 inventory lists — `name:` for an
Entity and for an absolute ClonePlacement, `point:`, `role:`, `anchor:`,
`thermal:`, `net:`, `pad:`, and a nested placement depth 2 — and the REAL
planners run on `FakeLiveBoardAdapter` (a local subclass only supplies the one
footprint the fake takes as a fixture, plus `get_bounding_boxes`, exactly like
the redraw harnesses). For each emitted `registry_key`:
  * its `anchor_id` is in `_compute_all_anchor_ids(cfg)`, AND
  * it starts with a prefix from `registry.PROTECTED_ANCHOR_PREFIXES`.
Parametrized over the format gate (2 and 3) and over the FORM (so one broken
form does not hide the others).
"""
from unittest.mock import MagicMock

import pytest

from kicadstamp.apply_pipeline import _compute_all_anchor_ids
from kicadstamp.config import (
    Cell, CellPlacement, Chain, ClonePlacement, Config, Entity, ManualSpoke,
    NetTrace, TemplateTrack, TemplateVia, ThermalViaArrayConfig,
)
from kicadstamp.config import Point
from kicadstamp.config import format_version
from kicadstamp.domain.geometry import Vector2
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.placement.services.manual_position_calculator import (
    ManualPositionCalculator,
)
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.registry import PROTECTED_ANCHOR_PREFIXES
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from tests.fakes.format3 import format3  # noqa: F401  (fixture for gate 3)
from tests.fakes.live_board import FakeLiveBoardAdapter

MM = 1_000_000


def _make_fp():
    """One footprint `U1` with Role/Cluster/Sheet = FPGA, at (65.0, -65.0) mm,
    carrying pad `1` (size 4 mm, so a 1x1 thermal grid is placeable)."""
    fp = MagicMock()
    fp.ref = "U1"
    fp.uuid = "fp-uuid"
    fp.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    fp.angle_deg = 0.0
    fp.rotation = 0.0

    def _field(field):
        return "FPGA" if field in ("Role", "Cluster", "Sheet") else None
    fp.get_field_value = _field

    pad = MagicMock()
    pad.number = "1"
    pad.net_name = "GND"
    pad.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    pad.size = Vector2.from_xy(4.0 * MM, 4.0 * MM)
    pad.angle_rad = 0.0
    fp.pads = {"1": pad}

    def _pad(num):
        return fp.pads.get(str(num))
    fp.pad = _pad
    fp.definition = MagicMock(items=[])
    return fp


class _AllFormsAdapter(FakeLiveBoardAdapter):
    """The shared live-board fake plus the two things its own fixture does not
    carry: the footprint above and `get_bounding_boxes` (a REAL adapter method
    the thermal keepout reads)."""

    def __init__(self):
        super().__init__(_make_fp())

    def get_bounding_boxes(self):
        return []


def _leaf():
    return Cell(name="leaf", layer="F.Cu", vias=[
        TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="GND",
                    drill_mm=0.3, diameter_mm=0.6)],
        tracks=[TemplateTrack(start_along_mm=-1.0, start_across_mm=0.0,
                              end_along_mm=1.0, end_across_mm=0.0,
                              width_mm=0.25, net="GND")])


def _cfg():
    """ONE config carrying all key forms (see the module docstring)."""
    return Config(
        layer="F.Cu",
        cells={
            "leaf": _leaf(),
            "lvl2": Cell(name="lvl2", clone_placements=[
                CellPlacement(name="inner2", cell="leaf", xy=(1.0, 0.0))]),
            "lvl1": Cell(name="lvl1", clone_placements=[
                CellPlacement(name="inner1", cell="lvl2", xy=(1.0, 0.0))]),
        },
        points={"P1": Point(name="P1", xy=(10.0, 20.0))},
        clone_placements=[
            ClonePlacement(cluster="cabs", cell="leaf", xy=(0.0, 0.0)),
            ClonePlacement(cluster="cref", cell="leaf", xy=(0.0, 0.0),
                           anchor_ref="U1"),
            ClonePlacement(cluster="crole", cell="leaf", xy=(0.0, 0.0),
                           anchor_role="FPGA", anchor_sheet="FPGA",
                           anchor_cluster="FPGA"),
            ClonePlacement(cluster="cpoint", cell="leaf", xy=(0.0, 0.0),
                           anchor_point="P1"),
            ClonePlacement(cluster="cnest", cell="lvl1", xy=(0.0, 0.0)),
        ],
        entities=[Entity(name="E", cell="leaf", cluster="FPGA")],
        trees=[Tree(name="etree",
                    anchor=TreeAnchor(role="FPGA", anchor_sheet="FPGA",
                                      anchor_cluster="FPGA"),
                    nodes=[TreeNode(ref="E", kind="placement", xy=(0.0, 0.0),
                                    polar=None, rotation=0.0, name=None,
                                    group=None)])],
        chains=[Chain(net="GND", name="ch1", anchor_ref="U1",
                      spokes=[ManualSpoke(pad="1", cell="leaf")])],
        thermal_via_arrays=[ThermalViaArrayConfig(
            name="tva1", anchor_ref="U1", pad="1", net="GND", rows=1, cols=1,
            margin_mm=0.0, pattern="grid", drill_mm=0.3, diameter_mm=0.6)],
        net_traces=[NetTrace(
            name="nt1", net="GND", anchor_role="FPGA", anchor_sheet="FPGA",
            anchor_cluster="FPGA",
            tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                                  end_along_mm=2.0, end_across_mm=0.0,
                                  width_mm=0.25, net="GND", layer="F.Cu")],
            vias=[TemplateVia(net="GND", drill_mm=0.3, diameter_mm=0.6)])],
        skip_existing_components=False,
        via_keepout_clearance_mm=0.2,
        via_search_step_mm=0.1,
        via_search_max_radius_mm=3.0,
        via_search_n_directions=8,
    )


def _clone_commands(cfg, adapter, clones):
    _p, vias, tracks = ClonePositionCalculator(adapter, cfg, {}).compute_raw_positions(clones)
    return vias + tracks


# ── one producer per FORM, each running the REAL planner ────────────────────

def _p_name_clone(cfg, adapter):
    clone = next(c for c in cfg.clone_placements if c.cluster == "cabs")
    return _clone_commands(cfg, adapter, [clone])


def _p_anchor_clone(cfg, adapter):
    clone = next(c for c in cfg.clone_placements if c.cluster == "cref")
    return _clone_commands(cfg, adapter, [clone])


def _p_role_clone(cfg, adapter):
    clone = next(c for c in cfg.clone_placements if c.cluster == "crole")
    return _clone_commands(cfg, adapter, [clone])


def _p_point_clone(cfg, adapter):
    clone = next(c for c in cfg.clone_placements if c.cluster == "cpoint")
    return _clone_commands(cfg, adapter, [clone])


def _p_nested(cfg, adapter):
    clone = next(c for c in cfg.clone_placements if c.cluster == "cnest")
    return _clone_commands(cfg, adapter, [clone])


def _p_entity(cfg, adapter):
    clones = materialize_entity_placements(adapter, cfg, {})
    return _clone_commands(cfg, adapter, clones)


def _p_thermal(cfg, adapter):
    return ViaPlanner(adapter, cfg).plan_vias([], [])


def _p_pad(cfg, adapter):
    _p, vias, tracks = ManualPositionCalculator(adapter, cfg).compute_raw_positions(
        list(cfg.chains))
    return vias + tracks


def _p_net(cfg, adapter):
    vias, tracks = plan_net_traces(adapter, list(cfg.net_traces))
    return vias + tracks


_PRODUCERS = {
    "name": _p_name_clone,
    "anchor": _p_anchor_clone,
    "role": _p_role_clone,
    "point": _p_point_clone,
    "nested": _p_nested,
    "entity": _p_entity,
    "thermal": _p_thermal,
    "pad": _p_pad,
    "net": _p_net,
}


@pytest.mark.parametrize("gate", [2, 3])
@pytest.mark.parametrize("form", sorted(_PRODUCERS))
def test_every_scheduler_key_is_protected(form, gate, monkeypatch, request):
    if gate == 3:
        # Pin the build to format 3 through the ONE documented mechanism (the
        # У5.3 builders will make the keys UUID-based under this gate; today the
        # cell proves the builder<->protection linkage holds under both gates).
        request.getfixturevalue("format3")
    else:
        monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)

    cfg = _cfg()
    adapter = _AllFormsAdapter()
    commands = _PRODUCERS[form](cfg, adapter)
    keys = [c.registry_key for c in commands if getattr(c, "registry_key", None)]
    assert keys, f"form {form!r} produced no registry-keyed command (vacuous row)"

    protected = _compute_all_anchor_ids(cfg)
    for key in keys:
        anchor_id = key.split("|", 1)[0]
        assert anchor_id.startswith(PROTECTED_ANCHOR_PREFIXES), (
            f"form {form!r}: registry anchor_id {anchor_id!r} has no protected "
            f"prefix (PROTECTED_ANCHOR_PREFIXES={PROTECTED_ANCHOR_PREFIXES})")
        assert anchor_id in protected, (
            f"form {form!r}: the scheduler emitted {anchor_id!r}, which "
            f"_compute_all_anchor_ids does not protect — reconcile() would prune "
            f"its copper on an --only run")
