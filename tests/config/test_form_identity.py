# tests/config/test_form_identity.py
"""The pure rule behind part 0 of plan_2026_10_05_uuid_tails —
`kicadstamp.config.form_identity.identify`: a form-built record gets the identity
of the record it replaces (its own `uuid` + every reference's `<field>_uuid`).

Qt-free: these cells build a Config in Python and call the rule directly. The
dock-level half (the five redraw paths) lives in
tests/gui/docks/test_form_identity_redraw.py.
"""
import pytest

from kicadstamp.config.form_identity import FormReferenceMissing, identify, section_uuids
from kicadstamp.config.models import (Cell, Chain, Config, ManualSpoke,
                                      ThermalViaArrayConfig)
from kicadstamp.config.points import Point


def _cfg() -> Config:
    return Config(
        cells={"c1": Cell(name="c1", uuid="U-CELL")},
        points={"p1": Point(name="p1", anchor_role="FPGA", uuid="U-POINT")},
        thermal_via_arrays=[
            ThermalViaArrayConfig(name="tva1", pad="1", uuid="U-TVA")],
        chains=[Chain(net="CL", uuid="U-CHAIN", spokes=[])],
    )


def test_section_uuids_reads_both_dict_and_list_sections():
    cfg = _cfg()
    assert section_uuids(cfg, "cells") == {"c1": "U-CELL"}
    assert section_uuids(cfg, "points") == {"p1": "U-POINT"}
    assert section_uuids(cfg, "thermal_via_arrays") == {"tva1": "U-TVA"}


def test_own_uuid_is_inherited_from_the_record_with_the_same_name():
    out = identify({"name": "tva1", "pad": "2"}, "thermal_via_arrays", cfg=_cfg())
    assert out["uuid"] == "U-TVA"


def test_a_rename_keeps_the_remembered_uuid():
    """The form was loaded from record U-TVA; typing a new name must still edit
    THAT record, so the remembered uuid wins over a name lookup that now misses."""
    out = identify({"name": "renamed", "pad": "2"}, "thermal_via_arrays",
                   cfg=_cfg(), remembered_uuid="U-TVA")
    assert out["uuid"] == "U-TVA"


def test_a_brand_new_record_reuses_the_draft_uuid():
    first = identify({"name": "fresh", "pad": "1"}, "thermal_via_arrays", cfg=_cfg())
    again = identify({"name": "fresh", "pad": "1"}, "thermal_via_arrays",
                     cfg=_cfg(), draft_uuid=first["uuid"])
    assert first["uuid"]
    assert again["uuid"] == first["uuid"]


def test_an_existing_uuid_on_the_entry_is_kept():
    out = identify({"name": "x", "pad": "1", "uuid": "U-KEEP"},
                   "thermal_via_arrays", cfg=_cfg())
    assert out["uuid"] == "U-KEEP"


def test_a_name_hint_resolves_the_reference_uuid():
    out = identify({"name": "tva1", "pad": "1", "anchor_point": "p1"},
                   "thermal_via_arrays", cfg=_cfg())
    assert out["anchor_point_uuid"] == "U-POINT"


def test_a_chain_spokes_cell_resolves_to_cell_uuid():
    cfg = _cfg()
    cfg.chains = [Chain(net="CL", name="CL", uuid="U-CHAIN",
                        spokes=[ManualSpoke(pad="1", cell="c1")])]
    out = identify({"name": "CL", "spokes": [{"pad": "1", "cell": "c1"}]},
                   "chains", cfg=cfg, identity="CL",
                   identity_of=lambda m: m.name or m.net)
    assert out["uuid"] == "U-CHAIN"
    assert out["spokes"][0]["cell_uuid"] == "U-CELL"


def test_a_clone_placement_resolves_cell_and_anchor_point():
    cfg = _cfg()
    out = identify({"cluster": "C1", "cell": "c1", "anchor_point": "p1"},
                   "clone_placements", cfg=cfg, identity="C1",
                   identity_of=lambda m: m.name or m.cluster)
    assert out["cell_uuid"] == "U-CELL"
    assert out["anchor_point_uuid"] == "U-POINT"


def test_a_reference_name_with_no_target_is_refused():
    with pytest.raises(FormReferenceMissing) as e:
        identify({"name": "tva1", "pad": "1", "anchor_point": "typo"},
                 "thermal_via_arrays", cfg=_cfg())
    assert "typo" in str(e.value)


def test_a_target_without_a_uuid_carries_nothing_and_is_not_a_refusal():
    """A format-2 graph (or a fake) may hold the target WITHOUT a uuid: the name
    resolves, there is simply nothing to carry across."""
    cfg = _cfg()
    cfg.points = {"p1": Point(name="p1", anchor_role="FPGA")}   # no uuid
    out = identify({"name": "tva1", "pad": "1", "anchor_point": "p1"},
                   "thermal_via_arrays", cfg=cfg)
    assert out.get("anchor_point_uuid") is None
