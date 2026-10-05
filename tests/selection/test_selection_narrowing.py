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

from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
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

    # build_refresh_plan's net_from_role resolution: no pads here, so every net
    # a NEW record gets stays a literal — enough for the fixpoint cells below.
    def get_footprint(self, ref):
        return None

    def get_pad_by_number(self, fp, pad):
        return None

    def get_footprint_pads(self, fp):
        return []


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


# ── N1: a cell's OWN anchor:/role: copper is not subtracted ─────────────────

def test_subtract_foreign_copper_keeps_own_anchor_and_role(gate):
    """N1 (acceptance of b209c58): a cell placed by a ClonePlacement anchored on
    a component/role records its OWN copper under `anchor:`/`role:`. The first
    version called that foreign and — with the mixed path's old
    remove_missing=True — DELETED the cell's records. Own `anchor:<ref>` is kept
    when <ref> is among the chosen instance's components; own
    `role:<role>:<sheet>:<cluster>` when its address matches the instance;
    `point:`/`pad:` stay foreign."""
    cfg, cell_uuid, _chosen, _other, _pif, _pif_uuid = _cfg_two_instances()
    cell_identity = record_key_part("dac_buf", cell_uuid)
    own_addresses = cell_record_addresses(cfg, "dac_buf")
    chosen_address = ("DAC_BUF", "Channel_0")
    chosen_refs = ["C1", "C2"]  # the chosen instance's own components

    own_anchor = _Copper("own_anchor")
    foreign_anchor = _Copper("foreign_anchor")
    own_role = _Copper("own_role")
    foreign_role = _Copper("foreign_role")
    point = _Copper("point")
    owner = {
        "own_anchor": make_registry_key("anchor:C1:1:0.0000:0.0000",
                                        cell_identity, "r", 0),
        "foreign_anchor": make_registry_key("anchor:X9:1:0.0000:0.0000",
                                            cell_identity, "r", 0),
        "own_role": make_registry_key("role:DA:Channel_0:DAC_BUF:1:0.0000:0.0000",
                                      cell_identity, "r", 0),
        "foreign_role": make_registry_key("role:DA:Channel_1:DAC_BUF:1:0.0000:0.0000",
                                          cell_identity, "r", 0),
        "point": make_registry_key("point:pt-uuid:0.0000:0.0000",
                                   cell_identity, "r", 0),
    }
    sub = subtract_foreign_copper(
        [own_anchor, foreign_anchor, own_role, foreign_role, point],
        owner, cell_identity, own_addresses, chosen_address, chosen_refs)
    assert {i.uuid for i in sub.kept} == {"own_anchor", "own_role"}
    assert {i.uuid for i in sub.removed} == {"foreign_anchor", "foreign_role",
                                             "point"}


# ── N2: the read-back is a fixpoint, and a manual removal deletes its record ─

def _live_fp(ref, role, cluster, x_mm, y_mm, chain):
    """A live Footprint DTO — as adapter.get_selected_items() hands it to both
    the narrowing (sheet chain + Role/Cluster) and build_refresh_plan (position,
    angle)."""
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = tuple(chain)
    return fp


def _apply_refresh_plan(plan, components, vias, tracks):
    """The CellDock apply convention (_apply_refresh_plan): write the geometry
    onto the SAME dicts, append the brand-new records, drop the removed ones —
    and NOTHING is written to disk anywhere in the round trip."""
    for rec, new_geo in (plan.component_updates + plan.via_updates
                         + plan.track_updates):
        rec.update(new_geo)
    vias.extend(plan.new_via_records)
    tracks.extend(plan.new_track_records)
    doomed = {id(r) for r in plan.removed_via_records
              + plan.removed_track_records}
    if doomed:
        vias[:] = [r for r in vias if id(r) not in doomed]
        tracks[:] = [r for r in tracks if id(r) not in doomed]


def _plan_decomposition(plan):
    """(changed_pairs, new_vias, new_tracks, removed_vias, removed_tracks).

    build_refresh_plan ALWAYS lists a recomputed (record, new_geo) pair for every
    match — even when the geometry did not move — so the read-back's "plan is
    empty" is: every pair is a NO-OP (new_geo equals the record's own values) and
    nothing was added or removed."""
    changed = [(rec, new_geo)
               for rec, new_geo in (plan.component_updates + plan.via_updates
                                    + plan.track_updates)
               if any(rec.get(k) != v for k, v in new_geo.items())]
    return (changed, plan.new_via_records, plan.new_track_records,
            plan.removed_via_records, plan.removed_track_records)


def _config_mixed_roundtrip():
    """A config with ONE cell dac_buf and ONE entity dac0 placing it on
    Channel_0 — the chosen instance the mixed selection narrows to."""
    cell_uuid = det_uuid("cells:dac_buf")
    ent = _Rec(name="dac0", uuid=det_uuid("entities:dac0"), cell="dac_buf",
               cluster="DAC_BUF", sheet="Channel_0")
    return (_Cfg(entities=[ent], cells={"dac_buf": _Rec(uuid=cell_uuid)}),
            cell_uuid, ent)


def _roundtrip_scenario(gate):
    cfg, cell_uuid, _ent = _config_mixed_roundtrip()
    components = [
        {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
    ]
    fp_c1 = _live_fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0", "s1"))
    fp_c2 = _live_fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0", "s2"))
    fp_p1 = _live_fp("P1", "C_IN_BULK", "PIF_AVDD", 20.0, 10.0, ("ch1", "s3"))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "P1": ("C_IN_BULK", "PIF_AVDD")})
    sheet_names = {"ch0": "Channel_0", "ch1": "Channel_1"}
    track = Track(uuid="t_unreg", net_name="GND",
                  start=Vector2.from_xy_mm(10.0, 10.0),
                  end=Vector2.from_xy_mm(15.0, 10.0),
                  width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    return (cfg, cell_uuid, components, fp_c1, fp_c2, fp_p1, adapter,
            sheet_names, track)


def test_read_back_is_a_fixpoint(gate, tmp_path):
    """N2, cell 9: a MIXED read (narrow → plan → apply it to the record lists)
    then a read AGAIN on exactly the selection-after-read (the instance's
    components + the copper that entered the read) yields an EMPTY plan — 0
    changed updates, 0 new, 0 removed — and writes NO file. This is the whole
    scheme's spine (Denis 2026-10-04: "а после — перечитать")."""
    from gui.mixed_selection import narrow_mixed_selection

    config_path = tmp_path / "config.sexp"
    config_path.write_text("original", encoding="utf-8")
    _write_registries(tmp_path, {}, {})  # nothing registered — the copper stays
    (cfg, _cell_uuid, components, fp_c1, fp_c2, fp_p1, adapter, sheet_names,
     track) = _roundtrip_scenario(gate)
    vias, tracks = [], []

    # 1) the MIXED read.
    prelude = narrow_mixed_selection(
        config_path=str(config_path), adapter=adapter,
        footprints=[fp_c1, fp_c2, fp_p1], vias=[], tracks=[track],
        cfg=cfg, sheet_names=sheet_names, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert [f.ref for f in prelude.footprints] == ["C1", "C2"]
    assert {t.uuid for t in prelude.tracks} == {"t_unreg"}
    plan1 = build_refresh_plan(
        components, vias, tracks, list(prelude.footprints), list(prelude.vias),
        list(prelude.tracks), adapter, add_new_copper=True, remove_missing=False,
        cell_layer="F.Cu")
    assert len(plan1.new_track_records) == 1
    assert plan1.removed_via_records == [] and plan1.removed_track_records == []
    _apply_refresh_plan(plan1, components, vias, tracks)
    assert len(tracks) == 1

    before = (config_path.read_bytes(),
              (tmp_path / "registry" / "config.registry.json").read_bytes(),
              (tmp_path / "tracks" / "config.tracks.registry.json").read_bytes())

    # 2) read AGAIN on the selection-after-read (clean → the prelude is None).
    after = narrow_mixed_selection(
        config_path=str(config_path), adapter=adapter,
        footprints=list(prelude.instance_footprints), vias=list(prelude.vias),
        tracks=list(prelude.tracks), cfg=cfg, sheet_names=sheet_names,
        cell_name="dac_buf", cell_roles={"DA", "DB"})
    assert after is None
    plan2 = build_refresh_plan(
        components, vias, tracks, [fp_c1, fp_c2], [], [track], adapter,
        add_new_copper=True, remove_missing=True, cell_layer="F.Cu")
    assert _plan_decomposition(plan2) == ([], [], [], [], [])
    assert (config_path.read_bytes(),
            (tmp_path / "registry" / "config.registry.json").read_bytes(),
            (tmp_path / "tracks" / "config.tracks.registry.json").read_bytes()
            ) == before


def test_read_back_after_manual_track_removal_deletes_its_record(gate, tmp_path):
    """N2, cell 10: from the selection-after-read one instance track is removed
    by hand — its record is DELETED by the ordinary (clean) read, everything else
    is untouched."""
    (cfg, _cell_uuid, components, fp_c1, fp_c2, fp_p1, adapter, sheet_names,
     track) = _roundtrip_scenario(gate)
    vias, tracks = [], []

    # Bring the record list to the post-mixed-read state (its record exists).
    plan1 = build_refresh_plan(
        components, vias, tracks, [fp_c1, fp_c2], [], [track], adapter,
        add_new_copper=True, remove_missing=False, cell_layer="F.Cu")
    _apply_refresh_plan(plan1, components, vias, tracks)
    assert len(tracks) == 1

    # The user removes the track from the selection and reads again (clean path:
    # the mixed narrowing is not in play, remove_missing=True as everywhere).
    plan2 = build_refresh_plan(
        components, vias, tracks, [fp_c1, fp_c2], [], [], adapter,
        add_new_copper=True, remove_missing=True, cell_layer="F.Cu")
    changed, new_v, new_t, rem_v, rem_t = _plan_decomposition(plan2)
    assert (changed, new_v, new_t, rem_v, rem_t) == ([], [], [], [], [tracks[0]])
