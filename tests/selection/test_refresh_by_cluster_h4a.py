# tests/selection/test_refresh_by_cluster_h4a.py
"""Н4а cells (acceptance of cbc8bdc): the surviving mutation rows N4, N5, N7,
N8, N11, N18–N20 and the findings Ф1–Ф3 of
plan_2026_10_04_refresh_mixed_cluster_selection (Denis, 2026-10-05).

Focused on the properties the first acceptance could not see: components FROM
THE BOARD (N5), the remembered-sheet fallback (N4), the new record's ANGLE in
the cell's axes (N7) and its `layer` side rule (N8), that only a REMOVED role's
copper goes (N11), the unregistered `net_traces:` subtraction (N18–N20) and the
three findings (unreadable board copper, referenced removed role, unresolved
net_traces anchor). Format 2 + format 3 through the `gate` fixture.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.config import format_version
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.registry import make_registry_key
from kicadstamp.selection_narrowing import (
    CopperReadContext,
    apply_live_copper_rule,
    subtract_net_trace_copper,
)
from tests.fakes.format3 import det_uuid


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Rec:
    def __init__(self, name=None, uuid=None, cell=None, cluster=None, sheet=None,
                 anchor_role=None, role=None):
        self.name = name
        self.uuid = uuid
        self.cell = cell
        self.cluster = cluster
        self.sheet = sheet
        self.anchor_role = anchor_role
        self.role = role


class _Cfg:
    def __init__(self, entities=(), clone_placements=(), cells=None,
                 net_traces=None):
        self.entities = list(entities)
        self.clone_placements = list(clone_placements)
        self.cells = cells or {}
        self.net_traces = list(net_traces or [])


class _Adapter:
    def __init__(self, fields, footprints=(), vias=(), tracks=()):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_footprint(self, ref):
        return None

    def get_pad_by_number(self, fp, pad):
        return None

    def get_footprint_pads(self, fp):
        return []


def _fp(ref, role, cluster, x_mm, y_mm, chain=(), angle=0.0,
        layer=BoardLayer.BL_F_Cu):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=angle,
                   layer=layer)
    fp.sheet_path_uuids = tuple(chain)
    return fp


def _config_path(tmp_path):
    p = tmp_path / "config.sexp"
    p.write_text("", encoding="utf-8")
    return str(p)


def _one_cell_cfg(**kw):
    return _Cfg(entities=[_Rec(name="dac0", uuid=det_uuid("entities:dac0"),
                               cell="dac_buf", cluster="DAC_BUF",
                               sheet="Channel_0")],
                cells={"dac_buf": _Rec(uuid=det_uuid("cells:dac_buf"))}, **kw)


# ── N4: the remembered-sheet fallback ───────────────────────────────────────

def test_h4_remembered_sheet_is_used_when_the_selection_has_no_cell_cluster(
        gate, tmp_path):
    """N4: the selection holds NONE of the cell's cluster; the remembered
    (cluster, sheet) resolves on the board -> that instance is read."""
    from gui.mixed_selection import narrow_mixed_selection

    cfg = _one_cell_cfg()
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    fp_p1 = _fp("P1", "PA", "PIF_AVDD", 20.0, 10.0, ("ch1",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "P1": ("PA", "PIF_AVDD")},
                       footprints=[fp_c1, fp_c2, fp_p1])
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter, footprints=[fp_p1],
        vias=[], tracks=[], cfg=cfg, sheet_names={"ch0": "Channel_0"},
        cell_name="dac_buf", cell_roles={"DA", "DB"},
        remembered_cluster="DAC_BUF", remembered_sheet="Channel_0")
    assert prelude is not None and prelude.refusal is None
    assert [f.ref for f in prelude.footprints] == ["C1", "C2"]


# ── N5: components come from the BOARD, not the selection ────────────────────

def test_h4_components_come_from_the_board(gate, tmp_path):
    """N5: the selection holds only C1, the board holds C1+C2 of the instance —
    BOTH are read (the component outside the frame is not lost)."""
    from gui.mixed_selection import narrow_mixed_selection

    cfg = _one_cell_cfg()
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter, footprints=[fp_c1],
        vias=[], tracks=[], cfg=cfg, sheet_names={"ch0": "Channel_0"},
        cell_name="dac_buf", cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert [f.ref for f in prelude.footprints] == ["C1", "C2"]


# ── N7/N8: the new component record's angle and layer ───────────────────────

def _rotated_setup(gate):
    components = [
        {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
    ]
    # A 90° instance: C1 at 0°, C2 sits "above" C1 and is turned 90°.
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",), angle=0.0)
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 10.0, 15.0, ("ch0",), angle=90.0)
    return components, fp_c1, fp_c2


def test_h4a_new_record_angle_is_in_the_cell_axes(gate):
    """N7: a role the instance gained on a TURNED instance gets the angle in the
    cell's OWN axes (board angle − theta), not the board angle itself."""
    components, fp_c1, fp_c2 = _rotated_setup(gate)
    fp_new = _fp("R70", "R_SD_PROT", "DAC_BUF", 10.0, 20.0, ("ch0",),
                 angle=90.0)
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "R70": ("R_SD_PROT", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2, fp_new])
    plan = build_refresh_plan(
        components, [], [], [fp_c1, fp_c2, fp_new], [], [], adapter,
        reconcile_components=True, cell_layer="F.Cu")
    record = plan.new_component_records[0]
    assert record["role"] == "R_SD_PROT"
    # The instance is rotated: the new part sits at the SAME board angle as the
    # matched role DB, so its CELL angle must equal DB's recomputed cell angle —
    # NOT the raw board angle (the axis of N7).
    db_angle = next(geo["angle_deg"] for rec, geo in plan.component_updates
                    if rec["role"] == "DB")
    assert record["angle_deg"] == pytest.approx(db_angle, abs=1e-6)
    assert record["angle_deg"] != pytest.approx(90.0, abs=1e-6)


def test_h4a_new_record_layer_written_only_on_the_other_side(gate):
    """N8: `layer` is written ONLY when the part is on the other side than the
    cell — same side: no key; other side: `layer: B.Cu`."""
    for expected_layer, fp_layer in (("same", BoardLayer.BL_F_Cu),
                                     ("B.Cu", BoardLayer.BL_B_Cu)):
        components = [
            {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0},
            {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0},
        ]
        fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
        fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
        fp_new = _fp("R70", "R_SD_PROT", "DAC_BUF", 20.0, 10.0, ("ch0",),
                     layer=fp_layer)
        adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                            "R70": ("R_SD_PROT", "DAC_BUF")},
                           footprints=[fp_c1, fp_c2, fp_new])
        plan = build_refresh_plan(
            components, [], [], [fp_c1, fp_c2, fp_new], [], [], adapter,
            reconcile_components=True, cell_layer="F.Cu")
        record = plan.new_component_records[0]
        if expected_layer == "same":
            assert "layer" not in record
        else:
            assert record["layer"] == "B.Cu"


# ── N11: only the REMOVED role's copper goes ─────────────────────────────────

def test_h4a_only_the_removed_roles_copper_is_deleted(gate):
    """N11: a track whose `net_from_role` is a role that is STILL on the board is
    NOT removed when another role goes."""
    components = [
        {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "GONE", "offset_along_mm": 2.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
    ]
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    gone_via = {"net_from_role": "GONE", "offset_along_mm": 0.0,
                "offset_across_mm": 0.0, "drill_mm": 0.3, "diameter_mm": 0.6}
    other_track = {"net": "GND", "layer": "F.Cu", "width_mm": 0.25,
                   "start_along_mm": 0.0, "start_across_mm": 2.0,
                   "end_along_mm": 1.0, "end_across_mm": 2.0}
    plan = build_refresh_plan(
        components, [gone_via], [other_track], [fp_c1, fp_c2], [], [], adapter,
        keep_unpaired=True, reconcile_components=True, cell_layer="F.Cu")
    assert gone_via in plan.removed_via_records
    assert other_track not in plan.removed_track_records


# ── N18–N20/Ф3: net_traces outside the registry ─────────────────────────────

def _patch_live_copper(monkeypatch, found, reason=None, identity="NT"):
    import kicadstamp.net_trace_planner as planner
    stub = SimpleNamespace(found=list(found), reason=reason, identity=identity)
    monkeypatch.setattr(planner, "find_live_copper",
                        lambda adapter, nt, **kw: stub)


def _track(uuid, net="NT"):
    return Track(uuid=uuid, net_name=net,
                 start=Vector2.from_xy_mm(0.0, 0.0),
                 end=Vector2.from_xy_mm(1.0, 0.0),
                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _via(uuid, net="NT"):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(0.0, 0.0),
               net_name=net, drill_mm=0.3, diameter_mm=0.6)


def _net_trace_cfg():
    return _one_cell_cfg(net_traces=[SimpleNamespace(net="NT")])


def test_h4a_unregistered_net_trace_track_is_subtracted(gate, tmp_path,
                                                        monkeypatch):
    """N18: a selected track that a live `net_traces:` record plans is subtracted
    even though the registry does not know it."""
    from gui.mixed_selection import narrow_mixed_selection

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    t_nt = _track("t-nt")
    _patch_live_copper(monkeypatch, [t_nt])
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[fp_c1, fp_c2], vias=[], tracks=[t_nt], cfg=_net_trace_cfg(),
        sheet_names={"ch0": "Channel_0"}, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert {t.uuid for t in prelude.tracks} == set()


def test_h4a_unregistered_net_trace_via_is_subtracted(gate, tmp_path,
                                                      monkeypatch):
    """N19: the same for a via."""
    from gui.mixed_selection import narrow_mixed_selection

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    v_nt = _via("v-nt")
    _patch_live_copper(monkeypatch, [v_nt])
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[fp_c1, fp_c2], vias=[v_nt], tracks=[], cfg=_net_trace_cfg(),
        sheet_names={"ch0": "Channel_0"}, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert {v.uuid for v in prelude.vias} == set()


def test_h4a_only_the_records_copper_is_subtracted_not_the_whole_net(
        gate, tmp_path, monkeypatch):
    """N20: a track on the SAME net as a `net_traces:` record but NOT one of its
    planned pieces stays in the read (never the whole net by name)."""
    from gui.mixed_selection import narrow_mixed_selection

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    t_other = _track("t-other")
    _patch_live_copper(monkeypatch, [])  # the record plans NOTHING here
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[fp_c1, fp_c2], vias=[], tracks=[t_other],
        cfg=_net_trace_cfg(), sheet_names={"ch0": "Channel_0"},
        cell_name="dac_buf", cell_roles={"DA", "DB"})
    assert prelude is not None and prelude.refusal is None
    assert {t.uuid for t in prelude.tracks} == {"t-other"}


def test_h4a_unresolved_net_trace_anchor_is_reported(gate, tmp_path,
                                                     monkeypatch):
    """Ф3: an anchor that did not resolve is SAID in the Log, and the record is
    not subtracted — never a silent skip."""
    from gui.mixed_selection import narrow_mixed_selection

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    t_nt = _track("t-nt")
    _patch_live_copper(monkeypatch, [], reason="anchor 'X' not found")
    prelude = narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[fp_c1, fp_c2], vias=[], tracks=[t_nt], cfg=_net_trace_cfg(),
        sheet_names={"ch0": "Channel_0"}, cell_name="dac_buf",
        cell_roles={"DA", "DB"})
    texts = [text for text, _level in prelude.log_lines]
    assert any("not subtracted" in t and "anchor 'X' not found" in t
               for t in texts)
    assert {t.uuid for t in prelude.tracks} == {"t-nt"}


# ── Ф1: a failed board-copper read deletes NOTHING ──────────────────────────

def test_h4a_failed_board_read_deletes_nothing(gate):
    """Ф1: board_read_ok=False means "we do not know" — a record whose registry
    uuid is not in the (empty) board set is KEPT, and the Log says why."""
    record = {"net": "GND", "layer": "F.Cu", "width_mm": 0.25,
              "start_along_mm": 0.0, "start_across_mm": 2.0,
              "end_along_mm": 1.0, "end_across_mm": 2.0}
    key = make_registry_key("name:ent", "cell", None, 0)
    entries = {key: SimpleNamespace(uuid="u-gone")}
    ctx = CopperReadContext(
        cell_identity="cell",
        own_addresses={"ent": ("DAC_BUF", "Channel_0")},
        chosen_address=("DAC_BUF", "Channel_0"), chosen_refs=frozenset(),
        via_entries={}, track_entries=entries,
        board_via_uuids=frozenset(), board_track_uuids=frozenset(),
        vias=[], tracks=[record], board_read_ok=False)
    plan = SimpleNamespace(unpaired_via_records=[],
                           unpaired_track_records=[record],
                           removed_via_records=[], removed_track_records=[])
    lines = apply_live_copper_rule(plan, ctx)
    assert plan.removed_track_records == []
    assert any("could not read the board copper" in ln for ln in lines)


# ── Ф2: a removed role referenced outside the cell is named ─────────────────

def test_h4a_removed_role_referenced_outside_is_reported(gate):
    """Ф2: an entity anchor that points at a role the reconcile removes yields
    ONE yellow line naming the role and the referring record."""
    cfg = _Cfg(entities=[_Rec(name="dac0", cell="dac_buf", cluster="DAC_BUF",
                              anchor_role="GONE")])
    components = [
        {"role": "DA", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "DB", "offset_along_mm": 5.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
        {"role": "GONE", "offset_along_mm": 2.0, "offset_across_mm": 0.0,
         "angle_deg": 0.0},
    ]
    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    plan = build_refresh_plan(
        components, [], [], [fp_c1, fp_c2], [], [], adapter,
        reconcile_components=True, config=cfg, cell_layer="F.Cu")
    assert [c["role"] for c in plan.removed_component_records] == ["GONE"]
    assert any("reference the removed role 'GONE'" in w for w in plan.warnings)


def test_h4a_subtract_net_trace_copper_reports_the_reason(gate, monkeypatch):
    """Ф3 at the pure level: `subtract_net_trace_copper` carries the reason of an
    unresolved anchor in its notes."""
    import kicadstamp.net_trace_planner as planner
    stub = SimpleNamespace(found=[], reason="anchor 'X' not found",
                           identity="NT")
    monkeypatch.setattr(planner, "find_live_copper",
                        lambda adapter, nt, **kw: stub)
    nt = SimpleNamespace(net="NT")
    sub = subtract_net_trace_copper([], [nt], None, via_entries={},
                                    track_entries={})
    assert sub.notes and "anchor 'X' not found" in sub.notes[0]


# ── Р3а-3: the transfer runs ONLY for the journal's instance ─────────────────

_JOURNAL = {"cell": "dac_buf", "cluster": "DAC_BUF", "sheet": "Channel_0"}


def _one_track_read(tmp_path, monkeypatch, *, journal, explode_transfer=True):
    """The DAC_BUF instance's read with one net_traces-owned track selected."""
    from gui.mixed_selection import narrow_mixed_selection

    fp_c1 = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    fp_c2 = _fp("C2", "DB", "DAC_BUF", 15.0, 10.0, ("ch0",))
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF")},
                       footprints=[fp_c1, fp_c2])
    t_nt = _track("t-nt")
    _patch_live_copper(monkeypatch, [t_nt])
    return narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[fp_c1, fp_c2], vias=[], tracks=[t_nt],
        cfg=_net_trace_cfg(), sheet_names={"ch0": "Channel_0"},
        cell_name="dac_buf", cell_roles={"DA", "DB"},
        explode_transfer=explode_transfer, explode_journal=journal)


def test_r3a3_the_transfer_runs_for_the_journals_instance(gate, tmp_path,
                                                         monkeypatch):
    """Р3а-3: the journal's own instance is being read -> the piece stays in the
    read and is NAMED for the ownership transfer."""
    prelude = _one_track_read(tmp_path, monkeypatch, journal=_JOURNAL)
    assert prelude is not None and prelude.refusal is None
    assert {t.uuid for t in prelude.tracks} == {"t-nt"}      # kept, not subtracted
    assert prelude.transfers                                 # named for the apply
    assert any("transferred from net_traces" in text
               for text, _level in prelude.log_lines)


def test_r3a3_a_different_instance_is_subtracted_with_a_line(
        gate, tmp_path, monkeypatch):
    """Р3а-3: the read resolved to ANOTHER instance of the same cell while the tab
    asked for the transfer — the inter-cluster copper is SUBTRACTED as usual (Н4)
    and the Log says so; nothing is transferred."""
    other = {"cell": "dac_buf", "cluster": "PIF_AVDD", "sheet": "Channel_0"}
    prelude = _one_track_read(tmp_path, monkeypatch, journal=other)
    assert prelude is not None and prelude.refusal is None
    assert {t.uuid for t in prelude.tracks} == set()         # subtracted
    assert prelude.transfers == ()                           # nothing handed over
    texts = [text for text, _level in prelude.log_lines]
    assert any("not the exploded one" in t for t in texts)
    assert not any("transferred from net_traces" in t for t in texts)


def test_r3a3_no_transfer_request_is_a_plain_subtraction(gate, tmp_path,
                                                         monkeypatch):
    """Р3а-3: without the request (the cell window's own button) the journal is
    irrelevant — the copper is subtracted and nothing is said about instances."""
    prelude = _one_track_read(tmp_path, monkeypatch, journal=_JOURNAL,
                              explode_transfer=False)
    assert {t.uuid for t in prelude.tracks} == set()
    assert prelude.transfers == ()
    texts = [text for text, _level in prelude.log_lines]
    assert not any("not the exploded one" in t for t in texts)
