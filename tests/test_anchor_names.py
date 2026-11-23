# tests/test_anchor_names.py
"""С2 cells (plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 23.11): a Log
line names the RECORD, not its uuid.

Under the format-3 gate an anchor id carries a uuid — «name:6038e889-…: 1» — while
the user needs «spoke_rst_switcha: 1». ONE map answers it, built with the very
builders that WRITE those ids (`entity_anchor_id` / `clone_anchor_id`,
`thermal_anchor_id`, `net_trace_anchor_id`), so a name can never be attached to
somebody else's key.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.anchor_names import name_for, named_anchors
from kicadstamp.config import format_version


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


def _cfg(**kw):
    base = dict(entities=[], clone_placements=[], cells={},
                thermal_via_arrays=[], net_traces=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_an_entity_anchor_names_its_record(gate):
    """The map is keyed by `entity_anchor_id` — the builder that WRITES the registry
    key — so the name and the key can never drift apart."""
    from kicadstamp.placement.services.clone_position_calculator import (
        entity_anchor_id,
    )

    entity = SimpleNamespace(name="spoke_rst_switcha", uuid="uuid-e1",
                             cluster="SPOKE_RST", sheet="SwitchA", cell="spoke",
                             retired=False)
    names = named_anchors(_cfg(entities=[entity]))

    assert names[entity_anchor_id(entity)] == "spoke_rst_switcha"
    assert name_for(names, entity_anchor_id(entity)) == "spoke_rst_switcha"


def test_a_net_trace_anchor_names_its_record(gate):
    from kicadstamp.net_trace_planner import net_trace_anchor_id

    record = SimpleNamespace(name="5v_idc__driver__spoke_p5v_2", net="+5V_IDC",
                             uuid="uuid-nt", retired=False)
    names = named_anchors(_cfg(net_traces=[record]))

    assert name_for(names, net_trace_anchor_id(record)) == \
        "5v_idc__driver__spoke_p5v_2"


def test_a_thermal_array_anchor_names_its_record(gate):
    from kicadstamp.placement.services.via_planner import thermal_anchor_id

    array = SimpleNamespace(name="PAD_THERMAL", uuid="uuid-t", retired=False)
    names = named_anchors(_cfg(thermal_via_arrays=[array]))

    assert name_for(names, thermal_anchor_id(array)) == "PAD_THERMAL"


def test_an_unknown_anchor_keeps_its_id_and_a_point_needs_no_entry(gate):
    """An ORPHAN key (its record is gone from the config) has no name — the anchor
    id comes back unchanged, never a placeholder invented on the spot. A `point:`
    anchor already carries the point's name."""
    names = named_anchors(_cfg())

    assert name_for(names, "name:6038e889-dead-beef") == "name:6038e889-dead-beef"
    assert name_for(names, "point:VCC_SENSE") == "point:VCC_SENSE"


def test_a_nested_anchor_is_never_split(gate):
    """A nested anchor is `<outer>/<nested>`, and a slash is legal INSIDE a cluster
    name (`clone_position_calculator.nested_anchor_id` says so) — splitting the path
    would be a guess, so the id stays whole."""
    names = {"name:outer-id": "outer"}

    assert name_for(names, "name:outer-id/nested_1") == "name:outer-id/nested_1"
