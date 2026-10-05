# tests/selection/test_selection_narrowing.py
"""Cells for the mixed-selection narrowing
(plan_2026_10_04_refresh_mixed_cluster_selection).

Every cell runs under BOTH gates — format 2 (registry keys by NAME) and format 3
(keys by UUID) — through the ``gate`` fixture, so the format-gated key reading
(record_key_part) is exercised on both. The pure rule
(``kicadstamp.selection_narrowing``) is driven directly; one integration cell
drives ``gui.mixed_selection.narrow_mixed_selection`` against a real config path
and real registry files, and one covers the "Fill from selection" filtering.
"""
import json

import pytest

from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.registry import make_registry_key, record_key_part
from kicadstamp.selection_narrowing import (
    FootprintInfo,
    cell_clusters,
    cell_record_addresses,
    choose_instance,
    group_selection,
    subtract_foreign_copper,
)
from tests.fakes.format3 import det_uuid, identity_value


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


# ── light stand-ins for the loaded config and the live selection ───────────

class _Rec:
    def __init__(self, name=None, uuid=None, cell=None, cluster=None, sheet=None):
        self.name = name
        self.uuid = uuid
        self.cell = cell
        self.cluster = cluster
        self.sheet = sheet


class _Cfg:
    def __init__(self, entities=(), clone_placements=(), cells=None):
        self.entities = list(entities)
        self.clone_placements = list(clone_placements)
        self.cells = cells or {}


class _FP:
    def __init__(self, ref):
        self.ref = ref


class _Copper:
    def __init__(self, uuid):
        self.uuid = uuid


def _fi(ref, role, cluster, sheet=()):
    """A FootprintInfo whose sheet chain is already RESOLVED names."""
    return FootprintInfo(item=_FP(ref), role=role, cluster=cluster,
                         sheet=tuple(sheet))


# ── С1: a mixed selection picks the cell's cluster ──────────────────────────

def test_mixed_selection_chooses_the_cells_cluster(gate):
    """The DAC_BUF cluster is selected together with two PIF clusters; only the
    one whose Cluster is the cell's (DAC_BUF) and whose role set is the cell's
    qualifies."""
    ent = _Rec(name="dac_buf_channel_0", uuid=det_uuid("entities:dac_buf_channel_0"),
               cell="dac_buf", cluster="DAC_BUF")
    cfg = _Cfg(entities=[ent])
    infos = [_fi("C1", "DA", "DAC_BUF"), _fi("C2", "DB", "DAC_BUF"),
             _fi("C3", "PA", "PIF_AVDD"), _fi("C4", "PB", "PIF_AVDD"),
             _fi("C5", "PC", "PIF_OA_N2V5")]
    groups = group_selection(infos, cfg.entities)
    assert set(groups) == {("DAC_BUF", None), ("PIF_AVDD", None),
                           ("PIF_OA_N2V5", None)}
    choice = choose_instance(groups, {"DA", "DB"}, cell_clusters(cfg, "dac_buf"))
    assert choice.chosen_key == ("DAC_BUF", None)
    assert [m.item.ref for m in choice.members] == ["C1", "C2"]
    assert {key for key, _count in choice.others} == {("PIF_AVDD", None),
                                                      ("PIF_OA_N2V5", None)}


# ── С2: two instances of the cell are ambiguous ─────────────────────────────

def test_two_instances_of_the_cell_are_ambiguous(gate):
    """Two DAC_BUF groups on two sheets (cloned sheets) — more than one
    candidate; the caller must refuse with a list, not pick the first."""
    infos = [_fi("C1", "DA", "DAC_BUF", ("Channel_0",)),
             _fi("C2", "DB", "DAC_BUF", ("Channel_0",)),
             _fi("C3", "DA", "DAC_BUF", ("Channel_1",)),
             _fi("C4", "DB", "DAC_BUF", ("Channel_1",))]
    groups = group_selection(infos)
    assert set(groups) == {("DAC_BUF", "Channel_0"), ("DAC_BUF", "Channel_1")}
    choice = choose_instance(groups, {"DA", "DB"}, {"DAC_BUF"})
    assert choice.chosen_key is None
    assert len(choice.candidate_groups) == 2


# ── С3: no candidate — today's refusal path (nothing is chosen) ─────────────

def test_no_candidate_leaves_todays_refusal(gate):
    """The selection is one group, but the cell has a role the group lacks — no
    candidate, so the caller keeps its (role) refusal."""
    infos = [_fi("C1", "DA", "DAC_BUF"), _fi("C2", "DB", "DAC_BUF")]
    groups = group_selection(infos)
    choice = choose_instance(groups, {"DA", "DB", "DC"}, {"DAC_BUF"})
    assert choice.chosen_key is None
    assert choice.candidate_groups == ()


# ── С4: PIF disambiguation is by the cell's cluster, not roles ──────────────

def test_pif_instance_chosen_by_cell_cluster(gate):
    """PIF_AVDD and PIF_OA_N2V5 carry the SAME roles; only the cell's cluster
    (PIF_AVDD) tells them apart. A role-only rule would see two candidates."""
    ent = _Rec(name="pif_avdd", uuid=det_uuid("entities:pif_avdd"),
               cell="pif_avdd", cluster="PIF_AVDD")
    cfg = _Cfg(entities=[ent])
    roles = {"C_IN_BULK", "C_IN_BYPASS"}
    infos = [_fi("D1", "R_DA", "DAC_BUF"), _fi("D2", "R_DB", "DAC_BUF"),
             _fi("P1", "C_IN_BULK", "PIF_AVDD"), _fi("P2", "C_IN_BYPASS", "PIF_AVDD"),
             _fi("Q1", "C_IN_BULK", "PIF_OA_N2V5"),
             _fi("Q2", "C_IN_BYPASS", "PIF_OA_N2V5")]
    groups = group_selection(infos, cfg.entities)
    choice = choose_instance(groups, roles, cell_clusters(cfg, "pif_avdd"))
    assert choice.chosen_key == ("PIF_AVDD", None)
    # Without the cluster step the roles alone would make TWO candidates.
    role_only = choose_instance(groups, roles, ())
    assert len(role_only.candidate_groups) == 2


# ── С5: unknown cell cluster — roles only, and two groups are ambiguous ─────

def test_unknown_cell_cluster_falls_back_to_roles(gate):
    roles = {"A", "B"}
    infos = [_fi("C1", "A", "CL_1"), _fi("C2", "B", "CL_1"),
             _fi("C3", "A", "CL_2"), _fi("C4", "B", "CL_2")]
    groups = group_selection(infos)
    choice = choose_instance(groups, roles, ())
    assert choice.chosen_key is None
    assert len(choice.candidate_groups) == 2


# ── С6: registry subtraction, both gates ────────────────────────────────────

def _cfg_two_instances():
    cell_uuid = det_uuid("cells:dac_buf")
    chosen = _Rec(name="dac0", uuid=det_uuid("entities:dac0"), cell="dac_buf",
                  cluster="DAC_BUF", sheet="Channel_0")
    other = _Rec(name="dac1", uuid=det_uuid("entities:dac1"), cell="dac_buf",
                 cluster="DAC_BUF", sheet="Channel_1")
    pif_uuid = det_uuid("cells:pif_avdd")
    pif = _Rec(name="pif0", uuid=det_uuid("entities:pif0"), cell="pif_avdd",
               cluster="PIF_AVDD")
    cfg = _Cfg(entities=[chosen, other, pif],
               cells={"dac_buf": _Rec(uuid=cell_uuid),
                      "pif_avdd": _Rec(uuid=pif_uuid)})
    return cfg, cell_uuid, chosen, other, pif, pif_uuid


def test_subtract_foreign_copper(gate):
    cfg, cell_uuid, chosen, other, pif, pif_uuid = _cfg_two_instances()
    cell_identity = record_key_part("dac_buf", cell_uuid)
    own_addresses = cell_record_addresses(cfg, "dac_buf")
    # The addresses: the chosen instance on Channel_0, the twin on Channel_1.
    assert set(own_addresses.values()) == {("DAC_BUF", "Channel_0"),
                                           ("DAC_BUF", "Channel_1")}
    chosen_address = ("DAC_BUF", "Channel_0")
    id_chosen = identity_value("dac0", chosen.uuid)
    id_other = identity_value("dac1", other.uuid)
    id_pif = identity_value("pif0", pif.uuid)

    own = _Copper("own")
    other_inst = _Copper("other")           # same cell, ANOTHER instance
    net_trace = _Copper("net")              # inter-cluster copper (net:)
    physical = _Copper("pad")               # physics prefix — foreign
    other_cell = _Copper("pif")             # a record of ANOTHER cell
    unregistered = _Copper("unreg")         # not in the registry -> KEPT
    owner = {
        "own": make_registry_key("name:" + id_chosen, cell_identity, "r", 0),
        "other": make_registry_key("name:" + id_other, cell_identity, "r", 0),
        "net": make_registry_key("net:x", cell_identity, "r", 0),
        "pad": make_registry_key("pad:1", cell_identity, "r", 0),
        "pif": make_registry_key("name:" + id_pif,
                                 record_key_part("pif_avdd", pif_uuid), "r", 0),
    }
    sub = subtract_foreign_copper(
        [own, other_inst, net_trace, physical, other_cell, unregistered],
        owner, cell_identity, own_addresses, chosen_address)
    assert {i.uuid for i in sub.kept} == {"own", "unreg"}
    assert {i.uuid for i in sub.removed} == {"other", "net", "pad", "pif"}


# ── integration: gui.mixed_selection.narrow_mixed_selection ─────────────────

class _Adapter:
    def __init__(self, fields):
        self._fields = fields

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster


def _write_registries(tmp_path, via_entries, track_entries):
    schema = 2 if current_format() >= 3 else 1
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir()
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **track_entries}),
        encoding="utf-8")


def _via_entry(uuid):
    return {"uuid": uuid, "x_mm": 0.0, "y_mm": 0.0, "net": "N",
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_entry(uuid):
    return {"uuid": uuid, "start_x_mm": 0.0, "start_y_mm": 0.0,
            "end_x_mm": 1.0, "end_y_mm": 1.0, "width_mm": 0.25,
            "net": "N", "layer": "F.Cu"}


def test_narrow_mixed_selection_keeps_own_copper_and_reads_clean(gate, tmp_path):
    from gui.mixed_selection import narrow_mixed_selection

    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    cfg, cell_uuid, chosen, other, _pif, _pif_uuid = _cfg_two_instances()
    cell_identity = record_key_part("dac_buf", cell_uuid)
    id_chosen = identity_value("dac0", chosen.uuid)
    id_other = identity_value("dac1", other.uuid)
    own_v = make_registry_key("name:" + id_chosen, cell_identity, "r", 0)
    other_v = make_registry_key("name:" + id_other, cell_identity, "r", 0)
    own_t = make_registry_key("name:" + id_chosen, cell_identity, "r", 0)
    other_t = make_registry_key("name:" + id_other, cell_identity, "r", 0)
    _write_registries(
        tmp_path,
        {own_v: _via_entry("v_own"), other_v: _via_entry("v_other")},
        {own_t: _track_entry("t_own"), other_t: _track_entry("t_other")})

    class _FpRef:
        def __init__(self, ref, chain):
            self.ref = ref
            self.sheet_path_uuids = tuple(chain)

    fp_c1 = _FpRef("C1", ("ch0", "s1"))
    fp_c2 = _FpRef("C2", ("ch0", "s2"))
    fp_p1 = _FpRef("P1", ("ch1", "s3"))
    fp_p2 = _FpRef("P2", ("ch1", "s4"))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "P1": ("C_IN_BULK", "PIF_AVDD"),
                        "P2": ("C_IN_BYPASS", "PIF_AVDD")})
    sheet_names = {"ch0": "Channel_0", "ch1": "Channel_1"}
    v_own, v_other, v_unreg = _Copper("v_own"), _Copper("v_other"), _Copper("v_unreg")
    t_own, t_other = _Copper("t_own"), _Copper("t_other")

    prelude = narrow_mixed_selection(
        config_path=str(config_path), adapter=adapter,
        footprints=[fp_c1, fp_c2, fp_p1, fp_p2],
        vias=[v_own, v_other, v_unreg], tracks=[t_own, t_other],
        cfg=cfg, sheet_names=sheet_names, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert [f.ref for f in prelude.footprints] == ["C1", "C2"]
    assert {v.uuid for v in prelude.vias} == {"v_own", "v_unreg"}
    assert {t.uuid for t in prelude.tracks} == {"t_own"}
    assert {c.uuid for c in prelude.kept_copper} == {"v_own", "v_unreg", "t_own"}
    texts = [text for text, _level in prelude.log_lines]
    assert any("read instance" in text for text in texts)
    assert any("subtracted" in text for text in texts)

    # "Read again": the selection the prelude made is CLEAN — one instance and
    # its copper -> the prelude has nothing to narrow (today's ordinary path).
    again = narrow_mixed_selection(
        config_path=str(config_path), adapter=adapter,
        footprints=list(prelude.instance_footprints),
        vias=list(prelude.vias), tracks=list(prelude.tracks),
        cfg=cfg, sheet_names=sheet_names, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    assert again is None


def test_two_candidates_produce_a_refusal_not_a_plan(gate, tmp_path):
    from gui.mixed_selection import narrow_mixed_selection

    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    _write_registries(tmp_path, {}, {})
    cfg = _Cfg(entities=[])
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "C3": ("DA", "DAC_BUF"), "C4": ("DB", "DAC_BUF")})

    class _FpRef:
        def __init__(self, ref, chain):
            self.ref = ref
            self.sheet_path_uuids = tuple(chain)

    fps = [_FpRef("C1", ("ch0", "a")), _FpRef("C2", ("ch0", "b")),
           _FpRef("C3", ("ch1", "c")), _FpRef("C4", ("ch1", "d"))]
    prelude = narrow_mixed_selection(
        config_path=str(config_path), adapter=adapter, footprints=fps,
        vias=[], tracks=[], cfg=cfg,
        sheet_names={"ch0": "Channel_0", "ch1": "Channel_1"},
        cell_name="dac_buf", cell_roles={"DA", "DB"},
        remembered_cluster="DAC_BUF")
    assert prelude is not None
    assert prelude.refusal and "Channel_0" in prelude.refusal
    assert prelude.refusal and "Channel_1" in prelude.refusal


# ── "Fill from selection": filter other clusters BEFORE the refusals ────────

class _Comp:
    def __init__(self, role):
        self.role = role


class _Cell:
    def __init__(self, name, roles):
        self.name = name
        self.components = [_Comp(r) for r in roles]


def _sel(ref, role, cluster, sheet):
    from gui.cell_identification import SelectionRecord
    return SelectionRecord(ref=ref, role=role, cluster=cluster, sheet=tuple(sheet))


def test_identify_filters_foreign_clusters_before_refusals():
    from gui.cell_identification import identify_cell_instance
    cell = _Cell("pif_avdd", ["C_IN_BULK", "C_IN_BYPASS"])
    selected = [_sel("D1", "R_DA", "DAC_BUF", ("Channel_0",)),
                _sel("P1", "C_IN_BULK", "PIF_AVDD", ("Channel_0",)),
                _sel("P2", "C_IN_BYPASS", "PIF_AVDD", ("Channel_0",)),
                _sel("Q1", "C_IN_BULK", "PIF_OA_N2V5", ("Channel_1",)),
                _sel("Q2", "C_IN_BYPASS", "PIF_OA_N2V5", ("Channel_1",))]
    members = [_sel("P1", "C_IN_BULK", "PIF_AVDD", ("Channel_0",)),
               _sel("P2", "C_IN_BYPASS", "PIF_AVDD", ("Channel_0",))]
    ident = identify_cell_instance(
        cell, selected, members, entities=(), sheet_names={},
        cell_clusters={"PIF_AVDD"})
    assert ident.cluster == "PIF_AVDD"
    assert ident.role_to_ref == {"C_IN_BULK": "P1", "C_IN_BYPASS": "P2"}


def test_identify_without_cell_cluster_still_refuses_today():
    """The same selection WITHOUT the cell's cluster keeps today's refusal — the
    filtering is the ONLY change (plan item 5)."""
    from kicadstamp.exceptions import ValidationError

    from gui.cell_identification import identify_cell_instance
    cell = _Cell("pif_avdd", ["C_IN_BULK", "C_IN_BYPASS"])
    selected = [_sel("D1", "R_DA", "DAC_BUF", ("Channel_0",)),
                _sel("P1", "C_IN_BULK", "PIF_AVDD", ("Channel_0",)),
                _sel("P2", "C_IN_BYPASS", "PIF_AVDD", ("Channel_0",)),
                _sel("Q1", "C_IN_BULK", "PIF_OA_N2V5", ("Channel_1",)),
                _sel("Q2", "C_IN_BYPASS", "PIF_OA_N2V5", ("Channel_1",))]
    with pytest.raises(ValidationError):
        identify_cell_instance(cell, selected, selected, entities=(),
                               sheet_names={})


def test_identify_clean_selection_is_unchanged():
    """A clean (single-cluster) selection is identified exactly as before, with
    or without the cell's cluster given."""
    from gui.cell_identification import identify_cell_instance
    cell = _Cell("pif_avdd", ["C_IN_BULK", "C_IN_BYPASS"])
    selected = [_sel("P1", "C_IN_BULK", "PIF_AVDD", ("Channel_0",)),
                _sel("P2", "C_IN_BYPASS", "PIF_AVDD", ("Channel_0",))]
    base = identify_cell_instance(cell, selected, selected, entities=(),
                                  sheet_names={})
    with_clusters = identify_cell_instance(cell, selected, selected, entities=(),
                                           sheet_names={},
                                           cell_clusters={"PIF_AVDD"})
    assert base == with_clusters


def test_identify_spoke_cluster_is_not_filtered_out():
    """A spoke's own cluster (FPGA_PWR_BANK) is the cell's cluster, so filtering
    leaves every component in — the spoke path is unchanged."""
    from gui.cell_identification import KIND_SPOKE, identify_cell_instance
    cell = _Cell("fpga_pwr_bank", ["C_FPGA_BULK", "C_FPGA_BYPASS"])
    selected = [_sel("C74", "C_FPGA_BULK", "FPGA_PWR_BANK", ("FPGA",)),
                _sel("C53", "C_FPGA_BYPASS", "FPGA_PWR_BANK", ("FPGA",))]
    members = [_sel("C74", "C_FPGA_BULK", "FPGA_PWR_BANK", ("FPGA",)),
               _sel("C53", "C_FPGA_BYPASS", "FPGA_PWR_BANK", ("FPGA",)),
               _sel("C99", "C_FPGA_BULK", "FPGA_PWR_BANK", ("FPGA",))]
    ident = identify_cell_instance(cell, selected, members, entities=(),
                                   sheet_names={},
                                   cell_clusters={"FPGA_PWR_BANK"})
    assert ident.kind == KIND_SPOKE
    assert ident.role_to_ref == {"C_FPGA_BULK": "C74", "C_FPGA_BYPASS": "C53"}
