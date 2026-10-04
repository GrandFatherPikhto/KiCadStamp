# tests/placement/test_scheduler_key_protection.py
"""У5.2 + У5.3 (plan §6): every registry key the REAL scheduler emits must be
PROTECTED from an `--only` prune, and, under the format gate, every record part
of that key must be the record's UUID — never its name.

The linkage that У5.1б's surviving mutation B4 broke is "the key a planner
builds" == "the anchor_id `apply_pipeline._compute_all_anchor_ids` protects": the
two must go through the SAME builder. A mutation that re-spells the nested key at
ONE call site (`_resolve_one_level`) makes the planner emit a key no protection
set contains, and `reconcile` then prunes the nested cell's copper.

У5.3 (Р-У5.1/Р-У5.2) re-spells the record-identifying PART of every key:
`name:<uuid>`, `point:<uuid>:…`, `thermal:<uuid>`, `net:<uuid>`, and the
`template_name` (the cell / net-trace identity) — while the physics stays
untouched (`anchor:`/`role:`/`pad:`, offsets, index, the `thermal_via_array`
literal). This file pins BOTH: the protection linkage AND the record part.

Here one config carries EVERY key form the У5.0 inventory lists — `name:` for an
Entity and for an absolute ClonePlacement, `point:`, `role:`, `anchor:`,
`thermal:`, `net:`, `pad:`, and a nested placement depth 2 — and the REAL
planners run on `FakeLiveBoardAdapter` (a local subclass only supplies the one
footprint the fake takes as a fixture, plus `get_bounding_boxes`, exactly like
the redraw harnesses). For each emitted `registry_key`:
  * its `anchor_id` is in `_compute_all_anchor_ids(cfg)`, AND
  * it starts with a prefix from `registry.PROTECTED_ANCHOR_PREFIXES`, AND
  * its record parts are EXACTLY the expected values for the active gate —
    names in format 2, the records' UUIDs in format 3 (`_EXPECTED`).

Р-У5.7 has its own cells: a record WITHOUT a uuid under the gate is a fatal (both
straight on the ONE helper and through each real producer). Parametrized over the
format gate (2 and 3) and over the FORM (so one broken form does not hide the
others).
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
from kicadstamp.exceptions import ValidationError
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.placement.services.manual_position_calculator import (
    ManualPositionCalculator,
)
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.registry import PROTECTED_ANCHOR_PREFIXES, record_key_part
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from tests.fakes.format3 import format3  # noqa: F401  (fixture for gate 3)
from tests.fakes.live_board import FakeLiveBoardAdapter

MM = 1_000_000

# The deterministic UUID every record of the fixture carries in format 3.
_UUID = {
    "leaf": "uuid-leaf", "lvl1": "uuid-lvl1", "lvl2": "uuid-lvl2",
    "P1": "uuid-P1",
    "cabs": "uuid-cabs", "cref": "uuid-cref", "crole": "uuid-crole",
    "cpoint": "uuid-cpoint", "cnest": "uuid-cnest",
    "E": "uuid-E",
    "ch1": "uuid-ch1",
    "tva1": "uuid-tva1",
    "nt1": "uuid-nt1",
}

# Per form: the EXACT anchor_id and template_name under each gate. Format 2 is
# the record NAME, format 3 its UUID (Р-У5.1/Р-У5.2); the physics parts (anchor
# ref/pad/offsets, role triple, pad number) and the thermal literal are
# byte-identical in both gates — that is the point of the table.
_EXPECTED = {
    "name":    {"anchor2": "name:cabs", "anchor3": "name:uuid-cabs",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "anchor":  {"anchor2": "anchor:U1::0.0000:0.0000",
                "anchor3": "anchor:U1::0.0000:0.0000",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "role":    {"anchor2": "role:FPGA:FPGA:FPGA::0.0000:0.0000",
                "anchor3": "role:FPGA:FPGA:FPGA::0.0000:0.0000",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "point":   {"anchor2": "point:P1:0.0000:0.0000",
                "anchor3": "point:uuid-P1:0.0000:0.0000",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "nested":  {"anchor2": "name:cnest/inner1/inner2",
                "anchor3": "name:uuid-cnest/inner1/inner2",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "entity":  {"anchor2": "name:E", "anchor3": "name:uuid-E",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "thermal": {"anchor2": "thermal:tva1", "anchor3": "thermal:uuid-tva1",
                "tpl2": "thermal_via_array", "tpl3": "thermal_via_array"},
    "pad":     {"anchor2": "pad:1", "anchor3": "pad:1",
                "tpl2": "leaf", "tpl3": "uuid-leaf"},
    "net":     {"anchor2": "net:nt1", "anchor3": "net:uuid-nt1",
                "tpl2": "nt1", "tpl3": "uuid-nt1"},
}


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
    return Cell(name="leaf", layer="F.Cu", uuid=_UUID["leaf"], vias=[
        TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="GND",
                    drill_mm=0.3, diameter_mm=0.6)],
        tracks=[TemplateTrack(start_along_mm=-1.0, start_across_mm=0.0,
                              end_along_mm=1.0, end_across_mm=0.0,
                              width_mm=0.25, net="GND")])


def _cfg():
    """ONE config carrying all key forms (see the module docstring).

    Every record carries its format-3 `uuid` (and the reference `*_uuid`), so
    the SAME fixture exercises both gates: format 2 builders keep the names,
    format 3 builders must use these uuids (Р-У5.1/Р-У5.2)."""
    return Config(
        layer="F.Cu",
        cells={
            "leaf": _leaf(),
            "lvl2": Cell(name="lvl2", uuid=_UUID["lvl2"], clone_placements=[
                CellPlacement(name="inner2", cell="leaf", xy=(1.0, 0.0))]),
            "lvl1": Cell(name="lvl1", uuid=_UUID["lvl1"], clone_placements=[
                CellPlacement(name="inner1", cell="lvl2", xy=(1.0, 0.0))]),
        },
        points={"P1": Point(name="P1", xy=(10.0, 20.0), uuid=_UUID["P1"])},
        clone_placements=[
            ClonePlacement(cluster="cabs", cell="leaf", xy=(0.0, 0.0),
                           uuid=_UUID["cabs"]),
            ClonePlacement(cluster="cref", cell="leaf", xy=(0.0, 0.0),
                           anchor_ref="U1", uuid=_UUID["cref"]),
            ClonePlacement(cluster="crole", cell="leaf", xy=(0.0, 0.0),
                           anchor_role="FPGA", anchor_sheet="FPGA",
                           anchor_cluster="FPGA", uuid=_UUID["crole"]),
            ClonePlacement(cluster="cpoint", cell="leaf", xy=(0.0, 0.0),
                           anchor_point="P1", anchor_point_uuid=_UUID["P1"],
                           uuid=_UUID["cpoint"]),
            ClonePlacement(cluster="cnest", cell="lvl1", xy=(0.0, 0.0),
                           uuid=_UUID["cnest"]),
        ],
        entities=[Entity(name="E", cell="leaf", cluster="FPGA",
                         uuid=_UUID["E"], cell_uuid=_UUID["leaf"])],
        trees=[Tree(name="etree",
                    anchor=TreeAnchor(role="FPGA", anchor_sheet="FPGA",
                                      anchor_cluster="FPGA"),
                    nodes=[TreeNode(ref="E", kind="placement", xy=(0.0, 0.0),
                                    polar=None, rotation=0.0, name=None,
                                    group=None)])],
        chains=[Chain(net="GND", name="ch1", anchor_ref="U1", uuid=_UUID["ch1"],
                      spokes=[ManualSpoke(pad="1", cell="leaf")])],
        thermal_via_arrays=[ThermalViaArrayConfig(
            name="tva1", uuid=_UUID["tva1"], anchor_ref="U1", pad="1", net="GND",
            rows=1, cols=1, margin_mm=0.0, pattern="grid", drill_mm=0.3,
            diameter_mm=0.6)],
        net_traces=[NetTrace(
            name="nt1", uuid=_UUID["nt1"], net="GND", anchor_role="FPGA",
            anchor_sheet="FPGA", anchor_cluster="FPGA",
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
        # Pin the build to format 3 through the ONE documented mechanism.
        request.getfixturevalue("format3")
    else:
        monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)

    cfg = _cfg()
    adapter = _AllFormsAdapter()
    commands = _PRODUCERS[form](cfg, adapter)
    keys = [c.registry_key for c in commands if getattr(c, "registry_key", None)]
    assert keys, f"form {form!r} produced no registry-keyed command (vacuous row)"

    expected = _EXPECTED[form]
    protected = _compute_all_anchor_ids(cfg)
    for key in keys:
        parts = key.split("|")
        assert len(parts) == 4, f"form {form!r}: malformed key {key!r}"
        anchor_id, template_name = parts[0], parts[1]

        # У5.3 (Р-У5.1/Р-У5.2): the record part is the UUID under the gate and
        # the NAME in format 2 — the expected value carries "name absent".
        assert anchor_id == expected[f"anchor{gate}"], (
            f"form {form!r} gate {gate}: anchor_id {anchor_id!r} != "
            f"{expected[f'anchor{gate}']!r} (a builder left a name in a format-3 "
            f"key, or the gate is off by one)")
        assert template_name == expected[f"tpl{gate}"], (
            f"form {form!r} gate {gate}: template_name {template_name!r} != "
            f"{expected[f'tpl{gate}']!r} (Р-У5.1 covers template_name too)")

        # The У5.1/У5.1б protection linkage, unchanged by У5.3.
        assert anchor_id.startswith(PROTECTED_ANCHOR_PREFIXES), (
            f"form {form!r}: registry anchor_id {anchor_id!r} has no protected "
            f"prefix (PROTECTED_ANCHOR_PREFIXES={PROTECTED_ANCHOR_PREFIXES})")
        assert anchor_id in protected, (
            f"form {form!r}: the scheduler emitted {anchor_id!r}, which "
            f"_compute_all_anchor_ids does not protect — reconcile() would prune "
            f"its copper on an --only run")


# ── Р-У5.7: a record without a uuid under the gate is a FATAL ────────────────

def test_record_key_part_returns_uuid_in_format3(format3):  # noqa: F811
    assert record_key_part("leaf", _UUID["leaf"]) == _UUID["leaf"]


def test_record_key_part_returns_name_in_format2(monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    assert record_key_part("leaf", _UUID["leaf"]) == "leaf"
    # Without the gate a missing uuid is irrelevant — the name is the value.
    assert record_key_part("leaf", None) == "leaf"


def test_record_key_part_without_uuid_is_a_fatal_in_format3(format3):  # noqa: F811
    with pytest.raises(ValidationError):
        record_key_part("leaf", None)


def _blank_uuid_for(cfg, form):
    """Remove the uuid that the form's key must read, so the producer either
    resolves a record-identifying part from a record with no uuid (anchor-side)
    or a template_name from a cell with no uuid."""
    if form in ("name", "nested"):
        record = next(c for c in cfg.clone_placements
                      if c.cluster == ("cabs" if form == "name" else "cnest"))
        record.uuid = None
    elif form == "point":
        next(c for c in cfg.clone_placements if c.cluster == "cpoint").anchor_point_uuid = None
    elif form == "entity":
        cfg.entities[0].uuid = None
    elif form == "thermal":
        cfg.thermal_via_arrays[0].uuid = None
    elif form == "net":
        cfg.net_traces[0].uuid = None
    else:
        # anchor/role/pad: the anchor_id is physics — the template_name is the
        # cell, so blanking the cell's uuid must fatal.
        cfg.cells["leaf"].uuid = None


@pytest.mark.parametrize("form", sorted(_PRODUCERS))
def test_record_without_uuid_is_a_fatal_under_gate3(form, format3):  # noqa: F811
    cfg = _cfg()
    _blank_uuid_for(cfg, form)
    with pytest.raises(ValidationError):
        _PRODUCERS[form](cfg, _AllFormsAdapter())
